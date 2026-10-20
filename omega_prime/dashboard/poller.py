# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Shared scheduled collector poller for the task dashboard."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from omega_prime.dashboard.model import SourceResult, normalize_tasks
from omega_prime.dashboard.redact import redact_value

LOCAL_SOURCES = 3.0
HERDR_PODMAN = 5.0
FLEET = 30.0
GITHUB = 60.0
_STATUS_EXTRA = {
    "agentos": ("routing_summary", "state_entries"),
    "command_log": ("count", "last_exit_codes"),
}


@dataclass(frozen=True)
class CollectorSpec:
    """A named blocking collector and its scheduling bounds."""

    source: str
    fn: Callable[[], SourceResult]
    interval_s: float
    timeout_s: float


class DashboardPoller:
    """Run blocking collectors concurrently and cache one shared snapshot."""

    def __init__(
        self,
        specs: list[CollectorSpec],
        *,
        quiet_minutes: float = 15.0,
        window_hours: float = 24.0,
        clock: Callable[[], datetime] | None = None,
        tick_s: float = 0.25,
    ) -> None:
        self.specs = list(specs)
        self.quiet_minutes = quiet_minutes
        self.window_hours = window_hours
        self.clock = clock or (lambda: datetime.now(UTC))
        self.tick_s = tick_s
        self._results: dict[str, SourceResult] = {}
        self._status: dict[str, dict[str, Any]] = {}
        self._snapshot: dict[str, Any] | None = None
        self._hash: str | None = None
        self._changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._next_due: dict[str, float] = {}
        self._in_flight: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        """Start the background polling loop once."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever())

    async def stop(self) -> None:
        """Cancel and await the polling loop and its in-flight collectors."""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        tasks = tuple(self._in_flight)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run_forever(self) -> None:
        """Run scheduled collectors until cancelled."""
        while True:
            await asyncio.sleep(self.tick_s)
            now = time.monotonic()
            for spec in self.specs:
                if now < self._next_due.get(spec.source, 0.0):
                    continue
                self._next_due[spec.source] = now + max(0.0, spec.interval_s)
                task = asyncio.create_task(self._run_one(spec))
                self._in_flight.add(task)
                task.add_done_callback(self._in_flight.discard)

    async def _run_one(self, spec: CollectorSpec) -> None:
        await self._collect(spec)
        self._maybe_build()

    async def _run_specs(self, specs: list[CollectorSpec]) -> None:
        await asyncio.gather(*(self._run_one(spec) for spec in specs))

    def _maybe_build(self) -> None:
        if not all(spec.source in self._results for spec in self.specs):
            return
        try:
            self._status.pop("normalization", None)
            self._build_snapshot()
        except Exception as exc:
            self._status["normalization"] = {
                "status": "error",
                "error": f"normalize failed: {type(exc).__name__}",
            }

    async def _collect(self, spec: CollectorSpec) -> None:
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(spec.fn), timeout=spec.timeout_s
            )
            if not isinstance(result, SourceResult):
                raise TypeError("collector returned an invalid result")
            if result.source != spec.source:
                result.source = spec.source
        except Exception as exc:
            result = SourceResult(
                source=spec.source, status="error", error=type(exc).__name__
            )
        took_ms = round((time.perf_counter() - started) * 1000.0, 2)
        self._results[spec.source] = result
        status: dict[str, Any] = {"status": result.status, "took_ms": took_ms}
        if result.error is not None:
            status["error"] = result.error
        if result.detail is not None:
            status["detail"] = result.detail
        for key in _STATUS_EXTRA.get(spec.source, ()):
            if key in result.extra:
                status[key] = redact_value(result.extra[key])
        self._status[spec.source] = status

    def _build_snapshot(self) -> None:
        now = self.clock()
        normalized_results: dict[str, SourceResult] = dict(self._results)
        for result in self._results.values():
            for item in result.items:
                kind = item.get("kind")
                if isinstance(kind, str):
                    bucket = normalized_results.get(kind)
                    if bucket is None:
                        bucket = SourceResult(kind, result.status)
                        normalized_results[kind] = bucket
                    bucket.items.append(item)
        normalized = normalize_tasks(
            normalized_results,
            now=now,
            quiet_minutes=self.quiet_minutes,
            window_hours=self.window_hours,
        ).to_dict()
        value = {
            "generated_at": now.isoformat(),
            "sources": self.sources_status(),
            **normalized,
        }
        safe = redact_value(value)
        canonical = dict(safe)
        canonical.pop("generated_at", None)
        canonical_sources = canonical.get("sources", {})
        if isinstance(canonical_sources, dict):
            canonical["sources"] = {
                name: {key: item for key, item in state.items() if key != "took_ms"}
                for name, state in canonical_sources.items()
            }
        digest = hashlib.sha256(
            json.dumps(
                canonical, sort_keys=True, separators=(",", ":"), default=str
            ).encode()
        ).hexdigest()
        self._snapshot = safe
        if digest != self._hash:
            self._hash = digest
            self._changed.set()

    def snapshot(self) -> dict[str, Any] | None:
        """Return the most recently built, redacted snapshot."""
        return self._snapshot

    def snapshot_hash(self) -> str | None:
        """Return the canonical content hash of the cached snapshot."""
        return self._hash

    def sources_status(self) -> dict[str, dict[str, Any]]:
        """Return per-source status and timing metadata."""
        return redact_value(self._status)

    async def wait_for_update(self, timeout: float) -> bool:
        """Wait until snapshot content changes, returning false on timeout."""
        if self._changed.is_set():
            self._changed.clear()
            return True
        try:
            await asyncio.wait_for(self._changed.wait(), timeout=timeout)
        except TimeoutError:
            return False
        self._changed.clear()
        return True

    async def refresh_now(self) -> None:
        """Run every collector immediately and rebuild the shared snapshot."""
        await self._run_specs(self.specs)
        now = time.monotonic()
        for spec in self.specs:
            self._next_due[spec.source] = now + max(0.0, spec.interval_s)
