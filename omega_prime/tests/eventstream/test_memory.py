from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from omega_prime.eventstream.adapters.memory import ManualClock, MemoryEventBus
from omega_prime.eventstream.envelope import CloudEvent, Delivery, PublishRequest
from omega_prime.eventstream.ports import SubscriptionType, UnsupportedSubscriptionMode
from omega_prime.eventstream.taxonomy import derive_key, schema_uri_for_type


def request(
    event_id: str,
    key: str,
    *,
    topic: str = "persistent://omega/task/state",
    delay: datetime | None = None,
    event_type: str = "omega.task.state_changed",
) -> PublishRequest:
    if event_type == "omega.task.heartbeat":
        data = {
            "task_id": key,
            "sequence": 1,
            "occurred_at": "2026-10-23T12:00:00Z",
        }
    else:
        data = {
            "task_id": key,
            "previous_state": "queued",
            "state": "running",
            "sequence": 1,
            "actor": "alice",
        }
    event = CloudEvent.model_validate(
        {
            "specversion": "1.0",
            "id": event_id,
            "source": "github://example/project",
            "type": event_type,
            "subject": f"task/{key}",
            "time": datetime(2026, 10, 23, 12, tzinfo=UTC),
            "datacontenttype": "application/json",
            "dataschema": schema_uri_for_type(event_type),
            "data": data,
            "omegakey": key,
        }
    )
    return PublishRequest(
        event=event,
        topic=topic,
        key=derive_key(event.type, event.data),
        sequence_id=0,
        producer_name="test",
        deliver_after=delay,
        properties={"source": "test"},
    )


def test_cancelled_receive_and_reopened_subscription_redeliver_unacked_message() -> (
    None
):
    async def scenario() -> None:
        bus = MemoryEventBus(clock=ManualClock(datetime(2026, 10, 23, tzinfo=UTC)))
        await bus.publish(request("unacked", "example/project#7"))
        first = await bus.subscribe("persistent://omega/task/state", "gate")
        delivery = await first.receive()
        assert delivery.event.id == "unacked"
        await first.close()

        reopened = await bus.subscribe("persistent://omega/task/state", "gate")
        redelivery = await reopened.receive()
        assert redelivery.event.id == "unacked"
        assert redelivery.redelivered is True
        await delivery.acknowledge()
        assert bus.acknowledged("gate", redelivery.message_id) is False
        await redelivery.acknowledge()
        await redelivery.acknowledge()
        assert bus.acknowledged("gate", redelivery.message_id) is True
        await reopened.close()
        await bus.close()

    asyncio.run(scenario())


def test_cancelled_wait_does_not_lose_future_delivery() -> None:
    async def scenario() -> None:
        bus = MemoryEventBus(clock=ManualClock(datetime(2026, 10, 23, tzinfo=UTC)))
        subscriber = await bus.subscribe("persistent://omega/task/state", "gate")
        waiting = asyncio.create_task(subscriber.receive())
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        await bus.publish(request("after-cancel", "example/project#7"))
        delivered = await subscriber.receive()
        assert delivered.event.id == "after-cancel"
        await delivered.acknowledge()
        await subscriber.close()
        await bus.close()

    asyncio.run(scenario())


def test_different_topic_receivers_keep_independent_wakeups() -> None:
    async def scenario() -> None:
        bus = MemoryEventBus(clock=ManualClock(datetime(2026, 10, 23, tzinfo=UTC)))
        first_topic = "persistent://omega/task/state"
        second_topic = "persistent://omega/task/heartbeat"
        first = await bus.subscribe(first_topic, "state")
        second = await bus.subscribe(second_topic, "heartbeat")
        first_started = asyncio.Event()
        second_started = asyncio.Event()
        first_delivered = asyncio.Event()

        async def receive_and_loop() -> None:
            first_started.set()
            delivery = await first.receive()
            assert delivery.event.id == "first-topic"
            first_delivered.set()
            await first.receive()

        async def receive_second_topic() -> Delivery:
            second_started.set()
            return await second.receive()

        first_task = asyncio.create_task(receive_and_loop())
        second_task = asyncio.create_task(receive_second_topic())
        await asyncio.gather(first_started.wait(), second_started.wait())

        await bus.publish(request("first-topic", "state-key", topic=first_topic))
        await first_delivered.wait()
        await bus.publish(
            request(
                "second-topic",
                "heartbeat-key",
                topic=second_topic,
                event_type="omega.task.heartbeat",
            )
        )
        second_delivery = await second_task
        assert second_delivery.event.id == "second-topic"
        await second_delivery.acknowledge()
        first_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_task
        await first.close()
        await second.close()
        await bus.close()

    asyncio.run(scenario())


