# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-sync-plan``: what the findings sync would send. Read-only."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from omega_prime.commands.redteam_common import (
    Error,
    parse_flags,
    resolve_reports_dir,
)

NAME = "omega-redteam-sync-plan"
USAGE = "/omega-redteam-sync-plan"
SUMMARY = (
    "Plan the next findings sync without dispatching anything: what would sync "
    "and what is held."
)
WHEN = "The user asks what the findings sync would send to the tracker next."

DEFAULT_TRACKER = "github"


def _forbidden_runner(argv: Any, data: Any) -> Any:
    raise AssertionError("sync-plan must never reach the dispatch runner")


def _plan_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Deep copy of ``config`` with both findings-sync ledgers forced off."""
    plan = copy.deepcopy(dict(config))
    redteam = plan.get("redteam")
    node = redteam.get("findings_sync") if isinstance(redteam, dict) else None
    if isinstance(node, dict):
        node["dispatch_ledger"] = ""
        node["grant_usage_ledger"] = ""
    return plan


def _tracker(config: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    """Effective tracker: the plan rows' tracker, else the configured one."""
    skipped = result.get("skipped_tracker")
    if isinstance(skipped, str) and skipped:
        return skipped
    plan = result.get("dispatch_plan")
    if isinstance(plan, list):
        for row in plan:
            if isinstance(row, dict) and isinstance(row.get("tracker"), str):
                return str(row["tracker"])
    redteam = config.get("redteam")
    node = redteam.get("findings_sync") if isinstance(redteam, dict) else None
    configured = node.get("tracker") if isinstance(node, dict) else None
    if isinstance(configured, str) and configured.strip():
        return configured.strip().lower()
    return DEFAULT_TRACKER


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """The would-sync plan from a dry tick with every ledger off. Writes nothing."""
    parse_flags(args, USAGE)
    reports = resolve_reports_dir(env, USAGE)
    try:
        from omega_prime.config import load_config
        from omega_prime.grokbot import findings_sync

        config = load_config()
        result = findings_sync.tick(
            _plan_config(config),
            reports_directory=reports,
            runner=_forbidden_runner,
            dispatch_dry_run=True,
            now=datetime.now(UTC).replace(microsecond=0),
        )
    except Exception as exc:
        raise Error(
            "plan_failed", {"usage": USAGE, "reason": type(exc).__name__}
        ) from None
    if result.get("error"):
        raise Error(
            "plan_failed",
            {"usage": USAGE, "reason": "tick-error", "detail": str(result["error"])},
        )
    pushed = [row for row in _list(result.get("pushed")) if isinstance(row, dict)]
    return {
        "tracker": _tracker(config, result),
        "disabled": bool(result.get("disabled")),
        "would_sync": [str(row.get("dedupe_hash", "")) for row in pushed],
        "skipped": len(_list(result.get("skipped"))),
        "quiet_held": _list(result.get("quiet_skipped")),
        "budget_held": _list(result.get("budget_held")),
        "grant_conflicts": _list(result.get("grant_conflicts")),
    }


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]
