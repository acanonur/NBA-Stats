"""Defence by opponent position: where a team's points allowed come from, and how sure we are.

What it measures, and what it does not
--------------------------------------
This measures **points scored by opposing players who are listed at a position**. It does not
measure who guarded whom: a centre listed at C who scores from the perimeter against a guard
counts as a point allowed *to centers*. Every payload carries :data:`CAVEAT` saying so, and the
module is built so a reader can check the one claim it does make: the position buckets add up
to the team's points allowed, exactly, every game.

The allocation
--------------
For a defending team in a final game, each opposing player who played (minutes above zero; a
DNP has no line and contributes nothing) has a position given as bucket weights summing to
one (:mod:`nbastats.shared.positions`). His points are split across buckets by weight, so a
guard-forward's 20 points are 10 and 10. Likewise his minutes, and his attempts. Buckets are
the scheme's (``G``, ``F``, ``C``, or the workbook's five) plus ``unknown``, which is **never
redistributed**: points from players with no listed position stay visible as their own
bucket.

**Reconciliation.** The buckets' points must sum to the opponent's score for the game
(``|sum - opp_pts| < 1e-9``; weights are multiples of one half and exact in binary). A game
that fails, for instance because the box score is missing a player, is excluded and counted
in ``unreconciledGames``, never patched.

The window, and the league reference
------------------------------------
Over a team's season to date, or its last ``window`` reconciled games, the module reports per
bucket the mean points allowed ``a``, the mean opponent minutes ``m``, the sample standard
deviation ``s`` of points allowed per game, and a rate ``r = (sum A / sum M) * regulation
minutes``: points per 48 (NBA) or 40 (EuroLeague) opponent floor-minutes at that position,
which controls for how a team's opponents happened to distribute their minutes. The league
reference is always the whole season scope: ``mu`` (mean points allowed per team-game),
``rho`` (the rate) and ``sigma`` (the share, ``mu`` over the sum of ``mu``).

``deltaPerGame = a - mu`` sums, across buckets, to exactly the team's points allowed per game
minus the league's, an identity the payload states and a test checks. The league table is
sorted by points allowed per game, a recorded fact. **There are no ranks.**

Withholding, in the order it is decided
---------------------------------------
1. ``minimumGames``: fewer than the league's minimum reconciled games in the window.
2. ``positionCoverage``: the share of the team's points allowed that went to unknown
   positions, or the league's, exceeds the ceiling (5%). Coverage is not a nuisance: a team
   whose unknown points are 20% has a guard index that is wrong in an unknowable direction.
3. ``leagueSample``: fewer than 80% of the league's teams meet the minimum, or fewer than two
   teams can be compared, so the spread between teams cannot be estimated.

When any applies, every raw index, standard error and band is ``None``; the raw buckets,
including unknown, still show, because they are recorded facts. Below a second, larger
threshold the table is ``provisional``: indices show, bands do not.

Shrinkage and bands
-------------------
A raw index ``I = a / mu`` for one team at one position is mostly noise after a few dozen
games, so it is shrunk toward 1 by empirical Bayes:

* ``SE = s / sqrt(n) / mu`` (per-game basis). On the per-minute basis the index is
  ``r / rho`` and ``SE`` is the ratio estimator's: ``sqrt(sum_g (A_g - r_hat M_g)^2 /
  (n (n - 1))) / mean(M) * regulation / rho``, with ``r_hat = sum A / sum M``.
* Between-team variance ``tau2 = max(0, var(I) - mean(SE^2))``, over teams that have an
  index, with ``var`` the *sample* variance (the method-of-moments estimate is unbiased only
  with the ``n - 1`` divisor).
* League reliability ``R = tau2 / (tau2 + mean(SE^2))``.
* Shrunk index ``I_hat = 1 + B (I - 1)`` with ``B = tau2 / (tau2 + SE^2)``, and posterior
  spread ``sqrt(tau2 SE^2 / (tau2 + SE^2))``.
* A band (``better`` / ``typical`` / ``worse``; lower points allowed is better) appears only
  when the table is not provisional **and** ``R`` reaches 0.2 at that position. ``better``
  means ``I_hat + z * psd < 1``, ``worse`` means ``I_hat - z * psd > 1``, and ``z =
  Phi^-1(1 - 0.05 / k)`` with ``k`` the number of displayed cells (positions times teams with
  an index): a Bonferroni family, so a league with no real positional signal shows essentially
  no ``better`` or ``worse`` at all.
* When ``tau2 = 0`` every shrunk index is exactly 1 and there are no bands. That is the
  correct, expected answer for much of any season, not a failure; :data:`NO_SIGNAL_MESSAGE`
  says so in words.
* ``method.leagueSignal`` is ``detected`` when at least one displayed cell is ``better`` or
  ``worse``, ``none detected`` when cells could be banded and none was (which includes
  ``tau2 = 0``), and ``None`` when nothing could be tested yet (withheld league-wide, or every
  team still provisional). It is deliberately keyed to the displayed bands and not to the
  reliability gate: a simulated league of identical teams clears a 0.2 reliability by chance in
  about half of all seasons, so a label keyed to it would claim a signal that is not there
  half the time.

Not done in v1, and said in ``method.limitations``: no adjustment for the strength of the
opponents faced (the per-minute basis controls for roster mix only), and the injury layer
never changes defence.

Pure and stdlib-only: :class:`statistics.NormalDist` supplies the normal quantile.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from statistics import NormalDist
from typing import Any, Final, Hashable, Iterable, Mapping, Sequence

from .league_profile import LeagueProfile
from .positions import (
    BASES,
    BASIS_LISTED,
    BASIS_UNKNOWN,
    BASIS_WORKBOOK_LISTING,
    SCHEME_GFC,
    UNKNOWN,
    all_buckets,
    bucket_label,
    buckets_for_scheme,
    is_valid_weights,
)

__all__ = [
    "CAVEAT",
    "IDENTITY",
    "NO_SIGNAL_MESSAGE",
    "LIMITATIONS",
    "RECONCILIATION_TOLERANCE",
    "BASIS_PER_GAME",
    "BASIS_PER_MINUTE",
    "BAND_BETTER",
    "BAND_TYPICAL",
    "BAND_WORSE",
    "WITHHELD_REASONS",
    "OpponentLine",
    "GameAllocation",
    "LeagueReference",
    "Coverage",
    "Withheld",
    "BucketResult",
    "TeamDefense",
    "DefenseMethod",
    "DefenseTable",
    "allocate_game",
    "build_league_reference",
    "bonferroni_z",
    "taxonomy_text",
    "compute_defense",
]

CAVEAT: Final = (
    "Counts points scored by opposing players listed at each position. It does not measure "
    "who guarded whom."
)

IDENTITY: Final = "sum(deltaPerGame) = pointsAllowedPerGame - leaguePointsAllowedPerGame"

NO_SIGNAL_MESSAGE: Final = (
    "No team's points allowed at this position differ from the league by more than chance "
    "this season"
)

LIMITATIONS: Final[tuple[str, ...]] = (
    "Positions are the ones a roster lists for the season, not who guarded whom on the night.",
    "No adjustment is made for the strength of the opponents a team has faced; the per-minute "
    "basis controls for how opponents distributed their minutes, and nothing more.",
    "The injury layer never changes defence: absences move projected scores, not these figures.",
    "Points from players with no listed position are shown as their own bucket and are never "
    "reassigned.",
)

#: A game whose buckets differ from the opponent's score by this much or more is unreconciled.
RECONCILIATION_TOLERANCE: Final = 1e-9

BASIS_PER_GAME: Final = "perGame"
BASIS_PER_MINUTE: Final = "perMinute"
_BASES: Final = (BASIS_PER_GAME, BASIS_PER_MINUTE)

BAND_BETTER: Final = "better"
BAND_TYPICAL: Final = "typical"
BAND_WORSE: Final = "worse"

WITHHELD_REASONS: Final[tuple[str, ...]] = ("minimumGames", "positionCoverage", "leagueSample")

_STAT_FIELDS: Final = ("fga", "fta", "fg3a")


def taxonomy_text(scheme: str) -> str:
    """The sentence that says what the buckets are, for ``method.taxonomy``."""
    if scheme == SCHEME_GFC:
        return "Guard, Forward and Center, as each roster lists a player for the season."
    return (
        "Positions assigned by the workbook author; the EuroLeague registers only Guard, "
        "Forward and Center."
    )


# ----------------------------------------------------------------------- the allocation


@dataclass(frozen=True)
class OpponentLine:
    """One opposing player's line in one game, with his position as bucket weights."""

    minutes: float | None
    pts: float | None
    #: Bucket to weight, summing to one over the scheme's buckets; ``None`` for unknown.
    weights: Mapping[str, float] | None
    #: ``listed`` or ``workbookListing``; irrelevant when ``weights`` is ``None``.
    basis: str = BASIS_LISTED
    fga: float | None = None
    fta: float | None = None
    fg3a: float | None = None


