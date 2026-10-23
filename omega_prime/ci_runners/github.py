"""GitHub adapter: App/PAT auth, JIT runner configs, listing, idle deletion.

The only component holding GitHub credentials (D-120-02). App auth signs an
RS256 JWT with a lazily imported ``cryptography``; the installation token is
cached until five minutes before expiry and refreshed single-flight on demand
(no daemon). PAT mode is explicit; App failures never fall back to it.

Only real rate-limit responses are retried: at most 3 attempts inside a
60-second budget, honouring ``Retry-After`` / ``x-ratelimit-reset`` only when
the wait fits. Errors carry an operation name and status, never a body.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol
from urllib.parse import quote

from omega_prime.ci_runners.http import (
    HttpRequest,
    HttpResponse,
    Transport,
    TransportError,
    send,
    urllib_transport,
)
from omega_prime.ci_runners.jobs import is_pool_runner
from omega_prime.ci_runners.settings import (
    RUNNER_NAME_RE,
    AppAuth,
    AuthMode,
    ControllerSettings,
    SecretFile,
)

API = "https://api.github.com"
API_VERSION = "2022-11-28"
TOKEN_REFRESH_MARGIN_S = 300.0
MAX_ATTEMPTS = 3
RETRY_BUDGET_S = 60.0
REQUEST_TIMEOUT_S = 20.0
PAGE_SIZE = 100
MAX_PAGES = 20

Wait = Callable[[float], Awaitable[None]]
Signer = Callable[[bytes, bytes], bytes]


class GitHubError(Exception):
    """Sanitized failure: operation + status only."""

    def __init__(self, op: str, status: int | None, code: str = "http") -> None:
        self.op = op
        self.status = status
        self.code = code
        where = f"HTTP {status}" if status is not None else code
        super().__init__(f"github {op}: {where}")


class GitHubAuthError(GitHubError):
    """401/403 that is not a rate limit, or unusable credentials."""


class GitHubRateLimited(GitHubError):
    """A rate limit whose wait does not fit the remaining budget."""


class CryptoUnavailable(GitHubAuthError):
    def __init__(self) -> None:
        super().__init__("app_jwt", None, "crypto_unavailable")
        self.args = (
            "GitHub App auth needs the 'cryptography' package "
            "(pip install cryptography). PAT mode is never selected "
            "automatically; set OMEGA_CI_GITHUB_PAT_FILE and remove the App "
            "variables to use it instead.",
        )


@dataclass(frozen=True)
class JitConfig:
    runner_id: int
    name: str
    encoded: str = field(repr=False)


@dataclass(frozen=True)
class RunnerInfo:
    runner_id: int
    name: str
    online: bool
    busy: bool


def _b64url(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def rs256_sign(key_pem: bytes, data: bytes) -> bytes:
    """RS256 signature via a lazily imported ``cryptography``."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError:
        raise CryptoUnavailable from None
    try:
        key = serialization.load_pem_private_key(key_pem, password=None)
    except (ValueError, TypeError):
        raise GitHubAuthError("app_jwt", None, "bad_app_key") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise GitHubAuthError("app_jwt", None, "bad_app_key")
    return key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def app_jwt(app_id: int, key_pem: bytes, now: float, signer: Signer) -> str:
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    issued = int(now)
    claims = {"iat": issued - 60, "exp": issued + 540, "iss": str(app_id)}
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = header + b"." + payload
    return (signing_input + b"." + _b64url(signer(key_pem, signing_input))).decode()


class TokenSource(Protocol):
    async def token(self) -> str: ...

    def secrets(self) -> tuple[str, ...]: ...


class PatTokenSource:
    """Explicit PAT read from its ``*_FILE`` on first use."""

    def __init__(self, pat_file: SecretFile) -> None:
        self._file = pat_file
        self._value: str | None = None

    async def token(self) -> str:
        if self._value is None:
            self._value = self._file.read()
        return self._value

    def secrets(self) -> tuple[str, ...]:
        return (self._value,) if self._value else ()


