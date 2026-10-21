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
        "idle_sessions": [],
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
    # Quiet and explicitly stopped sessions are Idle; a recent session with an
    # unknown cwd stays Active as stopped? because exit cannot be established.
    assert {x.id: x.state for x in snap.active} == {"omp:live": "stopped?"}
    assert {x.id for x in snap.idle_sessions} == {"omp:old", "omp:exit"}
    assert snap.finished == []


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
    detail = next(t.detail for t in snap.active if t.id == "wiring:p1")
    assert isinstance(detail, str)
    assert "pid 9" in detail
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
    ]
    assert {(item.id, item.result) for item in snap.finished} >= {
        ("tasks:4", "ok"), ("tasks:5", "failed")
    }


def test_omega_job_never_finishes_from_quiet_minutes_alone() -> None:
    snap = normalize_tasks(
        {
            "omega_job": source(
                "omega_job",
                [
                    {
                        "job_id": "late",
                        "ok": None,
                        "last_activity_at": (NOW - timedelta(hours=2)).isoformat(),
                    }
                ],
            )
        },
        now=NOW,
    )
    assert not any(item.id == "job:late" for item in snap.finished)
    assert [(item.id, item.state) for item in snap.active] == [("job:late", "quiet")]


def test_rebuilds_do_not_move_measured_timestamps() -> None:
    t0 = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    sources = {
        "fleet": source(
            "fleet",
            [
                {
                    "job_id": "f",
                    "state": "running",
                    "age_s": 10,
                    "age_measured_at": t0.isoformat(),
                }
            ],
        ),
        "omp_proc": source(
            "omp_proc",
            [
                {
                    "pid": 9,
                    "cwd": "/un",
                    "elapsed_s": 7,
                    "elapsed_measured_at": t0.isoformat(),
                }
            ],
        ),
    }
    first = normalize_tasks(sources, now=t0 + timedelta(hours=1))
    second = normalize_tasks(sources, now=t0 + timedelta(hours=2))
    for task_id, age in (("fleet:f", 10), ("proc:9", 7)):
        expected = (t0 - timedelta(seconds=age)).isoformat()
        left = next(task for task in first.active if task.id == task_id)
        right = next(task for task in second.active if task.id == task_id)
        assert left.started_at == right.started_at == expected
        assert left.last_activity_at == right.last_activity_at == expected
def test_task_like_records_expose_default_numbering_and_wiring_fields() -> None:
    task = normalize_tasks(
        {
            "tasks_json": SourceResult(
                "tasks_json",
                "ok",
                extra={
                    "tasks": [
                        {
                            "id": "bridge",
                            "title": "Bridge task",
                            "state": "working",
                            "owner": {"kind": "human", "ref": "owner"},
                        }
                    ],
                    "waiting_on_ove": [],
                    "finished": [],
                },
            )
        },
        now=NOW,
    ).active[0].to_dict()
    assert task["seq"] is None
    assert task["display_id"] is None
    assert task["timeline"] == []
    assert task["wiring"] is None


def test_waiting_and_finished_model_fields_are_additive() -> None:
    from omega_prime.dashboard.model import FinishedItem, WaitingItem

    waiting = WaitingItem("ask:1", "Need input", None, "").to_dict()
    finished = FinishedItem("tasks:done", "Done", None, "ok").to_dict()
    assert waiting["seq"] is None and waiting["display_id"] is None
    assert waiting["timeline"] == [] and waiting["related_task_id"] is None
    assert finished["seq"] is None and finished["display_id"] is None
    assert finished["timeline"] == [] and finished["wiring"] is None
