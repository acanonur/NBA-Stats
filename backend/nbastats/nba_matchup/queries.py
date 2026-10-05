"""The NBA read side's data access: one context, loaded once, that every builder shares.

Every matchup, defence table and projection needs the same handful of facts: which season and
season type, what the clock says, which teams and games exist, which model settings are in force,
and how fresh the stats store is. Rather than have a dozen builders each re-read them (and quietly
disagree about "now"), :func:`build_context` reads them once into a :class:`ReadContext` and the
builders take that. The context is also the one place a clock enters the read side: builders never
call ``datetime.now``, so a fixture, a test and the scheduler can all ask "what would this have
said at 11:30 on 3 January" and get a reproducible answer.

This module only *reads* (games, lines, teams, positions, settings) and computes nothing a
statistician would call a number except through the shared pure core (:mod:`nbastats.shared`).
The team-scoring scope is :func:`load_team_games`, and its rule is the design's (section 7.1):
**only final games with both sides' box rows count**. A game that is final but whose team rows
have not arrived yet does not count until they do, which keeps a team's points allowed equal to
what the opponent's team row says it scored.

Two clocks per game, kept apart
-------------------------------
``games.game_date`` is the NBA scheduling day in US Eastern and carries no hour; the real tip-off
lives in ``game_schedule_detail`` and is often absent, because the scoreboard fields that would
fill it are unverified. Three instants are derived from the pair, each for one purpose, and each
errs the same way, towards *not* claiming knowledge it does not have:

``game_start``
    Where a game's *inputs* are cut off. The tip-off if known, else midnight Eastern of the game
    day. Statuses and reports are read up to this instant.
``result_known_by``
    Whether a game's *result* may inform something cut off at an instant: only a game on an
    earlier Eastern game day. A game that tipped at 19:00 is not final by a 19:30 tip-off, so a
    game on the same day never informs another, whether or not its tip-off is known.
``lock_deadline``
    When a frozen projection must already exist: the tip-off if known, else noon Eastern, the
    earliest an NBA game tips. A lock is refused from this instant on.
``game_reference``
    What ``resultPending`` is measured from: the tip-off if known, else 23:59 Eastern.

The "next slate" token
----------------------
``date=next`` means the next Eastern date that has scheduled games, and it is answered from the
store, not from the calendar alone (:func:`next_slate_date`): the first scheduled date on or after
today, and, when the store's schedule is behind the clock (the demo league, or an ingest that has
stalled), the first scheduled date after the newest final game. A client is never told "nothing
is scheduled" because the data is old.

What this module deliberately does not do
-----------------------------------------
It never writes, never opens a session (the caller owns it), and never reads the EuroLeague's
store or imports its package. Nothing here knows about HTTP beyond the clock dependency routes
inject.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any, Callable, Final, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import catalog
from ..api import errors as api_errors
from ..api.errors import ApiError
from ..models import (
    SEASON_TYPES,
    Game,
    GameScheduleDetail,
    Player,
    PlayerPositionSeason,
    SyncState,
    Team,
    TeamGame,
)
from ..nba_intel import settings as intel_settings
from ..nba_intel import store as intel_store
from ..nba_intel.models import (
    NbaIntelModelSetting,
    NbaIntelOverride,
    NbaIntelSnapshot,
    NbaIntelStatus,
)
from ..shared import positions as shared_positions
from ..shared import refs
from ..shared.league_profile import NBA, NBA_KEY
from ..shared.team_form import TeamGame as FormTeamGame

__all__ = [
    "EASTERN",
    "UTC",
    "PROFILE",
    "REGULAR_SEASON",
    "PROJECTABLE_SEASON_TYPES",
    "PROJECTION_FROM",
    "MEMO_SIZE",
    "utc_now",
    "get_now",
    "aware",
    "naive_utc",
    "bad_request",
    "game_not_found",
    "team_not_found",
    "season_not_loaded",
    "invalid_status",
    "internal_error",
    "season_string",
    "season_year",
    "parse_season_type",
    "parse_team_id",
    "loaded_seasons",
    "latest_season",
    "previous_season_of",
    "resolve_season",
    "game_has_result",
    "game_start",
    "lock_deadline",
    "game_reference",
    "primary_bucket",
    "ReadContext",
    "build_context",
    "context_for_game",
    "next_slate_date",
    "latest_final_date",
    "season_of_date",
    "load_team_games",
    "result_day",
    "result_known_by",
    "memoise",
    "clear_memo",
    "digest",
]

PROFILE: Final = NBA
EASTERN: Final = ZoneInfo(PROFILE.schedule_tz)
UTC: Final = timezone.utc

REGULAR_SEASON: Final = "Regular Season"

#: The season types a game can be projected in. Pre-season and all-star games are not scored by
#: the team model: neither is a contest between full-strength rosters.
PROJECTABLE_SEASON_TYPES: Final[tuple[str, ...]] = ("Regular Season", "Play In", "Playoffs")

#: Projections are offered only from the first season with league-wide possession data, the same
#: boundary ``next_game_projection`` uses (``contracts/CONTRACT.md`` section 6).
PROJECTION_FROM: Final = "1996-97"

#: The most computed tables kept between requests. Every key carries the data marks, so a stale
#: entry can never be served; this only bounds memory.
MEMO_SIZE: Final = 12

_SEASON_RE = re.compile(r"^\d{4}-\d{2}$")


# --------------------------------------------------------------------------- errors


def bad_request(message: str, field_name: str | None = None) -> ApiError:
    return api_errors.bad_request(message, field_name)


def game_not_found(game_id: Any) -> ApiError:
    return api_errors.game_not_found(game_id)


def team_not_found(team_id: Any) -> ApiError:
    return api_errors.team_not_found(team_id)


def season_not_loaded(season: str, field_name: str | None = "season") -> ApiError:
    return api_errors.season_not_loaded(season, field_name)


def internal_error(message: str = "An unexpected error occurred.") -> ApiError:
    return api_errors.internal_error(message)


def invalid_status(message: str, field_name: str | None = "status") -> ApiError:
    """400 ``invalid_status``: an availability status outside the five (contract section 7)."""
    return ApiError("invalid_status", message, http_status=400, field=field_name)


# --------------------------------------------------------------------------- time


def utc_now() -> datetime:
    """The clock: aware UTC. The only place the read side asks the system for the time."""
    return datetime.now(UTC)


def get_now() -> datetime:
    """The clock as a request dependency. A test or a fixture holds time still by overriding it
    (``app.dependency_overrides[get_now]``); routes never call ``datetime.now`` themselves."""
    return utc_now()


def aware(value: datetime | None) -> datetime | None:
    """``value`` as aware UTC. A naive datetime is the store's convention and means UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def naive_utc(value: datetime) -> datetime:
    """The store's form of a moment: naive UTC."""
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _eastern(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=EASTERN).astimezone(UTC)


