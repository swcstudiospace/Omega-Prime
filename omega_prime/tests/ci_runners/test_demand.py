"""Restart-safe demand: persisted queue, TTL, corrupt-file tolerance."""

from __future__ import annotations

import json
import stat
from pathlib import Path

from omega_prime.ci_runners.demand import DEMAND_TTL_S, DemandStore
from omega_prime.ci_runners.jobs import JobStatus, WorkflowJob

from ._support import FakeClock


def _job(job_id: int, status: JobStatus = JobStatus.QUEUED) -> WorkflowJob:
    return WorkflowJob(
        job_id=job_id,
        run_id=job_id * 10,
        repo="acme/widgets",
        private=True,
        labels=("self-hosted", "omega-ci"),
        status=status,
    )


def test_round_trip_keeps_first_seen_and_drops_non_queued(tmp_path: Path) -> None:
    wall = FakeClock(1_000_000.0)
    path = tmp_path / "state" / "demand.json"
    store = DemandStore(path, wall=wall)
    store.save([_job(1), _job(2, JobStatus.IN_PROGRESS)])
    wall.advance(60)
    store.save([_job(1), _job(3)])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    rows = json.loads(path.read_text())["jobs"]
    assert [(r["job_id"], r["seen_at"]) for r in rows] == [
        (1, 1_000_000.0),
        (3, 1_000_060.0),
    ]
    assert set(rows[0]) == {
        "job_id",
        "run_id",
        "repo",
        "private",
        "public",
        "fork",
        "labels",
        "seen_at",
    }
    assert DemandStore(path, wall=wall).load() == (_job(1), _job(3))


def test_entries_older_than_the_ttl_are_dropped(tmp_path: Path) -> None:
    wall = FakeClock(5_000_000.0)
    path = tmp_path / "demand.json"
    DemandStore(path, wall=wall).save([_job(1)])
    wall.advance(DEMAND_TTL_S + 1)
    DemandStore(path, wall=wall).save([])  # unrelated
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {**_row(1), "seen_at": 5_000_000.0},
                    {**_row(2), "seen_at": wall.now - 10},
                ],
            }
        )
    )
    assert [j.job_id for j in DemandStore(path, wall=wall).load()] == [2]


def _row(job_id: int) -> dict[str, object]:
    return {
        "job_id": job_id,
        "run_id": 7,
        "repo": "acme/widgets",
        "private": True,
        "labels": ["omega-ci"],
    }


def test_missing_corrupt_or_malformed_files_load_empty(tmp_path: Path) -> None:
    path = tmp_path / "demand.json"
    assert DemandStore(path).load() == ()
    for text in ("{not json", "[]", '{"jobs": 3}', "\udcff"):
        path.write_text(text, errors="surrogateescape")
        assert DemandStore(path).load() == ()
    bad_rows = [
        "x",
        {**_row(1), "job_id": True},
        {**_row(2), "repo": 5},
        {**_row(3), "labels": [1]},
        {**_row(4), "seen_at": "now"},
        {**_row(5), "seen_at": 1e18},  # in the future is fine
        {**_row(5), "seen_at": 1e18},  # duplicate id is skipped
    ]
    path.write_text(json.dumps({"jobs": bad_rows}))
    assert [j.job_id for j in DemandStore(path).load()] == [5]
