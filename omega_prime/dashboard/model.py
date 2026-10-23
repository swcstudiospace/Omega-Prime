# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Normalized dashboard task model.

Item kind registry (keys optional unless marked req; unknown keys ignored):
- herdr_pane: pane_id(req), workspace, name, agent_status, cwd, terminal_title, session_path
- omp_session: session_path(req), session_id, cwd, title, model, thinking_level, started_at, last_ts, stop_reason, usage{input,output}, progress{done,total}
- omp_proc: pid(req), elapsed_s, elapsed_measured_at, cwd, mode, role (agent|helper|service), label{task,task_title,agent,phase,plan,stage,run_id,model,worker}
- wiring: phase(req), branch, head_sha, stage, last_log_ts, progress, worktree
- sandbox_e2e: run_id(req), last_ts, path, stage
- sandbox_receipt: name(req), last_ts, path
- sandbox_container: name(req), state, status, created
- omega_job: job_id(req), model, plan, provider, sandbox, ok, stage, final_text_line, started_at, last_activity_at
- github_pr: repo(req), number(req), title, branch, draft, mergeable, ci, greptile, merge_rule, updated_at, url
- fleet_job: job_id(req), state, worker, age_s, age_measured_at
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from omega_prime.dashboard.labels import (
    known_task_titles,
    session_title,
    work_description,
    work_title,
)
from omega_prime.dashboard.redact import redact_value


def _safe(value: Any) -> Any:
    return redact_value(value)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _anchor(stamp: Any) -> datetime | None:
    if not isinstance(stamp, str):
        return None
    try:
        value = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value


def _dict(obj: Any, names: tuple[str, ...]) -> dict[str, Any]:
    return _safe({name: getattr(obj, name) for name in names})


def _wiring(item: Mapping[str, Any]) -> dict[str, str | None]:
    return {
        "phase": item.get("phase") if isinstance(item.get("phase"), str) else None,
        "branch": item.get("branch") if isinstance(item.get("branch"), str) else None,
        "head_sha": item.get("head_sha")
        if isinstance(item.get("head_sha"), str)
        else None,
        "stage": item.get("stage") if isinstance(item.get("stage"), str) else None,
        "repo": item.get("repo") if isinstance(item.get("repo"), str) else None,
        "worktree": item.get("worktree")
        if isinstance(item.get("worktree"), str)
        else None,
    }


@dataclass
class Owner:
    kind: str
    ref: str
    label: str

    def to_dict(self) -> dict[str, Any]:
        return _dict(self, ("kind", "ref", "label"))


@dataclass
class Task:
    id: str
    title: str
    source: str
    owner: Owner
    state: str
    started_at: str | None = None
    last_activity_at: str | None = None
    progress: tuple[int, int] | None = None
    model: str | None = None
    detail: str | None = None
    links: tuple[dict[str, str], ...] = ()
    seq: int | None = None
    display_id: str | None = None
    timeline: list[dict[str, str]] = field(default_factory=list)
    wiring: dict[str, str | None] | None = None
    herdr_pane: dict[str, str] | None = None
    stale: dict[str, str | None] | None = None

    def to_dict(self) -> dict[str, Any]:
        return _safe(
            {
                "id": self.id,
                "title": self.title,
                "source": self.source,
                "owner": self.owner.to_dict(),
                "state": self.state,
                "started_at": self.started_at,
                "last_activity_at": self.last_activity_at,
                "progress": None
                if self.progress is None
                else {"done": self.progress[0], "total": self.progress[1]},
                "model": self.model,
                "detail": self.detail,
                "links": list(self.links),
                "seq": self.seq,
                "display_id": self.display_id,
                "timeline": self.timeline,
                "wiring": self.wiring,
                "herdr_pane": self.herdr_pane,
                "stale": self.stale,
            }
        )


@dataclass
class SourceResult:
    source: str
    status: str
    items: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    detail: str | None = None


