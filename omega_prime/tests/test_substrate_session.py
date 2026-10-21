"""Phase 41: substrate session, loop wiring, docs_search tool."""

from __future__ import annotations

import json
from pathlib import Path

from omega_prime.agent.conversation_loop import Agent, run_conversation
from omega_prime.agent.model import ScriptedModel
from omega_prime.substrate.client import (
    SubstrateClient,
    SubstrateError,
    SubstrateProtocolError,
)
from omega_prime.substrate.session import SubstrateSession
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.registry import ToolRegistry
from omega_prime.tools.substrate_tools import (
    SUBSTRATE_TOOL_NAMES,
    register_substrate_tools,
)


class _FakeClient:
    """Scripted brief/emit/docs_search; every call recorded."""

    def __init__(
        self,
        brief: str = "# brief\n",
        docs: dict | Exception | None = None,
        emit_error: Exception | None = None,
        brief_error: Exception | None = None,
    ) -> None:
        self._brief = brief
        self._docs = (
            docs if docs is not None else {"ok": True, "status": 200, "body": {}}
        )
        self._emit_error = emit_error
        self._brief_error = brief_error
        self.briefs: list[dict] = []
        self.emits: list[dict] = []
        self.docs_calls: list[str] = []
        self.graph_calls: list[dict] = []

    def brief(self, repo=None, branch=None, graph_id=None) -> str:
        self.briefs.append({"repo": repo, "branch": branch, "graph_id": graph_id})
        if self._brief_error is not None:
            raise self._brief_error
        return self._brief

    def emit(self, kind: str, summary: str, **fields) -> dict:
        self.emits.append({"kind": kind, "summary": summary, **fields})
        if self._emit_error is not None:
            raise self._emit_error
        return {"stored": True, "id": "e1"}

    def docs_search(self, query: str) -> dict:
        self.docs_calls.append(query)
        if isinstance(self._docs, Exception):
            raise self._docs
        return self._docs

    def graph_claim(
        self, graph_id: str, node_id: str, session_id: str, ttl_seconds: int = 3600
    ) -> dict:
        self.emits.append({"kind": "claim", "graph_id": graph_id, "node_id": node_id})
        return {
            "ok": True,
            "lease_id": "lease-id",
            "token": "lease-tok",
            "expires": "soon",
        }

    def graph_release(self, graph_id: str, node_id: str, lease_id: str) -> str:
        self.graph_calls.append(
            {
                "method": "release",
                "graph_id": graph_id,
                "node_id": node_id,
                "lease_id": lease_id,
            }
        )
        return f"Released {graph_id}/{node_id}."

    def graph_complete(self, graph_id: str, node_id: str, lease_id: str) -> str:
        self.graph_calls.append(
            {
                "method": "complete",
                "graph_id": graph_id,
                "node_id": node_id,
                "lease_id": lease_id,
            }
        )
        return f"Completed {graph_id}/{node_id}."

    def graph_heartbeat(self, graph_id: str, node_id: str, lease_id: str) -> dict:
        self.graph_calls.append(
            {
                "method": "heartbeat",
                "graph_id": graph_id,
                "node_id": node_id,
                "lease_id": lease_id,
            }
        )
        return {"ok": True, "graph_id": graph_id, "node_id": node_id}


def _session(
    client: _FakeClient, tmp_path: Path | None = None, **kwargs
) -> SubstrateSession:
    if tmp_path is not None and "brief_dir" not in kwargs:
        kwargs["brief_dir"] = tmp_path
    return SubstrateSession(client, session_id="s1", graph_id="ut-1", **kwargs)


def test_open_emits_start_caches_and_writes_brief(tmp_path: Path) -> None:
    client = _FakeClient()
    session = _session(client, tmp_path)
    assert session.open() == "# brief\n"
    assert session.open() == "# brief\n"
    assert len(client.briefs) == 1
    assert client.emits[0]["kind"] == "session.start"
    assert client.emits[0]["session_id"] == "s1"
    assert client.emits[0]["graph_id"] == "ut-1"
    assert session.brief_block() == "## Substrate brief\n# brief\n"
    assert (tmp_path / ".substrate" / "BRIEF.md").read_text() == "# brief\n"


def test_open_empty_brief_writes_no_file(tmp_path: Path) -> None:
    client = _FakeClient(brief="")
    session = _session(client, tmp_path)
    assert session.open() == ""
    assert session.brief_block() == ""
    assert not (tmp_path / ".substrate" / "BRIEF.md").exists()


