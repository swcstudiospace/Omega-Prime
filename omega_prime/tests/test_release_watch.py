# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Release watcher digest tests: new-tags, clean, seed, failures (PLAN-G7)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from omega_prime.grokbot.release_watch import (
    build_digest,
    check_release_watch,
    make_checker,
    read_releases_block,
)

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
V12 = {"tag": "v1.2", "sha": "831a6b0", "date": "2026-09-01T00:00:00+00:00"}
V13 = {"tag": "v1.3", "sha": "9f2c1aa", "date": "2026-10-01T00:00:00+00:00"}


def _write_state(path, pairs):
    path.write_text(
        json.dumps(
            {
                "last_seen": [{"tag": t, "sha": s} for t, s in pairs],
                "checked_at": "2026-10-12T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_release_watch_new_tag(tmp_path):
    state = _write_state(tmp_path / "release-watch.json", [("v1.2", "831a6b0")])
    result = check_release_watch(state, lambda: [dict(V12), dict(V13)], NOW)
    assert result["seeded"] is False
    assert result["new"] == [dict(V13)]
    stored = json.loads(state.read_text(encoding="utf-8"))
    assert [(p["tag"], p["sha"]) for p in stored["last_seen"]] == [
        ("v1.2", "831a6b0"),
        ("v1.3", "9f2c1aa"),
    ]
    # Re-tick same input fires nothing (no re-fire of recorded tags).
    again = check_release_watch(state, lambda: [dict(V12), dict(V13)], NOW)
    assert again["new"] == []
    assert again["seeded"] is False


def test_release_watch_gh_tag_shape(tmp_path):
    """Real `gh api .../tags` entries (name/commit/sha) normalize."""
    state = _write_state(tmp_path / "release-watch.json", [("v1.2", "831a6b0")])
    upstream = [
        {"name": "v1.2", "commit": {"sha": "831a6b0"}},
        {"name": "v1.3", "commit": {"sha": "9f2c1aa"}},
    ]
    result = check_release_watch(state, lambda: upstream, NOW)
    assert [e["tag"] for e in result["new"]] == ["v1.3"]
    assert result["new"][0]["sha"] == "9f2c1aa"


def test_release_watch_seed(tmp_path):
    state = tmp_path / "release-watch.json"
    result = check_release_watch(state, lambda: [dict(V12), dict(V13)], NOW)
    assert result == {
        "new": [],
        "seeded": True,
        "checked_at": NOW.isoformat(timespec="seconds"),
    }
    stored = json.loads(state.read_text(encoding="utf-8"))
    assert [(p["tag"], p["sha"]) for p in stored["last_seen"]] == [
        ("v1.2", "831a6b0"),
        ("v1.3", "9f2c1aa"),
    ]


def test_release_watch_clean(tmp_path):
    state = _write_state(
        tmp_path / "release-watch.json", [("v1.2", "831a6b0"), ("v1.3", "9f2c1aa")]
    )
    before = json.loads(state.read_text(encoding="utf-8"))
    result = check_release_watch(state, lambda: [dict(V12), dict(V13)], NOW)
    assert result["new"] == []
    assert result["seeded"] is False
    stored = json.loads(state.read_text(encoding="utf-8"))
    assert stored["checked_at"] == NOW.isoformat(timespec="seconds")
    assert stored["checked_at"] != before["checked_at"]


def test_release_watch_sha_refresh(tmp_path):
    """Upstream SHA refresh on a known tag updates state without firing."""
    state = _write_state(tmp_path / "release-watch.json", [("v1.2", "oldsha")])
    result = check_release_watch(state, lambda: [dict(V12)], NOW)
    assert result["new"] == []
    stored = json.loads(state.read_text(encoding="utf-8"))
    assert stored["last_seen"] == [{"tag": "v1.2", "sha": "831a6b0"}]


def test_release_watch_gh_absent(tmp_path):
    state = _write_state(tmp_path / "release-watch.json", [("v1.2", "831a6b0")])
    before = state.read_bytes()

    def _missing():
        raise FileNotFoundError("gh")

    with pytest.raises(FileNotFoundError):
        check_release_watch(state, _missing, NOW)
    assert state.read_bytes() == before  # byte-identical on fetch failure

    # The heartbeat-facing checker degrades instead of raising.
    degraded = make_checker(str(state), _missing)(NOW)
    assert degraded["new"] == []
    assert degraded["seeded"] is False
    assert "error" in degraded
    assert state.read_bytes() == before


def test_release_watch_corrupt_state_reseeds(tmp_path):
    state = tmp_path / "release-watch.json"
    state.write_text("{not json", encoding="utf-8")
    result = check_release_watch(state, lambda: [dict(V12)], NOW)
    assert result["seeded"] is True
    assert result["new"] == []
    stored = json.loads(state.read_text(encoding="utf-8"))
    assert stored["last_seen"] == [{"tag": "v1.2", "sha": "831a6b0"}]


def test_release_watch_state_round_trip(tmp_path):
    from omega_prime.grokbot.release_watch import _read_state, _write_state

    state = tmp_path / "release-watch.json"
    pairs = [
        {"tag": "v1.2", "sha": "831a6b0"},
        {"tag": "v1.3", "sha": "9f2c1aa"},
    ]
    _write_state(state, pairs, NOW.isoformat(timespec="seconds"))
    assert _read_state(state) == pairs


def test_release_watch_cli_usage_error(capsys):
    from omega_prime.grokbot.release_watch import main

    with pytest.raises(SystemExit) as exc:
        main(["check"])  # no --state
    assert exc.value.code == 2


def test_release_watch_cli_empty_repo_fails_closed(tmp_path, capsys):
    from omega_prime.grokbot.release_watch import main

    code = main(["check", "--state", str(tmp_path / "rw.json"), "--repo", ""])
    assert code == 2


def test_build_digest_and_releases_block(tmp_path):
    digest = build_digest({"new": [dict(V13)], "checked_at": "x", "seeded": False})
    assert digest == {"releases": {"new": [dict(V13)], "checked_at": "x"}}
    assert read_releases_block(tmp_path) == {"new": [], "checked_at": None}