def game_start(game: Game, tipoff_utc: datetime | None) -> datetime:
    """The instant a game's inputs are cut off at: tip-off, else midnight Eastern of its day."""
    return aware(tipoff_utc) or _eastern(game.game_date, time(0, 0))  # type: ignore[return-value]


def result_day(cutoff: datetime) -> date:
    """The Eastern game day an instant falls on: results from games before it are known."""
    return aware(cutoff).astimezone(EASTERN).date()  # type: ignore[union-attr]


def result_known_by(game: Game, cutoff: datetime) -> bool:
    """Whether ``game``'s result may inform anything cut off at ``cutoff``.

    Only games on an earlier Eastern game day count. A game's start is not when its result is
    known: one that tips 30 minutes before another is still being played when the other starts,
    and a game whose tip-off is unknown could have tipped at any hour of its day.
    """
    return game.game_date < result_day(cutoff)


def lock_deadline(game: Game, tipoff_utc: datetime | None) -> datetime:
    """When a locked projection must already exist: tip-off, else noon Eastern of the game day."""
    return aware(tipoff_utc) or _eastern(game.game_date, time(12, 0))  # type: ignore[return-value]


def game_reference(game: Game, tipoff_utc: datetime | None) -> datetime:
    """What ``resultPending`` is measured from: tip-off, else 23:59 Eastern of the game day."""
    return aware(tipoff_utc) or _eastern(game.game_date, time(23, 59))  # type: ignore[return-value]


