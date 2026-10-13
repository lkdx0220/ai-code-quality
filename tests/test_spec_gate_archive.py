"""Issue #2 回归：归档变更按区间参与声明匹配。

场景：流程是「实施 → 验证 → 归档变更 → 提交推送」，--base 区间会同时包含
代码改动与变更归档（changes/X → changes/archive/X 的移动）。归档变更若不参与
声明匹配，它声明过的代码会被误报 SPEC_DRIFT；但也要防止久远归档永久白名单。
"""

import subprocess
from pathlib import Path

from ai_code_quality.specdrift import SpecDriftConfig, run_spec_gate


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _w(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _commit(root: Path, msg: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", msg)
    return _git(root, "rev-parse", "HEAD")


def _cfg(tmp_path: Path):
    return SpecDriftConfig(
        code_paths=["app/"], spec_root="openspec/specs", change_root="openspec/changes"
    )


def _new_change(tmp_path: Path, name: str, declared_file: str) -> None:
    """在 changes/<name>/ 建一个声明 declared_file 的合规变更（spec_ref + delta）。"""
    spec_name = declared_file.split("/")[1].split(".")[0]
    _w(tmp_path, f"openspec/specs/{spec_name}/spec.md", f"# {spec_name} 现状\n")
    _w(
        tmp_path,
        f"openspec/changes/{name}/proposal.md",
        f"spec_ref: {spec_name}\n\n本提案修改 {declared_file}。\n",
    )
    _w(tmp_path, f"openspec/changes/{name}/specs/{spec_name}.md", "## MODIFIED\n### x\n")


def _archive(tmp_path: Path, name: str) -> None:
    # 纯文件系统移动：tracked（git 识别为 rename）与 untracked 场景都适用
    src = tmp_path / "openspec/changes" / name
    dst = tmp_path / "openspec/changes/archive" / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)


def test_in_range_archived_change_still_covers(tmp_path):
    # 实施提交（代码+变更同 commit）→ 归档提交（git mv）→ --base 检查不误报
    (tmp_path / ".specdrift.toml").write_text('code_paths = ["app/"]\n', encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@t")
    base = _commit(tmp_path, "base")
    _new_change(tmp_path, "add-entity", "app/entity_retrieval.py")
    _w(tmp_path, "app/entity_retrieval.py", "def fetch():\n    pass\n")
    _commit(tmp_path, "实施 add-entity")
    _archive(tmp_path, "add-entity")
    _commit(tmp_path, "归档 add-entity")

    res = run_spec_gate(tmp_path, cfg=_cfg(tmp_path), mode="hard", base=base, now=None)
    assert res.drift == [] and res.exit_code == 0
    assert res.covered_by.get("app/entity_retrieval.py") == "add-entity"


def test_out_of_range_archived_change_no_whitelist(tmp_path):
    # 旧归档（归档动作在 base 之前，其文件不在区间 diff）→ 仍报 drift，不得永久白名单
    (tmp_path / ".specdrift.toml").write_text('code_paths = ["app/"]\n', encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@t")
    _new_change(tmp_path, "old-archived", "app/legacy.py")
    _w(tmp_path, "app/legacy.py", "def old():\n    pass\n")
    _archive(tmp_path, "old-archived")
    base = _commit(tmp_path, "base（旧归档已存在）")
    _w(tmp_path, "app/legacy.py", "def old2():\n    pass\n")
    _commit(tmp_path, "区间内只改代码，旧归档不在区间")

    res = run_spec_gate(tmp_path, cfg=_cfg(tmp_path), mode="hard", base=base, now=None)
    assert res.drift == ["app/legacy.py"] and res.exit_code == 1


def test_no_base_archived_skipped_unchanged(tmp_path):
    # 红线：无 --base/--range 时保持原行为（归档不参与）
    (tmp_path / ".specdrift.toml").write_text('code_paths = ["app/"]\n', encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@t")
    _commit(tmp_path, "base")
    _new_change(tmp_path, "add-x", "app/x.py")
    _w(tmp_path, "app/x.py", "def x():\n    pass\n")
    _archive(tmp_path, "add-x")
    _w(tmp_path, "app/x.py", "def x2():\n    pass\n")  # 工作区改动（归档变更声明过它）

    res = run_spec_gate(tmp_path, cfg=_cfg(tmp_path), mode="hard", now=None)
    assert res.drift == ["app/x.py"] and res.exit_code == 1
