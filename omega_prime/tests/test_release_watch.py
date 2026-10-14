# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Release watcher digest tests: new-tags, clean, seed, failures (PLAN-G7)."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

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


def test_fetch_upstream_tags_passes_a_timeout_to_gh(monkeypatch):
    from omega_prime.grokbot import release_watch

    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        seen["cmd"] = list(cmd)
        return SimpleNamespace(returncode=0, stdout="[]", stderr="", args=cmd)

    monkeypatch.setattr(release_watch.subprocess, "run", fake_run)
    assert release_watch.fetch_upstream_tags("OWNER/NAME") == []
    assert seen["cmd"] == ["gh", "api", "repos/OWNER/NAME/tags"]
    assert seen["timeout"] == 60


def test_fetch_upstream_tags_timeout_is_a_fetch_error(tmp_path, monkeypatch, capsys):
    from omega_prime.grokbot import release_watch

    def hanging(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(release_watch.subprocess, "run", hanging)
    state = _write_state(tmp_path / "release-watch.json", [("v1.2", "831a6b0")])
    before = state.read_bytes()

    def fetch():
        return release_watch.fetch_upstream_tags("OWNER/NAME")

    with pytest.raises(subprocess.TimeoutExpired):
        check_release_watch(state, fetch, NOW)
    assert state.read_bytes() == before
    degraded = make_checker(str(state), fetch)(NOW)
    assert "error" in degraded and degraded["new"] == []
    code = release_watch.main(["check", "--state", str(state)])
    assert code == release_watch.FETCH_FAILURE_EXIT
    assert "fetch failed" in capsys.readouterr().err


def test_build_digest_and_releases_block(tmp_path):
    digest = build_digest({"new": [dict(V13)], "checked_at": "x", "seeded": False})
    assert digest == {"releases": {"new": [dict(V13)], "checked_at": "x"}}
    assert read_releases_block(tmp_path) == {"new": [], "checked_at": None}


def test_auto_pin_bump_success_records_bump(tmp_path):
    from omega_prime.grokbot.release_watch import check_release_watch

    state = _write_state(tmp_path / "release-watch.json", [("v1.2", "831a6b0")])
    calls = []

    def fake_bump(tag):
        calls.append(tag)
        return 0

    result = check_release_watch(
        state, lambda: [dict(V12), dict(V13)], NOW, auto_pin_bump=True, bump=fake_bump
    )
    assert calls == ["v1.3"]  # newest tag, upstream order
    assert result["bump"] == {"tag": "v1.3", "exit": 0, "recorded": True}
    stored = json.loads(state.read_text(encoding="utf-8"))
    assert stored["last_pin_bump"] == {
        "tag": "v1.3",
        "checked_at": NOW.isoformat(timespec="seconds"),
        "exit": 0,
    }
    # Re-tick: nothing new, no second bump, record preserved.
    again = check_release_watch(
        state, lambda: [dict(V12), dict(V13)], NOW, auto_pin_bump=True, bump=fake_bump
    )
    assert again["new"] == []
    assert "bump" not in again
    assert calls == ["v1.3"]
    assert (
        json.loads(state.read_text(encoding="utf-8"))["last_pin_bump"]["tag"] == "v1.3"
    )


def test_auto_pin_bump_regression_logs_report_without_recording(tmp_path, capsys):
    from omega_prime.grokbot.release_watch import check_release_watch

    state = _write_state(tmp_path / "release-watch.json", [("v1.2", "831a6b0")])
    report = tmp_path / "report.md"
    report.write_text("delta report", encoding="utf-8")

    def fake_bump(tag):
        assert tag == "v1.3"
        return 1

    result = check_release_watch(
        state,
        lambda: [dict(V12), dict(V13)],
        NOW,
        auto_pin_bump=True,
        bump=fake_bump,
        root=tmp_path,
    )
    assert result["bump"] == {
        "tag": "v1.3",
        "exit": 1,
        "report": str(report),
        "recorded": False,
    }
    err = capsys.readouterr().err
    assert "v1.3" in err and str(report) in err
    stored = json.loads(state.read_text(encoding="utf-8"))
    assert "last_pin_bump" not in stored  # pin left unchanged
    # New tags are still digested (state advances) even when the bump fails.
    assert [p["tag"] for p in stored["last_seen"]] == ["v1.2", "v1.3"]


def test_auto_pin_bump_regression_without_report_file(tmp_path, capsys):
    from omega_prime.grokbot.release_watch import check_release_watch

    state = _write_state(tmp_path / "release-watch.json", [("v1.2", "831a6b0")])
    result = check_release_watch(
        state,
        lambda: [dict(V12), dict(V13)],
        NOW,
        auto_pin_bump=True,
        bump=lambda tag: 1,
        root=tmp_path,
    )
    assert result["bump"]["report"] is None
    assert result["bump"]["recorded"] is False
    assert "report: None" in capsys.readouterr().err


def test_auto_pin_bump_disabled_by_default_even_with_bump_runner(tmp_path):
    from omega_prime.grokbot.release_watch import check_release_watch

    state = _write_state(tmp_path / "release-watch.json", [("v1.2", "831a6b0")])

    def fail_if_called(tag):
        raise AssertionError("bump must not run when auto_pin_bump is False")

    result = check_release_watch(
        state, lambda: [dict(V12), dict(V13)], NOW, bump=fail_if_called
    )
    assert result["new"] == [dict(V13)]
    assert "bump" not in result


def test_auto_pin_bump_seed_never_bumps(tmp_path):
    from omega_prime.grokbot.release_watch import check_release_watch

    state = tmp_path / "release-watch.json"

    def fail_if_called(tag):
        raise AssertionError("seeding ticks must never bump")

    result = check_release_watch(
        state,
        lambda: [dict(V12), dict(V13)],
        NOW,
        auto_pin_bump=True,
        bump=fail_if_called,
    )
    assert result["seeded"] is True
    assert "bump" not in result


def test_auto_pin_bump_targets_newest_tag_first(tmp_path):
    from omega_prime.grokbot.release_watch import check_release_watch

    v14 = {"tag": "v1.4", "sha": "aa11bb2", "date": "2026-10-10T00:00:00+00:00"}
    state = _write_state(tmp_path / "release-watch.json", [("v1.2", "831a6b0")])
    calls = []
    result = check_release_watch(
        state,
        lambda: [dict(v14), dict(V13), dict(V12)],
        NOW,
        auto_pin_bump=True,
        bump=lambda tag: calls.append(tag) or 0,  # type: ignore[func-returns-value]
    )
    assert calls == ["v1.4"]
    assert result["bump"]["tag"] == "v1.4"


def test_auto_pin_bump_runner_exception_leaves_state_untouched(tmp_path):
    from omega_prime.grokbot.release_watch import check_release_watch

    state = _write_state(tmp_path / "release-watch.json", [("v1.2", "831a6b0")])
    before = state.read_bytes()

    def boom(tag):
        raise RuntimeError("bump exploded")

    with pytest.raises(RuntimeError, match="bump exploded"):
        check_release_watch(
            state, lambda: [dict(V12), dict(V13)], NOW, auto_pin_bump=True, bump=boom
        )
    assert state.read_bytes() == before


def test_make_checker_auto_pin_bump_passthrough(tmp_path):
    state = _write_state(tmp_path / "release-watch.json", [("v1.2", "831a6b0")])
    calls = []
    checker = make_checker(
        str(state),
        lambda: [dict(V12), dict(V13)],
        auto_pin_bump=True,
        bump=lambda tag: calls.append(tag) or 0,  # type: ignore[func-returns-value]
    )
    result = checker(NOW)
    assert calls == ["v1.3"]
    assert result["bump"]["recorded"] is True
    # Default checker stays inert.
    plain = make_checker(str(state), lambda: [dict(V12), dict(V13)])(NOW)
    assert "bump" not in plain


def test_default_bump_runner_calls_pin_bump_with_newest_tag(tmp_path, monkeypatch):
    from omega_prime.grokbot import pin_bump
    from omega_prime.grokbot.release_watch import check_release_watch

    state = _write_state(tmp_path / "release-watch.json", [("v1.2", "831a6b0")])
    seen = []

    def fake_pin_bump(to, *, root, **kwargs):
        seen.append((to, root))
        return 0

    monkeypatch.setattr(pin_bump, "bump", fake_pin_bump)
    result = check_release_watch(
        state, lambda: [dict(V12), dict(V13)], NOW, auto_pin_bump=True, root=tmp_path
    )
    assert seen == [("v1.3", tmp_path)]
    assert result["bump"]["recorded"] is True


def test_auto_pin_bump_enabled_helper():
    from omega_prime.grokbot.release_watch import auto_pin_bump_enabled

    assert auto_pin_bump_enabled() is False
    assert auto_pin_bump_enabled({}) is False
    assert auto_pin_bump_enabled({"redteam": {}}) is False
    assert auto_pin_bump_enabled({"redteam": {"auto_pin_bump": False}}) is False
    assert auto_pin_bump_enabled({"redteam": {"auto_pin_bump": "true"}}) is False
    assert auto_pin_bump_enabled({"redteam": {"auto_pin_bump": True}}) is True


def test_auto_pin_bump_config_default_and_overrides(tmp_path, monkeypatch):
    from omega_prime.config import load_config
    from omega_prime.grokbot.release_watch import auto_pin_bump_enabled

    assert load_config(tmp_path)["redteam"]["auto_pin_bump"] is False

    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_AUTO_PIN_BUMP", "1")
    assert load_config(tmp_path)["redteam"]["auto_pin_bump"] is True

    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_AUTO_PIN_BUMP", "0")
    assert load_config(tmp_path)["redteam"]["auto_pin_bump"] is False

    monkeypatch.delenv("OMEGA_PRIME_REDTEAM_AUTO_PIN_BUMP")
    (tmp_path / "omega-prime.json").write_text(
        json.dumps({"redteam": {"auto_pin_bump": True}}), encoding="utf-8"
    )
    assert load_config(tmp_path)["redteam"]["auto_pin_bump"] is True
    assert auto_pin_bump_enabled(load_config(tmp_path)) is True
