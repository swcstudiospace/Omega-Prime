# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Turnkey 1-Click Grok Bot CLI.

Orchestrates environment preflight, bind-safety evaluation, manifest export, and
launches the tool host in stdio or SSE mode.

Exit codes: 0 success, 1 self-test failure, 2 configuration error (bad token, unsafe
bind, ...), 3 strict preflight failure. Secrets come from a 0600 token file, a hashed
token store, the environment, or (deprecated) argv; a token is never written to the
manifest, printed to stdout, or put in JSON output. `--self-test` proves a spawned
host: it mints tokens, verifies both transports, and requires SIGTERM to exit 0.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, NoReturn, TextIO
from urllib.parse import urlsplit

from omega_prime.grokbot import doctor as _doctor
from omega_prime.grokbot import receipts as _receipts
from omega_prime.grokbot import rerun as _rerun
from omega_prime.grokbot.approvals_file import (
    load_approvals_file,
    resolve_approvals_digest,
    resolve_approvals_path,
)
from omega_prime.grokbot.manifest import generate_manifest
from omega_prime.grokbot.security import (
    SecurityConfigError,
    TokenStore,
    check_bind_safety,
    generate_token,
)
from omega_prime.settings import load_settings
from omega_prime.tooling.fs import (
    PRIVATE_FILE_MODE,
    atomic_write_text,
    ensure_private_dir,
    read_secret_file,
)

if TYPE_CHECKING:
    from omega_prime.grokbot.local_validation import LocalLayout

EXIT_OK = 0
EXIT_SELF_TEST = 1
EXIT_CONFIG = 2
EXIT_PREFLIGHT = 3

_SELF_TEST_GRACE = 5.0
_SELF_TEST_READY_TIMEOUT = 20.0

DEFAULT_TOKEN_ENV = "MCP_AUTH_TOKEN"
_WILDCARD_HOSTS = frozenset({"", "0.0.0.0", "::", "[::]"})
_PREFIX = "omega-prime-oneclick"
_fcntl: ModuleType | None
try:
    import fcntl as _fcntl_module
except ImportError:  # pragma: no cover - non-POSIX platforms
    _fcntl = None
else:
    _fcntl = _fcntl_module


def _no_export_lock_dir() -> Path:
    """Private per-user dir for the no-export launch lock base."""
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        try:
            st = os.stat(xdg)
        except OSError:
            pass
        else:
            try:
                owned = st.st_uid == os.getuid()
            except AttributeError:  # pragma: no cover - non-POSIX platforms
                owned = True
            if owned and stat.S_ISDIR(st.st_mode):
                return Path(xdg)
    try:
        uid = os.getuid()
    except AttributeError:  # pragma: no cover - non-POSIX platforms
        return Path(tempfile.gettempdir())
    target = Path(tempfile.gettempdir()) / f"omega-prime-oneclick-{uid}"
    ensure_private_dir(target)
    return target


def _launch_lock_base(export_manifest: Path | None, port: int) -> Path:
    """Per-launch identity whose ``<name>.lock`` sidecar serializes launches.

    Launches exporting the same manifest share the receipt-path lock; launches
    without an export share a port-keyed lock under a private per-user dir
    (never the repo tree, so the guard leaves no stray files behind).
    """
    if export_manifest is not None:
        return _receipts.receipt_path_for_manifest(export_manifest)
    return _no_export_lock_dir() / f"omega-prime-oneclick-{port}.launch"


