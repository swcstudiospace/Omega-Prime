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
from pathlib import Path

from omega_prime.agent.interrupt import InterruptFlag
from omega_prime.engine.attach import delegate_via_engine, ultrathink_engine
from omega_prime.engine.omp_driver import run
from omega_prime.engine.sandbox_omp import (
    ANDA_BIN_ENV,
    BACKEND_ENV,
    WORKSPACE_ROOTS_ENV,
)
from omega_prime.tests.test_engine import _env, _install_fake

# --- the fake Anda wrapper ---------------------------------------------------

_ANDA_FAKE = r'''#!/usr/bin/env python3
"""Fake omp-sandbox-anda wrapper: the client.rs stdio protocol, stdlib only.

Behaviour is selected by ``behavior.json`` beside this script -- never
argv: ``{"behavior": "happy" | "tier_error" | "question" | "hang"}``. One
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


def export_commit(work, tag):
    """Make one real commit and bundle in the seeded worktree."""
    name = ".fake-export-%s.txt" % tag
    with open(os.path.join(work, name), "w", encoding="utf-8") as handle:
        handle.write("exported by the fake anda wrapper\n")
    git(work, "add", "--", name)
    git(
        work,
        "-c", "user.name=Fake Anda",
        "-c", "user.email=fake-anda@example.invalid",
        "commit", "-q", "-m", "fake export %s" % tag,
    )
    head = git(work, "rev-parse", "HEAD")
    base = git(work, "rev-parse", "HEAD~1")
    ref = git(work, "symbolic-ref", "--short", "HEAD")
    dirty = git(work, "status", "--porcelain") != ""
    bundle = os.path.join(HERE, "export-%s.bundle" % tag)
    if os.path.exists(bundle):
        os.unlink(bundle)
    git(work, "bundle", "create", bundle, ref)
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


with open(os.path.join(HERE, "behavior.json"), encoding="utf-8") as handle:
    behavior = json.load(handle).get("behavior", "happy")

receipt({
    "ready": True,
    "pid": os.getpid(),
    "argv": sys.argv[1:],
    "workspace_roots": os.environ.get("OMP_SANDBOX_WORKSPACE_ROOTS"),
})
emit({"event": "wrapper_ready", "wrapper_pid": os.getpid()})

work = None
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
        if behavior == "tier_error":
            emit({
                "id": rid,
                "ok": False,
                "error": "sandbox tier unavailable (fake)",
                "code": "anda_error",
            })
            continue
        work = (args.get("seed_repo") or {}).get("path")
        emit({
            "id": rid,
            "ok": True,
            "result": {"tier": "fake-tier", "workspace": work},
        })
        continue
    if tool == "run_gsd_stage":
        exports = 0
        stage_id = "stage-%d" % (exports + 1)
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
        if behavior == "hang":
            hanging = True
            continue
        if behavior == "question":
            emit({
                "event": "question",
                "stageId": stage_id,
                "question": "Which shape do you want?",
            })
        emit({"event": "stage_done", "stageId": stage_id, "status": "completed"})
        continue
    if tool == "abort":
        emit({"id": rid, "ok": True, "result": {}})
        if hanging and stage_id is not None:
            emit({"event": "stage_done", "stageId": stage_id, "status": "aborted"})
            hanging = False
        continue
    if tool == "sandbox_status":
        emit({
            "id": rid,
            "ok": True,
            "result": {
                "stageId": stage_id,
                "status": "completed",
                "lastText": "fake final text",
                "error": None,
            },
        })
        continue
    if tool == "export_commits":
        exports += 1
        emit({
            "id": rid,
            "ok": True,
            "result": export_commit(work, "%d-%d" % (os.getpid(), exports)),
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
        "-c", "user.name=Seed",
        "-c", "user.email=seed@example.invalid",
        "commit", "-q", "-m", "seed",
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
            **extra,
        },
    )


def _row(task_id: str, state: str = "working", **extra: object) -> dict:
    row: dict = {"id": task_id, "state": state}
    row.update(extra)
    return row


def _seed(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"tasks": rows, "waiting_on_ove": []}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


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
    stages = [
        row for row in _wrapper_log(log) if row.get("tool") == "run_gsd_stage"
    ]
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
    script, log = _install_fake_anda(tmp_path, "happy")
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
    # The reported head is the worktree's HEAD right now, and the commit is
    # really in the worktree's log.
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
    assert data["tasks"][0] == {"id": "gb:tier", "state": "blocked"}
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
    assert data["tasks"][0] == {"id": "gb:gate", "state": "blocked"}
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
    timer = threading.Timer(0.8, interrupt.set)
    timer.start()
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
        row.get("type") == "job_end" and row.get("ok") is False
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
        "plan the thing", env=env, cwd=str(work), stage="plan", engine_plan="zai"
    )
    assert raw is not None
    body = json.loads(raw)
    assert body["engine"] == "omp"
    assert body["stage"] == "plan"
    assert body["model"] == "glm-5.3"
    assert body["summary"] == "fake final text"
    # The requested plan reached run_gsd_stage verbatim; probe's backend
    # readiness gate passed on the same env (the call got this far).
    stages = [
        row for row in _wrapper_log(log) if row.get("tool") == "run_gsd_stage"
    ]
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
        engine_plan="zai",
    )
    assert body["used"] is True
    assert body["stage"] == "plan"
    assert body["plan"] == "zai"
    assert body["model"] == "glm-5.3"
    assert body["final_text"] == "fake final text"
    stages = [
        row for row in _wrapper_log(log) if row.get("tool") == "run_gsd_stage"
    ]
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
        "printf '%s\\n' \"$@\" > \"$OMEGA_FAKE_OMP_BWRAP_LOG\"\n"
        "while [ \"$1\" != \"--\" ]; do shift; done\n"
        "shift\n"
        "exec \"$@\"\n",
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
    script, log = _install_fake_anda(tmp_path, "happy")
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
    hits = [
        str(p)
        for p in candidates
        if token.encode("utf-8") in p.read_bytes()
    ]
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
    assert data["tasks"][0] == {"id": "gb:q", "state": "working"}
    assert "answer_question" not in _wrapper_tools(log)

