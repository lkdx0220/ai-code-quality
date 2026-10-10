"""DeepSec Shield L1/L2：安全扫描（离线层）。

L1 = 密钥/危险配置/AI 代码坏味道；L2 = SAST（注入等）。L3 需联网，永不默认启用。

deepsec 有两种可用形态，按顺序探测：
1. pip 安装：`<python> -m deepsec` 直接可用；
2. 源码仓库：CQ_DEEPSEC_HOME / cq.toml 指定仓库路径，注入 PYTHONPATH 调用。
都不行 → skipped（显式，不算通过）。
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ..schema import AdapterResult, Finding
from .base import Adapter, ScanContext, finding

_SEV_MAP = {"critical": "blocker", "high": "high", "medium": "medium", "low": "low", "info": "info"}


@dataclass
class ScanOneOutcome:
    """单目标单层 deepsec 扫描的结果摘要。"""

    duration_s: float = 0.0
    timed_out: bool = False
    error: str = ""


def _conf_to_word(conf: object) -> str:
    try:
        c = float(cast("float", conf))
    except (TypeError, ValueError):
        return "medium"
    if c >= 0.8:
        return "high"
    if c >= 0.5:
        return "medium"
    return "low"


SECRET_EVIDENCE_PLACEHOLDER = "[密钥类证据已隐藏：原文不落盘]"


def _strip_secret_evidence(json_text: str) -> str:
    """把 deepsec 原始 JSON 里密钥类 finding 的 evidence 也替换掉，raw 文件同样不落密钥碎片。"""
    import json as _json

    try:
        data = _json.loads(json_text)
    except _json.JSONDecodeError:
        return json_text
    items = data.get("findings") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return json_text
    changed = False
    for item in items:
        if not isinstance(item, dict):
            continue
        rule = str(item.get("detection_rule") or item.get("type") or "")
        if (
            "secret" in rule.lower() or "password" in rule.lower() or "credential" in rule.lower()
        ) and item.get("evidence"):
            item["evidence"] = SECRET_EVIDENCE_PLACEHOLDER
            changed = True
    if not changed:
        return json_text
    try:
        return _json.dumps(data, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return json_text


def _safe_evidence(rule: str, finding_type: str, evidence: str) -> str:
    """密钥类 finding 的 evidence 可能是"截断后的密钥碎片"（如 sk-p...cdef），
    碎片绕得过正则脱敏。密钥类证据从源头整体隐藏，不依赖模式匹配。"""
    text = f"{rule} {finding_type}".lower()
    if "secret" in text or "password" in text or "credential" in text or "token" in text:
        return SECRET_EVIDENCE_PLACEHOLDER
    return evidence


def parse_deepsec_json(text: str) -> list[dict[str, object]]:
    try:
        data = json.loads(text)
        items = data.get("findings") if isinstance(data, dict) else data
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
    except json.JSONDecodeError:
        return []


class DeepsecAdapter(Adapter):
    name = "deepsec"
    tool = "deepsec"
    description = "deepsec shield scan L1+L2（安全：密钥/危险写法/注入）"

    def _resolve(
        self, ctx: ScanContext
    ) -> tuple[list[str] | None, dict[str, str] | None, str, str]:
        """→ (基础命令前缀, 附加环境变量, 版本号, 探测说明)；不可用返回 (None, None, "", reason)。"""
        from ..runner import probe

        py = ctx.cfg.deepsec_python or ctx.cfg.python
        r = probe([py, "-m", "deepsec", "--help"], timeout=60)
        if r.returncode == 0:
            return [py, "-m", "deepsec"], {}, _version_of(py, None), "pip 形态"

        home = ctx.cfg.deepsec_home
        if home:
            home_path = Path(home)
            env = {"PYTHONPATH": str(home_path.resolve())}
            r2 = probe([py, "-m", "deepsec", "--help"], timeout=60, extra_env=env)
            if r2.returncode == 0:
                return (
                    [py, "-m", "deepsec"],
                    env,
                    _version_of(py, home_path),
                    f"源码形态（PYTHONPATH={home}）",
                )
            return None, None, "", f"deepsec 不可用（{home} 下 python -m deepsec 失败）"
        return (
            None,
            None,
            "",
            "deepsec 不可用（未 pip 安装，且未配置 CQ_DEEPSEC_HOME / cq.toml deepsec_home）",
        )

    def run(self, ctx: ScanContext) -> AdapterResult:

        if ctx.cfg.no_deepsec:
            return AdapterResult(
                name=self.name, tool=self.tool, status="skipped", reason="--no-deepsec 显式关闭"
            )
        base, env, version, note = self._resolve(ctx)
        if base is None:
            return AdapterResult(name=self.name, tool=self.tool, status="skipped", reason=note)
        if not ctx.targets:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="ok",
                version=version,
                reason="没有可扫描的 .py 目标",
            )

        findings: list[Finding] = []
        raw_files: list[str] = []
        total_s = 0.0
        errors: list[str] = []

        for layer in ("l1", "l2"):
            for i, target in enumerate(ctx.targets):
                out = self._scan_one(ctx, base, env or {}, layer, i, target, findings, raw_files)
                total_s += out.duration_s
                if out.timed_out:
                    return AdapterResult(
                        name=self.name,
                        tool=self.tool,
                        status="timeout",
                        version=version,
                        reason=f"deepsec {layer} 超时（>{ctx.cfg.timeout_for(self.name)}s）",
                        raw_files=raw_files,
                    )
                if out.error:
                    errors.append(out.error)

        if errors and not findings:
            return AdapterResult(
                name=self.name,
                tool=self.tool,
                status="error",
                version=version,
                reason="; ".join(errors),
                raw_files=raw_files,
            )
        status = "ok" if not errors else "error"
        return AdapterResult(
            name=self.name,
            tool=self.tool,
            status=status,
            version=version,
            duration_s=total_s,
            findings=findings,
            raw_files=raw_files,
        )

    def _scan_one(
        self,
        ctx: ScanContext,
        base: list[str],
        env: dict[str, str],
        layer: str,
        i: int,
        target: Path,
        findings: list[Finding],
        raw_files: list[str],
    ) -> ScanOneOutcome:
        """跑单目标单层扫描，把 findings 追加进调用方列表；返回耗时与错误信息。"""
        from ..runner import run_cmd

        out = ctx.raw_dir / f"deepsec-{layer}-{i}.json"
        cmd = [
            *base,
            "shield",
            "scan",
            str(target),
            "--layer",
            layer,
            "--format",
            "json",
            "--output",
            str(out),
        ]
        proc = run_cmd(
            cmd,
            timeout=ctx.cfg.timeout_for(self.name),
            cwd=ctx.raw_dir,
            extra_env=env or None,
        )
        if proc.timed_out:
            return ScanOneOutcome(duration_s=proc.duration_s, timed_out=True)
        # 找到高危时 deepsec 可能以非零退出；只要产物在且能解析就继续，否则记错误
        if proc.returncode not in (0, 1) and not out.is_file():
            return ScanOneOutcome(
                duration_s=proc.duration_s,
                error=f"{layer}: 退出码 {proc.returncode} {proc.stderr.strip()[:150]}",
            )
        if out.is_file():
            text = out.read_text(encoding="utf-8", errors="replace")
            text = _strip_secret_evidence(text)
            rel = f"raw/deepsec-{layer}-{i}.json"
            raw_files.append(self.write_raw(ctx, f"deepsec-{layer}-{i}.json", text))
            self._collect_layer_findings(ctx, text, layer, rel, findings)
        return ScanOneOutcome(duration_s=proc.duration_s)

    def _collect_layer_findings(
        self, ctx: ScanContext, text: str, layer: str, rel: str, findings: list[Finding]
    ) -> None:
        for item in parse_deepsec_json(text):
            sev = _SEV_MAP.get(str(item.get("severity", "info")).lower(), "info")
            rule = str(item.get("detection_rule") or item.get("type") or layer)
            findings.append(
                finding(
                    ctx,
                    source="deepsec",
                    category="security",
                    severity=sev,
                    file=str(item.get("target") or item.get("file") or ""),
                    line=int(cast("int", item.get("line") or 0)),
                    message=str(
                        item.get("title") or item.get("message") or item.get("type") or "安全问题"
                    ),
                    rule=rule,
                    evidence=_safe_evidence(
                        rule,
                        str(item.get("type") or ""),
                        str(item.get("evidence") or ""),
                    ),
                    fix_hint=str(item.get("suggestion") or ""),
                    confidence=_conf_to_word(item.get("confidence")),
                    raw_ref=rel,
                )
            )


def _version_of(python: str, home: Path | None) -> str:
    if home is not None:
        try:
            with open(home / "pyproject.toml", "rb") as f:
                data = tomllib.load(f)
            v = data.get("project", {}).get("version")
            if v:
                return f"{v}（源码）"
        except (OSError, tomllib.TOMLDecodeError):
            pass
    return "unknown"
