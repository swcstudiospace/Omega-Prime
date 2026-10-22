from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from omega_prime.eventstream.envelope import CloudEvent, PublishReceipt, PublishRequest
from omega_prime.eventstream.errors import SchemaError
from omega_prime.eventstream.observability import EventStreamObserver
from omega_prime.eventstream.outbox import (
    OutboxFullError,
    OutboxRecord,
    SQLiteOutbox,
)
from omega_prime.eventstream.taxonomy import derive_key, schema_uri_for_type


def make_request(
    event_id: str,
    *,
    actor: str = "alice",
    properties: dict[str, Any] | None = None,
) -> PublishRequest:
    event_type = "omega.sdlc.pr.opened"
    event = CloudEvent.model_validate(
        {
            "specversion": "1.0",
            "id": event_id,
            "source": "github://example/project",
            "type": event_type,
            "subject": "pr/7",
            "time": datetime(2026, 10, 23, 12, tzinfo=UTC),
            "datacontenttype": "application/json",
            "dataschema": schema_uri_for_type(event_type),
            "data": {
                "repo": "example/project",
                "number": 7,
                "head_sha": "abc123",
                "actor": actor,
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


class AcceptingPublisher:
    def __init__(self) -> None:
        self.requests: list[PublishRequest] = []

    async def publish(self, request: PublishRequest) -> PublishReceipt:
        self.requests.append(request)
        return PublishReceipt(
            event_id=request.event.id,
            topic=request.topic,
            message_id=f"broker-{request.sequence_id}",
            acknowledged_at=datetime(2026, 10, 23, 12, tzinfo=UTC),
        )

    async def close(self) -> None:
        return None


class SchemaRejectingPublisher:
    async def publish(self, request: PublishRequest) -> PublishReceipt:
        del request
        raise SchemaError("schema rejected token=sk-abcdefghijklmnop")

    async def close(self) -> None:
        return None


class RecordingObserver(EventStreamObserver):
    def __init__(self) -> None:
        self.outbox_samples: list[tuple[int, int, float]] = []

    def outbox(self, depth: int, bytes: int, oldest_age: float) -> None:
        self.outbox_samples.append((depth, bytes, oldest_age))


def test_caller_rollback_hides_both_changes_and_never_notifies(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        outbox = SQLiteOutbox(
            tmp_path / "outbox.sqlite3", producer_name="test-producer"
        )
        writer = outbox.open_connection()
        writer.execute("CREATE TABLE business (value TEXT NOT NULL)")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO business VALUES ('uncommitted')")
        outbox.append(writer, make_request("rolled-back"))

        observer = outbox.open_connection()
        assert observer.execute("SELECT COUNT(*) FROM business").fetchone() == (0,)
        assert await outbox.pending() == ()
        assert not outbox.wake_event.is_set()

        writer.rollback()
        assert await outbox.pending() == ()
        assert not outbox.wake_event.is_set()
        assert observer.execute("SELECT COUNT(*) FROM business").fetchone() == (0,)
        observer.close()
        writer.close()

    asyncio.run(scenario())


def test_caller_commit_notifies_only_through_explicit_post_commit_seam(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        outbox = SQLiteOutbox(
            tmp_path / "outbox.sqlite3", producer_name="test-producer"
        )
        connection = outbox.open_connection()
        connection.execute("CREATE TABLE business (value TEXT NOT NULL)")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO business VALUES ('committed')")
        row = outbox.append(connection, make_request("committed"))
        assert not outbox.wake_event.is_set()
        connection.commit()

        # Append never commits or signals an independent caller transaction.
        assert not outbox.wake_event.is_set()
        outbox.notify_after_commit()
        assert outbox.wake_event.is_set()

        publisher = AcceptingPublisher()
        assert await outbox.relay_once(publisher) == 1
        assert [request.sequence_id for request in publisher.requests] == [
            row.sequence_id
        ]
        assert await outbox.pending() == ()
        assert outbox.acknowledged_watermark == row.sequence_id
        connection.close()

    asyncio.run(scenario())


def test_observer_reports_post_commit_drain_and_exact_pressure_boundary(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = [datetime(2026, 10, 23, 12, tzinfo=UTC)]
        observer = RecordingObserver()
        outbox = SQLiteOutbox(
            tmp_path / "outbox.sqlite3",
            producer_name="test-producer",
            max_bytes=3_500,
            observer=observer,
            clock=lambda: now[0],
        )
        connection = outbox.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        row = outbox.append(connection, make_request("observed"))
        assert observer.outbox_samples == []
        connection.commit()

        now[0] += timedelta(seconds=5)
        outbox.notify_after_commit()
        assert observer.outbox_samples[-1] == (1, row.byte_size, 5.0)
        assert outbox.pressure_threshold_bytes == 2_800
        assert not outbox.is_under_pressure

        assert await outbox.relay_once(AcceptingPublisher()) == 1
        assert observer.outbox_samples[-1] == (0, 0, 0.0)
        connection.close()

    asyncio.run(scenario())


def test_outbox_owned_transaction_commits_then_notifies(tmp_path: Path) -> None:
    async def scenario() -> None:
        outbox = SQLiteOutbox(
            tmp_path / "outbox.sqlite3", producer_name="test-producer"
        )
        with outbox.transaction() as connection:
            row = outbox.append(connection, make_request("owned-transaction"))
        assert outbox.wake_event.is_set()
        publisher = AcceptingPublisher()
        assert await outbox.relay_once(publisher) == 1
        assert publisher.requests[0].sequence_id == row.sequence_id

    asyncio.run(scenario())


def test_startup_scan_recovers_commit_before_lost_notification(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "outbox.sqlite3"
        first = SQLiteOutbox(database, producer_name="test-producer")
        connection = first.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        row = first.append(connection, make_request("crash-before-wake"))
        connection.commit()
        assert not first.wake_event.is_set()
        connection.close()

        reopened = SQLiteOutbox(database, producer_name="test-producer")
        publisher = AcceptingPublisher()
        assert await reopened.relay_once(publisher) == 1
        assert publisher.requests[0].sequence_id == row.sequence_id
        assert await reopened.pending() == ()

    asyncio.run(scenario())


def test_wal_full_stable_identity_sequence_cleanup_and_safe_watermark(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "outbox.sqlite3"
        outbox = SQLiteOutbox(database, producer_name="test-producer")
        connection = outbox.open_connection()
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        connection.execute("BEGIN IMMEDIATE")
        first = outbox.append(connection, make_request("first"))
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        second = outbox.append(connection, make_request("second"))
        connection.commit()

        # Later sequence acknowledgement cannot jump over an unresolved earlier row.
        outbox.mark_sent(
            second.sequence_id,
            PublishReceipt(
                event_id=second.event.id,
                topic=second.topic,
                message_id="broker-second",
                acknowledged_at=datetime(2026, 10, 23, 12, tzinfo=UTC),
            ),
        )
        assert outbox.acknowledged_watermark == 0
        assert await outbox.pending() == (first,)
        outbox.mark_sent(
            first.sequence_id,
            PublishReceipt(
                event_id=first.event.id,
                topic=first.topic,
                message_id="broker-first",
                acknowledged_at=datetime(2026, 10, 23, 12, tzinfo=UTC),
            ),
        )
        assert outbox.acknowledged_watermark == second.sequence_id
        assert outbox.cleanup_sent() == 2
        connection.close()

        reopened = SQLiteOutbox(database, producer_name="test-producer")
        next_connection = reopened.open_connection()
        next_connection.execute("BEGIN IMMEDIATE")
        third = reopened.append(next_connection, make_request("third"))
        next_connection.commit()
        assert third.sequence_id > second.sequence_id
        assert reopened.acknowledged_watermark == second.sequence_id
        assert reopened.producer_name == "test-producer"
        assert (
            next_connection.execute("PRAGMA journal_mode").fetchone()[0].lower()
            == "wal"
        )
        assert next_connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        next_connection.close()

    asyncio.run(scenario())


def test_outbox_rejects_a_different_persisted_producer_identity(tmp_path: Path) -> None:
    database = tmp_path / "outbox.sqlite3"
    SQLiteOutbox(database, producer_name="producer-one")
    with pytest.raises(ValueError, match="producer identity"):
        SQLiteOutbox(database, producer_name="producer-two")


def test_capacity_is_atomic_and_never_discards_existing_pending_work(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        outbox = SQLiteOutbox(
            tmp_path / "outbox.sqlite3", producer_name="test-producer", max_bytes=3_000
        )
        connection = outbox.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        accepted = outbox.append(connection, make_request("fits"))
        connection.commit()
        existing = await outbox.pending()

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(OutboxFullError):
            outbox.append(
                connection,
                make_request("overflow", properties={"payload": "x" * 4096}),
            )
        assert await outbox.pending() == existing == (accepted,)
        assert outbox.pending_bytes <= outbox.max_bytes
        connection.close()

    asyncio.run(scenario())


def test_pressure_event_signals_at_eighty_percent_and_overflow_is_atomic(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        outbox = SQLiteOutbox(
            tmp_path / "outbox.sqlite3", producer_name="test-producer", max_bytes=8_000
        )
        connection = outbox.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        accepted = outbox.append(
            connection,
            make_request("pressure", properties={"payload": "x" * 4_200}),
        )
        connection.commit()
        outbox.notify_after_commit()
        assert outbox.pending_bytes == accepted.byte_size
        assert outbox.is_under_pressure
        assert outbox.pressure_event.is_set()

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(OutboxFullError):
            outbox.append(
                connection,
                make_request("over-cap", properties={"payload": "x" * 1_000}),
            )
        connection.rollback()
        assert await outbox.pending() == (accepted,)
        assert outbox.pending_bytes <= outbox.max_bytes
        connection.close()

    asyncio.run(scenario())


def test_redacts_known_and_secret_shaped_values_before_persistence(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "outbox.sqlite3"
        known = "synthetic-known-secret-105"
        outbox = SQLiteOutbox(
            database,
            producer_name="test-producer",
            known_secrets=(known,),
        )
        connection = outbox.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        outbox.append(
            connection,
            make_request(
                "redacted",
                actor=known,
                properties={"authorization": "Bearer ghp_abcdefghijklmnop"},
            ),
        )
        connection.commit()
        stored = connection.execute(
            "SELECT event, properties FROM eventstream_outbox"
        ).fetchone()
        assert stored is not None
        serialized = stored[0].decode("utf-8") + stored[1]
        assert known not in serialized
        assert "ghp_abcdefghijklmnop" not in serialized
        assert "[REDACTED]" in serialized
        connection.close()

    asyncio.run(scenario())


def test_schema_rejection_is_terminal_sanitized_and_not_acknowledged(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "outbox.sqlite3"
        alerts: list[OutboxRecord] = []
        outbox = SQLiteOutbox(
            database, producer_name="test-producer", on_rejected=alerts.append
        )
        connection = outbox.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        row = outbox.append(connection, make_request("schema-reject"))
        connection.commit()

        assert await outbox.relay_once(SchemaRejectingPublisher()) == 0
        rejected = await outbox.get(row.sequence_id)
        assert rejected is not None
        assert rejected.status == "rejected"
        assert rejected.rejection_reason is not None
        assert "sk-abcdefghijklmnop" not in rejected.rejection_reason
        assert len(alerts) == 1
        assert alerts[0].status == "rejected"
        assert alerts[0].rejection_reason == rejected.rejection_reason
        assert outbox.acknowledged_watermark == 0
        assert await outbox.pending() == ()
        assert await outbox.rejected() == (rejected,)
        connection.close()

    asyncio.run(scenario())


def test_rejection_reason_reservation_preserves_capacity_and_accounts_redacted_unicode(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        outbox = SQLiteOutbox(
            tmp_path / "outbox.sqlite3", producer_name="test-producer", max_bytes=4_000
        )
        connection = outbox.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        row = outbox.append(connection, make_request("reject-boundary"))
        connection.commit()
        assert row.byte_size <= outbox.max_bytes
        assert outbox.pending_bytes == row.byte_size
        with pytest.raises(OutboxFullError), outbox.transaction() as transaction:
            outbox.append(transaction, make_request("must-not-displace"))
        reason = "原因 sk-abcdefghijklmnop " + "界" * 500
        rejected = outbox.mark_rejected(row.sequence_id, reason)
        assert rejected.status == "rejected"
        assert rejected.rejection_reason is not None
        assert "sk-abcdefghijklmnop" not in rejected.rejection_reason
        assert "界" in rejected.rejection_reason
        assert rejected.byte_size == row.byte_size - 512 * 4 + len(
            rejected.rejection_reason.encode("utf-8")
        )
        assert outbox.pending_bytes == rejected.byte_size
        assert outbox.pressure_ratio == rejected.byte_size / outbox.max_bytes
        assert await outbox.rejected() == (rejected,)
        connection.close()

    asyncio.run(scenario())


def test_append_refuses_invalid_payload_even_if_model_validation_was_bypassed(
    tmp_path: Path,
) -> None:
    event_type = "omega.sdlc.pr.opened"
    invalid_event = CloudEvent.model_construct(
        specversion="1.0",
        id="invalid-schema",
        source="github://example/project",
        type=event_type,
        subject="pr/7",
        time=datetime(2026, 10, 23, 12, tzinfo=UTC),
        datacontenttype="application/json",
        dataschema=schema_uri_for_type(event_type),
        data={"repo": "example/project", "number": "not-an-integer"},
        __pydantic_extra__={"omegakey": "example/project#7"},
    )
    invalid_request = PublishRequest.model_construct(
        event=invalid_event,
        topic="persistent://omega/sdlc/pr",
        key="example/project#7",
        sequence_id=0,
        producer_name="test-producer",
        properties={},
    )
    outbox = SQLiteOutbox(tmp_path / "outbox.sqlite3", producer_name="test-producer")
    connection = outbox.open_connection()
    connection.execute("BEGIN IMMEDIATE")
    with pytest.raises(ValueError):
        outbox.append(connection, invalid_request)
    connection.rollback()
    assert asyncio.run(outbox.pending()) == ()
    connection.close()


def test_commit_notification_from_caller_thread_wakes_bound_relay(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        outbox = SQLiteOutbox(
            tmp_path / "outbox.sqlite3", producer_name="test-producer"
        )
        outbox.bind_loop()
        connection = outbox.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        outbox.append(connection, make_request("threaded-commit"))
        connection.commit()

        await asyncio.to_thread(outbox.notify_after_commit)
        assert outbox.wake_event.is_set()
        connection.close()

    asyncio.run(scenario())


def test_caller_transaction_is_not_committed_by_append(tmp_path: Path) -> None:
    database = tmp_path / "outbox.sqlite3"
    outbox = SQLiteOutbox(database, producer_name="test-producer")
    connection = outbox.open_connection()
    connection.execute("BEGIN IMMEDIATE")
    outbox.append(connection, make_request("caller-owns-commit"))
    assert connection.in_transaction
    connection.rollback()
    connection.close()
    with closing(sqlite3.connect(database)) as observer:
        assert observer.execute(
            "SELECT COUNT(*) FROM eventstream_outbox"
        ).fetchone() == (0,)


def test_concurrent_relay_passes_keep_sequence_order_and_do_not_overlap(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        outbox = SQLiteOutbox(
            tmp_path / "outbox.sqlite3", producer_name="test-producer"
        )
        with outbox.transaction() as connection:
            first = outbox.append(connection, make_request("ordered-first"))
            second = outbox.append(connection, make_request("ordered-second"))

        class BarrierPublisher:
            def __init__(self) -> None:
                self.first_started = asyncio.Event()
                self.release_first = asyncio.Event()
                self.sequences: list[int] = []
                self.active = 0
                self.maximum_active = 0

            async def publish(self, request: PublishRequest) -> PublishReceipt:
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
                self.sequences.append(request.sequence_id)
                if len(self.sequences) == 1:
                    self.first_started.set()
                await self.release_first.wait()
                self.active -= 1
                return PublishReceipt(
                    event_id=request.event.id,
                    topic=request.topic,
                    message_id=f"barrier-{request.sequence_id}",
                    acknowledged_at=datetime(2026, 10, 23, 12, tzinfo=UTC),
                )

            async def close(self) -> None:
                return None

        publisher = BarrierPublisher()
        first_drain = asyncio.create_task(outbox.relay_once(publisher))
        await publisher.first_started.wait()
        second_started = asyncio.Event()

        async def second_drain() -> int:
            second_started.set()
            return await outbox.relay_once(publisher)

        overlapping_drain = asyncio.create_task(second_drain())
        await second_started.wait()
        publisher.release_first.set()

        assert await asyncio.gather(first_drain, overlapping_drain) == [2, 0]
        assert publisher.sequences == [first.sequence_id, second.sequence_id]
        assert publisher.maximum_active == 1
        assert outbox.acknowledged_watermark == second.sequence_id

    asyncio.run(scenario())


def test_relay_does_not_lose_commit_between_empty_check_and_event_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        outbox = SQLiteOutbox(
            tmp_path / "outbox.sqlite3", producer_name="test-producer"
        )

        outbox._wake_event = asyncio.Event()
        original_pending = outbox.pending
        empty_check_complete = asyncio.Event()
        release_empty_check = asyncio.Event()
        pending_calls = 0

        async def pause_after_final_empty_check() -> tuple[OutboxRecord, ...]:
            nonlocal pending_calls
            records = await original_pending()
            pending_calls += 1
            if pending_calls == 2:
                empty_check_complete.set()
                await release_empty_check.wait()
            return records

        monkeypatch.setattr(outbox, "pending", pause_after_final_empty_check)

        class SignallingPublisher(AcceptingPublisher):
            def __init__(self) -> None:
                super().__init__()
                self.published = asyncio.Event()

            async def publish(self, request: PublishRequest) -> PublishReceipt:
                receipt = await super().publish(request)
                self.published.set()
                return receipt

        publisher = SignallingPublisher()
        serving = asyncio.create_task(outbox.serve(publisher))
        try:
            await empty_check_complete.wait()

            def commit_and_notify() -> None:
                connection = outbox.open_connection()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    outbox.append(connection, make_request("wake-during-check"))
                    connection.commit()
                    outbox.notify_after_commit()
                finally:
                    connection.close()

            await asyncio.to_thread(commit_and_notify)
            release_empty_check.set()
            await asyncio.wait_for(publisher.published.wait(), timeout=2)
            assert [request.event.id for request in publisher.requests] == [
                "wake-during-check"
            ]
        finally:
            release_empty_check.set()
            serving.cancel()
            with pytest.raises(asyncio.CancelledError):
                await serving

    asyncio.run(scenario())
