"""Nine-category fantasy valuation: what a player is worth, and what a trade does.

Built from the user's ``Fantasy NBA 2026-27 Toolkit`` workbook, whose Method sheet cites the
same monograph this project's projection engine does — the Table B.3 stabilisation constants in
:mod:`nbastats.projection_constants` and the ones in that workbook are the same numbers. This
module implements the workbook's valuation, and three decisions in it are worth stating up
front because each was arrived at by ruling something else out.

**It reads season aggregates, not game logs.** A draft board values 150–460 players at once.
Running the next-game projection per player costs ~5 queries and ~4ms each — 2,367 queries and
3.1 seconds at 460 players, and it structurally cannot produce FG% or FT% because the engine
projects no attempts. Reading ``player_season`` instead costs a handful of queries flat, and
agrees with the projection path at Spearman 0.99 with 145 of the top 150 shared. The board is a
ranking; 0.99 rank agreement for 1/80th of the cost is the right trade.

**It adds no projection constants.** The monograph supplies a league rate and a stabilisation
constant for seven counting stats and *nothing* for FGA or FTA, so projecting attempt volume
would mean inventing two numbers under a citation the paper does not support. It is not needed:
the percentages shrink against **attempts**, which is exactly what Table B.5 (already in
:data:`nbastats.projection_constants.PCT_STABILISATION`, until now unused) gives, and the
attempts themselves come from the season line rather than from a model.

**It reports a sensitivity range, never a probabilistic interval.** The engine's negative
binomial interval is a *single-game count* for *one player in one category*, fitted on
single-game residuals. A trade delta is a signed sum over up to eight players and nine
standardised categories; an honest variance for it needs a 72x72 covariance matrix where this
repo has a 3x3 for one player, and the missing assumptions push the band in opposite directions
— so it would not even be conservative. :func:`trade_sensitivity` recomputes the whole delta
under a handful of *named, enumerable* scenarios and reports the spread with the scenario that
produced each end. Nothing distributional is claimed, and the reader is told which assumption
moved the number, which the spreadsheet cannot do.

See :doc:`docs/FANTASY.md`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from . import projection_constants as C

__all__ = [
    "CATEGORIES",
    "COUNTING_CATEGORIES",
    "PERCENTAGE_CATEGORIES",
    "NEGATIVE_CATEGORIES",
    "PERCENTAGE_PARTS",
    "ESPN_POINTS",
    "YAHOO_POINTS",
    "SCORING_SYSTEMS",
    "DEFAULT_POOL_SIZE",
    "DEFAULT_REPLACEMENT_DEPTH",
    "DEFAULT_BANDS",
    "SeasonLine",
    "CategoryValue",
    "PlayerValuation",
    "ValuedPool",
    "VerdictBands",
    "TradeSide",
    "TradeResult",
    "Scenario",
    "SensitivityRange",
    "FULL_SEASON_GAMES",
    "DraftPick",
    "DraftBoard",
    "shrunk_pct",
    "value_pool",
    "fantasy_points",
    "evaluate_trade",
    "trade_sensitivity",
    "build_draft_board",
    "STANDARD_SCENARIOS",
]

# --------------------------------------------------------------------------- categories

#: The nine categories, in the order the workbook lists them and every UI should show them.
CATEGORIES: tuple[str, ...] = (
    "pts", "fg3m", "reb", "ast", "stl", "blk", "tov", "fg_pct", "ft_pct",
)

#: The six plain counting categories plus turnovers: standardised directly from a per-game value.
COUNTING_CATEGORIES: tuple[str, ...] = ("pts", "fg3m", "reb", "ast", "stl", "blk", "tov")

#: The two that are ratios, and therefore need the volume-weighted impact treatment below.
PERCENTAGE_CATEGORIES: tuple[str, ...] = ("fg_pct", "ft_pct")

#: Categories where less is better. The z is negated *after* standardising, which is identical
#: to negating the value first but keeps the pool's mean and SD describing the real statistic.
NEGATIVE_CATEGORIES: frozenset[str] = frozenset({"tov"})

#: ``category -> (makes attribute, attempts attribute)`` on a season line.
PERCENTAGE_PARTS: Mapping[str, tuple[str, str]] = {
    "fg_pct": ("fgm", "fga"),
    "ft_pct": ("ftm", "fta"),
}

# --------------------------------------------------------------------------- points leagues

#: ESPN's default head-to-head points scoring, from the workbook's Settings sheet.
ESPN_POINTS: Mapping[str, float] = {
    "pts": 1.0, "fg3m": 1.0, "fgm": 2.0, "fga": -1.0, "ftm": 1.0, "fta": -1.0,
    "reb": 1.0, "ast": 2.0, "stl": 4.0, "blk": 4.0, "tov": -2.0,
}

#: Yahoo's default points scoring. Note this is *not* the same as the ``fantasy_pts`` metric the
#: rest of the app carries: that one is NBA.com's own ``NBA_FANTASY_PTS`` formula, which scores
#: rebounds at 1.2 and assists at 1.5 like Yahoo but has no three-pointer or shooting terms.
YAHOO_POINTS: Mapping[str, float] = {
    "pts": 1.0, "reb": 1.2, "ast": 1.5, "stl": 3.0, "blk": 3.0, "tov": -1.0,
}

SCORING_SYSTEMS: Mapping[str, Mapping[str, float]] = {
    "espn_points": ESPN_POINTS,
    "yahoo_points": YAHOO_POINTS,
}

# --------------------------------------------------------------------------- pool defaults

#: How many players are standardised against each other. The workbook's default, and roughly a
#: 12-team league's drafted population.
DEFAULT_POOL_SIZE = 150

#: The replacement level is the mean of this many players ranked just after the pool — what a
#: freed roster spot actually gets refilled with.
DEFAULT_REPLACEMENT_DEPTH = 12

#: Smallest attempts figure treated as real volume. Below it a percentage is all prior.
_MIN_ATTEMPTS = 1e-9


# --------------------------------------------------------------------------- inputs


@dataclass(frozen=True, slots=True)
class SeasonLine:
    """One player's per-game season line — everything the valuation needs, and nothing else.

    Deliberately a plain value object rather than a ``PlayerSeason`` row, so the same engine
    values a database season, a hand-edited projection, or a line imported from a spreadsheet.
    That last one matters: a manager's own projections, with their own role and injury
    judgements, are better input than any model here can produce.
    """

    player_id: int
    name: str = ""
    team_abbr: str | None = None
    position: str | None = None
    games_played: int = 0
    #: Games this line is projected to play. Defaults to ``games_played`` when not supplied.
    games_projected: float | None = None
    minutes_per_game: float = 0.0
    pts: float = 0.0
    fg3m: float = 0.0
    reb: float = 0.0
    ast: float = 0.0
    stl: float = 0.0
    blk: float = 0.0
    tov: float = 0.0
    fgm: float = 0.0
    fga: float = 0.0
    ftm: float = 0.0
    fta: float = 0.0

    @property
    def games(self) -> float:
        """The games the valuation should use, which is the projection when one was given."""
        projected = self.games_projected
        if projected is not None and projected > 0:
            return float(projected)
        return float(self.games_played)

    def counting(self, category: str) -> float:
        return float(getattr(self, category, 0.0) or 0.0)

    def parts(self, category: str) -> tuple[float, float]:
        """``(makes per game, attempts per game)`` for a percentage category."""
        makes_attr, attempts_attr = PERCENTAGE_PARTS[category]
        return (
            float(getattr(self, makes_attr, 0.0) or 0.0),
            float(getattr(self, attempts_attr, 0.0) or 0.0),
        )

    def scaled(self, *, minutes: float = 1.0, games: float | None = None) -> "SeasonLine":
        """A copy with every rate stat multiplied by ``minutes``, for the scenario sweep.

        Scaling per-game production by a minutes factor is the crudest possible workload model
        and it is only ever used to bound a sensitivity range, never to produce a projection.
        """
        if minutes == 1.0 and games is None:
            return self
        scale = float(minutes)
        return SeasonLine(
            player_id=self.player_id,
            name=self.name,
            team_abbr=self.team_abbr,
            position=self.position,
            games_played=self.games_played,
            games_projected=self.games if games is None else float(games),
            minutes_per_game=self.minutes_per_game * scale,
            pts=self.pts * scale,
            fg3m=self.fg3m * scale,
            reb=self.reb * scale,
            ast=self.ast * scale,
            stl=self.stl * scale,
            blk=self.blk * scale,
            tov=self.tov * scale,
            fgm=self.fgm * scale,
            fga=self.fga * scale,
            ftm=self.ftm * scale,
            fta=self.fta * scale,
        )


# --------------------------------------------------------------------------- percentages


@dataclass(frozen=True, slots=True)
class ShrunkPct:
    """A shooting percentage regressed toward the league rate by **attempts**.

    Paper Table B.5, and the first production use of :data:`PCT_STABILISATION`. Attempts rather
    than minutes is the whole point: a percentage is a binomial ratio, so the exposure that
    stabilises it is the number of trials, not the time on court. FT% settles in 25 attempts and
    FG% in 129, a factor of five apart, which is why one constant for both would be wrong twice.
    """

    category: str
    value: float
    league_value: float
    attempts: float
    k_attempts: float

    @property
    def weight(self) -> float:
        """How much of the estimate is the player rather than the league prior."""
        total = self.attempts + self.k_attempts
        return 0.0 if total <= 0 else self.attempts / total


def shrunk_pct(category: str, makes: float, attempts: float, league_value: float) -> ShrunkPct:
    """``(makes + k*p_league) / (attempts + k)`` — the padding estimator in attempts."""
    k = C.pct_stabilisation_attempts(category)
    total = float(attempts) + k
    value = league_value if total <= 0 else (float(makes) + k * league_value) / total
    return ShrunkPct(category, value, league_value, float(attempts), k)


# --------------------------------------------------------------------------- valuation


@dataclass(frozen=True, slots=True)
class CategoryValue:
    """One player in one category: the raw number, what it is worth, and why."""

    category: str
    #: The per-game statistic as the reader knows it: 26.8 points, or 0.571 for FG%.
    value: float
    #: The quantity that was actually standardised. Identical to ``value`` for a counting
    #: category; for a percentage it is the volume-weighted impact, which is a different thing
    #: and must never be shown as though it were a shooting percentage.
    standardised: float
    z: float
    #: Percentage categories only: attempts per game, the volume the impact is weighted by.
    attempts: float | None = None
    #: Percentage categories only: the rate after shrinkage, which is what ``value`` reports.
    shrinkage_weight: float | None = None

    @property
    def is_percentage(self) -> bool:
        return self.category in PERCENTAGE_CATEGORIES


@dataclass(frozen=True, slots=True)
class PlayerValuation:
    """A player's nine z-scores, plus the two points-league totals."""

    line: SeasonLine
    categories: Mapping[str, CategoryValue]
    espn_points: float
    yahoo_points: float
    #: Rank within the valued population by unweighted total z. Stable regardless of punts.
    baseline_rank: int = 0
    in_pool: bool = False

    @property
    def player_id(self) -> int:
        return self.line.player_id

    def z(self, category: str) -> float:
        found = self.categories.get(category)
        return 0.0 if found is None else found.z

    def total_z(self, weights: Mapping[str, float] | None = None) -> float:
        """The weighted **sum** of the nine z — the Basketball Monster convention."""
        w = weights or {}
        return sum(self.z(c) * float(w.get(c, 1.0)) for c in CATEGORIES)

    def score(self, weights: Mapping[str, float] | None = None) -> float:
        """The weighted **mean**. Same information as ``total_z``, a ninth of the scale.

        Both are reported because the workbook uses both and they are easy to confuse: a
        verdict band written for one is off by a factor of nine against the other.
        """
        w = weights or {}
        total = sum(float(w.get(c, 1.0)) for c in CATEGORIES)
        return 0.0 if total <= 0 else self.total_z(weights) / total

    def points(self, scoring: str, weights: Mapping[str, float] | None = None) -> float:
        if scoring == "espn_points":
            return self.espn_points
        if scoring == "yahoo_points":
            return self.yahoo_points
        return self.total_z(weights)


