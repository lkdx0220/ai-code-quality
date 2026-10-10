"""cq gate：质量门验收。

退出码（交接方案 §5）：0 达标；1 未达标（含新增 blocker/high）；2 工具执行错误；
3 配置/加载错误（CLI 层映射 RunNotFoundError 等）。

评级是本地近似 local_rating（交接方案 §3 L2）：没有 Sonar 时用本地震级近似
Sonar 的 A-E rating，口径写死在 _rating_of，结果进 gate.json 供人工比对外部 Sonar。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from .diff import DiffResult, diff_findings
from .runs import RunData, load_run

TARGETS = ("A", "B")
# target → 允许的 (blocker, high) 上限（活跃、未豁免）
_TARGET_LIMITS = {"A": (0, 0), "B": (0, 2)}


@dataclass
class GateCheck:
    name: str
    passed: bool
    detail: str
    skipped: bool = False  # 缺数据（如无基线/无覆盖率）时不算失败，但要点名

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "passed": self.passed,
            "skipped": self.skipped,
            "detail": self.detail,
        }


@dataclass
class GateReport:
    run_id: str
    target: str
    passed: bool
    exit_code: int
    checks: list[GateCheck] = field(default_factory=list)
    ratings: dict[str, str] = field(default_factory=dict)
    diff_counts: dict[str, int] = field(default_factory=dict)
    next_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "target": self.target,
            "passed": self.passed,
            "exit_code": self.exit_code,
            "ratings": self.ratings,
            "diff_counts": self.diff_counts,
            "checks": [c.to_dict() for c in self.checks],
            "next_steps": self.next_steps,
        }


def _rating_of(blocker: int, high: int, medium: int) -> str:
    """blocker/high/medium → A-E 的本地近似（比 Sonar 宽松，仅作自用口径）。"""
    if blocker == 0 and high == 0:
        return "A" if medium <= 5 else "B"
    if blocker == 0 and high <= 2:
        return "C"
    if blocker <= 1 and high <= 5:
        return "D"
    return "E"


def _cat_counts(
    findings: list[dict[str, object]], cats: set[str], sources: set[str] | None = None
) -> dict[str, int]:
    src = sources or set()
    counts = {"blocker": 0, "high": 0, "medium": 0}
    for f in findings:
        if str(f.get("category")) in cats or str(f.get("source")) in src:
            sev = str(f.get("severity"))
            if sev in counts:
                counts[sev] += 1
    return counts


def _coverage_percent(run: RunData) -> float | None:
    """从 run 的 raw/coverage.json 读总覆盖率；没有则 None。"""
    path = run.run_dir / "raw" / "coverage.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pct = (data.get("totals") or {}).get("percent_covered")
        return float(pct) if pct is not None else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def run_gate(
    project_root: Path,
    run_ref: str,
    *,
    baseline_ref: str | None = None,
    target: str = "A",
) -> GateReport:
    if target not in TARGETS:
        raise ValueError(f"target 只支持 {'/'.join(TARGETS)}，收到 {target!r}")
    run = load_run(project_root, run_ref)
    base: RunData | None = None
    if baseline_ref:
        from .baseline import load_baseline

        _, base = load_baseline(project_root, baseline_ref)

    active = run.findings_active
    checks: list[GateCheck] = []

    # 0) 工具执行健康度：有 error/timeout → 整体 exit 2（结果不完整不许当达标）
    tools = cast("list[object]", run.summary.get("tools") or [])
    if run.has_tool_errors():
        bad = [
            f"{t.get('name')}:{t.get('status')}"
            for t in tools
            if isinstance(t, dict) and t.get("status") in ("error", "timeout")
        ]
        checks.append(GateCheck("tools_ok", False, f"工具执行不完整: {', '.join(bad)}"))
        return _finish(run, target, checks, ratings={}, diff=None, exit_code=2)
    checks.append(GateCheck("tools_ok", True, f"{len(tools)} 个工具全部正常完成"))

    # 1) blocker / high 上限
    by_sev: dict[str, int] = {}
    for f in active:
        sev = str(f.get("severity"))
        by_sev[sev] = by_sev.get(sev, 0) + 1
    max_b, max_h = _TARGET_LIMITS[target]
    n_b, n_h = by_sev.get("blocker", 0), by_sev.get("high", 0)
    checks.append(GateCheck("blockers", n_b <= max_b, f"blocker {n_b}/{max_b}（目标 {target}）"))
    checks.append(GateCheck("highs", n_h <= max_h, f"high {n_h}/{max_h}（目标 {target}）"))

    # 2) 复杂度 D/E/F = 0
    cc = [f for f in active if str(f.get("rule")).startswith("cc-")]
    checks.append(
        GateCheck("complexity", not cc, f"圈复杂度 D/E/F 共 {len(cc)} 条" if cc else "无 D/E/F")
    )

    # 3) 覆盖率（有数据才判；pytest skipped 时点名跳过）
    cov = _coverage_percent(run)
    if cov is None:
        checks.append(
            GateCheck(
                "coverage", True, "无覆盖率数据（pytest/pytest-cov 未跑），本项跳过", skipped=True
            )
        )
    else:
        # 阈值沿用扫描时的配置（summary.config）
        cfg = cast("dict[str, object]", run.summary.get("config") or {})
        threshold = float(cast("float", cfg.get("coverage_threshold", 80.0)))
        checks.append(
            GateCheck("coverage", cov >= threshold, f"覆盖率 {cov:.1f}%（阈值 {threshold:g}%）")
        )

    # 4) 新增 blocker/high = 0（有基线才判）
    diff: DiffResult | None = None
    if base is not None:
        diff = diff_findings(base.findings, run.findings)
        n_new_bh = len(diff.new_blocker_high)
        checks.append(
            GateCheck(
                "new_blocker_high",
                n_new_bh == 0,
                f"新增 blocker/high {n_new_bh} 条"
                + (
                    f"：{'、'.join(_short(f) for f in diff.new_blocker_high[:3])}"
                    if n_new_bh
                    else ""
                ),
            )
        )
    else:
        checks.append(
            GateCheck(
                "new_blocker_high", True, "未提供基线，本项跳过（--baseline 可启用）", skipped=True
            )
        )

    # 5) 本地近似评级
    security = _cat_counts(active, {"security"}, {"deepsec"})
    reliability = _cat_counts(active, {"reliability"}, {"pytest"})
    maintainability = _cat_counts(active, {"maintainability", "architecture"})
    ratings = {
        "security": _rating_of(security["blocker"], security["high"], security["medium"]),
        "reliability": _rating_of(
            reliability["blocker"], reliability["high"], reliability["medium"]
        ),
        "maintainability": _rating_of(
            maintainability["blocker"], maintainability["high"], maintainability["medium"]
        ),
        "note": "local_rating 近似口径，非 Sonar 官方评级",
    }

    failed = [c for c in checks if not c.passed]
    exit_code = 1 if failed else 0
    return _finish(run, target, checks, ratings, diff, exit_code)


def _short(f: dict[str, object]) -> str:
    return f"{f.get('source')}:{f.get('rule')}@{f.get('file')}:{f.get('line')}"


def _finish(
    run: RunData,
    target: str,
    checks: list[GateCheck],
    ratings: dict[str, str],
    diff: DiffResult | None,
    exit_code: int,
) -> GateReport:
    active = [f for f in run.findings if f.get("status") != "suppressed"]
    report = GateReport(
        run_id=run.run_id,
        target=target,
        passed=exit_code == 0,
        exit_code=exit_code,
        checks=checks,
        ratings=ratings,
        diff_counts=diff.summary_counts() if diff else {},
        next_steps=_next_steps_dicts(active),
    )
    return report


def _next_steps_dicts(active: list[dict[str, object]]) -> list[str]:
    if not active:
        return ["没有活跃 findings：维持现状，关注新增代码的门槛（diff + gate）"]
    order = {"blocker": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    worst = sorted(active, key=lambda f: order.get(str(f.get("severity")), 9))
    steps = []
    for f in worst[:3]:
        steps.append(
            f"{f.get('severity')} · {f.get('source')}:{f.get('rule')} · {f.get('file')}:{f.get('line')} — {f.get('message')}"
        )
    return steps


def render_console(report: GateReport) -> str:
    icon = "✅ 达标" if report.passed else "❌ 未达标"
    lines = [f"gate · {report.run_id} · 目标 {report.target} → {icon}"]
    lines.append("")
    for c in report.checks:
        mark = "✅" if c.passed and not c.skipped else ("⏭️" if c.skipped else "❌")
        lines.append(f"  {mark} {c.name:<18} {c.detail}")
    if report.ratings:
        lines.append("")
        ratings = {k: v for k, v in report.ratings.items() if k != "note"}
        lines.append(
            "local_rating："
            + "、".join(f"{k} {v}" for k, v in ratings.items())
            + f"（{report.ratings.get('note')}）"
        )
    if report.diff_counts:
        lines.append(
            "对比基线：" + "、".join(f"{k} {v}" for k, v in report.diff_counts.items() if v)
        )
    if report.next_steps:
        lines.append("")
        lines.append("下一步最有效的 3 件事：")
        for s in report.next_steps:
            lines.append(f"  - {s}")
    return "\n".join(lines)
