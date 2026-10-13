# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Slash commands Grok Bot runs through one tool.

A command is the whole user message: ``/omega-help``, not a sentence that
mentions it. Execution goes through the registry. A tool error stays an error.

The 16 curated commands come first. Generated commands follow, one per
checked-in skill and routine (``omega_prime.commands.generated``). A skill
command returns that skill's instructions. A routine command whose exact stem
is a workflow runs that workflow; any other routine returns its text.
"""

from __future__ import annotations

import functools
import importlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from omega_prime.commands.generated import (
    CATALOG_ROOT,
    CommandSpec,
    GenerationError,
    generate_commands,
)
from omega_prime.commands.workflows import (
    HINDSIGHT_PREFIX,
    WORKFLOW_NAMES,
    connector_requests,
    dispatch_tool,
    run_workflow,
    validated_doctor_context,
)
from omega_prime.skills_runtime.guards import parse_frontmatter

_COMMAND_RE = re.compile(
    r"^/(omega-[a-z0-9]+(?:-[a-z0-9]+)*)(?:\s+(\S[\s\S]*))?$",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|private[_-]?key)\s*[:=]\s*\S+"
)


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        "omega-help",
        "/omega-help",
        "List every slash command.",
        "The user asks what Omega Prime can run, or which slash commands exist.",
    ),
    CommandSpec(
        "omega-doctor",
        "/omega-doctor",
        "Run the lead doctor check.",
        "The user asks for doctor, integrity, or whether the seat is healthy.",
    ),
    CommandSpec(
        "omega-roster",
        "/omega-roster",
        "Show the served roster status.",
        "The user asks which tools or seats are served.",
    ),
    CommandSpec(
        "omega-recall",
        "/omega-recall <query>",
        "Recall memory for a query.",
        "The user asks what Omega Prime remembers about a topic.",
        needs_args=True,
    ),
    CommandSpec(
        "omega-retain",
        "/omega-retain <text>",
        "Store one memory entry. Refuses secrets.",
        "The user asks to remember a fact that is not a secret.",
        needs_args=True,
    ),
    CommandSpec(
        "omega-gates",
        "/omega-gates",
        "Run the repo gate suites.",
        "The user asks for the full test, type, and lint gates.",
    ),
    CommandSpec(
        "omega-python",
        "/omega-python",
        "Check this package with ruff and compileall.",
        "The user asks whether the Python package is clean.",
    ),
    CommandSpec(
        "omega-delegate",
        "/omega-delegate <goal>",
        "Delegate one goal to a child agent.",
        "The user asks to fan a goal out to a child agent.",
        needs_args=True,
    ),
    CommandSpec(
        "omega-workflow",
        "/omega-workflow <name>",
        "Run a named workflow.",
        "The user names a workflow: onboard, python-clean, connectors, or desk.",
        needs_args=True,
    ),
    CommandSpec(
        "omega-onboard",
        "/omega-onboard",
        "First run: doctor, roster, seed memory, connector requests.",
        "The user asks to onboard, set up, or take a first run.",
    ),
    CommandSpec(
        "omega-connectors",
        "/omega-connectors",
        "List configured connectors and requests for missing env names.",
        "The user asks which connectors are missing or how to connect one.",
    ),
    CommandSpec(
        "omega-desk",
        "/omega-desk",
        "Desk status: doctor, roster, and the next intake.",
        "The user asks for desk status or the next intake.",
    ),
    CommandSpec(
        "omega-redteam-run",
        "/omega-redteam-run --suite <name>",
        "Run a red-team simulation suite through the fail-closed gate.",
        "The user asks to run the red-team harness or a simulation suite.",
        needs_args=True,
    ),
    CommandSpec(
        "omega-redteam-status",
        "/omega-redteam-status",
        "Show the red-team harness state and recent transitions.",
        "The user asks for red-team harness status or state.",
    ),
    CommandSpec(
        "omega-redteam-findings",
        "/omega-redteam-findings [--limit N]",
        "Tail recent red-team harness findings. Read-only.",
        "The user asks for red-team findings or detections.",
    ),
    CommandSpec(
        "omega-redteam-triage",
        "/omega-redteam-triage <dedupe-hash> --rationale <text>",
        "Propose triage for one red-team finding.",
        "The user asks to triage a red-team finding with a rationale.",
        needs_args=True,
    ),
)

_BY_NAME = {spec.name: spec for spec in COMMANDS}
_CATALOG_ROOT = CATALOG_ROOT


@dataclass(frozen=True)
class _Generated:
    """Generated commands for one root, or the error that aborted them."""

    root: Path
    commands: tuple[CommandSpec, ...] = ()
    error: str | None = None
    by_name: Mapping[str, CommandSpec] = field(default_factory=dict)


@functools.cache
def _generated_for(root: Path) -> _Generated:
    """Build once per root. A generation conflict is returned, never raised."""
    try:
        commands = generate_commands(root, _BY_NAME)
    except GenerationError as exc:
        return _Generated(root, error=str(exc))
    return _Generated(root, commands, None, {spec.name: spec for spec in commands})


def _generated() -> _Generated:
    return _generated_for(_CATALOG_ROOT)


def _command_names() -> list[str]:
    return [spec.name for spec in COMMANDS] + [
        spec.name for spec in _generated().commands
    ]


def parse_omega_command(text: str) -> tuple[str, str] | None:
    """Return ``(name, args)`` when ``text`` is only one ``/omega-`` command."""
    if not isinstance(text, str):
        return None
    match = _COMMAND_RE.match(text.strip())
    if match is None:
        return None
    name = match.group(1).lower()
    args = match.group(2) or ""
    return name, args.strip()


def execute_command(
    text: str,
    registry: Any,
    env: Mapping[str, str],
    *,
    doctor_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one command. A bad command is a dict, not an exception.

    ``doctor_context`` carries explicit caller state (``prompt_sha256`` and
    ``installed_skills`` only) into direct and workflow doctor steps. It is
    validated once with the shared strict convention: any other shape is
    treated as missing, never healthy and never fatal.
    """
    parsed = parse_omega_command(text)
    if parsed is None:
        return {
            "ok": False,
            "command": "",
            "error": "not_a_command",
            "hint": "expected /omega-<name>",
        }
    name, args = parsed
    spec = _BY_NAME.get(name)
    if spec is None:
        generated = _generated()
        found = generated.by_name.get(name)
        if found is not None:
            context = validated_doctor_context(doctor_context)
            return _run_generated(found, args, registry, env, context, generated.root)
        if generated.error is not None:
            return {
                "ok": False,
                "command": name,
                "error": "catalog_generation_failed",
                "reason": generated.error,
                "commands": _command_names(),
            }
        return {
            "ok": False,
            "command": name,
            "error": "unknown_command",
            "commands": _command_names(),
        }
    if spec.needs_args and not args:
        return {
            "ok": False,
            "command": name,
            "error": "missing_args",
            "usage": spec.usage,
        }
    if name == "omega-retain" and _is_secret(args):
        return {
            "ok": False,
            "command": name,
            "error": "secret_refused",
            "reason": "Do not put a secret in chat or memory.",
        }
    context = validated_doctor_context(doctor_context)
    return _run(name, args, registry, env, context)


