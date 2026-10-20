"""Enterprise Grok Bot Preflight Health Doctor.

Diagnoses environment readiness, tool rosters, submodule checkouts, native extensions,
manifest consistency, security posture (policy, bind exposure, token strength,
audit chain), and SSE server port availability with remediation guidance.

Secret values are never read into a check message: token checks report lengths and
connector checks report variable names only.
"""

from __future__ import annotations

import argparse
import errno
import importlib
import json
import os
import socket
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omega_prime.engine.jobs import omp_home_override, state_dir
from omega_prime.grokbot.audit import GrokBotAuditTracer, default_audit_path
from omega_prime.grokbot.manifest import (
    find_repo_root,
    generate_manifest,
    lint_template,
)
from omega_prime.grokbot.security import is_loopback_host

TOKEN_ENV = "MCP_AUTH_TOKEN"
RECOMMENDED_TOKEN_LENGTH = 32
CONNECTOR_ENV_VARS = (
    "XAI_API_KEY",
    "X_API_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_BOT_TOKEN",
    "SUBSTRATE_TOKEN",
    "HINDSIGHT_API_KEY",
)
# Tools registered only when their `prime.<family>.enabled` flag is on (default
# off), or where a live agent is available (messaging). `delegate_task` is
# served by the default registry on the in-process desk parent (DESK-03).
_GATED_TOOL_PREFIXES = (
    "rlm_",
    "harness_",
    "goal_",
    "heartbeat_",
    "autonomous_",
    "prime_",
)
_GATED_TOOL_NAMES = frozenset({"agent_message_send", "agent_observe"})
_MAX_LISTED = 10


@dataclass
class DiagnosticCheck:
    id: str
    category: str
    title: str
    status: str  # "PASS", "WARN", "FAIL"
    message: str
    remediation: str | None = None


def _is_gated_prime_tool(name: str) -> bool:
    return name in _GATED_TOOL_NAMES or name.startswith(_GATED_TOOL_PREFIXES)


def _bind_family(host: str) -> socket.AddressFamily:
    return socket.AF_INET6 if ":" in host else socket.AF_INET


