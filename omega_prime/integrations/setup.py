# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Resumable installer prompts for Notion, Linear, and optional gateways.

Replies are short verbs on ``/omega-connectors``. A token in the reply is
refused and never stored.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from omega_prime.credentials.broker import CredentialBroker
from omega_prime.integrations.binding import (
    GATEWAY_NAMES,
    LINEAR_ENV,
    NOTION_ENV,
    binding_path,
    fresh_binding,
    load_binding,
    resolve_secret,
    save_binding,
)
from omega_prime.integrations.contract import CONTRACT_VERSION, schema_gaps
from omega_prime.integrations.gateways import probe_gateway, validate_gateway_url
from omega_prime.integrations.linear import LINEAR_API_URL, LinearClient, LinearError
from omega_prime.integrations.notion import NOTION_API, NotionClient, NotionError
from omega_prime.providers.base import ProviderError

_SECRET_RE = re.compile(
    r"(?i)(lin_api_|ntn_|secret_|ghp_|sk-|xox[baprs]-|"
    r"api[_-]?key\s*[:=]|token\s*[:=]|bearer\s+\S{8,})"
)
_PROBE_TEXT = {
    "auth": "rejected the credential",
    "timeout": "timed out",
    "tls": "failed the TLS handshake",
    "refused": "refused the connection",
}

Probe = Callable[[str], str]


def handle(
    args: str,
    env: Mapping[str, str],
    *,
    notion: Any = None,
    linear: Any = None,
    probe: Probe | None = None,
    policy: Any = None,
) -> dict[str, Any]:
    """Apply one setup reply and return a public status. Never includes a secret."""
    text = (args or "").strip()
    explicit = bool(str(env.get("OMEGA_PRIME_STATE_DIR", "") or "").strip())
    try:
        exists = binding_path(env).is_file()
    except ValueError:
        exists = False
    binding = load_binding(env) if explicit or exists else fresh_binding()
    if text and _SECRET_RE.search(text):
        return _pack(
            binding,
            ok=False,
            error="secret_refused",
            prompt=(
                "Do not paste a token into chat. Set NOTION_TOKEN or "
                "LINEAR_API_KEY on the host, or point NOTION_TOKEN_FILE or "
                "LINEAR_API_KEY_FILE at a mode 0600 file."
            ),
        )
    message = _apply(binding, text, env, notion, linear, probe, policy)
    message = _annotate_policy(message, binding, policy)
    if explicit or exists or text:
        try:
            save_binding(binding, env)
        except ValueError as exc:
            return _pack(binding, ok=False, error="binding_refused", prompt=str(exc))
    invalid = (
        binding["notion"]["state"] == "invalid"
        or binding["linear"]["state"] == "invalid"
    )
    return _pack(
        binding,
        ok=not invalid,
        error="setup_blocked" if invalid else "",
        prompt=message or _prompt(binding),
    )


def _apply(
    binding: dict[str, Any],
    text: str,
    env: Mapping[str, str],
    notion: Any,
    linear: Any,
    probe: Probe | None,
    policy: Any = None,
) -> str:
    if text in ("", "status"):
        return _prompt(binding)
    verb, _, rest = text.partition(" ")
    rest = rest.strip()
    if verb == "duplicated":
        return _duplicated(binding)
    if verb == "connected":
        return _connected(binding, env, notion, linear, policy)
    if verb == "notion-database":
        return _select_database(binding, rest, env, notion, policy)
    if verb == "linear-team":
        return _select_team(binding, rest, env, linear, policy)
    if verb == "linear-project":
        return _select_project(binding, rest)
    if verb == "skip-gateway":
        return _skip_gateways(binding)
    if verb == "gateway":
        return _configure_gateway(binding, rest, probe, env, policy)
    if verb == "task":
        return _task(binding, rest, env, notion, policy)
    if verb == "linear-issue":
        return _linear_issue(binding, rest, env, linear, policy)
    if verb == "linear-read":
        return _linear_read(binding, rest, env, linear, policy)
    if verb == "linear-update":
        return _linear_update(binding, rest, env, linear, policy)
    if verb in ("reset", "disconnect"):
        return _reset(binding, rest)
    return (
        "Reply with one of: duplicated, connected, notion-database <id>, "
        "linear-team <id>, linear-project <id>, skip-gateway, "
        "gateway <substrate|redteam|desk_bus> <url>, task list, "
        "task add <name>, linear-issue <title>, status, reset <notion|linear|gateway>."
    )


