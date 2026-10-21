# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Phase 111: immediate block surfacing into waiting_on_ove (W111-01..W111-05).

Store primitives, the raise/clear/push helpers, and the engine wiring.
tmp_path and fakes only: no network, no gh/herdr/podman/openshell, no
model calls.
"""

from __future__ import annotations

import fcntl
import json
from pathlib import Path

import pytest

from omega_prime.credentials.redact import REDACTED
from omega_prime.dashboard.status_file import (
    ITEM_FIELDS,
    TASK_STATES,
    _write_locked,
    locked_tasks,
)


def _row(task_id: str, state: str = "working", **extra: object) -> dict:
    row: dict = {"id": task_id, "state": state}
    row.update(extra)
    return row


def _seed(
    path: Path,
    rows: list[dict] | None = None,
    items: list[dict] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tasks": rows or [], "waiting_on_ove": items or []}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


# --- W111-01: store primitives ---------------------------------------------


def test_schema_constants_match_the_box_contract() -> None:
    assert TASK_STATES == (
        "queued",
        "working",
        "blocked",
        "waiting_on_ove",
        "done",
        "failed",
    )
    assert ITEM_FIELDS == ("id", "seq", "ask", "blocks", "since")


def test_locked_tasks_seeds_a_missing_file_without_creating_it(tmp_path: Path) -> None:
    store = tmp_path / "tasks.json"
    with locked_tasks(store) as data:
        assert data == {"tasks": [], "waiting_on_ove": []}
    assert not store.exists()


def test_write_roundtrip_persists_and_leaves_no_residue(tmp_path: Path) -> None:
    store = tmp_path / "nested" / "tasks.json"
    with locked_tasks(store, write=True) as data:
        data["tasks"].append(_row("gb:t1", "queued"))
        _write_locked(data, store)
    with locked_tasks(store) as data:
        assert data == {"tasks": [{"id": "gb:t1", "state": "queued"}], "waiting_on_ove": []}
    raw = store.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert sorted(p.name for p in store.parent.iterdir()) == [
        "tasks.json",
        "tasks.json.lock",
    ]


def test_write_lock_is_exclusive_against_a_second_writer(tmp_path: Path) -> None:
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:t1")])
    with locked_tasks(store, write=True) as data:
        second = (tmp_path / "tasks.json.lock").open("a+")
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(second.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            second.close()
        data["tasks"][0]["state"] = "blocked"
        _write_locked(data, store)
    assert json.loads(store.read_text(encoding="utf-8"))["tasks"][0]["state"] == "blocked"


def test_unknown_fields_survive_a_roundtrip_untouched(tmp_path: Path) -> None:
    store = tmp_path / "tasks.json"
    row = _row(
        "gb:t1",
        "working",
        owner="desk",
        last_activity_at="2026-10-21T00:00:00Z",
        nested={"a": [1, 2]},
    )
    item = {
        "id": "gb:t1:push",
        "seq": 7,
        "ask": "ask",
        "blocks": "gate",
        "since": "2026-10-21T01:00:00Z",
        "extra": True,
    }
    _seed(store, rows=[row], items=[item])
    with locked_tasks(store, write=True) as data:
        data["tasks"][0]["state"] = "blocked"
        _write_locked(data, store)
    with locked_tasks(store) as data:
        assert data["tasks"][0] == {**row, "state": "blocked"}
        assert data["waiting_on_ove"][0] == item
        assert list(data["waiting_on_ove"][0]) == [
            "id",
            "seq",
            "ask",
            "blocks",
            "since",
            "extra",
        ]
