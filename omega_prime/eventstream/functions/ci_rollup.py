"""Deterministic CI run rollup and Pulsar process adapter."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from omega_prime.eventstream.envelope import CloudEvent
from omega_prime.eventstream.taxonomy import validate_payload

_OUTPUT_TYPES = {
    "passed": "omega.ci.run.passed",
    "failed": "omega.ci.run.failed",
}
_TERMINAL_FAILURES = frozenset({"failure", "cancelled", "timed_out", "action_required"})
_TERMINAL_SUCCESSES = frozenset({"success", "neutral", "skipped"})


def _identity(value: Mapping[str, Any]) -> tuple[str, int, str, str]:
    repo, number, head, run = (
        value.get("repo"),
        value.get("number"),
        value.get("head_sha"),
        value.get("run_id"),
    )
    if (
        not isinstance(repo, str)
        or not repo.strip()
        or isinstance(number, bool)
        or not isinstance(number, int)
        or number < 1
        or not isinstance(head, str)
        or not head.strip()
        or not isinstance(run, str)
        or not run.strip()
    ):
        raise ValueError(
            "CI input requires repo, positive number, head_sha, and run_id"
        )
    return repo, number, head, run


def reduce_ci_run(
    state: Mapping[str, Any] | None,
    check: Mapping[str, Any],
    required_checks: Sequence[str],
) -> dict[str, Any]:
    """Merge one check into explicit state; emit a result only when complete."""
    repo, number, head, run = _identity(check)
    if isinstance(required_checks, str):
        raise ValueError("required_checks must contain nonempty names")
    required_values = tuple(required_checks)
    if not required_values or any(
        not isinstance(name, str) or not name.strip() for name in required_values
    ):
        raise ValueError("required_checks must contain nonempty names")
    required = tuple(sorted({name.strip() for name in required_values}))
    name, status, conclusion = (
        check.get("check"),
        check.get("status"),
        check.get("conclusion"),
    )
    if not isinstance(name, str) or not name.strip():
        raise ValueError("CI input requires a check name")
    name = name.strip()
    if name not in required:
        return dict(state or {})
    if status not in ("queued", "in_progress", "completed"):
        raise ValueError("CI check status is invalid")
    if status == "completed" and (
        not isinstance(conclusion, str)
        or conclusion not in _TERMINAL_FAILURES | _TERMINAL_SUCCESSES
    ):
        raise ValueError("completed CI check requires a terminal conclusion")
    if status != "completed" and conclusion not in (None, ""):
        raise ValueError("nonterminal CI check cannot have a conclusion")

    prior = dict(state or {})
    if prior and _identity(prior) != (repo, number, head, run):
        prior = {}
    checks = dict(prior.get("checks", {}))
    previous = checks.get(name)
    rank = {"queued": 0, "in_progress": 1, "completed": 2}
    # Terminal observations are immutable; reordered stale events cannot regress them.
    if previous is None or rank[status] > rank.get(previous.get("status"), -1):
        checks[name] = {"status": status, "conclusion": conclusion}
    elif previous.get("status") == "completed" and status == "completed":
        # Conflicting terminal duplicates resolve conservatively and independently of arrival order.
        outcomes = {previous.get("conclusion"), conclusion}
        failures = sorted(outcomes & _TERMINAL_FAILURES)
        checks[name] = {
            "status": "completed",
            "conclusion": failures[0] if failures else sorted(outcomes)[0],
        }

    result = {
        "repo": repo,
        "number": number,
        "head_sha": head,
        "run_id": run,
        "checks": checks,
    }
    completed = [checks.get(item, {}).get("status") == "completed" for item in required]
    result["result"] = None
    if all(completed):
        conclusions = [checks[item]["conclusion"] for item in required]
        result["result"] = (
            "failed"
            if any(item in _TERMINAL_FAILURES for item in conclusions)
            else "passed"
        )
    actors = (
        candidate
        for candidate in (check.get("actor"), prior.get("actor"))
        if isinstance(candidate, str) and candidate.strip()
    )
    stable_actor = min(actors, default=None)
    if stable_actor is not None:
        result["actor"] = stable_actor
    return result


def output_event(state: Mapping[str, Any]) -> tuple[CloudEvent, str] | None:
    """Return the validated, stable-key output for a completed run, if any."""
    result = state.get("result")
    if result not in _OUTPUT_TYPES:
        return None
    repo, number, head, run = _identity(state)
    if result == "passed":
        conclusion = "success"
    else:
        failures = (
            item.get("conclusion")
            for _, item in sorted(state["checks"].items())
            if isinstance(item, Mapping)
            and item.get("status") == "completed"
            and item.get("conclusion") in _TERMINAL_FAILURES
        )
        conclusion = next(
            (
                item
                for item in failures
                if item in {"failure", "cancelled", "timed_out"}
            ),
            "failure",
        )
    payload = {
        "repo": repo,
        "number": number,
        "run_id": run,
        "head_sha": head,
        "actor": str(state.get("actor") or "ci-rollup"),
        "conclusion": conclusion,
    }
    event_type = _OUTPUT_TYPES[str(result)]
    payload = dict(validate_payload(event_type, payload))
    digest = hashlib.sha256(
        f"{repo}\0{number}\0{head}\0{run}\0{result}".encode()
    ).hexdigest()[:32]
    event = CloudEvent(
        specversion="1.0",
        id=f"ci-{digest}",
        source="urn:omega:function:ci-rollup",
        type=event_type,
        datacontenttype="application/json",
        dataschema=f"urn:omega:schema:{event_type}:1",
        data=payload,
    )
    return event, f"{repo}#{number}"


def _check_list(value: object) -> tuple[str, ...] | None:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = [part.strip() for part in value.split(",") if part.strip()]
        value = decoded
    if not isinstance(value, (list, tuple)):
        return None
    if any(not isinstance(name, str) or not name.strip() for name in value):
        return None
    return tuple(value)


def _message_properties(context: Any) -> Mapping[str, Any]:
    getter = getattr(context, "get_message_properties", None)
    if not callable(getter):
        return {}
    properties = getter()
    return properties if isinstance(properties, Mapping) else {}


def _required_checks(
    context: Any, properties: Mapping[str, Any]
) -> tuple[str, ...] | None:
    getter = getattr(context, "get_user_config_value", None)
    configured = getter("required_checks") if callable(getter) else None
    if configured is None:
        configured = properties.get("required_checks")
    checks = _check_list(configured)
    return checks if checks else None


def _state_key(repo: str, number: int, head: str, run: str) -> str:
    digest = hashlib.sha256(f"{repo}\\0{number}\\0{head}\\0{run}".encode()).hexdigest()
    return f"ci-rollup:{digest}"


def _load_state(context: Any, key: str) -> Mapping[str, Any] | None:
    getter = getattr(context, "get_state", None)
    if not callable(getter):
        return None
    value = getter(key)
    if value is None:
        return None
    if isinstance(value, bytes):
        decoded = json.loads(value.decode("utf-8"))
    elif isinstance(value, str):
        decoded = json.loads(value)
    elif isinstance(value, Mapping):
        decoded = dict(value)
    else:
        raise ValueError("CI Function state must be JSON")
    if not isinstance(decoded, Mapping):
        raise ValueError("CI Function state must be an object")
    return decoded


def process(input: bytes, context: Any) -> None:
    """Aggregate configured check observations carried beside typed run events.

    ``check_name`` and ``required_checks`` are transport properties because the
    registered run payload deliberately models run-level identity only.
    """
    try:
        incoming = CloudEvent.from_bytes(input)
    except (TypeError, ValueError):
        return None
    if incoming.type not in {
        "omega.ci.run.started",
        "omega.ci.run.passed",
        "omega.ci.run.failed",
    } or not is_source_message(incoming):
        return None
    properties = _message_properties(context)
    required = _required_checks(context, properties)
    check_name = properties.get("check_name")
    if required is None or not isinstance(check_name, str) or not check_name.strip():
        return None
    check = dict(incoming.data)
    check["check"] = check_name
    if incoming.type == "omega.ci.run.started":
        status = check.get("status")
        check["conclusion"] = None
    else:
        status = "completed"
    check["status"] = status
    repo, number, head, run = _identity(check)
    state_key = _state_key(repo, number, head, run)
    state = reduce_ci_run(_load_state(context, state_key), check, required)
    setter = getattr(context, "put_state", None)
    if not callable(setter):
        raise TypeError("Pulsar Function context does not support durable state")
    setter(state_key, json.dumps(state, sort_keys=True, separators=(",", ":")).encode())
    result = output_event(state)
    if result is None:
        return None
    output, key = result
    from omega_prime.eventstream.functions import publish_keyed
    from omega_prime.eventstream.taxonomy import EVENT_TYPES

    publish_keyed(
        context,
        EVENT_TYPES[output.type].topic,
        output.to_bytes(),
        key,
    )
    return None


def is_source_message(event: CloudEvent) -> bool:
    """Reject only this Function's deterministic outputs from its own source."""
    return event.source != "urn:omega:function:ci-rollup"


class CiRollupFunction:
    """Apache Pulsar Python Function class entry point."""

    def process(self, input: bytes, context: Any) -> None:
        return process(input, context)