@dataclass
class PrRow:
    id: str
    repo: str
    number: int
    title: str
    branch: str
    draft: bool
    mergeable: bool | None
    ci: str
    greptile: str
    merge_rule: str
    updated_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return _dict(self, tuple(self.__dataclass_fields__))


@dataclass
class WaitingItem:
    id: str
    ask: str
    since: str | None
    blocks: str
    seq: int | None = None
    display_id: str | None = None
    timeline: list[dict[str, str]] = field(default_factory=list)
    related_task_id: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _dict(self, tuple(self.__dataclass_fields__))


@dataclass
class FinishedItem:
    id: str
    title: str
    finished_at: str | None
    result: str
    receipt: str | None = None
    summary: str | None = None
    seq: int | None = None
    display_id: str | None = None
    timeline: list[dict[str, str]] = field(default_factory=list)
    wiring: dict[str, str | None] | None = None
    source: str | None = None
    owner: Owner | None = None
    state: str | None = None
    started_at: str | None = None
    last_activity_at: str | None = None
    progress: tuple[int, int] | None = None
    model: str | None = None
    detail: str | None = None
    links: tuple[dict[str, str], ...] = ()
    herdr_pane: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return _safe(
            {
                "id": self.id,
                "title": self.title,
                "finished_at": self.finished_at,
                "result": self.result,
                "receipt": self.receipt,
                "summary": self.summary,
                "seq": self.seq,
                "display_id": self.display_id,
                "timeline": self.timeline,
                "wiring": self.wiring,
                "source": self.source,
                "owner": self.owner.to_dict() if self.owner else None,
                "state": self.state,
                "started_at": self.started_at,
                "last_activity_at": self.last_activity_at,
                "progress": None
                if self.progress is None
                else {"done": self.progress[0], "total": self.progress[1]},
                "herdr_pane": self.herdr_pane,
                "model": self.model,
                "detail": self.detail,
                "links": list(self.links),
            }
        )


@dataclass
class Snapshot:
    active: list[Task] = field(default_factory=list)
    pr_gate: list[PrRow] = field(default_factory=list)
    waiting_on_ove: list[WaitingItem] = field(default_factory=list)
    finished: list[FinishedItem] = field(default_factory=list)
    idle_sessions: list[Task] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _safe(
            {
                "active": [x.to_dict() for x in self.active],
                "pr_gate": [x.to_dict() for x in self.pr_gate],
                "waiting_on_ove": [x.to_dict() for x in self.waiting_on_ove],
                "finished": [x.to_dict() for x in self.finished],
                "idle_sessions": [x.to_dict() for x in self.idle_sessions],
            }
        )

    @property
    def content_hash(self) -> str:
        payload = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def _items(sources: Mapping[str, SourceResult], name: str) -> list[dict[str, Any]]:
    result = sources.get(name)
    return [x for x in result.items if isinstance(x, dict)] if result else []


def _recent(stamp: Any, now: datetime, window: float) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (now - dt).total_seconds() / 3600
    except ValueError:
        return None


