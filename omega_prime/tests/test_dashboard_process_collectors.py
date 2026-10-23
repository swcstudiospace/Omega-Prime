# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Fake-only tests for process, Herdr and Podman dashboard collectors."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from typing import Any

import pytest

from omega_prime.dashboard.collectors.herdr import make_herdr_collector
from omega_prime.dashboard.collectors.podman import make_podman_collector
from omega_prime.dashboard.collectors.procs import make_procs_collector


def _proc(
    root: Path, pid: int, *, comm: str = "omp", cmdline: bytes = b"omp\0"
) -> None:
    path = root / str(pid)
    path.mkdir(parents=True)
    (path / "comm").write_text(comm, encoding="utf-8")
    fields = ["S"] + ["0"] * 40
    fields[19] = "100"
    (path / "stat").write_text(
        f"{pid} (process (with parens)) " + " ".join(fields), encoding="utf-8"
    )
    (path / "cmdline").write_bytes(cmdline)
    (path / "cwd").symlink_to("/tmp/workspace")


def test_procs_filters_allowlisted_fields_and_extracts_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _proc(tmp_path, 101, cmdline=b"omp\0--mode\0rpc\0")
    _proc(tmp_path, 102)
    _proc(tmp_path, 103, comm="other")
    now = datetime.fromtimestamp(1020, UTC)
    original = os.readlink

    def guarded(path: os.PathLike[str] | str, *args: Any, **kwargs: Any) -> str:
        assert "environ" not in os.fspath(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "readlink", guarded)
    result = make_procs_collector(
        proc_root=tmp_path, now_fn=lambda: now, clock_hz=10, boot_time=1000
    )()
    assert result.status == "ok"
    assert {frozenset(item) for item in result.items} == {
        frozenset(
            {
                "kind",
                "pid",
                "elapsed_s",
                "elapsed_measured_at",
                "cwd",
                "mode",
                "role",
                "label",
            }
        )
    }
    measured_at = datetime.fromisoformat(result.items[0]["elapsed_measured_at"])
    assert measured_at.tzinfo is not None
    assert abs((measured_at - now).total_seconds()) <= 2
    assert [item["mode"] for item in result.items] == ["rpc", None]
    assert all(item["elapsed_s"] == 10 for item in result.items)
    assert result.extra == {"skipped": 0, "exited": 0, "services": []}


def test_known_daemons_are_services_not_agent_work(tmp_path: Path) -> None:
    _proc(
        tmp_path,
        301,
        cmdline=b"bun\0/home/box/.bun/bin/omp\0auth-broker\0serve\0--bind=127.0.0.1:35137\0",
    )
    _proc(
        tmp_path,
        302,
        comm="openshell-gatew",
        cmdline=b"/opt/openshell/bin/openshell-gateway\0--config\0g.toml\0",
    )
    _proc(tmp_path, 303, comm="herdr", cmdline=b"/home/box/.local/bin/herdr\0server\0")
    _proc(tmp_path, 304, comm="herdr", cmdline=b"herdr\0pane\0list\0")
    _proc(tmp_path, 305, cmdline=b"bun\0omp\0-p\0--mode\0json\0")
    result = make_procs_collector(
        proc_root=tmp_path,
        now_fn=lambda: datetime.fromtimestamp(1020, UTC),
        clock_hz=10,
        boot_time=1000,
    )()
    assert [(item["pid"], item["role"]) for item in result.items] == [(305, "agent")]
    assert [(s["pid"], s["name"]) for s in result.extra["services"]] == [
        (301, "omp auth-broker"),
        (302, "openshell-gateway"),
        (303, "herdr server"),
    ]
    assert all(s["role"] == "service" for s in result.extra["services"])


def test_argument_values_never_turn_an_agent_into_a_service(tmp_path: Path) -> None:
    """Only the executable and its subcommand position name a daemon."""
    _proc(tmp_path, 311, cmdline=b"bun\0omp\0--cwd\0/work/auth-broker\0-p\0")
    _proc(tmp_path, 312, cmdline=b"omp\0--cwd\0/work/openshell-gateway\0-p\0")
    _proc(tmp_path, 313, cmdline=b"bun\0omp\0--mode\0rpc\0/work/server\0")
    result = make_procs_collector(
        proc_root=tmp_path,
        now_fn=lambda: datetime.fromtimestamp(1020, UTC),
        clock_hz=10,
        boot_time=1000,
    )()
    assert [(item["pid"], item["role"]) for item in result.items] == [
        (311, "agent"),
        (312, "agent"),
        (313, "agent"),
    ]
    assert result.extra["services"] == []


def test_dead_and_reused_pids_are_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _proc(tmp_path, 401)
    stat = tmp_path / "401" / "stat"
    stat.write_text(stat.read_text().replace(") S ", ") Z "), encoding="utf-8")
    _proc(tmp_path, 402, cmdline=b"")
    _proc(tmp_path, 403)
    _proc(tmp_path, 404)
    original = os.readlink

    def exit_during_scan(path: os.PathLike[str] | str, *args: Any, **kwargs: Any):
        target = original(path, *args, **kwargs)
        name = Path(os.fspath(path)).parent.name
        if name == "403":
            (tmp_path / "403" / "stat").unlink()
        if name == "404":
            again = tmp_path / "404" / "stat"
            again.write_text(again.read_text().replace(" 100 ", " 999 ", 1))
        return target

    monkeypatch.setattr(os, "readlink", exit_during_scan)
    result = make_procs_collector(
        proc_root=tmp_path,
        now_fn=lambda: datetime.fromtimestamp(1020, UTC),
        clock_hz=10,
        boot_time=1000,
    )()
    assert result.items == []
    assert result.status == "empty"
    assert result.extra["exited"] == 4
    assert result.extra["skipped"] == 0


