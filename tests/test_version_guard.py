"""打包元数据守卫：防止 pyproject 与 __init__ 版本再度脱节（v0.5.0 曾错位：0.4.0 vs 0.5.0）。"""

import tomllib
from pathlib import Path


def test_version_consistency():
    root = Path(__file__).resolve().parent.parent
    meta = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    pyproject_version = meta["project"]["version"]
    from ai_code_quality import __version__

    assert pyproject_version == __version__, (
        f"pyproject({pyproject_version}) 与 __init__({__version__}) 版本脱节——"
        "发版时两处必须同步修改"
    )
