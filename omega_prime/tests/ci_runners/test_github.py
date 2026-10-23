"""GitHub adapter: auth modes, JIT routes, bounded rate limits, idle delete."""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from omega_prime.ci_runners.github import (
    AppTokenSource,
    CryptoUnavailable,
    GitHubAuthError,
    GitHubClient,
    GitHubError,
    GitHubRateLimited,
    PatTokenSource,
    app_jwt,
    rate_limit_wait,
    rs256_sign,
)
from omega_prime.ci_runners.http import HttpResponse, TransportError
from omega_prime.ci_runners.settings import (
    DEFAULT_LABELS,
    GITHUB_AUDIENCE,
    AppAuth,
    ControllerSettings,
    SecretFile,
)
from omega_prime.tests.ci_runners._secrets import placeholder, write_secret

from ._fakes import FakeHttp, StaticToken, resp
from ._support import FakeClock

ORG = "/orgs/acme/actions/runners"
JIT = placeholder("jit-config")
PAT = placeholder("pat")
INSTALLATION_TOKEN = placeholder("installation-token")


def client(http: FakeHttp, clock: FakeClock, **kw: Any) -> GitHubClient:
    kw.setdefault("scope", "org")
    kw.setdefault("repo", None)
    kw.setdefault("auth", StaticToken())
    return GitHubClient(
        org="acme",
        runner_group_id=7,
        labels=DEFAULT_LABELS,
        transport=http,
        clock=clock,
        wall=clock,
        wait=clock.wait,
        **kw,
    )


def jit_reply(runner_id: int = 42) -> HttpResponse:
    return resp(201, {"runner": {"id": runner_id}, "encoded_jit_config": JIT})


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_org_jit_request_shape(clock: FakeClock) -> None:
    http = FakeHttp().on("POST", f"{ORG}/generate-jitconfig", jit_reply())
    auth = StaticToken()
    jit = run(client(http, clock, auth=auth).generate_jit("omega-ci-101-abcd"))
    assert jit.runner_id == 42 and jit.encoded == JIT and JIT not in repr(jit)
    [body] = http.bodies(f"{ORG}/generate-jitconfig")
    assert body == {
        "name": "omega-ci-101-abcd",
        "runner_group_id": 7,
        "labels": list(DEFAULT_LABELS),
        "work_folder": "_work",
    }
    headers = http.calls[0].headers
    assert headers["Authorization"] == f"Bearer {auth.value}"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_repo_scope_routes(clock: FakeClock) -> None:
    base = "/repos/acme/widgets/actions/runners"
    http = FakeHttp().on("POST", f"{base}/generate-jitconfig", jit_reply())
    gh = client(http, clock, scope="repo", repo="acme/widgets")
    run(gh.generate_jit("omega-ci-1-a"))
    assert http.paths() == [f"{base}/generate-jitconfig"]
    with pytest.raises(ValueError):
        client(http, clock, scope="repo", repo=None)


def test_jit_validation(clock: FakeClock) -> None:
    http = FakeHttp().on("POST", f"{ORG}/generate-jitconfig", resp(201, {"runner": {}}))
    gh = client(http, clock)
    with pytest.raises(ValueError):
        run(gh.generate_jit("Not A Pool Name"))
    with pytest.raises(GitHubError, match="generate_jitconfig"):
        run(gh.generate_jit("omega-ci-1-a"))
    http.on("POST", f"{ORG}/generate-jitconfig", HttpResponse(201, {}, b"not json"))
    with pytest.raises(GitHubError):
        run(gh.generate_jit("omega-ci-1-a"))


def test_auth_failure_is_sanitized_and_not_retried(clock: FakeClock) -> None:
    http = FakeHttp().on(
        "POST", f"{ORG}/generate-jitconfig", resp(401, {"message": "token ghs_leak"})
    )
    with pytest.raises(GitHubAuthError) as info:
        run(client(http, clock).generate_jit("omega-ci-1-a"))
    assert "leak" not in str(info.value) and len(http.calls) == 1
    http.on("POST", f"{ORG}/generate-jitconfig", resp(403, {"message": "no perms"}))
    with pytest.raises(GitHubAuthError):
        run(client(http, clock).generate_jit("omega-ci-1-a"))
    http.on("POST", f"{ORG}/generate-jitconfig", resp(500))
    with pytest.raises(GitHubError) as other:
        run(client(http, clock).generate_jit("omega-ci-1-a"))
    assert not isinstance(other.value, GitHubAuthError)


