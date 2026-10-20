"""Real HTTP transport for providers: stdlib only, retries, host allowlist.

`HttpTransport` implements the `Transport` seams over `http.client`:
JSON request bodies, JSON responses (`post_text` for plain-text bodies),
line-iterated `stream` responses, per-call timeouts, and retries on
connection failures, timeouts, HTTP 408, HTTP 429 (honoring `Retry-After`
up to 60s), and HTTP 5xx. With a seat policy attached, hosts outside the
allowlist are refused before any socket exists.

The `connect` factory (default: stdlib HTTP/HTTPS connections) exists so
tests can run the same request/response framing over a socketpair to a
scripted peer that speaks real HTTP bytes.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import time
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlencode, urlsplit

from omega_prime.providers.base import ProviderError


class _McpResponse:
    """Context-managed HTTP response whose body remains incrementally readable."""

    def __init__(
        self, connection: Any, response: Any, status: int, headers: dict[str, str]
    ):
        self.status = status
        self.headers = headers
        self._connection = connection
        self._response = response
        self._closed = False

    def read(self, size: int = 4096) -> bytes:
        read1 = getattr(self._response, "read1", None)
        if callable(read1):
            chunk = read1(size)
            if isinstance(chunk, bytes):
                return chunk
        return self._response.read(size)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(OSError):
                self._connection.close()

    def __enter__(self) -> _McpResponse:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


class HttpTransport:
    """POST JSON over HTTP. `max_retries` counts retries after the first try."""

    def __init__(
        self,
        policy: Any = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        backoff: float = 0.1,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive number of seconds")
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or max_retries < 0
        ):
            raise ValueError("max_retries must be a non-negative integer")
        self._policy = policy
        self._timeout = float(timeout)
        self._max_retries = max_retries
        self._backoff = backoff
        self._connect = connect or _stdlib_connect

    def post(self, url: str, headers: dict, body: dict) -> dict:
        """POST one JSON body. Return the decoded JSON response."""
        payload = json.dumps(body).encode("utf-8")
        merged = {
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        }
        merged.update(headers or {})
        return self._send("POST", url, merged, payload)

    def post_text(self, url: str, headers: dict, body: dict) -> str:
        """POST one JSON body. Return the raw UTF-8 response body."""
        payload = json.dumps(body).encode("utf-8")
        merged = {
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        }
        merged.update(headers or {})
        return self._send_text("POST", url, merged, payload)

    def get(self, url: str, headers: dict, params: dict) -> dict:
        """GET one URL with a query string. Return the decoded JSON response."""
        query = urlencode(sorted((params or {}).items()), doseq=True)
        target = url + ("&" if "?" in url else "?") + query if query else url
        merged = {"Accept": "application/json"}
        merged.update(headers or {})
        return self._send("GET", target, merged, None)

    def stream(self, url: str, headers: dict, body: dict) -> Iterator[str]:
        """POST once and yield response lines. Retries end at first byte."""
        payload = json.dumps(body).encode("utf-8")
        merged = {
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
            "Accept": "text/event-stream",
        }
        merged.update(headers or {})
        return self._stream_lines(url, merged, payload)

    def post_mcp(self, url: str, headers: dict, envelope: dict) -> _McpResponse:
        """POST one MCP message without replay, retaining an incremental body."""
        payload = json.dumps(envelope).encode("utf-8")
        merged = {
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
            "Accept": "application/json, text/event-stream",
        }
        merged.update(headers or {})
        # The MCP response negotiation is part of this dedicated transport
        # contract and cannot be overridden by caller-supplied headers.
        merged["Accept"] = "application/json, text/event-stream"
        host, port, path, use_tls = _split(url)
        if host is None:
            raise ProviderError("cannot parse request host")
        if self._policy is not None and not self._policy.allows_host(host):
            raise ProviderError(f"network blocked to host {host}")
        try:
            connection = self._connect(host, port, self._timeout, use_tls)
        except OSError as exc:
            raise ProviderError("MCP transport is unreachable") from exc
        try:
            connection.request("POST", path, body=payload, headers=merged)
            response = connection.getresponse()
            seen: dict[str, str] = {}
            for key, value in response.getheaders():
                seen.setdefault(str(key).lower(), str(value))
            return _McpResponse(connection, response, response.status, seen)
        except (http.client.HTTPException, TimeoutError, OSError) as exc:
            with contextlib.suppress(OSError):
                connection.close()
            raise ProviderError("MCP transport failed") from exc

    def _send(
        self, method: str, url: str, headers: dict, payload: bytes | None
    ) -> dict:
        """One request with retries. Hosts are allowlisted before any socket."""
        host, port, path, use_tls = _split(url)
        if host is None:
            raise ProviderError(f"cannot parse request host from {url!r}")
        if self._policy is not None and not self._policy.allows_host(host):
            raise ProviderError(f"network blocked to host {host}")
        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return self._once(
                    method, host, port, path, use_tls, headers, payload, url
                )
            except _Retryable as exc:
                last = exc
                delay = self._delay(attempt, exc.retry_after)
                if delay is not None:
                    time.sleep(delay)
            except ProviderError:
                raise
        assert last is not None
        raise ProviderError(f"request to {host} failed after retries: {last}") from last

    def _send_text(
        self, method: str, url: str, headers: dict, payload: bytes | None
    ) -> str:
        """One request with retries, returning the raw UTF-8 body."""
        host, port, path, use_tls = _split(url)
        if host is None:
            raise ProviderError(f"cannot parse request host from {url!r}")
        if self._policy is not None and not self._policy.allows_host(host):
            raise ProviderError(f"network blocked to host {host}")
        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return self._once_text(
                    method, host, port, path, use_tls, headers, payload, url
                )
            except _Retryable as exc:
                last = exc
                delay = self._delay(attempt, exc.retry_after)
                if delay is not None:
                    time.sleep(delay)
            except ProviderError:
                raise
        assert last is not None
        raise ProviderError(f"request to {host} failed after retries: {last}") from last

    def _delay(self, attempt: int, retry_after: float | None) -> float | None:
        """Seconds to sleep before the next attempt. None means no sleep."""
        if attempt >= self._max_retries or self._backoff <= 0:
            return None
        delay = self._backoff * (attempt + 1)
        if retry_after is not None:
            delay = max(delay, retry_after)
        return delay

    def _stream_lines(self, url: str, headers: dict, payload: bytes) -> Iterator[str]:
        """Yield response lines. Status errors retry; mid-stream ends it."""
        host, port, path, use_tls = _split(url)
        if host is None:
            raise ProviderError(f"cannot parse request host from {url!r}")
        if self._policy is not None and not self._policy.allows_host(host):
            raise ProviderError(f"network blocked to host {host}")
        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                yield from self._stream_once(
                    host, port, path, use_tls, headers, payload, url
                )
                return
            except _Retryable as exc:
                last = exc
                delay = self._delay(attempt, exc.retry_after)
                if delay is not None:
                    time.sleep(delay)
            except ProviderError:
                raise
        assert last is not None
        raise ProviderError(
            f"stream from {host} failed after retries: {last}"
        ) from last

    def _stream_once(
        self,
        host: str,
        port: int,
        path: str,
        use_tls: bool,
        headers: dict,
        payload: bytes,
        url: str,
    ) -> Iterator[str]:
        """One POST; yield decoded lines. Errors raise before first yield."""
        try:
            connection = self._connect(host, port, self._timeout, use_tls)
        except OSError as exc:
            raise _Retryable(str(exc) or type(exc).__name__) from exc
        try:
            try:
                connection.request("POST", path, body=payload, headers=headers)
                response = connection.getresponse()
                status = response.status
                seen: dict[str, str] = {}
                for key, value in response.getheaders():
                    seen.setdefault(str(key).lower(), str(value))
            except (http.client.HTTPException, TimeoutError, OSError) as exc:
                raise _Retryable(str(exc) or type(exc).__name__) from exc
            _check_status(status, url, seen)
            while True:
                raw = response.readline()
                if not raw:
                    return
                yield raw.decode("utf-8", "replace").rstrip("\r\n")
        finally:
            with contextlib.suppress(OSError):
                connection.close()

    def _once(
        self,
        method: str,
        host: str,
        port: int,
        path: str,
        use_tls: bool,
        headers: dict,
        payload: bytes | None,
        url: str,
    ) -> dict:
        status, headers_out, raw = self._request(
            method, host, port, path, use_tls, headers, payload
        )
        _check_status(status, url, headers_out)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError(f"response is not JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise ProviderError("response is not a JSON object")
        return decoded

    def _once_text(
        self,
        method: str,
        host: str,
        port: int,
        path: str,
        use_tls: bool,
        headers: dict,
        payload: bytes | None,
        url: str,
    ) -> str:
        status, headers_out, raw = self._request(
            method, host, port, path, use_tls, headers, payload
        )
        _check_status(status, url, headers_out)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProviderError(f"response is not text: {exc}") from exc

    def _request(
        self,
        method: str,
        host: str,
        port: int,
        path: str,
        use_tls: bool,
        headers: dict,
        payload: bytes | None,
    ) -> tuple[int, dict[str, str], bytes]:
        """One socket round trip. Return status, headers, and raw body bytes."""
        try:
            connection = self._connect(host, port, self._timeout, use_tls)
        except OSError as exc:
            raise _Retryable(str(exc) or type(exc).__name__) from exc
        try:
            try:
                connection.request(method, path, body=payload, headers=headers)
                response = connection.getresponse()
                status = response.status
                raw = response.read()
                seen: dict[str, str] = {}
                for key, value in response.getheaders():
                    seen.setdefault(str(key).lower(), str(value))
            except (http.client.HTTPException, TimeoutError, OSError) as exc:
                raise _Retryable(str(exc) or type(exc).__name__) from exc
        finally:
            with contextlib.suppress(OSError):
                connection.close()
        return status, seen, raw


def _check_status(status: int, url: str, headers: dict[str, str]) -> None:
    """Raise for error statuses. 408, 429, and 5xx retry; 4xx is final."""
    if status == 408 or status == 429:
        raise _Retryable(f"HTTP {status}", retry_after=_retry_after(headers))
    if 500 <= status <= 599:
        raise _Retryable(f"HTTP {status}")
    if 400 <= status <= 499:
        raise ProviderError(f"HTTP {status} from {url}")


def _retry_after(headers: dict[str, str]) -> float | None:
    """Seconds from a `Retry-After` header, capped at 60. None when absent."""
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, 60.0)


class _Retryable(Exception):
    """A failure worth one more attempt."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _stdlib_connect(
    host: str, port: int, timeout: float, use_tls: bool
) -> http.client.HTTPConnection:
    """Open a real stdlib connection."""
    if use_tls:
        return http.client.HTTPSConnection(host, port, timeout=timeout)
    return http.client.HTTPConnection(host, port, timeout=timeout)


def _split(url: str) -> tuple[str | None, int, str, bool]:
    if not isinstance(url, str) or url == "":
        return None, 0, "/", False
    try:
        parts = urlsplit(url)
    except ValueError:
        return None, 0, "/", False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None, 0, "/", False
    host = parts.hostname.lower()
    use_tls = parts.scheme == "https"
    port = parts.port or (443 if use_tls else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return host, port, path, use_tls


__all__ = ["HttpTransport"]
