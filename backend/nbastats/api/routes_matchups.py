"""``GET /v1/matchups``, ``/v1/teams/{teamId}/matchup`` and ``/v1/games/{gameId}/matchup``.

``TeamMatchup`` (design section 9.4): two teams side by side, with each side's latest game and
score, points scored and allowed per game, last-N form with scores, home and away splits, the
opponent-adjusted values, who is missing, where its defence gives up points, and the projection of
the game between them when one is scheduled. The builders are
:mod:`nbastats.nba_matchup.matchup`; these routes only parse the request, take the session and the
clock, and hand them over, so the dashboard's widget and the REST payload are one object.

Three ways in
-------------
``/matchups?homeTeamId&awayTeamId``
    Two teams by id. ``game`` and ``projection`` are present only when the two are scheduled to
    meet with those sides. ``season`` (default ``latest``), ``seasonType`` (default the regular
    season) and ``window`` (3 to 15, default 5) scope the statistics.
``/teams/{teamId}/matchup``
    The team's next scheduled game, or ``404 game_not_found``.
``/games/{gameId}/matchup``
    One game, with every input cut off before it started.

Team ids are the NBA's numeric ids as strings. A EuroLeague club code is simply an unknown team
here (``404 team_not_found``): the league is chosen by the URL prefix, never guessed from an id.
Every query, path and body name on these routes is on the allowed-parameter list
(:data:`nbastats.shared.market_guard.ALLOWED_PARAMETERS`), and a test walks the OpenAPI document to
prove it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query

from .. import nba_matchup
from ..nba_matchup.queries import get_now
from .deps import SessionDep

__all__ = ["router"]

router = APIRouter(tags=["matchups"])

NowDep = Annotated[datetime, Depends(get_now)]
TeamPath = Annotated[str, Path(alias="teamId")]
GamePath = Annotated[str, Path(alias="gameId")]
Window = Annotated[int | None, Query(description="Games of recent form: 3 to 15 (default 5).")]


@router.get("/matchups", summary="Two teams side by side")
def matchups(
    session: SessionDep,
    now: NowDep,
    home_team_id: Annotated[str, Query(alias="homeTeamId")],
    away_team_id: Annotated[str, Query(alias="awayTeamId")],
    season: Annotated[str | None, Query()] = None,
    season_type: Annotated[str | None, Query(alias="seasonType")] = None,
    window: Window = None,
) -> dict[str, Any]:
    return nba_matchup.team_matchup(
        session,
        home=home_team_id,
        away=away_team_id,
        season=season,
        season_type=season_type,
        window=window,
        now=now,
    )


@router.get("/teams/{teamId}/matchup", summary="A team's next game")
def team_matchup(
    session: SessionDep, now: NowDep, team_id: TeamPath, window: Window = None
) -> dict[str, Any]:
    return nba_matchup.team_matchup(session, team=team_id, window=window, now=now)


@router.get("/games/{gameId}/matchup", summary="A game's matchup, inputs cut off before it started")
def game_matchup(
    session: SessionDep, now: NowDep, game_id: GamePath, window: Window = None
) -> dict[str, Any]:
    return nba_matchup.team_matchup(session, game_id=game_id, window=window, now=now)
