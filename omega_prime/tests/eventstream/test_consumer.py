from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal, cast

import pytest

from omega_prime.eventstream.consumer import ConsumerRuntime, HandlerContext
from omega_prime.eventstream.envelope import (
    CloudEvent,
    Delivery,
    PublishReceipt,
    PublishRequest,
)
from omega_prime.eventstream.errors import AuthenticationError
from omega_prime.eventstream.idempotency import SQLiteIdempotencyStore
from omega_prime.eventstream.observability import EventStreamObserver
from omega_prime.eventstream.ports import Ack, ClaimResult, DeadLetter, Retry
from omega_prime.eventstream.taxonomy import schema_uri_for_type


class Subscriber:
    def __init__(self, deliveries: list[Delivery]) -> None:
        self.deliveries = deliveries
        self.closed = False

    async def receive(self) -> Delivery:
        while not self.deliveries:
            await asyncio.Event().wait()
        return self.deliveries.pop(0)

    async def close(self) -> None:
        self.closed = True


class Publisher:
    def __init__(self) -> None:
        self.closed = False

    async def publish(self, request: PublishRequest) -> PublishReceipt:
        return PublishReceipt(
            event_id=request.event.id,
            topic=request.topic,
            message_id="m",
            acknowledged_at=datetime.now(UTC),
        )

    async def close(self) -> None:
        self.closed = True


class Store:
    def __init__(
        self, status: Literal["acquired", "already-complete", "busy"] = "acquired"
    ) -> None:
        self.status = status
        self.calls: list[tuple[str, str]] = []
        self.claimed = asyncio.Event()

    async def claim(
        self, subscription: str, event_id: str, owner_token: str
    ) -> ClaimResult:
        self.calls.append(("claim", event_id))
        self.claimed.set()
        if self.status == "acquired":
            return ClaimResult.acquired(owner_token)
        if self.status == "already-complete":
            return ClaimResult.already_complete()
        return ClaimResult.busy()

    async def complete(
        self,
        subscription: str,
        event_id: str,
        owner_token: str,
        *,
        connection: Any | None = None,
    ) -> bool:
        self.calls.append(("complete", event_id))
        return True

    async def release(self, subscription: str, event_id: str, owner_token: str) -> bool:
        self.calls.append(("release", event_id))
        return True


def delivery(
    acked: list[bool],
    *,
    target: str | None = None,
    acked_event: asyncio.Event | None = None,
    event_id: str = "event-1",
    task_id: str = "task-1",
    order: list[str] | None = None,
) -> Delivery:
    event = CloudEvent.model_validate(
        {
            "specversion": "1.0",
            "id": event_id,
            "source": "urn:test",
            "type": "omega.task.state_changed",
            "datacontenttype": "application/json",
            "dataschema": schema_uri_for_type("omega.task.state_changed"),
            "data": {
                "task_id": task_id,
                "previous_state": "queued",
                "state": "running",
                "sequence": 1,
                "actor": "test",
            },
            "omegakey": task_id,
        }
    )

    async def ack() -> None:
        acked.append(True)
        if order is not None:
            order.append("ack")
        if acked_event is not None:
            acked_event.set()

    properties = {"omega_retry_target": target} if target is not None else {}
    return Delivery(
        event=event,
        topic="persistent://omega/task/state",
        key=task_id,
        message_id=f"m-{event_id}",
        properties=properties,
        ack=ack,
    )


def test_consumer_claims_before_effect_and_completes_before_ack() -> None:
    async def scenario() -> None:
        acked: list[bool] = []
        subscriber = Subscriber([delivery(acked)])
        publisher = Publisher()
        store = Store()
        called = asyncio.Event()

        async def handler(_delivery: Delivery, _context: HandlerContext) -> Ack:
            called.set()
            return Ack()

        runtime = ConsumerRuntime(
            subscriber, publisher, store, "gate", handler, max_in_flight=1
        )
        task = asyncio.create_task(runtime.run())
        await called.wait()
        runtime.stop()
        result = await task
        assert result.clean and result.completed == 1
        assert acked == [True]
        assert store.calls == [("claim", "event-1"), ("complete", "event-1")]
        assert subscriber.closed and publisher.closed

    asyncio.run(scenario())


