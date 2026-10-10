# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Black-box conformance checks for a running Grok Bot tool host.

`run_verification` drives plain HTTP and both MCP transports against a host that
is already up (this process did not start it). Failures and timeouts become
`fail` checks; a check never raises. Tokens are never printed, logged, or placed
in the report.

CLI: `python -m omega_prime.grokbot.verify --url URL [--json]`. Exit 0 when no
check fails, 1 when any check fails, 2 on usage errors or an unreachable URL.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import quote, urlencode

import httpx2
from mcp.client import Client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from omega_prime.grokbot.audit import redact_sensitive
from omega_prime.grokbot.secret_guard import SecretLeakError, assert_no_secrets
from omega_prime.grokbot.telemetry import parse_traceparent
from omega_prime.tooling.fs import read_secret_file

_READ_TOOL = "todo_read"
_UNKNOWN_TOOL = "verify_unknown_tool"
_APPROVAL_TTL_SECONDS = 30.0
_DETAIL_LIMIT = 200
_TRANSPORTS = ("sse", "http")
_TRANSPORT_STEPS: tuple[tuple[str, str], ...] = (
    ("initialize", "session initializes"),
    ("list_tools", "tool list matches the manifest"),
    ("call_tool", "read-only tool call"),
    ("unknown_tool", "unknown tool is an error"),
    ("gated_tool_refused", "gated tool requires approval"),
)
_SSE_ACCEPT = "application/json, text/event-stream"
_PRE_RUN_MARKERS = ("missing required arguments", "unexpected arguments")
#: Fixed artifact-guard label for every verifier diagnostic publication.
_VERIFIER_GUARD_LABEL = "verifier"

#: Fixed safe detail published when the secret gate trips. A short literal
#: with no excerpts, so it carries no secret bytes by construction.
_VERIFIER_SAFE_DETAIL = "verifier: diagnostic withheld (secret gate tripped)"


class _UsageError(Exception):
    """CLI arguments were rejected. `main` turns this into exit code 2."""


@dataclass(frozen=True)
class Check:
    """One conformance probe. `status` is `pass`, `fail`, or `skip`."""

    id: str
    title: str
    status: str
    detail: str
    duration_ms: float


@dataclass
class VerifyReport:
    """The outcome of one verification run. `ok` is true when nothing failed."""

    base_url: str
    checks: list[Check]
    server_version: str | None = None
    reachable: bool = True

    @property
    def ok(self) -> bool:
        return all(check.status != "fail" for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        """Stable JSON: `ok`, `base_url`, `server_version`, `counts`, `checks`."""
        counts = {"pass": 0, "fail": 0, "skip": 0}
        for check in self.checks:
            if check.status in counts:
                counts[check.status] += 1
        return {
            "ok": self.ok,
            "base_url": self.base_url,
            "server_version": self.server_version,
            "counts": counts,
            "checks": [
                {
                    "id": check.id,
                    "title": check.title,
                    "status": check.status,
                    "detail": check.detail,
                    "duration_ms": check.duration_ms,
                }
                for check in self.checks
            ],
        }


def format_report(report: VerifyReport) -> str:
    """Human table for a report. Check details are already redacted."""
    counts = report.to_dict()["counts"]
    version = report.server_version or "-"
    lines = [
        (
            f"base_url={report.base_url} version={version} "
            f"ok={str(report.ok).lower()} "
            f"pass={counts['pass']} fail={counts['fail']} skip={counts['skip']}"
        )
    ]
    for check in report.checks:
        lines.append(
            f"{check.id:<32} {check.status:<4} {check.duration_ms:8.1f}ms  "
            f"{check.detail}"
        )
    return "\n".join(lines)


def _one_line(text: str, limit: int = _DETAIL_LIMIT) -> str:
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 3] + "..."


