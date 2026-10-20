# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Non-secret dashboard settings allowlist."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DashboardConfig:
    window_hours: float = 24.0
    quiet_minutes: float = 15.0
    fleet_enabled: bool = False


def load_dashboard_config(path: Path | None = None) -> DashboardConfig:
    """Load only explicitly allowlisted dashboard configuration values."""
    target = (
        path if path is not None else Path.home() / ".omega-prime" / "settings.json"
    )
    try:
        data: Any = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DashboardConfig()
    if not isinstance(data, dict) or not isinstance(data.get("dashboard"), dict):
        return DashboardConfig()
    values = data["dashboard"]
    hours, quiet = values.get("window_hours"), values.get("quiet_minutes")
    return DashboardConfig(
        hours
        if isinstance(hours, (int, float)) and not isinstance(hours, bool) and hours > 0
        else 24.0,
        quiet
        if isinstance(quiet, (int, float)) and not isinstance(quiet, bool) and quiet > 0
        else 15.0,
        values.get("fleet_enabled")
        if isinstance(values.get("fleet_enabled"), bool)
        else False,
    )
