# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""W112-05: the omp-sandbox engine backend proven against a fake Anda wrapper.

The Rust ``omp-sandbox-anda`` wrapper is never built here. A generated
python script is written into ``tmp_path`` (never a committed fixture) and
speaks the client.rs stdio protocol: ``wrapper_ready`` first, one
id-matched reply per ``{"id","tool","args"}`` line, unsolicited
``{"event", ...}`` lines interleaved, ``"attestation": "mock"`` on every
emitted object, and a ``down`` reply before exit. Its behaviour is selected
by a sibling ``behavior.json`` — never argv, never a credentialed command
line — and every tool call is receipted to a log beside the script, so the
tests can prove both ordering and that a spawn never happened.

Every behavioural assertion drives the real ``omp_driver.run()`` and its
real phase-111 block wiring; the fake only ever replaces the wrapper
binary. Env values are never printed or asserted: the allowlist flag and
the API-key-shaped input are set truthy at runtime and only their effects
are asserted (T-112-10).
"""

from __future__ import annotations

import json
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from omega_prime.agent.interrupt import InterruptFlag
from omega_prime.engine import sandbox_omp
from omega_prime.engine.attach import delegate_via_engine, ultrathink_engine
from omega_prime.engine.omp_driver import probe, run
from omega_prime.engine.sandbox_omp import (
    ANDA_BIN_ENV,
    BACKEND_ENV,
    TOOLS_WAIVER_ENV,
    WORKSPACE_ROOTS_ENV,
)
from omega_prime.tests.test_engine import _env, _install_fake

# --- the fake Anda wrapper ---------------------------------------------------

_ANDA_FAKE = r'''#!/usr/bin/env python3
"""Fake omp-sandbox-anda wrapper: the client.rs stdio protocol, stdlib only.

Behaviour is selected by ``behavior.json`` beside this script -- never
argv: ``{"behavior": "happy" | "tier_error" | "question" | "hang" |
"early_done" | "abort_race" | "fallback" | "long" | "status_fail" |
"host_moved" | "leaves_edits" | "stuck_dirty" | "up_hangs"}``. ``up`` clones the
seeded branch into a private sandbox checkout beside this script (the host worktree is never written by the
fake), and ``export_commits`` commits there and bundles ``base..ref``. One
receipt line per request ({"id", "tool", "args"}) is appended to
wrapper-log.jsonl beside this script before the reply is written. Every
emitted line carries "attestation": "mock". ``down`` is answered and the
wrapper then exits, as client.rs does.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "wrapper-log.jsonl")


def emit(obj):
    obj["attestation"] = "mock"
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def receipt(row):
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def git(work, *argv):
    proc = subprocess.run(
        ["git", "-C", work] + list(argv),
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", "/tmp"),
        },
    )
    return proc.stdout.strip()


def export_commit(box, tag, include_dirty=False, stuck=False):
    """Make one real commit in the sandbox checkout and bundle base..ref.

    ``include_dirty`` first commits what the stage left uncommitted, as the
    real core does; ``stuck`` reports an edit that could not be committed.
    """
    base = git(box, "rev-parse", "HEAD")
    if include_dirty and git(box, "status", "--porcelain"):
        git(box, "add", "-A")
        git(
            box,
            "-c", "user.name=Fake Anda",
            "-c", "user.email=fake-anda@example.invalid",
            "commit", "-q", "-m", "omp-sandbox: edits the stage left uncommitted",
        )
    name = ".fake-export-%s.txt" % tag
    with open(os.path.join(box, name), "w", encoding="utf-8") as handle:
        handle.write("exported by the fake anda wrapper\n")
    git(box, "add", "--", name)
    git(
        box,
        "-c", "user.name=Fake Anda",
        "-c", "user.email=fake-anda@example.invalid",
        "commit", "-q", "-m", "fake export %s" % tag,
    )
    head = git(box, "rev-parse", "HEAD")
    ref = git(box, "symbolic-ref", "--short", "HEAD")
    dirty = git(box, "status", "--porcelain") != "" or stuck
    bundle = os.path.join(HERE, "export-%s.bundle" % tag)
    if os.path.exists(bundle):
        os.unlink(bundle)
    git(box, "bundle", "create", "-q", bundle, "%s..%s" % (base, ref))
    return {
        "head": head,
        "base": base,
        "ref": ref,
        "commits": 1,
        "dirty": dirty,
        "bundle": bundle,
        "bytes": os.path.getsize(bundle),
        "via": "fake",
    }


LONG_TEXT = "BEGIN-" + "x" * 5000 + "-END"


with open(os.path.join(HERE, "behavior.json"), encoding="utf-8") as handle:
    behavior = json.load(handle).get("behavior", "happy")

receipt({
    "ready": True,
    "pid": os.getpid(),
    "argv": sys.argv[1:],
    "workspace_roots": os.environ.get("OMP_SANDBOX_WORKSPACE_ROOTS"),
    "cwd": os.getcwd(),
    "sandbox_root": os.environ.get("OMP_SANDBOX_ROOT"),
})
emit({"event": "wrapper_ready", "wrapper_pid": os.getpid()})