def _duplicated(binding: dict[str, Any]) -> str:
    dashboard = binding["notion"]["dashboard"]
    if dashboard != "template-not-duplicated":
        return (
            "The template step is already recorded. No second dashboard was created. "
            + _prompt(binding)
        )
    binding["notion"]["dashboard"] = "shared-but-unchecked"
    binding["notion"]["state"] = "awaiting-user"
    return _prompt(binding)


def _connected(
    binding: dict[str, Any],
    env: Mapping[str, str],
    notion: Any,
    linear: Any,
    policy: Any = None,
) -> str:
    step = _step(binding)
    if step == "template":
        return _prompt(binding)
    if step == "notion":
        message = _connect_notion(binding, env, notion, policy)
        if message:
            return message
        return _prompt(binding)
    if step in ("linear", "linear-team", "linear-project"):
        message = _connect_linear(binding, env, linear, policy)
        if message:
            return message
        return _prompt(binding)
    return _prompt(binding)


def _connect_notion(
    binding: dict[str, Any],
    env: Mapping[str, str],
    notion: Any,
    policy: Any = None,
) -> str | None:
    client, problem = _notion_client(env, notion, policy)
    if problem:
        binding["notion"]["state"] = "awaiting-user"
        return problem
    if client is None:
        binding["notion"]["state"] = "awaiting-user"
        return (
            "Set NOTION_TOKEN or NOTION_TOKEN_FILE on the host, then reply "
            "/omega-connectors connected. Do not paste the token into chat."
        )
    try:
        client.me()
        matches, gaps = client.matching_databases()
    except NotionError as exc:
        return _notion_failure(binding, exc)
    if len(matches) == 1:
        _bind_database(binding, matches[0]["id"])
        return None
    if len(matches) > 1:
        binding["notion"]["state"] = "awaiting-user"
        binding["notion"]["dashboard"] = "shared-but-unchecked"
        binding["notion"]["offered_database_ids"] = [row["id"] for row in matches]
        return (
            "More than one database matches the template. Reply "
            "/omega-connectors notion-database <id> with one of:\n" + _choices(matches)
        )
    if gaps:
        missing = next(iter(gaps.values()))
        binding["notion"]["state"] = "invalid"
        binding["notion"]["dashboard"] = "schema-mismatch"
        return (
            "The shared database is missing: "
            + ", ".join(missing)
            + ". Update the duplicate from docs/notion-task-dashboard.md, "
            "then reply /omega-connectors connected."
        )
    binding["notion"]["state"] = "invalid"
    binding["notion"]["dashboard"] = "shared-but-unchecked"
    return (
        "Notion authenticated but cannot see the Tasks database. Share the "
        "duplicated dashboard with the integration, then reply "
        "/omega-connectors connected."
    )


def _select_database(
    binding: dict[str, Any],
    database_id: str,
    env: Mapping[str, str],
    notion: Any,
    policy: Any = None,
) -> str:
    if _step(binding) == "template":
        return _prompt(binding)
    client, problem = _notion_client(env, notion, policy)
    if problem or client is None:
        return problem or "Set NOTION_TOKEN on the host first."
    offered = binding["notion"]["offered_database_ids"]
    chosen = database_id.replace("-", "")
    if offered and chosen not in offered:
        return "That id was not one of the databases just listed. " + _prompt(binding)
    try:
        payload = client.database(chosen)
    except NotionError as exc:
        return _notion_failure(binding, exc)
    gaps = schema_gaps(payload)
    if gaps:
        binding["notion"]["state"] = "invalid"
        binding["notion"]["dashboard"] = "schema-mismatch"
        return (
            "The shared database is missing: "
            + ", ".join(gaps)
            + ". Update the duplicate from docs/notion-task-dashboard.md."
        )
    _bind_database(binding, chosen)
    return _prompt(binding)


def _connect_linear(
    binding: dict[str, Any],
    env: Mapping[str, str],
    linear: Any,
    policy: Any = None,
) -> str | None:
    client, problem = _linear_client(env, linear, policy)
    if problem:
        binding["linear"]["state"] = "awaiting-user"
        return problem
    if client is None:
        binding["linear"]["state"] = "awaiting-user"
        return (
            "Set LINEAR_API_KEY or LINEAR_API_KEY_FILE on the host, then reply "
            "/omega-connectors connected. Do not paste the token into chat."
        )
    try:
        client.viewer()
        teams = client.teams()
    except LinearError as exc:
        return _linear_failure(binding, exc)
    if not teams:
        binding["linear"]["state"] = "invalid"
        return (
            "Linear returned no teams for this credential. Choose another "
            "workspace key, then reply /omega-connectors connected."
        )
    if len(teams) == 1:
        return _adopt_team(binding, client, teams[0])
    binding["linear"]["state"] = "awaiting-user"
    binding["linear"]["offered_team_ids"] = [row["id"] for row in teams]
    return (
        "This Linear account has more than one team. Reply "
        "/omega-connectors linear-team <id> with one of:\n" + _choices(teams)
    )