@dataclass(frozen=True)
class GameAllocation:
    """One defending team's game, allocated across the position buckets."""

    game_id: str
    date: date
    team: Hashable
    opp_pts: float
    reconciled: bool
    #: ``|sum of buckets - opp_pts|``; infinite when the game cannot be allocated at all.
    reconciliation_error: float
    points: Mapping[str, float]
    minutes: Mapping[str, float]
    #: Attempts by bucket; ``None`` when any contributing line did not record the stat.
    attempts: Mapping[str, Mapping[str, float | None]]
    #: Points by the basis of the position that placed them (``listed``, ``workbookListing``,
    #: ``unknown``).
    basis_points: Mapping[str, float]


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def allocate_game(
    *,
    game_id: str,
    date: date,  # noqa: A002 - mirrors the payload field
    team: Hashable,
    opp_pts: float,
    lines: Iterable[OpponentLine],
    scheme: str = SCHEME_GFC,
) -> GameAllocation:
    """Allocate one game's opposing lines across ``scheme``'s buckets and reconcile.

    A line whose weights are missing, malformed, or name a bucket outside the scheme is
    treated as unknown: it can only reduce coverage, never move points into a wrong bucket.
    A line with no minutes is skipped; a played line with no points makes the game
    unreconciled, because its points cannot be placed.
    """
    buckets = all_buckets(scheme)
    known = set(buckets_for_scheme(scheme))
    points: dict[str, list[float]] = {b: [] for b in buckets}
    minutes: dict[str, list[float]] = {b: [] for b in buckets}
    stat_terms: dict[str, dict[str, list[float]]] = {
        s: {b: [] for b in buckets} for s in _STAT_FIELDS
    }
    stat_missing: dict[str, set[str]] = {s: set() for s in _STAT_FIELDS}
    basis_points: dict[str, list[float]] = {basis: [] for basis in BASES}
    incomplete = False

    for line in lines:
        played = _finite(line.minutes)
        if played is None or played <= 0:
            continue
        scored = _finite(line.pts)
        if scored is None:
            incomplete = True
            continue
        weights = line.weights
        if weights is None or not is_valid_weights(weights) or not set(weights) <= known:
            weights = {UNKNOWN: 1.0}
            basis = BASIS_UNKNOWN
        elif line.basis in (BASIS_LISTED, BASIS_WORKBOOK_LISTING):
            basis = line.basis
        else:
            basis = BASIS_UNKNOWN
        basis_points[basis].append(scored)
        for bucket, weight in weights.items():
            if weight == 0:
                continue
            points[bucket].append(scored * weight)
            minutes[bucket].append(played * weight)
            for stat in _STAT_FIELDS:
                value = _finite(getattr(line, stat))
                if value is None:
                    stat_missing[stat].add(bucket)
                else:
                    stat_terms[stat][bucket].append(value * weight)

    allocated = {b: math.fsum(v) for b, v in points.items()}
    error = (
        math.inf
        if incomplete or _finite(opp_pts) is None
        else abs(math.fsum(allocated.values()) - float(opp_pts))
    )
    return GameAllocation(
        game_id=game_id,
        date=date,
        team=team,
        opp_pts=float(opp_pts) if _finite(opp_pts) is not None else math.nan,
        reconciled=error < RECONCILIATION_TOLERANCE,
        reconciliation_error=error,
        points=allocated,
        minutes={b: math.fsum(v) for b, v in minutes.items()},
        attempts={
            stat: {
                b: (None if b in stat_missing[stat] else math.fsum(stat_terms[stat][b]))
                for b in buckets
            }
            for stat in _STAT_FIELDS
        },
        basis_points={basis: math.fsum(v) for basis, v in basis_points.items()},
    )


