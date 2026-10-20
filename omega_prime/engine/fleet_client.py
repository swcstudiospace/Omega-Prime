# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""A1b client for the coding-fleet coordinator.

``JobSpec`` is the body ``POST /jobs`` accepts. The field names are the
``JobSpec`` interface in coding-fleet ``src/shared/types.ts`` at commit
``2c6a99f`` (branch ``feat/fleet-v0.1``). Required fields are ``repo`` and
``prompt``. Everything else is optional and omitted when unset, so the
coordinator keeps its own defaults.

Routes used here, from that commit's coordinator and its ``bin/fleet.mjs``
client:

- ``POST /jobs`` — one spec, or the client sends one spec object
- ``GET /jobs`` and ``GET /jobs/:id``
- ``GET /jobs/:id/events?after=``
- ``GET /status``
- ``GET /hitl``
- ``POST /hitl/answer`` with ``{"job_id", "value"}``
- ``POST /jobs/:id/cancel``

The coordinator reads ``Authorization: Bearer`` (``src/shared/util.ts``
``bearer``). This client sends that header and no other credential header.
``FLEET_COORDINATOR_URL`` and ``FLEET_COORDINATOR_TOKEN`` are read by name.
A ``*_FILE`` sibling is used when the value itself is unset. Values are not
logged, stored on the spec, or copied into errors.
"""

from __future__ import annotations

import json
import os
import re
import stat
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlsplit, urlunsplit

from omega_prime.engine.protocol import redact

_REPO_OK = re.compile(r"^[^/\s]+/[^/\s]+$")
_BRANCH_OK = re.compile(r"^[A-Za-z0-9._/-]+$")
_JOB_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_PROTECTED_BRANCHES = frozenset({"main", "master"})
_AGENTS = frozenset({"omp", "claude", "codex"})
_APPROVAL_MODES = frozenset({"yolo", "write", "always-ask"})


class FleetError(RuntimeError):
    """A coordinator call did not run. ``code`` is safe to show."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class JobSpec:
    """One job, accepted by both lanes. Field names match the coordinator."""

    repo: str
    prompt: str
    base: str | None = None
    branch: str | None = None
    phase: str | None = None
    plan: str | None = None
    agent: str | None = None
    approval_mode: str | None = None
    push: bool | None = None
    pr: bool | None = None
    notify: bool | None = None
    max_minutes: int | None = None
    max_files: int | None = None
    max_attempts: int | None = None
    links: tuple[str, ...] = ()
    slash_command: str | None = None
    slash_args: str | None = None
    raw_prompt: bool | None = None
    hitl_test: bool | None = None
    events: bool | None = None
    model: str | None = None

    def to_json(self) -> dict[str, Any]:
        """JSON object for ``POST /jobs``. Unset fields are left out."""
        payload: dict[str, Any] = {"repo": self.repo, "prompt": self.prompt}
        optional: tuple[tuple[str, Any], ...] = (
            ("base", self.base),
            ("branch", self.branch),
            ("phase", self.phase),
            ("plan", self.plan),
            ("agent", self.agent),
            ("approval_mode", self.approval_mode),
            ("push", self.push),
            ("pr", self.pr),
            ("notify", self.notify),
            ("max_minutes", self.max_minutes),
            ("max_files", self.max_files),
            ("max_attempts", self.max_attempts),
            ("links", list(self.links) if self.links else None),
            ("slash_command", self.slash_command),
            ("slash_args", self.slash_args),
            ("raw_prompt", self.raw_prompt),
            ("hitl_test", self.hitl_test),
            ("events", self.events),
            ("model", self.model),
        )
        for key, value in optional:
            if value is not None:
                payload[key] = value
        return payload


