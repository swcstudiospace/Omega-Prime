# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""The agent data folder: one path for host and sandbox, and its migration."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from omega_prime.agent_state import (
    AGENT_STATE_ENV,
    agent_memory_dir,
    agent_path,
    agent_state_dir,
    legacy_layout,
    legacy_sources,
    migrate_agent_state,
    private_state_paths,
)
from omega_prime.engine.jobs import agent_data_dir
from omega_prime.mcp_server import default_registry

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROOT = OMEGA_PRIME.parent


def _dummy(tag: str) -> str:
    """A dummy credential value built at runtime, never stored as a literal."""
    return "-".join(("dummy", tag, "value"))


def _tree(base: Path) -> dict[str, str]:
    """Relative path -> file bytes (as text) for every file under ``base``."""
    out: dict[str, str] = {}
    for path in sorted(base.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(base))] = path.read_bytes().hex()
    return out


def _seed_state_root(state: Path) -> None:
    (state / "memory").mkdir(parents=True)
    (state / "memory" / "MEMORY.md").write_text("old fact\n", encoding="utf-8")
    (state / "memory" / "USER.md").write_text("old user\n", encoding="utf-8")
    db = sqlite3.connect(state / "sessions.db")
    db.execute("create table t (v text)")
    db.execute("insert into t values ('hist')")
    db.commit()
    db.close()
    (state / "sessions.db-wal").write_bytes(b"")
    (state / "intake.json").write_text('{"orders": []}', encoding="utf-8")
    (state / "events.ndjson").write_text('{"kind":"note"}\n', encoding="utf-8")
    (state / "packs.json").write_text("{}", encoding="utf-8")
    (state / "rlm").mkdir()
    (state / "rlm" / "s1.json").write_text("{}", encoding="utf-8")
    # Operator files that must never move.
    (state / "settings.json").write_text("{}", encoding="utf-8")
    (state / "secrets").mkdir()
    (state / "secrets" / "TOKEN").write_text("t", encoding="utf-8")
    (state / "dashboard").mkdir()
    (state / "dashboard" / "tasks.json").write_text("{}", encoding="utf-8")
    (state / "jobs" / "j1").mkdir(parents=True)
    (state / "jobs" / "j1" / "events.jsonl").write_text("", encoding="utf-8")
    (state / "ui-control").mkdir()
    (state / "mcp-attachments").mkdir()


# -- resolution --------------------------------------------------------------


def test_agent_folder_resolution_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert agent_state_dir({}) is None
    assert (
        agent_state_dir({}, home=tmp_path / "h")
        == tmp_path / "h" / ".omega-prime" / "agent"
    )
    assert agent_state_dir({"OMEGA_PRIME_STATE_DIR": "s"}) == tmp_path / "s" / "agent"
    explicit = agent_state_dir(
        {"OMEGA_PRIME_STATE_DIR": "s", AGENT_STATE_ENV: "a"}, home=tmp_path
    )
    assert explicit == tmp_path / "a"
    assert explicit is not None
    assert explicit.is_absolute()


def test_host_and_engine_name_the_same_folder(tmp_path):
    for env in (
        {"OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")},
        {AGENT_STATE_ENV: str(tmp_path / "data")},
        {
            "OMEGA_PRIME_STATE_DIR": str(tmp_path / "s"),
            AGENT_STATE_ENV: str(tmp_path / "d"),
        },
    ):
        assert agent_state_dir(env) == agent_data_dir(env)


def test_live_host_default_matches_the_engine_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OMEGA_PRIME_STATE_DIR", raising=False)
    monkeypatch.delenv(AGENT_STATE_ENV, raising=False)
    assert agent_state_dir({}, home=Path.home()) == agent_data_dir({})
    assert agent_data_dir({}) == tmp_path / ".omega-prime" / "agent"


# -- shared memory between host and sandboxed child --------------------------


