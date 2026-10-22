"""Provider-free tests for the driver-mediated clarify socket seam."""

from __future__ import annotations

import contextlib
import json
import re
import socket
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from omega_prime.engine.clarify_ipc import (
    CLARIFY_SOCKET_ENV,
    ClarifySocketBroker,
    ClarifySocketClient,
    ClarifySocketError,
)
from omega_prime.engine.mcp_bridge import mcp_server_entry
from omega_prime.mcp_server import default_registry


def _start_broker(path: Path, reply) -> tuple[threading.Thread, list[dict]]:
    # The listener is created synchronously so the client cannot race its bind.
    requests: list[dict] = []
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)

    def serve() -> None:
        connection, _ = listener.accept()
        with listener, connection:
            received = bytearray()
            while b"\n" not in received:
                part = connection.recv(4096)
                if not part:
                    return
                received.extend(part)
            line, _, _ = received.partition(b"\n")
            requests.append(json.loads(line.decode("utf-8")))
            response = reply(requests[-1])
            if response is not None:
                connection.sendall(response)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread, requests


def _reply(request: dict, **changes: Any) -> bytes:
    response = {
        "type": "answer",
        "request_id": request["request_id"],
        "answer": "operator answer",
    }
    response.update(changes)
    return (json.dumps(response) + "\n").encode("utf-8")


def test_socket_client_round_trip_uses_correlated_newline_json(tmp_path: Path):
    socket_path = tmp_path / "clarify.sock"
    thread, requests = _start_broker(socket_path, _reply)

    answer = ClarifySocketClient(socket_path).ask("Which option? π")
    thread.join(timeout=2)

    assert answer == "operator answer"
    assert len(requests) == 1
    request = requests[0]
    assert set(request) == {"type", "request_id", "question"}
    assert request["type"] == "clarify"
    assert re.fullmatch(r"[0-9a-f]{32}", request["request_id"])
    assert request["question"] == "Which option? π"


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (lambda _request: b"not-json\n", "malformed JSON"),
        (lambda _request: b"[]\n", "invalid response"),
        (lambda request: _reply(request, type="unexpected"), "response type"),
        (lambda request: _reply(request, request_id="0" * 32), "correlation"),
        (lambda request: _reply(request, answer=None), "invalid answer"),
        (lambda _request: None, "closed without an answer"),
        (lambda request: _reply(request).rstrip(b"\n"), "unterminated response"),
    ],
)
def test_socket_client_fails_closed_on_bad_responses(
    tmp_path: Path, reply, message: str
):
    socket_path = tmp_path / "clarify.sock"
    thread, _ = _start_broker(socket_path, reply)

    with pytest.raises(ClarifySocketError, match=message):
        ClarifySocketClient(socket_path).ask("private question")
    thread.join(timeout=2)


def test_default_registry_clarify_uses_socket_from_run_environment(tmp_path: Path):
    socket_path = tmp_path / "clarify.sock"
    work_root = tmp_path / "work"
    work_root.mkdir()
    thread, requests = _start_broker(socket_path, _reply)
    registry = default_registry(
        tmp_path / "install",
        tmp_path / "home",
        env={CLARIFY_SOCKET_ENV: str(socket_path)},
        work_root=work_root,
    )
    tool_names = {schema["function"]["name"] for schema in registry.schemas()}
    assert {"clarify", "omega_command"} <= tool_names

    result = json.loads(registry.dispatch("clarify", {"question": "Need operator"}))
    thread.join(timeout=2)

    assert result == {"answer": "operator answer"}
    assert requests[0]["question"] == "Need operator"
    assert requests[0]["type"] == "clarify"


def test_default_registry_without_socket_reports_undeliverable(tmp_path: Path):
    work_root = tmp_path / "work"
    work_root.mkdir()
    registry = default_registry(
        tmp_path / "install",
        tmp_path / "home",
        env={},
        work_root=work_root,
    )

    result = json.loads(registry.dispatch("clarify", {"question": "Need operator"}))

    assert result == {
        "error": "undeliverable: no clarify interaction is configured",
    }


def test_mcp_server_entry_carries_only_clarify_socket_path(tmp_path: Path):
    socket_path = tmp_path / "clarify.sock"

    entry = mcp_server_entry(
        python=sys.executable,
        work_root=tmp_path,
        clarify_socket_path=socket_path,
    )

    assert entry["env"] == {CLARIFY_SOCKET_ENV: str(socket_path)}
    assert "token" not in json.dumps(entry).lower()
    assert "secret" not in json.dumps(entry).lower()


