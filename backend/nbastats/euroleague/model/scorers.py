"""Round scorers: who is projected to score, and how much, without a number to compare it with.

The workbook's "R3 Scorers" sheet projects each club's best scorers for a round. The sheet is
also where its gambling machinery lived (a typed price per player, a probability of going over
it, a lean, and hand-picked "picks" to put those against), and none of that is here. What is kept
is the projection itself, the context behind it and the top few players per club by it.

The projection, cell by cell
----------------------------
For a player ``j`` of club ``C`` playing opponent ``O`` at home (``side = +1``) or away
(``side = -1``)::

    context X      = D_O + side * h / (2 * pf_C)                           (R3 Scorers ``X``)
    model points   = (p_j * c_j * boost) * X                               (``Y``)
    form           = mean points over his last <= 10 games with minutes   (``N``)
    projection     = 0                                   if c_j = 0
                   = model                               if there is no form
                   = (1 - w) * model + w * form * c_j    otherwise         (``P``)

``p_j * c_j * boost`` is the injury layer's projected points for him; ``D_O`` is the opponent's
defence index (above 1 *allows* more than average, so a scorer facing it is projected up);
``h`` is the game's home advantage and ``pf_C`` the club's rating, so the venue term is the
share of the club's scoring that half the home advantage moves; ``w`` is ``formWeight`` (0.25).
A neutral venue has ``h = 0`` and so no venue term.

One documented deviation: **the form is made of EuroLeague games only**. The workbook's form
reads its "Game Logs" sheet, which mixes friendlies, national-team qualifiers and domestic
games from sources whose terms nobody has reviewed; none of that is imported, so form is the
mean over the player's last ten official games *in the store* before the cut-off, and the
payload carries ``formGames`` (how many that was) so a player with two games of form is
visibly a player with two. A player with no game on record has ``formAverage: null``, and his
projection is the model alone.

What is not here
----------------
No spread for a player's points (the workbook's ``PSDBase`` and ``PSDSlope`` exist only to turn
a projection into a probability of beating a typed number), no "picks" (the workbook's
manual overrule of the model's order, there to feed the comparison), and no rank of the model's
choice against the user's. The output is the top few players per club by projection, ties broken
by the player's code so the list never reshuffles for nothing.

Pure over the objects the projection produced: the only database read is the form index.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import select

from ..models import ElPlayerGame
from ..read.queries import ReadContext, aware, counts_for_scoring, game_start
from .round_projection import ProjectionResult, SideResult

__all__ = [
    "FORM_GAMES",
    "ScorerLine",
    "form_index",
    "context_factor",
    "club_scorers",
    "game_scorers",
]

#: How many recent games a scorer's form averages over.
FORM_GAMES: Final = 10


@dataclass(frozen=True)
class ScorerLine:
    """One player's projection for one game."""

    person_code: str
    status: str | None
    chance: float
    model_points: float
    form_average: float | None
    form_games: int
    projected_points: float


def form_index(ctx: ReadContext) -> dict[str, list[tuple[datetime, int]]]:
    """Each player's ``(game start, points)`` for played games with minutes, oldest first.

    Games count when they are in the scoring scope (final, box score passed). A line with no
    recorded points is left out (not a zero), and so is a line with no minutes.
    """
    cached = ctx.cache.get("form_index")
    if cached is not None:
        return cached
    starts = {g.game_id: game_start(g) for g in ctx.games if counts_for_scoring(g)}
    index: dict[str, list[tuple[datetime, int]]] = {}
    if starts:
        rows = ctx.session.execute(
            select(ElPlayerGame.person_code, ElPlayerGame.game_id, ElPlayerGame.pts).where(
                ElPlayerGame.participation == "played",
                ElPlayerGame.seconds_played > 0,
                ElPlayerGame.pts.is_not(None),
                ElPlayerGame.game_id.in_(list(starts)),
            )
        )
        for person, game_id, pts in rows:
            index.setdefault(person, []).append((starts[game_id], pts))
    for values in index.values():
        values.sort()
    ctx.cache["form_index"] = index
    return index


def _form(
    index: dict[str, list[tuple[datetime, int]]], person: str, before: datetime
) -> tuple[float | None, int]:
    cutoff = aware(before)
    recent = [pts for start, pts in index.get(person, ()) if start < cutoff][-FORM_GAMES:]  # type: ignore[operator]
    if not recent:
        return None, 0
    return math.fsum(recent) / len(recent), len(recent)


def context_factor(
    opponent_defence: float, side: int, home_advantage: float, own_pf: float
) -> float:
    """``D_O + side * h / (2 * pf_C)`` (R3 Scorers ``X``)."""
    if own_pf <= 0:
        raise ValueError("a club's rating must be positive to scale a venue term by it")
    return opponent_defence + side * home_advantage / (2.0 * own_pf)


def club_scorers(
    ctx: ReadContext,
    own: SideResult,
    opponent: SideResult,
    *,
    side: int,
    home_advantage: float,
    as_of: datetime,
    per_club: int,
) -> list[ScorerLine]:
    """The top ``per_club`` scorers of ``own`` against ``opponent``, best first."""
    weight = ctx.setting("formWeight")
    factor = context_factor(opponent.defence, side, home_advantage, own.pf)
    index = form_index(ctx)
    lines: list[ScorerLine] = []
    for entry in own.squad:
        model = entry.outcome.projected_points * factor
        form, games = _form(index, entry.person_code, as_of)
        if entry.chance == 0:
            projected = 0.0
        elif form is None:
            projected = model
        else:
            projected = (1 - weight) * model + weight * form * entry.chance
        lines.append(
            ScorerLine(
                person_code=entry.person_code,
                status=entry.status,
                chance=entry.chance,
                model_points=model,
                form_average=form,
                form_games=games,
                projected_points=projected,
            )
        )
    lines.sort(key=lambda s: (-s.projected_points, s.person_code))
    return lines[: max(0, per_club)]


def game_scorers(
    ctx: ReadContext, result: ProjectionResult, *, per_club: int
) -> tuple[list[ScorerLine], list[ScorerLine]]:
    """``(home scorers, away scorers)`` for one projected game."""
    h = result.home_advantage.points
    home = club_scorers(
        ctx,
        result.home,
        result.away,
        side=1,
        home_advantage=h,
        as_of=result.as_of,
        per_club=per_club,
    )
    away = club_scorers(
        ctx,
        result.away,
        result.home,
        side=-1,
        home_advantage=h,
        as_of=result.as_of,
        per_club=per_club,
    )
    return home, away