def command_catalog() -> list[dict[str, str]]:
    """Name, usage, summary, and when: curated in order, then generated by name."""
    return [
        {
            "name": spec.name,
            "usage": spec.usage,
            "summary": spec.summary,
            "when": spec.when,
        }
        for spec in (*COMMANDS, *_generated().commands)
    ]


def _run(
    name: str,
    args: str,
    registry: Any,
    env: Mapping[str, str],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    doctor_context = dict(context or {})
    if name == "omega-help":
        error = _generated().error
        if error is not None:
            return _fail(
                name,
                "catalog_generation_failed",
                {"commands": command_catalog(), "reason": error},
            )
        return _ok(name, {"commands": command_catalog()})
    if name == "omega-connectors":
        return _ok(name, connector_requests(env))
    if name == "omega-python":
        return _workflow("omega-python", "python-clean", registry, env, doctor_context)
    if name == "omega-onboard":
        return _workflow("omega-onboard", "onboard", registry, env, doctor_context)
    if name == "omega-desk":
        return _workflow("omega-desk", "desk", registry, env, doctor_context)
    if name == "omega-workflow":
        if args not in WORKFLOW_NAMES:
            body = run_workflow(args, registry, env, doctor_context=doctor_context)
            return _fail(name, "unknown_workflow", body)
        return _workflow(name, args, registry, env, doctor_context)
    if name == "omega-recall":
        return _tool(name, registry, "lead_memory_recall", {"query": args})
    if name == "omega-retain":
        content = args if args.startswith(HINDSIGHT_PREFIX) else HINDSIGHT_PREFIX + args
        return _tool(
            name,
            registry,
            "memory",
            {"action": "add", "target": "memory", "content": content},
        )
    if name == "omega-delegate":
        return _tool(name, registry, "delegate_task", {"goal": args})
    if name == "omega-doctor":
        return _doctor(name, registry, doctor_context)
    if name == "omega-roster":
        return _tool(name, registry, "lead_roster_status", {})
    if name == "omega-gates":
        return _tool(name, registry, "qua_gates_run", {})
    if name == "omega-redteam-run":
        return _redteam(name, args, env, "run")
    if name == "omega-redteam-status":
        return _redteam(name, args, env, "status")
    if name == "omega-redteam-findings":
        return _redteam(name, args, env, "findings")
    if name == "omega-redteam-triage":
        return _redteam(name, args, env, "triage")
    return _fail(name, "unknown_command", {"commands": _command_names()})


def _redteam(
    command: str,
    args: str,
    env: Mapping[str, str],
    kind: str,
) -> dict[str, Any]:
    """Run one red-team harness command. A bad run is a dict, never a raise."""
    try:
        module_name = {
            "run": "omega_prime.commands.redteam_run",
            "status": "omega_prime.commands.redteam_status",
            "findings": "omega_prime.commands.redteam_findings",
            "triage": "omega_prime.commands.redteam_triage",
        }[kind]
        mod = importlib.import_module(module_name)
        return _ok(command, mod.run(args, env))
    except mod.Error as exc:
        return _fail(command, exc.error, exc.result)
    except Exception as exc:
        return _fail(
            command, "command_failed", {"error": f"{type(exc).__name__}: {exc}"}
        )


def _run_generated(
    spec: CommandSpec,
    args: str,
    registry: Any,
    env: Mapping[str, str],
    doctor_context: Mapping[str, Any],
    root: Path,
) -> dict[str, Any]:
    """Run one generated command. The file comes only from the catalog entry.

    ``args`` is passed back as text and never used as a path. A routine whose
    exact stem is a workflow runs it through ``run_workflow``, so approval
    gating is the same as for the curated command.
    """
    missing = _fail(
        spec.name, "source_missing", {"kind": spec.kind, "source": spec.source}
    )
    path = root.joinpath(*PurePosixPath(spec.source).parts)
    if not path.is_file():
        return missing
    stem = PurePosixPath(spec.source).stem
    if spec.kind == "routine" and stem in WORKFLOW_NAMES:
        return _workflow(spec.name, stem, registry, env, dict(doctor_context))
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return missing
    except (OSError, UnicodeDecodeError):
        return _fail(
            spec.name,
            "source_unreadable",
            {"kind": spec.kind, "source": spec.source},
        )
    instructions = text
    if spec.kind == "skill":
        _mapping, body, _error = parse_frontmatter(text)
        if body is None:
            return _fail(
                spec.name,
                "source_invalid",
                {"kind": spec.kind, "source": spec.source},
            )
        instructions = body
    return _ok(
        spec.name,
        {
            "kind": spec.kind,
            "source": spec.source,
            "description": spec.summary,
            "instructions": instructions,
            "args": args,
        },
    )


def _doctor(
    command: str, registry: Any, doctor_context: Mapping[str, Any]
) -> dict[str, Any]:
    """Run the direct doctor check with explicit caller context.

    Only validated ``prompt_sha256``/``installed_skills`` values reach the
    check; absent or invalid fields stay omitted so the seat evaluates them
    as missing. An explicit ``green`` false or ``ok`` false payload is a
    failed command that keeps its result, never a success.
    """
    payload = dispatch_tool(
        registry, "lead_doctor", {"action": "check", **doctor_context}
    )
    if payload.get("error") == "dispatch_failed":
        return {
            "ok": False,
            "command": command,
            "error": "dispatch_failed",
            "reason": payload.get("reason"),
            "result": payload,
        }
    if payload.get("error"):
        return _fail(command, str(payload["error"]), payload)
    if payload.get("green") is False or payload.get("ok") is False:
        return _fail(command, "doctor_failed", payload)
    return _ok(command, payload)


def _workflow(
    command: str,
    workflow: str,
    registry: Any,
    env: Mapping[str, str],
    doctor_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body = run_workflow(workflow, registry, env, doctor_context=doctor_context)
    if body.get("ok"):
        return _ok(command, body)
    return _fail(command, str(body.get("error") or "workflow_failed"), body)


def _tool(
    command: str, registry: Any, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = dispatch_tool(registry, tool, arguments)
    if payload.get("error") == "dispatch_failed":
        return {
            "ok": False,
            "command": command,
            "error": "dispatch_failed",
            "reason": payload.get("reason"),
            "result": payload,
        }
    if payload.get("error"):
        return _fail(command, str(payload["error"]), payload)
    return _ok(command, payload)


def _ok(command: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "command": command, "result": result}


def _fail(command: str, error: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": False, "command": command, "error": error, "result": result}


def _is_secret(text: str) -> bool:
    if text.startswith("sk-"):
        return True
    if "BEGIN PRIVATE KEY" in text:
        return True
    return _SECRET_RE.search(text) is not None


__all__ = [
    "COMMANDS",
    "CommandSpec",
    "command_catalog",
    "execute_command",
    "parse_omega_command",
]
