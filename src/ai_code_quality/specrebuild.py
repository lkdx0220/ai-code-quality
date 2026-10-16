"""cq spec-drift（二期）：启发式逆向对账 + 决策晋升候选。

对齐口径（需求单第七节 4/5 条）：
- spec-rebuild 定位是**线索清单**，不是真理：AST 提取 code_paths 的模块/公开符号，
  对 spec_root 全文做词边界匹配；反向用 spec 章节标题里的标识符 token 对代码符号集。
  全部结果带置信度（class/function=high、module=medium、标题反向=low）。
- `--apply` 只写骨架级 patch（带 TODO 的章节模板，不替人写 spec 内容），并重置 spec 债
  （清掉过期的 spec_waiver 条目）。
- promote：decisions/ 文件 + 账本 spec_waiver 条目按标题 slug + 关键词重合聚类，
  窗口（promote_window，默认 30d）内出现 ≥ 阈值（promote_threshold，默认 3）→ 输出候选
  与 constitution 草稿段落；**只输出，永不写文件**。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from .specdrift import (
    SpecDriftConfig,
    SpecDriftError,
    load_specdrift_config,
    parse_ttl,
    slugify,
)

# ---------------- AST 符号提取 ----------------


@dataclass
class CodeSymbol:
    name: str
    kind: str  # class | function | module
    file: str  # 相对项目根
    line: int

    @property
    def confidence(self) -> str:
        return {"class": "high", "function": "high", "module": "medium"}[self.kind]


def _iter_py(root: Path, prefixes: list[str]):
    for pre in prefixes:
        base = root / pre.rstrip("/")
        if not base.exists():
            continue
        if base.is_file() and base.suffix == ".py":
            yield base
            continue
        yield from sorted(base.rglob("*.py"))


def extract_public_symbols(
    project_root: Path, code_paths: list[str]
) -> tuple[list[CodeSymbol], list[str]]:
    """提取模块级公开类/函数 + 模块名。返回 (符号列表, 解析失败文件列表)。"""
    root = project_root.resolve()
    symbols: list[CodeSymbol] = []
    broken: list[str] = []
    for p in _iter_py(root, code_paths):
        rel = p.relative_to(root).as_posix()
        mod_name = p.stem if p.stem not in ("__init__",) else p.parent.name
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            broken.append(rel)
            continue
        symbols.append(CodeSymbol(name=mod_name, kind="module", file=rel, line=1))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if node.name.startswith("_"):
                continue
            symbols.append(
                CodeSymbol(
                    name=node.name,
                    kind=("class" if isinstance(node, ast.ClassDef) else "function"),
                    file=rel,
                    line=node.lineno,
                )
            )
    return symbols, broken


# ---------------- spec 文本索引与匹配 ----------------


def load_spec_text(project_root: Path, cfg: SpecDriftConfig) -> str:
    root = project_root / cfg.spec_root
    if not root.is_dir():
        return ""
    chunks = [p.read_text(encoding="utf-8", errors="replace") for p in sorted(root.rglob("*.md"))]
    return "\n".join(chunks)


def _word_found(name: str, text: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text, re.IGNORECASE) is not None


def _normalize_token(s: str) -> str:
    return re.sub(r"[_\-\s]+", "", s).lower()


def extract_spec_headings(project_root: Path, cfg: SpecDriftConfig) -> list[tuple[str, str]]:
    """spec_root 下全部 md 的 (文件相对路径, 标题文本)，层级 ≤ ###。"""
    root = project_root / cfg.spec_root
    out: list[tuple[str, str]] = []
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(project_root).as_posix()
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^#{1,3}\s+(.+?)\s*$", ln)
            if m:
                out.append((rel, m.group(1)))
    return out


def _heading_tokens(heading: str) -> set[str]:
    return {t for t in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", heading) if len(t) >= 3}


# ---------------- 对账 ----------------


@dataclass
class RebuildResult:
    configured: bool
    code_missing: list[CodeSymbol] = field(default_factory=list)  # 代码有、spec 无
    spec_missing: list[tuple[str, str]] = field(default_factory=list)  # spec 有、代码无（低置信度）
    covered_count: int = 0
    broken_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "code_missing": [
                {
                    "name": s.name,
                    "kind": s.kind,
                    "file": s.file,
                    "line": s.line,
                    "confidence": s.confidence,
                }
                for s in self.code_missing
            ],
            "spec_missing": [{"file": f, "heading": h} for f, h in self.spec_missing],
            "covered_count": self.covered_count,
            "broken_files": self.broken_files,
            "notes": self.notes,
            "disclaimer": "启发式对账：线索清单，非真理。置信度 class/function=high、module=medium、标题反向=low",
        }


