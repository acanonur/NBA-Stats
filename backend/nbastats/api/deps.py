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
"""
from __future__ import annotations

import base64
import binascii
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
from ..config import get_settings
from ..db import get_session
from ..models import SEASON_TYPES, Game
from . import errors

__all__ = [
    "API_KEY_HEADER",
    "API_KEY_EXEMPT_PATHS",
    "DEFAULT_RATE_LIMIT",
    "DEFAULT_RATE_WINDOW_SECONDS",
    "CAREER_TOKENS",
    "SessionDep",
    "RateLimiter",
    "get_db",
    "require_api_key",
    "enforce_rate_limit",
    "get_rate_limiter",
    "reset_rate_limiter",
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


def enforce_rate_limit(request: Request) -> None:
    """Dependency: 429 with ``Retry-After`` once a caller exceeds the window.

    Callers are keyed by API key when one is presented and by client address otherwise, so
    one noisy client cannot spend another's budget.
    """
    if request.url.path in API_KEY_EXEMPT_PATHS:
        return
    limiter = get_rate_limiter()
    if not limiter.enabled:
        return
    key = request.headers.get(API_KEY_HEADER) or (
        request.client.host if request.client else "anonymous"
    )
    retry_after = limiter.check(key)
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