def test_from_env_is_absent_without_socket_configuration():
    assert ClarifySocketClient.from_env({}) is None


def _poll_request(broker: ClarifySocketBroker):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        request = broker.next_request()
        if request is not None:
            return request
        time.sleep(0.001)
    raise AssertionError("broker did not receive a request")


def _send_raw_request(path: str | Path, payload: bytes) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(str(path))
        connection.sendall(payload)
        with connection.makefile("rb") as stream:
            line = stream.readline()
    assert line.endswith(b"\n")
    return json.loads(line)


def _raw_request_thread(path: Path, request_id: str, question: str, results: list):
    payload = (
        json.dumps(
            {"type": "clarify", "request_id": request_id, "question": question}
        ).encode("utf-8")
        + b"\n"
    )
    try:
        results.append(_send_raw_request(path, payload))
    except Exception as error:  # surfaced in the owning test thread
        results.append(error)


def test_socket_broker_answers_with_client_correlation_and_host_request(tmp_path: Path):
    socket_path = tmp_path / "clarify.sock"
    broker = ClarifySocketBroker(socket_path).start()
    assert broker.next_request() is None
    outcomes: list[str] = []
    client_thread = threading.Thread(
        target=lambda: outcomes.append(
            ClarifySocketClient(broker.socket_path).ask("Which option? π")
        )
    )
    try:
        client_thread.start()
        request = _poll_request(broker)

        assert request.question == "Which option? π"
        assert re.fullmatch(r"[0-9a-f]{32}", request.request_id)
        request.answer("choose π")
        client_thread.join(timeout=2)

        assert not client_thread.is_alive()
        assert outcomes == ["choose π"]
    finally:
        broker.close()
    assert not socket_path.exists()


@pytest.mark.parametrize(
    ("payload", "correlation_id"),
    [
        (
            b'{"type":"clarify","request_id":"'
            b'0123456789abcdef0123456789abcdef","question":9}\n',
            "0123456789abcdef0123456789abcdef",
        ),
        (
            b'{"type":"clarify","request_id":"'
            b'0123456789abcdef0123456789abcdef","question":"secret","extra":true}\n',
            "0123456789abcdef0123456789abcdef",
        ),
        (b"{not-json}\n", None),
    ],
)
def test_socket_broker_rejects_malformed_request_without_echoing_payload(
    tmp_path: Path, payload: bytes, correlation_id: str | None
):
    broker = ClarifySocketBroker(tmp_path / "clarify.sock").start()
    try:
        response = _send_raw_request(broker.socket_path, payload)

        assert response == {
            "type": "error",
            "request_id": correlation_id,
            "error": "request rejected",
        }
        assert "secret" not in json.dumps(response)
    finally:
        broker.close()


def test_socket_broker_assigns_distinct_host_ids_for_duplicate_client_ids(
    tmp_path: Path,
):
    broker = ClarifySocketBroker(tmp_path / "clarify.sock").start()
    client_id = "0123456789abcdef0123456789abcdef"
    responses: list[dict | Exception] = []
    clients = [
        threading.Thread(
            target=_raw_request_thread,
            args=(Path(broker.socket_path), client_id, question, responses),
        )
        for question in ("first question", "second question")
    ]
    try:
        for client in clients:
            client.start()
        requests = [_poll_request(broker), _poll_request(broker)]

        assert {request.question for request in requests} == {
            "first question",
            "second question",
        }
        assert len({request.request_id for request in requests}) == 2
        assert all(request.request_id != client_id for request in requests)
        for request in requests:
            request.answer(f"answer to {request.question}")
        for client in clients:
            client.join(timeout=2)

        assert all(not client.is_alive() for client in clients)
        assert sorted(
            response["answer"] for response in responses if isinstance(response, dict)
        ) == ["answer to first question", "answer to second question"]
        assert all(
            response["request_id"] == client_id
            for response in responses
            if isinstance(response, dict)
        )
        assert not any(isinstance(response, Exception) for response in responses)
    finally:
        broker.close()


def test_socket_broker_close_releases_every_pending_client(tmp_path: Path):
    broker = ClarifySocketBroker(tmp_path / "clarify.sock").start()
    client_id = "abcdef0123456789abcdef0123456789"
    responses: list[dict | Exception] = []
    clients = [
        threading.Thread(
            target=_raw_request_thread,
            args=(Path(broker.socket_path), client_id, f"question {i}", responses),
        )
        for i in range(2)
    ]
    for client in clients:
        client.start()
    try:
        _requests = [_poll_request(broker), _poll_request(broker)]
        broker.close()
        for client in clients:
            client.join(timeout=2)

        assert all(not client.is_alive() for client in clients)
        assert len(responses) == 2
        assert all(
            response
            == {
                "type": "error",
                "request_id": client_id,
                "error": "request rejected",
            }
            or isinstance(response, (OSError, EOFError, AssertionError))
            for response in responses
        )
        with broker._lock:
            assert not broker._active
            assert not broker._handlers

    finally:
        broker.close()


