"""``daily_movers`` — last night's biggest games, measured against each player's own baseline.

Three directions, and the third is the interesting one:

``best``      the highest values on the slate, in the metric's own direction.
``worst``     the lowest — the same table read from the other end.
``surprise``  the **largest gap against the player's own season average**. A 34-point night
              from a player who averages 33 is not a surprise; 22 from someone who averages 9
              is. Ranking by the size of the gap is what the config's own help text promises.

``seasonAverage`` and ``delta`` are on every row whatever the direction, because the gap is
the context that makes a single-game number mean anything.

Payload: ``contracts/CONTRACT.md`` §4 ``daily_movers``.
"""
from __future__ import annotations

from datetime import date as date_type
from typing import Any, Optional

from .. import catalog
from . import queries as q
from .base import ResolveContext, availability_note, resolve_date, stat_line

__all__ = ["resolve", "DIRECTIONS", "LINE_METRICS", "DEFAULT_LIMIT"]

DIRECTIONS = ("best", "worst", "surprise")

#: What the one-line summary on each row is built from.
LINE_METRICS: tuple[str, ...] = ("pts", "reb", "ast", "stl", "blk")

DEFAULT_LIMIT = 8


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``daily_movers``."""
    notes: list[str] = []
    metric_key = config.get("metric") or "game_score"
    direction = config.get("direction") or "best"
    if direction not in DIRECTIONS:
        direction = "best"
    limit = max(1, int(config.get("limit") or DEFAULT_LIMIT))
    min_minutes = float(config.get("minMinutes") or 0.0)

    day = resolve_date(ctx, config.get("date"), field="date")
    if day is None:
        day = ctx.as_of or date_type.today()
        notes.append("No completed game is loaded, so there is no slate to rank.")
        return _payload(day, metric_key, direction, []), "unavailable", notes

    games = [game for game in q.slate_games(ctx, day) if game.status == "final"]
    lines = q.slate_player_lines(ctx, [game.game_id for game in games])

    availability, note = availability_note(metric_key, _slate_season(games, ctx), "game")
    if note:
        notes.append(note)
    if availability == "unavailable":
        return _payload(day, metric_key, direction, []), availability, notes

    higher_is_better = bool(catalog.metric(metric_key).get("higherIsBetter", True))

    candidates: list[tuple[float, Optional[float], Optional[float], Any]] = []
    for line in lines:
        if (line.basic.minutes or 0.0) < min_minutes:
            continue
        value = line.values([metric_key])[metric_key]
        if value is None:
            continue
        season_row = q.player_season_row(
            ctx, line.basic.player_id, line.game.season, line.game.season_type
        )
        baseline = q.season_value(
            season_row, metric_key, line.game.season, "player", "PerGame"
        )
        delta = None if baseline is None else float(value) - float(baseline)
        candidates.append((float(value), baseline, delta, line))

    if not candidates:
        notes.append(
            f"No line on {day.isoformat()} cleared {min_minutes:.0f} minutes with a "
            f"{metric_key} value."
        )

    ordered = _order(candidates, direction, higher_is_better)[:limit]
    q.players(ctx, [line.basic.player_id for _, _, _, line in ordered])
    team_index = q.all_teams(ctx)

    rows: list[dict[str, Any]] = []
    for rank, (value, baseline, delta, line) in enumerate(ordered, start=1):
        # The client's ``DailyMoverRow.player`` is non-optional, so a line whose player row
        # is missing is dropped rather than published as a null that fails to decode.
        player_ref = q.player_ref_dict_for_team(
            ctx, line.basic.player_id, line.basic.team_id
        )
        if player_ref is None:
            continue
        opponent = team_index.get(line.opponent_id)
        rows.append(
            {
                "rank": rank,
                "player": player_ref,
                "gameId": line.game.game_id,
                "opponentAbbr": opponent.abbr if opponent is not None else None,
                "isHome": line.is_home,
                "result": line.result,
                "line": stat_line(line.values(LINE_METRICS)),
                # ``leagueAverage`` and ``delta`` stay null on the MetricValue: the contract
                # defines them as the *league* comparison, and this row's comparison is
                # against the player's own baseline, which has its own two fields below.
                "value": q.metric_value_dict(
                    metric_key,
                    value,
                    rank=rank,
                    season=line.game.season,
                    granularity="game",
                ),
                "seasonAverage": baseline,
                "delta": delta,
            }
        )

    return _payload(day, metric_key, direction, rows), availability, notes


def _payload(
    day: date_type, metric_key: str, direction: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "metric": q.metric_descriptor_dict(metric_key),
        "direction": direction,
        "rows": rows,
    }


def _order(
    candidates: list[tuple[float, Optional[float], Optional[float], Any]],
    direction: str,
    higher_is_better: bool,
) -> list[tuple[float, Optional[float], Optional[float], Any]]:
    """Sort the slate for the requested direction.

    ``surprise`` needs a baseline to measure against, so a player in his first game of the
    season — no season average yet — is left out of that ranking rather than credited with an
    infinite gap.
    """
    if direction == "surprise":
        with_baseline = [entry for entry in candidates if entry[2] is not None]
        return sorted(with_baseline, key=lambda entry: abs(float(entry[2])), reverse=True)
    best_first = higher_is_better if direction == "best" else not higher_is_better
    return sorted(candidates, key=lambda entry: entry[0], reverse=best_first)


def _slate_season(games: list[Any], ctx: ResolveContext) -> str:
    """The season the slate belongs to — every game on one night shares it."""
    for game in games:
        if game.season:
            return str(game.season)
    return ctx.season
