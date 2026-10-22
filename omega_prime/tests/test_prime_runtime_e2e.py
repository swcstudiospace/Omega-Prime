# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Exercise Rust provider HTTP through the single Python agent and registry."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from omega_prime.agent import OmegaPrimeAgent
from omega_prime.prime_kernel.native import extension_path, load_extension
from omega_prime.providers.base import ProviderError
from omega_prime.providers.prime import PrimeProviderModel
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.registry import ToolRegistry


@contextmanager
def _provider_peer(*, delay=0):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            time.sleep(delay)
            if len(requests) == 1:
                delta = {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-fact",
                            "type": "function",
                            "function": {"name": "read_fact", "arguments": "{}"},
                        }
                    ]
                }
                reason = "tool_calls"
            else:
                result = next(row for row in body["messages"] if row["role"] == "tool")
                delta = {"content": result["content"]}
                reason = "stop"
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {
                    "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
                    "usage": {
                        "prompt_tokens": 9,
                        "completion_tokens": 3,
                        "total_tokens": 12,
                    },
                },
            ]
            data = (
                "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                + "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _model(base_url):
    return {
        "id": "fixture",
        "name": "fixture",
        "api": "openai-completions",
        "provider": "openai",
        "baseUrl": base_url,
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 8192,
        "maxTokens": 1024,
    }


@pytest.fixture
def native_ai():
    if extension_path() is None:
        pytest.skip("optional omega_prime_prime extension is not built")
    return load_extension().ai


@pytest.mark.native
def test_real_prime_provider_reads_disk_through_the_omega_loop(tmp_path, native_ai):
    fact = tmp_path / "fact.txt"
    fact.write_text("a verified local fact", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(
        "read_fact",
        "Read the local fact file.",
        {"type": "object", "properties": {}},
        lambda: fact.read_text(encoding="utf-8"),
    )
    with _provider_peer() as (url, requests):
        agent = OmegaPrimeAgent.from_prime(
            _model(url),
            registry=registry,
            api_key="fixture-key",
            provider_options={"timeoutMs": 5000},
            system_message="Read the fact.",
            max_iterations=3,
        )
        result = agent.run("What is the local fact?")

    assert result["completed"] is True
    assert result["final_response"] == '"a verified local fact"'
    assert result["api_calls"] == 2
    assert (
        requests[0]["tools"][0]["function"]["description"]
        == "Read the local fact file."
    )
    tool = next(row for row in requests[1]["messages"] if row["role"] == "tool")
    assert tool["tool_call_id"] == "call-fact"
    assert tool["content"] == '"a verified local fact"'
    usage = getattr(agent.provider_model, "last_usage", None)
    assert isinstance(usage, dict) and usage["total_tokens"] == 12
    persisted = json.loads(json.dumps(agent.messages))
    assistants = [row for row in persisted if row["role"] == "assistant"]
    assert assistants[0]["prime_message"]["content"][0]["id"] == "call-fact"
    assert assistants[-1]["prime_message"]["stopReason"] == "stop"


@pytest.mark.native
def test_native_tool_round_does_not_bypass_registry_approval(tmp_path, native_ai):
    output = tmp_path / "must-not-exist"
    registry = ToolRegistry(approval_log=ApprovalLog())
    registry.register(
        "read_fact",
        "Approval-gated fixture.",
        {"type": "object", "properties": {}},
        lambda: output.write_text("changed", encoding="utf-8"),
        requires_approval=True,
    )
    with _provider_peer() as (url, requests):
        agent = OmegaPrimeAgent.from_prime(
            _model(url),
            registry=registry,
            api_key="fixture-key",
            provider_options={"timeoutMs": 5000},
            max_iterations=3,
        )
        result = agent.run("Try the approval-gated tool.")
    assert not output.exists()
    assert "approval required" in result["final_response"]
    tool = next(row for row in requests[1]["messages"] if row["role"] == "tool")
    assert tool["content"].startswith("error:")


@pytest.mark.native
def test_native_timeout_cannot_be_reported_as_a_success(native_ai):
    with _provider_peer(delay=0.5) as (base_url, requests):
        model = PrimeProviderModel(
            _model(base_url), api_key="local-fixture", options={"timeoutMs": 30}
        )
        started = time.monotonic()
        with pytest.raises(ProviderError, match=r"(?i)timed? ?out|timeout"):
            model.complete([{"role": "user", "content": "wait"}])
        assert time.monotonic() - started < 2
        assert model.last_usage is None
        assert len(requests) == 1


@pytest.mark.native
def test_native_unknown_options_are_rejected_before_dispatch(native_ai):
    with _provider_peer() as (base_url, requests):
        with pytest.raises(ValueError, match="Unsupported Prime completion option"):
            native_ai.complete(
                _model(base_url),
                {"messages": [{"role": "user", "content": "hello", "timestamp": 1}]},
                {"apiKey": "local-fixture", "onPayload": "unsupported"},
            )
        assert requests == []
