"""mypy：类型错误。项目有自己的 mypy 配置则完全尊重之，否则补 --ignore-missing-imports。"""

from __future__ import annotations

import re

from ..schema import AdapterResult, Finding
from .base import Adapter, ScanContext, finding

# file:line: error: message  [error-code]
_MYPY_LINE = re.compile(r"^(.+?):(\d+): (error|warning|note): (.*?)(?:\s+\[([a-z0-9-]+)\])?$")


def parse_mypy_output(text: str) -> list[tuple[str, int, str, str, str]]:
    """→ [(file, line, level, message, code)]；note 不产出（太噪）。"""
    out: list[tuple[str, int, str, str, str]] = []
    for ln in text.splitlines():
        m = _MYPY_LINE.match(ln)
        if not m:
            continue
        file, line, level, msg, code = m.groups()
        out.append((file, int(line), level, msg, code or ""))
    return out


class MypyAdapter(Adapter):
    name = "mypy"
    tool = "mypy"
    description = "mypy（类型错误）"

    def run(self, ctx: ScanContext) -> AdapterResult:
        from ..runner import probe, run_cmd

        ver = probe([ctx.cfg.python, "-m", "mypy", "--version"])
        if ver.not_found or ver.returncode != 0:
            return AdapterResult(name=self.name, tool=self.tool, status="skipped",
                                 reason="mypy 不可用（未安装）")
        version = ver.stdout.strip().splitlines()[0] if ver.stdout.strip() else ""

        if not ctx.targets:
            return AdapterResult(name=self.name, tool=self.tool, status="ok", version=version,
                                 reason="没有可检查的 .py 目标")

        cache_dir = ctx.raw_dir / ".mypy-cache"
        cmd = [ctx.cfg.python, "-m", "mypy",
               "--show-error-codes", "--no-error-summary", "--hide-error-context",
               f"--cache-dir={cache_dir}",
               *[str(t) for t in ctx.targets]]
        if ctx.mypy_config is None:
            # 无项目配置时避免被三方依赖的 import 报错淹没；
            # explicit-package-bases 避免 root conftest + 子包同时作为目标时的模块重名报错
            cmd += ["--ignore-missing-imports", "--explicit-package-bases"]

        proc = run_cmd(cmd, timeout=ctx.cfg.timeout_for(self.name), cwd=ctx.project_root)
        raw_ref = self.write_raw(ctx, "mypy.txt", proc.stdout)
        if proc.timed_out:
            return AdapterResult(name=self.name, tool=self.tool, status="timeout", version=version,
                                 reason=f"mypy 超时（>{ctx.cfg.timeout_for(self.name)}s）", raw_files=[raw_ref])
        if proc.not_found:
            return AdapterResult(name=self.name, tool=self.tool, status="skipped", version=version,
                                 reason="无法启动 mypy 进程")
        if proc.returncode not in (0, 1):  # mypy: 0=干净 1=有错误 2=致命错误
            return AdapterResult(name=self.name, tool=self.tool, status="error", version=version,
                                 reason=f"mypy 退出码 {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:200]}",
                                 raw_files=[raw_ref])

        findings: list[Finding] = []
        for file, line, level, msg, code in parse_mypy_output(proc.stdout):
            if level == "note":
                continue
            findings.append(finding(
                ctx,
                source="mypy",
                category="maintainability",
                severity="medium" if level == "error" else "low",
                file=file,
                line=line,
                message=msg,
                rule=code or "mypy",
                fix_hint=f"修复类型错误 [{code}]" if code else "",
                raw_ref=raw_ref,
            ))
        return AdapterResult(name=self.name, tool=self.tool, status="ok", version=version,
                             duration_s=proc.duration_s, findings=findings, raw_files=[raw_ref])
