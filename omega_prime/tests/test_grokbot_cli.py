# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Phase 62: `omega-prime-mcp-server` argument handling, both transports."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from omega_prime import mcp_server
from omega_prime.grokbot.audit import GrokBotAuditTracer, default_audit_path

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "omega_prime" / "contracts"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("OMEGA_PRIME_AUDIT_LOG", raising=False)


@pytest.fixture
def sse_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _serve_sse(root: Path, **kwargs: Any) -> int:
        calls.append({"root": root, **kwargs})
        return 0

    monkeypatch.setattr("omega_prime.grokbot.remote.serve_sse", _serve_sse)
    return calls


def _sse(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--root",
        str(tmp_path),
        "--home",
        str(tmp_path),
        "--transport",
        "sse",
        *extra,
    ]


def _token_file(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    path = tmp_path / "token"
    path.write_text(text + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def _root_copy(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    shutil.copytree(CONTRACTS, root / "omega_prime" / "contracts")
    return root


def _one_line_reason(err: str) -> str:
    lines = err.strip().splitlines()
    assert len(lines) == 1, err
    assert lines[0].startswith("omega-prime-mcp-server: ")
    return lines[0]


def test_stdio_missing_roster_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    root = _root_copy(tmp_path)
    (root / "omega_prime/contracts/tool-rosters/omega-prime.yaml").unlink()
    code = mcp_server.main(["--root", str(root), "--home", str(tmp_path)])
    assert code == 2
    assert "cannot read roster" in _one_line_reason(capsys.readouterr().err)


def test_stdio_broken_policy_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    root = _root_copy(tmp_path)
    (root / "omega_prime/contracts/policies/omega-prime.json").write_text(
        "{not json", encoding="utf-8"
    )
    code = mcp_server.main(["--root", str(root), "--home", str(tmp_path)])
    assert code == 2
    assert "cannot load seat policy" in _one_line_reason(capsys.readouterr().err)


def test_stdio_serves_with_runtime_and_approvals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runtime = SimpleNamespace(registry=object(), roster=["read_file"])
    loaded: list[tuple[Path, Path, bool, list[tuple[str, str]]]] = []
    served: list[object] = []

    def _load_runtime(root, home, *, no_roster, approvals, work_root=None, env=None):
        loaded.append((root, home, no_roster, list(approvals)))
        return runtime

    async def _serve(server: object) -> None:
        served.append(server)

    def _build_server(registry: object, roster: list[str] | None) -> str:
        return f"server:{roster}"

    monkeypatch.setattr(mcp_server, "load_runtime", _load_runtime)
    monkeypatch.setattr(mcp_server, "build_server", _build_server)
    monkeypatch.setattr(mcp_server, "_serve", _serve)
    code = mcp_server.main(
        [
            "--root",
            str(tmp_path),
            "--home",
            str(tmp_path / "h"),
            "--no-roster",
            "--approve",
            "git_push:alice",
            "--approve",
            "deploy:bob:extra",
        ]
    )
    assert code == 0
    assert loaded == [
        (
            tmp_path.resolve(),
            tmp_path / "h",
            True,
            [("git_push", "alice"), ("deploy", "bob:extra")],
        )
    ]
    assert served == ["server:['read_file']"]


def test_stdio_stamps_the_resolved_audit_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _load_runtime(
        root: Path,
        home: Path,
        *,
        no_roster: bool,
        approvals: object,
        work_root: object = None,
        env: dict[str, str] | None = None,
    ) -> SimpleNamespace:
        del home, no_roster, approvals, work_root
        captured["root"] = root
        captured["env"] = env
        return SimpleNamespace(registry=object(), roster=["read_file"])

    async def _serve(server: object) -> None:
        del server

    monkeypatch.setattr(mcp_server, "load_runtime", _load_runtime)
    monkeypatch.setattr(mcp_server, "build_server", lambda *_a, **_k: "server")
    monkeypatch.setattr(mcp_server, "_serve", _serve)
    log = tmp_path / "audit.jsonl"
    assert (
        mcp_server.main(
            [
                "--root",
                str(tmp_path),
                "--home",
                str(tmp_path),
                "--no-roster",
                "--audit-log",
                str(log),
            ]
        )
        == 0
    )
    assert captured["env"]["OMEGA_PRIME_AUDIT_LOG"] == str(log)
    assert (
        mcp_server.main(
            ["--root", str(tmp_path), "--home", str(tmp_path), "--no-roster"]
        )
        == 0
    )
    assert captured["env"]["OMEGA_PRIME_AUDIT_LOG"] == str(
        default_audit_path(tmp_path.resolve())
    )


def test_stdio_real_runtime_reaches_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    served: list[object] = []

    async def _serve(server: object) -> None:
        served.append(server)

    monkeypatch.setattr(mcp_server, "_serve", _serve)
    code = mcp_server.main(["--root", str(ROOT), "--home", str(tmp_path)])
    assert code == 0
    assert len(served) == 1


@pytest.mark.parametrize("item", ["nocolon", ":who", "tool:", ""])
@pytest.mark.parametrize("transport", ["stdio", "sse"])
def test_malformed_approve_exits_2_on_both_transports(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sse_calls: list[dict[str, Any]],
    transport: str,
    item: str,
):
    code = mcp_server.main(
        [
            "--root",
            str(ROOT),
            "--home",
            str(tmp_path),
            "--transport",
            transport,
            "--approve",
            item,
        ]
    )
    assert code == 2
    line = _one_line_reason(capsys.readouterr().err)
    assert line == (
        f"omega-prime-mcp-server: bad --approve {item!r}, want TOOL:APPROVER"
    )
    assert sse_calls == []


def test_sse_receives_approvals_and_defaults(
    tmp_path: Path, sse_calls: list[dict[str, Any]]
):
    code = mcp_server.main(
        _sse(
            tmp_path,
            "--approve",
            "a:one",
            "--approve",
            "b:two",
            "--public-url",
            "https://bot.example/sse",
            "--no-roster",
            "--port",
            "9100",
        )
    )
    assert code == 0
    (call,) = sse_calls
    assert call["root"] == tmp_path.resolve()
    assert call["approvals"] == [("a", "one"), ("b", "two")]
    assert call["host"] == "127.0.0.1"
    assert call["port"] == 9100
    assert call["home"] == tmp_path
    assert call["no_roster"] is True
    assert call["public_url"] == "https://bot.example/sse"
    assert call["token"] is None
    assert call["allowed_hosts"] == ()
    assert call["allowed_origins"] == ()
    assert call["allow_insecure_no_auth"] is False
    assert call["audit"] is None
    assert call["max_body_bytes"] == 1_048_576
    assert call["shutdown_grace"] == 20.0


def test_sse_forwards_limits(tmp_path: Path, sse_calls: list[dict[str, Any]]):
    code = mcp_server.main(
        _sse(
            tmp_path,
            "--max-body-bytes",
            "2048",
            "--shutdown-grace",
            "1.5",
            "--allow-insecure-no-auth",
        )
    )
    assert code == 0
    (call,) = sse_calls
    assert call["max_body_bytes"] == 2048
    assert call["shutdown_grace"] == 1.5
    assert call["allow_insecure_no_auth"] is True


def test_sse_returns_serve_sse_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("omega_prime.grokbot.remote.serve_sse", lambda *a, **k: 2)
    assert mcp_server.main(_sse(tmp_path)) == 2


def test_token_precedence_file_over_argv_over_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sse_calls: list[dict[str, Any]],
):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "from-env")
    path = _token_file(tmp_path, "from-file")

    assert mcp_server.main(_sse(tmp_path, "--token", "from-argv")) == 0
    assert sse_calls[-1]["token"] == "from-argv"

    args = _sse(tmp_path, "--token", "from-argv", "--token-file", str(path))
    assert mcp_server.main(args) == 0
    assert sse_calls[-1]["token"] == "from-file"

    assert mcp_server.main(_sse(tmp_path)) == 0
    assert sse_calls[-1]["token"] == "from-env"


def test_token_env_name_is_configurable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sse_calls: list[dict[str, Any]],
):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "default-name")
    monkeypatch.setenv("BOT_TOKEN", "custom-name")
    assert mcp_server.main(_sse(tmp_path, "--token-env", "BOT_TOKEN")) == 0
    assert sse_calls[-1]["token"] == "custom-name"
    assert mcp_server.main(_sse(tmp_path, "--token-env", "UNSET_TOKEN_VAR")) == 0
    assert sse_calls[-1]["token"] is None


