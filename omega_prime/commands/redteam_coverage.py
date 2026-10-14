# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-coverage``: per-class detection coverage. Read-only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from omega_prime.commands.redteam_common import (
    Error,
    load_engagement_module,
    parse_flags,
    resolve_reports_dir,
    validate_since,
)

NAME = "omega-redteam-coverage"
USAGE = "/omega-redteam-coverage [--jsonl] [--since <RFC3339>]"
SUMMARY = "Show the per-class detection coverage matrix from stored history. Read-only."
WHEN = "The user asks for red-team detection coverage or the coverage matrix."


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Coverage rows (one per technique class); ``--jsonl`` adds the JSONL text."""
    flags = parse_flags(args, USAGE, switches=("--jsonl",), options=("--since",))
    since = validate_since(flags["--since"], USAGE) if "--since" in flags else None
    reports = resolve_reports_dir(env, USAGE)
    module = load_engagement_module(USAGE)
    rows: list[dict[str, Any]] = []
    if reports.is_dir():
        try:
            rows = [dict(row) for row in module.coverage_jsonl_rows(reports, since)]
        except (module.ReportError, module.BaselineUsageError) as exc:
            raise Error(
                "report_failed", {"usage": USAGE, "reason": type(exc).__name__}
            ) from None
    result: dict[str, Any] = {"rows": rows, "total": len(rows)}
    if "--jsonl" in flags:
        result["jsonl"] = module.render_jsonl(rows)
    return result


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]
