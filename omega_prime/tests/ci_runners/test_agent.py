"""Runner-agent ASGI contract, driven in-process against real fake children."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from omega_prime.ci_runners import __main__ as cli
from omega_prime.ci_runners.agent.app import (
    ControllerCallback,
    build_agent_app,
    create_agent_app,
)
from omega_prime.ci_runners.agent.supervisor import Supervisor, UnsafeCredentialFile
from omega_prime.ci_runners.desktop import DesktopClient, LaunchOutcome
from omega_prime.ci_runners.http import HttpRequest, HttpResponse
from omega_prime.ci_runners.settings import MAX_BODY_BYTES, SettingsError

from ._fakes import FakeHttp, resp
from ._runner import AGENT_TOKEN, JIT, Exits, runner_root, supervisor
from ._support import asgi_call, encode

NAME = "omega-ci-101-abcd"
AUTH = [("Authorization", f"Bearer {AGENT_TOKEN}")]
LAUNCH = {"name": NAME, "generation": "gen-a", "jitconfig": JIT}


def gated_exit(gate: Path, code: int = 0) -> str:
    """A run.sh tail that stays alive until ``gate`` exists, then exits.

    Keeps "child still running" assertions deterministic: a replay is only
    REPLAYED while the child lives, and FINISHED once it has exited.
    """
    return f"while [ ! -e '{gate}' ]; do sleep 0.01; done\nexit {code}"


def rig(
    tmp_path: Path, tail: str = "exec sleep 30", **kw: Any
) -> tuple[Any, Supervisor, Exits]:
    exits = Exits()
    s = supervisor(tmp_path, runner_root(tmp_path, tail=tail), exits, **kw)
    return create_agent_app(s, token=AGENT_TOKEN), s, exits


def post(app: Any, payload: Any, headers: Any = AUTH, raw: bytes | None = None) -> Any:
    body = raw if raw is not None else encode(payload)
    return asgi_call(app, "POST", "/v1/runners", body=body, headers=headers)


def test_tracer_launch_replay_conflict_exit(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    gate = tmp_path / "release"
    app, s, exits = rig(tmp_path, tail=gated_exit(gate))
    bodies: list[bytes] = []

    async def main() -> None:
        s.prepare()
        first = await post(app, LAUNCH)
        replay = await post(app, LAUNCH)
        conflict = await post(app, LAUNCH | {"generation": "gen-b"})
        bodies.extend([first.body, replay.body, conflict.body])
        assert (first.status, first.json()) == (201, {"status": "accepted"})
        assert (replay.status, replay.json()) == (200, {"status": "replayed"})
        assert (conflict.status, conflict.json()) == (409, {"error": "conflict"})
        gate.touch()
        await exits.wait()

    asyncio.run(main())
    assert exits.payloads == [
        {
            "type": "agent.runner_exited",
            "name": NAME,
            "generation": "gen-a",
            "exit_code": 0,
        }
    ]
    assert all(JIT.encode() not in b for b in bodies) and JIT not in caplog.text


def test_inventory_drain_delete_and_health(tmp_path: Path) -> None:
    app, _s, exits = rig(tmp_path, max_runners=1)

    async def main() -> None:
        assert (await post(app, LAUNCH)).status == 201
        full = await post(app, LAUNCH | {"name": "omega-ci-102-b"})
        assert (full.status, full.json()) == (429, {"error": "capacity"})
        rows = (await asgi_call(app, "GET", "/v1/runners", headers=AUTH)).json()
        assert [set(r) for r in rows] == [{"name", "generation", "status", "pid"}]
        assert rows[0]["generation"] == "gen-a" and JIT not in json.dumps(rows)
        health = (await asgi_call(app, "GET", "/healthz")).json()
        assert health == {
            "status": "ok",
            "draining": False,
            "runners": 1,
            "capacity": 1,
        }
        drained = await asgi_call(app, "POST", "/v1/drain", headers=AUTH)
        assert (drained.status, drained.json()) == (200, {"draining": True})
        refused = await post(app, LAUNCH | {"name": "omega-ci-103-c"})
        assert (refused.status, refused.json()) == (409, {"error": "draining"})
        gone = await asgi_call(app, "DELETE", f"/v1/runners/{NAME}", headers=AUTH)
        assert gone.status == 202
        await exits.wait()
        missing = await asgi_call(app, "DELETE", f"/v1/runners/{NAME}", headers=AUTH)
        assert missing.status == 404
        bad = await asgi_call(app, "DELETE", "/v1/runners/..%2Fetc", headers=AUTH)
        assert bad.status == 400

    asyncio.run(main())


def test_auth_routes_and_body_limits(tmp_path: Path) -> None:
    app, s, _ = rig(tmp_path)

    async def main() -> None:
        for method, path in (
            ("GET", "/v1/runners"),
            ("POST", "/v1/runners"),
            ("POST", "/v1/drain"),
            ("DELETE", f"/v1/runners/{NAME}"),
        ):
            for headers in ([], [("Authorization", "Bearer wrong")]):
                assert (
                    await asgi_call(app, method, path, headers=headers)
                ).status == 401
        assert (await asgi_call(app, "GET", "/nope", headers=AUTH)).status == 404
        assert (await asgi_call(app, "PUT", "/v1/runners", headers=AUTH)).status == 405
        assert (await asgi_call(app, "POST", "/healthz")).status == 405
        assert (await post(app, None, raw=b"{")).json() == {"error": "bad_json"}
        assert (await post(app, None, raw=b"\xff")).status == 400
        for broken in (
            [LAUNCH],
            LAUNCH | {"extra": "x"},
            {"name": NAME, "generation": "g"},
            LAUNCH | {"generation": 1},
            LAUNCH | {"name": "nope"},
            LAUNCH | {"jitconfig": "a b"},
        ):
            assert (await post(app, broken)).status == 400
        huge = [("Content-Length", str(MAX_BODY_BYTES + 1)), *AUTH]
        assert (await post(app, LAUNCH, headers=huge)).status == 413
        chunked = await asgi_call(
            app,
            "POST",
            "/v1/runners",
            headers=AUTH,
            chunks=[b"x" * MAX_BODY_BYTES, b"x"],
        )
        assert chunked.status == 413
        lost = await asgi_call(
            app, "POST", "/v1/runners", headers=AUTH, disconnect=True
        )
        assert lost.status == 0
        await app({"type": "websocket"}, None, None)

    asyncio.run(main())
    assert s.children == {}
    with pytest.raises(ValueError):
        create_agent_app(s, token="")


def test_lifespan_prepares_and_shuts_down(tmp_path: Path) -> None:
    app, s, exits = rig(tmp_path)
    stale = tmp_path / "work" / "omega-ci-9-old"
    stale.mkdir(parents=True)

    async def main() -> None:
        inbox = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        sent: list[str] = []

        async def receive() -> dict[str, str]:
            if sent:
                assert not stale.exists()
                assert (await post(app, LAUNCH)).status == 201
            return inbox.pop(0)

        async def send(message: dict[str, Any]) -> None:
            sent.append(message["type"])

        await app({"type": "lifespan"}, receive, send)
        assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]

    asyncio.run(main())
    assert [p["name"] for p in exits.payloads] == [NAME] and s.children == {}


def test_controller_desktop_client_round_trip(tmp_path: Path) -> None:
    """02's DesktopClient against this agent: the shared contract agrees."""
    gate = tmp_path / "release"
    app, _s, exits = rig(tmp_path, tail=gated_exit(gate))
    loop_box: list[asyncio.AbstractEventLoop] = []

    def transport(req: HttpRequest) -> HttpResponse:
        path = req.url.removeprefix("https://desktop.example.test")
        headers = [(k, v) for k, v in req.headers.items()]
        future = asyncio.run_coroutine_threadsafe(
            asgi_call(app, req.method, path, body=req.body or b"", headers=headers),
            loop_box[0],
        )
        r = future.result(10)
        return HttpResponse(r.status, r.headers, r.body)

    async def main() -> None:
        loop_box.append(asyncio.get_running_loop())
        client = DesktopClient(
            "https://desktop.example.test", lambda: AGENT_TOKEN, transport
        )
        result = await client.launch(NAME, "gen-a", JIT)
        assert result.outcome is LaunchOutcome.ACCEPTED
        # Child alive: the retry is a live replay.
        assert (await client.launch(NAME, "gen-a", JIT)).outcome is (
            LaunchOutcome.REPLAYED
        )
        gate.touch()
        await exits.wait()
        # Child gone: the retry reports the finished outcome and exit code.
        finished = await client.launch(NAME, "gen-a", JIT)
        assert (finished.outcome, finished.exit_code) == (LaunchOutcome.FINISHED, 0)
        assert await client.find(NAME, "gen-a") is False
        assert await client.stop(NAME) is True  # 404 → already gone

    asyncio.run(main())