def test_group_readable_token_file_exits_2(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sse_calls: list[dict[str, Any]],
):
    path = _token_file(tmp_path, "secret", mode=0o644)
    code = mcp_server.main(_sse(tmp_path, "--token-file", str(path)))
    assert code == 2
    assert "chmod 600" in _one_line_reason(capsys.readouterr().err)
    assert sse_calls == []


def test_missing_token_file_exits_2(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sse_calls: list[dict[str, Any]],
):
    code = mcp_server.main(_sse(tmp_path, "--token-file", str(tmp_path / "nope")))
    assert code == 2
    _one_line_reason(capsys.readouterr().err)
    assert sse_calls == []


def test_argv_token_warns_on_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sse_calls: list[dict[str, Any]],
):
    assert mcp_server.main(_sse(tmp_path, "--token", "hunter2")) == 0
    err = capsys.readouterr().err
    assert "warning" in _one_line_reason(err)
    assert "process list" in err
    assert "hunter2" not in err
    assert sse_calls[-1]["token"] == "hunter2"


def test_no_warning_without_argv_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    sse_calls: list[dict[str, Any]],
):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "from-env")
    assert mcp_server.main(_sse(tmp_path)) == 0
    assert capsys.readouterr().err == ""


def test_allow_host_and_origin_are_repeatable(
    tmp_path: Path, sse_calls: list[dict[str, Any]]
):
    code = mcp_server.main(
        _sse(
            tmp_path,
            "--allow-host",
            "bot.example",
            "--allow-host",
            "bot.example:8443",
            "--allow-origin",
            "https://a.example",
            "--allow-origin",
            "https://b.example",
        )
    )
    assert code == 0
    (call,) = sse_calls
    assert call["allowed_hosts"] == ("bot.example", "bot.example:8443")
    assert call["allowed_origins"] == ("https://a.example", "https://b.example")


