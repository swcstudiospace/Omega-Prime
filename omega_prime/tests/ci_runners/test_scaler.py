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
    ReapFinished,
    ReapOutcome,
    RetryDeadline,
    RunnerExited,
    RunnerState,
    ScheduleIdle,
    ScheduleRetry,
    WorkflowJob,
    parse_workflow_job,
)
from omega_prime.ci_runners.publisher import CloudEvent, JsonlPublisher
from omega_prime.ci_runners.scaler import (
    JOB_MEMORY,
    MAX_CONSECUTIVE_FAILURES,
    RETRY_BASE_S,
    RETRY_CAP_S,
    RETRY_KEY,
    Scaler,
    Step,
)
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
    # The job is done but the agent slot is not free until the process exits.
    assert "finished" in kinds(done)
    assert scaler.runners[launch.name].state is RunnerState.FINISHING
    [arm] = [d for d in done.decisions if isinstance(d, ScheduleIdle)]
    exited = scaler.handle(RunnerExited(launch.name, launch.generation, 0))
    assert kinds(exited) == ["exited"] and launch.name not in scaler.runners
    assert CancelIdle(launch.name, arm.generation) in exited.decisions
    assert scaler.failures == 0 and scaler.hold is None


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
    assert [d for d in step.decisions if isinstance(d, Reap)] == [
        Reap(launch.name, launch.generation, 9, delete_github=True, stop_agent=False),
    ]
    [retry] = [d for d in step.decisions if isinstance(d, ScheduleRetry)]
    assert retry == ScheduleRetry(RETRY_KEY, retry.generation, RETRY_BASE_S)
    # Held: new demand does not relaunch; only the backoff deadline does.
    assert launches(scaler.handle(job(job_id=2))) == []
    assert scaler.handle(RetryDeadline(RETRY_KEY, "stale")) == Step()
    assert len(launches(scaler.handle(RetryDeadline(RETRY_KEY, retry.generation)))) == 2
    assert scaler.hold is None


def test_rejected_without_registration_has_no_reap(ids: Ids) -> None:
    scaler = make(ids)
    [launch] = launches(scaler.handle(job()))
    step = scaler.handle(LaunchFailed(launch.name, launch.generation, "jit_failed"))
    assert not [d for d in step.decisions if isinstance(d, Reap)]
    assert [type(d) for d in step.decisions] == [ScheduleRetry]


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
    assert kinds(step) == ["launch_backoff", "error"] and scaler.hold == "failure"


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


def test_unknown_input_event_is_rejected(ids: Ids) -> None:
    class Bogus:
        pass

    with pytest.raises(TypeError, match="unknown input event Bogus"):
        make(ids).handle(Bogus())  # type: ignore[arg-type]


# --- completion holds the slot until the exit (finding: stalled queue) -------


def _idle(scaler: Scaler, job_id: int, runner_id: int = 5) -> Launch:
    [launch] = launches(scaler.handle(job(job_id=job_id)))
    scaler.handle(LaunchSucceeded(launch.name, launch.generation, runner_id))
    return launch


def test_completed_runner_keeps_capacity_until_exit_then_hold_clears(
    ids: Ids,
) -> None:
    scaler = make(ids, max_runners=1)
    a = _idle(scaler, 1)
    scaler.handle(job("in_progress", job_id=1, runner_name=a.name))
    scaler.handle(job(job_id=2))
    done = scaler.handle(job("completed", job_id=1, runner_name=a.name))
    assert launches(done) == []  # the agent slot is still occupied
    assert scaler.wanted() == 0 and len(scaler.runners) == 1
    exited = scaler.handle(RunnerExited(a.name, a.generation, 0))
    assert len(launches(exited)) == 1 and scaler.hold is None


def test_capacity_hold_is_cleared_by_a_finishing_runner_exit(ids: Ids) -> None:
    """The reported stall: a 429 while the old runner cleans up."""
    scaler = make(ids, max_runners=2)
    a = _idle(scaler, 1)
    b = launches(scaler.handle(job(job_id=2)))[0]
    scaler.handle(job("in_progress", job_id=1, runner_name=a.name))
    scaler.handle(job("completed", job_id=1, runner_name=a.name))
    scaler.handle(LaunchFailed(b.name, b.generation, "agent_429", capacity=True))
    assert scaler.hold == "capacity" and scaler.failures == 0
    step = scaler.handle(RunnerExited(a.name, a.generation, 0))  # still tracked
    assert scaler.hold is None and len(launches(step)) == 1


def test_finish_deadline_stops_a_runner_whose_exit_was_lost(ids: Ids) -> None:
    scaler = make(ids, idle_grace_s=30)
    a = _idle(scaler, 1)
    scaler.handle(job("in_progress", job_id=1, runner_name=a.name))
    done = scaler.handle(job("completed", job_id=1, runner_name=a.name))
    [arm] = [d for d in done.decisions if isinstance(d, ScheduleIdle)]
    fired = scaler.handle(IdleDeadline(a.name, arm.generation))
    assert fired.decisions == (
        Reap(a.name, a.generation, None, delete_github=False, stop_agent=True),
    )
    assert scaler.runners[a.name].state is RunnerState.REAPING  # slot held
    step = scaler.handle(ReapFinished(a.name, a.generation, ReapOutcome.DONE))
    assert kinds(step) == ["exited"] and a.name not in scaler.runners


# --- reaps keep the runner tracked until their outcome ------------------------


def _reaping(scaler: Scaler) -> Launch:
    a = _idle(scaler, 1)
    step = scaler.handle(job("in_progress", job_id=1, runner_name="vps-runner"))
    [arm] = [d for d in step.decisions if isinstance(d, ScheduleIdle)]
    fired = scaler.handle(IdleDeadline(a.name, arm.generation))
    assert fired.decisions == (Reap(a.name, a.generation, 5),)
    assert scaler.runners[a.name].state is RunnerState.REAPING
    return a


def test_failed_github_reap_returns_runner_to_idle_and_rearms(ids: Ids) -> None:
    scaler = make(ids)
    a = _reaping(scaler)
    step = scaler.handle(ReapFinished(a.name, a.generation, ReapOutcome.GITHUB_FAILED))
    assert scaler.runners[a.name].state is RunnerState.IDLE
    assert any(isinstance(d, ScheduleIdle) for d in step.decisions)
    # Its exit callback is still accepted: the live child stays tracked.
    assert scaler.registered(a.name, a.generation)


def test_busy_reap_keeps_runner_busy(ids: Ids) -> None:
    scaler = make(ids)
    a = _reaping(scaler)
    scaler.handle(ReapFinished(a.name, a.generation, ReapOutcome.BUSY))
    assert scaler.runners[a.name].state is RunnerState.BUSY


def test_agent_stop_failure_holds_slot_and_retries_stop(ids: Ids) -> None:
    scaler = make(ids)
    a = _reaping(scaler)
    step = scaler.handle(ReapFinished(a.name, a.generation, ReapOutcome.AGENT_FAILED))
    assert scaler.runners[a.name].state is RunnerState.FINISHING
    [arm] = [d for d in step.decisions if isinstance(d, ScheduleIdle)]
    again = scaler.handle(IdleDeadline(a.name, arm.generation))
    assert again.decisions == (Reap(a.name, a.generation, None, delete_github=False),)


def test_reaping_runner_that_takes_a_job_becomes_busy(ids: Ids) -> None:
    scaler = make(ids)
    a = _reaping(scaler)
    scaler.handle(job(job_id=2))
    scaler.handle(job("in_progress", job_id=2, runner_name=a.name))
    assert scaler.runners[a.name].state is RunnerState.BUSY
    assert scaler.handle(ReapFinished(a.name, a.generation, ReapOutcome.DONE)) == Step()


def test_reap_outcomes_for_untracked_or_stale_runners_are_ignored(ids: Ids) -> None:
    scaler = make(ids)
    a = _reaping(scaler)
    assert (
        scaler.handle(ReapFinished("omega-ci-9-zzzz", "g", ReapOutcome.DONE)) == Step()
    )
    assert scaler.handle(ReapFinished(a.name, "other", ReapOutcome.DONE)) == Step()
    assert scaler.runners[a.name].state is RunnerState.REAPING


