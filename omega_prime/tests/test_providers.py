"""Provider contracts and actual request/response behavior."""

from __future__ import annotations

import json

import pytest

from omega_prime.agent.conversation_loop import Agent, run_conversation
from omega_prime.providers.anthropic import AnthropicProvider
from omega_prime.providers.base import ProviderError, ProviderModel
from omega_prime.providers.fake import FakeTransport
from omega_prime.providers.gemini import GeminiProvider
from omega_prime.providers.grok import XAI_DEFAULT_BASE_URL, GrokProvider
from omega_prime.providers.http import HttpTransport
from omega_prime.providers.ollama import OllamaProvider
from omega_prime.providers.openai import OpenAIProvider


def test_fake_transport_completes_a_turn_through_the_contract():
    transport = FakeTransport(
        script=[
            {"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}]}
        ]
    )
    model = ProviderModel(GrokProvider(), "grok-4", transport, api_key="fake-key")
    agent = Agent(model=model, tools={}, max_iterations=4)
    result = run_conversation(agent, "ping", system_message="sys")

    assert result["final_response"] == "hello"
    assert len(transport.calls) == 1
    _method, url, headers, body = transport.calls[0]
    assert url == XAI_DEFAULT_BASE_URL + "/chat/completions"
    assert headers["Authorization"] == "Bearer fake-key"
    assert body["model"] == "grok-4"
    assert body["messages"][0]["role"] == "system"
    assert "sys" in body["messages"][0]["content"]


def test_grok_adapter_round_trips_a_tool_call_and_needs_a_key():
    transport = FakeTransport(
        script=[
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path": "x"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ]
    )
    model = ProviderModel(GrokProvider(), "grok-4", transport, api_key="fake-key")
    row = model.complete(
        [{"role": "user", "content": "read"}], {"read_file": lambda: ""}
    )

    assert row["role"] == "assistant"
    assert row["tool_calls"][0]["function"]["name"] == "read_file"
    assert row["finish_reason"] == "tool_calls"
    assert transport.calls[0][3]["tools"][0]["function"]["name"] == "read_file"

    keyless = ProviderModel(GrokProvider(), "grok-4", transport, api_key="")
    with pytest.raises(ProviderError, match="needs an API key"):
        keyless.complete([{"role": "user", "content": "x"}])
    assert len(transport.calls) == 1


def test_openai_adapter_answers_the_contract():
    transport = FakeTransport(
        script=[{"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}]
    )
    chat = OpenAIProvider(api_mode="chat_completions")
    model = ProviderModel(chat, "gpt-5", transport, api_key="fake-key")
    row = model.complete([{"role": "user", "content": "hi"}])

    assert row == {"role": "assistant", "content": "ok", "finish_reason": "stop"}
    _method, url, headers, body = transport.calls[0]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer fake-key"
    assert "tools" not in body


def test_openai_routing_prefers_responses_on_first_party():
    assert OpenAIProvider().effective_mode() == "responses"
    assert (
        OpenAIProvider(base_url="https://proxy.local/v1").effective_mode()
        == "chat_completions"
    )
    assert OpenAIProvider(api_mode="responses").effective_mode() == "responses"
    assert (
        OpenAIProvider(api_mode="chat_completions").effective_mode()
        == "chat_completions"
    )
    assert GrokProvider().effective_mode() == "chat_completions"

    url, _, _ = OpenAIProvider().build_request("gpt-5", [], None, "k")
    assert url == "https://api.openai.com/v1/responses"
    compat, _, _ = OpenAIProvider(base_url="https://proxy.local/v1").build_request(
        "gpt-5", [], None, "k"
    )
    assert compat == "https://proxy.local/v1/chat/completions"


def test_anthropic_adapter_answers_the_contract():
    transport = FakeTransport(
        script=[
            {
                "content": [
                    {"type": "text", "text": "reading"},
                    {
                        "type": "tool_use",
                        "id": "tu-1",
                        "name": "read_file",
                        "input": {"path": "x"},
                    },
                ],
                "stop_reason": "tool_use",
            }
        ]
    )
    model = ProviderModel(
        AnthropicProvider(), "claude-sonnet-4", transport, api_key="fake-key"
    )
    row = model.complete(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "read x"},
        ]
    )

    assert row["content"] == "reading"
    assert row["tool_calls"] == [
        {
            "id": "tu-1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "x"}'},
        }
    ]
    _method, url, headers, body = transport.calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "fake-key"
    assert headers["anthropic-version"] == "2023-06-01"
    assert body["system"] == "sys"
    assert body["messages"] == [{"role": "user", "content": "read x"}]
    assert body["max_tokens"] == 8192


def test_gemini_adapter_answers_the_contract():
    transport = FakeTransport(
        script=[
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "read_file",
                                        "args": {"path": "x"},
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    )
    model = ProviderModel(GeminiProvider(), "gemini-3", transport, api_key="fake-key")
    row = model.complete([{"role": "user", "content": "read x"}])

    assert row["tool_calls"][0]["function"]["name"] == "read_file"
    assert json.loads(row["tool_calls"][0]["function"]["arguments"]) == {"path": "x"}
    _method, url, headers, body = transport.calls[0]
    assert url == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3:generateContent"
    )
    assert headers["x-goog-api-key"] == "fake-key"
    assert body["contents"][0]["role"] == "user"


def test_ollama_adapter_answers_the_contract_without_a_key():
    transport = FakeTransport(
        script=[
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-9",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": {"path": "x"},
                            },
                        }
                    ],
                }
            }
        ]
    )
    model = ProviderModel(OllamaProvider(), "qwen3", transport)
    row = model.complete([{"role": "user", "content": "read x"}])

    assert row["tool_calls"][0]["id"] == "call-9"
    _method, url, headers, body = transport.calls[0]
    assert url == "http://localhost:11434/api/chat"
    assert "Authorization" not in headers
    assert body["stream"] is False

    empty = ProviderModel(
        OllamaProvider(), "qwen3", FakeTransport(script=[{"message": {"role": "x"}}])
    )
    with pytest.raises(ProviderError):
        empty.complete([{"role": "user", "content": "x"}])


