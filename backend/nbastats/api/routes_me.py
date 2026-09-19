"""``/v1/me/*`` (account settings, sessions, identities, export, erasure) and
``/v1/dashboards/*`` (user-scoped saved dashboards) — WEB_DESIGN.md §4.2-§4.3.

Both live in one module because both are "the signed-in account's own stuff", and because that
is exactly how the design document splits WP2's ownership from WP1's: WP1 built the primitives
this module orchestrates — ``accounts.passwords``, ``accounts.sessions``, ``accounts.tokens``,
``accounts.linking``, ``accounts.mail`` — and none of the security reasoning for *why* a
password hash is never compared in variable time, or why a session rotates on every privilege
change, lives here. This module's job is narrower: turn an HTTP request into exactly the right
sequence of calls into those primitives, with the right freshness and CSRF checks in front of
each one.

Why every dependency function here is named for what it proves, not for what it blocks
------------------------------------------------------------------------------------------
* ``Depends(require_user)`` — a live session. Every route under here needs at least this much.
* ``Depends(require_write)`` — a live session **and** CSRF (``check_origin`` + ``check_csrf``).
  Every unsafe method (``POST``/``PATCH``/``PUT``/``DELETE``) depends on this, because a
  same-origin-only synchroniser token is this design's entire CSRF defence (WEB_DESIGN.md §2.4)
  — there is no CSRF cookie anywhere to fall back on.
* ``Depends(require_fresh_user)`` — a live session **less than ten minutes past sign-in**.
  Reserved for the four actions that change *who can get in*: setting or changing a password,
  changing the email address, and deleting the account. A route that needs both freshness and
  CSRF (all four of those) depends on **both** ``require_fresh_user`` and ``require_write`` —
  they check different things and neither substitutes for the other.

Why the link-start route calls into ``routes_auth`` instead of rebuilding the flow
------------------------------------------------------------------------------------
``POST /v1/me/identities/{provider}/start`` has to build the exact authorization-code-plus-PKCE
transaction ``routes_auth.oauth_start`` already builds — the state/handle split, the snapshotted
redirect URI, the provider-specific authorize parameters — and a second, independently-written
copy of that logic is precisely the drift ``WEB_DESIGN.md`` §6 exists to prevent. So the shared
builder lives in ``routes_auth`` as ``begin_oauth(..., intent="link", link_user_id=…)`` and this
module calls it. The import is made *inside* the handler, not at module scope, because
``routes_auth`` pulls in the OIDC provider modules (and therefore ``pyjwt``): the dashboard
routes below must keep serving in a checkout where that optional dependency is absent, which is
why ``app.py`` includes these two modules independently in the first place.

Leaving that route out — as this module originally did — was not a neutral omission. It made
``_finish_oauth``'s ``intent == "link"`` branch and the whole of ``linking.link_to`` (rule 6)
dead code, and it meant the only remedy the product offers for the commonest OIDC collision
(rule 5: "sign in with your password first, then link Google from Settings") named an action the
API did not expose, permanently barring every password account from attaching a provider.

One response object per handler, always
------------------------------------------
Any handler here that both sets a cookie and returns a body must set the cookie on the object it
**returns**. FastAPI merges the injected ``response: Response`` sub-response's headers only when
the endpoint's return value is *not* itself a ``Response``
(``fastapi/routing.py``: ``if isinstance(raw_response, Response): response = raw_response``), and
every handler in this module returns a ``JSONResponse`` or a ``Response``. Writing a
``Set-Cookie`` onto the injected object and returning a different one silently drops it — which
is how changing a password used to revoke the caller's session and then never deliver the
replacement.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import catalog
from ..accounts import require_fresh_user, require_user, require_write
from ..accounts import linking, mail, passwords, tokens
from ..accounts import sessions as sessions_module
from ..accounts import store
from ..accounts.config import get_auth_settings
from ..accounts.models import (
    PROVIDERS,
    THEME_PREFERENCES,
    AuthSession,
    User,
    UserIdentity,
)
from ..accounts.layouts import LayoutTooNew, NotALayout, UnreadableLayout
from ..db import utcnow
from . import errors
from .deps import RateLimiter, SessionDep
from .errors import ApiError

__all__ = ["router"]

router = APIRouter(tags=["me"])

#: ``GET /v1/me/export`` — its own bucket, well below the shared per-IP limiter, because a
#: full-account export is orders of magnitude more expensive than any other route here.
_EXPORT_LIMIT = RateLimiter(limit=5, window=3600)


def reset_me_route_limiters() -> None:
    """Mirrors ``routes_auth.reset_auth_route_limiters`` for the one local bucket this module
    keeps; tests that exercise ``/v1/me/export`` repeatedly must call this."""
    _EXPORT_LIMIT.reset()


class _MeModel(BaseModel):
    """Same camelCase-alias contract as ``api/schemas.py::ContractModel``, defined locally so
    this module's request bodies do not require an edit to a file no WP in this build owns."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PatchMeBody(_MeModel):
    display_name: Optional[str] = None
    favorite_player_id: Optional[int] = None
    favorite_team_id: Optional[int] = None
    theme: Optional[str] = None
    selected_dashboard_id: Optional[str] = None


