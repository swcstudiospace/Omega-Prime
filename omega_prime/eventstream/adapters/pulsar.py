"""Thin, lazily-loaded Pulsar asyncio transport for eventstream ports."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast

from omega_prime.credentials.redact import redact_value
from omega_prime.eventstream.backoff import create_with_backoff
from omega_prime.eventstream.config import EventStreamConfig
from omega_prime.eventstream.envelope import (
    CloudEvent,
    Delivery,
    PublishReceipt,
    PublishRequest,
)
from omega_prime.eventstream.errors import (
    AuthenticationError,
    EventStreamError,
    PermanentError,
    SchemaError,
)
from omega_prime.eventstream.ports import (
    InitialPosition,
    Reader,
    Subscriber,
    SubscriptionType,
    UnsupportedSubscriptionMode,
)
from omega_prime.eventstream.taxonomy import (
    EVENT_TYPES,
    get_event_type,
    resolve_schema_uri,
    validate_payload,
)


class PulsarAdapterError(EventStreamError):
    """Sanitized Pulsar adapter failure."""


class EncryptionUnavailable(PermanentError):
    """Auth-family transport lacks its mandatory payload encryption material."""


Watermark = Callable[[str, str], int | Awaitable[int]]
WakeCallback = Callable[[], None]


class _Codec:
    """Validate CloudEvents at both SDK boundaries and preserve structured bytes."""

    def encode(self, event: CloudEvent) -> bytes:
        validate_payload(event.type, event.data)
        return event.to_bytes()

    def decode(self, payload: bytes) -> CloudEvent:
        event = CloudEvent.from_bytes(payload)
        validate_payload(event.type, event.data)
        return event


def _pr_state_native_schema() -> dict[str, object]:
    """Build deterministic native Avro metadata for the compacted PR view."""
    review_fields = [
        (
            "greptile_scored",
            "GreptileScored",
            [{"name": "score", "type": "long"}],
        ),
        ("approved", "Approved", [{"name": "reviewer", "type": "string"}]),
        (
            "changes_requested",
            "ChangesRequested",
            [
                {"name": "reviewer", "type": "string"},
                {"name": "summary", "type": "string"},
            ],
        ),
        (
            "commented",
            "Commented",
            [
                {"name": "reviewer", "type": "string"},
                {"name": "comment_id", "type": "string"},
            ],
        ),
    ]
    review = {
        "type": "record",
        "name": "ReviewState",
        "fields": [
            {
                "name": field_name,
                "type": [
                    "null",
                    {"type": "record", "name": record_name, "fields": fields},
                ],
                "default": None,
            }
            for field_name, record_name, fields in review_fields
        ],
    }
    ci = {
        "type": "record",
        "name": "CiState",
        "fields": [
            {
                "name": "status",
                "type": {
                    "type": "enum",
                    "name": "CiStatus",
                    "symbols": ["in_progress", "passed", "failed"],
                },
            },
            {"name": "event_type", "type": "string"},
            {"name": "conclusion", "type": ["null", "string"], "default": None},
        ],
    }
    return {
        "type": "record",
        "name": "OmegaPrState",
        "namespace": "omega.eventstream",
        "fields": [
            {"name": "id", "type": "string"},
            {"name": "key", "type": "string"},
            {"name": "repo", "type": "string"},
            {"name": "number", "type": "long"},
            {"name": "head_sha", "type": "string"},
            {
                "name": "status",
                "type": {
                    "type": "enum",
                    "name": "PrStatus",
                    "symbols": ["open", "closed", "merged"],
                },
            },
            {"name": "draft", "type": "boolean"},
            {"name": "closed", "type": "boolean"},
            {"name": "merged", "type": "boolean"},
            {"name": "review", "type": {"type": "map", "values": review}},
            {"name": "ci", "type": {"type": "map", "values": ci}},
        ],
    }


def _avro_name(value: str) -> str:
    """Normalize names for the Avro identifier grammar deterministically."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not name or (not name[0].isalpha() and name[0] != "_"):
        name = "_" + name
    return name


