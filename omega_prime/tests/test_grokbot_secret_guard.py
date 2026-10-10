# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Tests for the Grok Bot secret-redaction gate."""

import json
import os
from pathlib import Path

import pytest

from omega_prime.grokbot.secret_guard import (
    SecretLeakError,
    assert_no_secrets,
    enforce_token_file_mode,
)

#: Recognized-prefix fixture, assembled at runtime from a fixed suffix so the
#: suite never depends on randomness. Trips the ``omk`` pattern (suffix >= 8).
_FIXED_OMK_SUFFIX = "9f2Kd7QwX4mZ8VbN1"
_FIXED_OMK_TOKEN = "omk_" + _FIXED_OMK_SUFFIX

#: Fixed diverse lowercase-plus-digit run (all 26 letters + 10 digits).
#: Trips the generic high-entropy heuristic deterministically:
#: length 36, not hex/UUID, >4 distinct chars, two classes, high entropy.
_GENERIC_RUN = "abcdefghijklmnopqrstuvwxyz0123456789"
_GENERIC_PATH = "/cache/" + _GENERIC_RUN + "/manifest.json"

#: Fixed known-shape credentials (no randomness, no committed live values).
_FIXED_SK_TOKEN = "sk-" + "K7mQ2vX9bN4pL8wR6sT3yU1"
_FIXED_BEARER_TOKEN = "Bearer " + "C7fK9mQ2vXb4NpL8wRsT6yU"
_FIXED_PEM_BLOCK = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIBOgIBAAJBAK7mQ2vX9bN4pL8wR6sT3yU1vXb4NpL8wRsT6yU fixed body\n"
    "-----END RSA PRIVATE KEY-----"
)

#: Integrity metadata the gate must never flag.
_FIXED_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_FIXED_UUID = "123e4567-e89b-12d3-a456-426614174000"


def test_clean_text_passes():
    assert_no_secrets("manifest.json", "manifest")
    assert_no_secrets("diag bundle body: all checks passed", "diag")
    assert_no_secrets(
        "skill programming_desk_run_terminal_approval_flow ready",
        "diag",
    )
    assert_no_secrets(f"artifact digest {_FIXED_SHA256}", "manifest")
    assert_no_secrets(f"run id {_FIXED_UUID}", "manifest")


def test_clean_manifest_passes():
    manifest = {
        "manifest_version": "1.0.0",
        "bot": {"name": "Omega Prime"},
        "digest": _FIXED_SHA256,
        "run_id": _FIXED_UUID,
    }
    assert_no_secrets(json.dumps(manifest, sort_keys=True), "manifest")


def test_clean_0600_file_passes(tmp_path: Path):
    target = tmp_path / "manifest.json"
    target.write_text("nothing secret here\n", encoding="utf-8")
    target.chmod(0o600)
    assert_no_secrets(target, "manifest")


@pytest.mark.parametrize(
    "secret",
    [
        _FIXED_SK_TOKEN,
        _FIXED_OMK_TOKEN,
        _FIXED_BEARER_TOKEN,
        "Authorization: Bearer C7fK9mQ2vXb4NpL8wRsT6yU",
        "api_key=abcdefgh12345678",
        _FIXED_PEM_BLOCK,
        _GENERIC_RUN,
    ],
)
def test_token_bearing_text_raises_without_echo(secret: str):
    with pytest.raises(SecretLeakError) as excinfo:
        assert_no_secrets(f"diag bundle body: {secret}", "diag")
    message = str(excinfo.value)
    assert "token-pattern" in message
    assert "diag" in message
    for fragment in secret.split():
        if len(fragment) >= 8:
            assert fragment not in message


@pytest.mark.parametrize(
    "secret",
    [_FIXED_OMK_TOKEN, _FIXED_SK_TOKEN, _FIXED_BEARER_TOKEN, _GENERIC_RUN],
)
def test_token_bearing_file_contents_raise_despite_clean_name(
    tmp_path: Path, secret: str
):
    """Contents are scanned; a benign filename cannot launder secret bytes."""
    target = tmp_path / "notes.txt"
    target.write_text(f"clean header\n{secret}\n", encoding="utf-8")
    target.chmod(0o600)
    with pytest.raises(SecretLeakError) as excinfo:
        assert_no_secrets(target, "diag")
    message = str(excinfo.value)
    assert "token-pattern" in message
    for fragment in secret.split():
        if len(fragment) >= 8:
            assert fragment not in message


def test_same_file_str_rejects_token_pattern_while_path_accepts(tmp_path: Path):
    """The same real file discriminates str-as-text from Path-as-file."""
    target = tmp_path / (f"bundle-{_FIXED_OMK_TOKEN}.json")
    target.write_text("nothing secret here\n", encoding="utf-8")
    target.chmod(0o600)
    with pytest.raises(SecretLeakError) as excinfo:
        assert_no_secrets(os.fspath(target), "manifest")
    message = str(excinfo.value)
    assert "token-pattern" in message
    assert "manifest" in message
    assert _FIXED_OMK_TOKEN not in message
    assert os.fspath(target) not in message
    # Same file as Path: clean contents, not filename spelling, are scanned.
    assert_no_secrets(target, "manifest")


def test_same_file_mode_change_keeps_str_token_pattern(tmp_path: Path):
    target = tmp_path / (f"bundle-{_FIXED_OMK_TOKEN}.json")
    target.write_text("nothing secret here\n", encoding="utf-8")
    target.chmod(0o644)
    with pytest.raises(SecretLeakError) as excinfo:
        assert_no_secrets(target, "manifest")
    assert "0600-violation" in str(excinfo.value)
    # The str form still classifies text; it never reports a filesystem mode.
    with pytest.raises(SecretLeakError) as excinfo:
        assert_no_secrets(os.fspath(target), "manifest")
    assert "token-pattern" in str(excinfo.value)


