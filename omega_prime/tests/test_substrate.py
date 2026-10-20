"""Phase 39: substrate client + memory bridge, post_text transport."""

from __future__ import annotations

import contextlib
import http.client
import json
import socket
import threading
import uuid

import pytest

from omega_prime.providers.base import ProviderError
from omega_prime.providers.http import HttpTransport
from omega_prime.substrate.client import (
    SubstrateClient,
    SubstrateError,
    SubstrateProtocolError,
    SubstrateUnreachableError,
)

TOKEN = "test-mock-token"


class _FakeTransport:
    """Scripted transport: results or exceptions per call, requests recorded."""

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.requests: list[dict] = []

    def _next(self, method: str, url: str, headers: dict, body: dict):
        self.requests.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body}
        )
        if not self._script:
            raise AssertionError("fake transport script exhausted")
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def post_mcp(self, url: str, headers: dict, body: dict):
        outcome = self._next("POST", url, headers, body)
        return _FakeMcpResponse(outcome)

    def post(self, url: str, headers: dict, body: dict):
        return self._next("POST", url, headers, body)

    def post_text(self, url: str, headers: dict, body: dict):
        return self._next("POST", url, headers, body)

    def get(self, url: str, headers: dict, params: dict):
        return self._next("GET", url, headers, params)


class _FakeMcpResponse:
    def __init__(self, payload: dict, status: int = 200, headers: dict | None = None):
        self.status = status
        self.headers = {"content-type": "application/json", **(headers or {})}
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, size: int = 4096):
        body, self._body = self._body[:size], self._body[size:]
        return body


class _FakeBroker:
    """Canned token; redact replaces the token with [REDACTED]."""

    def __init__(self, token: str = TOKEN) -> None:
        self._token = token
        self.key_requests: list[tuple] = []

    def key_for(self, provider, url: str) -> str:
        self.key_requests.append((provider.name, url))
        if self._token == "":
            return ""
        if self._token is None:
            raise ProviderError("no credential for substrate (set SUBSTRATE_TOKEN)")
        return self._token

    def redact(self, text):
        if isinstance(text, str) and self._token:
            return text.replace(self._token, "[REDACTED]")
        return text


def _mcp_result(payload: dict, request_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
    }


def _client(
    script: list, token: str | None = TOKEN
) -> tuple[SubstrateClient, _FakeTransport]:
    transport = _FakeTransport(script)
    broker = None if token == "none" else _FakeBroker("" if token is None else token)
    return SubstrateClient("local", transport=transport, broker=broker), transport


def test_brief_sends_surface_and_auth() -> None:
    client, transport = _client(["# brief\n"])
    assert client.brief(repo="o/n", graph_id="ut-1") == "# brief\n"
    request = transport.requests[0]
    assert request["url"] == "http://127.0.0.1:7410/brief"
    assert request["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert request["body"] == {
        "surface": "grok-bot",
        "repo": "o/n",
        "graph_id": "ut-1",
    }


def test_brief_fail_open_on_transport_error() -> None:
    client, _ = _client([ProviderError("boom")])
    assert client.brief() == ""


def test_explicit_token_wins_and_redacts() -> None:
    transport = _FakeTransport(["# brief\n"])
    client = SubstrateClient("local", transport=transport, token="explicit-abc")
    assert client.brief() == "# brief\n"
    assert transport.requests[0]["headers"] == {"Authorization": "Bearer explicit-abc"}
    failing = SubstrateClient(
        "local",
        transport=_FakeTransport([ProviderError("bad explicit-abc")]),
        token="explicit-abc",
    )
    with pytest.raises(SubstrateError) as exc:
        failing.health()
    assert "explicit-abc" not in str(exc.value)
    with pytest.raises(ValueError):
        # Intentional misuse: the token must be a string.
        SubstrateClient(token=42)  # type: ignore[arg-type]


def test_brief_without_broker_sends_no_auth() -> None:
    client, transport = _client(["# brief\n"], token="none")
    assert client.brief() == "# brief\n"
    assert transport.requests[0]["headers"] == {}


def test_emit_success_passes_through() -> None:
    stored = {"stored": True, "id": "e1", "ts": "t", "hash": "h"}
    client, transport = _client([stored])
    assert client.emit("tool.call", "ran ls", session_id="s1") == stored
    assert transport.requests[0]["body"]["surface"] == "grok-bot"
    assert transport.requests[0]["body"]["session_id"] == "s1"


def test_emit_rejects_bad_kind_and_summary() -> None:
    client, _ = _client([{}])
    with pytest.raises(ValueError):
        client.emit("nope", "summary")
    with pytest.raises(ValueError):
        client.emit("note", "")


