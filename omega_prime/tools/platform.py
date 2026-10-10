"""Register the local platform tools on one registry.

``execute_code`` and ``mcp_call`` close over ``home``. The browser tools close
over ``browser`` and do not open a socket when it is missing. ``mcp_call``
keeps its roster name and argv/timeout contract while delegating to the
canonical initialized SDK session client (initialize handshake plus
tools/call over stdio); the obsolete uninitialized one-line client is gone.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from omega_prime.receipts import append_execution
from omega_prime.tools.browser import BrowserSession
from omega_prime.tools.execute import execute_code as run_execute_code
from omega_prime.tools.mcp_session import mcp_session_call as run_mcp_call
from omega_prime.tools.registry import ToolRegistry

# Offered after delegate_task. omega_prime/contracts/tool-rosters/omega-prime.yaml lists these.
PLATFORM_TOOL_NAMES = (
    "execute_code",
    "mcp_call",
    "browser_navigate",
    "browser_snapshot",
)


def register_platform_tools(
    registry: ToolRegistry,
    *,
    home: str | Path,
    browser: BrowserSession | None = None,
) -> list[str]:
    """Register the four platform tools. The model does not send ``home``."""

    def execute_code(code: str, timeout: float = 5) -> dict[str, Any]:
        result = run_execute_code(code, home=home, timeout=timeout)
        exit_code = result.get("exit_code")
        error = result.get("error")
        if (
            isinstance(code, str)
            and isinstance(exit_code, int)
            and not isinstance(exit_code, bool)
            and (error is None or str(error).startswith("timed out"))
        ):
            output = f"{result.get('stdout') or ''}{result.get('stderr') or ''}"
            append_execution(
                " ".join((sys.executable, "-c", code)),
                exit_code,
                output[-2000:],
                cwd=str(home),
            )
        return result

    def mcp_call(
        command: Any,
        tool: str,
        arguments: dict,
        timeout: float = 5,
    ) -> dict[str, Any]:
        return run_mcp_call(command, tool, arguments, cwd=home, timeout=timeout)

    def browser_navigate(url: str) -> dict[str, Any]:
        if browser is None:
            return {"error": "browser is not available"}
        return browser.browser_navigate(url)

    def browser_snapshot() -> dict[str, Any]:
        if browser is None:
            return {"error": "browser is not available"}
        return browser.browser_snapshot()

    handlers: dict[str, Callable[..., Any]] = {
        "execute_code": execute_code,
        "mcp_call": mcp_call,
        "browser_navigate": browser_navigate,
        "browser_snapshot": browser_snapshot,
    }
    if tuple(handlers) != PLATFORM_TOOL_NAMES:
        raise RuntimeError("platform tool handlers drifted from PLATFORM_TOOL_NAMES")
    for name in PLATFORM_TOOL_NAMES:
        description, parameters = _SCHEMAS[name]
        registry.register(name, description, parameters, handlers[name])
    return list(PLATFORM_TOOL_NAMES)


def _object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _string(description: str) -> dict:
    return {"type": "string", "description": description}


_SCHEMAS: dict[str, tuple[str, dict]] = {
    "execute_code": (
        "Run Python source in a short-lived local process whose working directory is the bot home. "
        "Empty code is refused. A timeout kills the process.",
        _object(
            {
                "code": _string(
                    "Python source. Empty source is an error and starts nothing."
                ),
                "timeout": {
                    "type": "number",
                    "description": "Seconds before the process is killed. Default is 5.",
                },
            },
            ["code"],
        ),
    ),
    "mcp_call": (
        "Call one MCP tool over an initialized SDK session: the server is spawned from an argv, "
        "reached with the initialize handshake plus tools/call over stdio, and reaped on timeout. "
        "command is an argument list, not a shell string.",
        _object(
            {
                "command": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Argv of the MCP server. A string is an error and starts nothing.",
                },
                "tool": _string("Tool name to pass as params.name."),
                "arguments": {
                    "type": "object",
                    "description": "Arguments object passed to the MCP tool.",
                },
                "timeout": {
                    "type": "number",
                    "description": "Seconds before the process is killed. Default is 5.",
                },
            },
            ["command", "tool", "arguments"],
        ),
    ),
    "browser_navigate": (
        "Open a URL through the injected browser transport and return the page text. "
        "No socket is opened. A missing browser is an error.",
        _object(
            {"url": _string("URL the transport should open.")},
            ["url"],
        ),
    ),
    "browser_snapshot": (
        "Return the injected browser transport's current page text. "
        "No socket is opened. A missing browser is an error.",
        _object({}, []),
    ),
}


__all__ = ["PLATFORM_TOOL_NAMES", "register_platform_tools"]
