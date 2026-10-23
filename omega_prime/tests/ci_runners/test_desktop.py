"""Desktop wake client: bounded retries, outcomes, inventory, stop."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from omega_prime.ci_runners.desktop import (
    BACKOFF_CAP_S,
    DesktopClient,
    DesktopError,
    LaunchOutcome,
)
from omega_prime.ci_runners.http import HttpRequest, HttpResponse, TransportError

from ._fakes import FakeHttp, resp
from ._support import FakeClock

URL = "https://desktop.example.test"
TOKEN = "desktop-bearer-token-for-tests-000000"
JIT = "jit-blob-secret-for-tests"


def make(http: FakeHttp, clock: FakeClock, **kw: Any) -> DesktopClient:
    kw.setdefault("jitter", lambda: 1.0)
    return DesktopClient(URL, lambda: TOKEN, http, clock=clock, wait=clock.wait, **kw)


def launch(client: DesktopClient) -> Any:
    return asyncio.run(client.launch("omega-ci-1-abcd", "gen-1", JIT))


def test_https_only() -> None:
    with pytest.raises(ValueError):
        DesktopClient("http://desktop.example.test", lambda: TOKEN, FakeHttp())


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (201, LaunchOutcome.ACCEPTED),
        (200, LaunchOutcome.REPLAYED),
        (429, LaunchOutcome.CAPACITY),
        (409, LaunchOutcome.CONFLICT),
        (400, LaunchOutcome.REJECTED),
    ],
)
def test_first_request_is_the_launch_post(
    clock: FakeClock, status: int, outcome: LaunchOutcome
) -> None:
    http = FakeHttp().on("POST", "/v1/runners", resp(status))
    result = launch(make(http, clock))
    assert result.outcome is outcome and result.attempts == 1
    assert [(c.method, c.url) for c in http.calls] == [("POST", f"{URL}/v1/runners")]
    assert http.bodies("/v1/runners") == [
        {"name": "omega-ci-1-abcd", "generation": "gen-1", "jitconfig": JIT}
    ]
    assert http.calls[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert JIT not in repr(result) and clock.waits == []


def test_wake_retries_502_503_and_connect_with_same_body(clock: FakeClock) -> None:
    http = FakeHttp().on(
        "POST",
        "/v1/runners",
        resp(503),
        TransportError("ConnectionRefusedError"),
        resp(502),
        resp(201),
    )
    result = launch(make(http, clock))
    assert result.outcome is LaunchOutcome.ACCEPTED and result.attempts == 4
    assert clock.waits == [0.5, 1.0, 2.0]
    assert len({c.body for c in http.calls}) == 1


def test_budget_bounds_attempts_and_waits(clock: FakeClock) -> None:
    http = FakeHttp().on("POST", "/v1/runners", resp(503))
    start = clock.now
    result = launch(make(http, clock))
    assert result.outcome is LaunchOutcome.WAKE_FAILED
    assert max(clock.waits) == BACKOFF_CAP_S
    assert clock.now - start < 150
    assert result.attempts == 14  # 0.5+1+2+4+8 then 15 s steps until 150 s


def test_attempt_cap_is_sixteen(clock: FakeClock) -> None:
    http = FakeHttp().on("POST", "/v1/runners", resp(503))
    result = launch(make(http, clock, budget_s=10_000))
    assert result.attempts == 16 and len(http.calls) == 16 and len(clock.waits) == 15


def test_request_time_counts_and_timeouts_are_clamped(clock: FakeClock) -> None:
    timeouts: list[float] = []

    def slow(req: HttpRequest) -> HttpResponse:
        timeouts.append(req.timeout)
        clock.advance(40)
        return resp(503)

    http = FakeHttp().on("POST", "/v1/runners", slow)
    result = launch(make(http, clock))
    assert result.outcome is LaunchOutcome.WAKE_FAILED
    assert result.elapsed_s <= 150 + 1e-9 or result.attempts <= 4
    assert timeouts[0] == 30 and all(t <= 30 for t in timeouts)
    assert len(timeouts) <= 4


def test_jitter_range(clock: FakeClock) -> None:
    low = make(FakeHttp(), clock, jitter=lambda: 0.0)
    assert low.backoff(1) == 0.25 and low.backoff(10) == BACKOFF_CAP_S / 2


def test_launch_validation(clock: FakeClock) -> None:
    client = make(FakeHttp(), clock)
    with pytest.raises(ValueError):
        asyncio.run(client.launch("bad name", "g", JIT))
    with pytest.raises(ValueError):
        asyncio.run(client.launch("omega-ci-1-a", "", JIT))


def test_inventory_and_find(clock: FakeClock) -> None:
    rows = [
        {"name": "omega-ci-1-a", "generation": "g1", "status": "running", "pid": 7},
        {"name": "omega-ci-2-b", "generation": "g2", "status": 3, "pid": True},
        {"name": "evil name", "generation": "g"},
        {"name": "omega-ci-3-c", "generation": 3},
        "junk",
    ]
    http = FakeHttp().on("GET", "/v1/runners", resp(200, rows))
    client = make(http, clock)
    got = asyncio.run(client.inventory())
    assert [(r.name, r.status, r.pid) for r in got] == [
        ("omega-ci-1-a", "running", 7),
        ("omega-ci-2-b", "unknown", None),
    ]
    assert asyncio.run(client.find("omega-ci-1-a", "g1")) is True
    assert asyncio.run(client.find("omega-ci-1-a", "other")) is False
    http.on("GET", "/v1/runners", resp(401))
    assert asyncio.run(client.find("omega-ci-1-a", "g1")) is None
    for bad in (resp(200, {"no": "list"}), HttpResponse(200, {}, b"{")):
        http.on("GET", "/v1/runners", bad)
        with pytest.raises(DesktopError):
            asyncio.run(client.inventory())


def test_stop(clock: FakeClock) -> None:
    http = FakeHttp().on("DELETE", "/v1/runners/omega-ci-1-a", resp(202))
    client = make(http, clock)
    assert asyncio.run(client.stop("omega-ci-1-a"))
    http.on("DELETE", "/v1/runners/omega-ci-1-a", resp(404))
    assert asyncio.run(client.stop("omega-ci-1-a"))
    http.on("DELETE", "/v1/runners/omega-ci-1-a", resp(500))
    with pytest.raises(DesktopError, match="500"):
        asyncio.run(client.stop("omega-ci-1-a"))
    with pytest.raises(DesktopError, match="no response"):
        asyncio.run(make(http, clock, max_attempts=1, budget_s=0).stop("omega-ci-1-a"))
    with pytest.raises(ValueError):
        asyncio.run(client.stop("../etc"))
