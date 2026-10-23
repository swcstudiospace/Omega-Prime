"""Run the real Desktop wrapper with a fake ``python3`` and fake upstream command."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Iterator
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


@contextlib.contextmanager
def _wrapper(args: list[str], env: dict[str, str]) -> Iterator[subprocess.Popen[bytes]]:
    """Run the wrapper in its own process group; always kill and reap the group.

    The wrapper no longer execs upstream, so a failed test must not leave the
    looping fake agent or desktop behind.
    """
    proc = subprocess.Popen([str(WRAPPER), *args], env=env, start_new_session=True)
    try:
        yield proc
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)


def _run(args: list[str], env: dict[str, str], timeout: float = 20) -> int:
    with _wrapper(args, env) as proc:
        return proc.wait(timeout=timeout)


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
    returncode = _run(args, env)
    out = tmp_path / "out"
    assert returncode == 7  # upstream exit status is the wrapper's
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
    with _wrapper([str(upstream)], env) as proc:
        _wait_for(out / "agent.trapped")
        _wait_for(tmp_path / "upstream.trapped")
        os.kill(proc.pid, signal.SIGTERM)
        assert proc.wait(timeout=10) == 143  # upstream's status is the wrapper's
    assert (tmp_path / "upstream.sig").read_text().strip() == "term"
    assert (out / "agent.sig").read_text().strip() == "term"
    # The wrapper waited for the agent's drain before exiting.
    assert (out / "agent.drained").read_text().strip() == "drained"


def test_repeated_sigterm_does_not_cut_the_agent_drain_short(tmp_path: Path) -> None:
    env, upstream = _setup(
        tmp_path, tail=TRAPPING_UPSTREAM, agent_tail=DRAINING_AGENT.replace("0.3", "1")
    )
    out = tmp_path / "out"
    with _wrapper([str(upstream)], env) as proc:
        _wait_for(out / "agent.trapped")
        _wait_for(tmp_path / "upstream.trapped")
        os.kill(proc.pid, signal.SIGTERM)
        _wait_for(out / "agent.sig")  # the agent is now draining
        os.kill(proc.pid, signal.SIGTERM)
        assert proc.wait(timeout=10) == 143
        # Exited only after the drain, despite the second signal.
        assert (out / "agent.drained").read_text().strip() == "drained"


def test_upstream_status_on_sigterm_is_kept_not_the_signal(tmp_path: Path) -> None:
    """Upstream exiting 3 on SIGTERM must surface as 3, never the trap's 143."""
    tail = TRAPPING_UPSTREAM.replace("exit 143", "exit 3")
    for i in range(5):  # the exit races the interrupted wait; try a few times
        run = tmp_path / f"r{i}"
        run.mkdir()
        env, upstream = _setup(run, tail=tail)
        with _wrapper([str(upstream)], env) as proc:
            _wait_for(run / "upstream.trapped")
            os.kill(proc.pid, signal.SIGTERM)
            assert proc.wait(timeout=10) == 3


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
    assert _run([str(upstream)], env) == 3
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
