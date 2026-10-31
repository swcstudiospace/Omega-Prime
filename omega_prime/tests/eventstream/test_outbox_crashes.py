from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from contextlib import closing, suppress
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from omega_prime.eventstream.envelope import CloudEvent, PublishReceipt, PublishRequest
from omega_prime.eventstream.idempotency import SQLiteIdempotencyStore
from omega_prime.eventstream.outbox import SQLiteOutbox
from omega_prime.eventstream.taxonomy import derive_key, schema_uri_for_type


def make_request(event_id: str) -> PublishRequest:
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
                "actor": "alice",
                "title": "Crash recovery event",
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
        producer_name="crash-test-producer",
    )


class InjectedCrash(RuntimeError):
    """Synthetic process crash at a durable outbox boundary."""


class DurableDestination:
    """SQLite-backed fake broker separating raw sends from accepted messages."""

    def __init__(self, database: Path) -> None:
        self.database = database
        with closing(self.connect()) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS raw_sends ("
                "sequence_id INTEGER NOT NULL, event_id TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS accepted ("
                "sequence_id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, topic TEXT NOT NULL)"
            )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=0.0, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    async def publish(
        self, request: PublishRequest, *, fault: str | None = None
    ) -> PublishReceipt:
        if fault == "before-send":
            raise InjectedCrash("injected failure before send")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO raw_sends(sequence_id, event_id) VALUES (?, ?)",
                (request.sequence_id, request.event.id),
            )
            connection.commit()
            if fault == "after-send-before-ack":
                raise InjectedCrash("injected failure after send before broker ack")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO accepted(sequence_id, event_id, topic) "
                "VALUES (?, ?, ?)",
                (request.sequence_id, request.event.id, request.topic),
            )
            connection.commit()
            if fault == "after-ack-before-mark":
                raise InjectedCrash("injected failure after broker ack")
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return PublishReceipt(
            event_id=request.event.id,
            topic=request.topic,
            message_id=f"durable-{request.sequence_id}",
            acknowledged_at=datetime(2026, 10, 23, 12, tzinfo=UTC),
        )

    async def close(self) -> None:
        return None

    def accepted_sequences(self) -> set[int]:
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT sequence_id FROM accepted").fetchall()
        return {int(row[0]) for row in rows}

    def accepted_events(self) -> tuple[tuple[int, str], ...]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT sequence_id, event_id FROM accepted ORDER BY sequence_id"
            ).fetchall()
        return tuple((int(sequence), str(event_id)) for sequence, event_id in rows)

    def raw_event_ids(self) -> tuple[str, ...]:
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT event_id FROM raw_sends").fetchall()
        return tuple(str(row[0]) for row in rows)

    def raw_send_count(self, sequence_id: int) -> int:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM raw_sends WHERE sequence_id = ?", (sequence_id,)
            ).fetchone()
        return int(row[0])


class FaultingPublisher:
    def __init__(self, destination: DurableDestination, fault: str | None) -> None:
        self.destination = destination
        self.fault = fault

    async def publish(self, request: PublishRequest) -> PublishReceipt:
        fault, self.fault = self.fault, None
        return await self.destination.publish(request, fault=fault)

    async def close(self) -> None:
        return None


async def process_duplicate_delivery(
    store: SQLiteIdempotencyStore, event_id: str
) -> None:
    owner = "consumer-owner-for-" + hashlib.sha256(event_id.encode()).hexdigest()
    claim = await store.claim("crash-test", event_id, owner)
    if claim.status != "acquired":
        return
    connection = store.open_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        event_hash = hashlib.sha256(event_id.encode()).hexdigest()
        connection.execute(
            "INSERT INTO crash_test_effects(event_hash) VALUES (?)",
            (event_hash,),
        )
        assert await store.complete(
            "crash-test", event_id, owner, connection=connection
        )
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def setup_effect_table(store: SQLiteIdempotencyStore) -> None:
    connection = store.open_connection()
    connection.execute(
        "CREATE TABLE IF NOT EXISTS crash_test_effects (event_hash TEXT PRIMARY KEY)"
    )
    connection.close()


def effect_count(store: SQLiteIdempotencyStore, event_id: str) -> int:
    event_hash = hashlib.sha256(event_id.encode()).hexdigest()
    connection = store.open_connection()
    try:
        row = connection.execute(
            "SELECT COUNT(*) FROM crash_test_effects WHERE event_hash = ?",
            (event_hash,),
        ).fetchone()
        return int(row[0])
    finally:
        connection.close()


