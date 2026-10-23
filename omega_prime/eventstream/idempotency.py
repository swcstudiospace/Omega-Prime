"""SQLite claim-before-effect ledger with owner-token compare-and-set semantics."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from omega_prime.eventstream.ports import ClaimResult


class SQLiteIdempotencyStore:
    """Durable idempotency claims keyed by subscription and CloudEvent id.

    Claim owners are stored only as SHA-256 digests. A claim is not leased by
    time. Each store instance is a worker *incarnation* that holds an exclusive
    ``flock`` on ``<database>.owners/<incarnation>.lock`` for its lifetime and
    stamps every claim it makes with that incarnation. The kernel drops the
    lock when the owning process exits for any reason (including SIGKILL), so a
    restarted worker that meets a ``claimed`` row whose incarnation lock is free
    knows that owner is dead and transfers the claim to itself instead of
    reporting ``busy`` forever. A live owner (same or other process) keeps its
    lock, so its claims stay exclusive. When a second live incarnation
    presents the same owner token, the original incarnation is kept and the
    newcomer is recorded as an additional *holder* in
    ``eventstream_idempotency_holders``; a claim is treated as orphaned only
    when the stamped incarnation **and** every recorded holder are dead, so a
    genuinely restarted owner can resume while a live owner is never
    superseded. WAL SQLite already requires every
    sharer to be on one host, which is exactly the scope of ``flock``.
    ``reclaim`` remains for explicit, caller-proven recovery. ``connection=``
    on complete/release allows the caller to commit a local business effect and
    the corresponding ledger transition in one SQLite transaction.
    """

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).expanduser().resolve()
        self._initialize()
        self._owners_dir = self.database.with_name(self.database.name + ".owners")
        self._owners_dir.mkdir(mode=0o700, exist_ok=True)
        self.incarnation = uuid.uuid4().hex
        self._lock_path = self._owners_dir / f"{self.incarnation}.lock"
        descriptor = os.open(
            self._lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(descriptor)
            raise
        self._lock_fd: int | None = descriptor

    def close(self) -> None:
        """End this incarnation; its remaining claims become recoverable."""
        descriptor, self._lock_fd = self._lock_fd, None
        if descriptor is None:
            return
        with contextlib.suppress(FileNotFoundError):
            self._lock_path.unlink()
        os.close(descriptor)

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown path
        with contextlib.suppress(Exception):
            self.close()

    def _incarnation_alive(self, incarnation: str | None) -> bool:
        """True unless ``incarnation``'s lock can be taken (its process ended)."""
        if not incarnation:
            # Claims without an incarnation predate recovery stamping; only an
            # explicit ``reclaim`` may transfer them.
            return True
        if incarnation == self.incarnation:
            return self._lock_fd is not None
        if not all(char in "0123456789abcdef" for char in incarnation):
            return True
        path = self._owners_dir / f"{incarnation}.lock"
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        except FileNotFoundError:
            return False
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            return False
        finally:
            os.close(descriptor)

    def _claim_alive(
        self,
        database: sqlite3.Connection,
        subscription_hash: str,
        event_hash: str,
        incarnation: str | None,
    ) -> bool:
        """True while the stamped incarnation or any co-holder is still alive."""
        if self._incarnation_alive(incarnation):
            return True
        holders = database.execute(
            "SELECT incarnation FROM eventstream_idempotency_holders "
            "WHERE subscription_hash = ? AND event_hash = ?",
            (subscription_hash, event_hash),
        ).fetchall()
        return any(self._incarnation_alive(str(row[0])) for row in holders)

    @staticmethod
    def _clear_holders(
        database: sqlite3.Connection, subscription_hash: str, event_hash: str
    ) -> None:
        database.execute(
            "DELETE FROM eventstream_idempotency_holders "
            "WHERE subscription_hash = ? AND event_hash = ?",
            (subscription_hash, event_hash),
        )

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
                "incarnation TEXT, "
                "PRIMARY KEY (subscription_hash, event_hash), "
                "CHECK ((status = 'claimed' AND owner_hash IS NOT NULL) OR "
                "(status = 'complete' AND owner_hash IS NULL)))"
            )
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(eventstream_idempotency)"
                ).fetchall()
            }
            if "incarnation" not in columns:
                connection.execute(
                    "ALTER TABLE eventstream_idempotency ADD COLUMN incarnation TEXT"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS eventstream_idempotency_holders ("
                "subscription_hash TEXT NOT NULL, "
                "event_hash TEXT NOT NULL, "
                "incarnation TEXT NOT NULL, "
                "PRIMARY KEY (subscription_hash, event_hash, incarnation))"
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
                    "SELECT owner_hash, status, incarnation "
                    "FROM eventstream_idempotency "
                    "WHERE subscription_hash = ? AND event_hash = ?",
                    (subscription_hash, event_hash),
                ).fetchone()
                if row is None:
                    self._clear_holders(database, subscription_hash, event_hash)
                    database.execute(
                        "INSERT INTO eventstream_idempotency "
                        "(subscription_hash, event_hash, owner_hash, status, "
                        "incarnation) VALUES (?, ?, ?, 'claimed', ?)",
                        (subscription_hash, event_hash, owner_hash, self.incarnation),
                    )
                    return ClaimResult.acquired(owner_token)
                persisted_owner, status, incarnation = row
                if status == "complete":
                    return ClaimResult.already_complete()
                if persisted_owner == owner_hash:
                    if incarnation != self.incarnation:
                        if self._incarnation_alive(incarnation):
                            # The original incarnation still holds its lock:
                            # keep it as the claim's owner and record this
                            # incarnation as a co-holder, so the claim stays
                            # live while *either* of them is running.
                            database.execute(
                                "INSERT OR IGNORE INTO "
                                "eventstream_idempotency_holders "
                                "(subscription_hash, event_hash, incarnation) "
                                "VALUES (?, ?, ?)",
                                (subscription_hash, event_hash, self.incarnation),
                            )
                        else:
                            # The prior incarnation is dead (restarted owner):
                            # move the claim's liveness to this live
                            # incarnation with a CAS on the dead incarnation.
                            database.execute(
                                "UPDATE eventstream_idempotency SET incarnation = ? "
                                "WHERE subscription_hash = ? AND event_hash = ? "
                                "AND owner_hash = ? AND status = 'claimed' "
                                "AND incarnation IS ?",
                                (
                                    self.incarnation,
                                    subscription_hash,
                                    event_hash,
                                    owner_hash,
                                    incarnation,
                                ),
                            )
                    return ClaimResult.acquired(owner_token)
                if not self._claim_alive(
                    database, subscription_hash, event_hash, incarnation
                ):
                    # The owning worker process has exited (its incarnation lock
                    # is free): transfer the orphaned claim with a CAS so a
                    # crash after claim() cannot strand the event forever.
                    result = database.execute(
                        "UPDATE eventstream_idempotency "
                        "SET owner_hash = ?, incarnation = ? "
                        "WHERE subscription_hash = ? AND event_hash = ? "
                        "AND owner_hash = ? AND status = 'claimed'",
                        (
                            owner_hash,
                            self.incarnation,
                            subscription_hash,
                            event_hash,
                            persisted_owner,
                        ),
                    )
                    if result.rowcount == 1:
                        self._clear_holders(database, subscription_hash, event_hash)
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
            if result.rowcount == 1:
                self._clear_holders(database, subscription_hash, event_hash)
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
            if result.rowcount == 1:
                self._clear_holders(database, subscription_hash, event_hash)
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
                    "(subscription_hash, event_hash, owner_hash, status, "
                    "incarnation) VALUES (?, ?, ?, 'claimed', ?)",
                    (subscription_hash, event_hash, new_hash, self.incarnation),
                )
                return ClaimResult.acquired(new_owner_token)
            persisted_owner, status = row
            if status == "complete":
                return ClaimResult.already_complete()
            if persisted_owner != stale_hash:
                return ClaimResult.busy()
            result = database.execute(
                "UPDATE eventstream_idempotency SET owner_hash = ?, incarnation = ? "
                "WHERE subscription_hash = ? AND event_hash = ? "
                "AND owner_hash = ? AND status = 'claimed'",
                (new_hash, self.incarnation, subscription_hash, event_hash, stale_hash),
            )
            if result.rowcount == 1:
                self._clear_holders(database, subscription_hash, event_hash)
            return (
                ClaimResult.acquired(new_owner_token)
                if result.rowcount == 1
                else ClaimResult.busy()
            )


__all__ = ["SQLiteIdempotencyStore"]
