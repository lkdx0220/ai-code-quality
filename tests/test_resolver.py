import sys

from ai_code_quality.config import Config
from ai_code_quality.doctor import REQUIRED_TOOLS, resolve_tool_python


def _cfg(py: str) -> Config:
    c = Config()
    c.python = py
    return c


def test_primary_has_sentinel_no_fallback():
    # 默认解释器哨兵（ruff）可用 → 原样返回，不触发候选探测
    calls = []

    def fake_probe(py):
        calls.append(py)
        return {"ruff"}

    py, note = resolve_tool_python(_cfg("PY_DEFAULT"), probe_required=fake_probe, candidates=list)
    assert py == "PY_DEFAULT" and note == ""
    assert calls == ["PY_DEFAULT"]


def test_fallback_switches_to_complete_candidate():
    # 默认解释器（=sys.executable，即未显式指定）缺全部工具 → 换到全齐的系统解释器
    def fake_probe(py):
        return set(REQUIRED_TOOLS) if py == "PY_SYSTEM" else set()

    py, note = resolve_tool_python(
        _cfg(sys.executable),
        probe_required=fake_probe,
        candidates=lambda: ["PY_BAD", "PY_SYSTEM"],
    )
    assert py == "PY_SYSTEM"
    assert "CQ_PYTHON=PY_SYSTEM" in note
    assert "已自动改用系统解释器" in note


def test_explicit_python_never_switched():
    # 用户显式指定的解释器（≠sys.executable）即使缺工具也不擅自换
    def fake_probe(py):
        return set()

    called = []

    def fake_candidates():
        called.append(1)
        return ["PY_SYSTEM"]

    py, note = resolve_tool_python(
        _cfg("PY_EXPLICIT"),
        probe_required=fake_probe,
        candidates=fake_candidates,
    )
    assert py == "PY_EXPLICIT" and note == ""
    assert called == []  # 显式路径连候选探测都不做


def test_no_complete_candidate_keeps_primary():
    def fake_probe(py):
        return {"ruff", "mypy"} if py == "PY_PARTIAL" else set()

    py, note = resolve_tool_python(
        _cfg(sys.executable),
        probe_required=fake_probe,
        candidates=lambda: ["PY_PARTIAL", "PY_EMPTY"],
    )
    assert py == sys.executable and note == ""


def test_candidate_same_as_primary_skipped(monkeypatch):
    # 同一解释器（仅大小写差异）不重复探测。用相对名而不是 Windows 风格绝对路径：
    # "C:/x" 与 "c:\\x" 只有在 Windows 上 resolve 后才相等，Linux 上是两个不同路径
    # （CI 在 ubuntu 上跑，曾因此误判为"未跳过"）。
    probed = []

    def fake_probe(py):
        probed.append(py)
        return set()

    cfg = Config()
    cfg.python = "PyMain"
    monkeypatch.setattr(sys, "executable", cfg.python)  # 视为默认解释器
    resolve_tool_python(cfg, probe_required=fake_probe, candidates=lambda: ["pymain"])
    assert probed.count("pymain") == 0  # 同一解释器不再探测