async def exercise_accepted_deliveries(
    store: SQLiteIdempotencyStore, destination: DurableDestination
) -> None:
    setup_effect_table(store)
    for _sequence, event_id in destination.accepted_events():
        # Multiple raw sends may be observed for one accepted destination event;
        # claim/completion makes the local effect visible once.
        await process_duplicate_delivery(store, event_id)
        await process_duplicate_delivery(store, event_id)
        assert effect_count(store, event_id) == 1


@pytest.mark.parametrize(
    "fault_site",
    [
        "before-append",
        "after-append-before-commit",
        "after-commit-before-notify",
        "before-send",
        "after-send-before-ack",
        "after-ack-before-mark",
        "before-mark-sent",
        "before-watermark-commit",
        "after-mark-before-cleanup",
        "after-cleanup",
    ],
)
def test_forced_crash_boundaries_reopen_real_sqlite_files(
    tmp_path: Path, fault_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        database = tmp_path / "outbox.sqlite3"
        destination = DurableDestination(tmp_path / "destination.sqlite3")
        outbox = SQLiteOutbox(database, producer_name="crash-test-producer")
        ledger = SQLiteIdempotencyStore(database)
        setup_effect_table(ledger)
        event_id = f"forced-{fault_site}"
        request = make_request(event_id)
        connection = outbox.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        row = None
        if fault_site != "before-append":
            row = outbox.append(connection, request)
        if fault_site in {"before-append", "after-append-before-commit"}:
            connection.rollback()
            connection.close()
            outbox = SQLiteOutbox(database, producer_name="crash-test-producer")
            assert event_id not in destination.raw_event_ids()
            assert await outbox.pending() == ()
            return

        connection.commit()
        connection.close()
        outbox = SQLiteOutbox(database, producer_name="crash-test-producer")
        assert row is not None

        if fault_site == "after-commit-before-notify":
            # Reopening and scanning, not a remembered Event, finds the row.
            assert await outbox.relay_once(destination) == 1
        elif fault_site in {
            "before-send",
            "after-send-before-ack",
            "after-ack-before-mark",
        }:
            with suppress(InjectedCrash):
                await outbox.relay_once(FaultingPublisher(destination, fault_site))
            outbox = SQLiteOutbox(database, producer_name="crash-test-producer")
            assert await outbox.relay_once(destination) == 1
        elif fault_site == "before-mark-sent":

            def fail_before_mark(_sequence_id: int, _receipt: PublishReceipt) -> int:
                raise InjectedCrash("injected crash before mark-sent")

            monkeypatch.setattr(outbox, "mark_sent", fail_before_mark)
            with suppress(InjectedCrash):
                await outbox.relay_once(destination)
            monkeypatch.undo()
            outbox = SQLiteOutbox(database, producer_name="crash-test-producer")
            assert await outbox.relay_once(destination) == 1
        elif fault_site == "before-watermark-commit":
            original_write_meta = outbox._write_meta

            def fail_before_watermark(
                connection: sqlite3.Connection, key: str, value: int
            ) -> None:
                if key == "acked_watermark":
                    raise InjectedCrash("injected crash before watermark commit")
                original_write_meta(connection, key, value)

            monkeypatch.setattr(outbox, "_write_meta", fail_before_watermark)
            with suppress(InjectedCrash):
                await outbox.relay_once(destination)
            monkeypatch.undo()
            reopened = SQLiteOutbox(database, producer_name="crash-test-producer")
            pending_row = await reopened.get(row.sequence_id)
            assert pending_row is not None and pending_row.status == "pending"
            outbox = reopened
            assert await outbox.relay_once(destination) == 1
        elif fault_site == "after-mark-before-cleanup":
            assert await outbox.relay_once(destination) == 1
            assert outbox.acknowledged_watermark == row.sequence_id
            sent_row = await outbox.get(row.sequence_id)
            assert sent_row is not None and sent_row.status == "sent"
        else:
            assert await outbox.relay_once(destination) == 1
            assert outbox.acknowledged_watermark == row.sequence_id
            assert outbox.cleanup_sent() == 1

        # The outbox, ledger, and destination are reconstructed from their SQLite
        # files; only the durable fake destination survives as external state.
        outbox = SQLiteOutbox(database, producer_name="crash-test-producer")
        ledger = SQLiteIdempotencyStore(database)
        await exercise_accepted_deliveries(ledger, destination)
        assert row.sequence_id in destination.accepted_sequences()
        assert event_id in destination.raw_event_ids()
        assert effect_count(ledger, event_id) == 1
        assert outbox.acknowledged_watermark == row.sequence_id
        connection = outbox.open_connection()
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        connection.close()

    asyncio.run(scenario())


class OutboxCrashStateMachine(RuleBasedStateMachine):
    """Generated caller transactions, broker faults, recovery and local effects."""

    def __init__(self) -> None:
        super().__init__()
        self._temporary = TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.database = self.root / "outbox.sqlite3"
        self.destination = DurableDestination(self.root / "destination.sqlite3")
        self.next_event_number = 0
        self.committed: dict[int, str] = {}
        self.committed_sequences: set[int] = set()
        self.rolled_back: set[str] = set()
        self._reopen()

    def _reopen(self) -> None:
        # Replacing both objects models discarding process-local state.
        self.outbox = SQLiteOutbox(
            self.database, producer_name="crash-test-producer", max_bytes=1_000_000
        )
        self.ledger = SQLiteIdempotencyStore(self.database)
        setup_effect_table(self.ledger)

    def _event_id(self) -> str:
        self.next_event_number += 1
        return f"generated-event-{self.next_event_number}"

    @rule(notify=st.booleans())
    def commit_append_then_reopen(self, notify: bool) -> None:
        event_id = self._event_id()
        connection = self.outbox.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        row = self.outbox.append(connection, make_request(event_id))
        connection.commit()
        connection.close()
        self.committed[row.sequence_id] = event_id
        assert row.sequence_id not in self.committed_sequences
        self.committed_sequences.add(row.sequence_id)
        if notify:
            self.outbox.notify_after_commit()
        self._reopen()

    @rule()
    def rollback_append_then_reopen(self) -> None:
        event_id = self._event_id()
        connection = self.outbox.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        self.outbox.append(connection, make_request(event_id))
        connection.rollback()
        connection.close()
        self.rolled_back.add(event_id)
        self._reopen()

    @rule(
        fault=st.sampled_from(
            [None, "before-send", "after-send-before-ack", "after-ack-before-mark"]
        )
    )
    def drain_with_crash_window(self, fault: str | None) -> None:
        with suppress(InjectedCrash):
            asyncio.run(
                self.outbox.relay_once(FaultingPublisher(self.destination, fault))
            )
        asyncio.run(exercise_accepted_deliveries(self.ledger, self.destination))
        self._reopen()

    @rule()
    def cleanup_acknowledged_rows_then_reopen(self) -> None:
        self.outbox.cleanup_sent()
        self._reopen()

    @rule()
    def explicit_reconnect_then_drain(self) -> None:
        self.outbox.notify_reconnected()
        with suppress(InjectedCrash):
            asyncio.run(self.outbox.relay_once(self.destination))
        asyncio.run(exercise_accepted_deliveries(self.ledger, self.destination))
        self._reopen()

    @invariant()
    def every_committed_event_is_durable_or_accepted(self) -> None:
        connection = sqlite3.connect(self.database)
        try:
            rows = connection.execute(
                "SELECT sequence_id, event_id, status FROM eventstream_outbox"
            ).fetchall()
        finally:
            connection.close()
        rows_by_sequence = {
            int(seq): (str(event_id), str(status)) for seq, event_id, status in rows
        }
        accepted = self.destination.accepted_sequences()
        for sequence_id, event_id in self.committed.items():
            row = rows_by_sequence.get(sequence_id)
            assert row is not None or sequence_id in accepted
            if row is not None:
                assert row[0] == event_id
                assert row[1] in {"pending", "rejected", "sent"}
            assert event_id not in self.rolled_back
        assert not (set(self.destination.raw_event_ids()) & self.rolled_back)

    @invariant()
    def watermark_never_crosses_unresolved_rows(self) -> None:
        connection = sqlite3.connect(self.database)
        try:
            watermark = int(
                connection.execute(
                    "SELECT value FROM eventstream_meta WHERE key='acked_watermark'"
                ).fetchone()[0]
            )
            unresolved = connection.execute(
                "SELECT COUNT(*) FROM eventstream_outbox "
                "WHERE sequence_id <= ? AND status IN ('pending', 'rejected')",
                (watermark,),
            ).fetchone()[0]
        finally:
            connection.close()
        assert unresolved == 0

    @invariant()
    def once_only_effects_hold_under_raw_duplicates(self) -> None:
        for sequence_id, event_id in self.destination.accepted_events():
            assert self.destination.raw_send_count(sequence_id) >= 1
            assert effect_count(self.ledger, event_id) == 1

    def teardown(self) -> None:
        self._temporary.cleanup()


TestOutboxCrashStateMachine = OutboxCrashStateMachine.TestCase
TestOutboxCrashStateMachine.settings = settings(
    max_examples=35, stateful_step_count=30, deadline=None
)
