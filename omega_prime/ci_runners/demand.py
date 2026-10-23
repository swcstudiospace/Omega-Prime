"""Accepted-but-unserved demand that must survive a controller restart.

GitHub sends each ``queued`` webhook once. If the controller restarts with a
backlog larger than its live runners, those jobs would otherwise be forgotten:
the surviving runners finish and nothing launches replacements. The
controller therefore mirrors the scaler's queue to a small JSON file
(non-secret job fields only, written atomically with mode 0600) and replays
it in the one startup reconcile. Entries older than ``DEMAND_TTL_S`` are
dropped on load: GitHub cancels a job that waits that long anyway.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from omega_prime.ci_runners.jobs import JobStatus, WorkflowJob
from omega_prime.tooling.fs import atomic_write_json

DEMAND_TTL_S = 24 * 3600.0
MAX_DEMAND = 4096
_VERSION = 1


class DemandStore:
    def __init__(
        self,
        path: Path,
        *,
        wall: Callable[[], float] = time.time,
        ttl_s: float = DEMAND_TTL_S,
    ) -> None:
        self.path = path
        self._wall = wall
        self._ttl_s = ttl_s
        self._seen: dict[int, float] = {}

    def load(self) -> tuple[WorkflowJob, ...]:
        """The persisted queue, oldest first. Missing or corrupt → empty."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, UnicodeDecodeError, ValueError):
            return ()
        rows = raw.get("jobs") if isinstance(raw, dict) else None
        if not isinstance(rows, list):
            return ()
        now = self._wall()
        out: list[WorkflowJob] = []
        for row in rows[:MAX_DEMAND]:
            parsed = _job(row)
            if parsed is None:
                continue
            job, seen_at = parsed
            if now - seen_at > self._ttl_s or job.job_id in self._seen:
                continue
            self._seen[job.job_id] = seen_at
            out.append(job)
        return tuple(out)

    def save(self, jobs: Iterable[WorkflowJob]) -> None:
        now = self._wall()
        queued = [j for j in jobs if j.status is JobStatus.QUEUED][:MAX_DEMAND]
        self._seen = {j.job_id: self._seen.get(j.job_id, now) for j in queued}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write_json(
            self.path,
            {
                "version": _VERSION,
                "jobs": [
                    {
                        "job_id": j.job_id,
                        "run_id": j.run_id,
                        "repo": j.repo,
                        "private": j.private,
                        "public": j.public,
                        "labels": list(j.labels),
                        "seen_at": self._seen[j.job_id],
                    }
                    for j in queued
                ],
            },
        )


def _positive(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _job(row: Any) -> tuple[WorkflowJob, float] | None:
    if not isinstance(row, dict):
        return None
    job_id, run_id = _positive(row.get("job_id")), _positive(row.get("run_id"))
    repo, labels = row.get("repo"), row.get("labels")
    seen_at, private = row.get("seen_at"), row.get("private")
    public = row.get("public") is True and private is not True
    if job_id is None or run_id is None or not isinstance(repo, str):
        return None
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
        return None
    if isinstance(seen_at, bool) or not isinstance(seen_at, (int, float)):
        return None
    job = WorkflowJob(
        job_id=job_id,
        run_id=run_id,
        repo=repo,
        private=private is True,
        labels=tuple(labels),
        status=JobStatus.QUEUED,
        public=public,  # ``trusted`` is never persisted; replay re-verifies
    )
    return job, float(seen_at)


__all__ = ["DEMAND_TTL_S", "MAX_DEMAND", "DemandStore"]
