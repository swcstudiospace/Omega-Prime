# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-report``: engagement digest from stored history. Read-only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from omega_prime.commands.redteam_common import (
    Error,
    load_engagement_module,
    parse_flags,
    resolve_reports_dir,
    validate_since,
    validate_threshold,
)

NAME = "omega-redteam-report"
USAGE = (
    "/omega-redteam-report [--summary] [--since <RFC3339>] "
    "[--threshold gate:<pct>|gate-risk:<score>]"
)
SUMMARY = (
    "Print the engagement digest (or one-line summary) from stored harness "
    "history. Read-only."
)
WHEN = "The user asks for the red-team engagement report, digest, or posture summary."


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Digest (or one-line summary) of the stored history. Writes nothing."""
    flags = parse_flags(
        args, USAGE, switches=("--summary",), options=("--since", "--threshold")
    )
    since = validate_since(flags["--since"], USAGE) if "--since" in flags else None
    threshold = (
        validate_threshold(flags["--threshold"], USAGE)
        if "--threshold" in flags
        else None
    )
    summary_only = "--summary" in flags
    if summary_only and threshold is not None:
        raise Error("bad_args", {"usage": USAGE})
    reports = resolve_reports_dir(env, USAGE)
    module = load_engagement_module(USAGE)
    failures = (module.ReportError, module.BaselineUsageError)
    try:
        if summary_only:
            found = module.summarize(reports, since)
            return {
                "mode": "summary",
                "state": str(found["state"]),
                "lines": [str(found["line"])],
                "gate": None,
            }
        # A gate needs history to read; a missing directory is plain no-reports.
        gate_spec = threshold if reports.is_dir() else None
        digest = module.build_digest(reports, since, threshold=gate_spec)
    except failures as exc:
        raise Error(
            "report_failed", {"usage": USAGE, "reason": type(exc).__name__}
        ) from None
    return {
        "mode": "digest",
        "state": str(digest["state"]),
        "lines": [str(line) for line in digest["lines"]],
        "gate": digest["gate"],
    }


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]
