"""Ultrathink turn plan resolution: find this turn's plan, if any.

Probes `last-plan.json` across the host state directories (same order
as the ultrathink Grok host) and reports the plan + spec paths. A
missing file or an unreadable plan is `found: False`, never an
exception: there is simply no plan for this turn.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def candidate_state_dirs(env: Mapping[str, str]) -> list[Path]:
    """Return the ordered state directories used for turn-plan discovery."""
    home = env.get("HOME", os.path.expanduser("~"))
    grok_home = env.get("GROK_HOME", str(Path(home) / ".grok"))
    hermes_home = env.get("HERMES_HOME", str(Path(home) / ".hermes"))
    xdg = env.get("XDG_CONFIG_HOME", str(Path(home) / ".config"))
    ordered: list[str] = []
    if env.get("OMEGA_PRIME_STATE_DIR"):
        ordered.append(str(Path(env["OMEGA_PRIME_STATE_DIR"]) / "ultrathink"))
    if env.get("ULTRATHINK_STATE_DIR"):
        ordered.append(str(Path(env["ULTRATHINK_STATE_DIR"])))
    if env.get("GROK_PLUGIN_DATA"):
        ordered.append(str(Path(env["GROK_PLUGIN_DATA"]) / "ultrathink"))
    ordered.extend(
        [
            str(Path(grok_home) / "plugin-data" / "ultrathink"),
            str(Path(hermes_home) / "ultrathink"),
            str(Path(xdg) / "muse" / "ultrathink"),
        ]
    )
    if env.get("PI_CODING_AGENT_DIR"):
        ordered.append(str(Path(env["PI_CODING_AGENT_DIR"]) / "ultrathink"))
    else:
        ordered.append(str(Path(home) / ".omp" / "agent" / "ultrathink"))
    ordered.append(str(Path(home) / ".claude" / "ultrathink"))
    return [Path(path) for path in ordered]


def _candidates(env: dict[str, str]) -> list[Path]:
    return [directory / "last-plan.json" for directory in candidate_state_dirs(env)]


def resolve_turn_plan(
    *,
    state_dirs: list[str | Path] | None = None,
    env: dict[str, str] | None = None,
    max_age_hours: float = 24.0,
) -> dict[str, Any]:
    """Return `{found, plan_path, spec_path, spec_exists, stale}` for this turn.

    A plan older than `max_age_hours` belongs to an earlier request:
    it comes back `found: False` with `stale: True` so the turn
    never works a previous prompt's spec.
    """
    if state_dirs is not None:
        paths = [Path(d) / "last-plan.json" for d in state_dirs]
    else:
        paths = _candidates(dict(os.environ) if env is None else env)
    for path in paths:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            plan = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(plan, dict):
            continue
        try:
            age_hours = (time.time() - path.stat().st_mtime) / 3600.0
        except OSError:
            continue
        if age_hours > max_age_hours:
            return {
                "found": False,
                "plan_path": str(path),
                "spec_path": None,
                "spec_exists": False,
                "stale": True,
            }
        spec_path = (
            plan.get("specPath") if isinstance(plan.get("specPath"), str) else None
        )
        spec_exists = spec_path is not None and Path(spec_path).is_file()
        return {
            "found": True,
            "plan_path": str(path),
            "spec_path": spec_path,
            "spec_exists": spec_exists,
            "stale": False,
        }
    return {
        "found": False,
        "plan_path": None,
        "spec_path": None,
        "spec_exists": False,
        "stale": False,
    }


def _engine_annotation(
    plan: Mapping[str, Any], env: Mapping[str, str]
) -> dict[str, Any]:
    """omp run for this spec, or a fallback reason when the engine is not ready."""
    from omega_prime.engine.attach import ultrathink_engine

    return ultrathink_engine(plan, env)


def execute_ultrathink(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Resolve this turn's plan. An empty env does not scan the process home."""
    if env is None:
        mapping: Mapping[str, str] = os.environ
        plan = resolve_turn_plan()
    elif not env:
        mapping = env
        plan = {
            "found": False,
            "plan_path": None,
            "spec_path": None,
            "spec_exists": False,
            "stale": False,
        }
    else:
        mapping = env
        plan = resolve_turn_plan(env=dict(env))
    plan["publishes"] = False
    plan["engine"] = _engine_annotation(plan, mapping)
    engine = plan["engine"]
    if isinstance(engine, dict) and engine.get("failed"):
        plan["failed"] = True
        plan["error"] = engine.get("error") or engine.get("reason") or "engine"
    return plan


def main(argv: list[str] | None = None) -> int:
    """``python -m omega_prime.routines.ultrathink_turn``."""
    parser = argparse.ArgumentParser(
        prog="python -m omega_prime.routines.ultrathink_turn"
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Directory that contains last-plan.json (defaults to host plan paths)",
    )
    args = parser.parse_args(argv)
    if args.state_dir:
        plan = resolve_turn_plan(state_dirs=[args.state_dir])
        plan["publishes"] = False
        plan["engine"] = _engine_annotation(plan, os.environ)
    else:
        plan = execute_ultrathink(os.environ)
    json.dump(plan, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
