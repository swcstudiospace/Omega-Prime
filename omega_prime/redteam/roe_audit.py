# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Append-only RoE grant audit log (JSONL).

Every grant lifecycle event — creation (:func:`sign_artifact`), expiry
(past ``effective_until`` observed by the lifecycle watcher), and
revocation (:func:`revoke_artifact`) — appends one JSON line::

    {"timestamp": ..., "event": ..., "artifact_path": ...,
     "operator": ..., "detail": ...}

The log is append-only: writers open the file with ``"a"`` and never
truncate or rewrite it. All writers never raise — log-write failures
are emitted with :mod:`warnings` and skipped so auditing can never
block a grant operation. Readers likewise never raise: a missing file
reads as ``[]`` and corrupt lines are skipped.

Stdlib only.
"""

from __future__ import annotations

import json
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Grant was signed (``sign_artifact``).
EVENT_CREATED = "grant-created"
#: Grant observed past ``effective_until`` (lifecycle watcher).
EVENT_EXPIRED = "grant-expired"
#: Grant signature was revoked (``revoke_artifact``).
EVENT_REVOKED = "grant-revoked"

EVENTS = (EVENT_CREATED, EVENT_EXPIRED, EVENT_REVOKED)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def append_event(
    log_path: str | Path | None,
    event: str,
    artifact_path: str | Path = "",
    operator: str = "",
    detail: str = "",
    timestamp: str | None = None,
) -> dict[str, Any] | None:
    """Append one audit row; never raises.

    Returns the stored row on success, ``None`` when the path is empty
    or the write failed (a warning is emitted in the failure case).
    """
    if log_path is None or not str(log_path).strip():
        return None
    row = {
        "timestamp": timestamp or _utc_now_iso(),
        "event": event,
        "artifact_path": str(artifact_path or ""),
        "operator": operator or "",
        "detail": detail or "",
    }
    try:
        target = Path(log_path)
        if target.parent != Path("") and str(target.parent) not in ("", "."):
            target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError as exc:
        warnings.warn(f"roe audit log write failed ({log_path!r}): {exc}")
        return None
    return row


def log_created(
    log_path: str | Path | None,
    artifact_path: str | Path = "",
    operator: str = "",
    detail: str = "",
    timestamp: str | None = None,
) -> dict[str, Any] | None:
    """Append a grant-created row; never raises."""
    return append_event(
        log_path, EVENT_CREATED, artifact_path, operator, detail, timestamp
    )


def log_expired(
    log_path: str | Path | None,
    artifact_path: str | Path = "",
    operator: str = "",
    detail: str = "",
    timestamp: str | None = None,
) -> dict[str, Any] | None:
    """Append a grant-expired row; never raises."""
    return append_event(
        log_path, EVENT_EXPIRED, artifact_path, operator, detail, timestamp
    )


def log_revoked(
    log_path: str | Path | None,
    artifact_path: str | Path = "",
    operator: str = "",
    detail: str = "",
    timestamp: str | None = None,
) -> dict[str, Any] | None:
    """Append a grant-revoked row; never raises."""
    return append_event(
        log_path, EVENT_REVOKED, artifact_path, operator, detail, timestamp
    )


def revoke_artifact(
    artifact: str | Path,
    audit_log: str | Path | None = None,
    operator: str = "",
    detail: str = "",
) -> bool:
    """Revoke a grant by removing its detached ``.sig``; never raises.

    Appends a ``grant-revoked`` row to ``audit_log`` when given (log
    failures are warned and skipped). Returns ``True`` when a signature
    file was removed, ``False`` otherwise (including on error).
    """
    from omega_prime.redteam.roe import signature_path

    path = str(artifact or "")
    try:
        document_operator = operator
        if not document_operator and path:
            try:
                raw = Path(path).read_text(encoding="utf-8")
                document = json.loads(raw)
                if isinstance(document, dict):
                    candidate = document.get("operator", "")
                    if isinstance(candidate, str):
                        document_operator = candidate.strip()
            except (OSError, ValueError):
                pass
        removed = False
        if path:
            try:
                sig = signature_path(path)
                if sig.is_file():
                    sig.unlink()
                    removed = True
            except OSError as exc:
                warnings.warn(f"roe revoke failed ({path!r}): {exc}")
                return False
        if audit_log is not None and str(audit_log).strip():
            log_revoked(
                audit_log,
                path,
                document_operator,
                detail or "signature revoked",
            )
        return removed
    except Exception as exc:  # never block callers on audit/revoke bookkeeping
        warnings.warn(f"roe revoke failed ({path!r}): {exc}")
        return False


def read_log(log_path: str | Path | None) -> list[dict[str, Any]]:
    """Read audit rows; never raises.

    Missing/unreadable files yield ``[]``; corrupt lines are skipped.
    """
    if log_path is None or not str(log_path).strip():
        return []
    try:
        text = Path(log_path).read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            rows.append(record)
    return rows


__all__ = [
    "EVENTS",
    "EVENT_CREATED",
    "EVENT_EXPIRED",
    "EVENT_REVOKED",
    "append_event",
    "log_created",
    "log_expired",
    "log_revoked",
    "read_log",
    "revoke_artifact",
]