class PasswordBody(_MeModel):
    current_password: Optional[str] = None
    new_password: str


class EmailBody(_MeModel):
    new_email: str
    current_password: Optional[str] = None


class ConfirmDeleteBody(_MeModel):
    confirm: str


class CreateDashboardBody(_MeModel):
    layout: Optional[dict[str, Any]] = None
    preset_key: Optional[str] = None


class ReorderBody(_MeModel):
    layout_ids: list[str]


# --------------------------------------------------------------------------- shared helpers


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat() + "Z"


def _user_out(db: SessionDep, user: User) -> dict[str, Any]:
    """The ``User`` wire shape (WEB_DESIGN.md §4.1) — kept in this module rather than imported
    from ``routes_auth`` (whose equivalent, ``_user_out``, is private on purpose) so neither
    module has to expose an internal helper across the WP1/WP2 file boundary."""
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


def _no_store(payload: Any, *, status_code: int = 200) -> JSONResponse:
    response = JSONResponse(content=payload, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


def _invalid_layout(exc: Exception) -> ApiError:
    if isinstance(exc, LayoutTooNew):
        return ApiError("layout_too_new", str(exc), http_status=409, recoverable=False)
    if isinstance(exc, NotALayout):
        return ApiError("not_a_layout", str(exc), http_status=400, recoverable=False)
    if isinstance(exc, UnreadableLayout):
        return ApiError("not_a_layout", str(exc), http_status=400, recoverable=False)
    raise exc  # pragma: no cover - defensive; every caller only ever passes the three above


# --------------------------------------------------------------------------- §4.2 account


@router.get("/me")
def get_me(db: SessionDep, user: User = Depends(require_user)) -> Response:
    return _no_store(_user_out(db, user))


@router.patch("/me")
def patch_me(
    request: Request,
    db: SessionDep,
    body: PatchMeBody,
    user: User = Depends(require_user),
    _write: AuthSession = Depends(require_write),
) -> Response:
    """An explicit allowlist, applied by **what the caller sent**, not by what is non-null.

    Every field on ``PatchMeBody`` is ``Optional[...] = None``, so testing ``is not None``
    made "omitted" and "sent as ``null``" the same thing — and since no other route writes
    these columns, a favourite, a display name or a selected dashboard could be set and then
    never cleared again. That is load-bearing now that ``routes_dashboard`` substitutes the
    stored favourite into every request whose ``context.favoritePlayerId`` is null: an
    unremovable pin follows the reader onto every ``$favorite_player`` widget forever.
    ``model_fields_set`` is pydantic's record of which keys were actually present in the JSON,
    so an explicit ``null`` clears and an absent key is left alone.

    ``selectedDashboardId`` is additionally checked against this user's own dashboards —
    it used to be stored verbatim, including somebody else's layout id.
    """
    sent = body.model_fields_set
    if "display_name" in sent:
        user.display_name = (body.display_name or "").strip()[:128] or None
    if "favorite_player_id" in sent:
        user.favorite_player_id = body.favorite_player_id
    if "favorite_team_id" in sent:
        user.favorite_team_id = body.favorite_team_id
    if "theme" in sent:
        if body.theme not in THEME_PREFERENCES:
            raise errors.bad_request(
                f"{body.theme!r} is not one of {list(THEME_PREFERENCES)}.", "theme"
            )
        user.theme_preference = body.theme
    if "selected_dashboard_id" in sent:
        if body.selected_dashboard_id is not None and (
            store.get_dashboard(db, user.user_id, body.selected_dashboard_id) is None
        ):
            raise errors.bad_request(
                "That dashboard does not exist on this account.", "selectedDashboardId"
            )
        user.selected_dashboard_id = body.selected_dashboard_id
    user.updated_at = utcnow()
    db.add(user)
    db.commit()
    return _no_store(_user_out(db, user))


@router.post("/me/password")
def change_password(
    db: SessionDep,
    body: PasswordBody,
    request: Request,
    user: User = Depends(require_fresh_user),
    _write: AuthSession = Depends(require_write),
) -> Response:
    """``currentPassword`` is required whenever the account already has a password, and may be
    omitted only to set a *first* password on a provider-only account."""
    if user.password_hash is not None:
        if not body.current_password or not passwords.verify_password(
            body.current_password, user.password_hash
        ):
            raise ApiError(
                "invalid_credentials", "That current password is not correct.", http_status=401
            )

    problem = passwords.policy_problem(body.new_password, email=user.email)
    if problem:
        raise errors.bad_request(problem, field="newPassword")

    user.password_hash = passwords.hash_password(body.new_password)
    user.password_changed_at = utcnow()
    db.add(user)
    # §4.2: "rotates this session, revokes all others". Spare the caller's own row from the
    # sweep and then rotate it, rather than revoking everything and issuing an unrelated
    # session: `rotate` stamps `rotated_from` so the chain stays auditable, and it cannot
    # revoke a row twice.
    sessions_module.revoke_all(db, user.user_id, except_id=_write.session_id)
    live = db.get(AuthSession, _write.session_id)
    issued = sessions_module.rotate(db, live, request=request, method="password")
    db.commit()
    # One response object. Writing the cookie onto the injected `response` and returning a
    # different one drops the header entirely (see `logout` in routes_auth for the FastAPI
    # mechanics): changing your password used to revoke the session the browser was holding
    # and then never deliver the replacement, so every request after it 401'd.
    out = _no_store({"csrfToken": issued.csrf_token})
    sessions_module.set_session_cookie(out, issued)
    return out


@router.post("/me/email", status_code=202)
def change_email(
    db: SessionDep,
    body: EmailBody,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_fresh_user),
    _write: AuthSession = Depends(require_write),
) -> Response:
    """``202`` with an identical body whether or not the address is already in use — and, now,
    in identical *time*. The send used to happen inline on the free-address branch only, so with
    ``HARDWOOD_MAILER=smtp`` a stopwatch on a single request separated "free" from "taken" by
    the length of an SMTP round trip: an authenticated but unprivileged caller could walk a list
    of addresses and learn which were registered. ``BackgroundTasks`` runs the send *after* the
    response is composed, exactly as ``routes_auth.signup`` and ``forgot_password`` do for the
    same reason (WEB_DESIGN.md §4.9)."""
    new_lookup = body.new_email.strip().lower()
    if not new_lookup or "@" not in new_lookup:
        raise errors.bad_request("That does not look like an email address.", "newEmail")

    if user.password_hash is not None:
        if not body.current_password or not passwords.verify_password(
            body.current_password, user.password_hash
        ):
            raise ApiError(
                "invalid_credentials", "That current password is not correct.", http_status=401
            )

    existing = db.execute(
        select(User).where(User.email_lookup == new_lookup, User.deleted_at.is_(None))
    ).scalar_one_or_none()
    settings = get_auth_settings()
    if existing is None or existing.user_id == user.user_id:
        raw = tokens.mint(db, user.user_id, "email_change", payload={"newEmail": body.new_email})
        db.commit()
        link = f"{settings.public_base_url.rstrip('/')}/v1/me/email/confirm?token={raw}"
        background_tasks.add_task(mail.send_email_change_email, body.new_email, link)
    else:
        db.commit()  # nothing to persist; still answer identically either way
    return _no_store({"status": "checkYourEmail"}, status_code=202)


