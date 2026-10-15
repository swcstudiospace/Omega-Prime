# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""JSON HTTP for installer clients. Failures are classified and redacted."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any


class TransportError(Exception):
    """A classified transport failure. ``kind`` is refused, tls, timeout, or network."""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(message)


def request_json(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any] | None,
    *,
    timeout: float = 10.0,
    secret: str = "",
) -> tuple[int, dict[str, Any]]:
    """One JSON request. The secret is stripped from any error text."""
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers=dict(headers),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = int(exc.code)
    except urllib.error.URLError as exc:
        raise TransportError(*_classify(exc, secret)) from exc
    except TimeoutError as exc:
        raise TransportError("timeout", _redact("timed out", secret)) from exc
    except ssl.SSLError as exc:
        raise TransportError("tls", _redact("tls handshake failed", secret)) from exc
    except OSError as exc:
        raise TransportError(*_classify(exc, secret)) from exc
    parsed: dict[str, Any] = {}
    if raw:
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            parsed = loaded
    return status, parsed


def _classify(exc: BaseException, secret: str) -> tuple[str, str]:
    text = str(exc.reason) if isinstance(exc, urllib.error.URLError) else str(exc)
    lowered = text.lower()
    if "timed out" in lowered or "timeout" in lowered:
        kind = "timeout"
    elif "certificate" in lowered or "tls" in lowered or "ssl" in lowered:
        kind = "tls"
    elif "refused" in lowered or "unreachable" in lowered:
        kind = "refused"
    else:
        kind = "network"
    return kind, _redact(kind, secret)


def _redact(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "[redacted]")
    return text