def test_retry_after_exit_reports_finished_with_exit_code(tmp_path: Path) -> None:
    app, s, exits = rig(tmp_path, tail="exit 5")

    async def main() -> None:
        s.prepare()
        assert (await post(app, LAUNCH)).status == 201
        await exits.wait()
        await asyncio.sleep(0)
        retry = await post(app, LAUNCH)
        assert (retry.status, retry.json()) == (
            200,
            {"status": "finished", "exit_code": 5},
        )

    asyncio.run(main())
    assert len(exits.payloads) == 1  # the single-use JIT config never reran


def test_controller_callback_posts_once() -> None:
    http = FakeHttp().on("POST", "/agent/events", resp(202), resp(404))
    callback = ControllerCallback("https://ctl.example.test/", AGENT_TOKEN, http)
    payload = {
        "type": "agent.runner_exited",
        "name": NAME,
        "generation": "g",
        "exit_code": 0,
    }
    asyncio.run(callback(payload))
    asyncio.run(callback(payload))
    assert [c.url for c in http.calls] == ["https://ctl.example.test/agent/events"] * 2
    assert http.calls[0].headers["Authorization"] == f"Bearer {AGENT_TOKEN}"
    assert json.loads(http.calls[0].body or b"") == payload
    assert http.calls[0].timeout == 10.0 and AGENT_TOKEN not in repr(callback)


