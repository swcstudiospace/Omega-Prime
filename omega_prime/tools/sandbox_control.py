# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Sandbox family: OpenShell (Podman driver) sandboxes as first-class tools.

``sandbox_create``, ``sandbox_exec`` and ``sandbox_down`` change state and
require approval. ``sandbox_status`` and ``sandbox_list`` are read-only.
Each lifecycle change is written as a CloudEvent to the outbox and applied
to task rows through ``on_sandbox_event``. These tools never start the
OpenShell gateway, the Podman socket or an image build, and they do not
call a model HTTP API.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from omega_prime.sandbox_control.events import (
    EventSink,
    FanoutSink,
    HandlerSink,
    OutboxSink,
    dashboard_task_store,
)
from omega_prime.sandbox_control.provider import (
    OpenShellSandboxProvider,
    SandboxConfig,
    SandboxControlError,
    subprocess_runner,
)
from omega_prime.sdlc_events.idempotency import IdempotencyStore
from omega_prime.tools.registry import ToolRegistry

SANDBOX_TOOL_NAMES = (
    "sandbox_create",
    "sandbox_exec",
    "sandbox_down",
    "sandbox_status",
    "sandbox_list",
)

ProviderFactory = Callable[[], OpenShellSandboxProvider]


TASK_STORE_UNAVAILABLE = "unavailable"


def default_provider(env: Mapping[str, str] | None = None) -> OpenShellSandboxProvider:
    """Provider over the resolved env (``settings.json`` values included).

    ``env=None`` reads ``os.environ``. When the dashboard package is missing,
    lifecycle events reach only the outbox and every tool result says
    ``task_store: unavailable`` so callers know task rows were not updated.
    """
    env = dict(env if env is not None else os.environ)
    config = SandboxConfig.from_env(env)
    sinks: list[EventSink] = [OutboxSink(config.outbox)]
    store = dashboard_task_store(
        Path(
            env.get("OMEGA_PRIME_TASKS_FILE")
            or config.state_dir / "dashboard/tasks.json"
        )
    )
    if store is not None:
        sinks.append(
            HandlerSink(
                store, IdempotencyStore(config.state_dir / "events/sandbox-idem.json")
            )
        )
    return OpenShellSandboxProvider(
        config=config,
        runner=subprocess_runner(env),
        sink=FanoutSink(sinks),
        task_store_available=store is not None,
    )


def _guard(
    call: Callable[[], dict[str, Any]], provider: OpenShellSandboxProvider
) -> dict[str, Any]:
    sink = provider.sink
    before = len(sink.errors) if isinstance(sink, FanoutSink) else 0
    try:
        out = call()
    except SandboxControlError as exc:
        out = exc.to_dict()
    if isinstance(sink, FanoutSink) and len(sink.errors) > before:
        out["event_sink_errors"] = sink.errors[before:]
    if not provider.task_store_available:
        out["task_store"] = TASK_STORE_UNAVAILABLE
    return out


def register_sandbox_control_tools(
    registry: ToolRegistry,
    provider_factory: ProviderFactory | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Register the Sandbox family. The provider is built per call (cheap, no I/O).

    Pass the resolved ``env`` from ``default_registry`` so state paths such as
    ``OMEGA_PRIME_STATE_DIR`` from ``settings.json`` are honoured.
    """
    resolved = dict(env) if env is not None else None
    factory = provider_factory or (lambda: default_provider(resolved))

    def sandbox_create(
        slug: str, task_id: str | None = None, ready_timeout_s: int | None = None
    ) -> dict[str, Any]:
        provider = factory()
        return _guard(
            lambda: provider.create(
                slug,
                task_id=task_id,
                ready_timeout_s=float(ready_timeout_s) if ready_timeout_s else None,
            ),
            provider,
        )

    def sandbox_exec(
        name: str,
        argv: list[str],
        cwd: str = "/sandbox",
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_s: int = 600,
        report_exit: bool = False,
    ) -> dict[str, Any]:
        provider = factory()
        return _guard(
            lambda: provider.exec(
                name,
                argv,
                cwd=cwd,
                env=env,
                stdin=stdin,
                timeout_s=timeout_s,
                report_exit=report_exit,
            ),
            provider,
        )

    def sandbox_down(name: str) -> dict[str, Any]:
        provider = factory()
        return _guard(lambda: provider.down(name), provider)

    def sandbox_status(name: str) -> dict[str, Any]:
        provider = factory()
        return _guard(lambda: provider.status(name), provider)

    def sandbox_list() -> dict[str, Any]:
        provider = factory()
        return _guard(lambda: {"sandboxes": provider.list_sandboxes()}, provider)

    name_schema = {
        "type": "string",
        "description": "Sandbox name. Exec and down accept only ompsbx-* names.",
    }
    registry.register(
        "sandbox_create",
        "Create an OpenShell sandbox (Podman driver) named ompsbx-<slug>-<hex> "
        "from the omp-sandbox image and policy, wait for Ready, and evaluate "
        "the readiness gate (container, broker relay, tools, skills). Fails "
        "closed when the gateway is not Connected or the policy is missing. "
        "Never starts the gateway or builds an image. Requires approval.",
        {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Short label, e.g. the task slug.",
                },
                "task_id": {
                    "type": "string",
                    "description": "Owning dashboard task id (e.g. gb:00045). "
                    "Blocking lifecycle events mark it blocked.",
                },
                "ready_timeout_s": {"type": "integer", "minimum": 10, "maximum": 900},
            },
            "required": ["slug"],
        },
        sandbox_create,
        requires_approval=True,
    )
    registry.register(
        "sandbox_exec",
        "Run one argv (no shell string) in an ompsbx-* sandbox. The cwd must be "
        "under /sandbox. Secret-shaped env names are refused. Output tails are "
        "bounded and redacted. report_exit=true marks this as the task's agent "
        "process: a non-zero exit blocks the owning task. Requires approval.",
        {
            "type": "object",
            "properties": {
                "name": name_schema,
                "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "cwd": {"type": "string"},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
                "stdin": {"type": "string"},
                "timeout_s": {"type": "integer", "minimum": 1, "maximum": 3600},
                "report_exit": {"type": "boolean"},
            },
            "required": ["name", "argv"],
        },
        sandbox_exec,
        requires_approval=True,
    )
    registry.register(
        "sandbox_down",
        "Delete an ompsbx-* sandbox and verify no containers, volumes or list "
        "rows remain. Emits the removed lifecycle event. Requires approval.",
        {"type": "object", "properties": {"name": name_schema}, "required": ["name"]},
        sandbox_down,
        requires_approval=True,
    )
    registry.register(
        "sandbox_status",
        "Read one OpenShell sandbox: phase, Ready condition, exit code, owner "
        "and owning task. Read-only.",
        {"type": "object", "properties": {"name": name_schema}, "required": ["name"]},
        sandbox_status,
    )
    registry.register(
        "sandbox_list",
        "List OpenShell sandboxes on this gateway with phase and whether these "
        "tools own them (ompsbx-*). Read-only.",
        {"type": "object", "properties": {}, "required": []},
        sandbox_list,
    )
    return list(SANDBOX_TOOL_NAMES)
