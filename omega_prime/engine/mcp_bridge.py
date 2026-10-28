# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Project-local omp MCP configuration attachment with identity-safe cleanup."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import json
import os
import secrets
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from omega_prime.agent_state import AGENT_STATE_ENV, AGENT_SUBDIR

_INSTALL = Path(__file__).resolve().parents[2]
_AT_EMPTY_PATH = 0x1000
_RENAME_NOREPLACE = 1
_HANDLE_BYTES = 128
_AT_STATX_SYNC_AS_STAT = 0x0000
_AT_SYMLINK_NOFOLLOW = 0x100
_AT_HANDLE_FID = 0x200
_STATX_BTIME = 0x800
# Pseudo handle types for filesystems that cannot encode a file handle (for
# example an overlay or sandboxed rootfs that answers EOPNOTSUPP). Kernel
# handle types are never negative, so these cannot collide with a real one.
_HANDLE_TYPE_BIRTH_TIME = -2
_HANDLE_TYPE_NONE = -1
_HANDLE_UNSUPPORTED = frozenset({errno.EOPNOTSUPP, errno.ENOTSUP, errno.ENOSYS})
# Device, inode, and the filesystem generation handle from the pinned descriptor.
# The generation is a kernel file handle, else a statx birth time, else empty
# (``_HANDLE_TYPE_NONE``): an empty generation is only trusted while this
# process still pins the inode, and is never written as recoverable identity.
_ArtifactIdentity = tuple[int, int, int, bytes]


class _FileHandle(ctypes.Structure):
    _fields_ = (
        ("handle_bytes", ctypes.c_uint),
        ("handle_type", ctypes.c_int),
        ("f_handle", ctypes.c_ubyte * _HANDLE_BYTES),
    )


_LIBC = ctypes.CDLL(None, use_errno=True)
_renameat2 = _LIBC.renameat2
_renameat2.argtypes = [
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_uint,
]
_renameat2.restype = ctypes.c_int
_name_to_handle_at = _LIBC.name_to_handle_at
_name_to_handle_at.argtypes = [
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.POINTER(_FileHandle),
    ctypes.POINTER(ctypes.c_int),
    ctypes.c_int,
]
_name_to_handle_at.restype = ctypes.c_int


class _StatxTimestamp(ctypes.Structure):
    _fields_ = (
        ("tv_sec", ctypes.c_int64),
        ("tv_nsec", ctypes.c_uint32),
        ("_reserved", ctypes.c_int32),
    )


class _Statx(ctypes.Structure):
    _fields_ = (
        ("stx_mask", ctypes.c_uint32),
        ("stx_blksize", ctypes.c_uint32),
        ("stx_attributes", ctypes.c_uint64),
        ("stx_nlink", ctypes.c_uint32),
        ("stx_uid", ctypes.c_uint32),
        ("stx_gid", ctypes.c_uint32),
        ("stx_mode", ctypes.c_uint16),
        ("_spare0", ctypes.c_uint16),
        ("stx_ino", ctypes.c_uint64),
        ("stx_size", ctypes.c_uint64),
        ("stx_blocks", ctypes.c_uint64),
        ("stx_attributes_mask", ctypes.c_uint64),
        ("stx_atime", _StatxTimestamp),
        ("stx_btime", _StatxTimestamp),
        ("stx_ctime", _StatxTimestamp),
        ("stx_mtime", _StatxTimestamp),
        ("stx_rdev_major", ctypes.c_uint32),
        ("stx_rdev_minor", ctypes.c_uint32),
        ("stx_dev_major", ctypes.c_uint32),
        ("stx_dev_minor", ctypes.c_uint32),
        # stx_mnt_id, the dio alignments and the kernel's reserved tail.
        ("_spare", ctypes.c_uint64 * 14),
    )


_statx = getattr(_LIBC, "statx", None)
if _statx is not None:
    _statx.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.POINTER(_Statx),
    ]
    _statx.restype = ctypes.c_int

ENGINE_SYSTEM_PROMPT = (
    "You are Omega Prime's coding loop running on omp. "
    "Omega Prime supplies tools through the omega-prime MCP server, plus "
    "skills, prompts, and loop policy. Do not call model provider HTTP APIs. "
    "Do not print secrets, tokens, or environment values."
)


def python_executable() -> str:
    """Return the absolute interpreter used by this installation."""
    return sys.executable


def install_root() -> Path:
    """Repository root that holds the roster and policy."""
    return _INSTALL


