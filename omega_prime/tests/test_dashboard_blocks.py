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
from datetime import datetime
from pathlib import Path

import pytest

from omega_prime.credentials.redact import REDACTED
from omega_prime.dashboard.blocks import clear_block, push_block, raise_block
from omega_prime.dashboard.run_rows import finish, start, touch
from omega_prime.dashboard.status_file import (
    ITEM_FIELDS,
    TASK_STATES,
    _write_locked,
    locked_tasks,
)
from omega_prime.engine.omp_driver import run
from omega_prime.tests.test_engine import _env, _install_fake


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
        assert data == {
            "tasks": [{"id": "gb:t1", "state": "queued"}],
            "waiting_on_ove": [],
        }
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
    assert (
        json.loads(store.read_text(encoding="utf-8"))["tasks"][0]["state"] == "blocked"
    )


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


# --- W111-02: raise/clear upsert lifecycle ----------------------------------


def test_raise_block_upserts_by_stable_id_and_blocks_the_row(tmp_path: Path) -> None:
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:t1")])
    raise_block(store, "blk-1", "first ask", "gate-1", "gb:t1")
    raise_block(store, "blk-2", "second ask", "gate-2", "gb:t1")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert [item["id"] for item in data["waiting_on_ove"]] == ["blk-1", "blk-2"]
    assert [item["seq"] for item in data["waiting_on_ove"]] == [1, 2]
    assert all(item["since"] for item in data["waiting_on_ove"])
    assert data["tasks"][0]["state"] == "blocked"
    first = dict(data["waiting_on_ove"][0])
    raise_block(store, "blk-1", "updated ask", "gate-1b", "gb:t1")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert [item["id"] for item in data["waiting_on_ove"]] == ["blk-1", "blk-2"]
    repeat = data["waiting_on_ove"][0]
    assert repeat["ask"] == "updated ask"
    assert repeat["blocks"] == "gate-1b"
    assert repeat["seq"] == first["seq"]
    assert repeat["since"] == first["since"]
    assert data["tasks"][0]["state"] == "blocked"


def test_clear_restores_working_and_is_idempotent(tmp_path: Path) -> None:
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:t1")])
    raise_block(store, "blk-1", "need eyes", "gate", "gb:t1")
    clear_block(store, "blk-1", "gb:t1")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert data["waiting_on_ove"] == []
    assert data["tasks"][0]["state"] == "working"
    clear_block(store, "blk-1", "gb:t1")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert data["waiting_on_ove"] == []
    assert data["tasks"][0]["state"] == "working"


def test_task_stays_blocked_until_its_last_block_clears(tmp_path: Path) -> None:
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:t1"), _row("gb:t2")])
    raise_block(store, "gb:t1:plan_not_allowed", "plan gate", "plan", "gb:t1")
    push_block(store, "gb:t1", "gh_not_logged_in")
    raise_block(store, "gb:t2:push", "other task", "push", "gb:t2")
    clear_block(store, "gb:t1:plan_not_allowed", "gb:t1")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert [item["id"] for item in data["waiting_on_ove"]] == [
        "gb:t1:push",
        "gb:t2:push",
    ]
    assert {row["id"]: row["state"] for row in data["tasks"]} == {
        "gb:t1": "blocked",
        "gb:t2": "blocked",
    }
    clear_block(store, "gb:t1:push", "gb:t1")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert {row["id"]: row["state"] for row in data["tasks"]} == {
        "gb:t1": "working",
        "gb:t2": "blocked",
    }


def test_items_written_before_owner_tracking_still_hold_their_task(
    tmp_path: Path,
) -> None:
    legacy = {"id": "gb:t1:push", "seq": 1, "ask": "a", "blocks": "b", "since": "x"}
    store = _seed(
        tmp_path / "tasks.json", rows=[_row("gb:t1", "blocked")], items=[legacy]
    )
    raise_block(store, "gb:t1:plan_not_allowed", "plan gate", "plan", "gb:t1")
    clear_block(store, "gb:t1:plan_not_allowed", "gb:t1")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert data["tasks"][0]["state"] == "blocked"
    clear_block(store, "gb:t1:push", "gb:t1")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert data["tasks"][0]["state"] == "working"


def test_clear_never_resurrects_terminal_rows(tmp_path: Path) -> None:
    store = _seed(
        tmp_path / "tasks.json",
        rows=[_row("gb:a"), _row("gb:done", "done"), _row("gb:failed", "failed")],
    )
    push_block(store, "gb:a", "auto_review_blocked")
    # Clears naming a foreign task id leave that row's state alone: only a
    # currently blocked row flips back to working, so terminal rows a block
    # never touched stay done/failed.
    clear_block(store, "gb:a:push", "gb:done")
    clear_block(store, "gb:a:push", "gb:failed")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert {row["id"]: row["state"] for row in data["tasks"]} == {
        "gb:a": "blocked",
        "gb:done": "done",
        "gb:failed": "failed",
    }
    assert data["waiting_on_ove"] == []
    clear_block(store, "gb:a:push", "gb:a")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert {row["id"]: row["state"] for row in data["tasks"]} == {
        "gb:a": "working",
        "gb:done": "done",
        "gb:failed": "failed",
    }
    assert data["waiting_on_ove"] == []


