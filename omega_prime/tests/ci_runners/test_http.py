"""The urllib transport seam (no sockets: the opener is replaced)."""

from __future__ import annotations

import asyncio
import http.client as httplib
import io
import threading
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from typing import ClassVar

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


class _Scripted302(urllib.request.BaseHandler):
    """Answers every https request with a 302 in-process (no socket)."""

    handler_order = 100  # ahead of the stock HTTPSHandler

    def __init__(self) -> None:
        self.hits: list[str] = []

    def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
        self.hits.append(req.selector)
        hdrs = Message()
        hdrs["Location"] = "https://elsewhere.test/steal"
        hdrs["Content-Length"] = "0"
        resp = urllib.response.addinfourl(io.BytesIO(b""), hdrs, req.full_url, 302)
        # urllib's processors read ``.msg`` (the reason phrase) off responses.
        setattr(resp, "msg", "Found")  # noqa: B010 - not in addinfourl's stubs
        return resp


def test_redirects_are_returned_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 302 (which could bounce the bearer token elsewhere) is surfaced as-is.

    The scripted handler sits inside the production opener, so urllib's real
    redirect processing runs; following it would record a second hit.
    """
    scripted = _Scripted302()
    monkeypatch.setattr(http, "_OPENER", http.build_opener(scripted))
    response = urllib_transport(
        HttpRequest("GET", "https://x.test/start", {"Authorization": "Bearer t"})
    )
    assert response.status == 302
    assert response.header("location") == "https://elsewhere.test/steal"
    assert scripted.hits == ["/start"]


@pytest.mark.parametrize(
    "exc",
    [
        httplib.IncompleteRead(b"partial", 10),
        httplib.BadStatusLine("garbage"),
        httplib.RemoteDisconnected("closed"),
    ],
)
def test_broken_responses_become_transport_errors(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    class _Truncated(_Resp):
        def read(self, *_: object) -> bytes:
            raise exc

    monkeypatch.setattr(http._OPENER, "open", lambda req, timeout: _Truncated(b""))
    with pytest.raises(TransportError) as info:
        urllib_transport(HttpRequest("GET", "https://x.test/"))
    assert str(info.value) == type(exc).__name__ and info.value.__cause__ is None


def test_truncated_error_body_becomes_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Body(io.BytesIO):
        def read(self, *_: object) -> bytes:
            raise httplib.IncompleteRead(b"", 5)

    def http_error(req, timeout):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, 500, "x", Message(), _Body())

    monkeypatch.setattr(http._OPENER, "open", http_error)
    with pytest.raises(TransportError, match="IncompleteRead"):
        urllib_transport(HttpRequest("GET", "https://x.test/"))


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
