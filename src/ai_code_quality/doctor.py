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


# 兜底探测：cq 装在 uv tool 隔离 venv 时，自带解释器里没有外部工具，
# 但系统 Python 里往往全有。默认解释器缺核心工具且未被显式指定时，
# 自动找一台全齐的系统解释器顶上，避免"看着像缺 5 个工具、诱导重复安装"的陷阱。
REQUIRED_TOOLS = ("ruff", "mypy", "radon", "vulture", "pytest")
_SENTINEL = "ruff"


def _has_sentinel(py: str) -> bool:
    """单次哨兵探测（约 0.2s）：ruff 在则视为该解释器已配置过工具链。"""
    return probe([py, "-m", _SENTINEL, "--version"], timeout=20).returncode == 0


def _probe_required(py: str) -> set[str]:
    have: set[str] = set()
    for mod in REQUIRED_TOOLS:
        if probe([py, "-m", mod, "--version"], timeout=20).returncode == 0:
            have.add(mod)
    return have


def _candidate_pythons() -> list[str]:
    """系统解释器候选：Windows py 启动器 → PATH 上的 python/python3，去重保序。"""
    out: list[str] = []
    r = probe(["py", "-3", "-c", "import sys; print(sys.executable)"], timeout=20)
    line = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
    if r.returncode == 0 and line:
        out.append(line)
    for name in ("python", "python3"):
        exe = shutil.which(name)
        if exe:
            out.append(exe)
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        key = str(Path(p).resolve()).lower()
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def resolve_tool_python(
    cfg: Config,
    *,
    probe_required: Callable[[str], set[str]] | None = None,
    candidates: Callable[[], list[str]] | None = None,
) -> tuple[str, str]:
    """返回（应使用的解释器, 说明）。

    哨兵探测（ruff）确认默认解释器缺核心工具、且该解释器未被用户显式指定
    （env/CLI/cq.toml 指过的都不动）时，依次尝试系统解释器候选，
    找到 5 个核心工具全齐的就换过去；说明非空 = 发生了自动切换（含固化建议）。
    健康环境只多付一次哨兵探测（约 0.2s）。
    """
    import sys as _sys

    _probe = probe_required or _probe_required
    primary = cfg.python
    if _SENTINEL in _probe(primary):
        return primary, ""
    explicit = Path(primary).resolve() != Path(_sys.executable).resolve()
    if explicit:
        return primary, ""  # 用户显式指定的解释器：不擅自换，缺失由 doctor/scan 如实报告
    missing = set(REQUIRED_TOOLS) - _probe(primary)
    for cand in (candidates or _candidate_pythons)():
        if str(Path(cand).resolve()).lower() == str(Path(primary).resolve()).lower():
            continue
        if set(REQUIRED_TOOLS) <= _probe(cand):
            note = (
                f"cq 自带解释器缺 {'/'.join(sorted(missing))}，已自动改用系统解释器；"
                f"固化方式（任选其一）：设环境变量 CQ_PYTHON={cand}、"
                "仓库根 cq.toml 的 [tool] python、或每次传 --python"
            )
            return cand, note
    return primary, ""


def run_doctor(
    cfg: Config, probe_fn: Callable[[], list[ToolCheck]] | None = None
) -> tuple[list[ToolCheck], str]:
    """probe_fn 供测试注入；默认真实探测。返回（检查结果, 解释器切换说明）。"""
    if probe_fn is not None:
        return probe_fn(), ""
    note: str = ""
    if not _has_sentinel(cfg.python):
        chosen, note = resolve_tool_python(cfg)
        if note:
            cfg.python = chosen
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
    return checks, note


def doctor_summary(
    checks: list[ToolCheck],
    *,
    python_source: str = "default",
) -> dict[str, object]:
    missing_required = [c.name for c in checks if not c.available and not c.optional]
    missing_optional = [c.name for c in checks if not c.available and c.optional]
    summary = {
        "tools": [c.to_dict() for c in checks],
        "python_source": python_source,
        "total": len(checks),
        "available": sum(1 for c in checks if c.available),
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "all_available": not missing_required and not missing_optional,
    }
    # 疑似执行拦截：≥2 个核心工具的 detail 是 spawn 失败 → 全局提示，避免误判要重装工具
    spawn_blocked = [
        c
        for c in checks
        if not c.available
        and "[spawn-failed]" in (c.detail or "")
        and any(k in (c.detail or "") for k in ("Permission", "拒绝访问", "Access is denied"))
    ]
    if len(spawn_blocked) >= 2:
        summary["spawn_blocked_hint"] = (
            f"{len(spawn_blocked)} 个工具的探测进程无法启动（权限被拒/沙箱拦截）："
            "这不是工具缺失。用 --python 显式指向可运行的解释器（如工具所在 venv 的 python.exe）"
            "再验证；或在安全软件中放行该解释器。"
        )
    return summary


_SOURCE_LABEL = {
    "default": "默认（cq 自身运行解释器）",
    "config": "cq.toml 配置",
    "env": "环境变量 CQ_PYTHON",
    "cli": "命令行 --python",
}


def render_console(py: str, summary: dict[str, object]) -> str:
    lines: list[str] = ["cq doctor · 环境体检", ""]
    src = str(summary.get("python_source", "default"))
    lines.append(
        f"工具解释器：{py}（来源：{_SOURCE_LABEL.get(src, src)}；CQ_PYTHON / --python 可覆盖）"
    )
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
