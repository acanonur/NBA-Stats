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

from typing import Any, Mapping, Optional, Sequence

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

#: The board is a **table**, and its columns ship as data so the client renders whatever the
#: server sends rather than hardcoding a column set that then drifts.
#:
#: The order is the one a FanScout export uses, because that is the layout a reader is most
#: likely to be holding beside the app: rank and player, then a value, then the identity block,
#: then the raw per-game line, then the nine z-scores. Two departures from that export are
#: deliberate and are documented in docs/FANTASY.md:
#:
#: * **No Contract column.** Nothing in this project knows a player's contract status — it is
#:   not in the box score, not in the identity snapshot and not on any NBA.com endpoint the
#:   ingest touches. An export has it because a subscription supplied it.
#: * **``score`` sits where a FanScout "Value" would.** That number is proprietary and provably
#:   not the mean of the nine z-scores printed beside it, so reproducing the header with a
#:   different number underneath would be the worst of both. ``score`` is the weighted mean this
#:   engine computes and docs/FANTASY.md derives.
#:
#: The z block keeps the export's ordering (zPTS zTPM zAST zREB…), which differs from the raw
#: block's (PTS TPM REB AST…). That is a quirk of the export rather than a mistake of ours, and
#: reproducing it is what lets a reader diff the two column by column.
_COLUMNS: tuple[dict[str, Any], ...] = (
    {"key": "score", "label": "Value", "format": "decimal2", "group": "summary",
     "signed": True, "higherIsBetter": True},
    {"key": "team", "label": "Team", "format": None, "group": "summary",
     "align": "leading", "higherIsBetter": None},
    {"key": "gp", "label": "GP", "format": "integer", "group": "summary",
     "higherIsBetter": True},
    # The export calls this "Total Minutes" and it is nothing of the kind — the values run 5 to
    # 37 against season game counts, i.e. minutes per game. Labelled for what it is.
    {"key": "mpg", "label": "MPG", "format": "decimal1", "group": "summary",
     "higherIsBetter": True},
    {"key": "pts", "label": "PTS", "format": "decimal1", "group": "production",
     "higherIsBetter": True},
    {"key": "fg3m", "label": "TPM", "format": "decimal1", "group": "production",
     "higherIsBetter": True},
    {"key": "reb", "label": "REB", "format": "decimal1", "group": "production",
     "higherIsBetter": True},
    {"key": "ast", "label": "AST", "format": "decimal1", "group": "production",
     "higherIsBetter": True},
    {"key": "stl", "label": "STL", "format": "decimal1", "group": "production",
     "higherIsBetter": True},
    {"key": "blk", "label": "BLK", "format": "decimal1", "group": "production",
     "higherIsBetter": True},
    {"key": "tov", "label": "TOV", "format": "decimal1", "group": "production",
     "higherIsBetter": False},
    {"key": "fg_pct", "label": "FG%", "format": "percent1", "group": "production",
     "higherIsBetter": True},
    {"key": "fga", "label": "FGA", "format": "decimal1", "group": "production",
     "higherIsBetter": None},
    {"key": "ft_pct", "label": "FT%", "format": "percent1", "group": "production",
     "higherIsBetter": True},
    {"key": "fta", "label": "FTA", "format": "decimal1", "group": "production",
     "higherIsBetter": None},
    {"key": "z_pts", "label": "zPTS", "format": "decimal2", "group": "impact",
     "signed": True, "higherIsBetter": True},
    {"key": "z_fg3m", "label": "zTPM", "format": "decimal2", "group": "impact",
     "signed": True, "higherIsBetter": True},
    {"key": "z_ast", "label": "zAST", "format": "decimal2", "group": "impact",
     "signed": True, "higherIsBetter": True},
    {"key": "z_reb", "label": "zREB", "format": "decimal2", "group": "impact",
     "signed": True, "higherIsBetter": True},
    {"key": "z_stl", "label": "zSTL", "format": "decimal2", "group": "impact",
     "signed": True, "higherIsBetter": True},
    {"key": "z_blk", "label": "zBLK", "format": "decimal2", "group": "impact",
     "signed": True, "higherIsBetter": True},
    # Already sign-flipped by the valuation, so a high zTOV is FEW turnovers and "higher is
    # better" is true of the z even though it is false of the statistic it came from.
    {"key": "z_tov", "label": "zTOV", "format": "decimal2", "group": "impact",
     "signed": True, "higherIsBetter": True},
    {"key": "z_fg_pct", "label": "zFG%", "format": "decimal2", "group": "impact",
     "signed": True, "higherIsBetter": True},
    {"key": "z_ft_pct", "label": "zFT%", "format": "decimal2", "group": "impact",
     "signed": True, "higherIsBetter": True},
)