# --------------------------------------------------------------------------- seasons and ids


def season_string(start_year: int) -> str:
    """``2025`` as ``"2025-26"``."""
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def season_year(season: str) -> int:
    return catalog.season_sort_key(season)


def parse_season_type(raw: str | None, *, field_name: str = "seasonType") -> str:
    """The season type spelled as the store spells it; a blank means the regular season."""
    if raw is None or not raw.strip():
        return REGULAR_SEASON
    candidate = raw.strip()
    for known in SEASON_TYPES:
        if candidate.lower() == known.lower():
            return known
    raise bad_request(
        f"{candidate!r} is not a season type; expected one of {list(SEASON_TYPES)}.", field_name
    )


def parse_team_id(raw: Any) -> int | None:
    """An NBA team id from its wire form (a string of digits, or an int); ``None`` otherwise.

    A EuroLeague club code is not an NBA team id and must read as an *unknown* one, so the
    caller turns ``None`` into ``404 team_not_found`` and a wrong-league id never resolves.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def loaded_seasons(session: Session) -> list[str]:
    """Every season the store holds games for, oldest first."""
    rows = session.execute(select(Game.season).distinct()).scalars().all()
    return sorted({s for s in rows if s and _SEASON_RE.match(s)}, key=catalog.season_sort_key)


def latest_season(session: Session) -> str | None:
    seasons = loaded_seasons(session)
    return seasons[-1] if seasons else None


def previous_season_of(session: Session, season: str) -> str | None:
    """The season immediately before ``season`` if the store holds it, else ``None``.

    Last season means last season: a store that skips a year does not borrow the one before.
    """
    wanted = season_string(season_year(season) - 1)
    return wanted if wanted in loaded_seasons(session) else None


def resolve_season(session: Session, raw: str | None, *, field_name: str = "season") -> str:
    """``latest`` (or nothing) is the newest season the store holds; anything else must be an
    NBA-style season string the store holds. A EuroLeague code such as ``E2026`` is not one."""
    text = (raw or "").strip()
    if not text or text.lower() == "latest":
        found = latest_season(session)
        if found is None:
            raise ApiError(
                "season_not_loaded",
                "No NBA season is loaded yet.",
                http_status=422,
                recoverable=True,
                field=field_name,
            )
        return found
    if not catalog.is_season_string(text):
        raise bad_request(f"{text!r} is not a season such as '2025-26' or 'latest'.", field_name)
    if text not in loaded_seasons(session):
        raise season_not_loaded(text, field_name)
    return text


# --------------------------------------------------------------------------- games


def game_has_result(game: Game) -> bool:
    return game.status == "final" and game.home_pts is not None and game.away_pts is not None


def primary_bucket(position_raw: str | None) -> str | None:
    """``G``, ``F`` or ``C`` for a listed position, or ``None`` when it is not understood.

    A hybrid names its first position first (``G-F`` is a guard-forward), and that is the one a
    ``LeaguePlayerRef`` shows; the half-and-half split lives in the allocation, not on the card.
    """
    weights = shared_positions.normalise_nba_position(position_raw)
    if weights is None or not isinstance(position_raw, str):
        return None
    if len(weights) == 1:
        return next(iter(weights))
    first = re.split(r"[-/‐-―−\s]+", position_raw.strip())[0]
    single = shared_positions.normalise_nba_position(first)
    return next(iter(single)) if single is not None and len(single) == 1 else None


# --------------------------------------------------------------------------- the context


@dataclass
class ReadContext:
    """Everything a builder needs about one season of the stats store, read once."""

    session: Session
    now: datetime
    season: str
    season_type: str
    sync_version: int
    data_through: date | None
    is_demo: bool
    teams: dict[int, Team]
    games: list[Game]
    details: dict[str, GameScheduleDetail]
    settings: dict[str, intel_settings.SettingValue]
    cache: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ settings

    def setting(self, key: str) -> float | None:
        """The value in force for ``key`` (``None`` only for a spread nobody has fitted yet)."""
        return self.settings[key].value

    def chance_table(self) -> dict[str, float]:
        return {s: float(self.settings[f"statusChance.{s}"].value) for s in PROFILE.chance_table()}

    # ------------------------------------------------------------------ teams

    def require_team(self, raw: Any, *, field_name: str | None = None) -> int:
        """The team id, or ``404 team_not_found`` (also for an id that is not an NBA one)."""
        team_id = parse_team_id(raw)
        if team_id is None or team_id not in self.teams:
            raise team_not_found(raw)
        return team_id

    def team_ref(self, team_id: int) -> dict[str, Any]:
        team = self.teams.get(team_id)
        if team is None:  # a game that names a team the store has no row for: still say who
            return refs.nba_team_ref(team_id, str(team_id), str(team_id))
        return refs.nba_team_ref(team.team_id, team.abbr, team.name, team.nickname)

    def team_abbr(self, team_id: int) -> str:
        team = self.teams.get(team_id)
        return team.abbr if team is not None else str(team_id)

    # ------------------------------------------------------------------ players

    @property
    def positions(self) -> dict[int, PlayerPositionSeason]:
        """The season's listed positions, by player."""
        cached = self.cache.get("positions")
        if cached is None:
            rows = self.session.execute(
                select(PlayerPositionSeason).where(PlayerPositionSeason.season == self.season)
            ).scalars()
            cached = self.cache["positions"] = {r.player_id: r for r in rows}
        return cached

    def load_players(self, player_ids: Iterable[int]) -> None:
        """Read the named players into the cache in one query."""
        cache: dict[int, Player] = self.cache.setdefault("players", {})
        wanted = sorted({int(p) for p in player_ids if int(p) not in cache})
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            for row in self.session.execute(
                select(Player).where(Player.player_id.in_(chunk))
            ).scalars():
                cache[row.player_id] = row

    def player(self, player_id: int) -> Player | None:
        self.load_players([player_id])
        return self.cache["players"].get(player_id)

    def player_name(self, player_id: int) -> str:
        row = self.player(player_id)
        return row.full_name if row is not None else str(player_id)

    def player_ref(self, player_id: int, team_id: int | None = None) -> dict[str, Any]:
        """A ``LeaguePlayerRef``. ``position`` is the season's *listed* bucket only: the
        ``players.position`` column is a lineup-card label and is never shown as one."""
        row = self.player(player_id)
        listing = self.positions.get(player_id)
        raw = listing.position_raw if listing is not None else None
        return refs.nba_player_ref(
            player_id,
            row.full_name if row is not None else str(player_id),
            position=primary_bucket(raw),
            position_raw=raw,
            jersey=row.jersey if row is not None else None,
            headshot_url=row.headshot_url if row is not None else None,
        )

    # ------------------------------------------------------------------ games

    @property
    def games_by_id(self) -> dict[str, Game]:
        cached = self.cache.get("games_by_id")
        if cached is None:
            cached = self.cache["games_by_id"] = {g.game_id: g for g in self.games}
        return cached

    def game(self, game_id: str) -> Game:
        game = self.games_by_id.get(game_id)
        if game is None:
            raise game_not_found(game_id)
        return game

    def tipoff_of(self, game: Game) -> datetime | None:
        detail = self.details.get(game.game_id)
        return aware(detail.tipoff_utc) if detail is not None else None

    def start_of(self, game: Game) -> datetime:
        return game_start(game, self.tipoff_of(game))

    def result_known_by(self, game: Game, cutoff: datetime) -> bool:
        """Whether ``game``'s result may inform anything cut off at ``cutoff``: see
        :func:`result_known_by`."""
        return result_known_by(game, cutoff)

    def deadline_of(self, game: Game) -> datetime:
        return lock_deadline(game, self.tipoff_of(game))

    def game_status(self, game: Game) -> str:
        """``scheduled``, ``resultPending``, ``final`` or ``postponed``.

        The store's ``live`` is a game in progress and has no word of its own in the contract's
        four, so it reads ``scheduled`` until three hours past tip-off and ``resultPending``
        after: a result that has not arrived is never presented as an upcoming game.
        """
        return refs.game_status(
            league=NBA_KEY,
            has_result=game_has_result(game),
            now=self.now,
            tipoff_utc=self.tipoff_of(game),
            game_date=game.game_date,
            postponed=game.status in ("postponed", "cancelled"),
        )

    def overtime_periods(self, game: Game) -> int | None:
        """Overtimes played, from the home side's recorded team minutes (240 plus 25 each);
        ``None`` when the minutes are unknown, because a guess would be a zero."""
        cache: dict[str, int | None] = self.cache.setdefault("overtime", {})
        if game.game_id not in cache:
            minutes = self.session.execute(
                select(TeamGame.minutes).where(
                    TeamGame.game_id == game.game_id, TeamGame.is_home.is_(True)
                )
            ).scalar_one_or_none()
            cache[game.game_id] = _overtimes_from_minutes(minutes)
        return cache[game.game_id]

    def game_ref(self, game: Game) -> dict[str, Any]:
        detail = self.details.get(game.game_id)
        status = self.game_status(game)
        return refs.game_ref(
            NBA_KEY,
            game_id=game.game_id,
            date=game.game_date,
            home=self.team_ref(game.home_team_id),
            away=self.team_ref(game.away_team_id),
            status=status,
            tipoff_utc=detail.tipoff_utc if detail is not None else None,
            venue=detail.arena_name if detail is not None else None,
            is_neutral=None,
            round_number=None,
            phase=None,
            home_pts=game.home_pts,
            away_pts=game.away_pts,
            overtime_periods=self.overtime_periods(game) if status == "final" else None,
        )

    def scheduled_games(self) -> list[Game]:
        """Games the store still has as ``scheduled``, soonest first, in a projectable type."""
        return sorted(
            (
                g
                for g in self.games
                if g.status == "scheduled" and g.season_type in PROJECTABLE_SEASON_TYPES
            ),
            key=lambda g: (self.start_of(g), g.game_id),
        )

    def games_on(self, day: date) -> list[Game]:
        return sorted(
            (g for g in self.games if g.game_date == day and g.status != "final"),
            key=lambda g: (self.start_of(g), g.game_id),
        )

    def next_game_of(self, team_id: int) -> Game | None:
        for game in self.scheduled_games():
            if team_id in (game.home_team_id, game.away_team_id):
                return game
        return None

    # ------------------------------------------------------------------ keys

    def data_key(self) -> tuple[Any, ...]:
        """What a memoised computation must be keyed on so a stale result can never be served.

        The sync version moves with every finalised game; the other marks cover the writes that
        do not touch it (a position listing, a status, an override, a setting, a tip-off).
        """
        cached = self.cache.get("data_key")
        if cached is None:
            session = self.session
            seasons = [self.season]
            before = previous_season_of(session, self.season)
            if before is not None:
                seasons.append(before)
            games = session.execute(
                select(func.count(), func.max(Game.ingested_at)).where(Game.season.in_(seasons))
            ).one()
            finals = session.execute(
                select(func.count()).where(Game.season.in_(seasons), Game.status == "final")
            ).scalar_one()
            positions = session.execute(
                select(func.count(), func.max(PlayerPositionSeason.fetched_at)).where(
                    PlayerPositionSeason.season.in_(seasons)
                )
            ).one()
            details = session.execute(
                select(func.count(), func.max(GameScheduleDetail.ingested_at))
            ).one()
            intel = tuple(
                session.execute(select(func.coalesce(func.max(column), 0))).scalar_one()
                for column in (
                    NbaIntelStatus.status_id,
                    NbaIntelOverride.override_id,
                    NbaIntelSnapshot.snapshot_id,
                )
            )
            cleared = session.execute(
                select(func.count()).where(NbaIntelOverride.cleared_at.is_not(None))
            ).scalar_one()
            stored = session.execute(
                select(func.count(), func.max(NbaIntelModelSetting.set_at))
            ).one()
            cached = self.cache["data_key"] = (
                str(session.get_bind().url),
                self.season,
                self.sync_version,
                tuple(games),
                finals,
                tuple(positions),
                tuple(details),
                intel,
                cleared,
                tuple(stored),
            )
        return cached