def run_rebuild(project_root: Path, *, cfg: SpecDriftConfig | None = None) -> RebuildResult:
    root = project_root.resolve()
    if cfg is None:
        cfg = load_specdrift_config(root)
    if cfg is None:
        return RebuildResult(configured=False, notes=["未找到 .specdrift.toml，spec-drift 未启用"])
    if not cfg.code_paths:
        return RebuildResult(configured=True, notes=["code_paths 为空，无法对账"])

    symbols, broken = extract_public_symbols(root, cfg.code_paths)
    spec_text = load_spec_text(root, cfg)
    if not spec_text:
        return RebuildResult(
            configured=True,
            broken_files=broken,
            code_missing=symbols,
            notes=["spec_root 无任何 md 文件：全部符号视为未覆盖"],
        )

    res = RebuildResult(configured=True, broken_files=broken)
    for s in symbols:
        if _word_found(s.name, spec_text):
            res.covered_count += 1
        else:
            res.code_missing.append(s)

    # 反向：spec 章节标题的 token 在代码符号集中找不到归一化对应 → spec 有代码无（低置信度）
    symbol_norms = {_normalize_token(s.name) for s in symbols}
    seen_headings: set[str] = set()
    for f, h in extract_spec_headings(root, cfg):
        key = f"{f}::{h}"
        if key in seen_headings:
            continue
        seen_headings.add(key)
        toks = _heading_tokens(h)
        if not toks:
            continue
        # 全部 token 都无代码对应 → 疑似 spec 有代码无（中文标题整体归一后比对）
        if _normalize_token(h) in symbol_norms:
            continue
        if all(_normalize_token(t) not in symbol_norms for t in toks):
            res.spec_missing.append((f, h))
    return res


# ---------------- 骨架 patch ----------------


def build_patch_skeleton(res: RebuildResult, *, now: datetime | None = None) -> str:
    now = _aware(now or datetime.now().astimezone())
    by_file: dict[str, list[CodeSymbol]] = {}
    for s in res.code_missing:
        by_file.setdefault(s.file, []).append(s)
    lines = [
        f"# spec-rebuild 骨架（{now:%Y-%m-%d %H:%M} 生成）",
        "",
        "> 启发式对账产物：下面每个章节都是**待补写的模板**（TODO 处由人/Agent 撰写），",
        "> cq 不替你写 spec 内容。review 后用 `cq spec-rebuild --apply` 落盘。",
        "",
    ]
    for f in sorted(by_file):
        lines.append(f"## 模块：{f}")
        lines.append("")
        for s in sorted(by_file[f], key=lambda x: (x.kind, x.name)):
            lines.append(f"### {s.name}（{s.kind}，{s.file}:{s.line}，置信度 {s.confidence}）")
            lines.append("")
            lines.append("- TODO：描述该符号的行为契约（输入/输出/副作用/约束）")
            lines.append("")
    if not by_file:
        lines.append("（无「代码有、spec 无」项，未生成骨架）")
    return "\n".join(lines)


def apply_rebuild(
    project_root: Path,
    res: RebuildResult,
    cfg: SpecDriftConfig,
    *,
    skeleton: str | None = None,
    now: datetime | None = None,
) -> tuple[Path, int]:
    """写入骨架 patch 并重置 spec 债（清掉过期的 spec_waiver 条目）。

    返回（生成的 spec 文件路径, 清掉的债务条数）。"""
    from . import suppress as suppress_mod

    now = _aware(now or datetime.now().astimezone())
    if not res.code_missing:
        raise SpecDriftError("没有「代码有、spec 无」项，无需 apply")
    skeleton = skeleton if skeleton is not None else build_patch_skeleton(res, now=now)
    spec_dir = project_root / cfg.spec_root
    spec_dir.mkdir(parents=True, exist_ok=True)
    out = spec_dir / f"_rebuild-{now:%Y%m%d-%H%M%S}.md"
    out.write_text(skeleton, encoding="utf-8")

    # 重置 spec 债：过期 waiver 移除；未过期的保留（期限未到，不算债）
    items = suppress_mod.load(project_root)
    kept: list[dict[str, str]] = []
    removed = 0
    cur = now
    for it in items:
        if it.get("kind") == "spec_waiver":
            try:
                if datetime.fromisoformat(it.get("expires_at") or "") <= cur:
                    removed += 1
                    continue
            except ValueError:
                removed += 1
                continue
        kept.append(it)
    suppress_mod.save(project_root, kept)
    return out, removed


# ---------------- promote（候选与草稿，只读） ----------------


@dataclass
class DecisionRecord:
    source: str  # decisions 文件相对路径 或 "<ledger>"
    title: str
    date: datetime
    slug: str
    reason: str = ""


