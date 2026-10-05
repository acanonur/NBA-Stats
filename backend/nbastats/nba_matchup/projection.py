"""The NBA team-score projection: the workbook's model, run for every game, from the stats store.

This is the module where the pieces meet. The shared pure core supplies the formulas
(:mod:`nbastats.shared.team_projection`, :mod:`nbastats.shared.injury_layer`); the availability
view (:mod:`nbastats.nba_matchup.availability_view`) supplies who plays; this module owns the
*order of operations* and the honesty rules that make the result trustworthy. It is read-only
over ``team_game``, ``player_game_basic`` and the intel tables, and it is independent of
:mod:`nbastats.projection` (the player stat-line engine), which it never touches: that engine
projects one player's box score, this one projects two teams' scores, and the two families are
never added together.

One game, in order (design section 8.4)
---------------------------------------
1. **The league level** ``L = (n * L_cur + W * L_prev) / (n + W)``, the mean regulation-scaled
   score per team-game this season blended with last season's, ``n`` the team-games played and
   ``W`` the ``leagueLevelWeight`` (150). It slides from last season's level to this season's as
   games accumulate, with no jump at some arbitrary game.
2. **Ratings.** Each team's prior is last season's regulation-scaled points for and against,
   regressed toward the league (``rating(prior, L, r, adj)`` with ``r = priorRegression``,
   0.3, a DEFAULT and not the workbook's); a team with no previous season starts at ``L``. The
   prior is restated at the current league level (``prior * L / L_prev``) so that an index
   ``pf / L`` means the same thing in a high-scoring year as in a low one; with equal levels it
   is the design's ``rating(PPG_prev, L_prev, r, 0)`` exactly. The in-season adjustment
   ``adj`` moves after every game by ``w_n * (actual - projected)`` with ``w_n = 1 / (n +
   priorWeightGames)``, ``n`` the team's game number *this season*.
3. **The injury layer** (:func:`side_availability`), for the players a team has: expected
   points ``p`` and minutes ``m`` are the blend ``(n * cur + 10 * prev) / (n + 10)`` of this
   season's per-game values and last season's, over players whose latest team this season is the
   team; the chance ``c`` is that of the status *in force* at the cut-off (nothing in force means
   1, and he is counted as assumed available). The replacement rate is ``replacementShare`` times
   the league's points per team-minute, a quantity derived from NBA scoring and not the
   EuroLeague's 9 per 40. Defence indices are never changed by absences.
4. **The match** ``home = L * A_home' * D_away + h / 2`` and ``away = L * A_away' * D_home - h / 2``
   with ``A' = A * af`` and ``h`` the home advantage: the mean margin of this season and last once
   300 games are final, the setting (2.5, a DEFAULT) before. The NBA records no neutral sites, so
   ``venueAssumed`` is always true.

The walk-forward, and why the model is built once and handed out
-----------------------------------------------------------------
A team's rating after game ``n`` needs the projection that was made *before* game ``n``, which
needs the ratings after game ``n - 1``. :meth:`Model.build` therefore folds this season's regular
season games in order: games that started at the same instant are projected from the same state
and then applied together (a game never informs another that tipped no later than it), each
projection is measured against the result, and a snapshot of every team's state is kept after each
group. The finished :class:`Model` is plain data (no session, no ORM object), memoised per
``(data marks)`` by :func:`get_model`, so a request after the first costs a lookup. A projection of
any game is then ``model.project(ctx, game, ...)`` from the snapshot in force at its start.

Honesty rules
-------------
* **Nothing from the future.** A projection uses games that *started* strictly before the game
  does, availability *recorded* and published strictly before ``as_of``, and the overrides
  entered by then. A *reconstructed* projection (made after the game, for lack of a frozen one) is
  cut off one second before the lock deadline, so its inputs are exactly what was knowable.
* **A frozen projection is never replaced.** A ``locked`` ledger row is shown as it was written.
* **Playoff and play-in games are projected from the regular season's ratings.** The model
  is built over the regular season and is not updated by playoff results; a note says so.
* **The spreads are fitted, never borrowed.** The 80% interval needs ``teamSd`` and ``marginSd``,
  which have no default. They are fitted from the previous season's walk-forward residuals
  (``fittedPrevSeason``) and, after 300 locked games, from the ledger (``fittedLedger``); until
  then the interval is ``null`` and shows as a dash. The EuroLeague's 9.5 and 11.5 are never used.
* **Eras.** Projections exist from 1996-97 on; an earlier season is ``unavailable``.

What is deliberately absent
---------------------------
No probability of winning and nothing to compare a projected score with: the margin, its range,
the projected winner and the projected combined points are the whole of it
(:mod:`nbastats.shared.team_projection` explains why).

The scheduler's entry point
---------------------------
:func:`run_calibrate` (daily) is what ``nbastats.worker`` calls by string name. The ledger's
two (:func:`~nbastats.nba_matchup.ledger.run_refresh` and ``run_lock``) are in
:mod:`nbastats.nba_matchup.ledger`.
"""

from __future__ import annotations

