# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-findings``: tail recent findings. Read-only, never replays."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

NAME = "omega-redteam-findings"
USAGE = "/omega-redteam-findings [--limit N]"
SUMMARY = "Tail recent red-team harness findings. Read-only."
WHEN = "The user asks for red-team findings or detections."

REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"
DEFAULT_LIMIT = 10
MAX_LIMIT = 100


class Error(Exception):
    """Structured command failure: ``error`` names it, ``result`` carries it."""

    def __init__(self, error: str, result: dict[str, Any]) -> None:
        super().__init__(error)
        self.error = error
        self.result = result


def _reports_dir(env: Mapping[str, str]) -> Path | None:
    """Reports dir override from the caller env, else the sidecar default."""
    override = (env.get(REPORTS_ENV_VAR) or "").strip()
    if not override:
        override = (os.environ.get(REPORTS_ENV_VAR) or "").strip()
    return Path(override) if override else None


def _limit(args: str) -> int:
    """Parse ``--limit N``; default 10, clamped to 1..100, never zero."""
    try:
        tokens = shlex.split(args)
    except ValueError:
        raise Error("bad_limit", {"usage": USAGE, "limit": args.strip()}) from None
    if not tokens:
        return DEFAULT_LIMIT
    if len(tokens) != 2 or tokens[0] != "--limit":
        raise Error("bad_limit", {"usage": USAGE, "limit": args.strip()})
    try:
        value = int(tokens[1], 10)
    except ValueError:
        raise Error("bad_limit", {"usage": USAGE, "limit": tokens[1]}) from None
    return max(1, min(MAX_LIMIT, value))


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Return the newest ``limit`` findings rows, newest last. No writes."""
    limit = _limit(args)
    try:
        from sidecar import findings
    except ImportError as exc:
        raise Error(
            "sidecar_unavailable",
            {"usage": USAGE, "reason": f"{type(exc).__name__}: {exc}"},
        ) from exc
    rows = findings.load_findings(_reports_dir(env))
    return {"findings": rows[-limit:], "total": len(rows), "limit": limit}


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]
