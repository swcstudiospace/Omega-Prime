# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-cycle``: plan or run the purple cycle; never dispatches."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from omega_prime.commands.redteam_common import (
    STATE_DIR_ENV_VAR,
    Error,
    parse_flags,
    resolve_reports_dir,
)

NAME = "omega-redteam-cycle"
USAGE = "/omega-redteam-cycle [--run] [--steps <a,b>]"
SUMMARY = (
    "Plan (default) or run the purple cycle: preflight, release watch, pin-bump "
    "audit, engagement digest, findings plan. Never dispatches findings."
)
WHEN = "The user asks to plan or run the purple-team operations cycle."

#: Result keys that carry absolute local paths; never returned to chat.
_PATH_KEYS = frozenset({"checkpoint", "ledger"})


def _cycle_env(env: Mapping[str, str]) -> dict[str, str]:
    """The caller env, plus the process state dir when the caller set none."""
    merged = dict(env)
    if not (merged.get(STATE_DIR_ENV_VAR) or "").strip():
        fallback = (os.environ.get(STATE_DIR_ENV_VAR) or "").strip()
        if fallback:
            merged[STATE_DIR_ENV_VAR] = fallback
    return merged


def _public(result: Mapping[str, Any]) -> dict[str, Any]:
    """``result`` without the absolute ``checkpoint`` / ``ledger`` paths."""
    return {key: value for key, value in result.items() if key not in _PATH_KEYS}


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Plan (pure) or ``--run`` the cycle. Apply is unreachable from chat."""
    flags = parse_flags(args, USAGE, switches=("--run",), options=("--steps",))
    steps: list[str] | None = None
    if "--steps" in flags:
        steps = [part.strip() for part in str(flags["--steps"]).split(",")]
        if not all(steps):
            raise Error("bad_args", {"usage": USAGE})
    reports = resolve_reports_dir(env, USAGE)
    try:
        from omega_prime.grokbot import purple_cycle
    except ImportError as exc:
        raise Error(
            "cycle_unavailable", {"usage": USAGE, "reason": type(exc).__name__}
        ) from exc
    try:
        result = purple_cycle.run_cycle(
            mode="run" if "--run" in flags else "plan",
            steps=steps,
            env=_cycle_env(env),
            reports_dir=reports,
        )
    except ValueError:
        raise Error("bad_args", {"usage": USAGE}) from None
    if result.get("state") == "failed":
        raise Error("cycle_failed", _public(result))
    if result.get("state") != "completed":
        raise Error("cycle_blocked", _public(result))
    return _public(result)


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]