def test_emit_fail_open_with_reason() -> None:
    client, _ = _client([ProviderError("connection refused")])
    result = client.emit("note", "hello")
    assert result["stored"] is False
    assert result == {"stored": False, "error": "substrate transport failed"}


@pytest.mark.parametrize("state", ["skipped", "invalid", "degraded"])
def test_blocked_saved_binding_never_falls_back(tmp_path, state: str) -> None:
    from omega_prime.integrations.binding import fresh_binding, save_binding

    env = {"OMEGA_PRIME_STATE_DIR": str(tmp_path)}
    binding = fresh_binding()
    binding["gateways"]["substrate"]["state"] = state
    binding["gateways"]["substrate"]["url"] = "https://saved.example"
    save_binding(binding, env)
    transport = _FakeTransport([])
    client = SubstrateClient(
        "local", transport=transport, honor_binding=True, binding_env=env
    )
    assert client.brief() == ""
    assert transport.requests == []


def test_ready_saved_binding_precedes_constructor_url(tmp_path) -> None:
    from omega_prime.integrations.binding import fresh_binding, save_binding

    env = {"OMEGA_PRIME_STATE_DIR": str(tmp_path)}
    binding = fresh_binding()
    binding["gateways"]["substrate"].update(
        {"state": "ready", "url": "https://saved.example"}
    )
    save_binding(binding, env)
    transport = _FakeTransport(["brief"])
    client = SubstrateClient(
        "local", transport=transport, honor_binding=True, binding_env=env
    )
    assert client.brief() == "brief"
    assert transport.requests[0]["url"] == "https://saved.example/brief"


def test_memory_write_statuses_pass_through() -> None:
    for status in ("accepted", "conflict", "quarantined", "denied"):
        payload = {"status": status, "reason": "r", "remedy": "fix"}
        client, transport = _client([_mcp_result(payload)])
        result = client.memory_write("graph:g1", "fact", "sky is blue")
        assert result["status"] == status
        envelope = transport.requests[0]["body"]
        assert envelope["method"] == "tools/call"
        assert envelope["params"]["name"] == "memory_write"
        arguments = envelope["params"]["arguments"]
        assert arguments["scope"] == "graph:g1"
        assert "expected_version" not in arguments
        generated_key = uuid.UUID(arguments["idempotency_key"])
        assert generated_key.version == 4


def test_memory_write_preserves_optional_version_and_key() -> None:
    client, transport = _client([_mcp_result({"status": "accepted"})])
    client.memory_write(
        "graph:g1",
        "fact",
        "sky is blue",
        expected_version=7,
        idempotency_key="caller-key",
    )
    arguments = transport.requests[0]["body"]["params"]["arguments"]
    assert arguments["expected_version"] == 7
    assert arguments["idempotency_key"] == "caller-key"


def test_memory_write_forwards_optional_version_and_idempotency() -> None:
    client, transport = _client([_mcp_result({"status": "accepted"})])
    client.memory_write(
        "graph:g1",
        "fact",
        "remember this",
        idempotency_key="operation-1",
        expected_version=7,
    )
    arguments = transport.requests[0]["body"]["params"]["arguments"]
    assert arguments == {
        "scope": "graph:g1",
        "kind": "fact",
        "text": "remember this",
        "idempotency_key": "operation-1",
        "expected_version": 7,
    }


def test_memory_search_forwards_repository_filter() -> None:
    client, transport = _client([_mcp_result({"entries": []})])
    assert client.memory_search("question", scope="team", limit=4, repo="repo-a") == []
    arguments = transport.requests[0]["body"]["params"]["arguments"]
    assert arguments == {
        "query": "question",
        "scope": "team",
        "limit": 4,
        "repo": "repo-a",
    }


def test_memory_write_fail_closed() -> None:
    client, _ = _client([ProviderError("down")])
    with pytest.raises(SubstrateError):
        client.memory_write("graph:g1", "fact", "x")


def test_memory_write_validates_inputs() -> None:
    client, _ = _client([{}])
    with pytest.raises(ValueError):
        client.memory_write("", "fact", "x")
    with pytest.raises(ValueError):
        client.memory_write("graph:g1", "nope", "x")
    with pytest.raises(ValueError):
        client.memory_write("graph:g1", "fact", "")


