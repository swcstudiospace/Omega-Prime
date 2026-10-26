"""Unit tests for the owned secure Pulsar CI helper (no broker or docker)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_HELPER = Path(__file__).resolve().parents[2] / "scripts" / "eventstream_it.py"


@pytest.fixture(scope="module")
def helper() -> Any:
    spec = importlib.util.spec_from_file_location("eventstream_it_helper", _HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_failed_command_reports_argv_and_redacted_stderr(
    helper: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Built at runtime so secret scanners never see a credential-shaped literal.
    jwt = ".".join(("eyJ" + "a" * 12, "b" * 16, "c" * 16))
    pem_label = "PRIVATE " + "KEY"
    stderr = (
        "mkdir: cannot create directory '/pulsar/ci-secrets': Permission denied\n"
        f"Authorization: Bearer {jwt}\n"
        f"-----BEGIN {pem_label}-----\nMIIEvQ\n-----END {pem_label}-----\n"
    )

    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr=stderr)

    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    with pytest.raises(helper.HelperError) as failure:
        helper._run(["docker", "exec", "abc123", "mkdir", "-p", "/pulsar/ci-secrets"])
    message = str(failure.value)
    assert "command failed: docker (exit 1)" in message
    assert "argv=[docker exec abc123 mkdir -p /pulsar/ci-secrets]" in message
    assert "Permission denied" in message
    assert jwt not in message
    assert "MIIEvQ" not in message
    assert "[redacted" in message


def test_failed_command_stderr_is_bounded(
    helper: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 2, stdout="", stderr="x" * 10_000)

    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    with pytest.raises(helper.HelperError) as failure:
        helper._run(["docker", "inspect", "abc"])
    assert len(str(failure.value)) < helper._STDERR_LIMIT + 200


def test_merge_properties_replaces_in_place_without_duplicates(helper: Any) -> None:
    stock = (
        "# stock comment\nclusterName=standalone\njournalSyncData=false\n"
        "# journalSyncData=commented\nauthenticationEnabled=false\n"
    )
    merged = helper._merge_properties(
        stock,
        [
            "# override comment\njournalSyncData=true\nauthenticationEnabled=true\n",
            "authenticationEnabled=true\nadvertisedAddress=127.0.0.1\n",
        ],
    )
    active = [line for line in merged.splitlines() if line and not line.startswith("#")]
    assert active.count("journalSyncData=true") == 1
    assert "journalSyncData=false" not in active
    assert active.count("authenticationEnabled=true") == 1
    assert "clusterName=standalone" in active
    assert active[-1] == "advertisedAddress=127.0.0.1"
    assert "# journalSyncData=commented" in merged
    keys = [line.split("=", 1)[0] for line in active]
    assert len(keys) == len(set(keys))


def test_staging_uses_root_for_setup_and_verifies_as_runtime_user(
    helper: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_docker(args: list[str], *, check: bool = True) -> str:
        del check
        calls.append(args)
        if args[-2:] == ["-u", "pulsar"]:
            return "10000"
        if args[-2:] == ["-g", "pulsar"]:
            return "0"
        return ""

    monkeypatch.setattr(helper, "_docker", fake_docker)
    helper._stage("abc123", tmp_path)
    mkdir = next(call for call in calls if "mkdir" in call)
    assert mkdir[:4] == ["exec", "-u", "0", "abc123"]
    chown = next(call for call in calls if "chown" in call)
    assert chown[:4] == ["exec", "-u", "0", "abc123"]
    assert "10000:0" in chown
    readable = [call for call in calls if "test" in call and "-r" in call]
    assert len(readable) == len(helper._STAGED_SECRETS)
    assert all(call[:3] == ["exec", "-u", "10000:0"] for call in readable)


def test_broker_identity_matches_jvm_after_launcher_exec(helper: Any) -> None:
    jvm = (
        "/opt/jvm/bin/java -Dpulsar.config.file=/pulsar/conf/standalone.conf "
        "org.apache.pulsar.PulsarStandaloneStarter --no-functions-worker "
        "--config /pulsar/conf/standalone.conf"
    )
    assert helper._BROKER_ARGV in jvm
    assert "bin/pulsar standalone" not in jvm
    assert f">>{helper._BROKER_OUTPUT} 2>&1" in helper._BROKER_LAUNCH


def test_broker_client_token_parameters_are_file_references() -> None:
    root = Path(__file__).resolve().parents[3]
    for relative in (
        "deploy/pulsar/standalone.conf",
        "deploy/pulsar/functions_worker.yml",
        "omega_prime/scripts/eventstream_it.py",
    ):
        text = (root / relative).read_text(encoding="utf-8")
        # AuthenticationToken treats "token:" as a literal JWT, so
        # "token:file:///..." would send the path itself as the token.
        assert "token:file:" not in text, relative