def _overtimes_from_minutes(minutes: float | None) -> int | None:
    """Overtime periods from team minutes: 240 for regulation, 25 more for each extra period."""
    if minutes is None:
        return None
    extra = (minutes * 60 - PROFILE.team_regulation_seconds) / PROFILE.team_overtime_seconds
    if extra < -1e-9 or abs(extra - round(extra)) > 1e-6:
        return None
    return int(round(extra))


def build_context(
    session: Session,
    season: str | None = None,
    *,
    season_type: str | None = None,
    now: datetime | None = None,
    allow_empty: bool = False,
) -> ReadContext:
    """Read a season's shared facts once. ``season`` is a season string, ``latest`` or ``None``.

    ``allow_empty`` is for the routes that must answer even before any season is loaded (the
    sources panel and the model settings): they get a context with no season and no games, whose
    settings, clock and demo flag are still real. Anything that needs a season must not pass it.
    """
    kind = parse_season_type(season_type)
    if allow_empty and not (season or "").strip() and latest_season(session) is None:
        return _context(session, "", kind, now)
    return _context(session, resolve_season(session, season), kind, now)


def context_for_game(
    session: Session,
    game_id: str,
    *,
    season_type: str | None = None,
    now: datetime | None = None,
) -> ReadContext:
    """The context of the season the named game belongs to, or ``404 game_not_found``."""
    game = session.get(Game, game_id)
    if game is None:
        raise game_not_found(game_id)
    return _context(session, game.season, parse_season_type(season_type), now)


