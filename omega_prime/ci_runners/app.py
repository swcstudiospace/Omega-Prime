"""Framework-free ASGI app for the always-on controller.

Routes: ``GET /healthz``, ``POST /github/webhook`` (HMAC), ``POST
/agent/events`` (bearer) plus lifespan. A verified job is acknowledged with
202 as soon as it is queued, before any GitHub or Desktop call. Queue
admission happens before the delivery id is committed, so a 503 lets GitHub
redeliver.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from omega_prime.ci_runners.controller import Controller
from omega_prime.ci_runners.jobs import PayloadError, parse_workflow_job
from omega_prime.ci_runners.settings import RUNNER_NAME_RE
from omega_prime.ci_runners.webhook import (
    ClientDisconnected,
    DeliveryLRU,
    Receive,
    Scope,
    Send,
    WebhookRejected,
    bearer_ok,
    header_map,
    read_body,
    send_json,
    verify_delivery,
)

CALLBACK_MAX_BYTES = 4096
EXITED = "agent.runner_exited"

ASGIApp = Callable[[Scope, Receive, Send], Any]


def create_app(
    controller: Controller,
    *,
    webhook_secret: bytes,
    agent_token: str,
    dedupe: DeliveryLRU | None = None,
) -> ASGIApp:
    if not webhook_secret or not agent_token:
        raise ValueError("webhook secret and agent token are required")
    seen = dedupe if dedupe is not None else DeliveryLRU()

    async def lifespan(receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await controller.start()
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await controller.stop()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def webhook(scope: Scope, receive: Receive, send: Send) -> None:
        headers = header_map(scope)
        body = await read_body(receive)
        delivery = verify_delivery(headers, body, webhook_secret)
        if delivery.event == "ping":
            await send_json(send, 200, {"status": "pong"})
            return
        if seen.seen(delivery.delivery_id):
            await send_json(send, 202, {"status": "duplicate"})
            return
        try:
            job = parse_workflow_job(delivery.payload)
        except PayloadError:
            raise WebhookRejected(400, "bad_payload") from None
        if job is None:
            seen.commit(delivery.delivery_id)
            await send_json(send, 202, {"status": "ignored"})
            return
        if not controller.offer_job(job):
            await send_json(send, 503, {"error": "busy"})
            return
        seen.commit(delivery.delivery_id)
        await send_json(send, 202, {"status": "accepted"})

    async def callback(scope: Scope, receive: Receive, send: Send) -> None:
        headers = header_map(scope)
        if not bearer_ok(headers.get("authorization"), agent_token):
            raise WebhookRejected(401, "unauthorized")
        body = await read_body(receive, CALLBACK_MAX_BYTES)
        try:
            data = json.loads(body)
        except (UnicodeDecodeError, ValueError):
            raise WebhookRejected(400, "bad_json") from None
        if not isinstance(data, dict) or data.get("type") != EXITED:
            raise WebhookRejected(400, "bad_event")
        name, generation, code = (
            data.get("name"),
            data.get("generation"),
            data.get("exit_code"),
        )
        if (
            not isinstance(name, str)
            or not RUNNER_NAME_RE.match(name)
            or not isinstance(generation, str)
            or not generation
            or not isinstance(code, int)
            or isinstance(code, bool)
        ):
            raise WebhookRejected(400, "bad_event")
        if not controller.runner_exited(name, generation, code):
            await send_json(send, 404, {"error": "unknown_runner"})
            return
        await send_json(send, 202, {"status": "accepted"})

    routes = {
        ("POST", "/github/webhook"): webhook,
        ("POST", "/agent/events"): callback,
    }

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        method, path = scope.get("method", "GET"), scope.get("path", "/")
        if path == "/healthz" and method == "GET":
            await send_json(
                send,
                200,
                {
                    "status": "ok",
                    "ready": controller.ready.is_set(),
                    "runners": len(controller.scaler.runners),
                    "queued": len(controller.scaler.queued),
                },
            )
            return
        handler = routes.get((method, path))
        if handler is None:
            known = any(p == path for _, p in routes) or path == "/healthz"
            await send_json(send, 405 if known else 404, {"error": "not_found"})
            return
        try:
            await handler(scope, receive, send)
        except WebhookRejected as exc:
            await send_json(send, exc.status, {"error": exc.reason})
        except ClientDisconnected:
            return

    return app


def build_controller_app(settings: Any, **overrides: Any) -> ASGIApp:
    """Wire production adapters from validated :class:`ControllerSettings`."""
    from omega_prime.ci_runners.demand import DemandStore
    from omega_prime.ci_runners.desktop import DesktopClient
    from omega_prime.ci_runners.github import GitHubClient
    from omega_prime.ci_runners.http import urllib_transport
    from omega_prime.ci_runners.publisher import make_publisher
    from omega_prime.ci_runners.scaler import Scaler

    transport = overrides.pop("transport", urllib_transport)
    webhook_secret = settings.webhook_secret_file.read()
    desktop_token = settings.desktop_token_file.read()
    controller = Controller(
        scaler=Scaler(
            labels=settings.labels,
            repos=settings.repos,
            max_runners=settings.max_runners,
            idle_grace_s=settings.idle_grace_s,
        ),
        github=GitHubClient.from_settings(settings, transport),
        desktop=DesktopClient(
            settings.desktop_url,
            lambda: desktop_token,
            transport,
            budget_s=settings.wake_budget_s,
        ),
        publisher=overrides.pop("publisher", None)
        or make_publisher(settings.publisher, settings.events_path),
        extra_secrets=lambda: (webhook_secret, desktop_token),
        demand=DemandStore(settings.demand_path),
    )
    return create_app(
        controller, webhook_secret=webhook_secret.encode(), agent_token=desktop_token
    )


__all__ = ["CALLBACK_MAX_BYTES", "build_controller_app", "create_app"]
