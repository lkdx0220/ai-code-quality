from pathlib import Path

from ai_code_quality.adapters.base import ScanContext, finding


def _ctx(tmp_path: Path) -> ScanContext:
    return ScanContext(
        project_root=tmp_path,
        run_dir=tmp_path / "audits" / "r1",
        raw_dir=tmp_path / "audits" / "r1" / "raw",
        run_id="r1",
        cfg=None,  # type: ignore[arg-type]  # finding() 不读 cfg
        targets=[],
        source_dirs=[],
        has_tests=False,
        test_paths=[],
        importlinter_config=None,
        mypy_config=None,
    )


def test_inside_root_path_kept(tmp_path):
    (tmp_path / "app").mkdir()
    f = finding(_ctx(tmp_path), source="ruff", category="maintainability",
                severity="high", file=tmp_path / "app" / "a.py", line=3, message="x")
    assert f.file == "app/a.py" and f.severity == "high"


def test_outside_root_abs_path_downgraded(tmp_path):
    # 绝对路径指向项目外（如 %TEMP% 的嵌套 pytest 一次性项目）→ 降级 info、file 清空、留痕
    f = finding(
        _ctx(tmp_path),
        source="pytest",
        category="test",
        severity="high",
        file=tmp_path.parent / "elsewhere" / "t.py",
        line=0,
        message="test-error: t.py",
    )
    assert f.severity == "info" and f.file == ""
    assert "[项目外路径已降级]" in f.message


def test_outside_root_relative_escape_downgraded(tmp_path):
    f = finding(
        _ctx(tmp_path),
        source="pytest",
        category="test",
        severity="high",
        file="../outside/tests/test_x.py",
        line=0,
        message="test-error: test_x.py",
    )
    assert f.severity == "info" and f.file == ""


def test_inside_root_relative_path_kept(tmp_path):
    f = finding(_ctx(tmp_path), source="ruff", category="maintainability",
                severity="medium", file="app/b.py", line=1, message="y")
    assert f.file == "app/b.py" and f.severity == "medium"


def test_empty_file_global_finding_untouched(tmp_path):
    f = finding(_ctx(tmp_path), source="pytest", category="test",
                severity="medium", file="", line=0, message="总覆盖率低于阈值")
    assert f.severity == "medium" and f.file == ""
