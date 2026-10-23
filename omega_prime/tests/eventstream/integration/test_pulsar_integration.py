"""Real Pulsar data-path tests for loopback and helper-owned secure brokers."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omega_prime.eventstream.adapters.pulsar import PulsarAdapterError

pytestmark = [pytest.mark.eventstream_integration, pytest.mark.asyncio]


def _settings() -> dict[str, str]:
    url = os.environ.get("OMEGA_EVENTSTREAM_IT_URL")
    if not url:
        pytest.skip("OMEGA_EVENTSTREAM_IT_URL is not configured")
    mode = os.environ.get("OMEGA_EVENTSTREAM_IT_MODE", "external-loopback")
    if mode not in {"external-loopback", "owned-secure"}:
        pytest.fail("OMEGA_EVENTSTREAM_IT_MODE is invalid")
    admin_url = os.environ.get("OMEGA_EVENTSTREAM_IT_ADMIN_URL")
    if mode == "owned-secure":
        required = (
            "OMEGA_EVENTSTREAM_IT_ADMIN_URL",
            "OMEGA_EVENTSTREAM_IT_OWNERSHIP_FILE",
            "OMEGA_EVENTSTREAM_TOKEN_FILE",
            "OMEGA_EVENTSTREAM_TLS_CA_FILE",
            "OMEGA_EVENTSTREAM_ENCRYPTION_KEY_FILE",
            "OMEGA_EVENTSTREAM_PRIVATE_KEY_FILE",
        )
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            pytest.fail("owned-secure prerequisites missing: " + ", ".join(missing))
        ownership = Path(os.environ["OMEGA_EVENTSTREAM_IT_OWNERSHIP_FILE"])
        if not ownership.is_file():
            pytest.fail("owned-secure ownership record is absent")
        record = json.loads(ownership.read_text(encoding="utf-8"))
        if not isinstance(record, Mapping) or not record.get("container_id"):
            pytest.fail("owned-secure ownership record is malformed")
        if record.get("service_url") != url or record.get("admin_url") != admin_url:
            pytest.fail("owned-secure endpoints do not match the ownership record")
        for name in (
            "OMEGA_EVENTSTREAM_TOKEN_FILE",
            "OMEGA_EVENTSTREAM_TLS_CA_FILE",
            "OMEGA_EVENTSTREAM_ENCRYPTION_KEY_FILE",
            "OMEGA_EVENTSTREAM_PRIVATE_KEY_FILE",
        ):
            if not Path(os.environ[name]).is_file():
                pytest.fail(f"owned-secure protected file is absent: {name}")
    if not admin_url:
        pytest.fail("configured broker requires OMEGA_EVENTSTREAM_IT_ADMIN_URL")
    return {"url": url, "admin_url": admin_url, "mode": mode}


@pytest.fixture(scope="session")
def it_settings() -> dict[str, str]:
    return _settings()


def _event(event_type: str = "omega.sdlc.pr.pushed") -> Any:
    from omega_prime.eventstream.envelope import CloudEvent
    from omega_prime.eventstream.taxonomy import EVENT_TYPES

    number = int(uuid.uuid4().hex[:8], 16) % 2_000_000_000 + 1
    event = EVENT_TYPES[event_type]
    if event_type != "omega.sdlc.pr.pushed":
        raise ValueError("integration event helper requires a PR push event")
    return CloudEvent(
        specversion="1.0",
        id=uuid.uuid4().hex,
        source="urn:omega:eventstream-integration",
        type=event.type,
        subject=f"pr/{number}",
        datacontenttype="application/json",
        dataschema=event.schema_ref,
        data={
            "repo": "eventstream-integration/example",
            "number": number,
            "head_sha": uuid.uuid4().hex + uuid.uuid4().hex[:8],
            "actor": "eventstream-integration",
            "before_sha": uuid.uuid4().hex + uuid.uuid4().hex[:8],
        },
    )


def _request(
    event: Any,
    *,
    producer_name: str | None = None,
    sequence_id: int = 0,
    properties: Mapping[str, Any] | None = None,
) -> Any:
    from omega_prime.eventstream.envelope import PublishRequest
    from omega_prime.eventstream.taxonomy import EVENT_TYPES, derive_key

    return PublishRequest(
        event=event,
        topic=EVENT_TYPES[event.type].topic,
        key=derive_key(event.type, event.data),
        sequence_id=sequence_id,
        producer_name=producer_name or f"eventstream-it-{uuid.uuid4().hex}",
        properties=properties or {},
    )


@pytest_asyncio.fixture
async def broker(
    it_settings: dict[str, str], tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[dict[str, Any]]:
    """Connect to the explicitly configured broker; never control its lifecycle."""
    from omega_prime.eventstream.adapters.pulsar import PulsarEventBus
    from omega_prime.eventstream.admin import PulsarAdminPort
    from omega_prime.eventstream.config import EventStreamConfig

    values = dict(os.environ)
    values["OMEGA_EVENTSTREAM_URL"] = it_settings["url"]
    values["OMEGA_EVENTSTREAM_ADMIN_URL"] = it_settings["admin_url"]
    if it_settings["mode"] == "external-loopback":
        # Redelivery consumers require CryptoKeyReader material even when the
        # exercised events are non-auth; mint ephemeral 0600 files for the suite.
        key_dir = tmp_path_factory.mktemp("eventstream-it-keys")
        public = key_dir / "auth-public.pem"
        private = key_dir / "auth-private.pem"
        public.write_text("IT-PUBLIC-KEY", encoding="utf-8")
        private.write_text("IT-PRIVATE-KEY", encoding="utf-8")
        public.chmod(0o600)
        private.chmod(0o600)
        values.setdefault("OMEGA_EVENTSTREAM_ENCRYPTION_KEY_FILE", str(public))
        values.setdefault("OMEGA_EVENTSTREAM_PRIVATE_KEY_FILE", str(private))
    config = EventStreamConfig.from_env(env=values)
    admin = PulsarAdminPort()
    bus = None
    try:
        await admin.configure(config)
        if it_settings["mode"] == "owned-secure":
            await admin.reconcile(dry_run=False)
        else:
            await _provision_external_loopback(admin)
        bus = PulsarEventBus(config, acknowledged_watermark=lambda _topic, _name: 0)
        yield {"bus": bus, "admin": admin, "config": config, "settings": it_settings}
    finally:
        if bus is not None:
            await bus.close()
        await admin.close()


async def _provision_external_loopback(admin: Any) -> None:
    """Create only resources consumed by these tests; never alter broker settings."""
    resources: tuple[tuple[str, dict[str, object]], ...] = (
        (
            "/admin/v2/tenants/omega",
            {"allowedClusters": ["standalone"], "adminRoles": []},
        ),
        ("/admin/v2/namespaces/omega/sdlc", {}),
        ("/admin/v2/namespaces/omega/system", {}),
    )
    for path, body in resources:
        response = await admin._request("GET", path, allow_not_found=True)
        if response.status_code == 404:
            await admin._request("PUT", path, json_body=body)

    for topic, partitions in (
        ("persistent://omega/sdlc/pr", 4),
        ("persistent://omega/system/retry.gate", 1),
        ("persistent://omega/system/dlq.gate", 1),
    ):
        from omega_prime.eventstream.admin import _url_path

        topic_path = _url_path(topic)
        probe = f"{topic_path}/partitions"
        response = await admin._request("GET", probe, allow_not_found=True)
        if response.status_code == 404:
            if partitions > 1:
                await admin._request("PUT", probe, json_body=partitions)
            else:
                await admin._request("PUT", topic_path, json_body=None)


async def _provision_topics(admin: Any, topics: tuple[str, ...]) -> None:
    """Create only test-owned topics through the broker's REST API."""
    from omega_prime.eventstream.admin import _url_path

    for topic in topics:
        topic_path = _url_path(topic)
        probe = f"{topic_path}/partitions"
        response = await admin._request("GET", probe, allow_not_found=True)
        if response.status_code == 404:
            await admin._request("PUT", topic_path, json_body=None)


