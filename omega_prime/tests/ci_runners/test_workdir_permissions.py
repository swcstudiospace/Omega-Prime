"""00055: runner work directories are listable, and runner output is logged.

Smoke run 39999949179 failed because every JIT runner exited 0 within a
second: actions/runner requires read permission on its directory and every
directory above it, and the image made the work root 0711. These tests pin
the modes (image and ``Supervisor.prepare``), the startup chain check, and the
bounded, redacted relay of runner stdout/stderr and ``_diag`` tails.

``OMEGA_CI_TEST_RUNNER_ROOT`` (an extracted actions/runner release) turns on
a test that runs the real runner's directory check as this non-root user.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

from omega_prime.ci_runners.agent import supervisor as sup
from omega_prime.ci_runners.agent.supervisor import (
    REDACTED,
    UnsafeWorkdir,
    build_workdir,
    child_env,
    diag_tail,
    ensure_listable_chain,
    redact_runner_line,
)

from ._runner import JIT, Exits, runner_root, supervisor
from ._secrets import placeholder, provider_shaped

REPO = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO / "deploy" / "ci-runner-desktop" / "Dockerfile"
NAME = "omega-ci-101-abcd"
DIR_ERROR = "Permission to read the directory contents is required"


# --- modes -------------------------------------------------------------------


def _dockerfile_runs() -> str:
    return DOCKERFILE.read_text().replace("\\\n", " ")


def test_image_work_root_and_parent_are_listable() -> None:
    text = _dockerfile_runs()
    assert "chmod 0711" not in text
    assert re.search(r"chmod 0755 /var/lib/omega-ci /var/lib/omega-ci/work\b", text)
    assert "chown root:root /var/lib/omega-ci /var/lib/omega-ci/work" in text


def test_image_build_runs_the_real_runner_check_as_the_job_user() -> None:
    text = _dockerfile_runs()
    [check] = [line for line in text.splitlines() if "omega-ci-build-check" in line]
    assert 'setpriv --reuid="${JOB_UID}" --regid="${JOB_GID}" --clear-groups' in check
    assert "./run.sh" in check and "Not configured" in check
    assert "grep -q 'Permission to read the directory contents'" in check
    assert "&& ! printf" in check  # the directory error fails the build
    assert 'rm -rf "$check"' in check  # the probe tree never ships


def test_prepare_makes_the_work_root_0755_and_runner_dirs_stay_0700(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    s = supervisor(tmp_path, runner_root(tmp_path), Exits())
    s.prepare()
    assert stat.S_IMODE(work.stat().st_mode) == 0o755
    build_workdir(runner_root(tmp_path / "r"), work / NAME, None, None)
    assert stat.S_IMODE((work / NAME).stat().st_mode) == 0o700
    for scratch in ("_work", "_diag"):
        assert stat.S_IMODE((work / NAME / scratch).stat().st_mode) == 0o700


def test_prepare_checks_the_chain_when_jobs_run_as_another_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []
    monkeypatch.setattr(sup, "ensure_listable_chain", seen.append)
    s = supervisor(
        tmp_path, runner_root(tmp_path), Exits(), job_uid=65534, job_gid=65534
    )
    s.prepare()
    assert seen == [tmp_path / "work"]


def test_prepare_fails_closed_on_an_unlistable_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "omega-ci"
    parent.mkdir(mode=0o755)
    parent.chmod(0o711)  # the 00055 image mode
    real = ensure_listable_chain
    monkeypatch.setattr(
        sup, "ensure_listable_chain", lambda path: real(path, top=tmp_path)
    )
    s = sup.Supervisor(
        runner_root=runner_root(tmp_path),
        work_root=parent / "work",
        max_runners=1,
        notify=Exits(),
        job_uid=65534,
        job_gid=65534,
        base_env={},
    )
    try:
        with pytest.raises(UnsafeWorkdir, match="0711 is not listable"):
            s.prepare()
    finally:
        parent.chmod(0o755)


def test_listable_chain_accepts_0755_and_rejects_0711(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)
    leaf = tmp_path / "a" / "b"
    leaf.mkdir(parents=True)
    for d in (tmp_path / "a", leaf):
        d.chmod(0o755)
    ensure_listable_chain(leaf, top=tmp_path)
    (tmp_path / "a").chmod(0o711)
    try:
        with pytest.raises(UnsafeWorkdir, match=str(tmp_path / "a")):
            ensure_listable_chain(leaf, top=tmp_path)
    finally:
        (tmp_path / "a").chmod(0o755)


# --- redaction ---------------------------------------------------------------


def test_redaction_hides_secret_shapes_and_long_blobs_but_keeps_paths() -> None:
    token = provider_shaped("ghs_")
    jit = secrets.token_urlsafe(900)  # a real JIT config is ~1-2 KB base64
    path = "/var/lib/omega-ci/work/omega-ci-120573143952-k7xq/_work/_temp"
    line = f"x {token} y {jit} at '{path}' Authorization: Bearer abc.def"
    out = redact_runner_line(line, cap=10_000)
    assert token not in out and jit not in out and "abc.def" not in out
    assert path in out and out.count(REDACTED) >= 3
    assert len(redact_runner_line("z" * 5000)) <= sup.LOG_LINE_CAP + 1
    assert redact_runner_line("a\x1b[31mb\x00c") == "a[31mbc"


def test_diag_tail_is_bounded_redacted_and_never_follows_symlinks(
    tmp_path: Path,
) -> None:
    diag = tmp_path / "_diag"
    diag.mkdir()
    token = provider_shaped("ghp_")
    body = "".join(f"line {i}\n" for i in range(5000)) + f"leak {token}\nlast\n"
    (diag / "Runner_1.log").write_text(body)
    tail = diag_tail(tmp_path)
    assert tail[-1] == "last" and len(tail) <= sup.DIAG_TAIL_LINES
    assert all(token not in t for t in tail) and f"leak {REDACTED}" in tail
    outside = tmp_path / "outside.log"
    outside.write_text("secret elsewhere\n")
    shutil.rmtree(diag)
    diag.mkdir()
    (diag / "Runner_2.log").symlink_to(outside)
    assert diag_tail(tmp_path) == []
    assert diag_tail(tmp_path / "missing") == []


def test_diag_tail_never_blocks_on_a_fifo_swapped_in_after_the_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job can replace the checked log with a FIFO before the agent opens it."""
    diag = tmp_path / "_diag"
    diag.mkdir()
    log_file = diag / "Runner_1.log"
    log_file.write_text("hello\n")
    real_open = os.open

    def swap_then_open(path: str, flags: int, *a: object) -> int:
        if path == str(log_file):
            os.remove(log_file)
            os.mkfifo(log_file)  # no writer: a blocking open never returns
        return real_open(path, flags, *a)

    monkeypatch.setattr(sup.os, "open", swap_then_open)
    result: list[list[str]] = []
    worker = threading.Thread(
        target=lambda: result.append(diag_tail(tmp_path)), daemon=True
    )
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "diag_tail blocked on a FIFO"
    assert result == [[]]


