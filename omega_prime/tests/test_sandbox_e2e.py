"""End-to-end proof of the omp-sandbox wiring from Omega Prime's own loop.

Hermetic contract: everything here runs against the fake omp binary, the
fake herdr runner and a self-contained two-stage fake omp-sandbox-anda
wrapper doing real GSD-shaped git work in a throwaway temp worktree under
a temp allowlisted root, with a temp tasks store and a temp state dir.
No herdr, tmux, podman, gh, openshell, network or model call ever happens.
The one opt-in test drives the REAL wrapper on the host only, behind the
OMEGA_PRIME_SANDBOX_E2E_REAL marker; it reads consent/API envs
presence/truthiness-only and never prints any env value — effects only.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime
from pathlib import Path

import pytest

from omega_prime.engine import sandbox_omp
from omega_prime.engine.omp_driver import run
from omega_prime.routines.ultrathink_turn import execute_ultrathink
from omega_prime.tests.test_engine import _env, _install_fake
from omega_prime.tests.test_engine_omp_sandbox import (
    _backend_env,
    _git,
    _git_worktree,
    _install_fake_anda,
    _install_fake_herdr,
    _jsonl,
    _read_tasks,
    _row,
    _seed,
    _shaped_zai_key,
    _wrapper_log,
    _wrapper_tools,
)

_REAL_MARKER = "OMEGA_PRIME_SANDBOX_E2E_REAL"

_SPEC_TEXT = "Wire the demo greeting for the throwaway project."


_E2E_ANDA_FAKE = r'''#!/usr/bin/env python3
"""Self-contained two-stage fake omp-sandbox-anda: client.rs stdio protocol.

One receipt line per request ({"id", "tool", "args"}) is appended to
wrapper-log.jsonl beside this script before the reply is written; the
startup receipt carries the observed OMP_SANDBOX_WORKSPACE_ROOTS. Every
emitted line carries "attestation": "mock". ``down`` is answered and the
wrapper then exits. ``up`` clones the seeded branch into a private sandbox
checkout beside this script (like the real wrapper, the host worktree is
never written here), and ``run_gsd_stage`` performs REAL GSD-shaped stage
work in that checkout: the plan stage writes
.planning/phases/999-demo/PLAN.md and commits "gsd(plan): demo plan";
the execute stage writes src/demo/greeting.txt and commits
"gsd(execute): demo greeting". ``export_commits`` makes a real git
bundle of ``base..ref`` there; the engine applies it to the host branch.
Stdlib only.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "wrapper-log.jsonl")
EXPORTS = [0]


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