def test_open_never_raises(tmp_path: Path) -> None:
    client = _FakeClient(brief_error=RuntimeError("down"))
    session = _session(client, tmp_path)
    assert session.open() == ""
    assert session.brief_block() == ""


def test_prompt_tool_file_turn_close_events() -> None:
    client = _FakeClient()
    session = _session(client)
    session.open()
    session.on_prompt("do the thing " + "x" * 500)
    session.on_tool_call("read_file", {"path": "a.py"}, ok=True)
    session.on_tool_call("write_file", {"path": "b.py", "content": "secret"}, ok=False)
    session.on_turn_end("stop")
    session.close()
    session.close()
    kinds = [event["kind"] for event in client.emits]
    assert kinds == [
        "session.start",
        "prompt",
        "tool.call",
        "tool.call",
        "file.edit",
        "note",
        "session.end",
    ]
    prompt = client.emits[1]
    assert len(prompt["summary"]) == 240
    assert all(event["session_id"] == "s1" for event in client.emits)
    assert client.emits[3]["summary"] == "write_file error"
    assert client.emits[4]["summary"] == "write_file b.py"
    assert "secret" not in json.dumps(client.emits)
    assert client.emits[5]["summary"] == "turn ended: stop"


def test_hooks_never_raise() -> None:
    client = _FakeClient(emit_error=RuntimeError("down"))
    session = _session(client)
    session.open()
    session.on_prompt("hi")
    session.on_tool_call("x", None)
    session.on_turn_end(None)
    session.close()


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


def test_loop_injects_brief_and_reports_trail(tmp_path: Path) -> None:
    client = _FakeClient()
    session = _session(client, tmp_path, repo="o/n")
    model = ScriptedModel(
        [
            _tool_call("read_file", '{"path": "a.py"}'),
            {"role": "assistant", "content": "done"},
        ]
    )
    agent = Agent(
        model=model,
        tools={"read_file": lambda path: "contents"},
        substrate=session,
    )
    result = run_conversation(agent, "read a.py", system_message="Be brief.")
    assert result["turn_exit_reason"] == "text_response(finish_reason=stop)"
    system_row = model.seen[0][0]
    assert system_row["role"] == "system"
    assert "## Substrate brief\n# brief\n" in system_row["content"]
    assert "Be brief." in system_row["content"]
    kinds = [event["kind"] for event in client.emits]
    assert kinds == ["session.start", "prompt", "tool.call", "note"]
    assert client.emits[2]["summary"] == "read_file ok"
    assert client.emits[1]["repo"] == "o/n"


def test_loop_without_substrate_is_unchanged() -> None:
    model = ScriptedModel([{"role": "assistant", "content": "done"}])
    agent = Agent(model=model)
    result = run_conversation(agent, "hi", system_message="Be brief.")
    assert result["turn_exit_reason"] == "text_response(finish_reason=stop)"
    assert "Be brief." in model.seen[0][0]["content"]


def _registry(client: _FakeClient) -> ToolRegistry:
    registry = ToolRegistry()
    assert register_substrate_tools(registry, client) == list(SUBSTRATE_TOOL_NAMES)
    return registry


