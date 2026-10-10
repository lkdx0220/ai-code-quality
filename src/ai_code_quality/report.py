"""report.md 与控制台输出。"""

from __future__ import annotations

from .redact import redact_text
from .schema import Finding

_STATUS_ICON = {"ok": "✅", "skipped": "⏭️", "timeout": "⏱️", "error": "❌"}
_SEV_ICON = {"blocker": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}
# 报告里各 severity 最多展开条数，防止报告爆炸；info 只计数
_SEV_LIMIT = {"blocker": 50, "high": 50, "medium": 20, "low": 10, "info": 0}


def build_report_md(summary: dict, findings: list[Finding]) -> str:
    totals = summary.get("totals") or {}
    lines: list[str] = []
    _header_section(lines, summary, totals)
    _tools_table(lines, summary)
    _severity_table(lines, totals)
    _finding_details(lines, findings, totals)
    _next_steps_section(lines, summary, findings)
    lines.append("---")
    lines.append(
        f"产物目录：`{summary['project_root']}/audits/{summary['run_id']}/`（summary.json / findings.json / raw/）"
    )
    return redact_text("\n".join(lines))


def _header_section(lines: list[str], summary: dict, totals: dict) -> None:
    git = summary.get("git") or {}
    lines.append(f"# 代码质量扫描报告 · {summary['run_id']}")
    lines.append("")
    lines.append(f"- 项目：`{summary['project_root']}`")
    lines.append(f"- 时间：{summary.get('created_at', '')}")
    lines.append(
        f"- 范围：{summary.get('scope')}（目标 {len(summary.get('scanned_targets', []))} 项）"
    )
    if git.get("is_repo"):
        lines.append(
            f"- Git：`{git.get('branch')}@{git.get('commit')}`，未提交文件 {git.get('dirty_files')} 个"
        )
    else:
        lines.append("- Git：非 git 仓库")
    lines.append(
        f"- 总 findings：**{totals.get('findings', 0)}**（其中可自动修复 {totals.get('auto_fixable', 0)}）"
    )
    lines.append("")


def _tools_table(lines: list[str], summary: dict) -> None:
    lines.append("## 工具状态")
    lines.append("")
    lines.append("| 工具 | 状态 | 版本 | 耗时 | findings | 说明 |")
    lines.append("|---|---|---|---|---|---|")
    for t in summary.get("tools", []):
        status = t.get("status", "?")
        icon = _STATUS_ICON.get(status, "❓")
        reason = str(t.get("reason") or "").replace("|", "\\|").replace("\n", " ")
        if not reason:
            reason = str(t.get("description") or "")
        lines.append(
            f"| {t.get('tool')} ({t.get('name')}) | {icon} {status} | {t.get('version', '')} "
            f"| {t.get('duration_s', 0)}s | {t.get('finding_count', 0)} | {reason} |"
        )
    lines.append("")


def _severity_table(lines: list[str], totals: dict) -> None:
    by_sev = totals.get("by_severity") or {}
    lines.append("## 分级统计")
    lines.append("")
    lines.append("| severity | 数量 |")
    lines.append("|---|---|")
    for sev in ("blocker", "high", "medium", "low", "info"):
        if by_sev.get(sev):
            lines.append(f"| {_SEV_ICON[sev]} {sev} | {by_sev[sev]} |")
    by_cat = totals.get("by_category") or {}
    if by_cat:
        lines.append("")
        lines.append("分类：" + "、".join(f"{k} {v}" for k, v in sorted(by_cat.items())))
    lines.append("")


def _finding_details(lines: list[str], findings: list[Finding], totals: dict) -> None:
    """findings 明细（分级限额）；info 只计数；suppressed 单列豁免节。"""
    for sev in ("blocker", "high", "medium", "low"):
        limit = _SEV_LIMIT[sev]
        group = [f for f in findings if f.severity == sev and f.status != "suppressed"]
        if limit == 0 or not group:
            continue
        lines.append(f"## {sev.upper()}（{len(group)}）")
        lines.append("")
        for f in group[:limit]:
            _finding_line(lines, sev, f)
        if len(group) > limit:
            lines.append(f"- …另有 {len(group) - limit} 条，见 findings.json")
        lines.append("")
    suppressed = [f for f in findings if f.status == "suppressed"]
    if suppressed:
        lines.append(f"## 已豁免（{len(suppressed)}）")
        lines.append("")
        for f in suppressed:
            action = f.suppression_action or "suppressed"
            lines.append(
                f"- ⚪ **[{f.source}] `{f.rule}`** `{f.file}:{f.line}` — {action}：{f.suppression_reason}"
            )
        lines.append("")
    info_count = totals.get("by_severity", {}).get("info") or 0
    if info_count:
        lines.append(f"另有 info 级 {info_count} 条，见 findings.json。")
        lines.append("")


