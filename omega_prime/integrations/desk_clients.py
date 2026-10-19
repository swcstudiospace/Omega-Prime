# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Urllib clients for the desk Railway, Greptile, Vercel, Play, and App Store seams.

Each client takes an injectable transport ``(request) -> {status, body}``.
The default transport uses ``urllib.request`` and never logs a token. Callers
leave the context field None when the token is missing; these classes are
built only for a present token.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

RAILWAY_URL = "https://backboard.railway.com/graphql/v2"
GREPTILE_URL = "https://api.greptile.com/v2"
VERCEL_URL = "https://api.vercel.com"
PLAY_URL = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"
ASC_URL = "https://api.appstoreconnect.apple.com"
_TIMEOUT = 30

Transport = Callable[[dict[str, Any]], dict[str, Any]]

# Names only. `variables` is a JSON scalar, so the document must not select
# a `value` field (and must not sub-select anything). Callers keep the keys.
_VARIABLE_NAMES_QUERY = """
query variableNames($projectId: String!, $environmentId: String!, $serviceId: String!) {
  variables(projectId: $projectId, environmentId: $environmentId, serviceId: $serviceId)
}
""".strip()

_PROJECTS_QUERY = """
query projects($after: String) {
  projects(first: 100, after: $after) {
    edges { node { id name } }
    pageInfo { hasNextPage endCursor }
  }
}
""".strip()

_PROJECT_QUERY = """
query project($id: String!) {
  project(id: $id) {
    id
    name
    services {
      edges {
        node {
          id
          name
          serviceInstances {
            edges {
              node {
                latestDeployment { id status createdAt }
              }
            }
          }
        }
      }
    }
    environments {
      edges { node { id name } }
    }
  }
}
""".strip()

_LOGS_QUERY = """
query deploymentLogs($deploymentId: String!, $limit: Int) {
  deploymentLogs(deploymentId: $deploymentId, limit: $limit) {
    message
    timestamp
    severity
  }
}
""".strip()

_DEPLOYMENT_QUERY = """
query deployment($id: String!) {
  deployment(id: $id) {
    id
    status
    projectId
    serviceId
    environmentId
    createdAt
  }
}
""".strip()

_REDEPLOY_MUTATION = """
mutation serviceInstanceRedeploy($serviceId: String!, $environmentId: String!) {
  serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
}
""".strip()


def urllib_transport(request: dict[str, Any]) -> dict[str, Any]:
    """POST/GET one request with urllib. Tokens stay in the header only."""
    headers = {
        str(key): str(value) for key, value in (request.get("headers") or {}).items()
    }
    url = str(request.get("url") or "")
    params = request.get("params")
    if isinstance(params, Mapping):
        query = _query(params)
        if query:
            url = f"{url}&{query}" if "?" in url else f"{url}?{query}"
    method = str(request.get("method") or "GET").upper()
    payload = request.get("body")
    data: bytes | None
    if payload is None:
        data = None
    elif isinstance(payload, bytes):
        data = payload
    elif isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = json.dumps(payload).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    outgoing = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(outgoing, timeout=_TIMEOUT) as response:
            status = int(getattr(response, "status", 200))
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        finally:
            exc.close()
    except Exception as exc:
        return {"status": 0, "body": {"reason": _redact(str(exc), headers)}}
    return {"status": status, "body": _parse_body(raw)}


def asc_signing_available() -> bool:
    """True when PyJWT and cryptography can sign an ES256 App Store JWT."""
    try:
        import jwt
        from cryptography.hazmat.primitives.asymmetric import ec
    except Exception:
        return False
    return callable(getattr(jwt, "encode", None)) and hasattr(ec, "SECP256R1")


