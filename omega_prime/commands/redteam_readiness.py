# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-readiness``: live-readiness report. Read-only, never engages."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

NAME = "omega-redteam-readiness"
USAGE = "/omega-redteam-readiness"
SUMMARY = "Report whether the signed Rules of Engagement are ready for a live run."
WHEN = (
    "The user asks for a read-only live-readiness report: RoE window, "
    "gateway reachability, and loaded suites, without starting an engagement."
)

ROE_FILE_ENV_VAR = "OMEGA_PRIME_REDTEAM_ROE_FILE"
GATEWAY_URL_ENV_VAR = "OMEGA_PRIME_REDTEAM_GATEWAY_URL"

_KEYS = (
    "roe_state",
    "gateway_reachable",
    "suites_loaded",
    "suites_total",
    "ready_for_live",
)


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


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Probe readiness. Any argument is rejected before the probe runs.

    The artifact path and optional gateway URL come only from the host
    environment. A missing artifact is passed as ``None`` so the lifecycle
    check fails closed. Nothing is written.
    """
    if args.strip():
        raise Error("bad_args", {"usage": USAGE})
    from omega_prime.grokbot.roe_lifecycle import check_readiness

    artifact = _first(env, ROE_FILE_ENV_VAR) or None
    gateway = _first(env, GATEWAY_URL_ENV_VAR) or None
    report = check_readiness(artifact, datetime.now(UTC), gateway_url=gateway)
    return {key: report[key] for key in _KEYS}


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]
