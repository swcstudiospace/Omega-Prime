# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Fixed tool sequences for slash commands.

A workflow dispatches registry tools in order. It records a tool error
as that step's result. It does not replace the error with a success.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DOCTOR_CONTEXT_KEYS = ("prompt_sha256", "installed_skills")

SEED_PATH = Path(__file__).resolve().parent / "seeds" / "memory.txt"
HINDSIGHT_PREFIX = "[hindsight:omega-prime-lead] "
WORKFLOW_NAMES = (
    "onboard",
    "python-clean",
    "connectors",
    "desk",
    "nightly",
    "sweep",
    "ultrathink",
    "desk-lead",
)
_ROUTINE_LOCALS = ("nightly", "sweep", "ultrathink", "desk-lead")


def validated_doctor_context(value: Any) -> dict[str, Any]:
    """Return the strict caller-supplied doctor context, or ``{}`` when missing.

    Only ``prompt_sha256`` (a ``str``) and ``installed_skills`` (a list of
    ``str``) pass through. Any other shape — a non-mapping, an extra key, or
    a mistyped value — is treated as missing, never healthy and never fatal.
    The result never carries ``action``, registration, or approval fields.
    """
    if not isinstance(value, Mapping):
        return {}
    for key in value:
        if key not in _DOCTOR_CONTEXT_KEYS:
            return {}
    prompt = value.get("prompt_sha256")
    if prompt is not None and not isinstance(prompt, str):
        return {}
    skills = value.get("installed_skills")
    if skills is not None and (
        not isinstance(skills, list)
        or not all(isinstance(item, str) for item in skills)
    ):
        return {}
    context: dict[str, Any] = {}
    if prompt is not None:
        context["prompt_sha256"] = prompt
    if skills is not None:
        context["installed_skills"] = list(skills)
    return context


_CONNECTORS: tuple[dict[str, Any], ...] = (
    {
        "id": "x",
        "env": ("X_API_TOKEN",),
        "mode": "all",
        "ask": "Set X_API_TOKEN, X_API_TOKEN_FILE, or $OMEGA_PRIME_STATE_DIR/secrets/X_API_TOKEN (mode 0600). Do not paste it into chat.",
    },
    {
        "id": "telegram",
        "env": ("TELEGRAM_BOT_TOKEN",),
        "mode": "all",
        "ask": "Set TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_TOKEN_FILE, or $OMEGA_PRIME_STATE_DIR/secrets/TELEGRAM_BOT_TOKEN (mode 0600). Do not paste it into chat.",
    },
    {
        "id": "discord",
        "env": ("DISCORD_BOT_TOKEN",),
        "mode": "all",
        "ask": "Set DISCORD_BOT_TOKEN, DISCORD_BOT_TOKEN_FILE, or $OMEGA_PRIME_STATE_DIR/secrets/DISCORD_BOT_TOKEN (mode 0600). Do not paste it into chat.",
    },
    {
        "id": "delegate",
        "env": ("XAI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"),
        "mode": "any",
        "ask": "Set one delegate key (XAI_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY), its *_FILE variant, or the matching file under $OMEGA_PRIME_STATE_DIR/secrets/ (mode 0600). Do not paste it into chat.",
    },
    {
        "id": "substrate",
        "env": ("SUBSTRATE_TOKEN", "SUBSTRATE_TOKEN_GROK_BOT"),
        "mode": "any",
        "ask": "Set SUBSTRATE_TOKEN or SUBSTRATE_TOKEN_GROK_BOT, the matching *_FILE variable, or the file under $OMEGA_PRIME_STATE_DIR/secrets/ (mode 0600). Do not paste it into chat.",
    },
    {
        "id": "hindsight",
        "env": ("HINDSIGHT_API_KEY", "HINDSIGHT_API_TOKEN"),
        "mode": "any",
        "ask": "Set HINDSIGHT_API_KEY or HINDSIGHT_API_TOKEN, the matching *_FILE variable, or the file under $OMEGA_PRIME_STATE_DIR/secrets/ (mode 0600). HINDSIGHT_URL stays unset until you run your own service. Do not paste a secret into chat.",
    },
    {
        "id": "railway",
        "env": ("RAILWAY_TOKEN",),
        "mode": "all",
        "ask": "Set RAILWAY_TOKEN, RAILWAY_TOKEN_FILE, or $OMEGA_PRIME_STATE_DIR/secrets/RAILWAY_TOKEN (mode 0600). Do not paste it into chat.",
    },
    {
        "id": "greptile",
        "env": ("GREPTILE_API_KEY",),
        "mode": "all",
        "ask": "Set GREPTILE_API_KEY, GREPTILE_API_KEY_FILE, or $OMEGA_PRIME_STATE_DIR/secrets/GREPTILE_API_KEY (mode 0600). Do not paste it into chat.",
    },
    {
        "id": "vercel",
        "env": ("VERCEL_TOKEN",),
        "mode": "all",
        "ask": "Set VERCEL_TOKEN, VERCEL_TOKEN_FILE, or $OMEGA_PRIME_STATE_DIR/secrets/VERCEL_TOKEN (mode 0600). Do not paste it into chat.",
    },
    {
        "id": "play",
        "env": ("PLAY_CONSOLE_TOKEN",),
        "mode": "all",
        "ask": "Set PLAY_CONSOLE_TOKEN on the host for Play Console. Do not paste it into chat.",
    },
    {
        "id": "appstore",
        "env": ("ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_PRIVATE_KEY"),
        "mode": "all",
        "ask": "Set ASC_KEY_ID, ASC_ISSUER_ID, and ASC_PRIVATE_KEY on the host for App Store Connect. Do not paste them into chat.",
    },
)


