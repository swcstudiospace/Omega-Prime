# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Regression tests for job outcomes and dashboard source visibility."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from omega_prime.dashboard.collectors.omega_jobs import make_omega_jobs_collector
from omega_prime.dashboard.model import SourceResult, normalize_tasks

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def test_job_terminal_event_overrides_session_and_invalidates_cache(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "job-one"
    directory.mkdir()
    session = directory / "session.json"
    session.write_text(json.dumps({"job_id": "one"}), encoding="utf-8")
    collector = make_omega_jobs_collector(jobs_root=tmp_path, now_fn=lambda: NOW)
    assert collector().items[0]["ok"] is None

    events = directory / "events.jsonl"
    events.write_text(
        json.dumps({"type": "job_end", "ok": False, "reason": "engine"}) + "\n",
        encoding="utf-8",
    )
    row = collector().items[0]
    assert row["ok"] is False
    snapshot = normalize_tasks(
        {"omega_job": SourceResult("omega_job", "ok", [row])}, now=NOW
    )
    assert [(item.id, item.result) for item in snapshot.finished] == [
        ("job:one", "failed")
    ]


def test_successful_job_keeps_redacted_summary(tmp_path: Path) -> None:
    directory = tmp_path / "job-two"
    directory.mkdir()
    (directory / "session.json").write_text(
        json.dumps(
            {
                "job_id": "two",
                "ok": True,
                "final_text": "  Finished successfully\nsecond line",
            }
        ),
        encoding="utf-8",
    )
    row = make_omega_jobs_collector(jobs_root=tmp_path, now_fn=lambda: NOW)().items[0]
    snapshot = normalize_tasks(
        {"omega_job": SourceResult("omega_job", "ok", [row])}, now=NOW
    )
    assert snapshot.finished[0].summary == "  Finished successfully"


def test_fleet_jobs_are_normalized_as_tasks() -> None:
    fleet = SourceResult(
        "fleet",
        "ok",
        [
            {"job_id": "r", "state": "running", "age_s": 10},
            {"job_id": "h", "state": "hitl", "age_s": 20},
            {"job_id": "q", "state": "queued", "age_s": 30},
            {"job_id": "d", "state": "done", "age_s": 40},
        ],
    )
    snapshot = normalize_tasks({"fleet": fleet}, now=NOW)
    assert {task.id: task.state for task in snapshot.active} == {
        "fleet:r": "working",
        "fleet:h": "blocked",
        "fleet:q": "idle",
    }
    assert [(item.id, item.result) for item in snapshot.finished] == [("fleet:d", "ok")]


def test_only_unmatched_omp_process_becomes_a_task() -> None:
    sources = {
        "omp_session": SourceResult(
            "omp_session",
            "ok",
            [
                {
                    "session_path": "/session/live",
                    "cwd": "/matched",
                    "last_ts": NOW.isoformat(),
                }
            ],
        ),
        "omp_proc": SourceResult(
            "omp_proc",
            "ok",
            [
                {"pid": 101, "cwd": "/matched", "elapsed_s": 5},
                {"pid": 202, "cwd": "/unmatched", "elapsed_s": 7},
            ],
        ),
    }
    snapshot = normalize_tasks(sources, now=NOW)
    proc_tasks = [task for task in snapshot.active if task.source == "omp_proc"]
    assert [(task.id, task.title, task.state) for task in proc_tasks] == [
        ("proc:202", "omp process 202", "working")
    ]
