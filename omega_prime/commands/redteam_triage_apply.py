# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-triage-apply``: preview or apply one triage PROPOSAL.

Read-only without ``--apply``. With ``--apply`` exactly one
``TRIAGED`` line is inserted or removed, then self-checked.
"""

from __future__ import annotations

import difflib
import importlib.util
import json
import os
import re
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

NAME = "omega-redteam-triage-apply"
USAGE = "/omega-redteam-triage-apply --hash <dedupe-hash> [--untriage] [--apply]"
SUMMARY = "Apply (or preview) one triage PROPOSAL into the TRIAGED block."
WHEN = "The user asks to approve or apply a red-team triage proposal."

REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"
REVIEW_QUEUE_FILENAME = "review-queue.ndjson"

SCRIPT_RELATIVE = Path("scripts") / "redteam-audit-triage.py"
SCRIPT_PATH_OVERRIDE: Path | None = None

_HASH_RE = re.compile(r"[0-9a-f]{64}")
_OPENER_RE = re.compile(r"^TRIAGED\s*:.*=\s*\{\s*$")
_ENTRY_RE = re.compile(r'^\s*"([^"]+)"\s*:\s*\(')


class Error(Exception):
    """Structured command failure: ``error`` names it, ``result`` carries it."""

    def __init__(self, error: str, result: dict[str, Any]) -> None:
        super().__init__(error)
        self.error = error
        self.result = result


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _script_path() -> Path:
    if SCRIPT_PATH_OVERRIDE is not None:
        return SCRIPT_PATH_OVERRIDE
    return _repo_root() / SCRIPT_RELATIVE


def _queue_path(env: Mapping[str, str]) -> Path:
    """Review-queue file: env override wins, else the sidecar reports dir."""
    override = (env.get(REPORTS_ENV_VAR) or "").strip()
    if not override:
        override = (os.environ.get(REPORTS_ENV_VAR) or "").strip()
    if override:
        return Path(override) / REVIEW_QUEUE_FILENAME
    try:
        from sidecar.state import reports_dir
    except ImportError as exc:
        raise Error(
            "sidecar_unavailable",
            {"usage": USAGE, "reason": f"{type(exc).__name__}: {exc}"},
        ) from exc
    return reports_dir() / REVIEW_QUEUE_FILENAME


def _parse_flags(args: str) -> tuple[str, bool, bool]:
    """Return ``(hash, untriage, apply)``; any flag/hash problem is ``bad_args``."""
    try:
        tokens = shlex.split(args)
    except ValueError:
        raise Error("bad_args", {"usage": USAGE}) from None
    digest: str | None = None
    untriage = False
    apply = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--hash":
            if digest is not None or index + 1 >= len(tokens):
                raise Error("bad_args", {"usage": USAGE})
            digest = tokens[index + 1]
            index += 2
        elif token == "--untriage":
            if untriage:
                raise Error("bad_args", {"usage": USAGE})
            untriage = True
            index += 1
        elif token == "--apply":
            if apply:
                raise Error("bad_args", {"usage": USAGE})
            apply = True
            index += 1
        else:
            raise Error("bad_args", {"usage": USAGE})
    if digest is None or _HASH_RE.fullmatch(digest) is None:
        raise Error("bad_args", {"usage": USAGE})
    return digest, untriage, apply


def _newest_proposal(queue: Path, digest: str) -> dict[str, Any] | None:
    """Newest queue row for ``digest``; ``None`` when the file is missing/empty."""
    try:
        text = queue.read_text(encoding="utf-8")
    except OSError:
        return None
    match: dict[str, Any] | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("dedupe_hash") == digest:
            match = row
    return match


def _git_status_porcelain(script: Path) -> str:
    """``git status --porcelain`` for the script; stubbed in hermetic tests."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--", str(script)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, ValueError) as exc:
        raise Error(
            "dirty_script",
            {"script": str(script), "reason": f"{type(exc).__name__}: git unavailable"},
        ) from exc
    if proc.returncode != 0:
        raise Error(
            "dirty_script",
            {"script": str(script), "reason": "git status failed"},
        )
    return proc.stdout


def _today_utc() -> str:
    """Today's UTC date; stubbed in hermetic tests."""
    try:
        from sidecar.state import utc_now_rfc3339
    except ImportError as exc:
        raise Error(
            "sidecar_unavailable",
            {"usage": USAGE, "reason": f"{type(exc).__name__}: {exc}"},
        ) from exc
    return utc_now_rfc3339()[:10]


