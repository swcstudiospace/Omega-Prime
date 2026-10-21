# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Composite fake-backed integration tests for the task dashboard."""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from typing import Any

from starlette.testclient import TestClient
from starlette.types import Message, Scope

from omega_prime.dashboard.app import create_app
from omega_prime.dashboard.collectors.agentos import make_agentos_collector
from omega_prime.dashboard.collectors.command_log import make_command_log_collector
from omega_prime.dashboard.collectors.github import make_github_collector
from omega_prime.dashboard.collectors.herdr import make_herdr_collector
from omega_prime.dashboard.collectors.omega_jobs import make_omega_jobs_collector
from omega_prime.dashboard.collectors.omp_sessions import make_omp_sessions_collector
from omega_prime.dashboard.collectors.podman import make_podman_collector
from omega_prime.dashboard.collectors.procs import make_procs_collector
from omega_prime.dashboard.collectors.sandbox_files import make_sandbox_files_collector
from omega_prime.dashboard.collectors.tasks_json import make_tasks_json_collector
from omega_prime.dashboard.collectors.wiring import make_wiring_collector
from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.poller import CollectorSpec, DashboardPoller
from omega_prime.dashboard.status_file import write_tasks

NOW = datetime(2026, 10, 21, tzinfo=UTC)
_SESSION_SECRET = "sk" + "-" + token_hex(9)
_JOB_SECRET = "sk" + "-" + token_hex(9)
_CMD_TOKEN_VALUE = token_hex(9)
_PROC_TOKEN = "gh" + "p_" + "x" * 21
_GH_TOKEN = "gh" + "p_" + "x" * 21


def _spec(source: str, collector: Any, interval: float = 0.2) -> CollectorSpec:
    return CollectorSpec(source, collector, interval, 1.0)


