# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Minimal Omega Prime configuration surface.

v10 introduces per-family Prime capability flags. There is intentionally no
central settings module elsewhere in the repo (per-subsystem JSON documents
loaded from explicit paths are the convention — see ``policy/policy.py``
``SeatPolicy.load``); this module is the one small exception, carrying only
the Prime capability gates.

Sources, in precedence order (later wins):

1. Built-in defaults — every Prime family disabled.
2. An optional ``omega-prime.json`` at the repository root (stdlib json, the
   ``SeatPolicy.load`` precedent). Missing file ⇒ defaults.
3. ``OMEGA_PRIME_*`` environment overrides, e.g.
   ``OMEGA_PRIME_PRIME_RLM_ENABLED=1`` sets ``prime.rlm.enabled``.

All flags default off: with every family disabled, Omega Prime behaves
exactly as it did pre-v10 (LOOP-07).
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Prime capability families gated by config. Each maps to one rostered tool
# family or loop hook; a disabled family is not registered and is absent from
# the prompt (LOOP-05).
PRIME_FAMILIES: tuple[str, ...] = (
    "rlm",
    "harness",
    "goals",
    "heartbeat",
    "autonomous",
    "messaging",
    "kernel",
)

_ENV_PREFIX = "OMEGA_PRIME_"


def _defaults() -> dict[str, Any]:
    return {
        "prime": {family: {"enabled": False} for family in PRIME_FAMILIES},
        # Red team family (T3MP3ST loopback gateway): a top-level key, not a
        # Prime family. Off by default; the gateway and bearer file follow the
        # upstream contract.
        "redteam": {
            "enabled": False,
            "gateway_url": "http://127.0.0.1:3333",
            "bearer_token_file": "",
            # Sidecar authorization (Phase 79). All restrictive by default:
            # kill stops everything, RoE is required, and an empty roe_file
            # means BLOCKED when enabled. roe_key is the detached-HMAC
            # verification key (config-supplied, never fetched at runtime).
            "kill": False,
            "roe_required": True,
            "roe_file": "",
            "roe_key": "",
        },
        # Telemetry export (Phase 81). Off by default with an empty endpoint;
        # the exporter refuses to start until an endpoint is configured.
        "telemetry": {
            "otlp": {
                "enabled": False,
                "endpoint": "",
                "interval_seconds": 60,
            },
        },
        # Host-token rotation (Phase 82). Off by default; the scheduled
        # `rotate --due-only` job skips unless enabled. overlap_hours is the
        # scheduled-run window (the manual CLI default stays 1h).
        "rotation": {
            "enabled": False,
            "overlap_hours": 24,
        },
        # Per-caller usage quotas (Phase 82). Report-only by default and
        # inert until `document` names a quota file: report mode enforces
        # nothing (counts only), soft warns at >=80%, hard denies over-limit
        # pre-dispatch. An empty document means unlimited for every caller.
        "quotas": {
            "mode": "report",
            "document": "",
            "rollup_dir": ".omega-quotas/",
        },
    }


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``overlay`` onto ``base`` (overlay wins on scalars)."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(root: str | Path | None = None) -> dict[str, Any]:
    """Load the merged configuration. A missing config file is not an error."""
    config = _defaults()
    if root is not None:
        path = Path(root) / "omega-prime.json"
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("omega-prime.json must contain a JSON object")
            _merge(config, loaded)
    _merge(config, _env_overrides(os.environ))
    _normalize_quotas_mode(config)
    return config


QUOTA_MODES = ("report", "soft", "hard")


def _normalize_quotas_mode(config: dict[str, Any]) -> None:
    """Fall back to `report` loudly when `quotas.mode` is unknown.

    Config loading never crashes on a bogus mode and never silently
    enables enforcement: anything outside {report, soft, hard} reads as
    report (the enforcing-nothing default).
    """
    node = config.get("quotas")
    if not isinstance(node, dict):
        return
    mode = node.get("mode", "report")
    if mode not in QUOTA_MODES:
        print(
            f"warning: ignoring invalid quotas.mode={mode!r}; using 'report'",
            file=sys.stderr,
        )
        node["mode"] = "report"