def test_audit_log_flag_builds_tracer(tmp_path: Path, sse_calls: list[dict[str, Any]]):
    path = tmp_path / "audit.jsonl"
    assert mcp_server.main(_sse(tmp_path, "--audit-log", str(path))) == 0
    audit = sse_calls[-1]["audit"]
    assert isinstance(audit, GrokBotAuditTracer)
    assert Path(audit.log_path) == path


def test_audit_log_defaults_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sse_calls: list[dict[str, Any]],
):
    env_path = tmp_path / "env-audit.jsonl"
    flag_path = tmp_path / "flag-audit.jsonl"
    monkeypatch.setenv("OMEGA_PRIME_AUDIT_LOG", str(env_path))
    assert mcp_server.main(_sse(tmp_path)) == 0
    assert Path(sse_calls[-1]["audit"].log_path) == env_path
    assert mcp_server.main(_sse(tmp_path, "--audit-log", str(flag_path))) == 0
    assert Path(sse_calls[-1]["audit"].log_path) == flag_path


def test_empty_audit_env_means_no_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sse_calls: list[dict[str, Any]],
):
    monkeypatch.setenv("OMEGA_PRIME_AUDIT_LOG", "")
    assert mcp_server.main(_sse(tmp_path)) == 0
    assert sse_calls[-1]["audit"] is None


_ENTERPRISE_FLAGS = (
    "--token-store",
    "--rate-limit",
    "--global-rate-limit",
    "--tool-rate-limit",
    "--auth-failure-limit",
    "--breaker-threshold",
    "--breaker-cooldown",
    "--no-metrics",
    "--metrics-scope",
    "--log-format",
    "--session-idle-timeout",
    "--max-sessions",
    "--approval-max-ttl",
)


