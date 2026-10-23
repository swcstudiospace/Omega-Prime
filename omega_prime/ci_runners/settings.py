"""Validated controller and agent settings (the one settings convention).

Secrets are referenced by file path only (``*_FILE``); the single exception is
the Railway-only ``OMEGA_CI_AGENT_TOKEN`` variable on the agent. Secret values
are read on demand, never stored in a repr, and never logged.

Auth selection is explicit and fail-closed: a complete GitHub App
configuration selects App auth; a wholly absent App configuration plus
``OMEGA_CI_GITHUB_PAT_FILE`` selects PAT; a partial App configuration is an
error. Ambient credentials (``GH_TOKEN`` and friends) are never consulted.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

DEFAULT_LABELS: tuple[str, ...] = ("self-hosted", "linux", "x64", "omega-ci")
DEFAULT_RUNNER_GROUP = "omega-ci"
DEFAULT_MAX_RUNNERS = 8
HARD_MAX_RUNNERS = 10
DEFAULT_IDLE_GRACE_S = 180.0
DEFAULT_WAKE_BUDGET_S = 150.0
MAX_BODY_BYTES = 1 << 20
RUNNER_PREFIX = "omega-ci-"
RUNNER_NAME_RE = re.compile(r"^omega-ci-[a-z0-9-]{1,48}$")
DEFAULT_EVENTS_PATH = Path("~/.omega/ci-runners/events.jsonl")
DEFAULT_AGENT_RUNNER_ROOT = Path("/opt/actions-runner")
DEFAULT_AGENT_WORK_ROOT = Path("/config/ci-runners")

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9._-]{1,100}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_APP_KEYS = (
    "OMEGA_CI_GITHUB_APP_ID",
    "OMEGA_CI_GITHUB_INSTALLATION_ID",
    "OMEGA_CI_GITHUB_APP_KEY_FILE",
)


class SettingsError(ValueError):
    """A setting is missing or invalid. Messages name variables, never values."""


@dataclass(frozen=True)
class SecretFile:
    """Reference to a file that holds one secret. The value is read on demand."""

    env_name: str
    path: Path

    def read(self) -> str:
        try:
            value = self.path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SettingsError(
                f"{self.env_name}: secret file unreadable ({type(exc).__name__})"
            ) from None
        if not value:
            raise SettingsError(f"{self.env_name}: secret file is empty")
        return value

    def __repr__(self) -> str:
        return f"SecretFile({self.env_name}, path={str(self.path)!r})"


@dataclass(frozen=True)
class SecretValue:
    """An in-memory secret (Railway variable). ``repr`` never shows it."""

    env_name: str
    _value: str = field(repr=False)

    def read(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"SecretValue({self.env_name}, <redacted>)"


class AuthMode(enum.Enum):
    APP = "app"
    PAT = "pat"


@dataclass(frozen=True)
class AppAuth:
    app_id: int
    installation_id: int
    key_file: SecretFile


def _get(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _int(
    env: Mapping[str, str],
    name: str,
    default: int | None,
    *,
    low: int,
    high: int | None = None,
) -> int | None:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SettingsError(f"{name}: not an integer") from None
    if value < low or (high is not None and value > high):
        bound = f"{low}..{high}" if high is not None else f">= {low}"
        raise SettingsError(f"{name}: must be {bound}")
    return value


def _float(
    env: Mapping[str, str], name: str, default: float, *, low: float, high: float
) -> float:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise SettingsError(f"{name}: not a number") from None
    if not (low <= value <= high):
        raise SettingsError(f"{name}: must be {low:g}..{high:g}")
    return value


def _secret_file(env: Mapping[str, str], name: str) -> SecretFile | None:
    raw = _get(env, name)
    return SecretFile(name, Path(raw).expanduser()) if raw else None


def _required_secret_file(env: Mapping[str, str], name: str) -> SecretFile:
    ref = _secret_file(env, name)
    if ref is None:
        raise SettingsError(f"{name}: required (path to a secret file)")
    return ref


_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def _url(raw: str, name: str, *, https: bool) -> str:
    """Bearer-carrying URLs are https; ``https=False`` allows http on loopback."""
    parts = urlsplit(raw)
    ok = parts.scheme == "https" or (
        not https and parts.scheme == "http" and parts.hostname in _LOOPBACK
    )
    if not ok or not parts.hostname:
        want = "an https:// URL" if https else "https:// (http only on loopback)"
        raise SettingsError(f"{name}: must be {want}")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise SettingsError(f"{name}: must not carry credentials, query or fragment")
    return raw.rstrip("/")


def parse_labels(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return DEFAULT_LABELS
    labels = tuple(
        dict.fromkeys(p.strip().lower() for p in raw.split(",") if p.strip())
    )
    if not labels or any(not _LABEL.match(label) for label in labels):
        raise SettingsError("OMEGA_CI_LABELS: comma-separated runner labels")
    if "self-hosted" not in labels:
        raise SettingsError("OMEGA_CI_LABELS: must include self-hosted")
    return labels


def _repos(raw: str | None) -> frozenset[str]:
    if raw is None:
        return frozenset()
    repos = [p.strip() for p in raw.split(",") if p.strip()]
    for repo in repos:
        if not _REPO.match(repo):
            raise SettingsError("OMEGA_CI_REPOS: comma-separated owner/repo names")
    return frozenset(repo.lower() for repo in repos)


@dataclass(frozen=True)
class ControllerSettings:
    """Everything the always-on controller consumes."""

    scope: Literal["org", "repo"]
    org: str
    repo: str | None
    repos: frozenset[str]
    labels: tuple[str, ...]
    runner_group: str
    runner_group_id: int
    max_runners: int
    idle_grace_s: float
    wake_budget_s: float
    auth_mode: AuthMode
    app: AppAuth | None
    pat_file: SecretFile | None
    webhook_secret_file: SecretFile
    desktop_url: str
    desktop_token_file: SecretFile
    publisher: Literal["jsonl", "null", "eventstream"]
    events_path: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ControllerSettings:
        scope_raw = (_get(env, "OMEGA_CI_SCOPE") or "org").lower()
        if scope_raw not in ("org", "repo"):
            raise SettingsError("OMEGA_CI_SCOPE: must be org or repo")
        scope: Literal["org", "repo"] = "org" if scope_raw == "org" else "repo"
        repo = _get(env, "OMEGA_CI_REPO")
        org = _get(env, "OMEGA_CI_ORG")
        if scope == "repo":
            if not repo or not _REPO.match(repo):
                raise SettingsError("OMEGA_CI_REPO: owner/repo required for repo scope")
            org = org or repo.split("/", 1)[0]
        elif repo:
            raise SettingsError("OMEGA_CI_REPO: only valid with OMEGA_CI_SCOPE=repo")
        if not org or not _SLUG.match(org):
            raise SettingsError("OMEGA_CI_ORG: required organisation login")
        repos = _repos(_get(env, "OMEGA_CI_REPOS"))
        if scope == "repo" and repo:
            repos = frozenset({repo.lower()})
        group = _get(env, "OMEGA_CI_RUNNER_GROUP") or DEFAULT_RUNNER_GROUP
        group_id = _int(
            env, "OMEGA_CI_RUNNER_GROUP_ID", 1 if scope == "repo" else None, low=1
        )
        if group_id is None:
            raise SettingsError(
                "OMEGA_CI_RUNNER_GROUP_ID: numeric runner group id required for "
                "org scope (it is never inferred from the group name)"
            )
        max_runners = _int(
            env,
            "OMEGA_CI_MAX_RUNNERS",
            DEFAULT_MAX_RUNNERS,
            low=1,
            high=HARD_MAX_RUNNERS,
        )
        assert max_runners is not None
        auth_mode, app, pat = _select_auth(env)
        publisher = (_get(env, "OMEGA_CI_PUBLISHER") or "jsonl").lower()
        if publisher not in ("jsonl", "null", "eventstream"):
            raise SettingsError("OMEGA_CI_PUBLISHER: jsonl, null or eventstream")
        desktop_url = _get(env, "OMEGA_CI_DESKTOP_URL")
        if not desktop_url:
            raise SettingsError("OMEGA_CI_DESKTOP_URL: required https:// agent URL")
        events = _get(env, "OMEGA_CI_EVENTS_PATH")
        return cls(
            scope=scope,
            org=org,
            repo=repo,
            repos=repos,
            labels=parse_labels(_get(env, "OMEGA_CI_LABELS")),
            runner_group=group,
            runner_group_id=group_id,
            max_runners=max_runners,
            idle_grace_s=_float(
                env, "OMEGA_CI_IDLE_GRACE_S", DEFAULT_IDLE_GRACE_S, low=0, high=3600
            ),
            wake_budget_s=_float(
                env, "OMEGA_CI_WAKE_BUDGET_S", DEFAULT_WAKE_BUDGET_S, low=1, high=900
            ),
            auth_mode=auth_mode,
            app=app,
            pat_file=pat,
            webhook_secret_file=_required_secret_file(
                env, "OMEGA_CI_WEBHOOK_SECRET_FILE"
            ),
            desktop_url=_url(desktop_url, "OMEGA_CI_DESKTOP_URL", https=True),
            desktop_token_file=_required_secret_file(
                env, "OMEGA_CI_DESKTOP_TOKEN_FILE"
            ),
            publisher=publisher,  # type: ignore[arg-type]
            events_path=Path(events).expanduser()
            if events
            else DEFAULT_EVENTS_PATH.expanduser(),
        )


def _select_auth(
    env: Mapping[str, str],
) -> tuple[AuthMode, AppAuth | None, SecretFile | None]:
    present = [name for name in _APP_KEYS if _get(env, name)]
    pat = _secret_file(env, "OMEGA_CI_GITHUB_PAT_FILE")
    if present and len(present) != len(_APP_KEYS):
        missing = ", ".join(n for n in _APP_KEYS if n not in present)
        raise SettingsError(f"partial GitHub App configuration; missing {missing}")
    if present:
        if pat is not None:
            raise SettingsError(
                "OMEGA_CI_GITHUB_PAT_FILE: set either the GitHub App or a PAT file, "
                "not both (no silent fallback)"
            )
        app_id = _int(env, "OMEGA_CI_GITHUB_APP_ID", None, low=1)
        inst = _int(env, "OMEGA_CI_GITHUB_INSTALLATION_ID", None, low=1)
        assert app_id is not None and inst is not None
        key = _required_secret_file(env, "OMEGA_CI_GITHUB_APP_KEY_FILE")
        return AuthMode.APP, AppAuth(app_id, inst, key), None
    if pat is not None:
        return AuthMode.PAT, None, pat
    raise SettingsError(
        "GitHub auth: configure the GitHub App (OMEGA_CI_GITHUB_APP_ID, "
        "OMEGA_CI_GITHUB_INSTALLATION_ID, OMEGA_CI_GITHUB_APP_KEY_FILE) or "
        "OMEGA_CI_GITHUB_PAT_FILE"
    )


@dataclass(frozen=True)
class AgentSettings:
    """Everything the Desktop runner agent consumes. No GitHub credential."""

    token: SecretFile | SecretValue
    max_runners: int
    runner_root: Path
    work_root: Path
    controller_url: str
    job_uid: int | None
    job_gid: int | None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AgentSettings:
        token_file = _secret_file(env, "OMEGA_CI_AGENT_TOKEN_FILE")
        token_raw = env.get("OMEGA_CI_AGENT_TOKEN", "").strip()
        token: SecretFile | SecretValue
        if token_file is not None and token_raw:
            raise SettingsError(
                "OMEGA_CI_AGENT_TOKEN_FILE and OMEGA_CI_AGENT_TOKEN are exclusive"
            )
        if token_file is not None:
            token = token_file
        elif token_raw:
            if len(token_raw) < 32:
                raise SettingsError("OMEGA_CI_AGENT_TOKEN: at least 32 characters")
            token = SecretValue("OMEGA_CI_AGENT_TOKEN", token_raw)
        else:
            raise SettingsError(
                "OMEGA_CI_AGENT_TOKEN_FILE (or Railway OMEGA_CI_AGENT_TOKEN) required"
            )
        max_runners = _int(
            env,
            "OMEGA_CI_AGENT_MAX_RUNNERS",
            DEFAULT_MAX_RUNNERS,
            low=1,
            high=HARD_MAX_RUNNERS,
        )
        assert max_runners is not None
        controller = _get(env, "OMEGA_CI_CONTROLLER_URL")
        if not controller:
            raise SettingsError("OMEGA_CI_CONTROLLER_URL: required callback base URL")
        uid = _int(env, "OMEGA_CI_AGENT_JOB_UID", None, low=1)
        gid = _int(env, "OMEGA_CI_AGENT_JOB_GID", None, low=1)
        if (uid is None) != (gid is None):
            raise SettingsError(
                "OMEGA_CI_AGENT_JOB_UID and OMEGA_CI_AGENT_JOB_GID go together"
            )
        return cls(
            token=token,
            max_runners=max_runners,
            runner_root=Path(
                _get(env, "OMEGA_CI_AGENT_RUNNER_ROOT") or DEFAULT_AGENT_RUNNER_ROOT
            ),
            work_root=Path(
                _get(env, "OMEGA_CI_AGENT_WORK_ROOT") or DEFAULT_AGENT_WORK_ROOT
            ),
            controller_url=_url(controller, "OMEGA_CI_CONTROLLER_URL", https=False),
            job_uid=uid,
            job_gid=gid,
        )


__all__ = [
    "DEFAULT_IDLE_GRACE_S",
    "DEFAULT_LABELS",
    "DEFAULT_MAX_RUNNERS",
    "DEFAULT_WAKE_BUDGET_S",
    "HARD_MAX_RUNNERS",
    "MAX_BODY_BYTES",
    "RUNNER_NAME_RE",
    "RUNNER_PREFIX",
    "AgentSettings",
    "AppAuth",
    "AuthMode",
    "ControllerSettings",
    "SecretFile",
    "SecretValue",
    "SettingsError",
    "parse_labels",
]
