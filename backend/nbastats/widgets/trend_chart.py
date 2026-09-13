"""``trend_chart`` — a metric game by game, with a rolling average and a league baseline.

Up to four series, each with a ``colorIndex`` the client maps onto its palette. The rolling
line comes from :func:`nbastats.metrics.rolling_average`, which starts only once the window is
full and refuses to average over a DNP stretch — a five-game average that quietly became a
two-game average would read as a collapse in form that never happened.

``yDomain`` is padded around the data rather than taken from the metric's catalog domain: a
player whose TS% lives between .55 and .62 deserves a chart that shows the shape of that, not
a flat line across a .35-.75 axis.

Payload: ``contracts/CONTRACT.md`` §4 ``trend_chart``.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from .. import catalog
from ..metrics import rolling_average
from . import queries as q
from .base import (
    ResolveContext,
    attach_availability,
    availability_note,
    resolve_season,
    resolve_subject_list,
)

__all__ = ["resolve", "MAX_SERIES", "DOMAIN_PADDING"]

#: The widget catalog caps ``subjectIds`` here; enforced again so a hand-written layout
#: cannot ask for twenty lines on a 360-point-tall tile.
MAX_SERIES = 4

#: Share of the data's own range left as breathing room above and below the line.
DOMAIN_PADDING = 0.08


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``trend_chart``."""
    notes: list[str] = []
    subject_type = "team" if config.get("subjectType") == "team" else "player"
    subject_ids = resolve_subject_list(
        config.get("subjectIds"), subject_type, ctx, field="subjectIds", maximum=MAX_SERIES
    )
    for subject_id in subject_ids[:1]:
        ctx.note_team(subject_id) if subject_type == "team" else ctx.note_player(subject_id)

    metric_key = config.get("metric") or "ts_pct"
    season = resolve_season(ctx, config.get("season"))
    season_type = config.get("seasonType") or "Regular Season"
    window = max(1, int(config.get("rollingWindow") or 5))
    show_league_average = config.get("showLeagueAverage", True)
    show_raw_points = config.get("showRawPoints", True)

    per_game_availability = catalog.metric_availability(metric_key, season, "game")
    series: list[dict[str, Any]] = []
    if per_game_availability == "unavailable":
        _, note = availability_note(metric_key, season, "game")
        if note:
            notes.append(note)
    else:
        for index, subject_id in enumerate(subject_ids):
            series.append(
                _series(
                    ctx,
                    subject_type,
                    subject_id,
                    index,
                    metric_key,
                    season,
                    season_type,
                    window,
                    show_raw_points,
                )
            )
        if not any(entry["points"] for entry in series):
            notes.append(f"No {season} {season_type} games are loaded for these subjects.")

    league_average = None
    if show_league_average:
        spread = q.distribution(ctx, metric_key, subject_type, season, season_type)
        league_average = spread.average

    availability = attach_availability(notes, [metric_key], season, "game")
    payload: dict[str, Any] = {
        "metric": q.metric_descriptor_dict(metric_key),
        "rollingWindow": window,
        "leagueAverage": league_average,
        "yDomain": _y_domain(series, metric_key, league_average),
        "series": series,
    }
    return payload, availability, notes


def _series(
    ctx: ResolveContext,
    subject_type: str,
    subject_id: int,
    color_index: int,
    metric_key: str,
    season: str,
    season_type: str,
    window: int,
    show_raw_points: bool,
) -> dict[str, Any]:
    """One subject's line: oldest game first, with the trailing rolling average alongside."""
    team_index = q.all_teams(ctx)

    if subject_type == "team":
        lines = list(reversed(q.team_games(ctx, subject_id, season, season_type)))
        team_row = team_index.get(subject_id)
        label = team_row.name if team_row is not None else str(subject_id)
    else:
        lines = list(reversed(q.player_games(ctx, subject_id, season, season_type)))
        player_row = q.player(ctx, subject_id)
        label = player_row.full_name if player_row is not None else str(subject_id)

    raw = [line.values([metric_key])[metric_key] for line in lines]
    rolling = rolling_average(raw, window)

    points: list[dict[str, Any]] = []
    for line, value, average in zip(lines, raw, rolling):
        opponent = team_index.get(line.opponent_id)
        points.append(
            {
                "x": line.game.game_date.isoformat(),
                "y": (None if not show_raw_points else _number(value)),
                "rolling": _number(average),
                "gameId": line.game.game_id,
                "opponentAbbr": opponent.abbr if opponent is not None else None,
            }
        )

    return {
        "id": str(subject_id),
        "label": label,
        "colorIndex": color_index,
        "points": points,
    }


def _number(value: Optional[float]) -> Optional[float]:
    return None if value is None else float(value)


def _y_domain(
    series: Sequence[dict[str, Any]], metric_key: str, league_average: Optional[float]
) -> Optional[dict[str, float]]:
    """Axis bounds that contain every point, the rolling line and the league baseline.

    Falls back to the metric's catalog domain when there is nothing to plot, and to ``None``
    when even that is absent — an axis invented from no data is worse than no axis.
    """
    values: list[float] = []
    for entry in series:
        for point in entry["points"]:
            for key in ("y", "rolling"):
                value = point.get(key)
                if value is not None:
                    values.append(float(value))
    if league_average is not None and values:
        values.append(float(league_average))

    if not values:
        domain = catalog.metric(metric_key).get("domain")
        if domain:
            return {"min": float(domain["min"]), "max": float(domain["max"])}
        return None

    low, high = min(values), max(values)
    if low == high:
        # A flat series still needs a band, or the client divides by a zero-height axis.
        pad = abs(low) * DOMAIN_PADDING or 1.0
    else:
        pad = (high - low) * DOMAIN_PADDING
    return {"min": low - pad, "max": high + pad}
