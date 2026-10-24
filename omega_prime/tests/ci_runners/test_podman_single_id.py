# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""The omega-ci single-ID newuidmap/newgidmap shims write one mapping."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / ".github" / "workflows" / "ci-podman-single-id.sh"


def _map_file(pid: int, name: str) -> str:
    return Path(f"/proc/{pid}/{name}").read_text(encoding="utf-8")


def _spawn_user_namespace() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, time; os.unshare(os.CLONE_NEWUSER); time.sleep(30)",
        ]
    )


def _kernel_allows_single_id_map() -> bool:
    """Write the one-line map ourselves. A refusal here is the kernel, not the shim."""
    child = _spawn_user_namespace()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                Path(f"/proc/{child.pid}/setgroups").write_text(
                    "deny\n", encoding="utf-8"
                )
                Path(f"/proc/{child.pid}/uid_map").write_text(
                    f"0 {os.getuid()} 1\n", encoding="utf-8"
                )
                Path(f"/proc/{child.pid}/gid_map").write_text(
                    f"0 {os.getgid()} 1\n", encoding="utf-8"
                )
            except OSError:
                time.sleep(0.05)
                continue
            return _map_file(child.pid, "uid_map").split() == [
                "0",
                str(os.getuid()),
                "1",
            ]
        return False
    finally:
        child.kill()
        child.wait(timeout=5)


def test_idmap_shims_keep_only_the_callers_single_id(tmp_path: Path) -> None:
    dest = tmp_path / "bin"
    subprocess.run(
        ["sh", str(SCRIPT), "install-shims", str(dest)],
        check=True,
        timeout=30,
    )
    if not _kernel_allows_single_id_map():
        pytest.skip("this kernel refused a single-ID user namespace")
    child = _spawn_user_namespace()
    try:
        deadline = time.monotonic() + 5
        uid_ran = False
        while time.monotonic() < deadline and not uid_ran:
            try:
                subprocess.run(
                    [
                        str(dest / "newuidmap"),
                        str(child.pid),
                        "0",
                        str(os.getuid()),
                        "1",
                        "1",
                        "100000",
                        "65536",
                    ],
                    check=True,
                    timeout=5,
                )
                uid_ran = True
            except subprocess.CalledProcessError:
                time.sleep(0.05)
        assert uid_ran, "newuidmap failed after the kernel accepted a single-ID map"
        deadline = time.monotonic() + 5
        gid_ran = False
        while time.monotonic() < deadline and not gid_ran:
            try:
                subprocess.run(
                    [
                        str(dest / "newgidmap"),
                        str(child.pid),
                        "0",
                        str(os.getgid()),
                        "1",
                        "1",
                        "100000",
                        "65536",
                    ],
                    check=True,
                    timeout=5,
                )
                gid_ran = True
            except subprocess.CalledProcessError:
                time.sleep(0.05)
        assert gid_ran, "newgidmap failed after the kernel accepted a single-ID map"
        uid_map = _map_file(child.pid, "uid_map")
        assert uid_map.split() == ["0", str(os.getuid()), "1"]
        assert _map_file(child.pid, "gid_map").split() == ["0", str(os.getgid()), "1"]
        assert "100000" not in uid_map
    finally:
        child.kill()
        child.wait(timeout=5)


def test_engine_shim_enters_a_private_mount_namespace(tmp_path: Path) -> None:
    """vfs layer apply remounts / (MS_REC|MS_PRIVATE). That needs a mount ns."""
    dest = tmp_path / "podman"
    subprocess.run(
        ["sh", str(SCRIPT), "install-engine", str(dest)],
        check=True,
        timeout=30,
    )
    text = dest.read_text(encoding="utf-8")
    assert "exec /usr/bin/podman" in text
    subprocess.run(["sh", "-n", str(dest)], check=True, timeout=10)
