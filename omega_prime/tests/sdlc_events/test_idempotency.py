from pathlib import Path

import pytest

from omega_prime.sdlc_events.handlers import (
    HeadMovedError,
    on_blocked_signal,
    on_gate_signal,
)
from omega_prime.sdlc_events.idempotency import ClaimOutcome, IdempotencyStore
from omega_prime.sdlc_events.task_store import MemoryTaskStore
from omega_prime.sdlc_events.types import (
    DeliveryId,
    EventKind,
    PrRef,
    RepoRef,
    SdlcEvent,
)


def _ev(kind: EventKind, delivery: str = "d1", head: str = "abc1234def") -> SdlcEvent:
    return SdlcEvent(
        kind=kind,
        delivery=DeliveryId(delivery),
        pr=PrRef(
            repo=RepoRef("swcstudiospace", "Omega-Prime"), number=75, head_sha=head
        ),
    )


class _FailingStore(MemoryTaskStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    def upsert_note(self, task_id: str, state: str, note: str) -> None:
        if self.fail:
            raise OSError("disk hiccup")
        super().upsert_note(task_id, state, note)


def test_failed_attempt_releases_claim_and_retry_completes() -> None:
    store = _FailingStore()
    idem = IdempotencyStore()
    ev = _ev(EventKind.BLOCKED_SIGNAL)
    with pytest.raises(OSError):
        on_blocked_signal(ev, store=store, idem=idem, reason="ci_red")
    assert not idem.is_done(ev.delivery)
    store.fail = False
    r = on_blocked_signal(ev, store=store, idem=idem, reason="ci_red")
    assert r.action == "blocked"
    assert idem.is_done(ev.delivery)


def test_shared_file_ledger_is_seen_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "idem.json"
    a, b = IdempotencyStore(path), IdempotencyStore(path)
    d = DeliveryId("gh-1")
    assert a.claim(d) is ClaimOutcome.ACQUIRED
    assert b.claim(d) is ClaimOutcome.IN_PROGRESS
    a.complete(d)
    assert b.claim(d) is ClaimOutcome.DONE


def test_stale_claim_expires(tmp_path: Path) -> None:
    now = [1000.0]
    path = tmp_path / "idem.json"
    a = IdempotencyStore(path, claim_ttl_s=60, clock=lambda: now[0])
    b = IdempotencyStore(path, claim_ttl_s=60, clock=lambda: now[0])
    d = DeliveryId("gh-2")
    assert a.claim(d) is ClaimOutcome.ACQUIRED
    now[0] += 61
    assert b.claim(d) is ClaimOutcome.ACQUIRED


class _HeadCheckingMerge:
    def __init__(self, live_head: str) -> None:
        self.live_head = live_head
        self.calls: list[str] = []

    def merge(self, repo: str, pr: int, *, expected_head_sha: str) -> str:
        self.calls.append(expected_head_sha)
        if expected_head_sha != self.live_head:
            raise HeadMovedError(f"{repo}#{pr} head moved")
        return "m3rg3"


def _gate(ev: SdlcEvent, store: MemoryTaskStore, idem: IdempotencyStore, merge):
    return on_gate_signal(
        ev,
        store=store,
        idem=idem,
        greptile_score=5,
        greptile_on_head=True,
        ci_green=True,
        mergeable=True,
        merge=merge,
        dry_run=False,
    )


def test_merge_receives_checked_head_and_refuses_moved_head() -> None:
    store, idem = MemoryTaskStore(), IdempotencyStore()
    ev = _ev(EventKind.CHECK_COMPLETED, head="checked111")
    merge = _HeadCheckingMerge(live_head="newpush222")
    with pytest.raises(HeadMovedError):
        _gate(ev, store, idem, merge)
    assert merge.calls == ["checked111"]
    assert not idem.is_done(ev.delivery)


def test_merge_succeeds_on_matching_head() -> None:
    store, idem = MemoryTaskStore(), IdempotencyStore()
    ev = _ev(EventKind.CHECK_COMPLETED, head="same333")
    r = _gate(ev, store, idem, _HeadCheckingMerge(live_head="same333"))
    assert r.action == "merged"
    assert idem.is_done(ev.delivery)