def _select_team(
    binding: dict[str, Any],
    team_id: str,
    env: Mapping[str, str],
    linear: Any,
    policy: Any = None,
) -> str:
    offered = binding["linear"]["offered_team_ids"]
    if team_id not in offered:
        return "That id was not one of the teams just listed. " + _prompt(binding)
    client, problem = _linear_client(env, linear, policy)
    if problem or client is None:
        return problem or "Set LINEAR_API_KEY on the host first."
    try:
        teams = client.teams()
    except LinearError as exc:
        return _linear_failure(binding, exc)
    chosen = next((row for row in teams if row["id"] == team_id), None)
    if chosen is None:
        binding["linear"]["state"] = "invalid"
        return "Linear can no longer see that team. Reply /omega-connectors connected."
    message = _adopt_team(binding, client, chosen)
    return message or _prompt(binding)


def _adopt_team(
    binding: dict[str, Any], client: Any, team: dict[str, str]
) -> str | None:
    binding["linear"]["team_id"] = team["id"]
    binding["linear"]["team_name"] = team["name"]
    binding["linear"]["project_id"] = ""
    binding["linear"]["project_name"] = ""
    binding["linear"]["offered_project_ids"] = []
    try:
        projects = client.projects(team["id"])
    except LinearError as exc:
        if binding["linear"]["state"] == "ready":
            binding["linear"]["state"] = "awaiting-user"
        return _linear_failure(binding, exc)
    if len(projects) <= 1:
        project = projects[0] if projects else {"id": "", "name": ""}
        _bind_linear(binding, project["id"], project["name"])
        return None
    binding["linear"]["state"] = "awaiting-user"
    binding["linear"]["offered_project_ids"] = [row["id"] for row in projects]
    return (
        "This team has more than one project. Reply "
        "/omega-connectors linear-project <id> with one of:\n" + _choices(projects)
    )


def _select_project(binding: dict[str, Any], project_id: str) -> str:
    offered = binding["linear"]["offered_project_ids"]
    if project_id not in offered:
        return "That id was not one of the projects just listed. " + _prompt(binding)
    _bind_linear(binding, project_id, "")
    return _prompt(binding)


def _skip_gateways(binding: dict[str, Any]) -> str:
    if _step(binding) != "gateway" and _step(binding) != "done":
        return _prompt(binding)
    for name in GATEWAY_NAMES:
        node = binding["gateways"][name]
        if node["state"] in ("unconfigured", "awaiting-user"):
            node["state"] = "skipped"
            node["url"] = ""
    return _prompt(binding)


def _configure_gateway(
    binding: dict[str, Any],
    rest: str,
    probe: Probe | None,
    env: Mapping[str, str],
    policy: Any = None,
) -> str:
    if _step(binding) not in ("gateway", "done"):
        return _prompt(binding)
    name, _, url = rest.partition(" ")
    if name not in GATEWAY_NAMES:
        return "Name a gateway: substrate, redteam, or desk_bus."
    url = url.strip()
    error = validate_gateway_url(url)
    if error:
        return error
    if _policy_blocks(policy, url):
        host = _hostname(url) or "that host"
        return (
            f"The seat policy does not allow {host}. The saved gateway was not changed."
        )
    try:
        result = probe_gateway(name, url, env) if probe is None else probe(url)
    except Exception:
        result = "refused"
    node = binding["gateways"][name]
    previous_state = node["state"]
    previous_url = node["url"]
    if result == "ok":
        node["url"] = url
        node["state"] = "ready"
        return _prompt(binding)
    kept = previous_state == "ready" and bool(previous_url)
    if result in ("timeout", "degraded"):
        if kept:
            detail = "timed out" if result == "timeout" else "returned a server error"
            return (
                f"{name} gateway {detail}. The previous URL is unchanged. "
                "Notion and Linear are unchanged."
            )
        node["url"] = url
        node["state"] = "degraded"
        if result == "degraded":
            return (
                f"{name} gateway returned a server error. The URL is kept "
                "for a later retry. Notion and Linear are unchanged."
            )
        return (
            f"{name} gateway timed out. The URL is kept for a later retry. "
            "Notion and Linear are unchanged."
        )
    if kept:
        detail = _PROBE_TEXT.get(result, "could not be reached")
        return f"{name} gateway {detail}. The previous URL is unchanged."
    node["url"] = url
    node["state"] = "invalid"
    detail = _PROBE_TEXT.get(result, "could not be reached")
    return f"{name} gateway {detail}."


