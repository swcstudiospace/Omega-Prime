"""Phase 27: systems tool family behind fake stores and servers."""

from __future__ import annotations

import json
import sys

from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.registry import ToolRegistry
from omega_prime.tools.systems import (
    SystemsClient,
    SystemsContext,
    register_systems_tools,
)

LSP_FIXTURE = r"""
import json
import sys

out = sys.stdout.buffer
inp = sys.stdin.buffer


def read_message():
    headers = b""
    while b"\r\n\r\n" not in headers:
        chunk = inp.read(1)
        if not chunk:
            return None
        headers += chunk
    head, rest = headers.split(b"\r\n\r\n", 1)
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    body = rest
    while len(body) < length:
        body += inp.read(length - len(body))
    return json.loads(body.decode("utf-8"))


def send(message):
    body = json.dumps(message).encode("utf-8")
    out.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    out.flush()


while True:
    message = read_message()
    if message is None:
        break
    method = message.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}})
    elif method == "textDocument/didOpen":
        send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
              "params": {"uri": message["params"]["textDocument"]["uri"],
                         "diagnostics": [{"message": "fixture: unused import", "severity": 2}]}})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": message["id"], "result": None})
    elif method == "exit":
        break
"""


def _ctx(tmp_path, **overrides):
    base = {
        "root": tmp_path,
        "timescale": lambda sql, limit: {"rows": [{"sql": sql, "limit": limit}]},
        "greptime": lambda sql, limit: {"rows": []},
        "vcs_show": lambda ref, path: f"{ref}:{path}:content",
    }
    base.update(overrides)
    return SystemsContext(**base)


def test_sql_queries_pass_through_and_fail_open(tmp_path):
    client = SystemsClient(_ctx(tmp_path))
    assert client.index_query("select 1", limit=5) == {
        "rows": [{"sql": "select 1", "limit": 5}]
    }
    assert client.events_query("select 2") == {"rows": []}
    failing = SystemsClient(
        _ctx(tmp_path, timescale=lambda sql, limit: {"error": "db_down", "reason": "x"})
    )
    assert failing.index_query("select 1") == {
        "rows": [],
        "error": "db_down",
        "reason": "x",
    }
    bare = SystemsClient(SystemsContext(root=tmp_path))
    assert bare.index_query("select 1")["error"].startswith("not_configured")
    assert bare.events_query("select 1")["error"].startswith("not_configured")


def test_sql_queries_stay_read_only(tmp_path):
    import pytest

    client = SystemsClient(_ctx(tmp_path))
    assert client.index_query("WITH recent AS (SELECT 1) SELECT * FROM recent;")["rows"]
    assert client.events_query("-- nightly\nselect 2") == {"rows": []}
    for bad in (
        "DELETE FROM events",
        "update rows set x = 1",
        "DROP TABLE t",
        "select 1; delete from t",
        "/* x */ insert into t values (1)",
        "EXPLAIN DELETE FROM t",
        "",
    ):
        with pytest.raises(ValueError):
            client.index_query(bad)
    assert client.index_query("EXPLAIN SELECT 1")["rows"]


def test_cache_namespaces_expires_and_refuses_secrets(tmp_path):
    client = SystemsClient(_ctx(tmp_path))
    assert client.cache("set", "k", value="v") == {"key": "k", "ok": True}
    assert client.cache("get", "k") == {"key": "k", "value": "v"}
    assert client.cache("delete", "k") == {"key": "k", "deleted": True}
    assert client.cache("get", "k")["value"] is None
    client.cache("set", "gone", value="v", ttl_sec=0)
    assert client.cache("get", "gone")["value"] is None
    assert "secret_refused" in client.cache("set", "s", value="token=abc")["error"]


def test_lsp_tier1_gating_and_session(tmp_path):
    (tmp_path / "a.py").write_text("import os\n", encoding="utf-8")
    server = tmp_path / "fake_lsp.py"
    server.write_text(LSP_FIXTURE, encoding="utf-8")
    client = SystemsClient(_ctx(tmp_path))
    ok = client.lsp_diagnostics([sys.executable, str(server)], "a.py")
    assert ok["path"] == "a.py"
    assert ok["diagnostics"] == [{"message": "fixture: unused import", "severity": 2}]
    assert (
        "language_not_tier1"
        in client.lsp_diagnostics([sys.executable], "a.rs")["error"]
    )
    assert (
        "language_not_tier1"
        in client.lsp_diagnostics([sys.executable], "a.txt")["error"]
    )
    assert (
        "repo-relative" in client.lsp_diagnostics([sys.executable], "/abs.py")["error"]
    )
    assert "argv list" in client.lsp_diagnostics("python3", "a.py")["error"]
    assert (
        "missing"
        in client.lsp_diagnostics([sys.executable, str(server)], "missing.py")["error"]
    )


def test_contract_propose_bundle_shape_and_refusals(tmp_path):
    client = SystemsClient(_ctx(tmp_path))
    bundle = client.contract_propose(
        "contracts/api.json",
        '{"v": 1}',
        "1.2.0",
        "add field",
        False,
        ["web"],
        change_id="ct-1",
        migration_note="none",
    )
    assert bundle["ok"] is True and bundle["pushed"] is False
    assert bundle["branch"] == "bot-00-omega-prime/contract-ct-1"
    assert bundle["bundle"]["pr_title"] == "contract: ct-1 (additive)"
    record = bundle["bundle"]["files"]["contracts/changes/ct-1.yaml"]
    assert (
        "change_id: ct-1" in record
        and "- web" in record
        and "acknowledgements: []" in record
    )
    assert "operator commits" in bundle["reason"]
    assert (
        "secret_refused"
        in client.contract_propose("s", "token=x", "1", "s", False, ["w"])["error"]
    )


def test_design_artifact_truncates_and_misses(tmp_path):
    client = SystemsClient(
        _ctx(
            tmp_path,
            vcs_show=lambda ref, path: "z" * 70000 if path == "big.md" else None,
        )
    )
    big = client.design_artifact_get("big.md")
    assert big["truncated"] is True and len(big["content"]) == 60000
    assert "not_found" in client.design_artifact_get("gone.md")["error"]
    assert "repo-relative" in client.design_artifact_get("../x")["error"]


def test_approvals_gate_writes_only(tmp_path):
    log = ApprovalLog()
    registry = ToolRegistry(approval_log=log)
    register_systems_tools(registry, SystemsClient(_ctx(tmp_path)))
    assert (
        json.loads(registry.dispatch("sys_index_query", {"sql": "select 1"}))["rows"]
        != []
    )
    for tool, args in (
        ("sys_cache", {"action": "get", "key": "k"}),
        (
            "sys_contract_propose",
            {
                "surface": "s",
                "body": "b",
                "version": "v",
                "summary": "s",
                "breaking": False,
                "consumers_required": ["w"],
            },
        ),
    ):
        assert json.loads(registry.dispatch(tool, args)) == {
            "error": "approval required",
            "tool": tool,
        }
    assert log.approve("sys_cache", "ada").get("approved") is True
    assert (
        json.loads(registry.dispatch("sys_cache", {"action": "get", "key": "k"}))[
            "value"
        ]
        is None
    )
