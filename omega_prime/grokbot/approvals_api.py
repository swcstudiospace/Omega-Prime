# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Admin HTTP API for approving approval-gated tools at runtime.

Routes (all require an authenticated principal holding the ``admin`` scope):

* ``GET /admin/approvals``: live approvals and the gated tool names.
* ``POST /admin/approvals``: ``{"tool": str, "ttl_seconds": number?}`` approves a
  gated tool for a limited time.
* ``DELETE /admin/approvals/{tool}``: revokes an approval.

Security model: the handlers trust the principal that ``AuthMiddleware`` placed
on the request, so mount this API only when authentication is enabled. The bot's
own token must carry the ``call`` scope, never ``admin``: a call-scope principal
gets 403 here, and the approval log itself refuses an approver equal to the bot.
TTLs above ``max_ttl_seconds`` are rejected (422), never clamped. Request bodies
are capped at 4096 bytes. Responses never echo request headers or tokens, and an
audit failure never changes the HTTP result.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable, Sequence
from typing import Any

from anyio.to_thread import run_sync as run_in_worker_thread
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from omega_prime.grokbot.security import SCOPE_ADMIN, Principal, principal_from_request

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 4096
_ALLOWED_KEYS = frozenset({"tool", "ttl_seconds"})
_REALM_HEADER = 'Bearer realm="omega-prime"'
_SEPARATE_APPROVAL_NAME = "pin_bump_apply"


def _reject_constant(name: str) -> Any:
    raise ValueError(f"unsupported JSON constant {name}")


def _error(status: int, message: str, **headers: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status, headers=headers)


def _guard(request: Request) -> tuple[Principal | None, Response | None]:
    principal = principal_from_request(request)
    if principal is None:
        return None, _error(
            401, "authentication required", **{"WWW-Authenticate": _REALM_HEADER}
        )
    if not principal.allows(SCOPE_ADMIN):
        return None, _error(403, "admin scope required")
    return principal, None


class _BodyTooLargeError(Exception):
    pass


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY_BYTES:
                raise _BodyTooLargeError
        except ValueError:
            pass
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise _BodyTooLargeError
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_body(raw: bytes, content_type: str) -> tuple[dict[str, Any] | None, str]:
    """Return ``(body, "")`` or ``(None, reason)``."""
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        return None, "content-type must be application/json"
    try:
        body = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, "body must be valid JSON"
    if not isinstance(body, dict):
        return None, "body must be a JSON object"
    unknown = sorted(str(key) for key in body if key not in _ALLOWED_KEYS)
    if unknown:
        return None, "unknown keys: " + ", ".join(unknown)
    return body, ""


def approval_routes(
    *,
    approval_log: Any,
    gated_tools: Callable[[], Sequence[str]],
    audit: Any = None,
    default_ttl_seconds: float = 3600.0,
    max_ttl_seconds: float = 86400.0,
    separate_approval_name: str | None = None,
) -> list[Route]:
    """Build the admin approval routes over ``approval_log``.

    ``gated_tools`` returns names currently requiring approval; it is called
    per request and is the only list exposed as gated-tool metadata.
    ``separate_approval_name`` permits one non-tool action to be managed here
    without publishing it as a served or gated MCP tool.
    """
    if separate_approval_name not in (None, _SEPARATE_APPROVAL_NAME):
        raise ValueError("unsupported separate approval name")

    def is_approvable(tool: str) -> bool:
        return tool in gated_tools() or tool == separate_approval_name

    async def _audit(
        event: str, tool: str, principal: Principal, ttl: float | None, approver: str
    ) -> None:
        if audit is None:
            return

        def write() -> None:
            audit.log_event(
                event,
                tool_name=tool,
                caller=principal.id,
                status="ok",
                details={"ttl_seconds": ttl, "approver": approver},
            )

        try:
            await run_in_worker_thread(write)
        except Exception:
            logger.warning("approvals: audit write failed for %s", event, exc_info=True)

    async def list_approvals(request: Request) -> Response:
        _, denied = _guard(request)
        if denied is not None:
            return denied
        return JSONResponse(
            {
                "approvals": approval_log.entries(),
                "gated_tools": sorted(gated_tools()),
            }
        )

    async def grant(request: Request) -> Response:
        principal, denied = _guard(request)
        if denied is not None or principal is None:
            return denied or _error(401, "authentication required")
        try:
            raw = await _read_body(request)
        except _BodyTooLargeError:
            return _error(413, f"body exceeds {MAX_BODY_BYTES} bytes")
        body, reason = _parse_body(raw, request.headers.get("content-type", ""))
        if body is None:
            return _error(400, reason)
        tool = body.get("tool")
        if not isinstance(tool, str) or tool == "":
            return _error(400, "tool must be a non-empty string")
        ttl_raw = body.get("ttl_seconds", default_ttl_seconds)
        if (
            isinstance(ttl_raw, bool)
            or not isinstance(ttl_raw, (int, float))
            or not math.isfinite(ttl_raw)
        ):
            return _error(400, "ttl_seconds must be a finite number")
        if not is_approvable(tool):
            return _error(404, "unknown or non-gated tool")
        if ttl_raw <= 0:
            return _error(422, "ttl_seconds must be greater than 0")
        if ttl_raw > max_ttl_seconds:
            return _error(422, f"ttl_seconds must not exceed {max_ttl_seconds:g}")
        approver = principal.label or principal.id
        outcome = approval_log.approve(tool, approver, ttl_seconds=float(ttl_raw))
        if not outcome.get("approved"):
            return _error(403, str(outcome.get("error", "approval refused")))
        await _audit("approval_granted", tool, principal, float(ttl_raw), approver)
        return JSONResponse(
            {
                "approved": True,
                "tool": tool,
                "approved_by": approver,
                "expires_at": outcome.get("expires_at"),
            },
            status_code=201,
        )

    async def revoke(request: Request) -> Response:
        principal, denied = _guard(request)
        if denied is not None or principal is None:
            return denied or _error(401, "authentication required")
        tool = request.path_params["tool"]
        if not is_approvable(tool) or not approval_log.revoke(tool):
            return _error(404, "no active approval for tool")
        await _audit(
            "approval_revoked", tool, principal, None, principal.label or principal.id
        )
        return JSONResponse({"revoked": True})

    return [
        Route("/admin/approvals", list_approvals, methods=["GET"]),
        Route("/admin/approvals", grant, methods=["POST"]),
        Route("/admin/approvals/{tool:path}", revoke, methods=["DELETE"]),
    ]


__all__ = ["MAX_BODY_BYTES", "approval_routes"]
