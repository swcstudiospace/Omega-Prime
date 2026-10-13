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
import os
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
