"""cq spec-drift（一期）：spec 驱动项目的防漂移门 + 有期限豁免。

设计依据：需求单 docs（2026-10-12 版，含第七节对齐决策）。
- `cq spec-gate --mode=soft|hard [--paths a,b]`：检查本次变更的代码相对活跃 proposal 是否声明；
- `cq waiver --reason=… --ttl=24h`：生成有期限豁免（decisions/ + suppressions.json kind=spec_waiver）。

兼容性红线（需求单第四节）：
- 默认关闭：项目根无 `.specdrift.toml` 时，spec-gate 只提示未启用并退出 0，scan/gate 完全不感知；
- 账本复用：waiver 进 `.cq/suppressions.json`，条目带 kind；既有 finding 逻辑互不干扰。

覆盖判定（变更粒度 delta 校验的落地口径）：
活跃 change = change_root 下含 proposal.md 且其 spec_ref 指向 spec_root 中存在的 spec，
且 change 内含 ADDED/MODIFIED/REMOVED 标记。变更文件 F 视为"已声明"当且仅当：
存在活跃 change 的文本（proposal.md 及该 change 目录下全部 md）引用了 F 的相对路径
或 F 的父目录相对路径。时间戳比较仅作软提示（soft_timestamp_check）。
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

SPECDRIFT_CONFIG = ".specdrift.toml"


class SpecDriftError(ValueError):
    """spec-drift 操作非法（配置损坏、ttl 格式错误等）。"""


# ---------------- 配置 ----------------


@dataclass
class SpecDriftConfig:
    code_paths: list[str] = field(default_factory=list)
    spec_root: str = "openspec/specs"
    change_root: str = "openspec/changes"
    waiver_dir: str = "decisions"
    soft_timestamp_check: bool = True
    promote_window: str = "30d"  # promote 候选统计窗口（parse_ttl 格式）
    promote_threshold: int = 3  # 窗口内出现次数 ≥ 此值才出候选


def load_specdrift_config(project_root: Path) -> SpecDriftConfig | None:
    """读项目根 .specdrift.toml；不存在返回 None（= 未启用，红线 1）。"""
    p = project_root / SPECDRIFT_CONFIG
    if not p.is_file():
        return None
    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SpecDriftError(f"{SPECDRIFT_CONFIG} 解析失败: {e}") from e
    cfg = SpecDriftConfig()
    section = data.get("specdrift", data)  # 顶层键或 [specdrift] 节都接受
    if not isinstance(section, dict):
        raise SpecDriftError(f"{SPECDRIFT_CONFIG} 结构非法")
    cfg.code_paths = [str(x) for x in section.get("code_paths", []) if str(x).strip()]
    cfg.spec_root = str(section.get("spec_root", cfg.spec_root))
    cfg.change_root = str(section.get("change_root", cfg.change_root))
    cfg.waiver_dir = str(section.get("waiver_dir", cfg.waiver_dir))
    cfg.soft_timestamp_check = bool(section.get("soft_timestamp_check", True))
    cfg.promote_window = str(section.get("promote_window", cfg.promote_window))
    cfg.promote_threshold = int(section.get("promote_threshold", cfg.promote_threshold))
    return cfg


# ---------------- 变更文件收集 ----------------


def parse_porcelain(text: str) -> list[str]:
    """git status --porcelain → 相对路径列表（去掉状态列，处理重命名的 -> 形态）。"""
    out: list[str] = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        path = ln[3:].strip().strip('"')
        if "->" in path:
            path = path.split("->", 1)[1].strip()
        out.append(path)
    return out


def collect_changed_files(
    project_root: Path, *, base: str | None = None, range_: str | None = None
) -> list[str]:
    """变更集合的三种来源（Issue #1：CI 干净 checkout 下工作区恒空，需要修订区间）：

    - 无参（v0.3.0 行为）：`git status --porcelain`，工作区 vs HEAD（本地用法）；
    - base=<ref>：`git diff --name-only <ref>...HEAD`（三点 = merge-base，CI 门标准口径）；
    - range=<A..B>：`git diff --name-only <A..B>` 显式区间（PR 场景）。
    git 失败（ref 不存在等）抛 SpecDriftError。"""
    from .runner import run_cmd

    if base and range_:
        raise SpecDriftError("--base 与 --range 互斥，只能用一个")
    if range_:
        spec = ["diff", "--name-only", range_]
    elif base:
        spec = ["diff", "--name-only", f"{base}...HEAD"]
    else:
        # -uall：展开未跟踪目录（否则新文件的变更被折叠成 "dir/"，无法逐文件匹配）
        r = run_cmd(["git", "-C", str(project_root), "status", "--porcelain", "-uall"], timeout=30)
        if r.returncode != 0:
            return []  # 非 git 仓库：无变更可判（spec-gate 会按无变更处理）
        return parse_porcelain(r.stdout)

    r = run_cmd(["git", "-C", str(project_root), *spec], timeout=60)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip()[:200]
        raise SpecDriftError(f"git {' '.join(spec[3:])} 失败（ref 是否存在？）: {detail}")
    out: list[str] = []
    for ln in r.stdout.splitlines():
        p = ln.strip().strip('"')
        if p:
            out.append(p)
    return out


def _under_any(path: str, prefixes: list[str]) -> bool:
    p = path.replace("\\", "/").rstrip("/")
    for pre in prefixes:
        pre = pre.replace("\\", "/").rstrip("/")
        if p == pre or p.startswith(pre + "/"):
            return True
    return False


# ---------------- 活跃 proposal 解析 ----------------


_SPEC_REF = re.compile(r"spec_ref:\s*[\"']?([^\s\"']+)")
_DELTA_MARK = re.compile(r"^#{1,3}\s*(ADDED|MODIFIED|REMOVED)\b", re.MULTILINE)


@dataclass
class ActiveChange:
    name: str
    spec_ref: str
    spec_exists: bool
    has_delta: bool
    text: str  # proposal.md + 该 change 目录下全部 md 的拼接（用于路径引用匹配）


def _parse_change_dir(project_root: Path, cfg: SpecDriftConfig, d: Path) -> ActiveChange | None:
    """proposal.md + spec_ref + delta 标记 → ActiveChange；不合规返回 None。"""
    proposal = d / "proposal.md"
    if not proposal.is_file():
        return None
    proposal_text = proposal.read_text(encoding="utf-8", errors="replace")
    m = _SPEC_REF.search(proposal_text)
    if not m:
        return None
    spec_ref = m.group(1)
    spec_exists = (project_root / cfg.spec_root / spec_ref).exists() or (
        project_root / cfg.spec_root / (spec_ref + ".md")
    ).exists()
    chunks = [proposal_text]
    for md in sorted(d.rglob("*.md")):
        if md != proposal:
            chunks.append(md.read_text(encoding="utf-8", errors="replace"))
    text = "\n".join(chunks)
    return ActiveChange(
        name=d.name,
        spec_ref=spec_ref,
        spec_exists=spec_exists,
        has_delta=bool(_DELTA_MARK.search(text)),
        text=text,
    )


def load_active_changes(project_root: Path, cfg: SpecDriftConfig) -> list[ActiveChange]:
    root = project_root / cfg.change_root
    if not root.is_dir():
        return []
    out: list[ActiveChange] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith((".", "archive")):
            continue
        ch = _parse_change_dir(project_root, cfg, d)
        if ch is not None:
            out.append(ch)
    return out


def load_archived_changes_in_range(
    project_root: Path, cfg: SpecDriftConfig, diff_paths: set[str]
) -> list[ActiveChange]:
    """区间感知（Issue #2）：归档变更仅在"其目录文件出现在本次检查区间 diff 中"时
    参与声明匹配——支持"实施 → 归档 → 提交推送"流程，同时不让久远归档永久白名单。

    diff_paths = 本次区间的全部变更路径（git diff --name-only，含移动产生的新路径）。"""
    root = project_root / cfg.change_root
    if not root.is_dir():
        return []
    out: list[ActiveChange] = []
    for proposal in sorted(root.rglob("proposal.md")):
        d = proposal.parent
        parts = d.relative_to(root).as_posix().split("/")
        if "archive" not in parts:
            continue  # 活跃变更由 load_active_changes 处理
        prefix = d.relative_to(project_root).as_posix() + "/"
        if not any(p.startswith(prefix) for p in diff_paths):
            continue  # 不在区间内：不参与（防永久白名单）
        ch = _parse_change_dir(project_root, cfg, d)
        if ch is not None:
            out.append(ch)
    return out


def _declared(file_rel: str, changes: list[ActiveChange]) -> ActiveChange | None:
    """变更文件被某活跃 change 声明的判定：

    ① 文件级：change 文本按词边界出现该文件相对路径（如 `app/auth.py`）→ 只覆盖该文件；
    ② 目录级：文本显式以目录形态引用某父目录（`app/` 后不跟路径字符，
       如"改造 app/ 模块"）→ 覆盖其下全部文件。
    区分两种形态是为了防止"文本里写了 app/auth.py"被误当成"引用了整个 app/ 目录"
    （否则同目录的无关变更全部被静默放行）。"""
    rel = file_rel.replace("\\", "/")
    parts = rel.split("/")
    file_re = re.compile(rf"(?<![\w/.-]){re.escape(rel)}(?![\w/.-])")
    dir_res = [
        re.compile(rf"(?<![\w/.-]){re.escape('/'.join(parts[:i]))}/(?![\w.-])")
        for i in range(1, len(parts))
    ]
    for c in changes:
        if file_re.search(c.text):
            return c
        for dr in dir_res:
            if dr.search(c.text):
                return c
    return None


# ---------------- waiver（有期限豁免） ----------------

_TTL_RE = re.compile(r"^(\d+)\s*([smhd])$", re.IGNORECASE)
_TTL_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_ttl(ttl: str) -> timedelta:
    m = _TTL_RE.match(ttl.strip())
    if not m:
        raise SpecDriftError(f"ttl 格式非法：{ttl!r}（支持如 30m / 12h / 7d）")
    n, unit = int(m.group(1)), m.group(2).lower()
    return timedelta(seconds=n * _TTL_UNITS[unit])


def slugify(reason: str, max_len: int = 40) -> str:
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", reason.strip()).strip("-")
    if not s:
        s = "waiver"
    return s[:max_len].rstrip("-")


def active_waivers(
    project_root: Path, now: datetime | None = None
) -> tuple[list[dict], list[dict]]:
    """读 suppressions.json 里的 spec_waiver 条目 → (生效中, 已过期)。"""
    from . import suppress as suppress_mod

    now = _aware(now) if now is not None else datetime.now().astimezone()
    items = suppress_mod.load(project_root)
    active: list[dict] = []
    expired: list[dict] = []
    for it in items:
        if it.get("kind") != "spec_waiver":
            continue
        exp = it.get("expires_at") or ""
        try:
            deadline = datetime.fromisoformat(exp)
        except ValueError:
            expired.append(it)
            continue
        (active if deadline > now else expired).append(it)
    return active, expired


def create_waiver(
    project_root: Path,
    *,
    reason: str,
    ttl: str,
    change_id: str = "",
    cfg: SpecDriftConfig | None = None,
    now: datetime | None = None,
) -> tuple[Path, dict]:
    """生成 decision 文件 + 记入 suppressions.json（kind=spec_waiver）。"""
    from . import suppress as suppress_mod

    if not reason.strip():
        raise SpecDriftError("必须写 --reason")
    cfg = cfg or (load_specdrift_config(project_root) or SpecDriftConfig())
    now = _aware(now) if now is not None else datetime.now().astimezone()
    deadline = now + parse_ttl(ttl)
    day = now.strftime("%Y-%m-%d")
    slug = slugify(reason)
    wdir = project_root / cfg.waiver_dir
    wdir.mkdir(parents=True, exist_ok=True)
    path = wdir / f"{day}-{slug}.md"
    n = 1
    while path.exists():  # 同日同 slug 防覆盖
        n += 1
        path = wdir / f"{day}-{slug}-{n}.md"

    content = (
        f"# Waiver: {reason.strip()}\n\n"
        f"- created_at: {now.isoformat(timespec='seconds')}\n"
        f"- deadline: {deadline.isoformat(timespec='seconds')}\n"
        f"- change_id: {change_id or '(none)'}\n"
        f"- kind: spec_waiver\n\n"
        "超时未归档（spec 补齐后请删除本条或让它过期）将被 `cq spec-gate --mode=hard` 视为漂移。\n"
    )
    path.write_text(content, encoding="utf-8")

    items = suppress_mod.load(project_root)
    entry = {
        # id 仅用于让条目通过账本 load() 的合法性过滤并便于唯一引用；
        # spec_waiver 的匹配靠 kind/expires_at，与 finding 的 id/sha1 语义无关
        "id": f"spec-waiver-{now.strftime('%Y%m%d%H%M%S')}-{slug}",
        "kind": "spec_waiver",
        "reason": reason.strip(),
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": deadline.isoformat(timespec="seconds"),
        "change_id": change_id or "",
        "decision_file": str(path.relative_to(project_root)),
    }
    items.append(entry)
    suppress_mod.save(project_root, items)
    return path, entry


# ---------------- spec-gate 主判定 ----------------


@dataclass
class SpecGateResult:
    mode: str
    configured: bool
    changed_files: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)
    waived: list[str] = field(default_factory=list)
    covered_by: dict[str, str] = field(default_factory=dict)  # file -> change name
    expired_waivers: list[dict] = field(default_factory=list)
    soft_hints: list[str] = field(default_factory=list)
    exit_code: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def _resolve_changed(
    root: Path,
    changed_files: list[str] | None,
    base: str | None,
    range_: str | None,
    mode: str,
) -> tuple[list[str], SpecGateResult | None]:
    """变更来源解析：显式注入 > base/range 区间 > 工作区。

    区间内零变更时返回 early 结果（SPEC_OK + 软提示），供调用方直接返回。"""
    if changed_files is not None:
        return changed_files, None
    if base or range_:
        files = collect_changed_files(root, base=base, range_=range_)
        if not files:
            res = SpecGateResult(mode=mode, configured=True, exit_code=0)
            res.soft_hints = [f"区间内（{base or range_}）受管路径无变更"]
            return [], res
        return files, None
    return collect_changed_files(root), None


def run_spec_gate(
    project_root: Path,
    *,
    mode: str = "soft",
    paths_override: list[str] | None = None,
    cfg: SpecDriftConfig | None = None,
    changed_files: list[str] | None = None,
    base: str | None = None,
    range_: str | None = None,
    now: datetime | None = None,
) -> SpecGateResult:
    root = project_root.resolve()
    if cfg is None:
        cfg = load_specdrift_config(root)
    if cfg is None:
        return _unconfigured_result(mode)
    _validate_mode(mode)

    code_paths = [p.strip() for p in (paths_override or cfg.code_paths) if p.strip()]
    if not code_paths:
        return _empty_paths_result(mode)

    # 变更来源优先级：显式注入（测试）> base/range 区间（CI/PR）> 工作区（v0.3.0 默认）
    files, early = _resolve_changed(root, changed_files, base, range_, mode)
    if early is not None:
        return early
    return _evaluate_gate(root, cfg, mode, code_paths, files, base, range_, now)


def _unconfigured_result(mode: str) -> SpecGateResult:
    return SpecGateResult(
        mode=mode,
        configured=False,
        exit_code=0,
        soft_hints=[f"未找到 {SPECDRIFT_CONFIG}，spec-drift 未启用（未配置的项目不受影响）"],
    )


def _empty_paths_result(mode: str) -> SpecGateResult:
    return SpecGateResult(
        mode=mode,
        configured=True,
        exit_code=0,
        soft_hints=["code_paths 为空（.specdrift.toml 或 --paths 均未提供），不检查"],
    )


def _validate_mode(mode: str) -> None:
    if mode not in ("soft", "hard"):
        raise SpecDriftError(f"mode 只支持 soft|hard，收到 {mode!r}")


def _evaluate_gate(
    root: Path,
    cfg: SpecDriftConfig,
    mode: str,
    code_paths: list[str],
    files: list[str],
    base: str | None,
    range_: str | None,
    now: datetime | None,
) -> SpecGateResult:
    changed_in_scope = [f for f in files if _under_any(f, code_paths)]
    changes = load_active_changes(root, cfg)
    if base or range_:
        # 区间感知（Issue #2）：本次区间内被归档的变更仍参与声明匹配，
        # 否则"实施→归档→推送"的提交在 CI 会被误报；区间外的旧归档不白名单
        changes += load_archived_changes_in_range(root, cfg, set(files))
    viable = [c for c in changes if c.spec_exists and c.has_delta]
    current_time = _aware(now) if now is not None else datetime.now().astimezone()
    actives, expired = active_waivers(root, current_time)
    waived_by_expiry = len(expired) > 0

    res = SpecGateResult(
        mode=mode, configured=True, changed_files=changed_in_scope, expired_waivers=expired
    )
    waiver_active = bool(actives) and not waived_by_expiry
    _classify(res, changed_in_scope, viable, waiver_active)
    if cfg.soft_timestamp_check:
        _timestamp_hint(root, cfg, changed_in_scope, res.soft_hints)

    if res.drift and mode == "hard":
        res.exit_code = 1
    return res


def _classify(
    res: SpecGateResult,
    changed_in_scope: list[str],
    viable: list[ActiveChange],
    waiver_active: bool,
) -> None:
    """逐文件分类：被 proposal 声明 → covered；waiver 生效 → waived；否则 drift。"""
    for f in changed_in_scope:
        ch = _declared(f, viable)
        if ch is not None:
            res.covered_by[f] = ch.name
        elif waiver_active:
            res.waived.append(f)
        else:
            res.drift.append(f)


def _timestamp_hint(
    root: Path, cfg: SpecDriftConfig, changed_in_scope: list[str], hints: list[str]
) -> None:
    """时间戳软提示：变更文件晚于全部 spec 文件的最大 mtime → 提示（不构成失败）。"""
    if not changed_in_scope:
        return
    spec_dir = root / cfg.spec_root
    spec_files = list(spec_dir.rglob("*.md")) if spec_dir.is_dir() else []
    if not spec_files:
        return
    spec_newest = max(p.stat().st_mtime for p in spec_files)
    late = [
        f
        for f in changed_in_scope
        if (root / f).exists() and (root / f).stat().st_mtime > spec_newest
    ]
    if late:
        hints.append(f"软提示：{len(late)} 个变更文件晚于 spec 最新修改时间（仅提示，不构成失败）")


def render_spec_gate(res: SpecGateResult) -> str:
    lines: list[str] = []
    if not res.configured:
        return res.soft_hints[0] if res.soft_hints else "spec-drift 未启用"
    lines.append(f"spec-gate · mode={res.mode} · 变更 {len(res.changed_files)} 个受管文件")
    for hint in res.soft_hints:
        lines.append(f"  提示：{hint}")
    for f, ch in sorted(res.covered_by.items()):
        lines.append(f"  ✅ SPEC_OK: {f}（声明于 change: {ch}）")
    for f in res.waived:
        lines.append(f"  ⏭️ WAIVED: {f}（存在生效中的 waiver）")
    for it in res.expired_waivers:
        lines.append(
            f"  🔴 WAIVER_EXPIRED: {it.get('reason', '')[:50]}（deadline {it.get('expires_at', '?')}）"
        )
    for f in res.drift:
        lines.append(f"  🔴 SPEC_DRIFT: {f} 代码变动未在 spec 中声明")
    if not res.changed_files:
        lines.append("  本次无受管路径的变更")
    lines.append(
        f"结论：{'SPEC_OK' if not res.drift else f'SPEC_DRIFT_FOUND: {len(res.drift)}'}"
        + ("" if res.mode == "soft" or not res.drift else "（hard 模式，退出码 1）")
    )
    if res.drift and res.mode == "soft":
        lines.append(
            "（soft 模式仅警告；先写代码时可用 cq waiver --reason=… --ttl=24h 申请期限豁免）"
        )
    return "\n".join(lines)


def _aware(dt: datetime) -> datetime:
    """naive datetime 按本地时区补全（测试常传 naive；与账本 ISO 的 aware 统一）。"""
    return dt.astimezone() if dt.tzinfo is None else dt