def _env_overrides(environ: Mapping[str, str]) -> dict[str, Any]:
    """Map ``OMEGA_PRIME_PRIME_<FAMILY>_ENABLED`` style vars onto the tree."""
    overlay: dict[str, Any] = {}
    for key, value in environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        parts = key[len(_ENV_PREFIX) :].lower().split("_")
        if not parts:
            continue
        if parts[0] == "prime":
            # prime_<family>_enabled — family names are single words today.
            if len(parts) == 3 and parts[1] in PRIME_FAMILIES and parts[2] == "enabled":
                enabled = value.strip().lower() in ("1", "true", "yes", "on")
                overlay.setdefault("prime", {}).setdefault(parts[1], {})["enabled"] = (
                    enabled
                )
        elif parts[0] == "redteam":
            # redteam_enabled / redteam_gateway_url.
            if len(parts) == 2 and parts[1] == "enabled":
                overlay.setdefault("redteam", {})["enabled"] = (
                    value.strip().lower()
                    in (
                        "1",
                        "true",
                        "yes",
                        "on",
                    )
                )
            elif len(parts) == 3 and parts[1] == "gateway" and parts[2] == "url":
                overlay.setdefault("redteam", {})["gateway_url"] = value.strip()
            elif len(parts) == 2 and parts[1] == "kill":
                # OMEGA_PRIME_REDTEAM_KILL — evaluated before every other
                # redteam control; true implies BLOCKED (GAP-08).
                overlay.setdefault("redteam", {})["kill"] = value.strip().lower() in (
                    "1",
                    "true",
                    "yes",
                    "on",
                )
            elif len(parts) == 3 and parts[1] == "roe" and parts[2] == "required":
                # OMEGA_PRIME_REDTEAM_ROE_REQUIRED — default true (fail
                # closed); explicit falsy opts out of the RoE requirement.
                overlay.setdefault("redteam", {})["roe_required"] = (
                    value.strip().lower()
                    in (
                        "1",
                        "true",
                        "yes",
                        "on",
                    )
                )
            elif len(parts) == 3 and parts[1] == "roe" and parts[2] == "file":
                # OMEGA_PRIME_REDTEAM_ROE_FILE — path to the signed artifact;
                # empty (the default) means BLOCKED when enabled.
                overlay.setdefault("redteam", {})["roe_file"] = value.strip()
            elif len(parts) == 3 and parts[1] == "roe" and parts[2] == "key":
                # OMEGA_PRIME_REDTEAM_ROE_KEY — detached-HMAC verification
                # key. Secret: never logged, never rendered to metrics.
                overlay.setdefault("redteam", {})["roe_key"] = value.strip()
        elif parts[0] == "telemetry":
            # OMEGA_PRIME_TELEMETRY_OTLP_ENABLED — truthy enables export;
            # the exporter additionally requires a non-empty push target.
            if len(parts) == 3 and parts[1] == "otlp" and parts[2] == "enabled":
                overlay.setdefault("telemetry", {}).setdefault("otlp", {})[
                    "enabled"
                ] = value.strip().lower() in ("1", "true", "yes", "on")
            elif len(parts) == 3 and parts[1] == "otlp" and parts[2] == "endpoint":
                # OMEGA_PRIME_TELEMETRY_OTLP_ENDPOINT — push target URL.
                overlay.setdefault("telemetry", {}).setdefault("otlp", {})[
                    "endpoint"
                ] = value.strip()
            elif (
                len(parts) == 4
                and parts[1] == "otlp"
                and parts[2] == "interval"
                and parts[3] == "seconds"
            ):
                # OMEGA_PRIME_TELEMETRY_OTLP_INTERVAL_SECONDS — push cadence.
                try:
                    interval = int(value.strip())
                except ValueError:
                    continue
                overlay.setdefault("telemetry", {}).setdefault("otlp", {})[
                    "interval_seconds"
                ] = interval
        elif parts[0] == "rotation":
            # OMEGA_PRIME_ROTATION_ENABLED — truthy enables scheduled
            # rotation; anything else leaves the default (off) in place.
            if len(parts) == 2 and parts[1] == "enabled":
                overlay.setdefault("rotation", {})["enabled"] = (
                    value.strip().lower() in ("1", "true", "yes", "on")
                )
            elif len(parts) == 3 and parts[1] == "overlap" and parts[2] == "hours":
                # OMEGA_PRIME_ROTATION_OVERLAP_HOURS — scheduled-run window.
                # Invalid values fall back to the default loudly; config
                # loading never crashes and never silently enables rotation.
                try:
                    hours = float(value.strip())
                except ValueError:
                    hours = float("nan")
                if not (hours > 0 and hours != float("inf")):
                    print(
                        "warning: ignoring invalid OMEGA_PRIME_ROTATION_OVERLAP_HOURS"
                        f"={value.strip()!r}; using default",
                        file=sys.stderr,
                    )
                    continue
                overlay.setdefault("rotation", {})["overlap_hours"] = hours
        elif parts[0] == "quotas":
            # OMEGA_PRIME_QUOTAS_MODE — report/soft/hard; anything else
            # falls back to report loudly in _normalize_quotas_mode
            # (never crashes, never silently enables enforcement).
            if len(parts) == 2 and parts[1] == "mode":
                overlay.setdefault("quotas", {})["mode"] = value.strip().lower()
            elif len(parts) == 2 and parts[1] == "document":
                # OMEGA_PRIME_QUOTAS_DOCUMENT — path to the quota JSON;
                # empty (the default) means the engine is inert.
                overlay.setdefault("quotas", {})["document"] = value.strip()
    return overlay


def prime_enabled(config: dict[str, Any], family: str) -> bool:
    """Whether one Prime capability family is enabled. Unknown ⇒ False."""
    if family not in PRIME_FAMILIES:
        return False
    node = config.get("prime", {}).get(family, {})
    return bool(isinstance(node, dict) and node.get("enabled") is True)


def redteam_enabled(config: dict[str, Any]) -> bool:
    """Whether the red team gateway family is enabled. Absent ⇒ False."""
    node = config.get("redteam", {})
    return bool(isinstance(node, dict) and node.get("enabled") is True)


def redteam_kill(config: dict[str, Any]) -> bool:
    """Whether the red team kill switch is set. Absent ⇒ False."""
    node = config.get("redteam", {})
    return bool(isinstance(node, dict) and node.get("kill") is True)


def family_config(config: dict[str, Any], family: str) -> dict[str, Any]:
    """The raw config subtree for one family (empty dict when absent)."""
    node = config.get("prime", {}).get(family, {})
    return node if isinstance(node, dict) else {}


__all__ = [
    "PRIME_FAMILIES",
    "QUOTA_MODES",
    "family_config",
    "load_config",
    "prime_enabled",
    "redteam_enabled",
    "redteam_kill",
]