@dataclass(frozen=True, slots=True)
class ValuedPool:
    """Every player valued against a common pool.

    The z-scores here are **weight-independent on purpose**. Weights and punts are applied when
    a total is read, not when the pool is built, so a punt-FT roster and a balanced roster see
    the same per-category numbers and a trade between them is symmetric. It also means one pool
    computation serves every widget in a request.
    """

    players: Mapping[int, PlayerValuation]
    pool_ids: tuple[int, ...]
    #: ``category -> (mean, population sd)`` of the standardised quantity over the pool.
    moments: Mapping[str, tuple[float, float]]
    #: ``category -> the pool's aggregate make rate`` for the two percentage categories.
    pool_pct: Mapping[str, float]
    #: ``category -> the league's aggregate make rate``, the prior the shrinkage pulls toward.
    league_pct: Mapping[str, float]
    replacement_ids: tuple[int, ...]
    pool_size: int
    season: str = ""
    season_type: str = "Regular Season"

    def get(self, player_id: int) -> Optional[PlayerValuation]:
        return self.players.get(int(player_id))

    def ranked(self, weights: Mapping[str, float] | None = None) -> list[PlayerValuation]:
        """Every valued player, best first, under these weights."""
        return sorted(self.players.values(), key=lambda v: -v.total_z(weights))

    def replacement(self, weights: Mapping[str, float] | None = None) -> float:
        """The total z of the player a freed roster spot actually gets refilled with.

        The mean of the players ranked just after the pool — below pool average by
        construction, so this number is negative in a normal league. That is not a quirk: it is
        why giving away two players for one costs you something even when the one is better.
        """
        if not self.replacement_ids:
            return 0.0
        values = [self.players[pid].total_z(weights) for pid in self.replacement_ids]
        return sum(values) / len(values)

    def replacement_points(self, scoring: str) -> float:
        if not self.replacement_ids:
            return 0.0
        values = [self.players[pid].points(scoring) for pid in self.replacement_ids]
        return sum(values) / len(values)

    def spread(self, weights: Mapping[str, float] | None = None) -> float:
        """Population SD of total z across the pool — the scale a verdict band lives on.

        Reported so a band can be read against the league it was set for rather than taken on
        faith. A "fair trade" threshold of 0.75 means something quite different when the pool's
        own spread is 2.8 than when it is 0.9.
        """
        if not self.pool_ids:
            return 0.0
        values = [self.players[pid].total_z(weights) for pid in self.pool_ids]
        return _population_sd(values)