def test_primary_rate_limit_waits_for_reset(clock: FakeClock) -> None:
    limited = resp(
        403, {}, x_ratelimit_remaining="0", x_ratelimit_reset=str(int(clock.now) + 5)
    )
    http = FakeHttp().on("POST", f"{ORG}/generate-jitconfig", limited, jit_reply())
    run(client(http, clock).generate_jit("omega-ci-1-a"))
    assert clock.waits == [pytest.approx(5)] and len(http.calls) == 2


def test_rate_limit_attempts_capped_at_three(clock: FakeClock) -> None:
    http = FakeHttp().on(
        "POST", f"{ORG}/generate-jitconfig", resp(429, {}, retry_after="1")
    )
    with pytest.raises(GitHubRateLimited):
        run(client(http, clock).generate_jit("omega-ci-1-a"))
    assert len(http.calls) == 3 and clock.waits == [1.0, 1.0]


@pytest.mark.parametrize(
    "reply",
    [
        resp(403, {}, retry_after="120"),
        HttpResponse(
            403, {}, b'{"message":"You have exceeded a secondary rate limit"}'
        ),
        resp(429),
    ],
)
def test_waits_beyond_budget_fail_fast(clock: FakeClock, reply: HttpResponse) -> None:
    http = FakeHttp().on("POST", f"{ORG}/generate-jitconfig", reply)
    with pytest.raises(GitHubRateLimited):
        run(client(http, clock).generate_jit("omega-ci-1-a"))
    assert clock.waits == [] and len(http.calls) == 1


def test_rate_limit_wait_parsing() -> None:
    assert rate_limit_wait(resp(200), 0) is None
    assert rate_limit_wait(resp(403, {}, retry_after="soon"), 0) is None
    assert rate_limit_wait(resp(403, {}, x_ratelimit_remaining="0"), 0) is None
    bad_reset = resp(403, {}, x_ratelimit_remaining="0", x_ratelimit_reset="x")
    assert rate_limit_wait(bad_reset, 0) is None
    past = resp(403, {}, x_ratelimit_remaining="0", x_ratelimit_reset="5")
    assert rate_limit_wait(past, 10) == 0.0


def test_budget_exhaustion_without_requests(clock: FakeClock) -> None:
    def slow(_req: Any) -> HttpResponse:
        clock.advance(59.5)
        return resp(429, {}, retry_after="0.1")

    http = FakeHttp().on("POST", f"{ORG}/generate-jitconfig", slow)
    with pytest.raises(GitHubRateLimited):
        run(client(http, clock).generate_jit("omega-ci-1-a"))
    # The 0.1 s wait fits; the second request's timeout is clamped to what is
    # left of the 60 s budget, and nothing starts after it is spent.
    assert len(http.calls) == 2 and http.calls[1].timeout < 0.5


def test_transport_error(clock: FakeClock) -> None:
    http = FakeHttp().on("GET", f"{ORG}/5", TransportError("TimeoutError"))
    with pytest.raises(GitHubError, match="transport_TimeoutError"):
        run(client(http, clock).get_runner(5))


def _row(i: int, name: str, busy: bool = False) -> dict[str, Any]:
    return {"id": i, "name": name, "status": "online", "busy": busy}


def test_list_paginates_and_filters(clock: FakeClock) -> None:
    page1 = [_row(i, f"omega-ci-{i}-aaaa") for i in range(1, 99)]
    page1 += [_row(99, "vps-runner"), {"id": "x"}]
    page2 = [_row(200, "omega-ci-200-bbbb", busy=True)]

    def pages(req: Any) -> HttpResponse:
        return resp(200, {"runners": page1 if req.url.endswith("&page=1") else page2})

    http = FakeHttp().on("GET", ORG, pages)
    found = run(client(http, clock).list_pool_runners())
    assert len(found) == 99 and found[-1].busy and len(http.calls) == 2
    assert all(f.name.startswith("omega-ci-") for f in found)


