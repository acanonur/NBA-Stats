"""``comparison`` — two to four players side by side.

``normalization`` decides what the bars are drawn from. ``"percentile"`` puts every metric on
one 0-1 axis, which is the only way a chart can show PTS and TS% in the same row; ``"raw"``
leaves the numbers alone for a table. Either way the ``MetricValue`` carries the real value,
the rank and the league average, so the table style and the bar style read the same payload.

One wrinkle the presets create on purpose: ``$favorite_player`` and ``$league_leader`` can
resolve to the same person — the reader's favourite may *be* the scoring leader, and with no
favourite set both fall back to a featured subject. Rather than serve a one-bar comparison,
the field is topped up from the season's PIE leaders and a note explains it, so the tile still
says something.

Payload: ``contracts/CONTRACT.md`` §4 ``comparison``.
"""
from __future__ import annotations

from typing import Any

from . import queries as q
from .base import ResolveContext, attach_availability, resolve_season, resolve_subject_list

__all__ = ["resolve", "MIN_SUBJECTS", "MAX_SUBJECTS", "FALLBACK_METRICS"]

MIN_SUBJECTS = 2
MAX_SUBJECTS = 4

#: Used only if a layout arrives with an empty metric list.
FALLBACK_METRICS: tuple[str, ...] = (
    "pts",
    "ts_pct",
    "usg_pct",
    "ast_pct",
    "reb_pct",
    "net_rtg",
)

#: What the field is topped up from when the configured subjects collapse into one.
BACKFILL_METRIC = "pie"


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``comparison``."""
    notes: list[str] = []
    player_ids = resolve_subject_list(
        config.get("playerIds"), "player", ctx, field="playerIds", maximum=MAX_SUBJECTS
    )
    ctx.note_player(player_ids[0])

    season = resolve_season(ctx, config.get("season"))
    season_type = config.get("seasonType") or "Regular Season"
    metric_keys = list(config.get("metrics") or FALLBACK_METRICS)
    normalization = config.get("normalization") or "percentile"
    style = config.get("style") or "bars"

    player_ids = _ensure_two(player_ids, ctx, season, season_type, notes)

    refs = q.player_ref_dicts(ctx, player_ids, season)

    subjects: list[dict[str, Any]] = []
    for index, player_id in enumerate(player_ids):
        row = q.player_season_row(ctx, player_id, season, season_type)
        values: list[dict[str, Any]] = []
        for key in metric_keys:
            value = q.season_value(row, key, season, "player", "PerGame")
            spread = q.distribution(ctx, key, "player", season, season_type)
            rank, percentile = spread.for_subject(player_id)
            values.append(
                q.metric_value_dict(
                    key,
                    value,
                    rank=rank,
                    percentile=percentile if normalization == "percentile" else None,
                    league_average=spread.average,
                    delta=spread.delta(value),
                    season=season,
                )
            )
        subjects.append(
            {
                "player": refs.get(player_id),
                "colorIndex": index,
                "values": values,
            }
        )

    if not any(
        value["value"] is not None for subject in subjects for value in subject["values"]
    ):
        notes.append(f"No {season} {season_type} lines are loaded for these players.")

    availability = attach_availability(notes, metric_keys, season, "season")
    payload: dict[str, Any] = {
        "season": season,
        "seasonType": season_type,
        "normalization": normalization,
        "style": style,
        "metrics": [q.metric_descriptor_dict(key) for key in metric_keys],
        "subjects": subjects,
    }
    return payload, availability, notes


def _ensure_two(
    player_ids: list[int],
    ctx: ResolveContext,
    season: str,
    season_type: str,
    notes: list[str],
) -> list[int]:
    """Top the field up to two distinct players when the tokens collapsed into one.

    A comparison of one player against himself is not a comparison. The replacement is drawn
    from the top of the season's PIE board — the same field ``$featured_player`` uses — and
    the substitution is always stated in a note, never made silently.
    """
    if len(player_ids) >= MIN_SUBJECTS:
        return player_ids

    spread = q.distribution(ctx, BACKFILL_METRIC, "player", season, season_type)
    ordered = sorted(
        spread.by_subject.items(), key=lambda item: item[1], reverse=spread.higher_is_better
    )
    for candidate, _ in ordered:
        if candidate in player_ids:
            continue
        player_ids = [*player_ids, int(candidate)]
        row = q.player(ctx, candidate)
        notes.append(
            "The configured players resolved to a single subject; "
            f"{row.full_name if row is not None else candidate} was added so there is "
            "something to compare against."
        )
        break

    if len(player_ids) < MIN_SUBJECTS:
        notes.append(
            f"Only one subject could be resolved for {season} {season_type}, so there is "
            "nothing to compare against."
        )
    return player_ids
