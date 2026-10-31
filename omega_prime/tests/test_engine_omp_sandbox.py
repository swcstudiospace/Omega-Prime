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
import shlex
import shutil
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from omega_prime.agent.interrupt import InterruptFlag
from omega_prime.dashboard.status_file import _write_locked, locked_tasks
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
argv: ``{"behavior": "happy" | "tier_error" | "question" |
"question_reask" | "question_early" | "hang" | "early_done" | "abort_race" |
"fallback" | "long" | "status_fail" | "host_moved" | "leaves_edits" |
"stuck_dirty" | "two_questions" | "up_hangs"}``.
``up`` clones the seeded branch into a private sandbox checkout beside this
script (the host worktree is never written by the fake), and
``export_commits`` commits there and bundles ``base..ref``. One
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

# The real wrapper's question shape (core.ts: omp's questions + method).
QUESTION = {
    "method": "ask",
    "questions": [
        {
            "id": "shape",
            "question": "Which shape do you want?",
            "options": [{"label": "French"}, {"label": "Round"}],
        }
    ],
}


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
pending = None
reasked = False
# two_questions: every live request id, each answerable on its own.
open_requests = set()

# A multi-question ask (two ids), as core.ts forwards omp's ask.
MULTI = {
    "method": "ask",
    "questions": [
        {"id": "a", "question": "Which crust?", "options": [{"label": "Thin"}]},
        {"id": "b", "question": "Why?", "options": []},
    ],
}

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
        if behavior == "question_early":
            # The question lands before the stage-start reply.
            pending = {"stage": stage_id, "request": "req-1", "answered": False}
            emit({
                "event": "question",
                "stageId": stage_id,
                "requestId": "req-1",
                **QUESTION,
            })
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
        if behavior in ("early_done", "question_early"):
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
        if behavior == "two_questions":
            # Two live requests at once; a new one never cancels the first.
            open_requests = {"req-1", "req-2"}
            emit({"event": "question", "stageId": stage_id, "requestId": "req-1", **QUESTION})
            emit({"event": "question", "stageId": stage_id, "requestId": "req-2", **MULTI})
            continue
        if behavior in ("question", "question_reask", "answer_hangs"):
            # The real wrapper holds stage_done until answer_question:
            # one question at a time, a requestId per ask (e2e.py).
            pending = {"stage": stage_id, "request": "req-1", "answered": False}
            emit({
                "event": "question",
                "stageId": stage_id,
                "requestId": pending["request"],
                **QUESTION,
            })
            continue
        emit({"event": "stage_done", "stageId": stage_id, "status": "completed"})
        continue
    if tool == "answer_question":
        stale = {
            "id": rid,
            "ok": False,
            "error": "stale answer (fake)",
            "code": "stale_answer",
        }
        matching = (
            pending is not None
            and not pending["answered"]
            and args.get("stage_id") == pending["stage"]
            and args.get("request_id") == pending["request"]
        )
        if behavior == "two_questions":
            if args.get("request_id") in open_requests:
                open_requests.discard(args.get("request_id"))
                emit({"id": rid, "ok": True, "result": {"answered": True}})
                if not open_requests:
                    emit({"event": "stage_done", "stageId": stage_id, "status": "completed"})
                continue
            emit(stale)
            continue
        if behavior == "answer_hangs":
            # The wrapper stays alive but never replies to the answer.
            continue
        if behavior == "question_reask" and pending is not None and not reasked:
            # The first answer ALWAYS arrives stale: the ask was already
            # superseded server-side. Re-ask under a fresh requestId; the
            # engine is expected to drop the dead item and raise the new
            # one. Any later answer goes stale again once req-2 is done.
            reasked = True
            emit(stale)
            pending = {"stage": stage_id, "request": "req-2", "answered": False}
            emit({
                "event": "question",
                "stageId": stage_id,
                "requestId": "req-2",
                **QUESTION,
            })
            continue
        if matching:
            pending["answered"] = True
            emit({"id": rid, "ok": True, "result": {"answered": True}})
            emit({"event": "stage_done", "stageId": stage_id, "status": "completed"})
            continue
        emit(stale)
        continue
    if tool == "abort":
        if pending is not None and not pending["answered"]:
            # Aborting a stage that waits on a question ends it.
            pending["answered"] = True
            emit({"id": rid, "ok": True, "result": {}})
            emit({"event": "stage_done", "stageId": stage_id, "status": "aborted"})
            continue
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


