# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Optional gateways already in the bot: substrate, red team, desk bus.

Skipped is a steady state. A missing binding file leaves the old defaults
alone so an install that never ran setup keeps its current behavior.
"""

from __future__ import annotations

import contextlib
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from omega_prime.integrations.binding import (
    GATEWAY_NAMES,
    binding_path,
    load_binding,
    secret_value,
)

Probe = str


def validate_gateway_url(text: str) -> str | None:
    """Return an error string, or ``None`` when the URL is http(s) without userinfo."""
    if not isinstance(text, str) or not text.strip():
        return "The gateway URL is empty."
    try:
        parts = urlsplit(text.strip())
    except ValueError:
        return "The gateway URL is not a URL."
    if parts.scheme not in ("http", "https"):
        return "The gateway URL must be http or https."
    if not parts.hostname:
        return "The gateway URL has no host."
    if parts.username or parts.password:
        return "The gateway URL must not include a username or password."
    return None


class _CrossOriginRedirect(Exception):
    """A health probe was redirected off the origin that was checked."""


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only when the next request stays on the same origin.

    ``urllib`` copies ``Authorization`` onto a redirected request. A gateway
    that redirects to another host would otherwise receive the bearer.
    """

    def redirect_request(  # type: ignore[override]
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        current = req.full_url if isinstance(req.full_url, str) else ""
        if not _same_origin(current, newurl):
            _drain(fp)
            raise _CrossOriginRedirect
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        return redirected if isinstance(redirected, urllib.request.Request) else None


def _probe_opener() -> urllib.request.OpenerDirector:
    """Health opener: no ambient proxy, and no cross-origin redirect."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _SameOriginRedirect,
    )


def _open_probe(request: urllib.request.Request, timeout: float) -> Any:
    """Open one health request directly, never through an ambient proxy."""
    return _probe_opener().open(request, timeout=timeout)


def probe_url(
    url: str,
    *,
    timeout: float = 3.0,
    headers: Mapping[str, str] | None = None,
) -> str:
    """Return ok, degraded, refused, tls, auth, or timeout. Does not return the body.

    Only a 2xx response is ok. 401 and 403 are auth. 5xx is degraded.
    Any other HTTP status, including a redirect to another origin, is refused.
    Same-origin redirects are followed. The bearer is not sent to the other host.
    """
    request = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
    try:
        with _open_probe(request, timeout) as response:
            status = int(response.status)
    except _CrossOriginRedirect:
        return "refused"
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
    except urllib.error.URLError as exc:
        return _url_kind(exc)
    except TimeoutError:
        return "timeout"
    except ssl.SSLError:
        return "tls"
    except OSError as exc:
        return _os_kind(exc)
    if status in (401, 403):
        return "auth"
    if 200 <= status < 300:
        return "ok"
    if status >= 500:
        return "degraded"
    if 300 <= status < 400:
        return "refused"
    return "refused"


def gateway_probe_headers(name: str, env: Mapping[str, str]) -> dict[str, str]:
    """Authorization header the live client would send, or an empty mapping."""
    token = ""
    if name == "substrate":
        # Same resolver as the live client: plain env, NAME_FILE, or the
        # mode 0600 file under $OMEGA_PRIME_STATE_DIR/secrets/.
        token = secret_value(env, "SUBSTRATE_TOKEN") or secret_value(
            env, "SUBSTRATE_TOKEN_GROK_BOT"
        )
    elif name == "redteam":
        raw = env.get("T3MP3ST_BEARER_TOKEN") or ""
        token = raw.strip() if isinstance(raw, str) else ""
        if not token:
            token = _bearer_file(env)
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


# Health routes the live clients already use. The saved value is the base
# URL (substrate brief/events, T3MP3ST, agent bus), not the health path.
_HEALTH_PATH = {
    "substrate": "/healthz",
    "redteam": "/health",
    "desk_bus": "/health",
}


def health_url(name: str, url: str) -> str:
    """Base URL plus that gateway's health path. A URL that already ends there is kept."""
    base = url.strip().rstrip("/")
    path = _HEALTH_PATH.get(name, "")
    if not path or base.endswith(path):
        return base
    return base + path


def probe_gateway(name: str, url: str, env: Mapping[str, str] | None = None) -> str:
    """Probe one gateway's health route with the same bearer the runtime client sends."""
    source = env or {}
    return probe_url(health_url(name, url), headers=gateway_probe_headers(name, source))


def ready_gateway_url(name: str, env: Mapping[str, str] | None = None) -> str:
    """Saved URL when this gateway is ready, else ``""``.

    A missing binding file leaves the caller's existing default alone.
    """
    if name not in GATEWAY_NAMES:
        return ""
    try:
        path = binding_path(env)
    except ValueError:
        return ""
    if not path.is_file():
        return ""
    node = load_binding(env)["gateways"].get(name) or {}
    url = node.get("url")
    if node.get("state") == "ready" and isinstance(url, str) and url:
        return url
    return ""


def active_gateway(
    name: str, fallback: str, env: Mapping[str, str] | None = None
) -> tuple[str, dict[str, Any] | None]:
    """``(url, None)`` or ``("", block)``.

    A ready saved URL wins over ``fallback``. No binding file, or a gateway
    that was never configured, keeps ``fallback``. Skipped, invalid, and
    degraded block instead of calling the old default.
    """
    blocked = gateway_block(name, env)
    if blocked is not None:
        return "", blocked
    saved = ready_gateway_url(name, env)
    if saved:
        return saved, None
    return fallback, None


def gateway_block(
    name: str, env: Mapping[str, str] | None = None
) -> dict[str, Any] | None:
    """Refusal row when a saved binding marks this gateway skipped, invalid, or degraded.

    ``None`` means the caller should use its existing default.
    """
    if name not in GATEWAY_NAMES:
        return None
    try:
        path = binding_path(env)
    except ValueError:
        return None
    if not path.is_file():
        return None
    node = load_binding(env)["gateways"].get(name) or {}
    state = node.get("state")
    if state == "skipped":
        return _row(
            "not_configured",
            f"{name} gateway is not configured. Run /omega-connectors to set it.",
            name,
        )
    if state == "invalid":
        return _row(
            "invalid",
            f"{name} gateway rejected the connection. Run /omega-connectors to set it again.",
            name,
        )
    if state == "degraded":
        return _row(
            "degraded",
            f"{name} gateway timed out. The last URL is kept for a later retry.",
            name,
        )
    return None


def gateway_status(name: str, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Status for a gateway-dependent action, including an unset binding."""
    blocked = gateway_block(name, env)
    if blocked is not None:
        return blocked
    if name not in GATEWAY_NAMES:
        return _row("not_configured", "unknown gateway", name)
    try:
        path = binding_path(env)
    except ValueError:
        path = None
    if path is None or not path.is_file():
        return _row(
            "not_configured",
            f"{name} gateway is not configured. Run /omega-connectors to set or skip it.",
            name,
        )
    node = load_binding(env)["gateways"][name]
    if node["state"] == "ready" and node["url"]:
        return {"ok": True, "gateway": name, "url": node["url"]}
    return _row(
        "not_configured",
        f"{name} gateway is not configured. Run /omega-connectors to set or skip it.",
        name,
    )


def _row(code: str, reason: str, name: str) -> dict[str, Any]:
    return {"ok": False, "error": f"{code}: {reason}", "gateway": name}


def _origin(url: str) -> tuple[str, str, int] | None:
    """``(scheme, host, port)`` for an http(s) URL, else ``None``."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname.lower() if isinstance(parts.hostname, str) else ""
    if scheme not in ("http", "https") or not host:
        return None
    port = parts.port if parts.port is not None else 443 if scheme == "https" else 80
    return scheme, host, port


def _same_origin(left: str, right: str) -> bool:
    origin = _origin(left)
    return origin is not None and origin == _origin(right)


def _drain(fp: Any) -> None:
    with contextlib.suppress(Exception):
        fp.read()
    with contextlib.suppress(Exception):
        fp.close()


def _url_kind(exc: urllib.error.URLError) -> str:
    text = str(exc.reason).lower()
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "certificate" in text or "ssl" in text or "tls" in text:
        return "tls"
    if "refused" in text:
        return "refused"
    return "refused"


def _os_kind(exc: OSError) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    text = str(exc).lower()
    if "refused" in text:
        return "refused"
    return "refused"


def _bearer_file(env: Mapping[str, str]) -> str:
    raw = env.get("T3MP3ST_BEARER_TOKEN_FILE") or ""
    path = raw.strip() if isinstance(raw, str) else ""
    if not path:
        return ""
    from omega_prime.tooling.fs import read_secret_file

    try:
        return read_secret_file(path).strip()
    except ValueError:
        return ""
