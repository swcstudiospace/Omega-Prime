"""Head-aware compacted pull-request projection for Pulsar Functions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from omega_prime.eventstream.envelope import CloudEvent
from omega_prime.eventstream.taxonomy import validate_payload

_PR_TYPES = frozenset(
    {
        "omega.sdlc.pr.opened",
        "omega.sdlc.pr.pushed",
        "omega.sdlc.pr.ready",
        "omega.sdlc.pr.converted_to_draft",
        "omega.sdlc.pr.merged",
        "omega.sdlc.pr.closed",
    }
)
_REVIEW_TYPES = frozenset(
    {
        "omega.sdlc.review.greptile_scored",
        "omega.sdlc.review.approved",
        "omega.sdlc.review.changes_requested",
        "omega.sdlc.review.commented",
    }
)
_CI_TYPES = frozenset(
    {"omega.ci.run.started", "omega.ci.run.passed", "omega.ci.run.failed"}
)
_SUPPORTED_TYPES = _PR_TYPES | _REVIEW_TYPES | _CI_TYPES
_STATE_VERSION = 1


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"PR projection requires nonempty {name}")
    return value


def _identity(data: Mapping[str, Any]) -> tuple[str, int, str]:
    repo = _required_string(data.get("repo"), "repo")
    number = data.get("number")
    head = _required_string(data.get("head_sha"), "head_sha")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ValueError("PR projection requires positive integer number")
    return repo, number, head


def _position(value: object) -> tuple[int, int, int] | None:
    if value is None:
        return None
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 3
        or any(
            isinstance(part, bool) or not isinstance(part, int) or part < 0
            for part in value
        )
    ):
        raise ValueError(
            "source_position must be a nonnegative (partition, ledger, entry) tuple"
        )
    return value[0], value[1], value[2]


def _initial(repo: str, number: int, head: str) -> dict[str, Any]:
    return {
        "version": _STATE_VERSION,
        "repo": repo,
        "number": number,
        "head_sha": head,
        "status": "open",
        "draft": False,
        "review": {},
        "ci": {},
        "closed": False,
        "merged": False,
        "source_positions": {},
    }


def _state_copy(previous: Mapping[str, Any] | None) -> dict[str, Any]:
    if previous is None:
        return {}
    try:
        # Copy through canonical JSON to detach mutable nested state and ensure it is
        # exactly the durable JSON shape consumed by the Function runtime.
        encoded = json.dumps(previous, sort_keys=True, separators=(",", ":"))
        result = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "previous PR projection state must be JSON-compatible"
        ) from exc
    if not isinstance(result, dict):
        raise ValueError("previous PR projection state must be an object")
    return result


def reduce_pr_state(
    previous: Mapping[str, Any] | None,
    event: CloudEvent | Mapping[str, Any],
    *,
    source: str | None = None,
    source_position: tuple[int, int, int] | None = None,
) -> dict[str, Any]:
    """Reduce one catalogued event into explicit durable projection state.

    Source positions are compared only within the same source topic and partition;
    no ordering is inferred across topics. The returned state is independent of
    arrival time and input mapping identity.
    """
    if isinstance(event, CloudEvent):
        event_type = event.type
        data: Mapping[str, Any] | Any = event.data
    else:
        raw_type = event.get("type")
        raw_data = event.get("data")
        if not isinstance(raw_type, str):
            raise ValueError("unsupported PR projection event type")
        event_type = raw_type
        data = raw_data
    if not isinstance(event_type, str) or event_type not in _SUPPORTED_TYPES:
        raise ValueError("unsupported PR projection event type")
    if not isinstance(data, Mapping):
        raise ValueError("PR projection event data must be an object")
    data = validate_payload(event_type, data)
    repo, number, head = _identity(data)
    state = _state_copy(previous)
    if state and (state.get("repo"), state.get("number")) != (repo, number):
        raise ValueError("previous PR projection identity does not match event")
    position = _position(source_position)
    positions = state.get("source_positions", {})
    if not isinstance(positions, dict):
        raise ValueError("source_positions state must be an object")
    source_name = source or (
        "sdlc/pr"
        if event_type in _PR_TYPES
        else "sdlc/review"
        if event_type in _REVIEW_TYPES
        else "sdlc/ci"
    )
    source_key = f"{source_name}#{position[0]}" if position is not None else source_name
    if position is not None:
        prior_position = positions.get(source_key)
        if prior_position is not None:
            prior = _position(prior_position)
            if prior is None or prior[0] != position[0] or position[1:] <= prior[1:]:
                return state

    if not state:
        state = _initial(repo, number, head)
        positions = state["source_positions"]
    elif (state.get("repo"), state.get("number")) != (repo, number):
        raise ValueError("previous PR projection identity does not match event")

    current_head = state.get("head_sha")
    if not isinstance(current_head, str) or not current_head:
        raise ValueError("previous PR projection has invalid head_sha")

    def _apply_review_or_ci(
        target: dict[str, Any], etype: str, payload: Mapping[str, Any]
    ) -> None:
        if target.get("closed"):
            return
        if etype in _REVIEW_TYPES:
            review = dict(target.get("review", {}))
            review[etype.rsplit(".", 1)[-1]] = {
                key: payload[key]
                for key in sorted(payload)
                if key not in {"repo", "number", "head_sha", "actor"}
            }
            target["review"] = review
            return
        ci = dict(target.get("ci", {}))
        run_id = payload.get("run_id")
        if isinstance(run_id, str) and run_id:
            ci[run_id] = {
                "status": (
                    "in_progress"
                    if etype == "omega.ci.run.started"
                    else "passed"
                    if etype.endswith(".passed")
                    else "failed"
                ),
                "event_type": etype,
                "conclusion": payload.get("conclusion"),
            }
            target["ci"] = ci

    if event_type in _PR_TYPES:
        if event_type == "omega.sdlc.pr.pushed" and head != current_head:
            if (
                not state.get("closed")
                and not state.get("merged")
                and data.get("before_sha") == current_head
            ):
                # Only an active PR advances, and only when the push follows its
                # known head, rejecting stale/conflicting branch transitions.
                state["head_sha"] = head
                state["review"] = {}
                state["ci"] = {}
                if not state.get("closed"):
                    state["status"] = "open"
                # Apply reviews/CI that arrived for the new head before this push.
                pending = state.get("pending_by_head", {})
                if isinstance(pending, dict):
                    pending = dict(pending)
                    buffered = pending.pop(head, [])
                    state["pending_by_head"] = pending
                    if isinstance(buffered, list):
                        for item in buffered:
                            if not isinstance(item, Mapping):
                                continue
                            etype = item.get("type")
                            payload = item.get("data")
                            if isinstance(etype, str) and isinstance(payload, Mapping):
                                _apply_review_or_ci(state, etype, payload)
            # A mismatch is a stale or conflicting branch transition.
        elif head != current_head:
            # Other historical lifecycle messages (and terminal events for an old
            # commit) cannot move the aggregate backward.
            pass
        elif event_type == "omega.sdlc.pr.opened":
            if not state.get("closed") and not state.get("merged"):
                state.update(status="open", draft=False)
        elif not state.get("closed") and event_type == "omega.sdlc.pr.ready":
            state["draft"] = False
        elif (
            not state.get("closed") and event_type == "omega.sdlc.pr.converted_to_draft"
        ):
            state["draft"] = True
        elif event_type == "omega.sdlc.pr.merged":
            state.update(status="merged", closed=True, merged=True)
        elif event_type == "omega.sdlc.pr.closed":
            if data.get("merged") is True:
                state.update(status="merged", closed=True, merged=True)
            elif not state.get("merged"):
                state.update(status="closed", closed=True)
    elif head == current_head and not state.get("closed"):
        _apply_review_or_ci(state, event_type, data)
    elif (
        head != current_head and not state.get("closed") and event_type not in _PR_TYPES
    ):
        # Buffer reviews/CI for a future head so an early arrival is not lost
        # when source_positions would otherwise mark it already seen.
        pending = state.get("pending_by_head", {})
        pending = {} if not isinstance(pending, dict) else dict(pending)
        bucket = pending.get(head, [])
        bucket = [] if not isinstance(bucket, list) else list(bucket)
        bucket.append({"type": event_type, "data": dict(data)})
        pending[head] = bucket
        state["pending_by_head"] = pending
    if position is not None:
        positions[source_key] = list(position)
        state["source_positions"] = positions
    return state


def projection(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return deterministic compacted value, excluding transport bookkeeping."""
    repo = _required_string(state.get("repo"), "repo")
    number = state.get("number")
    head = _required_string(state.get("head_sha"), "head_sha")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ValueError("PR projection requires positive integer number")
    aggregate = f"{repo}#{number}"
    identity = hashlib.sha256(aggregate.encode("utf-8")).hexdigest()
    reviews = state.get("review", {})
    ci = state.get("ci", {})
    if not isinstance(reviews, Mapping) or not isinstance(ci, Mapping):
        raise ValueError("PR projection review and CI state must be objects")
    return {
        "id": identity,
        "key": aggregate,
        "repo": repo,
        "number": number,
        "head_sha": head,
        "status": state.get("status", "open"),
        "draft": bool(state.get("draft", False)),
        "closed": bool(state.get("closed", False)),
        "merged": bool(state.get("merged", False)),
        "review": {key: reviews[key] for key in sorted(reviews)},
        "ci": {key: ci[key] for key in sorted(ci)},
    }