import bisect
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Final, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import Game, PlayerGameBasic, TeamGame, TeamProjectionLedger
from ..nba_intel import settings as intel_settings
from ..shared.injury_layer import (
    CAP_CONSISTENT,
    CAP_WORKBOOK,
    InjuryResult,
    InjurySettings,
    PlayerInput,
    PlayerOutcome,
    apply_injury_layer,
)
from ..shared.team_projection import (
    HomeAdvantage,
    MatchProjection,
    TeamStrength,
    attack_index,
    blend_league_level,
    defence_index,
    home_advantage_for_game,
    project_match,
    rating,
    sequential_weight,
    update_adjustment,
)
from .availability_view import (
    Resolved,
    get_book,
    source_of,
)
from .queries import (
    PROFILE,
    PROJECTABLE_SEASON_TYPES,
    PROJECTION_FROM,
    REGULAR_SEASON,
    ReadContext,
    aware,
    build_context,
    digest,
    game_has_result,
    memoise,
    naive_utc,
    previous_season_of,
    result_day,
    result_known_by,
    season_year,
)

__all__ = [
    "MODEL_KEY",
    "MODEL_VERSION",
    "ABSENCE_LIMIT",
    "PLAYER_PRIOR_GAMES",
    "PRIOR_MIN_GAMES",
    "MIN_FIT_GAMES",
    "ProjectionUnavailable",
    "SquadLine",
    "SideResult",
    "ProjectionResult",
    "ProjectionView",
    "TeamState",
    "Snapshot",
    "Model",
    "CalibrationResult",
    "get_model",
    "can_project",
    "injury_settings",
    "interval_basis",
    "model_constants",
    "side_availability",
    "side_absences",
    "ledger_rows_for",
    "current_projection",
    "calibrate",
    "run_calibrate",
]

_LOG = logging.getLogger(__name__)

#: The model's name in a payload and in the review.
MODEL_KEY: Final = "hardwood"
#: Bumped whenever the way a projection is computed changes, so a ledger row says which it was.
MODEL_VERSION: Final = "nba-1"
#: How many absences a side lists in ``keyAbsences``.
ABSENCE_LIMIT: Final = 5
#: The weight, in games, last season's per-game points and minutes carry in a player's blend.
PLAYER_PRIOR_GAMES: Final = 10
#: A team needs this many games last season for last season to be its prior.
PRIOR_MIN_GAMES: Final = 10
#: Games of residuals needed before a spread is fitted from them.
MIN_FIT_GAMES: Final = 100
#: Display minutes only: the share of lost minutes the listed rotation takes.
ROTATION_SHARE: Final = 0.5

_ONE_SECOND: Final = timedelta(seconds=1)
_REGULATION_MINUTES: Final = PROFILE.team_regulation_seconds / 60


class ProjectionUnavailable(Exception):
    """A game cannot be projected (an era without projections, or no scored games to start from)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class SquadLine:
    """One player going into the injury layer, and what came out."""

    player_id: int
    team_id: int
    minutes: float
    points: float
    chance: float
    #: The status the model used, or ``None`` when nothing was in force (he is assumed to play).
    status: str | None
    assumed: bool
    resolved: Resolved
    outcome: PlayerOutcome


@dataclass(frozen=True)
class SideResult:
    """One team's side of a projected game."""

    team_id: int
    pf: float
    pa: float
    attack: float
    defence: float
    squad: tuple[SquadLine, ...]
    injury: InjuryResult | None
    availability_factor: float
    assumed_available: int
    stale_ignored: int
    report_state: str
    assumed_basis: str


@dataclass(frozen=True)
class ProjectionResult:
    """A game projected, with everything needed to explain it and to write it to the ledger."""

    game_id: str
    #: ``latest`` (made now, for a game before its deadline) or ``reconstructed``.
    kind: str
    as_of: datetime
    league_level: float
    regression: float
    home_advantage: HomeAdvantage
    home: SideResult
    away: SideResult
    match: MatchProjection
    team_sd: float | None
    margin_sd: float | None
    cap_policy: str
    settings_sha256: str
    inputs_sha256: str
    snapshot_id: int | None

    @property
    def sides(self) -> tuple[SideResult, SideResult]:
        return self.home, self.away


@dataclass(frozen=True)
class ProjectionView:
    """The projection a screen is shown for a game, and where it came from."""

    kind: str
    result: ProjectionResult | None = None
    ledger: TeamProjectionLedger | None = None


@dataclass(frozen=True)
class TeamState:
    """A team's in-season adjustments (points) and games played this season."""

    adj_pf: float = 0.0
    adj_pa: float = 0.0
    games: int = 0


_EMPTY_TEAM: Final = TeamState()


@dataclass(frozen=True)
class Snapshot:
    """The model's state after one game day's games: valid for any game on a later Eastern game
    day (``start`` and ``day`` are ``None`` for the state before any game). A day's games are
    projected together from the state before the day, because none of them is final when the
    others tip off."""

    start: datetime | None
    teams: dict[int, TeamState]
    day: date | None = None
    league_sum: float = 0.0
    league_n: int = 0
    margin_sum: float = 0.0
    margin_n: int = 0


@dataclass(frozen=True)
class _Pre:
    """What the fold remembers about one game's projection: the two projected scores."""

    game_id: str
    home_points: float
    away_points: float


# --------------------------------------------------------------------------- settings


