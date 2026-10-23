"""Validated controller and agent settings (the one settings convention).

Secrets are referenced by file path only (``*_FILE``); the single exception is
the Railway-only ``OMEGA_CI_AGENT_TOKEN`` variable on the agent. Secret values
are read on demand, never stored in a repr, and never logged.

Every read resolves through :class:`omega_prime.credentials.CredentialBroker`
(the repository's one credential path): each secret carries the audience URL
it is presented to, the broker's policy approves only that host, and the
broker sees a filtered environment holding just that secret's ``*_FILE`` path
(or the Railway variable). The broker's file rule applies: a regular,
non-empty file with mode 0600 (no group/other bits), owned by the service.

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
DEFAULT_DEMAND_PATH = Path("~/.omega/ci-runners/demand.json")
DEFAULT_AGENT_RUNNER_ROOT = Path("/opt/actions-runner")
DEFAULT_AGENT_WORK_ROOT = Path("/config/ci-runners")
# Root-owned allowlist the pre-job hook reads (deploy/ci-runner-desktop/job-started.sh).
DEFAULT_AGENT_JOB_POLICY = Path("/var/lib/omega-ci/job-policy.json")

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


GITHUB_AUDIENCE = "https://api.github.com"
WEBHOOK_AUDIENCE = "https://github.com"  # GitHub signs deliveries with it


class _AudiencePolicy:
    """Broker policy whose only approved host is the secret's audience."""

    def __init__(self, host: str) -> None:
        self._host = host

    def allows_host(self, host: str) -> bool:
        return bool(self._host) and host == self._host


@dataclass(frozen=True)
class _Credential:
    """Provider-shaped shim: the one env var the broker may resolve."""

    name: str
    env_vars: tuple[str, ...]
    requires_key: bool = True


def _broker_read(env_name: str, var: str, env: Mapping[str, str], audience: str) -> str:
    from omega_prime.credentials.broker import CredentialBroker
    from omega_prime.providers.base import ProviderError

    host = (urlsplit(audience).hostname or "").lower()
    broker = CredentialBroker(_AudiencePolicy(host), env)
    try:
        return broker.key_for(_Credential(env_name, (var,)), audience)
    except ProviderError:
        # The broker's message names variables only; ours does too.
        raise SettingsError(
            f"{env_name}: secret file unreadable, empty or not mode 0600"
        ) from None


@dataclass(frozen=True)
class SecretFile:
    """Reference to a file that holds one secret. The value is read on demand,
    through the credential broker, for the one ``audience`` it is sent to."""

    env_name: str
    path: Path
    audience: str

    def read(self) -> str:
        var = self.env_name.removesuffix("_FILE")
        env = {f"{var}_FILE": str(self.path)}  # *_FILE only: never a raw value
        return _broker_read(self.env_name, var, env, self.audience)

    def __repr__(self) -> str:
        return f"SecretFile({self.env_name}, path={str(self.path)!r})"


@dataclass(frozen=True)
class SecretValue:
    """An in-memory secret (Railway variable). ``repr`` never shows it."""

    env_name: str
    _value: str = field(repr=False)
    audience: str = ""

    def read(self) -> str:
        env = {self.env_name: self._value}
        return _broker_read(self.env_name, self.env_name, env, self.audience)

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


def _secret_file(env: Mapping[str, str], name: str, audience: str) -> SecretFile | None:
    raw = _get(env, name)
    return SecretFile(name, Path(raw).expanduser(), audience) if raw else None


def _required_secret_file(
    env: Mapping[str, str], name: str, audience: str
) -> SecretFile:
    ref = _secret_file(env, name, audience)
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


