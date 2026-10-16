from pathlib import Path
from types import SimpleNamespace

from ai_code_quality.adapters import get_adapters
from ai_code_quality.adapters.base import ScanContext
from ai_code_quality.adapters.deepsec_adapter import DeepsecAdapter
from ai_code_quality.adapters.import_linter import ImportLinterAdapter, parse_contracts
from ai_code_quality.adapters.mypy_adapter import MypyAdapter, parse_mypy_output
from ai_code_quality.adapters.pytest_cov import PytestCovAdapter
from ai_code_quality.adapters.radon_adapter import RadonAdapter
from ai_code_quality.adapters.ruff_check import RuffCheckAdapter
from ai_code_quality.adapters.ruff_format import RuffFormatAdapter
from ai_code_quality.adapters.vulture_adapter import VultureAdapter
from ai_code_quality.config import Config


def _ctx(tmp_path, *, targets=True, import_config=None):
    source = tmp_path / "src"
    source.mkdir(exist_ok=True)
    file = source / "app.py"
    file.write_text("value = 1\n", encoding="utf-8")
    run_dir = tmp_path / "audits" / "run"
    raw = run_dir / "raw"
    return ScanContext(
        project_root=tmp_path,
        run_dir=run_dir,
        raw_dir=raw,
        run_id="run",
        cfg=Config(python_source="config"),
        targets=[file] if targets else [],
        source_dirs=[source],
        has_tests=False,
        test_paths=[],
        importlinter_config=import_config,
        mypy_config=None,
    )


def _proc(stdout="", *, returncode=0, stderr="", timed_out=False, not_found=False):
    return SimpleNamespace(
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        timed_out=timed_out,
        not_found=not_found,
        duration_s=0.01,
    )


def test_mypy_adapter_normal_and_fatal(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        "ai_code_quality.runner.probe", lambda *args, **kwargs: _proc(stdout="mypy 1.2")
    )
    monkeypatch.setattr(
        "ai_code_quality.runner.run_cmd",
        lambda *args, **kwargs: _proc("src/app.py:3: error: bad  [return-value]\n"),
    )
    result = MypyAdapter().run(ctx)
    assert result.status == "ok" and result.findings[0].rule == "return-value"

    monkeypatch.setattr(
        "ai_code_quality.runner.run_cmd",
        lambda *args, **kwargs: _proc(
            "src/app.py:3: error: bad  [return-value]\n", returncode=2, stderr="INTERNAL ERROR"
        ),
    )
    fatal = MypyAdapter().run(ctx)
    assert fatal.status == "error" and fatal.findings


def test_mypy_adapter_skip_and_timeout(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path, targets=False)
    monkeypatch.setattr(
        "ai_code_quality.runner.probe", lambda *args, **kwargs: _proc(not_found=True, returncode=1)
    )
    assert MypyAdapter().run(ctx).status == "skipped"

    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        "ai_code_quality.runner.probe", lambda *args, **kwargs: _proc(stdout="mypy 1.2")
    )
    monkeypatch.setattr(
        "ai_code_quality.runner.run_cmd", lambda *args, **kwargs: _proc(timed_out=True)
    )
    assert MypyAdapter().run(ctx).status == "timeout"


def test_mypy_adapter_empty_targets_and_unstartable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "ai_code_quality.runner.probe", lambda *args, **kwargs: _proc(stdout="mypy 1.2")
    )
    empty = MypyAdapter().run(_ctx(tmp_path, targets=False))
    assert empty.status == "ok" and "没有可检查" in empty.reason

    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        "ai_code_quality.runner.run_cmd", lambda *args, **kwargs: _proc(not_found=True)
    )
    unstartable = MypyAdapter().run(ctx)
    assert unstartable.status == "skipped" and "无法启动" in unstartable.reason


