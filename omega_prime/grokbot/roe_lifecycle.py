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

The ``readiness`` subcommand (batch-04 Feature H2) is the post-RoE-signing
check: it reports ``roe_state`` (``IDLE`` when the lifecycle window is
open, else ``BLOCKED``), a 2s-timeout TCP probe of the configured
``gateway_url``, and ``suites_loaded``/``suites_total`` from
``sidecar/cases`` ``load_suite`` (drafts excluded). ``ready_for_live`` is
true only when all three hold. It never starts an engagement.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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


#: Default live-gateway URL for the readiness probe (mirrors
#: ``redteam.gateway_url`` in :mod:`omega_prime.config`).
DEFAULT_GATEWAY_URL = "http://127.0.0.1:3333"

#: TCP connect timeout (seconds) for the gateway reachability probe.
GATEWAY_TIMEOUT_SECONDS = 2.0

#: Lifecycle states that count as authorized for the readiness check: the
#: window is open (mirrors the harness ``IDLE`` verdict, which likewise
#: ignores expiry proximity — only a closed window blocks).
READY_LIFECYCLE_STATES = ("valid", "expiring-7d", "expiring-1d")


def resolve_gateway_url(explicit: str | None = None) -> str:
    """Gateway URL for the readiness probe: flag wins, then config."""
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    try:
        from omega_prime.config import load_config  # deferred: keeps import light

        url = load_config().get("redteam", {}).get("gateway_url", "")
        if isinstance(url, str) and url.strip():
            return url.strip()
    except Exception:
        pass
    return DEFAULT_GATEWAY_URL


def gateway_reachable(
    gateway_url: str, timeout: float = GATEWAY_TIMEOUT_SECONDS
) -> bool:
    """TCP-connect probe of ``gateway_url`` host:port (no engagement)."""
    try:
        parts = urlsplit(gateway_url.strip())
    except Exception:
        return False
    host = parts.hostname or ""
    if not host:
        return False
    port = parts.port
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def suite_counts(cases_dir: str | Path | None = None) -> tuple[int, int]:
    """``(suites_loaded, suites_total)`` from ``sidecar/cases`` load_suite.

    Drafts are excluded from both counts (``load_suite`` never enters
    ``drafts/`` and skips ``status: draft`` docs; the file walk below
    applies the same rule to the total). Files that fail to parse count
    toward the total but not toward loaded.
    """
    from sidecar.simulation import (  # deferred: keeps the watcher stdlib-light
        cases_dir as default_cases_dir,
    )
    from sidecar.simulation import (
        load_case_file,
        load_suite,
    )

    resolved = Path(cases_dir) if cases_dir is not None else default_cases_dir()
    cases, _errors = load_suite(resolved)
    loaded = len(cases)
    try:
        files = sorted(resolved.glob("*.yaml"))
    except OSError:
        return loaded, loaded
    total = 0
    for path in files:
        try:
            doc = load_case_file(path)
        except Exception:
            total += 1
            continue
        if isinstance(doc, dict) and doc.get("status") == "draft":
            continue
        total += 1
    return loaded, total


