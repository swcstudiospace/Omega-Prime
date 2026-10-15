# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-conflicts``: empty env, bad args, basenames, detector failure."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from omega_prime.commands.redteam_conflicts import USAGE, Error, run

_WINDOW = {
    "effective_from": "2026-01-01T00:00:00+00:00",
    "effective_until": "2026-12-31T00:00:00+00:00",
}


def _grant(path: Path, operator: str) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operator": operator,
                "authorized_targets": [{"host": "127.0.0.1"}],
                "case_classes": ["prompt-injection"],
                **_WINDOW,
            }
        ),
        encoding="utf-8",
    )


def test_empty_env_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    before = sorted(path.name for path in tmp_path.iterdir())
    assert run("", {}) == {"conflicts": [], "total": 0, "truncated": 0}
    assert run(
        "",
        {"OMEGA_PRIME_REDTEAM_ROE_FILE": "  ", "OMEGA_PRIME_REDTEAM_ROE_FILES": ", ,"},
    ) == {"conflicts": [], "total": 0, "truncated": 0}
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_bad_args_do_not_call_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("detector must not run")

    monkeypatch.setattr(
        "omega_prime.grokbot.roe_lifecycle.grant_conflicts",
        _forbidden,
    )
    with pytest.raises(Error) as caught:
        run("--flag", {"OMEGA_PRIME_REDTEAM_ROE_FILE": "/tmp/grant.json"})
    assert caught.value.error == "bad_args"
    assert caught.value.result == {"usage": USAGE}


def test_conflicts_are_basenames_only(tmp_path: Path) -> None:
    first = tmp_path / "alpha-grant.json"
    second = tmp_path / "beta-grant.json"
    _grant(first, "operator-a")
    _grant(second, "operator-b")
    result = run(
        "",
        {"OMEGA_PRIME_REDTEAM_ROE_FILES": f"{first}, {second}"},
    )
    rendered = json.dumps(result)
    assert result["truncated"] == 0
    assert result["total"] == len(result["conflicts"])
    assert result["conflicts"]
    assert all(row["kind"] != "conflicts-truncated" for row in result["conflicts"])
    labels = " ".join(row["label"] for row in result["conflicts"])
    assert first.name in labels
    assert second.name in labels
    assert str(tmp_path) not in rendered
    assert "/" not in labels


def test_files_list_replaces_the_single_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def _capture(paths: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        seen["paths"] = list(paths)
        return []

    monkeypatch.setattr("omega_prime.grokbot.roe_lifecycle.grant_conflicts", _capture)
    active = tmp_path / "active.json"
    other = tmp_path / "other.json"
    stale = tmp_path / "stale.json"
    result = run(
        "",
        {
            "OMEGA_PRIME_REDTEAM_ROE_FILE": str(stale),
            "OMEGA_PRIME_REDTEAM_ROE_FILES": f"{active}, {other}",
        },
    )
    assert result == {"conflicts": [], "total": 0, "truncated": 0}
    assert seen["paths"] == [str(active), str(other)]
    blank = run(
        "",
        {
            "OMEGA_PRIME_REDTEAM_ROE_FILE": str(stale),
            "OMEGA_PRIME_REDTEAM_ROE_FILES": " , ",
        },
    )
    assert blank["total"] == 0
    assert seen["paths"] == [str(stale)]


def test_detector_exception_is_detector_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("secret /var/roe/grant.json")

    monkeypatch.setattr(
        "omega_prime.grokbot.roe_lifecycle.grant_conflicts",
        _boom,
    )
    with pytest.raises(Error) as caught:
        run("", {"OMEGA_PRIME_REDTEAM_ROE_FILE": "/var/roe/grant.json"})
    assert caught.value.error == "detector_failed"
    assert caught.value.result == {"usage": USAGE}
    assert "grant.json" not in str(caught.value)
    assert "secret" not in str(caught.value)
