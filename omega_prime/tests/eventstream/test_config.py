from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from omega_prime.eventstream.config import EventStreamConfig
from omega_prime.eventstream.envelope import CloudEvent
from omega_prime.eventstream.errors import ConfigurationError
from omega_prime.eventstream.taxonomy import schema_uri_for_type


def secure_file(path: Path, contents: str) -> Path:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_secure_secret_read_and_reopen_after_atomic_rotation(tmp_path: Path) -> None:
    token_path = secure_file(tmp_path / "token", "first-token\n")
    config = EventStreamConfig(token_file=token_path)
    assert config.read_secret("token") == "first-token"

    replacement = secure_file(tmp_path / "replacement", "rotated-token\n")
    os.replace(replacement, token_path)
    assert config.read_secret("token") == "rotated-token"


def test_initial_secret_read_rejects_permissions_other_than_exact_0600(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("private-content", encoding="utf-8")
    token_path.chmod(0o640)
    config = EventStreamConfig(token_file=token_path)
    with pytest.raises(ConfigurationError) as error:
        config.read_secret("token")
    assert "private-content" not in str(error.value)


def test_reopened_insecure_replacement_is_rejected_without_leaking_contents(
    tmp_path: Path,
) -> None:
    token_path = secure_file(tmp_path / "token", "known-sensitive-token")
    config = EventStreamConfig(token_file=token_path)
    token_path.write_text("known-sensitive-token", encoding="utf-8")
    token_path.chmod(0o644)
    with pytest.raises(ConfigurationError) as error:
        config.read_secret("token")
    assert "known-sensitive-token" not in str(error.value)


def test_rejects_symlink_and_nonregular_credential_targets(tmp_path: Path) -> None:
    real_file = secure_file(tmp_path / "real", "sensitive")
    link = tmp_path / "link"
    link.symlink_to(real_file)
    with pytest.raises(ConfigurationError):
        EventStreamConfig(token_file=link).read_secret("token")

    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ConfigurationError):
        EventStreamConfig(token_file=directory)


def test_rejects_literal_secret_environment_and_argv_values() -> None:
    unrelated = EventStreamConfig.from_env({"GITHUB_TOKEN": "unrelated-value"})
    assert unrelated.broker_url == "pulsar://127.0.0.1:26650"
    with pytest.raises(ConfigurationError, match=r"\*_FILE"):
        EventStreamConfig.from_env({"OMEGA_EVENTSTREAM_TOKEN": "never-display-this"})
    with pytest.raises(ConfigurationError) as error:
        EventStreamConfig.from_env({}, argv=("--token", "never-display-this"))
    assert "never-display-this" not in str(error.value)
    with pytest.raises(ConfigurationError):
        EventStreamConfig.from_env({}, argv=("--token=never-display-this",))


def test_local_noauth_smoke_and_remote_tls_auth_policy(tmp_path: Path) -> None:
    local = EventStreamConfig()
    assert local.broker_url == "pulsar://127.0.0.1:26650"
    assert local.admin_url == "http://127.0.0.1:28080"

    with pytest.raises(ConfigurationError):
        EventStreamConfig(broker_url="pulsar://broker.example:6650")
    with pytest.raises(ConfigurationError):
        EventStreamConfig(admin_url="http://admin.example:8080")

    ca = tmp_path / "ca.pem"
    ca.write_text("certificate", encoding="utf-8")
    token = secure_file(tmp_path / "token", "token")
    secured = EventStreamConfig(
        broker_url="pulsar+ssl://broker.example:6651",
        admin_url="https://admin.example:8443",
        tls_ca_file=ca,
        token_file=token,
    )
    assert secured.tls_ca_file == ca
    with pytest.raises(ConfigurationError):
        EventStreamConfig(
            broker_url="pulsar+ssl://broker.example:6651",
            admin_url="https://admin.example:8443",
            tls_ca_file=ca,
        )


def test_from_env_converts_secure_file_paths_to_path_values(tmp_path: Path) -> None:
    token_path = secure_file(tmp_path / "token", "remote-token")
    ca_path = tmp_path / "ca.pem"
    ca_path.write_text("certificate", encoding="utf-8")
    config = EventStreamConfig.from_env(
        {
            "OMEGA_EVENTSTREAM_URL": "pulsar+ssl://broker.example:6651",
            "OMEGA_EVENTSTREAM_ADMIN_URL": "https://admin.example:8443",
            "OMEGA_EVENTSTREAM_TLS_CA_FILE": str(ca_path),
            "OMEGA_EVENTSTREAM_TOKEN_FILE": str(token_path),
        }
    )
    assert config.tls_ca_file == ca_path
    assert config.token_file == token_path


def test_invalid_secret_file_path_errors_remain_sanitized() -> None:
    with pytest.raises(ConfigurationError) as error:
        EventStreamConfig(token_file=Path("\x00"))
    assert "embedded null byte" not in str(error.value)


def test_limits_and_safe_repr_and_errors(tmp_path: Path) -> None:
    token_path = secure_file(
        tmp_path / "private-token-path", "tok_live_known-secret-value"
    )
    config = EventStreamConfig(
        token_file=token_path, outbox_max_bytes=1024, max_concurrency=4
    )
    assert "known-secret-value" not in repr(config)
    assert "private-token-path" not in repr(config)
    assert config.outbox_max_bytes == 1024
    assert config.max_concurrency == 4
    for field, value in (("outbox_max_bytes", 0), ("max_concurrency", 0)):
        with pytest.raises(ConfigurationError):
            EventStreamConfig(**{field: value})  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


def test_actual_redactor_copies_nested_values_and_redacts_event_bytes() -> None:
    secret = "tok_live_super-secret-123"
    original: dict[str, Any] = {
        "nested": [{"access_token": secret}, "sk-secretshape123456"]
    }
    config = EventStreamConfig()
    redacted = config.redact(original, known_secrets=(secret,))
    assert original["nested"][0]["access_token"] == secret
    assert secret not in json.dumps(redacted)
    assert "sk-secretshape123456" not in json.dumps(redacted)

    event_values: dict[str, Any] = {
        "specversion": "1.0",
        "id": "webhook-delivery-1",
        "source": "test://eventstream",
        "type": "omega.sdlc.pr.opened",
        "subject": "pr/2",
        "time": datetime(2026, 10, 23, tzinfo=UTC),
        "datacontenttype": "application/json",
        "dataschema": schema_uri_for_type("omega.sdlc.pr.opened"),
        "data": {
            "repo": "example/repo",
            "number": 2,
            "head_sha": "abc123",
            "actor": secret,
            "title": "Example change",
            "base_ref": "main",
        },
        "omegakey": "example/repo#2",
    }
    event = CloudEvent(**event_values)
    serialized = event.to_bytes(extra=(secret,))
    assert secret.encode() not in serialized
    assert secret not in repr(event)
