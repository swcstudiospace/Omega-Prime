# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Tests for the dashboard live events websocket (protocol default 9)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from omega_prime.dashboard import SourceResult
from omega_prime.dashboard.__main__ import main
from omega_prime.dashboard.app import create_app
from omega_prime.dashboard.collectors.tasks_json import make_tasks_json_collector
from omega_prime.dashboard.events import (
    EVENTS_WATCH_INTERVAL_S,
    EVENTS_WS_LIMIT,
    TasksFileWatcher,
    compute_deltas,
)
from omega_prime.dashboard.poller import CollectorSpec, DashboardPoller
from omega_prime.dashboard.status_file import write_tasks

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
PORT = 7441
HEADERS = {"host": f"127.0.0.1:{PORT}", "origin": f"http://127.0.0.1:{PORT}"}
SNAPSHOT_KEYS = {
    "generated_at",
    "sources",
    "active",
    "pr_gate",
    "waiting_on_ove",
    "finished",
    "idle_sessions",
}


def _snapshot(active=(), waiting=(), finished=(), *, generated_at="2026-10-21T12:00:00+00:00"):
    return {
        "generated_at": generated_at,
        "sources": {},
        "active": list(active),
        "pr_gate": [],
        "waiting_on_ove": list(waiting),
        "finished": list(finished),
        "idle_sessions": [],
    }


def _task(row_id, **overrides):
    row = {"id": row_id, "title": f"title {row_id}", "note": None, "stale": None}
    row.update(overrides)
    return row


def test_events_module_carries_the_locked_constants():
    assert EVENTS_WS_LIMIT == 16
    assert EVENTS_WATCH_INTERVAL_S == 0.5


def test_identical_snapshots_produce_no_deltas():
    snap = _snapshot(active=[_task("tasks:one")])
    assert compute_deltas(snap, snap) == []


def test_note_only_change_emits_one_task_changed_with_full_row():
    before = _snapshot(active=[_task("tasks:one", note=None)])
    new_row = _task("tasks:one", note="make check failed")
    after = _snapshot(active=[new_row])
    assert compute_deltas(before, after) == [{"type": "task_changed", "task": new_row}]


def test_stale_transitions_emit_exactly_one_stale_changed():
    stale = {"reason": "no_heartbeat", "since": "2026-10-21T11:40:00+00:00"}
    other = {"reason": "pid_dead", "since": "2026-10-21T11:45:00+00:00"}

    null_to_object = compute_deltas(
        _snapshot(active=[_task("tasks:one")]),
        _snapshot(active=[_task("tasks:one", stale=stale)]),
    )
    assert null_to_object == [
        {"type": "stale_changed", "id": "tasks:one", "stale": stale}
    ]

    object_to_null = compute_deltas(
        _snapshot(active=[_task("tasks:one", stale=stale)]),
        _snapshot(active=[_task("tasks:one")]),
    )
    assert object_to_null == [{"type": "stale_changed", "id": "tasks:one", "stale": None}]

    reason_change = compute_deltas(
        _snapshot(active=[_task("tasks:one", stale=stale)]),
        _snapshot(active=[_task("tasks:one", stale=other)]),
    )
    assert reason_change == [{"type": "stale_changed", "id": "tasks:one", "stale": other}]


def test_note_and_stale_change_emit_task_changed_then_stale_changed():
    stale = {"reason": "pid_dead", "since": "2026-10-21T11:50:00+00:00"}
    before = _snapshot(active=[_task("tasks:one")])
    new_row = _task("tasks:one", note="unresponsive", stale=stale)
    after = _snapshot(active=[new_row])
    assert compute_deltas(before, after) == [
        {"type": "task_changed", "task": new_row},
        {"type": "stale_changed", "id": "tasks:one", "stale": stale},
    ]


def test_new_active_id_upserts_as_task_changed():
    row = _task("tasks:two", note="fresh")
    before = _snapshot(active=[_task("tasks:one")])
    after = _snapshot(active=[_task("tasks:one"), row])
    assert compute_deltas(before, after) == [{"type": "task_changed", "task": row}]


def test_active_to_finished_emits_only_task_finished():
    before = _snapshot(active=[_task("tasks:one")])
    finished_row = {"id": "tasks:one", "title": "title tasks:one", "result": "ok"}
    after = _snapshot(finished=[finished_row])
    assert compute_deltas(before, after) == [
        {"type": "task_finished", "task": finished_row}
    ]


def test_finished_id_appearing_without_ever_being_active_emits_task_finished():
    finished_row = {"id": "tasks: nine", "result": "failed"}
    before = _snapshot()
    after = _snapshot(finished=[finished_row])
    assert compute_deltas(before, after) == [
        {"type": "task_finished", "task": finished_row}
    ]


