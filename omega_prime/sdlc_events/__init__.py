"""Event-driven SDLC handlers for Grok Bot (00043).

Transport-agnostic: GitHub/Origin listener payloads are normalized into
typed envelopes before handlers run. Merge side-effects are gated and
default to dry-run.
"""

from omega_prime.sdlc_events.gate import GateInput, GateResult, evaluate_merge_gate
from omega_prime.sdlc_events.handlers import (
    HandlerResult,
    on_blocked_signal,
    on_gate_signal,
    on_review_signal,
    on_review_then_gate,
)
from omega_prime.sdlc_events.types import (
    DeliveryId,
    EventKind,
    PrRef,
    RepoRef,
    SdlcEvent,
)

__all__ = [
    "DeliveryId",
    "EventKind",
    "GateInput",
    "GateResult",
    "HandlerResult",
    "PrRef",
    "RepoRef",
    "SdlcEvent",
    "evaluate_merge_gate",
    "on_blocked_signal",
    "on_gate_signal",
    "on_review_signal",
    "on_review_then_gate",
]