def _repos(raw: str | None, name: str = "OMEGA_CI_REPOS") -> frozenset[str]:
    if raw is None:
        return frozenset()
    repos = [p.strip() for p in raw.split(",") if p.strip()]
    for repo in repos:
        if not _REPO.match(repo):
            raise SettingsError(f"{name}: comma-separated owner/repo names")
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
    demand_path: Path = DEFAULT_DEMAND_PATH
    # Public repositories explicitly opted in (subset of ``repos``). Their jobs
    # still pass the fork guard (see :mod:`omega_prime.ci_runners.forkguard`).
    public_repos: frozenset[str] = frozenset()

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
        public_repos = _repos(
            _get(env, "OMEGA_CI_PUBLIC_REPOS"), "OMEGA_CI_PUBLIC_REPOS"
        )
        if public_repos and not (repos and public_repos <= repos):
            raise SettingsError(
                "OMEGA_CI_PUBLIC_REPOS: every public repo must also be listed in "
                "OMEGA_CI_REPOS (an empty OMEGA_CI_REPOS cannot opt public repos in)"
            )
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
        desktop = _url(desktop_url, "OMEGA_CI_DESKTOP_URL", https=True)
        events = _get(env, "OMEGA_CI_EVENTS_PATH")
        demand = _get(env, "OMEGA_CI_DEMAND_PATH")
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
                env, "OMEGA_CI_WEBHOOK_SECRET_FILE", WEBHOOK_AUDIENCE
            ),
            desktop_url=desktop,
            desktop_token_file=_required_secret_file(
                env, "OMEGA_CI_DESKTOP_TOKEN_FILE", desktop
            ),
            publisher=publisher,  # type: ignore[arg-type]
            events_path=Path(events).expanduser()
            if events
            else DEFAULT_EVENTS_PATH.expanduser(),
            demand_path=Path(demand).expanduser()
            if demand
            else DEFAULT_DEMAND_PATH.expanduser(),
            public_repos=public_repos,
        )


def _select_auth(
    env: Mapping[str, str],
) -> tuple[AuthMode, AppAuth | None, SecretFile | None]:
    present = [name for name in _APP_KEYS if _get(env, name)]
    pat = _secret_file(env, "OMEGA_CI_GITHUB_PAT_FILE", GITHUB_AUDIENCE)
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
        key = _required_secret_file(
            env, "OMEGA_CI_GITHUB_APP_KEY_FILE", GITHUB_AUDIENCE
        )
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
    # Repositories the runner-side pre-job hook lets run (empty → none).
    job_repos: frozenset[str] = frozenset()
    job_hook: Path | None = None
    job_policy: Path = DEFAULT_AGENT_JOB_POLICY

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AgentSettings:
        # The agent token is presented to the controller (exit callbacks).
        audience = _get(env, "OMEGA_CI_CONTROLLER_URL") or ""
        token_file = _secret_file(env, "OMEGA_CI_AGENT_TOKEN_FILE", audience)
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
            token = SecretValue("OMEGA_CI_AGENT_TOKEN", token_raw, audience)
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
            job_repos=_repos(_get(env, "OMEGA_CI_AGENT_REPOS"), "OMEGA_CI_AGENT_REPOS"),
            job_hook=_absolute(
                _get(env, "OMEGA_CI_AGENT_JOB_HOOK"), "OMEGA_CI_AGENT_JOB_HOOK"
            ),
            job_policy=_absolute(
                _get(env, "OMEGA_CI_AGENT_JOB_POLICY"), "OMEGA_CI_AGENT_JOB_POLICY"
            )
            or DEFAULT_AGENT_JOB_POLICY,
        )


def _absolute(raw: str | None, name: str) -> Path | None:
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise SettingsError(f"{name}: absolute path required")
    return path


__all__ = [
    "DEFAULT_AGENT_JOB_POLICY",
    "DEFAULT_DEMAND_PATH",
    "DEFAULT_IDLE_GRACE_S",
    "DEFAULT_LABELS",
    "DEFAULT_MAX_RUNNERS",
    "DEFAULT_WAKE_BUDGET_S",
    "GITHUB_AUDIENCE",
    "HARD_MAX_RUNNERS",
    "MAX_BODY_BYTES",
    "RUNNER_NAME_RE",
    "RUNNER_PREFIX",
    "WEBHOOK_AUDIENCE",
    "AgentSettings",
    "AppAuth",
    "AuthMode",
    "ControllerSettings",
    "SecretFile",
    "SecretValue",
    "SettingsError",
    "parse_labels",
]
