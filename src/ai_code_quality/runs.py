"""run 目录的解析与加载：diff/gate/triage/baseline 共用。

run_ref 支持完整 run_id 或唯一前缀；找不到/歧义抛 RunNotFoundError（CLI 映射退出码 3）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

BASELINES_SUBDIR = "baselines"
BASELINE_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class RunNotFoundError(LookupError):
    """run/baseline 引用无法解析（不存在、歧义或损坏）。"""


@dataclass
class RunData:
    run_id: str
    run_dir: Path
    summary: dict[str, object]
    findings: list[dict[str, object]] = field(default_factory=list)

    @property
    def findings_active(self) -> list[dict[str, object]]:
        """未豁免的 findings（suppressed 的不参与 diff/gate 判定）。"""
        return [f for f in self.findings if f.get("status") != "suppressed"]

    @property
    def findings_suppressed(self) -> list[dict[str, object]]:
        return [f for f in self.findings if f.get("status") == "suppressed"]

    def has_tool_errors(self) -> bool:
        tools = cast("list[object]", self.summary.get("tools") or [])
        return any(t.get("status") in ("error", "timeout") for t in tools if isinstance(t, dict))


def audits_dir(project_root: Path) -> Path:
    return project_root / "audits"


def resolve_run_dir(project_root: Path, run_ref: str) -> Path:
    root = audits_dir(project_root)
    if not root.is_dir():
        raise RunNotFoundError(f"项目没有 audits/ 目录: {project_root}")
    exact = root / run_ref
    if (exact / "summary.json").is_file():
        return exact
    matches = sorted(
        d
        for d in root.iterdir()
        if d.is_dir() and d.name.startswith(run_ref) and (d / "summary.json").is_file()
    )
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = "、".join(m.name for m in matches[:5])
        raise RunNotFoundError(f"run 前缀 {run_ref!r} 命中多个 run，请用更长的前缀: {names}")
    raise RunNotFoundError(f"找不到 run: {run_ref}（audits/ 下没有匹配的 run 目录）")


def load_run(project_root: Path, run_ref: str) -> RunData:
    run_dir = resolve_run_dir(project_root, run_ref)
    try:
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        fdata = json.loads((run_dir / "findings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RunNotFoundError(f"run {run_dir.name} 的产物读取失败: {e}") from e
    findings = fdata.get("findings") if isinstance(fdata, dict) else None
    if not isinstance(findings, list):
        findings = []
    run_id = str(summary.get("run_id") or run_dir.name)
    return RunData(run_id=run_id, run_dir=run_dir, summary=summary, findings=findings)


def validate_baseline_name(name: str) -> str:
    if not name or not set(name) <= BASELINE_NAME_CHARS:
        raise RunNotFoundError(f"基线名 {name!r} 非法：只允许字母、数字、下划线、连字符")
    return name


def baseline_path(project_root: Path, name: str) -> Path:
    return audits_dir(project_root) / BASELINES_SUBDIR / f"{name}.json"
