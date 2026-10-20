"""Read-only surface probes, run by hand against live services.

`python -m omega_prime.substrate.probes` checks substrate-mcp (`GET /healthz`,
`POST /brief`) and the Hindsight service (`GET /health`, `GET /version`)
from environment config. Every probe is a read: the probe transport never
emits, retains, writes memory, or claims. Secrets are never printed — the
report names which variables are set, never their values.

Manual tooling only: this module is not imported by tests-with-network
(because there are none) and must never run in CI.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

SUBSTRATE_URL_ENV = "SUBSTRATE_URL"
SUBSTRATE_TOKEN_ENVS = ("SUBSTRATE_TOKEN", "SUBSTRATE_TOKEN_GROK_BOT")
HINDSIGHT_URL_ENV = "HINDSIGHT_URL"
HINDSIGHT_TOKEN_ENVS = ("HINDSIGHT_API_KEY", "HINDSIGHT_API_TOKEN")

DEFAULT_HINDSIGHT_URL = ""


def config_from_env(env: Any = None) -> dict:
    """Read probe config. Values stay in the dict; callers never print them."""
    source = env if env is not None else os.environ
    substrate_token, substrate_token_name = _named_secret(source, SUBSTRATE_TOKEN_ENVS)
    raw_substrate_url = source.get(SUBSTRATE_URL_ENV)
    try:
        from omega_prime.substrate.client import _check_base_url

        substrate_url = _check_base_url(raw_substrate_url)
        substrate_error = None if substrate_url else "not_configured"
    except ValueError:
        substrate_url = None
        substrate_error = "unsafe substrate URL"
    hindsight_raw = source.get(HINDSIGHT_URL_ENV, "")
    hindsight_url = (
        hindsight_raw
        if isinstance(hindsight_raw, str) and hindsight_raw.strip()
        else ""
    ) or DEFAULT_HINDSIGHT_URL
    # An unset URL disables the bank. Do not read its credential: a missing
    # or badly mode-checked HINDSIGHT_*_FILE would raise here and block the
    # substrate probes that should still run.
    hindsight_token, hindsight_token_name = (
        _named_secret(source, HINDSIGHT_TOKEN_ENVS) if hindsight_url else ("", "")
    )
    return {
        "substrate_url": substrate_url,
        "substrate_error": substrate_error,
        "substrate_token": substrate_token,
        "substrate_token_name": substrate_token_name,
        "hindsight_url": hindsight_url,
        "hindsight_token": hindsight_token,
        "hindsight_token_name": hindsight_token_name,
    }


def run_probes(config: dict, transport: Any) -> dict:
    """Run every probe; each is attempted even when an earlier one fails."""
    secrets = [
        value
        for value in (config.get("substrate_token"), config.get("hindsight_token"))
        if isinstance(value, str) and value
    ]
    probes: dict[str, dict] = {}
    substrate_error = config.get("substrate_error")
    if substrate_error or not config.get("substrate_url"):
        detail = (
            "not_configured"
            if substrate_error == "not_configured"
            else "unsafe substrate URL"
        )
        probes["substrate_health"] = {"ok": False, "detail": detail}
        probes["substrate_brief"] = {"ok": False, "detail": detail}
    else:
        probes["substrate_health"] = _probe(
            lambda: transport.get(
                _join(config["substrate_url"], "/healthz"),
                _auth(config.get("substrate_token")),
                {},
            ),
            _health_detail,
            secrets,
        )
        probes["substrate_brief"] = _probe(
            lambda: transport.post_text(
                _join(config["substrate_url"], "/brief"),
                _auth(config.get("substrate_token")),
                {"surface": "grok-bot"},
            ),
            _brief_detail,
            secrets,
        )
    hindsight_url = config.get("hindsight_url") or ""
    if isinstance(hindsight_url, str) and hindsight_url.strip():
        probes["hindsight_health"] = _probe(
            lambda: transport.get(
                _join(hindsight_url, "/health"),
                _auth(config.get("hindsight_token")),
                {},
            ),
            _health_detail,
            secrets,
        )
        probes["hindsight_version"] = _probe(
            lambda: transport.get(
                _join(hindsight_url, "/version"),
                _auth(config.get("hindsight_token")),
                {},
            ),
            _version_detail,
            secrets,
        )
    else:
        skipped = {"ok": True, "detail": "disabled (HINDSIGHT_URL unset)"}
        probes["hindsight_health"] = dict(skipped)
        probes["hindsight_version"] = dict(skipped)
    passed = all(probe["ok"] for probe in probes.values())
    return {"probes": probes, "passed": passed}


def format_report(report: dict, config: dict) -> str:
    """One line per probe plus the verdict. Never includes secret values."""
    lines = []
    for name, probe in report["probes"].items():
        mark = "ok" if probe["ok"] else "FAIL"
        lines.append(f"[{mark}] {name}: {probe['detail']}")
    lines.append(_config_line(config))
    lines.append("probes passed" if report["passed"] else "probes FAILED")
    return "\n".join(lines)


def main(argv: list[str] | None = None, env: Any = None, transport: Any = None) -> int:
    """Run the probes from the environment. Exit 0 iff every probe passes."""
    del argv
    from omega_prime.providers.http import HttpTransport

    config = config_from_env(env)
    transport = transport or HttpTransport(timeout=10.0)
    report = run_probes(config, transport)
    print(format_report(report, config))
    return 0 if report["passed"] else 1


def _named_secret(source: Any, names: tuple[str, ...]) -> tuple[str, str]:
    """First configured token among ``names``. ``*_FILE`` and the state dir count."""
    from omega_prime.integrations.binding import secret_value

    for name in names:
        value = secret_value(source, name)
        if value:
            return value, name
    return "", ""


def _probe(call: Any, describe: Any, secrets: list[str]) -> dict:
    try:
        result = call()
    except Exception as exc:
        return {"ok": False, "detail": _redact(str(exc) or type(exc).__name__, secrets)}
    try:
        return {"ok": True, "detail": _redact(describe(result), secrets)}
    except Exception as exc:
        return {"ok": False, "detail": _redact(f"unparsable response: {exc}", secrets)}


def _health_detail(body: Any) -> str:
    if not isinstance(body, dict):
        raise ValueError("health body is not an object")
    parts = []
    for key in ("index", "greptime", "eventsWritable", "status", "ok"):
        if key in body:
            parts.append(f"{key}={body[key]}")
    return ", ".join(parts) or "healthy"


def _brief_detail(body: Any) -> str:
    if not isinstance(body, str):
        raise ValueError("brief body is not text")
    if body == "":
        return "empty brief (surface idle or anonymous)"
    lines = body.count("\n") + 1
    return f"brief received ({lines} lines, {len(body)} chars)"


def _version_detail(body: Any) -> str:
    if not isinstance(body, dict):
        raise ValueError("version body is not an object")
    version = body.get("api_version", "?")
    return f"api_version={version}"


def _auth(token: Any) -> dict:
    if isinstance(token, str) and token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _join(base: Any, path: str) -> str:
    if not isinstance(base, str) or base == "":
        raise ValueError("probe base URL is missing")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"probe base URL is not http(s): {base}")
    return base.rstrip("/") + path


def _redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def _config_line(config: dict) -> str:
    names = []
    if config.get("substrate_token_name"):
        names.append(config["substrate_token_name"])
    if config.get("hindsight_token_name"):
        names.append(config["hindsight_token_name"])
    if not names:
        return "auth: anonymous (no token env set)"
    return f"auth: {' + '.join(names)} set (values never shown)"


__all__ = ["config_from_env", "format_report", "main", "run_probes"]


if __name__ == "__main__":
    raise SystemExit(main())
