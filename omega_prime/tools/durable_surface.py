# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Register the durable-status tool.

Cron job runs persist a turn journal beside ``cron/jobs.json``. This tool
reads that journal and counts workflow checkpoints under ``workflows/``.
It does not start a turn. A missing journal is an explicit absent status,
not an error.
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega_prime.config import load_config
from omega_prime.durable.journal import TurnJournal
from omega_prime.grokbot.roe_lifecycle import check_roe_lifecycle
from omega_prime.prime.types import reject_extra
from omega_prime.tools.registry import ToolRegistry

DURABLE_TOOL_NAMES = ("durable_status",)

#: Env override for the engagement history source (mirrors the report script).
ENGAGEMENT_SIDECAR_REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"

#: Empty-digest line used when no reports exist yet (never an error).
ENGAGEMENT_EMPTY_LINE = "runs=0 pass=0% gaps=0 findings=+0/-0"

_ENGAGEMENT_SUMMARY_FN = None


def _engagement_summarize(reports_dir: Path) -> dict[str, Any] | None:
    """Load ``summarize()`` from ``scripts/engagement-report.py`` (cached)."""
    global _ENGAGEMENT_SUMMARY_FN
    if _ENGAGEMENT_SUMMARY_FN is None:
        script = (
            Path(__file__).resolve().parents[2] / "scripts" / "engagement-report.py"
        )
        try:
            spec = importlib.util.spec_from_file_location(
                "omega_prime_engagement_report", script
            )
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            return None
        _ENGAGEMENT_SUMMARY_FN = module.summarize
    try:
        return dict(_ENGAGEMENT_SUMMARY_FN(reports_dir, None))
    except Exception:
        return None


#: Remediation command per sidecar BLOCKED/DEGRADED cause, shown alongside
#: the sidecar state block. Unknown causes fall back to
#: ``_SIDECAR_GENERIC_HINT``. Hints name commands and env vars only —
#: never key material or artifact contents.
_SIDECAR_HINTS = {
    "kill-switch-set": "Unset OMEGA_PRIME_REDTEAM_KILL to re-enable the sidecar",
    "roe-missing": (
        "Create an artifact with /omega-redteam-roe-create, sign it with "
        "/omega-redteam-roe-sign, then set OMEGA_PRIME_REDTEAM_ROE_FILE"
    ),
    "roe-expired": "Re-sign with /omega-redteam-roe-sign",
    "roe-invalid": (
        "Re-create the artifact with /omega-redteam-roe-create and sign it "
        "with /omega-redteam-roe-sign"
    ),
    "roe-key-in-file": (
        "Move the key to a file and set OMEGA_PRIME_REDTEAM_ROE_KEY_FILE"
    ),
    "disabled": (
        "Set OMEGA_PRIME_REDTEAM_ENABLED=1 (or redteam.enabled) to enable the sidecar"
    ),
    "drift-detected": (
        "Run python -m omega_prime.grokbot.pin_bump --to <upstream-ref>"
    ),
    "gateway-unreachable": (
        "Start the red-team gateway on http://127.0.0.1:3333 and re-check"
    ),
    "protocol-fault": (
        "Inspect sidecar/reports/transitions.ndjson for the fault, "
        "then re-run the failing step"
    ),
}

#: Fallback hint for DEGRADED/BLOCKED causes with no specific entry above.
_SIDECAR_GENERIC_HINT = (
    "Inspect sidecar/reports/transitions.ndjson and re-run the failing check"
)

#: Sidecar states that carry a remediation hint.
_HINTED_SIDECAR_STATES = ("DEGRADED", "BLOCKED")


def _sidecar_block(root: Path) -> dict[str, Any]:
    """Additive sidecar-harness state block from the transitions audit log.

    State is the last row's target (``IDLE`` when the log is absent or
    empty); ``cause`` is the last row's cause. DEGRADED/BLOCKED states
    carry a ``hint`` with the remediation command for the cause. Never
    raises: unreadable history reports ``IDLE`` with no cause.
    """
    try:
        override = os.environ.get(ENGAGEMENT_SIDECAR_REPORTS_ENV_VAR, "").strip()
        reports_dir = Path(override) if override else root / "sidecar" / "reports"
        text = (reports_dir / "transitions.ndjson").read_text(encoding="utf-8")
    except Exception:
        return {"state": "IDLE", "cause": None}
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    if not rows:
        return {"state": "IDLE", "cause": None}
    last = rows[-1]
    state = str(last.get("to") or "IDLE")
    cause = last.get("cause")
    block: dict[str, Any] = {
        "state": state,
        "cause": cause if isinstance(cause, str) else None,
    }
    if state in _HINTED_SIDECAR_STATES:
        key = cause if isinstance(cause, str) else ""
        block["hint"] = _SIDECAR_HINTS.get(key, _SIDECAR_GENERIC_HINT)
    return block