def test_waiting_rows_emit_added_and_cleared():
    item = {"id": "tasks:w1", "ask": "approve deploy", "since": "2026-10-21T12:00:00+00:00"}

    added = compute_deltas(_snapshot(), _snapshot(waiting=[item]))
    assert added == [{"type": "waiting_added", "item": item}]

    cleared = compute_deltas(_snapshot(waiting=[item]), _snapshot())
    assert cleared == [{"type": "waiting_cleared", "id": "tasks:w1"}]


def test_waiting_to_finished_emits_finished_before_cleared():
    item = {"id": "tasks:w1", "ask": "approve deploy", "since": "2026-10-21T12:00:00+00:00"}
    finished_row = {"id": "tasks:w1", "result": "ok"}
    before = _snapshot(waiting=[item])
    after = _snapshot(finished=[finished_row])
    assert compute_deltas(before, after) == [
        {"type": "task_finished", "task": finished_row},
        {"type": "waiting_cleared", "id": "tasks:w1"},
    ]


def test_multi_change_fixture_has_the_exact_ordered_message_list():
    stale = {"reason": "pane_gone", "since": "2026-10-21T11:55:00+00:00"}
    new_row = _task("tasks:new")
    done_row = {"id": "tasks:old", "result": "ok"}
    stale_row = _task("tasks:stale", stale=stale)
    waiting_item = {"id": "tasks:w2", "ask": "ok the spend", "since": "2026-10-21T12:01:00+00:00"}

    before = _snapshot(active=[_task("tasks:old", note="running"), _task("tasks:stale")])
    after = _snapshot(
        active=[new_row, stale_row],
        waiting=[waiting_item],
        finished=[done_row],
    )
    assert compute_deltas(before, after) == [
        {"type": "task_finished", "task": done_row},
        {"type": "task_changed", "task": new_row},
        {"type": "waiting_added", "item": waiting_item},
        {"type": "stale_changed", "id": "tasks:stale", "stale": stale},
    ]


def test_none_previous_treats_every_row_as_new_in_group_order():
    active_rows = [_task("tasks:a"), _task("tasks:b")]
    waiting_item = {"id": "tasks:w", "ask": "hi", "since": None}
    finished_row = {"id": "tasks:f", "result": "ok"}
    current = _snapshot(active=active_rows, waiting=[waiting_item], finished=[finished_row])
    assert compute_deltas(None, current) == [
        {"type": "task_finished", "task": finished_row},
        {"type": "task_changed", "task": active_rows[0]},
        {"type": "task_changed", "task": active_rows[1]},
        {"type": "waiting_added", "item": waiting_item},
    ]


def test_malformed_rows_are_skipped_and_last_row_per_id_wins():
    before = _snapshot()
    after = _snapshot(
        active=["nope", {"title": "no id"}, _task("tasks:dup", note="first"), _task("tasks:dup", note="last")]
    )
    deltas = compute_deltas(before, after)
    assert deltas == [{"type": "task_changed", "task": _task("tasks:dup", note="last")}]


# --- route tests (TestClient websockets, injected intervals, port 7441 only) ---

def _tasks_row(row_id="tasks:one", state="working", **overrides):
    row = {
        "id": row_id,
        "title": "task one",
        "owner": {"kind": "agent", "ref": row_id},
        "state": state,
        "started_at": "2026-10-21T11:00:00+00:00",
        "last_activity_at": "2026-10-21T12:00:00+00:00",
        "progress": None,
        "note": "running",
        "links": [],
    }
    row.update(overrides)
    return row


def _write(path, tasks=(), waiting=(), finished=()):
    write_tasks(
        {
            "version": 1,
            "updated_at": "2026-10-21T12:00:00+00:00",
            "tasks": list(tasks),
            "waiting_on_ove": list(waiting),
            "finished": list(finished),
        },
        path=path,
    )


def _tasks_poller(tasks_path, *, interval_s=0.2, tick_s=0.05):
    collector = make_tasks_json_collector(tasks_path=tasks_path, now_fn=lambda: NOW)
    return DashboardPoller(
        [CollectorSpec("tasks_json", collector, interval_s, 1.0)],
        tick_s=tick_s,
        clock=lambda: NOW,
    )


def _bare_snapshot():
    return {
        "generated_at": "2026-10-21T12:00:00+00:00",
        "sources": {},
        "active": [{"id": "tasks:one", "title": "task one", "note": None, "stale": None}],
        "pr_gate": [],
        "waiting_on_ove": [],
        "finished": [],
        "idle_sessions": [],
    }


