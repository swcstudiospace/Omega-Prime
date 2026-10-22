# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Run the interactive omp agent inside a sandbox, in a Herdr pane on the host.

Herdr runs on the box; the pane's process is ``openshell sandbox exec --tty``
that runs the real omp TUI in the sandbox (no ``--mode rpc-ui``). The Command
Center attaches to that pane like any other. Checked live on 2026-10-23
with OpenShell 0.1.4 (Podman driver): ``--tty`` gives the remote process a
PTY (``/dev/pts/N``, 80x24 default, ``TERM`` forwarded) and the exec exit
code is passed through.

Secrets never go on this argv. The pane sources the sandbox env file
(written 0600 over exec stdin by the omp-sandbox tier, which holds the
broker relay URL) before it ``exec``s omp.
"""

from __future__ import annotations

import json
import re
import secrets
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from omega_prime.sandbox_control.provider import (
    RunResult,
    SandboxControlError,
    exec_argv,
    is_owned,
)

ENV_FILE = "/sandbox/home/.omp-sandbox.env"
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/@-]{1,128}$")
THINKING = frozenset({"off", "minimal", "low", "medium", "high", "xhigh"})
PROMPT_RE = r"[$#>%] ?$"

HerdrRunner = Callable[[Sequence[str]], RunResult]


def session_script(tag: str) -> str:
    """POSIX sh body: record the session id, source the broker env, exec omp."""
    if not re.fullmatch(r"[a-f0-9]{8,32}", tag):
        raise SandboxControlError("bad_tag", "session tag must be hex")
    sid = f"/tmp/.ompsbx-{tag}.sid"
    return f'echo $$ > {sid}; [ -f {ENV_FILE} ] && . {ENV_FILE}; exec omp "$@"'


def agent_pane_argv(
    openshell: str,
    sandbox: str,
    *,
    model: str,
    thinking: str = "medium",
    yolo: bool = True,
    resume: bool = False,
    workdir: str = "/sandbox/work",
    tag: str | None = None,
) -> tuple[list[str], str]:
    """Return ``(argv, session_tag)`` for the pane process.

    With ``--tty``, OpenShell starts the command as a session leader that owns
    the PTY. ``sh`` records ``$$`` (the session id) and then ``exec``s omp,
    so omp keeps the controlling terminal: SIGWINCH reaches it on resize and
    stop/down can kill the whole remote tree with ``pkill -s``. This is what
    ``OpenShellPodmanTier.#killSession`` does. Unlike the ``--no-tty`` path,
    ``setsid`` is deliberately absent: it would detach omp from the PTY
    (checked live: ``setsid -w`` left omp with TTY ``?``).
    """
    if not is_owned(sandbox) and not sandbox.startswith("osp-"):
        raise SandboxControlError("not_owned", "unknown sandbox owner prefix")
    if not MODEL_RE.match(model):
        raise SandboxControlError("bad_model", "invalid model id")
    if thinking not in THINKING:
        raise SandboxControlError(
            "bad_thinking", f"thinking must be one of {sorted(THINKING)}"
        )
    tag = tag or secrets.token_hex(6)
    omp_args = ["--model", model, "--thinking", thinking]
    if yolo:
        omp_args += ["--approval-mode", "yolo"]
    if resume:
        omp_args.append("--continue")
    inner = ["sh", "-c", session_script(tag), "sh", *omp_args]
    argv = exec_argv(openshell, sandbox, inner, cwd=workdir, tty=True, timeout_s=0)
    return argv, tag


def stop_session_argv(openshell: str, sandbox: str, tag: str) -> list[str]:
    """Kill the pane's remote session group (used by stop/down)."""
    sid = f"/tmp/.ompsbx-{tag}.sid"
    session_script(tag)  # validates tag
    script = f'[ -f {sid} ] && pkill -KILL -s "$(cat {sid})"; rm -f {sid}; true'
    return exec_argv(openshell, sandbox, ["sh", "-c", script], cwd="/sandbox")


@dataclass(frozen=True)
class PaneHandle:
    pane_id: str
    workspace_id: str
    session_tag: str

    def as_row(self) -> dict[str, str]:
        return {"pane_id": self.pane_id, "workspace": self.workspace_id}


@dataclass
class HerdrAgentPane:
    """Drive one sandboxed omp agent through the Herdr CLI (injected runner)."""

    herdr: str
    runner: HerdrRunner
    source: str = "omega-prime"

    def _run(self, *args: str) -> RunResult:
        result = self.runner([self.herdr, *args])
        if result.returncode != 0:
            raise SandboxControlError(
                "herdr_failed", f"herdr {args[0]} {args[1] if len(args) > 1 else ''}"
            )
        return result

    def launch(
        self,
        pane_argv: Sequence[str],
        *,
        session_tag: str,
        label: str,
        host_cwd: str,
        resume_argv: Sequence[str] | None = None,
    ) -> PaneHandle:
        created = self._run(
            "workspace", "create", "--cwd", host_cwd, "--label", label, "--no-focus"
        )
        pane_id, workspace_id = _ids(created.stdout)
        self._run(
            "pane", "wait-output", "--regex", PROMPT_RE, "--timeout", "15000", pane_id
        )
        self._run("pane", "run", pane_id, shlex.join(pane_argv))
        self.report(pane_id, "working", resume_argv=resume_argv)
        return PaneHandle(pane_id, workspace_id, session_tag)

    def report(
        self,
        pane_id: str,
        state: str,
        *,
        message: str | None = None,
        resume_argv: Sequence[str] | None = None,
    ) -> None:
        if state not in {"idle", "working", "blocked", "unknown"}:
            raise SandboxControlError("bad_state", state)
        args = [
            "pane",
            "report-agent",
            "--source",
            self.source,
            "--agent",
            "omp",
            "--state",
            state,
        ]
        if message:
            args += ["--message", message[:200]]
        args.append(pane_id)
        if resume_argv:
            args += ["--", *resume_argv]
        self._run(*args)

    def prompt(self, pane_id: str, text: str) -> None:
        """Type one line (e.g. ``/gsd-plan-phase <MAGIC>``) and submit it."""
        if "\n" in text or "\r" in text:
            raise SandboxControlError(
                "multiline_prompt",
                "pass long task text as a file in the sandbox, not as typed input",
            )
        self._run("pane", "send-text", pane_id, text)
        self._run("pane", "send-keys", pane_id, "Enter")

    def interrupt(self, pane_id: str) -> None:
        self._run("pane", "send-keys", pane_id, "esc")
        self.report(pane_id, "idle", message="interrupted")

    def stop(self, pane_id: str) -> None:
        self._run("pane", "send-keys", pane_id, "C-c", "C-c")
        self.report(pane_id, "idle", message="stopped")


def _ids(stdout: str) -> tuple[str, str]:
    try:
        payload: Any = json.loads(stdout)
        result = payload.get("result", payload)
        pane = result["root_pane"]["pane_id"]
        workspace = result.get("workspace_id") or result["workspace"]["workspace_id"]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SandboxControlError(
            "herdr_unparseable", "workspace create output"
        ) from exc
    return str(pane), str(workspace)
