from __future__ import annotations

import asyncio
import json

from starlette.testclient import TestClient

from omega_prime.dashboard.app import create_app
from omega_prime.dashboard.poller import DashboardPoller
from omega_prime.dashboard.terminal import TerminalService

HOST = {"host": "127.0.0.1:7421"}


def _poller():
    poller = DashboardPoller([])
    poller._snapshot = {"active": [{"id": "task:one", "owner": {"kind": "other", "ref": "no"}, "herdr_pane": {"pane_id": "w2:p4"}}], "idle_sessions": [], "finished": []}
    return poller


def test_terminal_route_resolves_only_task_owned_pane_and_uses_read_argv():
    seen = []
    async def runner(pane):
        seen.append(pane)
        return "safe output"
    service = TerminalService(runner=runner, sleep=lambda _: asyncio.sleep(3600))
    with TestClient(create_app(_poller(), terminal_service=service)) as client:
        response = client.get("/api/tasks/task%3Aone/terminal", headers=HOST, stream=True)
        assert response.status_code == 200
        response.close()
    assert seen == ["w2:p4"]


def test_terminal_sanitizes_redacts_caps_and_sends_only_changed_frames():
    async def runner(_): return "<b>safe</b> ghp_abcdefgh123456"
    service = TerminalService(runner=runner)
    stream = service.stream("w2:p4")
    assert "retry: 3000" in asyncio.run(anext(stream))
    event = asyncio.run(anext(stream))
    assert "[REDACTED]" in event and "ghp_abcdefgh123456" not in event and "<b>safe</b>" in event
    asyncio.run(stream.aclose())


def test_terminal_disconnect_releases_capacity_and_stops_reads():
    calls = []
    async def runner(_):
        calls.append(1)
        return "text"
    service = TerminalService(runner=runner)
    async def drain_one():
        stream = service.stream("w1:p1")
        await anext(stream)
        await anext(stream)
        await stream.aclose()
        assert service._slots._value == 4
    asyncio.run(drain_one())
    assert len(calls) == 1


def test_terminal_has_no_input_route_or_subcommand():
    paths = {route.path for route in create_app(_poller()).routes}
    assert "/api/tasks/{task_id:path}/terminal" in paths
    assert not any("send" in path or "input" in path for path in paths)
