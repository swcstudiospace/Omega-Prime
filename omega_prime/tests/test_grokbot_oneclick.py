"""Tests for Grok Bot 1-Click Launcher."""

import itertools
import json
import os
import select
import signal
import stat
import string
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from omega_prime.grokbot import oneclick
from omega_prime.grokbot.manifest import find_repo_root
from omega_prime.grokbot.oneclick import main, run_oneclick

TOKEN = "env-token-0123456789-abcdefghijklmnop"


def test_oneclick_dry_run_stdio(tmp_path: Path):
    root = find_repo_root()
    manifest_out = tmp_path / "grok_manifest.json"

    ret = run_oneclick(
        root=root,
        transport="stdio",
        export_manifest=manifest_out,
        dry_run=True,
    )
    assert ret == 0
    assert manifest_out.is_file()

    data = json.loads(manifest_out.read_text(encoding="utf-8"))
    assert data["manifest_version"] == "1.0.0"
    assert data["bot"]["name"] == "Omega Prime"
    assert data["mcp_server"]["transport"] == "stdio"


def test_oneclick_dry_run_sse(tmp_path: Path):
    root = find_repo_root()
    manifest_out = tmp_path / "grok_manifest_sse.json"

    ret = run_oneclick(
        root=root,
        transport="sse",
        host="127.0.0.1",
        port=59125,
        auth_token="test-secret-0123456789",
        export_manifest=manifest_out,
        dry_run=True,
    )
    assert ret == 0
    assert manifest_out.is_file()

    data = json.loads(manifest_out.read_text(encoding="utf-8"))
    assert data["mcp_server"]["transport"] == "sse"
    assert data["mcp_server"]["url"] == "http://127.0.0.1:59125/sse"
    assert data["mcp_server"]["auth"]["type"] == "bearer"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(find_repo_root())  # `--root` defaults to the cwd
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("OMEGA_PRIME_TEST_TOKEN", raising=False)


def _doctor_returning(*statuses: str) -> Any:
    """A fake `run_doctor_checks` returning one check per status."""
    seen: list[dict[str, Any]] = []

    def fake(root: Path, port: int, *, host: str, auth_enabled: bool) -> dict[str, Any]:
        del root
        seen.append({"port": port, "host": host, "auth_enabled": auth_enabled})
        checks = [
            {
                "id": f"c{i}",
                "name": f"Check {i}",
                "category": "test",
                "status": status,
                "message": f"{status} message",
                "fix_hint": "do the thing",
            }
            for i, status in enumerate(statuses)
        ]
        return {"passed": "fail" not in statuses, "checks": checks}

    fake.seen = seen  # type: ignore[attr-defined]
    return fake


def _sse(*extra: str) -> list[str]:
    return ["--transport", "sse", "--port", "59125", "--skip-doctor", *extra]


def test_public_url_wins_over_host_and_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    out = tmp_path / "m.json"
    ret = main(
        [
            *_sse("--host", "0.0.0.0"),
            "--public-url",
            "https://bot.example.com/",
            "--export-manifest",
            str(out),
            "--dry-run",
            "--json",
        ]
    )
    assert ret == 0
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert manifest["mcp_server"]["url"] == "https://bot.example.com/sse"
    report = json.loads(capsys.readouterr().out)
    assert report["url"] == "https://bot.example.com/sse"


def test_wildcard_host_exports_loopback_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    out = tmp_path / "m.json"
    ret = main([*_sse("--host", "0.0.0.0", "--export-manifest", str(out), "--dry-run")])
    assert ret == 0
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert manifest["mcp_server"]["url"] == "http://127.0.0.1:59125/sse"


def test_strict_failure_aborts_with_exit_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    fake = _doctor_returning("ok", "fail")
    monkeypatch.setattr(oneclick, "run_doctor_checks", fake)
    out = tmp_path / "m.json"
    ret = main(
        [
            "--transport",
            "sse",
            "--export-manifest",
            str(out),
            "--dry-run",
            "--json",
        ]
    )
    assert ret == 3
    assert not out.exists()
    captured = capsys.readouterr()
    assert "do the thing" in captured.out + captured.err
    report = json.loads(captured.out)
    assert report["passed"] is False
    assert report["strict"] is True
    assert fake.seen[0]["host"] == "127.0.0.1"
    assert fake.seen[0]["auth_enabled"] is False


def test_no_strict_continues_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    monkeypatch.setattr(oneclick, "run_doctor_checks", _doctor_returning("fail"))
    out = tmp_path / "m.json"
    ret = main(
        [
            "--transport",
            "sse",
            "--no-strict",
            "--export-manifest",
            str(out),
            "--dry-run",
        ]
    )
    assert ret == 0
    assert out.is_file()
    assert "Continuing launch" in capsys.readouterr().out


def test_warnings_never_abort_and_stdio_defaults_to_non_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(oneclick, "run_doctor_checks", _doctor_returning("warn"))
    assert main(["--transport", "sse", "--dry-run"]) == 0
    monkeypatch.setattr(oneclick, "run_doctor_checks", _doctor_returning("fail"))
    assert main(["--transport", "stdio", "--dry-run"]) == 0
    assert main(["--transport", "stdio", "--strict", "--dry-run"]) == 3


def test_skip_doctor_does_not_call_doctor(monkeypatch: pytest.MonkeyPatch):
    fake = _doctor_returning("fail")
    monkeypatch.setattr(oneclick, "run_doctor_checks", fake)
    assert main(_sse("--dry-run")) == 0
    assert fake.seen == []


