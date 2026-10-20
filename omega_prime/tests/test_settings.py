from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import cast

import pytest

import omega_prime.settings as settings


@pytest.fixture
def git_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "worktree"
    root.mkdir()
    monkeypatch.setattr(
        settings, "_worktree_metadata", lambda: ((root,), frozenset({"tracked.json"}))
    )
    return tmp_path, root


def write_settings(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_missing_default_override_and_injected_paths_are_silent(
    git_guard, tmp_path: Path
) -> None:
    base, _ = git_guard
    home = base / "home"
    default = settings.load_settings({}, home=home)
    assert default.status == "missing"
    assert dict(default.effective_env) == {}

    override = write_settings(
        base / "override.json", {"SUBSTRATE_URL": "https://example.test"}
    )
    result = settings.load_settings(
        {"OMEGA_PRIME_SETTINGS_FILE": str(override)}, home=home
    )
    assert result.status == "loaded"
    assert result.effective_env["SUBSTRATE_URL"] == "https://example.test"

    injected = write_settings(
        base / "injected.json", {"HINDSIGHT_URL": "https://h.test"}
    )
    assert (
        settings.load_settings({}, path=injected).effective_env["HINDSIGHT_URL"]
        == "https://h.test"
    )
    assert (
        settings.load_settings(
            {}, home=home, path=home / ".omega-prime" / "settings.json"
        ).status
        == "missing"
    )


def test_exact_allowlist_strings_and_presence_precedence(git_guard) -> None:
    base, _ = git_guard
    path = write_settings(
        base / "settings.json",
        {
            "SUBSTRATE_URL": "from-file",
            "HINDSIGHT_URL": "h",
            "ULTRATHINK_ROOT": "/u",
            "ULTRATHINK_SHIP": "ship",
            "OMEGA_PRIME_STATE_DIR": "/state",
        },
    )
    env = {"SUBSTRATE_URL": "", "KEEP": "process"}
    result = settings.load_settings(env, path=path)
    assert result.status == "loaded"
    assert result.effective_env["SUBSTRATE_URL"] == ""
    assert result.effective_env["HINDSIGHT_URL"] == "h"
    assert set(result.loaded_names) == {
        "HINDSIGHT_URL",
        "ULTRATHINK_ROOT",
        "ULTRATHINK_SHIP",
        "OMEGA_PRIME_STATE_DIR",
    }
    assert env == {"SUBSTRATE_URL": "", "KEEP": "process"}


def test_unknown_keys_warn_and_are_ignored(git_guard) -> None:
    base, _ = git_guard
    path = write_settings(
        base / "settings.json", {"OTHER": "hidden", "SUBSTRATE_URL": "local"}
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = settings.load_settings({}, path=path)
    assert result.status == "loaded"
    assert result.effective_env == {"SUBSTRATE_URL": "local"}
    assert len(caught) == 1
    assert "OTHER" in str(caught[0].message)
    assert "hidden" not in str(caught[0].message)


@pytest.mark.parametrize(
    "secret_name",
    ["TOKEN", "MY_KEY", "CLIENT_SECRET", "PASSWORD", "PAT", "AUTH_HEADER"],
)
def test_secret_shaped_names_reject_atomically_without_value_disclosure(
    git_guard, secret_name: str
) -> None:
    base, _ = git_guard
    secret = "NEVER-DISCLOSE-SECRET-VALUE"
    path = write_settings(
        base / "settings.json", {"SUBSTRATE_URL": "partial", secret_name: secret}
    )
    result = settings.load_settings({"KEEP": "yes"}, path=path)
    assert result.status == "rejected"
    assert result.effective_env == {"KEEP": "yes"}
    assert secret_name in result.rejected_names
    assert secret not in repr(result) and secret not in str(result.reasons)


def test_non_string_allowed_value_and_duplicate_keys_reject_atomically(
    git_guard,
) -> None:
    base, _ = git_guard
    path = write_settings(
        base / "settings.json", {"SUBSTRATE_URL": "partial", "ULTRATHINK_ROOT": 3}
    )
    result = settings.load_settings({"KEEP": "yes"}, path=path)
    assert result.status == "rejected" and result.effective_env == {"KEEP": "yes"}
    duplicate = base / "duplicate.json"
    duplicate.write_text(
        '{"SUBSTRATE_URL":"one","SUBSTRATE_URL":"two"}', encoding="utf-8"
    )
    duplicate.chmod(0o600)
    malformed = settings.load_settings({"KEEP": "yes"}, path=duplicate)
    assert malformed.status == "malformed" and malformed.effective_env == {
        "KEEP": "yes"
    }


def test_malformed_json_rejects_atomically(git_guard) -> None:
    base, _ = git_guard
    path = base / "bad.json"
    path.write_text('{"SUBSTRATE_URL":"partial",', encoding="utf-8")
    path.chmod(0o600)
    result = settings.load_settings({"KEEP": "yes"}, path=path)
    assert result.status == "malformed"
    assert result.effective_env == {"KEEP": "yes"}
    assert "partial" not in repr(result)


def test_lexical_and_resolved_worktree_aliases_are_rejected(git_guard) -> None:
    base, root = git_guard
    lexical = write_settings(root / "settings.json", {})
    assert settings.load_settings({}, path=lexical).status == "rejected"
    outside = write_settings(base / "outside.json", {})
    alias = root / "alias.json"
    alias.symlink_to(outside)
    assert settings.load_settings({}, path=alias).status == "rejected"


def test_linked_worktree_pointer_and_tracked_path_metadata_are_honored(
    git_guard, monkeypatch
) -> None:
    base, root = git_guard
    linked = base / "linked"
    linked.mkdir()
    (linked / ".git").write_text(
        "gitdir: /isolated/git/worktrees/linked", encoding="utf-8"
    )
    candidate = write_settings(linked / "config.json", {})
    monkeypatch.setattr(
        settings, "_worktree_metadata", lambda: ((root, linked), frozenset())
    )
    assert settings.load_settings({}, path=candidate).status == "rejected"

    tracked = write_settings(root / "tracked.json", {})
    monkeypatch.setattr(
        settings, "_worktree_metadata", lambda: ((root,), frozenset({"tracked.json"}))
    )
    assert settings.load_settings({}, path=tracked).status == "rejected"


def test_symlink_to_worktree_and_nonregular_targets_are_rejected(git_guard) -> None:
    base, root = git_guard
    link = base / "unsafe-link.json"
    link.symlink_to(write_settings(root / "secret.json", {}))
    assert settings.load_settings({}, path=link).status == "rejected"
    assert settings.load_settings({}, path=root).status == "rejected"


def test_group_or_world_writable_target_is_rejected(git_guard) -> None:
    base, _ = git_guard
    path = write_settings(base / "loose.json", {"SUBSTRATE_URL": "not-loaded"})
    path.chmod(0o666)
    result = settings.load_settings({}, path=path)
    assert result.status == "rejected" and result.effective_env == {}


def test_safety_provenance_failure_fails_closed_without_causes(
    git_guard, monkeypatch
) -> None:
    base, _ = git_guard
    path = write_settings(base / "settings.json", {"SUBSTRATE_URL": "must-not-load"})

    def fail_metadata():
        raise RuntimeError("private provenance detail")

    monkeypatch.setattr(settings, "_worktree_metadata", fail_metadata)
    result = settings.load_settings({"KEEP": "yes"}, path=path)
    assert result.status == "rejected"
    assert result.effective_env == {"KEEP": "yes"}
    assert "private provenance detail" not in repr(result)
    assert "must-not-load" not in repr(result)
    assert "effective_env" not in repr(result)
    assert isinstance(result.effective_env, type(settings.MappingProxyType({})))
    with pytest.raises(TypeError):
        cast(dict[str, str], result.effective_env)["X"] = "y"


def test_open_identity_race_fails_closed(git_guard, monkeypatch) -> None:
    base, _ = git_guard
    target = write_settings(base / "settings.json", {"SUBSTRATE_URL": "must-not-load"})
    replacement = write_settings(
        base / "replacement.json", {"SUBSTRATE_URL": "also-not-load"}
    )
    real_open = os.open

    def raced_open(_path, flags, *args, **kwargs):
        return real_open(replacement, flags, *args, **kwargs)

    monkeypatch.setattr(settings.os, "open", raced_open)
    result = settings.load_settings({}, path=target)
    assert result.status == "rejected"
    assert result.effective_env == {}
    assert "must-not-load" not in repr(result)