def clients_from_env(env: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build desk clients for tokens present in ``env``. Missing tokens are None."""
    source: Mapping[str, Any] = env or {}
    railway_token = _env_token(source, "RAILWAY_TOKEN")
    greptile_token = _env_token(source, "GREPTILE_API_KEY")
    vercel_token = _env_token(source, "VERCEL_TOKEN")
    play_token = _env_token(source, "PLAY_CONSOLE_TOKEN")
    key_id = _env_token(source, "ASC_KEY_ID")
    issuer_id = _env_token(source, "ASC_ISSUER_ID")
    private_key = _env_token(source, "ASC_PRIVATE_KEY")
    asc = None
    if key_id and issuer_id and private_key and asc_signing_available():
        asc = AscClient(key_id, issuer_id, private_key)
    return {
        "railway": RailwayClient(railway_token) if railway_token else None,
        "projects": _railway_projects(source),
        "greptile": (
            GreptileClient(
                greptile_token,
                github_token=_env_token(source, "GREPTILE_GITHUB_TOKEN"),
            )
            if greptile_token
            else None
        ),
        "vercel": VercelClient(vercel_token) if vercel_token else None,
        "play": PlayClient(play_token) if play_token else None,
        "asc": asc,
    }


def guarded_browser_factory(transport: Any) -> Any:
    """A GuardedBrowserFactory, or None when import or construction fails."""
    try:
        from omega_prime.tools.browser_egress import GuardedBrowserFactory

        return GuardedBrowserFactory(transport, config=None)
    except Exception:
        return None


class RailwayClient:
    """Railway public GraphQL. Redeploy is the only mutation."""

    def __init__(self, token: str, *, transport: Transport | None = None) -> None:
        self._token = _require_token(token, "Railway")
        self._transport = transport or urllib_transport

    def __repr__(self) -> str:
        return "RailwayClient(configured=True)"

    def project_status(self, name: str) -> dict[str, Any]:
        found = self._lookup_project(name)
        if isinstance(found, dict):
            return found
        if not found:
            return {
                "error": "not_found",
                "reason": f"no Railway project named {name}",
            }
        return self._graphql(_PROJECT_QUERY, {"id": found})

    def logs(self, deployment_id: str, lines: int) -> dict[str, Any]:
        return self._graphql(
            _LOGS_QUERY,
            {"deploymentId": deployment_id, "limit": int(lines)},
        )

    def variable_names(
        self, project_id: str, environment_id: str, service_id: str
    ) -> dict[str, Any]:
        """Variable names for one service. Values are discarded and never logged."""
        result = self._graphql(
            _VARIABLE_NAMES_QUERY,
            {
                "projectId": project_id,
                "environmentId": environment_id,
                "serviceId": service_id,
            },
        )
        if result.get("error"):
            return {
                "error": result.get("error", "upstream_error"),
                "reason": result.get("reason", "variable lookup failed"),
            }
        return {"names": _variable_name_list(result.get("body"))}

    def redeploy(self, service_id: str, environment_id: str) -> dict[str, Any]:
        return self._graphql(
            _REDEPLOY_MUTATION,
            {"serviceId": service_id, "environmentId": environment_id},
        )

    def project_id(self, name: str) -> str | None:
        found = self._lookup_project(name)
        if isinstance(found, str):
            return found
        return None

    def _lookup_project(self, name: str) -> str | dict[str, Any] | None:
        if not isinstance(name, str) or name == "":
            return None
        after: str | None = None
        for _ in range(10):
            variables: dict[str, Any] = {}
            if after:
                variables["after"] = after
            result = self._graphql(_PROJECTS_QUERY, variables)
            if result.get("error"):
                return result
            connection = ((result.get("body") or {}).get("data") or {}).get(
                "projects"
            ) or {}
            for edge in connection.get("edges") or []:
                node = (edge or {}).get("node") or {}
                if node.get("name") == name or node.get("id") == name:
                    project_id = node.get("id")
                    if isinstance(project_id, str) and project_id:
                        return project_id
                    return None
            page = connection.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return None
            cursor = page.get("endCursor")
            if not isinstance(cursor, str) or cursor == "":
                return None
            after = cursor
        return None

    def deployment(self, deployment_id: str) -> dict[str, Any]:
        return self._graphql(_DEPLOYMENT_QUERY, {"id": deployment_id})

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        return _send(
            self._transport,
            method="POST",
            url=RAILWAY_URL,
            headers=_bearer(self._token),
            params=None,
            body={"query": query, "variables": variables},
            secrets=(self._token,),
            graphql=True,
        )


class GreptileClient:
    """Greptile REST v2. Review trigger, fetch, and comment list."""

    def __init__(
        self,
        token: str,
        *,
        github_token: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self._token = _require_token(token, "Greptile")
        self._github_token = (
            github_token.strip()
            if isinstance(github_token, str) and github_token.strip()
            else None
        )
        self._transport = transport or urllib_transport

    def __repr__(self) -> str:
        return "GreptileClient(configured=True)"

    def trigger(self, target: str, pr_number: int) -> dict[str, Any]:
        return self._call(
            "POST",
            "/reviews",
            body={
                "remote": "github",
                "repository": target,
                "prNumber": pr_number,
            },
        )

    def get(self, review_id: str) -> dict[str, Any]:
        return self._call("GET", f"/reviews/{_segment(review_id)}")

    def comments(self, target: str, pr_number: int) -> dict[str, Any]:
        return self._call(
            "GET",
            "/reviews/comments",
            params={"repository": target, "prNumber": pr_number},
        )

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
    ) -> dict[str, Any]:
        headers = _bearer(self._token)
        secrets = [self._token]
        if self._github_token:
            headers["X-Github-Token"] = self._github_token
            secrets.append(self._github_token)
        return _send(
            self._transport,
            method=method,
            url=GREPTILE_URL + path,
            headers=headers,
            params=params,
            body=body,
            secrets=tuple(secrets),
        )


class VercelClient:
    """Vercel REST. ``allowed`` is the token's project list, not a local file."""

    def __init__(self, token: str, *, transport: Transport | None = None) -> None:
        self._token = _require_token(token, "Vercel")
        self._transport = transport or urllib_transport

    def __repr__(self) -> str:
        return "VercelClient(configured=True)"

    def deployment(self, deployment_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v13/deployments/{_segment(deployment_id)}")

    def deployments(self, project: str, limit: int) -> dict[str, Any]:
        return self._call(
            "GET",
            "/v7/deployments",
            params={"projectId": project, "limit": limit},
        )

    def promote(self, project: str, deployment_id: str) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/v10/projects/{_segment(project)}/promote/{_segment(deployment_id)}",
        )

    def rollback(self, project: str, deployment_id: str) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/v1/projects/{_segment(project)}/rollback/{_segment(deployment_id)}",
        )

    def allowed(self, project: str) -> bool:
        """True when this token's project list contains ``project``.

        A transport or HTTP error is False. The tool then uses its existing
        not-allowed path.
        """
        if not isinstance(project, str) or project == "":
            return False
        until: str | None = None
        for _ in range(10):
            params: dict[str, Any] = {"limit": 100}
            if until is not None:
                params["until"] = until
            result = self._call("GET", "/v9/projects", params=params)
            if result.get("error"):
                return False
            body = result.get("body")
            if not isinstance(body, dict):
                return False
            for item in body.get("projects") or []:
                if not isinstance(item, dict):
                    continue
                if project in (item.get("name"), item.get("id")):
                    return True
            pagination = body.get("pagination") or {}
            nxt = pagination.get("next") if isinstance(pagination, dict) else None
            if nxt in (None, ""):
                return False
            until = str(nxt)
        return False

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
    ) -> dict[str, Any]:
        return _send(
            self._transport,
            method=method,
            url=VERCEL_URL + path,
            headers=_bearer(self._token),
            params=params,
            body=body,
            secrets=(self._token,),
        )