def _population_sd(values: Sequence[float]) -> float:
    """Population SD. The pool is the population of interest, not a sample from a larger one."""
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(max(variance, 0.0))


def fantasy_points(line: SeasonLine, scoring: Mapping[str, float]) -> float:
    """A points-league score for one per-game line."""
    total = 0.0
    for stat, weight in scoring.items():
        total += float(getattr(line, stat, 0.0) or 0.0) * float(weight)
    return total


def value_pool(
    lines: Iterable[SeasonLine],
    *,
    pool_size: int = DEFAULT_POOL_SIZE,
    replacement_depth: int = DEFAULT_REPLACEMENT_DEPTH,
    season: str = "",
    season_type: str = "Regular Season",
) -> ValuedPool:
    """Standardise every line against the top ``pool_size`` of them.

    Two passes, because the pool is defined by a ranking that the pool itself produces. The
    first pass ranks on raw fantasy points, which needs no pool; the second standardises against
    the top ``pool_size`` of that ranking. The workbook does the same thing and calls the first
    pass a "fixed baseline rank ... so the formulas never loop".
    """
    rows = [line for line in lines if line is not None]
    if not rows:
        return ValuedPool({}, (), {}, {}, {}, (), pool_size, season, season_type)

    # The league rate is the shrinkage prior, so it is the raw aggregate over everybody.
    league_pct = _aggregate_pct(rows)

    # Pass one: an exogenous ranking, so pool membership never depends on the pool.
    seeded = sorted(rows, key=lambda ln: -fantasy_points(ln, YAHOO_POINTS))
    pool_lines = seeded[: max(int(pool_size), 1)]

    # The pool rate the impact is measured against must be the aggregate of the *same*
    # quantities the impact uses — the shrunk rates, not the raw ones. Mixing the two makes the
    # impacts sum to something other than zero over the pool, which is not a rounding artifact:
    # every player's rate is pulled toward the league and the pool sits above the league, so the
    # whole pool would read as below-average shooters. _pool_shrunk_pct closes that.
    pool_pct = _pool_shrunk_pct(pool_lines, league_pct)

    # Pass two: the moments every z is measured against.
    standardised: dict[str, list[float]] = {}
    for category in CATEGORIES:
        standardised[category] = [
            _standardised_value(line, category, pool_pct, league_pct) for line in pool_lines
        ]
    moments = {
        category: (
            sum(values) / len(values) if values else 0.0,
            _population_sd(values),
        )
        for category, values in standardised.items()
    }
    # The identity the percentage baseline exists to satisfy. Cheap to check, and it catches
    # the one bug in this module that would be invisible on screen: every z quietly shifted by
    # a constant, which looks like a plausible board and ranks a volume shooter wrongly.
    for category in PERCENTAGE_CATEGORIES:
        drift = sum(standardised[category])
        assert abs(drift) < 1e-6, f"{category} impacts sum to {drift:+.9f} over the pool, not 0"

    players: dict[int, PlayerValuation] = {}
    for line in rows:
        categories: dict[str, CategoryValue] = {}
        for category in CATEGORIES:
            categories[category] = _category_value(line, category, pool_pct, league_pct, moments)
        players[line.player_id] = PlayerValuation(
            line=line,
            categories=categories,
            espn_points=fantasy_points(line, ESPN_POINTS),
            yahoo_points=fantasy_points(line, YAHOO_POINTS),
        )

    # Baseline rank is unweighted, so it is a stable identity for a player rather than a
    # property of one manager's punts.
    ordered = sorted(players.values(), key=lambda v: -v.total_z())
    pool_ids: list[int] = []
    ranked: dict[int, PlayerValuation] = {}
    for index, valuation in enumerate(ordered, start=1):
        in_pool = index <= pool_size
        ranked[valuation.player_id] = PlayerValuation(
            line=valuation.line,
            categories=valuation.categories,
            espn_points=valuation.espn_points,
            yahoo_points=valuation.yahoo_points,
            baseline_rank=index,
            in_pool=in_pool,
        )
        if in_pool:
            pool_ids.append(valuation.player_id)

    replacement_ids = tuple(
        v.player_id for v in ordered[pool_size : pool_size + max(int(replacement_depth), 0)]
    )

    return ValuedPool(
        players=ranked,
        pool_ids=tuple(pool_ids),
        moments=moments,
        pool_pct=pool_pct,
        league_pct=league_pct,
        replacement_ids=replacement_ids,
        pool_size=int(pool_size),
        season=season,
        season_type=season_type,
    )