def test_usage_normalizes_across_providers():
    assert OpenAIProvider().parse_usage(
        {"usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}}
    ) == {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
    assert GrokProvider().parse_usage(
        {"usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}
    ) == {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
    assert AnthropicProvider().parse_usage(
        {"usage": {"input_tokens": 7, "output_tokens": 2}}
    ) == {"prompt_tokens": 7, "completion_tokens": 2}
    assert GeminiProvider().parse_usage(
        {
            "usageMetadata": {
                "promptTokenCount": 5,
                "candidatesTokenCount": 6,
                "totalTokenCount": 11,
            }
        }
    ) == {"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11}
    assert OllamaProvider().parse_usage({"prompt_eval_count": 9, "eval_count": 3}) == {
        "prompt_tokens": 9,
        "completion_tokens": 3,
    }


def test_usage_ignores_absent_and_junk_counters():
    assert OpenAIProvider().parse_usage({}) is None
    assert OpenAIProvider().parse_usage({"usage": None}) is None
    assert OpenAIProvider().parse_usage({"usage": {"prompt_tokens": True}}) is None
    assert OpenAIProvider().parse_usage({"usage": {"prompt_tokens": -1}}) is None
    assert AnthropicProvider().parse_usage({"usage": {"input_tokens": "9"}}) is None
    assert GeminiProvider().parse_usage({"usageMetadata": []}) is None


def test_complete_records_last_usage():
    transport = FakeTransport(
        script=[
            {
                "choices": [
                    {
                        "message": {"content": "hi"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 2,
                    "total_tokens": 14,
                },
            }
        ]
    )
    model = ProviderModel(GrokProvider(), "grok-4", transport, api_key="fake-key")
    assert model.last_usage is None
    row = model.complete([{"role": "user", "content": "hi"}])
    assert row["content"] == "hi"
    assert model.last_usage == {
        "prompt_tokens": 12,
        "completion_tokens": 2,
        "total_tokens": 14,
    }

    bare = FakeTransport(script=[{"choices": [{"message": {"content": "yo"}}]}])
    plain = ProviderModel(GrokProvider(), "grok-4", bare, api_key="fake-key")
    plain.complete([{"role": "user", "content": "yo"}])
    assert plain.last_usage is None


def _sse(*bodies: dict) -> list[str]:
    lines = ["data: " + json.dumps(body) for body in bodies]
    lines.append("data: [DONE]")
    return lines


def test_responses_round_trip_maps_input_and_output():
    seen: dict = {}

    def _post(url: str, headers: dict, body: dict) -> dict:
        seen["url"] = url
        seen["body"] = body
        return {
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "read it"}],
                },
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "read_file",
                    "arguments": '{"path": "x"}',
                },
            ],
            "usage": {"input_tokens": 9, "output_tokens": 3, "total_tokens": 12},
        }

    transport = FakeTransport(post_fn=_post)
    model = ProviderModel(OpenAIProvider(), "gpt-5", transport, api_key="fake-key")
    row = model.complete(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "read x"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c0",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": {"path": "x"}},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c0", "content": "contents"},
        ],
        {"read_file": lambda: ""},
    )

    assert seen["url"] == "https://api.openai.com/v1/responses"
    body = seen["body"]
    assert body["model"] == "gpt-5" and body["store"] is False
    kinds = [item.get("type") or item.get("role") for item in body["input"]]
    assert kinds == [
        "system",
        "user",
        "function_call",
        "assistant",
        "function_call_output",
    ]
    assert body["input"][2]["arguments"] == '{"path": "x"}'
    assert body["tools"] == [
        {
            "type": "function",
            "name": "read_file",
            "parameters": {"type": "object", "properties": {}},
        }
    ]
    assert row["content"] == "read it"
    assert row["tool_calls"] == [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "x"}'},
        }
    ]
    assert model.last_usage == {
        "prompt_tokens": 9,
        "completion_tokens": 3,
        "total_tokens": 12,
    }
    assert model.last_fallback is False