def _task(
    binding: dict[str, Any],
    rest: str,
    env: Mapping[str, str],
    notion: Any,
    policy: Any = None,
) -> str:
    if binding["notion"]["state"] != "ready":
        return _prompt(binding)
    client, problem = _notion_client(env, notion, policy)
    if problem or client is None:
        return problem or "Set NOTION_TOKEN on the host first."
    database_id = binding["notion"]["database_id"]
    verb, _, remainder = rest.partition(" ")
    try:
        if verb == "list":
            rows = client.query_tasks(database_id)
            if not rows:
                return "The bound Tasks database has no pages."
            return "Tasks:\n" + _choices(rows)
        if verb == "add":
            name = remainder.strip()
            if not name:
                return "Reply /omega-connectors task add <name>."
            created = client.create_task(database_id, name)
            return f"Created task {created['id']} in the bound database."
        if verb == "status":
            page_id, _, status = remainder.partition(" ")
            if not page_id or not status.strip():
                return "Reply /omega-connectors task status <page-id> <status>."
            updated = client.update_task_status(
                database_id, page_id.strip(), status.strip()
            )
            return f"Updated task {updated['id']} in the bound database."
    except NotionError as exc:
        return _notion_failure(binding, exc) or str(exc)
    return "Reply task list, task add <name>, or task status <page-id> <status>."


def _linear_issue(
    binding: dict[str, Any],
    title: str,
    env: Mapping[str, str],
    linear: Any,
    policy: Any = None,
) -> str:
    if binding["linear"]["state"] != "ready":
        return _prompt(binding)
    if not title:
        return "Reply /omega-connectors linear-issue <title>."
    client, problem = _linear_client(env, linear, policy)
    if problem or client is None:
        return problem or "Set LINEAR_API_KEY on the host first."
    try:
        created = client.create_issue(
            binding["linear"]["team_id"],
            title,
            "Opened from Omega Prime installer setup.",
            0,
            binding["linear"]["project_id"],
        )
    except LinearError as exc:
        return _linear_failure(binding, exc) or str(exc)
    return f"Created Linear issue {created['id']} on the bound team."


def _linear_read(
    binding: dict[str, Any],
    issue_id: str,
    env: Mapping[str, str],
    linear: Any,
    policy: Any = None,
) -> str:
    if binding["linear"]["state"] != "ready":
        return _prompt(binding)
    client, problem = _linear_client(env, linear, policy)
    if problem or client is None:
        return problem or "Set LINEAR_API_KEY on the host first."
    try:
        row = client.issue(issue_id)
    except LinearError as exc:
        return _linear_failure(binding, exc) or str(exc)
    if row["team_id"] != binding["linear"]["team_id"]:
        return "That issue is not on the Linear team this install selected."
    return f"Linear issue {row['id']} is on the bound team."


def _linear_update(
    binding: dict[str, Any],
    rest: str,
    env: Mapping[str, str],
    linear: Any,
    policy: Any = None,
) -> str:
    if binding["linear"]["state"] != "ready":
        return _prompt(binding)
    issue_id, _, title = rest.partition(" ")
    if not issue_id or not title.strip():
        return "Reply /omega-connectors linear-update <id> <title>."
    client, problem = _linear_client(env, linear, policy)
    if problem or client is None:
        return problem or "Set LINEAR_API_KEY on the host first."
    try:
        updated = client.update_issue(
            issue_id.strip(), binding["linear"]["team_id"], title.strip()
        )
    except LinearError as exc:
        return _linear_failure(binding, exc) or str(exc)
    return f"Updated Linear issue {updated['id']} on the bound team."


def _reset(binding: dict[str, Any], target: str) -> str:
    fresh = fresh_binding()
    if target == "notion":
        binding["notion"] = fresh["notion"]
    elif target == "linear":
        binding["linear"] = fresh["linear"]
    elif target == "gateway":
        binding["gateways"] = fresh["gateways"]
    else:
        return "Reset one of: notion, linear, gateway."
    return _prompt(binding)


