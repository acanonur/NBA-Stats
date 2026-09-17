"""``fantasy_draft_board`` — who to take next, and what taking them gives you.

Built from the user's ``Fantasy NBA 2026-27 Toolkit``. The valuation is
:mod:`nbastats.fantasy`; this module's whole job is turning a season's ``player_season`` rows
into :class:`~nbastats.fantasy.SeasonLine` inputs and the result into a payload.

**It reads season aggregates rather than projecting each player.** A board ranks 150 or more
players at once, and running the next-game projection per player costs about five queries and
four milliseconds each — over two thousand queries for a full board, and it cannot produce FG%
or FT% at all because the engine projects no shot attempts. The season line has every column
the nine categories need and costs a handful of queries flat. Rank agreement between the two
approaches is Spearman 0.99 with 145 of the top 150 shared, which is the right trade for a
ranking.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from .. import catalog, fantasy as F
from ..models import PlayerSeason
from . import queries as q
from .base import (
    ResolveContext,
    WidgetError,
    availability_note,
    resolve_season,
    resolve_subject_token,
)

__all__ = ["resolve", "SCORINGS", "DEFAULT_LIMIT", "season_lines"]

SCORINGS = ("categories", "espn_points", "yahoo_points")
DEFAULT_LIMIT = 30

#: Below this many games a season line is noise rather than evidence, and a draft board full of
#: four-game cameos at the top is the classic way a value model embarrasses itself.
MIN_GAMES = 10


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``fantasy_draft_board``."""
    notes: list[str] = []
    season = resolve_season(ctx, config.get("season"))
    season_type = str(config.get("seasonType") or "Regular Season")

    availability, era_note = availability_note("fg3m", season, "season")
    if era_note:
        notes.append(era_note)
    if availability == "unavailable":
        return _empty(season, season_type, notes), availability, notes

    lines = season_lines(ctx, season, season_type)
    if not lines:
        notes.append("No season lines are loaded for this season, so there is nothing to rank.")
        return _empty(season, season_type, notes), "unavailable", notes

    punts = [c for c in (config.get("puntCategories") or []) if c in F.CATEGORIES]
    weights = {c: (0.0 if c in punts else 1.0) for c in F.CATEGORIES}
    scoring = config.get("scoring") if config.get("scoring") in SCORINGS else "categories"
    teams = max(int(config.get("teams") or 12), 1)
    roster_spots = max(int(config.get("rosterSpots") or 13), 1)
    pool_size = max(int(config.get("poolSize") or F.DEFAULT_POOL_SIZE), 1)
    limit = max(int(config.get("limit") or DEFAULT_LIMIT), 1)

    pool = F.value_pool(
        lines,
        pool_size=pool_size,
        replacement_depth=F.DEFAULT_REPLACEMENT_DEPTH,
        season=season,
        season_type=season_type,
    )
    drafted = {pid: "drafted" for pid in _player_ids(config.get("draftedPlayerIds"), ctx)}
    mine = {pid: "My Team" for pid in _player_ids(config.get("myPlayerIds"), ctx)}
    drafted.update(mine)

    board = F.build_draft_board(
        pool,
        weights=weights,
        drafted=drafted,
        my_team="My Team" if mine else None,
        teams=teams,
        roster_spots=roster_spots,
        limit=limit,
    )
    notes.extend(board.notes)
    if punts:
        notes.append(
            "Punting " + ", ".join(F._label(c) for c in punts)
            + ". Punted categories are weighted 0 in every total; the per-category numbers are "
            "unchanged, so the board stays comparable with a manager who punts nothing."
        )

    refs = q.player_ref_dicts(ctx, [p.valuation.player_id for p in board.picks])
    payload = _payload(board, pool, refs, season, season_type, scoring, punts)
    return payload, "estimated", notes


def _player_ids(raw: Any, ctx: ResolveContext) -> list[int]:
    """Ids from a player list, resolving ``$`` tokens and skipping anything unresolvable."""
    out: list[int] = []
    for value in raw or []:
        try:
            out.append(resolve_subject_token(value, "player", ctx, field="playerIds"))
        except (WidgetError, TypeError, ValueError):
            continue
    return out


