"""Process configuration, read from the environment.

Deliberately dependency-free: a frozen dataclass plus small parsers, rather than
pydantic-settings. ``get_settings()`` is memoised so the rest of the service can call it
from anywhere; tests that mutate the environment call ``reset_settings_cache()``.

Environment variables
---------------------
``DATABASE_URL``             SQLAlchemy URL. SQLite by default; a Postgres URL works
                             without code changes.
``HARDWOOD_API_KEY``         When set, ``/v1`` requires ``X-API-Key`` (except ``/v1/health``).
``HARDWOOD_DEMO_MODE``       Serve the seeded synthetic league and advertise it in ``/v1/health``.
``HARDWOOD_CONTRACTS_DIR``   Override the location of ``contracts/*.json``.
``NBA_API_PROXY``            Optional proxy for the live ingest path.
``INGEST_POLL_SECONDS``      Scoreboard poll cadence during the game window.
``CURRENT_SEASON``           Season the service treats as "latest".
``LOG_LEVEL``                Standard logging level name.
``CORRECTION_WINDOW_DAYS``   How many past days the nightly run re-pulls for stat corrections.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Mapping

__all__ = [
    "Settings",
    "get_settings",
    "reset_settings_cache",
    "current_season_string",
    "season_start_year",
    "DEFAULT_DATABASE_URL",
    "DEFAULT_CURRENT_SEASON",
    "SEASON_ROLLOVER_MONTH",
]

DEFAULT_DATABASE_URL = "sqlite:///./hardwood.db"

#: The season the bundled demo data runs through. The live service overrides this with
#: ``CURRENT_SEASON``; ``current_season_string()`` computes it from the calendar instead.
DEFAULT_CURRENT_SEASON = "2025-26"

#: The NBA league year rolls over on 1 July: everything from July onward belongs to the
#: season that tips off in the autumn of that calendar year.
SEASON_ROLLOVER_MONTH = 7

_TRUTHY = {"1", "true", "t", "yes", "y", "on"}
_FALSEY = {"0", "false", "f", "no", "n", "off", ""}


def _get(environ: Mapping[str, str], name: str) -> str | None:
    raw = environ.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _env_str(environ: Mapping[str, str], name: str, default: str) -> str:
    return _get(environ, name) or default


def _env_optional(environ: Mapping[str, str], name: str) -> str | None:
    return _get(environ, name)


def _env_bool(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _get(environ, name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSEY:
        return False
    raise ValueError(f"{name}={raw!r} is not a boolean")


def _env_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = _get(environ, name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"{name}={raw!r} is not an integer") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable view of the process configuration."""

    database_url: str = DEFAULT_DATABASE_URL
    api_key: str | None = None
    demo_mode: bool = False
    contracts_dir: str | None = None
    nba_api_proxy: str | None = None
    ingest_poll_seconds: int = 60
    current_season: str = DEFAULT_CURRENT_SEASON
    log_level: str = "INFO"
    correction_window_days: int = 3

    @property
    def is_sqlite(self) -> bool:
        """True when the store is SQLite, which needs connect-args the others do not."""
        return self.database_url.startswith("sqlite")

    @property
    def requires_api_key(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        return cls(
            database_url=_env_str(env, "DATABASE_URL", DEFAULT_DATABASE_URL),
            api_key=_env_optional(env, "HARDWOOD_API_KEY"),
            demo_mode=_env_bool(env, "HARDWOOD_DEMO_MODE", False),
            contracts_dir=_env_optional(env, "HARDWOOD_CONTRACTS_DIR"),
            nba_api_proxy=_env_optional(env, "NBA_API_PROXY"),
            ingest_poll_seconds=_env_int(env, "INGEST_POLL_SECONDS", 60),
            current_season=_env_str(env, "CURRENT_SEASON", DEFAULT_CURRENT_SEASON),
            log_level=_env_str(env, "LOG_LEVEL", "INFO").upper(),
            correction_window_days=_env_int(env, "CORRECTION_WINDOW_DAYS", 3),
        )


def season_start_year(today: date | None = None) -> int:
    """Calendar year the NBA season containing ``today`` tipped off in."""
    day = today or date.today()
    return day.year if day.month >= SEASON_ROLLOVER_MONTH else day.year - 1


def current_season_string(today: date | None = None) -> str:
    """NBA-style season string for ``today`` — ``date(2025, 10, 22)`` gives ``"2025-26"``."""
    start = season_start_year(today)
    return f"{start}-{(start + 1) % 100:02d}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, read once from the environment."""
    return Settings.from_env()


def reset_settings_cache() -> None:
    """Drop the memoised settings; used by tests that mutate the environment."""
    get_settings.cache_clear()
