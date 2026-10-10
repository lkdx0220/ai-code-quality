"""ruff check：lint、未使用导入/变量等（默认规则集，尊重项目自己的 ruff 配置）。"""

from __future__ import annotations

import json

from ..schema import AdapterResult, Finding
from .base import Adapter, ScanContext, finding


def classify_ruff(code: str) -> tuple[str, str]:
    """(category, severity)：按规则前缀粗分，覆盖默认规则集 + S/B 常见扩展。"""
    if not code:
        return "maintainability", "medium"
    if code.startswith("E9") or code == "F821":  # 语法错误 / 未定义名
        return "reliability", "high"
    if code.startswith("S"):  # flake8-bandit 安全面
        return "security", "high"
    if code.startswith(("F", "B")):  # 未使用导入/变量、bugbear
        return "maintainability", "medium"
    if code.startswith("E4") or code.startswith("W"):
        return "maintainability", "low"
    return "maintainability", "medium"


def parse_ruff_json(text: str) -> list[dict[str, object]]:
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


class RuffCheckAdapter(Adapter):
    name = "ruff-check"
    tool = "ruff"
    description = "ruff check（lint / 未使用导入 / bugbear / bandit）"

    def run(self, ctx: ScanContext) -> AdapterResult:
        from ..runner import probe, run_cmd

        ver = probe([ctx.cfg.python, "-m", "ruff", "--version"])
        if ver.not_found or ver.returncode != 0:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="skipped",
                reason=f"ruff 不可用（python -m ruff --version 失败: {ver.stderr.strip()[:120]}）",
            )
        version = ver.stdout.strip().replace("ruff ", "")

        if not ctx.targets:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="ok",
                version=version,
                reason="没有可扫描的 .py 目标",
            )

        cmd = [
            ctx.cfg.python,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--output-format",
            "json",
            *[str(t) for t in ctx.targets],
        ]
        proc = run_cmd(cmd, timeout=ctx.cfg.timeout_for(self.name), cwd=ctx.project_root)
        raw_ref = self.write_raw(ctx, "ruff-check.json", proc.stdout or "[]")
        if proc.timed_out:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="timeout",
                version=version,
                reason=f"ruff check 超时（>{ctx.cfg.timeout_for(self.name)}s）",
                raw_files=[raw_ref],
            )
        if proc.not_found:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="skipped",
                version=version,
                reason="无法启动 ruff 进程",
            )
        # ruff: 0=干净 1=有违规 2=出错
        if proc.returncode not in (0, 1):
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="error",
                version=version,
                reason=f"ruff check 退出码 {proc.returncode}: {proc.stderr.strip()[:200]}",
                raw_files=[raw_ref],
            )

        findings: list[Finding] = []
        for item in parse_ruff_json(proc.stdout):
            code = str(item.get("code") or "")
            category, severity = classify_ruff(code)
            loc = item.get("location") or {}
            fix = item.get("fix")
            findings.append(
                finding(
                    ctx,
                    source="ruff",
                    category=category,
                    severity=severity,
                    file=str(item.get("filename") or ""),
                    line=int(loc.get("row") or 0),
                    message=str(item.get("message") or code),
                    rule=code,
                    auto_fixable=bool(fix),
                    fix_hint=f"ruff rule {code}" + ("（可 ruff --fix 自动修复）" if fix else ""),
                    raw_ref=raw_ref,
                )
            )
        return AdapterResult(
            name=self.name,
            tool=self.tool,
            status="ok",
            version=version,
            duration_s=proc.duration_s,
            findings=findings,
            raw_files=[raw_ref],
        )