def test_list_errors(clock: FakeClock) -> None:
    full = [_row(i, f"omega-ci-{i}-a") for i in range(100)]
    http = FakeHttp().on("GET", ORG, resp(200, {"runners": full}))
    with pytest.raises(GitHubError, match="too_many_pages"):
        run(client(http, clock).list_pool_runners())
    http.on("GET", ORG, resp(200, {"runners": "nope"}))
    with pytest.raises(GitHubError):
        run(client(http, clock).list_pool_runners())


def test_delete_only_when_idle(clock: FakeClock) -> None:
    http = FakeHttp().on(
        "GET", f"{ORG}/5", resp(200, _row(5, "omega-ci-5-a", busy=True))
    )
    gh = client(http, clock)
    assert run(gh.delete_runner_if_idle(5)) is False
    assert http.paths("DELETE") == []
    http.on("GET", f"{ORG}/5", resp(200, _row(5, "omega-ci-5-a")))
    http.on("DELETE", f"{ORG}/5", resp(204))
    assert run(gh.delete_runner_if_idle(5)) is True
    http.on("DELETE", f"{ORG}/5", resp(422))
    assert run(gh.delete_runner_if_idle(5)) is False
    http.on("GET", f"{ORG}/5", resp(404))
    assert run(gh.delete_runner_if_idle(5)) is True
    http.on("GET", f"{ORG}/5", resp(200, {"id": 5}))
    with pytest.raises(GitHubError):
        run(gh.get_runner(5))


# --- auth sources ---------------------------------------------------------------


def test_pat_source(tmp_path: Path) -> None:
    write_secret(tmp_path / "pat", PAT)
    source = PatTokenSource(
        SecretFile("OMEGA_CI_GITHUB_PAT_FILE", tmp_path / "pat", GITHUB_AUDIENCE)
    )
    assert source.secrets() == ()
    assert run(source.token()) == PAT
    assert source.secrets() == (PAT,)


def _app(tmp_path: Path) -> AppAuth:
    write_secret(tmp_path / "key.pem", "-----BEGIN FAKE KEY-----")
    return AppAuth(
        123,
        456,
        SecretFile(
            "OMEGA_CI_GITHUB_APP_KEY_FILE", tmp_path / "key.pem", GITHUB_AUDIENCE
        ),
    )


TOKEN_PATH = "/app/installations/456/access_tokens"


def _token_reply(expires: str = "2026-10-23T01:00:00Z") -> HttpResponse:
    return resp(201, {"token": INSTALLATION_TOKEN, "expires_at": expires})


def test_app_token_single_flight_and_refresh_margin(tmp_path: Path) -> None:
    from datetime import datetime

    expires = datetime.fromisoformat("2026-10-23T01:00:00+00:00").timestamp()
    wall = FakeClock(expires - 3600)
    http = FakeHttp().on("POST", TOKEN_PATH, _token_reply())
    source = AppTokenSource(_app(tmp_path), http, wall=wall, signer=lambda k, d: b"sig")

    async def burst() -> list[str]:
        return await asyncio.gather(*(source.token() for _ in range(8)))

    assert set(run(burst())) == {INSTALLATION_TOKEN}
    assert source.refreshes == 1
    jwt = http.calls[0].headers["Authorization"].removeprefix("Bearer ")
    assert jwt.count(".") == 2 and jwt in source.secrets()
    wall.now = expires - 301
    run(source.token())
    assert source.refreshes == 1
    wall.now = expires - 299
    run(source.token())
    assert source.refreshes == 2


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (resp(401), GitHubAuthError),
        (resp(404), GitHubAuthError),
        (resp(500), GitHubError),
        (resp(201, {"token": 1}), GitHubError),
        (resp(201, {"token": "t", "expires_at": "tomorrow"}), GitHubError),
        (HttpResponse(201, {}, b"[]"), GitHubError),
    ],
)
def test_app_token_failures_never_fall_back(
    tmp_path: Path, reply: HttpResponse, error: type[Exception]
) -> None:
    http = FakeHttp().on("POST", TOKEN_PATH, reply)
    source = AppTokenSource(_app(tmp_path), http, signer=lambda k, d: b"sig")
    with pytest.raises(error):
        run(source.token())
    assert http.paths() == [TOKEN_PATH]


