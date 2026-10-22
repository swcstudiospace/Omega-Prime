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
from pathlib import Path
from typing import Any

from omega_prime.dashboard.model import SourceResult, normalize_tasks
from omega_prime.dashboard.redact import redact_value
from omega_prime.dashboard.stale import StaleChecker
from omega_prime.dashboard.terminal import resolve_task_pane

LOCAL_SOURCES = 3.0
HERDR_PODMAN = 5.0
FLEET = 30.0
GITHUB = 60.0
# Pane evidence older than this is treated as unavailable rather than trusted.
PANE_EVIDENCE_MAX_AGE = 4 * HERDR_PODMAN
_STATUS_EXTRA = {
    "agentos": ("routing_summary", "state_entries"),
    "command_log": ("count", "last_exit_codes"),
    "github": ("command_center",),
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
        tick_s: float = 0.25,
        clock: Callable[[], datetime] | None = None,
        task_ids_path: Path | None = None,
        stale_checker: StaleChecker | None = None,
    ) -> None:
        self.task_ids_path = task_ids_path
        self.stale_checker = stale_checker or StaleChecker()
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
        self._running: set[str] = set()
        self._build_lock = asyncio.Lock()
        self._build_pending = False
        self._build_task: asyncio.Task[None] | None = None
        # Herdr pane evidence for stale checks is refreshed on its own task so
        # a slow or hung `herdr pane list` never holds back a snapshot build.
        self._panes: dict[str, dict[str, Any]] | None = None
        self._panes_at: float | None = None
        self._panes_task: asyncio.Task[None] | None = None

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
                if spec.source in self._running:
                    continue
                self._next_due[spec.source] = now + max(0.0, spec.interval_s)
                task = asyncio.create_task(self._run_one(spec))
                self._in_flight.add(task)
                task.add_done_callback(self._in_flight.discard)

    async def _run_one(self, spec: CollectorSpec) -> None:
        self._running.add(spec.source)
        straggler: asyncio.Future[SourceResult] | None = None
        try:
            straggler = await self._collect(spec)
        finally:
            if straggler is None or straggler.done():
                self._running.discard(spec.source)
            else:
                # A timeout cannot stop the worker thread, so the source stays
                # occupied until that call really returns; the scheduler never
                # starts a second overlapping call into the same collector.
                straggler.add_done_callback(
                    lambda worker, source=spec.source: self._release(source, worker)
                )
        # Collection is done; the source may be rescheduled while the build
        # (and its possibly slow Herdr pane check) runs.
        self._request_build()

    def _release(self, source: str, worker: asyncio.Future[SourceResult]) -> None:
        self._running.discard(source)
        if not worker.cancelled():
            worker.exception()  # retrieved: a late failure is already reported

    def _request_build(self) -> asyncio.Task[None]:
        """Coalesce build requests into one worker sharing each pane check.

        Any number of collectors finishing during a slow build schedule just
        one follow-up build over the latest results, not one build each.
        """
        self._build_pending = True
        if self._build_task is None or self._build_task.done():
            self._build_task = asyncio.create_task(self._drain_builds())
            self._in_flight.add(self._build_task)
            self._build_task.add_done_callback(self._in_flight.discard)
        return self._build_task

    async def _drain_builds(self) -> None:
        while self._build_pending:
            self._build_pending = False
            await self._maybe_build()

    async def _run_specs(self, specs: list[CollectorSpec]) -> None:
        await asyncio.gather(*(self._run_one(spec) for spec in specs))
        if self._build_task is not None:
            await self._build_task

    def _pane_evidence(self) -> dict[str, dict[str, Any]] | None:
        """The cached pane listing, or None when missing or too old to trust."""
        if self._panes_at is None:
            return None
        if time.monotonic() - self._panes_at > PANE_EVIDENCE_MAX_AGE:
            return None
        return self._panes

    def _ensure_pane_refresh(self, rows: list[dict[str, Any]]) -> None:
        """Start a background pane listing when a row needs fresh evidence."""
        if self._panes_task is not None and not self._panes_task.done():
            return
        if not any(resolve_task_pane(row) for row in rows):
            return
        if (
            self._panes_at is not None
            and time.monotonic() - self._panes_at < HERDR_PODMAN
        ):
            return
        self._panes_task = asyncio.create_task(self._refresh_panes())
        self._in_flight.add(self._panes_task)
        self._panes_task.add_done_callback(self._in_flight.discard)

    async def _refresh_panes(self) -> None:
        """List Herdr panes off the event loop and rebuild if they changed."""
        try:
            panes = await asyncio.to_thread(self.stale_checker.pane_lister)
        except Exception:
            panes = None
        changed = panes != self._pane_evidence()
        self._panes = panes
        self._panes_at = time.monotonic()
        if changed:
            self._request_build()

    async def _maybe_build(self) -> None:
        if not all(spec.source in self._results for spec in self.specs):
            return
        # Builds are serialized so an older build can never overwrite a newer
        # one. Stale checks use the cached pane listing (refreshed on its own
        # task), so a slow Herdr delays only the pane badges, never task-file
        # or GitHub updates; the /proc probes run in a worker thread.
        async with self._build_lock:
            try:
                self._status.pop("normalization", None)
                now = self.clock()
                # Freeze the results and their source status together: a
                # collector may replace a result while the probe runs, and a
                # snapshot must never mix one collection's rows with another
                # collection's status (or apply old stale evidence to a newer
                # row). The newer result triggers its own build.
                results = dict(self._results)
                status = dict(self._status)
                rows = self._stale_rows(results)
                self._ensure_pane_refresh(rows)
                stale_values = await asyncio.to_thread(
                    self.stale_checker.stale_map,
                    rows,
                    now=now,
                    panes=self._pane_evidence(),
                )
                # Composition (including task numbering, which takes the
                # tasks.json file lock and writes) runs off the event loop;
                # publication stays on it, ordered by the build lock.
                composed = await asyncio.to_thread(
                    self._compose_snapshot,
                    now=now,
                    stale_values=stale_values,
                    results=results,
                    status=status,
                )
                self._publish(*composed)
            except Exception as exc:
                self._status["normalization"] = {
                    "status": "error",
                    "error": f"normalize failed: {type(exc).__name__}",
                }

    def _stale_rows(
        self, results: dict[str, SourceResult] | None = None
    ) -> list[dict[str, Any]]:
        result = (self._results if results is None else results).get("tasks_json")
        if result is None:
            return []
        return [row for row in result.extra.get("tasks", []) if isinstance(row, dict)]

    async def _collect(
        self, spec: CollectorSpec
    ) -> asyncio.Future[SourceResult] | None:
        """Run one collection; return the worker if it outlived its timeout."""
        started = time.perf_counter()
        straggler: asyncio.Future[SourceResult] | None = None
        try:
            worker = asyncio.ensure_future(asyncio.to_thread(spec.fn))
            done, _ = await asyncio.wait({worker}, timeout=spec.timeout_s)
            if not done:
                straggler = worker
                raise TimeoutError
            result = worker.result()
            if not isinstance(result, SourceResult):
                raise TypeError("collector returned an invalid result")
            if result.source != spec.source:
                result.source = spec.source
        except Exception as exc:
            previous = self._results.get(spec.source)
            if spec.source == "github" and previous is not None:
                result = SourceResult(
                    source=spec.source,
                    status="error",
                    items=list(previous.items),
                    extra=dict(previous.extra),
                    error=type(exc).__name__,
                    detail="last successful GitHub data retained",
                )
            else:
                result = SourceResult(
                    source=spec.source, status="error", error=type(exc).__name__
                )
        took_ms = round((time.perf_counter() - started) * 1000.0, 2)
        self._results[spec.source] = result
        status: dict[str, Any] = {"status": result.status, "took_ms": took_ms}
        if spec.source == "github" and result.status == "error" and result.detail:
            status["stale"] = True
        if result.error is not None:
            status["error"] = result.error
        if result.detail is not None:
            status["detail"] = result.detail
        for key in _STATUS_EXTRA.get(spec.source, ()):
            if key not in result.extra:
                continue
            extra = result.extra[key]
            if spec.source == "github" and key == "command_center":
                if isinstance(extra, dict):
                    extra = {
                        name: extra[name]
                        for name in ("repositories", "ci_cd", "recent_merges", "meta")
                        if name in extra
                    }
                else:
                    continue
            status[key] = redact_value(extra)
        self._status[spec.source] = status
        return straggler

    def _build_snapshot(
        self,
        *,
        now: datetime | None = None,
        stale_values: dict[str, dict[str, str | None] | None] | None = None,
        results: dict[str, SourceResult] | None = None,
        status: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Build and cache the snapshot.

        ``stale_values`` is the pre-collected (off-loop) stale evidence; when
        omitted (direct synchronous callers such as tests) it is computed
        inline.
        """
        self._publish(
            *self._compose_snapshot(
                now=now, stale_values=stale_values, results=results, status=status
            )
        )

    def _compose_snapshot(
        self,
        *,
        now: datetime | None = None,
        stale_values: dict[str, dict[str, str | None] | None] | None = None,
        results: dict[str, SourceResult] | None = None,
        status: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Return the redacted snapshot and its canonical digest (no publish)."""
        if now is None:
            now = self.clock()
        if results is None:
            results = self._results
        normalized_results = {
            name: SourceResult(
                result.source,
                result.status,
                list(result.items),
                result.error,
                dict(result.extra),
                result.detail,
            )
            for name, result in results.items()
        }
        for result in results.values():
            for item in result.items:
                kind = item.get("kind")
                if isinstance(kind, str):
                    bucket = normalized_results.get(kind)
                    if bucket is None:
                        bucket = SourceResult(kind, result.status)
                        normalized_results[kind] = bucket
                    bucket.items.append(item)
        snapshot = normalize_tasks(
            normalized_results,
            now=now,
            quiet_minutes=self.quiet_minutes,
            window_hours=self.window_hours,
        )
        tasks_result = normalized_results.get("tasks_json")
        if tasks_result is not None:
            if stale_values is None:
                rows = [
                    row
                    for row in tasks_result.extra.get("tasks", [])
                    if isinstance(row, dict)
                ]
                stale_values = self.stale_checker.stale_map(rows, now=now)
            for task in snapshot.active:
                if task.source == "tasks_json" and task.id in stale_values:
                    task.stale = stale_values[task.id]
        sources = dict(self._status if status is None else status)
        if self.task_ids_path is not None:
            from omega_prime.dashboard.task_ids import number_snapshot

            try:
                snapshot = number_snapshot(snapshot, path=self.task_ids_path, now=now)
            except Exception as exc:
                # A malformed, locked-out or unwritable tasks.json must not
                # freeze every other source. Numbering raises before writing,
                # so the damaged file is left untouched for repair; rows are
                # published unnumbered with the failure in source health.
                sources["task_ids"] = {
                    "status": "error",
                    "error": f"task numbering failed: {type(exc).__name__}",
                }
        normalized = snapshot.to_dict()
        value = {
            "generated_at": now.isoformat(),
            "sources": redact_value(sources),
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
        return safe, digest

    def _publish(self, safe: dict[str, Any], digest: str) -> None:
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
        """Run every collector immediately and rebuild the shared snapshot.

        Fresh pane evidence is gathered first so this one-shot build (the
        ``--once`` CLI and tests) reports pane staleness directly.
        """
        await self._refresh_panes()
        await self._run_specs(self.specs)
        now = time.monotonic()
        for spec in self.specs:
            self._next_due[spec.source] = now + max(0.0, spec.interval_s)

    def nudge(self, source: str) -> None:
        """Make one collector due immediately on the next scheduler tick."""
        if any(spec.source == source for spec in self.specs):
            self._next_due[source] = 0.0
