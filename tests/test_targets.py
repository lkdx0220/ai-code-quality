from pathlib import Path

from ai_code_quality.targets import (
    _looks_like_test,
    _top_entries_with_py,
    resolve_targets,
)


def _mk(root: Path, rel: str, content: str = "x = 1\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_excluded_dirs_not_scanned(tmp_path):
    _mk(tmp_path, "app/main.py")
    _mk(tmp_path, ".venv/lib.py")
    _mk(tmp_path, "audits/junk.py")
    _mk(tmp_path, "node_modules/j.py")
    entries = _top_entries_with_py(tmp_path)
    assert [e.name for e in entries] == ["app"]


def test_top_level_loose_files(tmp_path):
    _mk(tmp_path, "tool.py")
    _mk(tmp_path, "pkg/a.py")
    entries = _top_entries_with_py(tmp_path)
    assert {e.name for e in entries} == {"tool.py", "pkg"}


def test_src_layout(tmp_path):
    _mk(tmp_path, "src/mypkg/a.py")
    _mk(tmp_path, "tests/test_a.py")
    st = resolve_targets(tmp_path)
    assert [t.name for t in st.targets] == ["mypkg"]
    assert st.has_tests


def test_flat_layout_detects_tests(tmp_path):
    _mk(tmp_path, "app/__init__.py")
    _mk(tmp_path, "app/auth.py")
    _mk(tmp_path, "tests/test_auth.py")
    st = resolve_targets(tmp_path)
    assert {t.name for t in st.targets} >= {"app", "tests"}
    assert st.has_tests
    assert (tmp_path / "tests") in st.test_paths
    assert (tmp_path / "app") in st.source_dirs


def test_importlinter_config_detection(tmp_path):
    _mk(tmp_path, "app/a.py")
    assert resolve_targets(tmp_path).importlinter_config is None
    (tmp_path / ".importlinter").write_text("{}")
    assert resolve_targets(tmp_path).importlinter_config == tmp_path / ".importlinter"


def test_looks_like_test():
    assert _looks_like_test(Path("tests/test_a.py"))
    assert _looks_like_test(Path("src/test_helper.py"))
    assert not _looks_like_test(Path("app/main.py"))
