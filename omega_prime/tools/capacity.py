# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``omega_capacity``: per-plan remaining quota and the allowlist state.

The numbers come from ``omp usage --json``. This module does not call a model
HTTP API. Fleet status is reported only when a fleet client is importable.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from omega_prime.engine.omp_driver import EngineError, omp_version, resolve_omp
from omega_prime.engine.plan_router import (
    PlanNotAllowed,
    evaluate_all,
    read_usage,
    select,
)
from omega_prime.engine.protocol import OMP_SUPPORTED_MAJOR
from omega_prime.tools.registry import ToolRegistry

CAPACITY_TOOL_NAMES = ("omega_capacity",)


def capacity_report(
    env: Mapping[str, str] | None = None,
    *,
    plan: str | None = None,
) -> dict[str, Any]:
    import os

    mapping = env if env is not None else os.environ
    binary = resolve_omp(mapping)
    version: str | None = None
    major: int | None = None
    available = False
    quotas = {}
    if binary is not None:
        try:
            parsed = omp_version(binary, mapping)
        except (EngineError, OSError):
            parsed = None
        if parsed is not None and parsed[0] == OMP_SUPPORTED_MAJOR:
            available = True
            version = ".".join(str(part) for part in parsed)
            major = parsed[0]
            quotas = read_usage(binary, mapping)
    plans = []
    for decision in evaluate_all(mapping):
        quota = quotas.get(decision.plan)
        plans.append(
            {
                "plan": decision.plan,
                "provider": decision.provider,
                "model": decision.model_id,
                "allowlisted": decision.allowlisted,
                "auth": decision.auth,
                "verdict": decision.verdict,
                "reason": decision.reason,
                "source": "omp usage" if quota is not None else "none",
                "window": None if quota is None else quota.window,
                "used": None if quota is None else quota.used,
                "remaining": None if quota is None else quota.remaining,
                "percent": None if quota is None else quota.percent,
                "resets_at": None if quota is None else quota.resets_at,
                "confidence": "unknown" if quota is None else quota.confidence,
            }
        )
    selected: dict[str, Any] | None = None
    select_error: str | None = None
    try:
        choice = select(mapping, quotas, requested=plan)
    except PlanNotAllowed as exc:
        select_error = exc.detail
    else:
        selected = {
            "plan": choice.decision.plan,
            "provider": choice.decision.provider,
            "model": choice.decision.model_id,
            "auth": choice.decision.auth,
        }
    api_key = [item.plan for item in evaluate_all(mapping) if item.auth == "api_key"]
    opted = [
        item.plan
        for item in evaluate_all(mapping)
        if item.auth == "subscription_opt_in"
    ]
    return {
        "plans": plans,
        "allowlist": {
            "default": "allowed-only",
            "subscription_opt_in": opted,
            "api_key": api_key,
            "refused_subscription_oauth": ["anthropic", "muse"],
        },
        "selected": selected,
        "select_error": select_error,
        "fleet": fleet_status(mapping),
        "omp": {
            "available": available,
            "path_found": binary is not None,
            "version": version,
            "major": major,
            "supported_major": OMP_SUPPORTED_MAJOR,
        },
        "state_dir_set": bool(str(mapping.get("OMEGA_PRIME_STATE_DIR", "")).strip()),
    }


def fleet_status(env: Mapping[str, str]) -> dict[str, Any]:
    """Read fleet status only when the A1b client is installed."""
    del env
    try:
        from omega_prime.dispatch import fleet as fleet_module
    except ImportError:
        return {"status": "not_configured", "reason": "fleet client is A1b"}
    reader = getattr(fleet_module, "status", None)
    if not callable(reader):
        return {"status": "not_configured", "reason": "fleet client is A1b"}
    try:
        payload = reader()
    except Exception:
        return {"status": "not_configured", "reason": "fleet client is A1b"}
    if isinstance(payload, dict):
        return payload
    return {"status": "not_configured", "reason": "fleet client is A1b"}


def register_capacity_tools(registry: ToolRegistry) -> list[str]:
    """Register the read-only capacity tool."""

    def omega_capacity(plan: str | None = None) -> dict[str, Any]:
        import os

        return capacity_report(os.environ, plan=plan)

    registry.register(
        "omega_capacity",
        "Report per-plan remaining quota from omp usage and the compliance "
        "allowlist. The default allowlist is empty. API-key profiles are "
        "listed when their environment variable is set. Anthropic and Muse "
        "subscription OAuth are never allowlisted. Values of keys are not "
        "returned. This tool does not call a model HTTP API.",
        {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "string",
                    "description": (
                        "Optional plan name: zai, xai, anthropic, muse, cursor, or "
                        "chatgpt. "
                        "Omit it to let the router choose."
                    ),
                }
            },
            "required": [],
        },
        omega_capacity,
    )
    return list(CAPACITY_TOOL_NAMES)
