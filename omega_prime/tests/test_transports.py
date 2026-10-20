"""Phase 16: stdlib HTTP transport and trace export."""

from __future__ import annotations

import contextlib
import http.client
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from omega_prime.agent.conversation_loop import Agent, run_conversation
from omega_prime.agent.model import ScriptedModel
from omega_prime.audit.trace import Tracer
from omega_prime.policy.policy import SeatPolicy
from omega_prime.providers.base import ProviderError, ProviderModel
from omega_prime.providers.fake import FakeTransport
from omega_prime.providers.grok import GrokProvider
from omega_prime.providers.http import HttpTransport
from omega_prime.substrate.client import _matching_sse_message
from omega_prime.tools.coding import register_coding_tools
from omega_prime.tools.registry import ToolRegistry


def _tool_call(name: str, arguments: str = "{}", call_id: str = "call-1") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }


class _PairedConnection(http.client.HTTPConnection):
    """http.client framing over a socketpair end instead of TCP."""

    def __init__(self, sock: socket.socket, timeout: float) -> None:
        super().__init__("fixture.local", 80, timeout=timeout)
        self._paired = sock

    def connect(self) -> None:
        self.sock = self._paired


class _Fixture:
    """Scripted HTTP peer over socketpairs: real request/response bytes."""

    def __init__(
        self,
        statuses: list[int],
        payload: dict,
        headers: list[str] | None = None,
        raw_bodies: list[bytes] | None = None,
    ) -> None:
        self.statuses = list(statuses)
        self.payload = payload
        self.headers = list(headers or [])
        self.raw_bodies = list(raw_bodies or [])
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
            self.requests.append(
                {
                    "target": lines[0],
                    "headers": {
                        key.lower(): value.strip()
                        for line in lines[1:]
                        if ":" in line
                        for key, value in [line.split(":", 1)]
                    },
                    "body": json.loads(body or b"{}"),
                }
            )
            status = self.statuses.pop(0) if self.statuses else 200
            extra = self.headers.pop(0) if self.headers else ""
            raw_body = self.raw_bodies.pop(0) if self.raw_bodies else None
            if raw_body is not None:
                data = raw_body
            else:
                data = json.dumps(self.payload).encode("utf-8")
            sock.sendall(
                f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(data)}\r\nConnection: close\r\n{extra}\r\n".encode()
                + data
            )
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError):
                sock.close()


def _payload() -> dict:
    return {"choices": [{"message": {"content": "live"}, "finish_reason": "stop"}]}


def test_http_transport_retries_a_dropped_first_attempt():
    fixture = _Fixture([503, 200], _payload())
    provider = GrokProvider()
    provider.base_url = "http://fixture.local"
    transport = HttpTransport(
        timeout=5, max_retries=2, backoff=0.01, connect=fixture.connect
    )
    model = ProviderModel(provider, "grok-4", transport, api_key="fake-key")
    row = model.complete([{"role": "user", "content": "ping"}])

    assert row["content"] == "live"
    assert len(fixture.requests) == 2
    assert fixture.requests[0]["target"] == "POST /chat/completions HTTP/1.1"
    assert fixture.requests[0]["body"]["model"] == "grok-4"


def test_http_transport_refuses_blocked_hosts_and_gives_up():
    fixture = _Fixture([], _payload())
    policy = SeatPolicy({"version": 1, "network": {"hosts": ["api.x.ai"]}})
    blocked = HttpTransport(policy=policy, timeout=5, connect=fixture.connect)
    with pytest.raises(ProviderError, match="network blocked to host"):
        blocked.post("http://evil.example/chat/completions", {}, {"model": "m"})
    assert fixture.requests == []

    def refuse(host: str, port: int, timeout: float, use_tls: bool):
        raise ConnectionRefusedError("closed")

    refused = HttpTransport(timeout=2, max_retries=1, backoff=0.01, connect=refuse)
    with pytest.raises(ProviderError, match="failed after retries"):
        refused.post("http://fixture.local/chat/completions", {}, {"model": "m"})

    missing = _Fixture([404], _payload())
    client = HttpTransport(
        timeout=5, max_retries=3, backoff=0.01, connect=missing.connect
    )
    with pytest.raises(ProviderError, match="HTTP 404"):
        client.post("http://fixture.local/chat/completions", {}, {"model": "m"})
    assert len(missing.requests) == 1


