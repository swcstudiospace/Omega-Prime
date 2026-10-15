# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Red team pack: T3MP3ST loopback gateway tools as an Omega Prime family.

Mirrors the upstream T3MP3ST MCP proxy (``redteam/src/mcp-server.ts``): every
tool is one canonical call over the loopback HTTP API — no shells, no child
processes, and an unreachable gateway degrades to a ``gateway_unreachable``
error row instead of raising. Mission start/stop, operator tasking, and hat
dispatch demand approval. The HTTP transport is an injected seam (stdlib
``http.client`` by default); tests use fakes only and never open a socket.
A non-loopback gateway URL is refused unless a bearer token is configured,
per the upstream bearer contract (``T3MP3ST_BEARER_TOKEN`` / ``_FILE``).
"""

from __future__ import annotations

import http.client
import json
import os
import urllib.parse
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any

from omega_prime.tools.registry import ToolRegistry

REDTEAM_TOOL_NAMES = (
    "tempest_status",
    "tempest_mission_start",
    "tempest_mission_stop",
    "tempest_report",
    "tempest_operators_list",
    "tempest_operator_task",
    "tempest_findings",
    "tempest_providers",
    "tempest_purple_synthesize",
    "tempest_blue_gaps",
    "tempest_black_cover",
    "tempest_white_roe",
    "tempest_hats_list",
    "tempest_hat_dispatch",
)

DEFAULT_GATEWAY_URL = "http://127.0.0.1:3333"

KILL_ENV_VAR = "OMEGA_PRIME_REDTEAM_KILL"

# Tracked harness state for gateway-boundary hooks. ``None`` (the default)
# means tracking is off: direct client users and unit tests see zero new
# behavior. The registry guard enables it on the IDLE path via
# :func:`sidecar_tracking_start`.
_sidecar_current: str | None = None


def _kill_switch_set(env: Mapping[str, str] | None = None) -> bool:
    """Whether ``OMEGA_PRIME_REDTEAM_KILL`` aborts everything right now."""
    source = env if env is not None else os.environ
    return (source.get(KILL_ENV_VAR) or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def current_sidecar_state() -> str | None:
    """Tracked harness state, or ``None`` when tracking is off."""
    return _sidecar_current


def sidecar_tracking_start(state: str = "IDLE") -> None:
    """Enable gateway-boundary transition logging from ``state``."""
    global _sidecar_current
    _sidecar_current = state


def sidecar_tracking_stop() -> None:
    """Disable gateway-boundary transition logging."""
    global _sidecar_current
    _sidecar_current = None


def _sidecar_note(to_state: str, cause: str) -> None:
    """Best-effort transition from the tracked state. Never raises."""
    global _sidecar_current
    if _sidecar_current is None:
        return
    try:
        from sidecar.state import transition
    except ImportError:
        return
    try:
        transition(_sidecar_current, to_state, cause)
    except Exception:
        return
    _sidecar_current = to_state


def _error(code: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"error": f"{code}: {reason}", **extra}


def _is_loopback(host: str) -> bool:
    """Loopback is exactly ``127.0.0.1`` or ``localhost`` (fixed contract)."""
    normalized = host.lower()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    return normalized in ("127.0.0.1", "localhost")


def resolve_bearer_token(
    environ: Mapping[str, str],
    read_text: Callable[[str], str],
    extra_file: str = "",
) -> str:
    """Bearer token: ``T3MP3ST_BEARER_TOKEN`` env wins over any token file.

    The file comes from ``T3MP3ST_BEARER_TOKEN_FILE`` or the config's
    ``bearer_token_file``. Mirrors the upstream contract (``net/remote-access.ts``):
    a missing or unreadable file degrades to no token — it never throws.
    """
    from_env = (environ.get("T3MP3ST_BEARER_TOKEN") or "").strip()
    if from_env:
        return from_env
    path = (environ.get("T3MP3ST_BEARER_TOKEN_FILE") or "").strip() or (
        extra_file or ""
    ).strip()
    if not path:
        return ""
    try:
        return (read_text(path) or "").strip()
    except (OSError, ValueError):
        return ""


def _package_seat_policy() -> Any:
    """Seat policy beside this package, for callers that have no runtime policy."""
    from pathlib import Path

    from omega_prime.policy.policy import SeatPolicy

    path = (
        Path(__file__).resolve().parents[1]
        / "contracts"
        / "policies"
        / "omega-prime.json"
    )
    try:
        return SeatPolicy.load(path)
    except (OSError, ValueError):
        return None


def _saved_gateway_policy(
    name: str, url: str, policy: Any = None
) -> dict[str, Any] | None:
    """Refuse a saved gateway whose host the active seat policy forbids.

    ``policy`` is the seat ``load_runtime`` loaded from ``--root``. Callers
    without one fall back to the policy file beside this package. The
    constructor default is not checked here. That URL is operator
    configuration, not a chat-supplied binding.
    """
    from urllib.parse import urlsplit

    active = policy if policy is not None else _package_seat_policy()
    if active is None:
        return None
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    if host and active.allows_host(host):
        return None
    shown = host or "that host"
    return _error(
        "policy",
        f"The seat policy does not allow {shown}.",
        ok=False,
        gateway=name,
    )


# Words the mission-status document uses for a mission this client may still
# have to stop, and words that mean that mission is already over. Pause and
# park keep the pin: the mission still lives on the gateway that started it.
_MISSION_RUNNING = frozenset(
    {
        "active",
        "running",
        "started",
        "paused",
        "parked",
        "resumed",
        "in_progress",
        "in-progress",
    }
)
_MISSION_ENDED = frozenset(
    {
        "idle",
        "stopped",
        "none",
        "inactive",
        "ended",
        "complete",
        "completed",
        "absent",
    }
)


def _status_says_ended(status: int, data: Any) -> bool:
    """True only when a mission-status body says the active mission is over.

    Anything else, including a transport-level failure the caller handles
    before this, keeps the pin. A stop must not move to a newly saved
    gateway while the original mission might still be running.
    """
    if not isinstance(status, int) or not 200 <= status < 300:
        return False
    if not isinstance(data, dict):
        return False
    if data.get("active") is True:
        return False
    if data.get("active") is False or data.get("idle") is True:
        return True
    for key in ("phase", "status", "state"):
        token = data.get(key)
        if not isinstance(token, str):
            continue
        word = token.strip().lower()
        if word in _MISSION_RUNNING:
            return False
        if word in _MISSION_ENDED:
            return True
    mission = data.get("mission")
    if isinstance(mission, str) and mission.strip():
        return False
    if isinstance(mission, dict):
        if mission.get("active") is True:
            return False
        if mission.get("active") is False:
            return True
        ident = mission.get("id") or mission.get("missionId")
        if isinstance(ident, str) and ident.strip():
            return False
    return mission is None and "mission" in data and not _mission_id(data)


def _mission_id(data: Mapping[str, Any]) -> str:
    for key in ("missionId", "mission_id"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _http_request(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, Any]:
    """One HTTP round trip over stdlib ``http.client``. Returns (status, body)."""
    parts = urllib.parse.urlsplit(base_url)
    connect = (
        http.client.HTTPSConnection
        if parts.scheme == "https"
        else http.client.HTTPConnection
    )
    body: bytes | None = None
    request_headers = dict(headers)
    if method == "POST":
        body = json.dumps(payload if payload is not None else {}).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    host = parts.hostname
    if host is None:
        raise ValueError(f"invalid_gateway_url: {base_url!r} is not a parseable URL")
    connection = connect(host, parts.port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        text = response.read().decode("utf-8", errors="replace")
        status = response.status
    finally:
        connection.close()
    try:
        return status, json.loads(text)
    except ValueError:
        return status, {"raw": text}


@dataclass
class RedteamContext:
    """Everything the red team tools need. Defaults to the loopback gateway."""

    gateway_url: str = DEFAULT_GATEWAY_URL
    bearer_token: str = ""
    timeout: float = 30.0
    request: Any = None  # injected transport; default is the stdlib impl
    roe: dict[str, Any] | None = None  # signed grant kept from the guard
    policy: Any = None  # seat from load_runtime; None uses the package file


class RedteamClient:
    """T3MP3ST loopback tools bound to one context."""

    def __init__(self, ctx: RedteamContext) -> None:
        self.ctx = ctx
        # Gateway a successful mission_start actually called. Later calls,
        # including mission_stop, stay there until that stop succeeds or the
        # gateway itself reports the mission has ended.
        self._mission_gateway: str | None = None

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.ctx.bearer_token:
            headers["Authorization"] = f"Bearer {self.ctx.bearer_token}"
        return headers

    def _refusal(self) -> dict[str, Any] | None:
        """Refusal row for composed tools, checked before any transport call."""
        return self._base_or_error()[1]

    def _base_or_error(self) -> tuple[str, dict[str, Any] | None]:
        """``(base, None)`` or ``("", refusal row)`` — the upstream gate."""
        pinned = self._pinned_gateway()
        if pinned:
            return pinned, None
        return self._configured_base()

    def _pinned_gateway(self) -> str | None:
        """Pinned mission gateway, or None once that mission has ended.

        A saved binding that still names this gateway is not asked again.
        When the operator points the binding at another gateway, this asks
        the pinned one ``GET /api/mission/status`` (the documented status
        read, the same document a direct ``POST /api/mission/stop`` updates).
        An explicit idle or stopped status drops the pin so a later call
        follows the new binding. ``mission_stop`` does not use that drop:
        it stays on this gateway, and a status that already says the
        mission ended is answered locally instead of stopping the new one.
        A transport error or an unclear body keeps the pin. The kill switch
        skips this read.
        """
        pinned = self._mission_gateway
        if not pinned or _kill_switch_set():
            return pinned
        if not self._binding_moved(pinned):
            return pinned
        if self._mission_still_running(pinned):
            return pinned
        self._mission_gateway = None
        return None

    def _binding_moved(self, pinned: str) -> bool:
        from omega_prime.integrations.gateways import gateway_block, ready_gateway_url

        saved = ready_gateway_url("redteam")
        if gateway_block("redteam") is None and not saved:
            return False
        return saved.rstrip("/") != pinned.rstrip("/")

    def _mission_still_running(self, base: str) -> bool:
        request = self.ctx.request or _http_request
        try:
            status, data = request(
                base,
                "GET",
                "/api/mission/status",
                None,
                self._headers(),
                self.ctx.timeout,
            )
        except (OSError, http.client.HTTPException, TimeoutError, ValueError):
            return True
        return not _status_says_ended(status, data)

    def _configured_base(self) -> tuple[str, dict[str, Any] | None]:
        from omega_prime.integrations.gateways import gateway_block, ready_gateway_url

        blocked = gateway_block("redteam")
        if blocked is not None:
            return "", blocked
        saved = ready_gateway_url("redteam")
        if saved:
            refused = _saved_gateway_policy("redteam", saved, self.ctx.policy)
            if refused is not None:
                return "", refused
        base = (saved or self.ctx.gateway_url or "").strip().rstrip("/")
        try:
            parts = urllib.parse.urlsplit(base)
        except ValueError:
            parts = None
        if parts is None or not parts.scheme or not parts.hostname:
            return "", _error(
                "invalid_gateway_url",
                f"{base!r} is not a parseable URL",
                ok=False,
            )
        if not _is_loopback(parts.hostname) and not self.ctx.bearer_token:
            return "", _error(
                "non_loopback_refused",
                f"refusing non-loopback gateway URL {base!r}: the contract "
                "requires a bearer token (T3MP3ST_BEARER_TOKEN) off loopback",
                ok=False,
            )
        return base, None

    def _call(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        tool: str | None = None,
    ) -> dict[str, Any]:
        # Kill switch aborts in-flight runs at the gateway call boundary,
        # before any transport I/O. Transition logging is best-effort and
        # only active once the registry guard starts tracking.
        if _kill_switch_set():
            _sidecar_note("BLOCKED", "kill-switch-set")
            return _error(
                "killed",
                "redteam kill switch set (OMEGA_PRIME_REDTEAM_KILL): "
                "in-flight run aborted at the gateway call boundary",
                ok=False,
            )
        # A stop never follows a binding change onto another gateway.
        # If the pinned gateway already reports the mission ended, answer
        # here and leave the new gateway's mission alone.
        if path == "/api/mission/stop" and self._mission_gateway:
            pinned = self._mission_gateway
            if self._binding_moved(pinned) and not self._mission_still_running(pinned):
                grant_refusal = self._grant_or_error(tool)
                if grant_refusal is not None:
                    return grant_refusal
                self._mission_gateway = None
                return {
                    "ok": True,
                    "status": 200,
                    "data": {"active": False, "mission": None},
                }
            base, refusal = pinned, None
        else:
            base, refusal = self._base_or_error()
        if refusal is not None:
            return refusal
        grant_refusal = self._grant_or_error(tool)
        if grant_refusal is not None:
            return grant_refusal
        request = self.ctx.request or _http_request
        if _sidecar_current == "IDLE":
            _sidecar_note("RUNNING", "run-started")
        try:
            status, data = request(
                base, method, path, payload, self._headers(), self.ctx.timeout
            )
        except (OSError, http.client.HTTPException, TimeoutError) as exc:
            _sidecar_note("ERROR", "gateway-unreachable")
            return _error(
                "gateway_unreachable",
                f"t3mp3st backend unreachable at {base} — start it with "
                f"npm run server ({type(exc).__name__}: {exc})",
                ok=False,
            )
        if _sidecar_current == "RUNNING":
            _sidecar_note("IDLE", "run-completed")
        result = {"ok": bool(200 <= status < 300), "status": status, "data": data}
        if result["ok"] and path == "/api/mission/start":
            self._mission_gateway = base
        elif result["ok"] and path == "/api/mission/stop":
            self._mission_gateway = None
        return result

    def _grant_or_error(self, tool: str | None) -> dict[str, Any] | None:
        """Signed-grant enforcement at call time: window, then allowlist.

        No grant (direct client use, or the guard resolved none) keeps
        historical behavior. The kill switch and the gateway refusal run
        first — this check never precedes them.
        """
        from omega_prime.redteam.roe import grant_expired, grant_tool_allowlist

        grant = self.ctx.roe
        if grant is None:
            return None
        expired = grant_expired(grant)
        if expired is None:
            return _error(
                "roe-invalid",
                "BLOCKED: signed RoE grant has no parseable "
                "effective_until — re-sign to renew",
                ok=False,
            )
        if expired:
            return _error(
                "roe-expired",
                "BLOCKED: signed RoE grant expired at "
                f"{grant.get('effective_until')!r} — re-sign to renew",
                ok=False,
            )
        if tool is not None:
            allowed = grant_tool_allowlist(grant)
            if allowed is not None and tool not in allowed:
                return _error(
                    "roe-tool-denied",
                    f"BLOCKED: tool {tool!r} is outside the signed RoE "
                    f"grant (allowed: {sorted(allowed)})",
                    ok=False,
                )
        return None

    def _get(self, path: str, tool: str | None = None) -> dict[str, Any]:
        return self._call("GET", path, tool=tool)

    def _post(
        self, path: str, payload: dict[str, Any], tool: str | None = None
    ) -> dict[str, Any]:
        return self._call("POST", path, payload, tool=tool)

    def status(self) -> dict[str, Any]:
        """Mission + provider status; two GETs composed like the upstream case."""
        refusal = self._refusal()
        if refusal is not None:
            return refusal
        return {
            "tool": "tempest_status",
            "mission": self._get("/api/mission/status", tool="tempest_status"),
            "llm": self._get("/api/llm/status", tool="tempest_status"),
        }

    def mission_start(
        self,
        name: Any = None,
        targets: Any = None,
        operators: Any = None,
    ) -> dict[str, Any]:
        """Start a mission; absent args are dropped like JSON.stringify does."""
        payload = {"name": name, "targets": targets, "operators": operators}
        return self._post(
            "/api/mission/start",
            {key: value for key, value in payload.items() if value is not None},
            tool="tempest_mission_start",
        )

    def mission_stop(self) -> dict[str, Any]:
        """Stop the active mission."""
        return self._post("/api/mission/stop", {}, tool="tempest_mission_stop")

    def report(self, mission_id: Any = None) -> dict[str, Any]:
        """Engagement report for one mission, or the active one."""
        if isinstance(mission_id, str) and mission_id:
            return self._get(
                f"/api/mission/{urllib.parse.quote(mission_id, safe='')}/report",
                tool="tempest_report",
            )
        return self._get("/api/mission/report", tool="tempest_report")

    def operators_list(self) -> dict[str, Any]:
        """Backend operators and their states."""
        return self._get("/api/operators/list", tool="tempest_operators_list")

    def operator_task(
        self,
        operator_id: Any = None,
        task_name: Any = None,
        task_description: Any = None,
    ) -> dict[str, Any]:
        """Dispatch one task to one operator seat."""
        if not isinstance(operator_id, str) or not operator_id:
            return {"ok": False, "error": "operatorId required"}
        return self._post(
            f"/api/operators/{urllib.parse.quote(operator_id, safe='')}/task",
            {"taskName": task_name, "taskDescription": task_description},
            tool="tempest_operator_task",
        )

    def findings(self, mission_id: Any = None) -> dict[str, Any]:
        """Ledger findings, optionally filtered to one mission."""
        if isinstance(mission_id, str) and mission_id:
            return self._get(
                f"/api/findings?missionId={urllib.parse.quote(mission_id, safe='')}",
                tool="tempest_findings",
            )
        return self._get("/api/findings", tool="tempest_findings")

    def providers(self) -> dict[str, Any]:
        """Provider + subscription-session state; two GETs composed."""
        refusal = self._refusal()
        if refusal is not None:
            return refusal
        return {
            "tool": "tempest_providers",
            "llm": self._get("/api/llm/status", tool="tempest_providers"),
            "oauth": self._get("/api/oauth/status", tool="tempest_providers"),
        }

    def purple_synthesize(
        self, findings: Any = None, detections: Any = None
    ) -> dict[str, Any]:
        """Purple posture synthesis from red findings + blue detection gaps."""
        return self._post(
            "/api/teams/purple/synthesize",
            {
                "findings": findings if findings is not None else [],
                "detections": detections if detections is not None else [],
            },
            tool="tempest_purple_synthesize",
        )

    def blue_gaps(self) -> dict[str, Any]:
        """Blue detection-coverage gaps for the whole findings ledger."""
        return self._get("/api/teams/blue/gaps", tool="tempest_blue_gaps")

    def black_cover(self) -> dict[str, Any]:
        """Black opsec cover review of the active red plan."""
        return self._get("/api/teams/black/cover", tool="tempest_black_cover")

    def white_roe(self) -> dict[str, Any]:
        """White ROE adjudication state on record."""
        return self._get("/api/teams/white/roe", tool="tempest_white_roe")

    def hats_list(self) -> dict[str, Any]:
        """Specialist hats and parent teams, projected live from tool grants."""
        refusal = self._refusal()
        if refusal is not None:
            return refusal
        grants = self._get("/api/teams/tool-grants", tool="tempest_hats_list")
        prompts = self._get("/api/operators/prompts", tool="tempest_hats_list")
        if not (grants.get("ok") and prompts.get("ok")):
            return {
                "tool": "tempest_hats_list",
                "ok": False,
                "grants": grants,
                "prompts": prompts,
            }
        grants_data = grants.get("data")
        grants_data = grants_data if isinstance(grants_data, dict) else {}
        record = grants_data.get("grants")
        record = record if isinstance(record, dict) else {}
        prompts_data = prompts.get("data")
        prompts_data = prompts_data if isinstance(prompts_data, dict) else {}
        operators = prompts_data.get("operators")
        operators = operators if isinstance(operators, list) else []
        prompted = {
            row.get("archetype")
            for row in operators
            if isinstance(row, dict) and isinstance(row.get("archetype"), str)
        }
        # No hardcoded grant tables: the hat rows and the parent-team rows are
        # both projected live from the tool-grants response. Only the three
        # parent-team ids separate the two groupings.
        parent_teams = ("black", "blue", "white")
        teams = [
            {"team": team, "grants": record[team]}
            for team in parent_teams
            if isinstance(record.get(team), list)
        ]
        hats = [
            {"hat": hat, "grants": record[hat], "hasPrompt": hat in prompted}
            for hat in record
            if hat not in parent_teams
        ]
        return {"tool": "tempest_hats_list", "ok": True, "teams": teams, "hats": hats}

    def hat_dispatch(
        self, hat: Any = None, task: Any = None, mission_id: Any = None
    ) -> dict[str, Any]:
        """Spawn one specialist hat, then task the spawned operator id."""
        if not isinstance(hat, str) or not hat:
            return {"ok": False, "error": "hat required"}
        if not isinstance(task, str) or not task:
            return {"ok": False, "error": "task required"}
        spawned = self._post(
            "/api/operators/spawn", {"archetype": hat}, tool="tempest_hat_dispatch"
        )
        if not spawned.get("ok"):
            detail = spawned.get("error") or f"HTTP {spawned.get('status')}"
            return _error(
                "spawn_failed",
                f"spawning hat {hat!r} failed ({detail}); no task was sent",
                tool="tempest_hat_dispatch",
                ok=False,
                stage="spawn",
                hat=hat,
                result=spawned,
            )
        data = spawned.get("data")
        data = data if isinstance(data, dict) else {}
        operator = data.get("operator")
        operator = operator if isinstance(operator, dict) else {}
        operator_id = operator.get("id") or data.get("id") or data.get("operatorId")
        if not isinstance(operator_id, str) or not operator_id:
            return {
                "tool": "tempest_hat_dispatch",
                "ok": False,
                "stage": "spawn",
                "error": "spawn answered without an operator id",
                "result": spawned,
            }
        quoted = (
            f"{task} (mission {mission_id})"
            if isinstance(mission_id, str) and mission_id
            else task
        )
        tasked = self._post(
            f"/api/operators/{urllib.parse.quote(operator_id, safe='')}/task",
            {"taskName": task[:120], "taskDescription": quoted},
            tool="tempest_hat_dispatch",
        )
        if not tasked.get("ok"):
            detail = tasked.get("error") or f"HTTP {tasked.get('status')}"
            return _error(
                "task_failed",
                f"spawned operator {operator_id} but the task request failed "
                f"({detail}); the operator is idle and may need cleanup",
                tool="tempest_hat_dispatch",
                ok=False,
                stage="task",
                hat=hat,
                operatorId=operator_id,
                result=tasked,
            )
        return {
            "tool": "tempest_hat_dispatch",
            "ok": True,
            "hat": hat,
            "operatorId": operator_id,
            "result": tasked,
        }


def register_redteam_tools(
    registry: ToolRegistry,
    client: RedteamClient,
    allowed: Collection[str] | None = None,
) -> list[str]:
    """Register the red team tools. Start/stop/task/dispatch need approval.

    ``allowed`` is the signed grant's tool allowlist (``None`` means the
    full fourteen): a grant limited to a subset registers only that subset,
    so a limited grant never enables tools outside the signed scope.
    """

    def _wrap(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, TypeError) as exc:
            return {"error": str(exc)}

    handlers: dict[str, Callable[..., Any]] = {
        "tempest_status": lambda: _wrap(client.status),
        "tempest_mission_start": lambda name=None, targets=None, operators=None: _wrap(
            client.mission_start, name=name, targets=targets, operators=operators
        ),
        "tempest_mission_stop": lambda: _wrap(client.mission_stop),
        "tempest_report": lambda missionId=None: _wrap(client.report, missionId),
        "tempest_operators_list": lambda: _wrap(client.operators_list),
        "tempest_operator_task": lambda operatorId=None, taskName=None, taskDescription=None: (
            _wrap(client.operator_task, operatorId, taskName, taskDescription)
        ),
        "tempest_findings": lambda missionId=None: _wrap(client.findings, missionId),
        "tempest_providers": lambda: _wrap(client.providers),
        "tempest_purple_synthesize": lambda findings=None, detections=None: _wrap(
            client.purple_synthesize, findings, detections
        ),
        "tempest_blue_gaps": lambda: _wrap(client.blue_gaps),
        "tempest_black_cover": lambda: _wrap(client.black_cover),
        "tempest_white_roe": lambda: _wrap(client.white_roe),
        "tempest_hats_list": lambda: _wrap(client.hats_list),
        "tempest_hat_dispatch": lambda hat=None, task=None, missionId=None: _wrap(
            client.hat_dispatch, hat, task, missionId
        ),
    }
    if set(handlers) != set(REDTEAM_TOOL_NAMES):
        raise RuntimeError("Red team tool handlers drifted from REDTEAM_TOOL_NAMES")
    approvals = {
        "tempest_mission_start",
        "tempest_mission_stop",
        "tempest_operator_task",
        "tempest_hat_dispatch",
    }
    for name in REDTEAM_TOOL_NAMES:
        if allowed is not None and name not in allowed:
            continue
        description, parameters = _SCHEMAS[name]
        registry.register(
            name,
            description,
            parameters,
            handlers[name],
            requires_approval=(name in approvals),
        )
    return [name for name in REDTEAM_TOOL_NAMES if allowed is None or name in allowed]


def _object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _string(description: str) -> dict:
    return {"type": "string", "description": description}


_SCHEMAS: dict[str, tuple[str, dict]] = {
    "tempest_status": (
        "Mission + provider status from the loopback gateway (GET /api/mission/status + /api/llm/status). Read-only.",
        _object({}, []),
    ),
    "tempest_mission_start": (
        "Start a mission on the loopback gateway (POST /api/mission/start). Requires approval.",
        _object(
            {
                "name": _string("Mission name."),
                "targets": {
                    "type": "array",
                    "description": 'Targets, e.g. [{ "host": "example.com" }].',
                },
                "operators": {
                    "type": "array",
                    "description": 'Operator archetypes, e.g. ["recon", "scanner"].',
                },
            },
            [],
        ),
    ),
    "tempest_mission_stop": (
        "Stop the active mission on the loopback gateway (POST /api/mission/stop). Requires approval.",
        _object({}, []),
    ),
    "tempest_report": (
        "Engagement report for a mission (GET /api/mission/report). Read-only.",
        _object(
            {"missionId": _string("Mission id; omit for the active mission.")},
            [],
        ),
    ),
    "tempest_operators_list": (
        "List backend operators and their states (GET /api/operators/list). Read-only.",
        _object({}, []),
    ),
    "tempest_operator_task": (
        "Dispatch a task to one backend operator (POST /api/operators/:id/task). Requires approval.",
        _object(
            {
                "operatorId": _string("Operator id from tempest_operators_list."),
                "taskName": _string("Short task name."),
                "taskDescription": _string("What the operator should do."),
            },
            ["operatorId"],
        ),
    ),
    "tempest_findings": (
        "Ledger findings, optionally for one mission (GET /api/findings). Read-only.",
        _object(
            {"missionId": _string("Mission id filter; omit for all findings.")},
            [],
        ),
    ),
    "tempest_providers": (
        "Provider + subscription-session state (GET /api/llm/status + /api/oauth/status). Read-only.",
        _object({}, []),
    ),
    "tempest_purple_synthesize": (
        "Purple posture synthesis from red findings + blue detection gaps (POST /api/teams/purple/synthesize). Read-only.",
        _object(
            {
                "findings": {
                    "type": "array",
                    "description": 'Findings, e.g. [{ "id": "...", "title": "...", "severity": "high" }].',
                },
                "detections": {
                    "type": "array",
                    "description": "Detection gaps paired with the findings.",
                },
            },
            [],
        ),
    ),
    "tempest_blue_gaps": (
        "Blue detection-coverage gaps for the whole findings ledger (GET /api/teams/blue/gaps). Read-only.",
        _object({}, []),
    ),
    "tempest_black_cover": (
        "Black opsec cover review of the active red plan (GET /api/teams/black/cover). Read-only.",
        _object({}, []),
    ),
    "tempest_white_roe": (
        "White ROE adjudication state on record (GET /api/teams/white/roe). Read-only.",
        _object({}, []),
    ),
    "tempest_hats_list": (
        "Specialist hats with parent teams and live tool grants (GET /api/teams/tool-grants + /api/operators/prompts). Read-only.",
        _object({}, []),
    ),
    "tempest_hat_dispatch": (
        "Spawn one specialist hat and dispatch a task to it (POST /api/operators/spawn {archetype} then POST /api/operators/:id/task). Requires approval.",
        _object(
            {
                "hat": _string("Hat archetype to spawn; the backend validates it."),
                "task": _string("What the hat should do."),
                "missionId": _string(
                    "Mission id quoted on follow-ups; omit when the backend "
                    "holds the active mission."
                ),
            },
            ["hat", "task"],
        ),
    ),
}


__all__ = [
    "DEFAULT_GATEWAY_URL",
    "KILL_ENV_VAR",
    "REDTEAM_TOOL_NAMES",
    "RedteamClient",
    "RedteamContext",
    "current_sidecar_state",
    "register_redteam_tools",
    "resolve_bearer_token",
    "sidecar_tracking_start",
    "sidecar_tracking_stop",
]