@contextlib.contextmanager
def _hold_launch_lock(base: Path) -> Iterator[None]:
    """Hold an exclusive ``flock`` on ``<base>.lock`` (``fs`` sidecar use).

    A foreign-owned or otherwise unusable lock file never blocks a launch:
    warn on stderr and proceed without the lock (fail-open for availability;
    the rerun guard plus failed-status receipt still protect correctness).

    Acquisition (dir setup, ownership check, open, flock) is tried/excepted;
    the body ``yield`` sits outside that ``try`` so body failures (including
    an ``OSError`` from manifest export, e.g. disk full) propagate untouched
    instead of being mistaken for lock-acquisition failures. An outer
    ``try/finally`` covers setup plus body so a cancel (``KeyboardInterrupt``)
    while blocked in ``flock`` still closes the open descriptor.
    """
    target = Path(base)
    lock_path = target.with_name(f"{target.name}.lock")
    fd: int | None = None
    try:
        try:
            skip = False
            ensure_private_dir(target.parent)
            try:
                st = os.stat(lock_path)
            except FileNotFoundError:
                pass
            else:
                try:
                    foreign = st.st_uid != os.getuid()
                except AttributeError:  # pragma: no cover - non-POSIX platforms
                    foreign = False
                if foreign:
                    print(
                        f"{_PREFIX}: warning: lock {lock_path} owned by another user; "
                        "proceeding without lock",
                        file=sys.stderr,
                    )
                    skip = True
            if not skip:
                fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, PRIVATE_FILE_MODE)
                if _fcntl is not None:
                    try:
                        _fcntl.flock(fd, _fcntl.LOCK_EX)
                    except OSError as exc:
                        print(
                            f"{_PREFIX}: warning: cannot lock {lock_path} ({exc}); "
                            "proceeding without lock",
                            file=sys.stderr,
                        )
                        os.close(fd)
                        fd = None
        except OSError as exc:
            print(
                f"{_PREFIX}: warning: cannot use lock {lock_path} ({exc}); "
                "proceeding without lock",
                file=sys.stderr,
            )
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
                fd = None
        yield
    finally:
        if fd is not None:
            os.close(fd)  # closing the descriptor releases the lock


def run_doctor_checks(
    root: Path, port: int, *, host: str, auth_enabled: bool
) -> dict[str, Any]:
    """The doctor entry point used by the launcher (looked up at call time)."""
    return _doctor.run_doctor_checks(root, port, host=host, auth_enabled=auth_enabled)


