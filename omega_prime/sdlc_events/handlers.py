"""SDLC event handlers — side-effect ports injected by caller."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from omega_prime.sdlc_events.gate import GateInput, evaluate_merge_gate
from omega_prime.sdlc_events.idempotency import ClaimOutcome, IdempotencyStore
from omega_prime.sdlc_events.types import EventKind, SdlcEvent


class TaskStorePort(Protocol):
    def upsert_note(self, task_id: str, state: str, note: str) -> None: ...


class MergePort(Protocol):
    """Live merge adapter.

    Implementations MUST merge only if the PR head still equals
    ``expected_head_sha`` (e.g. GitHub's ``sha`` merge parameter) and raise
    ``HeadMovedError`` otherwise, so a push between gate check and merge can
    never land an unchecked commit.
    """

    def merge(self, repo: str, pr: int, *, expected_head_sha: str) -> str: ...


class HeadMovedError(RuntimeError):
    """The PR head changed after the gate evaluated it; the merge was refused."""


DEFAULT_ALLOWLIST = frozenset({"swcstudiospace/Omega-Prime", "swcstudiospace/*"})


@dataclass(frozen=True, slots=True)
class HandlerResult:
    action: str
    detail: str
    skipped_duplicate: bool = False


def _task_id_for(event: SdlcEvent) -> str:
    return f"gb:pr-{event.pr.repo.name}-{event.pr.number}"


def _run_claimed(
    event: SdlcEvent, idem: IdempotencyStore, work: Callable[[], HandlerResult]
) -> HandlerResult:
    """Run ``work`` once per delivery; release the claim if it fails so it can retry."""
    claim = idem.claim(event.delivery)
    if not claim.acquired or claim.token is None:
        detail = (
            "duplicate_delivery"
            if claim.outcome is ClaimOutcome.DONE
            else "delivery_in_progress"
        )
        return HandlerResult(action="skip", detail=detail, skipped_duplicate=True)
    try:
        result = work()
    except BaseException:
        idem.release(event.delivery, claim.token)
        raise
    idem.complete(event.delivery, claim.token)
    return result


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

    def work() -> HandlerResult:
        return _gate_work(
            event,
            store=store,
            greptile_score=greptile_score,
            greptile_on_head=greptile_on_head,
            ci_green=ci_green,
            mergeable=mergeable,
            merge=merge,
            allowlist=allowlist,
            dry_run=dry_run,
        )

    return _run_claimed(event, idem, work)


def _blocked_or_working(reasons: tuple[str, ...]) -> str:
    """``blocked`` when a fix is needed (red CI or a Greptile score below 5).

    Waiting reasons (no review yet, review on an older head, draft, not yet
    mergeable) stay ``working``. A review below 5 must stay ``blocked`` after
    the gate runs, or ``on_review_then_gate`` would overwrite the review's
    ``blocked`` row and hide the cue to start fixes.
    """
    needs_fix = "ci_not_green" in reasons or any(
        r.startswith("greptile_score_") and r != "greptile_score_missing"
        for r in reasons
    )
    return "blocked" if needs_fix else "working"


def _gate_work(
    event: SdlcEvent,
    *,
    store: TaskStorePort,
    greptile_score: int | None,
    greptile_on_head: bool,
    ci_green: bool,
    mergeable: bool,
    merge: MergePort | None,
    allowlist: frozenset[str],
    dry_run: bool,
) -> HandlerResult:
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
        store.upsert_note(tid, _blocked_or_working(gate.reasons), note)
        return HandlerResult(action="gate_blocked", detail=note)
    note = f"gate open on {event.pr.head_sha[:7]} (Greptile 5/5, CI green, mergeable)"
    if dry_run or merge is None:
        store.upsert_note(tid, "working", note + " [dry-run; no merge]")
        return HandlerResult(action="gate_open_dry_run", detail=note)
    sha = merge.merge(
        event.pr.repo.full, event.pr.number, expected_head_sha=event.pr.head_sha
    )
    store.upsert_note(tid, "done", note + f" merged={sha}")
    return HandlerResult(action="merged", detail=sha)


def _review_work(
    event: SdlcEvent, *, store: TaskStorePort, greptile_score: int | None
) -> HandlerResult:
    score = greptile_score if greptile_score is not None else -1
    note = f"Greptile signal score={score} head={event.pr.head_sha[:7]}"
    state = "working" if score >= 5 else "blocked"
    store.upsert_note(_task_id_for(event), state, note)
    return HandlerResult(action="review_noted", detail=note)


def on_review_signal(
    event: SdlcEvent,
    *,
    store: TaskStorePort,
    idem: IdempotencyStore,
    greptile_score: int | None,
) -> HandlerResult:
    return _run_claimed(
        event,
        idem,
        lambda: _review_work(event, store=store, greptile_score=greptile_score),
    )


def on_review_then_gate(
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
) -> tuple[HandlerResult, ...]:
    """A review wakes both the review note and the merge gate (see the
    routine→event mapping). Both run under one delivery claim: calling the two
    handlers one after the other would let the first claim the delivery and the
    second skip it as a duplicate."""
    if event.kind is not EventKind.REVIEW_SIGNAL:
        return (HandlerResult(action="ignore", detail=f"kind={event.kind.value}"),)
    results: list[HandlerResult] = []

    def work() -> HandlerResult:
        results.append(_review_work(event, store=store, greptile_score=greptile_score))
        results.append(
            _gate_work(
                event,
                store=store,
                greptile_score=greptile_score,
                greptile_on_head=greptile_on_head,
                ci_green=ci_green,
                mergeable=mergeable,
                merge=merge,
                allowlist=allowlist,
                dry_run=dry_run,
            )
        )
        return results[-1]

    claimed = _run_claimed(event, idem, work)
    return tuple(results) if results else (claimed,)


def on_blocked_signal(
    event: SdlcEvent,
    *,
    store: TaskStorePort,
    idem: IdempotencyStore,
    reason: str,
) -> HandlerResult:
    def work() -> HandlerResult:
        note = f"blocked: {reason} head={event.pr.head_sha[:7]}"
        store.upsert_note(_task_id_for(event), "blocked", note)
        return HandlerResult(action="blocked", detail=note)

    return _run_claimed(event, idem, work)
