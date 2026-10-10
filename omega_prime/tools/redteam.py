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
import urllib.parse
from collections.abc import Callable, Mapping
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
    "tempest_hats_list",
    "tempest_hat_dispatch",
)

DEFAULT_GATEWAY_URL = "http://127.0.0.1:3333"


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


class RedteamClient:
    """T3MP3ST loopback tools bound to one context."""

    def __init__(self, ctx: RedteamContext) -> None:
        self.ctx = ctx

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
        base = (self.ctx.gateway_url or "").strip().rstrip("/")
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
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        base, refusal = self._base_or_error()
        if refusal is not None:
            return refusal
        request = self.ctx.request or _http_request
        try:
            status, data = request(
                base, method, path, payload, self._headers(), self.ctx.timeout
            )
        except (OSError, http.client.HTTPException, TimeoutError) as exc:
            return _error(
                "gateway_unreachable",
                f"t3mp3st backend unreachable at {base} — start it with "
                f"npm run server ({type(exc).__name__}: {exc})",
                ok=False,
            )
        return {"ok": bool(200 <= status < 300), "status": status, "data": data}

    def _get(self, path: str) -> dict[str, Any]:
        return self._call("GET", path)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", path, payload)

    def status(self) -> dict[str, Any]:
        """Mission + provider status; two GETs composed like the upstream case."""
        refusal = self._refusal()
        if refusal is not None:
            return refusal
        return {
            "tool": "tempest_status",
            "mission": self._get("/api/mission/status"),
            "llm": self._get("/api/llm/status"),
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
        )

    def mission_stop(self) -> dict[str, Any]:
        """Stop the active mission."""
        return self._post("/api/mission/stop", {})

    def report(self, mission_id: Any = None) -> dict[str, Any]:
        """Engagement report for one mission, or the active one."""
        if isinstance(mission_id, str) and mission_id:
            return self._get(
                f"/api/mission/{urllib.parse.quote(mission_id, safe='')}/report"
            )
        return self._get("/api/mission/report")

    def operators_list(self) -> dict[str, Any]:
        """Backend operators and their states."""
        return self._get("/api/operators/list")

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
        )

    def findings(self, mission_id: Any = None) -> dict[str, Any]:
        """Ledger findings, optionally filtered to one mission."""
        if isinstance(mission_id, str) and mission_id:
            return self._get(
                f"/api/findings?missionId={urllib.parse.quote(mission_id, safe='')}"
            )
        return self._get("/api/findings")

    def providers(self) -> dict[str, Any]:
        """Provider + subscription-session state; two GETs composed."""
        refusal = self._refusal()
        if refusal is not None:
            return refusal
        return {
            "tool": "tempest_providers",
            "llm": self._get("/api/llm/status"),
            "oauth": self._get("/api/oauth/status"),
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
        )

    def hats_list(self) -> dict[str, Any]:
        """Specialist hats and parent teams, projected live from tool grants."""
        refusal = self._refusal()
        if refusal is not None:
            return refusal
        grants = self._get("/api/teams/tool-grants")
        prompts = self._get("/api/operators/prompts")
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
        spawned = self._post("/api/operators/spawn", {"archetype": hat})
        if not spawned.get("ok"):
            return {
                "tool": "tempest_hat_dispatch",
                "ok": False,
                "stage": "spawn",
                "result": spawned,
            }
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
        )
        return {
            "tool": "tempest_hat_dispatch",
            "ok": True,
            "hat": hat,
            "operatorId": operator_id,
            "result": tasked,
        }


def register_redteam_tools(registry: ToolRegistry, client: RedteamClient) -> list[str]:
    """Register the 11 red team tools. Start/stop/task/dispatch need approval."""

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
        description, parameters = _SCHEMAS[name]
        registry.register(
            name,
            description,
            parameters,
            handlers[name],
            requires_approval=(name in approvals),
        )
    return list(REDTEAM_TOOL_NAMES)


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
    "REDTEAM_TOOL_NAMES",
    "RedteamClient",
    "RedteamContext",
    "register_redteam_tools",
    "resolve_bearer_token",
]
