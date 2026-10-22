from __future__ import annotations

import json
from importlib import resources
from types import MappingProxyType

import pytest
from pydantic import ValidationError

from omega_prime.eventstream.taxonomy import (
    EVENT_TYPES,
    EventType,
    derive_key,
    get_event_type,
    resolve_schema_uri,
    schema_uri_for_type,
    validate_payload,
)

EXPECTED_TYPES = {
    *(
        f"omega.sdlc.pr.{suffix}"
        for suffix in (
            "opened",
            "pushed",
            "ready",
            "converted_to_draft",
            "merged",
            "closed",
        )
    ),
    *(
        f"omega.sdlc.review.{suffix}"
        for suffix in ("greptile_scored", "approved", "changes_requested", "commented")
    ),
    "omega.ci.run.started",
    "omega.ci.run.passed",
    "omega.ci.run.failed",
    "omega.ci.branch.passed",
    "omega.ci.branch.failed",
    *(
        f"omega.auth.session.{suffix}"
        for suffix in (
            "login_started",
            "login_succeeded",
            "login_failed",
            "expired",
            "logout",
        )
    ),
    *(
        f"omega.job.{suffix}"
        for suffix in ("queued", "started", "succeeded", "failed", "cancelled")
    ),
    *(
        f"omega.sandbox.{suffix}"
        for suffix in (
            "created",
            "started",
            "ready",
            "unhealthy",
            "oom",
            "exited",
            "removed",
        )
    ),
    "omega.task.state_changed",
    "omega.task.heartbeat",
    "omega.mem.hindsight.retained",
    "omega.mem.hindsight.teach_failed",
    *(f"omega.control.signal.{suffix}" for suffix in ("gate", "blocked", "review")),
    *(
        f"omega.control.action.{suffix}"
        for suffix in ("merge_requested", "merged", "merge_refused", "fix_requested")
    ),
    *(
        f"omega.control.deadline.{suffix}"
        for suffix in ("greptile_review_due", "heartbeat_due", "hitl_followup_due")
    ),
    "omega.hitl.asked",
    "omega.hitl.answered",
}


def test_registry_has_exact_immutable_concrete_catalog() -> None:
    assert len(EVENT_TYPES) == 48
    assert set(EVENT_TYPES) == EXPECTED_TYPES
    assert isinstance(EVENT_TYPES, MappingProxyType)
    with pytest.raises(TypeError):
        EVENT_TYPES["omega.audit.recorded"] = EventType("x", "x", "x", "x")  # type: ignore[index]


def test_event_type_topics_and_schema_urns_are_concrete_and_stable() -> None:
    pr = get_event_type("omega.sdlc.pr.opened")
    assert (pr.topic, pr.key_rule, pr.schema_ref) == (
        "persistent://omega/sdlc/pr",
        "repo#pr",
        "urn:omega:schema:omega.sdlc.pr.opened:1",
    )
    assert (
        get_event_type("omega.task.state_changed").topic
        == "persistent://omega/task/state"
    )
    assert (
        get_event_type("omega.task.heartbeat").topic
        == "persistent://omega/task/heartbeat"
    )
    assert get_event_type("omega.ci.run.passed").key_rule == "repo#pr"
    assert get_event_type("omega.ci.branch.passed").key_rule == "repo@branch"
    assert (
        schema_uri_for_type("omega.control.action.merged")
        == "urn:omega:schema:omega.control.action.merged:1"
    )
    audit = get_event_type("omega.audit.recorded")
    assert (audit.type, audit.topic, audit.key_rule, audit.schema_ref) == (
        "omega.audit.recorded",
        "persistent://omega/audit/log",
        "aggregate",
        "urn:omega:schema:omega.audit.recorded:1",
    )


