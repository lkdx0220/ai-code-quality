"""vulture：死代码 / 未使用变量与函数。"""

from __future__ import annotations

import re

from ..schema import AdapterResult, Finding
from .base import Adapter, ScanContext, finding

_VULTURE_LINE = re.compile(r"^(.+?):(\d+): (.+?) \((\d+)% confidence\)$")


def parse_vulture_output(text: str) -> list[tuple[str, int, str, int]]:
    """→ [(file, line, message, confidence%)]"""
    out: list[tuple[str, int, str, int]] = []
    for ln in text.splitlines():
        m = _VULTURE_LINE.match(ln.strip())
        if m:
            file, line, msg, conf = m.groups()
            out.append((file, int(line), msg, int(conf)))
    return out


class VultureAdapter(Adapter):
    name = "vulture"
    tool = "vulture"
    description = "vulture（死代码 / 未使用符号）"

    def run(self, ctx: ScanContext) -> AdapterResult:
        from ..runner import probe, run_cmd

        ver = probe([ctx.cfg.python, "-m", "vulture", "--version"])
        if ver.not_found or ver.returncode != 0:
            return AdapterResult(name=self.name, tool=self.tool, status="skipped",
                                 reason="vulture 不可用（未安装）")
        version = ver.stdout.strip().splitlines()[-1] if ver.stdout.strip() else ""

        if not ctx.targets:
            return AdapterResult(name=self.name, tool=self.tool, status="ok", version=version,
                                 reason="没有可分析的 .py 目标")

        cmd = [ctx.cfg.python, "-m", "vulture", *[str(t) for t in ctx.targets],
               "--min-confidence", str(ctx.cfg.vulture_min_confidence)]
        proc = run_cmd(cmd, timeout=ctx.cfg.timeout_for(self.name), cwd=ctx.project_root)
        raw_ref = self.write_raw(ctx, "vulture.txt", proc.stdout)
        if proc.timed_out:
            return AdapterResult(name=self.name, tool=self.tool, status="timeout", version=version,
                                 reason=f"vulture 超时（>{ctx.cfg.timeout_for(self.name)}s）", raw_files=[raw_ref])
        if proc.not_found:
            return AdapterResult(name=self.name, tool=self.tool, status="skipped", version=version,
                                 reason="无法启动 vulture 进程")
        # vulture 实测退出码：0=干净，3=发现死代码（2.16）；其余按错误处理
        if proc.returncode not in (0, 3):
            return AdapterResult(name=self.name, tool=self.tool, status="error", version=version,
                                 reason=f"vulture 退出码 {proc.returncode}: {proc.stderr.strip()[:200]}",
                                 raw_files=[raw_ref])

        findings = [
            finding(
                ctx,
                source="vulture",
                category="maintainability",
                severity="medium" if conf >= 90 else "low",
                file=file,
                line=line,
                message=msg,
                rule=msg.split()[0] if msg else "vulture",  # "unused function 'x'" → unused
                fix_hint="确认后删除，或加白名单（vulture whitelist）",
                confidence="high" if conf >= 90 else "medium",
                raw_ref=raw_ref,
            )
            for file, line, msg, conf in parse_vulture_output(proc.stdout)
        ]
        return AdapterResult(name=self.name, tool=self.tool, status="ok", version=version,
                             duration_s=proc.duration_s, findings=findings, raw_files=[raw_ref])