def _context(session: Session, season: str, season_type: str, now: datetime | None) -> ReadContext:
    state = session.execute(select(SyncState).where(SyncState.id == 1)).scalar_one_or_none()
    games = list(
        session.execute(
            select(Game).where(Game.season == season).order_by(Game.game_date, Game.game_id)
        ).scalars()
    )
    details: dict[str, GameScheduleDetail] = {}
    if games:
        for row in session.execute(
            select(GameScheduleDetail)
            .join(Game, Game.game_id == GameScheduleDetail.game_id)
            .where(Game.season == season)
        ).scalars():
            details[row.game_id] = row
    return ReadContext(
        session=session,
        now=aware(now) if now is not None else utc_now(),  # type: ignore[arg-type]
        season=season,
        season_type=season_type,
        sync_version=(state.sync_version or 0) if state is not None else 0,
        data_through=state.data_through if state is not None else None,
        is_demo=intel_store.store_is_synthetic(session),
        teams={t.team_id: t for t in session.execute(select(Team)).scalars()},
        games=games,
        details=details,
        settings={s.key: s for s in intel_settings.get_all(session)},
    )


# --------------------------------------------------------------------------- the slate token


def next_slate_date(session: Session, now: datetime, season: str | None = None) -> date | None:
    """The date ``date=next`` means (see the module docstring); ``None`` with nothing scheduled."""
    target = season or latest_season(session)
    if target is None:
        return None
    today = aware(now).astimezone(EASTERN).date()  # type: ignore[union-attr]
    scheduled = session.execute(
        select(func.min(Game.game_date)).where(
            Game.season == target,
            Game.status == "scheduled",
            Game.season_type.in_(PROJECTABLE_SEASON_TYPES),
            Game.game_date >= today,
        )
    ).scalar_one_or_none()
    if scheduled is not None:
        return scheduled
    newest_final = session.execute(
        select(func.max(Game.game_date)).where(Game.season == target, Game.status == "final")
    ).scalar_one_or_none()
    behind = select(func.min(Game.game_date)).where(
        Game.season == target,
        Game.status == "scheduled",
        Game.season_type.in_(PROJECTABLE_SEASON_TYPES),
    )
    if newest_final is not None:
        behind = behind.where(Game.game_date > newest_final)
    return session.execute(behind).scalar_one_or_none()