def _roe_block(root: Path) -> dict[str, Any]:
    """Additive RoE lifecycle block read from ``redteam.roe_file`` at ``root``.

    Never raises: config or artifact problems report ``invalid``/``missing``
    states. Reports lifecycle state only — no signatures, no file contents.
    """
    try:
        config = load_config(root)
    except Exception:
        return {"state": "invalid", "days_remaining": None, "effective_until": None}
    node = config.get("redteam")
    artifact = node.get("roe_file", "") if isinstance(node, dict) else ""
    try:
        result = check_roe_lifecycle(artifact, datetime.now(UTC))
    except Exception:
        return {"state": "invalid", "days_remaining": None, "effective_until": None}
    return {
        "state": result["state"],
        "days_remaining": result["days_remaining"],
        "effective_until": result["effective_until"],
    }


def _engagement_block(root: Path) -> dict[str, Any]:
    """Additive engagement-posture block from the newest engagement report.

    Never raises: config/artifact problems report ``invalid``/``missing``/
    ``no-reports`` states. Reports posture only — no report file contents.
    """
    try:
        override = os.environ.get(ENGAGEMENT_SIDECAR_REPORTS_ENV_VAR, "").strip()
        reports_dir = Path(override) if override else root / "sidecar" / "reports"
        if not reports_dir.is_dir():
            return {
                "state": "no-reports",
                "line": ENGAGEMENT_EMPTY_LINE,
                "report": None,
                "generated_at": None,
            }
        summary = _engagement_summarize(reports_dir)
    except Exception:
        summary = None
    if not isinstance(summary, dict) or "line" not in summary:
        return {
            "state": "invalid",
            "line": ENGAGEMENT_EMPTY_LINE,
            "report": None,
            "generated_at": None,
        }
    return {
        "state": str(summary.get("state") or "invalid"),
        "line": str(summary.get("line") or ENGAGEMENT_EMPTY_LINE),
        "report": summary.get("report"),
        "generated_at": summary.get("generated_at"),
    }


def _reports_dir(root: Path) -> Path:
    """Sidecar reports directory, same override ``_engagement_block`` uses."""
    override = os.environ.get(ENGAGEMENT_SIDECAR_REPORTS_ENV_VAR, "").strip()
    return Path(override) if override else root / "sidecar" / "reports"


def _empty_cycle() -> dict[str, Any]:
    return {"id": None, "state": None, "age_seconds": None, "pending_tag": None}


def _empty_backlog() -> dict[str, Any]:
    return {"trackers": {"default": {"depth": 0, "oldest_age_seconds": None}}}


