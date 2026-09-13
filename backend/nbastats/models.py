"""SQLAlchemy 2.0 declarative models for the Hardwood store.

Mirrors ``contracts/CONTRACT.md``. Columns are ``snake_case``; the API layer translates to
the contract's ``lowerCamelCase`` JSON. Percentages are stored as fractions in ``[0, 1]``.

Era honesty is encoded in the schema: every advanced column is nullable, because a stat that
did not exist in an era must be ``NULL`` — never ``0``. ``player_game_advanced`` rows only
exist from 1996-97 onward, and pre-1996-97 ``player_season`` rows carry ``is_estimated``.

The ``*_METRIC_COLUMNS`` maps at the bottom are the contract between a metric key in
``contracts/metrics.json`` and the column that holds it, so the API layer never hard-codes
column names. ``season_column_for()`` resolves a key for a given ``perMode``.

Partitioning note: BigQuery's "partition by game_date, cluster by player_id" plan becomes
plain composite indexes here — ``games(game_date)``, ``player_season(player_id, season,
season_type)`` and ``(season, season_type)`` on the season tables. SQLite is the default
store and a Postgres ``DATABASE_URL`` works unchanged; no BigQuery client is imported.

``league_season`` note: the table is keyed ``(subject_type, season, season_type,
metric_key)``. ``subject_type`` is part of the key because a player's offensive-rating
distribution is not a team's — mixing them would silently corrupt every percentile.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = [
    "Base",
    "Team",
    "Player",
    "Game",
    "PlayerGameBasic",
    "PlayerGameAdvanced",
    "TeamGame",
    "PlayerSeason",
    "TeamSeason",
    "ShotZoneSeason",
    "LeagueSeason",
    "IdCrosswalk",
    "SyncState",
    "IngestLog",
    "SHOT_ZONES",
    "SHOT_ZONE_LABELS",
    "SEASON_TYPES",
    "GAME_STATUSES",
    "PLAYER_GAME_METRIC_COLUMNS",
    "PLAYER_GAME_ADVANCED_METRIC_COLUMNS",
    "TEAM_GAME_METRIC_COLUMNS",
    "PLAYER_SEASON_PER_GAME_COLUMNS",
    "PLAYER_SEASON_TOTALS_COLUMNS",
    "PLAYER_SEASON_RATE_COLUMNS",
    "TEAM_SEASON_PER_GAME_COLUMNS",
    "TEAM_SEASON_TOTALS_COLUMNS",
    "TEAM_SEASON_RATE_COLUMNS",
    "season_column_for",
    "render_schema_sql",
]

#: Court zones, in the order the ``shot_profile`` widget renders them.
SHOT_ZONES = ("rim", "paint_non_rim", "mid_range", "corner_three", "above_break_three")

SHOT_ZONE_LABELS = {
    "rim": "At Rim",
    "paint_non_rim": "Paint (non-rim)",
    "mid_range": "Mid Range",
    "corner_three": "Corner 3",
    "above_break_three": "Above the Break 3",
}

SEASON_TYPES = ("Regular Season", "Playoffs", "Play In", "All Star", "Pre Season")

GAME_STATUSES = ("scheduled", "live", "final")


class Base(DeclarativeBase):
    """Declarative base for every Hardwood table."""


def _num() -> Any:
    """A nullable float column — every advanced/derived stat is one of these."""
    return mapped_column(Float, nullable=True)


def _count() -> Any:
    """A nullable integer box-score count (``NULL`` when the era did not record it)."""
    return mapped_column(Integer, nullable=True)


# --------------------------------------------------------------------------- reference


class Team(Base):
    """A franchise. Ids are the real NBA.com team ids."""

    __tablename__ = "teams"

    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    abbr: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    city: Mapped[str] = mapped_column(String(64), nullable=False)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    conference: Mapped[str | None] = mapped_column(String(8), nullable=True)
    division: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    year_founded: Mapped[int | None] = _count()
    year_last_active: Mapped[int | None] = _count()


class Player(Base):
    """A player and their biography. Ids are NBA.com person ids."""

    __tablename__ = "players"
    __table_args__ = (
        Index("ix_players_last_name", "last_name"),
        Index("ix_players_is_active", "is_active"),
    )

    player_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    full_name: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    first_name: Mapped[str | None] = mapped_column(String(48), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(48), nullable=True)
    bbref_slug: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    position: Mapped[str | None] = mapped_column(String(8), nullable=True)
    height: Mapped[str | None] = mapped_column(String(8), nullable=True)
    weight: Mapped[int | None] = _count()
    birthdate: Mapped[date | None] = mapped_column(Date, nullable=True)
    country: Mapped[str | None] = mapped_column(String(48), nullable=True)
    school: Mapped[str | None] = mapped_column(String(96), nullable=True)
    draft_year: Mapped[int | None] = _count()
    draft_round: Mapped[int | None] = _count()
    draft_pick: Mapped[int | None] = _count()
    from_year: Mapped[int | None] = _count()
    to_year: Mapped[int | None] = _count()
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    jersey: Mapped[str | None] = mapped_column(String(8), nullable=True)
    headshot_url: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Game(Base):
    """One game. ``game_date`` is the NBA scheduling day in US Eastern."""

    __tablename__ = "games"
    __table_args__ = (
        Index("ix_games_game_date", "game_date"),
        Index("ix_games_season_type", "season", "season_type"),
        Index("ix_games_home_team", "home_team_id", "game_date"),
        Index("ix_games_away_team", "away_team_id", "game_date"),
    )

    game_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    season: Mapped[str] = mapped_column(String(8), nullable=False)
    season_type: Mapped[str] = mapped_column(String(16), nullable=False)
    home_team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("teams.team_id"), nullable=False
    )
    away_team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("teams.team_id"), nullable=False
    )
    home_pts: Mapped[int | None] = _count()
    away_pts: Mapped[int | None] = _count()
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="scheduled")
    period: Mapped[int | None] = _count()
    clock: Mapped[str | None] = mapped_column(String(8), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------- box scores


class PlayerGameBasic(Base):
    """The traditional box score line. Available for every era from 1946-47.

    Era-limited columns are ``NULL`` rather than ``0``: no three-pointers before 1979-80, no
    steals/blocks or OREB/DREB split before 1973-74, no individual turnovers before 1977-78,
    no plus/minus before 1996-97.
    """

    __tablename__ = "player_game_basic"
    __table_args__ = (
        Index("ix_pgb_player", "player_id"),
        Index("ix_pgb_team", "team_id"),
    )

    game_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("games.game_id"), primary_key=True
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.player_id"), primary_key=True
    )
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.team_id"), nullable=False)
    started: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    minutes: Mapped[float | None] = _num()
    fgm: Mapped[int | None] = _count()
    fga: Mapped[int | None] = _count()
    fg3m: Mapped[int | None] = _count()
    fg3a: Mapped[int | None] = _count()
    ftm: Mapped[int | None] = _count()
    fta: Mapped[int | None] = _count()
    oreb: Mapped[int | None] = _count()
    dreb: Mapped[int | None] = _count()
    reb: Mapped[int | None] = _count()
    ast: Mapped[int | None] = _count()
    stl: Mapped[int | None] = _count()
    blk: Mapped[int | None] = _count()
    tov: Mapped[int | None] = _count()
    pf: Mapped[int | None] = _count()
    pts: Mapped[int | None] = _count()
    plus_minus: Mapped[float | None] = _num()
    fantasy_pts: Mapped[float | None] = _num()
    data_source: Mapped[str | None] = mapped_column(String(16), nullable=True)


class PlayerGameAdvanced(Base):
    """Per-game advanced box score. Rows exist only from 1996-97 onward.

    That boundary is a data fact, not a convention: per-game ratings, plus/minus and shot
    charts begin with league-wide play-by-play in 1996-97.
    """

    __tablename__ = "player_game_advanced"
    __table_args__ = (Index("ix_pga_player", "player_id"),)

    game_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("games.game_id"), primary_key=True
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.player_id"), primary_key=True
    )
    off_rtg: Mapped[float | None] = _num()
    def_rtg: Mapped[float | None] = _num()
    net_rtg: Mapped[float | None] = _num()
    ast_pct: Mapped[float | None] = _num()
    ast_tov: Mapped[float | None] = _num()
    ast_ratio: Mapped[float | None] = _num()
    oreb_pct: Mapped[float | None] = _num()
    dreb_pct: Mapped[float | None] = _num()
    reb_pct: Mapped[float | None] = _num()
    tov_pct: Mapped[float | None] = _num()
    efg_pct: Mapped[float | None] = _num()
    ts_pct: Mapped[float | None] = _num()
    usg_pct: Mapped[float | None] = _num()
    pace: Mapped[float | None] = _num()
    poss: Mapped[float | None] = _num()
    pie: Mapped[float | None] = _num()
    game_score: Mapped[float | None] = _num()
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data_source: Mapped[str | None] = mapped_column(String(16), nullable=True)


class TeamGame(Base):
    """One team's side of one game: the box, the four factors and the ratings."""

    __tablename__ = "team_game"
    __table_args__ = (Index("ix_team_game_team", "team_id"),)

    game_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("games.game_id"), primary_key=True
    )
    team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("teams.team_id"), primary_key=True
    )
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)
    won: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    minutes: Mapped[float | None] = _num()
    pts: Mapped[int | None] = _count()
    opp_pts: Mapped[int | None] = _count()
    fgm: Mapped[int | None] = _count()
    fga: Mapped[int | None] = _count()
    fg3m: Mapped[int | None] = _count()
    fg3a: Mapped[int | None] = _count()
    ftm: Mapped[int | None] = _count()
    fta: Mapped[int | None] = _count()
    oreb: Mapped[int | None] = _count()
    dreb: Mapped[int | None] = _count()
    reb: Mapped[int | None] = _count()
    ast: Mapped[int | None] = _count()
    stl: Mapped[int | None] = _count()
    blk: Mapped[int | None] = _count()
    tov: Mapped[int | None] = _count()
    pf: Mapped[int | None] = _count()
    plus_minus: Mapped[float | None] = _num()
    fg_pct: Mapped[float | None] = _num()
    fg3_pct: Mapped[float | None] = _num()
    ft_pct: Mapped[float | None] = _num()
    fg3a_rate: Mapped[float | None] = _num()
    pps: Mapped[float | None] = _num()
    ts_pct: Mapped[float | None] = _num()
    # Dean Oliver's four factors, offense.
    efg_pct: Mapped[float | None] = _num()
    tov_pct: Mapped[float | None] = _num()
    oreb_pct: Mapped[float | None] = _num()
    ftr: Mapped[float | None] = _num()
    # …and the same four conceded to the opponent.
    opp_efg_pct: Mapped[float | None] = _num()
    opp_tov_pct: Mapped[float | None] = _num()
    opp_oreb_pct: Mapped[float | None] = _num()
    opp_ftr: Mapped[float | None] = _num()
    off_rtg: Mapped[float | None] = _num()
    def_rtg: Mapped[float | None] = _num()
    net_rtg: Mapped[float | None] = _num()
    pace: Mapped[float | None] = _num()
    poss: Mapped[float | None] = _num()
    data_source: Mapped[str | None] = mapped_column(String(16), nullable=True)


