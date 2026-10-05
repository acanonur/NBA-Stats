"""Sign in with Apple: the web (``response_mode=form_post``) flow's Apple-specific pieces.

Apple's OAuth is close enough to Google's that ``routes_auth.py`` drives both through the same
authorization-code-plus-PKCE machinery and the same ``providers/oidc.py`` verification — but
three things about Apple are different enough to need their own code, all in this module:

1. **The confidential client "secret" is a JWT we mint ourselves**, not a static string from a
   developer console. Apple issues no client secret at all; instead it trusts a short-lived
   ES256 JWT signed with a private key downloaded once from the Apple Developer portal
   (``AuthKey_<key id>.p8``) and never rotated except by hand. :func:`client_secret` mints one
   good for five minutes — deliberately far short of Apple's own six-month maximum, because a
   credential that authenticates to Apple's token endpoint and that we mint on demand anyway
   gains nothing from a longer lifetime except a longer window in which a leaked key (the
   ``.p8`` file, not the minted JWT) can be used before anyone notices, and no amount of JWT
   lifetime shortening helps if the ``.p8`` itself leaks — only revoking it in the portal does.
2. **The callback is ``response_mode=form_post``**: a cross-site ``POST`` with an
   ``application/x-www-form-urlencoded`` body, which is why :func:`parse_callback_body` uses
   :func:`urllib.parse.parse_qsl` instead of FastAPI's ``Form(...)`` — that needs
   ``python-multipart``, which is not installed and which this design deliberately does not
   add (``WEB_DESIGN.md`` §0.1-G).
3. **The user's name arrives, if ever, exactly once** — on the very first authorization, and
   in practice never again, not even after the user revokes and re-grants access. See
   :func:`first_authorization_name`.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl

import jwt

from ..config import AuthSettings

__all__ = [
    "APPLE_ISSUER",
    "AUTHORIZE",
    "TOKEN",
    "JWKS",
    "ALGORITHMS",
    "SCOPE",
    "client_secret",
    "parse_callback_body",
    "first_authorization_name",
]

APPLE_ISSUER = "https://appleid.apple.com"
AUTHORIZE = "https://appleid.apple.com/auth/authorize"
TOKEN = "https://appleid.apple.com/auth/token"
JWKS = "https://appleid.apple.com/auth/keys"

#: Apple documents ES256; RS256 is accepted defensively in case of an announced future
#: rotation, exactly as it is for Google — neither entry is ever an HS* variant.
ALGORITHMS = ["ES256", "RS256"]

SCOPE = "name email"

#: See point 1 in the module docstring for why this is five minutes, not Apple's maximum.
_CLIENT_SECRET_LIFETIME_SECONDS = 300
#: Re-mint a little before actual expiry so a request already in flight never presents a
#: secret that expires mid-request.
_REISSUE_MARGIN_SECONDS = 30

_lock = threading.Lock()
_cache: dict[str, tuple[float, str]] = {}


def _cache_key(s: AuthSettings) -> str:
    return f"{s.apple_team_id}:{s.apple_key_id}:{s.apple_services_id}:{s.apple_key_file}"


def _load_private_key(s: AuthSettings) -> str:
    if not s.apple_key_file:
        raise RuntimeError("HARDWOOD_APPLE_KEY_FILE is not set")
    return Path(s.apple_key_file).read_text(encoding="utf-8")


def client_secret(s: AuthSettings) -> str:
    """An ES256 JWT we sign ourselves: ``{"alg":"ES256","kid":<key id>}`` /
    ``{"iss":<team id>,"iat":now,"exp":now+300,"aud":"https://appleid.apple.com",
    "sub":<services id>}``. Minted on demand and cached in-process for the lifetime above;
    never logged, never persisted to disk or the database.
    """
    key = _cache_key(s)
    now = time.monotonic()
    fresh_for = _CLIENT_SECRET_LIFETIME_SECONDS - _REISSUE_MARGIN_SECONDS
    with _lock:
        cached = _cache.get(key)
        if cached is not None and now - cached[0] < fresh_for:
            return cached[1]

    private_key = _load_private_key(s)
    minted_at = int(time.time())
    token = jwt.encode(
        {
            "iss": s.apple_team_id,
            "iat": minted_at,
            "exp": minted_at + _CLIENT_SECRET_LIFETIME_SECONDS,
            "aud": APPLE_ISSUER,
            "sub": s.apple_services_id,
        },
        private_key,
        algorithm="ES256",
        headers={"kid": s.apple_key_id},
    )
    with _lock:
        _cache[key] = (now, token)
    return token


def parse_callback_body(body: bytes) -> dict[str, str]:
    """Parse Apple's ``form_post`` body without FastAPI's ``Form(...)`` (which needs
    ``python-multipart``, not installed here — see the module docstring)."""
    pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, strict_parsing=False)
    return dict(pairs)


def first_authorization_name(user_field: str | None) -> tuple[str | None, str | None]:
    """Apple sends ``{"name": {"firstName", "lastName"}, "email": "..."}`` as the ``user``
    form field on the very first consent, and in practice never again — not even after the
    user revokes access in their Apple ID settings and re-authorises. Parse it defensively (a
    malformed or absent field must never fail the sign-in) and persist whatever comes back in
    the same transaction that creates the user or identity row, since there will not be a
    second chance. An absent ``user`` field on a later callback means *absent*, never "the
    user cleared their name" — callers must not overwrite an already-stored name with
    ``(None, None)``.
    """
    if not user_field:
        return None, None
    try:
        data = json.loads(user_field)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    name = data.get("name")
    if not isinstance(name, dict):
        return None, None
    first = name.get("firstName")
    last = name.get("lastName")
    first = first.strip() if isinstance(first, str) and first.strip() else None
    last = last.strip() if isinstance(last, str) and last.strip() else None
    return first, last
