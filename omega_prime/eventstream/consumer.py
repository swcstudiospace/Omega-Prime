"""Bounded asyncio consumer dispatch with owner-token idempotency and drain."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from omega_prime.credentials.redact import redact_value
from omega_prime.eventstream.envelope import Delivery
from omega_prime.eventstream.errors import (
    AuthenticationError,
    PermanentError,
    RetryableError,
)
from omega_prime.eventstream.observability import EventStreamObserver
from omega_prime.eventstream.ports import (
    Ack,
    DeadLetter,
    IdempotencyStore,
    Publisher,
    Retry,
    Subscriber,
)
from omega_prime.eventstream.retry import RetryRouter

_LOG = logging.getLogger(__name__)
Handler = Callable[[Delivery, "HandlerContext"], Awaitable[Any]]


@dataclass(slots=True)
class HandlerContext:
    """Owner-scoped completion operations for a single acquired delivery."""

    subscription: str
    event_id: str
    owner_token: str
    store: IdempotencyStore
    _completion_succeeded: bool = False

    async def complete(self, connection: Any) -> bool:
        """Complete this claim within the caller's transaction.

        The caller MUST commit that transaction before returning an Ack outcome.
        A failed owner compare-and-set leaves the source delivery unsettled.
        """
        completed = await self.store.complete(
            self.subscription, self.event_id, self.owner_token, connection=connection
        )
        if completed:
            self._completion_succeeded = True
        return completed

    @property
    def completion_succeeded(self) -> bool:
        """Whether this context's owner-guarded completion succeeded."""
        return self._completion_succeeded


@dataclass(slots=True)
class _KeyLockEntry:
    lock: asyncio.Lock
    users: int = 0


@dataclass(frozen=True, slots=True)
class ShutdownResult:
    clean: bool
    completed: int