def test_str_never_mode_checks_and_path_does(tmp_path: Path):
    """Str classifies fixed text without touching disk; Path enforces 0600."""
    target = tmp_path / "manifest.json"
    target.write_text("nothing secret here\n", encoding="utf-8")
    target.chmod(0o644)
    # Fixed text control: no tmp_dir bytes, no mode check, scanned as data.
    assert_no_secrets("manifest.json", "manifest")
    with pytest.raises(SecretLeakError) as excinfo:
        assert_no_secrets(target, "manifest")
    assert "0600-violation" in str(excinfo.value)


@pytest.mark.parametrize("mode", [0o400, 0o640, 0o644])
def test_wrong_mode_path_rejects(tmp_path: Path, mode: int):
    target = tmp_path / "token.env"
    target.write_text("nothing secret here\n", encoding="utf-8")
    target.chmod(mode)
    with pytest.raises(SecretLeakError) as excinfo:
        assert_no_secrets(target, "token-file")
    assert "0600-violation" in str(excinfo.value)
    with pytest.raises(SecretLeakError) as excinfo:
        enforce_token_file_mode(target)
    assert "0600-violation" in str(excinfo.value)


def test_wrong_mode_message_hides_contents(tmp_path: Path):
    target = tmp_path / "token.env"
    target.write_text(_FIXED_OMK_TOKEN + "\n", encoding="utf-8")
    target.chmod(0o640)
    with pytest.raises(SecretLeakError) as excinfo:
        enforce_token_file_mode(target)
    message = str(excinfo.value)
    assert "0600-violation" in message
    assert _FIXED_OMK_TOKEN not in message


def test_directory_and_missing_path_reject(tmp_path: Path):
    with pytest.raises(SecretLeakError) as excinfo:
        enforce_token_file_mode(tmp_path)
    assert "0600-violation" in str(excinfo.value)
    with pytest.raises(SecretLeakError) as excinfo:
        assert_no_secrets(tmp_path / "absent.env", "token-file")
    assert "0600-violation" in str(excinfo.value)


def test_enforce_0600_returns_path(tmp_path: Path):
    target = tmp_path / "token.env"
    target.write_text("nothing secret here\n", encoding="utf-8")
    target.chmod(0o600)
    assert enforce_token_file_mode(target) == target


class _FspathWrapper(os.PathLike):
    """Minimal non-Path ``os.PathLike[str]`` around a real file."""

    def __init__(self, inner: Path):
        self._inner = inner

    def __fspath__(self) -> str:
        return os.fspath(self._inner)


def test_non_path_pathlike_follows_file_contract(tmp_path: Path):
    target = tmp_path / (f"bundle-{_FIXED_OMK_TOKEN}.json")
    target.write_text("nothing secret here\n", encoding="utf-8")
    target.chmod(0o600)
    wrapper = _FspathWrapper(target)
    # A PathLike wrapper scans contents/mode like Path ...
    assert_no_secrets(wrapper, "manifest")
    # ... while its str form classifies the token-shaped spelling as text.
    with pytest.raises(SecretLeakError) as excinfo:
        assert_no_secrets(os.fspath(wrapper), "manifest")
    assert "token-pattern" in str(excinfo.value)
    target.chmod(0o644)
    with pytest.raises(SecretLeakError) as excinfo:
        assert_no_secrets(_FspathWrapper(target), "manifest")
    assert "0600-violation" in str(excinfo.value)


def test_generic_slash_path_rejects_without_echo(tmp_path: Path):
    with pytest.raises(SecretLeakError) as excinfo:
        assert_no_secrets(_GENERIC_PATH, "diag")
    message = str(excinfo.value)
    assert "token-pattern" in message
    assert "diag" in message
    assert _GENERIC_RUN not in message
    assert _GENERIC_PATH not in message


def test_nonexistent_token_shaped_path_never_touches_disk(tmp_path: Path):
    """A str payload is data: rejection needs no filesystem access."""
    ghost = tmp_path / "cache" / _GENERIC_RUN / "manifest.json"
    assert not ghost.exists()
    with pytest.raises(SecretLeakError) as excinfo:
        assert_no_secrets(os.fspath(ghost), "diag")
    assert "token-pattern" in str(excinfo.value)
    assert not ghost.exists()


def test_serialized_path_field_rejects_without_echo():
    payload = json.dumps({"path": _GENERIC_PATH, "status": "cached"}, sort_keys=True)
    with pytest.raises(SecretLeakError) as excinfo:
        assert_no_secrets(payload, "diag-bundle")
    message = str(excinfo.value)
    assert "token-pattern" in message
    assert _GENERIC_RUN not in message
    assert _GENERIC_PATH not in message


def test_short_and_low_diversity_runs_pass():
    # Short recognized-prefix form: below the minimum suffix length.
    assert_no_secrets("provision omk_ab12 for review", "diag")
    # Short diverse run: below the minimum candidate length.
    assert_no_secrets("cache key abcdefgh0123456789", "diag")
    # Long but low-diversity run: padding/repeats are excluded.
    assert_no_secrets("filler " + "ab0123" * 8 + " end", "diag")


def test_qualifying_short_omk_prefix_rejects_without_entropy():
    """Recognized prefixes trip on shape alone, not on entropy."""
    short_token = "omk_" + "9f2Kd7Qw"
    with pytest.raises(SecretLeakError) as excinfo:
        assert_no_secrets(f"diag bundle body: {short_token}", "diag")
    message = str(excinfo.value)
    assert "token-pattern" in message
    assert short_token not in message
