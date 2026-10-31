# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""OpenShell (Podman driver) sandbox provider for the sandbox-control tools.

The argv mirrors ``omp-sandbox``'s ``OpenShellPodmanTier`` so both paths make
the same sandboxes. The rules, all fail closed:

* the gateway must already report ``Connected`` and the policy file must
  exist; this module never starts a gateway, Podman socket or image build;
* the provider may exec in or delete only sandboxes named ``ompsbx-…``
  (sandboxes from other owners, such as the TS core's ``osp-…``, are
  read-only here);
* exec takes an argv (no shell string), a cwd under ``/sandbox``, and an env
  with no secret-shaped names; secrets never go on argv;
* outputs are tail-bounded while the command runs (host memory stays
  bounded) and redacted before they leave this module;
* ``down`` checks that no containers, volumes or list rows are left, and a
  failed Podman check is an error, never an empty result.
"""

from __future__ import annotations

import contextlib
import json
import os
import posixpath
import re
import secrets
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omega_prime.credentials.redact import redact_text
from omega_prime.sandbox_control.events import EventSink, MemorySink
from omega_prime.sandbox_control.ownership import Ownership
from omega_prime.sdlc_events.sandbox import (
    ReadinessInput,
    SandboxEvent,
    SandboxLifecycle,
    readiness_event,
)
from omega_prime.sdlc_events.types import DeliveryId

OWNED_PREFIX = "ompsbx-"
SOURCE = "sandbox_control"
SECRET_ENV_RE = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL|AUTH", re.IGNORECASE)
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
SLUG_RE = re.compile(r"[^a-z0-9]+")
TAIL_CHARS = 8000
# Bytes kept per stream while a command runs; the rest is dropped as it arrives.
CAPTURE_BYTES = 64 * 1024
_READ_CHUNK = 8192
MAX_ARGV = 256
MAX_ARG_CHARS = 16_384
MAX_STDIN_BYTES = 1_000_000
SANDBOX_PATH = "/opt/bun/bin:/opt/node/bin:/usr/local/bin:/usr/bin:/bin"
BASE_ENV = {
    "HOME": "/sandbox/home",
    "PATH": SANDBOX_PATH,
    "LANG": "C.UTF-8",
    "TERM": "dumb",
    "OMP_SANDBOX": "1",
}


class SandboxControlError(Exception):
    """A refused or failed sandbox operation; ``code`` is machine-readable."""

    def __init__(
        self, code: str, reason: str, *, details: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        # Structured context the caller needs to recover, e.g. the allocated
        # sandbox ``name`` when create fails after the gateway saw it.
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {**self.details, "error": self.code, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    # True when the runner dropped output beyond its capture bound.
    truncated: bool = False


Runner = Callable[[Sequence[str], str | None, float], RunResult]


def host_env(env: Mapping[str, str]) -> dict[str, str]:
    """Host env the openshell/podman CLIs need: config and runtime dirs, no secrets."""
    pairs = (
        ("HOME", env.get("HOME")),
        ("PATH", env.get("PATH")),
        ("LANG", env.get("LANG")),
        ("XDG_RUNTIME_DIR", env.get("XDG_RUNTIME_DIR")),
        ("XDG_CONFIG_HOME", env.get("XDG_CONFIG_HOME")),
        ("XDG_DATA_HOME", env.get("XDG_DATA_HOME")),
        ("CONTAINER_HOST", env.get("CONTAINER_HOST")),
        ("OPENSHELL_GATEWAY", env.get("OPENSHELL_GATEWAY")),
        ("OPENSHELL_GATEWAY_ENDPOINT", env.get("OPENSHELL_GATEWAY_ENDPOINT")),
    )
    out = {key: value for key, value in pairs if value}
    out["OPENSHELL_TELEMETRY_ENABLED"] = "false"
    return out


class _TailBuffer:
    """Keeps only the last ``limit`` bytes of a stream as it is read."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._data = bytearray()
        self.dropped = False

    def feed(self, chunk: bytes) -> None:
        self._data += chunk
        extra = len(self._data) - self._limit
        if extra > 0:
            del self._data[:extra]
            self.dropped = True

    def text(self) -> str:
        return self._data.decode("utf-8", errors="replace")


def _drain(stream: Any, buffer: _TailBuffer) -> None:
    with stream:
        while chunk := stream.read(_READ_CHUNK):
            buffer.feed(chunk)


def _feed_stdin(stream: Any, data: bytes) -> None:
    with contextlib.suppress(BrokenPipeError, OSError), stream:
        stream.write(data)


