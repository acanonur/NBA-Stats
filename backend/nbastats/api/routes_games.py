"""``GET /v1/games`` and ``GET /v1/games/{gameId}/box``.

``/v1/games`` answers two different questions with one shape: *what happened on this day*
(``date``, ISO or the literal ``"latest"``) and *what games does this season hold*
(``season`` + ``seasonType`` + ``teamId``, cursor paginated). ``"latest"`` means the most
recent date with at least one **final** game, so an empty evening or a slate that has not
tipped off yet never shows up as "last night".

The box score serves basic, advanced or both. Before 1996-97 there is no advanced box at
all — those keys come back ``null`` with the row marked ``partial``, never zero.
"""
from __future__ import annotations

from datetime import date as date_type
from typing import Optional, Sequence

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Game, Player, PlayerGameAdvanced, PlayerGameBasic, TeamGame
from . import errors
from .deps import (
    SessionDep,
    clamp_limit,
    decode_cursor,
    encode_cursor,
    parse_iso_date,
    parse_season_type,
    resolve_season,
)
from .schemas import BoxScorePlayer, BoxScoreResponse, BoxScoreTeam, ScoreboardResponse
from .serializers import (
    combined_availability,
    game_ref,
    load_teams,
    player_game_values,
    player_ref,
    team_game_values,
    team_ref,
)

__all__ = [
    "router",
    "BASIC_PLAYER_METRICS",
    "ADVANCED_PLAYER_METRICS",
    "BASIC_TEAM_METRICS",
    "ADVANCED_TEAM_METRICS",
    "latest_final_date",
]

router = APIRouter()

BASIC_PLAYER_METRICS: tuple[str, ...] = (
    "min",
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

ADVANCED_PLAYER_METRICS: tuple[str, ...] = (
    "ts_pct",
    "efg_pct",
    "usg_pct",
    "off_rtg",
    "def_rtg",
    "net_rtg",
    "ast_pct",
    "reb_pct",
    "tov_pct",
    "pie",
    "game_score",
)

BASIC_TEAM_METRICS: tuple[str, ...] = (
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
    "fg_pct",
    "fg3_pct",
    "ft_pct",
)

ADVANCED_TEAM_METRICS: tuple[str, ...] = (
    "off_rtg",
    "def_rtg",
    "net_rtg",
    "pace",
    "poss",
    "efg_pct",
    "ts_pct",
    "tov_pct",
    "oreb_pct",
    "ftr",
    "opp_efg_pct",
    "opp_tov_pct",
    "opp_oreb_pct",
    "opp_ftr",
)

_VIEWS = ("basic", "advanced", "both")


def latest_final_date(session: Session) -> Optional[date_type]:
    """The most recent scheduling day with at least one final game."""
    return session.execute(
        select(func.max(Game.game_date)).where(Game.status == "final")
    ).scalar_one_or_none()


def _metrics_for_view(view: str, basic: Sequence[str], advanced: Sequence[str]) -> list[str]:
    if view == "basic":
        return list(basic)
    if view == "advanced":
        return list(advanced)
    return [*basic, *advanced]


@router.get("/games", response_model=ScoreboardResponse, summary="Games by day or by season")
def list_games(
    session: SessionDep,
    date: Optional[str] = Query(None, description="An ISO date, or 'latest'."),
    season: Optional[str] = Query(None),
    season_type: Optional[str] = Query(None, alias="seasonType"),
    team_id: Optional[int] = Query(None, alias="teamId"),
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None),
) -> ScoreboardResponse:
    """One day's slate, or a season's games newest-first.

    With no parameters at all this behaves as ``date=latest``, which is what the scoreboard
    widget wants on a cold start.
    """
    page_size = clamp_limit(limit, 50, 200)
    by_day = date is not None or (season is None and team_id is None)
    latest_completed = latest_final_date(session)

    statement = select(Game)
    target_day: Optional[date_type] = None

    if by_day:
        raw = (date or "latest").strip()
        target_day = latest_completed if raw.lower() == "latest" else parse_iso_date(raw)
        if target_day is None:
            # Nothing final has ever been ingested: an honest empty slate, not a 404.
            return ScoreboardResponse(
                date=None, is_latest_completed=False, games=[], next_cursor=None
            )
        statement = statement.where(Game.game_date == target_day)
    if season is not None:
        statement = statement.where(Game.season == str(resolve_season(session, season)))
    if season_type is not None:
        statement = statement.where(Game.season_type == parse_season_type(season_type))
    if team_id is not None:
        statement = statement.where(
            (Game.home_team_id == team_id) | (Game.away_team_id == team_id)
        )

    keyset = decode_cursor(cursor)
    if keyset is not None:
        after_date = keyset.get("d")
        after_game = keyset.get("g")
        if not isinstance(after_date, str) or not isinstance(after_game, str):
            raise errors.bad_request("The cursor is not valid.", "cursor")
        boundary = parse_iso_date(after_date, field="cursor")
        statement = statement.where(
            (Game.game_date < boundary)
            | ((Game.game_date == boundary) & (Game.game_id < after_game))
        )

    statement = statement.order_by(Game.game_date.desc(), Game.game_id.desc()).limit(
        page_size + 1
    )
    games = session.execute(statement).scalars().all()
    has_more = len(games) > page_size
    games = games[:page_size]

    teams = load_teams(
        session,
        {game.home_team_id for game in games} | {game.away_team_id for game in games},
    )
    next_cursor = (
        encode_cursor({"d": games[-1].game_date.isoformat(), "g": games[-1].game_id})
        if has_more and games
        else None
    )
    return ScoreboardResponse(
        date=target_day,
        is_latest_completed=bool(target_day is not None and target_day == latest_completed),
        games=[game_ref(game, teams=teams) for game in games],
        next_cursor=next_cursor,
    )


