# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Thread-safe lifecycle management for RPC extension UI requests."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, TypeAlias

from omega_prime.engine.protocol import (
    AskAnswer,
    AskRequest,
    ConfirmRequest,
    SelectRequest,
    UiRequest,
    build_ui_response,
)


class RequestStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    ANSWERED = "answered"
    STALE = "stale"
    DUPLICATE = "duplicate"
    INVALID = "invalid"
    SEND_FAILED = "send_failed"
    UNDELIVERABLE = "undeliverable"
    UNKNOWN = "unknown"
    WRONG_IDENTITY = "wrong_identity"


class DeliveryStatus(StrEnum):
    DELIVERED = "delivered"
    UNDELIVERABLE = "undeliverable"


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    job_id: str
    session_generation: str
    turn_generation: str
    request_id: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (
                self.job_id,
                self.session_generation,
                self.turn_generation,
                self.request_id,
            )
        ):
            raise ValueError("request identity fields must be non-empty strings")


def new_generation() -> str:
    """Create a non-counter generation identifier suitable across restarts."""
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class OperatorRequest:
    identity: RequestIdentity
    request: UiRequest


class OperatorTransport(Protocol):
    """Non-blocking delivery boundary for a request awaiting operator input."""

    def deliver(self, request: OperatorRequest) -> DeliveryStatus: ...


@dataclass(frozen=True, slots=True)
class RequestSnapshot:
    identity: RequestIdentity
    request: UiRequest
    state: RequestStatus


@dataclass(slots=True)
class _RequestEntry:
    identity: RequestIdentity
    request: UiRequest
    state: RequestStatus


ResponseSender: TypeAlias = Callable[[dict[str, Any]], None]
AskResponse: TypeAlias = Sequence[AskAnswer]
UiAnswer: TypeAlias = str | bool | AskResponse


class PendingRequestRegistry:
    """Own request identities and reserve exactly one response write."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[RequestIdentity, _RequestEntry] = {}
        self._request_ids: dict[str, set[RequestIdentity]] = {}

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def state(self, identity: RequestIdentity) -> RequestStatus | None:
        with self._lock:
            entry = self._entries.get(identity)
            return entry.state if entry is not None else None

    def get(self, identity: RequestIdentity) -> RequestSnapshot | None:
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                return None
            return RequestSnapshot(entry.identity, entry.request, entry.state)

    def open(
        self,
        identity: RequestIdentity,
        request: UiRequest,
        transport: OperatorTransport | None,
    ) -> RequestStatus:
        """Register and deliver an observed request, failing closed if undeliverable."""
        if (
            not isinstance(request, (AskRequest, SelectRequest, ConfirmRequest))
            or identity.request_id != request.id
        ):
            return RequestStatus.INVALID
        with self._lock:
            if identity in self._entries:
                return RequestStatus.DUPLICATE
            entry = _RequestEntry(identity, request, RequestStatus.PENDING)
            self._entries[identity] = entry
            self._request_ids.setdefault(identity.request_id, set()).add(identity)
            if transport is None:
                entry.state = RequestStatus.UNDELIVERABLE
                return entry.state

        try:
            delivered = (
                transport.deliver(OperatorRequest(identity, request))
                is DeliveryStatus.DELIVERED
            )
        except Exception:
            delivered = False

        with self._lock:
            if entry.state is RequestStatus.PENDING and not delivered:
                entry.state = RequestStatus.UNDELIVERABLE
            return entry.state

    def submit(
        self,
        identity: RequestIdentity,
        answer: UiAnswer,
        send_response: ResponseSender,
    ) -> RequestStatus:
        """Validate then reserve a single response write before calling the sender."""
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                if identity.request_id in self._request_ids:
                    return RequestStatus.WRONG_IDENTITY
                return RequestStatus.UNKNOWN
            if entry.state is RequestStatus.STALE:
                return RequestStatus.STALE
            if entry.state is RequestStatus.UNDELIVERABLE:
                return RequestStatus.UNDELIVERABLE
            if entry.state is not RequestStatus.PENDING:
                return RequestStatus.DUPLICATE
            if not _valid_answer(entry.request, answer):
                return RequestStatus.INVALID
            try:
                response = build_ui_response(entry.request, answer)
            except (TypeError, ValueError):
                return RequestStatus.INVALID
            entry.state = RequestStatus.SENDING

        try:
            send_response(response)
        except Exception:
            with self._lock:
                entry.state = RequestStatus.SEND_FAILED
            return RequestStatus.SEND_FAILED

        with self._lock:
            entry.state = RequestStatus.ANSWERED
        return RequestStatus.ANSWERED

    def invalidate(self, identity: RequestIdentity) -> bool:
        """Mark one pending request stale; a reserved send is allowed to finish."""
        return self._invalidate(lambda entry: entry.identity == identity) > 0

    def invalidate_turn(
        self, job_id: str, session_generation: str, turn_generation: str
    ) -> int:
        return self._invalidate(
            lambda entry: (
                entry.identity.job_id == job_id
                and entry.identity.session_generation == session_generation
                and entry.identity.turn_generation == turn_generation
            )
        )

    def invalidate_session(self, job_id: str, session_generation: str) -> int:
        return self._invalidate(
            lambda entry: (
                entry.identity.job_id == job_id
                and entry.identity.session_generation == session_generation
            )
        )

    def invalidate_job(self, job_id: str) -> int:
        return self._invalidate(lambda entry: entry.identity.job_id == job_id)

    def _invalidate(self, matches: Callable[[_RequestEntry], bool]) -> int:
        changed = 0
        with self._lock:
            for entry in self._entries.values():
                if matches(entry) and entry.state is RequestStatus.PENDING:
                    entry.state = RequestStatus.STALE
                    changed += 1
        return changed


def _valid_answer(request: UiRequest, answer: object) -> bool:
    if isinstance(request, SelectRequest):
        return isinstance(answer, str) and answer in request.options
    if isinstance(request, ConfirmRequest):
        return isinstance(answer, bool)
    if isinstance(request, AskRequest):
        return _valid_ask_answer(request, answer)
    return False


def _valid_ask_answer(request: AskRequest, answer: object) -> bool:
    if not isinstance(answer, Sequence) or isinstance(answer, (str, bytes)):
        return False
    if len(answer) != len(request.questions):
        return False
    for question, answer_item in zip(request.questions, answer, strict=True):
        if not isinstance(answer_item, AskAnswer) or answer_item.id != question.id:
            return False
        selected = answer_item.selected_options
        if not isinstance(selected, Sequence) or isinstance(selected, (str, bytes)):
            return False
        labels = {option.label for option in question.options}
        if any(not isinstance(value, str) or value not in labels for value in selected):
            return False
        if len(set(selected)) != len(selected):
            return False
        custom_input = answer_item.custom_input
        if custom_input is not None and not isinstance(custom_input, str):
            return False
        if not question.multi and (
            len(selected) > 1
            or (
                bool(selected)
                and custom_input is not None
                and bool(custom_input.strip())
            )
        ):
            return False
    return True
