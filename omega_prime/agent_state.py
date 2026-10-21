# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""The agent's own writable data folder and its one-time migration.

Omega Prime keeps two kinds of files under its state root
(``$OMEGA_PRIME_STATE_DIR``, default ``~/.omega-prime``):

* operator files the agent must never write: ``settings.json``,
  ``secrets/``, ``dashboard/``, the ``ui-control`` queue, the
  ``mcp-attachments`` journal and per-run ``jobs/``;
* the agent's own data: memory, session history, desk intake, roster,
  events, turn journal, RLM sessions and state-side skills.

The agent's data lives in one subfolder, ``<state>/agent`` (or
``$OMEGA_PRIME_AGENT_STATE_DIR`` when set). The host MCP server and the
sandboxed MCP child both open their stores there, and the sandbox binds
only that folder writable. :func:`migrate_agent_state` moves data written
by older releases (directly under the state root, under ``mcp-state``, or
inside the install tree) into the subfolder. It never overwrites a file,
so a second run is a no-op and a clash leaves both copies in place.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

AGENT_STATE_ENV = "OMEGA_PRIME_AGENT_STATE_DIR"
STATE_DIR_ENV = "OMEGA_PRIME_STATE_DIR"
AGENT_SUBDIR = "agent"
#: Short-lived per-child folder from an earlier draft of the engine bridge.
LEGACY_MCP_STATE = "mcp-state"
MIGRATION_RECEIPT = "MIGRATION.json"

MEMORY_FILES = ("MEMORY.md", "USER.md")
SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
#: Desk stores the lead pack and the delegate shim open.
DESK_STORES = ("turns.sqlite", "packs.json", "events.ndjson", "intake.json", "rlm")
#: Every top-level name the agent writes under its data folder.
AGENT_ITEMS = ("memory", "sessions.db", *DESK_STORES, "skills")
#: Operator files under the state root. Never inside the agent folder, never
#: bound writable, and hidden from the engine sandbox.
PRIVATE_STATE_NAMES = ("settings.json", "secrets", "dashboard")


def _sqlite_family(name: str) -> tuple[str, ...]:
    if name.endswith((".db", ".sqlite")):
        return (name, *(name + suffix for suffix in SQLITE_SIDECARS))
    return (name,)


def _absolute(raw: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(Path(raw).expanduser()))


def agent_state_dir(
    env: Mapping[str, str],
    *,
    home: str | os.PathLike[str] | None = None,
) -> Path | None:
    """The agent data folder, absolute, or None for the legacy layout.

    ``OMEGA_PRIME_AGENT_STATE_DIR`` wins. Otherwise ``OMEGA_PRIME_STATE_DIR``
    gives ``<state>/agent``. Otherwise ``home`` (a live host) gives
    ``<home>/.omega-prime/agent``. With none of them the caller keeps its
    legacy in-tree paths, so a bare unit-test registry never touches HOME.
    """
    explicit = str(env.get(AGENT_STATE_ENV, "") or "").strip()
    if explicit:
        return _absolute(explicit)
    state = str(env.get(STATE_DIR_ENV, "") or "").strip()
    if state:
        return _absolute(Path(state).expanduser() / AGENT_SUBDIR)
    if home is not None:
        return _absolute(Path(home).expanduser() / ".omega-prime" / AGENT_SUBDIR)
    return None


def state_root_for(
    env: Mapping[str, str],
    *,
    home: str | os.PathLike[str] | None = None,
) -> Path | None:
    """The state root whose top level may hold pre-subfolder agent data."""
    state = str(env.get(STATE_DIR_ENV, "") or "").strip()
    if state:
        return _absolute(state)
    if home is not None:
        return _absolute(Path(home).expanduser() / ".omega-prime")
    return None


def private_state_paths(roots: Iterable[Path]) -> list[Path]:
    """Existing operator files under each state root, for the sandbox to hide."""
    seen: list[Path] = []
    for root in roots:
        for name in PRIVATE_STATE_NAMES:
            path = _absolute(Path(root) / name)
            if path not in seen and os.path.lexists(path):
                seen.append(path)
    return seen


def _parent_writable(path: Path) -> bool:
    return os.access(path.parent, os.W_OK | os.X_OK)


