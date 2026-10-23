# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Human labels for agent work found only as processes or untitled sessions.

Ove reads the Command Center by title, and every row should read
``000NN - Title`` with a description (see ``frontend/src/lib/taskCard.ts``).
An omp run launched by agentOS has no session title (``--no-title``) and its
session lives outside ``~/.omp/agent/sessions``, so the only evidence the
dashboard has is the process itself. This module turns that evidence into a
label.

Parsing keeps a strict allowlist: a handful of short, regex-captured fields
(task number and title, agent role, phase, plan, stage, run id, model, mode,
helper marker). The prompt text itself is never stored or returned.
Composition is pure, so the model stays deterministic.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import PurePath
from typing import Any

from omega_prime.dashboard.redact import redact_value

# Ove's task numbers: five digits with a 000 lead (00046, 00051), never part
# of a longer digit run such as an epoch or a pid.
_TASK_NUMBER = re.compile(r"(?<![0-9])(000[0-9]{2})(?![0-9])")
# The first line of every GSD stage prompt names the role and the work:
#   You are the GSD executor (...) for Omega Prime milestone 00046 "Event
#   Stream", phase 105, plan 105-04.
_HEADER = re.compile(r"You are the GSD ([A-Za-z][A-Za-z-]{1,30})[^\n]{0,400}")
_HEADER_TASK = re.compile(
    r"\b(?:task|milestone)\s+(000[0-9]{2})(?![0-9])"
    r"(?:\s+[\"\u201c]([^\"\u201d\n]{1,80})[\"\u201d])?"
)
_HEADER_PHASE = re.compile(r"\bphase\s+([0-9]{2,4})(?![0-9-])")
_HEADER_PLAN = re.compile(r"\bplan\s+([0-9]{2,4}-[0-9]{2})(?![0-9])")
# agentOS run ids: omp-<stage>-<base36 time>-<hex>.
_RUN_ID = re.compile(r"\b(omp-(plan|execute)-[a-z0-9]{4,16}-[0-9a-f]{4,12})\b")
_WORKER = re.compile(r"^__omp_worker_([a-z0-9_]{1,40})$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._/:@+-]{1,80}$")
# Only the head of each argument is searched; prompts can be very large.
_SCAN = 4096
_TITLE_MAX = 80

_ACTIVITY = {
    "executor": "executing",
    "planner": "planning",
    "plan-checker": "checking plans",
    "verifier": "verifying",
    "phase-researcher": "researching",
    "researcher": "researching",
    "debugger": "debugging",
    "code-fixer": "fixing review findings",
    "codebase-mapper": "mapping the codebase",
}
_STAGE_ACTIVITY = {"execute": "executing", "plan": "planning"}

LABEL_FIELDS = (
    "task",
    "task_title",
    "agent",
    "phase",
    "plan",
    "stage",
    "run_id",
    "model",
    "worker",
)


def task_number(*texts: Any) -> str | None:
    """The first ``000NN`` task number in any of ``texts`` (paths, names)."""
    for text in texts:
        if isinstance(text, str):
            found = _TASK_NUMBER.search(text)
            if found:
                return found.group(1)
    return None


def _clean(value: str | None, limit: int = _TITLE_MAX) -> str | None:
    if value is None:
        return None
    flat = " ".join(value.split())[:limit].strip()
    return str(redact_value(flat)) if flat else None


def _option(args: Sequence[str], name: str) -> str | None:
    for index, arg in enumerate(args):
        if arg == name and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith(name + "="):
            return arg[len(name) + 1 :]
    return None


def parse_omp_argv(args: Sequence[str]) -> dict[str, str | None]:
    """Allowlisted work identity from one omp process' argv.

    Returns every key in ``LABEL_FIELDS`` (``None`` when absent). Nothing
    outside the captured groups leaves this function.
    """
    label: dict[str, str | None] = dict.fromkeys(LABEL_FIELDS)
    for arg in args[:3]:
        worker = _WORKER.match(arg)
        if worker:
            label["worker"] = worker.group(1)
    model = _option(args, "--model")
    if model and _SAFE_TOKEN.match(model):
        label["model"] = model
    session_dir = _option(args, "--session-dir")
    if session_dir:
        run = _RUN_ID.search(session_dir)
        if run:
            label["run_id"], label["stage"] = run.group(1), run.group(2)
    for arg in args:
        header = _HEADER.search(arg[:_SCAN])
        if header is None:
            continue
        line = header.group(0)
        label["agent"] = header.group(1).lower()
        task = _HEADER_TASK.search(line)
        if task:
            label["task"] = task.group(1)
            label["task_title"] = _clean(task.group(2))
        phase = _HEADER_PHASE.search(line)
        if phase:
            label["phase"] = phase.group(1)
        plan = _HEADER_PLAN.search(line)
        if plan:
            label["plan"] = plan.group(1)
        break
    return label


