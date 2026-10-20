# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Isolated readiness support: disposable layout, fresh-child probe, child profile.

This module is stdlib-only at import time: importing it never touches
production code, the process environment, or the network. Production imports
(emulator, registry, catalog, host) happen lazily inside functions that run
exclusively in the fresh product child spawned by
``omega_prime.grokbot.readiness``.

Layout contract
---------------
``isolated_layout`` exports a minimal safe subset of the source tree into a
private disposable base, builds a small explicit child environment (never a
copy of ``os.environ``), and removes the base on all exits. The child process
runs production against the exported install only.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SCOPE = "isolated-local"

ROSTER_REL = Path("omega_prime/contracts/tool-rosters/omega-prime.yaml")
FIXTURE_NAME = "omega-prime.json"
DETAIL_LIMIT = 200

_SYSTEM_TMP = "/tmp"

# Non-Python data kept in the export follows a shipped/public allowlist, not a
# directory prefix: every candidate must pass ``_is_shipped_data``. Private or
# learned data (.env, keys, credential-named files, non-SKILL skill content) is
# excluded by rule even inside otherwise public directories.
_NONPY_TOP_FILES: tuple[str, ...] = ("omega_prime/ownership.yaml",)

_PRIVATE_NAME_PARTS = (
    "credential",
    "secret",
    "passwd",
    "password",
    "private",
    "token",
)
_PRIVATE_SUFFIXES = frozenset({".pem", ".key"})


def _looks_private(name: str) -> bool:
    """Whether ``name`` looks like private material that never ships."""
    lowered = name.lower()
    if lowered == ".env" or lowered.endswith(".env"):
        return True
    if Path(name).suffix.lower() in _PRIVATE_SUFFIXES:
        return True
    return any(part in lowered for part in _PRIVATE_NAME_PARTS)


def _is_shipped_data(rel: Path) -> bool:
    """Whether source-relative ``rel`` is shipped public data worth exporting.

    Provenance: contracts carry the roster/policy/packs/pin documents, prompts
    carry the shipped prompt inputs, skills ship exactly ``SKILL.md`` per
    skill, scripts carry setup helpers, commands carry the onboard seed,
    routines carry the shipped routine inputs render_prompt lists, grokbot
    carries loopback templates/rosters. Memory/session Python code is
    handled as code; their runtime data never matches this predicate.
    """
    if _is_excluded_file(rel):
        return False
    if _looks_private(rel.name):
        return False
    parts = rel.parts
    if len(parts) < 3 or parts[0] != "omega_prime":
        return parts == ("omega_prime", "ownership.yaml")
    area = parts[1]
    suffix = rel.suffix.lower()
    if area == "contracts":
        return suffix in {".yaml", ".yml", ".json", ".md"}
    if area in ("prompts", "prompts-assembled"):
        return suffix in {".xml", ".md"}
    if area == "skills":
        return rel.name == "SKILL.md"
    if area == "scripts":
        return suffix in {".sh", ".py", ".md"}
    if area == "commands":
        return len(parts) > 3 and parts[2] == "seeds" and suffix in {".txt", ".md"}
    if area == "routines":
        return suffix == ".md"
    if area == "grokbot" and len(parts) > 3 and parts[2] in ("templates", "rosters"):
        return suffix in {".md", ".json"}
    return False


# Public Prime resources resolved in-child relative to the install root.
_PRIME_STATIC_FILES: tuple[str, ...] = (
    "prime-agent/Cargo.toml",
    "prime-agent/LICENSE",
    "prime-agent/target/debug/libomega_prime_prime.so",
)
_PRIME_STATIC_DIRS: tuple[str, ...] = (
    "prime-agent/prime-agent-runtime/src",
    "prime-agent/skills",
)

_EXCLUDED_DIR_NAMES = frozenset({"__pycache__", ".git", "node_modules", ".venv"})
_EXCLUDED_SUFFIXES = frozenset(
    {".db", ".sqlite", ".sqlite3", ".wal", ".shm", ".journal", ".pyc", ".pyo", ".log"}
)
_EXCLUDED_FILE_NAMES = frozenset({"jobs.json", "intake.json", ".DS_Store", ".planning"})
# A source-level user config overlay must never export (the fresh child owns
# the fixture). Scoped to this exact path so the canonical nested seat policy
# ``omega_prime/contracts/policies/omega-prime.json`` still ships.
_EXCLUDED_PATHS = frozenset({Path("omega_prime/omega-prime.json")})

_CHILD_ERROR_PREFIX = "readiness-child-error: "


class IsolationError(RuntimeError):
    """The disposable layout could not be built safely. Fail closed."""


class ReadinessFailure(Exception):
    """One scenario assertion failed. Captured as a failed check, never blocked."""


@dataclass
class LocalLayout:
    """Paths and explicit environment for one isolated readiness run."""

    source: Path
    base: Path
    install: Path
    home: Path
    work: Path
    state: Path
    audit: Path
    command_log: Path
    env: dict[str, str] = field(default_factory=dict)
    prime_missing: tuple[str, ...] = ()