def _aggregate_pct(lines: Sequence[SeasonLine]) -> dict[str, float]:
    """``sum(makes) / sum(attempts)`` per percentage category — a ratio, never a mean of ratios.

    Averaging players' percentages would give a 12-attempt shooter the same say as a 20-attempt
    one, and the pool rate is meant to be the rate a roster actually posts.
    """
    out: dict[str, float] = {}
    for category in PERCENTAGE_CATEGORIES:
        makes = 0.0
        attempts = 0.0
        for line in lines:
            made, tried = line.parts(category)
            makes += made
            attempts += tried
        out[category] = makes / attempts if attempts > _MIN_ATTEMPTS else 0.0
    return out


def _pool_shrunk_pct(
    lines: Sequence[SeasonLine], league_pct: Mapping[str, float]
) -> dict[str, float]:
    """The pool's rate, as the attempts-weighted aggregate of its players' **shrunk** rates.

    This is what makes ``sum(impact) == 0`` over the pool an identity rather than an
    approximation, and :func:`value_pool` asserts it. Weighting by attempts rather than taking a
    mean of rates is the same choice as in :func:`_aggregate_pct`, and for the same reason: the
    pool rate should be the rate a roster made of the pool would actually post.
    """
    out: dict[str, float] = {}
    for category in PERCENTAGE_CATEGORIES:
        weighted = 0.0
        attempts_total = 0.0
        for line in lines:
            _makes_pg, attempts_pg = line.parts(category)
            weighted += attempts_pg * _shrunk_for(line, category, league_pct).value
            attempts_total += attempts_pg
        out[category] = (
            weighted / attempts_total
            if attempts_total > _MIN_ATTEMPTS
            else league_pct.get(category, 0.0)
        )
    return out


def _shrunk_for(
    line: SeasonLine, category: str, league_pct: Mapping[str, float]
) -> ShrunkPct:
    """One player's shrunk rate, with the season's attempts as the exposure."""
    makes_pg, attempts_pg = line.parts(category)
    return shrunk_pct(
        category,
        makes_pg * line.games,
        attempts_pg * line.games,
        league_pct.get(category, 0.0),
    )


def _standardised_value(
    line: SeasonLine,
    category: str,
    pool_pct: Mapping[str, float],
    league_pct: Mapping[str, float],
) -> float:
    """The quantity that gets standardised — a per-game count, or a percentage impact."""
    if category not in PERCENTAGE_CATEGORIES:
        return line.counting(category)

    _makes_pg, attempts_pg = line.parts(category)
    # Exposure for the shrinkage is the season's attempts, not one game's.
    shrunk = _shrunk_for(line, category, league_pct)
    # Impact = attempts x (this player's rate - the pool's rate). Volume-weighted, so a 90%
    # free-throw shooter taking one attempt a game moves the category by almost nothing, which
    # is the whole reason a plain z of the percentage is wrong.
    return attempts_pg * (shrunk.value - pool_pct.get(category, 0.0))


