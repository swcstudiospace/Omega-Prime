# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Scoped, expiring, revocable bearer tokens kept in a hashed JSON file.

File format (`--token-store PATH`)::

    {"version": 1, "tokens": [
        {"id": "tok_1a2b3c4d", "sha256": "<hex>", "scopes": ["read", "call"],
         "label": "ci", "created_at": "2026-01-01T00:00:00Z",
         "expires_at": null, "revoked_at": null}]}

Only the SHA-256 of a token is stored; the plaintext is returned once by
`create_token` and never written anywhere. The file is replaced atomically with mode
0600 under an exclusive `flock` on a sibling `<name>.lock`, so concurrent CLI calls do
not lose each other's updates.

`FileTokenStore` is a `security.TokenStore`, so `AuthMiddleware` uses it unchanged. It
fails closed: a missing, unreadable, corrupt or wrong-version file denies EVERYONE,
sets `degraded` and `degraded_reason` (never file contents), and `enabled` stays True
so a bad file can never mean "no authentication". A later good file clears the state.

CLI: `python -m omega_prime.grokbot.tokens {new,list,revoke,rotate}`.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from omega_prime.grokbot.security import (
    SCOPE_CALL,
    SCOPE_READ,
    SCOPES,
    Principal,
    SecurityConfigError,
    TokenStore,
    generate_token,
    hash_token,
)
from omega_prime.tooling.fs import (
    PRIVATE_FILE_MODE,
    atomic_write_json,
    ensure_private_dir,
)

_fcntl: ModuleType | None
try:
    import fcntl as _fcntl_module
except ImportError:  # pragma: no cover - non-POSIX platforms
    _fcntl = None
else:
    _fcntl = _fcntl_module

FORMAT_VERSION = 1
# mtime_ns, size, inode, sha256. Inode catches an atomic replace inside one
# mtime tick; the hash catches an in-place rewrite that keeps the inode.
_FileSignature = tuple[int, int, int, bytes]
TOKEN_ID_PREFIX = "tok_"
DEFAULT_RELOAD_INTERVAL = 1.0
DEFAULT_CLI_SCOPES = (SCOPE_READ, SCOPE_CALL)
DEFAULT_CLI_OVERLAP_HOURS = 1.0
DEFAULT_SCHEDULED_OVERLAP_HOURS = 24.0

STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_REVOKED = "revoked"

_SECONDS_PER_DAY = 86400
_SHA256_HEX_LENGTH = 64
_HEX_DIGITS = frozenset("0123456789abcdef")
_SCOPE_ORDER = {scope: index for index, scope in enumerate(SCOPES)}

Clock = Callable[[], float]


class TokenFileError(ValueError):
    """The token file is unreadable, corrupt, or of an unsupported version."""


def _format_time(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: str) -> float:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _normalize_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    held = frozenset(scopes)
    if not held:
        raise SecurityConfigError("a token needs at least one scope")
    unknown = sorted(held.difference(SCOPES))
    if unknown:
        raise SecurityConfigError(
            f"unknown token scope(s): {', '.join(unknown)}; "
            f"valid scopes are {', '.join(SCOPES)}"
        )
    return tuple(sorted(held, key=_SCOPE_ORDER.__getitem__))


@dataclass(frozen=True)
class TokenRecord:
    """One stored credential. `sha256` is the digest of the plaintext token."""

    id: str
    sha256: str
    scopes: tuple[str, ...]
    label: str
    created_at: str
    expires_at: str | None = None
    revoked_at: str | None = None
    # Key versioning (Phase 82 rotation). Entry hashes, not token material:
    # `rotated_from` is the predecessor entry's `sha256` digest (or null when
    # this entry was minted by `new`), `rotated_to` the successor's digest
    # (or null while this entry is the lineage head).
    rotated_from: str | None = None
    rotated_to: str | None = None

    def status(self, now: float) -> str:
        """`revoked`, `expired` (when `now >= expires_at`) or `active`."""
        if self.revoked_at is not None:
            return STATUS_REVOKED
        if self.expires_at is not None and now >= _parse_time(self.expires_at):
            return STATUS_EXPIRED
        return STATUS_ACTIVE

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sha256": self.sha256,
            "scopes": list(self.scopes),
            "label": self.label,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "rotated_from": self.rotated_from,
            "rotated_to": self.rotated_to,
        }

    def public_json(self, now: float) -> dict[str, Any]:
        """Listing view: never includes the digest."""
        data = self.to_json()
        del data["sha256"]
        data["status"] = self.status(now)
        return data


