"""Canonical MCP client: the initialized SDK session is the only transport.

The public roster ``mcp_call`` binds :func:`mcp_session_call`, which spawns
``command`` in ``cwd``, runs the official SDK ``initialize()`` handshake plus
``call_tool`` over stdio, and returns ``{"result": ...}`` with JSON-safe
content blocks. A tool-level error returns both ``error`` and ``result``.
Refusals, spawn failures, and timeouts return ``error`` only.

Safety contract: ``command`` is an argv list, a string is refused and starts
nothing, and on timeout the child process tree is reaped. The synchronous
entry works both outside and inside an already-running event loop: without a
running loop it keeps the direct ``asyncio.run`` path; inside an active loop
the SDK coroutine runs on a joined non-daemon helper thread that owns its
loop, and the caller waits through the bounded SDK cleanup (never detached,
never an early join-timeout return while the child lives).

The child inherits only the SDK default restricted passthrough plus safe
explicit isolated additions (owned TMPDIR/XDG roots and a bytecode policy);
the calling process environment, provider credentials, and endpoints are
never blanket-inherited. Child stderr goes to a privately opened real-fileno
devnull sink (never StringIO, never read back, bounded by the kernel) and
SDK protocol diagnostics are contained for the duration
of the call. Refusals, spawn failures, timeouts, and transport exceptions
carry fixed safe labels only; successful results and tool-level error
payloads remain server-controlled untrusted content and must be treated
as untrusted downstream.
Sampling, elicitation, and roots callbacks keep their default denial: no
automatic model or credential callbacks are registered.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

#: SDK logger subtree contained (buffered, then discarded) per session call so
#: malformed-protocol diagnostics cannot leak peer bytes into host logs.
_SDK_LOGGER_NAME = "mcp"

#: Upper bound on buffered SDK log records per call; the buffer is discarded.
_LOG_RECORD_BOUND = 32

#: Shared ownership of the process-wide ``propagate`` change on the SDK logger:
#: overlapping calls (e.g. a background desk turn beside a foreground request)
#: may exit in any order, so only the last exit restores the original value.
_CONTAINMENT_LOCK = threading.Lock()
_containment_depth = 0
_original_propagate = True

#: TMPDIR-style keys pointed at the owned child temp dir.
_TMP_ENV_KEYS = ("TMPDIR", "TEMP", "TMP")

#: XDG keys pointed at owned subdirs of the child env root, with their leaf.
_XDG_ENV_DIRS = (
    ("XDG_CACHE_HOME", "cache"),
    ("XDG_CONFIG_HOME", "config"),
    ("XDG_DATA_HOME", "data"),
    ("XDG_STATE_HOME", "state"),
    ("XDG_RUNTIME_DIR", "run"),
)


def mcp_session_call(
    command: Any,
    tool: Any,
    arguments: Any,
    *,
    cwd: str | Path,
    timeout: float = 5,
) -> dict[str, Any]:
    """Spawn ``command`` in ``cwd`` and call ``tool`` over an SDK session.

    Opens a stdio session, runs ``initialize()`` plus ``call_tool``, and
    returns ``{"result": ...}`` with JSON-safe content blocks. A tool-level
    error returns both ``error`` and ``result``. Refusals, spawn failures,
    and timeouts return ``error`` only.
    """
    refusal = _validate(command, tool, arguments, cwd, timeout)
    if refusal is not None:
        return {"error": refusal}
    assert isinstance(command, list)
    root = Path(cwd)
    try:
        return _run_bridge(command, tool, arguments, root, timeout)
    except TimeoutError:
        return {"error": f"timed out after {timeout} seconds"}
    except OSError as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:
        # Fixed safe label: exception text may carry peer bytes, so only the
        # type name is surfaced. Child stderr and SDK diagnostics stay in
        # their private sinks and are discarded with the call.
        return {"error": f"MCP session failed: {type(exc).__name__}"}


def _validate(
    command: Any, tool: Any, arguments: Any, cwd: Any, timeout: Any
) -> str | None:
    if isinstance(command, str):
        return "command must be an argv list, not a string"
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or part == "" for part in command)
    ):
        return "command must be a non-empty argv list of strings"
    if not isinstance(tool, str) or tool == "":
        return "tool must be a non-empty string"
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
    ):
        return "timeout must be a positive number of seconds"
    if not Path(cwd).is_dir():
        return f"cwd is not a directory: {cwd}"
    return None


def _run_bridge(
    command: list[str],
    tool: str,
    arguments: dict,
    cwd: Path,
    timeout: float,
) -> dict[str, Any]:
    """Run the SDK coroutine with or without an already-running loop.

    No running loop: direct ``asyncio.run`` exactly as before. Inside an
    active loop ``asyncio.run`` would raise, so the coroutine moves to a
    helper thread that owns its loop. The thread is non-daemon and joined
    with no timeout: the SDK call itself is bounded by ``timeout`` plus the
    SDK's bounded shutdown escalation, and returning early would detach a
    live child. Exceptions (including the timeout) are re-raised here so the
    caller keeps the unchanged error mapping.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_call(command, tool, arguments, cwd, timeout))
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["value"] = asyncio.run(_call(command, tool, arguments, cwd, timeout))
        except BaseException as exc:  # re-raised by the joining thread below
            box["raised"] = exc

    worker = threading.Thread(
        target=_target, name="omega-prime-mcp-session", daemon=False
    )
    worker.start()
    worker.join()
    if "raised" in box:
        raise box["raised"]
    return box["value"]


