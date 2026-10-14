"""Resumable purple-cycle orchestration: one workflow over existing operations.

A *purple cycle* composes the existing purple-team operations into one
fail-closed, resumable workflow with durable per-step records. It is an
analysis / reporting / operations cycle only: it never runs a red-team
simulation, never signs or creates a RoE artifact, never edits repo files, and
never applies a pin bump.

Steps, in canonical order (:data:`STEP_NAMES`):

1. ``preflight`` (all modes, read-only): sidecar harness verdict plus the RoE
   lifecycle of the configured artifacts. Informational (``ok``/``warn``).
2. ``release-watch`` (run/apply): diff upstream tags against the state file
   ``<reports>/release-watch.json`` (writes it, uses the network).
3. ``pin-bump-audit`` (run/apply): audit-only pin-bump check for the newest
   new tag. ``apply_scheduled_bump`` / a non-audit ``bump`` are never called.
4. ``engagement`` (all modes, pure): engagement summary (and digest when the
   report script provides ``build_digest``).
5. ``findings-plan`` (all modes): findings-sync dispatch plan with ledgers
   forced off; the runner is never reachable.
6. ``findings-dispatch`` (apply only): refused (exit 3) unless the harness is
   IDLE and no configured RoE is expired/missing/invalid; then the real
   findings-sync tick.

Modes (:data:`MODES`): ``plan`` is PURE (no checkpoint, no ledger, no state
file, no network, no subprocess); ``run`` adds release-watch and the pin-bump
audit; ``apply`` additionally dispatches findings.

Durability: run/apply checkpoint through
:func:`omega_prime.durable.workflows.run_workflow` at
``<root>/workflows/<cycle_id>.json`` (step records live in the checkpoint
``state``). A step that returns (ok/warn/skipped/blocked) is checkpointed; a
step error halts the cycle WITHOUT checkpointing that step, so a rerun
re-executes it while completed steps are reused. Steps after a halt are
reported as skipped/``halted``. Re-running a finished cycle executes nothing
and returns the stored result (``resumed`` True). An append-only ledger
``<reports>/purple-cycle.ndjson`` receives one secret-free row per executed
step.

Exit codes: 0 completed (ok/warn/skipped only), 1 any step error, 3 apply
refused/blocked, 2 CLI usage error.

CLI::

    python -m omega_prime.grokbot.purple_cycle [--mode plan|run|apply] [--apply]
        [--steps a,b] [--reports-dir DIR] [--root DIR] [--now ISO-8601-UTC]
        [--force-new] [--json]
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega_prime.durable import workflows

STEP_NAMES = (
    "preflight",
    "release-watch",
    "pin-bump-audit",
    "engagement",
    "findings-plan",
    "findings-dispatch",
)
MODES = ("plan", "run", "apply")

LEDGER_FILENAME = "purple-cycle.ndjson"
RELEASE_WATCH_FILENAME = "release-watch.json"
REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"
STATE_DIR_ENV_VAR = "OMEGA_PRIME_STATE_DIR"

#: Per-command timeout of the default pin-bump subprocess runner.
PIN_RUN_TIMEOUT_SECONDS = 300

EXIT_OK = 0
EXIT_STEP_ERROR = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3

_STATUS_EXIT = {"ok": 0, "warn": 0, "skipped": 0, "error": 1, "blocked": 3}
_REFUSED_ROE_STATES = ("expired", "missing", "invalid")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORD_RE = re.compile(r"^[A-Za-z0-9._-]{1,48}$")
_TAG_RE = re.compile(r"^v?\d+\.\d+(?:\.\d+)*(?:[-+.][0-9A-Za-z.]+)*$")
_MAX_TAG_LENGTH = 64
_MAX_LINE_LENGTH = 200
_MAX_DIGEST_LINES = 20
_ENGAGEMENT_MODULE: Any = None


# --------------------------------------------------------------------------
# small helpers


def _word(value: Any, default: str = "unknown") -> str:
    """A short vocabulary-safe word, else ``default`` (never free text)."""
    return value if isinstance(value, str) and _WORD_RE.match(value) else default


def _safe_tag(value: Any) -> str | None:
    """The tag when it looks like a plain version tag, else ``None``."""
    if (
        isinstance(value, str)
        and len(value) <= _MAX_TAG_LENGTH
        and _TAG_RE.match(value) is not None
    ):
        return value
    return None


def _clip(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    printable = "".join(ch for ch in text if ch.isprintable())
    return printable.strip()[:_MAX_LINE_LENGTH]


def _count(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _record(
    name: str,
    status: str,
    reason: str,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "exit": _STATUS_EXIT[status],
        "reason": reason,
        "detail": dict(detail) if detail else {},
    }


def _as_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC).replace(microsecond=0)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC, microsecond=0)
    return now.astimezone(UTC).replace(microsecond=0)


def _resolve_reports_dir(env: Mapping[str, str]) -> Path:
    """Env override (mapping first, then process env), else the sidecar default."""
    override = (env.get(REPORTS_ENV_VAR) or "").strip()
    if not override:
        override = (os.environ.get(REPORTS_ENV_VAR) or "").strip()
    if override:
        return Path(override)
    try:
        from sidecar import state as sidecar_state

        return Path(sidecar_state.reports_dir())
    except ImportError:
        return Path("sidecar") / "reports"


def _forbidden_runner(argv: Any, data: Any) -> Any:
    raise AssertionError("findings-plan must never call the dispatch runner")


def _default_pin_run(cmd: Sequence[str], cwd: Path | None = None) -> Any:
    """Subprocess seam for the pin-bump audit with a per-command timeout."""
    return subprocess.run(
        list(cmd),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=PIN_RUN_TIMEOUT_SECONDS,
        check=False,
    )


class _TimeoutTracker:
    """Delegating runner that remembers whether a command timed out."""

    def __init__(self, inner: Callable[..., Any]) -> None:
        self.inner = inner
        self.timed_out = False

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self.inner(*args, **kwargs)
        except subprocess.TimeoutExpired:
            self.timed_out = True
            raise


def _default_fetch() -> Any:
    from omega_prime.grokbot import release_watch

    return release_watch.fetch_upstream_tags(release_watch.DEFAULT_REPO)


def _load_engagement_module() -> Any:
    """Load ``scripts/engagement-report.py`` by path (hyphenated name)."""
    global _ENGAGEMENT_MODULE
    if _ENGAGEMENT_MODULE is None:
        script = _REPO_ROOT / "scripts" / "engagement-report.py"
        spec = importlib.util.spec_from_file_location(
            "omega_prime_purple_cycle_engagement", script
        )
        if spec is None or spec.loader is None:
            raise ImportError("engagement-report script cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _ENGAGEMENT_MODULE = module
    return _ENGAGEMENT_MODULE


def _digest_lines(value: Any) -> list[str]:
    """Digest output as a short list of clipped single-line strings."""
    if isinstance(value, Mapping):
        value = value.get("lines", [])
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, (list, tuple)):
        return []
    lines = [_clip(item) for item in value if isinstance(item, str)]
    return [line for line in lines if line][:_MAX_DIGEST_LINES]


# --------------------------------------------------------------------------
# context and steps


@dataclass
class _Ctx:
    mode: str
    cycle_id: str
    root: Path | None
    state_root: Path
    reports_dir: Path
    config: dict[str, Any] | None
    env: Mapping[str, str]
    now: datetime
    runner: Any
    pin_run: Any
    fetch: Callable[[], Any] | None
    ledger: bool


def _roe_conflict_count(roe_lifecycle: Any, paths: Sequence[Any], now: datetime) -> int:
    """Advisory grant-conflict count; fail-open (0) on any detector failure."""
    try:
        found = roe_lifecycle.grant_conflicts(list(paths), now=now)
    except Exception:
        return 0
    return len(found) if isinstance(found, list) else 0


def _preflight_facts(ctx: _Ctx) -> dict[str, Any]:
    from omega_prime.config import redteam_roe_files
    from omega_prime.grokbot import roe_lifecycle
    from sidecar import harness

    harness_state, harness_cause, _detail = harness.evaluate(ctx.env)
    paths = redteam_roe_files(ctx.config) if ctx.config is not None else []
    conflicts: int | None = None
    if paths:
        roe = roe_lifecycle.check_roe_lifecycle_all(paths, ctx.now, audit_log=None)
        roe_state = _word(roe.get("state"))
        if len(paths) >= 2:
            conflicts = _roe_conflict_count(roe_lifecycle, paths, ctx.now)
    else:
        roe_state = "none"
    facts: dict[str, Any] = {
        "harness_state": _word(harness_state),
        "harness_cause": _word(harness_cause),
        "roe_state": roe_state,
        "roe_artifacts": len(paths),
    }
    if conflicts is not None:
        facts["roe_conflicts"] = conflicts
    if ctx.config is None:
        facts["config_error"] = True
    return facts


def _step_preflight(ctx: _Ctx, state: dict[str, Any]) -> dict[str, Any]:
    facts = _preflight_facts(ctx)
    if facts["harness_state"] != "IDLE":
        return _record("preflight", "warn", "harness-blocked", facts)
    if facts["roe_state"] not in ("valid", "none"):
        return _record("preflight", "warn", f"roe-{facts['roe_state']}", facts)
    if facts.get("roe_conflicts"):
        return _record("preflight", "warn", "roe-conflicts", facts)
    if facts.get("config_error"):
        return _record("preflight", "warn", "config-error", facts)
    return _record("preflight", "ok", "ok", facts)


def _find(state: Mapping[str, Any], name: str) -> dict[str, Any] | None:
    for rec in state.get("steps", []):
        if isinstance(rec, dict) and rec.get("name") == name:
            return rec
    return None


def _step_release_watch(ctx: _Ctx, state: dict[str, Any]) -> dict[str, Any]:
    if ctx.mode == "plan":
        return _record("release-watch", "skipped", "plan-mode")
    from omega_prime.grokbot import release_watch

    fetch = ctx.fetch if ctx.fetch is not None else _default_fetch
    result = release_watch.check_release_watch(
        ctx.reports_dir / RELEASE_WATCH_FILENAME, fetch, ctx.now
    )
    new = [entry for entry in result.get("new", []) if isinstance(entry, dict)]
    tags = [_safe_tag(entry.get("tag")) for entry in new]
    detail: dict[str, Any] = {
        "new_count": len(new),
        "seeded": bool(result.get("seeded")),
        "new_tags": [tag for tag in tags if tag is not None],
        "newest_tag": tags[0] if tags else None,
    }
    if detail["seeded"]:
        return _record("release-watch", "ok", "seeded", detail)
    reason = "new-release" if new else "no-new-release"
    return _record("release-watch", "ok", reason, detail)


def _step_pin_bump_audit(ctx: _Ctx, state: dict[str, Any]) -> dict[str, Any]:
    if ctx.mode == "plan":
        return _record("pin-bump-audit", "skipped", "plan-mode")
    watch = _find(state, "release-watch")
    detail = watch["detail"] if watch is not None and watch["status"] == "ok" else {}
    if not detail.get("new_count"):
        return _record("pin-bump-audit", "skipped", "no-new-release")
    tag = detail.get("newest_tag")
    if not isinstance(tag, str) or _safe_tag(tag) is None:
        return _record("pin-bump-audit", "warn", "unsafe-tag", {"audited": False})
    from omega_prime.grokbot import pin_bump

    tracker = _TimeoutTracker(
        ctx.pin_run if ctx.pin_run is not None else _default_pin_run
    )
    root = ctx.root if ctx.root is not None else pin_bump.REPO_ROOT
    result = pin_bump.audit_scheduled_job({"to": tag}, root=root, run=tracker)
    verdict = _word(result.get("verdict"))
    exit_code = result.get("exit_code")
    summary: dict[str, Any] = {
        "tag": tag,
        "verdict": verdict,
        "exit_code": exit_code if isinstance(exit_code, int) else None,
        "semver_compatible": bool(result.get("semver_compatible")),
        "timeout": tracker.timed_out,
    }
    if tracker.timed_out or verdict == "error":
        return _record("pin-bump-audit", "error", "step-error", summary)
    if verdict == "regressed":
        return _record("pin-bump-audit", "warn", "regressed", summary)
    if not summary["semver_compatible"]:
        return _record("pin-bump-audit", "warn", "semver-incompatible", summary)
    return _record("pin-bump-audit", "ok", "ok", summary)


def _step_engagement(ctx: _Ctx, state: dict[str, Any]) -> dict[str, Any]:
    module = _load_engagement_module()
    summary = dict(module.summarize(ctx.reports_dir, None))
    detail: dict[str, Any] = {
        "state": _word(summary.get("state")),
        "line": _clip(summary.get("line")),
    }
    build_digest = getattr(module, "build_digest", None)
    if not callable(build_digest):
        return _record("engagement", "ok", "ok", detail)
    try:
        detail["digest"] = _digest_lines(build_digest(ctx.reports_dir))
    except Exception:
        detail["digest"] = []
        detail["digest_error"] = True
        return _record("engagement", "warn", "digest-error", detail)
    return _record("engagement", "ok", "ok", detail)


def _plan_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Deep copy of ``config`` with both findings-sync ledgers forced off."""
    cfg = copy.deepcopy(dict(config))
    redteam = cfg.get("redteam")
    node = redteam.get("findings_sync") if isinstance(redteam, dict) else None
    if isinstance(node, dict):
        node["dispatch_ledger"] = ""
        node["grant_usage_ledger"] = ""
    return cfg


