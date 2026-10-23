"""Fake actions/runner installation with a harmless, self-reporting ``run.sh``."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from omega_prime.ci_runners.agent.supervisor import Supervisor
from omega_prime.tests.ci_runners._secrets import placeholder

JIT = placeholder("jit-config")
AGENT_TOKEN = placeholder("agent-token")

REPORT = """#!/bin/sh
d='{report}'
printf '%s' "$ACTIONS_RUNNER_INPUT_JITCONFIG" > "$d/jit"
printf '%s\\n' "$0" "$@" > "$d/argv"
env | cut -d= -f1 | sort > "$d/keys"
pwd > "$d/cwd"
ulimit -n > "$d/nofile"
nice > "$d/nice"
ls -a > "$d/ls"
{tail}
"""


def runner_root(tmp: Path, tail: str = "exit 3", report: Path | None = None) -> Path:
    root = tmp / "opt-actions-runner"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "Runner.Listener").write_text("binary\n")
    (root / "bin" / "current").symlink_to("Runner.Listener")
    out = report or tmp / "report"
    out.mkdir(exist_ok=True)
    run = root / "run.sh"
    run.write_text(REPORT.format(report=out, tail=tail))
    run.chmod(0o755)
    return root


class Exits:
    """Collects exit callbacks; tests await the next one with a bounded wait."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self._event = asyncio.Event()

    async def __call__(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)
        self._event.set()

    async def wait(self, count: int = 1, timeout: float = 15.0) -> None:
        async def until() -> None:
            while len(self.payloads) < count:
                self._event.clear()
                await self._event.wait()

        await asyncio.wait_for(until(), timeout)


def supervisor(tmp: Path, root: Path, exits: Exits, **kw: Any) -> Supervisor:
    kw.setdefault("max_runners", 2)
    kw.setdefault(
        "base_env",
        {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "PASSWORD": "desktop-password-1234567890",
            "OMEGA_CI_AGENT_TOKEN": AGENT_TOKEN,
            "GITHUB_TOKEN": placeholder("ambient-github-token"),
        },
    )
    return Supervisor(
        runner_root=root, work_root=tmp / "work", notify=exits, term_grace_s=1.0, **kw
    )