def test_app_jwt_claims() -> None:
    token = app_jwt(123, b"key", 1_000_000, lambda k, d: b"signature")
    header, payload, sig = token.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    claims = json.loads(base64.urlsafe_b64decode(pad(payload)))
    assert json.loads(base64.urlsafe_b64decode(pad(header)))["alg"] == "RS256"
    assert claims == {"iat": 999_940, "exp": 1_000_540, "iss": "123"}
    assert base64.urlsafe_b64decode(pad(sig)) == b"signature"


def test_missing_cryptography_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    for mod in list(sys.modules):
        if mod == "cryptography" or mod.startswith("cryptography."):
            monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setitem(sys.modules, "cryptography", None)
    with pytest.raises(CryptoUnavailable) as info:
        rs256_sign(b"-----BEGIN KEY-----secret", b"data")
    assert "pip install cryptography" in str(info.value)
    assert "secret" not in str(info.value)


def test_real_rs256_signature() -> None:
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    sig = rs256_sign(pem, b"payload")
    key.public_key().verify(sig, b"payload", padding.PKCS1v15(), hashes.SHA256())
    ec_pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    with pytest.raises(GitHubAuthError, match="bad_app_key"):
        rs256_sign(ec_pem, b"x")
    with pytest.raises(GitHubAuthError, match="bad_app_key"):
        rs256_sign(b"garbage", b"x")


def test_from_settings_selects_mode(tmp_path: Path) -> None:
    env = {
        "OMEGA_CI_ORG": "acme",
        "OMEGA_CI_RUNNER_GROUP_ID": "7",
        "OMEGA_CI_WEBHOOK_SECRET_FILE": str(tmp_path / "h"),
        "OMEGA_CI_DESKTOP_URL": "https://d.example.test",
        "OMEGA_CI_DESKTOP_TOKEN_FILE": str(tmp_path / "d"),
    }
    pat = ControllerSettings.from_env(env | {"OMEGA_CI_GITHUB_PAT_FILE": "/p"})
    assert isinstance(GitHubClient.from_settings(pat, FakeHttp()).auth, PatTokenSource)
    app = ControllerSettings.from_env(
        env
        | {
            "OMEGA_CI_GITHUB_APP_ID": "1",
            "OMEGA_CI_GITHUB_INSTALLATION_ID": "2",
            "OMEGA_CI_GITHUB_APP_KEY_FILE": "/k",
        }
    )
    gh = GitHubClient.from_settings(app, FakeHttp())
    assert isinstance(gh.auth, AppTokenSource) and gh.secrets() == ()


def test_import_does_not_load_optional_packages() -> None:
    code = (
        "import sys, omega_prime.ci_runners.github, omega_prime.ci_runners.app;"
        "print(any(m.split('.')[0] in {'cryptography', 'uvicorn'} for m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"


def test_app_token_connection_failure_is_a_github_error(tmp_path: Path) -> None:
    http = FakeHttp().on("POST", TOKEN_PATH, TransportError("URLError"))
    source = AppTokenSource(_app(tmp_path), http, signer=lambda k, d: b"sig")
    with pytest.raises(GitHubError) as info:
        run(source.token())
    assert info.value.code == "transport_URLError" and info.value.status is None


@pytest.mark.parametrize("make_source", ["app", "pat"])
def test_unreadable_credentials_are_github_auth_errors(
    tmp_path: Path, make_source: str
) -> None:
    missing = SecretFile("OMEGA_CI_X_FILE", tmp_path / "absent", GITHUB_AUDIENCE)
    source: Any = (
        AppTokenSource(AppAuth(1, 2, missing), FakeHttp(), signer=lambda k, d: b"s")
        if make_source == "app"
        else PatTokenSource(missing)
    )
    with pytest.raises(GitHubAuthError) as info:
        run(source.token())
    assert info.value.code == "credential_unreadable"