def _tick_detail(result: Mapping[str, Any]) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "would_sync": _count(result.get("pushed")),
        "already_synced": _count(result.get("skipped")),
        "quiet_held": _count(result.get("quiet_skipped")),
        "grant_conflicts": "grant_conflicts" in result,
        "dry_run": bool(result.get("dry_run")),
    }
    if "budget_held" in result:
        detail["budget_held"] = _count(result.get("budget_held"))
    return detail


def _step_findings_plan(ctx: _Ctx, state: dict[str, Any]) -> dict[str, Any]:
    if ctx.config is None:
        return _record("findings-plan", "error", "config-error")
    from omega_prime.grokbot import findings_sync

    result = findings_sync.tick(
        _plan_config(ctx.config),
        reports_directory=ctx.reports_dir,
        runner=_forbidden_runner,
        dispatch_dry_run=True,
        now=ctx.now,
    )
    if result.get("disabled"):
        return _record("findings-plan", "skipped", "disabled")
    detail = _tick_detail(result)
    if result.get("error"):
        return _record("findings-plan", "warn", "plan-error", detail)
    if result.get("skipped_tracker"):
        return _record("findings-plan", "warn", "tracker-skipped", detail)
    return _record("findings-plan", "ok", "ok", detail)


def _refusal_cause(facts: Mapping[str, Any]) -> str | None:
    if facts.get("harness_state") != "IDLE":
        return _word(facts.get("harness_cause"))
    roe_state = facts.get("roe_state")
    if roe_state in _REFUSED_ROE_STATES:
        return _word(roe_state)
    return None


