# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-status``: harness state plus recent transitions. Read-only."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

NAME = "omega-redteam-status"
USAGE = "/omega-redteam-status"
SUMMARY = "Show the red-team harness state and recent transitions."
WHEN = "The user asks for red-team harness status or state."

REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"
TAIL = 10


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


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Tail the transitions log; state is the last row's target, else IDLE."""
    del args
    try:
        from sidecar import state
    except ImportError as exc:
        raise Error(
            "sidecar_unavailable",
            {"usage": USAGE, "reason": f"{type(exc).__name__}: {exc}"},
        ) from exc
    path = state.transitions_path(_reports_dir(env))
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    tail = rows[-TAIL:]
    current = str(tail[-1].get("to", "IDLE")) if tail else "IDLE"
    return {"state": current, "transitions": tail, "total": len(rows)}


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]
