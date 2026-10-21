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
from collections.abc import Iterable, Iterator, Mapping, Sequence
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
    candidates: list[Path] = []
    for old in legacy.get("memory", ()):
        candidates.append(Path(old))
    for name in MEMORY_FILES:
        for old in legacy.get(f"memory/{name}", ()):
            candidates.append(Path(old).parent)
    usable = [
        folder
        for folder in candidates
        if folder != target
        and not folder.is_symlink()
        and any((folder / name).is_file() for name in MEMORY_FILES)
        and os.access(folder, os.W_OK | os.X_OK)
    ]
    here = {name for name in MEMORY_FILES if (target / name).exists()}
    # The agent folder wins only when it holds every memory file an old
    # folder still has. After a partial move (one file kept back) the old
    # folder that still holds the missing file is read instead, so neither
    # the memory nor the profile drops out of view.
    missing = {
        name
        for name in MEMORY_FILES
        if name not in here and any((f / name).is_file() for f in usable)
    }
    if here and not missing:
        return target
    best: Path | None = None
    best_count = len(here)
    for folder in usable:
        count = sum((folder / name).is_file() for name in MEMORY_FILES)
        if count > best_count:
            best, best_count = folder, count
    return best if best is not None else target


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


#: ``os.link`` errors that mean "no hard link here": copy and publish instead.
_NO_LINK = {
    errno.EXDEV,
    errno.EPERM,
    errno.EMLINK,
    getattr(errno, "ENOTSUP", errno.EPERM),
    getattr(errno, "EOPNOTSUPP", errno.EPERM),
}
#: ``os.rename`` errors that mean the directory destination already holds data.
_DIR_TAKEN = {errno.EEXIST, errno.ENOTEMPTY, errno.ENOTDIR, errno.EISDIR}


def _scratch(dst: Path) -> Path:
    return dst.parent / f".{dst.name}.migrating-{os.getpid()}"


class SourceChangedError(OSError):
    """The source file was replaced by a writer while it was being moved."""


def _identity(path: Path) -> tuple[int, int, int, int]:
    st = os.lstat(path)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


@contextlib.contextmanager
def _writer_lock(src: Path, *, create: bool = False) -> Iterator[None]:
    """Hold the store's own ``<name>.lock`` flock while ``src`` moves.

    ``IntakeStore`` (and the other JSON stores) take an exclusive ``flock``
    on that stable sidecar for every save, so a pass that is still writing
    the old file waits until the move is complete instead of replacing the
    source between the publish and the unlink. A store without a lock file
    is moved unlocked; the identity check in :func:`_publish_file` still
    refuses to delete a source that changed. With ``create`` the sidecar is
    created when missing (a folder move: the skill manager takes the same
    ``<folder>.lock`` for every save, so no save lands mid-move).
    """
    lock = src.with_name(src.name + ".lock")
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        yield
        return
    try:
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        if create:
            flags |= os.O_CREAT
        fd = os.open(lock, flags, 0o600)
    except OSError:
        yield
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _publish_file(src: Path, dst: Path) -> bool:
    """Move ``src`` to a missing ``dst`` under the store's writer lock."""
    with _writer_lock(src):
        return _publish_file_locked(src, dst)


def _publish_file_locked(src: Path, dst: Path) -> bool:
    """Move one file to ``dst`` only if ``dst`` does not exist. Atomic.

    ``os.link`` fails with EEXIST instead of replacing, so a record another
    process creates at ``dst`` meanwhile is never overwritten. Without a hard
    link (another filesystem), the file is copied to a scratch name beside
    ``dst``, flushed, and published with the same no-replace link; a failed
    copy is removed and the source stays. False when ``dst`` already exists.

    The source is unlinked only while it is still the file that was
    published. A writer that replaced it meanwhile (atomic save) keeps its
    newer file: the published copy is withdrawn and
    :class:`SourceChangedError` is raised, so the source stays the one
    readable copy and the next start retries.
    """
    dst.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    before = _identity(src)
    try:
        os.link(src, dst, follow_symlinks=False)
    except FileExistsError:
        return False
    except OSError as exc:
        if exc.errno not in _NO_LINK:
            raise
        scratch = _scratch(dst)
        try:
            with open(src, "rb") as reader, open(scratch, "xb") as writer:
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())
            shutil.copystat(src, scratch)
            if os.path.getsize(scratch) != os.path.getsize(src):
                raise OSError(errno.EIO, "copy size mismatch") from exc
            try:
                os.link(scratch, dst)
            except FileExistsError:
                return False
        finally:
            with contextlib.suppress(OSError):
                os.unlink(scratch)
    try:
        changed = _identity(src) != before
    except FileNotFoundError:
        changed = True
    if changed:
        published = os.lstat(dst)
        if published.st_size == before[2] and (
            (published.st_dev, published.st_ino) == before[:2]
            or published.st_mtime_ns == before[3]
        ):
            with contextlib.suppress(OSError):
                os.unlink(dst)
        raise SourceChangedError(errno.EAGAIN, "source changed during move")
    os.unlink(src)
    return True