def test_non_target_retry_is_acked_before_idempotency_claim() -> None:
    async def scenario() -> None:
        acked: list[bool] = []
        acked_event = asyncio.Event()
        subscriber = Subscriber(
            [delivery(acked, target="another-subscription", acked_event=acked_event)]
        )
        store = Store()
        called = asyncio.Event()

        async def handler(_delivery: Delivery, _context: HandlerContext) -> None:
            called.set()

        runtime = ConsumerRuntime(
            subscriber,
            Publisher(),
            store,
            "gate",
            handler,
            target="gate",
            max_in_flight=1,
        )
        task = asyncio.create_task(runtime.run())
        await acked_event.wait()
        runtime.stop()
        result = await task
        assert result.clean and acked == [True]
        assert store.calls == []
        assert not called.is_set()


def test_busy_claim_remains_unsettled() -> None:
    async def scenario() -> None:
        acked: list[bool] = []
        subscriber = Subscriber([delivery(acked)])
        store = Store("busy")

        async def handler(_delivery: Delivery, _context: HandlerContext) -> None:
            return None

        runtime = ConsumerRuntime(
            subscriber, Publisher(), store, "gate", handler, max_in_flight=1
        )
        task = asyncio.create_task(runtime.run())
        await store.claimed.wait()
        runtime.stop()
        await task
        assert acked == []
        assert store.calls == [("claim", "event-1")]

    asyncio.run(scenario())


def test_global_handler_bound_is_enforced_with_barrier() -> None:
    async def scenario() -> None:
        acked: list[bool] = []
        subscriber = Subscriber(
            [
                delivery(acked, event_id=f"event-{n}", task_id=f"task-{n}")
                for n in range(3)
            ]
        )
        store = Store()
        active = 0
        maximum = 0
        finished = 0
        two_active = asyncio.Event()
        release = asyncio.Event()
        all_done = asyncio.Event()

        async def handler(_item: Delivery, _context: HandlerContext) -> Ack:
            nonlocal active, maximum, finished
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                two_active.set()
            await release.wait()
            active -= 1
            finished += 1
            if finished == 3:
                all_done.set()
            return Ack()

        runtime = ConsumerRuntime(
            subscriber, Publisher(), store, "gate", handler, max_in_flight=2
        )
        task = asyncio.create_task(runtime.run())
        await two_active.wait()
        release.set()
        await all_done.wait()
        runtime.stop()
        await task
        assert maximum == 2 and len(acked) == 3

    asyncio.run(scenario())


def test_deadline_cancels_active_handler_without_ack_and_releases_claim() -> None:
    async def scenario() -> None:
        acked: list[bool] = []
        subscriber = Subscriber([delivery(acked)])
        store = Store()
        entered = asyncio.Event()
        deadline = asyncio.Event()

        async def handler(_item: Delivery, _context: HandlerContext) -> Ack:
            entered.set()
            await asyncio.Event().wait()
            return Ack()

        runtime = ConsumerRuntime(
            subscriber, Publisher(), store, "gate", handler, max_in_flight=1
        )
        task = asyncio.create_task(runtime.run(deadline_reached=deadline))
        await entered.wait()
        deadline.set()
        result = await task
        assert not result.clean and acked == []
        assert store.calls == [("claim", "event-1"), ("release", "event-1")]

    asyncio.run(scenario())


def test_transaction_to_effect_once(tmp_path) -> None:

    class RecordingStore(SQLiteIdempotencyStore):
        def __init__(self, database, order: list[str]) -> None:
            super().__init__(database)
            self.order = order
            self.complete_owners: list[str] = []

        async def complete(
            self,
            subscription: str,
            event_id: str,
            owner_token: str,
            *,
            connection: Any | None = None,
        ) -> bool:
            self.order.append("complete")
            self.complete_owners.append(owner_token)
            return await super().complete(
                subscription, event_id, owner_token, connection=connection
            )

    async def scenario() -> None:
        order: list[str] = []
        database = tmp_path / "consumer.sqlite3"
        store = RecordingStore(database, order)
        connection = store.open_connection()
        connection.execute(
            "CREATE TABLE effects (event_id TEXT PRIMARY KEY, count INTEGER NOT NULL)"
        )
        connection.close()
        acked: list[bool] = []
        acked_event = asyncio.Event()
        subscriber = Subscriber([delivery(acked, acked_event=acked_event, order=order)])

        async def handler(_item: Delivery, context: HandlerContext) -> Ack:
            connection = store.open_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO effects(event_id, count) VALUES (?, 1)",
                    (context.event_id,),
                )
                assert await context.complete(connection)
                connection.commit()
            finally:
                connection.close()
            return Ack()

        runtime = ConsumerRuntime(
            subscriber, Publisher(), store, "gate", handler, max_in_flight=1
        )
        task = asyncio.create_task(runtime.run())
        await acked_event.wait()
        runtime.stop()
        result = await task
        assert result.clean and acked == [True]
        assert len(store.complete_owners) == 1
        assert order == ["complete", "ack"]
        assert store.complete_owners[0]
        assert (
            await store.claim("gate", "event-1", "another-owner")
            == ClaimResult.already_complete()
        )
        connection = store.open_connection()
        try:
            assert connection.execute(
                "SELECT count FROM effects WHERE event_id = ?", ("event-1",)
            ).fetchone() == (1,)
        finally:
            connection.close()

    asyncio.run(scenario())


