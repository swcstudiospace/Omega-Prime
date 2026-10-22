"""SDLC event handlers — side-effect ports injected by caller."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from omega_prime.sdlc_events.gate import GateInput, evaluate_merge_gate
from omega_prime.sdlc_events.idempotency import IdempotencyStore
from omega_prime.sdlc_events.types import EventKind, SdlcEvent


class TaskStorePort(Protocol):
    def upsert_note(self, task_id: str, state: str, note: str) -> None: ...


class MergePort(Protocol):
    def merge(self, repo: str, pr: int) -> str: ...


DEFAULT_ALLOWLIST = frozenset({"swcstudiospace/Omega-Prime", "swcstudiospace/*"})


@dataclass(frozen=True, slots=True)
class HandlerResult:
    action: str
    detail: str
    skipped_duplicate: bool = False


def _task_id_for(event: SdlcEvent) -> str:
    return f"gb:pr-{event.pr.repo.name}-{event.pr.number}"


def on_gate_signal(
    event: SdlcEvent,
    *,
    store: TaskStorePort,
    idem: IdempotencyStore,
    greptile_score: int | None,
    greptile_on_head: bool,
    ci_green: bool,
    mergeable: bool,
    merge: MergePort | None = None,
    allowlist: frozenset[str] = DEFAULT_ALLOWLIST,
    dry_run: bool = True,
) -> HandlerResult:
    if event.kind not in {
        EventKind.CHECK_COMPLETED,
        EventKind.PR_UPDATED,
        EventKind.REVIEW_SIGNAL,
    }:
        return HandlerResult(action="ignore", detail=f"kind={event.kind.value}")
    if idem.seen(event.delivery):
        return HandlerResult(
            action="skip", detail="duplicate_delivery", skipped_duplicate=True
        )
    idem.mark(event.delivery)
    gate = evaluate_merge_gate(
        GateInput(
            greptile_score=greptile_score,
            greptile_on_head=greptile_on_head,
            ci_green=ci_green,
            mergeable=mergeable,
            draft=event.pr.draft,
            auto_deploy=event.pr.auto_deploy,
            repo_allowed=event.pr.repo.allowed(allowlist),
        )
    )
    tid = _task_id_for(event)
    if not gate.ok:
        note = f"gate blocked on {event.pr.head_sha[:7]}: {', '.join(gate.reasons)}"
        store.upsert_note(
            tid, "blocked" if "ci_not_green" in gate.reasons else "working", note
        )
        return HandlerResult(action="gate_blocked", detail=note)
    note = f"gate open on {event.pr.head_sha[:7]} (Greptile 5/5, CI green, mergeable)"
    if dry_run or merge is None:
        store.upsert_note(tid, "working", note + " [dry-run; no merge]")
        return HandlerResult(action="gate_open_dry_run", detail=note)
    sha = merge.merge(event.pr.repo.full, event.pr.number)
    store.upsert_note(tid, "done", note + f" merged={sha}")
    return HandlerResult(action="merged", detail=sha)


def on_review_signal(
    event: SdlcEvent,
    *,
    store: TaskStorePort,
    idem: IdempotencyStore,
    greptile_score: int | None,
) -> HandlerResult:
    if idem.seen(event.delivery):
        return HandlerResult(
            action="skip", detail="duplicate_delivery", skipped_duplicate=True
        )
    idem.mark(event.delivery)
    tid = _task_id_for(event)
    score = greptile_score if greptile_score is not None else -1
    note = f"Greptile signal score={score} head={event.pr.head_sha[:7]}"
    state = "working" if score >= 5 else "blocked"
    store.upsert_note(tid, state, note)
    return HandlerResult(action="review_noted", detail=note)


def on_blocked_signal(
    event: SdlcEvent,
    *,
    store: TaskStorePort,
    idem: IdempotencyStore,
    reason: str,
) -> HandlerResult:
    if idem.seen(event.delivery):
        return HandlerResult(
            action="skip", detail="duplicate_delivery", skipped_duplicate=True
        )
    idem.mark(event.delivery)
    tid = _task_id_for(event)
    note = f"blocked: {reason} head={event.pr.head_sha[:7]}"
    store.upsert_note(tid, "blocked", note)
    return HandlerResult(action="blocked", detail=note)
