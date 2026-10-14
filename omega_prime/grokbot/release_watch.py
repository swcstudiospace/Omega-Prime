# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Upstream release watcher digest (PLAN-G7).

Watches upstream tags (``gh api repos/<owner>/<name>/tags``) and digests
what is new since the last tick. Self-contained: the pure core
:func:`check_release_watch` takes an injected ``fetch`` callable so tests
run hermetically with a fake fetcher (zero network); only :func:`main`
spawns the real ``gh`` subprocess.

State file schema (sibling of ``sidecar/reports/last-run.json``)::

    {"last_seen": [{"tag": str, "sha": str}], "checked_at": "<ISO-8601 UTC>"}

First run (state absent — or corrupt, which is treated as absent) seeds
silently: writes current tags, fires nothing, exits 0. Seeding is not a
finding. Fetch failures never touch the state file; the exception
propagates so the heartbeat tick can record it in job history (signal,
not red) and the CLI can exit 3.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Default upstream repo watched (config key
#: ``OMEGA_PRIME_REDTEAM_RELEASE_WATCH_REPO`` overrides this).
DEFAULT_REPO = "swcstudiospace/purple-team"

#: Default state-file location (config key
#: ``OMEGA_PRIME_REDTEAM_RELEASE_WATCH_STATE`` overrides this).
DEFAULT_STATE = "sidecar/reports/release-watch.json"

#: CLI exit when the ``gh`` fetch fails (mirrors ``roe_lifecycle`` BLOCKED).
FETCH_FAILURE_EXIT = 3

#: Timeout (seconds) for the ``gh api`` subprocess in :func:`fetch_upstream_tags`.
GH_TIMEOUT_SECONDS = 60

#: Config key gating release-watch → pin-bump chaining (explicit opt-in).
AUTO_PIN_BUMP_CONFIG_KEY = "auto_pin_bump"

#: Sentinel for :func:`_write_state`: carry forward any recorded pin-bump
#: outcome instead of overwriting it.
_PRESERVE_BUMP: Any = object()


def _as_utc(now: datetime | int | float | None = None) -> datetime:
    """Normalize ``now`` to an aware UTC datetime (hermetic clock)."""
    if now is None:
        return datetime.now(UTC)
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=UTC)
        return now.astimezone(UTC)
    return datetime.fromtimestamp(now, tz=UTC)


