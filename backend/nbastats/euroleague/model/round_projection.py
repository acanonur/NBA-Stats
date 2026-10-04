"""The EuroLeague projection: the workbook's team model, run for every game, from the store.

This is the module where the pieces meet. The shared pure core supplies the formulas
(:mod:`nbastats.shared.team_projection`, :mod:`nbastats.shared.injury_layer`); the neighbouring
modules supply the club ratings (:mod:`.ratings`) and the player rates and minutes
(:mod:`.player_rates`); the availability layer (:mod:`nbastats.euroleague.read.availability`)
supplies who plays. What this module owns is the *order of operations* and the three honesty
rules that make the result trustworthy.

One game, in order (design section 8.3)
---------------------------------------
1. **Ratings as of the previous round.** ``pf`` and ``pa`` from the club's rating as it stood
   before this game's round, with ``L`` the league level fixed for the season.
2. **The squad.** Every player with a rate row for the club, minutes from the minutes blend,
   points ``p = pts40 * minutes / 40``. When ``squadReconcile`` is on (the default) the squad's
   points are scaled so they sum to ``pf``: the players then add up to the team, and the injury
   layer's availability factor equals the workbook's ``AB / O``.
3. **The injury layer.** Each player's chance of playing is the chance of his status *in force*
   at the cut-off (nothing in force means 1, and he is counted as assumed available); the shared
   layer turns the squad and its chances into an availability factor and each player's boost.
4. **The match.** ``home = L * A_home' * D_away + h / 2`` and ``away = L * A_away' * D_home - h / 2``
   with ``A' = A * af``; ``h`` is 0 at a neutral venue, the game's override when it has one, the
   setting otherwise (flagged ``venueAssumed`` when neutrality is unknown).

The fold, and why the model is built once and handed out
--------------------------------------------------------
Ratings after round 4 need every projection for rounds 3 and 4, which need ratings after round 3.
:meth:`Model.build` walks the rounds in order, makes each game's projection from the *previous*
round's snapshot, measures the result against it and moves the clubs
(:func:`~nbastats.euroleague.model.ratings.apply_game`), then builds the players' state as of that
round. The finished :class:`Model` is plain data (snapshots by round), is memoised per
``(sync version, ledger, statuses, settings)`` by :func:`get_model`, and holds **no session**: a
projection is made by ``model.project(ctx, game, ...)`` with the *current request's* context, so
nothing here can outlive the session it was read with.

The three honesty rules
-----------------------
* **Nothing from the future.** A projection takes an ``as_of`` and uses only entries published
  strictly before it and games that started strictly before it. A *reconstructed* projection
  (one made after the game, for lack of a frozen one) is cut off one second before tip-off, so
  its inputs are exactly what was knowable.
* **A frozen projection is never replaced.** The model's own update uses the ``locked`` ledger
  row when there is one; only without it does it reconstruct, and it says so.
* **No game before the base is projected from the base.** A game in or before the imported
  as-of round has no pre-game state in the store (the base *contains* its result). Its
  projection is the ledger's ``imported`` row (the workbook's published scores) when there is
  one, and nothing otherwise.

What is deliberately absent
---------------------------
No probability of winning and nothing to compare with an outside number (:mod:`nbastats.shared.
team_projection` explains why). The interval is ``centre +- 1.2816 * sd`` with the settings'
spreads, and the payload says ``intervalBasis: "assumed"`` until the spreads have been fitted from
ledger residuals (:func:`~nbastats.euroleague.model.ledger.calibrate`). Defence indices are not
changed by absences, and a payload says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Final, Sequence

from sqlalchemy import select

from ...shared.injury_layer import (
    CAP_CONSISTENT,
    CAP_WORKBOOK,
    InjuryResult,
    InjurySettings,
    PlayerInput,
    PlayerOutcome,
    apply_injury_layer,
    scale_to_total,
)
from ...shared.team_projection import (
    HomeAdvantage,
    MatchProjection,
    TeamStrength,
    home_advantage_for_game,
    project_match,
)
from ..models import ElGame, ElProjectionLedger
from ..read.availability import Resolved, get_book, source_of
from ..read.queries import ReadContext, counts_for_scoring, digest, game_start, memoise
from .player_rates import PlayerState, build_states, load_bases, load_lines
from .ratings import (
    ClubRating,
    RatingBook,
    apply_game,
    game_numbers,
    league_level,
    load_base_ratings,
    locked_scores,
)

__all__ = [
    "MODEL_KEY",
    "MODEL_VERSION",
    "ABSENCE_LIMIT",
    "ProjectionUnavailable",
    "SquadLine",
    "SideResult",
    "ProjectionResult",
    "ProjectionView",
    "Model",
    "get_model",
    "injury_settings",
    "interval_basis",
    "side_absences",
    "current_projection",
    "ledger_rows_for",
]

#: The model's name in a payload and in the review; the workbook's own projections are
#: reviewed under ``"workbook"``.
MODEL_KEY: Final = "hardwood"
#: Bumped whenever the way a projection is computed changes, so a ledger row says which it was.
MODEL_VERSION: Final = "el-1"
#: How many absences a side lists in ``keyAbsences``.
ABSENCE_LIMIT: Final = 5

_ONE_SECOND: Final = timedelta(seconds=1)


class ProjectionUnavailable(Exception):
    """A game cannot be projected (no ratings, or a club the store has no rating for)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class SquadLine:
    """One player going into the injury layer, and what came out."""

    person_code: str
    club_code: str
    minutes: float
    points: float
    chance: float
    #: The status the model used, or ``None`` when nothing was in force (he is assumed to play).
    status: str | None
    assumed: bool
    resolved: Resolved
    outcome: PlayerOutcome
    state: PlayerState


