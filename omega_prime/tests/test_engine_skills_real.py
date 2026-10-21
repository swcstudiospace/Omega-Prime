# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Provider-free resolution proof using the installed omp skill-list loader."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from omega_prime.commands.generated import checked_in_skills
from omega_prime.engine.mcp_bridge import install_root


def _records(value: object) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_records(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_records(child))
    return found


def _names(value: object) -> set[str]:
    return {
        name
        for record in _records(value)
        if isinstance((name := record.get("name")), str)
    }


def _manifest_paths(value: object, name: str) -> list[Path]:
    return [
        Path(path)
        for record in _records(value)
        if record.get("name") == name
        and isinstance((path := record.get("filePath")), str)
    ]


def _list_skills(omp: str, project: Path, env: dict[str, str], filter_value: str):
    result = subprocess.run(
        [omp, "--skills", filter_value, "skill", "list", "--json"],
        cwd=project,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_installed_omp_skill_list_resolves_project_omega_gsd_and_user_skills(
    tmp_path: Path,
):
    """The installed loader resolves linked skills and preserves native GSD."""
    omp = shutil.which("omp")
    if omp is None:
        pytest.skip("installed omp CLI is required for the loader proof")

    home = tmp_path / "home"
    config = tmp_path / "config"
    home.mkdir()
    config.mkdir()
    agent_dir = home / ".omp"
    user_skills = agent_dir / "skills"
    user_skills.mkdir(parents=True)

    gsd_source = user_skills / "gsd-autonomous"
    gsd_source.mkdir()
    gsd_manifest = gsd_source / "SKILL.md"
    gsd_manifest.write_text(
        "---\nname: gsd-autonomous\ndescription: isolated native GSD fixture\n---\n",
        encoding="utf-8",
    )

    project = tmp_path / "project"
    view = project / ".omp" / "skills"
    view.mkdir(parents=True, mode=0o700)
    install = install_root()
    skill_root = install / "omega_prime" / "skills"
    for name in checked_in_skills(install):
        source = skill_root / name
        assert (source / "SKILL.md").is_file(), f"missing catalog manifest: {name}"
        (view / name).symlink_to(source.resolve(), target_is_directory=True)

    user_source = tmp_path / "user-skill"
    (user_source / "references").mkdir(parents=True)
    user_manifest = user_source / "SKILL.md"
    user_manifest.write_text(
        "\n".join(
            (
                "---",
                "name: user-planted",
                "description: local fixture",
                "---",
                "Read [the guide](references/guide.md).",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    guide = user_source / "references" / "guide.md"
    guide.write_text("Fixture guide.\n", encoding="utf-8")
    (view / "user-planted").symlink_to(user_source, target_is_directory=True)

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "PI_CODING_AGENT_DIR": str(agent_dir),
        "XDG_CONFIG_HOME": str(config),
    }

    resolved = _list_skills(omp, project, env, "*")
    names = _names(resolved)
    assert {"omega-commands", "python", "gsd-autonomous", "user-planted"} <= names

    for name in ("omega-commands", "python"):
        paths = _manifest_paths(resolved, name)
        expected = skill_root / name / "SKILL.md"
        assert any(path.resolve() == expected.resolve() for path in paths)
    gsd_paths = _manifest_paths(resolved, "gsd-autonomous")
    assert gsd_paths and all(
        path.resolve() == gsd_manifest.resolve() for path in gsd_paths
    )

    user_paths = _manifest_paths(resolved, "user-planted")
    assert any(path.resolve() == user_manifest.resolve() for path in user_paths)
    assert guide.is_file()
    assert user_paths[0].resolve().parent.joinpath("references/guide.md").is_file()

    shutil.rmtree(view)
    without_project = _list_skills(omp, project, env, "*")
    without_names = _names(without_project)
    assert "gsd-autonomous" in without_names
    assert not ({"omega-commands", "python", "user-planted"} & without_names)
