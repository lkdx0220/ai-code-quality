import pytest

from ai_code_quality.cli import build_parser, main


def test_parser_has_scan_and_doctor():
    parser = build_parser()
    args = parser.parse_args(["scan", "--project", ".", "--scope", "changed", "--format", "json"])
    assert args.command == "scan" and args.scope == "changed" and args.format == "json"
    args = parser.parse_args(["doctor", "--format", "json"])
    assert args.command == "doctor"


def test_parser_phase2_subcommands_exist():
    parser = build_parser()
    required = {
        "triage": [],
        "baseline": [],
        "diff": ["--baseline", "b"],
        "gate": [],
        "suppress": ["--finding", "f", "--action", "false_positive", "--reason", "r"],
    }
    for name, extra in required.items():
        args = parser.parse_args([name, "--run", "x", *extra])
        assert args.command == name
    # fix 仍是占位（Phase 3）
    with pytest.raises(SystemExit):
        parser.parse_args(["fix"])


def test_main_no_command_prints_help():
    assert main([]) == 1


def test_main_unknown_command_rejected_by_argparse():
    # argparse 层直接拒绝非法子命令（exit 2）；cli 里的"未知子命令"分支是防御性兜底
    with pytest.raises(SystemExit):
        main(["frobnicate"])


def test_main_gate_requires_run():
    # gate 缺 --run → argparse 直接报错退出
    with pytest.raises(SystemExit):
        main(["gate"])


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


def test_scan_json_output_contains_tools_and_notes(tmp_path):
    """N2 兑现断言：scan --format json 的 summary 必须携带 tools[] 与 notes（缺一即假绿）。"""
    import json as _json

    from ai_code_quality.config import Config
    from ai_code_quality.scan import run_scan

    out = run_scan(tmp_path, cfg=Config.load(tmp_path))
    assert isinstance(out.summary["tools"], list) and len(out.summary["tools"]) > 0
    assert "notes" in out.summary
    # 序列化往返：Agent/插件消费的就是这份 JSON
    payload = _json.dumps(out.summary, ensure_ascii=False)
    assert '"tools"' in payload and '"notes"' in payload