def season_lines(
    ctx: ResolveContext, season: str, season_type: str
) -> list[F.SeasonLine]:
    """Every qualifying player's per-game line for one season, in one memoised query."""
    index = q.player_season_index(ctx, season, season_type)
    out: list[F.SeasonLine] = []
    for player_id, row in index.items():
        if (row.gp or 0) < MIN_GAMES:
            continue
        out.append(
            F.SeasonLine(
                player_id=int(player_id),
                games_played=int(row.gp or 0),
                minutes_per_game=float(row.min_pg or 0.0),
                pts=float(row.pts or 0.0),
                fg3m=float(row.fg3m or 0.0),
                reb=float(row.reb or 0.0),
                ast=float(row.ast or 0.0),
                stl=float(row.stl or 0.0),
                blk=float(row.blk or 0.0),
                tov=float(row.tov or 0.0),
                fgm=float(row.fgm or 0.0),
                fga=float(row.fga or 0.0),
                ftm=float(row.ftm or 0.0),
                fta=float(row.fta or 0.0),
            )
        )
    return out


def _payload(
    board: F.DraftBoard,
    pool: F.ValuedPool,
    refs: dict[int, dict[str, Any]],
    season: str,
    season_type: str,
    scoring: str,
    punts: Sequence[str],
) -> dict[str, Any]:
    return {
        "season": season,
        "seasonType": season_type,
        "scoring": scoring,
        "categories": list(F.CATEGORIES),
        "puntCategories": list(punts),
        "teams": board.teams,
        "rosterSpots": board.roster_spots,
        "nextPick": {
            "overall": board.next_overall,
            "round": board.next_round,
            "pickInRound": board.next_pick_in_round,
        },
        "poolSize": pool.pool_size,
        "replacementValue": round(board.replacement_value, 3),
        "weakestCategories": list(board.weakest),
        "rosterStrength": {c: round(v, 3) for c, v in board.roster_strength.items()},
        "picks": [_pick(pick, refs, scoring) for pick in board.picks],
        "note": (
            "Values are z-scores against the top "
            f"{pool.pool_size} players of {season}; a projection is not involved. "
            "FG% and FT% are volume-weighted, so a high percentage on few attempts is worth "
            "little."
        ),
    }


def _pick(pick: F.DraftPick, refs: dict[int, dict[str, Any]], scoring: str) -> dict[str, Any]:
    valuation = pick.valuation
    return {
        "player": refs.get(valuation.player_id),
        "overall": pick.overall_rank,
        "round": pick.round_number,
        "pickInRound": pick.pick_in_round,
        "baselineRank": valuation.baseline_rank,
        "totalZ": round(valuation.total_z(), 3),
        "score": round(valuation.score(), 3),
        "valueOverReplacement": round(pick.value_over_replacement, 3),
        "suggestion": round(pick.suggestion, 3),
        "espnPoints": round(valuation.espn_points, 2),
        "yahooPoints": round(valuation.yahoo_points, 2),
        "gamesPlayed": valuation.line.games_played,
        "minutesPerGame": round(valuation.line.minutes_per_game, 1),
        "categories": [_category(valuation, c) for c in F.CATEGORIES],
        "fills": list(pick.fills),
        "reason": pick.reason,
        "availability": "estimated",
    }


def _category(valuation: F.PlayerValuation, category: str) -> dict[str, Any]:
    value = valuation.categories[category]
    out: dict[str, Any] = {
        "category": category,
        "value": round(value.value, 4),
        "z": round(value.z, 3),
    }
    if value.is_percentage:
        out["attempts"] = round(value.attempts or 0.0, 2)
        out["impact"] = round(value.standardised, 4)
        out["shrinkageWeight"] = round(value.shrinkage_weight or 0.0, 3)
    return out


def _empty(season: str, season_type: str, notes: list[str]) -> dict[str, Any]:
    return {
        "season": season,
        "seasonType": season_type,
        "scoring": "categories",
        "categories": list(F.CATEGORIES),
        "puntCategories": [],
        "teams": 0,
        "rosterSpots": 0,
        "nextPick": {"overall": 1, "round": 1, "pickInRound": 1},
        "poolSize": 0,
        "replacementValue": 0.0,
        "weakestCategories": [],
        "rosterStrength": {},
        "picks": [],
        "note": None,
    }
