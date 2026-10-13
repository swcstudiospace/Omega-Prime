# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Fail-closed OpenShell sandbox executor (Phase 83, SAND-02).

Runs an argv inside an OpenShell sandbox created from the generated
policy file. The fail-closed rule (plan-05 fixed): when a sandbox was
requested but the binary is unrunnable, the policy is unreadable, or
sandbox creation fails, the call raises :class:`SandboxUnavailable`
naming the cause and the argv is NEVER executed outside the sandbox —
no unsandboxed fallback path exists in this module.

The subprocess call goes through a constructor-injected transport
(defaulting to :func:`default_transport`), so tests exercise the full
matrix hermetically without the real binary.
"""

import shutil
import subprocess
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omega_prime.receipts import command_digest
from omega_prime.sandbox.policy_gen import digest_file

#: Tail length matching gates_run's truncation so downstream parsing is stable.
OUTPUT_TAIL = 2500

Transport = Callable[..., dict[str, Any]]


class SandboxUnavailable(Exception):
    """A requested sandbox run failed closed. ``cause`` names why."""

    def __init__(self, cause: str) -> None:
        self.cause = cause
        super().__init__(f"sandbox_unavailable: {cause}")


@dataclass
class SandboxResult:
    """One sandboxed attempt: argv, exit, output, and receipt binding."""

    argv: list[str]
    argv_sha256: str
    exit_code: int
    output: str
    policy_sha256: str
    degraded: str | None = None


def default_transport(
    argv: list[str], timeout: float | int = 120, cwd: str | None = None
) -> dict[str, Any]:
    """Run argv directly (no shell) and capture exit + output.

    Startup failures (missing binary, timeout before exec) are marked
    with ``setup_error`` so :meth:`SandboxRunner.run` raises
    :class:`SandboxUnavailable` instead of recording an exit-127 result
    as executed evidence.
    """
    try:
        run = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "exit_code": 127,
            "stdout": "",
            "stderr": str(exc),
            "setup_error": f"transport startup failed: {exc}",
        }
    return {"exit_code": run.returncode, "stdout": run.stdout, "stderr": run.stderr}


def probe_openshell() -> tuple[bool, str | None]:
    """Check for a runnable openshell binary. Gate on runnable, not version."""
    exe = shutil.which("openshell")
    if exe is None:
        return False, "binary-missing: openshell not on PATH"
    try:
        run = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=10
        )
    except FileNotFoundError:
        return False, "binary-missing: openshell not on PATH"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"binary-unrunnable: openshell --version failed: {exc}"
    if run.returncode != 0:
        return False, "binary-unrunnable: openshell --version exited non-zero"
    return True, None


def build_create_argv(
    policy_path: str | Path, name: str, argv: Sequence[str]
) -> list[str]:
    """Sandbox-create argv for one gate command (in-tree usage shape)."""
    return [
        "openshell",
        "sandbox",
        "create",
        "--name",
        name,
        "--policy",
        str(policy_path),
        "--no-auto-providers",
        "--",
        *[str(part) for part in argv],
    ]


class SandboxRunner:
    """Execute argv inside OpenShell sandboxes; fail closed otherwise."""

    def __init__(
        self,
        policy_path: str | Path,
        *,
        transport: Transport | None = None,
        probe: Callable[[], tuple[bool, str | None]] | None = None,
        name_prefix: str = "omega-prime-gate",
    ) -> None:
        self.policy_path = Path(policy_path)
        self.transport: Transport = transport or default_transport
        self._probe = probe
        self.name_prefix = name_prefix

    def available(self) -> tuple[bool, str | None]:
        """Probe result: (True, None) or (False, cause).

        The default transport gets the real binary probe. An injected
        transport fully emulates the binary, so its ``--version`` verdict
        is the presence verdict — stub tests never touch the ambient PATH.
        """
        if self._probe is not None:
            return self._probe()
        if self.transport is default_transport:
            return probe_openshell()
        try:
            result = self.transport(["openshell", "--version"], 10, None)
        except FileNotFoundError:
            return False, "binary-missing: openshell not on PATH"
        except Exception as exc:
            return False, f"binary-unrunnable: openshell --version failed: {exc}"
        if not isinstance(result, dict) or result.get("exit_code") != 0:
            return False, "binary-unrunnable: openshell --version exited non-zero"
        return True, None

    def run(
        self,
        argv: list[str],
        timeout: float | int = 120,
        cwd: str | None = None,
    ) -> SandboxResult:
        """Run argv inside a fresh sandbox. Fail closed, never fall back."""
        ok, cause = self.available()
        if not ok:
            raise SandboxUnavailable(str(cause))
        try:
            policy_sha256 = digest_file(self.policy_path)
        except OSError as exc:
            raise SandboxUnavailable(f"policy-unreadable: {exc}") from exc
        name = f"{self.name_prefix}-{uuid.uuid4().hex[:12]}"
        create_argv = build_create_argv(self.policy_path, name, argv)
        try:
            result = self.transport(create_argv, timeout, cwd)
        except Exception as exc:
            raise SandboxUnavailable(f"create-failed: {exc}") from exc
        if not isinstance(result, dict):
            raise SandboxUnavailable("create-failed: transport returned no result")
        setup_error = result.get("setup_error")
        if isinstance(setup_error, str) and setup_error.strip():
            # Startup failure, not an execution: degrade explicitly instead
            # of recording exit 127 as executed evidence with the argv.
            raise SandboxUnavailable(f"create-failed: {setup_error.strip()}")
        exit_code = result.get("exit_code", 127)
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            exit_code = 127
        output = ((result.get("stdout") or "") + (result.get("stderr") or ""))[
            -OUTPUT_TAIL:
        ]
        record = list(argv)
        return SandboxResult(
            argv=record,
            argv_sha256=command_digest(record),
            exit_code=exit_code,
            output=output,
            policy_sha256=policy_sha256,
        )


__all__ = [
    "OUTPUT_TAIL",
    "SandboxResult",
    "SandboxRunner",
    "SandboxUnavailable",
    "build_create_argv",
    "default_transport",
    "probe_openshell",
]