# --- the relay, with real children --------------------------------------------


def _run_one(tmp_path: Path, tail: str, jit: str = JIT) -> list[dict[str, object]]:
    exits = Exits()

    async def main() -> None:
        s = supervisor(tmp_path, runner_root(tmp_path, tail=tail), exits)
        s.prepare()
        s.admit(NAME, "g", jit)
        await exits.wait()

    asyncio.run(main())
    return exits.payloads


def test_runner_output_reaches_the_agent_log_redacted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    jit = placeholder("jit") + secrets.token_hex(64)
    token = provider_shaped("ghs_")
    tail = (
        "echo 'An error occurred: Permission to read the directory contents is"
        " required'\n"
        'echo "jit=$ACTIONS_RUNNER_INPUT_JITCONFIG" >&2\n'
        f"echo 'auth {token}'\n"
        "mkdir -p _diag && echo 'diag says hello' > _diag/Runner_1.log\n"
        "exit 0"
    )
    caplog.set_level(logging.INFO, logger=sup.__name__)
    [payload] = _run_one(tmp_path, tail, jit)
    assert payload["exit_code"] == 0
    text = caplog.text
    assert f"runner {NAME}: An error occurred: {DIR_ERROR}" in text
    assert "before taking a job" in text and "diag: diag says hello" in text
    assert jit not in text and token not in text and JIT not in text