def test_all_entries_map_to_expected_family_topics_and_key_rules() -> None:
    for name, event_type in EVENT_TYPES.items():
        if name.startswith("omega.sdlc.pr."):
            family, rule = "sdlc/pr", "repo#pr"
        elif name.startswith("omega.sdlc.review."):
            family, rule = "sdlc/review", "repo#pr"
        elif name.startswith("omega.ci.run."):
            family, rule = "sdlc/ci", "repo#pr"
        elif name.startswith("omega.ci.branch."):
            family, rule = "sdlc/ci", "repo@branch"
        elif name.startswith("omega.auth.session."):
            family, rule = "auth/session", "host:tool"
        elif name.startswith("omega.job."):
            family, rule = "job/lifecycle", "job_id"
        elif name.startswith("omega.sandbox."):
            family, rule = "sandbox/lifecycle", "sandbox_id"
        elif name == "omega.task.state_changed":
            family, rule = "task/state", "task_id"
        elif name == "omega.task.heartbeat":
            family, rule = "task/heartbeat", "task_id"
        elif name.startswith("omega.mem.hindsight."):
            family, rule = "memory/hindsight", "bank"
        elif name.startswith("omega.control.signal."):
            family = "control/signal"
            rule = "blocked_branch" if name.endswith(".blocked") else "repo#pr"
        elif name.startswith("omega.control.action."):
            family, rule = "control/action", "repo#pr"
        elif name.startswith("omega.control.deadline."):
            family, rule = "control/deadline", "aggregate"
        else:
            family, rule = "control/hitl", "task_id"
        assert event_type.topic == f"persistent://omega/{family}"
        assert event_type.key_rule == rule
        assert event_type.schema_ref == f"urn:omega:schema:{name}:1"


def test_unknown_non_audit_types_and_literal_audit_wildcard_reject() -> None:
    for type_name in (
        "omega.not.real",
        "omega.deploy.succeeded",
        "omega.audit.*",
        "omega.audit.",
        "omega.audit.BadSuffix",
    ):
        with pytest.raises(ValueError):
            get_event_type(type_name)


def test_unknown_type_and_schema_errors_do_not_echo_untrusted_values() -> None:
    secret = "ghp_SUPER_SECRET_TOKEN_0123456789"
    with pytest.raises(ValueError) as type_error:
        get_event_type(secret)
    assert secret not in str(type_error.value)
    for uri in (f"https://example.invalid/{secret}", f"urn:omega:schema:{secret}:1"):
        with pytest.raises(ValueError) as schema_error:
            resolve_schema_uri(uri)
        assert secret not in str(schema_error.value)


def test_payload_identity_keys_and_delimiter_unicode_values() -> None:
    assert (
        derive_key(
            "omega.sdlc.pr.opened",
            {
                "repo": 'space/#"🚀',
                "number": 9,
                "head_sha": "abc",
                "actor": "A",
                "title": "T",
                "base_ref": "main",
            },
        )
        == 'space/#"🚀#9'
    )
    assert (
        derive_key(
            "omega.ci.branch.passed",
            {
                "repo": "a@b",
                "branch": "feat/@/雪",
                "run_id": "run",
                "head_sha": "sha",
                "actor": "ci",
                "conclusion": "success",
            },
        )
        == "a@b@feat/@/雪"
    )
    assert (
        derive_key(
            "omega.auth.session.login_started",
            {
                "host": "host:one",
                "tool": "cli:thing",
                "session_id": "s",
                "actor": "u",
                "action": "login",
            },
        )
        == "host:one:cli:thing"
    )


