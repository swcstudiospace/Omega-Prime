# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Collect a bounded, read-only Herdr pane snapshot."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.redact import redact_value


def _default_runner(argv: list[str], *, timeout: float) -> str:
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode != 0:
        raise OSError("Herdr snapshot unavailable")
    return result.stdout


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _collect_agents(value: Any, workspace: str | None = None) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(node: Any, inherited_workspace: str | None) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child, inherited_workspace)
            return
        if not isinstance(node, dict):
            return
        label = (
            _text(node.get("label")) or _text(node.get("name")) or inherited_workspace
        )
        workspace_label = inherited_workspace or _text(node.get("label"))
        agent_session = node.get("agent_session")
        session = (
            agent_session.get("value") if isinstance(agent_session, dict) else None
        )
        pane_id = _text(node.get("pane_id"))
        is_agent = pane_id is not None and any(
            key in node
            for key in ("agent_status", "agent_session", "terminal_title", "cwd")
        )
        if is_agent and pane_id is not None and pane_id not in seen:
            seen.add(pane_id)
            found.append(
                {
                    "kind": "herdr_pane",
                    "pane_id": pane_id,
                    "workspace": workspace_label or label,
                    "name": _text(node.get("name")),
                    "agent_status": _text(node.get("agent_status")),
                    "cwd": _text(node.get("cwd")),
                    "terminal_title": _text(node.get("terminal_title")),
                    "session_path": _text(session),
                    "has_live_agent": (
                        bool(_text(node.get("agent")))
                        if "agent" in node
                        else (isinstance(session, str) and bool(session))
                        if "agent_session" in node
                        else node.get("agent_status") in {"idle", "working", "blocked"}
                    ),
                }
            )
        child_scope = inherited_workspace or (
            label
            if any(key in node for key in ("tabs", "panes", "workspace"))
            else None
        )
        for key, child in node.items():
            child_workspace = (
                child_scope
                if key in {"workspaces", "workspace", "tabs", "panes"}
                else inherited_workspace
            )
            if key in {"workspace", "workspaces"} and isinstance(child, dict):
                walk(
                    child,
                    _text(child.get("label")) or _text(child.get("name")) or label,
                )
            else:
                walk(child, child_workspace)

    walk(value, workspace)
    return found


def make_herdr_collector(
    *, runner: Callable[[list[str]], str] | None = None, timeout: float = 3.0
) -> Callable[[], SourceResult]:
    """Create a Herdr collector with an independent subprocess deadline."""

    def collect() -> SourceResult:
        try:
            output = (
                runner(["herdr", "api", "snapshot"])
                if runner is not None
                else _default_runner(["herdr", "api", "snapshot"], timeout=timeout)
            )
        except FileNotFoundError:
            return SourceResult("herdr", "unavailable", error="Herdr unavailable")
        except subprocess.TimeoutExpired:
            return SourceResult(
                "herdr", "unavailable", error="Herdr snapshot timed out"
            )
        except OSError:
            return SourceResult(
                "herdr", "unavailable", error="Herdr snapshot unavailable"
            )
        try:
            payload = json.loads(output)
            snapshot = payload["result"]["snapshot"]
            items = _collect_agents(snapshot)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return SourceResult("herdr", "unavailable", error="invalid Herdr snapshot")
        safe_items = redact_value(items)
        return SourceResult("herdr", "ok" if safe_items else "empty", safe_items)

    return collect