def _fixture_tree(tmp_path: Path) -> list[CollectorSpec]:
    home = tmp_path / "home"
    sessions = home / ".omp" / "agent" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "session.jsonl").write_text(
        json.dumps({"type": "message", "message": "todo: integrate"}) + "\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    phase = runs / "p1"
    phase.mkdir(parents=True)
    (phase / "phase.log").write_text("working on integration\n", encoding="utf-8")
    (phase / "sol.done").write_text("done\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    sandbox = tmp_path / "sandbox"
    (sandbox / ".state" / "e2e").mkdir(parents=True)
    (sandbox / ".state" / "receipts").mkdir(parents=True)
    (sessions / "session.jsonl").write_text(
        json.dumps(
            {
                "type": "todo",
                "tool": "todo",
                "done": 1,
                "total": 2,
                "text": _SESSION_SECRET,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (sandbox / ".state" / "e2e" / "run.jsonl").write_text(
        '{"status":"running","name":"e2e"}\n', encoding="utf-8"
    )
    (sandbox / ".state" / "receipts" / "receipt.json").write_text(
        '{"id":"receipt-one","status":"done"}', encoding="utf-8"
    )
    jobs = home / ".omega-prime" / "jobs" / "job-one"
    jobs.mkdir(parents=True)
    (jobs / "session.json").write_text(
        json.dumps(
            {
                "job_id": "job-one",
                "stage": "working",
                "final_text": "safe first line\n" + _JOB_SECRET,
            }
        ),
        encoding="utf-8",
    )
    command_log = home / ".omega-prime" / "command-log.jsonl"
    command_log.parent.mkdir(parents=True, exist_ok=True)
    command_log.write_text(
        json.dumps({"cmd": "echo TOKEN=" + _CMD_TOKEN_VALUE, "exit_code": 0}) + "\n",
        encoding="utf-8",
    )
    tasks_path = home / ".omega-prime" / "dashboard" / "tasks.json"
    tasks_path.parent.mkdir(parents=True)
    write_tasks(
        {
            "version": 1,
            "updated_at": NOW.isoformat(),
            "tasks": [],
            "waiting_on_ove": [],
            "finished": [],
        },
        path=tasks_path,
    )
    agentos = tmp_path / "agentos"
    agentos.mkdir()
    (agentos / "gsd-routing.json").write_text('{"routes":[{}]}', encoding="utf-8")
    proc = tmp_path / "proc" / "123"
    proc.mkdir(parents=True)
    (proc / "cmdline").write_bytes(
        b"omega\x00--mode\x00worker\x00--token\x00" + _PROC_TOKEN.encode()
    )
    (proc / "stat").write_text(
        "123 (omega) S 1 1 1 0 0 0 0 0 0 0 0 0 0 0 0 1 0 1 0\n", encoding="utf-8"
    )
    herdr = make_herdr_collector(
        runner=lambda _argv: json.dumps(
            {
                "result": {
                    "snapshot": {
                        "panes": [
                            {
                                "pane_id": "pane1",
                                "name": "integration",
                                "agent_status": "working",
                            }
                        ]
                    }
                }
            }
        )
    )
    podman = make_podman_collector(runner=lambda _argv: "[]")
    github = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": _GH_TOKEN},
        transport=lambda url, _headers: (
            200,
            json.dumps(
                [
                    {
                        "number": 2,
                        "title": "PR integration",
                        "state": "open",
                        "html_url": "https://example.invalid/pr/2",
                        "head": {"sha": "abc", "ref": "feature"},
                        "user": {"login": "author"},
                        "created_at": NOW.isoformat(),
                        "updated_at": NOW.isoformat(),
                    }
                ]
                if "pulls" in url
                else {"check_runs": [{"name": "CI", "conclusion": "success"}]}
                if "check-runs" in url
                else [{"body": "Confidence score: 5/5"}]
            ).encode(),
        ),
        now_fn=lambda: NOW,
    )
    return [
        _spec(
            "omp_sessions",
            make_omp_sessions_collector(root=sessions, now_fn=lambda: NOW),
        ),
        _spec(
            "wiring",
            make_wiring_collector(
                repo_root=repo,
                runs_root=runs,
                now_fn=lambda: NOW,
                runner=lambda _argv: "",
            ),
        ),
        _spec(
            "sandbox",
            make_sandbox_files_collector(sandbox_root=sandbox, now_fn=lambda: NOW),
        ),
        _spec(
            "omega_jobs",
            make_omega_jobs_collector(jobs_root=jobs.parent, now_fn=lambda: NOW),
        ),
        _spec(
            "command_log",
            make_command_log_collector(log_path=command_log, now_fn=lambda: NOW),
        ),
        _spec(
            "tasks_json",
            make_tasks_json_collector(tasks_path=tasks_path, now_fn=lambda: NOW),
        ),
        _spec(
            "agentos", make_agentos_collector(agentos_root=agentos, now_fn=lambda: NOW)
        ),
        _spec(
            "procs",
            make_procs_collector(
                proc_root=proc.parent, now_fn=lambda: NOW, clock_hz=100, boot_time=0
            ),
        ),
        _spec("herdr", herdr),
        _spec("podman", podman),
        _spec("github", github),
        CollectorSpec("fleet", lambda: SourceResult("fleet", "disabled"), 30.0, 1.0),
    ]


def test_composite_snapshot_guards_redaction_and_sse(tmp_path: Path) -> None:
    poller = DashboardPoller(_fixture_tree(tmp_path), clock=lambda: NOW)
    asyncio.run(poller.refresh_now())
    app = create_app(poller)
    with TestClient(app) as client:
        response = client.get("/api/snapshot", headers={"host": "127.0.0.1:7421"})
        assert response.status_code == 200
        payload = response.json()
        assert {
            "generated_at",
            "active",
            "pr_gate",
            "waiting_on_ove",
            "finished",
            "sources",
        } <= payload.keys()
        statuses = {key: value["status"] for key, value in payload["sources"].items()}
        assert statuses["fleet"] == "disabled"
        assert statuses["github"] == "ok"
        assert any(row["id"].startswith("herdr:") for row in payload["active"])
        assert "content-security-policy" in response.headers
        assert response.headers["x-content-type-options"] == "nosniff"
        html = client.get("/", headers={"host": "127.0.0.1:7421"}).text
        assert "Command Center · Omega Prime" in html
        legacy = client.get("/legacy", headers={"host": "127.0.0.1:7421"}).text
        assert "Omega Prime — Task Dashboard" in legacy
        for secret in (
            _SESSION_SECRET,
            _JOB_SECRET,
            _CMD_TOKEN_VALUE,
            _PROC_TOKEN,
            _GH_TOKEN,
        ):
            assert secret not in response.text
            assert secret not in html
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            for path in (
                "/",
                "/cc/",
                "/legacy",
                "/cc/assets/index-DIGTqjrX.css",
                "/api/snapshot",
                "/healthz",
            ):
                assert (
                    client.request(
                        method, path, headers={"host": "127.0.0.1:7421"}
                    ).status_code
                    == 405
                )
        assert client.get("/", headers={"host": "evil.example:7421"}).status_code == 403
        assert client.get("/", headers={"host": "127.0.0.1:9999"}).status_code == 403
    first_hash = poller.snapshot_hash()
    asyncio.run(poller.refresh_now())
    assert poller.snapshot_hash() == first_hash


def test_tasks_writer_bridge_refreshes_without_reloading(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    collector = make_tasks_json_collector(tasks_path=path, now_fn=lambda: NOW)
    poller = DashboardPoller([_spec("tasks_json", collector)], clock=lambda: NOW)
    asyncio.run(poller.refresh_now())
    app = create_app(poller)
    with TestClient(app) as client:
        writer_started = __import__("time").monotonic()
        write_tasks(
            {
                "version": 1,
                "updated_at": NOW.isoformat(),
                "tasks": [
                    {
                        "id": "new-task",
                        "title": "New task",
                        "state": "working",
                        "owner": {"kind": "agent", "ref": "agent-one"},
                    }
                ],
                "waiting_on_ove": [
                    {"id": "wait-one", "ask": "Waiting", "blocks": "task-one"}
                ],
                "finished": [
                    {
                        "id": "done-one",
                        "title": "Finished",
                        "finished_at": NOW.isoformat(),
                        "result": "ok",
                    }
                ],
            },
            path=path,
        )
        asyncio.run(poller.refresh_now())
        snapshot = client.get(
            "/api/snapshot", headers={"host": "127.0.0.1:7421"}
        ).json()
        assert "new-task" in json.dumps(snapshot)
        assert "wait-one" in json.dumps(snapshot)
        assert "done-one" in json.dumps(snapshot)
        assert __import__("time").monotonic() - writer_started <= 5.0


def test_degraded_sources_do_not_hide_healthy_snapshot(tmp_path: Path) -> None:

    def good() -> SourceResult:
        return SourceResult(
            "omp_sessions",
            "ok",
            items=[{"kind": "omp_session", "session_id": "healthy"}],
        )

    specs = [
        _spec("omp_sessions", good),
        _spec(
            "herdr",
            make_herdr_collector(
                runner=lambda _argv: (_ for _ in ()).throw(
                    subprocess.TimeoutExpired("herdr", 1)
                )
            ),
        ),
        _spec(
            "podman",
            make_podman_collector(
                runner=lambda _argv: (_ for _ in ()).throw(FileNotFoundError())
            ),
        ),
        _spec("github", lambda: SourceResult("github", "error", error="fake failure")),
    ]
    poller = DashboardPoller(specs, clock=lambda: NOW)
    asyncio.run(poller.refresh_now())
    snapshot = poller.snapshot()
    assert snapshot is not None
    assert poller.sources_status()["omp_sessions"]["status"] == "ok"
    assert poller.sources_status()["herdr"]["status"] == "unavailable"
    assert poller.sources_status()["podman"]["status"] == "unavailable"
    assert poller.sources_status()["github"]["status"] == "error"


def test_snapshot_api_and_named_sse_share_redacted_nested_github_data() -> None:
    secret = "ghp_" + token_hex(16)
    github_payload = {
        "repositories": [
            {
                "name": "omes-bot",
                "visibility": "private",
                "description": "token=" + secret,
                "access_token": secret,
            }
        ],
        "ci_cd": [
            {
                "name": "check",
                "conclusion": "success",
                "details_url": "https://example.invalid/check?token=" + secret,
            }
        ],
        "recent_merges": [
            {
                "title": "safe merge",
                "comment": "credential " + secret,
                "url": "https://example.invalid/merge/" + secret,
            }
        ],
        "meta": {"fetched_at": NOW.isoformat(), "age_seconds": 0},
        "ignored_future_field": secret,
    }
    poller = DashboardPoller(
        [
            _spec(
                "github",
                lambda: SourceResult(
                    "github",
                    "ok",
                    items=[
                        {
                            "kind": "github_pr",
                            "repo": "swcstudiospace/omes-bot",
                            "number": 47,
                            "title": "aggregate PR",
                        }
                    ],
                    extra={"command_center": github_payload},
                ),
            )
        ],
        clock=lambda: NOW,
    )
    asyncio.run(poller.refresh_now())
    app = create_app(poller)
    host = [(b"host", b"127.0.0.1:7421")]

    with TestClient(app) as client:
        response = client.get("/api/snapshot", headers={"host": "127.0.0.1:7421"})
        assert response.status_code == 200
        snapshot = response.json()
        assert set(snapshot) == {
            "generated_at",
            "sources",
            "active",
            "pr_gate",
            "waiting_on_ove",
            "finished",
            "idle_sessions",
        }
        nested = snapshot["sources"]["github"]["command_center"]
        assert set(nested) == {"repositories", "ci_cd", "recent_merges", "meta"}
        assert snapshot["pr_gate"][0]["title"] == "aggregate PR"

    async def read_one_event() -> str:
        chunks: asyncio.Queue[str] = asyncio.Queue()

        async def receive() -> Message:
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            body = message.get("body", b"")
            if isinstance(body, bytes) and body:
                chunks.put_nowait(body.decode("utf-8"))

        scope: Scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/stream",
            "raw_path": b"/api/stream",
            "headers": host,
            "query_string": b"",
        }
        stream_task = asyncio.create_task(app(scope, receive, send))
        received = ""
        try:
            async with asyncio.timeout(2):
                while "data: " not in received:
                    received += await chunks.get()
        finally:
            stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stream_task
        return received

    event = asyncio.run(read_one_event())
    assert "retry: 3000" in event
    assert "event: snapshot" in event
    frame = next(
        line.removeprefix("data: ")
        for line in event.splitlines()
        if line.startswith("data: ")
    )
    streamed = json.loads(frame)
    assert streamed == snapshot
    assert streamed["sources"]["github"]["command_center"] == nested
    assert secret not in response.text
    assert secret not in event
    assert "ignored_future_field" not in event
