"""``career_arc`` — one metric across a whole career, with the eras marked.

The chart the era-availability model exists for. A career that starts in 1969 and ends in 1984
crosses three boundaries — the OREB/DREB split, individual turnovers, the three-point line —
and a line drawn straight through them would be a lie told in a smooth curve. So every season
carries its own ``availability``, the boundaries that govern *this* metric are shipped with
the payload for the client to draw as vertical rules, and a season the metric did not exist
for is ``null`` rather than a dip to zero.

``playoffSeasons`` is the overlay. It is the postseason line drawn over a regular-season arc;
when the arc itself is already the postseason there is nothing to overlay and it is empty,
rather than the same line drawn twice.

Payload: ``contracts/CONTRACT.md`` §4 ``career_arc``.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from .. import catalog
from ..models import PlayerSeason
from . import queries as q
from .base import ResolveContext, availability_note, resolve_subject_token

__all__ = ["resolve", "PLAYOFF_SEASON_TYPE"]

PLAYOFF_SEASON_TYPE = "Playoffs"


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``career_arc``."""
    notes: list[str] = []
    player_id = resolve_subject_token(config.get("playerId"), "player", ctx, field="playerId")
    ctx.note_player(player_id)

    metric_key = config.get("metric") or "per"
    season_type = config.get("seasonType") or "Regular Season"
    include_playoffs = config.get("includePlayoffs", True)
    x_axis = config.get("xAxis") or "season"

    primary = _arc(ctx, player_id, metric_key, season_type)
    overlay: list[dict[str, Any]] = []
    if include_playoffs and season_type != PLAYOFF_SEASON_TYPE:
        overlay = _arc(ctx, player_id, metric_key, PLAYOFF_SEASON_TYPE)

    if not primary:
        notes.append(f"No {season_type} seasons are loaded for this player.")

    seasons = [entry["season"] for entry in primary]
    availability = catalog.metric_availability(metric_key, seasons or None, "season")
    for boundary_season in _note_seasons(primary):
        _, note = availability_note(metric_key, boundary_season, "season")
        if note and note not in notes:
            notes.append(note)

    payload: dict[str, Any] = {
        "player": q.player_ref_dict(ctx, player_id, None),
        "metric": q.metric_descriptor_dict(metric_key),
        "xAxis": x_axis,
        "seasons": primary,
        "playoffSeasons": overlay,
        "eraBoundaries": _era_boundaries(metric_key),
        "peak": _peak(primary, metric_key),
    }
    return payload, availability, notes


def _arc(
    ctx: ResolveContext, player_id: int, metric_key: str, season_type: str
) -> list[dict[str, Any]]:
    """One point per season, oldest first.

    A player traded mid-season has two rows; the arc shows one point, from the team he played
    the most games for — a career chart with two 2011-12 dots is a bug report waiting to
    happen, and the split belongs on the player page instead.
    """
    rows = q.career_season_rows(ctx, player_id, season_type)
    if not rows:
        return []

    by_season: dict[str, PlayerSeason] = {}
    for row in rows:
        current = by_season.get(row.season)
        if current is None or (row.gp or 0) > (current.gp or 0):
            by_season[row.season] = row

    team_index = q.all_teams(ctx)
    out: list[dict[str, Any]] = []
    for season in sorted(by_season, key=catalog.season_sort_key):
        row = by_season[season]
        value = q.season_value(row, metric_key, season, "player", "PerGame")
        availability = catalog.metric_availability(metric_key, season, "season")
        if availability == "unavailable":
            value = None
        elif value is None:
            availability = "unavailable"
        team_row = team_index.get(row.team_id)
        out.append(
            {
                "season": season,
                "seasonType": row.season_type,
                "age": row.age,
                "teamAbbr": team_row.abbr if team_row is not None else None,
                "gp": row.gp,
                "value": value,
                "displayValue": catalog.format_metric(metric_key, value),
                "availability": availability,
            }
        )
    return out


def _note_seasons(arc: Sequence[dict[str, Any]]) -> list[str]:
    """The seasons worth explaining: the first estimated one and the first missing one.

    One note per *kind* of gap. A twenty-year career that predates the possession era would
    otherwise produce twenty identical sentences.
    """
    seen: set[str] = set()
    out: list[str] = []
    for entry in arc:
        availability = entry["availability"]
        if availability == "full" or availability in seen:
            continue
        seen.add(availability)
        out.append(entry["season"])
    return out


def _era_boundaries(metric_key: str) -> list[dict[str, Any]]:
    """The catalog boundaries that govern this metric, in chronological order.

    Not every boundary in league history — only the ones that explain *this* line: the season
    the metric begins, and the season it stops being a box-score estimate.
    """
    spec = catalog.metric(metric_key)["availability"]
    wanted = {
        season
        for season in (spec.get("seasonFrom"), spec.get("estimatedBefore"))
        if season
    }
    boundaries = [
        {
            "season": boundary["season"],
            "label": boundary.get("label", ""),
            "detail": boundary.get("detail"),
        }
        for boundary in catalog.era_boundaries()
        if boundary.get("season") in wanted
    ]
    return sorted(boundaries, key=lambda entry: catalog.season_sort_key(entry["season"]))


def _peak(arc: Sequence[dict[str, Any]], metric_key: str) -> Optional[dict[str, Any]]:
    """The best season of the arc, in the metric's own direction."""
    scored = [entry for entry in arc if entry["value"] is not None]
    if not scored:
        return None
    higher_is_better = bool(catalog.metric(metric_key).get("higherIsBetter", True))
    best = (max if higher_is_better else min)(scored, key=lambda entry: float(entry["value"]))
    return {
        "season": best["season"],
        "value": best["value"],
        "displayValue": best["displayValue"],
    }