def _scrub_detail(detail: str, secrets: Sequence[str]) -> tuple[str, bool]:
    """Redact and gate the FULL `detail` before any flatten/clip.

    Known-minted secrets and audit-shaped credentials are scrubbed from the
    FULL text first; the unchanged ``assert_no_secrets`` gate then runs on
    that FULL redacted text BEFORE ``_one_line`` flattens/clips, so a
    scanner-only fragment (generic high-entropy token, short ``omk_`` form,
    PEM armour) straddling the clip edge cannot be truncated into a
    publishable fragment the gate would no longer recognise. Returns the
    publishable text and whether the gate tripped: on a trip only the fixed
    safe label is published and the caller must retain a failed/non-success
    outcome. The same unchanged gate still runs on the clipped result as
    defense in depth.
    """
    text = detail
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    redacted = redact_sensitive(text)
    try:
        assert_no_secrets(redacted, _VERIFIER_GUARD_LABEL)
    except SecretLeakError:
        return _VERIFIER_SAFE_DETAIL, True
    clipped = _one_line(redacted)
    try:
        assert_no_secrets(clipped, _VERIFIER_GUARD_LABEL)
    except SecretLeakError:
        return _VERIFIER_SAFE_DETAIL, True
    return clipped, False


def _gate_metadata(value: str, secrets: Sequence[str]) -> tuple[str, bool]:
    """Gate a metadata string on its FULL value without clipping.

    Metadata (``base_url``, ``server_version``) is published verbatim on the
    success path, so unlike check details it is never clipped; on a gate
    trip only the fixed safe label is published.
    """
    text = value
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    redacted = redact_sensitive(text)
    try:
        assert_no_secrets(redacted, _VERIFIER_GUARD_LABEL)
    except SecretLeakError:
        return _VERIFIER_SAFE_DETAIL, True
    return redacted, False


def _safe_error_text(text: str, secrets: Sequence[str]) -> str:
    """Gate a CLI error diagnostic on its FULL text before printing.

    Usage/transport errors echo the offending URL or env-var label; when the
    unchanged ``assert_no_secrets`` gate trips on that FULL text, only the
    fixed safe label is published. Clean diagnostics are returned unchanged
    (never clipped), so usage errors keep their exact wording.
    """
    cleaned = text
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "[redacted]")
    redacted = redact_sensitive(cleaned)
    try:
        assert_no_secrets(redacted, _VERIFIER_GUARD_LABEL)
    except SecretLeakError:
        return _VERIFIER_SAFE_DETAIL
    return redacted


def sanitize_report_for_publication(
    report: VerifyReport, secrets: Sequence[str] = ()
) -> VerifyReport:
    """Return a publishable copy of `report` with every field gated on its FULL value.

    Each check detail is re-gated on its FULL stored value (defense in depth
    behind ``_Runner._add``); ``base_url``/``server_version`` metadata is
    gated verbatim. A gate trip publishes only the fixed safe label and
    coerces the outcome to failed -- a metadata trip appends a ``fail`` gate
    check so ``ok`` cannot stay true. Clean reports are returned unchanged in
    shape and content.
    """
    known = list(secrets)
    checks: list[Check] = []
    for check in report.checks:
        cleaned, tripped = _scrub_detail(check.detail, known)
        status = check.status
        if tripped and status != "fail":
            status = "fail"
        checks.append(Check(check.id, check.title, status, cleaned, check.duration_ms))
    base_url = report.base_url
    server_version = report.server_version
    metadata_tripped = False
    gated_base, tripped = _gate_metadata(base_url, known)
    if tripped:
        base_url = _VERIFIER_SAFE_DETAIL
        metadata_tripped = True
    else:
        base_url = gated_base
    if server_version is not None:
        gated_version, tripped = _gate_metadata(server_version, known)
        if tripped:
            server_version = _VERIFIER_SAFE_DETAIL
            metadata_tripped = True
        else:
            server_version = gated_version
    if metadata_tripped:
        checks.append(
            Check(
                "verifier:publication_gate",
                "Publication gate",
                "fail",
                _VERIFIER_SAFE_DETAIL,
                0.0,
            )
        )
    return VerifyReport(
        base_url=base_url,
        checks=checks,
        server_version=server_version,
        reachable=report.reachable,
    )


