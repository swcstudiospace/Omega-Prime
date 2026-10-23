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