class ConsumerRuntime:
    """Consume one durable subscription with fixed worker and queue bounds."""

    def __init__(
        self,
        subscriber: Subscriber,
        publisher: Publisher,
        store: IdempotencyStore,
        subscription: str,
        handler: Handler,
        *,
        observer: EventStreamObserver | None = None,
        on_authentication_failure: Callable[[], Awaitable[None]] | None = None,
        max_in_flight: int = 16,
        receiver_queue_size: int | None = None,
        target: str | None = None,
        subscription_type: str = "Key_Shared",
        known_secrets: tuple[str, ...] = (),
        close_consumers: tuple[Subscriber, ...] = (),
        flush_producers: tuple[Any, ...] = (),
        final_relay: Callable[[], Awaitable[None]] | None = None,
        close_client: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if (
            isinstance(max_in_flight, bool)
            or not isinstance(max_in_flight, int)
            or max_in_flight < 1
        ):
            raise ValueError("max_in_flight must be a positive integer")
        self.subscriber = subscriber
        self.publisher = publisher
        self.store = store
        self.subscription = subscription
        self.handler = handler
        self.observer = observer
        self.max_in_flight = max_in_flight
        self.queue_size = (
            max_in_flight if receiver_queue_size is None else receiver_queue_size
        )
        if (
            isinstance(self.queue_size, bool)
            or not isinstance(self.queue_size, int)
            or self.queue_size < 1
        ):
            raise ValueError("receiver_queue_size must be a positive integer")
        self.target = subscription if target is None else target
        self.known_secrets = known_secrets
        self.close_consumers = close_consumers or (subscriber,)
        self.flush_producers = flush_producers
        self.final_relay = final_relay
        self.close_client = close_client
        self.router = RetryRouter(
            publisher,
            subscription,
            observer=observer,
            known_secrets=known_secrets,
            subscription_type=subscription_type,
            on_authentication_failure=on_authentication_failure,
        )
        self._stopping = asyncio.Event()
        self._active = 0
        self._key_locks: dict[str, _KeyLockEntry] = {}
        self._key_lock_guard = asyncio.Lock()
        self._completed = 0
        self._slots = asyncio.Semaphore(max_in_flight)
        self._cleanup_task: asyncio.Task[None] | None = None

    async def run(
        self, *, deadline_reached: asyncio.Event | None = None
    ) -> ShutdownResult:
        """Receive until stop/deadline, then drain bounded work and close resources."""
        queue: asyncio.Queue[Delivery | None] = asyncio.Queue(self.queue_size)
        clean = True
        try:
            async with asyncio.TaskGroup() as group:
                receiver = group.create_task(
                    self._receive(queue), name=f"{self.subscription}:receiver"
                )
                workers = [
                    group.create_task(
                        self._worker(queue), name=f"{self.subscription}:worker:{i}"
                    )
                    for i in range(self.max_in_flight)
                ]
                if deadline_reached is None:
                    await self._stopping.wait()
                else:
                    stop_task = group.create_task(self._stopping.wait())
                    deadline_task = group.create_task(deadline_reached.wait())
                    done, pending = await asyncio.wait(
                        (stop_task, deadline_task), return_when=asyncio.FIRST_COMPLETED
                    )
                    clean = deadline_task not in done
                    for pending_task in pending:
                        pending_task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    if not clean:
                        self._stopping.set()
                receiver.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await receiver
                if not clean:
                    for worker in workers:
                        worker.cancel()
                else:
                    # Sentinels drain already-received work after receiving stops.
                    for _ in workers:
                        await queue.put(None)
                results = await asyncio.gather(*workers, return_exceptions=True)
                if any(
                    isinstance(result, BaseException)
                    and not isinstance(result, asyncio.CancelledError)
                    for result in results
                ):
                    clean = False
        finally:
            await self._close_once()
        return ShutdownResult(clean, self._completed)

    async def _close_once(self) -> None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._close_ordered())
        try:
            await asyncio.shield(self._cleanup_task)
        except asyncio.CancelledError:
            await self._cleanup_task
            raise

    def stop(self) -> None:
        self._stopping.set()

    async def _receive(self, queue: asyncio.Queue[Delivery | None]) -> None:
        while not self._stopping.is_set():
            await self._slots.acquire()
            try:
                delivery = await self.subscriber.receive()
                await queue.put(delivery)
            except BaseException:
                self._slots.release()
                raise

    async def _worker(self, queue: asyncio.Queue[Delivery | None]) -> None:

        while True:
            delivery = await queue.get()
            if delivery is None:
                queue.task_done()
                return
            try:
                await self._process(delivery)
            finally:
                self._slots.release()
                queue.task_done()

    async def _process(self, delivery: Delivery) -> None:
        props = delivery.properties
        # Retry handoff is filtered before ledger claim, so irrelevant deliveries
        # cannot occupy a key or suppress the eligible handler.
        retry_target = props.get("omega_retry_target")
        if retry_target is not None and retry_target != self.target:
            await delivery.acknowledge()
            return
        async with self._key_lock_guard:
            entry = self._key_locks.get(delivery.key)
            if entry is None:
                entry = _KeyLockEntry(asyncio.Lock())
                self._key_locks[delivery.key] = entry
            entry.users += 1
        try:
            async with entry.lock:
                await self._process_locked(delivery)
        finally:
            async with self._key_lock_guard:
                entry.users -= 1
                if entry.users == 0 and self._key_locks.get(delivery.key) is entry:
                    del self._key_locks[delivery.key]

    async def _process_locked(self, delivery: Delivery) -> None:
        props = delivery.properties
        owner = uuid.uuid4().hex
        claim = await self.store.claim(self.subscription, delivery.event.id, owner)
        if claim.status == "already-complete":
            await delivery.acknowledge()
            return
        if claim.status == "busy":
            return
        token = claim.owner_token
        assert token is not None
        context = HandlerContext(
            self.subscription, delivery.event.id, token, self.store
        )
        traceparent = (
            delivery.event.model_extra.get("traceparent")
            if delivery.event.model_extra
            else None
        )
        trace_id = str(traceparent).split("-")[1] if traceparent else ""
        fields = {
            "ce_id": str(redact_value(delivery.event.id, self.known_secrets)),
            "ce_type": delivery.event.type,
            "subscription": str(redact_value(self.subscription, self.known_secrets))[
                :64
            ],
            "attempt": int(props.get("omega_attempt", 1)),
            "trace_id": trace_id,
        }
        self._active += 1
        if self.observer:
            self.observer.handler_in_flight(self._active)
        try:
            try:
                outcome = await self.handler(delivery, context)
            except AuthenticationError as exc:
                await self.router.handle(delivery, exc)
                await self.store.release(self.subscription, delivery.event.id, token)
                _LOG.warning("eventstream handler authentication failure", extra=fields)
                return
            except (PermanentError, RetryableError) as exc:
                outcome = exc
            except Exception as exc:
                outcome = exc
            if not isinstance(outcome, (Ack, Retry, DeadLetter, BaseException)):
                outcome = Retry(0.0, "handler returned unsupported outcome")

            async def settle_before_ack(action: str) -> bool:
                if action == "ack":
                    if context.completion_succeeded:
                        return True
                    return await self.store.complete(
                        self.subscription, delivery.event.id, token
                    )
                if action in {"retry", "dlq"}:
                    return await self.store.release(
                        self.subscription, delivery.event.id, token
                    )
                return False

            action = await self.router.handle(
                delivery,
                outcome,
                attempt=int(props.get("omega_attempt", 1)),
                settle_before_ack=settle_before_ack,
            )
            if action == "unsettled":
                await self.store.release(self.subscription, delivery.event.id, token)
        except asyncio.CancelledError:
            # Release only this acquired owner; leave the durable source unsettled.
            await asyncio.shield(
                self.store.release(self.subscription, delivery.event.id, token)
            )
            raise
        except Exception as exc:
            safe = redact_value(str(exc), self.known_secrets)
            _LOG.warning(
                "eventstream handler did not settle",
                extra={**fields, "error": str(safe)[:256]},
            )
            await self.store.release(self.subscription, delivery.event.id, token)
        finally:
            self._active -= 1
            self._completed += 1
            if self.observer:
                self.observer.handler_in_flight(self._active)

    async def _close_ordered(self) -> None:
        for consumer in self.close_consumers:
            await consumer.close()
        for producer in self.flush_producers:
            flush = getattr(producer, "flush", None)
            if flush is not None:
                result = flush()
                if hasattr(result, "__await__"):
                    await result
        if self.final_relay is not None:
            await self.final_relay()
        await self.publisher.close()
        if self.close_client is not None:
            await self.close_client()


__all__ = ["ConsumerRuntime", "HandlerContext", "ShutdownResult"]
