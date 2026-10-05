"""Defence by opponent position, on the NBA's three published positions.

What this answers, and what it does not
---------------------------------------
"How many points does this team let guards, forwards and centers score?" It counts the points
scored by opposing players *listed at* a position for the season. It does not say who guarded
whom: a center who scores from the perimeter against a guard is a point allowed to centers. Every
payload carries the shared caveat saying so. The arithmetic (exact allocation, reconciliation to
the score, empirical-Bayes shrinkage, the coverage, sample and reliability gates, Bonferroni
bands) is :mod:`nbastats.shared.defense_position`; this module's job is the NBA's *position
source* and the loading, and they are where the honesty is decided.

Where a player's position comes from (design section 7.2)
---------------------------------------------------------
One basis per player-season, never per game: the ``player_position_season`` row for the player
and the season, whose three weights (guard, forward, center) are either all unknown or sum to
one. The weights were normalised by :mod:`nbastats.shared.positions` when the row was written,
so ``G-F`` is half guard and half forward here exactly as it was when the roster was read.

Three things are deliberately **not** used, and each one is a bias the design closed:

* ``players.position`` and the box score's start slot. A starter slot exists only for nights the
  live watcher ran (so a bucket would depend on whether a laptop was awake) and it is a
  lineup-card label that a bench player never receives.
* ``queries._matches_position``, which counts a hybrid in both of his positions.
* A guess for a player with no row. He is the ``unknown`` bucket: shown, counted, capped (5% of
  a team's points allowed, or of the league's, withholds every index) and never redistributed.
  That is the honest "starter-only gap": a bench player nobody listed is not assigned a position.

The window and the cut-off
--------------------------
``window = 0`` is the season to date; ``window = N`` is each team's last ``N`` reconciled games,
and the league reference is the whole season either way. A matchup for a particular game passes a
``before`` cut-off (the game's start) and the table is computed from only the games whose result
was known by then (an earlier Eastern game day; ``queries.result_known_by``): the defence a
matchup shows is the defence the teams had going into the game, not the defence they went on to
have.

A game counts when it is final in the requested season type. A game whose buckets do not add up
to the opponent's recorded score (a box score missing a player, a minutes column the era did not
record) is excluded and counted in ``reconciliation.unreconciledGames``, never patched. The
buckets add up, across positions, to the headline points allowed per game, and the payload prints
the identity so a reader can check it.

Computed on read, memoised
--------------------------
A season holds about thirty thousand opposing lines, so the per-game allocation is computed from
the lines and memoised per ``(data marks, season type)``
(:func:`~nbastats.nba_matchup.queries.memoise`); the table (shrinkage and bands, which need every
team) is rebuilt from the memoised allocations for each distinct window and cut-off.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from sqlalchemy import select

from ..models import Game, PlayerGameBasic, TeamGame
from ..shared.defense_position import (
    BASIS_PER_GAME,
    BASIS_PER_MINUTE,
    CAVEAT,
    DefenseTable,
    GameAllocation,
    OpponentLine,
    TeamDefense,
    allocate_game,
    compute_defense,
)
from ..shared.positions import BASIS_LISTED, SCHEME_GFC, weights_from_columns
from .queries import (
    PROFILE,
    ReadContext,
    bad_request,
    game_has_result,
    memoise,
    result_day,
)

__all__ = [
    "BASES",
    "NOTE_NO_LISTINGS",
    "validate_basis",
    "validate_window",
    "position_source",
    "build_table",
    "defense_availability",
    "defense_payload",
    "defense_table_payload",
    "summary_for_team",
]

BASES: Final[tuple[str, ...]] = (BASIS_PER_GAME, BASIS_PER_MINUTE)

NOTE_NO_LISTINGS: Final = (
    "No listed positions are recorded for this season, so every point is of unknown position and "
    "no index can be shown. Fetch the team rosters (the nba.rosters job) to list them."
)

_SOURCE_LABELS: Final[dict[str, str]] = {
    "commonTeamRoster": "the league's team rosters",
    "kaggleCurrent": "a bulk-file snapshot of current listings",
    "seedArchetype": "the demo league's own labels",
}


# --------------------------------------------------------------------------- parameters


def validate_basis(value: str | None) -> str:
    basis = (value or BASIS_PER_GAME).strip()
    if basis not in BASES:
        raise bad_request(f"{basis!r} is not a basis; expected one of {list(BASES)}.", "basis")
    return basis


def validate_window(value: int | None) -> int:
    window = 0 if value is None else value
    if window < 0:
        raise bad_request("window must be 0 (the season) or a positive number of games.", "window")
    return window


def position_source(ctx: ReadContext) -> str | None:
    """Where the listings behind this table came from, in words, or ``None`` with no listings."""
    sources = sorted({row.source for row in ctx.positions.values()})
    if not sources:
        return None
    named = ", ".join(_SOURCE_LABELS.get(source, source) for source in sources)
    return f"Each player's listed position for the season, from {named}."


# --------------------------------------------------------------------------- allocation


def _allocate(ctx: ReadContext) -> dict[str, Any]:
    """Allocate every counting game for both defenders. Plain data, safe to memoise."""
    kind = ctx.season_type
    games = {g.game_id: g for g in ctx.games if g.season_type == kind and game_has_result(g)}
    session = ctx.session
    lines: dict[tuple[str, int], list[Any]] = {}
    opp_points: dict[tuple[str, int], int | None] = {}
    if games:
        for row in session.execute(
            select(
                PlayerGameBasic.game_id,
                PlayerGameBasic.team_id,
                PlayerGameBasic.player_id,
                PlayerGameBasic.minutes,
                PlayerGameBasic.pts,
                PlayerGameBasic.fga,
                PlayerGameBasic.fta,
                PlayerGameBasic.fg3a,
            )
            .join(Game, Game.game_id == PlayerGameBasic.game_id)
            .where(Game.season == ctx.season, Game.season_type == kind, Game.status == "final")
        ):
            lines.setdefault((row.game_id, row.team_id), []).append(row)
        for row in session.execute(
            select(TeamGame.game_id, TeamGame.team_id, TeamGame.opp_pts)
            .join(Game, Game.game_id == TeamGame.game_id)
            .where(Game.season == ctx.season, Game.season_type == kind, Game.status == "final")
        ):
            opp_points[(row.game_id, row.team_id)] = row.opp_pts
    weights = {
        pid: weights_from_columns(row.g_weight, row.f_weight, row.c_weight)
        for pid, row in ctx.positions.items()
    }
    allocations: dict[int, list[GameAllocation]] = {}
    for game in games.values():
        for defender, attacker, attacker_score in (
            (game.home_team_id, game.away_team_id, game.away_pts),
            (game.away_team_id, game.home_team_id, game.home_pts),
        ):
            opp_pts = opp_points.get((game.game_id, defender))
            if opp_pts is None:
                opp_pts = attacker_score
            opposing = [
                OpponentLine(
                    minutes=row.minutes,
                    pts=row.pts,
                    weights=weights.get(row.player_id),
                    basis=BASIS_LISTED,
                    fga=row.fga,
                    fta=row.fta,
                    fg3a=row.fg3a,
                )
                for row in lines.get((game.game_id, attacker), ())
            ]
            allocations.setdefault(defender, []).append(
                allocate_game(
                    game_id=game.game_id,
                    date=game.game_date,
                    team=defender,
                    opp_pts=opp_pts,
                    lines=opposing,
                    scheme=SCHEME_GFC,
                )
            )
    return {
        "allocations": allocations,
        # the Eastern game day: a result informs only cut-offs on a later day
        "known": {g.game_id: g.game_date for g in games.values()},
    }


def build_table(
    ctx: ReadContext,
    *,
    basis: str = BASIS_PER_GAME,
    window: int = 0,
    before: datetime | None = None,
    include: int | None = None,
) -> DefenseTable:
    """Every team's defence by position, computed together (the shrinkage needs them all).

    ``include`` names a team to add with no games when it has none in the scope, so a team that
    did not play the season gets an honest empty answer rather than a ``KeyError``.
    """
    memo = memoise(ctx, "nba-defense-allocation", ctx.season_type, lambda: _allocate(ctx))
    cutoff_day = result_day(before) if before is not None else None
    team_games: dict[int, list[GameAllocation]] = {}
    for team, rows in memo["allocations"].items():
        team_games[team] = [
            r for r in rows if cutoff_day is None or memo["known"][r.game_id] < cutoff_day
        ]
    if include is not None and include not in team_games:
        team_games[include] = []
    return compute_defense(
        team_games,
        profile=PROFILE,
        scheme=SCHEME_GFC,
        basis=basis,
        window=window,
        coverage_ceiling=ctx.setting("positionCoverageCeiling"),
        position_source=position_source(ctx),
    )


# --------------------------------------------------------------------------- payloads


def defense_availability(table: DefenseTable, team: TeamDefense | None) -> str:
    """``full``, ``partial`` or ``unavailable`` for a defence payload.

    ``unavailable`` with nothing to show; ``partial`` when a game was unreconciled or some points
    had no listed position (the numbers are right for what they cover and say how much that is);
    ``full`` otherwise.
    """
    if table.league.team_games == 0 or (team is not None and team.games == 0):
        return "unavailable"
    if team is not None:
        unknown = team.coverage.unknown or 0.0
        return "partial" if (team.unreconciled_games or unknown > 0) else "full"
    league_unknown = table.league.unknown_share or 0.0
    unreconciled = any(t.unreconciled_games for t in table.teams)
    return "partial" if (unreconciled or league_unknown > 0) else "full"


def _notes(ctx: ReadContext) -> list[str]:
    notes = [f"{ctx.season_type} games of {ctx.season} only."]
    if not ctx.positions:
        notes.append(NOTE_NO_LISTINGS)
    return notes


def _head(ctx: ReadContext, table: DefenseTable, freshness: dict[str, Any]) -> dict[str, Any]:
    return {
        "league": "nba",
        "season": ctx.season,
        "seasonType": ctx.season_type,
        "scheme": table.scheme,
        "basis": table.basis,
        "regulationMinutes": PROFILE.regulation_minutes,
        "freshness": freshness,
    }


def defense_payload(
    ctx: ReadContext,
    team_id: int,
    *,
    basis: str,
    window: int,
    freshness: dict[str, Any],
    before: datetime | None = None,
) -> dict[str, Any]:
    """``DefenseByPosition`` for one team."""
    table = build_table(ctx, basis=basis, window=window, before=before, include=team_id)
    team = table.team(team_id)
    payload = _head(ctx, table, freshness)
    payload["team"] = ctx.team_ref(team_id)
    payload.update(team.to_payload())
    payload["method"] = table.method.to_payload()
    payload["methodMessage"] = table.league_signal_message
    payload["availability"] = defense_availability(table, team)
    payload["caveat"] = CAVEAT
    payload["notes"] = _notes(ctx)
    return payload


def defense_table_payload(
    ctx: ReadContext,
    *,
    basis: str,
    window: int,
    freshness: dict[str, Any],
) -> dict[str, Any]:
    """``DefenseByPositionTable``: every team, sorted by points allowed (a fact, not a rank)."""
    table = build_table(ctx, basis=basis, window=window)
    payload = _head(ctx, table, freshness)
    payload["leaguePointsAllowedPerGame"] = table.league.points_allowed_per_game
    payload["window"] = {
        "kind": "season" if window == 0 else "lastGames",
        "requested": window or None,
    }
    payload["teams"] = [
        {"team": ctx.team_ref(int(t.team)), **t.to_table_row()} for t in table.teams
    ]
    payload["method"] = table.method.to_payload()
    payload["methodMessage"] = table.league_signal_message
    payload["availability"] = defense_availability(table, None)
    payload["caveat"] = CAVEAT
    payload["notes"] = _notes(ctx)
    return payload


def summary_for_team(table: DefenseTable, team_id: int) -> dict[str, Any] | None:
    """The ``defenseSummary`` of a matchup for one team (``None`` for a team not in the table)."""
    try:
        return table.team(team_id).to_summary()
    except KeyError:
        return None