def _optional_time(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TokenFileError(f"token record field {key!r} must be a string or null")
    try:
        _parse_time(value)
    except ValueError as exc:
        raise TokenFileError(f"token record field {key!r} is not ISO-8601") from exc
    return value


def _optional_str(raw: dict[str, Any], key: str) -> str | None:
    """An optional string field; absent (pre-plan files) reads as null."""
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TokenFileError(f"token record field {key!r} must be a string or null")
    return value


def _record_from_json(raw: Any) -> TokenRecord:
    if not isinstance(raw, dict):
        raise TokenFileError("token record must be an object")
    token_id = raw.get("id")
    digest = raw.get("sha256")
    scopes = raw.get("scopes")
    label = raw.get("label", "")
    created = raw.get("created_at")
    if not isinstance(token_id, str) or not token_id:
        raise TokenFileError("token record has no id")
    if (
        not isinstance(digest, str)
        or len(digest) != _SHA256_HEX_LENGTH
        or not _HEX_DIGITS.issuperset(digest.lower())
    ):
        raise TokenFileError("token record sha256 is not a SHA-256 hex digest")
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        raise TokenFileError("token record scopes must be a list of strings")
    if not isinstance(label, str):
        raise TokenFileError("token record label must be a string")
    if not isinstance(created, str):
        raise TokenFileError("token record created_at must be a string")
    try:
        normalized = _normalize_scopes(scopes)
    except SecurityConfigError as exc:
        raise TokenFileError("token record has invalid scopes") from exc
    return TokenRecord(
        id=token_id,
        sha256=digest.lower(),
        scopes=normalized,
        label=label,
        created_at=created,
        expires_at=_optional_time(raw, "expires_at"),
        revoked_at=_optional_time(raw, "revoked_at"),
        rotated_from=_optional_str(raw, "rotated_from"),
        rotated_to=_optional_str(raw, "rotated_to"),
    )


def _parse_file(data: bytes) -> list[TokenRecord]:
    try:
        document = json.loads(data)
    except ValueError as exc:
        raise TokenFileError("token file is not valid JSON") from exc
    if not isinstance(document, dict):
        raise TokenFileError("token file must contain a JSON object")
    if document.get("version") != FORMAT_VERSION:
        raise TokenFileError("unsupported token file version")
    tokens = document.get("tokens")
    if not isinstance(tokens, list):
        raise TokenFileError("token file 'tokens' must be a list")
    return [_record_from_json(item) for item in tokens]


def _read_records(path: Path) -> list[TokenRecord]:
    """Records in `path`; a missing file is an empty store, a bad one raises."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise TokenFileError(f"token file unreadable ({type(exc).__name__})") from exc
    return _parse_file(data)


def _write_records(path: Path, records: Sequence[TokenRecord]) -> None:
    atomic_write_json(
        path,
        {
            "version": FORMAT_VERSION,
            "tokens": [record.to_json() for record in records],
        },
        mode=PRIVATE_FILE_MODE,
    )


@contextlib.contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive `flock` on `<path>.lock` for a read-modify-write cycle."""
    ensure_private_dir(path.parent)
    lock_path = path.with_name(f"{path.name}.lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, PRIVATE_FILE_MODE)
    try:
        if _fcntl is not None:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def create_token(
    path: Path | str,
    *,
    scopes: Iterable[str],
    label: str = "",
    ttl_days: float | None = None,
    clock: Clock = time.time,
) -> tuple[str, TokenRecord]:
    """Mint a token; returns `(plaintext, record)`. The plaintext is not stored."""
    target = Path(path)
    held = _normalize_scopes(scopes)
    if ttl_days is not None and ttl_days <= 0:
        raise ValueError("ttl_days must be positive")
    plaintext = generate_token()
    now = clock()
    with _exclusive_lock(target):
        records = _read_records(target)
        used = {record.id for record in records}
        token_id = f"{TOKEN_ID_PREFIX}{secrets.token_hex(4)}"
        while token_id in used:
            token_id = f"{TOKEN_ID_PREFIX}{secrets.token_hex(4)}"
        record = TokenRecord(
            id=token_id,
            sha256=hash_token(plaintext),
            scopes=held,
            label=label,
            created_at=_format_time(now),
            expires_at=(
                None
                if ttl_days is None
                else _format_time(now + ttl_days * _SECONDS_PER_DAY)
            ),
        )
        _write_records(target, [*records, record])
    return plaintext, record


def list_tokens(path: Path | str) -> list[TokenRecord]:
    """All stored records (digests only); a missing file lists nothing."""
    return _read_records(Path(path))


def revoke_token(path: Path | str, token_id: str, *, clock: Clock = time.time) -> bool:
    """Revoke `token_id`. Idempotent; False when the id is unknown."""
    target = Path(path)
    with _exclusive_lock(target):
        records = _read_records(target)
        updated: list[TokenRecord] = []
        found = False
        changed = False
        for record in records:
            if record.id == token_id:
                found = True
                if record.revoked_at is None:
                    record = TokenRecord(
                        id=record.id,
                        sha256=record.sha256,
                        scopes=record.scopes,
                        label=record.label,
                        created_at=record.created_at,
                        expires_at=record.expires_at,
                        revoked_at=_format_time(clock()),
                        rotated_from=record.rotated_from,
                        rotated_to=record.rotated_to,
                    )
                    changed = True
            updated.append(record)
        if changed:
            _write_records(target, updated)
    return found


_SECONDS_PER_HOUR = 3600


def label_hash(label: str) -> str:
    """SHA-256 hex of a token label (the only label form that leaves the process)."""
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def default_audit_file_for(store_path: Path | str) -> Path:
    """Where `rotate` writes its audit event when `--audit-file` is absent."""
    return Path(store_path).parent / "grokbot_audit.jsonl"


def _append_rotation_audit(
    audit_path: Path | str | None,
    *,
    old: TokenRecord,
    new: TokenRecord,
    overlap_hours: float,
) -> None:
    """Append one hash-only `token_rotated` event; never raises.

    A failing or read-only audit log must never block the rotation itself, so
    every failure degrades to one stderr line. The plaintext token never
    crosses this call — only digests and the label hash.
    """
    if audit_path is None:
        return
    try:
        from omega_prime.grokbot.audit import GrokBotAuditTracer

        tracer = GrokBotAuditTracer(audit_path)
        tracer.log_event(
            "token_rotated",
            caller="tokens-cli",
            status="ok",
            details={
                "label_hash": label_hash(old.label),
                "old_entry_hash": old.sha256,
                "new_entry_hash": new.sha256,
                "overlap_hours": overlap_hours,
            },
        )
    except Exception as exc:
        print(
            f"warning: rotation audit write failed ({exc}); continuing", file=sys.stderr
        )


def _select_rotation_target(
    records: Sequence[TokenRecord],
    *,
    label: str | None,
    token_id: str | None,
) -> TokenRecord:
    """The entry a manual `rotate` replaces (raises `TokenFileError` when none)."""
    if token_id is not None:
        for record in records:
            if record.id == token_id:
                return record
        raise TokenFileError(f"unknown token id {token_id}")
    assert label is not None
    matching = [record for record in records if record.label == label]
    if not matching:
        raise TokenFileError(f"no token with label {label!r}")
    # Prefer lineage heads (never superseded); newest wins. A fully-superseded
    # label falls back to its newest entry so an explicit rotate still works.
    heads = [record for record in matching if record.rotated_to is None]
    pool = heads or matching
    return max(pool, key=lambda record: (record.created_at, record.id))


def _mint_replacement(
    records: Sequence[TokenRecord],
    old: TokenRecord,
    now: float,
    overlap_hours: float,
) -> tuple[str, TokenRecord, TokenRecord, list[TokenRecord]]:
    """Mint the successor for `old`; pure record surgery, no I/O or locking.

    Returns `(plaintext, stamped_old, new, updated)`. The caller MUST hold the
    store lock across select → mint → write so a concurrent run cannot mint a
    second successor for the same entry.
    """
    horizon = now + overlap_hours * _SECONDS_PER_HOUR
    plaintext = generate_token()
    used = {record.id for record in records}
    new_id = f"{TOKEN_ID_PREFIX}{secrets.token_hex(4)}"
    while new_id in used:
        new_id = f"{TOKEN_ID_PREFIX}{secrets.token_hex(4)}"
    new = TokenRecord(
        id=new_id,
        sha256=hash_token(plaintext),
        scopes=old.scopes,
        label=old.label,
        created_at=_format_time(now),
        expires_at=None,
        revoked_at=None,
        rotated_from=old.sha256,
    )
    old_expires = None if old.expires_at is None else _parse_time(old.expires_at)
    clamped = _format_time(horizon)
    # The successor link is unconditional (first successor wins) so every
    # rotation stays reconstructable and the old entry never stays due;
    # the horizon only ever shortens expiry, never extends it.
    stamped_old = TokenRecord(
        id=old.id,
        sha256=old.sha256,
        scopes=old.scopes,
        label=old.label,
        created_at=old.created_at,
        expires_at=(
            clamped if old_expires is None or old_expires > horizon else old.expires_at
        ),
        revoked_at=old.revoked_at,
        rotated_from=old.rotated_from,
        rotated_to=old.rotated_to if old.rotated_to is not None else new.sha256,
    )
    updated = [stamped_old if record.id == old.id else record for record in records]
    updated.append(new)
    return plaintext, stamped_old, new, updated


def _emit_rotation_effects(
    store_path: Path,
    audit_file: Any,
    *,
    old: TokenRecord,
    new: TokenRecord,
    overlap_hours: float,
    metrics: Any | None,
) -> None:
    """Audit + metrics side effects of one rotation; runs outside the lock."""
    resolved = default_audit_file_for(store_path) if audit_file is ... else audit_file
    _append_rotation_audit(resolved, old=old, new=new, overlap_hours=overlap_hours)
    if metrics is not None:
        try:
            metrics.record_rotation()
        except Exception as exc:  # never block rotation on metrics
            print(
                f"warning: token metrics record_rotation failed: {exc}", file=sys.stderr
            )


def rotate_token(
    path: Path | str,
    *,
    label: str | None = None,
    token_id: str | None = None,
    overlap_hours: float = DEFAULT_CLI_OVERLAP_HOURS,
    clock: Clock = time.time,
    audit_file: Any = ...,
    metrics: Any | None = None,
) -> tuple[str, TokenRecord, TokenRecord]:
    """Mint a replacement for one entry, linked by `rotated_from`/`rotated_to`.

    Returns `(plaintext, old_record, new_record)`. The plaintext is printed by
    the caller exactly once and never stored or logged. The old entry keeps
    authenticating until `mint_time + overlap_hours` — implemented by clamping
    its `expires_at` down to that horizon on the same expiry path `--ttl-days`
    uses — then reads as expired. Revocation bypasses the window entirely
    (use `revoke_token` for break-glass). `audit_file=None` disables the audit
    event; the default (`...`) resolves to the store's sibling chain file.
    `metrics` is an optional `TokenMetrics`-shaped handle (duck-typed:
    `.record_rotation()`); each successful rotation records exactly once.
    A metrics failure never blocks rotation — it warns on stderr.
    """
    if (label is None) == (token_id is None):
        raise ValueError("rotate needs exactly one of label= or token_id=")
    if not (overlap_hours > 0 and overlap_hours != float("inf")):
        raise ValueError("overlap_hours must be a positive finite number")
    target = Path(path)
    now = clock()
    with _exclusive_lock(target):
        records = _read_records(target)
        old = _select_rotation_target(records, label=label, token_id=token_id)
        plaintext, stamped_old, new, updated = _mint_replacement(
            records, old, now, overlap_hours
        )
        _write_records(target, updated)
    _emit_rotation_effects(
        target,
        audit_file,
        old=stamped_old,
        new=new,
        overlap_hours=overlap_hours,
        metrics=metrics,
    )
    return plaintext, stamped_old, new


def due_for_rotation(
    records: Sequence[TokenRecord], now: float, overlap_hours: float
) -> list[TokenRecord]:
    """Entries the scheduled job should replace: expiring within the window.

    An entry is due when it is unrevoked, never superseded (`rotated_to` is
    null), carries an `expires_at`, and `expires_at - now <= overlap_hours`.
    Entries without expiry never come due; already-expired entries count as
    due (negative remainder) so a stalled scheduler self-heals once per label.
    """
    window = overlap_hours * _SECONDS_PER_HOUR
    due: list[TokenRecord] = []
    for record in records:
        if record.revoked_at is not None or record.rotated_to is not None:
            continue
        if record.expires_at is None:
            continue
        if _parse_time(record.expires_at) - now <= window:
            due.append(record)
    return due


def rotate_due_tokens(
    path: Path | str,
    *,
    overlap_hours: float,
    clock: Clock = time.time,
    audit_file: Any = ...,
    metrics: Any | None = None,
) -> list[tuple[str, TokenRecord, TokenRecord]]:
    """Rotate every due entry once; a second run mints nothing (fresh entries
    carry no expiry, superseded ones are excluded from `due_for_rotation`).
    The due recheck and the mint share ONE lock per entry, so two concurrent
    scheduled runs cannot both mint: the loser re-reads a superseded entry
    and skips it. `metrics`, when given, is forwarded to each rotation so
    every successful rotation records exactly once."""
    target = Path(path)
    now = clock()
    with _exclusive_lock(target):
        due_ids = {
            record.id
            for record in due_for_rotation(_read_records(target), now, overlap_hours)
        }
    rotated: list[tuple[str, TokenRecord, TokenRecord]] = []
    for token_id in sorted(due_ids):
        # Recheck AND rotate under one lock; a concurrent run that wins the
        # race leaves this entry superseded, so the loser skips it here
        # instead of minting a second successor.
        with _exclusive_lock(target):
            current = _read_records(target)
            candidates = {record.id: record for record in current}
            candidate = candidates.get(token_id)
            if (
                candidate is None
                or candidate.revoked_at is not None
                or candidate.rotated_to is not None
            ):
                continue
            if token_id not in {
                record.id for record in due_for_rotation(current, now, overlap_hours)
            }:
                continue
            plaintext, stamped_old, new, updated = _mint_replacement(
                current, candidate, now, overlap_hours
            )
            _write_records(target, updated)
        _emit_rotation_effects(
            target,
            audit_file,
            old=stamped_old,
            new=new,
            overlap_hours=overlap_hours,
            metrics=metrics,
        )
        rotated.append((plaintext, stamped_old, new))
    return rotated


@dataclass(frozen=True)
class _Entry:
    principal: Principal
    digest: bytes
    expires_at: float | None
    revoked: bool

    def valid(self, now: float) -> bool:
        if self.revoked:
            return False
        return self.expires_at is None or now < self.expires_at


def _entry_from_record(record: TokenRecord) -> _Entry:
    expires = None if record.expires_at is None else _parse_time(record.expires_at)
    return _Entry(
        principal=Principal(record.id, frozenset(record.scopes), record.label),
        digest=record.sha256.encode("ascii"),
        expires_at=expires,
        revoked=record.revoked_at is not None,
    )


def _file_signature(info: os.stat_result, data: bytes) -> _FileSignature:
    return (
        int(info.st_mtime_ns),
        int(info.st_size),
        int(info.st_ino),
        hashlib.sha256(data).digest(),
    )


class FileTokenStore(TokenStore):
    """A `TokenStore` backed by the hashed token file; live-reloading, fail closed."""

    def __init__(
        self,
        path: Path | str,
        *,
        clock: Clock = time.time,
        reload_interval: float = DEFAULT_RELOAD_INTERVAL,
    ) -> None:
        super().__init__(())
        self.path = Path(path)
        self._clock = clock
        self._reload_interval = reload_interval
        self._lock = threading.Lock()
        self._file_entries: tuple[_Entry, ...] = ()
        self._signature: _FileSignature | None = None
        self._last_check = 0.0
        self._degraded_reason: str | None = None
        with self._lock:
            self._reload()
            self._last_check = self._clock()

    @property
    def enabled(self) -> bool:
        return True

    @property
    def degraded(self) -> bool:
        with self._lock:
            return self._degraded_reason is not None

    @property
    def degraded_reason(self) -> str | None:
        with self._lock:
            return self._degraded_reason

    def _reload(self) -> None:
        """Re-read the file; caller holds `self._lock`."""
        try:
            with self.path.open("rb") as handle:
                info = os.fstat(handle.fileno())
                data = handle.read()
        except FileNotFoundError:
            self._fail("token file not found", None)
            return
        except OSError as exc:
            self._fail(f"token file unreadable ({type(exc).__name__})", None)
            return
        signature = _file_signature(info, data)
        try:
            records = _parse_file(data)
        except TokenFileError as exc:
            self._fail(str(exc), signature)
            return
        self._file_entries = tuple(_entry_from_record(record) for record in records)
        self._signature = signature
        self._degraded_reason = None

    def _fail(self, reason: str, signature: _FileSignature | None) -> None:
        self._file_entries = ()
        self._signature = signature
        self._degraded_reason = reason

    def _refresh(self) -> tuple[_Entry, ...]:
        with self._lock:
            now = self._clock()
            elapsed = now - self._last_check
            if elapsed >= self._reload_interval or elapsed < 0:
                self._last_check = now
                if not self._stat_matches():
                    self._reload()
            return self._file_entries

    def _stat_matches(self) -> bool:
        """False when the file changed, including a same-size rewrite in one mtime tick.

        ``(st_mtime_ns, st_size)`` misses an atomic replace that lands inside
        the filesystem's mtime resolution. The inode catches that replace.
        A content hash catches an in-place rewrite that keeps the inode too.
        """
        try:
            info = self.path.stat()
        except OSError:
            if self._degraded_reason != "token file not found":
                return False
            return self._signature is None
        if self._signature is None:
            return False
        identity = (int(info.st_mtime_ns), int(info.st_size), int(info.st_ino))
        if identity != self._signature[:3]:
            return False
        try:
            digest = hashlib.sha256(self.path.read_bytes()).digest()
        except OSError:
            return False
        return digest == self._signature[3]

    def verify(self, presented: str | None) -> Principal | None:
        """The principal for a valid, unexpired, unrevoked token; else None.

        Every record is compared with `hmac.compare_digest` (no early exit).
        """
        if not presented:
            return None
        entries = self._refresh()
        digest = hash_token(presented).encode("ascii")
        now = self._clock()
        match: Principal | None = None
        for entry in entries:
            equal = hmac.compare_digest(digest, entry.digest)
            if equal and match is None and entry.valid(now):
                match = entry.principal
        return match

    def principals(self) -> list[Principal]:
        entries = self._refresh()
        now = self._clock()
        return [entry.principal for entry in entries if entry.valid(now)]


class CompositeTokenStore(TokenStore):
    """Several stores behind one: the first match wins, every store is evaluated."""

    def __init__(self, stores: Sequence[TokenStore]) -> None:
        super().__init__(())
        self._stores = tuple(stores)

    @property
    def enabled(self) -> bool:
        return any(store.enabled for store in self._stores)

    @property
    def degraded(self) -> bool:
        return any(getattr(store, "degraded", False) for store in self._stores)

    @property
    def degraded_reason(self) -> str | None:
        for store in self._stores:
            reason = getattr(store, "degraded_reason", None)
            if reason:
                return str(reason)
        return None

    def verify(self, presented: str | None) -> Principal | None:
        match: Principal | None = None
        for store in self._stores:
            principal = store.verify(presented)
            if match is None:
                match = principal
        return match

    def principals(self) -> list[Principal]:
        return [p for store in self._stores for p in store.principals()]


def non_file_principals(store: TokenStore) -> list[Principal]:
    """Principals behind `store` with no file backing (single bearer/env token).

    Walks composite children; file-backed stores (anything with a `path`, e.g.
    `FileTokenStore`) are skipped because the record-based snapshot already
    counts them. Used by the serve-time active-token snapshot so a combined
    bearer-token + file store still counts the bearer caller.
    """
    found: list[Principal] = []
    seen: set[int] = set()
    stack: list[Any] = [store]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        children = getattr(current, "_stores", ())
        if children:
            stack.extend(children)
        elif getattr(current, "path", None) is None:
            found.extend(current.principals())
    return found


def observe_active_with_bearer(
    metrics_handle: Any,
    store: TokenStore,
    records: Sequence[TokenRecord],
    now: float,
) -> None:
    """Sync the active-token gauge from file `records` plus bearer principals.

    `metrics_handle` is duck-typed (`.observe(records, now)`, `.set_active()`).
    File records go through `observe` unchanged; each non-file-backed
    principal (label hash of its known label) is added on top so combined
    deployments count every caller exactly once.
    """
    metrics_handle.observe(records, now)
    extra = non_file_principals(store)
    if not extra:
        return
    live: dict[str, int] = {}
    for record in records:
        if record.status(now) == STATUS_ACTIVE:
            digest = label_hash(record.label)
            live[digest] = live.get(digest, 0) + 1
    counts: dict[str, int] = {}
    for principal in extra:
        digest = label_hash(principal.label or "")
        counts[digest] = counts.get(digest, 0) + 1
    for digest, count in counts.items():
        metrics_handle.set_active(digest, live.get(digest, 0) + count)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m omega_prime.grokbot.tokens",
        description="Manage the hashed Grok Bot token file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="mint a token (plaintext is printed once)")
    new.add_argument("--file", required=True, help="token file path")
    new.add_argument(
        "--scope",
        action="append",
        dest="scopes",
        help=f"scope to grant ({', '.join(SCOPES)}); repeatable; "
        f"default: {', '.join(DEFAULT_CLI_SCOPES)}",
    )
    new.add_argument("--label", default="", help="free-form label")
    new.add_argument("--ttl-days", type=float, default=None, help="expire after N days")
    listing = sub.add_parser("list", help="list tokens (never shows hashes)")
    listing.add_argument("--file", required=True, help="token file path")
    listing.add_argument("--json", action="store_true", help="machine-readable output")
    revoke = sub.add_parser("revoke", help="revoke a token by id")
    revoke.add_argument("--file", required=True, help="token file path")
    revoke.add_argument("token_id", metavar="ID", help="token id, e.g. tok_1a2b3c4d")
    rotate = sub.add_parser(
        "rotate",
        help="mint a replacement; the old entry stays live for the overlap window",
    )
    rotate.add_argument("--file", required=True, help="token file path")
    target = rotate.add_mutually_exclusive_group()
    target.add_argument(
        "--label", default=None, help="rotate the head entry with this label"
    )
    target.add_argument("--id", default=None, help="rotate the entry with this id")
    rotate.add_argument(
        "--overlap-hours",
        type=float,
        default=None,
        help="overlap window in hours (default: 1 for manual rotate, "
        "rotation.overlap_hours for --due-only)",
    )
    rotate.add_argument(
        "--due-only",
        action="store_true",
        help="rotate only entries expiring within the window; "
        "takes no --label/--id and honors rotation.enabled",
    )
    rotate.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would rotate without mutating the store",
    )
    rotate.add_argument(
        "--audit-file",
        default=None,
        help="audit chain file (default: <store-dir>/grokbot_audit.jsonl)",
    )
    return parser


def _cmd_new(args: argparse.Namespace, clock: Clock) -> int:
    scopes = args.scopes or list(DEFAULT_CLI_SCOPES)
    if args.ttl_days is not None and args.ttl_days <= 0:
        print("error: --ttl-days must be positive", file=sys.stderr)
        return 2
    try:
        plaintext, record = create_token(
            args.file,
            scopes=scopes,
            label=args.label,
            ttl_days=args.ttl_days,
            clock=clock,
        )
    except SecurityConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (TokenFileError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(plaintext)
    print(f"token id: {record.id}", file=sys.stderr)
    return 0


def _cmd_list(args: argparse.Namespace, clock: Clock) -> int:
    try:
        records = list_tokens(args.file)
    except (TokenFileError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    now = clock()
    if args.json:
        print(json.dumps([r.public_json(now) for r in records], indent=2))
        return 0
    header = ("ID", "SCOPES", "LABEL", "CREATED", "EXPIRES", "STATUS")
    rows = [
        (
            r.id,
            ",".join(r.scopes),
            r.label,
            r.created_at,
            r.expires_at or "never",
            r.status(now),
        )
        for r in records
    ]
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return 0


def _cmd_revoke(args: argparse.Namespace, clock: Clock) -> int:
    try:
        found = revoke_token(args.file, args.token_id, clock=clock)
    except (TokenFileError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not found:
        print(f"error: unknown token id {args.token_id}", file=sys.stderr)
        return 1
    print(f"revoked {args.token_id}", file=sys.stderr)
    return 0


def _rotation_config() -> tuple[bool, float]:
    """`(enabled, overlap_hours)` from config; bad values fall back loudly."""
    try:
        from omega_prime.config import load_config

        config = load_config()
    except Exception as exc:
        print(
            f"warning: rotation config unreadable ({exc}); scheduled rotation off",
            file=sys.stderr,
        )
        return False, DEFAULT_SCHEDULED_OVERLAP_HOURS
    node = config.get("rotation", {})
    if not isinstance(node, dict):
        node = {}
    enabled = node.get("enabled") is True
    overlap = node.get("overlap_hours", DEFAULT_SCHEDULED_OVERLAP_HOURS)
    if (
        isinstance(overlap, bool)
        or not isinstance(overlap, (int, float))
        or not (overlap > 0 and overlap != float("inf"))
    ):
        print(
            "warning: rotation.overlap_hours invalid; using "
            f"{DEFAULT_SCHEDULED_OVERLAP_HOURS}",
            file=sys.stderr,
        )
        overlap = DEFAULT_SCHEDULED_OVERLAP_HOURS
    return enabled, float(overlap)


def _cmd_rotate(args: argparse.Namespace, clock: Clock) -> int:
    # Stdout carries fresh token plaintext only (empty when nothing rotated);
    # every status line goes to stderr so wrappers can capture stdout safely.
    if args.overlap_hours is not None and not (
        args.overlap_hours > 0 and args.overlap_hours != float("inf")
    ):
        print(
            "error: --overlap-hours must be a positive finite number", file=sys.stderr
        )
        return 2
    if args.due_only and (args.label is not None or args.id is not None):
        print("error: --due-only takes no --label/--id", file=sys.stderr)
        return 2
    if not args.due_only and args.label is None and args.id is None:
        print("error: rotate needs --label, --id, or --due-only", file=sys.stderr)
        return 2
    # `--audit-file` unset means the sibling chain file (the `rotate_token`
    # sentinel default), NOT disabled auditing: only pass it when explicit.
    audit_kwargs: dict[str, Any] = {}
    if args.audit_file is not None:
        audit_kwargs["audit_file"] = args.audit_file

    if args.due_only:
        enabled, configured = _rotation_config()
        if not enabled:
            print(
                "rotation disabled by configuration (rotation.enabled is false); "
                "skipping",
                file=sys.stderr,
            )
            return 0
        overlap = args.overlap_hours if args.overlap_hours is not None else configured
        try:
            records = list_tokens(args.file)
        except (TokenFileError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        now = clock()
        due = due_for_rotation(records, now, overlap)
        if args.dry_run:
            if not due:
                print("no tokens due for rotation", file=sys.stderr)
            for record in due:
                remaining = (
                    _parse_time(record.expires_at) - now if record.expires_at else 0
                )
                print(
                    f"due {record.id} label={record.label!r} "
                    f"remaining_seconds={max(0, int(remaining))}",
                    file=sys.stderr,
                )
            return 0
        if not due:
            print("no tokens due for rotation", file=sys.stderr)
            return 0
        try:
            rotated = rotate_due_tokens(
                args.file,
                overlap_hours=overlap,
                clock=clock,
                **audit_kwargs,
            )
        except (TokenFileError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        for plaintext, old, new in rotated:
            print(plaintext)
            print(f"rotated {old.id} -> {new.id}", file=sys.stderr)
        return 0
    overlap = (
        args.overlap_hours
        if args.overlap_hours is not None
        else DEFAULT_CLI_OVERLAP_HOURS
    )
    if args.dry_run:
        try:
            target = _select_rotation_target(
                list_tokens(args.file), label=args.label, token_id=args.id
            )
        except (TokenFileError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(
            f"would rotate {target.id} label={target.label!r} overlap_hours={overlap}",
            file=sys.stderr,
        )
        return 0
    try:
        plaintext, old, new = rotate_token(
            args.file,
            label=args.label,
            token_id=args.id,
            overlap_hours=overlap,
            clock=clock,
            **audit_kwargs,
        )
    except (TokenFileError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(plaintext)
    print(f"rotated {old.id} -> {new.id}", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None, *, clock: Clock = time.time) -> int:
    """CLI entry point; returns the process exit code."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    if args.command == "new":
        return _cmd_new(args, clock)
    if args.command == "list":
        return _cmd_list(args, clock)
    if args.command == "rotate":
        return _cmd_rotate(args, clock)
    return _cmd_revoke(args, clock)


__all__ = [
    "DEFAULT_CLI_OVERLAP_HOURS",
    "DEFAULT_SCHEDULED_OVERLAP_HOURS",
    "FORMAT_VERSION",
    "STATUS_ACTIVE",
    "STATUS_EXPIRED",
    "STATUS_REVOKED",
    "CompositeTokenStore",
    "FileTokenStore",
    "TokenFileError",
    "TokenRecord",
    "create_token",
    "default_audit_file_for",
    "due_for_rotation",
    "label_hash",
    "list_tokens",
    "main",
    "non_file_principals",
    "observe_active_with_bearer",
    "revoke_token",
    "rotate_due_tokens",
    "rotate_token",
]

if __name__ == "__main__":
    raise SystemExit(main())
