# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Driver-mediated local IPC for live clarification requests."""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import socket
import stat
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

CLARIFY_SOCKET_ENV = "OMEGA_PRIME_CLARIFY_SOCKET"
_CLIENT_REQUEST_ID = re.compile(r"[0-9a-fA-F]{32}\Z")
_REQUEST_FIELDS = {"type", "request_id", "question"}


class ClarifySocketError(RuntimeError):
    """A safe, payload-free error communicating with the clarify broker."""


class _RequestCompletion:
    """Thread-safe, one-shot completion shared by the host and socket handler."""

    __slots__ = (
        "_answer",
        "_done",
        "_lock",
        "_rejected",
        "client_request_id",
        "question",
    )

    def __init__(self, client_request_id: str, question: str) -> None:
        self.client_request_id = client_request_id
        self.question = question
        self._answer: str | None = None
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._rejected = False

    def complete(self, answer: str | None, *, rejected: bool) -> None:
        with self._lock:
            if self._done.is_set():
                raise RuntimeError("clarify request already completed")
            self._answer = answer
            self._rejected = rejected
            self._done.set()

    def reject_on_close(self) -> None:
        with self._lock:
            if not self._done.is_set():
                self._rejected = True
                self._done.set()

    def wait(self) -> tuple[str | None, bool]:
        self._done.wait()
        with self._lock:
            return self._answer, self._rejected


@dataclass(frozen=True, slots=True)
class ClarifyRequest:
    """An immutable host-side request with one-shot completion methods."""

    request_id: str
    question: str
    _completion: _RequestCompletion = field(repr=False, compare=False)

    def answer(self, text: str) -> None:
        """Complete this request with an answer for its waiting client."""
        if not isinstance(text, str):
            raise TypeError("clarify answer must be a string")
        self._completion.complete(text, rejected=False)

    def reject(self) -> None:
        """Reject this request with a generic error for its waiting client."""
        self._completion.complete(None, rejected=True)


