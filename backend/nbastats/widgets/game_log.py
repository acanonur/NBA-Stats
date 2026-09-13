"""``game_log`` — a player's recent games as a table, newest first.

One rule shapes this module: **``seasonBests`` is computed across the whole season, not across
the page.** A tile showing the last ten games would otherwise highlight the best of those ten
as a season best, and the reader would be told a 22-point night was a career high because the
40-point one was eleven games ago. So the season is loaded once (it is memoised, and the trend
chart on the same dashboard reuses it), the bests come from all of it, and only then is the
page sliced off the front.

A best respects the metric's direction: the season best for ``tov`` is the fewest turnovers,
not the most.

Payload: ``contracts/CONTRACT.md`` §4 ``game_log``.
"""
from __future__ import annotations

from typing import Any, Sequence

from .. import catalog
from . import queries as q
from .base import (
    ResolveContext,
    attach_availability,
    combined_row_availability,
    resolve_season,
    resolve_subject_token,
)

__all__ = ["resolve", "season_bests", "FALLBACK_COLUMNS", "DEFAULT_LIMIT"]

DEFAULT_LIMIT = 10

#: Used only if a layout arrives with an empty column list.
FALLBACK_COLUMNS: tuple[str, ...] = (
    "min",
    "pts",
    "reb",
    "ast",
    "ts_pct",
    "usg_pct",
    "plus_minus",
    "game_score",
)


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``game_log``."""
    notes: list[str] = []
    player_id = resolve_subject_token(config.get("playerId"), "player", ctx, field="playerId")
    ctx.note_player(player_id)

    season = resolve_season(ctx, config.get("season"))
    season_type = config.get("seasonType") or "Regular Season"
    columns = [key for key in (config.get("columns") or FALLBACK_COLUMNS)]
    limit = max(1, int(config.get("limit") or DEFAULT_LIMIT))
    highlight = config.get("highlightSeasonBest", True)

    lines = q.player_games(ctx, player_id, season, season_type)
    if not lines:
        notes.append(f"No {season} {season_type} games are loaded for this player.")

    bests = season_bests(lines, columns) if highlight else {}
    page = lines[:limit]

    rows: list[dict[str, Any]] = []
    team_index = q.all_teams(ctx)
    for line in page:
        opponent = team_index.get(line.opponent_id)
        values = line.values(columns)
        rows.append(
            {
                "gameId": line.game.game_id,
                "date": line.game.game_date.isoformat(),
                "opponentAbbr": opponent.abbr if opponent is not None else None,
                "isHome": line.is_home,
                "result": line.result,
                "score": line.score,
                "started": line.basic.started,
                "minutes": line.basic.minutes,
                "values": values,
                "availability": combined_row_availability(columns, line.game.season, "game"),
            }
        )

    availability = attach_availability(notes, columns, season, "game")
    payload: dict[str, Any] = {
        "player": q.player_ref_dict(ctx, player_id, season),
        "season": season,
        "seasonType": season_type,
        "columns": [q.metric_descriptor_dict(key) for key in columns],
        "seasonBests": bests,
        "rows": rows,
    }
    return payload, availability, notes


def season_bests(
    lines: Sequence[q.GameLine], columns: Sequence[str]
) -> dict[str, float | int]:
    """The best value of each column across **every** game of the season.

    Direction comes from the catalog, so ``tov`` and ``def_rtg`` report their minimum. A
    column with no value all season is absent rather than present as ``null``: the map exists
    only to highlight cells, and there is nothing to highlight. Values keep the type they had
    in the row — a 38-point night is ``38``, not ``38.0``, exactly as the contract shows.
    """
    if not lines or not columns:
        return {}
    directions = {
        key: bool(catalog.metric(key).get("higherIsBetter", True)) for key in columns
    }
    best: dict[str, float | int] = {}
    for line in lines:
        for key, value in line.values(columns).items():
            if value is None:
                continue
            current = best.get(key)
            if current is None:
                best[key] = value
                continue
            if (value > current) if directions.get(key, True) else (value < current):
                best[key] = value
    return best