def _bind_database(binding: dict[str, Any], database_id: str) -> None:
    binding["notion"]["database_id"] = database_id
    binding["notion"]["state"] = "ready"
    binding["notion"]["dashboard"] = "ready"
    binding["notion"]["contract_version"] = CONTRACT_VERSION
    binding["notion"]["offered_database_ids"] = []


def _bind_linear(binding: dict[str, Any], project_id: str, project_name: str) -> None:
    binding["linear"]["project_id"] = project_id
    binding["linear"]["project_name"] = project_name
    binding["linear"]["state"] = "ready"
    binding["linear"]["offered_team_ids"] = []
    binding["linear"]["offered_project_ids"] = []


def _notion_client(
    env: Mapping[str, str], notion: Any, policy: Any = None
) -> tuple[Any, str | None]:
    if notion is not None:
        return notion, None
    token, error = _checked_secret(policy, env, NOTION_ENV, NOTION_API)
    if error:
        return None, error
    if not token:
        return None, None
    return NotionClient(token, policy=policy), None


def _linear_client(
    env: Mapping[str, str], linear: Any, policy: Any = None
) -> tuple[Any, str | None]:
    if linear is not None:
        return linear, None
    token, error = _checked_secret(policy, env, LINEAR_ENV, LINEAR_API_URL)
    if error:
        return None, error
    if not token:
        return None, None
    return LinearClient(token, policy=policy), None


def _checked_secret(
    policy: Any, env: Mapping[str, str], name: str, url: str
) -> tuple[str | None, str | None]:
    """Resolve a host secret only after the seat policy allows that host.

    No policy keeps the historical resolver, including ``*_FILE``. With a
    policy, the env-var form goes through ``CredentialBroker`` (the same
    host gate as every other provider). The file form is read only after
    that host check.
    """
    if policy is None:
        return resolve_secret(env, name)
    host = _hostname(url)
    if not host or not policy.allows_host(host):
        shown = host or "that host"
        return None, f"The seat policy does not allow {shown}."
    file_key = name + "_FILE"
    file_value = env.get(file_key, "")
    if isinstance(file_value, str) and file_value.strip():
        return resolve_secret(env, name)
    raw = env.get(name, "")
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    provider = SimpleNamespace(name=name.lower(), requires_key=True, env_vars=(name,))
    try:
        token = CredentialBroker(policy, env).key_for(provider, url)
    except ProviderError as exc:
        if "no credential for host" in str(exc):
            return None, f"The seat policy does not allow {host}."
        return None, None
    return token, None


def _policy_blocks(policy: Any, url: str) -> bool:
    if policy is None:
        return False
    host = _hostname(url)
    return not host or not policy.allows_host(host)


def _hostname(url: str) -> str:
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return ""
    return host or ""


def _annotate_policy(message: str, binding: dict[str, Any], policy: Any) -> str:
    if policy is None or not message:
        return message
    step = _step(binding)
    note = ""
    if step in ("template", "notion") and not policy.allows_host("api.notion.com"):
        note = "The seat policy does not allow api.notion.com."
    elif step in ("linear", "linear-team", "linear-project") and not policy.allows_host(
        "api.linear.app"
    ):
        note = "The seat policy does not allow api.linear.app."
    elif step == "gateway":
        note = (
            "A gateway URL is refused unless its host is listed in the "
            "seat policy network.hosts."
        )
    if not note or note in message:
        return message
    return message + "\n" + note


def _notion_failure(binding: dict[str, Any], exc: NotionError) -> str:
    if exc.code == "auth":
        binding["notion"]["state"] = "invalid"
    elif exc.code == "permission":
        binding["notion"]["state"] = "invalid"
        if binding["notion"]["dashboard"] == "ready":
            binding["notion"]["dashboard"] = "shared-but-unchecked"
    return str(exc)


def _linear_failure(binding: dict[str, Any], exc: LinearError) -> str:
    if exc.code == "auth":
        binding["linear"]["state"] = "invalid"
    return str(exc)


def _step(binding: dict[str, Any]) -> str:
    notion = binding["notion"]
    if notion["dashboard"] == "template-not-duplicated":
        return "template"
    if notion["state"] != "ready" or notion["dashboard"] != "ready":
        return "notion"
    linear = binding["linear"]
    if linear["state"] != "ready":
        if linear["offered_project_ids"] and linear["team_id"]:
            return "linear-project"
        if linear["offered_team_ids"] and not linear["team_id"]:
            return "linear-team"
        return "linear"
    if any(
        binding["gateways"][name]["state"] == "unconfigured" for name in GATEWAY_NAMES
    ):
        return "gateway"
    return "done"


