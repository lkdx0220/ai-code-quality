import json
from pathlib import Path

import pytest

from ai_code_quality.gate import run_gate
from ai_code_quality.runs import RunData
from ai_code_quality.schema import make_id
from ai_code_quality.triage import disposition_of, triage_run


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


def _run(tmp_path: Path, findings: list[dict], tools: list[dict] | None = None) -> RunData:
    run_dir = tmp_path / "audits" / "r1"
    (run_dir / "raw").mkdir(parents=True)
    summary = {
        "run_id": "r1",
        "tools": tools or [{"name": "ruff-check", "status": "ok"}],
        "config": {"coverage_threshold": 80.0},
    }
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (run_dir / "findings.json").write_text(
        json.dumps({"run_id": "r1", "findings": findings}), encoding="utf-8"
    )
    return RunData(run_id="r1", run_dir=run_dir, summary=summary, findings=findings)


# ---------- gate ----------


def test_gate_pass_on_clean_run(tmp_path):
    _run(tmp_path, [])
    report = run_gate(tmp_path, "r1", target="A")
    assert report.passed and report.exit_code == 0
    assert all(c.passed for c in report.checks)


def test_gate_fails_on_high(tmp_path):
    _run(tmp_path, [_f(severity="high", category="reliability")])
    report = run_gate(tmp_path, "r1", target="A")
    assert report.exit_code == 1 and not report.passed
    highs = next(c for c in report.checks if c.name == "highs")
    assert not highs.passed
    # target B 允许 ≤2 high
    report_b = run_gate(tmp_path, "r1", target="B")
    assert report_b.exit_code == 0


def test_gate_tool_error_exit_2(tmp_path):
    _run(tmp_path, [], tools=[{"name": "mypy", "status": "error"}])
    report = run_gate(tmp_path, "r1")
    assert report.exit_code == 2


def test_gate_complexity_check(tmp_path):
    _run(tmp_path, [_f(source="radon", rule="cc-E", severity="high")])
    report = run_gate(tmp_path, "r1")
    assert report.exit_code == 1
    assert any(c.name == "complexity" and not c.passed for c in report.checks)


def test_gate_coverage_threshold(tmp_path):
    run = _run(tmp_path, [])
    cov = run.run_dir / "raw" / "coverage.json"
    cov.write_text(json.dumps({"totals": {"percent_covered": 57.0}}), encoding="utf-8")
    report = run_gate(tmp_path, "r1")
    cov_check = next(c for c in report.checks if c.name == "coverage")
    assert not cov_check.passed and "57.0%" in cov_check.detail


def test_gate_new_blocker_high_with_baseline(tmp_path):
    base = [_f(line=1)]
    _run(tmp_path, base)
    # 把当前 run 覆盖为带新增 high 的状态
    curr = base + [_f(rule="S101", severity="high", category="security", message="assert")]
    run_dir = tmp_path / "audits" / "r2"
    (run_dir / "raw").mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({"run_id": "r2", "tools": [], "config": {}}), encoding="utf-8"
    )
    (run_dir / "findings.json").write_text(
        json.dumps({"run_id": "r2", "findings": curr}), encoding="utf-8"
    )
    report = run_gate(tmp_path, "r2", baseline_ref="r1")
    new_check = next(c for c in report.checks if c.name == "new_blocker_high")
    assert not new_check.passed and report.exit_code == 1
    assert report.diff_counts["new"] == 1


def test_gate_suppressed_not_counted(tmp_path):
    findings = [
        _f(
            source="deepsec",
            rule="hardcoded_secret",
            file="a.py",
            line=7,
            message="secret",
            severity="blocker",
            category="security",
            status="suppressed",
        )
    ]
    findings[0]["suppression_reason"] = "误报"
    _run(tmp_path, findings)
    report = run_gate(tmp_path, "r1", target="A")
    assert report.passed and report.exit_code == 0


def test_gate_ratings_present(tmp_path):
    _run(tmp_path, [])
    report = run_gate(tmp_path, "r1")
    assert set(report.ratings) >= {"security", "reliability", "maintainability"}
    assert report.ratings["security"] == "A"


def test_gate_bad_target(tmp_path):
    with pytest.raises(ValueError):
        run_gate(tmp_path, "r1", target="E")


# ---------- triage ----------


def test_disposition_mapping():
    assert disposition_of({"status": "suppressed"}) == "suppressed"
    assert disposition_of({"auto_fixable": True}) == "auto_fixable"
    assert disposition_of({"source": "ruff-format"}) == "auto_fixable"
    assert disposition_of({"source": "pytest", "category": "test"}) == "needs_test"
    assert disposition_of({"source": "radon", "rule": "cc-D"}) == "needs_refactor"
    assert disposition_of({"source": "deepsec", "category": "security"}) == "needs_decision"
    assert disposition_of({"source": "mypy", "category": "maintainability"}) == "needs_review"


def test_triage_run_shapes(tmp_path):
    findings = [
        _f(auto_fixable=True),
        _f(source="deepsec", rule="hardcoded_secret", severity="blocker", category="security"),
        _f(source="radon", rule="cc-E", severity="high"),
        _f(source="pytest", rule="test-failed", severity="high", category="test", file="t.py"),
        _f(status="suppressed"),
    ]
    run = _run(tmp_path, findings)
    result = triage_run(run)
    assert result["total"] == 5 and result["active"] == 4
    assert result["by_disposition"]["suppressed"] == 1
    assert result["by_disposition"]["needs_decision"] == 1
    assert result["dimensions"]["security"]["count"] == 1
    assert result["dimensions"]["test"]["count"] == 1
    batches = result["batches"]
    assert batches, "有活跃 findings 时必须给出批次"
    assert batches[0]["batch"] == 1
