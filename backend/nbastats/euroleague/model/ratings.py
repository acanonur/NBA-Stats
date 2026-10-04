"""Club ratings: the workbook's prior, moved by each result since, one game at a time.

A club's rating is a pair of points-per-game figures, what it scores (``pf``) and what it allows
(``pa``), built the way the workbook's Team Ratings sheet builds them::

    pf = L + (pf_prior - L) * (1 - r) + attack_adj          (Team Ratings ``K``)
    pa = L + (pa_prior - L) * (1 - r) + defence_adj         (Team Ratings ``L``)

``L`` is the league's average team score *last season* (the mean of the clubs' ``pf_prior``,
Settings ``C17``, fixed for the season, including the flagged estimate for a club with no
record), ``r`` the share of last season given back to the league mean (0.3), and the two
adjustments are the roster adjustment the workbook typed in plus everything the results have
moved since. :func:`nbastats.shared.team_projection.rating` is the formula; this module decides
what is fed to it.

The in-season update
--------------------
After a club's game is final its adjustments move by a weight times the miss (R2 Review
``J``/``K`` and ``M``/``O``)::

    attack_adj  += w_n * (scored  - projected_scored)
    defence_adj += w_n * (allowed - projected_allowed)

and the points above are the *regulation-scaled* actuals (see below). Three things in that are
decisions rather than arithmetic, and the first two fix faults the judges found:

* **The game number counts from the start of the season, not from the import.** ``n`` is the
  club's final games so far this season (Rounds 1 and 2 count: they are in the store as finals).
  Restarting the count at the first game after the import would give round 3 the weight round 1
  had, nearly a third too heavy. The weights are the workbook's for the rounds it states
  (``roundWeight.1`` 0.10, ``roundWeight.2`` 0.09) and ``1 / (n + 9)`` beyond, which gives round 3
  a weight of 0.0833; a weight beyond what the workbook states is flagged ``extrapolated``.
* **Only games after the import's as-of round update anything.** The imported adjustments
  already contain Rounds 1 and 2; applying their results again would double-count them.
* **The projection the miss is measured against is the one made before the game.** That is the
  ``locked`` ledger row when there is one. When there is none (the game was played before the
  workbook was imported, or the Mac was asleep at tip-off) the model *reconstructs* it from
  inputs dated strictly before tip-off, writes a ``reconstructed`` ledger row, and records
  ``update_basis = 'reconstructed'`` so the review can tell the two apart and report them
  separately. A miss measured against a projection made with the result in hand would be a lie.

Regulation-scaled actuals
-------------------------
An overtime game is forty-five minutes of scoring, and reading 98 points in 45 minutes as a
defence that collapsed would move a rating for the wrong reason. Each actual is restated as if
the game had ended after regulation: ``points * 12000 / team_seconds``, with the team time taken
from the overtime count (12,000 seconds plus 1,500 per period; an exact figure, where the
decimal minutes of a workbook box score are rounded). Switch it off with ``overtimeScaling = 0``.
This is a documented deviation from the workbook, which did not scale.

What this module is and is not
------------------------------
It holds the rating arithmetic, the loading of the imported base and the persistence of the
result (:func:`persist_book`, run by the worker job :func:`run_ratings`). The *fold*, which needs
a projection for every game and therefore the whole model, lives in
:mod:`nbastats.euroleague.model.round_projection`, because a projection needs the players as well
as the clubs. Nothing here is computed on a request that could be served from the memoised
:class:`~nbastats.euroleague.model.round_projection.Model`.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Final, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...shared.team_projection import (
    RoundWeight,
    attack_index,
    defence_index,
    rating,
    regulation_scale,
    round_weight,
    update_adjustment,
)
from ..models import ElProjectionLedger, ElTeamRating
from ..profile import PROFILE
from ..read.queries import ReadContext, counts_for_scoring, game_has_result, naive_utc

__all__ = [
    "WORKBOOK_ROUNDS",
    "ClubRating",
    "RatingUpdate",
    "RatingBook",
    "load_base_ratings",
    "league_level",
    "weight_for",
    "regulation_actual",
    "game_numbers",
    "locked_scores",
    "apply_game",
    "persist_book",
    "run_ratings",
]

_LOG = logging.getLogger(__name__)

#: The last game number the workbook states a weight for (Round 1: 0.10, Round 2: 0.09).
WORKBOOK_ROUNDS: Final = 2


@dataclass(frozen=True)
class ClubRating:
    """One club's rating as of a round."""

    club_code: str
    as_of_round: int
    pf_prior: float
    pa_prior: float
    prior_is_estimate: bool
    attack_adj: float
    defence_adj: float
    #: ``workbookImport``, ``roundUpdate``, ``manual`` or ``syntheticDemo``.
    source: str
    #: Set on a club the round's update moved; ``None`` on one carried forward unchanged.
    update_weight: float | None = None
    #: ``locked`` or ``reconstructed``: where the projection the update measured against came from.
    update_basis: str | None = None
    extrapolated: bool = False
    #: The club's game number at the latest update (0 for an imported row).
    games_counted: int = 0

    def pf(self, league_level_: float, regression: float) -> float:
        return rating(self.pf_prior, league_level_, regression, self.attack_adj)

    def pa(self, league_level_: float, regression: float) -> float:
        return rating(self.pa_prior, league_level_, regression, self.defence_adj)

    def attack(self, league_level_: float, regression: float) -> float:
        return attack_index(self.pf(league_level_, regression), league_level_)

    def defence(self, league_level_: float, regression: float) -> float:
        return defence_index(self.pa(league_level_, regression), league_level_)


