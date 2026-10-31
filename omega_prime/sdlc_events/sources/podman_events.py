"""L13: Podman container events → sandbox lifecycle events.

Reads ``podman events --format json --filter type=container`` lines. The line
shape and the OpenShell container names were checked against this box's
history (2026-10-23):

* workload: ``openshell-<workspace>--<sandbox>-<uuid>``
* supervisor: ``openshell-supervisor-<uuid>`` (the same uuid as its workload)
* ``Status``: create, init, start, attach, died, cleanup, stop, remove;
  ``ContainerExitCode`` appears on died/remove.

Deleting a sandbox kills its containers (``died`` with 137). Those deaths
are teardown, not failures, so the source asks a ``teardown`` predicate
(the sandbox-control ownership record) before it reports a death as
``exited``/``unhealthy``. The subprocess is injected so tests never spawn
Podman, and the source never starts or configures Podman itself.
"""

from __future__ import annotations

import json
import re
import select
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from omega_prime.sdlc_events.sandbox import SandboxEvent, SandboxLifecycle
from omega_prime.sdlc_events.types import DeliveryId

SOURCE = "podman-events"
DEFAULT_TEARDOWN_GRACE_S = 10.0
PODMAN_EVENTS_ARGV_TAIL = ("events", "--format", "json", "--filter", "type=container")

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
WORKLOAD_RE = re.compile(
    rf"^openshell-(?P<ws>[a-z0-9][a-z0-9-]*?)--(?P<name>[a-z0-9][a-z0-9-]*?)-(?P<uuid>{_UUID})$"
)
SUPERVISOR_RE = re.compile(rf"^openshell-supervisor-(?P<uuid>{_UUID})$")

_WORKLOAD_STATUS = {
    "create": SandboxLifecycle.CREATED,
    "start": SandboxLifecycle.STARTED,
    "died": SandboxLifecycle.EXITED,
    "oom": SandboxLifecycle.OOM,
    "remove": SandboxLifecycle.REMOVED,
}
_HELD = frozenset({SandboxLifecycle.EXITED, SandboxLifecycle.UNHEALTHY})
# ``openshell sandbox delete`` stops containers with SIGTERM/SIGKILL. Only a
# held death with one of these codes can be teardown; anything else (exit 1,
# an unknown code) is a real failure even if removal follows it.
TEARDOWN_EXIT_CODES = frozenset({137, 143})


@dataclass
class PodmanEventParser:
    """Stateful parser: remembers uuid → sandbox so supervisor events resolve."""

    task_for: Callable[[str], str | None] = lambda _name: None
    teardown: Callable[[str], bool] = lambda _name: False
    _names: dict[str, str] = field(default_factory=dict)

    def parse_line(self, line: str) -> SandboxEvent | None:
        line = line.strip()
        if not line:
            return None
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return None
        return self.parse(raw) if isinstance(raw, dict) else None

    def parse(self, raw: dict[str, Any]) -> SandboxEvent | None:
        if raw.get("Type", "container") != "container":
            return None
        name = str(raw.get("Name") or "")
        status = str(raw.get("Status") or "").lower()
        cid = str(raw.get("ID") or "")
        stamp = raw.get("timeNano") or raw.get("time") or ""
        if not cid or not status:
            return None
        delivery = DeliveryId(f"podman:{cid[:12]}:{status}:{stamp}")
        at = _seconds(raw)
        workload = WORKLOAD_RE.match(name)
        if workload:
            sandbox = workload.group("name")
            self._names[workload.group("uuid")] = sandbox
            return self._workload(raw, sandbox, status, delivery, at)
        supervisor = SUPERVISOR_RE.match(name)
        if supervisor:
            sandbox_name = self._names.get(supervisor.group("uuid"))
            if sandbox_name is None or status not in {"died", "oom"}:
                return None
            if self.teardown(sandbox_name):
                return None
            return SandboxEvent(
                delivery=delivery,
                sandbox=sandbox_name,
                lifecycle=SandboxLifecycle.UNHEALTHY,
                source=SOURCE,
                task_id=self.task_for(sandbox_name),
                exit_code=_exit_code(raw),
                reasons=(f"supervisor_{status}",),
                at=at,
            )
        return None

    def _workload(
        self,
        raw: dict[str, Any],
        sandbox: str,
        status: str,
        delivery: DeliveryId,
        at: float,
    ) -> SandboxEvent | None:
        if status == "health_status":
            health = str(raw.get("HealthStatus") or raw.get("health_status") or "")
            if health.lower() != "unhealthy":
                return None
            lifecycle = SandboxLifecycle.UNHEALTHY
        else:
            mapped = _WORKLOAD_STATUS.get(status)
            if mapped is None:
                return None
            lifecycle = mapped
        if lifecycle is SandboxLifecycle.EXITED and self.teardown(sandbox):
            return None
        return SandboxEvent(
            delivery=delivery,
            sandbox=sandbox,
            lifecycle=lifecycle,
            source=SOURCE,
            task_id=self.task_for(sandbox),
            exit_code=_exit_code(raw) if lifecycle is SandboxLifecycle.EXITED else None,
            at=at,
        )