@pytest.mark.parametrize("dry_run", [True, False])
def test_bind_safety_refuses_open_wildcard_before_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture, dry_run: bool
):
    out = tmp_path / "m.json"
    argv = _sse("--host", "0.0.0.0", "--export-manifest", str(out))
    if dry_run:
        argv.append("--dry-run")
    assert main(argv) == 2
    assert not out.exists()
    assert "without authentication" in capsys.readouterr().err


def test_allow_insecure_no_auth_permits_wildcard(tmp_path: Path):
    out = tmp_path / "m.json"
    argv = _sse("--host", "0.0.0.0", "--allow-insecure-no-auth")
    ret = main([*argv, "--export-manifest", str(out), "--dry-run"])
    assert ret == 0
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert manifest["mcp_server"]["auth"]["type"] == "none"


def test_short_token_is_a_configuration_error(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "short")
    assert main(_sse("--dry-run")) == 2
    assert "too short" in capsys.readouterr().err


def test_token_file_must_be_private(tmp_path: Path, capsys: pytest.CaptureFixture):
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN, encoding="utf-8")
    token_file.chmod(0o644)
    assert main(_sse("--token-file", str(token_file), "--dry-run")) == 2
    assert "chmod 600" in capsys.readouterr().err
    token_file.chmod(0o600)
    assert main(_sse("--token-file", str(token_file), "--dry-run")) == 0


def test_token_env_name_is_configurable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OMEGA_PRIME_TEST_TOKEN", TOKEN)
    out = tmp_path / "m.json"
    ret = main(
        [
            *_sse("--host", "0.0.0.0", "--token-env", "OMEGA_PRIME_TEST_TOKEN"),
            "--export-manifest",
            str(out),
            "--dry-run",
        ]
    )
    assert ret == 0
    auth = json.loads(out.read_text(encoding="utf-8"))["mcp_server"]["auth"]
    assert auth["type"] == "bearer"
    assert auth["token_env"] == "OMEGA_PRIME_TEST_TOKEN"


def test_generate_token_requires_sse(capsys: pytest.CaptureFixture):
    assert main(["--generate-token", "--dry-run"]) == 2
    assert "--transport sse" in capsys.readouterr().err


def test_dry_run_generate_token_neither_prints_nor_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    token_file = tmp_path / "token"
    ret = main(
        [
            *_sse(
                "--host", "0.0.0.0", "--generate-token", "--token-file", str(token_file)
            ),
            "--dry-run",
            "--json",
        ]
    )
    assert ret == 0
    assert not token_file.exists()
    captured = capsys.readouterr()
    assert "omk_" not in captured.out + captured.err
    assert json.loads(captured.out)["auth"]["source"] == "generated"


def test_generate_token_shown_once_and_written_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    calls: list[dict[str, Any]] = []
    env_seen: list[str | None] = []

    def fake_serve(**kwargs: Any) -> int:
        calls.append(kwargs)
        env_seen.append(os.environ.get("MCP_AUTH_TOKEN"))
        return 0

    monkeypatch.setattr("omega_prime.grokbot.remote.serve_sse", fake_serve)
    token_file = tmp_path / "secrets" / "token"
    ret = main(
        [
            *_sse(
                "--host", "0.0.0.0", "--generate-token", "--token-file", str(token_file)
            ),
            "--json",
        ]
    )
    assert ret == 0
    token = token_file.read_text(encoding="utf-8").strip()
    assert token.startswith("omk_")
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    captured = capsys.readouterr()
    assert captured.err.count(token) == 1
    assert token not in captured.out
    assert calls[0]["token"] == token
    # WR-02: the token travels only as the serve argument, never via environ.
    assert env_seen == [None]
    assert "MCP_AUTH_TOKEN" not in os.environ

    # The saved file is reused: nothing is generated or printed again.
    ret = main(
        _sse("--host", "0.0.0.0", "--generate-token", "--token-file", str(token_file))
    )
    assert ret == 0
    assert calls[1]["token"] == token
    assert token not in capsys.readouterr().err


