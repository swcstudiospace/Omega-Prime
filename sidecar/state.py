"""Purple-team sidecar state machine (GAP-09) with audit-logged transitions.

Stdlib-only. Never imports ``omega_prime.redteam`` or ``redteam/`` (the
submodule) at runtime — this module is the authorization/state layer that
gates the red-team adapter, not a client of it.

States: ``IDLE`` (enabled, authorized, no run) → ``RUNNING`` (simulation run
active) → ``DEGRADED`` (consistency triangle or drift check failed at
runtime) → ``BLOCKED`` (kill switch set, RoE missing/invalid, or
``redteam.enabled`` false) → ``ERROR`` (gateway unreachable or protocol
fault).

Rules enforced by :func:`transition`:

- Recovery re-evaluates from ``IDLE``: ``BLOCKED`` and ``ERROR`` only exit
  to ``IDLE``. They may reaffirm themselves (``BLOCKED`` → ``BLOCKED``,
  ``ERROR`` → ``ERROR``, ``IDLE`` → ``IDLE``) when a fresh evaluation
  reaches the same verdict; every other self-transition is rejected.
- Entry into ``BLOCKED``/``ERROR`` is only legal via one of
  :data:`BLOCKED_CAUSES`. Any other cause, or any unknown state or cause,
  raises :class:`ValueError` loudly instead of failing silent.
- Every accepted transition is appended as one JSON row
  ``{from, to, cause, ts}`` to ``sidecar/reports/transitions.ndjson`` (the
  isolated artifacts dir, GAP-14 output area). ``ts`` is an RFC3339 UTC
  timestamp. There are no in-memory-only transitions (T-79-04).

The reports directory follows ``OMEGA_PRIME_SIDECAR_REPORTS_DIR`` when set
(hermetic tests point it at a tmp dir); otherwise it is the ``reports/``
directory next to this file.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

STATES = ("IDLE", "RUNNING", "DEGRADED", "BLOCKED", "ERROR")

TERMINAL_STATES = ("BLOCKED", "ERROR")

BLOCKED_CAUSES = (
    "kill-switch-set",
    "roe-missing",
    "roe-expired",
    "roe-invalid",
    "roe-key-in-file",
    "disabled",
    "drift-detected",
    "gateway-unreachable",
    "protocol-fault",
)

OPERATIONAL_CAUSES = (
    "run-started",
    "run-completed",
    "re-evaluated",
)

KNOWN_CAUSES = BLOCKED_CAUSES + OPERATIONAL_CAUSES

LEGAL_TRANSITIONS = {
    "IDLE": frozenset({"IDLE", "RUNNING", "DEGRADED", "BLOCKED", "ERROR"}),
    "RUNNING": frozenset({"IDLE", "DEGRADED", "BLOCKED", "ERROR"}),
    "DEGRADED": frozenset({"IDLE", "BLOCKED", "ERROR"}),
    "BLOCKED": frozenset({"IDLE", "BLOCKED"}),
    "ERROR": frozenset({"IDLE", "ERROR"}),
}

REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"
TRANSITIONS_FILENAME = "transitions.ndjson"


def reports_dir() -> Path:
    """Reports dir: env override wins, else ``sidecar/reports/``."""
    override = os.environ.get(REPORTS_ENV_VAR, "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "reports"


def transitions_path(directory: str | Path | None = None) -> Path:
    """Full path of the transitions audit log."""
    base = Path(directory) if directory is not None else reports_dir()
    return base / TRANSITIONS_FILENAME


def utc_now_rfc3339() -> str:
    """Current UTC time as an RFC3339 timestamp (``+00:00`` offset)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def transition(
    from_state: str,
    to_state: str,
    cause: str,
    *,
    directory: str | Path | None = None,
) -> dict[str, str]:
    """Move the harness from ``from_state`` to ``to_state`` for ``cause``.

    Validates states, cause, and edge, appends one JSON row
    ``{from, to, cause, ts}`` to the transitions log, and returns the row.
    Raises :class:`ValueError` on any unknown state, unknown cause, illegal
    edge, or non-allowlisted cause into ``BLOCKED``/``ERROR``.
    """
    if from_state not in STATES:
        raise ValueError(f"unknown sidecar state: {from_state!r}")
    if to_state not in STATES:
        raise ValueError(f"unknown sidecar state: {to_state!r}")
    if cause not in KNOWN_CAUSES:
        raise ValueError(f"unknown sidecar cause: {cause!r}")
    if to_state not in LEGAL_TRANSITIONS[from_state]:
        raise ValueError(
            f"illegal sidecar transition: {from_state} -> {to_state} (cause {cause!r})"
        )
    if to_state in TERMINAL_STATES and cause not in BLOCKED_CAUSES:
        raise ValueError(
            f"cause {cause!r} cannot enter {to_state}: "
            "BLOCKED/ERROR require one of "
            f"{', '.join(BLOCKED_CAUSES)}"
        )
    row = {
        "from": from_state,
        "to": to_state,
        "cause": cause,
        "ts": utc_now_rfc3339(),
    }
    path = transitions_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return row


__all__ = [
    "BLOCKED_CAUSES",
    "KNOWN_CAUSES",
    "LEGAL_TRANSITIONS",
    "OPERATIONAL_CAUSES",
    "REPORTS_ENV_VAR",
    "STATES",
    "TERMINAL_STATES",
    "TRANSITIONS_FILENAME",
    "reports_dir",
    "transition",
    "transitions_path",
    "utc_now_rfc3339",
]
