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


def _write_state(
    state_path: str | Path, pairs: list[dict[str, str]], checked_at: str
) -> None:
    """Atomically write the state file (tmp + rename)."""
    path = Path(state_path)
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_seen": [{"tag": p["tag"], "sha": p["sha"]} for p in pairs],
        "checked_at": checked_at,
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def check_release_watch(
    state_path: str | Path,
    fetch: Callable[[], Any],
    now: datetime | int | float | None = None,
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
) -> Callable[[Any], dict[str, Any]]:
    """Build a ``checker(now)`` closure for the heartbeat tick driver.

    The returned checker never raises: fetch failures degrade to
    ``{"new": [], "seeded": False, "error": str, "checked_at": None}``
    so the tick records the failure in job history with state untouched
    (signal, not red). Only ticks with a non-empty ``new`` list fire.
    """

    def _default_fetch() -> Any:
        return fetch_upstream_tags(repo)

    active = fetch if fetch is not None else _default_fetch

    def checker(now: Any) -> dict[str, Any]:
        try:
            return check_release_watch(state_path, active, now)
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
    ``subprocess.CalledProcessError`` on non-zero ``gh`` exit. Never
    prints tokens; ``gh`` stderr passes through uncensored.
    """
    if not isinstance(repo, str) or not repo.strip():
        raise RuntimeError("repo must be a non-empty OWNER/NAME (fails closed)")
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo.strip()}/tags"],
        capture_output=True,
        text=True,
        check=False,
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
        description="Upstream release watcher — digest new tags, never bump"
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
        except (subprocess.CalledProcessError, RuntimeError, OSError) as exc:
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
    "DEFAULT_REPO",
    "DEFAULT_STATE",
    "FETCH_FAILURE_EXIT",
    "build_digest",
    "check_release_watch",
    "fetch_upstream_tags",
    "main",
    "make_checker",
    "read_releases_block",
]