@router.get(
    "/games/{game_id}/box", response_model=BoxScoreResponse, summary="One game's box score"
)
def box_score(
    session: SessionDep,
    game_id: str,
    view: str = Query("both", description="basic | advanced | both"),
) -> BoxScoreResponse:
    """The traditional box, the advanced box, or both, for one game."""
    if view not in _VIEWS:
        raise errors.bad_request(f"{view!r} is not one of {list(_VIEWS)}.", "view")

    game = session.get(Game, game_id)
    if game is None:
        raise errors.game_not_found(game_id)

    teams = load_teams(session, {game.home_team_id, game.away_team_id})
    player_keys = _metrics_for_view(view, BASIC_PLAYER_METRICS, ADVANCED_PLAYER_METRICS)
    team_keys = _metrics_for_view(view, BASIC_TEAM_METRICS, ADVANCED_TEAM_METRICS)

    team_rows = {
        row.team_id: row
        for row in session.execute(select(TeamGame).where(TeamGame.game_id == game_id))
        .scalars()
        .all()
    }
    basics = session.execute(
        select(PlayerGameBasic, Player)
        .join(Player, Player.player_id == PlayerGameBasic.player_id)
        .where(PlayerGameBasic.game_id == game_id)
    ).all()
    advanced = {
        row.player_id: row
        for row in session.execute(
            select(PlayerGameAdvanced).where(PlayerGameAdvanced.game_id == game_id)
        )
        .scalars()
        .all()
    }

    availability = combined_availability(player_keys, game.season, "game")
    lines: dict[int, list[BoxScorePlayer]] = {}
    for basic, player in basics:
        lines.setdefault(basic.team_id, []).append(
            BoxScorePlayer(
                player=player_ref(player, teams.get(basic.team_id)),
                started=basic.started,
                minutes=basic.minutes,
                values=player_game_values(
                    basic, advanced.get(basic.player_id), player_keys, game.season
                ),
                availability=availability,
            )
        )
    for entries in lines.values():
        entries.sort(
            key=lambda entry: (bool(entry.started), entry.minutes or 0.0), reverse=True
        )

    sides: list[BoxScoreTeam] = []
    for team_id in (game.home_team_id, game.away_team_id):
        team = teams.get(team_id)
        if team is None:  # pragma: no cover - a game always names two real franchises
            continue
        sides.append(
            BoxScoreTeam(
                team=team_ref(team),
                values=team_game_values(team_rows.get(team_id), team_keys, game.season),
                players=lines.get(team_id, []),
            )
        )

    return BoxScoreResponse(game=game_ref(game, teams=teams), teams=sides)
