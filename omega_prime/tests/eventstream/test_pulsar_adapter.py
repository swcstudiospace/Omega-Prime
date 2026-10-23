"""Broker-free behavioral tests for the optional Pulsar transport boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from omega_prime.eventstream.adapters.pulsar import (
    EncryptionUnavailable,
    PulsarAdapterError,
    PulsarEventBus,
    native_json_schema,
)
from omega_prime.eventstream.config import EventStreamConfig
from omega_prime.eventstream.envelope import CloudEvent, Delivery, PublishRequest
from omega_prime.eventstream.errors import ContractError
from omega_prime.eventstream.ports import UnsupportedSubscriptionMode


class FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, dict[str, Any]]] = []
        self.closed = False

    async def send(self, payload: bytes, **options: Any) -> str:
        self.sent.append((payload, options))
        return "ack-id"

    async def close(self) -> None:
        self.closed = True


class FakeConsumer:
    def __init__(self) -> None:
        self.messages: list[FakeMessage] = []
        self.acknowledged: list[FakeMessage] = []
        self.closed = False

    async def receive(self) -> FakeMessage:
        return self.messages.pop(0)

    async def acknowledge(self, message: FakeMessage) -> None:
        self.acknowledged.append(message)

    async def close(self) -> None:
        self.closed = True


class FakeReader:
    def __init__(self) -> None:
        self.messages: list[FakeMessage] = []
        self.seeks: list[Any] = []
        self.closed = False

    async def has_message_available(self) -> bool:
        return bool(self.messages)

    async def read_next(self) -> FakeMessage:
        return self.messages.pop(0)

    async def seek(self, position: Any) -> None:
        self.seeks.append(position)

    async def close(self) -> None:
        self.closed = True


class FakeMessage:
    def __init__(self, event: CloudEvent, *, key: str = "owner/repo#1") -> None:
        self._payload = event.to_bytes()
        self._key = key

    def data(self) -> bytes:
        return self._payload

    def partition_key(self) -> str:
        return self._key

    def topic_name(self) -> str:
        return "persistent://omega/sdlc/pr"

    def message_id(self) -> str:
        return "ledger:0:entry:1"

    def redelivery_count(self) -> int:
        return 1

    def properties(self) -> dict[str, str]:
        return {"it": "true", "ce_id": "event-1"}


class FakeClient:
    def __init__(self) -> None:
        self.created: list[tuple[str, dict[str, Any]]] = []
        self.producers: list[FakeProducer] = []
        self.subscriptions: list[tuple[str, str, dict[str, Any]]] = []
        self.consumers: list[FakeConsumer] = []
        self.readers: list[tuple[str, dict[str, Any], FakeReader]] = []
        self.closed = False

    async def create_producer(self, topic: str, **options: Any) -> FakeProducer:
        self.created.append((topic, options))
        producer = FakeProducer()
        self.producers.append(producer)
        return producer

    async def subscribe(
        self, topic: str, subscription: str, **options: Any
    ) -> FakeConsumer:
        self.subscriptions.append((topic, subscription, options))
        consumer = FakeConsumer()
        self.consumers.append(consumer)
        return consumer

    async def create_reader(self, topic: str, **options: Any) -> FakeReader:
        reader = FakeReader()
        self.readers.append((topic, options, reader))
        return reader

    async def close(self) -> None:
        self.closed = True


def _event(event_type: str = "omega.sdlc.pr.pushed") -> CloudEvent:
    return CloudEvent(
        specversion="1.0",
        id="event-1",
        source="https://example.test",
        type=event_type,
        subject="pr/1",
        datacontenttype="application/json",
        dataschema=f"urn:omega:schema:{event_type}:1",
        data={
            "repo": "owner/repo",
            "number": 1,
            "head_sha": "a" * 40,
            "actor": "tester",
            "before_sha": "b" * 40,
        },
    )


def _event_for_topic(topic: str) -> CloudEvent:
    """Build a catalog-valid event whose type belongs on ``topic``."""
    if (
        topic == "persistent://omega/sdlc/pr"
        or topic.startswith("persistent://omega/system/retry.")
        or topic.startswith("persistent://omega/system/dlq.")
    ):
        return _event()
    if topic == "persistent://omega/sdlc/review":
        return CloudEvent(
            specversion="1.0",
            id="event-1",
            source="https://example.test",
            type="omega.sdlc.review.greptile_scored",
            subject="pr/1",
            datacontenttype="application/json",
            dataschema="urn:omega:schema:omega.sdlc.review.greptile_scored:1",
            data={
                "repo": "owner/repo",
                "number": 1,
                "head_sha": "a" * 40,
                "actor": "tester",
                "score": 5,
            },
        )
    raise ValueError(f"no fixture event for topic {topic}")


def _request(
    name: str = "stable",
    *,
    topic: str = "persistent://omega/sdlc/pr",
    properties: dict[str, object] | None = None,
    deliver_after: datetime | None = None,
) -> PublishRequest:
    return PublishRequest(
        event=_event_for_topic(topic),
        topic=topic,
        key="owner/repo#1",
        sequence_id=8,
        producer_name=name,
        deliver_after=deliver_after,
        properties=properties or {},
    )


def _bus(native: FakeClient, **options: Any) -> PulsarEventBus:
    return PulsarEventBus(
        EventStreamConfig(),
        native_client=native,
        acknowledged_watermark=lambda _topic, _name: 7,
        **options,
    )


@pytest.mark.asyncio
async def test_publish_maps_bytes_sequence_schema_properties_and_ack() -> None:
    native = FakeClient()
    wakes: list[str] = []
    bus = _bus(native, relay_wake=lambda: wakes.append("wake"))
    request = _request(properties={"omega_attempt": 2, "ce_id": "caller-value"})

    receipt = await bus.publish(request)

    assert receipt.message_id == "ack-id"
    assert receipt.event_id == request.event.id
    assert native.created[0][1]["initial_sequence_id"] == 7
    assert native.created[0][1]["producer_name"] == "stable"
    assert native.created[0][1]["send_timeout_millis"] == 0
    assert native.created[0][1]["batching_enabled"] is False
    schema = native.created[0][1]["schema"]
    assert schema.encode(request.event.to_bytes()) == request.event.to_bytes()
    assert schema.decode(request.event.to_bytes()) == request.event.to_bytes()
    payload, send_options = native.producers[0].sent[0]
    assert payload == request.event.to_bytes()
    assert send_options["sequence_id"] == 8
    assert send_options["partition_key"] == "owner/repo#1"
    assert send_options["properties"]["ce_id"] == "event-1"
    assert send_options["properties"]["omega_attempt"] == "2"
    assert send_options["properties"]["content_type"] == "application/cloudevents+json"
    assert wakes
    await bus.close()
    assert native.closed


@pytest.mark.asyncio
async def test_producer_cache_includes_topic_name_and_encryption_mode() -> None:
    native = FakeClient()
    bus = _bus(native)
    await bus.publish(_request("first"))
    await bus.publish(_request("first"))
    await bus.publish(_request("second"))
    await bus.publish(_request("first", topic="persistent://omega/sdlc/review"))
    assert len(native.created) == 3
    await bus.close()


@pytest.mark.asyncio
async def test_send_error_wakes_transport_waiter_and_sanitizes_error() -> None:
    class BrokenProducer(FakeProducer):
        async def send(self, payload: bytes, **options: Any) -> str:
            raise OSError("token=top-secret")

    class BrokenClient(FakeClient):
        async def create_producer(self, topic: str, **options: Any) -> BrokenProducer:
            producer = BrokenProducer()
            self.producers.append(producer)
            return producer

    relay_wakes: list[None] = []
    bus = _bus(BrokenClient(), relay_wake=lambda: relay_wakes.append(None))
    waiter = asyncio.create_task(bus.wait_transport_change())
    with pytest.raises(PulsarAdapterError, match="Pulsar publish failed") as error:
        await bus.publish(_request())
    assert "top-secret" not in str(error.value)
    assert relay_wakes
    await asyncio.wait_for(waiter, timeout=0.1)
    await bus.close()


@pytest.mark.asyncio
async def test_cancelled_publish_keeps_native_send_and_completion_wakes_relay() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    relay_wakes: asyncio.Queue[None] = asyncio.Queue()

    class PendingProducer(FakeProducer):
        async def send(self, payload: bytes, **options: Any) -> str:
            self.sent.append((payload, options))
            started.set()
            await release.wait()
            return "completed-after-cancel"

    class PendingClient(FakeClient):
        async def create_producer(self, topic: str, **options: Any) -> PendingProducer:
            self.created.append((topic, options))
            producer = PendingProducer()
            self.producers.append(producer)
            return producer

    bus = _bus(PendingClient(), relay_wake=lambda: relay_wakes.put_nowait(None))
    publish = asyncio.create_task(bus.publish(_request()))
    await started.wait()
    publish.cancel()
    with pytest.raises(asyncio.CancelledError):
        await publish

    assert len(bus._pending_sends) == 1
    assert bus._producers[("persistent://omega/sdlc/pr", "stable", False)]
    client = cast(FakeClient, bus._client)
    assert client.created[0][1]["send_timeout_millis"] == 0
    assert not relay_wakes.empty()
    while not relay_wakes.empty():
        relay_wakes.get_nowait()
    release.set()
    await asyncio.wait_for(relay_wakes.get(), timeout=0.1)
    assert not bus._pending_sends
    await bus.close()


@pytest.mark.asyncio
async def test_initial_sequence_requires_durable_watermark() -> None:
    bus = PulsarEventBus(EventStreamConfig(), native_client=FakeClient())
    with pytest.raises(PulsarAdapterError, match="persisted acknowledged-watermark"):
        await bus.publish(_request())
    await bus.close()


@pytest.mark.asyncio
async def test_delayed_send_is_native_only_on_retry_topics() -> None:
    native = FakeClient()
    bus = _bus(native)
    delayed = _request(
        topic="persistent://omega/system/retry.gate",
        properties={
            "omega_attempt": 1,
            "omega_origin_topic": "persistent://omega/sdlc/pr",
        },
        deliver_after=datetime.now(UTC) + timedelta(seconds=3),
    )
    await bus.publish(delayed)
    assert native.producers[0].sent[0][1]["deliver_at"] > int(
        datetime.now(UTC).timestamp() * 1000
    )
    with pytest.raises(UnsupportedSubscriptionMode):
        await bus.publish(
            _request(deliver_after=datetime.now(UTC) + timedelta(seconds=1))
        )
    assert len(native.created) == 1
    await bus.close()


@pytest.mark.asyncio
async def test_auth_payload_requires_crypto_even_on_retry_destination() -> None:
    native = FakeClient()
    bus = _bus(native)
    for destination in (
        "persistent://omega/system/retry.gate",
        "persistent://omega/system/dlq.gate",
    ):
        request = _request(
            topic=destination,
            properties={
                "omega_attempt": 1,
                "omega_origin_topic": "persistent://omega/auth/session",
            },
        )
        with pytest.raises(EncryptionUnavailable, match="encryption keys"):
            await bus.publish(request)
    assert native.created == []
    await bus.close()


@pytest.mark.asyncio
async def test_auth_crypto_files_are_revalidated_and_bound_to_producer(
    tmp_path: Path,
) -> None:
    public = tmp_path / "auth-public.pem"
    private = tmp_path / "auth-private.pem"
    public.write_text("public-key", encoding="utf-8")
    private.write_text("private-key", encoding="utf-8")
    public.chmod(0o600)
    private.chmod(0o600)
    config = EventStreamConfig(encryption_key_file=public, private_key_file=private)
    native = FakeClient()
    bus = PulsarEventBus(
        config,
        native_client=native,
        acknowledged_watermark=lambda _topic, _name: 0,
    )
    request = _request(
        topic="persistent://omega/system/retry.gate",
        properties={
            "omega_attempt": 1,
            "omega_origin_topic": "persistent://omega/auth/session",
        },
    )
    await bus.publish(request)
    options = native.created[0][1]
    assert options["encryption_key"] == "omega-auth"
    assert options["crypto_key_reader"] is not None
    plain_request = _request(
        topic="persistent://omega/system/retry.gate",
        properties={"omega_attempt": 1},
    )
    await bus.publish(plain_request)
    assert len(native.created) == 2
    public.chmod(0o644)
    with pytest.raises(EncryptionUnavailable, match="0600"):
        await bus.subscribe(
            "persistent://omega/auth/session", "auth-sub", subscription_type="Shared"
        )
    await bus.close()


@pytest.mark.asyncio
async def test_redelivery_consumers_require_auth_decryption_keys() -> None:
    pytest.importorskip("pulsar")

    native = FakeClient()
    bus = _bus(native)
    with pytest.raises(EncryptionUnavailable, match="encryption keys"):
        await bus.subscribe(
            "persistent://omega/system/retry.gate",
            "retry-sub",
            subscription_type="Shared",
        )
    with pytest.raises(EncryptionUnavailable, match="encryption keys"):
        await bus.read("persistent://omega/system/dlq.gate")
    assert native.subscriptions == []
    assert native.readers == []
    await bus.close()


@pytest.mark.asyncio
async def test_subscription_modes_individual_ack_and_reader_seek() -> None:
    import pulsar

    native = FakeClient()
    bus = _bus(native)
    expected = {
        "Shared": pulsar.ConsumerType.Shared,
        "Key_Shared": pulsar.ConsumerType.KeyShared,
        "Failover": pulsar.ConsumerType.Failover,
        "Exclusive": pulsar.ConsumerType.Exclusive,
    }
    for mode, native_mode in expected.items():
        subscriber = await bus.subscribe(
            "persistent://omega/sdlc/pr",
            f"sub-{mode}",
            subscription_type=cast(Any, mode),
            receiver_queue_size=17,
        )
        options = native.subscriptions[-1][2]
        assert options["consumer_type"] == native_mode
        assert options["initial_position"] == pulsar.InitialPosition.Earliest
        assert options["receiver_queue_size"] == 17
        assert options["batch_index_ack_enabled"] is True
        assert options["negative_ack_redelivery_delay_ms"] == 1000
        message = FakeMessage(_event())
        native.consumers[-1].messages.append(message)
        delivery = await subscriber.receive()
        assert isinstance(delivery, Delivery)
        assert delivery.redelivered is True
        assert delivery.properties["it"] == "true"
        await delivery.acknowledge()
        assert native.consumers[-1].acknowledged == [message]
        await subscriber.close()
    policy = native.subscriptions[1][2]["key_shared_policy"]
    assert policy.key_shared_mode == pulsar.KeySharedMode.AutoSplit
    with pytest.raises(UnsupportedSubscriptionMode, match="Shared or Key_Shared"):
        await bus.subscribe(
            "persistent://omega/system/retry.gate",
            "singleton",
            subscription_type="Failover",
        )

    reader = await bus.read("persistent://omega/sdlc/pr")
    native_reader = native.readers[-1][2]
    message = FakeMessage(_event())
    native_reader.messages.append(message)
    maybe_delivery = await reader.read_nowait()
    assert maybe_delivery is not None
    delivery = maybe_delivery
    assert delivery.key == "owner/repo#1"
    await delivery.acknowledge()
    assert not native_reader.seeks
    native_reader_api = cast(Any, reader)
    await native_reader_api.seek(2)
    await native_reader_api.seek_time(datetime.now(UTC))
    assert len(native_reader.seeks) == 2
    await reader.close()
    await bus.close()


def test_native_metadata_has_stable_typed_payload_branches() -> None:
    topic_schema = native_json_schema("persistent://omega/sdlc/pr")
    assert topic_schema == native_json_schema("persistent://omega/sdlc/pr")
    assert topic_schema["type"] == "record"
    assert "$schema" not in topic_schema
    fields = topic_schema["fields"]
    assert isinstance(fields, list)
    data_field = next(field for field in fields if field["name"] == "data")
    branches = data_field["type"]
    assert isinstance(branches, list)
    assert len(branches) == 6
    pushed = next(
        branch
        for branch in branches
        if branch["name"] == "Payload_omega_sdlc_pr_pushed"
    )
    payload_fields = pushed["fields"]
    assert isinstance(payload_fields, list)
    number_field = next(field for field in payload_fields if field["name"] == "number")
    assert number_field["type"] == "long"
    audit_schema = native_json_schema("persistent://omega/audit/log")
    assert "Payload_omega_audit__" in str(audit_schema)
    audit_fields_raw = audit_schema["fields"]
    assert isinstance(audit_fields_raw, list)
    audit_data = next(
        field
        for field in audit_fields_raw
        if isinstance(field, dict) and field.get("name") == "data"
    )["type"]
    assert isinstance(audit_data, dict)
    audit_fields = audit_data["fields"]
    details = next(field for field in audit_fields if field["name"] == "details")
    details_type = details["type"]
    if isinstance(details_type, list):
        assert "null" in details_type
        details_type = next(item for item in details_type if item != "null")
    assert details_type == {
        "type": "map",
        "values": ["string", "long", "boolean"],
    }
    assert native_json_schema("persistent://omega/sdlc/pr-state")["type"] == "record"
    assert (
        native_json_schema("persistent://omega/system/retry.gate")["type"] == "record"
    )
    with pytest.raises(ValueError, match="no registered"):
        native_json_schema("persistent://omega/no-topic")


def test_codec_rejects_invalid_payload_and_preserves_structured_bytes() -> None:
    from omega_prime.eventstream.adapters.pulsar import _Codec

    event = _event()
    encoded = _Codec().encode(event)
    assert _Codec().decode(encoded).to_bytes() == encoded
    invalid = b'{"specversion":"1.0","type":"omega.sdlc.pr.pushed","data":{}}'
    with pytest.raises(ContractError):
        _Codec().decode(invalid)


def test_logical_topic_strips_partition_suffix() -> None:
    from omega_prime.eventstream.adapters.pulsar import _logical_topic

    assert (
        _logical_topic("persistent://omega/sdlc/pr-partition-1")
        == "persistent://omega/sdlc/pr"
    )
    assert _logical_topic("persistent://omega/sdlc/pr") == "persistent://omega/sdlc/pr"
    assert (
        _logical_topic("persistent://omega/sdlc/pr-partition-x")
        == "persistent://omega/sdlc/pr-partition-x"
    )


def test_avro_metadata_edges_and_tls_client_options(tmp_path: Path) -> None:
    from omega_prime.eventstream.adapters.pulsar import (
        PulsarAdapterError,
        _avro_name,
        _avro_type,
        _client_options,
        _infer_const_avro_type,
        _logical_topic,
    )

    assert _avro_name("omega.sdlc.pr") == "omega_sdlc_pr"
    assert _avro_name("9bad").startswith("_")
    assert _infer_const_avro_type(None) == "null"
    assert _infer_const_avro_type(True) == "boolean"
    assert _infer_const_avro_type(3) == "long"
    assert _infer_const_avro_type(1.5) == "double"
    assert _infer_const_avro_type("x") == "string"
    with pytest.raises(ValueError, match="unsupported JSON Schema const"):
        _infer_const_avro_type([1])
    with pytest.raises(ValueError, match="unsupported enum"):
        _avro_type({"enum": []}, "field")
    with pytest.raises(ValueError, match="mixed enum"):
        _avro_type({"enum": [1, "x"]}, "field")
    with pytest.raises(ValueError, match="unsupported JSON Schema type"):
        _avro_type({}, "field")
    assert _avro_type({"const": "opened"}, "status") == "string"
    assert _avro_type({"enum": [1, 2]}, "n") == "long"
    assert _avro_type({"type": ["null", "string"]}, "opt") == ["null", "string"]
    mapped = _avro_type(
        {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "properties": {},
        },
        "labels",
    )
    assert mapped == {"type": "map", "values": "string"}
    with pytest.raises(ValueError, match="unsupported object property"):
        _avro_type(
            {"type": "object", "properties": {1: {"type": "string"}}, "required": []},  # type: ignore[dict-item]
            "bad",
        )

    assert (
        _logical_topic("persistent://omega/sdlc/pr-partition-3")
        == "persistent://omega/sdlc/pr"
    )

    class _TlsMissing:
        broker_url = "pulsar+ssl://broker.example:6651"
        tls_ca_file = None
        token_file = None

        def read_secret(self, name: str) -> str:
            raise AssertionError(name)

    with pytest.raises(PulsarAdapterError, match="CA file"):
        _client_options(_TlsMissing())  # type: ignore[arg-type]

    ca = tmp_path / "ca.pem"
    ca.write_text("ca", encoding="utf-8")
    ca.chmod(0o600)
    token = tmp_path / "token"
    token.write_text("tok", encoding="utf-8")
    token.chmod(0o600)

    class _TlsReady:
        broker_url = "pulsar+ssl://broker.example:6651"
        tls_ca_file = ca
        token_file = token

        def read_secret(self, name: str) -> str:
            assert name == "token"
            return "tok"

    url, options = _client_options(_TlsReady())  # type: ignore[arg-type]
    assert url.startswith("pulsar+ssl://")
    assert options["tls_trust_certs_file_path"] == str(ca)
    assert options["tls_validate_hostname"] is True


def test_native_json_schema_builds_for_known_topics() -> None:
    from omega_prime.eventstream.adapters.pulsar import native_json_schema

    schema = native_json_schema("persistent://omega/sdlc/pr")
    assert schema["type"] == "record"
    name = schema["name"]
    assert isinstance(name, str) and name.startswith("OmegaCloudEvent")
    schema_fields = schema["fields"]
    assert isinstance(schema_fields, list)
    fields = {
        field["name"]
        for field in schema_fields
        if isinstance(field, dict) and isinstance(field.get("name"), str)
    }
    assert {"specversion", "id", "source", "type", "data"}.issubset(fields)
    review = native_json_schema("persistent://omega/sdlc/review")
    assert review["type"] == "record"


def test_pulsar_helper_predicates_and_property_encoding() -> None:
    from omega_prime.eventstream.adapters.pulsar import (
        PulsarAdapterError,
        _delayed_topic_supported,
        _is_system_redelivery_topic,
        _is_system_retry_topic,
        _message_property,
        _requires_encryption,
        _sanitized,
    )
    from omega_prime.eventstream.envelope import _FrozenMap

    assert _is_system_retry_topic("persistent://omega/system/retry.gate")
    assert _is_system_redelivery_topic("persistent://omega/system/dlq.gate")
    assert not _is_system_retry_topic("persistent://omega/sdlc/pr")
    assert _delayed_topic_supported("persistent://omega/control/deadline")
    assert not _delayed_topic_supported("persistent://omega/sdlc/pr")
    assert _message_property("plain") == "plain"
    assert _message_property(None) == "None"
    assert _message_property(True) == "True"
    assert '"a":1' in _message_property(_FrozenMap({"a": 1}))
    err = _sanitized("boom", RuntimeError("token=sk-secret"))
    assert isinstance(err, PulsarAdapterError)
    assert "sk-secret" not in str(err)
    auth_req = _request(
        "auth-retry",
        topic="persistent://omega/system/retry.gate",
        properties={
            "omega_origin_topic": "persistent://omega/auth/session",
            "omega_attempt": 1,
        },
    )
    assert _requires_encryption(auth_req) is True
    plain = _request("plain")
    assert _requires_encryption(plain) is False
