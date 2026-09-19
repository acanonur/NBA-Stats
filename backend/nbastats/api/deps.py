"""Shared request plumbing: the session, the API-key gate, the limiter, query parsing.

Everything here is small, explicit and reusable from any route module, so that no route
re-implements a rule the contract states once:

* ``/v1`` needs ``X-API-Key`` **only** when ``HARDWOOD_API_KEY`` is set, and ``/v1/health``
  never needs it (``contracts/CONTRACT.md`` §1).
* Collection endpoints take ``limit`` and an **opaque** ``cursor``; the cursor is base64url
  JSON so a client cannot meaningfully hand-craft one, and a malformed one is
  ``400 bad_request`` rather than a 500.
* The season literal ``"latest"`` resolves to the newest season the store actually holds —
  not to the calendar — so a demo database seeded through 2025-26 answers honestly.

The rate limiter is deliberately in-process: one uvicorn worker, one counter. It is a
courtesy brake for a single-instance deployment, not a distributed quota. Tune it with
``HARDWOOD_RATE_LIMIT`` (requests per window, ``0`` disables) and
``HARDWOOD_RATE_WINDOW_SECONDS``; both are read here rather than in ``config.py`` because
they are an HTTP-layer concern.

Identity, added for Hardwood Web without touching a byte of the above
--------------------------------------------------------------------------
``require_api_key`` stays exactly as it was — existing tests import it, and the five original
routers plus the widget layer keep working with zero API-key configuration exactly as they do
today. What is new is layered *beside* it: :func:`current_session`, :func:`require_user`,
:func:`require_fresh_user` and :func:`require_write` are re-exported from
``nbastats.accounts`` (WP1's package) rather than implemented here — this module is the one
place that imports them, so every route file reaches identity through ``nbastats.api.deps``
and never has to know which WP1 module actually owns a session row.

That import is wrapped in a ``try/except ImportError`` on purpose. ``nbastats.accounts``
ships its real session/password/OIDC machinery on its own schedule; until it lands (or on a
deployment that never installs the ``[web]`` extra at all), every name below still exists and
behaves exactly like "there is no session" — which is precisely today's behaviour, since today
there is no session mechanism at all. :func:`require_api_key_or_session` is what makes that
concrete: it calls :func:`current_session` first so ``request.state.user`` is populated
whenever a live session exists, then falls back to the *same* key check
:func:`require_api_key` already performs. A deployment with no ``HARDWOOD_API_KEY`` and no
accounts feature therefore behaves byte-for-byte as it did before this module grew a session
concept at all.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import threading
import time
from collections import deque
from datetime import date
from typing import Annotated, Any, Iterator, Mapping, Sequence

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog
from ..accounts.models import User
from ..config import get_settings
from ..db import get_session
from ..models import SEASON_TYPES, Game
from . import errors

__all__ = [
    "API_KEY_HEADER",
    "API_KEY_EXEMPT_PATHS",
    "DEFAULT_RATE_LIMIT",
    "DEFAULT_RATE_WINDOW_SECONDS",
    "DEFAULT_AUTH_RATE_LIMIT",
    "DEFAULT_AUTH_RATE_WINDOW_SECONDS",
    "FRESH_SESSION_SECONDS",
    "CAREER_TOKENS",
    "SessionDep",
    "RateLimiter",
    "get_db",
    "require_api_key",
    "enforce_rate_limit",
    "get_rate_limiter",
    "reset_rate_limiter",
    "current_session",
    "require_api_key_or_session",
    "require_user",
    "require_fresh_user",
    "require_write",
    "get_auth_limiter",
    "reset_auth_limiter",
    "loaded_seasons",
    "current_season",
    "resolve_season",
    "ensure_season_loaded",
    "parse_season_type",
    "parse_iso_date",
    "parse_metric_keys",
    "clamp_limit",
    "encode_cursor",
    "decode_cursor",
]

API_KEY_HEADER = "X-API-Key"

#: Paths that never require a key, whatever ``HARDWOOD_API_KEY`` says. Health has to answer
#: an unauthenticated load balancer.
API_KEY_EXEMPT_PATHS = frozenset({"/v1/health", "/v1/health/"})

DEFAULT_RATE_LIMIT = 600
DEFAULT_RATE_WINDOW_SECONDS = 60

#: Accepted in place of a season string where a whole career is meaningful.
CAREER_TOKENS = frozenset({"career", "all_time", "all-time", "alltime"})


# --------------------------------------------------------------------------- session


def get_db() -> Iterator[Session]:
    """Request-scoped SQLAlchemy session (closed when the response is finished)."""
    yield from get_session()


SessionDep = Annotated[Session, Depends(get_db)]


# --------------------------------------------------------------------------- auth


def require_api_key(request: Request) -> None:
    """Enforce ``X-API-Key`` when the service is configured with one.

    Comparison is constant-time: a timing oracle on a shared key is cheap to avoid.
    """
    settings = get_settings()
    if not settings.requires_api_key:
        return
    if request.url.path in API_KEY_EXEMPT_PATHS:
        return
    supplied = request.headers.get(API_KEY_HEADER)
    expected = settings.api_key or ""
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise errors.unauthorized()


# --------------------------------------------------------------------------- identity

#: How long after ``authenticated_at`` a session counts as "fresh" for a sensitive action
#: (linking or unlinking a provider, changing a password or email, deleting the account).
#: ``WEB_DESIGN.md`` §2.13 fixes this at ten minutes.
FRESH_SESSION_SECONDS = 600

try:  # pragma: no cover - exercised by whichever branch this checkout actually has
    from ..accounts import current_session as _accounts_current_session
    from ..accounts import require_fresh_user as _accounts_require_fresh_user
    from ..accounts import require_user as _accounts_require_user
    from ..accounts import require_write as _accounts_require_write

    current_session = _accounts_current_session
    require_user = _accounts_require_user
    require_fresh_user = _accounts_require_fresh_user
    require_write = _accounts_require_write
except ImportError:  # nbastats.accounts has not landed its session machinery yet.

    def current_session(request: Request) -> None:
        """No accounts feature is installed: there is never a session to find.

        Still stashes ``request.state.user = None`` / ``request.state.auth_session = None`` so
        every downstream read of ``request.state.user`` (``routes_dashboard.py``'s favourites
        substitution, in particular) can use ``getattr(request.state, "user", None)`` without
        caring which branch of this ``try`` ran.
        """
        request.state.user = None
        request.state.auth_session = None
        return None

    def require_user(request: Request) -> "User":
        """401 always: there is no session mechanism to have signed anyone in with."""
        current_session(request)
        raise errors.unauthorized("Sign in to use this feature.")

    def require_fresh_user(request: Request) -> "User":
        return require_user(request)

    def require_write(request: Request) -> None:
        raise errors.unauthorized("Sign in to use this feature.")


def require_api_key_or_session(request: Request) -> None:
    """The guard for the five original routers plus ``routes_dashboard`` / ``routes_leaders``
    / ``routes_fantasy``: an ``X-API-Key`` **or** a live browser session, either is enough.

    Order matters, and it is the whole point of this function rather than a second copy of
    :func:`require_api_key` with one more ``or``: identity is resolved *before* the early
    return that a keyless deployment takes on every request. ``HARDWOOD_API_KEY`` is unset on
    a normal laptop, so a guard that checked the key first and returned immediately when none
    is configured would never populate ``request.state.user`` — and "pin my player" (which
    reads that state from inside a resolver) would silently do nothing in the default
    configuration, the one nearly everyone runs.
    """
    current_session(request)  # always, so request.state.user is set whenever it can be
    settings = get_settings()
    if not settings.requires_api_key:
        return
    if request.url.path in API_KEY_EXEMPT_PATHS:
        return
    supplied = request.headers.get(API_KEY_HEADER)
    expected = settings.api_key or ""
    if supplied and hmac.compare_digest(supplied, expected):
        return
    if getattr(request.state, "auth_session", None) is not None:
        return
    raise errors.unauthorized()


# --------------------------------------------------------------------------- auth rate limit


_auth_limiter: "RateLimiter | None" = None
_auth_limiter_lock = threading.Lock()

#: The most common bucket in ``WEB_DESIGN.md`` §4.8's table (``login:ip:<prefix>``). Auth
#: routes that need a different cadence (``signup:ip`` at 5/hour, ``export:user`` at 5/hour) key
#: their own bucket string but currently share this window; see that module's own docstring
#: once it exists for the exact per-bucket accounting.
DEFAULT_AUTH_RATE_LIMIT = 30
DEFAULT_AUTH_RATE_WINDOW_SECONDS = 900


def get_auth_limiter() -> "RateLimiter":
    """A **second**, independent :class:`RateLimiter` for auth-sensitive endpoints
    (``/v1/auth/*``, ``/v1/me/export``), so a burst of anonymous login attempts cannot spend the
    budget every other ``/v1`` route shares via :func:`get_rate_limiter`."""
    global _auth_limiter
    if _auth_limiter is None:
        with _auth_limiter_lock:
            if _auth_limiter is None:
                _auth_limiter = RateLimiter(
                    limit=int(
                        os.environ.get("HARDWOOD_AUTH_RATE_LIMIT", DEFAULT_AUTH_RATE_LIMIT)
                    ),
                    window=int(
                        os.environ.get(
                            "HARDWOOD_AUTH_RATE_WINDOW_SECONDS", DEFAULT_AUTH_RATE_WINDOW_SECONDS
                        )
                    ),
                )
    return _auth_limiter


def reset_auth_limiter() -> None:
    """Drop the auth limiter so the next call rebuilds it from the environment — mirrors
    :func:`reset_rate_limiter`; tests that exercise auth rate limiting must call this too."""
    global _auth_limiter
    with _auth_limiter_lock:
        _auth_limiter = None


# --------------------------------------------------------------------------- rate limit


class RateLimiter:
    """Fixed-window in-process limiter: at most ``limit`` requests per ``window`` per key.

    ``limit <= 0`` disables it entirely. ``check()`` returns the number of seconds the
    caller should wait, or ``0`` when the request is allowed.
    """

    def __init__(self, limit: int = DEFAULT_RATE_LIMIT, window: int = DEFAULT_RATE_WINDOW_SECONDS):
        self.limit = limit
        self.window = max(1, window)
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def check(self, key: str, now: float | None = None) -> int:
        """Record a hit for ``key``; return ``0`` to allow, else the retry-after seconds."""
        if not self.enabled:
            return 0
        moment = time.monotonic() if now is None else now
        cutoff = moment - self.window
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, int(round(hits[0] + self.window - moment)))
            hits.append(moment)
            if len(self._hits) > 4096:  # keep the table from growing without bound
                self._evict(cutoff)
            return 0

    def _evict(self, cutoff: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            self._hits.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_limiter: RateLimiter | None = None
_limiter_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    """The process-wide limiter, built from the environment on first use."""
    global _limiter
    if _limiter is None:
        with _limiter_lock:
            if _limiter is None:
                _limiter = RateLimiter(
                    limit=int(os.environ.get("HARDWOOD_RATE_LIMIT", DEFAULT_RATE_LIMIT)),
                    window=int(
                        os.environ.get(
                            "HARDWOOD_RATE_WINDOW_SECONDS", DEFAULT_RATE_WINDOW_SECONDS
                        )
                    ),
                )
    return _limiter


def reset_rate_limiter() -> None:
    """Drop the limiter so the next request rebuilds it from the environment."""
    global _limiter
    with _limiter_lock:
        _limiter = None


def _rate_limit_bucket(request: Request) -> str:
    """Which bucket this request spends from.

    The ``X-API-Key`` header is used **only after it has been checked against the configured
    key**. Keying on the raw header turned the limiter off for anyone who bothered to vary it:
    with ``HARDWOOD_API_KEY`` unset — the default, and the shape every web deployment runs —
    nothing ever compared the header to anything, so ``X-API-Key: bucket-1``, ``bucket-2``, …
    bought a fresh allowance per request and the one global brake in front of the single
    worker was gone.

    A validated key still gets its own bucket (a trusted integration should not share a
    /24's budget with a browser), a signed-in caller is keyed on their session so one account
    cannot spend a whole network's allowance, and everyone else falls back to the client
    address.
    """
    settings = get_settings()
    supplied = request.headers.get(API_KEY_HEADER)
    if settings.requires_api_key and supplied:
        expected = settings.api_key or ""
        if hmac.compare_digest(supplied, expected):
            return f"key:{hashlib.sha256(supplied.encode('utf-8')).hexdigest()[:32]}"

    auth_session = getattr(request.state, "auth_session", None)
    session_id = getattr(auth_session, "session_id", None)
    if session_id:
        return f"session:{session_id}"

    return f"addr:{request.client.host if request.client else 'anonymous'}"


def enforce_rate_limit(request: Request) -> None:
    """Dependency: 429 with ``Retry-After`` once a caller exceeds the window.

    See :func:`_rate_limit_bucket` for how a caller is identified — in particular, why an
    unvalidated ``X-API-Key`` header is never the bucket key.
    """
    if request.url.path in API_KEY_EXEMPT_PATHS:
        return
    limiter = get_rate_limiter()
    if not limiter.enabled:
        return
    retry_after = limiter.check(_rate_limit_bucket(request))
    if retry_after:
        raise errors.rate_limited(retry_after)


# --------------------------------------------------------------------------- seasons


def loaded_seasons(session: Session) -> list[str]:
    """Every season the store holds games for, oldest first."""
    seasons = session.execute(select(Game.season).distinct()).scalars().all()
    return sorted({s for s in seasons if s}, key=catalog.season_sort_key)


def current_season(session: Session) -> str:
    """The season ``"latest"`` means: the newest one loaded, else the configured default."""
    seasons = loaded_seasons(session)
    return seasons[-1] if seasons else get_settings().current_season


def resolve_season(
    session: Session,
    season: str | None,
    *,
    field: str = "season",
    allow_career: bool = False,
    default_latest: bool = True,
) -> str | None:
    """Resolve a ``season`` query parameter to a concrete season string.

    ``None`` (when ``default_latest``) and ``"latest"`` become the current season.
    ``"career"`` is passed through as ``"career"`` when ``allow_career``. Anything that is
    not an NBA-style season string is ``400 bad_request``.
    """
    if season is None:
        return current_season(session) if default_latest else None
    value = season.strip()
    if not value:
        return current_season(session) if default_latest else None
    if value.lower() == "latest":
        return current_season(session)
    if value.lower() in CAREER_TOKENS:
        if allow_career:
            return "career"
        raise errors.bad_request(f"{value!r} is not a season for this endpoint.", field)
    if not catalog.is_season_string(value):
        raise errors.bad_request(
            f"{value!r} is not a season such as '2025-26' or 'latest'.", field
        )
    return value


def ensure_season_loaded(session: Session, season: str, *, field: str = "season") -> str:
    """422 ``season_not_loaded`` for a valid season this server has not ingested."""
    if season == "career":
        return season
    if season not in loaded_seasons(session):
        raise errors.season_not_loaded(season, field)
    return season


def parse_season_type(value: str | None, *, field: str = "seasonType") -> str:
    """Validate a season type against the five the contract allows."""
    if value is None or not value.strip():
        return "Regular Season"
    candidate = value.strip()
    for known in SEASON_TYPES:
        if candidate.lower() == known.lower():
            return known
    raise errors.bad_request(
        f"{candidate!r} is not a season type; expected one of {list(SEASON_TYPES)}.", field
    )


def parse_iso_date(value: str, *, field: str = "date") -> date:
    """Parse an ISO calendar date, or ``400 bad_request``."""
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError) as exc:
        raise errors.bad_request(f"{value!r} is not an ISO date such as '2026-01-02'.", field) from exc


# --------------------------------------------------------------------------- metrics


def parse_metric_keys(
    raw: str | None,
    default: Sequence[str],
    *,
    field: str = "metrics",
    scope: str | None = None,
    maximum: int = 24,
) -> list[str]:
    """Parse a comma-separated metric list, validated against ``contracts/metrics.json``.

    Order is the caller's, duplicates are dropped, an empty parameter takes ``default``, and
    an unknown key is ``400 bad_request`` naming the key (it is a client bug, not an era gap
    — a real metric outside its era is served as ``null``, not as an error).
    """
    if raw is None or not raw.strip():
        keys = list(default)
    else:
        keys = [part.strip() for part in raw.split(",") if part.strip()]
    seen: list[str] = []
    for key in keys:
        if not catalog.has_metric(key):
            raise errors.bad_request(f"Unknown metric {key!r}.", field)
        if scope is not None and scope not in catalog.metric(key).get("scope", ()):
            raise errors.bad_request(f"Metric {key!r} is not a {scope} metric.", field)
        if key not in seen:
            seen.append(key)
    if len(seen) > maximum:
        raise errors.bad_request(
            f"{len(seen)} metrics exceeds the maximum of {maximum}.", field
        )
    return seen


# --------------------------------------------------------------------------- pagination


def clamp_limit(value: int | None, default: int, maximum: int) -> int:
    """A usable page size: the default when absent, clamped to ``[1, maximum]``."""
    if value is None:
        return default
    return max(1, min(int(value), maximum))


def encode_cursor(payload: Mapping[str, Any]) -> str:
    """Encode keyset state as an opaque base64url token (no padding)."""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None, *, field: str = "cursor") -> dict[str, Any] | None:
    """Decode a cursor produced by :func:`encode_cursor`; ``None`` passes through.

    Anything unreadable is ``400 bad_request``: a cursor is server-minted, so a broken one
    means the client invented it or truncated ours.
    """
    if cursor is None or not cursor.strip():
        return None
    token = cursor.strip()
    padding = "=" * (-len(token) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(token + padding))
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise errors.bad_request("The cursor is not valid.", field) from exc
    if not isinstance(payload, dict):
        raise errors.bad_request("The cursor is not valid.", field)
    return payload