def test_responses_failed_and_empty_shapes_raise():
    failed = FakeTransport(
        script=[{"status": "failed", "error": {"message": "bad key"}, "output": []}]
    )
    with pytest.raises(ProviderError, match="response failed: bad key"):
        ProviderModel(OpenAIProvider(), "gpt-5", failed, api_key="k").complete(
            [{"role": "user", "content": "hi"}]
        )
    for payload in ({"status": "completed", "output": []}, {"mystery": True}):
        transport = FakeTransport(script=[payload])
        with pytest.raises(ProviderError):
            ProviderModel(OpenAIProvider(), "gpt-5", transport, api_key="k").complete(
                [{"role": "user", "content": "hi"}]
            )


def test_responses_incomplete_raises_provider_error():
    transport = FakeTransport(
        script=[
            {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "part"}],
                    }
                ],
            }
        ]
    )
    with pytest.raises(ProviderError, match=r"incomplete.*max_output_tokens"):
        ProviderModel(OpenAIProvider(), "gpt-5", transport, api_key="k").complete(
            [{"role": "user", "content": "hi"}]
        )


def test_responses_refusal_preserves_message():
    transport = FakeTransport(
        script=[
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "refusal",
                                "refusal": "I cannot fulfill this request",
                            }
                        ],
                    }
                ],
            }
        ]
    )
    row = ProviderModel(OpenAIProvider(), "gpt-5", transport, api_key="k").complete(
        [{"role": "user", "content": "hi"}]
    )
    assert row["content"] == "I cannot fulfill this request"


def test_responses_denied_falls_back_to_chat_completions():
    def _post(url: str, headers: dict, body: dict) -> dict:
        if url.endswith("/responses"):
            raise ProviderError("HTTP 404 from https://api.openai.com/v1/responses")
        return {"choices": [{"message": {"content": "via chat"}}]}

    transport = FakeTransport(post_fn=_post)
    model = ProviderModel(OpenAIProvider(), "gpt-5", transport, api_key="k")
    row = model.complete([{"role": "user", "content": "hi"}])

    assert row["content"] == "via chat"
    assert model.last_fallback is True
    assert transport.calls[0][1].endswith("/responses")
    assert transport.calls[1][1].endswith("/chat/completions")
    assert transport.calls[1][3]["messages"] == [{"role": "user", "content": "hi"}]


def test_responses_scope_denied_falls_back_but_client_errors_do_not():
    def _denied(url: str, headers: dict, body: dict) -> dict:
        if url.endswith("/responses"):
            raise ProviderError(
                "HTTP 403: key lacks api.responses.write for /v1/responses"
            )
        return {"choices": [{"message": {"content": "ok"}}]}

    scoped = ProviderModel(
        OpenAIProvider(), "gpt-5", FakeTransport(post_fn=_denied), api_key="k"
    )
    assert scoped.complete([{"role": "user", "content": "hi"}])["content"] == "ok"
    assert scoped.last_fallback is True

    def _bad(url: str, headers: dict, body: dict) -> dict:
        raise ProviderError("HTTP 400 from https://api.openai.com/v1/responses")

    broken = ProviderModel(
        OpenAIProvider(), "gpt-5", FakeTransport(post_fn=_bad), api_key="k"
    )
    with pytest.raises(ProviderError, match="HTTP 400"):
        broken.complete([{"role": "user", "content": "hi"}])
    assert broken.last_fallback is False

    grok = ProviderModel(
        GrokProvider(), "grok-4", FakeTransport(post_fn=_bad), api_key="k"
    )
    with pytest.raises(ProviderError, match="HTTP 400"):
        grok.complete([{"role": "user", "content": "hi"}])
    assert grok.last_fallback is False


def test_stream_requests_follow_each_wire():
    url, _, body = AnthropicProvider().stream_request("m", [], None, "k")
    assert url.endswith("/messages") and body["stream"] is True
    url, _, body = GeminiProvider().stream_request("gemini-3", [], None, "k")
    assert url.endswith(":streamGenerateContent?alt=sse") and "stream" not in body
    url, _, body = OllamaProvider().stream_request("m", [], None, "")
    assert url.endswith("/api/chat") and body["stream"] is True
    chat = OpenAIProvider(api_mode="chat_completions")
    url, _, body = chat.stream_request("m", [], None, "k")
    assert url.endswith("/chat/completions")
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


