# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Behavioral tests for the RPC UI protocol and live answer lifecycle."""

from __future__ import annotations

import threading
from typing import Any, cast

import pytest

from omega_prime.engine.hitl import (
    DeliveryStatus,
    OperatorRequest,
    PendingRequestRegistry,
    RequestIdentity,
    RequestStatus,
    new_generation,
)
from omega_prime.engine.protocol import (
    EVENT_FILTER,
    OMP_SUPPORTED_MAJOR,
    THINKING_LEVELS,
    AskAnswer,
    AskRequest,
    ConfirmRequest,
    SelectRequest,
    UiFrameError,
    build_ui_response,
    event_filter,
    parse_ui_request,
)


def _select_frame(request_id: str = "select-1") -> dict[str, object]:
    return {
        "type": "extension_ui_request",
        "id": request_id,
        "method": "select",
        "title": "Choose a model",
        "options": ["fast", "careful"],
        "optionDetails": [{"description": "quick"}, {"description": "thorough"}],
    }


def _confirm_frame(request_id: str = "confirm-1") -> dict[str, object]:
    return {
        "type": "extension_ui_request",
        "id": request_id,
        "method": "confirm",
        "title": "Proceed?",
        "message": "Continue with the operation?",
    }


def _ask_frame(request_id: str = "ask-1") -> dict[str, object]:
    return {
        "type": "extension_ui_request",
        "id": request_id,
        "method": "ask",
        "questions": [
            {
                "id": "q1",
                "question": "Choose a color",
                "options": [{"label": "blue"}, {"label": "green"}],
            }
        ],
    }


class _Transport:
    def __init__(self, status: DeliveryStatus = DeliveryStatus.DELIVERED) -> None:
        self.status = status
        self.requests: list[OperatorRequest] = []

    def deliver(self, request: OperatorRequest) -> DeliveryStatus:
        self.requests.append(request)
        return self.status


def _identity(request_id: str = "select-1") -> RequestIdentity:
    return RequestIdentity("job-a", "session-a", "turn-a", request_id)


def _open_select(
    registry: PendingRequestRegistry,
    *,
    identity: RequestIdentity | None = None,
    transport: _Transport | None = None,
) -> tuple[RequestIdentity, SelectRequest, _Transport]:
    parsed = parse_ui_request(_select_frame())
    assert isinstance(parsed, SelectRequest)
    request_identity = identity or _identity(parsed.id)
    operator_transport = transport or _Transport()
    result = registry.open(request_identity, parsed, operator_transport)
    assert result is RequestStatus.PENDING
    return request_identity, parsed, operator_transport


def test_rpc_filter_preserves_default_while_rpc_ui_receives_ui_requests() -> None:
    frames = [
        {"type": "response"},
        {"type": "agent_start"},
        {"type": "extension_ui_request"},
    ]
    rpc_filter = event_filter("rpc")
    rpc_ui_filter = event_filter("rpc-ui")

    def visible(mode_filter: list[str]) -> list[dict[str, str]]:
        return [frame for frame in frames if frame["type"] in mode_filter]

    assert rpc_filter == EVENT_FILTER
    assert visible(rpc_filter) == frames[:2]
    assert visible(rpc_ui_filter) == frames


def test_max_is_supported_but_auto_is_not() -> None:
    assert OMP_SUPPORTED_MAJOR == 18
    assert "max" in THINKING_LEVELS
    assert "auto" not in THINKING_LEVELS


