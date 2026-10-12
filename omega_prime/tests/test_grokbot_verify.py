# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Black-box conformance verifier against a real host and hostile wrappers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import string
import subprocess
import sys
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from omega_prime.grokbot.manifest import find_repo_root
from omega_prime.grokbot.tokens import create_token
from omega_prime.grokbot.verify import (
    Check,
    VerifyReport,
    format_report,
    main,
    run_verification,
)
from omega_prime.tests.test_grokbot_integration import _app, _serve

_TRACE = {
    "X-Request-Id": "0123456789abcdef",
    "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
}
_WWW = {"WWW-Authenticate": 'Bearer realm="omega-prime"'}
_EXPECTED = [
    "health",
    "ready",
    "auth_required",
    "auth_missing",
    "auth_query_token",
    "auth_invalid",
    "origin_rejected",
    "request_id",
    "manifest",
    "metrics",
    "sse:initialize",
    "sse:list_tools",
    "sse:call_tool",
    "sse:unknown_tool",
    "sse:gated_tool_refused",
    "http:initialize",
    "http:list_tools",
    "http:call_tool",
    "http:unknown_tool",
    "http:gated_tool_refused",
    "admin:scope_enforced",
    "admin:approval_roundtrip",
]
_Handler = Callable[[Request], Awaitable[Response]]


def _check(report: VerifyReport, check_id: str) -> Check:
    for item in report.checks:
        if item.id == check_id:
            return item
    raise AssertionError(check_id)


def _write_secret(path: Path, value: str) -> None:
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health_body(auth: str, rostered: int) -> dict[str, object]:
    return {
        "status": "healthy",
        "service": "wrapper",
        "version": "test",
        "rostered_tools": rostered,
        "auth": auth,
    }


@contextlib.contextmanager
def _wrapper(handler: _Handler) -> Iterator[str]:
    app = Starlette(
        routes=[
            Route("/{path:path}", handler, methods=["GET", "POST", "DELETE"]),
        ]
    )
    with _serve(app) as host:
        yield host.base


def test_integrated_host_passes_every_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "tokens.json"
    caller, _caller_record = create_token(
        store, scopes=["read", "call"], label="caller"
    )
    admin, _admin_record = create_token(store, scopes=["admin"], label="ops")
    caller_file = tmp_path / "caller"
    admin_file = tmp_path / "admin"
    _write_secret(caller_file, caller)
    _write_secret(admin_file, admin)
    app = _app(tmp_path, token_store_path=store)
    with _serve(app) as host:
        report = run_verification(
            host.base,
            token=caller,
            admin_token=admin,
            require_auth=True,
            timeout=15,
        )
        code = main(
            [
                "--url",
                host.base,
                "--token-file",
                str(caller_file),
                "--admin-token-file",
                str(admin_file),
                "--require-auth",
                "--timeout",
                "15",
                "--json",
            ]
        )
        captured = capsys.readouterr()
    failed = [(item.id, item.detail) for item in report.checks if item.status != "pass"]
    assert not failed
    assert [item.id for item in report.checks] == _EXPECTED
    assert report.ok is True
    assert report.server_version
    payload = report.to_dict()
    assert set(payload) == {"ok", "base_url", "server_version", "counts", "checks"}
    assert payload["counts"] == {"pass": len(_EXPECTED), "fail": 0, "skip": 0}
    assert set(payload["checks"][0]) == {
        "id",
        "title",
        "status",
        "detail",
        "duration_ms",
    }
    rendered = format_report(report)
    blob = json.dumps(payload) + rendered + captured.out + captured.err
    assert caller not in blob
    assert admin not in blob
    assert code == 0
    cli = json.loads(captured.out)
    assert cli["ok"] is True
    assert cli["counts"]["fail"] == 0
    assert [item["id"] for item in cli["checks"]] == _EXPECTED