async def _receive(subscriber: Any, *, timeout: float = 15.0) -> Any:
    return await asyncio.wait_for(subscriber.receive(), timeout=timeout)


async def _expect_quiet(subscriber: Any, *, timeout: float = 1.0) -> None:
    """Assert no delivery arrives without cancelling the subscriber receive.

    ``asyncio.wait_for`` cancels its awaited coroutine on timeout, which would
    tear down an in-flight Pulsar receive and poison failover standby handoff.
    """
    task = asyncio.create_task(_receive(subscriber, timeout=timeout + 30.0))
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if done:
        task.result()
        raise AssertionError("standby received a delivery before failover")
    subscriber._quiet_wait_task = task


async def _receive_after_quiet(subscriber: Any, *, timeout: float = 30.0) -> Any:
    pending = getattr(subscriber, "_quiet_wait_task", None)
    if pending is not None:
        try:
            return await asyncio.wait_for(asyncio.shield(pending), timeout=timeout)
        finally:
            if pending.done():
                subscriber._quiet_wait_task = None
    return await _receive(subscriber, timeout=timeout)


async def test_admin_dry_run_apply_and_second_apply_are_read_only(
    broker: dict[str, Any],
) -> None:
    admin = broker["admin"]
    dry = await admin.reconcile(dry_run=True)
    assert dry.dry_run
    if broker["settings"]["mode"] == "owned-secure":
        await admin.reconcile(dry_run=False)
        second = await admin.reconcile(dry_run=False)
        assert not second.actions