def _finding_line(lines: list[str], sev: str, f: Finding) -> None:
    loc = f"`{f.file}:{f.line}`" if f.file else "（全局）"
    rule = f" `{f.rule}`" if f.rule else ""
    lines.append(f"- {_SEV_ICON[sev]} **[{f.source}]{rule}** {loc} — {f.message}")
    if f.evidence:
        lines.append(f"  - 证据：`{f.evidence[:160]}`")
    if f.fix_hint:
        lines.append(f"  - 建议：{f.fix_hint}")


def _next_steps_section(lines: list[str], summary: dict, findings: list[Finding]) -> None:
    lines.append("## 建议的下一步（启发式）")
    lines.append("")
    for step in _next_steps(findings):
        lines.append(f"- {step}")
    lines.append("")
    notes = summary.get("notes") or []
    if notes:
        lines.append("## 备注")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")


def _next_steps(findings: list[Finding]) -> list[str]:
    findings = [f for f in findings if f.status != "suppressed"]
    counts = _severity_security_counts(findings)
    steps = _prioritized_steps(findings, counts)
    if not steps:
        steps.append("没有 blocker/high：可转向覆盖率和架构治理（Phase 2 基线对比）")
    return steps[:5]


def _severity_security_counts(findings: list[Finding]) -> dict[str, list[Finding]]:
    return {
        "blockers": [f for f in findings if f.severity == "blocker"],
        "highs": [f for f in findings if f.severity == "high"],
        "security": [
            f for f in findings if f.category == "security" and f.severity in ("blocker", "high")
        ],
        "auto": [f for f in findings if f.auto_fixable],
        "complex": [
            f for f in findings if f.rule.startswith("cc-") and f.severity in ("high", "blocker")
        ],
    }


def _prioritized_steps(findings: list[Finding], counts: dict[str, list[Finding]]) -> list[str]:
    steps: list[str] = []
    if counts["blockers"]:
        steps.append(
            f"先处理 {len(counts['blockers'])} 个 blocker（含复杂度 rank F / critical 安全问题）"
        )
    if counts["security"]:
        steps.append(
            f"处理 {len(counts['security'])} 个高危安全问题（deepsec finding，先核对再修，密钥类必须轮换）"
        )
    if counts["complex"]:
        steps.append(f"拆分 {len(counts['complex'])} 个高复杂度函数（radon rank E/F）")
    if counts["highs"]:
        steps.append(f"排查 {len(counts['highs'])} 个 high（类型错误 / 失败测试 / 未定义名）")
    if counts["auto"]:
        steps.append(
            f"{len(counts['auto'])} 条可自动修复（ruff --fix / ruff format），建议作为第一批，改完跑测试"
        )
    return steps


def build_console_output(summary: dict, findings: list[Finding]) -> str:
    lines: list[str] = []
    lines.append(f"扫描完成：{summary['run_id']}")
    lines.append("")
    for t in summary.get("tools", []):
        status = t.get("status", "?")
        icon = _STATUS_ICON.get(status, "❓")
        extra = f"（{t.get('reason')}）" if t.get("reason") else ""
        lines.append(
            f"  {icon} {t.get('name'):<14} {status:<8} {t.get('finding_count', 0):>3} findings  {extra}"
        )
    totals = summary.get("totals") or {}
    by_sev = totals.get("by_severity") or {}
    lines.append("")
    sev_str = "  ".join(
        f"{s}:{by_sev.get(s, 0)}"
        for s in ("blocker", "high", "medium", "low", "info")
        if by_sev.get(s)
    )
    lines.append(f"  共 {totals.get('findings', 0)} findings  [{sev_str}]")
    lines.append("")
    lines.append(f"  报告：{summary['project_root']}/audits/{summary['run_id']}/report.md")
    return "\n".join(lines)