def _step_findings_dispatch(ctx: _Ctx, state: dict[str, Any]) -> dict[str, Any]:
    if ctx.mode != "apply":
        reason = "plan-mode" if ctx.mode == "plan" else "not-selected"
        return _record("findings-dispatch", "skipped", reason)
    recorded = _find(state, "preflight")
    if recorded is not None and recorded["status"] in ("ok", "warn"):
        facts = recorded["detail"]
    else:
        facts = _preflight_facts(ctx)
    cause = _refusal_cause(facts)
    if cause is not None:
        detail = {"cause": cause}
        return _record("findings-dispatch", "blocked", "apply-refused", detail)
    if ctx.config is None:
        return _record("findings-dispatch", "error", "config-error")
    from omega_prime.grokbot import findings_sync

    result = findings_sync.tick(
        ctx.config,
        reports_directory=ctx.reports_dir,
        runner=ctx.runner,
        now=ctx.now,
    )
    if result.get("disabled"):
        return _record("findings-dispatch", "skipped", "disabled")
    detail = _tick_detail(result)
    if result.get("error"):
        return _record("findings-dispatch", "error", "dispatch-error", detail)
    if result.get("skipped_tracker"):
        return _record("findings-dispatch", "warn", "tracker-skipped", detail)
    return _record("findings-dispatch", "ok", "ok", detail)


