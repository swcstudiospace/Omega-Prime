# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Tests for the local read-only dashboard API, poller, and CLI."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from omega_prime.dashboard import SourceResult
from omega_prime.dashboard.__main__ import main
from omega_prime.dashboard.app import create_app
from omega_prime.dashboard.poller import CollectorSpec, DashboardPoller

NOW = datetime(2026, 10, 21, tzinfo=UTC)


def _collectors() -> list[CollectorSpec]:
    return [
        CollectorSpec(
            "herdr",
            lambda: SourceResult(
                "herdr",
                "ok",
                items=[
                    {
                        "kind": "herdr_pane",
                        "pane_id": "1",
                        "name": "fake",
                        "agent_status": "working",
                    }
                ],
            ),
            30.0,
            1.0,
        ),
        CollectorSpec(
            "github",
            lambda: SourceResult(
                "github",
                "ok",
                items=[
                    {
                        "kind": "github_pr",
                        "repo": "org/repo",
                        "number": 7,
                        "title": "review",
                    }
                ],
            ),
            30.0,
            1.0,
        ),
    ]


def _ready_poller() -> DashboardPoller:
    poller = DashboardPoller(_collectors(), clock=lambda: NOW)
    asyncio.run(poller.refresh_now())
    return poller


def test_static_snapshot_and_health_headers() -> None:
    poller = _ready_poller()
    app = create_app(poller)
    with TestClient(app) as client:
        headers = {"host": "127.0.0.1:7421"}
        page = client.get("/", headers=headers)
        assert page.status_code == 200
        assert "Omega Prime — Task Dashboard" in page.text
        assert "default-src 'self'" in page.headers["content-security-policy"]
        assert page.headers["x-content-type-options"] == "nosniff"
        response = client.get("/api/snapshot", headers=headers)
        payload = response.json()
        assert set(payload) == {
            "generated_at",
            "sources",
            "active",
            "pr_gate",
            "waiting_on_ove",
            "finished",
        }
        assert payload["sources"]["herdr"]["status"] == "ok"
        assert "took_ms" in payload["sources"]["herdr"]
        assert len(payload["active"]) == 1
        assert payload["active"][0]["source"] == "herdr_pane"
        assert len(payload["pr_gate"]) == 1
        assert payload["pr_gate"][0]["number"] == 7
        health = client.get("/healthz", headers=headers)
        assert health.json() == {"ok": True, "version": "1"}


def test_method_and_host_guards() -> None:
    app = create_app(_ready_poller())
    with TestClient(app) as client:
        assert client.get("/api/snapshot").status_code == 403
        assert client.get("/api/snapshot", headers={"host": ""}).status_code == 403
        assert (
            client.get("/api/snapshot", headers={"host": "127.0.0.1:7421"}).status_code
            == 200
        )
        assert (
            client.get("/api/snapshot", headers={"host": "LOCALHOST:7421"}).status_code
            == 200
        )
        for host in ("127.0.0.1:9999", "evil.example:7421"):
            assert (
                client.get("/api/snapshot", headers={"host": host}).status_code == 403
            )
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            for path in ("/", "/api/snapshot"):
                response = client.request(
                    method, path, headers={"host": "127.0.0.1:7421"}
                )
                assert response.status_code == 405


def test_poller_isolates_timeout_and_hashes_content() -> None:
    calls = {"slow": 0, "fast": 0}
    changing = {"title": "first"}

    def slow() -> SourceResult:
        calls["slow"] += 1
        time.sleep(0.2)
        return SourceResult("slow", "ok")

    def fast() -> SourceResult:
        calls["fast"] += 1
        return SourceResult(
            "fast",
            "ok",
            items=[
                {
                    "kind": "github_pr",
                    "repo": "a/b",
                    "number": 1,
                    "title": changing["title"],
                }
            ],
        )

    poller = DashboardPoller(
        [CollectorSpec("slow", slow, 3.0, 0.02), CollectorSpec("fast", fast, 3.0, 0.5)],
        clock=lambda: NOW,
    )
    asyncio.run(poller.refresh_now())
    first_hash = poller.snapshot_hash()
    assert poller.sources_status()["slow"]["status"] == "error"
    assert poller.sources_status()["fast"]["status"] == "ok"
    asyncio.run(poller.refresh_now())
    assert poller.snapshot_hash() == first_hash
    changing["title"] = "second"
    asyncio.run(poller.refresh_now())
    assert poller.snapshot_hash() != first_hash
    assert calls["fast"] == 3


def test_poller_redacts_snapshot_and_waiter() -> None:
    secret = "sk-FAKE1234567890abcd"
    poller = DashboardPoller(
        [
            CollectorSpec(
                "github",
                lambda: SourceResult(
                    "github",
                    "ok",
                    items=[
                        {
                            "kind": "github_pr",
                            "repo": "a/b",
                            "number": 1,
                            "title": secret,
                        }
                    ],
                ),
                3.0,
                1.0,
            )
        ],
        clock=lambda: NOW,
    )
    asyncio.run(poller.refresh_now())
    assert secret not in json.dumps(poller.snapshot())
    assert asyncio.run(poller.wait_for_update(0.01))
    assert not asyncio.run(poller.wait_for_update(0.01))