def injury_settings(ctx: ReadContext, level: float) -> InjurySettings:
    """The five levers of the injury layer from the model settings.

    ``replacementShare`` is a share of the league's points per team-minute (``level`` over the
    240 team-minutes of a regulation game), which is what makes the replacement rate an NBA
    quantity rather than the EuroLeague's 9 per 40.
    """
    return InjurySettings(
        replacement_per_minute=ctx.setting("replacementShare") * level / _REGULATION_MINUTES,
        absorb_share=ctx.setting("absorbShare"),
        boost_cap=ctx.setting("boostCap"),
        cap_policy=CAP_CONSISTENT if ctx.setting("capPolicyConsistent") >= 0.5 else CAP_WORKBOOK,
        rotation_share=ROTATION_SHARE,
    )


def interval_basis(ctx: ReadContext) -> str | None:
    """Where the spreads came from: ``fittedPrevSeason``, ``fittedLedger``, ``assumed`` (set by
    hand, so unchecked against results) or ``None`` when there is no interval at all."""
    sd, margin = ctx.settings["teamSd"], ctx.settings["marginSd"]
    if sd.value is None or margin.value is None:
        return None
    provenances = {sd.provenance, margin.provenance}
    if provenances == {"fittedLedger"}:
        return "fittedLedger"
    if provenances <= {"fittedLedger", "fittedPrevSeason"}:
        return "fittedPrevSeason"
    return "assumed"


def can_project(season: str, season_type: str) -> str | None:
    """``None`` when a game of this season and type may be projected, else the reason it may not."""
    if season_year(season) < season_year(PROJECTION_FROM):
        return f"Projections exist from {PROJECTION_FROM}; {season} predates them."
    if season_type not in PROJECTABLE_SEASON_TYPES:
        return f"{season_type} games are not projected."
    return None


# --------------------------------------------------------------------------- loading


@dataclass(frozen=True)
class _Row:
    """One team's side of one final regular-season game, as the fold needs it."""

    game_id: str
    team: int
    opp: int
    is_home: bool
    pts: float
    opp_pts: float
    seconds: float | None

    @property
    def scale(self) -> float:
        """``R / t``: restates the score as if the game had ended after regulation time. Unknown
        team time is read as regulation length (scale 1), which is the common case."""
        if self.seconds is None or not self.seconds > 0:
            return 1.0
        return PROFILE.team_regulation_seconds / self.seconds


def _load_rows(session: Session, season: str, season_type: str) -> list[_Row]:
    rows = session.execute(
        select(
            TeamGame.game_id,
            TeamGame.team_id,
            TeamGame.is_home,
            TeamGame.pts,
            TeamGame.opp_pts,
            TeamGame.minutes,
            Game.home_team_id,
            Game.away_team_id,
        )
        .join(Game, Game.game_id == TeamGame.game_id)
        .where(
            Game.season == season,
            Game.season_type == season_type,
            Game.status == "final",
            TeamGame.pts.is_not(None),
            TeamGame.opp_pts.is_not(None),
        )
    )
    out: list[_Row] = []
    for r in rows:
        opponent = r.away_team_id if r.is_home else r.home_team_id
        if opponent == r.team_id:
            continue
        out.append(
            _Row(
                r.game_id,
                r.team_id,
                opponent,
                bool(r.is_home),
                float(r.pts),
                float(r.opp_pts),
                (r.minutes * 60.0) if r.minutes else None,
            )
        )
    return out


