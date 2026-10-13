"""Sidecar packaging exclusions: reports stay out of Docker and bootstrap.

The whole ``/sidecar`` tree — harness, RoE material, contracts, and the
``sidecar/reports/`` findings/transitions output — is excluded from the
Docker build context via the ``deploy.py`` generator (``.dockerignore``
is generated: change the generator first, then the output). The Bot
bootstrap path never touches the sidecar either, mirroring the existing
redteam invariant (``test_grokbot_bootstrap.py`` asserts redteam absent
from bootstrap args and directories).

Hermetic and offline: only committed text files and the in-repo
``deploy`` module are read.
"""

from __future__ import annotations

from pathlib import Path

from omega_prime.grokbot import deploy
from omega_prime.grokbot.manifest import find_repo_root

REPO = find_repo_root()

# Path-like references that would mean the bootstrap path ships, reads, or
# writes sidecar material. The bare word "sidecar" is deliberately NOT in
# this list: omega_prime/grokbot/oneclick.py uses it for the unrelated
# `fs` flock lockfile ("sidecar") convention, which predates the
# purple-team sidecar and must not trip this invariant.
SIDECAR_PATH_PATTERNS = (
    "sidecar/",
    "OMEGA_PRIME_SIDECAR",
    "sidecar/reports",
)

BOOTSTRAP_FILES = (
    Path("omega_prime") / "scripts" / "grokbot-1click.sh",
    Path("omega_prime") / "grokbot" / "oneclick.py",
)


def test_generator_excludes_sidecar():
    # Generator-first rule: the exclusion lives in _DOCKERIGNORE; the
    # committed .dockerignore below is rendered output, never hand-edited.
    assert "/sidecar" in deploy._DOCKERIGNORE
    assert "/redteam" in deploy._DOCKERIGNORE


def test_committed_dockerignore_excludes_sidecar():
    text = (REPO / ".dockerignore").read_text(encoding="utf-8")
    assert "regenerate instead of editing by hand" in text.splitlines()[0]
    lines = text.splitlines()
    assert "/sidecar" in lines
    assert "/redteam" in lines
    # No negation re-includes sidecar material into the image.
    assert not [line for line in lines if line.startswith("!") and "sidecar" in line]


def test_generator_output_agrees_with_committed_file(tmp_path):
    rendered = deploy.render("docker", tmp_path / "docker")
    names = [path.name for path in rendered]
    assert ".dockerignore" in names
    committed = (REPO / ".dockerignore").read_bytes()
    for path in rendered:
        if path.name == ".dockerignore":
            assert path.read_bytes() == committed
    # The /sidecar prefix rule covers the reports dir: findings and
    # transitions never ship in the image without a narrower rule.
    assert b"/sidecar" in committed


def test_bootstrap_has_no_sidecar_path_references():
    for rel in BOOTSTRAP_FILES:
        text = (REPO / rel).read_text(encoding="utf-8")
        hits = [pattern for pattern in SIDECAR_PATH_PATTERNS if pattern in text]
        assert not hits, f"{rel} references sidecar paths: {hits}"
