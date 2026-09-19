"""CSRF defence for every unsafe, session-authenticated request — a synchroniser token
checked against the session row, plus an ``Origin``/``Sec-Fetch-Site`` check. There is no CSRF
cookie anywhere in this design.

Why not the usual double-submit cookie
------------------------------------------
The standard "double-submit cookie" pattern (send a token in a cookie *and* a header/body
field, reject a request where they disagree) relies on cookies being scoped the same way an
origin is. They are not: cookies are scoped by **host**, not by **port**, and Hardwood Web's
own local-development story is ``http://127.0.0.1:8000`` with the API and the SPA on the same
origin. Any *other* process listening on ``127.0.0.1`` on any other port — another dev server,
a container's exposed port, literally anything — can set a cookie for that host that a browser
will happily attach to a request aimed at us, defeating a double-submit check outright. A
synchroniser token has no such hole: it is compared against ``auth_sessions.csrf_hash``, a
value an attacker who cannot read our database cannot produce, and it never travels as a
cookie at all — see ``accounts/sessions.py::csrf_token_for`` for how the client gets hold of it
(in the JSON body of ``GET /v1/auth/session``, held in memory, never in storage).

Why ``Origin`` is checked too, not just the token
------------------------------------------------------
The token check alone is already sufficient against a *cross-site* forged request — a hostile
page has no way to read our session row's hash. ``check_origin`` is defence in depth against a
different bug class: a same-site subdomain or a compromised piece of first-party JavaScript
making a request the token check alone would happily wave through, since it presumably also
has access to the in-memory token. Comparing the *parsed* ``(scheme, host, port)`` triple,
never the raw string, matters for a boring but real reason: a trailing slash or a stray path
on ``HARDWOOD_PUBLIC_BASE_URL`` (a config typo) must not make every single write request in
the service fail with ``csrf_failed`` — a string-equality check would do exactly that.
"""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import SplitResult, urlsplit

from fastapi import Request

from ..api.errors import ApiError
from .config import LOOPBACK_HOSTS, get_auth_settings
from .models import AuthSession
from .sessions import current_session

__all__ = ["CSRF_HEADER", "check_origin", "check_csrf", "require_write"]

CSRF_HEADER = "X-Hardwood-CSRF"


def _csrf_failed() -> ApiError:
    return ApiError(
        "csrf_failed",
        "This request could not be verified. Reload the page and try again.",
        http_status=403,
        recoverable=True,
    )


def check_origin(request: Request) -> None:
    """Raise ``csrf_failed`` unless the request is same-origin.

    If ``Origin`` is present, it must equal :attr:`AuthSettings.public_origin` when both are
    parsed into ``(scheme, host, port)`` triples. If ``Origin`` is absent (older browsers, or a
    same-origin request some browsers omit it for), ``Sec-Fetch-Site`` must say
    ``same-origin`` or ``none``. If both headers are absent, the request is rejected — a
    browser new enough to enforce ``SameSite`` cookies is new enough to send at least one of
    these on every request that matters.
    """
    origin = request.headers.get("origin")
    if origin:
        settings = get_auth_settings()
        parsed = urlsplit(origin)
        expected = urlsplit(settings.public_origin)
        if not _same_origin(parsed, expected, loopback=settings.is_loopback):
            raise _csrf_failed()
        return
    sec_fetch_site = request.headers.get("sec-fetch-site")
    if sec_fetch_site in ("same-origin", "none"):
        return
    raise _csrf_failed()


def _same_origin(parsed: SplitResult, expected: SplitResult, *, loopback: bool) -> bool:
    """``(scheme, host, port)`` equality — with one deliberate widening.

    On a **loopback** deployment the host halves are compared as members of
    :data:`~accounts.config.LOOPBACK_HOSTS` rather than as strings, so a browser pointed at
    ``http://localhost:8000`` is accepted by a service whose ``HARDWOOD_PUBLIC_BASE_URL`` is the
    default ``http://127.0.0.1:8000`` (and vice versa). Without this, the single most likely
    thing a person types makes *every* write in the product fail with
    ``"This request could not be verified. Reload the page and try again."`` — advice that can
    never work, because reloading the same URL sends the same ``Origin``.

    This gives up nothing: ``config.py`` already treats the three loopback spellings as one host
    everywhere else (``is_loopback`` gates the http-in-the-clear refusal, the dev-link rule and
    the ``open`` signup backstop), cookies are not port-scoped so a hostile process on
    ``127.0.0.1`` is *already* same-site with one on ``localhost``, and the CSRF synchroniser
    token — which such a process cannot read — is what actually stops it. A **non-loopback**
    deployment keeps exact host matching, where the distinction is real.
    """
    if (parsed.scheme, parsed.port) != (expected.scheme, expected.port):
        return False
    parsed_host = (parsed.hostname or "").lower()
    expected_host = (expected.hostname or "").lower()
    if parsed_host == expected_host:
        return True
    return loopback and parsed_host in LOOPBACK_HOSTS and expected_host in LOOPBACK_HOSTS


def check_csrf(request: Request, row: AuthSession) -> None:
    """Raise ``csrf_failed`` unless ``sha256(X-Hardwood-CSRF header)`` matches ``row.csrf_hash``
    — compared with :func:`hmac.compare_digest`, never ``==``, and always against the *session
    row*, never a cookie (see the module docstring)."""
    supplied = request.headers.get(CSRF_HEADER)
    if not supplied:
        raise _csrf_failed()
    supplied_hash = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(supplied_hash, row.csrf_hash):
        raise _csrf_failed()


def require_write(request: Request) -> AuthSession:
    """The dependency every unsafe (state-changing) session-authenticated route depends on:
    resolves the live session (running :func:`~accounts.sessions.current_session` if some
    earlier dependency has not already), then enforces :func:`check_origin` and
    :func:`check_csrf` in that order, and returns the session row so the route does not have to
    look it up again.
    """
    row = current_session(request)
    if row is None:
        raise ApiError(
            "unauthorized", "Sign in to continue.", http_status=401, recoverable=False
        )
    check_origin(request)
    check_csrf(request, row)
    return row