class ClarifySocketBroker:
    """Relay MCP-child clarification requests to a polling driver event loop."""

    def __init__(self, socket_path: str | Path) -> None:
        path = os.fspath(socket_path)
        if not path:
            raise ValueError("clarify socket path must not be empty")
        self._socket_path = path
        self._lock = threading.RLock()
        self._close_lock = threading.Lock()
        self._requests: queue.Queue[ClarifyRequest] = queue.Queue()
        self._active: dict[socket.socket, _RequestCompletion | None] = {}
        self._pending: set[_RequestCompletion] = set()
        self._handlers: set[threading.Thread] = set()
        self._server: socket.socket | None = None
        self._server_thread: threading.Thread | None = None
        self._socket_inode: tuple[int, int] | None = None
        self._started = False
        self._closed = False
        self._fully_closed = False

    @property
    def socket_path(self) -> str:
        """The stable filesystem path clients use to connect to this broker."""
        return self._socket_path

    def start(self) -> ClarifySocketBroker:
        """Bind a private filesystem socket and start accepting child clients."""
        with self._lock:
            if self._started:
                raise RuntimeError("clarify socket broker already started")
            if self._closed:
                raise RuntimeError("clarify socket broker is closed")
            if os.path.lexists(self._socket_path):
                raise FileExistsError("clarify socket path already exists")

            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                server.bind(self._socket_path)
                socket_stat = os.lstat(self._socket_path)
                if not stat.S_ISSOCK(socket_stat.st_mode):
                    raise OSError("clarify broker did not create a socket")
                self._socket_inode = (socket_stat.st_dev, socket_stat.st_ino)
                os.chmod(self._socket_path, 0o600, follow_symlinks=False)
                server.listen()
                # This only bounds shutdown latency in accept(); request handlers
                # deliberately have no timeout while waiting for host input.
                server.settimeout(0.2)
            except BaseException:
                server.close()
                self._remove_owned_socket()
                raise

            self._server = server
            self._started = True
            self._server_thread = threading.Thread(
                target=self._accept_clients,
                args=(server,),
                name="clarify-socket-accept",
                daemon=True,
            )
            try:
                self._server_thread.start()
            except BaseException:
                self._closed = True
                server.close()
                self._remove_owned_socket()
                raise
        return self

    def next_request(self) -> ClarifyRequest | None:
        """Return the next queued request without blocking, or None if empty."""
        with self._lock:
            if self._closed:
                return None
            try:
                return self._requests.get_nowait()
            except queue.Empty:
                return None

    def close(self) -> None:
        """Release every waiting client, stop serving, and remove our socket."""
        with self._close_lock:
            with self._lock:
                if self._fully_closed:
                    return
                self._closed = True
                server = self._server
                server_thread = self._server_thread
                active = tuple(self._active.items())
                pending = tuple(self._pending)
                while True:
                    try:
                        self._requests.get_nowait()
                    except queue.Empty:
                        break

            for completion in pending:
                completion.reject_on_close()
            for connection, active_completion in active:
                if active_completion is None:
                    with contextlib.suppress(OSError):
                        connection.shutdown(socket.SHUT_RDWR)

            if server is not None:
                with contextlib.suppress(OSError):
                    server.shutdown(socket.SHUT_RDWR)
                server.close()
            if (
                server_thread is not None
                and server_thread is not threading.current_thread()
            ):
                server_thread.join()

            with self._lock:
                handlers = tuple(self._handlers)
            for handler in handlers:
                if handler is not threading.current_thread():
                    handler.join()

            self._remove_owned_socket()
            with self._lock:
                self._server = None
                self._server_thread = None
                self._fully_closed = True

    def _accept_clients(self, server: socket.socket) -> None:
        while True:
            try:
                connection, _ = server.accept()
            except TimeoutError:
                with self._lock:
                    if self._closed:
                        return
                continue
            except OSError:
                return

            with self._lock:
                if self._closed:
                    connection.close()
                    return
                self._active[connection] = None
                handler = threading.Thread(
                    target=self._serve_client,
                    args=(connection,),
                    name="clarify-socket-client",
                    daemon=True,
                )
                self._handlers.add(handler)
                try:
                    handler.start()
                except BaseException:
                    self._handlers.discard(handler)
                    self._active.pop(connection, None)
                    connection.close()
                    raise

    def _serve_client(self, connection: socket.socket) -> None:
        completion: _RequestCompletion | None = None
        try:
            try:
                with connection.makefile("rb") as stream:
                    line = stream.readline()
            except (OSError, ValueError):
                return

            request, client_request_id = self._decode_request(line)
            if request is None:
                self._send_error(connection, client_request_id)
                return

            completion = _RequestCompletion(
                client_request_id=request["request_id"],
                question=request["question"],
            )
            with self._lock:
                if self._closed:
                    should_reject = True
                else:
                    should_reject = False
                    self._active[connection] = completion
                    self._pending.add(completion)
                    self._requests.put(
                        ClarifyRequest(
                            request_id=uuid.uuid4().hex,
                            question=request["question"],
                            _completion=completion,
                        )
                    )

            if should_reject:
                self._send_error(connection, request["request_id"])
                return

            answer, rejected = completion.wait()
            if rejected:
                self._send_error(connection, request["request_id"])
            else:
                self._send_json(
                    connection,
                    {
                        "type": "answer",
                        "request_id": request["request_id"],
                        "answer": answer,
                    },
                )
        finally:
            with self._lock:
                if completion is not None:
                    self._pending.discard(completion)
                self._active.pop(connection, None)
                self._handlers.discard(threading.current_thread())
            with contextlib.suppress(OSError):
                connection.close()

    @staticmethod
    def _decode_request(
        line: bytes,
    ) -> tuple[dict[str, str] | None, str | None]:
        terminated = line.endswith(b"\n")
        raw = line[:-1] if terminated else line
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return None, None

        correlation_id = None
        if isinstance(payload, dict):
            candidate = payload.get("request_id")
            if isinstance(candidate, str) and _CLIENT_REQUEST_ID.fullmatch(candidate):
                correlation_id = candidate
        if (
            not terminated
            or not isinstance(payload, dict)
            or set(payload) != _REQUEST_FIELDS
            or payload.get("type") != "clarify"
            or not isinstance(payload.get("request_id"), str)
            or not _CLIENT_REQUEST_ID.fullmatch(payload["request_id"])
            or not isinstance(payload.get("question"), str)
        ):
            return None, correlation_id
        return payload, correlation_id

    @staticmethod
    def _send_error(connection: socket.socket, client_request_id: str | None) -> None:
        ClarifySocketBroker._send_json(
            connection,
            {
                "type": "error",
                "request_id": client_request_id,
                "error": "request rejected",
            },
        )

    @staticmethod
    def _send_json(connection: socket.socket, response: dict[str, object]) -> None:
        try:
            payload = json.dumps(
                response, ensure_ascii=True, separators=(",", ":")
            ).encode("ascii")
            connection.sendall(payload + b"\n")
        except (OSError, UnicodeError, ValueError):
            pass

    def _remove_owned_socket(self) -> None:
        identity = self._socket_inode
        if identity is None:
            return
        try:
            current = os.lstat(self._socket_path)
        except FileNotFoundError:
            return
        if (
            stat.S_ISSOCK(current.st_mode)
            and (current.st_dev, current.st_ino) == identity
        ):
            os.unlink(self._socket_path)
        self._socket_inode = None