# ---------------------------------------------------------------------- league reference


@dataclass(frozen=True)
class LeagueReference:
    """The whole-season reference every team is compared with."""

    team_games: int
    teams: int
    #: ``mu``: mean points allowed per team-game, by bucket (including ``unknown``).
    mean_points: Mapping[str, float | None]
    #: ``rho``: points per regulation minutes of opponent floor time, by bucket.
    rate: Mapping[str, float | None]
    #: ``sigma``: each bucket's share of points allowed.
    share: Mapping[str, float | None]
    #: Sum of ``mu``: the league's points allowed per game.
    points_allowed_per_game: float | None
    #: ``u_L``: unknown positions' share of league points allowed.
    unknown_share: float | None


def build_league_reference(
    allocations: Iterable[GameAllocation], *, scheme: str, regulation_minutes: int
) -> LeagueReference:
    """The league reference from every reconciled team-game in scope."""
    buckets = all_buckets(scheme)
    games = [g for g in allocations if g.reconciled]
    n = len(games)
    teams = len({g.team for g in games})
    if n == 0:
        none: dict[str, float | None] = {b: None for b in buckets}
        return LeagueReference(0, 0, dict(none), dict(none), dict(none), None, None)
    sum_a = {b: math.fsum(g.points[b] for g in games) for b in buckets}
    sum_m = {b: math.fsum(g.minutes[b] for g in games) for b in buckets}
    mu: dict[str, float | None] = {b: sum_a[b] / n for b in buckets}
    total_mu = math.fsum(v for v in mu.values() if v is not None)
    rho: dict[str, float | None] = {
        b: (sum_a[b] / sum_m[b] * regulation_minutes) if sum_m[b] > 0 else None for b in buckets
    }
    sigma: dict[str, float | None] = {
        b: (mu[b] / total_mu) if total_mu > 0 and mu[b] is not None else None for b in buckets
    }
    total_allowed = math.fsum(g.opp_pts for g in games)
    return LeagueReference(
        team_games=n,
        teams=teams,
        mean_points=mu,
        rate=rho,
        share=sigma,
        points_allowed_per_game=total_mu,
        unknown_share=(sum_a[UNKNOWN] / total_allowed) if total_allowed > 0 else None,
    )