def test_http_transport_retries_408_and_429_then_succeeds():
    fixture = _Fixture([408, 429, 200], _payload())
    transport = HttpTransport(
        timeout=5, max_retries=3, backoff=0.01, connect=fixture.connect
    )
    row = transport.post("http://fixture.local/chat/completions", {}, {"model": "m"})

    assert row["choices"][0]["message"]["content"] == "live"
    assert len(fixture.requests) == 3


def test_http_transport_honors_retry_after_up_to_the_cap(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    fixture = _Fixture(
        [429, 429, 200], _payload(), headers=["Retry-After: 5\r\n", "", ""]
    )
    transport = HttpTransport(
        timeout=5, max_retries=3, backoff=0.01, connect=fixture.connect
    )
    transport.post("http://fixture.local/chat/completions", {}, {"model": "m"})

    assert sleeps == [5.0, 0.01 * 2]

    capped_sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", capped_sleeps.append)
    capped = _Fixture([429, 200], _payload(), headers=["Retry-After: 999\r\n", ""])
    retrying = HttpTransport(
        timeout=5, max_retries=1, backoff=0.01, connect=capped.connect
    )
    retrying.post("http://fixture.local/chat/completions", {}, {"model": "m"})

    assert capped_sleeps == [60.0]


def test_http_transport_gives_up_on_persistent_429s():
    fixture = _Fixture([429, 429, 429], _payload())
    transport = HttpTransport(
        timeout=5, max_retries=2, backoff=0.01, connect=fixture.connect
    )
    with pytest.raises(ProviderError, match="failed after retries"):
        transport.post("http://fixture.local/chat/completions", {}, {"model": "m"})
    assert len(fixture.requests) == 3


def test_http_transport_never_sleeps_when_backoff_is_zero(monkeypatch):
    def _boom(seconds: float) -> None:
        raise AssertionError(f"slept {seconds}s with backoff=0")

    monkeypatch.setattr(time, "sleep", _boom)
    fixture = _Fixture([429, 200], _payload(), headers=["Retry-After: 30\r\n", ""])
    transport = HttpTransport(
        timeout=5, max_retries=1, backoff=0, connect=fixture.connect
    )
    row = transport.post("http://fixture.local/chat/completions", {}, {"model": "m"})

    assert row["choices"][0]["message"]["content"] == "live"
    assert len(fixture.requests) == 2


def test_traces_cover_tools_policy_and_model_calls(tmp_path: Path):
    tracer = Tracer("trace-1")
    policy = SeatPolicy({"version": 1, "tools": {"allow": ["read_file"]}})
    registry = ToolRegistry(policy=policy, tracer=tracer)
    registry.register(
        "read_file", "d", {"type": "object", "properties": {}}, lambda: "ok"
    )
    registry.register(
        "write_file", "d", {"type": "object", "properties": {}}, lambda: "ok"
    )

    registry.dispatch("read_file", {})
    registry.dispatch("write_file", {})

    kinds = [(span["kind"], span["name"]) for span in tracer.spans()]
    assert kinds == [
        ("tool", "read_file"),
        ("tool", "write_file"),
        ("policy", "write_file"),
    ]
    assert tracer.spans()[0]["fields"] == {"verdict": "allowed"}
    assert tracer.spans()[2]["fields"]["verdict"] == "denied"

    root = tmp_path / "ws"
    (root / "prompts").mkdir(parents=True)
    workspace_tracer = Tracer()
    file_policy = SeatPolicy(
        {"version": 1, "paths": {"read_only": ["prompts/**"]}, "network": {}}
    )
    file_registry = ToolRegistry()
    register_coding_tools(
        file_registry, root, policy=file_policy, tracer=workspace_tracer
    )
    file_registry.dispatch("write_file", {"path": "prompts/x.md", "content": "x"})
    policy_spans = [
        span for span in workspace_tracer.spans() if span["kind"] == "policy"
    ]
    assert len(policy_spans) == 1
    assert policy_spans[0]["fields"]["verdict"] == "denied"

    turn_tracer = Tracer()
    model = ScriptedModel(
        [_tool_call("echo"), {"role": "assistant", "content": "done"}]
    )
    agent = Agent(
        model=model,
        tools={"echo": lambda: "pong"},
        max_iterations=4,
        tracer=turn_tracer,
    )
    run_conversation(agent, "ping")

    exported = turn_tracer.to_dict()
    assert exported["trace_id"] == turn_tracer.trace_id
    assert [span["seq"] for span in exported["spans"]] == [1, 2]
    assert all(span["kind"] == "model" for span in exported["spans"])
    assert all(span["duration_ms"] >= 0 for span in exported["spans"])
    assert all(span["ts"].endswith("Z") for span in exported["spans"])


def test_model_spans_carry_usage_when_the_model_reports_it():
    scripted = FakeTransport(
        script=[
            {
                "choices": [{"message": {"content": "done"}}],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 2,
                    "total_tokens": 10,
                },
            }
        ]
    )
    tracer = Tracer()
    agent = Agent(
        model=ProviderModel(GrokProvider(), "grok-4", scripted, api_key="k"),
        tools={},
        max_iterations=4,
        tracer=tracer,
    )
    result = run_conversation(agent, "ping")

    assert result["final_response"] == "done"
    spans = [span for span in tracer.spans() if span["kind"] == "model"]
    assert len(spans) == 1
    assert spans[0]["fields"]["usage"] == {
        "prompt_tokens": 8,
        "completion_tokens": 2,
        "total_tokens": 10,
    }