def serialize_projection(state: Mapping[str, Any]) -> bytes:
    """Canonical JSON bytes for the compacted state output."""
    return json.dumps(
        projection(state), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _message_position(message_id: object) -> tuple[int, int, int] | None:
    if message_id is None:
        return None
    names = ("partition", "ledger_id", "entry_id")
    values: list[int] = []
    for name in names:
        getter = getattr(message_id, f"get_{name}", None)
        if not callable(getter):
            getter = getattr(message_id, name, None)
        if not callable(getter):
            return None
        value = getter()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        values.append(value)
    return values[0], values[1], values[2]


def process(input: bytes, context: Any) -> None:
    """Persist the reducer state, then publish its keyed compacted projection.

    Publishing waits for the broker acknowledgement. A crash after state
    persistence but before source acknowledgement is closed by deterministic
    re-emission when Pulsar redelivers the source record.
    """
    try:
        incoming = CloudEvent.from_bytes(input)
    except (TypeError, ValueError):
        return None
    if incoming.type not in _SUPPORTED_TYPES:
        return None
    repo, number, _ = _identity(incoming.data)
    aggregate = f"{repo}#{number}"
    get_state = getattr(context, "get_state", None)
    put_state = getattr(context, "put_state", None)
    if not callable(get_state) or not callable(put_state):
        raise TypeError(
            "PR state projector requires durable context get_state/put_state"
        )
    raw_state = get_state(aggregate)
    if raw_state is None:
        previous = None
    elif isinstance(raw_state, bytes):
        decoded = json.loads(raw_state.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("durable PR projector state must be a JSON object")
        previous = decoded
    elif isinstance(raw_state, Mapping):
        previous = dict(raw_state)
    else:
        raise ValueError("durable PR projector state has unsupported representation")
    topic_getter = getattr(context, "get_current_message_topic_name", None)
    topic = topic_getter() if callable(topic_getter) else None
    source = None
    if isinstance(topic, str) and topic:
        source = topic.removeprefix("persistent://omega/")
        suffix, separator, partition = source.rpartition("-partition-")
        if separator and partition.isdigit():
            source = suffix
    message_getter = getattr(context, "get_message_id", None)
    message_id = message_getter() if callable(message_getter) else None
    updated = reduce_pr_state(
        previous,
        incoming,
        source=source,
        source_position=_message_position(message_id),
    )
    durable = json.dumps(updated, sort_keys=True, separators=(",", ":")).encode("utf-8")
    put_state(aggregate, durable)
    from omega_prime.eventstream.functions import publish_keyed

    publish_keyed(
        context,
        "persistent://omega/sdlc/pr-state",
        serialize_projection(updated),
        aggregate,
    )
    return None


def is_source_message(event: CloudEvent) -> bool:
    """Reject Function output or unrelated events from a configured source."""
    return event.type in _SUPPORTED_TYPES


__all__ = [
    "is_source_message",
    "process",
    "projection",
    "reduce_pr_state",
    "serialize_projection",
]


class PrStateProjectorFunction:
    """Apache Pulsar Python Function class entry point."""

    def process(self, input: bytes, context: Any) -> None:
        return process(input, context)