work = None
box = None
model = None
stage_id = None
hanging = False
exports = 0

for line in sys.stdin:
    text = line.strip()
    if not text:
        continue
    req = json.loads(text)
    rid = req.get("id")
    tool = req.get("tool")
    args = req.get("args") or {}
    receipt({"id": rid, "tool": tool, "args": args})
    if tool == "down":
        emit({
            "id": rid,
            "ok": True,
            "result": {"leftovers": [], "ownership": "released"},
        })
        break
    if tool == "up":
        if behavior == "up_hangs":
            # A cold start still building: no reply until it is torn down.
            continue
        if behavior == "tier_error":
            emit({
                "id": rid,
                "ok": False,
                "error": "sandbox tier unavailable (fake)",
                "code": "anda_error",
            })
            continue
        seed = args.get("seed_repo") or {}
        work = seed.get("path")
        box = os.path.join(HERE, "sandbox-%d" % os.getpid())
        subprocess.run(
            ["git", "clone", "-q", "--branch", seed.get("ref"), work, box],
            check=True,
            capture_output=True,
        )
        emit({
            "id": rid,
            "ok": True,
            "result": {"tier": "fake-tier", "workspace": work},
        })
        continue
    if tool == "run_gsd_stage":
        exports = 0
        stage_id = "stage-%d" % (exports + 1)
        model = args.get("model")
        if behavior == "early_done":
            # The terminal event lands before the stage-start reply.
            emit({"event": "stage_done", "stageId": stage_id, "status": "completed"})
        emit({
            "id": rid,
            "ok": True,
            "result": {
                "stageId": stage_id,
                "plan": args.get("plan"),
                "model": args.get("model"),
                "fallback": None,
                "command": args.get("gsd_command"),
            },
        })
        if behavior in ("hang", "abort_race"):
            hanging = True
            continue
        if behavior == "early_done":
            continue
        if behavior == "host_moved":
            # Something else commits on the host branch while the stage runs.
            with open(os.path.join(work, "host.txt"), "w", encoding="utf-8") as handle:
                handle.write("host change\n")
            git(work, "add", "host.txt")
            git(
                work,
                "-c", "user.name=Host",
                "-c", "user.email=host@example.invalid",
                "commit", "-q", "-m", "host moved",
            )
        if behavior == "leaves_edits":
            # The stage edits a file in the sandbox but never commits it.
            with open(os.path.join(box, "left-by-stage.txt"), "w", encoding="utf-8") as handle:
                handle.write("uncommitted stage output\n")
        if behavior == "question":
            emit({
                "event": "question",
                "stageId": stage_id,
                "question": "Which shape do you want?",
            })
        emit({"event": "stage_done", "stageId": stage_id, "status": "completed"})
        continue
    if tool == "abort":
        if behavior == "abort_race" and hanging and stage_id is not None:
            # Core.abort waits for the stage to settle, so stage_done can
            # precede the abort reply.
            emit({"event": "stage_done", "stageId": stage_id, "status": "aborted"})
            hanging = False
        emit({"id": rid, "ok": True, "result": {}})
        if hanging and stage_id is not None:
            emit({"event": "stage_done", "stageId": stage_id, "status": "aborted"})
            hanging = False
        continue
    if tool == "sandbox_status":
        if behavior in ("status_fail", "status_and_export_fail"):
            emit({"id": rid, "ok": False, "error": "status broke", "code": "error"})
            continue
        final = LONG_TEXT if behavior == "long" else "fake final text"
        requested = model
        fallback_from = None
        if behavior == "fallback":
            fallback_from = model
            requested = "zai/" + ("glm-5.3" if "sol" in str(model) else "glm-5.3-flash")
        emit({
            "id": rid,
            "ok": True,
            "result": {
                "stageId": stage_id,
                "status": "completed",
                "modelRequested": requested,
                "fallbackFrom": fallback_from,
                "lastText": final[-600:],
                "finalText": final,
                "error": None,
            },
        })
        continue
    if tool == "export_commits":
        if behavior == "status_and_export_fail":
            emit({"id": rid, "ok": False, "error": "export broke", "code": "error"})
            continue
        exports += 1
        emit({
            "id": rid,
            "ok": True,
            "result": export_commit(
                box,
                "%d-%d" % (os.getpid(), exports),
                include_dirty=args.get("include_dirty") is True,
                stuck=behavior == "stuck_dirty",
            ),
        })
        continue
    emit({
        "id": rid,
        "ok": False,
        "error": "unknown tool %s" % tool,
        "code": "unknown_tool",
    })
