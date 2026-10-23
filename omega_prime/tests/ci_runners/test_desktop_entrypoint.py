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


def _setup(tmp_path: Path, tail: str = "exit 7") -> tuple[dict[str, str], Path]:
    out = tmp_path / "out"
    out.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _script(bindir / "python3", FAKE_PYTHON.format(out=out))
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


def test_starts_agent_then_execs_upstream_unchanged(tmp_path: Path) -> None:
    env, upstream = _setup(tmp_path)
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


def test_upstream_replaces_wrapper_and_receives_signals(tmp_path: Path) -> None:
    env, upstream = _setup(
        tmp_path,
        tail=(
            "trap 'echo term > \"$0.sig\"; exit 143' TERM\n"
            'echo 1 > "$0.trapped"\n'
            "while :; do sleep 0.05; done"
        ),
    )
    proc = subprocess.Popen([str(WRAPPER), str(upstream)], env=env)
    try:
        pid = int(_wait_for(tmp_path / "out" / "upstream.pid"))
        assert pid == proc.pid  # exec: the upstream supervisor kept the PID
        _wait_for(tmp_path / "upstream.trapped")  # trap installed before SIGTERM
        os.kill(proc.pid, signal.SIGTERM)
        assert proc.wait(timeout=10) == 143
        assert (tmp_path / "upstream.sig").read_text().strip() == "term"
    finally:
        if proc.poll() is None:
            proc.kill()


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
