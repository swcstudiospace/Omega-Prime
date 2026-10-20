"""HTTP client for substrate-mcp: brief, events, shared memory.

Omega Prime is the ``grok-bot`` surface. Brief fetches and event emits are
fail-open (they degrade to ``""`` / ``stored: False`` so a substrate outage
never blocks a turn); shared memory writes are fail-closed (a lost write
raises so no caller mistakes it for landed). Upstream kinds mirror
``packages/mcp-server/src/types.ts``; tool names use the underscore form
from ``mcp.ts``.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import uuid
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from omega_prime.providers.http import HttpTransport

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


class SubstrateNotConfiguredError(SubstrateError):
    """No substrate gateway URL is configured."""


class SubstrateUnauthorizedError(SubstrateError):
    """The MCP endpoint rejected authentication."""


class SubstrateUnreachableError(SubstrateError):
    """The MCP endpoint could not be reached."""


class SubstrateProtocolError(SubstrateError):
    """The MCP endpoint returned an invalid protocol response."""


class SubstrateToolError(SubstrateError):
    """The MCP tool reported an execution error."""


class SubstrateClient:
    """Brief, emit, and shared memory over one substrate-mcp base URL."""

    def __init__(
        self,
        base_url: str | None = None,
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
        try:
            base = _check_base_url(base_url)
            self._base_invalid = False
        except ValueError:
            base = None
            self._base_invalid = True
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
        self._mcp_sessions: dict[tuple[str, str], str] = {}
        self._honor_binding = honor_binding
        self._binding_env = binding_env
        self._policy = policy

    @property
    def base_url(self) -> str | None:
        """The validated substrate-mcp base URL, if configured."""
        return self._base_url

    @property
    def surface(self) -> str:
        """The surface name this client emits as."""
        return self._surface

    def health(self) -> dict:
        """GET /healthz. Raises ``SubstrateError`` on any failure."""
        base, error = self._resolve_base()
        if error or not base:
            message = error or "substrate gateway is not configured"
            if "is not configured" in message:
                raise SubstrateNotConfiguredError(message)
            raise SubstrateProtocolError(message)
        try:
            return self._transport.get(base + "/healthz", self._headers(base), {})
        except Exception:
            raise SubstrateError("substrate health failed") from None

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
            message = error or "substrate gateway is not configured"
            if "is not configured" in message:
                return {"stored": False, "error": f"not_configured: {message}"}
            return {"stored": False, "error": message}
        try:
            result = self._transport.post(base + "/events", self._headers(base), body)
        except Exception:
            return {"stored": False, "error": "substrate transport failed"}
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
        expected_version: Any = None,
        idempotency_key: str | None = None,
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
        if expected_version is not None:
            arguments["expected_version"] = expected_version
        arguments["idempotency_key"] = (
            idempotency_key if idempotency_key is not None else str(uuid.uuid4())
        )
        return self._call_tool("memory_write", arguments)

    def memory_search(
        self,
        query: str,
        scope: str | None = None,
        limit: int | None = None,
        repo: str | None = None,
    ) -> list:
        """Call ``memory_search``. Fail-open: failures return ``[]``."""
        if not isinstance(query, str) or query == "":
            raise ValueError("query must be a non-empty string")
        arguments: dict[str, Any] = {"query": query}
        if scope is not None:
            arguments["scope"] = scope
        if limit is not None:
            arguments["limit"] = limit
        if repo is not None:
            arguments["repo"] = repo
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
        return self._call_tool("docs_search", {"question": query})

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

    def graph_release(self, graph_id: str, node_id: str, lease_id: str) -> str:
        """Call ``graph_release``. Returns the result text. Failures raise."""
        _check_graph_ref(graph_id, node_id)
        _check_lease_id(lease_id)
        return self._call_tool_text(
            "graph_release",
            {"graph_id": graph_id, "node_id": node_id, "lease_id": lease_id},
        )

    def graph_complete(self, graph_id: str, node_id: str, lease_id: str) -> str:
        """Call ``graph_complete``. Returns the result text. Failures raise."""
        _check_graph_ref(graph_id, node_id)
        _check_lease_id(lease_id)
        return self._call_tool_text(
            "graph_complete",
            {"graph_id": graph_id, "node_id": node_id, "lease_id": lease_id},
        )

    def graph_heartbeat(self, graph_id: str, node_id: str, lease_id: str) -> dict:
        """Call ``graph_heartbeat``. Returns the parsed dict. Failures raise."""
        _check_graph_ref(graph_id, node_id)
        _check_lease_id(lease_id)
        return self._call_tool(
            "graph_heartbeat",
            {"graph_id": graph_id, "node_id": node_id, "lease_id": lease_id},
        )

    def _call_tool_text(self, name: str, arguments: dict) -> str:
        """One MCP ``tools/call`` round trip returning raw text."""
        text = _tool_text(self._mcp_post(name, arguments))
        if text is None:
            raise SubstrateProtocolError("substrate tool returned a malformed result")
        return text

    def _call_tool(self, name: str, arguments: dict) -> dict:
        """One MCP ``tools/call`` round trip. Failures raise ``SubstrateError``."""
        text = _tool_text(self._mcp_post(name, arguments))
        if text is None:
            raise SubstrateProtocolError("substrate tool returned a malformed result")
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            raise SubstrateProtocolError(
                "substrate tool returned invalid JSON"
            ) from None
        if not isinstance(decoded, dict):
            raise SubstrateProtocolError("substrate tool returned no result object")
        return decoded

    def _mcp_post(self, name: str, arguments: dict) -> Any:
        """POST and validate one MCP response, using only scoped session state."""
        self._rpc_id += 1
        request_id = self._rpc_id
        envelope = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        base, error = self._resolve_base()
        if error or not base:
            message = error or "substrate gateway is not configured"
            if "is not configured" in message:
                raise SubstrateNotConfiguredError(message)
            raise SubstrateProtocolError(message)
        endpoint = base + "/mcp"
        headers = self._headers(base)
        auth_scope = hashlib.sha256(
            headers.get("Authorization", "").encode()
        ).hexdigest()
        scope = (endpoint, auth_scope)
        session_id = self._mcp_sessions.get(scope)
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        retried = False
        while True:
            try:
                response_context = self._transport.post_mcp(endpoint, headers, envelope)
                with response_context as response:
                    if response.status in (401, 403):
                        raise SubstrateUnauthorizedError(
                            "substrate authentication failed"
                        )
                    if response.status == 404 and session_id and not retried:
                        self._mcp_sessions.pop(scope, None)
                        session_id = None
                        headers.pop("Mcp-Session-Id", None)
                        retried = True
                        continue
                    if response.status < 200 or response.status >= 300:
                        raise SubstrateProtocolError(
                            f"substrate returned HTTP {response.status}"
                        )
                    received_session = response.headers.get("mcp-session-id")
                    if received_session:
                        self._mcp_sessions[scope] = received_session
                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    if content_type == "application/json":
                        raw = _read_all(response)
                        try:
                            message = json.loads(raw.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            raise SubstrateProtocolError(
                                "substrate returned malformed JSON"
                            ) from None
                        messages = [message]
                    elif content_type == "text/event-stream":
                        messages = _matching_sse_message(response, request_id)
                    else:
                        raise SubstrateProtocolError(
                            "substrate returned an unsupported content type"
                        )
                    response_message = next(
                        (
                            candidate
                            for candidate in messages
                            if _valid_response(candidate, request_id)
                        ),
                        None,
                    )
                    if response_message is None:
                        raise SubstrateProtocolError(
                            "substrate response did not match the request"
                        )
                    if response_message.get("error") is not None:
                        raise SubstrateToolError("substrate tool returned an error")
                    result = response_message.get("result")
                    if not isinstance(result, dict):
                        raise SubstrateProtocolError(
                            "substrate response has no result object"
                        )
                    if result.get("isError") is True:
                        raise SubstrateToolError("substrate tool reported an error")
                    return response_message
            except SubstrateError:
                raise
            except Exception:
                raise SubstrateUnreachableError("substrate transport failed") from None

    def _resolve_base(self) -> tuple[str | None, str | None]:
        """Return the validated endpoint, or a safe blocking reason."""
        if not self._honor_binding:
            if self._base_invalid:
                return None, "configured substrate URL is unsafe"
            return (
                self._base_url,
                None if self._base_url else "substrate gateway is not configured",
            )
        from omega_prime.integrations.gateways import gateway_block, ready_gateway_url

        blocked = gateway_block("substrate", self._binding_env)
        if blocked is not None:
            return None, str(blocked.get("error") or "substrate gateway is blocked")
        saved = ready_gateway_url("substrate", self._binding_env)
        if saved:
            try:
                checked = _check_base_url(saved)
            except ValueError:
                return None, "saved substrate gateway URL is unsafe"
            if checked is None or not self._saved_host_allowed(checked):
                return None, "saved substrate gateway is not allowed by seat policy"
            return checked, None
        return self._base_url, (
            "configured substrate URL is unsafe"
            if self._base_invalid
            else None
            if self._base_url
            else "substrate gateway is not configured"
        )

    def _saved_host_allowed(self, url: str) -> bool:
        if self._policy is None:
            return True
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            return False
        return bool(host) and bool(self._policy.allows_host(host))

    def _headers(self, base: str | None = None) -> dict:
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


def _check_base_url(base_url: str | None) -> str | None:
    if base_url is None or base_url == "":
        return None
    if not isinstance(base_url, str) or base_url != base_url.strip():
        raise ValueError("base_url is not a safe HTTPS URL")
    if base_url == "local":
        return "http://127.0.0.1:7410"
    try:
        parts = urlsplit(base_url)
        port = parts.port
    except ValueError:
        raise ValueError("base_url is not a safe HTTPS URL") from None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or (port is not None and not 1 <= port <= 65535)
        or parts.query
        or parts.fragment
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in base_url)
    ):
        raise ValueError("base_url is not a safe HTTPS URL")
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


def _valid_response(message: Any, request_id: Any) -> bool:
    return (
        isinstance(message, dict)
        and message.get("jsonrpc") == "2.0"
        and type(message.get("id")) is type(request_id)
        and message.get("id") == request_id
    )


def _read_all(response: Any) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = response.read(4096)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _matching_sse_message(response: Any, request_id: Any) -> list[dict]:
    buffer = ""
    data_lines: list[str] = []
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    while True:
        chunk = response.read(4096)
        if not chunk:
            buffer += decoder.decode(b"", final=True)
            line = buffer.rstrip("\r")
            if line.startswith("data:"):
                data = line[5:]
                data_lines.append(data[1:] if data.startswith(" ") else data)
            break
        buffer += decoder.decode(chunk)
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.rstrip("\r")
            if line == "":
                message = _parse_sse_data(data_lines)
                data_lines = []
                if message is not None and not isinstance(message, dict):
                    raise SubstrateProtocolError(
                        "substrate returned malformed SSE data"
                    )
                if isinstance(message, dict) and _valid_response(message, request_id):
                    return [message]
            elif line.startswith(":"):
                continue
            elif line.startswith("data:"):
                data = line[5:]
                data_lines.append(data[1:] if data.startswith(" ") else data)
    message = _parse_sse_data(data_lines)
    if message is not None and not isinstance(message, dict):
        raise SubstrateProtocolError("substrate returned malformed SSE data")
    return [message] if isinstance(message, dict) else []


def _parse_sse_data(lines: list[str]) -> Any:
    if not lines:
        return None
    payload = "\n".join(lines)
    if payload == "":
        return None
    try:
        message = json.loads(payload)
        if message is None:
            raise SubstrateProtocolError("substrate returned malformed SSE data")
        return message
    except json.JSONDecodeError:
        raise SubstrateProtocolError("substrate returned malformed SSE data") from None


__all__ = [
    "DEFAULT_SURFACE",
    "EVENT_KINDS",
    "MEMORY_KINDS",
    "SubstrateClient",
    "SubstrateError",
    "SubstrateNotConfiguredError",
    "SubstrateProtocolError",
    "SubstrateToolError",
    "SubstrateUnauthorizedError",
    "SubstrateUnreachableError",
]


def _check_lease_id(lease_id: str) -> None:
    if not isinstance(lease_id, str) or lease_id == "":
        raise ValueError("lease_id must be a non-empty string")