@dataclass(frozen=True)
class RatingUpdate:
    """One club's rating moved by one game."""

    club_code: str
    opponent: str
    round: int
    game_id: str
    #: The club's game number this season.
    n: int
    weight: float
    extrapolated: bool
    projected_scored: float
    projected_allowed: float
    #: The actuals the update used (regulation-scaled unless scaling is off).
    scored: float
    allowed: float
    basis: str


@dataclass
class RatingBook:
    """Ratings for every club as of every round the store can tell, and how they got there."""

    season_code: str
    league_level: float
    regression: float
    base_round: int
    snapshots: dict[int, dict[str, ClubRating]]
    updates: list[RatingUpdate]

    def at(self, as_of_round: int) -> dict[str, ClubRating]:
        """The newest snapshot at or before ``as_of_round`` (the base when none is earlier)."""
        eligible = [r for r in self.snapshots if r <= as_of_round]
        return self.snapshots[max(eligible) if eligible else self.base_round]

    @property
    def rounds(self) -> list[int]:
        return sorted(self.snapshots)


# --------------------------------------------------------------------------- the base


def load_base_ratings(ctx: ReadContext) -> tuple[int, dict[str, ClubRating]]:
    """The imported (or hand-entered) ratings the fold starts from, and their round.

    Rows written by this model's own round updates are not a base: they are a record of the
    fold, and a later workbook import replaces the base wholesale. An empty store gives ``(0, {})``.
    """
    rows = list(
        ctx.session.execute(
            select(ElTeamRating).where(
                ElTeamRating.season_code == ctx.season_code, ElTeamRating.source != "roundUpdate"
            )
        ).scalars()
    )
    if not rows:
        return 0, {}
    base_round = max(row.as_of_round for row in rows)
    newest: dict[str, ElTeamRating] = {}
    for row in rows:
        held = newest.get(row.club_code)
        if held is None or row.as_of_round > held.as_of_round:
            newest[row.club_code] = row
    return base_round, {
        club: ClubRating(
            club_code=club,
            as_of_round=base_round,
            pf_prior=row.pf_prior,
            pa_prior=row.pa_prior,
            prior_is_estimate=bool(row.prior_is_estimate),
            attack_adj=row.attack_adj,
            defence_adj=row.defence_adj,
            source=row.source,
        )
        for club, row in newest.items()
    }


def league_level(base: Mapping[str, ClubRating]) -> float:
    """``L``: the mean of the clubs' ``pf_prior`` (Settings ``C17``). Fixed for the season."""
    if not base:
        raise ValueError("no club ratings to take a league level from")
    return math.fsum(r.pf_prior for r in base.values()) / len(base)


# --------------------------------------------------------------------------- one update