@router.get("/me/email/confirm")
def confirm_email_change(token: str, request: Request, db: SessionDep) -> RedirectResponse:
    """Consume the emailed token, move the address, and revoke every **other** session.

    Three things this handler has to get right, each of which it previously did not:

    * The address is re-checked for a collision *here*, not only when the token was minted an
      hour earlier. ``users.email_lookup`` is ``UNIQUE``, so somebody else signing up with the
      pending address in the meantime turned the commit into an unhandled ``IntegrityError`` —
      a 500 whose rollback also undid ``tokens.consume``'s ``consumed_at``, so the link stayed
      "valid" and answered 500 on every retry, forever. The ``try/except IntegrityError``
      around the commit is the backstop for the genuine race the re-check cannot close.
    * ``revoke_all`` spares the caller's own session (§4.2: "revokes all *other* sessions").
      Revoking everything signed the person out of the browser they were standing in.
    * That surviving session is **rotated**, because an email change is a privilege change
      (§2.3) — but only when the cookie on this request already belongs to *this* user, so
      following somebody else's confirmation link can never mint a session for a stranger.
    """
    settings = get_auth_settings()
    expired = RedirectResponse(
        url=f"{settings.public_base_url.rstrip('/')}/verify?expired=1", status_code=303
    )

    row = tokens.consume(db, "email_change", token)
    user = db.get(User, row.user_id) if row is not None else None
    if row is None or user is None or user.deleted_at is not None:
        db.commit()
        return expired

    payload = tokens.payload_of(row)
    new_email = payload.get("newEmail")
    now = utcnow()
    if new_email:
        new_lookup = new_email.strip().lower()
        taken = db.execute(
            select(User).where(
                User.email_lookup == new_lookup,
                User.deleted_at.is_(None),
                User.user_id != user.user_id,
            )
        ).scalar_one_or_none()
        if taken is not None:
            db.commit()  # the token is spent either way; it named an address someone else took
            return RedirectResponse(
                url=f"{settings.public_base_url.rstrip('/')}/settings?emailTaken=1",
                status_code=303,
            )
        user.email = new_email
        user.email_lookup = new_lookup
        user.email_verified_at = now
        user.updated_at = now
        db.add(user)

    caller = sessions_module.verify(db, sessions_module.read_session_cookie(request))
    caller_is_this_user = caller is not None and caller.user_id == user.user_id
    sessions_module.revoke_all(
        db, user.user_id, except_id=caller.session_id if caller_is_this_user else None
    )
    issued = (
        sessions_module.rotate(db, caller, request=request, method=caller.auth_method)
        if caller_is_this_user
        else None
    )
    try:
        db.commit()
    except IntegrityError:
        # The unique index on `email_lookup` is the last word; losing the race is not a 500.
        db.rollback()
        return RedirectResponse(
            url=f"{settings.public_base_url.rstrip('/')}/settings?emailTaken=1", status_code=303
        )

    response = RedirectResponse(
        url=f"{settings.public_base_url.rstrip('/')}/settings?emailChanged=1", status_code=303
    )
    if issued is not None:
        sessions_module.set_session_cookie(response, issued)
    return response


