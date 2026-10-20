# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""HTTP regressions for the built and legacy dashboard assets."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import omega_prime.dashboard.app as app_module
from omega_prime.dashboard.app import create_app
from omega_prime.dashboard.poller import DashboardPoller

HOST = {"host": "127.0.0.1:7421"}
NOW = datetime(2026, 10, 21, tzinfo=UTC)


def _app(host: str = "127.0.0.1") -> object:
    poller = DashboardPoller([], clock=lambda: NOW)
    asyncio.run(poller.refresh_now())
    return create_app(poller, host=host)


def _assert_security(response: object) -> None:
    headers = response.headers
    csp = headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert "font-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "img-src 'self'" in csp
    assert "base-uri 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp.replace("style-src 'self' 'unsafe-inline'", "")
    assert "https:" not in csp and "http:" not in csp and "*" not in csp
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"


def test_default_cc_and_legacy_routes_serve_only_expected_assets() -> None:
    with TestClient(_app()) as client:
        page = client.get("/", headers=HOST)
        assert page.status_code == 200
        assert "Command Center · Omega Prime" in page.text
        assert page.headers["content-type"].startswith("text/html")
        assert page.headers["cache-control"] == "no-cache"
        _assert_security(page)
        cc_page = client.get("/cc/", headers=HOST)
        assert cc_page.text == page.text
        _assert_security(cc_page)
        redirect = client.get("/cc", headers=HOST, follow_redirects=False)
        assert redirect.status_code == 308
        assert redirect.headers["location"] == "/cc/"
        _assert_security(redirect)
        legacy = client.get("/legacy", headers=HOST)
        assert legacy.status_code == 200
        assert "Omega Prime — Task Dashboard" in legacy.text
        assert legacy.headers["cache-control"] == "no-cache"
        for path, mime in (
            ("/static/app.js", "text/javascript"),
            ("/static/app.css", "text/css"),
        ):
            response = client.get(path, headers=HOST)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(mime)

        entry_refs = re.findall(r'(?:href|src)="([^"]+)"', page.text)
        assert entry_refs
        for ref in entry_refs:
            if ref != "/legacy":
                assert ref.startswith("/cc/assets/")
                asset = client.get(ref, headers=HOST)
                assert asset.status_code == 200
                assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
                _assert_security(asset)

        asset_root = Path(app_module.__file__).resolve().parent / "static" / "cc"
        for asset in (asset_root / "assets").iterdir():
            if asset.suffix in {".js", ".css", ".woff2"}:
                response = client.get(f"/cc/assets/{asset.name}", headers=HOST)
                expected = {".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2"}[asset.suffix]
                assert response.headers["content-type"].startswith(expected)
                assert response.status_code == 200
        for notice in (asset_root / "licenses").iterdir():
            response = client.get(f"/cc/licenses/{notice.name}", headers=HOST)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/plain")
            assert "immutable" not in response.headers.get("cache-control", "")

        for path in ("/cc/assets/missing.js", "/cc/assets/index.js.map", "/cc/.hidden", "/cc/frontend/package.json", "/static/cc/assets/index.js"):
            response = client.get(path, headers=HOST)
            assert response.status_code == 404, path
            _assert_security(response)
        assert client.get("/api/missing", headers=HOST).status_code == 404


def test_decoded_traversal_and_escaping_symlinks_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dashboard = tmp_path / "dashboard"
    static = dashboard / "static"
    cc = static / "cc"
    (cc / "assets").mkdir(parents=True)
    (cc / "licenses").mkdir()
    (cc / "index.html").write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside.js"
    outside.write_text("secret", encoding="utf-8")
    (cc / "assets" / "escape.js").symlink_to(outside)
    monkeypatch.setattr(app_module, "__file__", str(dashboard / "app.py"))
    with TestClient(_app()) as client:
        for path in (
            "/cc/assets/%2e%2e/index.html",
            "/cc/assets/escape.js",
            "/cc/assets/.secret.js",
            "/cc/assets/source.js.map",
        ):
            response = client.get(path, headers=HOST)
            assert response.status_code == 404, path
            assert "secret" not in response.text
            _assert_security(response)


def test_missing_build_is_explicit_and_errors_keep_security_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dashboard = tmp_path / "missing-dashboard"
    (dashboard / "static").mkdir(parents=True)
    monkeypatch.setattr(app_module, "__file__", str(dashboard / "app.py"))
    with TestClient(_app()) as client:
        for path in ("/", "/cc/"):
            response = client.get(path, headers=HOST)
            assert response.status_code == 503
            assert 'href="/legacy"' in response.text
            assert response.headers["cache-control"] == "no-store"
            _assert_security(response)
        response = client.get("/missing", headers=HOST)
        assert response.status_code == 404
        _assert_security(response)


def test_method_host_and_ipv6_guards_cover_page_and_asset_routes() -> None:
    with TestClient(_app(host="::1")) as client:
        paths = ("/", "/cc/", "/legacy", "/cc/assets/index.js", "/api/snapshot")
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            for path in paths:
                response = client.request(method, path, headers={**HOST, "host": "evil.invalid"})
                assert response.status_code == 405, (method, path)
                _assert_security(response)
        for host in ("evil.invalid:7421", "127.0.0.1:9999", "", "127.0.0.1:7421,127.0.0.1:7421"):
            response = client.get("/", headers={"host": host})
            assert response.status_code == 403
            _assert_security(response)
        assert client.get("/", headers={"host": "[::1]:7421"}).status_code == 200
        assert client.get("/", headers={"host": "127.0.0.1:7421"}).status_code == 200


def test_duplicate_host_headers_are_rejected_with_security_headers() -> None:
    app = _app()
    starts: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start":
            starts.append(message)

    scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "headers": [
            (b"host", b"127.0.0.1:7421"),
            (b"host", b"127.0.0.1:7421"),
        ],
        "query_string": b"",
    }
    asyncio.run(app(scope, receive, send))
    assert starts[0]["status"] == 403
    headers = {key.decode(): value.decode() for key, value in starts[0]["headers"]}
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