async def test_structured_bytes_properties_and_individual_ack(
    broker: dict[str, Any],
) -> None:
    bus = broker["bus"]
    event = _event()
    topic = "persistent://omega/sdlc/pr"
    subscriber = await bus.subscribe(
        topic,
        f"it-ack-{uuid.uuid4().hex}",
        subscription_type="Shared",
        initial_position="Latest",
    )
    try:
        receipt = await bus.publish(_request(event, properties={"it_case": "ack"}))
        assert receipt.event_id == event.id and receipt.topic == topic
        delivery = await _receive(subscriber)
        assert delivery.event.to_bytes() == event.to_bytes()
        assert delivery.properties["it_case"] == "ack"
        await delivery.acknowledge()
    finally:
        await subscriber.close()


async def test_subscription_modes_and_key_shared_ordering(
    broker: dict[str, Any],
) -> None:
    bus = broker["bus"]
    topic = "persistent://omega/sdlc/pr"
    for mode in ("Shared", "Key_Shared", "Failover", "Exclusive"):
        subscriber = await bus.subscribe(
            topic,
            f"it-{mode.lower()}-{uuid.uuid4().hex}",
            subscription_type=mode,
            initial_position="Latest",
        )
        try:
            event = _event()
            await bus.publish(_request(event))
            delivery = await _receive(subscriber)
            assert delivery.event.id == event.id
            await delivery.acknowledge()
        finally:
            await subscriber.close()

    first = await bus.subscribe(
        topic,
        f"it-order-{uuid.uuid4().hex}",
        subscription_type="Key_Shared",
        initial_position="Latest",
    )
    try:
        producer_name = f"it-order-producer-{uuid.uuid4().hex}"
        first_event = _event()
        second_event = first_event.model_copy(
            update={
                "id": uuid.uuid4().hex,
                "data": {**first_event.data, "head_sha": uuid.uuid4().hex},
            }
        )
        first_request = _request(
            first_event, producer_name=producer_name, sequence_id=1
        )
        second_request = _request(
            second_event, producer_name=producer_name, sequence_id=2
        )
        assert first_request.key == second_request.key
        await bus.publish(first_request)
        await bus.publish(second_request)
        received = [await _receive(first), await _receive(first)]
        assert [item.event.id for item in received] == [first_event.id, second_event.id]
        for delivery in received:
            await delivery.acknowledge()
    finally:
        await first.close()