@router.get("/me/sessions")
def list_sessions(request: Request, db: SessionDep, user: User = Depends(require_user)) -> Response:
    current: AuthSession = request.state.auth_session
    rows = db.execute(
        select(AuthSession)
        .where(AuthSession.user_id == user.user_id, AuthSession.revoked_at.is_(None))
        .order_by(AuthSession.last_seen_at.desc())
    ).scalars().all()
    return _no_store(
        {
            "sessions": [
                {
                    "sessionId": row.session_id,
                    "createdAt": _iso(row.created_at),
                    "lastSeenAt": _iso(row.last_seen_at),
                    "userAgent": row.user_agent,
                    "ipPrefix": row.ip_prefix,
                    "authMethod": row.auth_method,
                    "current": row.session_id == current.session_id,
                }
                for row in rows
            ]
        }
    )


@router.delete("/me/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: str,
    db: SessionDep,
    user: User = Depends(require_user),
    _write: AuthSession = Depends(require_write),
) -> Response:
    row = db.execute(
        select(AuthSession).where(
            AuthSession.session_id == session_id, AuthSession.user_id == user.user_id
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError("not_found", "No such session.", http_status=404)
    sessions_module.revoke(db, row)
    db.commit()
    return Response(status_code=204)


@router.post("/me/identities/{provider}/start")
def start_identity_link(
    provider: str,
    request: Request,
    db: SessionDep,
    user: User = Depends(require_fresh_user),
    _write: AuthSession = Depends(require_write),
) -> Response:
    """Begin an ``intent="link"`` OAuth transaction for the signed-in user.

    A **POST**, not a ``GET``: §6.4 names the link-flow account-capture attack (attacker starts
    a link flow, hands the victim the authorize URL, the victim's Google identity ends up bound
    to the attacker's account) and closes it three ways at once — CSRF on the start, a *fresh*
    session, and a callback that needs the ``hw_oauth`` handle this response sets in the
    victim's own browser.

    Without this route the whole ``link`` half of the design was unreachable: ``oauth_start``
    hardcoded ``intent="signin"``, so ``_finish_oauth``'s link branch and ``linking.link_to``
    (rule 6) were dead code — and the *only* remedy the product offers for the commonest OIDC
    collision, rule 5's "sign in with your password first, then link Google from Settings",
    named an action the API did not expose. Every password account was permanently barred from
    ever attaching a provider.

    ``routes_auth`` is imported here rather than at module scope on purpose: it pulls in the
    provider modules (and therefore ``pyjwt``), and ``routes_me``'s dashboard routes must keep
    working in a checkout where that optional dependency is absent — ``app.py`` includes each
    of these modules independently for exactly that reason.
    """
    try:
        from . import routes_auth
    except ImportError as exc:  # pragma: no cover - only without the optional OIDC dependency
        raise ApiError(
            "provider_not_configured",
            "Provider sign-in is not available in this build.",
            http_status=404,
        ) from exc

    authorize_url, handle = routes_auth.begin_oauth(
        db, request, provider, intent="link", link_user_id=user.user_id
    )
    out = _no_store({"redirectUrl": authorize_url})
    routes_auth.set_oauth_cookie(out, handle)
    return out


@router.delete("/me/identities/{provider}")
def unlink_identity(
    provider: str,
    request: Request,
    db: SessionDep,
    user: User = Depends(require_fresh_user),
    _write: AuthSession = Depends(require_write),
) -> Response:
    """Detach a provider — and evict every other session while doing it.

    ``accounts/sessions.py`` lists "linking or unlinking a provider" among the privilege changes
    that rotate (§2.3), and ``_finish_oauth`` already rotates on *link*; unlinking did nothing at
    all. That asymmetry mattered: removing an unexpected Google entry from Settings is the one
    remediation the UI offers for that provider, and it used to leave a session an attacker had
    obtained completely untouched. Now the caller's own session is rotated and every other one
    is revoked, so the response carries a new cookie and the new CSRF token.
    """
    if provider not in PROVIDERS:
        raise ApiError("not_found", f"{provider!r} is not a known provider.", http_status=404)
    linking.unlink(db, user, provider)  # raises 409 last_credential on the last-credential case
    sessions_module.revoke_all(db, user.user_id, except_id=_write.session_id)
    live = db.get(AuthSession, _write.session_id)
    issued = sessions_module.rotate(db, live, request=request, method=live.auth_method)
    db.commit()
    out = _no_store({"csrfToken": issued.csrf_token})
    sessions_module.set_session_cookie(out, issued)
    return out


@router.get("/me/export")
def export_account(
    request: Request, db: SessionDep, user: User = Depends(require_user)
) -> Response:
    key = f"export:user:{user.user_id}"
    retry_after = _EXPORT_LIMIT.check(key)
    if retry_after:
        raise errors.rate_limited(retry_after)
    return _no_store(
        {
            "account": _user_out(db, user),
            "dashboards": store.export_envelope(db, user.user_id)["layouts"],
            "exportedAt": _iso(utcnow()),
        }
    )


@router.delete("/me", status_code=204)
def delete_account(
    db: SessionDep,
    body: ConfirmDeleteBody,
    user: User = Depends(require_fresh_user),
    _write: AuthSession = Depends(require_write),
) -> Response:
    """Soft-delete, exactly as §4.2 and §2.16 specify: stamp ``deleted_at``, disable the
    account, revoke every session, clear the cookie. ``admin.py purge`` hard-deletes 30 days
    later, through ``accounts.models.delete_user`` — the one erasure primitive.

    This used to call ``delete_user`` inline, which erased the row immediately. Three things
    followed: a mis-click had no recovery path at all where the design promised thirty days
    (and with the default ``HARDWOOD_MAILER=log`` there is not even a mail trail); the
    ``deleted_at``/``is_disabled`` writes two lines above were flushed and then thrown away by
    the ``DELETE``, so the row ``cmd_purge`` selects could never exist and that whole branch was
    dead; and the freed ``email_lookup`` could be re-registered the same second, so a later
    "Sign in with Google" for that address hit linking rule 3 and quietly created a *new*
    verified account at the old address.
    """
    if body.confirm != "DELETE":
        raise errors.bad_request('Send {"confirm":"DELETE"} to erase this account.', "confirm")
    now = utcnow()
    user.deleted_at = now
    user.is_disabled = True
    user.updated_at = now
    db.add(user)
    sessions_module.revoke_all(db, user.user_id)
    db.commit()
    out = Response(status_code=204)
    sessions_module.clear_session_cookie(out)
    return out


# --------------------------------------------------------------------------- §4.3 dashboards


def _dashboard_summary(row: store.UserDashboard) -> dict[str, Any]:
    return {
        "layoutId": row.layout_id,
        "name": row.name,
        "icon": row.icon,
        "accent": row.accent,
        "presentation": row.presentation,
        "presetKey": row.preset_key,
        "isPreset": row.is_preset,
        "widgetCount": row.widget_count,
        "position": row.position,
        "updatedAt": _iso(row.updated_at),
        "revision": row.revision,
    }


def _dashboard_detail(row: store.UserDashboard, notes: list[str]) -> dict[str, Any]:
    return {"layout": json.loads(row.document_json), "revision": row.revision, "notes": notes}


@router.get("/dashboards")
def list_dashboards(db: SessionDep, user: User = Depends(require_user)) -> dict[str, Any]:
    rows = store.list_dashboards(db, user.user_id)
    return {"dashboards": [_dashboard_summary(row) for row in rows]}


@router.post("/dashboards", status_code=201)
def create_dashboard(
    db: SessionDep,
    body: CreateDashboardBody,
    user: User = Depends(require_user),
    _write: AuthSession = Depends(require_write),
) -> Response:
    if body.preset_key is not None:
        try:
            row, notes = store.create_from_preset(db, user.user_id, body.preset_key)
        except store.TooManyDashboards as exc:
            raise ApiError("too_many_dashboards", str(exc), http_status=409, recoverable=False)
        except catalog.UnknownPresetError as exc:
            raise ApiError(
                "not_found", f"No preset with key {body.preset_key!r}.", http_status=404,
                field="presetKey",
            ) from exc
    elif body.layout is not None:
        try:
            row, notes = store.create_dashboard(db, user.user_id, body.layout)
        except store.TooManyDashboards as exc:
            raise ApiError("too_many_dashboards", str(exc), http_status=409, recoverable=False)
        except store.PayloadTooLarge as exc:
            raise ApiError("payload_too_large", str(exc), http_status=413, recoverable=False)
        except (LayoutTooNew, NotALayout, UnreadableLayout) as exc:
            raise _invalid_layout(exc) from exc
    else:
        raise errors.bad_request('Send {"layout": {...}} or {"presetKey": "..."}.', "layout")

    db.commit()
    return JSONResponse(
        status_code=201, content={"layout": json.loads(row.document_json), "revision": row.revision, "notes": notes}
    )


@router.get("/dashboards/export")
def export_dashboards(db: SessionDep, user: User = Depends(require_user)) -> Response:
    envelope = store.export_envelope(db, user.user_id)
    response = JSONResponse(content=envelope)
    response.headers["Content-Disposition"] = 'attachment; filename="Layouts.json"'
    return response


@router.put("/dashboards/order", status_code=204)
def reorder_dashboards(
    db: SessionDep,
    body: ReorderBody,
    user: User = Depends(require_user),
    _write: AuthSession = Depends(require_write),
) -> Response:
    store.reorder(db, user.user_id, body.layout_ids)
    db.commit()
    return Response(status_code=204)


async def bounded_import_payload(request: Request) -> Any:
    """Read, cap and decode ``POST /v1/dashboards/import``'s body.

    A **dependency**, not inline in the handler, for two reasons. First, the cap has to be
    applied before anything parses the payload: declaring the body as ``raw: dict | list`` handed
    the parsing to FastAPI, which made ``store.import_envelope``'s
    ``isinstance(raw, (str, bytes, bytearray))`` guard permanently false (the store only ever saw
    an already-decoded object), so the contracted ``413`` was unreachable and nothing capped the
    request at all — uvicorn has no default body limit and ``app.py`` adds none, so an arbitrarily
    large envelope was read and ``json.loads``-ed into memory by the one supported worker (§4.8).
    Second, it keeps the handler itself a plain ``def``: FastAPI runs an ``async`` dependency on
    the event loop and a ``def`` endpoint in the threadpool, so the migration and the database
    writes stay off the loop and cannot stall ``/v1/sync/stream``.
    """
    raw_body = await request.body()
    if len(raw_body) > store.MAX_IMPORT_BYTES:
        raise ApiError(
            "payload_too_large",
            f"That import is larger than the {store.MAX_IMPORT_BYTES}-byte limit.",
            http_status=413,
            recoverable=False,
        )
    try:
        raw: Any = json.loads(raw_body)
    except ValueError as exc:
        raise errors.bad_request("That import file is not valid JSON.", "body") from exc
    if not isinstance(raw, (dict, list)):
        raise errors.bad_request("That import file does not contain any dashboards.", "body")
    return raw


@router.post("/dashboards/import")
def import_dashboards(
    db: SessionDep,
    raw: Any = Depends(bounded_import_payload),
    user: User = Depends(require_user),
    _write: AuthSession = Depends(require_write),
) -> dict[str, Any]:
    """Import a Layouts.json envelope, a bare array, or one bare layout."""
    try:
        imported, notes, failures = store.import_envelope(db, user.user_id, raw)
    except store.PayloadTooLarge as exc:
        raise ApiError("payload_too_large", str(exc), http_status=413, recoverable=False)
    db.commit()
    return {
        "imported": [_dashboard_summary(row) for row in imported],
        "notes": notes,
        "failures": failures,
    }


@router.get("/dashboards/{layout_id}")
def get_dashboard(
    layout_id: str, db: SessionDep, user: User = Depends(require_user)
) -> Response:
    row = store.get_dashboard(db, user.user_id, layout_id)
    if row is None:
        # Another user's layout id is a 404, never a 403 — see accounts/store.py's docstring.
        raise ApiError("not_found", "No such dashboard.", http_status=404)
    response = JSONResponse(content=_dashboard_detail(row, []))
    response.headers["ETag"] = f'"{row.revision}"'
    return response


@router.put("/dashboards/{layout_id}")
def update_dashboard(
    layout_id: str,
    db: SessionDep,
    body: CreateDashboardBody,
    request: Request,
    user: User = Depends(require_user),
    _write: AuthSession = Depends(require_write),
) -> Response:
    if_match = request.headers.get("if-match")
    if not if_match:
        raise ApiError(
            "precondition_required",
            "PUT requires an If-Match header naming the revision you last read.",
            http_status=428,
            recoverable=True,
        )
    try:
        expected_revision = int(if_match.strip().strip('"'))
    except ValueError:
        raise errors.bad_request("If-Match must name a numeric revision.", "If-Match")

    if body.layout is None:
        raise errors.bad_request('Send {"layout": {...}}.', "layout")

    try:
        row, notes = store.update_dashboard(
            db, user.user_id, layout_id, body.layout, expected_revision=expected_revision
        )
    except store.DashboardNotFound as exc:
        raise ApiError("not_found", "No such dashboard.", http_status=404) from exc
    except store.StaleWrite as exc:
        db.rollback()
        current = exc.current
        response = JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "stale_write",
                    "message": "This dashboard changed on another device.",
                    "recoverable": True,
                    "field": None,
                    "requestId": errors.request_id_of(request),
                },
                "layout": json.loads(current.document_json),
                "revision": current.revision,
            },
        )
        return response
    except store.PayloadTooLarge as exc:
        raise ApiError("payload_too_large", str(exc), http_status=413, recoverable=False)
    except (LayoutTooNew, NotALayout, UnreadableLayout) as exc:
        raise _invalid_layout(exc) from exc

    db.commit()
    response = JSONResponse(
        content={"layout": json.loads(row.document_json), "revision": row.revision, "notes": notes}
    )
    response.headers["ETag"] = f'"{row.revision}"'
    return response


@router.delete("/dashboards/{layout_id}", status_code=204)
def delete_dashboard(
    layout_id: str,
    db: SessionDep,
    user: User = Depends(require_user),
    _write: AuthSession = Depends(require_write),
) -> Response:
    try:
        store.delete_dashboard(db, user.user_id, layout_id)
    except store.DashboardNotFound as exc:
        raise ApiError("not_found", "No such dashboard.", http_status=404) from exc
    db.commit()
    return Response(status_code=204)


@router.post("/dashboards/{layout_id}/restore")
def restore_dashboard(
    layout_id: str,
    db: SessionDep,
    user: User = Depends(require_user),
    _write: AuthSession = Depends(require_write),
) -> dict[str, Any]:
    try:
        row = store.restore_dashboard(db, user.user_id, layout_id)
    except store.DashboardNotFound as exc:
        raise ApiError("not_found", "No such dashboard.", http_status=404) from exc
    db.commit()
    return _dashboard_summary(row)
