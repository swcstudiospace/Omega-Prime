# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""`OMEGA_PRIME_STATE_DIR` moves the growth stores off a read-only source tree."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from omega_prime.grokbot.manifest import find_repo_root
from omega_prime.mcp_server import default_registry, load_runtime

REPO = find_repo_root()
STATE_ENV = "OMEGA_PRIME_STATE_DIR"


def _snapshot(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


def _remember(registry, content: str) -> None:
    result = json.loads(
        registry.dispatch(
            "memory", {"action": "add", "target": "memory", "content": content}
        )
    )
    assert result.get("ok") is True, result


def test_state_dir_redirects_memory_and_sessions(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "omega_prime").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "state"
    before = _snapshot(root)

    registry = default_registry(root, home, env={STATE_ENV: str(state)})
    _remember(registry, "state-dir sentinel")

    assert "state-dir sentinel" in (state / "agent" / "memory" / "MEMORY.md").read_text(
        encoding="utf-8"
    )
    assert (state / "agent" / "sessions.db").is_file()
    assert _snapshot(root) == before


def test_unset_state_dir_keeps_the_root_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "omega_prime").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()

    registry = default_registry(root, home, env={})
    _remember(registry, "root sentinel")

    memory = root / "omega_prime" / "memory" / "MEMORY.md"
    assert "root sentinel" in memory.read_text(encoding="utf-8")
    assert (root / "omega_prime" / "sessions.db").is_file()


def test_empty_state_dir_is_treated_as_unset(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "omega_prime").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()

    default_registry(root, home, env={STATE_ENV: ""})

    assert (root / "omega_prime" / "memory").is_dir()
    assert (root / "omega_prime" / "sessions.db").is_file()


def test_state_dir_through_load_runtime_on_a_copied_tree(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "omega_prime").mkdir(parents=True)
    shutil.copytree(
        REPO / "omega_prime" / "contracts", root / "omega_prime" / "contracts"
    )
    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "state"
    before = _snapshot(root)

    runtime = load_runtime(root, home, no_roster=True, env={STATE_ENV: str(state)})
    _remember(runtime.registry, "runtime sentinel")

    assert (state / "agent" / "memory" / "MEMORY.md").is_file()
    assert _snapshot(root) == before


def test_read_only_install_creates_state_skills_and_keeps_shipped_names(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    install = root / "omega_prime" / "skills"
    (install / "desk-gateway").mkdir(parents=True)
    (install / "desk-gateway" / "SKILL.md").write_text(
        "shipped body\n", encoding="utf-8"
    )
    install.chmod(0o555)
    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "state"
    before = _snapshot(root)
    try:
        registry = default_registry(root, home, env={STATE_ENV: str(state)})
        # Learned skills live in the agent data folder, not the state root.
        assert not (state / "skills").exists()
        assert (state / "agent" / "skills").is_dir()
        viewed = json.loads(registry.dispatch("skill_view", {"name": "desk-gateway"}))
        assert viewed["success"] is True
        assert viewed["content"] == "shipped body\n"
        blocked = json.loads(
            registry.dispatch(
                "skill_manage",
                {
                    "action": "create",
                    "name": "desk-gateway",
                    "content": (
                        "---\nname: desk-gateway\n"
                        "description: Hide the original.\n---\nnew\n"
                    ),
                },
            )
        )
        assert blocked == {
            "success": False,
            "error": "A skill named 'desk-gateway' already exists.",
        }
        assert not (state / "agent" / "skills" / "desk-gateway").exists()
        created = json.loads(
            registry.dispatch(
                "skill_manage",
                {
                    "action": "create",
                    "name": "deploy",
                    "content": "---\nname: deploy\ndescription: Deploy steps.\n---\nrun\n",
                },
            )
        )
        assert created["success"] is True
        assert (state / "agent" / "skills" / "deploy" / "SKILL.md").is_file()
        assert _snapshot(root) == before
    finally:
        install.chmod(0o755)
