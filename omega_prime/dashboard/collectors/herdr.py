# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Collect a bounded, read-only Herdr pane snapshot."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
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


_AGENT_STATES = {"idle", "working", "blocked", "done"}
_SHELLS = {"sh", "bash", "zsh", "fish", "dash"}


def _command_label(proc: dict[str, Any]) -> str:
    """Short, non-secret label for a foreground process: name plus module."""
    argv = proc.get("argv")
    parts = [str(a) for a in argv] if isinstance(argv, list) else []
    name = str(proc.get("name") or (parts[0] if parts else "process"))
    if "-m" in parts and parts.index("-m") + 1 < len(parts):
        return f"{name} -m {parts[parts.index('-m') + 1]}"[:80]
    script = next(
        (
            a
            for a in parts[1:]
            if (not a.startswith("-") and "/" in a) or a.endswith(".sh")
        ),
        None,
    )
    return f"{name} {script.rsplit('/', 1)[-1]}"[:80] if script else name[:80]


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
                }
                | (
                    {"label": _text(node.get("label"))}
                    if _text(node.get("label")) and not _text(node.get("name"))
                    else {}
                )
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


def _process_info(
    pane_id: str, runner: Callable[[list[str]], str] | None, timeout: float
) -> tuple[list[dict[str, Any]], Any] | None:
    """Foreground processes and shell pid for one pane, or None on failure."""
    argv = ["herdr", "pane", "process-info", "--pane", pane_id]
    try:
        raw = (
            runner(argv)
            if runner is not None
            else _default_runner(argv, timeout=timeout)
        )
        info = json.loads(raw)["result"]["process_info"]
        procs = [
            proc
            for proc in info.get("foreground_processes") or []
            if isinstance(proc, dict)
        ]
        return procs, info.get("shell_pid")
    except Exception:
        return None


def make_herdr_collector(
    *,
    runner: Callable[[list[str]], str] | None = None,
    timeout: float = 3.0,
    lookup_budget: float = 4.0,
) -> Callable[[], SourceResult]:
    """Create a Herdr collector with an independent subprocess deadline."""

    def collect() -> SourceResult:
        started = time.monotonic()
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
        shell_panes = [
            item for item in items if item.get("agent_status") not in _AGENT_STATES
        ]
        if shell_panes:
            # Plain shell panes: ask Herdr what runs in the foreground so the
            # card can say working or idle. Lookups run concurrently under one
            # shared deadline; a slow or failed lookup degrades only its pane.
            remaining = max(0.1, lookup_budget - (time.monotonic() - started))
            pool = ThreadPoolExecutor(max_workers=min(8, len(shell_panes)))
            futures = {
                pool.submit(
                    _process_info, item["pane_id"], runner, min(timeout, remaining)
                ): item
                for item in shell_panes
            }
            done, _ = wait(futures, timeout=remaining)
            pool.shutdown(wait=False, cancel_futures=True)
            for future, item in futures.items():
                info = future.result() if future in done else None
                if info is None:
                    continue
                procs, shell_pid = info
                job = [
                    proc
                    for proc in procs
                    if proc.get("pid") != shell_pid and proc.get("name") not in _SHELLS
                ]
                item["foreground"] = _command_label(job[0]) if job else None
                item["has_job"] = bool(job)
        safe_items = redact_value(items)
        return SourceResult("herdr", "ok" if safe_items else "empty", safe_items)

    return collect
