from ai_code_quality.diff import diff_findings
from ai_code_quality.schema import make_id


def _f(source="ruff", rule="F401", file="a.py", line=1, message="unused", severity="medium"):
    return {
        "id": make_id(source, rule, file, line, message),
        "source": source,
        "rule": rule,
        "file": file,
        "line": line,
        "message": message,
        "severity": severity,
        "status": "new",
    }


def test_duplicate_sig_one_moved_other_not_new():
    # 同一文件两条同签名 finding（不同行），格式化后行号都漂移 → 都该进 moved/existing，不该出 new
    base = [_f(line=10), _f(line=50)]
    curr = [_f(line=12), _f(line=52)]
    r = diff_findings(base, curr)
    assert r.new == []
    assert len(r.moved) == 2 and len(r.existing) == 2


def test_duplicate_sig_partial_match():
    # 基线 2 条、当前只剩 1 条（另一条修掉了）→ 1 moved/existing + 1 fixed
    base = [_f(line=10), _f(line=50)]
    curr = [_f(line=12)]
    r = diff_findings(base, curr)
    assert len(r.existing) == 1 and len(r.moved) == 1
    assert len(r.fixed) == 1 and r.new == []


def test_duplicate_sig_grew():
    # 基线 1 条、当前 2 条（新增一处同类问题）→ 1 moved + 1 new
    base = [_f(line=10)]
    curr = [_f(line=12), _f(line=60)]
    r = diff_findings(base, curr)
    assert len(r.moved) == 1 and len(r.new) == 1
    assert len(r.existing) == 1


def test_moved_never_in_new_blocker_high():
    base = [_f(line=10, severity="high")]
    curr = [_f(line=99, severity="high")]
    r = diff_findings(base, curr)
    assert r.new_blocker_high == []
