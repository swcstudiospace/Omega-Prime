# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Redteam pin-bump automation — the ``docs/red-team.md`` runbook as one CLI.

The ``redteam/`` gitlink is the only moving part of the red-team module.
This CLI performs the whole bump runbook in order and stops loudly on the
first failure, leaving no partial pin records behind:

1. **Move the pin.** Validate ``--to`` (a 40-hex SHA, or a ``v``-tag
   resolved via ``git ls-remote``; anything else is a usage error),
   fetch upstream inside ``redteam/``, check out the ref, and stage the
   gitlink (``git add redteam``).
2. **Re-vendor the pack.** Mirror ``redteam/grokbot/`` over
   ``omega_prime/redteam/pack/`` byte-verbatim (same relative paths,
   binary assets included), preserving ``pack/LICENSE`` (no upstream
   counterpart, skipped by ``pack-diff``).
3. **Rewrite the pin records and prove provenance.** Set the ``Commit:``
   line under the ``T3MP3ST red team pack`` section of ``VENDOR.md`` and
   the ``Upstream commit:`` docstring in
   ``omega_prime/redteam/__init__.py`` to the resolved SHA, then run
   ``check`` and ``pack-diff`` until both are green (re-copy loop, max 2
   passes; still diverged is a hard failure). ``surface`` DRIFT is
   reported and stops the bump — updating ``REDTEAM_TOOL_NAMES`` stays a
   manual legitimacy decision.
4. **Audit the new pin.** Run ``npm audit --omit=dev --audit-level=high
   --json`` at the new pin (same chain as ``supply-chain.yml``) and
   triage it with ``scripts/redteam-audit-triage.py::triage``. Untriaged
   high-or-above advisories fail the bump. The CLI never writes the
   ``TRIAGED`` block — triage stays a dated human entry.
5. **Emit the delta report.** Diff the old-pin and new-pin audits with
   ``scripts/redteam-audit-delta.py`` into ``report.md`` and quote the
   single-word verdict (``improved`` / ``regressed`` / ``unchanged``) on
   stdout. ``--old`` defaults to the pre-bump pin's saved audit artifact
   (``omega_prime/tests/data/redteam-audit-<pin>.json``) when present,
   else ``--old-audit`` is required. A ``regressed`` verdict fails the
   bump and nothing is pushed.
6. **Open the PR (``--open-pr`` only).** Create branch
   ``pin-bump-<short-sha>``, commit exactly the bump set (gitlink +
   ``VENDOR.md`` + ``__init__.py`` + pack), push, and run ``gh pr
   create`` with the delta verdict and the ``report.md`` link in the
   body. Pushing happens only after local green.
7. **Exit codes.** ``0`` = bump complete, provenance green, audit
   green-or-``--skip-audit``; ``1`` = audit regressed / untriaged high+
   at the new pin / provenance still red after the re-copy loop; ``2`` =
   usage error (bad ref, unresolvable tag, missing ``--old-audit`` when
   no saved artifact, ``gh`` missing under ``--open-pr``); ``3`` =
   degraded — ``node``/``npm`` absent or the submodule unreachable, with
   the completed prefix steps named on stderr.

``--skip-audit`` skips steps 4-5 (offline/provenance-only bump); stdout
says ``audit-skipped`` so the PR body cannot quote a verdict.
``--rollback`` re-pins to the previous version from the last bump record
(``report.md``'s ``Old:`` audit label, else the git history of
``VENDOR.md``), restores the old lockfile entry (and the
``redteam/package.json`` version when ``--bump-commit`` was part of the
original bump), and opens a revert PR. When the tick/job context carries
a RoE artifact the grant must be active (non-expired, non-revoked);
without RoE the rollback proceeds (fail-open local runs). No prior bump
is a usage error naming every location checked. ``--dry-run`` prints the
revert plan with zero mutations. ``report.md`` is left untouched so it
stays the record a future rollback reads.

``--dry-run`` runs the verification steps (ref resolution, pin-record
readiness, re-vendor preview counts, surface check, npm audit, delta)
and prints what WOULD change, but performs zero file mutations: no
``git`` checkout/add, no re-vendor copy, no pin rewrite, no
``report.md``, no PR branch. Every would-be step is printed with a
``[dry-run]`` prefix. Exit ``0`` when the bump would succeed, ``1``
when the audit would regress (or shows untriaged high+), ``2`` for
usage errors, ``3`` for degraded — the same codes as normal mode. The
npm audit covers the current checkout (the new pin is never checked
out in dry-run mode).
``--report-to PATH`` writes the delta report to ``PATH`` instead of
``<root>/report.md`` (exclusive; relative paths resolve against the
current working directory). The write is atomic (temp file in the
destination directory + ``os.replace``, mode 0644) and missing parent
directories are created. A symlink, directory, non-regular file,
empty/NUL path or non-directory parent is a usage error (exit ``2``), as
is combining it with ``--audit-only``, ``--rollback`` or
``--skip-audit``. ``--rollback`` always reads ``<root>/report.md`` and
falls back to the ``VENDOR.md`` git history, so a custom path loses the
report-based rollback record. ``--dry-run`` validates only. ``--schedule``
stores the (absolute) path and every replay re-validates it, writing
nothing when it is invalid. ``--verify`` failure removes the report only
when this run created it.
``--bump-commit`` (with ``--semver-check``) rolls the ``version`` field in
``redteam/package.json`` forward one patch — or one minor when a compatible
fix moved the minor digit — when the audit verdict is ``improved`` and every
fix classified compatible. Anything else (a major/unknown fix, a missing or
unreadable ``package.json``) skips the bump with a warning and leaves the
exit code untouched (fail-closed).
``--pin <advisory-or-package>`` (repeatable) runs the same audit chain but
restricts the triage gate and the delta verdict to the pinned advisory set
(match by advisory id or package name substring). Other advisories are
listed under ``## Out of scope for --pin`` in the delta report but do not
affect the verdict or block the bump. A selector that matches nothing in
either audit is a usage error naming the advisories found. Composes with
``--dry-run``, ``--semver-check``, ``--json``, and ``--audit-only``; the
file path (fetch/checkout, full re-vendor for a green pack-diff, pin
rewrite, ``report.md``) still runs whole-tree so provenance stays green.

All ``git``/``npm``/``node``/``gh`` calls go through the injectable
``run(cmd, cwd)`` seam (default :func:`subprocess.run`) so tests stay
hermetic. The CLI takes no ``OMEGA_PRIME_*`` env vars and reads no
``omega-prime.json`` tree: bump pins are git state, not family flags.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sidecar import provenance

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = REPO_ROOT / "scripts"

VENDOR_SECTION = "## T3MP3ST red team pack"
VENDOR_PIN_LINE_RE = re.compile(
    r"^(\s*-\s*Commit:\s*`)[0-9a-fA-F]{40}(`)", re.MULTILINE
)
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
TAG_RE = re.compile(r"^v[0-9][0-9A-Za-z._-]*$")
LS_REMOTE_SHA_RE = re.compile(r"\b([0-9a-f]{40})\b")
UPSTREAM_REMOTE = "origin"

EXIT_OK = 0
EXIT_BUMP_FAILED = 1
EXIT_USAGE = 2
EXIT_DEGRADED = 3
EXIT_VERIFY_FAILED = 4

# Type of the subprocess seam: run(cmd, cwd=None) -> CompletedProcess-like.
Runner = Callable[..., Any]


def _default_run(cmd: list[str], cwd: Path | None = None) -> Any:
    """Default subprocess seam (real ``git``/``npm``/``node``/``gh``)."""
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True)