_HERDR_FAKE = r'''#!/usr/bin/env python3
"""Fake herdr runner: argv receipts beside itself, mode-baked output.

``ok`` mode prints the JSON create reply ``herdr_launch`` parses
(``result.root_pane.pane_id``) and exits 0 for create/run;
``fail`` mode exits 1 on every call, receipts still written. The create
receipt carries the exact pane id it printed so tests can compare the
row's ``herdr_pane`` against what the launcher parsed. Stdlib only, no
env knobs: the same two-key child env the launcher passes is enough.
"""
import json
import os
import sys

MODE = "__MODE__"
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "herdr-log.jsonl")

argv = sys.argv[1:]
row = {"argv": argv}
created = MODE == "ok" and argv[:2] == ["workspace", "create"]
if created:
    row["pane"] = "w1:p%d" % os.getpid()
with open(LOG, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(row) + "\n")
if created:
    sys.stdout.write(
        json.dumps({"result": {"root_pane": {"pane_id": row["pane"]}}}) + "\n"
    )
    sys.exit(0)
if MODE == "ok" and argv[:2] == ["pane", "run"]:
    sys.exit(0)
sys.exit(1)
'''


def _watch_argv(job_id: str, state: Path) -> list[str]:
    """The pinned watcher argv: explicit import root, then the module run."""
    import omega_prime

    root = str(Path(omega_prime.__file__).resolve().parent.parent)
    return [
        shutil.which("env") or "/usr/bin/env",
        f"PYTHONPATH={root}",
        sys.executable,
        "-m",
        "omega_prime.engine.watch",
        job_id,
        "--follow",
        "--state-dir",
        str(state.resolve()),
    ]


def _install_fake_herdr(tmp_path: Path, mode: str = "ok") -> tuple[Path, Path]:
    """Write an executable fake ``herdr`` into the tmp bin dir (first on PATH).

    Returns the script and its receipt log. ``ok`` mode prints a pane id
    that parses under the pinned contract; ``fail`` mode fails every call
    so the no-pane path is deterministic on any box. Receipts let the
    tests pin the exact argv the engine passed (label, watch command).
    """
    binary = tmp_path / "bin" / "herdr"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(_HERDR_FAKE.replace("__MODE__", mode), encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return binary, binary.parent / "herdr-log.jsonl"


_TMUX_FAKE = r'''#!/usr/bin/env python3
"""Fake tmux: argv receipts beside itself; has-session misses, new-session wins."""
import json
import os
import sys

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmux-log.jsonl")
argv = sys.argv[1:]
with open(LOG, "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": argv}) + "\n")
if argv[:1] == ["has-session"]:
    sys.exit(1)
if argv[:1] == ["new-session"]:
    sys.exit(0)
sys.exit(1)
'''


def _install_fake_tmux(tmp_path: Path) -> Path:
    """Write an executable fake ``tmux`` into the tmp bin dir (first on PATH).

    The receipts pin the fallback session-spawn argv; ``has-session``
    always misses so every test exercises the ``new-session`` path.
    """
    binary = tmp_path / "bin" / "tmux"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(_TMUX_FAKE, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return binary


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
    """The engine env for the omp-sandbox backend against the fake wrapper.

    Hermetic by default: without an explicitly installed stub the run gets
    a fail-mode fake herdr (deterministic no-pane path) — a herdr on the
    executor box can never be reached, because the tmp bin dir is FIRST
    on PATH and always carries the fake.
    """
    if not (tmp_path / "bin" / "herdr").exists():
        _install_fake_herdr(tmp_path, "fail")
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


# --- W113-05: the run is visible (row, pane argv, fallback, absence) --------


def test_tracked_run_lands_a_row_working_to_done(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "happy")
    _install_fake_herdr(tmp_path, "ok")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    other = _row("gb:other", "queued", owner={"kind": "agent", "ref": "sol"})
    store = _seed(tmp_path / "tasks.json", rows=[other])
    result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-vis",
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:vis",
    )
    assert result.ok is True, result.detail
    data = _read_tasks(store)
    row = next(r for r in data["tasks"] if r["id"] == "gb:vis")
    assert row["state"] == "done"
    receipts = _jsonl(tmp_path / "bin" / "herdr-log.jsonl")
    create = next(e for e in receipts if e["argv"][:2] == ["workspace", "create"])
    assert row["pane"] == create["pane"]
    assert row["herdr_pane"] == {"pane_id": create["pane"]}
    started = datetime.fromisoformat(row["started_at"])
    activity = datetime.fromisoformat(row["last_activity_at"])
    assert started <= activity
    assert row["last_event"] == "stage_done completed"
    assert set(row["receipt"]) == {"job_id", "stage_id", "ok", "status", "export"}
    assert row["receipt"]["job_id"] == "job-vis"
    assert row["receipt"]["stage_id"] == "stage-1"
    assert row["receipt"]["ok"] is True
    assert row["receipt"]["status"] == "completed"
    assert set(row["receipt"]["export"]) == {
        "head",
        "base",
        "ref",
        "commits",
        "dirty",
        "bundle",
        "bytes",
        "via",
    }
    assert row["receipt"]["export"]["via"] == "fake"
    # An unrelated row keeps its identity, owner and state through the run.
    kept = next(r for r in data["tasks"] if r["id"] == "gb:other")
    assert {k: kept[k] for k in ("id", "title", "owner", "state")} == {
        k: other[k] for k in ("id", "title", "owner", "state")
    }


def test_pane_label_and_watch_argv_are_pinned(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "happy")
    _install_fake_herdr(tmp_path, "ok")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:argv")])
    result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-argv",
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:argv",
    )
    assert result.ok is True, result.detail
    receipts = _jsonl(tmp_path / "bin" / "herdr-log.jsonl")
    create = next(e for e in receipts if e["argv"][:2] == ["workspace", "create"])
    pane_run = next(e for e in receipts if e["argv"][:2] == ["pane", "run"])
    assert create["argv"] == [
        "workspace",
        "create",
        "--cwd",
        str(work),
        "--label",
        "gb:argv plan",
        "--no-focus",
    ]
    # One shell string, as herdr_launch.launch sends it.
    assert pane_run["argv"] == [
        "pane",
        "run",
        create["pane"],
        shlex.join(_watch_argv("job-argv", tmp_path / "state")),
    ]


