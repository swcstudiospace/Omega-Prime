# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Admin approvals API over a real ApprovalLog (63-03)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.testclient import TestClient
from starlette.types import ASGIApp, Receive, Scope, Send

from omega_prime.grokbot.approvals_api import MAX_BODY_BYTES, approval_routes
from omega_prime.grokbot.security import Principal
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.registry import ToolRegistry

PRINCIPALS = {
    "alice": Principal(id="tok_alice", scopes=frozenset({"admin"}), label="alice"),
    "bob": Principal(id="tok_bob", scopes=frozenset({"call"}), label="bob"),
    "bot": Principal(
        id="tok_bot", scopes=frozenset({"admin"}), label="bot-00-omega-prime"
    ),
}
GATED = ["deploy", "publish"]


class _HeaderPrincipal:
    """Stand-in for AuthMiddleware: principal chosen by `x-test-user`."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            name = headers.get(b"x-test-user", b"").decode()
            principal = PRINCIPALS.get(name)
            if principal is not None:
                scope.setdefault("state", {})["principal"] = principal
        await self.app(scope, receive, send)


class _Audit:
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[dict[str, Any]] = []
        self.fail = fail

    def log_event(self, event: str, **kwargs: Any) -> dict[str, Any]:
        if self.fail:
            raise OSError("disk full")
        record = {"event": event, **kwargs}
        self.events.append(record)
        return record


class _Clock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


def _client(log: ApprovalLog, audit: _Audit | None = None, **kwargs: Any) -> TestClient:
    routes = approval_routes(
        approval_log=log, gated_tools=lambda: GATED, audit=audit, **kwargs
    )
    app = Starlette(routes=routes, middleware=[Middleware(_HeaderPrincipal)])
    return TestClient(app)


def _as(user: str) -> dict[str, str]:
    return {"x-test-user": user}


def test_auth_matrix_on_every_route() -> None:
    log = ApprovalLog()
    client = _client(log)
    calls = [
        ("get", "/admin/approvals", None),
        ("post", "/admin/approvals", {"tool": "deploy"}),
        ("delete", "/admin/approvals/deploy", None),
    ]
    for method, path, body in calls:
        kwargs: dict[str, Any] = {"json": body} if body is not None else {}
        none = getattr(client, method)(path, **kwargs)
        assert none.status_code == 401
        assert "error" in none.json()
        bob = getattr(client, method)(path, headers=_as("bob"), **kwargs)
        assert bob.status_code == 403
        assert "error" in bob.json()
    assert log.entries() == []
    admin = client.get("/admin/approvals", headers=_as("alice"))
    assert admin.status_code == 200


def test_grant_visible_in_list_and_audited() -> None:
    clock = _Clock()
    log = ApprovalLog(clock=clock)
    audit = _Audit()
    client = _client(log, audit)
    response = client.post(
        "/admin/approvals",
        json={"tool": "deploy", "ttl_seconds": 120},
        headers=_as("alice"),
    )
    assert response.status_code == 201
    assert response.json() == {
        "approved": True,
        "tool": "deploy",
        "approved_by": "alice",
        "expires_at": 5120.0,
    }
    assert log.is_approved("deploy")
    listing = client.get("/admin/approvals", headers=_as("alice")).json()
    assert listing["gated_tools"] == ["deploy", "publish"]
    assert [e["tool"] for e in listing["approvals"]] == ["deploy"]
    assert listing["approvals"][0]["approved_by"] == "alice"
    assert audit.events == [
        {
            "event": "approval_granted",
            "tool_name": "deploy",
            "caller": "tok_alice",
            "status": "ok",
            "details": {"ttl_seconds": 120.0, "approver": "alice"},
        }
    ]


def test_default_ttl_applied() -> None:
    log = ApprovalLog(clock=_Clock())
    client = _client(log, default_ttl_seconds=600.0)
    response = client.post(
        "/admin/approvals", json={"tool": "deploy"}, headers=_as("alice")
    )
    assert response.status_code == 201
    assert response.json()["expires_at"] == 5600.0


def test_non_gated_tool_404() -> None:
    log = ApprovalLog()
    client = _client(log)
    response = client.post(
        "/admin/approvals", json={"tool": "echo"}, headers=_as("alice")
    )
    assert response.status_code == 404
    assert response.json() == {"error": "unknown or non-gated tool"}
    assert log.entries() == []


def test_ttl_cap_and_lower_bound_422() -> None:
    log = ApprovalLog()
    client = _client(log, max_ttl_seconds=100.0)
    for ttl in (100.5, 10**6, 0, -5):
        response = client.post(
            "/admin/approvals",
            json={"tool": "deploy", "ttl_seconds": ttl},
            headers=_as("alice"),
        )
        assert response.status_code == 422, ttl
    at_cap = client.post(
        "/admin/approvals",
        json={"tool": "deploy", "ttl_seconds": 100},
        headers=_as("alice"),
    )
    assert at_cap.status_code == 201


@pytest.mark.parametrize(
    "payload",
    [
        {"tool": 5},
        {"tool": ""},
        {},
        {"tool": "deploy", "ttl_seconds": "60"},
        {"tool": "deploy", "ttl_seconds": True},
        {"tool": "deploy", "ttl_seconds": None},
        {"tool": "deploy", "extra": 1},
        ["deploy"],
        "deploy",
    ],
)
def test_bad_bodies_400(payload: object) -> None:
    log = ApprovalLog()
    client = _client(log)
    response = client.post("/admin/approvals", json=payload, headers=_as("alice"))
    assert response.status_code == 400
    assert log.entries() == []


@pytest.mark.parametrize(
    "content",
    [b"{not json", b"", b'{"tool": "deploy", "ttl_seconds": NaN}', b"\xff\xfe"],
)
def test_malformed_json_400(content: bytes) -> None:
    client = _client(ApprovalLog())
    response = client.post(
        "/admin/approvals",
        content=content,
        headers={**_as("alice"), "content-type": "application/json"},
    )
    assert response.status_code == 400


def test_wrong_content_type_400() -> None:
    client = _client(ApprovalLog())
    response = client.post(
        "/admin/approvals",
        content=b'{"tool": "deploy"}',
        headers={**_as("alice"), "content-type": "text/plain"},
    )
    assert response.status_code == 400


def test_oversized_body_413() -> None:
    log = ApprovalLog()
    client = _client(log)
    big = json.dumps({"tool": "deploy", "pad": "x" * MAX_BODY_BYTES}).encode()
    response = client.post(
        "/admin/approvals",
        content=big,
        headers={**_as("alice"), "content-type": "application/json"},
    )
    assert response.status_code == 413
    assert log.entries() == []


def test_oversized_chunked_body_413() -> None:
    client = _client(ApprovalLog())

    def chunks() -> Any:
        yield b'{"tool": "deploy", "pad": "'
        yield b"x" * MAX_BODY_BYTES
        yield b'"}'

    response = client.post(
        "/admin/approvals",
        content=chunks(),
        headers={**_as("alice"), "content-type": "application/json"},
    )
    assert response.status_code == 413


def test_bot_cannot_approve_itself_403() -> None:
    log = ApprovalLog()
    audit = _Audit()
    client = _client(log, audit)
    response = client.post(
        "/admin/approvals", json={"tool": "deploy"}, headers=_as("bot")
    )
    assert response.status_code == 403
    assert not log.is_approved("deploy")
    assert audit.events == []


def test_delete_revokes() -> None:
    log = ApprovalLog()
    audit = _Audit()
    client = _client(log, audit)
    client.post("/admin/approvals", json={"tool": "deploy"}, headers=_as("alice"))
    assert log.is_approved("deploy")
    response = client.delete("/admin/approvals/deploy", headers=_as("alice"))
    assert response.status_code == 200
    assert response.json() == {"revoked": True}
    assert not log.is_approved("deploy")
    assert [e["event"] for e in audit.events] == [
        "approval_granted",
        "approval_revoked",
    ]
    assert audit.events[1]["tool_name"] == "deploy"
    assert audit.events[1]["details"]["approver"] == "alice"
    again = client.delete("/admin/approvals/deploy", headers=_as("alice"))
    assert again.status_code == 404
    unknown = client.delete("/admin/approvals/echo", headers=_as("alice"))
    assert unknown.status_code == 404
    odd = client.delete("/admin/approvals/..%2Fetc", headers=_as("alice"))
    assert odd.status_code == 404
    assert len(audit.events) == 2


def test_audit_failure_does_not_change_result() -> None:
    log = ApprovalLog()
    client = _client(log, _Audit(fail=True))
    granted = client.post(
        "/admin/approvals", json={"tool": "deploy"}, headers=_as("alice")
    )
    assert granted.status_code == 201
    assert log.is_approved("deploy")
    revoked = client.delete("/admin/approvals/deploy", headers=_as("alice"))
    assert revoked.status_code == 200


def test_expiry_requires_reapproval() -> None:
    clock = _Clock()
    log = ApprovalLog(clock=clock)
    client = _client(log)
    client.post(
        "/admin/approvals",
        json={"tool": "deploy", "ttl_seconds": 30},
        headers=_as("alice"),
    )
    assert log.is_approved("deploy")
    clock.now += 31
    assert not log.is_approved("deploy")
    listing = client.get("/admin/approvals", headers=_as("alice")).json()
    assert listing["approvals"] == []
    assert (
        client.delete("/admin/approvals/deploy", headers=_as("alice")).status_code
        == 404
    )


def test_separate_pin_approval_is_manageable_but_not_gated_metadata() -> None:
    clock = _Clock()
    log = ApprovalLog(clock=clock)
    audit = _Audit()
    client = _client(
        log,
        audit,
        separate_approval_name="pin_bump_apply",
        default_ttl_seconds=30.0,
    )
    listing = client.get("/admin/approvals", headers=_as("alice")).json()
    assert listing["gated_tools"] == GATED

    unrelated = client.post(
        "/admin/approvals",
        json={"tool": "pin_bump_apply_extra"},
        headers=_as("alice"),
    )
    assert unrelated.status_code == 404
    assert not log.is_approved("pin_bump_apply")

    granted = client.post(
        "/admin/approvals",
        json={"tool": "pin_bump_apply"},
        headers=_as("alice"),
    )
    assert granted.status_code == 201
    assert log.is_approved("pin_bump_apply")
    assert (
        client.get("/admin/approvals", headers=_as("alice")).json()["gated_tools"]
        == GATED
    )

    clock.now += 31
    assert not log.is_approved("pin_bump_apply")
    assert (
        client.delete(
            "/admin/approvals/pin_bump_apply", headers=_as("alice")
        ).status_code
        == 404
    )
    again = client.post(
        "/admin/approvals",
        json={"tool": "pin_bump_apply", "ttl_seconds": 10},
        headers=_as("alice"),
    )
    assert again.status_code == 201
    revoked = client.delete("/admin/approvals/pin_bump_apply", headers=_as("alice"))
    assert revoked.status_code == 200
    assert not log.is_approved("pin_bump_apply")
    assert [event["tool_name"] for event in audit.events] == [
        "pin_bump_apply",
        "pin_bump_apply",
        "pin_bump_apply",
    ]


def test_separate_approval_name_is_exact_and_cannot_self_approve() -> None:
    with pytest.raises(ValueError, match="unsupported separate approval name"):
        approval_routes(
            approval_log=ApprovalLog(),
            gated_tools=lambda: GATED,
            separate_approval_name="other_action",
        )
    log = ApprovalLog()
    client = _client(log, separate_approval_name="pin_bump_apply")
    denied = client.post(
        "/admin/approvals",
        json={"tool": "pin_bump_apply"},
        headers=_as("bot"),
    )
    assert denied.status_code == 403
    assert not log.is_approved("pin_bump_apply")


def test_responses_do_not_echo_credentials() -> None:
    log = ApprovalLog()
    client = _client(log)
    secret = "Bearer super-secret-token-value"
    response = client.post(
        "/admin/approvals",
        json={"tool": "deploy"},
        headers={**_as("alice"), "authorization": secret},
    )
    assert "super-secret" not in response.text
    assert "super-secret" not in json.dumps(dict(response.headers))


def test_registry_integration_gate_before_and_after_approval() -> None:
    log = ApprovalLog()
    registry = ToolRegistry(approval_log=log)
    registry.register(
        "deploy",
        "Deploy",
        {"type": "object", "properties": {}},
        lambda: {"deployed": True},
        requires_approval=True,
    )
    client = _client(log)
    before = json.loads(registry.dispatch("deploy", {}))
    assert before == {"error": "approval required", "tool": "deploy"}
    assert (
        client.post(
            "/admin/approvals", json={"tool": "deploy"}, headers=_as("alice")
        ).status_code
        == 201
    )
    assert json.loads(registry.dispatch("deploy", {})) == {"deployed": True}
    client.delete("/admin/approvals/deploy", headers=_as("alice"))
    assert json.loads(registry.dispatch("deploy", {}))["error"] == "approval required"
