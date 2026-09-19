"""``/v1/auth/*`` — password and provider sign-in, session lifecycle, email verification and
password reset. See ``WEB_DESIGN.md`` §4.1, §4.6-§4.9, §5 and §6.

This module carries **no API-key guard** — it is included with only the rate limiter as a
dependency (``OPTIONAL_ROUTE_GUARDS = {"routes_auth": "rate_only"}`` in ``api/app.py``, WP2's
territory), because a service with ``HARDWOOD_API_KEY`` set for its stats endpoints must still
let an unauthenticated browser sign in. Everything that touches identity — hashing, sessions,
tokens, CSRF, linking, the OIDC dance — is delegated to the ``nbastats.accounts`` package this
module only orchestrates; see that package for the actual security reasoning.

Rate limiting here is deliberately **not** the shared, single-bucket
``nbastats.api.deps.RateLimiter`` instance the rest of the API uses (that one guards against
any client hammering the service in general). ``WEB_DESIGN.md`` §4.8 calls for several
independent, finer-grained buckets — ``login:ip``, ``login:user``, ``signup:ip``, ``forgot:ip``,
``oauth_start:ip`` — each with its own limit and window, because a brake tuned for "don't let
one client spam the whole API" is much too loose for "don't let one client try ten thousand
passwords against one account." ``deps.py`` is WP2's file, so rather than add a second
general-purpose limiter factory there, this module keeps its own small set of ``RateLimiter``
instances (the same reusable class ``deps.py`` already defines), reset together by
:func:`reset_auth_route_limiters` for tests — mirroring, not editing, the existing
``reset_rate_limiter()`` pattern.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import logging
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode, urljoin
from uuid import uuid4

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..accounts import require_user, require_write
from ..accounts import mail, passwords, tokens
from ..accounts import sessions as sessions_module
from ..accounts.config import AuthSettings, get_auth_settings
from ..accounts.csrf import check_origin
from ..accounts.linking import LinkConflict, link_to, resolve_login
from ..accounts.models import (
    AuthSession,
    OAuthTransaction,
    PROVIDERS,
    User,
    UserIdentity,
    WebInvite,
)
from ..accounts.providers import apple as apple_provider
from ..accounts.providers import google as google_provider
from ..accounts.providers import provider_status, safe_next
from ..accounts.providers.oidc import Claims, OIDCError, discovery, verify_id_token
from ..db import utcnow
from . import errors
from .deps import RateLimiter, SessionDep
from .errors import ApiError

__all__ = ["router", "reset_auth_route_limiters", "begin_oauth", "set_oauth_cookie"]

logger = logging.getLogger("nbastats.api.auth")

router = APIRouter(tags=["auth"])


class _AuthModel(BaseModel):
    """Same camelCase-alias contract as ``api/schemas.py::ContractModel``, defined locally so
    this module's request bodies do not require an edit to a file WP1 does not own."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class SignupBody(_AuthModel):
    email: str
    password: str
    display_name: str | None = None
    invite_code: str | None = None


class LoginBody(_AuthModel):
    email: str
    password: str


class LogoutBody(_AuthModel):
    all: bool = False


class EmailOnlyBody(_AuthModel):
    email: str


class ResetBody(_AuthModel):
    token: str
    password: str


# --------------------------------------------------------------------------- rate limits
#
# Limits and windows are exactly WEB_DESIGN.md §4.8's table. Each bucket is its own
# RateLimiter instance (rather than one shared instance keyed by a prefixed string) so that
# resetting or reasoning about one bucket in a test never has to worry about key collisions
# with another.

_LOGIN_IP = RateLimiter(limit=30, window=15 * 60)
_LOGIN_USER = RateLimiter(limit=10, window=15 * 60)
_SIGNUP_IP = RateLimiter(limit=5, window=60 * 60)
_FORGOT_IP = RateLimiter(limit=5, window=60 * 60)
_VERIFY_RESEND_IP = RateLimiter(limit=5, window=60 * 60)
_OAUTH_START_IP = RateLimiter(limit=20, window=15 * 60)

_ALL_LIMITERS = (
    _LOGIN_IP,
    _LOGIN_USER,
    _SIGNUP_IP,
    _FORGOT_IP,
    _VERIFY_RESEND_IP,
    _OAUTH_START_IP,
)


def reset_auth_route_limiters() -> None:
    """Clear every auth-route rate limiter. Tests call this in setup so one test's login
    attempts never count against the next test's quota."""
    for limiter in _ALL_LIMITERS:
        limiter.reset()


def _client_key(request: Request) -> str:
    return sessions_module.ip_prefix_of(request) or (
        request.client.host if request.client else "unknown"
    )


