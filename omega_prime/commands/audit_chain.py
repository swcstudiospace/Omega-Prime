# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-audit``: verify the tamper-evident tool-call audit chain. Read-only."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omega_prime.grokbot.audit import (
    GrokBotAuditTracer,
    default_audit_path,
    redact_sensitive,
)

NAME = "omega-audit"
USAGE = "/omega-audit [--tail N]"
SUMMARY = "Verify the tamper-evident tool-call audit chain and optionally tail it."
WHEN = "The user asks to verify or tail the Grok Bot audit log."

AUDIT_LOG_ENV = "OMEGA_PRIME_AUDIT_LOG"
STATE_DIR_ENV = "OMEGA_PRIME_STATE_DIR"
_TAIL_MIN = 1
_TAIL_MAX = 20
_TAIL_FIELDS = ("seq", "timestamp", "event", "tool_name", "status")


class Error(Exception):
    """Structured command failure: ``error`` names it, ``result`` carries it."""

    def __init__(self, error: str, result: dict[str, Any]) -> None:
        super().__init__(error)
        self.error = error
        self.result = result


def _tail_limit(args: str) -> int | None:
    """``None`` verifies only. ``--tail N`` requires an integer in 1..20."""
    try:
        tokens = shlex.split(args or "")
    except ValueError:
        raise Error("bad_args", {"usage": USAGE}) from None
    if not tokens:
        return None
    if len(tokens) != 2 or tokens[0] != "--tail":
        raise Error("bad_args", {"usage": USAGE})
    try:
        value = int(tokens[1], 10)
    except ValueError:
        raise Error("bad_args", {"usage": USAGE}) from None
    if value < _TAIL_MIN or value > _TAIL_MAX:
        raise Error("bad_args", {"usage": USAGE})
    return value


def _log_path(env: Mapping[str, str]) -> Path:
    override = (env.get(AUDIT_LOG_ENV) or "").strip()
    if override:
        return Path(override)
    root = (env.get(STATE_DIR_ENV) or "").strip() or "."
    return default_audit_path(root)


def _project(record: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the public tail fields. Drop anything that looks like a token."""
    projected: dict[str, Any] = {}
    for key in _TAIL_FIELDS:
        if key not in record:
            continue
        value = record[key]
        if isinstance(value, str) and redact_sensitive(value) != value:
            continue
        projected[key] = value
    return projected


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Verify the audit chain. With ``--tail N``, add redacted newest records.

    A missing log is empty, not success. A broken chain returns the verifier
    message and no record bodies. This function does not create or write the log.
    """
    tail_n = _tail_limit(args)
    path = _log_path(env)
    if not path.is_file():
        return {"ok": False, "verified": 0, "status": "empty"}
    ok, count, message = GrokBotAuditTracer(path).verify_integrity(include_rotated=True)
    if not ok:
        return {
            "ok": False,
            "verified": count,
            "status": "broken",
            "detail": message,
        }
    result: dict[str, Any] = {"ok": True, "verified": count, "status": "verified"}
    if tail_n is not None:
        recent = GrokBotAuditTracer(path).read_recent(limit=tail_n)
        result["tail"] = [_project(record) for record in recent]
    return result


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]