# ------------------------------------------------------------------------ result objects


@dataclass(frozen=True)
class Coverage:
    """Fractions of a team's points allowed by how the scorer's position was established."""

    listed: float | None
    workbook_listing: float | None
    unknown: float | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "listed": self.listed,
            "workbookListing": self.workbook_listing,
            "unknown": self.unknown,
        }


@dataclass(frozen=True)
class Withheld:
    reason: str
    message: str

    def to_payload(self) -> dict[str, Any]:
        return {"reason": self.reason, "message": self.message}


@dataclass(frozen=True)
class BucketResult:
    """One position's row for one team (design section 9.4, ``buckets[]``)."""

    position: str
    points_allowed_per_game: float | None
    league_average: float | None
    delta_per_game: float | None
    opponent_minutes_per_game: float | None
    points_per_regulation_minutes: float | None
    league_rate: float | None
    share: float | None
    league_share: float | None
    raw_index: float | None
    index: float | None
    standard_error: float | None
    band: str | None
    #: Allocated attempts per game over the games that recorded them; not on the wire in v1.
    attempts_per_game: Mapping[str, float | None]

    @property
    def label(self) -> str:
        return bucket_label(self.position)

    def to_payload(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "label": self.label,
            "pointsAllowedPerGame": self.points_allowed_per_game,
            "leagueAverage": self.league_average,
            "deltaPerGame": self.delta_per_game,
            "opponentMinutesPerGame": self.opponent_minutes_per_game,
            "pointsPerRegulationMinutes": self.points_per_regulation_minutes,
            "leagueRate": self.league_rate,
            "share": self.share,
            "leagueShare": self.league_share,
            "rawIndex": self.raw_index,
            "index": self.index,
            "standardError": self.standard_error,
            "band": self.band,
        }