def _agent_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "OMEGA_CI_CONTROLLER_URL": "https://ctl.example.test",
        "OMEGA_CI_AGENT_RUNNER_ROOT": str(tmp_path / "runner"),
        "OMEGA_CI_AGENT_WORK_ROOT": str(tmp_path / "work"),
        **extra,
    }


def test_build_agent_app_token_sources(tmp_path: Path) -> None:
    token = tmp_path / "agent.token"
    token.write_text(AGENT_TOKEN + "\n")
    token.chmod(0o600)
    app = build_agent_app(_agent_env(tmp_path, OMEGA_CI_AGENT_TOKEN_FILE=str(token)))

    async def main() -> None:
        assert (await asgi_call(app, "GET", "/v1/runners", headers=AUTH)).json() == []

    asyncio.run(main())
    railway = build_agent_app(_agent_env(tmp_path, OMEGA_CI_AGENT_TOKEN=AGENT_TOKEN))
    assert callable(railway)
    token.chmod(0o644)
    with pytest.raises(UnsafeCredentialFile):
        build_agent_app(_agent_env(tmp_path, OMEGA_CI_AGENT_TOKEN_FILE=str(token)))
    with pytest.raises(SettingsError):
        build_agent_app(_agent_env(tmp_path))


def test_cli_agent_dispatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    served: list[tuple[Any, str, int]] = []
    env = _agent_env(tmp_path, OMEGA_CI_AGENT_TOKEN=AGENT_TOKEN)
    assert cli.main(["agent"], env=env, serve_fn=lambda *a: served.append(a)) == 0
    assert served[0][1:] == ("0.0.0.0", 8787)
    assert cli.main(["agent"], env={}, serve_fn=lambda *a: None) == 2
    assert AGENT_TOKEN not in capsys.readouterr().err