'''


def _shaped_zai_key() -> str:
    """A ZAI_API_KEY-shaped value built at runtime, never a literal."""
    return "".join(("zai", "-key-", "never", "-store-", "this"))


def _install_fake_anda(tmp_path: Path, behavior: str) -> tuple[Path, Path]:
    """Write the fake wrapper beside ``behavior.json``; return script and log.

    The script is chmod +x and pointed at via ``OMEGA_PRIME_OMP_SANDBOX_ANDA``;
    its behaviour file is rewritten per test, never passed on argv.
    """
    home = tmp_path / "anda"
    home.mkdir(exist_ok=True)
    script = home / "fake-anda"
    script.write_text(_ANDA_FAKE, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    (home / "behavior.json").write_text(
        json.dumps({"behavior": behavior}) + "\n", encoding="utf-8"
    )
    return script, home / "wrapper-log.jsonl"


def _jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _wrapper_log(log: Path) -> list[dict]:
    """Receipt rows from the fake wrapper's log (empty when never spawned)."""
    return _jsonl(log)


def _wrapper_tools(log: Path) -> list[str]:
    return [str(row.get("tool")) for row in _wrapper_log(log) if "tool" in row]


def _interrupt_once_the_stage_runs(log: Path, interrupt: Any) -> threading.Thread:
    """Started thread that sets ``interrupt`` once ``run_gsd_stage`` was
    sent, so an abort test cancels a live stage (an interrupt during the
    cold start cancels before any stage is submitted). Test-side bound."""

    def wait_then_set() -> None:
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if "run_gsd_stage" in _wrapper_tools(log):
                break
            time.sleep(0.05)
        time.sleep(0.3)
        interrupt.set()

    thread = threading.Thread(target=wait_then_set, daemon=True)
    thread.start()
    return thread


def _read_tasks(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(*argv: str, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *argv],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _git_worktree(parent: Path) -> Path:
    """A real git worktree on branch ``main`` under ``parent`` (tmp only)."""
    work = parent / "wt"
    work.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", ".", cwd=work)
    (work / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git("add", "seed.txt", cwd=work)
    _git(
        "-c",
        "user.name=Seed",
        "-c",
        "user.email=seed@example.invalid",
        "commit",
        "-q",
        "-m",
        "seed",
        cwd=work,
    )
    return work.resolve()


def _backend_env(
    tmp_path: Path,
    omp: Path,
    anda: Path,
    roots: Path,
    **extra: str,
) -> dict[str, str]:
    """The engine env for the omp-sandbox backend against the fake wrapper."""
    return _env(
        tmp_path,
        omp,
        **{
            BACKEND_ENV: "omp-sandbox",
            ANDA_BIN_ENV: str(anda),
            WORKSPACE_ROOTS_ENV: str(roots),
            TOOLS_WAIVER_ENV: "1",
            **extra,
        },
    )


def _row(task_id: str, state: str = "working", **extra: object) -> dict:
    row: dict = {
        "id": task_id,
        "title": task_id,
        "owner": {"kind": "engine", "ref": task_id},
        "state": state,
    }
    row.update(extra)
    return row


def _seed(path: Path, rows: list[dict]) -> Path:
    """A dashboard tasks.json v1 (the validated store on main)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated_at": "2026-10-31T00:00:00+00:00",
        "tasks": rows,
        "waiting_on_ove": [],
        "finished": [],
        "next_seq": 1,
        "identity_registry": {},
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _id_state(row: dict) -> dict:
    return {"id": row["id"], "state": row["state"]}


# --- W112-05: the run contract through the real engine -----------------------


def test_plan_then_execute_orders_two_stages_on_one_worktree(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    plan_result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-plan",
        stage="plan",
        plan="zai",
    )
    assert plan_result.ok is True, plan_result.detail
    exec_result = run(
        "build it",
        cwd=work,
        env=env,
        job_id="job-exec",
        stage="execute",
        plan="zai",
    )
    assert exec_result.ok is True, exec_result.detail
    # One result contract for both stages, on the omp-sandbox backend.
    for result, stage_name, model in (
        (plan_result, "plan", "glm-5.3"),
        (exec_result, "execute", "glm-5.3-flash"),
    ):
        assert result.sandbox == "omp-sandbox"
        assert result.plan == "zai"
        assert result.stage == stage_name
        assert result.model == model
        assert result.fallback_from is None
        assert result.final_text == "fake final text"
        assert result.started is True
        assert result.commands == [
            "up",
            "run_gsd_stage",
            "sandbox_status",
            "export_commits",
            "down",
        ]
    # Exactly two run_gsd_stage calls, in order, with the contracted args.
    stages = [row for row in _wrapper_log(log) if row.get("tool") == "run_gsd_stage"]
    assert [row["args"]["stage"] for row in stages] == ["plan", "execute"]
    first, second = stages
    assert first["args"]["plan"] == "zai" and second["args"]["plan"] == "zai"
    assert first["args"]["model"] == "zai/glm-5.3"
    assert second["args"]["model"] == "zai/glm-5.3-flash"
    assert first["args"]["gsd_command"] == "gsd-plan-phase"
    assert second["args"]["gsd_command"] == "gsd-execute-phase"
    assert first["args"]["task"] == "plan the thing"
    assert second["args"]["task"] == "build it"
    # Both stages seeded the same worktree at the same ref.
    ups = [row for row in _wrapper_log(log) if row.get("tool") == "up"]
    assert len(ups) == 2
    for row in ups:
        assert row["args"]["seed_repo"]["path"] == str(work)
        assert row["args"]["seed_repo"]["ref"] == "main"
    # The fake observed the engine-forwarded workspace roots value.
    seen_roots = {
        row.get("workspace_roots")
        for row in _wrapper_log(log)
        if "workspace_roots" in row
    }
    assert seen_roots == {str(roots)}
    # Wrapper-originated events all carry the attestation stamp.
    events = [row for row in _jsonl(Path(plan_result.events_path)) if "event" in row]
    assert events
    assert all(row.get("attestation") == "mock" for row in events)


def test_export_lands_back_in_the_worktree_and_is_reported(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-export",
        stage="plan",
        plan="zai",
    )
    assert result.ok is True, result.detail
    stats = result.stats["omp_sandbox"]
    assert set(stats) == {
        "stage_id",
        "tier",
        "status",
        "model_requested",
        "export",
        "applied",
    }
    assert stats["status"] == "completed"
    assert stats["tier"] == "fake-tier"
    assert stats["model_requested"] == "zai/glm-5.3"
    export = stats["export"]
    assert set(export) == {
        "head",
        "base",
        "ref",
        "commits",
        "dirty",
        "bundle",
        "bytes",
        "via",
    }
    # The fake committed only in its private sandbox checkout; the engine
    # fast-forwarded the host branch onto the exported bundle, so the
    # reported head is the worktree's HEAD right now and the commit is
    # really in the worktree's log.
    assert stats["applied"] == {"applied": True, "head": export["head"]}
    assert export["head"] == _git("rev-parse", "HEAD", cwd=work)
    assert export["base"] == _git("rev-parse", "HEAD~1", cwd=work)
    assert export["ref"] == "main"
    assert export["commits"] == 1
    assert export["dirty"] is False
    subjects = _git("log", "--format=%s", cwd=work).splitlines()
    assert any("fake export" in line for line in subjects)
    bundle = Path(export["bundle"])
    assert bundle.is_file()
    assert export["bytes"] == bundle.stat().st_size
    assert export["bytes"] > 0


def test_tier_unavailable_raises_exactly_one_sandbox_item(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "tier_error")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:tier")])
    result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-tier",
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:tier",
    )
    assert result.ok is False
    assert result.reason == "sandbox_unavailable"
    assert "tier unavailable" in result.detail
    # The item exists the moment run() returned, through run()'s own wiring.
    data = _read_tasks(store)
    items = data["waiting_on_ove"]
    assert [item["id"] for item in items] == ["gb:tier:sandbox_unavailable"]
    assert items[0]["blocks"] == "engine sandbox gate (sandbox_unavailable)"
    assert _id_state(data["tasks"][0]) == {"id": "gb:tier", "state": "blocked"}
    # The run stopped at the up refusal: only up and the shutdown ran.
    assert _wrapper_tools(log) == ["up", "down"]
    # A retried run upserts instead of duplicating (one item, stable id).
    first = dict(items[0])
    result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-tier-2",
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:tier",
    )
    assert result.ok is False and result.reason == "sandbox_unavailable"
    data = _read_tasks(store)
    assert [item["id"] for item in data["waiting_on_ove"]] == [
        "gb:tier:sandbox_unavailable"
    ]
    assert data["waiting_on_ove"][0]["seq"] == first["seq"]
    assert data["waiting_on_ove"][0]["since"] == first["since"]


def test_plan_not_allowed_raises_one_item_without_spawning(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")  # canary: logs on spawn
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots)
    env.pop("ANTHROPIC_API_KEY", None)  # no other allowlisted plan
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:gate")])
    result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-gate",
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:gate",
    )
    assert result.ok is False
    assert result.reason == "plan_not_allowed"
    data = _read_tasks(store)
    items = data["waiting_on_ove"]
    assert [item["id"] for item in items] == ["gb:gate:plan_not_allowed"]
    assert items[0]["blocks"] == "engine plan gate (plan_not_allowed)"
    assert _id_state(data["tasks"][0]) == {"id": "gb:gate", "state": "blocked"}
    # The gate refused before any wrapper spawn: the canary left no log.
    assert not log.exists()
    assert _wrapper_log(log) == []
    # The retried run upserts the same single item.
    run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-gate-2",
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:gate",
    )
    data = _read_tasks(store)
    assert [item["id"] for item in data["waiting_on_ove"]] == [
        "gb:gate:plan_not_allowed"
    ]


def test_interrupt_aborts_the_stage_and_returns_cancelled(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "hang")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    interrupt = InterruptFlag()
    timer = _interrupt_once_the_stage_runs(log, interrupt)
    try:
        result = run(
            "build it",
            cwd=work,
            env=env,
            job_id="job-abort",
            stage="execute",
            plan="zai",
            interrupt=interrupt,
        )
    finally:
        timer.join()
    assert result.ok is False
    assert result.reason == "cancelled"
    assert result.started is True
    # The engine sent the abort tool with the live stage id, and the
    # aborted stage_done is visible in events.jsonl.
    events = _jsonl(Path(result.events_path))
    aborted = [
        row
        for row in events
        if row.get("event") == "stage_done" and row.get("status") == "aborted"
    ]
    assert len(aborted) == 1
    stage_id = aborted[0]["stageId"]
    aborts = [row for row in _wrapper_log(log) if row.get("tool") == "abort"]
    assert len(aborts) == 1
    assert aborts[0]["args"] == {"stage_id": stage_id}
    assert _wrapper_tools(log) == ["up", "run_gsd_stage", "abort", "down"]
    assert any(
        row.get("type") == "job_end"
        and row.get("ok") is False
        and row.get("reason") == "cancelled"
        for row in events
    )


def test_delegate_via_engine_passes_engine_plan_to_the_wrapper(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    raw = delegate_via_engine(
        "plan the thing", env=env, cwd=str(work), stage="plan", plan="zai"
    )
    assert raw is not None
    body = json.loads(raw)
    assert body["engine"] == "omp"
    assert body["stage"] == "plan"
    assert body["model"] == "glm-5.3"
    assert body["summary"] == "fake final text"
    # The requested plan reached run_gsd_stage verbatim; probe's backend
    # readiness gate passed on the same env (the call got this far).
    stages = [row for row in _wrapper_log(log) if row.get("tool") == "run_gsd_stage"]
    assert len(stages) == 1
    args = stages[0]["args"]
    assert args["stage"] == "plan"
    assert args["plan"] == "zai"
    assert args["model"] == "zai/glm-5.3"
    assert args["gsd_command"] == "gsd-plan-phase"
    assert args["task"] == "plan the thing"


def test_ultrathink_engine_passes_engine_plan_to_the_wrapper(
    tmp_path: Path, monkeypatch
) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    spec = tmp_path / "spec.md"
    spec.write_text("Build the export button.\n", encoding="utf-8")
    monkeypatch.chdir(work)  # ultrathink runs on the process cwd
    body = ultrathink_engine(
        {"found": True, "spec_path": str(spec)},
        env,
        stage="plan",
        requested_plan="zai",
    )
    assert body["used"] is True
    assert body["stage"] == "plan"
    assert body["plan"] == "zai"
    assert body["model"] == "glm-5.3"
    assert body["final_text"] == "fake final text"
    stages = [row for row in _wrapper_log(log) if row.get("tool") == "run_gsd_stage"]
    assert len(stages) == 1
    args = stages[0]["args"]
    assert args["stage"] == "plan"
    assert args["plan"] == "zai"
    assert args["model"] == "zai/glm-5.3"
    assert args["gsd_command"] == "gsd-plan-phase"
    assert "Carry out this ultrathink spec." in args["task"]
    assert "Build the export button." in args["task"]


# --- W112-05: the guards ------------------------------------------------------


def test_allowlist_refusal_precedes_any_wrapper_spawn(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")  # canary: logs on spawn
    roots = tmp_path / "roots"
    outside = tmp_path / "outside"
    work = _git_worktree(outside)  # a real git worktree OUTSIDE the roots
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-refused",
        stage="plan",
        plan="zai",
    )
    assert result.ok is False
    assert result.reason == "sandbox_unavailable"
    assert "path_refused" in result.detail
    # The refusal happened before any spawn: the canary left no log.
    assert not log.exists()
    assert _wrapper_log(log) == []


def test_bwrap_default_untouched_when_backend_unset(
    tmp_path: Path, monkeypatch
) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")  # canary: backend untaken
    bwrap = tmp_path / "bin" / "bwrap"
    bwrap.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$@" > "$OMEGA_FAKE_OMP_BWRAP_LOG"\n'
        'while [ "$1" != "--" ]; do shift; done\n'
        "shift\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    bwrap.chmod(bwrap.stat().st_mode | stat.S_IEXEC)
    env = _env(
        tmp_path,
        omp,
        OMEGA_PRIME_ENGINE_UNSANDBOXED="0",
        OMEGA_FAKE_OMP_BWRAP_LOG=str(tmp_path / "bwrap.txt"),
        # The Anda override is present but must never be used: the backend
        # is only selected by the explicit OMEGA_PRIME_OMP_BACKEND knob.
        **{ANDA_BIN_ENV: str(script), WORKSPACE_ROOTS_ENV: str(tmp_path)},
    )
    assert BACKEND_ENV not in env  # unset: the bwrap default stands
    result = run("PONG", cwd=tmp_path, env=env, job_id="job-bwrap")
    assert result.ok is True
    assert result.sandbox == "bwrap"
    logged = (tmp_path / "bwrap.txt").read_text(encoding="utf-8")
    assert "--ro-bind" in logged
    assert "--unshare-pid" in logged
    assert not log.exists()
    assert _wrapper_log(log) == []


def test_no_secret_shaped_value_lands_in_any_durable_artifact(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    token = _shaped_zai_key()  # built at runtime, never a literal
    env = _backend_env(
        tmp_path,
        omp,
        script,
        roots,
        OMEGA_PRIME_OMP_ALLOW_ZAI="1",
        ZAI_API_KEY=token,
    )
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:secret")])
    result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-secret",
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:secret",
    )
    assert result.ok is True, result.detail
    # The truthy env routed the run (behaviour only, never values).
    assert result.plan == "zai"
    assert result.stats["omp_sandbox"]["tier"] == "fake-tier"
    # Byte-scan every artifact the run produced: the job state dir (events,
    # session.json), the operator-visible tasks store, and the wrapper's
    # receipts. On failure this prints paths only — never contents, never
    # the value.
    candidates = [p for p in tmp_path.rglob("*") if p.is_file()]
    hits = [str(p) for p in candidates if token.encode("utf-8") in p.read_bytes()]
    assert hits == [], "secret-shaped value leaked into durable artifacts"


def test_question_surfaces_as_event_only(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "question")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:q")])
    result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-q",
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:q",
    )
    assert result.ok is True, result.detail
    # The question is visible in events.jsonl as an event — and only there.
    events = _jsonl(Path(result.events_path))
    questions = [row for row in events if row.get("event") == "question"]
    assert len(questions) == 1
    assert questions[0]["attestation"] == "mock"
    # No waiting_on_ove item, no answer_question round-trip (node n4).
    data = _read_tasks(store)
    assert data["waiting_on_ove"] == []
    assert _id_state(data["tasks"][0]) == {"id": "gb:q", "state": "working"}
    assert "answer_question" not in _wrapper_tools(log)


# --- Greptile #61: backend fixes ------------------------------------------------


def _stage_args(log: Path) -> list[dict]:
    return [
        row["args"] for row in _wrapper_log(log) if row.get("tool") == "run_gsd_stage"
    ]


def test_wrapper_starts_from_the_package_root_not_the_callers_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    monkeypatch.chdir(tmp_path)  # a caller directory with no omp-sandbox in it
    result = run("plan", cwd=work, env=env, job_id="job-root", stage="plan", plan="zai")
    assert result.ok is True, result.detail
    package = Path(sandbox_omp.__file__).resolve().parents[2] / "omp-sandbox"
    ready = next(row for row in _wrapper_log(log) if row.get("ready"))
    assert ready["cwd"] == str(package)
    assert ready["sandbox_root"] == str(package)


def test_a_root_without_the_core_refuses_readiness_and_never_spawns(
    tmp_path: Path,
) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(
        tmp_path,
        omp,
        script,
        roots,
        OMEGA_PRIME_OMP_ALLOW_ZAI="1",
        OMP_SANDBOX_ROOT=str(tmp_path / "not-a-package"),
    )
    assert probe(env, stage="plan", plan="zai") == (False, "sandbox_unavailable")
    result = run(
        "plan", cwd=work, env=env, job_id="job-noroot", stage="plan", plan="zai"
    )
    assert result.ok is False and result.reason == "sandbox_unavailable"
    assert "OMP_SANDBOX_ROOT" in result.detail
    assert not log.exists()


def test_a_stageless_call_runs_the_execute_stage_and_readiness_agrees(
    tmp_path: Path,
) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    assert probe(env, plan="zai") == (True, "ok")
    raw = delegate_via_engine("build it", env=env, cwd=str(work), plan="zai")
    assert raw is not None
    body = json.loads(raw)
    assert body["stage"] == "execute"
    assert body["model"] == "glm-5.3-flash"
    (args,) = _stage_args(log)
    assert args["stage"] == "execute"
    assert args["gsd_command"] == "gsd-execute-phase"


def test_readiness_checks_the_requested_plan_like_the_router(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    env = _env(tmp_path, omp)  # only the Anthropic key is allowlisted
    assert probe(env, unsandboxed=True, stage="execute") == (False, "plan_not_allowed")
    assert probe(env, unsandboxed=True, stage="execute", plan="anthropic") == (
        True,
        "ok",
    )


def test_the_sandbox_gets_only_a_fallback_python_approved(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_CHATGPT="1")
    first = run("plan", cwd=work, env=env, job_id="job-fb-none", stage="plan")
    assert first.ok is True, first.detail
    env["OMEGA_PRIME_OMP_ALLOW_ZAI"] = "1"
    second = run("plan", cwd=work, env=env, job_id="job-fb-zai", stage="plan")
    assert second.ok is True, second.detail
    assert [args["plan"] for args in _stage_args(log)] == ["chatgpt", "chatgpt"]
    assert [args["fallback_plan"] for args in _stage_args(log)] == [None, "zai"]
    assert [args["fallback_model"] for args in _stage_args(log)] == [
        None,
        "zai/glm-5.3",
    ]
    # An operator model override reaches the sandbox-side fallback too.
    env["OMEGA_PRIME_OMP_MODEL_ZAI_PLAN"] = "glm-5.3-tuned"
    third = run("plan", cwd=work, env=env, job_id="job-fb-tuned", stage="plan")
    assert third.ok is True, third.detail
    assert _stage_args(log)[-1]["fallback_model"] == "zai/glm-5.3-tuned"


def test_backend_refuses_a_plan_its_core_cannot_route(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots)
    env["ANTHROPIC_API_KEY"] = "anthropic-key-for-the-router-only"
    assert probe(env, stage="execute", plan="anthropic") == (False, "plan_not_allowed")
    result = run(
        "x", cwd=work, env=env, job_id="job-anth", stage="execute", plan="anthropic"
    )
    assert result.ok is False
    assert result.reason == "plan_not_allowed"
    assert result.started is False
    # Refused before any wrapper started.
    assert not log.exists() or not [
        r for r in _wrapper_log(log) if r.get("tool") == "up"
    ]


def test_results_name_the_model_the_sandbox_fell_back_to(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "fallback")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(
        tmp_path,
        omp,
        script,
        roots,
        OMEGA_PRIME_OMP_ALLOW_CHATGPT="1",
        OMEGA_PRIME_OMP_ALLOW_ZAI="1",
    )
    result = run("plan", cwd=work, env=env, job_id="job-fell", stage="plan")
    assert result.ok is True, result.detail
    assert (result.plan, result.provider, result.model) == ("zai", "zai", "glm-5.3")
    assert result.fallback_from == "chatgpt"
    assert result.stats["omp_sandbox"]["model_requested"] == "zai/glm-5.3"
    session = json.loads((Path(result.events_path).parent / "session.json").read_text())
    assert session["model"] == "glm-5.3" and session["fallback_from"] == "chatgpt"


def test_long_answers_keep_their_beginning(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "long")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    result = run("plan", cwd=work, env=env, job_id="job-long", stage="plan", plan="zai")
    assert result.ok is True, result.detail
    assert result.final_text.startswith("BEGIN-")
    assert result.final_text.endswith("-END")
    assert len(result.final_text) == 5010


def test_a_dirty_worktree_is_refused_before_any_spawn(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    (work / "local-edit.txt").write_text("not committed\n", encoding="utf-8")
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    result = run(
        "plan", cwd=work, env=env, job_id="job-dirty", stage="plan", plan="zai"
    )
    assert result.ok is False
    assert result.reason == "dirty_worktree"
    assert result.started is False
    assert not log.exists()
    assert (work / "local-edit.txt").read_text(encoding="utf-8") == "not committed\n"


def test_a_stage_done_before_the_start_reply_is_not_lost(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "early_done")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    outcome: dict = {}
    worker = threading.Thread(
        target=lambda: outcome.update(
            result=run(
                "plan", cwd=work, env=env, job_id="job-early", stage="plan", plan="zai"
            )
        ),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=60)
    assert not worker.is_alive(), (
        "the stage waiter hung on an event it had already read"
    )
    assert outcome["result"].ok is True, outcome["result"].detail


def test_a_stage_done_before_the_abort_reply_still_cancels(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "abort_race")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    interrupt = InterruptFlag()
    timer = _interrupt_once_the_stage_runs(log, interrupt)
    outcome: dict = {}
    worker = threading.Thread(
        target=lambda: outcome.update(
            result=run(
                "build",
                cwd=work,
                env=env,
                job_id="job-abort-race",
                stage="execute",
                plan="zai",
                interrupt=interrupt,
            )
        ),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=60)
    timer.join()
    assert not worker.is_alive(), "cancellation waited forever"
    result = outcome["result"]
    assert result.reason == "cancelled" and result.started is True
    assert _wrapper_tools(log) == ["up", "run_gsd_stage", "abort", "down"]


def test_failures_after_the_stage_started_keep_started_true(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "status_fail")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    result = run("plan", cwd=work, env=env, job_id="job-late", stage="plan", plan="zai")
    assert result.ok is False
    assert result.reason == "sandbox_unavailable"
    assert result.started is True
    # An auto-mode delegate must not re-run a started task on the Python path.
    raw = delegate_via_engine("plan", env=env, cwd=str(work), stage="plan", plan="zai")
    assert raw is not None and json.loads(raw)["error"] == "sandbox_unavailable"


def test_a_host_branch_that_moved_is_never_overwritten(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "host_moved")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    result = run(
        "plan", cwd=work, env=env, job_id="job-moved", stage="plan", plan="zai"
    )
    assert result.ok is False
    assert result.reason == "export_conflict"
    assert result.started is True
    assert _git("log", "-1", "--format=%s", cwd=work) == "host moved"
    assert "bundle kept at" in result.detail


def test_edits_a_stage_left_uncommitted_reach_the_host(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "leaves_edits")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    result = run("plan", cwd=work, env=env, job_id="job-left", stage="plan", plan="zai")
    assert result.ok is True, result.detail
    exports = [r for r in _wrapper_log(log) if r.get("tool") == "export_commits"]
    assert [r["args"] for r in exports] == [{"include_dirty": True}]
    assert (work / "left-by-stage.txt").read_text(encoding="utf-8") == (
        "uncommitted stage output\n"
    )
    assert _git("status", "--porcelain", cwd=work) == ""


def test_edits_still_uncommitted_after_export_are_not_a_success(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "stuck_dirty")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    result = run(
        "plan", cwd=work, env=env, job_id="job-stuck", stage="plan", plan="zai"
    )
    assert result.ok is False
    assert result.reason == "export_dirty"
    assert result.started is True


def test_startup_and_export_get_deadlines_that_cover_their_inner_steps(
    tmp_path: Path, monkeypatch: Any
) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    seen: dict[str, float | None] = {}
    original = sandbox_omp.AndaClient.call

    def spy(
        self: Any,
        tool: str,
        args: Any = None,
        timeout: float | None = None,
        interrupt: Any = None,
    ) -> Any:
        seen[tool] = timeout
        return original(self, tool, args, timeout, interrupt)

    monkeypatch.setattr(sandbox_omp.AndaClient, "call", spy)
    result = run(
        "plan", cwd=work, env=env, job_id="job-deadlines", stage="plan", plan="zai"
    )
    assert result.ok is True, result.detail
    # A cold start (image build alone may take 1800s) is not cut at 300s.
    assert seen["up"] is not None and seen["up"] > 1800.0 + 600.0
    assert seen["export_commits"] is not None and seen["export_commits"] > 600.0 + 300.0
    assert seen["run_gsd_stage"] is None  # the per-tool bound


def test_interrupt_during_a_cold_start_returns_promptly_and_submits_nothing(
    tmp_path: Path,
) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "up_hangs")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    interrupt = InterruptFlag()
    timer = threading.Timer(0.5, interrupt.set)
    timer.start()
    began = time.monotonic()
    try:
        result = run(
            "build it",
            cwd=work,
            env=env,
            job_id="job-cold-cancel",
            stage="execute",
            plan="zai",
            interrupt=interrupt,
        )
    finally:
        timer.join()
    # Not the up deadline (well over an hour): the interrupt ends the wait.
    assert time.monotonic() - began < 30.0
    assert result.ok is False
    assert result.reason == "cancelled"
    assert result.started is True  # a cancelled task is never re-run elsewhere
    tools = _wrapper_tools(log)
    assert "up" in tools and "down" in tools
    assert "run_gsd_stage" not in tools


def test_without_the_tools_waiver_the_backend_refuses_before_any_spawn(
    tmp_path: Path,
) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    for value in ("", "0", "yes"):
        env[TOOLS_WAIVER_ENV] = value
        assert probe(env, stage="plan", plan="zai") == (False, "sandbox_unavailable")
        result = run(
            "plan", cwd=work, env=env, job_id="job-notools", stage="plan", plan="zai"
        )
        assert result.ok is False and result.reason == "sandbox_unavailable"
        assert TOOLS_WAIVER_ENV in result.detail
        assert result.started is False
    assert not log.exists()


def test_a_completed_stage_is_exported_before_teardown_when_status_fails(
    tmp_path: Path,
) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "status_fail")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    result = run("plan", cwd=work, env=env, job_id="job-save", stage="plan", plan="zai")
    assert result.ok is False and result.reason == "sandbox_unavailable"
    assert result.started is True
    tools = _wrapper_tools(log)
    assert tools[-2:] == ["export_commits", "down"]
    assert "completed work saved in bundle" in result.detail
    bundle = result.detail.rsplit("bundle ", 1)[1].rstrip(")")
    assert Path(bundle).is_file()
    events = _jsonl(Path(result.events_path))
    saved = [e for e in events if e.get("type") == "recovery_export"]
    assert saved == [{"type": "recovery_export", "saved": True, "bundle": bundle}]


def test_a_failed_recovery_export_keeps_the_original_failure(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "status_and_export_fail")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    result = run("plan", cwd=work, env=env, job_id="job-lost", stage="plan", plan="zai")
    assert result.ok is False and result.reason == "sandbox_unavailable"
    assert "status broke" in result.detail
    assert "saved in bundle" not in result.detail
    assert _wrapper_log(log)[-1]["tool"] == "down"
    events = _jsonl(Path(result.events_path))
    assert [e["saved"] for e in events if e.get("type") == "recovery_export"] == [False]


def test_a_string_tasks_path_still_raises_the_block_item(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "tier_error")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:str")])
    result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-str",
        stage="plan",
        plan="zai",
        tasks_path=str(store),
        task_id="gb:str",
    )
    assert result.ok is False and result.reason == "sandbox_unavailable"
    items = _read_tasks(store)["waiting_on_ove"]
    assert [item["id"] for item in items] == ["gb:str:sandbox_unavailable"]