@dataclass(frozen=True)
class Step:
    """One workflow step. ``local`` is ``seed`` or ``connectors``; otherwise ``tool`` runs."""

    name: str
    stop_on_error: bool
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    local: str | None = None


def connector_requests(env: Mapping[str, str]) -> dict[str, Any]:
    """Report connector ids from env names. Never copy env values."""
    configured: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    for spec in _CONNECTORS:
        names = tuple(spec["env"])
        present = _present(env, names, str(spec["mode"]))
        row: dict[str, Any] = {"id": spec["id"], "env": list(names)}
        if present:
            configured.append(row)
        else:
            row["ask"] = spec["ask"]
            requests.append(row)
    return {"configured": configured, "requests": requests}


def seed_memory(registry: Any) -> dict[str, Any]:
    """Add the checked-in seed through the memory tool. A duplicate is the store's concern."""
    if not SEED_PATH.is_file():
        return {
            "error": "seed_missing",
            "reason": "memory seed file is not in the package",
        }
    text = SEED_PATH.read_text(encoding="utf-8").strip()
    if not text or "§" in text:
        return {
            "error": "seed_missing",
            "reason": "memory seed file is empty or unusable",
        }
    content = text if text.startswith(HINDSIGHT_PREFIX) else HINDSIGHT_PREFIX + text
    return dispatch_tool(
        registry,
        "memory",
        {"action": "add", "target": "memory", "content": content},
    )


