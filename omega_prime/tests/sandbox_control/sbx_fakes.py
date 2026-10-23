"""Fakes for the sandbox-control tests (no OpenShell, no Podman)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from omega_prime.sandbox_control.events import MemorySink
from omega_prime.sandbox_control.provider import (
    OpenShellSandboxProvider,
    RunResult,
    SandboxConfig,
)

OPENSHELL = "/fake/openshell"
PODMAN = "/fake/podman"
UUID = "d8eca4fc-95f0-4bec-8c2f-9bd266b012a9"


def sandbox_row(name: str, phase: str = "Ready") -> dict[str, object]:
    # Shape captured from `openshell sandbox get -o json` (0.1.4, podman driver).
    return {
        "conditions": [
            {
                "message": "Supervisor session connected",
                "reason": "DependenciesReady",
                "status": "True",
                "type": "Ready",
            }
        ],
        "created_at": "2026-10-22 14:03:39",
        "exit_code": None,
        "id": UUID,
        "labels": {},
        "name": name,
        "phase": phase,
        "workspace": "default",
    }


@dataclass
class FakeOpenShell:
    """Scriptable stand-in for the openshell + podman CLIs."""

    sandboxes: dict[str, dict[str, object]] = field(default_factory=dict)
    connected: bool = True
    create_rc: int = 0
    exec_result: RunResult = field(default_factory=lambda: RunResult(0, "ok\n", ""))
    phase_on_create: str = "Ready"
    leftover_containers: list[str] = field(default_factory=list)
    calls: list[tuple[list[str], str | None]] = field(default_factory=list)

    def __call__(
        self, argv: Sequence[str], stdin: str | None, timeout: float
    ) -> RunResult:
        args = list(argv)
        self.calls.append((args, stdin))
        if args[0] == PODMAN:
            if args[1] == "ps":
                return RunResult(0, "\n".join(self.leftover_containers), "")
            return RunResult(0, "", "")
        sub = args[1:]
        if sub == ["status"]:
            status = "Connected" if self.connected else "Disconnected"
            return RunResult(
                0, f"Server Status\n\n  Gateway: box-vm\n  Status: {status}\n", ""
            )
        if sub[:2] == ["sandbox", "list"]:
            rows = list(self.sandboxes.values())
            return RunResult(
                0, json.dumps({"next_page_token": "", "sandboxes": rows}), ""
            )
        if sub[:2] == ["sandbox", "get"]:
            name = sub[2]
            if name not in self.sandboxes:
                return RunResult(1, "", 'Error: message: "sandbox not found"')
            return RunResult(0, json.dumps(self.sandboxes[name]), "")
        if sub[:2] == ["sandbox", "create"]:
            if self.create_rc:
                return RunResult(
                    self.create_rc, "", "create exploded ghp_abcdefghijklmnop"
                )
            name = sub[sub.index("--name") + 1]
            self.sandboxes[name] = sandbox_row(name, self.phase_on_create)
            return RunResult(0, f"Created sandbox: {name}\n", "")
        if sub[:2] == ["sandbox", "delete"]:
            self.sandboxes.pop(sub[2], None)
            return RunResult(0, "", "")
        if sub[:2] == ["sandbox", "exec"]:
            return self.exec_result
        return RunResult(2, "", f"unexpected {sub}")


def make_config(tmp_path: Path) -> SandboxConfig:
    policy = tmp_path / "policy-podman.yaml"
    policy.write_text("version: 1\n")
    binary = tmp_path / "openshell"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    return SandboxConfig(
        openshell=str(binary),
        podman=PODMAN,
        image="localhost/omp-sandbox:0.1",
        policy=policy,
        state_dir=tmp_path / "state",
        ready_timeout_s=5,
    )


def make_provider(
    config: SandboxConfig, fake: FakeOpenShell
) -> OpenShellSandboxProvider:
    def runner(argv: Sequence[str], stdin: str | None, timeout: float) -> RunResult:
        args = list(argv)
        if args[0] == config.openshell:
            args[0] = OPENSHELL
        return fake(args, stdin, timeout)

    return OpenShellSandboxProvider(
        config=config, runner=runner, sink=MemorySink(), sleep=lambda _s: None
    )
