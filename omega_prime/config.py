# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Minimal Omega Prime configuration surface.

v10 introduces per-family Prime capability flags. There is intentionally no
central settings module elsewhere in the repo (per-subsystem JSON documents
loaded from explicit paths are the convention — see ``policy/policy.py``
``SeatPolicy.load``); this module is the one small exception, carrying only
the Prime capability gates.

Sources, in precedence order (later wins):

1. Built-in defaults — every Prime family disabled.
2. An optional ``omega-prime.json`` at the repository root (stdlib json, the
   ``SeatPolicy.load`` precedent). Missing file ⇒ defaults.
3. ``OMEGA_PRIME_*`` environment overrides, e.g.
   ``OMEGA_PRIME_PRIME_RLM_ENABLED=1`` sets ``prime.rlm.enabled``.

All flags default off: with every family disabled, Omega Prime behaves
exactly as it did pre-v10 (LOOP-07).
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Prime capability families gated by config. Each maps to one rostered tool
# family or loop hook; a disabled family is not registered and is absent from
# the prompt (LOOP-05).
PRIME_FAMILIES: tuple[str, ...] = (
    "rlm",
    "harness",
    "goals",
    "heartbeat",
    "autonomous",
    "messaging",
    "kernel",
)

_ENV_PREFIX = "OMEGA_PRIME_"


def _defaults() -> dict[str, Any]:
    return {
        "prime": {family: {"enabled": False} for family in PRIME_FAMILIES},
        # Red team family (T3MP3ST loopback gateway): a top-level key, not a
        # Prime family. Off by default; the gateway and bearer file follow the
        # upstream contract.
        "redteam": {
            "enabled": False,
            "gateway_url": "http://127.0.0.1:3333",
            "bearer_token_file": "",
            # Sidecar authorization (Phase 79). All restrictive by default:
            # kill stops everything, RoE is required, and an empty roe_file
            # means BLOCKED when enabled. The verification key never lives
            # in this file: it resolves from OMEGA_PRIME_REDTEAM_ROE_KEY or
            # from the file named by roe_key_file (broker rule). A key value
            # stored under roe_key here is rejected fail-closed by the guard.
            "kill": False,
            "roe_required": True,
            "roe_file": "",
            # Concurrent RoE grants (batch-04 H7): when non-empty, these
            # artifact paths replace the single ``roe_file`` above; the
            # guard authorizes the union of tools from all valid grants.
            # Empty (the default) keeps the legacy single-file behavior.
            "roe_files": [],
            "roe_key": "",
            "roe_key_file": "",
            # Weekly engagement reports (PLAN-F7): output dir override;
            # empty (the default) means sidecar/reports/reports/.
            "reports_dir": "",
            # Coverage-matrix alerts (batch-03 Feature E): pass-rate
            # percent; a covered technique class below it fires the
            # coverage_alert heartbeat kind. The watcher itself stays
            # inert until scheduled. Non-secret.
            "coverage_alert_threshold": 80.0,
            # Release-watcher auto-chaining (batch-03 Feature F). Off by
            # default: check_release_watch() only invokes pin_bump when
            # this is explicitly True. Non-secret.
            "auto_pin_bump": False,
            # Findings → tracker sync (Phase 86, PLAN-F3). Off by default;
            # the CLI and heartbeat tick are inert until enabled with a
            # non-empty repo. Labels/repo are non-secret (operator map of
            # severity → GitHub labels; per-severity replacement).
            "findings_sync": {
                "enabled": False,
                "repo": "",
                "labels": {},
                "tracker": "github",
                "linear_team_id": "",
                # Slack incoming-webhook URL for tracker="slack" (batch-04
                # Feature H3). Empty (the default) means the Slack sync
                # fails closed. Secret: never logged, never rendered.
                "slack_webhook_url": "",
                # Teams webhook URL for tracker="teams" (batch-05).
                # Empty (the default) means the Teams sync fails closed.
                # Secret: never logged, never rendered.
                "teams_webhook_url": "",
            },
        },
        # Telemetry export (Phase 81). Off by default with an empty endpoint;
        # the exporter refuses to start until an endpoint is configured.
        "telemetry": {
            "otlp": {
                "enabled": False,
                "endpoint": "",
                "interval_seconds": 60,
            },
        },
        # Host-token rotation (Phase 82). Off by default; the scheduled
        # `rotate --due-only` job skips unless enabled. overlap_hours is the
        # scheduled-run window (the manual CLI default stays 1h).
        "rotation": {
            "enabled": False,
            "overlap_hours": 24,
        },
        # Per-caller usage quotas (Phase 82). Report-only by default and
        # inert until `document` names a quota file: report mode enforces
        # nothing (counts only), soft warns at >=80%, hard denies over-limit
        # pre-dispatch. An empty document means unlimited for every caller.
        "quotas": {
            "mode": "report",
            "document": "",
            "rollup_dir": ".omega-quotas/",
        },
        # OpenShell sandboxed execution (Phase 83). Off by default: with
        # sandbox disabled, gates and execute paths run exactly as today.
        # `policy` is the generated sandbox policy file (plan 83-01);
        # `scope` gates which paths may sandbox — `gates` (verification
        # gates only, the fixed slice order) or `gates+execute`.
        "sandbox": {
            "enabled": False,
            "policy": "omega_prime/hosting/openshell/sandbox-policy.yaml",
            "scope": "gates",
        },
        # Live evals and regression tracking (Phase 84). `regress_threshold`
        # is the per-case score drop that counts as a regression; the judge
        # is off by default with an empty model (enabling without a model
        # still refuses to start). The judge credential comes from the
        # environment/secret store only — never from this file.
        "evals": {
            "regress_threshold": 0.1,
            "judge": {
                "enabled": False,
                "model": "",
            },
        },
    }


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``overlay`` onto ``base`` (overlay wins on scalars)."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(root: str | Path | None = None) -> dict[str, Any]:
    """Load the merged configuration. A missing config file is not an error."""
    config = _defaults()
    if root is not None:
        path = Path(root) / "omega-prime.json"
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("omega-prime.json must contain a JSON object")
            if _file_holds_roe_key(loaded):
                # Broker rule: the verification key must never live in the
                # config file. The marker survives the env merge below so
                # the guard can reject fail-closed even when the env key
                # is also set.
                loaded.setdefault("redteam", {})["roe_key_in_file"] = True
            _merge(config, loaded)
    _merge(config, _env_overrides(os.environ))
    _normalize_quotas_mode(config)
    _normalize_sandbox_scope(config)
    _normalize_evals(config)
    return config