def _category_value(
    line: SeasonLine,
    category: str,
    pool_pct: Mapping[str, float],
    league_pct: Mapping[str, float],
    moments: Mapping[str, tuple[float, float]],
) -> CategoryValue:
    standardised = _standardised_value(line, category, pool_pct, league_pct)
    mean, sd = moments.get(category, (0.0, 0.0))
    z = 0.0 if sd <= 0 else (standardised - mean) / sd
    if category in NEGATIVE_CATEGORIES:
        z = -z

    if category not in PERCENTAGE_CATEGORIES:
        return CategoryValue(category=category, value=standardised, standardised=standardised, z=z)

    _makes_pg, attempts_pg = line.parts(category)
    shrunk = _shrunk_for(line, category, league_pct)
    return CategoryValue(
        category=category,
        value=shrunk.value,
        standardised=standardised,
        z=z,
        attempts=attempts_pg,
        shrinkage_weight=shrunk.weight,
    )


# --------------------------------------------------------------------------- trades


@dataclass(frozen=True, slots=True)
class VerdictBands:
    """Where "fair" ends and "clear win" begins, on each of the two scales.

    The workbook's defaults are kept because they are the user's own league's calibration, but
    they are **configurable and reported alongside the pool's realised spread** rather than
    presented as universal. A z-sum band of 0.75 means one thing in a pool whose own spread is
    0.9 and something else entirely in a pool whose spread is 2.8; a band quoted without the
    scale it lives on is a number pretending to be a judgement.
    """

    #: On the weighted **sum** of the nine z, per game.
    fair_z: float = 0.75
    clear_z: float = 2.0
    #: On fantasy points per game.
    fair_points: float = 3.0
    clear_points: float = 8.0

    def verdict(self, net: float, *, scale: str) -> str:
        fair = self.fair_z if scale == "categories" else self.fair_points
        clear = self.clear_z if scale == "categories" else self.clear_points
        size = abs(net)
        if size < fair:
            return "fair"
        direction = "win" if net > 0 else "loss"
        return f"clear {direction}" if size >= clear else ("slight " + direction)


DEFAULT_BANDS = VerdictBands()


@dataclass(frozen=True, slots=True)
class TradeSide:
    """One side of a trade: the players, and what they add up to."""

    label: str
    valuations: tuple[PlayerValuation, ...]
    missing: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.valuations)

    def category_total(
        self, category: str, availability: Mapping[int, float] | None = None
    ) -> float:
        return sum(
            v.z(category) * _availability_of(v, availability) for v in self.valuations
        )

    def total_z(
        self,
        weights: Mapping[str, float] | None = None,
        availability: Mapping[int, float] | None = None,
    ) -> float:
        return sum(
            v.total_z(weights) * _availability_of(v, availability) for v in self.valuations
        )

    def points(self, scoring: str, availability: Mapping[int, float] | None = None) -> float:
        return sum(
            v.points(scoring) * _availability_of(v, availability) for v in self.valuations
        )


@dataclass(frozen=True, slots=True)
class TradeResult:
    """What a trade does, per category and in total."""

    give: TradeSide
    get: TradeSide
    #: ``category -> (give total, get total, change, net after the roster adjustment)``.
    categories: Mapping[str, tuple[float, float, float, float]]
    #: ``get - give``, before the roster-spot adjustment.
    change_z: float
    #: ``(n_give - n_get) x replacement value``. Its own field, never folded into the total:
    #: it routinely dominates an uneven trade, and a reader who cannot see it has no way to
    #: tell a bad trade from simple slot arithmetic.
    roster_adjustment: float
    net_z: float
    replacement_value: float
    change_points: Mapping[str, float]
    net_points: Mapping[str, float]
    verdict: str
    points_verdicts: Mapping[str, str]
    bands: VerdictBands
    #: The pool's own spread of total z, so the bands can be read against the league.
    pool_spread: float
    notes: tuple[str, ...] = ()

    @property
    def category_verdicts(self) -> dict[str, str]:
        """Per-category gain / loss / level, at a quarter of a standard deviation."""
        out: dict[str, str] = {}
        for category, (_give, _get, _change, net) in self.categories.items():
            if net > 0.25:
                out[category] = "gain"
            elif net < -0.25:
                out[category] = "loss"
            else:
                out[category] = "level"
        return out


#: A full season, the denominator an availability weight is measured against.
FULL_SEASON_GAMES = 82.0


def _availability_of(
    valuation: PlayerValuation, availability: Mapping[int, float] | None
) -> float:
    """How much of this player you actually get, as a fraction of a full season.

    1.0 unless a caller says otherwise. The sensitivity sweep is the only thing that sets it,
    because availability is exactly the assumption a per-game valuation cannot see: a player's
    per-game line is unchanged by missing twenty games, but what you receive is not.
    """
    if not availability:
        return 1.0
    return float(availability.get(valuation.player_id, 1.0))


