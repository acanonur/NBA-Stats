"""``shot_profile`` — where the shots come from, and how they fall.

Five zones in a fixed order: ``rim``, ``paint_non_rim``, ``mid_range``, ``corner_three``,
``above_break_three``. The order is the court, from the basket outward, and the client draws
it that way; it is not sorted by volume.

**A season before 1996-97 is not an error.** Shot charts begin with league-wide play-by-play
in 1996-97, so an older season comes back with the zones present, every number ``null``, an
availability of ``"unavailable"`` and a ``note`` that says why. A 404 or a 500 there would be
the service claiming the request was wrong, when in fact the request was fine and history is
simply shorter than the widget.

Payload: ``contracts/CONTRACT.md`` §4 ``shot_profile``.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import catalog
from ..models import SHOT_ZONES
from . import queries as q
from .base import ResolveContext, resolve_season, resolve_subject_token

__all__ = ["resolve", "SHOT_CHARTS_FROM", "ZONES"]

#: First season with play-by-play shot locations (``contracts/CONTRACT.md`` §6).
SHOT_CHARTS_FROM = "1996-97"

#: The contract's fixed zone order, from the rim outward.
ZONES: tuple[str, ...] = tuple(SHOT_ZONES)


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``shot_profile``."""
    notes: list[str] = []
    subject_type = "team" if config.get("subjectType") == "team" else "player"
    subject_id = resolve_subject_token(
        config.get("subjectId"), subject_type, ctx, field="subjectId"
    )
    if subject_type == "team":
        ctx.note_team(subject_id)
    else:
        ctx.note_player(subject_id)

    season = resolve_season(ctx, config.get("season"))
    season_type = config.get("seasonType") or "Regular Season"
    compare = config.get("compareToLeague", True)

    payload: dict[str, Any] = {
        "subject": q.subject_dict(ctx, subject_type, subject_id, season),
        "season": season,
        "seasonType": season_type,
        "zones": [],
        "threePointRate": None,
        "freeThrowRate": None,
        "note": None,
    }

    if catalog.season_sort_key(season) < catalog.season_sort_key(SHOT_CHARTS_FROM):
        note = (
            f"Shot locations begin in {SHOT_CHARTS_FROM}, with league-wide play-by-play; "
            f"the {season} season has box scores but no shot chart."
        )
        payload["zones"] = [_empty_zone(zone) for zone in ZONES]
        payload["note"] = note
        notes.append(note)
        return payload, "unavailable", notes

    rows = q.shot_zones(ctx, subject_type, subject_id, season, season_type)
    league = q.league_shot_zones(ctx, subject_type, season, season_type) if compare else {}

    payload["zones"] = [
        _zone(zone, rows.get(zone), league.get(zone) if compare else None) for zone in ZONES
    ]

    season_row = (
        q.player_season_row(ctx, subject_id, season, season_type)
        if subject_type == "player"
        else q.team_season_row(ctx, subject_id, season, season_type)
    )
    payload["threePointRate"] = q.season_value(
        season_row, "fg3a_rate", season, subject_type, "PerGame"
    )
    payload["freeThrowRate"] = q.season_value(season_row, "ftr", season, subject_type, "PerGame")

    if not rows:
        note = f"No {season} {season_type} shot chart is loaded for this subject."
        payload["note"] = note
        notes.append(note)
        return payload, "partial", notes

    return payload, "full", notes


def _empty_zone(zone: str) -> dict[str, Any]:
    """A zone with every number ``null`` — the shape the client needs to draw an em dash."""
    return {
        "zone": zone,
        "label": q.zone_label(zone),
        "fga": None,
        "fgPct": None,
        "shareOfFga": None,
        "pointsPerShot": None,
        "leagueFgPct": None,
        "leagueShareOfFga": None,
    }


def _zone(
    zone: str, row: Any, league: Optional[dict[str, Optional[float]]]
) -> dict[str, Any]:
    """One zone of the profile, with the league's own shot diet alongside when asked for."""
    entry = _empty_zone(zone)
    if row is not None:
        entry["fga"] = row.fga
        entry["fgPct"] = row.fg_pct
        entry["shareOfFga"] = row.share_of_fga
        entry["pointsPerShot"] = row.points_per_shot
    if league is not None:
        entry["leagueFgPct"] = league.get("fgPct")
        entry["leagueShareOfFga"] = league.get("shareOfFga")
    return entry
