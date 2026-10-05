"""``fantasy_trade`` — what a trade gains you, what it costs you, and how sure that is.

Built from the ``Trade Analyzer`` sheet of the user's workbook: up to four players a side, the
change per category, and a roster-spot adjustment for the slots an uneven trade frees or fills.

Two things here are not in the spreadsheet, and both are about not overclaiming.

**The roster-spot adjustment is its own row.** Give two, get one, and the spare slot gets
refilled from waivers at below pool average — routinely a larger number than the difference
between the players. Folding it into the total makes a reasonable 2-for-1 read as a blowout with
no way to see why.

**The verdict comes with a range, not an interval.** The projection engine's negative-binomial
band is a *single-game count for one player in one category*; a trade delta is a signed sum over
eight players and nine standardised categories, and an honest variance for that needs a
covariance matrix this project does not have. So instead of a band nobody could justify, the
widget recomputes the whole trade under four named scenarios — the player you get misses time,
loses minutes, the player you give up does either — and reports the spread with the scenario
that produced each end. When those scenarios disagree about the *sign*, that is the finding.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from .. import fantasy as F
from . import queries as q
from .base import (
    ResolveContext,
    WidgetError,
    availability_note,
    resolve_season,
    resolve_subject_token,
)
from .fantasy_draft_board import season_lines

__all__ = ["resolve", "MAX_PER_SIDE"]

#: The workbook's own cap, and about the point where a per-category table stops being readable.
MAX_PER_SIDE = 4


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``fantasy_trade``."""
    notes: list[str] = []
    season = resolve_season(ctx, config.get("season"))
    season_type = str(config.get("seasonType") or "Regular Season")

    availability, era_note = availability_note("fg3m", season, "season")
    if era_note:
        notes.append(era_note)
    if availability == "unavailable":
        return _empty(season, season_type), availability, notes

    give_ids = _side_ids(config.get("givePlayerIds"), "give", ctx, notes)
    get_ids = _side_ids(config.get("getPlayerIds"), "get", ctx, notes)
    if not give_ids or not get_ids:
        notes.append("Add at least one player to each side to see what the trade does.")
        return _empty(season, season_type), "partial", notes

    lines = season_lines(ctx, season, season_type)
    if not lines:
        notes.append("No season lines are loaded for this season, so nothing can be valued.")
        return _empty(season, season_type), "unavailable", notes

    punts = [c for c in (config.get("puntCategories") or []) if c in F.CATEGORIES]
    weights = {c: (0.0 if c in punts else 1.0) for c in F.CATEGORIES}
    pool_size = max(int(config.get("poolSize") or F.DEFAULT_POOL_SIZE), 1)
    bands = F.VerdictBands(
        fair_z=float(config.get("fairBand") or F.DEFAULT_BANDS.fair_z),
        clear_z=float(config.get("clearBand") or F.DEFAULT_BANDS.clear_z),
    )

    pool = F.value_pool(
        lines, pool_size=pool_size, season=season, season_type=season_type
    )
    result = F.evaluate_trade(pool, give_ids, get_ids, weights=weights, bands=bands)
    notes.extend(result.notes)

    sweep: Optional[F.SensitivityRange] = None
    if bool(config.get("showSensitivity", True)):
        sweep = F.trade_sensitivity(
            lines, give_ids, get_ids, weights=weights, pool_size=pool_size, bands=bands
        )
        if sweep.flips:
            notes.append(
                "The scenarios do not agree on whether this trade helps: it wins under some "
                "and loses under others. That disagreement is the answer, not the midpoint."
            )

    everyone = [v.player_id for v in (*result.give.valuations, *result.get.valuations)]
    refs = q.player_ref_dicts(ctx, everyone)
    return _payload(result, sweep, refs, season, season_type, punts), "estimated", notes


def _side_ids(raw: Any, label: str, ctx: ResolveContext, notes: list[str]) -> list[int]:
    """Player ids for one side, resolving ``$`` tokens the way every other subject field does.

    A list field carries tokens as readily as a single one — ``["$favorite_player"]`` is what a
    preset ships — and an earlier version cast straight to ``int``, so every token raised
    ``ValueError`` and was dropped without a word. That produced an empty trade that looked like
    the reader simply had not filled it in.
    """
    ids: list[int] = []
    for value in raw or []:
        try:
            ids.append(resolve_subject_token(value, "player", ctx, field=f"{label}PlayerIds"))
        except WidgetError:
            notes.append(f"A player on the {label} side could not be resolved and was skipped.")
        except (TypeError, ValueError):
            continue
    if len(ids) > MAX_PER_SIDE:
        notes.append(
            f"Only the first {MAX_PER_SIDE} players on the {label} side are used; a longer list "
            "stops fitting a nine-row category table."
        )
    return ids[:MAX_PER_SIDE]


