"""Deterministic in-process EventBus using only standard-library primitives."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from omega_prime.eventstream.config import EventStreamConfig
from omega_prime.eventstream.envelope import Delivery, PublishReceipt, PublishRequest
from omega_prime.eventstream.ports import (
    InitialPosition,
    SubscriptionType,
    UnsupportedSubscriptionMode,
)


class ManualClock:
    """Explicitly advanced UTC clock that wakes registered async receivers."""

    def __init__(self, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock time must be timezone-aware")
        self._now = now
        self._wakeups: list[asyncio.Event] = []

    @property
    def now(self) -> datetime:
        return self._now

    def register_wakeup(self, event: asyncio.Event) -> None:
        if event not in self._wakeups:
            self._wakeups.append(event)

    def unregister_wakeup(self, event: asyncio.Event) -> None:
        with suppress(ValueError):
            self._wakeups.remove(event)

    def advance(self, amount: timedelta) -> None:
        if amount < timedelta(0):
            raise ValueError("clock cannot move backwards")
        self._now += amount
        for event in self._wakeups:
            event.set()


def _delivery_properties(request: PublishRequest) -> dict[str, Any]:
    """Combine publish properties with authoritative CloudEvents attributes."""
    properties = dict(request.properties)
    properties.update(request.event.to_properties())
    return properties


@dataclass(frozen=True, slots=True)
class _Message:
    request: PublishRequest
    message_id: str


@dataclass(slots=True)
class _SubscriptionState:
    start_at: int
    acknowledged: set[str] = field(default_factory=set)
    in_flight: dict[str, str] = field(default_factory=dict)
    delivered: set[str] = field(default_factory=set)


class MemoryEventBus:
    """Real broker-free publish/subscribe/read behavior with durable local state.

    The bus owns messages and subscription acknowledgement state for its lifetime.
    It creates no background tasks; caller tasks own every receive operation.
    """

    def __init__(self, *, clock: ManualClock) -> None:
        self.clock = clock
        self._topics: dict[str, list[_Message]] = {}
        self._subscriptions: dict[tuple[str, str], _SubscriptionState] = {}
        self._waiters: set[asyncio.Event] = set()
        self._closed = False
        self._acked_messages: set[tuple[str, str]] = set()

    def publisher(self) -> MemoryPublisher:
        self._ensure_open()
        return MemoryPublisher(self)

    async def publish(self, request: PublishRequest) -> PublishReceipt:
        self._ensure_open()
        topic_messages = self._topics.setdefault(request.topic, [])
        ordinal = len(topic_messages)
        message_id = f"memory:{request.topic}:{ordinal}"
        topic_messages.append(_Message(request=request, message_id=message_id))
        self._notify()
        return PublishReceipt(
            event_id=request.event.id,
            topic=request.topic,
            message_id=message_id,
            acknowledged_at=self.clock.now,
        )

    async def subscribe(
        self,
        topic: str,
        subscription: str,
        *,
        subscription_type: SubscriptionType = "Key_Shared",
        initial_position: InitialPosition = "Earliest",
        receiver_queue_size: int = 1000,
    ) -> MemorySubscriber:
        self._ensure_open()
        if subscription_type not in (
            "Exclusive",
            "Failover",
            "Shared",
            "Key_Shared",
        ):
            raise ValueError("unsupported subscription type")
        if not topic.strip() or not subscription.strip():
            raise ValueError("topic and subscription must be nonempty")
        if receiver_queue_size <= 0:
            raise ValueError("receiver_queue_size must be positive")
        if initial_position not in ("Earliest", "Latest"):
            raise ValueError("initial_position must be Earliest or Latest")
        key = (topic, subscription)
        state = self._subscriptions.get(key)
        if state is None:
            start_at = (
                0
                if initial_position == "Earliest"
                else len(self._topics.get(topic, ()))
            )
            state = _SubscriptionState(start_at=start_at)
            self._subscriptions[key] = state
        return MemorySubscriber(
            bus=self,
            topic=topic,
            subscription=subscription,
            subscription_type=subscription_type,
            receiver_queue_size=receiver_queue_size,
            state=state,
        )

    async def read(self, topic: str, *, start_at: int = 0) -> MemoryReader:
        self._ensure_open()
        if start_at < 0:
            raise ValueError("reader start position must be nonnegative")
        return MemoryReader(self, topic, start_at)

    def acknowledged(self, subscription: str, message_id: str) -> bool:
        return (subscription, message_id) in self._acked_messages

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for state in self._subscriptions.values():
            state.in_flight.clear()
        self._notify()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("memory event bus is closed")

    def _register_waiter(self) -> asyncio.Event:
        event = asyncio.Event()
        self._waiters.add(event)
        self.clock.register_wakeup(event)
        return event

    def _unregister_waiter(self, event: asyncio.Event) -> None:
        self._waiters.discard(event)
        self.clock.unregister_wakeup(event)

    def _notify(self) -> None:
        for event in self._waiters:
            event.set()


class MemoryPublisher:
    def __init__(self, bus: MemoryEventBus) -> None:
        self._bus = bus
        self._closed = False

    async def publish(self, request: PublishRequest) -> PublishReceipt:
        if self._closed:
            raise RuntimeError("memory publisher is closed")
        return await self._bus.publish(request)

    async def close(self) -> None:
        self._closed = True


class MemorySubscriber:
    def __init__(
        self,
        *,
        bus: MemoryEventBus,
        topic: str,
        subscription: str,
        subscription_type: SubscriptionType,
        receiver_queue_size: int,
        state: _SubscriptionState,
    ) -> None:
        self._bus = bus
        self.topic = topic
        self.subscription = subscription
        self.subscription_type = subscription_type
        self.receiver_queue_size = receiver_queue_size
        self._state = state
        self._closed = False
        self._owned_inflight: set[str] = set()

    async def receive(self) -> Delivery:
        self._ensure_open()
        event = self._bus._register_waiter()
        try:
            while True:
                self._ensure_open()
                event.clear()
                if len(self._state.in_flight) < self.receiver_queue_size:
                    message = self._next_message()
                    if message is not None:
                        self._state.in_flight[message.message_id] = message.request.key
                        self._owned_inflight.add(message.message_id)
                        redelivered = message.message_id in self._state.delivered
                        self._state.delivered.add(message.message_id)
                        return self._delivery(message, redelivered=redelivered)
                await event.wait()
        finally:
            self._bus._unregister_waiter(event)

    def _next_message(self) -> _Message | None:
        messages = self._bus._topics.get(self.topic, ())
        for index, message in enumerate(messages):
            message_id = message.message_id
            if index < self._state.start_at or message_id in self._state.acknowledged:
                continue
            if message_id in self._state.in_flight:
                continue
            prior_same_key_unsettled = any(
                messages[prior].request.key == message.request.key
                and messages[prior].message_id not in self._state.acknowledged
                for prior in range(self._state.start_at, index)
            )
            if prior_same_key_unsettled:
                continue
            available_at = message.request.deliver_after
            if available_at is not None and self.subscription_type not in (
                "Shared",
                "Key_Shared",
            ):
                raise UnsupportedSubscriptionMode(
                    f"subscription mode {self.subscription_type} does not support delayed delivery"
                )
            if available_at is not None and available_at > self._bus.clock.now:
                continue
            return message
        return None

    def _delivery(self, message: _Message, *, redelivered: bool) -> Delivery:
        async def acknowledge() -> None:
            if message.message_id in self._state.acknowledged:
                return
            if (
                message.message_id not in self._owned_inflight
                or message.message_id not in self._state.in_flight
            ):
                return
            self._state.acknowledged.add(message.message_id)
            self._state.in_flight.pop(message.message_id, None)
            self._owned_inflight.discard(message.message_id)
            self._bus._acked_messages.add((self.subscription, message.message_id))
            self._bus._notify()

        return Delivery(
            event=message.request.event,
            topic=message.request.topic,
            key=message.request.key,
            message_id=message.message_id,
            redelivered=redelivered,
            properties=_delivery_properties(message.request),
            ack=acknowledge,
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for message_id in self._owned_inflight:
            self._state.in_flight.pop(message_id, None)
        self._owned_inflight.clear()
        self._bus._notify()

    def _ensure_open(self) -> None:
        if self._closed or self._bus._closed:
            raise RuntimeError("memory subscriber is closed")


class MemoryReader:
    def __init__(self, bus: MemoryEventBus, topic: str, position: int) -> None:
        self._bus = bus
        self.topic = topic
        self._position = position
        self._closed = False

    async def read(self) -> Delivery:
        self._ensure_open()
        event = self._bus._register_waiter()
        try:
            while True:
                self._ensure_open()
                event.clear()
                delivery = self._read_next()
                if delivery is not None:
                    return delivery
                await event.wait()
        finally:
            self._bus._unregister_waiter(event)

    async def read_nowait(self) -> Delivery | None:
        self._ensure_open()
        return self._read_next()

    def _read_next(self) -> Delivery | None:
        messages = self._bus._topics.get(self.topic, ())
        if self._position >= len(messages):
            return None
        message = messages[self._position]
        if message.request.deliver_after is not None:
            raise UnsupportedSubscriptionMode(
                "Reader does not support delayed delivery"
            )
        self._position += 1

        async def no_ack() -> None:
            return None

        return Delivery(
            event=message.request.event,
            topic=message.request.topic,
            key=message.request.key,
            message_id=message.message_id,
            properties=_delivery_properties(message.request),
            ack=no_ack,
        )

    async def seek(self, position: int) -> None:
        self._ensure_open()
        if position < 0:
            raise ValueError("reader position must be nonnegative")
        self._position = position

    async def close(self) -> None:
        self._closed = True
        self._bus._notify()

    def _ensure_open(self) -> None:
        if self._closed or self._bus._closed:
            raise RuntimeError("memory reader is closed")


class MemoryAdminPort:
    """Minimal in-memory admin port; topic creation is lazy in the bus."""

    def __init__(
        self, bus: MemoryEventBus, config: EventStreamConfig | None = None
    ) -> None:
        self._bus = bus
        self.config = config
        self._closed = False

    async def configure(self, config: EventStreamConfig) -> None:
        if self._closed:
            raise RuntimeError("memory admin port is closed")
        self.config = config

    async def ensure_topic(self, topic: str, *, partitions: int = 1) -> None:
        if self._closed:
            raise RuntimeError("memory admin port is closed")
        if not topic.strip() or partitions <= 0:
            raise ValueError("topic and positive partitions are required")
        self._bus._ensure_open()
        self._bus._topics.setdefault(topic, [])

    async def close(self) -> None:
        self._closed = True