def _enforce(limiter: RateLimiter, key: str) -> None:
    retry_after = limiter.check(key)
    if retry_after:
        raise errors.rate_limited(retry_after)


# --------------------------------------------------------------------------- shared helpers


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else None
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _invalid_credentials() -> ApiError:
    return ApiError(
        "invalid_credentials",
        "That email and password do not match.",
        http_status=401,
        recoverable=True,
    )


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat() + "Z"


def _user_out(db: Session, user: User) -> dict:
    identities = sorted(
        db.execute(
            select(UserIdentity.provider).where(UserIdentity.user_id == user.user_id)
        ).scalars()
    )
    return {
        "userId": user.user_id,
        "email": user.email,
        "emailVerified": user.email_verified_at is not None,
        "displayName": user.display_name,
        "isPrivateRelay": user.is_private_relay,
        "hasPassword": user.password_hash is not None,
        "identities": identities,
        "favoritePlayerId": user.favorite_player_id,
        "favoriteTeamId": user.favorite_team_id,
        "selectedDashboardId": user.selected_dashboard_id,
        "theme": user.theme_preference,
        "createdAt": _iso(user.created_at),
    }


def _methods_payload(settings: AuthSettings) -> dict:
    status = provider_status(settings)
    return {
        "password": {"enabled": True, "signupMode": settings.signup_mode},
        "google": status["google"],
        "apple": status["apple"],
    }


def _link_for(settings: AuthSettings, purpose: str, token: str) -> str:
    base = settings.public_base_url.rstrip("/")
    if purpose == "verify":
        return f"{base}/v1/auth/verify?token={token}"
    if purpose == "reset":
        return f"{base}/reset?token={token}"
    raise ValueError(f"routes_auth mints no link for token purpose {purpose!r}")


def _mint_link(db: Session, user_id: str, purpose: str, settings: AuthSettings) -> str:
    raw = tokens.mint(db, user_id, purpose)
    return _link_for(settings, purpose, raw)


def _invalid_invite() -> ApiError:
    return ApiError(
        "invalid_invite", "That invite code is not valid.", http_status=400, field="inviteCode"
    )


def _check_invite(
    db: Session, settings: AuthSettings, supplied: str | None
) -> WebInvite | None:
    """Enforce ``HARDWOOD_SIGNUP_MODE`` and return the ``web_invites`` row to burn, if any.

    Two gates, not one:

    * ``invite`` mode accepts either a single-use row from ``web_invites`` (minted by
      ``python3 -m nbastats.accounts.admin invite``) **or** the well-known
      ``HARDWOOD_INVITE_CODE``, if one is configured.
    * ``open`` mode is genuinely open **only when no ``HARDWOOD_INVITE_CODE`` is set**. When one
      is, it is required. That variable is documented as "mainly for ``open`` mode's non-loopback
      backstop" and ``config.startup_refusals`` refuses to start a public ``open`` deployment
      without it — but nothing in the request path had ever compared anything to it, so the one
      control the design placed between the open internet and account creation was decorative
      and any stranger got a usable account. This is the missing half.

    Comparison is :func:`hmac.compare_digest`: the shared code is a low-entropy secret typed by
    a human, and a timing oracle on it is cheap to avoid.
    """
    supplied = (supplied or "").strip()
    if settings.signup_mode == "closed":
        raise ApiError("signup_closed", "New sign-ups are not open right now.", http_status=403)

    shared_ok = bool(settings.invite_code) and hmac.compare_digest(
        supplied, settings.invite_code or ""
    )

    if settings.signup_mode == "open":
        if settings.invite_code and not shared_ok:
            raise _invalid_invite()
        return None

    # invite mode
    now = utcnow()
    row = (
        db.execute(select(WebInvite).where(WebInvite.code == supplied)).scalar_one_or_none()
        if supplied
        else None
    )
    row_ok = (
        row is not None
        and row.used_at is None
        and (row.expires_at is None or row.expires_at > now)
    )
    if not row_ok and not shared_ok:
        raise _invalid_invite()
    return row if row_ok else None


# --------------------------------------------------------------------------- §4.1 basics


@router.get("/auth/methods")
def get_auth_methods() -> dict:
    """Which sign-in methods the client should offer. Read fresh on every call — see
    ``accounts/providers/__init__.py::provider_status``."""
    return _methods_payload(get_auth_settings())


