# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Hermetic fleet-coordinator client tests. No live coordinator is contacted."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from omega_prime.credentials.broker import CredentialBroker
from omega_prime.engine.fleet_client import (
    FleetClient,
    FleetError,
    HttpResponse,
    JobSpec,
    UrllibTransport,
    read_named_secret,
)
from omega_prime.policy.policy import SeatPolicy
from omega_prime.tools.capacity import fleet_status

TOKEN = "fleet-test-token"
SPEC = JobSpec(
    repo="acme/widget",
    prompt="fix the python test",
    model="zai/glm-4.5",
    slash_command="gsd-autonomous",
    slash_args="ultrathink orchestrate",
    max_minutes=20,
    max_files=4,
    push=True,
    pr=True,
)


class FakeTransport:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[dict[str, object]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> HttpResponse:
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body}
        )
        return self.handler(method, url, headers, body)


def _allow(*hosts: str) -> SeatPolicy:
    return SeatPolicy({"version": 1, "network": {"hosts": list(hosts)}})


def _expect_host_denied(
    checks: tuple[tuple[Callable[..., object], tuple[object, ...]], ...],
) -> None:
    """Each call must raise ``host_not_allowed`` and must not echo the token."""
    for method, args in checks:
        try:
            method(*args)
        except FleetError as exc:
            assert exc.code == "host_not_allowed"
            assert TOKEN not in str(exc)
        else:
            raise AssertionError("expected host_not_allowed")


def _client(handler, **env: str) -> tuple[FleetClient, FakeTransport]:
    base = {
        "FLEET_COORDINATOR_URL": "https://fleet.example",
        "FLEET_COORDINATOR_TOKEN": TOKEN,
    }
    base.update(env)
    transport = FakeTransport(handler)
    return FleetClient(base, transport, policy=_allow("fleet.example")), transport


def _ok(payload: dict) -> HttpResponse:
    return HttpResponse(200, json.dumps(payload).encode())


def test_submit_posts_one_jobspec_with_bearer() -> None:
    def handler(method, url, headers, body):
        assert method == "POST"
        assert urlsplit(url).path == "/jobs"
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        posted = json.loads(body.decode())
        assert posted == SPEC.to_json()
        assert "languages" not in posted
        return HttpResponse(
            201, json.dumps({"jobs": [{"id": "job-1", "status": "queued"}]}).encode()
        )

    client, transport = _client(handler)
    job = client.submit(SPEC)
    assert job == {"id": "job-1", "status": "queued"}
    assert len(transport.calls) == 1


def test_false_push_flag_is_sent() -> None:
    spec = JobSpec(repo="acme/widget", prompt="docs", push=False, pr=False)

    def handler(method, url, headers, body):
        del method, url, headers
        posted = json.loads(body.decode())
        assert posted["push"] is False
        assert posted["pr"] is False
        return HttpResponse(201, json.dumps({"jobs": [{"id": "job-2"}]}).encode())

    client, _transport = _client(handler)
    assert client.submit(spec)["id"] == "job-2"


def test_unset_optional_fields_stay_off_the_wire() -> None:
    spec = JobSpec(repo="acme/widget", prompt="docs")
    body = spec.to_json()
    assert body == {"repo": "acme/widget", "prompt": "docs"}


def test_missing_token_does_not_call() -> None:
    def handler(method, url, headers, body):
        del method, url, headers, body
        raise AssertionError("transport was called")

    client, _transport = _client(handler, FLEET_COORDINATOR_TOKEN="")
    assert client.configured() is False
    try:
        client.submit(SPEC)
    except FleetError as exc:
        assert exc.code == "not_configured"
        assert TOKEN not in str(exc)
    else:
        raise AssertionError("submit should have failed")