def test_help_lists_every_enterprise_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        mcp_server.main(["--help"])
    assert exc.value.code == 0
    text = capsys.readouterr().out
    for flag in _ENTERPRISE_FLAGS:
        assert flag in text


def test_sse_enterprise_defaults(
    tmp_path: Path, sse_calls: list[dict[str, Any]]
) -> None:
    assert mcp_server.main(_sse(tmp_path)) == 0
    call = sse_calls[-1]
    assert call["token_store_path"] is None
    assert call["rate_limit"] == 600
    assert call["global_rate_limit"] == 0
    assert call["tool_rate_limit"] == 300
    assert call["auth_failure_limit"] == 10
    assert call["breaker_threshold"] == 5
    assert call["breaker_cooldown"] == 30.0
    assert call["metrics_enabled"] is True
    assert call["metrics_scope"] == "read"
    assert call["log_format"] == "text"
    assert call["session_idle_timeout"] == 1800.0
    assert call["max_sessions"] == 64
    assert call["approval_max_ttl"] == 86400.0


def test_token_store_maps_to_token_store_path(
    tmp_path: Path, sse_calls: list[dict[str, Any]]
) -> None:
    store = tmp_path / "tokens.json"
    assert mcp_server.main(_sse(tmp_path, "--token-store", str(store))) == 0
    assert sse_calls[-1]["token_store_path"] == store


def test_token_store_coexists_with_bearer_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sse_calls: list[dict[str, Any]],
) -> None:
    token = _token_file(tmp_path, "from-file")
    store = tmp_path / "store.json"
    monkeypatch.setenv("MCP_AUTH_TOKEN", "from-env")
    assert (
        mcp_server.main(
            _sse(
                tmp_path,
                "--token",
                "from-argv",
                "--token-file",
                str(token),
                "--token-store",
                str(store),
            )
        )
        == 0
    )
    call = sse_calls[-1]
    assert call["token"] == "from-file"
    assert call["token_store_path"] == store


@pytest.mark.parametrize(
    ("extra", "key", "expected"),
    [
        (["--rate-limit", "12"], "rate_limit", 12),
        (["--global-rate-limit", "40"], "global_rate_limit", 40),
        (["--tool-rate-limit", "7"], "tool_rate_limit", 7),
        (["--auth-failure-limit", "3"], "auth_failure_limit", 3),
        (["--breaker-threshold", "9"], "breaker_threshold", 9),
        (["--breaker-cooldown", "2.5"], "breaker_cooldown", 2.5),
        (["--no-metrics"], "metrics_enabled", False),
        (["--metrics-scope", "read"], "metrics_scope", "read"),
        (["--metrics-scope", "call"], "metrics_scope", "call"),
        (["--metrics-scope", "admin"], "metrics_scope", "admin"),
        (["--log-format", "text"], "log_format", "text"),
        (["--log-format", "json"], "log_format", "json"),
        (["--session-idle-timeout", "15"], "session_idle_timeout", 15.0),
        (["--max-sessions", "3"], "max_sessions", 3),
        (["--approval-max-ttl", "120"], "approval_max_ttl", 120.0),
    ],
)
def test_sse_flag_maps_to_serve_sse(
    tmp_path: Path,
    sse_calls: list[dict[str, Any]],
    extra: list[str],
    key: str,
    expected: object,
) -> None:
    assert mcp_server.main(_sse(tmp_path, *extra)) == 0
    assert sse_calls[-1][key] == expected


def test_zero_disables_limits(tmp_path: Path, sse_calls: list[dict[str, Any]]) -> None:
    assert (
        mcp_server.main(
            _sse(
                tmp_path,
                "--rate-limit",
                "0",
                "--global-rate-limit",
                "0",
                "--tool-rate-limit",
                "0",
                "--auth-failure-limit",
                "0",
                "--breaker-threshold",
                "0",
                "--breaker-cooldown",
                "0",
            )
        )
        == 0
    )
    call = sse_calls[-1]
    assert call["rate_limit"] == 0
    assert call["global_rate_limit"] == 0
    assert call["tool_rate_limit"] == 0
    assert call["auth_failure_limit"] == 0
    assert call["breaker_threshold"] == 0
    assert call["breaker_cooldown"] == 0.0


