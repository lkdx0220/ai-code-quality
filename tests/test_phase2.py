import json

import pytest

from ai_code_quality import suppress as sup
from ai_code_quality.diff import diff_findings
from ai_code_quality.runs import RunNotFoundError, validate_baseline_name
from ai_code_quality.schema import make_id


def _f(
    source="ruff",
    rule="F401",
    file="a.py",
    line=1,
    message="unused",
    severity="medium",
    status="new",
):
    return {
        "id": make_id(source, rule, file, line, message),
        "source": source,
        "rule": rule,
        "file": file,
        "line": line,
        "message": message,
        "severity": severity,
        "status": status,
    }


# ---------- runs ----------


def test_validate_baseline_name():
    assert validate_baseline_name("baseline-1") == "baseline-1"
    with pytest.raises(RunNotFoundError):
        validate_baseline_name("../evil")
    with pytest.raises(RunNotFoundError):
        validate_baseline_name("")


# ---------- suppress ----------


def test_suppress_requires_reason(tmp_path):
    with pytest.raises(sup.SuppressionError):
        sup.add(
            tmp_path, finding_id="a" * 40, action="false_positive", reason="  ", source_run="r1"
        )


def test_suppress_rejects_bad_action(tmp_path):
    with pytest.raises(sup.SuppressionError):
        sup.add(tmp_path, finding_id="a" * 40, action="whatever", reason="理由", source_run="r1")


def test_suppress_add_and_dedup(tmp_path):
    _, p1 = sup.add(
        tmp_path, finding_id="a" * 40, action="false_positive", reason="误报", source_run="r1"
    )
    sup.add(
        tmp_path,
        finding_id="a" * 40,
        action="debt_accepted",
        reason="改口：暂不修",
        source_run="r2",
    )
    items = sup.load(tmp_path)
    assert len(items) == 1  # 同 id 覆盖而不是追加
    assert items[0]["action"] == "debt_accepted"
    assert json.loads(p1.read_text(encoding="utf-8"))["suppressions"]


def test_resolve_finding_id_prefix():
    findings = [{"id": "abcdef123456"}, {"id": "bbcdef123456"}]
    assert sup.resolve_finding_id(findings, "abcdef12") == "abcdef123456"
    with pytest.raises(sup.SuppressionError):
        sup.resolve_finding_id(findings, "cd")  # 太短
    with pytest.raises(sup.SuppressionError):
        sup.resolve_finding_id(findings, "zzzzzz")  # 命中 0 条
    with pytest.raises(sup.SuppressionError):
        sup.resolve_finding_id([{"id": "abc123"}, {"id": "abc456"}], "abc")  # 命中多条且前缀够长


# ---------- diff ----------


def test_diff_exact_and_new_fixed():
    base = [_f(line=1), _f(rule="E501", file="b.py")]
    curr = [_f(line=1), _f(rule="SIM102", file="c.py", severity="high")]
    r = diff_findings(base, curr)
    assert len(r.existing) == 1
    assert len(r.fixed) == 1 and r.fixed[0]["rule"] == "E501"
    assert len(r.new) == 1 and r.new[0]["rule"] == "SIM102"
    assert len(r.new_blocker_high) == 1


def test_diff_moved_line_not_flagged_new():
    base = [_f(line=10)]
    curr = [_f(line=25)]  # 同 source/rule/file/message，仅行号变
    r = diff_findings(base, curr)
    assert not r.new and not r.fixed
    assert len(r.existing) == 1 and len(r.moved) == 1
    assert r.moved[0]["from_line"] == 10 and r.moved[0]["to_line"] == 25


def test_diff_suppressed_excluded():
    base = [_f(), _f(rule="E501", file="b.py", status="suppressed")]
    curr = [_f(), _f(rule="E501", file="b.py", status="suppressed")]
    r = diff_findings(base, curr)
    assert r.summary_counts() == {"new": 0, "fixed": 0, "existing": 1, "moved": 0, "suppressed": 1}


def test_diff_message_change_is_new_not_moved():
    base = [_f(message="old msg")]
    curr = [_f(message="new msg")]
    r = diff_findings(base, curr)
    assert len(r.new) == 1 and len(r.fixed) == 1 and not r.existing
