"""Defence by opponent position, on the EuroLeague's three positions (and, opt-in, the workbook's five).

What this answers, and what it does not
---------------------------------------
"How many points does this club let guards, forwards and centers score?" It counts the points
scored by opposing players *registered at* a position. It does not say who guarded whom: a center
who scores from the perimeter against a guard is a point allowed to centers. Every payload
carries the shared caveat saying so. The arithmetic (exact allocation, reconciliation to the
score, empirical-Bayes shrinkage, the coverage, sample and reliability gates, Bonferroni bands) is
:mod:`nbastats.shared.defense_position`; this module's job is the EuroLeague's *position source*
and the loading, and they are where the honesty is decided.

Where a player's position comes from (design section 7.2)
---------------------------------------------------------
One basis per player-season, never per game, in this order:

1. **The registration** (``el_registration.position_code`` for the season and the club he
   played for that night): 1 guard, 2 forward, 3 center. Official.
2. **The box score's own listing** (``el_player_game.position_code_at_game``), when the
   registration has none. Also official, and recorded the same night.
3. **Unknown.** Never guessed: unknown points are their own bucket, counted, capped (5% of a
   club's points allowed, or of the league's, withholds every index) and never redistributed.

The workbook author's five-way label (``PG``, ``SG``, ``SF``, ``PF``, ``C``) is an *estimate*, not
the league's data. It is used for the three-way scheme **only when the store holds no official
registration at all** (a store built from the workbook alone), never to fill a gap for an
individual player, because mixing the two inside one store would make the estimated share depend
on which players the registry happened to omit. Whenever it is used the payload says
``availability: "estimated"`` and ``coverage.workbookListing`` says how much of the number rests
on it. The five-way scheme (``scheme=workbook5``) is opt-in, always estimated, and says why:
"positions assigned by the workbook author; the EuroLeague registers only Guard, Forward and
Center".

The window and the cut-off
--------------------------
``window = 0`` is the season to date; ``window = N`` is each club's last ``N`` reconciled games,
and the league reference is the whole season either way. A matchup for a particular game passes a
``before`` cut-off (the game's tip-off), and the table is computed from only the games that had
started by then: the defence a matchup shows is the defence the clubs had going into the game,
not the defence they went on to have.

Only games in the scoring scope count (final, box score passed its invariants), and a game whose
buckets do not add up to the opponent's score is excluded and counted in
``reconciliation.unreconciledGames``, never patched. The buckets add up, across positions, to the
headline points allowed per game, and the payload prints that identity so a reader can check it.

Computed on read
----------------
A season holds about 9,000 player lines, so the per-game allocation is computed from the lines
and memoised per ``(sync version, settings, ...)`` (:func:`~nbastats.euroleague.read.queries.
memoise`); the table (shrinkage and bands, which need every club) is rebuilt from the memoised
allocations for each distinct window, phase set and cut-off.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Sequence

from sqlalchemy import select

from ...shared.defense_position import (
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
from ...shared.positions import (
    SCHEME_GFC,
    SCHEME_WORKBOOK5,
    SCHEMES,
    resolve_euroleague_position,
)
from ..models import ElPlayerGame
from ..profile import PROFILE
from .queries import ReadContext, aware, bad_request, counts_for_scoring, game_start, memoise

__all__ = [
    "BASES",
    "NOTE_SCOPE",
    "NOTE_WORKBOOK5",
    "validate_scheme",
    "validate_basis",
    "validate_window",
    "build_table",
    "team_defense",
    "defense_availability",
    "defense_payload",
    "defense_table_payload",
    "summary_for_club",
]

BASES: Final[tuple[str, ...]] = (BASIS_PER_GAME, BASIS_PER_MINUTE)

#: Every EuroLeague payload says what its scope is: EuroLeague games only.
NOTE_SCOPE: Final = (
    "EuroLeague games only: friendlies, domestic leagues and national-team games are not tracked."
)
NOTE_WORKBOOK5: Final = (
    "Positions assigned by the workbook author; the EuroLeague registers only Guard, Forward and "
    "Center."
)
NOTE_WORKBOOK_LISTING: Final = (
    "This store holds no official registrations, so positions are the workbook author's labels, "
    "folded to Guard, Forward and Center. They are an estimate."
)


# --------------------------------------------------------------------------- parameters


def validate_scheme(value: str | None) -> str:
    scheme = (value or SCHEME_GFC).strip()
    if scheme not in SCHEMES:
        raise bad_request(f"{scheme!r} is not a scheme; expected one of {list(SCHEMES)}.", "scheme")
    return scheme


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


# --------------------------------------------------------------------------- allocation


def _scheme_source(ctx: ReadContext, scheme: str) -> str:
    if scheme == SCHEME_WORKBOOK5:
        return "the workbook author's five-way labels (estimated)"
    if ctx.has_official_registrations:
        return (
            "the EuroLeague registration, and the box score's own listing where the registration "
            "has none"
        )
    return "the workbook author's labels folded to three positions (estimated)"


def _allocate(ctx: ReadContext, scheme: str) -> dict[str, Any]:
    """Allocate every counting game for both defenders. Plain data, safe to memoise."""
    games = {g.game_id: g for g in ctx.games if counts_for_scoring(g)}
    by_side: dict[tuple[str, str], list[ElPlayerGame]] = {}
    if games:
        rows = ctx.session.execute(
            select(ElPlayerGame).where(
                ElPlayerGame.game_id.in_(list(games)), ElPlayerGame.participation == "played"
            )
        ).scalars()
        for row in rows:
            by_side.setdefault((row.game_id, row.club_code), []).append(row)
    official = ctx.has_official_registrations
    registrations = ctx.registrations
    allocations: dict[str, list[GameAllocation]] = {club: [] for club in ctx.clubs}
    for game in games.values():
        for defender, attacker, opp_pts in (
            (game.home_club_code, game.away_club_code, game.away_pts),
            (game.away_club_code, game.home_club_code, game.home_pts),
        ):
            lines: list[OpponentLine] = []
            for row in by_side.get((game.game_id, attacker), ()):
                reg = registrations.get((attacker, row.person_code))
                resolution = resolve_euroleague_position(
                    registration_code=reg.position_code if reg is not None else None,
                    box_score_code=row.position_code_at_game,
                    workbook_label=reg.position5_workbook if reg is not None else None,
                    scheme=scheme,
                    has_official_registrations=official,
                )
                lines.append(
                    OpponentLine(
                        minutes=(
                            (row.seconds_played / 60.0) if row.seconds_played is not None else None
                        ),
                        pts=row.pts,
                        weights=resolution.weights,
                        basis=resolution.basis,
                        fga=(
                            (row.fga2 + row.fga3)
                            if row.fga2 is not None and row.fga3 is not None
                            else None
                        ),
                        fta=row.fta,
                        fg3a=row.fga3,
                    )
                )
            allocations.setdefault(defender, []).append(
                allocate_game(
                    game_id=game.game_id,
                    date=game.game_date,
                    team=defender,
                    opp_pts=opp_pts,
                    lines=lines,
                    scheme=scheme,
                )
            )
    return {
        "allocations": allocations,
        "phase": {g.game_id: g.phase_code for g in games.values()},
        "start": {g.game_id: game_start(g) for g in games.values()},
    }


def build_table(
    ctx: ReadContext,
    *,
    scheme: str = SCHEME_GFC,
    basis: str = BASIS_PER_GAME,
    window: int = 0,
    phases: Sequence[str] | None = None,
    before: datetime | None = None,
) -> DefenseTable:
    """Every club's defence by position, computed together (the shrinkage needs them all)."""
    memo = memoise(ctx, "defense-allocation", scheme, lambda: _allocate(ctx, scheme))
    keep = None
    if phases or before is not None:
        cutoff = aware(before) if before is not None else None
        wanted = set(phases) if phases else None

        def keep(game_id: str) -> bool:  # type: ignore[misc]
            if wanted is not None and memo["phase"][game_id] not in wanted:
                return False
            return cutoff is None or memo["start"][game_id] < cutoff

    team_games: dict[str, list[GameAllocation]] = {}
    for club in ctx.clubs:
        rows = memo["allocations"].get(club, [])
        team_games[club] = [r for r in rows if keep is None or keep(r.game_id)]
    return compute_defense(
        team_games,
        profile=PROFILE,
        scheme=scheme,
        basis=basis,
        window=window,
        coverage_ceiling=ctx.setting("positionCoverageCeiling"),
        position_source=_scheme_source(ctx, scheme),
        league_team_count=len(ctx.clubs),
    )


