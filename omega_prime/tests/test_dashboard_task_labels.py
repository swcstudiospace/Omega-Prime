# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""00051: dashboard rows for live omp work carry a human Title and Description.

The Command Center renders ``title`` as the row heading and ``detail`` as the
description.  Live omp processes used to surface as ``omp process <pid>`` with
``unmatched process`` underneath; these tests pin the replacement contract.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path

from omega_prime.dashboard.collectors.procs import make_procs_collector
from omega_prime.dashboard.labels import (
    LABEL_FIELDS,
    known_task_titles,
    parse_omp_argv,
    session_title,
    task_number,
    work_title,
)
from omega_prime.dashboard.model import SourceResult, normalize_tasks
from omega_prime.dashboard.poller import CollectorSpec, DashboardPoller

NOW = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)
# The frontend's Title rule (frontend/src/lib/taskCard.ts).
_UI_TITLE = re.compile(r"^\s*(\d{5})\s+-(?:\s+|$)(.*)$")

EXEC_PROMPT = (
    "You are the GSD executor (autonomous, unattended) for Omega Prime milestone "
    '00046 "Event Stream", phase 105, plan 105-04.\nDo the work.'
)
PLAN_PROMPT = (
    "You are the GSD planner (autonomous) for Omega Prime task 00050 "
    '"CI Runner Fleet", phase 120, research then plan.'
)
WT = "/workspace/omes-bot-00046/.claude/worktrees/agent-pp105-04-1792714654-0a10"


def _exec_argv(
    prompt: str = EXEC_PROMPT, run: str = "omp-execute-ab12cd-1f2e3d"
) -> list[str]:
    return [
        "omp",
        "-p",
        "--mode",
        "json",
        "--no-title",
        "--session-dir",
        f"/home/box/agentos/state/omp/{run}/sessions",
        "--model",
        "openai-codex/gpt-6-luna",
        "--cwd",
        WT,
        prompt,
    ]


def _agent(
    pid: int, cwd: str, argv: list[str], *, elapsed: int = 30
) -> dict[str, object]:
    label = parse_omp_argv(argv)
    return {
        "kind": "omp_proc",
        "pid": pid,
        "cwd": cwd,
        "elapsed_s": elapsed,
        "elapsed_measured_at": NOW.isoformat(),
        "mode": "json",
        "role": "helper" if label["worker"] else "agent",
        "label": label,
    }


def _procs(*items: dict[str, object]) -> dict[str, SourceResult]:
    return {"omp_proc": SourceResult("omp_proc", "ok", list(items))}


# --- argv parsing ----------------------------------------------------------


def test_prompt_header_names_task_stage_and_plan() -> None:
    label = parse_omp_argv(_exec_argv())
    assert set(label) == set(LABEL_FIELDS)
    assert label["task"] == "00046"
    assert label["task_title"] == "Event Stream"
    assert label["agent"] == "executor"
    assert label["phase"] == "105"
    assert label["plan"] == "105-04"
    assert label["stage"] == "execute"
    assert label["run_id"] == "omp-execute-ab12cd-1f2e3d"
    assert label["model"] == "openai-codex/gpt-6-luna"
    assert label["worker"] is None


def test_helper_worker_argv_is_recognised() -> None:
    label = parse_omp_argv(["bun.exe", "/x/cli.js", "__omp_worker_lsp_mux"])
    assert label["worker"] == "lsp_mux"


def test_task_number_is_exactly_five_digits() -> None:
    assert task_number("/workspace/omes-bot-00051") == "00051"
    assert task_number("build 1000512 of x") is None
    assert task_number(None, "nothing here") is None


def test_secret_shaped_prompt_text_never_reaches_the_label() -> None:
    secret = "ghp_" + "A" * 36
    # Token lives inside the quoted task title that parse_omp_argv captures,
    # so this fails if _clean stops redacting captured titles.
    prompt = (
        "You are the GSD executor (autonomous, unattended) for Omega Prime "
        f'milestone 00046 "Event Stream {secret}", phase 105, plan 105-04.\n'
        "Do the work."
    )
    label = parse_omp_argv(_exec_argv(prompt=prompt))
    assert label["task_title"] is not None
    assert secret not in label["task_title"]
    assert secret not in repr(label)
    title = work_title(label, WT)
    assert secret not in title
    assert "[REDACTED]" in title


