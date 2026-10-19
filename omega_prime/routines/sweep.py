"""Engagement sweep: fresh mentions in, queued reply drafts out.

Each mention checkpoints as it is drafted, so an interrupted sweep resumes
without duplicating drafts. The composer is injected — tests use a stub,
production wires a model turn. The sweep never publishes: drafts queue for
a human or an approved job.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from omega_prime.routines import state_dir

_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def sweep_mentions(
    client: Any,
    composer: Callable[[dict], str],
    *,
    directory: str | Path,
    sweep_id: str,
    user_id: str | None = None,
    max_results: int = 10,
) -> dict:
    """Draft one reply per fresh mention. Return this run's drafts."""
    if not isinstance(sweep_id, str) or _ID.match(sweep_id) is None:
        raise ValueError(f"invalid sweep id: {sweep_id!r}")
    if not callable(composer):
        raise ValueError("composer must be callable")
    state = _load(directory, sweep_id)
    drafted = set(state["drafted"])
    skipped = set(state["skipped"])
    fresh_drafts: list[dict] = []
    fresh_skipped: list[str] = []
    mentions = client.mentions(user_id=user_id, max_results=max_results)
    for mention in mentions.get("posts", []):
        if not isinstance(mention, dict):
            continue
        mention_id = mention.get("id")
        if not isinstance(mention_id, str) or mention_id == "":
            continue
        if mention_id in drafted or mention_id in skipped:
            continue
        text = composer(mention)
        if not isinstance(text, str) or text.strip() == "":
            skipped.add(mention_id)
            fresh_skipped.append(mention_id)
        else:
            draft = {"mention_id": mention_id, "reply_to": mention_id, "text": text}
            state["drafts"].append(draft)
            drafted.add(mention_id)
            fresh_drafts.append(draft)
        state["drafted"] = sorted(drafted)
        state["skipped"] = sorted(skipped)
        _write(directory, sweep_id, state)
    return {
        "sweep_id": sweep_id,
        "drafts": fresh_drafts,
        "skipped": fresh_skipped,
        "total": len(state["drafts"]),
    }


def load_sweep(directory: str | Path, sweep_id: str) -> dict | None:
    """The sweep state, or None when the sweep never ran."""
    if not isinstance(sweep_id, str) or _ID.match(sweep_id) is None:
        raise ValueError(f"invalid sweep id: {sweep_id!r}")
    target = _path(directory, sweep_id)
    if not target.is_file():
        return None
    return _load(directory, sweep_id)


def _load(directory: str | Path, sweep_id: str) -> dict[str, Any]:
    target = _path(directory, sweep_id)
    if not target.is_file():
        return {"drafted": [], "drafts": [], "skipped": []}
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {target}: {exc}") from exc
    if (
        not isinstance(document, dict)
        or not isinstance(document.get("drafted"), list)
        or not isinstance(document.get("drafts"), list)
        or not isinstance(document.get("skipped"), list)
    ):
        raise ValueError(f"sweep state {target} has an unsupported shape")
    return document


def _path(directory: str | Path, sweep_id: str) -> Path:
    return Path(directory) / "sweeps" / f"{sweep_id}.json"


