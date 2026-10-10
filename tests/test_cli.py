
from ai_code_quality.cli import build_parser, main


def test_parser_has_scan_and_doctor():
    parser = build_parser()
    args = parser.parse_args(["scan", "--project", ".", "--scope", "changed", "--format", "json"])
    assert args.command == "scan" and args.scope == "changed" and args.format == "json"
    args = parser.parse_args(["doctor", "--format", "json"])
    assert args.command == "doctor"


def test_parser_placeholder_subcommands_exist():
    parser = build_parser()
    for name in ("triage", "fix", "gate", "baseline", "diff"):
        args = parser.parse_args([name])
        assert args.command == name


def test_main_no_command_prints_help():
    assert main([]) == 1


def test_main_unimplemented_command_exits_1(capsys):
    assert main(["gate"]) == 1
    assert "Phase 2" in capsys.readouterr().err


def test_main_scan_missing_project(tmp_path):
    assert main(["scan", "--project", str(tmp_path / "nope")]) == 1


def test_main_doctor_json(tmp_path, capsys, monkeypatch):
    # doctor 真实探测当前环境（工具可用性不断言），只验证输出是合法 JSON 且含关键字段
    monkeypatch.chdir(tmp_path)
    rc = main(["doctor", "--format", "json"])
    import json

    data = json.loads(capsys.readouterr().out)
    assert data["total"] == 8
    assert rc in (0, 2)
