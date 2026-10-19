# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Tests for the hashed, scoped, expiring token file and its stores (Phase 63-02)."""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from omega_prime.grokbot.security import (
    AuthMiddleware,
    Principal,
    SecurityConfigError,
    TokenStore,
    hash_token,
    principal_from_request,
)
from omega_prime.grokbot.tokens import (
    CompositeTokenStore,
    FileTokenStore,
    TokenRecord,
    create_token,
    list_tokens,
    main,
    revoke_token,
)
from omega_prime.tooling.fs import atomic_write_json

START = 1_800_000_000.0
DAY = 86400.0


class FakeClock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    return tmp_path / "tokens" / "tokens.json"


def test_create_list_revoke_round_trip(token_file: Path) -> None:
    clock = FakeClock()
    plaintext, record = create_token(
        token_file, scopes=["call", "read"], label="ci", clock=clock
    )
    assert record.id.startswith("tok_")
    assert len(record.id) == len("tok_") + 8
    assert record.sha256 == hash_token(plaintext)
    assert record.scopes == ("read", "call")
    assert record.expires_at is None and record.revoked_at is None

    assert list_tokens(token_file) == [record]

    clock.now += 60
    assert revoke_token(token_file, record.id, clock=clock) is True
    (revoked,) = list_tokens(token_file)
    assert revoked.revoked_at is not None
    first_revoked_at = revoked.revoked_at

    clock.now += 60
    assert revoke_token(token_file, record.id, clock=clock) is True
    assert list_tokens(token_file)[0].revoked_at == first_revoked_at
    assert revoke_token(token_file, "tok_deadbeef") is False


def test_list_missing_file_is_empty(token_file: Path) -> None:
    assert list_tokens(token_file) == []


def test_plaintext_never_in_file_and_mode_is_private(token_file: Path) -> None:
    plaintext, record = create_token(token_file, scopes=["read"])
    raw = token_file.read_bytes()
    assert plaintext.encode() not in raw
    assert record.sha256.encode() in raw
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    document = json.loads(raw)
    assert document["version"] == 1
    # Phase 82 adds rotated_from/rotated_to lineage fields alongside the
    # original seven; plaintext/sha256-only/0600 guarantees are unchanged.
    assert set(document["tokens"][0]) == {
        "id",
        "sha256",
        "scopes",
        "label",
        "created_at",
        "expires_at",
        "revoked_at",
        "rotated_from",
        "rotated_to",
    }


def test_scope_validation(token_file: Path) -> None:
    with pytest.raises(SecurityConfigError):
        create_token(token_file, scopes=["root"])
    with pytest.raises(SecurityConfigError):
        create_token(token_file, scopes=[])
    with pytest.raises(ValueError, match="ttl_days"):
        create_token(token_file, scopes=["read"], ttl_days=0)
    assert not token_file.exists()


def test_create_refuses_to_overwrite_corrupt_file(token_file: Path) -> None:
    token_file.parent.mkdir(parents=True)
    token_file.write_text("{broken")
    with pytest.raises(ValueError, match="valid JSON"):
        create_token(token_file, scopes=["read"])
    assert token_file.read_text() == "{broken"


def test_concurrent_creates_are_not_lost(token_file: Path) -> None:
    count = 12
    barrier = threading.Barrier(count)
    minted: list[str] = []
    guard = threading.Lock()

    def worker() -> None:
        barrier.wait()
        plaintext, _ = create_token(token_file, scopes=["read"])
        with guard:
            minted.append(plaintext)

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    records = list_tokens(token_file)
    assert len(records) == count
    assert len({r.id for r in records}) == count
    assert {r.sha256 for r in records} == {hash_token(p) for p in minted}


def test_store_verifies_scopes_and_principal(token_file: Path) -> None:
    plaintext, record = create_token(token_file, scopes=["read", "call"], label="bot")
    store = FileTokenStore(token_file, reload_interval=0)
    assert store.enabled is True
    assert store.degraded is False and store.degraded_reason is None
    principal = store.verify(plaintext)
    assert principal is not None
    assert principal.id == record.id
    assert principal.scopes == frozenset({"read", "call"})
    assert principal.label == "bot"
    assert store.verify("omk_wrong") is None
    assert store.verify("") is None
    assert store.verify(None) is None
    assert [p.id for p in store.principals()] == [record.id]