@dataclass(frozen=True)
class TeamDefense:
    """One team's defence by position within a table."""

    team: Hashable
    #: Reconciled games in the window.
    games: int
    #: Reconciled games in the whole season scope.
    season_games: int
    unreconciled_games: int
    window_requested: int | None
    points_allowed_per_game: float | None
    league_points_allowed_per_game: float | None
    buckets: tuple[BucketResult, ...]
    provisional: bool
    withheld: Withheld | None
    coverage: Coverage
    sum_of_buckets: float | None

    @property
    def window_kind(self) -> str:
        return "season" if self.window_requested is None else "lastGames"

    def bucket(self, position: str) -> BucketResult:
        for result in self.buckets:
            if result.position == position:
                return result
        raise KeyError(position)

    def to_payload(self) -> dict[str, Any]:
        """The fields of ``DefenseByPosition`` this module owns (the caller adds the rest)."""
        return {
            "window": {
                "kind": self.window_kind,
                "games": self.games,
                "requested": self.window_requested,
            },
            "pointsAllowedPerGame": self.points_allowed_per_game,
            "leaguePointsAllowedPerGame": self.league_points_allowed_per_game,
            "buckets": [b.to_payload() for b in self.buckets],
            "provisional": self.provisional,
            "withheld": self.withheld.to_payload() if self.withheld else None,
            "coverage": self.coverage.to_payload(),
            "reconciliation": {
                "sumOfBuckets": self.sum_of_buckets,
                "pointsAllowedPerGame": self.points_allowed_per_game,
                "unreconciledGames": self.unreconciled_games,
                "identity": IDENTITY,
            },
        }

    def to_table_row(self) -> dict[str, Any]:
        """A ``DefenseByPositionTable.teams[]`` row without its ``team`` ref."""
        return {
            "games": self.games,
            "pointsAllowedPerGame": self.points_allowed_per_game,
            "buckets": [b.to_payload() for b in self.buckets],
            "provisional": self.provisional,
            "withheld": self.withheld.to_payload() if self.withheld else None,
        }

    def to_summary(self) -> dict[str, Any]:
        """The ``defenseSummary`` a matchup carries for a team."""
        return {
            "pointsAllowedPerGame": self.points_allowed_per_game,
            "buckets": [
                {
                    "position": b.position,
                    "pointsAllowedPerGame": b.points_allowed_per_game,
                    "deltaPerGame": b.delta_per_game,
                    "band": b.band,
                }
                for b in self.buckets
            ],
            "withheld": self.withheld.to_payload() if self.withheld else None,
        }


def bonferroni_z(family_size: int, alpha: float) -> float:
    """``Phi^-1(1 - alpha / k)``: the one-sided critical value for a family of ``k`` cells."""
    if family_size < 1:
        raise ValueError("a family needs at least one cell")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be within (0, 1)")
    return NormalDist().inv_cdf(1.0 - alpha / family_size)


def _band_rule(z: float | None, family: int | None, gate: float) -> str:
    base = (
        "A position shows better or worse only when the table is not provisional, the league's "
        f"reliability at that position is at least {gate:g}, and the shrunk index differs "
        "from 1 by more than z posterior standard deviations"
    )
    if z is None or family is None:
        return base + "."
    return f"{base} (z = {z:.3f}, a Bonferroni family of {family} cells)."


@dataclass(frozen=True)
class DefenseMethod:
    """The ``method`` block: the rules a reader needs to interpret the table."""

    minimum_games: int
    provisional_below_games: int
    coverage_ceiling: float
    #: ``R`` per position; ``None`` where it could not be estimated.
    league_reliability: Mapping[str, float | None]
    #: ``detected`` (some displayed cell is better or worse), ``none detected`` (cells were
    #: testable and none separated) or ``None`` (nothing testable yet).
    league_signal: str | None
    band_rule: str
    position_source: str | None
    taxonomy: str
    limitations: tuple[str, ...]
    z: float | None
    family_size: int | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "minimumGames": self.minimum_games,
            "provisionalBelowGames": self.provisional_below_games,
            "coverageCeiling": self.coverage_ceiling,
            "shrinkage": "empiricalBayes",
            "leagueReliability": dict(self.league_reliability),
            "leagueSignal": self.league_signal,
            "bandRule": self.band_rule,
            "positionSource": self.position_source,
            "taxonomy": self.taxonomy,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class DefenseTable:
    """Every team's defence by position, computed together (the shrinkage needs them all)."""

    scheme: str
    basis: str
    window_requested: int | None
    league: LeagueReference
    method: DefenseMethod
    #: Sorted by points allowed per game ascending, teams without one last. Not a ranking.
    teams: tuple[TeamDefense, ...]

    @property
    def league_signal_message(self) -> str | None:
        return NO_SIGNAL_MESSAGE if self.method.league_signal == "none detected" else None

    def team(self, key: Hashable) -> TeamDefense:
        for entry in self.teams:
            if entry.team == key:
                return entry
        raise KeyError(key)