def test_procs_skips_racing_pid_and_handles_missing_or_empty_root(
    tmp_path: Path,
) -> None:
    _proc(tmp_path, 201)
    (tmp_path / "201" / "stat").unlink()
    result = make_procs_collector(
        proc_root=tmp_path,
        now_fn=lambda: datetime.fromtimestamp(1020, UTC),
        clock_hz=10,
        boot_time=1000,
    )()
    assert result.status == "empty"
    assert result.extra["skipped"] == 1
    assert (
        make_procs_collector(proc_root=tmp_path / "missing")().status == "unavailable"
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    assert (
        make_procs_collector(proc_root=empty, clock_hz=10, boot_time=1000)().status
        == "empty"
    )


def test_herdr_snapshot_allowlist_redaction_and_invocation() -> None:
    leak = "sk" + "-" + token_hex(6)
    payload = {
        "result": {
            "snapshot": {
                "workspaces": [
                    {
                        "name": "swarm-smoke",
                        "label": "swarm-smoke",
                        "tabs": [
                            {
                                "panes": [
                                    {
                                        "pane_id": "w2:p1",
                                        "name": "worker",
                                        "cwd": "/tmp/work",
                                        "agent_status": "idle",
                                        "terminal_title": leak,
                                        "agent_session": {
                                            "value": "/tmp/session.jsonl"
                                        },
                                        "unknown": "discard",
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        }
    }
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        calls.append(argv)
        return json.dumps(payload)

    result = make_herdr_collector(runner=runner)()
    assert calls == [["herdr", "api", "snapshot"]]
    assert result.status == "ok"
    item = result.items[0]
    assert item == {
        "kind": "herdr_pane",
        "pane_id": "w2:p1",
        "workspace": "swarm-smoke",
        "name": "worker",
        "agent_status": "idle",
        "cwd": "/tmp/work",
        "terminal_title": "[REDACTED]",
        "session_path": "/tmp/session.jsonl",
        "has_live_agent": True,
    }
    assert leak not in json.dumps(result.items)


def test_herdr_failure_malformed_and_empty_are_source_local() -> None:
    def timeout(argv: list[str]) -> str:
        raise subprocess.TimeoutExpired(argv, 3.0)

    assert make_herdr_collector(runner=timeout)().status == "unavailable"
    assert make_herdr_collector(runner=lambda argv: "{")().status == "unavailable"
    empty = make_herdr_collector(
        runner=lambda argv: '{"result":{"snapshot":{"workspaces":[]}}}'
    )()
    assert empty.status == "empty"
    podman = make_podman_collector(runner=lambda argv: "[]")()
    assert podman.status == "empty"


def test_podman_parses_containers_and_invokes_read_only_argv() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        calls.append(argv)
        return json.dumps(
            [
                {
                    "Names": ["sandbox"],
                    "State": "running",
                    "Status": "Up",
                    "Created": "today",
                },
                {
                    "Names": ["supervisor"],
                    "State": "exited",
                    "Status": "Exited",
                    "CreatedAt": "yesterday",
                },
            ]
        )

    result = make_podman_collector(runner=runner)()
    assert calls == [["podman", "ps", "-a", "--format", "json"]]
    assert result.status == "ok"
    assert [item["name"] for item in result.items] == ["sandbox", "supervisor"]
    assert result.items[0]["state"] == "running"
    assert result.items[0]["status"] == "Up"
    assert result.items[0]["created"] == "today"
    assert result.items[1]["created"] == "yesterday"


def test_podman_missing_executable_and_malformed_output_are_unknown() -> None:
    def missing(argv: list[str]) -> str:
        raise FileNotFoundError("podman")

    assert "sandbox containers unknown" in (
        make_podman_collector(runner=missing)().error or ""
    )
    malformed = make_podman_collector(runner=lambda argv: "not json")()
    assert malformed.status == "unavailable"
    assert "sandbox containers unknown" in (malformed.error or "")


def test_default_runners_pass_exact_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], float]] = []

    class Completed:
        returncode = 0
        stdout = ""

    def run(
        argv: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        check: bool,
    ) -> Completed:
        calls.append((argv, timeout))
        result = Completed()
        result.stdout = (
            '{"result":{"snapshot":{"workspaces":[]}}}' if argv[0] == "herdr" else "[]"
        )
        return result

    monkeypatch.setattr(subprocess, "run", run)
    assert make_herdr_collector()().status == "empty"
    assert make_podman_collector()().status == "empty"
    assert calls == [
        (["herdr", "api", "snapshot"], 3.0),
        (["podman", "ps", "-a", "--format", "json"], 5.0),
    ]
