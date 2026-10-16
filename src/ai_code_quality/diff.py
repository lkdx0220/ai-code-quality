"""cq diff：两次 run 的对比。

匹配分两层（行号漂移是现实问题）：
1. 精确 id（sha1(source|rule|file|line|message)）相同 → existing；
2. id 不同但 (source, rule, file, message) 相同、仅行号变化 → existing（moved），
   不算 new/fixed，避免"改一行代码导致全文件行号平移"制造假警报。
结果四分类：new / fixed / existing / suppressed（各 run 扫描时已带 status）。
基线的保存与加载见 baseline.py。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DiffResult:
    new: list[dict[str, object]] = field(default_factory=list)
    fixed: list[dict[str, object]] = field(default_factory=list)
    existing: list[dict[str, object]] = field(default_factory=list)
    moved: list[dict[str, object]] = field(default_factory=list)
    suppressed: int = 0
    new_blocker_high: list[dict[str, object]] = field(default_factory=list)

    def summary_counts(self) -> dict[str, int]:
        return {
            "new": len(self.new),
            "fixed": len(self.fixed),
            "existing": len(self.existing),
            "moved": len(self.moved),
            "suppressed": self.suppressed,
        }


def _sig(f: dict[str, object]) -> tuple[str, str, str, str]:
    return (
        str(f.get("source")),
        str(f.get("rule")),
        str(f.get("file")),
        str(f.get("message")),
    )


def diff_findings(
    baseline: list[dict[str, object]], current: list[dict[str, object]]
) -> DiffResult:
    result = DiffResult()
    base_active = _active_findings(baseline)
    curr_active = _active_findings(current)
    result.suppressed = len(current) - len(curr_active)

    unmatched_base, unmatched_curr = _match_ids(base_active, curr_active, result)
    consumed_base, consumed_curr = _match_moved(unmatched_base, unmatched_curr, result)
    result.fixed = [f for f in unmatched_base if id(f) not in consumed_base]
    result.new = [f for f in unmatched_curr if id(f) not in consumed_curr]

    result.new_blocker_high = [f for f in result.new if f.get("severity") in ("blocker", "high")]
    return result


def _active_findings(findings: list[dict[str, object]]) -> list[dict[str, object]]:
    return [f for f in findings if f.get("status") != "suppressed"]


def _match_ids(
    baseline: list[dict[str, object]], current: list[dict[str, object]], result: DiffResult
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """精确 ID 匹配，返回尚未配对的基线和当前 finding。"""
    base_by_id = {str(f.get("id")): f for f in baseline}
    curr_by_id = {str(f.get("id")): f for f in current}
    unmatched_base = []
    for fid, finding in base_by_id.items():
        if fid in curr_by_id:
            result.existing.append(finding)
        else:
            unmatched_base.append(finding)
    unmatched_curr = [f for fid, f in curr_by_id.items() if fid not in base_by_id]
    return unmatched_base, unmatched_curr


def _match_moved(
    baseline: list[dict[str, object]], current: list[dict[str, object]], result: DiffResult
) -> tuple[set[int], set[int]]:
    """按签名成组配对行号变化，保留同签名 finding 的多重计数。"""
    base_group = _group_by_signature(baseline)
    curr_group = _group_by_signature(current)
    consumed_base: set[int] = set()
    consumed_curr: set[int] = set()
    for sig, base_list in base_group.items():
        curr_list = curr_group.get(sig, [])
        for old, curr in zip(base_list, curr_list):
            consumed_base.add(id(old))
            consumed_curr.add(id(curr))
            result.existing.append(old)
            result.moved.append(_moved_record(old, curr))
    return consumed_base, consumed_curr


def _group_by_signature(
    findings: list[dict[str, object]],
) -> dict[tuple[str, str, str, str], list[dict[str, object]]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, object]]] = {}
    for finding in findings:
        groups.setdefault(_sig(finding), []).append(finding)
    return groups


def _moved_record(old: dict[str, object], current: dict[str, object]) -> dict[str, object]:
    return {
        "source": old.get("source"),
        "rule": old.get("rule"),
        "file": old.get("file"),
        "message": old.get("message"),
        "from_line": old.get("line"),
        "to_line": current.get("line"),
    }