def test_store_matches_every_record(token_file: Path) -> None:
    first, rec_a = create_token(token_file, scopes=["read"])
    second, rec_b = create_token(token_file, scopes=["admin"])
    store = FileTokenStore(token_file, reload_interval=0)
    a = store.verify(first)
    b = store.verify(second)
    assert a is not None and a.id == rec_a.id
    assert b is not None and b.id == rec_b.id


def test_expiry_boundary_with_injected_clock(token_file: Path) -> None:
    clock = FakeClock()
    plaintext, record = create_token(
        token_file, scopes=["read"], ttl_days=1, clock=clock
    )
    assert record.expires_at is not None
    store = FileTokenStore(token_file, clock=clock, reload_interval=0)
    clock.now = START + DAY - 1
    assert store.verify(plaintext) is not None
    clock.now = START + DAY
    assert store.verify(plaintext) is None
    assert store.principals() == []
    assert record.status(START + DAY - 1) == "active"
    assert record.status(START + DAY) == "expired"


def test_live_reload_and_revocation_without_restart(token_file: Path) -> None:
    first, record = create_token(token_file, scopes=["read"])
    store = FileTokenStore(token_file, reload_interval=0)
    assert store.verify(first) is not None

    second, _ = create_token(token_file, scopes=["call"])
    assert store.verify(second) is not None

    assert revoke_token(token_file, record.id) is True
    assert store.verify(first) is None
    assert store.verify(second) is not None


def test_reload_picks_up_external_rewrite(token_file: Path) -> None:
    first, _ = create_token(token_file, scopes=["read"])
    store = FileTokenStore(token_file, reload_interval=0)
    assert store.verify(first) is not None

    replacement = TokenRecord(
        id="tok_00000001",
        sha256=hash_token("omk_external_token_value"),
        scopes=("admin",),
        label="",
        created_at="2026-01-01T00:00:00Z",
    )
    atomic_write_json(token_file, {"version": 1, "tokens": [replacement.to_json()]})
    assert store.verify(first) is None
    principal = store.verify("omk_external_token_value")
    assert principal is not None and principal.id == "tok_00000001"


def test_reload_interval_defers_reload(token_file: Path) -> None:
    clock = FakeClock()
    first, _ = create_token(token_file, scopes=["read"], clock=clock)
    store = FileTokenStore(token_file, clock=clock, reload_interval=10.0)
    second, _ = create_token(token_file, scopes=["read"], clock=clock)
    # Within the interval the new token is not visible yet.
    assert store.verify(first) is not None
    assert store.verify(second) is None
    clock.now += 10
    assert store.verify(second) is not None


def test_corrupt_file_denies_everyone_then_recovers(token_file: Path) -> None:
    plaintext, _ = create_token(token_file, scopes=["admin"])
    store = FileTokenStore(token_file, reload_interval=0)
    assert store.verify(plaintext) is not None

    good = token_file.read_bytes()
    atomic_write_json(token_file, "not-an-object-but-secret-marker")
    assert store.verify(plaintext) is None
    assert store.enabled is True
    assert store.degraded is True
    assert store.degraded_reason is not None
    assert "secret-marker" not in store.degraded_reason
    assert store.principals() == []

    token_file.write_text("{ nope", encoding="utf-8")
    assert store.verify(plaintext) is None
    assert store.degraded_reason == "token file is not valid JSON"

    token_file.write_bytes(good)
    assert store.verify(plaintext) is not None
    assert store.degraded is False and store.degraded_reason is None


def test_wrong_version_denies_everyone(token_file: Path) -> None:
    plaintext, record = create_token(token_file, scopes=["read"])
    store = FileTokenStore(token_file, reload_interval=0)
    atomic_write_json(token_file, {"version": 2, "tokens": [record.to_json()]})
    assert store.verify(plaintext) is None
    assert store.degraded is True
    assert store.degraded_reason == "unsupported token file version"


def _pin_mtime(path: Path, mtime_ns: int) -> None:
    os.utime(path, ns=(mtime_ns, mtime_ns))


