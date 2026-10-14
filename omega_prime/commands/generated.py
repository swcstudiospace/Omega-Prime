# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Generated ``/omega-<name>`` commands, one per checked-in skill and routine.

The builder is deterministic. It reads ``omega_prime/skills/*/SKILL.md``
(keyed by folder name) and ``omega_prime/routines/*.md`` (keyed by stem)
under a root directory, and returns ``CommandSpec`` entries sorted by name.

Naming rule:

1. ``base = "omega-" + stem.removeprefix("omega-")``. The prefix is never
   doubled, so the skill ``omega-commands`` becomes ``/omega-commands``.
2. A name must match ``^omega-[a-z0-9]+(?:-[a-z0-9]+)*$`` and be at most 64
   ASCII bytes. Anything else aborts generation, naming the source path.
3. Curated names win. When a base equals a curated name, or two generated
   sources share a base, every generated claimant takes ``-skill`` or
   ``-routine``.
4. A collision left after suffixing aborts generation, naming both sources.
   Nothing is dropped or shadowed.

"Checked in" means git-tracked when ``root/.git`` exists and git answers,
otherwise the files on disk. A learned skill written at runtime is not
tracked, so it never joins the catalog of a checkout.

This module imports nothing from ``omega_prime.commands`` or
``omega_prime.grokbot``: both import it.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from omega_prime.skills_runtime.guards import parse_frontmatter

CATALOG_ROOT = Path(__file__).resolve().parents[2]
"""The directory that contains the ``omega_prime`` package."""

NAME_PATTERN = re.compile(r"omega-[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_NAME_LENGTH = 64
COMMAND_PREFIX = "omega-"

_SKILLS = ("omega_prime", "skills")
_ROUTINES = ("omega_prime", "routines")


class GenerationError(ValueError):
    """Generation aborted. The message names the offending source path(s)."""


@dataclass(frozen=True)
class CommandSpec:
    """One catalog entry. ``when`` is the sentence that tells Grok to use it.

    Curated entries keep the defaults. Generated entries set ``kind`` to
    ``skill`` or ``routine`` and ``source`` to the repo-relative file path.
    """

    name: str
    usage: str
    summary: str
    when: str
    needs_args: bool = False
    kind: str = "curated"
    source: str = ""


@dataclass(frozen=True)
class CommandSource:
    """One checked-in skill or routine that yields a generated command."""

    kind: str
    stem: str
    source: str
    description: str


def tracked_paths(root: Path) -> frozenset[str] | None:
    """Root-relative git-tracked skill and routine paths, or None without VCS."""
    if not (root / ".git").exists():
        return None
    try:
        listed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--",
                "/".join(_SKILLS),
                "/".join(_ROUTINES),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listed.returncode != 0:
        return None
    text = listed.stdout.decode("utf-8", "surrogateescape")
    return frozenset(item for item in text.split("\0") if item)


def checked_in_skills(root: Path) -> tuple[str, ...]:
    """Sorted folder names of checked-in ``skills/*/SKILL.md``; never raises."""
    return _checked_in_skills(root, tracked_paths(root))


def checked_in_routines(root: Path) -> tuple[str, ...]:
    """Sorted stems of checked-in ``routines/*.md``; never raises."""
    return _checked_in_routines(root, tracked_paths(root))


def discover_sources(root: Path) -> tuple[CommandSource, ...]:
    """Skills sorted by folder name, then routines sorted by stem."""
    tracked = tracked_paths(root)
    sources: list[CommandSource] = []
    for folder in _checked_in_skills(root, tracked):
        source = _relative(*_SKILLS, folder, "SKILL.md")
        text = _read(root, source)
        mapping, _body, error = parse_frontmatter(text)
        if mapping is None:
            raise GenerationError(f"generated commands: {source}: {error}")
        description = mapping.get("description", "").strip()
        if not description:
            raise GenerationError(
                f"generated commands: {source}: frontmatter has no description"
            )
        sources.append(CommandSource("skill", folder, source, description))
    for stem in _checked_in_routines(root, tracked):
        source = _relative(*_ROUTINES, f"{stem}.md")
        description = _routine_description(_read(root, source))
        if not description:
            raise GenerationError(
                f"generated commands: {source}: routine has no description line"
            )
        sources.append(CommandSource("routine", stem, source, description))
    return tuple(sources)


def name_commands(
    curated_names: Iterable[str], sources: Iterable[CommandSource]
) -> tuple[CommandSpec, ...]:
    """Apply the naming and collision rule. Pure: no file or process access."""
    curated = frozenset(curated_names)
    claims: dict[str, list[CommandSource]] = {}
    for item in sources:
        base = COMMAND_PREFIX + item.stem.removeprefix(COMMAND_PREFIX)
        _validate(base, item)
        claims.setdefault(base, []).append(item)
    owners: dict[str, str] = {}
    specs: list[CommandSpec] = []
    for base, claimants in claims.items():
        contested = base in curated or len(claimants) > 1
        for item in claimants:
            name = f"{base}-{item.kind}" if contested else base
            _validate(name, item)
            if name in curated:
                raise GenerationError(
                    f"generated commands: /{name} from {item.source} "
                    f"collides with the curated /{name}"
                )
            if name in owners:
                raise GenerationError(
                    f"generated commands: /{name} is claimed by both "
                    f"{owners[name]} and {item.source}"
                )
            owners[name] = item.source
            specs.append(_spec(name, item))
    return tuple(sorted(specs, key=lambda spec: spec.name))


def generate_commands(
    root: Path, curated_names: Iterable[str]
) -> tuple[CommandSpec, ...]:
    """Generated commands for the checked-in skills and routines under ``root``."""
    return name_commands(curated_names, discover_sources(root))


def _checked_in_skills(root: Path, tracked: frozenset[str] | None) -> tuple[str, ...]:
    directory = root.joinpath(*_SKILLS)
    names = [
        path.parent.name
        for path in directory.glob("*/SKILL.md")
        if path.is_file() and _counts(root, path, tracked)
    ]
    return tuple(sorted(names))


