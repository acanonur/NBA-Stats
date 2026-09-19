"""Two pure-ASGI middlewares that protect the browser surface: security response headers, and
a guard against a spoofed reverse-proxy header.

Both are plain ASGI callables — never ``starlette.middleware.base.BaseHTTPMiddleware`` — for the
exact reason ``nbastats/api/errors.py`` already gives ``RequestIdMiddleware``:
``BaseHTTPMiddleware`` buffers the whole response and hides a client disconnect from anything
inside it, which would break ``GET /v1/sync/stream``'s long-lived connection. A middleware that
only ever needs to add headers to a normal response has no excuse to pay that cost, so both of
these speak the raw ASGI three-callable protocol instead.

Why a CSP this strict is affordable here
--------------------------------------------
``script-src 'self'`` and ``style-src 'self'`` carry **no** ``'unsafe-inline'`` and no nonce
machinery, which is normally the hard part of writing a CSP. It is free here because
``web/vite.config.ts`` builds with ``build.modulePreload.polyfill = false`` and nothing under
``web/src`` uses ``dangerouslySetInnerHTML`` (an ESLint rule forbids it) — the bundle simply
never emits an inline ``<script>`` or a ``style`` attribute a strict policy would have to carve
an exception for. ``form-action`` names Apple's and Google's own domains and nothing else,
because the only cross-origin form submissions this app ever makes are the two OAuth
authorization redirects.

Why both middlewares sit *just inside* ``RequestIdMiddleware``
--------------------------------------------------------------------
``app.py`` adds middleware in this order: CORS, then ``SecurityHeadersMiddleware``, then
``ProxyHeadersGuard``, then ``RequestIdMiddleware`` — and because each ``add_middleware`` call
wraps the ones already registered, the *last* call ends up *outermost*, exactly as the comment
beside that call already says ("added last, so it is outermost: every response, error envelopes
included, gets an id"). So a request actually passes through, outside in:
``RequestIdMiddleware`` (every response, including an error envelope, carries an id) ->
``ProxyHeadersGuard`` (strip a forged proxy header before anything further in — the rate
limiter, ``accounts.sessions.ip_prefix_of`` — can trust ``request.client`` or a forwarded
header) -> ``SecurityHeadersMiddleware`` (every response below this point, error envelopes
included, gets the header set) -> CORS -> routing. Nothing about the header set depends on
whether the response is a success or one of ``errors.py``'s envelopes, which is the point of
sitting this close to the outside: an attacker's most useful target is often the *error* page,
and this makes sure it is never the one response that slipped through unprotected.
"""
from __future__ import annotations

import ipaddress
from typing import Any, Awaitable, Callable

from starlette.datastructures import MutableHeaders

from ..accounts.config import get_auth_settings

__all__ = ["SecurityHeadersMiddleware", "ProxyHeadersGuard", "security_headers_for"]

#: Paths (exact match or prefix) whose responses must never be cached by a shared or browser
#: cache — they carry a session's own state, not public data. ``/v1/auth/session`` is checked
#: for equality; ``/v1/me`` and ``/v1/dashboards`` are prefixes, so ``/v1/me/sessions``,
#: ``/v1/me/export``, ``/v1/dashboards/{id}`` and ``/v1/dashboards/export`` are covered without
#: naming each one.
#:
#: ``/v1/dashboards`` was missing, and every response under it — including the whole-account
#: ``Layouts.json`` export — went out with no ``Cache-Control``, no ``Expires`` and no ``Vary``,
#: which RFC 9111 §4.2.2 makes heuristically cacheable; ``GET /v1/dashboards/{id}`` even ships
#: an ``ETag`` inviting exactly that. On the deployment shape §5 and §12.5 contemplate (an https
#: reverse proxy in front of one worker, every user on one origin) that is one user's saved
#: dashboards replayed to the next on the same URL, and single-user it is the export sitting in
#: the browser's on-disk cache after sign-out.
_NO_STORE_EXACT = frozenset({"/v1/auth/session"})
_NO_STORE_PREFIXES = ("/v1/me", "/v1/dashboards")