def test_unconfigured_substrate_tool_reports_state_without_request() -> None:
    class _NeverTransport:
        calls = 0

        def post_mcp(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("unconfigured client attempted a request")

    transport = _NeverTransport()
    client = SubstrateClient(transport=transport)
    registry = ToolRegistry()
    register_substrate_tools(registry, client)

    payload = json.loads(registry.dispatch("substrate_docs_search", {"query": "q"}))

    assert payload["error"].startswith("not_configured:")
    assert transport.calls == 0


def test_docs_tool_returns_excerpts_without_raw() -> None:
    body = {
        "code": 0,
        "data": {
            "chunks": [
                {
                    "content": "cited",
                    "document_keyword": "guide.md",
                    "dataset_id": "ds-1",
                    "similarity": 0.9,
                    "id": "chunk-1",
                    "document_id": "document-1",
                    "positions": [[1, 2]],
                }
            ]
        },
    }
    registry = _registry(_FakeClient(docs={"ok": True, "status": 200, "body": body}))
    payload = json.loads(registry.dispatch("substrate_docs_search", {"query": "q"}))
    assert "retrieval" not in payload
    assert payload == {
        "chunks": [
            {
                "content": "cited",
                "document": "guide.md",
                "dataset_id": "ds-1",
                "score": 0.9,
                "chunk_id": "chunk-1",
                "document_id": "document-1",
                "positions": [[1, 2]],
            }
        ]
    }


def test_docs_tool_redacts_entire_payload_and_caps_content() -> None:
    token = "ghp_" + "x" * 36
    sentinel = "SENTINEL-BEYOND-CAP"
    raw = {
        "code": 0,
        "data": {
            "chunks": [
                {
                    "content": f"leak {token} " + "y" * 2000 + sentinel,
                    "document_keyword": f"guide-{token}.md",
                    "dataset_id": f"ds-{token}",
                    "similarity": 0.9,
                    "id": f"chunk-{token}",
                    "document_id": f"document-{token}",
                    "positions": [[1, 2]],
                },
                "not-a-chunk",
            ]
        },
    }
    registry = _registry(_FakeClient(docs={"ok": True, "status": 200, "body": raw}))
    payload = json.loads(registry.dispatch("substrate_docs_search", {"query": "q"}))
    assert "retrieval" not in payload
    serialized = json.dumps(payload)
    assert token not in serialized
    assert sentinel not in serialized
    assert len(payload["chunks"]) == 1
    (shaped,) = payload["chunks"]
    assert len(shaped["content"]) <= 1500
    assert shaped["document"] == "guide-[REDACTED].md"
    assert shaped["score"] == 0.9
    assert shaped["chunk_id"] == "chunk-[REDACTED]"
    assert shaped["document_id"] == "document-[REDACTED]"


def test_docs_tool_validates_metadata_types_and_preserves_zero() -> None:
    body = {
        "code": 0,
        "data": {
            "chunks": [
                {"content": "zero", "docnm_kwd": "zero.md", "score": 0},
                {
                    "content": "typed",
                    "document": {"nested": "nope"},
                    "dataset_id": ["ds", "list"],
                    "score": "high",
                },
                "not-a-chunk",
            ]
        },
    }
    registry = _registry(_FakeClient(docs={"ok": True, "status": 200, "body": body}))
    payload = json.loads(registry.dispatch("substrate_docs_search", {"query": "q"}))
    assert payload == {
        "chunks": [
            {
                "content": "zero",
                "document": "zero.md",
                "dataset_id": None,
                "score": 0,
            },
            {
                "content": "typed",
                "document": None,
                "dataset_id": None,
                "score": None,
            },
        ]
    }


def test_docs_tool_redacts_every_error_branch() -> None:
    token = "ghp_" + "x" * 36
    ragflow = _registry(
        _FakeClient(
            docs={
                "ok": True,
                "status": 200,
                "body": {"code": 102, "message": f"no dataset {token} " + "z" * 900},
            }
        )
    )
    ragflow_payload = json.loads(
        ragflow.dispatch("substrate_docs_search", {"query": "q"})
    )
    assert set(ragflow_payload) == {"error"}
    assert token not in json.dumps(ragflow_payload)
    assert len(ragflow_payload["error"]) <= 500

    plane = _registry(_FakeClient(docs={"ok": False, "error": f"down {token}"}))
    plane_payload = json.loads(plane.dispatch("substrate_docs_search", {"query": "q"}))
    assert token not in json.dumps(plane_payload)
    assert len(plane_payload["error"]) <= 500

    down = _registry(_FakeClient(docs=SubstrateError(f"boom {token}")))
    down_payload = json.loads(down.dispatch("substrate_docs_search", {"query": "q"}))
    assert "unreachable" in down_payload["error"]
    assert token not in json.dumps(down_payload)
    assert len(down_payload["error"]) <= 500

    registry = _registry(_FakeClient(docs={"ok": True, "status": 200, "body": {}}))
    assert "error" in json.loads(
        registry.dispatch("substrate_docs_search", {"query": ""})
    )


def test_docs_tool_malformed_response_is_error_not_empty() -> None:
    malformed: list[dict] = [
        {"ok": True, "status": 200},
        {"ok": True, "status": 200, "body": None},
        {"ok": True, "status": 200, "body": "chunks"},
        {"ok": True, "status": 200, "body": ["chunks"]},
        {"ok": True, "status": 200, "body": {"code": 0}},
        {"ok": True, "status": 200, "body": {"code": 0, "data": {}}},
        {"ok": True, "status": 200, "body": {"code": 0, "data": {"chunks": {}}}},
        {"ok": True, "status": 200, "body": {"code": 0, "data": "chunks"}},
        {"ok": True, "status": 200, "body": {"code": 0, "chunks": "chunks"}},
        {"ok": True, "status": 200, "body": {"code": "zero"}},
        {"ok": True, "status": 200, "body": {"code": True}},
    ]
    for docs in malformed:
        registry = _registry(_FakeClient(docs=docs))
        payload = json.loads(registry.dispatch("substrate_docs_search", {"query": "q"}))
        assert set(payload) == {"error"}, docs
        assert "malformed" in payload["error"], docs

    empty = _registry(
        _FakeClient(
            docs={
                "ok": True,
                "status": 200,
                "body": {"code": 0, "data": {"chunks": []}},
            }
        )
    )
    assert json.loads(empty.dispatch("substrate_docs_search", {"query": "q"})) == {
        "chunks": []
    }

    top_level = _registry(
        _FakeClient(
            docs={
                "ok": True,
                "status": 200,
                "body": {"code": 0, "chunks": [{"content": "cited"}]},
            }
        )
    )
    assert json.loads(top_level.dispatch("substrate_docs_search", {"query": "q"})) == {
        "chunks": [
            {"content": "cited", "document": None, "dataset_id": None, "score": None}
        ]
    }


def _approved_registry(client: _FakeClient) -> ToolRegistry:
    log = ApprovalLog()
    for name in (
        "substrate_graph_claim",
        "substrate_graph_release",
        "substrate_graph_complete",
    ):
        assert log.approve(name, "person")["approved"] is True
    registry = ToolRegistry(approval_log=log)
    assert register_substrate_tools(registry, client) == list(SUBSTRATE_TOOL_NAMES)
    return registry


def test_graph_tools_dispatch_with_lease() -> None:
    client = _FakeClient()
    registry = _approved_registry(client)
    claim = json.loads(
        registry.dispatch(
            "substrate_graph_claim",
            {"graph_id": "g", "node_id": "n", "session_id": "s1"},
        )
    )
    assert claim == {
        "ok": True,
        "lease_id": "lease-id",
        "token": "lease-tok",
        "expires": "soon",
    }
    release = json.loads(
        registry.dispatch(
            "substrate_graph_release",
            {"graph_id": "g", "node_id": "n", "lease_id": "lease-id"},
        )
    )
    assert release == {"result": "Released g/n."}
    complete = json.loads(
        registry.dispatch(
            "substrate_graph_complete",
            {"graph_id": "g", "node_id": "n", "lease_id": "lease-id"},
        )
    )
    assert complete == {"result": "Completed g/n."}
    beat = json.loads(
        registry.dispatch(
            "substrate_graph_heartbeat",
            {"graph_id": "g", "node_id": "n", "lease_id": "lease-id"},
        )
    )
    assert beat == {"ok": True, "graph_id": "g", "node_id": "n"}
    assert [call["lease_id"] for call in client.graph_calls] == [
        "lease-id",
        "lease-id",
        "lease-id",
    ]
    bad = json.loads(
        registry.dispatch(
            "substrate_graph_claim", {"graph_id": "", "node_id": "n", "session_id": "s"}
        )
    )
    assert "graph_id" in bad["error"]


def test_graph_tools_reject_missing_lease_id() -> None:
    registry = _approved_registry(_FakeClient())
    for name in (
        "substrate_graph_release",
        "substrate_graph_complete",
        "substrate_graph_heartbeat",
    ):
        bad = json.loads(registry.dispatch(name, {"graph_id": "g", "node_id": "n"}))
        assert bad == {"error": "lease_id must be a non-empty string"}


def test_graph_mutations_require_approval() -> None:
    registry = ToolRegistry(approval_log=ApprovalLog())
    register_substrate_tools(registry, _FakeClient())
    for name in (
        "substrate_graph_claim",
        "substrate_graph_release",
        "substrate_graph_complete",
    ):
        denied = json.loads(registry.dispatch(name, {"graph_id": "g", "node_id": "n"}))
        assert denied["error"] == "approval required"
    heartbeat = json.loads(
        registry.dispatch(
            "substrate_graph_heartbeat",
            {"graph_id": "g", "node_id": "n", "lease_id": "lease-id"},
        )
    )
    assert heartbeat["ok"] is True


class _PersistentSseResponse:
    status = 200

    def __init__(self):
        self.headers = {"content-type": "text/event-stream"}
        self.chunks = iter(
            [
                b": ping\r\n\r\n",
                b'data: {"jsonrpc":"2.0",\r\n',
                b'data: "method":"notification"}\r\n\r\n',
                b'data: {"jsonrpc":"2.0","id":1,\r\n',
                b'data: "result":{"content":[]}}\r\n\r\n',
            ]
        )
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def read(self, _size=4096):
        try:
            return next(self.chunks)
        except StopIteration:
            raise AssertionError("parser waited for persistent stream EOF") from None


class _SseTransport:
    def __init__(self):
        self.response = _PersistentSseResponse()

    def post_mcp(self, _url, _headers, _envelope):
        return self.response


def test_mcp_sse_returns_matching_response_before_persistent_stream_eof():
    transport = _SseTransport()
    client = SubstrateClient("local", transport=transport)
    assert client._mcp_post("sample", {}) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": []},
    }
    assert transport.response.closed


