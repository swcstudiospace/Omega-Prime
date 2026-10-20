# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Regressions for Greptile round 7 on the task dashboard."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from typing import Any

import pytest

from omega_prime.dashboard.collectors.fleet import make_fleet_collector
from omega_prime.dashboard.collectors.herdr import make_herdr_collector
from omega_prime.dashboard.collectors.omega_jobs import make_omega_jobs_collector
from omega_prime.dashboard.model import FinishedItem, SourceResult, normalize_tasks

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def _fleet(tmp_path: Path, routes: dict[str, bytes]) -> SourceResult:
    token = "fleet" + "_" + token_hex(8)
    config = tmp_path / "coordinator.env"
    config.write_text(
        "FLEET_COORDINATOR_URL=http://127.0.0.1:19999\n"
        f"FLEET_COORDINATOR_TOKEN={token}\n",
        encoding="utf-8",
    )

    def transport(url: str, headers: Any) -> tuple[int, bytes]:
        for suffix, body in routes.items():
            if url.split("?")[0].endswith(suffix):
                return 200, body
        return 404, b"{}"

    result = make_fleet_collector(enabled=True, env_file=config, transport=transport)()
    assert token not in json.dumps(result.__dict__, default=str)
    return result


def test_fleet_reads_status_field(tmp_path: Path) -> None:
    jobs = {
        "jobs": [
            {"id": "job-1", "status": "running"},
            {"id": "job-2", "status": "done"},
        ]
    }
    result = _fleet(tmp_path, {"/status": b"{}", "/jobs": json.dumps(jobs).encode()})
    assert [i["state"] for i in result.items] == ["running", "done"]
    snap = normalize_tasks({"fleet": result}, now=NOW)
    assert [(t.id, t.state) for t in snap.active] == [("fleet:job-1", "working")]
    assert [(f.id, f.result) for f in snap.finished] == [("fleet:job-2", "ok")]


def test_fleet_pending_hitl_questions_show_under_waiting(tmp_path: Path) -> None:
    hitl = {"pending": [{"job_id": "job-7", "question": "Ship it?"}]}
    result = _fleet(
        tmp_path,
        {
            "/status": b"{}",
            "/jobs": b'{"jobs": [{"id": "job-7", "status": "running"}]}',
            "/hitl": json.dumps(hitl).encode(),
        },
    )
    assert result.extra["hitl_count"] == 1
    snap = normalize_tasks({"fleet": result}, now=NOW)
    waits = {w.id: w.ask for w in snap.waiting_on_ove}
    assert waits == {"wait:fleet:hitl:job-7": "fleet job job-7 asks: Ship it?"}


def _shell_snapshot(n: int) -> str:
    panes = [
        {"pane_id": f"w{i}:p1", "label": f"pane-{i}", "agent_status": "unknown"}
        for i in range(n)
    ]
    return json.dumps(
        {"result": {"snapshot": {"workspaces": [{"tabs": [{"panes": panes}]}]}}}
    )


def test_herdr_slow_lookups_share_one_deadline_and_degrade_per_pane() -> None:
    lock = threading.Lock()
    calls: list[str] = []

    def runner(argv: list[str]) -> str:
        if argv[:3] == ["herdr", "api", "snapshot"]:
            return _shell_snapshot(4)
        with lock:
            calls.append(argv[4])
        if argv[4] == "w0:p1":
            info = {
                "foreground_processes": [
                    {"name": "python", "pid": 2, "argv": ["python"]}
                ],
                "shell_pid": 1,
            }
            return json.dumps({"result": {"process_info": info}})
        time.sleep(0.6)
        raise OSError("slow")

    started = time.monotonic()
    result = make_herdr_collector(runner=runner, lookup_budget=1.0)()
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, elapsed  # concurrent, not 3 x 0.6 s in series
    assert result.status == "ok"
    assert len(result.items) == 4
    by_id = {i["pane_id"]: i for i in result.items}
    assert by_id["w0:p1"]["has_job"] is True
    assert "has_job" not in by_id["w1:p1"]


def test_herdr_lookup_past_deadline_keeps_the_base_snapshot() -> None:
    def runner(argv: list[str]) -> str:
        if argv[:3] == ["herdr", "api", "snapshot"]:
            return _shell_snapshot(2)
        time.sleep(1.5)
        return "{}"

    started = time.monotonic()
    result = make_herdr_collector(runner=runner, lookup_budget=0.3)()
    assert time.monotonic() - started < 1.0
    assert result.status == "ok"
    assert [i["pane_id"] for i in result.items] == ["w0:p1", "w1:p1"]


def test_custom_job_ids_are_collected_but_unrelated_dirs_are_not(
    tmp_path: Path,
) -> None:
    root = tmp_path / "jobs"
    for name, payload in (
        ("audit-1", {"job_id": "audit-1", "model": "m", "ok": True}),
        ("job-2", {"model": "m"}),
        ("notes", {"title": "not a job"}),
        ("other", {"job_id": "different"}),
    ):
        (root / name).mkdir(parents=True)
        (root / name / "session.json").write_text(json.dumps(payload))
        stamp = NOW.timestamp() - 60
        os.utime(root / name / "session.json", (stamp, stamp))
    result = make_omega_jobs_collector(jobs_root=root, now_fn=lambda: NOW)()
    assert sorted(i["job_id"] for i in result.items) == ["2", "audit-1"]