def subprocess_runner(
    env: Mapping[str, str] | None = None, *, capture_bytes: int = CAPTURE_BYTES
) -> Runner:
    """Real runner: no shell, minimal host env, hard timeout, bounded capture.

    Output is read while the process runs and only the last ``capture_bytes``
    of each stream are kept, so a noisy hour-long command cannot grow host
    memory without limit.
    """
    child_env = host_env(env if env is not None else os.environ)

    def run(argv: Sequence[str], stdin: str | None, timeout: float) -> RunResult:
        try:
            proc = subprocess.Popen(
                list(argv),
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
            )
        except FileNotFoundError as exc:
            raise SandboxControlError("binary_missing", str(argv[0])) from exc
        out, err = _TailBuffer(capture_bytes), _TailBuffer(capture_bytes)
        workers = [
            threading.Thread(target=_drain, args=(proc.stdout, out), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr, err), daemon=True),
        ]
        if stdin is not None:
            workers.append(
                threading.Thread(
                    target=_feed_stdin, args=(proc.stdin, stdin.encode()), daemon=True
                )
            )
        for worker in workers:
            worker.start()
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.wait()
            raise SandboxControlError("timeout", f"{timeout:.0f}s") from exc
        finally:
            for worker in workers:
                worker.join(timeout=5)
        return RunResult(
            returncode, out.text(), err.text(), truncated=out.dropped or err.dropped
        )

    return run


def resolve_binary(command: str, env: Mapping[str, str] | None = None) -> str | None:
    """Executable path for ``command``: explicit paths as-is, bare names via PATH."""
    if "/" in command:
        return command if os.access(command, os.X_OK) else None
    path = (env if env is not None else os.environ).get("PATH")
    return shutil.which(command, path=path)


@dataclass(frozen=True)
class SandboxConfig:
    openshell: str
    podman: str
    image: str
    policy: Path
    state_dir: Path
    ready_timeout_s: float = 180.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> SandboxConfig:
        # No Path.home() fallback: an isolated caller without HOME must not
        # silently pick up the operator's OpenShell CLI and gateway.
        home = env.get("HOME") or ""
        root = Path(env.get("OMP_SANDBOX_ROOT") or "/workspace/omp-sandbox")
        state_dir = env.get("OMEGA_PRIME_STATE_DIR") or (
            str(Path(home) / ".omega-prime") if home else ".omega-prime"
        )
        default_openshell = (
            str(Path(home) / ".local/opt/openshell/0.1.4/bin/openshell")
            if home
            else "openshell"
        )
        return cls(
            openshell=env.get("OMP_SANDBOX_OPENSHELL") or default_openshell,
            podman=env.get("OMP_SANDBOX_PODMAN") or "/usr/bin/podman",
            image=env.get("OMP_SANDBOX_PODMAN_IMAGE") or "localhost/omp-sandbox:0.1",
            policy=Path(
                env.get("OMP_SANDBOX_POLICY") or root / "sandbox/policy-podman.yaml"
            ),
            state_dir=Path(state_dir),
        )

    @property
    def outbox(self) -> Path:
        return self.state_dir / "events" / "outbox.jsonl"

    @property
    def ownership_dir(self) -> Path:
        return self.state_dir / "sandbox" / "owned"


def owned_name(slug: str, suffix: str | None = None) -> str:
    clean = SLUG_RE.sub("-", slug.lower()).strip("-")[:24].strip("-") or "task"
    return f"{OWNED_PREFIX}{clean}-{suffix or secrets.token_hex(3)}"


def is_owned(name: str) -> bool:
    return bool(re.fullmatch(rf"{OWNED_PREFIX}[a-z0-9-]{{1,48}}", name))


def _tail(text: str) -> tuple[str, bool]:
    clean = redact_text(text or "")
    if len(clean) <= TAIL_CHARS:
        return clean, False
    return clean[-TAIL_CHARS:], True


def safe_cwd(cwd: str) -> str:
    if not cwd.startswith("/"):
        raise SandboxControlError("bad_cwd", "cwd must be absolute")
    norm = posixpath.normpath(cwd)
    if norm != "/sandbox" and not norm.startswith("/sandbox/"):
        raise SandboxControlError("bad_cwd", "cwd must be under /sandbox")
    return norm


