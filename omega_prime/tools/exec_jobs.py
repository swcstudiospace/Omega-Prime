"""Foreground and background argv jobs with the security screen wired in.

Ports the ``packages/coding-agent/src/exec`` job shape (argv execution with
a result) plus job control: start returns an id, and the job can be polled,
waited on, or killed. Every argv passes
:func:`omega_prime.agent.security.screen_argv` first — a refusal starts nothing.
Every started job is reaped exactly once; results are cached so a second
``wait`` after exit never touches the process.
"""

from __future__ import annotations

import math
import subprocess
import threading
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
        )
        with self._lock:
            self._next += 1
            job_id = f"job-{self._next}"
            self._jobs[job_id] = {
                "child": child,
                "lock": threading.Lock(),
                "result": None,
            }
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
            try:
                return self._collect(job_id, job, timeout=0)
            except subprocess.TimeoutExpired:
                return {"id": job_id, "running": True}

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
            if child.poll() is None:
                child.terminate()
                try:
                    return self._collect(job_id, job, timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    return self._collect(job_id, job, timeout=5)
            try:
                return self._collect(job_id, job, timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                return self._collect(job_id, job, timeout=5)

    def shutdown(self) -> None:
        """Terminate and reap every child still owned by this controller."""
        with self._lock:
            job_ids = tuple(self._jobs)
        for job_id in job_ids:
            self.kill(job_id)

    def _job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"unknown job: {job_id}")
        return job

    def _collect(self, job_id: str, job: dict[str, Any], timeout: float) -> dict:
        child = job["child"]
        stdout, stderr = child.communicate(timeout=timeout)
        result = {
            "running": False,
            "exit_code": child.returncode,
            "stdout": _text(stdout),
            "stderr": _text(stderr),
        }
        job["result"] = result
        return {"id": job_id, **result}


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


def _text(data: bytes | None) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


__all__ = ["JobControl", "register_exec_job_tools"]
