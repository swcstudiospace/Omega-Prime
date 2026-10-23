"""The urllib transport seam (no sockets: the opener is replaced)."""

from __future__ import annotations

import asyncio
import io
import threading
import urllib.error
from email.message import Message
from typing import Any, ClassVar

import pytest

from omega_prime.ci_runners import http
from omega_prime.ci_runners.http import (
    HttpRequest,
    HttpResponse,
    TransportError,
    send,
    urllib_transport,
)


class _Resp(io.BytesIO):
    status = 201
    headers: ClassVar[dict[str, str]] = {"X-Thing": "1"}

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def test_request_repr_hides_credentials() -> None:
    req = HttpRequest(
        "POST", "https://x.test/a", {"Authorization": "Bearer s3"}, b"jit"
    )
    assert "s3" not in repr(req) and "jit" not in repr(req)
    assert HttpResponse(200, {"a": "b"}).header("A") == "b"


def test_urllib_success_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float] = []

    def ok(req, timeout):  # type: ignore[no-untyped-def]
        seen.append(timeout)
        return _Resp(b'{"ok":1}')

    monkeypatch.setattr(http._OPENER, "open", ok)
    out = urllib_transport(HttpRequest("GET", "https://x.test/", timeout=3))
    assert out.status == 201 and out.header("x-thing") == "1" and seen == [3]

    def http_error(req, timeout):  # type: ignore[no-untyped-def]
        hdrs = Message()
        hdrs["Retry-After"] = "5"
        raise urllib.error.HTTPError(req.full_url, 403, "no", hdrs, io.BytesIO(b"x"))

    monkeypatch.setattr(http._OPENER, "open", http_error)
    out = urllib_transport(HttpRequest("GET", "https://x.test/"))
    assert out.status == 403 and out.header("retry-after") == "5" and out.body == b"x"

    def refused(req, timeout):  # type: ignore[no-untyped-def]
        raise urllib.error.URLError("refused https://secret@host")

    monkeypatch.setattr(http._OPENER, "open", refused)
    with pytest.raises(TransportError) as info:
        urllib_transport(HttpRequest("GET", "https://x.test/"))
    assert str(info.value) == "URLError"
    with pytest.raises(TransportError, match="timeout"):
        urllib_transport(HttpRequest("GET", "https://x.test/", timeout=0))


def test_redirects_are_returned_not_followed() -> None:
    """A 302 (which could bounce the bearer token elsewhere) is surfaced as-is."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    hits: list[str] = []

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/start"
        response = urllib_transport(HttpRequest("GET", url, timeout=5))
    finally:
        server.shutdown()
        server.server_close()
    assert response.status == 302 and response.header("location") == "/elsewhere"
    assert hits == ["/start"]


def test_send_runs_off_the_event_loop_thread() -> None:
    threads: list[int] = []

    def transport(_: HttpRequest) -> HttpResponse:
        threads.append(threading.get_ident())
        return HttpResponse(204)

    async def scenario() -> int:
        await send(transport, HttpRequest("GET", "https://x.test/"))
        return threading.get_ident()

    loop_thread = asyncio.run(scenario())
    assert threads and threads[0] != loop_thread