def test_ruff_check_adapter_finding_and_timeout(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        "ai_code_quality.runner.probe", lambda *args, **kwargs: _proc(stdout="ruff 0.1")
    )
    monkeypatch.setattr(
        "ai_code_quality.runner.run_cmd",
        lambda *args, **kwargs: _proc(
            '[{"code":"F401","message":"unused","filename":"src/app.py","location":{"row":1}}]',
            returncode=1,
        ),
    )
    result = RuffCheckAdapter().run(ctx)
    assert result.status == "ok" and result.findings[0].auto_fixable is False

    monkeypatch.setattr(
        "ai_code_quality.runner.run_cmd", lambda *args, **kwargs: _proc(timed_out=True)
    )
    assert RuffCheckAdapter().run(ctx).status == "timeout"


def test_import_linter_skip_without_config_and_success(monkeypatch, tmp_path):
    no_config = _ctx(tmp_path)
    assert ImportLinterAdapter().run(no_config).status == "skipped"

    config = tmp_path / ".importlinter"
    config.write_text("[importlinter]\n", encoding="utf-8")
    ctx = _ctx(tmp_path, import_config=config)
    monkeypatch.setattr("shutil.which", lambda name: "lint-imports.exe")
    calls = iter([_proc(stdout="import-linter 2.0"), _proc(stdout="0 contracts broken\n")])
    monkeypatch.setattr("ai_code_quality.runner.run_cmd", lambda *args, **kwargs: next(calls))
    result = ImportLinterAdapter().run(ctx)
    assert result.status == "ok"


def test_import_linter_parser_and_broken_run(monkeypatch, tmp_path):
    kept, broken, blocks = parse_contracts(
        "* core kept\n* api broken\n1 contracts broken\napi (API boundary)\nwhy it broke\n"
    )
    assert kept == ["core"] and broken == ["api"] and blocks[0][0].startswith("api:")
    config = tmp_path / ".importlinter"
    config.write_text("[importlinter]\n", encoding="utf-8")
    ctx = _ctx(tmp_path, import_config=config)
    monkeypatch.setattr("shutil.which", lambda name: "lint-imports.exe")
    calls = iter([_proc(stdout="2.0"), _proc("api (API)\n* api broken\n", returncode=1)])
    monkeypatch.setattr("ai_code_quality.runner.run_cmd", lambda *args, **kwargs: next(calls))
    result = ImportLinterAdapter().run(ctx)
    assert result.status in {"error", "ok"}


def test_radon_adapter_reports_cc_and_mi(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        "ai_code_quality.runner.probe", lambda *args, **kwargs: _proc(stdout="radon 6.0")
    )
    calls = iter(
        [
            _proc(
                '{"src/app.py": [{"name":"f","complexity":22,"rank":"D","lineno":2,"type":"function"}]}'
            ),
            _proc('{"src/app.py": {"mi": 40, "rank": "C"}}'),
        ]
    )
    monkeypatch.setattr("ai_code_quality.runner.run_cmd", lambda *args, **kwargs: next(calls))
    result = RadonAdapter().run(ctx)
    assert result.status == "ok"
    assert {f.rule for f in result.findings} == {"cc-D", "mi-C"}


