"""Foreground and background argv jobs with the security screen wired in.

Ports the ``packages/coding-agent/src/exec`` job shape (argv execution with
a result) plus job control: start returns an id, and the job can be polled,
waited on, or killed. Every argv passes
:func:`omega_prime.agent.security.screen_argv` first — a refusal starts nothing.
Every started job is reaped exactly once; results are cached so a second
``wait`` after exit never touches the process.
"""

from __future__ import annotations

import contextlib
import math
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from omega_prime.agent.security import screen_argv


class JobControl:
    """Spawn, poll, wait, and kill child processes under one cwd."""

    def __init__(self, cwd: str | Path) -> None:
        self.cwd = str(cwd)
        self._lock = threading.Lock()
        self._next = 0
        self._jobs: dict[str, dict[str, Any]] = {}

    def start(self, argv: list[str]) -> str:
        """Spawn ``argv``. Return the process-local job id."""
        verdict = screen_argv(argv)
        if not verdict.get("ok"):
            raise ValueError(f"job refused: {verdict.get('reason')}")
        child = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            shell=False,
            start_new_session=os.name == "posix",
        )
        job: dict[str, Any] = {
            "child": child,
            "lock": threading.Lock(),
            "result": None,
            "stdout": bytearray(),
            "stderr": bytearray(),
            "readers": [],
        }
        for name in ("stdout", "stderr"):
            stream = getattr(child, name)
            if stream is not None:
                reader = threading.Thread(
                    target=_drain,
                    args=(stream, job[name]),
                    daemon=True,
                )
                job["readers"].append(reader)
                reader.start()
        with self._lock:
            self._next += 1
            job_id = f"job-{self._next}"
            self._jobs[job_id] = job
        return job_id

    def poll(self, job_id: str) -> dict:
        """Return status without waiting for a running job."""
        job = self._job(job_id)
        with job["lock"]:
            if job["result"] is not None:
                return {"id": job_id, **job["result"]}
            child = job["child"]
            if child.poll() is None:
                return {"id": job_id, "running": True}
            return self._collect(job_id, job, timeout=0)

    def wait(self, job_id: str, timeout: float = 1.0) -> dict:
        """Wait up to ``timeout`` seconds; a timeout leaves the process running."""
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
            or timeout > 30
        ):
            raise ValueError("timeout must be greater than 0 and no greater than 30")
        job = self._job(job_id)
        with job["lock"]:
            if job["result"] is not None:
                return {"id": job_id, **job["result"]}
            try:
                return self._collect(job_id, job, timeout=float(timeout))
            except subprocess.TimeoutExpired:
                return {"id": job_id, "running": True}

    def kill(self, job_id: str) -> dict:
        """Terminate, escalate to kill, and cache the final status."""
        job = self._job(job_id)
        with job["lock"]:
            if job["result"] is not None:
                return {"id": job_id, **job["result"]}
            child = job["child"]
            _signal_job(child, signal.SIGTERM)
            try:
                return self._collect(job_id, job, timeout=5)
            except subprocess.TimeoutExpired:
                _signal_job(child, signal.SIGKILL)
                return self._collect(job_id, job, timeout=5)

    def shutdown(self) -> None:
        """Terminate and reap every child still owned by this controller."""
        with self._lock:
            job_ids = tuple(self._jobs)
        for job_id in job_ids:
            try:
                self.kill(job_id)
            except Exception:
                # Keep shutdown progressing, but make a best-effort hard cleanup
                # of this job in case termination or collection only partly ran.
                try:
                    job = self._job(job_id)
                    _signal_job(job["child"], signal.SIGKILL)
                    with job["lock"]:
                        if job["result"] is None:
                            self._collect(job_id, job, timeout=5)
                except Exception:
                    continue

    def _job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"unknown job: {job_id}")
        return job

    def _collect(self, job_id: str, job: dict[str, Any], timeout: float) -> dict:
        child = job["child"]
        started = time.monotonic()
        child.wait(timeout=timeout)
        for reader in job["readers"]:
            remaining = max(0.0, timeout - (time.monotonic() - started))
            reader.join(timeout=remaining)
            if reader.is_alive():
                if timeout > 0:
                    raise subprocess.TimeoutExpired(child.args, timeout)
                return {"id": job_id, "running": True}
        result = {
            "running": False,
            "exit_code": child.returncode,
            "stdout": _text(job["stdout"]),
            "stderr": _text(job["stderr"]),
        }
        # Keep the cached text result, not a second copy of raw output bytes.
        job["stdout"].clear()
        job["stderr"].clear()
        job["result"] = result
        return {"id": job_id, **result}


def _drain(stream: Any, output: bytearray) -> None:
    """Continuously drain a child pipe while retaining only bounded output."""
    try:
        while True:
            chunk = os.read(stream.fileno(), 64 * 1024)
            if not chunk:
                return
            remaining = _MAX_OUTPUT_BYTES - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])
    finally:
        stream.close()


_MAX_OUTPUT_BYTES = 1024 * 1024


def _signal_job(child: subprocess.Popen, signum: int) -> None:
    """Signal the process group so descendants cannot retain the job's pipes."""
    if os.name == "posix":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signum)
    elif child.poll() is None:
        if signum == signal.SIGTERM:
            child.terminate()
        else:
            child.kill()


def register_exec_job_tools(registry: Any, cwd: str | Path) -> JobControl:
    """Register approval-gated process controls and return their owner."""
    jobs = JobControl(cwd)
    registry.register(
        "exec_job_start",
        "Start a screened argv process in the configured work directory.",
        {
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["argv"],
        },
        lambda argv: {"id": jobs.start(argv)},
        requires_approval=True,
    )
    registry.register(
        "exec_job_poll",
        "Check whether a process-local argv job has finished.",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        jobs.poll,
        requires_approval=True,
    )
    registry.register(
        "exec_job_wait",
        "Wait up to 30 seconds for a process-local argv job.",
        {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "timeout": {"type": "number", "default": 1.0},
            },
            "required": ["job_id"],
        },
        lambda job_id, timeout=1.0: jobs.wait(job_id, timeout),
        requires_approval=True,
    )
    registry.register(
        "exec_job_kill",
        "Terminate a process-local argv job.",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        jobs.kill,
        requires_approval=True,
    )
    return jobs


def _text(data: bytes | bytearray | None) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


__all__ = ["JobControl", "register_exec_job_tools"]
