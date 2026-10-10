from pathlib import Path

from ai_code_quality.targets import _Gitignore

# 假想根目录：不需要真实存在，match() 只做 resolve + relative_to 的路径运算
ROOT = Path("C:/_gitest")


def _gi(lines: list[str]) -> _Gitignore:
    return _Gitignore(ROOT, lines)


def _match(gi: _Gitignore, rel: str, is_dir: bool = False) -> bool:
    return gi.match(ROOT / rel, is_dir=is_dir)


def test_dir_name_pattern_matches_any_level():
    gi = _gi(["_tools/"])
    assert _match(gi, "_tools/run.py")
    assert _match(gi, "sub/_tools/lib.py")


def test_basename_glob():
    gi = _gi(["*.bak", "data_*.json"])
    assert _match(gi, "a.bak")
    assert _match(gi, "deep/data_2024.json")
    assert not _match(gi, "keep.py")


def test_rel_path():
    gi = _gi(["legacy/old.py", "vendor/"])
    assert _match(gi, "legacy/old.py")
    assert not _match(gi, "other/old.py")
    assert _match(gi, "vendor/x.py")


def test_comments_skipped_and_negation_counted():
    gi = _gi(["# 注释", "", "!keep/", "_tmp/"])
    assert not _match(gi, "keep/x.py")  # ! 否定不支持：keep/ 不作为忽略项
    assert _match(gi, "_tmp/a.py")
    assert gi.negations == 1
