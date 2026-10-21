# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Load the pinned MIT Prime runtime (VENDOR.md) without copying it.

``load_extension()`` returns the PyO3 module ``omega_prime_prime``. That
module calls the Rust implementations; it is not a link check.

- ``goals`` — objective and budget checks, token delta, state normalization,
  host replies, goal-context messages
- ``autonomous.AutonomousRun`` — limits, usage, continuation text
- ``protocol`` — REPL protocol 3 parse and request encoding
- ``heartbeat`` — schedules, delivery, deferral
- ``refinement`` — history path, scope, append and load
- ``commands`` — ``/goal`` and ``/autonomous`` argument parsers
- ``goals.Goal`` — the thread-goal state those parsers apply

Wire objects keep Prime's serde names (camelCase state, snake_case goal
replies). ``crates.probe`` calls each of the nine crates: telemetry catalog,
incident ids, provider overflow and JSON repair, model-catalog checks, tool
argument validation, REPL protocol 3, private-frame encoding, session search,
and CLI mode names. Nothing here starts the terminal UI or a daemon worker.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_REPO = Path(__file__).resolve().parents[2]
_CANDIDATES = (
    _REPO / "prime-agent" / "target" / "debug" / "libomega_prime_prime.so",
    _REPO / "prime-agent" / "target" / "release" / "libomega_prime_prime.so",
    _REPO
    / "native"
    / "omega-prime-prime"
    / "target"
    / "debug"
    / "libomega_prime_prime.so",
    _REPO
    / "native"
    / "omega-prime-prime"
    / "target"
    / "release"
    / "libomega_prime_prime.so",
)
_MODULE: ModuleType | None = None


def _members_from_cargo() -> list[str]:
    text = (_REPO / "prime-agent" / "Cargo.toml").read_text(encoding="utf-8")
    members: list[str] = []
    in_members = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("members"):
            in_members = True
            continue
        if in_members:
            if stripped.startswith("]"):
                break
            if stripped.startswith('"'):
                path = stripped.strip('",')
                members.append(path.rsplit("/", 1)[-1])
    return members


def _workspace_members() -> list[str]:
    try:
        from omega_prime.prime_kernel.checkout import workspace_members
    except ImportError:
        return _members_from_cargo()
    return workspace_members()


def extension_path() -> Path | None:
    for path in _CANDIDATES:
        if path.is_file():
            return path
    return None


def load_extension() -> ModuleType:
    global _MODULE
    if _MODULE is not None:
        return _MODULE
    path = extension_path()
    if path is None:
        searched = "\n".join(str(item) for item in _CANDIDATES)
        raise ImportError(
            f"omega_prime_prime extension is not built. Searched:\n{searched}"
        )
    spec = importlib.util.spec_from_file_location("omega_prime_prime", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load omega_prime_prime from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MODULE = module
    return module


def crate_surface() -> dict:
    """Call ``crates.probe`` so each linked Prime crate runs in this process."""
    return load_extension().crates.probe()


def native_status() -> dict:
    try:
        module = load_extension()
    except ImportError as exc:
        try:
            crates = _workspace_members()
        except OSError:
            crates = []
        return {"loaded": False, "crates": crates, "error": str(exc)}
    probed = module.crates.probe()
    return {
        "loaded": True,
        "crates": list(module.linked_crates()),
        "error": None,
        "callable": sorted(probed),
    }


def validate_goal_objective(text: str) -> str:
    return load_extension().validate_goal_objective(text)


def goal_token_delta(prompt_tokens: int, completion_tokens: int) -> int:
    return int(load_extension().goal_token_delta(prompt_tokens, completion_tokens))


def parse_kernel_event(line: str) -> dict:
    """Parse one REPL protocol line with ``pa_core::kernel::protocol::parse_event``."""
    return load_extension().parse_kernel_event(line)
