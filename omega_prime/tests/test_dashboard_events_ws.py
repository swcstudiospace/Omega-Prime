# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Tests for the dashboard live events websocket (protocol default 9)."""

from __future__ import annotations

from omega_prime.dashboard.events import (
    EVENTS_WATCH_INTERVAL_S,
    EVENTS_WS_LIMIT,
    compute_deltas,
)


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
