"""Runnable nightly, sweep, ultrathink, and desk-lead entry points."""

from __future__ import annotations

import json
from pathlib import Path

from omega_prime.routines.desk_lead import execute_desk_lead
from omega_prime.routines.desk_lead import main as desk_main
from omega_prime.routines.nightly import execute_nightly
from omega_prime.routines.nightly import main as nightly_main
from omega_prime.routines.sweep import execute_sweep
from omega_prime.routines.sweep import main as sweep_main
from omega_prime.routines.ultrathink_turn import execute_ultrathink
from omega_prime.routines.ultrathink_turn import main as plan_main
from omega_prime.tools.x import XClient


def test_nightly_cli_reviews_a_turns_file(tmp_path: Path) -> None:
    turns = tmp_path / "turns.json"
    turns.write_text(
        json.dumps(
            [
                {
                    "id": "t1",
                    "messages": [
                        {"role": "user", "content": "hello"},
                        {"role": "tool", "content": "ok", "is_error": False},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    code = nightly_main(["--state-dir", str(tmp_path / "state"), "--turns", str(turns)])
    assert code == 0
    state = json.loads(
        (tmp_path / "state" / "nightly.json").read_text(encoding="utf-8")
    )
    assert state["reviewed"] == ["t1"]


def test_nightly_without_env_does_not_touch_a_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = execute_nightly({})
    assert result["error"] == "not_configured"
    assert result["publishes"] is False
    assert not (tmp_path / "omega_prime").exists()


def test_sweep_from_a_file_queues_drafts_and_never_publishes(tmp_path: Path) -> None:
    mentions = tmp_path / "mentions.json"
    mentions.write_text(
        json.dumps({"posts": [{"id": "m1", "text": "hello there"}]}),
        encoding="utf-8",
    )
    result = execute_sweep(
        {},
        mentions_path=mentions,
        directory=tmp_path / "state",
        sweep_id="weekday",
    )
    assert result["publishes"] is False
    assert result["drafts"][0]["text"].startswith("[draft-only]")
    assert "error" not in result
    saved = json.loads(
        (tmp_path / "state" / "sweeps" / "weekday.json").read_text(encoding="utf-8")
    )
    assert saved["drafts"][0]["mention_id"] == "m1"


def test_sweep_refuses_a_client_that_can_publish(tmp_path: Path) -> None:
    client = XClient(transport=object(), token="not-used")
    result = execute_sweep({}, directory=tmp_path, client=client)
    assert result["error"] == "publish_refused"
    assert result["publishes"] is False
    assert result["drafts"] == []


def test_sweep_cli_without_token_does_not_publish(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("X_API_TOKEN", raising=False)
    monkeypatch.delenv("OMEGA_PRIME_SWEEP_MENTIONS", raising=False)
    code = sweep_main(["--state-dir", str(tmp_path)])
    assert code == 1


def test_ultrathink_cli_reads_an_explicit_directory(tmp_path: Path) -> None:
    plan = tmp_path / "last-plan.json"
    plan.write_text(
        json.dumps({"specPath": str(tmp_path / "missing.md")}), encoding="utf-8"
    )
    code = plan_main(["--state-dir", str(tmp_path)])
    assert code == 0
    empty = execute_ultrathink({})
    assert empty["found"] is False
    assert empty["publishes"] is False


def test_desk_lead_cli_on_an_empty_queue(tmp_path: Path) -> None:
    code = desk_main(["--state-dir", str(tmp_path), "--limit", "2"])
    assert code == 0
    blocked = execute_desk_lead({})
    assert blocked["error"] == "not_configured"
    assert blocked["publishes"] is False