def test_token_file_is_read_and_a_loose_file_is_refused(tmp_path) -> None:
    path = tmp_path / "token"
    path.write_text(TOKEN + "\n", encoding="utf-8")
    path.chmod(0o600)

    def handler(method, url, headers, body):
        del method, url, body
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        return HttpResponse(201, json.dumps({"jobs": [{"id": "job-3"}]}).encode())

    env = {
        "FLEET_COORDINATOR_URL": "https://fleet.example",
        "FLEET_COORDINATOR_TOKEN_FILE": str(path),
    }
    client = FleetClient(env, FakeTransport(handler), policy=_allow("fleet.example"))
    assert client.submit(SPEC)["id"] == "job-3"
    path.chmod(0o644)
    loose = FleetClient(env, FakeTransport(handler), policy=_allow("fleet.example"))
    assert loose.configured() is False
    assert loose.configuration_error() == "secret_permissions"


def test_missing_or_empty_token_file_is_not_a_permissions_error(tmp_path) -> None:
    def handler(method, url, headers, body):
        del method, url, headers, body
        raise AssertionError("transport was called")

    missing = FleetClient(
        {
            "FLEET_COORDINATOR_URL": "https://fleet.example",
            "FLEET_COORDINATOR_TOKEN_FILE": str(tmp_path / "absent"),
        },
        FakeTransport(handler),
        policy=_allow("fleet.example"),
    )
    assert missing.configured() is False
    assert missing.configuration_error() == "secret_unreadable"
    empty = tmp_path / "empty"
    empty.write_text("", encoding="utf-8")
    empty.chmod(0o600)
    blank = FleetClient(
        {
            "FLEET_COORDINATOR_URL": "https://fleet.example",
            "FLEET_COORDINATOR_TOKEN_FILE": str(empty),
        },
        FakeTransport(handler),
        policy=_allow("fleet.example"),
    )
    assert blank.configured() is False
    assert blank.configuration_error() == "secret_unreadable"