def run_gsd_stage(work, args):
    """Real GSD-shaped stage work: write the stage's file, commit it."""
    stage = args.get("stage")
    first_line = str(args.get("task") or "").splitlines()[0]
    if stage == "plan":
        path = os.path.join(work, ".planning", "phases", "999-demo", "PLAN.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        body = "# Demo plan\n" + first_line + "\n"
        subject = "gsd(plan): demo plan"
    elif stage == "execute":
        path = os.path.join(work, "src", "demo", "greeting.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        body = "hello from the e2e fake\n" + first_line + "\n"
        subject = "gsd(execute): demo greeting"
    else:
        raise SystemExit("unknown stage %r" % stage)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    git(work, "add", "--", os.path.relpath(path, work))
    git(
        work,
        "-c", "user.name=E2E Anda",
        "-c", "user.email=e2e-anda@example.invalid",
        "commit", "-q", "-m", subject,
    )
    EXPORTS[0] = 0
    return "stage-e2e"


def export_commits(work):
    """Bundle the branch after the stage's commit."""
    EXPORTS[0] += 1
    head = git(work, "rev-parse", "HEAD")
    base = git(work, "rev-parse", "HEAD~1")
    ref = git(work, "symbolic-ref", "--short", "HEAD")
    dirty = git(work, "status", "--porcelain") != ""
    bundle = os.path.join(HERE, "export-%d.bundle" % EXPORTS[0])
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


receipt({
    "ready": True,
    "pid": os.getpid(),
    "argv": sys.argv[1:],
    "workspace_roots": os.environ.get("OMP_SANDBOX_WORKSPACE_ROOTS"),
})
emit({"event": "wrapper_ready", "wrapper_pid": os.getpid()})

work = None
box = None
stage_id = None

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
        stage_id = run_gsd_stage(box, args)
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
        emit({"event": "stage_done", "stageId": stage_id, "status": "completed"})
        continue
    if tool == "sandbox_status":
        emit({
            "id": rid,
            "ok": True,
            "result": {
                "stageId": stage_id,
                "status": "completed",
                "lastText": "e2e fake final text",
                "finalText": "e2e fake final text",
                "error": None,
            },
        })
        continue
    if tool == "export_commits":
        emit({
            "id": rid,
            "ok": True,
            "result": export_commits(box),
        })
        continue
    emit({
        "id": rid,
        "ok": False,
        "error": "unknown tool %s" % tool,
        "code": "unknown_tool",
    })
'''


def _install_e2e_anda(tmp_path: Path) -> tuple[Path, Path]:
    """Write the two-stage fake wrapper into its own dir; return (script, log)."""
    home = tmp_path / "anda-e2e"
    home.mkdir(exist_ok=True)
    script = home / "fake-anda-e2e"
    script.write_text(_E2E_ANDA_FAKE, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script, home / "wrapper-log.jsonl"


def _seed_turn(state: Path, stage: str, spec_text: str) -> None:
    """Seed <state>/ultrathink with the spec and a fresh stage-named plan."""
    home = state / "ultrathink"
    home.mkdir(parents=True, exist_ok=True)
    spec = home / "spec.md"
    spec.write_text(spec_text + "\n", encoding="utf-8")
    (home / "last-plan.json").write_text(
        json.dumps({"specPath": str(spec), "stage": stage}) + "\n",
        encoding="utf-8",
    )


def test_routine_drives_plan_then_execute_end_to_end(
    tmp_path: Path, monkeypatch
) -> None:
    omp = _install_fake(tmp_path)
    _install_fake_herdr(tmp_path, "ok")  # BEFORE _backend_env: keep the ok stub
    script, log = _install_e2e_anda(tmp_path)
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    store = _seed(
        tmp_path / "tasks.json",
        rows=[_row("gb:sbx5-plan"), _row("gb:sbx5-exec")],
    )
    extra = {"OMEGA_PRIME_OMP_ALLOW_ZAI": "1"}
    base = _backend_env(tmp_path, omp, script, roots, **extra)
    monkeypatch.chdir(work)  # ultrathink runs on the process cwd
    state = Path(base["OMEGA_PRIME_STATE_DIR"])

    for stage, task_id, model in (
        ("plan", "gb:sbx5-plan", "glm-5.3"),
        ("execute", "gb:sbx5-exec", "glm-5.3-flash"),
    ):
        _seed_turn(state, stage, _SPEC_TEXT)
        env = dict(
            base,
            OMEGA_PRIME_TASKS_PATH=str(store),
            OMEGA_PRIME_TASK_ID=task_id,
        )
        plan = execute_ultrathink(env)
        assert plan["found"] is True
        engine = plan["engine"]
        assert engine["used"] is True, engine
        assert engine["plan"] == "zai"
        assert engine["stage"] == stage
        assert engine["model"] == model
        assert "failed" not in plan
        session = json.loads(
            (state / "jobs" / engine["job_id"] / "session.json").read_text(
                encoding="utf-8"
            )
        )
        assert session["sandbox"] == "omp-sandbox"
        assert session["model"] == model
        assert session["stage"] == stage
        assert session["ok"] is True

    # Wrapper receipts: the whole chain, in order, with the contracted args.
    stages = [row for row in _wrapper_log(log) if row.get("tool") == "run_gsd_stage"]
    assert [row["args"]["stage"] for row in stages] == ["plan", "execute"]
    assert [row["args"]["plan"] for row in stages] == ["zai", "zai"]
    assert [row["args"]["model"] for row in stages] == [
        "zai/glm-5.3",
        "zai/glm-5.3-flash",
    ]
    assert [row["args"]["gsd_command"] for row in stages] == [
        "gsd-plan-phase",
        "gsd-execute-phase",
    ]
    assert all(_SPEC_TEXT in row["args"]["task"] for row in stages)
    ups = [row for row in _wrapper_log(log) if row.get("tool") == "up"]
    assert len(ups) == 2
    for row in ups:
        assert row["args"]["seed_repo"]["path"] == str(work)
    ready = [row for row in _wrapper_log(log) if row.get("ready")]
    assert len(ready) == 2
    assert {row["workspace_roots"] for row in ready} == {str(roots)}
    events: list[dict] = []
    for job_dir_path in sorted((state / "jobs").iterdir()):
        events_path = job_dir_path / "events.jsonl"
        if events_path.is_file():
            events.extend(
                row for row in _jsonl(events_path) if "event" in row and "id" not in row
            )
    assert events
    assert all(row.get("attestation") == "mock" for row in events)

    # The exported commits really landed in the job worktree.
    subjects = _git("log", "--format=%s", cwd=work).splitlines()
    assert "gsd(plan): demo plan" in subjects
    assert "gsd(execute): demo greeting" in subjects
    assert len(subjects) >= 3  # seed + both GSD-shaped commits
    assert (work / ".planning" / "phases" / "999-demo" / "PLAN.md").is_file()
    assert (work / "src" / "demo" / "greeting.txt").is_file()

    # Rows and panes: one row per stage, each with its own timeline.
    data = _read_tasks(store)
    herdr_receipts = _jsonl(tmp_path / "bin" / "herdr-log.jsonl")
    for task_id, stage in (("gb:sbx5-plan", "plan"), ("gb:sbx5-exec", "execute")):
        row = next(r for r in data["tasks"] if r["id"] == task_id)
        assert row["state"] == "done"
        assert row["stage"] == stage
        create = next(
            e
            for e in herdr_receipts
            if e["argv"][:2] == ["workspace", "create"]
            and f"{task_id} {stage}" in e["argv"]
        )
        assert row["herdr_pane"] == create["pane"]
        started = datetime.fromisoformat(
            row.get("started_at") or row["last_activity_at"]
        )
        activity = datetime.fromisoformat(row["last_activity_at"])
        assert started <= activity
        assert row["last_event"] == "stage_done completed"
        assert set(row["receipt"]) == {
            "job_id",
            "stage_id",
            "ok",
            "status",
            "export",
        }
        assert row["receipt"]["ok"] is True
        assert row["receipt"]["status"] == "completed"
        export = row["receipt"]["export"]
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
        assert export["commits"] >= 1
        assert Path(export["bundle"]).is_file()


def test_forced_plan_gate_blocks_the_row_in_the_same_call(
    tmp_path: Path, monkeypatch
) -> None:
    omp = _install_fake(tmp_path)
    canary_script, canary_log = _install_fake_anda(tmp_path, "happy")
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    store = _seed(tmp_path / "tasks.json", rows=[_row("gb:sbx5-gate")])
    token = _shaped_zai_key()  # runtime-built, never a literal
    extra = {
        "OMEGA_PRIME_TASKS_PATH": str(store),
        "OMEGA_PRIME_TASK_ID": "gb:sbx5-gate",
        "ZAI_API_KEY": token,
    }
    env = _backend_env(tmp_path, omp, canary_script, roots, **extra)
    env.pop("ANTHROPIC_API_KEY", None)  # no other allowlisted plan
    assert "OMEGA_PRIME_OMP_ALLOW_ZAI" not in env
    state = Path(env["OMEGA_PRIME_STATE_DIR"])
    _seed_turn(state, "plan", _SPEC_TEXT)
    monkeypatch.chdir(work)

    # Layer 1: the routine refuses through the probe short-circuit, raising
    # nothing and writing nothing — the shipped layering (the routine call
    # itself never reaches run()'s raise_block; see the SUMMARY drift note).
    plan = execute_ultrathink(env)
    assert plan["engine"]["used"] is False
    assert plan["engine"]["reason"] == "plan_not_allowed"
    data = _read_tasks(store)
    assert data["waiting_on_ove"] == []
    assert data["tasks"][0]["state"] == "working"

    # Layer 2: the exact engine call the routine wraps refuses pre-spawn and
    # raises exactly one waiting_on_ove item in the same returning call.
    result = run(
        "Carry out this ultrathink spec.\n\n" + _SPEC_TEXT,
        env=env,
        system_prompt="Ultrathink turn. Stay inside the spec.",
        stage="plan",
        tasks_path=store,
        task_id="gb:sbx5-gate",
    )
    assert result.ok is False
    assert result.reason == "plan_not_allowed"
    assert result.started is False
    data = _read_tasks(store)
    items = data["waiting_on_ove"]
    assert [item["id"] for item in items] == ["gb:sbx5-gate:plan_not_allowed"]
    assert items[0]["blocks"] == "engine plan gate (plan_not_allowed)"
    assert "OMEGA_PRIME_OMP_ALLOW_ZAI" in items[0]["ask"]
    assert data["tasks"][0] == {"id": "gb:sbx5-gate", "state": "blocked"}

    # No wrapper spawn happened: the canary left no log.
    assert not canary_log.exists()
    assert _wrapper_log(canary_log) == []
    assert _wrapper_tools(canary_log) == []

    # The runtime-built token never reached any durable artifact: the store,
    # every events.jsonl under the state jobs dir, and the herdr log.
    assert token not in store.read_text(encoding="utf-8")
    jobs_dir = state / "jobs"
    scanned: list[Path] = (
        list(jobs_dir.rglob("events.jsonl")) if jobs_dir.is_dir() else []
    )
    herdr_log = tmp_path / "bin" / "herdr-log.jsonl"
    if herdr_log.is_file():
        scanned.append(herdr_log)
    hits = [str(p) for p in scanned if token in p.read_text(encoding="utf-8")]
    assert hits == [], "secret-shaped value leaked into durable artifacts"


def test_opt_in_real_run_drives_the_real_wrapper_on_the_host(
    tmp_path: Path, monkeypatch
) -> None:
    if os.environ.get(_REAL_MARKER) != "1":
        pytest.skip(
            "host-only: set OMEGA_PRIME_SANDBOX_E2E_REAL=1 to run the real "
            "omp-sandbox-anda binary"
        )
    resolver: dict[str, str] = {"PATH": os.environ.get("PATH", "")}
    override = os.environ.get("OMEGA_PRIME_OMP_SANDBOX_ANDA")
    if override:
        resolver["OMEGA_PRIME_OMP_SANDBOX_ANDA"] = override
    if sandbox_omp.anda_binary(resolver) is None:
        pytest.skip(
            "host-only: no real wrapper found via OMEGA_PRIME_OMP_SANDBOX_ANDA or PATH"
        )
    roots = tmp_path / "roots"
    work = _git_worktree(roots)
    store = _seed(
        tmp_path / "tasks.json",
        rows=[_row("gb:sbx5-plan"), _row("gb:sbx5-exec")],
    )
    state = tmp_path / "state"
    base = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path / "home"),
        "OMEGA_PRIME_STATE_DIR": str(state),
        "OMEGA_PRIME_OMP_BACKEND": "omp-sandbox",
        "OMP_SANDBOX_WORKSPACE_ROOTS": str(roots),
        "OMEGA_PRIME_OMP_ALLOW_ZAI": "1",
        "OMEGA_PRIME_TASKS_PATH": str(store),
    }
    monkeypatch.chdir(work)
    for stage, task_id, model in (
        ("plan", "gb:sbx5-plan", "glm-5.3"),
        ("execute", "gb:sbx5-exec", "glm-5.3-flash"),
    ):
        _seed_turn(state, stage, _SPEC_TEXT)
        env = dict(base, OMEGA_PRIME_TASK_ID=task_id)
        plan = execute_ultrathink(env)
        assert plan["found"] is True
        engine = plan["engine"]
        assert engine["used"] is True, engine
        assert engine["plan"] == "zai"
        assert engine["stage"] == stage
        assert engine["model"] == model
        assert "failed" not in plan
        session = json.loads(
            (state / "jobs" / engine["job_id"] / "session.json").read_text(
                encoding="utf-8"
            )
        )
        assert session["sandbox"] == "omp-sandbox"
        assert session["model"] == model
        assert session["stage"] == stage
        assert session["ok"] is True
        subjects = _git("log", "--format=%s", cwd=work).splitlines()
        assert len(subjects) > 1
    data = _read_tasks(store)
    for task_id in ("gb:sbx5-plan", "gb:sbx5-exec"):
        row = next(r for r in data["tasks"] if r["id"] == task_id)
        assert row["state"] == "done"
        export = row["receipt"]["export"]
        assert export["commits"] >= 1
        assert Path(export["bundle"]).is_file()
