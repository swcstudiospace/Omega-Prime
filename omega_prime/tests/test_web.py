"""Phase 46-02: policy-bound preview behind the SEC-NET 2.2.0 transport.

Hermetic cases run production DestinationTransport/SafeFetch/WebClient with
controlled resolver/socket/clock fixtures. They assert observable decisions
and returned bytes, never source text. Real-socket proof lives behind the
opt-in ``--runtime`` entrypoint, which Main alone executes.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from omega_prime.policy.policy import SeatPolicy
from omega_prime.providers.destination import (
    DecodeBudget,
    DestinationDenied,
    DestinationTransport,
    HttpRequest,
    ResolvedAddress,
    SafeFetch,
)
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.playwright_browser import PlaywrightBrowser
from omega_prime.tools.registry import ToolRegistry
from omega_prime.tools.webpack import WebClient, WebContext, register_web_tools

AC = "AC-OMEGA-V9-01"
CHECK = "LAND-01/T-46-08-hermetic-transport-regressions"

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


def _policy_doc(hosts: list[str]) -> dict:
    return {"version": 1, "tools": {}, "paths": {}, "network": {"hosts": hosts}}


def _policy(hosts: list[str]) -> SeatPolicy:
    return SeatPolicy(_policy_doc(hosts))


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


class FakeResolver:
    def __init__(self, answers: dict[str, list[ResolvedAddress] | Exception]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def resolve(
        self, host: str, port: int, *, scope, limits
    ) -> tuple[ResolvedAddress, ...]:
        self.calls.append(host)
        answer = self.answers.get(host, [])
        if isinstance(answer, Exception):
            raise answer
        return tuple(answer)


def _v4(addr: str = PUBLIC_V4, port: int = 443) -> ResolvedAddress:
    return ResolvedAddress(family=2, address=addr, port=port)


def _v6(addr: str = PUBLIC_V6, port: int = 443) -> ResolvedAddress:
    return ResolvedAddress(family=10, address=addr, port=port)


class FakeSocket:
    family = 2

    def __init__(self, peer: tuple, inbound: bytes = b"") -> None:
        self._peer = peer
        self._inbound = bytearray(inbound)
        self.sent = bytearray()
        self.closed = False
        self.inner = self

    def getpeername(self):  # type: ignore[no-untyped-def]
        return self._peer

    def setblocking(self, flag: bool) -> None:
        pass

    def fileno(self) -> int:
        raise OSError("fake socket has no fd")

    def send(self, data: memoryview) -> int:
        blob = bytes(data)
        self.sent.extend(blob)
        return len(blob)

    def recv_into(self, buffer: memoryview) -> int:
        n = min(len(buffer), len(self._inbound))
        buffer[:n] = self._inbound[:n]
        del self._inbound[:n]
        return n

    def shutdown(self, how: int) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeDialer:
    def __init__(self, inbound: bytes, peer_override: tuple | None = None) -> None:
        self.inbound = inbound
        self.peer_override = peer_override
        self.calls: list[ResolvedAddress] = []
        self.sockets: list[FakeSocket] = []

    def dial(self, endpoint: ResolvedAddress, *, scope) -> FakeSocket:
        scope.remaining()
        self.calls.append(endpoint)
        peer: tuple = self.peer_override or (endpoint.address, endpoint.port)
        sock = FakeSocket(peer, bytes(self.inbound))
        sock.family = 2 if endpoint.family == 2 else 10
        self.sockets.append(sock)
        return sock


class FakeTLS:
    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def wrap(self, raw, *, identity: str, context, scope):  # type: ignore[no-untyped-def]
        self.calls.append(identity)
        if self.fail is not None:
            raise self.fail
        return raw


def _http_response(
    status: int, body: bytes, headers: dict[str, str] | None = None, reason: str = "OK"
) -> bytes:
    head = {"Content-Length": str(len(body)), "Content-Type": "text/html"}
    head.update(headers or {})
    lines = [f"HTTP/1.1 {status} {reason}"]
    lines += [f"{k}: {v}" for k, v in head.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin1") + body


_DEFAULT_POLICY = object()


def _harness(
    hosts: list[str],
    inbound: bytes,
    answers: dict | None = None,
    port: int = 443,
    tls_fail: Exception | None = None,
    policy: Any = _DEFAULT_POLICY,
):
    clock = FakeClock()
    pol = _policy(hosts) if policy is _DEFAULT_POLICY else policy
    host = hosts[0].lower() if hosts else "acme.test"
    default_answers: dict[str, list[ResolvedAddress] | Exception] = {
        host: [_v4(port=port)]
    }
    if answers is not None:
        default_answers.update(answers)
    resolver = FakeResolver(default_answers)
    dialer = FakeDialer(inbound)
    tls = FakeTLS(fail=tls_fail)
    transport = DestinationTransport(
        pol, resolver=resolver, dialer=dialer, tls_verifier=tls, clock=clock
    )
    fetch = SafeFetch(transport)
    return transport, fetch, resolver, dialer, tls, clock


def _scripted_client(
    tmp_path,
    hosts,
    body: bytes = b"<h1>hi</h1>",
    status: int = 200,
    extra_headers: dict | None = None,
    answers: dict | None = None,
    port: int = 443,
    policy: Any = _DEFAULT_POLICY,
):
    inbound = _http_response(status, body, extra_headers)
    transport, fetch, resolver, dialer, tls, clock = _harness(
        hosts, inbound, answers, port, policy=policy
    )
    ctx = WebContext(
        root=tmp_path,
        policy=transport.policy,
        transport=transport,
        fetch=fetch,
        browser_factory=lambda: PlaywrightBrowser.__new__(PlaywrightBrowser),
        vision=FakeVision(),
        artifacts_dir=tmp_path / "shots",
    )
    return WebClient(ctx), transport, fetch, resolver, dialer, tls, clock


class FakeVercel:
    def __init__(self, projects=("acme",)):
        self.projects = tuple(projects)

    def allowed(self, project):
        return project in self.projects

    def deployments(self, project, limit):
        return {
            "body": {
                "deployments": [
                    {
                        "uid": "d-1",
                        "state": "READY",
                        "target": "production",
                        "url": "acme.vercel.app",
                        "created": 1,
                        "meta": {},
                    }
                ]
            }
        }

    def deployment(self, deployment_id):
        return {"body": {"uid": deployment_id}}

    def promote(self, project, deployment_id):
        return {"body": {"aliased": True}}

    def rollback(self, project, deployment_id):
        return {"body": {"aliased": True}}


class FakePage:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.url = "about:blank"
        self.closed = False

    def goto(self, url, wait_until=None):
        self.url = url
        return FakeResponse(200)

    def title(self):
        return "Acme"

    def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG-fake")

    def close(self):
        self.closed = True


class FakeResponse:
    def __init__(self, status):
        self.status = status


class FakeBrowserPeer:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.page = FakePage(tmp_path)
        self.closed = False

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


class FakeVision:
    def analyze(self, image_url, question, region=None):
        return {"answer": f"saw {image_url}: {question}"}


def _ctx(tmp_path, **overrides) -> WebContext:
    _client, _transport, _fetch, _resolver, _dialer, _tls, _clock = _scripted_client(
        tmp_path, ["acme.test"], port=443, answers={"acme.test": [_v4(port=443)]}
    )
    base: dict[str, Any] = {
        "root": Path(tmp_path),
        "vercel": FakeVercel(),
        "policy": _transport.policy,
        "transport": _transport,
        "fetch": _fetch,
        "browser_factory": lambda: PlaywrightBrowser(
            launch=lambda: FakeBrowserPeer(tmp_path)
        ),
        "vision": FakeVision(),
        "artifacts_dir": Path(tmp_path) / "shots",
    }
    base.update(overrides)
    return WebContext(**base)


def test_vercel_allowlist_and_shapes(tmp_path):
    client = WebClient(_ctx(tmp_path))
    listed = client.vercel_deployments("acme")
    assert listed["deployments"][0]["uid"] == "d-1"
    assert (
        client.vercel_deployments("acme", deployment_id="d-9")["deployment"]["uid"]
        == "d-9"
    )
    assert client.vercel_promote("acme", "d-1") == {
        "ok": True,
        "result": {"aliased": True},
    }
    assert client.vercel_rollback("acme", "d-1") == {
        "ok": True,
        "result": {"aliased": True},
    }
    assert "forbidden" in client.vercel_deployments("evil")["error"]
    assert "forbidden" in client.vercel_promote("evil", "d-1")["error"]
    bare = WebClient(WebContext(root=tmp_path))
    assert "not_configured" in bare.vercel_deployments("acme")["error"]


def test_preview_shape_mismatch_and_failure(tmp_path):
    client = WebClient(_ctx(tmp_path))
    ok = client.preview_check("https://acme.test/", expect_status=200)
    assert ok["matches"] is True and ok["status"] == 200 and ok["bytes"] == 11
    assert ok["host"] == "acme.test" and ok["redirect"] is None
    assert ok["content_type"] == "text/html"
    assert ok["body_sha256"] == hashlib.sha256(b"<h1>hi</h1>").hexdigest()
    mismatch = client.preview_check("https://acme.test/", expect_status=201)
    assert mismatch["matches"] is False
    with pytest.raises(ValueError, match="non-empty string"):
        client.preview_check("")


def test_bundle_scan_findings(tmp_path):
    (tmp_path / "app.js").write_text(
        "const key = 'sk-abcdefgh12345678';\n", encoding="utf-8"
    )
    (tmp_path / "clean.js").write_text("console.log(1);\n", encoding="utf-8")
    client = WebClient(_ctx(tmp_path))
    result = client.bundle_secret_scan(["app.js", "clean.js"])
    assert result["ok"] is False and result["gate"] == "G-3"
    assert result["findings"] == [{"path": "app.js", "line": 1}]
    assert client.bundle_secret_scan(["clean.js"])["ok"] is True
    assert "not under this root" in client.bundle_secret_scan(["missing.js"])["error"]
    assert "repo-relative" in client.bundle_secret_scan(["../x.js"])["error"]


def test_review_page_orchestrates_all_three(tmp_path):
    client = WebClient(_ctx(tmp_path))
    report = client.review_page(
        "https://acme.test/", "is the hero visible?", name="home"
    )
    assert report["connectivity"]["status"] == 200
    assert report["page"] == {
        "title": "Acme",
        "url": "https://acme.test/",
        "status": 200,
    }
    assert report["screenshot"]["path"].endswith("home.png")
    assert Path(report["screenshot"]["path"]).is_file()
    assert "hero visible" in report["vision"]["answer"]
    blind = WebClient(_ctx(tmp_path, vision=None))
    assert (
        "not configured"
        in blind.review_page("https://acme.test/", "q")["vision"]["error"]
    )
    assert (
        "invalid_name"
        in client.review_page("https://acme.test/", "q", name="../../evil")["error"]
    )


def test_browser_lifecycle_and_errors(tmp_path):
    peer_holder: list = []

    def launch():
        peer = FakeBrowserPeer(tmp_path)
        peer_holder.append(peer)
        return peer

    browser = PlaywrightBrowser(launch=launch)
    assert browser.navigate("https://x/") == {"error": "browser is not started"}
    assert browser.start() == {"ok": True, "open": True}
    assert browser.snapshot() == {"title": "Acme", "url": "about:blank"}
    shot = browser.screenshot(tmp_path / "s.png")
    assert shot["bytes"] == 9 and Path(shot["path"]).is_file()
    assert browser.close() == {"ok": True}
    assert peer_holder[0].closed is True and peer_holder[0].page.closed is True
    assert browser.close() == {"ok": True}


def test_approvals_gate_promote_rollback_only(tmp_path):
    log = ApprovalLog()
    registry = ToolRegistry(approval_log=log)
    register_web_tools(registry, WebClient(_ctx(tmp_path)))
    assert (
        json.loads(
            registry.dispatch("web_preview_check", {"url": "https://acme.test/"})
        )["status"]
        == 200
    )
    for tool in ("web_vercel_promote", "web_vercel_rollback"):
        assert json.loads(
            registry.dispatch(tool, {"project": "acme", "deployment_id": "d-1"})
        ) == {"error": "approval required", "tool": tool}
    assert log.approve("web_vercel_promote", "ada").get("approved") is True
    assert (
        json.loads(
            registry.dispatch(
                "web_vercel_promote", {"project": "acme", "deployment_id": "d-1"}
            )
        )["ok"]
        is True
    )


# ---- P46_R01: exact policy admission performs no resolver/dial when denied ----


@pytest.mark.parametrize("case_id", ["P46_R01"])
def test_P46_R01_none_empty_unlisted_subdomain_deny_before_dns(tmp_path, case_id):
    assert bool(AC and CHECK)  # trace: AC-OMEGA-V9-01 / LAND-01
    inbound = _http_response(200, b"<h1>hi</h1>")
    for policy in (None, _policy([])):
        transport, fetch, resolver, dialer, _, clock = _harness(
            ["acme.test"], inbound, policy=policy
        )
        root = transport.begin_operation(deadline_at=clock.now + 15.0)
        out = fetch("https://acme.test/", 15.0, operation=root)
        assert "forbidden_host" in str(out["error"])
        assert resolver.calls == [] and dialer.calls == []
    transport, fetch, resolver, dialer, _, clock = _harness(["acme.test"], inbound)
    root = transport.begin_operation(deadline_at=clock.now + 15.0)
    out = fetch("https://other.test/", 15.0, operation=root)
    assert "forbidden_host" in str(out["error"])
    assert resolver.calls == [] and dialer.calls == []
    root2 = transport.begin_operation(deadline_at=clock.now + 15.0)
    out = fetch("https://sub.acme.test/", 15.0, operation=root2)
    assert "forbidden_host" in str(out["error"])
    assert resolver.calls == [] and dialer.calls == []


@pytest.mark.parametrize("case_id", ["P46_R01"])
def test_P46_R01_exact_mixed_case_host_allows_one_exchange(tmp_path, case_id):
    client, _transport, _fetch, _resolver, dialer, _, _clock = _scripted_client(
        tmp_path, ["Acme.Test"], port=443
    )
    out = client.preview_check("https://ACME.test/", expect_status=200)
    assert out["matches"] is True and out["status"] == 200
    assert len(dialer.calls) == 1


# ---- P46_R02: every record classified; mixed/malformed/empty refuse ----


@pytest.mark.parametrize("case_id", ["P46_R02"])
def test_P46_R02_all_public_answers_succeed(tmp_path, case_id):
    client, *_ = _scripted_client(
        tmp_path,
        ["acme.test"],
        port=443,
        answers={"acme.test": [_v4(port=443), _v6(port=443)]},
    )
    out = client.preview_check("https://acme.test/")
    assert out["status"] == 200


@pytest.mark.parametrize("case_id", ["P46_R02"])
def test_P46_R02_mixed_malformed_empty_fail_before_dial(tmp_path, case_id):
    inbound = _http_response(200, b"x")
    bad_sets: list = [
        [_v4(port=443), ResolvedAddress(family=2, address="10.0.0.9", port=443)],
        [_v4(port=443), ResolvedAddress(family=2, address="169.254.169.254", port=443)],
        [_v4(port=443), ResolvedAddress(family=2, address="127.0.0.1", port=443)],
        [_v4(port=443), ResolvedAddress(family=10, address="::1", port=443)],
        [_v4(port=443), ResolvedAddress(family=10, address="fe80::1", port=443)],
        [_v4(port=443), ResolvedAddress(family=10, address="ff02::1", port=443)],
        [_v4(port=443), ResolvedAddress(family=10, address="2001:db8::1", port=443)],
        [_v4(port=443), ResolvedAddress(family=2, address="224.0.0.1", port=443)],
        [_v4(port=443), ResolvedAddress(family=2, address="0.0.0.0", port=443)],
        [_v4(port=443), ResolvedAddress(family=2, address="192.168.1.7", port=443)],
        [_v4(port=443), ResolvedAddress(family=2, address="203.0.113.9", port=443)],
        [_v4(port=443), ResolvedAddress(family=2, address="198.51.100.9", port=443)],
        [_v4(port=443), ResolvedAddress(family=2, address="192.0.2.9", port=443)],
        [_v4(port=443), ResolvedAddress(family=2, address="100.64.0.9", port=443)],
        [
            _v4(port=443),
            ResolvedAddress(family=10, address="64:ff9b::808:808", port=443),
        ],
        [_v4(port=443), ResolvedAddress(family=10, address="2002::1", port=443)],
        [_v4(port=443), ResolvedAddress(family=2, address="not-an-ip", port=443)],
        [_v4(port=443), ResolvedAddress(family=2, address=PUBLIC_V4, port=444)],
        [],
    ]
    for answers in bad_sets:
        transport, fetch, _resolver, dialer, _, clock = _harness(
            ["acme.test"], inbound, {"acme.test": answers}
        )
        root = transport.begin_operation(deadline_at=clock.now + 15.0)
        out = fetch("https://acme.test/", 15.0, operation=root)
        assert "error" in out, answers
        assert str(out["error"]).split(":")[0] in ("forbidden_host", "upstream_error")
        assert dialer.calls == [], answers
    transport, fetch, _resolver, dialer, _, clock = _harness(
        ["acme.test"], inbound, {"acme.test": OSError("dns down")}
    )
    root = transport.begin_operation(deadline_at=clock.now + 15.0)
    out = fetch("https://acme.test/", 15.0, operation=root)
    assert "error" in out and dialer.calls == []


# ---- P46_R03: ambiguous authority refuses; canonical positives work ----


@pytest.mark.parametrize("case_id", ["P46_R03"])
def test_P46_R03_ambiguous_authority_refuses_before_resolution(tmp_path, case_id):
    inbound = _http_response(200, b"x")
    transport, fetch, resolver, dialer, _, clock = _harness(["acme.test"], inbound)
    bad_urls = [
        "http://user:pass@acme.test/",
        "http://acme.test:0/",
        "http://acme.test:99999/",
        "http://acme.test./",
        "http://[::1/",
        "http://acme.test\\@evil/",
        "ftp://acme.test/",
        "http://[fe80::1%eth0]/",
        "http://0x7f.1/",
        "http://0177.0.0.1/",
        "http://2130706433/",
        "http://1.2.3/",
        "http://[::FFFF:93.184.216.34]/",
        "http://acme.test:abc/",
        "https://acme.test/\x01",
        "http://exa mple.test/",
    ]
    for url in bad_urls:
        root = transport.begin_operation(deadline_at=clock.now + 15.0)
        out = fetch(url, 15.0, operation=root)
        assert "error" in out, url
        assert str(out["error"]).split(":")[0] in ("invalid_url", "forbidden_host"), url
    assert resolver.calls == [] and dialer.calls == []


@pytest.mark.parametrize("case_id", ["P46_R03"])
def test_P46_R03_canonical_dns_ipv4_ipv6_work(tmp_path, case_id):
    for url, host, answers in [
        ("https://acme.test/", "acme.test", [_v4(port=443)]),
        ("http://93.184.216.34/", "93.184.216.34", [_v4(addr=PUBLIC_V4, port=80)]),
        (
            "https://[2606:2800:220:1:248:1893:25c8:1946]/",
            "2606:2800:220:1:248:1893:25c8:1946",
            [_v6(port=443)],
        ),
    ]:
        client, *_ = _scripted_client(
            tmp_path,
            [host],
            port=80 if ":80" in url or url.startswith("http://") else 443,
            answers={host: answers},
        )
        out = client.preview_check(url)
        assert out["status"] == 200, url


# ---- P46_R04: permit binding, single dial, peer checks ----


@pytest.mark.parametrize("case_id", ["P46_R04"])
def test_P46_R04_single_dial_readmit_foreign_consumed_expired_mismatch(
    tmp_path, case_id
):
    inbound = _http_response(200, b"hello")
    transport, _fetch, resolver, dialer, _, clock = _harness(["acme.test"], inbound)
    root = transport.begin_operation(deadline_at=clock.now + 15.0)
    scope = transport.open_scope(operation=root, deadline_at=clock.now + 15.0)
    req = HttpRequest(url="https://acme.test/", method="GET", headers=(), body=None)
    permit = transport.admit(req, scope=scope)
    assert resolver.calls == ["acme.test"]
    conn = transport.connect(permit, scope=scope)
    assert len(dialer.calls) == 1
    resp = transport.exchange(conn, req, scope=scope, sample_limit=None)
    assert resp.status == 200 and resp.body == b"hello"
    conn.close()
    with pytest.raises(DestinationDenied):
        transport.connect(permit, scope=scope)
    other = DestinationTransport(_policy(["acme.test"]), clock=clock)
    with pytest.raises(DestinationDenied):
        other.connect(permit, scope=scope)
    scope2 = transport.open_scope(operation=root, deadline_at=clock.now + 15.0)
    with pytest.raises(DestinationDenied):
        transport.connect(permit, scope=scope2)
    clock.now += 1_000.0
    with pytest.raises(DestinationDenied):
        root.remaining()
    scope.close()
    scope2.close()


@pytest.mark.parametrize("case_id", ["P46_R04"])
def test_P46_R04_peer_mismatch_sends_no_http(tmp_path, case_id):
    inbound = _http_response(200, b"hello")
    fake_clock = FakeClock()
    transport = DestinationTransport(
        _policy(["acme.test"]),
        resolver=FakeResolver({"acme.test": [_v4(port=443)]}),
        dialer=FakeDialer(inbound, peer_override=("203.0.113.99", 443)),
        tls_verifier=FakeTLS(),
        clock=fake_clock,
    )
    fetch = SafeFetch(transport)
    root = transport.begin_operation(deadline_at=fake_clock.now + 15.0)
    out = fetch("https://acme.test/", 15.0, operation=root)
    assert "error" in out


# ---- P46_R05: wire fidelity + typed decoder ----


@pytest.mark.parametrize("case_id", ["P46_R05"])
def test_P46_R05_framing_tls_decoder_oracles(tmp_path, case_id):
    inbound = _http_response(200, b"hello")
    transport, _fetch, _resolver, _dialer, _tls, clock = _harness(
        ["acme.test"], inbound
    )
    root = transport.begin_operation(deadline_at=clock.now + 15.0)
    scope = transport.open_scope(operation=root, deadline_at=clock.now + 15.0)
    bad = HttpRequest(
        url="https://acme.test/",
        method="GET",
        headers=((b"Content-Length", b"5"), (b"Content-Length", b"6")),
        body=None,
    )
    with pytest.raises(DestinationDenied):
        transport.admit(bad, scope=scope)
    bad2 = HttpRequest(url="https://acme.test/", method="POST", headers=(), body=b"x")
    with pytest.raises(DestinationDenied):
        transport.admit(bad2, scope=scope)
    scope.close()
    from omega_prime.providers.destination import Refusal as _Refusal

    tls_fail = DestinationDenied(
        _Refusal("upstream_error", "tls_verification", "TLS verification failed")
    )
    fail_clock = FakeClock()
    fail_transport = DestinationTransport(
        _policy(["acme.test"]),
        resolver=FakeResolver({"acme.test": [_v4(port=443)]}),
        dialer=FakeDialer(inbound),
        tls_verifier=FakeTLS(fail=tls_fail),
        clock=fail_clock,
    )
    fail_fetch = SafeFetch(fail_transport)
    fail_root = fail_transport.begin_operation(deadline_at=fail_clock.now + 15.0)
    out = fail_fetch("https://acme.test/", 15.0, operation=fail_root)
    assert "error" in out and tls_fail is not None
    # decoder: complete gzip entity
    gz = gzip.compress(b"decoded-bytes")
    raw = _http_response(200, gz, {"Content-Encoding": "gzip"})
    t3, _, _, _, _, c3 = _harness(["acme.test"], raw)
    r3 = t3.begin_operation(deadline_at=c3.now + 15.0)
    s3 = t3.open_scope(operation=r3, deadline_at=c3.now + 15.0)
    req3 = HttpRequest(url="https://acme.test/", method="GET", headers=(), body=None)
    resp3 = t3.fetch(req3, scope=s3, sample_limit=None)
    budget = DecodeBudget(
        decoded_max=1_000_000, base64_max=1_400_000, scratch_max=262_144
    )
    decoded = t3.decode_response(resp3, request=req3, scope=s3, budget=budget)
    assert decoded.body == b"decoded-bytes" and decoded.body_kind == "entity"
    assert decoded.base64_bytes == 4 * ((len(b"decoded-bytes") + 2) // 3)
    with pytest.raises(DestinationDenied):
        t3.decode_response(resp3, request=req3, scope=s3, budget=budget)
    # sampled response refuses decode
    t4, _, _, _, _, c4 = _harness(["acme.test"], _http_response(200, b"z" * 40))
    r4 = t4.begin_operation(deadline_at=c4.now + 15.0)
    s4 = t4.open_scope(operation=r4, deadline_at=c4.now + 15.0)
    req4 = HttpRequest(url="https://acme.test/", method="GET", headers=(), body=None)
    resp4 = t4.fetch(req4, scope=s4, sample_limit=10)
    assert resp4.body_complete is False
    with pytest.raises(DestinationDenied):
        t4.decode_response(resp4, request=req4, scope=s4, budget=budget)
    # negative budget refuses
    with pytest.raises(DestinationDenied):
        t3.decode_response(
            resp3,
            request=req3,
            scope=s3,
            budget=DecodeBudget(decoded_max=-1, base64_max=10, scratch_max=262_144),
        )
    # HEAD bodyless
    head_raw = (
        b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\nContent-Type: text/html\r\n\r\n"
    )
    t5, _, _, _, _, c5 = _harness(["acme.test"], head_raw)
    r5 = t5.begin_operation(deadline_at=c5.now + 15.0)
    s5 = t5.open_scope(operation=r5, deadline_at=c5.now + 15.0)
    req5 = HttpRequest(url="https://acme.test/", method="HEAD", headers=(), body=None)
    resp5 = t5.fetch(req5, scope=s5, sample_limit=None)
    dec5 = t5.decode_response(resp5, request=req5, scope=s5, budget=budget)
    assert dec5.body == b"" and dec5.body_kind == "head"
    # 204 with framing refuses
    bad204 = b"HTTP/1.1 204 No Content\r\nContent-Length: 3\r\n\r\n"
    t6, f6, _, _, _, c6 = _harness(["acme.test"], bad204)
    r6 = t6.begin_operation(deadline_at=c6.now + 15.0)
    out6 = f6("https://acme.test/", 15.0, operation=r6)
    assert "error" in out6
    # multiple Location refuses
    multi_loc = b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nLocation: https://a.test/\r\nLocation: https://b.test/\r\n\r\nx"
    t7, f7, _, _, _, c7 = _harness(["acme.test"], multi_loc)
    r7 = t7.begin_operation(deadline_at=c7.now + 15.0)
    assert "error" in f7("https://acme.test/", 15.0, operation=r7)


# ---- P46_R06: no follow, no proxy, no upgrade ----


@pytest.mark.parametrize("case_id", ["P46_R06"])
def test_P46_R06_redirects_never_followed_proxy_ignored(tmp_path, case_id, monkeypatch):
    for status in (301, 302, 303, 307, 308):
        raw = _http_response(status, b"", {"Location": "https://evil.test/"})
        client, _transport, _fetch, _resolver, dialer, _, _clock = _scripted_client(
            tmp_path, ["acme.test"], port=443, answers={"acme.test": [_v4(port=443)]}
        )
        dialer.inbound = bytes(raw)
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
        monkeypatch.setenv("https_proxy", "http://127.0.0.1:9999")
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
        out = client.preview_check("https://acme.test/")
        assert out["status"] == status and out["redirect"] == "https://evil.test/"
        assert len(dialer.calls) == 1
        for sock in dialer.sockets:
            assert b"evil.test" not in bytes(sock.sent)
    raw101 = b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
    client, _t, _f, _r, dialer101, _tls, _c = _scripted_client(tmp_path, ["acme.test"])
    dialer101.inbound = bytes(raw101)
    out = client.preview_check("https://acme.test/")
    assert "error" in out


@pytest.mark.parametrize("case_id", ["P46_R06"])
def test_P46_R06_preview_headers_hash_sample_preserved(tmp_path, case_id):
    body = b"y" * 100
    client, _transport, _fetch, _resolver, dialer, _tls, _clock = _scripted_client(
        tmp_path, ["acme.test"], body=body
    )
    out = client.preview_check("https://acme.test/")
    sent = bytes(dialer.sockets[0].sent).decode("latin1")
    assert "User-Agent: omega-prime/preview-check" in sent
    assert "Accept-Encoding: identity" in sent
    assert out["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert out["bytes"] == len(body)


# ---- P46_R07: operation ownership, abort binding (sync partition) ----


@pytest.mark.parametrize("case_id", ["P46_R07"])
def test_P46_R07_abort_foreign_finished_mismatched_refuse_before_dns(tmp_path, case_id):
    inbound = _http_response(200, b"x")
    transport, fetch, resolver, dialer, _, clock = _harness(["acme.test"], inbound)
    root = transport.begin_operation(deadline_at=clock.now + 15.0)
    root.abort_handle.abort("cancelled")
    out = fetch("https://acme.test/", 15.0, operation=root)
    assert "error" in out and resolver.calls == [] and dialer.calls == []
    foreign_clock = FakeClock()
    foreign = DestinationTransport(_policy(["acme.test"]), clock=foreign_clock)
    foreign_root = foreign.begin_operation(deadline_at=foreign_clock.now + 15.0)
    out = fetch("https://acme.test/", 15.0, operation=foreign_root)
    assert "not_configured" in str(out["error"]) and resolver.calls == []
    root2 = transport.begin_operation(deadline_at=clock.now + 15.0)
    transport.finish_operation(root2, deadline_at=clock.now + 20.0)
    out = fetch("https://acme.test/", 15.0, operation=root2)
    assert "error" in out and resolver.calls == []


@pytest.mark.parametrize("case_id", ["P46_R07"])
def test_P46_R07_owned_preview_binds_resets_and_finishes(tmp_path, case_id):
    client, transport, _fetch, _resolver, _dialer, _tls, _clock = _scripted_client(
        tmp_path, ["acme.test"]
    )
    assert transport.current_operation() is None
    out = client.preview_check("https://acme.test/")
    assert out["status"] == 200
    assert transport.current_operation() is None


@pytest.mark.parametrize("case_id", ["P46_R07"])
def test_P46_R07_unconfigured_preview_denies(tmp_path, case_id):
    bare = WebClient(WebContext(root=tmp_path))
    out = bare.preview_check("https://acme.test/")
    assert "not_configured" in out["error"]


# ---- P46_R12_FETCH: loopback omega-rebind refuses with zero private requests ----


@pytest.mark.parametrize("case_id", ["P46_R12_FETCH"])
def test_P46_R12_FETCH_omega_rebind_loopback_refuses(tmp_path, case_id):
    inbound = _http_response(200, b"private")
    loopback = ResolvedAddress(family=2, address="127.0.0.1", port=80)
    transport, fetch, _resolver, dialer, _, clock = _harness(
        ["omega-rebind.example"], inbound, {"omega-rebind.example": [loopback]}
    )
    root = transport.begin_operation(deadline_at=clock.now + 15.0)
    out = fetch("http://omega-rebind.example/", 15.0, operation=root)
    private_server_requests: list = []
    private_response_received = False
    assert "error" in out
    assert str(out["error"]).split(":")[0] in ("forbidden_host", "upstream_error")
    assert dialer.calls == []
    assert private_server_requests == [] and private_response_received is False


# ---- opt-in runtime entrypoint (Main only; never run by default pytest) ----


def _runtime_r12_fetch() -> dict:
    """Real loopback server; production preview must refuse with zero hits."""
    hits: list = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            body = b"private"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    admitted = False
    refusal: str | None = None
    try:
        transport = DestinationTransport(_policy(["omega-rebind.example"]))
        clock = transport._clock
        root = transport.begin_operation(deadline_at=clock.monotonic() + 15.0)
        scope = transport.open_scope(
            operation=root, deadline_at=clock.monotonic() + 15.0
        )
        req = HttpRequest(
            url=f"http://127.0.0.1:{port}/", method="GET", headers=(), body=None
        )
        try:
            transport.admit(req, scope=scope)
            admitted = True
        except DestinationDenied as exc:
            admitted = False
            refusal = f"{exc.refusal.public_code}: {exc.refusal.message}"
        scope.close()
        transport.finish_operation(root, deadline_at=clock.monotonic() + 5.0)
    finally:
        server.shutdown()
        thread.join(timeout=5)
    return {
        "case": "P46-R12-FETCH",
        "admitted": admitted,
        "refusal": None if admitted else refusal,
        "private_server_requests": [str(h) for h in hits],
        "private_response_received": bool(hits),
    }


def _runtime_numeric() -> dict:
    """Real numeric dial against a closed loopback port must fail safe."""
    transport = DestinationTransport(_policy(["example.test"]))
    clock = transport._clock
    root = transport.begin_operation(deadline_at=clock.monotonic() + 15.0)
    scope = transport.open_scope(operation=root, deadline_at=clock.monotonic() + 10.0)
    req = HttpRequest(
        url="http://93.184.216.34:9/", method="GET", headers=(), body=None
    )
    outcome = "refused-before-dial"
    try:
        permit = transport.admit(req, scope=scope)
        try:
            conn = transport.connect(permit, scope=scope)
            conn.close()
            outcome = "connected"
        except DestinationDenied as exc:
            outcome = f"dial-refused: {exc.refusal.internal_reason}"
    except DestinationDenied as exc:
        outcome = f"admit-refused: {exc.refusal.internal_reason}"
        permit = None
    finally:
        scope.close()
        transport.finish_operation(root, deadline_at=clock.monotonic() + 5.0)
    return {
        "case": "P46-R04/R05-numeric",
        "outcome": outcome,
        "admitted": permit is not None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omega_prime.tests.test_web")
    parser.add_argument(
        "--runtime",
        nargs="*",
        default=[],
        help="opt-in real-socket cases: P46-R04 P46-R05 P46-R12-FETCH",
    )
    args = parser.parse_args(argv)
    wanted = set(args.runtime)
    if not wanted:
        print(json.dumps({"error": "no runtime cases requested"}))
        return 2
    receipts: list[dict] = []
    failures: list[str] = []
    if "P46-R12-FETCH" in wanted:
        receipt = _runtime_r12_fetch()
        receipts.append(receipt)
        if (
            receipt["admitted"]
            or receipt["private_server_requests"] != []
            or receipt["private_response_received"] is not False
        ):
            failures.append("P46-R12-FETCH: loopback was reached or admitted")
    if "P46-R04" in wanted or "P46-R05" in wanted:
        receipt = _runtime_numeric()
        receipts.append(receipt)
        if receipt["outcome"] == "connected":
            failures.append("P46-R04/R05: unexpected connection to closed port")
    for receipt in receipts:
        print(
            json.dumps(
                {
                    k: (v if k != "refusal" or v is None else "***")
                    for k, v in receipt.items()
                }
            )
        )
    if failures:
        for failure in failures:
            print(json.dumps({"failure": failure}), file=sys.stderr)
        return 1
    if wanted - {"P46-R04", "P46-R05", "P46-R12-FETCH"}:
        print(json.dumps({"error": f"unknown runtime cases: {sorted(wanted)}"}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