def _exit_code(raw: dict[str, Any]) -> int | None:
    value = raw.get("ContainerExitCode")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _seconds(raw: dict[str, Any]) -> float:
    nano = raw.get("timeNano")
    if isinstance(nano, int | float) and nano > 0:
        return float(nano) / 1e9
    sec = raw.get("time")
    if isinstance(sec, int | float) and sec > 0:
        return float(sec)
    return 0.0


def podman_events_argv(podman: str, *, since: str | None = None) -> list[str]:
    argv = [podman, *PODMAN_EVENTS_ARGV_TAIL]
    if since:
        argv += ["--since", since]
    return argv


def _line_time(line: str) -> float:
    """Event time of any Podman JSON line, mapped or not (0.0 if none)."""
    try:
        raw = json.loads(line)
    except ValueError:
        return 0.0
    return _seconds(raw) if isinstance(raw, dict) else 0.0


def _teardown_death(event: SandboxEvent) -> bool:
    return event.exit_code in TEARDOWN_EXIT_CODES


def iter_sandbox_events(
    lines: Iterable[str],
    parser: PodmanEventParser,
    *,
    teardown_grace_s: float = DEFAULT_TEARDOWN_GRACE_S,
    clock: Callable[[], float] | None = None,
) -> Iterator[SandboxEvent]:
    """Map an injected line stream (a live ``podman events`` pipe or a replay).

    A death (``exited``/``unhealthy``) is held until ``teardown_grace_s`` has
    passed. If the sandbox's workload is removed inside that window and the
    death's exit code is a delete's signal (137/143), it was a teardown and is
    dropped; any other held death is a real failure and is emitted before the
    ``removed`` event.

    Time advances on every line, including events the parser ignores, by the
    line's own event time, so replays stay deterministic. For a live stream,
    pass ``clock`` (wall seconds, e.g. ``time.time``) and feed it through
    :func:`with_heartbeats`: empty heartbeat lines then flush held deaths on a
    quiet stream instead of leaving them pending forever. Held deaths are also
    flushed when the stream ends.
    """
    pending: list[SandboxEvent] = []
    for line in lines:
        now = max(_line_time(line), clock() if clock else 0.0)
        event = parser.parse_line(line)
        if event is not None:
            now = max(now, event.at)
        ready = [p for p in pending if now - p.at >= teardown_grace_s]
        pending = [p for p in pending if p not in ready]
        yield from ready
        if event is None:
            continue
        if event.lifecycle is SandboxLifecycle.REMOVED:
            same = [p for p in pending if p.sandbox == event.sandbox]
            pending = [p for p in pending if p.sandbox != event.sandbox]
            yield from (p for p in same if not _teardown_death(p))
            yield event
        elif event.lifecycle in _HELD:
            pending.append(event)
        else:
            yield event
    yield from pending


def with_heartbeats(stream: Any, interval_s: float = 1.0) -> Iterator[str]:
    """Lines from a live pipe, with ``""`` whenever it is quiet for ``interval_s``.

    ``stream`` is a readable file object with ``fileno()`` (the ``podman
    events`` stdout). Ends at EOF.
    """
    while True:
        readable, _, _ = select.select([stream], [], [], interval_s)
        if not readable:
            yield ""
            continue
        line = stream.readline()
        if not line:
            return
        yield line
