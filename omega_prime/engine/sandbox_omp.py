# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Anda stdio client for the omp-sandbox engine backend.

The backend is selected only by ``OMEGA_PRIME_OMP_BACKEND=omp-sandbox``
(``selected``). One ``omp-sandbox-anda client`` wrapper process per run
speaks newline JSON on stdio: requests are ``{"id","tool","args"}`` lines,
replies are ``{"id","ok",...}`` (``ok:false`` carries ``error`` + ``code``),
unsolicited ``{"event", ...}`` lines interleave with replies, the first
line is ``{"event":"wrapper_ready", ...}``, and the ``down`` reply lands
before the wrapper exits (client.rs drains in-flight replies first).

Every wrapper failure — missing binary, non-zero exit, protocol garbage,
EOF mid-run, or any ``ok:false`` reply including ``path_refused`` and tier
errors — raises ``SandboxUnavailable``. There is no bwrap fallback
(GRACEFUL_DEGRADATION): an omp-sandbox outage is a sandbox failure, never
a silent switch of sandbox.

``poll_events`` is wrapper-internal (its event pump) and is never sent by
this client. Bounded waits cover only the ``wrapper_ready`` handshake and
individual tool replies; the stage wait itself has no deadline — liveness
is the wrapper's event stream, and a set interrupt aborts the stage
explicitly. This module is stdlib-only and never imports from
``omp-sandbox``.
"""

from __future__ import annotations

import contextlib
import json
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omega_prime.engine.sandbox import SandboxUnavailable

BACKEND_ENV = "OMEGA_PRIME_OMP_BACKEND"
BACKEND_NAME = "omp-sandbox"
ANDA_BIN_ENV = "OMEGA_PRIME_OMP_SANDBOX_ANDA"
WORKSPACE_ROOTS_ENV = "OMP_SANDBOX_WORKSPACE_ROOTS"
# Same default as omp-sandbox/src/core/paths.ts WORKSPACE_ROOTS.
DEFAULT_WORKSPACE_ROOTS = "/workspace/omes-bot-wt"

# Bounded waits, sized like omp_driver's _READY_TIMEOUT/_COMMAND_TIMEOUT
# with head-room for `up` (auth broker, sandbox tier, agentOS VM, GSD-Core
# — the core's own internal bounds run to 300s). Only the handshake and
# per-tool replies are bounded; the stage wait never times out.
_WRAPPER_READY_TIMEOUT = 30.0
_TOOL_TIMEOUT = 300.0
# The wrapper exits shortly after replying to `down`; grace before terminate.
_EXIT_GRACE = 10.0
# Wake-up tick for waits that must also notice an interrupt.
_POLL_TICK = 0.2

_EOF = object()


def anda_binary(env: Mapping[str, str]) -> str | None:
    """The wrapper binary: an explicit ``OMEGA_PRIME_OMP_SANDBOX_ANDA`` file
    or ``omp-sandbox-anda`` from PATH. ``None`` when neither resolves;
    never raises."""
    explicit = str(env.get(ANDA_BIN_ENV, "")).strip()
    if explicit:
        path = Path(explicit)
        try:
            if path.is_file() and path.stat().st_mode & 0o111 != 0:
                return str(path)
        except OSError:
            return None
        return None
    return shutil.which("omp-sandbox-anda", path=env.get("PATH") or None)


class AndaClient:
    """One ``omp-sandbox-anda client`` process and its stdio session.

    A daemon reader thread parses each stdout line as JSON onto a queue
    (EOF, a decode failure, or a non-object line puts a sentinel). Replies
    are matched strictly by the echoed ``id``; unsolicited ``{"event",...}``
    lines are buffered for ``drain_events()`` and are never mistaken for
    replies. Every failure mode — bad first line, garbage, EOF, non-zero
    exit, ``ok:false`` — raises ``SandboxUnavailable`` with the error and
    code in the detail. The client tolerates the ``attestation`` stamp on
    every line, never interprets it, and never writes received text
    anywhere.
    """

    def __init__(self, binary: str, env: Mapping[str, str]) -> None:
        self.binary = binary
        self.env = dict(env)
        self.proc: subprocess.Popen[str] | None = None
        self.events: list[dict[str, Any]] = []
        self._queue: queue.Queue[Any] = queue.Queue()
        self._reader_error: BaseException | None = None
        self._stderr_parts: list[str] = []
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._next_id = 0

    def start(self) -> None:
        """Spawn ``<binary> client`` and start the reader threads."""
        self.proc = subprocess.Popen(
            [self.binary, "client"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=self.env,
        )
        self._stdout_thread = threading.Thread(target=self._pump_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._pump_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def wait_ready(self, timeout: float | None = None) -> dict[str, Any]:
        """Consume the first protocol line; it must announce ``wrapper_ready``.

        Any other first line, a decode failure, EOF, or a wrapper that
        already exited non-zero is a SandboxUnavailable.
        """
        bound = _WRAPPER_READY_TIMEOUT if timeout is None else timeout
        deadline = time.monotonic() + bound
        first = self._take(deadline, "the wrapper_ready handshake")
        if first.get("event") != "wrapper_ready":
            raise SandboxUnavailable(
                "anda wrapper's first protocol line was not wrapper_ready"
            )
        return first

    def call(
        self,
        tool: str,
        args: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send one tool request and return its ``result`` (``ok:true``).

        Interleaved event lines are buffered; the first reply whose echoed
        ``id`` matches wins. ``ok:false`` raises SandboxUnavailable with
        ``"<tool> failed: <error> (code <code>)"``; a bounded-wait timeout,
        EOF, or a dead wrapper raises SandboxUnavailable naming the tool.
        """
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise SandboxUnavailable(
                f"{tool} was not sent: the anda wrapper is not running"
            )
        self._next_id += 1
        request_id = str(self._next_id)
        payload = json.dumps(
            {"id": request_id, "tool": tool, "args": dict(args or {})},
            ensure_ascii=False,
        )
        try:
            proc.stdin.write(payload + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise SandboxUnavailable(
                f"{tool} was not sent: {type(exc).__name__}"
            ) from exc
        bound = _TOOL_TIMEOUT if timeout is None else timeout
        deadline = time.monotonic() + bound
        while True:
            message = self._take(deadline, f"the {tool} reply")
            if "event" in message and "id" not in message:
                self.events.append(message)
                continue
            if str(message.get("id") or "") == request_id:
                if message.get("ok") is not True:
                    raise SandboxUnavailable(
                        f"{tool} failed: {message.get('error')} "
                        f"(code {message.get('code')})"
                    )
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            # A reply for an id we never sent is ignored rather than guessed
            # at (replies are only meaningful while their call is in flight).
            continue

    def next_event(self, tick: float) -> dict[str, Any] | None:
        """Next unsolicited event dict, blocking up to ``tick`` seconds.

        Returns ``None`` when the tick elapsed without an event — the
        caller decides what a quiet tick means (interrupt checks live
        there). EOF, protocol garbage, or a dead wrapper raises
        SandboxUnavailable, so a stage wait can never hang on a dead
        wrapper; it only ever stops on events, EOF, or an explicit abort.
        """
        deadline = time.monotonic() + max(tick, 0.0)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                message = self._queue.get(timeout=min(_POLL_TICK, remaining))
            except queue.Empty:
                continue
            if message is _EOF:
                self._raise_stream_failure()
            if "event" in message and "id" not in message:
                self.events.append(message)
                return message
            # Stray non-event lines (a reply outside any in-flight call) are
            # ignored rather than guessed at.
            continue

    def drain_events(self) -> list[dict[str, Any]]:
        """Hand over the buffered event dicts and empty the buffer."""
        drained, self.events = self.events, []
        return drained

    def down(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Send ``down``, then close stdin and wait for the wrapper to exit.

        The wrapper replies to ``down`` and exits afterwards, so the reply
        is the contract; a missing reply or a dead process is tolerated
        (errors are swallowed once the attempt is recorded) and the
        bounded exit wait plus terminate fallback finish the job. Returns
        the down result dict, or None when no reply arrived.
        """
        reply: dict[str, Any] | None = None
        try:
            reply = self.call("down", {}, timeout)
        except SandboxUnavailable:
            reply = None
        self._shutdown()
        return reply

    def close(self) -> None:
        """Kill a still-live wrapper without raising."""
        proc = self.proc
        if proc is None:
            return
        with contextlib.suppress(Exception):
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)

    def stderr_tail(self) -> str:
        return "".join(self._stderr_parts)[-2000:]

    def _shutdown(self) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.stdin is not None:
            with contextlib.suppress(OSError, ValueError):
                proc.stdin.close()
        try:
            proc.wait(timeout=_EXIT_GRACE)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(Exception):
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception):
                    proc.kill()

    def _take(self, deadline: float, what: str) -> dict[str, Any]:
        """Next protocol dict, bounded by ``deadline``; sentinel → failure."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SandboxUnavailable(f"timed out waiting for {what}")
            try:
                message = self._queue.get(timeout=min(_POLL_TICK, remaining))
            except queue.Empty:
                continue
            if message is _EOF:
                self._raise_stream_failure()
            return message

    def _raise_stream_failure(self) -> None:
        if self._reader_error is not None:
            raise SandboxUnavailable(
                "anda wrapper stdout reader failed: "
                f"{type(self._reader_error).__name__}"
            ) from self._reader_error
        status = self.proc.poll() if self.proc is not None else None
        if status is not None and status != 0:
            raise SandboxUnavailable(f"anda wrapper exited with status {status}")
        raise SandboxUnavailable(
            "anda wrapper stdout closed before the protocol completed"
        )

    def _pump_stdout(self) -> None:
        stream = self.proc.stdout if self.proc is not None else None
        if stream is None:
            self._queue.put(_EOF)
            return
        try:
            for line in stream:
                text = line.strip()
                if not text:
                    continue
                try:
                    parsed = json.loads(text)
                except ValueError:
                    self._queue.put(_EOF)
                    return
                if not isinstance(parsed, dict):
                    self._queue.put(_EOF)
                    return
                self._queue.put(parsed)
        except BaseException as exc:
            self._reader_error = exc
        finally:
            self._queue.put(_EOF)

    def _pump_stderr(self) -> None:
        stream = self.proc.stderr if self.proc is not None else None
        if stream is None:
            return
        try:
            for line in stream:
                if sum(len(part) for part in self._stderr_parts) < 8000:
                    self._stderr_parts.append(line)
        except (OSError, ValueError):
            pass
