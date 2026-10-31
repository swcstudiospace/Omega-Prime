"""Secret redaction for logs, events, and model-facing recall.

Known secret values (via ``extra``) plus secret-shaped patterns are replaced
with ``[REDACTED]``. Key names and the ``Bearer`` scheme word stay readable
so the shape of the redacted line still explains itself.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_.-]{8,}"),
    re.compile(r"xai-[A-Za-z0-9]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{8,}"),
    # Fine-grained GitHub personal access tokens.
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]

_BEARER = re.compile(r"(Bearer )([^\s]+)")
_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|token|secret)(\s*[:=]\s*)([\"']?)[^\s\"',}]+"
)


def redact_text(text: Any, extra: tuple[str, ...] = ()) -> Any:
    """Redact secrets in ``text``. Non-string input passes through."""
    if not isinstance(text, str):
        return text
    for secret in sorted((extra or ()), key=len, reverse=True):
        if isinstance(secret, str) and secret:
            text = text.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _BEARER.sub(lambda match: f"{match.group(1)}{REDACTED}", text)
    text = _ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)}{REDACTED}",
        text,
    )
    return text


def redact_value(value: Any, extra: tuple[str, ...] = ()) -> Any:
    """Recursively redact strings inside dicts, lists, and tuples."""
    if isinstance(value, str):
        return redact_text(value, extra)
    if isinstance(value, dict):
        return {key: redact_value(item, extra) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item, extra) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, extra) for item in value)
    return value


__all__ = ["REDACTED", "redact_text", "redact_value"]