def test_snapshot_first_then_task_finished_within_two_seconds(tmp_path):
    tasks_path = tmp_path / "tasks.json"
    _write(tasks_path, tasks=[_tasks_row()])
    poller = _tasks_poller(tasks_path)
    asyncio.run(poller.refresh_now())
    app = create_app(poller, port=PORT, heartbeat_s=0.5)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/events", headers=HEADERS) as ws:
            first = ws.receive_json()
            assert first["type"] == "snapshot"
            assert set(first["snapshot"]) == SNAPSHOT_KEYS
            assert [row["id"] for row in first["snapshot"]["active"]] == ["tasks:one"]
            _write(tasks_path, tasks=[_tasks_row(state="done", note="wrapped up")])
            started = time.monotonic()
            delta = ws.receive_json()
            elapsed = time.monotonic() - started
    assert delta["type"] == "task_finished"
    assert delta["task"]["id"] == "tasks:one"
    assert delta["task"]["state"] == "done"
    assert elapsed < 2.0


def test_waiting_added_delta_arrives_after_rewrite(tmp_path):
    tasks_path = tmp_path / "tasks.json"
    _write(tasks_path, tasks=[_tasks_row()])
    poller = _tasks_poller(tasks_path)
    asyncio.run(poller.refresh_now())
    app = create_app(poller, port=PORT, heartbeat_s=0.5)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/events", headers=HEADERS) as ws:
            assert ws.receive_json()["type"] == "snapshot"
            _write(
                tasks_path,
                tasks=[_tasks_row()],
                waiting=[
                    {
                        "id": "tasks:wait1",
                        "ask": "approve the deploy",
                        "since": "2026-10-21T12:01:00+00:00",
                        "blocks": "tasks:one",
                    }
                ],
            )
            delta = ws.receive_json()
    assert delta["type"] == "waiting_added"
    assert delta["item"]["id"] == "tasks:wait1"
    assert delta["item"]["ask"] == "approve the deploy"


def test_heartbeat_stale_changed_and_no_churn_without_collectors():
    poller = DashboardPoller([])
    poller._snapshot = _bare_snapshot()
    app = create_app(poller, port=PORT, heartbeat_s=0.2)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/events", headers=HEADERS) as ws:
            assert ws.receive_json()["type"] == "snapshot"
            assert ws.receive_json() == {"type": "heartbeat"}

            stale_copy = json.loads(json.dumps(poller._snapshot))
            stale_copy["active"][0]["stale"] = {
                "reason": "no_heartbeat",
                "since": "2026-10-21T11:40:00+00:00",
            }
            stale_copy["generated_at"] = "2026-10-21T12:30:00+00:00"
            poller._snapshot = stale_copy
            assert ws.receive_json() == {
                "type": "stale_changed",
                "id": "tasks:one",
                "stale": {
                    "reason": "no_heartbeat",
                    "since": "2026-10-21T11:40:00+00:00",
                },
            }

            churned = json.loads(json.dumps(poller._snapshot))
            churned["generated_at"] = "2026-10-21T13:00:00+00:00"
            poller._snapshot = churned
            assert ws.receive_json() == {"type": "heartbeat"}
            assert ws.receive_json() == {"type": "heartbeat"}


def test_events_ws_guard_rejects_foreign_missing_origin_and_bad_host():
    poller = DashboardPoller([])
    poller._snapshot = _bare_snapshot()
    app = create_app(poller, port=PORT)
    with TestClient(app) as client:
        for headers in (
            {"host": f"127.0.0.1:{PORT}", "origin": "http://evil.example"},
            {"host": f"127.0.0.1:{PORT}"},
            {"host": "evil.example:7441", "origin": f"http://127.0.0.1:{PORT}"},
        ):
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect("/ws/events", headers=headers):
                    pass
            assert exc.value.code == 4403
        with client.websocket_connect(
            "/ws/events",
            headers={"host": f"127.0.0.1:{PORT}", "origin": f"http://localhost:{PORT}"},
        ) as ws:
            assert ws.receive_json()["type"] == "snapshot"


def test_events_ws_cap_refuses_17th_and_releases_slots_on_close():
    poller = DashboardPoller([])
    poller._snapshot = _bare_snapshot()
    app = create_app(poller, port=PORT)
    with TestClient(app) as client:
        with contextlib.ExitStack() as stack:
            for _ in range(16):
                ws = stack.enter_context(
                    client.websocket_connect("/ws/events", headers=HEADERS)
                )
                assert ws.receive_json()["type"] == "snapshot"
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect("/ws/events", headers=HEADERS) as ws:
                    ws.receive_json()
            assert exc.value.code == 4429
        with client.websocket_connect("/ws/events", headers=HEADERS) as ws:
            assert ws.receive_json()["type"] == "snapshot"


