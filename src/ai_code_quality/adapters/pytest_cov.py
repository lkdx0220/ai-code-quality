"""pytest + pytest-cov：失败测试 / 低覆盖率。

缓存与数据文件全部重定向到 audits/<run_id>/raw/，不在目标仓库落任何文件：
- -p no:cacheprovider（.pytest_cache）
- COVERAGE_FILE=raw/.coverage（根目录 .coverage）
- PYTHONDONTWRITEBYTECODE（__pycache__，runner 已统一注入）
"""

from __future__ import annotations

import json
import re

from ..schema import AdapterResult, Finding
from .base import Adapter, ScanContext, finding

_FAILED = re.compile(r"^(FAILED|ERROR)\s+(\S+)(?:\s+-\s+(.*))?$")
_SUMMARY = re.compile(r"^=+ (.*?) in ([\d.]+)s.*=+$")


def parse_pytest_failures(text: str) -> list[tuple[str, str, str]]:
    """→ [(FAILED|ERROR, test_id, detail)]"""
    out: list[tuple[str, str, str]] = []
    for ln in text.splitlines():
        m = _FAILED.match(ln.strip())
        if m:
            out.append((m.group(1), m.group(2), m.group(3) or ""))
    return out


def parse_coverage_json(text: str) -> dict[str, float] | None:
    try:
        data = json.loads(text)
        totals = data.get("totals") or {}
        pct = totals.get("percent_covered")
        if pct is None:
            return None
        return {
            "percent": float(pct),
            "covered_lines": float(totals.get("covered_lines", 0)),
            "num_statements": float(totals.get("num_statements", 0)),
        }
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


class PytestCovAdapter(Adapter):
    name = "pytest"
    tool = "pytest"
    description = "pytest + pytest-cov（测试失败 / 覆盖率）"

    def run(self, ctx: ScanContext) -> AdapterResult:
        from ..runner import probe, run_cmd

        if not ctx.has_tests or not ctx.test_paths:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="skipped",
                reason="未发现测试（无 tests/ 目录、test_*.py 或 pytest 配置）",
            )

        ver = probe([ctx.cfg.python, "-m", "pytest", "--version"])
        if ver.not_found or ver.returncode != 0:
            return AdapterResult(
                name=self.name, tool=self.tool, status="skipped", reason="pytest 不可用（未安装）"
            )
        version = ver.stdout.strip().splitlines()[0] if ver.stdout.strip() else ""

        cov_probe = probe([ctx.cfg.python, "-c", "import pytest_cov"])
        has_cov = cov_probe.returncode == 0

        raw_files: list[str] = []
        findings: list[Finding] = []

        cov_json = ctx.raw_dir / "coverage.json"
        cmd = [
            ctx.cfg.python,
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            "-p",
            "no:cacheprovider",
            *[str(p) for p in ctx.test_paths],
        ]
        if has_cov and ctx.source_dirs:
            # --cov 一次只吃一个值，多目录必须重复传 --cov，否则多余值会变成 pytest 位置参数
            for d in ctx.source_dirs:
                cmd += ["--cov", str(d)]
            cmd += [f"--cov-report=json:{cov_json}"]
        env = {"COVERAGE_FILE": str(ctx.raw_dir / ".coverage")}
        # 被测包可能没装进工具解释器（如 src 布局未 pip install -e），
        # 把项目根（和 src/）加进 PYTHONPATH，否则 pytest 收集时直接 ImportError
        import os

        path_parts: list[str] = [str(ctx.project_root)]
        src_dir = ctx.project_root / "src"
        if src_dir.is_dir():
            path_parts.append(str(src_dir))
        if os.environ.get("PYTHONPATH"):
            path_parts.append(os.environ["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(path_parts)
        proc = run_cmd(
            cmd, timeout=ctx.cfg.timeout_for(self.name), cwd=ctx.project_root, extra_env=env
        )
        raw_files.append(
            self.write_raw(
                ctx,
                "pytest.txt",
                proc.stdout + ("\n--stderr--\n" + proc.stderr if proc.stderr else ""),
            )
        )
        if proc.timed_out:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="timeout",
                version=version,
                reason=f"pytest 超时（>{ctx.cfg.timeout_for(self.name)}s）",
                raw_files=raw_files,
            )
        if proc.not_found:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="skipped",
                version=version,
                reason="无法启动 pytest 进程",
            )

        # 无论退出码如何，先把能解析的失败/错误测试都收进来（部分结果也是证据）
        for kind, test_id, detail in parse_pytest_failures(proc.stdout):
            file = test_id.split("::")[0]
            findings.append(
                finding(
                    ctx,
                    source="pytest",
                    category="test",
                    severity="high",
                    file=file,
                    line=0,
                    message=f"{'测试失败' if kind == 'FAILED' else '测试错误'}: {test_id}"
                    + (f"（{detail[:100]}）" if detail else ""),
                    rule=f"test-{kind.lower()}",
                    raw_ref=raw_files[0],
                )
            )

        if proc.returncode not in (0, 1, 5):
            # 2=interrupted 3=internal 4=usage（环境缺依赖等），携带已解析的部分结果
            tail = [ln for ln in (proc.stderr + proc.stdout).splitlines() if ln.strip()][-3:]
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="error",
                version=version,
                reason=f"pytest 退出码 {proc.returncode}: {' / '.join(tail)[:300]}",
                findings=findings,
                raw_files=raw_files,
            )

        if proc.returncode == 5:
            findings.append(
                finding(
                    ctx,
                    source="pytest",
                    category="test",
                    severity="medium",
                    file="",
                    line=0,
                    message="pytest 没有收集到任何测试",
                    rule="no-tests-collected",
                    raw_ref=raw_files[0],
                )
            )

        if has_cov and cov_json.is_file():
            cov_text = cov_json.read_text(encoding="utf-8", errors="replace")
            raw_files.append(self.write_raw(ctx, "coverage.json", cov_text))
            cov = parse_coverage_json(cov_text)
            if cov and cov["percent"] < ctx.cfg.coverage_threshold:
                findings.append(
                    finding(
                        ctx,
                        source="pytest",
                        category="test",
                        severity="medium" if cov["percent"] >= 50 else "high",
                        file="",
                        line=0,
                        message=f"总覆盖率 {cov['percent']:.1f}% < 阈值 {ctx.cfg.coverage_threshold:g}%"
                        f"（{int(cov['covered_lines'])}/{int(cov['num_statements'])} 行）",
                        rule="coverage",
                        fix_hint="优先给核心分支补测试",
                        raw_ref=raw_files[-1],
                    )
                )
        elif not has_cov:
            ctx.notes.append("pytest-cov 未安装，覆盖率检查跳过（其余测试检查正常）")

        return AdapterResult(
            name=self.name,
            tool=self.tool,
            status="ok",
            version=version,
            duration_s=proc.duration_s,
            findings=findings,
            raw_files=raw_files,
        )
