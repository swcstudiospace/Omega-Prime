# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Small/big router tests. The fleet side is a fake coordinator."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from urllib.parse import urlsplit

from omega_prime.engine.fleet_client import (
    FleetClient,
    FleetError,
    HttpResponse,
    JobSpec,
)
from omega_prime.engine.jobs import job_dir
from omega_prime.engine.lanes import (
    BoxSnapshot,
    LaneLimits,
    LaneRouter,
    choose_lane,
)
from omega_prime.policy.policy import SeatPolicy
from omega_prime.tooling.fs import atomic_write_json
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.dispatch import register_dispatch_tools
from omega_prime.tools.registry import ToolRegistry

GIB = 1024 * 1024 * 1024
LIMITS = LaneLimits()
ROOMY = BoxSnapshot(free_bytes=4 * GIB)
TOKEN = "fleet-test-token"
CONFIRM = "confirm-test-token"


def _allow(*hosts: str) -> SeatPolicy:
    return SeatPolicy({"version": 1, "network": {"hosts": list(hosts)}})


def _spec(
    *,
    prompt: str = "fix the python test",
    model: str = "zai/glm-4.5",
    max_minutes: int = 15,
    max_files: int = 3,
    slash_command: str | None = None,
    slash_args: str | None = None,
) -> JobSpec:
    return JobSpec(
        repo="acme/widget",
        prompt=prompt,
        model=model,
        max_minutes=max_minutes,
        max_files=max_files,
        slash_command=slash_command,
        slash_args=slash_args,
    )