def test_openai_stream_accumulates_text_tool_calls_and_usage():
    lines = _sse(
        {"choices": [{"delta": {"content": "Hel"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "content": "lo",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "function": {"name": "sh", "arguments": '{"c": '},
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [{"index": 0, "function": {"arguments": '"ls"}'}}]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 7,
                "total_tokens": 12,
            },
        },
    )
    transport = FakeTransport(stream_script=[lines])
    model = ProviderModel(GrokProvider(), "grok-4", transport, api_key="k", stream=True)
    row = model.complete([{"role": "user", "content": "run"}])

    assert row["content"] == "Hello"
    assert row["tool_calls"][0]["function"] == {
        "name": "sh",
        "arguments": '{"c": "ls"}',
    }
    assert row["finish_reason"] == "tool_calls"
    assert model.last_usage == {
        "prompt_tokens": 5,
        "completion_tokens": 7,
        "total_tokens": 12,
    }
    stream_url, _, stream_body = transport.stream_calls[0]
    assert stream_url == "https://api.x.ai/v1/chat/completions"
    assert stream_body["stream"] is True


def test_anthropic_stream_accumulates_deltas_and_usage():
    lines = _sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 4}}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hi"},
        },
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "t1", "name": "sh"},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"c": "ls"}'},
        },
        {"type": "message_delta", "usage": {"output_tokens": 6}},
    )
    transport = FakeTransport(stream_script=[lines])
    model = ProviderModel(
        AnthropicProvider(), "claude-sonnet-4", transport, api_key="k", stream=True
    )
    row = model.complete([{"role": "user", "content": "run"}])

    assert row["content"] == "hi"
    assert row["tool_calls"][0]["function"] == {
        "name": "sh",
        "arguments": {"c": "ls"},
    }
    assert model.last_usage == {"prompt_tokens": 4, "completion_tokens": 6}


def test_gemini_and_ollama_streams_accumulate():
    gemini_lines = _sse(
        {"candidates": [{"content": {"parts": [{"text": "a"}]}}]},
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"functionCall": {"name": "sh", "args": {"c": "ls"}}}]
                    }
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 3,
                "candidatesTokenCount": 2,
                "totalTokenCount": 5,
            },
        },
    )
    gemini = ProviderModel(
        GeminiProvider(),
        "gemini-3",
        FakeTransport(stream_script=[gemini_lines]),
        api_key="k",
        stream=True,
    )
    row = gemini.complete([{"role": "user", "content": "run"}])
    assert row["content"] == "a"
    assert row["tool_calls"][0]["function"]["arguments"] == {"c": "ls"}
    assert gemini.last_usage == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
    }

    ndjson = [
        json.dumps({"message": {"content": "b"}}),
        "not json",
        json.dumps(
            {
                "message": {"content": "", "tool_calls": []},
                "done": True,
                "prompt_eval_count": 7,
                "eval_count": 1,
            }
        ),
    ]
    ollama = ProviderModel(
        OllamaProvider(), "m", FakeTransport(stream_script=[ndjson]), stream=True
    )
    row = ollama.complete([{"role": "user", "content": "run"}])
    assert row["content"] == "b"
    assert "tool_calls" not in row
    assert ollama.last_usage == {"prompt_tokens": 7, "completion_tokens": 1}


def test_streaming_without_transport_support_raises():
    class _NoStream:
        def post(self, url: str, headers: dict, body: dict) -> dict:
            raise AssertionError("must not post")

        def get(self, url: str, headers: dict, params: dict) -> dict:
            raise AssertionError("must not get")

    # Intentionally violates the Transport protocol: no stream method.
    bare = _NoStream()
    model = ProviderModel(
        GrokProvider(),
        "grok-4",
        bare,  # type: ignore[arg-type]
        api_key="k",
        stream=True,
    )
    with pytest.raises(ProviderError, match="does not stream"):
        model.complete([{"role": "user", "content": "hi"}])


@pytest.mark.parametrize(
    "method,status",
    [("get", 401), ("get", 403), ("post", 401), ("post", 403)],
)
def test_http_transport_exposes_numeric_status_without_response_body(
    monkeypatch, method, status
):
    transport = HttpTransport(max_retries=0)
    response_body = b'{"secret":"must not appear in diagnostics"}'
    monkeypatch.setattr(
        transport,
        "_request",
        lambda *_args: (status, {}, response_body),
    )

    with pytest.raises(ProviderError) as exc:
        if method == "get":
            transport.get("https://provider.local/path", {}, {})
        else:
            transport.post("https://provider.local/path", {}, {})

    assert getattr(exc.value, "status_code", None) == status
    assert response_body.decode() not in str(exc.value)
