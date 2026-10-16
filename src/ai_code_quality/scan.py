"""cq scan 主流程：解析目标 → 逐 adapter 执行 → 统一脱敏落盘。

产物（交接方案 Phase 1）：
  audits/<run_id>/summary.json   工具状态 + 总计
  audits/<run_id>/findings.json  全部 findings
  audits/<run_id>/report.md      人读报告
  audits/<run_id>/raw/<tool>.*   各工具原始输出（已脱敏）
"""

from __future__ import annotations

import json
import platform
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import __version__
from .adapters import get_adapters
from .adapters.base import ScanContext
from .config import Config
from .redact import redact_obj
from .schema import AdapterResult, Finding, sort_findings
from .targets import ScanTargets, get_git_info, resolve_targets


@dataclass
class ScanOutput:
    run_id: str
    run_dir: Path
    summary: dict[str, object]
    findings: list[Finding]
    results: list[AdapterResult]
    exit_code: int


def run_scan(
    project_root: Path,
    *,
    scope: str = "full",
    cfg: Config,
    enabled_adapters: list[str] | None = None,
) -> ScanOutput:
    root = project_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"项目目录不存在: {root}")

    # astimezone() = 带时区的本地时间（DTZ005）：run_id 保持本地可读，created_at 带 offset 无歧义
    now = datetime.now().astimezone()
    run_id = f"{now:%Y%m%d-%H%M%S}-{scope}"
    run_dir = root / "audits" / run_id
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    git_info = get_git_info(root)
    targets: ScanTargets = resolve_targets(
        root, scope=scope, git_info=git_info, respect_gitignore=cfg.respect_gitignore
    )

    # 解释器兜底：cq 装在 uv tool 隔离 venv 且未显式配置 python 时，
    # 按实际缺失的核心工具集合，自动改用工具链全齐的系统解释器（说明进报告备注）
    from .doctor import required_missing, resolve_tool_python

    missing = required_missing(cfg.python)
    if missing and cfg.python_source == "default":
        chosen, python_note = resolve_tool_python(cfg, missing=missing)
        if python_note:
            cfg.python = chosen
            targets.notes.append(python_note)

    ctx = ScanContext(
        project_root=root,
        run_dir=run_dir,
        raw_dir=raw_dir,
        run_id=run_id,
        cfg=cfg,
        targets=targets.targets,
        source_dirs=targets.source_dirs,
        has_tests=targets.has_tests,
        test_paths=targets.test_paths,
        importlinter_config=targets.importlinter_config,
        mypy_config=targets.mypy_config,
        notes=list(targets.notes),
    )
    cfg.enabled_adapters = enabled_adapters or cfg.enabled_adapters

    try:
        adapters = get_adapters(cfg.enabled_adapters)
    except ValueError as e:
        raise ValueError(str(e)) from e

    results: list[AdapterResult] = []
    for adapter in adapters:
        results.append(adapter.safe_run(ctx))

    # 不变量：findings id 唯一。adapter 层去重后的防御性兜底——重复 id 会破坏
    # diff 的计数配对与 suppress 的签名对齐（外部缺陷报告 §7.1 实测）。
    findings = sort_findings([f for r in results for f in r.findings])
    seen_ids: set[str] = set()
    deduped: list[Finding] = []
    for f in findings:
        if f.id in seen_ids:
            continue
        seen_ids.add(f.id)
        deduped.append(f)
    findings = deduped

    # 应用项目级豁免（.cq/suppressions.json）：按 id 或行号无关签名匹配，命中 → status=suppressed
    from . import suppress as suppress_mod

    sup_items = suppress_mod.load(root)
    sup_ids, sup_sigs = suppress_mod.suppression_ids(root)
    for f in findings:
        hit = None
        if f.id in sup_ids:
            hit = next(x for x in sup_items if str(x.get("id")) == f.id)
        else:
            sig = (f.source, f.rule, f.file, f.message)
            if sig in sup_sigs:
                hit = next(
                    (x for x in sup_items if suppress_mod._entry_sig(x) == sig),
                    None,
                )
        if hit is not None:
            f.status = "suppressed"
            f.suppression_action = hit.get("action", "")
            f.suppression_reason = hit.get("reason", "")

    summary = _build_summary(run_id, root, scope, cfg, git_info, targets, results, findings)

    # ---- 落盘（全部经 redact） ----
    (run_dir / "summary.json").write_text(
        json.dumps(redact_obj(summary), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "findings.json").write_text(
        json.dumps(
            redact_obj({"run_id": run_id, "findings": [f.to_dict() for f in findings]}),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    from .report import build_report_md

    (run_dir / "report.md").write_text(build_report_md(summary, findings), encoding="utf-8")

    exit_code = 2 if any(r.status in ("error", "timeout") for r in results) else 0
    return ScanOutput(
        run_id=run_id,
        run_dir=run_dir,
        summary=summary,
        findings=findings,
        results=results,
        exit_code=exit_code,
    )


def _build_summary(
    run_id: str,
    root: Path,
    scope: str,
    cfg: Config,
    git_info,
    targets: ScanTargets,
    results: list[AdapterResult],
    findings: list[Finding],
) -> dict[str, object]:
    by_sev: dict[str, int] = {}
    by_cat: dict[str, int] = {}
    by_src: dict[str, int] = {}
    suppressed = [f for f in findings if f.status == "suppressed"]
    active = [f for f in findings if f.status != "suppressed"]
    for f in active:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        by_cat[f.category] = by_cat.get(f.category, 0) + 1
        by_src[f.source] = by_src.get(f.source, 0) + 1

    auto_fixable = sum(1 for f in active if f.auto_fixable)

    return {
        "run_id": run_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cq_version": __version__,
        "project_root": str(root),
        "scope": scope,
        "scanned_targets": [str(t) for t in targets.targets],
        "python": cfg.python,  # 实际跑工具的解释器（可能经兜底切换，见 notes）
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "python_version": sys.version.split()[0],
        },
        "git": {
            "is_repo": git_info.is_repo,
            "commit": git_info.commit or None,
            "branch": git_info.branch or None,
            "dirty_files": git_info.dirty,
        },
        "tools": [r.to_dict() for r in results],
        "totals": {
            "findings": len(findings),
            "active": len(active),
            "suppressed": len(suppressed),
            "by_severity": by_sev,
            "by_category": by_cat,
            "by_source": by_src,
            "auto_fixable": auto_fixable,
        },
        "notes": targets.notes,
        "config": cfg.snapshot(),
        "artifacts": {
            "findings": "findings.json",
            "summary": "summary.json",
            "report": "report.md",
            "raw_dir": "raw/",
        },
        "generated_unix": time.time(),
    }
