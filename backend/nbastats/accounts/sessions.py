"""Opaque, server-side sessions: issue, verify, rotate, revoke — and the two FastAPI-facing
identity dependencies (:func:`current_session`, :func:`require_user`, :func:`require_fresh_user`)
that everything else in this codebase authenticates through.

Why a session is two random halves, not one token
----------------------------------------------------
The cookie value is ``f"{session_id}.{secret}"``. ``session_id`` (``uuid4().hex``) is a public
lookup key — it is what ``GET /v1/me/sessions`` lists and what ``DELETE /v1/me/sessions/{id}``
revokes by. ``secret`` (``secrets.token_urlsafe(32)``, 256 bits) is what actually authenticates
the cookie, and only ``sha256(secret)`` is ever written to the database, in ``token_hash``.
:func:`verify` looks the row up by the public ``session_id`` (an indexed, exact primary-key
match — no scan, no partial-match oracle) and only then ``hmac.compare_digest``s the secret's
hash against the stored one. Splitting the id from the secret this way is what makes the
sessions-list feature possible at all: a page that shows "signed in from Chrome on Tuesday" has
to name *something* stable to revoke, and that something cannot be the secret itself.

Why rotation happens on every privilege change
--------------------------------------------------
:func:`rotate` mints a brand-new session row, stamps the old row's ``revoked_at``, and records
``rotated_from`` — on password sign-in, every OIDC sign-in, a password set/change/reset, an
email-change confirmation, and linking or unlinking a provider (``WEB_DESIGN.md`` §2.3). There
is no such thing as a pre-authentication session in this design at all: OAuth's transactional
state lives in its own table (``accounts.models.OAuthTransaction``) under its own cookie
(``hw_oauth``), so session fixation — planting a session id in a victim's browser before they
authenticate, then reusing it once they do — has no session to plant. The neighbouring attack,
*forced login* (planting a session that is already authenticated as the **attacker**, so the
victim's subsequent work lands in the attacker's account), is **narrowed, not closed**: no
``GET`` route mints a session (``GET /v1/auth/verify`` used to, and no longer does), and on an
https deployment the cookie is read under the ``__Host-`` name only (:func:`_read_cookie`), so
a sibling *host* cannot write one. On an http deployment — the ``http://127.0.0.1:8000``
default, and every ``HARDWOOD_ALLOW_INSECURE_COOKIES=1`` LAN deployment — the bare name is
still accepted and nothing server-side can stop another process on the same host, or an
on-path device on the same network, from replacing the cookie outright with ``Set-Cookie``.
Cookies are scoped by host, not by port, and ``__Host-`` does not change that. The countermeasure
is not in this module: ``web/src/auth/AuthProvider.tsx`` pins the account the tab bootstrapped
with and forces a visible sign-out when ``GET /v1/auth/session`` starts answering with a
different one. See ``accounts/csrf.py``'s docstring for why the CSRF token is not that
countermeasure. Rotation on top of all of it
means that even a session an attacker *did* observe (over someone's shoulder, in a proxy log
before HTTPS was configured, whatever) stops working the moment its owner does anything that
should have ended it.

Why identity resolution opens its own database session
------------------------------------------------------------
:func:`current_session` is meant to run on effectively every request, before any route's own
dependencies get a chance to fail loudly. It therefore does not take the request-scoped
``SessionDep`` FastAPI dependency; it opens and closes its own short-lived
:class:`~sqlalchemy.orm.Session` via ``nbastats.db.get_sessionmaker()``. A transient database
hiccup then degrades this call to "nobody is signed in" instead of turning every single route
in the service into a 500 the moment the session table has a bad millisecond.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable
from uuid import uuid4

from fastapi import Request, Response
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from .config import AuthSettings, get_auth_settings
from .models import AuthSession, AuthToken, OAuthTransaction, User

# `nbastats.db` is imported lazily (inside `_utcnow`/`_get_sessionmaker` below), never at
# module level. `nbastats/db.py::init_db` has to import `accounts.models.AccountBase`, and
# reaching `accounts.models` always initialises the `nbastats.accounts` *package* first
# (`accounts/__init__.py`, which re-exports names from this module) — a module-level
# `from ..db import ...` here would therefore make `nbastats.db`'s own import of the accounts
# package recurse back into a not-yet-finished `nbastats.db`. Deferring the import to call
# time costs one `sys.modules` lookup per call and breaks the cycle for good; see
# `nbastats/accounts/models.py`'s module docstring for the one-way `db.py -> accounts.models`
# dependency this preserves.


def _utcnow():
    from ..db import utcnow as _utcnow_impl

    return _utcnow_impl()


def _get_sessionmaker():
    from ..db import get_sessionmaker as _get_sessionmaker_impl

    return _get_sessionmaker_impl()


__all__ = [
    "SESSION_COOKIE",
    "OAUTH_COOKIE",
    "IssuedSession",
    "issue",
    "verify",
    "touch",
    "rotate",
    "revoke",
    "revoke_all",
    "csrf_token_for",
    "current_csrf_token",
    "read_session_cookie",
    "set_session_cookie",
    "clear_session_cookie",
    "sweep",
    "ip_prefix_of",
    "current_session",
    "require_user",
    "require_fresh_user",
]

SESSION_COOKIE = "hw_session"
OAUTH_COOKIE = "hw_oauth"

#: Slide the idle expiry at most this often, so a busy dashboard tab is not one write per
#: request — see ``WEB_DESIGN.md`` §2.3.
_TOUCH_INTERVAL = timedelta(minutes=5)

#: Reauthentication freshness window used by :func:`require_fresh_user` — matches
#: ``WEB_DESIGN.md`` §2.13's ``require_fresh_user``.
FRESH_WINDOW = timedelta(seconds=600)

#: On every ``verify()`` call there's roughly this chance of also sweeping expired rows, so
#: cleanup happens continuously without a dedicated background task or a write on every call.
_SWEEP_PROBABILITY = 0.01

#: ``secrets.SystemRandom`` is a thin, thread-safe wrapper over ``os.urandom`` — no lock
#: needed for the sweep-probability coin flip below.
_rng = secrets.SystemRandom()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """The result of minting a session: the database row, the raw cookie value (never stored),
    and the raw CSRF token (returned in a JSON body only — never in a cookie; see
    ``accounts/csrf.py``)."""

    row: AuthSession
    raw_cookie: str
    csrf_token: str


def _cookie_name(base: str, settings: AuthSettings) -> str:
    """``__Host-<base>`` once cookies are ``Secure`` (i.e. on https), plain ``<base>``
    otherwise. ``__Host-`` requires ``Secure``, forbids ``Domain`` and pins ``Path=/`` — a
    compromised sibling subdomain cannot overwrite it — but the prefix is meaningless (and
    rejected by the browser) on plain http, which is exactly when we do not use it."""
    return f"__Host-{base}" if settings.use_secure_cookies else base


def _read_cookie(request: Request, base: str, settings: AuthSettings) -> str | None:
    """Read the session cookie, accepting the unprefixed name **only on an insecure
    deployment**.

    The lenient both-names read this used to do was justified by one migration story: flipping
    a deployment from http to https must not sign everybody out mid-session because the cookie
    they hold is now called the "wrong" thing. But reading the bare name *while ``Secure`` is
    on* throws away the entire point of the ``__Host-`` prefix, which exists to stop a sibling
    host (a compromised subdomain, a CNAMEd third party, an on-path attacker on any plain-http
    subdomain) writing ``Set-Cookie: hw_session=…; Domain=.example.com`` into the victim's
    browser. With the fallback in place that injected cookie is read and honoured, so
    ``__Host-`` protects nothing and a signed-out victim can be force-logged into an attacker's
    account.

    So the direction of the migration decides: on http we accept the ``__Host-`` name too (it
    can only have been set over https by us, and going https -> http is a downgrade nobody does
    by accident), and on https we accept **only** ``__Host-``. An http -> https flip therefore
    costs one sign-in, which is the correct price for the guarantee.
    """
    prefixed = request.cookies.get(f"__Host-{base}")
    if settings.use_secure_cookies:
        return prefixed
    return prefixed or request.cookies.get(base)


def read_session_cookie(request: Request) -> str | None:
    """The raw session-cookie value this request carries, under whichever name this
    configuration accepts. Public so callers outside this module (``routes_auth``'s OAuth
    finish, ``GET /v1/auth/session``) never re-implement the name rule above."""
    return _read_cookie(request, SESSION_COOKIE, get_auth_settings())


def issue(
    db: Session,
    user: User,
    *,
    method: str,
    request: Request,
    previous: AuthSession | None = None,
) -> IssuedSession:
    """Mint a brand-new session for ``user``. If ``previous`` is given, it is revoked and
    ``rotated_from`` records the chain — this is what :func:`rotate` calls."""
    settings = get_auth_settings()
    now = _utcnow()
    session_id = uuid4().hex
    secret = secrets.token_urlsafe(32)
    raw_cookie = f"{session_id}.{secret}"
    # Derived from the secret, not independently random, so `GET /v1/auth/session` can hand the
    # same token back without writing to the row — see `csrf_token_for`.
    csrf_token = csrf_token_for(raw_cookie)
    assert csrf_token is not None  # a freshly minted cookie always has both halves
    user_agent = (request.headers.get("user-agent") or "").strip()[:255] or None

    row = AuthSession(
        session_id=session_id,
        user_id=user.user_id,
        token_hash=_hash(secret),
        csrf_hash=_hash(csrf_token),
        auth_method=method,
        created_at=now,
        last_seen_at=now,
        authenticated_at=now,
        idle_expires_at=now + timedelta(days=settings.session_days),
        absolute_expires_at=now + timedelta(days=settings.session_absolute_days),
        rotated_from=previous.session_id if previous is not None else None,
        user_agent=user_agent,
        ip_prefix=ip_prefix_of(request),
    )
    db.add(row)
    if previous is not None:
        previous.revoked_at = now
        db.add(previous)
    db.flush()
    return IssuedSession(row=row, raw_cookie=raw_cookie, csrf_token=csrf_token)


def verify(db: Session, raw_cookie: str | None) -> AuthSession | None:
    """Resolve a cookie value to a live, non-revoked, non-expired session row, or ``None``.

    Looks the row up by the public ``session_id`` half (an indexed exact match — see the
    module docstring) and only then compares the secret half's hash, so there is no partial-
    match timing oracle and no table scan on every request.
    """
    if not raw_cookie or "." not in raw_cookie:
        return None
    session_id, _, secret = raw_cookie.partition(".")
    if not session_id or not secret:
        return None

    row = db.get(AuthSession, session_id)

    # Maintenance runs independently of whether *this* cookie turns out to be valid, so a
    # steady trickle of ordinary traffic keeps the table from accumulating expired rows
    # forever without a dedicated background job.
    if _rng.random() < _SWEEP_PROBABILITY:
        sweep(db)

    if row is None or row.revoked_at is not None:
        return None
    now = _utcnow()
    if row.idle_expires_at <= now or row.absolute_expires_at <= now:
        return None
    if not hmac.compare_digest(_hash(secret), row.token_hash):
        return None
    return row


def touch(db: Session, row: AuthSession) -> None:
    """Slide ``idle_expires_at`` forward, at most once per :data:`_TOUCH_INTERVAL`, and never
    past ``absolute_expires_at`` — the hard cap is never extended, sliding or not."""
    now = _utcnow()
    if now - row.last_seen_at < _TOUCH_INTERVAL:
        return
    settings = get_auth_settings()
    row.last_seen_at = now
    candidate = now + timedelta(days=settings.session_days)
    row.idle_expires_at = min(candidate, row.absolute_expires_at)
    db.add(row)


def rotate(db: Session, row: AuthSession, *, request: Request, method: str) -> IssuedSession:
    """Issue a fresh session for ``row``'s user and revoke ``row`` — the one operation that
    runs on every privilege change (see the module docstring)."""
    user = db.get(User, row.user_id)
    if user is None:
        raise ApiError("unauthorized", "That account no longer exists.", http_status=401)
    return issue(db, user, method=method, request=request, previous=row)


def revoke(db: Session, row: AuthSession) -> None:
    """Revoke exactly this session."""
    row.revoked_at = _utcnow()
    db.add(row)


def revoke_all(db: Session, user_id: str, *, except_id: str | None = None) -> int:
    """Revoke every live session for ``user_id``, optionally sparing ``except_id`` (the caller's
    own session, on a "log out everywhere else" action). Returns the number of rows touched."""
    stmt = update(AuthSession).where(
        AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
    )
    if except_id is not None:
        stmt = stmt.where(AuthSession.session_id != except_id)
    result = db.execute(stmt.values(revoked_at=_utcnow()))
    return result.rowcount or 0


#: Domain separator for the CSRF derivation below, so the value can never collide with any
#: other use of the session secret.
_CSRF_CONTEXT = b"hardwood-csrf-v1"


def csrf_token_for(raw_cookie: str | None) -> str | None:
    """This session's CSRF token, derived from the cookie the caller already holds.

    The token is ``HMAC-SHA256(secret_half_of_the_cookie, "hardwood-csrf-v1")``, and only
    ``sha256(token)`` is stored, in ``auth_sessions.csrf_hash`` — so the database still holds no
    usable credential, exactly as before, and :func:`~accounts.csrf.check_csrf` is unchanged.

    What changed is that the token is now a **function of the session**, minted once in
    :func:`issue`, instead of a fresh random value written to the row every time
    ``GET /v1/auth/session`` was read. Rotating on read made a plain ``GET`` state-changing:
    ``SameSite=Lax`` sends the session cookie on a cross-site *top-level navigation*, so any
    page could point a victim's browser at ``/v1/auth/session`` and invalidate the CSRF token
    the victim's open tab was holding — every subsequent write in that tab failing
    ``csrf_failed``, indefinitely, on a timer. It also meant two legitimate tabs of the same SPA
    broke each other with no attacker present at all. A derived token is stable for the life of
    the session, so reading it is genuinely a read.

    An attacker who can make the victim's browser issue that navigation still cannot *see* the
    response (CORS credentials are off), and knowing a CSRF token does not yield the session
    secret: HMAC is one-way, and the token is the output, not the key.
    """
    if not raw_cookie or "." not in raw_cookie:
        return None
    _session_id, _, secret = raw_cookie.partition(".")
    if not secret:
        return None
    digest = hmac.new(secret.encode("utf-8"), _CSRF_CONTEXT, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def current_csrf_token(request: Request) -> str | None:
    """The CSRF token belonging to the session cookie on ``request``, or ``None`` when there is
    no cookie. Never touches the database and never writes anything."""
    return csrf_token_for(read_session_cookie(request))


def set_session_cookie(response: Response, issued: IssuedSession) -> None:
    settings = get_auth_settings()
    response.set_cookie(
        key=_cookie_name(SESSION_COOKIE, settings),
        value=issued.raw_cookie,
        max_age=settings.session_days * 86400,
        path="/",
        httponly=True,
        secure=settings.use_secure_cookies,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    """Delete both possible cookie names — see :func:`_read_cookie` for why both are read on
    the way in; both must be cleared on the way out for the same reason."""
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(f"__Host-{SESSION_COOKIE}", path="/", secure=True, samesite="lax")


def sweep(db: Session) -> int:
    """Delete every expired row in the three tables that accumulate them, and return the total.

    * ``auth_sessions`` — idle window lapsed. Absolute-expired-but-not-idle-expired rows are
      cleaned up too, since ``idle_expires_at`` is always clamped to ``absolute_expires_at``
      (see :func:`touch`) and therefore never lags behind it for long.
    * ``oauth_transactions`` — past ``expires_at`` (600 s), or already consumed. Nothing else
      in the codebase ever deleted one, and ``GET /v1/auth/{provider}/start`` is an
      *unauthenticated* writer of one row per call, rate-limited only per /24: the table grew
      without bound and, worse, stood as a permanent store of plaintext PKCE ``code_verifier``
      values long after the flows they belonged to were dead.
    * ``auth_tokens`` — past ``expires_at``, or already consumed. Written by
      ``POST /v1/auth/password/forgot``, ``/verify/resend`` and ``POST /v1/me/email``, and
      previously only ever stamped, never removed.

    Both extra deletes are keyed on a timestamp already in the row, so a consumed-but-unexpired
    token is kept until its own expiry rather than being removed the instant it is used — the
    single-use check in ``tokens.consume`` still has a row to find, so a replayed link keeps
    answering "expired or already used" instead of turning into a confusing 500.
    """
    now = _utcnow()
    removed = db.execute(delete(AuthSession).where(AuthSession.idle_expires_at < now)).rowcount or 0
    removed += (
        db.execute(delete(OAuthTransaction).where(OAuthTransaction.expires_at < now)).rowcount or 0
    )
    removed += db.execute(delete(AuthToken).where(AuthToken.expires_at < now)).rowcount or 0
    return removed


def _in_any_cidr(host: str, cidrs: Iterable[str]) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def _client_ip(request: Request, settings: AuthSettings) -> str | None:
    """The address to record, honouring ``X-Forwarded-For`` only when the immediate peer is
    inside a configured, trusted CIDR — the default (empty) trusts nobody, so an internet-
    facing deployment with no reverse-proxy configuration recorded gets the proxy's own
    address for everyone rather than a client-forgeable one."""
    direct = request.client.host if request.client else None
    if not settings.trusted_proxy_cidrs or settings.trusted_proxy_hops <= 0 or direct is None:
        return direct
    if not _in_any_cidr(direct, settings.trusted_proxy_cidrs):
        return direct
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return direct
    hops = [part.strip() for part in forwarded.split(",") if part.strip()]
    if not hops:
        return direct
    # Count from the RIGHT: the entry our own trusted proxy appended is
    # ``trusted_proxy_hops`` positions in from the end. Anything further left could have been
    # supplied by the original client and must never be trusted.
    index = len(hops) - settings.trusted_proxy_hops
    if 0 <= index < len(hops):
        return hops[index]
    return direct


def ip_prefix_of(request: Request) -> str | None:
    """A truncated network prefix (``/24`` for IPv4, ``/48`` for IPv6) — honest minimisation,
    not pseudonymisation: a salted hash of a 32-bit address space is reversible by brute force
    in seconds, so storing one and calling it "anonymised" would be a lie a security audit
    would catch. A truncated prefix cannot be reversed to the original address at all."""
    settings = get_auth_settings()
    host = _client_ip(request, settings)
    if not host:
        return None
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return None
    prefix_len = 24 if addr.version == 4 else 48
    network = ipaddress.ip_network(f"{addr}/{prefix_len}", strict=False)
    return str(network)


# --------------------------------------------------------------------------- identity deps


def _resolve(request: Request) -> AuthSession | None:
    """Do the actual cookie verification, in a short-lived session of its own, and stash the
    result on ``request.state`` so every dependency in the same request shares one answer."""
    settings = get_auth_settings()
    raw_cookie = _read_cookie(request, SESSION_COOKIE, settings)
    factory = _get_sessionmaker()
    db = factory()
    try:
        row = verify(db, raw_cookie)
        user: User | None = None
        if row is not None:
            user = db.get(User, row.user_id)
            if user is None or user.deleted_at is not None or user.is_disabled:
                row = None
                user = None
            else:
                touch(db, row)
        db.commit()
    except Exception:
        db.rollback()
        row = None
        user = None
    finally:
        db.close()
    request.state.auth_session = row
    request.state.user = user
    return row


def current_session(request: Request) -> AuthSession | None:
    """Always runs. Reads the session cookie, verifies it, and stashes ``request.state.user`` /
    ``request.state.auth_session`` — a live session and its user, or both ``None``. Safe to
    call more than once per request: the result is cached on ``request.state`` after the
    first call.
    """
    if hasattr(request.state, "auth_session"):
        return request.state.auth_session
    return _resolve(request)


def require_user(request: Request) -> User:
    """401 ``unauthorized`` when there is no live session; otherwise the signed-in user."""
    current_session(request)
    user = getattr(request.state, "user", None)
    if user is None:
        raise ApiError(
            "unauthorized", "Sign in to continue.", http_status=401, recoverable=False
        )
    return user


def require_fresh_user(request: Request) -> User:
    """:func:`require_user`, plus ``now - authenticated_at <= 600s``.

    Required before any step-up action: linking or unlinking a provider, changing a password
    or email, or deleting the account. A session that has been alive for days should not be
    enough, on its own, to take over the identities attached to an account — the whole point
    of ``authenticated_at`` (stamped fresh on every sign-in and every rotation) is that it does
    not slide the way ``last_seen_at`` does.
    """
    user = require_user(request)
    row = request.state.auth_session
    if _utcnow() - row.authenticated_at > FRESH_WINDOW:
        raise ApiError(
            "reauthentication_required",
            "Please sign in again to confirm it's you.",
            http_status=403,
            recoverable=True,
        )
    return user