def _result_text(result: Any) -> str:
    parts: list[str] = []
    for part in getattr(result, "content", ()) or ():
        text = getattr(part, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _required_fields(schema: object) -> list[str]:
    if not isinstance(schema, dict):
        return []
    required = schema.get("required")
    if not isinstance(required, list):
        return []
    return [item for item in required if isinstance(item, str) and item]


def _mentions_approval(is_error: bool, text: str) -> bool:
    return is_error and "approval" in text.lower()


def _rejected_before_run(is_error: bool, text: str) -> bool:
    if not is_error or "approval" in text.lower():
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _PRE_RUN_MARKERS)


def _transport_title(transport: str, subtitle: str) -> str:
    label = "SSE" if transport == "sse" else "HTTP"
    return f"{label} {subtitle}"


class _Runner:
    """Sequential checks against one host. Holds tokens only to send them."""

    def __init__(
        self,
        base: str,
        *,
        http: httpx2.AsyncClient,
        token: str | None,
        admin_token: str | None,
        transports: tuple[str, ...],
        gated_tool: str | None,
        timeout: float,
        require_auth: bool,
        origin_probe: str,
    ) -> None:
        self.base = base
        self.http = http
        self.token = token
        self.admin_token = admin_token
        self.transports = transports
        self.gated_tool = gated_tool or None
        self.timeout = timeout
        self.require_auth = require_auth
        self.origin_probe = origin_probe
        self.checks: list[Check] = []
        self.reachable = False
        self.server_version: str | None = None
        self.auth_mode: str | None = None
        self.rostered: int | None = None
        self.request_id = ""
        self.traceparent = ""
        self.manifest_tools: list[str] = []
        self.approval_required: list[str] = []
        self._secrets = _secret_values(token, admin_token)

    def _clean(self, detail: str) -> str:
        cleaned, _tripped = _scrub_detail(detail, self._secrets)
        return cleaned

    def _add(
        self, check_id: str, title: str, status: str, detail: str, started: float
    ) -> None:
        if status not in ("pass", "fail", "skip"):
            detail = f"bad status {status}: {detail}"
            status = "fail"
        cleaned, tripped = _scrub_detail(detail, self._secrets)
        if tripped and status != "fail":
            status = "fail"
        elapsed = float(round((time.perf_counter() - started) * 1000.0, 3))
        self.checks.append(Check(check_id, title, status, cleaned, elapsed))

    async def _step(
        self,
        check_id: str,
        title: str,
        factory: Callable[[], Awaitable[tuple[str, str]]],
    ) -> None:
        started = time.perf_counter()
        try:
            status, detail = await factory()
        except TimeoutError:
            status, detail = "fail", "timed out"
        except Exception as exc:
            status, detail = "fail", f"{type(exc).__name__}: {exc}"
        self._add(check_id, title, status, detail, started)

    def _missing(self, transport: str, detail: str) -> None:
        cleaned = self._clean(detail)
        seen = {check.id for check in self.checks}
        for suffix, subtitle in _TRANSPORT_STEPS:
            check_id = f"{transport}:{suffix}"
            if check_id in seen:
                continue
            self.checks.append(
                Check(
                    check_id,
                    _transport_title(transport, subtitle),
                    "fail",
                    cleaned,
                    0.0,
                )
            )

    async def _exchange(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        query: dict[str, str] | None = None,
        origin: str | None = None,
        payload: dict[str, Any] | None = None,
        stream: bool = False,
        accept_sse: bool = False,
    ) -> tuple[int, str, str]:
        url = f"{self.base}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if origin:
            headers["Origin"] = origin
        if accept_sse:
            headers["Accept"] = _SSE_ACCEPT
            headers["Content-Type"] = "application/json"
        if stream:
            async with self.http.stream(method, url, headers=headers) as response:
                www = response.headers.get("www-authenticate", "")
                return response.status_code, www, ""
        response = await self.http.request(method, url, headers=headers, json=payload)
        www = response.headers.get("www-authenticate", "")
        return response.status_code, www, response.text

    async def _health(self) -> tuple[str, str]:
        try:
            response = await self.http.get(f"{self.base}/healthz")
        except httpx2.HTTPError as exc:
            return "fail", f"unreachable: {type(exc).__name__}"
        self.reachable = True
        self.request_id = response.headers.get("x-request-id", "")
        self.traceparent = response.headers.get("traceparent", "")
        if response.status_code != 200:
            return "fail", f"HTTP {response.status_code}"
        try:
            body = response.json()
        except ValueError:
            return "fail", "healthz was not JSON"
        if not isinstance(body, dict) or body.get("status") != "healthy":
            return "fail", "status is not healthy"
        version = body.get("version")
        if isinstance(version, str):
            self.server_version = version
        auth = body.get("auth")
        if isinstance(auth, str):
            self.auth_mode = auth
        rostered = body.get("rostered_tools")
        if isinstance(rostered, int) and not isinstance(rostered, bool):
            self.rostered = rostered
        return "pass", f"auth {self.auth_mode or 'unknown'}"

    async def _ready(self) -> tuple[str, str]:
        try:
            response = await self.http.get(f"{self.base}/readyz")
        except httpx2.HTTPError as exc:
            return "fail", f"unreachable: {type(exc).__name__}"
        if response.status_code != 200:
            return "fail", f"HTTP {response.status_code}"
        try:
            body = response.json()
        except ValueError:
            return "fail", "readyz was not JSON"
        if not isinstance(body, dict) or body.get("ready") is not True:
            return "fail", "not ready"
        return "pass", "ready"

    async def _auth_required(self) -> tuple[str, str]:
        if self.auth_mode is None:
            return "fail", "health did not report auth"
        if self.auth_mode == "disabled":
            if self.require_auth:
                return "fail", "authentication is disabled"
            return "skip", "authentication is disabled"
        if self.auth_mode == "required":
            return "pass", "authentication is required"
        return "fail", "unexpected auth mode"

    async def _auth_missing(self) -> tuple[str, str]:
        status, www, _body = await self._exchange("GET", "/manifest.json")
        if status != 401:
            return "fail", f"expected 401, got {status}"
        if "bearer" not in www.lower():
            return "fail", "401 without WWW-Authenticate"
        return "pass", "401 WWW-Authenticate"

    async def _auth_query_token(self) -> tuple[str, str]:
        probe = self.token or "query-token-probe"
        status, _www, _body = await self._exchange(
            "GET",
            "/sse",
            query={"token": probe},
            stream=True,
        )
        if status != 401:
            return "fail", f"expected 401, got {status}"
        return "pass", "query token rejected"

    async def _auth_invalid(self) -> tuple[str, str]:
        status, _www, _body = await self._exchange(
            "GET", "/manifest.json", token="invalid-token-probe"
        )
        if status != 401:
            return "fail", f"expected 401, got {status}"
        return "pass", "invalid token rejected"

    async def _origin_rejected(self) -> tuple[str, str]:
        status, _www, _body = await self._exchange(
            "POST",
            "/mcp",
            token=self.token,
            origin=self.origin_probe,
            payload={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            accept_sse=True,
        )
        if status != 403:
            return "fail", f"expected 403, got {status}"
        return "pass", "origin rejected"

    async def _request_id(self) -> tuple[str, str]:
        if not self.reachable:
            return "fail", "no response"
        if not self.request_id:
            return "fail", "missing X-Request-Id"
        if parse_traceparent(self.traceparent) is None:
            return "fail", "missing or invalid traceparent"
        return "pass", "request id and traceparent present"

    async def _manifest(self) -> tuple[str, str]:
        status, _www, text = await self._exchange(
            "GET", "/manifest.json", token=self.token
        )
        if any(secret and secret in text for secret in (self.token, self.admin_token)):
            return "fail", "response contains the bearer token"
        if status != 200:
            return "fail", f"HTTP {status}"
        try:
            body = json.loads(text)
        except ValueError:
            return "fail", "manifest is not JSON"
        if not isinstance(body, dict):
            return "fail", "manifest is not an object"
        digest = body.get("digest")
        if not isinstance(digest, str) or not digest:
            return "fail", "digest is missing"
        server = body.get("mcp_server")
        if not isinstance(server, dict):
            return "fail", "mcp_server is missing"
        tools = server.get("tools")
        if not isinstance(tools, list) or not all(
            isinstance(item, str) for item in tools
        ):
            return "fail", "tools list is missing"
        declared = server.get("rostered_tool_count", len(tools))
        if (
            self.rostered is None
            or len(tools) != self.rostered
            or declared != self.rostered
        ):
            return "fail", "tool count does not match rostered_tools"
        gated = server.get("approval_required")
        if not isinstance(gated, list) or not all(
            isinstance(item, str) for item in gated
        ):
            return "fail", "approval_required is missing"
        self.manifest_tools = list(tools)
        self.approval_required = list(gated)
        return "pass", f"{len(tools)} tools"

    async def _metrics(self) -> tuple[str, str]:
        anon, _www, _body = await self._exchange("GET", "/metrics")
        if anon != 401:
            return "fail", f"unauthenticated metrics returned {anon}"
        if not self.token:
            return "fail", "no token to read metrics"
        status, _auth, text = await self._exchange("GET", "/metrics", token=self.token)
        if any(secret and secret in text for secret in (self.token, self.admin_token)):
            return "fail", "response contains the bearer token"
        if status != 200:
            return "fail", f"HTTP {status}"
        if "omega_http_requests_total" not in text:
            return "fail", "omega_http_requests_total is missing"
        return "pass", "auth required; omega_http_requests_total present"

    def _gated_name(self) -> str | None:
        if self.gated_tool:
            return self.gated_tool
        if self.approval_required:
            return self.approval_required[0]
        return None

    @asynccontextmanager
    async def _session(self, transport: str) -> AsyncIterator[Client]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        if transport == "sse":
            client_transport = sse_client(
                f"{self.base}/sse",
                headers=headers,
                timeout=self.timeout,
                sse_read_timeout=self.timeout,
            )
            async with Client(
                client_transport,
                mode="legacy",
                read_timeout_seconds=self.timeout,
            ) as client:
                yield client
            return
        if transport != "http":
            raise ValueError(f"unknown transport {transport}")
        async with httpx2.AsyncClient(
            headers=headers,
            trust_env=False,
            timeout=self.timeout,
        ) as http:
            client_transport = streamable_http_client(
                f"{self.base}/mcp", http_client=http
            )
            async with Client(
                client_transport,
                mode="legacy",
                read_timeout_seconds=self.timeout,
            ) as client:
                yield client

    async def _collect_tools(self, client: Client) -> list[Any]:
        tools: list[Any] = []
        cursor: str | None = None
        for _ in range(20):
            listing = await asyncio.wait_for(
                client.list_tools(cursor=cursor), self.timeout
            )
            tools.extend(listing.tools)
            cursor = listing.next_cursor or None
            if cursor is None:
                return tools
        raise RuntimeError("tool list did not end")

    async def _probe(self, client: Client, name: str) -> tuple[bool, str]:
        result = await asyncio.wait_for(client.call_tool(name, {}), self.timeout)
        return bool(result.is_error), _result_text(result)

    async def _transport(self, transport: str) -> None:
        if transport not in _TRANSPORTS:
            self._missing(transport, f"unknown transport {transport}")
            return
        try:
            async with self._session(transport) as client:
                await self._transport_body(transport, client)
        except Exception as exc:
            self._missing(transport, f"{type(exc).__name__}: {exc}")

    async def _transport_body(self, transport: str, client: Client) -> None:
        async def initialize() -> tuple[str, str]:
            version = ""
            with contextlib.suppress(Exception):
                version = str(client.protocol_version)
            return "pass", version or "initialized"

        async def list_tools() -> tuple[str, str]:
            listing = await self._collect_tools(client)
            names = [tool.name for tool in listing if isinstance(tool.name, str)]
            if not self.manifest_tools:
                return "fail", "manifest unavailable"
            if sorted(names) != sorted(self.manifest_tools):
                return "fail", "tool names do not match the manifest"
            return "pass", f"{len(names)} tools"

        async def call_tool() -> tuple[str, str]:
            listing = await self._collect_tools(client)
            names = {tool.name for tool in listing}
            if _READ_TOOL not in names:
                return "skip", "no read-only tool"
            is_error, text = await self._probe(client, _READ_TOOL)
            if is_error:
                return "fail", text or "read-only tool returned an error"
            return "pass", _READ_TOOL

        async def unknown_tool() -> tuple[str, str]:
            is_error, text = await self._probe(client, _UNKNOWN_TOOL)
            if not is_error:
                return "fail", "unknown tool returned a result"
            if not text:
                return "fail", "unknown tool produced an empty error"
            return "pass", "error result"

        async def gated_tool() -> tuple[str, str]:
            name = self._gated_name()
            if name is None:
                return "skip", "no approval-gated tool"
            if not self._closed_probe_is_safe(name):
                return "fail", f"refusing to call {name}"
            is_error, text = await self._probe(client, name)
            if _mentions_approval(is_error, text):
                return "pass", name
            return "fail", "expected an approval error"

        steps: tuple[tuple[str, str, Callable[[], Awaitable[tuple[str, str]]]], ...] = (
            ("initialize", "session initializes", initialize),
            ("list_tools", "tool list matches the manifest", list_tools),
            ("call_tool", "read-only tool call", call_tool),
            ("unknown_tool", "unknown tool is an error", unknown_tool),
            ("gated_tool_refused", "gated tool requires approval", gated_tool),
        )
        for suffix, subtitle, factory in steps:
            await self._step(
                f"{transport}:{suffix}",
                _transport_title(transport, subtitle),
                factory,
            )

    def _closed_probe_is_safe(self, name: str) -> bool:
        """True when calling `name` with no arguments cannot run its handler.

        A gated tool is refused before the handler. A name outside the roster
        never resolves to a handler. Anything else needs a schema we do not
        have yet, so the transport check refuses it.
        """
        if name in self.approval_required:
            return True
        return bool(self.manifest_tools) and name not in self.manifest_tools

    async def _scope_enforced(self) -> tuple[str, str]:
        if not self.token:
            return "fail", "no call token"
        status, _www, _body = await self._exchange(
            "GET", "/admin/approvals", token=self.token
        )
        if status != 403:
            return "fail", f"expected 403, got {status}"
        return "pass", "call token forbidden"

    async def _grant(self, name: str) -> tuple[int, str]:
        status, _www, text = await self._exchange(
            "POST",
            "/admin/approvals",
            token=self.admin_token,
            payload={"tool": name, "ttl_seconds": _APPROVAL_TTL_SECONDS},
        )
        return status, text

    async def _revoke(self, name: str) -> int:
        status, _www, _body = await self._exchange(
            "DELETE",
            f"/admin/approvals/{quote(name, safe='')}",
            token=self.admin_token,
        )
        return status

    async def _approval_roundtrip(self) -> tuple[str, str]:
        name = self._gated_name()
        if name is None:
            return "skip", "no approval-gated tool"
        if not self.token:
            return "fail", "no call token"
        last = "no transport"
        for transport in self.transports:
            if transport not in _TRANSPORTS:
                continue
            try:
                return await self._roundtrip_on(transport, name)
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
        return "fail", last

    async def _roundtrip_on(self, transport: str, name: str) -> tuple[str, str]:
        async with self._session(transport) as client:
            listing = await self._collect_tools(client)
            schema: object = None
            for tool in listing:
                if tool.name == name:
                    schema = tool.input_schema
                    break
            required = _required_fields(schema)
            if not self._closed_probe_is_safe(name) and not required:
                return "fail", f"refusing to call {name}"
            closed, closed_text = await self._probe(client, name)
            if not _mentions_approval(closed, closed_text):
                return "fail", "gate was not closed: " + closed_text
            if not required:
                return (
                    "fail",
                    "refusing to open the gate: no required arguments to omit",
                )
            granted = False
            try:
                status, text = await self._grant(name)
                if status == 201:
                    granted = True
                if self.token and self.token in text:
                    return "fail", "approval response contains the bearer token"
                if self.admin_token and self.admin_token in text:
                    return "fail", "approval response contains the bearer token"
                if status != 201:
                    return "fail", f"approve returned HTTP {status}"
                try:
                    body = json.loads(text)
                except ValueError:
                    body = None
                expires = body.get("expires_at") if isinstance(body, dict) else None
                if isinstance(expires, bool) or not isinstance(expires, (int, float)):
                    return "fail", "approve response missing expires_at"
                opened, opened_text = await self._probe(client, name)
                if not _rejected_before_run(opened, opened_text):
                    return (
                        "fail",
                        "gate probe was not rejected before the tool ran: "
                        + opened_text,
                    )
                revoked = await self._revoke(name)
                granted = False
                if revoked != 200:
                    return "fail", f"revoke returned HTTP {revoked}"
                again, again_text = await self._probe(client, name)
                if not _mentions_approval(again, again_text):
                    return "fail", "gate stayed open after revoke"
            finally:
                if granted:
                    with contextlib.suppress(Exception):
                        await self._revoke(name)
        return "pass", f"{name} opened and closed"

    async def run(self) -> None:
        await self._step("health", "Health endpoint", self._health)
        await self._step("ready", "Readiness endpoint", self._ready)
        await self._step("auth_required", "Authentication enabled", self._auth_required)
        await self._step(
            "auth_missing", "Missing credential is rejected", self._auth_missing
        )
        await self._step(
            "auth_query_token",
            "Query-string token is rejected",
            self._auth_query_token,
        )
        await self._step(
            "auth_invalid", "Invalid credential is rejected", self._auth_invalid
        )
        await self._step(
            "origin_rejected", "Foreign Origin is rejected", self._origin_rejected
        )
        await self._step("request_id", "Request id and traceparent", self._request_id)
        await self._step("manifest", "Manifest matches the roster", self._manifest)
        await self._step(
            "metrics", "Metrics require auth and are exposed", self._metrics
        )
        for transport in self.transports:
            await self._transport(transport)
        if self.admin_token:
            await self._step(
                "admin:scope_enforced",
                "Call token cannot administer approvals",
                self._scope_enforced,
            )
            await self._step(
                "admin:approval_roundtrip",
                "Approval opens and closes the gate",
                self._approval_roundtrip,
            )


def _secret_values(token: str | None, admin_token: str | None) -> list[str]:
    values: list[str] = []
    for secret in (token, admin_token):
        if secret:
            values.append(secret)
    return values


def _validate_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a positive number")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive number")
    return float(timeout)