def test_aggregate_key_rules_cover_lifecycle_memory_control_and_hitl() -> None:
    assert (
        derive_key(
            "omega.job.queued",
            {
                "job_id": "job/42",
                "actor": "worker",
                "state": "queued",
                "queue": "default",
            },
        )
        == "job/42"
    )
    assert (
        derive_key(
            "omega.sandbox.created",
            {
                "sandbox_id": "sb-1",
                "actor": "runner",
                "state": "created",
                "image": "python:3",
            },
        )
        == "sb-1"
    )
    task = {
        "task_id": "task-7",
        "previous_state": "queued",
        "state": "running",
        "sequence": 2,
        "actor": "scheduler",
    }
    heartbeat = {
        "task_id": "task-7",
        "sequence": 3,
        "occurred_at": "2026-10-23T12:00:00Z",
    }
    assert (
        derive_key("omega.task.state_changed", task)
        == derive_key("omega.task.heartbeat", heartbeat)
        == "task-7"
    )
    assert (
        derive_key(
            "omega.mem.hindsight.retained",
            {"bank": "work", "record_id": "mem-1", "text": "fact", "source": "task"},
        )
        == "work"
    )
    assert (
        derive_key(
            "omega.control.deadline.heartbeat_due",
            {
                "aggregate": "task:task-7",
                "deadline_id": "due-1",
                "due_at": "2026-10-23T12:01:00Z",
                "sequence": 3,
            },
        )
        == "task:task-7"
    )
    assert (
        derive_key(
            "omega.hitl.asked",
            {"task_id": "task-7", "request_id": "request-1", "question": "Approve?"},
        )
        == "task-7"
    )


def test_unknown_extra_fields_and_invalid_identity_reject() -> None:
    with pytest.raises(ValidationError):
        validate_payload(
            "omega.sdlc.pr.opened",
            {
                "repo": "r",
                "number": 1,
                "head_sha": "h",
                "actor": "a",
                "title": "x",
                "base_ref": "main",
                "extra": True,
            },
        )
    with pytest.raises(ValidationError):
        validate_payload(
            "omega.sdlc.pr.opened",
            {
                "repo": "r",
                "number": 0,
                "head_sha": "h",
                "actor": "a",
                "title": "x",
                "base_ref": "main",
            },
        )


def test_validation_errors_hide_untrusted_payload_values() -> None:
    secret = "ghp_SUPER_SECRET_TOKEN_0123456789"
    payload = {
        "repo": "r",
        "number": 1,
        "head_sha": "h",
        "actor": "a",
        "title": "x",
        "base_ref": "main",
        "extra": secret,
    }
    with pytest.raises(ValidationError) as exc_info:
        validate_payload("omega.sdlc.pr.opened", payload)
    assert secret not in str(exc_info.value)


def test_integral_json_numbers_normalize_without_coercing_other_values() -> None:
    payload = {
        "sandbox_id": "sb-1",
        "actor": "runner",
        "state": "oom",
        "memory_limit_bytes": 536870912.0,
    }
    normalized = validate_payload("omega.sandbox.oom", payload)
    assert normalized["memory_limit_bytes"] == 536870912
    assert type(normalized["memory_limit_bytes"]) is int
    for invalid_value in (536870912.5, True):
        with pytest.raises(ValidationError):
            validate_payload(
                "omega.sandbox.oom", {**payload, "memory_limit_bytes": invalid_value}
            )


def test_timestamp_payload_fields_require_valid_zoned_rfc3339() -> None:
    invalid_payloads = (
        (
            "omega.task.heartbeat",
            {"task_id": "task-1", "sequence": 1, "occurred_at": "2026-10-23T12:00:00"},
        ),
        (
            "omega.control.deadline.heartbeat_due",
            {
                "aggregate": "task:task-1",
                "deadline_id": "due-1",
                "due_at": "2026-02-30T12:00:00Z",
                "sequence": 1,
            },
        ),
        (
            "omega.audit.merge",
            {
                "aggregate": "pr:r#1",
                "record_id": "ce-1",
                "action": "merge",
                "actor": "bot",
                "occurred_at": "not-a-timestamp",
            },
        ),
    )
    for event_type, payload in invalid_payloads:
        with pytest.raises(ValidationError):
            validate_payload(event_type, payload)