def test_undated_finished_items_sort_after_dated_ones() -> None:
    tasks = [
        {"id": "undated", "title": "u", "state": "done"},
        {
            "id": "old",
            "title": "o",
            "state": "done",
            "last_activity_at": "2026-10-21T01:00:00+00:00",
        },
        {
            "id": "new",
            "title": "n",
            "state": "done",
            "last_activity_at": "2026-10-21T09:00:00+00:00",
        },
    ]
    snap = normalize_tasks(
        {"tasks_json": SourceResult("tasks_json", "ok", [], extra={"tasks": tasks})},
        now=NOW,
    )
    assert [f.id for f in snap.finished] == ["tasks:new", "tasks:old", "tasks:undated"]
    assert isinstance(snap.finished[0], FinishedItem)


_JS_HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
let now = 0; let seq = 0; const timers = new Map();
const add = (fn, ms, repeat) => { const id = ++seq; timers.set(id, {at: now + ms, fn, ms, repeat}); return id; };
const window = {
  setTimeout: (fn, ms) => add(fn, ms, false), clearTimeout: (id) => timers.delete(id),
  setInterval: (fn, ms) => add(fn, ms, true), clearInterval: (id) => timers.delete(id),
};
const el = () => new Proxy(function () {}, {
  get: (t, k) => (k === 'firstChild' ? null : k === Symbol.toPrimitive ? () => '' : el()),
  set: () => true, apply: () => el(),
});
const document = {getElementById: () => el(), createElement: () => el()};
let fetches = 0;
const fetch = async () => { fetches += 1; return {ok: true, json: async () => ({})}; };
const sources = [];
class EventSource { constructor() { sources.push(this); } addEventListener() {} close() {} }
const console = {error() {}, log: (...a) => process.stdout.write(a.join(' ') + '\n')};
new Function('window', 'document', 'fetch', 'EventSource', 'console', src)(window, document, fetch, EventSource, console);
const flush = () => new Promise((r) => setImmediate(r));
async function advance(ms) {
  const end = now + ms;
  for (;;) {
    let next = null;
    for (const [id, t] of timers) if (t.at <= end && (!next || t.at < next[1].at)) next = [id, t];
    if (!next) break;
    const [id, t] = next; now = t.at;
    if (t.repeat) t.at += t.ms; else timers.delete(id);
    t.fn(); for (let i = 0; i < 5; i++) await flush();
  }
  now = end;
}
(async () => {
  for (let i = 0; i < 5; i++) await flush();
  sources[0].onerror();            // stream fails: fallback polling starts
  await advance(30000);            // reconnect creates a new, unopened stream
  if (sources.length < 2) throw new Error('no reconnect');
  sources[sources.length - 1].onerror(); // the retry fails before opening
  const before = fetches;
  await advance(20000);
  console.log(JSON.stringify({before, after: fetches}));
})().catch((e) => { process.stdout.write('ERR ' + e.message + '\n'); process.exit(1); });
"""


def test_fallback_polling_resumes_after_stream_retries(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    harness = tmp_path / "harness.js"
    harness.write_text(_JS_HARNESS, encoding="utf-8")
    app = Path(__file__).resolve().parents[1] / "dashboard" / "static" / "app.js"
    out = subprocess.run(
        [node, str(harness), str(app)],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    ).stdout
    counts = json.loads(out.strip().splitlines()[-1])
    assert counts["after"] - counts["before"] >= 3, counts


@pytest.mark.parametrize(
    "repo",
    [
        "swcstudiospace/Omega-Prime",
        "SWCSTUDIOSPACE/omega-prime",
        "swcstudiospace/omes-bot",
    ],
)
def test_merge_rule_matches_renamed_repo_case_insensitively(repo: str) -> None:
    pr = {
        "kind": "github_pr",
        "repo": repo,
        "number": 47,
        "ci": "pass",
        "greptile": "1/5",
    }
    snap = normalize_tasks(
        {"github_pr": SourceResult("github_pr", "ok", [pr])}, now=NOW
    )
    assert snap.pr_gate[0].merge_rule == "Greptile 5/5 + green CI, merge commit"


def test_unrelated_repo_still_needs_ove() -> None:
    pr = {"kind": "github_pr", "repo": "someone/else", "number": 1}
    snap = normalize_tasks(
        {"github_pr": SourceResult("github_pr", "ok", [pr])}, now=NOW
    )
    assert snap.pr_gate[0].merge_rule == "needs Ove's yes"


def test_github_repo_defaults_to_omega_prime_and_reads_settings(tmp_path: Path) -> None:
    from omega_prime.dashboard.collectors.github import make_github_collector
    from omega_prime.dashboard.config import DEFAULT_GITHUB_REPO, load_dashboard_config

    assert DEFAULT_GITHUB_REPO == "swcstudiospace/Omega-Prime"
    settings = tmp_path / "settings.json"
    assert load_dashboard_config(settings).github_repo == DEFAULT_GITHUB_REPO
    settings.write_text('{"dashboard": {"github_repo": "acme/widgets"}}')
    assert load_dashboard_config(settings).github_repo == "acme/widgets"
    settings.write_text('{"dashboard": {"github_repo": "../../etc"}}')
    assert load_dashboard_config(settings).github_repo == DEFAULT_GITHUB_REPO

    urls: list[str] = []

    def transport(url: str, headers: Any) -> tuple[int, bytes]:
        urls.append(url)
        return 200, b"[]"

    pat = "x" + token_hex(8)
    make_github_collector(env={"GITHUB_PAT_DESK_LEAD": pat}, transport=transport)()
    assert urls and "/repos/swcstudiospace/Omega-Prime/" in urls[0]