def season_of_date(session: Session, day: date) -> str | None:
    """The season holding games on ``day`` (the newest, should two ever overlap), else ``None``."""
    rows = session.execute(select(Game.season).where(Game.game_date == day).distinct()).scalars()
    seasons = sorted({s for s in rows if s and _SEASON_RE.match(s)}, key=catalog.season_sort_key)
    return seasons[-1] if seasons else None


def latest_final_date(session: Session, season: str | None = None) -> date | None:
    """The newest scheduling day with a final game (what ``date=latest`` means)."""
    target = season or latest_season(session)
    if target is None:
        return None
    return session.execute(
        select(func.max(Game.game_date)).where(Game.season == target, Game.status == "final")
    ).scalar_one_or_none()


# --------------------------------------------------------------------------- team games


def load_team_games(
    ctx: ReadContext,
    *,
    season_type: str | None = None,
    before: datetime | None = None,
) -> list[FormTeamGame]:
    """One :class:`~nbastats.shared.team_form.TeamGame` per side of every game in the scope.

    The scope is the context's season and ``season_type`` (the context's own when omitted),
    final games only, with both sides' scores recorded. ``before`` keeps only games whose result
    was known by that instant (an earlier Eastern game day, :func:`result_known_by`), which is how
    a game's matchup is cut off at its own tip-off. Team time is ``team_game.minutes`` in seconds;
    it stays ``None`` when unknown.
    """
    kind = season_type or ctx.season_type
    wanted = {g.game_id: g for g in ctx.games if g.season_type == kind and game_has_result(g)}
    if before is not None:
        cutoff = aware(before)
        wanted = {k: g for k, g in wanted.items() if result_known_by(g, cutoff)}
    if not wanted:
        return []
    rows = ctx.session.execute(
        select(TeamGame)
        .join(Game, Game.game_id == TeamGame.game_id)
        .where(Game.season == ctx.season, Game.season_type == kind, Game.status == "final")
    ).scalars()
    out: list[FormTeamGame] = []
    for row in rows:
        game = wanted.get(row.game_id)
        if game is None or row.pts is None or row.opp_pts is None:
            continue
        opponent = game.away_team_id if row.is_home else game.home_team_id
        if opponent == row.team_id:
            continue
        out.append(
            FormTeamGame(
                game_id=game.game_id,
                date=game.game_date,
                team=row.team_id,
                opponent=opponent,
                is_home=bool(row.is_home),
                pts=row.pts,
                opp_pts=row.opp_pts,
                is_neutral=None,
                team_seconds=(row.minutes * 60.0) if row.minutes else None,
                overtime_periods=_overtimes_from_minutes(row.minutes),
                tipoff_utc=ctx.tipoff_of(game),
            )
        )
    out.sort(key=lambda g: (g.date, g.game_id, g.team))
    return out


