import json
from pathlib import Path

import pytest

from omega_prime.sdlc_events.idempotency import IdempotencyStore
from omega_prime.sdlc_events.sandbox import (
    ReadinessInput,
    SandboxEvent,
    SandboxLifecycle,
    evaluate_sandbox_ready,
    event_from_mapping,
    on_sandbox_event,
    readiness_event,
    sandbox_row_id,
)
from omega_prime.sdlc_events.sources.podman_events import (
    PodmanEventParser,
    iter_sandbox_events,
    podman_events_argv,
)
from omega_prime.sdlc_events.task_store import MemoryTaskStore
from omega_prime.sdlc_events.types import DeliveryId

FIXTURE = Path(__file__).parent / "fixtures" / "podman_events_teardown.jsonl"
UUID = "e90c3b41-3cbf-432f-9e6a-93fbd8c0dc4b"


def _ev(lifecycle: SandboxLifecycle, **kw: object) -> SandboxEvent:
    return SandboxEvent(
        delivery=DeliveryId(str(kw.pop("delivery", f"t:{lifecycle.value}"))),
        sandbox=str(kw.pop("sandbox", "ompsbx-demo-abc123")),
        lifecycle=lifecycle,
        source="test",
        **kw,  # type: ignore[arg-type]
    )


def _line(name: str, status: str, nano: int, exit_code: int | None = None) -> str:
    raw: dict[str, object] = {
        "ID": f"{abs(hash((name, status))):012x}deadbeef",
        "Image": "localhost/omp-sandbox:0.1",
        "Name": name,
        "Status": status,
        "time": nano // 10**9,
        "timeNano": nano,
        "Type": "container",
        "Attributes": {},
    }
    if exit_code is not None:
        raw["ContainerExitCode"] = exit_code
    return json.dumps(raw)


# --- L14 readiness gate -----------------------------------------------------


def test_readiness_fails_closed_without_tools_and_skills_probe() -> None:
    gate = evaluate_sandbox_ready(ReadinessInput(container_ready=True))
    assert not gate.ok
    assert gate.reasons == ("tools_probe_missing(#61)", "skills_probe_missing(#61)")


def test_readiness_open_only_when_everything_connected() -> None:
    gate = evaluate_sandbox_ready(
        ReadinessInput(
            container_ready=True,
            broker_required=True,
            broker_relay_ready=True,
            tools_connected=True,
            skills_connected=True,
        )
    )
    assert gate.ok and gate.reasons == ()


def test_readiness_reports_each_missing_dependency() -> None:
    gate = evaluate_sandbox_ready(
        ReadinessInput(
            container_ready=False,
            broker_required=True,
            broker_relay_ready=False,
            tools_connected=False,
            skills_connected=True,
        )
    )
    assert gate.reasons == (
        "container_not_ready",
        "broker_relay_not_ready",
        "tools_not_connected",
    )


def test_readiness_event_not_ready_blocks_owner() -> None:
    event = readiness_event(
        "ompsbx-demo-abc123",
        ReadinessInput(container_ready=True),
        source="sandbox_control",
        task_id="gb:00045",
    )
    assert event.lifecycle is SandboxLifecycle.NOT_READY and event.blocking
    store = MemoryTaskStore()
    result = on_sandbox_event(event, store=store, idem=IdempotencyStore())
    assert result.action == "blocked"
    assert store.rows["gb:00045"]["state"] == "blocked"
    assert "tools_probe_missing(#61)" in store.rows["gb:00045"]["note"]
    assert store.rows[sandbox_row_id("ompsbx-demo-abc123")]["state"] == "blocked"


# --- L15 + handler ---------------------------------------------------------


@pytest.mark.parametrize(
    ("exit_code", "blocking"), [(0, False), (1, True), (137, True), (None, True)]
)
def test_exit_code_rule(exit_code: int | None, blocking: bool) -> None:
    assert _ev(SandboxLifecycle.EXITED, exit_code=exit_code).blocking is blocking


def test_nonzero_exit_blocks_owner_task() -> None:
    store = MemoryTaskStore()
    on_sandbox_event(
        _ev(SandboxLifecycle.EXITED, exit_code=2, task_id="gb:00045"),
        store=store,
        idem=IdempotencyStore(),
    )
    assert store.rows["gb:00045"]["state"] == "blocked"
    assert "exit=2" in store.rows["gb:00045"]["note"]


def test_removed_marks_only_sandbox_row_done() -> None:
    store = MemoryTaskStore()
    on_sandbox_event(
        _ev(SandboxLifecycle.REMOVED, task_id="gb:00045"),
        store=store,
        idem=IdempotencyStore(),
    )
    assert store.rows[sandbox_row_id("ompsbx-demo-abc123")]["state"] == "done"
    assert "gb:00045" not in store.rows


def test_duplicate_delivery_is_skipped() -> None:
    store = MemoryTaskStore()
    idem = IdempotencyStore()
    event = _ev(SandboxLifecycle.OOM, task_id="gb:00045")
    assert on_sandbox_event(event, store=store, idem=idem).action == "blocked"
    store.rows.clear()
    again = on_sandbox_event(event, store=store, idem=idem)
    assert again.skipped_duplicate and store.rows == {}


def test_failed_store_write_releases_claim_for_retry() -> None:
    class Boom(MemoryTaskStore):
        def upsert_note(self, task_id: str, state: str, note: str) -> None:
            raise OSError("disk")

    idem = IdempotencyStore()
    event = _ev(SandboxLifecycle.STARTED)
    with pytest.raises(OSError):
        on_sandbox_event(event, store=Boom(), idem=idem)
    store = MemoryTaskStore()
    assert on_sandbox_event(event, store=store, idem=idem).action == "noted"


def test_invalid_sandbox_name_rejected() -> None:
    with pytest.raises(ValueError):
        _ev(SandboxLifecycle.CREATED, sandbox="../etc")


def test_cloudevent_roundtrip() -> None:
    event = _ev(
        SandboxLifecycle.EXITED,
        exit_code=3,
        task_id="gb:00045",
        reasons=("x",),
        at=1792505274.5,
    )
    ce = event.to_cloudevent()
    assert ce["specversion"] == "1.0"
    assert ce["type"] == "op.sbx.exited"
    assert ce["subject"] == "op.sbx.ompsbx-demo-abc123.exited"
    assert ce["source"] == "omega-prime/sandbox/test"
    back = event_from_mapping(json.loads(json.dumps(ce)))
    assert back == event


# --- L13 podman events source ----------------------------------------------


def test_real_teardown_stream_emits_lifecycle_without_false_death() -> None:
    parser = PodmanEventParser(task_for=lambda name: "gb:00045")
    events = list(iter_sandbox_events(FIXTURE.read_text().splitlines(), parser))
    assert [e.lifecycle for e in events] == [
        SandboxLifecycle.CREATED,
        SandboxLifecycle.STARTED,
        SandboxLifecycle.REMOVED,
    ]
    assert {e.sandbox for e in events} == {"smoke1"}
    assert all(e.task_id == "gb:00045" for e in events)
    assert len({e.delivery for e in events}) == 3


def test_death_without_removal_is_emitted_after_grace() -> None:
    name = f"openshell-default--ompsbx-demo-abc123-{UUID}"
    sup = f"openshell-supervisor-{UUID}"
    base = 1_800_000_000 * 10**9
    lines = [
        _line(name, "create", base),
        _line(name, "start", base + 1),
        _line(sup, "died", base + 2 * 10**9, 1),
        _line(name, "died", base + 3 * 10**9, 2),
        _line(name, "cleanup", base + 30 * 10**9),
        _line(name, "start", base + 31 * 10**9),
    ]
    events = list(iter_sandbox_events(lines, PodmanEventParser()))
    kinds = [(e.lifecycle, e.exit_code) for e in events]
    assert kinds == [
        (SandboxLifecycle.CREATED, None),
        (SandboxLifecycle.STARTED, None),
        (SandboxLifecycle.UNHEALTHY, 1),
        (SandboxLifecycle.EXITED, 2),
        (SandboxLifecycle.STARTED, None),
    ]
    assert events[2].reasons == ("supervisor_died",)


def test_owned_teardown_drops_deaths_immediately() -> None:
    name = f"openshell-default--ompsbx-demo-abc123-{UUID}"
    parser = PodmanEventParser(teardown=lambda n: n == "ompsbx-demo-abc123")
    events = list(iter_sandbox_events([_line(name, "died", 5 * 10**9, 137)], parser))
    assert events == []


def test_unknown_supervisor_and_noise_ignored() -> None:
    parser = PodmanEventParser()
    assert parser.parse_line("") is None
    assert parser.parse_line("not json") is None
    assert (
        parser.parse_line(_line(f"openshell-supervisor-{UUID}", "died", 1, 1)) is None
    )
    assert parser.parse_line(_line("some-other-container", "died", 1, 1)) is None
    name = f"openshell-default--ompsbx-demo-abc123-{UUID}"
    assert parser.parse_line(_line(name, "attach", 1)) is None


def test_health_status_unhealthy_maps() -> None:
    name = f"openshell-default--ompsbx-demo-abc123-{UUID}"
    raw = json.loads(_line(name, "health_status", 10**9))
    raw["HealthStatus"] = "unhealthy"
    event = PodmanEventParser().parse(raw)
    assert event is not None and event.lifecycle is SandboxLifecycle.UNHEALTHY
    raw["HealthStatus"] = "healthy"
    assert PodmanEventParser().parse(raw) is None


def test_podman_events_argv() -> None:
    assert podman_events_argv("/usr/bin/podman", since="5m") == [
        "/usr/bin/podman",
        "events",
        "--format",
        "json",
        "--filter",
        "type=container",
        "--since",
        "5m",
    ]


def test_removal_in_grace_keeps_real_failures_and_drops_only_teardown() -> None:
    name = f"openshell-default--ompsbx-demo-abc123-{UUID}"
    base = 1_800_000_000 * 10**9
    crash = [_line(name, "died", base, 1), _line(name, "remove", base + 10**9, 1)]
    kinds = [
        (e.lifecycle, e.exit_code)
        for e in iter_sandbox_events(crash, PodmanEventParser())
    ]
    assert kinds == [(SandboxLifecycle.EXITED, 1), (SandboxLifecycle.REMOVED, None)]
    kill = [_line(name, "died", base, 137), _line(name, "remove", base + 10**9, 137)]
    kept = [e.lifecycle for e in iter_sandbox_events(kill, PodmanEventParser())]
    assert kept == [SandboxLifecycle.REMOVED]


def test_ignored_events_advance_the_grace_window() -> None:
    name = f"openshell-default--ompsbx-demo-abc123-{UUID}"
    base = 1_800_000_000 * 10**9
    lines = [_line(name, "died", base, 1), _line(name, "attach", base + 11 * 10**9)]
    stream = iter_sandbox_events(lines, PodmanEventParser())
    first = next(stream)  # released by the ignored line, not by stream end
    assert first.lifecycle is SandboxLifecycle.EXITED and first.exit_code == 1


def test_live_stream_flushes_held_death_on_quiet_heartbeat() -> None:
    name = f"openshell-default--ompsbx-demo-abc123-{UUID}"
    now = [1_800_000_000.0]

    def lines():  # a live pipe: one death, then quiet heartbeats forever
        yield _line(name, "died", int(now[0] * 10**9), 2)
        while True:
            now[0] += 5
            yield ""

    stream = iter_sandbox_events(lines(), PodmanEventParser(), clock=lambda: now[0])
    event = next(stream)
    assert event.lifecycle is SandboxLifecycle.EXITED and event.exit_code == 2
    assert now[0] - 1_800_000_000.0 >= 10  # held for the grace, not forever


def test_with_heartbeats_yields_blank_lines_when_quiet() -> None:
    import os

    from omega_prime.sdlc_events.sources.podman_events import with_heartbeats

    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd) as reader, os.fdopen(write_fd, "w") as writer:
        beats = with_heartbeats(reader, interval_s=0.01)
        assert next(beats) == ""
        writer.write('{"a": 1}\n')
        writer.flush()
        assert next(beats) == '{"a": 1}\n'
        writer.close()
        assert list(beats) == []
