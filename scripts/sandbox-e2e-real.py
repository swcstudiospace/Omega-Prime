#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Host-only live check: the ultrathink routine on the REAL omp-sandbox wrapper.

Not a test: it uses the real wrapper, a real model login and the network,
so it lives outside ``omega_prime/tests`` (which stays fakes-only) and runs
only when a person starts it on a configured host::

    python3 scripts/sandbox-e2e-real.py

It builds a throwaway git project under a temp allowlisted root, then
drives the routine (``execute_ultrathink``) for the plan stage and the
execute stage, exactly as the hermetic ``test_sandbox_e2e.py`` does with
its fakes. The host environment is used as-is, so the wrapper finds its
Node (``OMP_SANDBOX_NODE`` or ``$HOME/tools/node24``), its logins and its
binary (``OMEGA_PRIME_OMP_SANDBOX_ANDA`` or PATH); ``OMP_SANDBOX_ROOT``
defaults to this checkout's ``omp-sandbox`` as an absolute path. Only the
state dir, tasks store, backend and workspace root are overridden. No
environment value is ever printed; the output names stages, job ids and
pass/fail only. Exit 0 when both stages ran and exported, 1 otherwise, 2
when the host is not set up (no wrapper found).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from omega_prime.engine import sandbox_omp  # noqa: E402
from omega_prime.engine.plan_router import model_id  # noqa: E402
from omega_prime.routines.ultrathink_turn import execute_ultrathink  # noqa: E402

SPEC = "Wire the demo greeting for the throwaway project."
STAGES = (("plan", "gb:sbx5-plan"), ("execute", "gb:sbx5-exec"))


def _git(cwd: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *argv], check=True, capture_output=True, text=True
    ).stdout.strip()


def _project(roots: Path) -> Path:
    work = roots / "work"
    work.mkdir(parents=True)
    _git(work, "init", "-q", "-b", "main")
    (work / "README.md").write_text("throwaway e2e project\n", encoding="utf-8")
    _git(work, "add", "README.md")
    _git(
        work,
        "-c",
        "user.name=e2e",
        "-c",
        "user.email=e2e@example.invalid",
        "commit",
        "-q",
        "-m",
        "seed",
    )
    return work


def main() -> int:
    if sandbox_omp.anda_binary(os.environ) is None:
        print(
            "not set up: no omp-sandbox-anda via OMEGA_PRIME_OMP_SANDBOX_ANDA or PATH"
        )
        return 2
    with tempfile.TemporaryDirectory(prefix="omp-sbx-e2e-real-") as tmp:
        base = Path(tmp)
        roots = base / "roots"
        work = _project(roots)
        state = base / "state"
        store = base / "tasks.json"
        store.write_text(
            json.dumps({"tasks": [{"id": t, "state": "working"} for _, t in STAGES]})
            + "\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env.setdefault(sandbox_omp.ROOT_ENV, str((REPO / "omp-sandbox").resolve()))
        env.update(
            {
                "OMEGA_PRIME_STATE_DIR": str(state),
                "OMEGA_PRIME_OMP_BACKEND": "omp-sandbox",
                "OMP_SANDBOX_WORKSPACE_ROOTS": str(roots),
                "OMEGA_PRIME_TASKS_PATH": str(store),
            }
        )
        os.chdir(work)  # the routine runs on the process cwd
        home = state / "ultrathink"
        home.mkdir(parents=True)
        spec = home / "spec.md"
        spec.write_text(SPEC + "\n", encoding="utf-8")
        ok = True
        for stage, task_id in STAGES:
            (home / "last-plan.json").write_text(
                json.dumps({"specPath": str(spec), "stage": stage}) + "\n",
                encoding="utf-8",
            )
            plan = execute_ultrathink(dict(env, OMEGA_PRIME_TASK_ID=task_id))
            engine = plan.get("engine") or {}
            # The live Z.ai path is what this proves: a stage that ran on any
            # other plan (ChatGPT allowed with quota left) or model is a FAIL.
            want_model = model_id("zai", env, stage)
            on_zai = engine.get("plan") == "zai" and str(engine.get("model") or "") in (
                want_model,
                f"zai/{want_model}",
            )
            ran = bool(engine.get("used")) and not plan.get("failed") and on_zai
            print(
                f"{stage}: {'ok' if ran else 'FAILED'} job={engine.get('job_id')} "
                f"plan={engine.get('plan')} model={engine.get('model')} "
                f"(want zai/{want_model}) reason={engine.get('reason')}"
            )
            ok = ok and ran
        rows = {
            row["id"]: row
            for row in json.loads(store.read_text(encoding="utf-8"))["tasks"]
        }
        for _, task_id in STAGES:
            row = rows.get(task_id) or {}
            export = (row.get("receipt") or {}).get("export") or {}
            commits = export.get("commits") or []
            exported = row.get("state") == "done" and len(commits) >= 1
            # Visibility is part of the proof: a pane failure never fails the
            # backend, so a run nobody could watch (no herdr pane and no tmux
            # fallback recorded on the row) is a FAIL here.
            pane = row.get("herdr_pane")
            visible = isinstance(pane, str) and bool(pane.strip())
            print(
                f"{task_id}: state={row.get('state')} exported_commits={len(commits)}"
                f" pane={'yes' if visible else 'MISSING'}"
            )
            ok = ok and exported and visible
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