_STEP_FUNCS: dict[str, Callable[[_Ctx, dict[str, Any]], dict[str, Any]]] = {
    "preflight": _step_preflight,
    "release-watch": _step_release_watch,
    "pin-bump-audit": _step_pin_bump_audit,
    "engagement": _step_engagement,
    "findings-plan": _step_findings_plan,
    "findings-dispatch": _step_findings_dispatch,
}


def _execute(ctx: _Ctx, name: str, state: dict[str, Any]) -> dict[str, Any]:
    """Run one step to a record; exceptions become ``error`` records."""
    if name not in state.get("selected", []):
        return _record(name, "skipped", "not-selected")
    try:
        return _STEP_FUNCS[name](ctx, state)
    except Exception as exc:
        return _record(
            name, "error", "step-error", {"error_type": _word(type(exc).__name__)}
        )


def _append_ledger(ctx: _Ctx, rec: Mapping[str, Any]) -> None:
    """Append one secret-free ledger row. Best-effort; never raises."""
    try:
        path = ctx.reports_dir / LEDGER_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": ctx.now.isoformat(timespec="seconds"),
            "cycle_id": ctx.cycle_id,
            "step": rec["name"],
            "status": rec["status"],
            "exit": rec["exit"],
            "reason": rec["reason"],
        }
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:
        return None
    return None