def evaluate_trade(
    pool: ValuedPool,
    give_ids: Sequence[int],
    get_ids: Sequence[int],
    *,
    weights: Mapping[str, float] | None = None,
    bands: VerdictBands = DEFAULT_BANDS,
    scoring: str = "categories",
    availability: Mapping[int, float] | None = None,
) -> TradeResult:
    """Compare what you give with what you get, category by category.

    Everything stays in **per-game** units end to end, which is the basis the verdict bands are
    written against. Season totals are a draft-board ranking device; using them here would
    change the scale of the answer without changing the question.
    """
    give_side, give_missing = _side("give", pool, give_ids)
    get_side, get_missing = _side("get", pool, get_ids)

    categories: dict[str, tuple[float, float, float, float]] = {}
    weight_map = weights or {}
    roster_delta = give_side.count - get_side.count
    replacement = pool.replacement(weights)
    roster_adjustment = roster_delta * replacement

    for category in CATEGORIES:
        gave = give_side.category_total(category, availability)
        got = get_side.category_total(category, availability)
        change = got - gave
        # The roster adjustment is a whole-roster effect, not a per-category one; spreading it
        # evenly across the nine is the only division that does not invent a category story.
        share = roster_adjustment / len(CATEGORIES)
        weight = float(weight_map.get(category, 1.0))
        categories[category] = (gave, got, change, change * weight + share)

    change_z = get_side.total_z(weights, availability) - give_side.total_z(weights, availability)
    net_z = change_z + roster_adjustment

    change_points: dict[str, float] = {}
    net_points: dict[str, float] = {}
    points_verdicts: dict[str, str] = {}
    for system in SCORING_SYSTEMS:
        delta = get_side.points(system, availability) - give_side.points(system, availability)
        adjustment = roster_delta * pool.replacement_points(system)
        change_points[system] = delta
        net_points[system] = delta + adjustment
        points_verdicts[system] = bands.verdict(delta + adjustment, scale="points")

    notes: list[str] = []
    if give_missing or get_missing:
        unknown = ", ".join(sorted(set(give_missing) | set(get_missing)))
        notes.append(f"Not valued, and left out of every total: {unknown}.")
    if roster_delta != 0:
        direction = "frees" if roster_delta > 0 else "costs"
        notes.append(
            f"This trade {direction} {abs(roster_delta)} roster "
            f"{'spot' if abs(roster_delta) == 1 else 'spots'}. A freed spot is refilled from "
            f"waivers at {replacement:+.2f} total z, which is why the adjustment is its own row."
        )
    if pool.spread(weights) > 0:
        notes.append(
            f"The pool's own spread of total z is {pool.spread(weights):.2f}; the fair band is "
            f"{bands.fair_z:.2f}. Bands are editable — read them against that spread rather "
            "than as universal thresholds."
        )

    return TradeResult(
        give=give_side,
        get=get_side,
        categories=categories,
        change_z=change_z,
        roster_adjustment=roster_adjustment,
        net_z=net_z,
        replacement_value=replacement,
        change_points=change_points,
        net_points=net_points,
        verdict=bands.verdict(net_z, scale="categories"),
        points_verdicts=points_verdicts,
        bands=bands,
        pool_spread=pool.spread(weights),
        notes=tuple(notes),
    )


def _side(
    label: str, pool: ValuedPool, ids: Sequence[int]
) -> tuple[TradeSide, tuple[str, ...]]:
    found: list[PlayerValuation] = []
    missing: list[str] = []
    for raw in ids:
        valuation = pool.get(int(raw))
        if valuation is None:
            missing.append(str(raw))
        else:
            found.append(valuation)
    return TradeSide(label, tuple(found), tuple(missing)), tuple(missing)


# --------------------------------------------------------------------------- sensitivity


@dataclass(frozen=True, slots=True)
class Scenario:
    """One named "what if" the trade is recomputed under.

    Enumerable and deterministic on purpose. These are not draws from a distribution and the
    spread they produce is not a confidence interval — it is the range the answer moves over
    when an assumption the model cannot verify is changed to another defensible value.

    ``applies_to`` is the field that makes this work at all. A scenario that scales *everyone's*
    minutes changes nothing: a z-score is standardised against the pool, so a uniform shift
    cancels out exactly, and the first version of this swept five scenarios to produce a range
    of 0.02. The question a manager actually asks is asymmetric — "what if the player I am
    getting loses his role" — so a scenario moves one side and leaves the other, and the pool,
    where they are.
    """

    key: str
    label: str
    #: ``"get"``, ``"give"`` or ``"all"``.
    applies_to: str = "all"
    minutes: float = 1.0
    games: float | None = None


#: The assumptions most likely to be wrong, at values a manager would actually argue for, each
#: aimed at one side. Games played dominates every season-long fantasy question and is the thing
#: a projection is least able to know, so it takes two of the four.
STANDARD_SCENARIOS: tuple[Scenario, ...] = (
    Scenario("base", "As projected"),
    Scenario("get_injured", "The player you get misses 22 games", applies_to="get", games=60.0),
    Scenario("get_role_loss", "The player you get loses 15% of his minutes",
             applies_to="get", minutes=0.85),
    Scenario("give_injured", "The player you give up misses 22 games",
             applies_to="give", games=60.0),
    Scenario("give_breakout", "The player you give up gains 15% of his minutes",
             applies_to="give", minutes=1.15),
)


@dataclass(frozen=True, slots=True)
class SensitivityRange:
    """The spread of a trade's net across the scenarios, and which one produced each end."""

    base: float
    low: float
    high: float
    low_scenario: str
    high_scenario: str
    #: ``scenario key -> net z under that scenario``.
    by_scenario: Mapping[str, float]
    scenarios: tuple[Scenario, ...]

    @property
    def flips(self) -> bool:
        """True when the scenarios do not agree on whether the trade helps at all.

        The single most useful thing this range can say. A trade whose sign survives every
        assumption is a different proposition from one that only wins if everybody stays fit.
        """
        return self.low < 0 < self.high

    @property
    def width(self) -> float:
        return self.high - self.low


