# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Isolated local readiness CLI. Stdlib-only controller.

``python -m omega_prime.grokbot.readiness --root SOURCE --json --output REPORT``

The controller never initializes production handlers and never copies the
parent environment: it builds the disposable layout via ``local_validation``
(which is stdlib-only at import), spawns exactly one fresh product child that
runs the whole profile, and receives only validated sanitized output. The
explicit ``--output`` path is the only intended write outside the disposable
layout. Diagnostics are fixed safe labels only, never raw child content.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, NoReturn

from omega_prime.grokbot.local_validation import (
    ROSTER_REL,
    SCHEMA_VERSION,
    SCOPE,
    isolated_layout,
)
from omega_prime.grokbot.secret_guard import SecretLeakError, assert_no_secrets
from omega_prime.tooling.fs import atomic_write_text
from omega_prime.tooling.roster import load_roster_names

EXIT_OK = 0
EXIT_READINESS = 1
EXIT_USAGE = 2

_CHILD_TIMEOUT = 900.0
_REQUIRED_TOOL_KEYS = ("name", "family", "status", "evidence", "prerequisites")
_VALID_STATUSES = ("exercised", "feature_disabled", "blocked", "failed")


def find_default_root() -> Path:
    """Nearest ancestor of the cwd holding the canonical roster file."""
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ROSTER_REL).is_file():
            return candidate
    return current


_USAGE_WITHHELD = "usage error withheld: secret gate tripped"
"""Fixed safe label for a usage diagnostic that trips the secret gate.

Whole-input boundary: the complete diagnostic text reaches the unchanged
``assert_no_secrets`` gate BEFORE any stderr publication, so a recognized
candidate can never echo raw. Clean diagnostics pass through byte-identical;
only an actual classification trip publishes this constant.
"""


def _guard_usage_text(text: str) -> str:
    """Gate full usage-diagnostic ``text``; fixed label on a trip."""
    try:
        assert_no_secrets(str(text), "readiness-usage")
    except SecretLeakError:
        return _USAGE_WITHHELD
    return str(text)


class _UsageGuardedParser(argparse.ArgumentParser):
    """Parser that gates ``error`` text before stderr publication.

    Preserves genuine argparse behavior (usage line plus ``exit 2``); only
    the message carrying the offending arguments is gated whole-input.
    """

    def error(self, message: str) -> NoReturn:
        super().error(_guard_usage_text(message))


def _fail_usage(message: str) -> int:
    print(f"omega-prime-readiness: {_guard_usage_text(message)}", file=sys.stderr)
    return EXIT_USAGE


def _fail_readiness() -> int:
    print("readiness run failed", file=sys.stderr)
    return EXIT_READINESS


def _run_child(
    layout: Any, *, include_prime: bool, timeout: float = _CHILD_TIMEOUT
) -> tuple[int, str]:
    """Spawn the one fresh product child; return (returncode, stdout).

    The child leads its own process group so a deadline kill reaches the
    loopback host it spawned (shared group) before layout deletion. All
    diagnostics are fixed safe labels; raw child output is never printed.
    """
    code = (
        "from omega_prime.grokbot.local_validation import child_main as _main; "
        "raise SystemExit(_main())"
    )
    env = dict(layout.env)
    env["READINESS_BASE"] = str(layout.base)
    env["READINESS_INSTALL"] = str(layout.install)
    env["READINESS_HOME"] = str(layout.home)
    env["READINESS_WORK"] = str(layout.work)
    env["READINESS_STATE"] = str(layout.state)
    env["READINESS_AUDIT"] = str(layout.audit)
    env["READINESS_COMMAND_LOG"] = str(layout.command_log)
    env["READINESS_SOURCE"] = str(layout.source)
    env["READINESS_INCLUDE_PRIME"] = "1" if include_prime else "0"
    env["READINESS_PRIME_MISSING"] = ",".join(layout.prime_missing)
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=str(layout.work),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if proc is not None:
            with contextlib.suppress(OSError, ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=30)
        return EXIT_READINESS, ""
    if proc is not None and proc.returncode != 0:
        return EXIT_READINESS, ""
    return EXIT_OK, stdout if proc is not None else ""