def _overall(binding: dict[str, Any]) -> str:
    if _step(binding) not in ("gateway", "done"):
        return "not-ready"
    if binding["notion"]["state"] != "ready" or binding["linear"]["state"] != "ready":
        return "not-ready"
    states = [binding["gateways"][name]["state"] for name in GATEWAY_NAMES]
    if any(state == "degraded" for state in states):
        return "ready-with-gateway-degraded"
    if any(state == "unconfigured" for state in states):
        return "not-ready"
    if all(state == "skipped" for state in states):
        return "ready-with-gateway-skipped"
    if any(state == "invalid" for state in states):
        return "ready-with-gateway-degraded"
    return "ready"


def _prompt(binding: dict[str, Any]) -> str:
    step = _step(binding)
    if step == "template":
        return (
            "1. Open docs/notion-task-dashboard.md. That file is the Task "
            f"Management Dashboard template (contract version {CONTRACT_VERSION}). "
            "There is no public Notion gallery listing.\n"
            "2. In your own Notion, create a database named Tasks with the "
            "properties that page lists.\n"
            "3. Create a Notion integration with read content, update content, "
            "and insert content, and share that database with it.\n"
            "4. Put the token in the host environment as NOTION_TOKEN, or in a "
            "mode 0600 file named by NOTION_TOKEN_FILE. Do not paste it into chat.\n"
            "5. Reply /omega-connectors duplicated when the database exists."
        )
    if step == "notion":
        if binding["notion"]["dashboard"] == "schema-mismatch":
            return (
                "The duplicate does not match contract version "
                f"{CONTRACT_VERSION}. Fix the missing property named in the "
                "last reply, then reply /omega-connectors connected."
            )
        return (
            "1. Share the duplicated Tasks database with your Notion integration.\n"
            "2. Set NOTION_TOKEN or NOTION_TOKEN_FILE on the host. Do not paste "
            "the token into chat.\n"
            "3. Reply /omega-connectors connected."
        )
    if step == "linear":
        return (
            "1. Create a Linear personal API key that can read teams and create "
            "issues.\n"
            "2. Set LINEAR_API_KEY or LINEAR_API_KEY_FILE on the host. Do not "
            "paste the key into chat.\n"
            "3. Reply /omega-connectors connected."
        )
    if step == "linear-team":
        return "Reply /omega-connectors linear-team <id> with a team id from the list."
    if step == "linear-project":
        return (
            "Reply /omega-connectors linear-project <id> with a project id "
            "from the list."
        )
    if step == "gateway":
        return (
            "Gateways are optional. Notion and Linear already run without one.\n"
            "1. Reply /omega-connectors skip-gateway to leave them unset.\n"
            "2. Or reply /omega-connectors gateway <substrate|redteam|desk_bus> <url> "
            "to validate one. substrate is the brief/event URL, redteam is the "
            "T3MP3ST gateway, and desk_bus is OMEGA_PRIME_DESK_BUS_URL."
        )
    lines = [
        "Setup is complete.",
        f"Notion: ready (contract version {binding['notion']['contract_version']}).",
        "Linear: ready"
        + (
            f" (team {binding['linear']['team_name']})."
            if binding["linear"]["team_name"]
            else "."
        ),
    ]
    for name in GATEWAY_NAMES:
        lines.append(f"{name}: {binding['gateways'][name]['state']}.")
    lines.append(
        "Reply reset notion, reset linear, or reset gateway to change one binding."
    )
    return "\n".join(lines)


def _choices(rows: list[dict[str, str]]) -> str:
    return "\n".join(
        f"{index}. {row.get('name') or row['id']} ({row['id']})"
        for index, row in enumerate(rows, start=1)
    )


def _pack(
    binding: dict[str, Any], *, ok: bool, error: str, prompt: str
) -> dict[str, Any]:
    return {
        "ok": ok,
        "error": error,
        "overall": _overall(binding),
        "step": _step(binding),
        "prompt": prompt,
        "notion": {
            "state": binding["notion"]["state"],
            "dashboard": binding["notion"]["dashboard"],
            "contract_version": binding["notion"]["contract_version"],
        },
        "linear": {
            "state": binding["linear"]["state"],
            "team_id": binding["linear"]["team_id"],
            "project_id": binding["linear"]["project_id"],
        },
        "gateways": {
            name: binding["gateways"][name]["state"] for name in GATEWAY_NAMES
        },
    }
