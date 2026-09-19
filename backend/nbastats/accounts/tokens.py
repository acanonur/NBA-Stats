"""Single-use, hashed tokens for the three things a user does by following a link: verifying
an email address, resetting a forgotten password, and confirming a changed one.

Only ``sha256(raw)`` is ever stored — exactly the discipline ``sessions.py`` applies to a
session secret — so a leaked database backup cannot be used to forge a still-valid reset link.
Minting a token of a given purpose consumes every other outstanding token of that purpose for
the same user first, so requesting three password resets in a row leaves exactly one valid
link (the newest); an inbox with three stale reset emails in it is not a place any of them
should still work. Tokens travel in a query string on a plain ``GET`` that immediately
redirects with ``303`` after consuming the token (never on a page that renders normally with
the token still in the URL), and every response in this service carries
``Referrer-Policy: no-referrer`` — between them, a token cannot leak through a ``Referer``
header to whatever the redirect target happens to load next.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import utcnow
from .models import AuthToken

__all__ = ["PURPOSES", "TTL", "mint", "consume", "payload_of"]

PURPOSES = ("verify", "reset", "email_change")

TTL: dict[str, timedelta] = {
    "verify": timedelta(days=3),
    "reset": timedelta(hours=1),
    "email_change": timedelta(hours=1),
}


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mint(db: Session, user_id: str, purpose: str, *, payload: dict[str, Any] | None = None) -> str:
    """Mint a fresh single-use token of ``purpose`` for ``user_id`` and return the raw value.

    Consumes (stamps ``consumed_at``) every other outstanding, unconsumed token of the same
    purpose for the same user first — see the module docstring.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"unknown token purpose {purpose!r}; expected one of {PURPOSES}")
    now = utcnow()
    db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user_id,
            AuthToken.purpose == purpose,
            AuthToken.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    raw = secrets.token_urlsafe(32)
    row = AuthToken(
        token_id=uuid4().hex,
        user_id=user_id,
        purpose=purpose,
        token_hash=_hash(raw),
        payload_json=json.dumps(payload, separators=(",", ":")) if payload is not None else None,
        created_at=now,
        expires_at=now + TTL[purpose],
    )
    db.add(row)
    db.flush()
    return raw


def consume(db: Session, purpose: str, raw: str) -> AuthToken | None:
    """Look up, validate and single-use-consume a token, all inside the caller's transaction.

    Returns ``None`` for anything that is not a live, unconsumed, unexpired token of the
    requested ``purpose`` — an unknown token, a token of the wrong purpose, an already-used
    token, and an expired token are deliberately indistinguishable to the caller, since
    "invalid or expired" is the only thing a client can act on anyway.
    """
    if not raw:
        return None
    row = db.execute(
        select(AuthToken).where(AuthToken.token_hash == _hash(raw))
    ).scalar_one_or_none()
    if row is None or row.purpose != purpose:
        return None
    now = utcnow()
    if row.consumed_at is not None or row.expires_at <= now:
        return None
    row.consumed_at = now
    db.add(row)
    db.flush()
    return row


def payload_of(row: AuthToken) -> dict[str, Any]:
    """Decode ``payload_json`` back to a dict; ``{}`` when there is none."""
    if not row.payload_json:
        return {}
    return json.loads(row.payload_json)