def known_task_titles(rows: Iterable[Any]) -> dict[str, str]:
    """``{"00046": "Event Stream"}`` from recorded ``000NN - Title`` rows.

    The first recorded title for a number wins, so a later row reusing the
    number cannot relabel live work.
    """
    titles: dict[str, str] = {}
    for row in rows:
        title = row.get("title") if isinstance(row, Mapping) else None
        if not isinstance(title, str):
            continue
        match = re.match(r"\s*(000[0-9]{2})\s+-\s+(.+)", title)
        if match:
            text = _clean(match.group(2))
            if text:
                titles.setdefault(match.group(1), text)
    return titles


def _activity(label: Mapping[str, Any]) -> str:
    agent = label.get("agent")
    if isinstance(agent, str) and agent in _ACTIVITY:
        verb = _ACTIVITY[agent]
    else:
        stage = label.get("stage")
        verb = _STAGE_ACTIVITY.get(stage, "working") if isinstance(stage, str) else ""
        if not verb:
            verb = "working"
    plan, phase = label.get("plan"), label.get("phase")
    if isinstance(plan, str):
        return f"{verb} plan {plan}"
    if isinstance(phase, str):
        return f"{verb} phase {phase}"
    return verb


def _place(cwd: str | None) -> str | None:
    if not isinstance(cwd, str) or not cwd:
        return None
    return PurePath(cwd).name or cwd


def work_title(
    label: Mapping[str, Any],
    cwd: str | None,
    *,
    known: Mapping[str, str] | None = None,
) -> str:
    """``000NN - <task title> · <activity>`` or a plain-language fallback."""
    number = label.get("task") or task_number(cwd)
    name = label.get("task_title")
    if not name and isinstance(number, str) and known:
        name = known.get(number)
    activity = _activity(label)
    place = _place(cwd)
    if isinstance(number, str):
        if isinstance(name, str) and name:
            return f"{number} - {name} · {activity}"
        stage = label.get("stage") or "agent"
        where = f" in {place}" if place else ""
        return f"{number} - omp {stage} session{where} · {activity}"
    stage = label.get("stage") or "agent"
    purpose = label.get("agent") or place or "unlabelled work"
    return f"omp {stage} session ({purpose})"


def work_description(
    label: Mapping[str, Any],
    cwd: str | None,
    *,
    pids: Sequence[int] = (),
    helpers: Sequence[int] = (),
    mode: str | None = None,
) -> str:
    """One sentence of evidence: who is doing the work, where, and which pids."""
    agent = label.get("agent")
    who = f"GSD {agent}" if isinstance(agent, str) else "omp agent"
    model = label.get("model")
    parts = [f"{who} on {model}" if isinstance(model, str) else who]
    if isinstance(cwd, str) and cwd:
        parts.append(f"in {cwd}")
    run_id = label.get("run_id")
    if isinstance(run_id, str):
        parts.append(f"agentOS run {run_id}")
    evidence = ", ".join(str(pid) for pid in pids)
    if evidence:
        noun = "pid" if len(pids) == 1 else "pids"
        parts.append(f"{noun} {evidence}" + (f" ({mode} mode)" if mode else ""))
    if helpers:
        parts.append("helper workers " + ", ".join(str(pid) for pid in helpers))
    return "; ".join(parts)


def session_title(
    cwd: Any, *, session_id: str | None = None, known: Mapping[str, str] | None = None
) -> str:
    """Title for an omp session that never recorded a title event.

    Uses the checkout's task number (and its recorded title) when there is
    one; never the session file name.
    """
    place = _place(cwd if isinstance(cwd, str) else None)
    number = task_number(cwd)
    if number:
        name = known.get(number) if known else None
        if name:
            return f"{number} - {name} · omp session"
        return f"{number} - omp session in {place}"
    if place:
        return f"omp session in {place}"
    short = f" {session_id[:12]}" if session_id else ""
    return f"omp session{short} (no title recorded)"
