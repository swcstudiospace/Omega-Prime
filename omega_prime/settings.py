"""Safely load non-secret settings from a user-owned JSON file."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

_ALLOWED = frozenset(
    {
        "SUBSTRATE_URL",
        "HINDSIGHT_URL",
        "ULTRATHINK_ROOT",
        "ULTRATHINK_SHIP",
        "OMEGA_PRIME_STATE_DIR",
    }
)
_SECRET_PARTS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "PAT", "AUTH")


@dataclass(frozen=True, repr=False)
class SettingsResult:
    """Effective configuration and value-free loading provenance."""

    effective_env: Mapping[str, str] = field(repr=False)
    status: str
    loaded_names: tuple[str, ...]
    rejected_names: tuple[str, ...]
    reasons: tuple[str, ...]

    def __repr__(self) -> str:
        return (
            f"SettingsResult(status={self.status!r}, loaded_names={self.loaded_names!r}, "
            f"rejected_names={self.rejected_names!r}, reasons={self.reasons!r})"
        )


def load_settings(
    process_env: Mapping[str, str],
    home: str | os.PathLike[str] | None = None,
    path: str | os.PathLike[str] | None = None,
) -> SettingsResult:
    """Copy process configuration and atomically overlay safe settings-file entries."""
    effective = dict(process_env)
    if path is not None:
        candidate = Path(path).expanduser()
    elif "OMEGA_PRIME_SETTINGS_FILE" in process_env:
        candidate = Path(process_env["OMEGA_PRIME_SETTINGS_FILE"]).expanduser()
    else:
        base = Path(home).expanduser() if home is not None else Path.home()
        candidate = base / ".omega-prime" / "settings.json"

    try:
        resolved, before = _guard_and_open(candidate)
    except FileNotFoundError:
        return _result(effective, "missing")
    except Exception:
        return _result(effective, "rejected", reasons=("unsafe_path",))

    try:
        with os.fdopen(before, "rb", closefd=True) as stream:
            after = os.fstat(stream.fileno())
            if not _same_file_identity(resolved, after):
                return _result(
                    effective, "rejected", reasons=("file_identity_changed",)
                )
            raw = stream.read()
        # Confirm the candidate still resolves to the checked file before parsing.
        now = candidate.resolve(strict=True)
        current = now.stat()
        if now != resolved or not _same_file_identity(resolved, current):
            return _result(effective, "rejected", reasons=("file_identity_changed",))
    except Exception:
        return _result(effective, "rejected", reasons=("file_read_failed",))

    try:
        document = json.loads(raw, object_pairs_hook=_unique_object)
    except Exception:
        return _result(effective, "malformed", reasons=("invalid_json",))
    if not isinstance(document, dict):
        return _result(effective, "malformed", reasons=("invalid_document",))

    rejected: list[str] = []
    overlay: dict[str, str] = {}
    for name, value in document.items():
        # Inspect key names only until it is known to be an allowlisted key.
        if any(part in name.upper() for part in _SECRET_PARTS):
            rejected.append(name)
            continue
        if name not in _ALLOWED:
            warnings.warn(
                f"Ignoring unknown settings key {name}", UserWarning, stacklevel=2
            )
            continue
        if not isinstance(value, str):
            rejected.append(name)
            continue
        overlay[name] = value

    if rejected:
        return _result(
            effective,
            "rejected",
            rejected_names=tuple(sorted(rejected)),
            reasons=("rejected_keys",),
        )

    loaded: list[str] = []
    for name, value in overlay.items():
        if name not in process_env:
            effective[name] = value
            loaded.append(name)
    return _result(effective, "loaded", loaded_names=tuple(sorted(loaded)))


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _result(
    effective: dict[str, str],
    status: str,
    loaded_names: tuple[str, ...] = (),
    rejected_names: tuple[str, ...] = (),
    reasons: tuple[str, ...] = (),
) -> SettingsResult:
    return SettingsResult(
        MappingProxyType(effective), status, loaded_names, rejected_names, reasons
    )


def _same_file_identity(path: Path, info: os.stat_result) -> bool:
    try:
        current = path.stat()
    except OSError:
        return False
    return (current.st_dev, current.st_ino, current.st_mode) == (
        info.st_dev,
        info.st_ino,
        info.st_mode,
    )


def _guard_and_open(candidate: Path) -> tuple[Path, int]:
    lexical = Path(os.path.abspath(candidate))
    # Preserve the missing-file result without consulting repository metadata.
    lexical.lstat()
    resolved = lexical.resolve(strict=True)
    lexical_roots, lexical_tracked = _worktree_metadata(lexical)
    resolved_roots, resolved_tracked = _worktree_metadata(resolved)
    roots = tuple(dict.fromkeys((*lexical_roots, *resolved_roots)))
    tracked = lexical_tracked | resolved_tracked
    for root in roots:
        root_lexical = Path(os.path.abspath(root))
        root_real = root_lexical.resolve(strict=True)
        if _within(lexical, root_lexical) or _within(lexical, root_real):
            raise OSError("candidate in worktree")
        if _within(resolved, root_lexical) or _within(resolved, root_real):
            raise OSError("resolved candidate in worktree")
        try:
            relative = resolved.relative_to(root_real).as_posix()
        except ValueError:
            relative = ""
        if relative and relative in tracked:
            raise OSError("tracked candidate")

    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OSError("unsafe file mode")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(resolved, flags)
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
        info.st_dev,
        info.st_ino,
    ):
        os.close(descriptor)
        raise OSError("file identity changed")
    if opened.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        os.close(descriptor)
        raise OSError("unsafe file mode")
    return resolved, descriptor


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _worktree_metadata(
    candidate: Path | None = None,
) -> tuple[tuple[Path, ...], frozenset[str]]:
    """Resolve Git-reported worktrees and tracked paths for the candidate checkout."""
    git_env = os.environ.copy()
    git_env["LC_ALL"] = "C"
    try:
        current = Path(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=candidate.parent if candidate is not None else None,
                check=True,
                capture_output=True,
                text=True,
                env=git_env,
            ).stdout.strip()
        )
    except subprocess.CalledProcessError as exc:
        # Git uses a nonzero status both outside a repository and when it
        # cannot inspect a checkout (for example, dubious ownership). Only
        # the explicit non-repository diagnostic is safe to treat as absence.
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        if "not a git repository" in stderr.lower():
            return (), frozenset()
        raise
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=current,
        check=True,
        capture_output=True,
        text=True,
        env=git_env,
    ).stdout
    roots: list[Path] = []
    for line in listing.splitlines():
        if line.startswith("worktree "):
            root = Path(line[9:])
            marker = root / ".git"
            if marker.is_file():
                pointer = marker.read_text(encoding="utf-8")
                if not pointer.startswith("gitdir: "):
                    raise OSError("invalid worktree pointer")
            checked = Path(
                subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                    env=git_env,
                ).stdout.strip()
            )
            roots.append(checked)
    if not roots:
        roots.append(current)
    tracked: set[str] = set()
    for root in roots:
        output = subprocess.run(
            ["git", "ls-files", "--full-name"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            env=git_env,
        ).stdout
        tracked.update(line for line in output.splitlines() if line)
    return tuple(roots), frozenset(tracked)