def test_consumer_key_locks_are_removed_after_unique_keys_complete() -> None:
    async def scenario() -> None:
        count = 32
        acked: list[bool] = []
        subscriber = Subscriber(
            [
                delivery(acked, event_id=f"event-{n}", task_id=f"task-{n}")
                for n in range(count)
            ]
        )
        store = Store()
        handled = 0
        all_handled = asyncio.Event()

        async def handler(_item: Delivery, _context: HandlerContext) -> Ack:
            nonlocal handled
            handled += 1
            if handled == count:
                all_handled.set()
            return Ack()

        runtime = ConsumerRuntime(
            subscriber, Publisher(), store, "gate", handler, max_in_flight=4
        )
        task = asyncio.create_task(runtime.run())
        await all_handled.wait()
        runtime.stop()
        await task
        assert len(acked) == count
        assert runtime._key_locks == {}

    asyncio.run(scenario())


def test_external_cancellation_closes_resources_in_order_and_leaves_delivery_unacked() -> (
    None
):
    async def scenario() -> None:
        order: list[str] = []
        acked: list[bool] = []
        entered = asyncio.Event()

        class ClosingSubscriber(Subscriber):
            async def close(self) -> None:
                order.append("consumer")
                await super().close()

        class ClosingPublisher(Publisher):
            async def close(self) -> None:
                order.append("publisher")
                await super().close()

        class Producer:
            async def flush(self) -> None:
                order.append("producer")

        subscriber = ClosingSubscriber([delivery(acked)])
        store = Store()

        async def handler(_item: Delivery, _context: HandlerContext) -> Ack:
            entered.set()
            await asyncio.Event().wait()
            return Ack()

        async def relay() -> None:
            order.append("relay")

        async def close_client() -> None:
            order.append("client")

        runtime = ConsumerRuntime(
            subscriber,
            ClosingPublisher(),
            store,
            "gate",
            handler,
            max_in_flight=1,
            flush_producers=(Producer(),),
            final_relay=relay,
            close_client=close_client,
        )
        task = asyncio.create_task(runtime.run())
        await entered.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("external cancellation must propagate")
        assert acked == []
        assert store.calls == [("claim", "event-1"), ("release", "event-1")]
        assert order == ["consumer", "producer", "relay", "publisher", "client"]

    asyncio.run(scenario())


def test_consumer_redacts_event_id_from_structured_warning_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        secret_id = "ghp_test_event_identifier_secret"
        caplog.set_level("WARNING")
        acked: list[bool] = []

        class FailingStore(Store):
            async def complete(
                self,
                subscription: str,
                event_id: str,
                owner_token: str,
                *,
                connection: Any | None = None,
            ) -> bool:
                raise RuntimeError(f"completion unavailable for {secret_id}")

        async def handler(_item: Delivery, _context: HandlerContext) -> Ack:
            return Ack()

        runtime = ConsumerRuntime(
            Subscriber([]),
            Publisher(),
            FailingStore(),
            "gate",
            handler,
            known_secrets=(secret_id,),
        )
        await runtime._process(delivery(acked, event_id=secret_id))
        assert acked == []

    asyncio.run(scenario())
    consumer_records = [
        record
        for record in caplog.records
        if record.name == "omega_prime.eventstream.consumer"
    ]
    assert len(consumer_records) == 1
    record_values = vars(consumer_records[0])
    assert record_values["ce_id"] == "[REDACTED]"
    assert "[REDACTED]" in record_values["error"]
    assert "ghp_test_event_identifier_secret" not in caplog.text


