"""``GET /v1/teams/{teamId}/defense-by-position`` and ``GET /v1/defense-by-position``.

Points a team allows, by the position the opposing scorers are *listed at*: guard, forward, center,
and an ``unknown`` bucket for players nobody listed. It is not who guarded whom, and every payload
says so in its ``caveat``. The buckets add up to the headline points allowed per game, the payload
prints that identity, and a game whose positions do not reconcile to the final score is left out
and counted. The builders and the reasons are in :mod:`nbastats.nba_matchup.defense` and
:mod:`nbastats.shared.defense_position`.

Query: ``season`` (default ``latest``), ``seasonType`` (default the regular season), ``window``
(``0``, the default, is the whole season; ``N`` is each team's last ``N`` reconciled games) and
``basis`` (``perGame``, the default, or ``perMinute``, which controls for how opponents
distributed their minutes). There is no ``scheme`` parameter: the NBA publishes three positions
and a five-way split would be a presentation its data cannot back up. A table is sorted by points
allowed, a recorded fact; no payload carries a rank.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query

from .. import nba_matchup
from ..nba_matchup.queries import get_now
from .deps import SessionDep

__all__ = ["router"]

router = APIRouter(tags=["defense"])

NowDep = Annotated[datetime, Depends(get_now)]
TeamPath = Annotated[str, Path(alias="teamId")]
Season = Annotated[str | None, Query(description="A season such as 2025-26, or latest.")]
SeasonType = Annotated[str | None, Query(alias="seasonType")]
Window = Annotated[int | None, Query(description="0 for the season, or the last N games.")]
Basis = Annotated[str | None, Query(description="perGame (default) or perMinute.")]


@router.get("/teams/{teamId}/defense-by-position", summary="Points a team allows, by position")
def team_defense(
    session: SessionDep,
    now: NowDep,
    team_id: TeamPath,
    season: Season = None,
    season_type: SeasonType = None,
    window: Window = 0,
    basis: Basis = None,
) -> dict[str, Any]:
    return nba_matchup.defense_by_position(
        session,
        team=team_id,
        season=season,
        season_type=season_type,
        window=window,
        basis=basis,
        now=now,
    )


@router.get("/defense-by-position", summary="Every team's defence by position")
def league_defense(
    session: SessionDep,
    now: NowDep,
    season: Season = None,
    season_type: SeasonType = None,
    window: Window = 0,
    basis: Basis = None,
) -> dict[str, Any]:
    return nba_matchup.defense_by_position(
        session,
        team=None,
        season=season,
        season_type=season_type,
        window=window,
        basis=basis,
        now=now,
    )
