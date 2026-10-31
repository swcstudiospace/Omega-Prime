from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any

import pytest

from omega_prime.eventstream.adapters.memory import (
    ManualClock,
    MemoryAdminPort,
    MemoryEventBus,
)
from omega_prime.eventstream.envelope import CloudEvent, PublishRequest
from omega_prime.eventstream.ports import (
    ClaimResult,
    DeadLetter,
    Retry,
)
from omega_prime.eventstream.taxonomy import derive_key, schema_uri_for_type


def make_request(
    event_id: str, *, properties: dict[str, Any] | None = None
) -> PublishRequest:
    event = CloudEvent.model_validate(
        {
            "specversion": "1.0",
            "id": event_id,
            "source": "github://example/project",
            "type": "omega.sdlc.pr.opened",
            "subject": "pr/7",
            "time": datetime(2026, 10, 23, 12, tzinfo=UTC),
            "datacontenttype": "application/json",
            "dataschema": schema_uri_for_type("omega.sdlc.pr.opened"),
            "data": {
                "repo": "example/project",
                "number": 7,
                "head_sha": "abc123",
                "actor": "alice",
                "title": "Example change",
                "base_ref": "main",
            },
            "omegakey": "example/project#7",
        }
    )
    return PublishRequest(
        event=event,
        topic="persistent://omega/sdlc/pr",
        key=derive_key(event.type, event.data),
        sequence_id=0,
        producer_name="test-producer",
        properties=properties or {},
    )


def test_bus_protocol_path_returns_immutable_metadata_and_acknowledges_once() -> None:
    async def scenario() -> None:
        bus = MemoryEventBus(clock=ManualClock(datetime(2026, 10, 23, tzinfo=UTC)))
        admin = MemoryAdminPort(bus)
        publisher = bus.publisher()
        subscriber = await bus.subscribe(
            "persistent://omega/sdlc/pr", "gate", subscription_type="Key_Shared"
        )

        request = make_request(
            "event-1", properties={"custom": "value", "ce_id": "spoofed"}
        )
        receipt = await publisher.publish(request)
        delivery = await subscriber.receive()

        assert receipt.event_id == request.event.id
        assert receipt.topic == request.topic
        assert delivery.event == request.event
        assert delivery.key == request.key
        # Filterable CloudEvents metadata is duplicated into broker properties;
        # the canonical values take precedence over caller-supplied collisions.
        assert delivery.properties["ce_id"] == request.event.id
        assert delivery.properties["ce_type"] == request.event.type
        assert delivery.properties["ce_source"] == request.event.source
        assert delivery.properties["omegakey"] == request.key
        assert delivery.properties["custom"] == "value"
        with pytest.raises(TypeError):
            delivery.properties["custom"] = "mutated"  # type: ignore[index]
        await delivery.acknowledge()
        await delivery.acknowledge()
        assert bus.acknowledged("gate", delivery.message_id)
        pending = asyncio.create_task(subscriber.receive())
        await asyncio.sleep(0)
        assert not pending.done()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await subscriber.close()
        await admin.close()
        await bus.close()

    asyncio.run(scenario())


def test_outcomes_and_claim_results_are_frozen_contract_values() -> None:
    retry = Retry(after=2.5, reason="transient")
    dead_letter = DeadLetter(reason="invalid")
    acquired = ClaimResult.acquired("owner-1")
    complete = ClaimResult.already_complete()
    busy = ClaimResult.busy("owner-2")
    assert retry.after == 2.5 and retry.reason == "transient"
    assert dead_letter.reason == "invalid"
    assert acquired.status == "acquired" and acquired.owner_token == "owner-1"
    assert complete.status == "already-complete"
    assert busy.status == "busy" and busy.owner_token == "owner-2"
    with pytest.raises(FrozenInstanceError):
        retry.reason = "changed"  # type: ignore[misc]


def test_bus_and_reader_keep_protocol_positions_independent() -> None:
    async def scenario() -> None:
        bus = MemoryEventBus(clock=ManualClock(datetime(2026, 10, 23, tzinfo=UTC)))
        request = make_request("event-2")
        await bus.publish(request)
        durable = await bus.subscribe("persistent://omega/sdlc/pr", "worker")
        reader = await bus.read("persistent://omega/sdlc/pr", start_at=0)
        observed = await reader.read()
        assert observed.event.id == request.event.id
        await observed.acknowledge()
        assert await reader.read_nowait() is None
        pending = await durable.receive()
        assert pending.event.id == request.event.id
        await pending.acknowledge()
        await reader.close()
        await durable.close()
        await bus.close()

    asyncio.run(scenario())
