# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Project-local omp MCP configuration attachment with identity-safe cleanup."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Any

_INSTALL = Path(__file__).resolve().parents[2]

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
) -> dict[str, Any]:
    """Build the secret-free stdio server entry for a single work tree."""
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
    if clarify_socket_path is not None:
        socket_path = str(clarify_socket_path)
        if not socket_path:
            raise ValueError("clarify socket path must not be empty")
        entry["env"] = {"OMEGA_PRIME_CLARIFY_SOCKET": socket_path}
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


def _artifact_stat(omp_fd: int, relative_path: str) -> os.stat_result:
    parent_fd, name = _artifact_parent(omp_fd, relative_path)
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    finally:
        os.close(parent_fd)


def _remove_artifact(
    omp_fd: int, relative_path: str, identity: tuple[int, int]
) -> None:
    parent_fd, name = _artifact_parent(omp_fd, relative_path)
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity:
            raise OSError("artifact identity changed")
        if stat.S_ISDIR(current.st_mode):
            os.rmdir(name, dir_fd=parent_fd)
        else:
            os.unlink(name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


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
        self._artifacts: dict[str, tuple[int, int]] = {}
        self.cleanup_conflicts: list[str] = []

    def register_artifact(
        self, relative_path: str, prepared_identity: tuple[int, int]
    ) -> None:
        """Register a prepared nested artifact beneath work-root ``.omp``.

        For example, ``.omp/skills`` and ``.omp/skills/python``. Parent
        directories are opened descriptor-relatively without following links;
        identity is the prepared entry's ``(st_dev, st_ino)``.
        """
        if self._released:
            raise RuntimeError("attachment lease is already released")
        if not isinstance(prepared_identity, tuple) or len(prepared_identity) != 2:
            raise ValueError("artifact identity must be a (device, inode) tuple")
        current = _artifact_stat(self._omp_fd, relative_path)
        if (current.st_dev, current.st_ino) != prepared_identity:
            raise ValueError("prepared artifact identity does not match")
        previous = self._artifacts.get(relative_path)
        if previous is not None and previous != prepared_identity:
            raise ValueError("artifact identity is already owned")
        self._artifacts[relative_path] = prepared_identity
        self._write_journal()

    @property
    def active(self) -> bool:
        return not self._released

    def create_directory_artifact(
        self, relative_path: str, *, mode: int = 0o700
    ) -> tuple[int, int]:
        """Create and journal a directory beneath the owned ``.omp`` root."""
        if self._released:
            raise RuntimeError("attachment lease is already released")
        parent_fd, name = _artifact_parent(self._omp_fd, relative_path)
        identity: tuple[int, int] | None = None
        try:
            os.mkdir(name, mode, dir_fd=parent_fd)
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            identity = (current.st_dev, current.st_ino)
        finally:
            os.close(parent_fd)
        if identity is None:
            raise RuntimeError("created artifact identity is unavailable")
        try:
            self.register_artifact(relative_path, identity)
        except BaseException:
            with contextlib.suppress(OSError):
                _remove_artifact(self._omp_fd, relative_path, identity)
            raise
        return identity

    def create_symlink_artifact(
        self, relative_path: str, target: str | Path
    ) -> tuple[int, int]:
        """Create and journal a symlink beneath the owned ``.omp`` root."""
        if self._released:
            raise RuntimeError("attachment lease is already released")
        parent_fd, name = _artifact_parent(self._omp_fd, relative_path)
        identity: tuple[int, int] | None = None
        try:
            os.symlink(os.fspath(target), name, dir_fd=parent_fd)
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            identity = (current.st_dev, current.st_ino)
        finally:
            os.close(parent_fd)
        if identity is None:
            raise RuntimeError("created artifact identity is unavailable")
        try:
            self.register_artifact(relative_path, identity)
        except BaseException:
            with contextlib.suppress(OSError):
                _remove_artifact(self._omp_fd, relative_path, identity)
            raise
        return identity

    def remove_artifact(self, relative_path: str, identity: tuple[int, int]) -> None:
        if self._released or self._artifacts.get(relative_path) != identity:
            raise ValueError("artifact is not owned by this attachment")
        _remove_artifact(self._omp_fd, relative_path, identity)

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
                name: list(identity) for name, identity in self._artifacts.items()
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
                    artifact = _artifact_stat(self._omp_fd, name)
                except FileNotFoundError:
                    continue
                if (artifact.st_dev, artifact.st_ino) != identity:
                    conflicts.append(f"{name} artifact identity changed")
                else:
                    artifact_entries.append(name)
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
                        owned_artifacts: list[tuple[str, tuple[int, int]]] = []
                        artifacts_valid = True
                        for artifact_name, identity in artifacts.items():
                            if (
                                not isinstance(artifact_name, str)
                                or not isinstance(identity, list)
                                or len(identity) != 2
                                or any(not isinstance(value, int) for value in identity)
                            ):
                                artifacts_valid = False
                                break
                            try:
                                artifact_stat = _artifact_stat(omp_fd, artifact_name)
                            except FileNotFoundError:
                                continue
                            except ValueError:
                                artifacts_valid = False
                                break
                            if [artifact_stat.st_dev, artifact_stat.st_ino] != identity:
                                artifacts_valid = False
                                break
                            owned_artifacts.append((artifact_name, tuple(identity)))
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
) -> AcquiredAttachment:
    """Acquire .omp/mcp.json exclusively and persist its run ownership."""
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
