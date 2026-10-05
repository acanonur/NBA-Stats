"""Player statistics, computed on read, with the null rule applied column by column.

A EuroLeague season is about 9,000 player lines, so nothing here is stored: a player's season is
summed from his lines when asked, and the sums are memoised per data version
(:func:`~nbastats.euroleague.read.queries.memoise`). What is written down here is how a season is
summed *honestly*.

The per-column divisor rule (design section 3.C)
------------------------------------------------
A per-game average of a stat is ``sum / count of lines that carry that stat``, never ``sum /
games played``. The workbook's box scores carry no fouls drawn, no blocks against and no
plus/minus, while a live game carries them; a player's season built from both must not average
fouls drawn over every game, which would drag it toward zero by the games that *could not*
record it. Each stat therefore has its own count, exposed beside the value as
``gamesWithStat``. A stat no line carries is ``null``, rendered as an em dash, never 0.

The same discipline gives the other two modes: ``Totals`` is the sum over the lines that carry it,
and ``Per40`` is forty times the sum over the minutes of *those* lines (a line with the stat but
no recorded minutes contributes to neither). A percentage is made over attempts, so it is ``null``
when there are none: a player who never shot a three has no three-point percentage, not 0%.

``dnp`` lines (a player listed but not used) have every stat ``null`` and are not "games played";
they never enter an average, and they do not count in ``games``.

Metric keys are the catalogue's (``fg3m``, ``fg3a``, ``pir``; ``contracts/metrics.json`` under
``leagueMetrics.euroleague``), not the store's column names (``fgm3``): the clients format by key.
``pir`` is the official PIR as published; it is stored, never recomputed, and where fouls drawn or
blocks against are not recorded Hardwood could not recompute it anyway.

Sorting is descending by the chosen metric with ``null`` last and ties by name, so the order is
stable; it is a sort of recorded facts, not a ranking, and no rank field is emitted.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select

from ...api.errors import ApiError
from ...shared import refs
from ...shared.availability import display_chance
from ..models import ElPlayerGame
from ..profile import ESTIMATE_BASIS_LABEL, PROFILE
from .availability import get_book, source_of
from .queries import ReadContext, bad_request, counts_for_scoring, game_start, memoise

__all__ = [
    "PER_MODES",
    "METRIC_COLUMNS",
    "PERCENTAGES",
    "RATE_KEYS_FOR_CLUB_VIEW",
    "line_payload",
    "PlayerAggregate",
    "aggregate_players",
    "season_values",
    "build_stats_table",
    "build_player_detail",
    "build_player_gamelog",
]

PER_MODES: Final[tuple[str, ...]] = PROFILE.per_modes

#: Catalogue metric key to the store column it sums (``min`` is seconds, handled apart).
METRIC_COLUMNS: Final[dict[str, str]] = {
    "pts": "pts",
    "reb": "reb",
    "oreb": "oreb",
    "dreb": "dreb",
    "ast": "ast",
    "stl": "stl",
    "blk": "blk",
    "tov": "tov",
    "pf": "pf",
    "fgm2": "fgm2",
    "fga2": "fga2",
    "fg3m": "fgm3",
    "fg3a": "fga3",
    "ftm": "ftm",
    "fta": "fta",
    "blk_against": "blk_against",
    "fouls_drawn": "fouls_drawn",
    "plus_minus": "plus_minus",
    "pir": "pir_official",
}
#: Percentage metric to (makes column, attempts column).
PERCENTAGES: Final[dict[str, tuple[str, str]]] = {
    "fg2_pct": ("fgm2", "fga2"),
    "fg3_pct": ("fgm3", "fga3"),
    "ft_pct": ("ftm", "fta"),
}
_ALL_KEYS: Final[tuple[str, ...]] = ("min", *METRIC_COLUMNS, *PERCENTAGES)
#: The per-40 rates a club view shows from the model (not from box scores).
RATE_KEYS_FOR_CLUB_VIEW: Final[tuple[str, ...]] = ("pts", "reb", "ast", "fg3m", "stl", "blk", "tov")


def _pct(made: int | None, attempts: int | None) -> float | None:
    """A percentage as a fraction; ``None`` for no attempts or an unrecorded count."""
    if made is None or attempts is None or attempts <= 0:
        return None
    return made / attempts


def line_payload(row: Any) -> dict[str, Any]:
    """``ElLine`` for a player or team row: minutes from seconds, percentages from makes."""
    seconds = row.seconds_played
    return {
        "minutes": None if seconds is None else seconds / 60.0,
        "pts": row.pts,
        "fgm2": row.fgm2,
        "fga2": row.fga2,
        "fg2Pct": _pct(row.fgm2, row.fga2),
        "fgm3": row.fgm3,
        "fga3": row.fga3,
        "fg3Pct": _pct(row.fgm3, row.fga3),
        "ftm": row.ftm,
        "fta": row.fta,
        "ftPct": _pct(row.ftm, row.fta),
        "oreb": row.oreb,
        "dreb": row.dreb,
        "reb": row.reb,
        "ast": row.ast,
        "stl": row.stl,
        "tov": row.tov,
        "blk": row.blk,
        "blkAgainst": row.blk_against,
        "pf": row.pf,
        "foulsDrawn": row.fouls_drawn,
        "plusMinus": row.plus_minus,
        "pir": row.pir_official,
    }


# --------------------------------------------------------------------------- aggregation


@dataclass
class PlayerAggregate:
    """A player's season sums and the counts behind each stat."""

    person_code: str
    club_code: str
    last_start: Any
    games: int = 0
    seconds: int = 0
    seconds_lines: int = 0
    #: column -> (sum, lines that carry it, minutes of lines that carry it and have minutes,
    #: sum over only those minuted lines: the per-40 numerator, so a line with no recorded
    #: minutes adds to neither side of the rate)
    columns: dict[str, list[float]] = None  # type: ignore[assignment]
    #: percentage key -> (makes, attempts, lines that carry both, minutes of those lines)
    pairs: dict[str, list[float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.columns = defaultdict(lambda: [0.0, 0, 0.0, 0.0])
        self.pairs = defaultdict(lambda: [0.0, 0.0, 0, 0.0])


def _aggregate(ctx: ReadContext) -> dict[str, PlayerAggregate]:
    games = {g.game_id: g for g in ctx.games if counts_for_scoring(g)}
    out: dict[str, PlayerAggregate] = {}
    if not games:
        return out
    rows = ctx.session.execute(
        select(ElPlayerGame).where(
            ElPlayerGame.game_id.in_(list(games)), ElPlayerGame.participation == "played"
        )
    ).scalars()
    for row in sorted(rows, key=lambda r: (game_start(games[r.game_id]), r.game_id, r.person_code)):
        start = game_start(games[row.game_id])
        agg = out.get(row.person_code)
        if agg is None:
            agg = out[row.person_code] = PlayerAggregate(row.person_code, row.club_code, start)
        agg.club_code, agg.last_start = row.club_code, start  # newest line wins (rows ascend)
        agg.games += 1
        minutes = row.seconds_played / 60.0 if row.seconds_played else None
        if row.seconds_played is not None:
            agg.seconds += row.seconds_played
            agg.seconds_lines += 1
        for key, column in METRIC_COLUMNS.items():
            value = getattr(row, column)
            if value is None:
                continue
            slot = agg.columns[key]
            slot[0] += value
            slot[1] += 1
            if minutes is not None:
                slot[2] += minutes
                slot[3] += value
        for key, (makes_column, attempts_column) in PERCENTAGES.items():
            made, attempted = getattr(row, makes_column), getattr(row, attempts_column)
            if made is None or attempted is None:
                continue
            slot = agg.pairs[key]
            slot[0] += made
            slot[1] += attempted
            slot[2] += 1
            if minutes is not None:
                slot[3] += minutes
    return out


def aggregate_players(ctx: ReadContext) -> dict[str, PlayerAggregate]:
    """Every player's season sums, memoised per data version."""
    return memoise(ctx, "player-aggregate", None, lambda: _aggregate(ctx))


def season_values(
    agg: PlayerAggregate, per_mode: str
) -> tuple[dict[str, float | None], dict[str, int]]:
    """``(values, gamesWithStat)`` for one player in ``per_mode``, every key present."""
    values: dict[str, float | None] = {}
    counts: dict[str, int] = {}
    # minutes
    counts["min"] = agg.seconds_lines
    if agg.seconds_lines == 0:
        values["min"] = None
    elif per_mode == "PerGame":
        values["min"] = agg.seconds / 60.0 / agg.seconds_lines
    elif per_mode == "Totals":
        values["min"] = agg.seconds / 60.0
    else:
        values["min"] = None  # minutes per forty minutes is not a statistic
    for key in METRIC_COLUMNS:
        if key not in agg.columns:
            values[key], counts[key] = None, 0
            continue
        total, lines, minutes, minuted_total = agg.columns[key]
        counts[key] = int(lines)
        if per_mode == "PerGame":
            values[key] = total / lines
        elif per_mode == "Totals":
            values[key] = total
        else:
            values[key] = (40.0 * minuted_total / minutes) if minutes > 0 else None
    for key in PERCENTAGES:
        if key not in agg.pairs:
            values[key], counts[key] = None, 0
            continue
        made, attempted, lines, _ = agg.pairs[key]
        counts[key] = int(lines)
        values[key] = made / attempted if attempted > 0 else None
    return values, counts


# --------------------------------------------------------------------------- payloads


def _mode(value: str | None) -> str:
    mode = (value or "PerGame").strip()
    if mode not in PER_MODES:
        raise bad_request(
            f"{mode!r} is not a per mode; expected one of {list(PER_MODES)}.", "perMode"
        )
    return mode


def build_stats_table(
    ctx: ReadContext,
    *,
    per_mode: str | None,
    sort: str | None,
    club_code: str | None,
    min_games: int | None,
    limit: int | None,
    freshness: dict[str, Any],
) -> dict[str, Any]:
    """``ElPlayerStatsTable``."""
    mode = _mode(per_mode)
    key = (sort or "pts").strip()
    if key not in _ALL_KEYS:
        raise bad_request(f"{key!r} is not a statistic; expected one of {list(_ALL_KEYS)}.", "sort")
    club = ctx.require_club(club_code) if club_code else None
    floor = 1 if min_games is None else min_games
    if floor < 0:
        raise bad_request("minGames cannot be negative.", "minGames")
    size = 50 if limit is None else limit
    if not 1 <= size <= 500:
        raise bad_request("limit must be between 1 and 500.", "limit")
    rows: list[dict[str, Any]] = []
    for person, agg in aggregate_players(ctx).items():
        if agg.games < floor or (club is not None and agg.club_code != club):
            continue
        values, counts = season_values(agg, mode)
        rows.append(
            {
                "player": ctx.player_ref(person, agg.club_code),
                "team": ctx.team_ref(agg.club_code),
                "games": agg.games,
                "values": values,
                "gamesWithStat": counts,
            }
        )
    rows.sort(
        key=lambda r: (
            r["values"][key] is None,
            -(r["values"][key] or 0.0),
            r["player"]["name"].lower(),
            r["player"]["id"],
        )
    )
    return {
        "league": "euroleague",
        "season": ctx.season_label,
        "seasonCode": ctx.season_code,
        "perMode": mode,
        "sort": key,
        "freshness": freshness,
        "rows": rows[:size],
        "notes": [
            "A stat a game did not record is left out of that stat's average (see gamesWithStat), "
            "never counted as 0.",
            "PIR is the official figure as published, not recomputed.",
        ],
    }


def _person_or_404(ctx: ReadContext, person_code: str) -> Any:
    row = ctx.persons.get(person_code)
    if row is None:
        raise ApiError(
            "player_not_found", f"No EuroLeague player with code {person_code}.", http_status=404
        )
    return row


def build_player_detail(
    ctx: ReadContext, person_code: str, freshness: dict[str, Any]
) -> dict[str, Any]:
    """``ElPlayerDetail``: who he is, his season, his model rates and what is known about him."""
    from ..model.round_projection import ProjectionUnavailable, get_model

    person = _person_or_404(ctx, person_code)
    agg = aggregate_players(ctx).get(person_code)
    reg = ctx.registration_of(person_code)
    club = agg.club_code if agg is not None else (reg.club_code if reg is not None else None)
    averages = per40 = None
    if agg is not None:
        values, counts = season_values(agg, "PerGame")
        averages = {"games": agg.games, "values": values, "gamesWithStat": counts}
        per40 = season_values(agg, "Per40")[0]
    rate = None
    try:
        model = get_model(ctx)
        state = model.players.get(model.last_round, {}).get(person_code)
        if state is not None:
            rate = {
                "basis": state.basis,
                "basisNote": ESTIMATE_BASIS_LABEL if state.basis == "workbookEstimate" else None,
                "asOfRound": state.as_of_round,
                "projectedMinutes": state.minutes,
                "per40": {
                    k: state.rates.get(f"{k}40")
                    for k in ("pts", "reb", "ast", "fg3m", "stl", "blk", "tov")
                },
            }
    except ProjectionUnavailable:
        pass
    availability = None
    if club is not None:
        book = get_book(ctx)
        nxt = ctx.next_game_of(club)
        resolved = book.resolve(person_code, nxt.game_id if nxt else None, ctx.now)
        if resolved.present and resolved.row is not None:
            status = resolved.status
            availability = {
                "status": status,
                "chanceOfPlaying": display_chance(status, ctx.chance_table()),
                "inForce": resolved.in_force,
                "isStale": book.is_stale(resolved, club, ctx.now),
                "source": source_of(resolved.row),
            }
    return {
        "league": "euroleague",
        "season": ctx.season_label,
        "seasonCode": ctx.season_code,
        "freshness": freshness,
        "player": ctx.player_ref(person_code, club),
        "club": ctx.team_ref(club) if club else None,
        "registration": (
            {
                "dorsal": reg.dorsal,
                "positionCode": reg.position_code,
                "positionName": reg.position_name,
                "positionWorkbook5": reg.position5_workbook,
                "role": reg.role_workbook,
                "age": reg.age_workbook,
                "active": reg.active,
            }
            if reg is not None
            else None
        ),
        "birthDate": refs.iso_date(person.birth_date),
        "heightCm": person.height_cm,
        "weightKg": person.weight_kg,
        "countryCode": person.country_code,
        "seasonAverages": averages,
        "seasonPer40": per40,
        "rate": rate,
        "availability": availability,
        "notes": [
            "EuroLeague games only. A stat a game did not record is left out of that stat's average."
        ],
    }


def build_player_gamelog(
    ctx: ReadContext, person_code: str, limit: int | None, freshness: dict[str, Any]
) -> dict[str, Any]:
    """``ElPlayerGameLog``: every game the player has a line in, newest first (EuroLeague only)."""
    _person_or_404(ctx, person_code)
    size = 100 if limit is None else limit
    if not 1 <= size <= 500:
        raise bad_request("limit must be between 1 and 500.", "limit")
    games = {g.game_id: g for g in ctx.games if counts_for_scoring(g)}
    entries: list[tuple[Any, dict[str, Any]]] = []
    if games:
        rows = ctx.session.execute(
            select(ElPlayerGame).where(
                ElPlayerGame.person_code == person_code, ElPlayerGame.game_id.in_(list(games))
            )
        ).scalars()
        for row in rows:
            game = games[row.game_id]
            played = row.participation == "played"
            at_home = row.club_code == game.home_club_code
            own, other = (
                (game.home_pts, game.away_pts) if at_home else (game.away_pts, game.home_pts)
            )
            entries.append(
                (
                    (game_start(game), game.game_id),
                    {
                        "game": ctx.game_ref(game),
                        "club": ctx.team_ref(row.club_code),
                        # The result as the player's club lived it, in the same words a club's own
                        # ``scoring.form`` rows use, so a client prints "v OLY (H) W 94-84"
                        # without working out which side of the game he was on.
                        "opponent": ctx.team_ref(
                            game.away_club_code if at_home else game.home_club_code
                        ),
                        "isHome": at_home,
                        "isNeutral": game.is_neutral,
                        "teamScore": own,
                        "opponentScore": other,
                        "result": None if own == other else ("W" if own > other else "L"),
                        "participation": row.participation,
                        "isStarter": row.is_starter if played else None,
                        "stats": line_payload(row) if played else None,
                    },
                )
            )
    entries.sort(key=lambda item: item[0], reverse=True)
    return {
        "league": "euroleague",
        "season": ctx.season_label,
        "seasonCode": ctx.season_code,
        "freshness": freshness,
        "player": ctx.player_ref(person_code),
        "games": [entry for _, entry in entries[:size]],
        "notes": ["Official EuroLeague games only."],
    }