def test_pytest_adapter_failure_and_coverage(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    ctx.has_tests = True
    ctx.test_paths = [tmp_path / "tests"]
    ctx.source_dirs = [tmp_path / "src"]
    monkeypatch.setattr(
        "ai_code_quality.runner.probe",
        lambda cmd, **kwargs: (
            _proc(stdout="pytest 9.0") if "pytest_cov" not in " ".join(cmd) else _proc(stdout="")
        ),
    )

    def run(*args, **kwargs):
        cmd = args[0]
        for part in cmd:
            if str(part).startswith("--cov-report=json:"):
                cov_path = Path(str(part).split(":", 1)[1])
                cov_path.parent.mkdir(parents=True, exist_ok=True)
                cov_path.write_text(
                    '{"totals":{"percent_covered":45,"covered_lines":9,"num_statements":20}}',
                    encoding="utf-8",
                )
        return _proc("FAILED tests/test_app.py::test_bad - AssertionError: bad\n", returncode=1)

    monkeypatch.setattr("ai_code_quality.runner.run_cmd", run)
    result = PytestCovAdapter().run(ctx)
    assert result.status == "ok"
    assert {f.rule for f in result.findings} == {"test-failed", "coverage"}


def test_deepsec_adapter_scans_both_layers(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    ctx.cfg.deepsec_home = None
    monkeypatch.setattr(
        "ai_code_quality.runner.probe", lambda *args, **kwargs: _proc(stdout="deepsec help")
    )

    def run(*args, **kwargs):
        cmd = args[0]
        output = Path(cmd[cmd.index("--output") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            '{"findings":[{"severity":"high","detection_rule":"token_secret","target":"src/app.py","line":2,"title":"secret","evidence":"sk-p..."}]}',
            encoding="utf-8",
        )
        return _proc()

    monkeypatch.setattr("ai_code_quality.runner.run_cmd", run)
    result = DeepsecAdapter().run(ctx)
    assert result.status == "ok" and len(result.findings) == 2
    assert result.findings[0].evidence.startswith("[密钥类证据")


def test_deepsec_adapter_disabled_and_unavailable(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    ctx.cfg.no_deepsec = True
    assert DeepsecAdapter().run(ctx).status == "skipped"
    ctx.cfg.no_deepsec = False
    monkeypatch.setattr("ai_code_quality.runner.probe", lambda *args, **kwargs: _proc(returncode=1))
    assert DeepsecAdapter().run(ctx).status == "skipped"


def test_format_and_vulture_adapters(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        "ai_code_quality.runner.probe", lambda *args, **kwargs: _proc(stdout="ruff 0.1")
    )
    monkeypatch.setattr(
        "ai_code_quality.runner.run_cmd",
        lambda *args, **kwargs: _proc('[{"filename":"src/app.py"}]', returncode=1),
    )
    formatted = RuffFormatAdapter().run(ctx)
    assert formatted.status == "ok" and formatted.findings

    monkeypatch.setattr(
        "ai_code_quality.runner.probe", lambda *args, **kwargs: _proc(stdout="vulture 2.16")
    )
    monkeypatch.setattr(
        "ai_code_quality.runner.run_cmd",
        lambda *args, **kwargs: _proc(
            "src/app.py:2: unused function 'old' (90% confidence)\n", returncode=3
        ),
    )
    dead = VultureAdapter().run(ctx)
    assert dead.status == "ok" and dead.findings[0].confidence == "high"


def test_ruff_format_empty_targets(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "ai_code_quality.runner.probe", lambda *args, **kwargs: _proc(stdout="ruff 0.1")
    )
    result = RuffFormatAdapter().run(_ctx(tmp_path, targets=False))
    assert result.status == "ok" and "没有可检查" in result.reason


def test_ruff_format_timeout(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        "ai_code_quality.runner.probe", lambda *args, **kwargs: _proc(stdout="ruff 0.1")
    )
    monkeypatch.setattr(
        "ai_code_quality.runner.run_cmd", lambda *args, **kwargs: _proc(timed_out=True)
    )
    result = RuffFormatAdapter().run(ctx)
    assert result.status == "timeout"


def test_ruff_format_unstartable(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        "ai_code_quality.runner.probe", lambda *args, **kwargs: _proc(stdout="ruff 0.1")
    )
    monkeypatch.setattr(
        "ai_code_quality.runner.run_cmd", lambda *args, **kwargs: _proc(not_found=True)
    )
    result = RuffFormatAdapter().run(ctx)
    assert result.status == "skipped" and "无法启动" in result.reason


def test_adapter_registry_filters_and_rejects_unknown():
    assert len(get_adapters()) == 8
    assert [a.name for a in get_adapters(["mypy"])] == ["mypy"]
    try:
        get_adapters(["missing"])
    except ValueError as exc:
        assert "未知 adapter" in str(exc)
    else:
        raise AssertionError("unknown adapter must be rejected")


def test_mypy_parser_ignores_malformed_lines():
    assert parse_mypy_output("not a mypy diagnostic") == []
