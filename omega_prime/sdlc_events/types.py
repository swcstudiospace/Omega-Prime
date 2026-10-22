"""Typed envelopes for SDLC events."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventKind(StrEnum):
    CHECK_COMPLETED = "check_completed"
    REVIEW_SIGNAL = "review_signal"
    PR_UPDATED = "pr_updated"
    BLOCKED_SIGNAL = "blocked_signal"
    FLEET_SIGNAL = "fleet_signal"


@dataclass(frozen=True, slots=True)
class DeliveryId:
    """Idempotency key for a single webhook/listener delivery."""

    value: str

    def __post_init__(self) -> None:
        if not self.value or not self.value.strip():
            raise ValueError("DeliveryId must be non-empty")


@dataclass(frozen=True, slots=True)
class RepoRef:
    owner: str
    name: str

    @property
    def full(self) -> str:
        return f"{self.owner}/{self.name}"

    def allowed(self, allowlist: frozenset[str]) -> bool:
        return self.full in allowlist or f"{self.owner}/*" in allowlist


@dataclass(frozen=True, slots=True)
class PrRef:
    repo: RepoRef
    number: int
    head_sha: str
    draft: bool = False
    auto_deploy: bool = False

    def __post_init__(self) -> None:
        if self.number <= 0:
            raise ValueError("PR number must be positive")
        if not self.head_sha:
            raise ValueError("head_sha required")


@dataclass(frozen=True, slots=True)
class SdlcEvent:
    kind: EventKind
    delivery: DeliveryId
    pr: PrRef
    payload: dict[str, Any] = field(default_factory=dict)
