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
ROE_FILES_ENV_VAR = "OMEGA_PRIME_REDTEAM_ROE_FILES"
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


def _listed(env: Mapping[str, str], name: str) -> list[str]:
    """Comma-separated paths. Caller env wins over the process environment."""
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        raw = os.environ.get(name) or ""
    found: list[str] = []
    for entry in str(raw).split(","):
        text = entry.strip()
        if text and text not in found:
            found.append(text)
    return found


def _effective_grants(env: Mapping[str, str]) -> list[str]:
    """Same replacement rule as ``redteam_roe_files``.

    A non-empty ``OMEGA_PRIME_REDTEAM_ROE_FILES`` list is the active set.
    The single-file variable is the fallback only when that list is empty.
    """
    listed = _listed(env, ROE_FILES_ENV_VAR)
    if listed:
        return listed
    single = _first(env, ROE_FILE_ENV_VAR)
    return [single] if single else []


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Probe readiness. Any argument is rejected before the probe runs.

    Grant paths and the optional gateway URL come only from the host
    environment. A missing grant is passed as ``None`` so the lifecycle
    check fails closed. Concurrent grants are ready only when every one
    of them is. Nothing is written.
    """
    if args.strip():
        raise Error("bad_args", {"usage": USAGE})
    from omega_prime.grokbot.roe_lifecycle import (
        READY_LIFECYCLE_STATES,
        check_readiness,
        check_roe_lifecycle,
    )

    grants = _effective_grants(env)
    gateway = _first(env, GATEWAY_URL_ENV_VAR) or None
    now = datetime.now(UTC)
    primary = grants[0] if grants else None
    report = check_readiness(primary, now, gateway_url=gateway)
    if report.get("roe_state") == "IDLE":
        for extra in grants[1:]:
            lifecycle = check_roe_lifecycle(extra, now)
            if lifecycle.get("state") not in READY_LIFECYCLE_STATES:
                report = dict(report)
                report["roe_state"] = "BLOCKED"
                report["ready_for_live"] = False
                break
    return {key: report[key] for key in _KEYS}


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]
