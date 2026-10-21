# SPDX-License-Identifier: AGPL-3.0-only
"""Terminal websocket bridge, protocol and app-wiring tests (fakes only)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from omega_prime.dashboard.__main__ import main
from omega_prime.dashboard.app import create_app
from omega_prime.dashboard.poller import DashboardPoller
from omega_prime.dashboard.terminal import PaneClosedError
from omega_prime.dashboard.terminal_ws import (
    TERMINAL_INPUT_MAX_BYTES,
    TERMINAL_WS_LIMIT,
    HerdrBridge,
    make_terminal_ws_endpoint,
    redact_ansi_frame,
)

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)

# Synthetic redaction canaries, assembled at runtime so no complete
# credential-shaped literal is stored in the repository (no-secrets rule).
_CANARY = "canary" + "0" * 6
GH_CANARY = "gh" + "p_" + _CANARY
OPENAI_CANARY = "s" + "k-" + _CANARY
JWT_CANARY = ".".join(c * 8 for c in "abc")
BEARER_CANARY = "bearer" + "canary"
ASSIGN_NAME = "GITHUB" + "_" + "TOKEN"
ASSIGN_VALUE = "canary" + "value" + "42"


def _fake_runner(reads=None):
    """Async runner recording argv; scripted reads popped in order."""
    calls: list[list[str]] = []

    async def runner(argv):
        calls.append(argv)
        if reads is None:
            return "ok"
        return reads.pop(0)

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_redact_ansi_frame_preserves_escapes_and_redacts_visible():
    frame = (
        f"\x1b[31mBearer {BEARER_CANARY}\x1b[0m {GH_CANARY} "
        f"{OPENAI_CANARY} {JWT_CANARY} api_key='{ASSIGN_VALUE}'\n"
        "ok\x1b[2Jline"
    )
    out = redact_ansi_frame(frame)
    assert "\x1b[31m" in out and "\x1b[0m" in out and "\x1b[2J" in out
    assert BEARER_CANARY not in out
    assert GH_CANARY not in out
    assert OPENAI_CANARY not in out
    assert JWT_CANARY not in out
    assert ASSIGN_VALUE not in out
    assert "[REDACTED]" in out
    assert "\n" in out


def test_redact_ansi_frame_drops_control_chars_keeps_newline_tab():
    out = redact_ansi_frame("a\x85b\nc\td")
    assert "\x85" not in out
    assert "a" in out and "b" in out
    assert "\n" in out and "\t" in out


def _visible(text):
    import re

    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]", "", text)


def test_redact_ansi_frame_matches_across_styling_splits():
    frame = f"\x1b[32m{ASSIGN_NAME}=\x1b[0m{ASSIGN_VALUE}\x1b[0m done\nBearer \x1b[1m{BEARER_CANARY}\x1b[0m"
    out = redact_ansi_frame(frame)
    assert ASSIGN_VALUE not in out and BEARER_CANARY not in out
    assert ASSIGN_VALUE not in _visible(out) and BEARER_CANARY not in _visible(out)
    assert _visible(out) == f"{ASSIGN_NAME}=[REDACTED] done\nBearer [REDACTED]"
    assert out.startswith("\x1b[32m") and "\x1b[0m" in out


def test_redact_ansi_frame_visible_text_equals_sanitized_text():
    from omega_prime.dashboard.terminal import sanitize_terminal_text

    frame = f"a\x1b[31m{GH_CANARY[:4]}\x1b[0m{GH_CANARY[4:]} b\n\x1b[1m{ASSIGN_NAME}\x1b[0m={ASSIGN_VALUE}"
    out = redact_ansi_frame(frame)
    assert _visible(out) == sanitize_terminal_text(frame)
    assert GH_CANARY[4:] not in out and ASSIGN_VALUE not in out


def test_redact_ansi_frame_strips_osc_hyperlinks_and_titles():
    link = f"\x1b]8;;file:///canary?{ASSIGN_NAME}={ASSIGN_VALUE}\x07click\x1b]8;;\x07"
    title = f"\x1b]0;{GH_CANARY}\x1b\\"
    dcs = f"\x1bP{OPENAI_CANARY}\x1b\\"
    out = redact_ansi_frame(f"{title}see {link} ok{dcs}\x1b[1m!\x1b[0m")
    assert ASSIGN_VALUE not in out and GH_CANARY not in out and OPENAI_CANARY not in out
    assert "\x1b]" not in out and "\x1bP" not in out
    assert out == "see click ok\x1b[1m!\x1b[0m"


def test_redact_ansi_frame_unterminated_osc_is_redacted_as_text():
    out = redact_ansi_frame(f"x \x1b]8;;{ASSIGN_NAME}={ASSIGN_VALUE}")
    assert ASSIGN_VALUE not in out
    assert "\x1b]" not in out


def test_read_frame_uses_visible_ansi_argv_and_redacts():
    runner = _fake_runner(reads=[f"\x1b[31m{ASSIGN_NAME}={ASSIGN_VALUE}\x1b[0m"])
    bridge = HerdrBridge(runner=runner)
    frame = asyncio.run(bridge.read_frame("w2:p4"))
    assert runner.calls == [
        ["herdr", "pane", "read", "w2:p4", "--source", "visible", "--format", "ansi"]
    ]
    assert "\x1b[31m" in frame and "\x1b[0m" in frame
    assert ASSIGN_VALUE not in frame


def test_send_text_and_send_keys_argv_are_exact():
    runner = _fake_runner()
    bridge = HerdrBridge(runner=runner)
    asyncio.run(bridge.send_text("w2:p4", "make check\n"))
    asyncio.run(bridge.send_keys("w2:p4", ["enter", "c-c"]))
    assert runner.calls[0] == ["herdr", "pane", "send-text", "w2:p4", "make check\n"]
    assert runner.calls[1] == ["herdr", "pane", "send-keys", "w2:p4", "enter", "c-c"]


def _collect(generator, limit):
    async def run():
        out = []
        async for event in generator:
            out.append(event)
            if len(out) >= limit:
                break
        return out

    return asyncio.run(run())


def test_poll_events_emits_only_changed_frames():
    runner = _fake_runner(reads=["A", "A", "B"])
    seconds = iter(range(50))

    def clock():
        return NOW + timedelta(seconds=next(seconds))

    bridge = HerdrBridge(runner=runner, clock=clock, sleep=lambda _s: asyncio.sleep(0))
    events = _collect(bridge.poll_events(lambda: "w2:p4"), 2)
    assert [e["type"] for e in events] == ["frame", "frame"]
    assert events[0]["text"] == "A" and events[1]["text"] == "B"
    assert events[0]["updated_at"] != events[1]["updated_at"]


def test_poll_events_no_session_then_frame_on_return():
    runner = _fake_runner(reads=["A"])
    bridge = HerdrBridge(
        runner=runner, clock=lambda: NOW, sleep=lambda _s: asyncio.sleep(0)
    )
    provider: dict[str, str | None] = {"pane": None}

    def provide():
        return provider["pane"]

    async def run():
        gen = bridge.poll_events(provide)
        first = await gen.__anext__()
        provider["pane"] = "w2:p4"
        second = await gen.__anext__()
        third = await gen.__anext__()
        return [first, second, third]

    events = asyncio.run(run())
    assert events[0] == {"type": "status", "status": "no_session"}
    # Recovery is announced so the client clears its error state.
    assert events[1] == {"type": "status", "status": "live"}
    assert events[2]["type"] == "frame" and events[2]["text"] == "A"


def test_poll_events_recovery_resends_unchanged_frame():
    calls = {"n": 0}

    async def runner(argv):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("transient")
        return "same"

    bridge = HerdrBridge(
        runner=runner, clock=lambda: NOW, sleep=lambda _s: asyncio.sleep(0)
    )

    async def run():
        gen = bridge.poll_events(lambda: "w2:p4")
        return [await gen.__anext__() for _ in range(4)]

    events = asyncio.run(run())
    assert events[0]["type"] == "frame" and events[0]["text"] == "same"
    assert events[1] == {"type": "status", "status": "unavailable"}
    assert events[2] == {"type": "status", "status": "live"}
    assert events[3]["type"] == "frame" and events[3]["text"] == "same"


def test_poll_events_pane_switch_reyields_first_frame():
    runner = _fake_runner(reads=["A", "A"])
    bridge = HerdrBridge(
        runner=runner, clock=lambda: NOW, sleep=lambda _s: asyncio.sleep(0)
    )
    panes = ["w2:p4", "w2:p9"]

    def provide():
        return panes.pop(0)

    async def run():
        gen = bridge.poll_events(provide)
        first = await gen.__anext__()
        second = await gen.__anext__()
        return first, second

    first, second = asyncio.run(run())
    assert first["text"] == "A" and second["text"] == "A"
    assert first["type"] == "frame" and second["type"] == "frame"


def test_poll_events_pane_closed_ends_generator():
    async def runner(argv):
        raise PaneClosedError()

    bridge = HerdrBridge(
        runner=runner, clock=lambda: NOW, sleep=lambda _s: asyncio.sleep(0)
    )

    async def run():
        return [event async for event in bridge.poll_events(lambda: "w2:p4")]

    events = asyncio.run(run())
    assert events == [{"type": "status", "status": "pane_closed"}]


def test_poll_events_transient_error_yields_unavailable_once():
    calls = {"n": 0}

    async def runner(argv):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("boom")
        return "A"

    bridge = HerdrBridge(
        runner=runner, clock=lambda: NOW, sleep=lambda _s: asyncio.sleep(0)
    )

    async def run():
        out = []
        gen = bridge.poll_events(lambda: "w2:p4")
        async for event in gen:
            out.append(event)
            if len(out) >= 3:
                break
        return out

    events = asyncio.run(run())
    assert events[0] == {"type": "status", "status": "unavailable"}
    assert events[1] == {"type": "status", "status": "live"}
    assert events[2]["type"] == "frame" and events[2]["text"] == "A"


def test_reserve_caps_at_four_and_release_restores():
    bridge = HerdrBridge(runner=_fake_runner())

    async def run():
        held = [await bridge.reserve() for _ in range(TERMINAL_WS_LIMIT)]
        fifth = await bridge.reserve()
        bridge.release()
        again = await bridge.reserve()
        return held, fifth, again

    held, fifth, again = asyncio.run(run())
    assert held == [True] * TERMINAL_WS_LIMIT
    assert fifth is False
    assert again is True
    for _ in range(TERMINAL_WS_LIMIT):
        bridge.release()


FIXED_TOKEN = "fixed-test-token-00035"


def _poller(rows):
    poller = DashboardPoller([])
    poller._snapshot = {"active": rows, "idle_sessions": [], "finished": []}
    return poller


def _endpoint_app(endpoint):
    app = Starlette(
        routes=[WebSocketRoute("/ws/tasks/{task_id:path}/terminal", endpoint)]
    )
    return TestClient(app)


def _counter_runner():
    calls: list[list[str]] = []
    state = {"reads": 0, "sends": 0}

    async def runner(argv):
        calls.append(argv)
        if argv[1:3] in (["pane", "send-text"], ["pane", "send-keys"]):
            state["sends"] += 1
            return "{}"
        state["reads"] += 1
        return f"frame-{state['reads']}"

    runner.calls = calls  # type: ignore[attr-defined]
    runner.state = state  # type: ignore[attr-defined]
    return runner


def _auth(ws, mode="rw", token=FIXED_TOKEN):
    ws.send_json({"type": "auth", "token": token, "mode": mode})


def test_rw_session_forwards_input_and_keys_byte_exactly():
    calls: list[list[str]] = []
    sent = {"n": 0}

    async def runner(argv):
        calls.append(argv)
        if argv[1:3] in (["pane", "send-text"], ["pane", "send-keys"]):
            sent["n"] += 1
            return "{}"
        return "phase-two" if sent["n"] else "phase-one"

    poller = _poller(
        [
            {
                "id": "task:one",
                "herdr_pane": {"pane_id": "w2:p4"},
                "owner": {"kind": "worker", "ref": "x"},
            }
        ]
    )
    bridge = HerdrBridge(runner=runner, poll_s=0.01)
    endpoint = make_terminal_ws_endpoint(poller, bridge, token=FIXED_TOKEN)
    client = _endpoint_app(endpoint)
    with client.websocket_connect("/ws/tasks/task:one/terminal") as ws:
        _auth(ws)
        first = ws.receive_json()
        assert first == {
            "type": "frame",
            "text": "phase-one",
            "updated_at": first["updated_at"],
        }
        ws.send_json({"type": "input", "text": "make check\n"})
        second = ws.receive_json()
        assert second["text"] == "phase-two"
        ws.send_json({"type": "keys", "keys": ["enter", "c-c"]})
    assert calls.count(["herdr", "pane", "send-text", "w2:p4", "make check\n"]) == 1
    assert calls.count(["herdr", "pane", "send-keys", "w2:p4", "enter", "c-c"]) == 1


def test_wrong_token_closes_4401():
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=_fake_runner(), poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws/tasks/task:one/terminal") as ws,
    ):
        _auth(ws, token="wrong-token")
        ws.receive_json()
    assert exc.value.code == 4401


def test_non_string_token_closes_4401():
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=_fake_runner(), poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws/tasks/task:one/terminal") as ws,
    ):
        ws.send_json({"type": "auth", "token": 12345, "mode": "rw"})
        ws.receive_json()
    assert exc.value.code == 4401


def test_auth_silence_times_out_4401():
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller,
        HerdrBridge(runner=_fake_runner(), poll_s=0.01),
        token=FIXED_TOKEN,
        auth_timeout_s=0.05,
    )
    client = _endpoint_app(endpoint)
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws/tasks/task:one/terminal") as ws,
    ):
        ws.receive_json()
    assert exc.value.code == 4401


def test_bad_mode_closes_4401():
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=_fake_runner(), poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws/tasks/task:one/terminal") as ws,
    ):
        _auth(ws, mode="admin")
        ws.receive_json()
    assert exc.value.code == 4401


@pytest.mark.parametrize("task_id", ["unknown-task", "bad%01id"])
def test_unknown_or_invalid_task_closes_4404(task_id):
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=_fake_runner(), poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(f"/ws/tasks/{task_id}/terminal") as ws,
    ):
        _auth(ws)
        ws.receive_json()
    assert exc.value.code == 4404


def test_slash_task_id_reaches_end_to_end():
    runner = _counter_runner()
    poller = _poller(
        [
            {
                "id": "worker/1",
                "herdr_pane": {"pane_id": "w2:p4"},
                "owner": {"kind": "worker", "ref": "x"},
            }
        ]
    )
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=runner, poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with client.websocket_connect("/ws/tasks/worker%2F1/terminal") as ws:
        _auth(ws)
        frame = ws.receive_json()
        assert frame["type"] == "frame" and frame["text"] == "frame-1"


def test_client_readonly_receives_frames_drops_input():
    runner = _counter_runner()
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=runner, poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with client.websocket_connect("/ws/tasks/task:one/terminal") as ws:
        _auth(ws, mode="ro")
        assert ws.receive_json()["text"] == "frame-1"
        ws.send_json({"type": "input", "text": "should not land\n"})
        assert ws.receive_json()["text"] == "frame-2"
        assert ws.receive_json()["text"] == "frame-3"
    assert not any(
        c[1:3] in (["pane", "send-text"], ["pane", "send-keys"]) for c in runner.calls
    )


def test_oversized_input_is_dropped_silently():
    runner = _counter_runner()
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=runner, poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with client.websocket_connect("/ws/tasks/task:one/terminal") as ws:
        _auth(ws)
        ws.receive_json()
        ws.send_json({"type": "input", "text": "x" * (TERMINAL_INPUT_MAX_BYTES + 1)})
        ws.send_json({"type": "input", "text": "small\n"})
        while not any(c[1:3] == ["pane", "send-text"] for c in runner.calls):
            ws.receive_json()
    # Reads keep running concurrently, so compare the send calls themselves.
    sends = [c for c in runner.calls if c[1:3] == ["pane", "send-text"]]
    assert sends == [["herdr", "pane", "send-text", "w2:p4", "small\n"]]


def test_rate_limited_third_rapid_input_is_dropped():
    runner = _counter_runner()
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller,
        HerdrBridge(runner=runner, poll_s=0.01),
        token=FIXED_TOKEN,
        input_per_s=2,
    )
    client = _endpoint_app(endpoint)
    with client.websocket_connect("/ws/tasks/task:one/terminal") as ws:
        _auth(ws)
        ws.receive_json()
        for n in range(3):
            ws.send_json({"type": "input", "text": f"cmd-{n}\n"})
        for _ in range(4):
            ws.receive_json()
    sends = [c for c in runner.calls if c[1:3] == ["pane", "send-text"]]
    assert [c[4] for c in sends] == ["cmd-0\n", "cmd-1\n"]


def test_row_without_pane_yields_no_session_status():
    runner = _counter_runner()
    poller = _poller(
        [{"id": "task:one", "owner": {"kind": "worker", "ref": "not-a-pane"}}]
    )
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=runner, poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with client.websocket_connect("/ws/tasks/task:one/terminal") as ws:
        _auth(ws)
        assert ws.receive_json() == {"type": "status", "status": "no_session"}


def test_pane_closed_status_ends_session():
    async def runner(argv):
        raise PaneClosedError()

    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=runner, poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws/tasks/task:one/terminal") as ws,
    ):
        _auth(ws)
        assert ws.receive_json() == {"type": "status", "status": "pane_closed"}
        ws.receive_json()


def test_fifth_concurrent_session_closes_4429():
    runner = _counter_runner()
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=runner, poll_s=0.05), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    sessions = []
    try:
        for _ in range(4):
            ctx = client.websocket_connect("/ws/tasks/task:one/terminal")
            ws = ctx.__enter__()
            _auth(ws)
            assert ws.receive_json()["type"] == "frame"
            sessions.append(ctx)
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect("/ws/tasks/task:one/terminal") as ws5,
        ):
            _auth(ws5)
            ws5.receive_json()
        assert exc.value.code == 4429
    finally:
        for ctx in sessions:
            ctx.__exit__(None, None, None)


def test_only_changed_frames_cross_the_socket():
    pattern = ["A", "A", "B"]
    state = {"n": 0}

    async def runner(argv):
        text = pattern[state["n"] % len(pattern)]
        state["n"] += 1
        return text

    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=runner, poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with client.websocket_connect("/ws/tasks/task:one/terminal") as ws:
        _auth(ws)
        first = ws.receive_json()
        second = ws.receive_json()
        assert (first["text"], second["text"]) == ("A", "B")
        assert first["updated_at"] != second["updated_at"]


def test_frame_secrets_arrive_redacted_with_escapes_intact():
    async def runner(argv):
        return f"\x1b[31mtoken: {GH_CANARY}\x1b[0m ready"

    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=runner, poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with client.websocket_connect("/ws/tasks/task:one/terminal") as ws:
        _auth(ws)
        frame = ws.receive_json()
    assert frame["text"].startswith("\x1b[31mtoken: [REDACTED]\x1b[0m")
    assert GH_CANARY not in frame["text"]


def _wired_app(**kwargs):
    runner = _counter_runner()
    poller = _poller(
        [
            {"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}},
            {"id": "worker/1", "herdr_pane": {"pane_id": "w2:p5"}},
        ]
    )
    kwargs.setdefault("attach_token", FIXED_TOKEN)
    kwargs.setdefault("terminal_bridge", HerdrBridge(runner=runner, poll_s=0.01))
    app = create_app(poller, port=7441, **kwargs)
    return TestClient(app), app, runner


def _ws(client, path):
    headers = {"host": "127.0.0.1:7441", "origin": "http://127.0.0.1:7441"}
    return client.websocket_connect(path, headers=headers)


def test_api_session_releases_token_to_same_origin_only():
    client, _app, _runner = _wired_app()
    response = client.get("/api/session", headers={"host": "127.0.0.1:7441"})
    assert response.status_code == 200
    assert response.json() == {"token": FIXED_TOKEN, "readonly": False}
    assert response.headers["cache-control"] == "no-store"
    assert (
        client.get(
            "/api/session",
            headers={"host": "127.0.0.1:7441", "sec-fetch-site": "cross-site"},
        ).status_code
        == 403
    )
    for site in ("same-origin", "none"):
        assert (
            client.get(
                "/api/session",
                headers={"host": "127.0.0.1:7441", "sec-fetch-site": site},
            ).status_code
            == 200
        )
    assert (
        client.get("/api/session", headers={"host": "127.0.0.1:7441"}).status_code
        == 200
    )
    assert (
        client.post("/api/session", headers={"host": "127.0.0.1:7441"}).status_code
        == 405
    )
    assert (
        client.get("/api/session", headers={"host": "10.0.0.9:7441"}).status_code == 403
    )


def test_guard_admits_authorized_ws_end_to_end():
    client, _app, _runner = _wired_app()
    with _ws(client, "/ws/tasks/task:one/terminal") as ws:
        _auth(ws)
        assert ws.receive_json()["type"] == "frame"
    with _ws(client, "/ws/tasks/worker%2F1/terminal") as ws:
        _auth(ws)
        assert ws.receive_json()["type"] == "frame"


def test_guard_accepts_localhost_origin_variant_case_insensitive():
    client, _app, _runner = _wired_app()
    with client.websocket_connect(
        "/ws/tasks/task:one/terminal",
        headers={"host": "127.0.0.1:7441", "origin": "HTTP://LocalHost:7441"},
    ) as ws:
        _auth(ws)
        assert ws.receive_json()["type"] == "frame"


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "127.0.0.1:7441", "origin": "http://evil.example"},
        {"host": "127.0.0.1:7441"},
        {"host": "10.0.0.9:7441", "origin": "http://127.0.0.1:7441"},
    ],
)
def test_guard_closes_foreign_ws_4403(headers):
    client, _app, _runner = _wired_app()
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws/tasks/task:one/terminal", headers=headers) as ws,
    ):
        _auth(ws)
        ws.receive_json()
    assert exc.value.code == 4403


def test_minted_tokens_differ_per_app_and_are_strings():
    _counter_runner()
    poller = _poller([{"id": "task:one"}])
    first = create_app(poller, port=7441)
    second = create_app(poller, port=7441)
    client = TestClient(first)
    one = client.get("/api/session", headers={"host": "127.0.0.1:7441"}).json()
    client2 = TestClient(second)
    two = client2.get("/api/session", headers={"host": "127.0.0.1:7441"}).json()
    assert isinstance(one["token"], str) and one["token"]
    assert one["token"] != two["token"]


def test_csp_gains_ws_sources_and_keeps_substrings():
    client, _app, _runner = _wired_app()
    response = client.get("/api/snapshot", headers={"host": "127.0.0.1:7441"})
    csp = response.headers["content-security-policy"]
    assert "ws://127.0.0.1:7441" in csp and "ws://localhost:7441" in csp
    assert "default-src 'self'" in csp and "font-src 'self'" in csp
    assert "connect-src 'self' ws://127.0.0.1:7441 ws://localhost:7441" in csp


def test_default_port_csp_unit_level():
    from omega_prime.dashboard.app import _DashboardGuards

    guards = _DashboardGuards(app=None, port=7421, host="127.0.0.1")
    csp = guards.security_headers["content-security-policy"]
    assert "connect-src 'self' ws://127.0.0.1:7421 ws://localhost:7421" in csp


def test_route_paths_stay_free_of_send_and_input():
    _client, app, _runner = _wired_app()
    paths = [getattr(route, "path", "") for route in app.routes]
    assert "/api/tasks/{task_id:path}/terminal" in paths
    assert "/ws/tasks/{task_id:path}/terminal" in paths
    assert not any("send" in path or "input" in path for path in paths)


def test_server_readonly_flag_forces_rw_connections_readonly():
    runner = _counter_runner()
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    app = create_app(
        poller,
        port=7441,
        attach_token=FIXED_TOKEN,
        terminal_readonly=True,
        terminal_bridge=HerdrBridge(runner=runner, poll_s=0.01),
    )
    client = TestClient(app)
    with _ws(client, "/ws/tasks/task:one/terminal") as ws:
        _auth(ws)
        assert ws.receive_json()["type"] == "frame"
        ws.send_json({"type": "input", "text": "nope\n"})
        assert ws.receive_json()["type"] == "frame"
    assert not any(
        c[1:3] in (["pane", "send-text"], ["pane", "send-keys"]) for c in runner.calls
    )


def _serve_fake(findings):
    def serve(app: Any, host: str, port: int) -> None:
        client = TestClient(app)
        response = client.get("/api/session", headers={"host": "127.0.0.1:7441"})
        payload = response.json()
        findings["readonly"] = payload["readonly"]
        findings["token_is_str"] = isinstance(payload["token"], str) and bool(
            payload["token"]
        )
        headers = {"host": "127.0.0.1:7441", "origin": "http://127.0.0.1:7441"}
        with client.websocket_connect(
            "/ws/tasks/task:one/terminal", headers=headers
        ) as ws:
            ws.send_json({"type": "auth", "token": payload["token"], "mode": "rw"})
            findings["first"] = ws.receive_json()
            ws.send_json({"type": "input", "text": "dropped anyway\n"})
            findings["no_crash"] = True

    return serve


def _readonly_poller_factory():
    def factory():
        poller = DashboardPoller([])
        poller._snapshot = {
            "active": [{"id": "task:one", "owner": {"kind": "worker", "ref": "x"}}],
            "idle_sessions": [],
            "finished": [],
        }
        return poller

    return factory


def test_cli_terminal_readonly_flag_forces_readonly(tmp_path):
    findings: dict[object, object] = {}
    code = main(
        ["--host", "127.0.0.1", "--port", "7441", "--terminal-readonly"],
        home=tmp_path,
        serve=_serve_fake(findings),
        poller_factory=_readonly_poller_factory(),
    )
    assert code == 0
    assert findings["readonly"] is True
    assert findings["token_is_str"] is True
    assert findings["first"] == {"type": "status", "status": "no_session"}
    assert findings["no_crash"] is True
    assert not (tmp_path / ".omega-prime" / "dashboard" / "dashboard.pid").exists()


def test_cli_without_flag_reports_readonly_false(tmp_path):
    findings: dict[object, object] = {}
    code = main(
        ["--host", "127.0.0.1", "--port", "7441"],
        home=tmp_path,
        serve=_serve_fake(findings),
        poller_factory=_readonly_poller_factory(),
    )
    assert code == 0
    assert findings["readonly"] is False
    assert findings["first"] == {"type": "status", "status": "no_session"}


# --- Greptile round 1 regressions -------------------------------------------


def test_input_is_not_sent_to_a_pane_the_task_no_longer_maps():
    runner = _counter_runner()
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=runner, poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with client.websocket_connect("/ws/tasks/task:one/terminal") as ws:
        _auth(ws)
        assert ws.receive_json()["type"] == "frame"
        # The task loses its pane mapping: the writer reports no_session and
        # input must not reach the previously mapped pane.
        poller._snapshot = {
            "active": [{"id": "task:one"}],
            "idle_sessions": [],
            "finished": [],
        }
        while ws.receive_json() != {"type": "status", "status": "no_session"}:
            pass
        ws.send_json({"type": "input", "text": "late\n"})
        ws.send_json({"type": "keys", "keys": ["enter"]})
        # Remap to a new pane: input now goes to the new pane only.
        poller._snapshot = {
            "active": [{"id": "task:one", "herdr_pane": {"pane_id": "w2:p9"}}],
            "idle_sessions": [],
            "finished": [],
        }
        while ws.receive_json().get("type") != "frame":
            pass
        ws.send_json({"type": "input", "text": "fresh\n"})
        while not any(c[1:3] == ["pane", "send-text"] for c in runner.calls):
            ws.receive_json()
    sends = [
        c
        for c in runner.calls
        if c[1:3] in (["pane", "send-text"], ["pane", "send-keys"])
    ]
    assert sends == [["herdr", "pane", "send-text", "w2:p9", "fresh\n"]]


@pytest.mark.parametrize(
    "first_frame",
    [
        "not json at all",
        "[1, 2, 3]",
        json.dumps({"type": "auth", "token": "t\u00f6ken-\u2603", "mode": "rw"}),
    ],
)
def test_malformed_auth_frames_close_4401_cleanly(first_frame):
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=_fake_runner(), poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws/tasks/task:one/terminal") as ws,
    ):
        ws.send_text(first_frame)
        ws.receive_json()
    assert exc.value.code == 4401


def test_binary_auth_frame_closes_4401_cleanly():
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=_fake_runner(), poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws/tasks/task:one/terminal") as ws,
    ):
        ws.send_bytes(b"\x00\x01")
        ws.receive_json()
    assert exc.value.code == 4401


def test_malformed_input_frames_are_ignored_not_fatal():
    runner = _counter_runner()
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    endpoint = make_terminal_ws_endpoint(
        poller, HerdrBridge(runner=runner, poll_s=0.01), token=FIXED_TOKEN
    )
    client = _endpoint_app(endpoint)
    with client.websocket_connect("/ws/tasks/task:one/terminal") as ws:
        _auth(ws)
        ws.receive_json()
        ws.send_text("{broken")
        ws.send_bytes(b"\xff")
        ws.send_json({"type": "input", "text": "ok\n"})
        while not any(c[1:3] == ["pane", "send-text"] for c in runner.calls):
            ws.receive_json()
    sends = [c for c in runner.calls if c[1:3] == ["pane", "send-text"]]
    assert sends == [["herdr", "pane", "send-text", "w2:p4", "ok\n"]]


def test_ipv6_loopback_host_admits_its_own_origin_and_csp():
    from omega_prime.dashboard.app import _DashboardGuards

    guards = _DashboardGuards(app=None, port=7441, host="::1")
    assert "http://[::1]:7441" in guards.allowed_origins
    assert "http://127.0.0.1:7441" in guards.allowed_origins
    assert "http://localhost:7441" in guards.allowed_origins
    assert "ws://[::1]:7441" in guards.security_headers["content-security-policy"]


def test_ipv6_loopback_ws_end_to_end():
    runner = _counter_runner()
    poller = _poller([{"id": "task:one", "herdr_pane": {"pane_id": "w2:p4"}}])
    app = create_app(
        poller,
        port=7441,
        host="::1",
        attach_token=FIXED_TOKEN,
        terminal_bridge=HerdrBridge(runner=runner, poll_s=0.01),
    )
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/tasks/task:one/terminal",
        headers={"host": "[::1]:7441", "origin": "http://[::1]:7441"},
    ) as ws:
        _auth(ws)
        assert ws.receive_json()["type"] == "frame"
