"""The pure scaler: capacity formula, monotonic jobs, idle arms, reconcile."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omega_prime.ci_runners.jobs import (
    AgentRunner,
    CancelIdle,
    ControllerStarted,
    IdleDeadline,
    JobEvent,
    Launch,
    LaunchFailed,
    LaunchSucceeded,
    ObservedRunner,
    Reap,
    RunnerExited,
    RunnerState,
    ScheduleIdle,
    parse_workflow_job,
)
from omega_prime.ci_runners.publisher import CloudEvent, JsonlPublisher
from omega_prime.ci_runners.scaler import JOB_MEMORY, Scaler, Step
from omega_prime.ci_runners.settings import DEFAULT_LABELS
from omega_prime.ci_runners.webhook import verify_delivery

from ._support import WEBHOOK_SECRET, Ids, encode, job_payload, signed_headers


def make(ids: Ids, *, started: bool = True, **kw) -> Scaler:  # type: ignore[no-untyped-def]
    kw.setdefault("labels", DEFAULT_LABELS)
    scaler = Scaler(new_generation=ids.generation, new_suffix=ids.suffix, **kw)
    if started:
        scaler.handle(ControllerStarted())
    return scaler


def job(action: str = "queued", **kw) -> JobEvent:  # type: ignore[no-untyped-def]
    parsed = parse_workflow_job(job_payload(action, **kw))
    assert parsed is not None
    return JobEvent(parsed)


def launches(step: Step) -> list[Launch]:
    return [d for d in step.decisions if isinstance(d, Launch)]


def kinds(step: Step) -> list[str]:
    return [e.kind for e in step.events]


def test_tracer_signed_webhook_to_redacted_event(ids: Ids, tmp_path: Path) -> None:
    body = encode(job_payload())
    hdrs = {k.lower(): v for k, v in signed_headers(body)}
    delivery = verify_delivery(hdrs, body, WEBHOOK_SECRET)
    parsed = parse_workflow_job(delivery.payload)
    assert parsed is not None
    step = make(ids).handle(JobEvent(parsed))
    [launch] = launches(step)
    assert launch.name.startswith("omega-ci-101-") and launch.job_id == 101
    publisher = JsonlPublisher(tmp_path / "events.jsonl")
    secret = WEBHOOK_SECRET.decode()
    for event in step.events:
        data = dict(event.data) | {"note": f"sig={secret}"}
        publisher.publish(CloudEvent.of(event.kind, data), known_secrets=[secret])
    text = (tmp_path / "events.jsonl").read_text()
    assert secret not in text
    types = [json.loads(line)["type"] for line in text.splitlines()]
    assert types == ["omega.ci.runner.demand", "omega.ci.runner.launch_requested"]


def test_constructor_bounds(ids: Ids) -> None:
    with pytest.raises(ValueError):
        make(ids, max_runners=0)
    with pytest.raises(ValueError):
        make(ids, max_runners=11)


def test_nothing_happens_before_start(ids: Ids) -> None:
    scaler = make(ids, started=False)
    assert launches(scaler.handle(job())) == []
    step = scaler.handle(ControllerStarted())
    assert len(launches(step)) == 1
    assert scaler.handle(ControllerStarted()) == Step()  # one-shot


def test_capacity_formula_never_exceeds_cap(ids: Ids) -> None:
    scaler = make(ids, max_runners=3)
    total = 0
    for n in range(1, 8):
        total += len(launches(scaler.handle(job(job_id=n))))
        assert len(scaler.runners) <= 3
    assert total == 3 and scaler.wanted() == 0
    assert scaler.count(RunnerState.STARTING) == 3


def test_duplicate_and_late_events_are_monotonic(ids: Ids) -> None:
    scaler = make(ids)
    [launch] = launches(scaler.handle(job()))
    assert scaler.handle(job()) == Step()
    scaler.handle(job("in_progress", runner_name=launch.name, runner_id=9))
    assert scaler.handle(job()) == Step()
    assert scaler.runners[launch.name].state is RunnerState.BUSY


def test_unmatched_jobs_ignored(ids: Ids) -> None:
    scaler = make(ids, repos=("acme/widgets",))
    assert scaler.handle(job(labels=("ubuntu-latest",))) == Step()
    assert scaler.handle(job(private=False)) == Step()
    assert scaler.handle(job(repo="acme/other")) == Step()
    other = job("in_progress", labels=("ubuntu-latest",), runner_name="vps-1")
    assert scaler.handle(other) == Step()


def test_full_lifecycle_idle_reap(ids: Ids) -> None:
    scaler = make(ids, idle_grace_s=30)
    [launch] = launches(scaler.handle(job()))
    step = scaler.handle(LaunchSucceeded(launch.name, launch.generation, 77))
    assert kinds(step) == ["launched"] and step.decisions == ()
    busy = scaler.handle(job("in_progress", runner_name=launch.name, runner_id=77))
    assert kinds(busy) == ["busy"]
    done = scaler.handle(job("completed", runner_name=launch.name, runner_id=77))
    assert "exited" in kinds(done) and launch.name not in scaler.runners
    # The process callback after a completed job is a harmless no-op.
    assert scaler.handle(RunnerExited(launch.name, launch.generation, 0)) == Step()


def test_idle_arm_fire_reaps_once(ids: Ids) -> None:
    scaler = make(ids, idle_grace_s=30)
    [launch] = launches(scaler.handle(job(job_id=1)))
    # Job is picked by a different pool runner; ours becomes surplus.
    scaler.handle(LaunchSucceeded(launch.name, launch.generation, 5))
    step = scaler.handle(job("in_progress", job_id=1, runner_name="vps-runner"))
    [arm] = [d for d in step.decisions if isinstance(d, ScheduleIdle)]
    assert arm.delay_s == 30 and scaler.armed(launch.name) == arm.generation
    assert scaler.handle(IdleDeadline(launch.name, "stale")) == Step()
    fired = scaler.handle(IdleDeadline(launch.name, arm.generation))
    assert fired.decisions == (Reap(launch.name, launch.generation, 5),)
    assert scaler.handle(IdleDeadline(launch.name, arm.generation)) == Step()


def test_deadline_with_queued_work_keeps_runner(ids: Ids) -> None:
    scaler = make(ids, max_runners=1)
    [launch] = launches(scaler.handle(job(job_id=1)))
    scaler.handle(LaunchSucceeded(launch.name, launch.generation))
    arm_step = scaler.handle(job("in_progress", job_id=1, runner_name="vps-runner"))
    [arm] = [d for d in arm_step.decisions if isinstance(d, ScheduleIdle)]
    scaler.handle(job(job_id=2))  # new demand arrives before the deadline
    step = scaler.handle(IdleDeadline(launch.name, arm.generation))
    assert step.decisions == () and launch.name in scaler.runners
    picked = scaler.handle(job("in_progress", job_id=2, runner_name=launch.name))
    assert kinds(picked) == ["busy"]


def test_busy_cancels_arm(ids: Ids) -> None:
    scaler = make(ids)
    [launch] = launches(scaler.handle(job(job_id=1)))
    scaler.handle(LaunchSucceeded(launch.name, launch.generation))
    step = scaler.handle(job("in_progress", job_id=1, runner_name="vps-runner"))
    [arm] = [d for d in step.decisions if isinstance(d, ScheduleIdle)]
    scaler.handle(job(job_id=2))
    busy = scaler.handle(job("in_progress", job_id=2, runner_name=launch.name))
    assert CancelIdle(launch.name, arm.generation) in busy.decisions


def test_rejected_launch_releases_and_rolls_back(ids: Ids) -> None:
    scaler = make(ids)
    [launch] = launches(scaler.handle(job()))
    step = scaler.handle(
        LaunchFailed(launch.name, launch.generation, "agent_400", runner_id=9)
    )
    assert launch.name not in scaler.runners and scaler.hold == "failure"
    assert step.decisions == (
        Reap(launch.name, launch.generation, 9, delete_github=True, stop_agent=False),
    )
    # Held: no relaunch storm for the same queue; a new signal clears it.
    assert launches(scaler.handle(job(job_id=2))) != []


def test_rejected_without_registration_has_no_reap(ids: Ids) -> None:
    scaler = make(ids)
    [launch] = launches(scaler.handle(job()))
    step = scaler.handle(LaunchFailed(launch.name, launch.generation, "jit_failed"))
    assert step.decisions == ()


def test_capacity_hold_waits_for_exit(ids: Ids) -> None:
    scaler = make(ids, max_runners=2)
    a, b = launches(scaler.handle(job(job_id=1))) + launches(
        scaler.handle(job(job_id=2))
    )
    scaler.handle(LaunchSucceeded(a.name, a.generation))
    scaler.handle(LaunchFailed(b.name, b.generation, "capacity", capacity=True))
    assert scaler.hold == "capacity"
    assert launches(scaler.handle(job(job_id=3))) == []  # capacity hold persists
    scaler.handle(job("in_progress", job_id=1, runner_name=a.name))
    step = scaler.handle(RunnerExited(a.name, a.generation, 0))
    assert scaler.hold is None and len(launches(step)) >= 1


def test_ambiguous_failure_keeps_runner(ids: Ids) -> None:
    scaler = make(ids)
    [launch] = launches(scaler.handle(job()))
    step = scaler.handle(
        LaunchFailed(launch.name, launch.generation, "wake_timeout", rejected=False)
    )
    assert scaler.runners[launch.name].state is RunnerState.IDLE
    assert kinds(step) == ["error"] and scaler.hold == "failure"


def test_stale_launch_results_ignored(ids: Ids) -> None:
    scaler = make(ids)
    [launch] = launches(scaler.handle(job()))
    assert scaler.handle(LaunchSucceeded(launch.name, "other")) == Step()
    assert scaler.handle(LaunchFailed("omega-ci-x", "g", "r")) == Step()
    scaler.handle(LaunchSucceeded(launch.name, launch.generation))
    assert scaler.handle(LaunchSucceeded(launch.name, launch.generation)) == Step()
    assert scaler.handle(LaunchFailed(launch.name, launch.generation, "r")) == Step()


def test_exit_requires_matching_generation(ids: Ids) -> None:
    scaler = make(ids)
    [launch] = launches(scaler.handle(job()))
    assert not scaler.registered(launch.name, "")
    assert scaler.handle(RunnerExited(launch.name, "forged", 0)) == Step()
    assert scaler.registered(launch.name, launch.generation)


def test_adopts_unknown_pool_runner_as_busy(ids: Ids) -> None:
    scaler = make(ids, max_runners=1)
    step = scaler.handle(job("in_progress", job_id=5, runner_name="omega-ci-5-zzzz"))
    assert step.events[0].data.get("adopted") is True
    assert scaler.count(RunnerState.BUSY) == 1
    assert launches(scaler.handle(job(job_id=6))) == []  # cap honoured


def test_reconcile_on_start(ids: Ids) -> None:
    scaler = make(ids, started=False)
    started = ControllerStarted(
        github=(
            ObservedRunner("omega-ci-1-aaaa", 1, busy=True, online=True),
            ObservedRunner("omega-ci-2-bbbb", 2, busy=False, online=True),
            ObservedRunner("omega-ci-3-cccc", 3, busy=False, online=False),
            ObservedRunner("omega-ci-4-dddd", 4, busy=True, online=True),
            ObservedRunner("vps-runner", 9, busy=False, online=True),
        ),
        agent=(
            AgentRunner("omega-ci-1-aaaa", "g1", "running", 11),
            AgentRunner("omega-ci-2-bbbb", "g2", "running", 12),
            AgentRunner("omega-ci-9-zzzz", "g9", "running", 19),
            AgentRunner("not-ours", "g", "running", 1),
        ),
    )
    step = scaler.handle(started)
    reaps = {d.name: d for d in step.decisions if isinstance(d, Reap)}
    assert reaps["omega-ci-9-zzzz"].delete_github is False  # orphan process
    assert reaps["omega-ci-3-cccc"].stop_agent is False  # orphan registration
    assert "vps-runner" not in reaps and "not-ours" not in reaps
    assert scaler.runners["omega-ci-1-aaaa"].state is RunnerState.BUSY
    assert scaler.runners["omega-ci-4-dddd"].state is RunnerState.BUSY
    assert scaler.runners["omega-ci-2-bbbb"].state is RunnerState.IDLE
    assert scaler.armed("omega-ci-2-bbbb") is not None


def test_queue_memory_is_bounded(ids: Ids, monkeypatch: pytest.MonkeyPatch) -> None:
    import omega_prime.ci_runners.scaler as mod

    monkeypatch.setattr(mod, "JOB_MEMORY", 3)
    scaler = make(ids, max_runners=1)
    for n in range(1, 5):
        scaler.handle(job(job_id=n))
    step = scaler.handle(job(job_id=5))
    assert "error" in kinds(step) and len(scaler.queued) == 3
    assert len(scaler._jobs) == 3
    assert JOB_MEMORY == 4096


def test_name_collision_exhaustion(ids: Ids) -> None:
    scaler = Scaler(labels=DEFAULT_LABELS, new_suffix=lambda: "same")
    scaler.handle(ControllerStarted())
    scaler.handle(job(job_id=1))
    with pytest.raises(RuntimeError, match="unique"):
        scaler._name_for(1)


def test_default_id_sources_are_unique() -> None:
    scaler = Scaler(labels=DEFAULT_LABELS, max_runners=2)
    scaler.handle(ControllerStarted())
    a = launches(scaler.handle(job(job_id=1)))[0]
    b = launches(scaler.handle(job(job_id=2)))[0]
    assert a.generation != b.generation and len(a.name.rsplit("-", 1)[1]) == 4
