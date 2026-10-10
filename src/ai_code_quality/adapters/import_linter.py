"""import-linter：架构依赖契约。只在项目有配置时启用。

import-linter 2.x 没有 `python -m` 入口，必须用 `lint-imports` 控制台脚本。
"""

from __future__ import annotations

import re
import shutil

from ..schema import AdapterResult
from .base import Adapter, ScanContext, finding

_CONTRACT = re.compile(r"^\*\s+(\S+)\s+(kept|broken|skipped|ignored)\s*$")
_BROKEN_HEADER = re.compile(r"^\d+ contracts? broken", re.IGNORECASE)


def parse_contracts(text: str) -> tuple[list[str], list[str], list[list[str]]]:
    """→ (kept_ids, broken_ids, broken_blocks)。broken 块为 header 之后的说明文本。"""
    kept: list[str] = []
    broken: list[str] = []
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for ln in text.splitlines():
        ln = ln.rstrip()
        m = _CONTRACT.match(ln.strip())
        if m:
            cid, state = m.groups()
            if state == "kept":
                kept.append(cid)
            elif state == "broken":
                broken.append(cid)
            continue
        if _BROKEN_HEADER.match(ln.strip()):
            continue
        # "N contracted broken" 分节标题形如 "<id> (<name>)"
        if broken and re.match(r"^-\+$", ln.strip()) is None and current is None:
            hm = re.match(r"^(\S+)\s+\((.+)\)$", ln.strip())
            if hm and hm.group(1) in broken:
                current = [f"{hm.group(1)}: {hm.group(2)}"]
                blocks.append(current)
                continue
        if current is not None:
            if ln.strip() and not set(ln.strip()) <= {"-", "=", "*"}:
                current.append(ln.strip())
            elif current and ln.strip() == "":
                current = None
    return kept, broken, blocks


class ImportLinterAdapter(Adapter):
    name = "import-linter"
    tool = "import-linter"
    description = "import-linter（架构依赖契约）"

    def run(self, ctx: ScanContext) -> AdapterResult:
        from ..runner import run_cmd

        if ctx.importlinter_config is None:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="skipped",
                reason="未发现 import-linter 配置（.importlinter / pyproject [tool.importlinter] / setup.cfg）",
            )

        exe = shutil.which("lint-imports")
        if exe is None:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="skipped",
                reason="lint-imports 不在 PATH（import-linter 未安装）",
            )

        ver = run_cmd([exe, "--version"], timeout=30)
        version = (
            (ver.stdout or ver.stderr).strip().splitlines()[-1]
            if (ver.stdout or ver.stderr).strip()
            else ""
        )

        cmd = [exe, "--no-cache", "--no-logo", "--config", str(ctx.importlinter_config)]
        proc = run_cmd(cmd, timeout=ctx.cfg.timeout_for(self.name), cwd=ctx.project_root)
        raw_ref = self.write_raw(
            ctx,
            "import-linter.txt",
            proc.stdout + ("\n--stderr--\n" + proc.stderr if proc.stderr else ""),
        )
        if proc.timed_out:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="timeout",
                version=version,
                reason=f"lint-imports 超时（>{ctx.cfg.timeout_for(self.name)}s）",
                raw_files=[raw_ref],
            )
        if proc.returncode == 0:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="ok",
                version=version,
                duration_s=proc.duration_s,
                findings=[],
                raw_files=[raw_ref],
            )
        # 退出码非 0：可能是违约（1）也可能是配置错误（2）；按输出区分
        combined = proc.stdout + proc.stderr
        if "Traceback" in combined or "Error" in combined.split("\n\n")[0]:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="error",
                version=version,
                reason=f"lint-imports 执行失败: {combined.strip()[:200]}",
                raw_files=[raw_ref],
            )

        _kept, broken, blocks = parse_contracts(proc.stdout)
        findings = []
        for i, cid in enumerate(broken):
            evidence = "\n".join(blocks[i]) if i < len(blocks) else ""
            findings.append(
                finding(
                    ctx,
                    source="import-linter",
                    category="architecture",
                    severity="medium",
                    file="",
                    line=0,
                    message=f"架构契约违约: {cid}",
                    rule="import-contract",
                    evidence=evidence[:500],
                    fix_hint="按契约调整 import 方向或更新契约",
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