def _infer_const_avro_type(value: object) -> str:
    """Infer the Avro primitive that carries a JSON Schema const/enum value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "long"
    if isinstance(value, float):
        return "double"
    if isinstance(value, str):
        return "string"
    raise ValueError("unsupported JSON Schema const for native Pulsar metadata")


def _avro_type(schema: Mapping[str, Any], name: str) -> object:
    """Map supported JSON Schema value types to native Avro field types."""
    schema_type = schema.get("type")
    if schema_type is None:
        if "const" in schema:
            return _infer_const_avro_type(schema["const"])
        if "enum" in schema:
            values = schema["enum"]
            if not isinstance(values, list) or not values:
                raise ValueError("unsupported enum schema for native Pulsar metadata")
            inferred = {_infer_const_avro_type(item) for item in values}
            if len(inferred) != 1:
                raise ValueError(
                    "unsupported mixed enum schema for native Pulsar metadata"
                )
            return next(iter(inferred))
        alternatives = schema.get("oneOf")
        if alternatives is None:
            alternatives = schema.get("anyOf")
        if (
            isinstance(alternatives, list)
            and alternatives
            and all(isinstance(item, Mapping) for item in alternatives)
        ):
            mapped = [
                _avro_type(item, f"{name}_alt{index}")
                for index, item in enumerate(alternatives)
            ]
            return mapped[0] if len(mapped) == 1 else mapped
        raise ValueError("unsupported JSON Schema type for native Pulsar metadata")
    if isinstance(schema_type, list):
        return [_avro_type({"type": item}, name) for item in schema_type]
    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, Mapping) or not isinstance(required, list):
            raise ValueError("unsupported object schema for native Pulsar metadata")
        additional_properties = schema.get("additionalProperties", False)
        if not properties and isinstance(additional_properties, Mapping):
            alternatives = additional_properties.get("oneOf")
            if alternatives is None:
                value_type = _avro_type(additional_properties, f"{name}_value")
            elif isinstance(alternatives, list) and all(
                isinstance(item, Mapping) for item in alternatives
            ):
                mapped_types = [
                    _avro_type(item, f"{name}_value") for item in alternatives
                ]
                value_type = mapped_types[0] if len(mapped_types) == 1 else mapped_types
            else:
                raise ValueError("unsupported map schema for native Pulsar metadata")
            return {"type": "map", "values": value_type}
        fields: list[dict[str, object]] = []
        for field_name, field_schema in properties.items():
            if not isinstance(field_name, str) or not isinstance(field_schema, Mapping):
                raise ValueError(
                    "unsupported object property in native Pulsar metadata"
                )
            field_type = _avro_type(field_schema, f"{name}_{field_name}")
            if field_name not in required:
                field_type = ["null", field_type]
            fields.append(
                {
                    "name": _avro_name(field_name),
                    "type": field_type,
                    **({"default": None} if field_name not in required else {}),
                }
            )
        return {
            "type": "record",
            "name": _avro_name(name),
            "fields": fields,
        }
    if schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise ValueError("unsupported array schema for native Pulsar metadata")
        return {"type": "array", "items": _avro_type(items, f"{name}_item")}
    native_types = {
        "string": "string",
        "integer": "long",
        "number": "double",
        "boolean": "boolean",
        "null": "null",
    }
    try:
        return native_types[schema_type]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "unsupported JSON Schema type for native Pulsar metadata"
        ) from exc


def _payload_record(event_type: str, schema: Mapping[str, Any]) -> dict[str, object]:
    """Build one deterministic Avro union branch for an event payload."""
    payload_type = _avro_type(schema, f"Payload_{event_type}")
    if isinstance(payload_type, dict) and payload_type.get("type") == "record":
        return cast(dict[str, object], payload_type)
    return {
        "type": "record",
        "name": _avro_name(f"Payload_{event_type}"),
        "fields": [{"name": "value", "type": payload_type}],
    }


def native_json_schema(topic: str) -> dict[str, object]:
    """Build deterministic Avro-format JSON schema metadata for one topic."""
    if topic == "persistent://omega/sdlc/pr-state":
        return _pr_state_native_schema()
    event_types = sorted(
        (name, event) for name, event in EVENT_TYPES.items() if event.topic == topic
    )
    is_system_redelivery = bool(
        re.fullmatch(r"persistent://omega/system/(?:retry|dlq)\.[^/]+", topic)
    )
    if is_system_redelivery:
        event_types = sorted(EVENT_TYPES.items())
    if not event_types and topic != "persistent://omega/audit/log":
        raise ValueError("topic has no registered event payload schemas")

    payload_branches: list[object] = [
        _payload_record(name, resolve_schema_uri(event.schema_ref))
        for name, event in event_types
    ]
    if topic == "persistent://omega/audit/log" or is_system_redelivery:
        payload_branches.append(
            _payload_record(
                "omega.audit.*",
                resolve_schema_uri("urn:omega:schema:omega.audit.*:1"),
            )
        )
    payload_type: object = (
        payload_branches[0] if len(payload_branches) == 1 else payload_branches
    )

    fields: list[dict[str, object]] = []
    required = {
        "specversion",
        "id",
        "source",
        "type",
        "datacontenttype",
        "dataschema",
        "data",
    }
    envelope_fields = {
        "specversion": "string",
        "id": "string",
        "source": "string",
        "type": "string",
        "subject": "string",
        "time": "string",
        "datacontenttype": "string",
        "dataschema": "string",
        "traceparent": "string",
        "omegakey": "string",
        "omegatask": "string",
        "omegagraph": "string",
        "omegaheadsha": "string",
    }
    for field_name, field_type in envelope_fields.items():
        field: dict[str, object] = {
            "name": field_name,
            "type": field_type if field_name in required else ["null", field_type],
        }
        if field_name not in required:
            field["default"] = None
        fields.append(field)
    fields.append({"name": "data", "type": payload_type})

    topic_name = topic.rsplit("/", 1)[-1]
    return {
        "type": "record",
        "name": "OmegaCloudEvent_" + _avro_name(topic_name),
        "namespace": "omega.eventstream",
        "fields": fields,
    }


def _native_schema_option(topic: str) -> Any:
    """Create a Pulsar JSON Schema with a byte-exact structured JSON codec."""
    try:
        import _pulsar  # type: ignore[import-not-found,unused-ignore]
        import pulsar  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:
        raise PulsarAdapterError(
            "pulsar-client optional dependency is unavailable"
        ) from exc

    _Schema = cast(type[Any], pulsar.schema.Schema)

    class ExactJsonSchema(_Schema):  # type: ignore[valid-type,misc]
        def __init__(self) -> None:
            definition = native_json_schema(topic)
            schema_name = str(definition["name"])
            super().__init__(
                bytes,
                _pulsar.SchemaType.JSON,
                definition,
                schema_name,
            )

        def encode(self, obj: Any) -> bytes:
            if not isinstance(obj, bytes):
                raise TypeError("structured CloudEvents must be encoded as bytes")
            return obj

        def decode(self, data: bytes) -> bytes:
            if not isinstance(data, bytes):
                raise TypeError("structured CloudEvents must be decoded from bytes")
            return data

    return ExactJsonSchema()


def _is_retryable_pulsar_error(error: Exception) -> bool:
    if isinstance(error, OSError):
        return True
    try:
        import pulsar  # type: ignore[import-not-found,unused-ignore]
    except ImportError:
        return False
    names = ("ConnectError", "NotConnected", "Timeout")
    retryable = tuple(
        cls
        for cls in (getattr(pulsar, name, None) for name in names)
        if isinstance(cls, type)
    )
    if retryable and isinstance(error, retryable):
        return True
    # pulsar.asyncio raises one PulsarException class carrying a Result code.
    return _pulsar_result_name(error) in names


def _pulsar_result_name(error: BaseException) -> str | None:
    """Return the native ``pulsar.Result`` name carried by an asyncio error."""
    result_of = getattr(error, "error", None)
    if not callable(result_of):
        return None
    try:
        result = result_of()
    except Exception:
        return None
    name = getattr(result, "name", None)
    return name if isinstance(name, str) else None


def _is_system_retry_topic(topic: str) -> bool:
    return topic.startswith("persistent://omega/system/retry.")


def _is_system_redelivery_topic(topic: str) -> bool:
    return topic.startswith(
        ("persistent://omega/system/retry.", "persistent://omega/system/dlq.")
    )


def _requires_encryption(request: PublishRequest) -> bool:
    origin = request.properties.get("omega_origin_topic", "")
    return request.event.type.startswith("omega.auth.") or (
        _is_system_redelivery_topic(request.topic)
        and isinstance(origin, str)
        and origin.startswith("persistent://omega/auth/")
    )


def _crypto_reader(config: EventStreamConfig) -> Any:
    if config.encryption_key_file is None or config.private_key_file is None:
        raise EncryptionUnavailable("auth payload encryption keys are not configured")
    try:
        # Reopen and validate both files on every protected operation. The
        # native reader reads the key paths when Pulsar encrypts/decrypts data.
        config.read_secret("encryption_key")
        config.read_secret("private_key")
        import pulsar  # type: ignore[import-not-found,unused-ignore]

        return pulsar.CryptoKeyReader(
            str(config.encryption_key_file), str(config.private_key_file)
        )
    except EncryptionUnavailable:
        raise
    except Exception as exc:
        raise EncryptionUnavailable(
            str(
                redact_value(
                    f"Pulsar encryption keys are unavailable: {type(exc).__name__}: {exc}"
                )
            )
        ) from None


def _auth_options(config: EventStreamConfig, pulsar: Any) -> dict[str, object]:
    if config.token_file is None:
        return {}
    return {
        "authentication": pulsar.AuthenticationToken(
            lambda: config.read_secret("token")
        )
    }


def _client_options(config: EventStreamConfig) -> tuple[str, dict[str, object]]:
    try:
        import pulsar  # type: ignore[import-not-found,unused-ignore]
        from pulsar import asyncio as pulsar_asyncio
    except ImportError as exc:
        raise PulsarAdapterError(
            "pulsar-client optional dependency is unavailable"
        ) from exc
    _ = pulsar_asyncio
    options = _auth_options(config, pulsar)
    if config.broker_url.startswith("pulsar+ssl://"):
        if config.tls_ca_file is None:
            raise PulsarAdapterError("TLS broker requires a configured CA file")
        options.update(
            {
                "tls_trust_certs_file_path": str(config.tls_ca_file),
                "tls_validate_hostname": True,
                "tls_allow_insecure_connection": False,
            }
        )
    return config.broker_url, options


def _logical_topic(topic: str) -> str:
    """Map a Pulsar partition topic name back to its logical topic."""
    if "-partition-" not in topic:
        return topic
    head, _, tail = topic.rpartition("-partition-")
    if tail.isdigit():
        return head
    return topic


def _message_id(position: int) -> Any:
    try:
        import pulsar  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:
        raise PulsarAdapterError(
            "pulsar-client optional dependency is unavailable"
        ) from exc
    # asyncio.Reader.seek requires the high-level pulsar.MessageId wrapper.
    if position == 0:
        wrapper = pulsar.MessageId()
        wrapper._msg_id = pulsar.MessageId.earliest
        return wrapper
    return pulsar.MessageId(
        partition=-1, ledger_id=0, entry_id=position, batch_index=-1
    )


def _is_incompatible_schema(error: BaseException) -> bool:
    """True when the native client rejected the payload against the topic schema."""
    if type(error).__name__ == "IncompatibleSchema":
        return True
    # pulsar.asyncio wraps every native failure in PulsarException(Result);
    # the schema rejection is only visible through its Result code.
    if _pulsar_result_name(error) == "IncompatibleSchema":
        return True
    try:
        import pulsar
    except ImportError:
        return False
    return isinstance(error, pulsar.IncompatibleSchema)


def _sanitized(context: str, error: Exception) -> PulsarAdapterError:
    return PulsarAdapterError(
        str(redact_value(f"{context}: {type(error).__name__}: {error}"))
    )


def _message_property(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return str(value)
    from omega_prime.eventstream.envelope import _thaw

    return json.dumps(
        _thaw(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _properties(request: PublishRequest) -> dict[str, str]:
    cleaned = redact_value(dict(request.properties))
    if not isinstance(cleaned, Mapping):
        raise PulsarAdapterError(
            "message properties did not remain an object after redaction"
        )
    properties = {
        str(key): _message_property(value)
        for key, value in cleaned.items()
        if not str(key).startswith("ce_")
    }
    properties["content_type"] = "application/cloudevents+json"
    properties.update(request.event.to_properties())
    return properties


def _delayed_topic_supported(topic: str) -> bool:
    return topic.startswith(
        (
            "persistent://omega/control/deadline",
            "persistent://omega/system/retry.",
        )
    )


def _consumer_type(pulsar: Any, value: str) -> Any:
    native_types = {
        "Exclusive": pulsar.ConsumerType.Exclusive,
        "Failover": pulsar.ConsumerType.Failover,
        "Shared": pulsar.ConsumerType.Shared,
        "Key_Shared": pulsar.ConsumerType.KeyShared,
    }
    try:
        return native_types[value]
    except KeyError as exc:
        raise UnsupportedSubscriptionMode(
            "unsupported Pulsar subscription type"
        ) from exc


class PulsarEventBus:
    """EventBus implementation over pulsar-client's asyncio API."""

    def __init__(
        self,
        config: EventStreamConfig,
        *,
        native_client: Any = None,
        creation_attempts: int = 5,
        backoff_wait: Callable[[float], Awaitable[None]] = asyncio.sleep,
        acknowledged_watermark: Watermark | None = None,
        relay_wake: WakeCallback | None = None,
    ) -> None:
        if (
            isinstance(creation_attempts, bool)
            or not isinstance(creation_attempts, int)
            or not 1 <= creation_attempts <= 5
        ):
            raise ValueError("producer creation attempts must be between one and five")
        self.config = config
        self._client = native_client
        self._creation_attempts = creation_attempts
        self._backoff_wait = backoff_wait
        self._acknowledged_watermark = acknowledged_watermark
        self._relay_wake = relay_wake
        self._producers: dict[tuple[str, str, bool], Any] = {}
        self._creation_locks: dict[tuple[str, str, bool], asyncio.Lock] = {}
        self._resources: set[Any] = set()
        self._pending_sends: set[asyncio.Task[Any]] = set()
        self._release_tasks: set[asyncio.Task[None]] = set()
        self._changed = asyncio.Event()
        self._closed = False
        self._codec = _Codec()

    def _notify(self, *, relay: bool = False) -> None:
        self._changed.set()
        if relay and self._relay_wake is not None:
            with contextlib.suppress(Exception):
                self._relay_wake()

    def _send_finished(self, task: asyncio.Task[Any]) -> None:
        self._pending_sends.discard(task)
        with contextlib.suppress(asyncio.CancelledError, Exception):
            task.exception()
        self._notify(relay=True)

    async def _client_instance(self) -> Any:
        if self._closed:
            raise PulsarAdapterError("Pulsar event bus is closed")
        if self._client is not None:
            return self._client
        try:
            from pulsar import (
                asyncio as pulsar_asyncio,
            )

            url, options = _client_options(self.config)
            self._client = pulsar_asyncio.Client(url, **options)
        except ImportError as exc:
            raise PulsarAdapterError(
                "pulsar-client optional dependency is unavailable"
            ) from exc
        except PulsarAdapterError:
            raise
        except Exception as exc:
            raise _sanitized("Pulsar client creation failed", exc) from None
        self._notify(relay=True)
        return self._client

    async def _producer(
        self,
        request: PublishRequest,
        *,
        encrypt: bool,
        initial_sequence: int | None = None,
    ) -> Any:
        identity = (request.topic, request.producer_name, encrypt)
        cached = self._producers.get(identity)
        if cached is not None:
            return cached
        lock = self._creation_locks.setdefault(identity, asyncio.Lock())
        async with lock:
            cached = self._producers.get(identity)
            if cached is not None:
                return cached
            watermark: object
            if initial_sequence is not None:
                # Adapter-owned single-use producers (poison quarantine) have
                # no outbox row, so they start from an explicit sequence.
                watermark = initial_sequence
            else:
                if self._acknowledged_watermark is None:
                    raise PulsarAdapterError(
                        "a persisted acknowledged-watermark callback is required"
                    )
                watermark_value = self._acknowledged_watermark(
                    request.topic, request.producer_name
                )
                watermark = (
                    await watermark_value
                    if inspect.isawaitable(watermark_value)
                    else watermark_value
                )
            if (
                isinstance(watermark, bool)
                or not isinstance(watermark, int)
                or watermark < 0
            ):
                raise PulsarAdapterError("persisted producer watermark is invalid")
            client = await self._client_instance()
            try:
                options: dict[str, object] = {
                    "producer_name": request.producer_name,
                    "initial_sequence_id": watermark,
                    "send_timeout_millis": 0,
                    "max_pending_messages": 1000,
                    "block_if_queue_full": True,
                    "batching_enabled": False,
                    "schema": _native_schema_option(request.topic),
                }
                if encrypt:
                    options.update(
                        {
                            "encryption_key": "omega-auth",
                            "crypto_key_reader": _crypto_reader(self.config),
                        }
                    )
                producer = await create_with_backoff(
                    lambda: client.create_producer(request.topic, **options),
                    attempts=self._creation_attempts,
                    wait=self._backoff_wait,
                    retryable=_is_retryable_pulsar_error,
                )
            except Exception as exc:
                self._notify(relay=True)
                if isinstance(exc, EncryptionUnavailable):
                    raise
                # The broker runs its schema compatibility check when the
                # producer registers, so an incompatible topic schema surfaces
                # here first. Keep it typed so the outbox rejects the row
                # instead of retrying it forever at the head of the queue.
                if _is_incompatible_schema(exc):
                    raise SchemaError(
                        str(
                            redact_value(
                                f"native schema rejected producer: {type(exc).__name__}: {exc}"
                            )
                        )[:256]
                    ) from None
                raise _sanitized("Pulsar producer creation failed", exc) from None
            self._producers[identity] = producer
            self._notify(relay=True)
            return producer

    async def publish(self, request: PublishRequest) -> PublishReceipt:
        return await self._send(request)

    async def _send(
        self, request: PublishRequest, *, initial_sequence: int | None = None
    ) -> PublishReceipt:
        if self._closed:
            raise PulsarAdapterError("Pulsar event bus is closed")
        encrypt = _requires_encryption(request)
        payload = self._codec.encode(request.event)
        if request.deliver_after is not None and not _delayed_topic_supported(
            request.topic
        ):
            raise UnsupportedSubscriptionMode(
                "delayed delivery is supported only on deadline or retry topics"
            )
        identity = (request.topic, request.producer_name, encrypt)
        try:
            producer = await self._producer(
                request, encrypt=encrypt, initial_sequence=initial_sequence
            )
            options: dict[str, object] = {
                "properties": _properties(request),
                "partition_key": request.key,
                "sequence_id": request.sequence_id,
            }
            if request.deliver_after is not None:
                options["deliver_at"] = int(request.deliver_after.timestamp() * 1000)
            send_task = asyncio.create_task(producer.send(payload, **options))
            self._pending_sends.add(send_task)
            send_task.add_done_callback(self._send_finished)
            try:
                message_id = await asyncio.shield(send_task)
            except asyncio.CancelledError:
                self._notify(relay=True)
                if request.single_use:
                    # Release the per-event producer once the shielded native
                    # send settles instead of leaking it until bus close.
                    send_task.add_done_callback(
                        lambda _done: self._schedule_release(identity)
                    )
                raise
            except Exception as exc:
                self._notify(relay=True)
                # Native schema rejection must surface as SchemaError so the
                # outbox marks the row rejected instead of retrying forever.
                if _is_incompatible_schema(exc):
                    raise SchemaError(
                        str(redact_value(str(exc)))[:256]
                        or "native schema rejected publish"
                    ) from None
                raise _sanitized("Pulsar publish failed", exc) from None
        except asyncio.CancelledError:
            # A cancelled caller leaves the native send running; the producer
            # must stay open until that send completes.
            raise
        except BaseException:
            if request.single_use:
                await self._release_producer(identity)
            raise
        if request.single_use:
            await self._release_producer(identity)
        self._notify(relay=True)
        return PublishReceipt(
            event_id=request.event.id,
            topic=request.topic,
            message_id=str(message_id),
            acknowledged_at=datetime.now(UTC),
        )

    def _schedule_release(self, identity: tuple[str, str, bool]) -> None:
        if self._closed:
            return
        task = asyncio.get_running_loop().create_task(self._release_producer(identity))
        self._release_tasks.add(task)
        task.add_done_callback(self._release_tasks.discard)

    async def _release_producer(self, identity: tuple[str, str, bool]) -> None:
        """Close a per-event producer so retries/DLQs do not accumulate natives."""
        producer = self._producers.pop(identity, None)
        self._creation_locks.pop(identity, None)
        if producer is not None:
            with contextlib.suppress(Exception):
                await producer.close()

    async def _publish_poison(
        self,
        message: Any,
        *,
        source_topic: str,
        subscription: str,
        error: BaseException,
    ) -> None:
        """Quarantine an undecodable delivery as a decodable blocked record.

        The raw bytes are never republished: a consumer that could not decode
        them (including ``dlq-triage``) could not decode the copy either, and
        for auth-origin traffic the bytes are already decrypted plaintext.
        Instead a schema-valid ``omega.control.signal.blocked`` (kind=dlq)
        record carries the origin, the native message id and a payload digest.
        It goes to ``dlq.<subscription>`` through the normal publish path, so
        the shared ``_requires_encryption`` rule applies to the preserved
        origin. Poison read from a DLQ itself (the triage input) is raised
        straight onto the blocked signal topic, because triage owns no
        recursive ``dlq.dlq-triage`` resource.
        """
        raw = bytes(message.data())
        properties = {
            str(key): str(value)
            for key, value in dict(message.properties() or {}).items()
        }
        native_id = str(message.message_id())
        reason = (
            str(redact_value(str(error), ()))[:256].strip() or "undecodable payload"
        )
        reason = f"undecodable payload quarantined: {reason}"[:256]
        recorded_origin = properties.get("omega_origin_topic", "")
        origin = (
            recorded_origin
            if _is_system_redelivery_topic(source_topic)
            and recorded_origin.startswith("persistent://")
            else source_topic
        )
        aggregate = (
            str(message.partition_key() or "")
            or properties.get("omega_key", "")
            or f"poison:{native_id}"
        )
        source_ce_id = (
            properties.get("ce_id")
            or properties.get("omega_source_ce_id")
            or properties.get("omega_origin_ce_id")
            or f"native:{native_id}"
        )
        try:
            attempt = int(properties.get("omega_attempt", "1"))
        except ValueError:
            attempt = 1
        attempt = min(max(attempt, 1), 5)
        digest = hashlib.sha256(raw).hexdigest()
        blocked = CloudEvent(
            specversion="1.0",
            id=f"poison:{subscription}:{native_id}"[:256],
            source="omega://eventstream/poison",
            type="omega.control.signal.blocked",
            datacontenttype="application/json",
            dataschema="urn:omega:schema:omega.control.signal.blocked:1",
            data={
                "kind": "dlq",
                "origin_topic": origin,
                "aggregate_key": aggregate,
                "source_ce_id": source_ce_id,
                "reason": reason,
                "history": [{"attempt": attempt, "reason": reason}],
            },
        )
        triage_input = source_topic.startswith("persistent://omega/system/dlq.")
        topic = (
            get_event_type(blocked.type).topic
            if triage_input
            else f"persistent://omega/system/dlq.{subscription}"
        )
        safe_properties = {
            key: value
            for key, value in properties.items()
            if not key.startswith("ce_") and key != "omega_retry_target"
        }
        request = PublishRequest(
            event=blocked,
            topic=topic,
            key="dlq:"
            + json.dumps(
                [origin, aggregate], ensure_ascii=False, separators=(",", ":")
            ),
            sequence_id=1,
            producer_name=f"omega-eventstream-poison-{subscription}-{native_id}",
            single_use=True,
            properties={
                **safe_properties,
                "omega_attempt": attempt,
                "omega_origin_topic": origin,
                "omega_origin_message_id": native_id,
                "omega_source_ce_id": source_ce_id,
                "omega_poison": "true",
                "omega_poison_sha256": digest,
                "omega_poison_bytes": len(raw),
                "omega_last_error": reason,
            },
        )
        await self._send(request, initial_sequence=0)

    async def subscribe(
        self,
        topic: str,
        subscription: str,
        *,
        subscription_type: SubscriptionType = "Key_Shared",
        initial_position: InitialPosition = "Earliest",
        receiver_queue_size: int = 1000,
    ) -> Subscriber:
        if self._closed:
            raise PulsarAdapterError("Pulsar event bus is closed")
        if not subscription.strip():
            raise ValueError("subscription name must be nonempty")
        if isinstance(receiver_queue_size, bool) or receiver_queue_size <= 0:
            raise ValueError("receiver_queue_size must be positive")
        if initial_position not in ("Earliest", "Latest"):
            raise ValueError("initial_position must be Earliest or Latest")
        if _is_system_retry_topic(topic) and subscription_type not in (
            "Shared",
            "Key_Shared",
        ):
            raise UnsupportedSubscriptionMode(
                "delayed retry subscriptions require Shared or Key_Shared mode"
            )
        try:
            import pulsar  # type: ignore[import-not-found,unused-ignore]

            client = await self._client_instance()
            options: dict[str, object] = {
                "consumer_type": _consumer_type(pulsar, str(subscription_type)),
                "initial_position": (
                    pulsar.InitialPosition.Earliest
                    if initial_position == "Earliest"
                    else pulsar.InitialPosition.Latest
                ),
                "receiver_queue_size": receiver_queue_size,
                "batch_index_ack_enabled": True,
                "negative_ack_redelivery_delay_ms": 1000,
                "schema": _native_schema_option(topic),
            }
            if subscription_type == "Key_Shared":
                options["key_shared_policy"] = pulsar.ConsumerKeySharedPolicy(
                    key_shared_mode=pulsar.KeySharedMode.AutoSplit
                )
            if topic.startswith(
                "persistent://omega/auth/"
            ) or _is_system_redelivery_topic(topic):
                options.update(
                    {
                        "crypto_key_reader": _crypto_reader(self.config),
                        "crypto_failure_action": pulsar.ConsumerCryptoFailureAction.FAIL,
                    }
                )
            consumer = await create_with_backoff(
                lambda: client.subscribe(topic, subscription, **options),
                attempts=self._creation_attempts,
                wait=self._backoff_wait,
                retryable=_is_retryable_pulsar_error,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(
                exc, (EncryptionUnavailable, UnsupportedSubscriptionMode, ValueError)
            ):
                raise
            raise _sanitized("Pulsar consumer creation failed", exc) from None
        subscriber = _PulsarSubscriber(
            consumer,
            self._codec,
            self._notify,
            bus=self,
            topic=topic,
            subscription=subscription,
        )
        self._resources.add(subscriber)
        self._notify(relay=True)
        return subscriber

    async def read(self, topic: str, *, start_at: int = 0) -> Reader:
        if self._closed:
            raise PulsarAdapterError("Pulsar event bus is closed")
        if isinstance(start_at, bool) or not isinstance(start_at, int) or start_at < 0:
            raise ValueError("reader start position must be a nonnegative integer")
        try:
            import pulsar  # type: ignore[import-not-found,unused-ignore]

            client = await self._client_instance()
            options: dict[str, object] = {
                "start_message_id": _message_id(start_at),
                "start_message_id_inclusive": start_at > 0,
                "receiver_queue_size": 100,
                "schema": _native_schema_option(topic),
            }
            if topic.startswith(
                "persistent://omega/auth/"
            ) or _is_system_redelivery_topic(topic):
                options.update(
                    {
                        "crypto_key_reader": _crypto_reader(self.config),
                        "crypto_failure_action": pulsar.ConsumerCryptoFailureAction.FAIL,
                    }
                )
            reader = await create_with_backoff(
                lambda: client.create_reader(topic, **options),
                attempts=self._creation_attempts,
                wait=self._backoff_wait,
                retryable=_is_retryable_pulsar_error,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, EncryptionUnavailable):
                raise
            raise _sanitized("Pulsar reader creation failed", exc) from None
        result = _PulsarReader(reader, self._codec, self._notify, topic=topic)
        self._resources.add(result)
        self._notify(relay=True)
        return result

    async def wait_transport_change(self) -> None:
        """Await an adapter-owned producer/consumer state or completion change."""
        await self._changed.wait()
        self._changed.clear()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        resources = tuple(self._resources)
        self._resources.clear()
        failures: list[Exception] = []
        for resource in resources:
            try:
                await resource.close()
            except Exception as exc:
                failures.append(exc)
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.close()
            except Exception as exc:
                failures.append(exc)
        self._producers.clear()
        self._creation_locks.clear()
        self._notify()
        if failures:
            raise _sanitized("Pulsar event bus close failed", failures[0]) from None


class _PulsarSubscriber:
    def __init__(
        self,
        consumer: Any,
        codec: _Codec,
        notify: WakeCallback,
        *,
        bus: PulsarEventBus,
        topic: str,
        subscription: str,
    ) -> None:
        self._consumer = consumer
        self._codec = codec
        self._notify = notify
        self._bus = bus
        self._topic = topic
        self._subscription = subscription
        self._closed = False
        # Native Pulsar receive futures must not be cancelled mid-flight; a timed
        # wait_for on receive() would otherwise poison later deliveries (failover
        # standby checks). Keep one shielded in-flight task across calls.
        self._inflight: asyncio.Task[Any] | None = None

    async def receive(self) -> Delivery:
        """Receive the next decodable delivery; poison payloads go to the DLQ."""
        if self._closed:
            raise PulsarAdapterError("Pulsar subscriber is closed")
        while True:
            if self._inflight is None or (
                self._inflight.done() and self._inflight.cancelled()
            ):
                self._inflight = asyncio.create_task(self._consumer.receive())
            inflight = self._inflight
            try:
                message = await asyncio.shield(inflight)
            except asyncio.CancelledError:
                # Caller timed out or cancelled; leave inflight running for the next
                # receive() so the broker handshake is not torn down.
                raise
            except Exception as exc:
                self._inflight = None
                self._notify()
                raise _sanitized("Pulsar receive failed", exc) from None
            if self._inflight is inflight:
                self._inflight = None
            try:
                properties = dict(message.properties())
                event = self._codec.decode(bytes(message.data()))
                held = message

                async def acknowledge(held_message: Any = held) -> None:
                    await self._consumer.acknowledge(held_message)
                    self._notify()

                return Delivery(
                    event=event,
                    topic=_logical_topic(message.topic_name()),
                    key=message.partition_key() or str(properties.get("omega_key", "")),
                    message_id=str(message.message_id()),
                    redelivered=message.redelivery_count() > 0,
                    properties=cast(Mapping[str, Any], redact_value(properties)),
                    ack=acknowledge,
                )
            except asyncio.CancelledError:
                raise
            except AuthenticationError:
                self._notify()
                raise
            except Exception as exc:
                # Mirror the TypeScript adapter: undecodable payloads are published
                # to dlq.<subscription> with omega_poison markers, then acked so
                # one bad message cannot stop the consumer TaskGroup.
                try:
                    await self._bus._publish_poison(
                        message,
                        source_topic=self._topic,
                        subscription=self._subscription,
                        error=exc,
                    )
                    await self._consumer.acknowledge(message)
                    self._notify()
                except Exception as poison_exc:
                    self._notify()
                    raise _sanitized(
                        "Pulsar poison dead-letter failed", poison_exc
                    ) from None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        inflight, self._inflight = self._inflight, None
        if inflight is not None and not inflight.done():
            inflight.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await inflight
        try:
            await self._consumer.close()
        finally:
            self._notify()


class _PulsarReader:
    def __init__(
        self,
        reader: Any,
        codec: _Codec,
        notify: WakeCallback,
        *,
        topic: str,
    ) -> None:
        self._reader = reader
        self._codec = codec
        self._notify = notify
        self._topic = topic
        self._closed = False

    async def read(self) -> Delivery:
        self._ensure_open()
        try:
            message = await self._reader.read_next()
            self._notify()
            return _delivery_from_message(message, self._codec, topic=self._topic)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._notify()
            raise _sanitized("Pulsar reader read failed", exc) from None

    async def read_nowait(self) -> Delivery | None:
        self._ensure_open()
        try:
            if not await self._reader.has_message_available():
                return None
            message = await self._reader.read_next()
            self._notify()
            return _delivery_from_message(message, self._codec, topic=self._topic)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._notify()
            raise _sanitized("Pulsar reader read failed", exc) from None

    async def seek(self, position: int) -> None:
        self._ensure_open()
        if isinstance(position, bool) or not isinstance(position, int) or position < 0:
            raise ValueError("reader position must be a nonnegative integer")
        try:
            await self._reader.seek(_message_id(position))
        except Exception as exc:
            raise _sanitized("Pulsar reader seek failed", exc) from None

    async def seek_time(self, timestamp: datetime) -> None:
        self._ensure_open()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("reader seek timestamp must be timezone-aware")
        try:
            await self._reader.seek(int(timestamp.timestamp() * 1000))
        except Exception as exc:
            raise _sanitized("Pulsar reader time seek failed", exc) from None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._reader.close()
        finally:
            self._notify()

    def _ensure_open(self) -> None:
        if self._closed:
            raise PulsarAdapterError("Pulsar reader is closed")


def _delivery_from_message(
    message: Any, codec: _Codec, *, topic: str | None = None
) -> Delivery:
    properties = dict(message.properties())
    raw = bytes(message.data())
    logical = _logical_topic(message.topic_name()) if topic is None else topic

    async def no_ack() -> None:
        return None

    topic_name = _logical_topic(message.topic_name())
    key = message.partition_key() or str(properties.get("omega_key", ""))
    message_id = str(message.message_id())
    safe_props = cast(Mapping[str, Any], redact_value(properties))

    if logical.endswith("/sdlc/pr-state") or topic_name.endswith("/sdlc/pr-state"):
        # Compacted PR projection is plain JSON (id/key/state), not a CloudEvent.
        # Build Delivery via model_construct so the unregistered projection shape
        # can still be read without forcing CloudEvent taxonomy validation.
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PulsarAdapterError("pr-state projection payload is not JSON") from exc
        if not isinstance(payload, dict):
            raise PulsarAdapterError(
                "pr-state projection payload must be a JSON object"
            )
        aggregate = str(payload.get("key") or key or "")
        event = CloudEvent.model_construct(
            specversion="1.0",
            id=str(payload.get("id") or message_id),
            source="omega://eventstream/pr-state",
            type="omega.sdlc.pr_state.projected",
            subject=aggregate or None,
            time=None,
            datacontenttype="application/json",
            dataschema="urn:omega:schema:omega.sdlc.pr_state.projected:1",
            data=payload,
            model_extra={},
        )
        # Projection documents are not taxonomy CloudEvents; model_construct
        # avoids forcing a false registered type onto compacted PR state.
        return Delivery.model_construct(
            event=event,
            topic=topic_name,
            key=aggregate or key or "pr-state",
            message_id=message_id,
            redelivered=False,
            properties=safe_props,
            ack=no_ack,
        )

    event = codec.decode(raw)
    return Delivery(
        event=event,
        topic=topic_name,
        key=key,
        message_id=message_id,
        redelivered=False,
        properties=safe_props,
        ack=no_ack,
    )


__all__ = [
    "EncryptionUnavailable",
    "PulsarAdapterError",
    "PulsarEventBus",
    "native_json_schema",
]
