"""Purple-team sidecar harness entry point: fail-closed authorization check.

Ships BLOCKED (user decision 2026-10-13): with default config — or any
enabled-but-unsigned setup — ``python sidecar/harness.py --check`` prints
the exact blocking cause and exits 3 (BLOCKED). It exits 0 reporting IDLE
only when a valid, unexpired, signed RoE artifact resolves. No ``RUNNING``
path executes without a RoE, no network calls are made, and no live
engagement code paths execute: this module only reads local files.

Resolution order (kill switch first, mirroring the registry guard):

1. ``OMEGA_PRIME_REDTEAM_KILL`` truthy ⇒ ``BLOCKED`` (``kill-switch-set``).
2. ``OMEGA_PRIME_REDTEAM_ENABLED`` falsy ⇒ ``BLOCKED`` (``disabled``).
3. ``OMEGA_PRIME_REDTEAM_ROE_REQUIRED`` explicitly falsy ⇒ ``IDLE``
   (operator opt-out of the RoE requirement; default is required).
4. Otherwise the RoE at ``OMEGA_PRIME_REDTEAM_ROE_FILE``, verified with
   ``OMEGA_PRIME_REDTEAM_ROE_KEY``, decides: valid ⇒ ``IDLE``, anything
   else ⇒ ``BLOCKED`` with the validator's exact cause (``roe-missing`` /
   ``roe-expired`` / ``roe-invalid``).

Every evaluation logs one ``IDLE`` → verdict transition to
``sidecar/reports/transitions.ndjson`` via :mod:`sidecar.state`.

Stdlib-only. Never imports ``omega_prime.redteam`` or ``redteam/``.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sidecar.roe import authorize  # noqa: E402
from sidecar.state import TERMINAL_STATES, transition  # noqa: E402

EXIT_BLOCKED = 3

VERSION_FILE = _REPO_ROOT / "sidecar" / "VERSION"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def harness_version() -> str:
    """Harness version string from ``sidecar/VERSION`` (``"unknown"`` fallback)."""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def evaluate(env: Mapping[str, str]) -> tuple[str, str, str]:
    """Resolve ``(state, cause, detail)`` from env/config mirrors.

    Reads the same keys ``omega_prime/config.py`` maps from
    ``OMEGA_PRIME_REDTEAM_*`` so ``--check`` agrees with the registry guard.
    """
    get = lambda name: (env.get(name) or "").strip()  # noqa: E731
    if _truthy(get("OMEGA_PRIME_REDTEAM_KILL")):
        return (
            "BLOCKED",
            "kill-switch-set",
            "kill switch set (OMEGA_PRIME_REDTEAM_KILL) — "
            "takes precedence over any RoE artifact",
        )
    if not _truthy(get("OMEGA_PRIME_REDTEAM_ENABLED")):
        return (
            "BLOCKED",
            "disabled",
            "redteam.enabled is false (default off) — "
            "enable only with a signed RoE artifact",
        )
    required_raw = get("OMEGA_PRIME_REDTEAM_ROE_REQUIRED")
    if required_raw and not _truthy(required_raw):
        return (
            "IDLE",
            "re-evaluated",
            "RoE requirement explicitly disabled (OMEGA_PRIME_REDTEAM_ROE_REQUIRED=0)",
        )
    key_text = get("OMEGA_PRIME_REDTEAM_ROE_KEY")
    key = key_text.encode("utf-8") if key_text else None
    decision = authorize(get("OMEGA_PRIME_REDTEAM_ROE_FILE"), key=key)
    if decision.ok:
        return "IDLE", "re-evaluated", decision.detail
    return "BLOCKED", decision.cause, decision.detail


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """Evaluate authorization, log the transition, print cause, exit code."""
    parser = argparse.ArgumentParser(
        description="Purple-team sidecar harness check (fail-closed; exit 3 = BLOCKED)."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="evaluate authorization and exit 3 when BLOCKED",
    )
    parser.parse_args(argv)
    environ = env if env is not None else os.environ
    state, cause, detail = evaluate(environ)
    try:
        if state in TERMINAL_STATES or state == "IDLE":
            transition("IDLE", state, cause)
    except Exception:
        pass  # audit logging never changes the verdict
    print(f"sidecar {harness_version()}: {state} ({cause}): {detail}")
    return 0 if state == "IDLE" else EXIT_BLOCKED


if __name__ == "__main__":
    sys.exit(main())
