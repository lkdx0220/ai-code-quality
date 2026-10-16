import runpy
from types import SimpleNamespace

from ai_code_quality import cli
from ai_code_quality.schema import AdapterResult


def test_cli_scan_json_dispatch(monkeypatch, tmp_path, capsys):
    fake = SimpleNamespace(
        summary={"tools": [], "notes": []},
        findings=[],
        results=[AdapterResult(name="fake", tool="fake", status="ok")],
        exit_code=0,
    )
    monkeypatch.setattr("ai_code_quality.scan.run_scan", lambda *args, **kwargs: fake)
    assert cli.main(["scan", "--project", str(tmp_path), "--format", "json"]) == 0
    assert '"tools": []' in capsys.readouterr().out


def test_cli_scan_bad_adapter_and_options(tmp_path, capsys):
    assert cli.main(["scan", "--project", str(tmp_path / "missing")]) == 1
    assert cli.main(["scan", "--project", str(tmp_path), "--adapters", "unknown"]) == 1
    assert "未知 adapter" in capsys.readouterr().err


def test_cli_triage_and_baseline_dispatch(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        "ai_code_quality.triage.run_triage", lambda *args, **kwargs: (0, "triage ok")
    )
    assert cli.main(["triage", "--project", str(tmp_path), "--run", "r"]) == 0
    assert "triage ok" in capsys.readouterr().out

    run = SimpleNamespace(run_id="r", findings=[{"id": "x"}])
    monkeypatch.setattr(
        "ai_code_quality.baseline.save_baseline", lambda *args: (tmp_path / "b", run)
    )
    assert cli.main(["baseline", "--project", str(tmp_path), "--run", "r", "--name", "b"]) == 0
    assert "基线已保存" in capsys.readouterr().out


def test_cli_spec_commands_on_unconfigured_project(tmp_path, capsys):
    assert cli.main(["spec-gate", "--project", str(tmp_path)]) == 0
    assert "未找到" in capsys.readouterr().out
    assert cli.main(["spec-rebuild", "--project", str(tmp_path)]) == 0
    assert "未找到" in capsys.readouterr().out
    assert cli.main(["promote", "--project", str(tmp_path)]) == 0
    assert "晋升候选" in capsys.readouterr().out or "无晋升候选" in capsys.readouterr().out


def test_cli_spec_gate_invalid_mode(tmp_path, capsys):
    (tmp_path / ".specdrift.toml").write_text("code_paths=['src']\n", encoding="utf-8")
    assert cli.main(["spec-gate", "--project", str(tmp_path), "--mode", "hard"]) == 0
    assert "spec-gate" in capsys.readouterr().out


def test_baseline_round_trip_and_run_fallback(tmp_path):
    import json

    from ai_code_quality.baseline import load_baseline, save_baseline

    run_dir = tmp_path / "audits" / "20260101-000000-full"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({"run_id": "20260101-000000-full", "git": {}, "totals": {}}),
        encoding="utf-8",
    )
    (run_dir / "findings.json").write_text(json.dumps({"findings": []}), encoding="utf-8")
    path, _ = save_baseline(tmp_path, "20260101-000000-full", "baseline")
    assert path.is_file()
    ref, data = load_baseline(tmp_path, "baseline")
    assert ref == "baseline" and data.findings == []
    ref2, data2 = load_baseline(tmp_path, "20260101-000000-full")
    assert ref2 == "20260101-000000-full" and data2.run_id == "20260101-000000-full"


def test_module_entrypoint(monkeypatch):
    monkeypatch.setattr("sys.argv", ["cq", "--version"])
    try:
        runpy.run_module("ai_code_quality.__main__", run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0
