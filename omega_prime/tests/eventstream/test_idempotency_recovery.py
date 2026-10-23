"""Crash recovery for the SQLite idempotency ledger (worker incarnations)."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
from pathlib import Path

from omega_prime.eventstream.idempotency import SQLiteIdempotencyStore

_WORKER = """
import asyncio, os, sys
from omega_prime.eventstream.idempotency import SQLiteIdempotencyStore

async def main() -> None:
    store = SQLiteIdempotencyStore(sys.argv[1])
    claim = await store.claim("gate", "ce-crash", "dead-owner-token")
    assert claim.status == "acquired"
    if sys.argv[2] == "crash":
        os._exit(0)  # killed after claim(): no release, no close
    sys.stdout.write("claimed\\n")
    sys.stdout.flush()
    sys.stdin.readline()

asyncio.run(main())
"""


def test_restarted_worker_recovers_claims_of_a_crashed_process(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    SQLiteIdempotencyStore(database).close()
    crashed = subprocess.run(
        [sys.executable, "-c", _WORKER, str(database), "crash"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert crashed.returncode == 0, crashed.stderr

    async def scenario() -> None:
        restarted = SQLiteIdempotencyStore(database)
        try:
            claim = await restarted.claim("gate", "ce-crash", "fresh-owner")
            assert claim.status == "acquired"
            assert await restarted.complete("gate", "ce-crash", "fresh-owner")
            again = await restarted.claim("gate", "ce-crash", "another-owner")
            assert again.status == "already-complete"
        finally:
            restarted.close()

    asyncio.run(scenario())


def test_claims_of_a_live_worker_process_stay_exclusive(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    SQLiteIdempotencyStore(database).close()
    worker = subprocess.Popen(
        [sys.executable, "-c", _WORKER, str(database), "hold"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert worker.stdout is not None
        assert worker.stdout.readline() == "claimed\n"

        async def contend() -> str:
            other = SQLiteIdempotencyStore(database)
            try:
                return (await other.claim("gate", "ce-crash", "other-owner")).status
            finally:
                other.close()

        assert asyncio.run(contend()) == "busy"
    finally:
        assert worker.stdin is not None
        worker.stdin.close()
        worker.wait(timeout=30)


def test_in_process_incarnations_and_idempotent_close(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "ledger.sqlite3"
        first = SQLiteIdempotencyStore(database)
        second = SQLiteIdempotencyStore(database)
        assert first.incarnation != second.incarnation
        assert (await first.claim("gate", "ce-1", "owner-a")).status == "acquired"
        assert (await second.claim("gate", "ce-1", "owner-b")).status == "busy"
        first.close()
        first.close()
        assert (await second.claim("gate", "ce-1", "owner-b")).status == "acquired"
        assert not await first.release("gate", "ce-1", "owner-a")
        second.close()

    asyncio.run(scenario())


def test_legacy_claims_without_incarnation_require_explicit_reclaim(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "ledger.sqlite3"
        store = SQLiteIdempotencyStore(database)
        claim = await store.claim("gate", "ce-legacy", "old-owner")
        assert claim.status == "acquired"
        connection = store.open_connection()
        connection.execute("UPDATE eventstream_idempotency SET incarnation = NULL")
        connection.close()
        store.close()
        restarted = SQLiteIdempotencyStore(database)
        try:
            assert (await restarted.claim("gate", "ce-legacy", "new")).status == "busy"
            recovered = await restarted.reclaim(
                "gate",
                "ce-legacy",
                stale_owner_token="old-owner",
                new_owner_token="new",
            )
            assert recovered.status == "acquired"
        finally:
            restarted.close()

    asyncio.run(scenario())


def test_existing_ledger_without_incarnation_column_is_migrated(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE eventstream_idempotency ("
        "subscription_hash TEXT NOT NULL, event_hash TEXT NOT NULL, owner_hash TEXT, "
        "status TEXT NOT NULL CHECK (status IN ('claimed', 'complete')), "
        "PRIMARY KEY (subscription_hash, event_hash), "
        "CHECK ((status = 'claimed' AND owner_hash IS NOT NULL) OR "
        "(status = 'complete' AND owner_hash IS NULL)))"
    )
    connection.commit()
    connection.close()
    store = SQLiteIdempotencyStore(database)
    try:
        check = store.open_connection()
        try:
            columns = {
                row[1]
                for row in check.execute(
                    "PRAGMA table_info(eventstream_idempotency)"
                ).fetchall()
            }
        finally:
            check.close()
        assert "incarnation" in columns
        assert asyncio.run(store.claim("gate", "ce-1", "owner")).status == "acquired"
    finally:
        store.close()


def test_resumed_owner_moves_the_claim_to_its_live_incarnation(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "ledger.sqlite3"
        crashed = SQLiteIdempotencyStore(database)
        assert (await crashed.claim("gate", "ce-1", "owner-a")).status == "acquired"
        crashed.close()  # the first incarnation ends with the claim held
        resumed = SQLiteIdempotencyStore(database)
        competitor = SQLiteIdempotencyStore(database)
        try:
            # Same owner token resumes in a new live incarnation ...
            assert (await resumed.claim("gate", "ce-1", "owner-a")).status == (
                "acquired"
            )
            # ... so another worker must not treat the claim as orphaned.
            assert (await competitor.claim("gate", "ce-1", "owner-b")).status == (
                "busy"
            )
            assert await resumed.complete("gate", "ce-1", "owner-a")
        finally:
            competitor.close()
            resumed.close()

    asyncio.run(scenario())


def _stamped_incarnation(store: SQLiteIdempotencyStore) -> str | None:
    connection = store.open_connection()
    try:
        row = connection.execute(
            "SELECT incarnation FROM eventstream_idempotency"
        ).fetchone()
    finally:
        connection.close()
    return None if row is None else row[0]


def test_live_owner_is_kept_when_same_token_rejoins_and_leaves(
    tmp_path: Path,
) -> None:
    """Greptile 'Live claims become recoverable': the original stays the owner."""

    async def scenario() -> None:
        database = tmp_path / "ledger.sqlite3"
        original = SQLiteIdempotencyStore(database)
        rejoining = SQLiteIdempotencyStore(database)
        third = SQLiteIdempotencyStore(database)
        try:
            assert (await original.claim("gate", "ce-1", "owner-a")).status == (
                "acquired"
            )
            assert (await rejoining.claim("gate", "ce-1", "owner-a")).status == (
                "acquired"
            )
            # The live original incarnation is not replaced by the newcomer.
            assert _stamped_incarnation(original) == original.incarnation
            rejoining.close()
            # The original is still alive, so a different owner stays busy.
            assert (await third.claim("gate", "ce-1", "owner-c")).status == "busy"
            assert await original.complete("gate", "ce-1", "owner-a")
            again = await third.claim("gate", "ce-1", "owner-c")
            assert again.status == "already-complete"
        finally:
            third.close()
            rejoining.close()
            original.close()

    asyncio.run(scenario())


def test_claim_stays_live_while_any_same_token_holder_runs(tmp_path: Path) -> None:
    """If the original ends first, the live co-holder still protects the claim."""

    async def scenario() -> None:
        database = tmp_path / "ledger.sqlite3"
        original = SQLiteIdempotencyStore(database)
        rejoining = SQLiteIdempotencyStore(database)
        third = SQLiteIdempotencyStore(database)
        try:
            assert (await original.claim("gate", "ce-1", "owner-a")).status == (
                "acquired"
            )
            assert (await rejoining.claim("gate", "ce-1", "owner-a")).status == (
                "acquired"
            )
            original.close()
            assert (await third.claim("gate", "ce-1", "owner-c")).status == "busy"
            rejoining.close()
            # Every holder is now dead: the orphaned claim becomes recoverable.
            assert (await third.claim("gate", "ce-1", "owner-c")).status == ("acquired")
            assert _stamped_incarnation(third) == third.incarnation
            assert await third.complete("gate", "ce-1", "owner-c")
        finally:
            third.close()
            rejoining.close()
            original.close()

    asyncio.run(scenario())


def test_crashed_process_owner_resumes_with_same_token(tmp_path: Path) -> None:
    """Greptile 'Restarted workers share a claim': a dead owner's token resumes."""
    database = tmp_path / "ledger.sqlite3"
    SQLiteIdempotencyStore(database).close()
    crashed = subprocess.run(
        [sys.executable, "-c", _WORKER, str(database), "crash"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert crashed.returncode == 0, crashed.stderr

    async def scenario() -> None:
        resumed = SQLiteIdempotencyStore(database)
        competitor = SQLiteIdempotencyStore(database)
        try:
            claim = await resumed.claim("gate", "ce-crash", "dead-owner-token")
            assert claim.status == "acquired"
            assert _stamped_incarnation(resumed) == resumed.incarnation
            busy = await competitor.claim("gate", "ce-crash", "other-owner")
            assert busy.status == "busy"
            assert await resumed.complete("gate", "ce-crash", "dead-owner-token")
        finally:
            competitor.close()
            resumed.close()

    asyncio.run(scenario())


def test_live_process_owner_is_not_superseded_by_same_token(tmp_path: Path) -> None:
    """A same-token store cannot strip a live worker process of its claim."""
    database = tmp_path / "ledger.sqlite3"
    SQLiteIdempotencyStore(database).close()
    worker = subprocess.Popen(
        [sys.executable, "-c", _WORKER, str(database), "hold"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert worker.stdout is not None
        assert worker.stdout.readline() == "claimed\n"

        async def scenario() -> None:
            rejoining = SQLiteIdempotencyStore(database)
            before = _stamped_incarnation(rejoining)
            claim = await rejoining.claim("gate", "ce-crash", "dead-owner-token")
            assert claim.status == "acquired"
            assert _stamped_incarnation(rejoining) == before
            rejoining.close()
            third = SQLiteIdempotencyStore(database)
            try:
                busy = await third.claim("gate", "ce-crash", "other-owner")
                assert busy.status == "busy"
            finally:
                third.close()

        asyncio.run(scenario())
    finally:
        assert worker.stdin is not None
        worker.stdin.close()
        worker.wait(timeout=30)
