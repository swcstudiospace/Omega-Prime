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
says ``audit-skipped`` so the PR body cannot quote a verdict. Rollback
is a single revert: ``git reset --hard HEAD~1 && git submodule update
--init redteam`` — the bump commit is atomic, so no partial pin records
survive.

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
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
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
            "unreachable, with the completed prefix steps named on stderr."
        ),
    )
    parser.add_argument(
        "--to",
        required=True,
        help="New pin: a 40-hex upstream SHA or a v-tag (e.g. v1.4).",
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
        "--open-pr",
        action="store_true",
        help="Commit the bump set, push branch pin-bump-<short-sha>, run gh pr create.",
    )
    parser.add_argument(
        "--json",
        dest="json_mode",
        action="store_true",
        help="Print machine-readable {pin, verdict, report}.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: parent of omega_prime/). Testing override.",
    )
    return parser


def bump(
    to: str,
    *,
    root: Path = REPO_ROOT,
    run: Runner | None = None,
    old_audit: str | None = None,
    skip_audit: bool = False,
    auto_detect_upstream_fix: bool = False,
    open_pr: bool = False,
    json_mode: bool = False,
    dry_run: bool = False,
) -> int:
    """Run the pin-bump runbook against ``root``. Returns the exit code."""
    if run is None:
        run = _default_run
    redteam_dir = root / "redteam"
    done: list[str] = []
    verdict: str | None = None
    report_path: Path | None = None
    sha: str | None = None

    def finish(code: int) -> int:
        if json_mode and sha is not None:
            print(
                json.dumps(
                    {
                        "pin": sha,
                        "verdict": verdict,
                        "report": str(report_path) if report_path else None,
                    }
                )
            )
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
            if dry_untriaged:
                print(
                    "[dry-run] pin-bump FAILED: untriaged high/critical "
                    "advisories at new pin:"
                )
                for key, severity, name in dry_untriaged:
                    print(f"[dry-run]   {severity} {name}: {key}")
                return finish(EXIT_BUMP_FAILED)
            print("[dry-run] audit triage green (no untriaged high/critical)")

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
            dry_old_findings, _, dry_old_error = dry_delta_mod.parse_input(dry_old_doc)
            if dry_old_error is not None:
                print(f"[dry-run] pin-bump FAILED: old audit unusable: {dry_old_error}")
                return finish(EXIT_BUMP_FAILED)
            dry_new_findings, dry_new_packages, dry_new_error = (
                dry_delta_mod.parse_input(dry_new_doc)
            )
            if dry_new_error is not None:
                print(f"[dry-run] pin-bump FAILED: new audit unusable: {dry_new_error}")
                return finish(EXIT_BUMP_FAILED)
            _, _, _, _, verdict = dry_delta_mod.compute_delta(
                dry_old_findings, dry_new_findings, dry_new_packages
            )
            print(f"[dry-run] audit verdict: {verdict} (report.md not written)")
            if verdict == "regressed":
                return finish(EXIT_BUMP_FAILED)

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
        if untriaged:
            print("pin-bump FAILED: untriaged high/critical advisories at new pin:")
            for key, severity, name in untriaged:
                print(f"  {severity} {name}: {key}")
            return finish(EXIT_BUMP_FAILED)
        done.append("audit-green")

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
        old_findings, _, old_error = delta_mod.parse_input(old_doc)
        if old_error is not None:
            return failed(f"old audit unusable: {old_error}")
        new_findings, new_packages, new_error = delta_mod.parse_input(new_doc)
        if new_error is not None:
            return failed(f"new audit unusable: {new_error}")
        new_keys, fixed, rescinded, drift, verdict = delta_mod.compute_delta(
            old_findings, new_findings, new_packages
        )
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
        report_path = root / "report.md"
        try:
            report_path.write_text(report, encoding="utf-8")
        except OSError as exc:
            return failed(f"cannot write report {report_path} ({exc})")
        print(verdict)
        print(f"pin-bump audit: {verdict} (report: {report_path})")
        if verdict == "regressed":
            return finish(EXIT_BUMP_FAILED)

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
            audit_line = f"Audit verdict: {verdict} (report: report.md)."
        body = (
            f"Redteam pin bump {old_pin or 'unknown'} -> {sha}.\n\n"
            f"Provenance: check, pack-diff, surface green.\n{audit_line}"
        )
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
    """CLI: ``--to <sha|tag> [--old-audit <path>] [--skip-audit] [--dry-run] [--open-pr] [--json]``."""
    args = build_parser().parse_args(argv)
    root = Path(args.root) if args.root is not None else REPO_ROOT
    return bump(
        args.to,
        root=root,
        old_audit=args.old_audit,
        skip_audit=args.skip_audit,
        auto_detect_upstream_fix=args.auto_detect_upstream_fix,
        open_pr=args.open_pr,
        json_mode=args.json_mode,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
