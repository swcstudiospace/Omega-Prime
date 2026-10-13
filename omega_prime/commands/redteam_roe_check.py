# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-roe-check``: fail-closed RoE status. Read-only, never prints keys."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

NAME = "omega-redteam-roe-check"
USAGE = "/omega-redteam-roe-check"
SUMMARY = "Check the red-team Rules-of-Engagement authorization status."
WHEN = "The user asks whether the red-team Rules of Engagement are satisfied."

ROE_FILE_ENV_VAR = "OMEGA_PRIME_REDTEAM_ROE_FILE"
ROE_KEY_ENV_VAR = "OMEGA_PRIME_REDTEAM_ROE_KEY"
ROE_KEY_FILE_ENV_VAR = "OMEGA_PRIME_REDTEAM_ROE_KEY_FILE"


class Error(Exception):
    """Structured command failure: ``error`` names it, ``result`` carries it."""

    def __init__(self, error: str, result: dict[str, Any]) -> None:
        super().__init__(error)
        self.error = error
        self.result = result


def _first(env: Mapping[str, str], name: str) -> str:
    """One config value: caller env wins, else the process environment."""
    value = (env.get(name) or "").strip()
    if not value:
        value = (os.environ.get(name) or "").strip()
    return value


def _resolve_key(env: Mapping[str, str]) -> bytes | None:
    """Verification key, mirroring the runtime broker rule: direct key wins.

    A missing or unreadable key file degrades to no key so ``authorize()``
    fails closed. Key bytes are returned, never rendered.
    """
    direct = _first(env, ROE_KEY_ENV_VAR)
    if direct:
        return direct.encode("utf-8")
    key_file = _first(env, ROE_KEY_FILE_ENV_VAR)
    if not key_file:
        return None
    try:
        text = Path(key_file).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    return text.encode("utf-8") if text else None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _whole_days(artifact: str) -> int | None:
    """Whole days from now until ``effective_until``; ``None`` when unknown."""
    from omega_prime.redteam.roe import validate_schema

    try:
        document = json.loads(Path(artifact).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or validate_schema(document):
        return None
    end = _parse_time(document.get("effective_until"))
    if end is None:
        return None
    now = datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return int((end - now).total_seconds() // 86400)


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Resolve artifact + key like the runtime, then report ``authorize()``."""
    del args
    from omega_prime.redteam.roe import authorize

    artifact = _first(env, ROE_FILE_ENV_VAR)
    decision = authorize(artifact, key=_resolve_key(env))
    return {
        "allowed": decision.ok,
        "cause": decision.cause,
        "detail": decision.detail,
        "days_remaining": _whole_days(artifact),
    }


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]
