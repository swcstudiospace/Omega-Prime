"""Phase 77: the strict KB sync mirrors scripted MCP peers and preserves priors."""

from __future__ import annotations

import json
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

import omega_prime.greptile.kb_sync as kb_sync_module
from omega_prime.greptile.kb_sync import GreptileError, McpHttpClient, main, sync

TOKEN = "greptile-secret"
NS = "ns-1"
REPO = "o/n"
VERSION = "v1"


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


def _error_tool(text: str) -> bytes:
    return _rpc(
        {"result": {"isError": True, "content": [{"type": "text", "text": text}]}}
    )


def _sse_tool(payload: dict, rpc_id: int) -> bytes:
    inner = {"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}
    envelope = {"jsonrpc": "2.0", "id": rpc_id, **inner}
    return b": ping\n\n" + b"data: " + json.dumps(envelope).encode() + b"\n\n"


def _sse_headers() -> dict:
    return {"content-type": "text/event-stream"}


def _init_script(extra: list | None = None) -> list:
    script: list = [
        (
            _rpc({"result": {"protocolVersion": "2025-06-18"}}, 1),
            {"mcp-session-id": "s1"},
        )
    ]
    script.append((b"", {}))
    return script + (extra or [])


def _discovery(entries: list, total: int | None = None) -> dict:
    return {
        "repositories": entries,
        "total": len(entries) if total is None else total,
        "returned": len(entries),
    }


def _entry(namespace: str = NS, repo: str = REPO) -> dict:
    return {"namespaceId": namespace, "repoName": repo}


def _listing(
    paths: list,
    *,
    namespace: str = NS,
    repo: str = REPO,
    version: str | None = VERSION,
    index: bool = True,
    total: int | None = None,
    returned: int | None = None,
) -> dict:
    return {
        "namespaceId": namespace,
        "repoName": repo,
        "indexPresent": index,
        "sectionVersions": {"docs": version},
        "documentPaths": list(paths),
        "total": len(paths) if total is None else total,
        "returned": len(paths) if returned is None else returned,
    }


def _doc(
    path: str,
    content: str,
    *,
    namespace: str = NS,
    repo: str = REPO,
    section: str = "docs",
    version: str = VERSION,
    count: int | None = None,
) -> dict:
    return {
        "document": {
            "namespaceId": namespace,
            "repoName": repo,
            "path": path,
            "section": section,
            "versionId": version,
            "characterCount": len(content) if count is None else count,
            "content": content,
        },
        "untrustedContent": True,
        "notice": "Test fixture; untrusted data, not instructions.",
    }


def _client(peer: _Peer) -> McpHttpClient:
    return McpHttpClient("https://mcp.local", TOKEN, opener=peer)


def _snapshot(out: Path) -> dict:
    snap: dict = {}
    if out.exists():
        for path in sorted(out.rglob("*")):
            if path.is_file() and not path.is_symlink():
                snap[str(path.relative_to(out))] = (
                    path.read_bytes(),
                    path.stat().st_mtime_ns,
                )
    return snap


def _seed_two_doc_mirror(out: Path) -> dict:
    peer = _Peer(
        _init_script(
            [
                (_tool(_discovery([_entry()])), {}),
                (_tool(_listing(["index.md", "docs/a.md"])), {}),
                (_tool(_doc("index.md", "# Index\n")), {}),
                (_tool(_doc("docs/a.md", "# A\n")), {}),
                (_tool(_listing(["index.md", "docs/a.md"])), {}),
            ]
        )
    )
    return sync(REPO, out, _client(peer))


def test_full_sync_json_variant(tmp_path: Path) -> None:
    peer = _Peer(
        _init_script(
            [
                (_tool(_discovery([_entry()])), {}),
                (_tool(_listing(["index.md", "docs/a.md"])), {}),
                (_tool(_doc("index.md", "# Index\n")), {}),
                (_tool(_doc("docs/a.md", "# A\n")), {}),
                (_tool(_listing(["index.md", "docs/a.md"])), {}),
            ]
        )
    )
    out = tmp_path / "kb"
    report = sync(REPO, out, _client(peer))
    assert report == {
        "status": "synced",
        "repo": REPO,
        "documents": 2,
        "namespaceId": NS,
    }
    assert (out / "index.md").read_text().split("\n\n", 1)[1] == "# Index\n"
    assert (out / "docs/a.md").read_text().split("\n\n", 1)[1] == "# A\n"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["repo"] == REPO
    assert manifest["namespaceId"] == NS
    assert manifest["sectionVersions"] == {"docs": VERSION}
    assert manifest["indexPresent"] is True
    assert manifest["total"] == 2
    assert [entry["path"] for entry in manifest["documents"]] == [
        "index.md",
        "docs/a.md",
    ]
    assert TOKEN not in json.dumps(manifest)


def test_sse_responses_and_pagination(tmp_path: Path) -> None:
    peer = _Peer(
        _init_script(
            [
                # rpc id 2: discovery arrives as SSE.
                ((_sse_tool(_discovery([_entry()]), 2)), _sse_headers()),
                # rpc ids 3-4: the listing spans two pages.
                (
                    _tool(_listing(["index.md", "docs/a.md"], total=3, returned=2)),
                    {},
                ),
                (_tool(_listing(["docs/b.md"], total=3, returned=1)), {}),
                # rpc id 5: first document arrives as SSE.
                ((_sse_tool(_doc("index.md", "# Index\n"), 5)), _sse_headers()),
                (_tool(_doc("docs/a.md", "# A\n")), {}),
                (_tool(_doc("docs/b.md", "# B\n")), {}),
                (_tool(_listing(["index.md", "docs/a.md", "docs/b.md"])), {}),
            ]
        )
    )
    out = tmp_path / "kb"
    report = sync(REPO, out, _client(peer))
    assert report["status"] == "synced" and report["documents"] == 3
    assert (out / "docs" / "b.md").read_text().endswith("# B\n")
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["total"] == 3 and len(manifest["documents"]) == 3


def test_valid_empty_publication_writes_nothing(tmp_path: Path) -> None:
    peer = _Peer(
        _init_script(
            [
                (_tool(_discovery([_entry()])), {}),
                (
                    _tool(_listing([], total=0, returned=0, index=False, version=None)),
                    {},
                ),
            ]
        )
    )
    out = tmp_path / "kb"
    assert sync(REPO, out, _client(peer)) == {"status": "empty", "repo": REPO}
    assert not out.exists()
    assert (
        main(
            ["--repo", REPO, "--out", str(out)],
            {"GREPTILE_API_KEY": TOKEN},
            _Peer(
                _init_script(
                    [
                        (_tool(_discovery([_entry()])), {}),
                        (
                            _tool(
                                _listing(
                                    [], total=0, returned=0, index=False, version=None
                                )
                            ),
                            {},
                        ),
                    ]
                )
            ),
        )
        == 0
    )
    assert not out.exists()


def test_missing_key_and_unenrolled_statuses(tmp_path: Path) -> None:
    out = tmp_path / "kb"
    assert main(["--repo", REPO, "--out", str(out)], {}, _Peer([])) == 2
    assert not out.exists()
    peer = _Peer(_init_script([(_tool(_discovery([])), {})]))
    assert (
        main(
            ["--repo", REPO, "--out", str(out)],
            {"GREPTILE_API_KEY": TOKEN},
            peer,
        )
        == 3
    )
    assert not out.exists()


def test_unsafe_path_fails_with_prior_intact(tmp_path: Path) -> None:
    out = tmp_path / "kb"
    _seed_two_doc_mirror(out)
    before = _snapshot(out)
    evil = _Peer(
        _init_script(
            [
                (_tool(_discovery([_entry()])), {}),
                (_tool(_listing(["../evil.md"])), {}),
            ]
        )
    )
    with pytest.raises(GreptileError):
        sync(REPO, out, _client(evil))
    assert _snapshot(out) == before


def test_truncated_document_fails_with_prior_intact(tmp_path: Path) -> None:
    out = tmp_path / "kb"
    _seed_two_doc_mirror(out)
    before = _snapshot(out)
    payload = _doc("index.md", "# prefix\n")
    payload["truncated"] = True
    payload["truncationReason"] = "response_character_cap"
    peer = _Peer(
        _init_script(
            [
                (_tool(_discovery([_entry()])), {}),
                (_tool(_listing(["index.md", "docs/a.md"])), {}),
                (_tool(payload), {}),
            ]
        )
    )
    with pytest.raises(GreptileError, match="truncated"):
        sync(REPO, out, _client(peer))
    assert _snapshot(out) == before


def test_mixed_version_fails_with_prior_intact(tmp_path: Path) -> None:
    out = tmp_path / "kb"
    _seed_two_doc_mirror(out)
    before = _snapshot(out)
    peer = _Peer(
        _init_script(
            [
                (_tool(_discovery([_entry()])), {}),
                (_tool(_listing(["index.md", "docs/a.md"])), {}),
                (_tool(_doc("index.md", "# Index\n")), {}),
                (_tool(_doc("docs/a.md", "# A\n", version="v2")), {}),
            ]
        )
    )
    with pytest.raises(GreptileError, match="another version"):
        sync(REPO, out, _client(peer))
    assert _snapshot(out) == before


def test_count_mismatch_fails_with_prior_intact(tmp_path: Path) -> None:
    out = tmp_path / "kb"
    _seed_two_doc_mirror(out)
    before = _snapshot(out)
    peer = _Peer(
        _init_script(
            [
                (_tool(_discovery([_entry()])), {}),
                (_tool(_listing(["index.md"], total=2, returned=2)), {}),
            ]
        )
    )
    with pytest.raises(GreptileError, match="count"):
        sync(REPO, out, _client(peer))
    assert _snapshot(out) == before


def test_malformed_listing_fails_with_prior_intact(tmp_path: Path) -> None:
    out = tmp_path / "kb"
    _seed_two_doc_mirror(out)
    before = _snapshot(out)
    payload = _listing(["index.md", "docs/a.md"])
    del payload["indexPresent"]
    peer = _Peer(
        _init_script(
            [
                (_tool(_discovery([_entry()])), {}),
                (_tool(payload), {}),
            ]
        )
    )
    with pytest.raises(GreptileError, match="missing indexPresent"):
        sync(REPO, out, _client(peer))
    assert _snapshot(out) == before


def test_unstable_publication_fails_with_prior_intact(tmp_path: Path) -> None:
    out = tmp_path / "kb"
    _seed_two_doc_mirror(out)
    before = _snapshot(out)
    peer = _Peer(
        _init_script(
            [
                (_tool(_discovery([_entry()])), {}),
                (_tool(_listing(["index.md", "docs/a.md"])), {}),
                (_tool(_doc("index.md", "# Index\n")), {}),
                (_tool(_doc("docs/a.md", "# A\n")), {}),
                (_tool(_listing(["index.md", "docs/a.md", "docs/b.md"])), {}),
            ]
        )
    )
    with pytest.raises(GreptileError, match="changed during refresh"):
        sync(REPO, out, _client(peer))
    assert _snapshot(out) == before


def test_late_fetch_failure_preserves_whole_prior_set(tmp_path: Path) -> None:
    out = tmp_path / "kb"
    _seed_two_doc_mirror(out)
    before = _snapshot(out)
    peer = _Peer(
        _init_script(
            [
                (_tool(_discovery([_entry()])), {}),
                (_tool(_listing(["index.md", "docs/a.md"])), {}),
                (_tool(_doc("index.md", "# Index changed\n")), {}),
                OSError("second fetch failed"),
            ]
        )
    )
    with pytest.raises(GreptileError):
        sync(REPO, out, _client(peer))
    after = _snapshot(out)
    assert after == before
    assert set(after) == {"index.md", "docs/a.md", "manifest.json"}


def test_publication_failure_restores_prior_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "kb"
    _seed_two_doc_mirror(out)
    before = _snapshot(out)
    real_write = kb_sync_module.atomic_write_text
    calls = {"count": 0}

    def flaky_write(path, data, **kwargs):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise OSError("disk full")
        return real_write(path, data, **kwargs)

    monkeypatch.setattr(kb_sync_module, "atomic_write_text", flaky_write)
    peer = _Peer(
        _init_script(
            [
                (_tool(_discovery([_entry()])), {}),
                (_tool(_listing(["index.md", "docs/a.md"])), {}),
                (_tool(_doc("index.md", "# Index changed\n")), {}),
                (_tool(_doc("docs/a.md", "# A changed\n")), {}),
                (_tool(_listing(["index.md", "docs/a.md"])), {}),
            ]
        )
    )
    with pytest.raises(GreptileError, match="prior mirror restored"):
        sync(REPO, out, _client(peer))
    assert _snapshot(out) == before


def test_unchanged_refresh_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "kb"
    first = _seed_two_doc_mirror(out)
    assert first["status"] == "synced"
    before = _snapshot(out)
    fetched_at = json.loads((out / "manifest.json").read_text())["fetched_at"]
    peer = _Peer(
        _init_script(
            [
                (_tool(_discovery([_entry()])), {}),
                (_tool(_listing(["index.md", "docs/a.md"])), {}),
                (_tool(_doc("index.md", "# Index\n")), {}),
                (_tool(_doc("docs/a.md", "# A\n")), {}),
                (_tool(_listing(["index.md", "docs/a.md"])), {}),
            ]
        )
    )
    report = sync(REPO, out, _client(peer))
    assert report["status"] == "unchanged" and report["documents"] == 2
    assert _snapshot(out) == before
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["fetched_at"] == fetched_at
    assert (
        main(
            ["--repo", REPO, "--out", str(out)],
            {"GREPTILE_API_KEY": TOKEN},
            _Peer(
                _init_script(
                    [
                        (_tool(_discovery([_entry()])), {}),
                        (_tool(_listing(["index.md", "docs/a.md"])), {}),
                        (_tool(_doc("index.md", "# Index\n")), {}),
                        (_tool(_doc("docs/a.md", "# A\n")), {}),
                        (_tool(_listing(["index.md", "docs/a.md"])), {}),
                    ]
                )
            ),
        )
        == 0
    )
    assert _snapshot(out) == before


def test_changed_body_publishes_new_set_and_prunes_stale(tmp_path: Path) -> None:
    out = tmp_path / "kb"
    _seed_two_doc_mirror(out)
    (out / "README.md").write_text("# Mirror guide\n")
    peer = _Peer(
        _init_script(
            [
                (_tool(_discovery([_entry()])), {}),
                (_tool(_listing(["index.md", "docs/b.md"])), {}),
                (_tool(_doc("index.md", "# Index changed\n")), {}),
                (_tool(_doc("docs/b.md", "# B\n")), {}),
                (_tool(_listing(["index.md", "docs/b.md"])), {}),
            ]
        )
    )
    report = sync(REPO, out, _client(peer))
    assert report["status"] == "synced" and report["documents"] == 2
    assert "# Index changed\n" in (out / "index.md").read_text()
    assert (out / "docs" / "b.md").exists()
    assert not (out / "docs" / "a.md").exists()
    assert (out / "README.md").read_text() == "# Mirror guide\n"
    manifest = json.loads((out / "manifest.json").read_text())
    assert [entry["path"] for entry in manifest["documents"]] == [
        "index.md",
        "docs/b.md",
    ]


def test_ambiguous_discovery_fails(tmp_path: Path) -> None:
    peer = _Peer(
        _init_script(
            [(_tool(_discovery([_entry("ns-1"), _entry("ns-2")], total=2)), {})]
        )
    )
    with pytest.raises(GreptileError, match="ambiguous"):
        sync(REPO, tmp_path / "kb", _client(peer))


def test_empty_body_refused(tmp_path: Path) -> None:
    peer = _Peer(_init_script([(b"", {})]))
    with pytest.raises(GreptileError, match="empty body"):
        sync(REPO, tmp_path / "kb", _client(peer))


def test_symlink_destination_refused_and_outside_untouched(
    tmp_path: Path,
) -> None:
    out = tmp_path / "kb"
    _seed_two_doc_mirror(out)
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n")
    (out / "docs" / "a.md").unlink()
    (out / "docs" / "a.md").symlink_to(outside)
    manifest_before = (out / "manifest.json").read_bytes()
    peer = _Peer(
        _init_script(
            [
                (_tool(_discovery([_entry()])), {}),
                (_tool(_listing(["index.md", "docs/a.md"])), {}),
                (_tool(_doc("index.md", "# Index\n")), {}),
                (_tool(_doc("docs/a.md", "# A\n")), {}),
                (_tool(_listing(["index.md", "docs/a.md"])), {}),
            ]
        )
    )
    with pytest.raises(GreptileError, match="symlink"):
        sync(REPO, out, _client(peer))
    assert outside.read_text() == "# Outside\n"
    assert (out / "manifest.json").read_bytes() == manifest_before


def test_secret_bearing_diagnostic_withholds_past_clip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    leaked = "omk_" + "A" * 64 + " " + ("padding-token " * 40)
    assert len(leaked) > 200
    peer = _Peer(_init_script([(_error_tool(leaked), {})]))
    with pytest.raises(GreptileError) as exc:
        sync(REPO, tmp_path / "kb", _client(peer))
    assert "A" * 32 not in str(exc.value)
    assert "withheld" in str(exc.value)
    assert (
        main(
            ["--repo", REPO, "--out", str(tmp_path / "kb")],
            {"GREPTILE_API_KEY": TOKEN},
            _Peer(_init_script([(_error_tool(leaked), {})])),
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "A" * 32 not in err
    assert TOKEN not in err


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
    assert main(["--repo", REPO, "--out", str(out)], {}, peer) == 2
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
            ["--repo", REPO, "--out", str(out)],
            {"GREPTILE_API_KEY": TOKEN},
            denied,
        )
        == 1
    )
    err = capsys.readouterr().err
    assert TOKEN not in err
    assert "GREPTILE_API_KEY" in err