def weight_for(ctx: ReadContext, n: int) -> RoundWeight:
    """The update weight for a club's ``n``-th game of the season, and whether it is a default.

    The stored ``roundWeight.n`` setting is used when there is one (the workbook's, or the
    user's); a setting still on its default is flagged ``extrapolated`` beyond the two rounds
    the workbook states, and ``1 / (n + 9)`` is used past the table.
    """
    key = f"roundWeight.{n}"
    if key in ctx.settings:
        setting = ctx.settings[key]
        return RoundWeight(setting.value, setting.provenance == "default" and n > WORKBOOK_ROUNDS)
    return round_weight(n, {}, offset=9)


def regulation_actual(points: int, overtime_periods: int | None, enabled: bool) -> float:
    """``points`` restated as if the game had ended after regulation (or as-is when disabled).

    The team time comes from the overtime count, not from the box score's rounded minutes. An
    unknown overtime count is taken as none: there is nothing to scale by.
    """
    if not enabled or not overtime_periods:
        return float(points)
    return regulation_scale(
        points, PROFILE.team_time_seconds(overtime_periods), PROFILE.team_regulation_seconds
    )


def game_numbers(ctx: ReadContext) -> dict[tuple[str, str], int]:
    """``{(club, game_id): n}``: each club's final games, numbered from the season's first."""
    numbers: dict[tuple[str, str], int] = {}
    finals: dict[str, list[Any]] = {}
    for game in ctx.games:
        if game_has_result(game):
            for club in (game.home_club_code, game.away_club_code):
                finals.setdefault(club, []).append(game)
    for club, games in finals.items():
        games.sort(key=lambda g: (g.round_number, g.game_date, g.game_id))
        for index, game in enumerate(games, start=1):
            numbers[(club, game.game_id)] = index
    return numbers


def locked_scores(ctx: ReadContext) -> dict[str, tuple[float, float]]:
    """The ``locked`` projected scores by game: what was frozen before tip-off."""
    ids = [g.game_id for g in ctx.games]
    if not ids:
        return {}
    out: dict[str, tuple[float, float]] = {}
    rows = ctx.session.execute(
        select(ElProjectionLedger)
        .where(ElProjectionLedger.game_id.in_(ids), ElProjectionLedger.kind == "locked")
        .order_by(ElProjectionLedger.ledger_id)
    ).scalars()
    for row in rows:
        out[row.game_id] = (row.home_pts, row.away_pts)  # the newest locked row wins
    return out


def apply_game(
    ctx: ReadContext,
    *,
    game: Any,
    previous: Mapping[str, ClubRating],
    updated: dict[str, ClubRating],
    projected: tuple[float, float],
    basis: str,
    numbers: Mapping[tuple[str, str], int],
    as_of_round: int,
) -> list[RatingUpdate]:
    """Move both clubs of one final game and record the two updates.

    ``previous`` is the snapshot the projection was made from; ``updated`` is the snapshot being
    built (a club that plays twice in a round is moved twice, from its latest value). Returns
    the two :class:`RatingUpdate` rows, or nothing when either club has no rating.
    """
    home, away = game.home_club_code, game.away_club_code
    if home not in previous or away not in previous:
        return []
    scaling = bool(ctx.setting("overtimeScaling"))
    scored = {
        home: regulation_actual(game.home_pts, game.ot_periods, scaling),
        away: regulation_actual(game.away_pts, game.ot_periods, scaling),
    }
    allowed = {home: scored[away], away: scored[home]}
    proj_scored = {home: projected[0], away: projected[1]}
    proj_allowed = {home: projected[1], away: projected[0]}
    out: list[RatingUpdate] = []
    for club, opponent in ((home, away), (away, home)):
        n = numbers.get((club, game.game_id), 1)
        weight = weight_for(ctx, n)
        current = updated.get(club, previous[club])
        attack = update_adjustment(
            current.attack_adj, scored[club], proj_scored[club], weight.weight
        )
        defence = update_adjustment(
            current.defence_adj, allowed[club], proj_allowed[club], weight.weight
        )
        updated[club] = replace(
            current,
            as_of_round=as_of_round,
            attack_adj=attack,
            defence_adj=defence,
            source="roundUpdate",
            update_weight=weight.weight,
            update_basis=basis,
            extrapolated=weight.extrapolated,
            games_counted=n,
        )
        out.append(
            RatingUpdate(
                club_code=club,
                opponent=opponent,
                round=game.round_number,
                game_id=game.game_id,
                n=n,
                weight=weight.weight,
                extrapolated=weight.extrapolated,
                projected_scored=proj_scored[club],
                projected_allowed=proj_allowed[club],
                scored=scored[club],
                allowed=allowed[club],
                basis=basis,
            )
        )
    return out


