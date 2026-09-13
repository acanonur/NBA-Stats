"""Recompute everything derived, from the game rows that were just written.

Nothing in this module invents a number. Season rows are folded up from
``player_game_basic`` / ``team_game`` and then handed to :mod:`nbastats.metrics`,
which owns every formula in the system; the league distribution rows are produced
by :mod:`nbastats.percentiles`. If a value looks wrong, there is exactly one
place to fix it, and it is not here.

What gets rebuilt
-----------------
``player_season``  per-game values, season totals, shooting rates and the advanced
                   slate, computed through :func:`nbastats.metrics.compute_metric`
                   with the player's team and opponent totals **over the games he
                   actually played** — the correct denominator for USG%, AST% and
                   the rebound rates.
``team_season``    record, box, Oliver's four factors (and the four conceded), the
                   ratings and pace.
``league_season``  mean, population stddev and the p10/p25/p50/p75/p90 anchors of
                   every metric, per season and per subject type. This is what
                   every percentile bar and "league average" caption reads.
``shot_zone_season``  derived shares and rates on whatever zone rows a loader put
                   there, plus team rollups. Shot charts begin in 1996-97, so
                   earlier seasons have no rows and this is a no-op for them.

Idempotency, and why it is structural
-------------------------------------
The league issues post-hoc stat corrections, so an aggregate that could only be
built once would be wrong by the end of the week. Every write here is an
:func:`upsert` keyed on the row's primary key, paired with a :func:`delete_stale`
sweep for rows the season no longer produces. Running :func:`recompute_season`
twice produces identical rows and reports zero changes the second time; the test
suite asserts exactly that.

Rows an aggregation cannot produce are never clobbered. PER, Win Shares and BPM
come from the Basketball-Reference backfill (:mod:`nbastats.ingest.backfill`) and
no box score can reproduce them, so :data:`PRESERVED_PLAYER_SEASON_COLUMNS` is
left untouched — VORP is the one exception, recomputed from BPM when BPM is there.

Era honesty
-----------
Availability comes from ``contracts/metrics.json`` through
:func:`nbastats.catalog.metric_availability`, never from a hand-written list of
years. A metric the era did not record is written ``NULL``; a season whose
advanced values are box-score derivations rather than possession measurements is
stamped ``is_estimated``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog, metrics
from ..models import (
    PLAYER_GAME_ADVANCED_METRIC_COLUMNS,
    PLAYER_GAME_METRIC_COLUMNS,
    TEAM_GAME_METRIC_COLUMNS,
    Base,
    Game,
    LeagueSeason,
    Player,
    PlayerGameAdvanced,
    PlayerGameBasic,
    PlayerSeason,
    ShotZoneSeason,
    TeamGame,
    TeamSeason,
    PLAYER_SEASON_PER_GAME_COLUMNS,
    PLAYER_SEASON_RATE_COLUMNS,
    PLAYER_SEASON_TOTALS_COLUMNS,
    TEAM_SEASON_PER_GAME_COLUMNS,
    TEAM_SEASON_RATE_COLUMNS,
    TEAM_SEASON_TOTALS_COLUMNS,
)
from ..percentiles import summarize

__all__ = [
    "AGGREGATE_SOURCE",
    "PRESERVED_PLAYER_SEASON_COLUMNS",
    "AggregateReport",
    "upsert",
    "delete_stale",
    "coerce_values",
    "changed_columns",
    "era_plan",
    "apply_era",
    "derive_team_values",
    "recompute_player_seasons",
    "recompute_team_seasons",
    "recompute_league_season",
    "recompute_shot_zones",
    "recompute_season",
    "recompute_seasons",
    "affected_seasons",
    "season_types_in",
    "player_age",
]

logger = logging.getLogger("nbastats.ingest.aggregate")

#: ``data_source`` stamped on rows this module writes.
AGGREGATE_SOURCE = "aggregate"

#: Season ratings no box score can reproduce: they are fitted against league-wide
#: data and arrive from the Basketball-Reference dump. Aggregation never writes them.
PRESERVED_PLAYER_SEASON_COLUMNS: tuple[str, ...] = (
    "per",
    "ws",
    "ows",
    "dws",
    "ws48",
    "bpm",
    "obpm",
    "dbpm",
)

#: Box-score columns summed into a season total, in our column vocabulary.
_COUNTING_COLUMNS: tuple[str, ...] = (
    "pts",
    "reb",
    "oreb",
    "dreb",
    "ast",
    "stl",
    "blk",
    "tov",
    "pf",
    "fgm",
    "fga",
    "fg3m",
    "fg3a",
    "ftm",
    "fta",
    "plus_minus",
)

#: Advanced per-game columns worth carrying to the season row as a fallback: the
#: minute-weighted average of the league's own measured value, used only where the
#: formula cannot be evaluated from season totals.
_WEIGHTED_ADVANCED_COLUMNS: tuple[str, ...] = (
    "off_rtg",
    "def_rtg",
    "net_rtg",
    "ast_pct",
    "oreb_pct",
    "dreb_pct",
    "reb_pct",
    "tov_pct",
    "efg_pct",
    "ts_pct",
    "usg_pct",
    "pace",
    "pie",
)

#: Season ratings written only when they can actually be computed, so a value
#: loaded from a bulk source is never replaced by a ``None``.
_WRITE_ONLY_IF_COMPUTED: tuple[str, ...] = ("vorp",)

#: Player-season metric keys resolved through :func:`metrics.compute_metric`.
_PLAYER_RATE_KEYS: tuple[str, ...] = tuple(
    key
    for key in PLAYER_SEASON_RATE_COLUMNS
    if key not in PRESERVED_PLAYER_SEASON_COLUMNS and key not in _WRITE_ONLY_IF_COMPUTED
)

_FLOAT_TOLERANCE = 1e-9


# --------------------------------------------------------------------------- #
# Write primitives
# --------------------------------------------------------------------------- #


def _differs(current: Any, proposed: Any) -> bool:
    """True when a stored value and a proposed one are meaningfully different.

    Floats compare with a tolerance so that re-deriving the same number from the
    same inputs never counts as a change — otherwise every correction pass would
    look like it rewrote the whole season.
    """
    if current is None or proposed is None:
        return current is not proposed and not (current is None and proposed is None)
    if isinstance(current, bool) or isinstance(proposed, bool):
        return bool(current) is not bool(proposed)
    if isinstance(current, (int, float)) and isinstance(proposed, (int, float)):
        return abs(float(current) - float(proposed)) > _FLOAT_TOLERANCE
    return current != proposed


def changed_columns(row: Any, values: Mapping[str, Any]) -> list[str]:
    """Columns of ``values`` whose value differs from the ORM instance ``row``."""
    return [
        column
        for column, proposed in values.items()
        if _differs(getattr(row, column, None), proposed)
    ]


def _column_kinds(model: type[Base]) -> dict[str, str]:
    """Column name → ``int`` / ``float`` / ``bool`` / ``other`` for one model."""
    kinds: dict[str, str] = {}
    for column in model.__table__.columns:
        name = type(column.type).__name__.lower()
        if "bool" in name:
            kinds[column.key] = "bool"
        elif "int" in name:
            kinds[column.key] = "int"
        elif "float" in name or "numeric" in name:
            kinds[column.key] = "float"
        else:
            kinds[column.key] = "other"
    return kinds


def coerce_values(model: type[Base], values: Mapping[str, Any]) -> dict[str, Any]:
    """Fit values to their column types before they are compared or written.

    A derived total arrives as a float (``per_mode_convert`` returns one) but
    lands in an ``Integer`` column; rounding here rather than at every call site
    keeps the round-trip stable, so re-deriving the same season does not look
    like a change — and keeps Postgres, which rejects the mismatch SQLite
    tolerates, working from the same code.

    Values for columns this model does not have are dropped. That is deliberate:
    :func:`derive_team_values` produces one superset of team numbers for both
    ``team_game`` and ``team_season``, which hold overlapping but different
    column sets, and duplicating the derivation to match each of them is how the
    two drift apart.
    """
    kinds = _column_kinds(model)
    out: dict[str, Any] = {}
    for column, value in values.items():
        kind = kinds.get(column)
        if kind is None:
            logger.debug(
                "dropping_unknown_column model=%s column=%s", model.__tablename__, column
            )
            continue
        if value is None or kind == "other":
            out[column] = value
        elif kind == "bool":
            out[column] = bool(value)
        elif kind == "int":
            out[column] = int(round(float(value)))
        else:
            out[column] = float(value)
    return out


def upsert(
    session: Session,
    model: type[Base],
    keys: Mapping[str, Any],
    values: Mapping[str, Any],
) -> bool:
    """Insert or update one row, and report whether anything actually changed.

    This is the pipeline's only write primitive, because the league revises box
    scores after the fact: ingest must be able to run over yesterday again and
    overwrite it rather than duplicate it. Columns absent from ``values`` are left
    alone, which is how loader-owned columns survive an aggregation.

    Returns ``True`` on an insert or a real update, ``False`` when the stored row
    already said exactly this — the signal :mod:`nbastats.ingest.daily` uses to
    decide whether a game warrants a ``sync_version`` bump.
    """
    fitted = coerce_values(model, values)
    existing = session.execute(select(model).filter_by(**keys)).scalar_one_or_none()
    if existing is None:
        session.add(model(**{**keys, **fitted}))
        return True

    changed = changed_columns(existing, fitted)
    for column in changed:
        setattr(existing, column, fitted[column])
    return bool(changed)


def delete_stale(
    session: Session,
    model: type[Base],
    filters: Mapping[str, Any],
    key_columns: Sequence[str],
    keep: set[tuple[Any, ...]],
) -> int:
    """Drop rows in ``filters``' scope whose key is not in ``keep``.

    The other half of idempotency: an upsert refreshes what still exists, and
    this removes what no longer does — a player traded out of a team's season, a
    metric that has stopped qualifying for a distribution row. Rebuilding by
    ``DELETE`` + ``INSERT`` would be simpler but would report a change on every
    run, which is precisely the signal the ingest path depends on.
    """
    rows = session.execute(select(model).filter_by(**filters)).scalars().all()
    removed = 0
    for row in rows:
        if tuple(getattr(row, column, None) for column in key_columns) not in keep:
            session.delete(row)
            removed += 1
    return removed


class _Acc:
    """Sums a set of columns while telling "missing" apart from "zero".

    A 1962 box score has no steals column at all; summing it must give ``None``,
    not ``0``, or the API would confidently report that nobody stole the ball
    that year. Every column therefore carries a presence count alongside its sum.
    """

    __slots__ = ("_sums", "_counts", "rows")

    def __init__(self) -> None:
        self._sums: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self.rows = 0

    def add(self, column: str, value: Any) -> None:
        if value is None:
            self._sums.setdefault(column, 0.0)
            self._counts.setdefault(column, 0)
            return
        self._sums[column] = self._sums.get(column, 0.0) + float(value)
        self._counts[column] = self._counts.get(column, 0) + 1

    def add_row(self, row: Any, columns: Iterable[str]) -> None:
        for column in columns:
            self.add(column, getattr(row, column, None))
        self.rows += 1

    def get(self, column: str) -> float | None:
        """The sum, or ``None`` when no row carried a value for this column."""
        return self._sums.get(column) if self._counts.get(column) else None

    def count(self, column: str) -> int:
        return self._counts.get(column, 0)

    def as_row(self, columns: Iterable[str]) -> dict[str, Any]:
        return {column: self.get(column) for column in columns}


class _Weighted:
    """Minute-weighted mean of a per-game advanced column."""

    __slots__ = ("_totals", "_weights")

    def __init__(self) -> None:
        self._totals: dict[str, float] = {}
        self._weights: dict[str, float] = {}

    def add(self, column: str, value: Any, weight: float | None) -> None:
        if value is None or weight is None or weight <= 0:
            return
        self._totals[column] = self._totals.get(column, 0.0) + float(value) * weight
        self._weights[column] = self._weights.get(column, 0.0) + weight

    def get(self, column: str) -> float | None:
        weight = self._weights.get(column)
        if not weight:
            return None
        return self._totals[column] / weight


@dataclass
class AggregateReport:
    """What one recomputation touched, for the log and the ingest record."""

    season: str
    season_type: str
    player_seasons: int = 0
    team_seasons: int = 0
    league_rows: int = 0
    shot_zone_rows: int = 0
    changed: int = 0
    notes: list[str] = field(default_factory=list)

    def merge(self, other: "AggregateReport") -> "AggregateReport":
        self.player_seasons += other.player_seasons
        self.team_seasons += other.team_seasons
        self.league_rows += other.league_rows
        self.shot_zone_rows += other.shot_zone_rows
        self.changed += other.changed
        self.notes.extend(other.notes)
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "seasonType": self.season_type,
            "playerSeasons": self.player_seasons,
            "teamSeasons": self.team_seasons,
            "leagueRows": self.league_rows,
            "shotZoneRows": self.shot_zone_rows,
            "changed": self.changed,
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# Era rules
# --------------------------------------------------------------------------- #


def _merge_maps(*mappings: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    """Metric key → every column that holds it, across several column maps."""
    merged: dict[str, list[str]] = {}
    for mapping in mappings:
        for key, column in mapping.items():
            columns = merged.setdefault(key, [])
            if column not in columns:
                columns.append(column)
    return {key: tuple(columns) for key, columns in merged.items()}


#: Named groups of metric key → column, so era rules are applied from the catalog
#: rather than restated per table.
_COLUMN_GROUPS: dict[str, dict[str, tuple[str, ...]]] = {
    "player_game": _merge_maps(PLAYER_GAME_METRIC_COLUMNS),
    "player_game_advanced": _merge_maps(PLAYER_GAME_ADVANCED_METRIC_COLUMNS),
    "team_game": _merge_maps(TEAM_GAME_METRIC_COLUMNS),
    "player_season": _merge_maps(
        PLAYER_SEASON_PER_GAME_COLUMNS,
        PLAYER_SEASON_TOTALS_COLUMNS,
        PLAYER_SEASON_RATE_COLUMNS,
    ),
    "team_season": _merge_maps(
        TEAM_SEASON_PER_GAME_COLUMNS,
        TEAM_SEASON_TOTALS_COLUMNS,
        TEAM_SEASON_RATE_COLUMNS,
    ),
}


@lru_cache(maxsize=512)
def era_plan(group: str, season: str, granularity: str = "season") -> tuple[frozenset[str], bool]:
    """Columns to null for this era, and whether what remains is estimated.

    Read straight from the metric catalog — the boundaries live in
    ``contracts/metrics.json`` and are never restated in code. ``granularity`` is
    ``"season"`` for an aggregate row or ``"game"`` for a box-score row, which is
    the difference between TS% (computable from any era's box score) and a
    per-game offensive rating (possession data, 1996-97 onward).

    This is what keeps a 1962 steal count ``NULL`` instead of ``0``.
    """
    unavailable: set[str] = set()
    estimated = False
    for key, columns in _COLUMN_GROUPS[group].items():
        status = catalog.metric_availability(key, season, granularity)
        if status == "unavailable":
            unavailable.update(columns)
        elif status == "estimated":
            estimated = True
    return frozenset(unavailable), estimated


def apply_era(values: dict[str, Any], unavailable: Iterable[str]) -> dict[str, Any]:
    """Null every column this era did not record. Mutates and returns ``values``."""
    for column in unavailable:
        if column in values:
            values[column] = None
    return values


def player_age(birthdate: date | None, season: str) -> int | None:
    """Age on 1 February of the season's second calendar year.

    The convention Basketball-Reference and the league use, so a "27-year-old
    season" means the same thing here as everywhere else.
    """
    start = catalog.season_sort_key(season) if catalog.is_season_string(season) else None
    if birthdate is None or start is None:
        return None
    reference = date(start + 1, 2, 1)
    years = reference.year - birthdate.year
    if (reference.month, reference.day) < (birthdate.month, birthdate.day):
        years -= 1
    return years if years > 0 else None


# --------------------------------------------------------------------------- #
# Scope helpers
# --------------------------------------------------------------------------- #


def season_types_in(session: Session, season: str) -> list[str]:
    """Season types that have at least one game row in this season."""
    rows = session.execute(
        select(Game.season_type).where(Game.season == season).distinct()
    ).scalars()
    return sorted({row for row in rows if row})


def affected_seasons(session: Session, game_ids: Iterable[str]) -> set[tuple[str, str]]:
    """The ``(season, season_type)`` pairs a set of game ids belongs to."""
    ids = [game_id for game_id in game_ids if game_id]
    if not ids:
        return set()
    rows = session.execute(
        select(Game.season, Game.season_type).where(Game.game_id.in_(ids)).distinct()
    ).all()
    return {(season, season_type) for season, season_type in rows if season and season_type}


def _final_games(session: Session, season: str, season_type: str) -> dict[str, Game]:
    games = session.execute(
        select(Game).where(
            Game.season == season,
            Game.season_type == season_type,
            Game.status == "final",
        )
    ).scalars()
    return {game.game_id: game for game in games}


# --------------------------------------------------------------------------- #
# Player seasons
# --------------------------------------------------------------------------- #


def recompute_player_seasons(
    session: Session, season: str, season_type: str
) -> AggregateReport:
    """Rebuild ``player_season`` for one season from its game rows."""
    report = AggregateReport(season=season, season_type=season_type)
    games = _final_games(session, season, season_type)
    if not games:
        return report

    game_ids = list(games)
    basics = (
        session.execute(
            select(PlayerGameBasic).where(PlayerGameBasic.game_id.in_(game_ids))
        )
        .scalars()
        .all()
    )
    if not basics:
        return report

    advanced_by_key = {
        (row.game_id, row.player_id): row
        for row in session.execute(
            select(PlayerGameAdvanced).where(PlayerGameAdvanced.game_id.in_(game_ids))
        )
        .scalars()
        .all()
    }
    team_games = (
        session.execute(select(TeamGame).where(TeamGame.game_id.in_(game_ids)))
        .scalars()
        .all()
    )
    team_game_by_key = {(row.game_id, row.team_id): row for row in team_games}
    opponent_of: dict[tuple[str, int], TeamGame] = {}
    by_game: dict[str, list[TeamGame]] = {}
    for row in team_games:
        by_game.setdefault(row.game_id, []).append(row)
    for game_id, sides in by_game.items():
        if len(sides) == 2:
            opponent_of[(game_id, sides[0].team_id)] = sides[1]
            opponent_of[(game_id, sides[1].team_id)] = sides[0]

    # One accumulator trio per (player, team): the player's own line, his team's
    # totals over those same games, and the opponents' — the denominators every
    # on-floor rate needs.
    own: dict[tuple[int, int], _Acc] = {}
    team_ctx: dict[tuple[int, int], _Acc] = {}
    opp_ctx: dict[tuple[int, int], _Acc] = {}
    weighted: dict[tuple[int, int], _Weighted] = {}
    played: dict[tuple[int, int], int] = {}
    started: dict[tuple[int, int], int] = {}
    team_games_seen: dict[tuple[int, int], set[str]] = {}

    context_columns = ("minutes", *_COUNTING_COLUMNS)

    for line in basics:
        key = (line.player_id, line.team_id)
        accumulator = own.setdefault(key, _Acc())
        accumulator.add("minutes", line.minutes)
        for column in _COUNTING_COLUMNS:
            accumulator.add(column, getattr(line, column, None))
        accumulator.add("fantasy_pts", line.fantasy_pts)
        played[key] = played.get(key, 0) + 1
        if line.started:
            started[key] = started.get(key, 0) + 1

        team_row = team_game_by_key.get((line.game_id, line.team_id))
        if team_row is not None:
            team_ctx.setdefault(key, _Acc()).add_row(team_row, context_columns)
            team_games_seen.setdefault(key, set()).add(line.game_id)
        opponent_row = opponent_of.get((line.game_id, line.team_id))
        if opponent_row is not None:
            opp_ctx.setdefault(key, _Acc()).add_row(opponent_row, context_columns)

        advanced_row = advanced_by_key.get((line.game_id, line.player_id))
        if advanced_row is not None:
            bucket = weighted.setdefault(key, _Weighted())
            for column in _WEIGHTED_ADVANCED_COLUMNS:
                bucket.add(column, getattr(advanced_row, column, None), line.minutes)

    unavailable, estimated = era_plan("player_season", season)
    birthdays = {
        player_id: birthdate
        for player_id, birthdate in session.execute(
            select(Player.player_id, Player.birthdate)
        ).all()
    }
    written = 0
    changed = 0

    for (player_id, team_id), accumulator in own.items():
        games_played = played[(player_id, team_id)]
        minutes_total = accumulator.get("minutes")
        subject = _season_subject_row(accumulator, season, games_played)
        team_row = _context_row(
            team_ctx.get((player_id, team_id)),
            season,
            len(team_games_seen.get((player_id, team_id), ())),
        )
        opponent_row = _context_row(opp_ctx.get((player_id, team_id)), season, games_played)

        existing = session.execute(
            select(PlayerSeason).filter_by(
                player_id=player_id,
                season=season,
                season_type=season_type,
                team_id=team_id,
            )
        ).scalar_one_or_none()
        for column in PRESERVED_PLAYER_SEASON_COLUMNS:
            value = getattr(existing, column, None) if existing is not None else None
            if value is not None:
                subject[column] = value

        bucket = weighted.get((player_id, team_id))
        if bucket is not None:
            for column in _WEIGHTED_ADVANCED_COLUMNS:
                measured = bucket.get(column)
                if measured is not None:
                    # Present as a stored fallback only: compute_metric prefers the
                    # formula evaluated over season totals when it can.
                    subject.setdefault(column, measured)

        values: dict[str, Any] = {
            "age": player_age(birthdays.get(player_id), season),
            "gp": games_played,
            "gs": started.get((player_id, team_id), 0),
            "minutes": minutes_total,
            "min_pg": metrics.per_mode_convert(
                minutes_total, minutes_total, games_played, "PerGame"
            ),
            "is_estimated": estimated,
            "data_source": AGGREGATE_SOURCE,
        }

        for key, column in PLAYER_SEASON_TOTALS_COLUMNS.items():
            if key in {"gp", "gs", "min", "game_score"}:
                continue
            total = metrics.compute_metric(
                key, row=subject, team_row=team_row, opponent_row=opponent_row
            )
            values[column] = total
            per_game_column = PLAYER_SEASON_PER_GAME_COLUMNS.get(key)
            if per_game_column:
                values[per_game_column] = metrics.per_mode_convert(
                    total, minutes_total, games_played, "PerGame"
                )

        # Game Score is linear in the box score, so the season total divided by
        # games is exactly the average of the per-game values.
        total_game_score = metrics.compute_metric("game_score", row=subject)
        values["game_score"] = metrics.per_mode_convert(
            total_game_score, minutes_total, games_played, "PerGame"
        )

        for key in _PLAYER_RATE_KEYS:
            column = PLAYER_SEASON_RATE_COLUMNS[key]
            values[column] = metrics.compute_metric(
                key, row=subject, team_row=team_row, opponent_row=opponent_row
            )

        # VORP is the one season rating a box score can reach, given BPM.
        computed_vorp = metrics.compute_metric("vorp", row=subject, team_row=team_row)
        if computed_vorp is not None:
            values["vorp"] = computed_vorp

        apply_era(values, unavailable)
        if upsert(
            session,
            PlayerSeason,
            {
                "player_id": player_id,
                "season": season,
                "season_type": season_type,
                "team_id": team_id,
            },
            values,
        ):
            changed += 1
        written += 1

    # A row this aggregation used to own but no longer produces — a player whose
    # games moved to another team — is dropped. Rows from a bulk loader that has
    # no game rows here keep their own data_source and are left alone.
    changed += delete_stale(
        session,
        PlayerSeason,
        {"season": season, "season_type": season_type, "data_source": AGGREGATE_SOURCE},
        ("player_id", "team_id"),
        set(own),
    )

    session.flush()
    report.player_seasons = written
    report.changed += changed
    return report


def derive_team_values(
    subject: Mapping[str, Any], opponent: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Rates, four factors and ratings for one team row — a game or a season.

    A team is not a player. Its shooting rates come from the same formulas, but
    its rebound and turnover percentages are Oliver's *team* four factors rather
    than the on-floor player versions, so :func:`metrics.four_factors` produces
    those four and the four conceded. Shared by the per-game and per-season paths
    so one game's ``team_game`` row and the season's ``team_season`` row can
    never disagree about what "eFG%" means.

    ``subject`` and ``opponent`` are totals in the ``compute_metric`` vocabulary
    (``min``, ``pts``, ``fga``, …). Every value comes back ``None`` rather than
    zero when its inputs are missing.
    """
    minutes = subject.get("min")
    own_possessions = metrics.possessions(
        subject.get("fga"), subject.get("fta"), subject.get("oreb"), subject.get("tov")
    )
    opponent_possessions = (
        metrics.possessions(
            opponent.get("fga"), opponent.get("fta"), opponent.get("oreb"), opponent.get("tov")
        )
        if opponent
        else None
    )
    row = {**subject, "poss": own_possessions}
    opponent_row = (
        {**opponent, "poss": opponent_possessions} if opponent is not None else None
    )

    values: dict[str, Any] = {}
    for key in (
        "fg_pct",
        "fg3_pct",
        "ft_pct",
        "ts_pct",
        "fg3a_rate",
        "pps",
        "off_rtg",
        "def_rtg",
        "net_rtg",
        "win_pct",
    ):
        values[key] = metrics.compute_metric(key, row=row, opponent_row=opponent_row)

    for factor in metrics.four_factors(
        fgm=subject.get("fgm"),
        fg3m=subject.get("fg3m"),
        fga=subject.get("fga"),
        fta=subject.get("fta"),
        tov=subject.get("tov"),
        oreb=subject.get("oreb"),
        opp_dreb=opponent.get("dreb") if opponent else None,
    ):
        values[factor.key] = factor.value
    for factor in metrics.four_factors(
        fgm=opponent.get("fgm") if opponent else None,
        fg3m=opponent.get("fg3m") if opponent else None,
        fga=opponent.get("fga") if opponent else None,
        fta=opponent.get("fta") if opponent else None,
        tov=opponent.get("tov") if opponent else None,
        oreb=opponent.get("oreb") if opponent else None,
        opp_dreb=subject.get("dreb"),
    ):
        values[f"opp_{factor.key}"] = factor.value

    # The team counterpart of the player rebound rates: the share of the
    # rebounds that were available on each glass.
    values["dreb_pct"] = metrics.team_oreb_pct(
        subject.get("dreb"), opponent.get("oreb") if opponent else None
    )
    values["reb_pct"] = metrics.team_oreb_pct(
        subject.get("reb"), opponent.get("reb") if opponent else None
    )
    values["poss"] = own_possessions
    values["pace"] = metrics.pace(own_possessions, opponent_possessions, minutes)
    return values


def _season_subject_row(
    accumulator: _Acc, season: str, games_played: int
) -> dict[str, Any]:
    """The player's season totals in the vocabulary ``compute_metric`` reads."""
    row: dict[str, Any] = {
        "season": season,
        "gp": games_played,
        "min": accumulator.get("minutes"),
    }
    for column in _COUNTING_COLUMNS:
        row[column] = accumulator.get(column)
    row["fantasy_pts"] = accumulator.get("fantasy_pts")
    return row


def _context_row(
    accumulator: _Acc | None, season: str, games: int
) -> dict[str, Any] | None:
    """A team's (or opponent's) totals over the same games, plus its possessions."""
    if accumulator is None:
        return None
    row: dict[str, Any] = {
        "season": season,
        "gp": games or accumulator.rows,
        "min": accumulator.get("minutes"),
    }
    for column in _COUNTING_COLUMNS:
        row[column] = accumulator.get(column)
    row["poss"] = metrics.possessions(
        row.get("fga"), row.get("fta"), row.get("oreb"), row.get("tov")
    )
    return row


# --------------------------------------------------------------------------- #
# Team seasons
# --------------------------------------------------------------------------- #


def recompute_team_seasons(session: Session, season: str, season_type: str) -> AggregateReport:
    """Rebuild ``team_season`` for one season from its ``team_game`` rows.

    A team is not a player: its shooting rates come from the same formulas, but
    its rebound and turnover percentages are Oliver's *team* four factors rather
    than the on-floor player versions, so :func:`metrics.four_factors` is used
    directly for those four and for the four conceded.
    """
    report = AggregateReport(season=season, season_type=season_type)
    games = _final_games(session, season, season_type)
    if not games:
        return report

    rows = (
        session.execute(select(TeamGame).where(TeamGame.game_id.in_(list(games))))
        .scalars()
        .all()
    )
    if not rows:
        return report

    by_game: dict[str, list[TeamGame]] = {}
    for row in rows:
        by_game.setdefault(row.game_id, []).append(row)

    own: dict[int, _Acc] = {}
    opp: dict[int, _Acc] = {}
    record: dict[int, list[int]] = {}
    columns = ("minutes", *_COUNTING_COLUMNS)

    for game_id, sides in by_game.items():
        for side in sides:
            own.setdefault(side.team_id, _Acc()).add_row(side, columns)
            counts = record.setdefault(side.team_id, [0, 0, 0])
            counts[0] += 1
            if side.won is True:
                counts[1] += 1
            elif side.won is False:
                counts[2] += 1
        if len(sides) == 2:
            opp.setdefault(sides[0].team_id, _Acc()).add_row(sides[1], columns)
            opp.setdefault(sides[1].team_id, _Acc()).add_row(sides[0], columns)

    unavailable, estimated = era_plan("team_season", season)

    written = 0
    changed = 0
    for team_id, accumulator in own.items():
        games_played, wins, losses = record[team_id]
        minutes_total = accumulator.get("minutes")
        subject: dict[str, Any] = {
            "season": season,
            "gp": games_played,
            "wins": wins,
            "losses": losses,
            "min": minutes_total,
        }
        for column in _COUNTING_COLUMNS:
            subject[column] = accumulator.get(column)
        subject["poss"] = metrics.possessions(
            subject.get("fga"), subject.get("fta"), subject.get("oreb"), subject.get("tov")
        )

        opponent = _context_row(opp.get(team_id), season, games_played)

        values: dict[str, Any] = {
            "gp": games_played,
            "wins": wins,
            "losses": losses,
            "minutes": minutes_total,
            "min_pg": metrics.per_mode_convert(
                minutes_total, minutes_total, games_played, "PerGame"
            ),
            "opp_pts": metrics.per_mode_convert(
                opponent.get("pts") if opponent else None, None, games_played, "PerGame"
            ),
            "is_estimated": estimated,
            "data_source": AGGREGATE_SOURCE,
        }

        for key, column in TEAM_SEASON_TOTALS_COLUMNS.items():
            if key in {"gp", "wins", "losses", "min"}:
                continue
            values[column] = metrics.compute_metric(key, row=subject, opponent_row=opponent)
        for key, column in TEAM_SEASON_PER_GAME_COLUMNS.items():
            if key in {"gp", "wins", "losses", "min"}:
                continue
            values[column] = metrics.per_mode_convert(
                metrics.compute_metric(key, row=subject, opponent_row=opponent),
                minutes_total,
                games_played,
                "PerGame",
            )

        values.update(derive_team_values(subject, opponent))

        apply_era(values, unavailable)
        if upsert(
            session,
            TeamSeason,
            {"team_id": team_id, "season": season, "season_type": season_type},
            values,
        ):
            changed += 1
        written += 1

    changed += delete_stale(
        session,
        TeamSeason,
        {"season": season, "season_type": season_type, "data_source": AGGREGATE_SOURCE},
        ("team_id",),
        {(team_id,) for team_id in own},
    )

    session.flush()
    report.team_seasons = written
    report.changed += changed
    return report


# --------------------------------------------------------------------------- #
# League distributions
# --------------------------------------------------------------------------- #


def _qualified_player_rows(rows: Sequence[PlayerSeason]) -> list[PlayerSeason]:
    """Rotation players only, so a percentile means something.

    Ranking a 4-minute callup against a starter would make every percentile bar
    in the app flattering and meaningless. The threshold scales with the season's
    length so it works for a finished season and one four games old alike.
    """
    if not rows:
        return []
    most_games = max((row.gp or 0) for row in rows)
    threshold = max(2, int(0.2 * max(1, most_games)))
    qualified = [
        row for row in rows if (row.gp or 0) >= threshold and (row.min_pg or 0.0) >= 10.0
    ]
    return qualified or list(rows)


def recompute_league_season(
    session: Session, season: str, season_type: str
) -> AggregateReport:
    """Rebuild the ``league_season`` distribution rows for one season.

    Every row is fully derived from the season rows, so the table is refreshed
    in place — upsert what the season now says, then drop any metric that has
    stopped qualifying. Rebuilding by delete-and-insert would report a change on
    every run and make the freshness signal useless.
    """
    report = AggregateReport(season=season, season_type=season_type)

    player_rows = (
        session.execute(
            select(PlayerSeason).where(
                PlayerSeason.season == season, PlayerSeason.season_type == season_type
            )
        )
        .scalars()
        .all()
    )
    team_rows = (
        session.execute(
            select(TeamSeason).where(
                TeamSeason.season == season, TeamSeason.season_type == season_type
            )
        )
        .scalars()
        .all()
    )
    if not player_rows and not team_rows:
        return report

    plans: tuple[tuple[str, Sequence[Any], dict[str, str]], ...] = (
        (
            "player",
            _qualified_player_rows(player_rows),
            {
                **PLAYER_SEASON_RATE_COLUMNS,
                **PLAYER_SEASON_PER_GAME_COLUMNS,
            },
        ),
        (
            "team",
            team_rows,
            {**TEAM_SEASON_RATE_COLUMNS, **TEAM_SEASON_PER_GAME_COLUMNS},
        ),
    )

    written = 0
    changed = 0
    keep: set[tuple[Any, ...]] = set()
    for subject_type, rows, column_map in plans:
        if not rows:
            continue
        in_scope = {descriptor["key"] for descriptor in catalog.metrics_for_scope(subject_type)}
        for metric_key, column in column_map.items():
            if metric_key not in in_scope:
                continue
            if catalog.metric_availability(metric_key, season, "season") == "unavailable":
                continue
            values = [getattr(row, column, None) for row in rows]
            summary = summarize(values)
            if summary.count < 2:
                continue
            present = sorted(value for value in values if value is not None)
            keep.add((subject_type, metric_key))
            if upsert(
                session,
                LeagueSeason,
                {
                    "subject_type": subject_type,
                    "season": season,
                    "season_type": season_type,
                    "metric_key": metric_key,
                },
                {
                    "sample_size": summary.count,
                    "average": summary.mean,
                    "stddev": summary.stddev,
                    "p10": summary.p10,
                    "p25": summary.p25,
                    "p50": summary.p50,
                    "p75": summary.p75,
                    "p90": summary.p90,
                    "min_value": present[0],
                    "max_value": present[-1],
                },
            ):
                changed += 1
            written += 1

    changed += delete_stale(
        session,
        LeagueSeason,
        {"season": season, "season_type": season_type},
        ("subject_type", "metric_key"),
        keep,
    )

    session.flush()
    report.league_rows = written
    report.changed += changed
    return report


# --------------------------------------------------------------------------- #
# Shot zones
# --------------------------------------------------------------------------- #


def recompute_shot_zones(session: Session, season: str, season_type: str) -> AggregateReport:
    """Recompute derived shot-zone fields and rebuild the team rollups.

    Shot detail is loaded by :mod:`nbastats.ingest.backfill` (hoopR / shufinskiy)
    and only exists from 1996-97, so for earlier seasons — and for any season
    nobody has loaded shots for — this is a no-op rather than an invention.
    """
    report = AggregateReport(season=season, season_type=season_type)
    player_rows = (
        session.execute(
            select(ShotZoneSeason).where(
                ShotZoneSeason.season == season,
                ShotZoneSeason.season_type == season_type,
                ShotZoneSeason.subject_type == "player",
            )
        )
        .scalars()
        .all()
    )
    if not player_rows:
        return report

    # ``shot_zone_season`` is keyed by (subject, season, zone) with no team, so a player's
    # zone totals already span the whole season. ``player_season`` is keyed by (player, team),
    # so a traded player has one row per stint. Folding those rows with a dict comprehension
    # would let whichever stint the database returned last stand for the season: the per-game
    # conversion below would then divide full-season attempts by one stint's games.
    games_by_player: dict[int, float] = {}
    games_by_stint: dict[int, dict[int, float]] = {}
    for player_id, team_id, gp in session.execute(
        select(PlayerSeason.player_id, PlayerSeason.team_id, PlayerSeason.gp).where(
            PlayerSeason.season == season, PlayerSeason.season_type == season_type
        )
    ).all():
        played = float(gp or 0)
        games_by_player[player_id] = games_by_player.get(player_id, 0.0) + played
        if team_id is not None:
            stints = games_by_stint.setdefault(player_id, {})
            stints[team_id] = stints.get(team_id, 0.0) + played

    # The team rollup can only name one team per player, because the shot rows carry none.
    # Crediting the stint the player actually played most of is at least deterministic and
    # defensible; splitting a traded player's shots properly needs a team on the shot rows.
    team_by_player = {
        player_id: max(stints.items(), key=lambda item: item[1])[0]
        for player_id, stints in games_by_stint.items()
        if stints
    }

    totals_by_player: dict[int, float] = {}
    for row in player_rows:
        if row.fga_tot:
            totals_by_player[row.subject_id] = (
                totals_by_player.get(row.subject_id, 0.0) + row.fga_tot
            )

    changed = 0
    team_totals: dict[tuple[int, str], list[float]] = {}
    for row in player_rows:
        attempts = float(row.fga_tot or 0)
        makes = float(row.fgm_tot or 0)
        games = games_by_player.get(row.subject_id) or 0
        subject_total = totals_by_player.get(row.subject_id) or 0.0
        points = 3.0 if row.zone.endswith("three") else 2.0
        values = {
            "fga": metrics.per_mode_convert(attempts, None, games, "PerGame"),
            "fgm": metrics.per_mode_convert(makes, None, games, "PerGame"),
            "fg_pct": (makes / attempts) if attempts else None,
            "share_of_fga": (attempts / subject_total) if subject_total else None,
            "points_per_shot": (points * makes / attempts) if attempts else None,
        }
        if changed_columns(row, values):
            for column, value in values.items():
                setattr(row, column, value)
            changed += 1

        team_id = team_by_player.get(row.subject_id)
        if team_id is not None:
            bucket = team_totals.setdefault((team_id, row.zone), [0.0, 0.0])
            bucket[0] += attempts
            bucket[1] += makes

    team_games = {
        team_id: gp
        for team_id, gp in session.execute(
            select(TeamSeason.team_id, TeamSeason.gp).where(
                TeamSeason.season == season, TeamSeason.season_type == season_type
            )
        ).all()
    }
    team_attempts: dict[int, float] = {}
    for (team_id, _zone), (attempts, _makes) in team_totals.items():
        team_attempts[team_id] = team_attempts.get(team_id, 0.0) + attempts

    written = 0
    for (team_id, zone), (attempts, makes) in sorted(team_totals.items()):
        games = team_games.get(team_id) or 0
        points = 3.0 if zone.endswith("three") else 2.0
        total = team_attempts.get(team_id) or 0.0
        if upsert(
            session,
            ShotZoneSeason,
            {
                "subject_type": "team",
                "subject_id": team_id,
                "season": season,
                "season_type": season_type,
                "zone": zone,
            },
            {
                "fga": metrics.per_mode_convert(attempts, None, games, "PerGame"),
                "fgm": metrics.per_mode_convert(makes, None, games, "PerGame"),
                "fga_tot": attempts,
                "fgm_tot": makes,
                "fg_pct": (makes / attempts) if attempts else None,
                "share_of_fga": (attempts / total) if total else None,
                "points_per_shot": (points * makes / attempts) if attempts else None,
            },
        ):
            changed += 1
        written += 1

    changed += delete_stale(
        session,
        ShotZoneSeason,
        {"season": season, "season_type": season_type, "subject_type": "team"},
        ("subject_id", "zone"),
        set(team_totals),
    )

    session.flush()
    report.shot_zone_rows = len(player_rows) + written
    report.changed += changed
    return report


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #


def recompute_season(
    session: Session,
    season: str,
    season_type: str | None = None,
    *,
    commit: bool = False,
) -> AggregateReport:
    """Recompute every derived table for one season (or one of its season types).

    With ``season_type=None`` every season type that has games is rebuilt. The
    order matters: player and team seasons first, because the league distribution
    and the shot-zone rollups read what they wrote.
    """
    types = [season_type] if season_type else season_types_in(session, season)
    combined = AggregateReport(season=season, season_type=season_type or "all")
    for current in types:
        combined.merge(recompute_player_seasons(session, season, current))
        combined.merge(recompute_team_seasons(session, season, current))
        combined.merge(recompute_league_season(session, season, current))
        combined.merge(recompute_shot_zones(session, season, current))
    if commit:
        session.commit()
    logger.info(
        "event=aggregate season=%s season_types=%s players=%d teams=%d league=%d zones=%d changed=%d",
        season,
        ",".join(types) or "none",
        combined.player_seasons,
        combined.team_seasons,
        combined.league_rows,
        combined.shot_zone_rows,
        combined.changed,
    )
    return combined


def recompute_seasons(
    session: Session,
    seasons: Iterable[tuple[str, str | None]],
    *,
    commit: bool = False,
) -> list[AggregateReport]:
    """Recompute a set of ``(season, season_type)`` pairs, each one once."""
    seen: set[tuple[str, str | None]] = set()
    reports: list[AggregateReport] = []
    for season, season_type in seasons:
        if not season or (season, season_type) in seen:
            continue
        seen.add((season, season_type))
        reports.append(recompute_season(session, season, season_type))
    if commit:
        session.commit()
    return reports
