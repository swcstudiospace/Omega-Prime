"""Broker-neutral asynchronous ports and handler outcomes.

Durable subscriptions begin at Earliest by default and acknowledge each
Delivery individually. Readers are non-durable: their explicit start position
and seek cursor never create an acknowledgement obligation. Keys are ordering
metadata; ordering is guaranteed only within a key on one topic, never across
topics. Implementations must expose bounded receiver backpressure and must not
own caller-created tasks; closing a receiver releases unsettled work, while
cancellation leaves it available for redelivery.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeAlias, runtime_checkable

from omega_prime.eventstream.config import EventStreamConfig
from omega_prime.eventstream.envelope import (
    Delivery,
    PublishReceipt,
    PublishRequest,
)
from omega_prime.eventstream.errors import EventStreamError

SubscriptionType: TypeAlias = Literal["Exclusive", "Failover", "Shared", "Key_Shared"]
InitialPosition: TypeAlias = Literal["Earliest", "Latest"]


class UnsupportedSubscriptionMode(EventStreamError):
    """A subscription mode cannot satisfy the requested delivery behavior."""


@dataclass(frozen=True, slots=True)
class Ack:
    """Handler outcome requesting individual acknowledgement."""


@dataclass(frozen=True, slots=True)
class Retry:
    """Handler outcome requesting a delayed retry."""

    after: float
    reason: str

    def __post_init__(self) -> None:
        if isinstance(self.after, bool) or not isinstance(self.after, (int, float)):
            raise ValueError("retry delay must be a finite nonnegative number")
        try:
            finite = math.isfinite(self.after)
        except OverflowError:
            finite = False
        if not finite or self.after < 0:
            raise ValueError("retry delay must be a finite nonnegative number")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("retry reason must be a nonempty string")


@dataclass(frozen=True, slots=True)
class DeadLetter:
    """Handler outcome requesting dead-letter routing."""

    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("dead-letter reason must be a nonempty string")


HandlerOutcome: TypeAlias = Ack | Retry | DeadLetter
ClaimStatus: TypeAlias = Literal["acquired", "already-complete", "busy"]


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """Owner-token claim disposition; only acquired grants mutation rights."""

    status: ClaimStatus
    owner_token: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("acquired", "already-complete", "busy"):
            raise ValueError("invalid idempotency claim status")
        if self.status == "acquired" and not self.owner_token:
            raise ValueError("an acquired claim requires an owner token")
        if self.status == "already-complete" and self.owner_token is not None:
            raise ValueError("a completed claim has no owner token")

    @classmethod
    def acquired(cls, owner_token: str) -> ClaimResult:
        return cls("acquired", owner_token)

    @classmethod
    def already_complete(cls) -> ClaimResult:
        return cls("already-complete")

    @classmethod
    def busy(cls, owner_token: str | None = None) -> ClaimResult:
        return cls("busy", owner_token)


@runtime_checkable
class IdempotencyStore(Protocol):
    """Claim-before-effect ledger with owner-token guarded settlement."""

    async def claim(
        self, subscription: str, event_id: str, owner_token: str
    ) -> ClaimResult: ...

    async def complete(
        self,
        subscription: str,
        event_id: str,
        owner_token: str,
        *,
        connection: Any | None = None,
    ) -> bool: ...

    async def release(
        self, subscription: str, event_id: str, owner_token: str
    ) -> bool: ...


@runtime_checkable
class Publisher(Protocol):
    """Publishes validated immutable requests and closes owned resources."""

    async def publish(self, request: PublishRequest) -> PublishReceipt: ...

    async def close(self) -> None: ...


@runtime_checkable
class Subscriber(Protocol):
    """Durable individual-ack receiver with bounded queue/backpressure."""

    async def receive(self) -> Delivery: ...

    async def close(self) -> None: ...


@runtime_checkable
class EventBus(Protocol):
    """Broker-neutral publish, durable subscribe, non-durable read and close."""

    async def publish(self, request: PublishRequest) -> PublishReceipt: ...

    async def subscribe(
        self,
        topic: str,
        subscription: str,
        *,
        subscription_type: SubscriptionType = "Key_Shared",
        initial_position: InitialPosition = "Earliest",
        receiver_queue_size: int = 1000,
    ) -> Subscriber: ...

    async def read(self, topic: str, *, start_at: int = 0) -> Reader: ...

    async def close(self) -> None: ...


@runtime_checkable
class Reader(Protocol):
    """Non-durable event reader with explicit integer seek/start position."""

    async def read(self) -> Delivery: ...

    async def read_nowait(self) -> Delivery | None: ...

    async def seek(self, position: int) -> None: ...

    async def close(self) -> None: ...


@runtime_checkable
class AdminPort(Protocol):
    """Broker-independent topology administration surface."""

    async def configure(self, config: EventStreamConfig) -> None: ...

    async def ensure_topic(self, topic: str, *, partitions: int = 1) -> None: ...

    async def close(self) -> None: ...


__all__ = [
    "Ack",
    "AdminPort",
    "ClaimResult",
    "ClaimStatus",
    "DeadLetter",
    "EventBus",
    "HandlerOutcome",
    "IdempotencyStore",
    "InitialPosition",
    "Publisher",
    "Reader",
    "Retry",
    "Subscriber",
    "SubscriptionType",
    "UnsupportedSubscriptionMode",
]