def _cycle_id(document: dict[str, Any], stem: str) -> str | None:
    """JSON cycle id when present, otherwise the checkpoint filename stem."""
    for candidate in (
        document.get("cycle_id"),
        document.get("id"),
        (document.get("state") or {}).get("cycle_id")
        if isinstance(document.get("state"), dict)
        else None,
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return stem or None


def _explicit_cycle_state(document: dict[str, Any]) -> str | None:
    """A status string when the checkpoint actually carries one."""
    for key in ("status", "outcome"):
        value = document.get(key)
        if isinstance(value, str) and value:
            return value
    nested = document.get("state")
    if isinstance(nested, str) and nested:
        return nested
    if isinstance(nested, dict):
        for key in ("status", "outcome"):
            value = nested.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _derived_cycle_state(document: dict[str, Any]) -> str | None:
    """Outcome from a real ``run_workflow`` checkpoint.

    Those files store ``done`` plus ``state.steps`` and ``state.blocked``.
    They do not store the result's ``status`` or ``outcome``. A finished
    cycle has recorded every step function. An unfinished one stopped
    before a failed or blocked step, which is not written into the file.
    """
    nested = document.get("state")
    if not isinstance(nested, dict):
        return None
    steps = nested.get("steps")
    if not isinstance(steps, list):
        return None
    records = [rec for rec in steps if isinstance(rec, dict)]
    if any(rec.get("status") == "error" for rec in records):
        return "failed"
    if nested.get("blocked") or any(rec.get("status") == "blocked" for rec in records):
        return "blocked"
    done = document.get("done")
    if isinstance(done, bool) or not isinstance(done, int) or done <= 0 or not records:
        return None
    from omega_prime.grokbot.purple_cycle import STEP_NAMES

    if done == len(STEP_NAMES) and len(records) == len(STEP_NAMES):
        return "completed"
    return "incomplete"


def _cycle_state(document: dict[str, Any]) -> str | None:
    """Cycle outcome from the checkpoint, or None when it has no state."""
    explicit = _explicit_cycle_state(document)
    if explicit is not None:
        return explicit
    return _derived_cycle_state(document)


def _pending_tag(reports_dir: Path) -> str | None:
    try:
        raw = json.loads(
            (reports_dir / "purple-cycle-pending.json").read_text(encoding="utf-8")
        )
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    tag = raw.get("tag")
    return tag if isinstance(tag, str) and tag else None


def _cycle_block(reports_dir: Path, *, now: float | None = None) -> dict[str, Any]:
    """Newest purple-cycle checkpoint, or all-None when absent or unreadable.

    Reads only. Missing directories, unreadable JSON, and stat failures stay
    empty — no paths and no exception text.
    """
    try:
        if not reports_dir.is_dir():
            return _empty_cycle()
        workflow_dir = reports_dir / "purple-cycle" / "workflows"
        paths = [path for path in workflow_dir.glob("*.json") if path.is_file()]
    except Exception:
        return _empty_cycle()
    if not paths:
        block = _empty_cycle()
        block["pending_tag"] = _pending_tag(reports_dir)
        return block
    try:
        newest = max(paths, key=lambda path: (path.stat().st_mtime, path.name))
        document = json.loads(newest.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("checkpoint is not an object")
        age = int((time.time() if now is None else now) - newest.stat().st_mtime)
    except Exception:
        return _empty_cycle()
    return {
        "id": _cycle_id(document, newest.stem),
        "state": _cycle_state(document),
        "age_seconds": age,
        "pending_tag": _pending_tag(reports_dir),
    }


def _parse_timestamp(raw: Any) -> float | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            return float(raw)
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    return None


def _row_timestamp(row: dict[str, Any]) -> float | None:
    """Finding time. ``record_finding`` stores RFC3339 in ``ts``."""
    for key in ("ts", "timestamp"):
        parsed = _parse_timestamp(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _lacks_issue_url(row: dict[str, Any]) -> bool:
    url = row.get("issue_url")
    return not (isinstance(url, str) and url.strip())


def _sync_backlog(reports_dir: Path, *, now: float | None = None) -> dict[str, Any]:
    """Count findings rows that still lack a non-empty ``issue_url``.

    Corrupt or missing ``findings.ndjson`` is depth 0. Row bodies are not
    returned.
    """
    empty = _empty_backlog()
    try:
        if not reports_dir.is_dir():
            return empty
        text = (reports_dir / "findings.ndjson").read_text(encoding="utf-8")
    except Exception:
        return empty
    clock = time.time() if now is None else now
    depth = 0
    oldest: float | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not _lacks_issue_url(row):
            continue
        depth += 1
        stamp = _row_timestamp(row)
        if stamp is not None and (oldest is None or stamp < oldest):
            oldest = stamp
    tracker: dict[str, Any] = {"depth": depth, "oldest_age_seconds": None}
    if oldest is not None:
        tracker["oldest_age_seconds"] = int(clock - oldest)
    return {"trackers": {"default": tracker}}


def _releases_block(root: Path) -> dict[str, Any]:
    """Additive upstream-releases block from the release-watch state file.

    Never raises: absent/corrupt state reports an empty ``new`` list with
    ``checked_at: None``. Posture only — no release contents.
    """
    try:
        from omega_prime.grokbot.release_watch import read_releases_block

        return read_releases_block(root)
    except Exception:
        return {"releases": {"new": [], "checked_at": None}}


def register_durable_surface_tools(
    registry: ToolRegistry, root: str | Path
) -> list[str]:
    """Register ``durable_status`` against ``root``."""
    base = Path(root)

    def durable_status(**extra: Any) -> dict:
        reject_extra(extra, what="durable_status")
        journal_path = base / "cron" / "turns.sqlite"
        workflow_dir = base / "workflows"
        checkpoints = (
            len(list(workflow_dir.glob("*.json"))) if workflow_dir.is_dir() else 0
        )
        roe = _roe_block(base)
        engagement = _engagement_block(base)
        releases = _releases_block(base)
        sidecar = _sidecar_block(base)
        reports = _reports_dir(base)
        cycle = _cycle_block(reports)
        sync_backlog = _sync_backlog(reports)
        if not journal_path.is_file():
            return {
                "journal": "absent",
                "path": str(journal_path),
                "open_runs": 0,
                "entry_count": 0,
                "last_run_id": None,
                "workflow_checkpoints": checkpoints,
                "roe": roe,
                "engagement": engagement,
                "releases": releases,
                "sidecar": sidecar,
                "cycle": cycle,
                "sync_backlog": sync_backlog,
            }
        found = TurnJournal(journal_path).status()
        return {
            "journal": "present",
            "path": str(journal_path),
            "open_runs": found["open_runs"],
            "entry_count": found["entry_count"],
            "last_run_id": found["last_run_id"],
            "workflow_checkpoints": checkpoints,
            "roe": roe,
            "engagement": engagement,
            "releases": releases,
            "sidecar": sidecar,
            "cycle": cycle,
            "sync_backlog": sync_backlog,
        }

    registry.register(
        "durable_status",
        "Report the cron turn journal, workflow checkpoint count, RoE lifecycle, "
        "and purple-team engagement posture digest.",
        {"type": "object", "properties": {}, "required": []},
        durable_status,
    )
    return list(DURABLE_TOOL_NAMES)


__all__ = [
    "DURABLE_TOOL_NAMES",
    "ENGAGEMENT_EMPTY_LINE",
    "ENGAGEMENT_SIDECAR_REPORTS_ENV_VAR",
    "register_durable_surface_tools",
]
