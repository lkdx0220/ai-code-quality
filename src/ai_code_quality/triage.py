"""cq triage：把一次 run 的 findings 归因分级，产出修复批次建议。

四维归类（交接方案 §4 Phase 1）：安全 / 可靠性 / 可维护性 / 测试（+架构等其余）。
每条 finding 给 disposition：
- suppressed  → 已豁免（false_positive / debt_accepted，见 suppressions.json）
- auto_fixable → 可自动修复（ruff --fix / format），第一批
- needs_test   → 测试维度，先补测试
- needs_refactor → 复杂度/架构，要拆分
- needs_decision → 安全类，必须人工核对（可能要轮换密钥）
- needs_review → 其余人工排查
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from .runs import RunData, load_run

DISPOSITIONS = (
    "suppressed",
    "auto_fixable",
    "needs_test",
    "needs_refactor",
    "needs_decision",
    "needs_review",
)

_FOUR_DIMS = ("security", "reliability", "maintainability", "test")


def disposition_of(f: dict[str, object]) -> str:
    if f.get("status") == "suppressed":
        return "suppressed"
    rule = str(f.get("rule") or "")
    source = str(f.get("source") or "")
    if bool(f.get("auto_fixable")) or source == "ruff-format":
        return "auto_fixable"
    if str(f.get("category")) == "test" or source == "pytest":
        return "needs_test"
    if (
        source == "radon"
        or rule.startswith(("cc-", "mi-"))
        or str(f.get("category")) == "architecture"
    ):
        return "needs_refactor"
    if str(f.get("category")) == "security" or source == "deepsec":
        return "needs_decision"
    return "needs_review"


def triage_run(run: RunData) -> dict[str, object]:
    by_disp: dict[str, list[dict[str, object]]] = {d: [] for d in DISPOSITIONS}
    by_dim: dict[str, int] = {}
    for f in run.findings_active:
        by_disp[disposition_of(f)].append(f)
        cat = str(f.get("category") or "")
        dim = cat if cat in _FOUR_DIMS else "other"
        by_dim[dim] = by_dim.get(dim, 0) + 1
    for f in run.findings_suppressed:
        by_disp["suppressed"].append(f)

    batches = _batches(by_disp)
    return {
        "run_id": run.run_id,
        "total": len(run.findings),
        "active": len(run.findings_active),
        "by_disposition": {d: len(by_disp[d]) for d in DISPOSITIONS},
        "by_dimension": by_dim,
        "dimensions": {
            "security": _dim_detail(run, "security"),
            "reliability": _dim_detail(run, "reliability"),
            "maintainability": _dim_detail(run, "maintainability"),
            "test": _dim_detail(run, "test"),
            "other": len(run.findings_active)
            - sum(cast("int", _dim_detail(run, d)["count"]) for d in _FOUR_DIMS),
        },
        "batches": batches,
    }


def _dim_detail(run: RunData, dim: str) -> dict[str, object]:
    items = [
        f
        for f in run.findings_active
        if (
            str(f.get("category")) == dim
            if dim != "other"
            else str(f.get("category")) not in _FOUR_DIMS
        )
    ]
    by_sev: dict[str, int] = {}
    for f in items:
        sev = str(f.get("severity") or "info")
        by_sev[sev] = by_sev.get(sev, 0) + 1
    return {"count": len(items), "by_severity": by_sev}


def _batches(by_disp: dict[str, list[dict[str, object]]]) -> list[dict[str, object]]:
    """最多 3 个"当前最有效"的批次，按投入产出排序。"""
    batches: list[dict[str, object]] = []
    auto = by_disp["auto_fixable"]
    if auto:
        batches.append(
            _batch(
                len(batches) + 1,
                "自动修复（安全批次：改完必须跑测试）",
                auto,
                hint="ruff check --fix && ruff format <files>，然后跑测试再复扫",
            )
        )
    decisions = by_disp["needs_decision"]
    if decisions:
        batches.append(
            _batch(
                len(batches) + 1,
                "安全类人工核对（密钥类必须轮换，误报走 cq suppress）",
                decisions,
                hint="逐条核对 → 修掉或 cq suppress --action false_positive --reason …",
            )
        )
    refactor = by_disp["needs_refactor"]
    if refactor:
        batches.append(
            _batch(
                len(batches) + 1,
                "复杂度/架构拆分（一次只拆一类）",
                refactor,
                hint="拆函数/分支，每拆一个跑一次测试",
            )
        )
    tests = by_disp["needs_test"]
    if tests and len(batches) < 3:
        batches.append(
            _batch(len(batches) + 1, "测试修复与补齐", tests, hint="先修 failing，再补覆盖")
        )
    review = by_disp["needs_review"]
    if review and len(batches) < 3:
        batches.append(
            _batch(len(batches) + 1, "人工排查", review, hint="逐条看，多为类型/未定义名")
        )
    return batches


def _batch(seq: int, title: str, items: list[dict[str, object]], *, hint: str) -> dict[str, object]:
    files = sorted({str(f.get("file") or "") for f in items if f.get("file")})
    return {
        "batch": seq,
        "title": title,
        "count": len(items),
        "files": files,
        "hint": hint,
        "finding_ids": [str(f.get("id")) for f in items],
    }


def run_triage(project_root: Path, run_ref: str, *, fmt: str = "md") -> tuple[int, str]:
    run = load_run(project_root, run_ref)
    result = triage_run(run)
    out_path = run.run_dir / "triage.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if fmt == "json":
        return 0, json.dumps(result, ensure_ascii=False, indent=2)
    return 0, render_console(result, out_path)


def render_console(result: dict[str, object], out_path: Path) -> str:
    lines: list[str] = [f"triage · {result['run_id']}", ""]
    dims = cast("dict[str, object]", result.get("dimensions") or {})
    dim_counts = {
        d: cast("dict[str, object]", dims.get(d) or {}).get("count", 0) for d in _FOUR_DIMS
    }
    lines.append(
        "四维分布："
        + "、".join(f"{d} {n}" for d, n in dim_counts.items())
        + f"、other {dims.get('other', 0)}"
    )
    disp = cast("dict[str, object]", result.get("by_disposition") or {})
    lines.append("处置：" + "、".join(f"{k} {v}" for k, v in disp.items() if v))
    lines.append("")
    for b in cast("list[dict[str, object]]", result.get("batches") or []):
        lines.append(f"批次 {b['batch']}：{b['title']}（{b['count']} 条）")
        lines.append(f"  做法：{b['hint']}")
        files = cast("list[str]", b.get("files") or [])
        shown = "、".join(files[:5]) + (f" 等 {len(files)} 个文件" if len(files) > 5 else "")
        if shown:
            lines.append(f"  文件：{shown}")
        lines.append("")
    lines.append(f"产物：{out_path}")
    return "\n".join(lines)