def _payload(
    result: F.TradeResult,
    sweep: Optional[F.SensitivityRange],
    refs: dict[int, dict[str, Any]],
    season: str,
    season_type: str,
    punts: Sequence[str],
) -> dict[str, Any]:
    verdicts = result.category_verdicts
    return {
        "season": season,
        "seasonType": season_type,
        "puntCategories": list(punts),
        "give": _side(result.give, refs),
        "get": _side(result.get, refs),
        "categories": [
            {
                "category": category,
                "give": round(values[0], 3),
                "get": round(values[1], 3),
                "change": round(values[2], 3),
                "net": round(values[3], 3),
                "verdict": verdicts[category],
                "punted": category in punts,
            }
            for category, values in result.categories.items()
        ],
        "changeZ": round(result.change_z, 3),
        "rosterAdjustment": round(result.roster_adjustment, 3),
        "netZ": round(result.net_z, 3),
        "replacementValue": round(result.replacement_value, 3),
        "poolSpread": round(result.pool_spread, 3),
        "verdict": result.verdict,
        "bands": {"fair": result.bands.fair_z, "clear": result.bands.clear_z},
        "points": {
            system: {
                "change": round(result.change_points[system], 2),
                "net": round(result.net_points[system], 2),
                "verdict": result.points_verdicts[system],
            }
            for system in result.change_points
        },
        "sensitivity": None if sweep is None else _sensitivity(sweep),
        "note": (
            "The range is a sweep over named assumptions, not a confidence interval: nothing "
            "here is a probability. A trade whose sign survives every scenario is a different "
            "proposition from one that only wins if everybody stays healthy."
        ),
    }


def _sensitivity(sweep: F.SensitivityRange) -> dict[str, Any]:
    labels = {s.key: s.label for s in sweep.scenarios}
    return {
        "base": round(sweep.base, 3),
        "low": round(sweep.low, 3),
        "high": round(sweep.high, 3),
        "width": round(sweep.width, 3),
        "lowScenario": sweep.low_scenario,
        "highScenario": sweep.high_scenario,
        "flips": sweep.flips,
        "scenarios": [
            {"key": key, "label": labels.get(key, key), "net": round(value, 3)}
            for key, value in sweep.by_scenario.items()
        ],
    }


def _side(side: F.TradeSide, refs: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {
        "label": side.label,
        "count": side.count,
        "totalZ": round(side.total_z(), 3),
        "espnPoints": round(side.points("espn_points"), 2),
        "yahooPoints": round(side.points("yahoo_points"), 2),
        "players": [
            {
                "player": refs.get(v.player_id),
                "totalZ": round(v.total_z(), 3),
                "baselineRank": v.baseline_rank,
                "gamesPlayed": v.line.games_played,
                "categories": {c: round(v.z(c), 3) for c in F.CATEGORIES},
                "availability": "estimated",
            }
            for v in side.valuations
        ],
        "missing": list(side.missing),
    }


def _empty(season: str, season_type: str) -> dict[str, Any]:
    return {
        "season": season,
        "seasonType": season_type,
        "puntCategories": [],
        "give": {"label": "give", "count": 0, "totalZ": 0.0, "espnPoints": 0.0,
                 "yahooPoints": 0.0, "players": [], "missing": []},
        "get": {"label": "get", "count": 0, "totalZ": 0.0, "espnPoints": 0.0,
                "yahooPoints": 0.0, "players": [], "missing": []},
        "categories": [],
        "changeZ": 0.0,
        "rosterAdjustment": 0.0,
        "netZ": 0.0,
        "replacementValue": 0.0,
        "poolSpread": 0.0,
        "verdict": "fair",
        "bands": {"fair": F.DEFAULT_BANDS.fair_z, "clear": F.DEFAULT_BANDS.clear_z},
        "points": {},
        "sensitivity": None,
        "note": None,
    }
