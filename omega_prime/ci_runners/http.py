"""Tiny synchronous HTTP seam, always awaited through ``asyncio.to_thread``.

Requests carry bearer headers, so ``repr`` never shows headers or bodies,
redirects are refused (a redirect would forward the credential), and
transport errors keep only the exception type name.
"""

from __future__ import annotations

import asyncio
import http.client
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.message import Message

MAX_RESPONSE_BYTES = 4 << 20


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    body: bytes | None = field(default=None, repr=False)
    timeout: float = 30.0


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = field(default=b"", repr=False)

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


class TransportError(Exception):
    """No usable HTTP response (connect, timeout, TLS, truncated body). Secret-free."""


Transport = Callable[[HttpRequest], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def build_opener(*extra: urllib.request.BaseHandler) -> urllib.request.OpenerDirector:
    """The transport's opener: redirects refused, plus optional extra handlers."""
    return urllib.request.build_opener(_NoRedirect, *extra)


_OPENER = build_opener()


def _lower(headers: Message | Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def urllib_transport(request: HttpRequest) -> HttpResponse:
    """Blocking stdlib transport with a finite timeout and capped body read."""
    if request.timeout <= 0:
        raise TransportError("timeout")
    req = urllib.request.Request(
        request.url,
        data=request.body,
        method=request.method,
        headers=dict(request.headers),
    )
    try:
        with _OPENER.open(req, timeout=request.timeout) as resp:
            return HttpResponse(
                resp.status, _lower(resp.headers), resp.read(MAX_RESPONSE_BYTES)
            )
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(MAX_RESPONSE_BYTES) if exc.fp is not None else b""
        except (http.client.HTTPException, OSError, ValueError) as read_exc:
            raise TransportError(type(read_exc).__name__) from None
        return HttpResponse(exc.code, _lower(exc.headers), body)
    except (
        urllib.error.URLError,
        http.client.HTTPException,  # IncompleteRead, BadStatusLine, ...
        OSError,
        ValueError,
    ) as exc:
        raise TransportError(type(exc).__name__) from None


async def send(transport: Transport, request: HttpRequest) -> HttpResponse:
    """Run the blocking transport off the event loop."""
    return await asyncio.to_thread(transport, request)


__all__ = [
    "MAX_RESPONSE_BYTES",
    "HttpRequest",
    "HttpResponse",
    "Transport",
    "TransportError",
    "build_opener",
    "send",
    "urllib_transport",
]