def _load_script(name: str) -> Any:
    """Import a hyphen-named helper from ``scripts/`` (triage / delta)."""
    path = _SCRIPTS_DIR / name
    module_name = "pin_bump_" + path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load helper script {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_ls_remote_tag(stdout: str, tag: str) -> str | None:
    """Resolved commit SHA for ``tag`` from ``git ls-remote`` output.

    Prefers the peeled ``refs/tags/<tag>^{}`` line (the commit) over the
    tag-object line.
    """
    wanted = f"refs/tags/{tag}"
    fallback: str | None = None
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        sha, ref = parts[0], parts[1]
        if not SHA_RE.match(sha):
            continue
        if ref == wanted + "^{}":
            return sha.lower()
        if ref == wanted and fallback is None:
            fallback = sha.lower()
    return fallback


def _vendor_section(text: str) -> str | None:
    """The ``T3MP3ST red team pack`` section slice of ``VENDOR.md``."""
    start = text.find(VENDOR_SECTION)
    if start < 0:
        return None
    end = text.find("\n## ", start)
    return text[start:] if end < 0 else text[start:end]


def _default_old_audit(root: Path, old_pin: str | None) -> Path | None:
    """Saved audit artifact for the pre-bump pin, when present."""
    if not old_pin:
        return None
    candidate = (
        root / "omega_prime" / "tests" / "data" / f"redteam-audit-{old_pin}.json"
    )
    return candidate if candidate.is_file() else None


def _load_from_audit(path_str: str) -> tuple[Any | None, str | None]:
    """``(doc, error)`` for ``--from-audit``: the new-pin audit, no re-audit.

    A missing, unreadable, or unparseable file is a usage error (exit 2):
    the caller reports the returned message through its usage path.
    """
    path = Path(path_str)
    if not path.is_file():
        return None, f"--from-audit not found: {path_str}"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"cannot read --from-audit {path} ({exc})"
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, f"cannot parse --from-audit {path} ({exc})"


_GITHUB_REPO_RE = re.compile(
    r"^(?:https?://github\.com/|git@github\.com:)([^/\s]+)/([^/\s]+?)(?:\.git)?$"
)


def _parse_github_repo(url: str) -> tuple[str, str] | None:
    """``(owner, repo)`` from a github remote URL, or ``None``."""
    text = (url or "").strip().rstrip("/")
    if text.endswith(".git"):
        text = text[: -len(".git")]
    match = _GITHUB_REPO_RE.match(text)
    if match is None:
        return None
    return match.group(1), match.group(2)


def _check_upstream_fix(
    run: Runner, redteam_dir: Path
) -> tuple[bool | None, str, list[tuple[str, str, object]]]:
    """Probe upstream HEAD for untriaged high/critical advisories.

    Downloads the upstream ``package-lock.json`` via ``gh api`` into a
    temp dir and runs ``npm audit --json`` there. Returns ``(clean,
    note, untriaged)``: ``clean`` True means upstream shows zero
    untriaged high+ advisories (safe to bump); False means upstream is
    still vulnerable (nothing to fix yet); None means the probe was
    inconclusive (callers proceed with the bump). Never raises on
    subprocess or parse problems — those are inconclusive, not clean.
    """
    try:
        url_proc = run(["git", "config", "--get", "remote.origin.url"], cwd=redteam_dir)
    except OSError as exc:
        return None, f"cannot read origin url ({exc})", []
    if url_proc.returncode != 0:
        return (
            None,
            f"git config remote.origin.url failed: {url_proc.stderr.strip()}",
            [],
        )
    repo = _parse_github_repo(url_proc.stdout)
    if repo is None:
        return None, f"unparseable origin url {url_proc.stdout.strip()!r}", []
    owner, name = repo
    try:
        lock_proc = run(
            [
                "gh",
                "api",
                f"repos/{owner}/{name}/contents/package-lock.json",
                "--jq",
                ".content",
            ],
            cwd=redteam_dir,
        )
    except OSError as exc:
        return None, f"gh api failed ({exc})", []
    if lock_proc.returncode != 0:
        return None, f"gh api failed: {lock_proc.stderr.strip()}", []
    try:
        lock_bytes = base64.b64decode(
            lock_proc.stdout.strip().strip('"'), validate=True
        )
    except ValueError as exc:
        return None, f"cannot decode upstream package-lock.json ({exc})", []
    with tempfile.TemporaryDirectory(prefix="pin-bump-upstream-") as tmpname:
        tmpdir = Path(tmpname)
        try:
            (tmpdir / "package.json").write_text(
                '{"name": "upstream-audit", "version": "0.0.0"}\n',
                encoding="utf-8",
            )
            (tmpdir / "package-lock.json").write_bytes(lock_bytes)
        except OSError as exc:
            return None, f"cannot stage upstream lockfile ({exc})", []
        try:
            audit_proc = run(
                [
                    "npm",
                    "audit",
                    "--omit=dev",
                    "--audit-level=high",
                    "--json",
                ],
                cwd=tmpdir,
            )
        except OSError as exc:
            return None, f"npm is absent ({exc})", []
    try:
        upstream_doc = json.loads(audit_proc.stdout)
    except ValueError as exc:
        return None, f"cannot parse upstream npm audit output ({exc})", []
    try:
        triage_mod = _load_script("redteam-audit-triage.py")
    except (OSError, RuntimeError) as exc:
        return None, f"cannot load audit triage helper ({exc})", []
    findings, triage_error = triage_mod.triage(upstream_doc)
    if triage_error is not None:
        return None, f"upstream audit triage refused output: {triage_error}", []
    untriaged = [
        finding for finding in findings if finding[0] not in triage_mod.TRIAGED
    ]
    if untriaged:
        return False, "upstream HEAD still vulnerable", untriaged
    return True, "upstream HEAD audit clean", []


def revendor_pack(upstream_dir: Path, vendored_dir: Path) -> tuple[int, int]:
    """Mirror the upstream pack tree over the vendored pack tree.

    Copies every upstream file byte-verbatim to the same relative path
    (binary assets included) and removes vendored files with no upstream
    counterpart, except ``pack/LICENSE`` which has no upstream
    counterpart by design and is never touched. Returns ``(copied,
    removed)`` counts.
    """
    copied = 0
    for src in sorted(upstream_dir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(upstream_dir).as_posix()
        if rel in provenance.PACK_SKIP_REL:
            continue
        data = src.read_bytes()
        dst = vendored_dir / rel
        if dst.is_file() and dst.read_bytes() == data:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        copied += 1
    removed = 0
    for dst in sorted(vendored_dir.rglob("*")):
        if dst.is_dir():
            continue
        rel = dst.relative_to(vendored_dir).as_posix()
        if rel in provenance.PACK_SKIP_REL:
            continue
        if not os.path.lexists(upstream_dir / rel):
            dst.unlink()
            removed += 1
    for empty in sorted(
        (p for p in vendored_dir.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        with contextlib.suppress(OSError):
            empty.rmdir()
    return copied, removed


def revendor_preview(upstream_dir: Path, vendored_dir: Path) -> tuple[int, int]:
    """Read-only preview of :func:`revendor_pack`.

    Walks both trees with the same skip rules and returns the
    ``(would_copy, would_remove)`` counts without creating, modifying,
    or deleting any file. Used by ``--dry-run``.
    """
    would_copy = 0
    for src in sorted(upstream_dir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(upstream_dir).as_posix()
        if rel in provenance.PACK_SKIP_REL:
            continue
        data = src.read_bytes()
        dst = vendored_dir / rel
        if dst.is_file() and dst.read_bytes() == data:
            continue
        would_copy += 1
    would_remove = 0
    for dst in sorted(vendored_dir.rglob("*")):
        if dst.is_dir():
            continue
        rel = dst.relative_to(vendored_dir).as_posix()
        if rel in provenance.PACK_SKIP_REL:
            continue
        if not os.path.lexists(upstream_dir / rel):
            would_remove += 1
    return would_copy, would_remove


def rewrite_pins(root: Path, sha: str) -> str | None:
    """Rewrite both pin records to ``sha``. Returns an error, or None."""
    vendor_path = root / "VENDOR.md"
    try:
        vendor_text = vendor_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"cannot read VENDOR.md ({exc})"
    section = _vendor_section(vendor_text)
    if section is None:
        return f"no {VENDOR_SECTION!r} section in VENDOR.md"
    rewritten, count = VENDOR_PIN_LINE_RE.subn(
        r"\g<1>" + sha + r"\g<2>", section, count=1
    )
    if count == 0:
        return "no `Commit:` pin line in the T3MP3ST section of VENDOR.md"
    start = vendor_text.find(VENDOR_SECTION)
    end = vendor_text.find("\n## ", start)
    vendor_text = (
        vendor_text[:start] + rewritten
        if end < 0
        else vendor_text[:start] + rewritten + vendor_text[end:]
    )
    docstring_path = root / provenance.DOCSTRING_FILE
    try:
        docstring_text = docstring_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"cannot read {provenance.DOCSTRING_FILE.as_posix()} ({exc})"
    if not provenance.DOCSTRING_PIN_RE.search(docstring_text):
        return f"no `Upstream commit:` pin in {provenance.DOCSTRING_FILE.as_posix()}"
    docstring_text = provenance.DOCSTRING_PIN_RE.sub(
        "Upstream commit: " + sha, docstring_text
    )
    try:
        vendor_path.write_text(vendor_text, encoding="utf-8")
        docstring_path.write_text(docstring_text, encoding="utf-8")
    except OSError as exc:
        return f"cannot rewrite pin records ({exc})"
    return None


def build_parser() -> argparse.ArgumentParser:
    """CLI parser (kept separate so tests can drive :func:`main`)."""
    parser = argparse.ArgumentParser(
        description=(
            "Bump the redteam submodule pin: move the gitlink, re-vendor "
            "the pack, rewrite the pin records, prove provenance, audit "
            "the new pin, and report the audit delta."
        ),
        epilog=(
            "exit codes: 0 = bump complete, provenance green, audit "
            "green-or---skip-audit; 1 = audit regressed / untriaged high+ "
            "at the new pin / provenance still red after the re-copy loop; "
            "2 = usage error (bad ref, unresolvable tag, missing "
            "--old-audit when no saved artifact, gh missing under "
            "--open-pr); 3 = degraded — node/npm absent or the submodule "
            "unreachable, with the completed prefix steps named on stderr; "
            "4 = post-bump --verify smoke failed (bump rolled back locally, "
            "no PR)."
        ),
    )
    parser.add_argument(
        "--to",
        required=False,
        default=None,
        help="New pin: a 40-hex upstream SHA or a v-tag (e.g. v1.4). Required unless --rollback.",
    )
    parser.add_argument(
        "--rollback",
        dest="rollback",
        action="store_true",
        help=(
            "Re-pin to the previous version from the last bump record "
            "(report.md's Old audit label, else the git history of "
            "VENDOR.md), restore the old lockfile entry (and the "
            "redteam/package.json version when --bump-commit was part of "
            "the original bump), and open a revert PR. No prior bump is "
            "a usage error naming every location checked. Composes with "
            "--dry-run (prints the revert plan, zero mutations). When the "
            "tick/job context carries a RoE artifact the grant must be "
            "active (non-expired, non-revoked); without RoE the rollback "
            "proceeds (fail-open local runs)."
        ),
    )
    parser.add_argument(
        "--old-audit",
        default=None,
        help=(
            "Old-pin npm audit JSON for the delta report. Defaults to the "
            "pre-bump pin's saved artifact "
            "(omega_prime/tests/data/redteam-audit-<pin>.json) when present."
        ),
    )
    parser.add_argument(
        "--from-audit",
        default=None,
        metavar="PATH",
        help=(
            "New-pin npm audit JSON for the delta report: run the full "
            "bump flow from a previously exported audit document (same "
            "schema as the normal run's audit docs) without re-auditing. "
            "The old-pin audit still comes from --old-audit (required)."
        ),
    )
    parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="Skip the npm audit and delta report (offline bump).",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help=(
            "Verify only: resolve the ref, preview the re-vendor counts, "
            "check the surface, audit, and report the delta without "
            "changing any file."
        ),
    )
    parser.add_argument(
        "--audit-only",
        dest="audit_only",
        action="store_true",
        help=(
            "Audit only: run the npm audit, delta report, and "
            "--semver-check classification for the new pin and print the "
            "report to stdout without changing any file (no lockfile "
            "rewrite, no package.json bump even with --bump-commit, no "
            "commit, no PR; cannot be combined with --open-pr)."
        ),
    )
    parser.add_argument(
        "--pin",
        dest="pin",
        action="append",
        default=None,
        metavar="ADVISORY_OR_PACKAGE",
        help=(
            "Targeted mode: restrict the audit delta and verdict to the "
            "named advisory (match by advisory id or package name "
            "substring, e.g. --pin GHSA-xxxx-yyyy-zzzz --pin left-pad). "
            "Other advisories are listed as out of scope for --pin but do "
            "not affect the verdict. Repeatable; the verdict considers "
            "only the pinned set. Composes with --dry-run, --semver-check, "
            "--json, and --audit-only."
        ),
    )
    parser.add_argument(
        "--explain",
        dest="explain",
        default=None,
        metavar="ADVISORY_OR_PACKAGE",
        help=(
            "Explain one advisory: render a deterministic markdown one-pager "
            "to stdout for the single advisory matching the given id or "
            "package name substring (same matching as --pin) — WHAT "
            "(package, old->new pinned versions, severity, CVSS if present), "
            "WHY (detection summary from the audit data), FIX (the exact "
            "pin-bump action plus the semver classification), and ROLLBACK "
            "(the --rollback command line that would revert it). An unknown "
            "advisory is a usage error (exit 2) listing the advisories "
            "present. Composes with --audit-only (no mutations) and --json "
            "(which adds an explain object)."
        ),
    )
    parser.add_argument(
        "--pin-note",
        dest="pin_note",
        action="append",
        default=None,
        metavar="SELECTOR=TEXT",
        help=(
            "Operator annotation for the delta report: SELECTOR targets "
            "advisories by advisory id or package name substring (same "
            "case-insensitive matching as --pin) and TEXT is the note "
            "rendered in the report's ## Operator notes table. Notes whose "
            "selector matches no advisory render flagged "
            "'(no matching advisory)'. Repeatable; composes with --pin, "
            "--semver-check, --json, --dry-run, and --audit-only."
        ),
    )

    parser.add_argument(
        "--semver-check",
        dest="semver_check",
        action="store_true",
        help=(
            "Classify each fixed advisory as semver-compatible (patch/minor "
            "bump within the declared range) or major (outside the declared "
            "range) by comparing old/new dependency versions, and report "
            "the classification per advisory in the delta report."
        ),
    )
    parser.add_argument(
        "--bump-commit",
        dest="bump_commit",
        action="store_true",
        help=(
            "When the audit verdict is improved and --semver-check classifies "
            "every fix as semver-compatible, bump the version field in "
            "redteam/package.json (patch, or minor when a fix moved the minor "
            "digit) and note it in the --open-pr commit body. Major/unknown "
            "fixes or an absent/unreadable package.json skip the bump with a "
            "warning (fail-closed)."
        ),
    )
    parser.add_argument(
        "--auto-detect-upstream-fix",
        dest="auto_detect_upstream_fix",
        action="store_true",
        help=(
            "Probe upstream HEAD first: download its package-lock.json via "
            "gh api, audit it, and skip the bump (exit 0, nothing to fix "
            "yet) when upstream still shows untriaged high/critical "
            "advisories."
        ),
    )
    parser.add_argument(
        "--schedule",
        dest="schedule",
        default=None,
        metavar="CRON_EXPR",
        help=(
            "Register a recurring pin-bump audit on a five-field cron "
            "cadence (e.g. '0 6 * * *') instead of bumping now: each tick "
            "re-runs the audit chain with audit-only semantics and opens "
            "the normal bump PR path only when the verdict is improved "
            "(and, with --semver-check, every fix is semver-compatible). "
            "The current argv is stored as the job config; a duplicate "
            "--schedule-name replaces the previous job. An invalid cron "
            "expression is a usage error (exit 2) and registers nothing."
        ),
    )
    parser.add_argument(
        "--schedule-name",
        dest="schedule_name",
        default="pin-bump",
        metavar="NAME",
        help=(
            "Name for the --schedule job (default: pin-bump). Registering "
            "again under the same name replaces the previous schedule."
        ),
    )
    parser.add_argument(
        "--open-pr",
        action="store_true",
        help="Commit the bump set, push branch pin-bump-<short-sha>, run gh pr create.",
    )
    parser.add_argument(
        "--verify",
        dest="verify",
        action="store_true",
        help=(
            "Post-bump smoke: after the bump applies and before any PR, "
            "re-run the audit chain against the new pin and require an "
            "improved verdict with no untriaged high/critical (and the "
            "--semver-check classification to hold when enabled). A "
            "failed verification rolls the bump back locally and exits 4 "
            "with no PR. Cannot be combined with --skip-audit; composes "
            "with --dry-run (prints that verification would run, with no "
            "second audit)."
        ),
    )
    parser.add_argument(
        "--json",
        dest="json_mode",
        action="store_true",
        help=(
            "Emit the delta report as a single machine-readable JSON object "
            "on stdout (keys: verdict, rows, semver, bump, exit_code) "
            "instead of markdown; all human-readable progress goes to "
            "stderr. report.md is still written in normal mode; with "
            "--audit-only the JSON goes to stdout and no report.md is "
            "written."
        ),
    )
    parser.add_argument(
        "--report-to",
        dest="report_to",
        default=None,
        metavar="PATH",
        help=(
            "Write the delta report to PATH instead of <root>/report.md "
            "(exclusive: report.md is NOT also written). A relative PATH "
            "resolves against the current working directory; missing "
            "parent directories are created; the file is replaced "
            "atomically (mode 0644). A symlink, directory, other "
            "non-regular file, empty/NUL path, or a parent that is not a "
            "directory is a usage error (exit 2), as is combining it "
            "with --audit-only, --rollback or --skip-audit. --rollback "
            "still reads <root>/report.md (then the VENDOR.md git "
            "history), so a custom path loses the report-based rollback "
            "record. --dry-run only validates and prints where the "
            "report would go. With --json the payload gains a 'report' "
            "key holding the resolved path. Recorded by --schedule and "
            "re-validated at each replay."
        ),
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help=(
            "Confirm each step (ref resolved, pack re-vendor counts, "
            "audit verdict, delta verdict) with a y/n prompt before "
            "proceeding; non-TTY stdin auto-declines."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: parent of omega_prime/). Testing override.",
    )
    return parser


def _confirm_step(
    step: str,
    summary: str,
    *,
    interactive: bool,
    dry_run: bool = False,
    audit_only: bool = False,
) -> bool:
    """Y/n gate for ``--interactive`` mode. Returns True to proceed.

    Prints the step's result ``summary`` before prompting. A non-TTY
    stdin auto-declines (same as answering ``n``); any answer other
    than ``y``/``yes`` declines.
    """
    if not interactive:
        return True
    if audit_only:
        prefix = "[audit-only] "
    elif dry_run:
        prefix = "[dry-run] "
    else:
        prefix = ""
    print(f"{prefix}pin-bump confirm [{step}]: {summary}")
    try:
        is_tty = sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False
    except (OSError, ValueError):
        is_tty = False
    if not is_tty:
        print(f"{prefix}pin-bump declined [{step}]: non-TTY stdin (auto-decline)")
        return False
    print(f"{prefix}Proceed? [y/n]: ", end="")
    try:
        answer = sys.stdin.readline()
    except (OSError, ValueError):
        print(f"{prefix}pin-bump declined [{step}]: unreadable stdin (auto-decline)")
        return False
    if answer.strip().lower() in ("y", "yes"):
        return True
    print(f"{prefix}pin-bump declined [{step}]: operator declined")
    return False


_VERSION_RE = re.compile(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def _parse_version(spec: str) -> tuple[int, int, int] | None:
    """``(major, minor, patch)`` from an audit/lockfile version spec.

    Strips range operators (``^ ~ >= <= > < =``) by scanning for the first
    ``X[.Y[.Z]]`` run: ``^1.2.3`` -> ``(1, 2, 3)``, ``>=1.0.0 <2.0.0`` ->
    ``(1, 0, 0)``. Missing minor/patch default to 0. Returns None when the
    spec carries no version number at all.
    """
    match = _VERSION_RE.search(spec or "")
    if match is None:
        return None
    try:
        return (
            int(match.group(1)),
            int(match.group(2) or 0),
            int(match.group(3) or 0),
        )
    except ValueError:
        return None


def classify_semver_fixes(
    old_findings: dict[str, tuple[str, str, str]],
    old_packages: dict[str, str],
    new_packages: dict[str, str],
    fixed: list[str],
) -> list[tuple[str, str, str, str, str]]:
    """Classify each fixed advisory key for ``--semver-check``.

    Returns ``(key, package, old_spec, new_spec, label)`` rows where label
    is one of: ``compatible`` (same major — a patch/minor bump within the
    declared range), ``major`` (major version changed — outside the
    declared range), ``removed`` (package no longer in the new tree, so no
    bump was needed), or ``unknown`` (a version could not be parsed).
    """
    rows: list[tuple[str, str, str, str, str]] = []
    for key in fixed:
        _, package, _ = old_findings.get(key, ("", "", ""))
        old_spec = old_packages.get(package, "") if package else ""
        new_spec = new_packages.get(package, "") if package else ""
        if not package or not old_spec:
            label = "unknown"
        elif package not in new_packages:
            label = "removed"
        else:
            old_version = _parse_version(old_spec)
            new_version = _parse_version(new_spec)
            if old_version is None or new_version is None:
                label = "unknown"
            elif new_version[0] != old_version[0]:
                label = "major"
            else:
                label = "compatible"
        rows.append((key, package, old_spec, new_spec, label))
    return rows


def render_semver_section(
    rows: list[tuple[str, str, str, str, str]],
) -> str:
    """Markdown ``## Semver check`` section for the delta report."""
    lines = ["## Semver check (--semver-check)", ""]
    if not rows:
        lines.append("_No fixed advisories to classify._")
    for key, package, old_spec, new_spec, label in rows:
        if label == "compatible":
            note = "semver-compatible (patch/minor within the declared range)"
        elif label == "major":
            note = "MAJOR bump (outside the declared range)"
        elif label == "removed":
            note = "dependency removed (no bump needed)"
        else:
            note = "unknown (could not parse versions)"
        old_show = old_spec or "?"
        new_show = new_spec if label != "removed" else "gone"
        lines.append(
            f"- `{key}` (package `{package}`): {old_show} -> {new_show} — {note}"
        )
    majors = sum(1 for row in rows if row[4] == "major")
    if majors:
        lines += [
            "",
            f"> **WARNING: {majors} fix{'es' if majors != 1 else ''} "
            "require(s) a major version bump (outside the declared range).**",
        ]
    return "\n".join(lines)


_PKG_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(.*)$")
_PKG_VERSION_LINE_RE = re.compile(r'("version"\s*:\s*")([^"]*)(")')


def _bump_package_version(current: str, level: str) -> str | None:
    """Next ``x.y.z`` after a ``patch``/``minor`` bump, or None.

    Preserves any suffix (``1.2.3-rc.1`` + patch -> ``1.2.4-rc.1``).
    Returns None when ``current`` is not a numeric dotted version.
    """
    match = _PKG_VERSION_RE.match((current or "").strip())
    if match is None:
        return None
    major, minor, patch, suffix = (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        match.group(4),
    )
    if level == "minor":
        return f"{major}.{minor + 1}.0{suffix}"
    return f"{major}.{minor}.{patch + 1}{suffix}"


def _bump_level_for_rows(
    rows: list[tuple[str, str, str, str, str]],
) -> str:
    """``minor`` when a compatible fix moved the minor digit, else ``patch``."""
    for _, _, old_spec, new_spec, label in rows:
        if label != "compatible":
            continue
        old_version = _parse_version(old_spec)
        new_version = _parse_version(new_spec)
        if (
            old_version is not None
            and new_version is not None
            and new_version[1] != old_version[1]
        ):
            return "minor"
    return "patch"


def _bump_commit_gate(
    *,
    semver_check: bool,
    verdict: str | None,
    rows: list[tuple[str, str, str, str, str]],
) -> str | None:
    """None when a ``--bump-commit`` version bump may proceed, else why not.

    Fail-closed: without ``--semver-check`` classification, without an
    ``improved`` verdict, with zero classifiable fixes, with any ``major``
    or ``unknown`` fix, or with only ``removed`` fixes (nothing to match a
    version against), the bump is skipped.
    """
    if not semver_check:
        return "requires --semver-check (no compatibility classification)"
    if verdict != "improved":
        return f"audit verdict is {verdict!r}, not 'improved'"
    if not rows:
        return "no fixed advisories to base the bump on"
    if any(row[4] == "major" for row in rows):
        return "a fix requires a major version bump (outside the declared range)"
    if any(row[4] == "unknown" for row in rows):
        return "a fix has an unclassifiable version (unknown)"
    if not any(row[4] == "compatible" for row in rows):
        return "no compatible fixes to base the bump on"
    return None


def _package_version_from_text(text: str, label: str) -> tuple[str | None, str | None]:
    """``(version, error)`` for ``package.json`` text from any source.

    ``label`` names the source in errors (a path, or ``<pin>:package.json``
    for ``git show`` output). ``error`` is non-None when the text is
    unparsable or carries no usable ``version`` field.
    """
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return None, f"cannot parse package.json {label} ({exc})"
    version = doc.get("version") if isinstance(doc, dict) else None
    if not isinstance(version, str) or not version.strip():
        return None, f"package.json {label} has no version field"
    if _PKG_VERSION_LINE_RE.search(text) is None:
        return None, f"package.json {label} has no version line to rewrite"
    return version, None


def _read_package_text(
    path: Path,
) -> tuple[str | None, str | None, str | None]:
    """``(text, version, error)`` for a ``package.json`` version bump.

    ``error`` is non-None when the file is absent, unreadable, unparsable,
    or carries no usable ``version`` field — every fail-closed skip path.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None, f"package.json absent: {path}"
    except OSError as exc:
        return None, None, f"cannot read package.json {path} ({exc})"
    version, error = _package_version_from_text(text, str(path))
    if error is not None:
        return None, None, error
    return text, version, None


def _rewrite_package_version(text: str, new_version: str) -> str | None:
    """``text`` with the first ``"version": "<old>"`` line set to ``new``.

    A surgical line rewrite (not a JSON re-dump) so the file's formatting
    is preserved byte-for-byte outside the version value.
    """
    match = _PKG_VERSION_LINE_RE.search(text)
    if match is None:
        return None
    return text[: match.start(2)] + new_version + text[match.end(2) :]


def _pin_matches(selector: str, key: str, package: str) -> bool:
    """True when a ``--pin`` selector targets one advisory finding.

    Match by advisory id or package name substring (case-insensitive):
    the selector hits when it occurs in the finding key (GHSA/CVE/URL)
    or in the package name.
    """
    sel = selector.lower()
    return sel in key.lower() or (bool(package) and sel in package.lower())


def _pinned_keys(
    selectors: list[str],
    old_findings: dict[str, tuple[str, str, str]],
    new_findings: dict[str, tuple[str, str, str]],
) -> set[str]:
    """Finding keys in the old|new union targeted by ``--pin`` selectors."""
    pinned: set[str] = set()
    for key in set(old_findings) | set(new_findings):
        package = new_findings[key][1] if key in new_findings else old_findings[key][1]
        if any(_pin_matches(sel, key, package) for sel in selectors):
            pinned.add(key)
    return pinned


def _pin_no_match_message(
    selectors: list[str],
    old_findings: dict[str, tuple[str, str, str]],
    new_findings: dict[str, tuple[str, str, str]],
) -> str:
    """Actionable ``--pin`` usage error listing the advisories found."""
    found = sorted(set(old_findings) | set(new_findings))
    if not found:
        listing = "no advisories in either audit (both clean)"
    else:
        parts = []
        for key in found:
            if key in new_findings:
                severity, package, _ = new_findings[key]
            else:
                severity, package, _ = old_findings[key]
            parts.append(f"`{key}` (package `{package}`, severity {severity})")
        listing = "; ".join(parts)
    sels = ", ".join(f"`{s}`" for s in selectors)
    return (
        f"--pin matched no advisories (selectors: {sels}); "
        f"advisories found: {listing}. "
        "Match by advisory id (e.g. GHSA-xxxx-yyyy-zzzz) or package name substring."
    )


def _pin_scope_status(
    key: str,
    new_keys: list[str],
    fixed: list[str],
    rescinded: list[str],
    drift: list[tuple[str, str, str]],
) -> str:
    """Where one out-of-scope advisory sits in the full delta."""
    if key in new_keys:
        return "new"
    if key in fixed:
        return "fixed"
    if key in rescinded:
        return "rescinded"
    for dkey, old_sev, new_sev in drift:
        if dkey == key:
            return f"drift {old_sev}->{new_sev}"
    return "unchanged"


def render_pin_scope_section(
    selectors: list[str],
    old_findings: dict[str, tuple[str, str, str]],
    new_findings: dict[str, tuple[str, str, str]],
    new_keys: list[str],
    fixed: list[str],
    rescinded: list[str],
    drift: list[tuple[str, str, str]],
    pinned: set[str],
) -> str:
    """Markdown ``## Out of scope for --pin`` section for the delta report."""
    out_keys = sorted((set(old_findings) | set(new_findings)) - pinned)
    sels = ", ".join(f"`{s}`" for s in selectors)
    noun = "advisory" if len(out_keys) == 1 else "advisories"
    lines = [
        "## Out of scope for --pin",
        "",
        f"Targeted (--pin): {sels}. The verdict above considers only the "
        f"pinned advisory set; {len(out_keys)} other {noun} "
        "out of scope for --pin did not affect the verdict.",
        "",
    ]
    if out_keys:
        for key in out_keys:
            if key in new_findings:
                severity, package, _ = new_findings[key]
            else:
                severity, package, _ = old_findings[key]
            status = _pin_scope_status(key, new_keys, fixed, rescinded, drift)
            lines.append(
                f"- `{key}` — severity {severity} (package `{package}`) [{status}]"
            )
    else:
        lines.append("_None._")
    return "\n".join(lines)


def _pin_deltas(
    delta_mod: Any,
    selectors: list[str],
    old_findings: dict[str, tuple[str, str, str]],
    new_findings: dict[str, tuple[str, str, str]],
    new_packages: dict[str, str],
) -> tuple[
    set[str],
    tuple[list[str], list[str], list[str], list[tuple[str, str, str]], str],
    tuple[list[str], list[str], list[str], list[tuple[str, str, str]], str],
]:
    """Full and ``--pin``-scoped deltas for one old|new finding pair.

    Returns ``(pinned, full, scoped)`` where ``full`` is
    ``compute_delta`` over all findings and ``scoped`` is the same call
    over only the selector-matched keys. The scoped verdict drives the
    exit code; the full delta feeds the out-of-scope report section.
    """
    full = delta_mod.compute_delta(old_findings, new_findings, new_packages)
    pinned = _pinned_keys(selectors, old_findings, new_findings)
    scoped_old = {k: v for k, v in old_findings.items() if k in pinned}
    scoped_new = {k: v for k, v in new_findings.items() if k in pinned}
    scoped = delta_mod.compute_delta(scoped_old, scoped_new, new_packages)
    return pinned, full, scoped


def _parse_pin_notes(
    specs: list[str],
) -> tuple[list[tuple[str, str]] | None, str | None]:
    """``(notes, error)`` for ``--pin-note`` specs (``SELECTOR=TEXT``).

    Each spec splits on the first ``=``: the left side targets advisories
    with the same case-insensitive substring logic as ``--pin`` and the
    right side is the operator note text. A spec without ``=`` (or with
    an empty selector) is a usage error.
    """
    notes: list[tuple[str, str]] = []
    for spec in specs:
        if "=" not in spec:
            return None, (
                f"malformed --pin-note {spec!r}: expected SELECTOR=TEXT "
                "(e.g. --pin-note GHSA-xxxx-yyyy-zzzz='waived: downstream only')"
            )
        selector, _, text = spec.partition("=")
        selector = selector.strip()
        if not selector:
            return None, (
                f"malformed --pin-note {spec!r}: expected SELECTOR=TEXT "
                "with a non-empty advisory id or package name selector"
            )
        notes.append((selector, text.strip()))
    return notes, None


def _note_advisories(
    selector: str,
    old_findings: dict[str, tuple[str, str, str]],
    new_findings: dict[str, tuple[str, str, str]],
) -> list[str]:
    """Sorted finding keys targeted by one ``--pin-note`` selector."""
    matched: list[str] = []
    for key in set(old_findings) | set(new_findings):
        package = new_findings[key][1] if key in new_findings else old_findings[key][1]
        if _pin_matches(selector, key, package):
            matched.append(key)
    return sorted(matched)


def render_operator_notes_section(
    notes: list[tuple[str, str]],
    old_findings: dict[str, tuple[str, str, str]],
    new_findings: dict[str, tuple[str, str, str]],
) -> str:
    """Markdown ``## Operator notes`` section for the delta report."""
    lines = [
        "## Operator notes",
        "",
        "| Advisory | Note |",
        "| --- | --- |",
    ]
    for selector, text in notes:
        matched = _note_advisories(selector, old_findings, new_findings)
        cell = text.replace("|", "\\|").replace("\n", " ")
        if matched:
            advisory = ", ".join(f"`{key}`" for key in matched)
        else:
            advisory = f"`{selector}` (no matching advisory)"
        lines.append(f"| {advisory} | {cell} |")
    return "\n".join(lines)


def _explain_match_key(
    selector: str,
    old_findings: dict[str, tuple[str, str, str]],
    new_findings: dict[str, tuple[str, str, str]],
) -> tuple[str | None, list[str]]:
    """``(chosen key, all matches)`` for one ``--explain`` selector.

    An exact (case-insensitive) advisory-key hit wins; otherwise every
    advisory-or-package substring hit (the same rule as
    :func:`_pin_matches`) counts and the first in sorted order is chosen
    so the one-pager is deterministic. No hit returns ``(None, [])``.
    """
    union = set(old_findings) | set(new_findings)
    exact = sorted(key for key in union if key.lower() == selector.lower())
    if exact:
        return exact[0], exact

    def _package(key: str) -> str:
        if key in new_findings:
            return new_findings[key][1]
        return old_findings[key][1]

    matches = sorted(key for key in union if _pin_matches(selector, key, _package(key)))
    if not matches:
        return None, []
    return matches[0], matches


def _explain_no_match_message(
    selector: str,
    old_findings: dict[str, tuple[str, str, str]],
    new_findings: dict[str, tuple[str, str, str]],
) -> str:
    """Actionable ``--explain`` usage error listing the advisories found."""
    found = sorted(set(old_findings) | set(new_findings))
    if not found:
        listing = "no advisories in either audit (both clean)"
    else:
        parts = []
        for key in found:
            if key in new_findings:
                severity, package, _ = new_findings[key]
            else:
                severity, package, _ = old_findings[key]
            parts.append(f"`{key}` (package `{package}`, severity {severity})")
        listing = "; ".join(parts)
    return (
        f"--explain matched no advisories (selector: `{selector}`); "
        f"advisories found: {listing}. "
        "Match by advisory id (e.g. GHSA-xxxx-yyyy-zzzz) or package name substring."
    )


def _explain_advisory_detail(
    doc: object, package: str, key: str
) -> tuple[str | None, str | None, str | None]:
    """``(cvss, title, url)`` for one advisory from a raw npm audit doc.

    Scans the ``via`` objects of the package's ``vulnerabilities`` entry
    (npm audit shape: each carries ``title``/``url``/``cvss``) and the
    legacy ``advisories`` map. Anything absent stays None so callers
    render "not present in the audit data".
    """
    if not isinstance(doc, dict):
        return None, None, None
    records: list[dict] = []
    vulnerabilities = doc.get("vulnerabilities")
    if isinstance(vulnerabilities, dict):
        entry = vulnerabilities.get(package)
        if isinstance(entry, dict):
            via = entry.get("via")
            if isinstance(via, list):
                records.extend(item for item in via if isinstance(item, dict))
    advisories = doc.get("advisories")
    if isinstance(advisories, dict):
        for advisory in advisories.values():
            if (
                isinstance(advisory, dict)
                and str(advisory.get("module_name") or "") == package
            ):
                records.append(advisory)
    is_package_key = bool(package) and key.lower() == package.lower()
    for record in records:
        haystack = " ".join(
            str(record.get(field) or "")
            for field in ("url", "name", "title", "id", "source", "cves")
        )
        if key.lower() not in haystack.lower() and not (
            is_package_key
            and any(record.get(field) for field in ("title", "url", "cvss"))
        ):
            continue
        cvss = record.get("cvss")
        if isinstance(cvss, dict):
            score = cvss.get("score")
        elif isinstance(cvss, (int, float, str)):
            score = cvss
        else:
            score = None
        title = record.get("title")
        url = record.get("url")
        return (
            str(score) if score is not None and str(score) else None,
            str(title) if title else None,
            str(url) if url else None,
        )
    return None, None, None


#: The ``--rollback`` command line rendered in the ROLLBACK section of the
#: ``--explain`` one-pager (module invocation follows the repo convention
#: used across ``omega_prime/grokbot``).
_EXPLAIN_ROLLBACK_CMD = "python -m omega_prime.grokbot.pin_bump --rollback"


def render_explain_section(
    *,
    selector: str,
    key: str,
    matches: list[str],
    old_findings: dict[str, tuple[str, str, str]],
    new_findings: dict[str, tuple[str, str, str]],
    old_packages: dict[str, str],
    new_packages: dict[str, str],
    old_doc: object,
    new_doc: object,
    fixed: list[str],
    new_keys: list[str],
    rescinded: list[str],
    drift: list[tuple[str, str, str]],
    old_pin: str,
    new_pin: str,
) -> tuple[str, dict[str, Any]]:
    """Deterministic markdown one-pager + JSON object for ``--explain``.

    WHAT names the package, the old->new pinned versions, the severity,
    and the CVSS score when the audit data carries one; WHY summarizes
    detection from the two audit docs; FIX states the exact pin-bump
    action plus the :func:`classify_semver_fixes` label (``None`` when
    the advisory is not a fixed one); ROLLBACK prints the ``--rollback``
    command line that would revert the bump.
    """
    package = new_findings[key][1] if key in new_findings else old_findings[key][1]
    old_severity = old_findings[key][0] if key in old_findings else None
    new_severity = new_findings[key][0] if key in new_findings else None
    old_spec = old_packages.get(package, "")
    new_spec = new_packages.get(package, "")
    status = _pin_scope_status(key, new_keys, fixed, rescinded, drift)
    cvss: str | None = None
    title: str | None = None
    advisory_url: str | None = None
    for doc in (new_doc, old_doc):
        found_cvss, found_title, found_url = _explain_advisory_detail(doc, package, key)
        cvss = cvss or found_cvss
        title = title or found_title
        advisory_url = advisory_url or found_url
    if key in fixed:
        rows = classify_semver_fixes(old_findings, old_packages, new_packages, [key])
        semver_label = rows[0][4] if rows else "unknown"
    else:
        semver_label = None
    if old_severity is not None and new_severity is not None:
        severity_line = f"{old_severity} -> {new_severity}"
    elif old_severity is not None:
        severity_line = f"{old_severity} (old audit only)"
    else:
        severity_line = f"{new_severity} (new audit only)"
    old_show = old_spec or "?"
    if package in new_packages:
        new_show = new_spec or "?"
    else:
        new_show = "gone (dependency removed)"
    if title and advisory_url:
        evidence = f": {title} ({advisory_url})"
    elif title:
        evidence = f": {title}"
    elif advisory_url:
        evidence = f" ({advisory_url})"
    else:
        evidence = ""
    if status == "fixed":
        why = (
            f"Detected in the old-pin audit (severity {old_severity}, "
            f"range `{old_spec or '?'}`){evidence}; absent from the new-pin "
            "audit — this bump fixes it."
        )
        fix = (
            f"Pin bump {old_pin} -> {new_pin} fixes `{key}` "
            f"(package `{package}`: {old_show} -> {new_show})."
        )
    elif status == "new":
        why = (
            "Absent from the old-pin audit; detected in the new-pin audit "
            f"(severity {new_severity}, range `{new_spec or '?'}`){evidence} "
            "— this bump introduces it."
        )
        fix = (
            f"Pin bump {old_pin} -> {new_pin} introduces `{key}` — hold the "
            "bump or triage the advisory before proceeding."
        )
    elif status == "rescinded":
        why = (
            f"Flagged in the old-pin audit (severity {old_severity}){evidence}; "
            "no longer reported at the new pin with the package spec unchanged "
            "— rescinded, not a code fix."
        )
        fix = (
            f"Pin bump {old_pin} -> {new_pin} clears the `{key}` flag with no "
            "version change (rescinded)."
        )
    elif status.startswith("drift"):
        why = (
            "Present in both audits; severity drifted "
            f"{old_severity}->{new_severity}{evidence} — reviewer attention needed."
        )
        fix = (
            f"Pin bump {old_pin} -> {new_pin} leaves `{key}` in place "
            f"(status: {status}); no per-advisory action."
        )
    else:
        why = (
            f"Present in both audits (severity {new_severity or old_severity})"
            f"{evidence} — the bump leaves it unchanged."
        )
        fix = (
            f"Pin bump {old_pin} -> {new_pin} leaves `{key}` in place "
            f"(status: {status}); no per-advisory action."
        )
    if semver_label == "compatible":
        semver_line = (
            "Semver: semver-compatible (patch/minor within the declared range)"
        )
    elif semver_label == "major":
        semver_line = "Semver: MAJOR bump (outside the declared range)"
    elif semver_label == "removed":
        semver_line = "Semver: dependency removed (no bump needed)"
    elif semver_label == "unknown":
        semver_line = "Semver: unknown (could not parse versions)"
    else:
        semver_line = (
            "Semver: n/a (not a fixed advisory — the classifier only covers fixes)"
        )
    if len(matches) > 1:
        matched = (
            f"Selector: `{selector}` matched {len(matches)} advisories "
            f"(showing `{key}`): " + ", ".join(f"`{match}`" for match in matches)
        )
    else:
        matched = f"Selector: `{selector}` matched 1 advisory."
    lines = [
        f"## Explain: `{key}`",
        "",
        matched,
        "",
        "### WHAT",
        "",
        f"- Package: `{package}`",
        f"- Severity: {severity_line}",
        f"- Pinned versions: {old_show} -> {new_show}",
        f"- CVSS: {cvss if cvss is not None else '_not present in the audit data_'}",
        "",
        "### WHY",
        "",
        f"- {why}",
        "",
        "### FIX",
        "",
        f"- {fix}",
        f"- {semver_line}",
        "",
        "### ROLLBACK",
        "",
        f"- Revert this bump with: `{_EXPLAIN_ROLLBACK_CMD}`",
    ]
    payload = {
        "selector": selector,
        "key": key,
        "matched": list(matches),
        "package": package,
        "status": status,
        "severity_old": old_severity,
        "severity_new": new_severity,
        "spec_old": old_spec,
        "spec_new": new_spec,
        "cvss": cvss,
        "title": title,
        "url": advisory_url,
        "semver": semver_label,
        "fix": fix,
        "rollback": _EXPLAIN_ROLLBACK_CMD,
    }
    return "\n".join(lines), payload


def _register_pin_bump_schedule(
    cron_expr: str,
    *,
    name: str,
    root: Path,
    config: dict,
) -> int:
    """Register a recurring pin-bump audit. Returns the exit code.

    Fail-closed: an invalid cron expression (or an empty schedule
    name) is a usage error (exit 2) that registers nothing and runs
    no subprocess. A duplicate ``name`` replaces the previous job.
    """
    from omega_prime.cron import scheduler as scheduler_mod

    try:
        expr = scheduler_mod.validate_pin_bump_cron(cron_expr)
    except ValueError as exc:
        print(f"pin-bump usage error: --schedule {exc}", file=sys.stderr)
        return EXIT_USAGE
    if not isinstance(name, str) or not name.strip():
        print(
            "pin-bump usage error: --schedule-name requires a non-empty name",
            file=sys.stderr,
        )
        return EXIT_USAGE
    try:
        store = scheduler_mod.JobStore(Path(root) / "cron" / "jobs.json")
        job_id = scheduler_mod.schedule_pin_bump(
            store, cron_expr=expr, name=name.strip(), config=config
        )
    except ValueError as exc:
        print(f"pin-bump usage error: --schedule {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(
        f"pin-bump scheduled: {name.strip()!r} ({expr}) -> job {job_id} ({store.path})"
    )
    return EXIT_OK


_SCHEDULED_VERDICT_RE = re.compile(
    r"^\[audit-only\] audit verdict: (\S+)", re.MULTILINE
)
_SCHEDULED_SEMVER_RE = re.compile(r"^\[audit-only\] semver (\w+):", re.MULTILINE)


def audit_scheduled_job(
    config: dict | None,
    *,
    root: Path = REPO_ROOT,
    run: Runner | None = None,
) -> dict:
    """One scheduled tick's audit with audit-only semantics (no mutations).

    Re-runs the audit chain for the stored argv echo and returns
    ``{"verdict", "semver_compatible", "exit_code"}``. ``verdict`` is
    the audit delta verdict (or ``"error"`` when the chain never
    reached a verdict); ``semver_compatible`` reuses the
    :func:`_bump_commit_gate` policy when the stored config enables
    ``--semver-check`` and is True otherwise. Never raises:
    a broken chain reports ``"error"`` fail-closed.
    """
    if not isinstance(config, dict):
        return {"verdict": "error", "semver_compatible": False, "exit_code": EXIT_USAGE}
    to = config.get("to")
    if not isinstance(to, str) or not to:
        return {"verdict": "error", "semver_compatible": False, "exit_code": EXIT_USAGE}
    semver_check = bool(config.get("semver_check"))
    pin_note_specs = config.get("pin_note")
    pin_selectors = config.get("pin")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exit_code = bump(
                to,
                root=Path(root),
                run=run,
                old_audit=config.get("old_audit"),
                semver_check=semver_check,
                bump_commit=False,
                auto_detect_upstream_fix=bool(config.get("auto_detect_upstream_fix")),
                open_pr=False,
                json_mode=False,
                dry_run=False,
                audit_only=True,
                interactive=False,
                pin=list(pin_selectors) if isinstance(pin_selectors, list) else None,
                pin_note=(
                    list(pin_note_specs) if isinstance(pin_note_specs, list) else None
                ),
            )
    except Exception as exc:
        return {
            "verdict": "error",
            "semver_compatible": False,
            "exit_code": EXIT_DEGRADED,
            "error": f"{type(exc).__name__}: {exc}",
        }
    out = buf.getvalue()
    match = _SCHEDULED_VERDICT_RE.search(out)
    verdict = match.group(1) if match else "error"
    semver_compatible = True
    if semver_check:
        rows = [("", "", "", "", label) for label in _SCHEDULED_SEMVER_RE.findall(out)]
        semver_compatible = (
            _bump_commit_gate(semver_check=True, verdict=verdict, rows=rows) is None
        )
    return {
        "verdict": verdict,
        "semver_compatible": semver_compatible,
        "exit_code": exit_code,
    }


def apply_scheduled_bump(
    config: dict,
    *,
    root: Path = REPO_ROOT,
    run: Runner | None = None,
) -> int:
    """Open the normal bump PR path for one scheduled run.

    Replays the stored argv echo as a real (mutating) bump with
    ``--open-pr`` forced on — the tick only calls this after the
    improved + semver-compatible gate passes. Returns the exit code.
    """
    pin_note_specs = config.get("pin_note")
    pin_selectors = config.get("pin")
    stored_report_to = config.get("report_to")
    return bump(
        config.get("to", ""),
        root=Path(root),
        run=run,
        old_audit=config.get("old_audit"),
        semver_check=bool(config.get("semver_check")),
        bump_commit=bool(config.get("bump_commit")),
        auto_detect_upstream_fix=bool(config.get("auto_detect_upstream_fix")),
        open_pr=True,
        json_mode=False,
        dry_run=False,
        audit_only=False,
        interactive=False,
        pin=list(pin_selectors) if isinstance(pin_selectors, list) else None,
        pin_note=(list(pin_note_specs) if isinstance(pin_note_specs, list) else None),
        # Fail-closed replay: a stored path that is not a string is passed
        # through as-is so bump() rejects it instead of dropping the flag.
        report_to=stored_report_to,
    )


_ROLLBACK_REPORT_OLD_RE = re.compile(r"^Old:\s*`([^`]*)`", re.MULTILINE)
_ROLLBACK_AUDIT_PIN_RE = re.compile(r"redteam-audit-([0-9a-fA-F]{40})\.json")

#: RoE lifecycle states that count as an active grant for ``--rollback``
#: (mirrors ``roe_lifecycle.READY_LIFECYCLE_STATES`` without importing it).
_ROLLBACK_ROE_ACTIVE_STATES = ("valid", "expiring-7d", "expiring-1d")

#: Inline-document keys that may carry the artifact id for the
#: ``grant-revoked`` audit scan (mirrors ``roe_lifecycle`` candidates).
_ROLLBACK_ROE_ID_KEYS = ("artifact_id", "id", "artifact_path", "path")


def _find_rollback_target(
    run: Runner, root: Path
) -> tuple[str | None, str | None, list[str]]:
    """Previous pin from the last bump record.

    Returns ``(old_pin, source, looked_at)``: ``old_pin`` is the
    lower-cased 40-hex pin to roll back to (or None), ``source`` names
    the winning record, and ``looked_at`` lists every location probed
    (reported when no record exists). The delta report's ``Old:``
    audit label wins — the default saved artifact embeds the pre-bump
    pin as ``redteam-audit-<pin>.json``; otherwise the pin is read from
    the parent commit of the most recent ``VENDOR.md`` change.
    """
    looked_at: list[str] = []
    report_path = root / "report.md"
    try:
        report_text = report_path.read_text(encoding="utf-8")
    except OSError as exc:
        looked_at.append(f"{report_path} (unreadable: {exc})")
    else:
        old_match = _ROLLBACK_REPORT_OLD_RE.search(report_text)
        if old_match is None:
            looked_at.append(f"{report_path} (no 'Old:' audit label)")
        else:
            pin_match = _ROLLBACK_AUDIT_PIN_RE.search(old_match.group(1))
            if pin_match is None:
                looked_at.append(
                    f"{report_path} (Old label {old_match.group(1)!r} "
                    "carries no redteam-audit-<pin>.json)"
                )
            else:
                source = f"{report_path} (Old audit label)"
                looked_at.append(source)
                return pin_match.group(1).lower(), source, looked_at
    log_argv = ["git", "log", "--format=%H", "-n", "1", "--", "VENDOR.md"]
    try:
        logged = run(log_argv, cwd=root)
    except OSError as exc:
        looked_at.append(f"git history of VENDOR.md (git unavailable: {exc})")
        return None, None, looked_at
    if logged.returncode != 0:
        looked_at.append(
            f"git history of VENDOR.md (git log failed: {logged.stderr.strip()})"
        )
        return None, None, looked_at
    touched = (logged.stdout or "").split()
    if not touched:
        looked_at.append("git history of VENDOR.md (no commits touch VENDOR.md)")
        return None, None, looked_at
    commit = touched[0]
    show_argv = ["git", "show", f"{commit}^:VENDOR.md"]
    try:
        shown = run(show_argv, cwd=root)
    except OSError as exc:
        looked_at.append(f"git history of VENDOR.md (git show failed: {exc})")
        return None, None, looked_at
    if shown.returncode != 0:
        looked_at.append(
            f"git history of VENDOR.md (git show {commit[:7]}^:VENDOR.md "
            f"failed: {shown.stderr.strip()})"
        )
        return None, None, looked_at
    section = _vendor_section(shown.stdout or "")
    pin = provenance.parse_vendor_pin(section) if section is not None else None
    if pin is None or not SHA_RE.match(pin):
        looked_at.append(
            f"git history of VENDOR.md ({commit[:7]}^:VENDOR.md "
            "carries no parseable pin)"
        )
        return None, None, looked_at
    source = f"git history of VENDOR.md ({commit[:7]}^:VENDOR.md)"
    looked_at.append(source)
    return pin.lower(), source, looked_at


def _rollback_roe_context(job: Any) -> tuple[Any, Any]:
    """``(artifact, audit_log)`` from a tick/job context.

    Reads the heartbeat RoE key convention (``context``/job-level
    ``roe_artifact``/``roe_artifact_path`` plus the plural forms) and
    the audit-log path (``roe_audit_log``/``audit_log``/
    ``roe_audit_log_path``). Both None when no RoE is configured.
    """
    if not isinstance(job, dict):
        return None, None
    context = job.get("context")
    artifact = None
    if isinstance(context, dict):
        artifact = context.get("roe_artifact", context.get("roe_artifact_path"))
        if artifact is None:
            artifact = context.get("roe_artifacts", context.get("roe_artifact_paths"))
    if artifact is None:
        artifact = job.get("roe_artifact", job.get("roe_artifact_path"))
        if artifact is None:
            artifact = job.get("roe_artifacts", job.get("roe_artifact_paths"))
    log_path = None
    for scope in (context, job):
        if isinstance(scope, dict):
            for key in ("roe_audit_log", "audit_log", "roe_audit_log_path"):
                candidate = scope.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    log_path = candidate.strip()
                    break
        if log_path is not None:
            break
    return artifact, log_path


def _rollback_roe_check(job: Any) -> str | None:
    """None when a rollback may proceed, else the RoE block reason.

    Fail-open without RoE: no artifact in the tick/job context means
    local-run semantics (proceed). A present artifact must be an active
    grant — window open per :func:`roe_lifecycle.watchdog_check` and
    absent from the ``grant-revoked`` audit rows the lifecycle seams
    consult.
    """
    artifact, log_path = _rollback_roe_context(job)
    if artifact is None:
        return None
    grants = list(artifact) if isinstance(artifact, (list, tuple)) else [artifact]
    grants = [
        grant
        for grant in grants
        if not (grant is None or (isinstance(grant, str) and not grant.strip()))
    ]
    if not grants:
        return None
    from omega_prime.grokbot import roe_lifecycle as roe_lifecycle_mod
    from omega_prime.redteam import roe_audit as roe_audit_mod

    now = datetime.now(UTC)
    for grant in grants:
        result = roe_lifecycle_mod.watchdog_check(grant, now=now)
        state = result.get("state")
        artifact_id = result.get("artifact_id", "unknown-artifact")
        if state not in _ROLLBACK_ROE_ACTIVE_STATES:
            return (
                f"RoE grant {artifact_id!r} is not active "
                f"(state {state}; action: {result.get('action')})"
            )
        candidates = {str(artifact_id)}
        if isinstance(grant, (str, Path)) and str(grant).strip():
            candidates.add(str(grant).strip())
        if isinstance(grant, dict):
            for key in _ROLLBACK_ROE_ID_KEYS:
                candidate = grant.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    candidates.add(candidate.strip())
        revoked = set(roe_audit_mod.list_revoked(log_path))
        if candidates & revoked:
            where = f" (see audit log {log_path})" if log_path else ""
            return f"RoE grant {artifact_id!r} revoked{where} — re-sign to renew"
    return None


def _rollback_package_versions(
    run: Runner, redteam_dir: Path, old_sha: str
) -> tuple[str | None, str | None, str | None, str | None]:
    """``(current, old, note, warning)`` for the rollback version restore.

    Read-only: compares the working-tree ``redteam/package.json`` version
    against the version committed at ``old_sha``. ``note`` is
    ``"<current> -> <old>"`` when a restore applies; ``warning``
    explains why the restore cannot be confirmed (fail-open, the caller
    warns and continues); both None when the tree already matches the
    old pin (no ``--bump-commit`` to undo, or the checkout already
    restored it) or when no ``package.json`` exists.
    """
    pkg_path = redteam_dir / "package.json"
    if not pkg_path.is_file():
        return None, None, None, None
    try:
        text = pkg_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, None, None, f"cannot read {pkg_path} ({exc})"
    current, error = _package_version_from_text(text, str(pkg_path))
    if error is not None:
        return None, None, None, error
    try:
        shown = run(["git", "show", f"{old_sha}:package.json"], cwd=redteam_dir)
    except OSError as exc:
        return (
            current,
            None,
            None,
            (f"cannot show package.json at {old_sha[:7]} ({exc})"),
        )
    if shown.returncode != 0:
        return (
            current,
            None,
            None,
            (f"cannot show package.json at {old_sha[:7]}: {shown.stderr.strip()}"),
        )
    old, old_error = _package_version_from_text(
        shown.stdout or "", f"{old_sha[:7]}:package.json"
    )
    if old_error is not None:
        return current, None, None, old_error
    if old == current:
        return current, old, None, None
    return current, old, f"{current} -> {old}", None


def _verify_post_bump(
    run: Runner,
    redteam_dir: Path,
    *,
    old_findings: dict[str, tuple[str, str, str]],
    old_packages: dict[str, str],
    semver_check: bool,
    pin_selectors: list[str],
    baseline_rows: list[tuple[str, str, str, str, str]],
) -> tuple[bool, str | None, list[str], list[tuple[str, str, str, str, str]]]:
    """Re-run the audit chain against the NEW pin for ``--verify``.

    Returns ``(passed, verdict, notes, rows)``: ``passed`` is True only
    when the re-audit shows no untriaged high/critical advisories, the
    delta verdict against the pre-bump findings is ``improved``, and —
    with ``--semver-check`` — the re-computed per-advisory
    classification matches ``baseline_rows``. ``notes`` are the
    human-readable verification lines the caller prints either way (a
    failed verification exits 4 carrying them, with no PR).
    """
    notes: list[str] = []
    try:
        proc = run(
            ["npm", "audit", "--omit=dev", "--audit-level=high", "--json"],
            cwd=redteam_dir,
        )
    except OSError as exc:
        return False, None, [f"verification FAILED: npm is absent ({exc})"], []
    try:
        new_doc = json.loads(proc.stdout)
    except ValueError as exc:
        return (
            False,
            None,
            [f"verification FAILED: cannot parse npm audit output ({exc})"],
            [],
        )
    try:
        triage_mod = _load_script("redteam-audit-triage.py")
    except (OSError, RuntimeError) as exc:
        return (
            False,
            None,
            [f"verification FAILED: cannot load audit triage helper ({exc})"],
            [],
        )
    findings, triage_error = triage_mod.triage(new_doc)
    if triage_error is not None:
        return (
            False,
            None,
            [f"verification FAILED: audit triage refused output: {triage_error}"],
            [],
        )
    untriaged = [
        finding for finding in findings if finding[0] not in triage_mod.TRIAGED
    ]
    if pin_selectors:
        pinned_keys = {
            key
            for key, _, name in findings
            if any(_pin_matches(sel, key, str(name or "")) for sel in pin_selectors)
        }
        untriaged = [finding for finding in untriaged if finding[0] in pinned_keys]
    if untriaged:
        notes.append(
            "verification FAILED: untriaged high/critical advisories at new pin:"
        )
        notes.extend(f"  {severity} {name}: {key}" for key, severity, name in untriaged)
        return False, None, notes, []
    try:
        delta_mod = _load_script("redteam-audit-delta.py")
    except (OSError, RuntimeError) as exc:
        return (
            False,
            None,
            [f"verification FAILED: cannot load audit delta helper ({exc})"],
            [],
        )
    new_findings, new_packages, new_error = delta_mod.parse_input(new_doc)
    if new_error is not None:
        return (
            False,
            None,
            [f"verification FAILED: new audit unusable: {new_error}"],
            [],
        )
    _, fixed, _, _, verdict = delta_mod.compute_delta(
        old_findings, new_findings, new_packages
    )
    if pin_selectors:
        pinned, _, scoped = _pin_deltas(
            delta_mod, pin_selectors, old_findings, new_findings, new_packages
        )
        if not pinned:
            return (
                False,
                None,
                ["verification FAILED: --pin matched no advisories"],
                [],
            )
        (_, fixed, _, _, verdict) = scoped
    if verdict != "improved":
        return (
            False,
            verdict,
            [f"verification FAILED: verdict is {verdict!r}, not 'improved'"],
            [],
        )
    rows: list[tuple[str, str, str, str, str]] = []
    if semver_check:
        rows = classify_semver_fixes(old_findings, old_packages, new_packages, fixed)
        baseline_labels = {key: label for key, _, _, _, label in baseline_rows}
        verify_labels = {key: label for key, _, _, _, label in rows}
        if verify_labels != baseline_labels:
            return (
                False,
                verdict,
                [
                    "verification FAILED: semver classification changed "
                    f"since the bump audit (was {baseline_labels}, "
                    f"now {verify_labels})"
                ],
                rows,
            )
        notes.append(
            f"verification green: verdict improved, semver holds "
            f"({len(rows)} advisories classified)"
        )
    else:
        notes.append(
            "verification green: verdict improved (no untriaged high/critical)"
        )
    return True, verdict, notes, rows


def _resolve_report_to(raw: object) -> tuple[Path | None, str | None]:
    """Validate ``--report-to`` and return ``(destination, error)``.

    A relative path resolves against the current working directory and
    the parent directory is resolved through symlinks, but the final
    component is never followed: a symlink, a directory, or any other
    non-regular file is refused (fail-closed). Missing parent directories
    are allowed (created at write time) as long as the nearest existing
    ancestor is a directory. Pure: touches nothing on disk.
    """
    if not isinstance(raw, str):
        return None, "--report-to must be a path string"
    if not raw.strip():
        return None, "--report-to requires a non-empty path"
    if "\x00" in raw:
        return None, "--report-to path contains a NUL byte"
    given = Path(raw)
    if given.name in ("", ".", ".."):
        return None, f"--report-to {raw!r} does not name a file"
    absolute = given if given.is_absolute() else Path.cwd() / given
    target = Path(os.path.realpath(absolute.parent)) / absolute.name
    ancestor = target.parent
    while not os.path.lexists(ancestor) and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        return None, f"--report-to parent {ancestor} is not a directory"
    try:
        mode = os.lstat(target).st_mode
    except FileNotFoundError:
        return target, None
    except OSError as exc:
        return None, f"--report-to {target} is not accessible ({exc})"
    if stat.S_ISLNK(mode):
        return None, f"--report-to {target} is a symlink (refusing to follow it)"
    if stat.S_ISDIR(mode):
        return None, f"--report-to {target} is a directory"
    if not stat.S_ISREG(mode):
        return None, f"--report-to {target} is not a regular file"
    return target, None


def _write_report_atomic(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` atomically (sibling temp + ``os.replace``).

    The file ends up mode 0644 like the default ``report.md``; missing
    parent directories are created with default permissions and existing
    ones are never chmodded. A failed write leaves no temp file behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def bump(
    to: str | None,
    *,
    root: Path = REPO_ROOT,
    run: Runner | None = None,
    old_audit: str | None = None,
    from_audit: str | None = None,
    skip_audit: bool = False,
    semver_check: bool = False,
    bump_commit: bool = False,
    auto_detect_upstream_fix: bool = False,
    open_pr: bool = False,
    json_mode: bool = False,
    dry_run: bool = False,
    audit_only: bool = False,
    interactive: bool = False,
    pin: list[str] | None = None,
    pin_note: list[str] | None = None,
    explain: str | None = None,
    schedule: str | None = None,
    schedule_name: str = "pin-bump",
    rollback: bool = False,
    verify: bool = False,
    report_to: str | None = None,
    job: dict | None = None,
) -> int:
    """Run the pin-bump runbook against ``root``. Returns the exit code."""
    if rollback:
        # Mode conflicts fail closed before any other validation: the
        # audit-mode flags are inert on the rollback path (no audit
        # chain runs), while --to/--audit-only/--schedule name a
        # different target or mode. The schedule check below also
        # guards the non-rollback registration path.
        if to:
            print(
                "pin-bump usage error: --rollback derives the target from "
                "the last bump record; drop --to",
                file=sys.stderr,
            )
            return EXIT_USAGE
        if audit_only:
            print(
                "pin-bump usage error: --rollback never audits, so it cannot "
                "be combined with --audit-only",
                file=sys.stderr,
            )
            return EXIT_USAGE
        if schedule is not None:
            print(
                "pin-bump usage error: --rollback cannot be combined with --schedule",
                file=sys.stderr,
            )
            return EXIT_USAGE
    report_dest_resolved: Path | None = None
    if report_to is not None:
        # Fail closed before any subprocess: --report-to only makes sense
        # for the modes that write a delta report.
        report_mode_conflict = (
            "--rollback" if rollback else "--audit-only" if audit_only else None
        )
        if report_mode_conflict is not None:
            print(
                f"pin-bump usage error: --report-to cannot be combined with "
                f"{report_mode_conflict} (that mode never writes a report)",
                file=sys.stderr,
            )
            return EXIT_USAGE
        report_dest_resolved, report_to_error = _resolve_report_to(report_to)
        if report_to_error is not None:
            print(f"pin-bump usage error: {report_to_error}", file=sys.stderr)
            return EXIT_USAGE
    if schedule is not None:
        if not to:
            print(
                "pin-bump usage error: --schedule needs --to (the bump target)",
                file=sys.stderr,
            )
            return EXIT_USAGE
        # Registration path: no subprocess, no mutation — validate the
        # cron expression fail-closed and store the argv echo as the job
        # config. Every other flag is recorded, not executed.
        return _register_pin_bump_schedule(
            schedule,
            name=schedule_name,
            root=root,
            config={
                "to": to,
                "old_audit": old_audit,
                "from_audit": from_audit,
                "semver_check": semver_check,
                "bump_commit": bump_commit,
                "auto_detect_upstream_fix": auto_detect_upstream_fix,
                "open_pr": open_pr,
                "pin": [s.strip() for s in (pin or []) if s is not None],
                "pin_note": [s for s in (pin_note or []) if s is not None],
                "explain": explain.strip() if isinstance(explain, str) else None,
                "verify": verify,
                **(
                    {"report_to": str(report_dest_resolved)}
                    if report_dest_resolved is not None
                    else {}
                ),
            },
        )
    if semver_check and skip_audit:
        print(
            "pin-bump usage error: --semver-check needs audit data (drop --skip-audit)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if verify and skip_audit:
        print(
            "pin-bump usage error: --verify needs audit data (drop --skip-audit)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if audit_only and open_pr:
        print(
            "pin-bump usage error: --audit-only never mutates, so it cannot "
            "be combined with --open-pr",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if audit_only and skip_audit:
        print(
            "pin-bump usage error: --audit-only needs audit data (drop --skip-audit)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if from_audit and skip_audit:
        print(
            "pin-bump usage error: --from-audit provides the new audit "
            "(drop --skip-audit)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if from_audit and not old_audit:
        print(
            "pin-bump usage error: --from-audit needs --old-audit <path> "
            "for the old-pin audit",
            file=sys.stderr,
        )
        return EXIT_USAGE
    pin_selectors = [s.strip() for s in (pin or []) if s is not None]
    if any(not s for s in pin_selectors):
        print(
            "pin-bump usage error: --pin requires a non-empty advisory id "
            "or package name",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if pin_selectors and skip_audit:
        print(
            "pin-bump usage error: --pin needs audit data (drop --skip-audit)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    pin_note_specs = [s for s in (pin_note or []) if s is not None]
    pin_notes, pin_notes_error = _parse_pin_notes(pin_note_specs)
    if pin_notes_error is not None:
        print(f"pin-bump usage error: {pin_notes_error}", file=sys.stderr)
        return EXIT_USAGE
    assert pin_notes is not None
    if pin_notes and skip_audit:
        print(
            "pin-bump usage error: --pin-note needs audit data (drop --skip-audit)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    explain_selector = explain.strip() if isinstance(explain, str) else None
    if explain is not None and not explain_selector:
        print(
            "pin-bump usage error: --explain requires a non-empty advisory id "
            "or package name",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if explain_selector and skip_audit:
        print(
            "pin-bump usage error: --explain needs audit data (drop --skip-audit)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if explain_selector and rollback:
        print(
            "pin-bump usage error: --explain needs audit data; --rollback "
            "never audits, so they cannot be combined",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if pin_notes and rollback:
        print(
            "pin-bump usage error: --pin-note needs audit data; --rollback "
            "never audits, so they cannot be combined",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if report_dest_resolved is not None and skip_audit:
        print(
            "pin-bump usage error: --report-to has nothing to write with "
            "--skip-audit (no report is produced)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if run is None:
        run = _default_run
    redteam_dir = root / "redteam"
    done: list[str] = []
    verdict: str | None = None
    report_path: Path | None = None
    report_dest: Path | None = report_dest_resolved
    report_label = "report.md" if report_dest is None else str(report_dest)
    report_dest_created = False
    sha: str | None = None

    version_bump_note: str | None = None

    # --json payload state. ``json_fixed``/``json_old_findings``/
    # ``json_old_packages``/``json_new_packages`` describe the audit delta;
    # ``json_semver`` is the per-advisory classification (set only when
    # --semver-check ran); ``json_bump`` describes the --bump-commit
    # outcome (set only when the gate was evaluated).
    json_fixed: list[str] = []
    json_old_findings: dict[str, tuple[str, str, str]] = {}
    json_new_findings: dict[str, tuple[str, str, str]] = {}
    json_old_packages: dict[str, str] = {}
    json_new_packages: dict[str, str] = {}
    json_semver: dict[str, str] | None = None
    json_bump: dict[str, Any] | None = None
    # --pin payload state. ``json_out_of_scope`` lists advisories outside
    # the pinned set (set only when --pin ran); unpinned runs leave it
    # None so the default payload shape is unchanged.
    json_out_of_scope: list[dict[str, Any]] | None = None
    # --explain payload state. ``json_explain`` is the one-pager object
    # (set when --explain resolved an advisory); explained runs leave the
    # default payload shape unchanged.
    json_explain: dict[str, Any] | None = None
    # --verify payload state. ``json_verify`` is the post-bump smoke
    # object (``ran`` stays False until the verification audit executes;
    # dry-run and early exits keep it un-run). Only included in the
    # --json payload when the flag is set.
    json_verify: dict[str, Any] | None = (
        {"ran": False, "verdict": None, "passed": None} if verify else None
    )
    # Pre-initialised so --skip-audit --bump-commit skips with a warning
    # instead of raising NameError on the unbound ``semver_rows`` below.
    semver_rows: list[tuple[str, str, str, str, str]] = []

    _stdout = sys.stdout
    if json_mode:
        # Machine-readable mode: stdout carries exactly one JSON object
        # (emitted by finish()); all human-readable progress — including
        # output from helpers reached via run()/provenance — goes to
        # stderr instead.
        sys.stdout = sys.stderr

    def _json_payload(code: int) -> dict[str, Any]:
        """Machine-readable delta report for ``--json`` (keys sorted on dump)."""
        labels = dict(json_semver) if json_semver else {}
        rows = []
        for key in sorted(json_fixed):
            _, package, _ = json_old_findings.get(key, ("", "", ""))
            rows.append(
                {
                    "key": key,
                    "package": package,
                    "old": json_old_packages.get(package, "") if package else "",
                    "new": json_new_packages.get(package, "") if package else "",
                    "label": labels.get(key),
                }
            )
        return {
            "verdict": verdict,
            "rows": rows,
            "semver": (
                {key: labels[key] for key in sorted(labels)}
                if json_semver is not None
                else None
            ),
            "bump": json_bump,
            "exit_code": code,
            **(
                {
                    "pinned": list(pin_selectors),
                    "out_of_scope": json_out_of_scope or [],
                }
                if pin_selectors
                else {}
            ),
            **(
                {
                    "notes": [
                        {
                            "selector": selector,
                            "note": text,
                            "advisories": _note_advisories(
                                selector, json_old_findings, json_new_findings
                            ),
                        }
                        for selector, text in pin_notes
                    ]
                }
                if pin_notes
                else {}
            ),
            **({"explain": json_explain} if explain_selector else {}),
            **({"verify": json_verify} if verify else {}),
            **({"report": str(report_dest)} if report_dest is not None else {}),
        }

    def finish(code: int) -> int:
        if json_mode:
            try:
                print(json.dumps(_json_payload(code), sort_keys=True), file=_stdout)
            finally:
                sys.stdout = _stdout
        return code

    def usage(message: str) -> int:
        print(f"pin-bump usage error: {message}", file=sys.stderr)
        return finish(EXIT_USAGE)

    def degraded(message: str) -> int:
        print(
            f"pin-bump degraded: {message}; "
            f"completed: {', '.join(done) if done else 'none'}",
            file=sys.stderr,
        )
        return finish(EXIT_DEGRADED)

    def failed(message: str) -> int:
        print(f"pin-bump FAILED: {message}")
        return finish(EXIT_BUMP_FAILED)

    # --rollback (M9): re-pin to the previous version from the last bump
    # record, restore the old lockfile entry (and the package.json
    # version when --bump-commit was part of the original bump), and open
    # a revert PR. No audit chain runs; report.md is left untouched so it
    # stays the record a future rollback reads.
    if rollback:
        roe_block = _rollback_roe_check(job)
        if roe_block is not None:
            return failed(f"rollback blocked: {roe_block}")
        try:
            rb_vendor_text = (root / "VENDOR.md").read_text(encoding="utf-8")
        except OSError as exc:
            return failed(f"cannot read VENDOR.md ({exc})")
        rb_section = _vendor_section(rb_vendor_text)
        rb_current = (
            provenance.parse_vendor_pin(rb_section) if rb_section is not None else None
        )
        if rb_current is None or not SHA_RE.match(rb_current):
            return failed("cannot parse the current pin from VENDOR.md")
        rb_current = rb_current.lower()
        rb_old, rb_source, rb_looked = _find_rollback_target(run, root)
        if rb_old is None:
            return usage(
                "no prior bump record found; looked at: " + "; ".join(rb_looked)
            )
        if rb_old == rb_current:
            return usage(f"pin already at {rb_old[:7]}; nothing to roll back")
        if dry_run:
            print(
                f"[dry-run] would roll back pin {rb_current[:7]} -> "
                f"{rb_old[:7]} ({rb_source})"
            )
            dry_rb_upstream = root / provenance.PACK_UPSTREAM
            dry_rb_vendored = root / provenance.PACK_VENDORED
            if not dry_rb_upstream.is_dir():
                print(
                    "[dry-run] pin-bump FAILED: missing upstream pack "
                    f"{dry_rb_upstream} at rollback pin {rb_old[:7]}"
                )
                return finish(EXIT_BUMP_FAILED)
            if not dry_rb_vendored.is_dir():
                print(
                    "[dry-run] pin-bump FAILED: missing vendored pack "
                    f"{dry_rb_vendored}"
                )
                return finish(EXIT_BUMP_FAILED)
            dry_rb_copied, dry_rb_removed = revendor_preview(
                dry_rb_upstream, dry_rb_vendored
            )
            print(
                "[dry-run] would re-vendor pack: "
                f"copy {dry_rb_copied}, remove {dry_rb_removed}"
            )
            print(
                f"[dry-run] would update VENDOR.md pin {rb_current[:7]} -> {rb_old[:7]}"
            )
            print(
                "[dry-run] would update "
                f"{provenance.DOCSTRING_FILE.as_posix()} Upstream commit "
                f"{rb_current[:7]} -> {rb_old[:7]}"
            )
            _, _, dry_rb_note, dry_rb_warn = _rollback_package_versions(
                run, redteam_dir, rb_old
            )
            if dry_rb_note is not None:
                print(
                    "[dry-run] would restore redteam/package.json version "
                    f"{dry_rb_note}"
                )
            elif dry_rb_warn is not None:
                print(
                    f"[dry-run] note: package.json version not restored ({dry_rb_warn})"
                )
            print(
                "[dry-run] would open revert PR from branch "
                f"pin-rollback-{rb_old[:7]} (skipped)"
            )
            print(f"[dry-run] rollback to {rb_old[:7]} would succeed")
            return finish(EXIT_OK)
        if not redteam_dir.is_dir():
            return degraded(
                "submodule unreachable: redteam/ is missing "
                "(run git submodule update --init redteam)"
            )
        try:
            rb_fetched = run(["git", "fetch", UPSTREAM_REMOTE], cwd=redteam_dir)
        except OSError as exc:
            return degraded(f"submodule unreachable during fetch ({exc})")
        if rb_fetched.returncode != 0:
            return degraded(
                f"submodule unreachable: git fetch failed: {rb_fetched.stderr.strip()}"
            )
        try:
            rb_checked_out = run(["git", "checkout", rb_old], cwd=redteam_dir)
        except OSError as exc:
            return degraded(f"submodule unreachable during checkout ({exc})")
        if rb_checked_out.returncode != 0:
            return degraded(
                f"submodule unreachable: git checkout {rb_old} failed: "
                f"{rb_checked_out.stderr.strip()}"
            )
        try:
            rb_staged = run(["git", "add", "redteam"], cwd=root)
        except OSError as exc:
            return degraded(f"git add redteam failed ({exc})")
        if rb_staged.returncode != 0:
            return failed(f"git add redteam failed: {rb_staged.stderr.strip()}")
        done.append("pin-rolled-back")
        rb_upstream_dir = root / provenance.PACK_UPSTREAM
        rb_vendored_dir = root / provenance.PACK_VENDORED
        if not rb_upstream_dir.is_dir():
            return failed(
                f"missing upstream pack {rb_upstream_dir} after checkout {rb_old[:7]}"
            )
        if not rb_vendored_dir.is_dir():
            return failed(f"missing vendored pack {rb_vendored_dir}")
        rb_copied, rb_removed = revendor_pack(rb_upstream_dir, rb_vendored_dir)
        print(f"pin-bump re-vendor: copied {rb_copied}, removed {rb_removed}")
        done.append("pack-revendored")
        rb_error = rewrite_pins(root, rb_old)
        if rb_error is not None:
            return failed(rb_error)
        if provenance.cmd_check(root, gitlink_override=rb_old) != 0:
            return failed("check still red after pin rewrite (pin records disagree)")
        if provenance.cmd_pack_diff(root) != 0:
            revendor_pack(rb_upstream_dir, rb_vendored_dir)
            if (
                provenance.cmd_check(root, gitlink_override=rb_old) != 0
                or provenance.cmd_pack_diff(root) != 0
            ):
                return failed("pack-diff still diverged after 2 passes")
        if provenance.cmd_surface(root) != 0:
            return failed(
                "surface DRIFT: upstream tool surface changed; updating "
                "REDTEAM_TOOL_NAMES stays a manual legitimacy decision"
            )
        done.append("provenance-green")
        rb_version_note: str | None = None
        _, rb_old_ver, rb_restore_note, rb_restore_warn = _rollback_package_versions(
            run, redteam_dir, rb_old
        )
        if rb_restore_warn is not None:
            print(
                "pin-bump WARNING: package.json version not restored "
                f"({rb_restore_warn}; leaving working tree as-is)"
            )
        elif rb_restore_note is not None and rb_old_ver is not None:
            rb_pkg_path = redteam_dir / "package.json"
            try:
                rb_pkg_text = rb_pkg_path.read_text(encoding="utf-8")
            except OSError as exc:
                print(
                    "pin-bump WARNING: package.json version not restored "
                    f"(cannot read {rb_pkg_path}: {exc}; "
                    "leaving working tree as-is)"
                )
            else:
                rb_pkg_rewritten = _rewrite_package_version(rb_pkg_text, rb_old_ver)
                if rb_pkg_rewritten is None:
                    print(
                        "pin-bump WARNING: package.json version not restored "
                        f"(cannot rewrite {rb_pkg_path}; "
                        "leaving working tree as-is)"
                    )
                else:
                    try:
                        rb_pkg_path.write_text(rb_pkg_rewritten, encoding="utf-8")
                    except OSError as exc:
                        print(
                            "pin-bump WARNING: package.json version not "
                            f"restored (cannot write {rb_pkg_path}: {exc}; "
                            "leaving working tree as-is)"
                        )
                    else:
                        rb_version_note = rb_restore_note
                        print(
                            "pin-bump version restore "
                            f"(redteam/package.json): {rb_restore_note}"
                        )
        try:
            rb_gh = run(["gh", "--version"], cwd=root)
        except OSError:
            rb_gh = None
        if rb_gh is None or rb_gh.returncode != 0:
            return usage("gh is missing (install gh to use --rollback)")
        rb_branch = f"pin-rollback-{rb_old[:7]}"
        rb_checkout = run(["git", "checkout", "-b", rb_branch], cwd=root)
        if rb_checkout.returncode != 0:
            return failed(
                f"git checkout -b {rb_branch} failed: {rb_checkout.stderr.strip()}"
            )
        rb_add = run(
            [
                "git",
                "add",
                "redteam",
                "VENDOR.md",
                provenance.DOCSTRING_FILE.as_posix(),
                provenance.PACK_VENDORED.as_posix(),
            ],
            cwd=root,
        )
        if rb_add.returncode != 0:
            return failed(f"git add rollback set failed: {rb_add.stderr.strip()}")
        rb_title = f"revert(redteam): roll back pin to {rb_old[:7]}"
        rb_body = (
            f"Revert redteam pin bump {rb_current[:7]} -> {rb_old[:7]} "
            f"({rb_source}).\n\nProvenance: check, pack-diff, surface green."
        )
        if rb_version_note is not None:
            rb_body += f"\nVersion restore: {rb_version_note} (redteam/package.json)."
        rb_committed = run(["git", "commit", "-m", rb_title, "-m", rb_body], cwd=root)
        if rb_committed.returncode != 0:
            return failed(f"git commit failed: {rb_committed.stderr.strip()}")
        rb_pushed = run(["git", "push", "-u", "origin", rb_branch], cwd=root)
        if rb_pushed.returncode != 0:
            return failed(f"git push {rb_branch} failed: {rb_pushed.stderr.strip()}")
        rb_created = run(
            ["gh", "pr", "create", "--title", rb_title, "--body", rb_body], cwd=root
        )
        if rb_created.returncode != 0:
            return failed(f"gh pr create failed: {rb_created.stderr.strip()}")
        print(f"pin-bump revert PR: {rb_created.stdout.strip()}")
        print(f"pin-bump OK: rollback to {rb_old[:7]} complete")
        return finish(EXIT_OK)
    if not to:
        return usage("--to is required (or use --rollback)")

    # Step 1: validate and resolve the ref.
    if SHA_RE.match(to or ""):
        sha = to.lower()
    elif TAG_RE.match(to or ""):
        if not redteam_dir.is_dir():
            return degraded(
                "submodule unreachable: redteam/ is missing "
                "(run git submodule update --init redteam)"
            )
        try:
            listed = run(["git", "ls-remote", UPSTREAM_REMOTE, to], cwd=redteam_dir)
        except OSError as exc:
            return degraded(f"submodule unreachable during ls-remote ({exc})")
        if listed.returncode != 0:
            return usage(
                f"unresolvable tag {to!r}: "
                f"git ls-remote failed: {listed.stderr.strip()}"
            )
        sha = _parse_ls_remote_tag(listed.stdout, to)
        if sha is None:
            return usage(f"unresolvable tag {to!r}: no such tag on {UPSTREAM_REMOTE}")
    else:
        return usage(f"bad ref {to!r}: expected a 40-hex SHA or a v-tag such as v1.4")
    assert sha is not None
    if not _confirm_step(
        "ref resolved", f"resolved {to!r} -> {sha} ({sha[:7]})", interactive=interactive
    ):
        return failed("operator declined at ref-resolved; bump aborted")

    # Step 0 (opt-in): --auto-detect-upstream-fix probes upstream HEAD
    # before any local mutation. A still-vulnerable upstream means there
    # is nothing to fix yet: report the delta and exit 0 without moving
    # the pin. A clean upstream (or an inconclusive probe) proceeds.
    if auto_detect_upstream_fix:
        clean, note, upstream_untriaged = _check_upstream_fix(run, redteam_dir)
        if clean is False:
            print(
                "pin-bump auto-detect: upstream HEAD still shows "
                f"{len(upstream_untriaged)} untriaged high/critical "
                f"advisories; nothing to fix yet — skipping bump"
            )
            for key, severity, name in upstream_untriaged:
                print(f"  {severity} {name}: {key}")
            return finish(EXIT_OK)
        if clean is True:
            print(
                "pin-bump auto-detect: upstream HEAD audit clean "
                "(0 untriaged high/critical); proceeding with bump"
            )
        else:
            print(
                f"pin-bump auto-detect: upstream probe inconclusive "
                f"({note}); proceeding with bump"
            )

    # --audit-only: run the audit chain only, mutate nothing. The new
    # pin is never checked out (the current checkout in redteam/ is
    # audited instead); the delta report plus the --semver-check
    # classification print to stdout instead of report.md. No git
    # fetch/checkout/add, no re-vendor copy, no pin rewrite, no
    # package.json bump (even with --bump-commit), no commit, no PR
    # branch is ever performed below. Exit codes match the normal run
    # (0 unless the verdict is regressed), with no side effects either
    # way. Composes with --dry-run: this branch runs first and both
    # are non-mutating, so the output is identical either way.
    if audit_only:
        if not redteam_dir.is_dir():
            return degraded(
                "submodule unreachable: redteam/ is missing "
                "(run git submodule update --init redteam)"
            )
        try:
            audit_vendor_text = (root / "VENDOR.md").read_text(encoding="utf-8")
        except OSError:
            audit_vendor_text = ""
        audit_section = _vendor_section(audit_vendor_text)
        audit_old_pin = (
            provenance.parse_vendor_pin(audit_section)
            if audit_section is not None
            else None
        )
        if from_audit:
            audit_new_doc, audit_from_error = _load_from_audit(from_audit)
            if audit_from_error is not None:
                print(f"[audit-only] pin-bump usage error: {audit_from_error}")
                return finish(EXIT_USAGE)
            print(
                "[audit-only] note: using saved audit "
                f"{from_audit} (no re-audit; the new pin is never checked out)"
            )
        else:
            try:
                audit_node = run(["node", "--version"], cwd=root)
            except OSError:
                audit_node = None
            if audit_node is None or audit_node.returncode != 0:
                return degraded("node is absent (node --version failed)")
            print(
                "[audit-only] note: auditing the current checkout in "
                f"{redteam_dir} (the new pin is never checked out)"
            )
            try:
                audit_proc = run(
                    [
                        "npm",
                        "audit",
                        "--omit=dev",
                        "--audit-level=high",
                        "--json",
                    ],
                    cwd=redteam_dir,
                )
            except OSError as exc:
                return degraded(f"npm is absent ({exc})")
            try:
                audit_new_doc = json.loads(audit_proc.stdout)
            except ValueError as exc:
                print(
                    f"[audit-only] pin-bump FAILED: cannot parse npm audit output ({exc})"
                )
                return finish(EXIT_BUMP_FAILED)
        try:
            audit_triage_mod = _load_script("redteam-audit-triage.py")
        except (OSError, RuntimeError) as exc:
            print(
                f"[audit-only] pin-bump FAILED: cannot load audit triage helper ({exc})"
            )
            return finish(EXIT_BUMP_FAILED)
        audit_findings, audit_triage_error = audit_triage_mod.triage(audit_new_doc)
        if audit_triage_error is not None:
            print(
                "[audit-only] pin-bump FAILED: audit triage refused "
                f"output: {audit_triage_error}"
            )
            return finish(EXIT_BUMP_FAILED)
        audit_untriaged = [
            finding
            for finding in audit_findings
            if finding[0] not in audit_triage_mod.TRIAGED
        ]
        if pin_selectors:
            audit_pinned_keys = {
                key
                for key, _, name in audit_findings
                if any(_pin_matches(sel, key, str(name or "")) for sel in pin_selectors)
            }
            audit_untriaged_in = [
                finding
                for finding in audit_untriaged
                if finding[0] in audit_pinned_keys
            ]
            audit_untriaged_out = [
                finding
                for finding in audit_untriaged
                if finding[0] not in audit_pinned_keys
            ]
            if audit_untriaged_in:
                print(
                    "[audit-only] pin-bump FAILED: untriaged high/critical "
                    "pinned advisories at new pin:"
                )
                for key, severity, name in audit_untriaged_in:
                    print(f"[audit-only]   {severity} {name}: {key}")
                return finish(EXIT_BUMP_FAILED)
            if audit_untriaged_out:
                print(
                    f"[audit-only] note: {len(audit_untriaged_out)} untriaged "
                    "high/critical out of scope for --pin (ignored for verdict)"
                )
        elif audit_untriaged:
            print(
                "[audit-only] pin-bump FAILED: untriaged high/critical "
                "advisories at new pin:"
            )
            for key, severity, name in audit_untriaged:
                print(f"[audit-only]   {severity} {name}: {key}")
            return finish(EXIT_BUMP_FAILED)
        print("[audit-only] audit triage green (no untriaged high/critical)")
        if not _confirm_step(
            "audit verdict",
            f"audit triage green at {sha[:7]} "
            f"({len(audit_findings)} findings, 0 untriaged high/critical)",
            interactive=interactive,
            audit_only=True,
        ):
            print("[audit-only] pin-bump FAILED: operator declined at audit verdict")
            return finish(EXIT_BUMP_FAILED)

        audit_old_path = (
            Path(old_audit) if old_audit else _default_old_audit(root, audit_old_pin)
        )
        if audit_old_path is None:
            return usage(
                "no saved audit artifact for the pre-bump pin; pass --old-audit <path>"
            )
        if old_audit and not audit_old_path.is_file():
            return usage(f"--old-audit not found: {old_audit}")
        try:
            audit_old_doc = json.loads(audit_old_path.read_text(encoding="utf-8"))
        except OSError as exc:
            print(
                "[audit-only] pin-bump FAILED: cannot read old audit "
                f"{audit_old_path} ({exc})"
            )
            return finish(EXIT_BUMP_FAILED)
        except ValueError as exc:
            print(
                "[audit-only] pin-bump FAILED: cannot parse old audit "
                f"{audit_old_path} ({exc})"
            )
            return finish(EXIT_BUMP_FAILED)
        try:
            audit_delta_mod = _load_script("redteam-audit-delta.py")
        except (OSError, RuntimeError) as exc:
            print(
                f"[audit-only] pin-bump FAILED: cannot load audit delta helper ({exc})"
            )
            return finish(EXIT_BUMP_FAILED)
        audit_old_findings, audit_old_packages, audit_old_error = (
            audit_delta_mod.parse_input(audit_old_doc)
        )
        if audit_old_error is not None:
            print(
                f"[audit-only] pin-bump FAILED: old audit unusable: {audit_old_error}"
            )
            return finish(EXIT_BUMP_FAILED)
        audit_new_findings, audit_new_packages, audit_new_error = (
            audit_delta_mod.parse_input(audit_new_doc)
        )
        if audit_new_error is not None:
            print(
                f"[audit-only] pin-bump FAILED: new audit unusable: {audit_new_error}"
            )
            return finish(EXIT_BUMP_FAILED)
        (
            audit_new_keys,
            audit_fixed,
            audit_rescinded,
            audit_drift,
            verdict,
        ) = audit_delta_mod.compute_delta(
            audit_old_findings, audit_new_findings, audit_new_packages
        )
        if pin_selectors:
            audit_pinned, audit_full, audit_scoped = _pin_deltas(
                audit_delta_mod,
                pin_selectors,
                audit_old_findings,
                audit_new_findings,
                audit_new_packages,
            )
            if not audit_pinned:
                return usage(
                    _pin_no_match_message(
                        pin_selectors, audit_old_findings, audit_new_findings
                    )
                )
            (
                audit_full_new,
                audit_full_fixed,
                audit_full_rescinded,
                audit_full_drift,
                _,
            ) = audit_full
            (
                audit_new_keys,
                audit_fixed,
                audit_rescinded,
                audit_drift,
                verdict,
            ) = audit_scoped
            audit_report = audit_delta_mod.render_report(
                str(audit_old_path),
                "new-pin npm audit",
                {k: v for k, v in audit_old_findings.items() if k in audit_pinned},
                {k: v for k, v in audit_new_findings.items() if k in audit_pinned},
                audit_new_keys,
                audit_fixed,
                audit_rescinded,
                audit_drift,
                verdict,
            )
            audit_report += "\n" + render_pin_scope_section(
                pin_selectors,
                audit_old_findings,
                audit_new_findings,
                audit_full_new,
                audit_full_fixed,
                audit_full_rescinded,
                audit_full_drift,
                audit_pinned,
            )
            json_out_of_scope = [
                {
                    "key": key,
                    "package": (
                        audit_new_findings[key][1]
                        if key in audit_new_findings
                        else audit_old_findings[key][1]
                    ),
                    "status": _pin_scope_status(
                        key,
                        audit_full_new,
                        audit_full_fixed,
                        audit_full_rescinded,
                        audit_full_drift,
                    ),
                }
                for key in sorted(
                    (set(audit_old_findings) | set(audit_new_findings)) - audit_pinned
                )
            ]
            print(
                f"[audit-only] note: verdict {verdict} from pinned set "
                f"({len(audit_pinned)} targeted, "
                f"{len(json_out_of_scope)} out of scope for --pin)"
            )
        else:
            audit_report = audit_delta_mod.render_report(
                str(audit_old_path),
                "new-pin npm audit",
                audit_old_findings,
                audit_new_findings,
                audit_new_keys,
                audit_fixed,
                audit_rescinded,
                audit_drift,
                verdict,
            )
        audit_semver_rows: list[tuple[str, str, str, str, str]] = []
        if semver_check:
            audit_semver_rows = classify_semver_fixes(
                audit_old_findings,
                audit_old_packages,
                audit_new_packages,
                audit_fixed,
            )
        json_fixed = audit_fixed
        json_old_findings = audit_old_findings
        json_new_findings = audit_new_findings
        json_old_packages = audit_old_packages
        json_new_packages = audit_new_packages
        if semver_check:
            json_semver = {key: label for key, _, _, _, label in audit_semver_rows}
        if semver_check:
            audit_report += "\n" + render_semver_section(audit_semver_rows)
        if pin_notes:
            audit_report += "\n" + render_operator_notes_section(
                pin_notes, audit_old_findings, audit_new_findings
            )
        print(f"[audit-only] audit verdict: {verdict} (report not written)")
        print(audit_report)
        if semver_check:
            for key, package, old_spec, new_spec, label in audit_semver_rows:
                print(
                    f"[audit-only] semver {label}: `{key}` "
                    f"(package `{package}`): {old_spec or '?'} -> "
                    f"{new_spec if label != 'removed' else 'gone'}"
                )
            if any(row[4] == "major" for row in audit_semver_rows):
                print(
                    "[audit-only] WARNING: a fix requires a major version "
                    "bump (outside the declared range)"
                )
        if explain_selector:
            (
                explain_new_keys,
                explain_fixed,
                explain_rescinded,
                explain_drift,
                _,
            ) = audit_delta_mod.compute_delta(
                audit_old_findings, audit_new_findings, audit_new_packages
            )
            explain_key, explain_matches = _explain_match_key(
                explain_selector, audit_old_findings, audit_new_findings
            )
            if explain_key is None:
                return usage(
                    _explain_no_match_message(
                        explain_selector, audit_old_findings, audit_new_findings
                    )
                )
            explain_md, explain_obj = render_explain_section(
                selector=explain_selector,
                key=explain_key,
                matches=explain_matches,
                old_findings=audit_old_findings,
                new_findings=audit_new_findings,
                old_packages=audit_old_packages,
                new_packages=audit_new_packages,
                old_doc=audit_old_doc,
                new_doc=audit_new_doc,
                fixed=explain_fixed,
                new_keys=explain_new_keys,
                rescinded=explain_rescinded,
                drift=explain_drift,
                old_pin=audit_old_pin[:7] if audit_old_pin else "unknown",
                new_pin=sha[:7],
            )
            print(explain_md)
            json_explain = explain_obj
        if bump_commit:
            audit_bump_reason = _bump_commit_gate(
                semver_check=semver_check,
                verdict=verdict,
                rows=audit_semver_rows if semver_check else [],
            )
            if audit_bump_reason is not None:
                print(
                    "[audit-only] pin-bump WARNING: --bump-commit skipped "
                    f"({audit_bump_reason})"
                )
                json_bump = {
                    "applied": False,
                    "level": None,
                    "old": None,
                    "new": None,
                    "reason": audit_bump_reason,
                }
            else:
                audit_pkg_path = redteam_dir / "package.json"
                _, audit_current, audit_error = _read_package_text(audit_pkg_path)
                if audit_error is not None:
                    print(
                        "[audit-only] pin-bump WARNING: --bump-commit skipped "
                        f"({audit_error})"
                    )
                    json_bump = {
                        "applied": False,
                        "level": None,
                        "old": None,
                        "new": None,
                        "reason": audit_error,
                    }
                else:
                    assert audit_current is not None
                    audit_new_version = _bump_package_version(
                        audit_current, _bump_level_for_rows(audit_semver_rows)
                    )
                    if audit_new_version is None:
                        print(
                            "[audit-only] pin-bump WARNING: --bump-commit skipped "
                            f"(unparseable version {audit_current!r} in {audit_pkg_path})"
                        )
                        json_bump = {
                            "applied": False,
                            "level": None,
                            "old": audit_current,
                            "new": None,
                            "reason": (
                                f"unparseable version {audit_current!r} "
                                f"in {audit_pkg_path}"
                            ),
                        }
                    else:
                        print(
                            f"[audit-only] would bump version {audit_current} -> "
                            f"{audit_new_version} ({audit_pkg_path}; no files changed)"
                        )
                        json_bump = {
                            "applied": False,
                            "level": _bump_level_for_rows(audit_semver_rows),
                            "old": audit_current,
                            "new": audit_new_version,
                            "reason": "audit-only (no files changed)",
                        }
        if verify:
            audit_verify_passed, audit_verify_verdict, audit_verify_notes, _ = (
                _verify_post_bump(
                    run,
                    redteam_dir,
                    old_findings=audit_old_findings,
                    old_packages=audit_old_packages,
                    semver_check=semver_check,
                    pin_selectors=pin_selectors,
                    baseline_rows=audit_semver_rows if semver_check else [],
                )
            )
            json_verify = {
                "ran": True,
                "verdict": audit_verify_verdict,
                "passed": audit_verify_passed,
            }
            for audit_verify_note in audit_verify_notes:
                print(f"[audit-only] {audit_verify_note}")
            if not audit_verify_passed:
                print("[audit-only] pin-bump verification FAILED (no files changed)")
                return finish(EXIT_VERIFY_FAILED)

        if not _confirm_step(
            "delta verdict",
            f"audit delta verdict: {verdict} (report not written)",
            interactive=interactive,
            audit_only=True,
        ):
            print("[audit-only] pin-bump FAILED: operator declined at delta verdict")
            return finish(EXIT_BUMP_FAILED)
        if verdict == "regressed":
            return finish(EXIT_BUMP_FAILED)
        print(f"[audit-only] audit of {sha[:7]} complete: {verdict} (no files changed)")
        return finish(EXIT_OK)

    # --dry-run: run every verification step, mutate nothing. Each
    # would-be step is printed with a [dry-run] prefix; failures print
    # the same diagnostics as normal mode (prefixed) and return the
    # same exit codes. No git checkout/add, no re-vendor copy, no pin
    # rewrite, no report.md, no PR branch is ever performed below.
    if dry_run:
        if not redteam_dir.is_dir():
            return degraded(
                "submodule unreachable: redteam/ is missing "
                "(run git submodule update --init redteam)"
            )
        try:
            dry_vendor_text = (root / "VENDOR.md").read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[dry-run] pin-bump FAILED: cannot read VENDOR.md ({exc})")
            return finish(EXIT_BUMP_FAILED)
        dry_section = _vendor_section(dry_vendor_text)
        dry_old_pin = (
            provenance.parse_vendor_pin(dry_section)
            if dry_section is not None
            else None
        )
        dry_old_label = dry_old_pin[:7] if dry_old_pin else "unknown"
        print(
            f"[dry-run] would move pin {dry_old_label} -> {sha} "
            "(git fetch, checkout, add redteam)"
        )

        dry_upstream_dir = root / provenance.PACK_UPSTREAM
        dry_vendored_dir = root / provenance.PACK_VENDORED
        if not dry_upstream_dir.is_dir():
            print(
                f"[dry-run] pin-bump FAILED: missing upstream pack "
                f"{dry_upstream_dir} after checkout {sha[:7]}"
            )
            return finish(EXIT_BUMP_FAILED)
        if not dry_vendored_dir.is_dir():
            print(
                f"[dry-run] pin-bump FAILED: missing vendored pack {dry_vendored_dir}"
            )
            return finish(EXIT_BUMP_FAILED)
        dry_copied, dry_removed = revendor_preview(dry_upstream_dir, dry_vendored_dir)
        print(
            f"[dry-run] would re-vendor pack: copy {dry_copied}, remove {dry_removed}"
        )
        if not _confirm_step(
            "pack re-vendor",
            f"would re-vendor pack: copy {dry_copied}, remove {dry_removed}",
            interactive=interactive,
            dry_run=True,
        ):
            print("[dry-run] pin-bump FAILED: operator declined at pack re-vendor")
            return finish(EXIT_BUMP_FAILED)

        if dry_section is None:
            print(
                f"[dry-run] pin-bump FAILED: no {VENDOR_SECTION!r} section in VENDOR.md"
            )
            return finish(EXIT_BUMP_FAILED)
        if not VENDOR_PIN_LINE_RE.search(dry_section):
            print(
                "[dry-run] pin-bump FAILED: no `Commit:` pin line in the "
                "T3MP3ST section of VENDOR.md"
            )
            return finish(EXIT_BUMP_FAILED)
        try:
            dry_docstring_text = (root / provenance.DOCSTRING_FILE).read_text(
                encoding="utf-8"
            )
        except OSError as exc:
            print(
                "[dry-run] pin-bump FAILED: cannot read "
                f"{provenance.DOCSTRING_FILE.as_posix()} ({exc})"
            )
            return finish(EXIT_BUMP_FAILED)
        if not provenance.DOCSTRING_PIN_RE.search(dry_docstring_text):
            print(
                "[dry-run] pin-bump FAILED: no `Upstream commit:` pin in "
                f"{provenance.DOCSTRING_FILE.as_posix()}"
            )
            return finish(EXIT_BUMP_FAILED)
        print(f"[dry-run] would update VENDOR.md pin {dry_old_label} -> {sha[:7]}")
        print(
            f"[dry-run] would update {provenance.DOCSTRING_FILE.as_posix()} "
            f"Upstream commit {dry_old_label} -> {sha[:7]}"
        )

        if provenance.cmd_surface(root) != 0:
            print(
                "[dry-run] pin-bump FAILED: surface DRIFT: upstream tool "
                "surface changed; updating REDTEAM_TOOL_NAMES stays a "
                "manual legitimacy decision"
            )
            return finish(EXIT_BUMP_FAILED)
        print("[dry-run] surface check green")

        if skip_audit:
            verdict = "audit-skipped"
            print("[dry-run] audit skipped (--skip-audit; no verdict)")
            if bump_commit:
                print(
                    "[dry-run] pin-bump WARNING: --bump-commit skipped "
                    "(audit verdict is 'audit-skipped', not 'improved')"
                )
                json_bump = {
                    "applied": False,
                    "level": None,
                    "old": None,
                    "new": None,
                    "reason": "audit verdict is 'audit-skipped', not 'improved'",
                }
        else:
            if from_audit:
                dry_new_doc, dry_from_error = _load_from_audit(from_audit)
                if dry_from_error is not None:
                    print(f"[dry-run] pin-bump usage error: {dry_from_error}")
                    return finish(EXIT_USAGE)
                print(
                    "[dry-run] note: using saved audit "
                    f"{from_audit} (no re-audit; the new pin is never checked out)"
                )
            else:
                try:
                    dry_node = run(["node", "--version"], cwd=root)
                except OSError:
                    dry_node = None
                if dry_node is None or dry_node.returncode != 0:
                    return degraded("node is absent (node --version failed)")
                print(
                    "[dry-run] note: auditing the current checkout in "
                    f"{redteam_dir} (the new pin is never checked out)"
                )
                try:
                    dry_audit_proc = run(
                        [
                            "npm",
                            "audit",
                            "--omit=dev",
                            "--audit-level=high",
                            "--json",
                        ],
                        cwd=redteam_dir,
                    )
                except OSError as exc:
                    return degraded(f"npm is absent ({exc})")
                try:
                    dry_new_doc = json.loads(dry_audit_proc.stdout)
                except ValueError as exc:
                    print(
                        f"[dry-run] pin-bump FAILED: cannot parse npm audit output ({exc})"
                    )
                    return finish(EXIT_BUMP_FAILED)
            try:
                dry_triage_mod = _load_script("redteam-audit-triage.py")
            except (OSError, RuntimeError) as exc:
                print(
                    "[dry-run] pin-bump FAILED: cannot load audit triage "
                    f"helper ({exc})"
                )
                return finish(EXIT_BUMP_FAILED)
            dry_findings, dry_triage_error = dry_triage_mod.triage(dry_new_doc)
            if dry_triage_error is not None:
                print(
                    "[dry-run] pin-bump FAILED: audit triage refused "
                    f"output: {dry_triage_error}"
                )
                return finish(EXIT_BUMP_FAILED)
            dry_untriaged = [
                finding
                for finding in dry_findings
                if finding[0] not in dry_triage_mod.TRIAGED
            ]
            if pin_selectors:
                dry_pinned_keys = {
                    key
                    for key, _, name in dry_findings
                    if any(
                        _pin_matches(sel, key, str(name or "")) for sel in pin_selectors
                    )
                }
                dry_untriaged_in = [
                    finding
                    for finding in dry_untriaged
                    if finding[0] in dry_pinned_keys
                ]
                dry_untriaged_out = [
                    finding
                    for finding in dry_untriaged
                    if finding[0] not in dry_pinned_keys
                ]
                if dry_untriaged_in:
                    print(
                        "[dry-run] pin-bump FAILED: untriaged high/critical "
                        "pinned advisories at new pin:"
                    )
                    for key, severity, name in dry_untriaged_in:
                        print(f"[dry-run]   {severity} {name}: {key}")
                    return finish(EXIT_BUMP_FAILED)
                if dry_untriaged_out:
                    print(
                        f"[dry-run] note: {len(dry_untriaged_out)} untriaged "
                        "high/critical out of scope for --pin (ignored for verdict)"
                    )
            elif dry_untriaged:
                print(
                    "[dry-run] pin-bump FAILED: untriaged high/critical "
                    "advisories at new pin:"
                )
                for key, severity, name in dry_untriaged:
                    print(f"[dry-run]   {severity} {name}: {key}")
                return finish(EXIT_BUMP_FAILED)
            print("[dry-run] audit triage green (no untriaged high/critical)")
            if not _confirm_step(
                "audit verdict",
                f"audit triage green at {sha[:7]} "
                f"({len(dry_findings)} findings, 0 untriaged high/critical)",
                interactive=interactive,
                dry_run=True,
            ):
                print("[dry-run] pin-bump FAILED: operator declined at audit verdict")
                return finish(EXIT_BUMP_FAILED)

            dry_old_path = (
                Path(old_audit) if old_audit else _default_old_audit(root, dry_old_pin)
            )
            if dry_old_path is None:
                return usage(
                    "no saved audit artifact for the pre-bump pin; "
                    "pass --old-audit <path>"
                )
            if old_audit and not dry_old_path.is_file():
                return usage(f"--old-audit not found: {old_audit}")
            try:
                dry_old_doc = json.loads(dry_old_path.read_text(encoding="utf-8"))
            except OSError as exc:
                print(
                    "[dry-run] pin-bump FAILED: cannot read old audit "
                    f"{dry_old_path} ({exc})"
                )
                return finish(EXIT_BUMP_FAILED)
            except ValueError as exc:
                print(
                    "[dry-run] pin-bump FAILED: cannot parse old audit "
                    f"{dry_old_path} ({exc})"
                )
                return finish(EXIT_BUMP_FAILED)
            try:
                dry_delta_mod = _load_script("redteam-audit-delta.py")
            except (OSError, RuntimeError) as exc:
                print(
                    f"[dry-run] pin-bump FAILED: cannot load audit delta helper ({exc})"
                )
                return finish(EXIT_BUMP_FAILED)
            dry_old_findings, dry_old_packages, dry_old_error = (
                dry_delta_mod.parse_input(dry_old_doc)
            )
            if dry_old_error is not None:
                print(f"[dry-run] pin-bump FAILED: old audit unusable: {dry_old_error}")
                return finish(EXIT_BUMP_FAILED)
            dry_new_findings, dry_new_packages, dry_new_error = (
                dry_delta_mod.parse_input(dry_new_doc)
            )
            if dry_new_error is not None:
                print(f"[dry-run] pin-bump FAILED: new audit unusable: {dry_new_error}")
                return finish(EXIT_BUMP_FAILED)
            _, dry_fixed, _, _, verdict = dry_delta_mod.compute_delta(
                dry_old_findings, dry_new_findings, dry_new_packages
            )
            if pin_selectors:
                dry_pinned, dry_full, dry_scoped = _pin_deltas(
                    dry_delta_mod,
                    pin_selectors,
                    dry_old_findings,
                    dry_new_findings,
                    dry_new_packages,
                )
                if not dry_pinned:
                    return usage(
                        _pin_no_match_message(
                            pin_selectors, dry_old_findings, dry_new_findings
                        )
                    )
                (
                    dry_full_new,
                    dry_full_fixed,
                    dry_full_rescinded,
                    dry_full_drift,
                    _,
                ) = dry_full
                (_, dry_fixed, _, _, verdict) = dry_scoped
                json_out_of_scope = [
                    {
                        "key": key,
                        "package": (
                            dry_new_findings[key][1]
                            if key in dry_new_findings
                            else dry_old_findings[key][1]
                        ),
                        "status": _pin_scope_status(
                            key,
                            dry_full_new,
                            dry_full_fixed,
                            dry_full_rescinded,
                            dry_full_drift,
                        ),
                    }
                    for key in sorted(
                        (set(dry_old_findings) | set(dry_new_findings)) - dry_pinned
                    )
                ]
                print(
                    f"[dry-run] note: verdict {verdict} from pinned set "
                    f"({len(dry_pinned)} targeted, "
                    f"{len(json_out_of_scope)} out of scope for --pin)"
                )
                print(
                    render_pin_scope_section(
                        pin_selectors,
                        dry_old_findings,
                        dry_new_findings,
                        dry_full_new,
                        dry_full_fixed,
                        dry_full_rescinded,
                        dry_full_drift,
                        dry_pinned,
                    )
                )
            json_fixed = dry_fixed
            json_old_findings = dry_old_findings
            json_new_findings = dry_new_findings
            json_old_packages = dry_old_packages
            json_new_packages = dry_new_packages
            print(f"[dry-run] audit verdict: {verdict} (report.md not written)")
            if report_dest is not None:
                print(f"[dry-run] report would be written to {report_dest}")
            if semver_check:
                dry_rows = classify_semver_fixes(
                    dry_old_findings,
                    dry_old_packages,
                    dry_new_packages,
                    dry_fixed,
                )
                json_semver = {key: label for key, _, _, _, label in dry_rows}
                for key, package, old_spec, new_spec, label in dry_rows:
                    print(
                        f"[dry-run] semver {label}: `{key}` "
                        f"(package `{package}`): {old_spec or '?'} -> "
                        f"{new_spec if label != 'removed' else 'gone'}"
                    )
                if any(row[4] == "major" for row in dry_rows):
                    print(
                        "[dry-run] WARNING: a fix requires a major version "
                        "bump (outside the declared range)"
                    )
            if pin_notes:
                print(
                    render_operator_notes_section(
                        pin_notes, dry_old_findings, dry_new_findings
                    )
                )
            if explain_selector:
                (
                    explain_new_keys,
                    explain_fixed,
                    explain_rescinded,
                    explain_drift,
                    _,
                ) = dry_delta_mod.compute_delta(
                    dry_old_findings, dry_new_findings, dry_new_packages
                )
                explain_key, explain_matches = _explain_match_key(
                    explain_selector, dry_old_findings, dry_new_findings
                )
                if explain_key is None:
                    return usage(
                        _explain_no_match_message(
                            explain_selector, dry_old_findings, dry_new_findings
                        )
                    )
                explain_md, explain_obj = render_explain_section(
                    selector=explain_selector,
                    key=explain_key,
                    matches=explain_matches,
                    old_findings=dry_old_findings,
                    new_findings=dry_new_findings,
                    old_packages=dry_old_packages,
                    new_packages=dry_new_packages,
                    old_doc=dry_old_doc,
                    new_doc=dry_new_doc,
                    fixed=explain_fixed,
                    new_keys=explain_new_keys,
                    rescinded=explain_rescinded,
                    drift=explain_drift,
                    old_pin=dry_old_pin[:7] if dry_old_pin else "unknown",
                    new_pin=sha[:7],
                )
                print(explain_md)
                json_explain = explain_obj
            if bump_commit:
                dry_bump_reason = _bump_commit_gate(
                    semver_check=semver_check,
                    verdict=verdict,
                    rows=dry_rows if semver_check else [],
                )
                if dry_bump_reason is not None:
                    print(
                        "[dry-run] pin-bump WARNING: --bump-commit skipped "
                        f"({dry_bump_reason})"
                    )
                    json_bump = {
                        "applied": False,
                        "level": None,
                        "old": None,
                        "new": None,
                        "reason": dry_bump_reason,
                    }
                else:
                    dry_pkg_path = redteam_dir / "package.json"
                    _, dry_current, dry_error = _read_package_text(dry_pkg_path)
                    if dry_error is not None:
                        print(
                            "[dry-run] pin-bump WARNING: --bump-commit skipped "
                            f"({dry_error})"
                        )
                        json_bump = {
                            "applied": False,
                            "level": None,
                            "old": None,
                            "new": None,
                            "reason": dry_error,
                        }
                    else:
                        assert dry_current is not None
                        dry_new = _bump_package_version(
                            dry_current, _bump_level_for_rows(dry_rows)
                        )
                        if dry_new is None:
                            print(
                                "[dry-run] pin-bump WARNING: --bump-commit skipped "
                                f"(unparseable version {dry_current!r} in {dry_pkg_path})"
                            )
                            json_bump = {
                                "applied": False,
                                "level": None,
                                "old": dry_current,
                                "new": None,
                                "reason": (
                                    f"unparseable version {dry_current!r} "
                                    f"in {dry_pkg_path}"
                                ),
                            }
                        else:
                            print(
                                f"[dry-run] would bump version {dry_current} -> "
                                f"{dry_new} ({dry_pkg_path}; no files changed)"
                            )
                            json_bump = {
                                "applied": False,
                                "level": _bump_level_for_rows(dry_rows),
                                "old": dry_current,
                                "new": dry_new,
                                "reason": "dry-run (no files changed)",
                            }
            if not _confirm_step(
                "delta verdict",
                f"audit delta verdict: {verdict} (report.md not written)",
                interactive=interactive,
                dry_run=True,
            ):
                print("[dry-run] pin-bump FAILED: operator declined at delta verdict")
                return finish(EXIT_BUMP_FAILED)

            if verdict == "regressed":
                return finish(EXIT_BUMP_FAILED)

        if verify:
            print(
                f"[dry-run] would re-run verification audit at {sha[:7]} "
                "(skipped; no double audit)"
            )
        if open_pr:
            print(f"[dry-run] would open PR from branch pin-bump-{sha[:7]} (skipped)")
        print(f"[dry-run] bump to {sha[:7]} would succeed")
        return finish(EXIT_OK)

    # Step 1 (cont.): fetch, checkout, stage the gitlink.
    if not redteam_dir.is_dir():
        return degraded(
            "submodule unreachable: redteam/ is missing "
            "(run git submodule update --init redteam)"
        )
    try:
        fetched = run(["git", "fetch", UPSTREAM_REMOTE], cwd=redteam_dir)
    except OSError as exc:
        return degraded(f"submodule unreachable during fetch ({exc})")
    if fetched.returncode != 0:
        return degraded(
            f"submodule unreachable: git fetch failed: {fetched.stderr.strip()}"
        )
    try:
        checked_out = run(["git", "checkout", sha], cwd=redteam_dir)
    except OSError as exc:
        return degraded(f"submodule unreachable during checkout ({exc})")
    if checked_out.returncode != 0:
        return degraded(
            f"submodule unreachable: git checkout {sha} failed: "
            f"{checked_out.stderr.strip()}"
        )
    try:
        staged = run(["git", "add", "redteam"], cwd=root)
    except OSError as exc:
        return degraded(f"git add redteam failed ({exc})")
    if staged.returncode != 0:
        return failed(f"git add redteam failed: {staged.stderr.strip()}")
    done.append("pin-moved")

    # Remember the pre-bump pin for the default --old lookup.
    try:
        old_vendor_text = (root / "VENDOR.md").read_text(encoding="utf-8")
    except OSError as exc:
        return failed(f"cannot read VENDOR.md ({exc})")
    old_section = _vendor_section(old_vendor_text)
    old_pin = (
        provenance.parse_vendor_pin(old_section) if old_section is not None else None
    )

    # Step 2: re-vendor the pack.
    upstream_dir = root / provenance.PACK_UPSTREAM
    vendored_dir = root / provenance.PACK_VENDORED
    if not upstream_dir.is_dir():
        return failed(f"missing upstream pack {upstream_dir} after checkout {sha[:7]}")
    if not vendored_dir.is_dir():
        return failed(f"missing vendored pack {vendored_dir}")
    if interactive:
        preview_copy, preview_remove = revendor_preview(upstream_dir, vendored_dir)
        if not _confirm_step(
            "pack re-vendor",
            f"re-vendor pack: would copy {preview_copy}, remove {preview_remove}",
            interactive=interactive,
        ):
            return failed("operator declined at pack re-vendor; bump aborted")
    copied, removed = revendor_pack(upstream_dir, vendored_dir)
    print(f"pin-bump re-vendor: copied {copied}, removed {removed}")
    done.append("pack-revendored")

    # Step 3: rewrite the pin records, then prove provenance.
    error = rewrite_pins(root, sha)
    if error is not None:
        return failed(error)
    if provenance.cmd_check(root, gitlink_override=sha) != 0:
        return failed("check still red after pin rewrite (pin records disagree)")
    if provenance.cmd_pack_diff(root) != 0:
        revendor_pack(upstream_dir, vendored_dir)
        if (
            provenance.cmd_check(root, gitlink_override=sha) != 0
            or provenance.cmd_pack_diff(root) != 0
        ):
            return failed("pack-diff still diverged after 2 passes")
    if provenance.cmd_surface(root) != 0:
        return failed(
            "surface DRIFT: upstream tool surface changed; updating "
            "REDTEAM_TOOL_NAMES stays a manual legitimacy decision"
        )
    done.append("provenance-green")

    # Steps 4-5: audit the new pin and report the delta (unless skipped).
    if skip_audit:
        verdict = "audit-skipped"
        print("pin-bump OK: provenance green, audit-skipped (--skip-audit)")
        if bump_commit:
            print(
                "pin-bump WARNING: --bump-commit skipped "
                "(audit verdict is 'audit-skipped', not 'improved')"
            )
            json_bump = {
                "applied": False,
                "level": None,
                "old": None,
                "new": None,
                "reason": "audit verdict is 'audit-skipped', not 'improved'",
            }
    else:
        if from_audit:
            new_doc, from_error = _load_from_audit(from_audit)
            if from_error is not None:
                return usage(from_error)
            print(f"pin-bump note: using saved audit {from_audit} (no re-audit)")
        else:
            try:
                node = run(["node", "--version"], cwd=root)
            except OSError:
                node = None
            if node is None or node.returncode != 0:
                return degraded("node is absent (node --version failed)")
            try:
                audit_proc = run(
                    [
                        "npm",
                        "audit",
                        "--omit=dev",
                        "--audit-level=high",
                        "--json",
                    ],
                    cwd=redteam_dir,
                )
            except OSError as exc:
                return degraded(f"npm is absent ({exc})")
            try:
                new_doc = json.loads(audit_proc.stdout)
            except ValueError as exc:
                return failed(f"cannot parse npm audit output ({exc})")
        try:
            triage_mod = _load_script("redteam-audit-triage.py")
        except (OSError, RuntimeError) as exc:
            return failed(f"cannot load audit triage helper ({exc})")
        findings, triage_error = triage_mod.triage(new_doc)
        if triage_error is not None:
            return failed(f"audit triage refused output: {triage_error}")
        untriaged = [
            finding for finding in findings if finding[0] not in triage_mod.TRIAGED
        ]
        if pin_selectors:
            pinned_triage_keys = {
                key
                for key, _, name in findings
                if any(_pin_matches(sel, key, str(name or "")) for sel in pin_selectors)
            }
            untriaged_in = [
                finding for finding in untriaged if finding[0] in pinned_triage_keys
            ]
            untriaged_out = [
                finding for finding in untriaged if finding[0] not in pinned_triage_keys
            ]
            if untriaged_in:
                print(
                    "pin-bump FAILED: untriaged high/critical pinned "
                    "advisories at new pin:"
                )
                for key, severity, name in untriaged_in:
                    print(f"  {severity} {name}: {key}")
                return finish(EXIT_BUMP_FAILED)
            if untriaged_out:
                print(
                    f"pin-bump note: {len(untriaged_out)} untriaged high/critical "
                    "out of scope for --pin (ignored for verdict)"
                )
        elif untriaged:
            print("pin-bump FAILED: untriaged high/critical advisories at new pin:")
            for key, severity, name in untriaged:
                print(f"  {severity} {name}: {key}")
            return finish(EXIT_BUMP_FAILED)
        done.append("audit-green")
        if not _confirm_step(
            "audit verdict",
            f"audit triage green at {sha[:7]} "
            f"({len(findings)} findings, 0 untriaged high/critical)",
            interactive=interactive,
        ):
            return failed("operator declined at audit verdict; bump aborted")

        old_path = Path(old_audit) if old_audit else _default_old_audit(root, old_pin)
        if old_path is None:
            return usage(
                "no saved audit artifact for the pre-bump pin; pass --old-audit <path>"
            )
        if old_audit and not old_path.is_file():
            return usage(f"--old-audit not found: {old_audit}")
        try:
            old_doc = json.loads(old_path.read_text(encoding="utf-8"))
        except OSError as exc:
            return failed(f"cannot read old audit {old_path} ({exc})")
        except ValueError as exc:
            return failed(f"cannot parse old audit {old_path} ({exc})")
        try:
            delta_mod = _load_script("redteam-audit-delta.py")
        except (OSError, RuntimeError) as exc:
            return failed(f"cannot load audit delta helper ({exc})")
        old_findings, old_packages, old_error = delta_mod.parse_input(old_doc)
        if old_error is not None:
            return failed(f"old audit unusable: {old_error}")
        new_findings, new_packages, new_error = delta_mod.parse_input(new_doc)
        if new_error is not None:
            return failed(f"new audit unusable: {new_error}")
        new_keys, fixed, rescinded, drift, verdict = delta_mod.compute_delta(
            old_findings, new_findings, new_packages
        )
        if pin_selectors:
            pinned, full, scoped = _pin_deltas(
                delta_mod,
                pin_selectors,
                old_findings,
                new_findings,
                new_packages,
            )
            if not pinned:
                return usage(
                    _pin_no_match_message(pin_selectors, old_findings, new_findings)
                )
            (full_new, full_fixed, full_rescinded, full_drift, _) = full
            (new_keys, fixed, rescinded, drift, verdict) = scoped
            report = delta_mod.render_report(
                str(old_path),
                "new-pin npm audit",
                {k: v for k, v in old_findings.items() if k in pinned},
                {k: v for k, v in new_findings.items() if k in pinned},
                new_keys,
                fixed,
                rescinded,
                drift,
                verdict,
            )
            report += "\n" + render_pin_scope_section(
                pin_selectors,
                old_findings,
                new_findings,
                full_new,
                full_fixed,
                full_rescinded,
                full_drift,
                pinned,
            )
            json_out_of_scope = [
                {
                    "key": key,
                    "package": (
                        new_findings[key][1]
                        if key in new_findings
                        else old_findings[key][1]
                    ),
                    "status": _pin_scope_status(
                        key, full_new, full_fixed, full_rescinded, full_drift
                    ),
                }
                for key in sorted((set(old_findings) | set(new_findings)) - pinned)
            ]
            print(
                f"pin-bump --pin: verdict {verdict} from pinned set "
                f"({len(pinned)} targeted, "
                f"{len(json_out_of_scope)} out of scope for --pin; "
                f"see {report_label})"
            )
        else:
            report = delta_mod.render_report(
                str(old_path),
                "new-pin npm audit",
                old_findings,
                new_findings,
                new_keys,
                fixed,
                rescinded,
                drift,
                verdict,
            )
        semver_rows = []
        if semver_check:
            semver_rows = classify_semver_fixes(
                old_findings, old_packages, new_packages, fixed
            )
        json_fixed = fixed
        json_old_findings = old_findings
        json_new_findings = new_findings
        json_old_packages = old_packages
        json_new_packages = new_packages
        if semver_check:
            json_semver = {key: label for key, _, _, _, label in semver_rows}
        if semver_check:
            report += "\n" + render_semver_section(semver_rows)
        if pin_notes:
            report += "\n" + render_operator_notes_section(
                pin_notes, old_findings, new_findings
            )
            print(
                f"pin-bump operator notes: {len(pin_notes)} note(s) recorded "
                f"(see {report_label})"
            )
        if report_dest is None:
            report_path = root / "report.md"
            try:
                report_path.write_text(report, encoding="utf-8")
            except OSError as exc:
                return failed(f"cannot write report {report_path} ({exc})")
        else:
            report_path = report_dest
            report_dest_created = not os.path.lexists(report_path)
            try:
                _write_report_atomic(report_path, report)
            except OSError as exc:
                return failed(f"cannot write report {report_path} ({exc})")
        print(verdict)
        print(f"pin-bump audit: {verdict} (report: {report_path})")
        if semver_check:
            for key, package, old_spec, new_spec, label in semver_rows:
                print(
                    f"pin-bump semver {label}: `{key}` "
                    f"(package `{package}`): {old_spec or '?'} -> "
                    f"{new_spec if label != 'removed' else 'gone'}"
                )
            if any(row[4] == "major" for row in semver_rows):
                print(
                    "pin-bump WARNING: a fix requires a major version "
                    "bump (outside the declared range)"
                )
        if explain_selector:
            (
                explain_new_keys,
                explain_fixed,
                explain_rescinded,
                explain_drift,
                _,
            ) = delta_mod.compute_delta(old_findings, new_findings, new_packages)
            explain_key, explain_matches = _explain_match_key(
                explain_selector, old_findings, new_findings
            )
            if explain_key is None:
                return usage(
                    _explain_no_match_message(
                        explain_selector, old_findings, new_findings
                    )
                )
            explain_md, explain_obj = render_explain_section(
                selector=explain_selector,
                key=explain_key,
                matches=explain_matches,
                old_findings=old_findings,
                new_findings=new_findings,
                old_packages=old_packages,
                new_packages=new_packages,
                old_doc=old_doc,
                new_doc=new_doc,
                fixed=explain_fixed,
                new_keys=explain_new_keys,
                rescinded=explain_rescinded,
                drift=explain_drift,
                old_pin=old_pin[:7] if old_pin else "unknown",
                new_pin=sha[:7],
            )
            print(explain_md)
            json_explain = explain_obj
        if not _confirm_step(
            "delta verdict",
            f"audit delta verdict: {verdict} (report: {report_path})",
            interactive=interactive,
        ):
            return failed("operator declined at delta verdict; bump aborted")
        if verdict == "regressed":
            return finish(EXIT_BUMP_FAILED)
    # Step 5b (opt-in): --bump-commit rolls the target package's own
    # version forward when the fix is proven semver-compatible. Any
    # unmet precondition skips the bump with a warning and leaves the
    # exit code untouched (fail-closed).
    if bump_commit:
        bump_reason = _bump_commit_gate(
            semver_check=semver_check, verdict=verdict, rows=semver_rows
        )
        if bump_reason is not None:
            print(f"pin-bump WARNING: --bump-commit skipped ({bump_reason})")
            json_bump = {
                "applied": False,
                "level": None,
                "old": None,
                "new": None,
                "reason": bump_reason,
            }
        else:
            bump_pkg_path = redteam_dir / "package.json"
            bump_text, bump_current, bump_error = _read_package_text(bump_pkg_path)
            if bump_error is not None:
                print(f"pin-bump WARNING: --bump-commit skipped ({bump_error})")
                json_bump = {
                    "applied": False,
                    "level": None,
                    "old": None,
                    "new": None,
                    "reason": bump_error,
                }
            else:
                assert bump_text is not None and bump_current is not None
                bump_level = _bump_level_for_rows(semver_rows)
                bump_new = _bump_package_version(bump_current, bump_level)
                bump_rewritten = (
                    _rewrite_package_version(bump_text, bump_new)
                    if bump_new is not None
                    else None
                )
                if bump_new is None or bump_rewritten is None:
                    print(
                        "pin-bump WARNING: --bump-commit skipped "
                        f"(unparseable version {bump_current!r} in {bump_pkg_path})"
                    )
                    json_bump = {
                        "applied": False,
                        "level": None,
                        "old": bump_current,
                        "new": None,
                        "reason": (
                            f"unparseable version {bump_current!r} in {bump_pkg_path}"
                        ),
                    }
                else:
                    try:
                        bump_pkg_path.write_text(bump_rewritten, encoding="utf-8")
                    except OSError as exc:
                        print(
                            "pin-bump WARNING: --bump-commit skipped "
                            f"(cannot write {bump_pkg_path}: {exc})"
                        )
                        json_bump = {
                            "applied": False,
                            "level": bump_level,
                            "old": bump_current,
                            "new": bump_new,
                            "reason": f"cannot write {bump_pkg_path}: {exc}",
                        }
                    else:
                        version_bump_note = f"{bump_current} -> {bump_new}"
                        print(
                            f"pin-bump version bump ({bump_level}): "
                            f"{bump_current} -> {bump_new} ({bump_pkg_path})"
                        )
                        json_bump = {
                            "applied": True,
                            "level": bump_level,
                            "old": bump_current,
                            "new": bump_new,
                            "reason": None,
                        }

    # Step 5c (opt-in): --verify post-bump smoke. After the bump (pins,
    # pack, report, optional version bump) applies and before any PR,
    # re-run the audit chain against the NEW pin: the verdict must be
    # improved with no untriaged high/critical, and the --semver-check
    # classification must hold when enabled. A failed verification rolls
    # the bump back locally (pin records, package.json version,
    # report.md), exits 4, and opens no PR.
    if verify:
        verify_passed, verify_verdict, verify_notes, _ = _verify_post_bump(
            run,
            redteam_dir,
            old_findings=old_findings,
            old_packages=old_packages,
            semver_check=semver_check,
            pin_selectors=pin_selectors,
            baseline_rows=semver_rows if semver_check else [],
        )
        json_verify = {
            "ran": True,
            "verdict": verify_verdict,
            "passed": verify_passed,
        }
        for verify_note in verify_notes:
            print(verify_note)
        if not verify_passed:
            if old_pin is not None:
                verify_rb_error = rewrite_pins(root, old_pin)
                if verify_rb_error is not None:
                    print(f"pin-bump verification rollback FAILED: {verify_rb_error}")
                else:
                    print(
                        "pin-bump verification rollback: pin records "
                        f"restored to {old_pin[:7]}"
                    )
            if isinstance(json_bump, dict) and json_bump.get("applied"):
                verify_pkg_path = redteam_dir / "package.json"
                try:
                    verify_pkg_text = verify_pkg_path.read_text(encoding="utf-8")
                except OSError as exc:
                    print(
                        "pin-bump verification rollback note: cannot read "
                        f"{verify_pkg_path} ({exc})"
                    )
                else:
                    verify_restored = _rewrite_package_version(
                        verify_pkg_text, str(json_bump["old"])
                    )
                    if verify_restored is None:
                        print(
                            "pin-bump verification rollback note: cannot "
                            f"rewrite {verify_pkg_path}"
                        )
                    else:
                        try:
                            verify_pkg_path.write_text(
                                verify_restored, encoding="utf-8"
                            )
                        except OSError as exc:
                            print(
                                "pin-bump verification rollback note: cannot "
                                f"write {verify_pkg_path} ({exc})"
                            )
                        else:
                            print(
                                "pin-bump verification rollback: "
                                "redteam/package.json version restored to "
                                f"{json_bump['old']}"
                            )
            if report_path is not None and (report_dest is None or report_dest_created):
                try:
                    report_path.unlink()
                except OSError as exc:
                    print(
                        "pin-bump verification rollback note: cannot remove "
                        f"{report_path} ({exc})"
                    )
                else:
                    print(f"pin-bump verification rollback: {report_path.name} removed")
            elif report_path is not None:
                print(
                    "pin-bump verification rollback note: kept "
                    f"{report_path} (it existed before this run)"
                )
            print(
                f"pin-bump verification FAILED at new pin {sha[:7]} "
                "(bump rolled back locally; no PR)"
            )
            return finish(EXIT_VERIFY_FAILED)

    # Step 6: open the PR (only after local green).
    if open_pr:
        try:
            gh = run(["gh", "--version"], cwd=root)
        except OSError:
            gh = None
        if gh is None or gh.returncode != 0:
            return usage("gh is missing (install gh to use --open-pr)")
        branch = f"pin-bump-{sha[:7]}"
        checkout = run(["git", "checkout", "-b", branch], cwd=root)
        if checkout.returncode != 0:
            return failed(f"git checkout -b {branch} failed: {checkout.stderr.strip()}")
        add = run(
            [
                "git",
                "add",
                "redteam",
                "VENDOR.md",
                provenance.DOCSTRING_FILE.as_posix(),
                provenance.PACK_VENDORED.as_posix(),
            ],
            cwd=root,
        )
        if add.returncode != 0:
            return failed(f"git add bump set failed: {add.stderr.strip()}")
        title = f"chore(redteam): bump pin to {sha[:7]}"
        if verdict == "audit-skipped":
            audit_line = "Audit: skipped (--skip-audit; no verdict)."
        else:
            audit_line = (
                f"Audit verdict: {verdict} (report: "
                f"{report_dest.name if report_dest is not None else 'report.md'})."
            )
        body = (
            f"Redteam pin bump {old_pin or 'unknown'} -> {sha}.\n\n"
            f"Provenance: check, pack-diff, surface green.\n{audit_line}"
        )
        if version_bump_note is not None:
            body += f"\nVersion bump: {version_bump_note} (redteam/package.json)."
        committed = run(["git", "commit", "-m", title, "-m", body], cwd=root)
        if committed.returncode != 0:
            return failed(f"git commit failed: {committed.stderr.strip()}")
        pushed = run(["git", "push", "-u", "origin", branch], cwd=root)
        if pushed.returncode != 0:
            return failed(f"git push {branch} failed: {pushed.stderr.strip()}")
        created = run(
            ["gh", "pr", "create", "--title", title, "--body", body], cwd=root
        )
        if created.returncode != 0:
            return failed(f"gh pr create failed: {created.stderr.strip()}")
        print(f"pin-bump PR: {created.stdout.strip()}")

    print(f"pin-bump OK: pin {sha[:7]} complete")
    return finish(EXIT_OK)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``--to <sha|tag> [...]`` or ``--rollback [--dry-run]`` (target from the last bump record)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.rollback and args.to is not None:
        parser.error(
            "--rollback derives the target from the last bump record; drop --to"
        )
    if not args.rollback and not args.to:
        parser.error("--to is required (or use --rollback)")
    if args.rollback and args.audit_only:
        parser.error(
            "--rollback never audits, so it cannot be combined with --audit-only"
        )
    if args.rollback and args.schedule is not None:
        parser.error("--rollback cannot be combined with --schedule")
    if args.rollback and args.explain is not None:
        parser.error("--rollback never audits, so it cannot be combined with --explain")
    root = Path(args.root) if args.root is not None else REPO_ROOT
    return bump(
        args.to,
        root=root,
        old_audit=args.old_audit,
        from_audit=args.from_audit,
        skip_audit=args.skip_audit,
        semver_check=args.semver_check,
        bump_commit=args.bump_commit,
        auto_detect_upstream_fix=args.auto_detect_upstream_fix,
        open_pr=args.open_pr,
        json_mode=args.json_mode,
        dry_run=args.dry_run,
        audit_only=args.audit_only,
        interactive=args.interactive,
        pin=args.pin,
        pin_note=args.pin_note,
        explain=args.explain,
        schedule=args.schedule,
        schedule_name=args.schedule_name,
        rollback=args.rollback,
        verify=args.verify,
        report_to=args.report_to,
    )


if __name__ == "__main__":
    sys.exit(main())
