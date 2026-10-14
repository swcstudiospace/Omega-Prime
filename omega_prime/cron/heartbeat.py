# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Session heartbeat — a cron job kind that re-enters a named session.

Behavior port of Prime Agent's heartbeat (``rlm-heartbeat`` skill +
``pa-core/src/cron/``) @ ``967eb13f`` (MIT, PrimeIntellect). A heartbeat is a
recurring job whose prompt re-enters a named session on schedule, riding the
existing :class:`JobStore` scheduler (LOOP-02). Jobs fire serialized (the v4
rule): one tick runs each due heartbeat once, in order.

The heartbeat prompt carries the target session name so the runner re-enters
that session rather than starting a fresh conversation.
"""

from __future__ import annotations

import importlib.util
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega_prime.cron.scheduler import JobStore

#: Job kind marker stored on heartbeat jobs.
HEARTBEAT_KIND = "session_heartbeat"


def schedule_heartbeat(
    store: JobStore,
    session: str,
    prompt: str,
    *,
    interval_seconds: int | float,
    due_at: int | float,
) -> str:
    """Schedule a recurring heartbeat that re-enters ``session`` with ``prompt``.

    Heartbeats are interval jobs (a heartbeat with no interval is a single
    reminder, not a heartbeat). Returns the job id.
    """
    if not isinstance(session, str) or not session.strip():
        raise ValueError("session must be a non-empty string")
    if isinstance(interval_seconds, bool) or interval_seconds <= 0:
        raise ValueError("a heartbeat needs a positive interval_seconds")
    job_id = store.schedule(prompt, due_at, interval_seconds=interval_seconds)
    for job in store.jobs:
        if isinstance(job, dict) and job.get("id") == job_id:
            job["kind"] = HEARTBEAT_KIND
            job["session"] = session
            break
    store._save()
    return job_id


def list_heartbeats(store: JobStore) -> list[dict]:
    """All heartbeat jobs, complete or not."""
    return [
        job
        for job in store.jobs
        if isinstance(job, dict) and job.get("kind") == HEARTBEAT_KIND
    ]


def clear_heartbeat(store: JobStore, job_id: str) -> dict:
    """Remove one heartbeat by id. Unknown id is a structured result."""
    for index, job in enumerate(store.jobs):
        if (
            isinstance(job, dict)
            and job.get("id") == job_id
            and job.get("kind") == HEARTBEAT_KIND
        ):
            store.jobs.pop(index)
            store._save()
            return {"cleared": job_id}
    return {"error": f"no heartbeat job: {job_id!r}"}


def heartbeat_runner(session_runner: Any) -> Any:
    """Build a ``runner(prompt)`` that re-enters the job's named session.

    ``session_runner(session, prompt)`` runs one turn in the named session
    and returns its final response. The JobStore tick passes only the prompt,
    so the session is bound through the job dict at tick time by
    :func:`tick_heartbeats`.
    """

    def runner(session: str, prompt: str) -> Any:
        return session_runner(session, prompt)

    return runner


def _tick_roe_now(now: Any, override: Any) -> Any:
    """Clock for the RoE renewal digest: explicit override, else tick stamp."""
    if override is not None:
        return override
    if isinstance(now, bool):
        return None
    if isinstance(now, (int, float)):
        try:
            return datetime.fromtimestamp(now, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def tick_heartbeats(
    store: JobStore,
    now: int | float,
    session_runner: Any,
    *,
    roe_now: Any = None,
) -> list[dict]:
    """Run each due heartbeat, re-entering its named session.

    Like :meth:`JobStore.tick` but the runner receives the job's ``session``
    so the turn re-enters the named session. Only heartbeat jobs are considered.

    G11 additive digest: when a heartbeat job carries a RoE artifact in its
    context (``job["context"]["roe_artifact"]``, ``job["roe_artifact"]``, or
    ``job["roe_artifact_path"]``), newly crossed renewal thresholds append
    ``roe_renewal`` entries to ``job["roe_renewal_digest"]`` via
    :func:`heartbeat_roe_renewals`. Jobs without a RoE artifact are
    byte-for-byte unchanged, as are all pre-existing heartbeat kinds.
    """
    roe_clock = _tick_roe_now(now, roe_now)
    ran: list[dict] = []
    for job in store.jobs:
        if job.get("kind") != HEARTBEAT_KIND or job.get("completed"):
            continue
        if job["due_at"] > now:
            continue
        history = job.setdefault("history", [])
        if job.get("claimed_at") == now:
            continue
        if any(isinstance(e, dict) and e.get("ran_at") == now for e in history):
            continue
        job["claimed_at"] = now
        store._save()
        job["last_result"] = session_runner(job.get("session", ""), job["prompt"])
        job["last_ran_at"] = now
        history.append({"ran_at": now, "result": job["last_result"]})
        del history[:-20]
        job["claimed_at"] = None
        job["due_at"] = now + job["interval_seconds"]
        try:
            renewals = heartbeat_roe_renewals(job, now=roe_clock)
        except Exception:
            renewals = []
        if renewals:
            digest = job.setdefault("roe_renewal_digest", [])
            if isinstance(digest, list):
                digest.extend(renewals)
        ran.append(job)
    if ran:
        store._save()
    return ran


#: Heartbeat digest kind for RoE renewal reminders (G11). Emitted as
#: digest entries on heartbeat jobs whose context carries a RoE artifact;
#: never stored as a job ``kind`` — pre-existing kinds are unchanged.
ROE_RENEWAL_KIND = "roe_renewal"


def heartbeat_roe_renewals(job: Any, *, now: Any = None) -> list[dict]:
    """Digest entries for a heartbeat job's RoE artifact context (G11).

    Reads the RoE artifact from ``job["context"]["roe_artifact"]``,
    ``job["roe_artifact"]``, or ``job["roe_artifact_path"]`` (inline
    document dict or artifact path — whatever :func:`renewal_reminders`
    accepts). Each newly crossed renewal threshold becomes one
    ``{"kind": ROE_RENEWAL_KIND, "threshold", "detail", ...}`` entry with
    the threshold in the detail string. Fail-closed: a missing artifact,
    a malformed window, or any error returns ``[]`` and never raises.
    """
    try:
        if not isinstance(job, dict):
            return []
        artifact = None
        context = job.get("context")
        if isinstance(context, dict):
            artifact = context.get("roe_artifact", context.get("roe_artifact_path"))
        if artifact is None:
            artifact = job.get("roe_artifact", job.get("roe_artifact_path"))
        if artifact is None:
            return []
        from omega_prime.grokbot.roe_lifecycle import renewal_reminders

        reminders = renewal_reminders(artifact, now=now)
        entries: list[dict] = []
        for reminder in reminders:
            if not isinstance(reminder, dict):
                continue
            threshold = reminder.get("threshold", "?")
            entries.append(
                {
                    "kind": ROE_RENEWAL_KIND,
                    "threshold": threshold,
                    "detail": reminder.get(
                        "detail", f"roe renewal ({threshold}) — re-sign to renew"
                    ),
                    "artifact_id": reminder.get("artifact_id"),
                    "days_remaining": reminder.get("days_remaining"),
                    "effective_until": reminder.get("effective_until"),
                }
            )
        return entries
    except Exception:
        return []


#: Job kind marker stored on RoE expiry watcher jobs.
ROE_EXPIRY_KIND = "roe_expiry"

#: Default watcher cadence: once a day (seconds).
ROE_EXPIRY_INTERVAL_SECONDS = 86400

#: Bounded per-job run history, mirroring :func:`tick_heartbeats`.
ROE_EXPIRY_HISTORY_LIMIT = 20


def schedule_roe_expiry(
    store: JobStore,
    artifact_path: str,
    *,
    interval_seconds: int | float = ROE_EXPIRY_INTERVAL_SECONDS,
    due_at: int | float,
) -> str:
    """Schedule a recurring RoE expiry watcher for ``artifact_path``.

    Mirrors :func:`schedule_heartbeat`: validates a non-empty artifact
    path and a positive interval, then stamps ``kind`` + ``artifact_path``
    on the stored job. Returns the job id. The default cadence is daily.
    """
    if not isinstance(artifact_path, str) or not artifact_path.strip():
        raise ValueError("artifact_path must be a non-empty string")
    if isinstance(interval_seconds, bool) or interval_seconds <= 0:
        raise ValueError("a roe-expiry watcher needs a positive interval_seconds")
    prompt = f"roe-expiry-watch {artifact_path.strip()}"
    job_id = store.schedule(prompt, due_at, interval_seconds=interval_seconds)
    for job in store.jobs:
        if isinstance(job, dict) and job.get("id") == job_id:
            job["kind"] = ROE_EXPIRY_KIND
            job["artifact_path"] = artifact_path.strip()
            break
    store._save()
    return job_id


def list_roe_expiries(store: JobStore) -> list[dict]:
    """All RoE expiry watcher jobs, complete or not."""
    return [
        job
        for job in store.jobs
        if isinstance(job, dict) and job.get("kind") == ROE_EXPIRY_KIND
    ]


def clear_roe_expiry(store: JobStore, job_id: str) -> dict:
    """Remove one RoE expiry watcher by id. Unknown id is a structured result."""
    for index, job in enumerate(store.jobs):
        if (
            isinstance(job, dict)
            and job.get("id") == job_id
            and job.get("kind") == ROE_EXPIRY_KIND
        ):
            store.jobs.pop(index)
            store._save()
            return {"cleared": job_id}
    return {"error": f"no roe-expiry job: {job_id!r}"}


def _roe_now(now: Any) -> tuple[Any, float, str]:
    """Normalize ``now`` to ``(checker_now, stamp, day)``.

    Accepts a UTC datetime (hermetic clock) or a unix timestamp; the job
    record only ever stores numeric stamps (JSON-safe), while the checker
    always receives a datetime.
    """
    if isinstance(now, datetime):
        current = now
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        else:
            current = current.astimezone(UTC)
    else:
        current = datetime.fromtimestamp(now, tz=UTC)
    return current, current.timestamp(), current.date().isoformat()


def tick_roe_expiry(store: JobStore, now: Any, checker: Any) -> list[dict]:
    """Run each due RoE expiry watcher through ``checker``.

    Mirrors :func:`tick_heartbeats`: iterates only ``ROE_EXPIRY_KIND``
    non-completed jobs due at ``now``, claims via ``claimed_at``/``history``
    (``ran_at == now`` ⇒ skip), calls ``checker(artifact_path, now)``,
    records ``last_result``/``last_ran_at``, appends bounded history (cap
    20, ``del history[:-20]``), advances ``due_at = now + interval``,
    ``_save()``. Returns only the jobs that *fired* — ``valid`` ticks are
    silent bookkeeping, and each threshold (``expiring-7d`` / ``expiring-1d``
    / ``expired``) fires at most once per UTC calendar day per job, tracked
    as ``job["last_fired"] = {threshold: "YYYY-MM-DD"}``. Pass
    ``skip_kind=ROE_EXPIRY_KIND`` to the generic ``JobStore.tick`` driver
    so it leaves these jobs to this tick. Never calls ``transition()``.
    """
    checker_now, stamp, today = _roe_now(now)
    fired: list[dict] = []
    ran_any = False
    for job in store.jobs:
        if not isinstance(job, dict):
            continue
        if job.get("kind") != ROE_EXPIRY_KIND or job.get("completed"):
            continue
        due_at = job.get("due_at")
        if due_at is None or due_at > stamp:
            continue
        history = job.setdefault("history", [])
        if job.get("claimed_at") == stamp:
            continue
        if any(isinstance(e, dict) and e.get("ran_at") == stamp for e in history):
            continue
        job["claimed_at"] = stamp
        store._save()
        job["last_result"] = checker(job.get("artifact_path", ""), checker_now)
        job["last_ran_at"] = stamp
        history.append({"ran_at": stamp, "result": job["last_result"]})
        del history[:-ROE_EXPIRY_HISTORY_LIMIT]
        job["claimed_at"] = None
        job["due_at"] = stamp + job["interval_seconds"]
        ran_any = True
        result = job["last_result"]
        state = result.get("state") if isinstance(result, dict) else None
        if state in ("expiring-7d", "expiring-1d", "expired"):
            last_fired = job.setdefault("last_fired", {})
            if last_fired.get(state) != today:
                last_fired[state] = today
                fired.append(job)
    if ran_any:
        store._save()
    return fired


#: Job kind marker stored on engagement-digest jobs.
ENGAGEMENT_DIGEST_KIND = "engagement_digest"

#: Default digest cadence: once a week (seconds), matching the report epilog.
ENGAGEMENT_DIGEST_INTERVAL_SECONDS = 604800

#: Bounded per-job run history, mirroring :func:`tick_heartbeats`.
ENGAGEMENT_DIGEST_HISTORY_LIMIT = 20

#: Env override for the digest history source (mirrors the report script).
ENGAGEMENT_DIGEST_REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"


def schedule_engagement_digest(
    store: JobStore,
    *,
    interval_seconds: int | float = ENGAGEMENT_DIGEST_INTERVAL_SECONDS,
    due_at: int | float,
) -> str:
    """Schedule a recurring engagement-digest watcher.

    Mirrors :func:`schedule_roe_expiry`: validates a positive interval,
    then stamps ``kind`` on the stored job. Returns the job id. The
    default cadence is weekly.
    """
    if isinstance(interval_seconds, bool) or interval_seconds <= 0:
        raise ValueError("an engagement digest needs a positive interval_seconds")
    job_id = store.schedule(
        "engagement-digest", due_at, interval_seconds=interval_seconds
    )
    for job in store.jobs:
        if isinstance(job, dict) and job.get("id") == job_id:
            job["kind"] = ENGAGEMENT_DIGEST_KIND
            break
    store._save()
    return job_id


def list_engagement_digests(store: JobStore) -> list[dict]:
    """All engagement-digest jobs, complete or not."""
    return [
        job
        for job in store.jobs
        if isinstance(job, dict) and job.get("kind") == ENGAGEMENT_DIGEST_KIND
    ]


def clear_engagement_digest(store: JobStore, job_id: str) -> dict:
    """Remove one engagement digest by id. Unknown id is a structured result."""
    for index, job in enumerate(store.jobs):
        if (
            isinstance(job, dict)
            and job.get("id") == job_id
            and job.get("kind") == ENGAGEMENT_DIGEST_KIND
        ):
            store.jobs.pop(index)
            store._save()
            return {"cleared": job_id}
    return {"error": f"no engagement-digest job: {job_id!r}"}


def tick_engagement_digest(store: JobStore, now: Any, checker: Any) -> list[dict]:
    """Run each due engagement digest through ``checker``.

    Mirrors :func:`tick_roe_expiry`: iterates only
    ``ENGAGEMENT_DIGEST_KIND`` non-completed jobs due at ``now``,
    accepts a datetime-or-timestamp ``now`` via :func:`_roe_now`, claims
    via ``claimed_at``/``history`` (``ran_at == now`` ⇒ skip), calls
    ``checker(reports_dir, now)`` where ``reports_dir`` is the
    ``OMEGA_PRIME_SIDECAR_REPORTS_DIR`` override or ``None`` (resolve
    the default), records ``last_result``/``last_ran_at``, appends
    bounded history (cap 20, ``del history[:-20]``), advances
    ``due_at = stamp + interval``, ``_save()``. Every fire returns the
    job (the digest always fires when it runs — there is no
    silent-valid state). A checker exception is recorded as the job's
    ``last_result`` (``state: "error"``) with history still appended —
    the store is never corrupted. Pass
    ``skip_kind=ENGAGEMENT_DIGEST_KIND`` to the generic ``JobStore.tick``
    driver so it leaves these jobs to this tick. Never calls
    ``transition()``.
    """
    checker_now, stamp, _today = _roe_now(now)
    override = os.environ.get(ENGAGEMENT_DIGEST_REPORTS_ENV_VAR, "").strip()
    reports_dir = override or None
    fired: list[dict] = []
    ran_any = False
    for job in store.jobs:
        if not isinstance(job, dict):
            continue
        if job.get("kind") != ENGAGEMENT_DIGEST_KIND or job.get("completed"):
            continue
        due_at = job.get("due_at")
        if due_at is None or due_at > stamp:
            continue
        history = job.setdefault("history", [])
        if job.get("claimed_at") == stamp:
            continue
        if any(isinstance(e, dict) and e.get("ran_at") == stamp for e in history):
            continue
        job["claimed_at"] = stamp
        store._save()
        try:
            job["last_result"] = checker(reports_dir, checker_now)
        except Exception as exc:
            job["last_result"] = {"state": "error", "error": str(exc)}
        job["last_ran_at"] = stamp
        history.append({"ran_at": stamp, "result": job["last_result"]})
        del history[:-ENGAGEMENT_DIGEST_HISTORY_LIMIT]
        job["claimed_at"] = None
        job["due_at"] = stamp + job["interval_seconds"]
        ran_any = True
        fired.append(job)
    if ran_any:
        store._save()
    return fired


#: Job kind marker stored on release-watch jobs.
RELEASE_WATCH_KIND = "release_watch"
RELEASE_WATCH_INTERVAL_SECONDS = 86400
RELEASE_WATCH_HISTORY_LIMIT = 20


def schedule_release_watch(
    store: Any, *, interval_seconds: int = 86400, due_at: Any
) -> Any:
    if isinstance(interval_seconds, bool) or interval_seconds <= 0:
        raise ValueError("a release-watch watcher needs a positive interval_seconds")
    job_id = store.schedule("release-watch", due_at, interval_seconds=interval_seconds)
    for job in store.jobs:
        if isinstance(job, dict) and job.get("id") == job_id:
            job["kind"] = RELEASE_WATCH_KIND
            break
    store._save()
    return job_id


def list_release_watches(store: Any) -> list[Any]:
    return [
        job
        for job in store.jobs
        if isinstance(job, dict) and job.get("kind") == RELEASE_WATCH_KIND
    ]


def clear_release_watch(store: Any, job_id: Any) -> dict[str, Any]:
    for index, job in enumerate(store.jobs):
        if (
            isinstance(job, dict)
            and job.get("id") == job_id
            and job.get("kind") == RELEASE_WATCH_KIND
        ):
            store.jobs.pop(index)
            store._save()
            return {"cleared": job_id}
    return {"error": f"no release-watch job: {job_id!r}"}


def tick_release_watch(store: Any, now: Any, checker: Any) -> list[dict]:
    checker_now, stamp, _today = _roe_now(now)
    fired: list[dict] = []
    ran_any = False
    for job in store.jobs:
        if not isinstance(job, dict):
            continue
        if job.get("kind") != RELEASE_WATCH_KIND or job.get("completed"):
            continue
        due_at = job.get("due_at")
        if due_at is None or due_at > stamp:
            continue
        history = job.setdefault("history", [])
        if job.get("claimed_at") == stamp:
            continue
        if any(isinstance(e, dict) and e.get("ran_at") == stamp for e in history):
            continue
        job["claimed_at"] = stamp
        store._save()
        job["last_result"] = checker(checker_now)
        job["last_ran_at"] = stamp
        history.append({"ran_at": stamp, "result": job["last_result"]})
        del history[:-RELEASE_WATCH_HISTORY_LIMIT]
        job["claimed_at"] = None
        job["due_at"] = stamp + job["interval_seconds"]
        ran_any = True
        result = job["last_result"]
        if isinstance(result, dict) and result.get("new"):
            fired.append(job)
    if ran_any:
        store._save()
    return fired


#: Job kind marker stored on coverage-alert jobs.
COVERAGE_ALERT_KIND = "coverage_alert"

#: Default watcher cadence: once a day (seconds).
COVERAGE_ALERT_INTERVAL_SECONDS = 86400

#: Bounded per-job run history, mirroring :func:`tick_heartbeats`.
COVERAGE_ALERT_HISTORY_LIMIT = 20

#: Default pass-rate percent below which a class fires (config key
#: ``redteam.coverage_alert_threshold`` overrides this per deployment;
#: operators pass it to :func:`make_coverage_checker`).
COVERAGE_ALERT_DEFAULT_THRESHOLD = 80.0

#: Env override for the engagement history source (mirrors the report script).
COVERAGE_ALERT_REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"

#: Repo-relative fallback history dir when neither an arg nor the env
#: override names one.
_COVERAGE_DEFAULT_HISTORY = Path(__file__).resolve().parents[2] / "sidecar" / "reports"

#: Pass-rate cell in the rendered coverage matrix, e.g. ``3/3 (100.0%)``.
_COVERAGE_RATE_RE = re.compile(r"\(([\d.]+)%\)")

#: Cached engagement-report script module (loaded once, never raises).
_COVERAGE_REPORT_MODULE: Any = None
_COVERAGE_REPORT_LOADED = False


def _coverage_report_module() -> Any | None:
    """Load ``scripts/engagement-report.py`` once (cached importlib)."""
    global _COVERAGE_REPORT_MODULE, _COVERAGE_REPORT_LOADED
    if _COVERAGE_REPORT_LOADED:
        return _COVERAGE_REPORT_MODULE
    _COVERAGE_REPORT_LOADED = True
    script = Path(__file__).resolve().parents[2] / "scripts" / "engagement-report.py"
    try:
        spec = importlib.util.spec_from_file_location(
            "omega_prime_coverage_report", script
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        return None
    _COVERAGE_REPORT_MODULE = module
    return module


def _parse_coverage_rows(text: str) -> dict[str, dict[str, Any]]:
    """Parse the ``## Coverage matrix`` table of one engagement report.

    Returns ``{class: {"pass_rate": float | None, "verdict": str}}``.
    ``0/0 (n/a)`` rows (no runs) yield ``pass_rate None`` — unjudgeable,
    never alerting. Never raises: unparseable input yields ``{}``.
    """
    classes: dict[str, dict[str, Any]] = {}
    try:
        lines = text.splitlines()
        start = next(
            index
            for index, line in enumerate(lines)
            if line.strip() == "## Coverage matrix"
        )
    except StopIteration:
        return classes
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != 6:
            continue
        class_name, _cases, _runs, rate, _gaps, verdict = cells
        if class_name in ("class", "---") or not class_name.strip("- "):
            continue
        match = _COVERAGE_RATE_RE.search(rate)
        try:
            pass_rate = float(match.group(1)) if match else None
        except ValueError:
            pass_rate = None
        classes[class_name] = {"pass_rate": pass_rate, "verdict": verdict}
    return classes


def read_coverage_matrix(reports_dir: str | Path | None = None) -> dict[str, Any]:
    """Read the newest engagement report's coverage matrix.

    A ``read_releases_block``-style read through
    ``scripts/engagement-report.py``: resolves the report output dir via
    the script's own ``output_dir()`` over ``reports_dir`` (explicit
    arg, else the ``OMEGA_PRIME_SIDECAR_REPORTS_DIR`` override, else the
    ``sidecar/reports/`` default), picks the newest
    ``engagement-*.md``, and parses its ``## Coverage matrix`` table.

    Returns ``{"report": <name> | None, "classes": {class:
    {"pass_rate": float | None, "verdict": str}}}``. Never raises:
    missing dirs, no reports, or unparseable matrices yield
    ``{"report": None, "classes": {}}``.
    """
    try:
        if reports_dir is not None:
            history = Path(reports_dir)
        else:
            override = os.environ.get(COVERAGE_ALERT_REPORTS_ENV_VAR, "").strip()
            history = Path(override) if override else _COVERAGE_DEFAULT_HISTORY
        module = _coverage_report_module()
        if module is not None:
            out_dir = module.output_dir(history)
        else:
            out_dir = history / "reports"
        names = sorted(
            path.name for path in out_dir.glob("engagement-*.md") if path.is_file()
        )
        if not names:
            return {"report": None, "classes": {}}
        newest = names[-1]
        text = (out_dir / newest).read_text(encoding="utf-8")
        return {"report": newest, "classes": _parse_coverage_rows(text)}
    except (OSError, ValueError, UnicodeDecodeError):
        return {"report": None, "classes": {}}


def _coerce_threshold(threshold: Any) -> float:
    """Pass-rate percent; garbage (including bools) falls back to default."""
    if isinstance(threshold, bool):
        return COVERAGE_ALERT_DEFAULT_THRESHOLD
    try:
        value = float(threshold)
    except (TypeError, ValueError):
        return COVERAGE_ALERT_DEFAULT_THRESHOLD
    if not (value > 0 and value <= 100):
        return COVERAGE_ALERT_DEFAULT_THRESHOLD
    return value


def check_coverage_alert(
    reports_dir: str | Path | None,
    now: Any,
    threshold: Any = COVERAGE_ALERT_DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Check the newest engagement report's coverage matrix vs ``threshold``.

    ``threshold`` is a pass-rate percent (default 80.0; operators pass
    ``load_config(root)["redteam"]["coverage_alert_threshold"]``).
    Returns ``{"state": "ok" | "alert" | "no-reports", "threshold":
    float, "below": [{"class", "pass_rate"}], "report": <name> | None,
    "checked_at": "<ISO-8601 UTC>"}``. Classes with no runs (``0/0
    (n/a)``) are unjudgeable and never fire. Never raises: unreadable
    state degrades to ``no-reports`` (signal, not red) so the tick
    still records history.
    """
    checker_now, _stamp, _today = _roe_now(now)
    checked_at = checker_now.isoformat(timespec="seconds")
    wanted = _coerce_threshold(threshold)
    try:
        matrix = read_coverage_matrix(reports_dir)
    except Exception:
        matrix = {"report": None, "classes": {}}
    if matrix.get("report") is None:
        return {
            "state": "no-reports",
            "threshold": wanted,
            "below": [],
            "report": None,
            "checked_at": checked_at,
        }
    classes = matrix.get("classes")
    if not isinstance(classes, dict):
        classes = {}
    below = [
        {"class": name, "pass_rate": info["pass_rate"]}
        for name, info in classes.items()
        if isinstance(info, dict)
        and info.get("pass_rate") is not None
        and info["pass_rate"] < wanted
    ]
    return {
        "state": "alert" if below else "ok",
        "threshold": wanted,
        "below": below,
        "report": matrix.get("report"),
        "checked_at": checked_at,
    }


