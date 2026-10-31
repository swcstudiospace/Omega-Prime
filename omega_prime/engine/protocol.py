# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""NDJSON frames for omp RPC mode.

Host commands are one JSON object per line. omp replies with events, also
one JSON object per line. This module names the subset Phase A1a speaks.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from omega_prime.credentials.redact import redact_text

#: omp 18.x is the protocol this driver was written against.
OMP_SUPPORTED_MAJOR = 18

COMMANDS = (
    "prompt",
    "steer",
    "abort",
    "set_model",
    "set_thinking_level",
    "set_event_filter",
    "get_session_stats",
)

STREAM_EVENTS = (
    "ready",
    "agent_start",
    "turn_start",
    "turn_end",
    "message_update",
    "message_end",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "agent_end",
)

EVENT_FILTER = [*STREAM_EVENTS, "response"]

UI_EVENT = "extension_ui_request"

THINKING_LEVELS = frozenset({"off", "minimal", "low", "medium", "high", "xhigh", "max"})

MODES = frozenset({"rpc", "rpc-ui"})


def event_filter(mode: str) -> list[str]:
    """Return RPC's unchanged event filter, opting into UI requests for rpc-ui."""
    if mode == "rpc":
        return list(EVENT_FILTER)
    if mode == "rpc-ui":
        return [*EVENT_FILTER, UI_EVENT]
    raise ValueError("mode must be rpc or rpc-ui")


@dataclass(frozen=True, slots=True)
class UiFrameError:
    """Stable parse error for an unsupported or malformed extension UI frame."""

    code: str


@dataclass(frozen=True, slots=True)
class SelectOptionDetail:
    description: str | None = None


@dataclass(frozen=True, slots=True)
class AskOption:
    label: str
    description: str | None = None
    preview: str | None = None


@dataclass(frozen=True, slots=True)
class AskQuestion:
    id: str
    question: str
    options: tuple[AskOption, ...]
    header: str | None = None
    multi: bool = False
    recommended: int | None = None


@dataclass(frozen=True, slots=True)
class SelectRequest:
    id: str
    title: str
    options: tuple[str, ...]
    option_details: tuple[SelectOptionDetail, ...] | None = None
    method: Literal["select"] = "select"


@dataclass(frozen=True, slots=True)
class ConfirmRequest:
    id: str
    title: str
    message: str
    method: Literal["confirm"] = "confirm"


@dataclass(frozen=True, slots=True)
class AskRequest:
    id: str
    questions: tuple[AskQuestion, ...]
    method: Literal["ask"] = "ask"


UiRequest: TypeAlias = SelectRequest | ConfirmRequest | AskRequest


@dataclass(frozen=True, slots=True)
class AskAnswer:
    id: str
    selected_options: tuple[str, ...]
    custom_input: str | None = None


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _optional_text(value: Mapping[str, Any], key: str) -> tuple[str | None, str | None]:
    if key not in value:
        return None, None
    item = value[key]
    if not isinstance(item, str):
        return None, f"invalid_{key}"
    return item, None


def _parse_ask_questions(value: object) -> tuple[AskQuestion, ...] | None:
    if not isinstance(value, list):
        return None
    questions: list[AskQuestion] = []
    for item in value:
        if not isinstance(item, Mapping):
            return None
        question_id = item.get("id")
        prompt = item.get("question")
        options_value = item.get("options")
        if (
            not isinstance(question_id, str)
            or not isinstance(prompt, str)
            or not isinstance(options_value, list)
        ):
            return None
        header, error = _optional_text(item, "header")
        if error is not None:
            return None
        multi = item.get("multi", False)
        if not isinstance(multi, bool):
            return None
        if "recommended" in item:
            recommended = item["recommended"]
            if not _is_integer(recommended):
                return None
        else:
            recommended = None
        options: list[AskOption] = []
        for option in options_value:
            if not isinstance(option, Mapping) or not isinstance(
                option.get("label"), str
            ):
                return None
            description, error = _optional_text(option, "description")
            if error is not None:
                return None
            preview, error = _optional_text(option, "preview")
            if error is not None:
                return None
            options.append(AskOption(option["label"], description, preview))
        if recommended is not None and not 0 <= recommended < len(options):
            return None
        questions.append(
            AskQuestion(
                question_id,
                prompt,
                tuple(options),
                header,
                multi,
                recommended,
            )
        )
    return tuple(questions)