@pytest.mark.parametrize("transport", ["stdio", "sse"])
@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--rate-limit", "-1"),
        ("--global-rate-limit", "-5"),
        ("--tool-rate-limit", "-1"),
        ("--auth-failure-limit", "-1"),
        ("--breaker-threshold", "-1"),
        ("--breaker-cooldown", "-0.1"),
        ("--breaker-cooldown", "-1"),
        ("--session-idle-timeout", "0"),
        ("--session-idle-timeout", "-2"),
        ("--max-sessions", "0"),
        ("--max-sessions", "-3"),
        ("--approval-max-ttl", "0"),
        ("--approval-max-ttl", "-4"),
    ],
)
def test_limit_usage_errors_exit_2(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sse_calls: list[dict[str, Any]],
    transport: str,
    flag: str,
    value: str,
) -> None:
    code = mcp_server.main(
        [
            "--root",
            str(tmp_path),
            "--home",
            str(tmp_path),
            "--transport",
            transport,
            flag,
            value,
        ]
    )
    assert code == 2
    assert flag in _one_line_reason(capsys.readouterr().err)
    assert sse_calls == []


def test_stdio_ignores_sse_enterprise_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    sse_calls: list[dict[str, Any]],
) -> None:
    served: list[object] = []

    def _load_runtime(
        root: Path,
        home: Path,
        *,
        no_roster: bool,
        approvals: object,
        work_root: object = None,
        env: object = None,
    ):
        del root, home, no_roster, approvals, work_root, env
        return SimpleNamespace(registry=object(), roster=["read_file"])

    async def _serve(server: object) -> None:
        served.append(server)

    def _build_server(registry: object, roster: list[str] | None) -> str:
        del registry, roster
        return "server"

    monkeypatch.setattr(mcp_server, "load_runtime", _load_runtime)
    monkeypatch.setattr(mcp_server, "build_server", _build_server)
    monkeypatch.setattr(mcp_server, "_serve", _serve)
    store = tmp_path / "tokens.json"
    code = mcp_server.main(
        [
            "--root",
            str(tmp_path),
            "--home",
            str(tmp_path),
            "--token-store",
            str(store),
            "--rate-limit",
            "11",
            "--global-rate-limit",
            "22",
            "--tool-rate-limit",
            "33",
            "--auth-failure-limit",
            "4",
            "--breaker-threshold",
            "6",
            "--breaker-cooldown",
            "1.5",
            "--no-metrics",
            "--metrics-scope",
            "admin",
            "--log-format",
            "json",
            "--session-idle-timeout",
            "90",
            "--max-sessions",
            "8",
            "--approval-max-ttl",
            "60",
            "--host",
            "0.0.0.0",
            "--port",
            "9",
        ]
    )
    assert code == 0
    assert served == ["server"]
    assert sse_calls == []
    assert capsys.readouterr().err == ""


def test_dockerfile_cmd_is_accepted(
    tmp_path: Path, sse_calls: list[dict[str, Any]]
) -> None:
    """The image CMD (`--log-format json` plus the Phase 62 SSE flags) parses."""
    token = _token_file(tmp_path, "dockerfile-bearer-token")
    audit = tmp_path / "audit.jsonl"
    home = tmp_path / "home"
    code = mcp_server.main(
        [
            "--root",
            str(tmp_path),
            "--transport",
            "sse",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--token-file",
            str(token),
            "--audit-log",
            str(audit),
            "--home",
            str(home),
            "--shutdown-grace",
            "20",
            "--log-format",
            "json",
        ]
    )
    assert code == 0
    call = sse_calls[-1]
    assert call["log_format"] == "json"
    assert call["shutdown_grace"] == 20.0
    assert call["host"] == "0.0.0.0"
    assert call["port"] == 8000
    assert call["home"] == home
    assert call["token"] == "dockerfile-bearer-token"
    assert isinstance(call["audit"], GrokBotAuditTracer)
    assert Path(call["audit"].log_path) == audit
