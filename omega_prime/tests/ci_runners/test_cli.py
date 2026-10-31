"""``python -m omega_prime.ci_runners`` dispatch, without binding sockets."""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from omega_prime.ci_runners import __main__ as cli

from .test_app import _env


def test_parser_defaults() -> None:
    controller = cli.parser().parse_args(["controller"])
    assert (controller.host, controller.port) == ("127.0.0.1", 8788)
    agent = cli.parser().parse_args(["agent", "--port", "9000"])
    assert (agent.host, agent.port) == ("0.0.0.0", 9000)
    with pytest.raises(SystemExit):
        cli.parser().parse_args([])


def test_missing_settings_exit_2_naming_variable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["controller"], env={}, serve_fn=lambda *a: None) == 2
    err = capsys.readouterr().err
    assert err.startswith("omega-ci controller:") and "OMEGA_CI_" in err


def test_controller_serves_built_app(tmp_path: Path) -> None:
    served: list[Any] = []
    rc = cli.main(
        ["controller", "--port", "9999"],
        env=_env(tmp_path),
        serve_fn=lambda app, host, port: served.append((app, host, port)),
    )
    assert rc == 0 and served[0][1:] == ("127.0.0.1", 9999) and callable(served[0][0])


def test_serve_without_uvicorn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    assert cli.main(["controller"], env=_env(tmp_path)) == 2
    assert cli.EXTRA_HINT in capsys.readouterr().err


def test_serve_with_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    fake = types.ModuleType("uvicorn")
    fake.run = lambda app, **kw: calls.append((app, kw))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    cli.serve("APP", "127.0.0.1", 1)
    assert calls == [
        ("APP", {"host": "127.0.0.1", "port": 1, "access_log": False, "lifespan": "on"})
    ]


def test_agent_unavailable_until_built(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "omega_prime.ci_runners.agent.app", None)
    assert cli.main(["agent"], env={}, serve_fn=lambda *a: None) == 2
    assert "agent" in capsys.readouterr().err


def test_agent_dispatch_uses_build_agent_app(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("omega_prime.ci_runners.agent.app")
    fake.build_agent_app = lambda env: ("AGENT", dict(env))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "omega_prime.ci_runners.agent.app", fake)
    served: list[Any] = []
    assert (
        cli.main(["agent"], env={"K": "v"}, serve_fn=lambda *a: served.append(a)) == 0
    )
    assert served == [(("AGENT", {"K": "v"}), "0.0.0.0", 8787)]


def test_importing_cli_does_not_import_uvicorn() -> None:
    code = (
        "import sys, omega_prime.ci_runners.__main__; print('uvicorn' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"
