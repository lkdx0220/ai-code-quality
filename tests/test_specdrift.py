from datetime import UTC, datetime, timedelta

import pytest

from ai_code_quality import suppress as sup
from ai_code_quality.specdrift import (
    SpecDriftConfig,
    SpecDriftError,
    _declared,
    active_waivers,
    create_waiver,
    load_active_changes,
    load_specdrift_config,
    parse_porcelain,
    parse_ttl,
    render_spec_gate,
    run_spec_gate,
    slugify,
)


def _mk(root, rel, content="x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _cfg(tmp_path, code_paths=("app/",), **kw):
    c = SpecDriftConfig(code_paths=list(code_paths))
    for k, v in kw.items():
        setattr(c, k, v)
    return c


NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)


# ---------- ttl / slug ----------


def test_parse_ttl_forms():
    assert parse_ttl("30m") == timedelta(minutes=30)
    assert parse_ttl("12h") == timedelta(hours=12)
    assert parse_ttl("7d") == timedelta(days=7)
    assert parse_ttl("10s") == timedelta(seconds=10)
    with pytest.raises(SpecDriftError):
        parse_ttl("forever")
    with pytest.raises(SpecDriftError):
        parse_ttl("")


def test_slugify():
    assert slugify("先写代码 spec 后补") == "先写代码-spec-后补"
    assert slugify("!!!") == "waiver"
    assert len(slugify("x" * 100)) == 40


# ---------- porcelain ----------


def test_parse_porcelain():
    text = " M app/a.py\n?? new_dir/b.py\nR  old.py -> renamed.py\n"
    assert parse_porcelain(text) == ["app/a.py", "new_dir/b.py", "renamed.py"]


# ---------- 配置 ----------


def test_no_config_returns_none(tmp_path):
    assert load_specdrift_config(tmp_path) is None


def test_config_toml_roundtrip(tmp_path):
    _mk(
        tmp_path,
        ".specdrift.toml",
        'code_paths = ["app/", "prompts/"]\nspec_root = "openspec/specs"\nsoft_timestamp_check = false\n',
    )
    cfg = load_specdrift_config(tmp_path)
    assert cfg is not None
    assert cfg.code_paths == ["app/", "prompts/"]
    assert cfg.spec_root == "openspec/specs"
    assert cfg.soft_timestamp_check is False


# ---------- 无配置/空配置的行为（红线 1/2） ----------


def test_spec_gate_no_config_exit_zero(tmp_path):
    res = run_spec_gate(tmp_path, mode="hard", changed_files=["app/a.py"])
    assert res.exit_code == 0 and not res.configured


def test_spec_gate_empty_code_paths(tmp_path):
    res = run_spec_gate(tmp_path, cfg=_cfg(tmp_path, code_paths=[]), changed_files=["app/a.py"])
    assert res.exit_code == 0 and res.soft_hints


# ---------- 覆盖判定 ----------


def _setup_openspec(tmp_path):
    _mk(tmp_path, "openspec/specs/auth/spec.md", "# auth 规格现状\n")
    _mk(
        tmp_path,
        "openspec/changes/add-login/proposal.md",
        "# 提案\nspec_ref: auth\n\n涉及 app/auth.py 的登录改造。\n",
    )
    _mk(tmp_path, "openspec/changes/add-login/specs/auth.md", "## MODIFIED\n### 登录流程\n…\n")


def test_drift_when_code_changed_without_declaration(tmp_path):
    _setup_openspec(tmp_path)
    res = run_spec_gate(
        tmp_path, cfg=_cfg(tmp_path), mode="hard", changed_files=["app/other.py"], now=NOW
    )
    assert res.drift == ["app/other.py"] and res.exit_code == 1


def test_covered_when_proposal_declares_file(tmp_path):
    _setup_openspec(tmp_path)
    res = run_spec_gate(
        tmp_path, cfg=_cfg(tmp_path), mode="hard", changed_files=["app/auth.py"], now=NOW
    )
    assert res.drift == [] and res.covered_by == {"app/auth.py": "add-login"} and res.exit_code == 0


def test_out_of_scope_files_ignored(tmp_path):
    _setup_openspec(tmp_path)
    res = run_spec_gate(
        tmp_path,
        cfg=_cfg(tmp_path),
        mode="hard",
        changed_files=["docs/readme.md", "app/auth.py"],
        now=NOW,
    )
    assert res.changed_files == ["app/auth.py"]
    assert res.exit_code == 0


def test_soft_mode_drift_still_exit_zero(tmp_path):
    _setup_openspec(tmp_path)
    res = run_spec_gate(
        tmp_path, cfg=_cfg(tmp_path), mode="soft", changed_files=["app/other.py"], now=NOW
    )
    assert res.drift == ["app/other.py"] and res.exit_code == 0


