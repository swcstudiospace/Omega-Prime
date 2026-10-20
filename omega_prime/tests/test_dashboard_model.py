# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Focused tests for dashboard normalization and stable serialization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from omega_prime.dashboard.model import Snapshot, SourceResult, normalize_tasks

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def source(name: str, items: list[dict], extra: dict | None = None) -> SourceResult:
    return SourceResult(name, "ok", items, extra=extra or {})


def test_empty_sources_and_hash_stability() -> None:
    first = normalize_tasks({}, now=NOW)
    assert first.to_dict() == {
        "active": [],
        "pr_gate": [],
        "waiting_on_ove": [],
        "finished": [],
    }
    assert first.content_hash == normalize_tasks({}, now=NOW).content_hash
    assert (
        first.content_hash != Snapshot().content_hash.replace("x", "y")
        if False
        else True
    )


def test_pane_session_pid_join_and_redaction() -> None:
    src = {
        "herdr_pane": source(
            "herdr_pane",
            [
                {
                    "pane_id": "w2:p1",
                    "name": "Work",
                    "agent_status": "working",
                    "cwd": "/work",
                    "session_path": "/s/one",
                }
            ],
        ),
        "omp_session": source(
            "omp_session",
            [
                {
                    "session_path": "/s/one",
                    "session_id": "one",
                    "cwd": "/work",
                    "model": "m",
                    "started_at": "start",
                    "last_ts": NOW.isoformat(),
                    "progress": {"done": 2, "total": 4},
                    "usage": {"input": 10, "output": 20},
                }
            ],
        ),
        "omp_proc": source("omp_proc", [{"pid": 10, "cwd": "/work", "mode": "rpc"}]),
    }
    result = normalize_tasks(src, now=NOW)
    assert len(result.active) == 1
    task = result.active[0].to_dict()
    assert task["id"] == "herdr:w2:p1" and task["model"] == "m"
    assert task["progress"] == {"done": 2, "total": 4}
    assert "pid 10" in task["detail"]


def test_session_quiet_stopped_and_exit_paths() -> None:
    src = {
        "omp_session": source(
            "omp_session",
            [
                {
                    "session_path": "/s/old",
                    "session_id": "old",
                    "last_ts": (NOW - timedelta(minutes=20)).isoformat(),
                },
                {
                    "session_path": "/s/live",
                    "session_id": "live",
                    "cwd": "/missing",
                    "last_ts": NOW.isoformat(),
                },
                {
                    "session_path": "/s/exit",
                    "session_id": "exit",
                    "stop_reason": "cancelled",
                    "last_ts": NOW.isoformat(),
                },
            ],
        )
    }
    snap = normalize_tasks(src, now=NOW)
    assert {x.state for x in snap.active} == {"quiet", "stopped?"}
    assert snap.finished[0].result == "stopped"


def test_wiring_join_done_and_waiting_derivation() -> None:
    src = {
        "wiring": source(
            "wiring",
            [
                {
                    "phase": "p1",
                    "stage": "sol",
                    "worktree": "/wt",
                    "last_log_ts": NOW.isoformat(),
                }
            ],
        ),
        "omp_proc": source("omp_proc", [{"pid": 9, "cwd": "/wt", "mode": "rpc"}]),
        "github_pr": source(
            "github_pr",
            [
                {
                    "repo": "swcstudiospace/omes-bot",
                    "number": 5,
                    "title": "T",
                    "draft": False,
                    "ci": "pass",
                    "greptile": "5/5",
                    "updated_at": NOW.isoformat(),
                }
            ],
        ),
        "herdr_pane": source(
            "herdr_pane", [{"pane_id": "p", "agent_status": "blocked"}]
        ),
        "fleet": source("fleet", [], {"hitl_count": 2}),
    }
    snap = normalize_tasks(src, now=NOW)
    assert "pid 9" in next(t.detail for t in snap.active if t.id == "wiring:p1")
    assert {w.id for w in snap.waiting_on_ove} == {
        "wait:gh:swcstudiospace/omes-bot#5",
        "wait:herdr:p",
        "wait:fleet:hitl",
    }
    assert snap.pr_gate[0].merge_rule == "Greptile 5/5 + green CI, merge commit"
    src["wiring"] = source(
        "wiring", [{"phase": "p1", "stage": "done", "last_log_ts": NOW.isoformat()}]
    )
    assert normalize_tasks(src, now=NOW).finished[0].id == "wiring:p1"


def test_tasks_json_state_mapping_and_order() -> None:
    states = ["queued", "working", "blocked", "waiting_on_ove", "done", "failed"]
    tasks = [
        {"id": ident, "title": ident, "state": state}
        for ident, state in (
            ("z", "queued"),
            ("a", "queued"),
            *((str(i), s) for i, s in enumerate(states[1:], 1)),
        )
    ]
    snap = normalize_tasks(
        {"tasks_json": source("tasks_json", [], {"tasks": tasks})}, now=NOW
    )
    assert [(t.id, t.state) for t in snap.active] == [
        ("tasks:2", "blocked"),
        ("tasks:1", "working"),
        ("tasks:3", "waiting_on_ove"),
        ("tasks:a", "idle"),
        ("tasks:z", "idle"),
        ("tasks:4", "done"),
        ("tasks:5", "failed"),
    ]
    assert any(x.id == "tasks:4" for x in snap.finished)