def test_auth_disabled_fails_only_when_required(tmp_path: Path) -> None:
    del tmp_path

    async def handler(request: Request) -> Response:
        if request.url.path == "/healthz":
            return JSONResponse(_health_body("disabled", 0), headers=_TRACE)
        if request.url.path == "/readyz":
            return JSONResponse({"ready": True})
        return JSONResponse({"error": "unauthorized"}, status_code=401, headers=_WWW)

    with _wrapper(handler) as base:
        skipped = run_verification(base, timeout=2)
        failed = run_verification(base, timeout=2, require_auth=True)
    assert _check(skipped, "auth_required").status == "skip"
    assert _check(failed, "auth_required").status == "fail"
    assert failed.ok is False
    assert all(not item.id.startswith("admin:") for item in skipped.checks)


def test_query_token_accepted_is_a_failure() -> None:
    token = "omk_query_probe_token_0123456789abcd"

    async def handler(request: Request) -> Response:
        if request.query_params.get("token"):
            return JSONResponse({"accepted": True})
        if request.url.path == "/healthz":
            return JSONResponse(_health_body("required", 0), headers=_TRACE)
        if request.url.path == "/readyz":
            return JSONResponse({"ready": True})
        return JSONResponse({"error": "unauthorized"}, status_code=401, headers=_WWW)

    with _wrapper(handler) as base:
        report = run_verification(base, token=token, timeout=2)
    assert _check(report, "auth_query_token").status == "fail"
    assert report.ok is False
    assert token not in json.dumps(report.to_dict())
    assert token not in format_report(report)


def test_lying_manifest_fails_and_cli_exits_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def handler(request: Request) -> Response:
        if request.url.path == "/healthz":
            return JSONResponse(_health_body("disabled", 4), headers=_TRACE)
        if request.url.path == "/readyz":
            return JSONResponse({"ready": True})
        if request.url.path == "/manifest.json":
            return JSONResponse(
                {
                    "digest": "abc",
                    "mcp_server": {
                        "tools": ["only"],
                        "rostered_tool_count": 1,
                        "approval_required": [],
                    },
                }
            )
        return JSONResponse({"error": "no"}, status_code=404)

    with _wrapper(handler) as base:
        report = run_verification(base, timeout=2)
        code = main(["--url", base, "--timeout", "2", "--json"])
        captured = capsys.readouterr()
    assert _check(report, "manifest").status == "fail"
    assert "tool count" in _check(report, "manifest").detail
    assert report.ok is False
    assert code == 1
    body = json.loads(captured.out)
    assert body["ok"] is False
    assert body["counts"]["fail"] >= 1


def test_manifest_that_echoes_the_token_is_redacted() -> None:
    token = "omk_manifest_leak_0123456789abcdef"

    async def handler(request: Request) -> Response:
        if request.url.path == "/healthz":
            return JSONResponse(_health_body("required", 0), headers=_TRACE)
        if request.url.path == "/manifest.json":
            return JSONResponse(
                {
                    "digest": token,
                    "leak": token,
                    "mcp_server": {
                        "tools": [],
                        "rostered_tool_count": 0,
                        "approval_required": [],
                    },
                }
            )
        return JSONResponse({"error": "unauthorized"}, status_code=401, headers=_WWW)

    with _wrapper(handler) as base:
        report = run_verification(base, token=token, timeout=2)
    check = _check(report, "manifest")
    assert check.status == "fail"
    assert token not in check.detail
    assert token not in json.dumps(report.to_dict())
    assert token not in format_report(report)


def test_missing_request_id_fails() -> None:
    async def handler(request: Request) -> Response:
        if request.url.path == "/healthz":
            return JSONResponse(
                _health_body("disabled", 0),
                headers={
                    "traceparent": (
                        "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
                    )
                },
            )
        if request.url.path == "/readyz":
            return JSONResponse({"ready": True})
        return JSONResponse({"error": "no"}, status_code=404)

    with _wrapper(handler) as base:
        report = run_verification(base, timeout=2)
    assert _check(report, "request_id").status == "fail"
    assert "X-Request-Id" in _check(report, "request_id").detail
    assert report.ok is False