def test_mcp_sse_malformed_complete_data_frame_is_protocol_error():
    class Response:
        status = 200

        def __init__(self):
            self.headers = {"content-type": "text/event-stream"}
            self.chunks = iter([b"data: {not-json}\r\n\r\n"])

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, _size=4096):
            return next(self.chunks, b"")

    class Transport:
        def post_mcp(self, _url, _headers, _envelope):
            return Response()

    try:
        SubstrateClient("local", transport=Transport())._mcp_post("sample", {})
    except SubstrateProtocolError as exc:
        assert "malformed SSE data" in str(exc)
    else:
        raise AssertionError("malformed complete SSE frame was not rejected")


def test_mcp_sse_ignores_empty_and_valid_unrelated_frames():
    class Response:
        status = 200

        def __init__(self):
            self.headers = {"content-type": "text/event-stream"}
            self.chunks = iter(
                [
                    b"data:\r\n\r\n",
                    b'data: {"jsonrpc":"2.0","method":"notification"}\r\n\r\n',
                    b'data: {"jsonrpc":"2.0","id":1,"result":{"content":[]}}\r\n\r\n',
                ]
            )

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, _size=4096):
            return next(self.chunks, b"")

    class Transport:
        def post_mcp(self, _url, _headers, _envelope):
            return Response()

    assert SubstrateClient("local", transport=Transport())._mcp_post("sample", {}) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": []},
    }