def _mean(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


@dataclass(frozen=True)
class _Prior:
    level: float
    teams: dict[int, tuple[float, float]]
    margin_sum: float
    margin_n: int


def _load_prior(session: Session, season: str) -> _Prior | None:
    """Last season's league level and each team's points for and against, regulation-scaled."""
    before = previous_season_of(session, season)
    if before is None:
        return None
    rows = _load_rows(session, before, REGULAR_SEASON)
    if not rows:
        return None
    level = _mean([r.pts * r.scale for r in rows])
    assert level is not None
    teams: dict[int, tuple[float, float]] = {}
    by_team: dict[int, list[_Row]] = {}
    for r in rows:
        by_team.setdefault(r.team, []).append(r)
    for team, games in by_team.items():
        if len(games) < PRIOR_MIN_GAMES:
            continue
        pf = _mean([g.pts * g.scale for g in games])
        pa = _mean([g.opp_pts * g.scale for g in games])
        assert pf is not None and pa is not None
        teams[team] = (pf, pa)
    homes = [r for r in rows if r.is_home]
    return _Prior(
        level=level,
        teams=teams,
        margin_sum=math.fsum(r.pts - r.opp_pts for r in homes),
        margin_n=len(homes),
    )


# --------------------------------------------------------------------------- the squad


def _blend(n: int, current: float | None, previous: float | None) -> float | None:
    """``(n * cur + 10 * prev) / (n + 10)``, degrading to whichever exists."""
    if current is None:
        return previous
    if previous is None:
        return current
    return (n * current + PLAYER_PRIOR_GAMES * previous) / (n + PLAYER_PRIOR_GAMES)


def squad_stats(ctx: ReadContext, team_id: int, cutoff: datetime) -> list[tuple[int, float, float]]:
    """``(player_id, expected minutes, expected points)`` for a team's players before ``cutoff``.

    A player belongs to the squad when his latest line this season before the cut-off was for the
    team, or when the team's roster listing names him and he has played no game for anyone yet.
    His rates are the blend of this season's per-game values (over *all* his lines, whatever the
    team) and last season's. A player the store knows nothing about contributes nothing.
    """
    session = ctx.session
    stamp = aware(cutoff)
    start_of = {g.game_id: ctx.start_of(g) for g in ctx.games if g.season_type == REGULAR_SEASON}
    known = {
        g.game_id
        for g in ctx.games
        if g.season_type == REGULAR_SEASON and result_known_by(g, stamp)  # type: ignore[arg-type]
    }

    def before(game_id: str) -> bool:
        return game_id in known

    candidates = {
        r.player_id
        for r in session.execute(
            select(PlayerGameBasic.player_id, PlayerGameBasic.game_id)
            .join(Game, Game.game_id == PlayerGameBasic.game_id)
            .where(
                PlayerGameBasic.team_id == team_id,
                Game.season == ctx.season,
                Game.season_type == REGULAR_SEASON,
                Game.status == "final",
            )
        )
        if before(r.game_id)
    }
    listed = {pid for pid, row in ctx.positions.items() if row.team_id == team_id}
    ids = sorted(candidates | listed)
    if not ids:
        return []

    current: dict[int, list[tuple[datetime, str, int, float, float]]] = {}
    for start in range(0, len(ids), 400):
        for r in session.execute(
            select(
                PlayerGameBasic.player_id,
                PlayerGameBasic.game_id,
                PlayerGameBasic.team_id,
                PlayerGameBasic.minutes,
                PlayerGameBasic.pts,
            )
            .join(Game, Game.game_id == PlayerGameBasic.game_id)
            .where(
                PlayerGameBasic.player_id.in_(ids[start : start + 400]),
                Game.season == ctx.season,
                Game.season_type == REGULAR_SEASON,
                Game.status == "final",
            )
        ):
            if not before(r.game_id) or r.minutes is None or not r.minutes > 0 or r.pts is None:
                continue
            current.setdefault(r.player_id, []).append(
                (start_of[r.game_id], r.game_id, r.team_id, float(r.minutes), float(r.pts))
            )

    last = previous_season_of(session, ctx.season)
    prior: dict[int, tuple[int, float, float]] = {}
    if last is not None:
        for start in range(0, len(ids), 400):
            totals: dict[int, list[float]] = {}
            for r in session.execute(
                select(PlayerGameBasic.player_id, PlayerGameBasic.minutes, PlayerGameBasic.pts)
                .join(Game, Game.game_id == PlayerGameBasic.game_id)
                .where(
                    PlayerGameBasic.player_id.in_(ids[start : start + 400]),
                    Game.season == last,
                    Game.season_type == REGULAR_SEASON,
                    Game.status == "final",
                )
            ):
                if r.minutes is None or not r.minutes > 0 or r.pts is None:
                    continue
                t = totals.setdefault(r.player_id, [0.0, 0.0, 0.0])
                t[0] += 1
                t[1] += float(r.minutes)
                t[2] += float(r.pts)
            for pid, (n, minutes, pts) in totals.items():
                prior[pid] = (int(n), minutes / n, pts / n)

    out: list[tuple[int, float, float]] = []
    for pid in ids:
        lines = sorted(current.get(pid, ()))
        if lines and lines[-1][2] != team_id:
            continue  # his latest team this season is someone else's
        n = len(lines)
        cur_min = math.fsum(x[3] for x in lines) / n if n else None
        cur_pts = math.fsum(x[4] for x in lines) / n if n else None
        before_stats = prior.get(pid)
        minutes = _blend(n, cur_min, before_stats[1] if before_stats else None)
        points = _blend(n, cur_pts, before_stats[2] if before_stats else None)
        if minutes is None or points is None:
            continue
        out.append((pid, minutes, points))
    return out


def side_availability(
    ctx: ReadContext,
    team_id: int,
    game: Game,
    *,
    as_of: datetime,
    level: float,
    detail: bool,
) -> tuple[tuple[SquadLine, ...], InjuryResult | None, int, int, str, str]:
    """``(squad lines, injury result, assumed count, stale-ignored count, report state, basis)``.

    ``detail`` False is the fold's fast path: when no entry is in force for the team the squad is
    not built at all (the layer would return an availability factor of exactly 1), which is what
    keeps a season-long fold cheap while the intel tables are empty. The numbers are identical
    either way.
    """
    book = get_book(ctx)
    start = ctx.start_of(game)
    report_state, _ = book.report_for(team_id, game.game_id, as_of)
    basis = book.assumed_basis(team_id, game.game_id, as_of)
    if not detail:
        in_force = any(
            book.resolve(pid, team_id, game.game_id, as_of, start).model_status is not None
            for pid in book.players_with_entries(team_id)
        )
        if not in_force:
            return (), None, 0, 0, report_state, basis
    squad = squad_stats(ctx, team_id, start)
    table = ctx.chance_table()
    inputs: list[PlayerInput] = []
    resolved: list[Resolved] = []
    chances: list[tuple[float, bool]] = []
    for pid, minutes, points in squad:
        found = book.resolve(pid, team_id, game.game_id, as_of, start)
        chance, assumed = book.chance(found, table)
        inputs.append(PlayerInput(pid, max(minutes, 0.0), max(points, 0.0), chance))
        resolved.append(found)
        chances.append((chance, assumed))
    result = apply_injury_layer(inputs, injury_settings(ctx, level))
    lines = tuple(
        SquadLine(
            player_id=pid,
            team_id=team_id,
            minutes=inp.minutes,
            points=inp.points,
            chance=chance,
            status=found.model_status,
            assumed=assumed,
            resolved=found,
            outcome=outcome,
        )
        for (pid, _, _), inp, found, (chance, assumed), outcome in zip(
            squad, inputs, resolved, chances, result.players
        )
    )
    stale = sum(1 for found in resolved if found.present and not found.in_force)
    return lines, result, sum(1 for line in lines if line.assumed), stale, report_state, basis


def side_absences(
    ctx: ReadContext, side: SideResult, as_of: datetime, *, limit: int | None = ABSENCE_LIMIT
) -> list[dict[str, Any]]:
    """``AbsenceEntry`` rows for the players the model expects to miss time, biggest first.

    Only players with a status *in force* are listed: a player assumed to play is not an absence.
    The numbers are season averages, not engine projections, so a team's projected score and a
    player's are never presented as one sum.
    """
    book = get_book(ctx)
    rows: list[tuple[float, int, dict[str, Any]]] = []
    ctx.load_players(line.player_id for line in side.squad)
    for line in side.squad:
        if line.assumed or line.chance >= 1.0 or line.resolved.row is None:
            continue
        rows.append(
            (
                -line.outcome.expected_points_lost,
                line.player_id,
                {
                    "player": ctx.player_ref(line.player_id, side.team_id),
                    "status": line.status,
                    "chanceOfPlaying": line.chance,
                    "expectedPointsLost": line.outcome.expected_points_lost,
                    "expectedMinutesLost": line.outcome.expected_minutes_lost,
                    "inForce": True,
                    "isStale": book.is_stale(line.resolved, as_of),
                    "source": source_of(line.resolved.row),
                },
            )
        )
    rows.sort(key=lambda item: (item[0], item[1]))
    entries = [entry for _, _, entry in rows]
    return entries if limit is None else entries[:limit]


# --------------------------------------------------------------------------- the model


@dataclass
class Model:
    """The folded model: team states by game group, and what each game was projected at."""

    season: str
    prior: _Prior | None
    regression: float
    offset: float
    league_weight: float
    home_default: float
    home_fixed: bool
    home_fit_min: int
    snapshots: list[Snapshot]
    #: Every regular-season game the fold projected, by game id.
    pre: dict[str, _Pre]
    #: ``(home residual, away residual, margin residual)`` per projected game, regulation-scaled.
    residuals: list[tuple[float, float, float]]

    # ------------------------------------------------------------------ the state in force

    def snapshot_for(self, start: datetime) -> Snapshot:
        """The state after every game whose result was known by ``start``: every game day
        before the Eastern game day ``start`` falls on (:func:`result_known_by`)."""
        day = result_day(start)
        keys = [s.day for s in self.snapshots[1:]]
        index = bisect.bisect_left(keys, day)  # type: ignore[type-var]
        return self.snapshots[index]

    @property
    def final(self) -> Snapshot:
        return self.snapshots[-1]

    def league_level(self, snap: Snapshot) -> float | None:
        current = snap.league_sum / snap.league_n if snap.league_n else None
        return blend_league_level(
            current,
            snap.league_n,
            self.prior.level if self.prior else None,
            previous_weight=self.league_weight,
        )

    def home_advantage(self, snap: Snapshot) -> tuple[float, str]:
        """``(points, provenance)``: the setting when set by hand, the fitted mean margin once 300
        games are final, else the default."""
        if self.home_fixed:
            return self.home_default, "setting"
        n = snap.margin_n + (self.prior.margin_n if self.prior else 0)
        if n >= self.home_fit_min:
            total = snap.margin_sum + (self.prior.margin_sum if self.prior else 0.0)
            return total / n, "derived"
        return self.home_default, "default"

    def ratings(self, team: int, snap: Snapshot, level: float) -> tuple[float, float]:
        """``(pf, pa)``: ``rating(prior, L, r, adj)`` for points for and against."""
        state = snap.teams.get(team, _EMPTY_TEAM)
        prior = self.prior.teams.get(team) if self.prior else None
        if prior is None or self.prior is None:
            prior_pf = prior_pa = level
        else:
            scale = level / self.prior.level
            prior_pf, prior_pa = prior[0] * scale, prior[1] * scale
        return (
            rating(prior_pf, level, self.regression, state.adj_pf),
            rating(prior_pa, level, self.regression, state.adj_pa),
        )

    # ------------------------------------------------------------------ one projection

    def side(
        self,
        ctx: ReadContext,
        team: int,
        game: Game,
        snap: Snapshot,
        level: float,
        *,
        as_of: datetime,
        detail: bool,
    ) -> SideResult:
        """One team's side of ``game``: its ratings in ``snap``, its squad and the injury layer."""
        pf, pa = self.ratings(team, snap, level)
        lines, injury, assumed, stale, report_state, basis = side_availability(
            ctx, team, game, as_of=as_of, level=level, detail=detail
        )
        return SideResult(
            team_id=team,
            pf=pf,
            pa=pa,
            attack=attack_index(pf, level),
            defence=defence_index(pa, level),
            squad=lines,
            injury=injury,
            availability_factor=injury.availability_factor if injury is not None else 1.0,
            assumed_available=assumed,
            stale_ignored=stale,
            report_state=report_state,
            assumed_basis=basis,
        )

    def project(
        self,
        ctx: ReadContext,
        game: Game,
        *,
        as_of: datetime,
        kind: str,
        detail: bool = True,
    ) -> ProjectionResult:
        """Project ``game`` from the state in force at its start and the knowledge at ``as_of``."""
        reason = can_project(game.season, game.season_type)
        if reason is not None:
            raise ProjectionUnavailable(reason)
        for team in (game.home_team_id, game.away_team_id):
            if team not in ctx.teams:
                raise ProjectionUnavailable(f"The store has no team {team}.")
        snap = self.snapshot_for(ctx.start_of(game))
        level = self.league_level(snap)
        if level is None or not level > 0:
            raise ProjectionUnavailable(
                "No scored games and no previous season to start the ratings from."
            )
        h, _ = self.home_advantage(snap)
        advantage = home_advantage_for_game(default=h, override=None, is_neutral=None)
        sides = [
            self.side(ctx, team, game, snap, level, as_of=as_of, detail=detail)
            for team in (game.home_team_id, game.away_team_id)
        ]
        home, away = sides
        team_sd, margin_sd = ctx.setting("teamSd"), ctx.setting("marginSd")
        match = project_match(
            level,
            TeamStrength(home.attack, home.defence, home.availability_factor),
            TeamStrength(away.attack, away.defence, away.availability_factor),
            advantage.points,
            team_sd=team_sd,
            margin_sd=margin_sd,
        )
        cap = CAP_CONSISTENT if ctx.setting("capPolicyConsistent") >= 0.5 else CAP_WORKBOOK
        settings_sha = digest({key: s.value for key, s in sorted(ctx.settings.items())})
        driving = [
            [side.team_id, line.player_id, line.chance]
            for side in sides
            for line in side.squad
            if line.chance < 1.0
        ]
        inputs_sha = digest(
            MODEL_VERSION,
            game.game_id,
            level,
            advantage.points,
            [[s.team_id, s.pf, s.pa, s.availability_factor] for s in sides],
            driving,
            team_sd,
            margin_sd,
            cap,
        )
        newest = get_book(ctx).latest_snapshot(as_of) if detail else None
        return ProjectionResult(
            game_id=game.game_id,
            kind=kind,
            as_of=aware(as_of),  # type: ignore[arg-type]
            league_level=level,
            regression=self.regression,
            home_advantage=advantage,
            home=home,
            away=away,
            match=match,
            team_sd=team_sd,
            margin_sd=margin_sd,
            cap_policy=cap,
            settings_sha256=settings_sha,
            inputs_sha256=inputs_sha,
            snapshot_id=newest.snapshot_id if newest is not None else None,
        )

    def reconstruct(self, ctx: ReadContext, game: Game, *, detail: bool = True) -> ProjectionResult:
        """The projection rebuilt from inputs strictly before the lock deadline (one second
        before it), for a game that has none frozen."""
        return self.project(
            ctx,
            game,
            as_of=ctx.deadline_of(game) - _ONE_SECOND,
            kind="reconstructed",
            detail=detail,
        )

    def pre_scores(self, ctx: ReadContext, game: Game) -> tuple[float, float]:
        """``(home, away)`` projected points for a final game as the fold made them, falling back
        to a fresh reconstruction for a game the fold did not project (a playoff game)."""
        known = self.pre.get(game.game_id)
        if known is not None:
            return known.home_points, known.away_points
        built = self.reconstruct(ctx, game, detail=False)
        return built.match.home_points, built.match.away_points

    # ------------------------------------------------------------------ the fold

    @classmethod
    def build(cls, ctx: ReadContext) -> "Model":
        """Fold this season's regular-season games in order (see the module docstring)."""
        reason = can_project(ctx.season, REGULAR_SEASON)
        if reason is not None:
            raise ProjectionUnavailable(reason)
        regression = ctx.setting("priorRegression")
        offset = ctx.setting("priorWeightGames")
        stored = ctx.settings["homeAdvantagePoints"]
        model = cls(
            season=ctx.season,
            prior=_load_prior(ctx.session, ctx.season),
            regression=regression,
            offset=offset,
            league_weight=ctx.setting("leagueLevelWeight"),
            home_default=stored.value,
            home_fixed=not stored.is_default,
            home_fit_min=PROFILE.home_advantage_fit_min_games or 300,
            snapshots=[Snapshot(start=None, teams={})],
            pre={},
            residuals=[],
        )
        rows = _load_rows(ctx.session, ctx.season, REGULAR_SEASON)
        by_game: dict[str, dict[bool, _Row]] = {}
        for row in rows:
            by_game.setdefault(row.game_id, {})[row.is_home] = row
        folded: list[tuple[datetime, Game, _Row, _Row]] = []
        for game_id, sides in by_game.items():
            game = ctx.games_by_id.get(game_id)
            if game is None or True not in sides or False not in sides:
                continue
            folded.append((ctx.start_of(game), game, sides[True], sides[False]))
        # One group per Eastern game day: none of a day's games is final when the others tip
        # off, so every game of the day is projected from the state before the day.
        folded.sort(key=lambda item: (item[1].game_date, item[0], item[1].game_id))
        index = 0
        while index < len(folded):
            stop = index
            day = folded[index][1].game_date
            while stop < len(folded) and folded[stop][1].game_date == day:
                stop += 1
            model._fold_group(ctx, folded[index:stop])
            index = stop
        return model

    def _fold_group(
        self, ctx: ReadContext, group: Sequence[tuple[datetime, Game, _Row, _Row]]
    ) -> None:
        """Project every game of one group from the state before it, then apply them together."""
        before = self.snapshots[-1]
        level = self.league_level(before)
        teams = dict(before.teams)
        league_sum, league_n = before.league_sum, before.league_n
        margin_sum, margin_n = before.margin_sum, before.margin_n

        def count_game(team: int) -> None:
            state = before.teams.get(team, _EMPTY_TEAM)
            teams[team] = TeamState(state.adj_pf, state.adj_pa, state.games + 1)

        for _, game, home_row, away_row in group:
            home_scored = home_row.pts * home_row.scale
            home_allowed = home_row.opp_pts * home_row.scale
            away_scored = away_row.pts * away_row.scale
            away_allowed = away_row.opp_pts * away_row.scale
            league_sum += home_scored + away_scored
            league_n += 2
            margin_sum += home_row.pts - home_row.opp_pts
            margin_n += 1
            result: ProjectionResult | None = None
            if level is not None and level > 0:
                try:
                    result = self.project(
                        ctx,
                        game,
                        as_of=ctx.deadline_of(game) - _ONE_SECOND,
                        kind="reconstructed",
                        detail=False,
                    )
                except ProjectionUnavailable:
                    result = None
            if result is None:  # nothing to measure against: the game only counts as played
                count_game(home_row.team)
                count_game(away_row.team)
                continue
            match = result.match
            self.pre[game.game_id] = _Pre(game.game_id, match.home_points, match.away_points)
            self.residuals.append(
                (
                    home_scored - match.home_points,
                    away_scored - match.away_points,
                    (home_scored - away_scored) - (match.home_points - match.away_points),
                )
            )
            for row, scored, allowed, proj_scored, proj_allowed in (
                (home_row, home_scored, home_allowed, match.home_points, match.away_points),
                (away_row, away_scored, away_allowed, match.away_points, match.home_points),
            ):
                state = before.teams.get(row.team, _EMPTY_TEAM)
                weight = sequential_weight(state.games + 1, self.offset)
                teams[row.team] = TeamState(
                    update_adjustment(state.adj_pf, scored, proj_scored, weight),
                    update_adjustment(state.adj_pa, allowed, proj_allowed, weight),
                    state.games + 1,
                )
        self.snapshots.append(
            Snapshot(
                start=min(item[0] for item in group),
                teams=teams,
                day=group[0][1].game_date,
                league_sum=league_sum,
                league_n=league_n,
                margin_sum=margin_sum,
                margin_n=margin_n,
            )
        )


def get_model(ctx: ReadContext) -> Model:
    """The season's folded model, built once per ``(data marks)`` and shared."""
    reason = can_project(ctx.season, REGULAR_SEASON)
    if reason is not None:
        raise ProjectionUnavailable(reason)
    return memoise(ctx, "nba-model", None, lambda: Model.build(ctx))


# --------------------------------------------------------------------------- the ledger and views


def ledger_rows_for(ctx: ReadContext, game_id: str) -> list[TeamProjectionLedger]:
    """Every ledger row for a game, oldest first by the time it was computed."""
    return list(
        ctx.session.execute(
            select(TeamProjectionLedger)
            .where(TeamProjectionLedger.game_id == game_id)
            .order_by(TeamProjectionLedger.computed_at, TeamProjectionLedger.ledger_id)
        ).scalars()
    )


def _newest(rows: Sequence[TeamProjectionLedger], kind: str) -> TeamProjectionLedger | None:
    matching = [r for r in rows if r.kind == kind]
    return matching[-1] if matching else None


def current_projection(ctx: ReadContext, model: Model, game: Game) -> ProjectionView | None:
    """The projection a screen shows for ``game``, or ``None`` when there is no honest one.

    * before the lock deadline (the tip-off, or noon Eastern when it is unknown): the model's,
      made now (``latest``);
    * after it, whether the game is waiting for a result or final: the frozen ``locked`` row if
      the ledger has one, else the model's projection rebuilt from inputs strictly before the
      deadline (``reconstructed``).

    A projection made after tip-off is never shown as a prediction: past the deadline the inputs
    are cut off at the deadline whatever the clock says.
    """
    if game.status in ("postponed", "cancelled"):
        return None
    try:
        if not game_has_result(game) and ctx.now < ctx.deadline_of(game):
            return ProjectionView(
                "latest", result=model.project(ctx, game, as_of=ctx.now, kind="latest")
            )
        locked = _newest(ledger_rows_for(ctx, game.game_id), "locked")
        if locked is not None:
            return ProjectionView("locked", ledger=locked)
        return ProjectionView("reconstructed", result=model.reconstruct(ctx, game))
    except ProjectionUnavailable:
        return None


# --------------------------------------------------------------------------- the constants


def model_constants(
    ctx: ReadContext, model: Model | None, level: float | None
) -> list[dict[str, Any]]:
    """The constants behind a projection, each with where it came from."""
    keys = (
        "priorRegression",
        "priorWeightGames",
        "leagueLevelWeight",
        "homeAdvantagePoints",
        "teamSd",
        "marginSd",
        "replacementShare",
        "absorbShare",
        "boostCap",
        "capPolicyConsistent",
    )
    rows: list[dict[str, Any]] = []
    for key in keys:
        setting = ctx.settings[key]
        value, provenance, is_default = setting.value, setting.provenance, setting.is_default
        if key == "homeAdvantagePoints" and model is not None and setting.is_default:
            value, basis = model.home_advantage(model.final)
            provenance = "derived" if basis == "derived" else setting.provenance
            is_default = basis != "derived"
        rows.append({"key": key, "value": value, "provenance": provenance, "isDefault": is_default})
    rows.append(
        {
            "key": "playerPriorGames",
            "value": PLAYER_PRIOR_GAMES,
            "provenance": "default",
            "isDefault": True,
        }
    )
    if level is not None:
        rows.append(
            {
                "key": "leagueAveragePoints",
                "value": level,
                "provenance": "derived",
                "isDefault": False,
            }
        )
    return rows


# --------------------------------------------------------------------------- calibration


@dataclass(frozen=True)
class CalibrationResult:
    source: str | None
    games: int
    fitted: bool
    team_sd: float | None
    margin_sd: float | None
    skipped: tuple[str, ...] = ()
    reason: str | None = None


def _rms(values: Sequence[float]) -> float:
    return math.sqrt(math.fsum(v * v for v in values) / len(values))


def _store_spreads(
    session: Session,
    ctx: ReadContext,
    team_sd: float,
    margin_sd: float,
    provenance: str,
    now: datetime,
) -> tuple[str, ...]:
    """Write the fitted spreads, leaving alone a value a person set (``manual``) and never
    replacing a ledger fit with a previous-season one."""
    skipped: list[str] = []
    patch: dict[str, float] = {}
    for key, value in (("teamSd", team_sd), ("marginSd", margin_sd)):
        current = ctx.settings[key]
        if not current.is_default and (
            current.provenance == "manual"
            or (provenance == "fittedPrevSeason" and current.provenance == "fittedLedger")
        ):
            skipped.append(key)
            continue
        patch[key] = value
    if patch:
        intel_settings.apply_patch(session, patch, provenance=provenance, now=naive_utc(now))
    return tuple(skipped)


def calibrate(session: Session, ctx: ReadContext, now: datetime) -> CalibrationResult:
    """Fit ``teamSd`` and ``marginSd``: from the ledger once 300 locked games have results, else
    from the previous season's walk-forward residuals. Refused for the demo league.

    A spread fitted from invented games would outlive a re-seed (the settings are on the intel
    store) and then be applied to real ones, so nothing is written when the store holds the demo.
    The fit is the root mean square of the residuals, not their standard deviation about the mean,
    so a model that is biased pays for its bias in a wider interval instead of hiding it; the
    residuals are measured against regulation-scaled scores, the scale the projection is made on.
    """
    if ctx.is_demo:
        return CalibrationResult(
            None,
            0,
            False,
            None,
            None,
            reason="Demo league: spreads are not fitted from invented games.",
        )
    from . import ledger

    fit = ledger.ledger_residuals(session, ctx)
    if fit is not None:
        team, margin, games = fit
        skipped = _store_spreads(session, ctx, team, margin, "fittedLedger", now)
        return CalibrationResult("fittedLedger", games, True, team, margin, skipped)

    before = previous_season_of(session, ctx.season)
    if before is None:
        return CalibrationResult(
            None, 0, False, None, None, reason="The store holds no previous season to fit from."
        )
    last = build_context(session, before, now=now)
    try:
        model = get_model(last)
    except ProjectionUnavailable as exc:
        return CalibrationResult(None, 0, False, None, None, reason=exc.reason)
    games = len(model.residuals)
    if games < MIN_FIT_GAMES:
        return CalibrationResult(
            None,
            games,
            False,
            None,
            None,
            reason=f"{games} games of {before}; {MIN_FIT_GAMES} are needed to fit a spread.",
        )
    team = _rms([r for home, away, _ in model.residuals for r in (home, away)])
    margin = _rms([m for _, _, m in model.residuals])
    skipped = _store_spreads(session, ctx, team, margin, "fittedPrevSeason", now)
    return CalibrationResult("fittedPrevSeason", games, True, team, margin, skipped)


def run_calibrate(now: datetime | None = None) -> dict[str, Any]:
    """Worker job ``projections.calibrate`` (daily 05:00) for the NBA."""
    moment = aware(now) if now is not None else datetime.now(timezone.utc)
    try:
        with session_scope() as session:
            try:
                ctx = build_context(session, None, now=moment)
            except Exception as exc:  # noqa: BLE001 - no season loaded yet
                return {"status": "skipped", "reason": str(exc)}
            result = calibrate(session, ctx, ctx.now)
    except Exception as exc:  # noqa: BLE001 - the worker records a failure and carries on
        _LOG.exception("projections.calibrate failed")
        return {"status": "error", "error": str(exc)}
    return {
        "status": "ok" if result.fitted else "skipped",
        "source": result.source,
        "games": result.games,
        "teamSd": result.team_sd,
        "marginSd": result.margin_sd,
        "skipped": list(result.skipped),
        "reason": result.reason,
    }
