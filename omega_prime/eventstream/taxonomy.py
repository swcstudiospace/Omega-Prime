"""Immutable event catalog, payload contracts, and local schema resolution."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from types import MappingProxyType
from typing import Annotated, Any, Literal, TypeAlias, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    TypeAdapter,
)


def _normalize_integral_json_number(value: object) -> object:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


JsonInteger: TypeAlias = Annotated[
    StrictInt, BeforeValidator(_normalize_integral_json_number)
]

_RFC3339_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z"
)


def _validate_rfc3339_timestamp(value: object) -> object:
    if not isinstance(value, str):
        return value
    if not _RFC3339_PATTERN.fullmatch(value):
        raise ValueError("timestamp must be RFC3339 with a timezone")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("timestamp must be RFC3339 with a timezone") from exc
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must be RFC3339 with a timezone")
    return value


RFC3339Timestamp: TypeAlias = Annotated[
    StrictStr, BeforeValidator(_validate_rfc3339_timestamp)
]


@dataclass(frozen=True, slots=True)
class EventType:
    type: str
    topic: str
    key_rule: str
    schema_ref: str


@dataclass(frozen=True, slots=True)
class _CatalogEntry:
    event_type: EventType
    model: type[BaseModel] | TypeAdapter[Any]


class _Payload(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )


class _PullRequest(_Payload):
    repo: StrictStr = Field(min_length=1)
    number: JsonInteger = Field(ge=1)
    head_sha: StrictStr = Field(min_length=1)
    actor: StrictStr = Field(min_length=1)


class PrOpened(_PullRequest):
    title: StrictStr = Field(min_length=1)
    base_ref: StrictStr = Field(min_length=1)


class PrPushed(_PullRequest):
    before_sha: StrictStr = Field(min_length=1)


class PrReady(_PullRequest):
    draft: Literal[False]


class PrConvertedToDraft(_PullRequest):
    draft: Literal[True]


class PrMerged(_PullRequest):
    merged_by: StrictStr = Field(min_length=1)
    merge_sha: StrictStr = Field(min_length=1)


class PrClosed(_PullRequest):
    merged: bool
    closed_reason: StrictStr = Field(min_length=1)


class ReviewScored(_PullRequest):
    score: JsonInteger = Field(ge=0, le=100)


class ReviewApproved(_PullRequest):
    reviewer: StrictStr = Field(min_length=1)


class ReviewChangesRequested(_PullRequest):
    reviewer: StrictStr = Field(min_length=1)
    summary: StrictStr = Field(min_length=1)


class ReviewCommented(_PullRequest):
    reviewer: StrictStr = Field(min_length=1)
    comment_id: StrictStr = Field(min_length=1)


class _CiRun(_Payload):
    repo: StrictStr = Field(min_length=1)
    number: JsonInteger = Field(ge=1)
    run_id: StrictStr = Field(min_length=1)
    head_sha: StrictStr = Field(min_length=1)
    actor: StrictStr = Field(min_length=1)


class CiRunStarted(_CiRun):
    status: Literal["queued", "in_progress"]


class CiRunPassed(_CiRun):
    conclusion: Literal["success"]


class CiRunFailed(_CiRun):
    conclusion: Literal["failure", "cancelled", "timed_out"]


class _CiBranch(_Payload):
    repo: StrictStr = Field(min_length=1)
    branch: StrictStr = Field(min_length=1)
    run_id: StrictStr = Field(min_length=1)
    head_sha: StrictStr = Field(min_length=1)
    actor: StrictStr = Field(min_length=1)


class CiBranchPassed(_CiBranch):
    conclusion: Literal["success"]


class CiBranchFailed(_CiBranch):
    conclusion: Literal["failure", "cancelled", "timed_out"]


class _Auth(_Payload):
    host: StrictStr = Field(min_length=1)
    tool: StrictStr = Field(min_length=1)
    session_id: StrictStr = Field(min_length=1)
    actor: StrictStr = Field(min_length=1)


class AuthLoginStarted(_Auth):
    action: Literal["login"]


class AuthLoginSucceeded(_Auth):
    action: Literal["login"]
    method: StrictStr = Field(min_length=1)


class AuthLoginFailed(_Auth):
    action: Literal["login"]
    reason: StrictStr = Field(min_length=1)


class AuthExpired(_Auth):
    action: Literal["expired"]
    reason: StrictStr = Field(min_length=1)


class AuthLogout(_Auth):
    action: Literal["logout"]


class _Job(_Payload):
    job_id: StrictStr = Field(min_length=1)
    actor: StrictStr = Field(min_length=1)


class JobQueued(_Job):
    state: Literal["queued"]
    queue: StrictStr = Field(min_length=1)


class JobStarted(_Job):
    state: Literal["started"]
    worker: StrictStr = Field(min_length=1)


class JobSucceeded(_Job):
    state: Literal["succeeded"]
    result_ref: StrictStr = Field(min_length=1)


class JobFailed(_Job):
    state: Literal["failed"]
    reason: StrictStr = Field(min_length=1)


class JobCancelled(_Job):
    state: Literal["cancelled"]
    reason: StrictStr = Field(min_length=1)


class _Sandbox(_Payload):
    sandbox_id: StrictStr = Field(min_length=1)
    actor: StrictStr = Field(min_length=1)


class SandboxCreated(_Sandbox):
    state: Literal["created"]
    image: StrictStr = Field(min_length=1)


class SandboxStarted(_Sandbox):
    state: Literal["started"]
    container_id: StrictStr = Field(min_length=1)


class SandboxReady(_Sandbox):
    state: Literal["ready"]
    address: StrictStr = Field(min_length=1)


class SandboxUnhealthy(_Sandbox):
    state: Literal["unhealthy"]
    reason: StrictStr = Field(min_length=1)


class SandboxOom(_Sandbox):
    state: Literal["oom"]
    memory_limit_bytes: JsonInteger = Field(gt=0)


class SandboxExited(_Sandbox):
    state: Literal["exited"]
    exit_code: JsonInteger = Field(ge=0, le=255)


class SandboxRemoved(_Sandbox):
    state: Literal["removed"]
    reason: StrictStr = Field(min_length=1)


class TaskStateChanged(_Payload):
    task_id: StrictStr = Field(min_length=1)
    previous_state: StrictStr = Field(min_length=1)
    state: Literal["queued", "running", "blocked", "completed", "failed", "cancelled"]
    sequence: JsonInteger = Field(ge=0)
    actor: StrictStr = Field(min_length=1)


class TaskHeartbeat(_Payload):
    task_id: StrictStr = Field(min_length=1)
    sequence: JsonInteger = Field(ge=0)
    occurred_at: RFC3339Timestamp


class MemRetained(_Payload):
    bank: StrictStr = Field(min_length=1)
    record_id: StrictStr = Field(min_length=1)
    text: StrictStr = Field(min_length=1)
    source: StrictStr = Field(min_length=1)


class MemTeachFailed(_Payload):
    bank: StrictStr = Field(min_length=1)
    record_id: StrictStr = Field(min_length=1)
    reason: StrictStr = Field(min_length=1)


class ControlGate(_PullRequest):
    decision: Literal["allow", "block"]
    reason: StrictStr = Field(min_length=1)


class ControlBlockedPr(_PullRequest):
    kind: Literal["pr"]
    reason: StrictStr = Field(min_length=1)


class RetryRecord(_Payload):
    attempt: JsonInteger = Field(ge=1)
    reason: StrictStr = Field(min_length=1)


class ControlBlockedDlq(_Payload):
    kind: Literal["dlq"]
    origin_topic: StrictStr = Field(min_length=1)
    aggregate_key: StrictStr = Field(min_length=1)
    source_ce_id: StrictStr = Field(min_length=1)
    reason: StrictStr = Field(min_length=1)
    history: tuple[RetryRecord, ...] = Field(min_length=1, strict=False)


BlockedPayload: TypeAlias = Annotated[
    ControlBlockedPr | ControlBlockedDlq, Field(discriminator="kind")
]


class ControlReview(_PullRequest):
    review_state: Literal["pending", "approved", "changes_requested"]
    review_id: StrictStr = Field(min_length=1)


class ControlMergeRequested(_PullRequest):
    requested_by: StrictStr = Field(min_length=1)


class ControlMerged(_PullRequest):
    merged_by: StrictStr = Field(min_length=1)
    merge_sha: StrictStr = Field(min_length=1)


class ControlMergeRefused(_PullRequest):
    reason: StrictStr = Field(min_length=1)


class ControlFixRequested(_PullRequest):
    requested_by: StrictStr = Field(min_length=1)
    reason: StrictStr = Field(min_length=1)


class _Deadline(_Payload):
    aggregate: StrictStr = Field(min_length=1)
    deadline_id: StrictStr = Field(min_length=1)
    due_at: RFC3339Timestamp


class DeadlineGreptile(_Deadline):
    head_sha: StrictStr = Field(min_length=1)


class DeadlineHeartbeat(_Deadline):
    sequence: JsonInteger = Field(ge=0)


class DeadlineHitl(_Deadline):
    task_id: StrictStr = Field(min_length=1)
    sequence: JsonInteger = Field(ge=0)


class HitlAsked(_Payload):
    task_id: StrictStr = Field(min_length=1)
    request_id: StrictStr = Field(min_length=1)
    question: StrictStr = Field(min_length=1)


class HitlAnswered(_Payload):
    task_id: StrictStr = Field(min_length=1)
    request_id: StrictStr = Field(min_length=1)
    answer: StrictStr = Field(min_length=1)
    answered_by: StrictStr = Field(min_length=1)


class AuditPayload(_Payload):
    aggregate: StrictStr = Field(min_length=1)
    record_id: StrictStr = Field(min_length=1)
    action: StrictStr = Field(min_length=1)
    actor: StrictStr = Field(min_length=1)
    occurred_at: RFC3339Timestamp
    details: dict[StrictStr, StrictStr | JsonInteger | bool] = Field(
        default_factory=dict
    )


_CONCRETE: tuple[tuple[str, str, str, type[BaseModel] | TypeAdapter[Any]], ...] = (
    ("omega.sdlc.pr.opened", "sdlc/pr", "repo#pr", PrOpened),
    ("omega.sdlc.pr.pushed", "sdlc/pr", "repo#pr", PrPushed),
    ("omega.sdlc.pr.ready", "sdlc/pr", "repo#pr", PrReady),
    ("omega.sdlc.pr.converted_to_draft", "sdlc/pr", "repo#pr", PrConvertedToDraft),
    ("omega.sdlc.pr.merged", "sdlc/pr", "repo#pr", PrMerged),
    ("omega.sdlc.pr.closed", "sdlc/pr", "repo#pr", PrClosed),
    ("omega.sdlc.review.greptile_scored", "sdlc/review", "repo#pr", ReviewScored),
    ("omega.sdlc.review.approved", "sdlc/review", "repo#pr", ReviewApproved),
    (
        "omega.sdlc.review.changes_requested",
        "sdlc/review",
        "repo#pr",
        ReviewChangesRequested,
    ),
    ("omega.sdlc.review.commented", "sdlc/review", "repo#pr", ReviewCommented),
    ("omega.ci.run.started", "sdlc/ci", "repo#pr", CiRunStarted),
    ("omega.ci.run.passed", "sdlc/ci", "repo#pr", CiRunPassed),
    ("omega.ci.run.failed", "sdlc/ci", "repo#pr", CiRunFailed),
    ("omega.ci.branch.passed", "sdlc/ci", "repo@branch", CiBranchPassed),
    ("omega.ci.branch.failed", "sdlc/ci", "repo@branch", CiBranchFailed),
    ("omega.auth.session.login_started", "auth/session", "host:tool", AuthLoginStarted),
    (
        "omega.auth.session.login_succeeded",
        "auth/session",
        "host:tool",
        AuthLoginSucceeded,
    ),
    ("omega.auth.session.login_failed", "auth/session", "host:tool", AuthLoginFailed),
    ("omega.auth.session.expired", "auth/session", "host:tool", AuthExpired),
    ("omega.auth.session.logout", "auth/session", "host:tool", AuthLogout),
    ("omega.job.queued", "job/lifecycle", "job_id", JobQueued),
    ("omega.job.started", "job/lifecycle", "job_id", JobStarted),
    ("omega.job.succeeded", "job/lifecycle", "job_id", JobSucceeded),
    ("omega.job.failed", "job/lifecycle", "job_id", JobFailed),
    ("omega.job.cancelled", "job/lifecycle", "job_id", JobCancelled),
    ("omega.sandbox.created", "sandbox/lifecycle", "sandbox_id", SandboxCreated),
    ("omega.sandbox.started", "sandbox/lifecycle", "sandbox_id", SandboxStarted),
    ("omega.sandbox.ready", "sandbox/lifecycle", "sandbox_id", SandboxReady),
    ("omega.sandbox.unhealthy", "sandbox/lifecycle", "sandbox_id", SandboxUnhealthy),
    ("omega.sandbox.oom", "sandbox/lifecycle", "sandbox_id", SandboxOom),
    ("omega.sandbox.exited", "sandbox/lifecycle", "sandbox_id", SandboxExited),
    ("omega.sandbox.removed", "sandbox/lifecycle", "sandbox_id", SandboxRemoved),
    ("omega.task.state_changed", "task/state", "task_id", TaskStateChanged),
    ("omega.task.heartbeat", "task/heartbeat", "task_id", TaskHeartbeat),
    ("omega.mem.hindsight.retained", "memory/hindsight", "bank", MemRetained),
    ("omega.mem.hindsight.teach_failed", "memory/hindsight", "bank", MemTeachFailed),
    ("omega.control.signal.gate", "control/signal", "repo#pr", ControlGate),
    (
        "omega.control.signal.blocked",
        "control/signal",
        "blocked_branch",
        TypeAdapter(BlockedPayload),
    ),
    ("omega.control.signal.review", "control/signal", "repo#pr", ControlReview),
    (
        "omega.control.action.merge_requested",
        "control/action",
        "repo#pr",
        ControlMergeRequested,
    ),
    ("omega.control.action.merged", "control/action", "repo#pr", ControlMerged),
    (
        "omega.control.action.merge_refused",
        "control/action",
        "repo#pr",
        ControlMergeRefused,
    ),
    (
        "omega.control.action.fix_requested",
        "control/action",
        "repo#pr",
        ControlFixRequested,
    ),
    (
        "omega.control.deadline.greptile_review_due",
        "control/deadline",
        "aggregate",
        DeadlineGreptile,
    ),
    (
        "omega.control.deadline.heartbeat_due",
        "control/deadline",
        "aggregate",
        DeadlineHeartbeat,
    ),
    (
        "omega.control.deadline.hitl_followup_due",
        "control/deadline",
        "aggregate",
        DeadlineHitl,
    ),
    ("omega.hitl.asked", "control/hitl", "task_id", HitlAsked),
    ("omega.hitl.answered", "control/hitl", "task_id", HitlAnswered),
)


def _schema_ref(event_type: str) -> str:
    return f"urn:omega:schema:{event_type}:1"


_CATALOG: dict[str, _CatalogEntry] = {}
for _type_name, _topic_name, _key_rule, _model in _CONCRETE:
    _entry = EventType(
        _type_name,
        f"persistent://omega/{_topic_name}",
        _key_rule,
        _schema_ref(_type_name),
    )
    _CATALOG[_type_name] = _CatalogEntry(_entry, _model)
EVENT_TYPES: Mapping[str, EventType] = MappingProxyType(
    {name: item.event_type for name, item in _CATALOG.items()}
)
_AUDIT_TYPE = EventType(
    "omega.audit.*",
    "persistent://omega/audit/log",
    "aggregate",
    _schema_ref("omega.audit.*"),
)
_AUDIT_PATTERN = re.compile(r"omega\.audit\.[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_FAMILY_BY_TYPE = {
    **{
        name: "sdlc.pr"
        for name in tuple(EVENT_TYPES)
        if name.startswith("omega.sdlc.pr.")
    },
    **{
        name: "sdlc.review"
        for name in tuple(EVENT_TYPES)
        if name.startswith("omega.sdlc.review.")
    },
    **{name: "ci" for name in tuple(EVENT_TYPES) if name.startswith("omega.ci.")},
    **{name: "auth" for name in tuple(EVENT_TYPES) if name.startswith("omega.auth.")},
    **{name: "job" for name in tuple(EVENT_TYPES) if name.startswith("omega.job.")},
    **{
        name: "sandbox"
        for name in tuple(EVENT_TYPES)
        if name.startswith("omega.sandbox.")
    },
    **{name: "task" for name in tuple(EVENT_TYPES) if name.startswith("omega.task.")},
    **{name: "mem" for name in tuple(EVENT_TYPES) if name.startswith("omega.mem.")},
    **{
        name: "control"
        for name in tuple(EVENT_TYPES)
        if name.startswith("omega.control.")
    },
    **{name: "hitl" for name in tuple(EVENT_TYPES) if name.startswith("omega.hitl.")},
}


def get_event_type(type_name: str) -> EventType:
    """Return a concrete catalog entry or the explicit open audit family."""
    if type_name in EVENT_TYPES:
        return EVENT_TYPES[type_name]
    if _AUDIT_PATTERN.fullmatch(type_name):
        return EventType(
            type_name, _AUDIT_TYPE.topic, _AUDIT_TYPE.key_rule, _schema_ref(type_name)
        )
    raise ValueError("unknown event type")


def schema_uri_for_type(type_name: str) -> str:
    return get_event_type(type_name).schema_ref


def _resolve_family_bundle(family: str) -> Mapping[str, Any]:
    if family not in {
        "envelope",
        "sdlc.pr",
        "sdlc.review",
        "ci",
        "auth",
        "job",
        "sandbox",
        "task",
        "mem",
        "control",
        "hitl",
        "audit",
    }:
        raise ValueError("unknown schema family")
    resource = resources.files("omega_prime.eventstream").joinpath(
        "schemas", f"{family}.v1.json"
    )
    try:
        loaded: object = json.loads(resource.read_text(encoding="utf-8"))
    except (
        FileNotFoundError,
        ModuleNotFoundError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("schema bundle is unavailable") from exc
    if not isinstance(loaded, dict):
        raise ValueError("schema bundle is invalid")
    return cast(Mapping[str, Any], loaded)


def resolve_schema_uri(uri: str) -> Mapping[str, Any]:
    """Resolve the supported per-type schema URNs from installed package data only."""
    if (
        not isinstance(uri, str)
        or not uri.startswith("urn:omega:schema:")
        or not uri.endswith(":1")
    ):
        raise ValueError("unsupported schema URI")
    target = uri[len("urn:omega:schema:") : -2]
    if target == "omega.audit.*" or _AUDIT_PATTERN.fullmatch(target):
        family, definition = "audit", "omega.audit.*"
    else:
        get_event_type(target)
        schema_family = _FAMILY_BY_TYPE.get(target)
        if schema_family is None:
            raise ValueError("unknown schema URI")
        family = schema_family
        definition = target
    bundle = _resolve_family_bundle(family)
    try:
        result = bundle["$defs"][definition]
    except (KeyError, TypeError) as exc:
        raise ValueError("schema definition is unavailable") from exc
    if not isinstance(result, Mapping):
        raise ValueError("schema definition is invalid")
    return result


def _validate_audit(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    model = AuditPayload.model_validate(payload)
    return model.model_dump(mode="json")


def _to_plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return {key: _to_plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain_json(item) for item in value]
    return value


def validate_payload(type_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate a payload against its handwritten immutable Pydantic contract."""
    get_event_type(type_name)
    if not isinstance(payload, Mapping):
        raise TypeError("event payload must be a mapping")
    normalized = _to_plain_json(payload)
    if not isinstance(normalized, dict):
        raise TypeError("event payload must be a JSON object")
    if type_name not in EVENT_TYPES and _AUDIT_PATTERN.fullmatch(type_name):
        return _validate_audit(cast(Mapping[str, Any], normalized))
    model = _CATALOG[type_name].model
    validated = (
        model.validate_python(normalized)
        if isinstance(model, TypeAdapter)
        else model.model_validate(normalized)
    )
    if not isinstance(validated, BaseModel):
        raise TypeError("event payload model did not produce a mapping")
    return validated.model_dump(mode="json")


def derive_key(type_name: str, payload: Mapping[str, Any]) -> str:
    """Validate then derive the persistent routing key from aggregate identity."""
    validated = validate_payload(type_name, payload)
    event_type = get_event_type(type_name)
    rule = event_type.key_rule
    if rule == "repo#pr":
        return f"{validated['repo']}#{validated['number']}"
    if rule == "repo@branch":
        return f"{validated['repo']}@{validated['branch']}"
    if rule == "host:tool":
        return f"{validated['host']}:{validated['tool']}"
    if rule in {"job_id", "sandbox_id", "task_id", "bank", "aggregate"}:
        return str(validated[rule])
    if rule == "blocked_branch":
        if validated["kind"] == "pr":
            return f"{validated['repo']}#{validated['number']}"
        origin_topic = validated["origin_topic"]
        aggregate_key = validated["aggregate_key"]
        return "dlq:" + json.dumps(
            [origin_topic, aggregate_key], ensure_ascii=False, separators=(",", ":")
        )
    raise ValueError("unsupported key rule")
