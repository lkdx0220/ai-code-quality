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
    base_active = [f for f in baseline if f.get("status") != "suppressed"]
    curr_active = [f for f in current if f.get("status") != "suppressed"]
    result.suppressed = len(current) - len(curr_active)

    base_by_id = {str(f.get("id")): f for f in base_active}
    curr_by_id = {str(f.get("id")): f for f in curr_active}

    # 1) 精确 id 匹配 → existing
    unmatched_base: list[dict[str, object]] = []
    for fid, f in base_by_id.items():
        if fid in curr_by_id:
            result.existing.append(f)
        else:
            unmatched_base.append(f)

    unmatched_curr: list[dict[str, object]] = []
    for fid, f in curr_by_id.items():
        if fid not in base_by_id:
            unmatched_curr.append(f)

    # 2) 同源移动：(source, rule, file, message) 相同、行号不同 → existing(moved)，成对消费。
    #    按签名分组计数配对（配对数 = min(基线数, 当前数)）：基线多余 → fixed，当前多余 → new。
    #    （曾有 bug：dict-pop 只配每条签名的第一条，同签名其余条目被误判成 new——
    #    外部验证在真实仓库上抓到的"同时进 new 和 moved"即此。）
    base_group: dict[tuple[str, str, str, str], list[dict[str, object]]] = {}
    for f in unmatched_base:
        base_group.setdefault(_sig(f), []).append(f)
    curr_group: dict[tuple[str, str, str, str], list[dict[str, object]]] = {}
    for f in unmatched_curr:
        curr_group.setdefault(_sig(f), []).append(f)

    consumed_base: set[int] = set()
    consumed_curr: set[int] = set()
    for sig, base_list in base_group.items():
        curr_list = curr_group.get(sig, [])
        n = min(len(base_list), len(curr_list))
        for k in range(n):
            old, curr = base_list[k], curr_list[k]
            consumed_base.add(id(old))
            consumed_curr.add(id(curr))
            result.existing.append(old)
            result.moved.append(
                {
                    "source": old.get("source"),
                    "rule": old.get("rule"),
                    "file": old.get("file"),
                    "message": old.get("message"),
                    "from_line": old.get("line"),
                    "to_line": curr.get("line"),
                }
            )
    result.fixed = [f for f in unmatched_base if id(f) not in consumed_base]
    result.new = [f for f in unmatched_curr if id(f) not in consumed_curr]

    result.new_blocker_high = [f for f in result.new if f.get("severity") in ("blocker", "high")]
    return result
