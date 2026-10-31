# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Local read-only task dashboard core."""

from __future__ import annotations

from omega_prime.dashboard.config import DashboardConfig, load_dashboard_config
from omega_prime.dashboard.model import (
    Owner,
    Snapshot,
    SourceResult,
    Task,
    normalize_tasks,
)
from omega_prime.dashboard.redact import redact_line, redact_value
from omega_prime.dashboard.status_file import TasksFileResult, read_tasks, write_tasks

__version__ = "1"

__all__ = [
    "DashboardConfig",
    "Owner",
    "Snapshot",
    "SourceResult",
    "Task",
    "TasksFileResult",
    "__version__",
    "load_dashboard_config",
    "normalize_tasks",
    "read_tasks",
    "redact_line",
    "redact_value",
    "write_tasks",
]
