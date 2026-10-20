# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Focused contracts for the operator-local UI control boundary."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from omega_prime.engine.cli import main
from omega_prime.engine.ui_control import UIControlError, UIControlStore
from omega_prime.engine.watch import format_event, tail

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


def test_choice_keys_select_original_values_without_using_redacted_labels(
    tmp_path: Path,
) -> None:
    options = ("token=one-secret", "token=two-secret")
    with _store(tmp_path) as store:
        store.register_request(IDENTITY, {"method": "select", "options": options})
        pending = store.list_pending()[0]
        command = store.answer(IDENTITY, "select", "choice-2")

    assert pending["options"] == [
        {"key": "choice-1", "label": "token=[REDACTED]"},
        {"key": "choice-2", "label": "token=[REDACTED]"},
    ]
    assert command["answer"]["value"] == "token=two-secret"


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


def test_list_pending_keeps_each_ask_question_and_its_own_options(
    tmp_path: Path,
) -> None:
    with _store(tmp_path) as store:
        store.register_request(
            IDENTITY,
            {
                "method": "ask",
                "questions": (
                    {
                        "id": "first",
                        "question": "First token=first-secret?",
                        "options": ("red", "blue"),
                    },
                    {
                        "id": "second",
                        "question": "Second?",
                        "options": ("yes",),
                    },
                ),
            },
        )
        pending = store.list_pending()[0]

    assert [question["id"] for question in pending["questions"]] == [
        "first",
        "second",
    ]
    assert [question["options"] for question in pending["questions"]] == [
        [
            {"key": "choice-1", "label": "red"},
            {"key": "choice-2", "label": "blue"},
        ],
        [{"key": "choice-1", "label": "yes"}],
    ]
    assert "first-secret" not in json.dumps(pending)


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


def test_filtered_command_scan_preserves_unmatched_records(tmp_path: Path) -> None:
    other_identity = {**IDENTITY, "job_id": "job-other"}
    with _store(tmp_path) as store:
        store.register_request(IDENTITY, {"method": "confirm", "message": "First?"})
        store.register_request(
            other_identity, {"method": "confirm", "message": "Other?"}
        )
        store.answer(other_identity, "confirm", True)
        store.answer(IDENTITY, "confirm", False)

        selected = store.next_command(
            job_id=IDENTITY["job_id"],
            session_generation=IDENTITY["session_generation"],
        )
        assert selected is not None
        assert selected["identity"] == IDENTITY
        store.acknowledge_command(selected["queue_id"])
        unfiltered = store.next_command()

    assert unfiltered is not None
    assert unfiltered["identity"] == other_identity


def test_status_discards_terminal_duplicate_answers_after_concurrent_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    with _store(tmp_path) as store:
        store.register_request(IDENTITY, {"method": "confirm", "message": "Done?"})
        store.answer(IDENTITY, "confirm", True)
        store.answer(IDENTITY, "confirm", False)
        store.resolve_request(IDENTITY, "answered")

        original_unlink = os.unlink
        deleted_by_concurrent_scan = False

        def unlink_after_competing_scan(name, *, dir_fd):
            nonlocal deleted_by_concurrent_scan
            if deleted_by_concurrent_scan:
                original_unlink(name, dir_fd=dir_fd)
                raise FileNotFoundError(name)
            deleted_by_concurrent_scan = True
            return original_unlink(name, dir_fd=dir_fd)

        monkeypatch.setattr(
            "omega_prime.engine.ui_control.os.unlink", unlink_after_competing_scan
        )
        assert store.status()["queued_commands"] == 0
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


def test_store_creates_missing_nested_state_root_safely(tmp_path: Path) -> None:
    state = tmp_path / "missing-parent" / "nested" / "state"
    with UIControlStore(state, writable_binds=[]) as store:
        assert store.status()["pending_count"] == 0
    assert (state / "ui-control").is_dir()
    assert (state.parent.parent).stat().st_mode & 0o777 == 0o700


def test_nested_state_root_refuses_symlink_parent(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    target.chmod(0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)

    with pytest.raises(UIControlError, match="symlink"):
        UIControlStore(linked / "missing" / "state", writable_binds=[])
    assert not (target / "missing").exists()


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


def test_status_and_watch_show_redacted_question_local_metadata(
    tmp_path: Path, capsys
) -> None:
    request = {
        "method": "ask",
        "questions": (
            {
                "id": "question-1",
                "question": "Use token=top-secret-value?",
                "options": ("safe", "password=secret-value"),
            },
            {
                "id": "question-2",
                "question": "Continue?",
                "options": ("yes", "no"),
            },
        ),
    }
    with _store(tmp_path) as store:
        store.register_request(IDENTITY, request)
        status = store.status(job=IDENTITY["job_id"])
    questions = status["pending"][0]["questions"]
    assert [question["id"] for question in questions] == ["question-1", "question-2"]
    assert [option["label"] for option in questions[0]["options"]] == [
        "safe",
        "password=[REDACTED]",
    ]

    rendered = json.dumps(status)
    assert IDENTITY["request_id"] in rendered
    assert "top-secret-value" not in rendered
    assert "secret-value" not in rendered
    event = format_event(
        {"type": "ui_pending", "identity": IDENTITY, "request": request}
    )
    assert IDENTITY["turn_generation"] in event
    assert "question-2" in event
    assert "password=[REDACTED]" in event
    assert "top-secret-value" not in event
    assert "secret-value" not in event
    assert capsys.readouterr().out == ""


def test_watch_tail_renders_attachment_and_cleanup_details(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            json.dumps(event)
            for event in (
                {"type": "ui_control_attachment", "status": "attached"},
                {
                    "type": "mcp_attachment",
                    "status": "attached",
                    "cleanup_conflicts": ["cleanup failed: PermissionError"],
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    output = io.StringIO()

    assert tail(events_path, write=output) == 0
    rendered = output.getvalue()
    assert "ui_control_attachment status=attached" in rendered
    assert "mcp_attachment status=attached" in rendered
    assert "cleanup_conflicts=" in rendered


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
