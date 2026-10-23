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


def test_idmap_shims_keep_only_the_callers_single_id(tmp_path: Path) -> None:
    dest = tmp_path / "bin"
    subprocess.run(
        ["sh", str(SCRIPT), "install-shims", str(dest)],
        check=True,
        timeout=30,
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, time; os.unshare(os.CLONE_NEWUSER); time.sleep(30)",
        ]
    )
    try:
        deadline = time.monotonic() + 5
        uid_map = ""
        while time.monotonic() < deadline:
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
            except (subprocess.CalledProcessError, PermissionError):
                time.sleep(0.05)
                continue
            uid_map = _map_file(child.pid, "uid_map")
            if uid_map.strip():
                break
        else:
            pytest.skip("this kernel refused a single-ID user namespace")
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
    assert "OMEGA_CI_MOUNT_NOTIF" in text
    assert "/usr/bin/podman" in text
    subprocess.run(["sh", "-n", str(dest)], check=True, timeout=10)
    helper = ROOT / ".github" / "workflows" / "ci-podman-mount-notif.py"
    probe = subprocess.run(
        [
            sys.executable,
            str(helper),
            sys.executable,
            "-c",
            "import ctypes, os\n"
            "libc = ctypes.CDLL(None, use_errno=True)\n"
            "libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p]\n"
            "libc.mount.restype = ctypes.c_int\n"
            "rc = libc.mount(b'', b'/', None, 0x44000, None)\n"
            "if rc != 0:\n"
            "    raise SystemExit('remount ' + os.strerror(ctypes.get_errno()))\n"
            "rc = libc.mount(b'tmpfs', b'/no/such/ci-podman-dir', b'tmpfs', 0, None)\n"
            "if rc == 0:\n"
            "    raise SystemExit('a missing mount point was allowed')\n",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if (
        probe.returncode != 0
        and "seccomp user notification is unavailable" in probe.stderr
    ):
        pytest.skip(probe.stderr.strip())
    assert probe.returncode == 0, probe.stderr + probe.stdout