#: Left beside a moved folder: names where it went, for a writer that
#: resolved the old path before the move (see ``skills_runtime.manager``).
MOVED_SUFFIX = ".moved"


def _publish_dir(src: Path, dst: Path) -> bool:
    """Move a directory to a missing ``dst``. False when ``dst`` holds data.

    ``rename`` never replaces a non-empty directory or a file. Across
    filesystems the tree is copied to a scratch name first and renamed into
    place only once complete; a failed copy is removed and the source stays.
    """
    dst.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.rename(src, dst)
        return True
    except OSError as exc:
        if exc.errno in _DIR_TAKEN:
            return False
        if exc.errno != errno.EXDEV:
            raise
    scratch = _scratch(dst)
    before = _tree_identity(src)
    try:
        shutil.copytree(src, scratch, symlinks=True)
        if _tree_identity(src) != before:
            # A writer (a nightly pass saving a skill) changed the folder
            # while it was copied: keep the source whole and retry later.
            raise SourceChangedError(errno.EAGAIN, "source changed during move")
        try:
            os.rename(scratch, dst)
        except OSError as exc:
            if exc.errno in _DIR_TAKEN:
                return False
            raise
    finally:
        if os.path.lexists(scratch):
            shutil.rmtree(scratch, ignore_errors=True)
    _remove_copied(src, before)
    return True


def _tree_identity(root: Path) -> dict[str, tuple[int, int, int, int]]:
    """Relative path -> identity for every entry under ``root`` (no follow)."""
    out: dict[str, tuple[int, int, int, int]] = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            path = Path(base) / name
            st = os.lstat(path)
            ident = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
            if os.path.isdir(path) and not os.path.islink(path):
                ident = (st.st_dev, st.st_ino, 0, 0)
            out[str(path.relative_to(root))] = ident
    return out


def _remove_copied(root: Path, copied: Mapping[str, tuple[int, int, int, int]]) -> None:
    """Delete only the source entries that are unchanged since the copy.

    A file a writer created or replaced after the copy stays, with its
    folders, so the next start merges it into the agent folder instead of
    losing it.
    """
    for base, dirs, files in os.walk(root, topdown=False, followlinks=False):
        for name in files:
            path = Path(base) / name
            rel = str(path.relative_to(root))
            with contextlib.suppress(OSError):
                if _identity(path) == copied.get(rel):
                    os.unlink(path)
        for name in dirs:
            path = Path(base) / name
            with contextlib.suppress(OSError):
                if path.is_symlink():
                    if _identity(path) == copied.get(str(path.relative_to(root))):
                        os.unlink(path)
                else:
                    path.rmdir()
    with contextlib.suppress(OSError):
        root.rmdir()


