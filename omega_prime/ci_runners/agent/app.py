"""Plain-ASGI runner agent: the controller's only door into the Desktop.

Routes (all but ``/healthz`` need ``Authorization: Bearer <agent token>``):

* ``GET  /healthz``            → 200 ``{status, draining, runners, capacity}``
* ``GET  /v1/runners``         → 200 ``[{name, generation, status, pid}]``
* ``POST /v1/runners``         → 201 first, 200 same-generation replay
  (``{status: "replayed"}`` while running, ``{status: "finished", exit_code}``
  once it ran and exited), 409 conflict/draining, 429 full, 400 malformed,
  401, 413 over 1 MiB
* ``DELETE /v1/runners/{name}`` → 202 stopping, 404 unknown
* ``POST /v1/drain``           → 200 ``{draining: true}``
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from omega_prime.ci_runners.agent.supervisor import (
    Admission,
    Supervisor,
    check_private,
)
from omega_prime.ci_runners.http import HttpRequest, Transport, send, urllib_transport
from omega_prime.ci_runners.settings import (
    MAX_BODY_BYTES,
    RUNNER_NAME_RE,
    AgentSettings,
    SecretFile,
)
from omega_prime.ci_runners.webhook import (
    ClientDisconnected,
    Receive,
    Scope,
    Send,
    WebhookRejected,
    bearer_ok,
    header_map,
    read_body,
    send_json,
)

log = logging.getLogger(__name__)

CALLBACK_TIMEOUT_S = 10.0
LAUNCH_KEYS = frozenset({"name", "generation", "jitconfig"})
_STATUS = {
    Admission.ACCEPTED: 201,
    Admission.REPLAYED: 200,
    Admission.FINISHED: 200,
    Admission.CONFLICT: 409,
    Admission.DRAINING: 409,
    Admission.CAPACITY: 429,
}


class ControllerCallback:
    """One finite, authenticated POST per exit; never retried, never looped."""

    def __init__(
        self,
        controller_url: str,
        token: str,
        transport: Transport = urllib_transport,
        timeout_s: float = CALLBACK_TIMEOUT_S,
    ) -> None:
        self.url = controller_url.rstrip("/") + "/agent/events"
        self._token = token
        self._transport = transport
        self._timeout = timeout_s

    def __repr__(self) -> str:
        return f"ControllerCallback(url={self.url!r})"

    async def __call__(self, payload: dict[str, Any]) -> None:
        response = await send(
            self._transport,
            HttpRequest(
                "POST",
                self.url,
                {
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                json.dumps(payload).encode(),
                self._timeout,
            ),
        )
        if response.status != 202:
            log.warning("controller rejected exit callback: HTTP %s", response.status)


def create_agent_app(supervisor: Supervisor, *, token: str) -> Any:
    if not token:
        raise ValueError("agent token is required")

    async def lifespan(receive: Receive, send_: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                supervisor.prepare()
                await send_({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await supervisor.shutdown()
                await send_({"type": "lifespan.shutdown.complete"})
                return

    async def launch(receive: Receive, send_: Send) -> None:
        body = await read_body(receive, MAX_BODY_BYTES)
        try:
            data = json.loads(body)
        except (UnicodeDecodeError, ValueError):
            raise WebhookRejected(400, "bad_json") from None
        if (
            not isinstance(data, dict)
            or set(data) != LAUNCH_KEYS
            or not all(isinstance(data[k], str) for k in LAUNCH_KEYS)
        ):
            raise WebhookRejected(400, "bad_request")
        name, generation = data["name"], data["generation"]
        try:
            admission = supervisor.admit(name, generation, data["jitconfig"])
        except ValueError:
            raise WebhookRejected(400, "bad_request") from None
        finally:
            data.clear()
        status = _STATUS[admission]
        if status >= 400:
            await send_json(send_, status, {"error": admission.value})
            return
        if admission is Admission.FINISHED:
            exit_code = supervisor.finished_exit(name, generation)
            await send_json(
                send_, status, {"status": admission.value, "exit_code": exit_code}
            )
            return
        await send_json(send_, status, {"status": admission.value})

    async def app(scope: Scope, receive: Receive, send_: Send) -> None:
        if scope["type"] == "lifespan":
            await lifespan(receive, send_)
            return
        if scope["type"] != "http":
            return
        method, path = scope.get("method", "GET"), scope.get("path", "/")
        if path == "/healthz":
            if method != "GET":
                await send_json(send_, 405, {"error": "method_not_allowed"})
                return
            await send_json(
                send_,
                200,
                {
                    "status": "ok",
                    "draining": supervisor.draining,
                    "runners": len(supervisor.children),
                    "capacity": supervisor.max_runners,
                },
            )
            return
        known = path in ("/v1/runners", "/v1/drain") or path.startswith("/v1/runners/")
        if not known:
            await send_json(send_, 404, {"error": "not_found"})
            return
        try:
            if not bearer_ok(header_map(scope).get("authorization"), token):
                raise WebhookRejected(401, "unauthorized")
            if path == "/v1/runners" and method == "GET":
                await send_json(send_, 200, supervisor.inventory())
            elif path == "/v1/runners" and method == "POST":
                await launch(receive, send_)
            elif path == "/v1/drain" and method == "POST":
                supervisor.drain()
                await send_json(send_, 200, {"draining": True})
            elif path.startswith("/v1/runners/") and method == "DELETE":
                name = path.removeprefix("/v1/runners/")
                if not RUNNER_NAME_RE.match(name):
                    raise WebhookRejected(400, "bad_name")
                if supervisor.stop(name):
                    await send_json(send_, 202, {"status": "stopping"})
                else:
                    await send_json(send_, 404, {"error": "unknown_runner"})
            else:
                await send_json(send_, 405, {"error": "method_not_allowed"})
        except WebhookRejected as exc:
            await send_json(send_, exc.status, {"error": exc.reason})
        except ClientDisconnected:
            return

    return app


def build_agent_app(
    env: Mapping[str, str], *, transport: Transport | None = None
) -> Any:
    """Production wiring used by ``python -m omega_prime.ci_runners agent``."""
    settings = AgentSettings.from_env(env)
    if isinstance(settings.token, SecretFile):
        check_private(settings.token.path, settings.job_uid, settings.job_gid)
    token = settings.token.read()
    if settings.job_uid is None:
        log.warning("OMEGA_CI_AGENT_JOB_UID unset: jobs run as the agent's own user")
    supervisor = Supervisor(
        runner_root=settings.runner_root,
        work_root=settings.work_root,
        max_runners=settings.max_runners,
        notify=ControllerCallback(
            settings.controller_url, token, transport or urllib_transport
        ),
        job_uid=settings.job_uid,
        job_gid=settings.job_gid,
        base_env=env,
        job_hook=settings.job_hook,
        job_policy=settings.job_policy,
        job_repos=settings.job_repos,
    )
    return create_agent_app(supervisor, token=token)


__all__ = ["ControllerCallback", "build_agent_app", "create_agent_app"]
