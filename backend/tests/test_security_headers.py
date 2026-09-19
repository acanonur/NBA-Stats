"""``nbastats.api.security`` — the CSP/security header set and the forwarded-header guard.

These exercise :func:`security_headers_for` and :class:`ProxyHeadersGuard` directly wherever
possible (fast, no ASGI plumbing needed) and the live app for the "every response carries these"
claim, since that is a property of ``app.py`` wiring the middleware in, not of the header
function alone.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nbastats.accounts import config as auth_config
from nbastats.api import security
from nbastats.api.app import create_app


@pytest.fixture(autouse=True)
def _reset_auth_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    auth_config.reset_auth_settings_cache()
    yield
    auth_config.reset_auth_settings_cache()


def test_default_headers_carry_a_strict_csp_with_no_unsafe_inline() -> None:
    headers = dict(security.security_headers_for("/v1/health"))
    csp = headers[b"content-security-policy"].decode()
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self' https://appleid.apple.com https://accounts.google.com" in csp
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"x-frame-options"] == b"DENY"
    assert headers[b"referrer-policy"] == b"no-referrer"
    assert headers[b"cross-origin-opener-policy"] == b"same-origin"
    assert headers[b"permissions-policy"] == b"geolocation=(), camera=(), microphone=()"


def test_csp_img_src_includes_configured_extra_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARDWOOD_CSP_IMG_SRC", "https://images.example,https://other.example")
    auth_config.reset_auth_settings_cache()
    headers = dict(security.security_headers_for("/v1/health"))
    csp = headers[b"content-security-policy"].decode()
    assert "img-src 'self' data: https://images.example https://other.example" in csp


def test_hsts_only_appears_on_an_https_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = dict(security.security_headers_for("/v1/health"))
    assert b"strict-transport-security" not in headers  # default base url is http://127.0.0.1

    monkeypatch.setenv("HARDWOOD_PUBLIC_BASE_URL", "https://hardwood.example")
    auth_config.reset_auth_settings_cache()
    headers = dict(security.security_headers_for("/v1/health"))
    assert headers[b"strict-transport-security"] == b"max-age=63072000; includeSubDomains"


def test_no_store_on_auth_session_and_every_me_path() -> None:
    assert dict(security.security_headers_for("/v1/auth/session"))[b"cache-control"] == b"no-store"
    assert dict(security.security_headers_for("/v1/me"))[b"cache-control"] == b"no-store"
    assert dict(security.security_headers_for("/v1/me/sessions"))[b"cache-control"] == b"no-store"
    assert b"cache-control" not in dict(security.security_headers_for("/v1/health"))


def test_live_app_responses_carry_the_headers() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/v1/health")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "content-security-policy" in response.headers
    assert response.headers.get("cache-control") != "no-store"


def test_live_app_error_responses_also_carry_the_headers() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in response.headers


# --------------------------------------------------------------------------- ProxyHeadersGuard


def _scope(client_host: str | None, headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    return {
        "type": "http",
        "path": "/v1/health",
        "client": (client_host, 12345) if client_host else None,
        "headers": headers,
    }


async def _run_guard(scope: dict[str, Any]) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def inner_app(inner_scope: dict[str, Any], receive: Any, send: Any) -> None:
        captured["scope"] = inner_scope

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(_message: Any) -> None:
        pass

    guard = security.ProxyHeadersGuard(inner_app)
    await guard(scope, receive, send)
    return captured["scope"]


async def test_forwarded_headers_are_stripped_from_an_untrusted_peer() -> None:
    auth_config.reset_auth_settings_cache()
    scope = _scope(
        "203.0.113.9",
        [(b"x-forwarded-for", b"1.2.3.4"), (b"x-api-key", b"secret")],
    )
    result = await _run_guard(scope)
    names = {key for key, _ in result["headers"]}
    assert b"x-forwarded-for" not in names
    assert b"x-api-key" in names  # unrelated headers are untouched


async def test_forwarded_headers_survive_from_a_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HARDWOOD_TRUSTED_PROXY_CIDRS", "203.0.113.0/24")
    auth_config.reset_auth_settings_cache()
    scope = _scope("203.0.113.9", [(b"x-forwarded-for", b"1.2.3.4")])
    result = await _run_guard(scope)
    names = {key for key, _ in result["headers"]}
    assert b"x-forwarded-for" in names