def trade_sensitivity(
    lines: Sequence[SeasonLine],
    give_ids: Sequence[int],
    get_ids: Sequence[int],
    *,
    weights: Mapping[str, float] | None = None,
    pool_size: int = DEFAULT_POOL_SIZE,
    replacement_depth: int = DEFAULT_REPLACEMENT_DEPTH,
    scenarios: Sequence[Scenario] = STANDARD_SCENARIOS,
    bands: VerdictBands = DEFAULT_BANDS,
) -> SensitivityRange:
    """Recompute the trade under each scenario and report the spread.

    The whole pool is revalued per scenario, not just the traded players: a scenario that moves
    everyone's minutes moves the standardisation too, and holding the pool fixed while moving
    the players would manufacture a change that is really a change of units.
    """
    give_set = {int(pid) for pid in give_ids}
    get_set = {int(pid) for pid in get_ids}

    def _targets(scenario: Scenario) -> set[int]:
        if scenario.applies_to == "get":
            return get_set
        if scenario.applies_to == "give":
            return give_set
        return give_set | get_set

    results: dict[str, float] = {}
    for scenario in scenarios:
        targets = _targets(scenario)
        # Minutes change the per-game line itself, so they are applied to the line and the pool
        # is revalued around them. Games do not: a per-game line is exactly the same whether a
        # player appears 60 times or 82. Availability is therefore a weight on what you receive,
        # applied at the trade rather than to the line — the first version scaled the line by
        # games, which only moved the percentage shrinkage exposure and came out backwards.
        adjusted = (
            lines
            if scenario.minutes == 1.0
            else [
                line.scaled(minutes=scenario.minutes) if line.player_id in targets else line
                for line in lines
            ]
        )
        availability = (
            None
            if scenario.games is None
            else {pid: scenario.games / FULL_SEASON_GAMES for pid in targets}
        )
        scenario_pool = value_pool(
            adjusted, pool_size=pool_size, replacement_depth=replacement_depth
        )
        outcome = evaluate_trade(
            scenario_pool,
            give_ids,
            get_ids,
            weights=weights,
            bands=bands,
            availability=availability,
        )
        results[scenario.key] = outcome.net_z

    base = results.get("base", 0.0)
    low_key = min(results, key=lambda k: results[k])
    high_key = max(results, key=lambda k: results[k])
    labels = {s.key: s.label for s in scenarios}
    return SensitivityRange(
        base=base,
        low=results[low_key],
        high=results[high_key],
        low_scenario=labels.get(low_key, low_key),
        high_scenario=labels.get(high_key, high_key),
        by_scenario=results,
        scenarios=tuple(scenarios),
    )


# --------------------------------------------------------------------------- draft


@dataclass(frozen=True, slots=True)
class DraftPick:
    """One row of the board: a player, where they rank, and why they are being suggested."""

    valuation: PlayerValuation
    overall_rank: int
    round_number: int
    pick_in_round: int
    #: The suggestion score this row is sorted by. Equals total z when nothing is drafted yet.
    suggestion: float
    #: Total z minus the replacement level — value over a waiver-wire body, which is the unit a
    #: roster spot is actually spent in.
    value_over_replacement: float
    #: ``category -> z``, so the board can show what a pick actually adds.
    category_z: Mapping[str, float]
    #: The categories this pick most improves for the roster as it currently stands.
    fills: tuple[str, ...] = ()
    reason: str = ""
    drafted_by: str | None = None


@dataclass(frozen=True, slots=True)
class DraftBoard:
    """The board, plus the state that produced it."""

    picks: tuple[DraftPick, ...]
    #: Where the next pick falls in a snake draft, 1-based.
    next_overall: int
    next_round: int
    next_pick_in_round: int
    teams: int
    roster_spots: int
    #: ``category -> the roster's current total z`` for the manager's own team.
    roster_strength: Mapping[str, float]
    #: The categories the roster is weakest in, worst first.
    weakest: tuple[str, ...]
    replacement_value: float
    notes: tuple[str, ...] = ()


#: How much of the suggestion score is need rather than raw value, at most. Kept deliberately
#: small: a board that chases categories over talent drafts badly, and the most common way a
#: "smart" draft tool loses is by talking its user out of the best player available.
NEED_WEIGHT = 0.25

#: A roster is only "weak" somewhere once it has enough players for the shape to mean anything.
NEED_MIN_ROSTER = 3


