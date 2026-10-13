# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Register the durable-status tool.

Cron job runs persist a turn journal beside ``cron/jobs.json``. This tool
reads that journal and counts workflow checkpoints under ``workflows/``.
It does not start a turn. A missing journal is an explicit absent status,
not an error.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega_prime.config import load_config
from omega_prime.durable.journal import TurnJournal
from omega_prime.grokbot.roe_lifecycle import check_roe_lifecycle
from omega_prime.prime.types import reject_extra
from omega_prime.tools.registry import ToolRegistry

DURABLE_TOOL_NAMES = ("durable_status",)


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
        if not journal_path.is_file():
            return {
                "journal": "absent",
                "path": str(journal_path),
                "open_runs": 0,
                "entry_count": 0,
                "last_run_id": None,
                "workflow_checkpoints": checkpoints,
                "roe": roe,
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
        }

    registry.register(
        "durable_status",
        "Report the cron turn journal and workflow checkpoint count.",
        {"type": "object", "properties": {}, "required": []},
        durable_status,
    )
    return list(DURABLE_TOOL_NAMES)


__all__ = ["DURABLE_TOOL_NAMES", "register_durable_surface_tools"]