def validate_spec(spec: JobSpec) -> None:
    """Reject a spec the coordinator would refuse. Raises ``FleetError``."""
    if not isinstance(spec.repo, str) or _REPO_OK.fullmatch(spec.repo) is None:
        raise FleetError("invalid_repo")
    if not isinstance(spec.prompt, str) or not spec.prompt.strip():
        raise FleetError("invalid_prompt")
    if spec.branch is not None and not _branch_ok(spec.branch):
        raise FleetError("invalid_branch")
    if spec.base is not None and (
        not isinstance(spec.base, str) or _BRANCH_OK.fullmatch(spec.base) is None
    ):
        raise FleetError("invalid_base")
    if spec.agent is not None and spec.agent not in _AGENTS:
        raise FleetError("invalid_agent")
    if spec.approval_mode is not None and spec.approval_mode not in _APPROVAL_MODES:
        raise FleetError("invalid_approval_mode")
    for name, value in (
        ("max_minutes", spec.max_minutes),
        ("max_files", spec.max_files),
        ("max_attempts", spec.max_attempts),
    ):
        if value is not None and not _positive_int(value):
            raise FleetError(f"invalid_{name}")
    if spec.model is not None and (
        not isinstance(spec.model, str)
        or "/" not in spec.model
        or not spec.model.strip()
    ):
        raise FleetError("invalid_model")
    for link in spec.links:
        if not isinstance(link, str) or not link.strip():
            raise FleetError("invalid_links")


def _branch_ok(branch: str) -> bool:
    if not isinstance(branch, str) or branch in _PROTECTED_BRANCHES:
        return False
    return _BRANCH_OK.fullmatch(branch) is not None


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


class HttpResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> HttpResponse:
        """Perform one HTTP call. Implementations must not log ``headers``."""