def build_child_env(
    *,
    base: Path,
    install: Path,
    home: Path,
    work: Path,
    state: Path,
    audit: Path,
    command_log: Path,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the child's explicit environment. Never reads ``os.environ``.

    Only interpreter/locale settings plus fresh fixture values are present.
    ``PATH`` is derived from the running interpreter, never inherited, so host
    executable roots do not cross the boundary.
    """
    run_dir = audit / "run"
    tmp_dir = base / "tmp"
    python_bin = Path(sys.executable).parent
    env = {
        "PATH": os.pathsep.join([str(python_bin), "/usr/bin", "/bin"]),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
        "PYTHONPATH": str(install),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(base / "config"),
        "XDG_DATA_HOME": str(base / "data"),
        "XDG_CACHE_HOME": str(base / "cache"),
        "XDG_STATE_HOME": str(base / "xdgstate"),
        "XDG_RUNTIME_DIR": str(run_dir),
        "TMPDIR": str(tmp_dir),
        "TEMP": str(tmp_dir),
        "TMP": str(tmp_dir),
        "OMEGA_PRIME_WORK_ROOT": str(work),
        "OMEGA_PRIME_STATE_DIR": str(state),
        "OMEGA_PRIME_AUDIT_LOG": str(audit / "audit.jsonl"),
        "OMEGA_PRIME_COMMAND_LOG": str(command_log),
        "ULTRATHINK_ROOT": str(base / "ultrathink"),
        "PRIME_AGENT_CODING_AGENT_DIR": str(base / "prime-agent-dir"),
    }
    if extra:
        for key, value in extra.items():
            env[str(key)] = str(value)
    return env


def _is_excluded_file(rel: Path) -> bool:
    return (
        rel in _EXCLUDED_PATHS
        or rel.name in _EXCLUDED_FILE_NAMES
        or rel.suffix.lower() in _EXCLUDED_SUFFIXES
    )


def _tracked_files(source: Path) -> set[str] | None:
    """Source-relative VCS-tracked paths, or None when VCS is unavailable.

    Tracked-ness is the shipped provenance for non-Python data: a newly
    seeded learned skill is untracked, so it can never become shipped on
    re-export. Untracked newly implemented code stays eligible (handled by
    the caller, which copies ``.py`` unconditionally).
    """
    try:
        toplevel = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--show-toplevel"],
            capture_output=True,
            timeout=30,
        )
        if toplevel.returncode != 0:
            return None
        listed = subprocess.run(
            ["git", "-C", str(source), "ls-files", "-z"],
            capture_output=True,
            timeout=60,
        )
        if listed.returncode != 0:
            return None
    except Exception:
        # Best-effort provenance only: a stubbed or broken git client must
        # never break the export (seen under test doubles for subprocess).
        return None
    try:
        prefix = (
            Path(source)
            .resolve()
            .relative_to(Path(toplevel.stdout.decode().strip()).resolve())
        )
    except ValueError:
        return None
    trimmed = prefix.as_posix()
    tracked: set[str] = set()
    for raw in listed.stdout.split(b"\0"):
        if not raw:
            continue
        text = raw.decode("utf-8", "replace")
        if trimmed == ".":
            tracked.add(text)
        elif text == trimmed or text.startswith(trimmed + "/"):
            tracked.add(text[len(trimmed) + 1 :])
    return tracked


_INVENTORY_NAME = ".omega-readiness-inventory.json"


def _read_inventory(source: Path) -> set[str] | None:
    """Trusted data inventory preserved by an outer export, if present.

    A nested VCS-less source (an outer disposable install) carries the exact
    data set its own export vetted; without it, unvetted learned data would
    ship on re-export. Returns None when the source carries no inventory.
    """
    try:
        raw = (source / _INVENTORY_NAME).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) for item in parsed
    ):
        return None
    return set(parsed)


def _data_eligible(
    rel: Path, tracked: set[str] | None, inventory: set[str] | None
) -> bool:
    """Shipped predicate plus trusted provenance for non-Python data.

    Fail closed: without verified Git membership or a valid inherited
    inventory, unvetted data (including learned ``SKILL.md``) is refused.
    A source archive or container without ``.git`` MUST carry a verified
    ``.omega-readiness-inventory.json`` (written by a vetted outer export)
    or data will not export; the child then fails truthfully on the absent
    resource, never on silently admitted bytes.
    """
    if not _is_shipped_data(rel):
        return False
    if tracked is not None:
        return rel.as_posix() in tracked
    if inventory is not None:
        return rel.as_posix() in inventory
    return False


def _is_skill_code(rel: Path) -> bool:
    """Whether ``rel`` is Python living inside skill content (not product code).

    Each ``omega_prime/skills/<name>/`` directory is shipped skill content;
    a newly seeded learned skill arrives as untracked ``SKILL.md`` plus
    helper scripts there. Product Python (memory, session, tools, routines,
    skills_runtime, and the credential/security modules) stays unconditionally
    eligible so newly implemented code always ships.
    """
    parts = rel.parts
    return len(parts) >= 3 and parts[0] == "omega_prime" and parts[1] == "skills"


def _py_eligible(
    rel: Path, tracked: set[str] | None, inventory: set[str] | None
) -> bool:
    """Provenance gate for skill-content Python; product code always passes.

    Ordinary product Python (everything outside ``omega_prime/skills/``)
    stays unconditionally eligible so newly implemented code always ships
    without a Git gate. Skill-content Python fails closed without verified
    Git membership or a valid inherited inventory, so an untracked learned
    helper can never ship on re-export.
    """
    if not _is_skill_code(rel):
        return True
    if tracked is not None:
        return rel.as_posix() in tracked
    if inventory is not None:
        return rel.as_posix() in inventory
    return False


def _has_pruned_component(rel: Path) -> bool:
    """Whether any ancestor component of ``rel`` is a pruned/excluded entry.

    Mirrors the export walk's directory pruning (``_EXCLUDED_DIR_NAMES`` plus
    the ``state`` runtime directory) and the excluded file names, so an alias
    target under a pruned ancestor can never re-enter through a symlink.
    Component equality (never substring) keeps ``omega_prime/state.py``
    admissible while ``omega_prime/state/...`` is not.
    """
    return any(
        part in _EXCLUDED_DIR_NAMES or part == "state" or part in _EXCLUDED_FILE_NAMES
        for part in rel.parts
    )


def _is_safe_shape(rel: Path) -> bool:
    """Name/suffix/exclusion shape only; no provenance involved."""
    if _has_pruned_component(rel):
        return False
    if _is_excluded_file(rel):
        return False
    return not _looks_private(rel.name)


def _is_prime_rel(rel: Path) -> bool:
    """Whether ``rel`` lives under the optional ``prime-agent`` subtree."""
    return len(rel.parts) > 0 and rel.parts[0] == "prime-agent"


def _prime_tracked(source: Path) -> set[str] | None:
    """Submodule-tracked ``prime-agent/...`` paths, or None when unavailable.

    The superproject never lists submodule contents, so provenance for the
    optional Prime subtree comes from the submodule itself. A nested
    VCS-less re-export (plain directory, no submodule git) yields None and
    falls back to the inherited inventory; anything else fails closed.
    """
    try:
        listed = subprocess.run(
            ["git", "-C", str(source / "prime-agent"), "ls-files", "-z"],
            capture_output=True,
            timeout=60,
        )
        if listed.returncode != 0:
            return None
    except Exception:
        # Best-effort provenance only: a broken git client fails closed
        # here (unlike ``_tracked_files``) because Prime content is
        # optional; the child records it as named missing prerequisites.
        return None
    tracked: set[str] = set()
    for raw in listed.stdout.split(b"\0"):
        if not raw:
            continue
        tracked.add("prime-agent/" + raw.decode("utf-8", "replace"))
    return tracked


def _prime_eligible(
    rel: Path, prime_tracked: set[str] | None, inventory: set[str] | None
) -> bool:
    """Safe shape plus submodule/inventory provenance for Prime resources.

    Pinned static files bypass this predicate (the pin plus ``_is_safe_shape``
    is their admission); everything under the pinned directories must prove
    submodule-tracked or inherited-inventory membership, so seeded learned,
    private, or config entries can never enter. Fails closed.
    """
    if not _is_safe_shape(rel):
        return False
    if prime_tracked is not None:
        return rel.as_posix() in prime_tracked
    if inventory is not None:
        return rel.as_posix() in inventory
    return False


def _source_entry_eligible(
    rel: Path,
    tracked: set[str] | None,
    inventory: set[str] | None,
    prime_tracked: set[str] | None = None,
) -> bool:
    """One complete admission predicate for entries, aliases, and targets.

    Ordinary entries, alias rels, and resolved target rels all pass through
    this single choke point: pruned/excluded shape first, then provenance.
    Ordinary safe product Python stays Git-ungated by design; private-named
    product Python is admitted only with verified Git or inherited-inventory
    membership (so shipped ``secret_guard``/``tokens``-style modules survive
    while fresh credential-named files fail closed); data, skill code, and
    Prime content fail closed without verified membership.
    """
    if _has_pruned_component(rel):
        return False
    if _is_excluded_file(rel):
        return False
    if _is_prime_rel(rel):
        return _prime_eligible(rel, prime_tracked, inventory)
    if rel.suffix == ".py":
        if _is_skill_code(rel):
            return _py_eligible(rel, tracked, inventory)
        if _looks_private(rel.name):
            posix = rel.as_posix()
            if tracked is not None:
                return posix in tracked
            if inventory is not None:
                return posix in inventory
            return False
        return True
    return _data_eligible(rel, tracked, inventory)


def _reject_symlink(
    link: Path,
    source: Path,
    tracked: set[str] | None = None,
    inventory: set[str] | None = None,
    prime_tracked: set[str] | None = None,
    *,
    pinned: bool = False,
) -> Path:
    """Resolve ``link``; raise unless alias AND target stay in the source set.

    Both ends pass ``_source_entry_eligible``: an admissible alias pointing
    at a pruned ``state`` target, an unvetted learned helper, or a private
    Prime resource is refused before any byte copies. ``pinned`` covers the
    explicitly enumerated Prime static files only: the pin vouches for
    membership, so the target needs containment plus safe shape.
    """
    try:
        alias_rel = link.relative_to(source)
    except ValueError:
        raise IsolationError(
            f"export refuses symlink escaping the source tree: {link.name}"
        ) from None
    target = link.resolve()
    try:
        target_rel = target.relative_to(source)
    except ValueError:
        raise IsolationError(
            f"export refuses symlink escaping the source tree: {link.name}"
        ) from None
    if target.is_dir():
        raise IsolationError(f"export refuses directory symlink: {link.name}")
    if pinned:
        if not _is_safe_shape(alias_rel) or not _is_safe_shape(target_rel):
            raise IsolationError(
                f"export refuses symlink outside the included set: {link.name}"
            )
        return target
    if not _source_entry_eligible(alias_rel, tracked, inventory, prime_tracked):
        raise IsolationError(
            f"export refuses symlink outside the included set: {link.name}"
        )
    if not _source_entry_eligible(target_rel, tracked, inventory, prime_tracked):
        raise IsolationError(
            f"export refuses symlink target outside the included set: {link.name}"
        )
    return target


def _validate_prime_tree(
    entry: str,
    src: Path,
    source: Path,
    *,
    prime_tracked: set[str] | None = None,
    inventory: set[str] | None = None,
) -> None:
    """Reject escaping/excluded symlinks under a Prime dir BEFORE copying."""
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        current = Path(dirpath)
        for name in (*dirnames, *filenames):
            full = current / name
            if not full.is_symlink():
                continue
            _reject_symlink(full, source, None, inventory, prime_tracked)


def _export_prime_dir(
    entry: str,
    src: Path,
    dest: Path,
    source: Path,
    *,
    prime_tracked: set[str] | None,
    inventory: set[str] | None,
    shipped: list[str],
    _seen: set[tuple[int, int]] | None = None,
) -> None:
    """Copy one pinned Prime dir per entry with safe-resource filtering.

    Regular private files (``.env``, credentials), DB/state/config material,
    excluded names/suffixes, and unvetted learned entries are skipped, never
    copied; unsafe aliases raise via ``_reject_symlink`` before any copy.
    Directories materialize on demand only, so fully filtered subtrees leave
    no empty husk. Symlink cycles fail closed via visited-dir detection.
    """
    seen = _seen if _seen is not None else set()
    try:
        identity = (src.stat().st_dev, src.stat().st_ino)
    except OSError:
        raise IsolationError(
            f"export refuses unreadable prime resource: {entry}"
        ) from None
    if identity in seen:
        raise IsolationError(f"export refuses prime symlink cycle: {entry}")
    seen.add(identity)
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        current = Path(dirpath)
        try:
            rel_anchor = current.relative_to(src)
            _ = current.relative_to(source)
        except ValueError:
            raise IsolationError("prime walk escaped the source tree") from None
        for name in list(dirnames):
            full = current / name
            if name in _EXCLUDED_DIR_NAMES or name in ("state", "target"):
                dirnames.remove(name)
                continue
            if full.is_symlink():
                _reject_symlink(full, source, None, inventory, prime_tracked)
                dirnames.remove(name)
                _export_prime_dir(
                    f"{entry}/{name}",
                    full.resolve(),
                    dest / rel_anchor / name,
                    source,
                    prime_tracked=prime_tracked,
                    inventory=inventory,
                    shipped=shipped,
                    _seen=seen,
                )
        for name in filenames:
            full = current / name
            try:
                rel = full.relative_to(source)
            except ValueError:
                raise IsolationError("prime walk escaped the source tree") from None
            if full.is_symlink():
                _reject_symlink(full, source, None, inventory, prime_tracked)
                target = full.resolve()
                dest_file = dest / rel_anchor / name
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, dest_file)
                shutil.copymode(target, dest_file)
                shipped.append(rel.as_posix())
                continue
            if not _prime_eligible(rel, prime_tracked, inventory):
                continue
            dest_file = dest / rel_anchor / name
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full, dest_file)
            shipped.append(rel.as_posix())


def _export_tree(
    source: Path, install: Path, *, include_prime: bool
) -> tuple[str, ...]:
    """Copy the safe source subset into ``install``. Fail closed on symlinks.

    Optional Prime resources are copied only when ``include_prime`` is set;
    absent optional artifacts are returned (never a layout failure) so the
    child records them as named capability prerequisites. The inventory
    records every actually exported resource (package and optional Prime)
    so a nested VCS-less re-export inherits exactly the vetted set.
    """
    package = source / "omega_prime"
    tracked = _tracked_files(source)
    inventory = _read_inventory(source)
    prime_tracked = _prime_tracked(source)
    shipped: list[str] = []
    if not package.is_dir():
        raise IsolationError("source has no omega_prime package")
    else:
        for dirpath, dirnames, filenames in os.walk(package, followlinks=False):
            current = Path(dirpath)
            for entry in list(dirnames):
                full = current / entry
                if (
                    entry in _EXCLUDED_DIR_NAMES
                    or entry == "state"
                    or entry in _EXCLUDED_FILE_NAMES
                ):
                    dirnames.remove(entry)
                elif full.is_symlink():
                    _reject_symlink(full, source, tracked, inventory, prime_tracked)
                    dirnames.remove(entry)
            try:
                rel_dir = current.relative_to(source)
            except ValueError:
                raise IsolationError("walk escaped the source tree") from None
            dest_dir = install / rel_dir
            dest_dir.mkdir(parents=True, exist_ok=True)
            for name in filenames:
                full = current / name
                rel = rel_dir / name
                if full.is_symlink():
                    _reject_symlink(full, source, tracked, inventory, prime_tracked)
                    target = full.resolve()
                    dest = dest_dir / name
                    shutil.copyfile(target, dest)
                    shutil.copymode(target, dest)
                    shipped.append(rel.as_posix())
                    continue
                if not _source_entry_eligible(rel, tracked, inventory, prime_tracked):
                    continue
                shutil.copy2(full, dest_dir / name)
                shipped.append(rel.as_posix())
        for entry in _NONPY_TOP_FILES:
            src = source / entry
            if (
                src.is_file()
                and not src.is_symlink()
                and _data_eligible(Path(entry), tracked, inventory)
            ):
                shutil.copy2(src, install / entry)
                shipped.append(Path(entry).as_posix())
    missing: list[str] = []
    if include_prime:
        for entry in _PRIME_STATIC_FILES:
            src = source / entry
            if not src.is_file():
                missing.append(entry)
                continue
            if src.is_symlink():
                _reject_symlink(
                    src, source, tracked, inventory, prime_tracked, pinned=True
                )
                dest = install / entry
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src.resolve(), dest)
                shutil.copymode(src.resolve(), dest)
            else:
                if not _is_safe_shape(Path(entry)):
                    missing.append(entry)
                    continue
                dest = install / entry
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
                shutil.copymode(src, dest)
            shipped.append(entry)
        for entry in _PRIME_STATIC_DIRS:
            src = source / entry
            if src.is_symlink():
                _reject_symlink(
                    src, source, tracked, inventory, prime_tracked, pinned=True
                )
                src = src.resolve()
            if not src.is_dir():
                missing.append(entry)
                continue
            _validate_prime_tree(
                entry, src, source, prime_tracked=prime_tracked, inventory=inventory
            )
            _export_prime_dir(
                entry,
                src,
                install / entry,
                source,
                prime_tracked=prime_tracked,
                inventory=inventory,
                shipped=shipped,
            )
    (install / _INVENTORY_NAME).write_text(
        json.dumps(sorted(shipped), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return tuple(missing)


def _layout_dirs(base: Path) -> dict[str, Path]:
    return {
        "install": base / "install",
        "home": base / "home",
        "work": base / "work",
        "state": base / "state",
        "audit": base / "audit",
    }


@contextlib.contextmanager
def isolated_layout(
    source: str | Path, *, include_prime: bool = False
) -> Iterator[LocalLayout]:
    """Yield an isolated layout built from ``source``. Cleans up on all exits.

    ``include_prime`` selects the optional Prime profile: only then are the
    optional native/checkout/skill resources copied. The base lives under the
    system temp location, never under caller-inherited ``TMPDIR``. Every
    creation, export, and yield step sits inside the cleanup guard, so an
    export or symlink failure leaves no base behind. The Prime fixture is
    written by the fresh child from canonical config, not by the export.
    """
    src = Path(source).resolve()
    if not (src / ROSTER_REL).is_file():
        raise IsolationError(f"no canonical roster under {src}")
    base = Path(tempfile.mkdtemp(prefix="omega-readiness-", dir=_SYSTEM_TMP))
    try:
        dirs = _layout_dirs(base)
        install = dirs["install"]
        home = dirs["home"]
        work = dirs["work"]
        state = dirs["state"]
        audit = dirs["audit"]
        command_log = base / "command-log.jsonl"
        for directory in (*dirs.values(), base / "prime-kernel-seat"):
            directory.mkdir(parents=True, exist_ok=True)
        prime_missing = _export_tree(src, install, include_prime=include_prime)
        (install / "prime-kernel").mkdir(parents=True, exist_ok=True)
        env = build_child_env(
            base=base,
            install=install,
            home=home,
            work=work,
            state=state,
            audit=audit,
            command_log=command_log,
        )
        yield LocalLayout(
            source=src,
            base=base,
            install=install,
            home=home,
            work=work,
            state=state,
            audit=audit,
            command_log=command_log,
            env=env,
            prime_missing=prime_missing,
        )
    finally:
        shutil.rmtree(base, ignore_errors=True)


_CURATE_WITHHELD = "withheld: secret gate tripped"
"""Fixed safe metadata published when a diagnostic trips the secret gate.

