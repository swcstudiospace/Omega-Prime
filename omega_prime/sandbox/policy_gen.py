# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Seat-policy to OpenShell sandbox-policy renderer (Phase 83, SAND-01).

Reads a seat-policy JSON document **only** through ``SeatPolicy.load`` and
emits a deterministic OpenShell sandbox policy YAML: the same seat file
always produces the same bytes. Unknown top-level seat keys fail loudly
(``SeatPolicy._check`` ignores them, so the generator rejects them itself).

Mapping:
- ``paths.read_only`` globs ride alongside the fixed system read-only set
  as read-only filesystem grants (``allows_write`` semantics decide
  nothing here — the globs themselves are the grants).
- ``network.hosts`` exact matches become the only network allowlist
  entries; an empty seat allowlist renders ``network_policies: {}``
  (default-deny preserved).
- Read-write grants derive from the Omega home scratch
  (``Path.home() / ".omega-prime"``, the command-log home), the work-root
  state directory, plus ``/sandbox``, ``/tmp``, ``/dev/null`` — never a
  hardcoded username or ``/root`` path.

CLI: ``python -m omega_prime.sandbox.policy_gen --seat <seat.json>
--out <policy.yaml>`` (``--out -`` writes stdout).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from omega_prime.policy.policy import SeatPolicy

GENERATOR = "omega_prime.sandbox.policy_gen"
CANONICAL_SEAT = "omega_prime/contracts/policies/omega-prime.json"
CANONICAL_OUT = "omega_prime/hosting/openshell/sandbox-policy.yaml"
REGENERATE_COMMAND = (
    f"python3 -m {GENERATOR} --seat {CANONICAL_SEAT} --out {CANONICAL_OUT}"
)
CREATE_COMMAND = (
    "openshell sandbox create --name omega-prime "
    f"--policy {CANONICAL_OUT} --no-auto-providers"
)

#: Top-level seat keys the generator understands. Anything else fails
#: loudly instead of being silently dropped.
KNOWN_SEAT_KEYS = frozenset({"version", "seat", "tools", "paths", "network"})

#: Fixed system read-only set already shipped in the hand-maintained file.
SYSTEM_READ_ONLY = (
    "/bin",
    "/usr",
    "/lib",
    "/proc",
    "/dev/urandom",
    "/app",
    "/etc",
    "/var/log",
)

#: Fixed writable scratch set already shipped in the hand-maintained file.
FIXED_READ_WRITE = ("/sandbox", "/tmp", "/dev/null")

HOME_SCRATCH_NAME = ".omega-prime"


def digest_bytes(data: bytes) -> str:
    """SHA-256 hex of rendered policy bytes (receipt binding, plan 83-02)."""
    return hashlib.sha256(data).hexdigest()


def digest_file(path: str | Path) -> str:
    """SHA-256 hex of a policy file's bytes (the file passed to create)."""
    return digest_bytes(Path(path).read_bytes())


def load_seat_document(seat_path: str | Path) -> tuple[SeatPolicy, dict]:
    """Load via ``SeatPolicy.load``; reject unknown top-level keys loudly."""
    policy = SeatPolicy.load(seat_path)
    document = policy._document
    if not isinstance(document, dict):  # defensive: _check already enforces
        raise ValueError(f"policy {seat_path} must be a JSON object")
    unknown = sorted(set(document) - KNOWN_SEAT_KEYS)
    if unknown:
        raise ValueError(
            f"policy {seat_path} has unknown top-level keys: " + ", ".join(unknown)
        )
    return policy, document


def _seat_hosts(policy: SeatPolicy, document: dict) -> list[str]:
    """Seat network hosts in file order, via ``allows_host`` semantics."""
    network = document.get("network") or {}
    hosts = network.get("hosts") or ()
    # Exact-match allowlist, no implied subdomains (allows_host contract).
    return [host for host in hosts if policy.allows_host(host)]


def _seat_read_only_globs(document: dict) -> list[str]:
    """Seat ``paths.read_only`` globs in file order, deduplicated."""
    paths = document.get("paths") or {}
    seen: set[str] = set()
    globs: list[str] = []
    for glob in paths.get("read_only") or ():
        if glob not in seen:
            seen.add(glob)
            globs.append(glob)
    return globs


def _home_scratch() -> str:
    """Omega home scratch (the command-log home, receipts.py)."""
    return str(Path.home() / HOME_SCRATCH_NAME)


def _work_root_state_dir(seat_path: Path) -> str:
    """Work-root state dir, derived from the seat file's repo layout.

    The canonical seat lives at ``<root>/omega_prime/contracts/policies/``,
    so ``<root>/omega_prime/state`` is the work-root state location (the
    ``_desk_store_path`` default). Falls back to the current directory when
    the seat file sits outside that layout. Derived, never hardcoded.
    """
    parents = seat_path.resolve().parents
    if len(parents) >= 4 and parents[2].name == "omega_prime" and parents[3].is_dir():
        return str(parents[3] / "omega_prime" / "state")
    return str(Path.cwd() / "omega_prime" / "state")


def _slug(host: str) -> str:
    """Deterministic policy-entry slug for one allowlisted host."""
    return re.sub(r"[^A-Za-z0-9]+", "_", host).strip("_").lower() or "host"


