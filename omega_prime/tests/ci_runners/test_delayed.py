"""One-shot delayed events: asyncio call_later and Pulsar deliverAfter."""

from __future__ import annotations

import asyncio

import pytest

from omega_prime.ci_runners.delayed import (
    DEADLINE_TYPE,
    AsyncioDelayedEvents,
    PulsarDelayedEvents,
)
from omega_prime.ci_runners.jobs import IdleDeadline


def test_asyncio_fires_once_and_cancels() -> None:
    fired: list[IdleDeadline] = []

    async def scenario() -> None:
        delayed = AsyncioDelayedEvents(fired.append)
        ev = IdleDeadline("omega-ci-1-a", "g1")
        delayed.schedule("omega-ci-1-a", "g1", 0.01, ev)
        handle = delayed.schedule(
            "omega-ci-2-b", "g2", 0.01, IdleDeadline("omega-ci-2-b", "g2")
        )
        assert delayed.pending() == 2
        assert handle.cancel() and not handle.cancel()
        await asyncio.sleep(0.05)  # test-only wait; production code never sleeps
        assert delayed.pending() == 0

    asyncio.run(scenario())
    assert fired == [IdleDeadline("omega-ci-1-a", "g1")]


def test_asyncio_reschedule_replaces_and_cancel_all() -> None:
    fired: list[IdleDeadline] = []

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        delayed = AsyncioDelayedEvents(fired.append, loop)
        ev = IdleDeadline("n", "g")
        delayed.schedule("n", "g", 0.01, ev)
        delayed.schedule("n", "g", 0.01, ev)
        assert delayed.pending() == 1
        delayed.cancel_all()
        await asyncio.sleep(0.03)

    asyncio.run(scenario())
    assert fired == []


@pytest.mark.parametrize(
    ("name", "gen", "delay", "event"),
    [
        ("", "g", 1, IdleDeadline("", "g")),
        ("n", "g", 1, IdleDeadline("n", "other")),
        ("n", "g", float("inf"), IdleDeadline("n", "g")),
        ("n", "g", -1, IdleDeadline("n", "g")),
        ("n", "g", "5", IdleDeadline("n", "g")),
    ],
)
def test_schedule_validation(name, gen, delay, event) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        PulsarDelayedEvents(lambda *_: None, lambda _: None).schedule(
            name, gen, delay, event
        )


def test_pulsar_delivery_gating() -> None:
    sent: list[tuple[dict[str, str], float]] = []
    fired: list[IdleDeadline] = []
    delayed = PulsarDelayedEvents(lambda p, d: sent.append((dict(p), d)), fired.append)
    delayed.schedule("n", "g1", 30, IdleDeadline("n", "g1"))
    handle = delayed.schedule("m", "g2", 30, IdleDeadline("m", "g2"))
    assert sent[0] == ({"type": DEADLINE_TYPE, "name": "n", "generation": "g1"}, 30.0)
    assert handle.cancel() and not delayed.cancel("m", "g2")
    assert delayed.pending() == 1
    assert not delayed.on_delivery(sent[1][0])  # cancelled
    assert not delayed.on_delivery({"type": "other"})
    assert not delayed.on_delivery({"type": DEADLINE_TYPE, "name": 1})
    assert delayed.on_delivery(sent[0][0])
    assert not delayed.on_delivery(sent[0][0])  # one-shot
    assert fired == [IdleDeadline("n", "g1")]
    delayed.schedule("x", "g", 1, IdleDeadline("x", "g"))
    delayed.cancel_all()
    assert delayed.pending() == 0