@pytest.mark.parametrize(
    ("frame", "expected_type"),
    [
        (_select_frame(), SelectRequest),
        (_confirm_frame(), ConfirmRequest),
        (_ask_frame(), AskRequest),
    ],
)
def test_request_parser_normalizes_observed_interactions(frame, expected_type) -> None:
    parsed = parse_ui_request(frame)
    assert isinstance(parsed, (SelectRequest, ConfirmRequest, AskRequest))

    assert isinstance(parsed, expected_type)
    assert parsed.id == frame["id"]
    if isinstance(parsed, SelectRequest):
        assert parsed.title == "Choose a model"
        assert parsed.options == ("fast", "careful")
        assert [detail.description for detail in parsed.option_details or ()] == [
            "quick",
            "thorough",
        ]
    elif isinstance(parsed, ConfirmRequest):
        assert parsed.title == "Proceed?"
        assert parsed.message == "Continue with the operation?"
    else:
        assert parsed.questions[0].id == "q1"
        assert parsed.questions[0].question == "Choose a color"
        assert parsed.questions[0].options[0].label == "blue"
        assert parsed.questions[0].multi is False
    assert not hasattr(parsed, "timeout")


@pytest.mark.parametrize(
    "timeout",
    [120, 120.5],
)
def test_request_parser_ignores_observed_timeout_without_using_a_deadline(
    timeout: int | float,
) -> None:
    frame = {**_confirm_frame(), "timeout": timeout}

    parsed = parse_ui_request(frame)

    assert isinstance(parsed, ConfirmRequest)
    assert parsed.id == "confirm-1"
    assert not hasattr(parsed, "timeout")


@pytest.mark.parametrize(
    ("frame", "code"),
    [
        (None, "not_object"),
        ({"type": "response", "id": "x", "method": "confirm"}, "wrong_type"),
        ({"type": "extension_ui_request", "id": "", "method": "confirm"}, "invalid_id"),
        ({"type": "extension_ui_request", "id": 1, "method": "confirm"}, "invalid_id"),
        (
            {"type": "extension_ui_request", "id": "x", "method": "input"},
            "unsupported_method",
        ),
        (
            {
                "type": "extension_ui_request",
                "id": "x",
                "method": "select",
                "title": "x",
                "options": ["a", 2],
            },
            "invalid_options",
        ),
        (
            {
                "type": "extension_ui_request",
                "id": "x",
                "method": "confirm",
                "title": "x",
            },
            "missing_message",
        ),
        (
            {
                "type": "extension_ui_request",
                "id": "x",
                "method": "ask",
                "questions": [
                    {"id": "q", "question": "q", "options": ["not-an-option-object"]}
                ],
            },
            "invalid_questions",
        ),
    ],
)
def test_malformed_or_unsupported_requests_return_typed_errors(frame, code) -> None:
    parsed = parse_ui_request(frame)

    assert isinstance(parsed, UiFrameError)
    assert parsed.code == code


def test_parser_rejects_unhashable_method_and_explicit_null_optionals() -> None:
    frames = [
        (
            {"type": "extension_ui_request", "id": "x", "method": []},
            "unsupported_method",
        ),
        ({**_select_frame(), "optionDetails": None}, "invalid_option_details"),
        ({**_confirm_frame(), "timeout": True}, "invalid_timeout"),
        (
            {
                **_ask_frame(),
                "questions": [
                    {
                        "id": "q1",
                        "question": "Choose",
                        "options": [],
                        "recommended": None,
                    }
                ],
            },
            "invalid_questions",
        ),
    ]

    for frame, expected_code in frames:
        parsed = parse_ui_request(frame)
        assert isinstance(parsed, UiFrameError)
        assert parsed.code == expected_code


def test_response_builder_emits_only_observed_fields_and_reuses_request_id() -> None:
    select = parse_ui_request(_select_frame("external-id"))
    confirm = parse_ui_request(_confirm_frame("confirm-id"))
    ask = parse_ui_request(_ask_frame("ask-id"))
    assert isinstance(select, SelectRequest)
    assert isinstance(confirm, ConfirmRequest)
    assert isinstance(ask, AskRequest)

    assert build_ui_response(select, "careful") == {
        "type": "extension_ui_response",
        "id": "external-id",
        "value": "careful",
    }
    assert build_ui_response(confirm, True) == {
        "type": "extension_ui_response",
        "id": "confirm-id",
        "confirmed": True,
    }
    assert build_ui_response(ask, (AskAnswer("q1", ("blue",), "because"),)) == {
        "type": "extension_ui_response",
        "id": "ask-id",
        "answers": [
            {"id": "q1", "selectedOptions": ["blue"], "customInput": "because"}
        ],
    }


