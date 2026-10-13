"""Redteam submodule provenance checker: pin, pack, and tool-surface agreement.

Three offline subcommands, all stdlib-only, all run against the local
checkout (no network):

- ``check`` — the ``redteam/`` submodule gitlink, the pin on the
  ``Commit:`` line of ``VENDOR.md``, and every ``Upstream commit:``
  provenance docstring in ``omega_prime/redteam/__init__.py`` must agree.
  Any disagreeing pair exits nonzero naming the pair.
- ``pack-diff`` — every markdown/JSON file under
  ``omega_prime/redteam/pack/`` must be byte-identical to its counterpart
  under the submodule checkout at ``redteam/grokbot/``. Divergence exits
  nonzero listing the files.
- ``surface`` — the ``tempest_*`` tool-name declarations in upstream
  ``redteam/src/mcp-server.ts`` must equal ``REDTEAM_TOOL_NAMES`` in
  ``omega_prime/tools/redteam.py`` and ``tool_names`` in
  ``sidecar/contract/bot-interface.json``. On unparseable upstream surface
  the check fails loudly with the raw excerpt, never silently passes.

Relocated from plan-01's ``omega_prime/redteam/provenance.py`` to
``sidecar/provenance.py`` (checker advisory 4): the checker lives with
the sidecar it gates, next to the harness and RoE validator, instead of
inside the vendored package it verifies.

Stdlib-only. Never imports ``omega_prime`` or ``redteam`` at runtime:
the adapter surface is read with :mod:`ast`, so importing this module has
no side effects beyond the standard library.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

VENDOR_FILE = "VENDOR.md"
VENDOR_PIN_RE = re.compile(r"^-\s*Commit:\s*`([0-9a-f]{40})`", re.MULTILINE)
DOCSTRING_FILE = Path("omega_prime") / "redteam" / "__init__.py"
DOCSTRING_PIN_RE = re.compile(r"Upstream commit:\s*([0-9a-f]{40})")

PACK_VENDORED = Path("omega_prime") / "redteam" / "pack"
PACK_UPSTREAM = Path("redteam") / "grokbot"
# Vendored MIT notice with no upstream counterpart (upstream keeps its own
# LICENSE at the submodule root). Everything else must match byte for byte.
PACK_SKIP_REL = {"LICENSE"}
PACK_SUFFIXES = {".md", ".json"}

UPSTREAM_SURFACE_FILE = Path("redteam") / "src" / "mcp-server.ts"
UPSTREAM_TOOL_RE = re.compile(r"name:\s*'(tempest_[A-Za-z0-9_]+)'")
ADAPTER_FILE = Path("omega_prime") / "tools" / "redteam.py"
CONTRACT_FILE = Path("sidecar") / "contract" / "bot-interface.json"

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_ENV = 2


VENDOR_SECTION = "## T3MP3ST red team pack"


def parse_vendor_pin(text: str) -> str | None:
    """Pin from the red-team section's ``Commit:`` line of ``VENDOR.md``.

    VENDOR.md carries one ``Commit:`` pin per vendored component (Hermes,
    oh-my-pi, prime-agent, red team), so the search is scoped to the
    ``## T3MP3ST red team pack`` section; a file-wide search returns the
    Hermes pin and false-mismatches against the gitlink.
    """
    start = text.find(VENDOR_SECTION)
    if start == -1:
        return None
    next_section = text.find("\n## ", start + len(VENDOR_SECTION))
    section = text[start:] if next_section == -1 else text[start:next_section]
    match = VENDOR_PIN_RE.search(section)
    return match.group(1) if match else None


def parse_docstring_pins(text: str) -> list[str]:
    """Every ``Upstream commit:`` pin in the provenance docstrings."""
    return DOCSTRING_PIN_RE.findall(text)


def read_gitlink(root: Path) -> str:
    """Tracked ``redteam/`` gitlink SHA, staged index first (offline).

    Reads the staged gitlink (``git ls-files -s redteam``) so a Dependabot
    or hand bump staged with ``git add redteam`` verifies before commit;
    falls back to ``HEAD`` (``git ls-tree HEAD redteam``) when nothing is
    staged. Callers may bypass both with ``--gitlink``.
    """
    staged = subprocess.run(
        ["git", "ls-files", "-s", "redteam"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if staged.returncode == 0:
        match = re.search(r"\b([0-9a-f]{40})\b", staged.stdout)
        if match:
            return match.group(1)
    proc = subprocess.run(
        ["git", "ls-tree", "HEAD", "redteam"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-tree HEAD redteam failed: {proc.stderr.strip()}")
    match = re.search(r"\b([0-9a-f]{40})\b", proc.stdout)
    if not match:
        raise RuntimeError(
            "no gitlink SHA in `git ls-tree HEAD redteam` output: "
            f"{proc.stdout.strip()!r}"
        )
    return match.group(1)


def check_pins(gitlink: str, vendor: str | None, docstrings: list[str]) -> list[str]:
    """Disagreeing pin pairs as human-readable lines (empty when all agree)."""
    errors: list[str] = []
    if vendor is None:
        errors.append(
            "VENDOR.md-vs-docstrings: no `Commit:` pin line found in VENDOR.md"
        )
    if not docstrings:
        errors.append(
            "VENDOR.md-vs-docstrings: no `Upstream commit:` pin found in "
            f"{DOCSTRING_FILE.as_posix()}"
        )
    if vendor is not None and gitlink != vendor:
        errors.append(f"gitlink-vs-VENDOR.md: gitlink={gitlink} VENDOR.md={vendor}")
    for pin in dict.fromkeys(docstrings):
        if gitlink != pin:
            errors.append(f"gitlink-vs-docstrings: gitlink={gitlink} docstring={pin}")
        if vendor is not None and vendor != pin:
            errors.append(
                f"VENDOR.md-vs-docstrings: VENDOR.md={vendor} docstring={pin}"
            )
    return errors


def cmd_check(root: Path, gitlink_override: str | None = None) -> int:
    """Run the ``check`` subcommand. Returns the process exit code."""
    vendor_path = root / VENDOR_FILE
    docstring_path = root / DOCSTRING_FILE
    if not vendor_path.is_file():
        print(f"check FAILED (environment): missing {vendor_path}", file=sys.stderr)
        return EXIT_ENV
    if not docstring_path.is_file():
        print(f"check FAILED (environment): missing {docstring_path}", file=sys.stderr)
        return EXIT_ENV
    try:
        gitlink = gitlink_override or read_gitlink(root)
    except RuntimeError as exc:
        print(f"check FAILED (environment): {exc}", file=sys.stderr)
        return EXIT_ENV
    vendor = parse_vendor_pin(vendor_path.read_text(encoding="utf-8"))
    docstrings = parse_docstring_pins(docstring_path.read_text(encoding="utf-8"))
    errors = check_pins(gitlink, vendor, docstrings)
    if errors:
        print("check FAILED:")
        for line in errors:
            print(f"  MISMATCH {line}")
        return EXIT_MISMATCH
    print(f"check OK: pin {gitlink[:7]} agrees (gitlink, VENDOR.md, docstrings)")
    return EXIT_OK


def iter_pack_files(base: Path) -> dict[str, Path]:
    """Map of relpath -> path for markdown/JSON files under ``base``."""
    found: dict[str, Path] = {}
    for path in sorted(base.rglob("*")):
        if path.is_file() and path.suffix.lower() in PACK_SUFFIXES:
            found[path.relative_to(base).as_posix()] = path
    return found


def diff_pack_dirs(vendored_dir: Path, upstream_dir: Path) -> list[str]:
    """Byte-compare vendored pack against the upstream checkout.

    Returns human-readable divergence lines (empty when equivalent).
    ``LICENSE`` is skipped: the vendored MIT notice has no upstream
    counterpart inside the compared tree.
    """
    problems: list[str] = []
    vendored = iter_pack_files(vendored_dir)
    upstream = iter_pack_files(upstream_dir)
    for rel in sorted(set(vendored) | set(upstream)):
        if rel in PACK_SKIP_REL:
            continue
        if rel not in vendored:
            problems.append(f"EXTRA-UPSTREAM {rel} (no vendored counterpart)")
        elif rel not in upstream:
            problems.append(f"MISSING-UPSTREAM {rel} (no submodule counterpart)")
        elif vendored[rel].read_bytes() != upstream[rel].read_bytes():
            problems.append(f"DIVERGED {rel} (bytes differ)")
    return problems


def cmd_pack_diff(
    root: Path,
    vendored_dir: Path | None = None,
    upstream_dir: Path | None = None,
) -> int:
    """Run the ``pack-diff`` subcommand. Returns the process exit code."""
    vendored = vendored_dir or root / PACK_VENDORED
    upstream = upstream_dir or root / PACK_UPSTREAM
    if not vendored.is_dir():
        print(f"pack-diff FAILED (environment): missing {vendored}", file=sys.stderr)
        return EXIT_ENV
    if not upstream.is_dir():
        print(f"pack-diff FAILED (environment): missing {upstream}", file=sys.stderr)
        return EXIT_ENV
    problems = diff_pack_dirs(vendored, upstream)
    if problems:
        print("pack-diff FAILED:")
        for line in problems:
            print(f"  {line}")
        return EXIT_MISMATCH
    compared = len([r for r in iter_pack_files(vendored) if r not in PACK_SKIP_REL])
    print(f"pack-diff OK: {compared} markdown/JSON files byte-identical")
    return EXIT_OK


def parse_upstream_tool_names(text: str) -> list[str]:
    """Tool names declared in upstream ``mcp-server.ts`` (order-preserved).

    Raises :class:`ValueError` carrying the raw excerpt when nothing
    parses — the caller must fail loudly, never silently pass.
    """
    names = list(dict.fromkeys(UPSTREAM_TOOL_RE.findall(text)))
    if not names:
        raise ValueError(
            "unparseable upstream tool surface: no `name: 'tempest_*'` "
            f"declarations found in {UPSTREAM_SURFACE_FILE.as_posix()}; "
            f"raw excerpt: {text[:2000]!r}"
        )
    return names


def parse_adapter_tool_names(source: str) -> list[str]:
    """``REDTEAM_TOOL_NAMES`` from the adapter source via :mod:`ast`.

    Read as text, never imported: the checker stays stdlib-only and has
    no runtime dependency on the product it verifies.
    """
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "REDTEAM_TOOL_NAMES"
            for t in node.targets
        ):
            value = node.value
            elts = getattr(value, "elts", [])
            if isinstance(value, (ast.Tuple, ast.List)) and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elts
            ):
                names = [e.value for e in elts]
                if not names:
                    raise ValueError(
                        "unparseable adapter tool surface: REDTEAM_TOOL_NAMES is empty"
                    )
                return names
    raise ValueError(
        "unparseable adapter tool surface: no REDTEAM_TOOL_NAMES "
        "tuple/list of strings found"
    )


def surface_disagreements(
    upstream: list[str], adapter: list[str], contract: list[str]
) -> list[str]:
    """Disagreeing surface pairs as human-readable lines (empty when equal)."""
    errors: list[str] = []
    if upstream != adapter:
        errors.append(
            "upstream-vs-adapter: "
            f"upstream={upstream} adapter={adapter} "
            f"(only-upstream={sorted(set(upstream) - set(adapter))} "
            f"only-adapter={sorted(set(adapter) - set(upstream))})"
        )
    if upstream != contract:
        errors.append(
            "upstream-vs-contract: "
            f"upstream={upstream} contract={contract} "
            f"(only-upstream={sorted(set(upstream) - set(contract))} "
            f"only-contract={sorted(set(contract) - set(upstream))})"
        )
    if adapter != contract:
        errors.append(f"adapter-vs-contract: adapter={adapter} contract={contract}")
    return errors


def cmd_surface(
    root: Path,
    upstream_file: Path | None = None,
    adapter_file: Path | None = None,
    contract_file: Path | None = None,
) -> int:
    """Run the ``surface`` subcommand. Returns the process exit code."""
    upstream_path = upstream_file or root / UPSTREAM_SURFACE_FILE
    adapter_path = adapter_file or root / ADAPTER_FILE
    contract_path = contract_file or root / CONTRACT_FILE
    for label, path in (
        ("upstream", upstream_path),
        ("adapter", adapter_path),
        ("contract", contract_path),
    ):
        if not path.is_file():
            print(
                f"surface FAILED (environment): missing {label} file {path}",
                file=sys.stderr,
            )
            return EXIT_ENV
    try:
        upstream = parse_upstream_tool_names(upstream_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"surface FAILED: {exc}")
        return EXIT_MISMATCH
    try:
        adapter = parse_adapter_tool_names(adapter_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"surface FAILED: {exc}")
        return EXIT_MISMATCH
    try:
        contract_doc = json.loads(contract_path.read_text(encoding="utf-8"))
        contract = contract_doc["tool_names"]
        if not isinstance(contract, list) or not all(
            isinstance(n, str) for n in contract
        ):
            raise ValueError("`tool_names` is not a list of strings")
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"surface FAILED: unparseable contract {contract_path}: {exc}")
        return EXIT_MISMATCH
    errors = surface_disagreements(upstream, adapter, contract)
    if errors:
        print("surface FAILED:")
        for line in errors:
            print(f"  DRIFT {line}")
        return EXIT_MISMATCH
    print(f"surface OK: {len(upstream)} tool names agree (upstream, adapter, contract)")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """CLI parser (kept separate so tests can drive :func:`main`)."""
    parser = argparse.ArgumentParser(
        description="Redteam submodule provenance checker (offline, stdlib-only)."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root (default: parent of sidecar/).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Pin agreement across sources.")
    check.add_argument(
        "--gitlink",
        default=None,
        help="Override the git-read gitlink SHA (hermetic testing).",
    )
    sub.add_parser("pack-diff", help="Vendored pack byte-equivalence.")
    sub.add_parser("surface", help="Tool-surface agreement.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the exit code (0 agree, 1 drift, 2 environment)."""
    args = build_parser().parse_args(argv)
    root = args.root
    if args.command == "check":
        return cmd_check(root, gitlink_override=args.gitlink)
    if args.command == "pack-diff":
        return cmd_pack_diff(root)
    if args.command == "surface":
        return cmd_surface(root)
    raise AssertionError(f"unknown command {args.command}")  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
