import pytest

from ai_code_quality.schema import AdapterResult, Finding, make_id, sort_findings


def test_finding_id_stable_and_prefix():
    f = Finding(
        source="ruff",
        category="maintainability",
        severity="medium",
        file="src/a.py",
        line=10,
        message="unused import",
        rule="F401",
    )
    assert f.id == make_id("ruff", "F401", "src/a.py", 10, "unused import")
    assert len(f.id) == 40  # sha1 hex


def test_finding_rejects_bad_severity():
    with pytest.raises(ValueError):
        Finding(
            source="x",
            category="maintainability",
            severity="fatal",
            file="a.py",
            line=1,
            message="m",
        )


def test_finding_rejects_bad_category():
    with pytest.raises(ValueError):
        Finding(source="x", category="style", severity="low", file="a.py", line=1, message="m")


def test_adapter_result_requires_reason_when_not_ok():
    with pytest.raises(ValueError):
        AdapterResult(name="x", tool="x", status="skipped", reason="")
    with pytest.raises(ValueError):
        AdapterResult(name="x", tool="x", status="error", reason="")


def test_adapter_result_ok_without_reason():
    r = AdapterResult(name="x", tool="x", status="ok")
    assert r.to_dict()["finding_count"] == 0


def test_sort_findings_severity_first():
    low = Finding(
        source="a", category="maintainability", severity="low", file="z.py", line=1, message="m"
    )
    blocker = Finding(
        source="a", category="security", severity="blocker", file="z.py", line=1, message="m"
    )
    high = Finding(
        source="a", category="security", severity="high", file="a.py", line=1, message="m"
    )
    assert [f.severity for f in sort_findings([low, high, blocker])] == ["blocker", "high", "low"]