@router.get("/auth/session")
def get_session_info(
    request: Request, db: SessionDep, user: User = Depends(require_user)
) -> Response:
    """The signed-in user, this session's CSRF token, and the provider-availability map, in one
    call — the SPA's entire "who am I, and what can I show" bootstrap. ``Cache-Control:
    no-store``: this response names a real person and must never be served from a shared cache.

    This handler writes **nothing**. It used to mint a fresh CSRF token and store its hash on
    the session row, which made a plain ``GET`` state-changing: ``SameSite=Lax`` sends the
    session cookie on a cross-site *top-level* navigation, so any page could point a signed-in
    victim's browser here and permanently break every write in the victim's open tab (and two
    honest tabs of the SPA broke each other the same way, with no attacker at all). The token is
    now derived from the session secret the caller already holds —
    ``accounts/sessions.py::csrf_token_for`` — so handing it back is a genuine read.
    """
    csrf_token = sessions_module.current_csrf_token(request)
    payload = {
        "user": _user_out(db, user),
        "csrfToken": csrf_token,
        "methods": _methods_payload(get_auth_settings()),
    }
    response = JSONResponse(content=payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/auth/signup", status_code=202)
def signup(
    request: Request, background_tasks: BackgroundTasks, db: SessionDep, body: SignupBody
) -> dict:
    """Always ``202 {"status": "checkYourEmail"}`` — identical body and timing whether or not
    the address already has an account (``WEB_DESIGN.md`` §4.9). The password is hashed
    *before* checking whether the address is taken, so the new-account path and the
    already-exists path cost the same ~60ms; skipping the hash on the "taken" branch would
    make that branch reliably faster and turn a stopwatch into an enumeration oracle.
    """
    check_origin(request)
    settings = get_auth_settings()
    _enforce(_SIGNUP_IP, f"signup:ip:{_client_key(request)}")

    problem = passwords.policy_problem(body.password, email=body.email)
    if problem:
        raise errors.bad_request(problem, field="password")

    invite = _check_invite(db, settings, body.invite_code)

    # Hash unconditionally, before branching on whether the address is taken — see the
    # docstring above.
    password_hash = passwords.hash_password(body.password)

    email_lookup = _normalize_email(body.email)
    now = utcnow()
    created_user_id: str | None = None
    existing = db.execute(
        select(User).where(User.email_lookup == email_lookup, User.deleted_at.is_(None))
    ).scalar_one_or_none()

    if existing is not None:
        # The address is taken. Tell its owner, not the caller: a signed link that lets them
        # sign in (or reset a forgotten password) rather than confirmation that signing up
        # would have failed.
        reset_link = _mint_link(db, existing.user_id, "reset", settings)
        background_tasks.add_task(
            mail.send_signup_conflict_email, existing.email or body.email, reset_link
        )
    else:
        user = User(
            user_id=uuid4().hex,
            email=body.email,
            email_lookup=email_lookup,
            password_hash=password_hash,
            password_changed_at=now,
            display_name=body.display_name,
            theme_preference="system",
            profile_json="{}",
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        db.flush()
        created_user_id = user.user_id
        verify_link = _mint_link(db, user.user_id, "verify", settings)
        background_tasks.add_task(mail.send_verification_email, body.email, verify_link)

    # The invite is burned on *both* branches. Burning it only when an account was actually
    # created leaked the one bit §4.9 spends the whole route hiding: send the same code twice,
    # and a `202` on the second call means the first address was already registered while a
    # `400 invalid_invite` means it was free. `used_by` still records who redeemed it, and is
    # left NULL on the collision branch because nobody did.
    if invite is not None:
        invite.used_at = now
        invite.used_by = created_user_id
        db.add(invite)

    db.commit()
    return {"status": "checkYourEmail"}


@router.post("/auth/login")
def login(request: Request, response: Response, db: SessionDep, body: LoginBody) -> dict:
    """Exactly one scrypt derivation on every path — a real one for a known account with a
    password, :func:`passwords.dummy_verify` for everything else (unknown address, provider-
    only account, disabled, soft-deleted, or currently backed off) — so a miss never returns
    measurably faster than a wrong password on a real account (``WEB_DESIGN.md`` §4.9)."""
    check_origin(request)
    _enforce(_LOGIN_IP, f"login:ip:{_client_key(request)}")

    email_lookup = _normalize_email(body.email)
    user = db.execute(
        select(User).where(User.email_lookup == email_lookup, User.deleted_at.is_(None))
    ).scalar_one_or_none()

    if user is not None:
        # Keyed on the resolved user id, not the submitted email, so changing an address does
        # not mint a fresh quota (WEB_DESIGN.md §4.8).
        _enforce(_LOGIN_USER, f"login:user:{user.user_id}")

    now = utcnow()
    if user is None or user.password_hash is None or user.is_disabled:
        passwords.dummy_verify()
        raise _invalid_credentials()

    if user.locked_until is not None and user.locked_until > now:
        # The delay is applied by refusing early and *still* performing dummy_verify(), so a
        # locked account costs the same time and returns the same body as a wrong password.
        passwords.dummy_verify()
        raise _invalid_credentials()

    if not passwords.verify_password(body.password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= 6:
            delay_seconds = min(2 ** (user.failed_login_count - 5), 300)
            user.locked_until = now + timedelta(seconds=delay_seconds)
        db.add(user)
        db.commit()
        raise _invalid_credentials()

    # A correct password always clears the backoff state (WEB_DESIGN.md §4.8): a stranger who
    # knows an address can slow its owner down, never lock them out.
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    db.add(user)
    issued = sessions_module.issue(db, user, method="password", request=request)
    db.commit()
    sessions_module.set_session_cookie(response, issued)
    return {"user": _user_out(db, user), "csrfToken": issued.csrf_token}


@router.post("/auth/logout", status_code=204)
def logout(
    db: SessionDep,
    response: Response,
    body: LogoutBody = LogoutBody(),
    session_row: AuthSession = Depends(require_write),
) -> Response:
    if body.all:
        sessions_module.revoke_all(db, session_row.user_id)
    else:
        live = db.get(AuthSession, session_row.session_id)
        if live is not None:
            sessions_module.revoke(db, live)
    db.commit()
    # The cookie must be cleared on the response this handler actually **returns**. FastAPI
    # only merges the injected `response`'s headers into a return value that is *not* itself a
    # `Response` (fastapi/routing.py: `if isinstance(raw_response, Response): response =
    # raw_response`), so writing the deletion header onto the injected object and then returning
    # a different one silently dropped it — logout answered 204 with no `Set-Cookie` at all.
    out = Response(status_code=204)
    sessions_module.clear_session_cookie(out)
    return out


@router.get("/auth/verify")
def verify_email(token: str, db: SessionDep) -> RedirectResponse:
    """A single-use ``GET`` that immediately ``303``s, per ``WEB_DESIGN.md`` §2.5 — a token
    never sits in a URL a user can revisit or a page that renders normally with it still
    present, so it cannot leak through history or a ``Referer`` header on whatever loads next.

    **This route does not sign anybody in**, and must not be changed to.

    It used to mint a session and set the cookie, which is a textbook *forced login*: an
    attacker signs up with an address they control, reads their own verification link, does not
    click it, and instead gets a victim to make a top-level ``GET`` navigation to it (a link, a
    ``window.open``, an auto-submitted ``<form method=GET>``). Setting a ``SameSite=Lax`` cookie
    from a cross-site top-level navigation is permitted, so the victim's browser accepts the
    attacker's session and the victim silently continues as the attacker — every favourite,
    every dashboard and every export from then on landing in an account the attacker reads at
    leisure, and with ``authenticated_at`` fresh enough for the step-up actions too. A
    state-changing ``GET`` that mints a credential also breaks the premise ``WEB_DESIGN.md`` §5
    rests ``SameSite=Lax``'s sufficiency on ("no ``GET`` route in this design mutates
    anything").

    Nothing is lost by not signing in here: §4.6 is explicit that "an unverified email account
    can sign in and use every feature", so the person following this link either already has a
    session (in which case they keep it — this handler leaves it untouched) or signs in
    normally on the page they land on.
    """
    row = tokens.consume(db, "verify", token)
    user = db.get(User, row.user_id) if row is not None else None
    if row is None or user is None or user.deleted_at is not None:
        db.commit()
        return RedirectResponse(url="/verify?expired=1", status_code=303)

    user.email_verified_at = utcnow()
    db.add(user)
    db.commit()
    return RedirectResponse(url="/settings?verified=1", status_code=303)


@router.post("/auth/verify/resend", status_code=202)
def resend_verification(
    request: Request, background_tasks: BackgroundTasks, db: SessionDep, body: EmailOnlyBody
) -> dict:
    check_origin(request)
    _enforce(_VERIFY_RESEND_IP, f"verify_resend:ip:{_client_key(request)}")
    settings = get_auth_settings()
    email_lookup = _normalize_email(body.email)
    user = db.execute(
        select(User).where(User.email_lookup == email_lookup, User.deleted_at.is_(None))
    ).scalar_one_or_none()
    if user is not None and user.email is not None and user.email_verified_at is None:
        link = _mint_link(db, user.user_id, "verify", settings)
        background_tasks.add_task(mail.send_verification_email, user.email, link)
        db.commit()
    return {"status": "checkYourEmail"}


@router.post("/auth/password/forgot", status_code=202)
def forgot_password(
    request: Request, background_tasks: BackgroundTasks, db: SessionDep, body: EmailOnlyBody
) -> dict:
    check_origin(request)
    settings = get_auth_settings()
    _enforce(_FORGOT_IP, f"forgot:ip:{_client_key(request)}")

    email_lookup = _normalize_email(body.email)
    user = db.execute(
        select(User).where(User.email_lookup == email_lookup, User.deleted_at.is_(None))
    ).scalar_one_or_none()

    dev_link: str | None = None
    if user is not None and user.password_hash is not None and not user.is_disabled:
        link = _mint_link(db, user.user_id, "reset", settings)
        background_tasks.add_task(mail.send_reset_email, user.email, link)
        db.commit()
        # Gated on the *caller's* address being loopback, never on the configured base URL
        # (which defaults to loopback) — otherwise any caller on the LAN could request a
        # password-reset link for any address. The always-202 rule above means the real
        # account owner never sees evidence that this happened.
        if settings.dev_links and _is_loopback(request):
            dev_link = link

    result: dict = {"status": "checkYourEmail"}
    if dev_link is not None:
        result["devLink"] = dev_link
    return result


@router.post("/auth/password/reset", status_code=204)
def reset_password(request: Request, db: SessionDep, body: ResetBody) -> Response:
    check_origin(request)
    problem = passwords.policy_problem(body.password, email=None)
    if problem:
        raise errors.bad_request(problem, field="password")

    row = tokens.consume(db, "reset", body.token)
    user = db.get(User, row.user_id) if row is not None else None
    if row is None or user is None or user.deleted_at is not None:
        db.commit()
        raise ApiError(
            "invalid_token", "That reset link has expired or was already used.", http_status=400
        )

    user.password_hash = passwords.hash_password(body.password)
    user.password_changed_at = utcnow()
    user.failed_login_count = 0
    user.locked_until = None
    db.add(user)
    sessions_module.revoke_all(db, user.user_id)
    db.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- §6 OAuth
#
# Both providers drive the same authorization-code + PKCE dance, with the transaction's state
# entirely server-side in `OAuthTransaction`, keyed by a handle that travels in its own
# short-lived `hw_oauth` cookie — never by the `state` parameter alone, which travels in a URL
# and could leak through a log or a Referer. Splitting them is what makes a stolen `state`
# inert and a cookie-injecting attacker (who has no `state`) equally unable to complete a
# sign-in. See `WEB_DESIGN.md` §6.1 and `accounts/models.py::OAuthTransaction`.


def _hash_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _oauth_cookie_name(settings: AuthSettings) -> str:
    base = sessions_module.OAUTH_COOKIE
    return f"__Host-{base}" if settings.use_secure_cookies else base


def _set_oauth_cookie(response: Response, settings: AuthSettings, handle: str) -> None:
    # SameSite=None only over https: Apple's response_mode=form_post is a cross-site POST and
    # Lax would withhold the cookie; None requires Secure, which is exactly why Apple is
    # hard-gated off on http deployments (accounts/providers/__init__.py::provider_status).
    # Google's redirect is a top-level GET, for which Lax already suffices on localhost.
    same_site = "none" if settings.use_secure_cookies else "lax"
    response.set_cookie(
        key=_oauth_cookie_name(settings),
        value=handle,
        max_age=600,
        path="/",
        httponly=True,
        secure=settings.use_secure_cookies,
        samesite=same_site,
    )


def _clear_oauth_cookie(response: Response, settings: AuthSettings) -> None:
    """Append the oauth-cookie deletion header to ``response`` without disturbing any
    ``Set-Cookie`` header already on it.

    ``response.headers.update(...)`` (a plain dict update) is the wrong tool here:
    Starlette's ``MutableHeaders.__setitem__`` — which ``.update()`` calls — *replaces* every
    existing header of the same name, so updating with a second ``Set-Cookie`` would silently
    delete the session cookie ``sessions_module.set_session_cookie`` just wrote onto this same
    response. ``.append()`` preserves both.
    """
    header_value = _delete_oauth_cookie_header(settings)["Set-Cookie"]
    response.headers.append("set-cookie", header_value)


def _delete_oauth_cookie_header(settings: AuthSettings) -> dict[str, str]:
    """A ``Set-Cookie`` header that deletes the oauth-transaction cookie, for attaching to
    *any* response this flow produces — including an error response built by ``ApiError``,
    which does not otherwise go through a place we could call ``response.delete_cookie`` on.
    Every callback handler clears this cookie on its very first branch precisely so a stale or
    replayed ``hw_oauth`` cookie is never left sitting in the browser to be reused."""
    name = _oauth_cookie_name(settings)
    same_site = "None" if settings.use_secure_cookies else "Lax"
    secure = "; Secure" if settings.use_secure_cookies else ""
    return {"Set-Cookie": f"{name}=; Path=/; Max-Age=0; HttpOnly; SameSite={same_site}{secure}"}


def _oauth_error(
    code: str, message: str, *, settings: AuthSettings, http_status: int = 400
) -> ApiError:
    return ApiError(
        code, message, http_status=http_status, recoverable=True,
        headers=_delete_oauth_cookie_header(settings),
    )


def _pkce_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _authorize_url(
    provider: str,
    settings: AuthSettings,
    *,
    state: str,
    nonce: str,
    code_challenge: str,
    redirect_uri: str,
) -> str:
    if provider == "google":
        doc = discovery(google_provider.ISSUER)
        params = {
            "response_type": "code",
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri,
            "scope": google_provider.SCOPE,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return f"{doc['authorization_endpoint']}?{urlencode(params)}"
    if provider == "apple":
        # `code_challenge` is not optional decoration here: `_exchange_apple_code` sends a
        # `code_verifier`, and RFC 7636 §4.6 requires an authorization server to *reject* a
        # redemption that presents a verifier for a code issued with no challenge. Omitting the
        # challenge while still sending the verifier therefore risked a blanket `invalid_grant`
        # from Apple, surfaced as the generic "Apple sign-in failed. Try again." — in the one
        # flow that is hard-gated to https (§2.7) and so cannot be exercised locally. It also
        # meant the Apple leg had no proof-of-possession binding between `/start` and the
        # redemption at all, so a leaked code (a Referer, a proxy log, a mis-set redirect_uri)
        # was redeemable by anyone holding the client secret.
        params = {
            "response_type": "code id_token",
            "response_mode": "form_post",
            "client_id": settings.apple_services_id,
            "redirect_uri": redirect_uri,
            "scope": apple_provider.SCOPE,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{apple_provider.AUTHORIZE}?{urlencode(params)}"
    raise ValueError(f"unknown provider {provider!r}")  # pragma: no cover - guarded by caller


def begin_oauth(
    db: Session,
    request: Request,
    provider: str,
    *,
    intent: str = "signin",
    link_user_id: str | None = None,
    next_raw: str | None = None,
) -> tuple[str, str]:
    """Build one OAuth transaction and return ``(authorize_url, handle)``.

    The single writer of :class:`~accounts.models.OAuthTransaction`, shared by
    ``GET /v1/auth/{provider}/start`` (``intent="signin"``) and
    ``POST /v1/me/identities/{provider}/start`` (``intent="link"``). WEB_DESIGN.md §6 exists to
    stop the state/handle split, the PKCE parameters and the snapshotted ``redirect_uri`` from
    being written twice and drifting, so the link route calls this rather than carrying its own
    copy — the reason ``routes_me`` originally shipped without that route at all.

    The caller owns the response, and therefore the ``hw_oauth`` cookie: use
    :func:`set_oauth_cookie` on whatever object is actually returned.
    """
    if provider not in PROVIDERS:
        raise ApiError(
            "provider_not_configured", f"Unknown provider {provider!r}.", http_status=404
        )
    settings = get_auth_settings()
    status = provider_status(settings)[provider]
    if not status["enabled"]:
        raise ApiError(
            "provider_not_configured",
            status["reason"] or f"{provider} sign-in is not configured.",
            http_status=404,
        )
    _enforce(_OAUTH_START_IP, f"oauth_start:ip:{_client_key(request)}")

    next_path = safe_next(next_raw)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    handle = secrets.token_urlsafe(32)
    code_challenge = _pkce_challenge(code_verifier)
    redirect_uri = f"{settings.public_base_url.rstrip('/')}/v1/auth/{provider}/callback"

    now = utcnow()
    txn = OAuthTransaction(
        handle_hash=_hash_hex(handle),
        provider=provider,
        state_hash=_hash_hex(state),
        nonce=nonce,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        next_path=next_path,
        intent=intent,
        link_user_id=link_user_id,
        created_at=now,
        expires_at=now + timedelta(seconds=600),
    )
    db.add(txn)
    db.commit()

    authorize_url = _authorize_url(
        provider, settings, state=state, nonce=nonce, code_challenge=code_challenge,
        redirect_uri=redirect_uri,
    )
    return authorize_url, handle


def set_oauth_cookie(response: Response, handle: str) -> None:
    """Attach the ``hw_oauth`` transaction cookie for ``handle`` to ``response`` — public so
    ``routes_me``'s link-start route writes it exactly the way this module does."""
    _set_oauth_cookie(response, get_auth_settings(), handle)


@router.get("/auth/{provider}/start")
def oauth_start(
    provider: str, request: Request, db: SessionDep, next: str | None = Query(default=None)
) -> RedirectResponse:
    authorize_url, handle = begin_oauth(db, request, provider, next_raw=next)
    response = RedirectResponse(url=authorize_url, status_code=302)
    _set_oauth_cookie(response, get_auth_settings(), handle)
    return response


def _consume_transaction(
    db: Session, request: Request, settings: AuthSettings
) -> OAuthTransaction | None:
    """Read the ``hw_oauth`` handle, look up its transaction, and stamp it consumed
    **immediately** — before any further processing that could fail — so a replayed callback
    (the browser back button, a retried request) can never complete the flow twice."""
    # Exactly the name this configuration writes, with no fallback. The lenient
    # "`__Host-hw_oauth` or plain `hw_oauth`" read that used to be here nullified the
    # `__Host-` prefix outright: on an https deployment a sibling host (a compromised
    # subdomain, a CNAMEd third party, an on-path attacker on any plain-http subdomain) can
    # `Set-Cookie: hw_oauth=…; Domain=.example.com`, which `__Host-` exists precisely to
    # prevent, and that injected handle was then accepted here — letting an attacker who had
    # started their own flow (and so holds the matching `state` and a live `code`) force the
    # victim's browser to complete a sign-in into the *attacker's* account. The http->https
    # migration argument that justifies a both-names read for the 30-day session cookie cannot
    # apply to a handle that expires in 600 seconds.
    handle = request.cookies.get(_oauth_cookie_name(settings))
    if not handle:
        return None
    row = db.execute(
        select(OAuthTransaction).where(OAuthTransaction.handle_hash == _hash_hex(handle))
    ).scalar_one_or_none()
    if row is None:
        return None
    now = utcnow()
    if row.consumed_at is not None or row.expires_at <= now:
        return None
    row.consumed_at = now
    db.add(row)
    db.flush()
    return row


def _rotate_current_session_or_issue(
    db: Session, request: Request, user: User, method: str
) -> sessions_module.IssuedSession:
    """Rotate the caller's *own* live session onto ``user`` when there is one — the "linking
    or unlinking a provider" case in ``WEB_DESIGN.md`` §2.3's rotation list — otherwise issue
    a fresh one, exactly as a first sign-in does.

    A live session belonging to a *different* user is left alone rather than rotated: rotating
    it would mean silently ending someone else's browser session under the guise of "linking a
    provider" if the two ever disagreed, which cannot happen for the ``link`` intent (the
    linking route requires a fresh session for this exact user) but is worth guarding
    unconditionally rather than trusting that invariant from a different module.
    """
    raw_cookie = sessions_module.read_session_cookie(request)
    current_row = sessions_module.verify(db, raw_cookie) if raw_cookie else None
    if current_row is not None and current_row.user_id == user.user_id:
        return sessions_module.rotate(db, current_row, request=request, method=method)
    return sessions_module.issue(db, user, method=method, request=request)


def _finish_oauth(
    db: Session,
    request: Request,
    *,
    provider: str,
    claims: Claims,
    txn: OAuthTransaction,
    settings: AuthSettings,
    given_name: str | None = None,
    family_name: str | None = None,
) -> Response:
    """Apply the account-linking rules, issue a session, and redirect — shared by both
    providers' callbacks. See ``accounts/linking.py`` for the rules themselves."""
    try:
        if txn.intent == "link":
            link_user = db.get(User, txn.link_user_id) if txn.link_user_id else None
            if link_user is None:
                raise _oauth_error(
                    "oauth_failed",
                    "Your session ended before linking finished. Sign in and try again.",
                    settings=settings,
                )
            link_to(db, link_user, provider, claims)
            user = link_user
        else:
            user = resolve_login(db, provider, claims)
    except LinkConflict as conflict:
        db.commit()
        target = f"/auth/link-conflict?provider={provider}&reason={conflict.kind}"
        if conflict.email_masked:
            target += f"&email={quote(conflict.email_masked)}"
        response = RedirectResponse(url=target, status_code=303)
        _clear_oauth_cookie(response, settings)
        return response

    # Apple's name arrives, if ever, exactly once — persist it now, in the same transaction
    # that (on a fresh account) just created this user, and never overwrite an already-stored
    # name with an absence on a later callback (accounts/providers/apple.py).
    if (given_name or family_name) and user.given_name is None and user.family_name is None:
        user.given_name = given_name
        user.family_name = family_name
        if user.display_name is None:
            user.display_name = " ".join(p for p in (given_name, family_name) if p) or None
        db.add(user)

    issued = _rotate_current_session_or_issue(db, request, user, provider)
    db.commit()

    target_path = txn.next_path if txn.intent != "link" else f"/settings?linked={provider}"
    response = RedirectResponse(url=urljoin(settings.public_base_url, target_path), status_code=303)
    sessions_module.set_session_cookie(response, issued)
    _clear_oauth_cookie(response, settings)
    return response


def _exchange_google_code(settings: AuthSettings, code: str, txn: OAuthTransaction) -> dict:
    doc = discovery(google_provider.ISSUER)
    response = httpx.post(
        doc["token_endpoint"],
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": txn.redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": txn.code_verifier,
        },
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()


@router.get("/auth/google/callback")
def google_callback(
    request: Request,
    db: SessionDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    settings = get_auth_settings()
    txn = _consume_transaction(db, request, settings)
    db.commit()

    if txn is None:
        raise _oauth_error(
            "oauth_transaction_missing",
            "Your sign-in took too long or cookies were blocked. Try again.",
            settings=settings,
        )
    if txn.provider != "google":
        raise _oauth_error(
            "oauth_failed", "That sign-in link was for a different provider.", settings=settings
        )
    if error:
        response = RedirectResponse(url="/sign-in", status_code=303)
        _clear_oauth_cookie(response, settings)
        return response
    if not code or not state or not hmac.compare_digest(_hash_hex(state), txn.state_hash):
        raise _oauth_error(
            "oauth_state_mismatch", "Your sign-in could not be verified. Try again.",
            settings=settings,
        )

    try:
        token_response = _exchange_google_code(settings, code, txn)
        claims = verify_id_token(
            token_response["id_token"],
            issuers=set(google_provider.ACCEPTED_ISSUERS),
            audience=settings.google_client_id,
            algorithms=google_provider.ALGORITHMS,
            nonce=txn.nonce,
            jwks_uri=discovery(google_provider.ISSUER)["jwks_uri"],
        )
    except (OIDCError, httpx.HTTPError, KeyError) as exc:
        logger.warning("google oauth callback failed: %s", exc)
        raise _oauth_error(
            "oauth_failed", "Google sign-in failed. Try again.", settings=settings
        ) from exc

    return _finish_oauth(db, request, provider="google", claims=claims, txn=txn, settings=settings)


def _exchange_apple_code(settings: AuthSettings, code: str, txn: OAuthTransaction) -> dict:
    response = httpx.post(
        apple_provider.TOKEN,
        data={
            "code": code,
            "client_id": settings.apple_services_id,
            "client_secret": apple_provider.client_secret(settings),
            "redirect_uri": txn.redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": txn.code_verifier,
        },
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()


@router.get("/auth/apple/callback")
def apple_callback_wrong_method() -> None:
    """Apple's Services ID must be configured with ``response_mode=form_post``; landing here
    on a plain ``GET`` means it is not, and the fix is worth naming precisely rather than
    letting the request fall through to the SPA and look like a blank page."""
    raise ApiError(
        "apple_response_mode",
        "Set the Services ID's response mode to form_post.",
        http_status=400,
        recoverable=False,
    )


@router.post("/auth/apple/callback")
async def apple_callback(request: Request, db: SessionDep) -> Response:
    settings = get_auth_settings()
    raw_body = await request.body()
    fields = apple_provider.parse_callback_body(raw_body)

    txn = _consume_transaction(db, request, settings)
    db.commit()

    if fields.get("error") == "user_cancelled_authorize":
        response = RedirectResponse(url="/sign-in", status_code=303)
        _clear_oauth_cookie(response, settings)
        return response
    if txn is None:
        raise _oauth_error(
            "oauth_transaction_missing",
            "Your sign-in took too long or cookies were blocked. Try again.",
            settings=settings,
        )
    if txn.provider != "apple":
        raise _oauth_error(
            "oauth_failed", "That sign-in link was for a different provider.", settings=settings
        )

    state = fields.get("state")
    if not state or not hmac.compare_digest(_hash_hex(state), txn.state_hash):
        raise _oauth_error(
            "oauth_state_mismatch", "Your sign-in could not be verified. Try again.",
            settings=settings,
        )

    # Parsed before anything else can fail: this is the only chance this name will ever be
    # sent (accounts/providers/apple.py::first_authorization_name).
    given_name, family_name = apple_provider.first_authorization_name(fields.get("user"))

    code = fields.get("code")
    if not code:
        raise _oauth_error(
            "oauth_failed", "Apple did not return an authorization code.", settings=settings
        )

    try:
        token_response = _exchange_apple_code(settings, code, txn)
        claims = verify_id_token(
            token_response["id_token"],
            issuers={apple_provider.APPLE_ISSUER},
            audience=settings.apple_services_id,
            algorithms=apple_provider.ALGORITHMS,
            nonce=txn.nonce,
            jwks_uri=apple_provider.JWKS,
        )
    except (OIDCError, httpx.HTTPError, KeyError) as exc:
        logger.warning("apple oauth callback failed: %s", exc)
        raise _oauth_error(
            "oauth_failed", "Apple sign-in failed. Try again.", settings=settings
        ) from exc

    return _finish_oauth(
        db, request, provider="apple", claims=claims, txn=txn, settings=settings,
        given_name=given_name, family_name=family_name,
    )