class UrllibTransport:
    """stdlib HTTP. Redirects and proxies are disabled so the bearer stays put."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> HttpResponse:
        request = urllib.request.Request(
            url,
            data=body,
            headers=dict(headers),
            method=method,
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _RefuseRedirect(),
        )
        try:
            with opener.open(request, timeout=self._timeout) as response:
                raw = response.read()
                status = int(getattr(response, "status", 0) or 0)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = int(exc.code)
            exc.close()
        except urllib.error.URLError:
            raise FleetError("coordinator_unreachable") from None
        except FleetError:
            raise
        except OSError:
            raise FleetError("coordinator_unreachable") from None
        return HttpResponse(status, raw)


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        raise FleetError("redirect_refused")


def read_named_secret(env: Mapping[str, str], name: str) -> str:
    """Return the named setting. The direct value wins over ``NAME_FILE``.

    The return value is the secret. Callers must not log it.
    """
    direct = env.get(name, "")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    path = env.get(f"{name}_FILE", "")
    if not isinstance(path, str) or not path.strip():
        return ""
    file_path = Path(path.strip())
    try:
        mode = file_path.stat().st_mode
    except OSError:
        raise FleetError("secret_unreadable") from None
    if stat.S_IMODE(mode) & 0o077:
        raise FleetError("secret_permissions")
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        raise FleetError("secret_unreadable") from None
    return text.strip()


class FleetClient:
    """HTTP client. A missing URL or token means the fleet lane is unset."""

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        transport: Transport | None = None,
    ) -> None:
        self._env = env if env is not None else os.environ
        self._transport = transport if transport is not None else UrllibTransport()
        self._secret_error: str | None = None

    def configured(self) -> bool:
        """True when both settings resolve. A bad file is not configured."""
        try:
            url = read_named_secret(self._env, "FLEET_COORDINATOR_URL")
            token = read_named_secret(self._env, "FLEET_COORDINATOR_TOKEN")
        except FleetError as exc:
            self._secret_error = exc.code
            return False
        self._secret_error = None
        return bool(url) and bool(token)

    def configuration_error(self) -> str:
        """``not_configured`` or the secret-file code. No values."""
        if self._secret_error:
            return self._secret_error
        if not self.configured():
            return "not_configured"
        return ""

    def submit(self, spec: JobSpec) -> dict[str, Any]:
        """``POST /jobs`` with one spec. Returns the first job view."""
        validate_spec(spec)
        payload = self._call("POST", "/jobs", spec.to_json())
        jobs = _job_views(payload)
        if not jobs or not isinstance(jobs[0].get("id"), str):
            raise FleetError("invalid_response")
        return jobs[0]

    def list_jobs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """``GET /jobs``."""
        if not _positive_int(limit):
            raise FleetError("invalid_limit")
        payload = self._call("GET", "/jobs", query={"limit": str(limit)})
        return _job_views(payload)

    def get_job(self, job_id: str) -> dict[str, Any]:
        """``GET /jobs/:id``."""
        payload = self._call("GET", f"/jobs/{_job_path(job_id)}")
        job = payload.get("job") if isinstance(payload.get("job"), dict) else payload
        if not isinstance(job, dict) or not isinstance(job.get("id"), str):
            raise FleetError("invalid_response")
        return job

    def events(
        self, job_id: str, *, after: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        """``GET /jobs/:id/events``. Rows carry ``seq``, ``ts``, ``kind``, ``data``."""
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise FleetError("invalid_after")
        if not _positive_int(limit):
            raise FleetError("invalid_limit")
        payload = self._call(
            "GET",
            f"/jobs/{_job_path(job_id)}/events",
            query={"after": str(after), "limit": str(limit)},
        )
        rows = payload.get("events")
        if not isinstance(rows, list):
            raise FleetError("invalid_response")
        return [item for item in rows if isinstance(item, dict)]

    def poll(self, job_id: str, *, after: int = 0) -> dict[str, Any]:
        """One job plus the events after ``after``."""
        return {"job": self.get_job(job_id), "events": self.events(job_id, after=after)}

    def coordinator_status(self) -> dict[str, Any]:
        """``GET /status``."""
        return self._call("GET", "/status")

    def hitl_pending(self) -> dict[str, Any]:
        """``GET /hitl``."""
        return self._call("GET", "/hitl")

    def answer_hitl(self, job_id: str, value: str) -> dict[str, Any]:
        """``POST /hitl/answer`` with ``job_id`` and ``value``."""
        _job_path(job_id)
        if not isinstance(value, str) or not value.strip():
            raise FleetError("invalid_hitl_value")
        return self._call(
            "POST",
            "/hitl/answer",
            {"job_id": job_id, "value": value},
        )

    def cancel(self, job_id: str) -> dict[str, Any]:
        """``POST /jobs/:id/cancel``."""
        payload = self._call("POST", f"/jobs/{_job_path(job_id)}/cancel", {})
        job = payload.get("job") if isinstance(payload.get("job"), dict) else payload
        if not isinstance(job, dict):
            raise FleetError("invalid_response")
        return job

    def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        query: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self.configured():
            raise FleetError(self.configuration_error() or "not_configured")
        url = _join(read_named_secret(self._env, "FLEET_COORDINATOR_URL"), path, query)
        token = read_named_secret(self._env, "FLEET_COORDINATOR_TOKEN")
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        response = self._transport.request(method, url, headers, data)
        if response.status < 200 or response.status >= 300:
            raise FleetError(f"http_{response.status}")
        try:
            parsed = json.loads(response.body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise FleetError("invalid_response") from None
        if not isinstance(parsed, dict):
            raise FleetError("invalid_response")
        cleaned = redact(parsed)
        if not isinstance(cleaned, dict):
            raise FleetError("invalid_response")
        return cleaned


def _job_path(job_id: str) -> str:
    if not isinstance(job_id, str) or _JOB_ID_OK.fullmatch(job_id) is None:
        raise FleetError("invalid_job_id")
    return quote(job_id, safe="")


def _job_views(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("jobs")
    if isinstance(rows, list):
        return [item for item in rows if isinstance(item, dict)]
    job = payload.get("job")
    if isinstance(job, dict):
        return [job]
    return []


def _join(base: str, path: str, query: Mapping[str, str] | None) -> str:
    parts = urlsplit(base.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise FleetError("invalid_coordinator_url")
    if parts.username or parts.password or "@" in parts.netloc:
        raise FleetError("invalid_coordinator_url")
    suffix = path if path.startswith("/") else f"/{path}"
    joined = parts.path.rstrip("/") + suffix
    query_text = ""
    if query:
        query_text = "&".join(
            f"{key}={quote(value, safe='')}" for key, value in query.items()
        )
    return urlunsplit((parts.scheme, parts.netloc, joined, query_text, ""))


__all__ = [
    "FleetClient",
    "FleetError",
    "HttpResponse",
    "JobSpec",
    "Transport",
    "UrllibTransport",
    "read_named_secret",
    "validate_spec",
]
