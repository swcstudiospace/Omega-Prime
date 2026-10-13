# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""The RoE in-chat signing UX: create, sign, and check commands.

Hermetic: the reports dir always points at ``tmp_path`` and the signing
key is a random hex sentinel written to a tmp key file. No sockets open.
Key hygiene is asserted on every path: the sentinel never appears in
stdout or in any result dict.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from omega_prime.commands import (
    COMMANDS,
    command_catalog,
    execute_command,
    redteam_roe_check,
    redteam_roe_create,
    redteam_roe_sign,
)
from omega_prime.redteam.roe import (
    REQUIRED_KEYS,
    authorize,
    validate_schema,
    validate_targets,
)

REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"
ROE_FILE_ENV_VAR = "OMEGA_PRIME_REDTEAM_ROE_FILE"
ROE_KEY_ENV_VAR = "OMEGA_PRIME_REDTEAM_ROE_KEY"
ROE_KEY_FILE_ENV_VAR = "OMEGA_PRIME_REDTEAM_ROE_KEY_FILE"


def _key_file(tmp_path: Path) -> tuple[Path, str]:
    """Random hex key sentinel on disk; tests assert it never leaks."""
    sentinel = secrets.token_hex(32)
    path = tmp_path / "roe.key"
    path.write_text(sentinel + "\n", encoding="utf-8")
    return path, sentinel


def _reports_env(monkeypatch: Any, tmp_path: Path) -> dict[str, str]:
    reports = tmp_path / "reports"
    monkeypatch.setenv(REPORTS_ENV_VAR, str(reports))
    return {REPORTS_ENV_VAR: str(reports)}


def _create(
    monkeypatch: Any, tmp_path: Path, key_path: Path, days: str = "7"
) -> dict[str, Any]:
    env = _reports_env(monkeypatch, tmp_path)
    return redteam_roe_create.run(
        f"--operator alice --days {days} --key-file {key_path}", env
    )


