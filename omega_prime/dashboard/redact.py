# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Dashboard-specific recursive redaction and output clipping."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from omega_prime.credentials.redact import redact_text

_NAMED_SECRET_ENV = (
    "RAILWAY_API_TOKEN",
    "FLEET_COORDINATOR_TOKEN",
    "GITHUB_PAT_DESK_LEAD",
    "OMEGA_PRIME_OMP_ALLOW_CHATGPT",
    "OPENAI_CODEX_OAUTH_TOKEN",
    "OPENAI_API_KEY",
)
_DENIED = {"token", "key", "secret", "password", "pat", "auth", "cookie", "bearer"}
_HUMPS = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)(\b(?:password|passwd|pat|cookie|token|key|secret|auth|bearer)"
    r"\b['\"]?\s*[=:]\s*['\"]?)([^\s'\",;]+)"
)
# The shared ``sk-`` pattern has no left word boundary, so names such as
# "task-dashboard" would lose their tail. Shield word-internal "sk-" first.
_INNER_SK = re.compile(r"(?<=[A-Za-z0-9])sk-")
_SK_SHIELD = "\x00sk\x00"


def _secrets(extra: tuple[str, ...], env: Mapping[str, str] | None) -> tuple[str, ...]:
    source = os.environ if env is None else env
    named = (
        value
        for name, value in source.items()
        if name in _NAMED_SECRET_ENV or name.startswith("OMP_AUTH_BROKER")
    )
    # Opt-in flags like OMEGA_PRIME_OMP_ALLOW_CHATGPT=1 are named here, but a
    # one-character value is a flag, not a credential; scrubbing it would
    # redact every "1" in ids and summaries.
    return extra + tuple(value for value in named if len(value) >= 8)


def _denied_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    words = re.split(r"[_\-\s]+", _HUMPS.sub(" ", key))
    return any(word.lower() in _DENIED for word in words)


def redact_value(
    value: Any,
    extra: tuple[str, ...] = (),
    env: Mapping[str, str] | None = None,
) -> Any:
    """Recursively redact credentials, named env secrets, and secret keys."""
    secrets = _secrets(extra, env)

    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                key: "[REDACTED]" if _denied_key(key) else walk(child)
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [walk(child) for child in item]
        if isinstance(item, tuple):
            return tuple(walk(child) for child in item)
        if isinstance(item, str):
            # Exact secret values go first, before shielding can alter them.
            for secret in secrets:
                if secret:
                    item = item.replace(secret, "[REDACTED]")
            item = _ASSIGNMENT_SECRET.sub(r"\1[REDACTED]", item)
            shielded = _INNER_SK.sub(_SK_SHIELD, item)
            out = redact_text(shielded, secrets)
            return out.replace(_SK_SHIELD, "sk-") if isinstance(out, str) else out
        return redact_text(item, secrets)

    return walk(value)


def redact_line(text: Any, cap: int = 120, extra: tuple[str, ...] = ()) -> Any:
    """Redact the first line of strings, then clip to ``cap`` characters."""
    if not isinstance(text, str):
        return text
    first = text.splitlines()[0] if text.splitlines() else ""
    redacted = redact_value(first, extra)
    return redacted[:cap]


__all__ = ["redact_line", "redact_value"]