def check_readiness(
    artifact_path: str | Path | None,
    now: datetime,
    *,
    gateway_url: str | None = None,
    cases_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Post-RoE-signing readiness: authorized + reachable + suites loaded.

    Returns ``{"roe_state", "gateway_reachable", "suites_loaded",
    "suites_total", "ready_for_live"}``. ``roe_state`` is ``"IDLE"`` when
    the lifecycle window is open and ``"BLOCKED"`` otherwise (harness
    vocabulary); ``ready_for_live`` is true only when the state is
    ``IDLE``, the gateway TCP probe succeeds, and at least one suite
    loaded. Reads local files and opens one TCP probe connection only —
    it never starts an engagement (no ``run_suite``, no transitions).
    """
    lifecycle = check_roe_lifecycle(artifact_path, now)
    roe_state = "IDLE" if lifecycle["state"] in READY_LIFECYCLE_STATES else "BLOCKED"
    reachable = gateway_reachable(resolve_gateway_url(gateway_url))
    loaded, total = suite_counts(cases_dir)
    return {
        "roe_state": roe_state,
        "gateway_reachable": reachable,
        "suites_loaded": loaded,
        "suites_total": total,
        "ready_for_live": bool(roe_state == "IDLE" and reachable and loaded > 0),
    }


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
    artifact_path: str | Path | None,
    now: datetime,
    audit_log: str | Path | None = None,
) -> dict[str, Any]:
    """Classify the RoE lifecycle state of ``artifact_path`` at ``now``.

    Reads no wall-clock time: ``now`` (UTC) is injected by the caller.
    Never verifies signatures and never raises on artifact problems —
    unparseable or unreadable artifacts report ``missing``/``invalid``.

    When ``audit_log`` is given and the grant is past ``effective_until``,
    a ``grant-expired`` row is appended to that JSONL log (see
    :mod:`omega_prime.redteam.roe_audit`). Audit writes never raise:
    failures are warned and skipped.
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
        if audit_log is not None and str(audit_log).strip():
            try:
                from omega_prime.redteam import roe_audit

                candidate = document.get("operator", "")
                claimed = candidate.strip() if isinstance(candidate, str) else ""
                roe_audit.log_expired(
                    audit_log,
                    path,
                    claimed,
                    f"effective_until {effective_until!r} is in the past",
                )
            except Exception:
                import warnings

                warnings.warn("roe audit log write failed; skipped")
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


def _normalize_artifact_list(
    artifacts: str | Path | Sequence[str | Path] | None,
) -> list[str]:
    """Artifact paths as a plain list (single path wraps to one element)."""
    if artifacts is None:
        return []
    if isinstance(artifacts, (str, Path)):
        return [str(artifacts)]
    if isinstance(artifacts, (list, tuple)):
        paths: list[str] = []
        for entry in artifacts:
            if not isinstance(entry, (str, Path)):
                raise TypeError(
                    "RoE artifact paths must be str or Path, "
                    f"got {type(entry).__name__}"
                )
            paths.append(str(entry))
        return paths
    raise TypeError(
        "RoE artifacts must be a path, a list of paths, or None, "
        f"got {type(artifacts).__name__}"
    )


def check_roe_lifecycle_all(
    artifacts: str | Path | Sequence[str | Path] | None,
    now: datetime,
    audit_log: str | Path | None = None,
) -> dict[str, Any]:
    """Aggregate lifecycle state across concurrent RoE grants.

    Each artifact is classified with :func:`check_roe_lifecycle`; the
    aggregate reports the least-healthy state (``STATES`` order), the
    minimum ``days_remaining`` across parseable windows, the earliest
    ``effective_until``, and the per-artifact breakdown. A single path
    (or ``None``) behaves like :func:`check_roe_lifecycle` plus an
    ``artifacts`` breakdown entry. Never verifies signatures.

    When ``audit_log`` is given, each expired grant appends a
    ``grant-expired`` row (audit writes never raise).
    """
    paths = _normalize_artifact_list(artifacts)
    if not paths:
        result = _missing()
        result["artifacts"] = []
        return result
    per_artifact = [
        {"path": path, **check_roe_lifecycle(path, now, audit_log=audit_log)}
        for path in paths
    ]
    worst = max(per_artifact, key=lambda row: STATES.index(row["state"]))
    days = [
        row["days_remaining"]
        for row in per_artifact
        if row["days_remaining"] is not None
    ]
    untils = [
        str(row["effective_until"]) for row in per_artifact if row["effective_until"]
    ]
    return {
        "state": worst["state"],
        "days_remaining": min(days) if days else None,
        "cause": worst["cause"],
        "effective_until": min(untils) if untils else None,
        "artifacts": per_artifact,
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
    """CLI: ``check`` (lifecycle) and ``readiness`` (live-gateway check)."""
    parser = argparse.ArgumentParser(
        description="RoE lifecycle watcher — warn before RoE expiry, never renew"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Print the current RoE lifecycle state")
    check.add_argument(
        "--artifact",
        dest="artifacts",
        action="append",
        required=True,
        help="Path to a RoE JSON artifact (overrides redteam.roe_file; "
        "repeat for concurrent grants)",
    )
    check.add_argument(
        "--now",
        default=None,
        help="ISO-8601 UTC override for the clock (scripting/tests)",
    )
    check.add_argument(
        "--json", action="store_true", help="Output machine-readable JSON"
    )
    check.add_argument(
        "--audit-log",
        default=None,
        help="Path to the RoE grant audit JSONL log: expired grants append "
        "a grant-expired row here and the log contents are included in "
        "the output (audit_log key with --json). Audit writes never raise.",
    )
    ready = sub.add_parser(
        "readiness", help="Post-RoE-signing live-gateway readiness check"
    )
    ready.add_argument(
        "--artifact",
        required=True,
        help="Path to the RoE JSON artifact (overrides redteam.roe_file)",
    )
    ready.add_argument(
        "--now",
        default=None,
        help="ISO-8601 UTC override for the clock (scripting/tests)",
    )
    ready.add_argument(
        "--gateway-url",
        default=None,
        help="Live-gateway URL probe target (default: redteam.gateway_url)",
    )
    ready.add_argument(
        "--cases-dir",
        default=None,
        help="Case directory override (default: sidecar/cases)",
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
        artifacts: list[str] = args.artifacts or []
        audit_log = args.audit_log
        if len(artifacts) == 1:
            result = check_roe_lifecycle(artifacts[0], now, audit_log=audit_log)
        else:
            result = check_roe_lifecycle_all(artifacts, now, audit_log=audit_log)
        if audit_log is not None and str(audit_log).strip():
            try:
                from omega_prime.redteam import roe_audit

                result["audit_log"] = roe_audit.read_log(audit_log)
            except Exception:
                import warnings

                warnings.warn("roe audit log read failed; skipped")
                result["audit_log"] = []
        if args.json:
            print(json.dumps(result, indent=2))
        elif len(artifacts) == 1:
            days = result["days_remaining"]
            days_text = f"{days:.2f}d remaining" if days is not None else "no window"
            until = result["effective_until"] or "unknown"
            print(f"roe: {result['state']} ({days_text}, until {until})")
            if audit_log is not None and str(audit_log).strip():
                entries = result.get("audit_log", [])
                print(f"audit log: {len(entries)} entries ({audit_log})")
                for entry in entries:
                    print(
                        f"  - {entry.get('timestamp', '?')} "
                        f"{entry.get('event', '?')} "
                        f"{entry.get('artifact_path', '?')}"
                    )
        else:
            days = result["days_remaining"]
            days_text = f"{days:.2f}d remaining" if days is not None else "no window"
            until = result["effective_until"] or "unknown"
            print(
                f"roe: {result['state']} "
                f"(worst of {len(artifacts)} artifacts, "
                f"{days_text}, until {until})"
            )
            for row in result["artifacts"]:
                row_days = row["days_remaining"]
                row_text = (
                    f"{row_days:.2f}d remaining"
                    if row_days is not None
                    else "no window"
                )
                print(f"  - {row['path']}: {row['state']} ({row_text})")
            if audit_log is not None and str(audit_log).strip():
                entries = result.get("audit_log", [])
                print(f"audit log: {len(entries)} entries ({audit_log})")
                for entry in entries:
                    print(
                        f"  - {entry.get('timestamp', '?')} "
                        f"{entry.get('event', '?')} "
                        f"{entry.get('artifact_path', '?')}"
                    )
        return _exit_code(result["state"])
    if args.command == "readiness":
        now = _parse_now_override(args.now)
        if now is None:
            print(
                f"error: --now is not a parseable datetime: {args.now!r}",
                file=sys.stderr,
            )
            return 2
        result = check_readiness(
            args.artifact,
            now,
            gateway_url=args.gateway_url,
            cases_dir=args.cases_dir,
        )
        print(json.dumps(result, indent=2))
        return 0 if result["ready_for_live"] else 3
    return 2


if __name__ == "__main__":
    sys.exit(main())