# --------------------------------------------------------------------------- persistence


def _complete_rounds(ctx: ReadContext) -> set[int]:
    """Rounds where every game that is not postponed is final with a box score that passed."""
    rounds: dict[int, list[Any]] = {}
    for game in ctx.games:
        rounds.setdefault(game.round_number, []).append(game)
    return {
        number
        for number, games in rounds.items()
        if all(counts_for_scoring(g) for g in games if g.status != "postponed")
        and any(counts_for_scoring(g) for g in games)
    }


def persist_book(session: Session, ctx: ReadContext, book: RatingBook, now: datetime) -> int:
    """Write the book's completed-round updates to ``el_team_rating`` (idempotent). Returns the
    number of rows written or changed. Does not commit.

    One row per club per round after the base, ``source = 'roundUpdate'``, with the weight and
    the basis (``locked`` or ``reconstructed``) of the projection the update measured against.
    A round that is not complete (a game still to be played, or whose box score did not pass) is
    not written: its ratings are provisional and are served from the model, not the table.
    """
    complete = _complete_rounds(ctx)
    written = 0
    stamp = naive_utc(now)
    for round_number in book.rounds:
        if round_number <= book.base_round or round_number not in complete:
            continue
        for club, row in book.snapshots[round_number].items():
            values = {
                "pf_prior": row.pf_prior,
                "pa_prior": row.pa_prior,
                "prior_is_estimate": row.prior_is_estimate,
                "attack_adj": row.attack_adj,
                "defence_adj": row.defence_adj,
                "source": "roundUpdate",
                "update_weight": row.update_weight,
                "update_basis": row.update_basis,
            }
            existing = session.get(ElTeamRating, (ctx.season_code, club, round_number))
            if existing is None:
                session.add(
                    ElTeamRating(
                        season_code=ctx.season_code,
                        club_code=club,
                        as_of_round=round_number,
                        computed_at=stamp,
                        **values,
                    )
                )
                written += 1
            elif existing.source == "roundUpdate" and any(
                getattr(existing, key) != value for key, value in values.items()
            ):
                for key, value in values.items():
                    setattr(existing, key, value)
                existing.computed_at = stamp
                written += 1
    session.flush()
    return written


def run_ratings(now: datetime | None = None, force: bool = False) -> dict[str, Any]:
    """Worker job ``el.ratings``: rate every completed round, and keep the ledger honest.

    Called often (every 15 minutes) and expected to decide for itself: it computes the model,
    writes the rating rows of any round that has completed, and writes a ``reconstructed``
    ledger row for each game whose update had to be measured against a projection rebuilt after
    the fact. When nothing new has completed it writes nothing and says ``skipped``.
    """
    # Imported here: the model imports this module for the rating arithmetic.
    from ..db import el_session_scope
    from ..read.queries import build_context
    from . import ledger as ledger_module
    from .round_projection import ProjectionUnavailable, get_model

    moment = now or datetime.now(timezone.utc)
    with el_session_scope() as session:
        try:
            ctx = build_context(session, None, now=moment)
            model = get_model(ctx)
        except ProjectionUnavailable as exc:  # nothing imported yet: nothing to rate
            return {"status": "skipped", "reason": str(exc)}
        except Exception as exc:  # noqa: BLE001 - no season yet, or a store mid-import
            return {"status": "skipped", "reason": str(exc)}
        rows = persist_book(session, ctx, model.ratings, moment)
        reconstructed = ledger_module.write_reconstructed_rows(session, ctx, model, moment)
        if rows or reconstructed:
            _LOG.info(
                "el.ratings: %s rating rows, %s reconstructed projections", rows, reconstructed
            )
        return {
            "status": "ok" if (rows or reconstructed) else "skipped",
            "ratingRows": rows,
            "reconstructed": reconstructed,
        }