def validate_report(report: Any, canonical: list[str]) -> dict[str, Any]:
    """Shape-check the child report and its exactly-once roster accounting."""
    if not isinstance(report, dict):
        raise ValueError("report is not an object")
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("report schema_version is not 1")
    if report.get("scope") != SCOPE:
        raise ValueError("report scope is not isolated-local")
    for key in ("ok", "checks", "tools", "summary", "limitations"):
        if key not in report:
            raise ValueError(f"report is missing {key}")
    if not isinstance(report["ok"], bool):
        raise ValueError("report ok is not a bool")
    tools = report["tools"]
    if not isinstance(tools, list):
        raise ValueError("report tools is not a list")
    seen: list[str] = []
    for row in tools:
        if not isinstance(row, dict):
            raise ValueError("report tool row is not an object")
        for key in _REQUIRED_TOOL_KEYS:
            if key not in row:
                raise ValueError(f"report tool row is missing {key}")
        if row["status"] not in _VALID_STATUSES:
            raise ValueError(f"report tool row has bad status {row['status']!r}")
        seen.append(str(row["name"]))
    if sorted(seen) != sorted(canonical):
        missing = sorted(set(canonical) - set(seen))
        extra = sorted(set(seen) - set(canonical))
        raise ValueError(
            f"roster accounting gap: missing={missing[:5]} extra={extra[:5]}"
        )
    if len(set(seen)) != len(seen):
        raise ValueError("roster names are duplicated")
    summary = report["summary"]
    if not isinstance(summary, dict):
        raise ValueError("report summary is not an object")
    if "problems" in summary:
        problems = summary["problems"]
        if not isinstance(problems, list) or not all(
            isinstance(problem, str) for problem in problems
        ):
            raise ValueError("report summary problems is not a list of strings")
    checks = report["checks"]
    if not isinstance(checks, list):
        raise ValueError("report checks is not a list")
    for row in checks:
        if not isinstance(row, dict):
            raise ValueError("report check row is not an object")
    return report


def _human_lines(report: dict[str, Any]) -> list[str]:
    summary = report.get("summary", {})
    lines = [
        f"readiness {report.get('scope')} {report.get('profile')}: "
        f"ok={report.get('ok')} "
        f"tools={summary.get('total')} "
        f"exercised={summary.get('exercised')} "
        f"feature_disabled={summary.get('feature_disabled')} "
        f"blocked={summary.get('blocked')} "
        f"failed={summary.get('failed')}"
    ]
    for row in report.get("tools", []):
        if isinstance(row, dict) and row.get("status") == "failed":
            lines.append(f"failed: {row.get('name')}")
    for check in report.get("checks", []):
        if isinstance(check, dict) and check.get("status") == "failed":
            lines.append(f"failed-check: {check.get('name')}")
    for problem in summary.get("problems", []):
        lines.append(f"problem: {problem}")
    return lines


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. 0 only when the validated report has ``ok`` true."""
    parser = _UsageGuardedParser(prog="omega-prime-grokbot-readiness")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--include-prime", action="store_true")
    args = parser.parse_args(argv)

    root = args.root if args.root is not None else find_default_root()
    root = Path(root).resolve()
    roster_path = root / ROSTER_REL
    if not roster_path.is_file():
        return _fail_usage(f"no canonical roster under {root}")
    try:
        canonical = load_roster_names(roster_path)
    except OSError:
        return _fail_usage(f"cannot read roster {roster_path}")
    if not canonical:
        return _fail_usage("canonical roster lists no tools")

    try:
        with isolated_layout(root, include_prime=args.include_prime) as layout:
            code, stdout = _run_child(layout, include_prime=args.include_prime)
    except Exception:  # fixed message, no env bytes
        return _fail_readiness()
    if code != EXIT_OK:
        return _fail_readiness()
    try:
        report = validate_report(json.loads(stdout), canonical)
    except ValueError:
        return _fail_readiness()

    text = json.dumps(report, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        assert_no_secrets(text, "readiness-report")
    except SecretLeakError:
        print("readiness report withheld: secret gate tripped", file=sys.stderr)
        return EXIT_READINESS
    if args.output is not None:
        try:
            atomic_write_text(Path(args.output), text)
        except OSError:
            return _fail_readiness()
    if args.json:
        sys.stdout.write(text)
    else:
        for line in _human_lines(report):
            print(line)
    return EXIT_OK if report["ok"] else EXIT_READINESS


if __name__ == "__main__":
    raise SystemExit(main())
