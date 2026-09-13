"""``four_factors`` — Dean Oliver's four factors, offence and defence.

The order and the weights are fixed by the contract and by the literature, not by the config:
shooting 0.40, turnovers 0.25, offensive rebounding 0.20, free throws 0.15, in that order.
The defensive side is the same four conceded to the opponent (``opp_efg_pct`` and friends),
which is why "showOpponent" produces a second list rather than a second widget.

Direction matters twice here. ``tov_pct`` is better when lower, and so is ``opp_efg_pct`` —
but ``opp_tov_pct`` is better when *higher*, because forcing turnovers is a good thing. The
catalog knows all of that; this module never second-guesses it, it just asks
:func:`nbastats.widgets.queries.distribution` for the rank and lets the metric's own
``higherIsBetter`` decide which end of the league is rank 1.

Payload: ``contracts/CONTRACT.md`` §4 ``four_factors``.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import catalog
from ..metrics import FOUR_FACTOR_WEIGHTS
from . import queries as q
from .base import ResolveContext, attach_availability, resolve_season, resolve_subject_token

__all__ = ["resolve", "OFFENSE_FACTORS", "DEFENSE_FACTORS", "FACTOR_WEIGHTS"]

#: The contract's fixed order. Changing it changes the chart the client draws.
OFFENSE_FACTORS: tuple[str, ...] = ("efg_pct", "tov_pct", "oreb_pct", "ftr")

#: The same four, conceded.
DEFENSE_FACTORS: tuple[str, ...] = ("opp_efg_pct", "opp_tov_pct", "opp_oreb_pct", "opp_ftr")

#: 0.40 / 0.25 / 0.20 / 0.15, read from the metrics engine so there is one copy of them.
FACTOR_WEIGHTS: dict[str, float] = dict(FOUR_FACTOR_WEIGHTS)


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``four_factors``."""
    notes: list[str] = []
    team_id = resolve_subject_token(config.get("teamId"), "team", ctx, field="teamId")
    ctx.note_team(team_id)

    season = resolve_season(ctx, config.get("season"))
    season_type = config.get("seasonType") or "Regular Season"
    show_opponent = config.get("showOpponent", True)
    compare = config.get("comparison") or "league"

    row = q.team_season_row(ctx, team_id, season, season_type)
    if row is None:
        notes.append(f"No {season} {season_type} line is loaded for this team.")

    offense = [
        _factor(ctx, row, team_id, key, season, season_type, compare)
        for key in OFFENSE_FACTORS
    ]
    defense = (
        [
            _factor(ctx, row, team_id, key, season, season_type, compare)
            for key in DEFENSE_FACTORS
        ]
        if show_opponent
        else []
    )

    keys = list(OFFENSE_FACTORS) + (list(DEFENSE_FACTORS) if show_opponent else [])
    availability = attach_availability(notes, keys, season, "season")

    payload: dict[str, Any] = {
        "team": q.team_ref_dict(ctx, team_id),
        "season": season,
        "seasonType": season_type,
        "offense": offense,
        "defense": defense,
    }
    return payload, availability, notes


def _factor(
    ctx: ResolveContext,
    row: Any,
    team_id: int,
    metric_key: str,
    season: str,
    season_type: str,
    compare: str,
) -> dict[str, Any]:
    """One factor entry, already formatted for display.

    This is not a ``MetricValue``: the contract gives the four factors their own flatter
    shape carrying the ``weight``, because the client draws them as a weighted bar rather
    than as a stat line.
    """
    descriptor = catalog.metric(metric_key)
    availability = catalog.metric_availability(metric_key, season, "season")
    value = q.season_value(row, metric_key, season, "team", "PerGame")
    if availability == "unavailable":
        value = None

    league_average: Optional[float] = None
    percentile: Optional[float] = None
    rank: Optional[int] = None
    if compare == "league" and value is not None:
        spread = q.distribution(ctx, metric_key, "team", season, season_type)
        league_average = spread.average
        rank, percentile = spread.for_subject(team_id)

    return {
        "key": metric_key,
        "label": descriptor.get("shortName", metric_key),
        "weight": FACTOR_WEIGHTS.get(_weight_key(metric_key), 0.0),
        "value": value,
        "displayValue": catalog.format_metric(metric_key, value),
        "leagueAverage": league_average,
        "percentile": percentile,
        "rank": rank,
    }


def _weight_key(metric_key: str) -> str:
    """``"opp_efg_pct"`` weighs the same as ``"efg_pct"``: it is the same factor, conceded."""
    return metric_key[4:] if metric_key.startswith("opp_") else metric_key
