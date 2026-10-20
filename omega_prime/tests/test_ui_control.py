# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Focused contracts for the operator-local UI control boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omega_prime.engine.cli import main
from omega_prime.engine.ui_control import UIControlError, UIControlStore
from omega_prime.engine.watch import format_event

IDENTITY = {
    "job_id": "job-7",
    "session_generation": "session-opaque-a",
    "turn_generation": "turn-opaque-b",
    "request_id": "rpc-request-91",
}


def _store(tmp_path: Path, *, binds: tuple[Path, ...] = ()) -> UIControlStore:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    return UIControlStore(state, writable_binds=binds)


def test_answer_queue_preserves_full_identity_and_selection(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        store.register_request(
            IDENTITY,
            {"method": "select", "title": "Pick a route", "options": ("fast", "safe")},
        )
        result = store.answer(IDENTITY, "select", "safe")
        queued = store.next_command()

    assert result["identity"] == IDENTITY
    assert queued is not None
    assert queued["identity"] == IDENTITY
    assert queued["answer"] == {"type": "select", "value": "safe"}


def test_text_answers_retain_order_for_every_ask_question(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        store.register_request(
            IDENTITY,
            {
                "method": "ask",
                "questions": (
                    {"id": "first", "question": "First?"},
                    {"id": "second", "question": "Second?"},
                ),
            },
        )
        command = store.answer(IDENTITY, "text", ["one", "two"])

    assert command["answer"] == {
        "type": "ask",
        "answers": [
            {"id": "first", "selectedOptions": [], "customInput": "one"},
            {"id": "second", "selectedOptions": [], "customInput": "two"},
        ],
    }


def test_confirm_false_is_preserved_as_boolean_false(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        store.register_request(IDENTITY, {"method": "confirm", "message": "Continue?"})
        command = store.answer(IDENTITY, "confirm", False)
        queued = store.next_command()

    assert command["answer"] == {"type": "confirm", "confirmed": False}
    assert queued is not None
    assert queued["answer"]["confirmed"] is False


def test_answer_rejects_incomplete_or_stale_identity_without_queueing(
    tmp_path: Path,
) -> None:
    with _store(tmp_path) as store:
        store.register_request(IDENTITY, {"method": "confirm", "message": "Continue?"})
        with pytest.raises(UIControlError):
            store.answer({"job_id": IDENTITY["job_id"]}, "confirm", True)
        stale = {**IDENTITY, "session_generation": "old-session"}
        with pytest.raises(UIControlError, match="unknown or stale"):
            store.answer(stale, "confirm", True)
        assert store.next_command() is None


def test_resolved_request_command_is_not_delivered_again(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        store.register_request(IDENTITY, {"method": "confirm", "message": "Continue?"})
        store.answer(IDENTITY, "confirm", True)
        store.resolve_request(IDENTITY, "answered")
        assert store.next_command() is None


def test_abort_requires_observed_exact_session(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        with pytest.raises(UIControlError, match="observed pending session"):
            store.abort(IDENTITY["job_id"], IDENTITY["session_generation"])
        store.register_request(IDENTITY, {"method": "confirm", "message": "Continue?"})
        command = store.abort(IDENTITY["job_id"], IDENTITY["session_generation"])
        assert command == {
            "action": "abort",
            "identity": {
                "job_id": IDENTITY["job_id"],
                "session_generation": IDENTITY["session_generation"],
            },
        }
        queued = store.next_command()
        assert queued is not None
        assert queued["action"] == "abort"
        assert queued["identity"] == command["identity"]


def test_store_creates_missing_state_root_for_fresh_ui_status(tmp_path: Path) -> None:
    state = tmp_path / "new-state"
    with UIControlStore(state, writable_binds=[]) as store:
        assert store.status() == {
            "pending": [],
            "pending_count": 0,
            "queued_commands": 0,
        }
    assert (state / "ui-control").is_dir()


def test_bind_overlap_is_rejected_before_creating_state_root(tmp_path: Path) -> None:
    state = tmp_path / "new-state"
    with pytest.raises(UIControlError, match="overlaps"):
        UIControlStore(state, writable_binds=[state])
    assert not state.exists()


def test_control_directory_rejects_symlinks_mode_violations_and_bind_overlap(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    with pytest.raises(UIControlError, match="parent traversal"):
        UIControlStore(state / "..", writable_binds=[])
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    (state / "ui-control").symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        UIControlStore(state, writable_binds=[])

    (state / "ui-control").unlink()
    with UIControlStore(state, writable_binds=[]) as store:
        control = store.path
    control.chmod(0o755)
    with pytest.raises(UIControlError, match="mode 0700"):
        UIControlStore(state, writable_binds=[])

    control.chmod(0o700)
    with pytest.raises(UIControlError, match="overlaps"):
        UIControlStore(state, writable_binds=[state])


def test_status_and_watch_show_redacted_prompt_and_identity_only(
    tmp_path: Path, capsys
) -> None:
    with _store(tmp_path) as store:
        store.register_request(
            IDENTITY,
            {
                "method": "ask",
                "questions": (
                    {
                        "id": "question-1",
                        "question": "Use token=top-secret-value?",
                        "options": ({"label": "Safe"}, {"label": "Risky"}),
                    },
                ),
            },
        )
        status = store.status(job=IDENTITY["job_id"])
    assert status["pending"][0]["options"] == ["Safe", "Risky"]

    rendered = json.dumps(status)
    assert IDENTITY["request_id"] in rendered
    assert "top-secret-value" not in rendered
    assert "token=[REDACTED]" in rendered
    event = format_event(
        {
            "type": "ui_pending",
            "identity": IDENTITY,
            "request": {"method": "ask", "question": "password=hidden-value?"},
        }
    )
    assert IDENTITY["turn_generation"] in event
    assert "hidden-value" not in event
    assert "password=[REDACTED]" in event
    assert capsys.readouterr().out == ""


def test_cli_confirm_false_queues_false_without_rewriting_identity(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(state))
    with UIControlStore(state, writable_binds=[]) as store:
        store.register_request(IDENTITY, {"method": "confirm", "message": "Continue?"})

    result = main(
        [
            "ui",
            "answer",
            "--job",
            IDENTITY["job_id"],
            "--session",
            IDENTITY["session_generation"],
            "--turn",
            IDENTITY["turn_generation"],
            "--request",
            IDENTITY["request_id"],
            "--confirm",
            "false",
        ]
    )
    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["identity"] == IDENTITY
    with UIControlStore(state, writable_binds=[]) as store:
        queued = store.next_command()
    assert queued is not None
    assert queued["answer"]["confirmed"] is False