class PlayClient:
    """Android Publisher edits. Methods return ``{body}`` or ``{error, reason}``."""

    def __init__(self, token: str, *, transport: Transport | None = None) -> None:
        self._token = _require_token(token, "Play Console")
        self._transport = transport or urllib_transport

    def __repr__(self) -> str:
        return "PlayClient(configured=True)"

    def edit(self, package: str) -> dict[str, Any]:
        return self._call("POST", f"/{_segment(package)}/edits", body={})

    def track(self, package: str, edit_id: str, track: str) -> dict[str, Any]:
        return self._call(
            "GET",
            f"/{_segment(package)}/edits/{_segment(edit_id)}/tracks/{_segment(track)}",
        )

    def update_track(
        self, package: str, edit_id: str, track: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return self._call(
            "PUT",
            f"/{_segment(package)}/edits/{_segment(edit_id)}/tracks/{_segment(track)}",
            body=body,
        )

    def commit(self, package: str, edit_id: str) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/{_segment(package)}/edits/{_segment(edit_id)}:commit",
            body={},
        )

    def _call(self, method: str, path: str, *, body: Any = None) -> dict[str, Any]:
        return _send(
            self._transport,
            method=method,
            url=PLAY_URL + path,
            headers=_bearer(self._token),
            params=None,
            body=body,
            secrets=(self._token,),
        )