@dataclass(frozen=True)
class SideResult:
    """One club's side of a projected game."""

    club_code: str
    rating: ClubRating
    pf: float
    pa: float
    attack: float
    defence: float
    squad: tuple[SquadLine, ...]
    injury: InjuryResult
    #: ``pf / sum(raw points)`` when the squad was scaled to the club's rating, else ``None``.
    squad_scale: float | None
    assumed_available: int
    stale_ignored: int


@dataclass(frozen=True)
class ProjectionResult:
    """A game projected, with everything needed to explain it and to write it to the ledger."""

    game_id: str
    #: ``latest`` (made now, for a game still to be played) or ``reconstructed``.
    kind: str
    as_of: datetime
    state_round: int
    league_level: float
    regression: float
    home_advantage: HomeAdvantage
    home: SideResult
    away: SideResult
    match: MatchProjection
    team_sd: float
    margin_sd: float
    cap_policy: str
    settings_sha256: str
    inputs_sha256: str

    @property
    def sides(self) -> tuple[SideResult, SideResult]:
        return self.home, self.away


@dataclass(frozen=True)
class ProjectionView:
    """The projection a screen is shown for a game, and where it came from."""

    kind: str
    result: ProjectionResult | None = None
    ledger: ElProjectionLedger | None = None

    @property
    def home_points(self) -> float:
        return self.result.match.home_points if self.result else self.ledger.home_pts  # type: ignore[union-attr]

    @property
    def away_points(self) -> float:
        return self.result.match.away_points if self.result else self.ledger.away_pts  # type: ignore[union-attr]


# --------------------------------------------------------------------------- settings


def injury_settings(ctx: ReadContext) -> InjurySettings:
    """The five levers of the injury layer from the model settings (``replacementPer40`` is the
    workbook's units; the layer wants per-minute)."""
    return InjurySettings.from_per40(
        ctx.setting("replacementPer40"),
        ctx.setting("absorbShare"),
        ctx.setting("boostCap"),
        cap_policy=CAP_CONSISTENT if ctx.setting("capPolicyConsistent") >= 0.5 else CAP_WORKBOOK,
        rotation_share=ctx.setting("rotationShare"),
    )


def interval_basis(ctx: ReadContext) -> str | None:
    """``assumed`` until both spreads have been fitted from ledger residuals, then
    ``fittedLedger``. The spreads exist (the workbook's, or the user's), so there is always a
    basis; ``None`` is reserved for a payload with no interval."""
    fitted = all(ctx.settings[k].provenance == "fittedLedger" for k in ("teamSd", "marginSd"))
    return "fittedLedger" if fitted else "assumed"