class ClarifySocketClient:
    """Ask a live operator through a per-run UNIX-domain stream socket."""

    def __init__(self, socket_path: str | Path) -> None:
        path = str(socket_path)
        if not path:
            raise ValueError("clarify socket path must not be empty")
        self.socket_path = path

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ClarifySocketClient | None:
        """Build a client only when the per-run broker socket is configured."""
        socket_path = env.get(CLARIFY_SOCKET_ENV)
        if not socket_path:
            return None
        return cls(socket_path)

    def ask(self, question: str) -> str:
        """Send one correlated request and return one validated operator answer."""
        if not isinstance(question, str):
            raise ClarifySocketError("clarify question must be a string")

        request_id = uuid.uuid4().hex
        request = {
            "type": "clarify",
            "request_id": request_id,
            "question": question,
        }
        payload = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.connect(self.socket_path)
                connection.sendall((payload + "\n").encode("utf-8"))
                with connection.makefile("r", encoding="utf-8", newline="\n") as stream:
                    response_line = stream.readline()
        except (OSError, UnicodeError, ValueError):
            raise ClarifySocketError("clarify broker communication failed") from None

        if not response_line:
            raise ClarifySocketError("clarify broker closed without an answer")
        if not response_line.endswith("\n"):
            raise ClarifySocketError("clarify broker returned an unterminated response")
        try:
            response = json.loads(response_line)
        except (json.JSONDecodeError, UnicodeError):
            raise ClarifySocketError("clarify broker returned malformed JSON") from None
        if not isinstance(response, dict):
            raise ClarifySocketError("clarify broker returned an invalid response")
        if response.get("type") != "answer":
            raise ClarifySocketError("clarify broker returned an invalid response type")
        if response.get("request_id") != request_id:
            raise ClarifySocketError("clarify broker response correlation failed")
        answer = response.get("answer")
        if not isinstance(answer, str):
            raise ClarifySocketError("clarify broker returned an invalid answer")
        return answer


__all__ = [
    "CLARIFY_SOCKET_ENV",
    "ClarifyRequest",
    "ClarifySocketBroker",
    "ClarifySocketClient",
    "ClarifySocketError",
]