class _StepFailure(Exception):
    """A step errored: carries the pre-step state and the error record."""

    def __init__(self, state: dict[str, Any], rec: dict[str, Any]) -> None:
        super().__init__(rec["name"])
        self.state = state
        self.rec = rec


def _make_step(ctx: _Ctx, name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def step(state: dict[str, Any]) -> dict[str, Any]:
        records = list(state.get("steps", []))
        blocked = state.get("blocked")
        if blocked:
            rec = _record(name, "skipped", "blocked-upstream")
        else:
            rec = _execute(ctx, name, state)
            if ctx.ledger and rec["reason"] != "not-selected":
                _append_ledger(ctx, rec)
        if rec["status"] == "error":
            raise _StepFailure(dict(state), rec)
        new_state = dict(state)
        new_state["steps"] = [*records, rec]
        if rec["status"] == "blocked":
            new_state["blocked"] = {
                "cause": _word(rec["detail"].get("cause")),
                "step": name,
            }
        return new_state

    return step


# --------------------------------------------------------------------------
# result assembly


def _result(
    ctx: _Ctx,
    state: Mapping[str, Any],
    *,
    failed: dict[str, Any] | None,
    resumed: bool,
    checkpoint: str | None,
    ledger: str | None,
) -> dict[str, Any]:
    records = [dict(rec) for rec in state.get("steps", [])]
    if failed is not None:
        records.append(failed)
    seen = {rec["name"] for rec in records}
    for name in STEP_NAMES:
        if name not in seen:
            records.append(_record(name, "skipped", "halted"))
    order = {name: index for index, name in enumerate(STEP_NAMES)}
    records.sort(key=lambda rec: order[rec["name"]])
    blocked = state.get("blocked")
    if any(rec["status"] == "error" for rec in records):
        outcome, code = "failed", EXIT_STEP_ERROR
    elif blocked or any(rec["status"] == "blocked" for rec in records):
        outcome, code = "blocked", EXIT_BLOCKED
    else:
        outcome, code = "completed", EXIT_OK
    return {
        "ok": code == EXIT_OK,
        "exit": code,
        "mode": ctx.mode,
        "cycle_id": ctx.cycle_id,
        "state": outcome,
        "resumed": resumed,
        "blocked": (
            {"cause": str(blocked["cause"]), "step": str(blocked["step"])}
            if blocked
            else None
        ),
        "steps": records,
        "checkpoint": checkpoint,
        "ledger": ledger,
    }


def _pending_error(state: Mapping[str, Any], reason: str) -> dict[str, Any]:
    done = {rec.get("name") for rec in state.get("steps", []) if isinstance(rec, dict)}
    pending = next(name for name in STEP_NAMES if name not in done)
    return _record(pending, "error", reason)


def _validated_prior(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    state = checkpoint.get("state")
    done = checkpoint.get("done")
    steps = state.get("steps") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict)
        or not isinstance(done, int)
        or not isinstance(steps, list)
        or not 0 <= done <= len(STEP_NAMES)
        or len(steps) != done
        or not all(
            isinstance(rec, dict) and rec.get("name") in STEP_NAMES for rec in steps
        )
    ):
        raise ValueError("checkpoint is not a purple-cycle state")
    return state


