"""Run the real Desktop wrapper with a fake ``python3`` and fake upstream command."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from omega_prime.tests.ci_runners._secrets import placeholder

WRAPPER = (
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "ci-runner-desktop"
    / "entrypoint.sh"
)
PASSWORD = placeholder("desktop-password")

FAKE_PYTHON = """#!/bin/sh
printf '%s\\n' "$@" > '{out}/agent.argv'
printf '%s' "${{PASSWORD-unset}}" > '{out}/agent.password'
printf '%s' "$PYTHONPATH" > '{out}/agent.pythonpath'
{agent_tail}
"""

UPSTREAM = """#!/bin/sh
echo $$ > '{out}/upstream.pid'
printf '%s\\n' "$@" > '{out}/upstream.argv'
printf '%s' "$PASSWORD" > '{out}/upstream.password'
{tail}
"""


def _script(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


# A long-lived agent: on SIGTERM it "drains" (stands in for terminating its
# runners and posting their exit callbacks) before exiting.
DRAINING_AGENT = (
    'trap \'echo term > "{out}/agent.sig"; sleep 0.3; '
    'echo drained > "{out}/agent.drained"; exit 0\' TERM\n'
    "echo 1 > '{out}/agent.trapped'\n"
    "while :; do sleep 0.05; done"
)

TRAPPING_UPSTREAM = (
    "trap 'echo term > \"$0.sig\"; exit 143' TERM\n"
    'echo 1 > "$0.trapped"\n'
    "while :; do sleep 0.05; done"
)


def _setup(
    tmp_path: Path, tail: str = "exit 7", agent_tail: str = ""
) -> tuple[dict[str, str], Path]:
    out = tmp_path / "out"
    out.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _script(
        bindir / "python3",
        FAKE_PYTHON.format(out=out, agent_tail=agent_tail.format(out=out)),
    )
    upstream = _script(tmp_path / "upstream", UPSTREAM.format(out=out, tail=tail))
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "PASSWORD": PASSWORD,
        "OMEGA_CI_SITE": str(tmp_path / "site"),
    }
    return env, upstream


def _wait_for(path: Path, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while not path.exists() or not path.read_text():
        assert time.monotonic() < deadline, f"{path.name} never written"
        time.sleep(0.02)
    return path.read_text()


def test_starts_agent_then_runs_upstream_unchanged(tmp_path: Path) -> None:
    # Upstream outlives the agent's start-up, as the real desktop does; an
    # upstream exit stops the agent, so an instant exit would race it.
    env, upstream = _setup(
        tmp_path,
        tail=(
            'while [ ! -e "$(dirname "$0")/out/agent.pythonpath" ]; '
            "do sleep 0.02; done\nexit 7"
        ),
    )
    args = [str(upstream), "--flag", "two words", ""]
    proc = subprocess.run([str(WRAPPER), *args], env=env, timeout=20, check=False)
    out = tmp_path / "out"
    assert proc.returncode == 7  # upstream exit status is the wrapper's
    assert (out / "upstream.argv").read_text() == "--flag\ntwo words\n\n"
    assert (out / "upstream.password").read_text() == PASSWORD
    agent_argv = _wait_for(out / "agent.argv").splitlines()
    assert agent_argv == [
        "-m",
        "omega_prime.ci_runners",
        "agent",
        "--host",
        "0.0.0.0",
        "--port",
        "8787",
    ]
    assert _wait_for(out / "agent.password") == "unset"
    assert (out / "agent.pythonpath").read_text() == str(tmp_path / "site")
    assert all(PASSWORD not in a for a in agent_argv)


def test_sigterm_reaches_agent_and_upstream_and_agent_drains(tmp_path: Path) -> None:
    """00062: a redeploy's SIGTERM must reach the agent, not just upstream.

    Without it the agent dies by SIGKILL with its runners and never posts the
    exit callbacks that free the controller's slots.
    """
    env, upstream = _setup(tmp_path, tail=TRAPPING_UPSTREAM, agent_tail=DRAINING_AGENT)
    out = tmp_path / "out"
    proc = subprocess.Popen([str(WRAPPER), str(upstream)], env=env)
    try:
        _wait_for(out / "agent.trapped")
        _wait_for(tmp_path / "upstream.trapped")
        os.kill(proc.pid, signal.SIGTERM)
        assert proc.wait(timeout=10) == 143  # upstream's status is the wrapper's
        assert (tmp_path / "upstream.sig").read_text().strip() == "term"
        assert (out / "agent.sig").read_text().strip() == "term"
        # The wrapper waited for the agent's drain before exiting.
        assert (out / "agent.drained").read_text().strip() == "drained"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_upstream_exit_stops_agent_and_keeps_upstream_status(tmp_path: Path) -> None:
    env, upstream = _setup(tmp_path, tail="exit 3", agent_tail=DRAINING_AGENT)
    out = tmp_path / "out"
    # The upstream may exit before the agent installs its trap; make it wait.
    _script(
        upstream,
        upstream.read_text().replace(
            "exit 3",
            f"while [ ! -s '{out}/agent.trapped' ]; do sleep 0.02; done\nexit 3",
        ),
    )
    proc = subprocess.run(
        [str(WRAPPER), str(upstream)], env=env, timeout=20, check=False
    )
    assert proc.returncode == 3
    assert (out / "agent.sig").read_text().strip() == "term"
    assert (out / "agent.drained").read_text().strip() == "drained"


def test_missing_upstream_command_fails_fast(tmp_path: Path) -> None:
    env, _ = _setup(tmp_path)
    proc = subprocess.run(
        [str(WRAPPER)], env=env, capture_output=True, text=True, check=False
    )
    assert proc.returncode == 64 and "no upstream command" in proc.stderr
    assert not (tmp_path / "out" / "agent.argv").exists()


@pytest.mark.parametrize("shell", ["sh", "bash"])
def test_wrapper_syntax(shell: str) -> None:
    assert subprocess.run([shell, "-n", str(WRAPPER)], check=False).returncode == 0