# --------------------------------------------------------------------------- aggregates


class PlayerSeason(Base):
    """A player's season for one team: per-game values, totals and the advanced slate.

    ``minutes`` is the season total; ``min_pg`` is per game. Unsuffixed counting columns
    (``pts``, ``reb``, …) are per game and the ``_tot`` twins are season totals, so
    ``perMode=PerGame`` and ``perMode=Totals`` are both a single column read.

    ``is_estimated`` marks a row whose advanced values come from box-score formulas rather
    than possession data — every season before 1996-97.
    """

    __tablename__ = "player_season"
    __table_args__ = (
        Index("ix_player_season_player", "player_id", "season", "season_type"),
        Index("ix_player_season_season", "season", "season_type"),
        Index("ix_player_season_team", "team_id", "season", "season_type"),
    )

    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.player_id"), primary_key=True
    )
    season: Mapped[str] = mapped_column(String(8), primary_key=True)
    season_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("teams.team_id"), primary_key=True
    )

    age: Mapped[int | None] = _count()
    gp: Mapped[int | None] = _count()
    gs: Mapped[int | None] = _count()
    minutes: Mapped[float | None] = _num()
    min_pg: Mapped[float | None] = _num()

    # Per game.
    pts: Mapped[float | None] = _num()
    reb: Mapped[float | None] = _num()
    oreb: Mapped[float | None] = _num()
    dreb: Mapped[float | None] = _num()
    ast: Mapped[float | None] = _num()
    stl: Mapped[float | None] = _num()
    blk: Mapped[float | None] = _num()
    tov: Mapped[float | None] = _num()
    pf: Mapped[float | None] = _num()
    fgm: Mapped[float | None] = _num()
    fga: Mapped[float | None] = _num()
    fg3m: Mapped[float | None] = _num()
    fg3a: Mapped[float | None] = _num()
    ftm: Mapped[float | None] = _num()
    fta: Mapped[float | None] = _num()
    plus_minus: Mapped[float | None] = _num()
    fantasy_pts: Mapped[float | None] = _num()
    game_score: Mapped[float | None] = _num()

    # Season totals.
    pts_tot: Mapped[int | None] = _count()
    reb_tot: Mapped[int | None] = _count()
    oreb_tot: Mapped[int | None] = _count()
    dreb_tot: Mapped[int | None] = _count()
    ast_tot: Mapped[int | None] = _count()
    stl_tot: Mapped[int | None] = _count()
    blk_tot: Mapped[int | None] = _count()
    tov_tot: Mapped[int | None] = _count()
    pf_tot: Mapped[int | None] = _count()
    fgm_tot: Mapped[int | None] = _count()
    fga_tot: Mapped[int | None] = _count()
    fg3m_tot: Mapped[int | None] = _count()
    fg3a_tot: Mapped[int | None] = _count()
    ftm_tot: Mapped[int | None] = _count()
    fta_tot: Mapped[int | None] = _count()
    plus_minus_tot: Mapped[float | None] = _num()
    fantasy_pts_tot: Mapped[float | None] = _num()

    # Shooting rates.
    fg_pct: Mapped[float | None] = _num()
    fg3_pct: Mapped[float | None] = _num()
    ft_pct: Mapped[float | None] = _num()
    efg_pct: Mapped[float | None] = _num()
    ts_pct: Mapped[float | None] = _num()
    fg3a_rate: Mapped[float | None] = _num()
    ftr: Mapped[float | None] = _num()
    pps: Mapped[float | None] = _num()

    # Advanced.
    off_rtg: Mapped[float | None] = _num()
    def_rtg: Mapped[float | None] = _num()
    net_rtg: Mapped[float | None] = _num()
    usg_pct: Mapped[float | None] = _num()
    ast_pct: Mapped[float | None] = _num()
    ast_tov: Mapped[float | None] = _num()
    ast_ratio: Mapped[float | None] = _num()
    oreb_pct: Mapped[float | None] = _num()
    dreb_pct: Mapped[float | None] = _num()
    reb_pct: Mapped[float | None] = _num()
    tov_pct: Mapped[float | None] = _num()
    stl_pct: Mapped[float | None] = _num()
    blk_pct: Mapped[float | None] = _num()
    pace: Mapped[float | None] = _num()
    poss: Mapped[float | None] = _num()
    pie: Mapped[float | None] = _num()

    # Season-only ratings (no per-game meaning).
    per: Mapped[float | None] = _num()
    ws: Mapped[float | None] = _num()
    ows: Mapped[float | None] = _num()
    dws: Mapped[float | None] = _num()
    ws48: Mapped[float | None] = _num()
    bpm: Mapped[float | None] = _num()
    obpm: Mapped[float | None] = _num()
    dbpm: Mapped[float | None] = _num()
    vorp: Mapped[float | None] = _num()

    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data_source: Mapped[str | None] = mapped_column(String(16), nullable=True)