class AscClient:
    """App Store Connect REST. JWTs are signed; a missing signer is not faked."""

    def __init__(
        self,
        key_id: str,
        issuer_id: str,
        private_key: str,
        *,
        transport: Transport | None = None,
    ) -> None:
        if not all(
            isinstance(item, str) and item.strip()
            for item in (key_id, issuer_id, private_key)
        ):
            raise ValueError(
                "App Store Connect key id, issuer, and private key are required"
            )
        self._key_id = key_id.strip()
        self._issuer_id = issuer_id.strip()
        self._private_key = private_key.strip()
        self._transport = transport or urllib_transport

    def __repr__(self) -> str:
        return "AscClient(configured=True)"

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> dict[str, Any]:
        authorization = self._authorization()
        if authorization is None:
            return {
                "error": "not_configured",
                "reason": "App Store Connect key cannot be signed",
            }
        headers = {
            "Authorization": authorization,
            "Accept": "application/json",
        }
        if json is not None:
            headers["Content-Type"] = "application/json"
        return _send(
            self._transport,
            method=method,
            url=_asc_url(path),
            headers=headers,
            params=params,
            body=json,
            secrets=(self._private_key, authorization),
        )

    def _authorization(self) -> str | None:
        if not asc_signing_available():
            return None
        try:
            import jwt

            now = int(time.time())
            token = jwt.encode(
                {
                    "iss": self._issuer_id,
                    "iat": now,
                    "exp": now + 19 * 60,
                    "aud": "appstoreconnect-v1",
                },
                self._pem(),
                algorithm="ES256",
                headers={"kid": self._key_id, "typ": "JWT"},
            )
        except Exception:
            return None
        if isinstance(token, bytes):
            token = token.decode("ascii")
        if not isinstance(token, str) or token.count(".") != 2:
            return None
        return f"Bearer {token}"

    def _pem(self) -> str:
        text = self._private_key
        if "PRIVATE KEY" not in text:
            path = Path(text)
            if path.is_file():
                text = path.read_text(encoding="utf-8")
        if "\\n" in text and "PRIVATE KEY" in text:
            text = text.replace("\\n", "\n")
        return text


def _require_token(token: str, service: str) -> str:
    if not isinstance(token, str) or token.strip() == "":
        raise ValueError(f"{service} token must be a non-empty string")
    return token.strip()


def _env_token(env: Mapping[str, Any], name: str) -> str | None:
    """``NAME``, ``NAME_FILE``, or ``$OMEGA_PRIME_STATE_DIR/secrets/NAME``."""
    from omega_prime.integrations.binding import secret_value

    text = secret_value(env, name)
    return text or None


