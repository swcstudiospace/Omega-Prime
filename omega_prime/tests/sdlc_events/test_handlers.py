from omega_prime.sdlc_events.handlers import (
    on_blocked_signal,
    on_gate_signal,
    on_review_signal,
    on_review_then_gate,
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


def test_review_wakes_gate_under_one_claim() -> None:
    store = MemoryTaskStore()
    idem = IdempotencyStore()

    def wake() -> tuple:
        return on_review_then_gate(
            _ev(EventKind.REVIEW_SIGNAL),
            store=store,
            idem=idem,
            greptile_score=5,
            greptile_on_head=True,
            ci_green=True,
            mergeable=True,
        )

    assert [r.action for r in wake()] == ["review_noted", "gate_open_dry_run"]
    again = wake()
    assert len(again) == 1 and again[0].skipped_duplicate


def test_replay_runs_the_gate_for_a_review(tmp_path, capsys) -> None:
    import json

    from omega_prime.sdlc_events.replay import main

    fixture = tmp_path / "review.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "kind": "review_signal",
                    "delivery_id": "deliv-r1",
                    "owner": "swcstudiospace",
                    "repo": "Omega-Prime",
                    "pr": 74,
                    "head_sha": "f21a888cd2f1c320324591b296a17fd1ec0c18ec",
                    "greptile_score": 5,
                    "greptile_on_head": True,
                    "ci_green": True,
                    "mergeable": True,
                }
            ]
        )
    )
    assert main(["--fixture", str(fixture)]) == 0
    out = capsys.readouterr().out
    assert "deliv-r1: review_noted" in out
    assert "deliv-r1: gate_open_dry_run" in out
