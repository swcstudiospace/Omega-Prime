"""HTTP client for substrate-mcp: brief, events, shared memory.

Omega Prime is the ``grok-bot`` surface. Brief fetches and event emits are
fail-open (they degrade to ``""`` / ``stored: False`` so a substrate outage
never blocks a turn); shared memory writes are fail-closed (a lost write
raises so no caller mistakes it for landed). Upstream kinds mirror
``packages/mcp-server/src/types.ts``; tool names use the underscore form
from ``mcp.ts``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from omega_prime.providers.http import HttpTransport

DEFAULT_BASE_URL = "http://127.0.0.1:7410"
DEFAULT_SURFACE = "grok-bot"

# Mirrored from agent-substrate packages/mcp-server/src/types.ts.
EVENT_KINDS = (
    "session.start",
    "prompt",
    "claim",
    "tool.call",
    "file.edit",
    "shell",
    "commit",
    "pr",
    "handoff",
    "session.end",
    "note",
    "warning",
)
MEMORY_KINDS = ("decision", "fact", "warning", "handoff", "todo", "teachable")

_SUBSTRATE_PROVIDER = SimpleNamespace(
    name="substrate",
    requires_key=True,
    env_vars=("SUBSTRATE_TOKEN", "SUBSTRATE_TOKEN_GROK_BOT"),
)


class SubstrateError(Exception):
    """The substrate call failed and the caller must know."""


class SubstrateClient:
    """Brief, emit, and shared memory over one substrate-mcp base URL."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        transport: Any = None,
        broker: Any = None,
        token: str | None = None,
        surface: str = DEFAULT_SURFACE,
        timeout: float = 5.0,
        honor_binding: bool = False,
        binding_env: Mapping[str, str] | None = None,
        policy: Any = None,
    ) -> None:
        base = _check_base_url(base_url)
        if not isinstance(surface, str) or surface.strip() == "":
            raise ValueError("surface must be a non-empty string")
        if token is not None and not isinstance(token, str):
            raise ValueError("token must be a string or None")
        self._base_url = base
        self._surface = surface
        self._broker = broker
        self._token = token or ""
        self._transport = transport or HttpTransport(timeout=timeout)
        self._rpc_id = 0
        self._honor_binding = honor_binding
        self._binding_env = binding_env
        self._policy = policy

    @property
    def base_url(self) -> str:
        """The substrate-mcp base URL, without a trailing slash."""
        return self._base_url

    @property
    def surface(self) -> str:
        """The surface name this client emits as."""
        return self._surface

    def health(self) -> dict:
        """GET /healthz. Raises ``SubstrateError`` on any failure."""
        base, error = self._resolve_base()
        if error or not base:
            raise SubstrateError(error or "substrate gateway is not configured")
        try:
            return self._transport.get(base + "/healthz", self._headers(base), {})
        except Exception as exc:
            reason = self._redact(_reason(exc))
            raise SubstrateError(f"substrate health failed: {reason}") from exc

    def brief(
        self,
        repo: str | None = None,
        branch: str | None = None,
        graph_id: str | None = None,
    ) -> str:
        """POST /brief. Fail-open: any failure returns ``""``."""
        body = {"surface": self._surface}
        if repo is not None:
            body["repo"] = repo
        if branch is not None:
            body["branch"] = branch
        if graph_id is not None:
            body["graph_id"] = graph_id
        base, error = self._resolve_base()
        if error or not base:
            return ""
        try:
            return self._transport.post_text(base + "/brief", self._headers(base), body)
        except Exception:
            return ""

    def emit(self, kind: str, summary: str, **fields: Any) -> dict:
        """POST /events. Fail-open: failures return ``stored: False``."""
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind!r}")
        if not isinstance(summary, str) or summary == "":
            raise ValueError("summary must be a non-empty string")
        body = {"kind": kind, "summary": summary, "surface": self._surface}
        body.update(fields)
        base, error = self._resolve_base()
        if error or not base:
            return {"stored": False, "error": error or "substrate gateway is blocked"}
        try:
            result = self._transport.post(base + "/events", self._headers(base), body)
        except Exception as exc:
            return {"stored": False, "error": self._redact(_reason(exc))}
        if not isinstance(result, dict):
            return {"stored": False, "error": "substrate returned no event body"}
        return result

    def memory_write(
        self,
        scope: str,
        kind: str,
        text: str,
        *,
        trust: str | None = None,
        ttl: str | None = None,
        source_event: str | None = None,
    ) -> dict:
        """Call ``memory_write``. Fail-closed: failures raise ``SubstrateError``."""
        if not isinstance(scope, str) or scope == "":
            raise ValueError("scope must be a non-empty string")
        if kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind {kind!r}")
        if not isinstance(text, str) or text == "":
            raise ValueError("text must be a non-empty string")
        arguments: dict[str, Any] = {"scope": scope, "kind": kind, "text": text}
        if trust is not None:
            arguments["trust"] = trust
        if ttl is not None:
            arguments["ttl"] = ttl
        if source_event is not None:
            arguments["source_event"] = source_event
        return self._call_tool("memory_write", arguments)

    def memory_search(
        self, query: str, scope: str | None = None, limit: int | None = None
    ) -> list:
        """Call ``memory_search``. Fail-open: failures return ``[]``."""
        if not isinstance(query, str) or query == "":
            raise ValueError("query must be a non-empty string")
        arguments: dict[str, Any] = {"query": query}
        if scope is not None:
            arguments["scope"] = scope
        if limit is not None:
            arguments["limit"] = limit
        try:
            result = self._call_tool("memory_search", arguments)
        except SubstrateError:
            return []
        entries = result.get("entries", result)
        return entries if isinstance(entries, list) else []

    def docs_search(self, query: str) -> dict:
        """Call ``docs_search``. Failures raise ``SubstrateError``."""
        if not isinstance(query, str) or query == "":
            raise ValueError("query must be a non-empty string")
        return self._call_tool("docs_search", {"query": query})

    def graph_claim(
        self, graph_id: str, node_id: str, session_id: str, ttl_seconds: int = 3600
    ) -> dict:
        """Call ``graph_claim``. Returns the lease dict. Failures raise."""
        _check_graph_ref(graph_id, node_id)
        if not isinstance(session_id, str) or session_id == "":
            raise ValueError("session_id must be a non-empty string")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise ValueError("ttl_seconds must be an integer")
        return self._call_tool(
            "graph_claim",
            {
                "graph_id": graph_id,
                "node_id": node_id,
                "session_id": session_id,
                "surface": self._surface,
                "ttl_seconds": ttl_seconds,
            },
        )

    def graph_release(self, graph_id: str, node_id: str) -> str:
        """Call ``graph_release``. Returns the result text. Failures raise."""
        _check_graph_ref(graph_id, node_id)
        return self._call_tool_text(
            "graph_release", {"graph_id": graph_id, "node_id": node_id}
        )

    def graph_complete(self, graph_id: str, node_id: str) -> str:
        """Call ``graph_complete``. Returns the result text. Failures raise."""
        _check_graph_ref(graph_id, node_id)
        return self._call_tool_text(
            "graph_complete", {"graph_id": graph_id, "node_id": node_id}
        )

    def graph_heartbeat(self, graph_id: str, node_id: str, token: str) -> dict:
        """Call ``graph_heartbeat``. Returns the parsed dict. Failures raise."""
        _check_graph_ref(graph_id, node_id)
        if not isinstance(token, str) or token == "":
            raise ValueError("token must be a non-empty lease token")
        return self._call_tool(
            "graph_heartbeat",
            {"graph_id": graph_id, "node_id": node_id, "token": token},
        )

    def _call_tool_text(self, name: str, arguments: dict) -> str:
        """One MCP ``tools/call`` round trip returning raw text."""
        text = _tool_text(self._mcp_post(name, arguments))
        if text is None:
            raise SubstrateError(f"substrate {name} returned a malformed envelope")
        return text

    def _call_tool(self, name: str, arguments: dict) -> dict:
        """One MCP ``tools/call`` round trip. Failures raise ``SubstrateError``."""
        text = _tool_text(self._mcp_post(name, arguments))
        if text is None:
            raise SubstrateError(f"substrate {name} returned a malformed envelope")
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SubstrateError(
                f"substrate {name} returned unparsable content: {exc}"
            ) from exc
        if not isinstance(decoded, dict):
            raise SubstrateError(f"substrate {name} returned no result object")
        return decoded

    def _mcp_post(self, name: str, arguments: dict) -> Any:
        """POST one MCP envelope. Transport failures raise ``SubstrateError``."""
        self._rpc_id += 1
        envelope = {
            "jsonrpc": "2.0",
            "id": self._rpc_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        base, error = self._resolve_base()
        if error or not base:
            raise SubstrateError(error or "substrate gateway is not configured")
        try:
            return self._transport.post(base + "/mcp", self._headers(base), envelope)
        except Exception as exc:
            reason = self._redact(_reason(exc))
            raise SubstrateError(f"substrate {name} failed: {reason}") from exc

    def _resolve_base(self) -> tuple[str | None, str | None]:
        """``(base, None)`` or ``(None, error)``.

        With ``honor_binding``, a skipped, invalid, or degraded saved gateway
        blocks the call. A ready saved URL replaces the constructor URL and
        is checked against the seat policy. No binding file keeps the
        constructor URL, which is the historical default.
        """
        if not self._honor_binding:
            return self._base_url, None
        from omega_prime.integrations.gateways import gateway_block, ready_gateway_url

        blocked = gateway_block("substrate", self._binding_env)
        if blocked is not None:
            return None, str(blocked.get("error") or "substrate gateway is blocked")
        saved = ready_gateway_url("substrate", self._binding_env)
        if saved:
            if not self._saved_host_allowed(saved):
                host = urlsplit(saved).hostname or "that host"
                return None, f"The seat policy does not allow {host}."
            return saved.rstrip("/"), None
        return self._base_url, None

    def _saved_host_allowed(self, url: str) -> bool:
        if self._policy is None:
            return True
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            return False
        return bool(host) and bool(self._policy.allows_host(host))

    def _headers(self, base: str | None = None) -> dict:
        """Authorization header, or none for anonymous local runs."""
        target = base or self._base_url
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        if self._broker is None:
            return {}
        token = self._broker.key_for(_SUBSTRATE_PROVIDER, target)
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _redact(self, text: str) -> str:
        if self._token and isinstance(text, str):
            text = text.replace(self._token, "[REDACTED]")
        if self._broker is None:
            return text
        try:
            return self._broker.redact(text)
        except Exception:
            return text


def _check_base_url(base_url: str) -> str:
    if not isinstance(base_url, str) or base_url == "":
        raise ValueError("base_url must be a non-empty http(s) URL")
    try:
        parts = urlsplit(base_url)
    except ValueError:
        raise ValueError("base_url must be a non-empty http(s) URL") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("base_url must be a non-empty http(s) URL")
    return base_url.rstrip("/")


def _check_graph_ref(graph_id: str, node_id: str) -> None:
    if not isinstance(graph_id, str) or graph_id == "":
        raise ValueError("graph_id must be a non-empty string")
    if not isinstance(node_id, str) or node_id == "":
        raise ValueError("node_id must be a non-empty string")


def _tool_text(response: Any) -> str | None:
    """First text content of an MCP ``tools/call`` response, if present."""
    if not isinstance(response, dict):
        return None
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return None
    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "text":
        return None
    text = first.get("text")
    return text if isinstance(text, str) else None


def _reason(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_SURFACE",
    "EVENT_KINDS",
    "MEMORY_KINDS",
    "SubstrateClient",
    "SubstrateError",
]