class _Transport:
    def __init__(self) -> None:
        self.bodies: list[dict[str, object]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> HttpResponse:
        del method, url, headers
        posted = json.loads(body.decode()) if body else {}
        self.bodies.append(posted)
        return HttpResponse(
            201,
            json.dumps(
                {
                    "jobs": [
                        {
                            "id": "fleet-1",
                            "status": "queued",
                            "branch": posted.get("branch"),
                        }
                    ]
                }
            ).encode(),
        )


class _Start:
    def __init__(self, fail: bool = False) -> None:
        self.jobs: list[str] = []
        self.fail = fail

    def start(self, job_id: str, spec: JobSpec) -> None:
        del spec
        self.jobs.append(job_id)
        if self.fail:
            raise RuntimeError("sandbox down")


def _router(
    tmp_path, transport: _Transport | None = None, executor: _Start | None = None
):
    env = {
        "OMEGA_PRIME_STATE_DIR": str(tmp_path),
        "FLEET_COORDINATOR_URL": "https://fleet.example",
        "FLEET_COORDINATOR_TOKEN": TOKEN,
    }
    client = FleetClient(
        env,
        transport or _Transport(),
        policy=_allow("fleet.example"),
    )
    router = LaneRouter(
        env,
        client=client,
        executor=executor or _Start(),
        memory=lambda: 4 * GIB,
    )
    return router, env


def _mark_running(
    env: Mapping[str, str], name: str, model: str = "zai/glm-4.5"
) -> None:
    atomic_write_json(
        job_dir(env, name) / "lane.json",
        {"id": name, "lane": "local", "status": "running", "model": model},
    )


def test_small_python_job_stays_local() -> None:
    decision = choose_lane(_spec(), ROOMY, limits=LIMITS)
    assert decision.lane == "local"
    assert decision.status == "running"
    assert decision.reasons == ()


def test_cloud_markers_force_the_fleet() -> None:
    cases = [
        ("port the rust crate", ()),
        ("fix the test", ("Dockerfile",)),
        ("update docs", ("service/go.mod",)),
        ("notes", ("android/app/src",)),
        ("ship the ios app", ()),
        ("Fix the Go service", ()),
        ("the parser is written in go", ()),
        ("using go for the small library", ()),
    ]
    for prompt, paths in cases:
        decision = choose_lane(
            _spec(prompt=prompt),
            BoxSnapshot(free_bytes=4 * GIB, paths=paths),
            limits=LIMITS,
        )
        assert decision.lane == "fleet", prompt
        assert "cloud_only" in decision.reasons


def test_ordinary_words_are_not_cloud_markers() -> None:
    for prompt in (
        "trust the docs",
        "a good module",
        "previous notes",
        "go ahead and fix the Python test",
        "ready to go and fix the Python test",
    ):
        decision = choose_lane(
            _spec(prompt=prompt),
            BoxSnapshot(free_bytes=4 * GIB, languages=("python",)),
            limits=LIMITS,
        )
        assert decision.lane == "local", prompt


def test_file_and_minute_caps() -> None:
    assert choose_lane(_spec(max_files=25, max_minutes=30), ROOMY).lane == "local"
    files = choose_lane(_spec(max_files=26), ROOMY)
    assert files.lane == "fleet" and "max_files" in files.reasons
    minutes = choose_lane(_spec(max_minutes=31), ROOMY)
    assert minutes.lane == "fleet" and "max_minutes" in minutes.reasons


def test_other_languages_go_to_the_fleet() -> None:
    decision = choose_lane(
        _spec(),
        BoxSnapshot(free_bytes=4 * GIB, languages=("ruby",)),
    )
    assert decision.lane == "fleet"
    assert "language" in decision.reasons
    local = choose_lane(
        _spec(),
        BoxSnapshot(free_bytes=4 * GIB, languages=("python", "md")),
    )
    assert local.lane == "local"


def test_full_box_spills_or_queues() -> None:
    full = BoxSnapshot(running_local=2, free_bytes=4 * GIB)
    spill = choose_lane(_spec(), full, overflow="spill")
    assert spill.lane == "fleet"
    assert spill.reasons[0] == "capacity"
    assert "max_parallel" in spill.reasons
    queued = choose_lane(_spec(), full, overflow="queue")
    assert queued.lane == "local"
    assert queued.status == "queued"


def test_memory_and_xai_oauth_gates() -> None:
    low = BoxSnapshot(free_bytes=3 * GIB - 1)
    assert "memory" in choose_lane(_spec(), low, overflow="spill").reasons
    exact = BoxSnapshot(free_bytes=3 * GIB)
    assert choose_lane(_spec(), exact).lane == "local"
    unknown = BoxSnapshot(free_bytes=None)
    assert "memory" in choose_lane(_spec(), unknown).reasons
    busy = BoxSnapshot(
        running_local=1,
        running_xai_oauth=1,
        free_bytes=4 * GIB,
    )
    spilled = choose_lane(_spec(model="xai-oauth/grok-build"), busy)
    assert spilled.lane == "fleet"
    assert "xai_oauth" in spilled.reasons
    other = choose_lane(_spec(model="zai/glm-4.5"), busy)
    assert other.lane == "local"


def test_dispatch_local_uses_the_stub_and_keeps_the_spec(tmp_path) -> None:
    transport = _Transport()
    executor = _Start()
    router, env = _router(tmp_path, transport, executor)
    spec = _spec(slash_command="gsd-autonomous", slash_args="ultrathink orchestrate")
    record = router.dispatch(spec, languages=("python",))
    assert record["lane"] == "local"
    assert record["status"] == "running"
    assert executor.jobs == [record["id"]]
    assert transport.bodies == []
    stored = json.loads((job_dir(env, record["id"]) / "lane.json").read_text())
    assert stored["spec"] == spec.to_json()
    assert TOKEN not in json.dumps(stored)


def test_dispatch_fleet_posts_the_same_spec(tmp_path) -> None:
    transport = _Transport()
    executor = _Start()
    router, _env = _router(tmp_path, transport, executor)
    spec = _spec(prompt="port the rust parser", max_files=4)
    record = router.dispatch(spec)
    assert record["lane"] == "fleet"
    assert record["fleet_job_id"] == "fleet-1"
    assert executor.jobs == []
    assert transport.bodies == [spec.to_json()]


def test_queue_does_not_start_the_executor(tmp_path) -> None:
    executor = _Start()
    router, env = _router(tmp_path, _Transport(), executor)
    for name in ("run-one", "run-two"):
        _mark_running(env, name)
    record = router.dispatch(_spec(), overflow="queue")
    assert record["status"] == "queued"
    assert executor.jobs == []


def test_a_failed_spec_write_does_not_hold_a_local_slot(tmp_path, monkeypatch) -> None:
    real = atomic_write_json

    def fail_spec(path, obj, **kwargs):
        if str(path).endswith("spec.json"):
            raise OSError("disk full")
        return real(path, obj, **kwargs)

    monkeypatch.setattr("omega_prime.engine.lanes.atomic_write_json", fail_spec)
    router, _env = _router(tmp_path)
    try:
        router.dispatch(_spec(), languages=("python",))
    except OSError:
        pass
    else:
        raise AssertionError("expected the spec write to fail")
    jobs = tmp_path / "jobs"
    lanes = list(jobs.glob("*/lane.json")) if jobs.is_dir() else []
    assert lanes == []
    monkeypatch.setattr("omega_prime.engine.lanes.atomic_write_json", real)
    again = router.dispatch(_spec(), languages=("python",))
    assert again["lane"] == "local"
    assert again["status"] == "running"


def test_missing_bounds_are_filled_with_the_local_caps(tmp_path) -> None:
    router, env = _router(tmp_path)
    spec = JobSpec(
        repo="acme/widget", prompt="fix the python test", model="zai/glm-4.5"
    )
    record = router.dispatch(spec, languages=("python",))
    assert record["lane"] == "local"
    stored = json.loads((job_dir(env, record["id"]) / "spec.json").read_text())
    assert stored["max_minutes"] == 30
    assert stored["max_files"] == 25


def test_local_slot_is_reserved_before_another_dispatch(tmp_path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def memory() -> int:
        entered.set()
        assert release.wait(timeout=2)
        return 4 * GIB

    router, _env = _router(tmp_path)
    router._memory = memory
    router._limits = LaneLimits(max_parallel=1)
    first: list[dict[str, object]] = []
    second: list[dict[str, object]] = []

    def run_first() -> None:
        first.append(router.dispatch(_spec(), languages=("python",)))

    def run_second() -> None:
        second.append(router.dispatch(_spec(), languages=("python",)))

    left = threading.Thread(target=run_first)
    left.start()
    assert entered.wait(timeout=2)
    right = threading.Thread(target=run_second)
    right.start()
    right.join(timeout=0.3)
    assert right.is_alive()
    release.set()
    left.join(timeout=2)
    right.join(timeout=2)
    assert first[0]["status"] == "running"
    assert second[0]["lane"] == "fleet"
    reasons = second[0]["reasons"]
    assert isinstance(reasons, list)
    assert "max_parallel" in reasons


def test_escalation_keeps_the_original_task_text(tmp_path) -> None:
    transport = _Transport()
    router, env = _router(tmp_path, transport, _Start())
    prompt = "change token=old to token=new"
    started = router.dispatch(_spec(prompt=prompt), languages=("python",))
    spec_text = (job_dir(env, started["id"]) / "spec.json").read_text()
    assert prompt in spec_text
    lane_text = (job_dir(env, started["id"]) / "lane.json").read_text()
    assert "token=old" not in lane_text
    router.escalate(started["id"], reason="time_cap", partial_branch="fleet/partial-1")
    assert transport.bodies[-1]["prompt"] == prompt
    receipt = (job_dir(env, started["id"]) / "receipt.json").read_text()
    assert "token=old" not in receipt


def test_escalation_posts_the_partial_branch_and_records_the_lane_change(
    tmp_path,
) -> None:
    transport = _Transport()
    router, env = _router(tmp_path, transport, _Start())
    spec = _spec()
    started = router.dispatch(spec, languages=("python",))
    receipt = router.escalate(
        started["id"],
        reason="time_cap",
        partial_branch="fleet/partial-1",
    )
    assert receipt["lane_change"]["from"] == "local"
    assert receipt["lane_change"]["to"] == "fleet"
    assert receipt["lane_change"]["reason"] == "time_cap"
    assert receipt["lane_change"]["partial_branch"] == "fleet/partial-1"
    assert receipt["lane_change"]["fleet_job_id"] == "fleet-1"
    posted = transport.bodies[-1]
    assert posted["branch"] == "fleet/partial-1"
    assert posted["repo"] == spec.repo
    assert posted["prompt"] == spec.prompt
    text = (job_dir(env, started["id"]) / "receipt.json").read_text()
    assert "time_cap" in text
    assert TOKEN not in text
    lane = json.loads((job_dir(env, started["id"]) / "lane.json").read_text())
    assert lane["status"] == "escalated"
    assert lane["changes"][0]["partial_branch"] == "fleet/partial-1"


def test_escalation_rejects_main_and_a_second_change(tmp_path) -> None:
    router, _env = _router(tmp_path)
    started = router.dispatch(_spec(), languages=("py",))
    try:
        router.escalate(started["id"], reason="vm_limit", partial_branch="main")
    except FleetError as exc:
        assert exc.code == "invalid_branch"
    else:
        raise AssertionError("expected invalid_branch")
    router.escalate(
        started["id"], reason="unsupported_toolchain", partial_branch="fleet/partial-2"
    )
    try:
        router.escalate(
            started["id"], reason="vm_limit", partial_branch="fleet/partial-3"
        )
    except FleetError as exc:
        assert exc.code == "already_escalated"
    else:
        raise AssertionError("expected already_escalated")


def test_failed_local_start_is_recorded(tmp_path) -> None:
    router, _env = _router(tmp_path, executor=_Start(fail=True))
    try:
        router.dispatch(_spec())
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the stub failure")
    root = tmp_path / "jobs"
    lanes = list(root.glob("*/lane.json"))
    assert len(lanes) == 1
    stored = json.loads(lanes[0].read_text())
    assert stored["status"] == "failed"
    assert stored["error"] == "local_start_failed"


def test_dispatch_tool_needs_approval_and_confirm_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("OMEGA_PRIME_DISPATCH_CONFIRM", CONFIRM)
    monkeypatch.setattr(
        "omega_prime.engine.lanes.free_memory_bytes",
        lambda: 4 * GIB,
    )
    bare = ToolRegistry()
    register_dispatch_tools(bare)
    refused = json.loads(
        bare.dispatch(
            "omega_dispatch",
            {
                "repo": "acme/widget",
                "prompt": "fix the python test",
                "confirm_token": CONFIRM,
            },
        )
    )
    assert refused["error"] == "approval required"
    assert not (tmp_path / "jobs").exists()

    log = ApprovalLog()
    log.approve("omega_dispatch", "ove")
    gated = ToolRegistry(approval_log=log)
    register_dispatch_tools(gated)
    mismatch = json.loads(
        gated.dispatch(
            "omega_dispatch",
            {
                "repo": "acme/widget",
                "prompt": "fix the python test",
                "confirm_token": "nope",
                "max_minutes": 10,
                "max_files": 2,
            },
        )
    )
    assert mismatch["error"] == "confirm_rejected"
    assert not (tmp_path / "jobs").exists()

    accepted = json.loads(
        gated.dispatch(
            "omega_dispatch",
            {
                "repo": "acme/widget",
                "prompt": "fix the python test",
                "confirm_token": CONFIRM,
                "model": "zai/glm-4.5",
                "slash_command": "gsd-autonomous",
                "slash_args": "ultrathink orchestrate",
                "max_minutes": 10,
                "max_files": 2,
                "languages": ["python"],
            },
        )
    )
    assert accepted["lane"] == "local"
    assert accepted["status"] == "running"
    stored = (tmp_path / "jobs" / accepted["id"] / "lane.json").read_text()
    assert CONFIRM not in stored
    assert "gsd-autonomous" in stored


def test_status_and_cancel_tools_use_the_client(monkeypatch) -> None:
    monkeypatch.delenv("FLEET_COORDINATOR_URL", raising=False)
    monkeypatch.delenv("FLEET_COORDINATOR_TOKEN", raising=False)
    registry = ToolRegistry()
    register_dispatch_tools(registry)
    missing = json.loads(registry.dispatch("omega_jobs_status", {}))
    assert missing["error"] == "not_configured"

    class Scripted:
        def __init__(self, timeout: float = 30.0) -> None:
            del timeout

        def request(self, method, url, headers, body):
            del body
            assert headers["Authorization"] == f"Bearer {TOKEN}"
            path = urlsplit(url).path
            if method == "GET" and path == "/status":
                return HttpResponse(200, json.dumps({"draining": False}).encode())
            if method == "GET" and path == "/jobs":
                return HttpResponse(
                    200, json.dumps({"jobs": [{"id": "job-1"}]}).encode()
                )
            if method == "POST" and path == "/jobs/job-1/cancel":
                return HttpResponse(
                    200,
                    json.dumps(
                        {"job": {"id": "job-1", "status": "cancelled"}}
                    ).encode(),
                )
            raise AssertionError(f"unexpected {method} {path}")

    monkeypatch.setenv("FLEET_COORDINATOR_URL", "https://fleet.example")
    monkeypatch.setenv("FLEET_COORDINATOR_TOKEN", TOKEN)
    monkeypatch.setattr(
        "omega_prime.engine.fleet_client.seat_policy",
        lambda: _allow("fleet.example"),
    )
    monkeypatch.setattr("omega_prime.engine.fleet_client.UrllibTransport", Scripted)
    status = json.loads(registry.dispatch("omega_jobs_status", {}))
    assert status["coordinator"]["draining"] is False
    assert status["jobs"][0]["id"] == "job-1"
    refused = json.loads(registry.dispatch("omega_jobs_cancel", {"job_id": "job-1"}))
    assert refused["error"] == "approval required"
    log = ApprovalLog()
    log.approve("omega_jobs_cancel", "ove")
    approved = ToolRegistry(approval_log=log)
    register_dispatch_tools(approved)
    cancelled = json.loads(approved.dispatch("omega_jobs_cancel", {"job_id": "job-1"}))
    assert cancelled["job"]["status"] == "cancelled"
