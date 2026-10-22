# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Operator-local, file-backed control queue for RPC UI interactions.

The queue is deliberately separate from per-job directories: sandboxed children
receive those directories as writable binds, but never this state-root sibling.
The driver-facing boundary is :meth:`register_request`, :meth:`next_command`,
and :meth:`resolve_request`; publishing a command does not itself answer omp.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omega_prime.credentials.redact import redact_text
from omega_prime.engine.jobs import state_dir

_CONTROL_DIR = "ui-control"
_REQUESTS_DIR = "requests"
_QUEUE_DIR = "queue"
_ID_FIELDS = ("job_id", "session_generation", "turn_generation", "request_id")
_JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_CLI_FIELDS = ("job", "session", "turn", "request")


class UIControlError(ValueError):
    """Unsafe storage, malformed identity, or a stale control operation."""


def identity_key(identity: Mapping[str, Any]) -> dict[str, str]:
    """Validate and copy the complete correlation identity without rewriting it."""
    if not isinstance(identity, Mapping):
        raise UIControlError("identity must include job, session, turn and request")
    result: dict[str, str] = {}
    for field in _ID_FIELDS:
        value = identity.get(field)
        if value is None:
            cli_field = _CLI_FIELDS[_ID_FIELDS.index(field)]
            value = identity.get(cli_field)
        pattern = _JOB_ID_RE if field == "job_id" else _TOKEN_RE
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise UIControlError(f"identity {field} must be a non-empty safe token")
        result[field] = value
    return result


def _session_key(job: str, session: str) -> dict[str, str]:
    if not isinstance(job, str) or _JOB_ID_RE.fullmatch(job) is None:
        raise UIControlError("job must be a valid job id")
    if not isinstance(session, str) or _TOKEN_RE.fullmatch(session) is None:
        raise UIControlError("session must be a non-empty observed session token")
    return {"job_id": job, "session_generation": session}


def _strict_path(path: Path) -> Path:
    """Resolve existing paths only after rejecting symlink components."""
    expanded = path.expanduser()
    if ".." in expanded.parts:
        raise UIControlError("unsafe parent traversal in control path")
    absolute = Path(os.path.abspath(os.fspath(expanded)))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise UIControlError(f"unsafe symlink in control path: {current}")
    return absolute


def _overlaps(path: Path, bind: Path) -> bool:
    candidate = Path(os.path.abspath(os.fspath(bind.expanduser())))
    return path == candidate or path in candidate.parents or candidate in path.parents


