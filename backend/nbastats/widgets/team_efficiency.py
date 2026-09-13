"""``team_efficiency`` — the league table by offence, defence, net rating and pace.

The ranks in this payload are **league-wide**, not positions in the rendered table. With
``conference: "East"`` the eighth-best offence in the league is still rank 8, and the table
reads ``1, 3, 4, 7…`` — which is the honest answer to "how good is this offence?" and the one
a reader screenshots. Row order still follows the ``sortBy`` metric, so the best team is at
the top either way.

Direction comes from the catalog, so sorting by ``def_rtg`` puts the *best* defence first
without the widget knowing that defensive rating is better when lower.

Payload: ``contracts/CONTRACT.md`` §4 ``team_efficiency``.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import catalog
from . import queries as q
from .base import ResolveContext, attach_availability, resolve_season

__all__ = ["resolve", "TABLE_METRICS", "LEAGUE_AVERAGE_METRICS"]

#: The four columns the contract's table carries.
TABLE_METRICS: tuple[str, ...] = ("off_rtg", "def_rtg", "net_rtg", "pace")

#: What the header caption compares against.
LEAGUE_AVERAGE_METRICS: tuple[str, ...] = TABLE_METRICS


def _win_pct(row: Any) -> float:
    """Win percentage from the stored column, else from the record. Every era recorded both."""
    stored = getattr(row, "win_pct", None)
    if stored is not None:
        return float(stored)
    wins = row.wins or 0
    losses = row.losses or 0
    played = wins + losses
    return (wins / played) if played else 0.0


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``team_efficiency``."""
    notes: list[str] = []
    season = resolve_season(ctx, config.get("season"))
    season_type = config.get("seasonType") or "Regular Season"
    sort_by = config.get("sortBy") or "net_rtg"
    conference = config.get("conference") or "all"
    limit = max(1, int(config.get("limit") or 30))
    style = config.get("style") or "table"

    keys = list(TABLE_METRICS)
    if sort_by not in keys:
        keys.append(sort_by)

    rows_by_team = q.team_season_index(ctx, season, season_type)
    directory = q.all_teams(ctx)
    if not rows_by_team:
        notes.append(f"No {season} {season_type} team rows are loaded.")

    spreads = {
        key: q.distribution(ctx, key, "team", season, season_type) for key in keys
    }
    sort_spread = spreads[sort_by]

    entries: list[tuple[Optional[float], int, Any]] = []
    for team_id, row in rows_by_team.items():
        team_row = directory.get(team_id)
        if team_row is None:
            continue
        if conference in ("East", "West") and team_row.conference != conference:
            continue
        entries.append((q.season_value(row, sort_by, season, "team", "PerGame"), team_id, row))

    # A team with no value for the sort metric (an era that never recorded it) sinks to the
    # bottom of the table rather than being dropped: it still played the season.
    entries.sort(
        key=lambda entry: (
            entry[0] is None,
            -(entry[0] or 0.0) if sort_spread.higher_is_better else (entry[0] or 0.0),
        )
    )

    # When *no* team has the sort metric, the sort key above is identical for every entry and the
    # order that survives is whatever the database returned. Handing that out as ranks 1..N
    # produced a "league table" where a 13-7 team sat below a 7-13 team with every value an em
    # dash. Win percentage is recorded in every era, so order by that instead and say so; the
    # ranks themselves are left unset below, because there is genuinely no ranking to report.
    sort_metric_is_rankable = any(value is not None for value in (entry[0] for entry in entries))
    if entries and not sort_metric_is_rankable:
        entries.sort(key=lambda entry: -_win_pct(entry[2]))
        name = catalog.metric(sort_by).get("name", sort_by)
        notes.append(
            f"{name} was not recorded in {season}, so this table is ordered by win "
            "percentage rather than by it."
        )

    rows: list[dict[str, Any]] = []
    for _, team_id, row in entries[:limit]:
        values = {
            key: q.season_value(row, key, season, "team", "PerGame") for key in keys
        }
        ranks = {key: spreads[key].for_subject(team_id)[0] for key in keys}
        rows.append(
            {
                # 0, not a made-up position: the client renders a non-positive rank as an em
                # dash (`contracts/CONTRACT.md` §6 — never a number the data does not support).
                "rank": ranks.get(sort_by) or 0,
                "team": q.team_ref_dict(ctx, team_id),
                "wins": row.wins,
                "losses": row.losses,
                "values": values,
                "ranks": ranks,
            }
        )

    availability = attach_availability(notes, keys, season, "season")
    payload: dict[str, Any] = {
        "season": season,
        "seasonType": season_type,
        "sortBy": sort_by,
        "style": style,
        "leagueAverage": {
            key: spreads[key].average
            for key in LEAGUE_AVERAGE_METRICS
            if key in spreads
        },
        "rows": rows,
    }
    return payload, availability, notes