async def _call(
    command: list[str], tool: str, arguments: dict, cwd: Path, timeout: float
) -> dict[str, Any]:
    async def _session() -> dict[str, Any]:
        params = StdioServerParameters(
            command=command[0],
            args=command[1:],
            cwd=str(cwd),
            env=_restricted_env(cwd),
        )
        # No sampling/elicitation/roots callbacks: the SDK default denial
        # stands, so the server can never pull model or credential work.
        with _contained_sdk_logging(), _private_errlog() as errlog:
            async with (
                stdio_client(params, errlog=errlog) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool(tool, arguments)
        return _shape(result)

    try:
        return await asyncio.wait_for(_session(), timeout)
    except TimeoutError:
        raise TimeoutError from None


def _restricted_env(cwd: Path) -> dict[str, str]:
    """Safe explicit additions merged over the SDK default passthrough.

    The SDK already restricts inheritance to its small default set
    (HOME/LOGNAME/PATH/SHELL/TERM/USER on POSIX); this adds only an owned
    temp dir, owned XDG roots, and a no-bytecode policy. The calling
    ``os.environ`` is never copied, so provider credentials, endpoints, and
    config selectors cannot reach the child.
    """
    root = cwd / ".mcp-child"
    tmp = root / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = {"PYTHONDONTWRITEBYTECODE": "1"}
    for key in _TMP_ENV_KEYS:
        env[key] = str(tmp)
    for key, leaf in _XDG_ENV_DIRS:
        path = root / leaf
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    return env


class _DiscardBuffer(logging.Handler):
    """Bounded in-memory sink for contained SDK log records."""

    def __init__(self, records: list[str]) -> None:
        super().__init__()
        self._records = records

    def emit(self, record: logging.LogRecord) -> None:
        if len(self._records) >= _LOG_RECORD_BOUND:
            return
        with contextlib.suppress(Exception):
            self._records.append(self.format(record)[-2048:])


@contextlib.contextmanager
def _contained_sdk_logging() -> Iterator[None]:
    """Buffer-then-discard SDK diagnostics for the duration of one call.

    Malformed-protocol lines are reported by the SDK through logging, and the
    formatted records can carry peer bytes. While a session runs, records
    under the ``mcp`` logger go to a bounded private buffer with propagation
    disabled so they never reach host handlers; the buffer is cleared on
    exit. Nothing buffered is ever attached to results.
    """
    global _containment_depth, _original_propagate
    logger = logging.getLogger(_SDK_LOGGER_NAME)
    records: list[str] = []
    handler = _DiscardBuffer(records)
    with _CONTAINMENT_LOCK:
        if _containment_depth == 0:
            _original_propagate = logger.propagate
            logger.propagate = False
        _containment_depth += 1
        logger.addHandler(handler)
    try:
        yield
    finally:
        with _CONTAINMENT_LOCK:
            logger.removeHandler(handler)
            _containment_depth -= 1
            if _containment_depth == 0:
                logger.propagate = _original_propagate
        records.clear()


@contextlib.contextmanager
def _private_errlog() -> Iterator[TextIO]:
    """Yield a privately opened real-fileno devnull sink for child stderr.

    ``os.devnull`` opened per call: a genuine file descriptor the SDK
    subprocess spawn accepts (StringIO has none and is never used here),
    discarded by the kernel so a hostile peer cannot grow disk unbounded,
    and never read back into results. The sink is evidence containment,
    not a data source.
    """
    with open(os.devnull, mode="w", encoding="utf-8", errors="replace") as tmp:
        yield tmp


def _shape(result: Any) -> dict[str, Any]:
    dump = result.model_dump(mode="json", exclude_none=True)
    failed = dump.get("is_error", dump.get("isError", False))
    if not failed:
        return {"result": dump}
    texts = [
        block.get("text")
        for block in dump.get("content", [])
        if isinstance(block, dict) and block.get("text")
    ]
    error = texts[0] if texts else "MCP tool reported an error"
    return {"error": error, "result": dump}


__all__ = ["mcp_session_call"]
