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
   ``<reports>/release-watch.json`` (writes it, uses the network). The newest
   new tag is written to ``<reports>/purple-cycle-pending.json`` inside the
   step (before its checkpoint), so a crash cannot lose its audit.
3. ``pin-bump-audit`` (run/apply): audit-only pin-bump check for the newest
   new tag (or the pending tag of an earlier cycle). A failing audit is
   NON-halting: ``warn``/``audit-failed`` (detail: tag, error type, attempt
   count); the pending file stores ``{"tag": T, "attempts": n}`` and after
   ``MAX_AUDIT_ATTEMPTS`` (3) failed attempts the tag is given up
   (``audit-abandoned``, once) so a persistent failure never starves apply.
   A success clears the pending file. Only ``v<digits>.<digits>...`` tags that
   ``pin_bump`` itself accepts are audited. ``apply_scheduled_bump`` / a
   non-audit ``bump`` are never called.
4. ``engagement`` (all modes, pure): engagement summary (and digest when the
   report script provides ``build_digest``).
5. ``findings-plan`` (all modes): findings-sync dispatch plan with ledgers
   forced off; the runner is never reachable.
6. ``findings-dispatch`` (apply only): refused (exit 3) unless the sidecar
   harness is IDLE (env verdict, plus ``redteam.kill`` set or an explicit
   ``redteam.enabled: false`` in the merged config) and at least one RoE
   artifact is configured and NONE is expired, missing, invalid nor revoked.
   The artifacts are the union of ``redteam.roe_file(s)`` and the findings-sync
   tick's grant keys (``roe_artifact(s)`` ... on the ``findings_sync`` and
   ``redteam`` nodes). A grant is revoked when any ``grant-revoked`` row in
   an audit log (``roe_audit_log``/``audit_log``/``roe_audit_log_path``/
   ``audit_log_path`` under ``redteam``, ``redteam.findings_sync`` or the top
   level; read-only) names it, compared by realpath (relative to the cwd),
   file identity (symlink/hardlink aliases) and its own id fields. Then the
   real findings-sync tick runs. The gate is re-evaluated FRESH every time the
   step is reached (a caller-injected ``now`` is used as is; otherwise the
   clock is re-read); checkpointed preflight facts are reporting only.

Modes (:data:`MODES`): ``plan`` is PURE (no checkpoint, no ledger, no state
file, no network, no subprocess); ``run`` adds release-watch and the pin-bump
audit; ``apply`` additionally dispatches findings.

Durability: run/apply checkpoint through
:func:`omega_prime.durable.workflows.run_workflow` at
``<reports>/purple-cycle/workflows/<cycle_id>.json`` (step records live in
the checkpoint ``state``). A step that returns (ok/warn/skipped) is
checkpointed; a step error or a ``blocked`` refusal halts the cycle WITHOUT
checkpointing that step, so a rerun re-evaluates it while completed steps are
reused. The cycle id is ``purple-cycle-<UTC date>-<mode>``, plus ``-s<8 hex>``
(sha256 of the comma-joined selected names) for a non-default ``--steps``
selection. A checkpoint whose mode/cycle id/selection do not match the current
run, or whose records are not exactly well formed (typed fields, a
``[a-z0-9-]{1,40}`` reason, a flat JSON-scalar ``detail`` of at most 4 KiB),
is treated as unreadable (``checkpoint-unreadable``). Steps
after a halt are reported as skipped/``halted``. Re-running a finished cycle
executes nothing and returns the stored result (``resumed`` True). A
checkpoint-write failure is attributed to the step that ran. An
append-only ledger ``<reports>/purple-cycle.ndjson`` receives one secret-free
row per executed step; a ``blocked`` row carries ``reason:cause`` (cause only
when it is a short vocabulary word) and is appended once, not per rerun.

Exit codes: 0 completed (ok/warn/skipped only), 1 any step error, 3 apply
refused/blocked, 2 CLI usage error.

CLI::

    python -m omega_prime.grokbot.purple_cycle [--mode plan|run|apply] [--apply]
        [--steps a,b] [--reports-dir DIR] [--root DIR] [--now ISO-8601-UTC]
        [--force-new] [--json]
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
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
PENDING_FILENAME = "purple-cycle-pending.json"
#: Checkpoint directory name under the reports dir.
CHECKPOINT_DIRNAME = "purple-cycle"
REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"

