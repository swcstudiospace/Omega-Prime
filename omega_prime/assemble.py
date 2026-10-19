"""Assemble the single Omega Prime seat prompt.

Prepend prompts/_shared/core-directives.xml to prompts/bot-00-omega-prime.xml and
write prompts-assembled/OMEGA_PRIME.xml. Substitutes {{DEFAULT_BRANCH}}, {{BOT_ID}},
and {{ROSTER_VERSION}} from the roster JSON. A leftover {{...}} is an error.

Prime capability tool entries whose family is not enabled are omitted from the
effective prompt (LOOP-05); without an explicit config the assembly is
canonically default-off. The template stays the allowed superset. A run that
enables families never touches the canonical artifact; it needs ``--output``.

The template may name a skill or a routine only when that file exists.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PLACEHOLDER_RE = re.compile(r"\{\{[A-Z0-9_:]+\}\}")
BULLET_RE = re.compile(r"^-\s+([a-z0-9][a-z0-9-]*)\b")
TOOL_LINE_RE = re.compile(r'^\s*<tool\s+name="([^"]+)"\s*/>\s*$')
_XML_DECLARATION_RE = re.compile(r"<\?xml[^?]*\?>\s*")

SEAT_SOURCE = "bot-00-omega-prime.xml"
SEAT_NAME = "OMEGA_PRIME"
ROSTER_KEYS = ("bot_id", "default_branch", "roster_version")


def _prime_family_tools() -> dict[str, tuple[str, ...]]:
    """Prime family → rostered tool names, reusing each family's own constant."""
    from omega_prime.tools.agent_message import MESSAGING_TOOL_NAMES
    from omega_prime.tools.autonomous import AUTONOMOUS_TOOL_NAMES
    from omega_prime.tools.goals import GOAL_TOOL_NAMES
    from omega_prime.tools.harness import HARNESS_TOOL_NAMES
    from omega_prime.tools.heartbeat import HEARTBEAT_TOOL_NAMES
    from omega_prime.tools.prime_runtime import KERNEL_TOOL_NAMES
    from omega_prime.tools.rlm import RLM_TOOL_NAMES

    return {
        "rlm": tuple(RLM_TOOL_NAMES),
        "harness": tuple(HARNESS_TOOL_NAMES),
        "goals": tuple(GOAL_TOOL_NAMES),
        "heartbeat": tuple(HEARTBEAT_TOOL_NAMES),
        "autonomous": tuple(AUTONOMOUS_TOOL_NAMES),
        "messaging": tuple(MESSAGING_TOOL_NAMES),
        "kernel": tuple(KERNEL_TOOL_NAMES),
    }


def disabled_family_tools(config: dict[str, Any] | None) -> set[str]:
    """Tool names of Prime families not enabled in ``config`` (LOOP-05).

    ``None`` is the canonical default-off assembly: every Prime family is
    disabled without reading user config, HOME, or process environment.
    The Red Team family joins the same filter through its top-level
    ``redteam.enabled`` config key, so the default artifact omits it too.
    """
    from omega_prime.config import PRIME_FAMILIES, prime_enabled, redteam_enabled
    from omega_prime.tools.redteam import REDTEAM_TOOL_NAMES

    mapping = _prime_family_tools()
    if set(mapping) != set(PRIME_FAMILIES):
        raise SystemExit(
            "assemble-prompts: prime family tool map drifted from config.PRIME_FAMILIES"
        )
    if config is None:
        config = {}
    disabled = {
        name
        for family, names in mapping.items()
        for name in names
        if not prime_enabled(config, family)
    }
    if not redteam_enabled(config):
        disabled.update(REDTEAM_TOOL_NAMES)
    return disabled


def _without_declaration(text: str) -> str:
    """Drop one leading XML declaration so two sources can share a root."""
    stripped = text.lstrip("\ufeff")
    return _XML_DECLARATION_RE.sub("", stripped, count=1).strip("\n")


