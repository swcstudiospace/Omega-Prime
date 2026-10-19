# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Installer binding file. Identifiers and states only. Never a secret."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omega_prime.tooling.fs import atomic_write_json, read_secret_file

LINEAR_ENV = "LINEAR_API_KEY"
NOTION_ENV = "NOTION_TOKEN"
GATEWAY_NAMES = ("substrate", "redteam", "desk_bus")
_STATES = {
    "unconfigured",
    "awaiting-user",
    "validating",
    "ready",
    "invalid",
    "degraded",
    "skipped",
}
_DASHBOARD = {
    "template-not-duplicated",
    "shared-but-unchecked",
    "schema-mismatch",
    "ready",
}


def fresh_binding() -> dict[str, Any]:
    """A new install: Notion template first, Linear unset, gateways unset."""
    return {
        "version": 1,
        "notion": {
            "state": "unconfigured",
            "dashboard": "template-not-duplicated",
            "database_id": "",
            "contract_version": 0,
            "offered_database_ids": [],
        },
        "linear": {
            "state": "unconfigured",
            "team_id": "",
            "team_name": "",
            "project_id": "",
            "project_name": "",
            "offered_team_ids": [],
            "offered_project_ids": [],
        },
        "gateways": {
            name: {"state": "unconfigured", "url": ""} for name in GATEWAY_NAMES
        },
    }


def binding_path(env: Mapping[str, str] | None = None) -> Path:
    """State file outside the repository. ``OMEGA_PRIME_STATE_DIR`` wins."""
    source = os.environ if env is None else env
    state = source.get("OMEGA_PRIME_STATE_DIR", "")
    if isinstance(state, str) and state.strip():
        root = Path(state.strip()).expanduser()
    else:
        root = Path.home() / ".omega-prime"
    path = (root / "integrations.json").resolve()
    repo = Path(__file__).resolve().parents[2]
    if path == repo or repo in path.parents:
        raise ValueError("refusing to store installer bindings inside the repository")
    return path


