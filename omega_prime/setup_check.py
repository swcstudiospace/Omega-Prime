"""Setup smoke check: verify an Omega Prime install end to end, locally.

`python -m omega_prime.setup_check --root <repo>`: runs the roster, template,
assembly, plugin, registry, ultrathink, substrate, and submodule checks and
exits 0 only when every required check passes. An unconfigured ultrathink
checkout is reported as a skip; missing substrate configuration fails with
guidance and no reachability probe. An empty ``prime-agent`` checkout
fails and prints ``git submodule update --init --recursive``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omega_prime.assemble import load_roster
from omega_prime.integrations.submodules import check_submodules
from omega_prime.integrations.ultrathink import (
    READINESS_ENV_KEYS,
    ultrathink_readiness,
)
from omega_prime.mcp_server import default_registry, roster_names
from omega_prime.settings import SettingsResult, load_settings


def check_roster(root: Path) -> tuple[bool, str]:
    """Roster JSON loads with its required keys."""
    path = root / "omega_prime" / "grokbot" / "rosters" / "default.json"
    try:
        load_roster(path)
    except SystemExit as exc:
        return False, str(exc)
    return True, f"roster ok ({path})"


def check_template(root: Path) -> tuple[bool, str]:
    """Template entries resolve to checked-in files and list them all.

    Both directions, like the doctor's `template_integrity` row: every entry
    must resolve to a checked-in skill or routine file, and every checked-in
    skill and routine must be listed.
    """
    from omega_prime.grokbot.manifest import lint_template

    lint = lint_template(root)
    if not lint.ok:
        parts = []
        if lint.missing_skills:
            parts.append("missing skills: " + ", ".join(lint.missing_skills))
        if lint.missing_routines:
            parts.append("missing routines: " + ", ".join(lint.missing_routines))
        if lint.unlisted_skills:
            parts.append("unlisted skills: " + ", ".join(lint.unlisted_skills))
        if lint.unlisted_routines:
            parts.append("unlisted routines: " + ", ".join(lint.unlisted_routines))
        return False, "template and disk disagree (" + "; ".join(parts) + ")"
    return True, (
        "template ok (every entry resolves and every checked-in file is listed)"
    )


def check_assembly(root: Path) -> tuple[bool, str]:
    """Assembled prompt matches a fresh rebuild."""
    from omega_prime.assemble import render

    omega_prime = root / "omega_prime"
    try:
        fresh = render(
            omega_prime, omega_prime / "grokbot" / "rosters" / "default.json"
        )
    except SystemExit as exc:
        return False, f"assembly failed: {exc}"
    shipped = omega_prime / "prompts-assembled" / "OMEGA_PRIME.xml"
    try:
        if shipped.read_text(encoding="utf-8") != fresh:
            return False, f"{shipped} is stale; rerun assemble-prompts.sh"
    except OSError as exc:
        return False, f"cannot read {shipped}: {exc}"
    return True, "assembly up to date"


def check_plugin(root: Path) -> tuple[bool, str]:
    """Generated Cursor plugin set matches a fresh render (no subprocess)."""
    from omega_prime.tooling import plugin

    try:
        fresh = plugin.render(root)
    except plugin.PluginError as exc:
        return False, f"plugin generation failed: {'; '.join(exc.problems)}"
    problems = plugin.drift(root, fresh)
    if problems:
        return False, (
            f"plugin set is stale: {'; '.join(problems)}; "
            f"rerun {plugin.GENERATOR} --write"
        )
    return True, f"plugin up to date ({len(fresh)} generated files)"


def check_registry(root: Path) -> tuple[bool, str]:
    """Default registry builds and serves the roster over the MCP handlers."""
    roster_path = (
        root / "omega_prime" / "contracts" / "tool-rosters" / "omega-prime.yaml"
    )
    try:
        roster = roster_names(roster_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return False, f"cannot read {roster_path}: {exc}"
    if not roster:
        return False, "roster names no tools"
    home = Path(os.path.expanduser("~"))
    try:
        registry = default_registry(root, home)
    except Exception as exc:
        return False, f"registry build failed: {type(exc).__name__}: {exc}"
    from omega_prime.mcp_server import list_tools_handler

    listed = asyncio.run(list_tools_handler(registry, roster)(None, None))
    served = [tool.name for tool in listed.tools]
    missing = [name for name in roster if name not in served]
    # Prime families are config-gated (default off), including rlm and
    # messaging. With a flag on, default_registry serves that family. This
    # check still treats a disabled family as an expected absence. The Red
    # Team family joins them through its top-level redteam.enabled gate.
    from omega_prime.tools.agent_message import MESSAGING_TOOL_NAMES
    from omega_prime.tools.autonomous import AUTONOMOUS_TOOL_NAMES
    from omega_prime.tools.goals import GOAL_TOOL_NAMES
    from omega_prime.tools.harness import HARNESS_TOOL_NAMES
    from omega_prime.tools.heartbeat import HEARTBEAT_TOOL_NAMES
    from omega_prime.tools.prime_runtime import KERNEL_TOOL_NAMES
    from omega_prime.tools.redteam import REDTEAM_TOOL_NAMES
    from omega_prime.tools.rlm import RLM_TOOL_NAMES

    gated = (
        set(RLM_TOOL_NAMES)
        | set(HARNESS_TOOL_NAMES)
        | set(GOAL_TOOL_NAMES)
        | set(HEARTBEAT_TOOL_NAMES)
        | set(AUTONOMOUS_TOOL_NAMES)
        | set(MESSAGING_TOOL_NAMES)
        | set(KERNEL_TOOL_NAMES)
        | set(REDTEAM_TOOL_NAMES)
    )
    missing = [name for name in missing if name not in gated]
    if missing:
        return False, f"registry does not serve: {', '.join(missing)}"
    return True, f"registry serves {len(served)} roster tools"


def check_ultrathink(
    root: Path, env: Mapping[str, str] | None = None
) -> tuple[bool | None, str]:
    """Ultrathink checkout configured, or clearly skipped."""
    source = os.environ if env is None else env
    readiness = ultrathink_readiness(
        {key: source[key] for key in READINESS_ENV_KEYS if key in source}
    )
    state = readiness["state"]
    ship = "on" if readiness["ship_enabled"] else "off"
    discovery = readiness["preferred_state_dir"]
    root = readiness["root"]
    if state == "not_configured":
        return None, (
            f"ultrathink {state}: ULTRATHINK_ROOT not set; ship {ship}; "
            f"plan discovery dir: {discovery}"
        )
    if state == "root_invalid":
        problems = "; ".join(readiness["problems"])
        return False, (
            f"ultrathink {state}: ULTRATHINK_ROOT={root}; {problems}; "
            f"ship {ship}; plan discovery dir: {discovery}"
        )
    return True, (
        f"ultrathink {state}: root={root}; ship {ship}; plan discovery dir: {discovery}"
    )


def check_substrate(
    root: Path, settings: SettingsResult | None = None
) -> tuple[bool, str]:
    """Report saved substrate configuration without probing the endpoint."""
    del root
    from omega_prime.integrations.binding import secret_value
    from omega_prime.substrate.client import SubstrateClient

    if settings is None:
        settings = load_settings(dict(os.environ))
    env = settings.effective_env
    if settings.status in ("malformed", "rejected"):
        details = ", ".join(settings.reasons) or settings.status
        if settings.rejected_names:
            details += " (" + ", ".join(settings.rejected_names) + ")"
        return False, f"settings {settings.status}: {details}; process environment only"

    token = secret_value(env, "SUBSTRATE_TOKEN") or secret_value(
        env, "SUBSTRATE_TOKEN_GROK_BOT"
    )
    client = SubstrateClient(
        env.get("SUBSTRATE_URL") or None,
        token=token or None,
        honor_binding=True,
        binding_env=env,
    )
    base, error = client._resolve_base()
    if base is None:
        return (
            False,
            (error or "substrate is not configured")
            + "; set SUBSTRATE_URL via process environment or settings file",
        )
    detail = "substrate configured-only; reachability not checked"
    if not token:
        detail += "; anonymous mode"
    if settings.status == "loaded":
        detail += "; settings loaded: " + ", ".join(settings.loaded_names)
    return True, detail


CHECKS = (
    ("roster", check_roster),
    ("template", check_template),
    ("assembly", check_assembly),
    ("plugin", check_plugin),
    ("registry", check_registry),
    ("ultrathink", check_ultrathink),
    ("substrate", check_substrate),
    ("submodules", check_submodules),
)


def run_checks(root: Path) -> dict[str, Any]:
    """Run every check; `{name: {ok, detail}}` plus `passed`."""
    settings_error: str | None = None
    try:
        settings = load_settings(dict(os.environ))
    except Exception as exc:
        settings = None
        settings_error = f"{type(exc).__name__}: {exc}"
    results: dict[str, Any] = {}
    for name, fn in CHECKS:
        try:
            if name == "ultrathink":
                env = settings.effective_env if settings is not None else os.environ
                ok, detail = check_ultrathink(root, env)
            elif name == "substrate" and settings_error is not None:
                ok, detail = False, f"settings load failed: {settings_error}"
            elif name == "substrate":
                ok, detail = check_substrate(root, settings)
            else:
                ok, detail = fn(root)
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results[name] = {"ok": ok, "detail": detail}
    results["passed"] = all(
        item["ok"] is not False
        for item in (v for k, v in results.items() if k != "passed")
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omega-prime-setup-check")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    report = run_checks(args.root.resolve())
    for name, _ in CHECKS:
        item = report[name]
        mark = (
            "ok" if item["ok"] is True else ("skip" if item["ok"] is None else "FAIL")
        )
        print(f"[{mark}] {name}: {item['detail']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
