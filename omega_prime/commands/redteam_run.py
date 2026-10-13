# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-run``: request one simulation suite run, fail-closed first."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

NAME = "omega-redteam-run"
USAGE = "/omega-redteam-run --suite <name>"
SUMMARY = "Run a red-team simulation suite through the fail-closed gate."
WHEN = "The user asks to run the red-team harness or a simulation suite."

REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"


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


def _suite_name(args: str) -> str:
    """The ``--suite`` value in ``args``; empty when absent."""
    try:
        tokens = shlex.split(args)
    except ValueError:
        return ""
    for index, token in enumerate(tokens):
        if token == "--suite" and index + 1 < len(tokens):
            return tokens[index + 1].strip()
    return ""


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Evaluate the gate, then run the named suite. BLOCKED never executes."""
    try:
        from sidecar import harness
    except ImportError as exc:
        raise Error(
            "sidecar_unavailable",
            {"usage": USAGE, "reason": f"{type(exc).__name__}: {exc}"},
        ) from exc
    suite = _suite_name(args)
    if not suite:
        raise Error("missing_suite", {"usage": USAGE})
    state, cause, detail = harness.evaluate(env)
    line = f"sidecar {harness.harness_version()}: {state} ({cause}): {detail}"
    if state != "IDLE":
        raise Error(
            "harness_blocked",
            {"line": line, "state": state, "cause": cause, "detail": detail},
        )
    try:
        from sidecar import simulation
    except ImportError as exc:
        raise Error(
            "sidecar_unavailable",
            {"usage": USAGE, "reason": f"{type(exc).__name__}: {exc}"},
        ) from exc
    available = list(simulation.KNOWN_SUITES)
    if suite not in available:
        raise Error(
            "unknown_suite",
            {"suite": suite, "available": available, "usage": USAGE},
        )
    cases, load_errors = simulation.load_suite()
    if load_errors or not cases:
        raise Error(
            "suite_invalid",
            {"suite": suite, "errors": load_errors or ["no cases loaded"]},
        )
    outcome = simulation.run_suite(cases, reports=_reports_dir(env))
    return {
        "line": line,
        "suite": suite,
        "cases": outcome.get("cases"),
        "counts": outcome.get("counts"),
    }


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]
