"""cq fix：分批修复计划（默认 dry-run）。

硬规矩（交接方案 §2/§7）：
- 默认只输出计划（批次、文件、命令、预期效果），不改任何代码；
- `--apply` 是用户显式批准的载体，且一次只允许应用一个批次（--batch N 必填）；
- 只有"可自动修复"批次可 apply（ruff --fix / ruff format）；其余批次给人工指引；
- apply 后绝不自动 commit；提示"跑测试 → cq scan → cq diff → cq gate"闭环。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .runs import RunData


@dataclass
class FixBatch:
    batch: int
    title: str
    applicable: bool  # True = 可 --apply（命令安全可自动执行）
    commands: list[list[str]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    finding_ids: list[str] = field(default_factory=list)
    guidance: str = ""  # 不可自动执行批次的人工指引

    def to_dict(self) -> dict[str, object]:
        return {
            "batch": self.batch,
            "title": self.title,
            "applicable": self.applicable,
            "commands": self.commands,
            "files": self.files,
            "finding_ids": self.finding_ids,
            "guidance": self.guidance,
        }


class FixError(ValueError):
    """fix 操作非法（批次号、apply 门禁等）。"""


def build_fix_plan(run: RunData, cfg_python: str) -> list[FixBatch]:
    """由 run 的 findings 推导修复批次。批次顺序 = triage 的投入产出排序。"""
    active = run.findings_active
    builders = (_auto_batch, _security_batch, _refactor_batch, _tests_batch)
    batches = [b for b in (fn(active, cfg_python) for fn in builders) if b is not None]
    claimed = {fid for b in batches for fid in b.finding_ids}
    review = _review_batch(active, claimed)
    if review is not None:
        batches.append(review)
    for i, b in enumerate(batches, start=1):
        b.batch = i
    return batches


def _files_of(items: list[dict[str, object]]) -> list[str]:
    return sorted({str(f.get("file")) for f in items if f.get("file")})


def _auto_batch(active: list[dict[str, object]], cfg_python: str) -> FixBatch | None:
    hits = [f for f in active if f.get("auto_fixable") or str(f.get("source")) == "ruff-format"]
    files = _files_of(hits)
    if not files:
        return None
    return FixBatch(
        batch=0,  # 号码在 build_fix_plan 统一重排
        title="自动修复（ruff --fix + ruff format）",
        applicable=True,
        commands=[
            [cfg_python, "-m", "ruff", "check", "--fix", *files],
            [cfg_python, "-m", "ruff", "format", *files],
        ],
        files=files,
        finding_ids=[str(f.get("id")) for f in hits],
        guidance="apply 后必须跑测试，再 cq scan 复扫对比",
    )


def _security_batch(active: list[dict[str, object]], _python: str) -> FixBatch | None:
    hits = [
        f
        for f in active
        if str(f.get("category")) == "security" or str(f.get("source")) == "deepsec"
    ]
    if not hits:
        return None
    return FixBatch(
        batch=0,
        title="安全类人工核对（不可自动修复）",
        applicable=False,
        files=_files_of(hits),
        finding_ids=[str(f.get("id")) for f in hits],
        guidance="逐条核对：真问题先修（密钥类必须轮换）；确认误报用 "
        "cq suppress --action false_positive --reason … 豁免",
    )


def _refactor_batch(active: list[dict[str, object]], _python: str) -> FixBatch | None:
    hits = [
        f
        for f in active
        if str(f.get("source")) == "radon"
        or str(f.get("rule")).startswith(("cc-", "mi-"))
        or str(f.get("category")) == "architecture"
    ]
    if not hits:
        return None
    return FixBatch(
        batch=0,
        title="复杂度/架构拆分（不可自动修复）",
        applicable=False,
        files=_files_of(hits),
        finding_ids=[str(f.get("id")) for f in hits],
        guidance="一次只拆一个函数：拆 → 跑测试 → 复扫；参考 finding 的 fix_hint",
    )


def _tests_batch(active: list[dict[str, object]], _python: str) -> FixBatch | None:
    hits = [
        f for f in active if str(f.get("category")) == "test" or str(f.get("source")) == "pytest"
    ]
    if not hits:
        return None
    return FixBatch(
        batch=0,
        title="测试修复与补齐（不可自动修复）",
        applicable=False,
        files=_files_of(hits),
        finding_ids=[str(f.get("id")) for f in hits],
        guidance="先修 failing（看 raw/pytest.txt 的失败详情），再按覆盖率 finding 补测试",
    )


def _review_batch(active: list[dict[str, object]], claimed: set[str]) -> FixBatch | None:
    hits = [f for f in active if str(f.get("id")) not in claimed]
    if not hits:
        return None
    return FixBatch(
        batch=0,
        title="人工排查（类型/未定义名等）",
        applicable=False,
        files=_files_of(hits),
        finding_ids=[str(f.get("id")) for f in hits],
        guidance="逐条看 findings.json 的 message/fix_hint",
    )


def apply_batch(
    project_root: Path,
    batches: list[FixBatch],
    batch_no: int,
    *,
    timeout: int = 300,
) -> tuple[list[str], str]:
    """执行某个可自动修复批次；返回（执行的命令, 输出摘要）。所有门禁不满足时抛 FixError。"""
    target = next((b for b in batches if b.batch == batch_no), None)
    if target is None:
        raise FixError(f"批次 {batch_no} 不存在（计划共 {len(batches)} 批）")
    if not target.applicable:
        raise FixError(
            f"批次 {batch_no}（{target.title}）不可自动执行，--apply 只对'自动修复'批次开放；"
            f"人工指引：{target.guidance}"
        )

    from .runner import run_cmd

    outputs: list[str] = []
    for cmd in target.commands:
        proc = run_cmd(cmd, timeout=timeout, cwd=project_root)
        head = (proc.stdout or proc.stderr).strip().splitlines()
        outputs.append(f"$ {' '.join(cmd)}\n  退出码 {proc.returncode}\n  " + "\n  ".join(head[:6]))
        if proc.returncode not in (0, 1):
            raise FixError(
                f"命令失败（退出码 {proc.returncode}）：{' '.join(cmd)}\n{proc.stderr[:300]}"
            )
    return [" ".join(c) for c in target.commands], "\n\n".join(outputs)


def save_plan(run_dir: Path, batches: list[FixBatch]) -> Path:
    out = run_dir / "fix-plan.json"
    out.write_text(
        json.dumps(
            {"run_id": run_dir.name, "batches": [b.to_dict() for b in batches]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def render_console(
    run_id: str, batches: list[FixBatch], plan_path: Path, *, applied: bool = False
) -> str:
    lines = [
        f"fix · {run_id}" + ("（已应用）" if applied else "（dry-run 计划，未改任何代码）"),
        "",
    ]
    for b in batches:
        mark = "🤖 可自动" if b.applicable else "👤 人工"
        lines.append(f"批次 {b.batch}：{b.title}（{len(b.finding_ids)} 条，{mark}）")
        if b.files:
            shown = "、".join(b.files[:6]) + (
                f" 等 {len(b.files)} 个文件" if len(b.files) > 6 else ""
            )
            lines.append(f"  文件：{shown}")
        for cmd in b.commands:
            lines.append(f"  命令：{' '.join(cmd)}")
        if b.guidance:
            lines.append(f"  指引：{b.guidance}")
        lines.append("")
    lines.append(f"计划已落盘：{plan_path}")
    if not applied:
        lines.append(
            "应用方式（需用户显式批准）：cq fix --run <run_id> --batch <N> --apply\n"
            "apply 后必做：跑测试 → cq scan → cq diff --baseline <基线> → cq gate"
        )
    return "\n".join(lines)