def make_coverage_checker(
    threshold: Any = COVERAGE_ALERT_DEFAULT_THRESHOLD,
) -> Callable[[Any, Any], dict[str, Any]]:
    """Build a ``checker(reports_dir, now)`` closure for the tick driver.

    The returned checker never raises: unreadable state degrades to
    ``{"state": "no-reports", ...}`` so the tick records history with
    the store untouched (signal, not red).
    """
    wanted = _coerce_threshold(threshold)

    def checker(reports_dir: Any, now: Any) -> dict[str, Any]:
        try:
            return check_coverage_alert(reports_dir, now, wanted)
        except Exception as exc:
            return {
                "state": "error",
                "threshold": wanted,
                "below": [],
                "report": None,
                "checked_at": None,
                "error": f"{type(exc).__name__}: {exc}",
            }

    return checker


def schedule_coverage_alert(
    store: JobStore,
    *,
    interval_seconds: int | float = COVERAGE_ALERT_INTERVAL_SECONDS,
    due_at: int | float,
) -> str:
    """Schedule a recurring coverage-matrix alert watcher.

    Mirrors :func:`schedule_roe_expiry`: validates a positive interval,
    then stamps ``kind`` on the stored job. Returns the job id. The
    default cadence is daily; the pass threshold lives in the checker
    closure (see :func:`make_coverage_checker`), not on the job.
    """
    if isinstance(interval_seconds, bool) or interval_seconds <= 0:
        raise ValueError("a coverage-alert watcher needs a positive interval_seconds")
    job_id = store.schedule("coverage-alert", due_at, interval_seconds=interval_seconds)
    for job in store.jobs:
        if isinstance(job, dict) and job.get("id") == job_id:
            job["kind"] = COVERAGE_ALERT_KIND
            break
    store._save()
    return job_id


