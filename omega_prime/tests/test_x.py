"""Phase 17: the X connector family behind a fake transport."""

from __future__ import annotations

import base64
import json
import urllib.request

import pytest

from omega_prime.agent.modes import apply_plan_mode
from omega_prime.credentials.broker import CredentialBroker
from omega_prime.policy.policy import SeatPolicy
from omega_prime.providers.base import ProviderError
from omega_prime.providers.fake import FakeTransport
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.registry import ToolRegistry
from omega_prime.tools.x import XClient, register_x_tools


def _load(payload: str) -> dict:
    return json.loads(payload)


def _policy(hosts: list[str]) -> SeatPolicy:
    return SeatPolicy(
        {"version": 1, "tools": {}, "paths": {}, "network": {"hosts": hosts}}
    )


def _client(transport: FakeTransport) -> XClient:
    broker = CredentialBroker(
        _policy(["api.x.com", "upload.twitter.com"]), {"X_API_TOKEN": "x-secret"}
    )
    return XClient(transport).with_broker(broker)


def _registry(client: XClient, approval_log=None) -> ToolRegistry:
    registry = ToolRegistry(approval_log=approval_log)
    register_x_tools(registry, client)
    return registry


def test_mentions_and_reads_need_no_approval():
    transport = FakeTransport(
        script=[
            {"data": {"id": "bot-9", "name": "Omega Prime", "username": "omegaprime"}},
            {
                "data": [
                    {"id": "m1", "text": "hi @omegaprime"},
                    {"id": "m2", "text": "yo @omegaprime"},
                ],
                "meta": {"result_count": 2, "next_token": "tok-1"},
            },
            {"data": {"id": "m1", "text": "hi @omegaprime"}},
        ]
    )
    registry = _registry(_client(transport))

    mentions = _load(registry.dispatch("x_mentions", {}))
    assert [post["id"] for post in mentions["posts"]] == ["m1", "m2"]
    assert mentions["next_token"] == "tok-1"

    post = _load(registry.dispatch("x_read_post", {"post_id": "m1"}))
    assert post == {"id": "m1", "text": "hi @omegaprime"}

    methods = [call[0] for call in transport.calls]
    assert methods == ["GET", "GET", "GET"]
    assert transport.calls[1][1].endswith("/2/users/bot-9/mentions")
    assert transport.calls[1][3] == {"max_results": 10}
    assert transport.calls[0][2]["Authorization"] == "Bearer x-secret"


def test_publishing_requires_approval_and_threads_chain():
    transport = FakeTransport(
        script=[
            {"data": {"id": "p1", "text": "hello"}},
            {"data": {"id": "t1", "text": "one"}},
            {"data": {"id": "t2", "text": "two"}},
            {"data": {"id": "t3", "text": "three"}},
        ]
    )
    log = ApprovalLog()
    registry = _registry(_client(transport), approval_log=log)

    blocked = _load(registry.dispatch("x_post", {"text": "hello"}))
    assert blocked == {"error": "approval required", "tool": "x_post"}
    assert transport.calls == []

    assert log.approve("x_post", "ada")["approved"] is True
    assert log.approve("x_post_thread", "ada")["approved"] is True

    posted = _load(registry.dispatch("x_post", {"text": "hello", "reply_to": "m1"}))
    assert posted == {"id": "p1", "text": "hello"}
    assert transport.calls[0][3] == {
        "text": "hello",
        "reply": {"in_reply_to_tweet_id": "m1"},
    }

    threaded = _load(
        registry.dispatch("x_post_thread", {"texts": ["one", "two", "three"]})
    )
    assert [post["id"] for post in threaded["posts"]] == ["t1", "t2", "t3"]
    bodies = [call[3] for call in transport.calls[1:]]
    assert bodies[0] == {"text": "one"}
    assert bodies[1]["reply"] == {"in_reply_to_tweet_id": "t1"}
    assert bodies[2]["reply"] == {"in_reply_to_tweet_id": "t2"}


def test_media_upload_stages_bytes_and_rejects_bad_base64():
    transport = FakeTransport(script=[{"media_id_string": "mid-1"}])
    registry = _registry(_client(transport))

    staged = _load(
        registry.dispatch(
            "x_upload_media",
            {"data_b64": base64.b64encode(b"png-bytes").decode("ascii")},
        )
    )
    assert staged == {"media_id": "mid-1"}
    assert transport.calls[0][1].endswith("/1.1/media/upload.json")
    assert transport.calls[0][3]["media_category"] == "tweet_image"

    bad = _load(registry.dispatch("x_upload_media", {"data_b64": "!!!not-b64!!!"}))
    assert bad == {"error": "data_b64 is not valid base64"}
    assert len(transport.calls) == 1


def test_api_errors_surface_and_unapproved_hosts_get_no_credential():
    class _Failing(FakeTransport):
        def get(self, url, headers, params):
            raise ProviderError("HTTP 403 from api.x.com")

    registry = _registry(XClient(_Failing(), token="fake"))
    refused = _load(registry.dispatch("x_read_post", {"post_id": "m1"}))
    assert "HTTP 403" in refused["error"]

    transport = FakeTransport()
    broker = CredentialBroker(_policy(["api.x.com"]), {"X_API_TOKEN": "x-secret"})
    client = XClient(transport).with_broker(broker)
    with pytest.raises(ProviderError, match=r"upload\.twitter\.com"):
        client.upload_media(b"bytes")
    assert transport.calls == []


def test_plan_mode_refuses_publishing_but_not_reads():
    def x_post():
        raise AssertionError("must not run")

    def x_mentions():
        return "mentions"

    wrapped = apply_plan_mode({"x_post": x_post, "x_mentions": x_mentions})

    assert wrapped["x_post"]() == "error: plan mode forbids x_post"
    assert wrapped["x_mentions"] is x_mentions


def test_runtime_transport_uses_urllib_offline(monkeypatch):
    from omega_prime.mcp_server import _UrllibXTransport

    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"data":{"id":"u1"}}'

    def urlopen(request, timeout):
        seen.append((request, timeout))
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    client = XClient(_UrllibXTransport(), token="offline-token")

    assert client.me() == {"id": "u1"}
    request, timeout = seen[0]
    assert request.full_url == "https://api.x.com/2/users/me"
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") == "Bearer offline-token"
    assert timeout == 30
