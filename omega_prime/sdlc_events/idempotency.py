"""In-memory / file-backed delivery idempotency."""

from __future__ import annotations

from pathlib import Path

from omega_prime.sdlc_events.types import DeliveryId


class IdempotencyStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._seen: set[str] = set()
        if path and path.exists():
            self._seen = {line.strip() for line in path.read_text().splitlines() if line.strip()}

    def seen(self, delivery: DeliveryId) -> bool:
        return delivery.value in self._seen

    def mark(self, delivery: DeliveryId) -> None:
        if delivery.value in self._seen:
            return
        self._seen.add(delivery.value)
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(delivery.value + "\n")
