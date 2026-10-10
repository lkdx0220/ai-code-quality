"""cq baseline：把某次 run 固化为具名基线，供 diff / gate 引用。

基线存 audits/baselines/<name>.json；引用时基线名和 run_id 都可以
（基线文件不存在时回退按 run 解析）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .runs import (
    RunData,
    RunNotFoundError,
    baseline_path,
    load_run,
    resolve_run_dir,
    validate_baseline_name,
)


def save_baseline(project_root: Path, run_ref: str, name: str) -> tuple[Path, RunData]:
    """把某次 run 的 findings + 关键 summary 固化为具名基线。"""
    validate_baseline_name(name)
    run = load_run(project_root, run_ref)
    path = baseline_path(project_root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": name,
        "source_run_id": run.run_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git": run.summary.get("git"),
        "totals": run.summary.get("totals"),
        "findings": run.findings,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, run


def load_baseline(project_root: Path, ref: str) -> tuple[str, RunData]:
    """基线引用可以是基线名，也可以是另一个 run_id（直接拿那次 run 当基线）。

    基线文件自包含 findings：即使源 run 目录已被清理，基线依然可用
    （run_dir 仅作占位，diff/gate 的判定数据都在 findings/summary 里）。
    """
    validate_baseline_name(ref)
    path = baseline_path(project_root, ref)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise RunNotFoundError(f"基线 {ref} 读取失败: {e}") from e
        source_id = str(data.get("source_run_id", ""))
        try:
            run_dir = resolve_run_dir(project_root, source_id) if source_id else path.parent
        except RunNotFoundError:
            run_dir = path.parent  # 源 run 已清理：基线仍然自洽可用
        findings = data.get("findings") if isinstance(data.get("findings"), list) else []
        return ref, RunData(
            run_id=f"{ref}(←{source_id or '?'})",
            run_dir=run_dir,
            summary=data if isinstance(data, dict) else {},
            findings=findings,
        )
    # 回退：当作 run_id / 前缀解析
    run = load_run(project_root, ref)
    return ref, run
