from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import inspect

from omega_prime.dashboard.app import create_app
from omega_prime.dashboard.poller import DashboardPoller


def _terminal_service(**kwargs):
    if "terminal_service" not in inspect.signature(create_app).parameters:
        return None
    from omega_prime.dashboard.terminal import TerminalService
    return TerminalService(**kwargs)


def _app(poller, service):
    if "terminal_service" in inspect.signature(create_app).parameters:
        return create_app(poller, terminal_service=service)
    return create_app(poller)

def _poller(*, task_id="task:one", mapping=None):
    poller = DashboardPoller([])
    rows = []
    if task_id is not None:
        row = {"id": task_id, "owner": {"kind": "worker", "ref": "not-a-pane"}}
        if mapping is not None:
            row["herdr_pane"] = mapping
        rows.append(row)
    poller._snapshot = {"active": rows, "idle_sessions": [], "finished": []}
    return poller


async def _wire(app, path, *, method="GET", stop_after_terminal=1, stop_on_heartbeat=False, disconnect_when=None):
    messages = []
    disconnected = asyncio.Event()
    request_sent = False
    terminal_frames = 0

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        if disconnect_when is not None:
            await disconnect_when.wait()
        else:
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
            if stop_on_heartbeat and b": heartbeat" in body:
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
    service = _terminal_service(runner=runner)
    app = _app(_poller(mapping={"pane_id": "w2:p4"}), service)
    status, wire = asyncio.run(_wire(app, "/api/tasks/task:one/terminal"))
    assert status == 200 and '"text":"safe output"' in wire
    assert calls == [["herdr", "pane", "read", "w2:p4", "--source", "recent", "--lines", "200", "--format", "text"]]
    unmapped_status, unmapped_wire = asyncio.run(_wire(_app(_poller(), service), "/api/tasks/task:one/terminal"))
    assert unmapped_status == 200 and '"status":"no_session"' in unmapped_wire and len(calls) == 1
    assert asyncio.run(_wire(_app(_poller(task_id=None), service), "/api/tasks/unknown/terminal"))[0] == 404


def test_terminal_sanitizes_redacts_caps_and_sends_only_changed_frames():
    content = "\x1b[31m" + "é" * 40000 + "\n" + "\n".join(f"line-{i}" for i in range(205)) + "\nBearer abcdefghijkl ghp_abcdefgh123456 github_pat_\x1b[32mABCDEFGHIJKLMNOPQRSTUVWXYZ012345 sk-abcdefgh123456 aaaaaaaa.bbbbbbbb.cccccccc api_key='topsecret'\x1b[0m"
    calls = 0
    async def runner(_argv):
        nonlocal calls
        calls += 1
        return content if calls == 1 else content + "\nnew frame"
    ticks = iter(datetime(2026, 10, 21, tzinfo=UTC) + timedelta(seconds=n) for n in range(10))
    service = _terminal_service(runner=runner, clock=lambda: next(ticks), sleep=lambda _: asyncio.sleep(0))
    status, wire = asyncio.run(_wire(_app(_poller(mapping={"pane_id": "w2:p4"}), service), "/api/tasks/task:one/terminal", stop_after_terminal=2))
    assert status == 200
    events = [json.loads(line[6:]) for line in wire.splitlines() if line.startswith("data: ")]
    assert len(events) == 2
    assert all(secret not in wire for secret in ("abcdefghijkl", "ghp_abcdefgh123456", "github_pat_", "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", "sk-abcdefgh123456", "aaaaaaaa.bbbbbbbb.cccccccc", "topsecret", "\x1b"))
    assert len(events[-1]["text"].encode()) <= 65536 and len(events[-1]["text"].splitlines()) <= 200
    assert events[0]["updated_at"] != events[1]["updated_at"]
    assert calls == 2 and service._slots._value == 4


def test_terminal_disconnect_releases_capacity_and_stops_reads():
    calls = []
    async def runner(_argv):
        calls.append(1)
        return "constant"
    service = _terminal_service(runner=runner, sleep=lambda _: asyncio.sleep(0))
    status, wire = asyncio.run(_wire(_app(_poller(mapping={"pane_id": "w1:p1"}), service), "/api/tasks/task:one/terminal"))
    assert status == 200 and "event: terminal" in wire
    assert len(calls) == 1 and service._slots._value == 4


def test_terminal_cancels_capture_when_raw_asgi_viewer_disconnects():
    started, cancelled = asyncio.Event(), asyncio.Event()
    async def runner(_argv):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    service = _terminal_service(runner=runner)
    async def exercise():
        status, wire = await _wire(_app(_poller(mapping={"pane_id": "w1:p1"}), service), "/api/tasks/task:one/terminal", disconnect_when=started)
        assert status == 200 and "retry: 3000" in wire
        assert cancelled.is_set() and service._slots._value == 4
    asyncio.run(exercise())


def test_terminal_unchanged_capture_heartbeats_without_timestamp_change():
    calls = []
    async def runner(_argv):
        calls.append(1)
        return "same"
    service = _terminal_service(runner=runner, sleep=lambda _: asyncio.sleep(0))
    status, wire = asyncio.run(_wire(_app(_poller(mapping={"pane_id": "w1:p1"}), service), "/api/tasks/task:one/terminal", stop_after_terminal=9999, stop_on_heartbeat=True))
    assert status == 200 and ": heartbeat" in wire
    assert wire.count('"updated_at":"') == 1 and len(calls) == 2
    assert service._slots._value == 4
def test_terminal_state_change_does_not_advance_output_timestamp():
    calls = 0
    async def runner(_argv):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "last good frame"
        raise OSError("private stderr is never emitted")
    service = _terminal_service(runner=runner, clock=lambda: datetime(2026, 10, 21, tzinfo=UTC), sleep=lambda _: asyncio.sleep(0))
    status, wire = asyncio.run(_wire(_app(_poller(mapping={"pane_id": "w1:p1"}), service), "/api/tasks/task:one/terminal", stop_after_terminal=2))
    events = [json.loads(line[6:]) for line in wire.splitlines() if line.startswith("data: ")]
    assert status == 200 and [frame["status"] for frame in events] == ["live", "unavailable"]
    assert events[0]["updated_at"] == events[1]["updated_at"]
    assert "private stderr" not in wire and service._slots._value == 4



def test_terminal_reports_confirmed_closed_pane_and_releases_capacity():
    from omega_prime.dashboard.terminal import PaneClosedError
    async def runner(_argv):
        raise PaneClosedError()
    service = _terminal_service(runner=runner)
    status, wire = asyncio.run(_wire(_app(_poller(mapping={"pane_id": "w1:p1"}), service), "/api/tasks/task:one/terminal"))
    assert status == 200 and '"status":"pane_closed"' in wire
    assert service._slots._value == 4


def test_terminal_stream_limit_returns_429_before_sse_starts():
    calls = []
    async def runner(argv):
        calls.append(argv)
        return "should not run"
    service = _terminal_service(runner=runner)
    async def saturate_and_request():
        for _ in range(4):
            assert await service.reserve()
        status, wire = await _wire(_app(_poller(mapping={"pane_id": "w1:p1"}), service), "/api/tasks/task:one/terminal")
        for _ in range(4):
            service._slots.release()
        return status, wire
    status, wire = asyncio.run(saturate_and_request())
    assert status == 429 and "event: terminal" not in wire
    assert calls == []


def test_terminal_has_no_input_route_or_subcommand():
    paths = {route.path for route in create_app(_poller()).routes}
    assert "/api/tasks/{task_id:path}/terminal" in paths
    assert not any("send" in path or "input" in path for path in paths)
