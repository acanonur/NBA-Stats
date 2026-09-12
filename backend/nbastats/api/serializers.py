"""The single place ORM rows become contract objects.

Every route and every widget resolver goes through these functions, for one reason: era
honesty has to be applied in exactly one place or it will be applied inconsistently. The
rule, from ``contracts/CONTRACT.md`` §6, is::

    the catalog decides whether a metric existed for this subject's era;
    if it did not, the value is NULL and the availability is "unavailable" — never 0

so :func:`metric_value` never formats a number the era cannot support, and the ``*_values``
helpers never read a column the catalog says does not apply. A stored value that survives in
the database for a season the catalog rules out (a bad ingest, say) is dropped here rather
than served.

Public surface, stable for the widget layer:

``player_ref(row, team=None)``                      a :class:`~nbastats.api.schemas.PlayerRef`
``team_ref(row)``                                   a :class:`~nbastats.api.schemas.TeamRef`
``game_ref(row, home=None, away=None, teams=None)`` a :class:`~nbastats.api.schemas.GameRef`
``metric_value(key, value, …)``                     a :class:`~nbastats.api.schemas.MetricValue`
``season_row(row, values, …)``                      a :class:`~nbastats.api.schemas.SeasonRow`
``metric_descriptor(key)``                          the catalog entry, as a model

plus the value extractors (:func:`player_game_values`, :func:`player_season_values`,
:func:`team_season_values`, :func:`team_game_values`) and :func:`load_player_teams`, which
resolves the team a ``PlayerRef`` should name — the ``players`` table holds none, because a
career spans several.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog
from ..metrics import compute_metric
from ..models import (
    PLAYER_GAME_ADVANCED_METRIC_COLUMNS,
    PLAYER_GAME_METRIC_COLUMNS,
    TEAM_GAME_METRIC_COLUMNS,
    Game,
    Player,
    PlayerGameAdvanced,
    PlayerGameBasic,
    PlayerSeason,
    Team,
    TeamGame,
    TeamSeason,
    season_column_for,
)
from .schemas import (
    Availability,
    DraftInfo,
    GameRef,
    MetricDescriptor,
    MetricValue,
    PlayerBio,
    PlayerRef,
    SeasonRow,
    TeamRef,
)

__all__ = [
    "player_ref",
    "team_ref",
    "game_ref",
    "metric_value",
    "metric_descriptor",
    "season_row",
    "player_bio",
    "availability_for",
    "combined_availability",
    "player_game_values",
    "player_season_values",
    "team_season_values",
    "team_game_values",
    "load_player_teams",
    "load_teams",
]

#: Season granularity for :func:`catalog.metric_availability`.
_SEASON = "season"
#: Single-game granularity: the ``perGameFrom`` boundary rather than ``seasonFrom``.
_GAME = "game"

#: How many ids to put in one ``IN (…)`` clause — SQLite caps bound parameters.
_CHUNK = 400

#: Metrics a single box-score line can produce on its own, with no team or opponent context.
#: Before 1996-97 there is no ``player_game_advanced`` row at all, so without this a game
#: score the era *did* record would be served as ``null``. The era gate still runs first: a
#: metric the catalog rules out for the season is never computed, only ones it allows.
_DERIVABLE_FROM_BOX = frozenset(
    {
        "reb",
        "fg_pct",
        "fg3_pct",
        "ft_pct",
        "efg_pct",
        "ts_pct",
        "fg3a_rate",
        "ftr",
        "pps",
        "ast_tov",
        "ast_ratio",
        "tov_pct",
        "game_score",
        "fantasy_pts",
    }
)


# --------------------------------------------------------------------------- refs


def player_ref(
    row: Player,
    team: Team | None = None,
    *,
    team_id: int | None = None,
    team_abbr: str | None = None,
) -> PlayerRef:
    """Build a ``PlayerRef`` from a ``players`` row.

    ``team`` (or the explicit ``team_id``/``team_abbr`` pair) fills the team fields; without
    it they are ``null``, which the contract allows. Use :func:`load_player_teams` to get the
    team a player last played for.
    """
    first = row.first_name
    last = row.last_name
    if not first or not last:
        # Older ingests stored only the full name; split it rather than emit a null the
        # client would render as a blank line.
        parts = (row.full_name or "").split(" ", 1)
        first = first or (parts[0] if parts else None)
        last = last or (parts[1] if len(parts) > 1 else None)
    return PlayerRef(
        player_id=row.player_id,
        name=row.full_name,
        first_name=first,
        last_name=last,
        team_id=team.team_id if team is not None else team_id,
        team_abbr=team.abbr if team is not None else team_abbr,
        position=row.position,
        jersey=row.jersey,
        headshot_url=row.headshot_url,
        is_active=bool(row.is_active),
    )


def team_ref(row: Team) -> TeamRef:
    """Build a ``TeamRef`` from a ``teams`` row."""
    return TeamRef(
        team_id=row.team_id,
        abbr=row.abbr,
        name=row.name,
        city=row.city,
        nickname=row.nickname,
        conference=row.conference,
        division=row.division,
    )


def game_ref(
    row: Game,
    home: Team | TeamRef | None = None,
    away: Team | TeamRef | None = None,
    *,
    teams: Mapping[int, Team] | None = None,
) -> GameRef:
    """Build a ``GameRef`` from a ``games`` row.

    The two franchises must be supplied, either directly as ``home``/``away`` (``Team`` rows
    or ready-made ``TeamRef``s) or through ``teams``, a ``{team_id: Team}`` map — there are
    no ORM relationships on ``Game``, so the caller owns that query and can batch it.
    """
    resolved_home = _coerce_team(home, row.home_team_id, teams)
    resolved_away = _coerce_team(away, row.away_team_id, teams)
    return GameRef(
        game_id=row.game_id,
        date=row.game_date,
        season=row.season,
        season_type=row.season_type,
        home=resolved_home,
        away=resolved_away,
        home_pts=row.home_pts,
        away_pts=row.away_pts,
        status=row.status if row.status in ("scheduled", "live", "final") else "scheduled",
        period=row.period,
        clock=row.clock,
        finalized_at=row.finalized_at,
    )


def _coerce_team(
    supplied: Team | TeamRef | None, team_id: int, teams: Mapping[int, Team] | None
) -> TeamRef:
    if isinstance(supplied, TeamRef):
        return supplied
    if supplied is not None:
        return team_ref(supplied)
    if teams is not None and team_id in teams:
        return team_ref(teams[team_id])
    raise ValueError(f"game_ref needs the Team row for team_id {team_id}")


def player_bio(row: Player) -> PlayerBio:
    """The biography block of ``GET /v1/players/{playerId}``."""
    draft = DraftInfo(year=row.draft_year, round=row.draft_round, pick=row.draft_pick)
    return PlayerBio(
        height=row.height,
        weight=row.weight,
        birthdate=row.birthdate,
        country=row.country,
        draft=draft,
        school=row.school,
        from_year=row.from_year,
        to_year=row.to_year,
        bbref_slug=row.bbref_slug,
    )


def metric_descriptor(key: str) -> MetricDescriptor:
    """One ``contracts/metrics.json`` entry as a model, for payloads that embed it."""
    return MetricDescriptor.model_validate(catalog.metric(key))


# --------------------------------------------------------------------------- era rules


def availability_for(
    key: str, season: str | Sequence[str] | None, granularity: str = _SEASON
) -> Availability:
    """Era availability of one metric, with ``"career"`` treated as the whole record."""
    if isinstance(season, str) and season.lower() in ("career", "all_time", "all-time"):
        return catalog.metric_availability(key, None, granularity)
    return catalog.metric_availability(key, season, granularity)


def combined_availability(
    keys: Iterable[str], season: str | Sequence[str] | None, granularity: str = _SEASON
) -> Availability:
    """Fold several metrics' availabilities into the one a row should advertise.

    A row mixing measured and era-missing columns is ``"partial"``: that is the caret and
    footnote treatment, and it is the honest answer for a 1992-93 line whose TS% is real but
    whose offensive rating cannot exist.
    """
    values = [availability_for(key, season, granularity) for key in keys]
    return catalog.combine_availability(values) if values else "full"


# --------------------------------------------------------------------------- metric value


def metric_value(
    key: str,
    value: float | int | None,
    rank: int | None = None,
    percentile: float | None = None,
    league_average: float | None = None,
    delta: float | None = None,
    season: str | Sequence[str] | None = None,
    *,
    granularity: str = _SEASON,
    availability: Availability | None = None,
) -> MetricValue:
    """Build the atom every widget renders.

    ``value`` is the raw number in the metric's native unit — percentages are fractions in
    ``[0, 1]``. ``displayValue`` comes from :func:`catalog.format_metric` and ``availability``
    from :func:`catalog.metric_availability` for ``season`` at ``granularity``
    (``"season"`` or ``"game"``), unless the caller overrides it.

    Two guarantees the caller cannot accidentally break:

    * a metric the era never recorded is served as ``null`` with ``"unavailable"``, whatever
      value was passed in;
    * a ``null`` value never carries a rank, a percentile or a formatted number.
    """
    resolved: Availability = availability or availability_for(key, season, granularity)
    number: float | int | None = value
    if resolved == "unavailable":
        number = None
    elif number is None:
        # The metric exists for this era but the subject has no value: a DNP, an unplayed
        # season, a column the ingest has not filled. Claiming "full" would be a lie.
        resolved = "unavailable"
    if number is None:
        rank = None
        percentile = None
        delta = None
    return MetricValue(
        metric=key,
        value=number,
        display_value=catalog.format_metric(key, number),
        rank=rank,
        percentile=percentile,
        league_average=league_average,
        delta=delta,
        is_estimated=resolved == "estimated",
        availability=resolved,
    )


def season_row(
    row: PlayerSeason | None,
    values: Mapping[str, Optional[float]],
    *,
    season: str | None = None,
    season_type: str | None = None,
    team_abbr: str | None = None,
    age: int | None = None,
    gp: int | None = None,
    availability: Availability | None = None,
) -> SeasonRow:
    """Build one ``SeasonRow`` — a career line, or one season for one team.

    ``row`` may be ``None`` for a synthesised line (the career total, whose ``season`` is the
    literal ``"Career"``); everything it would have supplied can be passed explicitly. The
    row's availability is folded from the metrics in ``values`` over that season, so a line
    that mixes measured and era-missing columns reports ``"partial"``.
    """
    resolved_season = season or (row.season if row is not None else "Career")
    resolved_type = season_type or (row.season_type if row is not None else None)
    resolved_age = age if age is not None else (row.age if row is not None else None)
    resolved_gp = gp if gp is not None else (row.gp if row is not None else None)
    if availability is None:
        era_season = None if resolved_season in ("Career", "career") else resolved_season
        availability = combined_availability(values.keys(), era_season, _SEASON)
    return SeasonRow(
        season=resolved_season,
        season_type=resolved_type,
        team_abbr=team_abbr,
        age=resolved_age,
        gp=resolved_gp,
        values=dict(values),
        availability=availability,
    )


# --------------------------------------------------------------------------- values


def _column(row: Any, column: str | None) -> float | int | None:
    if row is None or column is None:
        return None
    return getattr(row, column, None)


def player_game_values(
    basic: PlayerGameBasic | None,
    advanced: PlayerGameAdvanced | None,
    keys: Sequence[str],
    season: str,
) -> dict[str, Optional[float]]:
    """One game's values for ``keys``, era-correct.

    Every requested key is present. A key whose metric did not exist per game in that
    season — TS% before 1996-97, plus/minus before 1996-97, steals before 1973-74 — is
    present with a ``None`` value, because the client must render an em dash and never a 0.

    Where the era allows a metric but no stored column holds it (a pre-1996-97 game, which
    has no advanced row at all), a value the box score can derive on its own is computed
    through :func:`nbastats.metrics.compute_metric` rather than reported as missing.
    """
    out: dict[str, Optional[float]] = {}
    box: dict[str, Any] | None = None
    for key in keys:
        if availability_for(key, season, _GAME) == "unavailable":
            out[key] = None
            continue
        if key in PLAYER_GAME_METRIC_COLUMNS:
            value = _column(basic, PLAYER_GAME_METRIC_COLUMNS[key])
        elif key in PLAYER_GAME_ADVANCED_METRIC_COLUMNS:
            value = _column(advanced, PLAYER_GAME_ADVANCED_METRIC_COLUMNS[key])
        else:
            value = None
        if value is None and basic is not None and key in _DERIVABLE_FROM_BOX:
            if box is None:
                box = _box_row(basic)
            value = compute_metric(key, row=box)
        out[key] = value
    return out


def _box_row(basic: PlayerGameBasic) -> dict[str, Any]:
    """A player's box line as the plain snake_case dict the metrics engine reads."""
    row: dict[str, Any] = {
        key: getattr(basic, column, None)
        for key, column in PLAYER_GAME_METRIC_COLUMNS.items()
    }
    row["reb"] = basic.reb
    return row