def test_memory_search_entries_and_errors() -> None:
    entries = [{"id": "m1", "text": "sky is blue"}]
    client, transport = _client([_mcp_result({"entries": entries})])
    assert client.memory_search("sky") == entries
    assert transport.requests[0]["body"]["params"]["arguments"] == {"query": "sky"}

    with_repo, repo_transport = _client([_mcp_result({"entries": entries})])
    assert with_repo.memory_search("sky", repo="owner/project") == entries
    assert repo_transport.requests[0]["body"]["params"]["arguments"] == {
        "query": "sky",
        "repo": "owner/project",
    }
    failing, _ = _client([ProviderError("down")])
    with pytest.raises(SubstrateUnreachableError):
        failing.memory_search("sky")
    with pytest.raises(ValueError):
        client.memory_search("")


def test_malformed_mcp_envelope() -> None:
    client, _ = _client([{"jsonrpc": "2.0", "id": 1}])
    with pytest.raises(SubstrateError):
        client.memory_write("graph:g1", "fact", "x")
    searching, _ = _client([{"nope": True}])
    with pytest.raises(SubstrateProtocolError):
        searching.memory_search("sky")


def test_base_url_validation_and_unsafe_url_degradation() -> None:
    for unsafe in (
        "gopher://x",
        "http://h:7410",
        "https://user:pw@example.test",
        "https://bad:port",
    ):
        client = SubstrateClient(base_url=unsafe, transport=_FakeTransport([]))
        assert client.brief() == ""
        assert client._resolve_base() == (None, "configured substrate URL is unsafe")
    assert SubstrateClient(base_url="https://h:7410/").base_url == "https://h:7410"
    assert SubstrateClient(base_url="local").base_url == "http://127.0.0.1:7410"


def test_default_client_is_unconfigured_and_does_not_call_transport() -> None:
    transport = _FakeTransport([])
    client = SubstrateClient(transport=transport)
    assert client.brief() == ""
    assert client.emit("prompt", "test") == {
        "stored": False,
        "error": "not_configured: substrate gateway is not configured",
    }
    with pytest.raises(SubstrateError, match="not configured"):
        client.memory_write("g", "fact", "text")
    assert transport.requests == []


def test_token_never_leaks_into_errors_or_results() -> None:
    failing, _ = _client([ProviderError(f"bad auth {TOKEN}")])
    with pytest.raises(SubstrateError) as health_exc:
        failing.health()
    assert TOKEN not in str(health_exc.value)
    emitting, _ = _client([ProviderError(f"bad auth {TOKEN}")])
    assert TOKEN not in emitting.emit("note", "hi")["error"]
    writing, _ = _client([ProviderError(f"bad auth {TOKEN}")])
    with pytest.raises(SubstrateError) as write_exc:
        writing.memory_write("graph:g1", "fact", "x")
    assert TOKEN not in str(write_exc.value)


def test_docs_search_envelope_and_errors() -> None:
    body = {"ok": True, "status": 200, "body": {"chunks": []}}
    client, transport = _client([_mcp_result(body)])
    assert client.docs_search("a question") == body
    envelope = transport.requests[0]["body"]
    assert envelope["params"]["name"] == "docs_search"
    assert envelope["params"]["arguments"] == {"question": "a question"}
    with pytest.raises(ValueError):
        client.docs_search("")
    failing, _ = _client([ProviderError("down")])
    with pytest.raises(SubstrateError):
        failing.docs_search("q")
    malformed, _ = _client([{"nope": True}])
    with pytest.raises(SubstrateError):
        malformed.docs_search("q")


def _mcp_text(text: str, request_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": text}]},
    }


def test_graph_claim_lease_and_validation() -> None:
    lease = {
        "ok": True,
        "graph_id": "g",
        "node_id": "n",
        "lease_id": "lease-123",
        "token": "t",
        "expires": "e",
    }
    client, transport = _client([_mcp_result(lease)])
    result = client.graph_claim("g", "n", "s1", 60)
    assert result == lease
    assert result["lease_id"] == "lease-123"
    envelope = transport.requests[0]["body"]
    assert envelope["params"]["name"] == "graph_claim"
    assert envelope["params"]["arguments"] == {
        "graph_id": "g",
        "node_id": "n",
        "session_id": "s1",
        "surface": "grok-bot",
        "ttl_seconds": 60,
    }
    with pytest.raises(ValueError):
        client.graph_claim("", "n", "s1")
    with pytest.raises(ValueError):
        client.graph_claim("g", "", "s1")
    with pytest.raises(ValueError):
        client.graph_claim("g", "n", "")
    with pytest.raises(ValueError):
        client.graph_claim("g", "n", "s1", True)
    failing, _ = _client([ProviderError("down")])
    with pytest.raises(SubstrateError):
        failing.graph_claim("g", "n", "s1")


