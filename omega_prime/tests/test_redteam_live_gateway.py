"""Live-gateway integration scaffold for the Red Team adapter.

Uses the real stdlib HTTP client (``request=None``) against the configured
``redteam.gateway_url`` (``OMEGA_PRIME_REDTEAM_GATEWAY_URL`` env or config).
Every live test skips when a 2s TCP probe finds no gateway, so CI without a
``T3MP3ST`` backend stays green. Only read-only GETs run here — this module
never starts an engagement.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from omega_prime.grokbot.roe_lifecycle import (
    gateway_reachable,
    resolve_gateway_url,
)
from omega_prime.tools.redteam import (
    RedteamClient,
    RedteamContext,
    resolve_bearer_token,
)

GATEWAY_URL = (
    os.environ.get("OMEGA_PRIME_REDTEAM_GATEWAY_URL", "").strip()
    or resolve_gateway_url()
)

GATEWAY_REACHABLE = gateway_reachable(GATEWAY_URL, timeout=2.0)

requires_live_gateway = pytest.mark.skipif(
    not GATEWAY_REACHABLE,
    reason=f"live T3MP3ST gateway unreachable at {GATEWAY_URL}",
)


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _config_bearer_file() -> str:
    try:
        from omega_prime.config import load_config

        value = load_config().get("redteam", {}).get("bearer_token_file", "")
        return value if isinstance(value, str) else ""
    except Exception:
        return ""


def _live_client() -> RedteamClient:
    return RedteamClient(
        RedteamContext(
            gateway_url=GATEWAY_URL,
            bearer_token=resolve_bearer_token(
                os.environ, _read_text, _config_bearer_file()
            ),
            timeout=10.0,
            request=None,  # real stdlib HTTP client, never a fake transport
        )
    )


def test_gateway_url_resolves_from_config_or_env():
    assert isinstance(GATEWAY_URL, str) and GATEWAY_URL.startswith("http")


@requires_live_gateway
def test_live_tempest_status_returns_valid_json():
    result = _live_client().status()
    assert isinstance(result, dict)
    assert "mission" in result and "llm" in result
    json.dumps(result)  # must survive a JSON round trip


@requires_live_gateway
def test_live_tempest_hats_list_returns_a_list():
    result = _live_client().hats_list()
    assert result.get("ok") is True
    hats = result.get("hats")
    assert isinstance(hats, list)


@requires_live_gateway
def test_live_tempest_blue_gaps_returns_expected_keys():
    result = _live_client().blue_gaps()
    assert isinstance(result, dict)
    assert {"ok", "status", "data"} <= set(result)
