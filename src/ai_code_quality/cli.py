"""cq 命令行入口。Phase 1 只有 scan；triage/fix/gate/diff 等属 Phase 2+。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .adapters import ADAPTER_NAMES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cq",
        description="通用本地代码质量扫描：ruff / mypy / radon / vulture / pytest-cov / import-linter / deepsec L1+L2 → 统一 findings 报告",
    )
    parser.add_argument("--version", action="version", version=f"cq {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="扫描项目并生成 audits/<run_id>/ 报告（只读，不改代码）")
    p_scan.add_argument("--project", default=".", help="项目路径（默认当前目录）")
    p_scan.add_argument("--scope", choices=["full", "changed"], default="full",
                        help="full=全量（默认）；changed=只扫 git 变更的 .py 文件")
    p_scan.add_argument("--format", choices=["md", "json"], default="md",
                        help="控制台摘要格式（report.md / findings.json / summary.json 总是都会生成）")
    p_scan.add_argument("--timeout", type=int, default=None, help="单工具默认超时秒数（默认 300，pytest/mypy/deepsec 可在 cq.toml 单独覆盖）")
    p_scan.add_argument("--adapters", default=None,
                        help=f"逗号分隔的 adapter 子集（可选：{','.join(ADAPTER_NAMES)}）")
    p_scan.add_argument("--python", default=None, help="跑外部工具用的 Python 解释器（默认当前解释器）")
    p_scan.add_argument("--deepsec-home", default=None, help="deepsec 源码仓库路径（未 pip 安装 deepsec 时用）")
    p_scan.add_argument("--deepsec-python", default=None, help="承载 deepsec 的 Python 解释器（默认同 --python）")
    p_scan.add_argument("--no-deepsec", action="store_true", help="跳过 deepsec 安全扫描")

    # Phase 2+ 的占位：提前声明退出码语义，避免下游 agent 依赖错误行为
    for name, help_text in [
        ("triage", "findings 归因分级（Phase 2）"),
        ("fix", "分批修复计划（Phase 2，默认 dry-run）"),
        ("gate", "质量门验收（Phase 2）"),
        ("baseline", "把某次 run 固化为基线（Phase 2）"),
        ("diff", "两次 run 对比（Phase 2）"),
    ]:
        sub.add_parser(name, help=help_text, description=f"{help_text}：尚未实现")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows 管道/重定向下 Python 默认用本地编码（GBK），统一强制 UTF-8，避免中文乱码
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    if args.command != "scan":
        print(f"错误：`cq {args.command}` 属于 Phase 2+，本次未实现。当前可用：cq scan", file=sys.stderr)
        return 1

    from .config import Config
    from .scan import run_scan

    project = Path(args.project)
    if not project.is_dir():
        print(f"错误：项目目录不存在: {project}", file=sys.stderr)
        return 1

    overrides: dict[str, object] = {}
    if args.timeout is not None:
        overrides["timeout_default"] = args.timeout
    if args.python:
        overrides["python"] = args.python
    if args.deepsec_home:
        overrides["deepsec_home"] = args.deepsec_home
    if args.deepsec_python:
        overrides["deepsec_python"] = args.deepsec_python
    if args.no_deepsec:
        overrides["no_deepsec"] = True

    enabled = [a.strip() for a in args.adapters.split(",") if a.strip()] if args.adapters else None

    cfg = Config.load(project, cli_overrides=overrides)
    try:
        out = run_scan(project, scope=args.scope, cfg=cfg, enabled_adapters=enabled)
    except (FileNotFoundError, ValueError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(out.summary, ensure_ascii=False, indent=2))
    else:
        from .report import build_console_output

        print(build_console_output(out.summary, out.findings))
        if out.exit_code == 2:
            print("\n注意：有工具执行出错/超时（status=error/timeout），上述结果不完整。", file=sys.stderr)
        runnable = [r for r in out.results if r.status == "ok"]
        if out.results and not runnable:
            print("\n警告：所有工具都 skipped，本次扫描没有产生任何有效结论。"
                  "请检查工具安装或 --python 指向的解释器。", file=sys.stderr)
    return out.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