def test_tmux_fallback_records_the_session(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "happy")
    _, herdr_log = _install_fake_herdr(tmp_path, "fail")
    _install_fake_tmux(tmp_path)
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(
        tmp_path,
        omp,
        script,
        roots,
        OMEGA_PRIME_OMP_ALLOW_ZAI="1",
        OMEGA_PRIME_ENGINE_TMUX="1",
    )
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:tmux")])
    result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-tmux",
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:tmux",
    )
    assert result.ok is True, result.detail
    data = _read_tasks(store)
    row = next(r for r in data["tasks"] if r["id"] == "gb:tmux")
    assert row["state"] == "done"
    assert row["pane"] == "tmux:omega-job-tmux"
    assert row["herdr_pane"] is None, "a tmux session is not an attachable herdr pane"
    receipts = _jsonl(tmp_path / "bin" / "tmux-log.jsonl")
    assert receipts[0]["argv"] == ["has-session", "-t", "omega-job-tmux"]
    assert receipts[1]["argv"] == [
        "new-session",
        "-d",
        "-s",
        "omega-job-tmux",
        "--",
        *_watch_argv("job-tmux", tmp_path / "state"),
    ]
    # The failed herdr create was receipted; pane run never happened.
    assert [e["argv"][:2] for e in _jsonl(herdr_log)] == [["workspace", "create"]]


def test_no_pane_leaves_the_key_absent_and_the_run_healthy(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "happy")
    # No explicit stub: _backend_env installs the fail-mode herdr itself,
    # and OMEGA_PRIME_ENGINE_TMUX stays "0" — the deterministic no-pane path.
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:nopane")])
    result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-nopane",
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:nopane",
    )
    assert result.ok is True, result.detail
    data = _read_tasks(store)
    row = next(r for r in data["tasks"] if r["id"] == "gb:nopane")
    assert row["state"] == "done"
    assert "pane" not in row
    assert row["herdr_pane"] is None
    # The failing create was attempted exactly once — never fatal.
    assert [e["argv"][:2] for e in _jsonl(tmp_path / "bin" / "herdr-log.jsonl")] == [
        ["workspace", "create"]
    ]


def test_untracked_backend_run_writes_nothing(tmp_path: Path) -> None:
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")
    _install_fake_herdr(tmp_path, "fail")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    store = tmp_path / "tasks.json"
    result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-untracked",
        stage="plan",
        plan="zai",
    )
    assert result.ok is True, result.detail
    # Exactly the phase-112 shape: the backend ran, nothing was tracked.
    assert result.sandbox == "omp-sandbox"
    assert result.stage == "plan"
    assert result.final_text == "fake final text"
    assert not store.exists()
    assert _jsonl(tmp_path / "bin" / "herdr-log.jsonl") == []
    assert "answer_question" not in _wrapper_tools(log)


def _shaped_answer_token() -> str:
    """A credential-shaped value built at runtime, never a literal."""
    return "".join(("sk-", "ans", "wer-", "not", "-for-", "disk"))


