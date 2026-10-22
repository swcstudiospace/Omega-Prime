from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from omega_prime.eventstream.adapters.memory import ManualClock, MemoryEventBus
from omega_prime.eventstream.consumer import ConsumerRuntime, HandlerContext
from omega_prime.eventstream.envelope import (
    CloudEvent,
    Delivery,
    PublishReceipt,
    PublishRequest,
)
from omega_prime.eventstream.errors import ContractError, SchemaError
from omega_prime.eventstream.idempotency import SQLiteIdempotencyStore
from omega_prime.eventstream.outbox import SQLiteOutbox
from omega_prime.eventstream.ports import Ack, Publisher, Subscriber
from omega_prime.eventstream.taxonomy import derive_key, schema_uri_for_type


def _request(
    payload: Mapping[str, Any],
    *,
    event_id: str,
    properties: Mapping[str, Any] | None = None,
) -> PublishRequest:
    event_type = "omega.sdlc.pr.opened"
    event = CloudEvent.model_validate(
        {
            "specversion": "1.0",
            "id": event_id,
            "source": "github://example/project",
            "type": event_type,
            "subject": "pr/75",
            "time": datetime(2026, 10, 23, 12, tzinfo=UTC),
            "datacontenttype": "application/json",
            "dataschema": schema_uri_for_type(event_type),
            "data": payload,
            "omegakey": derive_key(event_type, payload),
        }
    )
    return PublishRequest(
        event=event,
        topic="persistent://omega/sdlc/pr",
        key=derive_key(event.type, event.data),
        sequence_id=0,
        producer_name="runtime-smoke",
        properties=properties or {},
    )