# --------------------------------------------------------------------------- memo

_MEMO: "OrderedDict[tuple[Any, ...], Any]" = OrderedDict()
_MEMO_LOCK = threading.Lock()


def memoise(ctx: ReadContext, name: str, extra: Any, build: Callable[[], Any]) -> Any:
    """Return ``build()`` for ``(ctx.data_key(), name, extra)``, computing it once.

    The key carries the sync version and the high-water marks of everything a computation reads,
    so any change that could alter an answer makes a new key; the table only ever serves an
    answer computed from exactly the data now in the store. What is stored must be plain data
    (no ORM objects, no session): it outlives the request that built it.
    """
    key = (*ctx.data_key(), name, json.dumps(extra, sort_keys=True, default=str))
    with _MEMO_LOCK:
        if key in _MEMO:
            _MEMO.move_to_end(key)
            return _MEMO[key]
    value = build()
    with _MEMO_LOCK:
        _MEMO[key] = value
        _MEMO.move_to_end(key)
        while len(_MEMO) > MEMO_SIZE:
            _MEMO.popitem(last=False)
    return value


def clear_memo() -> None:
    """Forget every memoised table (tests)."""
    with _MEMO_LOCK:
        _MEMO.clear()


def digest(*parts: Any) -> str:
    """A stable SHA-256 of JSON-able parts, for the ledger's input fingerprints.

    Floats are rendered to twelve significant digits so that a value recomputed by an
    arithmetically identical path cannot change a fingerprint by its last bit.
    """

    def clean(value: Any) -> Any:
        if isinstance(value, float):
            return float(f"{value:.12g}")
        if isinstance(value, dict):
            return {str(k): clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        return value

    canonical = json.dumps(clean(parts), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