def parse_ui_request(frame: object) -> UiRequest | UiFrameError:
    """Parse only the observed ask/select/confirm request variants."""
    if not isinstance(frame, Mapping):
        return UiFrameError("not_object")
    if frame.get("type") != "extension_ui_request":
        return UiFrameError("wrong_type")
    request_id = frame.get("id")
    if not isinstance(request_id, str) or not request_id.strip():
        return UiFrameError("invalid_id")
    method = frame.get("method")
    if not isinstance(method, str) or method not in {"ask", "select", "confirm"}:
        return UiFrameError("unsupported_method")
    if "timeout" in frame and not _is_number(frame["timeout"]):
        return UiFrameError("invalid_timeout")

    if method == "select":
        title = frame.get("title")
        if not isinstance(title, str):
            return UiFrameError(
                "missing_title" if "title" not in frame else "invalid_title"
            )
        options_value = frame.get("options")
        if not isinstance(options_value, list) or not all(
            isinstance(option, str) for option in options_value
        ):
            return UiFrameError("invalid_options")
        if "optionDetails" in frame:
            details_value = frame["optionDetails"]
            if not isinstance(details_value, list):
                return UiFrameError("invalid_option_details")
        else:
            details_value = None
        option_details: tuple[SelectOptionDetail, ...] | None = None
        if details_value is not None:
            details: list[SelectOptionDetail] = []
            for detail in details_value:
                if not isinstance(detail, Mapping):
                    return UiFrameError("invalid_option_details")
                description, error = _optional_text(detail, "description")
                if error is not None:
                    return UiFrameError("invalid_option_details")
                details.append(SelectOptionDetail(description))
            option_details = tuple(details)
        return SelectRequest(request_id, title, tuple(options_value), option_details)

    if method == "confirm":
        title = frame.get("title")
        message = frame.get("message")
        if not isinstance(title, str):
            return UiFrameError(
                "missing_title" if "title" not in frame else "invalid_title"
            )
        if not isinstance(message, str):
            return UiFrameError(
                "missing_message" if "message" not in frame else "invalid_message"
            )
        return ConfirmRequest(request_id, title, message)

    if "questions" not in frame:
        return UiFrameError("missing_questions")
    questions = _parse_ask_questions(frame["questions"])
    if questions is None:
        return UiFrameError("invalid_questions")
    return AskRequest(request_id, questions)


def build_ui_response(request: UiRequest, answer: object) -> dict[str, Any]:
    """Build the source-defined response variant with the original request id."""
    if not isinstance(request, (SelectRequest, ConfirmRequest, AskRequest)):
        raise TypeError("unsupported UI request")
    response: dict[str, Any] = {
        "type": "extension_ui_response",
        "id": request.id,
    }
    if isinstance(request, SelectRequest):
        if not isinstance(answer, str):
            raise TypeError("select response value must be a string")
        response["value"] = answer
        return response
    if isinstance(request, ConfirmRequest):
        if not isinstance(answer, bool):
            raise TypeError("confirm response must be a boolean")
        response["confirmed"] = answer
        return response
    if isinstance(request, AskRequest):
        if not isinstance(answer, Sequence) or isinstance(answer, (str, bytes)):
            raise TypeError("ask response must be a sequence of AskAnswer values")
        answers: list[dict[str, Any]] = []
        for item in answer:
            if not isinstance(item, AskAnswer):
                raise TypeError("ask response must contain AskAnswer values")
            if (
                not isinstance(item.id, str)
                or not isinstance(item.selected_options, Sequence)
                or isinstance(item.selected_options, (str, bytes))
                or any(not isinstance(value, str) for value in item.selected_options)
                or (
                    item.custom_input is not None
                    and not isinstance(item.custom_input, str)
                )
            ):
                raise TypeError("ask response fields do not match the wire schema")
            wire_answer: dict[str, Any] = {
                "id": item.id,
                "selectedOptions": list(item.selected_options),
            }
            if item.custom_input is not None:
                wire_answer["customInput"] = item.custom_input
            answers.append(wire_answer)
        response["answers"] = answers
        return response
    raise TypeError("unsupported UI request")


_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

_SECRET_KEYS = frozenset(
    {
        "apikey",
        "api_key",
        "api-key",
        "authorization",
        "auth",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
    }
)


def parse_version(text: str) -> tuple[int, int, int] | None:
    """Return the first ``major.minor.patch`` in ``text``."""
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def dumps(payload: dict[str, Any]) -> str:
    """One NDJSON line, without a trailing newline."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def loads(line: str) -> dict[str, Any] | None:
    """Parse one frame. A non-object line becomes a notice, not an exception."""
    text = line.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"type": "notice", "text": text[:500]}
    if isinstance(payload, dict):
        return payload
    return {"type": "notice", "text": text[:500]}


def redact(value: Any) -> Any:
    """Copy ``value`` with secret-shaped fields replaced.

    Keys whose names are credentials are replaced. String values that look
    like a bearer token or a common key prefix are replaced. The replacement
    is a fixed label, never a partial secret.
    """
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key).lower().replace("-", "_")
            if name in _SECRET_KEYS or name.endswith(("_token", "_secret")):
                cleaned[str(key)] = "[redacted]"
            else:
                cleaned[str(key)] = redact(item)
        return cleaned
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def assistant_text(event: dict[str, Any]) -> str:
    """Visible assistant text from a message frame."""
    message = event.get("message")
    content = (
        message.get("content") if isinstance(message, dict) else event.get("content")
    )
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    delta = event.get("delta")
    if isinstance(delta, str):
        return delta
    if isinstance(delta, dict):
        for key in ("text", "delta", "content"):
            item = delta.get(key)
            if isinstance(item, str):
                return item
    text = event.get("text")
    return text if isinstance(text, str) else ""


def usage_of(event: dict[str, Any]) -> tuple[dict[str, Any], float | None]:
    """Usage object and cost from a ``message_end`` frame."""
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    usage = event.get("usage")
    if not isinstance(usage, dict):
        usage = message.get("usage") if isinstance(message, dict) else None
    cost = event.get("cost")
    if cost is None and isinstance(message, dict):
        cost = message.get("cost")
    if not isinstance(usage, dict):
        usage = {}
    numeric = (
        float(cost)
        if isinstance(cost, (int, float)) and not isinstance(cost, bool)
        else None
    )
    return dict(usage), numeric
