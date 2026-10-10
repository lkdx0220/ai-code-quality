from pathlib import Path

import pytest

from ai_code_quality.fix import FixBatch, FixError, apply_batch, build_fix_plan, render_console
from ai_code_quality.runs import RunData
from ai_code_quality.schema import make_id


def _f(
    source="ruff",
    rule="F401",
    file="a.py",
    line=1,
    message="unused",
    severity="medium",
    category="maintainability",
    status="new",
    auto_fixable=False,
):
    return {
        "id": make_id(source, rule, file, line, message),
        "source": source,
        "rule": rule,
        "file": file,
        "line": line,
        "message": message,
        "severity": severity,
        "category": category,
        "status": status,
        "auto_fixable": auto_fixable,
    }


def _run(findings):
    return RunData(run_id="r1", run_dir=Path("audits/r1"), summary={}, findings=findings)


def test_fix_plan_auto_batch_first_with_commands():
    plan = build_fix_plan(
        _run([_f(auto_fixable=True), _f(source="ruff-format", rule="format", file="b.py")]),
        cfg_python="python",
    )
    assert plan[0].applicable and plan[0].batch == 1
    assert plan[0].files == ["a.py", "b.py"]
    assert plan[0].commands == [
        ["python", "-m", "ruff", "check", "--fix", "a.py", "b.py"],
        ["python", "-m", "ruff", "format", "a.py", "b.py"],
    ]


def test_fix_plan_manual_batches_not_applicable():
    plan = build_fix_plan(
        _run(
            [
                _f(
                    source="deepsec",
                    rule="hardcoded_secret",
                    severity="blocker",
                    category="security",
                ),
                _f(source="radon", rule="cc-E", severity="high"),
                _f(source="pytest", rule="test-failed", severity="high", category="test"),
                _f(source="mypy", rule="return-value"),
            ]
        ),
        cfg_python="python",
    )
    assert not any(b.applicable for b in plan)
    titles = [b.title for b in plan]
    assert any("安全" in t for t in titles)
    assert any("复杂度" in t for t in titles)
    assert any("测试" in t for t in titles)
    assert any("人工排查" in t for t in titles)


def test_fix_plan_empty_run():
    assert build_fix_plan(_run([]), cfg_python="python") == []


def test_fix_plan_suppressed_excluded():
    plan = build_fix_plan(_run([_f(auto_fixable=True, status="suppressed")]), cfg_python="python")
    assert plan == []


def test_apply_requires_existing_batch():
    plan = [FixBatch(batch=1, title="x", applicable=False, guidance="manual")]
    with pytest.raises(FixError, match="不存在"):
        apply_batch(Path("."), plan, 99)


def test_apply_refuses_manual_batch():
    plan = [FixBatch(batch=1, title="安全类", applicable=False, guidance="逐条核对")]
    with pytest.raises(FixError, match="不可自动执行"):
        apply_batch(Path("."), plan, 1)


def test_apply_runs_commands(tmp_path, monkeypatch):
    calls = []

    def fake_run_cmd(cmd, *, timeout, cwd=None, extra_env=None):
        calls.append((cmd, cwd))
        from ai_code_quality.runner import ProcResult

        return ProcResult(cmd=cmd, returncode=0, stdout="1 file reformatted", stderr="")

    # apply_batch 内部是延迟导入 ..runner.run_cmd，补丁打在源模块上
    import ai_code_quality.runner as runner_mod

    monkeypatch.setattr(runner_mod, "run_cmd", fake_run_cmd)
    plan = [
        FixBatch(
            batch=1,
            title="自动修复",
            applicable=True,
            commands=[["python", "-m", "ruff", "format", "a.py"]],
        )
    ]
    cmds, output = apply_batch(tmp_path, plan, 1)
    assert cmds == ["python -m ruff format a.py"]
    assert calls[0][1] == tmp_path
    assert "1 file reformatted" in output


def test_render_console_mentions_apply_gate(tmp_path):
    plan = [
        FixBatch(
            batch=1,
            title="自动修复",
            applicable=True,
            commands=[["python", "-m", "ruff", "check", "--fix", "a.py"]],
            guidance="g",
        )
    ]
    text = render_console("r1", plan, tmp_path / "fix-plan.json")
    assert "dry-run" in text and "未改任何代码" in text
    assert "--apply" in text and "cq gate" in text
