# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``omega watch <job>``: tail one job's event feed."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from omega_prime.credentials.redact import redact_text
from omega_prime.engine.jobs import JobPathError, job_dir
from omega_prime.engine.protocol import assistant_text, usage_of


def _event_identity(event: dict) -> dict[str, str]:
    raw_identity = event.get("identity")
    source = raw_identity if isinstance(raw_identity, dict) else event
    aliases = {
        "job": ("job", "job_id"),
        "session": ("session", "session_generation", "session_id"),
        "turn": ("turn", "turn_generation", "turn_id"),
        "request": ("request", "request_id", "external_request_id"),
    }
    result = {}
    for name, candidates in aliases.items():
        value = next(
            (source.get(key) for key in candidates if isinstance(source.get(key), str)),
            None,
        )
        if value is not None:
            result[name] = "".join(
                char if char.isprintable() else "?" for char in value
            )
    return result


def _safe_prompt(event: dict) -> str:
    from omega_prime.engine.ui_control import _redacted_prompt, _request_prompt

    raw_request = event.get("request")
    request = raw_request if isinstance(raw_request, dict) else event
    return _redacted_prompt(_request_prompt(request))


def _safe_options(request: dict) -> list[str]:
    from omega_prime.engine.ui_control import _option_identities

    options = request.get("options")
    if options is None and isinstance(request.get("questions"), (list, tuple)):
        options = [
            item
            for question in request["questions"]
            if isinstance(question, dict)
            for item in question.get("options", ())
        ]
    return _option_identities(options)


def format_event(event: dict) -> str:
    kind = str(event.get("type") or "event")
    if kind in {
        "ui_pending",
        "ui_answered",
        "ui_rejected_stale",
        "attached",
        "attach_failed",
    }:
        identity = _event_identity(event)
        fields = [f"{key}={value}" for key, value in identity.items()]
        raw_request = event.get("request")
        request = raw_request if isinstance(raw_request, dict) else event
        request_kind = request.get(
            "kind", request.get("method", request.get("request_kind"))
        )
        if isinstance(request_kind, str):
            fields.append(f"kind={request_kind[:40]}")
        prompt = _safe_prompt(event)
        if prompt:
            fields.append(f"prompt={json.dumps(prompt, ensure_ascii=False)}")
        option_ids = _safe_options(request)
        if option_ids:
            fields.append(f"options={json.dumps(option_ids, ensure_ascii=False)}")
        detail = " ".join(fields)
        return f"{kind} {detail}".rstrip()
    if kind == "message_update":
        text = redact_text(assistant_text(event))
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
    """Print the feed. With ``follow``, wait until the job ends.

    ``agent_end`` is a finished omp turn. ``job_end`` is the driver's
    record that the process stopped without one. ``timeout`` bounds the
    wait. ``None`` waits until one of those events.
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
            if event.get("type") in {"agent_end", "job_end"}:
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
        help="Seconds to follow. Omit it to wait until the job ends.",
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
    if not path.is_file() and not args.follow:
        sys.stderr.write("no event feed for that job\n")
        return 1
    return tail(path, follow=args.follow, timeout=args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
