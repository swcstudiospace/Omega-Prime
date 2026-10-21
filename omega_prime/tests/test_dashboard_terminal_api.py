from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from omega_prime.dashboard.app import create_app
from omega_prime.dashboard.poller import DashboardPoller
from omega_prime.dashboard.terminal import TerminalService


def _poller(*, task_id="task:one", mapping=None):
    poller = DashboardPoller([])
    row = {"id": task_id, "owner": {"kind": "worker", "ref": "not-a-pane"}}
    if mapping is not None:
        row["herdr_pane"] = mapping
    poller._snapshot = {"active": [row], "idle_sessions": [], "finished": []}
    return poller


async def _wire(app, path, *, method="GET", stop_after_terminal=1):
    messages = []
    disconnected = asyncio.Event()
    request_sent = False
    terminal_frames = 0
    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}
    async def send(message):
        nonlocal terminal_frames
        messages.append(message)
        if message.get("type") == "http.response.body":
            body = message.get("body", b"")
            if b"event: terminal" in body:
                terminal_frames += 1
                if terminal_frames >= stop_after_terminal:
                    disconnected.set()
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(b"host", b"127.0.0.1:7421")], "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 7421)}
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], body.decode()


def test_terminal_route_resolves_only_task_owned_pane_and_uses_read_argv():
    calls = []
    async def runner(argv):
        calls.append(argv)
        return "safe output"
    service = TerminalService(runner=runner)
    app = create_app(_poller(mapping={"pane_id": "w2:p4"}), terminal_service=service)
    status, wire = asyncio.run(_wire(app, "/api/tasks/task:one/terminal"))
    assert status == 200
    assert '"text":"safe output"' in wire
    assert calls == [["herdr", "pane", "read", "w2:p4", "--source", "recent", "--lines", "200", "--format", "text"]]
    assert asyncio.run(_wire(create_app(_poller(task_id="unknown"), terminal_service=service), "/api/tasks/unknown/terminal"))[0] == 404
    assert calls == calls[:1]


def test_terminal_sanitizes_redacts_caps_and_sends_only_changed_frames():
    content = "\x1b[31m" + "Bearer abcdefghijkl ghp_abcdefgh123456 api_key='topsecret'\x1b[0m\n" + "é" * 40000 + "\n" + "\n".join(f"line-{i}" for i in range(205))
    calls = 0
    async def runner(_argv):
        nonlocal calls
        calls += 1
        return content if calls == 1 else content + "\nnew frame"
    ticks = iter(datetime(2026, 10, 21, tzinfo=UTC) + timedelta(seconds=n) for n in range(10))
    service = TerminalService(runner=runner, clock=lambda: next(ticks), sleep=lambda _: asyncio.sleep(0))
    status, wire = asyncio.run(_wire(create_app(_poller(mapping={"pane_id": "w2:p4"}), terminal_service=service), "/api/tasks/task:one/terminal", stop_after_terminal=2))
    assert status == 200
    events = [json.loads(line[6:]) for line in wire.splitlines() if line.startswith("data: ")]
    assert len(events) == 2
    assert "abcdefghijkl" not in wire and "ghp_abcdefgh123456" not in wire and "topsecret" not in wire and "\x1b" not in wire
    assert len(events[-1]["text"].encode()) <= 65536 and len(events[-1]["text"].splitlines()) <= 200
    assert events[0]["updated_at"] != events[1]["updated_at"]
    assert calls == 2
    assert service._slots._value == 4


def test_terminal_disconnect_releases_capacity_and_stops_reads():
    calls = []
    async def runner(_argv):
        calls.append(1)
        return "constant"
    service = TerminalService(runner=runner, sleep=lambda _: asyncio.sleep(0))
    app = create_app(_poller(mapping={"pane_id": "w1:p1"}), terminal_service=service)
    status, wire = asyncio.run(_wire(app, "/api/tasks/task:one/terminal", stop_after_terminal=1))
    assert status == 200 and "event: terminal" in wire
    assert len(calls) == 1 and service._slots._value == 4


def test_terminal_has_no_input_route_or_subcommand():
    paths = {route.path for route in create_app(_poller()).routes}
    assert "/api/tasks/{task_id:path}/terminal" in paths
    assert not any("send" in path or "input" in path for path in paths)