# --------------------------------------------------------------------------- the model


@dataclass
class Model:
    """The folded model: ratings and player states by round, and the projections it rebuilt."""

    ratings: RatingBook
    #: ``as_of_round`` to every player's state as of that round.
    players: dict[int, dict[str, PlayerState]]
    #: Projections the fold had to rebuild (no locked row), by game id.
    reconstructed: dict[str, ProjectionResult]

    # ------------------------------------------------------------------ build

    @classmethod
    def build(cls, ctx: ReadContext) -> "Model":
        base_round, base = load_base_ratings(ctx)
        if not base:
            raise ProjectionUnavailable(
                "This store has no club ratings yet: import a workbook, or wait for the first ingest."
            )
        level = league_level(base)
        book = RatingBook(
            season_code=ctx.season_code,
            league_level=level,
            regression=ctx.setting("priorRegression"),
            base_round=base_round,
            snapshots={base_round: base},
            updates=[],
        )
        bases = load_bases(ctx)
        floor = min([base_round, *(b.as_of_round for b in bases.values())])
        lines, club_games = load_lines(ctx, floor)
        availability = get_book(ctx)
        games = ctx.games_by_id

        def excused(person: str, game_id: str) -> bool:
            game = games[game_id]
            return availability.excused(person, game_id, game_start(game))

        model = cls(
            ratings=book,
            players={base_round: build_states(bases, lines, club_games, excused, base_round)},
            reconstructed={},
        )
        scoring = [g for g in ctx.games if counts_for_scoring(g)]
        numbers = game_numbers(ctx)
        locked = locked_scores(ctx)
        previous_round = base_round
        for round_number in sorted(
            {g.round_number for g in scoring if g.round_number > base_round}
        ):
            previous = book.snapshots[previous_round]
            updated = dict(previous)
            for game in sorted(
                (g for g in scoring if g.round_number == round_number), key=lambda g: g.game_id
            ):
                scores = locked.get(game.game_id)
                basis = "locked"
                if scores is None:
                    try:
                        rebuilt = model.project(
                            ctx,
                            game,
                            as_of=game_start(game) - _ONE_SECOND,
                            kind="reconstructed",
                            state_round=previous_round,
                        )
                    except ProjectionUnavailable:
                        continue
                    model.reconstructed[game.game_id] = rebuilt
                    scores = (rebuilt.match.home_points, rebuilt.match.away_points)
                    basis = "reconstructed"
                book.updates.extend(
                    apply_game(
                        ctx,
                        game=game,
                        previous=previous,
                        updated=updated,
                        projected=scores,
                        basis=basis,
                        numbers=numbers,
                        as_of_round=round_number,
                    )
                )
            book.snapshots[round_number] = {
                club: (
                    row
                    if row.as_of_round == round_number
                    else replace(
                        row,
                        as_of_round=round_number,
                        update_weight=None,
                        update_basis=None,
                        extrapolated=False,
                    )
                )
                for club, row in updated.items()
            }
            model.players[round_number] = build_states(
                bases, lines, club_games, excused, round_number
            )
            previous_round = round_number
        return model

    # ------------------------------------------------------------------ queries

    @property
    def base_round(self) -> int:
        return self.ratings.base_round

    @property
    def last_round(self) -> int:
        return max(self.ratings.snapshots)

    def state_round_for(self, game_round: int) -> int:
        """The round whose snapshot a game in ``game_round`` is projected from: the newest
        before it, or the base when there is none (see the module docstring on pre-base games)."""
        earlier = [r for r in self.ratings.snapshots if r < game_round]
        return max(earlier) if earlier else self.ratings.base_round

    # ------------------------------------------------------------------ one side

    def side(
        self,
        ctx: ReadContext,
        club: str,
        *,
        as_of: datetime,
        game_id: str | None,
        state_round: int,
    ) -> SideResult:
        """One club's squad, chances and injury layer as of ``as_of``."""
        ratings = self.ratings.snapshots[state_round]
        if club not in ratings:
            raise ProjectionUnavailable(f"{club} has no rating in this store.")
        rating = ratings[club]
        level, regression = self.ratings.league_level, self.ratings.regression
        pf, pa = rating.pf(level, regression), rating.pa(level, regression)
        states = sorted(
            (
                s
                for s in self.players.get(state_round, {}).values()
                if s.club_code == club and s.minutes is not None and s.points_per_game is not None
            ),
            key=lambda s: s.person_code,
        )
        raw = [s.points_per_game for s in states]  # type: ignore[misc]
        raw_total = math.fsum(raw)
        scale: float | None = None
        if ctx.setting("squadReconcile") >= 0.5 and raw_total > 0:
            points = scale_to_total(raw, pf)  # type: ignore[arg-type]
            scale = pf / raw_total
        else:
            points = list(raw)  # type: ignore[arg-type]
        book = get_book(ctx)
        table = ctx.chance_table()
        inputs: list[PlayerInput] = []
        resolved_by: dict[str, Resolved] = {}
        assumed = stale = 0
        for state, p in zip(states, points):
            resolved = book.resolve(state.person_code, game_id, as_of)
            chance, was_assumed = book.chance(resolved, table)
            resolved_by[state.person_code] = resolved
            if was_assumed:
                assumed += 1
            if resolved.present and not resolved.in_force:
                stale += 1
            inputs.append(
                PlayerInput(
                    key=state.person_code,
                    minutes=state.minutes,  # type: ignore[arg-type]
                    points=p,
                    chance=chance,
                )
            )
        injury = apply_injury_layer(inputs, injury_settings(ctx))
        lines = tuple(
            SquadLine(
                person_code=state.person_code,
                club_code=club,
                minutes=inp.minutes,
                points=inp.points,
                chance=inp.chance,
                status=resolved_by[state.person_code].model_status,
                assumed=resolved_by[state.person_code].model_status is None,
                resolved=resolved_by[state.person_code],
                outcome=outcome,
                state=state,
            )
            for state, inp, outcome in zip(states, inputs, injury.players)
        )
        return SideResult(
            club_code=club,
            rating=rating,
            pf=pf,
            pa=pa,
            attack=rating.attack(level, regression),
            defence=rating.defence(level, regression),
            squad=lines,
            injury=injury,
            squad_scale=scale,
            assumed_available=assumed,
            stale_ignored=stale,
        )

    # ------------------------------------------------------------------ one game

    def project(
        self,
        ctx: ReadContext,
        game: ElGame,
        *,
        as_of: datetime,
        kind: str,
        state_round: int | None = None,
    ) -> ProjectionResult:
        """Project ``game`` from what was known at ``as_of`` (see the module docstring)."""
        state = self.state_round_for(game.round_number) if state_round is None else state_round
        home = self.side(
            ctx, game.home_club_code, as_of=as_of, game_id=game.game_id, state_round=state
        )
        away = self.side(
            ctx, game.away_club_code, as_of=as_of, game_id=game.game_id, state_round=state
        )
        advantage = home_advantage_for_game(
            default=ctx.setting("homeAdvantagePoints"),
            override=game.home_advantage_override,
            is_neutral=game.is_neutral,
        )
        team_sd, margin_sd = ctx.setting("teamSd"), ctx.setting("marginSd")
        match = project_match(
            self.ratings.league_level,
            TeamStrength(home.attack, home.defence, home.injury.availability_factor),
            TeamStrength(away.attack, away.defence, away.injury.availability_factor),
            advantage.points,
            team_sd=team_sd,
            margin_sd=margin_sd,
        )
        settings_sha = ctx.data_key()[-1]  # the settings digest, read once per context
        inputs_sha = digest(
            game.game_id,
            state,
            self.ratings.league_level,
            [
                home.rating.attack_adj,
                home.rating.defence_adj,
                away.rating.attack_adj,
                away.rating.defence_adj,
            ],
            advantage.points,
            [
                (s.person_code, s.minutes, s.points, s.chance)
                for side in (home, away)
                for s in side.squad
            ],
            settings_sha,
        )
        return ProjectionResult(
            game_id=game.game_id,
            kind=kind,
            as_of=as_of,
            state_round=state,
            league_level=self.ratings.league_level,
            regression=self.ratings.regression,
            home_advantage=advantage,
            home=home,
            away=away,
            match=match,
            team_sd=team_sd,
            margin_sd=margin_sd,
            cap_policy=(
                CAP_CONSISTENT if ctx.setting("capPolicyConsistent") >= 0.5 else CAP_WORKBOOK
            ),
            settings_sha256=settings_sha,
            inputs_sha256=inputs_sha,
        )


