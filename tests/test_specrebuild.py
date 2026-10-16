from datetime import UTC, datetime, timedelta

import pytest

from ai_code_quality.specdrift import SpecDriftConfig, load_specdrift_config
from ai_code_quality.specrebuild import (
    apply_rebuild,
    build_patch_skeleton,
    cluster_decisions,
    collect_decisions,
    extract_public_symbols,
    extract_spec_headings,
    promote_candidates,
    run_rebuild,
)

NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)


def _mk(root, rel, content="x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _cfg(**kw):
    c = SpecDriftConfig(code_paths=["app/"])
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ---------- 符号提取 ----------


def test_extract_symbols_public_only(tmp_path):
    _mk(
        tmp_path,
        "app/svc.py",
        (
            "import os\n"
            "class LoginService:\n    pass\n"
            "def verify_token(t):\n    return t\n"
            "def _helper():\n    pass\n"
            "def __dunder__():\n    pass\n"
        ),
    )
    syms, broken = extract_public_symbols(tmp_path, ["app/"])
    names = {(s.name, s.kind) for s in syms}
    assert ("LoginService", "class") in names
    assert ("verify_token", "function") in names
    assert ("svc", "module") in names
    assert all(not s.name.startswith("_") for s in syms)
    assert broken == []


def test_extract_symbols_broken_file_recorded(tmp_path):
    _mk(tmp_path, "app/bad.py", "def (:\n")
    syms, broken = extract_public_symbols(tmp_path, ["app/"])
    assert broken == ["app/bad.py"]
    assert ("bad", "module") not in {(s.name, s.kind) for s in syms}


# ---------- 对账 ----------


def test_rebuild_code_missing_and_covered(tmp_path):
    _mk(tmp_path, "app/auth.py", "def verify_token(t):\n    return t\n")
    _mk(tmp_path, "app/billing.py", "def charge_user(u):\n    return u\n")
    _mk(tmp_path, "openspec/specs/core/spec.md", "# 核心\nverify_token 负责令牌校验。\n")
    res = run_rebuild(tmp_path, cfg=_cfg())
    missing = {s.name for s in res.code_missing}
    assert "verify_token" not in missing  # spec 提到 → 覆盖
    assert "charge_user" in missing  # spec 没提 → 代码有 spec 无
    assert res.covered_count >= 1


def test_rebuild_no_spec_root(tmp_path):
    _mk(tmp_path, "app/a.py", "def f():\n    pass\n")
    res = run_rebuild(tmp_path, cfg=_cfg())
    assert res.code_missing and "spec_root 无任何" in res.notes[0]


def test_rebuild_unconfigured(tmp_path):
    res = run_rebuild(tmp_path)  # 无 .specdrift.toml
    assert not res.configured and res.notes


def test_rebuild_config_with_promote_fields(tmp_path):
    _mk(
        tmp_path,
        ".specdrift.toml",
        'code_paths = ["app/"]\npromote_window = "14d"\npromote_threshold = 5\n',
    )
    cfg = load_specdrift_config(tmp_path)
    assert cfg.promote_window == "14d" and cfg.promote_threshold == 5


def test_rebuild_spec_only_heading(tmp_path):
    _mk(tmp_path, "app/auth.py", "def verify_token(t):\n    return t\n")
    _mk(
        tmp_path,
        "openspec/specs/core/spec.md",
        "# auth\n## verify_token\nok\n## 遗留幻影模块\n（代码里没有）\n",
    )
    res = run_rebuild(tmp_path, cfg=_cfg())
    headings = {h for _, h in res.spec_missing}
    assert "遗留幻影模块" in headings  # spec 有代码无（低置信度）
    assert "verify_token" not in headings


def test_spec_headings_extracted(tmp_path):
    _mk(tmp_path, "openspec/specs/a/s.md", "# a\n## b\n### c\n正文\n")
    hs = extract_spec_headings(tmp_path, _cfg())
    assert [h for _, h in hs] == ["a", "b", "c"]


# ---------- 骨架与 apply ----------


def test_skeleton_is_template_not_content(tmp_path):
    _mk(tmp_path, "app/a.py", "def charge_user(u):\n    return u\n")
    res = run_rebuild(tmp_path, cfg=_cfg())
    sk = build_patch_skeleton(res, now=NOW)
    assert "charge_user" in sk and "TODO" in sk
    assert "app/a.py" in sk


def test_apply_writes_skeleton_and_resets_debt(tmp_path):
    from ai_code_quality.specdrift import create_waiver

    _mk(tmp_path, "app/a.py", "def charge_user(u):\n    return u\n")
    _mk(tmp_path, "openspec/specs/core/spec.md", "# core\n")
    # 一条已过期 + 一条未过期的 waiver
    create_waiver(tmp_path, reason="旧债", ttl="1h", now=NOW - timedelta(days=3))
    create_waiver(tmp_path, reason="新债", ttl="7d", now=NOW)
    res = run_rebuild(tmp_path, cfg=_cfg())
    out, removed = apply_rebuild(tmp_path, res, _cfg(), now=NOW)
    assert out.exists() and out.parent.name == "specs" and out.name.startswith("_rebuild-")
    assert removed == 1  # 只清过期的
    import json

    ledger = json.loads((tmp_path / ".cq" / "suppressions.json").read_text(encoding="utf-8"))
    reasons = [i["reason"] for i in ledger["suppressions"]]
    assert "新债" in reasons and "旧债" not in reasons


def test_apply_no_missing_raises(tmp_path):
    from ai_code_quality.specdrift import SpecDriftError

    # 符号名够独特，避免单字母在 spec 文本里词边界误命中
    _mk(tmp_path, "app/orders.py", "def process_order(o):\n    return o\n")
    _mk(tmp_path, "openspec/specs/s.md", "# orders 模块\nprocess_order 负责下单。\n")
    res = run_rebuild(tmp_path, cfg=_cfg())
    assert res.code_missing == []
    with pytest.raises(SpecDriftError):
        apply_rebuild(tmp_path, res, _cfg())


# ---------- promote ----------


def _decision(root, day, title):
    _mk(
        root,
        f"decisions/{day}-{title.replace(' ', '-')[:20]}.md",
        f"# Waiver: {title}\n\n- deadline: …\n",
    )


def test_promote_candidates_threshold(tmp_path):
    _decision(tmp_path, "2026-10-01", "先写代码 spec 后补")
    _decision(tmp_path, "2026-10-05", "先写代码 spec 后补")
    _decision(tmp_path, "2026-10-10", "先写代码 spec 后补")
    _decision(tmp_path, "2026-09-01", "先写代码 spec 后补")  # 窗口外
    _decision(tmp_path, "2026-10-08", "无关决策一次性")
    cands, _notes = promote_candidates(tmp_path, _cfg(), now=NOW)
    assert len(cands) == 1
    c = cands[0]
    assert c["count"] == 3  # 窗口内 3 次，窗口外的没算
    assert "TODO" in str(c["draft"]) and "拟晋升" in str(c["draft"])


def test_promote_threshold_configurable(tmp_path):
    for d in (1, 2):
        _decision(tmp_path, f"2026-10-0{d}", "先写代码 spec 后补")
    cands, _ = promote_candidates(tmp_path, _cfg(promote_threshold=2), now=NOW)
    assert len(cands) == 1
    cands2, _ = promote_candidates(tmp_path, _cfg(), now=NOW)  # 默认阈值 3
    assert cands2 == []


def test_cluster_by_keyword_overlap():
    from ai_code_quality.specrebuild import DecisionRecord

    def rec(t):
        return DecisionRecord(source="x", title=t, date=NOW, slug="")

    cls = cluster_decisions([rec("密钥轮换 每月"), rec("密钥轮换 每季度"), rec("完全无关")])
    sizes = sorted(len(c) for c in cls)
    assert sizes == [1, 2]


def test_collect_decisions_merges_ledger(tmp_path):
    from ai_code_quality.specdrift import create_waiver

    _decision(tmp_path, "2026-10-01", "文件里的决策")
    create_waiver(tmp_path, reason="账本里的决策", ttl="1h", now=NOW)
    records = collect_decisions(tmp_path, _cfg(), now=NOW)
    titles = {r.title for r in records}
    assert "文件里的决策" in titles and "账本里的决策" in titles
