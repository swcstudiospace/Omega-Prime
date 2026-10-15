# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Optional gateways already in the bot: substrate, red team, desk bus.

Skipped is a steady state. A missing binding file leaves the old defaults
alone so an install that never ran setup keeps its current behavior.
"""

from __future__ import annotations

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


def probe_url(url: str, *, timeout: float = 3.0) -> str:
    """Return ok, refused, tls, auth, or timeout. Does not return the body."""
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
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
    return "ok"


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