#: ``category key -> the z column that reports it``.
_Z_COLUMN = {category: f"z_{category}" for category in F.CATEGORIES}

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
    payload = _payload(board, pool, refs, season, season_type, scoring, punts, weights)
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
    weights: Mapping[str, float],
) -> dict[str, Any]:
    punted = set(punts)
    columns = [{**column, "punted": _is_punted(column["key"], punted)} for column in _COLUMNS]
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
        "columns": columns,
        "rows": [_row(pick, refs, weights) for pick in board.picks],
        "note": (
            "Values are z-scores against the top "
            f"{pool.pool_size} players of {season}. FG% and FT% are volume-weighted, so a high "
            "percentage on few attempts is worth little, and zTOV is sign-flipped, so a high "
            "number there means few turnovers."
        ),
    }


def _is_punted(key: str, punted: set[str]) -> bool:
    """True for a z column whose category is punted. Raw production is never punted.

    A punt zeroes a category's *weight*, not the player's production: the reader still wants to
    see that a centre gets nine rebounds, they just do not want it counted. Greying the raw
    column too would hide a fact rather than a judgement.
    """
    if not key.startswith("z_"):
        return False
    return key[2:] in punted


def _row(
    pick: F.DraftPick, refs: dict[int, dict[str, Any]], weights: Mapping[str, float]
) -> dict[str, Any]:
    """One table row: the pinned identity, then every column's value by key.

    ``values`` is a dict rather than a parallel array so a column the client does not know
    about is skipped rather than shifting every cell after it by one.

    ``weights`` has to be threaded all the way down here. The board sorts on a weighted
    suggestion, so a Value column computed with default weights is not monotonic with the rank
    beside it the moment anything is punted — a table that says it is sorted and visibly is not.
    """
    valuation = pick.valuation
    line = valuation.line
    player = refs.get(valuation.player_id) or {}
    values: dict[str, Any] = {
        "score": round(valuation.score(weights), 3),
        "team": player.get("teamAbbr"),
        "gp": line.games_played,
        "mpg": round(line.minutes_per_game, 1),
    }
    for category in F.CATEGORIES:
        value = valuation.categories[category]
        if category not in F.PERCENTAGE_CATEGORIES:
            values[category] = round(value.value, 2)
        else:
            values[category] = round(value.value, 4)
            makes, attempts = F.PERCENTAGE_PARTS[category]
            values[attempts] = round(getattr(line, attempts, 0.0), 1)
        values[_Z_COLUMN[category]] = round(value.z, 3)
    return {
        "rank": pick.overall_rank,
        "round": pick.round_number,
        "pickInRound": pick.pick_in_round,
        "player": refs.get(valuation.player_id),
        "baselineRank": valuation.baseline_rank,
        "totalZ": round(valuation.total_z(weights), 3),
        "valueOverReplacement": round(pick.value_over_replacement, 3),
        "suggestion": round(pick.suggestion, 3),
        "espnPoints": round(valuation.espn_points, 2),
        "yahooPoints": round(valuation.yahoo_points, 2),
        "values": values,
        "fills": list(pick.fills),
        "reason": pick.reason,
        # A valuation is never a record — the same rule the projection widgets follow.
        "availability": "estimated",
    }


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
        "columns": [{**column, "punted": False} for column in _COLUMNS],
        "rows": [],
        "note": None,
    }