def test_runner_that_took_a_job_skips_the_diag_dump(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    tail = (
        "echo 'Running job: build'\n"
        "mkdir -p _diag && echo 'diag noise' > _diag/Runner_1.log\nexit 0"
    )
    caplog.set_level(logging.INFO, logger=sup.__name__)
    _run_one(tmp_path, tail)
    assert "Running job: build" in caplog.text
    assert "before taking a job" not in caplog.text


def test_runner_output_is_bounded_and_the_pipe_is_drained(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    tail = 'i=0; while [ $i -lt 5000 ]; do echo "out $i"; i=$((i+1)); done\nexit 0'
    caplog.set_level(logging.INFO, logger=sup.__name__)
    [payload] = _run_one(tmp_path, tail)
    assert payload["exit_code"] == 0
    relayed = [
        r for r in caplog.records if r.getMessage().startswith(f"runner {NAME}: out ")
    ]
    assert len(relayed) == sup.LOG_MAX_LINES
    assert "further output not logged" in caplog.text


# --- the real actions/runner (opt-in) ----------------------------------------


REAL_ROOT = os.environ.get("OMEGA_CI_TEST_RUNNER_ROOT", "")


@pytest.mark.skipif(
    not REAL_ROOT or os.geteuid() == 0,
    reason="needs OMEGA_CI_TEST_RUNNER_ROOT (extracted actions/runner) and non-root",
)
def test_real_runner_passes_its_directory_check_in_a_prepared_work_root(
    tmp_path: Path,
) -> None:
    """Real ``run.sh`` as this non-root user, in a tree laid out by the agent.

    With the 00055 modes it gets past the directory check and stops at "Not
    configured" (no JIT config given). With an unreadable ancestor it prints
    the smoke-run error and still exits 0, which is why the scaler now backs
    off on any pre-job exit.
    """
    lib = tmp_path / "omega-ci"
    lib.mkdir(mode=0o755)
    s = sup.Supervisor(
        runner_root=Path(REAL_ROOT),
        work_root=lib / "work",
        max_runners=1,
        notify=Exits(),
        base_env={},
    )
    s.prepare()
    workdir = lib / "work" / NAME
    build_workdir(Path(REAL_ROOT), workdir, None, None)
    env = child_env({"PATH": "/usr/bin:/bin"}, "", workdir)
    env.pop(sup.JIT_ENV)

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(workdir / "run.sh")],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    ok = run()
    assert DIR_ERROR not in ok.stdout + ok.stderr
    assert "Not configured" in ok.stdout + ok.stderr
    lib.chmod(0o311)  # owner may search but not read: the 0711 failure mode
    try:
        bad = run()
    finally:
        lib.chmod(0o755)
    assert DIR_ERROR in bad.stdout + bad.stderr and bad.returncode == 0


def test_leftover_process_holding_the_output_pipe_cannot_hold_the_slot(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """run.sh exits but leaves a helper with its stdout open (Greptile P1).

    The exit must still be reported within about one grace period, and the
    helper (same session) is stopped.
    """
    pidfile = tmp_path / "helper.pid"
    tail = f"sleep 60 &\necho $! > '{pidfile}'\necho started\nexit 0"
    caplog.set_level(logging.INFO, logger=sup.__name__)
    started = time.monotonic()
    [payload] = _run_one(tmp_path, tail)
    assert payload["exit_code"] == 0
    assert time.monotonic() - started < 10
    assert "output stayed open" in caplog.text
    helper = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(helper, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(helper, 9)
        raise AssertionError("leftover helper was not stopped")
