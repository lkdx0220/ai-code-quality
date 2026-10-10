"""密钥脱敏：所有落盘文本（含 raw 原始输出）写之前必须过一遍。

硬边界第 5 条：不把密钥、token、.env 内容写进报告。
raw 文件虽然名为"原始输出"，同样落盘在项目里，因此一并脱敏；
值替换为 ***REDACTED***，保留变量名/前缀以便归因。
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "***REDACTED***"

_RULES: list[tuple[re.Pattern[str], str]] = [
    # 已知形态的凭据（与 deepsec L1 的密钥规则对齐，阈值放宽以便漏报形态也能被兜住）
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), REDACTED),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{15,}\b"), REDACTED),
    (re.compile(r"\bsk-(?:ant-)?(?:proj-)?[A-Za-z0-9_-]{16,}\b"), REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), REDACTED),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        REDACTED,
    ),
    # 赋值形态：password = "..." / api_key: '...' / token: `...`（保留键名，抹值）
    (
        re.compile(
            r"(\b[A-Za-z_]*(?:password|passwd|pwd|secret|token|api[_-]?key|"
            r"access[_-]?key|client[_-]?secret|credential)[A-Za-z0-9_]*\s*[:=]\s*[\"'`])"
            r"(?:(?![\"'`]).){6,}?([\"'`])",
            re.IGNORECASE,
        ),
        r"\1" + REDACTED + r"\2",
    ),
    # 连接串里的密码：postgres://user:pass@host
    (
        re.compile(
            r"\b((?:postgres|postgresql|mysql|mongodb|redis|amqp)://[^:\s/@]+:)[^@\s/\"']+(@)"
        ),
        r"\1" + REDACTED + r"\2",
    ),
]


def redact_text(text: str) -> str:
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text


def redact_obj(obj: Any) -> Any:
    """递归脱敏 dict/list/str；其他类型原样返回。"""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {
            redact_text(str(k)) if isinstance(k, str) else k: redact_obj(v) for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    return obj
