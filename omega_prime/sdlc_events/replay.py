"""Dry-run CLI: python -m omega_prime.sdlc_events.replay --fixture path.json"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

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


def _event_from(obj: dict) -> SdlcEvent:
    repo = RepoRef(owner=obj["owner"], name=obj["repo"])
    pr = PrRef(
        repo=repo,
        number=int(obj["pr"]),
        head_sha=obj["head_sha"],
        draft=bool(obj.get("draft", False)),
        auto_deploy=bool(obj.get("auto_deploy", False)),
    )
    return SdlcEvent(
        kind=EventKind(obj["kind"]),
        delivery=DeliveryId(obj["delivery_id"]),
        pr=pr,
        payload=obj.get("payload") or {},
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Replay SDLC event fixtures (dry-run)")
    p.add_argument("--fixture", type=Path, required=True)
    args = p.parse_args(argv)
    data = json.loads(args.fixture.read_text())
    events = data if isinstance(data, list) else data.get("events", [])
    store = MemoryTaskStore()
    idem = IdempotencyStore()
    for raw in events:
        ev = _event_from(raw)
        if ev.kind == EventKind.BLOCKED_SIGNAL:
            r = on_blocked_signal(
                ev, store=store, idem=idem, reason=raw.get("reason", "ci_red")
            )
        elif ev.kind == EventKind.REVIEW_SIGNAL:
            r = on_review_signal(
                ev, store=store, idem=idem, greptile_score=raw.get("greptile_score")
            )
        else:
            r = on_gate_signal(
                ev,
                store=store,
                idem=idem,
                greptile_score=raw.get("greptile_score"),
                greptile_on_head=bool(raw.get("greptile_on_head", False)),
                ci_green=bool(raw.get("ci_green", False)),
                mergeable=bool(raw.get("mergeable", False)),
                dry_run=True,
            )
        print(f"{ev.delivery.value}: {r.action} — {r.detail}")
    print(json.dumps(store.rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
