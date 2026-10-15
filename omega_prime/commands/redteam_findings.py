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
USAGE = "/omega-redteam-findings [--limit N] [--jsonl]"
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


def _options(args: str) -> tuple[int, bool]:
    """Parse ``--limit N`` and the ``--jsonl`` switch. Writes nothing."""
    try:
        tokens = shlex.split(args)
    except ValueError:
        raise Error("bad_args", {"usage": USAGE, "args": args.strip()}) from None
    limit = DEFAULT_LIMIT
    jsonl = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--jsonl":
            jsonl = True
            index += 1
            continue
        if token == "--limit":
            if index + 1 >= len(tokens):
                raise Error("bad_limit", {"usage": USAGE, "limit": ""})
            try:
                value = int(tokens[index + 1], 10)
            except ValueError:
                raise Error(
                    "bad_limit", {"usage": USAGE, "limit": tokens[index + 1]}
                ) from None
            limit = max(1, min(MAX_LIMIT, value))
            index += 2
            continue
        raise Error("bad_args", {"usage": USAGE, "args": args.strip()})
    return limit, jsonl


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Return the newest ``limit`` findings rows, newest last. No writes.

    ``--jsonl`` adds the rendered ``finding/1`` text for those same rows.
    The command never creates a file.
    """
    limit, jsonl = _options(args)
    try:
        from sidecar import findings
    except ImportError as exc:
        raise Error(
            "sidecar_unavailable",
            {"usage": USAGE, "reason": f"{type(exc).__name__}: {exc}"},
        ) from exc
    rows = findings.load_findings(_reports_dir(env))
    window = rows[-limit:]
    result: dict[str, Any] = {
        "findings": window,
        "total": len(rows),
        "limit": limit,
    }
    if jsonl:
        result["jsonl"] = findings.render_findings_jsonl(window)
    return result


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]