def player_season_values(
    row: PlayerSeason | None,
    keys: Sequence[str],
    season: str | None = None,
    per_mode: str = "PerGame",
) -> dict[str, Optional[float]]:
    """A player's season values for ``keys`` under ``per_mode``, era-correct."""
    target = season or (row.season if row is not None else None)
    out: dict[str, Optional[float]] = {}
    for key in keys:
        if availability_for(key, target, _SEASON) == "unavailable":
            out[key] = None
            continue
        out[key] = _column(row, season_column_for(key, "player", per_mode))
    return out


def team_season_values(
    row: TeamSeason | None,
    keys: Sequence[str],
    season: str | None = None,
    per_mode: str = "PerGame",
) -> dict[str, Optional[float]]:
    """A team's season values for ``keys`` under ``per_mode``, era-correct."""
    target = season or (row.season if row is not None else None)
    out: dict[str, Optional[float]] = {}
    for key in keys:
        if availability_for(key, target, _SEASON) == "unavailable":
            out[key] = None
            continue
        out[key] = _column(row, season_column_for(key, "team", per_mode))
    return out


def team_game_values(
    row: TeamGame | None, keys: Sequence[str], season: str
) -> dict[str, Optional[float]]:
    """One team's values from a single game, era-correct."""
    out: dict[str, Optional[float]] = {}
    for key in keys:
        if availability_for(key, season, _GAME) == "unavailable":
            out[key] = None
            continue
        out[key] = _column(row, TEAM_GAME_METRIC_COLUMNS.get(key))
    return out