def normalize_tasks(
    sources: Mapping[str, SourceResult],
    *,
    now: datetime,
    quiet_minutes: float = 15.0,
    window_hours: float = 24.0,
) -> Snapshot:
    """Build a deterministic snapshot from collector records."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    active: list[Task] = []
    idle_sessions: list[Task] = []
    prs: list[PrRow] = []
    waiting: dict[str, WaitingItem] = {}
    finished: list[FinishedItem] = []
    sessions = _items(sources, "omp_session")
    pane_rows = _items(sources, "herdr_pane")
    # Daemons (auth broker, gateway, Herdr server) are not agent work. Helper
    # workers prove their run is alive but are never a task of their own.
    procs = [p for p in _items(sources, "omp_proc") if p.get("role") != "service"]
    task_source = sources.get("tasks_json")
    known_titles = (
        known_task_titles(
            [
                *task_source.extra.get("tasks", []),
                *task_source.extra.get("finished", []),
            ]
        )
        if task_source
        else {}
    )
    # Absence of a process only proves a session ended after a scan that
    # actually ran; a failed or timed-out scan says nothing either way.
    procs_known = bool(procs) or any(
        (scan := sources.get(name)) is not None and scan.status in {"ok", "empty"}
        for name in ("procs", "omp_procs", "omp_proc")
    )
    # Checkouts already shown by a live row. Finished sessions do not claim
    # their checkout, so a new process started there still appears.
    live_cwds: set[str] = set()

    def claim(cwd: Any) -> None:
        if isinstance(cwd, str):
            live_cwds.add(cwd)

    claimed: set[int] = set()

    def proc_detail(cwd: Any) -> list[str]:
        return [
            f"pid {p['pid']} ({p.get('mode') or 'unknown'})"
            for p in procs
            if cwd is not None and p.get("cwd") == cwd and "pid" in p
        ]

    def live(cwd: Any) -> bool:
        return cwd is not None and any(p.get("cwd") == cwd for p in procs)

    for pane in pane_rows:
        pid = pane.get("pane_id")
        if not isinstance(pid, str):
            continue
        sess = next(
            (
                (i, x)
                for i, x in enumerate(sessions)
                if x.get("session_path") == pane.get("session_path")
                and pane.get("session_path") is not None
            ),
            None,
        )
        s = sess[1] if sess else {}
        if sess:
            claimed.add(sess[0])
        status = pane.get("agent_status")
        state = status if status in {"idle", "working", "blocked"} else "unknown"
        stamp = s.get("last_ts")
        if (
            state == "working"
            and (_recent(stamp, now, window_hours) or 0) * 60 >= quiet_minutes
        ):
            state = "quiet"
        detail: list[str] = []
        usage = s.get("usage")
        if (
            isinstance(usage, dict)
            and isinstance(usage.get("input"), int)
            and isinstance(usage.get("output"), int)
        ):
            detail.append(f"tokens in/out {usage['input']}/{usage['output']}")
        detail.extend(proc_detail(pane.get("cwd")))
        task_identity = f"herdr:{pid}"
        task = Task(
            task_identity,
            str(pane.get("name") or pane.get("terminal_title") or pid),
            "herdr_pane",
            Owner("herdr-pane", pid, f"Herdr pane {pid}"),
            state,
            s.get("started_at") if isinstance(s.get("started_at"), str) else None,
            stamp if isinstance(stamp, str) else None,
            _progress(s.get("progress")),
            s.get("model") if isinstance(s.get("model"), str) else None,
            "; ".join(detail) or None,
        )
        live_agent = pane.get("has_live_agent")
        if live_agent is not False and not s.get("stop_reason"):
            claim(pane.get("cwd"))
        if (
            state in {"unknown", "quiet"}
            or bool(s.get("stop_reason"))
            or live_agent is False
        ):
            idle_sessions.append(task)
        else:
            active.append(task)
        if state == "blocked":
            key = f"wait:herdr:{pid}"
            task_identity = f"herdr:{pid}"
            waiting[key] = WaitingItem(
                key, "blocked pane", stamp, pid, related_task_id=task_identity
            )

    for i, s in enumerate(sessions):
        if i in claimed or not isinstance(s.get("session_path"), str):
            continue
        path = s["session_path"]
        stop = s.get("stop_reason")
        sid = s.get("session_id") or hashlib.sha1(path.encode()).hexdigest()[:10]
        title = s.get("title") or session_title(
            s.get("cwd"), session_id=str(sid), known=known_titles
        )
        if stop:
            finished.append(
                FinishedItem(
                    f"omp:{sid}",
                    str(title),
                    s.get("last_ts"),
                    "stopped"
                    if str(stop).lower() in {"stopped", "aborted", "cancelled"}
                    else "ok",
                )
            )
            continue
        age = _recent(s.get("last_ts"), now, window_hours)
        if age is None or age > window_hours:
            continue
        state = "working" if age * 60 < quiet_minutes else "quiet"
        if (
            procs_known
            and state == "working"
            and s.get("cwd") is not None
            and not any(p.get("cwd") == s.get("cwd") for p in procs)
        ):
            state = "stopped?"
        if (
            procs_known
            and state == "quiet"
            and isinstance(s.get("cwd"), str)
            and not live(s.get("cwd"))
        ):
            # A known cwd without a process check proves this old session ended.
            finished.append(
                FinishedItem(f"omp:{sid}", str(title), s.get("last_ts"), "stopped")
            )
            continue
        detail = []
        usage = s.get("usage")
        if isinstance(usage, dict):
            detail.append(
                f"tokens in/out {usage.get('input', 0)}/{usage.get('output', 0)}"
            )
        if not detail:
            # Never leave a session row without a description.
            where = s.get("cwd")
            detail.append(
                f"omp session {sid}"
                + (f" in {where}" if isinstance(where, str) else "")
            )
        claim(s.get("cwd"))
        active.append(
            Task(
                f"omp:{sid}",
                str(title),
                "omp_session",
                Owner("omp-session", path, str(title)),
                state,
                s.get("started_at"),
                s.get("last_ts"),
                _progress(s.get("progress")),
                s.get("model"),
                "; ".join(detail) or None,
            )
        )

    for item in _items(sources, "wiring"):
        phase = item.get("phase")
        if not isinstance(phase, str):
            continue
        stamp = item.get("last_log_ts")
        if item.get("stage") != "done":
            claim(item.get("worktree"))
        if item.get("stage") == "done":
            finished.append(
                FinishedItem(
                    f"wiring:{phase}",
                    f"Wiring phase {phase}",
                    stamp,
                    "ok",
                    wiring=_wiring(item),
                )
            )
            continue
        if item.get("stage") not in {"sol", "luna"}:
            continue
        age = _recent(stamp, now, window_hours)
        running = live(item.get("worktree"))
        recent = age is not None and age * 60 < quiet_minutes
        if not running and not recent:
            finished.append(
                FinishedItem(
                    f"wiring:{phase}",
                    f"Wiring phase {phase}",
                    stamp,
                    "stopped",
                    wiring=_wiring(item),
                )
            )
            continue
        state = "working" if running or recent else "quiet"
        details = [
            x for x in (item.get("branch"), item.get("head_sha")) if isinstance(x, str)
        ] + proc_detail(item.get("worktree"))
        links = (
            ({"label": "phase.log", "path_or_url": str(item["worktree"])},)
            if item.get("worktree")
            else ()
        )
        active.append(
            Task(
                f"wiring:{phase}",
                f"Wiring phase {phase}",
                "wiring",
                Owner("wiring", phase, f"Wiring phase {phase}"),
                state,
                None,
                stamp,
                _progress(item.get("progress")),
                None,
                "; ".join(details) or None,
                links,
                wiring=_wiring(item),
            )
        )

    for item in _items(sources, "sandbox_e2e"):
        rid = item.get("run_id")
        age = _recent(item.get("last_ts"), now, window_hours)
        if not isinstance(rid, str) or age is None or age > window_hours:
            continue
        task = Task(
            f"sandbox:e2e:{rid}",
            f"Sandbox E2E {rid}",
            "sandbox_e2e",
            Owner("sandbox", rid, "Sandbox"),
            "working" if age * 60 < quiet_minutes else "quiet",
            None,
            item.get("last_ts"),
        )
        (active if task.state == "working" else idle_sessions).append(task)
    for item in _items(sources, "sandbox_container"):
        name = item.get("name")
        if isinstance(name, str) and item.get("state") == "running":
            active.append(
                Task(
                    f"sandbox:container:{name}",
                    f"Sandbox {name}",
                    "sandbox_container",
                    Owner("sandbox", name, "Sandbox"),
                    "working",
                    None,
                    item.get("created"),
                    detail=item.get("status"),
                )
            )
    for item in _items(sources, "sandbox_receipt"):
        name = item.get("name")
        if isinstance(name, str):
            finished.append(
                FinishedItem(
                    f"sandbox:receipt:{name}",
                    name,
                    item.get("last_ts"),
                    "ok",
                    item.get("path"),
                )
            )

    for item in _items(sources, "omega_job"):
        jid = item.get("job_id")
        if not isinstance(jid, str):
            continue
        stamp = item.get("last_activity_at")
        if item.get("ok") is not None:
            finished.append(
                FinishedItem(
                    f"job:{jid}",
                    f"omega job {jid}",
                    stamp,
                    "ok" if item.get("ok") else "failed",
                    summary=(
                        item.get("final_text_line")
                        if isinstance(item.get("final_text_line"), str)
                        else None
                    ),
                )
            )
            continue
        age = _recent(stamp, now, window_hours)
        if age is None or age > window_hours:
            continue
        state = "working" if age * 60 < quiet_minutes else "quiet"
        job_bits = [item.get("final_text_line"), item.get("model"), item.get("plan")]
        job_detail = "; ".join(str(x) for x in job_bits if x)
        job_links = item.get("links")
        active.append(
            Task(
                f"job:{jid}",
                f"omega job {jid}",
                "omega_job",
                Owner("omega-job", jid, f"omega job {jid}"),
                state,
                item.get("started_at"),
                stamp,
                None,
                item.get("model"),
                job_detail or None,
                tuple(job_links) if isinstance(job_links, list) else (),
            )
        )

    for item in _items(sources, "fleet"):
        jid = item.get("job_id")
        if not isinstance(jid, str):
            continue
        state_text = str(item.get("state") or "").lower()
        age_s = item.get("age_s")
        measured = _anchor(item.get("age_measured_at"))
        updated = (
            ((measured or now) - timedelta(seconds=float(age_s))).isoformat()
            if isinstance(age_s, (int, float))
            else None
        )
        task_id = f"fleet:{jid}"
        title = f"fleet job {jid}"
        owner = Owner("fleet", jid, title)
        if "hitl" in state_text:
            active.append(
                Task(task_id, title, "fleet_job", owner, "blocked", updated, updated)
            )
        elif any(word in state_text for word in ("done", "complete", "succeed")):
            finished.append(FinishedItem(task_id, title, updated, "ok"))
        elif any(word in state_text for word in ("fail", "error", "cancel", "abort")):
            finished.append(FinishedItem(task_id, title, updated, "failed"))
        elif any(word in state_text for word in ("run", "active", "work", "progress")):
            active.append(
                Task(task_id, title, "fleet_job", owner, "working", updated, updated)
            )
        elif any(word in state_text for word in ("queue", "pend", "wait")):
            active.append(
                Task(task_id, title, "fleet_job", owner, "idle", updated, updated)
            )
        else:
            active.append(
                Task(task_id, title, "fleet_job", owner, "unknown", updated, updated)
            )

    # One row per run (or per agent pid when no run id). Helpers fold into
    # the agent row that shares their checkout; separate agents never merge,
    # even when they share a cwd.
    groups: dict[str, list[dict[str, Any]]] = {}
    group_cwd: dict[str, str] = {}
    cwd_primary: dict[str, str] = {}
    for item in procs:
        pid, cwd = item.get("pid"), item.get("cwd")
        if not isinstance(pid, int) or not isinstance(cwd, str) or cwd in live_cwds:
            continue
        if item.get("role") == "helper":
            continue
        maybe_label = item.get("label")
        raw_label: dict[str, Any] = maybe_label if isinstance(maybe_label, dict) else {}
        run_id = raw_label.get("run_id")
        key = f"run:{run_id}" if isinstance(run_id, str) else f"pid:{pid}"
        groups.setdefault(key, []).append(item)
        group_cwd[key] = cwd
        cwd_primary.setdefault(cwd, key)
    helpers_by_group: dict[str, list[int]] = {key: [] for key in groups}
    for item in procs:
        pid, cwd = item.get("pid"), item.get("cwd")
        if item.get("role") != "helper":
            continue
        if not isinstance(pid, int) or not isinstance(cwd, str) or cwd in live_cwds:
            continue
        primary = cwd_primary.get(cwd)
        if primary is not None:
            helpers_by_group[primary].append(pid)
    for key in sorted(groups, key=lambda k: (group_cwd[k], groups[k][0]["pid"])):
        agents = sorted(groups[key], key=lambda member: member["pid"])
        helpers = helpers_by_group[key]
        cwd = group_cwd[key]
        label: dict[str, Any] = {}
        for member in agents:
            for name, value in (member.get("label") or {}).items():
                if value is not None and label.get(name) is None:
                    label[name] = value
        first = agents[0]
        elapsed_s = first.get("elapsed_s")
        measured = _anchor(first.get("elapsed_measured_at"))
        stamp = (
            ((measured or now) - timedelta(seconds=float(elapsed_s))).isoformat()
            if isinstance(elapsed_s, (int, float))
            else None
        )
        title = work_title(label, cwd, known=known_titles)
        run_id = label.get("run_id")
        who = label.get("agent") or label.get("stage") or "agent"
        active.append(
            Task(
                f"omp-run:{run_id}"
                if isinstance(run_id, str)
                else f"proc:{first['pid']}",
                title,
                "omp_proc",
                Owner("omp-proc", str(first["pid"]), f"omp {who}"),
                "working",
                stamp,
                stamp,
                model=label.get("model")
                if isinstance(label.get("model"), str)
                else None,
                detail=work_description(
                    label,
                    cwd,
                    pids=[m["pid"] for m in agents],
                    helpers=helpers,
                    mode=first.get("mode")
                    if isinstance(first.get("mode"), str)
                    else None,
                ),
            )
        )
    for item in _items(sources, "github_pr"):
        repo, number = item.get("repo"), item.get("number")
        if not isinstance(repo, str) or not isinstance(number, int):
            continue
        draft = bool(item.get("draft", False))
        ci = str(item.get("ci", "unknown"))
        gt = str(item.get("greptile", "not reviewed"))
        row = PrRow(
            f"gh:{repo}#{number}",
            repo,
            number,
            str(item.get("title", "")),
            str(item.get("branch", "")),
            draft,
            item.get("mergeable") if isinstance(item.get("mergeable"), bool) else None,
            ci,
            gt,
            "Greptile 5/5 + green CI, merge commit"
            if repo.lower() in {"swcstudiospace/omega-prime", "swcstudiospace/omes-bot"}
            else "needs Ove's yes",
            item.get("updated_at"),
        )
        prs.append(row)
        if gt == "5/5" and ci == "pass" and not draft:
            key = f"wait:gh:{repo}#{number}"
            waiting[key] = WaitingItem(
                key,
                "ready for your merge yes",
                row.updated_at,
                f"{repo}#{number} merge",
            )

    if task_source:
        for t in task_source.extra.get("tasks", []):
            if not isinstance(t, dict) or not isinstance(t.get("id"), str):
                continue
            task_id = t["id"]
            state_value = t.get("state")
            if isinstance(state_value, str):
                state = {
                    "queued": "idle",
                    "working": "working",
                    "blocked": "blocked",
                    "waiting_on_ove": "waiting_on_ove",
                    "done": "done",
                    "failed": "failed",
                }.get(state_value, "unknown")
            else:
                state = "unknown"
            owner_data = t.get("owner")
            if not isinstance(owner_data, dict):
                owner_data = {}
            ref = str(owner_data.get("ref", task_id))
            kind = str(owner_data.get("kind", "tasks"))
            title = str(t.get("title", task_id))
            links_value = t.get("links", [])
            links = tuple(links_value) if isinstance(links_value, list) else ()
            identity = task_id if ":" in task_id else f"tasks:{task_id}"
            owner = Owner(kind, ref, str(owner_data.get("label", ref)))
            activity = t.get("last_activity_at")
            progress = _progress(t.get("progress"))
            if state in {"done", "failed"}:
                finished.append(
                    FinishedItem(
                        identity,
                        title,
                        activity,
                        "ok" if state == "done" else "failed",
                        summary=t.get("summary")
                        if isinstance(t.get("summary"), str)
                        else None,
                        source="tasks_json",
                        owner=owner,
                        state=state,
                        started_at=t.get("started_at")
                        if isinstance(t.get("started_at"), str)
                        else None,
                        last_activity_at=activity
                        if isinstance(activity, str)
                        else None,
                        progress=progress,
                        model=t.get("model")
                        if isinstance(t.get("model"), str)
                        else None,
                        detail=t.get("note")
                        if isinstance(t.get("note"), str)
                        else None,
                        links=links,
                        wiring=_wiring(t.get("wiring", {}))
                        if isinstance(t.get("wiring"), dict)
                        else None,
                        herdr_pane=t.get("herdr_pane")
                        if isinstance(t.get("herdr_pane"), dict)
                        else None,
                    )
                )
                continue
            active.append(
                Task(
                    identity,
                    title,
                    "tasks_json",
                    owner,
                    state,
                    t.get("started_at"),
                    activity,
                    progress,
                    t.get("model") if isinstance(t.get("model"), str) else None,
                    t.get("note") if isinstance(t.get("note"), str) else None,
                    links,
                    wiring=_wiring(t.get("wiring", {}))
                    if isinstance(t.get("wiring"), dict)
                    else None,
                    herdr_pane=t.get("herdr_pane")
                    if isinstance(t.get("herdr_pane"), dict)
                    else None,
                )
            )
        for raw in task_source.extra.get("waiting_on_ove", []):
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                ident = raw["id"] if ":" in raw["id"] else f"tasks:{raw['id']}"
                waiting[ident] = WaitingItem(
                    ident,
                    str(raw.get("ask", "")),
                    raw.get("since"),
                    str(raw.get("blocks", "")),
                    source="tasks_json",
                )
        for raw in task_source.extra.get("finished", []):
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                ident = raw["id"] if ":" in raw["id"] else f"tasks:{raw['id']}"
                finished.append(
                    FinishedItem(
                        ident,
                        str(raw.get("title", "")),
                        raw.get("finished_at"),
                        str(raw.get("result", "ok")),
                        raw.get("receipt"),
                        source="tasks_json",
                        herdr_pane=raw.get("herdr_pane")
                        if isinstance(raw.get("herdr_pane"), dict)
                        else None,
                    )
                )
    fleet = sources.get("fleet")
    if (
        fleet
        and isinstance(fleet.extra.get("hitl_count"), int)
        and fleet.extra["hitl_count"] > 0
    ):
        n = fleet.extra["hitl_count"]
        waiting["wait:fleet:hitl"] = WaitingItem(
            "wait:fleet:hitl",
            f"{n} fleet job(s) waiting for a HITL answer",
            None,
            "fleet",
        )

    ranks = {
        "blocked": 0,
        "working": 1,
        "quiet": 2,
        "stopped?": 3,
        "waiting_on_ove": 4,
        "idle": 5,
        "unknown": 6,
        "done": 7,
        "failed": 8,
    }
    active.sort(key=lambda t: (ranks.get(t.state, 6), t.id))
    prs.sort(
        key=lambda p: (p.updated_at is not None, p.updated_at or "", p.id), reverse=True
    )
    waiting_list = list(waiting.values())
    waiting_list.sort(
        key=lambda x: (x.since is not None, x.since or "", x.id), reverse=True
    )
    # Newest dated results first; undated ones last so they never crowd out
    # recent completions before the cap.
    dated = sorted(
        (x for x in finished if x.finished_at),
        key=lambda x: (str(x.finished_at), x.id),
        reverse=True,
    )
    undated = sorted((x for x in finished if not x.finished_at), key=lambda x: x.id)
    finished = (dated + undated)[:50]
    idle_sessions.sort(key=lambda t: t.id)
    return Snapshot(
        active=active,
        pr_gate=prs,
        waiting_on_ove=waiting_list,
        finished=finished,
        idle_sessions=idle_sessions,
    )


def _progress(value: Any) -> tuple[int, int] | None:
    if (
        isinstance(value, dict)
        and isinstance(value.get("done"), int)
        and isinstance(value.get("total"), int)
    ):
        return value["done"], value["total"]
    return None
