"""豁免（suppression）机制：把确认误报/暂不修的 finding 标记掉，diff/gate 不再追究。

硬规矩（交接方案 §7 hard-rules）：suppression 必须写理由，不能直接关规则。
存储：<项目>/.cq/suppressions.json（可入版本库，评审时理由可见）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SUPPRESSIONS_REL = Path(".cq") / "suppressions.json"
ACTIONS = ("false_positive", "debt_accepted")
MIN_ID_PREFIX = 8


class SuppressionError(ValueError):
    """豁免操作非法（缺理由、action 非法、id 找不到/歧义）。"""


@dataclass
class Suppression:
    id: str
    action: str  # false_positive | debt_accepted
    reason: str
    created_at: str
    source_run: str  # 标记时参考的 run
    # 行号无关签名：id 含行号（sha1 里有 line），大改/全量格式化后行号漂移会使 id 失配；
    # 扫描时按 id 或签名（source|rule|file|message）双通道匹配，签名命中即刷新 id。
    sig_source: str = ""
    sig_rule: str = ""
    sig_file: str = ""
    sig_message: str = ""

    def to_dict(self) -> dict[str, str]:
        d = {
            "id": self.id,
            "action": self.action,
            "reason": self.reason,
            "created_at": self.created_at,
            "source_run": self.source_run,
        }
        if self.sig_message or self.sig_rule:
            d.update(
                {
                    "sig_source": self.sig_source,
                    "sig_rule": self.sig_rule,
                    "sig_file": self.sig_file,
                    "sig_message": self.sig_message,
                }
            )
        return d


def _sig_of(finding: dict[str, object]) -> tuple[str, str, str, str]:
    return (
        str(finding.get("source") or ""),
        str(finding.get("rule") or ""),
        str(finding.get("file") or ""),
        str(finding.get("message") or ""),
    )


def _entry_sig(item: dict[str, str]) -> tuple[str, str, str, str] | None:
    """条目里的签名；旧版条目（无 sig 字段）返回 None → 只按 id 精确匹配。"""
    if not item.get("sig_rule") and not item.get("sig_message"):
        return None
    return (
        str(item.get("sig_source") or ""),
        str(item.get("sig_rule") or ""),
        str(item.get("sig_file") or ""),
        str(item.get("sig_message") or ""),
    )


def suppression_ids(project_root: Path) -> tuple[set[str], set[tuple[str, str, str, str]]]:
    """扫描时用的两组匹配键：（精确 id 集, 签名集）。"""
    items = load(project_root)
    ids = {str(x.get("id")) for x in items}
    sigs = {s for x in items if (s := _entry_sig(x)) is not None}
    return ids, sigs


def resolve_finding_id(run_findings: list[dict[str, object]], id_prefix: str) -> str:
    """把用户给的 id 前缀解析成完整 finding id；找不到/歧义报错。"""
    if len(id_prefix) < MIN_ID_PREFIX:
        raise SuppressionError(f"finding id 前缀至少 {MIN_ID_PREFIX} 位（收到 {id_prefix!r}）")
    hits = [f for f in run_findings if str(f.get("id", "")).startswith(id_prefix)]
    if not hits:
        raise SuppressionError(f"run 里找不到 id 前缀 {id_prefix!r} 对应的 finding")
    if len(hits) > 1:
        raise SuppressionError(f"id 前缀 {id_prefix!r} 命中 {len(hits)} 条 finding，请用更长的前缀")
    return str(hits[0]["id"])


def suppressions_file(project_root: Path) -> Path:
    return project_root / SUPPRESSIONS_REL


def load(project_root: Path) -> list[dict[str, str]]:
    path = suppressions_file(project_root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("suppressions") if isinstance(data, dict) else data
    return (
        [x for x in items if isinstance(x, dict) and x.get("id")] if isinstance(items, list) else []
    )


def save(project_root: Path, items: list[dict[str, str]]) -> Path:
    path = suppressions_file(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"suppressions": items}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def add(
    project_root: Path,
    *,
    finding_id: str,
    action: str,
    reason: str,
    source_run: str,
    sig: tuple[str, str, str, str] | None = None,
) -> tuple[Suppression, Path]:
    if action not in ACTIONS:
        raise SuppressionError(f"action 必须是 {' / '.join(ACTIONS)}，收到 {action!r}")
    if not reason or not reason.strip():
        raise SuppressionError("必须写理由（--reason）：suppression 不能没有理由")
    items = [x for x in load(project_root) if x.get("id") != finding_id]
    sup = Suppression(
        id=finding_id,
        action=action,
        reason=reason.strip(),
        created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        source_run=source_run,
    )
    if sig is not None:
        sup.sig_source, sup.sig_rule, sup.sig_file, sup.sig_message = sig
    items.append(sup.to_dict())
    return sup, save(project_root, items)


def reapply(project_root: Path, run_findings: list[dict[str, object]]) -> dict[str, int]:
    """把存量豁免条目按签名重新对齐到最新 run：命中则刷新 id（旧版无签名条目只能按 id）。

    返回 {matched, unmatched, refreshed}。unmatched 条目保留不动（可能确已修复）。"""
    items = load(project_root)
    by_id = {str(f.get("id")): f for f in run_findings}
    by_sig: dict[tuple[str, str, str, str], list[dict[str, object]]] = {}
    for f in run_findings:
        by_sig.setdefault(_sig_of(f), []).append(f)

    matched = unmatched = refreshed = 0
    for item in items:
        f: dict[str, object] | None = by_id.get(str(item.get("id")))
        if f is None:
            sig = _entry_sig(item)
            cands = by_sig.get(sig, []) if sig else []
            if cands:
                f = cands.pop(0)  # 同签名多条时配第一条，消耗掉防止重复配
                matched += 1
                refreshed += 1
                item["id"] = str(f["id"])
                item["sig_source"], item["sig_rule"], item["sig_file"], item["sig_message"] = sig  # type: ignore[assignment]
                continue
            unmatched += 1
            continue
        matched += 1
        sig = _sig_of(f)
        if _entry_sig(item) != sig:
            refreshed += 1
        item["sig_source"], item["sig_rule"], item["sig_file"], item["sig_message"] = sig
    save(project_root, items)
    return {"matched": matched, "unmatched": unmatched, "refreshed": refreshed}