def mcp_server_entry(
    python: str | None = None,
    work_root: Path | None = None,
    clarify_socket_path: str | Path | None = None,
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Build the secret-free stdio server entry for a single work tree.

    ``state_dir`` is the agent data folder (``jobs.agent_data_dir``), the one
    directory the sandbox binds writable. The child gets it as both
    ``OMEGA_PRIME_AGENT_STATE_DIR`` (where its stores live, the same folder
    the host server opens) and ``OMEGA_PRIME_STATE_DIR`` (so nothing in the
    child resolves the operator's state root, which holds secrets).
    """
    executable = Path(python or python_executable())
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError("MCP interpreter must be an absolute existing file")
    if work_root is None:
        raise ValueError("work_root is required")
    entry: dict[str, Any] = {
        "type": "stdio",
        "command": str(executable),
        "args": [
            "-m",
            "omega_prime.mcp_server",
            "--root",
            str(_INSTALL),
            "--work-root",
            str(Path(os.path.abspath(work_root))),
        ],
        "cwd": str(_INSTALL),
    }
    child_env: dict[str, str] = {}
    if clarify_socket_path is not None:
        socket_path = str(clarify_socket_path)
        if not socket_path:
            raise ValueError("clarify socket path must not be empty")
        child_env["OMEGA_PRIME_CLARIFY_SOCKET"] = socket_path
    if state_dir is not None:
        state_text = str(state_dir)
        if not state_text or not Path(state_text).is_absolute():
            raise ValueError("MCP agent data dir must be an absolute path")
        child_env[AGENT_STATE_ENV] = state_text
        child_env["OMEGA_PRIME_STATE_DIR"] = state_text
    if child_env:
        entry["env"] = child_env
    return entry


def _process_identity(pid: int) -> dict[str, Any]:
    """Return PID plus boot and kernel start time; PID alone is never identity."""
    stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    fields = stat_text[stat_text.rfind(")") + 2 :].split()
    boot_id = (
        Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    )
    return {"pid": pid, "start_time": fields[19], "boot_id": boot_id}


def _identity_alive(identity: Any) -> bool | None:
    if (
        not isinstance(identity, dict)
        or not isinstance(identity.get("pid"), int)
        or not isinstance(identity.get("start_time"), str)
        or not isinstance(identity.get("boot_id"), str)
    ):
        return None
    try:
        current = _process_identity(identity["pid"])
    except FileNotFoundError:
        return False
    except (OSError, IndexError):
        return None
    return current == identity


def _artifact_parent(omp_fd: int, relative_path: str) -> tuple[int, str]:
    """Open the parent of a validated work-root-relative artifact without links."""
    if not isinstance(relative_path, str) or "\x00" in relative_path:
        raise ValueError("artifact path must be a string")
    parts = relative_path.split("/")
    if (
        len(parts) < 2
        or parts[0] != ".omp"
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("artifact path must be beneath .omp")
    parent_fd = os.dup(omp_fd)
    try:
        for part in parts[1:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            os.close(parent_fd)
            parent_fd = next_fd
        return parent_fd, parts[-1]
    except BaseException:
        os.close(parent_fd)
        raise


def _rename_noreplace(src_dir: int, src: str, dst_dir: int, dst: str) -> None:
    """Publish ``src`` as ``dst`` without replacing an existing entry."""
    rc = _renameat2(
        src_dir,
        os.fsencode(src),
        dst_dir,
        os.fsencode(dst),
        _RENAME_NOREPLACE,
    )
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), dst)


def _handle_of(fd: int, name: bytes, flags: int) -> tuple[int, bytes]:
    """Return the file-handle generation of an open descriptor or path."""
    handle = _FileHandle()
    handle.handle_bytes = _HANDLE_BYTES
    mount_id = ctypes.c_int()
    rc = _name_to_handle_at(
        fd, name, ctypes.byref(handle), ctypes.byref(mount_id), flags
    )
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    size = int(handle.handle_bytes)
    if size <= 0 or size > _HANDLE_BYTES:
        raise OSError("artifact file handle has an unexpected size")
    return int(handle.handle_type), bytes(handle.f_handle[:size])


def _birth_time_of(fd: int) -> bytes | None:
    """statx birth time of ``fd`` in nanoseconds, or None when not reported."""
    if _statx is None:
        return None
    buf = _Statx()
    rc = _statx(
        fd,
        b"",
        _AT_EMPTY_PATH | _AT_SYMLINK_NOFOLLOW | _AT_STATX_SYNC_AS_STAT,
        _STATX_BTIME,
        ctypes.byref(buf),
    )
    if rc != 0:
        err = ctypes.get_errno()
        if err in _HANDLE_UNSUPPORTED:
            return None
        raise OSError(err, os.strerror(err))
    if not buf.stx_mask & _STATX_BTIME:
        return None
    stamp = buf.stx_btime
    if stamp.tv_sec == 0 and stamp.tv_nsec == 0:
        return None
    nanoseconds = int(stamp.tv_sec) * 1_000_000_000 + int(stamp.tv_nsec)
    return nanoseconds.to_bytes(16, "big", signed=True)


def _generation_of(fd: int) -> tuple[int, bytes]:
    """Generation evidence for the inode ``fd`` holds, strongest first.

    A kernel file handle carries the inode generation, so a reused inode
    number never matches. Some filesystems refuse to encode one: an
    exportable handle first, then an identifier-only handle
    (``AT_HANDLE_FID``), then the statx birth time, which a reused inode
    number also cannot share. When none is available the generation is
    empty; such identity is only sound while the owner still pins the inode.
    """
    try:
        return _handle_of(fd, b"", _AT_EMPTY_PATH)
    except OSError as exc:
        if exc.errno not in _HANDLE_UNSUPPORTED:
            raise
    try:
        return _handle_of(fd, b"", _AT_EMPTY_PATH | _AT_HANDLE_FID)
    except OSError as exc:
        # Kernels before 6.5 reject the unknown AT_HANDLE_FID flag with EINVAL.
        if exc.errno not in _HANDLE_UNSUPPORTED and exc.errno != errno.EINVAL:
            raise
    birth = _birth_time_of(fd)
    if birth is not None:
        return _HANDLE_TYPE_BIRTH_TIME, birth
    return _HANDLE_TYPE_NONE, b""


# Linux CLOCK_REALTIME_COARSE: the clock the kernel stamps new inodes from.
_CLOCK_REALTIME_COARSE = getattr(time, "CLOCK_REALTIME_COARSE", 5)
# Coarse clock ticks are a jiffy (at most 10 ms); give the clock a few of them.
_BIRTH_SETTLE_SECONDS = 0.05


def _birth_is_settled(birth: bytes) -> bool:
    """True once the inode-stamping clock has moved past ``birth``.

    Birth times come from the coarse clock, so an inode freed and reused within
    the same tick reports the same birth time. The pin keeps the inode alive
    until after its identity is recorded, so any reuse is born later; once the
    coarse clock is past ``birth`` that reuse cannot share it.
    """
    born = int.from_bytes(birth, "big", signed=True)
    deadline = time.monotonic() + _BIRTH_SETTLE_SECONDS
    while True:
        try:
            now = time.clock_gettime_ns(_CLOCK_REALTIME_COARSE)
        except OSError:
            return False
        if now > born:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.001)


def _identity_from_pin(pin: int) -> _ArtifactIdentity:
    """Identity of the inode a pin holds, not of whatever path it once named."""
    current = os.fstat(pin)
    handle_type, handle = _generation_of(pin)
    if handle_type == _HANDLE_TYPE_BIRTH_TIME and not _birth_is_settled(handle):
        # The clock did not move past the birth time (or ran backwards), so a
        # reuse could share it: keep pinned-only identity instead.
        handle_type, handle = _HANDLE_TYPE_NONE, b""
    return (current.st_dev, current.st_ino, handle_type, handle)


def _identity_matches(
    artifact_fd: int, identity: _ArtifactIdentity | tuple[int, int]
) -> bool:
    current = os.fstat(artifact_fd)
    if (current.st_dev, current.st_ino) != (identity[0], identity[1]):
        return False
    if len(identity) == 2:
        return True
    if identity[2] == _HANDLE_TYPE_NONE:
        # Pinned-only identity: callers hold a reference to the inode (the
        # lease pin, or the rollback guard), so its number cannot have been
        # reused. Never parsed back from a journal.
        return True
    handle_type, handle = _generation_of(artifact_fd)
    return handle_type == identity[2] and handle == identity[3]


def _open_artifact(omp_fd: int, relative_path: str) -> tuple[int, int, str]:
    """Open the artifact without following links. Caller closes both fds."""
    parent_fd, name = _artifact_parent(omp_fd, relative_path)
    try:
        artifact_fd = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent_fd)
    except BaseException:
        os.close(parent_fd)
        raise
    return parent_fd, artifact_fd, name


def _artifact_matches(
    omp_fd: int,
    relative_path: str,
    identity: _ArtifactIdentity | tuple[int, int],
) -> bool:
    parent_fd, artifact_fd, _name = _open_artifact(omp_fd, relative_path)
    try:
        return _identity_matches(artifact_fd, identity)
    finally:
        os.close(artifact_fd)
        os.close(parent_fd)


def _remove_artifact(
    omp_fd: int,
    relative_path: str,
    identity: _ArtifactIdentity | tuple[int, int],
) -> None:
    parent_fd, artifact_fd, name = _open_artifact(omp_fd, relative_path)
    try:
        if not _identity_matches(artifact_fd, identity):
            raise OSError("artifact identity changed")
        if stat.S_ISDIR(os.fstat(artifact_fd).st_mode):
            os.rmdir(name, dir_fd=parent_fd)
        else:
            os.unlink(name, dir_fd=parent_fd)
    finally:
        os.close(artifact_fd)
        os.close(parent_fd)


def _discard_pinned_staging(parent_fd: int, staging: str, pin: int) -> None:
    """Unlink a private staging entry only when it is still the pinned inode."""
    try:
        current = os.stat(staging, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    pinned = os.fstat(pin)
    if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
        return
    identity = _identity_from_pin(pin)
    try:
        fresh = os.open(staging, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    try:
        if not _identity_matches(fresh, identity):
            return
        directory = stat.S_ISDIR(os.fstat(fresh).st_mode)
    finally:
        os.close(fresh)
    if directory:
        os.rmdir(staging, dir_fd=parent_fd)
    else:
        os.unlink(staging, dir_fd=parent_fd)


def _publish_pinned_artifact(
    omp_fd: int,
    relative_path: str,
    create: Callable[[int, str], None],
    validate: Callable[[int], None],
) -> int:
    """Create under a private name, pin that inode, then publish it.

    The public name does not exist until the ``O_PATH|O_NOFOLLOW`` pin is
    open, so a replacement cannot be handed the prepared inode number. The
    caller owns the returned descriptor and reads identity from it.
    """
    parent_fd, name = _artifact_parent(omp_fd, relative_path)
    pin = -1
    published = False
    staging = ""
    try:
        created = False
        for _attempt in range(3):
            staging = ".prep-" + secrets.token_hex(16)
            try:
                create(parent_fd, staging)
            except FileExistsError:
                continue
            created = True
            break
        if not created:
            raise FileExistsError(relative_path)
        pin = os.open(staging, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent_fd)
        validate(pin)
        _rename_noreplace(parent_fd, staging, parent_fd, name)
        published = True
        owned = pin
        pin = -1
        return owned
    except BaseException:
        if pin >= 0:
            with contextlib.suppress(OSError):
                _discard_pinned_staging(parent_fd, staging, pin)
            os.close(pin)
        elif staging and not published:
            with contextlib.suppress(OSError):
                os.unlink(staging, dir_fd=parent_fd)
            with contextlib.suppress(OSError):
                os.rmdir(staging, dir_fd=parent_fd)
        raise
    finally:
        os.close(parent_fd)


def _pin_matches_path(pin: int, relative_path: str) -> bool:
    path = os.readlink(f"/proc/self/fd/{pin}")
    deleted = " (deleted)"
    if path.endswith(deleted):
        path = path[: -len(deleted)]
    return path.endswith("/" + relative_path)


def _parse_artifact_identity(
    raw: Any,
) -> _ArtifactIdentity | tuple[int, int] | None:
    if not isinstance(raw, list) or any(isinstance(item, bool) for item in raw):
        return None
    if len(raw) == 2 and all(isinstance(item, int) for item in raw):
        return (raw[0], raw[1])
    if (
        len(raw) == 4
        and isinstance(raw[0], int)
        and isinstance(raw[1], int)
        and isinstance(raw[2], int)
        and isinstance(raw[3], str)
        and raw[3]
    ):
        try:
            handle = bytes.fromhex(raw[3])
        except ValueError:
            return None
        if not handle:
            return None
        return (raw[0], raw[1], raw[2], handle)
    return None


class AcquiredAttachment:
    """Exclusive run-owned project config, journaled under private job state."""

    def __init__(
        self,
        work_root: Path,
        parent_fd: int,
        omp_fd: int,
        omp_identity: tuple[int, int],
        config_identity: tuple[int, int],
        config_content: bytes,
        journal_path: Path,
        journal_identity: tuple[int, int],
        owner: str,
        owner_identity: dict[str, Any],
    ):
        self.work_root = work_root
        self.path = work_root / ".omp" / "mcp.json"
        self._parent_fd = parent_fd
        self._omp_fd = omp_fd
        self._omp_identity = omp_identity
        self._config_identity = config_identity
        self._config_content = config_content
        self._journal_path = journal_path
        self._journal_identity = journal_identity
        self._owner = owner
        self._owner_identity = owner_identity
        self._child_identity: dict[str, Any] | None = None
        self._released = False
        self._artifacts: dict[str, _ArtifactIdentity] = {}
        # Open O_PATH references that keep each registered inode allocated
        # until release, so a replacement entry never shares its number.
        self._artifact_pins: dict[str, int] = {}
        self.cleanup_conflicts: list[str] = []

    def register_artifact(self, relative_path: str, pin: int) -> _ArtifactIdentity:
        """Register a prepared artifact from the descriptor that already pins it.

        ``pin`` is an ``O_PATH|O_NOFOLLOW`` descriptor opened before the inode
        was published at ``relative_path``. Identity comes from that descriptor
        (device, inode, and file handle). The public path is not opened to
        discover identity: a replacement may already stand there, and ext4 can
        give it a previously saved inode number. The lease keeps ``pin`` until
        release so the prepared inode number cannot be reused while it is held.
        """
        if self._released:
            os.close(pin)
            raise RuntimeError("attachment lease is already released")
        if isinstance(pin, bool) or not isinstance(pin, int) or pin < 0:
            raise ValueError("artifact pin must be an open descriptor")
        try:
            parent_fd, _name = _artifact_parent(self._omp_fd, relative_path)
            os.close(parent_fd)
            if not _pin_matches_path(pin, relative_path):
                raise ValueError("prepared artifact pin does not match the path")
            current = os.fstat(pin)
            if not (stat.S_ISDIR(current.st_mode) or stat.S_ISLNK(current.st_mode)):
                raise ValueError("artifact must be a directory or a symlink")
            identity = _identity_from_pin(pin)
            previous = self._artifacts.get(relative_path)
            if previous is not None and previous != identity:
                raise ValueError("artifact identity is already owned")
            self._artifacts[relative_path] = identity
            if relative_path not in self._artifact_pins:
                self._artifact_pins[relative_path] = pin
                pin = -1
            self._write_journal()
            return identity
        finally:
            if pin >= 0:
                os.close(pin)

    @property
    def active(self) -> bool:
        return not self._released

    def create_directory_artifact(
        self, relative_path: str, *, mode: int = 0o700
    ) -> tuple[int, int]:
        """Create and journal a directory beneath the owned ``.omp`` root."""
        if self._released:
            raise RuntimeError("attachment lease is already released")

        def create_at(parent_fd: int, staging: str) -> None:
            os.mkdir(staging, mode, dir_fd=parent_fd)

        def validate(pin: int) -> None:
            if not stat.S_ISDIR(os.fstat(pin).st_mode):
                raise OSError("prepared artifact is not a directory")

        return self._register_published(relative_path, create_at, validate)

    def create_symlink_artifact(
        self, relative_path: str, target: str | Path
    ) -> tuple[int, int]:
        """Create and journal a symlink beneath the owned ``.omp`` root."""
        if self._released:
            raise RuntimeError("attachment lease is already released")
        target_text = os.fspath(target)

        def create_at(parent_fd: int, staging: str) -> None:
            os.symlink(target_text, staging, dir_fd=parent_fd)

        def validate(pin: int) -> None:
            if os.readlink("", dir_fd=pin) != target_text:
                raise OSError("prepared symlink target does not match")
            if not stat.S_ISLNK(os.fstat(pin).st_mode):
                raise OSError("prepared artifact is not a symlink")

        return self._register_published(relative_path, create_at, validate)

    def _register_published(
        self,
        relative_path: str,
        create: Callable[[int, str], None],
        validate: Callable[[int], None],
    ) -> tuple[int, int]:
        pin = _publish_pinned_artifact(self._omp_fd, relative_path, create, validate)
        published = os.fstat(pin)
        # register_artifact closes ``pin`` when it fails. A second reference
        # keeps the inode allocated through any rollback, so a replacement at
        # the path can never reuse its number and match a dev/ino identity.
        try:
            guard = os.dup(pin)
        except BaseException:
            with contextlib.suppress(OSError):
                _remove_artifact(
                    self._omp_fd, relative_path, (published.st_dev, published.st_ino)
                )
            os.close(pin)
            raise
        try:
            try:
                identity = _identity_from_pin(pin)
            except BaseException:
                os.close(pin)
                with contextlib.suppress(OSError):
                    _remove_artifact(
                        self._omp_fd,
                        relative_path,
                        (published.st_dev, published.st_ino),
                    )
                raise
            try:
                registered = self.register_artifact(relative_path, pin)
            except BaseException:
                with contextlib.suppress(OSError):
                    _remove_artifact(self._omp_fd, relative_path, identity)
                raise
            return (registered[0], registered[1])
        finally:
            os.close(guard)

    def remove_artifact(self, relative_path: str, identity: tuple[int, int]) -> None:
        owned = self._artifacts.get(relative_path)
        if (
            self._released
            or owned is None
            or (owned[0], owned[1]) != (identity[0], identity[1])
        ):
            raise ValueError("artifact is not owned by this attachment")
        _remove_artifact(self._omp_fd, relative_path, owned)

    def record_child(self, pid: int) -> None:
        self._child_identity = _process_identity(pid)
        self._write_journal(self._child_identity)

    def _write_journal(self, child: dict[str, Any] | None = None) -> None:
        payload = {
            "owner": self._owner,
            "owner_process": self._owner_identity,
            "work_root": str(self.work_root),
            "omp_identity": list(self._omp_identity),
            "config_identity": list(self._config_identity),
            "config_content": self._config_content.decode("utf-8"),
            "child": child if child is not None else self._child_identity,
            "artifacts": {
                name: [identity[0], identity[1], identity[2], identity[3].hex()]
                for name, identity in self._artifacts.items()
            },
        }
        temp_name = self._journal_path.name + "." + secrets.token_hex(8)
        dir_fd = os.open(
            self._journal_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=dir_fd,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream, sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(
                    temp_name,
                    self._journal_path.name,
                    src_dir_fd=dir_fd,
                    dst_dir_fd=dir_fd,
                )
                journal_stat = os.stat(
                    self._journal_path.name, dir_fd=dir_fd, follow_symlinks=False
                )
                self._journal_identity = (journal_stat.st_dev, journal_stat.st_ino)
                os.fsync(dir_fd)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(temp_name, dir_fd=dir_fd)
                raise
        finally:
            os.close(dir_fd)

    def release(self) -> list[str]:
        if self._released:
            return self.cleanup_conflicts
        conflicts: list[str] = []
        artifact_entries: list[str] = []
        try:
            current = os.stat(".omp", dir_fd=self._parent_fd, follow_symlinks=False)
            if (
                current.st_dev,
                current.st_ino,
            ) != self._omp_identity or not stat.S_ISDIR(current.st_mode):
                conflicts.append(".omp directory identity changed")
            try:
                entry = os.stat("mcp.json", dir_fd=self._omp_fd, follow_symlinks=False)
            except FileNotFoundError:
                entry = None
            if entry is not None and (
                (entry.st_dev, entry.st_ino) != self._config_identity
                or not stat.S_ISREG(entry.st_mode)
                or _read_file_at(self._omp_fd, "mcp.json") != self._config_content
            ):
                conflicts.append("mcp.json content or identity changed")
            for name, identity in self._artifacts.items():
                try:
                    matches = _artifact_matches(self._omp_fd, name, identity)
                except FileNotFoundError:
                    continue
                if matches:
                    artifact_entries.append(name)
                else:
                    conflicts.append(f"{name} artifact identity changed")
            if not conflicts:
                for name in sorted(
                    artifact_entries, key=lambda path: path.count("/"), reverse=True
                ):
                    _remove_artifact(self._omp_fd, name, self._artifacts[name])
                if entry is not None:
                    os.unlink("mcp.json", dir_fd=self._omp_fd)
                os.fsync(self._omp_fd)
                os.rmdir(".omp", dir_fd=self._parent_fd)
                os.fsync(self._parent_fd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            conflicts.append(f"cleanup failed: {type(exc).__name__}")
        if not conflicts:
            try:
                journal_fd = os.open(
                    self._journal_path.parent,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                )
                try:
                    current = os.stat(
                        self._journal_path.name,
                        dir_fd=journal_fd,
                        follow_symlinks=False,
                    )
                    if (current.st_dev, current.st_ino) == self._journal_identity:
                        os.unlink(self._journal_path.name, dir_fd=journal_fd)
                        os.fsync(journal_fd)
                finally:
                    os.close(journal_fd)
            except OSError as exc:
                conflicts.append(f"journal cleanup failed: {type(exc).__name__}")
        self._released = True
        self.cleanup_conflicts = conflicts
        for pin in self._artifact_pins.values():
            os.close(pin)
        self._artifact_pins.clear()
        os.close(self._omp_fd)
        os.close(self._parent_fd)
        return conflicts


def _read_file_at(directory_fd: int, name: str) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    try:
        with os.fdopen(fd, "rb") as stream:
            return stream.read()
    except BaseException:
        raise


def _open_dir_chain(path: Path) -> tuple[int, Path]:
    """Open every ancestor without following symlinks."""
    absolute = Path(os.path.abspath(path))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = next_fd
        return fd, absolute
    except BaseException:
        os.close(fd)
        raise


def recover_attachments(state_root: Path, work_root: Path) -> list[str]:
    """Reclaim only journaled attachments whose recorded child is proven dead."""
    conflicts: list[str] = []
    try:
        state_fd = os.open(
            Path(state_root) / "mcp-attachments",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
    except FileNotFoundError:
        return conflicts
    except OSError:
        return ["attachment journal directory unavailable"]
    work = Path(os.path.abspath(work_root))
    try:
        for name in os.listdir(state_fd):
            if not name.endswith(".json"):
                continue
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=state_fd)
                with os.fdopen(fd, "r", encoding="utf-8") as stream:
                    journal_stat = os.fstat(stream.fileno())
                    record = json.load(stream)
                if record.get("work_root") != str(work) or not record.get("owner"):
                    continue
                owner_process = record.get("owner_process")
                if _identity_alive(owner_process) is not False:
                    continue
                child = record.get("child")
                if child is not None and _identity_alive(child) is not False:
                    continue
                parent_fd, _ = _open_dir_chain(work)
                try:
                    omp_fd = os.open(
                        ".omp",
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=parent_fd,
                    )
                    try:
                        omp_stat = os.fstat(omp_fd)
                        if [omp_stat.st_dev, omp_stat.st_ino] != record.get(
                            "omp_identity"
                        ):
                            continue
                        expected = record.get("config_identity")
                        expected_content = record.get("config_content")
                        if not isinstance(expected_content, str):
                            conflicts.append(
                                f"recovery skipped {name}: missing config content"
                            )
                            continue
                        expected_bytes = expected_content.encode("utf-8")
                        artifacts = record.get("artifacts", {})
                        if not isinstance(artifacts, dict):
                            continue
                        owned_artifacts: list[
                            tuple[str, _ArtifactIdentity | tuple[int, int]]
                        ] = []
                        artifacts_valid = True
                        for artifact_name, raw_identity in artifacts.items():
                            parsed = _parse_artifact_identity(raw_identity)
                            if not isinstance(artifact_name, str) or parsed is None:
                                artifacts_valid = False
                                break
                            try:
                                matches = _artifact_matches(
                                    omp_fd, artifact_name, parsed
                                )
                            except FileNotFoundError:
                                continue
                            except ValueError:
                                artifacts_valid = False
                                break
                            if not matches:
                                artifacts_valid = False
                                break
                            owned_artifacts.append((artifact_name, parsed))
                        if not artifacts_valid:
                            continue
                        config_stat = None
                        with contextlib.suppress(FileNotFoundError):
                            config_stat = os.stat(
                                "mcp.json", dir_fd=omp_fd, follow_symlinks=False
                            )
                        if config_stat is not None and (
                            [config_stat.st_dev, config_stat.st_ino] != expected
                            or not stat.S_ISREG(config_stat.st_mode)
                            or _read_file_at(omp_fd, "mcp.json") != expected_bytes
                        ):
                            conflicts.append(
                                f"recovery skipped {name}: mcp.json content or identity changed"
                            )
                            continue
                        stage = ".mcp-" + str(record["owner"]) + ".stage"
                        stage_stat = None
                        if config_stat is None:
                            with contextlib.suppress(FileNotFoundError):
                                stage_stat = os.stat(
                                    stage, dir_fd=omp_fd, follow_symlinks=False
                                )
                            if stage_stat is not None and (
                                [stage_stat.st_dev, stage_stat.st_ino] != expected
                                or not stat.S_ISREG(stage_stat.st_mode)
                                or _read_file_at(omp_fd, stage) != expected_bytes
                            ):
                                conflicts.append(
                                    f"recovery skipped {name}: staged config content or identity changed"
                                )
                                continue
                        for artifact_name, identity in sorted(
                            owned_artifacts,
                            key=lambda item: item[0].count("/"),
                            reverse=True,
                        ):
                            _remove_artifact(omp_fd, artifact_name, identity)
                        if config_stat is not None:
                            os.unlink("mcp.json", dir_fd=omp_fd)
                        elif stage_stat is not None:
                            os.unlink(stage, dir_fd=omp_fd)
                        os.rmdir(".omp", dir_fd=parent_fd)
                        os.fsync(parent_fd)
                    finally:
                        os.close(omp_fd)
                finally:
                    os.close(parent_fd)
                current = os.stat(name, dir_fd=state_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (
                    journal_stat.st_dev,
                    journal_stat.st_ino,
                ):
                    os.unlink(name, dir_fd=state_fd)
                    os.fsync(state_fd)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                conflicts.append(f"recovery skipped {name}: {type(exc).__name__}")
    finally:
        os.close(state_fd)
    return conflicts


def try_acquire_attachment(
    work_root: Path,
    state_root: Path,
    job_id: str,
    *,
    clarify_socket_path: str | Path | None = None,
    agent_dir: str | Path | None = None,
) -> AcquiredAttachment:
    """Acquire .omp/mcp.json exclusively and persist its run ownership.

    ``agent_dir`` is the agent data folder the child's stores live in;
    it defaults to ``<state_root>/agent``.
    """
    parent_fd, work = _open_dir_chain(work_root)
    owner_identity = _process_identity(os.getpid())
    state_root = Path(state_root)
    omp_fd = -1
    owner = secrets.token_hex(16)
    journal_dir = state_root / "mcp-attachments"
    journal_path = journal_dir / (secrets.token_hex(16) + ".json")
    created_omp = False
    config_identity: tuple[int, int] | None = None
    omp_identity: tuple[int, int] | None = None
    journal_identity: tuple[int, int] | None = None
    payload: bytes | None = None
    try:
        # State is private and exclusive; no existing job record is read or replaced.
        journal_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        journal_stat = journal_dir.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(journal_stat.st_mode)
            or journal_stat.st_uid != os.getuid()
            or stat.S_IMODE(journal_stat.st_mode) & 0o077
        ):
            raise OSError("attachment journal directory is not private")
        os.mkdir(".omp", mode=0o700, dir_fd=parent_fd)
        created_omp = True
        os.fsync(parent_fd)
        omp_fd = os.open(
            ".omp", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
        )
        omp_stat = os.fstat(omp_fd)
        omp_identity = (omp_stat.st_dev, omp_stat.st_ino)
        payload = (
            json.dumps(
                {
                    "mcpServers": {
                        "omega-prime": mcp_server_entry(
                            work_root=work,
                            clarify_socket_path=clarify_socket_path,
                            state_dir=Path(
                                os.path.abspath(
                                    agent_dir
                                    if agent_dir is not None
                                    else state_root / AGENT_SUBDIR
                                )
                            ),
                        )
                    }
                },
                indent=2,
            ).encode()
            + b"\n"
        )
        stage_name = ".mcp-" + owner + ".stage"
        fd = os.open(
            stage_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=omp_fd,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
                config_stat = os.fstat(stream.fileno())
            config_identity = (config_stat.st_dev, config_stat.st_ino)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(stage_name, dir_fd=omp_fd)
            raise
        # Persist the intended owner and inode identity before publication.
        journal_fd = os.open(
            journal_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(journal_fd, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "owner": owner,
                    "owner_process": owner_identity,
                    "job_id": job_id,
                    "work_root": str(work),
                    "omp_identity": list(omp_identity),
                    "config_identity": list(config_identity),
                    "config_content": payload.decode("utf-8"),
                    "child": None,
                    "artifacts": {},
                },
                stream,
                sort_keys=True,
            )
            stream.flush()
            os.fsync(stream.fileno())
            journal_stat = os.fstat(stream.fileno())
            journal_identity = (journal_stat.st_dev, journal_stat.st_ino)
        state_fd = os.open(journal_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(state_fd)
        finally:
            os.close(state_fd)
        # Link is atomic and fails rather than replacing a concurrently-created config.
        os.link(
            stage_name,
            "mcp.json",
            src_dir_fd=omp_fd,
            dst_dir_fd=omp_fd,
            follow_symlinks=False,
        )
        os.unlink(stage_name, dir_fd=omp_fd)
        os.fsync(omp_fd)
        assert omp_identity is not None and config_identity is not None
        assert journal_identity is not None
        return AcquiredAttachment(
            work,
            parent_fd,
            omp_fd,
            omp_identity,
            config_identity,
            payload,
            journal_path,
            journal_identity,
            owner,
            owner_identity,
        )
    except BaseException:
        if omp_fd >= 0:
            if omp_identity is not None:
                try:
                    current = os.stat(".omp", dir_fd=parent_fd, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == omp_identity:
                        if config_identity is not None:
                            try:
                                item = os.stat(
                                    "mcp.json", dir_fd=omp_fd, follow_symlinks=False
                                )
                                if (
                                    (item.st_dev, item.st_ino) == config_identity
                                    and payload is not None
                                    and stat.S_ISREG(item.st_mode)
                                    and _read_file_at(omp_fd, "mcp.json") == payload
                                ):
                                    os.unlink("mcp.json", dir_fd=omp_fd)
                            except FileNotFoundError:
                                pass
                        stage = ".mcp-" + owner + ".stage"
                        try:
                            stage_stat = os.stat(
                                stage, dir_fd=omp_fd, follow_symlinks=False
                            )
                        except FileNotFoundError:
                            stage_stat = None
                        if (
                            stage_stat is not None
                            and config_identity is not None
                            and (stage_stat.st_dev, stage_stat.st_ino)
                            == config_identity
                            and payload is not None
                            and stat.S_ISREG(stage_stat.st_mode)
                            and _read_file_at(omp_fd, stage) == payload
                        ):
                            os.unlink(stage, dir_fd=omp_fd)
                        os.rmdir(".omp", dir_fd=parent_fd)
                except OSError:
                    pass
            os.close(omp_fd)
        elif created_omp:
            with contextlib.suppress(OSError):
                os.rmdir(".omp", dir_fd=parent_fd)
        if journal_identity is not None:
            try:
                os.stat(".omp", dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                try:
                    state_fd = os.open(
                        journal_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                    )
                    try:
                        current = os.stat(
                            journal_path.name, dir_fd=state_fd, follow_symlinks=False
                        )
                        if (current.st_dev, current.st_ino) == journal_identity:
                            os.unlink(journal_path.name, dir_fd=state_fd)
                            os.fsync(state_fd)
                    finally:
                        os.close(state_fd)
                except OSError:
                    pass
            except OSError:
                pass
        os.close(parent_fd)
        raise
