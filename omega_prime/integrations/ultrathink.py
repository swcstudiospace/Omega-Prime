# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Strict ship capability parsing and readonly Ultrathink readiness metadata."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omega_prime.routines.ultrathink_turn import candidate_state_dirs

ULTRATHINK_REQUIRED_BINS = ("ultrathink", "ultrathink-mcp", "ultrathink-ship")
ULTRATHINK_STATES = (
    "not_configured",
    "root_invalid",
    "ready_ship_off",
    "ready_ship_on",
)
READINESS_ENV_KEYS = (
    "HOME",
    "OMEGA_PRIME_STATE_DIR",
    "ULTRATHINK_STATE_DIR",
    "GROK_PLUGIN_DATA",
    "GROK_HOME",
    "HERMES_HOME",
    "XDG_CONFIG_HOME",
    "PI_CODING_AGENT_DIR",
    "ULTRATHINK_ROOT",
    "ULTRATHINK_SHIP",
)

__all__ = [
    "READINESS_ENV_KEYS",
    "ULTRATHINK_REQUIRED_BINS",
    "ULTRATHINK_STATES",
    "ship_enabled",
    "ultrathink_readiness",
]


def ultrathink_readiness(env: Mapping[str, str]) -> dict[str, Any]:
    """Return configured root readiness using filesystem metadata only."""
    root = env.get("ULTRATHINK_ROOT", "")
    enabled = ship_enabled(env)
    preferred = str(candidate_state_dirs(env)[0])
    problems: list[str] = []
    if root:
        for name in ULTRATHINK_REQUIRED_BINS:
            path = Path(root) / "bin" / name
            if not path.is_file():
                reason = "not a file" if path.is_dir() else "missing"
                problems.append(f"bin/{name}: {reason}")
            elif not os.access(path, os.X_OK):
                problems.append(f"bin/{name}: not executable")
    state = (
        "not_configured"
        if not root
        else "root_invalid"
        if problems
        else "ready_ship_on"
        if enabled
        else "ready_ship_off"
    )
    return {
        "state": state,
        "root": root,
        "ship_enabled": enabled,
        "preferred_state_dir": preferred,
        "problems": problems,
    }


def ship_enabled(env: Mapping[str, str]) -> bool:
    """Return whether the explicit environment opts into ship writes."""
    return env.get("ULTRATHINK_SHIP") in ("1", "true")