def _file_holds_roe_key(loaded: dict[str, Any]) -> bool:
    """True when the config file itself stores a ``redteam.roe_key`` value."""
    node = loaded.get("redteam")
    if not isinstance(node, dict):
        return False
    value = node.get("roe_key")
    return isinstance(value, str) and bool(value.strip())


QUOTA_MODES = ("report", "soft", "hard")

SANDBOX_SCOPES = ("gates", "gates+execute")


def _normalize_sandbox_scope(config: dict[str, Any]) -> None:
    """Fall back to `gates` loudly when `sandbox.scope` is unknown.

    Config loading never crashes on a bogus scope and never silently
    enables enforcement: anything outside {gates, gates+execute} reads as
    gates (verification gates only, the fixed slice order).
    """
    node = config.get("sandbox")
    if not isinstance(node, dict):
        return
    scope = node.get("scope", "gates")
    if scope not in SANDBOX_SCOPES:
        print(
            f"warning: ignoring invalid sandbox.scope={scope!r}; using 'gates'",
            file=sys.stderr,
        )
        node["scope"] = "gates"


def _normalize_quotas_mode(config: dict[str, Any]) -> None:
    """Fall back to `report` loudly when `quotas.mode` is unknown.

    Config loading never crashes on a bogus mode and never silently
    enables enforcement: anything outside {report, soft, hard} reads as
    report (the enforcing-nothing default).
    """
    node = config.get("quotas")
    if not isinstance(node, dict):
        return
    mode = node.get("mode", "report")
    if mode not in QUOTA_MODES:
        print(
            f"warning: ignoring invalid quotas.mode={mode!r}; using 'report'",
            file=sys.stderr,
        )
        node["mode"] = "report"


