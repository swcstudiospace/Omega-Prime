# SPDX-License-Identifier: AGPL-3.0-only
"""Terminal websocket bridge, protocol and app-wiring tests (fakes only)."""
import asyncio
import json
from datetime import UTC, datetime, timedelta

from omega_prime.dashboard.terminal import PaneClosedError
from omega_prime.dashboard.terminal_ws import (
    TERMINAL_WS_LIMIT,
    HerdrBridge,
    redact_ansi_frame,
)

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


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
        "\x1b[31mBearer abcdefghijkl\x1b[0m ghp_abcdefgh123456 "
        "sk-abcdefgh123456 aaaaaaaa.bbbbbbbb.cccccccc api_key='topsecret'\n"
        "ok\x1b[2Jline"
    )
    out = redact_ansi_frame(frame)
    assert "\x1b[31m" in out and "\x1b[0m" in out and "\x1b[2J" in out
    assert "abcdefghijkl" not in out
    assert "ghp_abcdefgh123456" not in out
    assert "sk-abcdefgh123456" not in out
    assert "aaaaaaaa.bbbbbbbb.cccccccc" not in out
    assert "topsecret" not in out
    assert "[REDACTED]" in out
    assert "\n" in out


def test_redact_ansi_frame_drops_control_chars_keeps_newline_tab():
    out = redact_ansi_frame("a\x85b\nc\td")
    assert "\x85" not in out
    assert "a" in out and "b" in out
    assert "\n" in out and "\t" in out


def test_read_frame_uses_visible_ansi_argv_and_redacts():
    runner = _fake_runner(reads=["\x1b[31mGITHUB_TOKEN=abc123def\x1b[0m"])
    bridge = HerdrBridge(runner=runner)
    frame = asyncio.run(bridge.read_frame("w2:p4"))
    assert runner.calls == [["herdr", "pane", "read", "w2:p4", "--source", "visible", "--format", "ansi"]]
    assert "\x1b[31m" in frame and "\x1b[0m" in frame
    assert "abc123def" not in frame


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
    clock = lambda: NOW + timedelta(seconds=next(seconds))
    bridge = HerdrBridge(runner=runner, clock=clock, sleep=lambda _s: asyncio.sleep(0))
    events = _collect(bridge.poll_events(lambda: "w2:p4"), 2)
    assert [e["type"] for e in events] == ["frame", "frame"]
    assert events[0]["text"] == "A" and events[1]["text"] == "B"
    assert events[0]["updated_at"] != events[1]["updated_at"]


def test_poll_events_no_session_then_frame_on_return():
    runner = _fake_runner(reads=["A"])
    bridge = HerdrBridge(runner=runner, clock=lambda: NOW, sleep=lambda _s: asyncio.sleep(0))
    provider = {"pane": None}

    def provide():
        return provider["pane"]

    async def run():
        gen = bridge.poll_events(provide)
        first = await gen.__anext__()
        provider["pane"] = "w2:p4"
        second = await gen.__anext__()
        return [first, second]

    events = asyncio.run(run())
    assert events[0] == {"type": "status", "status": "no_session"}
    assert events[1]["type"] == "frame" and events[1]["text"] == "A"


def test_poll_events_pane_switch_reyields_first_frame():
    runner = _fake_runner(reads=["A", "A"])
    bridge = HerdrBridge(runner=runner, clock=lambda: NOW, sleep=lambda _s: asyncio.sleep(0))
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

    bridge = HerdrBridge(runner=runner, clock=lambda: NOW, sleep=lambda _s: asyncio.sleep(0))

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

    bridge = HerdrBridge(runner=runner, clock=lambda: NOW, sleep=lambda _s: asyncio.sleep(0))

    async def run():
        out = []
        gen = bridge.poll_events(lambda: "w2:p4")
        async for event in gen:
            out.append(event)
            if len(out) >= 2:
                break
        return out

    events = asyncio.run(run())
    assert events[0] == {"type": "status", "status": "unavailable"}
    assert events[1]["type"] == "frame" and events[1]["text"] == "A"


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