def test_same_size_atomic_rewrite_inside_one_mtime_tick_reloads(
    token_file: Path,
) -> None:
    """An atomic replace with the same size and mtime must still be noticed."""
    plaintext, record = create_token(token_file, scopes=["read"])
    store = FileTokenStore(token_file, reload_interval=0)
    assert store.verify(plaintext) is not None
    before = token_file.stat()
    atomic_write_json(token_file, {"version": 2, "tokens": [record.to_json()]})
    _pin_mtime(token_file, before.st_mtime_ns)
    after = token_file.stat()
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ino != before.st_ino
    assert store.verify(plaintext) is None
    assert store.degraded is True
    assert store.degraded_reason == "unsupported token file version"


def test_inplace_same_inode_same_size_rewrite_reloads(token_file: Path) -> None:
    """A same-inode rewrite that also keeps mtime and size must still reload."""
    plaintext, record = create_token(token_file, scopes=["read"])
    store = FileTokenStore(token_file, reload_interval=0)
    assert store.verify(plaintext) is not None
    before = token_file.stat()
    original = token_file.read_text(encoding="utf-8")
    rewritten = original.replace('"version": 1', '"version": 2', 1)
    assert rewritten != original
    assert len(rewritten.encode("utf-8")) == len(original.encode("utf-8"))
    with token_file.open("r+", encoding="utf-8") as handle:
        handle.seek(0)
        handle.write(rewritten)
        handle.flush()
        os.fsync(handle.fileno())
    _pin_mtime(token_file, before.st_mtime_ns)
    after = token_file.stat()
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert store.verify(plaintext) is None
    assert store.degraded is True
    assert store.degraded_reason == "unsupported token file version"
    assert record.id


def test_bad_record_denies_everyone(token_file: Path) -> None:
    plaintext, record = create_token(token_file, scopes=["read"])
    bad = record.to_json() | {"id": "tok_bad", "scopes": ["root"]}
    atomic_write_json(token_file, {"version": 1, "tokens": [record.to_json(), bad]})
    store = FileTokenStore(token_file, reload_interval=0)
    assert store.degraded is True
    assert store.verify(plaintext) is None


def test_missing_and_deleted_file_deny_everyone(token_file: Path) -> None:
    missing = FileTokenStore(token_file, reload_interval=0)
    assert missing.enabled is True
    assert missing.degraded is True
    assert missing.verify("omk_anything_at_all_123") is None

    plaintext, _ = create_token(token_file, scopes=["read"])
    assert missing.verify(plaintext) is not None
    assert missing.degraded is False

    token_file.unlink()
    assert missing.verify(plaintext) is None
    assert missing.degraded is True
    assert missing.degraded_reason == "token file not found"


def test_composite_precedence_and_enabled(token_file: Path, tmp_path: Path) -> None:
    shared = "omk_shared_token_value_for_both"
    static_store = TokenStore.from_token(shared, principal_id="static", scopes=["read"])
    file_plain, file_rec = create_token(token_file, scopes=["admin"])
    file_store = FileTokenStore(token_file, reload_interval=0)

    composite = CompositeTokenStore([static_store, file_store])
    assert composite.enabled is True
    principal = composite.verify(shared)
    assert principal is not None and principal.id == "static"
    from_file = composite.verify(file_plain)
    assert from_file is not None and from_file.id == file_rec.id
    assert composite.verify("omk_unknown_token_value") is None
    assert composite.verify(None) is None
    assert {p.id for p in composite.principals()} == {"static", file_rec.id}

    other = tmp_path / "other.json"
    atomic_write_json(other, {"version": 1, "tokens": []})
    other_store = FileTokenStore(other, reload_interval=0)
    assert CompositeTokenStore([other_store]).enabled is True
    assert CompositeTokenStore([]).enabled is False

    # A shared plaintext in two stores resolves to the first store.
    reversed_store = CompositeTokenStore([file_store, static_store])
    only_static = reversed_store.verify(shared)
    assert only_static is not None and only_static.id == "static"