async def arun_verification(
    base_url: str,
    *,
    token: str | None = None,
    admin_token: str | None = None,
    transports: Sequence[str] = _TRANSPORTS,
    gated_tool: str | None = None,
    timeout: float = 10.0,
    require_auth: bool = False,
    origin_probe: str = "https://evil.example",
) -> VerifyReport:
    """Run every conformance check. A check never raises.

    Connection failures and timeouts are `fail` checks with a one-line detail.
    `token` and `admin_token` are sent as bearer credentials and never copied
    into the report.
    """
    if isinstance(transports, str):
        raise ValueError("transports must be a sequence of names")
    limit = _validate_timeout(timeout)
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("url is required")
    base = base_url.strip().rstrip("/")
    chosen = tuple(transports)
    async with httpx2.AsyncClient(trust_env=False, timeout=limit) as http:
        runner = _Runner(
            base,
            http=http,
            token=token,
            admin_token=admin_token,
            transports=chosen,
            gated_tool=gated_tool,
            timeout=limit,
            require_auth=require_auth,
            origin_probe=origin_probe,
        )
        await runner.run()
    return VerifyReport(
        base_url=base,
        checks=runner.checks,
        server_version=runner.server_version,
        reachable=runner.reachable,
    )


def run_verification(
    base_url: str,
    *,
    token: str | None = None,
    admin_token: str | None = None,
    transports: Sequence[str] = _TRANSPORTS,
    gated_tool: str | None = None,
    timeout: float = 10.0,
    require_auth: bool = False,
    origin_probe: str = "https://evil.example",
) -> VerifyReport:
    """Synchronous wrapper around `arun_verification`."""
    return asyncio.run(
        arun_verification(
            base_url,
            token=token,
            admin_token=admin_token,
            transports=transports,
            gated_tool=gated_tool,
            timeout=timeout,
            require_auth=require_auth,
            origin_probe=origin_probe,
        )
    )


