"""Google-specific constants for the OIDC authorization-code + PKCE flow.

Everything provider-agnostic (discovery caching, JWKS caching, ID-token verification) lives in
``providers/oidc.py``; this module only names the handful of Google-specific facts that
``routes_auth.py`` needs to drive the flow: which issuer strings Google's ID tokens actually
carry (``https://accounts.google.com`` per the discovery document, but Google's own
documentation also allows the bare host without a scheme, and both have been observed in the
wild), which scope to request, and which signing algorithm to accept.

Scope is deliberately ``openid email``, not ``openid email profile``: this product only ever
needs a stable subject and an email address to identify an account. Asking for less than
Google offers is not caution for its own sake — the privacy page can truthfully say we do not
receive a profile photo or a full contact list, because we never asked for one.
"""
from __future__ import annotations

__all__ = ["ISSUER", "ACCEPTED_ISSUERS", "SCOPE", "ALGORITHMS"]

ISSUER = "https://accounts.google.com"

#: Google's ID tokens have been observed with both forms as ``iss`` in the wild; Google's own
#: documentation confirms both are valid.
ACCEPTED_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})

SCOPE = "openid email"

#: Google signs with RS256 only; no HS* variant is ever acceptable (see ``providers/oidc.py``).
ALGORITHMS = ["RS256"]