class UIControlStore:
    """Private local request registry and append-only command queue.

    ``writable_binds`` must contain every effective writable child bind known
    to the caller. Paths are checked conservatively before the control store is
    opened or created. Request/command records are JSON objects with mode 0600.
    """

    def __init__(
        self,
        state_root: Path | str,
        *,
        writable_binds: tuple[Path | str, ...] | list[Path | str],
    ) -> None:
        self.state_root = _strict_path(Path(state_root))
        self.path = self.state_root / _CONTROL_DIR
        self._check_binds(writable_binds)
        self._ensure_state_root()
        self._root_fd = self._open_or_create_root()
        try:
            self._ensure_private_dir(self._root_fd, _REQUESTS_DIR)
            self._ensure_private_dir(self._root_fd, _QUEUE_DIR)
        except BaseException:
            os.close(self._root_fd)
            self._root_fd = -1
            raise

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        *,
        writable_binds: tuple[Path | str, ...] | list[Path | str],
    ) -> UIControlStore:
        return cls(state_dir(env), writable_binds=writable_binds)

    def close(self) -> None:
        if getattr(self, "_root_fd", -1) >= 0:
            os.close(self._root_fd)
            self._root_fd = -1

    def __enter__(self) -> UIControlStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _check_binds(
        self, writable_binds: tuple[Path | str, ...] | list[Path | str]
    ) -> None:
        for raw in writable_binds:
            bind = _strict_path(Path(raw))
            if _overlaps(self.path, bind):
                raise UIControlError("control directory overlaps a writable child bind")

    @staticmethod
    def _verify_private_dir(fd: int, label: str) -> None:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise UIControlError(f"{label} is not a directory")
        if info.st_uid != os.getuid():
            raise UIControlError(f"{label} has an unexpected owner")
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise UIControlError(f"{label} must have mode 0700")

    def _ensure_state_root(self) -> None:
        if self.state_root == Path(self.state_root.anchor):
            raise UIControlError("state root cannot be the filesystem root")
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        fd = os.open(self.state_root.anchor, flags)
        try:
            for component in self.state_root.parts[1:]:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(component, 0o700, dir_fd=fd)
                next_fd = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise UIControlError("state root has an unexpected type or owner")
            if stat.S_IMODE(info.st_mode) & 0o022:
                raise UIControlError("state root must not be group- or world-writable")
        finally:
            os.close(fd)

    def _open_or_create_root(self) -> int:
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        parent_fd = os.open(self.state_root, flags)
        try:
            with contextlib.suppress(FileExistsError):
                os.mkdir(_CONTROL_DIR, 0o700, dir_fd=parent_fd)
            fd = os.open(_CONTROL_DIR, flags, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        try:
            self._verify_private_dir(fd, "control directory")
            return fd
        except BaseException:
            os.close(fd)
            raise

    @classmethod
    def _ensure_private_dir(cls, parent_fd: int, name: str) -> None:
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        with contextlib.suppress(FileExistsError):
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        fd = os.open(name, flags, dir_fd=parent_fd)
        try:
            cls._verify_private_dir(fd, f"{name} directory")
        finally:
            os.close(fd)

    @staticmethod
    def _record_name(identity: Mapping[str, str]) -> str:
        material = json.dumps(
            [identity[field] for field in _ID_FIELDS],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest() + ".json"

    @staticmethod
    def _open_dir(parent_fd: int, name: str) -> int:
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        fd = os.open(name, flags, dir_fd=parent_fd)
        try:
            UIControlStore._verify_private_dir(fd, f"{name} directory")
            return fd
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def _read_record(directory_fd: int, name: str) -> dict[str, Any] | None:
        flags = (
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            fd = os.open(name, flags, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise UIControlError("control record has unsafe type, owner or mode")
            with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as stream:
                value = json.load(stream)
            return value if isinstance(value, dict) else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        finally:
            os.close(fd)

    @staticmethod
    def _publish(directory_fd: int, name: str, record: Mapping[str, Any]) -> None:
        payload = (
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        temporary = f".stage-{os.getpid()}-{secrets.token_hex(12)}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError(errno.EIO, "short write to control record")
                view = view[written:]
            os.fsync(fd)
            os.link(
                temporary,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
            os.fsync(directory_fd)
        finally:
            os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory_fd)

    def _request_dir(self) -> int:
        return self._open_dir(self._root_fd, _REQUESTS_DIR)

    def _queue_dir(self) -> int:
        return self._open_dir(self._root_fd, _QUEUE_DIR)

    def register_request(
        self, identity: Mapping[str, Any], request: Mapping[str, Any]
    ) -> None:
        """Publish a new pending request; duplicates are refused, never replaced."""
        key = identity_key(identity)
        if not isinstance(request, Mapping):
            raise UIControlError("request must be an object")
        root_fd = self._request_dir()
        try:
            self._publish(
                root_fd,
                self._record_name(key),
                {"identity": key, "state": "pending", "request": dict(request)},
            )
        except FileExistsError as exc:
            raise UIControlError("request identity is already registered") from exc
        finally:
            os.close(root_fd)

    def _load_request(self, key: Mapping[str, str]) -> dict[str, Any]:
        directory_fd = self._request_dir()
        try:
            record = self._read_record(directory_fd, self._record_name(key))
        finally:
            os.close(directory_fd)
        if (
            record is None
            or record.get("identity") != dict(key)
            or record.get("state") != "pending"
        ):
            raise UIControlError("unknown or stale UI request identity")
        return record

    @staticmethod
    def _validate_answer(
        kind: str, value: Any, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        method = request.get("method")
        if method is None:
            method = request.get("kind", request.get("type"))
        if kind == "text" and method == "ask" and isinstance(value, (str, list, tuple)):
            questions = request.get("questions")
            if not isinstance(questions, (list, tuple)) or not questions:
                raise UIControlError("text answer requires observed ask questions")
            values = [value] if isinstance(value, str) else list(value)
            if len(values) != len(questions) or not all(
                isinstance(item, str) for item in values
            ):
                raise UIControlError(
                    "provide one text answer for each observed ask question"
                )
            answers = []
            for question, text in zip(questions, values, strict=True):
                if not isinstance(question, Mapping) or not isinstance(
                    question.get("id"), str
                ):
                    raise UIControlError("ask question has no valid identity")
                answers.append(
                    {
                        "id": question["id"],
                        "selectedOptions": [],
                        "customInput": text,
                    }
                )
            return {"type": "ask", "answers": answers}
        if kind == "select" and method == "select" and isinstance(value, str):
            options = request.get("options")
            valid = isinstance(options, (list, tuple)) and any(
                value == _raw_option_value(option) for option in options
            )
            if not valid:
                raise UIControlError(
                    "selection is not one of the request's option identities"
                )
            return {"type": "select", "value": value}
        if kind == "confirm" and method == "confirm" and type(value) is bool:
            return {"type": "confirm", "confirmed": value}
        raise UIControlError(
            "answer does not match the observed ask, select or confirm request"
        )

    def answer(
        self, identity: Mapping[str, Any], kind: str, value: Any
    ) -> dict[str, Any]:
        """Queue an answer only for an exact currently-pending identity."""
        key = identity_key(identity)
        request_record = self._load_request(key)
        request = request_record["request"]
        if kind == "select" and isinstance(value, str):
            value = _selection_value(request.get("options"), value)
        answer = self._validate_answer(kind, value, request)
        command = {"action": "answer", "identity": key, "answer": answer}
        self._enqueue(command)
        return command

    def abort(self, job: str, session: str) -> dict[str, Any]:
        """Queue an abort only for a session with an observed pending request."""
        key = _session_key(job, session)
        pending = [
            item
            for item in self.list_pending(job=job)
            if item["identity"].get("session_generation") == session
        ]
        if not pending:
            raise UIControlError("abort requires an observed pending session identity")
        command = {"action": "abort", "identity": key}
        self._enqueue(command)
        return command

    def _enqueue(self, command: Mapping[str, Any]) -> str:
        queue_fd = self._queue_dir()
        try:
            for _ in range(8):
                name = f"{secrets.token_hex(16)}.json"
                try:
                    self._publish(queue_fd, name, command)
                    return name
                except FileExistsError:
                    continue
            raise UIControlError("unable to allocate a unique control queue record")
        finally:
            os.close(queue_fd)

    def list_pending(self, *, job: str | None = None) -> list[dict[str, Any]]:
        """Return pending request metadata only; request text is never exposed."""
        if job is not None and (
            not isinstance(job, str) or _JOB_ID_RE.fullmatch(job) is None
        ):
            raise UIControlError("job must be a valid job id")
        directory_fd = self._request_dir()
        try:
            names = sorted(os.listdir(directory_fd))
            records = [
                self._read_record(directory_fd, name)
                for name in names
                if name.endswith(".json")
            ]
        finally:
            os.close(directory_fd)
        result = []
        for record in records:
            if (
                not isinstance(record, dict)
                or record.get("state") != "pending"
                or not isinstance(record.get("identity"), dict)
            ):
                continue
            identity = record["identity"]
            if set(identity) != set(_ID_FIELDS) or (
                job is not None and identity.get("job_id") != job
            ):
                continue
            raw_request = record.get("request")
            request: dict[str, Any] = (
                raw_request if isinstance(raw_request, dict) else {}
            )
            method = request.get(
                "method", request.get("kind", request.get("type", "unknown"))
            )
            metadata: dict[str, Any] = {
                "identity": dict(identity),
                "kind": str(method),
                "prompt": _redacted_prompt(_request_prompt(request)),
            }
            if method == "ask" and isinstance(request.get("questions"), (list, tuple)):
                metadata["questions"] = _question_summaries(request["questions"])
            else:
                metadata["options"] = _option_summaries(request.get("options"))
            result.append(metadata)
        return result

    def status(self, *, job: str | None = None) -> dict[str, Any]:
        """Safe read-only status and pending queue summary for operator/driver."""
        pending = self.list_pending(job=job)
        queue_fd = self._queue_dir()
        try:
            self._discard_terminal_stale_answers(queue_fd, job_id=job)
            queued = 0
            for name in os.listdir(queue_fd):
                if not name.endswith(".json"):
                    continue
                command = self._read_record(queue_fd, name)
                identity = (
                    command.get("identity") if isinstance(command, dict) else None
                )
                if job is None or (
                    isinstance(identity, dict)
                    and identity.get("job_id", identity.get("job")) == job
                ):
                    queued += 1
        finally:
            os.close(queue_fd)
        return {
            "pending": pending,
            "pending_count": len(pending),
            "queued_commands": queued,
        }

    def _discard_terminal_stale_answers(
        self,
        queue_fd: int,
        *,
        job_id: str | None = None,
        session_generation: str | None = None,
    ) -> None:
        request_fd = self._request_dir()
        try:
            for name in os.listdir(queue_fd):
                if not name.endswith(".json"):
                    continue
                command = self._read_record(queue_fd, name)
                identity_value = (
                    command.get("identity") if isinstance(command, dict) else None
                )
                if (
                    not isinstance(command, dict)
                    or command.get("action") != "answer"
                    or not isinstance(identity_value, dict)
                ):
                    continue
                if job_id is not None and identity_value.get("job_id") != job_id:
                    continue
                if (
                    session_generation is not None
                    and identity_value.get("session_generation") != session_generation
                ):
                    continue
                try:
                    identity = identity_key(identity_value)
                except UIControlError:
                    continue
                request = self._read_record(request_fd, self._record_name(identity))
                if (
                    isinstance(request, dict)
                    and request.get("identity") == identity
                    and request.get("state")
                    in {
                        "answered",
                        "aborted",
                        "rejected_stale",
                        "send_failed",
                        "invalidated",
                    }
                ):
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(name, dir_fd=queue_fd)
            os.fsync(queue_fd)
        finally:
            os.close(request_fd)

    def next_command(
        self,
        *,
        job_id: str | None = None,
        session_generation: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the next still-valid command matching optional driver identity."""
        if session_generation is not None and job_id is None:
            raise UIControlError("session filter requires a job id")
        queue_fd = self._queue_dir()
        try:
            self._discard_terminal_stale_answers(
                queue_fd, job_id=job_id, session_generation=session_generation
            )
            names = sorted(
                item for item in os.listdir(queue_fd) if item.endswith(".json")
            )
            records = [(name, self._read_record(queue_fd, name)) for name in names]
            # Queue names are random, so order would otherwise be arbitrary:
            # an accepted abort is always handed out before any answer.
            records.sort(
                key=lambda item: (
                    not (isinstance(item[1], dict) and item[1].get("action") == "abort")
                )
            )
            for name, command in records:
                if not isinstance(command, dict):
                    continue
                action, identity = command.get("action"), command.get("identity")
                if job_id is not None and (
                    not isinstance(identity, dict)
                    or identity.get("job_id", identity.get("job")) != job_id
                ):
                    continue
                if session_generation is not None and (
                    not isinstance(identity, dict)
                    or identity.get("session_generation", identity.get("session"))
                    != session_generation
                ):
                    continue
                try:
                    if action == "answer":
                        if set(command) != {"action", "identity", "answer"}:
                            continue
                        if not isinstance(identity, dict):
                            continue
                        key = identity_key(identity)
                        request_record = self._load_request(key)
                        request_value = request_record.get("request")
                        if not isinstance(request_value, dict):
                            continue
                        raw_answer = command.get("answer")
                        if not isinstance(raw_answer, dict):
                            continue
                        answer_type = raw_answer.get("type")
                        if answer_type == "select":
                            validated = self._validate_answer(
                                "select", raw_answer.get("value"), request_value
                            )
                        elif answer_type == "confirm":
                            validated = self._validate_answer(
                                "confirm", raw_answer.get("confirmed"), request_value
                            )
                        elif answer_type == "ask":
                            answers = raw_answer.get("answers")
                            if not isinstance(answers, list) or any(
                                not isinstance(item, dict)
                                or item.get("selectedOptions") != []
                                for item in answers
                            ):
                                continue
                            validated = self._validate_answer(
                                "text",
                                [item.get("customInput") for item in answers],
                                request_value,
                            )
                        else:
                            continue
                        if validated != raw_answer:
                            continue
                    elif (
                        action == "abort"
                        and set(command) == {"action", "identity"}
                        and isinstance(identity, dict)
                        and isinstance(identity.get("job_id"), str)
                        and isinstance(identity.get("session_generation"), str)
                        and set(identity) == {"job_id", "session_generation"}
                    ):
                        session = _session_key(
                            identity["job_id"], identity["session_generation"]
                        )
                        # abort() already required a pending request when it
                        # accepted the command. Answering that request later
                        # must not void the abort, so only require that the
                        # session was observed (a request record in any state).
                        if not self._session_observed(
                            session["job_id"], session["session_generation"]
                        ):
                            continue
                    else:
                        continue
                except UIControlError:
                    continue
                return {"queue_id": name, **command}
            return None
        finally:
            os.close(queue_fd)

    def _session_observed(self, job: str, session: str) -> bool:
        directory_fd = self._request_dir()
        try:
            for name in os.listdir(directory_fd):
                if not name.endswith(".json"):
                    continue
                record = self._read_record(directory_fd, name)
                identity = record.get("identity") if isinstance(record, dict) else None
                if (
                    isinstance(identity, dict)
                    and identity.get("job_id") == job
                    and identity.get("session_generation") == session
                ):
                    return True
            return False
        finally:
            os.close(directory_fd)

    def acknowledge_command(
        self,
        queue_id: str,
        *,
        job_id: str,
        session_generation: str,
    ) -> None:
        """Remove a queue item only after confirming its exact driver session."""
        if (
            not isinstance(queue_id, str)
            or re.fullmatch(r"[a-f0-9]{32}\.json", queue_id) is None
        ):
            raise UIControlError("invalid queue record id")
        scope = _session_key(job_id, session_generation)
        queue_fd = self._queue_dir()
        try:
            command = self._read_record(queue_fd, queue_id)
            identity = command.get("identity") if isinstance(command, dict) else None
            if (
                not isinstance(identity, dict)
                or identity.get("job_id") != scope["job_id"]
                or identity.get("session_generation") != scope["session_generation"]
            ):
                raise UIControlError(
                    "queue item does not belong to this driver session"
                )
            os.unlink(queue_id, dir_fd=queue_fd)
            os.fsync(queue_fd)
        finally:
            os.close(queue_fd)

    def resolve_request(self, identity: Mapping[str, Any], resolution: str) -> None:
        """Close a pending request after the driver resolves or invalidates it."""
        key = identity_key(identity)
        if resolution not in {
            "answered",
            "aborted",
            "rejected_stale",
            "send_failed",
            "invalidated",
        }:
            raise UIControlError("unknown UI request resolution")
        request_fd = self._request_dir()
        name = self._record_name(key)
        try:
            record = self._read_record(request_fd, name)
            if (
                record is None
                or record.get("identity") != key
                or record.get("state") != "pending"
            ):
                raise UIControlError("unknown or stale UI request identity")
            record["state"] = resolution
            self._replace_record(request_fd, name, record)
        finally:
            os.close(request_fd)

    @staticmethod
    def _replace_record(
        directory_fd: int, name: str, record: Mapping[str, Any]
    ) -> None:
        """Atomically replace a state record after validating the current inode."""
        current = UIControlStore._read_record(directory_fd, name)
        if current is None:
            raise UIControlError("request disappeared during resolution")
        payload = (
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        temporary = f".stage-{os.getpid()}-{secrets.token_hex(12)}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError(errno.EIO, "short write to control record")
                view = view[written:]
            os.fsync(fd)
            os.replace(
                temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd
            )
            os.fsync(directory_fd)
        finally:
            os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory_fd)


def _redacted_prompt(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = redact_text(value)
    text = re.sub(
        r"(?i)(password|passwd|credential)(\s*[:=]\s*)([\"']?)[^\s\"',}]+",
        lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)}[REDACTED]",
        text,
    )
    return " ".join(text.split())[:160]


def _request_prompt(request: Mapping[str, Any]) -> str:
    for key in ("prompt", "question", "message", "title"):
        value = request.get(key)
        if isinstance(value, str):
            return value
    questions = request.get("questions")
    if isinstance(questions, (list, tuple)):
        for question in questions:
            if isinstance(question, Mapping) and isinstance(
                question.get("question"), str
            ):
                return question["question"]
    return ""


def _raw_option_value(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, Mapping):
        value = item.get("id", item.get("value"))
        return value if isinstance(value, str) else None
    return None


def _choice_keys(options: list[Any] | tuple[Any, ...]) -> list[str]:
    values = {_raw_option_value(option) for option in options}
    keys: list[str] = []
    used: set[str] = set()
    for index in range(len(options)):
        key = f"choice-{index + 1}"
        while key in values or key in used:
            key += "-key"
        keys.append(key)
        used.add(key)
    return keys


def _option_summaries(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        return []
    keys = _choice_keys(value)
    return [
        {
            "key": keys[index],
            "label": _redacted_prompt(
                item
                if isinstance(item, str)
                else (
                    item.get("label", item.get("id", ""))
                    if isinstance(item, Mapping)
                    else ""
                )
            )[:128],
        }
        for index, item in enumerate(value)
    ]


def _selection_value(options: Any, choice: str) -> str:
    if isinstance(options, (list, tuple)):
        for key, option in zip(_choice_keys(options), options, strict=True):
            if choice == key:
                value = _raw_option_value(option)
                if value is not None:
                    return value
    return choice


def _question_summaries(questions: Any) -> list[dict[str, Any]]:
    result = []
    for question in questions:
        if not isinstance(question, Mapping):
            continue
        question_id = question.get("id")
        text = question.get("question")
        result.append(
            {
                "id": question_id if isinstance(question_id, str) else "",
                "question": _redacted_prompt(text),
                "options": _option_summaries(question.get("options")),
            }
        )
    return result


def _option_identities(value: Any) -> list[str]:
    return [item["label"] for item in _option_summaries(value) if item["label"]]