def _normalize_evals(config: dict[str, Any]) -> None:
    """Fall back to the default threshold loudly when invalid.

    Config loading never crashes on a bogus ``evals.regress_threshold``
    and never silently gates: anything non-numeric or non-positive reads
    as the default (0.1). The judge node needs no normalization — off by
    default with an empty model, and enabling without a model still
    refuses to start at the judge call site.
    """
    node = config.get("evals")
    if not isinstance(node, dict):
        return
    threshold = node.get("regress_threshold", 0.1)
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not (threshold > 0)
        or threshold == float("inf")
    ):
        print(
            f"warning: ignoring invalid evals.regress_threshold={threshold!r}; "
            "using default 0.1",
            file=sys.stderr,
        )
        node["regress_threshold"] = 0.1


def _env_overrides(environ: Mapping[str, str]) -> dict[str, Any]:
    """Map ``OMEGA_PRIME_PRIME_<FAMILY>_ENABLED`` style vars onto the tree."""
    overlay: dict[str, Any] = {}
    for key, value in environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        parts = key[len(_ENV_PREFIX) :].lower().split("_")
        if not parts:
            continue
        if parts[0] == "prime":
            # prime_<family>_enabled — family names are single words today.
            if len(parts) == 3 and parts[1] in PRIME_FAMILIES and parts[2] == "enabled":
                enabled = value.strip().lower() in ("1", "true", "yes", "on")
                overlay.setdefault("prime", {}).setdefault(parts[1], {})["enabled"] = (
                    enabled
                )
        elif parts[0] == "redteam":
            # redteam_enabled / redteam_gateway_url.
            if len(parts) == 2 and parts[1] == "enabled":
                overlay.setdefault("redteam", {})["enabled"] = (
                    value.strip().lower()
                    in (
                        "1",
                        "true",
                        "yes",
                        "on",
                    )
                )
            elif len(parts) == 3 and parts[1] == "gateway" and parts[2] == "url":
                overlay.setdefault("redteam", {})["gateway_url"] = value.strip()
            elif len(parts) == 2 and parts[1] == "kill":
                # OMEGA_PRIME_REDTEAM_KILL — evaluated before every other
                # redteam control; true implies BLOCKED (GAP-08).
                overlay.setdefault("redteam", {})["kill"] = value.strip().lower() in (
                    "1",
                    "true",
                    "yes",
                    "on",
                )
            elif len(parts) == 3 and parts[1] == "roe" and parts[2] == "required":
                # OMEGA_PRIME_REDTEAM_ROE_REQUIRED — default true (fail
                # closed); explicit falsy opts out of the RoE requirement.
                overlay.setdefault("redteam", {})["roe_required"] = (
                    value.strip().lower()
                    in (
                        "1",
                        "true",
                        "yes",
                        "on",
                    )
                )
            elif len(parts) == 3 and parts[1] == "roe" and parts[2] == "file":
                # OMEGA_PRIME_REDTEAM_ROE_FILE — path to the signed artifact;
                # empty (the default) means BLOCKED when enabled.
                overlay.setdefault("redteam", {})["roe_file"] = value.strip()
            elif len(parts) == 3 and parts[1] == "roe" and parts[2] == "files":
                # OMEGA_PRIME_REDTEAM_ROE_FILES — comma-separated concurrent
                # grant paths; when non-empty these replace roe_file.
                # Empty entries are dropped; empty keeps the legacy
                # single-file behavior. Non-secret (paths only).
                overlay.setdefault("redteam", {})["roe_files"] = [
                    entry.strip() for entry in value.split(",") if entry.strip()
                ]
            elif len(parts) == 3 and parts[1] == "roe" and parts[2] == "key":
                # OMEGA_PRIME_REDTEAM_ROE_KEY — detached-HMAC verification
                # key. Secret: never logged, never rendered to metrics.
                overlay.setdefault("redteam", {})["roe_key"] = value.strip()
            elif (
                len(parts) == 4
                and parts[1] == "roe"
                and parts[2] == "key"
                and parts[3] == "file"
            ):
                # OMEGA_PRIME_REDTEAM_ROE_KEY_FILE — path to a file holding
                # the verification key (broker-rule alternative to the env
                # key above; mirrors bearer_token_file).
                overlay.setdefault("redteam", {})["roe_key_file"] = value.strip()
            elif len(parts) == 3 and parts[1] == "reports" and parts[2] == "dir":
                # OMEGA_PRIME_REDTEAM_REPORTS_DIR — engagement-report
                # output dir override; empty (the default) means
                # sidecar/reports/reports/. Non-secret.
                overlay.setdefault("redteam", {})["reports_dir"] = value.strip()
            elif (
                len(parts) == 4
                and parts[1] == "findings"
                and parts[2] == "sync"
                and parts[3] == "enabled"
            ):
                # OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_ENABLED — truthy enables
                # the findings → tracker push; anything else leaves the
                # default (off) in place.
                overlay.setdefault("redteam", {}).setdefault("findings_sync", {})[
                    "enabled"
                ] = value.strip().lower() in ("1", "true", "yes", "on")
            elif (
                len(parts) == 4
                and parts[1] == "findings"
                and parts[2] == "sync"
                and parts[3] == "repo"
            ):
                # OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_REPO — target OWNER/NAME;
                # empty (the default) means the CLI fails closed. Non-secret.
                overlay.setdefault("redteam", {}).setdefault("findings_sync", {})[
                    "repo"
                ] = value.strip()
            elif (
                len(parts) == 4
                and parts[1] == "findings"
                and parts[2] == "sync"
                and parts[3] == "tracker"
            ):
                # OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_TRACKER — tracker
                # backend (github/linear/slack/teams); blank or unknown
                # falls back to github loudly at sync time. Non-secret.
                overlay.setdefault("redteam", {}).setdefault("findings_sync", {})[
                    "tracker"
                ] = value.strip()
            elif (
                len(parts) == 6
                and parts[1] == "findings"
                and parts[2] == "sync"
                and parts[3] == "linear"
                and parts[4] == "team"
                and parts[5] == "id"
            ):
                # OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_LINEAR_TEAM_ID — Linear
                # team ID; required (fail-closed) when tracker=linear.
                # Non-secret.
                overlay.setdefault("redteam", {}).setdefault("findings_sync", {})[
                    "linear_team_id"
                ] = value.strip()
            elif (
                len(parts) == 6
                and parts[1] == "findings"
                and parts[2] == "sync"
                and parts[3] == "slack"
                and parts[4] == "webhook"
                and parts[5] == "url"
            ):
                # OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_SLACK_WEBHOOK_URL —
                # Slack incoming-webhook URL; required (fail-closed) when
                # tracker=slack. Secret: never logged.
                overlay.setdefault("redteam", {}).setdefault("findings_sync", {})[
                    "slack_webhook_url"
                ] = value.strip()
            elif (
                len(parts) == 6
                and parts[1] == "findings"
                and parts[2] == "sync"
                and parts[3] == "teams"
                and parts[4] == "webhook"
                and parts[5] == "url"
            ):
                # OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_TEAMS_WEBHOOK_URL —
                # Teams webhook URL; required (fail-closed) when
                # tracker=teams. Secret: never logged.
                overlay.setdefault("redteam", {}).setdefault("findings_sync", {})[
                    "teams_webhook_url"
                ] = value.strip()
            elif (
                len(parts) == 4
                and parts[1] == "auto"
                and parts[2] == "pin"
                and parts[3] == "bump"
            ):
                # OMEGA_PRIME_REDTEAM_AUTO_PIN_BUMP — truthy opts in to
                # release-watch → pin-bump chaining; anything else leaves
                # the default (off) in place. Non-secret.
                overlay.setdefault("redteam", {})["auto_pin_bump"] = (
                    value.strip().lower() in ("1", "true", "yes", "on")
                )
            elif (
                len(parts) == 4
                and parts[1] == "coverage"
                and parts[2] == "alert"
                and parts[3] == "threshold"
            ):
                # OMEGA_PRIME_REDTEAM_COVERAGE_ALERT_THRESHOLD — pass-rate
                # percent below which a class fires coverage_alert.
                # Invalid values are ignored loudly; config loading never
                # crashes and never silently arms the alerter.
                try:
                    threshold = float(value.strip())
                except ValueError:
                    threshold = float("nan")
                if not (threshold > 0 and threshold <= 100):
                    print(
                        "warning: ignoring invalid "
                        "OMEGA_PRIME_REDTEAM_COVERAGE_ALERT_THRESHOLD"
                        f"={value.strip()!r}; using default",
                        file=sys.stderr,
                    )
                    continue
                overlay.setdefault("redteam", {})["coverage_alert_threshold"] = (
                    threshold
                )
        elif parts[0] == "telemetry":
            # OMEGA_PRIME_TELEMETRY_OTLP_ENABLED — truthy enables export;
            # the exporter additionally requires a non-empty push target.
            if len(parts) == 3 and parts[1] == "otlp" and parts[2] == "enabled":
                overlay.setdefault("telemetry", {}).setdefault("otlp", {})[
                    "enabled"
                ] = value.strip().lower() in ("1", "true", "yes", "on")
            elif len(parts) == 3 and parts[1] == "otlp" and parts[2] == "endpoint":
                # OMEGA_PRIME_TELEMETRY_OTLP_ENDPOINT — push target URL.
                overlay.setdefault("telemetry", {}).setdefault("otlp", {})[
                    "endpoint"
                ] = value.strip()
            elif (
                len(parts) == 4
                and parts[1] == "otlp"
                and parts[2] == "interval"
                and parts[3] == "seconds"
            ):
                # OMEGA_PRIME_TELEMETRY_OTLP_INTERVAL_SECONDS — push cadence.
                try:
                    interval = int(value.strip())
                except ValueError:
                    continue
                overlay.setdefault("telemetry", {}).setdefault("otlp", {})[
                    "interval_seconds"
                ] = interval
        elif parts[0] == "rotation":
            # OMEGA_PRIME_ROTATION_ENABLED — truthy enables scheduled
            # rotation; anything else leaves the default (off) in place.
            if len(parts) == 2 and parts[1] == "enabled":
                overlay.setdefault("rotation", {})["enabled"] = (
                    value.strip().lower() in ("1", "true", "yes", "on")
                )
            elif len(parts) == 3 and parts[1] == "overlap" and parts[2] == "hours":
                # OMEGA_PRIME_ROTATION_OVERLAP_HOURS — scheduled-run window.
                # Invalid values fall back to the default loudly; config
                # loading never crashes and never silently enables rotation.
                try:
                    hours = float(value.strip())
                except ValueError:
                    hours = float("nan")
                if not (hours > 0 and hours != float("inf")):
                    print(
                        "warning: ignoring invalid OMEGA_PRIME_ROTATION_OVERLAP_HOURS"
                        f"={value.strip()!r}; using default",
                        file=sys.stderr,
                    )
                    continue
                overlay.setdefault("rotation", {})["overlap_hours"] = hours
        elif parts[0] == "quotas":
            # OMEGA_PRIME_QUOTAS_MODE — report/soft/hard; anything else
            # falls back to report loudly in _normalize_quotas_mode
            # (never crashes, never silently enables enforcement).
            if len(parts) == 2 and parts[1] == "mode":
                overlay.setdefault("quotas", {})["mode"] = value.strip().lower()
            elif len(parts) == 2 and parts[1] == "document":
                # OMEGA_PRIME_QUOTAS_DOCUMENT — path to the quota JSON;
                # empty (the default) means the engine is inert.
                overlay.setdefault("quotas", {})["document"] = value.strip()
        elif parts[0] == "sandbox":
            # OMEGA_PRIME_SANDBOX_ENABLED — truthy enables sandboxed
            # execution; anything else leaves the default (off) in place.
            if len(parts) == 2 and parts[1] == "enabled":
                overlay.setdefault("sandbox", {})["enabled"] = (
                    value.strip().lower() in ("1", "true", "yes", "on")
                )
            elif len(parts) == 2 and parts[1] == "policy":
                # OMEGA_PRIME_SANDBOX_POLICY — generated policy file path.
                overlay.setdefault("sandbox", {})["policy"] = value.strip()
            elif len(parts) == 2 and parts[1] == "scope":
                # OMEGA_PRIME_SANDBOX_SCOPE — gates/gates+execute; anything
                # else falls back to gates loudly in _normalize_sandbox_scope
                # (never crashes, never silently enables enforcement).
                overlay.setdefault("sandbox", {})["scope"] = value.strip().lower()
        elif parts[0] == "evals":
            # OMEGA_PRIME_EVALS_REGRESS_THRESHOLD — per-case score drop that
            # counts as a regression. Invalid values are ignored here and
            # fall back to the default loudly in _normalize_evals (never
            # crashes, never silently gates).
            if len(parts) == 3 and parts[1] == "regress" and parts[2] == "threshold":
                try:
                    threshold = float(value.strip())
                except ValueError:
                    threshold = float("nan")
                if not (threshold > 0 and threshold != float("inf")):
                    print(
                        "warning: ignoring invalid OMEGA_PRIME_EVALS_REGRESS_THRESHOLD"
                        f"={value.strip()!r}; using default",
                        file=sys.stderr,
                    )
                    continue
                overlay.setdefault("evals", {})["regress_threshold"] = threshold
            elif len(parts) == 3 and parts[1] == "judge" and parts[2] == "enabled":
                # OMEGA_PRIME_EVALS_JUDGE_ENABLED — truthy enables the
                # live-model judge; anything else leaves it off.
                overlay.setdefault("evals", {}).setdefault("judge", {})["enabled"] = (
                    value.strip().lower() in ("1", "true", "yes", "on")
                )
            elif len(parts) == 3 and parts[1] == "judge" and parts[2] == "model":
                # OMEGA_PRIME_EVALS_JUDGE_MODEL — model id; empty (the
                # default) means the judge refuses to start even when
                # enabled. The endpoint credential is env-only, never here.
                overlay.setdefault("evals", {}).setdefault("judge", {})["model"] = (
                    value.strip()
                )
    return overlay


