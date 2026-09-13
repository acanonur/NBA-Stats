"""``GET /v1/teams`` and ``GET /v1/teams/{teamId}``.

The list is the franchise directory the client uses for filters and team pickers; the detail
route is one team's season: record, team-level values and the roster with each player's
season line. Both answer with era-correct values — a 1992-93 team has no offensive rating,
so the key is present and ``null`` rather than absent or zero.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import select

from ..models import Player, PlayerSeason, Team, TeamSeason
from . import errors
from .deps import (
    SessionDep,
    ensure_season_loaded,
    parse_metric_keys,
    parse_season_type,
    resolve_season,
)
from .schemas import TeamDetail, TeamRecord, TeamRosterEntry, TeamsResponse
from .serializers import player_ref, player_season_values, team_ref, team_season_values

__all__ = ["router", "DEFAULT_TEAM_METRICS", "DEFAULT_ROSTER_METRICS"]

router = APIRouter()

#: The team-level slate the client shows without asking for anything specific.
DEFAULT_TEAM_METRICS: tuple[str, ...] = (
    "off_rtg",
    "def_rtg",
    "net_rtg",
    "pace",
    "efg_pct",
    "tov_pct",
    "oreb_pct",
    "ftr",
    "opp_efg_pct",
    "pts",
    "reb",
    "ast",
)

#: What each roster line carries.
DEFAULT_ROSTER_METRICS: tuple[str, ...] = (
    "gp",
    "min",
    "pts",
    "reb",
    "ast",
    "ts_pct",
    "usg_pct",
    "net_rtg",
)


@router.get("/teams", response_model=TeamsResponse, summary="Every franchise")
def list_teams(
    session: SessionDep,
    include_historical: bool = Query(False, alias="includeHistorical"),
) -> TeamsResponse:
    """The 30 current franchises, plus defunct ones when ``includeHistorical`` is set."""
    statement = select(Team).order_by(Team.abbr)
    if not include_historical:
        statement = statement.where(Team.is_active.is_(True))
    teams = session.execute(statement).scalars().all()
    return TeamsResponse(teams=[team_ref(team) for team in teams])


@router.get("/teams/{team_id}", response_model=TeamDetail, summary="One team's season")
def team_detail(
    session: SessionDep,
    team_id: int,
    season: Optional[str] = Query(None),
    season_type: Optional[str] = Query(None, alias="seasonType"),
    metrics: Optional[str] = Query(None, description="Comma-separated team metric keys."),
    roster_metrics: Optional[str] = Query(
        None, alias="rosterMetrics", description="Comma-separated player metric keys."
    ),
) -> TeamDetail:
    """Record, team values and roster for one season.

    A team that did not play the requested season comes back with a null record and null
    values rather than a 404: the franchise exists, the season simply is not its.
    """
    team = session.get(Team, team_id)
    if team is None:
        raise errors.team_not_found(team_id)

    resolved = str(resolve_season(session, season))
    ensure_season_loaded(session, resolved)
    wanted_type = parse_season_type(season_type)
    team_keys = parse_metric_keys(metrics, DEFAULT_TEAM_METRICS, scope="team")
    roster_keys = parse_metric_keys(
        roster_metrics, DEFAULT_ROSTER_METRICS, field="rosterMetrics", scope="player"
    )

    team_season = session.execute(
        select(TeamSeason)
        .where(TeamSeason.team_id == team_id)
        .where(TeamSeason.season == resolved)
        .where(TeamSeason.season_type == wanted_type)
    ).scalar_one_or_none()

    roster_rows = session.execute(
        select(PlayerSeason, Player)
        .join(Player, Player.player_id == PlayerSeason.player_id)
        .where(PlayerSeason.team_id == team_id)
        .where(PlayerSeason.season == resolved)
        .where(PlayerSeason.season_type == wanted_type)
    ).all()
    roster_rows.sort(key=lambda pair: (pair[0].min_pg or 0.0, pair[0].pts or 0.0), reverse=True)

    roster = [
        TeamRosterEntry(
            **player_ref(player, team).model_dump(),
            values=player_season_values(player_season, roster_keys, resolved),
        )
        for player_season, player in roster_rows
    ]

    record = (
        TeamRecord(wins=team_season.wins, losses=team_season.losses)
        if team_season is not None
        else None
    )
    return TeamDetail(
        team=team_ref(team),
        season=resolved,
        season_type=wanted_type,
        record=record,
        values=team_season_values(team_season, team_keys, resolved),
        roster=roster,
    )