def test_poller_due_scheduling() -> None:
    calls = 0

    def collect() -> SourceResult:
        nonlocal calls
        calls += 1
        return SourceResult("local", "ok")

    poller = DashboardPoller([CollectorSpec("local", collect, 3.0, 1.0)], tick_s=0.01)

    async def run_briefly() -> None:
        task = asyncio.create_task(poller.run_forever())
        await asyncio.sleep(0.08)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_briefly())
    assert calls == 1


def test_cli_loopback_port_once_stop_and_pidfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[tuple[object, str, int]] = []
    assert (
        main(
            ["--host", "0.0.0.0"],
            home=tmp_path,
            serve=lambda *args: served.append(args),
        )
        == 2
    )
    assert (
        main(["--host", "::"], home=tmp_path, serve=lambda *args: served.append(args))
        == 2
    )
    assert (
        main(
            ["--host", "10.0.0.5"],
            home=tmp_path,
            serve=lambda *args: served.append(args),
        )
        == 2
    )
    assert (
        main(
            ["--host", "localhost"],
            home=tmp_path,
            serve=lambda *args: served.append(args),
            poller_factory=_ready_poller,
        )
        == 0
    )
    assert served[-1][1:] == ("localhost", 7421)

    monkeypatch.setenv("OMEGA_PRIME_DASHBOARD_PORT", "7555")
    assert (
        main(
            ["--host", "localhost"],
            home=tmp_path,
            serve=lambda *args: served.append(args),
        )
        == 0
    )
    assert served[-1][1:] == ("localhost", 7555)
    assert not (tmp_path / ".omega-prime" / "dashboard" / "dashboard.pid").exists()

    output = io.StringIO()
    assert (
        main(
            ["--once"],
            home=tmp_path,
            stdout=output,
            poller_factory=_ready_poller,
            serve=lambda *args: pytest.fail("serve called"),
        )
        == 0
    )
    assert json.loads(output.getvalue())["sources"]["herdr"]["status"] == "ok"

    assert main(["--stop"], home=tmp_path) == 1
    pidfile = tmp_path / ".omega-prime" / "dashboard" / "dashboard.pid"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text("1234\n", encoding="ascii")
    signalled: list[tuple[int, int]] = []
    assert (
        main(
            ["--stop"],
            home=tmp_path,
            signal_fn=lambda pid, sig: signalled.append((pid, sig)),
        )
        == 0
    )
    assert signalled == [(1234, signal.SIGTERM)]

    modes: list[tuple[int, int]] = []

    def serving(app: object, host: str, port: int) -> None:
        modes.append(
            (pidfile.parent.stat().st_mode & 0o777, pidfile.stat().st_mode & 0o777)
        )

    assert (
        main(
            ["--host", "localhost", "--port", "7421"],
            home=tmp_path,
            serve=serving,
            poller_factory=_ready_poller,
        )
        == 0
    )
    assert modes == [(0o700, 0o600)]
    assert not pidfile.exists()


def test_sse_emits_retry_snapshot_and_update() -> None:
    """SSE is driven over raw ASGI: this starlette TestClient buffers whole bodies."""

    value = {"title": "before"}
    poller = DashboardPoller(
        [
            CollectorSpec(
                "github",
                lambda: SourceResult(
                    "github",
                    "ok",
                    items=[
                        {
                            "kind": "github_pr",
                            "repo": "a/b",
                            "number": 1,
                            "title": value["title"],
                        }
                    ],
                ),
                30.0,
                1.0,
            )
        ],
        clock=lambda: NOW,
        tick_s=0.01,
    )
    app = create_app(poller, heartbeat_s=0.05)

    async def drive() -> None:
        events: asyncio.Queue[str] = asyncio.Queue()

        async def receive() -> dict[str, object]:
            return {"type": "http.disconnect"}

        async def send(message: dict[str, object]) -> None:
            if message["type"] == "http.response.start":
                assert message["status"] == 200
                headers = {
                    key.decode(): value.decode() for key, value in message["headers"]
                }
                assert headers["content-type"].startswith("text/event-stream")
                return
            body = message.get("body", b"")
            if isinstance(body, bytes):
                for line in body.decode().splitlines():
                    if line:
                        events.put_nowait(line)

        scope: dict[str, object] = {
            "type": "http",
            "method": "GET",
            "path": "/api/stream",
            "headers": [(b"host", b"127.0.0.1:7421")],
            "query_string": b"",
        }
        server = asyncio.create_task(app(scope, receive, send))

        async def data_frame(deadline: float) -> str:
            loop = asyncio.get_running_loop()
            while loop.time() < deadline:
                line = await asyncio.wait_for(
                    events.get(), timeout=max(0.01, deadline - loop.time())
                )
                if line.startswith("data: "):
                    return line
            raise AssertionError("no snapshot data frame before deadline")

        loop = asyncio.get_running_loop()
        try:
            assert await asyncio.wait_for(events.get(), 2.0) == "retry: 3000"
            await poller.refresh_now()
            first = await data_frame(loop.time() + 2.0)
            assert (
                json.loads(first.removeprefix("data: "))["pr_gate"][0]["title"]
                == "before"
            )
            value["title"] = "after"
            await poller.refresh_now()
            second = await data_frame(loop.time() + 2.0)
            assert (
                json.loads(second.removeprefix("data: "))["pr_gate"][0]["title"]
                == "after"
            )
            await poller.refresh_now()
            assert await asyncio.wait_for(events.get(), 2.0) == ": heartbeat"
        finally:
            server.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server

    asyncio.run(drive())
