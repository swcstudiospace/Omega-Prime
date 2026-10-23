"""Operator command dispatch and eventstream boundary tests."""

from __future__ import annotations

import asyncio
import builtins
import json
from types import SimpleNamespace
from typing import Any

import pytest

from omega_prime.engine.cli import main as engine_main
from omega_prime.eventstream import cli
from omega_prime.eventstream.admin import ReconcileAction, ReconcileResult


def test_unrelated_engine_commands_do_not_import_pulsar(
    monkeypatch: Any, tmp_path: Any
) -> None:
    original = builtins.__import__

    def guarded(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "omega_prime.eventstream.adapters.pulsar" or name.startswith(
            "pulsar"
        ):
            raise AssertionError("unrelated command imported Pulsar")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert engine_main(["ui", "list", "--state-dir", str(tmp_path)]) in (0, 1, 2)
    assert engine_main(["watch", "missing-job", "--state-dir", str(tmp_path)]) in (
        0,
        1,
        2,
    )


def test_apply_dry_run_and_status_emit_actual_boundary_results(
    monkeypatch: Any, capsys: Any
) -> None:
    class FakeAdmin:
        async def configure(self, config: object) -> None:
            self.config = config

        async def reconcile(self, *, dry_run: bool = False) -> ReconcileResult:
            self.dry_run = dry_run
            return ReconcileResult(
                dry_run,
                (ReconcileAction("PUT", "/topic", "create topic"),),
                ("topic missing",),
            )

        async def status(self) -> dict[str, object]:
            return {"tenant": "omega", "healthy": True}

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("omega_prime.eventstream.admin.PulsarAdminPort", FakeAdmin)
    assert engine_main(["eventstream", "apply", "--dry-run"]) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run == {
        "operation": "apply",
        "dry_run": True,
        "changes": ["topic missing"],
        "actions": [{"method": "PUT", "path": "/topic", "description": "create topic"}],
    }
    assert engine_main(["eventstream", "apply"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is False
    assert engine_main(["eventstream", "status"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "operation": "status",
        "status": {"tenant": "omega", "healthy": True},
    }


def test_parent_dispatch_is_lazy_for_eventstream(monkeypatch: Any) -> None:
    called: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda argv: called.append(list(argv)) or 0)
    assert engine_main(["eventstream", "tail", "persistent://omega/sdlc/ci"]) == 0
    assert called == [["tail", "persistent://omega/sdlc/ci"]]


def test_errors_are_redacted_and_return_deterministic_code(
    monkeypatch: Any, capsys: Any
) -> None:
    class FailedAdmin:
        async def configure(self, config: object) -> None:
            pass

        async def status(self) -> dict[str, object]:
            raise RuntimeError("token=super-secret")

        async def close(self) -> None:
            pass

    monkeypatch.setattr("omega_prime.eventstream.admin.PulsarAdminPort", FailedAdmin)
    assert cli.main(["status"]) == 2
    error = capsys.readouterr().err
    assert "super-secret" not in error
    assert "omega eventstream:" in error


def test_tail_awaits_reader_sanitizes_events_and_closes_on_cancellation(
    monkeypatch: Any, capsys: Any
) -> None:
    class FakeReader:
        def __init__(self) -> None:
            self.closed = False
            self.reads = 0

        async def read(self) -> object:
            self.reads += 1
            if self.reads == 1:
                return SimpleNamespace(
                    topic="persistent://omega/sdlc/ci",
                    key="repo#1",
                    event=SimpleNamespace(
                        to_bytes=lambda: (
                            b'{"type":"omega.ci.run.passed","data":{"token":"secret-value"}}'
                        )
                    ),
                )
            raise asyncio.CancelledError

        async def close(self) -> None:
            self.closed = True

    buses: list[Any] = []

    class FakeBus:
        def __init__(self, config: object) -> None:
            self.reader = FakeReader()
            self.closed = False
            buses.append(self)

        async def read(self, topic: str, *, start_at: int) -> FakeReader:
            assert topic == "persistent://omega/sdlc/ci"
            assert start_at == 0
            return self.reader

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        "omega_prime.eventstream.adapters.pulsar.PulsarEventBus", FakeBus
    )
    config = cli.EventStreamConfig()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cli._tail("persistent://omega/sdlc/ci", config))
    output = json.loads(capsys.readouterr().out)
    assert output["event"]["data"]["token"] == "[REDACTED]"
    assert buses[0].reader.closed
    assert buses[0].closed


def test_cli_rejects_unknown_operation() -> None:
    import pytest

    from omega_prime.eventstream import cli as eventstream_cli

    with pytest.raises(SystemExit):
        eventstream_cli.main(["eventstream", "not-a-command"])