def test_open_audit_family_accepts_typed_payload_but_not_unknown_shape() -> None:
    valid = {
        "aggregate": "pr:r#2",
        "record_id": "ce-1",
        "action": "merge",
        "actor": "bot",
        "occurred_at": "2026-10-23T12:00:00Z",
        "details": {"count": 2.0},
    }
    normalized = validate_payload("omega.audit.merge", valid)
    assert normalized["details"]["count"] == 2
    assert type(normalized["details"]["count"]) is int
    assert derive_key("omega.audit.merge", valid) == "pr:r#2"
    with pytest.raises(ValidationError):
        validate_payload("omega.audit.merge", {**valid, "arbitrary": "value"})
    with pytest.raises(ValueError):
        schema_uri_for_type("omega.audit.*")


def test_blocked_pr_and_dlq_branches_preserve_distinct_identity() -> None:
    pr = {
        "kind": "pr",
        "repo": "org/repo",
        "number": 12,
        "head_sha": "abc",
        "actor": "bot",
        "reason": "gate failed",
    }
    assert derive_key("omega.control.signal.blocked", pr) == "org/repo#12"

    origin_topic = "persistent://omega/auth/session"
    aggregate_key = 'host:"α",cli'  # noqa: RUF001 - Unicode key encoding is intentional.
    dlq = {
        "kind": "dlq",
        "origin_topic": origin_topic,
        "aggregate_key": aggregate_key,
        "source_ce_id": "01JCEID",
        "reason": "sanitized failure",
        "history": [{"attempt": 1, "reason": "temporary"}],
    }
    expected = "dlq:" + json.dumps(
        [origin_topic, aggregate_key], ensure_ascii=False, separators=(",", ":")
    )
    assert derive_key("omega.control.signal.blocked", dlq) == expected
    assert "repo" not in validate_payload("omega.control.signal.blocked", dlq)
    for invalid in (
        {**dlq, "repo": "synthetic", "number": 1},
        {**dlq, "history": []},
        {**dlq, "history": [{"attempt": 0, "reason": "bad"}]},
        {**dlq, "kind": "other"},
        {key: value for key, value in dlq.items() if key != "source_ce_id"},
    ):
        with pytest.raises(ValidationError):
            validate_payload("omega.control.signal.blocked", invalid)
    with pytest.raises(ValidationError):
        validate_payload(
            "omega.control.signal.blocked", {**pr, "origin_topic": origin_topic}
        )


def test_schema_resolution_reads_only_the_installed_package_bundle() -> None:
    definition = resolve_schema_uri(schema_uri_for_type("omega.sdlc.pr.opened"))
    expected_bundle = json.loads(
        resources.files("omega_prime.eventstream")
        .joinpath("schemas", "sdlc.pr.v1.json")
        .read_text(encoding="utf-8")
    )
    assert definition == expected_bundle["$defs"]["omega.sdlc.pr.opened"]
    audit_definition = json.loads(
        resources.files("omega_prime.eventstream")
        .joinpath("schemas", "audit.v1.json")
        .read_text(encoding="utf-8")
    )["$defs"]["omega.audit.*"]
    concrete_audit_uri = schema_uri_for_type("omega.audit.recorded")
    assert concrete_audit_uri == "urn:omega:schema:omega.audit.recorded:1"
    assert resolve_schema_uri(concrete_audit_uri) == audit_definition
    assert resolve_schema_uri("urn:omega:schema:omega.audit.*:1") == audit_definition
    with pytest.raises(ValueError):
        resolve_schema_uri("https://example.invalid/schema.json")
    with pytest.raises(ValueError):
        resolve_schema_uri("urn:omega:schema:omega.missing.event:1")


def test_payload_validation_accepts_recursively_immutable_mappings() -> None:
    payload = MappingProxyType(
        {
            "kind": "dlq",
            "origin_topic": "persistent://omega/task/state",
            "aggregate_key": "task-7",
            "source_ce_id": "task-event-1",
            "reason": "invalid transition",
            "history": (MappingProxyType({"attempt": 1, "reason": "retry"}),),
        }
    )
    validated = validate_payload("omega.control.signal.blocked", payload)
    assert validated["history"] == [{"attempt": 1, "reason": "retry"}]