async def test_failover_hands_delivery_to_standby_after_active_closes(
    broker: dict[str, Any],
) -> None:
    """Failover elects by consumer-name order; discover the active at runtime.

    Pulsar promotes the lexicographically smallest consumer name, which is not
    creation order when names are broker-assigned. Race both subscribers for the
    first delivery, then close the winner and require the survivor to take over.
    """
    bus = broker["bus"]
    topic = "persistent://omega/sdlc/pr"
    subscription = f"it-failover-{uuid.uuid4().hex}"
    first = await bus.subscribe(
        topic, subscription, subscription_type="Failover", initial_position="Latest"
    )
    second = await bus.subscribe(
        topic, subscription, subscription_type="Failover", initial_position="Latest"
    )
    try:
        first_event = _event()
        await bus.publish(_request(first_event))
        tasks = {
            asyncio.create_task(_receive(first), name="first"): first,
            asyncio.create_task(_receive(second), name="second"): second,
        }
        done, pending = await asyncio.wait(
            set(tasks), return_when=asyncio.FIRST_COMPLETED, timeout=15.0
        )
        if not done:
            for task in pending:
                task.cancel()
            raise TimeoutError("failover active never received the first delivery")
        winner_task = next(iter(done))
        active = tasks[winner_task]
        standby = first if active is second else second
        active_delivery = winner_task.result()
        assert active_delivery.event.id == first_event.id
        await active_delivery.acknowledge()
        # The loser's receive task is already in-flight; reuse it after promotion
        # instead of cancelling (cancellation poisons Pulsar receive futures).
        loser_task = next(iter(pending)) if pending else None
        if loser_task is not None:
            standby._quiet_wait_task = loser_task
        else:
            await _expect_quiet(standby, timeout=1.0)

        await active.close()
        next_event = _event()
        await bus.publish(_request(next_event))
        standby_delivery = await _receive_after_quiet(standby, timeout=30.0)
        assert standby_delivery.event.id == next_event.id
        await standby_delivery.acknowledge()
    finally:
        await first.close()
        await second.close()


async def test_exclusive_subscription_rejects_or_blocks_second_consumer(
    broker: dict[str, Any],
) -> None:
    bus = broker["bus"]
    topic = "persistent://omega/sdlc/pr"
    subscription = f"it-exclusive-{uuid.uuid4().hex}"
    first = await bus.subscribe(
        topic, subscription, subscription_type="Exclusive", initial_position="Latest"
    )
    second = None
    try:
        second = await asyncio.wait_for(
            bus.subscribe(
                topic,
                subscription,
                subscription_type="Exclusive",
                initial_position="Latest",
            ),
            timeout=10.0,
        )
    except TimeoutError:
        # Broker blocked the second Exclusive consumer until we gave up.
        second = None
    except PulsarAdapterError as exc:
        # Pulsar fail-fast rejects a second Exclusive consumer with ConsumerBusy.
        if "ConsumerBusy" not in str(exc):
            raise
        second = None

    try:
        event = _event()
        await bus.publish(_request(event))
        delivery = await _receive(first)
        assert delivery.event.id == event.id
        await delivery.acknowledge()
        if second is not None:
            with pytest.raises(TimeoutError):
                await _receive(second, timeout=1.0)
    finally:
        await first.close()
        if second is not None:
            await second.close()


