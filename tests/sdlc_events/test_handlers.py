from omega_prime.sdlc_events.handlers import (
    on_blocked_signal,
    on_gate_signal,
    on_review_signal,
)
from omega_prime.sdlc_events.idempotency import IdempotencyStore
from omega_prime.sdlc_events.task_store import MemoryTaskStore
from omega_prime.sdlc_events.types import (
    DeliveryId,
    EventKind,
    PrRef,
    RepoRef,
    SdlcEvent,
)


def _ev(kind: EventKind, delivery: str = "d1") -> SdlcEvent:
    return SdlcEvent(
        kind=kind,
        delivery=DeliveryId(delivery),
        pr=PrRef(
            repo=RepoRef("swcstudiospace", "Omega-Prime"),
            number=74,
            head_sha="abc1234deadbeef",
        ),
    )


def test_gate_dry_run_writes_note() -> None:
    store = MemoryTaskStore()
    idem = IdempotencyStore()
    r = on_gate_signal(
        _ev(EventKind.CHECK_COMPLETED),
        store=store,
        idem=idem,
        greptile_score=5,
        greptile_on_head=True,
        ci_green=True,
        mergeable=True,
        dry_run=True,
    )
    assert r.action == "gate_open_dry_run"
    assert "gb:pr-Omega-Prime-74" in store.rows


def test_duplicate_delivery_skipped() -> None:
    store = MemoryTaskStore()
    idem = IdempotencyStore()
    on_review_signal(
        _ev(EventKind.REVIEW_SIGNAL), store=store, idem=idem, greptile_score=3
    )
    r2 = on_review_signal(
        _ev(EventKind.REVIEW_SIGNAL), store=store, idem=idem, greptile_score=3
    )
    assert r2.skipped_duplicate


def test_blocked_signal() -> None:
    store = MemoryTaskStore()
    idem = IdempotencyStore()
    r = on_blocked_signal(
        _ev(EventKind.BLOCKED_SIGNAL), store=store, idem=idem, reason="ci_red"
    )
    assert r.action == "blocked"
    assert store.rows["gb:pr-Omega-Prime-74"]["state"] == "blocked"
