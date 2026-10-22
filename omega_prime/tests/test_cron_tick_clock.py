"""Queued scheduler jobs judge lateness on a fresh clock. Hermetic.

Each tick selects due jobs at its ``now`` and runs them one after another.
When an earlier job is slow, a later queued job must be checked against the
time it actually starts, not the tick-start value, so a stale action (a pin
bump, a desk pass, a prompt dispatch, a purple cycle) cannot slip past the
lateness grace. The RoE watchdog only reads its grant, so an overdue
watchdog still reads it and records fresh state.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from omega_prime.cron import scheduler as sched
from omega_prime.cron.scheduler import JobStore

GRACE = 300
NOW = 1_700_000_000
SLOW = GRACE + 100


class _FakeMonotonic:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setenv(sched.CRON_LATENESS_GRACE_ENV, str(GRACE))
    fake = _FakeMonotonic()
    # raising=False: without the fix the seam does not exist, and the test
    # must then fail on behaviour (the second job runs), not on setup.
    monkeypatch.setattr(sched, "_monotonic", fake, raising=False)
    return fake


def _rows(store: JobStore) -> dict:
    return {job["id"]: job for job in JobStore(store.path).jobs}


def test_prompt_tick_rechecks_lateness_after_slow_job(tmp_path, clock):
    store = JobStore(tmp_path / "jobs.json")
    first = store.schedule("first", NOW - 10, interval_seconds=600)
    second = store.schedule("second", NOW - 10, interval_seconds=600)
    calls: list[str] = []

    def runner(prompt: str) -> str:
        calls.append(prompt)
        clock.advance(SLOW)
        return "done"

    store.tick(NOW, runner)

    rows = _rows(store)
    assert calls == ["first"]
    assert rows[first]["last_ran_at"] == NOW
    assert rows[second]["last_result"]["state"] == "missed"
    assert rows[second]["last_result"]["evaluated_at"] == NOW + SLOW
    assert rows[second]["due_at"] == NOW + SLOW + 600


def test_desk_tick_rechecks_lateness_after_slow_pass(tmp_path, clock):
    store = JobStore(tmp_path / "jobs.json")
    first = sched.schedule_desk_lead_pass(store, due_at=NOW - 10, interval_seconds=60)
    second = sched.schedule_desk_lead_pass(store, due_at=NOW - 10, interval_seconds=60)
    calls: list[str] = []

    def runner(job: dict) -> dict:
        calls.append(job["id"])
        clock.advance(SLOW)
        return {"state": "succeeded"}

    sched.tick_desk_lead_passes(store, NOW, runner)

    rows = _rows(store)
    assert calls == [first]
    assert rows[second]["last_result"]["state"] == "missed"
    assert rows[second]["last_result"]["lateness"] == SLOW + 10
    assert rows[second]["due_at"] == NOW + SLOW + 60


def test_pin_bump_tick_rechecks_lateness_after_slow_audit(tmp_path, clock):
    store = JobStore(tmp_path / "cron" / "jobs.json")
    first = sched.schedule_pin_bump(
        store, cron_expr="* * * * *", name="a", now=NOW - 120
    )
    second = sched.schedule_pin_bump(
        store, cron_expr="* * * * *", name="b", now=NOW - 120
    )
    audited: list[str] = []

    def audit(job: dict) -> dict:
        audited.append(job["id"])
        clock.advance(SLOW)
        return {"verdict": "improved"}

    sched.tick_pin_bump_schedules(store, NOW, audit=audit, approval_log=None)

    rows = _rows(store)
    assert audited == [first]
    assert rows[second]["last_result"]["disposition"] == "missed"
    assert rows[second]["last_result"]["evaluated_at"] == NOW + SLOW
    assert rows[second]["due_at"] > NOW + SLOW


def test_purple_tick_rechecks_lateness_after_slow_cycle(tmp_path, clock):
    store = JobStore(tmp_path / "jobs.json")
    first = sched.schedule_purple_cycle(store, due_at=NOW - 10, interval_seconds=90)
    second = sched.schedule_purple_cycle(store, due_at=NOW - 10, interval_seconds=90)
    calls: list[str] = []

    def runner(job: dict) -> dict:
        calls.append(job["id"])
        clock.advance(SLOW)
        return {"ok": True}

    sched.tick_purple_cycles(store, NOW, runner=runner)

    rows = _rows(store)
    assert calls == [first]
    assert rows[first]["due_at"] == NOW + 90
    assert rows[second]["last_result"]["state"] == "missed"
    assert rows[second]["due_at"] == NOW + SLOW + 90


def test_fast_tick_keeps_injected_stamp_exact(tmp_path, clock):
    store = JobStore(tmp_path / "jobs.json")
    ids = [store.schedule(f"job {i}", NOW - 10, interval_seconds=30) for i in range(3)]

    store.tick(NOW, lambda prompt: "ok")

    rows = _rows(store)
    assert [rows[i]["last_ran_at"] for i in ids] == [NOW, NOW, NOW]
    assert [rows[i]["due_at"] for i in ids] == [NOW + 30] * 3


END = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _grant(tmp_path, until: datetime):
    path = tmp_path / "roe.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operator": "test-operator",
                "authorized_targets": [
                    {"host": "127.0.0.1", "tools": ["tempest_status"]}
                ],
                "effective_from": (END - timedelta(days=30)).isoformat(
                    timespec="seconds"
                ),
                "effective_until": until.isoformat(timespec="seconds"),
                "case_classes": ["prompt-injection"],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_overdue_watchdog_still_reads_grant_and_flips_state(tmp_path, clock):
    store = JobStore(tmp_path / "cron" / "jobs.json")
    artifact = _grant(tmp_path, END + timedelta(days=30))
    job_id = sched.schedule_roe_watchdog(
        store, str(artifact), due_at=END.timestamp() - 3600, interval_seconds=86400
    )
    # Seed the last recorded state as valid, then let the grant expire.
    sched.tick_roe_watchdog(store, END - timedelta(hours=1))
    assert _rows(store)[job_id]["roe_watchdog_state"] == "valid"
    artifact.unlink()
    artifact = _grant(tmp_path, END - timedelta(days=1))

    # A restart picks the watchdog up a full day and an hour late.
    late_now = END + timedelta(days=1)
    ran = sched.tick_roe_watchdog(store, late_now)

    assert [job["id"] for job in ran] == [job_id]
    row = _rows(store)[job_id]
    assert row["last_result"]["state"] == "expired"
    assert row["roe_watchdog_state"] == "expired"
    assert row["roe_watchdog_digest"][-1]["from"] == "valid"
    assert row["roe_watchdog_digest"][-1]["to"] == "expired"
    assert row["history"][-1]["late"]["lateness"] == 3600.0
    assert row["due_at"] == late_now.timestamp() + 86400


def test_queued_watchdog_checks_grant_at_its_own_start_time(tmp_path, clock):
    store = JobStore(tmp_path / "cron" / "jobs.json")
    seen: list[datetime] = []

    def checker(artifact: str, *, now: datetime) -> dict:
        seen.append(now)
        clock.advance(SLOW)
        return {
            "state": "valid",
            "artifact_id": artifact,
            "effective_until": None,
            "action": "none",
        }

    for name in ("a.json", "b.json"):
        sched.schedule_roe_watchdog(
            store, name, due_at=END.timestamp() - 10, interval_seconds=60
        )

    ran = sched.tick_roe_watchdog(store, END, checker=checker)

    assert len(ran) == 2
    assert seen == [END, END + timedelta(seconds=SLOW)]
    assert "late" not in ran[0]["history"][-1]
    assert ran[1]["history"][-1]["late"]["lateness"] == SLOW + 10