def test_unreachable_url_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    port = _closed_port()
    code = main(["--url", f"http://127.0.0.1:{port}", "--timeout", "2", "--json"])
    captured = capsys.readouterr()
    assert code == 2
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["checks"][0]["id"] == "health"
    assert payload["checks"][0]["status"] == "fail"


def test_usage_errors_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert (
        main(
            [
                "--url",
                "http://127.0.0.1:9",
                "--token-env",
                "OMEGA_VERIFY_ABSENT",
                "--token-file",
                "ignored",
            ]
        )
        == 2
    )
    assert main(["--url", "http://127.0.0.1:9", "--timeout", "0"]) == 2
    assert (
        main(["--url", "http://127.0.0.1:9", "--token-env", "OMEGA_VERIFY_ABSENT"]) == 2
    )
    captured = capsys.readouterr()
    assert "omk_" not in captured.out + captured.err


def test_oneclick_self_test_exits_0() -> None:
    root = find_repo_root()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "omega_prime.grokbot.oneclick",
            "--self-test",
            "--root",
            str(root),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert completed.returncode == 0, (
        completed.stdout[-2000:] + completed.stderr[-2000:]
    )
    combined = completed.stdout + completed.stderr
    assert "omk_" not in combined
    assert "ok=true" in completed.stdout
    assert "shutdown=0" in completed.stdout


# --- Publication-boundary regressions ---------------------------------------
# The producer classifies the FULL diagnostic input before any flatten/clip:
# a scanner-only fragment straddling the old clip edge must never reach the
# public human/JSON renderers, and a trip stays failed, never passing.

#: Synthetic canaries are assembled at runtime so no complete credential shape
#: is committed to source.
_TOKEN_ALPHABET = string.ascii_letters + string.digits


def _synthetic_body(length: int, start: int = 0) -> str:
    """Deterministic mixed-class body; stride 17 is coprime with 62, so the
    first 62 characters are all distinct (maximal entropy, no randomness)."""
    return "".join(
        _TOKEN_ALPHABET[(start + 17 * index) % len(_TOKEN_ALPHABET)]
        for index in range(length)
    )


#: 64-char scanner-only generic token: a slash-bearing 36-char run, repeated.
_SLASH_TOKEN = _synthetic_body(4, start=1) + "/" + _synthetic_body(31, start=9)
_GENERIC_CANDIDATE = (_SLASH_TOKEN * 2)[:64]
#: Short ``omk_`` form: the 13-char suffix is below the audit rule's minimum.
_SHORT_OMK = "omk_" + _synthetic_body(13, start=21)
_PEM_HEADER = "-----BEGIN " + "RSA PRIVATE KEY-----"


def _boundary_runner(token: str | None = None, admin_token: str | None = None) -> Any:
    from omega_prime.grokbot.verify import _Runner

    return _Runner(
        "http://127.0.0.1:9",
        http=cast(Any, None),
        token=token,
        admin_token=admin_token,
        transports=(),
        gated_tool=None,
        timeout=1.0,
        require_auth=False,
        origin_probe="https://evil.example",
    )