def list_coverage_alerts(store: JobStore) -> list[dict]:
    """All coverage-alert jobs, complete or not."""
    return [
        job
        for job in store.jobs
        if isinstance(job, dict) and job.get("kind") == COVERAGE_ALERT_KIND
    ]


def clear_coverage_alert(store: JobStore, job_id: str) -> dict:
    """Remove one coverage alert by id. Unknown id is a structured result."""
    for index, job in enumerate(store.jobs):
        if (
            isinstance(job, dict)
            and job.get("id") == job_id
            and job.get("kind") == COVERAGE_ALERT_KIND
        ):
            store.jobs.pop(index)
            store._save()
            return {"cleared": job_id}
    return {"error": f"no coverage-alert job: {job_id!r}"}


def tick_coverage_alert(store: JobStore, now: Any, checker: Any) -> list[dict]:
    """Run each due coverage alert through ``checker``.

    Mirrors :func:`tick_engagement_digest` bookkeeping (datetime-or-
    timestamp ``now`` via :func:`_roe_now`, ``claimed_at``/``history``
    claims, bounded history, ``due_at`` advance, ``_save()``; a checker
    exception is recorded as ``last_result`` ``state: "error"``) with
    the :func:`tick_roe_expiry` fire-dedup pattern: each below-threshold
    class fires at most once per UTC calendar day per job, tracked as
    ``job["last_fired"] = {class: "YYYY-MM-DD"}``. Ticks whose classes
    all fired today, ``ok`` ticks, and ``no-reports`` ticks are silent
    bookkeeping. Pass ``skip_kind=COVERAGE_ALERT_KIND`` to the generic
    ``JobStore.tick`` driver so it leaves these jobs to this tick.
    Never calls ``transition()``.
    """
    checker_now, stamp, today = _roe_now(now)
    override = os.environ.get(COVERAGE_ALERT_REPORTS_ENV_VAR, "").strip()
    reports_dir = override or None
    fired: list[dict] = []
    ran_any = False
    for job in store.jobs:
        if not isinstance(job, dict):
            continue
        if job.get("kind") != COVERAGE_ALERT_KIND or job.get("completed"):
            continue
        due_at = job.get("due_at")
        if due_at is None or due_at > stamp:
            continue
        history = job.setdefault("history", [])
        if job.get("claimed_at") == stamp:
            continue
        if any(isinstance(e, dict) and e.get("ran_at") == stamp for e in history):
            continue
        job["claimed_at"] = stamp
        store._save()
        try:
            job["last_result"] = checker(reports_dir, checker_now)
        except Exception as exc:
            job["last_result"] = {"state": "error", "error": str(exc)}
        job["last_ran_at"] = stamp
        history.append({"ran_at": stamp, "result": job["last_result"]})
        del history[:-COVERAGE_ALERT_HISTORY_LIMIT]
        job["claimed_at"] = None
        job["due_at"] = stamp + job["interval_seconds"]
        ran_any = True
        result = job["last_result"]
        if isinstance(result, dict) and result.get("state") == "alert":
            below = result.get("below")
            if isinstance(below, list) and below:
                last_fired = job.setdefault("last_fired", {})
                new_fire = False
                for item in below:
                    cls = item.get("class") if isinstance(item, dict) else item
                    if not isinstance(cls, str) or not cls:
                        continue
                    if last_fired.get(cls) != today:
                        last_fired[cls] = today
                        new_fire = True
                if new_fire:
                    fired.append(job)
    if ran_any:
        store._save()
    return fired


