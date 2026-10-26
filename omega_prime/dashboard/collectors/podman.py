# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Collect rootless Podman container state without issuing write commands."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.redact import redact_value


def _default_runner(argv: list[str], *, timeout: float) -> str:
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode != 0:
        raise OSError("sandbox containers unknown")
    return result.stdout


def make_podman_collector(
    *, runner: Callable[[list[str]], str] | None = None, timeout: float = 5.0
) -> Callable[[], SourceResult]:
    """Create a rootless Podman collector with a finite timeout."""

    def collect() -> SourceResult:
        try:
            output = (
                runner(["podman", "ps", "-a", "--format", "json"])
                if runner is not None
                else _default_runner(
                    ["podman", "ps", "-a", "--format", "json"], timeout=timeout
                )
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return SourceResult(
                "podman", "unavailable", error="sandbox containers unknown"
            )
        try:
            payload: Any = json.loads(output)
            if not isinstance(payload, list):
                raise ValueError("invalid container list")
            items: list[dict[str, Any]] = []
            for container in payload:
                if not isinstance(container, dict):
                    raise ValueError("invalid container record")
                names = container.get("Names")
                name = (
                    names[0]
                    if isinstance(names, list) and names and isinstance(names[0], str)
                    else None
                )
                if name is None:
                    raise ValueError("missing container name")
                created = container.get("Created") or container.get("CreatedAt")
                items.append(
                    {
                        "kind": "sandbox_container",
                        "name": name,
                        "state": container.get("State")
                        if isinstance(container.get("State"), str)
                        else None,
                        "status": container.get("Status")
                        if isinstance(container.get("Status"), str)
                        else None,
                        "created": created if isinstance(created, str) else None,
                    }
                )
        except (json.JSONDecodeError, TypeError, ValueError):
            return SourceResult(
                "podman",
                "unavailable",
                error="sandbox containers unknown: invalid output",
            )
        safe_items = redact_value(items)
        return SourceResult("podman", "ok" if safe_items else "empty", safe_items)

    return collect
