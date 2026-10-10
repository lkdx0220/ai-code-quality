"""cq doctor：环境体检——告诉人和 AI"工具缺什么、去哪装、装到哪"。

设计动机：cq 是编排器，能力来自外部工具。新机器上直接 scan 会得到一排 skipped，
所以安装引导必须内建：doctor 是唯一维护"安装知识"的地方，
README / PROMPT.md / Agent 都只教"跑 doctor、按提示装"，不复制安装命令细节。
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .config import Config
from .runner import probe

# 实测通过的版本（2026-10，Python 3.13 / Windows）。
# ruff 等工具的默认规则集会随版本变化，版本差异影响结果可比性。
TESTED_VERSIONS = {
    "ruff": "0.16.9",
    "mypy": "2.3.1",
    "radon": "6.0.1",
    "vulture": "2.16",
    "pytest": "9.1.1",
    "pytest-cov": "7.1.0",
    "import-linter": "2.15",
    "deepsec": "0.2.0",
}

DEEPSEC_REPO = "https://github.com/Unclecheng-li/DeepSec"


@dataclass
class ToolCheck:
    name: str  # adapter 名
    tool: str
    available: bool
    version: str = ""
    form: str = ""  # 探测到的形态（如 deepsec 的 pip/源码）
    install: str = ""  # 缺失时给出的安装命令
    note: str = ""  # 用途/降级说明
    optional: bool = False  # True=缺失可降级，False=核心检查能力
    detail: str = ""  # 探测失败原因

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "tool": self.tool,
            "available": self.available,
            "version": self.version,
            "form": self.form,
            "optional": self.optional,
            "install": self.install,
            "note": self.note,
            "detail": self.detail,
            "tested_version": TESTED_VERSIONS.get(self.tool, ""),
        }


def _clean_version(raw: str, prefixes: tuple[str, ...] = ()) -> str:
    line = raw.strip().splitlines()[0].strip() if raw.strip() else ""
    for p in prefixes:
        if line.lower().startswith(p.lower()):
            line = line[len(p) :].strip()
    return line


def _first_line(stderr: str) -> str:
    for ln in stderr.strip().splitlines():
        if ln.strip():
            return ln.strip()[:120]
    return ""


def _check_pymod(
    py: str, module: str, name: str, tool: str, install: str, note: str, optional: bool, prefix: str
) -> ToolCheck:
    r = probe([py, "-m", module, "--version"], timeout=30)
    ok = (not r.not_found) and r.returncode == 0
    return ToolCheck(
        name=name,
        tool=tool,
        available=ok,
        version=_clean_version(r.stdout, (prefix,)) if ok else "",
        install=install,
        note=note,
        optional=optional,
        detail="" if ok else _first_line(r.stderr) or "python -m 探测失败",
    )


def _check_import(
    py: str, module: str, name: str, tool: str, install: str, note: str, optional: bool
) -> ToolCheck:
    """适用于没有 python -m 入口、只能 import 探测的包（如 pytest_cov）。"""
    r = probe(
        [py, "-c", f"import {module}; print(getattr({module}, '__version__', 'unknown'))"],
        timeout=30,
    )
    ok = (not r.not_found) and r.returncode == 0
    return ToolCheck(
        name=name,
        tool=tool,
        available=ok,
        version=r.stdout.strip() if ok else "",
        install=install,
        note=note,
        optional=optional,
        detail="" if ok else _first_line(r.stderr) or "import 探测失败",
    )


def _check_deepsec(cfg: Config) -> ToolCheck:
    py = cfg.deepsec_python or cfg.python
    install_pip = f"pip install git+{DEEPSEC_REPO}"
    install_src = (
        f"git clone {DEEPSEC_REPO} <任意路径>，然后设置环境变量 "
        f"CQ_DEEPSEC_HOME=<该路径>（cq 会通过 PYTHONPATH 调用，无需安装）"
    )
    r = probe([py, "-m", "deepsec", "--help"], timeout=60)
    if r.returncode == 0:
        v = probe(
            [py, "-c", "import importlib.metadata as m; print(m.version('deepsec'))"], timeout=30
        )
        version = v.stdout.strip() if v.returncode == 0 else "unknown"
        return ToolCheck(
            name="deepsec",
            tool="deepsec",
            available=True,
            version=version,
            form="pip 安装",
            note="安全扫描 L1/L2（缺失时安全维度无结论，其余工具不受影响）",
            optional=True,
            install=install_pip,
        )
    if cfg.deepsec_home:
        home = Path(cfg.deepsec_home)
        r2 = probe(
            [py, "-m", "deepsec", "--help"],
            timeout=60,
            extra_env={"PYTHONPATH": str(home.resolve())},
        )
        if r2.returncode == 0:
            version = _deepsec_source_version(home)
            return ToolCheck(
                name="deepsec",
                tool="deepsec",
                available=True,
                version=version,
                form=f"源码仓库（CQ_DEEPSEC_HOME={cfg.deepsec_home}）",
                note="安全扫描 L1/L2（缺失时安全维度无结论，其余工具不受影响）",
                optional=True,
                install=install_pip,
            )
        return ToolCheck(
            name="deepsec",
            tool="deepsec",
            available=False,
            install=f"{install_pip}\n或：{install_src}",
            note="安全扫描 L1/L2",
            optional=True,
            detail=f"CQ_DEEPSEC_HOME={cfg.deepsec_home} 下探测失败：{_first_line(r2.stderr)}",
        )
    return ToolCheck(
        name="deepsec",
        tool="deepsec",
        available=False,
        install=f"{install_pip}\n或：{install_src}",
        note="安全扫描 L1/L2（缺失时安全维度无结论，其余工具不受影响）",
        optional=True,
        detail="未 pip 安装，且未设置 CQ_DEEPSEC_HOME",
    )


def _deepsec_source_version(home: Path) -> str:
    import tomllib

    try:
        with open(home / "pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        v = data.get("project", {}).get("version")
        return f"{v}（源码）" if v else "unknown（源码）"
    except (OSError, tomllib.TOMLDecodeError):
        return "unknown（源码）"


def run_doctor(
    cfg: Config, probe_fn: Callable[[], list[ToolCheck]] | None = None
) -> list[ToolCheck]:
    """probe_fn 供测试注入；默认真实探测。"""
    if probe_fn is not None:
        return probe_fn()
    py = cfg.python
    checks = [
        _check_pymod(
            py,
            "ruff",
            "ruff-check",
            "ruff",
            "pip install ruff",
            "lint / 未使用导入 / 危险写法 / 格式检查（核心）",
            False,
            "ruff",
        ),
        _check_pymod(
            py, "mypy", "mypy", "mypy", "pip install mypy", "静态类型检查（核心）", False, "mypy"
        ),
        _check_pymod(
            py,
            "radon",
            "radon",
            "radon",
            "pip install radon",
            "圈复杂度 / 可维护性指数（核心）",
            False,
            "radon",
        ),
        _check_pymod(
            py,
            "vulture",
            "vulture",
            "vulture",
            "pip install vulture",
            "死代码检测（核心）",
            False,
            "vulture",
        ),
        _check_pymod(
            py,
            "pytest",
            "pytest",
            "pytest",
            "pip install pytest",
            "测试执行（核心；仓库无测试时自动 skipped）",
            False,
            "pytest",
        ),
        _check_import(
            py,
            "pytest_cov",
            "pytest-cov",
            "pytest-cov",
            "pip install pytest-cov",
            "覆盖率统计（可选：缺失时 pytest 照跑，仅无覆盖率结论）",
            True,
        ),
    ]
    # import-linter：入口是 lint-imports 控制台脚本，不在 python -m 里
    exe = shutil.which("lint-imports")
    if exe:
        r = probe([exe, "--version"], timeout=30)
        ok = r.returncode == 0
        ver = _clean_version(r.stdout or r.stderr)
    else:
        ok, ver = False, ""
    checks.append(
        ToolCheck(
            name="import-linter",
            tool="import-linter",
            available=ok,
            version=ver,
            install="pip install import-linter",
            note="架构依赖契约（可选：仅当目标仓库有配置时启用）",
            optional=True,
            detail="" if ok else "lint-imports 不在 PATH",
        )
    )
    checks.append(_check_deepsec(cfg))
    return checks


def doctor_summary(checks: list[ToolCheck]) -> dict[str, object]:
    missing_required = [c.name for c in checks if not c.available and not c.optional]
    missing_optional = [c.name for c in checks if not c.available and c.optional]
    return {
        "tools": [c.to_dict() for c in checks],
        "total": len(checks),
        "available": sum(1 for c in checks if c.available),
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "all_available": not missing_required and not missing_optional,
    }


def render_console(py: str, summary: dict[str, object]) -> str:
    lines: list[str] = ["cq doctor · 环境体检", ""]
    lines.append(f"工具解释器：{py}（CQ_PYTHON / --python 可覆盖）")
    lines.append("")
    tools = cast("list[dict[str, object]]", summary["tools"])
    for t in tools:
        if t["available"]:
            ver = f"{t['version']:<10}" if t["version"] else ""
            form = f"  [{t['form']}]" if t.get("form") else ""
            lines.append(f"  ✅ {t['name']:<14} {ver}{form}")
        else:
            lines.append(f"  ❌ {t['name']:<14} 缺失{'' if t['optional'] else '（核心）'}")
            for cmd in str(t["install"]).splitlines():
                lines.append(f"     安装：{cmd}")
            if t.get("detail"):
                lines.append(f"     详情：{t['detail']}")
    lines.append("")
    n_missing = cast("int", summary["total"]) - cast("int", summary["available"])
    if n_missing == 0:
        lines.append("全部工具就绪，可直接 cq scan。")
    else:
        lines.append(
            f"{summary['available']}/{summary['total']} 可用，{n_missing} 缺失。"
            "缺失不阻塞 cq scan（对应工具显示 skipped），但相应维度没有结论。"
        )
    tested = "、".join(f"{k} {v}" for k, v in TESTED_VERSIONS.items())
    lines.append(f"实测版本：{tested}")
    return "\n".join(lines)