def agent_path(
    agent_dir: Path,
    name: str,
    legacy: Iterable[Path] = (),
) -> Path:
    """``agent_dir/name``, or a writable legacy copy it has not replaced yet.

    This is the compatibility read: when a migration could not move an old
    store (for example a clash or a transient error), the server keeps using
    the old file instead of starting an empty one. A legacy path is only
    used while the agent copy is missing and the legacy folder is writable.
    """
    target = agent_dir / name
    if os.path.lexists(target):
        return target
    for old in legacy:
        old = Path(old)
        if (
            old != target
            and old.exists()
            and not old.is_symlink()
            and _parent_writable(old)
        ):
            return old
    return target


def agent_memory_dir(agent_dir: Path, legacy: Mapping[str, Sequence[Path]]) -> Path:
    """``agent_dir/memory``, or a writable legacy memory folder not yet moved."""
    target = agent_dir / "memory"
    if any((target / name).exists() for name in MEMORY_FILES):
        return target
    candidates: list[Path] = []
    for old in legacy.get("memory", ()):
        candidates.append(Path(old))
    for name in MEMORY_FILES:
        for old in legacy.get(f"memory/{name}", ()):
            candidates.append(Path(old).parent)
    for folder in candidates:
        if folder == target or folder.is_symlink():
            continue
        if any((folder / name).is_file() for name in MEMORY_FILES) and os.access(
            folder, os.W_OK | os.X_OK
        ):
            return folder
    return target


def legacy_sources(
    *,
    state_root: Path | None,
    install_root: Path | None = None,
    work_root: Path | None = None,
    engine_state_root: Path | None = None,
) -> dict[str, list[Path]]:
    """Older locations for each agent item, most authoritative first.

    ``state_root`` is a configured ``OMEGA_PRIME_STATE_DIR``: older releases
    wrote the agent's data directly under it. Without one they wrote it in
    the install tree (``install_root``/``work_root``), and the engine's
    default state root (``engine_state_root``, ``~/.omega-prime``) only ever
    held the short-lived ``mcp-state`` child.
    """
    sources: dict[str, list[Path]] = {}

    def add(name: str, path: Path) -> None:
        bucket = sources.setdefault(name, [])
        if path not in bucket:
            bucket.append(path)

    roots: list[Path] = []
    if state_root is not None:
        roots.extend([state_root, state_root / LEGACY_MCP_STATE])
    if engine_state_root is not None:
        roots.append(engine_state_root / LEGACY_MCP_STATE)
    for root in roots:
        for item in AGENT_ITEMS:
            for name in _sqlite_family(item):
                add(name, root / name)
    if install_root is not None:
        tree = Path(install_root) / "omega_prime"
        # The install `memory` folder is the Python package; only the two
        # store files are agent data.
        for name in MEMORY_FILES:
            add(f"memory/{name}", tree / "memory" / name)
        for name in _sqlite_family("sessions.db"):
            add(name, tree / name)
    for base in {p for p in (install_root, work_root) if p is not None}:
        tree = Path(base) / "omega_prime" / "state"
        for item in DESK_STORES:
            for name in _sqlite_family(item):
                add(name, tree / name)
    return sources


class LegacyLayout(TypedDict):
    state_root: Path | None
    install_root: Path | None
    work_root: Path | None
    engine_state_root: Path | None


def legacy_layout(
    env: Mapping[str, str],
    *,
    home: str | os.PathLike[str] | None,
    install_root: Path | None,
    work_root: Path | None,
) -> LegacyLayout:
    """Keyword arguments for :func:`legacy_sources` / :func:`migrate_agent_state`.

    A configured ``OMEGA_PRIME_STATE_DIR`` was the old data root; the install
    tree is then never touched. Otherwise the old data sat in the install
    tree, and only the engine's ``~/.omega-prime/mcp-state`` is also folded in.
    """
    state = str(env.get(STATE_DIR_ENV, "") or "").strip()
    if state:
        return {
            "state_root": _absolute(state),
            "install_root": None,
            "work_root": None,
            "engine_state_root": None,
        }
    return {
        "state_root": None,
        "install_root": install_root,
        "work_root": work_root,
        "engine_state_root": state_root_for(env, home=home),
    }


@dataclass
class MigrationReport:
    agent_dir: str
    dry_run: bool = False
    moved: list[dict[str, str]] = field(default_factory=list)
    kept: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "agent_dir": self.agent_dir,
            "dry_run": self.dry_run,
            "moved": list(self.moved),
            "kept": list(self.kept),
            "errors": list(self.errors),
        }


