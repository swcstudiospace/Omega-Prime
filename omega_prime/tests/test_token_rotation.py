# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Tests for host-token rotation with overlap windows (Phase 82-01)."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from omega_prime.grokbot.audit import GENESIS_HASH, GrokBotAuditTracer
from omega_prime.grokbot.metrics import (
    MetricsRegistry,
    register_token_metrics,
    token_label_hash,
)
from omega_prime.grokbot.security import TokenStore
from omega_prime.grokbot.tokens import (
    CompositeTokenStore,
    FileTokenStore,
    TokenFileError,
    TokenRecord,
    create_token,
    default_audit_file_for,
    due_for_rotation,
    list_tokens,
    main,
    non_file_principals,
    observe_active_with_bearer,
    revoke_token,
    rotate_due_tokens,
    rotate_token,
)

START = 1_800_000_000.0
HOUR = 3600.0


class FakeClock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _store(tmp_path: Path) -> Path:
    return tmp_path / "tokens.json"


def _audit(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


def _no_rotation_env(monkeypatch) -> None:
    monkeypatch.delenv("OMEGA_PRIME_ROTATION_ENABLED", raising=False)
    monkeypatch.delenv("OMEGA_PRIME_ROTATION_OVERLAP_HOURS", raising=False)


# --- Task 1: versioning + rotate ------------------------------------------------


def test_rotation_links_lineage_across_two_rotations(tmp_path: Path) -> None:
    path = _store(tmp_path)
    clock = FakeClock()
    _, first = create_token(path, scopes=["read", "call"], label="ci", clock=clock)
    assert first.rotated_from is None and first.rotated_to is None

    _, old1, new1 = rotate_token(
        path, label="ci", overlap_hours=1, clock=clock, audit_file=None
    )
    assert new1.rotated_from == old1.sha256
    assert old1.rotated_to == new1.sha256
    assert new1.scopes == old1.scopes and new1.label == "ci"

    clock.now += 10
    _, old2, new2 = rotate_token(
        path, label="ci", overlap_hours=1, clock=clock, audit_file=None
    )
    # The head (newest unsuperseded entry) is what rotates, not the stale head.
    assert old2.id == new1.id
    assert new2.rotated_from == new1.sha256
    assert old2.rotated_to == new2.sha256

    by_id = {record.id: record for record in list_tokens(path)}
    assert by_id[first.id].rotated_to == new1.sha256
    assert by_id[new1.id].rotated_from == first.sha256
    assert by_id[new1.id].rotated_to == new2.sha256
    assert by_id[new2.id].rotated_from == new1.sha256
    assert by_id[new2.id].rotated_to is None


def test_both_tokens_live_inside_window_only_new_after(tmp_path: Path) -> None:
    path = _store(tmp_path)
    clock = FakeClock()
    old_plain, _ = create_token(path, scopes=["read"], label="ci", clock=clock)
    new_plain, _, _ = rotate_token(
        path, label="ci", overlap_hours=1, clock=clock, audit_file=None
    )
    store = FileTokenStore(path, clock=clock, reload_interval=0)
    assert store.verify(old_plain) is not None
    assert store.verify(new_plain) is not None

    clock.now += HOUR + 1  # past mint_time + overlap_hours
    assert store.verify(old_plain) is None
    assert store.verify(new_plain) is not None
    assert [p.label for p in store.principals()] == ["ci"]


def test_revoke_during_window_kills_old_immediately(tmp_path: Path) -> None:
    path = _store(tmp_path)
    clock = FakeClock()
    old_plain, old = create_token(path, scopes=["read"], label="ci", clock=clock)
    new_plain, _, _ = rotate_token(
        path, label="ci", overlap_hours=24, clock=clock, audit_file=None
    )
    assert revoke_token(path, old.id, clock=clock) is True
    store = FileTokenStore(path, clock=clock, reload_interval=0)
    assert store.verify(old_plain) is None  # window bypassed: revoke is immediate
    assert store.verify(new_plain) is not None


def test_pre_plan_store_files_load_with_null_lineage(tmp_path: Path) -> None:
    path = _store(tmp_path)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "tokens": [
                    {
                        "id": "tok_11111111",
                        "sha256": hashlib.sha256(b"x").hexdigest(),
                        "scopes": ["read"],
                        "label": "legacy",
                        "created_at": "2026-01-01T00:00:00Z",
                        "expires_at": None,
                        "revoked_at": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (record,) = list_tokens(path)
    assert record.rotated_from is None and record.rotated_to is None


def test_due_only_dry_run_reports_without_mutation(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setenv("OMEGA_PRIME_ROTATION_ENABLED", "1")
    monkeypatch.delenv("OMEGA_PRIME_ROTATION_OVERLAP_HOURS", raising=False)
    path = _store(tmp_path)
    clock = FakeClock()
    create_token(
        path, scopes=["read"], label="soon", ttl_days=0.5 / 24, clock=clock
    )  # 30 min
    create_token(path, scopes=["read"], label="fresh", clock=clock)  # no expiry
    before = path.read_bytes()
    records = list_tokens(path)
    due = due_for_rotation(records, clock(), overlap_hours=1)
    assert [r.label for r in due] == ["soon"]
    assert (
        main(
            [
                "rotate",
                "--file",
                str(path),
                "--due-only",
                "--dry-run",
                "--overlap-hours",
                "1",
            ],
            clock=clock,
        )
        == 0
    )
    out, err = capsys.readouterr()
    assert out == ""  # dry runs never print token material
    assert "soon" in err and "fresh" not in err
    assert path.read_bytes() == before


def test_hand_corrupted_store_fails_closed(tmp_path: Path) -> None:
    path = _store(tmp_path)
    clock = FakeClock()
    create_token(path, scopes=["read"], label="ci", clock=clock)
    path.write_bytes(b"{not valid json")
    store = FileTokenStore(path, clock=clock, reload_interval=0)
    assert store.enabled is True
    assert store.degraded is True
    assert store.degraded_reason is not None
    assert store.verify("anything") is None
    assert store.principals() == []
    try:
        rotate_token(path, label="ci", clock=clock, audit_file=None)
    except TokenFileError:
        pass
    else:  # pragma: no cover - must fail closed, never mint on a bad file
        raise AssertionError("rotate on a corrupt store must raise")


def test_rotate_cli_prints_plaintext_once_and_links_json(
    tmp_path: Path, capsys
) -> None:
    path = _store(tmp_path)
    clock = FakeClock()
    assert main(["new", "--file", str(path), "--label", "ci"], clock=clock) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "rotate",
                "--file",
                str(path),
                "--label",
                "ci",
                "--audit-file",
                str(_audit(tmp_path)),
            ],
            clock=clock,
        )
        == 0
    )
    out, err = capsys.readouterr()
    lines = out.split()
    assert len(lines) == 1 and lines[0].startswith("omk_")
    assert "rotated" in err
    assert lines[0] not in path.read_text(encoding="utf-8")  # never stored
    (old, new) = sorted(list_tokens(path), key=lambda r: r.created_at)
    assert old.rotated_to == new.sha256 and new.rotated_from == old.sha256


def test_due_only_self_heals_stalled_scheduler(tmp_path: Path, capsys) -> None:
    path = _store(tmp_path)
    clock = FakeClock()
    create_token(path, scopes=["read"], label="svc", ttl_days=1, clock=clock)
    clock.now += 2 * 86400.0  # scheduler was down past expiry: still due
    (record,) = list_tokens(path)
    assert due_for_rotation([record], clock(), overlap_hours=24)
    rotated_plain, old, new = rotate_token(
        path, token_id=record.id, overlap_hours=24, clock=clock, audit_file=None
    )
    assert rotated_plain.startswith("omk_")
    assert old.rotated_to == new.sha256
    # Second pass mints nothing: the old entry is superseded, the new one
    # carries no expiry.
    assert due_for_rotation(list_tokens(path), clock(), overlap_hours=24) == []


# --- Task 2: config gate + audit chain ------------------------------------------


def test_rotation_config_defaults_and_env_overrides(monkeypatch) -> None:
    from omega_prime.config import load_config

    _no_rotation_env(monkeypatch)
    assert load_config()["rotation"] == {"enabled": False, "overlap_hours": 24}
    monkeypatch.setenv("OMEGA_PRIME_ROTATION_ENABLED", "1")
    monkeypatch.setenv("OMEGA_PRIME_ROTATION_OVERLAP_HOURS", "2")
    assert load_config()["rotation"] == {"enabled": True, "overlap_hours": 2}
    monkeypatch.setenv("OMEGA_PRIME_ROTATION_OVERLAP_HOURS", "bogus")
    assert load_config()["rotation"] == {"enabled": True, "overlap_hours": 24}


def test_due_only_flag_off_skips_without_mutation(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    _no_rotation_env(monkeypatch)
    path = _store(tmp_path)
    clock = FakeClock()
    create_token(path, scopes=["read"], label="svc", ttl_days=1, clock=clock)
    before = path.read_bytes()
    assert main(["rotate", "--file", str(path), "--due-only"], clock=clock) == 0
    out, err = capsys.readouterr()
    assert out == ""
    assert "disabled" in err
    assert path.read_bytes() == before


def test_rotation_appends_hash_only_chain_event(tmp_path: Path) -> None:
    path = _store(tmp_path)
    audit_path = _audit(tmp_path)
    clock = FakeClock()
    _, old = create_token(path, scopes=["read", "call"], label="ci", clock=clock)
    new_plain, _, new = rotate_token(
        path, label="ci", overlap_hours=1, clock=clock, audit_file=audit_path
    )
    tracer = GrokBotAuditTracer(audit_path)
    ok, count, _ = tracer.verify_integrity()
    assert (ok, count) == (True, 1)
    (event,) = tracer.read_recent(1)
    assert event["event"] == "token_rotated"
    assert event["prev"] == GENESIS_HASH
    assert event["details"] == {
        "label_hash": hashlib.sha256(b"ci").hexdigest(),
        "old_entry_hash": old.sha256,
        "new_entry_hash": new.sha256,
        "overlap_hours": 1,
    }
    body = audit_path.read_text(encoding="utf-8")
    assert new_plain not in body
    assert "omk_" not in body  # token-prefix grep count is 0
    assert '"ci"' not in body  # no raw label anywhere in the chain


def test_failing_audit_log_never_blocks_rotation(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    path = _store(tmp_path)
    clock = FakeClock()
    create_token(path, scopes=["read"], label="ci", clock=clock)
    monkeypatch.setattr(
        "omega_prime.grokbot.audit.GrokBotAuditTracer.log_event",
        lambda self, *a, **k: (_ for _ in ()).throw(OSError("read-only")),
    )
    new_plain, _, _ = rotate_token(
        path, label="ci", clock=clock, audit_file=_audit(tmp_path)
    )
    assert new_plain.startswith("omk_")
    assert "audit" in capsys.readouterr().err


# --- Task 3: metrics ------------------------------------------------------------


def test_token_metrics_counters_and_gauge(tmp_path: Path) -> None:
    path = _store(tmp_path)
    clock = FakeClock()
    create_token(path, scopes=["read"], label="a", clock=clock)
    create_token(path, scopes=["read"], label="b", ttl_days=1, clock=clock)

    registry = MetricsRegistry()
    handle = register_token_metrics(registry)
    summary = handle.observe(list_tokens(path), clock())
    assert summary == {"live": 2, "expired": 0, "newly_expired": []}
    handle.record_rotation()

    clock.now += 2 * 86400.0
    summary = handle.observe(list_tokens(path), clock())
    assert summary["live"] == 1 and summary["expired"] == 1
    assert len(summary["newly_expired"]) == 1
    # Re-observing the same expiry must not double-count.
    assert handle.observe(list_tokens(path), clock())["newly_expired"] == []

    text = registry.render()
    assert "omega_token_rotations_total 1\n" in text
    assert "omega_token_expiry_total 1\n" in text
    assert f'omega_token_active{{label_hash="{token_label_hash("a")}"}} 1\n' in text
    assert f'omega_token_active{{label_hash="{token_label_hash("b")}"}} 0\n' in text


def test_rotate_token_records_rotation_metric(tmp_path: Path) -> None:
    path = _store(tmp_path)
    clock = FakeClock()
    create_token(path, scopes=["read"], label="a", clock=clock)

    registry = MetricsRegistry()
    handle = register_token_metrics(registry)

    class _FakeRecorder:
        def __init__(self) -> None:
            self.calls = 0.0

        def record_rotation(self, n: float = 1.0) -> None:
            self.calls += n

    fake = _FakeRecorder()
    rotate_token(path, label="a", clock=clock, audit_file=None, metrics=fake)
    assert fake.calls == 1

    rotate_token(path, label="a", clock=clock, audit_file=None, metrics=handle)
    text = registry.render()
    assert "omega_token_rotations_total 1\n" in text

    # Default (no handle) stays backward compatible: rotates without recording.
    rotate_token(path, label="a", clock=clock, audit_file=None)
    assert registry.render() == text


def test_rotation_then_expiry_between_scrapes_bumps_expiry_total(
    tmp_path: Path,
) -> None:
    path = _store(tmp_path)
    clock = FakeClock()
    create_token(path, scopes=["read"], label="a", clock=clock)

    registry = MetricsRegistry()
    handle = register_token_metrics(registry)
    assert handle.observe(list_tokens(path), clock())["newly_expired"] == []

    rotate_token(
        path,
        label="a",
        overlap_hours=1,
        clock=clock,
        audit_file=None,
        metrics=handle,
    )
    clock.now += 2 * HOUR
    summary = handle.observe(list_tokens(path), clock())
    assert summary["expired"] == 1 and len(summary["newly_expired"]) == 1

    text = registry.render()
    assert "omega_token_rotations_total 1\n" in text
    assert "omega_token_expiry_total 1\n" in text


def test_concurrent_due_runs_mint_once(tmp_path: Path) -> None:
    """Two concurrent scheduled runs rotate a due entry exactly once."""
    path = _store(tmp_path)
    clock = FakeClock()
    create_token(path, scopes=["read"], label="svc", ttl_days=1, clock=clock)
    clock.now += 23 * HOUR  # 1h of TTL left: due inside a 24h window
    assert len(due_for_rotation(list_tokens(path), clock(), 24)) == 1

    outcomes: list[list[tuple[str, TokenRecord, TokenRecord]]] = []
    barrier = threading.Barrier(2)

    def _run() -> None:
        barrier.wait(timeout=10)
        outcomes.append(
            rotate_due_tokens(path, overlap_hours=24, clock=clock, audit_file=None)
        )

    workers = [threading.Thread(target=_run) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    assert all(not worker.is_alive() for worker in workers)
    assert sum(len(outcome) for outcome in outcomes) == 1
    records = list_tokens(path)
    assert len(records) == 2
    assert sum(1 for record in records if record.rotated_to is not None) == 1
    assert sum(1 for record in records if record.rotated_from is not None) == 1
    # A follow-up run mints nothing.
    assert rotate_due_tokens(path, overlap_hours=24, clock=clock, audit_file=None) == []


def test_cli_rotate_writes_default_audit_file(tmp_path: Path, capsys) -> None:
    """A plain CLI rotate (no --audit-file) writes the sibling audit event."""
    path = _store(tmp_path)
    clock = FakeClock()
    assert main(["new", "--file", str(path), "--label", "ci"], clock=clock) == 0
    capsys.readouterr()
    expected = default_audit_file_for(path)
    assert not expected.exists()
    assert main(["rotate", "--file", str(path), "--label", "ci"], clock=clock) == 0
    capsys.readouterr()
    assert expected.exists()
    lines = expected.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    (_, new) = sorted(list_tokens(path), key=lambda r: r.created_at)
    assert event["event"] == "token_rotated"
    assert event["details"]["new_entry_hash"] == new.sha256
    assert event["details"]["label_hash"] == hashlib.sha256(b"ci").hexdigest()


def test_cli_due_only_rotate_writes_default_audit_file(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """The --due-only CLI branch audits to the sibling file when unset."""
    monkeypatch.setenv("OMEGA_PRIME_ROTATION_ENABLED", "1")
    monkeypatch.delenv("OMEGA_PRIME_ROTATION_OVERLAP_HOURS", raising=False)
    path = _store(tmp_path)
    clock = FakeClock()
    create_token(path, scopes=["read"], label="svc", ttl_days=0.5 / 24, clock=clock)
    expected = default_audit_file_for(path)
    assert main(["rotate", "--file", str(path), "--due-only"], clock=clock) == 0
    out, _ = capsys.readouterr()
    assert len(out.split()) == 1  # fresh plaintext on stdout
    lines = expected.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "token_rotated"


def test_active_snapshot_counts_bearer_principal(tmp_path: Path) -> None:
    """Combined file + single-bearer store counts every live caller."""
    path = _store(tmp_path)
    clock = FakeClock()
    create_token(path, scopes=["read"], label="file-svc", clock=clock)
    bearer = TokenStore.from_token("omk_" + "e" * 32)
    file_store = FileTokenStore(path, clock=clock, reload_interval=0)
    combined = CompositeTokenStore([file_store, bearer])
    assert [p.id for p in non_file_principals(combined)] == ["env-token"]
    assert non_file_principals(file_store) == []

    registry = MetricsRegistry()
    handle = register_token_metrics(registry)
    observe_active_with_bearer(handle, combined, list_tokens(path), clock())
    text = registry.render()
    assert (
        f'omega_token_active{{label_hash="{token_label_hash("file-svc")}"}} 1\n' in text
    )
    assert f'omega_token_active{{label_hash="{token_label_hash("")}"}} 1\n' in text
