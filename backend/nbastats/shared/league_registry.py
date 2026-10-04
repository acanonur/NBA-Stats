"""A late-bound lookup from a league key to the things only that league's package can supply.

The EuroLeague is sealed: its code, its database and its routes live in
``nbastats/euroleague/``, and nothing outside that package may import it (an AST test
enforces this). Yet a few places outside it legitimately need to *ask* the EuroLeague
something without importing it: ``GET /v1/leagues`` has to list it, the widget layer has to
open a session on its store, and the widget config validator has to check that a club code
exists. This registry is the seam. The EuroLeague registers a :class:`LeagueProvider` when it
starts; everyone else looks one up by key and treats "not registered" as an ordinary,
reportable state rather than an import error.

Why a registry and not an import
--------------------------------
An import would make the NBA side depend on the EuroLeague side existing, fail the whole
application when the EuroLeague is disabled or misconfigured, and let an NBA module reach a
EuroLeague table by accident. A registry inverts it: the dependency points at this tiny
neutral module, a missing provider is a value (``None``, or a ``league_unavailable``
condition), and disabling the EuroLeague is simply not registering it.

What a provider supplies
------------------------
* ``session_factory``: a zero-argument callable returning a new database session on that
  league's store. The caller owns the session and closes it; the usual pattern is
  ``with session_for("euroleague") as session: ...``, which works for any session type that
  is a context manager (SQLAlchemy's is). This module neither imports SQLAlchemy nor knows
  what the session is.
* ``describe``: a zero-argument callable returning the league's row for ``GET /v1/leagues``
  (``key``, ``name``, ``apiPrefix``, ``enabled``, ``state``, ``reason``, ``isDemo``,
  ``currentSeason``, ``syncVersion``, ``dataThrough``, ``regulationMinutes``, ``perModes``,
  ``positionBuckets``, ``features``). It must not raise, but :func:`league_entry` survives it
  if it does.
* ``team_exists`` (optional): ``club_code -> bool``, so a widget config naming a club can be
  validated against the live store. When it is absent, existence cannot be checked and
  :func:`team_exists` says so by returning ``None``.

Thread safety
-------------
The registry is a dict behind a lock; lookups and registrations may happen from request
threads and the worker at once.

:func:`clear` exists for tests, which must not leak a provider from one test into the next.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Final, Mapping

from .league_profile import LEAGUE_KEYS, get_profile, is_league_key

__all__ = [
    "LeagueProvider",
    "LeagueUnavailableError",
    "register",
    "unregister",
    "clear",
    "get",
    "is_registered",
    "registered_keys",
    "session_for",
    "league_entry",
    "team_exists",
]


class LeagueUnavailableError(LookupError):
    """The league is not registered, or its provider cannot answer.

    ``code`` is the API error code the route layer maps this to (HTTP 503), and ``reason`` is
    the sentence a client can show.
    """

    code: Final = "league_unavailable"

    def __init__(self, key: str, reason: str) -> None:
        super().__init__(f"{key}: {reason}")
        self.key = key
        self.reason = reason


@dataclass(frozen=True)
class LeagueProvider:
    """What one league's package registers so that others can reach it without importing it."""

    key: str
    session_factory: Callable[[], Any]
    describe: Callable[[], Mapping[str, Any]]
    team_exists: Callable[[str], bool] | None = None


_LOCK = threading.Lock()
_PROVIDERS: dict[str, LeagueProvider] = {}


def register(provider: LeagueProvider) -> None:
    """Register (or replace) the provider for ``provider.key``.

    Replacing is allowed so a restart inside one process, or a test, can register again.
    Raises ``ValueError`` for a key that is not a known league.
    """
    if not is_league_key(provider.key):
        raise ValueError(
            f"cannot register unknown league {provider.key!r}; expected one of "
            f"{', '.join(LEAGUE_KEYS)}"
        )
    with _LOCK:
        _PROVIDERS[provider.key] = provider


def unregister(key: str) -> None:
    """Remove ``key``'s provider; unregistering something absent is not an error."""
    with _LOCK:
        _PROVIDERS.pop(key, None)


def clear() -> None:
    """Remove every provider. For tests."""
    with _LOCK:
        _PROVIDERS.clear()


def get(key: str) -> LeagueProvider | None:
    """The provider for ``key``, or ``None`` when that league is not registered."""
    with _LOCK:
        return _PROVIDERS.get(key)


def is_registered(key: str) -> bool:
    return get(key) is not None


def registered_keys() -> tuple[str, ...]:
    """The registered league keys, in canonical league order."""
    with _LOCK:
        return tuple(key for key in LEAGUE_KEYS if key in _PROVIDERS)


def session_for(key: str) -> Any:
    """A new session on ``key``'s store, opened by its provider; the caller closes it.

    Raises :class:`LeagueUnavailableError` when the league is not registered or its factory
    fails, so a resolver can turn either into the recoverable per-widget ``league_unavailable``
    error instead of a 500.
    """
    provider = get(key)
    if provider is None:
        raise LeagueUnavailableError(key, "this league is not available in this deployment")
    try:
        return provider.session_factory()
    except Exception as exc:  # the provider owns the store; any failure means "unavailable"
        raise LeagueUnavailableError(key, f"the league's store could not be opened: {exc}") from exc


def league_entry(key: str) -> dict[str, Any]:
    """The ``GET /v1/leagues`` row for ``key``, always complete.

    A registered league answers through its own ``describe``. An unregistered one, or a
    provider whose ``describe`` raises, yields a disabled row built from the league's
    profile, so a client can still show the league as unavailable with the reason, and can
    still read its units.
    """
    profile = get_profile(key)
    provider = get(key)
    base: dict[str, Any] = {
        "key": profile.key,
        "name": profile.name,
        "apiPrefix": profile.api_prefix,
        "enabled": False,
        "state": "unavailable",
        "reason": "This league is not available in this deployment.",
        "isDemo": False,
        "currentSeason": None,
        "syncVersion": None,
        "dataThrough": None,
        "regulationMinutes": profile.regulation_minutes,
        "perModes": list(profile.per_modes),
        "positionBuckets": list(profile.position_buckets),
        "features": [],
    }
    if provider is None:
        return base
    try:
        described = dict(provider.describe())
    except Exception as exc:
        base["state"] = "error"
        base["reason"] = f"The league could not describe itself: {exc}"
        return base
    base.update(described)
    base["key"] = profile.key  # a provider cannot rename or re-route itself
    base["apiPrefix"] = profile.api_prefix
    return base


def team_exists(key: str, team_code: str) -> bool | None:
    """Whether ``team_code`` names a team in ``key``'s store; ``None`` when that cannot be said.

    ``None`` means "no provider, or the provider has no checker, or the check failed": the
    caller must not read it as "does not exist".
    """
    provider = get(key)
    if provider is None or provider.team_exists is None:
        return None
    try:
        return bool(provider.team_exists(team_code))
    except Exception:
        return None
