import json

from ai_code_quality.config import Config
from ai_code_quality.doctor import (
    DEEPSEC_REPO,
    TESTED_VERSIONS,
    ToolCheck,
    doctor_summary,
    render_console,
    run_doctor,
)


def _fake_checks() -> list[ToolCheck]:
    return [
        ToolCheck(name="ruff-check", tool="ruff", available=True, version="0.16.9"),
        ToolCheck(
            name="mypy",
            tool="mypy",
            available=False,
            install="pip install mypy",
            note="静态类型检查（核心）",
            optional=False,
            detail="No module named mypy",
        ),
        ToolCheck(
            name="deepsec",
            tool="deepsec",
            available=True,
            version="0.2.0（源码）",
            form="源码仓库",
            optional=True,
        ),
    ]


def test_doctor_summary_structure():
    s = doctor_summary(_fake_checks())
    assert s["total"] == 3 and s["available"] == 2
    assert s["missing_required"] == ["mypy"]
    assert s["missing_optional"] == []
    assert s["all_available"] is False
    assert s["tools"][0]["tested_version"] == TESTED_VERSIONS["ruff"]


def test_render_console_shows_install_hint():
    text = render_console("some/python", doctor_summary(_fake_checks()))
    assert "pip install mypy" in text  # 缺失核心工具给安装命令
    assert "No module named mypy" in text  # 缺失原因可见
    assert "some/python" in text  # 显示探测用的解释器
    assert "源码仓库" in text


def test_doctor_covers_all_adapters_and_hints():
    # 真实探测一次：doctor 检查的是"工具"（ruff 一个工具供两个 adapter 用，pytest adapter 用两个工具），
    # 不变量 = 检查的工具集与 TESTED_VERSIONS 完全一致（加工具别忘 doctor 和版本记录）
    checks, _note = run_doctor(Config())
    tools = {c.tool for c in checks}
    assert tools == set(TESTED_VERSIONS), (
        f"doctor 与实测版本表不一致: {tools ^ set(TESTED_VERSIONS)}"
    )
    for c in checks:
        assert c.install, f"{c.name} 缺安装提示"
        if c.name == "deepsec":
            assert DEEPSEC_REPO in c.install


def test_doctor_json_serializable():
    s = doctor_summary(_fake_checks())
    text = json.dumps(s, ensure_ascii=False)
    assert "pip install mypy" in text
