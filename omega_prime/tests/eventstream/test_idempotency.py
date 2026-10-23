from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from omega_prime.eventstream.idempotency import SQLiteIdempotencyStore


def _create_effect_table(store: SQLiteIdempotencyStore) -> None:
    connection = store.open_connection()
    connection.execute(
        "CREATE TABLE IF NOT EXISTS effects (event_id TEXT PRIMARY KEY, count INTEGER NOT NULL)"
    )
    connection.close()


def _effect_count(store: SQLiteIdempotencyStore, event_id: str) -> int:
    connection = store.open_connection()
    try:
        row = connection.execute(
            "SELECT count FROM effects WHERE event_id = ?", (event_id,)
        ).fetchone()
        return 0 if row is None else int(row[0])
    finally:
        connection.close()


def test_claim_competition_completion_and_reopen_are_durable(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "ledger.sqlite3"
        first = SQLiteIdempotencyStore(database)
        competing = SQLiteIdempotencyStore(database)
        connection = first.open_connection()
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        connection.close()

        acquired = await first.claim("gate", "event-1", "owner-one")
        assert acquired.status == "acquired"
        assert acquired.owner_token == "owner-one"
        busy = await competing.claim("gate", "event-1", "owner-two")
        assert busy.status == "busy"
        same_owner = await competing.claim("gate", "event-1", "owner-one")
        assert same_owner.status == "acquired"

        assert await competing.complete("gate", "event-1", "owner-two") is False
        assert await first.complete("gate", "event-1", "owner-one") is True
        assert await first.release("gate", "event-1", "owner-one") is False

        reopened = SQLiteIdempotencyStore(database)
        completed = await reopened.claim("gate", "event-1", "owner-three")
        assert completed.status == "already-complete"
        assert completed.owner_token is None

    asyncio.run(scenario())


def test_explicit_reclaim_invalidates_stale_complete_and_release(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = SQLiteIdempotencyStore(tmp_path / "ledger.sqlite3")
        assert (
            await store.claim("review", "event-2", "owner-old")
        ).status == "acquired"

        recovered = await store.reclaim(
            "review",
            "event-2",
            stale_owner_token="owner-old",
            new_owner_token="owner-new",
        )
        assert recovered.status == "acquired"
        assert recovered.owner_token == "owner-new"
        assert await store.complete("review", "event-2", "owner-old") is False
        assert await store.release("review", "event-2", "owner-old") is False
        assert (await store.claim("review", "event-2", "owner-third")).status == "busy"
        assert await store.complete("review", "event-2", "owner-new") is True

    asyncio.run(scenario())


def test_claim_and_release_respect_caller_transaction(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteIdempotencyStore(tmp_path / "ledger.sqlite3")
        connection = store.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        uncommitted = await store.claim(
            "transactional",
            "event-transactional",
            "owner-uncommitted",
            connection=connection,
        )
        assert uncommitted.status == "acquired"
        assert connection.in_transaction
        connection.rollback()
        connection.close()

        committed = await store.claim(
            "transactional", "event-transactional", "owner-committed"
        )
        assert committed.status == "acquired"

        connection = store.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        assert await store.release(
            "transactional",
            "event-transactional",
            "owner-committed",
            connection=connection,
        )
        assert connection.in_transaction
        connection.rollback()
        connection.close()

        assert (
            await store.claim("transactional", "event-transactional", "owner-other")
        ).status == "busy"
        assert await store.release(
            "transactional", "event-transactional", "owner-committed"
        )
        assert (
            await store.claim("transactional", "event-transactional", "owner-other")
        ).status == "acquired"

    asyncio.run(scenario())


def test_local_effect_and_completion_share_the_callers_transaction(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "ledger.sqlite3"
        store = SQLiteIdempotencyStore(database)
        _create_effect_table(store)
        claim = await store.claim("merge-executor", "event-3", "effect-owner")
        assert claim.status == "acquired"

        connection = store.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO effects(event_id, count) VALUES (?, 1)", ("event-3",)
        )
        assert (
            await store.complete(
                "merge-executor", "event-3", "effect-owner", connection=connection
            )
            is True
        )
        connection.rollback()
        connection.close()

        # A process restart sees the durable claim, but neither the rolled-back
        # local effect nor a prematurely completed ledger record.
        reopened = SQLiteIdempotencyStore(database)
        assert (
            await reopened.claim("merge-executor", "event-3", "intruder")
        ).status == "busy"
        assert _effect_count(reopened, "event-3") == 0

        replacement = await reopened.reclaim(
            "merge-executor",
            "event-3",
            stale_owner_token="effect-owner",
            new_owner_token="recovery-owner",
        )
        assert replacement.status == "acquired"
        connection = reopened.open_connection()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO effects(event_id, count) VALUES (?, 1)", ("event-3",)
        )
        assert (
            await reopened.complete(
                "merge-executor", "event-3", "recovery-owner", connection=connection
            )
            is True
        )
        connection.commit()
        connection.close()

        assert _effect_count(reopened, "event-3") == 1
        duplicate = await reopened.claim(
            "merge-executor", "event-3", "owner-after-restart"
        )
        assert duplicate.status == "already-complete"
        assert _effect_count(reopened, "event-3") == 1

    asyncio.run(scenario())


def test_owner_tokens_and_identifiers_are_not_persisted_as_plaintext(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "ledger.sqlite3"
        store = SQLiteIdempotencyStore(database)
        await store.claim("gate", "synthetic-secret-event-id", "synthetic-owner-secret")
        connection = sqlite3.connect(database, timeout=0.0)
        rows = connection.execute(
            "SELECT subscription_hash, event_hash, owner_hash "
            "FROM eventstream_idempotency"
        ).fetchall()
        connection.close()
        wal_path = Path(f"{database}-wal")
        raw_storage = database.read_bytes()
        if wal_path.exists():
            raw_storage += wal_path.read_bytes()
        assert b"synthetic-owner-secret" not in raw_storage
        assert b"synthetic-secret-event-id" not in raw_storage
        assert all(
            "synthetic-owner-secret" not in str(row)
            and "synthetic-secret-event-id" not in str(row)
            for row in rows
        )

    asyncio.run(scenario())


def test_busy_and_completed_claims_do_not_expose_owner_tokens(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteIdempotencyStore(tmp_path / "ledger.sqlite3")
        await store.claim("blocked", "event-4", "synthetic-owner-token")
        busy = await store.claim("blocked", "event-4", "other-owner")
        assert busy.status == "busy"
        assert busy.owner_token is None
        assert await store.complete("blocked", "event-4", "synthetic-owner-token")
        completed = await store.claim("blocked", "event-4", "other-owner")
        assert completed.status == "already-complete"
        assert completed.owner_token is None

    asyncio.run(scenario())


def test_external_effect_deduplicates_by_cloud_event_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteIdempotencyStore(tmp_path / "ledger.sqlite3")
        external_effects: dict[str, str] = {}
        external_calls: list[str] = []

        def perform_external_effect(*, idempotency_key: str, event_id: str) -> None:
            assert idempotency_key == event_id
            external_calls.append(idempotency_key)
            external_effects.setdefault(idempotency_key, event_id)

        event_id = "ce-external-1"
        claim = await store.claim("external-handler", event_id, "owner-before-crash")
        assert claim.status == "acquired"
        perform_external_effect(idempotency_key=event_id, event_id=event_id)

        # Simulate a crash after the remote effect but before ledger completion.
        reclaimed = await store.reclaim(
            "external-handler",
            event_id,
            stale_owner_token="owner-before-crash",
            new_owner_token="owner-after-recovery",
        )
        assert reclaimed.status == "acquired"
        perform_external_effect(idempotency_key=event_id, event_id=event_id)
        assert external_effects == {event_id: event_id}
        assert external_calls == [event_id, event_id]

        assert await store.complete(
            "external-handler", event_id, "owner-after-recovery"
        )
        duplicate = await store.claim("external-handler", event_id, "next-owner")
        assert duplicate.status == "already-complete"
        assert external_effects == {event_id: event_id}
        assert external_calls == [event_id, event_id]

    asyncio.run(scenario())


def test_release_allows_a_fresh_owner_but_not_across_subscription_boundaries(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = SQLiteIdempotencyStore(tmp_path / "ledger.sqlite3")
        assert (await store.claim("handler-a", "shared-event", "owner-a")).status == (
            "acquired"
        )
        assert (await store.claim("handler-b", "shared-event", "owner-b")).status == (
            "acquired"
        )

        assert await store.release("handler-a", "shared-event", "wrong-owner") is False
        assert (await store.claim("handler-a", "shared-event", "intruder")).status == (
            "busy"
        )
        assert await store.release("handler-a", "shared-event", "owner-a") is True

        replacement = await store.claim("handler-a", "shared-event", "owner-new")
        assert replacement.status == "acquired"
        assert replacement.owner_token == "owner-new"
        assert (await store.claim("handler-b", "shared-event", "intruder")).status == (
            "busy"
        )

    asyncio.run(scenario())


def test_complete_requires_the_active_claim_and_completion_is_immutable(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = SQLiteIdempotencyStore(tmp_path / "ledger.sqlite3")
        assert await store.complete("handler", "missing-event", "owner") is False
        assert (await store.claim("handler", "event", "owner")).status == "acquired"
        assert await store.complete("handler", "event", "not-owner") is False
        assert await store.complete("handler", "event", "owner") is True
        assert await store.complete("handler", "event", "owner") is False
        assert await store.release("handler", "event", "owner") is False
        assert (await store.claim("handler", "event", "new-owner")).status == (
            "already-complete"
        )

    asyncio.run(scenario())


def test_idempotency_rejects_invalid_identities_and_caller_connections(
    tmp_path: Path,
) -> None:
    store = SQLiteIdempotencyStore(tmp_path / "ledger.sqlite3")

    async def scenario() -> None:
        with pytest.raises(ValueError, match="nonempty"):
            await store.claim("", "event", "owner")
        with pytest.raises(ValueError, match="nonempty"):
            await store.claim("handler", "   ", "owner")
        with pytest.raises(ValueError, match="nonempty"):
            await store.claim("handler", "event", "")
        foreign = sqlite3.connect(tmp_path / "other.sqlite3")
        try:
            foreign.execute("BEGIN IMMEDIATE")
            with pytest.raises(ValueError, match="different database"):
                await store.claim("handler", "event", "owner", connection=foreign)
        finally:
            foreign.rollback()
            foreign.close()
        connection = store.open_connection()
        try:
            with pytest.raises(ValueError, match="transaction"):
                await store.claim("handler", "event", "owner", connection=connection)
        finally:
            connection.close()
        # Caller connections must already be durable; degrade AFTER open, BEFORE begin.
        degraded = store.open_connection()
        try:
            degraded.execute("PRAGMA synchronous=OFF")
            degraded.execute("BEGIN IMMEDIATE")
            with pytest.raises(ValueError, match="WAL and synchronous FULL"):
                await store.claim("handler", "event", "owner", connection=degraded)
        finally:
            if degraded.in_transaction:
                degraded.rollback()
            degraded.close()

        missing = await store.reclaim(
            "handler",
            "never-seen",
            stale_owner_token="stale",
            new_owner_token="fresh",
        )
        assert missing.status == "acquired"
        assert (await store.claim("handler", "never-seen", "intruder")).status == "busy"
        assert await store.complete("handler", "never-seen", "fresh") is True
        completed = await store.reclaim(
            "handler",
            "never-seen",
            stale_owner_token="fresh",
            new_owner_token="after-complete",
        )
        assert completed.status == "already-complete"
        assert (await store.claim("handler", "live", "owner-a")).status == "acquired"
        busy = await store.reclaim(
            "handler",
            "live",
            stale_owner_token="not-the-owner",
            new_owner_token="owner-b",
        )
        assert busy.status == "busy"

    asyncio.run(scenario())


def test_reclaim_transfers_ownership_only_for_the_stated_stale_owner(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = SQLiteIdempotencyStore(tmp_path / "ledger.sqlite3")
        assert (await store.claim("handler", "event", "owner-a")).status == "acquired"
        transferred = await store.reclaim(
            "handler",
            "event",
            stale_owner_token="owner-a",
            new_owner_token="owner-b",
        )
        assert transferred.status == "acquired"
        assert transferred.owner_token == "owner-b"
        assert (await store.claim("handler", "event", "owner-a")).status == "busy"
        assert await store.complete("handler", "event", "owner-b") is True

    asyncio.run(scenario())