def _normalize_entries(raw: Any) -> list[dict[str, str]]:
    """Normalize one ``gh api .../tags`` listing to ``tag/sha/date`` dicts.

    Accepts the real ``gh`` shape (``{"name", "commit": {"sha"}}``), the
    already-normalized shape (``{"tag", "sha", "date"}``), and bare tag
    strings (sha/date default to ``""``). Unknown shapes are skipped;
    ``None``/dict input yields ``[]`` rather than raising.
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    entries: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            if item.strip():
                entries.append({"tag": item.strip(), "sha": "", "date": ""})
            continue
        if not isinstance(item, dict):
            continue
        tag = item.get("tag", item.get("name", ""))
        if not isinstance(tag, str) or not tag.strip():
            continue
        tag = tag.strip()
        sha = item.get("sha", "")
        commit = item.get("commit")
        if not sha and isinstance(commit, dict):
            sha = commit.get("sha", "")
        date = item.get("date", "")
        if not date and isinstance(commit, dict):
            date = commit.get("date", "")
        entries.append(
            {
                "tag": tag,
                "sha": sha if isinstance(sha, str) else "",
                "date": date if isinstance(date, str) else "",
            }
        )
    return entries


def _read_state(state_path: str | Path) -> list[dict[str, str]] | None:
    """Read stored ``last_seen`` pairs, or ``None`` when seeding is needed.

    Missing file, corrupt JSON, and wrong-schema contents all return
    ``None`` (rewrite path) — never raise.
    """
    try:
        raw = Path(state_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    seen = payload.get("last_seen")
    if not isinstance(seen, list):
        return None
    pairs: list[dict[str, str]] = []
    for item in seen:
        if (
            isinstance(item, dict)
            and isinstance(item.get("tag"), str)
            and isinstance(item.get("sha"), str)
        ):
            pairs.append({"tag": item["tag"], "sha": item["sha"]})
    if len(pairs) != len(seen):
        return None
    return pairs


def _read_bump_record(state_path: str | Path) -> dict[str, Any] | None:
    """Best-effort read of the recorded pin-bump outcome, else ``None``.

    Missing file, corrupt JSON, and wrong-schema contents all return
    ``None`` — never raise.
    """
    try:
        payload = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    record = payload.get("last_pin_bump")
    return dict(record) if isinstance(record, dict) else None


def _write_state(
    state_path: str | Path,
    pairs: list[dict[str, str]],
    checked_at: str,
    last_pin_bump: Any = _PRESERVE_BUMP,
) -> None:
    """Atomically write the state file (tmp + rename).

    A recorded pin-bump outcome (``last_pin_bump``) survives ordinary
    ticks: pass an explicit record to replace it, ``None`` to clear it,
    or leave the default to carry the stored record forward.
    """
    path = Path(state_path)
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)
    if last_pin_bump is _PRESERVE_BUMP:
        last_pin_bump = _read_bump_record(path)
    payload: dict[str, Any] = {
        "last_seen": [{"tag": p["tag"], "sha": p["sha"]} for p in pairs],
        "checked_at": checked_at,
    }
    if last_pin_bump is not None:
        payload["last_pin_bump"] = last_pin_bump
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def auto_pin_bump_enabled(config: Any = None) -> bool:
    """Whether release-watch may chain into pin-bump. Explicit opt-in.

    Only ``config["redteam"]["auto_pin_bump"] is True`` enables it;
    anything else (absent node, falsy value, non-dict config) is
    ``False``. Operators wire it as
    ``check_release_watch(..., auto_pin_bump=auto_pin_bump_enabled(cfg))``.
    """
    if not isinstance(config, dict):
        return False
    node = config.get("redteam")
    return bool(isinstance(node, dict) and node.get(AUTO_PIN_BUMP_CONFIG_KEY) is True)


def _default_bump_runner(root: str | Path | None) -> Callable[[str], int]:
    """Build the production pin-bump runner (lazy import, no load cycle)."""
    from omega_prime.grokbot.pin_bump import REPO_ROOT
    from omega_prime.grokbot.pin_bump import bump as _bump

    base = Path(root) if root is not None else REPO_ROOT

    def _run(tag: str) -> int:
        return int(_bump(tag, root=base))

    return _run


def _bump_report_path(root: str | Path | None) -> str | None:
    """Delta-report path ``pin_bump`` writes on the audit path, if present."""
    if root is not None:
        candidate = Path(root) / "report.md"
        return str(candidate) if candidate.is_file() else None
    try:
        from omega_prime.grokbot import pin_bump
    except ImportError:
        return None
    candidate = pin_bump.REPO_ROOT / "report.md"
    return str(candidate) if candidate.is_file() else None


def check_release_watch(
    state_path: str | Path,
    fetch: Callable[[], Any],
    now: datetime | int | float | None = None,
    *,
    auto_pin_bump: bool = False,
    bump: Callable[[str], int] | None = None,
    root: str | Path | None = None,
    on_new: Callable[[list[dict]], None] | None = None,
) -> dict[str, Any]:
    """Diff upstream tags against state; record new releases.

    ``fetch()`` returns the parsed ``gh api .../tags`` list (injected;
    the subprocess call lives in :func:`main`, never here). Returns::

        {"new": [{"tag", "sha", "date"}], "seeded": bool,
         "checked_at": "<ISO-8601 UTC>"}

    Missing/corrupt state seeds silently (writes current tags, ``new``
    empty, ``seeded`` True). No new tags is silent bookkeeping: stored
    SHAs refresh when the upstream listing changed shape and
    ``checked_at`` always refreshes. Fetch exceptions propagate with
    the state file untouched — callers degrade (heartbeat history /
    CLI exit 3), never delete state.

    Opt-in chaining (batch-03 Feature F): with ``auto_pin_bump=True``
    and a non-empty ``new`` list, the newest tag is passed to ``bump``
    (default: :func:`omega_prime.grokbot.pin_bump.bump` against
    ``root``). Exit 0 records ``last_pin_bump`` in the state file and
    the result carries ``"bump": {"tag", "exit": 0, "recorded": True}``.
    Any other exit (notably 1, audit regression) logs the delta-report
    path to stderr, leaves the pin and the bump record untouched, and
    carries ``"bump": {"tag", "exit", "report", "recorded": False}``.
    Bump-runner exceptions propagate with the state file untouched.
    Seeding ticks never bump. Default (``False``) is inert: the result
    shape is exactly the historical three-key dict.

    ``on_new`` (default ``None``: no effect) is called with the new-tag
    list when new tags were found on a non-seeding tick, AFTER they are
    computed and BEFORE any state write (and before an opt-in bump), so a
    caller can persist follow-up work first and a crash between the two
    writes cannot lose it. A raising callback aborts the tick with the
    state file untouched and propagates like a fetch error.
    """
    current = _as_utc(now)
    checked_at = current.isoformat(timespec="seconds")
    upstream = _normalize_entries(fetch())
    stored = _read_state(state_path)
    if stored is None:
        pairs = [{"tag": e["tag"], "sha": e["sha"]} for e in upstream]
        _write_state(state_path, pairs, checked_at)
        return {"new": [], "seeded": True, "checked_at": checked_at}
    known = {p["tag"]: p["sha"] for p in stored}
    new = [e for e in upstream if e["tag"] not in known]
    merged = list(stored)
    upstream_sha = {e["tag"]: e["sha"] for e in upstream}
    for index, pair in enumerate(merged):
        latest = upstream_sha.get(pair["tag"])
        if latest is not None and latest != pair["sha"]:
            merged[index] = {"tag": pair["tag"], "sha": latest}
    for entry in new:
        merged.append({"tag": entry["tag"], "sha": entry["sha"]})
    if new and on_new is not None:
        on_new(list(new))
    if auto_pin_bump and new:
        target = new[0]
        runner = bump if bump is not None else _default_bump_runner(root)
        exit_code = runner(target["tag"])
        if exit_code == 0:
            record = {
                "tag": target["tag"],
                "checked_at": checked_at,
                "exit": 0,
            }
            _write_state(state_path, merged, checked_at, last_pin_bump=record)
            return {
                "new": new,
                "seeded": False,
                "checked_at": checked_at,
                "bump": {"tag": target["tag"], "exit": 0, "recorded": True},
            }
        report = _bump_report_path(root)
        print(
            f"release-watch auto-pin-bump for {target['tag']} exited "
            f"{exit_code} (report: {report}); pin left unchanged",
            file=sys.stderr,
        )
        _write_state(state_path, merged, checked_at)
        return {
            "new": new,
            "seeded": False,
            "checked_at": checked_at,
            "bump": {
                "tag": target["tag"],
                "exit": exit_code,
                "report": report,
                "recorded": False,
            },
        }
    _write_state(state_path, merged, checked_at)
    return {"new": new, "seeded": False, "checked_at": checked_at}


def build_digest(result: dict[str, Any]) -> dict[str, Any]:
    """Build the ``durable_status`` ``releases`` block from a tick result.

    Additive shape: ``{"releases": {"new": [...], "checked_at": str |
    None}}``. Fetch-failure results (``error`` set, no ``checked_at``)
    yield ``{"new": [], "checked_at": None}``.
    """
    new = result.get("new")
    return {
        "releases": {
            "new": list(new) if isinstance(new, list) else [],
            "checked_at": result.get("checked_at"),
        }
    }


def read_releases_block(root: str | Path, state_rel: str = DEFAULT_STATE) -> dict:
    """Read the ``releases`` block from the state file under ``root``.

    State absent/corrupt ⇒ ``{"new": [], "checked_at": None}``. Existing
    ``durable_status`` keys are untouched by this additive block.
    """
    path = Path(root) / state_rel
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        seen = payload.get("last_seen")
        checked_at = payload.get("checked_at")
        if not isinstance(seen, list):
            raise ValueError("bad last_seen")
        return {
            "new": [],
            "checked_at": checked_at if isinstance(checked_at, str) else None,
        }
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return {"new": [], "checked_at": None}


def make_checker(
    state_path: str | Path,
    fetch: Callable[[], Any] | None = None,
    repo: str = DEFAULT_REPO,
    *,
    auto_pin_bump: bool = False,
    bump: Callable[[str], int] | None = None,
    root: str | Path | None = None,
    on_new: Callable[[list[dict]], None] | None = None,
) -> Callable[[Any], dict[str, Any]]:
    """Build a ``checker(now)`` closure for the heartbeat tick driver.

    The returned checker never raises: fetch failures degrade to
    ``{"new": [], "seeded": False, "error": str, "checked_at": None}``
    so the tick records the failure in job history with state untouched
    (signal, not red). Only ticks with a non-empty ``new`` list fire.
    ``auto_pin_bump``/``bump``/``root`` pass through to
    :func:`check_release_watch` (explicit opt-in; default inert), as does
    ``on_new`` (a raising callback degrades like a fetch failure).
    """

    def _default_fetch() -> Any:
        return fetch_upstream_tags(repo)

    active = fetch if fetch is not None else _default_fetch

    def checker(now: Any) -> dict[str, Any]:
        try:
            return check_release_watch(
                state_path,
                active,
                now,
                auto_pin_bump=auto_pin_bump,
                bump=bump,
                root=root,
                on_new=on_new,
            )
        except Exception as exc:
            return {
                "new": [],
                "seeded": False,
                "error": f"{type(exc).__name__}: {exc}",
                "checked_at": None,
            }

    return checker


def fetch_upstream_tags(repo: str) -> list[dict]:
    """Run ``gh api repos/<repo>/tags`` and return the parsed JSON list.

    Raises ``RuntimeError`` on empty repo (fail closed),
    ``FileNotFoundError`` when ``gh`` is absent, and
    ``subprocess.CalledProcessError`` on non-zero ``gh`` exit, and
    ``subprocess.TimeoutExpired`` when ``gh`` exceeds ``GH_TIMEOUT_SECONDS``.
    Never prints tokens; ``gh`` stderr passes through uncensored.
    """
    if not isinstance(repo, str) or not repo.strip():
        raise RuntimeError("repo must be a non-empty OWNER/NAME (fails closed)")
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo.strip()}/tags"],
        capture_output=True,
        text=True,
        check=False,
        timeout=GH_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise subprocess.CalledProcessError(
            proc.returncode, proc.args, output=proc.stdout, stderr=proc.stderr
        )
    try:
        payload = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh api returned unparseable JSON: {exc}") from exc
    return payload if isinstance(payload, list) else []


def main(argv: list[str] | None = None) -> int:
    """CLI: ``check --state <path> [--repo OWNER/NAME] [--json]``."""
    parser = argparse.ArgumentParser(
        description="Upstream release watcher — digest new tags "
        "(pin-bump only via the explicit API opt-in, never the CLI)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Digest tags new since last tick")
    check.add_argument(
        "--state",
        required=True,
        help="Path to the release-watch state file",
    )
    check.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"Upstream OWNER/NAME (default {DEFAULT_REPO}; empty fails closed)",
    )
    check.add_argument(
        "--json", action="store_true", help="Output machine-readable JSON"
    )
    args = parser.parse_args(argv)

    if args.command == "check":
        if not args.repo or not args.repo.strip():
            print("error: --repo must be a non-empty OWNER/NAME", file=sys.stderr)
            return 2
        try:
            result = check_release_watch(
                args.state, lambda: fetch_upstream_tags(args.repo), None
            )
        except FileNotFoundError:
            print("error: gh not found (GitHub CLI absent)", file=sys.stderr)
            return FETCH_FAILURE_EXIT
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            RuntimeError,
            OSError,
        ) as exc:
            print(f"error: release-watch fetch failed: {exc}", file=sys.stderr)
            return FETCH_FAILURE_EXIT
        if args.json:
            print(json.dumps({"new": result["new"], "seeded": result["seeded"]}))
        else:
            if result["seeded"]:
                print(f"seeded release-watch state at {args.state}")
            for entry in result["new"]:
                print(f"{entry['tag']} {entry['date']} {entry['sha']}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "AUTO_PIN_BUMP_CONFIG_KEY",
    "DEFAULT_REPO",
    "DEFAULT_STATE",
    "FETCH_FAILURE_EXIT",
    "GH_TIMEOUT_SECONDS",
    "auto_pin_bump_enabled",
    "build_digest",
    "check_release_watch",
    "fetch_upstream_tags",
    "main",
    "make_checker",
    "read_releases_block",
]