# --------------------------------------------------------------------------
# public API


def run_cycle(
    *,
    mode: str = "plan",
    root: Path | None = None,
    reports_dir: Path | None = None,
    config: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
    steps: Sequence[str] | None = None,
    force_new: bool = False,
    runner: Any = None,
    pin_run: Any = None,
    fetch: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Run (or plan) one purple cycle and return its structured result.

    See the module docstring. Raises ``ValueError`` only for an unknown
    ``mode`` or step name (before doing anything); step failures never raise.
    ``now`` defaults to the current UTC time here and nowhere else.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    if steps is None:
        selected = list(STEP_NAMES)
    else:
        if isinstance(steps, str) or not steps:
            raise ValueError("steps must be a non-empty sequence of step names")
        unknown = [name for name in steps if name not in STEP_NAMES]
        if unknown:
            raise ValueError(f"unknown step {unknown[0]!r}; expected {STEP_NAMES}")
        selected = [name for name in STEP_NAMES if name in set(steps)]
    current = _as_utc(now)
    environ: Mapping[str, str] = env if env is not None else os.environ
    cycle_id = f"purple-cycle-{current:%Y%m%d}-{mode}"
    if force_new:
        cycle_id += f"-{current:%H%M%S}"
    resolved_config: dict[str, Any] | None
    if config is not None:
        resolved_config = dict(config)
    else:
        try:
            from omega_prime.config import load_config

            resolved_config = load_config(root)
        except Exception:
            resolved_config = None
    state_dir = (environ.get(STATE_DIR_ENV_VAR) or "").strip()
    fallback_root = Path(state_dir) if state_dir else _REPO_ROOT
    state_root = Path(root) if root is not None else fallback_root
    ctx = _Ctx(
        mode=mode,
        cycle_id=cycle_id,
        root=Path(root) if root is not None else None,
        state_root=state_root,
        reports_dir=(
            Path(reports_dir)
            if reports_dir is not None
            else _resolve_reports_dir(environ)
        ),
        config=resolved_config,
        env=environ,
        now=current,
        runner=runner,
        pin_run=pin_run,
        fetch=fetch,
        ledger=mode != "plan",
    )
    initial: dict[str, Any] = {
        "cycle_id": cycle_id,
        "mode": mode,
        "now": current.isoformat(timespec="seconds"),
        "selected": selected,
        "steps": [],
        "blocked": None,
    }
    funcs = [_make_step(ctx, name) for name in STEP_NAMES]
    if mode == "plan":
        return _run_plan(ctx, initial, funcs)
    return _run_durable(ctx, initial, funcs)


def _run_plan(
    ctx: _Ctx,
    initial: dict[str, Any],
    funcs: list[Callable[[dict[str, Any]], dict[str, Any]]],
) -> dict[str, Any]:
    state = initial
    failed: dict[str, Any] | None = None
    try:
        for func in funcs:
            state = func(state)
    except _StepFailure as exc:
        state, failed = exc.state, exc.rec
    return _result(
        ctx, state, failed=failed, resumed=False, checkpoint=None, ledger=None
    )


def _run_durable(
    ctx: _Ctx,
    initial: dict[str, Any],
    funcs: list[Callable[[dict[str, Any]], dict[str, Any]]],
) -> dict[str, Any]:
    checkpoint_path = str(ctx.state_root / "workflows" / f"{ctx.cycle_id}.json")
    ledger_path = str(ctx.reports_dir / LEDGER_FILENAME)
    prior_state: dict[str, Any] | None = None
    try:
        document = workflows.load_checkpoint(ctx.state_root, ctx.cycle_id)
        if document is not None:
            prior_state = _validated_prior(document)
    except ValueError:
        return _result(
            ctx,
            initial,
            failed=_pending_error(initial, "checkpoint-unreadable"),
            resumed=False,
            checkpoint=checkpoint_path,
            ledger=ledger_path,
        )
    resumed = prior_state is not None and bool(prior_state["steps"])
    failed: dict[str, Any] | None = None
    try:
        outcome = workflows.run_workflow(
            ctx.state_root, ctx.cycle_id, funcs, state=initial
        )
        final: dict[str, Any] = outcome["state"]
    except _StepFailure as exc:
        final, failed = exc.state, exc.rec
    except (OSError, ValueError):
        final = prior_state if prior_state is not None else initial
        failed = _pending_error(final, "checkpoint-error")
    return _result(
        ctx,
        final,
        failed=failed,
        resumed=resumed,
        checkpoint=checkpoint_path,
        ledger=ledger_path,
    )


# --------------------------------------------------------------------------
# CLI


def _format_table(result: Mapping[str, Any]) -> str:
    lines = [
        f"purple-cycle {result['cycle_id']} state={result['state']} "
        f"exit={result['exit']}"
    ]
    lines.extend(
        f"{rec['name']} {rec['status']} {rec['reason']}" for rec in result["steps"]
    )
    return "\n".join(lines)


def _usage_error(message: str) -> int:
    print(f"purple-cycle: {message}", file=sys.stderr)
    return EXIT_USAGE


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    config: Mapping[str, Any] | None = None,
    runner: Any = None,
    pin_run: Any = None,
    fetch: Callable[[], Any] | None = None,
) -> int:
    """CLI entry point; the exit code is the run's ``exit`` (2 = usage error)."""
    parser = argparse.ArgumentParser(
        prog="python -m omega_prime.grokbot.purple_cycle",
        description=(
            "Resumable purple cycle: preflight, release-watch, pin-bump audit, "
            "engagement, findings plan and (apply only) findings dispatch. "
            "Exit 0 completed, 1 step error, 2 usage error, 3 apply refused."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=None,
        help="plan (default; pure), run (adds release-watch + pin-bump audit), "
        "or apply (adds findings dispatch)",
    )
    parser.add_argument("--apply", action="store_true", help="alias for --mode apply")
    parser.add_argument(
        "--steps",
        default=None,
        help="comma-separated subset of: " + ", ".join(STEP_NAMES),
    )
    parser.add_argument("--reports-dir", default=None, help="reports directory")
    parser.add_argument("--root", default=None, help="state/checkpoint root")
    parser.add_argument("--now", default=None, help="ISO-8601 UTC instant")
    parser.add_argument(
        "--force-new",
        action="store_true",
        help="start a new cycle id (appends -HHMMSS) instead of resuming",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the result as one JSON line"
    )
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    mode = args.mode
    if args.apply:
        if mode not in (None, "apply"):
            return _usage_error(f"--apply conflicts with --mode {mode}")
        mode = "apply"
    mode = mode or "plan"
    selected: list[str] | None = None
    if args.steps is not None:
        selected = [part.strip() for part in args.steps.split(",") if part.strip()]
        if not selected:
            return _usage_error("--steps must name at least one step")
        unknown = [name for name in selected if name not in STEP_NAMES]
        if unknown:
            return _usage_error(f"unknown step {unknown[0]!r}")
    now: datetime | None = None
    if args.now is not None:
        try:
            now = datetime.fromisoformat(args.now)
        except ValueError:
            return _usage_error("--now must be an ISO-8601 instant")
    try:
        result = run_cycle(
            mode=mode,
            root=Path(args.root) if args.root else None,
            reports_dir=Path(args.reports_dir) if args.reports_dir else None,
            config=config,
            env=env,
            now=now,
            steps=selected,
            force_new=args.force_new,
            runner=runner,
            pin_run=pin_run,
            fetch=fetch,
        )
    except ValueError as exc:
        return _usage_error(str(exc))
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(_format_table(result))
    return int(result["exit"])


__all__ = ["MODES", "STEP_NAMES", "main", "run_cycle"]


if __name__ == "__main__":
    sys.exit(main())