def _answer_when_raised(
    store: Path, item_id: str, answer: str | dict[str, Any], timeout: float = 10.0
) -> None:
    """Responder side of the round-trip: poll, then write the answer.

    The poll bound is TEST-side only — the engine wait has no deadline
    (phase-112 lock). The write is the exact production responder
    convention: one ``locked_tasks(write=True)`` block plus
    ``_write_locked``, so it serializes against the engine's polls.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with locked_tasks(store, write=False) as data:
            raised = any(item.get("id") == item_id for item in data["waiting_on_ove"])
        if raised:
            with locked_tasks(store, write=True) as data:
                target = next(
                    item for item in data["waiting_on_ove"] if item["id"] == item_id
                )
                target["answer"] = answer
                _write_locked(data, store)
            return
        time.sleep(0.05)
    raise AssertionError(f"{item_id} never appeared within {timeout:.0f}s")


def _question_fixture(
    tmp_path: Path, behavior: str
) -> tuple[dict[str, str], Path, Path, Path]:
    """Fixture shape shared by the question round-trip tests.

    Built in the MAIN thread before the run thread starts, so the
    responder can hold the store path while the run is still waiting.
    """
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, behavior)
    _install_fake_herdr(tmp_path, "ok")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:q")])
    return env, store, log, work


def _run_tracked(env: dict[str, str], work: Path, store: Path, job_id: str) -> Any:
    """One tracked question-stage run through the real engine."""
    return run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id=job_id,
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:q",
    )


def test_question_round_trip_resumes_the_run(tmp_path: Path) -> None:
    """The n4 contract: a GSD question is answerable mid-run.

    Cutover (documented): this replaces the phase-112 pin
    ``test_question_surfaces_as_event_only``, which asserted the n4
    BOUNDARY — a requestId-less question stayed an event only, no item
    was raised, no answer_question call, row exactly
    ``{"id": "gb:q", "state": "working"}``. n4 ships the round-trip, so
    the pin flips to the strictly stronger contract: every old axis is
    re-asserted on the new shape (one question event, attestation mock)
    and MORE is asserted — the item raised under the stable id, the
    answer piped through answer_question with matching ids, the item
    cleared, the row done, the run ok.
    """
    env, store, log, work = _question_fixture(tmp_path, "question")
    box: dict[str, Any] = {}
    failures: list[BaseException] = []

    def runner() -> None:
        try:
            box["run"] = _run_tracked(env, work, store, "job-q")
        except BaseException as exc:  # surfaced in the main thread
            failures.append(exc)

    def responder() -> None:
        try:
            deadline = time.monotonic() + 10.0
            item = None
            while time.monotonic() < deadline:
                with locked_tasks(store, write=False) as data:
                    raised = [
                        row
                        for row in data["waiting_on_ove"]
                        if row.get("id") == "gb:q:question:req-1"
                    ]
                if raised:
                    item = raised[0]
                    break
                time.sleep(0.05)
            if item is None:
                raise AssertionError("the question item never appeared")
            # Mid-run: exactly one live item, and the row is blocked.
            assert item["ask"] == "Which shape do you want? (options: French / Round)"
            assert item["blocks"] == "sandbox GSD question (stage plan)"
            with locked_tasks(store, write=False) as data:
                assert [row["id"] for row in data["waiting_on_ove"]] == [
                    "gb:q:question:req-1"
                ]
                row = next(r for r in data["tasks"] if r["id"] == "gb:q")
                assert row["state"] == "blocked"
            _answer_when_raised(store, "gb:q:question:req-1", "the square one")
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    responder()
    thread.join(timeout=30.0)
    _check_round_trip(box, failures, thread, "the answer")
    result = box["run"]
    assert result.ok is True, result.detail
    # The question event survived — exactly one, requestId stamped.
    events = _jsonl(Path(result.events_path))
    questions = [row for row in events if row.get("event") == "question"]
    assert len(questions) == 1
    assert questions[0]["requestId"] == "req-1"
    assert questions[0]["attestation"] == "mock"
    # The answer travelled through answer_question with the matching ids.
    assert [
        (row["tool"], row["args"])
        for row in _wrapper_log(log)
        if row.get("tool") == "answer_question"
    ] == [
        (
            "answer_question",
            {
                "stage_id": "stage-1",
                "request_id": "req-1",
                "text": "the square one",
            },
        )
    ]
    # The item cleared and the run finished.
    data = _read_tasks(store)
    assert data["waiting_on_ove"] == []
    row = next(r for r in data["tasks"] if r["id"] == "gb:q")
    assert row["state"] == "done"


def _check_round_trip(
    box: dict[str, Any],
    failures: list[BaseException],
    thread: threading.Thread,
    what: str,
) -> None:
    """Shared round-trip epilogue: surface responder/run errors, join bound."""
    if failures:
        raise failures[0]
    assert not thread.is_alive(), f"the run never resumed after {what}"


def test_stale_answer_reasks_under_a_new_item(tmp_path: Path) -> None:
    """A stale answer clears the dead item; the re-ask arrives under a new id."""
    env, store, log, work = _question_fixture(tmp_path, "question_reask")
    box: dict[str, Any] = {}
    failures: list[BaseException] = []

    def runner() -> None:
        try:
            box["run"] = _run_tracked(env, work, store, "job-reask")
        except BaseException as exc:
            failures.append(exc)

    def responder() -> None:
        try:
            _answer_when_raised(store, "gb:q:question:req-1", "first instinct")
            # The stale reply killed req-1: exactly the re-asked item remains.
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                with locked_tasks(store, write=False) as data:
                    ids = [row["id"] for row in data["waiting_on_ove"]]
                if ids == ["gb:q:question:req-2"]:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError(f"req-2 never replaced req-1; last ids {ids}")
            _answer_when_raised(store, "gb:q:question:req-2", "the square one")
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    responder()
    thread.join(timeout=30.0)
    _check_round_trip(box, failures, thread, "the re-ask")
    result = box["run"]
    assert result.ok is True, result.detail
    # Both asks are in the event feed, each under its own requestId.
    events = _jsonl(Path(result.events_path))
    assert [row["requestId"] for row in events if row.get("event") == "question"] == [
        "req-1",
        "req-2",
    ]
    # Exactly two answer_question calls: the stale one and the good one.
    answers = [
        row["args"] for row in _wrapper_log(log) if row.get("tool") == "answer_question"
    ]
    assert [args["request_id"] for args in answers] == ["req-1", "req-2"]
    assert [args["text"] for args in answers] == ["first instinct", "the square one"]
    data = _read_tasks(store)
    assert data["waiting_on_ove"] == []
    row = next(r for r in data["tasks"] if r["id"] == "gb:q")
    assert row["state"] == "done"


def test_answer_text_never_lands_in_durable_dashboard_artifacts(
    tmp_path: Path,
) -> None:
    """A credential-shaped answer never lands on disk, not even in transit.

    main's validated tasks.json store redacts every value it writes, so the
    responder's answer is redacted at rest before the engine reads it: the
    answer_question call carries the redacted text and the raw token never
    reaches tasks.json, events.jsonl, the herdr receipts or the wrapper.
    """
    token = _shaped_answer_token()
    answer = f"paste {token} to unblock"
    env, store, log, work = _question_fixture(tmp_path, "question")
    box: dict[str, Any] = {}
    failures: list[BaseException] = []

    def runner() -> None:
        try:
            box["run"] = _run_tracked(env, work, store, "job-secret-answer")
        except BaseException as exc:
            failures.append(exc)

    def responder() -> None:
        try:
            _answer_when_raised(store, "gb:q:question:req-1", answer)
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    responder()
    thread.join(timeout=30.0)
    _check_round_trip(box, failures, thread, "the answer")
    result = box["run"]
    assert result.ok is True, result.detail
    # The call carried the answer as the store holds it: token redacted.
    from omega_prime.dashboard.redact import redact_value

    expected = redact_value(answer)
    assert expected != answer, "the shaped token is one the store redacts"
    assert [
        row["args"].get("text")
        for row in _wrapper_log(log)
        if row.get("tool") == "answer_question"
    ] == [expected]
    assert token.encode("utf-8") not in log.read_bytes()
    # The durable product surfaces never saw it — byte-scan, paths only.
    assert token.encode("utf-8") not in store.read_bytes()
    assert token.encode("utf-8") not in Path(result.events_path).read_bytes()
    assert (
        token.encode("utf-8") not in (tmp_path / "bin" / "herdr-log.jsonl").read_bytes()
    )


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


def test_question_text_keeps_the_real_wrappers_questions_and_choices() -> None:
    from omega_prime.engine.sandbox_omp import _question_text

    assert (
        _question_text(
            {
                "event": "question",
                "method": "ask",
                "questions": [
                    {
                        "id": "a",
                        "question": "Which shape?",
                        "options": [{"label": "French"}, {"label": "Round"}],
                    },
                    {"id": "b", "question": "Why?", "options": []},
                ],
            }
        )
        == "Which shape? (options: French / Round) | Why?"
    )
    assert (
        _question_text({"method": "select", "title": "Pick", "options": ["x", "y"]})
        == "Pick (options: x / y)"
    )
    assert _question_text({"method": "confirm", "title": "Go on?"}) == "Go on?"
    assert _question_text({"method": "input"}) == (
        "the sandbox stage asks for input (input)"
    )


def test_a_question_before_the_stage_start_reply_is_still_answered(
    tmp_path: Path,
) -> None:
    env, store, log, work = _question_fixture(tmp_path, "question_early")
    box: dict[str, Any] = {}
    failures: list[BaseException] = []

    def runner() -> None:
        try:
            box["run"] = _run_tracked(env, work, store, "job-q-early")
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    try:
        _answer_when_raised(store, "gb:q:question:req-1", "the square one")
    except BaseException as exc:
        failures.append(exc)
    thread.join(timeout=30.0)
    _check_round_trip(box, failures, thread, "an early question")
    assert box["run"].ok is True, box["run"].detail
    answers = [
        r["args"] for r in _wrapper_log(log) if r.get("tool") == "answer_question"
    ]
    assert answers == [
        {"stage_id": "stage-1", "request_id": "req-1", "text": "the square one"}
    ]
    assert _read_tasks(store)["waiting_on_ove"] == []


def test_a_cancelled_run_clears_its_open_question(tmp_path: Path) -> None:
    env, store, _log, work = _question_fixture(tmp_path, "question")
    interrupt = InterruptFlag()
    outcome: dict[str, Any] = {}
    worker = threading.Thread(
        target=lambda: outcome.update(
            result=run(
                "plan the thing",
                cwd=work,
                env=env,
                job_id="job-q-cancel",
                stage="plan",
                plan="zai",
                tasks_path=store,
                task_id="gb:q",
                interrupt=interrupt,
            )
        ),
        daemon=True,
    )
    worker.start()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if _read_tasks(store)["waiting_on_ove"]:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the question item never appeared")
    interrupt.set()
    worker.join(timeout=30.0)
    assert not worker.is_alive(), "cancellation waited forever"
    assert outcome["result"].reason == "cancelled"
    data = _read_tasks(store)
    assert data["waiting_on_ove"] == []
    row = next(r for r in data["tasks"] if r["id"] == "gb:q")
    assert row["state"] == "failed"


def test_a_relative_state_dir_reaches_the_pane_absolute(
    tmp_path: Path, monkeypatch: Any
) -> None:
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "happy")
    _install_fake_herdr(tmp_path, "ok")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    monkeypatch.chdir(tmp_path)
    env["OMEGA_PRIME_STATE_DIR"] = "rel-state"
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:rel")])
    result = run(
        "plan",
        cwd=work,
        env=env,
        job_id="job-rel",
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:rel",
    )
    assert result.ok is True, result.detail
    receipts = _jsonl(tmp_path / "bin" / "herdr-log.jsonl")
    pane_run = next(e for e in receipts if e["argv"][:2] == ["pane", "run"])
    watch = shlex.split(pane_run["argv"][3])
    state = watch[watch.index("--state-dir") + 1]
    assert state == str((tmp_path / "rel-state").resolve())
    assert (Path(state) / "jobs").is_dir()


def test_a_hung_herdr_still_falls_back_to_tmux(monkeypatch: Any) -> None:
    from omega_prime.dashboard import sandbox_pane

    def hung(*_args: Any, **_kwargs: Any) -> str | None:
        raise subprocess.TimeoutExpired(["herdr"], 1)

    calls: list[str | None] = []

    def tmux(_env: Any, *, command: list[str], session: str | None) -> str | None:
        calls.append(session)
        return f"tmux:{session}"

    monkeypatch.setattr(sandbox_pane, "_herdr_pane", hung)
    monkeypatch.setattr(sandbox_pane, "_tmux_pane", tmux)
    pane = sandbox_pane.open_pane(
        {}, cwd="/tmp", label="x", command=["true"], session="omega-job"
    )
    assert pane == "tmux:omega-job"
    assert calls == ["omega-job"]


def test_watch_shows_sandbox_events_by_name_and_detail() -> None:
    from omega_prime.engine.watch import format_event

    question = format_event(
        {
            "event": "question",
            "stageId": "st-1",
            "requestId": "req-1",
            "method": "ask",
            "questions": [
                {
                    "id": "shape",
                    "question": "Which shape?",
                    "options": [{"label": "French"}],
                }
            ],
        }
    )
    assert question.startswith("question stage=st-1 request=req-1 method=ask")
    assert "Which shape?" in question and "French" in question
    assert format_event(
        {"event": "stage_done", "stageId": "st-1", "status": "completed"}
    ) == ("stage_done stage=st-1 status=completed")
    assert format_event(
        {"event": "fallback", "stageId": "st-1", "from": "a/b", "to": "c/d"}
    ) == ("fallback stage=st-1 from=a/b to=c/d")
    # omp's own events keep their type-keyed rendering.
    assert format_event({"type": "agent_end"}) == "agent_end"


def test_a_failed_run_keeps_its_terminal_row_beside_the_repair_item(
    tmp_path: Path,
) -> None:
    from omega_prime.dashboard.blocks import clear_block

    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "status_fail")
    _install_fake_herdr(tmp_path, "ok")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:late")])
    result = run(
        "plan",
        cwd=work,
        env=env,
        job_id="job-late-row",
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:late",
    )
    assert result.reason == "sandbox_unavailable" and result.started is True
    data = _read_tasks(store)
    # The run ended failed; the repair request is its own item.
    assert next(r for r in data["tasks"] if r["id"] == "gb:late")["state"] == "failed"
    assert [i["id"] for i in data["waiting_on_ove"]] == ["gb:late:sandbox_unavailable"]
    clear_block(store, "gb:late:sandbox_unavailable", "gb:late")
    data = _read_tasks(store)
    assert next(r for r in data["tasks"] if r["id"] == "gb:late")["state"] == "failed"


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


def test_two_live_questions_each_keep_their_item_and_their_own_answers(
    tmp_path: Path,
) -> None:
    """A second live request never clears the first one's item, and a
    multi-question ask passes each question its own answer (per_question)."""
    env, store, log, work = _question_fixture(tmp_path, "two_questions")
    box: dict[str, Any] = {}
    failures: list[BaseException] = []

    def runner() -> None:
        try:
            box["run"] = _run_tracked(env, work, store, "job-two-q")
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10.0
        items: dict[str, dict[str, Any]] = {}
        while time.monotonic() < deadline and len(items) < 2:
            with locked_tasks(store, write=False) as data:
                items = {row["id"]: row for row in data["waiting_on_ove"]}
            time.sleep(0.05)
        # Both requests stay answerable side by side.
        assert sorted(items) == ["gb:q:question:req-1", "gb:q:question:req-2"]
        assert items["gb:q:question:req-2"]["question_ids"] == ["a", "b"]
        assert items["gb:q:question:req-1"]["question_ids"] == ["shape"]
        _answer_when_raised(
            store,
            "gb:q:question:req-2",
            {"per_question": {"a": {"selected": ["Thin"]}, "b": "it is crisp"}},
        )
        _answer_when_raised(store, "gb:q:question:req-1", "Round")
    except BaseException as exc:
        failures.append(exc)
    thread.join(timeout=30.0)
    _check_round_trip(box, failures, thread, "both answers")
    assert box["run"].ok is True, box["run"].detail
    answers = {
        r["args"]["request_id"]: r["args"]
        for r in _wrapper_log(log)
        if r.get("tool") == "answer_question"
    }
    assert answers == {
        "req-1": {"stage_id": "stage-1", "request_id": "req-1", "text": "Round"},
        "req-2": {
            "stage_id": "stage-1",
            "request_id": "req-2",
            "per_question": {"a": {"selected": ["Thin"]}, "b": {"text": "it is crisp"}},
        },
    }
    data = _read_tasks(store)
    assert data["waiting_on_ove"] == []
    assert next(r for r in data["tasks"] if r["id"] == "gb:q")["state"] == "done"


def test_answer_args_shapes() -> None:
    from omega_prime.engine.sandbox_omp import _answer_args

    assert _answer_args("x") == {"text": "x"}
    assert _answer_args({"selected": ["a"]}) == {"selected": ["a"]}
    assert _answer_args({"text": "t"}) == {"text": "t"}
    assert _answer_args({"per_question": {"q1": "t", "q2": {"selected": ["s"]}}}) == {
        "per_question": {"q1": {"text": "t"}, "q2": {"selected": ["s"]}}
    }
    # A written answer beside a selection (even an empty one) reaches the
    # wrapper too: text is its custom input, never dropped for `selected`.
    assert _answer_args({"selected": [], "text": "use the existing layout"}) == {
        "selected": [],
        "text": "use the existing layout",
    }
    assert _answer_args(
        {"per_question": {"q1": {"selected": ["s"], "text": "and also this"}}}
    ) == {"per_question": {"q1": {"selected": ["s"], "text": "and also this"}}}
    # Not an answer yet: nothing usable to send.
    assert _answer_args({"per_question": {"q1": 3}}) == {}
    assert _answer_args({"note": "later"}) == {}


def test_a_run_that_does_not_own_the_row_never_finishes_it(tmp_path: Path) -> None:
    """A finished row of no known run stays that record: the new run gets no
    row (start declines), so its finish never overwrites the old receipt."""
    omp = _install_fake(tmp_path)
    script, _log = _install_fake_anda(tmp_path, "happy")
    _install_fake_herdr(tmp_path, "ok")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:closed", "failed")])
    before = store.read_bytes()
    result = run(
        "plan",
        cwd=work,
        env=env,
        job_id="job-closed",
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:closed",
    )
    assert result.ok is True, result.detail
    assert store.read_bytes() == before


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


def test_a_run_without_its_row_still_gets_answerable_questions(
    tmp_path: Path,
) -> None:
    """start declines a finished row of no known run, but the stage still
    launches: its question is raised and answered, and the row stays the
    finished record it was."""
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "question")
    _install_fake_herdr(tmp_path, "ok")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:q", "failed")])
    box: dict[str, Any] = {}
    failures: list[BaseException] = []

    def runner() -> None:
        try:
            box["run"] = _run_tracked(env, work, store, "job-unowned-q")
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    try:
        _answer_when_raised(store, "gb:q:question:req-1", "Round")
    except BaseException as exc:
        failures.append(exc)
    thread.join(timeout=30.0)
    _check_round_trip(box, failures, thread, "the answer")
    assert box["run"].ok is True, box["run"].detail
    assert [
        r["args"]["text"]
        for r in _wrapper_log(log)
        if r.get("tool") == "answer_question"
    ] == ["Round"]
    data = _read_tasks(store)
    assert data["waiting_on_ove"] == []
    assert [_id_state(r) for r in data["tasks"]] == [{"id": "gb:q", "state": "failed"}]


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


def test_a_stop_during_pane_setup_still_submits_nothing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Cancellation that lands while the pane opens is honoured pre-submit."""
    omp = _install_fake(tmp_path)
    script, log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    env = _backend_env(tmp_path, omp, script, roots, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:pane-stop")])
    interrupt = InterruptFlag()

    def slow_pane(*_args: Any, **_kwargs: Any) -> None:
        interrupt.set()  # the user stops the run while herdr is busy
        return None

    monkeypatch.setattr(sandbox_omp.sandbox_pane, "open_pane", slow_pane)
    result = run(
        "plan the thing",
        cwd=work,
        env=env,
        job_id="job-pane-stop",
        stage="plan",
        plan="zai",
        tasks_path=store,
        task_id="gb:pane-stop",
        interrupt=interrupt,
    )
    assert result.ok is False
    assert result.reason == "cancelled"
    tools = _wrapper_tools(log)
    assert "run_gsd_stage" not in tools
    assert "down" in tools
    row = next(r for r in _read_tasks(store)["tasks"] if r["id"] == "gb:pane-stop")
    assert row["state"] != "working", "a stopped run must not stay working"


def test_a_failed_question_write_is_retried_not_lost(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A transient block-write failure keeps the question scheduled."""
    env, store, _log, work = _question_fixture(tmp_path, "question")
    real = sandbox_omp._raise_question
    calls: list[str] = []

    def flaky(*args: Any, **kwargs: Any) -> str:
        calls.append(str(args[3]))
        if len(calls) == 1:
            raise OSError("No space left on device")
        return real(*args, **kwargs)

    monkeypatch.setattr(sandbox_omp, "_raise_question", flaky)
    box: dict[str, Any] = {}
    failures: list[BaseException] = []

    def runner() -> None:
        try:
            box["run"] = _run_tracked(env, work, store, "job-q-retry")
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    try:
        _answer_when_raised(store, "gb:q:question:req-1", "the square one")
    except BaseException as exc:
        failures.append(exc)
    thread.join(timeout=30.0)
    _check_round_trip(box, failures, thread, "a retried question write")
    assert box["run"].ok is True, box["run"].detail
    assert calls[:2] == ["req-1", "req-1"]
    assert _read_tasks(store)["waiting_on_ove"] == []


def test_a_stop_while_an_answer_reply_hangs_aborts_promptly(tmp_path: Path) -> None:
    """The answer_question wait honours the interrupt, then abort runs."""
    env, store, log, work = _question_fixture(tmp_path, "answer_hangs")
    interrupt = InterruptFlag()
    outcome: dict[str, Any] = {}
    worker = threading.Thread(
        target=lambda: outcome.update(
            result=run(
                "plan the thing",
                cwd=work,
                env=env,
                job_id="job-q-hang",
                stage="plan",
                plan="zai",
                tasks_path=store,
                task_id="gb:q",
                interrupt=interrupt,
            )
        ),
        daemon=True,
    )
    worker.start()
    _answer_when_raised(store, "gb:q:question:req-1", "the square one")
    deadline = time.monotonic() + 10.0
    while "answer_question" not in _wrapper_tools(log):
        if time.monotonic() > deadline:
            raise AssertionError("the answer was never sent")
        time.sleep(0.05)
    began = time.monotonic()
    interrupt.set()
    worker.join(timeout=30.0)
    assert not worker.is_alive(), "cancellation waited on the answer reply"
    assert time.monotonic() - began < 30.0
    assert outcome["result"].reason == "cancelled"
    assert "abort" in _wrapper_tools(log)
