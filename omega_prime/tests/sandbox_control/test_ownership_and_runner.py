"""Ownership records under concurrency, teardown lifetime, and the real runner."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from omega_prime.sandbox_control.ownership import TEARDOWN_TTL_S, Ownership
from omega_prime.sandbox_control.provider import (
    SandboxControlError,
    subprocess_runner,
)

NAME = "ompsbx-demo-abc123"


def test_concurrent_updates_never_collide_or_lose_fields(tmp_path: Path) -> None:
    owned = Ownership(tmp_path)
    owned.record(NAME, "gb:00045")
    errors: list[BaseException] = []

    def hammer(i: int) -> None:
        try:
            for _ in range(50):
                owned.mark_teardown(NAME, uuid=f"u{i}")
                owned.clear_teardown(NAME)
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert owned.task_for(NAME) == "gb:00045"  # no writer dropped the task
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        f"{NAME}.json",
        f"{NAME}.lock",
    ]  # no temp files left behind


def test_teardown_mark_expires_and_can_be_cleared(tmp_path: Path) -> None:
    now = [1000.0]
    owned = Ownership(tmp_path, clock=lambda: now[0])
    owned.record(NAME, None)
    assert not owned.teardown(NAME)
    owned.mark_teardown(NAME, uuid="abc")
    assert owned.teardown(NAME) and owned.uuid_for(NAME) == "abc"
    now[0] += TEARDOWN_TTL_S + 1
    assert not owned.teardown(NAME)  # a stuck mark cannot hide real deaths
    owned.mark_teardown(NAME)
    owned.clear_teardown(NAME)
    assert not owned.teardown(NAME) and owned.uuid_for(NAME) == "abc"


def test_runner_keeps_only_a_bounded_tail(tmp_path: Path) -> None:
    run = subprocess_runner({"PATH": "/usr/bin:/bin"}, capture_bytes=1024)
    script = "import sys; sys.stdout.write('x' * 200000 + 'END'); sys.stderr.write('e')"
    result = run([sys.executable, "-c", script], None, 30)
    assert result.returncode == 0 and result.truncated
    assert len(result.stdout) == 1024 and result.stdout.endswith("END")
    assert result.stderr == "e"


def test_runner_passes_stdin_and_times_out(tmp_path: Path) -> None:
    run = subprocess_runner({"PATH": "/usr/bin:/bin"})
    echo = run([sys.executable, "-c", "import sys; print(sys.stdin.read())"], "hi", 30)
    assert echo.stdout == "hi\n" and not echo.truncated
    with pytest.raises(SandboxControlError, match="timeout"):
        run([sys.executable, "-c", "import time; time.sleep(30)"], None, 0.5)
    with pytest.raises(SandboxControlError, match="binary_missing"):
        run([str(tmp_path / "missing")], None, 5)
