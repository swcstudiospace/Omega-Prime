"""SQLite claim-before-effect ledger with owner-token compare-and-set semantics."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from omega_prime.eventstream.ports import ClaimResult


class SQLiteIdempotencyStore:
    """Durable idempotency claims keyed by subscription and CloudEvent id.

    Claim owners are stored only as SHA-256 digests. A claim is not leased by
    time: recovery must explicitly replace the prior owner with ``reclaim``
    after that owner's lifecycle is known to have ended. ``connection=`` on
    complete/release allows the caller to commit a local business effect and
    the corresponding ledger transition in one SQLite transaction.
    """

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).expanduser().resolve()
        self._initialize()

    def open_connection(self) -> sqlite3.Connection:
        """Open a WAL/FULL connection suitable for caller-owned transactions."""
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
                raise RuntimeError("idempotency database durability is unavailable")
        except BaseException:
            connection.close()
            raise
        return connection

    def _initialize(self) -> None:
        connection = self.open_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS eventstream_idempotency ("
                "subscription_hash TEXT NOT NULL, "
                "event_hash TEXT NOT NULL, "
                "owner_hash TEXT, "
                "status TEXT NOT NULL CHECK (status IN ('claimed', 'complete')), "
                "PRIMARY KEY (subscription_hash, event_hash), "
                "CHECK ((status = 'claimed' AND owner_hash IS NOT NULL) OR "
                "(status = 'complete' AND owner_hash IS NULL)))"
            )
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validate_identity(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("idempotency identity must be nonempty")
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_owner(owner_token: str) -> str:
        if not isinstance(owner_token, str) or not owner_token:
            raise ValueError("idempotency owner token must be nonempty")
        return hashlib.sha256(owner_token.encode("utf-8")).hexdigest()

    def _validate_caller_connection(self, connection: sqlite3.Connection) -> None:
        if not connection.in_transaction:
            raise ValueError(
                "caller-owned idempotency connection must be in a transaction"
            )
        databases = connection.execute("PRAGMA database_list").fetchall()
        main_path = next((str(row[2]) for row in databases if row[1] == "main"), "")
        if not main_path or Path(main_path).expanduser().resolve() != self.database:
            raise ValueError("caller transaction belongs to a different database")
        mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        sync = int(connection.execute("PRAGMA synchronous").fetchone()[0])
        if mode != "wal" or sync < 2:
            raise ValueError("caller transaction must use WAL and synchronous FULL")

    @contextmanager
    def _transaction(
        self, connection: sqlite3.Connection | None
    ) -> Iterator[sqlite3.Connection]:
        owned = connection is None
        active = self.open_connection() if connection is None else connection
        try:
            if owned:
                active.execute("BEGIN IMMEDIATE")
            else:
                self._validate_caller_connection(active)
            yield active
            if owned:
                active.commit()
        except BaseException:
            if owned and active.in_transaction:
                active.rollback()
            raise
        finally:
            if owned:
                active.close()

    async def claim(
        self,
        subscription: str,
        event_id: str,
        owner_token: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> ClaimResult:
        """Claim once or report completion/contention without exposing owner data."""
        subscription_hash = self._validate_identity(subscription)
        event_hash = self._validate_identity(event_id)
        owner_hash = self._validate_owner(owner_token)
        try:
            with self._transaction(connection) as database:
                row = database.execute(
                    "SELECT owner_hash, status FROM eventstream_idempotency "
                    "WHERE subscription_hash = ? AND event_hash = ?",
                    (subscription_hash, event_hash),
                ).fetchone()
                if row is None:
                    database.execute(
                        "INSERT INTO eventstream_idempotency "
                        "(subscription_hash, event_hash, owner_hash, status) "
                        "VALUES (?, ?, ?, 'claimed')",
                        (subscription_hash, event_hash, owner_hash),
                    )
                    return ClaimResult.acquired(owner_token)
                persisted_owner, status = row
                if status == "complete":
                    return ClaimResult.already_complete()
                if persisted_owner == owner_hash:
                    return ClaimResult.acquired(owner_token)
                return ClaimResult.busy()
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).casefold() or "busy" in str(exc).casefold():
                return ClaimResult.busy()
            raise

    async def complete(
        self,
        subscription: str,
        event_id: str,
        owner_token: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """Compare-and-set the active owner to completed, optionally in a caller tx."""
        subscription_hash = self._validate_identity(subscription)
        event_hash = self._validate_identity(event_id)
        owner_hash = self._validate_owner(owner_token)
        with self._transaction(connection) as database:
            result = database.execute(
                "UPDATE eventstream_idempotency SET owner_hash = NULL, status = 'complete' "
                "WHERE subscription_hash = ? AND event_hash = ? "
                "AND owner_hash = ? AND status = 'claimed'",
                (subscription_hash, event_hash, owner_hash),
            )
            return result.rowcount == 1

    async def release(
        self,
        subscription: str,
        event_id: str,
        owner_token: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """Delete only this active claim; completed rows are immutable and durable."""
        subscription_hash = self._validate_identity(subscription)
        event_hash = self._validate_identity(event_id)
        owner_hash = self._validate_owner(owner_token)
        with self._transaction(connection) as database:
            result = database.execute(
                "DELETE FROM eventstream_idempotency "
                "WHERE subscription_hash = ? AND event_hash = ? "
                "AND owner_hash = ? AND status = 'claimed'",
                (subscription_hash, event_hash, owner_hash),
            )
            return result.rowcount == 1

    async def reclaim(
        self,
        subscription: str,
        event_id: str,
        *,
        stale_owner_token: str,
        new_owner_token: str,
        connection: sqlite3.Connection | None = None,
    ) -> ClaimResult:
        """Explicitly transfer a known-ended owner's claim with a token CAS.

        This method has no lease timeout and is never called by a timer or
        background reaper. If the row is absent, the explicit recovery caller
        acquires it; if another owner has already changed it, recovery is busy.
        """
        subscription_hash = self._validate_identity(subscription)
        event_hash = self._validate_identity(event_id)
        stale_hash = self._validate_owner(stale_owner_token)
        new_hash = self._validate_owner(new_owner_token)
        with self._transaction(connection) as database:
            row = database.execute(
                "SELECT owner_hash, status FROM eventstream_idempotency "
                "WHERE subscription_hash = ? AND event_hash = ?",
                (subscription_hash, event_hash),
            ).fetchone()
            if row is None:
                database.execute(
                    "INSERT INTO eventstream_idempotency "
                    "(subscription_hash, event_hash, owner_hash, status) "
                    "VALUES (?, ?, ?, 'claimed')",
                    (subscription_hash, event_hash, new_hash),
                )
                return ClaimResult.acquired(new_owner_token)
            persisted_owner, status = row
            if status == "complete":
                return ClaimResult.already_complete()
            if persisted_owner != stale_hash:
                return ClaimResult.busy()
            result = database.execute(
                "UPDATE eventstream_idempotency SET owner_hash = ? "
                "WHERE subscription_hash = ? AND event_hash = ? "
                "AND owner_hash = ? AND status = 'claimed'",
                (new_hash, subscription_hash, event_hash, stale_hash),
            )
            return (
                ClaimResult.acquired(new_owner_token)
                if result.rowcount == 1
                else ClaimResult.busy()
            )


__all__ = ["SQLiteIdempotencyStore"]