Constant bytes only: it never echoes detail input, so a scanner-only token
split across the clip window cannot leak as a below-threshold fragment.
"""


def _curate(text: str, redact: Callable[[str], str], limit: int = DETAIL_LIMIT) -> str:
    """Redact, gate the FULL text, then clip to ``limit`` chars.

    Known-secret/audit redaction runs first; the unchanged
    ``assert_no_secrets`` gate then classifies the full redacted diagnostic
    BEFORE any flatten/clip loss. A trip publishes only
    ``_CURATE_WITHHELD`` (no tail bytes, no fragment). The gate import stays
    lazy so this module remains stdlib-only at import time; an unavailable
    gate fails closed too. Final serialized-report gates are retained
    downstream and re-verify whatever this publishes.
    """
    cleaned = redact(str(text))
    try:
        from omega_prime.grokbot.secret_guard import assert_no_secrets

        assert_no_secrets(cleaned, "readiness-detail")
    except Exception:
        return _CURATE_WITHHELD
    flat = " ".join(cleaned.split())
    if len(flat) > limit:
        return flat[: limit - 3] + "..."
    return flat


def account_roster(
    *,
    canonical: list[str],
    offered: list[str],
    evidence: Mapping[str, list[str]],
    disabled: Mapping[str, str],
    blocked: Mapping[str, tuple[list[str], list[str]]],
    family_of: Callable[[str], str | None],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Account every canonical name exactly once. Pure; no I/O.

    Returns ``(rows, problems)``. ``problems`` is empty only when every
    offered-or-disabled name reconciles and no offered name is extra.
    ``blocked`` maps a tool to ``(evidence, prerequisites)`` aggregated from
    named prerequisite limits; blocked rows preserve those prerequisites
    while unexercised eligible tools without them stay failed.
    """
    offered_set = set(offered)
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for name in canonical:
        family = family_of(name) or "unmapped"
        if family == "unmapped":
            problems.append(f"unmapped-family:{name}")
        tool_evidence = list(evidence.get(name, ()))
        if tool_evidence:
            rows.append(
                {
                    "name": name,
                    "family": family,
                    "status": "exercised",
                    "evidence": tool_evidence,
                    "prerequisites": [],
                }
            )
        elif name in disabled:
            rows.append(
                {
                    "name": name,
                    "family": family,
                    "status": "feature_disabled",
                    "evidence": [disabled[name]],
                    "prerequisites": [],
                }
            )
        elif name in blocked:
            block_evidence, block_prereqs = blocked[name]
            rows.append(
                {
                    "name": name,
                    "family": family,
                    "status": "blocked",
                    "evidence": list(block_evidence),
                    "prerequisites": list(block_prereqs),
                }
            )
        elif name in offered_set:
            problems.append(f"offered-not-exercised:{name}")
            rows.append(
                {
                    "name": name,
                    "family": family,
                    "status": "failed",
                    "evidence": ["offered but never exercised"],
                    "prerequisites": [],
                }
            )
        else:
            problems.append(f"absent-from-offered:{name}")
            rows.append(
                {
                    "name": name,
                    "family": family,
                    "status": "failed",
                    "evidence": ["absent from offered tools"],
                    "prerequisites": [],
                }
            )
    for name in sorted(offered_set - set(canonical)):
        problems.append(f"offered-not-rostered:{name}")
    return rows, problems