def test_key_ordering_blocks_same_key_but_allows_independent_key_progress() -> None:
    async def scenario() -> None:
        bus = MemoryEventBus(clock=ManualClock(datetime(2026, 10, 23, tzinfo=UTC)))
        await bus.publish(request("a1", "key-a"))
        await bus.publish(request("a2", "key-a"))
        await bus.publish(request("b1", "key-b"))
        subscriber = await bus.subscribe(
            "persistent://omega/task/state", "gate", subscription_type="Shared"
        )
        first = await subscriber.receive()
        second = await subscriber.receive()
        assert (first.event.id, second.event.id) == ("a1", "b1")
        await second.acknowledge()
        await first.acknowledge()
        third = await subscriber.receive()
        assert third.event.id == "a2"
        await third.acknowledge()
        await subscriber.close()
        await bus.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("subscription_type", ["Shared", "Key_Shared"])
def test_delayed_messages_release_only_after_injected_clock_advance(
    subscription_type: SubscriptionType,
) -> None:
    async def scenario() -> None:
        start = datetime(2026, 10, 23, tzinfo=UTC)
        clock = ManualClock(start)
        bus = MemoryEventBus(clock=clock)
        subscriber = await bus.subscribe(
            "persistent://omega/task/state", "gate", subscription_type=subscription_type
        )
        await bus.publish(
            request("later", "example/project#7", delay=start + timedelta(seconds=10))
        )
        receive = asyncio.create_task(subscriber.receive())
        await asyncio.sleep(0)
        clock.advance(timedelta(seconds=9))
        await asyncio.sleep(0)
        assert not receive.done()
        clock.advance(timedelta(seconds=1))
        delivery = await receive
        assert delivery.event.id == "later"
        await delivery.acknowledge()
        await subscriber.close()
        await bus.close()

    asyncio.run(scenario())


def test_same_deadline_is_stable_and_unsupported_mode_is_typed() -> None:
    async def scenario() -> None:
        start = datetime(2026, 10, 23, tzinfo=UTC)
        clock = ManualClock(start)
        bus = MemoryEventBus(clock=clock)
        subscriber = await bus.subscribe(
            "persistent://omega/task/state", "single", subscription_type="Exclusive"
        )
        deadline = start + timedelta(seconds=4)
        await bus.publish(request("first", "key-1", delay=deadline))
        await bus.publish(request("second", "key-2", delay=deadline))
        clock.advance(timedelta(seconds=4))
        with pytest.raises(UnsupportedSubscriptionMode, match="delayed"):
            await subscriber.receive()
        await subscriber.close()

        shared = await bus.subscribe(
            "persistent://omega/task/state", "shared", subscription_type="Shared"
        )
        first = await shared.receive()
        second = await shared.receive()
        assert (first.event.id, second.event.id) == ("first", "second")
        await first.acknowledge()
        await second.acknowledge()
        await shared.close()
        await bus.close()

    asyncio.run(scenario())


def test_reader_seek_is_independent_of_durable_subscription_acknowledgements() -> None:
    async def scenario() -> None:
        bus = MemoryEventBus(clock=ManualClock(datetime(2026, 10, 23, tzinfo=UTC)))
        await bus.publish(request("one", "key-1"))
        await bus.publish(request("two", "key-2"))
        reader = await bus.read("persistent://omega/task/state", start_at=1)
        assert (await reader.read()).event.id == "two"
        await reader.seek(0)
        assert (await reader.read()).event.id == "one"
        await reader.close()
        durable = await bus.subscribe("persistent://omega/task/state", "reader-check")
        delivery = await durable.receive()
        assert delivery.event.id == "one"
        await delivery.acknowledge()
        await durable.close()
        await bus.close()

    asyncio.run(scenario())


def test_latest_subscription_starts_after_existing_messages() -> None:
    async def scenario() -> None:
        bus = MemoryEventBus(clock=ManualClock(datetime(2026, 10, 23, tzinfo=UTC)))
        await bus.publish(request("before", "key-before"))
        subscriber = await bus.subscribe(
            "persistent://omega/task/state",
            "latest",
            initial_position="Latest",
        )
        await bus.publish(request("after", "key-after"))
        delivery = await subscriber.receive()
        assert delivery.event.id == "after"
        await delivery.acknowledge()
        await subscriber.close()
        await bus.close()

    asyncio.run(scenario())


def test_memory_import_boundary_does_not_load_optional_transports_or_telemetry() -> (
    None
):
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import omega_prime.eventstream.ports; "
                "import omega_prime.eventstream.adapters; "
                "assert not any(name == 'pulsar' or name.startswith('pulsar.') "
                "or name == 'httpx' or name.startswith('httpx.') "
                "or name == 'opentelemetry' or name.startswith('opentelemetry.') "
                "for name in sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