def test_composite_evaluates_every_store() -> None:
    calls: list[str] = []

    class Recording(TokenStore):
        def __init__(self, name: str) -> None:
            super().__init__(())
            self.name = name

        @property
        def enabled(self) -> bool:
            return True

        def verify(self, presented: str | None) -> Principal | None:
            del presented
            calls.append(self.name)
            return None

    composite = CompositeTokenStore(
        [
            TokenStore.from_token("omk_first_store_token_value", principal_id="x"),
            Recording("second"),
            Recording("third"),
        ]
    )
    assert composite.verify("omk_first_store_token_value") is not None
    assert calls == ["second", "third"]


def test_composite_reports_degraded(token_file: Path) -> None:
    store = FileTokenStore(token_file, reload_interval=0)
    composite = CompositeTokenStore([store])
    assert composite.degraded is True
    assert composite.degraded_reason == "token file not found"


# --- CLI -------------------------------------------------------------------


def test_cli_new_list_revoke(
    token_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    file_arg = str(token_file)
    code = main(
        ["new", "--file", file_arg, "--scope", "call", "--label", "ci"], clock=clock
    )
    assert code == 0
    captured = capsys.readouterr()
    plaintext = captured.out.strip()
    assert captured.out.count(plaintext) == 1
    assert len(captured.out.strip().splitlines()) == 1
    assert plaintext not in captured.err
    (record,) = list_tokens(token_file)
    assert record.id in captured.err
    assert record.sha256 == hash_token(plaintext)
    assert record.scopes == ("call",)

    assert main(["list", "--file", file_arg], clock=clock) == 0
    table = capsys.readouterr().out
    assert record.id in table and "ci" in table and "active" in table
    assert record.sha256 not in table and plaintext not in table

    assert main(["list", "--file", file_arg, "--json"], clock=clock) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing[0]["id"] == record.id
    assert listing[0]["status"] == "active"
    assert "sha256" not in listing[0]

    assert main(["revoke", "--file", file_arg, record.id], clock=clock) == 0
    capsys.readouterr()
    assert main(["list", "--file", file_arg], clock=clock) == 0
    assert "revoked" in capsys.readouterr().out
    assert main(["revoke", "--file", file_arg, "tok_unknown0"], clock=clock) == 1


def test_cli_expired_status_and_default_scopes(
    token_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    file_arg = str(token_file)
    assert main(["new", "--file", file_arg, "--ttl-days", "1"], clock=clock) == 0
    capsys.readouterr()
    (record,) = list_tokens(token_file)
    assert record.scopes == ("read", "call")
    clock.now += 2 * DAY
    assert main(["list", "--file", file_arg], clock=clock) == 0
    assert "expired" in capsys.readouterr().out


def test_cli_errors(token_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file_arg = str(token_file)
    assert main(["new", "--file", file_arg, "--scope", "root"]) == 2
    assert "unknown token scope" in capsys.readouterr().err
    assert main(["new", "--file", file_arg, "--ttl-days", "0"]) == 2
    capsys.readouterr()
    assert not token_file.exists()
    assert main([]) == 2
    capsys.readouterr()

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("{broken")
    assert main(["list", "--file", file_arg]) == 1
    assert main(["revoke", "--file", file_arg, "tok_00000000"]) == 1


# --- AuthMiddleware integration --------------------------------------------


def test_auth_middleware_with_file_store(token_file: Path) -> None:
    plaintext, record = create_token(token_file, scopes=["read", "call"], label="it")
    store = FileTokenStore(token_file, reload_interval=0)

    async def whoami(request: Request) -> JSONResponse:
        principal = principal_from_request(request)
        assert principal is not None
        return JSONResponse({"id": principal.id, "scopes": sorted(principal.scopes)})

    app = Starlette(routes=[Route("/whoami", whoami)])
    client = TestClient(AuthMiddleware(app, store=store))
    headers = {"Authorization": f"Bearer {plaintext}"}

    ok = client.get("/whoami", headers=headers)
    assert ok.status_code == 200
    assert ok.json() == {"id": record.id, "scopes": ["call", "read"]}

    assert revoke_token(token_file, record.id) is True
    denied = client.get("/whoami", headers=headers)
    assert denied.status_code == 401
    assert denied.headers["WWW-Authenticate"].startswith("Bearer")

    token_file.write_text("garbage", encoding="utf-8")
    assert client.get("/whoami", headers=headers).status_code == 401
