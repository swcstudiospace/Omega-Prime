"""Delivery idempotency with an atomic claim / complete / release lifecycle.

A delivery moves ``unseen -> claimed -> done``. ``claim`` is the only way to
start work and it is atomic: with a file path it takes an exclusive ``flock``
and re-reads the shared ledger on every call, so separate listener wakes (or
processes) sharing one path agree on who owns a delivery. A failed attempt calls
``release`` so a retry of the same delivery can resume; a crashed attempt's
claim expires after ``claim_ttl_s`` so it cannot wedge the delivery forever.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Any

from omega_prime.sdlc_events.types import DeliveryId

DEFAULT_CLAIM_TTL_S = 600.0


class ClaimOutcome(StrEnum):
    ACQUIRED = "acquired"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class IdempotencyStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        claim_ttl_s: float = DEFAULT_CLAIM_TTL_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._ttl = claim_ttl_s
        self._clock = clock
        self._mem: dict[str, dict[str, Any]] = {}

    @contextmanager
    def _ledger(self) -> Iterator[dict[str, dict[str, Any]]]:
        if self._path is None:
            yield self._mem
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                data: dict[str, dict[str, Any]] = {}
                if self._path.exists():
                    raw = self._path.read_text(encoding="utf-8").strip()
                    if raw:
                        loaded = json.loads(raw)
                        if not isinstance(loaded, dict):
                            raise ValueError("idempotency ledger must be an object")
                        data = loaded
                yield data
                tmp = self._path.with_suffix(self._path.suffix + ".tmp")
                tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
                os.replace(tmp, self._path)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def claim(self, delivery: DeliveryId) -> ClaimOutcome:
        """Atomically claim a delivery; only ``ACQUIRED`` may do the work."""
        now = self._clock()
        with self._ledger() as data:
            entry = data.get(delivery.value)
            if entry is not None:
                if entry.get("state") == "done":
                    return ClaimOutcome.DONE
                if now - float(entry.get("at", 0.0)) < self._ttl:
                    return ClaimOutcome.IN_PROGRESS
            data[delivery.value] = {"state": "claimed", "at": now}
            return ClaimOutcome.ACQUIRED

    def complete(self, delivery: DeliveryId) -> None:
        with self._ledger() as data:
            data[delivery.value] = {"state": "done", "at": self._clock()}

    def release(self, delivery: DeliveryId) -> None:
        """Drop an unfinished claim so a retry of the same delivery can resume."""
        with self._ledger() as data:
            entry = data.get(delivery.value)
            if entry is not None and entry.get("state") == "claimed":
                del data[delivery.value]

    def is_done(self, delivery: DeliveryId) -> bool:
        with self._ledger() as data:
            entry = data.get(delivery.value)
            return entry is not None and entry.get("state") == "done"
