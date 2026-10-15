# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-readiness``: read-only live-readiness report.

The gateway TCP probe is always patched. ``run_suite`` is patched to raise
so an accidental engagement fails the test. No artifact file is created.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from omega_prime.commands import redteam_readiness
from sidecar import simulation

ROE_FILE_ENV_VAR = "OMEGA_PRIME_REDTEAM_ROE_FILE"
ROE_FILES_ENV_VAR = "OMEGA_PRIME_REDTEAM_ROE_FILES"
GATEWAY_URL_ENV_VAR = "OMEGA_PRIME_REDTEAM_GATEWAY_URL"
_KEYS = (
    "roe_state",
    "gateway_reachable",
    "suites_loaded",
    "suites_total",
    "ready_for_live",
)


def _seal(monkeypatch: Any, reachable: bool) -> None:
    """Block sockets and fail closed if an engagement starts."""

    def _reachable(_url: str, timeout: float = 2.0) -> bool:
        del timeout
        return reachable

    def _run_suite(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("run_suite must not be called")

    monkeypatch.setattr(
        "omega_prime.grokbot.roe_lifecycle.gateway_reachable", _reachable
    )
    monkeypatch.setattr(simulation, "run_suite", _run_suite)
    monkeypatch.delenv(ROE_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(ROE_FILES_ENV_VAR, raising=False)
    monkeypatch.delenv(GATEWAY_URL_ENV_VAR, raising=False)


def test_bad_args_do_not_probe(monkeypatch: Any) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("check_readiness must not be called")

    monkeypatch.setattr("omega_prime.grokbot.roe_lifecycle.check_readiness", _boom)
    with pytest.raises(redteam_readiness.Error) as caught:
        redteam_readiness.run("--force", {})
    assert caught.value.error == "bad_args"
    assert caught.value.result == {"usage": redteam_readiness.USAGE}


def test_down_gateway_is_not_ready(monkeypatch: Any, tmp_path: Path) -> None:
    _seal(monkeypatch, False)
    before = sorted(path.name for path in tmp_path.iterdir())
    report = redteam_readiness.run("", {})
    assert tuple(report) == _KEYS
    assert report["gateway_reachable"] is False
    assert report["ready_for_live"] is False
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_missing_artifact_passes_none_and_writes_nothing(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _seal(monkeypatch, True)
    seen: dict[str, Any] = {}

    def _capture(
        artifact: Any, now: Any, *, gateway_url: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        del now, kwargs
        seen["artifact"] = artifact
        seen["gateway_url"] = gateway_url
        return {
            "roe_state": "BLOCKED",
            "gateway_reachable": True,
            "suites_loaded": 0,
            "suites_total": 0,
            "ready_for_live": False,
            "extra": "dropped",
        }

    monkeypatch.setattr("omega_prime.grokbot.roe_lifecycle.check_readiness", _capture)
    monkeypatch.chdir(tmp_path)
    report = redteam_readiness.run("   ", {GATEWAY_URL_ENV_VAR: "http://127.0.0.1:9"})
    assert seen == {"artifact": None, "gateway_url": "http://127.0.0.1:9"}
    assert "extra" not in report
    assert report["ready_for_live"] is False
    assert list(tmp_path.iterdir()) == []


def test_roe_files_replace_the_single_file(monkeypatch: Any, tmp_path: Path) -> None:
    _seal(monkeypatch, True)
    seen: list[Any] = []

    def _capture(
        artifact: Any, now: Any, *, gateway_url: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        del now, gateway_url, kwargs
        seen.append(artifact)
        return {
            "roe_state": "IDLE",
            "gateway_reachable": True,
            "suites_loaded": 1,
            "suites_total": 1,
            "ready_for_live": True,
        }

    monkeypatch.setattr("omega_prime.grokbot.roe_lifecycle.check_readiness", _capture)
    active = tmp_path / "active.json"
    stale = tmp_path / "stale.json"
    report = redteam_readiness.run(
        "",
        {
            ROE_FILE_ENV_VAR: str(stale),
            ROE_FILES_ENV_VAR: str(active),
        },
    )
    assert seen == [str(active)]
    assert report["roe_state"] == "IDLE"
    assert report["ready_for_live"] is True


def test_a_later_grant_blocks_readiness(monkeypatch: Any, tmp_path: Path) -> None:
    _seal(monkeypatch, True)

    def _capture(
        artifact: Any, now: Any, *, gateway_url: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        del artifact, now, gateway_url, kwargs
        return {
            "roe_state": "IDLE",
            "gateway_reachable": True,
            "suites_loaded": 1,
            "suites_total": 1,
            "ready_for_live": True,
        }

    def _blocked(path: Any, now: Any, audit_log: Any = None) -> dict[str, Any]:
        del path, now, audit_log
        return {"state": "missing"}

    monkeypatch.setattr("omega_prime.grokbot.roe_lifecycle.check_readiness", _capture)
    monkeypatch.setattr(
        "omega_prime.grokbot.roe_lifecycle.check_roe_lifecycle", _blocked
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    report = redteam_readiness.run(
        "",
        {ROE_FILES_ENV_VAR: f"{first}, {second}"},
    )
    assert report["roe_state"] == "BLOCKED"
    assert report["ready_for_live"] is False
    assert report["gateway_reachable"] is True
