"""radon：圈复杂度（cc）+ 可维护性指数（mi）。"""

from __future__ import annotations

import json

from ..schema import AdapterResult, Finding
from .base import Adapter, ScanContext, finding

CC_SEVERITY = {"A": "info", "B": "info", "C": "info", "D": "medium", "E": "high", "F": "blocker"}
MI_SEVERITY = {"A": "info", "B": "info", "C": "low", "D": "medium", "E": "high"}
_RANK_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5}


def _parse_cc_json(text: str) -> dict[str, list[dict[str, object]]]:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _parse_mi_json(text: str) -> dict[str, dict[str, object]]:
    """radon mi -j 兼容两种形态：{"f": {"mi": 63, "rank": "C"}} 与 {"f": [63, "C"]]}。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    out: dict[str, dict[str, object]] = {}
    if isinstance(data, dict):
        for file, val in data.items():
            if isinstance(val, dict) and "rank" in val:
                out[file] = {"mi": val.get("mi", 0.0), "rank": str(val.get("rank", "A"))}
            elif isinstance(val, list) and len(val) == 2:
                out[file] = {"mi": val[0], "rank": str(val[1])}
    return out


class RadonAdapter(Adapter):
    name = "radon"
    tool = "radon"
    description = "radon cc/mi（圈复杂度 / 可维护性指数）"

    def run(self, ctx: ScanContext) -> AdapterResult:
        from ..runner import probe, run_cmd

        ver = probe([ctx.cfg.python, "-m", "radon", "--version"])
        if ver.not_found or ver.returncode != 0:
            return AdapterResult(name=self.name, tool=self.tool, status="skipped",
                                 reason="radon 不可用（未安装）")
        version = ver.stdout.strip().splitlines()[-1] if ver.stdout.strip() else ""

        if not ctx.targets:
            return AdapterResult(name=self.name, tool=self.tool, status="ok", version=version,
                                 reason="没有可分析的 .py 目标")

        findings: list[Finding] = []
        raw_files: list[str] = []
        total_s = 0.0

        cc_cmd = [ctx.cfg.python, "-m", "radon", "cc", "-s", "-j", "--total-average",
                  *[str(t) for t in ctx.targets]]
        proc = run_cmd(cc_cmd, timeout=ctx.cfg.timeout_for(self.name), cwd=ctx.project_root)
        cc_raw = self.write_raw(ctx, "radon-cc.json", proc.stdout)
        raw_files.append(cc_raw)
        if proc.timed_out:
            return AdapterResult(name=self.name, tool=self.tool, status="timeout", version=version,
                                 reason=f"radon cc 超时（>{ctx.cfg.timeout_for(self.name)}s）", raw_files=raw_files)
        if proc.returncode != 0:
            return AdapterResult(name=self.name, tool=self.tool, status="error", version=version,
                                 reason=f"radon cc 退出码 {proc.returncode}: {proc.stderr.strip()[:200]}",
                                 raw_files=raw_files)
        total_s += proc.duration_s

        worst_allowed = _RANK_ORDER.get(ctx.cfg.max_cc_rank.upper(), 3)
        for file, blocks in _parse_cc_json(proc.stdout).items():
            # radon 对解析不了的文件输出字符串 "error" 而不是块列表
            if not isinstance(blocks, list):
                continue
            block_queue: list[tuple[dict[str, object], str]] = []
            for b in blocks:
                if isinstance(b, str):
                    findings.append(finding(
                        ctx,
                        source="radon",
                        category="reliability",
                        severity="medium",
                        file=file,
                        line=0,
                        message=f"radon 无法解析该文件（{b}）",
                        rule="cc-parse-error",
                        raw_ref=cc_raw,
                    ))
                    continue
                if not isinstance(b, dict):
                    continue
                kind = str(b.get("type", "function"))
                block_queue.append((b, kind))
                # class 块的方法嵌套在 "methods" 里，也是拆分对象
                for m in b.get("methods", []) or []:
                    if isinstance(m, dict):
                        block_queue.append((m, str(m.get("type", "method"))))
            for b, kind in block_queue:
                rank = str(b.get("rank", "A"))
                if _RANK_ORDER.get(rank, 0) < worst_allowed:
                    continue
                findings.append(finding(
                    ctx,
                    source="radon",
                    category="maintainability",
                    severity=CC_SEVERITY.get(rank, "medium"),
                    file=file,
                    line=int(b.get("lineno") or 0),
                    message=f"{kind} '{b.get('name', '?')}' 圈复杂度 "
                            f"{b.get('complexity', '?')}（rank {rank}）",
                    rule=f"cc-{rank}",
                    fix_hint="拆分函数/分支，目标 rank ≤ C",
                    raw_ref=cc_raw,
                ))

        mi_cmd = [ctx.cfg.python, "-m", "radon", "mi", "-s", "-j", *[str(t) for t in ctx.targets]]
        proc = run_cmd(mi_cmd, timeout=ctx.cfg.timeout_for(self.name), cwd=ctx.project_root)
        mi_raw = self.write_raw(ctx, "radon-mi.json", proc.stdout)
        raw_files.append(mi_raw)
        if not proc.timed_out and proc.returncode == 0:
            total_s += proc.duration_s
            mi_worst = _RANK_ORDER.get(ctx.cfg.min_mi_rank.upper(), 2)
            for file, info in _parse_mi_json(proc.stdout).items():
                rank = str(info.get("rank", "A"))
                # mi 的 rank 越大越差，与 cc 同向可比
                if _RANK_ORDER.get(rank, 0) < mi_worst:
                    continue
                findings.append(finding(
                    ctx,
                    source="radon",
                    category="maintainability",
                    severity=MI_SEVERITY.get(rank, "low"),
                    file=file,
                    line=0,
                    message=f"可维护性指数 {info.get('mi', '?')}（rank {rank}）",
                    rule=f"mi-{rank}",
                    raw_ref=mi_raw,
                ))
        elif proc.timed_out:
            return AdapterResult(name=self.name, tool=self.tool, status="timeout", version=version,
                                 reason=f"radon mi 超时", raw_files=raw_files)

        return AdapterResult(name=self.name, tool=self.tool, status="ok", version=version,
                             duration_s=total_s, findings=findings, raw_files=raw_files)
