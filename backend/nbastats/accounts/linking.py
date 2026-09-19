"""The six account-linking rules of ``WEB_DESIGN.md`` §6.4, and the three identity-takeover
paths they exist to close.

Read this module alongside §6.4 before changing anything in it: every branch below corresponds
to exactly one numbered rule, in order, and the ordering is load-bearing — rule 1 (an existing
identity row) must be checked before anything email-based has a chance to run at all, because
``sub`` is the only identifier either provider promises is stable.

The three takeover paths, and where each is closed
--------------------------------------------------------
1. **Unverified-email takeover.** A provider that lets a user assert an arbitrary, unverified
   address must never be able to reach an *existing* account through it. Rule 2 below refuses
   to even look at ``email_lookup`` when the claim's email is absent, unverified, or an Apple
   private-relay address — it creates a brand-new, emailless-or-relay-only account instead.
   Closed in :func:`resolve_login`, the "no identity row" branch, before any ``SELECT`` against
   ``email_lookup`` runs.
2. **Pre-registration / reverse takeover.** An attacker who registers a password account at a
   victim's address *before* the victim ever uses "Sign in with Google" must not have that
   account auto-adopted once the victim does. Rule 4 only auto-links when the **existing** row
   already has no password *and* its own ``email_verified_at`` is set — and rule 2's refusal to
   ever write ``email_verified_at`` from an unverified claim means an attacker cannot forge
   that second condition by simply claiming to be verified. Rule 5 catches every remaining
   case (a password on file, or an unverified existing row) with a password challenge instead
   of a silent merge.
3. **Link-flow account capture.** The classic version of this attack starts a "link Google to
   my account" flow, then hands the *victim* the authorization URL carrying the *attacker's*
   ``state`` — if the callback simply linked whatever identity came back to whichever user
   started the flow, the victim's Google identity would end up permanently bound to the
   attacker's Hardwood account. Closed by three independent controls, none of which lives in
   this file: the link-start endpoint is a CSRF-protected ``POST`` requiring a **fresh**
   session (``sessions.require_fresh_user``), and the callback can only complete for whichever
   browser is still holding the matching ``hw_oauth`` handle cookie — which the victim, in this
   scenario, never received. :func:`link_to` itself only ever attaches an identity to the
   user object its caller already resolved through that machinery; it never re-derives "who is
   this for" from anything in the request.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..db import utcnow
from .models import User, UserIdentity
from .providers.oidc import Claims

__all__ = ["LinkConflict", "resolve_login", "link_to", "unlink"]


class LinkConflict(Exception):
    """Raised instead of ever silently merging or re-pointing an identity.

    ``kind`` is one of ``"password_account"`` (rule 5, an existing password on file),
    ``"unverified_account"`` (rule 5, the existing row's own email was never verified), or
    ``"identity_taken"`` (rule 6, the ``(provider, subject)`` pair already belongs to somebody
    else). ``email_masked`` is safe to put in a redirect URL's query string.
    """

    def __init__(self, kind: str, email_masked: str | None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.email_masked = email_masked


def _normalize_email(email: str) -> str:
    return unicodedata.normalize("NFKC", email).strip().lower()


def _mask_email(email: str | None) -> str | None:
    if not email:
        return None
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    return f"{local[:1]}***@{domain}"


def _is_private_relay(claims: Claims) -> bool:
    """Apple's ``is_private_email`` ID-token claim arrives as a real boolean or as the string
    ``"true"``/``"false"`` depending on the SDK version — the same inconsistency
    ``email_verified`` has, which is why both are normalised the same way. The address suffix
    is checked too as a backstop: relying on a claim alone means a future provider that reuses
    the field name differently could slip an address past this check."""
    raw_flag = claims.raw.get("is_private_email")
    if raw_flag is True or raw_flag == "true":
        return True
    return bool(claims.email and claims.email.lower().endswith("@privaterelay.appleid.com"))


def _attach_identity(
    db: Session, user: User, provider: str, claims: Claims, now: datetime
) -> UserIdentity:
    row = UserIdentity(
        identity_id=uuid4().hex,
        user_id=user.user_id,
        provider=provider,
        subject=claims.subject,
        email_at_link=claims.email,
        email_verified=claims.email_verified,
        created_at=now,
        last_login_at=now,
    )
    db.add(row)
    db.flush()
    return row


def resolve_login(db: Session, provider: str, claims: Claims) -> User:
    """Apply the six rules in order. Raises :class:`LinkConflict` rather than ever merging
    silently (rule 5); rule 6 — linking from inside an already-authenticated session — is
    :func:`link_to`, not this function, since it needs a fresh session the route enforces."""
    now = utcnow()

    # Rule 1: (provider, subject) already known -> sign in as that user. `sub` is the only
    # identifier either provider promises never changes; email is explicitly not.
    identity = db.execute(
        select(UserIdentity).where(
            UserIdentity.provider == provider, UserIdentity.subject == claims.subject
        )
    ).scalar_one_or_none()
    if identity is not None:
        user = db.get(User, identity.user_id)
        if user is None or user.deleted_at is not None:
            # The identity outlived its user, which delete_user() should make impossible —
            # fail closed rather than resurrect an account nobody asked to keep.
            raise LinkConflict("identity_taken", None)
        identity.last_login_at = now
        user.last_login_at = now
        db.add(identity)
        db.add(user)
        return user

    is_verified = claims.email_verified
    is_relay = _is_private_relay(claims)

    # Rule 2: no identity row, and the email is absent, unverified, or an Apple private-relay
    # address -> never look at email_lookup at all. See "Unverified-email takeover" above.
    if not claims.email or not is_verified or is_relay:
        user = User(
            user_id=uuid4().hex,
            email=claims.email if is_relay else None,
            email_lookup=_normalize_email(claims.email) if is_relay and claims.email else None,
            is_private_relay=is_relay,
            email_verified_at=None,
            display_name=claims.name,
            theme_preference="system",
            profile_json="{}",
            created_at=now,
            updated_at=now,
            last_login_at=now,
        )
        db.add(user)
        db.flush()
        _attach_identity(db, user, provider, claims, now)
        return user

    # From here: claims.email is present, verified, and not a private-relay address.
    email_lookup = _normalize_email(claims.email)
    existing = db.execute(
        select(User).where(User.email_lookup == email_lookup, User.deleted_at.is_(None))
    ).scalar_one_or_none()

    if existing is None:
        # Rule 3: nobody has this address -> create a fresh, verified account.
        user = User(
            user_id=uuid4().hex,
            email=claims.email,
            email_lookup=email_lookup,
            email_verified_at=now,
            display_name=claims.name,
            theme_preference="system",
            profile_json="{}",
            created_at=now,
            updated_at=now,
            last_login_at=now,
        )
        db.add(user)
        db.flush()
        _attach_identity(db, user, provider, claims, now)
        return user

    if existing.password_hash is None and existing.email_verified_at is not None:
        # Rule 4: both sides have independently proven control of the same mailbox. See
        # "Pre-registration / reverse takeover" above for why both conditions matter.
        existing.last_login_at = now
        db.add(existing)
        _attach_identity(db, existing, provider, claims, now)
        return existing

    # Rule 5: a password exists on the other account, or its own email was never verified ->
    # refuse to link, create no session. The caller redirects to /auth/link-conflict.
    kind = "password_account" if existing.password_hash is not None else "unverified_account"
    raise LinkConflict(kind, _mask_email(existing.email))


def link_to(db: Session, user: User, provider: str, claims: Claims) -> UserIdentity:
    """Rule 6: attach ``provider``/``claims.subject`` to an already-signed-in ``user``.

    The caller is responsible for enforcing that ``user``'s session is fresh and that the
    request was CSRF-checked — this function only enforces the identity-graph invariant: an
    identity is never silently re-pointed from one user to another.
    """
    existing = db.execute(
        select(UserIdentity).where(
            UserIdentity.provider == provider, UserIdentity.subject == claims.subject
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.user_id == user.user_id:
            return existing  # already linked to this same account; nothing to do.
        raise LinkConflict("identity_taken", None)

    same_provider = db.execute(
        select(UserIdentity).where(
            UserIdentity.provider == provider, UserIdentity.user_id == user.user_id
        )
    ).scalar_one_or_none()
    if same_provider is not None:
        # This user already has a *different* identity for this provider. Overwriting it
        # would silently re-point which external account controls this login method, the
        # exact thing this rule exists to prevent.
        raise LinkConflict("identity_taken", None)

    return _attach_identity(db, user, provider, claims, utcnow())


def unlink(db: Session, user: User, provider: str) -> None:
    """Remove ``provider`` from ``user``, unless doing so would leave them with no way to sign
    in at all (409 ``last_credential``)."""
    identity = db.execute(
        select(UserIdentity).where(
            UserIdentity.user_id == user.user_id, UserIdentity.provider == provider
        )
    ).scalar_one_or_none()
    if identity is None:
        return
    other_identities = db.execute(
        select(func.count())
        .select_from(UserIdentity)
        .where(UserIdentity.user_id == user.user_id, UserIdentity.provider != provider)
    ).scalar_one()
    if user.password_hash is None and other_identities == 0:
        raise ApiError(
            "last_credential",
            "Add a password or another sign-in method before removing this one.",
            http_status=409,
            recoverable=True,
        )
    db.delete(identity)
