"""Issue #1 回归：CI 干净 checkout 下 spec-gate 恒绿 → --base/--range 让门真正起作用。

真 git 仓库（tmp_path 内 init + 两次提交），判定逻辑与一期共用。
"""

import subprocess
from pathlib import Path

import pytest

from ai_code_quality.specdrift import SpecDriftConfig, SpecDriftError, run_spec_gate


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _repo_with_drift(tmp_path: Path) -> str:
    """base 提交干净；第二个提交改 app/auth.py（无 proposal 无 waiver）。返回 base ref。"""
    _mk = lambda rel, c: (tmp_path / rel).write_text(c, encoding="utf-8")
    (tmp_path / ".specdrift.toml").write_text(
        'code_paths = ["app/"]\nspec_root = "openspec/specs"\n', encoding="utf-8"
    )
    (tmp_path / "app").mkdir()
    _mk("app/README.md", "# app\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    _mk("app/auth.py", "def login():\n    pass\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "改 auth：未在 spec 声明")
    return base


def _cfg(tmp_path: Path):
    return SpecDriftConfig(
        code_paths=["app/"], spec_root="openspec/specs", change_root="openspec/changes"
    )


def test_base_clean_working_tree_still_catches_drift(tmp_path):
    base = _repo_with_drift(tmp_path)
    # 工作区干净（旧口径恒绿的场景）
    assert _git(tmp_path, "status", "--porcelain") == ""
    res = run_spec_gate(tmp_path, cfg=_cfg(tmp_path), mode="hard", base=base, now=None)
    assert res.drift == ["app/auth.py"] and res.exit_code == 1


def test_base_covered_by_proposal(tmp_path):
    base = _repo_with_drift(tmp_path)
    # 补 proposal（声明 app/auth.py）
    (tmp_path / "openspec/changes/add-login/specs").mkdir(parents=True)
    (tmp_path / "openspec/specs/auth").mkdir(parents=True)
    (tmp_path / "openspec/specs/auth/spec.md").write_text("# auth\n", encoding="utf-8")
    (tmp_path / "openspec/changes/add-login/proposal.md").write_text(
        "spec_ref: auth\n\n本提案修改 app/auth.py 的登录。\n", encoding="utf-8"
    )
    (tmp_path / "openspec/changes/add-login/specs/auth.md").write_text(
        "## MODIFIED\n### 登录\n", encoding="utf-8"
    )
    res = run_spec_gate(tmp_path, cfg=_cfg(tmp_path), mode="hard", base=base, now=None)
    assert res.exit_code == 0 and res.covered_by == {"app/auth.py": "add-login"}


def test_base_waiver_covers(tmp_path):
    from ai_code_quality.specdrift import create_waiver

    base = _repo_with_drift(tmp_path)
    create_waiver(tmp_path, reason="先写代码", ttl="1h", now=None)
    res = run_spec_gate(tmp_path, cfg=_cfg(tmp_path), mode="hard", base=base, now=None)
    assert res.exit_code == 0 and res.waived == ["app/auth.py"]


def test_base_ref_missing_exit_3_semantics(tmp_path):
    _repo_with_drift(tmp_path)
    with pytest.raises(SpecDriftError):
        run_spec_gate(tmp_path, cfg=_cfg(tmp_path), mode="hard", base="no-such-ref", now=None)


def test_explicit_range(tmp_path):
    base = _repo_with_drift(tmp_path)
    (tmp_path / "app/new.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "range 内的新提交")
    head = _git(tmp_path, "rev-parse", "HEAD")
    res = run_spec_gate(
        tmp_path, cfg=_cfg(tmp_path), mode="hard", range_=f"{base}..{head}", now=None
    )
    assert "app/new.py" in res.drift and res.exit_code == 1


def test_no_base_working_tree_behavior_unchanged(tmp_path):
    """红线：无 --base 时保持 v0.3.0 行为（工作区 vs HEAD）。"""
    base = _repo_with_drift(tmp_path)
    # 工作区干净 → 无参 = 0 受管变更 = SPEC_OK（即使区间内有漂移提交）
    res = run_spec_gate(tmp_path, cfg=_cfg(tmp_path), mode="hard", now=None)
    assert res.changed_files == [] and res.exit_code == 0
    # 工作区有未提交改动 → 仍按工作区判
    (tmp_path / "app/auth.py").write_text("def login2():\n    pass\n", encoding="utf-8")
    res2 = run_spec_gate(tmp_path, cfg=_cfg(tmp_path), mode="hard", now=None)
    assert res2.drift == ["app/auth.py"]
    assert base  # base ref 可用