def test_ack_remains_unsettled_when_owner_completion_cas_fails() -> None:
    async def scenario() -> None:
        class LostOwnerStore(Store):
            async def complete(
                self,
                subscription: str,
                event_id: str,
                owner_token: str,
                *,
                connection: Any | None = None,
            ) -> bool:
                self.calls.append(("complete", event_id))
                return False

        class Observer:
            def __init__(self) -> None:
                self.outcomes: list[str] = []

            def handler_in_flight(self, _active: int) -> None:
                pass

            def consume(self, outcome: str, *_args, **_kwargs) -> None:
                self.outcomes.append(outcome)

        acked: list[bool] = []

        async def handler(_item: Delivery, _context: HandlerContext) -> Ack:
            return Ack()

        store = LostOwnerStore()
        observer = Observer()
        runtime = ConsumerRuntime(
            Subscriber([]),
            Publisher(),
            store,
            "gate",
            handler,
            observer=cast(EventStreamObserver, observer),
        )
        await runtime._process(delivery(acked))
        assert acked == []
        assert store.calls == [("claim", "event-1"), ("complete", "event-1")]
        assert "ack" not in observer.outcomes

    asyncio.run(scenario())


def test_retry_and_dlq_release_owner_before_acknowledging() -> None:
    async def scenario() -> None:
        for outcome_case in (
            Retry(after=0, reason="temporary"),
            DeadLetter("poison"),
        ):
            order: list[str] = []
            acked: list[bool] = []

            class OrderedPublisher(Publisher):
                def __init__(self, events: list[str]) -> None:
                    super().__init__()
                    self.events = events

                async def publish(self, request: PublishRequest) -> PublishReceipt:
                    self.events.append("publish")
                    return await super().publish(request)

            class OrderedStore(Store):
                def __init__(self, events: list[str]) -> None:
                    super().__init__()
                    self.events = events

                async def release(
                    self, subscription: str, event_id: str, owner_token: str
                ) -> bool:
                    self.events.append("release")
                    return True

            async def handler(
                _item: Delivery,
                _context: HandlerContext,
                result=outcome_case,
            ):
                return result

            runtime = ConsumerRuntime(
                Subscriber([]),
                OrderedPublisher(order),
                OrderedStore(order),
                "gate",
                handler,
            )
            await runtime._process(delivery(acked, order=order))
            assert order == ["publish", "release", "ack"]
            assert acked == [True]

    asyncio.run(scenario())


def test_retry_release_cas_failure_leaves_source_unacked_and_suppresses_transition() -> (
    None
):
    async def scenario() -> None:
        order: list[str] = []
        acked: list[bool] = []

        class OrderedPublisher(Publisher):
            async def publish(self, request: PublishRequest) -> PublishReceipt:
                order.append("publish")
                return await super().publish(request)

        class LostOwnerStore(Store):
            async def release(
                self, subscription: str, event_id: str, owner_token: str
            ) -> bool:
                order.append("release")
                return False

        class Observer:
            def __init__(self) -> None:
                self.transitions: list[str] = []

            def handler_in_flight(self, _active: int) -> None:
                pass

            def consume(self, outcome: str, *_args, **_kwargs) -> None:
                self.transitions.append(outcome)

            def retry(self) -> None:
                self.transitions.append("retry")

            def publish(self, **_kwargs) -> None:
                self.transitions.append("publish")

        async def handler(_item: Delivery, _context: HandlerContext) -> Retry:
            return Retry(after=0, reason="temporary")

        observer = Observer()
        runtime = ConsumerRuntime(
            Subscriber([]),
            OrderedPublisher(),
            LostOwnerStore(),
            "gate",
            handler,
            observer=cast(EventStreamObserver, observer),
        )
        await runtime._process(delivery(acked, order=order))
        assert order == ["publish", "release"]
        assert acked == []
        assert "retry" not in observer.transitions
        assert "publish" not in observer.transitions

    asyncio.run(scenario())


