# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Phase 111: immediate block surfacing into waiting_on_ove (W111-01..W111-05).

Store primitives, the raise/clear/push helpers, and the engine wiring.
tmp_path and fakes only: no network, no gh/herdr/podman/openshell, no
model calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omega_prime.credentials.redact import REDACTED
from omega_prime.dashboard.blocks import clear_block, push_block, raise_block
from omega_prime.dashboard.status_file import (
    ITEM_FIELDS,
    TASK_STATES,
    locked_tasks,
)
from omega_prime.engine.omp_driver import run
from omega_prime.tests.test_engine import _env, _install_fake


def _row(task_id: str, state: str = "working", **extra: object) -> dict:
    row: dict = {
        "id": task_id,
        "title": task_id,
        "owner": {"kind": "engine", "ref": task_id},
        "state": state,
    }
    row.update(extra)
    return row


def _seed(
    path: Path,
    rows: list[dict] | None = None,
    items: list[dict] | None = None,
) -> Path:
    """A dashboard tasks.json v1 (the validated store on main)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated_at": "2026-10-31T00:00:00+00:00",
        "tasks": rows or [],
        "waiting_on_ove": items or [],
        "finished": [],
        "next_seq": 1,
        "identity_registry": {},
    }
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


def test_blocks_write_through_the_validated_store(tmp_path: Path) -> None:
    # The store on main validates every write and owns row sequences: a block
    # takes the next free seq and keeps its owning task id.
    store = _seed(tmp_path / "tasks.json", [_row("gb:t1", seq=1)])
    raw = json.loads(store.read_text(encoding="utf-8"))
    raw["next_seq"] = 2
    store.write_text(json.dumps(raw), encoding="utf-8")
    raise_block(store, "gb:t1:plan", ask="a", blocks="b", task_id="gb:t1")
    with locked_tasks(store, write=False) as data:
        (item,) = data["waiting_on_ove"]
        assert (item["seq"], item["task_id"]) == (2, "gb:t1")
        assert data["next_seq"] == 3
        assert data["tasks"][0]["state"] == "blocked"


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
    assert (data["tasks"][0]["id"], data["tasks"][0]["state"]) == ("gb:t1", "blocked")
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