def test_reaping_runner_occupies_a_slot(ids: Ids) -> None:
    scaler = make(ids, max_runners=1)
    _reaping(scaler)
    assert launches(scaler.handle(job(job_id=2))) == []


# --- spawn failures back off, then open the circuit ---------------------------


def _spawn_failure(scaler: Scaler, job_id: int, code: int = -1) -> Step:
    launch = _idle(scaler, job_id)
    return scaler.handle(RunnerExited(launch.name, launch.generation, code))


def _retry_of(step: Step) -> ScheduleRetry:
    [retry] = [d for d in step.decisions if isinstance(d, ScheduleRetry)]
    return retry


def test_spawn_failures_back_off_exponentially_then_open_circuit(ids: Ids) -> None:
    scaler = make(ids)
    step = _spawn_failure(scaler, 1)
    assert launches(step) == [] and scaler.hold == "failure"
    delays = []
    for n in range(2, MAX_CONSECUTIVE_FAILURES + 1):
        retry = _retry_of(step)
        delays.append(retry.delay_s)
        relaunch = scaler.handle(RetryDeadline(RETRY_KEY, retry.generation))
        [launch] = launches(relaunch)
        scaler.handle(LaunchSucceeded(launch.name, launch.generation))
        step = scaler.handle(RunnerExited(launch.name, launch.generation, -1))
        if n < MAX_CONSECUTIVE_FAILURES:
            assert "launch_backoff" in kinds(step)
    assert delays == [15.0, 30.0, 60.0, 120.0]
    assert scaler.hold == "circuit" and "launch_circuit_open" in kinds(step)
    assert not [d for d in step.decisions if isinstance(d, ScheduleRetry)]
    assert launches(scaler.handle(job(job_id=99))) == []  # demand cannot reopen


def test_backoff_is_capped() -> None:
    assert Scaler.backoff(1) == RETRY_BASE_S
    assert Scaler.backoff(50) == RETRY_CAP_S


def test_a_runner_taking_a_job_closes_the_circuit(ids: Ids) -> None:
    scaler = make(ids)
    scaler.hold, scaler.failures = "circuit", MAX_CONSECUTIVE_FAILURES
    scaler.handle(job(job_id=7))
    step = scaler.handle(job("in_progress", job_id=7, runner_name="omega-ci-7-zzzz"))
    assert "launch_circuit_closed" in kinds(step)
    assert scaler.hold is None and scaler.failures == 0


def test_exit_zero_before_a_job_backs_off(ids: Ids) -> None:
    """Regression (00055): actions/runner exits 0 on fatal startup errors.

    An unreadable work directory made every JIT runner exit 0 within a second;
    exit 0 was treated as clean, so the controller relaunched 285 times.
    """
    scaler = make(ids)
    step = _spawn_failure(scaler, 1, code=0)
    assert scaler.failures == 1 and scaler.hold == "failure"
    assert launches(step) == [] and _retry_of(step).delay_s == RETRY_BASE_S
    errors = [e.data for e in step.events if e.kind == "error"]
    assert errors == [
        {"name": errors[0]["name"], "reason": "exited_before_job", "exit_code": 0}
    ]
    assert launches(scaler.handle(job(job_id=2))) == []  # new demand: still held


def test_exit_zero_before_a_job_opens_the_circuit_without_looping(
    ids: Ids,
) -> None:
    scaler = make(ids)
    step = _spawn_failure(scaler, 1, code=0)
    total = 1
    for _ in range(50):
        if scaler.hold == "circuit":
            break
        relaunch = scaler.handle(RetryDeadline(RETRY_KEY, _retry_of(step).generation))
        [launch] = launches(relaunch)
        total += 1
        scaler.handle(LaunchSucceeded(launch.name, launch.generation))
        step = scaler.handle(RunnerExited(launch.name, launch.generation, 0))
    assert total == MAX_CONSECUTIVE_FAILURES and scaler.hold == "circuit"
    [opened] = [e.data for e in step.events if e.kind == "launch_circuit_open"]
    assert opened["failures"] == MAX_CONSECUTIVE_FAILURES and opened["queued"] == 1
    assert not [d for d in step.decisions if isinstance(d, ScheduleRetry)]
    assert launches(scaler.handle(job(job_id=99))) == []


