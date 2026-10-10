"""ruff format --check：格式一致性。"""

from __future__ import annotations

import json

from ..schema import AdapterResult
from .base import Adapter, ScanContext, finding


def parse_format_json(text: str) -> list[str]:
    """ruff format --check --output-format json → 与 check 同形的诊断列表（filename 键）；兼容文本输出。"""
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [
                str(it.get("filename") or it.get("path") or "")
                for it in data
                if it.get("filename") or it.get("path")
            ]
    except json.JSONDecodeError:
        pass
    return [
        ln.split("Would reformat:", 1)[1].strip()
        for ln in text.splitlines()
        if ln.startswith("Would reformat:")
    ]


class RuffFormatAdapter(Adapter):
    name = "ruff-format"
    tool = "ruff"
    description = "ruff format --check（格式一致性）"

    def run(self, ctx: ScanContext) -> AdapterResult:
        from ..runner import probe, run_cmd

        ver = probe([ctx.cfg.python, "-m", "ruff", "--version"])
        if ver.not_found or ver.returncode != 0:
            return AdapterResult(
                name=self.name, tool=self.tool, status="skipped", reason="ruff 不可用"
            )
        version = ver.stdout.strip().replace("ruff ", "")

        if not ctx.targets:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="ok",
                version=version,
                reason="没有可检查的 .py 目标",
            )

        cmd = [
            ctx.cfg.python,
            "-m",
            "ruff",
            "format",
            "--check",
            "--no-cache",
            "--output-format",
            "json",
            *[str(t) for t in ctx.targets],
        ]
        proc = run_cmd(cmd, timeout=ctx.cfg.timeout_for(self.name), cwd=ctx.project_root)
        raw_ref = self.write_raw(ctx, "ruff-format.json", proc.stdout or "[]")
        if proc.timed_out:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="timeout",
                version=version,
                reason=f"ruff format 超时（>{ctx.cfg.timeout_for(self.name)}s）",
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
        if proc.returncode not in (0, 1):
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="error",
                version=version,
                reason=f"ruff format 退出码 {proc.returncode}: {proc.stderr.strip()[:200]}",
                raw_files=[raw_ref],
            )

        findings = [
            finding(
                ctx,
                source="ruff-format",
                category="maintainability",
                severity="low",
                file=path,
                line=0,
                message="格式与 ruff format 不一致",
                rule="format",
                auto_fixable=True,
                fix_hint="ruff format <file> 可自动修复",
                raw_ref=raw_ref,
            )
            for path in parse_format_json(proc.stdout)
        ]
        return AdapterResult(
            name=self.name,
            tool=self.tool,
            status="ok",
            version=version,
            duration_s=proc.duration_s,
            findings=findings,
            raw_files=[raw_ref],
        )