def test_open_delivers_once_and_duplicate_open_does_not_create_another_entry() -> None:
    registry = PendingRequestRegistry()
    identity, request, transport = _open_select(registry)

    duplicate = registry.open(identity, request, transport)

    assert duplicate is RequestStatus.DUPLICATE
    assert len(transport.requests) == 1
    assert registry.state(identity) is RequestStatus.PENDING
    assert len(registry) == 1


def test_invalid_answers_stay_pending_until_valid_answer_is_sent() -> None:
    registry = PendingRequestRegistry()
    identity, _, _ = _open_select(registry)
    written: list[dict[str, object]] = []

    assert (
        registry.submit(identity, "missing-option", written.append)
        is RequestStatus.INVALID
    )
    assert registry.state(identity) is RequestStatus.PENDING
    assert (
        registry.submit(identity, "careful", written.append) is RequestStatus.ANSWERED
    )
    assert registry.state(identity) is RequestStatus.ANSWERED
    assert written == [
        {"type": "extension_ui_response", "id": "select-1", "value": "careful"}
    ]


def test_confirm_requires_a_boolean_and_ask_requires_matching_offered_answers() -> None:
    registry = PendingRequestRegistry()
    confirm = parse_ui_request(_confirm_frame())
    ask = parse_ui_request(_ask_frame())
    assert isinstance(confirm, ConfirmRequest)
    assert isinstance(ask, AskRequest)
    confirm_identity = _identity(confirm.id)
    ask_identity = _identity(ask.id)
    assert (
        registry.open(confirm_identity, confirm, _Transport()) is RequestStatus.PENDING
    )
    assert registry.open(ask_identity, ask, _Transport()) is RequestStatus.PENDING
    writes: list[dict[str, object]] = []

    assert (
        registry.submit(confirm_identity, cast(Any, 1), writes.append)
        is RequestStatus.INVALID
    )
    assert registry.state(confirm_identity) is RequestStatus.PENDING
    assert (
        registry.submit(confirm_identity, False, writes.append)
        is RequestStatus.ANSWERED
    )
    assert (
        registry.submit(ask_identity, (AskAnswer("q1", ("red",)),), writes.append)
        is RequestStatus.INVALID
    )
    assert registry.state(ask_identity) is RequestStatus.PENDING
    assert (
        registry.submit(ask_identity, (AskAnswer("q1", ("blue",)),), writes.append)
        is RequestStatus.ANSWERED
    )
    assert writes == [
        {
            "type": "extension_ui_response",
            "id": "confirm-1",
            "confirmed": False,
        },
        {
            "type": "extension_ui_response",
            "id": "ask-1",
            "answers": [{"id": "q1", "selectedOptions": ["blue"]}],
        },
    ]


def test_invalidation_and_generation_changes_make_old_answer_stale() -> None:
    registry = PendingRequestRegistry()
    identity, request, _ = _open_select(registry)
    replacement = RequestIdentity(
        identity.job_id,
        new_generation(),
        new_generation(),
        identity.request_id,
    )
    writes: list[dict[str, object]] = []

    assert registry.invalidate(identity) is True
    assert registry.submit(identity, "fast", writes.append) is RequestStatus.STALE
    assert registry.open(replacement, request, _Transport()) is RequestStatus.PENDING
    assert (
        registry.submit(replacement, "careful", writes.append) is RequestStatus.ANSWERED
    )
    assert registry.state(identity) is RequestStatus.STALE
    assert writes == [
        {"type": "extension_ui_response", "id": "select-1", "value": "careful"}
    ]