def test_paths_override(tmp_path):
    _setup_openspec(tmp_path)
    res = run_spec_gate(
        tmp_path,
        cfg=_cfg(tmp_path, code_paths=["app/"]),
        paths_override=["prompts/"],
        changed_files=["app/x.py", "prompts/y.py"],
        now=NOW,
    )
    assert res.changed_files == ["prompts/y.py"]


def test_change_without_delta_not_viable(tmp_path):
    _mk(tmp_path, "openspec/specs/auth/spec.md")
    _mk(
        tmp_path,
        "openspec/changes/bad/proposal.md",
        "spec_ref: auth\n只提到 app/auth.py 但没有 delta 标记\n",
    )
    res = run_spec_gate(
        tmp_path, cfg=_cfg(tmp_path), mode="hard", changed_files=["app/auth.py"], now=NOW
    )
    assert res.drift == ["app/auth.py"] and res.exit_code == 1


# ---------- waiver ----------


def test_waiver_covers_drift_until_expiry(tmp_path):
    _setup_openspec(tmp_path)
    create_waiver(tmp_path, reason="先写代码", ttl="1h", now=NOW)
    # 生效期内：不判漂移
    res = run_spec_gate(
        tmp_path,
        cfg=_cfg(tmp_path),
        mode="hard",
        changed_files=["app/other.py"],
        now=NOW + timedelta(minutes=30),
    )
    assert res.drift == [] and res.waived == ["app/other.py"] and res.exit_code == 0
    # 过期后：视为漂移
    res2 = run_spec_gate(
        tmp_path,
        cfg=_cfg(tmp_path),
        mode="hard",
        changed_files=["app/other.py"],
        now=NOW + timedelta(hours=2),
    )
    assert res2.drift == ["app/other.py"] and res2.exit_code == 1
    assert len(res2.expired_waivers) == 1


def test_waiver_decision_file_created(tmp_path):
    path, entry = create_waiver(
        tmp_path, reason="先写代码 spec 后补", ttl="24h", change_id="add-login", now=NOW
    )
    assert path.exists() and path.parent.name == "decisions"
    assert path.name.startswith("2026-10-12-")
    text = path.read_text(encoding="utf-8")
    assert "add-login" in text and "deadline" in text
    assert entry["kind"] == "spec_waiver" and entry["change_id"] == "add-login"
    assert entry["expires_at"].startswith("2026-10-13")  # +24h


def test_waiver_same_day_same_slug_no_overwrite(tmp_path):
    p1, _ = create_waiver(tmp_path, reason="同理由", ttl="1h", now=NOW)
    p2, _ = create_waiver(tmp_path, reason="同理由", ttl="1h", now=NOW)
    assert p1 != p2 and p1.exists() and p2.exists()


def test_finding_entries_ignore_waivers(tmp_path):
    create_waiver(tmp_path, reason="spec 后补", ttl="1h", now=NOW)
    sup.add(tmp_path, finding_id="a" * 40, action="false_positive", reason="误报", source_run="r1")
    items = sup.load(tmp_path)
    assert len(items) == 2
    findings = sup.finding_entries(items)
    assert len(findings) == 1 and findings[0].get("kind", "finding") == "finding"
    ids, _sigs = sup.suppression_ids(tmp_path)
    assert "a" * 40 in ids  # finding 豁免正常工作
    active, expired = active_waivers(tmp_path, now=NOW + timedelta(days=2))
    assert not active and len(expired) == 1  # waiver 归 spec-drift 管


def test_active_changes_parsed(tmp_path):
    _setup_openspec(tmp_path)
    changes = load_active_changes(tmp_path, _cfg(tmp_path))
    assert len(changes) == 1
    assert changes[0].spec_ref == "auth" and changes[0].spec_exists and changes[0].has_delta


def test_declared_directory_reference():
    ch = type("C", (), {"text": "本提案改造 app/ 模块"})()
    assert _declared("app/deep/file.py", [ch]) is not None


def test_render_contains_expected_markers(tmp_path):
    _setup_openspec(tmp_path)
    res = run_spec_gate(
        tmp_path, cfg=_cfg(tmp_path), mode="hard", changed_files=["app/other.py"], now=NOW
    )
    out = render_spec_gate(res)
    assert "SPEC_DRIFT: app/other.py" in out and "SPEC_DRIFT_FOUND: 1" in out
    res2 = run_spec_gate(
        tmp_path, cfg=_cfg(tmp_path), mode="hard", changed_files=["app/auth.py"], now=NOW
    )
    assert "SPEC_OK" in render_spec_gate(res2)