def safe_env(env: Mapping[str, str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (env or {}).items():
        if not isinstance(key, str) or not ENV_NAME_RE.match(key):
            raise SandboxControlError("bad_env", f"invalid env name {key!r}")
        if SECRET_ENV_RE.search(key):
            raise SandboxControlError(
                "secret_env_refused",
                f"{key} looks like a secret; secrets go through the broker env file",
            )
        if not isinstance(value, str) or "\n" in value or "\x00" in value:
            raise SandboxControlError("bad_env", f"invalid value for {key}")
        out[key] = value
    return out


def safe_argv(argv: Sequence[str]) -> list[str]:
    if not isinstance(argv, list | tuple) or not argv:
        raise SandboxControlError("bad_argv", "argv must be a non-empty list")
    if len(argv) > MAX_ARGV:
        raise SandboxControlError("bad_argv", f"at most {MAX_ARGV} arguments")
    out: list[str] = []
    for item in argv:
        if not isinstance(item, str) or "\x00" in item or len(item) > MAX_ARG_CHARS:
            raise SandboxControlError("bad_argv", "arguments must be short strings")
        out.append(item)
    return out


def exec_argv(
    openshell: str,
    name: str,
    argv: Sequence[str],
    *,
    cwd: str = "/sandbox",
    env: Mapping[str, str] | None = None,
    tty: bool = False,
    timeout_s: int = 0,
) -> list[str]:
    """``openshell sandbox exec`` argv. Secret-shaped names are never forwarded."""
    full = {**BASE_ENV, **safe_env(env)}
    if tty:
        full["TERM"] = "xterm-256color"
    out = [
        openshell,
        "sandbox",
        "exec",
        "-n",
        name,
        "--tty" if tty else "--no-tty",
        "--no-login-shell",
        "--timeout",
        str(max(0, int(timeout_s))),
        "--workdir",
        safe_cwd(cwd),
    ]
    for key, value in full.items():
        if not SECRET_ENV_RE.search(key):
            out += ["--env", f"{key}={value}"]
    return [*out, "--", *safe_argv(argv)]


@dataclass
class OpenShellSandboxProvider:
    config: SandboxConfig
    runner: Runner
    sink: EventSink = field(default_factory=MemorySink)
    ownership: Ownership | None = None
    readiness_probe: Callable[[str], ReadinessInput] | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    # False when lifecycle events reach only the outbox (no dashboard).
    task_store_available: bool = True

    def __post_init__(self) -> None:
        if self.ownership is None:
            self.ownership = Ownership(self.config.ownership_dir)

    # -- plumbing -----------------------------------------------------------

    def _os(
        self, *args: str, stdin: str | None = None, timeout: float = 60.0
    ) -> RunResult:
        result = self.runner([self.config.openshell, *args], stdin, timeout)
        if result.truncated:
            # A clipped control reply loses its head: JSON will not parse and
            # a listing may hide a row, so it is never treated as an answer.
            raise SandboxControlError(
                "control_output_truncated", f"openshell {' '.join(args[:2])}"
            )
        return result

    def _emit(self, event: SandboxEvent) -> None:
        self.sink.emit(event)

    def _event(
        self,
        name: str,
        lifecycle: SandboxLifecycle,
        *,
        task_id: str | None = None,
        exit_code: int | None = None,
        reasons: tuple[str, ...] = (),
    ) -> SandboxEvent:
        return SandboxEvent(
            delivery=DeliveryId(
                f"{SOURCE}:{name}:{lifecycle.value}:{secrets.token_hex(4)}"
            ),
            sandbox=name,
            lifecycle=lifecycle,
            source=SOURCE,
            task_id=task_id,
            exit_code=exit_code,
            reasons=reasons,
        )

    def _require_owned(self, name: str) -> None:
        if not is_owned(name):
            raise SandboxControlError(
                "not_owned",
                f"only {OWNED_PREFIX}* sandboxes are managed by these tools",
            )

    # -- read-only ------------------------------------------------------------

    def preflight(self) -> list[str]:
        """Reasons the provider cannot create sandboxes right now (empty = ok)."""
        reasons: list[str] = []
        if resolve_binary(self.config.openshell) is None:
            reasons.append("openshell_missing")
            return reasons
        if not self.config.policy.is_file():
            reasons.append("policy_missing")
        try:
            status = self._os("status", timeout=20)
        except SandboxControlError as exc:
            reasons.append(f"gateway_{exc.code}")
            return reasons
        if status.returncode != 0 or not re.search(
            r"Status:\s*Connected", status.stdout
        ):
            reasons.append("gateway_not_connected")
        return reasons

    def list_sandboxes(self) -> list[dict[str, Any]]:
        result = self._os("sandbox", "list", "-o", "json", timeout=30)
        if result.returncode != 0:
            raise SandboxControlError("list_failed", _tail(result.stderr)[0][-300:])
        try:
            payload = json.loads(result.stdout or "{}")
        except ValueError as exc:
            raise SandboxControlError("list_unparseable", str(exc)) from exc
        rows = payload.get("sandboxes") if isinstance(payload, dict) else None
        return [_summary(row) for row in rows or [] if isinstance(row, dict)]

    def status(self, name: str) -> dict[str, Any]:
        result = self._os("sandbox", "get", name, "-o", "json", timeout=30)
        if result.returncode != 0:
            text = (result.stderr or result.stdout).lower()
            if "not found" in text or "notfound" in text:
                return {"name": name, "exists": False}
            raise SandboxControlError("status_failed", _tail(result.stderr)[0][-300:])
        try:
            row = json.loads(result.stdout or "{}")
        except ValueError as exc:
            raise SandboxControlError("status_unparseable", str(exc)) from exc
        out = {"exists": True, **_summary(row)}
        owner = self.ownership.get(name) if self.ownership and is_owned(name) else None
        out["owned"] = is_owned(name)
        out["task_id"] = (owner or {}).get("task_id")
        return out

    # -- mutating (approval-gated at the tool layer) ---------------------------

    def create(
        self,
        slug: str,
        *,
        task_id: str | None = None,
        ready_timeout_s: float | None = None,
    ) -> dict[str, Any]:
        reasons = self.preflight()
        if reasons:
            raise SandboxControlError("preflight_failed", ",".join(reasons))
        name = owned_name(slug)
        try:
            return self._create(name, task_id, ready_timeout_s)
        except SandboxControlError as exc:
            # Once a name is allocated the gateway may already hold it (a CLI
            # timeout after the request landed), so callers need it to check
            # or delete instead of retrying under a new name.
            exc.details.setdefault("name", name)
            raise

    def _create(
        self, name: str, task_id: str | None, ready_timeout_s: float | None
    ) -> dict[str, Any]:
        assert self.ownership is not None
        self.ownership.record(name, task_id)
        result = self._os(
            "sandbox",
            "create",
            "--name",
            name,
            "--from",
            self.config.image,
            "--policy",
            str(self.config.policy),
            "--detach",
            "--no-tty",
            "--no-auto-providers",
            timeout=300,
        )
        if result.returncode != 0:
            self._emit(
                self._event(
                    name,
                    SandboxLifecycle.NOT_READY,
                    task_id=task_id,
                    reasons=("create_failed",),
                )
            )
            raise SandboxControlError("create_failed", _tail(result.stderr)[0][-500:])
        self._emit(self._event(name, SandboxLifecycle.CREATED, task_id=task_id))
        phase = self._wait_ready(name, ready_timeout_s or self.config.ready_timeout_s)
        probe = (
            self.readiness_probe(name)
            if self.readiness_probe
            else ReadinessInput(container_ready=phase == "Ready")
        )
        if phase != "Ready":
            probe = ReadinessInput(
                container_ready=False,
                broker_required=probe.broker_required,
                broker_relay_ready=probe.broker_relay_ready,
                tools_connected=probe.tools_connected,
                skills_connected=probe.skills_connected,
            )
        ready = readiness_event(
            name,
            probe,
            source=SOURCE,
            task_id=task_id,
            attempt=secrets.token_hex(4),
        )
        self._emit(ready)
        return {
            "name": name,
            "task_id": task_id,
            "phase": phase,
            "ready": ready.lifecycle is SandboxLifecycle.READY,
            "reasons": list(ready.reasons),
        }

    def _wait_ready(self, name: str, timeout_s: float) -> str:
        deadline = self.clock() + timeout_s
        phase = "Unknown"
        while True:
            try:
                phase = str(self.status(name).get("phase") or "Unknown")
            except SandboxControlError:
                phase = "Unknown"
            if phase == "Ready" or re.search(r"error|fail", phase, re.IGNORECASE):
                return phase
            if self.clock() >= deadline:
                return phase
            self.sleep(1.0)

    def exec(
        self,
        name: str,
        argv: Sequence[str],
        *,
        cwd: str = "/sandbox",
        env: Mapping[str, str] | None = None,
        stdin: str | None = None,
        timeout_s: int = 600,
        report_exit: bool = False,
    ) -> dict[str, Any]:
        self._require_owned(name)
        if stdin is not None and len(stdin.encode()) > MAX_STDIN_BYTES:
            raise SandboxControlError("stdin_too_large", f">{MAX_STDIN_BYTES} bytes")
        timeout_s = max(1, min(int(timeout_s), 3600))
        argv_full = exec_argv(
            self.config.openshell, name, argv, cwd=cwd, env=env, timeout_s=timeout_s
        )
        label = f"exec:{posixpath.basename(argv[0])[:40]}"
        try:
            result = self.runner(argv_full, stdin, timeout_s + 30)
        except SandboxControlError as exc:
            if report_exit:
                # The tracked process's exit is unknown (timeout, missing
                # CLI): block the owning task rather than leave it working.
                self._emit_exit(name, None, (label, f"exit_unknown:{exc.code}"))
            raise
        stdout, out_cut = _tail(result.stdout)
        stderr, err_cut = _tail(result.stderr)
        if report_exit:
            self._emit_exit(name, result.returncode, (label,))
        return {
            "name": name,
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": out_cut or err_cut or result.truncated,
        }

    def _emit_exit(
        self, name: str, exit_code: int | None, reasons: tuple[str, ...]
    ) -> None:
        assert self.ownership is not None
        self._emit(
            self._event(
                name,
                SandboxLifecycle.EXITED,
                task_id=self.ownership.task_for(name),
                exit_code=exit_code,
                reasons=reasons,
            )
        )

    def down(self, name: str) -> dict[str, Any]:
        self._require_owned(name)
        assert self.ownership is not None
        task_id = self.ownership.task_for(name)
        before = self.status(name)
        # The status row disappears once the gateway forgets the sandbox, so
        # the uuid is kept on the ownership record until cleanup passes; a
        # retry can still match ``openshell-supervisor-<uuid>`` leftovers.
        uuid = str(before.get("id") or "") or self.ownership.uuid_for(name) or ""
        self.ownership.mark_teardown(name, uuid=uuid or None)
        try:
            if before.get("exists"):
                result = self._os("sandbox", "delete", name, timeout=180)
                if result.returncode != 0:
                    raise SandboxControlError(
                        "delete_failed", _tail(result.stderr)[0][-500:]
                    )
            leftovers = self._leftovers(name, uuid)
            if leftovers:
                raise SandboxControlError("leftovers", ",".join(leftovers)[:500])
        except SandboxControlError:
            # The sandbox may still be running: later deaths are real again.
            self.ownership.clear_teardown(name)
            raise
        self._emit(self._event(name, SandboxLifecycle.REMOVED, task_id=task_id))
        self.ownership.forget(name)
        return {"name": name, "removed": True, "existed": bool(before.get("exists"))}

    def _leftovers(self, name: str, uuid: str) -> list[str]:
        found: list[str] = []
        key = f"--{name}-"
        for _ in range(10):
            found = []
            if any(row.get("name") == name for row in self.list_sandboxes()):
                found.append(f"list:{name}")
            # Filter server-side (repeated name filters are OR'd) so the
            # listing stays small; a clipped one is refused below.
            names = ["--filter", f"name={name}"]
            if uuid:
                names += ["--filter", f"name={uuid}"]
            ps = self._podman_check("ps", "-a", *names, "--format", "{{.Names}}")
            for line in ps.stdout.splitlines():
                if key in line or (uuid and line.endswith(uuid)):
                    found.append(f"container:{line}")
            vols = self._podman_check("volume", "ls", *names, "--format", "{{.Name}}")
            for line in vols.stdout.splitlines():
                if name in line or (uuid and uuid in line):
                    found.append(f"volume:{line}")
            if not found:
                return []
            self.sleep(1.0)
        return found

    def _podman_check(self, *args: str) -> RunResult:
        """A leftover probe; a failed probe proves nothing, so it is an error."""
        result = self.runner([self.config.podman, *args], None, 30)
        if result.returncode != 0:
            raise SandboxControlError(
                "leftover_check_failed",
                f"podman {args[0]} exit {result.returncode}: "
                + _tail(result.stderr)[0][-300:],
            )
        if result.truncated:
            # A leftover may sit in the dropped part: never report "removed".
            raise SandboxControlError(
                "leftover_check_failed", f"podman {args[0]} output truncated"
            )
        return result


def _summary(row: Mapping[str, Any]) -> dict[str, Any]:
    conditions = row.get("conditions") if isinstance(row, Mapping) else None
    ready = next(
        (
            c
            for c in conditions or []
            if isinstance(c, Mapping) and c.get("type") == "Ready"
        ),
        None,
    )
    name = str(row.get("name") or "")
    return {
        "name": name,
        "id": row.get("id"),
        "phase": row.get("phase"),
        "created_at": row.get("created_at"),
        "workspace": row.get("workspace"),
        "exit_code": row.get("exit_code"),
        "ready_condition": None
        if ready is None
        else {
            "status": ready.get("status"),
            "reason": ready.get("reason"),
            "message": redact_text(str(ready.get("message") or ""))[:200],
        },
        "owned": is_owned(name),
    }
