"""``player_snapshot`` — one player's advanced slate with league percentile bars.

The percentile is the whole point of this tile: 0.58 TS% means nothing on its own and means
"top of the league" once you know where the league sits. Each bar therefore comes from the
season's own qualified distribution (see :func:`nbastats.widgets.queries.distribution`), not
from a hard-coded range, so a 1992-93 snapshot is measured against 1992-93 and not against
today.

``eraNote`` is the one line under the grid explaining why some bars are empty — a 1992-93
season has no offensive rating at all, and saying so is better than five silent dashes.

Payload: ``contracts/CONTRACT.md`` §4 ``player_snapshot``.
"""
from __future__ import annotations

from typing import Any

from . import queries as q
from .base import ResolveContext, attach_availability, resolve_season, resolve_subject_token

__all__ = ["resolve", "FALLBACK_METRICS"]

#: Used only if a layout arrives with an empty metric list.
FALLBACK_METRICS: tuple[str, ...] = (
    "ts_pct",
    "usg_pct",
    "ast_pct",
    "reb_pct",
    "off_rtg",
    "def_rtg",
    "net_rtg",
    "pie",
)


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``player_snapshot``."""
    notes: list[str] = []
    player_id = resolve_subject_token(config.get("playerId"), "player", ctx, field="playerId")
    ctx.note_player(player_id)

    season = resolve_season(ctx, config.get("season"))
    season_type = config.get("seasonType") or "Regular Season"
    metric_keys = list(config.get("metrics") or FALLBACK_METRICS)
    show_percentiles = config.get("showPercentiles", True)

    row = q.player_season_row(ctx, player_id, season, season_type)
    if row is None:
        notes.append(f"No {season} {season_type} line is loaded for this player.")

    metrics: list[dict[str, Any]] = []
    for key in metric_keys:
        value = q.season_value(row, key, season, "player", "PerGame")
        spread = q.distribution(ctx, key, "player", season, season_type)
        rank, percentile = spread.for_subject(player_id)
        metrics.append(
            q.metric_value_dict(
                key,
                value,
                rank=rank if show_percentiles else None,
                percentile=percentile if show_percentiles else None,
                league_average=spread.average,
                delta=spread.delta(value),
                season=season,
            )
        )

    availability = attach_availability(notes, metric_keys, season, "season")
    team_abbr = None
    if row is not None:
        team_row = q.team(ctx, row.team_id)
        team_abbr = team_row.abbr if team_row is not None else None

    payload: dict[str, Any] = {
        "player": q.player_ref_dict(ctx, player_id, season),
        "season": season,
        "seasonType": season_type,
        "teamAbbr": team_abbr,
        "gp": row.gp if row is not None else None,
        "gs": row.gs if row is not None else None,
        "minutesPerGame": row.min_pg if row is not None else None,
        "metrics": metrics,
        "eraNote": _era_note(notes),
    }
    return payload, availability, notes


def _era_note(notes: list[str]) -> str | None:
    """The single sentence the tile prints under its grid.

    The resolve result already carries every note; this is the one the tile itself shows, so
    it is the first — the earliest boundary is the one that explains the most dashes.
    """
    return notes[0] if notes else None