__all__ = [
    "COVERAGE_ALERT_DEFAULT_THRESHOLD",
    "COVERAGE_ALERT_HISTORY_LIMIT",
    "COVERAGE_ALERT_INTERVAL_SECONDS",
    "COVERAGE_ALERT_KIND",
    "COVERAGE_ALERT_REPORTS_ENV_VAR",
    "ENGAGEMENT_DIGEST_HISTORY_LIMIT",
    "ENGAGEMENT_DIGEST_INTERVAL_SECONDS",
    "ENGAGEMENT_DIGEST_KIND",
    "ENGAGEMENT_DIGEST_REPORTS_ENV_VAR",
    "HEARTBEAT_KIND",
    "RELEASE_WATCH_HISTORY_LIMIT",
    "RELEASE_WATCH_INTERVAL_SECONDS",
    "RELEASE_WATCH_KIND",
    "ROE_EXPIRY_HISTORY_LIMIT",
    "ROE_EXPIRY_INTERVAL_SECONDS",
    "ROE_EXPIRY_KIND",
    "ROE_RENEWAL_KIND",
    "clear_coverage_alert",
    "clear_engagement_digest",
    "clear_heartbeat",
    "clear_release_watch",
    "clear_roe_expiry",
    "heartbeat_roe_renewals",
    "heartbeat_runner",
    "list_coverage_alerts",
    "list_engagement_digests",
    "list_heartbeats",
    "list_release_watches",
    "list_roe_expiries",
    "make_coverage_checker",
    "read_coverage_matrix",
    "schedule_coverage_alert",
    "schedule_engagement_digest",
    "schedule_heartbeat",
    "schedule_release_watch",
    "schedule_roe_expiry",
    "tick_coverage_alert",
    "tick_engagement_digest",
    "tick_heartbeats",
    "tick_release_watch",
    "tick_roe_expiry",
]