def test_unreadable_private_token_file_stays_inside_fleet_error(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "token"
    path.write_text(TOKEN + "\n", encoding="utf-8")
    path.chmod(0o600)
    real_read = Path.read_text

    def guarded(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if self == path:
            raise PermissionError("denied")
        return real_read(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", guarded)

    def handler(method, url, headers, body):
        del method, url, headers, body
        raise AssertionError("transport was called")

    env = {
        "FLEET_COORDINATOR_URL": "https://fleet.example",
        "FLEET_COORDINATOR_TOKEN_FILE": str(path),
    }
    client = FleetClient(env, FakeTransport(handler), policy=_allow("fleet.example"))
    assert client.configured() is False
    assert client.configuration_error() == "secret_unreadable"
    try:
        client.submit(SPEC)
    except FleetError as exc:
        assert exc.code == "secret_unreadable"
        assert "denied" not in str(exc)
        assert TOKEN not in str(exc)
    else:
        raise AssertionError("expected secret_unreadable")

    monkeypatch.setattr(
        "omega_prime.engine.fleet_client.seat_policy",
        lambda: _allow("fleet.example"),
    )
    report = fleet_status(env)
    assert report == {"status": "not_configured", "reason": "secret_unreadable"}


def test_status_events_hitl_and_cancel_use_the_coordinator_routes() -> None:
    def handler(method, url, headers, body):
        del headers
        parts = urlsplit(url)
        if method == "GET" and parts.path == "/status":
            return _ok({"draining": False})
        if method == "GET" and parts.path == "/jobs" and parts.query == "limit=20":
            return _ok({"jobs": [{"id": "job-1", "status": "running"}]})
        if method == "GET" and parts.path == "/jobs/job-1":
            return _ok(
                {"job": {"id": "job-1", "status": "running", "branch": "fleet/job-1"}}
            )
        if method == "GET" and parts.path == "/jobs/job-1/events":
            assert parts.query == "after=4&limit=500"
            return _ok({"events": [{"seq": 5, "ts": "t", "kind": "log", "data": "{}"}]})
        if method == "GET" and parts.path == "/hitl":
            return _ok({"pending": [{"job_id": "job-1"}]})
        if method == "POST" and parts.path == "/hitl/answer":
            assert json.loads(body.decode()) == {"job_id": "job-1", "value": "yes"}
            return _ok({"ok": True})
        if method == "POST" and parts.path == "/jobs/job-1/cancel":
            return _ok({"job": {"id": "job-1", "status": "cancelled"}})
        raise AssertionError(f"unexpected {method} {url}")

    client, _transport = _client(handler)
    assert client.coordinator_status()["draining"] is False
    assert client.list_jobs()[0]["id"] == "job-1"
    polled = client.poll("job-1", after=4)
    assert polled["job"]["branch"] == "fleet/job-1"
    assert polled["events"][0]["seq"] == 5
    assert client.hitl_pending()["pending"][0]["job_id"] == "job-1"
    assert client.answer_hitl("job-1", "yes")["ok"] is True
    assert client.cancel("job-1")["status"] == "cancelled"


def test_error_body_does_not_surface_the_token() -> None:
    def handler(method, url, headers, body):
        del method, url, headers, body
        leaked = json.dumps({"error": f"Bearer {TOKEN}"}).encode()
        return HttpResponse(401, leaked)

    client, _transport = _client(handler)
    try:
        client.submit(SPEC)
    except FleetError as exc:
        assert exc.code == "http_401"
        assert TOKEN not in str(exc)
    else:
        raise AssertionError("expected http_401")


def test_run_token_is_redacted_from_the_job_view() -> None:
    def handler(method, url, headers, body):
        del method, url, headers, body
        return HttpResponse(
            201,
            json.dumps(
                {"jobs": [{"id": "job-1", "run_token": "super-secret-run"}]}
            ).encode(),
        )

    client, _transport = _client(handler)
    job = client.submit(SPEC)
    assert job["run_token"] == "[redacted]"
    assert "super-secret-run" not in json.dumps(job)


def test_userinfo_in_the_coordinator_url_is_rejected() -> None:
    def handler(method, url, headers, body):
        del method, url, headers, body
        raise AssertionError("transport was called")

    client, _transport = _client(
        handler,
        FLEET_COORDINATOR_URL="https://user:secret@fleet.example",
    )
    try:
        client.submit(SPEC)
    except FleetError as exc:
        assert exc.code == "invalid_coordinator_url"
    else:
        raise AssertionError("expected invalid url")


def test_protected_branch_and_bad_repo_are_rejected() -> None:
    client, _transport = _client(lambda *args: HttpResponse(500, b""))
    try:
        client.submit(JobSpec(repo="not-a-repo", prompt="x"))
    except FleetError as exc:
        assert exc.code == "invalid_repo"
    else:
        raise AssertionError("expected invalid_repo")
    try:
        client.submit(JobSpec(repo="acme/widget", prompt="x", branch="main"))
    except FleetError as exc:
        assert exc.code == "invalid_branch"
    else:
        raise AssertionError("expected invalid_branch")


def test_remote_http_is_refused_before_the_transport() -> None:
    def handler(method, url, headers, body):
        del method, url, headers, body
        raise AssertionError("transport was called")

    client, _transport = _client(handler, FLEET_COORDINATOR_URL="http://fleet.example")
    try:
        client.submit(JobSpec(repo="acme/widget", prompt="docs"))
    except FleetError as exc:
        assert exc.code == "insecure_coordinator_url"
    else:
        raise AssertionError("expected insecure_coordinator_url")


def test_shipped_policy_blocks_an_unlisted_host() -> None:
    def handler(method, url, headers, body):
        del method, url, headers, body
        raise AssertionError("transport was called")

    client = FleetClient(
        {
            "FLEET_COORDINATOR_URL": "https://fleet.example",
            "FLEET_COORDINATOR_TOKEN": TOKEN,
        },
        FakeTransport(handler),
    )
    try:
        client.submit(JobSpec(repo="acme/widget", prompt="docs"))
    except FleetError as exc:
        assert exc.code == "host_not_allowed"
        assert TOKEN not in str(exc)
    else:
        raise AssertionError("expected host_not_allowed")


def test_urllib_transport_uses_a_scripted_opener(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        status = 201

        def read(self) -> bytes:
            return b'{"jobs":[{"id":"job-9"}]}'

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            del args

    class _Opener:
        def open(self, request: urllib.request.Request, timeout: float) -> _Response:
            del timeout
            captured["url"] = request.full_url
            captured["data"] = request.data
            captured["auth"] = request.get_header("Authorization")
            return _Response()

    def build_opener(*handlers: object) -> _Opener:
        captured["handlers"] = handlers
        return _Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    response = UrllibTransport().request(
        "POST",
        "https://fleet.example/jobs",
        {"Authorization": f"Bearer {TOKEN}"},
        b"{}",
    )
    assert response.status == 201
    assert captured["url"] == "https://fleet.example/jobs"
    assert captured["auth"] == f"Bearer {TOKEN}"
    handlers = captured["handlers"]
    assert isinstance(handlers, tuple)
    assert any(isinstance(item, urllib.request.ProxyHandler) for item in handlers)


def test_token_comes_from_the_credential_broker(monkeypatch) -> None:
    """The bearer is the broker key. The token name is not read here."""
    looked_up: list[tuple[str, tuple[str, ...], str]] = []

    def key_for(self: CredentialBroker, provider: Any, url: str) -> str:
        del self
        looked_up.append((provider.name, tuple(provider.env_vars), url))
        return "broker-issued"

    def named(env: Mapping[str, str], name: str) -> str:
        assert name == "FLEET_COORDINATOR_URL"
        return read_named_secret(env, name)

    monkeypatch.setattr(CredentialBroker, "key_for", key_for)
    monkeypatch.setattr("omega_prime.engine.fleet_client.read_named_secret", named)

    def handler(method, url, headers, body):
        del method, url, body
        assert headers["Authorization"] == "Bearer broker-issued"
        return HttpResponse(201, json.dumps({"jobs": [{"id": "job-broker"}]}).encode())

    client, transport = _client(handler)
    assert client.submit(SPEC)["id"] == "job-broker"
    assert len(transport.calls) == 1
    assert looked_up == [
        (
            "fleet-coordinator",
            ("FLEET_COORDINATOR_TOKEN",),
            "https://fleet.example/jobs",
        )
    ]
    assert TOKEN not in json.dumps(looked_up)


def test_disallowed_host_fails_closed_before_the_token_is_read(monkeypatch) -> None:
    """SeatPolicy.allows_host runs first. A miss does not read the token."""

    def named(env: Mapping[str, str], name: str) -> str:
        assert name == "FLEET_COORDINATOR_URL"
        return read_named_secret(env, name)

    def secret_value(env: Mapping[str, str], name: str) -> str:
        del env, name
        raise AssertionError("token was read for a refused host")

    monkeypatch.setattr("omega_prime.engine.fleet_client.read_named_secret", named)
    monkeypatch.setattr("omega_prime.credentials.broker.secret_value", secret_value)

    def handler(method, url, headers, body):
        del method, url, headers, body
        raise AssertionError("transport was called")

    spec = JobSpec(repo="acme/widget", prompt="docs")
    for policy in (_allow(), _allow("other.example")):
        client = FleetClient(
            {
                "FLEET_COORDINATOR_URL": "https://fleet.example",
                "FLEET_COORDINATOR_TOKEN": TOKEN,
            },
            FakeTransport(handler),
            policy=policy,
        )
        _expect_host_denied(
            (
                (client.submit, (spec,)),
                (client.list_jobs, ()),
                (client.get_job, ("job-1",)),
                (client.events, ("job-1",)),
                (client.poll, ("job-1",)),
                (client.coordinator_status, ()),
                (client.hitl_pending, ()),
                (client.answer_hitl, ("job-1", "yes")),
                (client.cancel, ("job-1",)),
            )
        )
        assert client.configured() is False
        assert client.configuration_error() == "host_not_allowed"


def test_a_stand_in_policy_cannot_allow_the_host() -> None:
    class StandIn:
        def allows_host(self, host: str) -> bool:
            del host
            return True

    def handler(method, url, headers, body):
        del method, url, headers, body
        raise AssertionError("transport was called")

    client = FleetClient(
        {
            "FLEET_COORDINATOR_URL": "https://fleet.example",
            "FLEET_COORDINATOR_TOKEN": TOKEN,
        },
        FakeTransport(handler),
        policy=cast(Any, StandIn()),
    )
    try:
        client.submit(JobSpec(repo="acme/widget", prompt="docs"))
    except FleetError as exc:
        assert exc.code == "host_not_allowed"
    else:
        raise AssertionError("expected host_not_allowed")
