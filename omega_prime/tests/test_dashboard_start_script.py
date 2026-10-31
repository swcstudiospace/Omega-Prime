# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""The launcher runs the checkout that contains it."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_launcher_resolves_its_own_checkout_and_configured_interpreter(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "other checkout"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "dashboard-start.sh", checkout / "scripts")
    fake = tmp_path / "python"
    fake.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$PWD" "$PYTHONPATH" "$*"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = {
        "PATH": os.environ["PATH"],
        "OMEGA_PRIME_DASHBOARD_PYTHON": str(fake),
        "OMEGA_PRIME_DASHBOARD_PORT": "7499",
    }
    out = subprocess.run(
        ["bash", str(checkout / "scripts" / "dashboard-start.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.splitlines()
    real = str(checkout.resolve())
    assert out[0] == real
    assert out[1] == real
    assert out[2] == "-m omega_prime.dashboard --host 127.0.0.1 --port 7499"