class TeamSeason(Base):
    """A team's season: record, box, four factors and ratings."""

    __tablename__ = "team_season"
    __table_args__ = (
        Index("ix_team_season_season", "season", "season_type"),
    )

    team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("teams.team_id"), primary_key=True
    )
    season: Mapped[str] = mapped_column(String(8), primary_key=True)
    season_type: Mapped[str] = mapped_column(String(16), primary_key=True)

    gp: Mapped[int | None] = _count()
    wins: Mapped[int | None] = _count()
    losses: Mapped[int | None] = _count()
    win_pct: Mapped[float | None] = _num()

    pts: Mapped[float | None] = _num()
    opp_pts: Mapped[float | None] = _num()
    reb: Mapped[float | None] = _num()
    oreb: Mapped[float | None] = _num()
    dreb: Mapped[float | None] = _num()
    ast: Mapped[float | None] = _num()
    stl: Mapped[float | None] = _num()
    blk: Mapped[float | None] = _num()
    tov: Mapped[float | None] = _num()
    pf: Mapped[float | None] = _num()
    fgm: Mapped[float | None] = _num()
    fga: Mapped[float | None] = _num()
    fg3m: Mapped[float | None] = _num()
    fg3a: Mapped[float | None] = _num()
    ftm: Mapped[float | None] = _num()
    fta: Mapped[float | None] = _num()
    min_pg: Mapped[float | None] = _num()

    pts_tot: Mapped[int | None] = _count()
    reb_tot: Mapped[int | None] = _count()
    ast_tot: Mapped[int | None] = _count()
    fgm_tot: Mapped[int | None] = _count()
    fga_tot: Mapped[int | None] = _count()
    fg3m_tot: Mapped[int | None] = _count()
    fg3a_tot: Mapped[int | None] = _count()
    ftm_tot: Mapped[int | None] = _count()
    fta_tot: Mapped[int | None] = _count()
    minutes: Mapped[float | None] = _num()

    fg_pct: Mapped[float | None] = _num()
    fg3_pct: Mapped[float | None] = _num()
    ft_pct: Mapped[float | None] = _num()
    efg_pct: Mapped[float | None] = _num()
    ts_pct: Mapped[float | None] = _num()
    fg3a_rate: Mapped[float | None] = _num()
    ftr: Mapped[float | None] = _num()
    pps: Mapped[float | None] = _num()
    tov_pct: Mapped[float | None] = _num()
    oreb_pct: Mapped[float | None] = _num()
    dreb_pct: Mapped[float | None] = _num()
    reb_pct: Mapped[float | None] = _num()

    opp_efg_pct: Mapped[float | None] = _num()
    opp_tov_pct: Mapped[float | None] = _num()
    opp_oreb_pct: Mapped[float | None] = _num()
    opp_ftr: Mapped[float | None] = _num()

    off_rtg: Mapped[float | None] = _num()
    def_rtg: Mapped[float | None] = _num()
    net_rtg: Mapped[float | None] = _num()
    pace: Mapped[float | None] = _num()
    poss: Mapped[float | None] = _num()

    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data_source: Mapped[str | None] = mapped_column(String(16), nullable=True)


