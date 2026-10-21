import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from omega_prime.dashboard.model import FinishedItem, Owner, Snapshot, Task, WaitingItem
from omega_prime.dashboard.status_file import write_tasks
from omega_prime.dashboard.task_ids import (
    allocate_task_id,
    assign_task_ids,
    format_display_id,
    next_task_id,
    number_snapshot,
)

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def _document(tasks=None, waiting=None, finished=None):
    return {
        "version": 1,
        "updated_at": NOW.isoformat(),
        "tasks": tasks or [],
        "waiting_on_ove": waiting or [],
        "finished": finished or [],
    }


def _task(identity, title):
    return {
        "id": identity,
        "title": title,
        "owner": {"kind": "human", "ref": "owner"},
        "state": "working",
    }


def test_subprocess_allocations_are_unique_and_persisted(tmp_path):
    path = tmp_path / "tasks.json"
    code = (
        "from datetime import UTC, datetime; from pathlib import Path; "
        "from omega_prime.dashboard.task_ids import allocate_task_id; "
        "import sys; print(allocate_task_id(path=Path(sys.argv[1]), "
        "identity=sys.argv[2], title=sys.argv[2], now=datetime.now(UTC))['seq'])"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(path), f"tasks:{index}"],
            stdout=subprocess.PIPE,
            text=True,
        )
        for index in range(8)
    ]
    results = [process.communicate()[0].strip() for process in processes]
    assert sorted(map(int, results)) == list(range(1, 9))
    assert next_task_id(path=path) == 9


def test_failed_replace_cleans_temp_and_preserves_previous_document(
    tmp_path, monkeypatch
):
    from omega_prime.dashboard import status_file

    path = tmp_path / "tasks.json"
    allocate_task_id(path=path, identity="tasks:existing", title="Existing", now=NOW)
    original = path.read_bytes()

    def fail_replace(*args):
        raise OSError("simulated replace interruption")

    monkeypatch.setattr(status_file.os, "replace", fail_replace)
    with pytest.raises(OSError):
        allocate_task_id(path=path, identity="tasks:new", title="New", now=NOW)
    assert path.read_bytes() == original
    assert {entry.name for entry in tmp_path.iterdir()} == {
        "tasks.json",
        "tasks.json.lock",
    }


def test_lock_file_is_restrictive(tmp_path):
    path = tmp_path / "tasks.json"
    next_task_id(path=path)
    assert (tmp_path / "tasks.json.lock").stat().st_mode & 0o777 == 0o600


def test_number_snapshot_numbers_derived_rows_and_records_observed_changes(tmp_path):
    path = tmp_path / "tasks.json"
    task = Task(
        "proc:42",
        "Process",
        "proc",
        Owner("process", "42", "Process"),
        "running",
        wiring={
            "phase": "build",
            "branch": "feature/x",
            "head_sha": "abc",
            "stage": "test",
            "repo": None,
        },
    )
    waiting = WaitingItem("ask:question", "Need input", None, "")
    finished = FinishedItem(
        "wiring:done",
        "Finished wiring",
        NOW.isoformat(),
        "ok",
        wiring={
            "phase": "release",
            "branch": "main",
            "head_sha": "def",
            "stage": "done",
            "repo": None,
        },
    )
    idle = Task("herdr:idle", "Idle", "herdr", Owner("herdr", "idle", "Idle"), "quiet")
    snapshot = Snapshot([task], [], [waiting], [finished], [idle])
    number_snapshot(snapshot, path=path, now=NOW)
    first = {
        row.id: row
        for row in [
            *snapshot.active,
            *snapshot.idle_sessions,
            *snapshot.waiting_on_ove,
            *snapshot.finished,
        ]
    }
    assert {row.seq for row in first.values()} == {1, 2, 3, 4}
    assert task.wiring["repo"] is None and finished.wiring["repo"] is None
    assert task.timeline == [{"at": NOW.isoformat(), "state": "running"}]
    number_snapshot(snapshot, path=path, now=NOW + timedelta(minutes=1))
    assert task.timeline == [{"at": NOW.isoformat(), "state": "running"}]
    task.state = "blocked"
    number_snapshot(snapshot, path=path, now=NOW + timedelta(minutes=2))
    assert task.timeline[-1] == {
        "at": (NOW + timedelta(minutes=2)).isoformat(),
        "state": "blocked",
    }


def test_preview_and_assignment_are_durable(tmp_path):
    path = tmp_path / "tasks.json"
    assert next_task_id(path=path) == 1
    assert not path.exists()
    assert allocate_task_id(path=path, identity="tasks:a", title="A", now=NOW) == {
        "seq": 1,
        "display_id": "00001 - A",
    }
    assert next_task_id(path=path) == 2
    assert (
        allocate_task_id(path=path, identity="tasks:a", title="renamed", now=NOW)["seq"]
        == 1
    )