def build_draft_board(
    pool: ValuedPool,
    *,
    weights: Mapping[str, float] | None = None,
    drafted: Mapping[int, str] | None = None,
    my_team: str | None = None,
    teams: int = 12,
    roster_spots: int = 13,
    limit: int = 50,
) -> DraftBoard:
    """Rank the undrafted players, best pick first.

    The ranking is **total z, adjusted by at most a quarter for what the roster still needs**.
    Three things were deliberately left out of it, each because it makes a board worse:

    * *Positional scarcity as a multiplier.* Nine-category leagues are won on categories, and
      position eligibility in most formats is loose enough that a scarcity term mostly
      reproduces the category term with more noise. Position is shown, not scored.
    * *Average draft position.* Drafting toward consensus is how a projection's edge gets
      thrown away; the board's job is to disagree with ADP when the numbers do.
    * *Tier breaks.* They are a presentation device, and inventing them here would hide the
      continuous quantity the reader should be looking at.
    """
    drafted = dict(drafted or {})
    weight_map = weights or {}
    replacement = pool.replacement(weights)

    mine = [
        pool.players[pid]
        for pid, owner in drafted.items()
        if my_team and owner == my_team and pid in pool.players
    ]
    roster_strength = {
        category: sum(v.z(category) for v in mine) for category in CATEGORIES
    }
    active = [c for c in CATEGORIES if float(weight_map.get(c, 1.0)) > 0]
    weakest = tuple(sorted(active, key=lambda c: roster_strength.get(c, 0.0))[:3])
    need_is_meaningful = len(mine) >= NEED_MIN_ROSTER

    available = [v for v in pool.players.values() if v.player_id not in drafted]
    scored: list[tuple[float, PlayerValuation, tuple[str, ...]]] = []
    for valuation in available:
        base = valuation.total_z(weights)
        fills: tuple[str, ...] = ()
        bonus = 0.0
        if need_is_meaningful and weakest:
            # Need is expressed in the same units as value and then damped, rather than as a
            # free-floating multiplier: a category bonus that is not commensurate with total z
            # cannot be reasoned about when the two disagree.
            contributions = [(c, valuation.z(c)) for c in weakest]
            bonus = NEED_WEIGHT * sum(z for _c, z in contributions) / max(len(weakest), 1)
            fills = tuple(c for c, z in sorted(contributions, key=lambda p: -p[1]) if z > 0.5)
        scored.append((base + bonus, valuation, fills))

    scored.sort(key=lambda row: -row[0])

    taken = len(drafted)
    next_overall = taken + 1
    next_round, next_pick = _snake_position(next_overall, teams)

    picks: list[DraftPick] = []
    for offset, (suggestion, valuation, fills) in enumerate(scored[: max(int(limit), 0)]):
        overall = next_overall + offset
        round_number, pick_in_round = _snake_position(overall, teams)
        picks.append(
            DraftPick(
                valuation=valuation,
                overall_rank=overall,
                round_number=round_number,
                pick_in_round=pick_in_round,
                suggestion=suggestion,
                value_over_replacement=valuation.total_z(weights) - replacement,
                category_z={c: valuation.z(c) for c in CATEGORIES},
                fills=fills,
                reason=_reason(valuation, fills, weights, offset),
                drafted_by=None,
            )
        )

    notes: list[str] = []
    if my_team and not need_is_meaningful:
        notes.append(
            f"Suggestions are pure value until your roster has {NEED_MIN_ROSTER} players — "
            "three picks is the earliest a category shape means anything."
        )
    elif need_is_meaningful and weakest:
        notes.append(
            "Weakest categories: "
            + ", ".join(_label(c) for c in weakest)
            + f". Need moves a suggestion by at most {NEED_WEIGHT:.0%} of its value."
        )
    if not my_team:
        notes.append("Set your team name to rank by what your roster still needs.")

    return DraftBoard(
        picks=tuple(picks),
        next_overall=next_overall,
        next_round=next_round,
        next_pick_in_round=next_pick,
        teams=int(teams),
        roster_spots=int(roster_spots),
        roster_strength=roster_strength,
        weakest=weakest,
        replacement_value=replacement,
        notes=tuple(notes),
    )


def _snake_position(overall: int, teams: int) -> tuple[int, int]:
    """``(round, pick within round)`` for a 1-based overall pick in a snake draft.

    Even rounds run backwards, so pick 13 of a 12-team draft is round 2 pick 1 and belongs to
    whoever picked 12th. The board reports the slot, not the owner, because only the league
    knows who sits where.
    """
    size = max(int(teams), 1)
    index = max(int(overall), 1) - 1
    round_number = index // size + 1
    position = index % size
    if round_number % 2 == 0:
        position = size - 1 - position
    return round_number, position + 1


def _reason(
    valuation: PlayerValuation,
    fills: Sequence[str],
    weights: Mapping[str, float] | None,
    offset: int,
) -> str:
    """One sentence a reader can disagree with, rather than a bare number."""
    best = sorted(
        ((c, valuation.z(c)) for c in CATEGORIES if float((weights or {}).get(c, 1.0)) > 0),
        key=lambda pair: -pair[1],
    )
    strengths = [c for c, z in best[:2] if z > 0.5]
    parts: list[str] = []
    if offset == 0:
        parts.append("Best available")
    if strengths:
        parts.append(_strengths_phrase(strengths))
    if fills:
        parts.append("fills " + ", ".join(_label(c) for c in fills))
    worst = best[-1] if best else None
    if worst is not None and worst[1] < -0.75:
        parts.append(_weakness_phrase(worst[0]))
    return "; ".join(parts) if parts else "Ranked on total value"


def _strengths_phrase(categories: Sequence[str]) -> str:
    """Turnovers are sign-flipped, so a high z there is *few* turnovers.

    Saying a player "carries turnovers" reads as praise for the thing the category penalises.
    The label has to follow the direction or the sentence contradicts the number beside it.
    """
    counting = [c for c in categories if c != "tov"]
    phrases: list[str] = []
    if counting:
        phrases.append("carries " + " and ".join(_label(c) for c in counting))
    if "tov" in categories:
        phrases.append("protects the ball")
    return "; ".join(phrases)


def _weakness_phrase(category: str) -> str:
    if category == "tov":
        return "turns it over"
    return f"costs you {_label(category)}"


_LABELS: Mapping[str, str] = {
    "pts": "points", "fg3m": "threes", "reb": "rebounds", "ast": "assists",
    "stl": "steals", "blk": "blocks", "tov": "turnovers", "fg_pct": "FG%", "ft_pct": "FT%",
}


def _label(category: str) -> str:
    return _LABELS.get(category, category)
