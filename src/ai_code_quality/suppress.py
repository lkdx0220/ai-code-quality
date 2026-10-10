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

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "action": self.action,
            "reason": self.reason,
            "created_at": self.created_at,
            "source_run": self.source_run,
        }


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


def add(
    project_root: Path,
    *,
    finding_id: str,
    action: str,
    reason: str,
    source_run: str,
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
    items.append(sup.to_dict())
    return sup, save(project_root, items)
