# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``omega watch <job>``: tail one job's event feed."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from omega_prime.engine.jobs import JobPathError, job_dir
from omega_prime.engine.protocol import assistant_text, usage_of


def format_event(event: dict) -> str:
    kind = str(event.get("type") or "event")
    if kind == "message_update":
        text = assistant_text(event)
        return f"message_update {text}" if text else "message_update"
    if kind == "message_end":
        usage, cost = usage_of(event)
        bits = ["message_end"]
        if usage:
            bits.append(
                f"usage={json.dumps(usage, ensure_ascii=False, separators=(',', ':'))}"
            )
        if cost is not None:
            bits.append(f"cost={cost}")
        return " ".join(bits)
    if kind.startswith("tool_execution"):
        name = event.get("toolName") or event.get("tool") or ""
        return f"{kind} {name}".rstrip()
    if kind in {"turn_start", "turn_end", "agent_start", "agent_end", "ready"}:
        return kind
    return kind


def iter_events(path: Path):
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload


def tail(
    path: Path,
    *,
    follow: bool = False,
    timeout: float | None = None,
    write=sys.stdout,
) -> int:
    """Print the feed. With ``follow``, wait until ``agent_end``.

    ``timeout`` bounds that wait. ``None`` waits until the job ends.
    """
    seen = 0
    ended = False
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        if not path.is_file():
            if not follow:
                return 1
            if deadline is not None and time.monotonic() >= deadline:
                return 1
            time.sleep(0.05)
            continue
        events = list(iter_events(path))
        for event in events[seen:]:
            write.write(format_event(event) + "\n")
            write.flush()
            if event.get("type") == "agent_end":
                ended = True
        seen = len(events)
        if not follow or ended:
            break
        if deadline is not None and time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    return 0 if path.is_file() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m omega_prime.engine.watch")
    parser.add_argument("job", help="Job id under the state jobs directory")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Seconds to follow. Omit it to wait until agent_end.",
    )
    args = parser.parse_args(argv)
    env = {}
    if args.state_dir:
        env["OMEGA_PRIME_STATE_DIR"] = args.state_dir
    else:
        import os

        raw = os.environ.get("OMEGA_PRIME_STATE_DIR", "")
        if raw:
            env["OMEGA_PRIME_STATE_DIR"] = raw
    try:
        directory = job_dir(env, args.job)
    except JobPathError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    path = directory / "events.jsonl"
    if not path.is_file():
        sys.stderr.write("no event feed for that job\n")
        return 1
    return tail(path, follow=args.follow, timeout=args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
