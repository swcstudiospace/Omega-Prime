# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Optional read-only fleet coordinator collector."""

from __future__ import annotations

import json
import types
import urllib.request
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from omega_prime.credentials.broker import CredentialBroker
from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.redact import redact_value


class _FleetEndpointPolicy:
    """Allow credentials only for the configured coordinator host."""

    def __init__(self, host: str) -> None:
        self._host = host

    def allows_host(self, host: str) -> bool:
        return bool(self._host) and host == self._host


def _request(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(request, timeout=10.0) as response:
        return response.status, response.read(1_000_000)


def make_fleet_collector(
    *,
    enabled: bool = False,
    env_file: Path | None = None,
    transport: Callable[[str, Mapping[str, str]], tuple[int, bytes]] | None = None,
    timeout: float = 10.0,
) -> Callable[[], SourceResult]:
    """Create an opt-in fleet status and jobs collector."""
    send = transport or _request

    def collect() -> SourceResult:
        if not enabled:
            return SourceResult(
                "fleet",
                "disabled",
                error="fleet collector off (enable via settings.json dashboard.fleet_enabled)",
            )
        path = (
            env_file
            if env_file is not None
            else Path.home() / ".config" / "fleet" / "coordinator.env"
        )
        try:
            values: dict[str, str] = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key.strip()] = value
        except OSError:
            return SourceResult(
                "fleet", "unavailable", error="fleet coordinator not configured"
            )
        base = (
            values.get("FLEET_COORDINATOR_URL") or values.get("COORDINATOR_URL") or ""
        ).rstrip("/")
        try:
            host = urlsplit(base).hostname or ""
            provider = types.SimpleNamespace(
                name="fleet-coordinator-dashboard",
                requires_key=True,
                env_vars=("FLEET_COORDINATOR_TOKEN", "COORDINATOR_TOKEN"),
            )
            token = str(
                CredentialBroker(_FleetEndpointPolicy(host), env=values).key_for(
                    provider, base
                )
            ).strip()
        except Exception:
            token = ""
        if not base or not token:
            return SourceResult(
                "fleet", "unavailable", error="fleet coordinator not configured"
            )
        headers = {"Authorization": f"Bearer {token}"}
        try:

            def get(url: str) -> Any:
                status, body = send(url, headers)
                if status != 200:
                    raise RuntimeError("http")
                return json.loads(body[:1_000_000])

            status_data = get(f"{base}/status")
            jobs_data = get(f"{base}/jobs?limit=20")
            jobs = (
                jobs_data.get("jobs", []) if isinstance(jobs_data, dict) else jobs_data
            )
            if not isinstance(jobs, list):
                jobs = []
            now = datetime.now(UTC).timestamp()
            items: list[dict[str, Any]] = []
            hitl = 0
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                state = job.get("state")
                if "hitl" in str(state).lower():
                    hitl += 1
                updated = job.get("updated_at")
                age: float | None = None
                if isinstance(updated, (int, float)) and not isinstance(updated, bool):
                    age = max(0.0, now - float(updated))
                elif isinstance(updated, str):
                    with suppress(ValueError):
                        stamp = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        age = max(0.0, now - stamp.timestamp())
                items.append(
                    {
                        "kind": "fleet_job",
                        "job_id": str(job.get("id", job.get("job_id", ""))),
                        "state": state,
                        "worker": job.get("worker"),
                        "age_s": age,
                        "age_measured_at": datetime.fromtimestamp(
                            now, tz=UTC
                        ).isoformat(),
                    }
                )
            if isinstance(status_data, dict):
                extra_hitl = status_data.get("hitl", status_data.get("hitl_pending", 0))
                if isinstance(extra_hitl, int) and not isinstance(extra_hitl, bool):
                    hitl += extra_hitl
            return SourceResult(
                "fleet",
                "ok",
                items=redact_value(items, extra=(token,), env={}),
                extra={"hitl_count": hitl},
            )
        except Exception as exc:
            return SourceResult(
                "fleet", "error", error=f"fleet request failed: {type(exc).__name__}"
            )

    return collect