TARGETED_LOCAL_APPROVALS: tuple[tuple[str, str], ...] = (
    ("sys_cache", "readiness-local"),
    ("sys_contract_propose", "readiness-local"),
    ("qua_receipt_approve", "readiness-local"),
    ("qua_waiver_record", "readiness-local"),
    ("lead_doctor", "readiness-local"),
    ("lead_intake_next", "readiness-local"),
    ("lead_intake_ack", "readiness-local"),
    ("lead_memory_retain", "readiness-local"),
    ("lead_event_emit", "readiness-local"),
    ("harness_upsert", "readiness-local"),
    ("harness_delete", "readiness-local"),
    ("harness_refine", "readiness-local"),
    ("harness_rollback", "readiness-local"),
    ("goal_set", "readiness-local"),
    ("goal_pause", "readiness-local"),
    ("goal_resume", "readiness-local"),
    ("goal_clear", "readiness-local"),
    ("heartbeat_set", "readiness-local"),
    ("heartbeat_clear", "readiness-local"),
    ("autonomous_start", "readiness-local"),
    ("autonomous_stop", "readiness-local"),
    ("agent_message_send", "readiness-local"),
    ("prime_cell", "readiness-local"),
    ("prime_bash", "readiness-local"),
    ("prime_goal", "readiness-local"),
    ("prime_autonomous", "readiness-local"),
    ("prime_factory_run", "readiness-local"),
    ("prime_factory_stop", "readiness-local"),
    ("prime_factory_resume", "readiness-local"),
    ("cron_job_create", "readiness-local"),
    ("cron_job_remove", "readiness-local"),
    ("autolearn_turn", "readiness-local"),
    ("rlm_spawn", "readiness-local"),
    ("rlm_create_session", "readiness-local"),
    ("rlm_delete_subagent", "readiness-local"),
    ("rlm_rename", "readiness-local"),
    ("omega_dispatch", "readiness-local"),
    ("omega_jobs_cancel", "readiness-local"),
)
"""Genuine targeted local approvals for the probe emulators.

Every entry is an approval-gated local operation actually used by the frozen
workflow/Prime scenarios, verified against the registry ``requires_approval``
sets: in-memory systems/quality decisions, local lead/harness/goal/heartbeat/
autonomous/messaging/cron/learning writes, RLM writes (which name the
unconfigured provider truthfully), local Prime cell/bash/goal/controller
operations per prime/kernel.py local semantics, and fixture-local factory
verbs whose genuine safe limit is the absent run_child in the fresh owned
runtime (a grant producing spurious ok surfaces as failure; approval refusal
would mask the real prerequisite). ``omega_dispatch`` and
``omega_jobs_cancel`` are granted so the probe can see the handler's own
refusal (confirm token unset, fleet coordinator unset). That grant does not
authorize a coordinator call. Never external-service authorization (no
bus/web/vision/greptile/play/vercel/railway/x/telegram/discord/substrate/
provider grants). The full deny/grant/revoke lifecycle stays on the loopback
host's separate approval domain.
"""