def test_authentication_failure_callback_runs_once_without_publish_or_ack() -> None:
    async def scenario() -> None:
        callbacks: list[str] = []
        acked: list[bool] = []

        class CountingPublisher(Publisher):
            def __init__(self) -> None:
                super().__init__()
                self.publish_calls = 0

            async def publish(self, request: PublishRequest) -> PublishReceipt:
                self.publish_calls += 1
                return await super().publish(request)

        async def on_authentication_failure() -> None:
            callbacks.append("refresh")

        async def handler(_item: Delivery, _context: HandlerContext) -> Ack:
            raise AuthenticationError("credentials expired")

        publisher = CountingPublisher()
        store = Store()
        runtime = ConsumerRuntime(
            Subscriber([]),
            publisher,
            store,
            "gate",
            handler,
            on_authentication_failure=on_authentication_failure,
        )
        await runtime._process(delivery(acked))
        assert callbacks == ["refresh"]
        assert publisher.publish_calls == 0
        assert acked == []
        assert store.calls == [("claim", "event-1"), ("release", "event-1")]

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["unknown", None, 1, []])
def test_claim_result_rejects_invalid_status(status) -> None:
    with pytest.raises(ValueError, match="invalid idempotency claim status"):
        ClaimResult(status)


def test_claim_result_preserves_owner_token_invariants() -> None:
    with pytest.raises(ValueError, match="requires an owner token"):
        ClaimResult("acquired")
    with pytest.raises(ValueError, match="no owner token"):
        ClaimResult("already-complete", "owner")


def test_already_completed_claim_acknowledges_without_running_handler() -> None:
    async def scenario() -> None:
        acked: list[bool] = []
        subscriber = Subscriber([delivery(acked)])
        store = Store("already-complete")
        called = False

        async def handler(_item: Delivery, _context: HandlerContext) -> Ack:
            nonlocal called
            called = True
            return Ack()

        runtime = ConsumerRuntime(
            subscriber, Publisher(), store, "gate", handler, max_in_flight=1
        )
        task = asyncio.create_task(runtime.run())
        await store.claimed.wait()
        runtime.stop()
        result = await task
        assert result.clean
        assert acked == [True]
        assert not called
        assert store.calls == [("claim", "event-1")]

    asyncio.run(scenario())


def test_key_dispatch_serializes_same_key_and_allows_other_key_progress() -> None:
    async def scenario() -> None:
        acked: list[bool] = []
        subscriber = Subscriber(
            [
                delivery(acked, event_id="same-1", task_id="same"),
                delivery(acked, event_id="same-2", task_id="same"),
                delivery(acked, event_id="other", task_id="other"),
            ]
        )
        store = Store()
        entered: list[str] = []
        first_same = asyncio.Event()
        other_entered = asyncio.Event()
        release = asyncio.Event()
        all_done = asyncio.Event()

        async def handler(item: Delivery, _context: HandlerContext) -> Ack:
            entered.append(item.event.id)
            if item.event.id == "same-1":
                first_same.set()
            elif item.event.id == "other":
                other_entered.set()
            await release.wait()
            if len(entered) == 3:
                all_done.set()
            return Ack()

        runtime = ConsumerRuntime(
            subscriber, Publisher(), store, "gate", handler, max_in_flight=3
        )
        task = asyncio.create_task(runtime.run())
        await first_same.wait()
        await other_entered.wait()
        assert "same-2" not in entered
        release.set()
        await all_done.wait()
        runtime.stop()
        await task
        assert entered.count("same-1") == 1
        assert entered.count("same-2") == 1
        assert entered.count("other") == 1
        assert len(acked) == 3

    asyncio.run(scenario())


def test_clean_shutdown_drains_then_closes_resources_in_order() -> None:
    async def scenario() -> None:
        order: list[str] = []
        acked: list[bool] = []
        acked_event = asyncio.Event()

        class OrderedSubscriber(Subscriber):
            async def close(self) -> None:
                order.append("consumer")

        class OrderedPublisher(Publisher):
            async def close(self) -> None:
                order.append("publisher")

        class Producer:
            async def flush(self) -> None:
                order.append("producer")

        async def handler(_item: Delivery, _context: HandlerContext) -> Ack:
            return Ack()

        async def final_relay() -> None:
            order.append("relay")

        async def close_client() -> None:
            order.append("client")

        runtime = ConsumerRuntime(
            OrderedSubscriber([delivery(acked, acked_event=acked_event, order=order)]),
            OrderedPublisher(),
            Store(),
            "gate",
            handler,
            max_in_flight=1,
            flush_producers=(Producer(),),
            final_relay=final_relay,
            close_client=close_client,
        )
        task = asyncio.create_task(runtime.run())
        await acked_event.wait()
        runtime.stop()
        result = await task
        assert result.clean and result.completed == 1
        assert order == ["ack", "consumer", "producer", "relay", "publisher", "client"]

    asyncio.run(scenario())