class AppTokenSource:
    """Installation token, cached until 5 minutes before expiry, single-flight."""

    def __init__(
        self,
        app: AppAuth,
        transport: Transport,
        *,
        api: str = API,
        wall: Callable[[], float] = time.time,
        signer: Signer = rs256_sign,
    ) -> None:
        self._app = app
        self._transport = transport
        self._api = api
        self._wall = wall
        self._signer = signer
        self._token: str | None = None
        self._expires = 0.0
        self._jwt: str | None = None
        self._lock = asyncio.Lock()
        self.refreshes = 0

    def secrets(self) -> tuple[str, ...]:
        return tuple(s for s in (self._token, self._jwt) if s)

    async def token(self) -> str:
        if self._fresh():
            assert self._token is not None
            return self._token
        async with self._lock:
            if not self._fresh():
                await self._refresh()
            assert self._token is not None
            return self._token

    def _fresh(self) -> bool:
        return (
            self._token is not None
            and self._wall() < self._expires - TOKEN_REFRESH_MARGIN_S
        )

    async def _refresh(self) -> None:
        key = self._app.key_file.read().encode()
        self._jwt = app_jwt(self._app.app_id, key, self._wall(), self._signer)
        path = f"/app/installations/{self._app.installation_id}/access_tokens"
        response = await send(
            self._transport,
            HttpRequest(
                "POST",
                self._api + path,
                _headers(self._jwt),
                b"{}",
                REQUEST_TIMEOUT_S,
            ),
        )
        if response.status != 201:
            cls = GitHubAuthError if response.status in (401, 403, 404) else GitHubError
            raise cls("installation_token", response.status)
        data = _json(response, "installation_token")
        token, expires_at = data.get("token"), data.get("expires_at")
        if not isinstance(token, str) or not isinstance(expires_at, str):
            raise GitHubError("installation_token", response.status, "bad_response")
        try:
            expires = datetime.fromisoformat(expires_at).timestamp()
        except ValueError:
            raise GitHubError(
                "installation_token", response.status, "bad_response"
            ) from None
        self._token, self._expires = token, expires
        self.refreshes += 1


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "omega-ci-autoscaler",
        "Content-Type": "application/json",
    }


def _json(response: HttpResponse, op: str) -> dict[str, Any]:
    try:
        data = json.loads(response.body or b"null")
    except (UnicodeDecodeError, ValueError):
        raise GitHubError(op, response.status, "bad_response") from None
    if not isinstance(data, dict):
        raise GitHubError(op, response.status, "bad_response")
    return data


def rate_limit_wait(response: HttpResponse, wall_now: float) -> float | None:
    """Seconds to wait if this is a real rate limit, else ``None``."""
    if response.status not in (403, 429):
        return None
    retry_after = response.header("retry-after")
    remaining = response.header("x-ratelimit-remaining")
    reset = response.header("x-ratelimit-reset")
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            return None
    if remaining == "0" and reset is not None:
        try:
            return max(0.0, float(reset) - wall_now)
        except ValueError:
            return None
    if b"secondary rate limit" in response.body.lower():
        return 60.0  # GitHub: wait at least a minute when no header says otherwise
    return 429.0 if response.status == 429 else None