def test_display_id_width_is_minimum():
    assert format_display_id(99999, "x") == "99999 - x"
    assert format_display_id(100000, "x") == "100000 - x"
    for invalid in (True, 0, -1, 1.5, "1"):
        with pytest.raises(ValueError):
            format_display_id(invalid, "x")


def test_preview_is_byte_and_mtime_preserving_after_registry_exists(tmp_path):
    path = tmp_path / "tasks.json"
    allocate_task_id(path=path, identity="tasks:a", title="A", now=NOW)
    before = path.read_bytes()
    stamp = path.stat().st_mtime_ns
    for _ in range(3):
        assert next_task_id(path=path) == 2
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == stamp


def test_bulk_migration_preserves_bridge_order_and_identity(tmp_path):
    path = tmp_path / "tasks.json"
    write_tasks(
        _document(
            tasks=[_task("tasks:a", "same"), _task("tasks:b", "same")],
            waiting=[{"id": "ask:a", "ask": "Question", "blocks": ""}],
            finished=[
                {
                    "id": "tasks:a",
                    "title": "same",
                    "finished_at": NOW.isoformat(),
                    "result": "ok",
                }
            ],
        ),
        path=path,
    )
    data = assign_task_ids(path=path, now=NOW)
    assert [r["seq"] for r in data["tasks"]] == [1, 2]
    assert [r["seq"] for r in data["waiting_on_ove"]] == [3]
    assert [r["seq"] for r in data["finished"]] == [1]
    assert data["next_seq"] == 4
    assert hashlib.sha256(b"tasks:a").hexdigest() in data["identity_registry"]
    assert "tasks:a" not in data["identity_registry"]


def test_legacy_writer_does_not_discard_tombstones_and_counter(tmp_path):
    path = tmp_path / "tasks.json"
    allocate_task_id(path=path, identity="tasks:gone", title="Gone", now=NOW)
    write_tasks(_document(tasks=[_task("tasks:new", "New")]), path=path)
    assert (
        allocate_task_id(path=path, identity="tasks:new", title="New", now=NOW)["seq"]
        == 2
    )
    assert (
        allocate_task_id(path=path, identity="tasks:gone", title="Back", now=NOW)["seq"]
        == 1
    )


def test_reorder_and_title_changes_do_not_change_numbers(tmp_path):
    path = tmp_path / "tasks.json"
    write_tasks(
        _document(tasks=[_task("tasks:a", "A"), _task("tasks:b", "B")]), path=path
    )
    original = assign_task_ids(path=path, now=NOW)
    write_tasks(
        _document(tasks=[_task("tasks:b", "B renamed"), _task("tasks:a", "A renamed")]),
        path=path,
    )
    updated = assign_task_ids(path=path, now=NOW + timedelta(minutes=1))
    assert {r["id"]: r["seq"] for r in original["tasks"]} == {
        r["id"]: r["seq"] for r in updated["tasks"]
    }
    assert updated["next_seq"] == 3


def test_concurrent_allocations_are_unique_and_monotone(tmp_path):
    path = tmp_path / "tasks.json"
    identities = [f"tasks:{index}" for index in range(24)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        allocations = list(
            pool.map(
                lambda identity: allocate_task_id(
                    path=path, identity=identity, title=identity, now=NOW
                ),
                identities,
            )
        )
    assert sorted(item["seq"] for item in allocations) == list(range(1, 25))
    assert next_task_id(path=path) == 25


@pytest.mark.parametrize("bad_seq", [True, 0, -1, 1.2, "1"])
def test_invalid_registry_data_fails_without_rewriting(tmp_path, bad_seq):
    path = tmp_path / "tasks.json"
    key = hashlib.sha256(b"tasks:a").hexdigest()
    data = _document()
    data.update(
        next_seq=1,
        identity_registry={
            key: {"seq": bad_seq, "title": "A", "timeline": [], "last_state": None}
        },
    )
    original = json.dumps(data).encode()
    path.write_bytes(original)
    with pytest.raises((ValueError, OSError)):
        next_task_id(path=path)
    assert path.read_bytes() == original


def test_corrupt_json_fails_closed_and_is_preserved(tmp_path):
    path = tmp_path / "tasks.json"
    original = b'{"version":'
    path.write_bytes(original)
    with pytest.raises((ValueError, OSError)):
        allocate_task_id(path=path, identity="tasks:a", title="A", now=NOW)
    assert path.read_bytes() == original


def test_finished_rows_record_result_not_none_and_stay_stable(tmp_path):
    path = tmp_path / "tasks.json"
    finished = FinishedItem("tasks:done-task", "Done task", NOW.isoformat(), "ok")
    assert finished.state is None
    snapshot = Snapshot([], [], [], [finished], [])
    for minute in range(5):
        number_snapshot(snapshot, path=path, now=NOW + timedelta(minutes=minute))
    assert finished.timeline == [{"at": NOW.isoformat(), "state": "ok"}]
    data = json.loads(path.read_text())
    assert all(
        event["state"] != "None"
        for record in data["identity_registry"].values()
        for event in record["timeline"]
    )
