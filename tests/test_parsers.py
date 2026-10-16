import json

from ai_code_quality.adapters.deepsec_adapter import SECRET_EVIDENCE_PLACEHOLDER, _safe_evidence
from ai_code_quality.adapters.mypy_adapter import parse_mypy_output
from ai_code_quality.adapters.pytest_cov import parse_coverage_json, parse_pytest_failures
from ai_code_quality.adapters.radon_adapter import _parse_cc_json, _parse_mi_json
from ai_code_quality.adapters.ruff_check import classify_ruff, parse_ruff_json
from ai_code_quality.adapters.ruff_format import parse_format_json
from ai_code_quality.adapters.vulture_adapter import parse_vulture_output


def test_deepsec_secret_evidence_hidden():
    # deepsec 的密钥 evidence 是截断碎片（sk-p...cdef），必须整体隐藏而不是靠正则
    assert (
        _safe_evidence("hardcoded_secret_openai_key", "hardcoded_secret", "sk-p...cdef")
        == SECRET_EVIDENCE_PLACEHOLDER
    )
    assert (
        _safe_evidence("ai_pattern_hardcoded_jwt_secret", "ai_pattern", "JWT_SECRET = eyJ...")
        == SECRET_EVIDENCE_PLACEHOLDER
    )
    # 非密钥类证据保留（eval 等危险写法需要证据定位）
    assert _safe_evidence("insecure_config_eval", "insecure_config", "eval(") == "eval("


def test_parse_ruff_json():
    text = '[{"code":"F401","message":"`os` imported but unused","filename":"app/x.py","location":{"row":1,"column":1},"fix":null}]'
    items = parse_ruff_json(text)
    assert items and items[0]["code"] == "F401"
    assert parse_ruff_json("not json") == []


def test_classify_ruff():
    assert classify_ruff("F821")[1] == "high"
    assert classify_ruff("E999")[0] == "reliability"
    assert classify_ruff("S101")[0] == "security"
    assert classify_ruff("F401")[1] == "medium"
    assert classify_ruff("E401")[1] == "low"


def test_parse_format_json():
    assert parse_format_json('[{"path":"a.py","diff":"..."}]') == ["a.py"]
    assert parse_format_json("Would reformat: b.py\n") == ["b.py"]


def test_parse_mypy_output():
    text = "app/x.py:12: error: Incompatible return value  [return-value]\napp/y.py:3: note: blah"
    out = parse_mypy_output(text)
    assert len(out) == 2
    assert out[0][2] == "error" and out[0][4] == "return-value"


def test_parse_radon_cc():
    text = '{"app/x.py": [{"name":"f","complexity":35,"rank":"E","lineno":5,"type":"function"}]}'
    data = _parse_cc_json(text)
    assert data["app/x.py"][0]["rank"] == "E"


def test_parse_radon_mi_both_shapes():
    a = _parse_mi_json('{"a.py": {"mi": 63.2, "rank": "C"}}')
    b = _parse_mi_json('{"b.py": [71.0, "C"]}')
    assert a["a.py"]["rank"] == "C" and b["b.py"]["rank"] == "C"


def test_parse_vulture():
    text = "app/x.py:10: unused function 'legacy' (90% confidence)"
    out = parse_vulture_output(text)
    assert out == [("app/x.py", 10, "unused function 'legacy'", 90)]


def test_parse_pytest_failures():
    text = "FAILED tests/test_a.py::test_one - AssertionError: bad\nERROR tests/test_b.py\n1 failed, 1 passed in 0.1s"
    out = parse_pytest_failures(text)
    assert out[0][1] == "tests/test_a.py::test_one"
    assert out[1][0] == "ERROR"


def test_parse_coverage_json():
    cov = parse_coverage_json(
        '{"totals": {"percent_covered": 45.5, "covered_lines": 10, "num_statements": 22}}'
    )
    assert cov and cov["percent"] == 45.5
    assert parse_coverage_json("{}") is None
    assert parse_coverage_json("nope") is None


def test_radon_double_reported_methods_deduped(tmp_path):
    """Issue（外部报告 §7.1）：radon 对 method 同时输出 class 嵌套与顶层两份，
    展开后同一 method 出现两次 → _cc_findings 必须按 id 去重（否则 diff 计数错乱）。"""
    from ai_code_quality.adapters.base import ScanContext
    from ai_code_quality.adapters.radon_adapter import RadonAdapter
    from ai_code_quality.config import Config

    ctx = ScanContext(
        project_root=tmp_path,
        run_dir=tmp_path / "a" / "r1",
        raw_dir=tmp_path / "a" / "r1" / "raw",
        run_id="r1",
        cfg=Config(),
        targets=[],
        source_dirs=[],
        has_tests=False,
        test_paths=[],
        importlinter_config=None,
        mypy_config=None,
    )
    stdout = json.dumps(
        {
            "x.py": [
                {
                    "type": "class",
                    "name": "MypyAdapter",
                    "rank": "D",
                    "lineno": 26,
                    "complexity": 23,
                    "methods": [
                        {
                            "type": "method",
                            "name": "run",
                            "rank": "D",
                            "lineno": 31,
                            "complexity": 22,
                        }
                    ],
                },
                {"type": "method", "name": "run", "rank": "D", "lineno": 31, "complexity": 22},
            ]
        }
    )
    findings = RadonAdapter._cc_findings(RadonAdapter(), ctx, stdout, "raw/radon-cc.json")
    runs_at_31 = [f for f in findings if f.rule == "cc-D" and f.line == 31]
    assert len(runs_at_31) == 1, findings  # 嵌套与顶层两份 run → 去重后 1 条