def _valid_payload(schema_bundles: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    definition = schema_bundles["sdlc.pr"]["$defs"]["omega.sdlc.pr.opened"]
    examples = definition["examples"]
    assert isinstance(examples, list) and examples
    return dict(examples[0])


def test_publish_read_ack(
    schema_bundles: Mapping[str, Mapping[str, Any]], tmp_path: Path
) -> None:
    """Exercise memory delivery and canonical CloudEvents properties."""

    async def scenario() -> None:
        payload = _valid_payload(schema_bundles)
        clock = ManualClock(datetime(2026, 10, 23, 12, tzinfo=UTC))
        bus = MemoryEventBus(clock=clock)
        publisher = bus.publisher()
        topic = "persistent://omega/sdlc/pr"
        reader = await bus.read(topic)
        subscriber = await bus.subscribe(topic, "runtime-smoke-subscription")

        request = _request(
            payload,
            event_id="runtime-smoke-event-1",
            properties={"priority": "high", "origin": {"kind": "test"}},
        )
        with pytest.raises((TypeError, ValidationError)):
            cast(Any, request.event).subject = "mutated"

        receipt = await publisher.publish(request)
        assert receipt.event_id == request.event.id
        assert receipt.topic == request.topic
        assert receipt.message_id
        assert receipt.acknowledged_at == clock.now

        delivery = await subscriber.receive()
        assert delivery.event.specversion == request.event.specversion
        assert delivery.event.id == request.event.id
        assert delivery.event.source == request.event.source
        assert delivery.event.type == request.event.type
        assert delivery.event.subject == request.event.subject
        assert delivery.event.time == request.event.time
        assert delivery.event.datacontenttype == request.event.datacontenttype
        assert delivery.event.dataschema == request.event.dataschema
        assert delivery.event.to_properties() == request.event.to_properties()
        assert delivery.event.model_extra == request.event.model_extra
        assert dict(delivery.event.data) == payload
        assert delivery.topic == request.topic
        assert delivery.key == request.key
        assert delivery.message_id == receipt.message_id
        assert delivery.properties == {
            **dict(request.event.to_properties()),
            "priority": "high",
            "origin": {"kind": "test"},
        }
        assert dict(delivery.properties["origin"]) == {"kind": "test"}
        assert dict(delivery.properties) == {
            **dict(request.event.to_properties()),
            "priority": "high",
            "origin": {"kind": "test"},
        }
        await delivery.acknowledge()
        assert bus.acknowledged("runtime-smoke-subscription", delivery.message_id)

        second = _request(payload, event_id="runtime-smoke-event-2")
        second_receipt = await publisher.publish(second)
        next_delivery = await subscriber.receive()
        assert next_delivery.event.id == second.event.id
        assert next_delivery.message_id == second_receipt.message_id
        assert not next_delivery.redelivered
        await next_delivery.acknowledge()
        assert bus.acknowledged("runtime-smoke-subscription", next_delivery.message_id)
        invalid_reader = await bus.read(topic, start_at=2)

        definition = schema_bundles["sdlc.pr"]["$defs"]["omega.sdlc.pr.opened"]
        invalid_examples = definition["x-omega-invalid-examples"]
        assert isinstance(invalid_examples, list) and invalid_examples
        invalid_payload = dict(invalid_examples[0])
        with pytest.raises(ValidationError):
            _request(invalid_payload, event_id="runtime-invalid-event")
        assert await invalid_reader.read_nowait() is None

        invalid_document = json.loads(request.event.to_bytes())
        invalid_document["data"] = invalid_payload
        with pytest.raises(ContractError):
            CloudEvent.from_bytes(json.dumps(invalid_document).encode("utf-8"))
        with pytest.raises(ContractError):
            CloudEvent.from_bytes(b"not-json")

        # A valid real source delivery exercises the runtime's durable DLQ
        # publish-before-source-ack settlement contract. Invalid wire bytes
        # cannot enter the validating MemoryEventBus as a Delivery.
        dlq_topic = "persistent://omega/system/dlq.runtime-dlq-subscription"
        dlq_subscriber = await bus.subscribe(dlq_topic, "runtime-dlq-observer")
        poison = _request(payload, event_id="runtime-dlq-event")
        await publisher.publish(poison)
        source_delivery = await subscriber.receive()
        acked: list[str] = []
        acked_event = asyncio.Event()
        runtime_holder: list[ConsumerRuntime] = []
        real_ack = source_delivery.ack

        async def record_ack() -> None:
            await real_ack()
            acked.append(source_delivery.message_id)
            acked_event.set()
            runtime_holder[0].stop()

        instrumented = source_delivery.model_copy(update={"ack": record_ack})

        class SingleDeliverySubscriber:
            def __init__(self, delivery: Delivery) -> None:
                self.delivery: Delivery | None = delivery

            async def receive(self) -> Delivery:
                if self.delivery is not None:
                    delivery, self.delivery = self.delivery, None
                    return delivery
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

            async def close(self) -> None:
                return None

        class RecordingPublisher:
            def __init__(self) -> None:
                self.requests: list[PublishRequest] = []

            async def publish(self, request: PublishRequest) -> PublishReceipt:
                self.requests.append(request)
                return await publisher.publish(request)

            async def close(self) -> None:
                await publisher.close()

        dlq_publisher = RecordingPublisher()

        async def reject(_item: Delivery, _context: HandlerContext) -> Ack:
            raise SchemaError("schema poison")

        runtime = ConsumerRuntime(
            SingleDeliverySubscriber(instrumented),
            dlq_publisher,
            SQLiteIdempotencyStore(tmp_path / "runtime-dlq-ledger.sqlite3"),
            "runtime-dlq-subscription",
            reject,
            max_in_flight=1,
        )
        runtime_holder.append(runtime)
        run = asyncio.create_task(runtime.run())
        await acked_event.wait()
        result = await run
        assert result.clean
        assert acked == [source_delivery.message_id]
        assert len(dlq_publisher.requests) == 1
        dlq_request = dlq_publisher.requests[0]
        assert dlq_request.topic == dlq_topic
        assert dlq_request.event.type == "omega.control.signal.blocked"
        assert dlq_request.event.data["kind"] == "pr"
        assert dlq_request.event.data["repo"] == payload["repo"]
        assert dlq_request.event.data["number"] == payload["number"]
        assert dlq_request.key == f"{payload['repo']}#{payload['number']}"
        assert bus.acknowledged(
            "runtime-smoke-subscription", source_delivery.message_id
        )
        dlq_delivery = await dlq_subscriber.receive()
        assert dlq_delivery.event.id == poison.event.id
        assert dlq_delivery.event.type == "omega.control.signal.blocked"
        await dlq_delivery.acknowledge()
        await dlq_subscriber.close()
        await subscriber.close()
        await invalid_reader.close()
        await reader.close()
        await bus.close()

    asyncio.run(scenario())


def test_transaction_to_effect_once(
    tmp_path: Path, schema_bundles: Mapping[str, Mapping[str, Any]]
) -> None:
    class SendThenFailPublisher:
        def __init__(self, publisher: Publisher) -> None:
            self.publisher = publisher
            self.fail_once = True
            self.receipts: list[PublishReceipt] = []

        async def publish(self, request: PublishRequest) -> PublishReceipt:
            receipt = await self.publisher.publish(request)
            self.receipts.append(receipt)
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("injected send-before-mark failure")
            return receipt

        async def close(self) -> None:
            await self.publisher.close()

    class InstrumentingSubscriber:
        def __init__(self, subscriber: Subscriber) -> None:
            self.subscriber = subscriber
            self.delivered: list[tuple[str, str]] = []
            self.acknowledged: list[str] = []
            self.two_acks = asyncio.Event()

        async def receive(self) -> Delivery:
            delivery = await self.subscriber.receive()
            self.delivered.append((delivery.event.id, delivery.message_id))
            original_ack = delivery.ack

            async def acknowledge() -> None:
                await original_ack()
                self.acknowledged.append(delivery.message_id)
                if len(self.acknowledged) == 2:
                    self.two_acks.set()

            return delivery.model_copy(update={"ack": acknowledge})

        async def close(self) -> None:
            await self.subscriber.close()

    async def scenario() -> None:
        database = tmp_path / "runtime-outbox.sqlite3"
        outbox = SQLiteOutbox(database, producer_name="runtime-smoke")
        store = SQLiteIdempotencyStore(database)
        connection = outbox.open_connection()
        connection.execute(
            "CREATE TABLE business (id INTEGER PRIMARY KEY, value TEXT NOT NULL, updates INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE effects (event_id TEXT PRIMARY KEY, effect_count INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO business(id, value, updates) VALUES (1, 'before', 0)"
        )
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE business SET value = 'committed', updates = updates + 1 WHERE id = 1"
        )
        request = _request(
            _valid_payload(schema_bundles), event_id="outbox-runtime-event"
        )
        row = outbox.append(connection, request)
        connection.commit()
        outbox.notify_after_commit()
        connection.close()

        clock = ManualClock(datetime(2026, 10, 23, 12, tzinfo=UTC))
        bus = MemoryEventBus(clock=clock)
        accepting_publisher = bus.publisher()
        faulty = SendThenFailPublisher(accepting_publisher)
        with pytest.raises(RuntimeError, match="injected send-before-mark"):
            await outbox.relay_once(faulty)
        pending_after_first_send = await outbox.pending()
        assert len(pending_after_first_send) == 1
        assert pending_after_first_send[0].sequence_id == row.sequence_id
        assert await outbox.relay_once(faulty) == 1
        assert len(faulty.receipts) == 2
        assert [receipt.event_id for receipt in faulty.receipts] == [
            request.event.id
        ] * 2
        assert len({receipt.message_id for receipt in faulty.receipts}) == 2

        raw_subscriber = await bus.subscribe(request.topic, "effect-once")
        subscriber = InstrumentingSubscriber(raw_subscriber)
        consumer_publisher = bus.publisher()
        handler_calls = 0

        async def apply_effect(delivery: Delivery, context: HandlerContext) -> Ack:
            nonlocal handler_calls
            handler_calls += 1
            local = store.open_connection()
            try:
                local.execute("BEGIN IMMEDIATE")
                local.execute(
                    "INSERT INTO effects(event_id, effect_count) VALUES (?, 1)",
                    (context.event_id,),
                )
                assert await context.complete(local)
                local.commit()
            except BaseException:
                if local.in_transaction:
                    local.rollback()
                raise
            finally:
                local.close()
            return Ack()

        runtime = ConsumerRuntime(
            subscriber,
            consumer_publisher,
            store,
            "effect-once",
            apply_effect,
            max_in_flight=1,
        )
        runtime_task = asyncio.create_task(runtime.run())
        await subscriber.two_acks.wait()
        runtime.stop()
        result = await runtime_task

        assert result.clean
        assert handler_calls == 1
        assert len(subscriber.acknowledged) == 2
        assert len(set(subscriber.acknowledged)) == 2
        assert subscriber.delivered == [
            (request.event.id, subscriber.acknowledged[0]),
            (request.event.id, subscriber.acknowledged[1]),
        ]
        assert all(
            bus.acknowledged("effect-once", message_id)
            for message_id in subscriber.acknowledged
        )
        assert await outbox.pending() == ()
        check = store.open_connection()
        try:
            assert check.execute(
                "SELECT value, updates FROM business WHERE id = 1"
            ).fetchone() == ("committed", 1)
            assert check.execute(
                "SELECT COUNT(*) FROM business WHERE id = 1"
            ).fetchone() == (1,)
            assert check.execute(
                "SELECT effect_count FROM effects WHERE event_id = ?",
                (request.event.id,),
            ).fetchone() == (1,)
            assert check.execute("SELECT COUNT(*) FROM effects").fetchone() == (1,)
        finally:
            check.close()
        await bus.close()

    asyncio.run(scenario())
