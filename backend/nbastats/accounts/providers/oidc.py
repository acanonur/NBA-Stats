"""Shared OIDC plumbing: discovery-document and JWKS caching, and ID-token verification.

Google and Apple both hand us a signed ID token and both need the same three defensive
properties, so they share one implementation rather than each rolling their own:

1. **An algorithm allowlist with no ``HS*`` member.** This is the whole defence against the
   classic "algorithm confusion" attack: a server that accepts both ``RS256`` (asymmetric) and
   ``HS256`` (symmetric, keyed by a shared secret) can be fed a token signed with ``alg: HS256``
   whose "signature" is just an HMAC computed with the provider's *public* RSA key treated as
   an HMAC secret — a key anyone can download. :func:`verify_id_token` refuses to even attempt
   verification if the caller's allowlist contains one, so this class of bug cannot be
   reintroduced by a future edit that widens the allowlist "just to be safe."
2. **Signature verification against the provider's own JWKS**, fetched over HTTPS and cached
   in-process for twelve hours — not skipped, per the design's judge table (`WEB_DESIGN.md`
   §0's "Judge disagreements, decided"): PyJWT and ``cryptography`` are already installed, so
   there is no dependency cost to paying for real verification.
3. **A bounded response to an unknown ``kid``.** A JWKS lookup miss forces at most one forced
   refresh per five minutes per ``jwks_uri`` — enough to pick up a real key rotation quickly,
   not so eager that a flood of callbacks carrying a bogus ``kid`` (accidental or adversarial)
   turns into a flood of outbound requests to the identity provider, which would be an
   amplification lever pointed at someone else's infrastructure.
"""
from __future__ import annotations

import hmac
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

__all__ = ["Claims", "OIDCError", "discovery", "jwks_key", "verify_id_token"]

_HTTP_TIMEOUT = 10.0
_DISCOVERY_TTL_SECONDS = 12 * 3600
_JWKS_TTL_SECONDS = 12 * 3600
_KID_MISS_REFRESH_COOLDOWN_SECONDS = 300

_lock = threading.Lock()
_discovery_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_last_forced_jwks_refresh: dict[str, float] = {}


class OIDCError(Exception):
    """Any failure fetching provider metadata or validating an ID token. Callers turn this
    into one generic ``oauth_failed`` HTTP response — a struggling identity provider, or
    somebody poking at the callback with a malformed token, must never become a detailed error
    oracle."""


@dataclass(frozen=True, slots=True)
class Claims:
    """The handful of ID-token claims this product actually uses, extracted and normalised
    once so nothing downstream re-parses a raw JWT payload."""

    subject: str
    email: str | None
    email_verified: bool
    name: str | None
    raw: dict[str, Any]


def discovery(issuer: str) -> dict[str, Any]:
    """The provider's ``/.well-known/openid-configuration`` document, cached for 12 hours."""
    now = time.monotonic()
    with _lock:
        cached = _discovery_cache.get(issuer)
        if cached is not None and now - cached[0] < _DISCOVERY_TTL_SECONDS:
            return cached[1]
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    doc = _get_json(url)
    with _lock:
        _discovery_cache[issuer] = (now, doc)
    return doc