# --- wake path: nudge, stat watcher, lifespan and CLI wiring ---


def test_nudge_schedules_named_collector_and_ignores_unknown_source():
    async def run():
        calls = []

        def collect():
            calls.append(len(calls))
            return SourceResult("tasks_json", "ok" if len(calls) == 1 else "degraded")

        poller = DashboardPoller(
            [CollectorSpec("tasks_json", collect, 30.0, 1.0)], tick_s=0.02
        )
        await poller.refresh_now()
        assert calls == [0]
        assert await poller.wait_for_update(0.0) is True
        await poller.start()
        try:
            poller.nudge("tasks_json")
            deadline = time.monotonic() + 0.5
            while len(calls) < 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert len(calls) >= 2
            assert poller.snapshot_hash() is not None
            assert await poller.wait_for_update(1.0) is True
            poller.nudge("missing-source")
            await asyncio.sleep(0.1)
            known_calls = len(calls)
            deadline = time.monotonic() + 0.2
            while time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert len(calls) == known_calls
        finally:
            await poller.stop()

    asyncio.run(run())


def test_tasks_file_watcher_fires_once_per_signature_change(tmp_path):
    class _Stop(Exception):
        pass

    sequence = [(1000, 10), (1000, 10), (1001, 12), FileNotFoundError, (1002, 20)]
    fired = []

    def stat():
        step = sequence.pop(0)
        if isinstance(step, type) and issubclass(step, BaseException):
            raise step()
        return SimpleNamespace(st_mtime_ns=step[0], st_size=step[1])

    async def sleep(_interval_s):
        if not sequence:
            raise _Stop()
        await asyncio.sleep(0)

    watcher = TasksFileWatcher(
        tmp_path / "tasks.json",
        on_change=lambda: fired.append(1),
        interval_s=0.0,
        sleep=sleep,
        stat=stat,
    )
    with pytest.raises(_Stop):
        asyncio.run(watcher.run())
    assert fired == [1, 1, 1]
    assert watcher.path == tmp_path / "tasks.json"


def test_tasks_json_wake_reaches_events_ws_within_two_seconds(tmp_path):
    tasks_path = tmp_path / "tasks.json"
    _write(tasks_path, tasks=[_tasks_row()])
    poller = _tasks_poller(tasks_path, interval_s=30.0, tick_s=0.05)
    asyncio.run(poller.refresh_now())
    cancelled: list[bool] = []
    sleeps = []

    async def _sleep(interval_s):
        sleeps.append(1)
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    watcher = TasksFileWatcher(
        tasks_path, on_change=lambda: poller.nudge("tasks_json"), interval_s=0.05, sleep=_sleep
    )
    app = create_app(poller, port=PORT, heartbeat_s=0.3, tasks_watcher=watcher)
    with TestClient(app) as client:
        assert app.state.tasks_watcher is watcher
        with client.websocket_connect("/ws/events", headers=HEADERS) as ws:
            first = ws.receive_json()
            assert first["type"] == "snapshot"
            assert [row["id"] for row in first["snapshot"]["active"]] == ["tasks:one"]
            _write(tasks_path, tasks=[_tasks_row(state="done", note="shipped")])
            started = time.monotonic()
            delta = None
            for _ in range(10):
                message = ws.receive_json()
                if message["type"] != "heartbeat":
                    delta = message
                    break
            elapsed = time.monotonic() - started
    assert sleeps, "watcher never ran"
    assert cancelled == [True]
    assert delta is not None
    assert delta["type"] == "task_finished"
    assert delta["task"]["id"] == "tasks:one"
    assert elapsed < 2.0


def test_main_threads_tasks_path_into_create_app(tmp_path):
    captured: dict[str, object] = {}
    poller = DashboardPoller([])

    def serving(app: object, host: str, port: int) -> None:
        captured["app"] = app

    assert (
        main(
            ["--host", "localhost", "--port", str(PORT)],
            home=tmp_path,
            serve=serving,
            poller_factory=lambda: poller,
        )
        == 0
    )
    app = captured["app"]
    watcher = app.state.tasks_watcher
    assert watcher is not None
    assert watcher.path == tmp_path / ".omega-prime" / "dashboard" / "tasks.json"


def test_create_app_without_tasks_path_has_no_watcher_but_runs_lifespan():
    poller = DashboardPoller([])
    app = create_app(poller, port=PORT)
    assert app.state.tasks_watcher is None
    with TestClient(app) as client:
        assert client.get(
            "/api/snapshot", headers={"host": f"127.0.0.1:{PORT}"}
        ).status_code == 200
        with client.websocket_connect("/ws/events", headers=HEADERS) as ws:
            assert ws.receive_json()["type"] == "snapshot"