def test_generate_token_without_file_prints_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    calls: list[dict[str, Any]] = []

    def fake_serve(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr("omega_prime.grokbot.remote.serve_sse", fake_serve)
    assert main(_sse("--generate-token")) == 0
    token = calls[0]["token"]
    captured = capsys.readouterr()
    assert captured.err.count(token) == 1
    assert token not in captured.out


def test_token_never_in_manifest_or_json_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    out = tmp_path / "m.json"
    ret = main(_sse("--export-manifest", str(out), "--dry-run", "--json"))
    assert ret == 0
    assert TOKEN not in out.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert TOKEN not in captured.out
    assert TOKEN not in captured.err
    report = json.loads(captured.out)
    assert set(report) >= {
        "passed",
        "strict",
        "checks",
        "transport",
        "url",
        "manifest_path",
        "auth",
    }
    assert report["manifest_path"] == str(out)
    assert report["auth"]["type"] == "bearer"
    assert stat.S_IMODE(out.stat().st_mode) == 0o644


def test_json_mode_keeps_stdout_machine_readable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    monkeypatch.setattr(oneclick, "run_doctor_checks", _doctor_returning("ok", "warn"))
    assert main(["--transport", "sse", "--dry-run", "--json"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["passed"] is True
    assert [c["status"] for c in report["checks"]] == ["ok", "warn"]
    assert "Preflight Health Checks" in captured.err


def test_argv_token_emits_warning_without_echoing_value(
    capsys: pytest.CaptureFixture,
):
    secret = "argv-token-0123456789-abcdef"
    assert main(_sse("--token", secret, "--dry-run")) == 0
    err = capsys.readouterr().err
    assert "--token" in err
    assert "process" in err
    assert secret not in err


def test_serving_passes_remote_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []

    def fake_serve(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 7

    monkeypatch.setattr("omega_prime.grokbot.remote.serve_sse", fake_serve)
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    audit_path = tmp_path / "audit.jsonl"
    ret = main(
        [
            *_sse("--public-url", "https://bot.example.com"),
            "--allow-host",
            "bot.example.com",
            "--allow-host",
            "other.example.com",
            "--allow-origin",
            "https://app.example.com",
            "--audit-log",
            str(audit_path),
        ]
    )
    assert ret == 7
    kwargs = calls[0]
    assert kwargs["token"] == TOKEN
    assert kwargs["public_url"] == "https://bot.example.com"
    assert kwargs["allowed_hosts"] == ("bot.example.com", "other.example.com")
    assert kwargs["allowed_origins"] == ("https://app.example.com",)
    assert kwargs["allow_insecure_no_auth"] is False
    assert kwargs["port"] == 59125
    assert Path(kwargs["audit"].log_path) == audit_path


def test_stdio_launch_passes_audit_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    seen: list[list[str]] = []

    def fake_call(cmd: list[str]) -> int:
        seen.append(cmd)
        return 0

    monkeypatch.setattr(oneclick.subprocess, "call", fake_call)
    audit_path = tmp_path / "audit.jsonl"
    assert main(["--skip-doctor", "--audit-log", str(audit_path)]) == 0
    assert seen[0][-2:] == ["--audit-log", str(audit_path)]
    assert seen[0][1:3] == ["-m", "omega_prime.mcp_server"]


def test_stdio_launch_forwards_work_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    seen: list[list[str]] = []

    def fake_call(cmd: list[str]) -> int:
        seen.append(cmd)
        return 0

    monkeypatch.setattr(oneclick.subprocess, "call", fake_call)
    audit_path = tmp_path / "audit.jsonl"
    work = tmp_path / "work"
    work.mkdir()
    argv = ["--skip-doctor", "--audit-log", str(audit_path), "--work-root", str(work)]
    assert main(argv) == 0
    assert seen[0][1:3] == ["-m", "omega_prime.mcp_server"]
    assert seen[0][-4:] == [
        "--audit-log",
        str(audit_path),
        "--work-root",
        str(work.resolve()),
    ]


def test_public_url_must_be_http(capsys: pytest.CaptureFixture):
    assert main(_sse("--public-url", "ftp://nope", "--dry-run")) == 2
    assert "--public-url" in capsys.readouterr().err


def _recording_serve(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_serve(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr("omega_prime.grokbot.remote.serve_sse", fake_serve)
    return calls


def test_token_store_reaches_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _recording_serve(monkeypatch)
    store = tmp_path / "tokens.json"
    assert (
        main(
            _sse(
                "--host",
                "0.0.0.0",
                "--token-store",
                str(store),
                "--log-format",
                "json",
            )
        )
        == 0
    )
    assert calls[0]["token_store_path"] == store
    assert calls[0]["token"] is None
    assert calls[0]["log_format"] == "json"


def test_token_store_satisfies_bind_safety(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    store = tmp_path / "tokens.json"
    out = tmp_path / "m.json"
    ret = main(
        [
            *_sse("--host", "0.0.0.0", "--token-store", str(store)),
            "--export-manifest",
            str(out),
            "--dry-run",
            "--json",
        ]
    )
    assert ret == 0
    assert not store.exists()
    captured = capsys.readouterr()
    assert "without authentication" not in captured.err
    report = json.loads(captured.out)
    assert report["auth"]["enabled"] is True
    assert report["auth"]["type"] == "bearer"
    assert report["auth"]["source"] == "store"
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert manifest["mcp_server"]["auth"]["type"] == "bearer"


def test_token_store_coexists_with_bearer_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _recording_serve(monkeypatch)
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    store = tmp_path / "tokens.json"
    assert main(_sse("--host", "0.0.0.0", "--token-store", str(store))) == 0
    assert calls[0]["token"] == TOKEN
    assert calls[0]["token_store_path"] == store


def test_log_format_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _recording_serve(monkeypatch)
    assert main(_sse("--log-format", "json")) == 0
    assert calls[0]["log_format"] == "json"
    assert main(_sse()) == 0
    assert calls[1]["log_format"] == "text"
    assert calls[1]["token_store_path"] is None


def test_self_test_conflicts_with_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--self-test", "--dry-run"]) == 2
    captured = capsys.readouterr()
    assert "dry-run" in captured.err
    assert "omk_" not in captured.out + captured.err


def test_stdio_does_not_forward_sse_only_launcher_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def fake_call(cmd: list[str]) -> int:
        seen.append(cmd)
        return 0

    monkeypatch.setattr(oneclick.subprocess, "call", fake_call)
    store = tmp_path / "tokens.json"
    assert (
        main(["--skip-doctor", "--log-format", "json", "--token-store", str(store)])
        == 0
    )
    assert "--log-format" not in seen[0]
    assert "--token-store" not in seen[0]


_PORT_COUNTER = itertools.count(59000)


def _free_port() -> int:
    """A dummy port number; liveness is scripted so nothing ever binds."""
    return next(_PORT_COUNTER)


def _isolate_supervisor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the default supervisor state at a missing tmp file (never live)."""
    state = tmp_path / "supervisor.json"
    monkeypatch.setattr(
        "omega_prime.grokbot.supervisor._default_state_file", lambda: state
    )


def _sse_launch(
    monkeypatch: pytest.MonkeyPatch,
    out: Path,
    port: int,
    launches: list[Any],
    exit_code: int = 0,
    **kwargs: Any,
) -> int:
    """Run the SSE launcher with a stubbed host; serve kwargs land in launches."""

    def _fake_serve(**serve_kwargs: Any) -> int:
        launches.append(serve_kwargs)
        return exit_code

    monkeypatch.setattr("omega_prime.grokbot.remote.serve_sse", _fake_serve)
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    return run_oneclick(
        find_repo_root(),
        transport="sse",
        port=port,
        export_manifest=out,
        skip_doctor=True,
        **kwargs,
    )


def test_nonzero_exit_records_failed_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A nonzero serve exit records STATUS_FAILED, never complete."""
    from omega_prime.grokbot import receipts

    _isolate_supervisor(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "omega_prime.grokbot.rerun._port_open", lambda host, port: False
    )
    out = tmp_path / "m.json"
    port = _free_port()
    launches: list[Any] = []

    assert _sse_launch(monkeypatch, out, port, launches, exit_code=3) == 3
    receipt_path = receipts.receipt_path_for_manifest(out)
    record = receipts.read_receipt(receipt_path)
    assert record is not None
    assert record["status"] == "failed"
    assert record["exit_code"] == 3
    capsys.readouterr()

    # The failed receipt must not suppress the retry: the next run relaunches
    # and a clean exit completes.
    assert _sse_launch(monkeypatch, out, port, launches, exit_code=0) == 0
    assert len(launches) == 2
    retried = receipts.read_receipt(receipt_path)
    assert retried is not None
    assert retried["status"] == "complete"


def test_json_noop_emits_machine_readable_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Both no-ops keep human text on stderr and emit JSON on stdout."""
    from omega_prime.grokbot import receipts

    _isolate_supervisor(tmp_path, monkeypatch)
    port_open = {"open": False}
    monkeypatch.setattr(
        "omega_prime.grokbot.rerun._port_open",
        lambda host, port: port_open["open"],
    )
    out = tmp_path / "m.json"
    port = _free_port()
    launches: list[Any] = []

    assert _sse_launch(monkeypatch, out, port, launches) == 0
    assert len(launches) == 1
    receipt = receipts.read_receipt(receipts.receipt_path_for_manifest(out))
    assert receipt is not None
    capsys.readouterr()

    assert _sse_launch(monkeypatch, out, port, launches, json_output=True) == 0
    assert len(launches) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "already-complete"
    assert payload["manifest_digest"] == receipt["manifest_digest"]
    assert payload["port"] == port
    assert "already complete" in captured.err

    port_open["open"] = True
    assert _sse_launch(monkeypatch, out, port, launches, json_output=True) == 0
    assert len(launches) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "already-running"
    assert payload["port"] == port
    assert "already running" in captured.err


def test_host_mismatch_does_not_suppress_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A 127.0.0.1 listener must not suppress a launch bound elsewhere."""
    from omega_prime.grokbot import rerun

    _isolate_supervisor(tmp_path, monkeypatch)
    seen: list[str] = []

    def _fake_port_open(host: str, port: int) -> bool:
        seen.append(host)
        return host == "127.0.0.1"

    monkeypatch.setattr("omega_prime.grokbot.rerun._port_open", _fake_port_open)
    out = tmp_path / "m.json"
    port = _free_port()
    launches: list[Any] = []

    assert _sse_launch(monkeypatch, out, port, launches, host="127.0.0.2") == 0
    assert len(launches) == 1
    assert seen and all(host == "127.0.0.2" for host in seen)
    capsys.readouterr()

    # The guard itself is host-scoped: same port, different hosts differ.
    assert rerun.check_rerun(None, port, host="127.0.0.2") == "fresh"
    assert rerun.check_rerun(None, port, host="127.0.0.1") == "already-running"


def test_launch_lock_serializes_check_through_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The per-launch flock covers check_rerun..export, released pre-serve."""
    import contextlib
    from collections.abc import Iterator

    from omega_prime.grokbot import receipts
    from omega_prime.grokbot import rerun as rerun_mod

    _isolate_supervisor(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "omega_prime.grokbot.rerun._port_open", lambda host, port: False
    )
    events: list[str] = []
    real_hold = oneclick._hold_launch_lock

    @contextlib.contextmanager
    def _spy_hold(base: Path) -> Iterator[None]:
        events.append("lock-acquire")
        with real_hold(base):
            events.append("lock-held")
            yield
        events.append("lock-release")

    monkeypatch.setattr(oneclick, "_hold_launch_lock", _spy_hold)

    real_check = rerun_mod.check_rerun

    def _spy_check(*args: Any, **kwargs: Any) -> Any:
        events.append("check")
        return real_check(*args, **kwargs)

    monkeypatch.setattr(rerun_mod, "check_rerun", _spy_check)

    real_write = oneclick.atomic_write_text

    def _spy_write(*args: Any, **kwargs: Any) -> None:
        events.append("export")
        real_write(*args, **kwargs)

    monkeypatch.setattr(oneclick, "atomic_write_text", _spy_write)

    out = tmp_path / "m.json"
    port = _free_port()
    launches: list[Any] = []

    def _fake_serve(**serve_kwargs: Any) -> int:
        events.append("serve")
        launches.append(serve_kwargs)
        return 0

    monkeypatch.setattr("omega_prime.grokbot.remote.serve_sse", _fake_serve)
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    assert (
        run_oneclick(
            find_repo_root(),
            transport="sse",
            port=port,
            export_manifest=out,
            skip_doctor=True,
        )
        == 0
    )
    capsys.readouterr()
    assert events == [
        "lock-acquire",
        "lock-held",
        "check",
        "export",
        "lock-release",
        "serve",
    ]
    assert len(launches) == 1

    # The sidecar exists and is released: a non-blocking take must succeed.
    receipt_path = receipts.receipt_path_for_manifest(out)
    lock_path = receipt_path.with_name(f"{receipt_path.name}.lock")
    assert lock_path.is_file()
    if oneclick._fcntl is not None:
        fd = os.open(str(lock_path), os.O_RDWR)
        try:
            oneclick._fcntl.flock(fd, oneclick._fcntl.LOCK_EX | oneclick._fcntl.LOCK_NB)
            oneclick._fcntl.flock(fd, oneclick._fcntl.LOCK_UN)
        finally:
            os.close(fd)


def test_no_export_lock_base_is_uid_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The no-export lock base lives in a private per-user dir, not shared tmp."""
    import tempfile

    from omega_prime.grokbot import receipts

    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    port = _free_port()
    base = oneclick._launch_lock_base(None, port)
    assert base.name == f"omega-prime-oneclick-{port}.launch"
    assert base.parent.parent == Path(tempfile.gettempdir())
    assert base.parent.name == f"omega-prime-oneclick-{os.getuid()}"
    assert base.parent.is_dir()
    assert stat.S_IMODE(base.parent.stat().st_mode) & 0o077 == 0

    runtime = tmp_path / "run"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    xdg_base = oneclick._launch_lock_base(None, port)
    assert xdg_base == runtime / f"omega-prime-oneclick-{port}.launch"

    out = tmp_path / "m.json"
    assert oneclick._launch_lock_base(out, port) == receipts.receipt_path_for_manifest(
        out
    )


def test_foreign_owned_lock_file_fails_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A lock file owned by another user warns and proceeds, never raises."""
    base = tmp_path / "omega-prime-oneclick-59999.launch"
    lock_path = base.with_name(f"{base.name}.lock")
    lock_path.write_bytes(b"foreign")
    real_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real_uid + 1)
    opened: list[str] = []
    real_open = os.open

    def _spy_open(path: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(os.fspath(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", _spy_open)
    with oneclick._hold_launch_lock(base):
        pass
    assert opened == []
    assert "proceeding without lock" in capsys.readouterr().err


def test_unusable_lock_file_fails_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A lock file that cannot be opened warns and proceeds, never raises."""
    base = tmp_path / "omega-prime-oneclick-59998.launch"

    def _deny_open(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "open", _deny_open)
    with oneclick._hold_launch_lock(base):
        pass
    assert "proceeding without lock" in capsys.readouterr().err


def test_body_oserror_propagates_without_lock_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """A body OSError (e.g. manifest export disk-full) is not a lock error."""
    base = tmp_path / "omega-prime-oneclick-59997.launch"
    with (
        pytest.raises(OSError, match="No space left"),
        oneclick._hold_launch_lock(base),
    ):
        raise OSError(28, "No space left on device")
    assert "proceeding without lock" not in capsys.readouterr().err


def test_foreign_lock_body_oserror_is_not_a_lock_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A body OSError under a foreign-owned lock still propagates as OSError."""
    base = tmp_path / "omega-prime-oneclick-59996.launch"
    base.with_name(f"{base.name}.lock").write_bytes(b"foreign")
    real_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real_uid + 1)
    with (
        pytest.raises(OSError, match="No space left"),
        oneclick._hold_launch_lock(base),
    ):
        raise OSError(28, "No space left on device")
    err = capsys.readouterr().err
    assert "owned by another user" in err
    assert "cannot use lock" not in err


def test_lock_acquisition_failure_still_runs_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Acquisition failure warns and proceeds: the body still runs."""
    base = tmp_path / "omega-prime-oneclick-59995.launch"

    def _deny_open(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "open", _deny_open)
    ran: list[bool] = []
    with oneclick._hold_launch_lock(base):
        ran.append(True)
    assert ran == [True]
    assert "proceeding without lock" in capsys.readouterr().err


def test_export_oserror_propagates_through_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A manifest-export OSError aborts the launch as OSError, not a lock skip."""
    _isolate_supervisor(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "omega_prime.grokbot.rerun._port_open", lambda host, port: False
    )
    served: list[bool] = []

    def _fake_serve(**serve_kwargs: Any) -> int:
        served.append(True)
        return 0

    monkeypatch.setattr("omega_prime.grokbot.remote.serve_sse", _fake_serve)

    def _deny_write(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(oneclick, "atomic_write_text", _deny_write)
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    out = tmp_path / "m.json"
    with pytest.raises(OSError, match="No space left"):
        run_oneclick(
            find_repo_root(),
            transport="sse",
            port=_free_port(),
            export_manifest=out,
            skip_doctor=True,
        )
    assert served == []
    assert "proceeding without lock" not in capsys.readouterr().err


def test_lock_cancel_during_flock_closes_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A cancel while blocked in flock still closes the open descriptor."""
    if oneclick._fcntl is None:
        pytest.skip("flock unavailable on this platform")
    base = tmp_path / "omega-prime-oneclick-59994.launch"
    opened: list[int] = []
    closed: list[int] = []
    real_open = os.open
    real_close = os.close

    def _spy_open(path: Any, *args: Any, **kwargs: Any) -> int:
        fd = real_open(path, *args, **kwargs)
        opened.append(fd)
        return fd

    def _track_close(fd: int, *args: Any, **kwargs: Any) -> None:
        closed.append(fd)
        real_close(fd, *args, **kwargs)

    monkeypatch.setattr(os, "open", _spy_open)
    monkeypatch.setattr(os, "close", _track_close)

    def _raise_cancel(fd: int, op: int) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr(oneclick._fcntl, "flock", _raise_cancel)
    ran: list[bool] = []
    with pytest.raises(KeyboardInterrupt), oneclick._hold_launch_lock(base):
        ran.append(True)
    assert ran == []
    assert len(opened) == 1
    assert opened[0] in closed
    with pytest.raises(OSError):
        os.fstat(opened[0])
    assert "proceeding without lock" not in capsys.readouterr().err


# --- 75-03: self-test isolation effects --------------------------------------
# These prove isolation observationally through the frozen layout contract and
# the real run_self_test path. Only the process-execution cost boundary is
# stubbed (subprocess.Popen, readiness polling, verification transport):
# isolation, env construction, schedule routing, cleanup, and redaction all
# run for real. No case reads implementation source, asserts a mock call as
# proof of isolation, or depends on timing sleeps.


def _self_test_cmd_value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def _stub_self_test_host(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
    *,
    log_bytes: bytes = b"",
    ready_reason: str | None = None,
    verify_error: BaseException | None = None,
) -> None:
    """Stand in for the loopback host at the process-spawn seam only.

    Only the host argv (``omega_prime.mcp_server``) is intercepted; every
    other spawn — notably Core's legitimate ``git``/tool subprocesses inside
    the shared layout helper — delegates to the real Popen untouched.
    """

    real_popen = oneclick.subprocess.Popen

    class _FakeProc:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs
            install = _self_test_cmd_value(list(cmd), "--root")
            schedule = Path(install) / "cron" / "jobs.json"
            captured["schedule_present"] = schedule.is_file()
            captured["schedule_bytes"] = (
                schedule.read_bytes() if schedule.is_file() else None
            )
            handle = kwargs.get("stdout")
            if handle is not None and log_bytes:
                handle.write(log_bytes)
                handle.flush()
            self.returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def send_signal(self, sig: int) -> None:
            captured.setdefault("signals", []).append(sig)
            self.returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            captured["killed"] = True
            self.returncode = -9

    def _dispatch_popen(cmd: Any, **kwargs: Any) -> Any:
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else []
        if "omega_prime.mcp_server" in argv:
            return _FakeProc(cmd, **kwargs)
        return real_popen(cmd, **kwargs)

    from omega_prime.grokbot.verify import Check, VerifyReport

    monkeypatch.setattr(oneclick.subprocess, "Popen", _dispatch_popen)
    if ready_reason is None:
        monkeypatch.setattr(
            oneclick, "_wait_until_ready", lambda url, proc, timeout: None
        )
    else:
        monkeypatch.setattr(
            oneclick, "_wait_until_ready", lambda url, proc, timeout: ready_reason
        )
    if verify_error is not None:

        def _raise(*args: Any, **kwargs: Any) -> Any:
            raise verify_error

        monkeypatch.setattr("omega_prime.grokbot.verify.run_verification", _raise)
    else:
        report = VerifyReport(
            base_url="http://127.0.0.1:1",
            checks=[
                Check(
                    id="self-test-probe",
                    title="probe",
                    status="pass",
                    detail="ok",
                    duration_ms=1.0,
                )
            ],
        )
        monkeypatch.setattr(
            "omega_prime.grokbot.verify.run_verification",
            lambda *args, **kwargs: report,
        )


def test_self_test_leaves_caller_environment_untouched(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The run only reads the parent env; caller globals are byte-identical."""
    monkeypatch.setenv("GREPTILE_API_KEY", "LIVE-SENTINEL-9z8y7x6w")
    monkeypatch.setenv("OMEGA_PRIME_PRIME_RLM_ENABLED", "1")
    before = dict(os.environ)
    captured: dict[str, Any] = {}
    _stub_self_test_host(monkeypatch, captured)
    assert oneclick.run_self_test(find_repo_root()) == 0
    assert dict(os.environ) == before
    capsys.readouterr()


def test_self_test_leaves_source_schedules_untouched(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Source schedule bytes are identical; the fixture schedule starts empty.

    The writable source is a real public export: an outer isolated layout of
    the repository, seeded with schedule bytes in its owned install, on which
    run_self_test then builds its own inner fresh layout.
    """
    from omega_prime.grokbot.local_validation import isolated_layout

    with isolated_layout(find_repo_root()) as export:
        schedule = export.install / "cron" / "jobs.json"
        schedule.parent.mkdir(parents=True, exist_ok=True)
        sentinel = b'{"jobs": [{"id": "source-sentinel", "schedule": "live"}]}'
        schedule.write_bytes(sentinel)
        captured: dict[str, Any] = {}
        _stub_self_test_host(monkeypatch, captured)
        assert oneclick.run_self_test(export.install) == 0
        assert schedule.read_bytes() == sentinel
        observed = captured["schedule_bytes"]
        assert observed in (None, b"") or b"source-sentinel" not in observed
        if observed not in (None, b""):
            assert json.loads(observed.decode("utf-8")).get("jobs", []) == []
    capsys.readouterr()


#: Synthetic credential-shaped canaries are assembled at runtime so no complete
#: credential shape is committed to source.
_TOKEN_ALPHABET = string.ascii_letters + string.digits


def _synthetic_body(length: int, start: int = 0) -> str:
    """Deterministic mixed-class body; stride 17 is coprime with 62, so the
    first 62 characters are all distinct (maximal entropy, no randomness)."""
    return "".join(
        _TOKEN_ALPHABET[(start + 17 * index) % len(_TOKEN_ALPHABET)]
        for index in range(length)
    )


_PEM_HEADER = "-----BEGIN " + "RSA PRIVATE KEY-----"
#: 36-char scanner-only generic token: 4-char head, slash, 31-char body.
_SLASH_TOKEN = _synthetic_body(4, start=1) + "/" + _synthetic_body(31, start=9)
#: 64-char scanner-only generic token built from the slash token.
_GENERIC_CANDIDATE = (_SLASH_TOKEN * 2)[:64]
#: Short ``omk_`` form: the 13-char suffix is below the audit rule's minimum.
_SHORT_OMK = "omk_" + _synthetic_body(13, start=21)


def test_self_test_not_ready_rejection_is_fixed_and_secret_free(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A dead host publishes only fixed safe metadata with a nonzero exit."""
    fragment = "MIIBSpikeKey4192"
    log_canary = (
        b"host output before failure\n"
        + _PEM_HEADER.encode()
        + b"\n"
        + fragment.encode()
        + b"\n"
    )
    captured: dict[str, Any] = {}
    _stub_self_test_host(
        monkeypatch,
        captured,
        log_bytes=log_canary,
        ready_reason="timed out waiting for /readyz",
    )
    assert oneclick.run_self_test(find_repo_root()) == 1
    err = capsys.readouterr().err
    assert fragment not in err
    assert "PRIVATE KEY" not in err
    assert oneclick._SELF_TEST_SAFE_DIAGNOSTIC in err
    assert signal.SIGTERM in captured.get("signals", [])


def test_self_test_verification_failure_is_bounded_and_secret_free(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A verification exception publishes only fixed safe metadata, exit nonzero."""
    fragment = _SLASH_TOKEN[5:]
    captured: dict[str, Any] = {}
    _stub_self_test_host(
        monkeypatch,
        captured,
        verify_error=RuntimeError(f"verify blew up on {_SLASH_TOKEN}"),
    )
    assert oneclick.run_self_test(find_repo_root()) == 1
    err = capsys.readouterr().err
    assert fragment not in err
    assert oneclick._SELF_TEST_SAFE_DIAGNOSTIC in err


def test_bounded_diagnostic_redacts_full_text_before_clipping() -> None:
    """A secret spanning the old clip window cannot survive redact-first."""
    secret = "boundary-secret-000000001"
    raw = "A" * 1320 + secret + "B" * 1480
    out = oneclick._bounded_diagnostic(raw, [secret])
    assert "[redacted]" in out
    assert secret not in out
    assert "000000001" not in out
    assert len(out) <= 500


@pytest.mark.parametrize(
    ("raw", "fragments"),
    [
        (
            f"key block {_PEM_HEADER} MIIB leak",
            ("PRIVATE KEY", "MIIB"),
        ),
        (
            f"generic slash token {_SLASH_TOKEN} in output",
            (_SLASH_TOKEN[5:],),
        ),
    ],
)
def test_bounded_diagnostic_withholds_secret_shaped_text(
    raw: str, fragments: tuple[str, ...]
) -> None:
    """PEM and slash-bearing canaries yield only fixed metadata."""
    out = oneclick._bounded_diagnostic(raw, [])
    assert out == oneclick._SELF_TEST_SAFE_DIAGNOSTIC
    for fragment in fragments:
        assert fragment not in out


def _clip_filler(length: int) -> str:
    """Gate-clean single-space filler of exactly `length` chars."""
    unit = "log entry ok "
    return (unit * (length // len(unit) + 1))[:length]


def test_bounded_diagnostic_withholds_scanner_only_generic_across_clip() -> None:
    """A 64-char scanner-only token split by the clip edge leaves no fragment.

    The candidate sits so its head survives the old tail+clip windows as a
    sub-32-char fragment the post-clip gate would accept; the pre-clip gate
    must withhold it instead.
    """
    candidate = _GENERIC_CANDIDATE
    after = (
        oneclick._DIAGNOSTIC_TAIL_CHARS
        - oneclick._DIAGNOSTIC_CLIP_CHARS
        + 20
        - len(candidate)
    )
    raw = f"{_clip_filler(2000)} {candidate} {_clip_filler(after)}"
    out = oneclick._bounded_diagnostic(raw, [])
    assert candidate not in out
    assert candidate[:18] not in out
    assert candidate[2:20] not in out


def test_bounded_diagnostic_withholds_short_omk_across_tail() -> None:
    """A short `omk_` form split by the tail edge leaves no fragment.

    Only the bare suffix survives the old tail window, matching neither the
    audit `omk_` rule (which needs a longer suffix) nor the post-clip guard;
    the pre-clip gate must withhold it instead.
    """
    token = _SHORT_OMK
    # Tail starts 4 chars into the token, keeping only the bare suffix.
    after = oneclick._DIAGNOSTIC_TAIL_CHARS + 4 - len(token) - 2
    raw = f"{_clip_filler(2000)} {token} {_clip_filler(after)}"
    out = oneclick._bounded_diagnostic(raw, [])
    assert token not in out
    assert token[4:] not in out
    assert token[6:] not in out


def test_bounded_diagnostic_withholds_pem_header_across_clip() -> None:
    """A PEM header split by the clip edge leaves no fragment.

    Only the head of the armour survives the old tail+clip windows, so the
    post-clip PEM rule would accept it; the pre-clip gate must withhold it.
    """
    header = _PEM_HEADER
    after = (
        oneclick._DIAGNOSTIC_TAIL_CHARS
        - oneclick._DIAGNOSTIC_CLIP_CHARS
        + 20
        - len(header)
    )
    raw = f"{_clip_filler(2000)} {header} {_clip_filler(after)}"
    out = oneclick._bounded_diagnostic(raw, [])
    assert header not in out
    assert header[:18] not in out
    assert "-----BEGIN" not in out


def test_log_tail_missing_file_is_empty(tmp_path: Path) -> None:
    assert oneclick._log_tail(tmp_path / "no-host.log", []) == ""


def test_self_test_withholds_secret_bearing_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A poisoned verification report publishes no fragment and stays failed.

    The stubbed host is clean and shuts down 0; failure comes only from the
    final publication gate coercing the secret-bearing detail and metadata
    to the fixed safe label.
    """
    from omega_prime.grokbot.verify import Check, VerifyReport

    candidate = _GENERIC_CANDIDATE
    version_canary = _SHORT_OMK
    poisoned = VerifyReport(
        base_url="http://127.0.0.1:1",
        checks=[
            Check(
                id="probe",
                title="Probe",
                status="pass",
                detail=f"all good {candidate}",
                duration_ms=1.0,
            )
        ],
        server_version=version_canary,
    )
    captured: dict[str, Any] = {}
    _stub_self_test_host(monkeypatch, captured)
    monkeypatch.setattr(
        "omega_prime.grokbot.verify.run_verification",
        lambda *args, **kwargs: poisoned,
    )
    assert oneclick.run_self_test(find_repo_root()) == 1
    streams = capsys.readouterr()
    combined = streams.out + streams.err
    assert candidate not in combined
    assert candidate[:18] not in combined
    assert version_canary not in combined
    assert version_canary[6:] not in combined
    assert "diagnostic withheld" in streams.out
    assert "shutdown=0" in streams.out


# --- parser-guard regressions --------------------------------------------------
# The launcher parser echoes offending values into its stderr diagnostic
# (invalid choice, ``type=`` rejection, unrecognized arguments). A recognized
# synthetic canary in any of those positions must publish only the fixed safe
# metadata with argparse exit 2 and no host started. These call the real
# ``main`` and assert on the real streams; no mock stands in for the echo.

_PARSER_GUARD_CANARY = "omk_" + _synthetic_body(10, start=33)


@pytest.mark.parametrize(
    "argv",
    [
        ["--transport", _PARSER_GUARD_CANARY],
        ["--port", _PARSER_GUARD_CANARY],
        [_PARSER_GUARD_CANARY],
    ],
    ids=["choice", "typed-value", "unknown-argument"],
)
def test_parser_guard_withholds_recognized_canary(
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each argparse rejection echoes its canary; the guard withholds it."""

    def _must_not_start(*args: Any, **kwargs: Any) -> int:
        raise AssertionError("parser rejection started the host")

    monkeypatch.setattr(oneclick, "run_oneclick", _must_not_start)
    monkeypatch.setattr(oneclick, "run_self_test", _must_not_start)
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    combined = captured.out + captured.err
    assert _PARSER_GUARD_CANARY not in combined
    assert _PARSER_GUARD_CANARY[4:] not in combined
    assert oneclick._SELF_TEST_SAFE_DIAGNOSTIC in captured.err


def test_parser_guard_keeps_clean_choice_helpful(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A canary-free invalid choice keeps argparse wording and real options."""
    with pytest.raises(SystemExit) as exc:
        main(["--transport", "bogus"])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage:" in captured.err
    assert "invalid choice" in captured.err
    assert "stdio" in captured.err and "sse" in captured.err
    assert oneclick._SELF_TEST_SAFE_DIAGNOSTIC not in captured.err


@pytest.mark.parametrize("json_report", [False, True])
def test_served_stdio_is_protocol_only_and_reads_the_owned_workspace(
    tmp_path: Path, json_report: bool
) -> None:
    root = find_repo_root()
    home, work, state = (tmp_path / name for name in ("home", "work", "state"))
    for path in (home, work, state):
        path.mkdir()
    expected = {"path": "owned-proof.txt", "content": "owned stdio workspace\n"}
    (work / expected["path"]).write_text(expected["content"], encoding="utf-8")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "PYTHONPATH": str(root),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "OMEGA_PRIME_CREDENTIAL_STORE": str(home / "absent-credentials.json"),
        "OMEGA_PRIME_STATE_DIR": str(state),
        "OMEGA_PRIME_WORK_ROOT": str(work),
    }
    args = [
        sys.executable,
        "-m",
        "omega_prime.grokbot.oneclick",
        "--root",
        str(root),
        "--transport",
        "stdio",
        # The transport regression must not touch the install-root doctor audit file.
        # Separate owned runtime smoke covers default preflight output routing.
        "--skip-doctor",
    ]
    if json_report:
        args.append("--json")
    with (tmp_path / "stderr.log").open("wb") as stderr:
        proc = subprocess.Popen(
            args,
            cwd=tmp_path,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            start_new_session=True,
        )
        assert proc.stdin is not None and proc.stdout is not None
        pending = b""

        def exchange(message: dict[str, Any]) -> dict[str, Any]:
            nonlocal pending
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(json.dumps(message).encode() + b"\n")
            proc.stdin.flush()
            deadline = time.monotonic() + 30
            while True:
                while b"\n" not in pending:
                    readable, _, _ = select.select(
                        [proc.stdout], [], [], max(0, deadline - time.monotonic())
                    )
                    assert readable, "stdio host did not reply before deadline"
                    chunk = os.read(proc.stdout.fileno(), 4096)
                    assert chunk, "stdio host closed before replying"
                    pending += chunk
                line, _, pending = pending.partition(b"\n")
                # A real protocol consumer rejects banners, blank lines and CLI reports.
                reply = json.loads(line)
                assert reply["jsonrpc"] == "2.0"
                if reply.get("id") == message["id"]:
                    assert "error" not in reply
                    return reply["result"]

        try:
            initialized = exchange(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "owned-stdio-consumer", "version": "1"},
                    },
                }
            )
            assert "tools" in initialized["capabilities"]
            proc.stdin.write(
                b'{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n'
            )
            proc.stdin.flush()
            result = exchange(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "read_file",
                        "arguments": {"path": expected["path"]},
                    },
                }
            )
            assert result.get("isError") is not True
            assert len(result["content"]) == 1
            assert json.loads(result["content"][0]["text"]) == expected
        finally:
            proc.stdin.close()
            proc.stdin = None
            try:
                proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.communicate(timeout=15)