# --- collector ---------------------------------------------------------------


def _proc(root: Path, pid: int, argv: list[str], *, comm: str = "omp") -> None:
    path = root / str(pid)
    path.mkdir(parents=True)
    (path / "comm").write_text(comm, encoding="utf-8")
    fields = ["S"] + ["0"] * 40
    fields[19] = "100"
    (path / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields), encoding="utf-8")
    (path / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (path / "cwd").symlink_to("/tmp/workspace")


def test_collector_labels_agents_and_classifies_helpers_and_daemons(
    tmp_path: Path,
) -> None:
    _proc(tmp_path, 401, ["bun", *_exec_argv()])
    _proc(tmp_path, 402, ["bun.exe", "/x/cli.js", "__omp_worker_lsp_mux"])
    _proc(tmp_path, 403, ["bun", "/home/box/.bun/bin/omp", "mcp", "serve"])
    _proc(tmp_path, 404, ["bun", "/home/box/.bun/bin/omp", "daemon", "broker"])
    result = make_procs_collector(
        proc_root=tmp_path,
        now_fn=lambda: datetime.fromtimestamp(1020, UTC),
        clock_hz=10,
        boot_time=1000,
    )()
    roles = {item["pid"]: item["role"] for item in result.items}
    assert roles == {401: "agent", 402: "helper"}
    assert result.items[0]["label"]["task"] == "00046"
    names = {s["name"] for s in result.extra["services"]}
    assert names == {"omp mcp", "omp daemon"}
    assert all(s["role"] == "service" for s in result.extra["services"])


# --- model rows --------------------------------------------------------------


def test_live_omp_run_becomes_one_titled_row_with_a_description() -> None:
    snapshot = normalize_tasks(
        _procs(
            _agent(158308, WT, _exec_argv()),
            _agent(193434, WT, ["bun.exe", "/x/cli.js", "__omp_worker_lsp_mux"]),
        ),
        now=NOW,
    )
    rows = [t for t in snapshot.active if t.source == "omp_proc"]
    assert len(rows) == 1, "helper workers fold into their agent's row"
    row = rows[0]
    assert row.id == "omp-run:omp-execute-ab12cd-1f2e3d"
    assert row.title == "00046 - Event Stream · executing plan 105-04"
    assert _UI_TITLE.match(row.title)
    assert "158308" not in row.title and "omp process" not in row.title
    assert row.detail is not None
    assert "GSD executor on openai-codex/gpt-6-luna" in row.detail
    assert "pid 158308" in row.detail and "helper workers 193434" in row.detail
    assert row.model == "openai-codex/gpt-6-luna"
    assert row.owner.label == "omp executor"


def test_planner_row_names_the_phase() -> None:
    argv = _exec_argv(prompt=PLAN_PROMPT, run="omp-plan-ab12cd-1f2e3d")
    snapshot = normalize_tasks(
        _procs(_agent(231829, "/workspace/omes-bot-00050", argv)), now=NOW
    )
    (row,) = snapshot.active
    assert row.title == "00050 - CI Runner Fleet · planning phase 120"


def test_helper_only_group_is_not_a_task() -> None:
    helper = _agent(9001, "/work/x", ["bun.exe", "/x/cli.js", "__omp_worker_lsp_mux"])
    assert normalize_tasks(_procs(helper), now=NOW).active == []


def test_separate_runs_sharing_a_checkout_stay_separate_rows() -> None:
    cwd = "/workspace/shared-checkout"
    a = _agent(100, cwd, _exec_argv(run="omp-execute-aaaaaa-111111"))
    b = _agent(
        200,
        cwd,
        _exec_argv(prompt=PLAN_PROMPT, run="omp-plan-bbbbbb-222222"),
    )
    helper = _agent(150, cwd, ["bun.exe", "/x/cli.js", "__omp_worker_lsp_mux"])
    snapshot = normalize_tasks(_procs(a, helper, b), now=NOW)
    rows = sorted(
        (t for t in snapshot.active if t.source == "omp_proc"), key=lambda t: t.id
    )
    assert [r.id for r in rows] == [
        "omp-run:omp-execute-aaaaaa-111111",
        "omp-run:omp-plan-bbbbbb-222222",
    ]
    assert "Event Stream" in rows[0].title
    assert "CI Runner Fleet" in rows[1].title
    # Helpers fold into the primary (lowest-pid agent) row only.
    assert rows[0].detail is not None and "helper workers 150" in rows[0].detail
    assert rows[1].detail is not None and "helper workers" not in rows[1].detail


def test_run_id_hex_suffix_is_never_a_task_number() -> None:
    # Hex suffix 00046a must not become task 00046 when the header and
    # checkout carry no number.
    label = parse_omp_argv(
        [
            "omp",
            "-p",
            "--session-dir",
            "/home/box/agentos/state/omp/omp-execute-ab12cd-00046a/sessions",
            "plain prompt with no GSD header",
        ]
    )
    assert label["run_id"] == "omp-execute-ab12cd-00046a"
    assert label["task"] is None
    title = work_title(label, "/tmp/unnumbered", known={"00046": "Event Stream"})
    assert "00046" not in title
    assert "Event Stream" not in title


def test_numbered_checkout_without_header_uses_recorded_task_title() -> None:
    sources = {
        **_procs(_agent(77, "/workspace/omes-bot-00051", ["omp", "--mode", "rpc"])),
        "tasks_json": SourceResult(
            "tasks_json",
            "ok",
            extra={
                "tasks": [],
                "finished": [
                    {
                        "id": "rec-51",
                        "title": "00051 - Command Center task labels",
                        "result": "ok",
                    }
                ],
            },
        ),
    }
    snapshot = normalize_tasks(sources, now=NOW)
    rows = [t for t in snapshot.active if t.source == "omp_proc"]
    assert len(rows) == 1
    assert _UI_TITLE.match(rows[0].title), rows[0].title
    assert "Command Center task labels" in rows[0].title
    assert rows[0].title.startswith("00051 - ")


def test_known_task_titles_first_recorded_title_wins() -> None:
    rows = [
        # display_id carries the dashboard's own sequence, not Ove's number.
        {"display_id": "00007 - 00099 - Not a source", "title": "Dashboard labels"},
        {"title": "00051 - Dashboard labels"},
        {"title": "00051 - Later duplicate"},
        {"title": "no number here"},
    ]
    assert known_task_titles(rows) == {"00051": "Dashboard labels"}


def test_untitled_session_is_never_titled_with_its_file_name() -> None:
    assert (
        session_title("/workspace/omes-bot-00051")
        == "00051 - omp session in omes-bot-00051"
    )
    assert session_title(None, session_id="2026-10-23T01-00-00_abcdef123456789") == (
        "omp session 2026-10-23T0 (no title recorded)"
    )


def test_daemons_publish_as_services_in_source_health_not_as_tasks() -> None:
    service = {"pid": 301, "name": "omp auth-broker", "role": "service", "elapsed_s": 9}
    agent = _agent(305, "/work/x", ["omp", "-p"])

    async def run() -> None:
        poller = DashboardPoller(
            [
                CollectorSpec(
                    "procs",
                    lambda: SourceResult(
                        "procs", "ok", [agent], extra={"services": [service]}
                    ),
                    1.0,
                    1.0,
                )
            ]
        )
        await poller.refresh_now()
        snap = poller.snapshot()
        assert snap is not None
        assert snap["sources"]["procs"]["services"] == [service]
        titles = [row["title"] for row in snap["active"]]
        assert titles == ["omp agent session (x)"]
        assert not any("auth-broker" in title for title in titles)

    asyncio.run(run())