def test_raise_never_reopens_terminal_rows(tmp_path: Path) -> None:
    store = _seed(
        tmp_path / "tasks.json",
        rows=[_row("gb:done", "done"), _row("gb:failed", "failed")],
    )
    # A late failure or a repeated push notice still records the item, but
    # the finished rows keep their terminal state, and clearing changes
    # nothing either.
    raise_block(store, "gb:done:late", "ask", "blocks", "gb:done")
    push_block(store, "gb:failed", "auto_review_blocked")
    push_block(store, "gb:failed", "auto_review_blocked")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert {row["id"]: row["state"] for row in data["tasks"]} == {
        "gb:done": "done",
        "gb:failed": "failed",
    }
    assert sorted(item["id"] for item in data["waiting_on_ove"]) == [
        "gb:done:late",
        "gb:failed:push",
    ]
    clear_block(store, "gb:done:late", "gb:done")
    clear_block(store, "gb:failed:push", "gb:failed")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert {row["id"]: row["state"] for row in data["tasks"]} == {
        "gb:done": "done",
        "gb:failed": "failed",
    }


# --- W111-05: push-step hook -------------------------------------------------


def test_push_block_raises_one_item_and_blocks_the_row(tmp_path: Path) -> None:
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:t1")])
    push_block(store, "gb:t1", "gh_not_logged_in")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert [item["id"] for item in data["waiting_on_ove"]] == ["gb:t1:push"]
    item = data["waiting_on_ove"][0]
    assert item["seq"] == 1
    assert item["since"]
    assert "gh auth login" in item["ask"]
    assert "gh_not_logged_in" in item["blocks"]
    assert data["tasks"][0]["state"] == "blocked"


def test_push_block_repeat_is_idempotent(tmp_path: Path) -> None:
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:t1")])
    push_block(store, "gb:t1", "auto_review_blocked")
    first = json.loads(store.read_text(encoding="utf-8"))["waiting_on_ove"][0]
    push_block(store, "gb:t1", "gh_not_logged_in")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert [item["id"] for item in data["waiting_on_ove"]] == ["gb:t1:push"]
    repeat = data["waiting_on_ove"][0]
    assert repeat["seq"] == first["seq"] == 1
    assert repeat["since"] == first["since"]
    assert "gh auth login" in repeat["ask"]
    assert data["tasks"][0]["state"] == "blocked"


def test_push_block_rejects_unknown_reasons_without_writing(tmp_path: Path) -> None:
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:t1")])
    before = store.read_bytes()
    with pytest.raises(ValueError, match="unknown push reason"):
        push_block(store, "gb:t1", "bogus_reason")
    assert store.read_bytes() == before


# --- W111-03: redaction ------------------------------------------------------


def _shaped_inputs() -> tuple[str, str]:
    """Credential-shaped strings built at runtime, never stored as literals."""
    token = "".join(("gh", "p_", "leak", "me", "12345"))
    assignment = "token=" + "".join(("hunter", "2-", "value"))
    return token, assignment


def test_stored_text_is_redacted_and_never_the_input(tmp_path: Path) -> None:
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:t1"), _row("gb:t2")])
    token, assignment = _shaped_inputs()
    raise_block(
        store,
        "blk-secret",
        f"paste {token} and {assignment}",
        f"Bearer {token}",
        "gb:t1",
    )
    push_block(store, "gb:t2", "auto_review_blocked", detail=f"argv-ish {assignment}")
    raw = store.read_text(encoding="utf-8")
    assert token not in raw
    assert assignment not in raw
    assert "hunter2-value" not in raw
    assert REDACTED in raw
    data = json.loads(store.read_text(encoding="utf-8"))
    assert [item["id"] for item in data["waiting_on_ove"]] == [
        "blk-secret",
        "gb:t2:push",
    ]


# --- W111-04: engine failure wiring -----------------------------------------


def _task_store(tmp_path: Path, task_id: str) -> Path:
    return _seed(tmp_path / "tasks.json", rows=[_row(task_id)])


