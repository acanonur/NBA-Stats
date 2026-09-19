"""Provider registry, redirect-safety, and "which sign-in buttons should the client show".

``safe_next`` is the one function in this package with real teeth of its own — it decides
whether a ``?next=`` query parameter (which path to land on after a successful sign-in) can be
trusted enough to redirect to. It is a copy of the same defensive shape as any open-redirect
guard, spelled out fully in its docstring, because getting this one wrong turns Google's or
Apple's own sign-in page into a phishing redirector for this service's own users.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from ..config import AuthSettings
from ..models import PROVIDERS

__all__ = ["PROVIDERS", "safe_next", "provider_status"]

#: A single leading ``/`` not followed by another ``/`` or a ``\`` (both of which a browser
#: will treat as the start of a protocol-relative URL, i.e. ``//evil.com`` or ``/\evil.com``
#: navigate off-site exactly like ``https://evil.com`` would), then up to 511 characters from
#: an allowlist that excludes control characters and quotes.
_SAFE_NEXT = re.compile(r"^/(?![/\\])[A-Za-z0-9._~!$&'()*+,;=:@%/?#-]{0,511}$")

_DISALLOWED_CHARS = ("\r", "\n", "\t", "\x00")


def safe_next(raw: str | None) -> str:
    """Return a same-origin path safe to redirect to, or ``"/"``.

    Rejects, in this order:

    * anything containing a literal CR, LF, TAB or NUL (header/log injection material, and
      never a legitimate part of a path);
    * anything whose percent-decoded form differs from the raw value in its first two
      characters — this is what actually stops ``/%5Cevil.com`` (decodes to ``/\\evil.com``),
      ``/%2f%2fevil.com`` (decodes to ``//evil.com``) and ``/%09//evil.com`` (decodes to a
      leading tab then ``//evil.com``): the raw forms would otherwise pass the regex below
      unchanged (``%`` is a legal path character), and the framework has already performed one
      layer of percent-decoding by the time this function sees ``raw``, so decoding again here
      is specifically catching a *second*, attacker-supplied layer of encoding;
    * anything the regex above does not match, which is what actually rejects the plain,
      undecorated ``//evil.com`` and ``/\\evil.com``.

    The result is stored on the ``oauth_transactions`` row at ``/start`` time and never read
    back from the callback URL — a callback carries only the opaque, single-use ``state`` and
    ``hw_oauth`` handle, never the redirect target itself.
    """
    if not raw:
        return "/"
    if any(ch in raw for ch in _DISALLOWED_CHARS):
        return "/"
    decoded = unquote(raw)
    if decoded[:2] != raw[:2]:
        return "/"
    if not _SAFE_NEXT.match(raw):
        return "/"
    return raw


def _apple_key_readable(path: str | None) -> bool:
    if not path:
        return False
    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


def provider_status(s: AuthSettings) -> dict[str, dict[str, Any]]:
    """``{"google": {"enabled": bool, "reason": str | None}, "apple": {...}}``.

    Computed fresh from ``s`` on every call, never cached at import time, so editing
    ``backend/.env`` and restarting the process is enough to light up a provider button — no
    rebuild, no explicit cache-bust call anywhere in this package.
    """
    google_enabled = bool(s.google_client_id and s.google_client_secret)
    google_reason = None
    if not google_enabled:
        google_reason = (
            "HARDWOOD_GOOGLE_CLIENT_ID and HARDWOOD_GOOGLE_CLIENT_SECRET must both be set."
        )

    https_base = s.public_base_url.startswith("https://")
    apple_configured = bool(
        s.apple_services_id
        and s.apple_team_id
        and s.apple_key_id
        and _apple_key_readable(s.apple_key_file)
    )
    apple_enabled = apple_configured and https_base
    apple_reason = None
    if not apple_enabled:
        if not https_base:
            apple_reason = (
                "Apple sign-in needs an https HARDWOOD_PUBLIC_BASE_URL: response_mode="
                "form_post requires a Secure, SameSite=None cookie, which requires https."
            )
        else:
            apple_reason = (
                "HARDWOOD_APPLE_SERVICES_ID, _TEAM_ID, _KEY_ID and _KEY_FILE (a readable "
                "path) must all be set."
            )

    return {
        "google": {"enabled": google_enabled, "reason": google_reason},
        "apple": {"enabled": apple_enabled, "reason": apple_reason},
    }