def one_document(core: str, body: str) -> str:
    """One well-formed document: a single declaration and an ``<omega_prime>`` root."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<omega_prime>\n"
        f"{_without_declaration(core)}\n"
        f"{_without_declaration(body)}\n"
        "</omega_prime>\n"
    )


def filter_disabled_tools(text: str, disabled: set[str]) -> str:
    """Drop ``<tool name=...>`` lines for disabled families, keep everything else."""
    if not disabled:
        return text
    kept: list[str] = []
    for line in text.splitlines():
        match = TOOL_LINE_RE.match(line)
        if match is not None and match.group(1) in disabled:
            continue
        kept.append(line)
    return "\n".join(kept) + "\n"


def package_root() -> Path:
    return Path(__file__).resolve().parent


def load_roster(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"assemble-prompts: cannot read roster {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise SystemExit(f"assemble-prompts: roster {path} is not an object")
    missing = [key for key in ROSTER_KEYS if key not in document]
    if missing:
        raise SystemExit(f"assemble-prompts: roster {path} lacks {', '.join(missing)}")
    return document


def substitute(text: str, roster: dict) -> str:
    values = {
        "{{DEFAULT_BRANCH}}": str(roster["default_branch"]),
        "{{BOT_ID}}": str(roster["bot_id"]),
        "{{ROSTER_VERSION}}": str(roster["roster_version"]),
    }
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def unfilled(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        found.extend((line_no, match) for match in PLACEHOLDER_RE.findall(line))
    return found


def render(root: Path, roster_path: Path, config: dict[str, Any] | None = None) -> str:
    """Assemble the seat prompt, omitting tools of disabled Prime families.

    The two-positional-argument call ``render(root, roster_path)`` is the
    canonical default-off assembly: no user config, HOME, or environment is
    read. Pass an explicit config (e.g. ``load_config()``) to offer the
    families it enables.
    """
    roster = load_roster(roster_path)
    core_path = root / "prompts" / "_shared" / "core-directives.xml"
    body_path = root / "prompts" / SEAT_SOURCE
    if not core_path.is_file():
        raise SystemExit(f"assemble-prompts: missing {core_path}")
    if not body_path.is_file():
        raise SystemExit(f"assemble-prompts: missing {body_path}")
    core = substitute(core_path.read_text(encoding="utf-8"), roster)
    body = substitute(body_path.read_text(encoding="utf-8"), roster)
    text = filter_disabled_tools(
        one_document(core, body), disabled_family_tools(config)
    )
    left = unfilled(text)
    if left:
        where = "; ".join(f"{name} at line {line_no}" for line_no, name in left[:5])
        raise SystemExit(f"assemble-prompts: unfilled placeholder(s): {where}")
    return text


def assembled_path(root: Path) -> Path:
    return root / "prompts-assembled" / f"{SEAT_NAME}.xml"


def write_assembled(destination: Path, text: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return destination


def section_bullets(template: str, heading: str) -> list[str]:
    lines = template.splitlines()
    start = None
    marker = f"## {heading}"
    for index, line in enumerate(lines):
        if line.strip() == marker:
            start = index + 1
            break
    if start is None:
        raise SystemExit(f"assemble-prompts: template missing section {heading!r}")
    names: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        match = BULLET_RE.match(line.strip())
        if match:
            names.append(match.group(1))
    return names


def template_gaps(root: Path, template: Path | None = None) -> list[str]:
    """Return repo-relative paths the template names that are not on disk."""
    path = template or (root / "grokbot" / "templates" / "OMEGA_PRIME.md")
    text = path.read_text(encoding="utf-8")
    gaps: list[str] = []
    for name in section_bullets(text, "Enabled skills"):
        skill = root / "skills" / name / "SKILL.md"
        if not skill.is_file():
            gaps.append(str(skill.relative_to(root)))
    for name in section_bullets(text, "Routines"):
        routine = root / "routines" / f"{name}.md"
        if not routine.is_file():
            gaps.append(str(routine.relative_to(root)))
    return gaps


def main(argv: list[str] | None = None) -> int:
    from omega_prime.config import PRIME_FAMILIES

    parser = argparse.ArgumentParser(prog="assemble-prompts")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--roster", type=Path, default=None)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--enable-family",
        action="append",
        choices=PRIME_FAMILIES,
        default=None,
        metavar="FAMILY",
        help=(
            "Offer one Prime capability family in the assembled prompt; "
            "repeatable. Without any flag the assembly is canonically "
            "default-off and reads no user config or environment."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Write the assembled prompt to (or with --check, compare it "
            "against) PATH instead of the canonical prompts-assembled "
            "artifact. Required with --enable-family, which never touches "
            "the canonical artifact."
        ),
    )
    args = parser.parse_args(argv)
    if args.enable_family and args.output is None:
        parser.error("--enable-family requires --output PATH")

    root = (args.root or package_root()).resolve()
    roster_path = (
        args.roster or (root / "grokbot" / "rosters" / "default.json")
    ).resolve()
    if not roster_path.is_file():
        raise SystemExit(f"assemble-prompts: missing roster {roster_path}")

    config: dict[str, Any] | None = None
    if args.enable_family:
        enabled = set(args.enable_family)
        config = {
            "prime": {
                family: {"enabled": family in enabled} for family in PRIME_FAMILIES
            }
        }

    text = render(root, roster_path, config)
    gaps = template_gaps(root)
    if gaps:
        for gap in gaps:
            print(f"  - {gap}", file=sys.stderr)
        raise SystemExit(
            "assemble-prompts: template names a skill or routine that is not on disk"
        )

    destination = (
        args.output.resolve() if args.output is not None else assembled_path(root)
    )
    shown = (
        destination.relative_to(root)
        if destination.is_relative_to(root)
        else destination
    )
    if args.check:
        if not destination.is_file():
            raise SystemExit(f"assemble-prompts --check: missing {destination}")
        have = destination.read_text(encoding="utf-8")
        if have != text:
            raise SystemExit(
                f"assemble-prompts --check: {shown} differs from the sources; "
                "run scripts/assemble-prompts.sh and commit the result"
            )
        print(f"assemble-prompts --check: {SEAT_NAME} up to date")
        return 0

    write_assembled(destination, text)
    print(f"wrote {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