def _load_secret(env_name: str | None, path: Path | None) -> str | None:
    if env_name is not None:
        value = os.environ.get(env_name, "").strip()
        if not value:
            raise ValueError(f"environment variable {env_name} is empty or unset")
        return value
    if path is not None:
        return read_secret_file(path)
    return None


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raw = f"{self.prog}: error: {message}"
        print(_safe_error_text(raw, []), file=sys.stderr)
        raise _UsageError(message)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Human table on stderr; JSON on stdout with `--json`."""
    parser = _Parser(prog="omega-prime-grokbot-verify")
    parser.add_argument("--url", required=True, help="Base URL of the running host")
    token_group = parser.add_mutually_exclusive_group()
    token_group.add_argument(
        "--token-env", metavar="NAME", help="Env var holding the call token"
    )
    token_group.add_argument(
        "--token-file",
        type=Path,
        metavar="PATH",
        help="0600 file holding the call token",
    )
    admin_group = parser.add_mutually_exclusive_group()
    admin_group.add_argument(
        "--admin-token-env", metavar="NAME", help="Env var holding the admin token"
    )
    admin_group.add_argument(
        "--admin-token-file",
        type=Path,
        metavar="PATH",
        help="0600 file holding the admin token",
    )
    parser.add_argument(
        "--transport",
        choices=["sse", "http", "both"],
        default="both",
        help="MCP transports to exercise (default: both)",
    )
    parser.add_argument(
        "--gated-tool", metavar="NAME", help="Approval-gated tool to probe"
    )
    parser.add_argument(
        "--require-auth",
        action="store_true",
        help="Fail when the host reports authentication disabled",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        metavar="S",
        help="Per-check timeout in seconds",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the report as JSON on stdout",
    )
    try:
        args = parser.parse_args(argv)
    except _UsageError:
        return 2
    if (
        isinstance(args.timeout, bool)
        or not math.isfinite(args.timeout)
        or args.timeout <= 0
    ):
        print(
            "omega-prime-grokbot-verify: error: --timeout must be positive",
            file=sys.stderr,
        )
        return 2
    try:
        token = _load_secret(args.token_env, args.token_file)
        admin_token = _load_secret(args.admin_token_env, args.admin_token_file)
    except ValueError as exc:
        print(
            f"omega-prime-grokbot-verify: {_safe_error_text(str(exc), [])}",
            file=sys.stderr,
        )
        return 2
    transports: tuple[str, ...] = (
        _TRANSPORTS if args.transport == "both" else (args.transport,)
    )
    try:
        report = run_verification(
            args.url,
            token=token,
            admin_token=admin_token,
            transports=transports,
            gated_tool=args.gated_tool,
            timeout=args.timeout,
            require_auth=args.require_auth,
        )
    except ValueError as exc:
        print(
            "omega-prime-grokbot-verify: "
            + _safe_error_text(
                str(exc), [secret for secret in (token, admin_token) if secret]
            ),
            file=sys.stderr,
        )
        return 2
    safe = sanitize_report_for_publication(
        report, [secret for secret in (token, admin_token) if secret]
    )
    if args.json:
        print(json.dumps(safe.to_dict(), sort_keys=True), flush=True)
    else:
        print(format_report(safe), file=sys.stderr, flush=True)
    if not safe.reachable:
        return 2
    return 0 if safe.ok else 1


if __name__ == "__main__":
    sys.exit(main())
