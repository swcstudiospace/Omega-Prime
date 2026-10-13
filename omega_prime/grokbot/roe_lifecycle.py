# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""RoE lifecycle watcher — expiry warnings with no auto-renewal (PLAN-F6).

Watches the configured rules-of-engagement artifact and classifies its
lifecycle state from the ``effective_until`` bound alone:

- ``valid`` — healthy, more than 7 days of window left (silent).
- ``expiring-7d`` — 7 or fewer days remain (warn, once per UTC day).
- ``expiring-1d`` — 1 or fewer days remain (warn, once per UTC day).
- ``expired`` — ``now`` is past ``effective_until`` (notice, once per day).
- ``missing`` — no artifact path configured or the file is unreadable.
- ``invalid`` — unparseable JSON, schema mismatch, or an unparseable
  ``effective_until`` bound. Never reported as ``expired``.

Signature verification (``verify_signature`` / ``authorize(key=...)``) is
deliberately NOT part of the watcher: expiry warnings must fire even for
an unsigned artifact; tamper verdicts stay with ``authorize``. Warnings
never touch the state machine — only actual expiry drives the
pre-existing ``roe-expired`` → BLOCKED transition. Nothing here writes
the artifact or its ``.sig``: there is no auto-renewal and no signing.

The checker takes an injected ``now`` (UTC) so tests stay hermetic; the
only wall-clock read is the CLI default when ``--now`` is omitted.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega_prime.redteam.roe import (
    CAUSE_EXPIRED,
    CAUSE_INVALID,
    CAUSE_MISSING,
    _parse_time,
    validate_schema,
)

#: Days of window left at which the 7-day warning starts firing.
WARNING_7D_DAYS = 7.0

#: Days of window left at which the 1-day warning starts firing.
WARNING_1D_DAYS = 1.0

#: Lifecycle states ordered from healthiest to least healthy.
STATES = ("valid", "expiring-7d", "expiring-1d", "expired", "missing", "invalid")

_CAUSE_OK = "ok"


def _as_utc(now: datetime) -> datetime:
    """Return ``now`` as a timezone-aware UTC datetime."""
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _missing() -> dict[str, Any]:
    return {
        "state": "missing",
        "days_remaining": None,
        "cause": CAUSE_MISSING,
        "effective_until": None,
    }


def _invalid() -> dict[str, Any]:
    return {
        "state": "invalid",
        "days_remaining": None,
        "cause": CAUSE_INVALID,
        "effective_until": None,
    }


def check_roe_lifecycle(
    artifact_path: str | Path | None, now: datetime
) -> dict[str, Any]:
    """Classify the RoE lifecycle state of ``artifact_path`` at ``now``.

    Reads no wall-clock time: ``now`` (UTC) is injected by the caller.
    Never verifies signatures and never raises on artifact problems —
    unparseable or unreadable artifacts report ``missing``/``invalid``.
    """
    current = _as_utc(now)
    path = str(artifact_path or "").strip()
    if not path:
        return _missing()
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return _missing()
    try:
        document = json.loads(raw)
    except ValueError:
        return _invalid()
    if validate_schema(document):
        return _invalid()
    end = _parse_time(document.get("effective_until"))
    if end is None:
        return _invalid()
    days_remaining = (end - current).total_seconds() / 86400
    effective_until = document.get("effective_until")
    if current > end:
        return {
            "state": "expired",
            "days_remaining": days_remaining,
            "cause": CAUSE_EXPIRED,
            "effective_until": effective_until,
        }
    if days_remaining <= WARNING_1D_DAYS:
        state = "expiring-1d"
    elif days_remaining <= WARNING_7D_DAYS:
        state = "expiring-7d"
    else:
        state = "valid"
    return {
        "state": state,
        "days_remaining": days_remaining,
        "cause": _CAUSE_OK,
        "effective_until": effective_until,
    }


def _parse_now_override(text: str | None) -> datetime | None:
    """Parse the ``--now`` override, or ``None`` when it is unparseable."""
    if text is None:
        return datetime.now(UTC)
    parsed = _parse_time(text)
    if parsed is None:
        return None
    return _as_utc(parsed)


def _exit_code(state: str) -> int:
    """0 for valid/expiring (warnings are not failures), else 3."""
    if state in ("valid", "expiring-7d", "expiring-1d"):
        return 0
    return 3


def main(argv: list[str] | None = None) -> int:
    """CLI: ``check --artifact <path> [--now <ISO-8601 UTC>] [--json]``."""
    parser = argparse.ArgumentParser(
        description="RoE lifecycle watcher — warn before RoE expiry, never renew"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Print the current RoE lifecycle state")
    check.add_argument(
        "--artifact",
        required=True,
        help="Path to the RoE JSON artifact (overrides redteam.roe_file)",
    )
    check.add_argument(
        "--now",
        default=None,
        help="ISO-8601 UTC override for the clock (scripting/tests)",
    )
    check.add_argument(
        "--json", action="store_true", help="Output machine-readable JSON"
    )
    args = parser.parse_args(argv)

    if args.command == "check":
        now = _parse_now_override(args.now)
        if now is None:
            print(
                f"error: --now is not a parseable datetime: {args.now!r}",
                file=sys.stderr,
            )
            return 2
        result = check_roe_lifecycle(args.artifact, now)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            days = result["days_remaining"]
            days_text = f"{days:.2f}d remaining" if days is not None else "no window"
            until = result["effective_until"] or "unknown"
            print(f"roe: {result['state']} ({days_text}, until {until})")
        return _exit_code(result["state"])
    return 2


if __name__ == "__main__":
    sys.exit(main())