def _err(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _fail(message: str) -> int:
    _err(f"{_PREFIX}: {message}")
    return EXIT_CONFIG


def _run_preflight(
    root: Path,
    port: int,
    host: str,
    auth_enabled: bool,
    stream: TextIO,
) -> dict[str, Any]:
    """Run the doctor and print every check to `stream`."""
    results = run_doctor_checks(root, port, host=host, auth_enabled=auth_enabled)
    print("\n--- Grok Bot Preflight Health Checks ---", file=stream)
    for check in results.get("checks", []):
        mark = (
            "✓"
            if check["status"] == "ok"
            else ("⚠" if check["status"] == "warn" else "✗")
        )
        print(f"[{mark}] {check['name']}: {check['message']}", file=stream)
        if check.get("fix_hint") and check["status"] in ("warn", "fail"):
            print(f"    → Fix hint: {check['fix_hint']}", file=stream)
    print("----------------------------------------\n", file=stream)
    return results


def _url_host(host: str) -> str:
    """Host usable in a client URL: wildcard binds become loopback."""
    name = host.strip()
    if name in _WILDCARD_HOSTS:
        return "127.0.0.1"
    if ":" in name and not name.startswith("["):
        return f"[{name}]"
    return name


def _sse_url(host: str, port: int, public_url: str | None) -> str:
    if public_url:
        return public_url.rstrip("/") + "/sse"
    return f"http://{_url_host(host)}:{port}/sse"


def _resolve_token(
    token_file: Path | None,
    argv_token: str | None,
    token_env: str,
    *,
    will_generate: bool,
) -> tuple[str | None, str]:
    """Token and its source ("file", "argv", "env", "none"), file > argv > env.

    A `--token-file` that does not exist yet is only acceptable when a token will be
    generated into it. Raises `ValueError`/`SecurityConfigError` on bad configuration.
    """
    token: str | None = None
    source = "none"
    if token_file is not None and (token_file.exists() or not will_generate):
        token, source = read_secret_file(token_file), "file"
    elif argv_token:
        token, source = argv_token, "argv"
    else:
        value = os.environ.get(token_env, "").strip()
        if value:
            token, source = value, "env"
    if token is not None:
        TokenStore.from_token(token)  # enforces the minimum length
    return token, source


def _announce_generated_token(token: str, token_file: Path | None) -> None:
    _err(f"Generated bearer token (shown only once): {token}")
    _err(
        "Put it in the Grok Bot connector settings; store it with "
        "--token-file PATH to reuse it."
    )
    if token_file is not None:
        atomic_write_text(token_file, token + "\n")
        _err(f"Token also written to {token_file} (mode 0600).")


def _emit_json(payload: dict[str, Any], file: TextIO) -> None:
    print(json.dumps(payload, sort_keys=True), file=file, flush=True)


def run_oneclick(
    root: Path,
    *,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
    export_manifest: Path | None = None,
    auth_token: str | None = None,
    skip_doctor: bool = False,
    dry_run: bool = False,
    public_url: str | None = None,
    token_env: str = DEFAULT_TOKEN_ENV,
    token_file: Path | None = None,
    generate_new_token: bool = False,
    allowed_hosts: Sequence[str] = (),
    allowed_origins: Sequence[str] = (),
    audit_log: Path | None = None,
    allow_insecure_no_auth: bool = False,
    strict: bool | None = None,
    json_output: bool = False,
    token_store_path: Path | None = None,
    log_format: str = "text",
    force: bool = False,
    resume: bool = False,
    work_root: Path | None = None,
    approvals_file: Path | None = None,
    approvals_sha256: str | None = None,
) -> int:
    """Execute the 1-click workflow."""
    sse = transport == "sse"
    settings = load_settings(dict(os.environ)) if sse else None
    effective_env = dict(settings.effective_env) if settings is not None else None
    strict = sse if strict is None else strict
    protocol_stdio = not sse and not dry_run
    # A served stdio process reserves stdout for the child's JSON-RPC frames.
    say = sys.stderr if json_output or protocol_stdio else sys.stdout
    json_sink = sys.stderr if protocol_stdio else sys.stdout
    # --- Resume-safe re-run guard (SSE serve only): stdio spawns a fresh
    # --- subprocess pipe and never binds TCP, so no port conflict is possible;
    # --- dry-run must always validate and report, never no-op.
    # ---
    # --- Double-submit guard: check_rerun through manifest export runs under
    # --- an exclusive flock on a per-launch lock file (a `<receipt>.lock`
    # --- sidecar, the `fs` flock convention), so two concurrent launches for
    # --- the same manifest cannot both pass the guard and export. The lock is
    # --- released before serve dispatch below.
    _guard: Any = contextlib.nullcontext()
    if sse and not dry_run:
        _guard = _hold_launch_lock(_launch_lock_base(export_manifest, port))
    with _guard:
        _verdict = None
        if sse and not dry_run:
            _verdict = _rerun.check_rerun(
                _receipts.receipt_path_for_manifest(export_manifest)
                if export_manifest
                else None,
                port,
                host=host,
            )
        if not force:
            if _verdict == _rerun.VERDICT_ALREADY_COMPLETE:
                print(
                    f"{_PREFIX}: already complete"
                    + (
                        f" (manifest {_verdict.manifest_digest})"
                        if _verdict.manifest_digest
                        else ""
                    )
                    + "; nothing to do.",
                    file=say,
                )
                if json_output:
                    _emit_json(
                        {
                            "status": "already-complete",
                            "transport": transport,
                            "host": host,
                            "port": port,
                            "manifest_digest": _verdict.manifest_digest,
                        },
                        json_sink,
                    )
                return EXIT_OK
            if _verdict == _rerun.VERDICT_ALREADY_RUNNING and not resume:
                print(
                    f"{_PREFIX}: already running (port {port} in use); nothing to do.",
                    file=say,
                )
                if json_output:
                    _emit_json(
                        {
                            "status": "already-running",
                            "transport": transport,
                            "host": host,
                            "port": port,
                        },
                        json_sink,
                    )
                return EXIT_OK
        if _verdict == _rerun.VERDICT_ALREADY_RUNNING and resume:
            print(
                f"{_PREFIX}: already running (port {port} in use); "
                "--resume: continuing into serve.",
                file=say,
            )
        elif _verdict == _rerun.VERDICT_INTERRUPTED:
            print(
                f"{_PREFIX}: previous run was interrupted; resuming launch "
                "(existing state preserved).",
                file=say,
            )

        print("=== Omega Prime ► Grok Bot Native 1-Click Launcher ===", file=say)
        print(f"Root: {root}", file=say)
        print(f"Transport: {transport}", file=say)

        if generate_new_token and not sse:
            return _fail("--generate-token requires --transport sse")
        if public_url:
            parts = urlsplit(public_url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                return _fail("--public-url must be an http(s) URL")

        # --- Secrets and bind safety: nothing is written or served before this passes.
        token: str | None = None
        token_source = "none"
        generated = False
        if sse:
            try:
                token, token_source = _resolve_token(
                    token_file, auth_token, token_env, will_generate=generate_new_token
                )
            except ValueError as exc:  # includes SecurityConfigError
                return _fail(str(exc))
            if generate_new_token:
                if token is None:
                    token, token_source, generated = generate_token(), "generated", True
                else:
                    _err(
                        f"{_PREFIX}: a token is already configured; "
                        "--generate-token ignored"
                    )
        if sse and token is None and token_store_path is not None:
            token_source = "store"
        auth_enabled = token is not None or token_source == "store"
        if sse:
            try:
                check_bind_safety(
                    host,
                    auth_enabled=auth_enabled,
                    allow_insecure=allow_insecure_no_auth,
                )
            except SecurityConfigError as exc:
                return _fail(str(exc))
            if allow_insecure_no_auth and not auth_enabled:
                _err(
                    f"{_PREFIX}: WARNING: serving without authentication "
                    "(--allow-insecure-no-auth)"
                )

        # --- Preflight.
        checks: list[dict[str, Any]] = []
        passed: bool | None = None
        url = _sse_url(host, port, public_url) if sse else None
        auth_info = {
            "enabled": auth_enabled,
            "type": "bearer" if auth_enabled else "none",
            "token_env": token_env,
            "source": token_source,
        }

        def report(manifest_path: Path | None) -> dict[str, Any]:
            return {
                "passed": passed,
                "strict": strict,
                "checks": checks,
                "transport": transport,
                "url": url,
                "manifest_path": str(manifest_path) if manifest_path else None,
                "auth": auth_info,
            }

        if not skip_doctor:
            results = _run_preflight(root, port, host, auth_enabled, say)
            checks = list(results.get("checks", []))
            failures = [c for c in checks if c.get("status") == "fail"]
            passed = bool(results.get("passed", not failures)) and not failures
            if not passed:
                if strict:
                    print(
                        f"ERROR: {len(failures)} preflight check(s) failed; aborting "
                        "(strict). Fix them or pass --no-strict to continue anyway.",
                        file=sys.stderr,
                        flush=True,
                    )
                    if json_output:
                        _emit_json(report(None), json_sink)
                    return EXIT_PREFLIGHT
                print(
                    "WARNING: Some preflight checks failed. Continuing launch...",
                    file=say,
                )

        # --- Manifest (never contains the token).
        manifest: dict[str, Any] | None = None
        manifest_path: Path | None = None
        if export_manifest or dry_run:
            try:
                manifest = generate_manifest(
                    root,
                    host_url=url or "",
                    transport=transport,
                    public_url=public_url if sse else None,
                    auth_enabled=auth_enabled,
                    token_env=token_env,
                )
            except ValueError as exc:
                return _fail(str(exc))
            if export_manifest:
                atomic_write_text(
                    export_manifest, json.dumps(manifest, indent=2) + "\n", mode=0o644
                )
                manifest_path = export_manifest
                print(f"✓ Manifest exported to {export_manifest}", file=say)

        if dry_run:
            print("✓ Dry run requested. Exiting without launching tool host.", file=say)
            if json_output:
                _emit_json(report(manifest_path), json_sink)
            return EXIT_OK

    def _finish(status: str, exit_code: int) -> None:
        if dry_run or export_manifest is None or manifest is None:
            return
        effective = status
        if effective == _receipts.STATUS_COMPLETE and exit_code != 0:
            # A failed launch must never read back as already-complete: only
            # exit 0 completes, anything else fails so the next run retries.
            effective = _receipts.STATUS_FAILED
        record = _receipts.build_receipt(
            transport=transport,
            host=host,
            port=port,
            manifest=manifest,
            token_source=token_source,
            doctor_passed=passed,
            exit_code=exit_code,
            status=effective,
        )
        _receipts.write_receipt(
            _receipts.receipt_path_for_manifest(export_manifest), record
        )

    # NOTE (residual bind race): the launch lock above serializes check_rerun
    # through manifest export on this host, but the final bind() below is a
    # small TOCTOU window — two processes (or hosts) can both pass the guard
    # and race on the port. The loser fails to serve and _finish records
    # STATUS_FAILED (never complete), so check_rerun treats the next attempt
    # as fresh and retries instead of wedging on already-complete.
    if json_output:
        _emit_json(report(manifest_path), json_sink)

    if generated and token is not None:
        _announce_generated_token(token, token_file)

    print(f"Starting Omega Prime MCP Tool Host ({transport})...", file=say, flush=True)
    approvals_path = resolve_approvals_path(approvals_file)
    approvals_digest = resolve_approvals_digest(approvals_sha256)
    if approvals_digest and approvals_path is None:
        return _fail("OMEGA_PRIME_APPROVALS_SHA256 is set without an approvals file")
    file_grants: list[tuple[str, str]] = []
    if approvals_path is not None:
        try:
            file_grants = load_approvals_file(
                approvals_path,
                repo_root=root,
                digest=approvals_digest,
                env=effective_env,
            )
        except ValueError as exc:
            return _fail(str(exc))
    if not sse:
        cmd = [sys.executable, "-m", "omega_prime.mcp_server", "--root", str(root)]
        if approvals_path is not None:
            cmd += [
                "--approvals-file",
                str(approvals_path),
                "--approvals-sha256",
                approvals_digest,
            ]
        if audit_log is not None:
            cmd += ["--audit-log", str(audit_log)]
        if work_root is not None:
            cmd += ["--work-root", str(work_root)]
        try:
            code = subprocess.call(cmd)
        except (KeyboardInterrupt, Exception):
            _finish("interrupted", 130)
            raise
        _finish("complete", code)
        return code

    from omega_prime.grokbot.remote import serve_sse

    audit = None
    if audit_log is not None:
        from omega_prime.grokbot.audit import GrokBotAuditTracer

        audit = GrokBotAuditTracer(audit_log)
    # The bearer token reaches the host only through `token`; it is never
    # exported into this process environment, which serve children inherit.
    try:
        code = serve_sse(
            root=root,
            host=host,
            port=port,
            token=token,
            public_url=public_url,
            allowed_hosts=tuple(allowed_hosts),
            allowed_origins=tuple(allowed_origins),
            allow_insecure_no_auth=allow_insecure_no_auth,
            audit=audit,
            approvals=file_grants,
            token_store_path=token_store_path,
            log_format=log_format,
            work_root=work_root,
            env=effective_env,
            settings_status=settings,
        )
    except (KeyboardInterrupt, Exception):
        _finish("interrupted", 130)
        raise
    _finish("complete", code)
    return code


def _redact_secrets(text: str, secrets: Sequence[str]) -> str:
    cleaned = text
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "[redacted]")
    return " ".join(cleaned.split())


#: Fixed artifact-guard label for every self-test diagnostic publication.
_SELF_TEST_GUARD_LABEL = "self-test"

#: Fixed safe metadata published when the secret gate trips. A short literal
#: with no excerpts, so it carries no secret bytes by construction.
_SELF_TEST_SAFE_DIAGNOSTIC = "self-test: diagnostic withheld (secret gate tripped)"

#: Publication bound for failure diagnostics: keep the tail window, then clip.
_DIAGNOSTIC_TAIL_CHARS = 1500
_DIAGNOSTIC_CLIP_CHARS = 500

#: The self-test child consumes the ONE frozen layout allowlist and nothing
#: else: ``dict(layout.env)`` is the whole ambient environment, so the caller
#: adds no parent-locale copy, no credential blacklist, and no
#: args/env-forwarding-only pins here. Host roots travel as argv
#: (``--root``/``--work-root``/``--home``/``--audit-log``); ``python -m``
#: resolves the install from the child cwd, and state follows ``--root``.


def _bounded_diagnostic(raw: str, secrets: Sequence[str]) -> str:
    """Redact and gate the FULL `raw` text before any clip for publication.

    Known-minted secrets and audit-shaped credentials are scrubbed from the
    FULL text first; the unchanged ``assert_no_secrets`` gate under the fixed
    ``self-test`` label then runs on that FULL redacted text BEFORE the
    tail/clip window is taken, so a scanner-only fragment (generic
    high-entropy token, short ``omk_`` form, PEM armour) straddling a window
    edge cannot be truncated into a publishable fragment the gate would no
    longer recognise. On a trip only the fixed safe metadata is published
    with no raw excerpts. The same unchanged gate still runs on the clipped
    result as defense in depth.
    """
    from omega_prime.grokbot.audit import redact_sensitive
    from omega_prime.grokbot.secret_guard import SecretLeakError, assert_no_secrets

    redacted = redact_sensitive(_redact_secrets(raw, secrets))
    try:
        assert_no_secrets(redacted, _SELF_TEST_GUARD_LABEL)
    except SecretLeakError:
        return _SELF_TEST_SAFE_DIAGNOSTIC
    clipped = redacted[-_DIAGNOSTIC_TAIL_CHARS:][:_DIAGNOSTIC_CLIP_CHARS]
    try:
        assert_no_secrets(clipped, _SELF_TEST_GUARD_LABEL)
    except SecretLeakError:
        return _SELF_TEST_SAFE_DIAGNOSTIC
    return clipped


def _self_test_audit_log(layout: LocalLayout) -> Path:
    """Audit-log file inside the disposable layout.

    ``LocalLayout.audit`` names the audit sink: a directory yields the
    conventional file inside it, otherwise it is already the log file.
    """
    if layout.audit.is_dir():
        return layout.audit / "audit.jsonl"
    return layout.audit


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_ready(
    url: str, proc: subprocess.Popen[bytes], timeout: float
) -> str | None:
    """Poll `/readyz` until it is ready. None on success, else a one-line reason."""
    import httpx2

    deadline = time.monotonic() + timeout
    with httpx2.Client(trust_env=False, timeout=1.0) as http:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return f"host exited {proc.returncode} before /readyz"
            try:
                response = http.get(url)
            except httpx2.HTTPError:
                time.sleep(0.05)
                continue
            if response.status_code == 200:
                try:
                    body = response.json()
                except ValueError:
                    body = None
                if isinstance(body, dict) and body.get("ready") is True:
                    return None
            time.sleep(0.05)
    if proc.poll() is not None:
        return f"host exited {proc.returncode} before /readyz"
    return "timed out waiting for /readyz"


def _terminate(proc: subprocess.Popen[bytes], grace: float) -> int | None:
    """SIGTERM, then wait `grace + 5` seconds. None when it does not exit in time."""
    if proc.poll() is not None:
        return proc.returncode
    proc.send_signal(signal.SIGTERM)
    try:
        return proc.wait(timeout=grace + 5)
    except subprocess.TimeoutExpired:
        proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        return None


def _log_tail(path: Path, secrets: Sequence[str]) -> str:
    """Read the full log text, then redact before any clipping.

    The raw bytes are never clipped pre-redaction: clipping happens inside
    :func:`_bounded_diagnostic` only after the full text is scrubbed and
    gated.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return _bounded_diagnostic(text, secrets)


def run_self_test(root: Path) -> int:
    """Start a loopback host, verify it, and require a clean SIGTERM.

    The host runs from a disposable isolated install (never the source
    checkout): ``--root``/home/audit/tokens resolve inside the frozen shared
    layout, the child env is exactly the frozen layout allowlist with no
    inherited live credentials or config roots, and fixture schedules start
    empty so source-install schedules are never ticked. The parent
    ``os.environ`` is never consulted.

    Mints a call token into a 0600 file and an admin token into a 0600 store.
    Neither token is printed. Exit 0 only when verification passes and the
    host exits 0 after SIGTERM within the grace window.
    """
    from omega_prime.grokbot.local_validation import isolated_layout
    from omega_prime.grokbot.tokens import create_token
    from omega_prime.grokbot.verify import (
        format_report,
        run_verification,
        sanitize_report_for_publication,
    )

    secrets: list[str] = []
    proc: subprocess.Popen[bytes] | None = None
    with isolated_layout(root) as layout:
        log_path = layout.base / "self-test-host.log"
        audit_log = _self_test_audit_log(layout)
        try:
            token = generate_token()
            secrets.append(token)
            token_file = layout.state / "self-test-caller.token"
            atomic_write_text(token_file, token + "\n")
            store = layout.state / "self-test-tokens.json"
            admin_token, _record = create_token(
                store, scopes=["admin"], label="self-test"
            )
            del _record
            secrets.append(admin_token)
            port = _free_port()
            env = dict(layout.env)
            cmd = [
                sys.executable,
                "-m",
                "omega_prime.mcp_server",
                "--transport",
                "sse",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--root",
                str(layout.install),
                "--work-root",
                str(layout.work),
                "--token-file",
                str(token_file),
                "--token-store",
                str(store),
                "--audit-log",
                str(audit_log),
                "--home",
                str(layout.home),
                "--log-format",
                "json",
                "--shutdown-grace",
                str(int(_SELF_TEST_GRACE)),
            ]
            with log_path.open("wb") as log_handle:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(layout.install),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            base = f"http://127.0.0.1:{port}"
            reason = _wait_until_ready(f"{base}/readyz", proc, _SELF_TEST_READY_TIMEOUT)
            if reason is not None:
                _terminate(proc, _SELF_TEST_GRACE)
                if proc.poll() is not None:
                    proc = None
                print(
                    f"{_PREFIX}: self-test: {_bounded_diagnostic(reason, secrets)}",
                    file=sys.stderr,
                )
                tail = _log_tail(log_path, secrets)
                if tail:
                    print(tail, file=sys.stderr)
                return EXIT_SELF_TEST
            try:
                report = run_verification(
                    base,
                    token=token,
                    admin_token=admin_token,
                    transports=("sse", "http"),
                    require_auth=True,
                )
            except Exception as exc:
                print(
                    f"{_PREFIX}: self-test: {_bounded_diagnostic(str(exc), secrets)}",
                    file=sys.stderr,
                )
                return EXIT_SELF_TEST
            shutdown = _terminate(proc, _SELF_TEST_GRACE)
            if proc.poll() is not None:
                proc = None
            safe = sanitize_report_for_publication(report, secrets)
            print(format_report(safe), flush=True)
            shown = "timeout" if shutdown is None else str(shutdown)
            print(f"shutdown={shown}", flush=True)
            if shutdown != 0 or not safe.ok or not safe.reachable:
                return EXIT_SELF_TEST
            return EXIT_OK
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=5)


class _GuardedParser(argparse.ArgumentParser):
    """Argument parser that withholds secret-shaped CLI diagnostics.

    The FULL ``"<prog>: error: <message>"`` text is passed through the
    unchanged ``_bounded_diagnostic`` gate with an empty known-secret
    sequence BEFORE anything is written to stderr, so a recognized canary
    echoed by argparse (invalid choice, ``type=`` rejection, unrecognized
    arguments) can only publish the fixed safe metadata. Clean diagnostics
    are short enough to pass through unchanged, preserving argparse
    usage/exit-2 semantics.
    """

    def error(self, message: str) -> NoReturn:
        raw = f"{self.prog}: error: {message}\n"
        gated = _bounded_diagnostic(raw, [])
        self.print_usage(sys.stderr)
        self.exit(2, gated if gated.endswith("\n") else gated + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = _GuardedParser(prog="omega-prime-grokbot-oneclick")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport type for MCP (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for SSE server")
    parser.add_argument("--port", type=int, default=8000, help="Port for SSE server")
    parser.add_argument(
        "--public-url",
        default=None,
        help="Externally reachable base URL (exported as <url>/sse in the manifest)",
    )
    parser.add_argument(
        "--export-manifest",
        type=Path,
        default=None,
        help="Export Grok Bot manifest JSON to file before serving",
    )
    parser.add_argument(
        "--token-env",
        default=DEFAULT_TOKEN_ENV,
        help=f"Env var holding the bearer token (default: {DEFAULT_TOKEN_ENV})",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=None,
        help="File (mode 0600) holding the bearer token",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="DEPRECATED: bearer token on the command line (visible to other users); "
        "use --token-file or --token-env",
    )
    parser.add_argument(
        "--generate-token",
        action="store_true",
        help="SSE only: mint a bearer token when none is configured and show it once",
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        help="Allowed browser Origin for SSE (repeatable)",
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="Allowed Host header value for SSE (repeatable)",
    )
    parser.add_argument(
        "--audit-log", type=Path, default=None, help="Audit log path for tool calls"
    )
    parser.add_argument(
        "--allow-insecure-no-auth",
        action="store_true",
        help="Permit an unauthenticated listener on a non-loopback address",
    )
    parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Abort when a preflight check fails (default: on for sse, off for stdio)",
    )
    parser.add_argument(
        "--skip-doctor",
        action="store_true",
        help="Skip preflight doctor checks",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run preflight, bind safety and manifest export without starting server",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Start a loopback host, verify it, and require a clean SIGTERM",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print one machine-readable JSON object on stdout (text goes to stderr)",
    )
    parser.add_argument(
        "--token-store",
        type=Path,
        default=None,
        metavar="PATH",
        help="hashed token store; counts as authentication for bind safety",
    )
    parser.add_argument(
        "--log-format",
        choices=["text", "json"],
        default="text",
        help="log format forwarded to the SSE host (default: text)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the resume-safe re-run guard and launch anyway",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue into serve even when a previous host is already running",
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=None,
        help="Repo the desk tools act on (forwarded to the stdio and SSE host; "
        "default: OMEGA_PRIME_WORK_ROOT, else the install root)",
    )
    parser.add_argument(
        "--approvals-file",
        type=Path,
        default=None,
        help=(
            "mode 0600 TOOL:APPROVER file outside the repository and outside "
            "OMEGA_PRIME_STATE_DIR. Forwarded to the stdio host. Never written "
            "by this process. A file under the state directory is not loaded."
        ),
    )
    parser.add_argument(
        "--approvals-sha256",
        default=None,
        help=(
            "sha256 of --approvals-file. A person sets it (or "
            "OMEGA_PRIME_APPROVALS_SHA256). A mismatch refuses to start."
        ),
    )
    args = parser.parse_args(argv)

    if args.self_test:
        if args.dry_run:
            return _fail("--self-test conflicts with --dry-run")
        return run_self_test(args.root.resolve())

    if args.token:
        _err(
            f"{_PREFIX}: WARNING: --token is visible to other users in the process "
            "list; prefer --token-file or the environment variable."
        )

    return run_oneclick(
        args.root.resolve(),
        transport=args.transport,
        host=args.host,
        port=args.port,
        export_manifest=args.export_manifest,
        auth_token=args.token,
        skip_doctor=args.skip_doctor,
        dry_run=args.dry_run,
        public_url=args.public_url,
        token_env=args.token_env,
        token_file=args.token_file,
        generate_new_token=args.generate_token,
        allowed_hosts=args.allow_host,
        allowed_origins=args.allow_origin,
        audit_log=args.audit_log,
        allow_insecure_no_auth=args.allow_insecure_no_auth,
        strict=args.strict,
        json_output=args.json,
        token_store_path=args.token_store,
        log_format=args.log_format,
        force=args.force,
        resume=args.resume,
        work_root=args.work_root.resolve() if args.work_root is not None else None,
        approvals_file=args.approvals_file,
        approvals_sha256=args.approvals_sha256,
    )


if __name__ == "__main__":
    sys.exit(main())