#: Per-command timeout of the default pin-bump subprocess runner.
PIN_RUN_TIMEOUT_SECONDS = 300

EXIT_OK = 0
EXIT_STEP_ERROR = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3

_STATUS_EXIT = {"ok": 0, "warn": 0, "skipped": 0, "error": 1, "blocked": 3}
_ROE_AUDIT_LOG_KEYS = (
    "roe_audit_log",
    "audit_log",
    "roe_audit_log_path",
    "audit_log_path",
)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORD_RE = re.compile(r"[A-Za-z0-9._-]{1,48}")
#: Flat (linear-time) subset of the ref grammar ``pin_bump`` accepts
#: (``^v[0-9][0-9A-Za-z._-]*$``): a ``v`` tag the cycle accepts is a tag the
#: audit will accept. No nested quantifiers (no catastrophic backtracking).
_TAG_RE = re.compile(r"v[0-9]+(?:\.[0-9]+)+(?:[._-][0-9A-Za-z._-]*)?")
_MAX_TAG_LENGTH = 64
#: Stored step reasons are short lowercase vocabulary words.
_REASON_RE = re.compile(r"[a-z0-9-]{1,40}")
_KEY_RE = re.compile(r"[a-z0-9_]{1,40}")
_RECORD_KEYS = frozenset({"name", "status", "exit", "reason", "detail"})
#: Largest serialized step ``detail`` and longest list inside it.
_MAX_DETAIL_BYTES = 4096
_MAX_DETAIL_LIST = 64
#: Failed pin-bump audits of one tag before the cycle gives up on it.
MAX_AUDIT_ATTEMPTS = 3
_OK_ROE_STATES = ("valid", "expiring-7d", "expiring-1d")
_MAX_LINE_LENGTH = 200
_MAX_DIGEST_LINES = 20
_ENGAGEMENT_MODULE: Any = None


# --------------------------------------------------------------------------
# small helpers


def _word(value: Any, default: str = "unknown") -> str:
    """A short vocabulary-safe word, else ``default`` (never free text)."""
    return value if isinstance(value, str) and _WORD_RE.fullmatch(value) else default


def _safe_tag(value: Any) -> str | None:
    """The tag when it looks like a plain version tag, else ``None``."""
    if (
        isinstance(value, str)
        and len(value) <= _MAX_TAG_LENGTH
        and _TAG_RE.fullmatch(value) is not None
    ):
        return value
    return None