def get_model(ctx: ReadContext) -> Model:
    """The folded model for the context's season, memoised per data version.

    Raises :class:`ProjectionUnavailable` when the store has no club ratings.
    """
    return memoise(ctx, "model", None, lambda: Model.build(ctx))


# --------------------------------------------------------------------------- views


def side_absences(
    ctx: ReadContext, side: SideResult, as_of: datetime, *, limit: int | None = ABSENCE_LIMIT
) -> list[dict[str, Any]]:
    """``AbsenceEntry`` rows for the players the model expects to miss time, biggest first.

    Only players with a status *in force* are listed (a player assumed to play is not an
    absence). ``limit`` of ``None`` lists them all.
    """
    book = get_book(ctx)
    rows: list[tuple[float, str, dict[str, Any]]] = []
    for line in side.squad:
        if line.assumed or line.chance >= 1.0 or line.resolved.row is None:
            continue
        rows.append(
            (
                -line.outcome.expected_points_lost,
                line.person_code,
                {
                    "player": ctx.player_ref(line.person_code, side.club_code),
                    "status": line.status,
                    "chanceOfPlaying": line.chance,
                    "expectedPointsLost": line.outcome.expected_points_lost,
                    "expectedMinutesLost": line.outcome.expected_minutes_lost,
                    "inForce": True,
                    "isStale": book.is_stale(line.resolved, side.club_code, as_of),
                    "source": source_of(line.resolved.row),
                },
            )
        )
    rows.sort(key=lambda item: (item[0], item[1]))
    entries = [entry for _, _, entry in rows]
    return entries if limit is None else entries[:limit]


