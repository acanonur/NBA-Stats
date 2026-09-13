"""A polite stats.nba.com client, wrapping the optional ``nba_api`` dependency.

.. warning::

   **stats.nba.com silently blocks datacenter IP ranges.** AWS, GCP and Azure
   addresses — and most GitHub Actions runners — do not get an error or a 403 from
   the Akamai layer in front of the stats API: the request simply hangs until it
   times out. This is the single most common way an otherwise correct ingest
   deployment fails, and no amount of retrying fixes it.

   Run the ingest worker from a **residential connection** (a home box, a Pi, a
   residential-IP VPS), or set ``NBA_API_PROXY`` to a residential pass-through
   proxy. Only the database and the API are safe to host in a cloud region.

   **Browser headers are mandatory.** Without ``User-Agent``, ``Referer:
   https://www.nba.com/``, ``x-nba-stats-origin: stats``, ``x-nba-stats-token:
   true`` and ``Accept-Language``, responses come back empty rather than failing
   loudly. ``nba_api`` bundles a working set; :data:`DEFAULT_HEADERS` restates
   them here so they can be overridden per deployment when the upstream changes.

Three operating modes
---------------------
``live``      ``nba_api`` is installed and calls go to stats.nba.com.
``fixture``   ``HARDWOOD_INGEST_FIXTURES`` (or ``fixtures_dir=``) points at a
              directory of recorded JSON payloads; nothing touches the network.
              This is how the whole pipeline is tested.
``absent``    ``nba_api`` is not installed. The module still imports cleanly — the
              seeded demo league and the API do not need it — and only an actual
              live call raises :class:`IngestUnavailable`.

Politeness
----------
Rate limits are undocumented; community consensus is roughly one request per
0.6–1.5s. :func:`polite_get` therefore enforces a minimum inter-request delay
(default 1.0s), a 60s timeout, and exponential backoff of ``2**i`` seconds plus
jitter between retries. Every call emits one structured log line and updates
:class:`RateLimitStats`, so a run's request budget is auditable after the fact.

Fixture naming
--------------
A call records its endpoint and the parameters that identify the payload::

    scoreboardv2__2026-01-02.json
    boxscoretraditionalv3__0022500512.json
    playergamelogs__2025-26__regular-season__advanced__2026-01-02__2026-01-02.json

Parts are lower-cased and non-alphanumerics collapse to ``-``. If the specific
file is absent, the endpoint-wide fallback ``<endpoint>.json`` is used, which
keeps small test corpora small. A miss raises :class:`FixtureMissing` naming both
paths it looked for — a fixture-mode test never silently falls through to the
network.
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..config import get_settings

__all__ = [
    "DEFAULT_HEADERS",
    "DEFAULT_MIN_DELAY_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_RETRIES",
    "FIXTURES_ENV_VAR",
    "IngestError",
    "IngestUnavailable",
    "FixtureMissing",
    "UpstreamUnavailable",
    "RateLimitStats",
    "RateLimiter",
    "StatsClient",
    "polite_get",
    "fixture_name",
    "nba_api_available",
    "log_line",
]

logger = logging.getLogger("nbastats.ingest.client")

#: Environment variable pointing at a directory of recorded JSON payloads.
FIXTURES_ENV_VAR = "HARDWOOD_INGEST_FIXTURES"

DEFAULT_MIN_DELAY_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_RETRIES = 5

#: The headers stats.nba.com requires. Sending anything less gets empty responses
#: rather than an error, which is why this is a constant and not a nicety.
DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}


class IngestError(RuntimeError):
    """Base class for every ingest-client failure."""


class IngestUnavailable(IngestError):
    """A live call was attempted but ``nba_api`` is not installed.

    Raised lazily, at call time — importing this module without ``nba_api`` is
    fine and supported, because only the live path needs it.
    """


class FixtureMissing(IngestError):
    """Fixture mode is on and the recorded payload for this call does not exist."""


class UpstreamUnavailable(IngestError):
    """stats.nba.com did not answer after every retry was exhausted.

    Maps to the contract's ``upstream_unavailable`` (HTTP 503). On a cloud host
    this is the expected symptom of the datacenter-IP block described above.
    """


def nba_api_available() -> bool:
    """True when the optional ``nba_api`` package can be imported."""
    try:  # pragma: no cover - exercised by whichever environment runs it
        import nba_api  # noqa: F401
    except ImportError:
        return False
    return True


def log_line(event: str, **fields: Any) -> str:
    """Render one structured, greppable log line: ``event=x key=value``."""
    parts = [f"event={event}"]
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value)
        parts.append(f"{key}={text!r}" if " " in text else f"{key}={text}")
    return " ".join(parts)


@dataclass
class RateLimitStats:
    """Per-client request bookkeeping, for the run summary and the ingest log."""

    calls: int = 0
    fixture_calls: int = 0
    live_calls: int = 0
    retries: int = 0
    failures: int = 0
    throttled_seconds: float = 0.0
    request_seconds: float = 0.0
    last_call_at: float | None = None
    by_endpoint: dict[str, int] = field(default_factory=dict)

    def record(self, endpoint: str, *, live: bool, seconds: float) -> None:
        self.calls += 1
        self.live_calls += int(live)
        self.fixture_calls += int(not live)
        self.request_seconds += seconds
        self.last_call_at = time.monotonic()
        self.by_endpoint[endpoint] = self.by_endpoint.get(endpoint, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        """A JSON-safe summary, suitable for a log line or an ingest-log note."""
        return {
            "calls": self.calls,
            "liveCalls": self.live_calls,
            "fixtureCalls": self.fixture_calls,
            "retries": self.retries,
            "failures": self.failures,
            "throttledSeconds": round(self.throttled_seconds, 3),
            "requestSeconds": round(self.request_seconds, 3),
            "byEndpoint": dict(self.by_endpoint),
        }


class RateLimiter:
    """Enforces a minimum gap between requests, and counts what it cost.

    ``sleeper`` and ``clock`` are injectable so tests can assert the pacing
    without actually waiting.
    """

    def __init__(
        self,
        min_delay: float = DEFAULT_MIN_DELAY_SECONDS,
        *,
        stats: RateLimitStats | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_delay = max(0.0, float(min_delay))
        self.stats = stats if stats is not None else RateLimitStats()
        self._sleep = sleeper
        self._clock = clock
        self._last: float | None = None

    def wait(self) -> float:
        """Sleep until ``min_delay`` has passed since the previous request."""
        now = self._clock()
        slept = 0.0
        if self._last is not None and self.min_delay > 0:
            remaining = self.min_delay - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                slept = remaining
                self.stats.throttled_seconds += remaining
        self._last = self._clock()
        return slept

    def backoff(self, attempt: int, rng: random.Random | None = None) -> float:
        """Sleep ``2**attempt`` seconds plus jitter, and return what was slept.

        The exponential term is the research's prescription; the jitter keeps a
        fleet of workers from retrying in lockstep after a shared outage.
        """
        generator = rng or random
        delay = float(2**attempt) + generator.uniform(0.0, 1.0)
        self._sleep(delay)
        self.stats.retries += 1
        self.stats.throttled_seconds += delay
        self._last = self._clock()
        return delay


def polite_get(
    fn: Callable[..., Any],
    *args: Any,
    retries: int = DEFAULT_RETRIES,
    limiter: RateLimiter | None = None,
    rng: random.Random | None = None,
    **kwargs: Any,
) -> Any:
    """Call ``fn(*args, **kwargs)`` politely: paced, retried, and backed off.

    Waits out the minimum inter-request delay, then makes up to ``retries``
    attempts, sleeping ``2**i + jitter`` seconds between them. Every positional
    and keyword argument other than the two reserved control keywords
    (``retries``, ``limiter``, ``rng``) is forwarded untouched, so this wraps an
    ``nba_api`` endpoint constructor directly.

    Raises :class:`UpstreamUnavailable` when every attempt fails, chaining the
    last underlying exception. :class:`IngestError` subclasses — a missing
    fixture, a missing ``nba_api`` — are *not* retried: they cannot succeed on a
    second attempt.
    """
    if retries < 1:
        raise ValueError(f"retries must be >= 1, got {retries}")
    pacer = limiter if limiter is not None else RateLimiter()
    last: Exception | None = None

    for attempt in range(retries):
        pacer.wait()
        try:
            return fn(*args, **kwargs)
        except IngestError:
            raise
        except Exception as exc:  # noqa: BLE001 - upstream raises many shapes
            last = exc
            pacer.stats.failures += 1
            if attempt + 1 >= retries:
                break
            delay = pacer.backoff(attempt, rng)
            logger.warning(
                log_line(
                    "nba_retry",
                    target=getattr(fn, "__name__", type(fn).__name__),
                    attempt=attempt + 1,
                    of=retries,
                    backoff_s=round(delay, 2),
                    error=type(exc).__name__,
                )
            )

    raise UpstreamUnavailable(
        f"stats.nba.com did not answer after {retries} attempts "
        f"({type(last).__name__ if last else 'unknown'}). If this host is in a "
        "datacenter IP range the request was silently dropped — run the worker "
        "from a residential IP or set NBA_API_PROXY."
    ) from last


def _slug(value: Any) -> str:
    """Lower-case a fixture name part, collapsing non-alphanumerics to ``-``."""
    text = str(value).strip().lower()
    out = "".join(char if char.isalnum() else "-" for char in text)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


def fixture_name(endpoint: str, parts: Sequence[Any] = ()) -> str:
    """File name of the recorded payload for one call — see the module docstring."""
    slugs = [_slug(part) for part in parts if part not in (None, "")]
    if not slugs:
        return f"{_slug(endpoint)}.json"
    return f"{_slug(endpoint)}__{'__'.join(slugs)}.json"


class StatsClient:
    """stats.nba.com endpoints this pipeline uses, each returning the raw payload.

    Every method returns the upstream JSON exactly as it arrived — V2's
    ``resultSets`` envelope or V3's nested camelCase object — and leaves all
    translation to :mod:`nbastats.ingest.normalize`. Keeping the raw shape is
    what makes a recorded fixture a faithful test double, and what makes moving
    to a licensed feed a matter of writing one new client rather than editing
    the whole pipeline.

    ``fixtures_dir`` (or ``HARDWOOD_INGEST_FIXTURES``) switches the whole client
    to recorded payloads; ``proxy`` defaults to ``NBA_API_PROXY``.
    """

    def __init__(
        self,
        *,
        fixtures_dir: str | os.PathLike[str] | None = None,
        min_delay: float | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
        headers: Mapping[str, str] | None = None,
        proxy: str | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        settings = get_settings()
        configured = fixtures_dir if fixtures_dir is not None else os.environ.get(
            FIXTURES_ENV_VAR
        )
        self.fixtures_dir: Path | None = Path(configured) if configured else None
        self.timeout = timeout
        self.retries = retries
        self.headers: dict[str, str] = {**DEFAULT_HEADERS, **(headers or {})}
        self.proxy = proxy if proxy is not None else settings.nba_api_proxy
        self.stats = RateLimitStats()
        self.limiter = RateLimiter(
            DEFAULT_MIN_DELAY_SECONDS if min_delay is None else min_delay,
            stats=self.stats,
            sleeper=sleeper,
        )
        self._rng = rng or random.Random()

    # ------------------------------------------------------------------ modes

    @property
    def fixture_mode(self) -> bool:
        """True when calls read recorded JSON instead of the network."""
        return self.fixtures_dir is not None

    def describe(self) -> str:
        """One line naming the mode, for the startup log."""
        if self.fixture_mode:
            return log_line("client_mode", mode="fixture", dir=str(self.fixtures_dir))
        return log_line(
            "client_mode",
            mode="live",
            proxy=self.proxy or "none",
            min_delay_s=self.limiter.min_delay,
            timeout_s=self.timeout,
            nba_api="installed" if nba_api_available() else "missing",
        )

    # ------------------------------------------------------------------ calls

    def _fixture(self, endpoint: str, parts: Sequence[Any]) -> dict[str, Any]:
        assert self.fixtures_dir is not None  # guarded by fixture_mode
        specific = self.fixtures_dir / fixture_name(endpoint, parts)
        fallback = self.fixtures_dir / fixture_name(endpoint, ())
        for path in (specific, fallback):
            if path.is_file():
                with path.open(encoding="utf-8") as handle:
                    return json.load(handle)
        raise FixtureMissing(
            f"No recorded payload for {endpoint}: looked for {specific.name} and "
            f"{fallback.name} in {self.fixtures_dir}"
        )

    def _endpoint_class(self, module_name: str, class_name: str) -> Any:
        """Import one ``nba_api`` endpoint class, or explain why we cannot."""
        try:
            import importlib

            module = importlib.import_module(f"nba_api.stats.endpoints.{module_name}")
        except ImportError as exc:
            raise IngestUnavailable(
                "nba_api is not installed, so live ingest is unavailable. Install "
                "the optional dependency (`pip install nba_api`), or point "
                f"{FIXTURES_ENV_VAR} at a directory of recorded payloads. Every "
                "other part of Hardwood runs without it."
            ) from exc
        try:
            return getattr(module, class_name)
        except AttributeError as exc:  # pragma: no cover - version drift
            raise IngestUnavailable(
                f"The installed nba_api has no {class_name}; endpoints are "
                "deprecated without notice, so pin a known-good version."
            ) from exc

    def call(
        self,
        endpoint: str,
        class_name: str,
        fixture_parts: Sequence[Any] = (),
        **params: Any,
    ) -> dict[str, Any]:
        """Fetch one endpoint's raw payload, politely, with one log line.

        ``endpoint`` is the ``nba_api`` module name (also the fixture prefix);
        ``class_name`` the endpoint class inside it. ``params`` are forwarded to
        that class, plus the headers, proxy and timeout this client was built
        with.
        """
        started = time.monotonic()
        cleaned = {key: value for key, value in params.items() if value is not None}

        if self.fixture_mode:
            payload = self._fixture(endpoint, fixture_parts)
            elapsed = time.monotonic() - started
            self.stats.record(endpoint, live=False, seconds=elapsed)
            logger.info(
                log_line(
                    "nba_call",
                    endpoint=endpoint,
                    mode="fixture",
                    params=",".join(f"{k}={v}" for k, v in sorted(cleaned.items())),
                    ms=round(elapsed * 1000, 1),
                )
            )
            return payload

        endpoint_class = self._endpoint_class(endpoint, class_name)

        def fetch() -> dict[str, Any]:
            response = endpoint_class(
                **cleaned,
                headers=self.headers,
                timeout=self.timeout,
                proxy=self.proxy,
            )
            return response.get_dict()

        payload = polite_get(
            fetch, retries=self.retries, limiter=self.limiter, rng=self._rng
        )
        elapsed = time.monotonic() - started
        self.stats.record(endpoint, live=True, seconds=elapsed)
        logger.info(
            log_line(
                "nba_call",
                endpoint=endpoint,
                mode="live",
                params=",".join(f"{k}={v}" for k, v in sorted(cleaned.items())),
                ms=round(elapsed * 1000, 1),
                calls=self.stats.calls,
            )
        )
        return payload

    # ------------------------------------------------------------- endpoints

    def scoreboard(self, game_date: date, league_id: str = "00") -> dict[str, Any]:
        """``ScoreboardV2`` for one calendar day: the slate and its live state."""
        return self.call(
            "scoreboardv2",
            "ScoreboardV2",
            fixture_parts=(game_date.isoformat(),),
            game_date=game_date.isoformat(),
            league_id=league_id,
            day_offset=0,
        )

    def league_game_log(
        self,
        season: str,
        season_type: str = "Regular Season",
        *,
        player_or_team: str = "T",
        date_from: date | None = None,
        date_to: date | None = None,
        league_id: str = "00",
    ) -> dict[str, Any]:
        """``LeagueGameLog`` — every team (or player) game row for a season.

        This is the enumeration call: one request lists a whole season's games,
        which is why the backfill and the daily bulk path both start here rather
        than asking for games one at a time.
        """
        return self.call(
            "leaguegamelog",
            "LeagueGameLog",
            fixture_parts=(
                season,
                season_type,
                player_or_team,
                date_from.isoformat() if date_from else None,
                date_to.isoformat() if date_to else None,
            ),
            season=season,
            season_type_all_star=season_type,
            player_or_team_abbreviation=player_or_team,
            date_from_nullable=date_from.isoformat() if date_from else None,
            date_to_nullable=date_to.isoformat() if date_to else None,
            league_id=league_id,
            counter=0,
            direction="ASC",
            sorter="DATE",
        )

    def player_game_logs(
        self,
        season: str,
        season_type: str = "Regular Season",
        measure_type: str = "Base",
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        """``PlayerGameLogs`` (plural) — **the efficient one**.

        One call returns every player's game rows for a season; with
        ``measure_type="Advanced"`` it returns the whole advanced slate (ORtg,
        DRtg, AST%, TOV%, eFG%, TS%, USG%, Pace, PIE). About 30 calls cover every
        advanced log from 1996-97 to today, against ~35,000 for the per-game
        box-score endpoint. Never loop ``BoxScoreAdvancedV3`` over a backfill.
        """
        return self.call(
            "playergamelogs",
            "PlayerGameLogs",
            fixture_parts=(
                season,
                season_type,
                measure_type,
                date_from.isoformat() if date_from else None,
                date_to.isoformat() if date_to else None,
            ),
            season_nullable=season,
            season_type_nullable=season_type,
            measure_type_player_game_logs_nullable=measure_type,
            date_from_nullable=date_from.isoformat() if date_from else None,
            date_to_nullable=date_to.isoformat() if date_to else None,
        )

    def box_score_traditional(self, game_id: str) -> dict[str, Any]:
        """``BoxScoreTraditionalV3`` for one game — used only for a just-final game."""
        return self.call(
            "boxscoretraditionalv3",
            "BoxScoreTraditionalV3",
            fixture_parts=(game_id,),
            game_id=game_id,
        )

    def box_score_advanced(self, game_id: str) -> dict[str, Any]:
        """``BoxScoreAdvancedV3`` for one game. Meaningless before 1996-97."""
        return self.call(
            "boxscoreadvancedv3",
            "BoxScoreAdvancedV3",
            fixture_parts=(game_id,),
            game_id=game_id,
        )

    def league_dash_player_stats(
        self,
        season: str,
        season_type: str = "Regular Season",
        *,
        per_mode: str = "PerGame",
        measure_type: str = "Base",
    ) -> dict[str, Any]:
        """``LeagueDashPlayerStats`` — season aggregates straight from the league."""
        return self.call(
            "leaguedashplayerstats",
            "LeagueDashPlayerStats",
            fixture_parts=(season, season_type, per_mode, measure_type),
            season=season,
            season_type_all_star=season_type,
            per_mode_detailed=per_mode,
            measure_type_detailed_defense=measure_type,
        )

    def common_all_players(
        self, season: str, *, only_current_season: bool = False, league_id: str = "00"
    ) -> dict[str, Any]:
        """``CommonAllPlayers`` — the roster, and the spine of the id crosswalk."""
        return self.call(
            "commonallplayers",
            "CommonAllPlayers",
            fixture_parts=(season, "current" if only_current_season else "all"),
            season=season,
            is_only_current_season=1 if only_current_season else 0,
            league_id=league_id,
        )