def test_socket_broker_close_interrupts_blocked_answer_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    broker = ClarifySocketBroker(tmp_path / "clarify.sock").start()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    client.connect(broker.socket_path)
    request_id = "abcdef0123456789abcdef0123456789"
    client.sendall(
        json.dumps(
            {"type": "clarify", "request_id": request_id, "question": "question"}
        ).encode()
        + b"\n"
    )
    send_started = threading.Event()
    original_send_json = broker._send_json

    def send_large_answer(connection: socket.socket, response: dict[str, object]):
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        send_started.set()
        original_send_json(
            connection,
            {**response, "answer": "x" * (8 * 1024 * 1024)},
        )

    monkeypatch.setattr(broker, "_send_json", send_large_answer)
    close_thread = threading.Thread(target=broker.close)
    try:
        request = _poll_request(broker)
        request.answer("answer")
        assert send_started.wait(timeout=2)
        close_thread.start()
        close_thread.join(timeout=2)

        assert not close_thread.is_alive()
        with broker._lock:
            assert not broker._active
            assert not broker._handlers
    finally:
        if close_thread.is_alive():
            with broker._lock:
                active = tuple(broker._active)
            for connection in active:
                with contextlib.suppress(OSError):
                    connection.shutdown(socket.SHUT_RDWR)
            close_thread.join(timeout=2)
        client.close()
        broker.close()


def test_socket_broker_close_unblocks_client_with_incomplete_request(
    tmp_path: Path,
):
    broker = ClarifySocketBroker(tmp_path / "clarify.sock").start()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(broker.socket_path)
    client.sendall(b'{"type":"clarify"')
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with broker._lock:
                if broker._active:
                    break
            time.sleep(0.001)
        else:
            raise AssertionError("broker did not accept incomplete client")

        broker.close()

        assert client.recv(1) == b""
        with broker._lock:
            assert not broker._active
            assert not broker._handlers
    finally:
        client.close()
        broker.close()


def test_socket_broker_reject_unblocks_client_and_completes_once(tmp_path: Path):
    broker = ClarifySocketBroker(tmp_path / "clarify.sock").start()
    client_id = "fedcba9876543210fedcba9876543210"
    responses: list[dict | Exception] = []
    client = threading.Thread(
        target=_raw_request_thread,
        args=(Path(broker.socket_path), client_id, "question", responses),
    )
    try:
        client.start()
        request = _poll_request(broker)
        request.reject()
        with pytest.raises(RuntimeError, match="already completed"):
            request.answer("late answer")
        with pytest.raises(RuntimeError, match="already completed"):
            request.reject()
        client.join(timeout=2)

        assert not client.is_alive()
        assert responses == [
            {
                "type": "error",
                "request_id": client_id,
                "error": "request rejected",
            }
        ]
    finally:
        broker.close()


def test_socket_broker_uses_private_mode_and_removes_only_its_socket_inode(
    tmp_path: Path,
):
    socket_path = tmp_path / "clarify.sock"
    broker = ClarifySocketBroker(socket_path).start()
    created_inode = socket_path.stat().st_ino
    assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600

    socket_path.unlink()
    replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    replacement.bind(str(socket_path))
    replacement_inode = socket_path.stat().st_ino
    try:
        assert replacement_inode != created_inode
        broker.close()
        assert socket_path.exists()
        assert socket_path.stat().st_ino == replacement_inode
    finally:
        replacement.close()
        socket_path.unlink(missing_ok=True)


def test_socket_broker_preserves_preexisting_socket(tmp_path: Path):
    socket_path = tmp_path / "clarify.sock"
    existing = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    existing.bind(str(socket_path))
    existing.listen(1)
    existing_inode = socket_path.stat().st_ino
    broker = ClarifySocketBroker(socket_path)
    try:
        with pytest.raises(OSError):
            broker.start()
        assert socket_path.stat().st_ino == existing_inode

        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(str(socket_path))
        finally:
            probe.close()
    finally:
        broker.close()
        existing.close()
        socket_path.unlink(missing_ok=True)
