"""Controller ASGI surface: verified fast acks, bounded admission, callbacks."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from omega_prime.ci_runners.app import (
    CALLBACK_MAX_BYTES,
    build_controller_app,
    create_app,
)
from omega_prime.ci_runners.http import HttpRequest, HttpResponse
from omega_prime.ci_runners.jobs import RunnerState
from omega_prime.ci_runners.settings import MAX_BODY_BYTES, ControllerSettings
from omega_prime.tests.ci_runners._secrets import placeholder, write_secret

from ._fakes import FakeHttp, ListPublisher, resp
from ._rig import JIT, ORG, TOKEN, Rig, routes
from ._support import WEBHOOK_SECRET, asgi_call, encode, job_payload, signed_headers

AUTH = [("Authorization", f"Bearer {TOKEN}")]


def make(rig: Rig) -> Any:
    return create_app(rig.ctrl, webhook_secret=WEBHOOK_SECRET, agent_token=TOKEN)


def hook(app: Any, payload: Any = None, delivery: str = "d-1", **kw: Any) -> Any:
    body = encode(job_payload() if payload is None else payload)
    headers = kw.pop("headers", None) or signed_headers(body, delivery=delivery)
    return asgi_call(app, "POST", "/github/webhook", body=body, headers=headers, **kw)


async def lifespan(app: Any, *messages: str, between: Any = None) -> list[str]:
    inbox = [{"type": m} for m in messages]
    sent: list[str] = []

    async def receive() -> dict[str, str]:
        if sent and between is not None:
            await between()
        return inbox.pop(0)

    async def send(message: dict[str, str]) -> None:
        sent.append(message["type"])

    await app({"type": "lifespan"}, receive, send)
    return sent


def test_requires_secrets() -> None:
    rig = Rig(routes())
    with pytest.raises(ValueError):
        create_app(rig.ctrl, webhook_secret=b"", agent_token=TOKEN)


def test_lifespan_healthz_and_accept() -> None:
    rig = Rig(routes())
    app = make(rig)

    async def main() -> None:
        health = await asgi_call(app, "GET", "/healthz")
        assert health.json() == {
            "status": "ok",
            "ready": False,
            "runners": 0,
            "queued": 0,
        }
        await rig.ctrl.start()
        await rig.ctrl.settle()
        response = await hook(app)
        assert (response.status, response.json()) == (202, {"status": "accepted"})
        again = await hook(app)
        assert again.json() == {"status": "duplicate"}
        await rig.ctrl.settle()
        assert (await asgi_call(app, "GET", "/healthz")).json()["runners"] == 1
        assert await lifespan(app, "lifespan.shutdown") == [
            "lifespan.shutdown.complete"
        ]

    asyncio.run(main())


def test_lifespan_startup_runs_controller() -> None:
    rig = Rig(routes())
    app = make(rig)

    async def main() -> None:
        sent = await lifespan(
            app, "lifespan.startup", "lifespan.shutdown", between=rig.ctrl.settle
        )
        assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]

    asyncio.run(main())
    assert rig.ctrl.ready.is_set()


def test_rejections() -> None:
    rig = Rig(routes())
    app = make(rig)

    async def main() -> None:
        body = encode(job_payload())
        assert (
            await hook(app, headers=signed_headers(body, secret=b"wrong"))
        ).status == 401
        ping = await hook(
            app, {"zen": "hi"}, headers=signed_headers(b'{"zen": "hi"}', event="ping")
        )
        assert ping.json() == {"status": "pong"}
        push = encode({})
        other = await asgi_call(
            app,
            "POST",
            "/github/webhook",
            body=push,
            headers=signed_headers(push, event="push"),
        )
        assert other.status == 202
        big = [("Content-Length", str(MAX_BODY_BYTES + 1)), *signed_headers(body)]
        assert (await hook(app, headers=big)).status == 413
        chunked = await asgi_call(
            app,
            "POST",
            "/github/webhook",
            headers=signed_headers(body),
            chunks=[b"x" * MAX_BODY_BYTES, b"x"],
        )
        assert chunked.status == 413
        assert (await hook(app, disconnect=True)).status == 0
        bad = {"action": "queued", "workflow_job": {"id": "x"}}
        assert (await hook(app, bad, delivery="d-bad")).json() == {
            "error": "bad_payload"
        }
        ignored = await hook(app, job_payload("waiting"), delivery="d-ign")
        assert ignored.json() == {"status": "ignored"}
        assert (await asgi_call(app, "GET", "/nope")).status == 404
        assert (await asgi_call(app, "GET", "/github/webhook")).status == 405
        assert (await asgi_call(app, "POST", "/healthz")).status == 405
        await app({"type": "websocket"}, None, None)

    asyncio.run(main())
    assert rig.ctrl.pending == 0


def test_saturated_queue_returns_503_without_committing_delivery() -> None:
    rig = Rig(routes(), max_pending=1)
    app = make(rig)

    async def main() -> None:
        assert (await hook(app, job_payload(job_id=1), delivery="d-1")).status == 202
        busy = await hook(app, job_payload(job_id=2), delivery="d-2")
        assert (busy.status, busy.json()) == (503, {"error": "busy"})
        await rig.ctrl.start()
        await rig.ctrl.settle()
        redelivered = await hook(app, job_payload(job_id=2), delivery="d-2")
        assert redelivered.json() == {"status": "accepted"}
        await rig.ctrl.settle()
        await rig.ctrl.stop()

    asyncio.run(main())
    assert len(rig.scaler.runners) == 2


def test_webhook_ack_does_not_wait_for_github() -> None:
    """A wedged generate-jitconfig call must not delay the 202."""
    gate = threading.Event()
    http = routes()

    def blocked(_req: HttpRequest) -> HttpResponse:
        assert gate.wait(10)
        return resp(201, {"runner": {"id": 42}, "encoded_jit_config": JIT})

    http.on("POST", f"{ORG}/generate-jitconfig", blocked)
    rig = Rig(http)
    app = make(rig)

    async def main() -> None:
        await rig.ctrl.start()
        await rig.ctrl.settle()
        response = await asyncio.wait_for(hook(app), timeout=2)
        assert response.status == 202
        for _ in range(100):
            if http.paths("POST"):
                break
            await asyncio.sleep(0.01)
        assert http.paths("POST") == [f"{ORG}/generate-jitconfig"]
        assert rig.only().state is RunnerState.STARTING
        gate.set()
        await rig.ctrl.settle()
        assert rig.only().state is RunnerState.IDLE
        await rig.ctrl.stop()

    try:
        asyncio.run(main())
    finally:
        gate.set()


def test_agent_exit_callback() -> None:
    rig = Rig(routes())
    app = make(rig)

    def call(payload: Any, headers: Any = AUTH, raw: bytes | None = None) -> Any:
        body = raw if raw is not None else encode(payload)
        return asgi_call(app, "POST", "/agent/events", body=body, headers=headers)

    async def main() -> None:
        await rig.ctrl.start()
        await rig.ctrl.settle()
        await hook(app)
        await rig.ctrl.settle()
        runner = rig.only()
        event = {
            "type": "agent.runner_exited",
            "name": runner.name,
            "generation": runner.generation,
            "exit_code": 0,
        }
        assert (
            await call(event, headers=[("Authorization", "Bearer nope")])
        ).status == 401
        assert (await call(event, headers=[])).status == 401
        assert (await call(None, raw=b"{")).json() == {"error": "bad_json"}
        assert (await call(None, raw=b"\xff")).status == 400
        assert (await call([1])).json() == {"error": "bad_event"}
        for broken in (
            {"name": "../x"},
            {"generation": ""},
            {"exit_code": "0"},
            {"exit_code": True},
        ):
            assert (await call(event | broken)).status == 400
        big = encode(event | {"pad": "x" * CALLBACK_MAX_BYTES})
        assert (await call(None, raw=big)).status == 413
        stale = await call(event | {"generation": "old"})
        assert (stale.status, stale.json()) == (404, {"error": "unknown_runner"})
        assert (await call(event)).status == 202
        await rig.ctrl.settle()
        assert runner.name not in rig.scaler.runners
        await rig.ctrl.stop()

    asyncio.run(main())


def _env(tmp_path: Path) -> dict[str, str]:
    for name, value in (
        ("hook", "file-webhook-secret"),
        ("desk", TOKEN),
        ("pat", placeholder("pat")),
    ):
        write_secret(tmp_path / name, value)
    return {
        "OMEGA_CI_ORG": "acme",
        "OMEGA_CI_RUNNER_GROUP_ID": "7",
        "OMEGA_CI_WEBHOOK_SECRET_FILE": str(tmp_path / "hook"),
        "OMEGA_CI_DESKTOP_URL": "https://desktop.example.test",
        "OMEGA_CI_DESKTOP_TOKEN_FILE": str(tmp_path / "desk"),
        "OMEGA_CI_GITHUB_PAT_FILE": str(tmp_path / "pat"),
    }


def test_build_controller_app_from_secret_files(tmp_path: Path) -> None:
    settings = ControllerSettings.from_env(_env(tmp_path))
    http = routes()
    app = build_controller_app(settings, transport=http, publisher=ListPublisher())

    async def main() -> None:
        body = encode(job_payload())
        signed = signed_headers(body, secret=b"file-webhook-secret")
        response = await asgi_call(
            app, "POST", "/github/webhook", body=body, headers=signed
        )
        assert response.status == 202
        unsigned = signed_headers(body)
        assert (
            await asgi_call(app, "POST", "/github/webhook", body=body, headers=unsigned)
        ).status == 401

    asyncio.run(main())


def test_build_controller_app_default_publisher(tmp_path: Path) -> None:
    env = _env(tmp_path) | {
        "OMEGA_CI_EVENTS_PATH": str(tmp_path / "ev" / "events.jsonl")
    }
    app = build_controller_app(ControllerSettings.from_env(env), transport=FakeHttp())
    assert callable(app)


def test_non_ascii_signature_is_401_not_500() -> None:
    """Raw UTF-8 bytes in the header arrive latin-1 decoded (non-ASCII str)."""
    rig = Rig(routes())
    app = make(rig)
    body = encode(job_payload())
    headers = [
        (k, v) for k, v in signed_headers(body) if k.lower() != "x-hub-signature-256"
    ]

    async def main() -> None:
        for bad in ("sha256=\u00e9", "sha256=" + "\u00e9" * 64):
            response = await asgi_call(
                app,
                "POST",
                "/github/webhook",
                body=body,
                headers=[*headers, ("X-Hub-Signature-256", bad)],
            )
            assert response.status == 401

    asyncio.run(main())


def test_injected_empty_dedupe_cache_is_used() -> None:
    from omega_prime.ci_runners.webhook import DeliveryLRU

    rig = Rig(routes())
    cache = DeliveryLRU(size=4)
    assert len(cache) == 0  # empty, hence falsy: the old `or` dropped it
    app = create_app(
        rig.ctrl, webhook_secret=WEBHOOK_SECRET, agent_token=TOKEN, dedupe=cache
    )

    async def main() -> None:
        await rig.ctrl.start()
        await rig.ctrl.settle()
        assert (await hook(app, delivery="d-shared")).status == 202
        await rig.ctrl.stop()

    asyncio.run(main())
    assert cache.seen("d-shared")
