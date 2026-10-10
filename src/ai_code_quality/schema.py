"""统一 finding schema 与工具结果模型（交接方案 §6）。

一条 Finding 是"某个工具在某文件某行发现的某个问题"，
带统一的 severity / category / confidence，供后续 triage / diff / gate 复用。
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

SEVERITIES = ("blocker", "high", "medium", "low", "info")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}

CATEGORIES = (
    "security",
    "reliability",
    "maintainability",
    "test",
    "architecture",
    "dependency",
    "slop",
)

SOURCES = (
    "ruff",
    "ruff-format",
    "mypy",
    "radon",
    "vulture",
    "pytest",
    "import-linter",
    "deepsec",
    # 预留：sonar / slop-rule / ai-review（Phase 2+）
)

# adapter 执行状态：ok 正常跑完；skipped 未跑（缺工具/无测试/无配置等，带 reason）；
# timeout 超时；error 崩溃或解析失败。skipped/error/timeout 都不允许当作"通过"。
TOOL_STATUSES = ("ok", "skipped", "timeout", "error")


def make_id(source: str, rule: str, file: str, line: int, message: str) -> str:
    raw = f"{source}|{rule}|{file}|{line}|{message}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@dataclass
class Finding:
    source: str
    category: str
    severity: str
    file: str  # 相对项目根，统一正斜杠；无文件级信息时为 ""
    line: int  # 无行号时为 0
    message: str
    rule: str = ""
    evidence: str = ""
    fix_hint: str = ""
    auto_fixable: bool = False
    status: str = (
        "new"  # new|existing|fixed|suppressed|accepted；Phase 1 扫描恒为 new，diff/suppress 改写
    )
    confidence: str = "high"  # 确定性工具默认 high
    raw_ref: str = ""
    suppression_action: str = ""  # status=suppressed 时的动作（false_positive/debt_accepted）
    suppression_reason: str = ""  # 豁免理由（必填，进报告）
    id: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"bad source: {self.source!r}（合法值见 SOURCES，Phase 2+ 扩展）")
        if self.severity not in SEVERITIES:
            raise ValueError(f"bad severity: {self.severity!r}")
        if self.category not in CATEGORIES:
            raise ValueError(f"bad category: {self.category!r}")
        if self.status == "new" and not self.id:
            self.id = make_id(self.source, self.rule, self.file, self.line, self.message)

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        # id 放最前，便于人读
        return {"id": d.pop("id"), **d}


@dataclass
class AdapterResult:
    """一个 adapter（对应一个工具/检查）的一次执行结果。"""

    name: str  # adapter 名，如 "ruff-check"
    tool: str  # 展示用工具名，如 "ruff"
    status: str  # TOOL_STATUSES 之一
    reason: str = ""  # skipped/error/timeout 的原因（必填，不允许静默）
    description: str = ""  # 该 adapter 检查什么（safe_run 从 Adapter 注入，报告展示用）
    version: str = ""
    duration_s: float = 0.0
    findings: list[Finding] = field(default_factory=list)
    raw_files: list[str] = field(default_factory=list)  # 相对 run 目录的原始输出文件

    def __post_init__(self) -> None:
        if self.status not in TOOL_STATUSES:
            raise ValueError(f"bad adapter status: {self.status!r}")
        if self.status in ("skipped", "timeout", "error") and not self.reason:
            raise ValueError(f"{self.name}: status={self.status} 必须给 reason，不许静默 pass")

    def to_dict(self) -> dict[str, object]:
        by_sev: dict[str, int] = {}
        for f in self.findings:
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        return {
            "name": self.name,
            "tool": self.tool,
            "description": self.description,
            "status": self.status,
            "reason": self.reason,
            "version": self.version,
            "duration_s": round(self.duration_s, 2),
            "finding_count": len(self.findings),
            "findings_by_severity": by_sev,
            "raw_files": self.raw_files,
        }


def sort_findings(findings: list[Finding]) -> list[Finding]:
    """blocker → info，同severity 按文件/行排序，保证报告可复现。"""
    return sorted(findings, key=lambda f: (SEVERITY_RANK[f.severity], f.file, f.line, f.id))
