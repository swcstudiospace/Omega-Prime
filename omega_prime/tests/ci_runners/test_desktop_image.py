"""The Desktop image's agent site is complete: build the app from it alone.

Materialises exactly what the Dockerfile COPYs into ``/opt/omega-ci/site``
(minus what ``.dockerignore`` excludes) and runs the image's own
``smoke.sh`` against it with an isolated interpreter, so a module missing
from the image fails here instead of at the Desktop's first boot.
"""

from __future__ import annotations

import fnmatch
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ._runner import runner_root

REPO = Path(__file__).resolve().parents[3]
DESKTOP = REPO / "deploy" / "ci-runner-desktop"
SITE = "/opt/omega-ci/site"


def _copies() -> list[tuple[list[str], str]]:
    out: list[tuple[list[str], str]] = []
    for line in (DESKTOP / "Dockerfile").read_text().splitlines():
        if not line.startswith("COPY "):
            continue
        words = shlex.split(line, comments=True)
        assert not any(w.startswith("--") for w in words), line
        out.append((words[1:-1], words[-1]))
    return out


def _ignored() -> list[str]:
    rules = []
    for raw in (REPO / ".dockerignore").read_text().splitlines():
        rule = raw.strip()
        if rule and not rule.startswith("#") and not rule.startswith("!"):
            rules.append(rule.lstrip("/"))
    return rules


def _excluded(rel: str, rules: list[str]) -> bool:
    for rule in rules:
        bare = rule.removeprefix("**/")
        if fnmatch.fnmatch(rel, rule) or rel.startswith(rule.rstrip("/") + "/"):
            return True
        if rule.startswith("**/") and any(
            fnmatch.fnmatch(part, bare) for part in rel.split("/")
        ):
            return True
    return False


def _materialise(dest: Path) -> Path:
    rules = _ignored()
    site = dest / "site"
    copied = 0
    for sources, target in _copies():
        if not target.startswith(SITE):
            continue
        base = site / target.removeprefix(SITE).strip("/")
        for src in sources:
            path = REPO / src
            files = [path] if path.is_file() else sorted(path.rglob("*"))
            for file in files:
                rel = file.relative_to(REPO).as_posix()
                if not file.is_file() or _excluded(rel, rules):
                    continue
                inner = file.relative_to(path) if path.is_dir() else None
                out = (
                    base / inner
                    if inner is not None
                    else (base / file.name if target.endswith("/") else base)
                )
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, out)
                copied += 1
    assert copied, "the Dockerfile copies nothing into the agent site"
    return site


def _smoke(tmp_path: Path, site: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "OMEGA_CI_SITE": str(site),
        "OMEGA_CI_AGENT_RUNNER_ROOT": str(runner_root(tmp_path)),
        "OMEGA_CI_PYTHON": sys.executable,
        "TMPDIR": str(tmp_path),
    }
    return subprocess.run(
        ["sh", str(DESKTOP / "smoke.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_dockerfile_runs_the_smoke_check_and_excludes_tests() -> None:
    text = (DESKTOP / "Dockerfile").read_text()
    assert "/opt/omega-ci/smoke.sh uvicorn" in text
    assert "omega_prime/tests" in _ignored()
    assert (["omega_prime"], f"{SITE}/omega_prime") in _copies()


def test_agent_app_builds_from_the_image_site_alone(tmp_path: Path) -> None:
    site = _materialise(tmp_path)
    assert not (site / "omega_prime" / "tests").exists()
    assert (site / "omega_prime" / "credentials" / "broker.py").is_file()
    result = _smoke(tmp_path, site)
    assert result.returncode == 0, result.stderr[-2000:]
    assert "agent app built from" in result.stdout
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith("tmp")]
    assert leftovers == []  # the scratch token dir is always removed


@pytest.mark.parametrize(
    "missing",
    ["omega_prime/credentials/broker.py", "omega_prime/providers/base.py"],
)
def test_smoke_fails_when_the_broker_chain_is_missing(
    tmp_path: Path, missing: str
) -> None:
    """The pre-fix image copied only credentials/redact.py: this must fail."""
    site = _materialise(tmp_path)
    (site / missing).unlink()
    result = _smoke(tmp_path, site)
    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr
