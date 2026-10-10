"""adapter 基类与扫描上下文。

每个 adapter = 一个工具检查，约定：
- 先探测可用性（--version / --help），不可用 → skipped + reason；
- 正式执行；超时 → timeout + reason；崩溃/解析失败 → error + reason；
- 原始输出一律落盘到 raw/（先脱敏），findings 的 raw_ref 指回它；
- file 字段统一转成"相对项目根、正斜杠"。
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..schema import AdapterResult, Finding


@dataclass
class ScanContext:
    project_root: Path
    run_dir: Path  # audits/<run_id>/
    raw_dir: Path  # audits/<run_id>/raw/
    run_id: str
    cfg: Config
    targets: list[Path]
    source_dirs: list[Path]
    has_tests: bool
    test_paths: list[Path]
    importlinter_config: Path | None
    mypy_config: Path | None
    notes: list[str] = field(default_factory=list)

    def rel(self, p: Path | str) -> str:
        """绝对/相对路径 → 相对项目根的正斜杠字符串；不在项目内则原样。"""
        p = Path(p)
        try:
            r = p.resolve().relative_to(self.project_root.resolve())
            return r.as_posix()
        except ValueError:
            return p.as_posix()


class Adapter:
    name: str = "adapter"
    tool: str = "tool"
    description: str = ""

    def run(self, ctx: ScanContext) -> AdapterResult:
        raise NotImplementedError

    def safe_run(self, ctx: ScanContext) -> AdapterResult:
        """scan.py 的统一入口：捕获一切异常，转为 error 结果，不许让整个扫描崩掉。"""
        try:
            result = self.run(ctx)
        except Exception as exc:  # noqa: BLE001 - 单工具崩溃不能拖垮整个扫描
            tb = traceback.format_exc()
            self._write_error_file(ctx, tb)
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="error",
                reason=f"adapter 内部异常: {type(exc).__name__}: {exc}",
            )
        if result.status != "ok" and not result.reason:
            result.reason = f"status={result.status}（未提供原因）"
        return result

    def _write_error_file(self, ctx: ScanContext, text: str) -> None:
        from ..redact import redact_text

        try:
            ctx.raw_dir.mkdir(parents=True, exist_ok=True)
            (ctx.raw_dir / f"{self.name}.error.txt").write_text(
                redact_text(text), encoding="utf-8", errors="replace"
            )
        except OSError:
            pass

    # ---- helpers ----
    def write_raw(self, ctx: ScanContext, filename: str, text: str) -> str:
        """脱敏后写 raw 文件，返回相对 run 目录的引用路径。"""
        from ..redact import redact_text

        ctx.raw_dir.mkdir(parents=True, exist_ok=True)
        path = ctx.raw_dir / filename
        path.write_text(redact_text(text), encoding="utf-8", errors="replace")
        return f"raw/{filename}"


def finding(
    ctx: ScanContext,
    *,
    source: str,
    category: str,
    severity: str,
    file: str | Path,
    line: int,
    message: str,
    **kw: object,
) -> Finding:
    """构造 Finding 的统一入口：file 归一化 + raw_ref 前缀。"""
    raw_ref = kw.pop("raw_ref", "")
    if raw_ref and not raw_ref.startswith("raw/"):
        raw_ref = f"raw/{raw_ref}"
    return Finding(
        source=source,
        category=category,
        severity=severity,
        file=ctx.rel(file) if file else "",
        line=line,
        message=str(message),
        raw_ref=raw_ref,
        **kw,  # type: ignore[arg-type]
    )