def test_engine_gated_failure_raises_one_block_in_the_same_call(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary)
    env.pop("ANTHROPIC_API_KEY")  # empty allowlist: the plan gate refuses
    store = _task_store(tmp_path, "gb:t1")
    result = run(
        "PONG",
        cwd=tmp_path,
        env=env,
        job_id="job-gate",
        stage="plan",
        tasks_path=store,
        task_id="gb:t1",
    )
    assert result.ok is False
    assert result.reason == "plan_not_allowed"
    # Immediacy: the item and the blocked row exist the moment run() returned,
    # with no polling step in between.
    data = json.loads(store.read_text(encoding="utf-8"))
    items = data["waiting_on_ove"]
    assert [item["id"] for item in items] == ["gb:t1:plan_not_allowed"]
    assert items[0]["blocks"] == "engine plan gate (plan_not_allowed)"
    assert "OMEGA_PRIME_OMP_ALLOW_ZAI" in items[0]["ask"]
    assert data["tasks"][0] == {"id": "gb:t1", "state": "blocked"}
    first = dict(items[0])
    # A retried run upserts instead of duplicating.
    run(
        "PONG",
        cwd=tmp_path,
        env=env,
        job_id="job-gate-2",
        stage="plan",
        tasks_path=store,
        task_id="gb:t1",
    )
    data = json.loads(store.read_text(encoding="utf-8"))
    assert [item["id"] for item in data["waiting_on_ove"]] == ["gb:t1:plan_not_allowed"]
    assert data["waiting_on_ove"][0]["seq"] == first["seq"]
    assert data["waiting_on_ove"][0]["since"] == first["since"]
    # Resolution clears the item and returns the task to working.
    clear_block(store, "gb:t1:plan_not_allowed", "gb:t1")
    data = json.loads(store.read_text(encoding="utf-8"))
    assert data["waiting_on_ove"] == []
    assert data["tasks"][0]["state"] == "working"


