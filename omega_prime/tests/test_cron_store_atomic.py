# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""The job store replaces its file atomically.

`JobStore._save` used to truncate and rewrite `jobs.json` in place, so a reader
racing a save (or a crash mid-write) saw an empty file. That made
`test_apscheduler_backend` fail intermittently in CI and put the whole schedule
at risk of corruption.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from omega_prime.cron.scheduler import JobStore


def test_save_leaves_only_the_store_file(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.json")
    store.schedule("hello", 1)
    store.schedule("again", 2)
    assert [p.name for p in tmp_path.iterdir()] == ["jobs.json"]
    persisted = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert [job["prompt"] for job in persisted["jobs"]] == ["hello", "again"]


def test_a_reader_never_sees_a_partial_store(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    store = JobStore(path)
    store.schedule("seed", 0)
    stop = threading.Event()
    errors: list[Exception] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # truncated file or a missing store
                errors.append(exc)

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        for i in range(300):
            store.schedule(f"job-{i}", i)
    finally:
        stop.set()
        thread.join()
    assert errors == []


def test_a_failed_replace_keeps_the_previous_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "jobs.json"
    store = JobStore(path)
    store.schedule("keep", 1)
    before = path.read_text(encoding="utf-8")

    def boom(src: str, dst: str) -> None:
        del src, dst
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk went away"):
        store.schedule("lost", 2)
    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == before
    assert [p.name for p in tmp_path.iterdir()] == ["jobs.json"]


def test_an_existing_store_keeps_its_file_mode(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    store = JobStore(path)
    store.schedule("first", 1)
    os.chmod(path, 0o640)
    store.schedule("second", 2)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_two_store_instances_cannot_claim_one_occurrence_twice(tmp_path: Path) -> None:

    path = tmp_path / "jobs.json"
    first = JobStore(path)
    second = JobStore(path)
    now = 1_700_000_000
    first.schedule("one occurrence", now)
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def runner(prompt: str) -> str:
        calls.append(prompt)
        started.set()
        assert release.wait(timeout=5)
        return '{"summary":"done"}'

    result: list[dict] = []
    worker = threading.Thread(
        target=lambda: result.extend(first.tick(now, runner, write_receipts=True))
    )
    worker.start()
    assert started.wait(timeout=5)

    def duplicate_runner(prompt: str) -> str:
        calls.append(prompt)
        return '{"summary":"duplicate"}'

    assert second.tick(now, duplicate_runner, write_receipts=True) == []
    release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert calls == ["one occurrence"]
    assert len(result) == 1
    assert JobStore(path).jobs[0]["history"][-1]["scheduled_for"] == now


def test_stale_claim_settlement_is_refused_and_preserves_unrelated_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "jobs.json"
    store = JobStore(path)
    now = 1_700_000_000
    job_id = store.schedule("claimed", now)
    unrelated_id = store.schedule("unrelated future", now + 600)
    claim = store._claim(job_id, now)
    assert claim is not None

    with store._store_transaction():
        row = next(job for job in store.jobs if job["id"] == job_id)
        row["claim_token"] = "new-owner"
        row["unrelated_state"] = "kept"
        store._save()

    assert (
        store._settle_claim(
            claim, now, {"state": "succeeded", "summary": "stale"}, "succeeded"
        )
        is None
    )
    rows = {job["id"]: job for job in JobStore(path).jobs}
    assert rows[job_id]["claim_token"] == "new-owner"
    assert rows[job_id]["unrelated_state"] == "kept"
    assert rows[job_id]["history"] == []
    assert rows[unrelated_id]["due_at"] == now + 600


def test_worker_restart_recovers_persisted_claim_token(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    store = JobStore(path)
    now = 1_700_000_000
    job_id = store.schedule("recover after restart", now)
    claims = []
    worker = threading.Thread(target=lambda: claims.append(store._claim(job_id, now)))
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert claims[0] is not None

    ran = JobStore(path).tick(now, lambda _prompt: "recovered")

    assert [job["id"] for job in ran] == [job_id]
    recovered = JobStore(path).jobs[0]
    assert recovered["claim_token"] is None
    assert recovered["last_result"] == "recovered"


def test_typed_claim_recovers_after_worker_thread_exits(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    store = JobStore(path)
    now = 1_700_000_000
    kind = "typed-recovery-test"
    job_id = store._schedule_typed("recover typed claim", now, kind=kind)
    claims = []
    worker = threading.Thread(
        target=lambda: claims.append(store._claim_typed(job_id, kind, now))
    )
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    abandoned = claims[0]
    assert abandoned is not None

    recovered = JobStore(path)._claim_typed(job_id, kind, now)

    assert recovered is not None
    assert recovered["job"]["id"] == job_id
    assert recovered["token"] != abandoned["token"]


def test_invalid_lateness_grace_does_not_leave_a_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JobStore(tmp_path / "jobs.json")
    job_id = store.schedule("must remain unclaimed", 100)
    monkeypatch.setenv("OMEGA_PRIME_CRON_LATENESS_GRACE_SECONDS", "invalid")

    with pytest.raises(ValueError, match="OMEGA_PRIME_CRON_LATENESS_GRACE_SECONDS"):
        store.tick(100, lambda _prompt: "unexpected")

    row = next(job for job in JobStore(store.path).jobs if job["id"] == job_id)
    assert row["claim_token"] is None
    assert row["claimed_at"] is None
