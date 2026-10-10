from ai_code_quality.report import (
    _next_steps,
    build_console_output,
    build_report_md,
)
from ai_code_quality.schema import Finding


def _mk_summary() -> dict:
    return {
        "run_id": "20260101-000000-full",
        "created_at": "2026-01-01T00:00:00",
        "project_root": "/tmp/proj",
        "scope": "full",
        "scanned_targets": ["/tmp/proj/src"],
        "git": {"is_repo": True, "branch": "main", "commit": "abc1234", "dirty_files": 2},
        "tools": [
            {
                "name": "ruff-check",
                "tool": "ruff",
                "description": "lint 检查",
                "status": "ok",
                "reason": "",
                "version": "0.16.9",
                "duration_s": 0.1,
                "finding_count": 1,
            },
            {
                "name": "mypy",
                "tool": "mypy",
                "description": "类型检查",
                "status": "skipped",
                "reason": "mypy 不可用",
                "version": "",
                "duration_s": 0,
                "finding_count": 0,
            },
        ],
        "totals": {
            "findings": 2,
            "by_severity": {"blocker": 1, "low": 1, "info": 3},
            "by_category": {"security": 1, "maintainability": 1},
            "auto_fixable": 1,
        },
        "notes": ["scope=changed 但没有变更"],
    }


def _mk_findings() -> list[Finding]:
    blocker = Finding(
        source="deepsec",
        category="security",
        severity="blocker",
        file="app/a.py",
        line=7,
        message="硬编码密钥",
        rule="hardcoded_secret",
    )
    low = Finding(
        source="ruff",
        category="maintainability",
        severity="low",
        file="app/b.py",
        line=9,
        message="unused import",
        rule="F401",
        auto_fixable=True,
    )
    return [blocker, low]


def test_report_md_contains_all_sections():
    md = build_report_md(_mk_summary(), _mk_findings())
    assert "# 代码质量扫描报告 · 20260101-000000-full" in md
    assert "## 工具状态" in md and "## 分级统计" in md
    assert "## BLOCKER（1）" in md and "## LOW（1）" in md
    assert "## 建议的下一步（启发式）" in md
    assert "## 备注" in md
    assert "main@abc1234" in md
    assert "另有 info 级 3 条" in md
    # ok 且无 reason 时说明列回退为 description
    assert "lint 检查" in md
    # skipped 工具显示原因
    assert "mypy 不可用" in md


def test_report_md_non_git_and_empty():
    s = _mk_summary()
    s["git"] = {"is_repo": False}
    s["notes"] = []
    md = build_report_md(s, [])
    assert "非 git 仓库" in md
    assert "没有 blocker/high" in md  # 空结果时的下一步建议


def test_report_details_capped_by_limit():
    s = _mk_summary()
    s["totals"]["by_severity"] = {"low": 15}
    many = [
        Finding(
            source="ruff",
            category="maintainability",
            severity="low",
            file=f"f{i}.py",
            line=1,
            message=f"m{i}",
        )
        for i in range(15)
    ]
    md = build_report_md(s, many)
    assert "另有 5 条，见 findings.json" in md


def test_console_output_shape():
    text = build_console_output(_mk_summary(), _mk_findings())
    assert "扫描完成：20260101-000000-full" in text
    assert "ruff-check" in text and "mypy" in text
    assert "blocker:1" in text
    assert "report.md" in text


def test_next_steps_priority_order():
    steps = _next_steps(_mk_findings())
    assert any("blocker" in s for s in steps[0:1])
    assert any("安全" in s for s in steps)
    assert any("自动修复" in s for s in steps)