def test_mcp_response_id_must_match_exactly():
    class WrongIdTransport:
        def post_mcp(self, _url, _headers, _envelope):
            class Response(_PersistentSseResponse):
                def __init__(self):
                    self.status = 200
                    self.headers = {"content-type": "application/json"}
                    self.body = b'{"jsonrpc":"2.0","id":"1","result":{}}'

                def read(self, _size=4096):
                    body, self.body = self.body, b""
                    return body

            return Response()

    try:
        SubstrateClient("local", transport=WrongIdTransport())._mcp_post("sample", {})
    except SubstrateProtocolError:
        pass
    else:
        raise AssertionError("string ID must not match numeric request ID")


def test_mcp_session_reuse_and_cached_404_reinitialization_once():
    class Response:
        def __init__(self, status, request_id, session=None):
            self.status = status
            self.headers = {"content-type": "application/json"}
            if session:
                self.headers["mcp-session-id"] = session
            self.body = json.dumps(
                {"jsonrpc": "2.0", "id": request_id, "result": {"content": []}}
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, _size=4096):
            body, self.body = self.body, b""
            return body

    class SessionTransport:
        def __init__(self):
            self.calls = []
            self.statuses = [200, 404, 200, 200]

        def post_mcp(self, url, headers, envelope):
            self.calls.append(dict(headers))
            status = self.statuses.pop(0)
            return Response(
                status,
                envelope["id"],
                "session-1" if len(self.calls) == 1 else "session-2",
            )

    transport = SessionTransport()
    client = SubstrateClient("local", transport=transport)
    client._mcp_post("sample", {})
    client._mcp_post("sample", {})
    client._mcp_post("sample", {})

    assert "Mcp-Session-Id" not in transport.calls[0]
    assert transport.calls[1]["Mcp-Session-Id"] == "session-1"
    assert "Mcp-Session-Id" not in transport.calls[2]
    assert transport.calls[3]["Mcp-Session-Id"] == "session-2"