def test_busy_exit_zero_is_not_a_failure(ids: Ids) -> None:
    scaler = make(ids)
    launch = _idle(scaler, 1)
    scaler.handle(job("in_progress", job_id=1, runner_name=launch.name))
    step = scaler.handle(RunnerExited(launch.name, launch.generation, 0))
    assert scaler.failures == 0 and scaler.hold is None
    assert "error" not in kinds(step)


def test_late_completed_webhook_clears_a_false_pre_job_failure(ids: Ids) -> None:
    """A runner ran its job but the in_progress webhook was lost: its exit
    looked pre-job; the completed webhook naming it resets the failure."""
    scaler = make(ids)
    step = _spawn_failure(scaler, 1, code=0)
    name = next(e.data["name"] for e in step.events if e.kind == "exited")
    assert scaler.hold == "failure"
    scaler.handle(job("completed", job_id=1, runner_name=name))
    assert scaler.failures == 0 and scaler.hold is None


def test_capacity_rejections_retry_without_counting_failures(ids: Ids) -> None:
    scaler = make(ids)
    [launch] = launches(scaler.handle(job(job_id=1)))
    step = scaler.handle(
        LaunchFailed(launch.name, launch.generation, "agent_429", capacity=True)
    )
    assert scaler.hold == "capacity" and scaler.failures == 0
    assert _retry_of(step).delay_s == RETRY_CAP_S
    relaunch = scaler.handle(RetryDeadline(RETRY_KEY, _retry_of(step).generation))
    assert len(launches(relaunch)) == 1 and scaler.hold is None


def test_retry_deadline_after_hold_cleared_is_a_noop(ids: Ids) -> None:
    scaler = make(ids)
    step = _spawn_failure(scaler, 1)
    retry = _retry_of(step)
    scaler.handle(job("in_progress", job_id=1, runner_name="omega-ci-1-zzzz"))
    assert scaler.handle(RetryDeadline(RETRY_KEY, retry.generation)) == Step()
    assert scaler.handle(RetryDeadline("other", retry.generation)) == Step()


def test_circuit_ignores_further_failures(ids: Ids) -> None:
    from omega_prime.ci_runners.scaler import _Out

    scaler = make(ids)
    scaler.hold, scaler.failures = "circuit", MAX_CONSECUTIVE_FAILURES
    out = _Out()
    scaler._failed(out)
    assert scaler.failures == MAX_CONSECUTIVE_FAILURES and out.step() == Step()


# --- restart re-enqueues persisted demand -------------------------------------


def test_restart_recovers_queued_demand(ids: Ids) -> None:
    pending = tuple(job(job_id=n).job for n in (11, 12, 13))
    scaler = make(ids, started=False, max_runners=2)
    step = scaler.handle(
        ControllerStarted(
            github=(ObservedRunner("omega-ci-1-aaaa", 1, busy=True, online=True),),
            agent=(AgentRunner("omega-ci-1-aaaa", "g1", "running", 11),),
            queued=pending,
        )
    )
    assert list(scaler.queued) == [11, 12, 13]
    assert len(launches(step)) == 1  # one survivor busy + one new = cap of 2
    scaler.handle(job("completed", job_id=1, runner_name="omega-ci-1-aaaa"))
    after = scaler.handle(RunnerExited("omega-ci-1-aaaa", "g1", 0))
    assert len(launches(after)) == 1  # the backlog keeps draining


def test_restart_ignores_non_queued_persisted_jobs(ids: Ids) -> None:
    stale: WorkflowJob = job("in_progress", job_id=20).job
    scaler = make(ids, started=False)
    scaler.handle(ControllerStarted(queued=(stale,)))
    assert not scaler.queued