def run_workflow(
    name: str,
    registry: Any,
    env: Mapping[str, str],
    *,
    doctor_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one named workflow. Unknown names list the catalog.

    ``doctor_context`` carries explicit caller state (``prompt_sha256`` and
    ``installed_skills`` only) into the ``lead_doctor`` steps of ``onboard``
    and ``desk``. A workflow that runs every step but saw a failed optional
    step stays ``ok`` false with ``workflow_incomplete``; every step result
    is preserved.
    """
    context = validated_doctor_context(doctor_context)
    steps = _workflows(context).get(name)
    if steps is None:
        return {
            "ok": False,
            "workflow": name,
            "error": "unknown_workflow",
            "workflows": list(WORKFLOW_NAMES),
            "steps": [],
        }
    ran: list[dict[str, Any]] = []
    for step in steps:
        payload = _run_step(step, registry, env)
        ok = _step_ok(payload)
        ran.append({"name": step.name, "ok": ok, "result": payload})
        if not ok and step.stop_on_error:
            return {
                "ok": False,
                "workflow": name,
                "error": "workflow_failed",
                "steps": ran,
            }
    if any(not row["ok"] for row in ran):
        return {
            "ok": False,
            "workflow": name,
            "error": "workflow_incomplete",
            "steps": ran,
        }
    return {"ok": True, "workflow": name, "steps": ran}


def _workflows(
    doctor_context: Mapping[str, Any] | None = None,
) -> dict[str, tuple[Step, ...]]:
    context = validated_doctor_context(doctor_context)
    exe = sys.executable
    ruff = {
        "argv": [exe, "-m", "ruff", "check", "."],
        "timeout": 120,
    }
    compileall = {
        "argv": [exe, "-m", "compileall", "-q", "."],
        "timeout": 120,
    }
    doctor_arguments = {"action": "check", **context}
    return {
        "python-clean": (
            Step("run_terminal", True, tool="run_terminal", arguments=ruff),
            Step("run_terminal", True, tool="run_terminal", arguments=compileall),
        ),
        "onboard": (
            Step(
                "lead_doctor",
                False,
                tool="lead_doctor",
                arguments=dict(doctor_arguments),
            ),
            Step("lead_roster_status", False, tool="lead_roster_status", arguments={}),
            Step("seed_memory", False, local="seed"),
            Step("connectors", False, local="connectors"),
        ),
        "desk": (
            Step(
                "lead_doctor",
                False,
                tool="lead_doctor",
                arguments=dict(doctor_arguments),
            ),
            Step("lead_roster_status", False, tool="lead_roster_status", arguments={}),
            Step("lead_intake_next", False, tool="lead_intake_next", arguments={}),
        ),
        "connectors": (Step("connectors", False, local="connectors"),),
        "nightly": (Step("nightly_pass", True, local="nightly"),),
        "sweep": (Step("sweep_mentions", True, local="sweep"),),
        "ultrathink": (Step("resolve_turn_plan", True, local="ultrathink"),),
        "desk-lead": (Step("run_lead_pass", True, local="desk-lead"),),
    }


def _run_routine(name: str, env: Mapping[str, str], registry: Any) -> dict[str, Any]:
    """Run one routine in-process. Sweep cannot publish from this path.

    Sweep mentions go through ``x_mentions`` on ``registry``. A denied read
    is the result; the routine does not open its own X client.
    """
    try:
        if name == "nightly":
            from omega_prime.routines.nightly import execute_nightly

            return execute_nightly(env)
        if name == "sweep":
            from omega_prime.routines.sweep import execute_sweep

            return execute_sweep(env, registry=registry)
        if name == "ultrathink":
            from omega_prime.routines.ultrathink_turn import execute_ultrathink

            return execute_ultrathink(env)
        if name == "desk-lead":
            from omega_prime.routines.desk_lead import execute_desk_lead

            return execute_desk_lead(env, registry=registry)
    except Exception as exc:
        return {"error": "routine_failed", "reason": f"{type(exc).__name__}: {exc}"}
    return {"error": "routine_failed", "reason": f"unknown routine {name}"}


def _run_step(step: Step, registry: Any, env: Mapping[str, str]) -> dict[str, Any]:
    try:
        if step.local == "seed":
            return seed_memory(registry)
        if step.local == "connectors":
            return connector_requests(env)
        if step.local in _ROUTINE_LOCALS:
            return _run_routine(step.local, env, registry)
        if step.tool is None:
            return {"error": "dispatch_failed", "reason": "step has no tool"}
        return dispatch_tool(registry, step.tool, step.arguments or {})
    except Exception as exc:
        return {
            "error": "dispatch_failed",
            "reason": f"{type(exc).__name__}: {exc}",
        }


def dispatch_tool(
    registry: Any, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    from omega_prime.commands.context import get_nested_dispatch

    nested = get_nested_dispatch()
    try:
        raw = (
            nested(tool, arguments)
            if nested is not None
            else registry.dispatch(tool, arguments)
        )
    except Exception as exc:
        return {
            "error": "dispatch_failed",
            "reason": f"{type(exc).__name__}: {exc}",
        }
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "dispatch_failed", "reason": "tool result is not JSON"}
        if isinstance(parsed, dict):
            return parsed
        return {"error": "dispatch_failed", "reason": "tool result is not an object"}
    if isinstance(raw, dict):
        return raw
    return {"error": "dispatch_failed", "reason": "tool result is not an object"}


def _step_ok(payload: dict[str, Any]) -> bool:
    if payload.get("error"):
        return False
    if payload.get("green") is False:
        return False
    if payload.get("ok") is False:
        return False
    if "exit_code" not in payload:
        return True
    code = payload["exit_code"]
    return isinstance(code, int) and not isinstance(code, bool) and code == 0


def _present(env: Mapping[str, str], names: tuple[str, ...], mode: str) -> bool:
    flags = [_filled(env, name) for name in names]
    if mode == "any":
        return any(flags)
    return all(flags)


def _filled(env: Mapping[str, str], name: str) -> bool:
    from omega_prime.integrations.binding import secret_configured

    return secret_configured(env, name)


__all__ = [
    "HINDSIGHT_PREFIX",
    "SEED_PATH",
    "WORKFLOW_NAMES",
    "connector_requests",
    "dispatch_tool",
    "run_workflow",
    "seed_memory",
    "validated_doctor_context",
]