def _railway_projects(env: Mapping[str, Any]) -> list[str]:
    raw = env.get("OMEGA_PRIME_RAILWAY_PROJECTS")
    if not isinstance(raw, str) or raw.strip() == "":
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _bearer(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def _segment(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


def _asc_url(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    if path == "/v1" or path.startswith("/v1/"):
        return ASC_URL + path
    return ASC_URL + "/v1" + path


def _query(params: Mapping[str, Any]) -> str:
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        pairs.append((str(key), str(value)))
    return urllib.parse.urlencode(pairs)


def _parse_body(raw: bytes) -> Any:
    if not raw:
        return {}
    text = raw.decode("utf-8", errors="replace")
    if text.strip() == "":
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _send(
    transport: Transport,
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, Any] | None,
    body: Any,
    secrets: tuple[str, ...],
    graphql: bool = False,
) -> dict[str, Any]:
    request = {
        "method": method.upper(),
        "url": url,
        "headers": headers,
        "params": params,
        "body": body,
    }
    try:
        response = transport(request)
    except Exception as exc:
        return {
            "error": "upstream_error",
            "reason": _redact(str(exc), headers, *secrets),
        }
    if not isinstance(response, dict):
        return {"error": "upstream_error", "reason": "transport returned no object"}
    return _interpret(response, graphql=graphql, secrets=secrets, headers=headers)


def _interpret(
    response: dict[str, Any],
    *,
    graphql: bool,
    secrets: tuple[str, ...],
    headers: Mapping[str, str],
) -> dict[str, Any]:
    status = response.get("status")
    body = response.get("body")
    ok = (
        isinstance(status, int) and not isinstance(status, bool) and 200 <= status < 300
    )
    if not ok:
        return {
            "error": "upstream_error",
            "reason": _redact(_http_reason(body, status), headers, *secrets),
        }
    if isinstance(body, str):
        if body.strip() == "":
            body = {}
        else:
            return {"error": "upstream_error", "reason": "response was not JSON"}
    if graphql and isinstance(body, dict) and body.get("errors"):
        return {
            "error": "upstream_error",
            "reason": _redact(_graphql_reason(body), headers, *secrets),
        }
    return {"body": {} if body is None else body}


def _http_reason(body: Any, status: Any) -> str:
    if isinstance(body, dict):
        reason = body.get("reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()[:500]
        error = body.get("error")
        message = body.get("message")
        if isinstance(error, dict):
            nested = error.get("message") or error.get("code")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()[:500]
        if isinstance(message, str) and message.strip():
            return message.strip()[:500]
        if isinstance(error, str) and error.strip():
            return error.strip()[:500]
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            return _graphql_reason(body)
    if isinstance(body, str) and body.strip():
        return body.strip()[:500]
    return f"HTTP {status}"


def _graphql_reason(body: dict[str, Any]) -> str:
    messages: list[str] = []
    errors = body.get("errors")
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, dict):
                message = item.get("message") or item.get("title")
                messages.append(str(message) if message else "graphql error")
            else:
                messages.append("graphql error")
    if not messages:
        messages.append("graphql error")
    return "; ".join(messages)[:500]


def _variable_name_list(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    data = body.get("data")
    raw = data.get("variables") if isinstance(data, dict) else None
    if isinstance(raw, dict):
        return [str(name) for name in raw]
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


def _redact(text: str, headers: Mapping[str, str] | None, *secrets: str) -> str:
    redacted = text
    for header, value in (headers or {}).items():
        if not isinstance(value, str) or value == "":
            continue
        lowered = header.lower()
        if lowered in {"authorization", "x-github-token"}:
            redacted = redacted.replace(value, "[redacted]")
            if lowered == "authorization":
                prefix, _, token = value.partition(" ")
                if prefix.lower() == "bearer" and token:
                    redacted = redacted.replace(token, "[redacted]")
    for secret in secrets:
        if isinstance(secret, str) and secret:
            redacted = redacted.replace(secret, "[redacted]")
            if secret.lower().startswith("bearer "):
                redacted = redacted.replace(secret[7:], "[redacted]")
    return redacted
