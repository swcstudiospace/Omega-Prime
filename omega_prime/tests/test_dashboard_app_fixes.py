# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Regression tests for the dashboard Greptile fixes."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, MutableMapping
from datetime import UTC, datetime
from typing import Any

import pytest
from starlette.testclient import TestClient

from omega_prime.dashboard import __main__ as dashboard_main
from omega_prime.dashboard.__main__ import build_default_collectors, main
from omega_prime.dashboard.app import _SSEResponse, create_app
from omega_prime.dashboard.config import DashboardConfig
from omega_prime.dashboard.poller import DashboardPoller

NOW = datetime(2026, 10, 21, tzinfo=UTC)


def test_package_data_build_py_collects_nested_dashboard_assets(
    tmp_path: Path,
) -> None:
    import importlib.util
    import tomllib

    if importlib.util.find_spec("setuptools") is None:
        pytest.skip(
            "setuptools is unavailable; real package-data collection cannot run"
        )

    from setuptools import Distribution
    from setuptools.command.build_py import build_py

    root = Path(__file__).resolve().parents[2]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = data["tool"]["setuptools"]["package-data"]["omega_prime"]
    dist = Distribution(
        {
            "packages": ["omega_prime", "omega_prime.dashboard"],
            "package_dir": {"omega_prime": str(root / "omega_prime")},
            "package_data": {"omega_prime": patterns},
        }
    )
    command = build_py(dist)
    command.build_lib = str(tmp_path / "lib")
    command.ensure_finalized()
    command.run()

    packaged = Path(command.build_lib) / "omega_prime" / "dashboard" / "static"
    actual = root / "omega_prime" / "dashboard" / "static"
    expected = {
        actual / "index.html",
        actual / "app.js",
        actual / "app.css",
        actual / "cc" / "index.html",
        *(
            file
            for folder in ("assets", "licenses")
            for file in (actual / "cc" / folder).iterdir()
            if file.is_file()
        ),
    }
    for source in expected:
        assert (packaged / source.relative_to(actual)).is_file(), source
    assert not list(packaged.rglob("*.map"))
    assert not list(packaged.rglob("node_modules"))

    import omega_prime.dashboard.app as app_module

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        app_module,
        "__file__",
        str(Path(command.build_lib) / "omega_prime" / "dashboard" / "app.py"),
    )
    try:
        with TestClient(create_app(_poller())) as client:
            host = {"host": "127.0.0.1:7421"}
            page = client.get("/", headers=host)
            assert page.status_code == 200
            css_path = re.search(r'href="([^"]+\.css)"', page.text)
            assert css_path is not None
            assert client.get(css_path.group(1), headers=host).status_code == 200
    finally:
        monkeypatch.undo()


def _pidfile(home: Path) -> Path:
    return home / ".omega-prime" / "dashboard" / "dashboard.pid"


def _poller() -> DashboardPoller:
    return DashboardPoller([], clock=lambda: NOW)


def test_sse_disconnect_ends_waiting_stream_without_external_cancellation() -> None:
    async def drive() -> None:
        first_chunk_sent = asyncio.Event()
        never = asyncio.Event()
        messages: list[MutableMapping[str, Any]] = []

        async def events() -> AsyncIterator[str]:
            yield "first"
            await never.wait()
            yield "unreachable"

        async def receive() -> dict[str, Any]:
            await first_chunk_sent.wait()
            return {"type": "http.disconnect"}

        async def send(message: MutableMapping[str, Any]) -> None:
            messages.append(message)
            if (
                message["type"] == "http.response.body"
                and message.get("body") == b"first"
            ):
                first_chunk_sent.set()

        await _SSEResponse(events())({"type": "http"}, receive, send)

        starts = [
            message for message in messages if message["type"] == "http.response.start"
        ]
        assert starts
        assert not any(
            key.lower() == b"content-length" for key, _ in starts[0]["headers"]
        )
        body_frames = [
            message for message in messages if message["type"] == "http.response.body"
        ]
        assert body_frames[0]["body"] == b"first"
        assert body_frames[-1] == {
            "type": "http.response.body",
            "body": b"",
            "more_body": False,
        }

    asyncio.run(drive())


