# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Omega-side fixture contract for skills attachment; not loader proof."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from omega_prime.engine import mcp_bridge, skills_attach


def _catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...]
) -> Path:
    install = tmp_path / "install"
    root = install / "omega_prime" / "skills"
    for name in names:
        skill = root / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    monkeypatch.setattr(skills_attach, "install_root", lambda: install)
    monkeypatch.setattr(skills_attach, "checked_in_skills", lambda _root: names)
    return root


def _lease(work: Path, state: Path):
    return mcp_bridge.try_acquire_attachment(work, state, "skills-fixture")


def test_catalog_skills_are_linked_and_registered_on_existing_lease(
    tmp_path, monkeypatch
):
    """Catalog completeness and lease registration, independent of omp discovery."""
    names = ("omega-commands", "python")
    root = _catalog(tmp_path, monkeypatch, names)
    work = tmp_path / "work"
    work.mkdir()
    lease = _lease(work, tmp_path / "state")

    result = skills_attach.attach_skills(work, lease)

    assert result["status"] == "attached"
    assert [item["name"] for item in result["linked"]] == list(names)
    assert {item["name"] for item in result["linked"]}.isdisjoint(
        {"tempest-run", "blue-sentinel"}
    )
    for name in names:
        link = work / ".omp" / "skills" / name
        assert link.is_symlink()
        assert link.resolve() == root / name
        assert (link / "SKILL.md").is_file()
        assert f".omp/skills/{name}" in lease._artifacts
    assert ".omp/skills" in lease._artifacts
    assert result["sources"]["gsd"]["status"] == "not_linked_native"
    assert lease.release() == []
    assert not (work / ".omp").exists()


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_preexisting_skills_view_backs_off_without_writing_through(
    tmp_path, monkeypatch, kind
):
    """User-owned view directories and links are not replaced or traversed."""
    _catalog(tmp_path, monkeypatch, ("python",))
    work = tmp_path / "work"
    work.mkdir()
    lease = _lease(work, tmp_path / "state")
    user_target = tmp_path / "user-skills"
    user_target.mkdir()
    sentinel = user_target / "sentinel"
    sentinel.write_text("user-owned", encoding="utf-8")
    view = work / ".omp" / "skills"
    if kind == "directory":
        view.mkdir()
        (view / "keep").write_text("unchanged", encoding="utf-8")
        expected = "skills_view_preexisting"
    else:
        view.symlink_to(user_target, target_is_directory=True)
        expected = "skills_view_is_symlink"

    result = skills_attach.attach_skills(work, lease)

    assert result["status"] == "attach_failed"
    assert result["reason"] == expected
    assert sentinel.read_text(encoding="utf-8") == "user-owned"
    assert view.exists() or view.is_symlink()
    assert lease.release()
    assert view.exists() or view.is_symlink()


def test_name_collision_rolls_back_only_created_view(tmp_path, monkeypatch):
    """A destination collision leaves the user entry byte-identical."""
    _catalog(tmp_path, monkeypatch, ("python",))
    work = tmp_path / "work"
    work.mkdir()
    lease = _lease(work, tmp_path / "state")
    view = work / ".omp" / "skills"
    original = mcp_bridge._rename_noreplace

    def plant_user_file(src_dir, src, dst_dir, dst):
        original(src_dir, src, dst_dir, dst)
        if dst == "skills":
            (view / "python").write_text("user-owned", encoding="utf-8")

    monkeypatch.setattr(mcp_bridge, "_rename_noreplace", plant_user_file)
    result = skills_attach.attach_skills(work, lease)

    assert result["status"] == "attach_failed"
    assert (view / "python").read_text(encoding="utf-8") == "user-owned"
    assert view.is_dir()
    conflicts = lease.release()
    assert conflicts
    assert (view / "python").read_text(encoding="utf-8") == "user-owned"


def test_missing_catalog_manifest_rolls_back_registered_view(tmp_path, monkeypatch):
    """A failed prepare removes only the view artifacts it registered."""
    _catalog(tmp_path, monkeypatch, ("python",))
    monkeypatch.setattr(skills_attach, "checked_in_skills", lambda _root: ("missing",))
    work = tmp_path / "work"
    work.mkdir()
    lease = _lease(work, tmp_path / "state")

    result = skills_attach.attach_skills(work, lease)

    assert result["status"] == "attach_failed"
    assert result["linked"] == []
    assert not (work / ".omp" / "skills").exists()
    assert lease.release() == []
    assert not (work / ".omp").exists()


def test_lease_cleanup_preserves_replaced_skill_link_identity(tmp_path, monkeypatch):
    """Cleanup must not unlink a user replacement at a registered name."""
    _catalog(tmp_path, monkeypatch, ("python",))
    work = tmp_path / "work"
    work.mkdir()
    lease = _lease(work, tmp_path / "state")
    result = skills_attach.attach_skills(work, lease)
    assert result["status"] == "attached"
    link = work / ".omp" / "skills" / "python"
    replacement = link.with_name("replacement")
    replacement.write_text("user replacement", encoding="utf-8")
    replaced = replacement.stat(follow_symlinks=False)
    replacement.replace(link)

    conflicts = lease.release()

    assert conflicts
    assert stat.S_ISREG(replaced.st_mode)
    assert link.read_text(encoding="utf-8") == "user replacement"