# --------------------------------------------------------------------------- lookups


def load_teams(session: Session, team_ids: Iterable[int]) -> dict[int, Team]:
    """``{team_id: Team}`` for the ids given, in as few queries as SQLite allows."""
    ids = sorted({int(team_id) for team_id in team_ids})
    out: dict[int, Team] = {}
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start : start + _CHUNK]
        rows = session.execute(select(Team).where(Team.team_id.in_(chunk))).scalars().all()
        out.update({team.team_id: team for team in rows})
    return out


def load_player_teams(
    session: Session, player_ids: Iterable[int], season: str | None = None
) -> dict[int, Team]:
    """The team each player should be shown with: ``{player_id: Team}``.

    ``players`` carries no team, so this reads ``player_season``. With ``season`` the answer
    is that season's team, falling back to the player's most recent team when they did not
    play it; without it, simply the most recent. Regular-season rows win ties over playoff
    rows for the same season, and more games played wins after that — a ten-day contract
    should not rename a player's team.
    """
    ids = sorted({int(pid) for pid in player_ids})
    if not ids:
        return {}

    preferred: dict[int, tuple[tuple[int, int, int], int]] = {}
    latest: dict[int, tuple[tuple[int, int, int], int]] = {}
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start : start + _CHUNK]
        rows = session.execute(
            select(
                PlayerSeason.player_id,
                PlayerSeason.season,
                PlayerSeason.season_type,
                PlayerSeason.team_id,
                PlayerSeason.gp,
            ).where(PlayerSeason.player_id.in_(chunk))
        ).all()
        for player_id, row_season, season_type, team_id, gp in rows:
            rank = (
                catalog.season_sort_key(row_season),
                1 if season_type == "Regular Season" else 0,
                int(gp or 0),
            )
            if player_id not in latest or rank > latest[player_id][0]:
                latest[player_id] = (rank, team_id)
            if season is not None and row_season == season:
                if player_id not in preferred or rank > preferred[player_id][0]:
                    preferred[player_id] = (rank, team_id)

    chosen = {pid: entry[1] for pid, entry in latest.items()}
    chosen.update({pid: entry[1] for pid, entry in preferred.items()})
    teams = load_teams(session, chosen.values())
    return {pid: teams[team_id] for pid, team_id in chosen.items() if team_id in teams}