def _read_artifact(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(Path(result["artifact"]).read_text(encoding="utf-8"))


def _assert_hygiene(sentinel: str, payloads: list[Any], captured: Any) -> None:
    assert sentinel not in captured.out
    assert sentinel not in captured.err
    for payload in payloads:
        assert sentinel not in str(payload)


def test_catalog_registers_roe_commands_with_exact_usage() -> None:
    by_name = {spec.name: spec for spec in COMMANDS}
    for module in (redteam_roe_create, redteam_roe_sign, redteam_roe_check):
        assert by_name[module.NAME].usage == module.USAGE
    assert by_name["omega-redteam-roe-create"].needs_args is True
    assert by_name["omega-redteam-roe-sign"].needs_args is True
    assert by_name["omega-redteam-roe-check"].needs_args is False
    catalog = {row["name"]: row for row in command_catalog()}
    for module in (redteam_roe_create, redteam_roe_sign, redteam_roe_check):
        assert catalog[module.NAME]["usage"] == module.USAGE


def test_create_sign_check_round_trip(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    key_path, sentinel = _key_file(tmp_path)
    created = _create(monkeypatch, tmp_path, key_path)
    document = _read_artifact(created)
    for required in REQUIRED_KEYS:
        assert required in document
    assert document["operator"] == "alice"
    assert document["schema_version"] == 1
    assert document["authorized_targets"] == [{"host": "127.0.0.1"}]
    assert validate_schema(document) == []
    assert validate_targets(document) == []
    start = datetime.fromisoformat(document["effective_from"])
    end = datetime.fromisoformat(document["effective_until"])
    assert end - start == timedelta(days=7)
    assert not Path(created["artifact"] + ".sig").exists()

    env = _reports_env(monkeypatch, tmp_path)
    signed = redteam_roe_sign.run(
        f"--artifact {created['artifact']} --key-file {key_path}", env
    )
    assert signed["artifact"] == created["artifact"]
    assert Path(signed["signature"]).exists()
    assert signed["signature"] == created["artifact"] + ".sig"
    assert ROE_FILE_ENV_VAR in signed["install"]
    assert created["artifact"] in signed["install"]

    decision = authorize(created["artifact"], key=sentinel.encode("utf-8"))
    assert decision.ok and decision.cause == "ok"

    check_env = {
        ROE_FILE_ENV_VAR: created["artifact"],
        ROE_KEY_FILE_ENV_VAR: str(key_path),
    }
    status = redteam_roe_check.run("", check_env)
    assert status["allowed"] is True
    assert status["cause"] == "ok"
    assert status["days_remaining"] in (6, 7)

    body = execute_command("/omega-redteam-roe-check", object(), {**env, **check_env})
    assert body["ok"] is True
    assert body["result"]["cause"] == "ok"
    captured = capsys.readouterr()
    _assert_hygiene(sentinel, [created, document, signed, status, body], captured)


def test_check_accepts_direct_key_env(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    key_path, sentinel = _key_file(tmp_path)
    created = _create(monkeypatch, tmp_path, key_path)
    env = _reports_env(monkeypatch, tmp_path)
    redteam_roe_sign.run(f"--artifact {created['artifact']} --key-file {key_path}", env)
    status = redteam_roe_check.run(
        "",
        {ROE_FILE_ENV_VAR: created["artifact"], ROE_KEY_ENV_VAR: sentinel},
    )
    assert status["allowed"] is True
    assert status["cause"] == "ok"
    _assert_hygiene(sentinel, [status], capsys.readouterr())


def test_create_rejects_bad_days(tmp_path: Path, monkeypatch: Any) -> None:
    key_path, _ = _key_file(tmp_path)
    env = _reports_env(monkeypatch, tmp_path)
    for days in ("0", "-3", "soon"):
        with pytest.raises(redteam_roe_create.Error) as excinfo:
            redteam_roe_create.run(
                f"--operator alice --days {days} --key-file {key_path}", env
            )
        assert excinfo.value.error == "invalid_days"
        assert excinfo.value.result["usage"] == redteam_roe_create.USAGE
    for args in (
        "--days 7 --key-file x",
        "--operator alice --key-file x",
        "--operator alice --days 7",
        "--operator alice --days 7 --key-file x --bogus 1",
    ):
        with pytest.raises(redteam_roe_create.Error):
            redteam_roe_create.run(args, env)


def test_expired_artifact_reports_roe_expired(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    key_path, sentinel = _key_file(tmp_path)
    created = _create(monkeypatch, tmp_path, key_path)
    document = _read_artifact(created)
    document["effective_until"] = (datetime.now(UTC) - timedelta(days=1)).isoformat(
        timespec="seconds"
    )
    Path(created["artifact"]).write_text(json.dumps(document), encoding="utf-8")
    env = _reports_env(monkeypatch, tmp_path)
    redteam_roe_sign.run(f"--artifact {created['artifact']} --key-file {key_path}", env)
    status = redteam_roe_check.run(
        "",
        {
            ROE_FILE_ENV_VAR: created["artifact"],
            ROE_KEY_FILE_ENV_VAR: str(key_path),
        },
    )
    assert status["allowed"] is False
    assert status["cause"] == "roe-expired"
    assert status["days_remaining"] is not None and status["days_remaining"] <= 0
    _assert_hygiene(sentinel, [status], capsys.readouterr())


def test_missing_artifact_reports_roe_missing(monkeypatch: Any, capsys: Any) -> None:
    status = redteam_roe_check.run("", {ROE_FILE_ENV_VAR: "", ROE_KEY_ENV_VAR: "x"})
    assert status["allowed"] is False
    assert status["cause"] == "roe-missing"
    assert status["days_remaining"] is None
    _assert_hygiene("x-unique-sentinel", [status], capsys.readouterr())


def test_sign_refuses_unreadable_key(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    key_path, sentinel = _key_file(tmp_path)
    created = _create(monkeypatch, tmp_path, key_path)
    env = _reports_env(monkeypatch, tmp_path)
    missing = tmp_path / "no-such.key"
    with pytest.raises(redteam_roe_sign.Error) as excinfo:
        redteam_roe_sign.run(
            f"--artifact {created['artifact']} --key-file {missing}", env
        )
    assert excinfo.value.error == "key_unreadable"
    assert sentinel not in str(excinfo.value.result)
    _assert_hygiene(sentinel, [created], capsys.readouterr())


def test_tampered_artifact_reports_roe_invalid(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    key_path, sentinel = _key_file(tmp_path)
    created = _create(monkeypatch, tmp_path, key_path)
    env = _reports_env(monkeypatch, tmp_path)
    redteam_roe_sign.run(f"--artifact {created['artifact']} --key-file {key_path}", env)
    document = _read_artifact(created)
    document["operator"] = "mallory"
    Path(created["artifact"]).write_text(json.dumps(document), encoding="utf-8")
    status = redteam_roe_check.run(
        "",
        {
            ROE_FILE_ENV_VAR: created["artifact"],
            ROE_KEY_FILE_ENV_VAR: str(key_path),
        },
    )
    assert status["allowed"] is False
    assert status["cause"] == "roe-invalid"
    _assert_hygiene(sentinel, [status], capsys.readouterr())


def test_non_loopback_targets_report_roe_invalid(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    key_path, sentinel = _key_file(tmp_path)
    created = _create(monkeypatch, tmp_path, key_path)
    document = _read_artifact(created)
    document["authorized_targets"] = [{"host": "203.0.11.7"}]
    assert validate_targets(document) != []
    Path(created["artifact"]).write_text(json.dumps(document), encoding="utf-8")
    env = _reports_env(monkeypatch, tmp_path)
    # Re-sign so the failure is the target scope, not the signature.
    redteam_roe_sign.run(f"--artifact {created['artifact']} --key-file {key_path}", env)
    decision = authorize(created["artifact"], key=sentinel.encode("utf-8"))
    assert decision.ok is False
    assert decision.cause == "roe-invalid"
    status = redteam_roe_check.run(
        "",
        {
            ROE_FILE_ENV_VAR: created["artifact"],
            ROE_KEY_FILE_ENV_VAR: str(key_path),
        },
    )
    assert status["cause"] == "roe-invalid"
    _assert_hygiene(sentinel, [status], capsys.readouterr())


def test_sign_reports_install_without_installing(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    key_path, sentinel = _key_file(tmp_path)
    created = _create(monkeypatch, tmp_path, key_path)
    env = _reports_env(monkeypatch, tmp_path)
    signed = redteam_roe_sign.run(
        f"--artifact {created['artifact']} --key-file {key_path}", env
    )
    assert f"export {ROE_FILE_ENV_VAR}={created['artifact']}" in signed["install"]
    assert str(key_path) in signed["install"]
    assert os.getenv(ROE_FILE_ENV_VAR, "") != created["artifact"]
    _assert_hygiene(sentinel, [signed], capsys.readouterr())