def _move_one(src: Path, dst: Path, report: MigrationReport) -> None:
    """Move ``src`` to ``dst`` without overwriting anything."""
    if src == dst or not os.path.lexists(src):
        return
    if src.is_symlink():
        report.kept.append({"from": str(src), "reason": "symlink"})
        return
    if os.path.lexists(dst):
        if src.is_dir() and dst.is_dir() and not dst.is_symlink():
            for child in sorted(src.iterdir()):
                _move_one(child, dst / child.name, report)
            if not report.dry_run:
                with contextlib.suppress(OSError):
                    src.rmdir()
            return
        report.kept.append(
            {"from": str(src), "to": str(dst), "reason": "already_exists"}
        )
        return
    if report.dry_run:
        report.moved.append({"from": str(src), "to": str(dst)})
        return
    try:
        dst.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.rename(src, dst)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            # Another filesystem: copy, check, then remove the original.
            if src.is_dir():
                shutil.copytree(src, dst, symlinks=True)
                shutil.rmtree(src)
            else:
                shutil.copy2(src, dst)
                if os.path.getsize(dst) != os.path.getsize(src):
                    os.unlink(dst)
                    raise OSError(errno.EIO, "copy size mismatch") from exc
                os.unlink(src)
    except OSError as exc:
        report.errors.append(
            {
                "from": str(src),
                "to": str(dst),
                "error": exc.strerror or type(exc).__name__,
            }
        )
        return
    report.moved.append({"from": str(src), "to": str(dst)})


def migrate_agent_state(
    agent_dir: Path,
    *,
    state_root: Path | None,
    install_root: Path | None = None,
    work_root: Path | None = None,
    engine_state_root: Path | None = None,
    dry_run: bool = False,
) -> MigrationReport:
    """Move pre-subfolder agent data into ``agent_dir``. Idempotent.

    Only the names in :data:`AGENT_ITEMS` (plus SQLite sidecars and the two
    install-tree memory files) are touched; ``settings.json``, ``secrets/``,
    ``dashboard/``, ``jobs/``, ``ui-control/`` and ``mcp-attachments/`` are
    never read or moved. An existing destination is never replaced. A
    source that cannot be moved stays where it is and is reported.
    """
    agent_dir = _absolute(agent_dir)
    report = MigrationReport(agent_dir=str(agent_dir), dry_run=dry_run)
    sources = legacy_sources(
        state_root=_absolute(state_root) if state_root is not None else None,
        install_root=Path(install_root) if install_root is not None else None,
        work_root=Path(work_root) if work_root is not None else None,
        engine_state_root=(
            _absolute(engine_state_root) if engine_state_root is not None else None
        ),
    )
    private = {
        _absolute(Path(root) / name)
        for root in (state_root, engine_state_root)
        if root is not None
        for name in PRIVATE_STATE_NAMES
    }
    for name, candidates in sources.items():
        target = agent_dir / name
        for src in candidates:
            src = _absolute(src)
            if src in private or src == agent_dir or agent_dir in src.parents:
                continue
            _move_one(src, target, report)
    if not dry_run and report.moved:
        try:
            agent_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            receipt = agent_dir / MIGRATION_RECEIPT
            history: list[object] = []
            if receipt.is_file():
                try:
                    loaded = json.loads(receipt.read_text(encoding="utf-8"))
                    history = list(loaded) if isinstance(loaded, list) else []
                except (OSError, ValueError):
                    history = []
            history.append({"moved": report.moved, "kept": report.kept})
            receipt.write_text(json.dumps(history, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def ensure_agent_dir(agent_dir: Path) -> bool:
    """Create the agent folder 0700. False when it cannot be created."""
    try:
        agent_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        return False
    return agent_dir.is_dir() and os.access(agent_dir, os.W_OK | os.X_OK)


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m omega_prime.agent_state [--dry-run] [--root R]``.

    Prints only paths and counts, never file contents.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="omega-prime-agent-state")
    parser.add_argument("--root", type=Path, default=None, help="install root")
    parser.add_argument("--work-root", type=Path, default=None)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    agent = agent_state_dir(env, home=args.home)
    assert agent is not None
    root = args.root.resolve() if args.root is not None else None
    work = args.work_root.resolve() if args.work_root is not None else root
    report = migrate_agent_state(
        agent,
        **legacy_layout(env, home=args.home, install_root=root, work_root=work),
        dry_run=args.dry_run,
    )
    print(json.dumps(report.as_dict(), indent=2))
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
