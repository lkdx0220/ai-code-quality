"""配置加载：默认值 < 项目 cq.toml/.cq.toml < 环境变量 < CLI 参数。

工具路径全部可配置（交接方案 §14.5），支持 Windows 中文路径。
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILENAMES = ("cq.toml", ".cq.toml")

# 各 adapter 的默认超时（秒）；--timeout 只改 default，个别工具可在此覆盖
DEFAULT_TIMEOUTS: dict[str, int] = {
    "default": 300,
    "pytest": 600,
    "mypy": 600,
    "deepsec": 600,
}

ENV_KEYS = {
    "python": "CQ_PYTHON",
    "deepsec_home": "CQ_DEEPSEC_HOME",
    "deepsec_python": "CQ_DEEPSEC_PYTHON",
    "timeout": "CQ_TIMEOUT",
}


@dataclass
class Config:
    # 跑外部工具用的解释器（python -m ruff 等）。默认当前解释器；
    # cq 装进隔离 venv 时，把它指到装了 ruff/mypy/... 的那个 Python。
    python: str = sys.executable
    # deepsec 源码仓库路径（未 pip install 时通过 PYTHONPATH 调用）；None 表示只用 pip 安装形态
    deepsec_home: str | None = None
    # 承载 deepsec 的解释器（可与 python 不同）；None 表示同 python
    deepsec_python: str | None = None
    timeout_default: int = DEFAULT_TIMEOUTS["default"]
    timeout_overrides: dict[str, int] = field(default_factory=dict)
    coverage_threshold: float = 80.0  # 总覆盖率低于此值报 finding
    vulture_min_confidence: int = 60
    max_cc_rank: str = "D"  # 圈复杂度 D 及更差报 finding（D/E/F）
    min_mi_rank: str = "C"  # 可维护性指数 C 及更差报 finding
    enabled_adapters: list[str] | None = None  # None = 全部启用
    no_deepsec: bool = False  # CLI --no-deepsec
    # 扫描目标是否遵循根 .gitignore（轻量子集：目录名/通配/相对路径，不支持 ! 否定）。
    # 与 ruff/pytest 等工具的忽略语义对齐；关掉则连 .gitignore 里的路径也扫。
    respect_gitignore: bool = True
    # 解释器来源（doctor 展示用）：default | config | env | cli
    python_source: str = "default"

    def timeout_for(self, adapter_name: str) -> int:
        if adapter_name in self.timeout_overrides:
            return self.timeout_overrides[adapter_name]
        return self.timeout_default

    @staticmethod
    def load(
        project_root: Path,
        *,
        cli_overrides: dict[str, object] | None = None,
    ) -> Config:
        cfg = Config()
        # 1) 项目配置文件
        for name in CONFIG_FILENAMES:
            p = project_root / name
            if p.is_file():
                data = _parse_toml(p)
                if data:
                    tool_section = data.get("tool", {})
                    if isinstance(tool_section, dict):
                        cfg = _apply(cfg, tool_section)
                    # 顶层键也接受（tool. 前缀省略的写法）
                    before = cfg.python
                    cfg = _apply(cfg, {k: v for k, v in data.items() if k != "tool"})
                    if cfg.python != before:
                        cfg.python_source = "config"
                break
        # 2) 环境变量
        env: dict[str, object] = {}
        if os.environ.get(ENV_KEYS["python"]):
            env["python"] = os.environ[ENV_KEYS["python"]]
        if os.environ.get(ENV_KEYS["deepsec_home"]):
            env["deepsec_home"] = os.environ[ENV_KEYS["deepsec_home"]]
        if os.environ.get(ENV_KEYS["deepsec_python"]):
            env["deepsec_python"] = os.environ[ENV_KEYS["deepsec_python"]]
        if os.environ.get(ENV_KEYS["timeout"]):
            try:
                env["timeout_default"] = int(os.environ[ENV_KEYS["timeout"]])
            except ValueError:
                pass
        before = cfg.python
        cfg = _apply(cfg, env)
        if cfg.python != before:
            cfg.python_source = "env"
        # 3) CLI 参数（最高优先级）
        if cli_overrides:
            before = cfg.python
            cfg = _apply(cfg, cli_overrides)
            if cfg.python != before:
                cfg.python_source = "cli"
        return cfg

    def snapshot(self) -> dict[str, object]:
        return {
            "python": self.python,
            "python_source": self.python_source,
            "deepsec_home": self.deepsec_home,
            "deepsec_python": self.deepsec_python,
            "timeout_default": self.timeout_default,
            "timeout_overrides": self.timeout_overrides,
            "coverage_threshold": self.coverage_threshold,
            "vulture_min_confidence": self.vulture_min_confidence,
            "max_cc_rank": self.max_cc_rank,
            "min_mi_rank": self.min_mi_rank,
        }


def _parse_toml(path: Path) -> dict[str, object]:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)  # type: ignore[no-any-return]
    except (tomllib.TOMLDecodeError, OSError):
        return {}


_KNOWN_KEYS = {
    "python": str,
    "deepsec_home": lambda v: str(v) or None,
    "deepsec_python": lambda v: str(v) or None,
    "timeout_default": int,
    "timeout_overrides": dict,
    "coverage_threshold": float,
    "vulture_min_confidence": int,
    "max_cc_rank": str,
    "min_mi_rank": str,
    "enabled_adapters": list,
    "no_deepsec": bool,
    "respect_gitignore": bool,
}


def _apply(cfg: Config, data: dict[str, object]) -> Config:
    for key, cast in _KNOWN_KEYS.items():
        if key in data and data[key] is not None:
            try:
                setattr(cfg, key, cast(data[key]))  # type: ignore[operator]
            except (ValueError, TypeError):
                continue  # 坏值忽略，保默认
    return cfg