def _write(directory: str | Path, sweep_id: str, state: dict) -> None:
    target = _path(directory, sweep_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(state, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{sweep_id}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


class _MentionsOnly:
    """A mentions reader with no publish method. Sweep must not post."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def mentions(
        self, user_id: str | None = None, max_results: int = 10
    ) -> dict[str, Any]:
        return self._inner.mentions(user_id=user_id, max_results=max_results)


class _FileMentions:
    def __init__(self, posts: list[dict]) -> None:
        self._posts = posts

    def mentions(
        self, user_id: str | None = None, max_results: int = 10
    ) -> dict[str, Any]:
        del user_id
        return {"posts": list(self._posts)[:max_results]}


class _ReadOnlyTransport:
    """GET-only X transport. There is no POST, so a sweep cannot publish."""

    @staticmethod
    def get(url: str, headers: dict, params: dict) -> dict:
        import urllib.parse
        import urllib.request

        query = urllib.parse.urlencode(
            {key: value for key, value in params.items() if value is not None}
        )
        target = f"{url}?{query}" if query else url
        request = urllib.request.Request(target, headers=dict(headers), method="GET")
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("X response is not an object")
        return payload


def draft_composer(mention: dict) -> str:
    """Queue a human-editable draft. Never a publishable post by itself."""
    text = mention.get("text") if isinstance(mention, dict) else None
    if not isinstance(text, str) or not text.strip():
        return ""
    body = " ".join(text.split())
    return f"[draft-only] {body}"[:500]


def _refuse_publisher(client: Any) -> None:
    publishes = callable(getattr(client, "post", None)) or callable(
        getattr(client, "thread", None)
    )
    if publishes:
        raise RuntimeError("sweep refuses a client that can publish")


def _posts_from_file(path: Path) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    posts = document.get("posts", []) if isinstance(document, dict) else document
    if not isinstance(posts, list):
        raise ValueError("mentions file must be a list or an object with posts")
    return [row for row in posts if isinstance(row, dict)]


def _mentions_client(
    env: Mapping[str, str], mentions_path: str | Path | None
) -> Any | None:
    if mentions_path:
        return _FileMentions(_posts_from_file(Path(mentions_path)))
    configured = env.get("OMEGA_PRIME_SWEEP_MENTIONS", "").strip()
    if configured:
        return _FileMentions(_posts_from_file(Path(configured)))
    token = env.get("X_API_TOKEN", "").strip()
    if not token:
        return None
    from omega_prime.tools.x import XClient

    return _MentionsOnly(XClient(_ReadOnlyTransport(), token=token))


def execute_sweep(
    env: Mapping[str, str] | None = None,
    *,
    mentions_path: str | Path | None = None,
    directory: str | Path | None = None,
    sweep_id: str = "weekday",
    user_id: str | None = None,
    max_results: int = 10,
    client: Any | None = None,
) -> dict[str, Any]:
    """Draft replies for fresh mentions. This function never publishes."""
    mapping: Mapping[str, str] = os.environ if env is None else env
    root = Path(directory) if directory is not None else state_dir(env)
    if root is None:
        return {
            "error": "not_configured",
            "reason": (
                "Set OMEGA_PRIME_STATE_DIR (or HOME) before a sweep. "
                "Sweep queues drafts and never publishes."
            ),
            "drafts": [],
            "publishes": False,
        }
    try:
        reader = (
            client if client is not None else _mentions_client(mapping, mentions_path)
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return {
            "error": "invalid_mentions",
            "reason": str(exc),
            "drafts": [],
            "publishes": False,
        }
    if reader is None:
        return {
            "error": "not_configured",
            "reason": (
                "Set X_API_TOKEN or OMEGA_PRIME_SWEEP_MENTIONS. "
                "Sweep queues drafts and never publishes."
            ),
            "drafts": [],
            "publishes": False,
        }
    try:
        _refuse_publisher(reader)
    except RuntimeError as exc:
        return {
            "error": "publish_refused",
            "reason": str(exc),
            "drafts": [],
            "publishes": False,
        }
    try:
        result = sweep_mentions(
            reader,
            draft_composer,
            directory=root,
            sweep_id=sweep_id,
            user_id=user_id,
            max_results=max_results,
        )
    except ValueError as exc:
        return {
            "error": "sweep_failed",
            "reason": str(exc),
            "drafts": [],
            "publishes": False,
        }
    result["publishes"] = False
    return result


def main(argv: list[str] | None = None) -> int:
    """``python -m omega_prime.routines.sweep``. Drafts only; never publishes."""
    parser = argparse.ArgumentParser(prog="python -m omega_prime.routines.sweep")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--sweep-id", default="weekday")
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument(
        "--mentions",
        default=None,
        help="JSON mentions file. Without it, X_API_TOKEN is read and nothing is posted.",
    )
    args = parser.parse_args(argv)
    result = execute_sweep(
        os.environ,
        mentions_path=args.mentions,
        directory=args.state_dir,
        sweep_id=args.sweep_id,
        user_id=args.user_id,
        max_results=args.max_results,
    )
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "draft_composer",
    "execute_sweep",
    "load_sweep",
    "main",
    "sweep_mentions",
]
