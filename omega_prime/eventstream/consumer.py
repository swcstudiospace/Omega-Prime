"""Bounded asyncio consumer dispatch with owner-token idempotency and drain."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
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
    SchemaError,
)
from omega_prime.eventstream.observability import EventStreamObserver
from omega_prime.eventstream.ports import (
    Ack,
    ClaimResult,
    DeadLetter,
    IdempotencyStore,
    Publisher,
    Retry,
    Subscriber,
)
from omega_prime.eventstream.retry import (
    InvalidRetryMetadata,
    RetryRouter,
    parse_retry_attempt,
)

_LOG = logging.getLogger(__name__)
Handler = Callable[[Delivery, "HandlerContext"], Awaitable[Any]]
#: First wait before re-claiming a ``busy`` delivery; doubles up to the max.
CLAIM_RETRY_INITIAL_DELAY = 0.05
#: Upper bound for one wait between ``busy`` claim attempts, in seconds.
CLAIM_RETRY_MAX_DELAY = 2.0


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
        """Receive until stop/deadline, then drain bounded work and close resources.

        The deadline is watched for the whole run, including the drain after
        ``stop()``: if it fires while handlers are still draining, remaining
        work is cancelled (its claims released, sources left unsettled) and
        the run reports an unclean shutdown instead of waiting forever.
        """
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
                deadline_task = (
                    None
                    if deadline_reached is None
                    else group.create_task(deadline_reached.wait())
                )
                stop_task = group.create_task(self._stopping.wait())
                waiters = (
                    {stop_task}
                    if deadline_task is None
                    else {
                        stop_task,
                        deadline_task,
                    }
                )
                done, _pending = await asyncio.wait(
                    waiters, return_when=asyncio.FIRST_COMPLETED
                )
                if deadline_task is not None and deadline_task in done:
                    clean = False
                self._stopping.set()
                await stop_task
                receiver.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await receiver
                if clean:
                    drain = group.create_task(self._drain(queue, workers))
                    drain_waiters: set[asyncio.Task[Any]] = {drain}
                    if deadline_task is not None:
                        drain_waiters.add(deadline_task)
                    drained, _waiting = await asyncio.wait(
                        drain_waiters, return_when=asyncio.FIRST_COMPLETED
                    )
                    if drain not in drained:
                        # Deadline fired mid-drain: stop waiting on handlers.
                        clean = False
                        drain.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await drain
                if not clean:
                    for worker in workers:
                        worker.cancel()
                if deadline_task is not None and not deadline_task.done():
                    deadline_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await deadline_task
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

    @staticmethod
    async def _drain(
        queue: asyncio.Queue[Delivery | None], workers: list[asyncio.Task[None]]
    ) -> None:
        # Sentinels drain already-received work after receiving stops.
        for _ in workers:
            await queue.put(None)
        await asyncio.wait(workers)

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

    async def _claim_until_settled(
        self, delivery: Delivery, owner: str
    ) -> ClaimResult | None:
        """Claim, retrying ``busy`` with bounded backoff while the runtime is open.

        ``busy`` means SQLite contention or another live owner; both clear on
        their own, so the delivery is retried in place instead of being
        stranded in flight until a restart. Each wait is capped at
        ``CLAIM_RETRY_MAX_DELAY`` and wakes early on ``stop()``; on shutdown
        the delivery is left unsettled so closing the receiver releases it.
        """
        delay = CLAIM_RETRY_INITIAL_DELAY
        while True:
            claim = await self.store.claim(self.subscription, delivery.event.id, owner)
            if claim.status != "busy":
                return claim
            if self._stopping.is_set():
                return None
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=delay)
            if self._stopping.is_set():
                return None
            delay = min(CLAIM_RETRY_MAX_DELAY, delay * 2)

    async def _reject_invalid_metadata(self, delivery: Delivery, reason: str) -> None:
        """Dead-letter a delivery whose retry metadata cannot be trusted.

        Runs before any ledger claim so malformed transport properties never
        reach handler bookkeeping; failures are logged (redacted) and leave the
        source unsettled rather than escaping into the worker TaskGroup.
        """
        try:
            await self.router.handle(
                delivery, SchemaError(reason), attempt=1, latency=0.0
            )
        except Exception as exc:
            _LOG.warning(
                "eventstream invalid retry metadata could not be dead-lettered",
                extra={
                    "ce_id": str(redact_value(delivery.event.id, self.known_secrets)),
                    "ce_type": delivery.event.type,
                    "subscription": str(
                        redact_value(self.subscription, self.known_secrets)
                    )[:64],
                    "error": str(redact_value(str(exc), self.known_secrets))[:256],
                },
            )

    async def _process_locked(self, delivery: Delivery) -> None:
        try:
            attempt = parse_retry_attempt(delivery.properties)
        except InvalidRetryMetadata as exc:
            await self._reject_invalid_metadata(delivery, str(exc))
            return
        owner = uuid.uuid4().hex
        claim = await self._claim_until_settled(delivery, owner)
        if claim is None:
            return
        if claim.status == "already-complete":
            await delivery.acknowledge()
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
            "attempt": attempt,
            "trace_id": trace_id,
        }
        self._active += 1
        if self.observer:
            self.observer.handler_in_flight(self._active)
        started = time.perf_counter()
        try:
            try:
                outcome = await self.handler(delivery, context)
            except AuthenticationError as exc:
                latency = time.perf_counter() - started
                await self.router.handle(delivery, exc, latency=latency)
                await self.store.release(self.subscription, delivery.event.id, token)
                _LOG.warning("eventstream handler authentication failure", extra=fields)
                return
            except (PermanentError, RetryableError) as exc:
                outcome = exc
            except Exception as exc:
                outcome = exc
            if not isinstance(outcome, (Ack, Retry, DeadLetter, BaseException)):
                outcome = Retry(0.0, "handler returned unsupported outcome")
            latency = time.perf_counter() - started

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
                attempt=attempt,
                settle_before_ack=settle_before_ack,
                latency=latency,
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
