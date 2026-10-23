"""One-shot delayed events (the only way anything happens "later").

No timers, cron, or sleep loops: an idle (or launch-backoff retry) deadline
is a single cancellable ``loop.call_later`` handle, or a Pulsar message sent
with ``deliverAfter`` once the mesh is live. Keys are ``(name, generation)`` where ``generation`` is
an opaque nonce minted per arm, unique across controller restarts.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from omega_prime.ci_runners.jobs import IdleDeadline, RetryDeadline

Deadline = IdleDeadline | RetryDeadline
Consumer = Callable[[Deadline], None]
Key = tuple[str, str]
DEADLINE_TYPE = "omega.ci.runner.idle_deadline"
RETRY_DEADLINE_TYPE = "omega.ci.runner.retry_deadline"
_TYPES: dict[str, type[IdleDeadline] | type[RetryDeadline]] = {
    DEADLINE_TYPE: IdleDeadline,
    RETRY_DEADLINE_TYPE: RetryDeadline,
}


class CancelHandle(Protocol):
    def cancel(self) -> bool: ...


class DelayedEvents(Protocol):
    def schedule(
        self, name: str, generation: str, delay_s: float, event: Deadline
    ) -> CancelHandle: ...

    def cancel(self, name: str, generation: str) -> bool: ...

    def cancel_all(self) -> None: ...


class _Handle:
    def __init__(self, owner: DelayedEvents, key: Key) -> None:
        self._owner = owner
        self._key = key

    def cancel(self) -> bool:
        return self._owner.cancel(*self._key)


def _check(name: str, generation: str, delay_s: float, event: Deadline) -> None:
    if not name or not generation:
        raise ValueError("name and generation are required")
    if (event.name, event.generation) != (name, generation):
        raise ValueError("event identity must match the schedule key")
    if not isinstance(delay_s, (int, float)) or not math.isfinite(delay_s):
        raise ValueError("delay must be a finite number")
    if delay_s < 0:
        raise ValueError("delay must not be negative")


class AsyncioDelayedEvents:
    """``loop.call_later`` one-shots; expiry hands the event to ``consumer``."""

    def __init__(
        self, consumer: Consumer, loop: asyncio.AbstractEventLoop | None = None
    ) -> None:
        self._consumer = consumer
        self._loop = loop
        self._handles: dict[Key, asyncio.TimerHandle] = {}

    def pending(self) -> int:
        return len(self._handles)

    def schedule(
        self, name: str, generation: str, delay_s: float, event: Deadline
    ) -> CancelHandle:
        _check(name, generation, delay_s, event)
        key = (name, generation)
        self.cancel(name, generation)
        loop = self._loop or asyncio.get_running_loop()
        self._handles[key] = loop.call_later(delay_s, self._fire, key, event)
        return _Handle(self, key)

    def _fire(self, key: Key, event: Deadline) -> None:
        if self._handles.pop(key, None) is not None:
            self._consumer(event)

    def cancel(self, name: str, generation: str) -> bool:
        handle = self._handles.pop((name, generation), None)
        if handle is None:
            return False
        handle.cancel()
        return True

    def cancel_all(self) -> None:
        for handle in self._handles.values():
            handle.cancel()
        self._handles.clear()


DeliverAfter = Callable[[Mapping[str, str], float], Any]


class PulsarDelayedEvents:
    """Delegates to an injected ``deliver_after(payload, delay_s)`` capability.

    A Pulsar ``deliverAfter`` message cannot be recalled, so cancellation is
    local: the consumer side calls :meth:`on_delivery`, which drops any
    delivery whose key was cancelled or never armed here.
    """

    def __init__(self, deliver_after: DeliverAfter, consumer: Consumer) -> None:
        self._deliver_after = deliver_after
        self._consumer = consumer
        self._live: set[Key] = set()

    def pending(self) -> int:
        return len(self._live)

    def schedule(
        self, name: str, generation: str, delay_s: float, event: Deadline
    ) -> CancelHandle:
        _check(name, generation, delay_s, event)
        key = (name, generation)
        self._live.add(key)
        kind = (
            RETRY_DEADLINE_TYPE if isinstance(event, RetryDeadline) else DEADLINE_TYPE
        )
        payload = {"type": kind, "name": name, "generation": generation}
        self._deliver_after(payload, float(delay_s))
        return _Handle(self, key)

    def cancel(self, name: str, generation: str) -> bool:
        key = (name, generation)
        if key in self._live:
            self._live.discard(key)
            return True
        return False

    def cancel_all(self) -> None:
        self._live.clear()

    def on_delivery(self, payload: Mapping[str, Any]) -> bool:
        """Feed a delivered message. Returns True if it reached the consumer."""
        factory = _TYPES.get(str(payload.get("type")))
        if factory is None:
            return False
        name, generation = payload.get("name"), payload.get("generation")
        if not isinstance(name, str) or not isinstance(generation, str):
            return False
        key = (name, generation)
        if key not in self._live:
            return False
        self._live.discard(key)
        self._consumer(factory(name, generation))
        return True


__all__ = [
    "DEADLINE_TYPE",
    "RETRY_DEADLINE_TYPE",
    "AsyncioDelayedEvents",
    "CancelHandle",
    "Deadline",
    "DelayedEvents",
    "PulsarDelayedEvents",
]