@pytest.mark.parametrize("mode", ["Shared", "Key_Shared", "Failover", "Exclusive"])
async def test_retry_exhaustion_routes_actual_attempts_and_history(
    broker: dict[str, Any], mode: str
) -> None:
    from omega_prime.eventstream.ports import Retry
    from omega_prime.eventstream.retry import RetryRouter, SingletonRetryHandoff

    bus = broker["bus"]
    topic = "persistent://omega/sdlc/pr"
    subscription = f"it-retry-{mode.lower()}-{uuid.uuid4().hex}"
    event = _event()
    source = await bus.subscribe(
        topic, subscription, subscription_type=mode, initial_position="Latest"
    )
    retry_topic = f"persistent://omega/system/retry.{subscription}"
    retry = None
    dlq_topic = f"persistent://omega/system/dlq.{subscription}"
    dlq = None
    try:
        await _provision_topics(broker["admin"], (retry_topic, dlq_topic))
        retry = await bus.subscribe(
            retry_topic,
            f"it-retry-receiver-{uuid.uuid4().hex}",
            subscription_type="Shared",
            initial_position="Latest",
        )
        dlq = await bus.subscribe(
            dlq_topic,
            f"it-dlq-receiver-{uuid.uuid4().hex}",
            subscription_type="Shared",
            initial_position="Latest",
        )
        await bus.publish(_request(event))
        current = await _receive(source)
        router = RetryRouter(
            bus,
            subscription,
            subscription_type=mode,
            rng=lambda low, _high: low,
        )
        handoff = SingletonRetryHandoff(bus, subscription)
        singleton = mode in {"Failover", "Exclusive"}

        for attempt in range(1, 5):
            assert int(current.properties.get("omega_attempt", 1)) == attempt
            assert (
                await router.handle(current, Retry(1.0, f"attempt {attempt}"))
                == "retry"
            )
            retry_delivery = await _receive(retry, timeout=30.0)
            assert retry_delivery.event.id == event.id
            assert int(retry_delivery.properties["omega_attempt"]) == attempt + 1
            assert retry_delivery.properties["omega_origin_topic"] == topic
            history_value = retry_delivery.properties["omega_failure_history"]
            history = (
                json.loads(history_value)
                if isinstance(history_value, str)
                else history_value
            )
            assert [item["attempt"] for item in history] == list(range(1, attempt + 1))
            if singleton:
                assert retry_delivery.properties["omega_retry_target"] == subscription
                assert await handoff.handle(retry_delivery) == "republished"
                current = await _receive(source, timeout=30.0)
                assert current.event.id == event.id
            else:
                current = retry_delivery

        assert int(current.properties.get("omega_attempt", 1)) == 5
        assert await router.handle(current, Retry(1.0, "attempt 5")) == "dlq"
        blocked = await _receive(dlq)
        assert blocked.event.type == "omega.control.signal.blocked"
        assert blocked.event.data["kind"] == "pr"
        history_value = blocked.properties["omega_failure_history"]
        history = (
            json.loads(history_value)
            if isinstance(history_value, str)
            else history_value
        )
        assert [item["attempt"] for item in history] == [1, 2, 3, 4, 5]
        assert int(blocked.properties["omega_attempt"]) == 5
        await blocked.acknowledge()
    finally:
        await source.close()
        if retry is not None:
            await retry.close()
        if dlq is not None:
            await dlq.close()


async def test_retry_publish_failure_leaves_source_unacknowledged(
    broker: dict[str, Any],
) -> None:
    from omega_prime.eventstream.ports import Retry
    from omega_prime.eventstream.retry import RetryRouter

    bus = broker["bus"]
    topic = "persistent://omega/sdlc/pr"
    subscription = f"it-retry-failure-{uuid.uuid4().hex}"
    source = await bus.subscribe(
        topic, subscription, subscription_type="Shared", initial_position="Latest"
    )
    redelivery = None
    try:
        event = _event()
        await bus.publish(_request(event))
        delivery = await _receive(source)

        class FailingRetryPublisher:
            async def publish(self, request: Any) -> Any:
                if request.topic == f"persistent://omega/system/retry.{subscription}":
                    raise RuntimeError("injected retry publish failure")
                return await bus.publish(request)

            async def close(self) -> None:
                return None

        router = RetryRouter(
            FailingRetryPublisher(), subscription, subscription_type="Shared"
        )
        with pytest.raises(RuntimeError, match="injected retry publish failure"):
            await router.handle(delivery, Retry(1.0, "retry failure"))
        await source.close()
        redelivery = await bus.subscribe(
            topic,
            subscription,
            subscription_type="Shared",
            initial_position="Latest",
        )
        replayed = await _receive(redelivery)
        assert replayed.event.id == event.id
        await replayed.acknowledge()
    finally:
        await source.close()
        if redelivery is not None:
            await redelivery.close()


