"""``GET /v1/fantasy/night`` — per-game fantasy points for one night's slate.

Why this is a REST route and not a 17th widget kind
--------------------------------------------------------
``contracts/widgets.json`` names exactly 16 widget kinds, and three separate things police that
number: contract check (d) (``nbastats.widgets.RESOLVERS`` must match the catalog exactly), the
matching SwiftUI view every widget kind needs on iOS, and the "16 widget kinds" claim written
into ``contracts/CONTRACT.md`` and this project's tests. None of that changes here. This route
answers a question dashboards do not ask — "score every player who played tonight under a
scoring system of my choosing" — as its own page, which the web is allowed to have and the
dashboard surface is not.

Why ``nba`` scoring is ``"full"`` and the other two are ``"estimated"``
----------------------------------------------------------------------------
``player_game_basic.fantasy_pts`` is a value the ingest pipeline already recorded on the box
score, computed once from NBA.com's own formula — reading it back is the same act as reading
``pts`` or ``reb``. ESPN and Yahoo scoring exist nowhere in the ingested data; this module
computes them itself, on demand, from the same box score. That is a re-scoring, not a record,
and the ``availability`` badge says so.

Why a missing component drops the player rather than scoring them at all
------------------------------------------------------------------------------
:func:`nbastats.fantasy.single_game_line` returns ``None`` when any stat a scoring system
weights is ``NULL`` on the row — blocks and steals before 1973-74, three-pointers before
1979-80, individual turnovers before 1977-78. This module drops that player from the response
rather than ever calling :func:`nbastats.fantasy.fantasy_points` on a line it knows is
incomplete: that function coerces a missing attribute to ``0.0``, which is correct for a
deliberately-punted category and wrong for an unrecorded one, and the difference between those
two cases can only be known here, before the call, never after.
"""
from __future__ import annotations

from datetime import date as date_type
from typing import Any, Optional

from fastapi import APIRouter, Query
from sqlalchemy import select

from .. import catalog
from .. import fantasy as F
from ..models import Game, Player, PlayerGameBasic
from . import errors
from .deps import SessionDep, clamp_limit, parse_iso_date
from .routes_games import latest_final_date
from .serializers import load_teams, player_ref

__all__ = ["router", "SCORING_SYSTEMS", "DEFAULT_SCORING"]

router = APIRouter(tags=["fantasy"])

SCORING_SYSTEMS: tuple[str, ...] = ("nba", "espn_points", "yahoo_points")
DEFAULT_SCORING = "nba"
DEFAULT_LIMIT = 25
DEFAULT_MIN_MINUTES = 12.0

_FORMULA_LABELS: dict[str, str] = {
    "nba": (
        "NBA's own fantasy-points formula (PTS + 1.2×REB + 1.5×AST + 3×STL + "
        "3×BLK − TOV), as recorded on this game's box score."
    ),
    "espn_points": "ESPN points scoring, applied to this game's box score.",
    "yahoo_points": "Yahoo points scoring, applied to this game's box score.",
}


def _empty_response(date: Optional[date_type], scoring: str) -> dict[str, Any]:
    return {
        "date": date.isoformat() if date else None,
        "scoring": scoring,
        "formulaLabel": _FORMULA_LABELS[scoring],
        "rows": [],
    }


def _line_summary(basic: PlayerGameBasic) -> str:
    """``"32 PTS · 8 REB · 11 AST"`` — the three counting stats every reader wants first.

    All three are on the traditional box score back to 1946-47, so a ``NULL`` here means "this
    row does not record it", never "zero". It is rendered through ``catalog.format_value``, which
    is the one place that decides what an absent number looks like (an em dash), rather than
    ``int(x or 0)`` — which printed a confident ``0 PTS`` for a stat the database does not have.
    """
    parts = (("pts", "PTS"), ("reb", "REB"), ("ast", "AST"))
    return " · ".join(
        f"{catalog.format_value('integer', getattr(basic, field))} {label}"
        for field, label in parts
    )


@router.get("/fantasy/night", summary="Per-game fantasy points for one night's slate")
def fantasy_night(
    session: SessionDep,
    date: Optional[str] = Query(None, description="An ISO date, or 'latest'."),
    scoring: str = Query(DEFAULT_SCORING),
    limit: int = Query(DEFAULT_LIMIT, ge=3, le=50),
    min_minutes: float = Query(DEFAULT_MIN_MINUTES, ge=0.0, le=48.0, alias="minMinutes"),
) -> dict[str, Any]:
    """One night's slate, ranked by fantasy points under the caller's chosen scoring system."""
    if scoring not in SCORING_SYSTEMS:
        raise errors.bad_request(
            f"{scoring!r} is not one of {list(SCORING_SYSTEMS)}.", "scoring"
        )
    page_size = clamp_limit(limit, DEFAULT_LIMIT, 50)

    raw = (date or "latest").strip()
    target_day = latest_final_date(session) if raw.lower() == "latest" else parse_iso_date(raw)
    if target_day is None:
        return _empty_response(None, scoring)

    games = (
        session.execute(select(Game).where(Game.game_date == target_day)).scalars().all()
    )
    if not games:
        return _empty_response(target_day, scoring)

    games_by_id = {game.game_id: game for game in games}
    team_ids = {game.home_team_id for game in games} | {game.away_team_id for game in games}
    teams = load_teams(session, team_ids)

    rows = session.execute(
        select(PlayerGameBasic, Player)
        .join(Player, Player.player_id == PlayerGameBasic.player_id)
        .where(PlayerGameBasic.game_id.in_(list(games_by_id)))
    ).all()

    scored: list[dict[str, Any]] = []
    for basic, player in rows:
        # An unrecorded `minutes` is unknown, not zero: it cannot be shown to have cleared a
        # minutes bar, so it is excluded whenever one is set. With no bar (`minMinutes=0`) there
        # is nothing to clear and the row stays, exactly as before.
        if min_minutes > 0 and (basic.minutes is None or basic.minutes < min_minutes):
            continue

        if scoring == "nba":
            points = basic.fantasy_pts
            if points is None:
                continue
            availability = "full"
        else:
            line = F.single_game_line(basic, player)
            if line is None:
                continue
            weights = F.ESPN_POINTS if scoring == "espn_points" else F.YAHOO_POINTS
            points = F.fantasy_points(line, weights)
            availability = "estimated"

        game = games_by_id[basic.game_id]
        is_home = basic.team_id == game.home_team_id
        opponent = teams.get(game.away_team_id if is_home else game.home_team_id)

        scored.append(
            {
                "player": player_ref(player, teams.get(basic.team_id)).model_dump(
                    mode="json", by_alias=True
                ),
                "gameId": basic.game_id,
                "opponentAbbr": opponent.abbr if opponent is not None else None,
                "isHome": is_home,
                "points": round(float(points), 2),
                "displayValue": f"{float(points):.1f}",
                "minutes": basic.minutes,
                "line": _line_summary(basic),
                "availability": availability,
            }
        )

    scored.sort(key=lambda row: row["points"], reverse=True)
    ranked = [{"rank": rank, **row} for rank, row in enumerate(scored[:page_size], start=1)]

    return {
        "date": target_day.isoformat(),
        "scoring": scoring,
        "formulaLabel": _FORMULA_LABELS[scoring],
        "rows": ranked,
    }