def team_defense(table: DefenseTable, club: str) -> TeamDefense:
    return table.team(club)


# --------------------------------------------------------------------------- payloads


def defense_availability(
    ctx: ReadContext, scheme: str, table: DefenseTable, team: TeamDefense | None
) -> str:
    """``full``, ``partial``, ``estimated`` or ``unavailable`` for a defence payload.

    ``estimated`` whenever any of the number rests on the workbook author's labels; otherwise
    ``unavailable`` with nothing to show, ``partial`` when a game was unreconciled or some points
    had no listed position, else ``full``.
    """
    if table.league.team_games == 0 or (team is not None and team.games == 0):
        return "unavailable"
    if scheme == SCHEME_WORKBOOK5 or not ctx.has_official_registrations:
        return "estimated"
    if team is not None:
        workbook = team.coverage.workbook_listing or 0.0
        if workbook > 0:
            return "estimated"
        unknown = team.coverage.unknown or 0.0
        return "partial" if (team.unreconciled_games or unknown > 0) else "full"
    return "full"


def _notes(ctx: ReadContext, scheme: str) -> list[str]:
    notes = [NOTE_SCOPE]
    if scheme == SCHEME_WORKBOOK5:
        notes.append(NOTE_WORKBOOK5)
    elif not ctx.has_official_registrations:
        notes.append(NOTE_WORKBOOK_LISTING)
    return notes