def test_model_spans_omit_usage_when_absent():
    tracer = Tracer()
    agent = Agent(
        model=ScriptedModel([{"role": "assistant", "content": "done"}]),
        tools={},
        max_iterations=4,
        tracer=tracer,
    )
    run_conversation(agent, "ping")

    spans = [span for span in tracer.spans() if span["kind"] == "model"]
    assert len(spans) == 1
    assert "usage" not in spans[0]["fields"]


def test_http_transport_streams_lines_and_retries_status_errors():
    sse = (
        b'data: {"choices": [{"delta": {"content": "a"}}]}\n\n'
        b": keep-alive\n\n"
        b'data: {"choices": [{"delta": {"content": "b"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    fixture = _Fixture([429, 200], {}, raw_bodies=[b"{}", sse])
    transport = HttpTransport(
        timeout=5, max_retries=1, backoff=0, connect=fixture.connect
    )
    lines = list(
        transport.stream("http://fixture.local/chat/completions", {}, {"model": "m"})
    )

    assert [line for line in lines if line.startswith("data:")] == [
        'data: {"choices": [{"delta": {"content": "a"}}]}',
        'data: {"choices": [{"delta": {"content": "b"}}]}',
        "data: [DONE]",
    ]
    assert len(fixture.requests) == 2


def test_http_transport_post_text_preserves_json_request_contract():
    body = {"surface": "grok-bot"}
    fixture = _Fixture([200], {}, raw_bodies=[b"# brief\n"])
    transport = HttpTransport(connect=fixture.connect)

    assert transport.post_text("http://fixture.local/brief", {}, body) == "# brief\n"
    request = fixture.requests[0]
    assert request["headers"]["content-type"] == "application/json"
    assert request["headers"]["content-length"] == str(
        len(json.dumps(body).encode("utf-8"))
    )
    assert request["body"] == body


def test_http_transport_mcp_exposes_incremental_response_and_accept():
    payload = {"jsonrpc": "2.0", "id": 1, "result": {}}
    fixture = _Fixture([200], payload)
    transport = HttpTransport(connect=fixture.connect, max_retries=3)

    with transport.post_mcp("http://fixture.local/mcp", {}, {"id": 1}) as response:
        assert response.status == 200
        assert response.headers["content-type"] == "application/json"
        assert json.loads(response.read()) == payload

    assert (
        fixture.requests[0]["headers"]["accept"]
        == "application/json, text/event-stream"
    )


def test_mcp_chunked_sse_returns_matching_event_before_stream_eof():
    event = b'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
    closed = threading.Event()

    def connect(_host, _port, timeout, _use_tls):
        client_sock, server_sock = socket.socketpair()

        def serve():
            server_sock.settimeout(5)
            try:
                request = b""
                while b"\r\n\r\n" not in request:
                    request += server_sock.recv(4096)
                headers, body = request.split(b"\r\n\r\n", 1)
                length = next(
                    int(line.split(b":", 1)[1].strip())
                    for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                while len(body) < length:
                    body += server_sock.recv(length - len(body))
                frame = f"{len(event):X}\r\n".encode() + event + b"\r\n"
                server_sock.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/event-stream\r\n"
                    b"Transfer-Encoding: chunked\r\n\r\n" + frame
                )
                while server_sock.recv(4096):
                    pass
            except OSError:
                pass
            finally:
                closed.set()
                server_sock.close()

        threading.Thread(target=serve, daemon=True).start()
        return _PairedConnection(client_sock, timeout)

    transport = HttpTransport(connect=connect, timeout=2)
    with transport.post_mcp("http://fixture.local/mcp", {}, {"id": 1}) as response:
        assert _matching_sse_message(response, 1) == [
            {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        ]
    assert closed.wait(timeout=2)


def test_http_transport_stream_refuses_error_status_without_retry():
    fixture = _Fixture([404], {}, raw_bodies=[b"{}"])
    transport = HttpTransport(
        timeout=5, max_retries=3, backoff=0, connect=fixture.connect
    )
    with pytest.raises(ProviderError, match="HTTP 404"):
        list(transport.stream("http://fixture.local/chat/completions", {}, {}))
    assert len(fixture.requests) == 1


def test_mcp_response_falls_back_to_read_for_simple_fakes():
    class Response:
        def read(self, size=4096):
            assert size == 17
            return b"fake"

    from omega_prime.providers.http import _McpResponse

    response = _McpResponse(object(), Response(), 200, {})
    assert response.read(17) == b"fake"


def test_mcp_response_uses_read1_incrementally_when_available():
    class Response:
        def read1(self, size=4096):
            assert size == 17
            return b"chunk"

        def read(self, size=4096):
            raise AssertionError("read1 should be used")

    from omega_prime.providers.http import _McpResponse

    response = _McpResponse(object(), Response(), 200, {})
    assert response.read(17) == b"chunk"


def test_loop_consumes_streamed_output_end_to_end():
    lines = [
        'data: {"choices": [{"delta": {"content": "stream"}}]}',
        'data: {"choices": [{"delta": {"content": "ed"}}]}',
        "data: [DONE]",
    ]
    transport = FakeTransport(stream_script=[lines])
    agent = Agent(
        model=ProviderModel(
            GrokProvider(), "grok-4", transport, api_key="k", stream=True
        ),
        tools={},
        max_iterations=4,
    )
    result = run_conversation(agent, "go")

    assert result["final_response"] == "streamed"
    stream_url, _, stream_body = transport.stream_calls[0]
    assert stream_url == "https://api.x.ai/v1/chat/completions"
    assert stream_body["stream"] is True