def test_graph_release_complete_text_and_heartbeat() -> None:
    client, transport = _client(
        [
            _mcp_text("Released g/n.", request_id=1),
            _mcp_text("Completed g/n.", request_id=2),
            _mcp_result({"ok": True}, request_id=3),
        ]
    )
    assert client.graph_release("g", "n", "lease-1") == "Released g/n."
    assert client.graph_complete("g", "n", "lease-2") == "Completed g/n."
    assert client.graph_heartbeat("g", "n", "lease-3") == {"ok": True}
    assert [r["body"]["params"]["name"] for r in transport.requests] == [
        "graph_release",
        "graph_complete",
        "graph_heartbeat",
    ]
    assert [r["body"]["params"]["arguments"] for r in transport.requests] == [
        {"graph_id": "g", "node_id": "n", "lease_id": "lease-1"},
        {"graph_id": "g", "node_id": "n", "lease_id": "lease-2"},
        {"graph_id": "g", "node_id": "n", "lease_id": "lease-3"},
    ]
    with pytest.raises(ValueError):
        client.graph_release("", "n", "lease")
    with pytest.raises(ValueError):
        client.graph_release("g", "n", "")
    with pytest.raises(ValueError):
        client.graph_complete("g", "n", "")
    with pytest.raises(ValueError):
        client.graph_heartbeat("g", "n", "")
    failing, _ = _client([ProviderError("down")])
    with pytest.raises(SubstrateError):
        failing.graph_release("g", "n", "lease")
    malformed, _ = _client([{"nope": True}])
    with pytest.raises(SubstrateError):
        malformed.graph_complete("g", "n", "lease")


def test_health_success() -> None:
    body = {"index": True, "greptime": True, "eventsWritable": True}
    client, transport = _client([body])
    assert client.health() == body
    assert transport.requests[0]["url"] == "http://127.0.0.1:7410/healthz"


class _PairedConnection(http.client.HTTPConnection):
    """http.client framing over a socketpair end instead of TCP."""

    def __init__(self, sock: socket.socket, timeout: float) -> None:
        super().__init__("fixture.local", 80, timeout=timeout)
        self._paired = sock

    def connect(self) -> None:
        self.sock = self._paired


class _TextFixture:
    """Scripted HTTP peer serving raw text bodies over socketpairs."""

    def __init__(self, statuses: list[int], body: bytes) -> None:
        self.statuses = list(statuses)
        self.body = body
        self.requests: list[dict] = []
        self._lock = threading.Lock()

    def connect(self, host: str, port: int, timeout: float, use_tls: bool):
        client, server = socket.socketpair()
        worker = threading.Thread(target=self._serve, args=(server,), daemon=True)
        worker.start()
        return _PairedConnection(client, timeout)

    def _serve(self, sock: socket.socket) -> None:
        sock.settimeout(10)
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = sock.recv(4096)
                if not chunk:
                    return
                head += chunk
            header, rest = head.split(b"\r\n\r\n", 1)
            lines = header.decode("latin-1").split("\r\n")
            length = 0
            for line in lines[1:]:
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
            body = rest
            while len(body) < length:
                more = sock.recv(length - len(body))
                if not more:
                    break
                body += more
            with self._lock:
                self.requests.append(
                    {"target": lines[0], "body": json.loads(body or b"{}")}
                )
                status = (
                    self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
                )
            payload = self.body
            sock.sendall(
                f"HTTP/1.1 {status} X\r\nContent-Length: {len(payload)}"
                "\r\nContent-Type: text/markdown\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
        finally:
            with contextlib.suppress(OSError):
                sock.close()


def test_post_text_round_trip_over_real_bytes() -> None:
    fixture = _TextFixture([200], b"# brief\n- one\n")
    transport = HttpTransport(connect=fixture.connect, backoff=0)
    assert (
        transport.post_text("http://fixture.local/brief", {}, {"surface": "grok-bot"})
        == "# brief\n- one\n"
    )
    assert fixture.requests[0]["target"] == "POST /brief HTTP/1.1"
    assert fixture.requests[0]["body"] == {"surface": "grok-bot"}


def test_post_text_retries_then_returns() -> None:
    fixture = _TextFixture([500, 200], b"ok")
    transport = HttpTransport(connect=fixture.connect, backoff=0)
    assert transport.post_text("http://fixture.local/brief", {}, {}) == "ok"
    assert len(fixture.requests) == 2


def test_post_text_client_error_raises() -> None:
    fixture = _TextFixture([404], b"missing")
    transport = HttpTransport(connect=fixture.connect, backoff=0)
    with pytest.raises(ProviderError):
        transport.post_text("http://fixture.local/brief", {}, {})
