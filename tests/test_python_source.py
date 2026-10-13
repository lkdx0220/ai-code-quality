"""python_source 追踪 / spawn 失败信息透传 / 疑似拦截提示（外部诊断报告改进）。"""

from ai_code_quality.config import Config
from ai_code_quality.doctor import doctor_summary
from ai_code_quality.runner import run_cmd


def _with_env(monkeypatch, **kv):
    for k in ("CQ_PYTHON",):
        monkeypatch.delenv(k, raising=False)
    for k, v in kv.items():
        monkeypatch.setenv(k, v)


def test_python_source_default(tmp_path, monkeypatch):
    _with_env(monkeypatch)
    cfg = Config.load(tmp_path)
    assert cfg.python_source == "default"


def test_python_source_env_beats_default(tmp_path, monkeypatch):
    _with_env(monkeypatch, CQ_PYTHON="C:/Env/py.exe")
    cfg = Config.load(tmp_path)
    assert cfg.python == "C:/Env/py.exe" and cfg.python_source == "env"


def test_python_source_cli_beats_env(tmp_path, monkeypatch):
    _with_env(monkeypatch, CQ_PYTHON="C:/Env/py.exe")
    cfg = Config.load(tmp_path, cli_overrides={"python": "C:/Cli/py.exe"})
    assert cfg.python == "C:/Cli/py.exe" and cfg.python_source == "cli"


def test_python_source_config_beats_default(tmp_path, monkeypatch):
    _with_env(monkeypatch)
    (tmp_path / "cq.toml").write_text('python = "C:/Toml/py.exe"\n', encoding="utf-8")
    cfg = Config.load(tmp_path)
    assert cfg.python == "C:/Toml/py.exe" and cfg.python_source == "config"


def test_env_beats_config(tmp_path, monkeypatch):
    # 优先级：cli > env > config > default
    (tmp_path / "cq.toml").write_text('python = "C:/Toml/py.exe"\n', encoding="utf-8")
    _with_env(monkeypatch, CQ_PYTHON="C:/Env/py.exe")
    cfg = Config.load(tmp_path)
    assert cfg.python == "C:/Env/py.exe" and cfg.python_source == "env"


def test_snapshot_contains_source(tmp_path, monkeypatch):
    _with_env(monkeypatch, CQ_PYTHON="C:/Env/py.exe")
    cfg = Config.load(tmp_path)
    assert cfg.snapshot()["python_source"] == "env"


def test_spawn_failure_surfaces_real_error(tmp_path):
    # 指向一个目录当作可执行文件 → spawn 被拒 → stderr 必须携带真实系统错误
    r = run_cmd([str(tmp_path), "--version"], timeout=10)
    assert r.not_found is True
    assert "[spawn-failed]" in r.stderr
    assert "Errno" in r.stderr or "拒绝" in r.stderr or "denied" in r.stderr.lower()


def test_doctor_summary_spawn_blocked_hint():
    from ai_code_quality.doctor import ToolCheck

    checks = [
        ToolCheck(
            name=n,
            tool=n,
            available=False,
            optional=False,
            install="pip install x",
            note="",
            detail="[spawn-failed] PermissionError: [WinError 5] 拒绝访问。",
        )
        for n in ("ruff", "mypy", "radon")
    ]
    s = doctor_summary(checks)
    assert "spawn_blocked_hint" in s and "不要" not in s["spawn_blocked_hint"][:2]
    assert "这不是工具缺失" in s["spawn_blocked_hint"]


def test_doctor_summary_no_hint_when_spawn_ok():
    from ai_code_quality.doctor import ToolCheck

    checks = [
        ToolCheck(
            name=n,
            tool=n,
            available=False,
            optional=False,
            install="pip install x",
            note="",
            detail="No module named x",
        )
        for n in ("ruff", "mypy", "radon")
    ]
    s = doctor_summary(checks)
    assert "spawn_blocked_hint" not in s