class ShotZoneSeason(Base):
    """Shot distribution by court zone, for a player or a team, for one season.

    Shot charts begin in 1996-97, so earlier seasons have no rows at all.
    ``fga``/``fgm`` are per game; ``fga_tot``/``fgm_tot`` are season totals.
    """

    __tablename__ = "shot_zone_season"
    __table_args__ = (
        Index("ix_shot_zone_subject", "subject_type", "subject_id", "season"),
    )

    subject_type: Mapped[str] = mapped_column(String(8), primary_key=True)
    subject_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[str] = mapped_column(String(8), primary_key=True)
    season_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    zone: Mapped[str] = mapped_column(String(24), primary_key=True)

    fga: Mapped[float | None] = _num()
    fgm: Mapped[float | None] = _num()
    fga_tot: Mapped[int | None] = _count()
    fgm_tot: Mapped[int | None] = _count()
    fg_pct: Mapped[float | None] = _num()
    share_of_fga: Mapped[float | None] = _num()
    points_per_shot: Mapped[float | None] = _num()


class LeagueSeason(Base):
    """League-wide distribution of one metric, for one season and subject type.

    This is the table behind every percentile bar and every "league average" caption.
    ``subject_type`` is part of the key: a player's rating distribution is not a team's.
    """

    __tablename__ = "league_season"

    subject_type: Mapped[str] = mapped_column(String(8), primary_key=True)
    season: Mapped[str] = mapped_column(String(8), primary_key=True)
    season_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    metric_key: Mapped[str] = mapped_column(String(32), primary_key=True)

    sample_size: Mapped[int | None] = _count()
    average: Mapped[float | None] = _num()
    stddev: Mapped[float | None] = _num()
    p10: Mapped[float | None] = _num()
    p25: Mapped[float | None] = _num()
    p50: Mapped[float | None] = _num()
    p75: Mapped[float | None] = _num()
    p90: Mapped[float | None] = _num()
    min_value: Mapped[float | None] = _num()
    max_value: Mapped[float | None] = _num()


