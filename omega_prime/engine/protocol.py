# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""NDJSON frames for omp RPC mode.

Host commands are one JSON object per line. omp replies with events, also
one JSON object per line. This module names the subset Phase A1a speaks.
"""

from __future__ import annotations

import json
import re
from typing import Any

from omega_prime.credentials.redact import redact_text

#: omp 18.x is the protocol this driver was written against.
OMP_SUPPORTED_MAJOR = 18

COMMANDS = (
    "prompt",
    "steer",
    "abort",
    "set_model",
    "set_thinking_level",
    "set_event_filter",
    "get_session_stats",
)

STREAM_EVENTS = (
    "ready",
    "agent_start",
    "turn_start",
    "turn_end",
    "message_update",
    "message_end",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "agent_end",
)

EVENT_FILTER = [*STREAM_EVENTS, "response"]

THINKING_LEVELS = frozenset({"off", "minimal", "low", "medium", "high", "xhigh"})

MODES = frozenset({"rpc", "rpc-ui"})

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

_SECRET_KEYS = frozenset(
    {
        "apikey",
        "api_key",
        "api-key",
        "authorization",
        "auth",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
    }
)


def parse_version(text: str) -> tuple[int, int, int] | None:
    """Return the first ``major.minor.patch`` in ``text``."""
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def dumps(payload: dict[str, Any]) -> str:
    """One NDJSON line, without a trailing newline."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def loads(line: str) -> dict[str, Any] | None:
    """Parse one frame. A non-object line becomes a notice, not an exception."""
    text = line.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"type": "notice", "text": text[:500]}
    if isinstance(payload, dict):
        return payload
    return {"type": "notice", "text": text[:500]}


def redact(value: Any) -> Any:
    """Copy ``value`` with secret-shaped fields replaced.

    Keys whose names are credentials are replaced. String values that look
    like a bearer token or a common key prefix are replaced. The replacement
    is a fixed label, never a partial secret.
    """
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key).lower().replace("-", "_")
            if (
                name in _SECRET_KEYS
                or name.endswith("_token")
                or name.endswith("_secret")
            ):
                cleaned[str(key)] = "[redacted]"
            else:
                cleaned[str(key)] = redact(item)
        return cleaned
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def assistant_text(event: dict[str, Any]) -> str:
    """Visible assistant text from a message frame."""
    message = event.get("message")
    content = (
        message.get("content") if isinstance(message, dict) else event.get("content")
    )
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    delta = event.get("delta")
    if isinstance(delta, str):
        return delta
    if isinstance(delta, dict):
        for key in ("text", "delta", "content"):
            item = delta.get(key)
            if isinstance(item, str):
                return item
    text = event.get("text")
    return text if isinstance(text, str) else ""


def usage_of(event: dict[str, Any]) -> tuple[dict[str, Any], float | None]:
    """Usage object and cost from a ``message_end`` frame."""
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    usage = event.get("usage")
    if not isinstance(usage, dict):
        usage = message.get("usage") if isinstance(message, dict) else None
    cost = event.get("cost")
    if cost is None and isinstance(message, dict):
        cost = message.get("cost")
    if not isinstance(usage, dict):
        usage = {}
    numeric = (
        float(cost)
        if isinstance(cost, (int, float)) and not isinstance(cost, bool)
        else None
    )
    return dict(usage), numeric
