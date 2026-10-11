"""Phase 45: the KB sync mirrors scripted MCP peers and skips cleanly."""

from __future__ import annotations

import json
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from omega_prime.greptile.kb_sync import GreptileError, McpHttpClient, main, sync

TOKEN = "greptile-secret"


class _Response:
    def __init__(self, body: bytes, headers: dict | None = None) -> None:
        self._body = body
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body


class _Peer:
    """Scripted MCP server: canned (body, headers) per request, requests recorded."""

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.requests: list[dict] = []

    def __call__(self, request, timeout=None):
        envelope = json.loads(request.data.decode("utf-8")) if request.data else {}
        self.requests.append(
            {
                "url": request.full_url,
                "headers": dict(request.header_items()),
                "envelope": envelope,
            }
        )
        if not self._script:
            raise AssertionError("peer script exhausted")
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        body, headers = outcome
        return _Response(body, headers)


def _rpc(payload: dict, rpc_id: int = 0) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": rpc_id or payload.pop("_id", 1), **payload}
    ).encode()


def _tool(payload: dict) -> bytes:
    return _rpc(
        {"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}
    )


def _init_script(extra: list | None = None) -> list:
    script: list = [
        (
            _rpc({"result": {"protocolVersion": "2025-06-18"}}, 1),
            {"mcp-session-id": "s1"},
        )
    ]
    script.append((b"", {}))
    return script + (extra or [])


def test_full_sync_json_variant(tmp_path: Path) -> None:
    peer = _Peer(
        _init_script(
            [
                (
                    _tool(
                        {
                            "repositories": [
                                {"repoNamespaceExternalId": "ns-1", "repoName": "o/n"}
                            ],
                            "total": 1,
                            "returned": 1,
                        }
                    ),
                    {},
                ),
                (
                    _tool(
                        {
                            "documentPaths": ["index.md", "docs/a.md"],
                            "sectionVersions": {"docs": "v1"},
                            "total": 2,
                            "returned": 2,
                        }
                    ),
                    {},
                ),
                (
                    _tool(
                        {
                            "document": {
                                "path": "index.md",
                                "versionId": "v1",
                                "content": "# Index\n",
                            }
                        }
                    ),
                    {},
                ),
                (
                    _tool(
                        {
                            "document": {
                                "path": "docs/a.md",
                                "versionId": "v1",
                                "content": "# A\n",
                            }
                        }
                    ),
                    {},
                ),
            ]
        )
    )
    out = tmp_path / "kb"
    report = sync("o/n", out, McpHttpClient("https://mcp.local", TOKEN, opener=peer))
    assert report == {"status": "synced", "repo": "o/n", "documents": 2}
    assert (out / "index.md").read_text().startswith("<!-- Greptile-synthesized")
    assert "# A\n" in (out / "docs" / "a.md").read_text()
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["repo"] == "o/n" and len(manifest["documents"]) == 2
    assert TOKEN not in json.dumps(manifest)
    calls = [r["envelope"].get("method") for r in peer.requests]
    assert calls[0] == "initialize"
    tools = [
        r["envelope"]["params"]["name"]
        for r in peer.requests
        if r["envelope"].get("method") == "tools/call"
    ]
    assert tools == [
        "list_knowledge_bases",
        "list_knowledge_base_documents",
        "get_knowledge_base_document",
        "get_knowledge_base_document",
    ]
    assert peer.requests[2]["headers"]["Mcp-session-id"] == "s1"


def test_sse_responses_and_pagination(tmp_path: Path) -> None:
    sse = (
        b": ping\n\n"
        + b'data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"'
        + json.dumps(
            {
                "repositories": [
                    {"repoNamespaceExternalId": "ns-9", "repoName": "o/n"}
                ],
                "total": 1,
                "returned": 1,
            }
        )
        .replace('"', '\\"')
        .encode()
        + b'"}]}}\n\n'
    )
    peer = _Peer(
        _init_script(
            [
                (sse, {"content-type": "text/event-stream"}),
                (
                    _tool(
                        {
                            "documentPaths": [],
                            "sectionVersions": {"docs": None},
                            "total": 0,
                            "returned": 0,
                        }
                    ),
                    {},
                ),
            ]
        )
    )
    report = sync(
        "o/n", tmp_path / "kb", McpHttpClient("https://mcp.local", TOKEN, opener=peer)
    )
    assert report["status"] == "empty"
    assert not (tmp_path / "kb").exists()


def test_skip_paths_and_unsafe_path(tmp_path: Path) -> None:
    assert main(["--repo", "o/n"], {}, _Peer([])) == 2
    peer = _Peer(
        _init_script([(_tool({"repositories": [], "total": 0, "returned": 0}), {})])
    )
    assert (
        main(
            ["--repo", "o/n", "--out", str(tmp_path)], {"GREPTILE_API_KEY": TOKEN}, peer
        )
        == 3
    )
    evil = _Peer(
        _init_script(
            [
                (
                    _tool(
                        {
                            "repositories": [
                                {"repoNamespaceExternalId": "n", "repoName": "o/n"}
                            ],
                            "total": 1,
                            "returned": 1,
                        }
                    ),
                    {},
                ),
                (
                    _tool({"documentPaths": ["../evil.md"], "total": 1, "returned": 1}),
                    {},
                ),
            ]
        )
    )
    with pytest.raises(GreptileError):
        sync("o/n", tmp_path, McpHttpClient("https://mcp.local", TOKEN, opener=evil))


def test_auth_failure_is_secret_free() -> None:
    def denied(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "x", Message(), None)

    client = McpHttpClient("https://mcp.local", TOKEN, opener=denied)
    with pytest.raises(GreptileError) as exc:
        client.call_tool("list_knowledge_bases", {})
    assert TOKEN not in str(exc.value)
    assert "GREPTILE_API_KEY" in str(exc.value)
    with pytest.raises(ValueError):
        McpHttpClient("https://mcp.local", "")


def test_missing_key_exits_explicitly_with_no_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "kb"
    peer = _Peer([])
    assert main(["--repo", "o/n", "--out", str(out)], {}, peer) == 2
    err = capsys.readouterr().err
    assert "GREPTILE_API_KEY" in err
    assert peer.requests == []
    assert not out.exists()


def test_sync_auth_denial_returns_failure_without_credential(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def denied(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "x", Message(), None)

    out = tmp_path / "kb"
    assert (
        main(
            ["--repo", "o/n", "--out", str(out)],
            {"GREPTILE_API_KEY": TOKEN},
            denied,
        )
        == 1
    )
    err = capsys.readouterr().err
    assert TOKEN not in err
    assert "GREPTILE_API_KEY" in err