def test_host_and_sandboxed_child_share_memory_history_and_intake(tmp_path):
    from omega_prime.engine import mcp_bridge

    state = tmp_path / "state"
    host_env = {"OMEGA_PRIME_STATE_DIR": str(state)}
    host = default_registry(ROOT, tmp_path / "home", env=host_env)
    added = json.loads(
        host.dispatch("memory", {"action": "add", "content": "shared fact"})
    )
    assert added.get("local_saved") is True, added

    work = tmp_path / "work"
    work.mkdir()
    attachment = mcp_bridge.try_acquire_attachment(
        work, state, "job-shared", agent_dir=agent_data_dir(host_env)
    )
    try:
        entry = json.loads((work / ".omp" / "mcp.json").read_text(encoding="utf-8"))[
            "mcpServers"
        ]["omega-prime"]
    finally:
        attachment.release()
    child_env = dict(entry["env"])
    assert child_env[AGENT_STATE_ENV] == str(state / "agent")
    child = default_registry(ROOT, tmp_path / "child-home", env=child_env)
    # The child appends to the same store the host wrote.
    child_added = json.loads(
        child.dispatch("memory", {"action": "add", "content": "child fact"})
    )
    assert child_added.get("local_saved") is True, child_added
    memory_text = (state / "agent" / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "shared fact" in memory_text and "child fact" in memory_text
    # Nothing was written beside the operator files.
    assert not (state / "memory").exists()
    assert not (state / "mcp-state").exists()

    from omega_prime.routines.desk_lead import desk_intake_path, host_intake_path

    assert (
        desk_intake_path(ROOT, state_dir=str(state)) == state / "agent" / "intake.json"
    )
    assert host_intake_path(child_env) == state / "agent" / "intake.json"
    assert host_intake_path(host_env) == state / "agent" / "intake.json"


# -- migration ---------------------------------------------------------------


def test_migration_moves_state_root_data_and_leaves_operator_files(tmp_path):
    state = tmp_path / "state"
    _seed_state_root(state)
    operator_before = {
        name: _tree(state / name) for name in ("secrets", "dashboard", "jobs")
    }
    data_before = {
        "memory/MEMORY.md": (state / "memory" / "MEMORY.md").read_bytes(),
        "sessions.db": (state / "sessions.db").read_bytes(),
        "intake.json": (state / "intake.json").read_bytes(),
        "rlm/s1.json": (state / "rlm" / "s1.json").read_bytes(),
    }
    agent = agent_state_dir({"OMEGA_PRIME_STATE_DIR": str(state)})
    assert agent is not None
    layout = legacy_layout(
        {"OMEGA_PRIME_STATE_DIR": str(state)},
        home=None,
        install_root=ROOT,
        work_root=ROOT,
    )
    assert layout["install_root"] is None  # a state-dir host never touches the tree
    report = migrate_agent_state(agent, **layout)
    assert not report.errors
    for rel, data in data_before.items():
        assert (agent / rel).read_bytes() == data
        assert not (state / rel).exists()
    assert (agent / "sessions.db-wal").exists()
    for name in ("events.ndjson", "packs.json"):
        assert (agent / name).is_file() and not (state / name).exists()
    # Operator files are untouched and never copied into the agent folder.
    assert (state / "settings.json").read_text(encoding="utf-8") == "{}"
    for name, tree in operator_before.items():
        assert _tree(state / name) == tree
        assert not (agent / name).exists()
    assert not (agent / "settings.json").exists()
    assert (state / "ui-control").is_dir() and (state / "mcp-attachments").is_dir()
    receipt = json.loads((agent / "MIGRATION.json").read_text(encoding="utf-8"))
    assert receipt and receipt[-1]["moved"]


def test_migration_is_idempotent(tmp_path):
    state = tmp_path / "state"
    _seed_state_root(state)
    agent = state / "agent"
    layout = legacy_layout(
        {"OMEGA_PRIME_STATE_DIR": str(state)},
        home=None,
        install_root=None,
        work_root=None,
    )
    first = migrate_agent_state(agent, **layout)
    assert first.moved
    snapshot = (_tree(state), _tree(agent))
    second = migrate_agent_state(agent, **layout)
    assert second.moved == [] and second.errors == []
    assert (_tree(state), _tree(agent)) == snapshot


def test_migration_never_overwrites_and_keeps_both_copies(tmp_path):
    state = tmp_path / "state"
    _seed_state_root(state)
    agent = state / "agent"
    (agent / "memory").mkdir(parents=True)
    (agent / "memory" / "MEMORY.md").write_text("newer fact\n", encoding="utf-8")
    (agent / "intake.json").write_text('{"newer": true}', encoding="utf-8")
    report = migrate_agent_state(
        agent, state_root=state, install_root=None, work_root=None
    )
    # The agent copy wins, the old copy stays where it was: no data is lost.
    assert (agent / "memory" / "MEMORY.md").read_text(
        encoding="utf-8"
    ) == "newer fact\n"
    assert (state / "memory" / "MEMORY.md").read_text(encoding="utf-8") == "old fact\n"
    assert (agent / "intake.json").read_text(encoding="utf-8") == '{"newer": true}'
    assert (state / "intake.json").read_text(encoding="utf-8") == '{"orders": []}'
    kept = {Path(item["from"]).name for item in report.kept}
    assert {"MEMORY.md", "intake.json"} <= kept
    # Non-clashing siblings still move.
    assert (agent / "memory" / "USER.md").read_text(encoding="utf-8") == "old user\n"
    assert not (state / "memory" / "USER.md").exists()


def test_dry_run_moves_nothing(tmp_path):
    state = tmp_path / "state"
    _seed_state_root(state)
    before = _tree(state)
    report = migrate_agent_state(state / "agent", state_root=state, dry_run=True)
    assert report.moved and report.dry_run
    assert _tree(state) == before
    assert not (state / "agent").exists()


def test_install_tree_layout_moves_only_store_files(tmp_path):
    install = tmp_path / "install"
    package = install / "omega_prime" / "memory"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# package\n", encoding="utf-8")
    (package / "MEMORY.md").write_text("tree fact\n", encoding="utf-8")
    (install / "omega_prime" / "sessions.db").write_bytes(b"db")
    desk = install / "omega_prime" / "state"
    desk.mkdir()
    (desk / "intake.json").write_text("{}", encoding="utf-8")
    home = tmp_path / "home"
    (home / ".omega-prime" / "mcp-state" / "memory").mkdir(parents=True)
    (home / ".omega-prime" / "mcp-state" / "memory" / "USER.md").write_text(
        "child user\n", encoding="utf-8"
    )
    (home / ".omega-prime" / "settings.json").write_text("{}", encoding="utf-8")
    agent = agent_state_dir({}, home=home)
    assert agent is not None
    layout = legacy_layout({}, home=home, install_root=install, work_root=install)
    report = migrate_agent_state(agent, **layout)
    assert not report.errors
    assert (agent / "memory" / "MEMORY.md").read_text(encoding="utf-8") == "tree fact\n"
    assert (agent / "memory" / "USER.md").read_text(encoding="utf-8") == "child user\n"
    assert (agent / "sessions.db").read_bytes() == b"db"
    assert (agent / "intake.json").is_file()
    # The Python package stays put; only its store files moved.
    assert (package / "__init__.py").is_file()
    assert not (package / "MEMORY.md").exists()
    assert (home / ".omega-prime" / "settings.json").is_file()
    assert not (agent / "settings.json").exists()


def test_host_registry_migrates_on_start_and_reads_the_moved_memory(tmp_path):
    state = tmp_path / "state"
    _seed_state_root(state)
    registry = default_registry(
        ROOT, tmp_path / "home", env={"OMEGA_PRIME_STATE_DIR": str(state)}
    )
    added = json.loads(
        registry.dispatch("memory", {"action": "add", "content": "new fact"})
    )
    assert added.get("local_saved") is True, added
    text = (state / "agent" / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "old fact" in text and "new fact" in text
    assert (state / "agent" / "memory" / "MEMORY.md").is_file()
    assert not (state / "memory" / "MEMORY.md").exists()
    assert (state / "secrets" / "TOKEN").is_file()


# -- compatibility read of unmoved data ---------------------------------------


def test_compat_read_uses_an_unmoved_writable_legacy_store(tmp_path):
    state = tmp_path / "state"
    (state / "memory").mkdir(parents=True)
    (state / "memory" / "MEMORY.md").write_text("stuck\n", encoding="utf-8")
    (state / "intake.json").write_text("{}", encoding="utf-8")
    agent = state / "agent"
    agent.mkdir()
    legacy = legacy_sources(state_root=state)
    assert (
        agent_path(agent, "intake.json", legacy["intake.json"]) == state / "intake.json"
    )
    assert agent_memory_dir(agent, legacy) == state / "memory"
    # Once the agent copy exists, it wins.
    (agent / "intake.json").write_text("{}", encoding="utf-8")
    assert (
        agent_path(agent, "intake.json", legacy["intake.json"]) == agent / "intake.json"
    )


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
def test_compat_read_skips_a_read_only_legacy_store(tmp_path):
    legacy_dir = tmp_path / "ro"
    legacy_dir.mkdir()
    (legacy_dir / "intake.json").write_text("{}", encoding="utf-8")
    legacy_dir.chmod(0o555)
    try:
        agent = tmp_path / "agent"
        agent.mkdir()
        assert agent_path(agent, "intake.json", [legacy_dir / "intake.json"]) == (
            agent / "intake.json"
        )
    finally:
        legacy_dir.chmod(0o755)


def test_private_state_paths_lists_only_existing_operator_files(tmp_path):
    state = tmp_path / "state"
    _seed_state_root(state)
    paths = private_state_paths([state])
    assert set(paths) == {
        state / "settings.json",
        state / "secrets",
        state / "dashboard",
    }
    assert state / "agent" not in paths


# -- Greptile PR 50 round 4: safe moves ---------------------------------------


def _db(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("create table t (v text)")
    db.execute("insert into t values (?)", (value,))
    db.commit()
    db.close()


def test_a_database_and_its_journal_move_together(tmp_path):
    state = tmp_path / "state"
    _db(state / "sessions.db", "host")
    _db(state / "mcp-state" / "sessions.db", "child")
    (state / "mcp-state" / "sessions.db-journal").write_bytes(b"child journal")
    agent = state / "agent"
    report = migrate_agent_state(agent, state_root=state)
    assert not report.errors
    # The first (host) database moved; the child's database keeps its own
    # journal beside it instead of meeting the host database.
    assert (agent / "sessions.db").is_file()
    assert not (agent / "sessions.db-journal").exists()
    assert (state / "mcp-state" / "sessions.db").is_file()
    assert (state / "mcp-state" / "sessions.db-journal").read_bytes() == (
        b"child journal"
    )
    db = sqlite3.connect(agent / "sessions.db")
    assert db.execute("select v from t").fetchall() == [("host",)]
    db.close()


def test_a_stray_destination_journal_blocks_the_database_move(tmp_path):
    state = tmp_path / "state"
    _db(state / "sessions.db", "host")
    agent = state / "agent"
    agent.mkdir(parents=True)
    (agent / "sessions.db-wal").write_bytes(b"stray")
    report = migrate_agent_state(agent, state_root=state)
    assert (state / "sessions.db").is_file()
    assert not (agent / "sessions.db").exists()
    assert any(Path(k["from"]).name == "sessions.db" for k in report.kept)


def test_a_record_created_during_the_move_is_never_replaced(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / "intake.json").write_text('{"old": true}', encoding="utf-8")
    agent = state / "agent"
    real_link = os.link

    def racing_link(src, dst, *args, **kwargs):
        # Another process writes the destination after the existence check.
        if Path(dst) == agent / "intake.json" and not Path(dst).exists():
            Path(dst).write_text('{"new": true}', encoding="utf-8")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", racing_link)
    report = migrate_agent_state(agent, state_root=state)
    assert (agent / "intake.json").read_text(encoding="utf-8") == '{"new": true}'
    assert (state / "intake.json").read_text(encoding="utf-8") == '{"old": true}'
    assert any(k.get("reason") == "already_exists" for k in report.kept)


def test_a_failed_cross_device_copy_leaves_no_partial_file(tmp_path, monkeypatch):
    import errno
    import shutil

    state = tmp_path / "state"
    state.mkdir()
    (state / "intake.json").write_text('{"orders": [1]}', encoding="utf-8")
    agent = state / "agent"

    def cross_device(src, dst, *args, **kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    def disk_full(reader, writer, *args, **kwargs):
        writer.write(b'{"ord')
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "link", cross_device)
    monkeypatch.setattr(shutil, "copyfileobj", disk_full)
    report = migrate_agent_state(agent, state_root=state)
    assert report.errors and not report.moved
    assert not (agent / "intake.json").exists()
    assert list(agent.glob(".*migrating*")) == []
    assert (state / "intake.json").read_text(encoding="utf-8") == '{"orders": [1]}'
    # The compatibility read keeps using the intact original.
    assert agent_path(agent, "intake.json", (state / "intake.json",)) == (
        state / "intake.json"
    )


def test_a_cross_device_copy_publishes_without_replacing(tmp_path, monkeypatch):
    import errno

    state = tmp_path / "state"
    state.mkdir()
    (state / "intake.json").write_text('{"orders": [2]}', encoding="utf-8")
    agent = state / "agent"
    real_link = os.link

    def no_link_from_source(src, dst, *args, **kwargs):
        if Path(src) == state / "intake.json":
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", no_link_from_source)
    report = migrate_agent_state(agent, state_root=state)
    assert not report.errors
    assert (agent / "intake.json").read_text(encoding="utf-8") == '{"orders": [2]}'
    assert not (state / "intake.json").exists()
    assert list(agent.glob(".*migrating*")) == []


def test_memory_files_stay_together_when_one_cannot_move(tmp_path):
    install = tmp_path / "install"
    package = install / "omega_prime" / "memory"
    package.mkdir(parents=True)
    (package / "MEMORY.md").write_text("tree fact\n", encoding="utf-8")
    profile = tmp_path / "profile.md"
    profile.write_text("tree user\n", encoding="utf-8")
    (package / "USER.md").symlink_to(profile)
    home = tmp_path / "home"
    agent = agent_state_dir({}, home=home)
    assert agent is not None
    layout = legacy_layout({}, home=home, install_root=install, work_root=install)
    migrate_agent_state(agent, **layout)
    # Neither file moved, so the memory folder still shows both.
    assert (package / "MEMORY.md").is_file()
    assert not (agent / "memory" / "MEMORY.md").exists()
    chosen = agent_memory_dir(agent, legacy_sources(**layout))
    assert chosen == package
    assert (chosen / "USER.md").read_text(encoding="utf-8") == "tree user\n"


def test_a_failed_memory_move_puts_the_moved_file_back(tmp_path, monkeypatch):
    import errno

    state = tmp_path / "state"
    install = tmp_path / "install"
    package = install / "omega_prime" / "memory"
    package.mkdir(parents=True)
    (package / "MEMORY.md").write_text("fact\n", encoding="utf-8")
    (package / "USER.md").write_text("user\n", encoding="utf-8")
    agent = state / "agent"
    real_link = os.link

    def fail_user(src, dst, *args, **kwargs):
        if Path(dst).name == "USER.md" and Path(dst).parent == agent / "memory":
            raise OSError(errno.EIO, "Input/output error")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", fail_user)
    report = migrate_agent_state(
        agent, state_root=None, install_root=install, work_root=install
    )
    assert report.errors
    assert (package / "MEMORY.md").read_text(encoding="utf-8") == "fact\n"
    assert (package / "USER.md").read_text(encoding="utf-8") == "user\n"
    assert not (agent / "memory" / "MEMORY.md").exists()


def test_read_only_install_reads_migrated_skills_from_the_agent_folder(tmp_path):
    from omega_prime.routines.nightly import writable_skills_root

    install = tmp_path / "omega_prime" / "skills"
    install.mkdir(parents=True)
    state = tmp_path / "state"
    (state / "skills" / "deploy").mkdir(parents=True)
    (state / "skills" / "deploy" / "SKILL.md").write_text("steps\n", encoding="utf-8")
    env = {"OMEGA_PRIME_STATE_DIR": str(state)}
    install.chmod(0o555)
    try:
        # Before the move the old folder is still used in place.
        assert writable_skills_root(install, env) == state / "skills"
        migrate_agent_state(state / "agent", state_root=state)
        assert writable_skills_root(install, env) == state / "agent" / "skills"
        assert (state / "agent" / "skills" / "deploy" / "SKILL.md").is_file()
        registry = default_registry(ROOT, tmp_path / "home", env=env)
        del registry
        assert not (state / "skills").exists()
    finally:
        install.chmod(0o755)


def test_secret_files_in_hidden_folders_are_passed_by_env(tmp_path):
    from omega_prime.engine.jobs import sandboxed_child_env

    state = tmp_path / "state"
    secrets = state / "secrets"
    secrets.mkdir(parents=True)
    token = secrets / "SUBSTRATE_TOKEN"
    substrate = _dummy("substrate")
    token.write_text(substrate + "\n", encoding="utf-8")
    token.chmod(0o600)
    loose = secrets / "X_API_TOKEN"
    loose.write_text(_dummy("loose") + "\n", encoding="utf-8")
    loose.chmod(0o644)
    implicit = secrets / "TELEGRAM_BOT_TOKEN"
    telegram = _dummy("telegram")
    implicit.write_text(telegram + "\n", encoding="utf-8")
    implicit.chmod(0o600)
    outside = tmp_path / "elsewhere" / "DISCORD_BOT_TOKEN"
    outside.parent.mkdir()
    outside.write_text(_dummy("discord") + "\n", encoding="utf-8")
    outside.chmod(0o600)
    direct = _dummy("direct")
    env = {
        "OMEGA_PRIME_STATE_DIR": str(state),
        "SUBSTRATE_TOKEN_FILE": str(token),
        "X_API_TOKEN_FILE": str(loose),
        "DISCORD_BOT_TOKEN_FILE": str(outside),
        "HINDSIGHT_API_KEY": direct,
        "HINDSIGHT_API_KEY_FILE": str(secrets / "HINDSIGHT_API_KEY"),
    }
    hidden = private_state_paths([state])
    child = sandboxed_child_env(env, hidden)
    # Resolved on the host, handed over by env (never argv or mcp.json).
    assert child["SUBSTRATE_TOKEN"] == substrate
    assert "SUBSTRATE_TOKEN_FILE" not in child
    assert child["TELEGRAM_BOT_TOKEN"] == telegram
    # An unreadable (group-readable) file is dropped, not a startup failure.
    assert "X_API_TOKEN" not in child and "X_API_TOKEN_FILE" not in child
    # A file the sandbox can still read keeps its path.
    assert child["DISCORD_BOT_TOKEN_FILE"] == str(outside)
    assert "DISCORD_BOT_TOKEN" not in child
    # A direct value still wins.
    assert child["HINDSIGHT_API_KEY"] == direct
    assert "HINDSIGHT_API_KEY_FILE" not in child
    # Without hidden paths nothing changes.
    plain = sandboxed_child_env(env, [])
    assert plain["SUBSTRATE_TOKEN_FILE"] == str(token)
    assert "SUBSTRATE_TOKEN" not in plain


def test_a_source_replaced_during_the_move_keeps_the_newer_file(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / "intake.json").write_text('{"old": true}', encoding="utf-8")
    agent = state / "agent"
    real_link = os.link

    def writer_saves_after_link(src, dst, *args, **kwargs):
        # A desk pass replaces the source (atomic save) right after the publish.
        result = real_link(src, dst, *args, **kwargs)
        if Path(dst) == agent / "intake.json":
            fresh = state / "intake.json.tmp"
            fresh.write_text('{"new": true}', encoding="utf-8")
            os.replace(fresh, state / "intake.json")
        return result

    monkeypatch.setattr(os, "link", writer_saves_after_link)
    report = migrate_agent_state(agent, state_root=state)
    # The newer save survives in place and the stale copy is withdrawn.
    assert (state / "intake.json").read_text(encoding="utf-8") == '{"new": true}'
    assert not (agent / "intake.json").exists()
    assert report.errors and not report.moved
    assert agent_path(agent, "intake.json", [state / "intake.json"]) == (
        state / "intake.json"
    )


def test_the_move_waits_for_the_intake_writer_lock(tmp_path):
    import fcntl
    import threading
    import time

    state = tmp_path / "state"
    state.mkdir()
    (state / "intake.json").write_text('{"old": true}', encoding="utf-8")
    lock = state / "intake.json.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    agent = state / "agent"
    done = threading.Event()

    def run() -> None:
        migrate_agent_state(agent, state_root=state)
        done.set()

    worker = threading.Thread(target=run)
    worker.start()
    try:
        time.sleep(0.3)
        # The writer still holds the lock: nothing has moved yet.
        assert not done.is_set()
        assert not (agent / "intake.json").exists()
        fresh = state / "intake.json.tmp"
        fresh.write_text('{"new": true}', encoding="utf-8")
        os.replace(fresh, state / "intake.json")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    worker.join(5)
    assert done.is_set()
    assert (agent / "intake.json").read_text(encoding="utf-8") == '{"new": true}'
    assert not (state / "intake.json").exists()


def test_orphan_sidecars_never_move_without_their_database(tmp_path):
    state = tmp_path / "state"
    child = state / "mcp-state"
    child.mkdir(parents=True)
    (state / "sessions.db-journal").write_bytes(b"stray")
    db = sqlite3.connect(child / "sessions.db")
    db.execute("create table t (v text)")
    db.execute("insert into t values ('child')")
    db.commit()
    db.close()
    agent = state / "agent"
    migrate_agent_state(agent, state_root=state)
    assert (state / "sessions.db-journal").read_bytes() == b"stray"
    assert (agent / "sessions.db").is_file()
    assert not (agent / "sessions.db-journal").exists()
    db = sqlite3.connect(agent / "sessions.db")
    assert db.execute("select v from t").fetchall() == [("child",)]
    db.close()


def test_memory_folder_with_half_a_set_defers_to_the_folder_with_both(tmp_path):
    agent = tmp_path / "agent"
    (agent / "memory").mkdir(parents=True)
    (agent / "memory" / "MEMORY.md").write_text("moved fact\n", encoding="utf-8")
    old = tmp_path / "old" / "memory"
    old.mkdir(parents=True)
    (old / "MEMORY.md").write_text("fact\n", encoding="utf-8")
    (old / "USER.md").write_text("user\n", encoding="utf-8")
    assert agent_memory_dir(agent, {"memory": [old]}) == old
    # Once the agent folder holds every file the old one has, it wins.
    (agent / "memory" / "USER.md").write_text("user\n", encoding="utf-8")
    assert agent_memory_dir(agent, {"memory": [old]}) == agent / "memory"


def test_nightly_with_only_home_uses_the_hosts_agent_folder(tmp_path):
    from omega_prime.routines.nightly import writable_skills_root

    install = tmp_path / "omega_prime" / "skills"
    install.mkdir(parents=True)
    home = tmp_path / "home"
    host_agent = agent_state_dir({}, home=home)
    assert host_agent is not None
    install.chmod(0o555)
    try:
        assert writable_skills_root(install, {"HOME": str(home)}) == (
            host_agent / "skills"
        )
    finally:
        install.chmod(0o755)


def test_a_cross_device_folder_move_keeps_a_skill_written_during_the_copy(
    tmp_path, monkeypatch
):
    import errno
    import shutil as _shutil

    state = tmp_path / "state"
    (state / "skills" / "deploy").mkdir(parents=True)
    (state / "skills" / "deploy" / "SKILL.md").write_text("v1\n", encoding="utf-8")
    agent = state / "agent"
    real_rename = os.rename
    real_copytree = _shutil.copytree

    def cross_device(src, dst, *args, **kwargs):
        if Path(src) == state / "skills":
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(src, dst, *args, **kwargs)

    def copy_then_writer_saves(src, dst, *args, **kwargs):
        result = real_copytree(src, dst, *args, **kwargs)
        if Path(src) != state / "skills":
            return result  # copytree recurses through this name
        # A nightly pass saves a new skill after the copy was read.
        (state / "skills" / "fresh").mkdir()
        (state / "skills" / "fresh" / "SKILL.md").write_text("new\n", encoding="utf-8")
        return result

    monkeypatch.setattr(os, "rename", cross_device)
    monkeypatch.setattr(_shutil, "copytree", copy_then_writer_saves)
    report = migrate_agent_state(agent, state_root=state)
    # The source stays whole, with the new skill, and nothing half-copied.
    assert report.errors and not report.moved
    assert (state / "skills" / "fresh" / "SKILL.md").read_text(encoding="utf-8") == (
        "new\n"
    )
    assert (state / "skills" / "deploy" / "SKILL.md").is_file()
    assert not (agent / "skills").exists()
    assert not agent.exists() or not list(agent.glob(".skills.migrating-*"))


def test_a_cross_device_folder_move_removes_only_copied_files(tmp_path, monkeypatch):
    import errno

    from omega_prime import agent_state

    state = tmp_path / "state"
    (state / "skills" / "deploy").mkdir(parents=True)
    (state / "skills" / "deploy" / "SKILL.md").write_text("v1\n", encoding="utf-8")
    agent = state / "agent"
    real_rename = os.rename
    real_remove = agent_state._remove_copied

    def cross_device(src, dst, *args, **kwargs):
        if Path(src) == state / "skills":
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(src, dst, *args, **kwargs)

    def writer_then_remove(root, copied):
        # Saved after the copy was published, before the source is cleared.
        (root / "late.md").write_text("late\n", encoding="utf-8")
        return real_remove(root, copied)

    monkeypatch.setattr(os, "rename", cross_device)
    monkeypatch.setattr(agent_state, "_remove_copied", writer_then_remove)
    report = migrate_agent_state(agent, state_root=state)
    assert not report.errors
    assert (agent / "skills" / "deploy" / "SKILL.md").read_text(encoding="utf-8") == (
        "v1\n"
    )
    assert not (state / "skills" / "deploy").exists()
    assert (state / "skills" / "late.md").read_text(encoding="utf-8") == "late\n"
    # The next start merges the late file in.
    monkeypatch.setattr(os, "rename", real_rename)
    migrate_agent_state(agent, state_root=state)
    assert (agent / "skills" / "late.md").is_file()
    assert not (state / "skills").exists()


def test_a_folder_move_holds_the_skill_writer_lock_for_the_whole_copy(
    tmp_path, monkeypatch
):
    import errno
    import fcntl
    import shutil as _shutil

    state = tmp_path / "state"
    (state / "skills" / "deploy").mkdir(parents=True)
    (state / "skills" / "deploy" / "SKILL.md").write_text("v1\n", encoding="utf-8")
    agent = state / "agent"
    real_rename = os.rename
    real_copytree = _shutil.copytree
    seen: list[bool] = []

    def cross_device(src, dst, *args, **kwargs):
        if Path(src) == state / "skills":
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(src, dst, *args, **kwargs)

    def copy_while_probing_the_lock(src, dst, *args, **kwargs):
        if Path(src) == state / "skills":
            # A save (which takes the same <folder>.lock) cannot start now.
            fd = os.open(state / "skills.lock", os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                seen.append(True)
            else:
                seen.append(False)
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "rename", cross_device)
    monkeypatch.setattr(_shutil, "copytree", copy_while_probing_the_lock)
    report = migrate_agent_state(agent, state_root=state)
    assert not report.errors
    assert seen == [True]
    assert (agent / "skills" / "deploy" / "SKILL.md").is_file()
    assert not (state / "skills").exists()


def test_a_save_that_resolved_the_old_skills_folder_lands_in_the_moved_one(tmp_path):
    from omega_prime.skills_runtime.manager import skill_manage

    state = tmp_path / "state"
    old = state / "skills"
    (old / "deploy").mkdir(parents=True)
    (old / "deploy" / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: Deploy it.\n---\nv1\n", encoding="utf-8"
    )
    agent = state / "agent"
    report = migrate_agent_state(agent, state_root=state)
    assert not report.errors and not old.exists()
    # A nightly pass still holding the pre-move path saves a new skill.
    result = json.loads(
        skill_manage(
            "create",
            "fresh",
            content="---\nname: fresh\ndescription: A new skill.\n---\nbody\n",
            skills_root=old,
        )
    )
    assert result["success"], result
    assert (agent / "skills" / "fresh" / "SKILL.md").is_file()
    assert not old.exists()


def test_saves_into_the_shipped_skills_folder_leave_no_lock_file(tmp_path, monkeypatch):
    from omega_prime.skills_runtime import manager

    shipped = tmp_path / "omega_prime" / "skills"
    shipped.mkdir(parents=True)
    monkeypatch.setattr(manager, "_SHIPPED_ROOT", shipped)
    result = json.loads(
        manager.skill_manage(
            "create",
            "fresh",
            content="---\nname: fresh\ndescription: A new skill.\n---\nbody\n",
            skills_root=shipped,
        )
    )
    assert result["success"], result
    assert not (shipped.parent / "skills.lock").exists()


def test_live_host_env_fills_the_default_agent_folder_only_for_a_live_host(tmp_path):
    from omega_prime.agent_state import live_host_env

    home = tmp_path / "home"
    assert AGENT_STATE_ENV not in live_host_env({})
    assert AGENT_STATE_ENV not in live_host_env({}, home=home)
    live = live_host_env({"HOME": str(home)})
    assert live[AGENT_STATE_ENV] == str(agent_state_dir({}, home=home))
    state = {"HOME": str(home), "OMEGA_PRIME_STATE_DIR": str(tmp_path / "s")}
    assert AGENT_STATE_ENV not in live_host_env(state)


def test_sse_launcher_host_opens_the_engine_agent_folder(tmp_path, monkeypatch):
    from omega_prime.grokbot import remote
    from omega_prime.grokbot.audit import GrokBotAuditTracer

    home = tmp_path / "home"
    seen: dict[str, str] = {}

    def spy(root, home_arg, **kwargs):
        seen.update(kwargs["env"])
        raise ValueError("stop after env")

    monkeypatch.setattr(remote, "load_runtime", spy)
    with pytest.raises(ValueError, match="stop after env"):
        remote.create_sse_app(
            ROOT,
            home=home,
            audit=GrokBotAuditTracer(tmp_path / "audit.jsonl"),
            env={"HOME": str(home)},
        )
    expected = agent_state_dir({}, home=home)
    assert seen[AGENT_STATE_ENV] == str(expected)
    # The engine child resolves the same folder from that env.
    assert agent_data_dir(seen) == expected