#: Headers a forged reverse-proxy hop could use to lie about the caller's address or scheme.
#: Stripped from the ASGI scope wholesale when the immediate TCP peer is not a trusted proxy —
#: see ``ProxyHeadersGuard``.
_FORWARDED_HEADER_NAMES = frozenset(
    {b"x-forwarded-for", b"x-forwarded-proto", b"x-forwarded-host", b"forwarded"}
)


def _no_store_for(path: str) -> bool:
    return path in _NO_STORE_EXACT or any(path.startswith(prefix) for prefix in _NO_STORE_PREFIXES)


def security_headers_for(path: str) -> list[tuple[bytes, bytes]]:
    """The header set for one response, as raw ASGI ``(name, value)`` byte pairs.

    A plain function (rather than logic buried in the middleware's ``__call__``) so
    ``test_security_headers.py`` can assert the exact header set for a path without spinning up
    an ASGI app, and so a future caller that is not a middleware — a hand-built error response,
    say — can still get the same headers.
    """
    settings = get_auth_settings()
    img_src = " ".join(("'self'", "data:", *settings.csp_img_src))
    csp = "; ".join(
        (
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            f"img-src {img_src}",
            "font-src 'self'",
            "connect-src 'self'",
            "form-action 'self' https://appleid.apple.com https://accounts.google.com",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "object-src 'none'",
        )
    )
    headers = [
        (b"content-security-policy", csp.encode("latin-1")),
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"cross-origin-opener-policy", b"same-origin"),
        (b"permissions-policy", b"geolocation=(), camera=(), microphone=()"),
    ]
    if settings.public_base_url.startswith("https://"):
        headers.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains"))
    if _no_store_for(path):
        headers.append((b"cache-control", b"no-store"))
        # Belt and braces for any cache that ignores `no-store` on a 200 with a validator: the
        # response varies by who is signed in, and the only thing that says who that is is the
        # session cookie.
        headers.append((b"vary", b"Cookie"))
    return headers


class SecurityHeadersMiddleware:
    """Adds the header set from :func:`security_headers_for` to every HTTP response.

    Pure ASGI — see the module docstring for why ``BaseHTTPMiddleware`` is the wrong base here.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[Any]],
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def send_with_headers(message: Any) -> None:
            if message["type"] == "http.response.start":
                mutable = MutableHeaders(scope=message)
                for name, value in security_headers_for(path):
                    mutable[name.decode("latin-1")] = value.decode("latin-1")
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _is_trusted_peer(client_host: str | None, cidrs: tuple[str, ...]) -> bool:
    """True when ``client_host`` — the immediate TCP peer, never a header — falls inside one of
    ``cidrs``. An empty ``cidrs`` (the default) makes every peer untrusted, which is what makes
    "strip forwarded headers" the safe default described in the module docstring."""
    if not client_host or not cidrs:
        return False
    try:
        address = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            if address in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


class ProxyHeadersGuard:
    """Strips ``X-Forwarded-For`` / ``-Proto`` / ``-Host`` / ``Forwarded`` from the ASGI scope
    unless the immediate peer is inside :attr:`AuthSettings.trusted_proxy_cidrs`.

    Without this, any direct caller — not just a configured reverse proxy — could set
    ``X-Forwarded-For`` itself and forge the address ``deps.py``'s rate limiter and
    ``accounts.sessions.ip_prefix_of`` key on, turning a per-IP brake into no brake at all. The
    default (``trusted_proxy_cidrs == ()``) strips these headers from every request, which is
    the only safe default for a service that, per ``WEB_DESIGN.md``, normally runs with no proxy
    in front of it at all.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[Any]],
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_host = client[0] if client else None
        settings = get_auth_settings()
        if not _is_trusted_peer(client_host, settings.trusted_proxy_cidrs):
            headers = scope.get("headers") or ()
            filtered = [
                (key, value) for key, value in headers if key.lower() not in _FORWARDED_HEADER_NAMES
            ]
            if len(filtered) != len(headers):
                scope = {**scope, "headers": filtered}

        await self.app(scope, receive, send)