def _clip(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    printable = "".join(ch for ch in text if ch.isprintable())
    return printable.strip()[:_MAX_LINE_LENGTH]


def _count(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _detail_size(detail: Mapping[str, Any]) -> int:
    return len(json.dumps(detail, sort_keys=True).encode("utf-8"))


def _bounded_detail(detail: Mapping[str, Any] | None) -> dict[str, Any]:
    """A copy of ``detail`` whose lists are trimmed to fit the stored limits."""
    out: dict[str, Any] = dict(detail) if detail else {}
    for key, value in out.items():
        if isinstance(value, list) and len(value) > _MAX_DETAIL_LIST:
            out[key] = value[:_MAX_DETAIL_LIST]
    try:
        while _detail_size(out) > _MAX_DETAIL_BYTES:
            lists = [key for key, value in out.items() if isinstance(value, list)]
            lists = [key for key in lists if out[key]]
            if not lists:
                break
            longest = max(lists, key=lambda key: len(out[key]))
            out[longest] = out[longest][:-1]
    except (TypeError, ValueError):
        return out
    return out


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
        "detail": _bounded_detail(detail),
    }


def _utc_now() -> datetime:
    """The cycle's default clock (seam; used only for ``run_cycle``'s ``now``)."""
    return datetime.now(UTC)


def _as_utc(now: datetime) -> datetime:
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
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = previous
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
    checkpoint_dir: Path
    reports_dir: Path
    config: dict[str, Any] | None
    env: Mapping[str, str]
    now: datetime
    runner: Any
    pin_run: Any
    fetch: Callable[[], Any] | None
    ledger: bool
    #: True when the caller injected ``now`` (then it stays authoritative).
    now_injected: bool = True
    #: Name of the step that executed last (attributes checkpoint failures).
    last_step: str | None = None


def _roe_conflict_count(roe_lifecycle: Any, paths: Sequence[Any], now: datetime) -> int:
    """Advisory grant-conflict count; fail-open (0) on any detector failure."""
    try:
        found = roe_lifecycle.grant_conflicts(list(paths), now=now)
    except Exception:
        return 0
    return len(found) if isinstance(found, list) else 0


def _roe_items(config: Mapping[str, Any] | None) -> list[Any]:
    """Union of the RoE grants the apply gate must check.

    ``redteam.roe_file(s)`` plus every grant the findings-sync tick scopes by
    (``roe_artifact``-family keys on the ``findings_sync`` and ``redteam``
    nodes, discovered through ``findings_sync._tick_grant_items``). Path
    items are deduplicated by their stripped text; documents are kept as is.
    """
    if config is None:
        return []
    from omega_prime.config import redteam_roe_files
    from omega_prime.grokbot import findings_sync

    items: list[Any] = list(redteam_roe_files(dict(config)))
    seen = {item for item in items if isinstance(item, str)}
    try:
        node = findings_sync._sync_node(config)
        extra = findings_sync._tick_grant_items(config, node)
    except Exception:
        extra = []
    for item in extra:
        if item is None:
            continue
        if isinstance(item, (str, Path)):
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                items.append(text)
        else:
            items.append(item)
    return items


def _roe_audit_logs(config: Mapping[str, Any] | None) -> list[str]:
    """Configured RoE audit-log paths: ``redteam`` first, then the sync node.

    Scopes, in order: ``redteam``, ``redteam.findings_sync``, top level. Every
    distinct path found is returned (a revocation in any of them counts).
    """
    if config is None:
        return []
    redteam = config.get("redteam")
    scopes: list[Any] = []
    if isinstance(redteam, dict):
        scopes.append(redteam)
        scopes.append(redteam.get("findings_sync"))
    scopes.append(config)
    found: list[str] = []
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        for key in _ROE_AUDIT_LOG_KEYS:
            candidate = scope.get(key)
            if isinstance(candidate, (str, Path)) and str(candidate).strip():
                text = str(candidate).strip()
                if text not in found:
                    found.append(text)
    return found


def _norm_path(text: str) -> str:
    """Realpath of ``text`` relative to the cwd (the text itself on failure)."""
    stripped = text.strip()
    try:
        return os.path.realpath(stripped)
    except (OSError, ValueError):
        return stripped


def _file_id(text: str) -> tuple[int, int] | None:
    """``(st_dev, st_ino)`` of an existing file (hardlink identity), else None."""
    try:
        info = os.stat(text.strip())
    except (OSError, ValueError):
        return None
    return (info.st_dev, info.st_ino)


def _any_roe_revoked(
    roe_lifecycle: Any, items: Sequence[Any], audit_logs: Sequence[str]
) -> bool:
    """True when any grant has a ``grant-revoked`` row in any audit log.

    Paths on BOTH sides are realpath-normalised relative to the cwd and also
    compared by file identity, so ``./g.json``, ``a/../g.json``, symlink and
    hardlink aliases and relative-vs-absolute spellings all match. A grant's
    own ``artifact_id``/``id``/``artifact_path``/``path`` fields count too.
    Read-only; a missing or unreadable log is "not revoked" (advisory input).
    """
    from omega_prime.redteam import roe_audit

    rows: set[str] = set()
    row_ids: set[tuple[int, int]] = set()
    for log in audit_logs:
        for row in roe_audit.list_revoked(log):
            text = row.strip()
            if not text:
                continue
            rows.add(text)
            rows.add(_norm_path(text))
            identity = _file_id(text)
            if identity is not None:
                row_ids.add(identity)
    if not rows:
        return False
    for item in items:
        texts: set[str] = set()
        if isinstance(item, (str, Path)):
            texts.add(str(item))
        document = roe_lifecycle._grant_document(item)
        if document is not None:
            for key in ("artifact_id", "id", "artifact_path", "path"):
                value = document.get(key)
                if isinstance(value, str) and value.strip():
                    texts.add(value)
        for text in texts:
            if text.strip() in rows or _norm_path(text) in rows:
                return True
            identity = _file_id(text)
            if identity is not None and identity in row_ids:
                return True
    return False


def _roe_state(roe_lifecycle: Any, items: Sequence[Any], now: datetime) -> str:
    """Least healthy lifecycle state across the union of grants."""
    paths = [item for item in items if isinstance(item, str)]
    states: list[str] = []
    if paths:
        aggregate = roe_lifecycle.check_roe_lifecycle_all(paths, now, audit_log=None)
        states.append(_word(aggregate.get("state")))
    for item in items:
        if not isinstance(item, str):
            verdict = roe_lifecycle.watchdog_check(item, now=now)
            states.append(_word(verdict.get("state")))
    order = roe_lifecycle.STATES
    worst = max(states, key=lambda s: order.index(s) if s in order else len(order))
    if worst in _OK_ROE_STATES and not all(
        _grant_schema_ok(roe_lifecycle, item) for item in items
    ):
        return "invalid"
    return worst


def _grant_schema_ok(roe_lifecycle: Any, item: Any) -> bool:
    """True when the grant's document passes the real RoE schema validator.

    The lifecycle/watchdog states only classify expiry, so an inline dict such
    as ``{"effective_until": ...}`` would look ``valid``. Fail-closed: a
    missing/unparseable document or any validator failure is not ok.
    """
    from omega_prime.redteam import roe

    try:
        document = roe_lifecycle._grant_document(item)
        return document is not None and not roe.validate_schema(document)
    except Exception:
        return False


def _harness_verdict(ctx: _Ctx) -> tuple[str, str]:
    """Sidecar harness ``(state, cause)`` with the merged config's switches.

    The env verdict stands; additionally ``redteam.kill`` (set) or an explicit
    ``redteam.enabled: false`` in the merged config blocks.
    """
    from omega_prime.config import redteam_enabled, redteam_kill
    from sidecar import harness

    state, cause, _detail = harness.evaluate(ctx.env)
    config = ctx.config
    if config is None:
        return state, cause
    if cause != "kill-switch-set" and redteam_kill(config):
        return "BLOCKED", "kill-switch-set"
    node = config.get("redteam")
    if (
        state == "IDLE"
        and isinstance(node, dict)
        and "enabled" in node
        and not redteam_enabled(config)
    ):
        return "BLOCKED", "disabled"
    return state, cause


def _preflight_facts(ctx: _Ctx, now: datetime) -> dict[str, Any]:
    from omega_prime.grokbot import roe_lifecycle

    harness_state, harness_cause = _harness_verdict(ctx)
    items = _roe_items(ctx.config)
    conflicts: int | None = None
    if items:
        roe_state = _roe_state(roe_lifecycle, items, now)
        if _any_roe_revoked(roe_lifecycle, items, _roe_audit_logs(ctx.config)):
            roe_state = "revoked"
        if len(items) >= 2:
            conflicts = _roe_conflict_count(roe_lifecycle, items, now)
    else:
        roe_state = "none"
    facts: dict[str, Any] = {
        "harness_state": _word(harness_state),
        "harness_cause": _word(harness_cause),
        "roe_state": roe_state,
        "roe_artifacts": len(items),
    }
    if conflicts is not None:
        facts["roe_conflicts"] = conflicts
    if ctx.config is None:
        facts["config_error"] = True
    return facts


def _step_preflight(ctx: _Ctx, state: dict[str, Any]) -> dict[str, Any]:
    facts = _preflight_facts(ctx, ctx.now)
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

    def persist_pending(found: list[dict[str, Any]]) -> None:
        # Runs BEFORE the seen-state is saved: a crash between the two writes
        # can no longer lose the audit of the newest tag.
        tags = [_safe_tag(entry.get("tag")) for entry in found]
        if tags and tags[0] is not None:
            _write_pending(ctx, tags[0], 0)

    result = release_watch.check_release_watch(
        ctx.reports_dir / RELEASE_WATCH_FILENAME,
        fetch,
        ctx.now,
        on_new=persist_pending,
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


def _read_pending(ctx: _Ctx) -> tuple[str, int] | None:
    """The pending ``(tag, failed attempts)`` (advisory; fail-open).

    ``None`` when absent, unusable, or when the tag already used up its
    attempts. A missing/invalid ``attempts`` counts as 0.
    """
    try:
        document = json.loads(
            (ctx.reports_dir / PENDING_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    tag = _safe_tag(document.get("tag"))
    if tag is None:
        return None
    attempts = document.get("attempts", 0)
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
        attempts = 0
    return None if attempts >= MAX_AUDIT_ATTEMPTS else (tag, attempts)


def _write_pending(ctx: _Ctx, tag: str, attempts: int) -> None:
    """Atomically persist ``tag`` as pending; best-effort, never raises."""
    tmp_name: str | None = None
    try:
        ctx.reports_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(ctx.reports_dir), prefix=f".{PENDING_FILENAME}.", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"attempts": attempts, "tag": tag}, sort_keys=True))
        os.replace(tmp_name, ctx.reports_dir / PENDING_FILENAME)
        tmp_name = None
    except Exception:
        if tmp_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)


