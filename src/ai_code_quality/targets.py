"""扫描目标解析：算出该把哪些路径喂给各工具，并探测 git / 测试 / 配置。

目标解析是所有 adapter 共用的唯一入口，保证"同一仓库、同一目标集、可复现"。
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# 这些目录一定不进扫描目标（venv、缓存、构建产物、audits 自身……）
EXCLUDE_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "env",
    ".env",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".hypothesis",
    "node_modules",
    "audits",
    "build",
    "dist",
    "out",
    "target",
    "bin",
    "obj",
    "htmlcov",
    ".idea",
    ".vscode",
    ".eggs",
    "site-packages",
    ".cq_cache",
    "__pycache__",
    ".zcode",
    ".dsh",
}
EXCLUDE_DIR_PATTERNS = ("*.egg-info",)

_TEST_DIR_NAMES = ("tests", "test")
_TEST_FILE_PATTERNS = ("test_*.py", "*_test.py")
_PYTEST_CONFIG_FILES = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")
_IMPORTLINTER_FILES = (".importlinter", "pyproject.toml", "setup.cfg")
_MYPY_CONFIG_FILES = ("mypy.ini", ".mypy.ini", "pyproject.toml", "setup.cfg")


@dataclass
class GitInfo:
    is_repo: bool = False
    commit: str = ""
    branch: str = ""
    dirty: int | None = None  # 未提交文件数；非仓库为 None
    changed_files: list[str] = field(default_factory=list)


@dataclass
class ScanTargets:
    project_root: Path
    # 喂给 lint/type/复杂度 工具的路径（绝对，可能不存在于 scope=changed 的极端情况）
    targets: list[Path] = field(default_factory=list)
    has_tests: bool = False
    test_paths: list[Path] = field(default_factory=list)  # pytest 的入口（目录或文件）
    source_dirs: list[Path] = field(default_factory=list)  # 覆盖率统计的源码目录（不含 tests）
    importlinter_config: Path | None = None
    mypy_config: Path | None = None
    scope: str = "full"
    notes: list[str] = field(default_factory=list)


def _is_excluded_dir(d: Path) -> bool:
    if d.name in EXCLUDE_DIR_NAMES:
        return True
    return any(fnmatch.fnmatch(d.name, p) for p in EXCLUDE_DIR_PATTERNS)


def _iter_py_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        dirnames[:] = [n for n in dirnames if not _is_excluded_dir(d / n)]
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(d / fn)
    return sorted(out)


def _looks_like_test(path: Path) -> bool:
    name = path.name
    if any(fnmatch.fnmatch(name, p) for p in _TEST_FILE_PATTERNS):
        return True
    return path.parent.name in _TEST_DIR_NAMES and path.suffix == ".py"


def _has_pytest_config(root: Path) -> bool:
    for name in _PYTEST_CONFIG_FILES:
        p = root / name
        if not p.is_file():
            continue
        if name == "pyproject.toml":
            if _toml_has_section(p, ("tool", "pytest", "ini_options")):
                return True
        elif name == "setup.cfg" or name == "tox.ini":
            if _cfg_has_section(p, "pytest"):
                return True
        else:
            return True
    return False


def _find_importlinter_config(root: Path) -> Path | None:
    for name in _IMPORTLINTER_FILES:
        p = root / name
        if not p.is_file():
            continue
        if name == ".importlinter":
            return p
        if name == "pyproject.toml" and _toml_has_section(p, ("tool", "importlinter")):
            return p
        if name == "setup.cfg" and _cfg_has_section(p, "importlinter"):
            return p
    return None


def _find_mypy_config(root: Path) -> Path | None:
    for name in _MYPY_CONFIG_FILES:
        p = root / name
        if not p.is_file():
            continue
        if name in ("mypy.ini", ".mypy.ini"):
            return p
        if name == "pyproject.toml" and _toml_has_section(p, ("tool", "mypy")):
            return p
        if name == "setup.cfg" and _cfg_has_section(p, "mypy"):
            return p
    return None


def _toml_has_section(path: Path, keys: tuple[str, ...]) -> bool:
    try:
        import tomllib

        with open(path, "rb") as f:
            data: object = tomllib.load(f)
        for k in keys:
            if not isinstance(data, dict) or k not in data:
                return False
            data = data[k]
        return True
    except (ImportError, OSError, tomllib.TOMLDecodeError, TypeError, KeyError):
        # 坏 TOML / 非预期结构都视为"无该配置节"，保守回退默认行为
        return False


def _cfg_has_section(path: Path, section: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return re.search(rf"^\[{re.escape(section)}[^\]]*\]", text, re.MULTILINE) is not None


def get_git_info(root: Path) -> GitInfo:
    from .runner import run_cmd

    def git(*args: str) -> str | None:
        r = run_cmd(["git", "-C", str(root), *args], timeout=30)
        if r.returncode == 0:
            return r.stdout.strip()
        return None

    head = git("rev-parse", "--short", "HEAD")
    if head is None:
        return GitInfo(is_repo=False)
    branch = git("rev-parse", "--abbrev-ref", "HEAD") or ""
    status = git("status", "--porcelain") or ""
    changed = [ln[3:].strip('"') for ln in status.splitlines() if ln.strip()]
    return GitInfo(
        is_repo=True, commit=head, branch=branch, dirty=len(changed), changed_files=changed
    )


def resolve_targets(
    project_root: Path, *, scope: str = "full", git_info: GitInfo | None = None
) -> ScanTargets:
    root = project_root.resolve()
    st = ScanTargets(project_root=root, scope=scope)

    all_py = _iter_py_files(root)
    src_tests = [p for p in all_py if _looks_like_test(p)]
    st.has_tests = bool(src_tests) or _has_pytest_config(root)
    st.test_paths = _test_roots(root, src_tests) or (
        [root / "tests"] if (root / "tests").is_dir() else []
    )
    st.source_dirs = _package_dirs(root, all_py)
    st.importlinter_config = _find_importlinter_config(root)
    st.mypy_config = _find_mypy_config(root)

    if scope == "changed":
        info = git_info or get_git_info(root)
        changed_py = [root / f for f in info.changed_files if f.endswith(".py")]
        changed_py = [p for p in changed_py if p.exists()]
        if not info.is_repo:
            st.notes.append("scope=changed 但项目不是 git 仓库，回退 full")
        elif not changed_py:
            st.notes.append("scope=changed 但没有变更的 .py 文件，回退 full")
        if info.is_repo and changed_py:
            st.targets = changed_py
            return st
        # 回退 full

    # full：src 布局优先；否则所有含 .py 的顶层入口（目录或散文件）
    src_dir = root / "src"
    if src_dir.is_dir() and any(p.suffix == ".py" for p in _iter_py_files(src_dir)):
        st.targets = _top_entries_with_py(src_dir)
    else:
        st.targets = _top_entries_with_py(root)
    return st


def _test_roots(root: Path, src_tests: list[Path]) -> list[Path]:
    """把散落的测试文件收敛到最上层的测试根目录（tests/、test/、src/*/tests/…），
    避免给 pytest 传几十个文件参数。"""
    roots: set[Path] = set()
    for p in src_tests:
        rel = p.relative_to(root)
        parts = rel.parts
        if "tests" in parts:
            roots.add(root / Path(*parts[: parts.index("tests") + 1]))
        elif "test" in parts:
            roots.add(root / Path(*parts[: parts.index("test") + 1]))
        else:
            roots.add(p)  # 不在 tests 目录里的散测试文件，直接传文件
    return sorted(r for r in roots if r.exists())


def _package_dirs(root: Path, all_py: list[Path]) -> list[Path]:
    """源码包目录（排除整个测试树），用于覆盖率 --cov。"""
    out: set[Path] = set()
    for p in all_py:
        rel = p.relative_to(root)
        parts = rel.parts
        if "tests" in parts or "test" in parts[:-1]:
            continue
        if len(parts) >= 2:  # 至少在一级子目录里
            out.add(root / parts[0])
    return sorted(out)


def _top_entries_with_py(root: Path) -> list[Path]:
    """root 直接子项中含 .py 的目录 + root 顶层 .py 散文件。"""
    entries: list[Path] = []
    try:
        items = sorted(root.iterdir())
    except OSError:
        return []
    for item in items:
        if item.is_dir():
            if _is_excluded_dir(item):
                continue
            if any(p.suffix == ".py" for p in _iter_py_files(item)):
                entries.append(item)
        elif item.is_file() and item.suffix == ".py":
            entries.append(item)
    return entries
