# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Dispatch tools for the local sandbox lane and the coding fleet.

``omega_dispatch`` is approval-gated and also requires ``confirm_token``.
The token is compared with ``OMEGA_PRIME_DISPATCH_CONFIRM`` (or
``OMEGA_PRIME_DISPATCH_CONFIRM_FILE``) and is not stored on the job.
``omega_jobs_cancel`` is approval-gated. ``omega_jobs_status`` is read-only.

These tools do not call a model HTTP API.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Mapping
from typing import Any

from omega_prime.engine.fleet_client import FleetClient, FleetError, JobSpec
from omega_prime.engine.lanes import LaneRouter
from omega_prime.tools.registry import ToolRegistry

DISPATCH_TOOL_NAMES = (
    "omega_dispatch",
    "omega_jobs_status",
    "omega_jobs_cancel",
)
_CONFIRM_NAME = "OMEGA_PRIME_DISPATCH_CONFIRM"


def register_dispatch_tools(registry: ToolRegistry) -> list[str]:
    """Register the A1b dispatch tools. Approval flags follow the handlers."""

    def omega_dispatch(
        repo: str,
        prompt: str,
        confirm_token: str,
        model: str | None = None,
        slash_command: str | None = None,
        slash_args: str | None = None,
        max_minutes: int | None = None,
        max_files: int | None = None,
        push: bool | None = None,
        pr: bool | None = None,
        base: str | None = None,
        branch: str | None = None,
        phase: str | None = None,
        plan: str | None = None,
        agent: str | None = None,
        approval_mode: str | None = None,
        notify: bool | None = None,
        max_attempts: int | None = None,
        links: list[str] | None = None,
        languages: list[str] | None = None,
        paths: list[str] | None = None,
        overflow: str = "spill",
    ) -> dict[str, Any]:
        if not confirm_matches(confirm_token, os.environ):
            return {"error": "confirm_rejected"}
        try:
            spec = JobSpec(
                repo=repo,
                prompt=prompt,
                model=model,
                slash_command=slash_command,
                slash_args=slash_args,
                max_minutes=max_minutes,
                max_files=max_files,
                push=push,
                pr=pr,
                base=base,
                branch=branch,
                phase=phase,
                plan=plan,
                agent=agent,
                approval_mode=approval_mode,
                notify=notify,
                max_attempts=max_attempts,
                links=tuple(links or ()),
            )
        except TypeError:
            return {"error": "invalid_spec"}
        router = LaneRouter(os.environ)
        try:
            return router.dispatch(
                spec,
                languages=tuple(languages or ()),
                paths=tuple(paths or ()),
                overflow=overflow,
            )
        except FleetError as exc:
            return {"error": exc.code}
        except Exception:
            return {"error": "local_start_failed"}

    def omega_jobs_status(
        job_id: str | None = None,
        after: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        client = FleetClient(os.environ)
        if not client.configured():
            return {"error": client.configuration_error() or "not_configured"}
        try:
            if job_id:
                return client.poll(job_id, after=after)
            return {
                "coordinator": client.coordinator_status(),
                "jobs": client.list_jobs(limit=limit),
            }
        except FleetError as exc:
            return {"error": exc.code}

    def omega_jobs_cancel(job_id: str) -> dict[str, Any]:
        client = FleetClient(os.environ)
        if not client.configured():
            return {"error": client.configuration_error() or "not_configured"}
        try:
            return {"job": client.cancel(job_id)}
        except FleetError as exc:
            return {"error": exc.code}

    registry.register(
        "omega_dispatch",
        "Route one JobSpec to the local sandbox lane or the coding fleet. "
        "The same fields are sent either way. Requires approval and "
        "confirm_token. The token is not stored. This tool does not call "
        "a model HTTP API.",
        {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "GitHub owner/name."},
                "prompt": {"type": "string"},
                "confirm_token": {
                    "type": "string",
                    "description": "Operator confirm token. Not stored on the job.",
                },
                "model": {"type": "string", "description": "provider/id."},
                "slash_command": {"type": "string"},
                "slash_args": {"type": "string"},
                "max_minutes": {"type": "integer"},
                "max_files": {"type": "integer"},
                "push": {"type": "boolean"},
                "pr": {"type": "boolean"},
                "base": {"type": "string"},
                "branch": {"type": "string"},
                "phase": {"type": "string"},
                "plan": {"type": "string"},
                "agent": {"type": "string"},
                "approval_mode": {"type": "string"},
                "notify": {"type": "boolean"},
                "max_attempts": {"type": "integer"},
                "links": {"type": "array", "items": {"type": "string"}},
                "languages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Local lane allows JS, TS, Python, and docs.",
                },
                "paths": {"type": "array", "items": {"type": "string"}},
                "overflow": {
                    "type": "string",
                    "enum": ["spill", "queue"],
                    "description": "When the box is full: spill to the fleet, or queue locally.",
                },
            },
            "required": ["repo", "prompt", "confirm_token"],
        },
        omega_dispatch,
        requires_approval=True,
    )
    registry.register(
        "omega_jobs_status",
        "Read coding-fleet job status. Pass job_id to poll one job and its "
        "events. Omit it to read GET /status and GET /jobs. Does not call "
        "a model HTTP API.",
        {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "after": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": [],
        },
        omega_jobs_status,
    )
    registry.register(
        "omega_jobs_cancel",
        "Cancel one coding-fleet job (POST /jobs/:id/cancel). Requires approval. "
        "Does not call a model HTTP API.",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        omega_jobs_cancel,
        requires_approval=True,
    )
    return list(DISPATCH_TOOL_NAMES)


def confirm_matches(provided: object, env: Mapping[str, str]) -> bool:
    """True when ``provided`` matches the configured confirm token.

    The comparison uses digests so the token value is not branched on
    directly. A missing configuration rejects the call.
    """
    if not isinstance(provided, str) or not provided:
        return False
    expected = _expected_confirm(env)
    if not expected:
        return False
    left = hashlib.sha256(provided.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def _expected_confirm(env: Mapping[str, str]) -> str:
    from omega_prime.engine.fleet_client import read_named_secret

    try:
        return read_named_secret(env, _CONFIRM_NAME)
    except FleetError:
        return ""


__all__ = [
    "DISPATCH_TOOL_NAMES",
    "confirm_matches",
    "register_dispatch_tools",
]
