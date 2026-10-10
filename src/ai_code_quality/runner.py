"""subprocess 执行器：统一 UTF-8、超时、找不到命令、缓存重定向。

Windows 约定：
- 所有子进程强制 PYTHONIOENCODING=utf-8，输出按 utf-8 解码（errors=replace）；
- PYTHONDONTWRITEBYTECODE=1，避免在目标仓库里落 __pycache__；
- 命令一律 list 形式，不经 shell，中文路径安全。
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field


@dataclass
class ProcResult:
    cmd: list[str]
    returncode: int | None  # None = 命令不存在或没能启动
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    timed_out: bool = False
    not_found: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0


BASE_ENV = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONDONTWRITEBYTECODE": "1",
}


def run_cmd(
    cmd: list[str],
    *,
    timeout: int,
    cwd: str | os.PathLike[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> ProcResult:
    env = dict(os.environ)
    env.update(BASE_ENV)
    if extra_env:
        env.update(extra_env)
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            capture_output=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
        return ProcResult(
            cmd=cmd,
            returncode=proc.returncode,
            stdout=proc.stdout.decode("utf-8", errors="replace"),
            stderr=proc.stderr.decode("utf-8", errors="replace"),
            duration_s=time.monotonic() - start,
        )
    except subprocess.TimeoutExpired:
        return ProcResult(
            cmd=cmd, returncode=None, timed_out=True, duration_s=time.monotonic() - start
        )
    except (FileNotFoundError, OSError):
        return ProcResult(
            cmd=cmd, returncode=None, not_found=True, duration_s=time.monotonic() - start
        )


def probe(cmd: list[str], *, timeout: int = 30, cwd=None, extra_env=None) -> ProcResult:
    """快速探测工具是否可用（通常 --version / --help）。"""
    return run_cmd(cmd, timeout=timeout, cwd=cwd, extra_env=extra_env)


@dataclass
class VersionProbe:
    available: bool
    version: str
    detail: str = field(default="")