def test_scanner_only_candidate_across_clip_edge_is_withheld() -> None:
    """FULL-input gate withholds a generic token the old clip would fragment."""
    from omega_prime.grokbot.secret_guard import SecretLeakError, assert_no_secrets
    from omega_prime.grokbot.verify import _DETAIL_LIMIT, _VERIFIER_SAFE_DETAIL, _Runner

    # Delimited low-entropy padding keeps the candidate its own 32+ run; it
    # starts just before the old clip edge so the old window would retain
    # only a sub-32-character fragment of it.
    window = _DETAIL_LIMIT - 3  # characters _one_line keeps before "..."
    head_len = (window - 12) // 5 * 5  # whole "safe " words, ~12-char fragment
    raw = "safe " * (head_len // 5) + _GENERIC_CANDIDATE + " " + "safe " * 40

    flat = " ".join(raw.split())
    start = flat.index(_GENERIC_CANDIDATE)
    assert len(flat) > _DETAIL_LIMIT
    assert start < window < start + len(_GENERIC_CANDIDATE)
    assert window - start < 32
    with pytest.raises(SecretLeakError):
        assert_no_secrets(raw, "verify-fixture")
    runner = cast(_Runner, _boundary_runner())

    async def factory() -> tuple[str, str]:
        return "fail", raw

    asyncio.run(runner._step("probe", "Probe", factory))
    check = runner.checks[0]
    assert check.status == "fail"
    assert check.detail == _VERIFIER_SAFE_DETAIL
    published = VerifyReport(base_url="http://127.0.0.1:9", checks=runner.checks)
    blob = json.dumps(published.to_dict()) + format_report(published)
    assert _GENERIC_CANDIDATE not in blob
    assert _GENERIC_CANDIDATE[:18] not in blob
    assert _GENERIC_CANDIDATE[2:20] not in blob


def test_secret_smuggled_in_passing_detail_fails_closed() -> None:
    """A secret smuggled into a nominally passing detail cannot stay passing."""
    from omega_prime.grokbot.verify import _VERIFIER_SAFE_DETAIL, _Runner

    runner = cast(_Runner, _boundary_runner())

    async def factory() -> tuple[str, str]:
        return "pass", "gate opened fine: " + _GENERIC_CANDIDATE

    asyncio.run(runner._step("probe", "Probe", factory))
    check = runner.checks[0]
    assert check.status == "fail"
    assert check.detail == _VERIFIER_SAFE_DETAIL
    published = VerifyReport(base_url="http://127.0.0.1:9", checks=runner.checks)
    assert published.ok is False
    assert _GENERIC_CANDIDATE not in format_report(published)


def test_short_omk_exception_diagnostic_is_withheld() -> None:
    """A short `omk_` form in an exception diagnostic leaves no fragment."""
    from omega_prime.grokbot.verify import _VERIFIER_SAFE_DETAIL, _Runner

    runner = cast(_Runner, _boundary_runner())

    async def factory() -> tuple[str, str]:
        raise ValueError(f"boom {_SHORT_OMK}")

    asyncio.run(runner._step("probe", "Probe", factory))
    check = runner.checks[0]
    assert check.status == "fail"
    assert check.detail == _VERIFIER_SAFE_DETAIL
    assert _SHORT_OMK not in check.detail
    assert _SHORT_OMK[4:] not in check.detail
    assert _SHORT_OMK[6:] not in check.detail


def test_pem_exception_diagnostic_is_withheld() -> None:
    """PEM armour in an exception diagnostic publishes only the safe label."""
    from omega_prime.grokbot.verify import _VERIFIER_SAFE_DETAIL, _Runner

    runner = cast(_Runner, _boundary_runner())

    async def factory() -> tuple[str, str]:
        raise RuntimeError(f"key block {_PEM_HEADER} MIIB leak")

    asyncio.run(runner._step("probe", "Probe", factory))
    check = runner.checks[0]
    assert check.status == "fail"
    assert check.detail == _VERIFIER_SAFE_DETAIL
    assert "PRIVATE KEY" not in check.detail
    assert "MIIB" not in check.detail


def test_known_token_redaction_preserves_passing_outcome() -> None:
    """Minted-token scrub is redaction, not a gate trip: clean verdicts stand."""
    from omega_prime.grokbot.verify import _Runner

    token = "env-token-0123456789-abcdefghijklmnop"
    runner = cast(_Runner, _boundary_runner(token=token))

    async def factory() -> tuple[str, str]:
        return "pass", f"authenticated with {token} fine"

    asyncio.run(runner._step("probe", "Probe", factory))
    check = runner.checks[0]
    assert check.status == "pass"
    assert token not in check.detail
    assert "[redacted]" in check.detail


def test_sanitize_withholds_secret_metadata_and_stays_failed() -> None:
    """A poisoned report object cannot publish metadata fragments as success."""
    from omega_prime.grokbot.verify import sanitize_report_for_publication

    poisoned = VerifyReport(
        base_url="http://127.0.0.1:9",
        checks=[
            Check(
                id="probe",
                title="Probe",
                status="pass",
                detail="ok",
                duration_ms=1.0,
            )
        ],
        server_version=_GENERIC_CANDIDATE,
    )
    safe = sanitize_report_for_publication(poisoned)
    assert safe.ok is False
    blob = json.dumps(safe.to_dict()) + format_report(safe)
    assert _GENERIC_CANDIDATE not in blob
    assert _GENERIC_CANDIDATE[:18] not in blob
    assert safe.server_version != _GENERIC_CANDIDATE


def test_cli_publication_withholds_secret_metadata(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Human AND JSON CLI publication reject secret-bearing metadata safely."""
    from omega_prime.grokbot.verify import _VERIFIER_SAFE_DETAIL

    async def handler(request: Request) -> Response:
        if request.url.path == "/healthz":
            body = dict(_health_body("disabled", 0))
            body["version"] = _GENERIC_CANDIDATE
            return JSONResponse(body, headers=_TRACE)
        if request.url.path == "/readyz":
            return JSONResponse({"ready": True})
        return JSONResponse({"error": "no"}, status_code=404)

    with _wrapper(handler) as base:
        json_code = main(["--url", base, "--timeout", "2", "--json"])
        json_out = capsys.readouterr().out
        human_code = main(["--url", base, "--timeout", "2"])
        human_err = capsys.readouterr().err
    assert json_code == 1
    assert human_code == 1
    assert _GENERIC_CANDIDATE not in json_out
    assert _GENERIC_CANDIDATE not in human_err
    assert _VERIFIER_SAFE_DETAIL in json_out
    assert _VERIFIER_SAFE_DETAIL in human_err


def test_cli_usage_error_with_secret_env_label_is_withheld(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A secret-shaped env label in a usage error publishes no fragment."""
    monkeypatch.setenv(_GENERIC_CANDIDATE, "")
    code = main(["--url", "http://127.0.0.1:9", "--token-env", _GENERIC_CANDIDATE])
    captured = capsys.readouterr()
    assert code == 2
    assert _GENERIC_CANDIDATE not in captured.out + captured.err
    assert _GENERIC_CANDIDATE[:18] not in captured.out + captured.err


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(
            ["--url", "http://127.0.0.1:9", "--transport", _SHORT_OMK],
            id="choice",
        ),
        pytest.param(
            ["--url", "http://127.0.0.1:9", "--timeout", _SHORT_OMK],
            id="typed-value",
        ),
        pytest.param(
            ["--url", "http://127.0.0.1:9", _SHORT_OMK],
            id="unrecognized",
        ),
    ],
)
def test_cli_parser_rejections_withhold_short_omk(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    """Parser rejections withhold a recognized short `omk_` form."""
    import omega_prime.grokbot.verify as verify_mod
    from omega_prime.grokbot.verify import _VERIFIER_SAFE_DETAIL

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _must_not_run(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        raise AssertionError("run_verification must not run on parser rejection")

    monkeypatch.setattr(verify_mod, "run_verification", _must_not_run)
    code = main(argv)
    captured = capsys.readouterr()
    assert code == 2
    combined = captured.out + captured.err
    assert captured.out == ""
    assert _SHORT_OMK not in combined
    assert _SHORT_OMK[4:] not in combined
    assert _SHORT_OMK[6:] not in combined
    assert "omk_" not in combined
    assert _VERIFIER_SAFE_DETAIL in captured.err
    assert calls == []


def test_cli_parser_clean_errors_stay_informative(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Clean parser rejections keep their exact wording."""
    assert main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--url" in captured.err
    assert "required" in captured.err