def _block_span(lines: list[str]) -> tuple[int, int]:
    """``(opener, closer)`` line indexes of the ``TRIAGED`` dict literal."""
    opener: int | None = None
    for index, line in enumerate(lines):
        if _OPENER_RE.match(line.rstrip("\n")):
            opener = index
            break
    if opener is None:
        raise Error("script_unparseable", {"reason": "TRIAGED block not found"})
    for index in range(opener + 1, len(lines)):
        if lines[index].strip() == "}":
            return opener, index
    raise Error("script_unparseable", {"reason": "TRIAGED block never closes"})


def _entry_keys(lines: list[str], opener: int, closer: int) -> dict[str, int]:
    """TRIAGED keys to their line indexes."""
    keys: dict[str, int] = {}
    for index in range(opener + 1, closer):
        found = _ENTRY_RE.match(lines[index].rstrip("\n"))
        if found is not None:
            keys[found.group(1)] = index
    return keys


def _entry_line(key: str, date: str, rationale: str) -> str:
    # JSON string literals are valid Python literals and escape quotes safely.
    return f"    {json.dumps(key)}: ({json.dumps(date)}, {json.dumps(rationale)}),\n"


def _self_check(script: Path) -> None:
    """Compile-check the edited script and validate its ``TRIAGED`` value."""
    source = script.read_text(encoding="utf-8")
    try:
        compile(source, str(script), "exec")
    except SyntaxError as exc:
        raise ValueError(f"edited script does not compile: {exc}") from exc
    module_name = f"redteam_audit_triage_selfcheck_{os.getpid()}"
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        raise ValueError("edited script is not importable")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ValueError(f"edited script does not import: {exc}") from exc
    triaged = getattr(module, "TRIAGED", None)
    if not isinstance(triaged, dict):
        raise ValueError("TRIAGED is not a dict after edit")
    for key, value in triaged.items():
        if not isinstance(key, str) or not key:
            raise ValueError("TRIAGED holds an empty key after edit")
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or not all(isinstance(part, str) and part for part in value)
        ):
            raise ValueError(f"TRIAGED[{key!r}] is not a (date, rationale) pair")


def _unified_diff(script: Path, old: list[str], new: list[str]) -> str:
    diff = difflib.unified_diff(
        old,
        new,
        fromfile=f"a/{script.name}",
        tofile=f"b/{script.name}",
    )
    text = "".join(diff)
    return text if text.endswith("\n") or not text else text + "\n"


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Preview (default) or apply (``--apply``) one PROPOSAL verdict."""
    digest, untriage, apply = _parse_flags(args)
    queue = _queue_path(env)
    proposal = _newest_proposal(queue, digest)
    if proposal is None:
        raise Error("no_proposal", {"usage": USAGE, "dedupe_hash": digest})
    rationale = proposal.get("rationale")
    if not isinstance(rationale, str) or not rationale:
        raise Error("no_proposal", {"usage": USAGE, "dedupe_hash": digest})
    script = _script_path()
    try:
        original = script.read_text(encoding="utf-8")
    except OSError as exc:
        raise Error(
            "script_unreadable",
            {"script": str(script), "reason": f"{type(exc).__name__}"},
        ) from exc
    lines = original.splitlines(keepends=True)
    opener, closer = _block_span(lines)
    keys = _entry_keys(lines, opener, closer)
    if untriage:
        if digest not in keys:
            raise Error("not_triaged", {"key": digest})
        new_lines = [line for idx, line in enumerate(lines) if idx != keys[digest]]
        # Report the removed entry's date in the result dict.
        date = _today_utc()
        date_match = re.search(r'\(\s*"([^"]*)"', lines[keys[digest]])
        if date_match is not None:
            date = date_match.group(1)
    else:
        if digest in keys:
            raise Error("already_triaged", {"key": digest})
        date = _today_utc()
        entry = _entry_line(digest, date, rationale)
        insert_at = closer
        for key in sorted(keys):
            if digest < key:
                insert_at = keys[key]
                break
        new_lines = [*lines[:insert_at], entry, *lines[insert_at:]]
    # Fail closed on concurrent edits before touching the file, in both modes.
    if _git_status_porcelain(script).strip():
        raise Error("dirty_script", {"script": str(script)})
    diff = _unified_diff(script, lines, new_lines)
    if not apply:
        return {
            "preview_only": True,
            "diff": diff,
            "queue": str(queue),
            "ts": proposal.get("ts"),
            "proposer": proposal.get("proposer"),
        }
    script.write_text("".join(new_lines), encoding="utf-8")
    try:
        _self_check(script)
    except ValueError as exc:
        script.write_text(original, encoding="utf-8")
        raise Error(
            "self_check_failed",
            {"key": digest, "self_check": "fail", "reason": str(exc)},
        ) from exc
    result: dict[str, Any] = {
        "applied": True,
        "key": digest,
        "date": date,
        "self_check": "pass",
    }
    if untriage:
        result["untriaged"] = True
    return result


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]