class GitHubClient:
    """Org- or repo-scoped self-hosted runner API."""

    def __init__(
        self,
        *,
        scope: Literal["org", "repo"],
        org: str,
        repo: str | None,
        runner_group_id: int,
        labels: Collection[str],
        auth: TokenSource,
        transport: Transport,
        api: str = API,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        wait: Wait = asyncio.sleep,
    ) -> None:
        if scope == "repo" and not repo:
            raise ValueError("repo scope needs owner/repo")
        self.base = (
            f"/orgs/{quote(org, safe='')}/actions/runners"
            if scope == "org"
            else f"/repos/{repo}/actions/runners"
        )
        self.scope = scope
        self.runner_group_id = runner_group_id
        self.labels = tuple(labels)
        self.auth = auth
        self._transport = transport
        self._api = api
        self._clock = clock
        self._wall = wall
        self._wait = wait

    @classmethod
    def from_settings(
        cls,
        settings: ControllerSettings,
        transport: Transport = urllib_transport,
        **kwargs: Any,
    ) -> GitHubClient:
        auth: TokenSource
        if settings.auth_mode is AuthMode.APP:
            assert settings.app is not None
            auth = AppTokenSource(settings.app, transport)
        else:
            assert settings.pat_file is not None
            auth = PatTokenSource(settings.pat_file)
        return cls(
            scope=settings.scope,
            org=settings.org,
            repo=settings.repo,
            runner_group_id=settings.runner_group_id,
            labels=settings.labels,
            auth=auth,
            transport=transport,
            **kwargs,
        )

    def secrets(self) -> tuple[str, ...]:
        return self.auth.secrets()

    async def _call(
        self,
        op: str,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        ok: Collection[int] = (200,),
    ) -> HttpResponse:
        deadline = self._clock() + RETRY_BUDGET_S
        payload = json.dumps(body).encode() if body is not None else None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            token = await self.auth.token()
            request = HttpRequest(
                method,
                self._api + path,
                _headers(token),
                payload,
                min(REQUEST_TIMEOUT_S, remaining),
            )
            try:
                response = await send(self._transport, request)
            except TransportError as exc:
                raise GitHubError(op, None, f"transport_{exc}") from None
            if response.status in ok:
                return response
            delay = rate_limit_wait(response, self._wall())
            if delay is None:
                cls = GitHubAuthError if response.status in (401, 403) else GitHubError
                raise cls(op, response.status)
            if attempt == MAX_ATTEMPTS or self._clock() + delay >= deadline:
                raise GitHubRateLimited(op, response.status, "rate_limited")
            await self._wait(delay)
        raise GitHubRateLimited(op, None, "budget_exhausted")

    async def generate_jit(self, name: str) -> JitConfig:
        if not RUNNER_NAME_RE.match(name):
            raise ValueError("runner name does not match the pool pattern")
        response = await self._call(
            "generate_jitconfig",
            "POST",
            f"{self.base}/generate-jitconfig",
            body={
                "name": name,
                "runner_group_id": self.runner_group_id,
                "labels": list(self.labels),
                "work_folder": "_work",
            },
            ok=(201,),
        )
        data = _json(response, "generate_jitconfig")
        runner, encoded = data.get("runner"), data.get("encoded_jit_config")
        runner_id = runner.get("id") if isinstance(runner, dict) else None
        if (
            not isinstance(encoded, str)
            or not encoded
            or not isinstance(runner_id, int)
        ):
            raise GitHubError("generate_jitconfig", response.status, "bad_response")
        return JitConfig(runner_id=runner_id, name=name, encoded=encoded)

    async def list_pool_runners(self) -> list[RunnerInfo]:
        """Startup-only listing of ``omega-ci-*`` runners (paginated, capped)."""
        found: list[RunnerInfo] = []
        for page in range(1, MAX_PAGES + 1):
            response = await self._call(
                "list_runners",
                "GET",
                f"{self.base}?per_page={PAGE_SIZE}&page={page}",
            )
            rows = _json(response, "list_runners").get("runners")
            if not isinstance(rows, list):
                raise GitHubError("list_runners", response.status, "bad_response")
            for row in rows:
                info = _runner_info(row)
                if info is not None and is_pool_runner(info.name):
                    found.append(info)
            if len(rows) < PAGE_SIZE:
                return found
        raise GitHubError("list_runners", None, "too_many_pages")

    async def get_runner(self, runner_id: int) -> RunnerInfo | None:
        response = await self._call(
            "get_runner", "GET", f"{self.base}/{int(runner_id)}", ok=(200, 404)
        )
        if response.status == 404:
            return None
        info = _runner_info(_json(response, "get_runner"))
        if info is None:
            raise GitHubError("get_runner", response.status, "bad_response")
        return info

    async def delete_runner_if_idle(self, runner_id: int) -> bool:
        """Delete only after GitHub confirms the runner is not busy.

        True when the registration is gone; False when it is busy (kept).
        """
        info = await self.get_runner(runner_id)
        if info is None:
            return True
        if info.busy:
            return False
        response = await self._call(
            "delete_runner",
            "DELETE",
            f"{self.base}/{int(runner_id)}",
            ok=(204, 404, 422),
        )
        return response.status != 422


def _runner_info(row: object) -> RunnerInfo | None:
    if not isinstance(row, dict):
        return None
    runner_id, name = row.get("id"), row.get("name")
    if not isinstance(runner_id, int) or not isinstance(name, str):
        return None
    return RunnerInfo(
        runner_id=runner_id,
        name=name,
        online=row.get("status") == "online",
        busy=row.get("busy") is True,
    )


__all__ = [
    "API",
    "AppTokenSource",
    "CryptoUnavailable",
    "GitHubAuthError",
    "GitHubClient",
    "GitHubError",
    "GitHubRateLimited",
    "JitConfig",
    "PatTokenSource",
    "RunnerInfo",
    "TokenSource",
    "app_jwt",
    "rate_limit_wait",
    "rs256_sign",
]