class ReadinessProbe:
    """Production probe. Constructed and used only inside the fresh child."""

    def __init__(self, layout: LocalLayout, *, include_prime: bool = False) -> None:
        self.layout = layout
        self.include_prime = include_prime
        self.emulator: Any = None
        self.registry: Any = None
        self.checks: list[dict[str, Any]] = []
        self.exercised: dict[str, list[str]] = {}

    def attach(self, emulator: Any) -> None:
        """Bind the main production emulator and its registry."""
        self.emulator = emulator
        self.registry = emulator.registry

    def _redact(self, text: str) -> str:
        from omega_prime.grokbot.audit import redact_sensitive

        return str(redact_sensitive(str(text)))

    def call(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        """Dispatch through the real emulator; raise on invocation error.

        Returns the unchanged successful ``execute_tool`` envelope
        (``status``/``result``/``elapsed_ms``). Success alone is not evidence;
        callers validate semantic results/effects before ``record``. The
        failure carries the curated invocation reason so availability
        branches (including approval negatives) stay decidable.
        """
        if self.emulator is None:
            raise ReadinessFailure("probe has no emulator")
        outcome = self.emulator.execute_tool(name, dict(arguments))
        if not isinstance(outcome, dict) or outcome.get("status") != "ok":
            reason = ""
            if isinstance(outcome, dict):
                reason = str(outcome.get("error", outcome.get("result", "")))
            raise ReadinessFailure(f"{name} failed: {_curate(reason, self._redact)}")
        return outcome

    def record(self, name: str, tools: tuple[str, ...], detail: str) -> None:
        """Mark passed evidence after the caller validated results/effects."""
        entry = {
            "name": name,
            "status": "passed",
            "tools": list(tools),
            "detail": _curate(detail, self._redact),
        }
        self.checks.append(entry)
        for tool in tools:
            self.exercised.setdefault(tool, []).append(name)

    def blocked(
        self,
        name: str,
        tools: tuple[str, ...],
        prerequisites: tuple[str, ...],
        detail: str,
    ) -> None:
        """Record an explicit prerequisite limit. Never swallows a failure."""
        self.checks.append(
            {
                "name": name,
                "status": "blocked",
                "tools": list(tools),
                "prerequisites": list(prerequisites),
                "detail": _curate(detail, self._redact),
            }
        )

    def check(self, condition: bool, message: str) -> None:
        """Raise a scenario failure when ``condition`` is false."""
        if not condition:
            raise ReadinessFailure(message)


def _failed_check(
    probe: ReadinessProbe, name: str, tools: tuple[str, ...], detail: str
) -> None:
    probe.checks.append(
        {
            "name": name,
            "status": "failed",
            "tools": list(tools),
            "detail": _curate(detail, probe._redact),
        }
    )


def _run_scenario_module(probe: ReadinessProbe, module: str) -> None:
    try:
        imported = importlib.import_module(module)
    except ImportError:
        _failed_check(
            probe,
            f"scenario:{module.split('.')[-1]}",
            (),
            f"scenario module {module} unavailable",
        )
        return
    try:
        imported.run(probe)
    except ReadinessFailure as exc:
        _failed_check(
            probe, f"scenario:{module.split('.')[-1]}", (), f"scenario failed: {exc}"
        )
    except Exception as exc:  # captured as a failed check
        _failed_check(
            probe,
            f"scenario:{module.split('.')[-1]}",
            (),
            f"scenario raised {type(exc).__name__}: {exc}",
        )


def _prime_gated_names() -> dict[str, tuple[str, ...]]:
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


def _redteam_disabled_evidence(
    config: dict[str, Any],
    offered: set[str] | frozenset[str] | list[str] | tuple[str, ...],
) -> dict[str, str]:
    """Disabled evidence for Red Team tools absent from the offered runtime.

    Returns a ``disabled`` entry per canonical Red Team name only when the
    effective fixture config leaves Red Team off AND the name is genuinely
    absent from the current offered runtime. Enabled-but-missing,
    offered-while-disabled, and unrelated absences are left untouched so the
    existing ``account_roster`` accounting still fails them.
    """
    from omega_prime.config import redteam_enabled
    from omega_prime.tools.redteam import REDTEAM_TOOL_NAMES

    if redteam_enabled(config):
        return {}
    offered_set = set(offered)
    return {
        name: "redteam.enabled=false verified absent"
        for name in REDTEAM_TOOL_NAMES
        if name not in offered_set
    }


def _off_profile_checks(probe: ReadinessProbe, emulator: Any) -> None:
    """Prove the OFF runtime genuinely absents and refuses Prime tools."""
    from omega_prime.config import (
        PRIME_FAMILIES,
        load_config,
        prime_enabled,
        redteam_enabled,
    )

    gated = _prime_gated_names()
    config = load_config(probe.layout.install)
    for family in PRIME_FAMILIES:
        probe.check(
            not prime_enabled(config, family),
            f"fixture must disable prime.{family}",
        )
    probe.check(
        not redteam_enabled(config),
        "fixture must disable redteam",
    )
    offered = set(emulator.runtime.tool_names)
    total = sum(len(names) for names in gated.values())
    missing = [n for names in gated.values() for n in names if n not in offered]
    probe.check(
        len(missing) == total,
        f"OFF runtime offers {total - len(missing)} Prime tools",
    )
    refused = 0
    for names in gated.values():
        for tool in names:
            outcome = emulator.execute_tool(tool, {})
            if isinstance(outcome, dict) and outcome.get("status") == "error":
                refused += 1
    probe.check(
        refused == len(missing),
        f"OFF refusals {refused} != absent {len(missing)}",
    )
    probe.record(
        "prime-off:absent-and-refused",
        (),
        f"{len(missing)} Prime tools absent and refused without effects",
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(base_url: str, proc: subprocess.Popen[bytes], timeout: float) -> bool:
    import httpx2

    deadline = time.monotonic() + timeout
    with httpx2.Client(trust_env=False, timeout=2.0) as http:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False
            try:
                response = http.get(f"{base_url}/readyz")
            except Exception:  # poll until ready or timeout
                time.sleep(0.1)
                continue
            if response.status_code == 200:
                try:
                    body = response.json()
                except ValueError:
                    body = None
                if isinstance(body, dict) and body.get("ready") is True:
                    return True
            time.sleep(0.1)
    return False


def _mcp_text_result(result: Any) -> tuple[bool, str]:
    try:
        parts = [part.text for part in result.content if hasattr(part, "text")]
    except AttributeError:
        return True, ""
    return bool(result.is_error), "".join(parts)


def _run_loopback(probe: ReadinessProbe) -> None:
    """Real SSE + Streamable HTTP host: file effects, approval lifecycle, audit."""
    import httpx2

    from omega_prime.grokbot.security import generate_token
    from omega_prime.grokbot.tokens import create_token

    layout = probe.layout
    call_token = generate_token()
    token_file = layout.base / "caller.token"
    fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (call_token + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    store = layout.base / "tokens.json"
    admin_token, _record = create_token(store, scopes=["admin"], label="readiness")
    audit_log = layout.audit / "loopback.jsonl"
    port = _free_port()
    cmd = [
        sys.executable,
        "-m",
        "omega_prime.mcp_server",
        "--transport",
        "sse",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--root",
        str(layout.install),
        "--home",
        str(layout.home),
        "--token-file",
        str(token_file),
        "--token-store",
        str(store),
        "--audit-log",
        str(audit_log),
        "--shutdown-grace",
        "5",
    ]
    host_log = layout.base / "host.log"
    proc: subprocess.Popen[bytes] | None = None
    try:
        with host_log.open("wb") as handle:
            proc = subprocess.Popen(
                cmd,
                cwd=str(layout.work),
                env=dict(layout.env),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
            )
        base_url = f"http://127.0.0.1:{port}"
        probe.check(
            _wait_ready(base_url, proc, 25.0),
            "loopback host never became ready",
        )
        with httpx2.Client(trust_env=False, timeout=10.0) as http:
            response = http.get(
                f"{base_url}/manifest.json",
                headers={"Authorization": f"Bearer {call_token}"},
            )
            probe.check(response.status_code == 200, "manifest unreachable")
            manifest = response.json()
            served = manifest.get("mcp_server", {})
            offered = list(probe.emulator.runtime.tool_names)
            probe.check(
                sorted(served.get("tools", [])) == sorted(offered),
                "served manifest tools disagree with the runtime",
            )
            probe.check(
                served.get("rostered_tool_count", -1) == len(offered),
                "served roster count disagrees with the runtime",
            )
            probe.check(bool(manifest.get("digest")), "served manifest digest missing")
            gated = [n for n in offered if probe.emulator.registry.approval_required(n)]
            probe.check(
                sorted(served.get("approval_required", [])) == sorted(gated),
                "served approval set disagrees with the runtime",
            )
            probe.record(
                "loopback:manifest-agrees",
                (),
                f"{len(offered)} served tools match the runtime manifest",
            )
            for transport in ("sse", "http"):
                _loopback_transport(
                    probe, http, base_url, transport, call_token, admin_token, port
                )
        try:
            text = audit_log.read_bytes()
        except OSError:
            text = b""
        probe.check(bool(text), "loopback audit log is empty")
        for secret in (call_token, admin_token):
            probe.check(
                secret.encode("utf-8") not in text,
                "loopback audit log contains credential bytes",
            )
        probe.record(
            "loopback:audit-sanitized",
            (),
            "audit log present with no credential bytes",
        )
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                code = proc.wait(timeout=5)
            probe.check(code == 0, f"loopback host shutdown code {code}")
            probe.record("loopback:clean-shutdown", (), "host exited 0 after SIGTERM")


def _loopback_transport(
    probe: ReadinessProbe,
    http: Any,
    base_url: str,
    transport: str,
    call_token: str,
    admin_token: str,
    port: int,
) -> None:
    import asyncio

    import httpx2
    from mcp.client import Client
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client

    key = f"readiness-loopback-{transport}"
    file_name = f"readiness-loopback-{transport}.txt"
    payload = f"loopback-{transport}-bytes"

    async def _session_body() -> None:
        headers = {"Authorization": f"Bearer {call_token}"}
        raw: Any = None
        if transport == "sse":
            session = sse_client(
                f"{base_url}/sse", headers=headers, timeout=15.0, sse_read_timeout=15.0
            )
        else:
            raw = httpx2.AsyncClient(headers=headers, trust_env=False, timeout=15.0)
            session = streamable_http_client(f"{base_url}/mcp", http_client=raw)
        try:
            async with Client(session, mode="legacy") as client:
                is_error, text = _mcp_text_result(
                    await client.call_tool(
                        "write_file", {"path": file_name, "content": payload}
                    )
                )
                probe.check(not is_error, f"{transport} write_file failed: {text}")
                is_error, text = _mcp_text_result(
                    await client.call_tool("read_file", {"path": file_name})
                )
                probe.check(not is_error, f"{transport} read_file failed")
                try:
                    body = json.loads(text)
                    content = body.get("content", text)
                except ValueError:
                    content = text
                probe.check(
                    payload in str(content),
                    f"{transport} file roundtrip bytes differ",
                )
                landed = probe.layout.work / file_name
                probe.check(
                    landed.is_file() and payload in landed.read_text(encoding="utf-8"),
                    f"{transport} file effect did not land in the owned work root",
                )
                probe.record(
                    f"loopback:{transport}:file-roundtrip",
                    ("write_file", "read_file"),
                    f"{transport} write/read exact bytes",
                )

                async def _cache(action: str, value: Any = None) -> tuple[bool, str]:
                    args: dict[str, Any] = {"action": action, "key": key}
                    if value is not None:
                        args["value"] = value
                    is_error, text = _mcp_text_result(
                        await client.call_tool("sys_cache", args)
                    )
                    return is_error, text

                denied, denied_text = await _cache("set", "v1")
                probe.check(
                    denied and "approval" in denied_text.lower(),
                    f"{transport} sys_cache set was not refused before grant",
                )

                granted = http.post(
                    f"{base_url}/admin/approvals",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={"tool": "sys_cache", "ttl_seconds": 60},
                )
                probe.check(granted.status_code == 201, "sys_cache grant failed")
                probe.check(
                    call_token not in granted.text and admin_token not in granted.text,
                    "approval response contains credential bytes",
                )
                is_error, text = await _cache("get")
                probe.check(not is_error, f"{transport} post-grant get failed")
                probe.check(
                    "v1" not in text,
                    f"{transport} denied set mutated the cache",
                )
                is_error, text = await _cache("set", "v1")
                probe.check(not is_error, f"{transport} granted set failed")
                is_error, text = await _cache("get")
                probe.check(
                    not is_error and "v1" in text,
                    f"{transport} granted get returned no value",
                )
                revoked = http.delete(
                    f"{base_url}/admin/approvals/sys_cache",
                    headers={"Authorization": f"Bearer {admin_token}"},
                )
                probe.check(revoked.status_code == 200, "sys_cache revoke failed")
                denied, denied_text = await _cache("set", "v2")
                probe.check(
                    denied and "approval" in denied_text.lower(),
                    f"{transport} sys_cache set was not refused after revoke",
                )
                granted = http.post(
                    f"{base_url}/admin/approvals",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={"tool": "sys_cache", "ttl_seconds": 60},
                )
                probe.check(granted.status_code == 201, "sys_cache regrant failed")
                is_error, text = await _cache("get")
                probe.check(
                    not is_error and "v1" in text and "v2" not in text,
                    f"{transport} revoked set mutated the cache",
                )
                revoked = http.delete(
                    f"{base_url}/admin/approvals/sys_cache",
                    headers={"Authorization": f"Bearer {admin_token}"},
                )
                probe.check(revoked.status_code == 200, "sys_cache final revoke failed")
                probe.record(
                    f"loopback:{transport}:approval-lifecycle",
                    ("sys_cache",),
                    f"{transport} deny/grant/set/get/revoke/deny/regrant-read/revoke",
                )
        finally:
            if raw is not None:
                await raw.aclose()

    asyncio.run(_session_body())


def _family_map() -> dict[str, str]:
    from omega_prime.tooling.catalog import FAMILIES

    mapping: dict[str, str] = {}
    for family, names in FAMILIES:
        for name in names:
            mapping.setdefault(name, family)
    return mapping


def child_main() -> int:
    """Run the whole profile inside the fresh product child. Stdout: report only."""
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            text = _run_profile()
    except Exception as exc:  # fixed safe line only, never raw
        try:
            from omega_prime.grokbot.audit import redact_sensitive

            detail = redact_sensitive(f"{type(exc).__name__}: {exc}")
        except ImportError:
            detail = type(exc).__name__
        try:
            from omega_prime.grokbot.secret_guard import assert_no_secrets

            assert_no_secrets(str(detail), "readiness-child-error")
        except Exception:
            print(f"{_CHILD_ERROR_PREFIX}{_CURATE_WITHHELD}", file=sys.stderr)
            return 1
        flat = " ".join(str(detail).split())[:DETAIL_LIMIT]
        print(f"{_CHILD_ERROR_PREFIX}{flat}", file=sys.stderr)
        return 1
    try:
        from omega_prime.grokbot.secret_guard import assert_no_secrets
    except ImportError:
        print(f"{_CHILD_ERROR_PREFIX}secret gate unavailable", file=sys.stderr)
        return 1
    try:
        assert_no_secrets(text, "readiness-report")
    except Exception:  # fixed label, prior bytes preserved
        print(
            "readiness report withheld: secret gate tripped",
            file=sys.stderr,
        )
        return 1
    sys.stdout.write(text + "\n")
    return 0


def _run_profile() -> str:
    import os as _os

    from omega_prime.config import (
        PRIME_FAMILIES,
        load_config,
        prime_enabled,
        redteam_enabled,
    )
    from omega_prime.grokbot.emulator import GrokBotTurnEmulator
    from omega_prime.tooling.roster import roster_names

    install = Path(_os.environ["READINESS_INSTALL"])
    include_prime = _os.environ.get("READINESS_INCLUDE_PRIME") == "1"
    missing = _os.environ.get("READINESS_PRIME_MISSING", "")
    layout = LocalLayout(
        source=Path(_os.environ.get("READINESS_SOURCE", str(install))),
        base=Path(_os.environ["READINESS_BASE"]),
        install=install,
        home=Path(_os.environ["READINESS_HOME"]),
        work=Path(_os.environ["READINESS_WORK"]),
        state=Path(_os.environ["READINESS_STATE"]),
        audit=Path(_os.environ["READINESS_AUDIT"]),
        command_log=Path(_os.environ["READINESS_COMMAND_LOG"]),
        env=dict(_os.environ),
        prime_missing=tuple(sorted(part for part in missing.split(",") if part)),
    )
    canonical = roster_names((install / ROSTER_REL).read_text(encoding="utf-8"))
    env = dict(_os.environ)
    # The disposable profile deliberately opts into loopback so the no-secret
    # local substrate checks exercise the configured path without inheriting
    # caller environment or relying on a production default.
    env["SUBSTRATE_URL"] = "local"
    home = layout.home

    off_fixture = {
        "prime": {name: {"enabled": False} for name in PRIME_FAMILIES},
        "redteam": {"enabled": False},
    }
    (install / FIXTURE_NAME).write_text(
        json.dumps(off_fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    off = GrokBotTurnEmulator(
        root=install, home=home, env=env, approvals=TARGETED_LOCAL_APPROVALS
    )
    offered_off = set(off.runtime.tool_names)
    gated = _prime_gated_names()

    probe = ReadinessProbe(layout, include_prime=include_prime)
    _off_profile_checks(probe, off)

    if include_prime:
        if layout.prime_missing:
            probe.blocked(
                "prime-resources:optional-artifacts-absent",
                (),
                tuple(sorted(layout.prime_missing)),
                "optional native artifacts absent from the export",
            )
        fixture = {
            "prime": {name: {"enabled": True} for name in PRIME_FAMILIES},
            "redteam": {"enabled": False},
        }
        (install / FIXTURE_NAME).write_text(
            json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        config = load_config(install)
        for family in PRIME_FAMILIES:
            probe.check(
                prime_enabled(config, family),
                f"fixture must enable prime.{family}",
            )
        probe.check(
            not redteam_enabled(config),
            "fixture must keep redteam disabled",
        )
        main = GrokBotTurnEmulator(
            root=install, home=home, env=env, approvals=TARGETED_LOCAL_APPROVALS
        )
        offered_on = set(main.runtime.tool_names)
        for names in gated.values():
            for tool in names:
                probe.check(
                    tool in offered_on,
                    f"enabled Prime tool {tool} absent from offered names",
                )
        probe.record(
            "prime-on:enabled-and-offered",
            (),
            "all seven Prime families enabled and offered",
        )
    else:
        main = off

    probe.attach(main)
    _run_scenario_module(probe, "omega_prime.grokbot.readiness_local")
    _run_scenario_module(probe, "omega_prime.grokbot.readiness_workflows")
    if include_prime:
        _run_scenario_module(probe, "omega_prime.grokbot.readiness_prime")

    try:
        _run_loopback(probe)
    except ReadinessFailure as exc:
        _failed_check(probe, "loopback", ("sys_cache",), f"loopback failed: {exc}")
    except Exception as exc:  # captured as a failed check
        _failed_check(
            probe,
            "loopback",
            ("sys_cache",),
            f"loopback raised {type(exc).__name__}: {exc}",
        )

    families = _family_map()
    evidence = {
        tool: [f"check:{name}" for name in checks]
        for tool, checks in probe.exercised.items()
    }
    disabled: dict[str, str] = {}
    config = load_config(install)
    if not include_prime:
        for family, names in gated.items():
            if prime_enabled(config, family):
                continue
            for tool in names:
                if tool in offered_off:
                    continue
                disabled[tool] = f"prime.{family}.enabled=false verified absent"
    disabled.update(_redteam_disabled_evidence(config, set(main.runtime.tool_names)))
    blocked: dict[str, tuple[list[str], list[str]]] = {}
    for check in probe.checks:
        if check.get("status") != "blocked":
            continue
        for tool in check.get("tools", []):
            entry = blocked.setdefault(tool, ([], []))
            entry[0].append(f"check:{check.get('name', '')}")
            for prereq in check.get("prerequisites", []):
                if prereq not in entry[1]:
                    entry[1].append(prereq)
    rows, problems = account_roster(
        canonical=canonical,
        offered=list(main.runtime.tool_names),
        evidence=evidence,
        disabled=disabled,
        blocked=blocked,
        family_of=families.get,
    )
    failed_checks = [c for c in probe.checks if c["status"] == "failed"]
    failed_tools = [r for r in rows if r["status"] == "failed"]
    ok = not failed_checks and not failed_tools and not problems
    counts = {"total": len(rows)}
    for status in ("exercised", "feature_disabled", "blocked", "failed"):
        counts[status] = sum(1 for r in rows if r["status"] == status)
    report = {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "scope": SCOPE,
        "profile": "include-prime" if include_prime else "local-only",
        "checks": probe.checks,
        "tools": rows,
        "summary": {
            **counts,
            "offered": len(main.runtime.tool_names),
            "roster": len(canonical),
            "problems": problems,
        },
        "limitations": [
            "no live provider, credential, device, or browser integration",
            "unconfigured services stay blocked with named prerequisites, never success",
            "sys_cache is in-memory; approval state is not durable",
            "model doubles prove local composition only",
        ],
    }
    return json.dumps(report, sort_keys=True, ensure_ascii=False)


__all__ = [
    "DETAIL_LIMIT",
    "FIXTURE_NAME",
    "ROSTER_REL",
    "SCHEMA_VERSION",
    "SCOPE",
    "TARGETED_LOCAL_APPROVALS",
    "IsolationError",
    "LocalLayout",
    "ReadinessFailure",
    "ReadinessProbe",
    "_curate",
    "account_roster",
    "build_child_env",
    "child_main",
    "isolated_layout",
]