def _checked_in_routines(root: Path, tracked: frozenset[str] | None) -> tuple[str, ...]:
    directory = root.joinpath(*_ROUTINES)
    stems = [
        path.stem
        for path in directory.glob("*.md")
        if path.is_file() and _counts(root, path, tracked)
    ]
    return tuple(sorted(stems))


def _counts(root: Path, path: Path, tracked: frozenset[str] | None) -> bool:
    return tracked is None or path.relative_to(root).as_posix() in tracked


def _relative(*parts: str) -> str:
    return "/".join(parts)


def _read(root: Path, source: str) -> str:
    try:
        return root.joinpath(*source.split("/")).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GenerationError(
            f"generated commands: cannot read {source} ({type(exc).__name__})"
        ) from None


def _routine_description(text: str) -> str:
    match = re.search(r"<summary>(.*?)</summary>", text, re.DOTALL)
    if match is not None:
        summary = match.group(1).strip()
        if summary:
            return summary
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def _validate(name: str, item: CommandSource) -> None:
    # NAME_PATTERN admits only ASCII, so the length is the ASCII byte count.
    if NAME_PATTERN.fullmatch(name) is None or len(name) > MAX_NAME_LENGTH:
        raise GenerationError(
            f"generated commands: {item.source} yields invalid command name "
            f"{name!r} (expected omega-<kebab-case>, at most "
            f"{MAX_NAME_LENGTH} characters)"
        )


def _spec(name: str, item: CommandSource) -> CommandSpec:
    if item.kind == "skill":
        when = f"The user asks for the {item.stem} skill instructions."
    else:
        when = f"The user asks to run or load the {item.stem} routine."
    return CommandSpec(
        name=name,
        usage=f"/{name} [args]",
        summary=item.description,
        when=when,
        kind=item.kind,
        source=item.source,
    )


__all__ = [
    "CATALOG_ROOT",
    "CommandSource",
    "CommandSpec",
    "GenerationError",
    "checked_in_routines",
    "checked_in_skills",
    "discover_sources",
    "generate_commands",
    "name_commands",
    "tracked_paths",
]