def _head(
    ctx: ReadContext, table: DefenseTable, freshness: dict[str, Any], phases: Sequence[str] | None
) -> dict[str, Any]:
    return {
        "league": "euroleague",
        "season": ctx.season_label,
        "seasonCode": ctx.season_code,
        "phase": list(phases) if phases else None,
        "scheme": table.scheme,
        "basis": table.basis,
        "regulationMinutes": PROFILE.regulation_minutes,
        "freshness": freshness,
    }


def defense_payload(
    ctx: ReadContext,
    club: str,
    *,
    scheme: str,
    basis: str,
    window: int,
    phases: Sequence[str] | None,
    freshness: dict[str, Any],
    before: datetime | None = None,
) -> dict[str, Any]:
    """``DefenseByPosition`` for one club."""
    table = build_table(
        ctx, scheme=scheme, basis=basis, window=window, phases=phases, before=before
    )
    team = table.team(club)
    payload = _head(ctx, table, freshness, phases)
    payload["team"] = ctx.team_ref(club)
    payload.update(team.to_payload())
    payload["method"] = table.method.to_payload()
    payload["methodMessage"] = table.league_signal_message
    payload["availability"] = defense_availability(ctx, scheme, table, team)
    payload["caveat"] = CAVEAT
    payload["notes"] = _notes(ctx, scheme)
    return payload


def defense_table_payload(
    ctx: ReadContext,
    *,
    scheme: str,
    basis: str,
    window: int,
    phases: Sequence[str] | None,
    freshness: dict[str, Any],
) -> dict[str, Any]:
    """``DefenseByPositionTable``: every club, sorted by points allowed (a recorded fact, not a rank)."""
    table = build_table(ctx, scheme=scheme, basis=basis, window=window, phases=phases)
    payload = _head(ctx, table, freshness, phases)
    payload["leaguePointsAllowedPerGame"] = table.league.points_allowed_per_game
    payload["window"] = {
        "kind": "season" if window == 0 else "lastGames",
        "requested": window or None,
    }
    payload["teams"] = [
        {"team": ctx.team_ref(str(t.team)), **t.to_table_row()} for t in table.teams
    ]
    payload["method"] = table.method.to_payload()
    payload["methodMessage"] = table.league_signal_message
    payload["availability"] = defense_availability(ctx, scheme, table, None)
    payload["caveat"] = CAVEAT
    payload["notes"] = _notes(ctx, scheme)
    return payload


def summary_for_club(table: DefenseTable, club: str) -> dict[str, Any] | None:
    """The ``defenseSummary`` of a matchup for ``club`` (``None`` for a club not in the table)."""
    try:
        return table.team(club).to_summary()
    except KeyError:
        return None
