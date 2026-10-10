"""adapter 注册表：新增工具 = 新增一个模块 + 在这里登记，scan.py 不改。"""

from __future__ import annotations

from .base import Adapter, ScanContext
from .deepsec_adapter import DeepsecAdapter
from .import_linter import ImportLinterAdapter
from .mypy_adapter import MypyAdapter
from .pytest_cov import PytestCovAdapter
from .radon_adapter import RadonAdapter
from .ruff_check import RuffCheckAdapter
from .ruff_format import RuffFormatAdapter
from .vulture_adapter import VultureAdapter

ALL_ADAPTERS: list[Adapter] = [
    RuffCheckAdapter(),
    RuffFormatAdapter(),
    MypyAdapter(),
    RadonAdapter(),
    VultureAdapter(),
    PytestCovAdapter(),
    ImportLinterAdapter(),
    DeepsecAdapter(),
]

ADAPTER_NAMES = [a.name for a in ALL_ADAPTERS]


def get_adapters(enabled: list[str] | None = None) -> list[Adapter]:
    if not enabled:
        return list(ALL_ADAPTERS)
    unknown = [n for n in enabled if n not in ADAPTER_NAMES]
    if unknown:
        raise ValueError(f"未知 adapter: {unknown}（可用: {', '.join(ADAPTER_NAMES)}）")
    return [a for a in ALL_ADAPTERS if a.name in enabled]


__all__ = [
    "Adapter",
    "ScanContext",
    "ALL_ADAPTERS",
    "ADAPTER_NAMES",
    "get_adapters",
]