def _move_one(src: Path, dst: Path, report: MigrationReport) -> None:
    """Move ``src`` to ``dst`` without overwriting anything."""
    if src == dst or not os.path.lexists(src):
        return
    if src.is_symlink():
        report.kept.append({"from": str(src), "reason": "symlink"})
        return
    if os.path.lexists(dst):
        if src.is_dir() and dst.is_dir() and not dst.is_symlink():
            _merge_dir(src, dst, report)
            return
        report.kept.append(
            {"from": str(src), "to": str(dst), "reason": "already_exists"}
        )
        return
    if report.dry_run:
        report.moved.append({"from": str(src), "to": str(dst)})
        return
    try:
        publish = _publish_dir if src.is_dir() else _publish_file
        published = publish(src, dst)
    except OSError as exc:
        _error(report, src, dst, exc)
        return
    if not published:
        # Created by someone else since the check: merge or keep both.
        if src.is_dir() and dst.is_dir() and not dst.is_symlink():
            _merge_dir(src, dst, report)
        else:
            report.kept.append(
                {"from": str(src), "to": str(dst), "reason": "already_exists"}
            )
        return
    report.moved.append({"from": str(src), "to": str(dst)})


def _move_root(src: Path, dst: Path, report: MigrationReport) -> None:
    """Move one top-level agent folder under its writer lock, then point at it.

    The lock (``<folder>.lock``) and the pointer (``<folder>.moved``) sit
    beside the folder, never inside a folder being merged, and only the
    whole root gets them. The skill manager takes the same lock for every
    save and follows the pointer whenever it names an existing folder, so a
    nightly pass still holding the old root writes where the host reads,
    even when a clash kept the old root from being removed.
    """
    if report.dry_run or not src.is_dir() or src.is_symlink():
        _move_one(src, dst, report)
        return
    with _writer_lock(src, create=_parent_writable(src)):
        errors = len(report.errors)
        _move_one(src, dst, report)
        if len(report.errors) != errors or not dst.is_dir() or dst.is_symlink():
            return
        with contextlib.suppress(OSError):
            pointer = src.with_name(src.name + MOVED_SUFFIX)
            fd = os.open(
                pointer,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(str(dst))


def _merge_dir(src: Path, dst: Path, report: MigrationReport) -> None:
    for child in sorted(src.iterdir()):
        _move_one(child, dst / child.name, report)
    if not report.dry_run:
        with contextlib.suppress(OSError):
            src.rmdir()


def _error(report: MigrationReport, src: Path, dst: Path, exc: OSError) -> None:
    report.errors.append(
        {"from": str(src), "to": str(dst), "error": exc.strerror or type(exc).__name__}
    )


def _move_group(
    pairs: Sequence[tuple[Path, Path]],
    guard: Sequence[Path],
    report: MigrationReport,
    *,
    keep_clashes: bool = False,
) -> None:
    """Move files that only make sense together, all or none.

    Used for a SQLite database with its ``-wal``/``-shm``/``-journal``
    sidecars and for the two memory files. When any name in ``guard``
    already exists at the destination, or any source is a symlink, every
    source stays where it is, so a database never meets another database's
    journal and a profile never splits from its memory. A failure part way
    moves the published files back. With ``keep_clashes`` a file whose own
    destination exists stays put on its own and the rest still move as one
    unit (the memory files: the agent folder then holds a complete set).
    """
    pairs = [(src, dst) for src, dst in pairs if os.path.lexists(src)]
    if not pairs:
        return
    if any(src.is_symlink() for src, _ in pairs):
        for src, _ in pairs:
            report.kept.append({"from": str(src), "reason": "symlink"})
        return
    if keep_clashes:
        for src, dst in pairs:
            if os.path.lexists(dst):
                report.kept.append(
                    {"from": str(src), "to": str(dst), "reason": "already_exists"}
                )
        pairs = [(src, dst) for src, dst in pairs if not os.path.lexists(dst)]
        guard = [dst for _, dst in pairs]
        if not pairs:
            return
    if any(os.path.lexists(path) for path in guard):
        for src, dst in pairs:
            report.kept.append(
                {"from": str(src), "to": str(dst), "reason": "already_exists"}
            )
        return
    if report.dry_run:
        for src, dst in pairs:
            report.moved.append({"from": str(src), "to": str(dst)})
        return
    done: list[tuple[Path, Path]] = []
    failure: tuple[Path, Path, OSError] | None = None
    for src, dst in pairs:
        try:
            if _publish_file(src, dst):
                done.append((src, dst))
                continue
            failure = (src, dst, FileExistsError(errno.EEXIST, "already exists"))
        except OSError as exc:
            failure = (src, dst, exc)
        break
    if failure is None:
        for src, dst in done:
            report.moved.append({"from": str(src), "to": str(dst)})
        return
    for src, dst in reversed(done):
        try:
            _publish_file(dst, src)
        except OSError as back:
            _error(report, dst, src, back)
    failed_src, failed_dst, cause = failure
    if isinstance(cause, FileExistsError):
        for item_src, item_dst in pairs:
            report.kept.append(
                {"from": str(item_src), "to": str(item_dst), "reason": "already_exists"}
            )
    else:
        _error(report, failed_src, failed_dst, cause)


def _sqlite_group(src: Path, dst: Path) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """A database and its sidecars. Every destination name guards.

    A stray ``-journal`` or ``-wal`` already at the destination would be
    replayed into the moved database, so it blocks the move too.
    """
    pairs = [
        (src.parent / (src.name + suffix), dst.parent / (dst.name + suffix))
        for suffix in ("", *SQLITE_SIDECARS)
    ]
    return pairs, [pair[1] for pair in pairs]


def _memory_group(
    folder: Path, target: Path
) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """The memory files present in ``folder``; only their own names guard.

    Complementary halves from two old folders still combine (one held
    ``MEMORY.md``, the other ``USER.md``). Moved with ``keep_clashes``.
    """
    pairs = [
        (folder / name, target / name)
        for name in MEMORY_FILES
        if os.path.lexists(folder / name)
    ]
    return pairs, [dst for _, dst in pairs]


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

    def allowed(src: Path) -> bool:
        return not (src in private or src == agent_dir or agent_dir in src.parents)

    sidecar_names = {
        item + suffix for item in AGENT_ITEMS for suffix in SQLITE_SIDECARS
    }
    memory_target = agent_dir / "memory"
    memory_folders: set[Path] = set()
    for name, candidates in sources.items():
        if name in sidecar_names:
            continue  # moved with their database
        target = agent_dir / name
        for src in map(_absolute, candidates):
            if not allowed(src):
                continue
            if name.startswith("memory/"):
                # The install tree's two memory files move as one unit.
                if src.parent not in memory_folders:
                    memory_folders.add(src.parent)
                    _move_group(
                        *_memory_group(src.parent, memory_target),
                        report,
                        keep_clashes=True,
                    )
                continue
            if name == "memory":
                if (
                    src.is_dir()
                    and not src.is_symlink()
                    and os.path.lexists(memory_target)
                ):
                    # Keep MEMORY.md and USER.md together; merge the rest.
                    _move_group(
                        *_memory_group(src, memory_target),
                        report,
                        keep_clashes=True,
                    )
                    for child in sorted(src.iterdir()):
                        if child.name not in MEMORY_FILES:
                            _move_one(child, memory_target / child.name, report)
                    if not dry_run:
                        with contextlib.suppress(OSError):
                            src.rmdir()
                else:
                    _move_one(src, memory_target, report)
                continue
            if name.endswith((".db", ".sqlite")):
                if not os.path.lexists(src):
                    # Sidecars without their database are left alone: a
                    # stray journal must never land beside another database.
                    continue
                _move_group(*_sqlite_group(src, target), report)
                continue
            _move_root(src, target, report)
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


def live_host_env(
    env: Mapping[str, str],
    *,
    home: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """``env`` with the live host's default agent folder filled in.

    Every live entry point (stdio ``main``, the SSE / one-click launcher)
    calls this, so the host opens ``<home>/.omega-prime/agent`` exactly as
    the engine child does. Only a mapping that carries ``HOME`` is a live
    host; an empty test mapping stays isolated. A configured
    ``OMEGA_PRIME_AGENT_STATE_DIR`` or ``OMEGA_PRIME_STATE_DIR`` wins.
    """
    out = dict(env)
    if str(out.get(AGENT_STATE_ENV, "") or "").strip():
        return out
    if str(out.get(STATE_DIR_ENV, "") or "").strip():
        return out
    env_home = str(out.get("HOME", "") or "").strip()
    if not env_home:
        return out
    agent = agent_state_dir({}, home=home if home is not None else env_home)
    if agent is not None and ensure_agent_dir(agent):
        out[AGENT_STATE_ENV] = str(agent)
    return out


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