def prime_enabled(config: dict[str, Any], family: str) -> bool:
    """Whether one Prime capability family is enabled. Unknown ⇒ False."""
    if family not in PRIME_FAMILIES:
        return False
    node = config.get("prime", {}).get(family, {})
    return bool(isinstance(node, dict) and node.get("enabled") is True)


def redteam_enabled(config: dict[str, Any]) -> bool:
    """Whether the red team gateway family is enabled. Absent ⇒ False."""
    node = config.get("redteam", {})
    return bool(isinstance(node, dict) and node.get("enabled") is True)


def redteam_kill(config: dict[str, Any]) -> bool:
    """Whether the red team kill switch is set. Absent ⇒ False."""
    node = config.get("redteam", {})
    return bool(isinstance(node, dict) and node.get("kill") is True)


def redteam_roe_files(config: dict[str, Any]) -> list[str]:
    """Configured RoE artifact paths (batch-04 H7).

    A non-empty ``redteam.roe_files`` list replaces the legacy single
    ``redteam.roe_file``; otherwise the single path (when non-empty) is
    returned alone. Empty list means no grant is configured (BLOCKED when
    enabled). Non-string entries are ignored; surrounding whitespace is
    stripped.
    """
    node = config.get("redteam", {})
    if not isinstance(node, dict):
        return []
    files = node.get("roe_files")
    if isinstance(files, (list, tuple)):
        paths = [
            entry.strip() for entry in files if isinstance(entry, str) and entry.strip()
        ]
        paths.extend(
            str(entry).strip()
            for entry in files
            if isinstance(entry, os.PathLike) and str(entry).strip()
        )
        if paths:
            return sorted(set(paths))
    single = node.get("roe_file", "")
    if isinstance(single, str) and single.strip():
        return [single.strip()]
    if isinstance(single, os.PathLike) and str(single).strip():
        return [str(single).strip()]
    return []


def family_config(config: dict[str, Any], family: str) -> dict[str, Any]:
    """The raw config subtree for one family (empty dict when absent)."""
    node = config.get("prime", {}).get(family, {})
    return node if isinstance(node, dict) else {}


__all__ = [
    "PRIME_FAMILIES",
    "QUOTA_MODES",
    "SANDBOX_SCOPES",
    "family_config",
    "load_config",
    "prime_enabled",
    "redteam_enabled",
    "redteam_kill",
    "redteam_roe_files",
]