def _get_json(url: str) -> dict[str, Any]:
    try:
        response = httpx.get(url, timeout=_HTTP_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OIDCError(f"could not fetch {url}: {exc}") from exc


def _find_key(jwks_doc: dict[str, Any], kid: str) -> Any:
    for jwk in jwks_doc.get("keys", ()):
        if jwk.get("kid") == kid:
            return jwt.PyJWK.from_dict(jwk)
    return None


def jwks_key(jwks_uri: str, kid: str) -> Any:
    """The signing key named ``kid`` from ``jwks_uri``'s key set, cached for 12 hours — with a
    bounded, cooldown-limited refresh on a miss. See the module docstring, point 3."""
    now = time.monotonic()
    with _lock:
        cached = _jwks_cache.get(jwks_uri)
    if cached is None or now - cached[0] >= _JWKS_TTL_SECONDS:
        doc = _get_json(jwks_uri)
        with _lock:
            _jwks_cache[jwks_uri] = (now, doc)
    else:
        doc = cached[1]

    key = _find_key(doc, kid)
    if key is not None:
        return key

    with _lock:
        last_forced = _last_forced_jwks_refresh.get(jwks_uri, float("-inf"))
        allowed_now = now - last_forced >= _KID_MISS_REFRESH_COOLDOWN_SECONDS
        if allowed_now:
            _last_forced_jwks_refresh[jwks_uri] = now
    if not allowed_now:
        raise OIDCError(
            f"no signing key {kid!r} at {jwks_uri}, and it was refreshed too recently to retry"
        )

    doc = _get_json(jwks_uri)
    with _lock:
        _jwks_cache[jwks_uri] = (now, doc)
    key = _find_key(doc, kid)
    if key is None:
        raise OIDCError(f"no signing key {kid!r} at {jwks_uri}")
    return key


def _truthy(value: Any) -> bool:
    """Google sends a real JSON boolean for ``email_verified``; Apple sends the *string*
    ``"true"``/``"false"``. Both must mean the same thing here."""
    return value is True or value == "true"


def verify_id_token(
    token: str,
    *,
    issuers: set[str],
    audience: str,
    algorithms: list[str],
    nonce: str,
    jwks_uri: str,
) -> Claims:
    """Verify ``token``'s signature and claims, returning the :class:`Claims` we act on.

    ``algorithms`` must be an explicit, short allowlist containing no ``HS*`` variant (see the
    module docstring, point 1). Verifies: signature (against ``jwks_uri``, keyed by the
    token's own ``kid``), ``exp``/``iat``/``aud``/``iss``/``sub`` are present, ``iss`` is one of
    ``issuers``, ``sub`` is non-empty, ``azp`` (when present) equals ``audience``, ``aud``
    matches ``audience`` whether it arrives as a bare string or a one-element list, and the
    token's ``nonce`` matches ``nonce`` in constant time.
    """
    if not algorithms or any(alg.upper().startswith("HS") for alg in algorithms):
        raise OIDCError("algorithm allowlist must be non-empty and exclude every HS* variant")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise OIDCError(f"malformed ID token: {exc}") from exc
    kid = header.get("kid")
    if not kid:
        raise OIDCError("ID token header has no kid")

    signing_key = jwks_key(jwks_uri, kid)
    try:
        claims = jwt.decode(
            token,
            # `signing_key.key` (the underlying cryptography key object), not the `PyJWK`
            # wrapper itself: the version of PyJWT pinned here (see accounts/config.py's
            # dependency comment) does not accept a bare `PyJWK` as `decode()`'s `key`
            # argument, only the concrete key material — passing the wrapper raises
            # "Expecting a PEM-formatted key" instead of verifying anything.
            key=signing_key.key,
            algorithms=algorithms,
            audience=audience,
            leeway=60,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise OIDCError(f"ID token failed verification: {exc}") from exc

    issuer = claims.get("iss")
    if issuer not in issuers:
        raise OIDCError(f"unexpected issuer {issuer!r}")

    subject = claims.get("sub")
    if not subject:
        raise OIDCError("ID token has an empty sub")

    azp = claims.get("azp")
    if azp is not None and azp != audience:
        raise OIDCError("azp does not match our client id")

    aud = claims.get("aud")
    aud_ok = aud == audience or (isinstance(aud, list) and aud == [audience])
    if not aud_ok:
        raise OIDCError("aud does not match our client id")

    token_nonce = claims.get("nonce")
    if not token_nonce or not hmac.compare_digest(str(token_nonce), nonce):
        raise OIDCError("nonce mismatch")

    return Claims(
        subject=str(subject),
        email=claims.get("email"),
        email_verified=_truthy(claims.get("email_verified", False)),
        name=claims.get("name"),
        raw=claims,
    )