def _clear_pending_tag(ctx: _Ctx) -> None:
    with contextlib.suppress(OSError):
        (ctx.reports_dir / PENDING_FILENAME).unlink()


def _audit_failure(
    ctx: _Ctx, tag: str, attempts: int, error_type: str
) -> dict[str, Any]:
    """Non-halting record for a failed audit; gives up after the attempt cap."""
    attempts += 1
    detail = {"tag": tag, "error_type": error_type, "attempts": attempts}
    if attempts >= MAX_AUDIT_ATTEMPTS:
        _clear_pending_tag(ctx)
        return _record("pin-bump-audit", "warn", "audit-abandoned", detail)
    _write_pending(ctx, tag, attempts)
    return _record("pin-bump-audit", "warn", "audit-failed", detail)


def _step_pin_bump_audit(ctx: _Ctx, state: dict[str, Any]) -> dict[str, Any]:
    if ctx.mode == "plan":
        return _record("pin-bump-audit", "skipped", "plan-mode")
    watch = _find(state, "release-watch")
    detail = watch["detail"] if watch is not None and watch["status"] == "ok" else {}
    pending = _read_pending(ctx)
    tag: Any
    if detail.get("new_count"):
        tag = detail.get("newest_tag")
        if not isinstance(tag, str) or _safe_tag(tag) is None:
            return _record("pin-bump-audit", "warn", "unsafe-tag", {"audited": False})
        attempts = pending[1] if pending is not None and pending[0] == tag else 0
    else:
        # A tag whose audit failed earlier is audited again even though
        # release-watch has already marked it seen.
        if pending is None:
            return _record("pin-bump-audit", "skipped", "no-new-release")
        tag, attempts = pending
    from omega_prime.grokbot import pin_bump

    tracker = _TimeoutTracker(
        ctx.pin_run if ctx.pin_run is not None else _default_pin_run
    )
    root = ctx.root if ctx.root is not None else pin_bump.REPO_ROOT
    try:
        result = pin_bump.audit_scheduled_job({"to": tag}, root=root, run=tracker)
    except Exception as exc:
        return _audit_failure(ctx, tag, attempts, _word(type(exc).__name__))
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
        return _audit_failure(
            ctx, tag, attempts, "timeout" if tracker.timed_out else "audit-error"
        )
    _clear_pending_tag(ctx)
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
    if roe_state == "none":
        return "roe-missing"
    if roe_state not in _OK_ROE_STATES:
        return _word(roe_state)
    return None