def ledger_rows_for(ctx: ReadContext, game_id: str) -> list[ElProjectionLedger]:
    """Every ledger row for a game, oldest first."""
    return list(
        ctx.session.execute(
            select(ElProjectionLedger)
            .where(ElProjectionLedger.game_id == game_id)
            .order_by(ElProjectionLedger.ledger_id)
        ).scalars()
    )


def _newest(rows: Sequence[ElProjectionLedger], kind: str) -> ElProjectionLedger | None:
    matching = [r for r in rows if r.kind == kind]
    return matching[-1] if matching else None


def current_projection(ctx: ReadContext, model: Model, game: ElGame) -> ProjectionView | None:
    """The projection a screen shows for ``game``, or ``None`` when there is no honest one.

    * a game still to be played: the model's, made now (``latest``);
    * a game that has tipped off (result pending or final): the frozen ``locked`` row if the
      ledger has one; else, for a game after the imported round, the model's projection rebuilt
      from inputs strictly before tip-off (``reconstructed``); else, for a game in or before the
      imported round, the workbook's published projection (``imported``) if it was imported.
    """
    status = ctx.game_status(game)
    if status == "postponed":
        return None
    rows = ledger_rows_for(ctx, game.game_id)
    if status == "scheduled":
        try:
            return ProjectionView(
                "latest", result=model.project(ctx, game, as_of=ctx.now, kind="latest")
            )
        except ProjectionUnavailable:
            return None
    locked = _newest(rows, "locked")
    if locked is not None:
        return ProjectionView("locked", ledger=locked)
    if game.round_number <= model.base_round:
        imported = _newest(rows, "imported")
        return ProjectionView("imported", ledger=imported) if imported is not None else None
    try:
        rebuilt = model.reconstructed.get(game.game_id) or model.project(
            ctx, game, as_of=game_start(game) - _ONE_SECOND, kind="reconstructed"
        )
    except ProjectionUnavailable:
        return None
    return ProjectionView("reconstructed", result=rebuilt)