def _port_probe(host: str, port: int) -> OSError | None:
    """Try to bind `host:port` exactly as a listener would; `None` when it is free."""
    try:
        with socket.socket(_bind_family(host), socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            if sys.platform != "win32":
                # Match uvicorn: a port in TIME_WAIT is still usable.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
    except OSError as exc:
        return exc
    return None


def _validate_no_symlink(path: Path) -> Path:
    absolute = _control_path(path)
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("unsafe symlink in state or bind path")
    return absolute


def _control_path(path: Path) -> Path:
    expanded = path.expanduser()
    if ".." in expanded.parts:
        raise ValueError("unsafe parent traversal in state or bind path")
    return Path(os.path.abspath(os.fspath(expanded)))


def _paths_overlap(path: Path, bind: Path) -> bool:
    return path == bind or path in bind.parents or bind in path.parents


def _open_readonly_directory(path: Path) -> int:
    """Open existing path components without following symlinks or creating paths."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _inspect_control_children(control_fd: int) -> None:
    """Validate existing request and queue entries without following links."""
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    record_flags = (
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    for directory_name in ("requests", "queue"):
        try:
            directory_fd = os.open(directory_name, directory_flags, dir_fd=control_fd)
        except FileNotFoundError:
            continue
        try:
            directory_info = os.fstat(directory_fd)
            if (
                not stat.S_ISDIR(directory_info.st_mode)
                or directory_info.st_uid != os.getuid()
                or stat.S_IMODE(directory_info.st_mode) != 0o700
            ):
                raise ValueError(
                    f"{directory_name} directory has unsafe type, owner or mode"
                )
            for name in os.listdir(directory_fd):
                try:
                    record_fd = os.open(name, record_flags, dir_fd=directory_fd)
                except FileNotFoundError:
                    continue
                try:
                    record_info = os.fstat(record_fd)
                    if (
                        not stat.S_ISREG(record_info.st_mode)
                        or record_info.st_uid != os.getuid()
                        or stat.S_IMODE(record_info.st_mode) != 0o600
                    ):
                        raise ValueError(
                            "control record has unsafe type, owner or mode"
                        )
                finally:
                    os.close(record_fd)
        finally:
            os.close(directory_fd)


def _ui_control_check(
    env: Mapping[str, str], writable_binds: Sequence[Path]
) -> DiagnosticCheck:
    raw_state_root = state_dir(env)
    control_path = (
        Path(os.path.abspath(os.fspath(raw_state_root.expanduser()))) / "ui-control"
    )
    try:
        state_root = _control_path(raw_state_root)
        control_path = state_root / "ui-control"
        for bind in writable_binds:
            if _paths_overlap(control_path, _validate_no_symlink(bind)):
                raise ValueError(
                    "control directory overlaps an effective writable child bind"
                )
        state_fd = _open_readonly_directory(state_root)
        try:
            state_info = os.fstat(state_fd)
            if not stat.S_ISDIR(state_info.st_mode) or state_info.st_uid != os.getuid():
                raise ValueError("state directory has an unexpected type or owner")
            if stat.S_IMODE(state_info.st_mode) & 0o022:
                raise ValueError("state directory is group- or world-writable")
            control_fd = os.open(
                "ui-control",
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=state_fd,
            )
        finally:
            os.close(state_fd)
        try:
            control_info = os.fstat(control_fd)
            if not stat.S_ISDIR(control_info.st_mode):
                raise ValueError("control path is not a directory")
            if control_info.st_uid != os.getuid():
                raise ValueError("control directory has an unexpected owner")
            if stat.S_IMODE(control_info.st_mode) != 0o700:
                raise ValueError("control directory must have mode 0700")
            _inspect_control_children(control_fd)
        finally:
            os.close(control_fd)
    except (OSError, ValueError) as exc:
        reason = str(exc)
        if isinstance(exc, OSError):
            reason = {
                errno.ENOENT: "state or control directory is missing",
                errno.ELOOP: "unsafe symlink in state or control path",
                errno.EACCES: "state or control directory is not readable",
                errno.ENOTDIR: "unsafe symlink or non-directory in state/control path",
            }.get(
                exc.errno if exc.errno is not None else -1,
                "state or control directory cannot be inspected safely",
            )
        missing = isinstance(exc, OSError) and exc.errno == errno.ENOENT
        return DiagnosticCheck(
            id="ui_control_health",
            category="Security",
            title="UI Control Health",
            status="WARN" if missing else "FAIL",
            message=(
                "state or control directory is not initialized; RPC-UI asks are "
                f"unavailable until configured; path={control_path}"
                if missing
                else f"{reason}; path={control_path}"
            ),
        )
    return DiagnosticCheck(
        id="ui_control_health",
        category="Security",
        title="UI Control Health",
        status="PASS",
        message=f"private control directory is safe; state_path={state_root}, "
        f"state_owner={state_info.st_uid}, state_mode={stat.S_IMODE(state_info.st_mode):04o}, "
        f"control_path={control_path}, control_owner={control_info.st_uid}, "
        f"control_mode={stat.S_IMODE(control_info.st_mode):04o}",
    )


class GrokBotDoctor:
    """Preflight diagnostic engine for Grok Bot integration."""

    def __init__(
        self, root: Path | None = None, *, env: Mapping[str, str] | None = None
    ) -> None:
        self.root = root or find_repo_root()
        self.env: Mapping[str, str] = os.environ if env is None else env

    def run_all_checks(
        self,
        check_port: int = 8000,
        *,
        host: str = "127.0.0.1",
        auth_enabled: bool = False,
    ) -> list[DiagnosticCheck]:
        checks: list[DiagnosticCheck] = []
        checks.extend(self._check_python())
        checks.append(self._check_venv())
        checks.append(self._check_submodule())
        checks.append(self._check_prompt())
        checks.append(self._check_dependencies())
        checks.append(self._check_manifest())
        checks.append(self._check_port(host, check_port))
        checks.append(self._check_audit_writeable())
        checks.append(self._check_policy())
        checks.append(self._check_roster())
        checks.append(self._check_template())
        checks.append(self._check_audit_chain())
        checks.append(self._check_ui_control())
        checks.append(self._check_bind_exposure(host, auth_enabled))
        checks.append(self._check_token_strength())
        checks.append(self._check_connector_secrets())
        return checks

    def _check_ui_control(self) -> DiagnosticCheck:
        binds = [Path.cwd()]
        work_root = str(self.env.get("OMEGA_PRIME_WORK_ROOT", "")).strip()
        if work_root:
            binds.append(Path(work_root).expanduser())
        omp_home = omp_home_override(self.env) or (Path.home() / ".omp")
        if omp_home.exists():
            binds.append(omp_home)
        return _ui_control_check(self.env, binds)

    # -- runtime ----------------------------------------------------------

    @staticmethod
    def _check_python() -> list[DiagnosticCheck]:
        py_ver = sys.version_info
        if py_ver >= (3, 11):
            return [
                DiagnosticCheck(
                    id="python_version",
                    category="Runtime",
                    title="Python Version",
                    status="PASS",
                    message=(
                        f"Python {py_ver.major}.{py_ver.minor}.{py_ver.micro} "
                        "satisfies requirement (>= 3.11)"
                    ),
                )
            ]
        return [
            DiagnosticCheck(
                id="python_version",
                category="Runtime",
                title="Python Version",
                status="FAIL",
                message=(
                    f"Python {py_ver.major}.{py_ver.minor} is below recommended >= 3.11"
                ),
                remediation=(
                    "Upgrade Python to 3.11+ using pyenv or system package manager."
                ),
            )
        ]

    @staticmethod
    def _check_venv() -> DiagnosticCheck:
        is_venv = hasattr(sys, "real_prefix") or (
            hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
        )
        return DiagnosticCheck(
            id="virtual_env",
            category="Runtime",
            title="Virtual Environment",
            status="PASS" if is_venv else "WARN",
            message=f"Executing inside venv ({sys.prefix})"
            if is_venv
            else "Not running in an isolated virtual environment",
            remediation=None
            if is_venv
            else "Run: python3 -m venv .venv && source .venv/bin/activate",
        )

    # -- repository -------------------------------------------------------

    def _check_submodule(self) -> DiagnosticCheck:
        submodule_path = self.root / "prime-agent"
        gitmodules_file = self.root / ".gitmodules"
        has_submodule_def = (
            gitmodules_file.is_file()
            and "prime-agent" in gitmodules_file.read_text(encoding="utf-8")
        )
        has_checkout = submodule_path.is_dir() and any(submodule_path.iterdir())
        if has_submodule_def and has_checkout:
            return DiagnosticCheck(
                id="submodule_prime_agent",
                category="Repository",
                title="prime-agent Submodule",
                status="PASS",
                message="prime-agent submodule is registered and populated",
            )
        if has_submodule_def:
            return DiagnosticCheck(
                id="submodule_prime_agent",
                category="Repository",
                title="prime-agent Submodule",
                status="FAIL",
                message="prime-agent directory is empty or uninitialized",
                remediation="Run: git submodule update --init --recursive",
            )
        return DiagnosticCheck(
            id="submodule_prime_agent",
            category="Repository",
            title="prime-agent Submodule",
            status="WARN",
            message="prime-agent is not tracked in .gitmodules",
            remediation=(
                "Run: git submodule add "
                "https://github.com/PrimeIntellect-ai/prime-agent.git prime-agent"
            ),
        )

    def _check_prompt(self) -> DiagnosticCheck:
        assembled_prompt = (
            self.root / "omega_prime" / "prompts-assembled" / "OMEGA_PRIME.xml"
        )
        if assembled_prompt.is_file() and assembled_prompt.stat().st_size > 1000:
            return DiagnosticCheck(
                id="prompt_assembled",
                category="Prompts",
                title="Assembled OMEGA_PRIME Prompt",
                status="PASS",
                message=(
                    f"OMEGA_PRIME.xml exists ({assembled_prompt.stat().st_size} bytes)"
                ),
            )
        return DiagnosticCheck(
            id="prompt_assembled",
            category="Prompts",
            title="Assembled OMEGA_PRIME Prompt",
            status="FAIL",
            message="OMEGA_PRIME.xml is missing or empty",
            remediation="Run: bash omega_prime/scripts/assemble-prompts.sh",
        )

    @staticmethod
    def _check_dependencies() -> DiagnosticCheck:
        missing_pkgs = []
        for pkg in ("mcp", "starlette", "uvicorn"):
            try:
                importlib.import_module(pkg)
            except ImportError:
                missing_pkgs.append(pkg)
        if not missing_pkgs:
            return DiagnosticCheck(
                id="mcp_dependencies",
                category="Dependencies",
                title="MCP Server Dependencies",
                status="PASS",
                message="mcp, starlette, and uvicorn are available",
            )
        return DiagnosticCheck(
            id="mcp_dependencies",
            category="Dependencies",
            title="MCP Server Dependencies",
            status="FAIL",
            message=f"Missing required packages: {', '.join(missing_pkgs)}",
            remediation=f"Run: pip install {' '.join(missing_pkgs)}",
        )

    # -- Grok Bot ---------------------------------------------------------

    def _check_manifest(self) -> DiagnosticCheck:
        try:
            manifest = generate_manifest(self.root)
            server = manifest.get("mcp_server", {})
            tool_cnt = len(server.get("tools", []))
            gated_cnt = len(server.get("approval_required", []))
            skill_cnt = len(manifest.get("bot", {}).get("skills", []))
        except Exception as exc:
            return DiagnosticCheck(
                id="manifest_generation",
                category="GrokBot",
                title="Manifest Generation",
                status="FAIL",
                message=f"Failed to generate manifest: {exc}",
                remediation=(
                    "Inspect OMEGA_PRIME.md template and "
                    "contracts/tool-rosters/omega-prime.yaml"
                ),
            )
        return DiagnosticCheck(
            id="manifest_generation",
            category="GrokBot",
            title="Manifest Generation",
            status="PASS",
            message=(
                f"Valid manifest produced ({tool_cnt} served tools, "
                f"{gated_cnt} approval-gated, {skill_cnt} skills)"
            ),
        )

    def _check_policy(self) -> DiagnosticCheck:
        from omega_prime.policy.policy import SeatPolicy

        path = self.root / "omega_prime" / "contracts" / "policies" / "omega-prime.json"
        try:
            SeatPolicy.load(path)
        except Exception as exc:
            return DiagnosticCheck(
                id="policy_loads",
                category="Security",
                title="Seat Policy",
                status="FAIL",
                message=f"Seat policy {path} does not load: {exc}",
                remediation=(
                    "Restore omega_prime/contracts/policies/omega-prime.json from "
                    "version control; the server refuses to start without it."
                ),
            )
        return DiagnosticCheck(
            id="policy_loads",
            category="Security",
            title="Seat Policy",
            status="PASS",
            message="Seat policy loads and validates",
        )

    def _check_roster(self) -> DiagnosticCheck:
        title = "Tool Roster"
        path = (
            self.root
            / "omega_prime"
            / "contracts"
            / "tool-rosters"
            / "omega-prime.yaml"
        )
        try:
            from omega_prime.mcp_server import default_registry, roster_names

            roster = roster_names(path.read_text(encoding="utf-8"))
            registry = default_registry(self.root, Path.home(), env={})
            registered = {item["function"]["name"] for item in registry.schemas()}
        except Exception as exc:
            return DiagnosticCheck(
                id="roster_consistency",
                category="GrokBot",
                title=title,
                status="FAIL",
                message=f"Cannot evaluate tool roster {path}: {exc}",
                remediation=(
                    "Restore contracts/tool-rosters/omega-prime.yaml and make sure "
                    "the tool packages import."
                ),
            )
        if not roster:
            return DiagnosticCheck(
                id="roster_consistency",
                category="GrokBot",
                title=title,
                status="FAIL",
                message=f"Roster {path} lists no tools",
                remediation="Restore contracts/tool-rosters/omega-prime.yaml.",
            )
        unknown = [
            name
            for name in roster
            if name not in registered and not _is_gated_prime_tool(name)
        ]
        if unknown:
            shown = ", ".join(unknown[:_MAX_LISTED])
            extra = (
                f" (+{len(unknown) - _MAX_LISTED} more)"
                if len(unknown) > _MAX_LISTED
                else ""
            )
            return DiagnosticCheck(
                id="roster_consistency",
                category="GrokBot",
                title=title,
                status="WARN",
                message=(
                    f"{len(unknown)} rostered tool(s) are not registered: "
                    f"{shown}{extra}"
                ),
                remediation=(
                    "Register the tools or remove them from "
                    "contracts/tool-rosters/omega-prime.yaml."
                ),
            )
        return DiagnosticCheck(
            id="roster_consistency",
            category="GrokBot",
            title=title,
            status="PASS",
            message=(
                f"All {len(roster)} rostered tools are registered or flag-gated "
                "Prime tools"
            ),
        )

    def _check_template(self) -> DiagnosticCheck:
        try:
            lint = lint_template(self.root)
        except Exception as exc:
            return DiagnosticCheck(
                id="template_integrity",
                category="GrokBot",
                title="Bot Template Integrity",
                status="FAIL",
                message=f"Cannot lint bot template: {exc}",
                remediation="Inspect omega_prime/grokbot/templates/OMEGA_PRIME.md",
            )
        if lint.ok:
            return DiagnosticCheck(
                id="template_integrity",
                category="GrokBot",
                title="Bot Template Integrity",
                status="PASS",
                message=(
                    "Every skill and routine in the bot template resolves, and "
                    "every checked-in skill and routine is listed"
                ),
            )
        parts = []
        if lint.missing_skills:
            parts.append(
                "missing skills: " + ", ".join(lint.missing_skills[:_MAX_LISTED])
            )
        if lint.missing_routines:
            parts.append(
                "missing routines: " + ", ".join(lint.missing_routines[:_MAX_LISTED])
            )
        if lint.unlisted_skills:
            parts.append(
                "unlisted skills: " + ", ".join(lint.unlisted_skills[:_MAX_LISTED])
            )
        if lint.unlisted_routines:
            parts.append(
                "unlisted routines: " + ", ".join(lint.unlisted_routines[:_MAX_LISTED])
            )
        return DiagnosticCheck(
            id="template_integrity",
            category="GrokBot",
            title="Bot Template Integrity",
            status="FAIL",
            message="Bot template and disk disagree (" + "; ".join(parts) + ")",
            remediation=(
                "List every checked-in skill and routine in "
                "omega_prime/grokbot/templates/OMEGA_PRIME.md and remove entries "
                "with no file behind them"
            ),
        )

    # -- network ----------------------------------------------------------

    @staticmethod
    def _check_port(host: str, port: int) -> DiagnosticCheck:
        label = host or "all interfaces"
        problem = _port_probe(host, port)
        if problem is None:
            return DiagnosticCheck(
                id="port_availability",
                category="Network",
                title=f"SSE Port {port}",
                status="PASS",
                message=(
                    f"Port {port} on {label} is free and ready for MCP SSE listener"
                ),
            )
        if problem.errno == errno.EADDRINUSE:
            message = f"Port {port} on {label} is already in use by another process"
        else:
            message = f"Cannot bind {label}:{port}: {problem.strerror or problem}"
        return DiagnosticCheck(
            id="port_availability",
            category="Network",
            title=f"SSE Port {port}",
            status="WARN",
            message=message,
            remediation=(
                f"Choose a different port with --port or stop the service using "
                f"port {port}."
            ),
        )

    @staticmethod
    def _check_bind_exposure(host: str, auth_enabled: bool) -> DiagnosticCheck:
        label = host or "all interfaces"
        if is_loopback_host(host):
            return DiagnosticCheck(
                id="bind_exposure",
                category="Security",
                title="Bind Exposure",
                status="PASS",
                message=f"{label} is loopback only",
            )
        if auth_enabled:
            return DiagnosticCheck(
                id="bind_exposure",
                category="Security",
                title="Bind Exposure",
                status="PASS",
                message=f"{label} is network-reachable and bearer auth is enabled",
            )
        return DiagnosticCheck(
            id="bind_exposure",
            category="Security",
            title="Bind Exposure",
            status="FAIL",
            message=f"{label} is network-reachable and authentication is disabled",
            remediation=(
                f"Set a bearer token (export {TOKEN_ENV}=... or --token-file) and "
                "pass --auth, or bind to 127.0.0.1."
            ),
        )

    # -- secrets ----------------------------------------------------------

    def _check_token_strength(self) -> DiagnosticCheck:
        token = self.env.get(TOKEN_ENV, "")
        if not token:
            return DiagnosticCheck(
                id="token_strength",
                category="Security",
                title="Bearer Token Strength",
                status="PASS",
                message=f"{TOKEN_ENV} is not set in this environment",
            )
        length = len(token)
        if length < RECOMMENDED_TOKEN_LENGTH:
            return DiagnosticCheck(
                id="token_strength",
                category="Security",
                title="Bearer Token Strength",
                status="WARN",
                message=(
                    f"{TOKEN_ENV} is {length} characters; "
                    f"{RECOMMENDED_TOKEN_LENGTH}+ recommended"
                ),
                remediation=(
                    "Generate a strong token: python -m omega_prime.grokbot.oneclick "
                    "--generate-token"
                ),
            )
        return DiagnosticCheck(
            id="token_strength",
            category="Security",
            title="Bearer Token Strength",
            status="PASS",
            message=f"{TOKEN_ENV} is {length} characters",
        )

    def _check_connector_secrets(self) -> DiagnosticCheck:
        configured = [name for name in CONNECTOR_ENV_VARS if self.env.get(name)]
        message = (
            "Connector credentials configured: " + ", ".join(configured)
            if configured
            else "No connector credentials configured in this environment"
        )
        return DiagnosticCheck(
            id="connector_secrets",
            category="Security",
            title="Connector Secrets",
            status="PASS",
            message=message,
        )

    # -- audit ------------------------------------------------------------

    def _check_audit_writeable(self) -> DiagnosticCheck:
        audit_file = default_audit_path(self.root)
        try:
            audit_file.parent.mkdir(parents=True, exist_ok=True)
            with open(audit_file, "a", encoding="utf-8") as f:
                f.write("")
        except Exception as exc:
            return DiagnosticCheck(
                id="audit_writeable",
                category="Audit",
                title="Audit Trail Storage",
                status="FAIL",
                message=f"Cannot write to audit log: {exc}",
                remediation=f"Ensure write permissions on {audit_file.parent}",
            )
        return DiagnosticCheck(
            id="audit_writeable",
            category="Audit",
            title="Audit Trail Storage",
            status="PASS",
            message=f"Audit log path {audit_file} is writeable",
        )

    def _check_audit_chain(self) -> DiagnosticCheck:
        audit_file = default_audit_path(self.root)
        try:
            ok, count, message = GrokBotAuditTracer(audit_file).verify_integrity()
        except Exception as exc:
            ok, count, message = False, 0, f"cannot verify: {exc}"
        if ok:
            return DiagnosticCheck(
                id="audit_chain",
                category="Audit",
                title="Audit Chain Integrity",
                status="PASS",
                message=(
                    f"Audit chain intact ({count} records)"
                    if count
                    else "No audit records yet"
                ),
            )
        return DiagnosticCheck(
            id="audit_chain",
            category="Audit",
            title="Audit Chain Integrity",
            status="FAIL",
            message=f"Audit chain is broken: {message}",
            remediation=(
                f"Preserve {audit_file} for investigation, then move it aside so a "
                "new chain starts."
            ),
        )


def summarize_checks(checks: Sequence[DiagnosticCheck]) -> dict[str, Any]:
    """The stable JSON shape: `{"passed": bool, "checks": [...]}`."""
    return {
        "passed": not any(c.status == "FAIL" for c in checks),
        "checks": [
            {
                "id": c.id,
                "name": c.title,
                "category": c.category,
                "status": "ok"
                if c.status == "PASS"
                else ("warn" if c.status == "WARN" else "fail"),
                "message": c.message,
                "fix_hint": c.remediation,
            }
            for c in checks
        ],
    }


def run_doctor_checks(
    root: Path | None = None,
    port: int = 8000,
    *,
    host: str = "127.0.0.1",
    auth_enabled: bool = False,
) -> dict[str, Any]:
    """Run all preflight doctor checks and return a summary dictionary."""
    doctor = GrokBotDoctor(root)
    checks = doctor.run_all_checks(
        check_port=port, host=host, auth_enabled=auth_enabled
    )
    return summarize_checks(checks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preflight Health Doctor for Grok Bot native integration"
    )
    parser.add_argument("--root", type=Path, default=None, help="Repository root")
    parser.add_argument(
        "--port", type=int, default=8000, help="Check availability of specific SSE port"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface the SSE server will bind (default 127.0.0.1)",
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help="The server will run with bearer authentication enabled",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output machine-readable JSON"
    )
    parser.add_argument(
        "--strict", action="store_true", help="Exit 1 on warnings as well as failures"
    )
    args = parser.parse_args(argv)

    doctor = GrokBotDoctor(args.root)
    checks = doctor.run_all_checks(
        check_port=args.port, host=args.host, auth_enabled=args.auth
    )
    has_failures = any(c.status == "FAIL" for c in checks)
    has_warnings = any(c.status == "WARN" for c in checks)
    exit_code = 1 if has_failures or (args.strict and has_warnings) else 0

    if args.json:
        payload = summarize_checks(checks)
        payload["healthy"] = payload["passed"]
        payload["strict"] = args.strict
        print(json.dumps(payload, indent=2))
        return exit_code

    print("=== Omega Prime Grok Bot Health Doctor ===")
    for c in checks:
        tag = f"[{c.status}]"
        print(f"{tag:<8} {c.category:12} - {c.title}: {c.message}")
        if c.remediation and c.status != "PASS":
            print(f"         Remediation: {c.remediation}")
    print("==========================================")
    if has_failures:
        print("Doctor found preflight issues requiring remediation before deployment.")
    elif args.strict and has_warnings:
        print("Doctor found warnings and --strict is set.")
    else:
        print("All preflight checks passed! Grok Bot integration is ready.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
