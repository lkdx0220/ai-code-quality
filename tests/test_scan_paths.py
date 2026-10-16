import json

from ai_code_quality import scan
from ai_code_quality.adapters.base import Adapter
from ai_code_quality.config import Config
from ai_code_quality.schema import AdapterResult, Finding


class _FakeAdapter(Adapter):
    name = "ruff-check"
    tool = "ruff"
    description = "fake adapter"

    def __init__(self, findings):
        self._findings = findings

    def run(self, ctx):
        return AdapterResult(
            name=self.name,
            tool=self.tool,
            status="ok",
            version="test",
            findings=list(self._findings),
        )


def _finding(message="problem", line=1):
    return Finding(
        source="ruff",
        category="maintainability",
        severity="medium",
        file="src/app.py",
        line=line,
        message=message,
        rule="F401",
    )


def test_run_scan_writes_arrays_and_deduplicates(monkeypatch, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    item = _finding()
    monkeypatch.setattr(scan, "get_adapters", lambda enabled: [_FakeAdapter([item, item])])
    monkeypatch.setattr("ai_code_quality.doctor.required_missing", lambda python: [])

    out = scan.run_scan(
        tmp_path, cfg=Config(python_source="config"), enabled_adapters=["ruff-check"]
    )

    assert out.exit_code == 0
    assert len(out.findings) == 1
    summary = json.loads((out.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert isinstance(summary["tools"], list)
    assert isinstance(summary["notes"], list)
    assert summary["tools"][0]["finding_count"] == 2


def test_run_scan_applies_signature_suppression(monkeypatch, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    item = _finding(line=9)
    cq = tmp_path / ".cq"
    cq.mkdir()
    (cq / "suppressions.json").write_text(
        json.dumps(
            {
                "suppressions": [
                    {
                        "id": "legacy-id",
                        "source": "ruff",
                        "sig_source": "ruff",
                        "sig_rule": "F401",
                        "sig_file": "src/app.py",
                        "sig_message": "problem",
                        "action": "false_positive",
                        "reason": "fixture",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(scan, "get_adapters", lambda enabled: [_FakeAdapter([item])])
    monkeypatch.setattr("ai_code_quality.doctor.required_missing", lambda python: [])

    out = scan.run_scan(
        tmp_path, cfg=Config(python_source="config"), enabled_adapters=["ruff-check"]
    )

    assert out.findings[0].status == "suppressed"
    assert out.findings[0].suppression_reason == "fixture"