def build_policy_doc(
    policy: SeatPolicy, document: dict, seat_path: str | Path
) -> dict[str, Any]:
    """Map a seat policy to an OpenShell sandbox policy dict."""
    read_only = list(SYSTEM_READ_ONLY)
    for glob in _seat_read_only_globs(document):
        if glob not in read_only:
            read_only.append(glob)
    read_write = [_home_scratch(), _work_root_state_dir(Path(seat_path))]
    for fixed in FIXED_READ_WRITE:
        if fixed not in read_write:
            read_write.append(fixed)
    network_policies: dict[str, Any] = {}
    for host in _seat_hosts(policy, document):
        slug = _slug(host)
        network_policies[f"{slug}_allow"] = {
            "name": f"{slug}-allow",
            "endpoints": [
                {
                    "host": host,
                    "port": 443,
                    "protocol": "rest",
                    "enforcement": "enforce",
                    "access": "read-write",
                }
            ],
            "binaries": [{"path": "/usr/bin/python3"}],
        }
    return {
        "version": 1,
        "filesystem_policy": {
            "include_workdir": True,
            "read_only": read_only,
            "read_write": read_write,
        },
        "landlock": {"compatibility": "best_effort"},
        "network_policies": network_policies,
    }


# --- Deterministic YAML emitter (deploy.py precedent: block style,
# strings always JSON-quoted, flow lists only when they fit) ---

_PLAIN_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_./-]*$")
_FLOW_LIMIT = 76


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    raise TypeError(f"cannot emit {type(value).__name__}")


def _key(key: str) -> str:
    return key if _PLAIN_KEY.match(key) else json.dumps(key)


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, str))


def _emit(value: Any, indent: int = 0) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            head = f"{pad}{_key(key)}:"
            if _is_scalar(item):
                lines.append(f"{head} {_scalar(item)}")
            elif isinstance(item, dict):
                lines.extend(
                    [f"{head} {{}}"] if not item else [head, *_emit(item, indent + 2)]
                )
            elif not item:
                lines.append(f"{head} []")
            elif all(_is_scalar(x) for x in item):
                flow = "[" + ", ".join(_scalar(x) for x in item) + "]"
                if len(head) + 1 + len(flow) <= _FLOW_LIMIT:
                    lines.append(f"{head} {flow}")
                else:
                    lines.extend([head, *_emit(item, indent + 2)])
            else:
                lines.extend([head, *_emit(item, indent + 2)])
        return lines
    for item in value:
        if _is_scalar(item):
            lines.append(f"{pad}- {_scalar(item)}")
            continue
        inner = _emit(item, indent + 2)
        lines.append(f"{pad}- {inner[0].lstrip()}")
        lines.extend(inner[1:])
    return lines


def header_lines(seat_name: str) -> list[str]:
    """Header comment the committed file carries (usage + regenerate)."""
    return [
        f"# Generated by {GENERATOR} — DO NOT EDIT by hand.",
        f"# Regenerate: {REGENERATE_COMMAND}",
        f"# Source seat: {seat_name}",
        "#",
        "# Omega Prime tool host policy for NVIDIA OpenShell.",
        "# Network is default-deny; the seat network.hosts allowlist",
        "# (empty today) is the only source of network_policies entries.",
        "#",
        "# Run the host inside a sandbox:",
        f"#   {CREATE_COMMAND}",
        "#   python -m omega_prime.mcp_server --root /work/omega-prime",
        "#",
        "# To let the host reach an upstream (model API, GitHub), add the",
        "# host to the seat policy network.hosts and regenerate — never",
        "# hand-edit this file.",
    ]


def render_policy(seat_path: str | Path) -> bytes:
    """Render the full policy file bytes (header + body) for one seat file."""
    policy, document = load_seat_document(seat_path)
    doc = build_policy_doc(policy, document, seat_path)
    lines = [*header_lines(policy.seat), "", *_emit(doc)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render an OpenShell sandbox policy from a seat policy."
    )
    parser.add_argument("--seat", required=True, help="Seat policy JSON file")
    parser.add_argument(
        "--out", required=True, help="Output policy YAML file ('-' = stdout)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry. Exit 0 on success, 2 on bad usage or bad policy."""
    args = _build_parser().parse_args(argv)
    try:
        rendered = render_policy(args.seat)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"{GENERATOR}: error: {exc}", file=sys.stderr)
        return 2
    if args.out == "-":
        sys.stdout.write(rendered.decode("utf-8"))
        return 0
    try:
        Path(args.out).write_bytes(rendered)
    except OSError as exc:
        print(f"{GENERATOR}: error: cannot write {args.out}: {exc}", file=sys.stderr)
        return 2
    return 0


__all__ = [
    "CANONICAL_OUT",
    "CANONICAL_SEAT",
    "CREATE_COMMAND",
    "FIXED_READ_WRITE",
    "GENERATOR",
    "KNOWN_SEAT_KEYS",
    "REGENERATE_COMMAND",
    "SYSTEM_READ_ONLY",
    "build_policy_doc",
    "digest_bytes",
    "digest_file",
    "header_lines",
    "load_seat_document",
    "main",
    "render_policy",
]

if __name__ == "__main__":
    raise SystemExit(main())