def test_engine_tier_failure_raises_its_own_block(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    env = _env(
        tmp_path,
        binary,
        OMEGA_PRIME_ENGINE_UNSANDBOXED="0",
        OMEGA_PRIME_BWRAP_BIN=str(tmp_path / "no-bwrap"),
    )
    store = _task_store(tmp_path, "gb:t2")
    result = run(
        "PONG",
        cwd=tmp_path,
        env=env,
        job_id="job-tier",
        tasks_path=store,
        task_id="gb:t2",
    )
    assert result.ok is False
    assert result.reason == "sandbox_unavailable"
    data = json.loads(store.read_text(encoding="utf-8"))
    assert [item["id"] for item in data["waiting_on_ove"]] == [
        "gb:t2:sandbox_unavailable"
    ]
    assert data["waiting_on_ove"][0]["blocks"] == (
        "engine sandbox gate (sandbox_unavailable)"
    )
    assert data["tasks"][0]["state"] == "blocked"


def test_engine_writes_no_tasks_file_without_task_context(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary)
    env.pop("ANTHROPIC_API_KEY")
    result = run("PONG", cwd=tmp_path, env=env, job_id="job-noctx", stage="plan")
    assert result.ok is False
    assert result.reason == "plan_not_allowed"
    assert not (tmp_path / "tasks.json").exists()
    # tasks_path without task_id is equally inert.
    store = tmp_path / "tasks.json"
    result = run(
        "PONG",
        cwd=tmp_path,
        env=env,
        job_id="job-halfctx",
        stage="plan",
        tasks_path=store,
    )
    assert result.ok is False
    assert result.reason == "plan_not_allowed"
    assert not store.exists()


def test_engine_ungated_failure_writes_no_item(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary, OMEGA_PRIME_OMP_BIN=str(tmp_path / "bin" / "missing"))
    store = _task_store(tmp_path, "gb:t3")
    result = run(
        "PONG",
        cwd=tmp_path,
        env=env,
        job_id="job-noomp",
        tasks_path=store,
        task_id="gb:t3",
    )
    assert result.ok is False
    assert result.reason == "not_configured"
    data = json.loads(store.read_text(encoding="utf-8"))
    assert data["waiting_on_ove"] == []
    assert data["tasks"][0]["state"] == "working"


# --- W113-02: run_rows lifecycle (start/touch/finish) -----------------------


def _row_by_id(store: Path, task_id: str) -> dict:
    data = json.loads(store.read_text(encoding="utf-8"))
    return next(row for row in data["tasks"] if row["id"] == task_id)


def test_run_rows_start_creates_the_working_row_and_pins_started_at(
    tmp_path: Path,
) -> None:
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:t1")])
    start(store, "gb:new", job_id="job-1", stage="plan", pane="pane-7")
    row = _row_by_id(store, "gb:new")
    assert row["state"] == "working"
    assert row["job_id"] == "job-1"
    assert row["stage"] == "plan"
    assert row["herdr_pane"] == "pane-7"
    started = datetime.fromisoformat(row["started_at"])
    activity = datetime.fromisoformat(row["last_activity_at"])
    assert started <= activity
    # A re-start moves the row back to working with started_at untouched;
    # a later pane=None leaves the recorded pane alone.
    start(store, "gb:new", job_id="job-2", stage="execute", pane=None)
    again = _row_by_id(store, "gb:new")
    assert again["state"] == "working"
    assert again["started_at"] == row["started_at"]
    assert again["stage"] == "execute"
    assert again["herdr_pane"] == "pane-7"


def test_run_rows_start_never_resurrects_terminal_rows(tmp_path: Path) -> None:
    store = _seed(
        tmp_path / "tasks.json",
        rows=[_row("gb:done", "done", receipt={"job_id": "job-0"})],
    )
    before = store.read_bytes()
    start(store, "gb:done", job_id="job-1", stage="plan", pane="pane-7")
    assert store.read_bytes() == before


def test_run_rows_start_returns_a_blocked_row_to_working(tmp_path: Path) -> None:
    store = _seed(
        tmp_path / "tasks.json",
        rows=[
            _row(
                "gb:blocked",
                "blocked",
                started_at="2026-01-01T00:00:00+00:00",
            )
        ],
    )
    start(store, "gb:blocked", job_id="job-1", stage="plan", pane=None)
    row = _row_by_id(store, "gb:blocked")
    assert row["state"] == "working"
    assert row["started_at"] == "2026-01-01T00:00:00+00:00"


def test_run_rows_touch_labels_question_and_stage_done_events(
    tmp_path: Path,
) -> None:
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:t1")])
    touch(store, "gb:t1", {"event": "question"})
    assert _row_by_id(store, "gb:t1")["last_event"] == "question"
    assert datetime.fromisoformat(_row_by_id(store, "gb:t1")["last_activity_at"])
    touch(store, "gb:t1", {"event": "stage_done", "status": "completed"})
    assert _row_by_id(store, "gb:t1")["last_event"] == "stage_done completed"
    touch(store, "gb:t1", {"event": "stage_done", "status": ""})
    assert _row_by_id(store, "gb:t1")["last_event"] == "stage_done"
    # A plain string event is used verbatim.
    touch(store, "gb:t1", "seeded the worktree")
    assert _row_by_id(store, "gb:t1")["last_event"] == "seeded the worktree"
    # Any other event shape leaves the label alone.
    touch(store, "gb:t1", {"event": "up"})
    assert _row_by_id(store, "gb:t1")["last_event"] == "seeded the worktree"


def test_run_rows_touch_is_a_noop_for_missing_and_terminal_rows(
    tmp_path: Path,
) -> None:
    store = _seed(
        tmp_path / "tasks.json",
        rows=[_row("gb:done", "done", last_event="stage_done completed")],
    )
    before = store.read_bytes()
    touch(store, "gb:done", {"event": "question"})
    touch(store, "gb:absent", {"event": "question"})
    assert store.read_bytes() == before


def test_run_rows_writes_leave_other_rows_byte_identical(tmp_path: Path) -> None:
    other = _row(
        "gb:other",
        "working",
        owner="sol",
        nested={"keep": [1, 2]},
        herdr_pane="pane-9",
        last_activity_at="2026-01-01T00:00:00+00:00",
    )
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:t1"), other])
    touch(store, "gb:t1", {"event": "question"})
    start(store, "gb:t1", job_id="job-1", stage="plan", pane="pane-1")
    finish(store, "gb:t1", ok=True, receipt={"job_id": "job-1"})
    data = json.loads(store.read_text(encoding="utf-8"))
    assert data["tasks"][1] == other


def test_run_rows_finish_upserts_terminal_states(tmp_path: Path) -> None:
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:t1")])
    finish(
        store,
        "gb:t1",
        ok=True,
        receipt={"job_id": "job-1", "stage_id": "stage-1", "ok": True},
    )
    row = _row_by_id(store, "gb:t1")
    assert row["state"] == "done"
    assert row["receipt"] == {"job_id": "job-1", "stage_id": "stage-1", "ok": True}
    # A missing row is created terminal so a suppressed start still records.
    finish(
        store,
        "gb:missing",
        ok=False,
        receipt={"job_id": "job-2", "stage_id": None, "ok": False, "status": "failed"},
    )
    failed = _row_by_id(store, "gb:missing")
    assert failed["state"] == "failed"
    assert failed["receipt"]["status"] == "failed"
    assert datetime.fromisoformat(failed["started_at"])


def test_run_rows_finish_redacts_secret_shaped_receipt_strings(
    tmp_path: Path,
) -> None:
    token, _ = _shaped_inputs()
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:t1")])
    finish(
        store,
        "gb:t1",
        ok=True,
        receipt={"job_id": "job-1", "detail": f"handler saw {token}"},
    )
    raw = store.read_text(encoding="utf-8")
    assert token not in raw
    row = _row_by_id(store, "gb:t1")
    assert row["receipt"]["detail"] == f"handler saw {REDACTED}"