def load_binding(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Load the binding, or a fresh one when the file is missing or unreadable.

    The file body is never included in an error. Unknown keys are dropped.
    """
    try:
        path = binding_path(env)
    except ValueError:
        return fresh_binding()
    if not path.is_file():
        return fresh_binding()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fresh_binding()
    if not isinstance(loaded, dict):
        return fresh_binding()
    return _sanitize(loaded)


def save_binding(binding: dict[str, Any], env: Mapping[str, str] | None = None) -> None:
    """Atomically write a sanitized binding (mode 0600)."""
    path = binding_path(env)
    atomic_write_json(path, _sanitize(binding))


def resolve_secret(env: Mapping[str, Any], name: str) -> tuple[str | None, str | None]:
    """Return ``(value, error)``. The error names the env var, never the value.

    ``name`` is the env var. ``name + "_FILE"`` is a mode 0600 file path.
    When both are unset, ``$OMEGA_PRIME_STATE_DIR/secrets/<name>`` is the
    same kind of file. A published Command server carries no env, so that
    directory is how a fresh install supplies a connector token.
    """
    raw = env.get(name, "")
    if isinstance(raw, str) and raw.strip():
        return raw.strip(), None
    file_key = name + "_FILE"
    file_value = env.get(file_key, "")
    if isinstance(file_value, str) and file_value.strip():
        try:
            return read_secret_file(file_value.strip()), None
        except ValueError:
            return None, (
                f"{file_key} is missing, empty, or not mode 0600. "
                "Fix the file on the host. Do not paste the secret into chat."
            )
    return _state_dir_secret(env, name)


def secret_value(env: Mapping[str, Any], name: str) -> str:
    """The resolved secret, or ``""`` when nothing is configured.

    A configured file that cannot be read raises ``ValueError``. The message
    names the variable, never the secret.
    """
    value, error = resolve_secret(env, name)
    if error:
        raise ValueError(error)
    return value or ""


def secret_configured(env: Mapping[str, Any], name: str) -> bool:
    """True when a value is readable, or a file is configured but unreadable.

    An unreadable file is still "configured": the connector must not ask the
    operator to paste the secret into chat.
    """
    try:
        return bool(secret_value(env, name))
    except ValueError:
        return True


def _state_dir_secret(
    env: Mapping[str, Any], name: str
) -> tuple[str | None, str | None]:
    if not name.isidentifier():
        return None, None
    state = env.get("OMEGA_PRIME_STATE_DIR", "")
    if not isinstance(state, str) or not state.strip():
        return None, None
    path = Path(state.strip()).expanduser() / "secrets" / name
    if not path.exists():
        return None, None
    try:
        return read_secret_file(path), None
    except ValueError:
        return None, (
            f"{name} under OMEGA_PRIME_STATE_DIR/secrets is missing, empty, "
            "or not mode 0600. Fix the file on the host. Do not paste the "
            "secret into chat."
        )


def ready_linear_team(env: Mapping[str, str] | None = None) -> str:
    """Bound Linear team id when that connection is ready, else ``""``."""
    binding = load_binding(env)
    linear = binding["linear"]
    if linear["state"] == "ready" and isinstance(linear["team_id"], str):
        return linear["team_id"]
    return ""


def ready_linear_project(env: Mapping[str, str] | None = None) -> str:
    """Bound Linear project id when that connection is ready, else ``""``."""
    binding = load_binding(env)
    linear = binding["linear"]
    if linear["state"] == "ready" and isinstance(linear["project_id"], str):
        return linear["project_id"]
    return ""


def _sanitize(loaded: Mapping[str, Any]) -> dict[str, Any]:
    clean = fresh_binding()
    notion = loaded.get("notion")
    if isinstance(notion, dict):
        clean["notion"]["state"] = _state(notion.get("state"), "unconfigured")
        dashboard = notion.get("dashboard")
        if dashboard in _DASHBOARD:
            clean["notion"]["dashboard"] = dashboard
        clean["notion"]["database_id"] = _identifier(notion.get("database_id"))
        version = notion.get("contract_version")
        if isinstance(version, int) and not isinstance(version, bool) and version >= 0:
            clean["notion"]["contract_version"] = version
        clean["notion"]["offered_database_ids"] = _id_list(
            notion.get("offered_database_ids")
        )
    linear = loaded.get("linear")
    if isinstance(linear, dict):
        node = clean["linear"]
        node["state"] = _state(linear.get("state"), "unconfigured")
        node["team_id"] = _identifier(linear.get("team_id"))
        node["team_name"] = _label(linear.get("team_name"))
        node["project_id"] = _identifier(linear.get("project_id"))
        node["project_name"] = _label(linear.get("project_name"))
        node["offered_team_ids"] = _id_list(linear.get("offered_team_ids"))
        node["offered_project_ids"] = _id_list(linear.get("offered_project_ids"))
    gateways = loaded.get("gateways")
    if isinstance(gateways, dict):
        for name in GATEWAY_NAMES:
            raw = gateways.get(name)
            if not isinstance(raw, dict):
                continue
            clean["gateways"][name]["state"] = _gateway_state(raw.get("state"))
            clean["gateways"][name]["url"] = _gateway_url(raw.get("url"))
    return clean


def _state(value: Any, default: str) -> str:
    if value in _STATES and value != "skipped":
        return str(value)
    return default


def _gateway_state(value: Any) -> str:
    if value in _STATES:
        return str(value)
    return "unconfigured"


def _identifier(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > 80:
        return ""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if any(char not in allowed for char in text):
        return ""
    return text


def _label(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if len(text) > 120:
        return ""
    return text


def _id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    found: list[str] = []
    for item in value:
        ident = _identifier(item)
        if ident and ident not in found:
            found.append(ident)
        # One setup list can span several API pages. Keep every id the
        # client returned, and stop only at a hard cap so a bad payload
        # cannot grow the binding file without limit.
        if len(found) >= 500:
            break
    return found


def _gateway_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) > 300 or not _acceptable_gateway_url(text):
        return ""
    return text


def _acceptable_gateway_url(text: str) -> bool:
    if text == "":
        return True
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(text)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    return not (parts.username or parts.password)
