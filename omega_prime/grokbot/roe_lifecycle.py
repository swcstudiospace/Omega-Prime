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
from collections.abc import Mapping, Sequence
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

                warnings.warn("roe audit log write failed; skipped", stacklevel=2)
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


def _grant_document(artifact: Any) -> dict[str, Any] | None:
    """One RoE artifact as a document dict, or ``None`` when unreadable."""
    if isinstance(artifact, dict):
        return artifact
    if isinstance(artifact, (str, Path)):
        path_text = str(artifact).strip()
        if not path_text:
            return None
        try:
            raw = Path(path_text).read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            loaded = json.loads(raw)
        except ValueError:
            return None
        return loaded if isinstance(loaded, dict) else None
    return None


def grant_trackers(artifact: Any) -> set[str] | None:
    """Tracker names covered by a RoE grant, or ``None`` for all.

    ``artifact`` is a parsed RoE document (dict), a path to a RoE JSON
    artifact file, or a list/tuple of either for operators with several
    concurrent grants (merged as a union — a tracker covered by any
    grant is allowed). Reads the document's ``covers`` key (a list of
    tracker names such as ``["slack", "jira"]``, matched
    case-insensitively after stripping whitespace; non-string entries
    are ignored).

    A missing or empty ``covers`` means all trackers are allowed
    (backward compatible) — as does an unreadable artifact or a grant
    set where any single grant allows all. Never raises.
    """
    if isinstance(artifact, (list, tuple)):
        if not artifact:
            return None
        merged: set[str] = set()
        for item in artifact:
            covered = grant_trackers(item)
            if covered is None:
                return None
            merged |= covered
        return merged or None
    document = _grant_document(artifact)
    if document is None:
        return None
    covers = document.get("covers")
    if covers is None:
        return None
    if not isinstance(covers, (list, tuple)):
        return None
    allowed = {str(name).strip().lower() for name in covers if isinstance(name, str)}
    allowed.discard("")
    return allowed or None


def record_grant_usage(
    ledger_path: str | Path | None,
    *,
    artifact_id: Any,
    tracker: Any,
    verdict: Any,
) -> None:
    """Append one per-grant tracker-gate decision to the usage ledger.

    Each call appends a single JSONL row ``{"ts", "artifact_id",
    "tracker", "verdict"}`` (keys sorted, one row per line) where
    ``ts`` is the current UTC time in ISO-8601 seconds precision.
    Parent directories are created as needed. Never raises: a blank
    path, an unwritable location, or any I/O failure is silently
    skipped (absence of a ledger row never blocks dispatch).
    """
    try:
        if not isinstance(ledger_path, (str, Path)):
            return None
        if not str(ledger_path).strip():
            return None
        path = Path(ledger_path)
        row = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "artifact_id": str(artifact_id),
            "tracker": str(tracker),
            "verdict": str(verdict),
        }
        if path.parent != Path(".") and str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:
        return None
    return None


def read_grant_usage(ledger_path: str | Path) -> list[dict[str, Any]]:
    """Read grant-usage ledger rows (oldest first).

    Returns the JSON-object rows of the ``ledger_path`` JSONL file in
    file order; blank lines and malformed/non-object lines are
    skipped. An unreadable or missing file returns ``[]``. Never
    raises.
    """
    try:
        text = Path(ledger_path).read_text(encoding="utf-8")
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except Exception:
                continue
            if isinstance(entry, dict):
                rows.append(entry)
    except Exception:
        return []
    return rows


#: Renewal-reminder thresholds (G11): days of window left at or below
#: which a ``renewal_reminders`` entry fires. Ordered widest first so a
#: single call returns every newly crossed threshold in the same order.
RENEWAL_THRESHOLDS_DAYS = (14.0, 7.0, 3.0, 1.0)

#: Heartbeat digest kind for a revoked RoE grant (J11). When
#: :func:`renewal_reminders` finds the artifact's id in the audit log's
#: ``grant-revoked`` rows, threshold reminders stop and a single entry
#: with this kind is returned instead — once per artifact id — so
#: operators see why reminders went silent.
ROE_REVOKED_KIND = "roe_revoked"