def _step_findings_dispatch(ctx: _Ctx, state: dict[str, Any]) -> dict[str, Any]:
    if ctx.mode != "apply":
        reason = "plan-mode" if ctx.mode == "plan" else "not-selected"
        return _record("findings-dispatch", "skipped", reason)
    # Evaluated FRESH on every pass (harness verdict and RoE lifecycle at the
    # current time): the checkpointed preflight is for reporting only. A
    # caller-injected ``now`` stays authoritative; otherwise the wall clock is
    # re-read so a long run never gates on a stale start-of-run time.
    now = ctx.now if ctx.now_injected else _as_utc(_utc_now())
    cause = _refusal_cause(_preflight_facts(ctx, now))
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
        now=now,
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


def _ledger_has(path: Path, row: Mapping[str, Any]) -> bool:
    """True when the ledger tail already holds ``row`` (ignoring ``ts``)."""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 65536))
            tail = handle.read()
    except OSError:
        return False
    for line in tail.splitlines():
        try:
            old = json.loads(line)
        except ValueError:
            continue
        if isinstance(old, dict) and all(
            old.get(key) == value for key, value in row.items() if key != "ts"
        ):
            return True
    return False


def _append_ledger(ctx: _Ctx, rec: Mapping[str, Any]) -> None:
    """Append one secret-free ledger row. Best-effort; never raises.

    A ``blocked`` row carries its cause (only when it is a short vocabulary
    word) as ``reason:cause`` and is written ONCE per identical row: rerunning
    a refused cycle does not append a row per pass.
    """
    try:
        path = ctx.reports_dir / LEDGER_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        reason = rec["reason"]
        if rec["status"] == "blocked":
            cause = rec["detail"].get("cause")
            if isinstance(cause, str) and _REASON_RE.fullmatch(cause):
                reason = f"{reason}:{cause}"
        row = {
            "ts": ctx.now.isoformat(timespec="seconds"),
            "cycle_id": ctx.cycle_id,
            "step": rec["name"],
            "status": rec["status"],
            "exit": rec["exit"],
            "reason": reason,
        }
        if rec["status"] == "blocked" and _ledger_has(path, row):
            return None
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:
        return None
    return None


