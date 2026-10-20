# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Regressions found by the first live run of the dashboard on the box."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from omega_prime.dashboard.app import _SSEResponse, create_app
from omega_prime.dashboard.collectors.wiring import make_wiring_collector
from omega_prime.dashboard.model import SourceResult, normalize_tasks
from omega_prime.dashboard.poller import DashboardPoller
from omega_prime.dashboard.redact import redact_value

NOW = datetime(2026, 10, 20, 16, 30, tzinfo=UTC)
HOST = {"host": "127.0.0.1:7421"}


def _src(name: str, items: list[dict[str, Any]]) -> SourceResult:
    return SourceResult(name, "ok", items)


def test_index_assets_resolve_under_static_mount() -> None:
    poller = DashboardPoller([], clock=lambda: NOW)
    asyncio.run(poller.refresh_now())
    with TestClient(create_app(poller)) as client:
        page = client.get("/", headers=HOST)
        refs = re.findall(r'(?:href|src)="([^"]+)"', page.text)
        assert refs, "index.html references no assets"
        for ref in refs:
            assert ref.startswith("/static/"), ref
            assert client.get(ref, headers=HOST).status_code == 200, ref


def test_sse_response_sends_no_content_length() -> None:
    async def events() -> Any:
        yield "retry: 3000\n\n"

    sent: list[dict[str, Any]] = []
    first_body = asyncio.Event()

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message.get("more_body"):
            first_body.set()

    async def receive() -> dict[str, Any]:
        await first_body.wait()
        return {"type": "http.disconnect"}

    asyncio.run(_SSEResponse(events())({"type": "http"}, receive, send))
    names = [key.lower() for key, _ in sent[0]["headers"]]
    assert b"content-length" not in names
    assert any(m.get("body") == b"retry: 3000\n\n" for m in sent[1:])


def test_word_internal_sk_is_not_a_credential() -> None:
    out = redact_value(
        {"branch": "feat/task-dashboard", "id": "gb:task-dashboard"}, env={}
    )
    assert out == {"branch": "feat/task-dashboard", "id": "gb:task-dashboard"}
    inline_key = "sk" + "-" + "a" * 16
    assert inline_key not in str(redact_value(f"key {inline_key}", env={}))
    secret = "Ask-" + "z" * 20
    assert secret not in redact_value(
        f"x {secret} y", env={"GITHUB_PAT_DESK_LEAD": secret}
    )


def test_wiring_log_times_are_box_local(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runs = tmp_path / "runs"
    (runs / "p1").mkdir(parents=True)
    local_now = NOW.astimezone()
    stamp_text = (local_now - timedelta(minutes=10)).strftime("%H:%M:%S")
    (runs / "p1" / "phase.log").write_text(
        f"{stamp_text} luna start\n", encoding="utf-8"
    )
    os.utime(
        runs / "p1" / "phase.log",
        (local_now.timestamp(), local_now.timestamp()),
    )
    result = make_wiring_collector(
        repo_root=repo, runs_root=runs, now_fn=lambda: NOW, runner=lambda _a: ""
    )()
    stamp = datetime.fromisoformat(result.items[0]["last_log_ts"])
    assert stamp.utcoffset() == local_now.utcoffset()
    assert abs((NOW - stamp) - timedelta(minutes=10)) < timedelta(seconds=2)


def test_stale_work_without_a_process_moves_to_finished() -> None:
    old = (NOW - timedelta(hours=2)).isoformat()
    fresh = (NOW - timedelta(minutes=1)).isoformat()
    sources = {
        "omp_session": _src(
            "omp_session",
            [
                {
                    "session_path": "/s/a.jsonl",
                    "session_id": "a",
                    "cwd": "/gone",
                    "last_ts": old,
                },
                {"session_path": "/s/b.jsonl", "session_id": "b", "last_ts": old},
            ],
        ),
        "omp_proc": _src("omp_proc", [{"pid": 7, "cwd": "/wt/p4", "mode": "rpc"}]),
        "wiring": _src(
            "wiring",
            [
                {
                    "phase": "p1",
                    "stage": "luna",
                    "last_log_ts": old,
                    "worktree": "/wt/p1",
                },
                {
                    "phase": "p4",
                    "stage": "luna",
                    "last_log_ts": old,
                    "worktree": "/wt/p4",
                },
            ],
        ),
        "omega_job": _src(
            "omega_job",
            [
                {"job_id": "job-old", "ok": None, "last_activity_at": old},
                {"job_id": "job-new", "ok": None, "last_activity_at": fresh},
            ],
        ),
    }
    snap = normalize_tasks(sources, now=NOW).to_dict()
    active = {t["id"]: t["state"] for t in snap["active"]}
    done = {f["id"]: f["result"] for f in snap["finished"]}
    assert active == {
        "wiring:p4": "working",
        "job:job-new": "working",
        "omp:b": "quiet",
        "job:job-old": "quiet",
    }
    assert done == {"omp:a": "stopped", "wiring:p1": "stopped"}


def _js_helpers() -> str:
    source = (
        Path(__file__).resolve().parents[1] / "dashboard" / "static" / "app.js"
    ).read_text(encoding="utf-8")
    parts = []
    for name in ("formatAedtTime", "relativeTime"):
        match = re.search(
            rf"  const {name} = \(value\) => \{{.*?\n  \}};", source, re.S
        )
        assert match, name
        parts.append(match.group(0))
    return "const text = (v) => String(v);\n" + "\n".join(parts)


def test_health_reason_and_finished_summary_are_rendered() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "dashboard" / "static" / "app.js"
    ).read_text(encoding="utf-8")
    health = source[
        source.index("const reason = source.error") : source.index("strip.append(chip)")
    ]
    assert "source.error || source.detail" in health
    assert "'missing', 'unreadable'" in health
    finished = source[
        source.index("function renderFinished") : source.index(
            "function renderSnapshot"
        )
    ]
    assert "item.summary" in finished
    assert "'finished-summary'" in finished


def test_client_time_helpers_do_not_throw() -> None:
    import json
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    past = "2026-10-20T16:00:00+00:00"
    script = (
        _js_helpers()
        + f"\nconsole.log(JSON.stringify([formatAedtTime({past!r}),"
        + " relativeTime(new Date(Date.now() - 120000).toISOString())]));"
    )
    out = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, timeout=20, check=True
    ).stdout
    absolute, relative = json.loads(out)
    assert "03:00" in absolute
    assert relative == "2 minutes ago"


def test_numbered_task_ids_stay_distinct_and_embedded_keys_redact() -> None:
    from secrets import token_hex

    ids = ["gb:task-dashboard-1", "gb:task-dashboard-2", "feat/task-dashboard"]
    assert redact_value(ids, env={}) == ids
    embedded = "x" + "sk" + "-" + token_hex(10)
    assert embedded[1:] not in str(redact_value(f"note {embedded}", env={}))
    long_lower = "a" + "sk" + "-" + "q" * 24
    assert long_lower[1:] not in str(redact_value(long_lower, env={}))


def test_quiet_minutes_never_below_engine_timeout(tmp_path) -> None:
    from omega_prime.dashboard.config import load_dashboard_config

    settings = tmp_path / "settings.json"
    settings.write_text('{"dashboard": {"quiet_minutes": 0.5}}')
    assert load_dashboard_config(settings).quiet_minutes == 2.0