#: In-memory per-threshold fire record for :func:`renewal_reminders`,
#: keyed ``(artifact_id, threshold-label)``. Each threshold fires once
#: per artifact id for the life of the process (mirrors the heartbeat
#: ``job["last_fired"]`` dedupe shape, without persisting to the store).
_RENEWAL_FIRED: set[tuple[str, str]] = set()


def clear_renewal_reminders() -> None:
    """Reset the in-memory renewal-reminder fire record (tests/harness)."""
    _RENEWAL_FIRED.clear()


def _renewal_now(now: Any) -> datetime | None:
    """Normalize ``now`` to aware UTC; ``None`` reads the wall clock."""
    if now is None:
        return datetime.now(UTC)
    if isinstance(now, datetime):
        return _as_utc(now)
    if isinstance(now, (int, float)) and not isinstance(now, bool):
        try:
            return datetime.fromtimestamp(now, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(now, str):
        parsed = _parse_time(now)
        return _as_utc(parsed) if parsed is not None else None
    return None


def _renewal_artifact_id(
    artifact: Any, document: dict[str, Any], until_text: str | None
) -> str:
    """Stable dedupe id for a renewal artifact (never empty)."""
    if isinstance(artifact, (str, Path)):
        text = str(artifact).strip()
        if text:
            return text
    for key in ("artifact_id", "id"):
        candidate = document.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    operator = document.get("operator")
    if isinstance(operator, str) and operator.strip():
        return f"{operator.strip()}|{until_text}"
    return until_text or "unknown-artifact"


#: Document keys that may carry the RoE audit-log path inside an inline
#: artifact document (checked when no explicit ``audit_log`` is passed).
_RENEWAL_AUDIT_LOG_KEYS = ("audit_log", "audit_log_path", "roe_audit_log")


def _renewal_audit_log(artifact: Any, document: dict[str, Any], explicit: Any) -> Any:
    """Audit-log path for revocation suppression (``None`` disables it)."""
    if isinstance(explicit, (str, Path)) and str(explicit).strip():
        return explicit
    for key in _RENEWAL_AUDIT_LOG_KEYS:
        candidate = document.get(key)
        if isinstance(candidate, (str, Path)) and str(candidate).strip():
            return candidate
    return None


def _artifact_revoked(candidates: set[str], audit_log: Any) -> bool:
    """True when any id candidate has a ``grant-revoked`` audit row."""
    if not candidates or audit_log is None:
        return False
    try:
        from omega_prime.redteam import roe_audit

        revoked = set(roe_audit.list_revoked(audit_log))
    except Exception:
        return False
    return bool(candidates & revoked)


def renewal_reminders(
    artifact: Any, *, now: Any = None, audit_log: Any = None
) -> list[dict[str, Any]]:
    """Renewal reminders for a signed RoE artifact approaching expiry.

    ``artifact`` is a parsed RoE document (dict) or a path to a RoE JSON
    artifact file — or a list/tuple of either for operators with several
    concurrent grants (iterated and merged, deduped per (artifact_id,
    threshold-or-kind)); ``now`` defaults to the wall clock (UTC). Returns one
    reminder dict per newly crossed ``RENEWAL_THRESHOLDS_DAYS`` threshold
    (``14d``/``7d``/``3d``/``1d`` at or below that many days of window
    left). Each ``(artifact_id, threshold)`` fires once per process
    lifetime (tracked in :data:`_RENEWAL_FIRED`); already-fired thresholds
    are skipped. Fail-closed: a missing/malformed ``effective_until``
    (or any unreadable artifact) returns ``[]`` and never raises.
    Expired windows likewise return ``[]`` — the expiry watcher owns them.

    Revoked grants (J11) are suppressed the same way: when the artifact's
    id appears in a ``grant-revoked`` audit row (read-only scan of
    ``audit_log``, falling back to the document's ``audit_log`` /
    ``audit_log_path`` / ``roe_audit_log`` key for inline documents), no
    threshold reminder fires. Instead a single ``{"kind": "roe_revoked"}``
    marker is returned — once per artifact id via the same dedupe set —
    so heartbeat digests show why reminders stopped. A missing or
    unreadable log disables suppression (never crashes, never suppresses).
    """
    try:
        if isinstance(artifact, (list, tuple)):
            # L11 multi-grant form: iterate and merge per-artifact
            # reminders. Dedupe per (artifact_id, threshold-or-kind)
            # across the whole set so an operator with several
            # concurrent grants gets one marker per artifact, not per
            # check run (the shared _RENEWAL_FIRED set already
            # suppresses refires; the explicit pass below guards
            # same-call duplicates).
            merged: list[dict[str, Any]] = []
            for item in artifact:
                merged.extend(renewal_reminders(item, now=now, audit_log=audit_log))
            seen: set[tuple[str, str]] = set()
            unique: list[dict[str, Any]] = []
            for reminder in merged:
                if not isinstance(reminder, dict):
                    continue
                key = (
                    str(reminder.get("artifact_id")),
                    str(reminder.get("threshold", reminder.get("kind", "?"))),
                )
                if key in seen:
                    continue
                seen.add(key)
                unique.append(reminder)
            return unique
        current = _renewal_now(now)
        if current is None:
            return []
        if artifact is None:
            return []
        if isinstance(artifact, (str, Path)):
            path_text = str(artifact).strip()
            if not path_text:
                return []
            try:
                raw = Path(path_text).read_text(encoding="utf-8")
            except OSError:
                return []
            try:
                loaded = json.loads(raw)
            except ValueError:
                return []
            if not isinstance(loaded, dict):
                return []
            document = loaded
        elif isinstance(artifact, dict):
            document = artifact
        else:
            return []
        until_text = document.get("effective_until")
        end = _parse_time(until_text)
        if end is None:
            return []
        until_label: str | None = (
            until_text if isinstance(until_text, str) else str(end.isoformat())
        )
        days_remaining = (end - current).total_seconds() / 86400
        artifact_id = _renewal_artifact_id(
            artifact, document, until_label or "unknown-artifact"
        )
        log_path = _renewal_audit_log(artifact, document, audit_log)
        if log_path is not None:
            candidates = {artifact_id}
            if isinstance(artifact, (str, Path)) and str(artifact).strip():
                candidates.add(str(artifact).strip())
            for id_key in ("artifact_id", "id", "artifact_path", "path"):
                id_candidate = document.get(id_key)
                if isinstance(id_candidate, str) and id_candidate.strip():
                    candidates.add(id_candidate.strip())
            if _artifact_revoked(candidates, log_path):
                marker_key = (artifact_id, ROE_REVOKED_KIND)
                if marker_key in _RENEWAL_FIRED:
                    return []
                _RENEWAL_FIRED.add(marker_key)
                return [
                    {
                        "kind": ROE_REVOKED_KIND,
                        "artifact_id": artifact_id,
                        "effective_until": until_label,
                        "days_remaining": days_remaining,
                        "detail": (
                            f"RoE grant {artifact_id!r} revoked — renewal "
                            f"reminders suppressed (see audit log "
                            f"{log_path}) — re-sign to renew"
                        ),
                    }
                ]
        if days_remaining < 0:
            return []
        reminders: list[dict[str, Any]] = []
        for threshold in RENEWAL_THRESHOLDS_DAYS:
            if days_remaining > threshold:
                continue
            label = f"{int(threshold)}d"
            key = (artifact_id, label)
            if key in _RENEWAL_FIRED:
                continue
            _RENEWAL_FIRED.add(key)
            reminders.append(
                {
                    "artifact_id": artifact_id,
                    "threshold": label,
                    "threshold_days": threshold,
                    "days_remaining": days_remaining,
                    "effective_until": until_label,
                    "detail": (
                        f"RoE grant {artifact_id!r} expires in "
                        f"{days_remaining:.1f}d (renewal threshold {label}; "
                        f"effective_until {until_label}) — re-sign to renew"
                    ),
                }
            )
        return reminders
    except Exception:
        return []


#: Required operator step per watchdog state (K11). ``valid`` needs no
#: action; expiring grants must be re-signed before the window closes;
#: expired grants are done and archive; ``missing``/``invalid`` name the
#: recovery step. Fail-closed: unknown states map to ``"fix artifact"``.
WATCHDOG_ACTIONS = {
    "valid": "none",
    "expiring-7d": "re-sign",
    "expiring-1d": "re-sign",
    "expired": "archive",
    "missing": "provide artifact",
    "invalid": "fix artifact",
}


def watchdog_check(artifact: Any, *, now: Any = None) -> dict[str, Any]:
    """Independent RoE expiry watchdog (K11): flag expired grants anyway.

    ``artifact`` is a RoE document dict or a path to a RoE JSON artifact
    file; ``now`` defaults to the wall clock (UTC) and accepts the same
    shapes as :func:`renewal_reminders` (datetime, timestamp, ISO text).
    Returns ``{"state", "artifact_id", "effective_until", "action"}``
    (plus ``"path"`` when ``artifact`` was a filesystem path) where
    ``state`` reuses the :data:`STATES` vocabulary (``valid`` /
    ``expiring-7d`` / ``expiring-1d`` / ``expired`` / ``missing`` /
    ``invalid``) and ``action`` names the required operator step from
    :data:`WATCHDOG_ACTIONS` (``"re-sign"``, ``"archive"``,
    ``"provide artifact"``, ...).

    Runs without the heartbeat system: no dedupe set, no audit writes,
    no signature checks — pure ``effective_until`` classification so a
    DOWN heartbeat cannot silence an expiring grant. Fail-closed: a
    missing/unreadable artifact reports ``missing`` with action
    ``"provide artifact"``; anything unparseable reports ``invalid``;
    never raises.
    """
    try:
        path_text: str | None = None
        if isinstance(artifact, (str, Path)) and str(artifact).strip():
            path_text = str(artifact).strip()
        current = _renewal_now(now)
        if current is None:
            return {
                "state": "invalid",
                "artifact_id": "unknown-artifact",
                "effective_until": None,
                "action": WATCHDOG_ACTIONS["invalid"],
            }
        if artifact is None:
            return {
                "state": "missing",
                "artifact_id": "unknown-artifact",
                "effective_until": None,
                "action": WATCHDOG_ACTIONS["missing"],
            }
        if isinstance(artifact, (str, Path)):
            path_text = str(artifact).strip()
            if not path_text:
                return {
                    "state": "missing",
                    "artifact_id": "unknown-artifact",
                    "effective_until": None,
                    "action": WATCHDOG_ACTIONS["missing"],
                }
            try:
                raw = Path(path_text).read_text(encoding="utf-8")
            except OSError:
                return {
                    "state": "missing",
                    "artifact_id": path_text,
                    "path": path_text,
                    "effective_until": None,
                    "action": WATCHDOG_ACTIONS["missing"],
                }
            try:
                loaded = json.loads(raw)
            except ValueError:
                return {
                    "state": "invalid",
                    "artifact_id": path_text,
                    "path": path_text,
                    "effective_until": None,
                    "action": WATCHDOG_ACTIONS["invalid"],
                }
            if not isinstance(loaded, dict):
                return {
                    "state": "invalid",
                    "artifact_id": path_text,
                    "path": path_text,
                    "effective_until": None,
                    "action": WATCHDOG_ACTIONS["invalid"],
                }
            document = loaded
            if validate_schema(document):
                return {
                    "state": "invalid",
                    "artifact_id": path_text,
                    "path": path_text,
                    "effective_until": None,
                    "action": WATCHDOG_ACTIONS["invalid"],
                }
            until_text = document.get("effective_until")
            end = _parse_time(until_text)
            if end is None:
                return {
                    "state": "invalid",
                    "artifact_id": path_text,
                    "path": path_text,
                    "effective_until": until_text
                    if isinstance(until_text, str)
                    else None,
                    "action": WATCHDOG_ACTIONS["invalid"],
                }
            until_label: str | None = (
                until_text if isinstance(until_text, str) else end.isoformat()
            )
            artifact_id = _renewal_artifact_id(artifact, document, until_label)
        elif isinstance(artifact, dict):
            document = artifact
            until_text = document.get("effective_until")
            end = _parse_time(until_text)
            until_label = (
                until_text
                if isinstance(until_text, str)
                else (end.isoformat() if end is not None else None)
            )
            if end is None:
                return {
                    "state": "invalid",
                    "artifact_id": _renewal_artifact_id(
                        artifact, document, until_label or "unknown-artifact"
                    ),
                    "effective_until": until_label,
                    "action": WATCHDOG_ACTIONS["invalid"],
                }
            artifact_id = _renewal_artifact_id(
                artifact, document, until_label or "unknown-artifact"
            )
        else:
            return {
                "state": "invalid",
                "artifact_id": "unknown-artifact",
                "effective_until": None,
                "action": WATCHDOG_ACTIONS["invalid"],
            }
        days_remaining = (end - current).total_seconds() / 86400
        if current > end:
            state = "expired"
        elif days_remaining <= WARNING_1D_DAYS:
            state = "expiring-1d"
        elif days_remaining <= WARNING_7D_DAYS:
            state = "expiring-7d"
        else:
            state = "valid"
        state_result: dict[str, Any] = {
            "state": state,
            "artifact_id": artifact_id,
            "effective_until": until_label,
            "action": WATCHDOG_ACTIONS[state],
        }
        if path_text is not None:
            state_result["path"] = path_text
        return state_result
    except Exception:
        fallback: dict[str, Any] = {
            "state": "missing",
            "artifact_id": "unknown-artifact",
            "effective_until": None,
            "action": WATCHDOG_ACTIONS["missing"],
        }
        try:
            if isinstance(artifact, (str, Path)) and str(artifact).strip():
                fallback["path"] = str(artifact).strip()
        except Exception:
            pass
        return fallback


def watchdog_check_all(artifacts: Any, *, now: Any = None) -> list[dict[str, Any]]:
    """Expiry watchdog across concurrent RoE grants (L11).

    ``artifacts`` is a list/tuple of RoE document dicts and/or artifact
    paths (whatever :func:`watchdog_check` accepts); ``now`` defaults to
    the wall clock (UTC) and accepts the same shapes. Returns one state
    dict per artifact — same shape as :func:`watchdog_check`
    (``{"state", "artifact_id", "effective_until", "action"}``) — in
    input order. A single document/path (not in a list) wraps to one
    element; ``None``/an empty list returns ``[]``. Fail-closed: per-item
    garbage reports ``missing``/``invalid`` via :func:`watchdog_check`;
    never raises.
    """
    try:
        if artifacts is None:
            return []
        if isinstance(artifacts, (list, tuple)):
            return [watchdog_check(item, now=now) for item in artifacts]
        return [watchdog_check(artifacts, now=now)]
    except Exception:
        return []


#: Watchdog states that trigger a grant-expiry notice via the
#: configured findings-sync tracker (O11). A healthy (``valid``) grant
#: stays silent; ``missing``/``invalid`` are operator-input problems,
#: not expiry, so they stay digest-only.
EXPIRY_NOTICE_STATES = ("expiring-7d", "expiring-1d", "expired")


#: Operator-facing re-sign command path (P11). :func:`renewal_command`
#: echoes this path with the artifact path/id arg. The key file stays
#: operator-local, so the command carries only ``--artifact`` (pure
#: string building — never reads files, never raises).
RENEWAL_COMMAND_PREFIX = "/omega-redteam-roe-sign --artifact"

#: Watchdog-state keys carrying a re-signable artifact path (P11), in
#: priority order. ``artifact_id``/``id`` are the id fallback used by
#: :func:`renewal_command` on its own (never ``"unknown-artifact"``);
#: :func:`expiry_notice_message` embeds the ``Renew:`` line only when a
#: path key is present (absent path = omitted).
_RENEWAL_PATH_KEYS = ("path", "artifact_path", "artifact")
_RENEWAL_ID_KEYS = ("artifact_id", "id")


def _renewal_ref(artifact: Any, *, ids: bool) -> str:
    """Best re-sign ref for ``artifact``: path first, id when ``ids``."""
    try:
        if isinstance(artifact, (str, Path)):
            return str(artifact).strip()
        if isinstance(artifact, Mapping):
            for key in _RENEWAL_PATH_KEYS:
                candidate = artifact.get(key)
                if isinstance(candidate, (str, Path)) and str(candidate).strip():
                    return str(candidate).strip()
            if ids:
                for key in _RENEWAL_ID_KEYS:
                    candidate = artifact.get(key)
                    if (
                        isinstance(candidate, str)
                        and candidate.strip()
                        and candidate.strip() != "unknown-artifact"
                    ):
                        return candidate.strip()
    except Exception:
        pass
    return ""


def renewal_command(artifact: Any) -> str:
    """Deterministic one-line CLI command an operator runs to re-sign (P11).

    ``artifact`` is an artifact path (``str``/``Path``) or a mapping
    carrying one (``path``/``artifact_path``/``artifact``), falling back
    to its ``artifact_id``/``id`` — pure string building over the
    :data:`RENEWAL_COMMAND_PREFIX` sign path. Returns ``""`` when no
    usable path/id is present (including ``"unknown-artifact"``); never
    raises.
    """
    try:
        ref = _renewal_ref(artifact, ids=True)
        if not ref:
            return ""
        return f"{RENEWAL_COMMAND_PREFIX} {ref}"
    except Exception:
        return ""


def expiry_notice_message(artifact_state: Any) -> str:
    """Deterministic plain-text expiry notice for a watchdog state (O11).

    ``artifact_state`` is one :func:`watchdog_check` dict; the notice
    renders its ``artifact_id``, ``state``, ``effective_until``, and
    ``action`` in fixed order, so the same grant state always renders
    byte-identical text. When the state carries a path (see
    :func:`renewal_command`), a final ``Renew: <command>`` line names
    the one-line re-sign command; absent path = omitted. Fail-closed:
    missing/malformed fields render as ``"unknown"`` (an unknown
    ``state`` maps its action via :data:`WATCHDOG_ACTIONS`, defaulting
    to ``"fix artifact"``); never raises.
    """
    try:
        if isinstance(artifact_state, Mapping):
            fields: dict[str, Any] = dict(artifact_state)
        else:
            fields = {}

        def _field(name: str, fallback: str = "unknown") -> str:
            try:
                value = fields.get(name, fallback)
            except Exception:
                return fallback
            if value is None or (isinstance(value, str) and not value.strip()):
                return fallback
            try:
                return str(value)
            except Exception:
                return fallback

        state = _field("state")
        action_raw = fields.get("action", "")
        if isinstance(action_raw, str) and action_raw.strip():
            action = action_raw
        else:
            action = WATCHDOG_ACTIONS.get(state, WATCHDOG_ACTIONS["invalid"])
        renew_ref = _renewal_ref(fields, ids=False)
        lines = [
            "RoE grant expiry notice",
            f"artifact_id: {_field('artifact_id', 'unknown-artifact')}",
            f"state: {state}",
            f"effective_until: {_field('effective_until')}",
            f"action: {action}",
        ]
        if renew_ref:
            lines.append(f"Renew: {RENEWAL_COMMAND_PREFIX} {renew_ref}")
        return "\n".join(lines) + "\n"
    except Exception:
        return "RoE grant expiry notice\nstate: unknown\n"


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
    """CLI: ``check`` (lifecycle), ``readiness`` (live check), ``revoke``."""
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
    revoke = sub.add_parser("revoke", help="Revoke a RoE grant signature")
    revoke.add_argument(
        "--revoke",
        required=True,
        help="Artifact path or id of the grant to revoke",
    )
    revoke.add_argument(
        "--reason",
        required=True,
        help="Reason for the revocation (stored as the audit detail)",
    )
    revoke.add_argument(
        "--audit-log",
        default=None,
        help="Path to the RoE grant audit JSONL log: a grant-revoked row "
        "is appended here. Audit writes never raise.",
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

                warnings.warn("roe audit log read failed; skipped", stacklevel=2)
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
    if args.command == "revoke":
        target = args.revoke.strip() if isinstance(args.revoke, str) else ""
        reason = args.reason.strip() if isinstance(args.reason, str) else ""
        if not target or not reason:
            print(
                "error: revoke needs --revoke <artifact_path_or_id> "
                "and --reason <text>",
                file=sys.stderr,
            )
            return 2
        try:
            from omega_prime.redteam import roe_audit

            removed = roe_audit.revoke_artifact(
                target, audit_log=args.audit_log, detail=reason
            )
        except Exception as exc:
            print(f"error: revoke failed ({target!r}): {exc}", file=sys.stderr)
            return 3
        sig_note = "signature removed" if removed else "no signature file found"
        audit_note = (
            f"; audit log: {args.audit_log}"
            if args.audit_log is not None and str(args.audit_log).strip()
            else "; no audit log"
        )
        print(f"revoked {target} ({sig_note}; reason: {reason}{audit_note})")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