# ------------------------------------------------------------------------------ the core


def _sample_sd(values: Sequence[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = math.fsum(values) / n
    return math.sqrt(math.fsum((x - mean) ** 2 for x in values) / (n - 1))


def _sample_variance(values: Sequence[float]) -> float:
    n = len(values)
    mean = math.fsum(values) / n
    return math.fsum((x - mean) ** 2 for x in values) / (n - 1)


@dataclass
class _TeamWork:
    """Scratch for one team's window while the table is assembled."""

    team: Hashable
    window: list[GameAllocation]
    season_games: int
    unreconciled: int
    sum_a: dict[str, float]
    sum_m: dict[str, float]
    sum_opp: float
    unknown_share: float | None
    coverage: Coverage

    @property
    def n(self) -> int:
        return len(self.window)


def _team_work(
    team: Hashable,
    rows: Sequence[GameAllocation],
    *,
    buckets: Sequence[str],
    window: int,
) -> _TeamWork:
    seen: set[str] = set()
    for row in rows:
        if row.game_id in seen:
            raise ValueError(f"duplicate game {row.game_id} for team {team!r}")
        seen.add(row.game_id)
    reconciled = sorted(
        (row for row in rows if row.reconciled),
        key=lambda r: (r.date, r.game_id),
        reverse=True,
    )
    chosen = reconciled[:window] if window > 0 else reconciled
    sum_a = {b: math.fsum(g.points[b] for g in chosen) for b in buckets}
    sum_m = {b: math.fsum(g.minutes[b] for g in chosen) for b in buckets}
    sum_opp = math.fsum(g.opp_pts for g in chosen)
    if chosen and sum_opp > 0:
        by_basis = {
            basis: math.fsum(g.basis_points[basis] for g in chosen) / sum_opp for basis in BASES
        }
        coverage = Coverage(
            listed=by_basis[BASIS_LISTED],
            workbook_listing=by_basis[BASIS_WORKBOOK_LISTING],
            unknown=by_basis[BASIS_UNKNOWN],
        )
        unknown_share: float | None = sum_a[UNKNOWN] / sum_opp
    else:
        coverage = Coverage(None, None, None)
        unknown_share = None
    return _TeamWork(
        team=team,
        window=chosen,
        season_games=len(reconciled),
        unreconciled=len(rows) - len(reconciled),
        sum_a=sum_a,
        sum_m=sum_m,
        sum_opp=sum_opp,
        unknown_share=unknown_share,
        coverage=coverage,
    )


def _raw_index_and_se(
    work: _TeamWork,
    bucket: str,
    *,
    basis: str,
    mu: float | None,
    rho: float | None,
    regulation_minutes: int,
) -> tuple[float, float] | None:
    """``(I, SE)`` for one team and position on the chosen basis, or ``None``."""
    n = work.n
    if n < 2:
        return None
    series = [g.points[bucket] for g in work.window]
    if basis == BASIS_PER_GAME:
        if mu is None or not mu > 0:
            return None
        sd = _sample_sd(series)
        if sd is None:
            return None
        return work.sum_a[bucket] / n / mu, sd / math.sqrt(n) / mu
    if rho is None or not rho > 0 or not work.sum_m[bucket] > 0:
        return None
    ratio = work.sum_a[bucket] / work.sum_m[bucket]
    minutes = [g.minutes[bucket] for g in work.window]
    mean_minutes = math.fsum(minutes) / n
    spread = math.fsum((a - ratio * m) ** 2 for a, m in zip(series, minutes))
    se_ratio = math.sqrt(spread / (n * (n - 1))) / mean_minutes
    return ratio * regulation_minutes / rho, se_ratio * regulation_minutes / rho


def _mean_or_none(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def compute_defense(
    team_games: Mapping[Hashable, Sequence[GameAllocation]],
    *,
    profile: LeagueProfile,
    scheme: str = SCHEME_GFC,
    basis: str = BASIS_PER_GAME,
    window: int = 0,
    coverage_ceiling: float | None = None,
    position_source: str | None = None,
    league_team_count: int | None = None,
) -> DefenseTable:
    """Defence by position for every team in ``team_games``, together.

    ``team_games`` maps each defending team to *all* its allocated games in the season scope,
    reconciled or not (the unreconciled ones are counted, not used). ``window`` is ``0`` for
    the season to date or ``N`` for each team's last ``N`` reconciled games; the league
    reference is the whole scope either way. ``coverage_ceiling`` overrides the profile's
    (the ``positionCoverageCeiling`` setting). ``league_team_count`` is the number of teams
    in the league when it exceeds the number present in ``team_games``.
    """
    if basis not in _BASES:
        raise ValueError(f"basis must be one of {', '.join(_BASES)}, got {basis!r}")
    if isinstance(window, bool) or not isinstance(window, int) or window < 0:
        raise ValueError(f"window must be 0 (season) or a positive game count, got {window!r}")
    real = buckets_for_scheme(scheme)
    buckets = all_buckets(scheme)
    rules = profile.defense
    ceiling = rules.unknown_ceiling if coverage_ceiling is None else coverage_ceiling
    regulation = profile.regulation_minutes

    everything = [row for rows in team_games.values() for row in rows]
    league = build_league_reference(everything, scheme=scheme, regulation_minutes=regulation)
    works = {
        team: _team_work(team, rows, buckets=buckets, window=window)
        for team, rows in team_games.items()
    }
    league_count = max(league_team_count or 0, len(team_games))

    meeting = [w for w in works.values() if w.n >= rules.min_games]
    league_coverage_bad = league.unknown_share is not None and league.unknown_share > ceiling
    league_share_bad = league_count == 0 or len(meeting) / league_count < rules.league_sample_share
    eligible = [
        w
        for w in meeting
        if not league_coverage_bad and w.unknown_share is not None and w.unknown_share <= ceiling
    ]
    league_level_ok = not league_coverage_bad and not league_share_bad and len(eligible) >= 2

    # ---- the shrinkage: per position, across eligible teams
    raw: dict[str, dict[Hashable, tuple[float, float]]] = {b: {} for b in real}
    tau2: dict[str, float] = {}
    mean_se2: dict[str, float] = {}
    reliability: dict[str, float | None] = {b: None for b in real}
    if league_level_ok:
        for bucket in real:
            for w in eligible:
                pair = _raw_index_and_se(
                    w,
                    bucket,
                    basis=basis,
                    mu=league.mean_points[bucket],
                    rho=league.rate[bucket],
                    regulation_minutes=regulation,
                )
                if pair is not None:
                    raw[bucket][w.team] = pair
            pairs = list(raw[bucket].values())
            if len(pairs) < 2:
                raw[bucket] = {}
                continue
            se2 = math.fsum(se * se for _, se in pairs) / len(pairs)
            between = max(0.0, _sample_variance([i for i, _ in pairs]) - se2)
            tau2[bucket] = between
            mean_se2[bucket] = se2
            denominator = between + se2
            reliability[bucket] = between / denominator if denominator > 0 else 0.0

    family = len(eligible) * len(real) if league_level_ok else None
    z = bonferroni_z(family, rules.familywise_alpha) if family else None
    gate = rules.reliability_gate

    # ---- assemble each team
    results: list[TeamDefense] = []
    for team, w in works.items():
        n = w.n
        provisional = n < rules.provisional_below
        withheld: Withheld | None = None
        if n < rules.min_games:
            noun = "game" if n == 1 else "games"
            withheld = Withheld("minimumGames", f"Only {n} {noun}, too few to judge a defence")
        elif league_coverage_bad or (w.unknown_share is not None and w.unknown_share > ceiling):
            shown = league.unknown_share if league_coverage_bad else w.unknown_share
            percent = f"{100 * shown:.1f}%" if shown is not None else "too many"
            who = "the league's" if league_coverage_bad else "this team's"
            withheld = Withheld(
                "positionCoverage",
                f"{percent} of {who} points allowed went to players with no listed position, "
                f"above the {100 * ceiling:g}% limit, so position indices are withheld",
            )
        elif not league_level_ok:
            withheld = Withheld(
                "leagueSample",
                "Too few teams have played enough games to tell a defence from league-wide "
                "noise, so position indices are withheld",
            )

        papg = (w.sum_opp / n) if n else None
        total_a = math.fsum(w.sum_a.values())
        cells: list[BucketResult] = []
        for bucket in buckets:
            a = (w.sum_a[bucket] / n) if n else None
            m = (w.sum_m[bucket] / n) if n else None
            r = (
                (w.sum_a[bucket] / w.sum_m[bucket] * regulation)
                if n and w.sum_m[bucket] > 0
                else None
            )
            mu = league.mean_points[bucket]
            raw_index = index = se = band = None
            if withheld is None and bucket in raw and team in raw[bucket]:
                raw_index, se = raw[bucket][team]
                b_tau2 = tau2[bucket]
                se2 = se * se
                denominator = b_tau2 + se2
                weight = b_tau2 / denominator if denominator > 0 else 0.0
                index = 1.0 + weight * (raw_index - 1.0)
                spread = math.sqrt(b_tau2 * se2 / denominator) if denominator > 0 else 0.0
                reliable = reliability[bucket]
                if z is not None and not provisional and reliable is not None and reliable >= gate:
                    if index + z * spread < 1.0:
                        band = BAND_BETTER
                    elif index - z * spread > 1.0:
                        band = BAND_WORSE
                    else:
                        band = BAND_TYPICAL
            attempts: dict[str, float | None] = {}
            for stat in _STAT_FIELDS:
                recorded = [
                    g.attempts[stat][bucket]
                    for g in w.window
                    if g.attempts[stat][bucket] is not None
                ]
                attempts[stat] = _mean_or_none([v for v in recorded if v is not None])
            cells.append(
                BucketResult(
                    position=bucket,
                    points_allowed_per_game=a,
                    league_average=mu,
                    delta_per_game=(a - mu) if a is not None and mu is not None else None,
                    opponent_minutes_per_game=m,
                    points_per_regulation_minutes=r,
                    league_rate=league.rate[bucket],
                    share=(w.sum_a[bucket] / total_a) if n and total_a > 0 else None,
                    league_share=league.share[bucket],
                    raw_index=raw_index,
                    index=index,
                    standard_error=se,
                    band=band,
                    attempts_per_game=attempts,
                )
            )
        results.append(
            TeamDefense(
                team=team,
                games=n,
                season_games=w.season_games,
                unreconciled_games=w.unreconciled,
                window_requested=window if window > 0 else None,
                points_allowed_per_game=papg,
                league_points_allowed_per_game=league.points_allowed_per_game,
                buckets=tuple(cells),
                provisional=provisional,
                withheld=withheld,
                coverage=w.coverage,
                sum_of_buckets=total_a / n if n else None,
            )
        )

    testable = any(t.withheld is None and not t.provisional for t in results)
    if not league_level_ok or not testable:
        signal: str | None = None
    elif any(b.band in (BAND_BETTER, BAND_WORSE) for t in results for b in t.buckets):
        signal = "detected"
    else:
        signal = "none detected"

    results.sort(
        key=lambda t: (
            t.points_allowed_per_game is None,
            t.points_allowed_per_game if t.points_allowed_per_game is not None else 0.0,
            str(t.team),
        )
    )
    method = DefenseMethod(
        minimum_games=rules.min_games,
        provisional_below_games=rules.provisional_below,
        coverage_ceiling=ceiling,
        league_reliability=reliability,
        league_signal=signal,
        band_rule=_band_rule(z, family, gate),
        position_source=position_source,
        taxonomy=taxonomy_text(scheme),
        limitations=LIMITATIONS,
        z=z,
        family_size=family,
    )
    return DefenseTable(
        scheme=scheme,
        basis=basis,
        window_requested=window if window > 0 else None,
        league=league,
        method=method,
        teams=tuple(results),
    )
