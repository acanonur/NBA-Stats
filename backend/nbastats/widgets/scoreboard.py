"""``scoreboard`` — a whole slate, with the best line from each side.

``date: "latest"`` means *the most recent day with at least one final game*, which is not the
same as yesterday and not the same as today: an All-Star break or a postponed Tuesday must
not produce an empty tile captioned "Last Night".

Each game carries up to two ``topPerformers``, one per team, ranked by game score where the
era recorded it and by points where it did not — 1962 has no game score, and picking the best
line from a night nobody measured that way would be an invention.

The game objects are ``GameRef`` **flattened**, with ``topPerformers`` alongside: that is what
``contracts/CONTRACT.md`` §4 shows and what the client's ``ScoreboardGame`` decodes.

Payload: ``contracts/CONTRACT.md`` §4 ``scoreboard``.
"""
from __future__ import annotations

from datetime import date as date_type
from typing import Any, Optional, Sequence

from .. import catalog
from . import queries as q
from .base import ResolveContext, latest_final_date, resolve_date, resolve_subject_token, stat_line

__all__ = ["resolve", "PERFORMER_METRICS", "LINE_METRICS", "PERFORMERS_PER_GAME"]

#: Ranking metrics for a top performer, best first. The first one the era recorded wins.
PERFORMER_METRICS: tuple[str, ...] = ("game_score", "pts")

#: What the one-line summary under a performer's name is built from.
LINE_METRICS: tuple[str, ...] = ("pts", "reb", "ast", "stl", "blk")

#: One from each side.
PERFORMERS_PER_GAME = 2


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``scoreboard``."""
    notes: list[str] = []
    day = resolve_date(ctx, config.get("date"), field="date")
    show_performers = config.get("showTopPerformers", True)
    team_ids = _team_ids(config, ctx)

    if day is None:
        day = ctx.as_of or date_type.today()
        notes.append("No completed game is loaded, so there is no slate to show.")
        return _empty(day), "unavailable", notes

    games = q.slate_games(ctx, day)
    if team_ids:
        games = [
            game
            for game in games
            if game.home_team_id in team_ids or game.away_team_id in team_ids
        ]
    if not games:
        notes.append(f"No game on {day.isoformat()} matches this widget's filters.")

    performers = (
        _performers_by_game(ctx, [game.game_id for game in games]) if show_performers else {}
    )

    entries: list[dict[str, Any]] = []
    for game in games:
        entry = q.game_ref_dict(ctx, game)
        entry["topPerformers"] = performers.get(game.game_id, [])
        entries.append(entry)

    latest = latest_final_date(ctx)
    payload: dict[str, Any] = {
        "date": day.isoformat(),
        "isLatestCompleted": latest is not None and day == latest,
        "allFinal": bool(games) and all(game.status == "final" for game in games),
        "games": entries,
    }
    return payload, "full" if games else "partial", notes


def _empty(day: date_type) -> dict[str, Any]:
    """The shape a slate-less night still has to have, so the tile draws a caption."""
    return {
        "date": day.isoformat(),
        "isLatestCompleted": False,
        "allFinal": False,
        "games": [],
    }


def _team_ids(config: dict[str, Any], ctx: ResolveContext) -> set[int]:
    """The ``teamIds`` filter, which may carry ``$favorite_team`` rather than an id."""
    raw = config.get("teamIds") or []
    if not isinstance(raw, (list, tuple)):
        return set()
    return {
        resolve_subject_token(item, "team", ctx, field="teamIds") for item in raw
    }


def _performers_by_game(
    ctx: ResolveContext, game_ids: Sequence[str]
) -> dict[str, list[dict[str, Any]]]:
    """The best line from each team in each game, in two queries for the whole slate."""
    if not game_ids:
        return {}
    lines = q.slate_player_lines(ctx, game_ids)
    if not lines:
        return {}

    q.players(ctx, [line.basic.player_id for line in lines])
    team_index = q.all_teams(ctx)

    # {game_id: {team_id: (score, line)}} — the best line each side produced.
    best: dict[str, dict[int, tuple[float, Any, str]]] = {}
    for line in lines:
        metric_key = _ranking_metric(line.game.season)
        if metric_key is None:
            continue
        value = line.values([metric_key])[metric_key]
        if value is None:
            continue
        slot = best.setdefault(line.game.game_id, {})
        current = slot.get(line.basic.team_id)
        if current is None or float(value) > current[0]:
            slot[line.basic.team_id] = (float(value), line, metric_key)

    out: dict[str, list[dict[str, Any]]] = {}
    for game_id, by_team in best.items():
        ranked = sorted(by_team.values(), key=lambda entry: entry[0], reverse=True)
        performers: list[dict[str, Any]] = []
        for value, line, metric_key in ranked[:PERFORMERS_PER_GAME]:
            player_row = q.player(ctx, line.basic.player_id)
            if player_row is None:
                continue
            team_row = team_index.get(line.basic.team_id)
            performers.append(
                {
                    "player": q.player_ref_dict_for_team(
                        ctx, line.basic.player_id, line.basic.team_id
                    ),
                    "teamAbbr": team_row.abbr if team_row is not None else None,
                    "line": stat_line(line.values(LINE_METRICS)),
                    "value": q.metric_value_dict(
                        metric_key, value, season=line.game.season, granularity="game"
                    ),
                }
            )
        out[game_id] = performers
    return out


def _ranking_metric(season: str) -> Optional[str]:
    """The first ranking metric the season actually recorded per game."""
    for key in PERFORMER_METRICS:
        if catalog.metric_availability(key, season, "game") != "unavailable":
            return key
    return None