def test_host_guard_accepts_selected_ipv6_loopback() -> None:
    with TestClient(create_app(_poller(), port=7421, host="::1")) as client:
        assert client.get("/healthz", headers={"host": "[::1]:7421"}).status_code == 200
        assert (
            client.get("/healthz", headers={"host": "127.0.0.1:7421"}).status_code
            == 200
        )
        assert (
            client.get("/healthz", headers={"host": "evil.example:7421"}).status_code
            == 403
        )


def test_start_refuses_an_existing_live_pidfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pidfile = _pidfile(tmp_path)
    pidfile.parent.mkdir(parents=True)
    pidfile.write_text("2345 6789\n", encoding="ascii")
    monkeypatch.setattr(dashboard_main, "_process_start_time", lambda pid: 6789)

    assert (
        main(
            [],
            home=tmp_path,
            serve=lambda *args: pytest.fail("serve must not run"),
            poller_factory=lambda: pytest.fail("poller must not be created"),
        )
        == 1
    )
    assert pidfile.read_text(encoding="ascii") == "2345 6789\n"
    assert "dashboard already running (pid 2345)" in capsys.readouterr().err


def test_start_preserves_pidfile_replaced_during_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pidfile = _pidfile(tmp_path)
    pidfile.parent.mkdir(parents=True)
    pidfile.write_text("2345 6789\n", encoding="ascii")

    def start_time(pid: int) -> int | None:
        return None if pid == 2345 else 2468

    monkeypatch.setattr(dashboard_main, "_process_start_time", start_time)

    def serve(app: object, host: str, port: int) -> None:
        pidfile.write_text("3456 7890\n", encoding="ascii")
        raise RuntimeError("serve failed")

    with pytest.raises(RuntimeError, match="serve failed"):
        main([], home=tmp_path, serve=serve, poller_factory=_poller)
    assert pidfile.read_text(encoding="ascii") == "3456 7890\n"


def test_stop_refuses_a_pidfile_for_a_different_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pidfile = _pidfile(tmp_path)
    pidfile.parent.mkdir(parents=True)
    pidfile.write_text("2345 6789\n", encoding="ascii")
    monkeypatch.setattr(dashboard_main, "_process_start_time", lambda pid: 9999)
    signalled: list[tuple[int, int]] = []

    assert (
        main(
            ["--stop"],
            home=tmp_path,
            signal_fn=lambda pid, sig: signalled.append((pid, sig)),
        )
        == 1
    )
    assert signalled == []
    assert pidfile.read_text(encoding="ascii") == "2345 6789\n"
    assert "pidfile refers to a different process; refusing" in capsys.readouterr().err


def test_stop_removes_a_pidfile_for_a_dead_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pidfile = _pidfile(tmp_path)
    pidfile.parent.mkdir(parents=True)
    pidfile.write_text("2345 6789\n", encoding="ascii")
    monkeypatch.setattr(dashboard_main, "_process_start_time", lambda pid: None)
    signalled: list[tuple[int, int]] = []

    assert (
        main(
            ["--stop"],
            home=tmp_path,
            signal_fn=lambda pid, sig: signalled.append((pid, sig)),
        )
        == 0
    )
    assert signalled == []
    assert not pidfile.exists()
    assert "dashboard process already stopped" in capsys.readouterr().err


def test_stop_rejects_legacy_pidfile_without_signalling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pidfile = _pidfile(tmp_path)
    pidfile.parent.mkdir(parents=True)
    pidfile.write_text("2345\n", encoding="ascii")
    monkeypatch.setattr(
        dashboard_main,
        "_process_start_time",
        lambda pid: pytest.fail("must not inspect an invalid pidfile"),
    )
    signalled: list[tuple[int, int]] = []

    assert (
        main(
            ["--stop"],
            home=tmp_path,
            signal_fn=lambda pid, sig: signalled.append((pid, sig)),
        )
        == 1
    )
    assert signalled == []
    assert pidfile.read_text(encoding="ascii") == "2345\n"
    assert "invalid dashboard pidfile" in capsys.readouterr().err


def test_disabled_fleet_collector_is_registered_without_io(tmp_path: Path) -> None:
    collectors = build_default_collectors(home=tmp_path, config=DashboardConfig())
    fleet = next(collector for collector in collectors if collector.source == "fleet")

    assert fleet.fn().status == "disabled"
