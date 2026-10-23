"""Adapter that records SDLC notes. Production path uses locked tasks.json helpers.

The dashboard process owns the lock; handlers may call this with an injectable
backend. Default is an in-memory store for unit tests and dry-run CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MemoryTaskStore:
    rows: dict[str, dict[str, str]] = field(default_factory=dict)

    def upsert_note(self, task_id: str, state: str, note: str) -> None:
        self.rows[task_id] = {"state": state, "note": note}


# Locked tasks.json wiring is documented for the live Command Center path:
#   from omega_prime.dashboard.status_file import locked_tasks, _write_locked
# Operators must import from the running dashboard tree (omes-bot-wt/herdr-ws)
# and never invent a second tasks store.