# --------------------------------------------------------------------------- plumbing


class IdCrosswalk(Base):
    """Maps an NBA person id to the other public id spaces."""

    __tablename__ = "id_crosswalk"

    nba_person_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    bbref_slug: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    espn_id: Mapped[int | None] = _count()
    balldontlie_id: Mapped[int | None] = _count()
    confidence: Mapped[float | None] = _num()
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)


class SyncState(Base):
    """Singleton row holding the freshness cursor the clients poll.

    ``sync_version`` increments once per finalized game; ``data_through`` moves to a date
    once every game on that slate is final.
    """

    __tablename__ = "sync_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_sync_state_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)
    sync_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data_through: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    games_ingested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class IngestLog(Base):
    """One row per ingest run, for operational forensics."""

    __tablename__ = "ingest_log"
    __table_args__ = (Index("ix_ingest_log_started", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    job: Mapped[str] = mapped_column(String(32), nullable=False)
    season: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    games_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


# ------------------------------------------------------- metric key → column mappings

#: ``metrics.json`` key → ``player_game_basic`` column.
PLAYER_GAME_METRIC_COLUMNS: dict[str, str] = {
    "min": "minutes",
    "pts": "pts",
    "reb": "reb",
    "oreb": "oreb",
    "dreb": "dreb",
    "ast": "ast",
    "stl": "stl",
    "blk": "blk",
    "tov": "tov",
    "pf": "pf",
    "fgm": "fgm",
    "fga": "fga",
    "fg3m": "fg3m",
    "fg3a": "fg3a",
    "ftm": "ftm",
    "fta": "fta",
    "plus_minus": "plus_minus",
    "fantasy_pts": "fantasy_pts",
}

#: ``metrics.json`` key → ``player_game_advanced`` column (1996-97 onward only).
PLAYER_GAME_ADVANCED_METRIC_COLUMNS: dict[str, str] = {
    key: key
    for key in (
        "off_rtg",
        "def_rtg",
        "net_rtg",
        "ast_pct",
        "ast_tov",
        "ast_ratio",
        "oreb_pct",
        "dreb_pct",
        "reb_pct",
        "tov_pct",
        "efg_pct",
        "ts_pct",
        "usg_pct",
        "pace",
        "poss",
        "pie",
        "game_score",
    )
}

#: ``metrics.json`` key → ``team_game`` column.
TEAM_GAME_METRIC_COLUMNS: dict[str, str] = {
    "min": "minutes",
    **{
        key: key
        for key in (
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
            "fg_pct",
            "fg3_pct",
            "ft_pct",
            "efg_pct",
            "ts_pct",
            "fg3a_rate",
            "ftr",
            "pps",
            "tov_pct",
            "oreb_pct",
            "off_rtg",
            "def_rtg",
            "net_rtg",
            "pace",
            "poss",
            "opp_efg_pct",
            "opp_tov_pct",
            "opp_oreb_pct",
            "opp_ftr",
        )
    },
}

#: ``metrics.json`` key → ``player_season`` column for ``perMode=PerGame``.
PLAYER_SEASON_PER_GAME_COLUMNS: dict[str, str] = {
    "min": "min_pg",
    "gp": "gp",
    "gs": "gs",
    **{
        key: key
        for key in (
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
            "fantasy_pts",
            "game_score",
        )
    },
}

#: ``metrics.json`` key → ``player_season`` column for ``perMode=Totals``.
PLAYER_SEASON_TOTALS_COLUMNS: dict[str, str] = {
    "min": "minutes",
    "gp": "gp",
    "gs": "gs",
    "game_score": "game_score",
    **{
        key: f"{key}_tot"
        for key in (
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
            "fantasy_pts",
        )
    },
}

#: Rate and rating metrics — identical under every ``perMode``.
PLAYER_SEASON_RATE_COLUMNS: dict[str, str] = {
    key: key
    for key in (
        "fg_pct",
        "fg3_pct",
        "ft_pct",
        "efg_pct",
        "ts_pct",
        "fg3a_rate",
        "ftr",
        "pps",
        "off_rtg",
        "def_rtg",
        "net_rtg",
        "usg_pct",
        "ast_pct",
        "ast_tov",
        "ast_ratio",
        "oreb_pct",
        "dreb_pct",
        "reb_pct",
        "tov_pct",
        "stl_pct",
        "blk_pct",
        "pace",
        "poss",
        "pie",
        "per",
        "ws",
        "ows",
        "dws",
        "ws48",
        "bpm",
        "obpm",
        "dbpm",
        "vorp",
    )
}

TEAM_SEASON_PER_GAME_COLUMNS: dict[str, str] = {
    "min": "min_pg",
    "gp": "gp",
    "wins": "wins",
    "losses": "losses",
    **{
        key: key
        for key in (
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
        )
    },
}

TEAM_SEASON_TOTALS_COLUMNS: dict[str, str] = {
    "min": "minutes",
    "gp": "gp",
    "wins": "wins",
    "losses": "losses",
    **{
        key: f"{key}_tot"
        for key in ("pts", "reb", "ast", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta")
    },
}

TEAM_SEASON_RATE_COLUMNS: dict[str, str] = {
    key: key
    for key in (
        "win_pct",
        "fg_pct",
        "fg3_pct",
        "ft_pct",
        "efg_pct",
        "ts_pct",
        "fg3a_rate",
        "ftr",
        "pps",
        "tov_pct",
        "oreb_pct",
        "dreb_pct",
        "reb_pct",
        "opp_efg_pct",
        "opp_tov_pct",
        "opp_oreb_pct",
        "opp_ftr",
        "off_rtg",
        "def_rtg",
        "net_rtg",
        "pace",
        "poss",
    )
}


def season_column_for(
    metric_key: str, subject_type: str = "player", per_mode: str = "PerGame"
) -> str | None:
    """Column on ``player_season``/``team_season`` holding ``metric_key`` for ``per_mode``.

    Returns ``None`` when the metric is not stored for that subject — including ``Per36``
    and ``Per100``, which the API derives from the ``Totals`` columns plus minutes or
    possessions rather than storing twice.
    """
    if subject_type == "team":
        rates, per_game, totals = (
            TEAM_SEASON_RATE_COLUMNS,
            TEAM_SEASON_PER_GAME_COLUMNS,
            TEAM_SEASON_TOTALS_COLUMNS,
        )
    else:
        rates, per_game, totals = (
            PLAYER_SEASON_RATE_COLUMNS,
            PLAYER_SEASON_PER_GAME_COLUMNS,
            PLAYER_SEASON_TOTALS_COLUMNS,
        )
    if metric_key in rates:
        return rates[metric_key]
    if per_mode == "Totals":
        return totals.get(metric_key)
    if per_mode == "PerGame":
        return per_game.get(metric_key)
    return None


def render_schema_sql() -> str:
    """Emit the whole schema as SQLite DDL — the source of ``nbastats/schema.sql``."""
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.schema import CreateIndex, CreateTable

    dialect = sqlite.dialect()
    lines = [
        "-- Hardwood schema, generated by nbastats.models.render_schema_sql().",
        "-- Do not edit by hand: run `python3 -m nbastats.models > nbastats/schema.sql`.",
        "-- Dialect: SQLite. A Postgres DATABASE_URL works from the same models without",
        "-- code changes; run init_db() there rather than this file.",
        "",
    ]
    for table in Base.metadata.sorted_tables:
        lines.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        lines.append("")
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            lines.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
        if table.indexes:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":  # pragma: no cover - developer utility
    print(render_schema_sql(), end="")
