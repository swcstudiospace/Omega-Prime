"""Caller-transaction SQLite outbox and event-woken publisher relay."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

from omega_prime.credentials.redact import redact_value
from omega_prime.eventstream.config import EventStreamConfig
from omega_prime.eventstream.envelope import CloudEvent, PublishReceipt, PublishRequest
from omega_prime.eventstream.errors import ContractError, EventStreamError, SchemaError
from omega_prime.eventstream.observability import EventStreamObserver
from omega_prime.eventstream.ports import Publisher

_MAX_REJECTION_REASON_CHARS = 512
_MAX_REJECTION_REASON_BYTES = _MAX_REJECTION_REASON_CHARS * 4

OutboxStatus: TypeAlias = Literal["pending", "sent", "rejected"]
ConfigSupplier: TypeAlias = EventStreamConfig | Callable[[], EventStreamConfig]
SecretSupplier: TypeAlias = Callable[[], tuple[str, ...]]
Clock: TypeAlias = Callable[[], datetime]


class OutboxError(EventStreamError):
    """Base class for local durable outbox failures."""


class OutboxFullError(OutboxError):
    """The append would exceed the configured durable byte capacity."""


class OutboxRejectedError(OutboxError):
    """A record was terminally rejected by the destination schema."""


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    """Immutable decoded view of one durable append."""

    sequence_id: int
    producer_name: str
    topic: str
    event: CloudEvent = field(repr=False)
    key: str
    properties: Mapping[str, Any] = field(repr=False)
    deliver_after: datetime | None
    status: OutboxStatus
    rejection_reason: str | None = field(default=None, repr=False)
    byte_size: int = 0

    @property
    def event_id(self) -> str:
        return self.event.id

    def request(self) -> PublishRequest:
        """Build the immutable publish value with its allocated sequence."""
        return PublishRequest(
            event=self.event,
            topic=self.topic,
            key=self.key,
            sequence_id=self.sequence_id,
            producer_name=self.producer_name,
            deliver_after=self.deliver_after,
            properties=self.properties,
        )


class SQLiteOutbox:
    """Append events inside caller transactions and relay only committed rows.

    ``append(connection, request)`` requires an active caller-owned transaction,
    uses a savepoint for its own atomicity, and never commits or rolls back that
    transaction. The caller must call ``notify_after_commit`` only after its
    successful commit. The ``transaction`` context manager is the alternative
    for callers that want the outbox to own the SQLite transaction and notify
    immediately after its commit. Startup scans durable pending rows, closing
    the commit-before-notification crash window.

    A committed SQLite AUTOINCREMENT row is the sequence allocation. Its
    allocation and row commit atomically; a rolled-back row was never visible to
    the relay and therefore cannot have exposed/reused a publisher sequence.
    ``sqlite_sequence`` plus the durable metadata high-water survives sent-row
    cleanup and database reopen.
    """

    def __init__(
        self,
        database: str | Path,
        *,
        producer_name: str,
        config: ConfigSupplier | None = None,
        max_bytes: int | None = None,
        known_secrets: tuple[str, ...] = (),
        secret_supplier: SecretSupplier | None = None,
        on_rejected: Callable[[OutboxRecord], None] | None = None,
        observer: EventStreamObserver | None = None,
        clock: Clock | None = None,
    ) -> None:
        if not isinstance(producer_name, str) or not producer_name.strip():
            raise ValueError("outbox producer identity must be nonempty")
        if max_bytes is not None and (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
        ):
            raise ValueError("outbox maximum bytes must be a positive integer")
        self.database = Path(database).expanduser().resolve()
        if str(database) == ":memory:":
            raise ValueError("outbox requires a file-backed SQLite database")
        if isinstance(config, EventStreamConfig):
            self._config_supplier: Callable[[], EventStreamConfig] = lambda: config
        elif config is None:
            self._config_supplier = EventStreamConfig
        else:
            self._config_supplier = config
        self._max_bytes_override = max_bytes
        self._producer_name = producer_name
        self._known_secrets = tuple(secret for secret in known_secrets if secret)
        self._secret_supplier = secret_supplier
        self._on_rejected = on_rejected
        self._observer = observer
        self._clock = clock if clock is not None else (lambda: datetime.now(UTC))
        self._wake_event = asyncio.Event()
        self._pressure_event = asyncio.Event()
        self._relay_lock = asyncio.Lock()
        self._event_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_wake = False
        self._pressured = False
        self._last_failure: str | None = None
        self._initialize()
        self._refresh_pressure()

    @property
    def producer_name(self) -> str:
        return self._producer_name

    @property
    def max_bytes(self) -> int:
        if self._max_bytes_override is not None:
            return self._max_bytes_override
        return self._config_supplier().outbox_max_bytes

    @property
    def wake_event(self) -> asyncio.Event:
        return self._wake_event

    @property
    def pressure_event(self) -> asyncio.Event:
        return self._pressure_event

    @property
    def last_failure(self) -> str | None:
        """Most recent bounded relay diagnostic, redacted before exposure."""
        return self._last_failure

    @property
    def sequence_high_water(self) -> int:
        return int(self._metadata("sequence_high_water"))

    @property
    def acknowledged_watermark(self) -> int:
        return int(self._metadata("acked_watermark"))

    @property
    def initial_sequence_id(self) -> int:
        """Safe initial sequence to pass to a deduplicating producer on reopen."""
        return self.acknowledged_watermark

    @property
    def pending_bytes(self) -> int:
        connection = self.open_connection()
        try:
            value = connection.execute(
                "SELECT COALESCE(SUM(byte_size), 0) FROM eventstream_outbox "
                "WHERE status IN ('pending', 'rejected')"
            ).fetchone()[0]
            return int(value)
        finally:
            connection.close()

    @property
    def pressure_ratio(self) -> float:
        return self.pending_bytes / self.max_bytes

    @property
    def pressure_threshold_bytes(self) -> int:
        """Minimum integer queue size meeting the 80% pressure boundary."""
        return (self.max_bytes * 4 + 4) // 5

    @property
    def is_under_pressure(self) -> bool:
        return self.pending_bytes >= self.pressure_threshold_bytes

    def open_connection(self) -> sqlite3.Connection:
        """Open an explicit-transaction connection configured WAL/FULL."""
        connection = sqlite3.connect(
            self.database,
            timeout=0.0,
            isolation_level=None,
        )
        try:
            mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            if mode != "wal":
                connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            sync = int(connection.execute("PRAGMA synchronous").fetchone()[0])
            if mode != "wal" or sync < 2:
                raise RuntimeError("outbox durability settings are unavailable")
        except BaseException:
            connection.close()
            raise
        return connection

    def _initialize(self) -> None:
        connection = self.open_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS eventstream_meta ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS eventstream_outbox ("
                "sequence_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "producer_name TEXT NOT NULL, topic TEXT NOT NULL, "
                "event_id TEXT NOT NULL, event BLOB NOT NULL, message_key TEXT NOT NULL, "
                "properties TEXT NOT NULL, deliver_after TEXT, byte_size INTEGER NOT NULL, "
                "status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'rejected')), "
                "rejection_reason TEXT, appended_at TEXT)"
            )
            existing_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(eventstream_outbox)")
            }
            if "appended_at" not in existing_columns:
                connection.execute(
                    "ALTER TABLE eventstream_outbox ADD COLUMN appended_at TEXT"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS eventstream_outbox_pending_sequence "
                "ON eventstream_outbox(status, sequence_id)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO eventstream_meta(key, value) VALUES (?, ?)",
                ("producer_name", self._producer_name),
            )
            persisted_name = self._read_meta(connection, "producer_name")
            if persisted_name != self._producer_name:
                raise ValueError(
                    "outbox producer identity does not match persisted producer identity"
                )
            connection.execute(
                "INSERT OR IGNORE INTO eventstream_meta(key, value) VALUES ('sequence_high_water', '0')"
            )
            connection.execute(
                "INSERT OR IGNORE INTO eventstream_meta(key, value) VALUES ('acked_watermark', '0')"
            )
            allocated = connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'eventstream_outbox'"
            ).fetchone()
            sequence_high_water = int(
                self._read_meta(connection, "sequence_high_water")
            )
            if allocated is not None:
                sequence_high_water = max(sequence_high_water, int(allocated[0]))
                self._write_meta(connection, "sequence_high_water", sequence_high_water)
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _read_meta(connection: sqlite3.Connection, key: str) -> str:
        row = connection.execute(
            "SELECT value FROM eventstream_meta WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            raise RuntimeError("outbox metadata is incomplete")
        return str(row[0])

    @staticmethod
    def _write_meta(connection: sqlite3.Connection, key: str, value: int) -> None:
        connection.execute(
            "INSERT INTO eventstream_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def _metadata(self, key: str) -> str:
        connection = self.open_connection()
        try:
            return self._read_meta(connection, key)
        finally:
            connection.close()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("outbox clock must return an aware datetime")
        return value.astimezone(UTC)

    def _validate_caller_connection(self, connection: sqlite3.Connection) -> None:
        if not connection.in_transaction:
            raise ValueError("outbox append requires an active caller transaction")
        databases = connection.execute("PRAGMA database_list").fetchall()
        main_path = next((str(row[2]) for row in databases if row[1] == "main"), "")
        if not main_path or Path(main_path).expanduser().resolve() != self.database:
            raise ValueError("caller transaction belongs to a different database")
        mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        sync = int(connection.execute("PRAGMA synchronous").fetchone()[0])
        if mode != "wal" or sync < 2:
            raise ValueError("caller transaction must use WAL and synchronous FULL")

    def _known_secret_values(self) -> tuple[str, ...]:
        values = [secret for secret in self._known_secrets if isinstance(secret, str)]
        if self._secret_supplier is not None:
            try:
                supplied = self._secret_supplier()
            except Exception:
                raise OutboxError(
                    "configured outbox secret supplier is unavailable"
                ) from None
            values.extend(
                secret for secret in supplied if isinstance(secret, str) and secret
            )
        try:
            config = self._config_supplier()
        except Exception:
            raise OutboxError("event-stream configuration is unavailable") from None
        for name, path in (
            ("token", config.token_file),
            ("encryption_key", config.encryption_key_file),
            ("private_key", config.private_key_file),
        ):
            if path is not None:
                values.append(config.read_secret(name))
        return tuple(dict.fromkeys(value for value in values if value))

    @classmethod
    def _plain_json(cls, value: object) -> object:
        if isinstance(value, Mapping):
            if not all(isinstance(key, str) for key in value):
                raise ContractError("outbox object keys must be strings")
            return {key: cls._plain_json(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._plain_json(item) for item in value]
        return value

    @staticmethod
    def _json_bytes(value: object) -> bytes:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ContractError("outbox value is not valid JSON") from exc

    def _encode_request(self, request: PublishRequest) -> tuple[bytes, str, int]:
        secrets = self._known_secret_values()
        event_bytes = request.event.to_bytes(extra=secrets)
        try:
            properties_value = redact_value(
                self._plain_json(request.properties), extra=secrets
            )
            if not isinstance(properties_value, dict):
                raise ContractError("publish properties must remain an object")
            properties = self._json_bytes(properties_value).decode("utf-8")
            metadata = redact_value(
                {
                    "producer_name": request.producer_name,
                    "topic": request.topic,
                    "key": request.key,
                },
                extra=secrets,
            )
            if metadata != {
                "producer_name": request.producer_name,
                "topic": request.topic,
                "key": request.key,
            }:
                raise ContractError("redaction changed outbox routing metadata")
            event = CloudEvent.from_bytes(event_bytes)
            sanitized_request = PublishRequest(
                event=event,
                topic=request.topic,
                key=request.key,
                sequence_id=request.sequence_id,
                producer_name=request.producer_name,
                deliver_after=request.deliver_after,
                properties=properties_value,
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ContractError):
                raise
            raise ContractError(
                "event is invalid after redaction and schema validation"
            ) from exc
        event_bytes = sanitized_request.event.to_bytes(extra=secrets)
        metadata_size = sum(
            len(value.encode("utf-8"))
            for value in (
                sanitized_request.producer_name,
                sanitized_request.topic,
                sanitized_request.key,
                sanitized_request.deliver_after.isoformat()
                if sanitized_request.deliver_after is not None
                else "",
            )
        )
        byte_size = len(event_bytes) + len(properties.encode("utf-8")) + metadata_size
        return event_bytes, properties, byte_size

    def append(
        self, connection: sqlite3.Connection, request: PublishRequest
    ) -> OutboxRecord:
        """Append without taking over the caller's commit or rollback decision."""
        self._validate_caller_connection(connection)
        if request.producer_name != self._producer_name:
            raise ValueError("publish request producer does not match outbox identity")
        event_bytes, properties, byte_size = self._encode_request(request)
        maximum = self.max_bytes
        appended_at = self._now().isoformat()
        byte_size += len(appended_at.encode("utf-8")) + _MAX_REJECTION_REASON_BYTES
        connection.execute("SAVEPOINT eventstream_outbox_append")
        try:
            pending_bytes = int(
                connection.execute(
                    "SELECT COALESCE(SUM(byte_size), 0) FROM eventstream_outbox "
                    "WHERE status IN ('pending', 'rejected')"
                ).fetchone()[0]
            )
            if pending_bytes + byte_size > maximum:
                raise OutboxFullError("eventstream outbox capacity exceeded")
            cursor = connection.execute(
                "INSERT INTO eventstream_outbox "
                "(producer_name, topic, event_id, event, message_key, properties, "
                "deliver_after, byte_size, status, appended_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    self._producer_name,
                    request.topic,
                    request.event.id,
                    event_bytes,
                    request.key,
                    properties,
                    request.deliver_after.isoformat()
                    if request.deliver_after is not None
                    else None,
                    byte_size,
                    appended_at,
                ),
            )
            if cursor.lastrowid is None:
                raise OutboxError("SQLite did not allocate an outbox sequence")
            sequence_id = cursor.lastrowid
            self._write_meta(connection, "sequence_high_water", sequence_id)
            connection.execute("RELEASE SAVEPOINT eventstream_outbox_append")
        except BaseException:
            connection.execute("ROLLBACK TO SAVEPOINT eventstream_outbox_append")
            connection.execute("RELEASE SAVEPOINT eventstream_outbox_append")
            raise
        record = self._record_from_values(
            sequence_id=sequence_id,
            producer_name=self._producer_name,
            topic=request.topic,
            event=event_bytes,
            message_key=request.key,
            properties=properties,
            deliver_after=(
                request.deliver_after.isoformat()
                if request.deliver_after is not None
                else None
            ),
            status="pending",
            rejection_reason=None,
            byte_size=byte_size,
        )
        return record

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Own one append transaction; commit first, then signal the relay."""
        connection = self.open_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        else:
            self.notify_after_commit()
        finally:
            connection.close()

    def bind_loop(self) -> None:
        """Bind relay Events to the current loop for safe cross-thread signals."""
        loop = asyncio.get_running_loop()
        with self._event_lock:
            if (
                self._loop is not None
                and self._loop is not loop
                and not self._loop.is_closed()
            ):
                raise RuntimeError(
                    "outbox relay is already bound to another event loop"
                )
            self._loop = loop
            pending_wake = self._pending_wake
            self._pending_wake = False
            pressured = self._pressured
        if pending_wake:
            self._wake_event.set()
        if pressured:
            self._pressure_event.set()

    def _signal_event(self, event: asyncio.Event, *, set_value: bool) -> None:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        with self._event_lock:
            if self._loop is None and current_loop is not None:
                self._loop = current_loop
            loop = self._loop
            if event is self._wake_event and set_value and loop is None:
                self._pending_wake = True
        if loop is None:
            return
        if loop.is_closed():
            with self._event_lock:
                if event is self._wake_event and set_value:
                    self._pending_wake = True
            return
        action = event.set if set_value else event.clear
        if current_loop is loop:
            action()
        else:
            loop.call_soon_threadsafe(action)

    def _refresh_pressure(self) -> None:
        pressured = self.pending_bytes >= self.pressure_threshold_bytes
        with self._event_lock:
            self._pressured = pressured
        self._signal_event(self._pressure_event, set_value=pressured)

    def _report_outbox(self) -> None:
        if self._observer is None:
            return
        connection = self.open_connection()
        try:
            depth, byte_count = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(byte_size), 0) "
                "FROM eventstream_outbox WHERE status IN ('pending', 'rejected')"
            ).fetchone()
            oldest = connection.execute(
                "SELECT appended_at FROM eventstream_outbox "
                "WHERE status IN ('pending', 'rejected') ORDER BY sequence_id LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        oldest_age = 0.0
        if oldest is not None:
            timestamp = (
                datetime.fromisoformat(str(oldest[0]))
                if oldest[0] is not None
                else self._now()
            )
            oldest_age = max(0.0, (self._now() - timestamp).total_seconds())
        self._observer.outbox(int(depth), int(byte_count), oldest_age)

    def notify_after_commit(self) -> None:
        """Notify after the caller has successfully committed its transaction."""
        self._refresh_pressure()
        self._signal_event(self._wake_event, set_value=True)
        self._report_outbox()

    def notify_reconnected(self) -> None:
        """Wake a relay after a real reconnect or explicit startup request."""
        self._refresh_pressure()
        self._signal_event(self._wake_event, set_value=True)
        self._report_outbox()

    def _record_from_values(
        self,
        *,
        sequence_id: int,
        producer_name: str,
        topic: str,
        event: bytes,
        message_key: str,
        properties: str,
        deliver_after: str | None,
        status: str,
        rejection_reason: str | None,
        byte_size: int,
    ) -> OutboxRecord:
        event_value = CloudEvent.from_bytes(bytes(event))
        properties_value = json.loads(properties)
        if not isinstance(properties_value, dict):
            raise ContractError("stored publish properties are invalid")
        delivered_at = (
            datetime.fromisoformat(deliver_after) if deliver_after is not None else None
        )
        typed_status = status
        if typed_status not in {"pending", "sent", "rejected"}:
            raise ContractError("stored outbox status is invalid")
        publish_request = PublishRequest(
            event=event_value,
            topic=topic,
            key=message_key,
            sequence_id=sequence_id,
            producer_name=producer_name,
            deliver_after=delivered_at,
            properties=properties_value,
        )
        return OutboxRecord(
            sequence_id=sequence_id,
            producer_name=producer_name,
            topic=topic,
            event=publish_request.event,
            key=publish_request.key,
            properties=publish_request.properties,
            deliver_after=delivered_at,
            status=cast(OutboxStatus, typed_status),
            rejection_reason=rejection_reason,
            byte_size=byte_size,
        )

    def _row_record(self, row: tuple[object, ...]) -> OutboxRecord:
        sequence_id = row[0]
        event = row[3]
        byte_size = row[9]
        if (
            isinstance(sequence_id, bool)
            or not isinstance(sequence_id, int)
            or isinstance(byte_size, bool)
            or not isinstance(byte_size, int)
        ):
            raise ContractError("stored outbox numeric fields are invalid")
        if not isinstance(event, (bytes, bytearray, memoryview)):
            raise ContractError("stored outbox event is invalid")
        return self._record_from_values(
            sequence_id=sequence_id,
            producer_name=str(row[1]),
            topic=str(row[2]),
            event=bytes(event),
            message_key=str(row[4]),
            properties=str(row[5]),
            deliver_after=None if row[6] is None else str(row[6]),
            status=str(row[7]),
            rejection_reason=None if row[8] is None else str(row[8]),
            byte_size=byte_size,
        )

    async def pending(self) -> tuple[OutboxRecord, ...]:
        connection = self.open_connection()
        try:
            rows = connection.execute(
                "SELECT sequence_id, producer_name, topic, event, message_key, properties, "
                "deliver_after, status, rejection_reason, byte_size "
                "FROM eventstream_outbox WHERE status = 'pending' ORDER BY sequence_id"
            ).fetchall()
            return tuple(self._row_record(row) for row in rows)
        finally:
            connection.close()

    async def rejected(self) -> tuple[OutboxRecord, ...]:
        connection = self.open_connection()
        try:
            rows = connection.execute(
                "SELECT sequence_id, producer_name, topic, event, message_key, properties, "
                "deliver_after, status, rejection_reason, byte_size "
                "FROM eventstream_outbox WHERE status = 'rejected' ORDER BY sequence_id"
            ).fetchall()
            return tuple(self._row_record(row) for row in rows)
        finally:
            connection.close()

    async def get(self, sequence_id: int) -> OutboxRecord | None:
        connection = self.open_connection()
        try:
            row = connection.execute(
                "SELECT sequence_id, producer_name, topic, event, message_key, properties, "
                "deliver_after, status, rejection_reason, byte_size "
                "FROM eventstream_outbox WHERE sequence_id = ?",
                (sequence_id,),
            ).fetchone()
            return None if row is None else self._row_record(row)
        finally:
            connection.close()

    def mark_sent(self, sequence_id: int, receipt: PublishReceipt) -> int:
        """Persist the matching destination ack and advance only a safe watermark."""
        connection = self.open_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, event_id, topic FROM eventstream_outbox "
                "WHERE sequence_id = ?",
                (sequence_id,),
            ).fetchone()
            if row is None:
                raise KeyError("outbox sequence does not exist")
            if receipt.event_id != row[1] or receipt.topic != row[2]:
                raise OutboxError(
                    "publisher acknowledgement identity did not match outbox row"
                )
            if row[0] == "rejected":
                raise OutboxRejectedError(
                    "terminally rejected outbox record cannot be sent"
                )
            if row[0] == "pending":
                connection.execute(
                    "UPDATE eventstream_outbox SET status = 'sent' "
                    "WHERE sequence_id = ? AND status = 'pending'",
                    (sequence_id,),
                )
            watermark = int(self._read_meta(connection, "acked_watermark"))
            high_water = int(self._read_meta(connection, "sequence_high_water"))
            first_unresolved = connection.execute(
                "SELECT MIN(sequence_id) FROM eventstream_outbox "
                "WHERE sequence_id > ? AND status != 'sent'",
                (watermark,),
            ).fetchone()[0]
            safe_watermark = (
                high_water if first_unresolved is None else int(first_unresolved) - 1
            )
            safe_watermark = max(watermark, safe_watermark)
            self._write_meta(connection, "acked_watermark", safe_watermark)
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        self._refresh_pressure()
        self._report_outbox()
        return safe_watermark

    def mark_rejected(self, sequence_id: int, reason: str) -> OutboxRecord:
        """Persist a terminal, redacted schema rejection without treating it as sent."""
        safe_reason = str(redact_value(reason, extra=self._known_secret_values()))[
            :_MAX_REJECTION_REASON_CHARS
        ]
        reason_bytes = len(safe_reason.encode("utf-8"))
        connection = self.open_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, byte_size FROM eventstream_outbox WHERE sequence_id = ?",
                (sequence_id,),
            ).fetchone()
            if row is None:
                raise KeyError("outbox sequence does not exist")
            if row[0] == "pending":
                actual_size = int(row[1]) - _MAX_REJECTION_REASON_BYTES + reason_bytes
                connection.execute(
                    "UPDATE eventstream_outbox SET status = 'rejected', "
                    "rejection_reason = ?, byte_size = ? "
                    "WHERE sequence_id = ? AND status = 'pending'",
                    (safe_reason, actual_size, sequence_id),
                )
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        self._refresh_pressure()
        self._report_outbox()
        result = self._get_sync(sequence_id)
        if result is None:
            raise RuntimeError("rejected outbox record disappeared")
        if self._on_rejected is not None:
            self._on_rejected(result)
        return result

    def _get_sync(self, sequence_id: int) -> OutboxRecord | None:
        connection = self.open_connection()
        try:
            row = connection.execute(
                "SELECT sequence_id, producer_name, topic, event, message_key, properties, "
                "deliver_after, status, rejection_reason, byte_size "
                "FROM eventstream_outbox WHERE sequence_id = ?",
                (sequence_id,),
            ).fetchone()
            return None if row is None else self._row_record(row)
        finally:
            connection.close()

    def cleanup_sent(self) -> int:
        """Remove only sent rows covered by the durable contiguous watermark."""
        connection = self.open_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            watermark = int(self._read_meta(connection, "acked_watermark"))
            result = connection.execute(
                "DELETE FROM eventstream_outbox WHERE status = 'sent' AND sequence_id <= ?",
                (watermark,),
            )
            removed = result.rowcount
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        self._refresh_pressure()
        return removed

    async def relay_once(self, publisher: Publisher) -> int:
        """Drain committed rows in sequence order, retaining replay after failures.

        One relay pass per outbox instance is allowed at a time. This prevents
        overlapping send loops from racing the same durable sequence or
        submitting a later sequence while an earlier publish is unresolved.
        """
        async with self._relay_lock:
            sent = 0
            for record in await self.pending():
                try:
                    receipt = await publisher.publish(record.request())
                except SchemaError as exc:
                    self.mark_rejected(record.sequence_id, str(exc))
                    continue
                if receipt.event_id != record.event_id or receipt.topic != record.topic:
                    raise OutboxError(
                        "publisher acknowledgement identity did not match outbox row"
                    )
                self.mark_sent(record.sequence_id, receipt)
                sent += 1
            return sent

    async def serve(self, publisher: Publisher) -> None:
        """Run an event-woken relay in the caller-owned task until cancellation."""
        self.bind_loop()
        self.notify_reconnected()
        while True:
            self._wake_event.clear()
            try:
                await self.relay_once(publisher)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_failure = self._safe_failure(str(exc))
                await self._wake_event.wait()
                continue
            if await self.pending():
                continue
            await self._wake_event.wait()

    def _safe_failure(self, message: str) -> str:
        try:
            secrets = self._known_secret_values()
        except Exception:
            return "relay failure (secret redaction unavailable)"
        return str(redact_value(message, extra=secrets))[:512]


__all__ = [
    "OutboxError",
    "OutboxFullError",
    "OutboxRecord",
    "OutboxRejectedError",
    "OutboxStatus",
    "SQLiteOutbox",
]