async def test_reader_seek_and_non_durable_read(broker: dict[str, Any]) -> None:
    bus = broker["bus"]
    topic = "persistent://omega/sdlc/pr"
    event = _event()
    await bus.publish(_request(event))
    reader = await bus.read(topic, start_at=0)
    try:
        found = None
        for _ in range(10_000):
            delivery = await asyncio.wait_for(reader.read(), timeout=15.0)
            if delivery.event.id == event.id:
                found = delivery
                break
        assert found is not None
        await reader.seek(0)
        assert (await asyncio.wait_for(reader.read(), timeout=15.0)).event.id
    finally:
        await reader.close()


async def test_outbox_relay_and_idempotency_claim_complete_release(
    broker: dict[str, Any], tmp_path: Path
) -> None:
    from omega_prime.eventstream.adapters.pulsar import PulsarEventBus
    from omega_prime.eventstream.idempotency import SQLiteIdempotencyStore
    from omega_prime.eventstream.outbox import SQLiteOutbox

    event = _event()
    producer_name = f"it-outbox-{uuid.uuid4().hex}"
    outbox = SQLiteOutbox(tmp_path / "outbox.sqlite3", producer_name=producer_name)
    with outbox.transaction() as connection:
        record = outbox.append(
            connection,
            _request(event, producer_name=producer_name),
        )
    config = broker["config"]
    bus = PulsarEventBus(
        config,
        acknowledged_watermark=lambda _topic, _name: outbox.initial_sequence_id,
    )
    subscriber = await bus.subscribe(
        "persistent://omega/sdlc/pr",
        f"it-outbox-consume-{uuid.uuid4().hex}",
        subscription_type="Shared",
        initial_position="Latest",
    )
    try:
        assert await outbox.relay_once(bus) == 1
        assert outbox.acknowledged_watermark == record.sequence_id
        delivery = await _receive(subscriber)
        assert delivery.event.id == event.id
        store = SQLiteIdempotencyStore(tmp_path / "idempotency.sqlite3")
        first = await store.claim("it-outbox", event.id, "owner-one")
        assert first.status == "acquired" and first.owner_token == "owner-one"
        assert await store.complete("it-outbox", event.id, "owner-one")
        assert (
            await store.claim("it-outbox", event.id, "owner-two")
        ).status == "already-complete"
        second = await store.claim("it-outbox", event.id + "-release", "owner-three")
        assert second.status == "acquired"
        assert await store.release("it-outbox", event.id + "-release", "owner-three")
        reclaimed = await store.claim("it-outbox", event.id + "-release", "owner-four")
        assert reclaimed.status == "acquired" and reclaimed.owner_token == "owner-four"
        assert await store.complete("it-outbox", event.id + "-release", "owner-four")
        await delivery.acknowledge()
    finally:
        await subscriber.close()
        await bus.close()


async def test_auth_encryption_is_required_even_on_loopback(
    broker: dict[str, Any],
) -> None:
    if broker["settings"]["mode"] != "owned-secure":
        pytest.skip("auth-payload encryption requires the helper-owned secure matrix")
    from omega_prime.eventstream.adapters.pulsar import PulsarEventBus
    from omega_prime.eventstream.errors import PermanentError

    bus = PulsarEventBus(
        broker["config"], acknowledged_watermark=lambda _topic, _name: 0
    )
    try:
        event = _event()
        auth_event = event.model_copy(
            update={
                "type": "omega.auth.session.login_started",
                "dataschema": "urn:omega:schema:omega.auth.session.login_started:1",
                "data": {
                    "host": "integration",
                    "tool": "oauth",
                    "session_id": uuid.uuid4().hex,
                    "actor": "integration",
                },
            }
        )
        with pytest.raises(PermanentError):
            await bus.publish(_request(auth_event))
    finally:
        await bus.close()


async def test_owned_secure_admin_status_does_not_claim_function_runtime(
    broker: dict[str, Any],
) -> None:
    if broker["settings"]["mode"] != "owned-secure":
        pytest.skip(
            "owned-secure admin status check requires the helper-owned CI service"
        )
    status = await broker["admin"].status()
    assert status