class _StepFailure(Exception):
    """A step errored or was blocked: carries the pre-step state and its record.

    Neither outcome is checkpointed, so a rerun re-evaluates the step.
    """

    def __init__(self, state: dict[str, Any], rec: dict[str, Any]) -> None:
        super().__init__(rec["name"])
        self.state = state
        self.rec = rec


def _make_step(ctx: _Ctx, name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def step(state: dict[str, Any]) -> dict[str, Any]:
        ctx.last_step = name
        records = list(state.get("steps", []))
        rec = _execute(ctx, name, state)
        if ctx.ledger and rec["reason"] != "not-selected":
            _append_ledger(ctx, rec)
        if rec["status"] in ("error", "blocked"):
            raise _StepFailure(dict(state), rec)
        new_state = dict(state)
        new_state["steps"] = [*records, rec]
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
    blocking = next((rec for rec in records if rec["status"] == "blocked"), None)
    if blocking is not None:
        blocked = {
            "cause": _word(blocking["detail"].get("cause")),
            "step": blocking["name"],
        }
    if any(rec["status"] == "error" for rec in records):
        outcome, code = "failed", EXIT_STEP_ERROR
    elif blocked:
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


def _pending_error(
    state: Mapping[str, Any], reason: str, step: str | None = None
) -> dict[str, Any]:
    """An ``error`` record for ``step`` (default: the first not-yet-done step)."""
    if step in STEP_NAMES:
        return _record(str(step), "error", reason)
    done = {rec.get("name") for rec in state.get("steps", []) if isinstance(rec, dict)}
    pending = next((name for name in STEP_NAMES if name not in done), STEP_NAMES[-1])
    return _record(pending, "error", reason)


def _json_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    return (
        isinstance(value, str)
        and len(value) <= _MAX_LINE_LENGTH
        and value.isprintable()
    )


def _valid_detail(detail: Any) -> bool:
    """A stored ``detail``: flat keys, JSON scalars or short scalar lists, <= 4 KiB."""
    if not isinstance(detail, dict):
        return False
    for key, value in detail.items():
        if not isinstance(key, str) or _KEY_RE.fullmatch(key) is None:
            return False
        if isinstance(value, list):
            if len(value) > _MAX_DETAIL_LIST or not all(map(_json_scalar, value)):
                return False
        elif not _json_scalar(value):
            return False
    try:
        return _detail_size(detail) <= _MAX_DETAIL_BYTES
    except (TypeError, ValueError):
        return False


def _valid_record(rec: Any, name: str) -> bool:
    """A stored step record has the exact shape ``_record`` produces.

    Every field's type is checked before any membership test, so a forged
    record can never raise; ``reason`` and ``detail`` are bounded too.
    """
    if not isinstance(rec, dict) or set(rec) != _RECORD_KEYS or rec["name"] != name:
        return False
    status, code, reason = rec["status"], rec["exit"], rec["reason"]
    return (
        isinstance(status, str)
        and status in _STATUS_EXIT
        and isinstance(code, int)
        and not isinstance(code, bool)
        and code == _STATUS_EXIT[status]
        and isinstance(reason, str)
        and _REASON_RE.fullmatch(reason) is not None
        and _valid_detail(rec["detail"])
    )


def _valid_blocked(blocked: Any) -> bool:
    if blocked is None:
        return True
    if not isinstance(blocked, dict) or set(blocked) != {"cause", "step"}:
        return False
    cause, step = blocked["cause"], blocked["step"]
    return (
        isinstance(cause, str)
        and _WORD_RE.fullmatch(cause) is not None
        and isinstance(step, str)
        and step in STEP_NAMES
    )


def _validated_prior(
    checkpoint: Mapping[str, Any], expected: Mapping[str, Any]
) -> dict[str, Any]:
    """The checkpointed state, or ``ValueError`` when it is not this cycle's.

    ``expected`` is the current run's initial state: the stored ``mode``,
    ``cycle_id`` and ``selected`` must match it, and every step record (and
    the ``blocked`` marker) must be well formed and in canonical order.
    """
    state = checkpoint.get("state")
    done = checkpoint.get("done")
    steps = state.get("steps") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict)
        or not isinstance(done, int)
        or isinstance(done, bool)
        or not isinstance(steps, list)
        or not 0 <= done <= len(STEP_NAMES)
        or len(steps) != done
        or any(not _valid_record(rec, STEP_NAMES[i]) for i, rec in enumerate(steps))
        or not _valid_blocked(state.get("blocked"))
        or state.get("mode") != expected["mode"]
        or state.get("cycle_id") != expected["cycle_id"]
        or state.get("selected") != expected["selected"]
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
    ``now`` defaults to the current UTC time (via :func:`_utc_now`) here and
    nowhere else.
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
    current = _as_utc(now if now is not None else _utc_now())
    environ: Mapping[str, str] = env if env is not None else os.environ
    cycle_id = f"purple-cycle-{current:%Y%m%d}-{mode}"
    if selected != list(STEP_NAMES):
        digest = hashlib.sha256(",".join(selected).encode("ascii")).hexdigest()
        cycle_id += f"-s{digest[:8]}"
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
    reports = (
        Path(reports_dir) if reports_dir is not None else _resolve_reports_dir(environ)
    )
    ctx = _Ctx(
        mode=mode,
        cycle_id=cycle_id,
        root=Path(root) if root is not None else None,
        checkpoint_dir=reports / CHECKPOINT_DIRNAME,
        reports_dir=reports,
        config=resolved_config,
        env=environ,
        now=current,
        runner=runner,
        pin_run=pin_run,
        fetch=fetch,
        ledger=mode != "plan",
        now_injected=now is not None,
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
    checkpoint_path = str(ctx.checkpoint_dir / "workflows" / f"{ctx.cycle_id}.json")
    ledger_path = str(ctx.reports_dir / LEDGER_FILENAME)
    prior_state: dict[str, Any] | None = None
    try:
        document = workflows.load_checkpoint(ctx.checkpoint_dir, ctx.cycle_id)
        if document is not None:
            prior_state = _validated_prior(document, initial)
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
            ctx.checkpoint_dir, ctx.cycle_id, funcs, state=initial
        )
        final: dict[str, Any] = outcome["state"]
    except _StepFailure as exc:
        final, failed = exc.state, exc.rec
    except (OSError, ValueError):
        final = prior_state if prior_state is not None else initial
        failed = _pending_error(final, "checkpoint-error", ctx.last_step)
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
        allow_abbrev=False,
        description=(
            "Resumable purple cycle: preflight, release-watch, pin-bump audit, "
            "engagement, findings plan and (apply only) findings dispatch. "
            "Exit 0 completed, 1 step error, 2 usage error, 3 apply refused. "
            "--now is rejected in apply mode (the RoE expiry check must use "
            "the real clock)."
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
    parser.add_argument("--root", default=None, help="pin-bump repository root")
    parser.add_argument(
        "--now", default=None, help="ISO-8601 UTC instant (not allowed with apply)"
    )
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
        if mode == "apply":
            return _usage_error("--now is not allowed in apply mode")
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
