import json

from ai_code_quality import suppress as sup
from ai_code_quality.schema import make_id


def _finding(source="ruff", rule="F401", file="a.py", line=10, message="unused"):
    return {
        "id": make_id(source, rule, file, line, message),
        "source": source,
        "rule": rule,
        "file": file,
        "line": line,
        "message": message,
        "severity": "medium",
        "status": "new",
    }


def test_add_stores_signature(tmp_path):
    f = _finding()
    sup.add(
        tmp_path,
        finding_id=f["id"],
        action="false_positive",
        reason="误报",
        source_run="r1",
        sig=(f["source"], f["rule"], f["file"], f["message"]),
    )
    item = sup.load(tmp_path)[0]
    assert item["sig_rule"] == "F401" and item["sig_file"] == "a.py"
    assert item["sig_message"] == "unused"


def test_sig_matching_survives_line_shift(tmp_path):
    # 全量格式化后行号 10 → 999：id 失配，但签名命中 → 仍应豁免
    f_old = _finding(line=10)
    sup.add(
        tmp_path,
        finding_id=f_old["id"],
        action="false_positive",
        reason="误报",
        source_run="r1",
        sig=(f_old["source"], f_old["rule"], f_old["file"], f_old["message"]),
    )
    f_new = _finding(line=999)
    ids, sigs = sup.suppression_ids(tmp_path)
    assert f_new["id"] not in ids  # 新 id 不在精确集
    assert (f_new["source"], f_new["rule"], f_new["file"], f_new["message"]) in sigs


def test_legacy_entry_without_sig_still_id_match(tmp_path):
    sup.add(tmp_path, finding_id="a" * 40, action="debt_accepted", reason="暂不修", source_run="r1")
    ids, sigs = sup.suppression_ids(tmp_path)
    assert ids == {"a" * 40} and sigs == set()  # 旧条目无签名 → 只按 id


def test_reapply_refreshes_id_by_sig(tmp_path):
    f_old = _finding(line=10)
    sup.add(
        tmp_path,
        finding_id=f_old["id"],
        action="false_positive",
        reason="误报",
        source_run="r1",
        sig=(f_old["source"], f_old["rule"], f_old["file"], f_old["message"]),
    )
    f_new = _finding(line=777)  # 行号漂移后的新 run
    stats = sup.reapply(tmp_path, [f_new])
    assert stats == {"matched": 1, "unmatched": 0, "refreshed": 1}
    item = sup.load(tmp_path)[0]
    assert item["id"] == f_new["id"]  # id 已刷新


def test_reapply_unmatched_kept(tmp_path):
    sup.add(
        tmp_path,
        finding_id="b" * 40,
        action="debt_accepted",
        reason="暂不修",
        source_run="r1",
        sig=("ruff", "E501", "gone.py", "line too long"),
    )
    stats = sup.reapply(tmp_path, [_finding()])
    assert stats["unmatched"] == 1
    assert len(sup.load(tmp_path)) == 1  # 未命中条目保留


def test_json_roundtrip_with_sig(tmp_path):
    f = _finding()
    sup.add(
        tmp_path,
        finding_id=f["id"],
        action="false_positive",
        reason="r",
        source_run="r",
        sig=(f["source"], f["rule"], f["file"], f["message"]),
    )
    data = json.loads((tmp_path / ".cq" / "suppressions.json").read_text(encoding="utf-8"))
    assert data["suppressions"][0]["sig_source"] == "ruff"
