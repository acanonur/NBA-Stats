"""``stat_tile`` — one headline number, its supporting cast, and a sparkline.

The smallest widget in the catalog and the one most likely to be wrong in a way nobody
notices, because a tile that shows ``0.0`` looks like a bad night rather than like a stat
that did not exist. Everything numeric here goes through
:func:`nbastats.widgets.queries.metric_value_dict`, so an era-missing metric is ``null`` with
``"unavailable"`` and an em dash, and the sparkline breaks rather than dipping to the floor.

Payload: ``contracts/CONTRACT.md`` §4 ``stat_tile``.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import catalog
from . import queries as q
from .base import (
    ResolveContext,
    attach_availability,
    context_label,
    resolve_season,
    resolve_subject_token,
)

__all__ = ["resolve", "SPARKLINE_FALLBACK_WINDOW"]

#: Used when a config somehow carries no sparkline window (a hand-written layout).
SPARKLINE_FALLBACK_WINDOW = 15


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``stat_tile``."""
    notes: list[str] = []
    subject_type = "team" if config.get("subjectType") == "team" else "player"
    subject_id = _subject(config, subject_type, ctx)
    season = resolve_season(ctx, config.get("season"))
    season_type = config.get("seasonType") or "Regular Season"
    per_mode = config.get("perMode") or "PerGame"

    metric_key = config.get("metric") or "ts_pct"
    secondary_keys = [key for key in (config.get("secondaryMetrics") or []) if key != metric_key]

    row = (
        q.player_season_row(ctx, subject_id, season, season_type)
        if subject_type == "player"
        else q.team_season_row(ctx, subject_id, season, season_type)
    )
    if row is None:
        notes.append(
            f"No {season} {season_type} line is loaded for this "
            f"{'player' if subject_type == 'player' else 'team'}."
        )

    primary = _value_for(ctx, row, metric_key, subject_id, subject_type, season, season_type, per_mode)
    secondary = [
        _value_for(ctx, row, key, subject_id, subject_type, season, season_type, per_mode)
        for key in secondary_keys
    ]

    show_sparkline = config.get("showSparkline", True)
    window = int(config.get("sparklineWindow") or SPARKLINE_FALLBACK_WINDOW)
    sparkline = (
        _sparkline(ctx, subject_type, subject_id, metric_key, season, season_type, window)
        if show_sparkline
        else []
    )

    availability = attach_availability(notes, [metric_key, *secondary_keys], season, "season")
    payload: dict[str, Any] = {
        "subject": q.subject_dict(ctx, subject_type, subject_id, season),
        "context": context_label(season, season_type, per_mode),
        "primary": primary,
        "secondary": secondary,
        "sparkline": sparkline,
        "sparklineMetric": metric_key,
    }
    return payload, availability, notes


def _subject(config: dict[str, Any], subject_type: str, ctx: ResolveContext) -> int:
    subject_id = resolve_subject_token(
        config.get("subjectId"), subject_type, ctx, field="subjectId"
    )
    if subject_type == "team":
        ctx.note_team(subject_id)
    else:
        ctx.note_player(subject_id)
    return subject_id


def _value_for(
    ctx: ResolveContext,
    row: Any,
    metric_key: str,
    subject_id: int,
    subject_type: str,
    season: str,
    season_type: str,
    per_mode: str,
) -> dict[str, Any]:
    """One ``MetricValue`` with its league context attached.

    The rank and percentile come from the season's whole qualified field, and ``delta`` is
    the distance from the league average — the three things a tile needs to say "good" rather
    than just "0.61".
    """
    value = q.season_value(row, metric_key, season, subject_type, per_mode)
    spread = q.distribution(ctx, metric_key, subject_type, season, season_type, per_mode)
    rank, percentile = spread.for_subject(subject_id)
    return q.metric_value_dict(
        metric_key,
        value,
        rank=rank,
        percentile=percentile,
        league_average=spread.average,
        delta=spread.delta(value),
        season=season,
    )


def _sparkline(
    ctx: ResolveContext,
    subject_type: str,
    subject_id: int,
    metric_key: str,
    season: str,
    season_type: str,
    window: int,
) -> list[dict[str, Any]]:
    """The last ``window`` games of this metric, oldest first.

    A game the metric does not exist for keeps its place with ``y: null`` — the client's
    sparkline breaks the line there instead of drawing a spike down to zero, which is the
    whole reason the point is present at all.
    """
    if catalog.metric_availability(metric_key, season, "game") == "unavailable":
        return []
    size = max(1, min(int(window), 82))

    points: list[dict[str, Any]] = []
    if subject_type == "player":
        lines = q.player_games(ctx, subject_id, season, season_type)[:size]
        for line in reversed(lines):
            points.append(
                {
                    "x": line.game.game_date.isoformat(),
                    "y": _as_number(line.values([metric_key])[metric_key]),
                    "gameId": line.game.game_id,
                }
            )
    else:
        lines = q.team_games(ctx, subject_id, season, season_type)[:size]
        for line in reversed(lines):
            points.append(
                {
                    "x": line.game.game_date.isoformat(),
                    "y": _as_number(line.values([metric_key])[metric_key]),
                    "gameId": line.game.game_id,
                }
            )
    return points


def _as_number(value: Optional[float]) -> Optional[float]:
    return None if value is None else float(value)