def test_open_rejects_a_request_id_that_does_not_match_identity() -> None:
    registry = PendingRequestRegistry()
    request = parse_ui_request(_select_frame())
    assert isinstance(request, SelectRequest)
    identity = RequestIdentity("job-a", "session-a", "turn-a", "different-id")

    assert registry.open(identity, request, _Transport()) is RequestStatus.INVALID
    assert len(registry) == 0


def test_same_request_id_with_different_identity_is_rejected() -> None:
    registry = PendingRequestRegistry()
    identity, _, _ = _open_select(registry)
    wrong_identity = RequestIdentity(
        "other-job", "session-a", "turn-a", identity.request_id
    )
    unknown_identity = RequestIdentity("unknown-job", "s", "t", "unknown-request")

    assert (
        registry.submit(wrong_identity, "fast", lambda _: None)
        is RequestStatus.WRONG_IDENTITY
    )
    assert (
        registry.submit(unknown_identity, "fast", lambda _: None)
        is RequestStatus.UNKNOWN
    )
    assert registry.state(identity) is RequestStatus.PENDING


def test_missing_or_undeliverable_operator_transport_is_terminal() -> None:
    registry = PendingRequestRegistry()
    parsed = parse_ui_request(_select_frame())
    assert isinstance(parsed, SelectRequest)
    identity = _identity(parsed.id)
    writes: list[dict[str, object]] = []

    assert registry.open(identity, parsed, None) is RequestStatus.UNDELIVERABLE
    assert registry.state(identity) is RequestStatus.UNDELIVERABLE
    assert (
        registry.submit(identity, "fast", writes.append) is RequestStatus.UNDELIVERABLE
    )
    assert writes == []

    another = RequestIdentity("job-b", "session-b", "turn-b", "select-2")
    request2 = parse_ui_request(_select_frame("select-2"))
    assert isinstance(request2, SelectRequest)
    failed_transport = _Transport(DeliveryStatus.UNDELIVERABLE)
    assert (
        registry.open(another, request2, failed_transport)
        is RequestStatus.UNDELIVERABLE
    )
    assert registry.state(another) is RequestStatus.UNDELIVERABLE


def test_send_reservation_wins_invalidation_race_and_duplicate_submit() -> None:
    registry = PendingRequestRegistry()
    identity, _, _ = _open_select(registry)
    entered_sender = threading.Barrier(2)
    finish_sender = threading.Barrier(2)
    writes: list[dict[str, object]] = []
    submitted: list[RequestStatus] = []

    def sender(frame: dict[str, object]) -> None:
        writes.append(frame)
        entered_sender.wait()
        finish_sender.wait()

    worker = threading.Thread(
        target=lambda: submitted.append(registry.submit(identity, "fast", sender))
    )
    worker.start()
    entered_sender.wait()

    assert registry.state(identity) is RequestStatus.SENDING
    assert registry.invalidate(identity) is False
    assert (
        registry.submit(identity, "careful", writes.append) is RequestStatus.DUPLICATE
    )
    finish_sender.wait()
    worker.join()

    assert submitted == [RequestStatus.ANSWERED]
    assert registry.state(identity) is RequestStatus.ANSWERED
    assert writes == [
        {"type": "extension_ui_response", "id": "select-1", "value": "fast"}
    ]


def test_send_failure_is_terminal_and_never_retried() -> None:
    registry = PendingRequestRegistry()
    identity, _, _ = _open_select(registry)
    calls = 0

    def failed_send(_: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        raise OSError("closed RPC stream")

    assert registry.submit(identity, "fast", failed_send) is RequestStatus.SEND_FAILED
    assert registry.state(identity) is RequestStatus.SEND_FAILED
    assert registry.submit(identity, "careful", failed_send) is RequestStatus.DUPLICATE
    assert calls == 1


def test_generation_identifiers_are_fresh_and_non_counter_based() -> None:
    generations = {new_generation() for _ in range(16)}

    assert len(generations) == 16
