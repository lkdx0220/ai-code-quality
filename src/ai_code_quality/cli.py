"""cq 命令行入口。

已实现：scan / doctor / triage / baseline / diff / gate / suppress；fix 属 Phase 3（默认只出计划，--apply 需用户批准）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__
from .adapters import ADAPTER_NAMES

if TYPE_CHECKING:
    from .scan import ScanOutput


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cq",
        description="通用本地代码质量扫描：ruff / mypy / radon / vulture / pytest-cov / import-linter / deepsec L1+L2 → 统一 findings 报告",
    )
    parser.add_argument("--version", action="version", version=f"cq {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="扫描项目并生成 audits/<run_id>/ 报告（只读，不改代码）")
    p_scan.add_argument("--project", default=".", help="项目路径（默认当前目录）")
    p_scan.add_argument(
        "--scope",
        choices=["full", "changed"],
        default="full",
        help="full=全量（默认）；changed=只扫 git 变更的 .py 文件",
    )
    p_scan.add_argument(
        "--format",
        choices=["md", "json"],
        default="md",
        help="控制台摘要格式（report.md / findings.json / summary.json 总是都会生成）",
    )
    p_scan.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="单工具默认超时秒数（默认 300，pytest/mypy/deepsec 可在 cq.toml 单独覆盖）",
    )
    p_scan.add_argument(
        "--adapters",
        default=None,
        help=f"逗号分隔的 adapter 子集（可选：{','.join(ADAPTER_NAMES)}）",
    )
    p_scan.add_argument(
        "--python", default=None, help="跑外部工具用的 Python 解释器（默认当前解释器）"
    )
    p_scan.add_argument(
        "--deepsec-home", default=None, help="deepsec 源码仓库路径（未 pip 安装 deepsec 时用）"
    )
    p_scan.add_argument(
        "--deepsec-python", default=None, help="承载 deepsec 的 Python 解释器（默认同 --python）"
    )
    p_scan.add_argument("--no-deepsec", action="store_true", help="跳过 deepsec 安全扫描")

    p_doc = sub.add_parser("doctor", help="环境体检：探测 8 个外部工具，缺什么给什么安装命令")
    p_doc.add_argument(
        "--format",
        choices=["md", "json"],
        default="md",
        help="控制台输出格式（json 供 Agent 消费）",
    )
    p_doc.add_argument(
        "--python", default=None, help="探测用的 Python 解释器（默认当前解释器 / CQ_PYTHON）"
    )
    p_doc.add_argument(
        "--deepsec-home", default=None, help="deepsec 源码仓库路径（探测源码形态用）"
    )
    p_doc.add_argument("--deepsec-python", default=None, help="承载 deepsec 的解释器")

    # ---- Phase 2：基线 / 对比 / 质量门 / 归因 / 豁免 ----
    p_tri = sub.add_parser("triage", help="findings 归因分级：四维归类 + 处置建议 + 修复批次")
    _add_run_ref_arg(p_tri)
    p_tri.add_argument("--format", choices=["md", "json"], default="md")

    p_base = sub.add_parser("baseline", help="把某次 run 固化为具名基线（供 diff/gate 引用）")
    _add_run_ref_arg(p_base)
    p_base.add_argument("--name", default="baseline", help="基线名（字母/数字/_/-）")

    p_diff = sub.add_parser("diff", help="当前 run 对比基线：new / fixed / existing / moved")
    _add_run_ref_arg(p_diff)
    p_diff.add_argument("--baseline", required=True, help="基线名或另一次 run 的 id/前缀")
    p_diff.add_argument("--format", choices=["md", "json"], default="md")

    p_gate = sub.add_parser(
        "gate", help="质量门验收。退出码：0 达标 / 1 未达标 / 2 工具执行错误 / 3 配置错误"
    )
    _add_run_ref_arg(p_gate)
    p_gate.add_argument(
        "--baseline", default=None, help="基线名或 run id（启用'新增 blocker/high=0'检查）"
    )
    p_gate.add_argument(
        "--target",
        choices=["A", "B"],
        default="A",
        help="A：0 blocker/0 high；B：0 blocker/≤2 high",
    )
    p_gate.add_argument("--format", choices=["md", "json"], default="md")

    p_sup = sub.add_parser(
        "suppress",
        help="豁免某条 finding（必须写理由）；或 --reapply 按签名把存量豁免对齐到最新 run",
    )
    _add_run_ref_arg(p_sup)
    p_sup.add_argument("--finding", default=None, help="finding id（≥8 位前缀即可）")
    p_sup.add_argument("--action", choices=["false_positive", "debt_accepted"], default=None)
    p_sup.add_argument(
        "--reason", default=None, help="豁免理由（必填，进报告与 .cq/suppressions.json）"
    )
    p_sup.add_argument(
        "--reapply",
        action="store_true",
        help="不新增豁免，把存量条目按签名重新对齐到 --run（大改/全量格式化后 id 失配时用）",
    )

    p_fix = sub.add_parser(
        "fix",
        help="分批修复计划（默认 dry-run 只出计划；--apply 需显式批准且一次一批，改完必须跑测试）",
    )
    _add_run_ref_arg(p_fix)
    p_fix.add_argument(
        "--batch", type=int, default=None, help="批次号（--apply 时必填，一次只应用一批）"
    )
    p_fix.add_argument(
        "--apply",
        action="store_true",
        help="真正执行该批次的自动修复命令（用户显式批准的载体；仅对'自动修复'批次开放）",
    )
    p_fix.add_argument("--format", choices=["md", "json"], default="md")

    # ---- spec-drift（一期：spec-gate / waiver；仅项目配置 .specdrift.toml 后生效）----
    p_sg = sub.add_parser(
        "spec-gate",
        help="spec 防漂移门：检查本次变更的代码是否在活跃 proposal 中声明（需 .specdrift.toml）",
    )
    p_sg.add_argument("--project", default=".", help="项目路径（默认当前目录）")
    p_sg.add_argument(
        "--mode",
        choices=["soft", "hard"],
        default="soft",
        help="soft=只警告（exit 0）；hard=漂移则退出码 1",
    )
    p_sg.add_argument(
        "--paths",
        default=None,
        help="受 spec 管辖的路径（逗号分隔，覆盖 .specdrift.toml 的 code_paths）",
    )

    p_wv = sub.add_parser(
        "waiver",
        help="spec-drift 有期限豁免：decisions/ 生成 decision 文件并计入 suppressions.json 债务账本",
    )
    p_wv.add_argument("--project", default=".", help="项目路径（默认当前目录）")
    p_wv.add_argument(
        "--reason", required=True, help="豁免理由（必填，如'先写代码，spec 周五前补齐'）"
    )
    p_wv.add_argument("--ttl", required=True, help="期限（如 30m / 12h / 7d）")
    p_wv.add_argument("--change", default="", help="关联的 change id（可选）")
    return parser


def _add_run_ref_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", default=".", help="项目路径（默认当前目录）")
    parser.add_argument("--run", required=True, help="run id（支持唯一前缀）")


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
    if args.command == "doctor":
        return _run_doctor_command(args)
    if args.command == "triage":
        return _run_triage_command(args)
    if args.command == "baseline":
        return _run_baseline_command(args)
    if args.command == "diff":
        return _run_diff_command(args)
    if args.command == "gate":
        return _run_gate_command(args)
    if args.command == "suppress":
        return _run_suppress_command(args)
    if args.command == "fix":
        return _run_fix_command(args)
    if args.command == "spec-gate":
        return _run_spec_gate_command(args)
    if args.command == "waiver":
        return _run_waiver_command(args)
    if args.command != "scan":
        print(f"错误：未知子命令 {args.command}", file=sys.stderr)
        return 1
    return _run_scan_command(args)


def _run_spec_gate_command(args: argparse.Namespace) -> int:
    from .specdrift import SpecDriftError, render_spec_gate, run_spec_gate

    project = Path(args.project)
    if not project.is_dir():
        print(f"错误：项目目录不存在: {project}", file=sys.stderr)
        return 3
    paths = [p.strip() for p in args.paths.split(",") if p.strip()] if args.paths else None
    try:
        res = run_spec_gate(project, mode=args.mode, paths_override=paths)
    except SpecDriftError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 3
    print(render_spec_gate(res))
    return res.exit_code


def _run_waiver_command(args: argparse.Namespace) -> int:
    from .specdrift import SpecDriftError, create_waiver

    project = Path(args.project)
    if not project.is_dir():
        print(f"错误：项目目录不存在: {project}", file=sys.stderr)
        return 3
    try:
        path, entry = create_waiver(
            project, reason=args.reason, ttl=args.ttl, change_id=args.change
        )
    except SpecDriftError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 3
    print(f"waiver 已生成：{path}")
    print(f"  截止：{entry['expires_at']}（过期后 spec-gate --mode=hard 视为漂移）")
    print("  已计入债务账本：.cq/suppressions.json（kind=spec_waiver）")
    print("  spec 补齐后建议删除该 decision 文件，或让它自然过期。")
    return 0


def _run_fix_command(args: argparse.Namespace) -> int:
    import json as _json

    from .config import Config
    from .fix import FixError, apply_batch, build_fix_plan, render_console, save_plan

    project = Path(args.project)
    if not project.is_dir():
        print(f"错误：项目目录不存在: {project}", file=sys.stderr)
        return 3
    run = _load_run_or_error(project, args.run)
    if run is None:
        return 3
    if args.apply and args.batch is None:
        print("错误：--apply 必须配合 --batch <批次号>（一次只应用一个批次）", file=sys.stderr)
        return 3

    cfg = Config.load(project)
    batches = build_fix_plan(run, cfg.python)
    plan_path = save_plan(run.run_dir, batches)

    if args.format == "json":
        print(
            _json.dumps(
                {"run_id": run.run_id, "batches": [b.to_dict() for b in batches]},
                ensure_ascii=False,
                indent=2,
            )
        )
        if not args.apply:
            return 0

    if not args.apply:
        print(render_console(run.run_id, batches, plan_path))
        return 0

    try:
        cmds, output = apply_batch(project, batches, args.batch, timeout=cfg.timeout_for("fix"))
    except FixError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 3
    print(f"已应用批次 {args.batch}（{len(cmds)} 条命令）：")
    for c in cmds:
        print(f"  $ {c}")
    print(output)
    print()
    print(f"计划/记录：{plan_path}")
    print("接下来必做（不会自动执行，更不会 commit）：")
    print("  1. 跑测试（确认自动修复没有破坏行为）")
    print("  2. cq scan --project <项目> 复扫")
    print("  3. cq diff --run <新run> --baseline <基线> 确认 fixed、无新增")
    print("  4. cq gate --run <新run> --baseline <基线> 验收")
    return 0


def _load_run_or_error(project: Path, run_ref: str):
    from .runs import load_run

    try:
        return load_run(project, run_ref)
    except LookupError as e:
        print(f"错误：{e}", file=sys.stderr)
        return None


def _run_triage_command(args: argparse.Namespace) -> int:
    from .triage import run_triage

    project = Path(args.project)
    if not project.is_dir():
        print(f"错误：项目目录不存在: {project}", file=sys.stderr)
        return 3
    try:
        code, text = run_triage(project, args.run, fmt=args.format)
    except LookupError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 3
    print(text)
    return code


def _run_baseline_command(args: argparse.Namespace) -> int:
    from .baseline import save_baseline

    project = Path(args.project)
    try:
        path, run = save_baseline(project, args.run, args.name)
    except (LookupError, ValueError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 3
    total = len(run.findings)
    print(f"基线已保存：{path}")
    print(f"  来源 run：{run.run_id}（{total} 条 findings）")
    print(
        f"  后续用法：cq diff --run <新run> --baseline {args.name} / cq gate --run <新run> --baseline {args.name}"
    )
    return 0


def _run_diff_command(args: argparse.Namespace) -> int:
    import json as _json

    from .baseline import load_baseline
    from .diff import diff_findings

    project = Path(args.project)
    run = _load_run_or_error(project, args.run)
    if run is None:
        return 3
    try:
        base_ref, base = load_baseline(project, args.baseline)
    except (LookupError, ValueError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 3

    result = diff_findings(base.findings, run.findings)
    payload = {
        "run_id": run.run_id,
        "baseline": base_ref,
        "baseline_run": base.run_id,
        **result.summary_counts(),
        "new_details": [
            {k: f.get(k) for k in ("id", "severity", "source", "rule", "file", "line", "message")}
            for f in result.new
        ],
        "fixed_details": [
            {k: f.get(k) for k in ("id", "severity", "source", "rule", "file", "line", "message")}
            for f in result.fixed
        ],
        "moved": result.moved,
    }
    out_path = run.run_dir / "diff.json"
    out_path.write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.format == "json":
        print(_json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        counts = result.summary_counts()
        print(f"diff · {run.run_id} vs {base_ref}（{base.run_id}）")
        print()
        print(
            f"  新增 {counts['new']}（其中 blocker/high {len(result.new_blocker_high)}）· "
            f"已修复 {counts['fixed']} · 仍存在 {counts['existing']}"
            f"（行号漂移 {counts['moved']}）· 豁免 {counts['suppressed']}"
        )
        for f in result.new[:20]:
            print(
                f"  + [{f.get('severity')}] {f.get('source')}:{f.get('rule')} {f.get('file')}:{f.get('line')} {f.get('message')}"
            )
        for f in result.fixed[:20]:
            print(
                f"  - [{f.get('severity')}] {f.get('source')}:{f.get('rule')} {f.get('file')}:{f.get('line')} {f.get('message')}"
            )
        print()
        print(f"  产物：{out_path}")
    # 1 = 有新增 blocker/high（与 gate 语义一致），供脚本直接判断
    return 1 if result.new_blocker_high else 0


def _run_gate_command(args: argparse.Namespace) -> int:
    import json as _json

    from .gate import render_console, run_gate

    project = Path(args.project)
    try:
        report = run_gate(project, args.run, baseline_ref=args.baseline, target=args.target)
    except LookupError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 3
    except ValueError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 3

    out_path = _load_run_or_error(project, args.run)
    gate_path = out_path.run_dir / "gate.json" if out_path else None
    if gate_path:
        gate_path.write_text(
            _json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if args.format == "json":
        print(_json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_console(report))
        if gate_path:
            print(f"\n  产物：{gate_path}")
    return report.exit_code


def _run_suppress_command(args: argparse.Namespace) -> int:
    from . import suppress as suppress_mod

    project = Path(args.project)
    run = _load_run_or_error(project, args.run)
    if run is None:
        return 3

    if getattr(args, "reapply", False):
        stats = suppress_mod.reapply(project, run.findings)
        print(
            f"已按签名对齐存量豁免：命中 {stats['matched']}（其中刷新 id {stats['refreshed']}），"
            f"未命中 {stats['unmatched']}（保留原样，可能确已修复）"
        )
        print(f"  文件：{suppress_mod.suppressions_file(project)}")
        return 0

    if not (args.finding and args.action and args.reason):
        print(
            "错误：新增豁免需要 --finding/--action/--reason；如为存量条目对齐，用 --reapply",
            file=sys.stderr,
        )
        return 3

    try:
        fid = suppress_mod.resolve_finding_id(run.findings, args.finding)
        hit = next(f for f in run.findings if str(f.get("id")) == fid)
        sup, path = suppress_mod.add(
            project,
            finding_id=fid,
            action=args.action,
            reason=args.reason,
            source_run=run.run_id,
            # 行号无关签名：全量格式化/大改后行号漂移，靠它保住豁免不失配
            sig=(
                str(hit.get("source") or ""),
                str(hit.get("rule") or ""),
                str(hit.get("file") or ""),
                str(hit.get("message") or ""),
            ),
        )
    except suppress_mod.SuppressionError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 3
    print(f"已豁免并写入 {path}")
    print(
        f"  [{sup.action}] {hit.get('source')}:{hit.get('rule')} {hit.get('file')}:{hit.get('line')}"
    )
    print(f"  理由：{sup.reason}")
    print("  下次 scan 起 status=suppressed，diff/gate 不再追究；如需立即生效请重新 scan。")
    return 0


def _run_doctor_command(args: argparse.Namespace) -> int:
    from .config import Config
    from .doctor import doctor_summary, render_console, run_doctor

    overrides: dict[str, object] = {}
    if getattr(args, "python", None):
        overrides["python"] = args.python
    if getattr(args, "deepsec_home", None):
        overrides["deepsec_home"] = args.deepsec_home
    if getattr(args, "deepsec_python", None):
        overrides["deepsec_python"] = args.deepsec_python
    cfg = Config.load(Path("."), cli_overrides=overrides)
    checks, python_note = run_doctor(cfg)
    summary = doctor_summary(checks)
    summary["cq_version"] = __version__
    summary["python"] = cfg.python
    if python_note:
        summary["python_note"] = python_note
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render_console(cfg.python, summary))
        if python_note:
            print(f"\n说明：{python_note}")
    # 0=全部就绪；2=有缺失（不阻塞 scan，但相应维度无结论）
    return 0 if summary["all_available"] else 2


def _run_scan_command(args: argparse.Namespace) -> int:
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
        _print_scan_console(out)
    return out.exit_code


def _print_scan_console(out: ScanOutput) -> None:
    from .report import build_console_output

    print(build_console_output(out.summary, out.findings))
    if out.exit_code == 2:
        print(
            "\n注意：有工具执行出错/超时（status=error/timeout），上述结果不完整。", file=sys.stderr
        )
    runnable = [r for r in out.results if r.status == "ok"]
    if out.results and not runnable:
        print(
            "\n警告：所有工具都 skipped，本次扫描没有产生任何有效结论。"
            "请检查工具安装或 --python 指向的解释器。",
            file=sys.stderr,
        )


if __name__ == "__main__":
    raise SystemExit(main())
