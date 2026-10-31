# SPDX-License-Identifier: AGPL-3.0-only
"""Protocol defaults 2 and 3: tolerant-but-strict validation and the heartbeat writer."""

from datetime import UTC, datetime

import pytest

from omega_prime.dashboard.status_file import heartbeat, locked_tasks, write_tasks

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _seed(path, **row):
    row = {
        "id": "gb:1",
        "title": "one",
        "owner": {"kind": "human", "ref": "o"},
        "state": "working",
        **row,
    }
    write_tasks(
        {
            "version": 1,
            "updated_at": "2026-10-21T11:00:00+00:00",
            "tasks": [row],
            "waiting_on_ove": [],
            "finished": [],
        },
        path=path,
    )


def _rows(path):
    with locked_tasks(path, write=False) as data:
        return data["tasks"], data["updated_at"]


def test_heartbeat_persists_liveness_and_refreshes_updated_at(tmp_path):
    path = tmp_path / "tasks.json"
    _seed(path)
    row = heartbeat(
        path, "gb:1", state="blocked", note="waiting on review", pid=4242, now=NOW
    )
    assert row["last_heartbeat_at"] == NOW.isoformat()
    assert row["pid"] == 4242
    assert row["state"] == "blocked" and row["note"] == "waiting on review"
    rows, updated_at = _rows(path)
    assert rows[0]["last_heartbeat_at"] == NOW.isoformat()
    assert rows[0]["pid"] == 4242
    assert rows[0]["state"] == "blocked"
    assert updated_at == NOW.isoformat()


def test_heartbeat_without_now_writes_parseable_timestamp(tmp_path):
    path = tmp_path / "tasks.json"
    _seed(path)
    row = heartbeat(path, "gb:1")
    assert datetime.fromisoformat(row["last_heartbeat_at"])


def test_heartbeat_keeps_optional_fields_absent(tmp_path):
    path = tmp_path / "tasks.json"
    _seed(path, note="keep me")
    row = heartbeat(path, "gb:1", now=NOW)
    assert row["pid"] is None
    assert row["note"] == "keep me"
    rows, _ = _rows(path)
    assert rows[0]["note"] == "keep me"
    assert rows[0]["pid"] is None


def test_heartbeat_unknown_id_raises_and_leaves_file_unchanged(tmp_path):
    path = tmp_path / "tasks.json"
    _seed(path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        heartbeat(path, "gb:missing", note="never written", now=NOW)
    assert path.read_bytes() == before


def test_heartbeat_clears_a_written_stale_marker(tmp_path):
    path = tmp_path / "tasks.json"
    _seed(
        path,
        stale={"reason": "pid_dead", "since": "2026-10-21T10:00:00+00:00"},
    )
    rows, _ = _rows(path)
    assert rows[0]["stale"] == {
        "reason": "pid_dead",
        "since": "2026-10-21T10:00:00+00:00",
    }
    heartbeat(path, "gb:1", now=NOW)
    rows, _ = _rows(path)
    assert rows[0]["stale"] is None


def test_new_fields_round_trip_through_write_and_read(tmp_path):
    path = tmp_path / "tasks.json"
    _seed(
        path,
        last_heartbeat_at="2026-10-21T11:30:00+00:00",
        pid=909,
        stale={"reason": "no_heartbeat", "since": "2026-10-21T11:30:00+00:00"},
    )
    rows, _ = _rows(path)
    assert rows[0]["last_heartbeat_at"] == "2026-10-21T11:30:00+00:00"
    assert rows[0]["pid"] == 909
    assert rows[0]["stale"] == {
        "reason": "no_heartbeat",
        "since": "2026-10-21T11:30:00+00:00",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pid", "abc"),
        ("pid", True),
        ("pid", 0),
        ("pid", -1),
        ("pid", 1.5),
        ("last_heartbeat_at", 12345),
        ("last_heartbeat_at", "not-a-date"),
        ("stale", {"reason": 5, "since": "2026-10-21T10:00:00+00:00"}),
        ("stale", {"reason": "pid_dead", "since": "nope"}),
        (
            "stale",
            {"reason": "pid_dead", "since": "2026-10-21T10:00:00+00:00", "pid": 1},
        ),
        ("stale", {"reason": "pid_dead"}),
        ("stale", "pid_dead"),
    ],
)
def test_junk_in_new_fields_is_rejected(tmp_path, field, value):
    path = tmp_path / "tasks.json"
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(ValueError):
        _seed(path, **{field: value})
    after = path.read_bytes() if path.exists() else before
    assert after == before


def test_heartbeat_junk_pid_fails_validation_without_writing(tmp_path):
    path = tmp_path / "tasks.json"
    _seed(path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        heartbeat(path, "gb:1", pid=True, now=NOW)
    assert path.read_bytes() == before