def collect_decisions(
    project_root: Path, cfg: SpecDriftConfig, *, now: datetime | None = None
) -> list[DecisionRecord]:
    """decisions/ 下的 md + 账本 spec_waiver 条目 → 决策记录。"""
    from . import suppress as suppress_mod

    now = _aware(now or datetime.now().astimezone())
    out: list[DecisionRecord] = []
    wdir = project_root / cfg.waiver_dir
    if wdir.is_dir():
        for p in sorted(wdir.glob("*.md")):
            text = p.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^#\s+Waiver:\s*(.+)$", text, re.MULTILINE)
            title = m.group(1).strip() if m else p.stem
            dm = re.match(r"^(\d{4}-\d{2}-\d{2})", p.name)
            date = (
                datetime.strptime(dm.group(1), "%Y-%m-%d").astimezone()
                if dm
                else datetime.fromtimestamp(p.stat().st_mtime, tz=now.tzinfo)
            )
            out.append(
                DecisionRecord(
                    source=p.relative_to(project_root).as_posix(),
                    title=title,
                    date=date,
                    slug=slugify(title),
                )
            )
    for it in suppress_mod.load(project_root):
        if it.get("kind") != "spec_waiver":
            continue
        reason = str(it.get("reason", ""))
        try:
            date = datetime.fromisoformat(it.get("created_at", "") or "")
        except ValueError:
            date = now
        out.append(
            DecisionRecord(
                source=str(it.get("decision_file", "<ledger>")),
                title=reason or "spec waiver",
                date=date,
                slug=slugify(reason),
                reason=reason,
            )
        )
    return out


def _aware(dt: datetime) -> datetime:
    """naive datetime 一律按本地时区补全（测试常传 naive；账本/ISO 都是 aware）。"""
    return dt.astimezone() if dt.tzinfo is None else dt


def _overlap(a: set[str], b: set[str]) -> float:
    """重叠系数 = 交集 / 较小集合。中文短标题无分词、token 少，Jaccard 天然偏低，
    重叠系数对'共享关键 token'更敏感（如「密钥轮换 每月」vs「密钥轮换 每季度」）。"""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def cluster_decisions(records: list[DecisionRecord]) -> list[list[DecisionRecord]]:
    """同类聚类：slug 相同，或标题关键词 Jaccard ≥ 0.5。贪心单遍合并。"""
    clusters: list[list[DecisionRecord]] = []
    toks = [
        {t for t in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", r.title) if len(t) >= 2}
        for r in records
    ]
    rep_indices: list[int] = []  # 每簇代表（首条）在 records 中的下标
    for i, r in enumerate(records):
        for cl, rep_idx in zip(clusters, rep_indices):
            if r.slug and r.slug == records[rep_idx].slug:
                cl.append(r)
                break
            if _overlap(toks[i], toks[rep_idx]) >= 0.5:
                cl.append(r)
                break
        else:
            clusters.append([r])
            rep_indices.append(i)
    return clusters


def promote_candidates(
    project_root: Path,
    cfg: SpecDriftConfig,
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    """窗口内出现 ≥ 阈值的同类决策 → 候选（只输出，不写任何文件）。"""
    now = _aware(now or datetime.now().astimezone())
    try:
        window = (
            parse_ttl(cfg.promote_window)
            if isinstance(cfg.promote_window, str)
            else timedelta(days=30)
        )
    except SpecDriftError:
        window = timedelta(days=30)
    records = collect_decisions(project_root, cfg, now=now)
    notes = [f"共收集 {len(records)} 条决策记录（{cfg.waiver_dir}/ + 账本 spec_waiver）"]

    cands: list[dict[str, object]] = []
    for cl in cluster_decisions(records):
        recent = [r for r in cl if now - r.date <= window]
        if len(recent) < cfg.promote_threshold:
            continue
        recent.sort(key=lambda r: r.date)
        cands.append(
            {
                "topic": recent[0].title,
                "slug": recent[0].slug,
                "count": len(recent),
                "window_days": window.days,
                "members": [
                    {"source": r.source, "date": r.date.strftime("%Y-%m-%d"), "title": r.title}
                    for r in recent
                ],
                "draft": _constitution_draft(recent),
            }
        )
    cands.sort(key=lambda c: cast("int", c["count"]), reverse=True)
    return cands, notes


def _constitution_draft(records: list[DecisionRecord]) -> str:
    topic = records[0].title
    srcs = "、".join(f"`{r.source}`" for r in records)
    return (
        f"## {topic}（拟晋升）\n\n"
        f"- TODO：把下列决策的共同规则写成不变式（constitution 条目）\n"
        f"- 依据：窗口内同类决策出现 {len(records)} 次：{srcs}\n"
    )
