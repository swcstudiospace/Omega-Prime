"""Omega Prime over MCP stdio: the tool registry as an MCP server.

`build_server` exposes registry tools (optionally roster-gated) to
MCP clients; `main` wires the default registry and serves stdio, so
`python -m omega_prime.mcp_server` is the tool host a Grok Bot install (or
any MCP client) connects to. Policy and approval denials come back
as error results with the registry's own text; approval-gated tools
stay ungated only when the server was built with pre-approvals.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import threading
import time
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import anyio
from anyio.to_thread import run_sync as run_in_worker_thread
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from omega_prime.commands.context import (
    NESTED_DISPATCH_DEPTH,
    nested_depth,
    nested_depth_frame,
    nested_dispatch_scope,
    served_roster_scope,
)
from omega_prime.credentials.broker import CredentialBroker
from omega_prime.cron.desk_driver import DeskDriver
from omega_prime.grokbot.approvals_file import (
    load_approvals_file,
    resolve_approvals_digest,
    resolve_approvals_path,
)
from omega_prime.grokbot.interceptors import (
    ToolCall,
    ToolCallInterceptor,
    call_from_context,
    run_tool_call,
)
from omega_prime.integrations.binding import secret_value
from omega_prime.integrations.desk_clients import (
    clients_from_env,
    guarded_browser_factory,
)
from omega_prime.memory.store import MemoryStore
from omega_prime.policy.policy import SeatPolicy
from omega_prime.providers.anthropic import AnthropicProvider
from omega_prime.providers.base import ProviderModel
from omega_prime.providers.destination import DestinationTransport, SafeFetch
from omega_prime.providers.gemini import GeminiProvider
from omega_prime.providers.grok import GrokProvider
from omega_prime.providers.http import HttpTransport
from omega_prime.providers.ollama import OllamaProvider
from omega_prime.providers.openai import OpenAIProvider
from omega_prime.routines.desk_lead import desk_intake_path
from omega_prime.substrate.client import (
    DEFAULT_BASE_URL as SUBSTRATE_DEFAULT_URL,
)
from omega_prime.substrate.client import (
    SubstrateClient,
    SubstrateError,
)
from omega_prime.tooling.fs import ensure_private_dir, read_secret_file
from omega_prime.tooling.roster import roster_names
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.coding import register_coding_tools
from omega_prime.tools.delegate import register_delegate_tools
from omega_prime.tools.discord import DiscordClient, register_discord_tools
from omega_prime.tools.exec_jobs import register_exec_job_tools
from omega_prime.tools.growth import register_growth_tools
from omega_prime.tools.ide import register_ide_tools
from omega_prime.tools.infra import InfraClient, InfraContext, register_infra_tools
from omega_prime.tools.lead import (
    EventStore,
    IntakeStore,
    LeadClient,
    LeadContext,
    RosterStore,
    register_lead_tools,
)
from omega_prime.tools.mobile import MobileClient, MobileContext, register_mobile_tools
from omega_prime.tools.omega_command import register_omega_command_tools
from omega_prime.tools.packs import PacksClient, PacksContext, register_packs_tools
from omega_prime.tools.platform import register_platform_tools
from omega_prime.tools.quality import (
    QualityClient,
    QualityContext,
    register_quality_tools,
)
from omega_prime.tools.registry import ToolRegistry
from omega_prime.tools.substrate_tools import (
    docs_search as _shape_substrate_docs,
)
from omega_prime.tools.substrate_tools import (
    register_substrate_tools,
)
from omega_prime.tools.systems import (
    SystemsClient,
    SystemsContext,
    register_systems_tools,
)
from omega_prime.tools.telegram import TelegramClient, register_telegram_tools
from omega_prime.tools.ultrathink import (
    UltrathinkClient,
    UltrathinkContext,
    register_ultrathink_tools,
)
from omega_prime.tools.webpack import WebClient, WebContext, register_web_tools
from omega_prime.tools.x import XClient, register_x_tools

SERVER_NAME = "omega-prime"
SERVER_VERSION = "6.0.0"

_T = TypeVar("_T")


class _UrllibXTransport:
    """Minimal X transport over urllib. Only built when a token is configured."""

    @staticmethod
    def _request(
        method: str,
        url: str,
        headers: dict,
        body: Any = None,
        params: dict | None = None,
    ) -> dict:
        import urllib.parse
        import urllib.request

        if params:
            query = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )
            url = f"{url}?{query}" if query else url
        data = json.dumps(body).encode("utf-8") if body is not None else None
        merged = dict(headers)
        if data is not None:
            merged.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=data, headers=merged, method=method)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def get(self, url: str, headers: dict, params: dict) -> dict:
        return self._request("GET", url, headers, params=params)

    def post(self, url: str, headers: dict, body: dict) -> dict:
        return self._request("POST", url, headers, body=body)


def _json_env(env: dict, name: str) -> dict:
    try:
        value = json.loads(env.get(name) or "")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# -- Desk runtime wiring (DESK-01/02/03/08) ---------------------------------
#
# The desk seams are real objects built here, not test fakes: one shared
# `MemoryStore`, JSON/NDJSON stores under `OMEGA_PRIME_STATE_DIR`, the real
# `SubstrateClient`, and an in-process delegate parent. Optional planes
# (bus, docs index, notify webhook) come from env and degrade loudly.

WORK_ROOT_ENV = "OMEGA_PRIME_WORK_ROOT"
DESK_BUS_URL_ENV = "OMEGA_PRIME_DESK_BUS_URL"
DESK_DOCS_INDEX_ENV = "OMEGA_PRIME_DESK_DOCS_INDEX"
DESK_NOTIFY_URL_ENV = "OMEGA_PRIME_DESK_NOTIFY_URL"
DESK_CHILD_PROVIDER_ENV = "OMEGA_PRIME_DESK_CHILD_PROVIDER"
DESK_CHILD_MODEL_ENV = "OMEGA_PRIME_DESK_CHILD_MODEL"


def resolve_work_root(
    root: str | Path,
    env: Mapping[str, str],
    work_root: str | Path | None = None,
) -> Path:
    """DESK-02: the directory the desk works on.

    Explicit argument wins, then `OMEGA_PRIME_WORK_ROOT`, then the install
    `root` (today's behavior). Roster/policy/contract/prompt/ownership
    lookups never follow this: they stay on the install root.
    """
    if work_root is not None:
        return Path(work_root)
    from_env = env.get(WORK_ROOT_ENV, "")
    if isinstance(from_env, str) and from_env.strip():
        return Path(from_env.strip())
    return Path(root)


def _desk_store_path(work_root: Path, state_dir: str | None, name: str) -> Path:
    """One desk store file: under the state dir when set, else the work root."""
    if state_dir:
        return Path(state_dir) / name
    return work_root / "omega_prime" / "state" / name


class _DeskParent:
    """In-process delegate parent (DESK-03).

    Satisfies `omega_prime.agent.delegate`'s parent contract: `child_model`
    runs child turns through the same provider/env wiring the conversation
    loop uses (`_desk_child_model`), and `tools` maps every registered tool
    name to a registry dispatcher, so children act behind the same policy and
    approval gate as the host. `tools` is computed from the live registry on
    each access, so the parent can exist before the last families register.
    """

    def __init__(self, registry: ToolRegistry, model: Any) -> None:
        self._registry = registry
        self.child_model = model
        self.delegate_depth = 0
        self.max_depth = 2
        self.max_children = 1
        self.journal: Any = None

    @property
    def tools(self) -> dict[str, Any]:
        return {
            item["function"]["name"]: _desk_dispatch(
                self._registry, item["function"]["name"]
            )
            for item in self._registry.schemas()
        }


def _desk_dispatch(registry: ToolRegistry, name: str) -> Any:
    def dispatch(**arguments: Any) -> str:
        return registry.dispatch(name, arguments)

    return dispatch


class _RlmParent(_DeskParent):
    """`_DeskParent` plus the RLM parent contract's session identity.

    Not a second implementation of child running: the child model and the
    live-registry tool map come from `_DeskParent`, so RLM children run
    through the same seams and behind the same policy and approval gate as
    `delegate_task` children. `require_parent`'s extra fields name the
    durable session: `session_dir` is where child session documents persist
    (the desk store convention), and `session_name` is the owning bot
    identity a restarted host recovers its own children by.
    """

    def __init__(self, registry: ToolRegistry, model: Any, session_dir: Path) -> None:
        super().__init__(registry, model)
        self.session_dir = session_dir
        self.session_name = "bot-00-omega-prime"
        self.rlm_unconfigured = False


def _rlm_child_runner(parent: Any) -> Any:
    """One RLM child prompt through the delegate child runner.


    `delegate_task` builds the child agent from the parent's own `child_model`
    and `tools` seams and returns its final response wrapped in JSON; the
    runner unwraps it to the response text `RlmHost` expects. The desk child
    model is the configured one, so a spawn's optional `model`/`thinking`
    selectors are accepted and not honored.
    """
    from omega_prime.agent.delegate import delegate_task

    def run_child(
        prompt: str, *, model: str | None = None, thinking: str | None = None
    ) -> str:
        del model, thinking
        out = json.loads(delegate_task(parent, prompt))
        if not isinstance(out.get("summary"), str):
            raise RuntimeError(out.get("error", "delegate child produced no summary"))
        return out["summary"]

    return run_child


_DESK_CHILD_PROVIDERS: tuple[tuple[str, Any], ...] = (
    ("grok", GrokProvider()),
    ("anthropic", AnthropicProvider()),
    ("openai", OpenAIProvider()),
    ("gemini", GeminiProvider()),
)


def _desk_child_model(env: dict, policy: Any) -> Any:
    """The delegate child model, or None when no provider env is configured.

    Same provider/env wiring as the conversation loop: one provider adapter
    over `HttpTransport`, the key resolved through the seat policy's
    credential broker when a policy exists, else straight from the env.
    `OMEGA_PRIME_DESK_CHILD_PROVIDER` names the adapter (`grok`,
    `anthropic`, `openai`, `gemini`, `ollama` — the last is the only way to
    pick keyless ollama); otherwise the first provider whose key variable is
    set wins. `OMEGA_PRIME_DESK_CHILD_MODEL` names the model id; without it
    there is no child model and delegation stays honestly unconfigured.
    """
    model_name = (env.get(DESK_CHILD_MODEL_ENV) or "").strip()
    wanted = (env.get(DESK_CHILD_PROVIDER_ENV) or "").strip().lower()
    provider: Any = None
    if wanted:
        provider = next(
            (
                candidate
                for name, candidate in (
                    *_DESK_CHILD_PROVIDERS,
                    ("ollama", OllamaProvider()),
                )
                if name == wanted
            ),
            None,
        )
        if provider is None:
            raise ValueError(
                f"unknown {DESK_CHILD_PROVIDER_ENV} {wanted!r}: "
                "want grok, anthropic, openai, gemini, or ollama"
            )
    else:
        provider = next(
            (
                candidate
                for _, candidate in _DESK_CHILD_PROVIDERS
                if any(_secret_present(env, name) for name in candidate.env_vars)
            ),
            None,
        )
    if provider is None or not model_name:
        return None
    transport = HttpTransport(policy=policy) if policy is not None else HttpTransport()
    if policy is not None:
        return ProviderModel(
            provider, model_name, transport, broker=CredentialBroker(policy, env)
        )
    key = ""
    for name in provider.env_vars:
        found = secret_value(env, name)
        if found:
            key = found
            break
    return ProviderModel(provider, model_name, transport, api_key=key)


def _secret_present(env: Mapping[str, str], name: str) -> bool:
    return bool(secret_value(env, name))


class _DeskSubstrate:
    """Adapt the real `SubstrateClient` onto the lead pack's injected seam.

    The seam (`omega_prime.tools.lead.LeadContext.substrate`) is
    duck-typed: `emit(event_dict)` for `lead_event_emit` and
    `call_tool(name, payload, timeout=...)` for the graph tools, both
    returning dicts that carry `body`/`content` on success and
    `error`/`reason` on failure. This adapter forwards onto the real client
    (`emit(kind, summary, **fields)`, MCP `tools/call`) so desk events and
    graph claims hit the actual substrate plane.
    """

    def __init__(self, client: SubstrateClient) -> None:
        self._client = client

    def emit(self, event: Any) -> dict:
        kind = event.get("kind") if isinstance(event, dict) else None
        summary = f"lead {kind}" if isinstance(kind, str) and kind else "lead note"
        fields = {
            key: value
            for key, value in (event.items() if isinstance(event, dict) else ())
            if key != "kind" and value is not None
        }
        result = self._client.emit("note", summary, **fields)
        if isinstance(result, dict) and result.get("stored") is False:
            return {
                "error": "upstream_error",
                "reason": str(result.get("error") or "event not accepted"),
            }
        return {"body": result}

    def call_tool(self, name: str, payload: dict, timeout: float = 10) -> dict:
        # The client's own transport timeout governs the round trip; the seam
        # carries one so injected fakes can honor it.
        _ = timeout
        try:
            return {"content": self._client._call_tool(name, payload)}
        except SubstrateError as exc:
            return {"error": "upstream_error", "reason": str(exc)}


class _DeskDocsIndex:
    """`lead_docs_search` over the substrate docs plane (DESK-08)."""

    def __init__(self, client: SubstrateClient) -> None:
        self._client = client

    def __call__(self, query: str, limit: int, repo: str | None) -> list:
        _ = repo
        result = _shape_substrate_docs(self._client, query)
        chunks = result.get("chunks") if isinstance(result, dict) else None
        if not isinstance(chunks, list):
            reason = (
                str(result.get("error"))
                if isinstance(result, dict) and result.get("error")
                else "docs index returned a malformed response"
            )
            raise RuntimeError(f"docs index failed: {reason}")
        return chunks[:limit]


class _DeskNotifier:
    """`lead_intake_ack` notify callback: POST the body to one webhook."""

    def __init__(self, url: str) -> None:
        self._url = url

    def __call__(self, link: str, body: str) -> bool:
        import urllib.request

        payload = json.dumps({"target": link, "body": body}).encode("utf-8")
        request = urllib.request.Request(
            self._url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return 200 <= response.status < 300
        except OSError:
            return False


class _DeskBusClient:
    """Agent-bus client over urllib (ports desk-gateway `upstreams.AgentBus`).

    `start_job` POSTs `/v1/jobs`; `wait_job` polls `GET /v1/jobs/{id}` until a
    terminal status or the deadline. Failures return the seam's error shape —
    no local fallback queue, the desk degrades loudly (DESK-08).
    """

    _TERMINAL = ("completed", "failed", "error")

    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        *,
        honor_binding: bool = False,
        binding_env: Mapping[str, str] | None = None,
        policy: Any = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._honor_binding = honor_binding
        self._binding_env = binding_env
        self._policy = policy

    def start_job(
        self,
        runtime: str,
        goal: str,
        provider: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {"runtime": runtime, "goal": goal}
        if provider:
            body["provider"] = provider
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        return self._request("POST", "/v1/jobs", body)

    def wait_job(self, job_id: str, timeout_sec: int, poll_sec: int) -> dict:
        deadline = time.monotonic() + timeout_sec
        while True:
            last = self._request("GET", f"/v1/jobs/{job_id}")
            body = last.get("body") if last.get("ok") else None
            status = body.get("status") if isinstance(body, dict) else None
            if last.get("error") or status in self._TERMINAL:
                return last
            if time.monotonic() >= deadline:
                return {**last, "timed_out": True}
            time.sleep(max(int(poll_sec), 0) or 1)

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        import urllib.request

        base, blocked = self._resolve_base()
        if blocked is not None:
            return {
                "ok": False,
                "error": str(blocked.get("error") or "desk_bus gateway is blocked"),
                "gateway": "desk_bus",
            }
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            base + path,
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            return {
                "ok": False,
                "error": "upstream_error",
                "reason": f"agent bus unreachable: {exc}",
            }
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error": "upstream_error",
                "reason": "agent bus returned no JSON object",
            }
        payload.setdefault("ok", True)
        return payload

    def _resolve_base(self) -> tuple[str, dict | None]:
        if not self._honor_binding:
            return self._base_url, None
        from urllib.parse import urlsplit

        from omega_prime.integrations.gateways import gateway_block, ready_gateway_url

        blocked = gateway_block("desk_bus", self._binding_env)
        if blocked is not None:
            return "", blocked
        saved = ready_gateway_url("desk_bus", self._binding_env)
        if saved:
            if self._policy is not None:
                try:
                    host = urlsplit(saved).hostname or ""
                except ValueError:
                    host = ""
                if not host or not self._policy.allows_host(host):
                    shown = host or "that host"
                    return "", {
                        "ok": False,
                        "error": f"The seat policy does not allow {shown}.",
                        "gateway": "desk_bus",
                    }
            return saved.rstrip("/"), None
        return self._base_url, None


_SIDECAR_LOG = logging.getLogger(__name__)


def _sidecar_import(name: str) -> Any:
    """Import ``sidecar.<name>`` (stdlib-only harness module).

    Works when the repo root is on ``sys.path`` (source checkout, tests)
    and falls back to a file-location load from the install root otherwise.
    Only ever loads ``sidecar/`` modules — never ``redteam/``.
    """
    try:
        import importlib

        return importlib.import_module(f"sidecar.{name}")
    except ImportError:
        pass
    import importlib.util

    root = Path(__file__).resolve().parent.parent
    path = root / "sidecar" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"sidecar.{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - missing tree
        raise ImportError(f"cannot load sidecar module {name!r} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _packaged_sandbox_policy() -> str:
    """Absolute path of the shipped OpenShell policy (work-root independent).

    The host may launch with ``--work-root`` pointing at another project,
    so a relative default would resolve against the wrong directory both
    for ``digest_file()`` and for the ``--policy`` argument OpenShell
    receives while running in the gate's work directory.
    """
    return str(
        Path(__file__).resolve().parent
        / "hosting"
        / "openshell"
        / "sandbox-policy.yaml"
    )


def _quality_sandbox_from_env(env: Mapping[str, str]) -> dict[str, Any] | None:
    """Sandbox node for the served quality tools (Phase 83, SAND-02).

    Reads the ``OMEGA_PRIME_SANDBOX_*`` overrides with the config.py
    semantics: only an explicit truthy ``ENABLED`` routes gates through
    :class:`SandboxRunner`; anything else returns None so gates run
    exactly as today (byte-identical flag-off behavior). Scope validation
    stays in ``quality._sandbox_cfg`` (bogus scope disables, never enables).
    An explicit ``OMEGA_PRIME_SANDBOX_POLICY`` stays as given; the default
    is the packaged absolute policy path.
    """
    get = env.get if isinstance(env, Mapping) else {}.get
    enabled = str(get("OMEGA_PRIME_SANDBOX_ENABLED", "") or "").strip().lower()
    if enabled not in ("1", "true", "yes", "on"):
        return None
    policy = str(get("OMEGA_PRIME_SANDBOX_POLICY", "") or "").strip()
    scope = str(get("OMEGA_PRIME_SANDBOX_SCOPE", "") or "").strip().lower()
    return {
        "enabled": True,
        "policy": policy or _packaged_sandbox_policy(),
        "scope": scope or "gates",
    }


def _log_sidecar_transition(from_state: str, to_state: str, cause: str) -> None:
    """Append one audit row. Best-effort: logging never breaks startup."""
    try:
        _sidecar_import("state").transition(from_state, to_state, cause)
    except Exception as exc:
        _SIDECAR_LOG.debug("sidecar transition log skipped: %s", exc)


def _resolve_roe_key(node: Mapping[str, Any], env: Mapping[str, str]) -> bytes | None:
    """RoE verification key under the credential-broker rule.

    ``OMEGA_PRIME_REDTEAM_ROE_KEY`` wins (passed env, then the process
    environment, then the merged config value, which ``load_config``
    overlays from that same variable); otherwise the file named by
    ``redteam.roe_key_file`` (read with the secret-file permission gate,
    mirroring ``bearer_token_file``). A missing or unreadable file
    degrades to no key — the validator then fails closed to
    ``roe-invalid``. A key value stored in the config *file* never
    reaches here: ``load_config`` marks it and the guard rejects
    fail-closed first.
    """
    get = env.get if isinstance(env, Mapping) else {}.get
    candidates = (
        get("OMEGA_PRIME_REDTEAM_ROE_KEY", ""),
        os.environ.get("OMEGA_PRIME_REDTEAM_ROE_KEY", ""),
        node.get("roe_key") or "",
    )
    for raw in candidates:
        if isinstance(raw, str) and raw.strip():
            return raw.strip().encode("utf-8")
    key_file = node.get("roe_key_file") or ""
    if isinstance(key_file, str) and key_file.strip():
        try:
            text = read_secret_file(key_file.strip())
        except (OSError, ValueError):
            return None
        text = text.strip()
        return text.encode("utf-8") if text else None
    return None


def _evaluate_redteam_guard(
    config: dict[str, Any], env: Mapping[str, str] | None = None
) -> tuple[str, str]:
    """Fail-closed verdict for the red team family: ``(state, cause)``.

    Evaluation order (GAP-08): kill switch first, then the default-off gate,
    then the broker-rule key check, then signed-RoE resolution. Any guard
    error implies ``BLOCKED`` with zero registrations. The default-off path
    (``redteam.enabled`` false, kill unset) is silent — no audit row — so
    default behavior is unchanged.

    The RoE helper is imported from :mod:`omega_prime.redteam.roe` (shipped
    in the wheel and the image); the ``sidecar/`` tree is never on the
    installed import path.
    """
    from omega_prime.config import redteam_enabled, redteam_kill, redteam_roe_files
    from omega_prime.redteam.roe import authorize_all

    try:
        if redteam_kill(config):
            _log_sidecar_transition("IDLE", "BLOCKED", "kill-switch-set")
            _SIDECAR_LOG.warning(
                "redteam sidecar BLOCKED (kill-switch-set): kill switch set"
            )
            return "BLOCKED", "kill-switch-set"
        if not redteam_enabled(config):
            return "BLOCKED", "disabled"
        node = config.get("redteam", {})
        node = node if isinstance(node, dict) else {}
        required = node.get("roe_required", True)
        if isinstance(required, str):
            required = required.strip().lower() in ("1", "true", "yes", "on")
        if not required:
            _log_sidecar_transition("IDLE", "IDLE", "re-evaluated")
            return "IDLE", "re-evaluated"
        if node.get("roe_key_in_file"):
            _log_sidecar_transition("IDLE", "BLOCKED", "roe-key-in-file")
            _SIDECAR_LOG.warning(
                "redteam sidecar BLOCKED (roe-key-in-file): redteam.roe_key "
                "must not hold a key value in omega-prime.json — remove it "
                "and use OMEGA_PRIME_REDTEAM_ROE_KEY or "
                "redteam.roe_key_file instead"
            )
            return "BLOCKED", "roe-key-in-file"
        resolve_env: Mapping[str, str] = env if isinstance(env, Mapping) else os.environ
        key = _resolve_roe_key(node, resolve_env)
        decision = authorize_all(redteam_roe_files(config), key=key)
        if decision.ok:
            _log_sidecar_transition("IDLE", "IDLE", "re-evaluated")
            return "IDLE", "re-evaluated"
        _log_sidecar_transition("IDLE", "BLOCKED", decision.cause)
        _SIDECAR_LOG.warning(
            "redteam sidecar BLOCKED (%s): %s", decision.cause, decision.detail
        )
        return "BLOCKED", decision.cause
    except Exception as exc:
        _log_sidecar_transition("IDLE", "BLOCKED", "roe-invalid")
        _SIDECAR_LOG.warning(
            "redteam sidecar BLOCKED (roe-invalid): guard error (%s)", exc
        )
        return "BLOCKED", "roe-invalid"


def _guard_grant(
    config: dict[str, Any], env: Mapping[str, str] | None = None
) -> dict[str, Any] | None:
    """Merged signed grant for the red team client, or ``None`` when absent.

    Re-resolves the guard's ``authorize_all`` decision so the client keeps
    the merged grant (union tool allowlist + window) for call-time
    enforcement. Any failure means no grant — the client then keeps
    historical behavior and registration stays filtered by the same
    absence.
    """
    try:
        from omega_prime.config import redteam_roe_files
        from omega_prime.redteam.roe import authorize_all

        node = config.get("redteam", {})
        node = node if isinstance(node, dict) else {}
        if node.get("roe_key_in_file"):
            return None
        resolve_env: Mapping[str, str] = env if isinstance(env, Mapping) else os.environ
        key = _resolve_roe_key(node, resolve_env)
        decision = authorize_all(redteam_roe_files(config), key=key)
        if decision.ok and isinstance(decision.roe, dict):
            return decision.roe
    except Exception:
        pass
    return None


def default_registry(
    root: str | Path,
    home: str | Path,
    *,
    policy: Any = None,
    approval_log: Any = None,
    env: dict | None = None,
    work_root: str | Path | None = None,
) -> ToolRegistry:
    """Wire every family, including `delegate_task` on an in-process parent.

    `policy` gates dispatch names and file writes; without it the
    registry keeps historical behavior. The desk works on `work_root`
    (`--work-root` / `OMEGA_PRIME_WORK_ROOT`, default the install `root`):
    coding tools root at `root/omega_prime` exactly when the work root is the
    install root, else at the work root itself, and the IDE/LSP/DAP jail and
    `QualityContext.root` follow the work root. Roster, policy, contract,
    prompt, and ownership lookups stay on the install root (DESK-02).

    The lead pack gets real seams (DESK-01): one shared `MemoryStore`, the
    intake/roster/event stores under `OMEGA_PRIME_STATE_DIR` (default the
    work root's `omega_prime/state`), and the substrate client. Connector credentials come
    from `env` (`X_API_TOKEN`, `TELEGRAM_BOT_TOKEN`,
    `DISCORD_BOT_TOKEN`, `ULTRATHINK_ROOT`, `OMEGA_PRIME_PACKS_JSON`,
    `OMEGA_PRIME_PACK_API_BASES_JSON`, `SUBSTRATE_URL`, `SUBSTRATE_TOKEN`,
    `SUBSTRATE_TOKEN_GROK_BOT`); missing values stay unconfigured
    and fail safe at call time. Desk service clients are built from
    `RAILWAY_TOKEN` (optional `OMEGA_PRIME_RAILWAY_PROJECTS`),
    `GREPTILE_API_KEY` (optional `GREPTILE_GITHUB_TOKEN`), `VERCEL_TOKEN`,
    `PLAY_CONSOLE_TOKEN`, and `ASC_KEY_ID` + `ASC_ISSUER_ID` +
    `ASC_PRIVATE_KEY`. A missing token leaves that context field None.
    `GuardedBrowserFactory` is attached only when it constructs. Desk
    optionals (`OMEGA_PRIME_DESK_BUS_URL`,
    `OMEGA_PRIME_DESK_DOCS_INDEX`, `OMEGA_PRIME_DESK_NOTIFY_URL`) wire their
    planes only when set and otherwise return explicit `not_configured`
    results (DESK-08). Delegate children need a provider env
    (`OMEGA_PRIME_DESK_CHILD_MODEL` plus a provider key, or
    `OMEGA_PRIME_DESK_CHILD_PROVIDER`); without one `delegate_task` is still
    served but returns `not_configured: provider`.
    """
    env = dict(env or {})
    from omega_prime.tools.plugins import load_plugins, plugins_enabled

    plugins_on = plugins_enabled(env)
    registry = ToolRegistry(approval_log=approval_log, policy=policy)
    root = Path(root)
    home = Path(home)
    work = resolve_work_root(root, env, work_root)
    legacy_root = root / "omes"
    target_root = root / "omega_prime"
    if legacy_root.is_dir():
        for item in ("memory", "skills", "sessions.db"):
            src = legacy_root / item
            dst = target_root / item
            if src.exists() and not dst.exists():
                try:
                    if src.is_dir():
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
                except Exception:
                    pass
    # DESK-02: a distinct work root is itself the coding workspace; the
    # default keeps the shipped `root/omega_prime` jail.
    coding_root = work if work != root else root / "omega_prime"
    register_coding_tools(registry, coding_root, policy=policy)
    # `OMEGA_PRIME_STATE_DIR` moves the growth stores and the desk stores out
    # of the (possibly read-only) source tree. New skills follow that volume
    # when the install skills directory cannot be written. The state skills
    # directory is created before the tools are registered so a read can fall
    # through to the shipped copy, and creating a shipped name is refused.
    state_dir = env.get("OMEGA_PRIME_STATE_DIR")
    state_value = state_dir if isinstance(state_dir, str) and state_dir else ""
    if state_value:
        memory_dir = Path(state_value) / "memory"
        session_db = Path(state_value) / "sessions.db"
    else:
        memory_dir = root / "omega_prime" / "memory"
        session_db = root / "omega_prime" / "sessions.db"
    # DESK-01: one MemoryStore instance shared by the growth tools and the
    # lead pack, so `lead_memory_recall` sees what the growth tools retain.
    memory_store = MemoryStore(memory_dir)
    from omega_prime.routines.nightly import writable_skills_root

    install_skills = root / "omega_prime" / "skills"
    skills_root = writable_skills_root(install_skills, env)
    if skills_root != install_skills:
        ensure_private_dir(skills_root)
    register_growth_tools(
        registry,
        skills_root=skills_root,
        skills_fallback=install_skills if skills_root != install_skills else None,
        memory_dir=memory_dir,
        session_db=session_db,
        memory_store=memory_store,
    )
    # DESK-03: delegate_task is served here (roster order: after growth), on
    # an in-process parent shim. Without a provider env the parent stays None
    # and the tool answers `not_configured: provider` instead of fabricating
    # a child. The shim's `tools` map reads the live registry, so children
    # see every family registered below.
    parent = None
    child_model = _desk_child_model(env, policy)
    if child_model is not None:
        parent = _DeskParent(registry, child_model)
        try:
            from omega_prime.durable.journal import TurnJournal

            parent.journal = TurnJournal(
                _desk_store_path(work, state_value, "turns.sqlite")
            )
        except Exception:
            parent.journal = None
    register_delegate_tools(registry, parent)
    register_omega_command_tools(registry, env)
    register_platform_tools(registry, home=home)
    register_ide_tools(registry, root, jail=None if work == root else work)
    job_control = register_exec_job_tools(registry, cwd=work)
    registry.runtime_bindings["exec_job_control"] = job_control
    x_token = secret_value(env, "X_API_TOKEN")
    register_x_tools(
        registry,
        XClient(transport=_UrllibXTransport() if x_token else None, token=x_token),
    )
    register_telegram_tools(
        registry,
        TelegramClient(make_bot=None, token=secret_value(env, "TELEGRAM_BOT_TOKEN")),
    )
    register_discord_tools(
        registry,
        DiscordClient(make_client=None, token=secret_value(env, "DISCORD_BOT_TOKEN")),
    )
    substrate_token = secret_value(env, "SUBSTRATE_TOKEN") or secret_value(
        env, "SUBSTRATE_TOKEN_GROK_BOT"
    )
    substrate_client = SubstrateClient(
        env.get("SUBSTRATE_URL", "") or SUBSTRATE_DEFAULT_URL,
        token=substrate_token or None,
        honor_binding=True,
        binding_env=env,
        policy=policy,
    )
    docs_url = (env.get(DESK_DOCS_INDEX_ENV) or "").strip()
    notify_url = (env.get(DESK_NOTIFY_URL_ENV) or "").strip()
    bus_url = (env.get(DESK_BUS_URL_ENV) or "").strip()
    from omega_prime.integrations.gateways import gateway_block, ready_gateway_url

    bus_saved = ready_gateway_url("desk_bus", env)
    bus_blocked = gateway_block("desk_bus", env)
    register_lead_tools(
        registry,
        LeadClient(
            LeadContext(
                root=root,
                registry=registry,
                memory=memory_store,
                intake=IntakeStore(
                    desk_intake_path(work, state_dir=state_value or None)
                ),
                roster=RosterStore(_desk_store_path(work, state_value, "packs.json")),
                events=EventStore(_desk_store_path(work, state_value, "events.ndjson")),
                substrate=_DeskSubstrate(substrate_client),
                docs_index=_DeskDocsIndex(
                    SubstrateClient(
                        docs_url,
                        token=substrate_token or None,
                    )
                )
                if docs_url
                else None,
                notify=_DeskNotifier(notify_url) if notify_url else None,
                bus=(
                    _DeskBusClient(
                        bus_saved or bus_url or "http://127.0.0.1",
                        honor_binding=True,
                        binding_env=env,
                        policy=policy,
                    )
                    if bus_blocked is not None or bus_saved or bus_url
                    else None
                ),
            )
        ),
    )
    register_systems_tools(registry, SystemsClient(SystemsContext(root=root)))
    desk = clients_from_env(env)
    transport = DestinationTransport(policy)
    safe_fetch = SafeFetch(transport)
    web_ctx = WebContext(
        root=root,
        policy=policy,
        transport=transport,
        fetch=safe_fetch,
        vercel=desk["vercel"],
        browser_factory=guarded_browser_factory(transport),
    )
    register_web_tools(registry, WebClient(web_ctx))
    bind_dispatch_transport(registry, transport)
    register_mobile_tools(
        registry,
        MobileClient(MobileContext(root=root, play=desk["play"], asc=desk["asc"])),
    )
    register_infra_tools(
        registry,
        InfraClient(InfraContext(railway=desk["railway"], projects=desk["projects"])),
    )
    register_quality_tools(
        registry,
        QualityClient(
            QualityContext(
                root=work,
                greptile=desk["greptile"],
                sandbox=_quality_sandbox_from_env(env),
            )
        ),
    )
    register_packs_tools(
        registry,
        PacksClient(
            PacksContext(
                packs=_json_env(env, "OMEGA_PRIME_PACKS_JSON"),
                api_bases=_json_env(env, "OMEGA_PRIME_PACK_API_BASES_JSON"),
            )
        ),
    )
    register_ultrathink_tools(
        registry,
        UltrathinkClient(UltrathinkContext(root=env.get("ULTRATHINK_ROOT", ""))),
    )
    register_substrate_tools(registry, substrate_client)
    from omega_prime.tools.cron_admin import register_cron_admin_tools
    from omega_prime.tools.durable_surface import register_durable_surface_tools
    from omega_prime.tools.learning_surface import register_learning_surface_tools

    register_cron_admin_tools(registry, root)
    register_learning_surface_tools(registry, root / "omega_prime")
    register_durable_surface_tools(registry, root)
    from omega_prime.tools.capacity import register_capacity_tools
    from omega_prime.tools.dispatch import register_dispatch_tools

    register_capacity_tools(registry)
    register_dispatch_tools(registry)
    # Prime capability families are config-gated (default off): a disabled
    # family is not registered and is absent from the offered roster (LOOP-05).
    # RLM registers on the desk parent when its flag is on. Without a child
    # model the tools stay served and spawn/create answer
    # ``not_configured: provider``. Messaging registers on the seat session
    # name when its flag is on. Harness, goals, heartbeat, autonomous, and
    # kernel are self-contained and register here when their flags are on.
    # The red team family registers from its own top-level `redteam` key.
    from omega_prime.config import (
        family_config,
        load_config,
        prime_enabled,
    )
    from omega_prime.tools.autonomous import register_autonomous_tools
    from omega_prime.tools.goals import register_goal_tools
    from omega_prime.tools.harness import register_harness_tools
    from omega_prime.tools.heartbeat import register_heartbeat_tools

    config = load_config(root)
    if prime_enabled(config, "harness"):
        register_harness_tools(registry, root)
    if prime_enabled(config, "goals"):
        register_goal_tools(registry, root)
    if prime_enabled(config, "heartbeat"):
        register_heartbeat_tools(registry, root)
    if prime_enabled(config, "autonomous"):
        register_autonomous_tools(
            registry, root, config=family_config(config, "autonomous")
        )
    if prime_enabled(config, "kernel"):
        from omega_prime.tools.prime_runtime import register_prime_kernel_tools

        register_prime_kernel_tools(registry, root)
    if prime_enabled(config, "rlm"):
        from omega_prime.tools.rlm import register_rlm_tools

        session_dir = _desk_store_path(work, state_value, "rlm")
        session_dir.mkdir(parents=True, exist_ok=True)
        rlm_parent = _RlmParent(registry, child_model, session_dir)
        rlm_parent.rlm_unconfigured = child_model is None
        register_rlm_tools(
            registry,
            rlm_parent,
            run_child=None if child_model is None else _rlm_child_runner(rlm_parent),
        )
    if prime_enabled(config, "messaging"):
        from omega_prime.tools.agent_message import register_messaging_tools

        register_messaging_tools(registry, "bot-00-omega-prime")
    # The red team family is a top-level config key (`redteam`), not a Prime
    # family. Off by default, so the default registry is unchanged; when on,
    # the T3MP3ST loopback tools register against the configured gateway with
    # the upstream bearer contract (T3MP3ST_BEARER_TOKEN / _FILE, else the
    # configured bearer_token_file) — but only after the Phase 79 sidecar
    # guard authorizes the run. Kill switch first, then default-off, then
    # signed-RoE resolution; BLOCKED always means zero registrations.
    sidecar_state, sidecar_cause = _evaluate_redteam_guard(config, env)
    registry.runtime_bindings["sidecar_state"] = sidecar_state
    registry.runtime_bindings["sidecar_cause"] = sidecar_cause
    if sidecar_state == "IDLE":
        from omega_prime.redteam.roe import grant_tool_allowlist
        from omega_prime.tools.redteam import (
            DEFAULT_GATEWAY_URL,
            RedteamClient,
            RedteamContext,
            register_redteam_tools,
            resolve_bearer_token,
        )

        redteam_node = config.get("redteam", {})
        redteam_node = redteam_node if isinstance(redteam_node, dict) else {}
        # The signed grant stays on the client: registration exposes only
        # the grant's allowed tools, and the client re-enforces the
        # allowlist plus the effective window at call time.
        grant = _guard_grant(config, env)
        register_redteam_tools(
            registry,
            RedteamClient(
                RedteamContext(
                    gateway_url=str(
                        redteam_node.get("gateway_url") or DEFAULT_GATEWAY_URL
                    ),
                    bearer_token=resolve_bearer_token(
                        env,
                        read_secret_file,
                        str(redteam_node.get("bearer_token_file") or ""),
                    ),
                    roe=grant,
                    policy=policy,
                )
            ),
            allowed=grant_tool_allowlist(grant) if grant is not None else None,
        )
        try:
            from omega_prime.tools.redteam import sidecar_tracking_start

            sidecar_tracking_start("IDLE")
        except Exception:
            _SIDECAR_LOG.debug("sidecar tracking not started")
    # DESK-04: the production desk-pass driver ticks due ``desk_lead_pass``
    # jobs against the shared JobStore on a background thread, serialized.
    # It holds the runtime's parent shim — None without a provider env never
    # crashes the server: every pass then writes explicit blocked receipts.
    # Starting is idempotent; the driver shares the heartbeat store path
    # convention (`root/cron/jobs.json`) so the desk pass and the plain cron
    # tick see one schedule file. Built last so a catch-up tick meets the
    # fully registered tool surface.
    driver = DeskDriver(
        root,
        parent=parent,
        work_root=work,
        approval_log=approval_log,
        can_apply_pin_bump=(
            lambda: bool(
                approval_log is not None
                and callable(getattr(approval_log, "is_approved", None))
                and approval_log.is_approved("pin_bump_apply")
            )
        ),
    )
    registry.runtime_bindings["desk_driver"] = driver
    # Started by the live server (stdio main / serve_sse), not here: building
    # a registry is also how doctor, setup_check, the catalog and tests
    # inspect the tool surface, and those must not spawn a ticking thread.
    if plugins_on:
        load_plugins(home, registry)
    return registry


_BOUND_TRANSPORTS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_BOUND_TRANSPORTS_LOCK = threading.Lock()

_DISPATCH_ROOT_TOOLS = (
    "web_preview_check",
    "web_review_page",
    "browser_navigate",
    "browser_snapshot",
)

_DISPATCH_DEADLINES = {
    "web_preview_check": 15.0,
    "browser_snapshot": 15.0,
    "web_review_page": 60.0,
    "browser_navigate": 60.0,
}


class _SerialCoordinator:
    """Serial dispatch gate for one registry's shared transport."""

    def __init__(self, transport: DestinationTransport) -> None:
        self.transport = transport
        self.lock = threading.Lock()
        self.pending = 0


def _coordinator_for(registry: ToolRegistry) -> _SerialCoordinator | None:
    with _BOUND_TRANSPORTS_LOCK:
        return _BOUND_TRANSPORTS.get(registry)


def bind_dispatch_transport(
    registry: ToolRegistry, transport: DestinationTransport
) -> None:
    """Bind one shared transport to a registry's dispatch path."""
    with _BOUND_TRANSPORTS_LOCK:
        existing = _BOUND_TRANSPORTS.get(registry)
        if existing is not None:
            if existing.transport is not transport:
                raise ValueError("registry is already bound to a different transport")
            return
        _BOUND_TRANSPORTS[registry] = _SerialCoordinator(transport)
    inner = registry.dispatch

    def _bound_dispatch(name: str, arguments: Any = None) -> str:
        if name not in _DISPATCH_ROOT_TOOLS:
            return inner(name, arguments)
        coordinator = _coordinator_for(registry)
        if coordinator is None or coordinator.transport is not transport:
            return json.dumps(
                {"error": "not_configured: no preview transport is bound"}
            )
        with coordinator.lock:
            if coordinator.pending >= 16:
                return json.dumps({"error": "upstream_error: dispatch queue is full"})
            coordinator.pending += 1
        try:
            cap = _DISPATCH_DEADLINES.get(name, 15.0)
            try:
                root = transport.begin_operation(
                    deadline_at=transport._clock.monotonic() + cap
                )
            except Exception:
                return json.dumps({"error": "upstream_error: cannot begin operation"})
            if root.abort_handle.reason is not None:
                transport.finish_operation(
                    root, deadline_at=transport._clock.monotonic() + 5.0
                )
                return json.dumps({"error": "upstream_error: operation was cancelled"})
            binding = transport.bind_operation(root)
            with binding:
                payload = inner(name, arguments)
            drain = transport.finish_operation(
                root, deadline_at=transport._clock.monotonic() + 5.0
            )
            if not drain.complete:
                return json.dumps(
                    {
                        "error": f"upstream_error: operation drain incomplete (pending={drain.pending_scopes}, abort={drain.abort_reason})"
                    }
                )
            return payload
        finally:
            with coordinator.lock:
                coordinator.pending -= 1

    registry.dispatch = _bound_dispatch  # type: ignore[method-assign]


def _mcp_tools(registry: ToolRegistry, roster: list[str] | None) -> list[Tool]:
    allowed = set(roster) if roster is not None else None
    out: list[Tool] = []
    for schema in registry.schemas():
        fn = schema.get("function", {})
        name = fn.get("name", "")
        if allowed is not None and name not in allowed:
            continue
        out.append(
            Tool(
                name=name,
                description=fn.get("description", ""),
                input_schema=fn.get("parameters", {"type": "object"}),
            )
        )
    return out


def list_tools_handler(registry: ToolRegistry, roster: list[str] | None = None):
    """MCP `tools/list` over the registry, optionally roster-gated."""
    from mcp.types import ListToolsResult

    tools = _mcp_tools(registry, roster)

    async def _list_tools(ctx: Any, params: Any) -> Any:
        del ctx, params
        return ListToolsResult(tools=tools)

    return _list_tools


class ToolGate:
    """Runs blocking tool calls in worker threads, `limit` at a time.

    Tools were written for the serial stdio host and share files and state
    without locks, so calls are serialized by default (`limit=1`). Running them
    off the event loop keeps health probes, keepalives and shutdown responsive
    while a tool works, and lets tools that start their own loop (`asyncio.run`)
    run. A cancelled caller keeps its slot until the worker thread ends: threads
    cannot be interrupted, so a disconnect must not start a second call.

    Share one gate between every server built on one registry.
    """

    def __init__(self, limit: int = 1) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.limit = limit
        # anyio needs a running loop to build a limiter, so it is made on first use.
        self._limiter: anyio.CapacityLimiter | None = None

    async def run(self, fn: Callable[..., _T], *args: Any) -> _T:
        if self._limiter is None:
            self._limiter = anyio.CapacityLimiter(self.limit)
        return await run_in_worker_thread(fn, *args, limiter=self._limiter)


def call_tool_handler(
    registry: ToolRegistry,
    roster: list[str] | None = None,
    *,
    interceptors: Sequence[ToolCallInterceptor] = (),
    transport: str = "stdio",
    gate: ToolGate | None = None,
):
    """MCP `tools/call` through `registry.dispatch`, roster-enforced.

    `interceptors` wrap every call (see `omega_prime.grokbot.interceptors`); a
    denial is returned as an error result and the tool is not dispatched. The
    call runs in a worker thread behind `gate` (one at a time by default).
    """

    allowed = set(roster) if roster is not None else None
    chain = tuple(interceptors)
    tool_gate = gate if gate is not None else ToolGate()

    async def _call_tool(ctx: Any, params: Any) -> Any:
        from mcp.types import CallToolResult

        name = params.name
        arguments = params.arguments or {}

        def _reenter(tool: str, args: dict) -> str:
            if nested_depth() >= NESTED_DISPATCH_DEPTH:
                return json.dumps(
                    {
                        "error": "nested_dispatch_depth",
                        "reason": "inner dispatch exceeded depth 2",
                        "tool": tool,
                    }
                )
            with nested_depth_frame():
                if allowed is not None and tool not in allowed:
                    return json.dumps({"error": f"policy forbids {tool}", "tool": tool})
                inner = ToolCall(
                    name=tool,
                    arguments=dict(args) if isinstance(args, dict) else {},
                    principal=call.principal,
                    transport=call.transport,
                    request_id=call.request_id,
                )

                def _terminal() -> str:
                    payload = args if isinstance(args, dict) else {}
                    return registry.dispatch(tool, payload)

                body, _outcome = run_tool_call(chain, inner, _terminal)
                return body

        def _dispatch() -> str:
            if allowed is not None and name not in allowed:
                return json.dumps({"error": f"policy forbids {name}", "tool": name})
            with nested_dispatch_scope(_reenter):
                roster_names = frozenset(allowed) if allowed is not None else None
                with served_roster_scope(roster_names):
                    return registry.dispatch(name, arguments)

        call = call_from_context(ctx, name, arguments, transport=transport)
        # ``is_error`` follows the payload: a null ``error`` is a status field,
        # not a failure (``prime_crates`` reports ``"error": null`` once the
        # extension loaded).
        payload, outcome = await tool_gate.run(run_tool_call, chain, call, _dispatch)
        is_error = outcome.is_error
        return CallToolResult(
            content=[TextContent(type="text", text=payload)], is_error=is_error
        )

    return _call_tool


def build_server(
    registry: ToolRegistry,
    roster: list[str] | None = None,
    *,
    interceptors: Sequence[ToolCallInterceptor] = (),
    transport: str = "stdio",
    gate: ToolGate | None = None,
) -> Server:
    """Serve `registry` over MCP. `roster` limits listing and calling."""
    return Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        on_list_tools=list_tools_handler(registry, roster),
        on_call_tool=call_tool_handler(
            registry,
            roster,
            interceptors=interceptors,
            transport=transport,
            gate=gate,
        ),
    )


class RuntimeConfigError(ValueError):
    """A runtime configuration problem. The message is one line, safe to print."""


@dataclass(frozen=True)
class Runtime:
    """Everything both transports need, built once and failing closed."""

    root: Path
    home: Path
    roster: list[str] | None
    policy: SeatPolicy
    registry: ToolRegistry
    approval_log: ApprovalLog
    tool_names: list[str]
    gated_tools: list[str]
    # DESK-02/03 contract: the directory the desk works on and the in-process
    # delegate parent (None without a provider env — delegate_task then
    # answers `not_configured: provider`).
    work_root: Path
    parent: Any


def _desk_driver_of(runtime: object) -> DeskDriver | None:
    """The live server's desk driver, or None when this runtime has none.

    Test fakes stand in a bare registry; building a registry must not be the
    only way to discover that, and a missing binding is not an error.
    """
    registry = getattr(runtime, "registry", None)
    bindings = getattr(registry, "runtime_bindings", None)
    if not isinstance(bindings, dict):
        return None
    driver = bindings.get("desk_driver")
    return driver if isinstance(driver, DeskDriver) else None


def load_runtime(
    root: str | Path,
    home: str | Path | None = None,
    *,
    no_roster: bool = False,
    approvals: Sequence[tuple[str, str]] = (),
    env: Mapping[str, str] | None = None,
    work_root: str | Path | None = None,
) -> Runtime:
    """Load roster, seat policy and approvals, then build the registry.

    A missing or unreadable roster, an invalid seat policy, or a refused approval
    raises `RuntimeConfigError`; nothing falls back to a more permissive setup.
    `env=None` reads `os.environ` (never mutated).
    """
    root = Path(root)
    home = Path(home) if home is not None else Path.home()
    roster: list[str] | None = None
    if not no_roster:
        roster_path = (
            root / "omega_prime" / "contracts" / "tool-rosters" / "omega-prime.yaml"
        )
        try:
            roster = roster_names(roster_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeConfigError(
                f"cannot read roster {roster_path}: {exc}"
            ) from exc
        if not roster:
            raise RuntimeConfigError(f"roster {roster_path} lists no tools")
    try:
        policy = SeatPolicy.load(
            root / "omega_prime" / "contracts" / "policies" / "omega-prime.json"
        )
    except (OSError, ValueError) as exc:
        raise RuntimeConfigError(f"cannot load seat policy: {exc}") from exc
    log = ApprovalLog()
    for tool, approver in approvals:
        if not tool or not approver:
            raise RuntimeConfigError(
                f"bad approval {(tool, approver)!r}, want TOOL:APPROVER"
            )
        outcome = log.approve(tool, approver)
        if not outcome.get("approved"):
            raise RuntimeConfigError(
                f"cannot pre-approve {tool}: {outcome.get('error')}"
            )
    registry = default_registry(
        root,
        home,
        policy=policy,
        approval_log=log,
        env=dict(os.environ if env is None else env),
        work_root=work_root,
    )
    tool_names = [tool.name for tool in _mcp_tools(registry, roster)]
    gated = [name for name in tool_names if registry.approval_required(name)]
    return Runtime(
        root=root,
        home=home,
        roster=roster,
        policy=policy,
        registry=registry,
        approval_log=log,
        tool_names=tool_names,
        gated_tools=gated,
        work_root=resolve_work_root(
            root, dict(os.environ if env is None else env), work_root
        ),
        parent=registry.runtime_bindings.get("delegate_parent"),
    )


async def _serve(server: Server) -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omega-prime-mcp-server")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument(
        "--work-root",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "directory the desk works on: coding/IDE/quality roots follow it "
            "while roster/policy stay on --root "
            "(default: OMEGA_PRIME_WORK_ROOT, else --root)"
        ),
    )
    parser.add_argument(
        "--no-roster",
        action="store_true",
        help="serve every registered tool, not just the roster",
    )
    parser.add_argument(
        "--approve",
        action="append",
        default=[],
        metavar="TOOL:APPROVER",
        help="pre-approve one gated tool (repeatable)",
    )
    parser.add_argument(
        "--approvals-file",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "mode 0600 file of TOOL:APPROVER lines, outside the repository "
            "and outside OMEGA_PRIME_STATE_DIR. A person writes it. The "
            "process only reads it, at startup."
        ),
    )
    parser.add_argument(
        "--approvals-sha256",
        default=None,
        metavar="HEX",
        help=(
            "sha256 of --approvals-file. A person sets it. A mismatch "
            "refuses to start. OMEGA_PRIME_APPROVALS_SHA256 is the fallback."
        ),
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="transport type for MCP (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="host for SSE transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="port for SSE transport (default: 8000)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help=(
            "DEPRECATED bearer token for SSE transport (visible in the process "
            "list; prefer --token-file or --token-env)"
        ),
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=None,
        help="read the bearer token from this file (must not be group/world-readable)",
    )
    parser.add_argument(
        "--token-env",
        default="MCP_AUTH_TOKEN",
        metavar="NAME",
        help="environment variable holding the bearer token (default: MCP_AUTH_TOKEN)",
    )
    parser.add_argument(
        "--public-url",
        default=None,
        metavar="URL",
        help="externally visible URL of the SSE endpoint, when behind a proxy",
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        metavar="HOST[:PORT]",
        help="additional accepted Host header value (repeatable)",
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="additional accepted Origin header value (repeatable)",
    )
    parser.add_argument(
        "--allow-insecure-no-auth",
        action="store_true",
        help="allow serving SSE without a bearer token on a non-loopback host",
    )
    parser.add_argument(
        "--audit-log",
        type=Path,
        default=os.environ.get("OMEGA_PRIME_AUDIT_LOG") or None,
        metavar="PATH",
        help="append a hash-chained audit trail here (env: OMEGA_PRIME_AUDIT_LOG)",
    )
    parser.add_argument(
        "--max-body-bytes",
        type=int,
        default=1_048_576,
        metavar="N",
        help="largest accepted SSE request body (default: 1048576)",
    )
    parser.add_argument(
        "--shutdown-grace",
        type=float,
        default=20.0,
        metavar="SECONDS",
        help="seconds to let in-flight SSE calls finish on shutdown (default: 20)",
    )
    parser.add_argument(
        "--token-store",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "hashed multi-token store; an authentication source that may be "
            "combined with the single bearer token"
        ),
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=600,
        metavar="N",
        help="requests per minute per principal (0 disables, default: 600)",
    )
    parser.add_argument(
        "--global-rate-limit",
        type=int,
        default=0,
        metavar="N",
        help="requests per minute for every principal (0 disables, default: 0)",
    )
    parser.add_argument(
        "--tool-rate-limit",
        type=int,
        default=300,
        metavar="N",
        help="tool calls per minute per principal (0 disables, default: 300)",
    )
    parser.add_argument(
        "--auth-failure-limit",
        type=int,
        default=10,
        metavar="N",
        help="auth failures per 60s per client (0 disables, default: 10)",
    )
    parser.add_argument(
        "--breaker-threshold",
        type=int,
        default=5,
        metavar="N",
        help=(
            "infrastructure failures before a tool circuit opens "
            "(0 disables, default: 5)"
        ),
    )
    parser.add_argument(
        "--breaker-cooldown",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="seconds a tool circuit stays open (default: 30)",
    )
    parser.add_argument(
        "--no-metrics",
        action="store_true",
        help="do not expose the Prometheus /metrics endpoint",
    )
    parser.add_argument(
        "--metrics-scope",
        choices=["read", "call", "admin"],
        default="read",
        help="scope required to read /metrics (default: read)",
    )
    parser.add_argument(
        "--log-format",
        choices=["text", "json"],
        default="text",
        help="server log format (default: text)",
    )
    parser.add_argument(
        "--session-idle-timeout",
        type=float,
        default=1800.0,
        metavar="SECONDS",
        help="idle seconds before a streamable session is dropped (default: 1800)",
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=64,
        metavar="N",
        help="maximum concurrent streamable sessions (default: 64)",
    )
    parser.add_argument(
        "--approval-max-ttl",
        type=float,
        default=86400.0,
        metavar="SECONDS",
        help="longest approval TTL the admin API accepts, in seconds (default: 86400)",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    from omega_prime.grokbot.audit import default_audit_path

    resolved_audit = args.audit_log if args.audit_log else default_audit_path(root)
    host_env = dict(os.environ)
    host_env["OMEGA_PRIME_AUDIT_LOG"] = str(resolved_audit)

    def _fail(reason: object) -> int:
        print(f"omega-prime-mcp-server: {reason}", file=sys.stderr)
        return 2

    approvals: list[tuple[str, str]] = []
    approvals_path = resolve_approvals_path(args.approvals_file)
    approvals_digest = resolve_approvals_digest(args.approvals_sha256)
    if approvals_digest and approvals_path is None:
        return _fail("OMEGA_PRIME_APPROVALS_SHA256 is set without an approvals file")
    if approvals_path is not None:
        try:
            approvals.extend(
                load_approvals_file(
                    approvals_path, repo_root=root, digest=approvals_digest
                )
            )
        except ValueError as exc:
            return _fail(exc)
    for item in args.approve:
        tool, _, approver = item.partition(":")
        if not tool or not approver:
            return _fail(f"bad --approve {item!r}, want TOOL:APPROVER")
        approvals.append((tool, approver))

    # SSE knobs are validated even when stdio will ignore them, so a bad value
    # is a usage error before either transport starts.
    non_negative: tuple[tuple[str, int | float], ...] = (
        ("--rate-limit", args.rate_limit),
        ("--global-rate-limit", args.global_rate_limit),
        ("--tool-rate-limit", args.tool_rate_limit),
        ("--auth-failure-limit", args.auth_failure_limit),
        ("--breaker-threshold", args.breaker_threshold),
        ("--breaker-cooldown", args.breaker_cooldown),
    )
    for flag, value in non_negative:
        if value < 0:
            return _fail(f"{flag} must not be negative")
    positive: tuple[tuple[str, int | float], ...] = (
        ("--session-idle-timeout", args.session_idle_timeout),
        ("--max-sessions", args.max_sessions),
        ("--approval-max-ttl", args.approval_max_ttl),
    )
    for flag, value in positive:
        if value <= 0:
            return _fail(f"{flag} must be positive")

    if args.transport == "sse":
        from omega_prime.grokbot.audit import GrokBotAuditTracer
        from omega_prime.grokbot.remote import serve_sse

        try:
            token = _resolve_token(args)
            audit = GrokBotAuditTracer(args.audit_log) if args.audit_log else None
        except ValueError as exc:
            return _fail(exc)
        return serve_sse(
            root,
            host=args.host,
            port=args.port,
            token=token,
            home=args.home,
            no_roster=args.no_roster,
            work_root=args.work_root,
            public_url=args.public_url,
            allowed_hosts=tuple(args.allow_host),
            allowed_origins=tuple(args.allow_origin),
            allow_insecure_no_auth=args.allow_insecure_no_auth,
            audit=audit,
            approvals=approvals,
            env=host_env,
            max_body_bytes=args.max_body_bytes,
            shutdown_grace=args.shutdown_grace,
            token_store_path=args.token_store,
            rate_limit=args.rate_limit,
            global_rate_limit=args.global_rate_limit,
            tool_rate_limit=args.tool_rate_limit,
            auth_failure_limit=args.auth_failure_limit,
            breaker_threshold=args.breaker_threshold,
            breaker_cooldown=args.breaker_cooldown,
            metrics_enabled=not args.no_metrics,
            metrics_scope=args.metrics_scope,
            log_format=args.log_format,
            session_idle_timeout=args.session_idle_timeout,
            max_sessions=args.max_sessions,
            approval_max_ttl=args.approval_max_ttl,
        )

    try:
        runtime = load_runtime(
            root,
            args.home,
            no_roster=args.no_roster,
            approvals=approvals,
            env=host_env,
            work_root=args.work_root,
        )
    except ValueError as exc:
        return _fail(exc)
    driver = _desk_driver_of(runtime)
    bindings = getattr(runtime.registry, "runtime_bindings", {})
    job_control = (
        bindings.get("exec_job_control") if isinstance(bindings, dict) else None
    )
    try:
        if driver is not None:
            driver.start()
        asyncio.run(_serve(build_server(runtime.registry, runtime.roster)))
    finally:
        try:
            if driver is not None:
                driver.stop()
        finally:
            shutdown = getattr(job_control, "shutdown", None)
            if callable(shutdown):
                shutdown()
    return 0


def _resolve_token(args: argparse.Namespace) -> str | None:
    """`--token-file` > `--token` > the `--token-env` variable; none means no auth."""
    if args.token_file is not None:
        return read_secret_file(args.token_file)
    if args.token:
        print(
            "omega-prime-mcp-server: warning: --token is visible to other users "
            "in the process list; use --token-file or --token-env instead",
            file=sys.stderr,
        )
        return str(args.token)
    return os.environ.get(args.token_env) or None


if __name__ == "__main__":
    raise SystemExit(main())
