"""解释器兜底：按实际缺失集合切换（显式指定永不自动切换）。"""

import sys

from ai_code_quality.config import Config
from ai_code_quality.doctor import resolve_tool_python

ALL = {"ruff", "mypy", "radon", "vulture", "pytest"}


def _cfg(py: str) -> Config:
    c = Config()
    c.python = py
    return c


def test_no_missing_no_fallback():
    calls = []

    def probe(py):
        calls.append(py)
        return ALL

    py, note = resolve_tool_python(
        _cfg("PY_DEFAULT"), missing=set(), probe_required=probe, candidates=lambda: ["X"]
    )
    assert py == "PY_DEFAULT" and note == "" and calls == []


def test_fallback_switches_to_complete_candidate():
    def probe(py):
        return ALL if py == "PY_SYSTEM" else set()

    py, note = resolve_tool_python(
        _cfg(sys.executable),
        missing=ALL - {"pytest"},
        probe_required=probe,
        candidates=lambda: ["PY_BAD", "PY_SYSTEM"],
    )
    assert py == "PY_SYSTEM"
    assert "CQ_PYTHON=PY_SYSTEM" in note and "已自动改用系统解释器" in note


def test_explicit_python_never_switched():
    calls = []

    def probe(py):
        return set()

    cfg = _cfg("PY_EXPLICIT")
    py, note = resolve_tool_python(
        cfg, missing=ALL, probe_required=probe, candidates=lambda: (calls.append(1), ["X"])[1]
    )
    assert py == "PY_EXPLICIT" and note == "" and calls == []


def test_no_complete_candidate_keeps_primary():
    def probe(py):
        return {"ruff", "mypy"} if py == "PY_PARTIAL" else set()

    py, note = resolve_tool_python(
        _cfg(sys.executable),
        missing={"mypy", "radon", "vulture"},
        probe_required=probe,
        candidates=lambda: ["PY_PARTIAL", "PY_EMPTY"],
    )
    assert py == sys.executable and note == ""


def test_candidate_same_as_primary_skipped():
    probed = []

    def probe(py):
        probed.append(py)
        return set()

    cfg = _cfg("PyMain")
    resolve_tool_python(cfg, missing=ALL, probe_required=probe, candidates=lambda: ["pymain"])
    assert probed.count("pymain") == 0  # 同一解释器（大小写差异）不再探测
