"""The next-game projection engine: opportunity x rate, shrunk, with an honest interval.

Implements ``docs/PROJECTION.md`` — the master formula (eq. 4.6)

    S_hat = M_hat * r_hat_reg * f_pace * f_opp * f_home * f_rest

the per-statistic shrinkage of section 2, the context factors of section 3, the negative-binomial
predictive distribution with a shrunken per-player dispersion multiplier of section 4, and the
correlated combination line of section 5. The output is shaped exactly like
``contracts/CONTRACT.md`` section 4's ``next_game_projection`` payload.

Design rules this module holds to
--------------------------------
*   **Pure.** Plain dicts, lists and floats in; dataclasses and dicts out. No database session,
    no request object, no global state. Everything here unit-tests without a fixture.
*   **Stdlib only.** ``math`` and ``statistics``, plus :mod:`nbastats.catalog` for metric
    formatting. numpy and scipy are not installed and must not become requirements — the
    negative binomial is summed with a recurrence instead of imported.
*   **A projection is never a record.** Every line carries ``availability: "estimated"``.
*   **Degrade honestly.** A player with no history gets the league prior, a dispersion multiplier
    of exactly 1.0, and diagnostics (exposure, shrinkage weight) that say so. Nothing here
    silently invents confidence.
*   **No market translation.** No implied probability, no vig removal, no expected value, no
    Kelly staking, no probability-versus-price function anywhere. See docs/PROJECTION.md
    section 6: NBA.com's terms forbid gambling use of their statistics, and the source paper
    makes no profitability claim. That omission is deliberate and load-bearing.

Where this deviates from the source pipeline, and why
-----------------------------------------------------
*   ``nbaproj/distributions.py`` draws correlated counts with a Gaussian-copula Monte Carlo
    (``copula_mc``), which needs ``numpy.linalg.cholesky`` and ``scipy.stats``. :func:`combo`
    instead applies the variance identity Var(sum) = sigma^T C sigma exactly — which is what the
    paper's eq. 11.1 actually asserts — and moment-matches a negative binomial to that mean and
    variance for the interval. The reported mean, sd, independent sd and inflation are exact; only
    the shape of the combination's interval is an approximation rather than a simulation.
*   The paper projects minutes with a gradient-boosted quantile model (Chapter 8). LightGBM is
    not a dependency here, so :func:`project_minutes` uses the paper's fitted 2-game half-life for
    the centre and the spread of the player's own recent minutes for the interval.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, NamedTuple, Sequence

from . import catalog
from . import projection_constants as C
from .projection_constants import (
    COMBO_LABEL,
    COMBO_METRICS,
    DEFAULT_INTERVAL_LEVEL,
    DISPERSION_PRIOR_MEAN,
    DISPERSION_SHRINKAGE_GAMES,
    FORM_BLEND_BASE_WEIGHT,
    FORM_BLEND_FORM_WEIGHT,
    MIN_DISPERSION_ALPHA,
    MINUTES_HALFLIFE_GAMES,
    MINUTES_INTERVAL_Z,
    MINUTES_MAX,
    MINUTES_METRIC_KEY,
    MINUTES_SD_FLOOR_FRACTION,
    SEASON_CAREER_BLEND_MINUTES,
    StatConstants,
    UnknownProjectionStatError,
)

__all__ = [
    "AVAILABILITY",
    "Factor",
    "RegressedRate",
    "MinutesProjection",
    "CalibratedContext",
    "StatInput",
    "StatProjection",
    "ComboProjection",
    "ewma",
    "project_minutes",
    "regressed_rate",
    "context_factors",
    "calibrate_context_factors",
    "total_context_multiplier",
    "nb_params",
    "nb_variance",
    "nb_pmf",
    "nb_pmf_table",
    "nb_cdf",
    "nb_interval",
    "NegativeBinomial",
    "fit_dispersion",
    "standardised_sq_residuals",
    "shrunken_dispersion_multiplier",
    "effective_alpha",
    "combo",
    "project_stat",
    "project_box_score",
]

#: docs/PROJECTION.md section 7 rule 5, and contracts/CONTRACT.md section 4: a projection carries
#: the same availability marker as a derived pre-1997 statistic. It is never ``"full"``.
AVAILABILITY = "estimated"

_METHOD_SUMMARY = "Opportunity x rate, shrunk per statistic."


# --------------------------------------------------------------------------- small helpers


def _num(value: Any) -> float | None:
    """A finite float, or ``None`` for anything that is not one (``None``, ``""``, NaN, inf)."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _non_negative(value: Any, default: float = 0.0) -> float:
    """A finite float clamped at zero — totals and exposures are never negative."""
    out = _num(value)
    if out is None:
        return default
    return out if out > 0.0 else 0.0


def _round(value: float | None, places: int) -> float | None:
    """Round for the JSON payload, passing ``None`` through."""
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return round(value, places)


def _as_number(value: float | int) -> float | int:
    """Emit an integral float as an int, so a Swift ``Int`` field decodes it without a surprise."""
    if isinstance(value, int):
        return value
    if math.isfinite(value) and float(value).is_integer():
        return int(value)
    return value


def _display(metric_key: str, value: float | None) -> str:
    """``displayValue`` for a metric, falling back to one decimal for unknown keys."""
    if catalog.has_metric(metric_key):
        return catalog.format_metric(metric_key, value)
    return catalog.EM_DASH if value is None else f"{value:.1f}"


def _descriptor(metric_key: str) -> dict[str, Any] | None:
    """The catalog's ``MetricDescriptor``, or ``None`` when the key is not in the catalog."""
    if not catalog.has_metric(metric_key):
        return None
    return dict(catalog.metric(metric_key))


# --------------------------------------------------------------------------- EWMA


def ewma(values: Sequence[Any] | Iterable[Any], halflife_games: float) -> float:
    """Causal exponentially weighted mean, **most recent last**.

    Weight of an observation ``g`` games before the most recent one is ``0.5 ** (g / h)``, so an
    observation exactly one half-life old counts half as much as the newest. Weights are
    normalised by their own sum, which is what makes the estimator work with fewer observations
    than the half-life: three games at ``h = 30`` return very nearly their flat mean rather than a
    number damped toward zero.

    Gaps are tolerated. Entries that are ``None`` or non-finite are skipped, but they still
    consume a position, so a missing game ages the observations around it correctly.

    Returns ``0.0`` for an empty series or one with no usable entries — there is no history, and
    callers must read that as "no information" rather than "zero production". :func:`regressed_rate`
    and :func:`project_minutes` both do.
    """
    seq = list(values)
    n = len(seq)
    if n == 0:
        return 0.0
    h = _num(halflife_games)
    if h is None or h <= 0.0:
        # No memory: the most recent usable observation is the whole estimate.
        for item in reversed(seq):
            x = _num(item)
            if x is not None:
                return x
        return 0.0
    num = 0.0
    den = 0.0
    for i, item in enumerate(seq):
        x = _num(item)
        if x is None:
            continue
        weight = 0.5 ** ((n - 1 - i) / h)
        num += weight * x
        den += weight
    return num / den if den > 0.0 else 0.0


def _weighted_sd(values: Sequence[Any], centre: float, halflife_games: float) -> float:
    """EWMA-weighted standard deviation about ``centre``, with a reliability-weight correction."""
    h = _num(halflife_games) or 1.0
    n = len(values)
    den = 0.0
    den_sq = 0.0
    acc = 0.0
    for i, item in enumerate(values):
        x = _num(item)
        if x is None:
            continue
        w = 0.5 ** ((n - 1 - i) / h) if h > 0 else 1.0
        den += w
        den_sq += w * w
        acc += w * (x - centre) ** 2
    if den <= 0.0:
        return 0.0
    correction = den * den - den_sq
    if correction > 0.0:
        return math.sqrt(max(acc * den / correction, 0.0))
    return math.sqrt(max(acc / den, 0.0))


# --------------------------------------------------------------------------- minutes


class MinutesProjection(NamedTuple):
    """``(mean, low, high)`` — projected minutes and the bounds of its interval.

    Deliberately a three-field tuple so ``mean, low, high = project_minutes(...)`` works. The
    half-life and season average that belong beside it in the payload are supplied to
    :func:`project_box_score` separately, because they are context rather than results.
    """

    mean: float
    low: float
    high: float


def project_minutes(
    recent_minutes: Sequence[Any] | Iterable[Any],
    season_average: float | None = None,
    *,
    halflife_games: float = MINUTES_HALFLIFE_GAMES,
    level: float | None = DEFAULT_INTERVAL_LEVEL,
) -> MinutesProjection:
    """Project minutes for the next game from recent minutes, most recent last.

    The centre is the EWMA at the paper's fitted 2-game half-life (Table B.4). Minutes decay far
    faster than any production rate because a rotation change is a discrete, persistent event —
    which is exactly why opportunity and rate are modelled separately (docs/PROJECTION.md
    section 2).

    ``season_average`` is a **fallback**, used only when there is no recent history at all. It is
    never blended into the mean: the contract reports it beside the projection so a reader who
    disagrees with the minutes can see both numbers rather than one number containing both.

    The interval is the weighted spread of the player's own recent minutes, floored at
    :data:`~nbastats.projection_constants.MINUTES_SD_FLOOR_FRACTION` of the mean so that one or
    two logged games cannot produce a falsely tight range, and clipped to ``[0, 48]``. Minutes
    are the dominant error term in the whole formula (supplying true minutes improves points RMSE
    by 19.3%), so this interval is shown, never suppressed.
    """
    seq = list(recent_minutes)
    usable = [x for x in (_num(v) for v in seq) if x is not None]
    season = _num(season_average)

    if usable:
        mean = ewma(seq, halflife_games)
        sd = _weighted_sd(seq, mean, halflife_games)
    elif season is not None:
        mean = max(season, 0.0)
        sd = 0.0
    else:
        return MinutesProjection(0.0, 0.0, 0.0)

    mean = min(max(mean, 0.0), MINUTES_MAX)
    if mean <= 0.0:
        return MinutesProjection(0.0, 0.0, 0.0)

    sd = max(sd, MINUTES_SD_FLOOR_FRACTION * mean)
    if level is None:
        return MinutesProjection(mean, mean, mean)
    half_width = MINUTES_INTERVAL_Z * sd
    low = max(0.0, mean - half_width)
    high = min(MINUTES_MAX, mean + half_width)
    return MinutesProjection(mean, low, high)


# --------------------------------------------------------------------------- the regressed rate


@dataclass(frozen=True, slots=True)
class RegressedRate:
    """A shrunken per-minute rate, with the diagnostics the widget has to show.

    ``rate``
        ``r_hat_reg``: the number the master formula multiplies by projected minutes.
    ``shrinkage_weight``
        The share of ``rate`` that comes from this player's own observations rather than the
        league prior, computed exactly from the padding weights rather than estimated. 0.0 means
        the projection is the league prior and nothing else; 1.0 means the prior is not in it at
        all. docs/PROJECTION.md section 7 rule 4 requires this to reach the screen.
    ``exposure_minutes``
        The minutes of history behind the estimate — the honest denominator.
    """

    stat_key: str
    rate: float
    league_rate: float
    k: float
    halflife_games: float
    exposure_minutes: float
    season_minutes: float
    career_minutes: float
    shrinkage_weight: float
    season_rate: float
    career_rate: float
    blended_rate: float
    form_rate: float | None
    observed_rate: float | None
    season_weight: float

    @property
    def constants(self) -> StatConstants:
        """The paper's constants behind this estimate."""
        return C.stat_constants(self.stat_key)

    @property
    def prior_weight(self) -> float:
        """Share of the rate that is the league prior: ``1 - shrinkage_weight``."""
        return 1.0 - self.shrinkage_weight


def regressed_rate(
    stat_key: str,
    season_total: float | None = 0.0,
    season_minutes: float | None = 0.0,
    career_total: float | None = None,
    career_minutes: float | None = None,
    form_stat_ewma: float | None = None,
    form_minutes_ewma: float | None = None,
) -> RegressedRate:
    """The blended, shrunken per-minute rate of docs/PROJECTION.md section 2.

    ::

        r_season = (sum stat_season + k*mu) / (sum min_season + k)
        r_career = (sum stat_career + k*mu) / (sum min_career + k)
        r_form   = EWMA_h(stat) / EWMA_h(minutes)
        w        = n / (n + 400),   n = cumulative season minutes
        r        = w*r_season + (1-w)*r_career
        r_hat    = 0.75*r + 0.25*r_form

    ``k`` is the statistic's own stabilisation constant, and the spread of ``k`` across statistics
    — 34 minutes for rebounds against 322 for steals — is the point of the whole section. At the
    same exposure a steals rate is pulled far harder toward the league prior than a rebounding
    rate, because rebounding volume is a structural consequence of role and size while steals are
    rare events dominated by opportunity.

    Career totals default to the season totals when not supplied, and the form term is dropped
    (not zeroed) when ``form_minutes_ewma`` is missing or zero: a player who has not played has no
    form, and pretending his form is zero would bias the rate to nothing.
    """
    consts = C.stat_constants(stat_key)
    mu = consts.league_rate_per_min
    k = consts.k_minutes

    s_total = _non_negative(season_total)
    s_min = _non_negative(season_minutes)
    c_total = s_total if career_total is None else _non_negative(career_total)
    c_min = s_min if career_minutes is None else _non_negative(career_minutes)

    # Padding estimators. With zero exposure both collapse to mu exactly.
    r_season = (s_total + k * mu) / (s_min + k)
    r_career = (c_total + k * mu) / (c_min + k)

    # Share of each padding estimator that is the player rather than the prior.
    a_season = s_min / (s_min + k)
    a_career = c_min / (c_min + k)

    w = s_min / (s_min + SEASON_CAREER_BLEND_MINUTES)
    blended = w * r_season + (1.0 - w) * r_career
    base_player_weight = w * a_season + (1.0 - w) * a_career

    form_stat = _num(form_stat_ewma)
    form_min = _num(form_minutes_ewma)
    if form_stat is not None and form_min is not None and form_min > 0.0:
        form_rate: float | None = max(form_stat, 0.0) / form_min
        # The form term is the player's own recent play, with no prior in it.
        form_player_weight = 1.0
    else:
        form_rate = None
        form_player_weight = base_player_weight

    rate = FORM_BLEND_BASE_WEIGHT * blended + FORM_BLEND_FORM_WEIGHT * (
        form_rate if form_rate is not None else blended
    )
    shrinkage_weight = (
        FORM_BLEND_BASE_WEIGHT * base_player_weight
        + FORM_BLEND_FORM_WEIGHT * form_player_weight
    )

    if c_min > 0.0:
        observed: float | None = c_total / c_min
    elif s_min > 0.0:
        observed = s_total / s_min
    else:
        observed = None

    return RegressedRate(
        stat_key=stat_key,
        rate=max(rate, 0.0),
        league_rate=mu,
        k=k,
        halflife_games=consts.halflife_games,
        exposure_minutes=max(c_min, s_min),
        season_minutes=s_min,
        career_minutes=c_min,
        shrinkage_weight=min(max(shrinkage_weight, 0.0), 1.0),
        season_rate=r_season,
        career_rate=r_career,
        blended_rate=blended,
        form_rate=form_rate,
        observed_rate=observed,
        season_weight=w,
    )


# --------------------------------------------------------------------------- context factors


@dataclass(frozen=True, slots=True)
class Factor:
    """One multiplicative context factor, normalised so 1.0 is neutral.

    Showing these is what turns the projection from an oracle into an argument
    (docs/PROJECTION.md section 7 rule 3).
    """

    key: str
    label: str
    value: float
    explanation: str

    def payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": _round(self.value, 4),
            "explanation": self.explanation,
        }


def _pct_phrase(value: float) -> str:
    return f"{(value - 1.0) * 100.0:+.1f}%"


def _pace_explanation(f_pace: float, known: bool) -> str:
    if not known:
        return "Pace unavailable for one of the teams; no tempo adjustment applied."
    delta = (f_pace - 1.0) * 100.0
    if abs(delta) < 0.5:
        return "Both teams play at about league-average pace."
    direction = "faster" if delta > 0 else "slower"
    return (
        f"The two teams' tempo deviations add: this game projects {abs(delta):.1f}% "
        f"{direction} than league average."
    )


def _opp_explanation(f_opp: float, known: bool) -> str:
    if not known:
        return "Opponent defensive rating unavailable; no opponent adjustment applied."
    delta = (f_opp - 1.0) * 100.0
    if abs(delta) < 0.3:
        return "Opponent defends at about league average."
    if delta > 0:
        return (
            f"Opponent defensive rating is {delta:.1f}% above league average — a weaker "
            "defence, so production projects up."
        )
    return (
        f"Opponent defensive rating is {abs(delta):.1f}% below league average — a stronger "
        "defence, so production projects down."
    )


def _venue_explanation(is_home: bool, value: float, calibrated: bool) -> str:
    where = "Home game" if is_home else "Road game"
    tail = "" if calibrated else " Default multiplier, not yet recalibrated from data."
    return f"{where}; the venue multiplier is {value:.3f} ({_pct_phrase(value)}).{tail}"


def _rest_explanation(bucket: int | None, value: float, calibrated: bool) -> str:
    if bucket is None:
        return "Days of rest unknown; no rest adjustment applied."
    words = {
        0: "Second night of a back-to-back",
        1: "One day of rest",
        2: "Two days of rest",
        3: "Three or more days of rest",
    }
    tail = "" if calibrated else " Default multiplier, not yet recalibrated from data."
    return f"{words[bucket]}; the rest multiplier is {value:.3f} ({_pct_phrase(value)}).{tail}"


def context_factors(
    team_pace: float | None,
    opp_pace: float | None,
    league_pace: float | None,
    opp_def_rtg: float | None,
    league_def_rtg: float | None,
    is_home: bool,
    rest_days: float | int | None = None,
    *,
    home_factors: Mapping[bool, float] | Mapping[int, float] | None = None,
    rest_factors: Mapping[int, float] | None = None,
    calibration: "CalibratedContext | None" = None,
) -> list[Factor]:
    """The four multiplicative context factors of docs/PROJECTION.md section 3.

    ::

        f_pace = Pace_tm * Pace_opp / Pace_lg^2      (eq. 4.7)
        f_opp  = DRtg_opp / DRtg_lg
        f_home, f_rest                                empirically calibrated multipliers

    The product-over-league pace form is used rather than an average because writing
    ``Pace_tm = Pace_lg(1+a)`` and ``Pace_opp = Pace_lg(1+b)`` gives ``f_pace ~= 1 + a + b``: the
    two teams' tempo deviations *add*, which is the behaviour a game-level tempo estimate should
    have. Two league-average teams give exactly 1.0.

    ``home_factors`` and ``rest_factors`` default to the documented placeholders in
    :mod:`nbastats.projection_constants`; pass the result of :func:`calibrate_context_factors`
    (either as ``calibration=`` or as the two mappings) once there is enough history to fit them.
    Rest days are clipped to 0..3, and unknown inputs produce a neutral 1.0 factor that says so
    in its explanation rather than a guess that does not.
    """
    if calibration is not None:
        home_factors = home_factors if home_factors is not None else calibration.home_factors
        rest_factors = rest_factors if rest_factors is not None else calibration.rest_factors

    tm = _num(team_pace)
    opp = _num(opp_pace)
    lg = _num(league_pace)
    pace_known = bool(tm and opp and lg and lg > 0 and tm > 0 and opp > 0)
    f_pace = (tm * opp) / (lg * lg) if pace_known else 1.0  # type: ignore[operator]

    drtg = _num(opp_def_rtg)
    lg_drtg = _num(league_def_rtg)
    opp_known = bool(drtg and lg_drtg and lg_drtg > 0 and drtg > 0)
    f_opp = drtg / lg_drtg if opp_known else 1.0  # type: ignore[operator]

    home = bool(is_home)
    f_home = C.home_factor(home, home_factors)
    bucket = C.clip_rest_days(rest_days)
    f_rest = C.rest_factor(rest_days, rest_factors)

    home_fitted = home_factors is not None
    rest_fitted = rest_factors is not None
    return [
        Factor("pace", "Pace", f_pace, _pace_explanation(f_pace, pace_known)),
        Factor("opponent", "Opponent Defence", f_opp, _opp_explanation(f_opp, opp_known)),
        Factor("venue", "Venue", f_home, _venue_explanation(home, f_home, home_fitted)),
        Factor("rest", "Rest", f_rest, _rest_explanation(bucket, f_rest, rest_fitted)),
    ]


def total_context_multiplier(factors: Iterable[Factor]) -> float:
    """The product of the context factors — the ``f_pace * f_opp * f_home * f_rest`` term."""
    total = 1.0
    for f in factors:
        value = _num(f.value)
        if value is not None and value > 0.0:
            total *= value
    return total


@dataclass(frozen=True, slots=True)
class CalibratedContext:
    """Empirical venue and rest multipliers, fitted as ratios of realised to predicted totals."""

    home_factors: dict[bool, float]
    rest_factors: dict[int, float]
    home_rows: dict[bool, int] = field(default_factory=dict)
    rest_rows: dict[int, int] = field(default_factory=dict)
    defaulted_home: tuple[bool, ...] = ()
    defaulted_rest: tuple[int, ...] = ()
    n_rows: int = 0

    @property
    def fully_calibrated(self) -> bool:
        """True when every bucket was fitted from data rather than left at its default."""
        return not self.defaulted_home and not self.defaulted_rest


def calibrate_context_factors(
    rows: Iterable[Mapping[str, Any]],
    *,
    min_rows: int = 25,
) -> CalibratedContext:
    """Fit ``f_home`` and ``f_rest`` from history, exactly as ``nbaproj/models.py`` does.

    Each row needs the realised total and the *raw* prediction — minutes x rate x ``f_pace`` x
    ``f_opp``, before any venue or rest correction — plus the venue and the days of rest::

        {"actual": 24.0, "raw_predicted": 22.6, "is_home": True, "rest_days": 1}

    Then, per paper eq. 4.9::

        f_home(v) = sum_{i in v} S_i / sum_{i in v} S0_i
        f_rest(r) = sum_{i in r} S_i / sum_{i in r} (S0_i * f_home(venue_i))

    Ratios of sums, not means of ratios: a low-minute game with a near-zero denominator cannot
    distort the estimate. The estimator forces the model to be unbiased within each stratum, so it
    absorbs a global bias correction at the same time.

    A bucket with fewer than ``min_rows`` rows, or a non-positive denominator, keeps its documented
    default and is listed in ``defaulted_home`` / ``defaulted_rest`` — a factor fitted on four
    games is worse than no factor at all, and the caller is told which is which.
    """
    home_num: dict[bool, float] = {True: 0.0, False: 0.0}
    home_den: dict[bool, float] = {True: 0.0, False: 0.0}
    home_rows: dict[bool, int] = {True: 0, False: 0}
    buckets = list(range(C.MAX_REST_DAYS + 1))
    kept: list[tuple[float, float, bool, int]] = []

    for row in rows:
        actual = _num(row.get("actual", row.get("observed")))
        raw = _num(row.get("raw_predicted", row.get("raw")))
        if actual is None or raw is None or raw <= 0.0 or actual < 0.0:
            continue
        home = bool(row.get("is_home", row.get("home", False)))
        bucket = C.clip_rest_days(row.get("rest_days"))
        home_num[home] += actual
        home_den[home] += raw
        home_rows[home] += 1
        if bucket is not None:
            kept.append((actual, raw, home, bucket))

    f_home: dict[bool, float] = {}
    defaulted_home: list[bool] = []
    for venue in (True, False):
        if home_rows[venue] >= min_rows and home_den[venue] > 0.0 and home_num[venue] > 0.0:
            f_home[venue] = home_num[venue] / home_den[venue]
        else:
            f_home[venue] = C.home_factor(venue, None)
            defaulted_home.append(venue)

    rest_num: dict[int, float] = {b: 0.0 for b in buckets}
    rest_den: dict[int, float] = {b: 0.0 for b in buckets}
    rest_rows: dict[int, int] = {b: 0 for b in buckets}
    for actual, raw, home, bucket in kept:
        rest_num[bucket] += actual
        rest_den[bucket] += raw * f_home[home]
        rest_rows[bucket] += 1

    f_rest: dict[int, float] = {}
    defaulted_rest: list[int] = []
    for bucket in buckets:
        if rest_rows[bucket] >= min_rows and rest_den[bucket] > 0.0 and rest_num[bucket] > 0.0:
            f_rest[bucket] = rest_num[bucket] / rest_den[bucket]
        else:
            f_rest[bucket] = C.rest_factor(bucket, None)
            defaulted_rest.append(bucket)

    return CalibratedContext(
        home_factors=f_home,
        rest_factors=f_rest,
        home_rows=home_rows,
        rest_rows=rest_rows,
        defaulted_home=tuple(defaulted_home),
        defaulted_rest=tuple(defaulted_rest),
        n_rows=home_rows[True] + home_rows[False],
    )


# --------------------------------------------------------------- the negative binomial
#
# Var = mu + alpha*mu^2,  r = 1/alpha,  p = r/(r+mu)   (docs/PROJECTION.md section 4)
#
# The source pipeline calls scipy.stats.nbinom. scipy is not installed and must not become a
# dependency, so the pmf is generated by its own recurrence
#
#     pmf(0) = p**r,      pmf(k) = pmf(k-1) * (r + k - 1)/k * (1-p)
#
# and pmf(0) is evaluated as exp(-r*log1p(mu/r)) rather than pow(p, r), which stays accurate as
# alpha -> 0 and hands back the Poisson limit exp(-mu) instead of underflowing.


def nb_params(mean: float, alpha: float) -> tuple[float, float]:
    """``(r, p)`` for the negative binomial with this mean and dispersion.

    ``alpha`` at or below :data:`MIN_DISPERSION_ALPHA` has no finite ``r``; the Poisson limit is
    returned as ``(inf, 1.0)`` and the callers below branch on it.
    """
    mu = max(_num(mean) or 0.0, 0.0)
    a = max(_num(alpha) or 0.0, 0.0)
    if a <= MIN_DISPERSION_ALPHA:
        return math.inf, 1.0
    r = 1.0 / a
    return r, r / (r + mu)


def nb_variance(mean: float, alpha: float) -> float:
    """``mu + alpha*mu^2`` — the variance law of docs/PROJECTION.md section 4."""
    mu = max(_num(mean) or 0.0, 0.0)
    a = max(_num(alpha) or 0.0, 0.0)
    return mu + a * mu * mu


def _pmf_iter(mean: float, alpha: float) -> Iterator[float]:
    """Yield ``pmf(0), pmf(1), ...`` by the recurrence, in the Poisson limit when alpha is 0."""
    mu = max(_num(mean) or 0.0, 0.0)
    a = max(_num(alpha) or 0.0, 0.0)
    if mu <= 0.0:
        yield 1.0
        while True:
            yield 0.0
    if a <= MIN_DISPERSION_ALPHA:
        value = math.exp(-mu)
        k = 0
        while True:
            yield value
            k += 1
            value *= mu / k
    else:
        r = 1.0 / a
        q = mu / (r + mu)  # 1 - p
        value = math.exp(-r * math.log1p(mu / r))  # p**r, computed stably
        k = 0
        while True:
            yield value
            k += 1
            value *= (r + k - 1.0) / k * q


#: Hard ceiling on how far the pmf recurrence is ever walked. Box-score counts put the real cap
#: in the low hundreds; this only stops a nonsense mean from allocating without bound.
_MAX_SUPPORT = 500_000


def _support_cap(mean: float, alpha: float) -> int:
    """An upper support bound past which the remaining mass is negligible."""
    mu = max(_num(mean) or 0.0, 0.0)
    sd = math.sqrt(nb_variance(mu, alpha))
    return min(max(20, int(math.ceil(mu + 12.0 * sd)) + 20), _MAX_SUPPORT)


def _pmf_table(mean: float, alpha: float, cap: int) -> list[float]:
    """``[pmf(0), ..., pmf(cap)]``."""
    out: list[float] = []
    it = _pmf_iter(mean, alpha)
    for _ in range(cap + 1):
        out.append(next(it))
    return out


def nb_pmf_table(mean: float, alpha: float, upto: int | None = None) -> list[float]:
    """``[P(X=0), ..., P(X=upto)]`` in a single pass of the recurrence.

    ``upto`` defaults to a support bound past which the remaining mass is negligible. Callers
    that need many probabilities from one distribution — a CRPS, a PIT histogram, a chart of the
    predictive mass — should use this rather than calling :func:`nb_pmf` in a loop, which restarts
    the recurrence every time.
    """
    cap = _support_cap(mean, alpha) if upto is None else max(int(upto), 0)
    return _pmf_table(mean, alpha, cap)


def nb_pmf(k: int | float, mean: float, alpha: float) -> float:
    """``P(X = k)``. Non-integer ``k`` is floored; negative ``k`` has probability zero."""
    kk = _num(k)
    if kk is None or kk < 0:
        return 0.0
    target = int(math.floor(kk + 1e-9))
    it = _pmf_iter(mean, alpha)
    value = 0.0
    for _ in range(target + 1):
        value = next(it)
    return value


def nb_cdf(k: int | float, mean: float, alpha: float) -> float:
    """``P(X <= k)``, summed with the same recurrence and stopped once the tail is exhausted."""
    kk = _num(k)
    if kk is None or kk < 0:
        return 0.0
    target = int(math.floor(kk + 1e-9))
    total = 0.0
    it = _pmf_iter(mean, alpha)
    for _ in range(target + 1):
        total += next(it)
        if total >= 1.0 - 1e-15:
            return 1.0
    return min(total, 1.0)


def nb_interval(
    mean: float, alpha: float, level: float | None = DEFAULT_INTERVAL_LEVEL
) -> tuple[int, int]:
    """The shortest central interval of integers carrying at least ``level`` of the mass.

    Growth starts at the mean and extends to whichever neighbouring outcome is more probable, so
    the result always brackets the mean (central) and no shorter interval containing it holds as
    much mass (shortest). Bounds are integers because the quantity is a count, which is what the
    contract's ``low``/``high`` are.

    A mean of zero gives ``(0, 0)``; ``level`` of zero or less gives the single most likely
    outcome; ``alpha`` of zero is the Poisson interval.
    """
    mu = max(_num(mean) or 0.0, 0.0)
    if mu <= 0.0:
        return 0, 0
    anchor = int(round(mu))
    lvl = _num(level)
    if lvl is None or lvl <= 0.0:
        return anchor, anchor
    lvl = min(lvl, 1.0 - 1e-12)

    cap = _support_cap(mu, alpha)
    pmf = _pmf_table(mu, alpha, cap)
    lo = hi = min(anchor, cap)
    mass = pmf[lo]
    while mass < lvl and (lo > 0 or hi < cap):
        p_lo = pmf[lo - 1] if lo > 0 else -1.0
        p_hi = pmf[hi + 1] if hi < cap else -1.0
        if p_lo >= p_hi:
            lo -= 1
            mass += p_lo
        else:
            hi += 1
            mass += p_hi
    return lo, hi


@dataclass(frozen=True, slots=True)
class NegativeBinomial:
    """The predictive distribution: a mean, a dispersion, and the three things they imply."""

    mean: float
    alpha: float

    @property
    def variance(self) -> float:
        return nb_variance(self.mean, self.alpha)

    @property
    def sd(self) -> float:
        return math.sqrt(self.variance)

    @property
    def params(self) -> tuple[float, float]:
        return nb_params(self.mean, self.alpha)

    def pmf(self, k: int | float) -> float:
        return nb_pmf(k, self.mean, self.alpha)

    def cdf(self, k: int | float) -> float:
        return nb_cdf(k, self.mean, self.alpha)

    def interval(self, level: float | None = DEFAULT_INTERVAL_LEVEL) -> tuple[int, int]:
        return nb_interval(self.mean, self.alpha, level)


# --------------------------------------------------------------------------- dispersion


def fit_dispersion(observed: Sequence[Any], predicted: Sequence[Any]) -> float:
    """Method-of-moments ``alpha`` in ``Var = mu + alpha*mu^2``.

    ``E[(y-mu)^2 - mu] = alpha*mu^2``, fitted through the origin on the squared-residual scale::

        alpha = sum[ ((y-mu)^2 - mu) * mu^2 ] / sum[ mu^4 ]

    matching ``nbaproj/distributions.py``. The two series are paired by position and a length
    mismatch uses the shorter of them; pairs with a missing or non-positive ``mu`` are dropped. Unlike the source, which floors at 1e-6 only so scipy has a finite ``r``, a
    non-positive fit is returned as exactly ``0.0``: this module evaluates the Poisson limit
    directly, and "no evidence of overdispersion" should read as zero, not as an epsilon.
    """
    num = 0.0
    den = 0.0
    for y_raw, mu_raw in zip(observed, predicted):
        y = _num(y_raw)
        mu = _num(mu_raw)
        if y is None or mu is None or mu <= 0.0:
            continue
        z = (y - mu) ** 2 - mu
        num += z * mu * mu
        den += mu**4
    if den <= 0.0:
        return 0.0
    alpha = num / den
    return alpha if alpha > 0.0 else 0.0


def standardised_sq_residuals(
    observed: Sequence[Any], predicted: Sequence[Any], alpha: float
) -> list[float]:
    """``z^2 = (S - mu)^2 / (mu + alpha*mu^2)`` per game — the input to eq. 10.5.

    Under a correctly specified variance law each term has expectation 1, so their mean is the
    ratio by which this player's realised variance exceeds the pooled model's. As in
    :func:`fit_dispersion`, the series are paired by position and the shorter one wins.
    """
    out: list[float] = []
    for y_raw, mu_raw in zip(observed, predicted):
        y = _num(y_raw)
        mu = _num(mu_raw)
        if y is None or mu is None or mu <= 0.0:
            continue
        var = nb_variance(mu, alpha)
        if var <= 0.0:
            continue
        out.append((y - mu) ** 2 / var)
    return out


def shrunken_dispersion_multiplier(
    standardised_sq_residuals: Sequence[Any],
    n_games: int | float | None = None,
) -> float:
    """The per-player dispersion multiplier of paper eq. 10.5::

        c_hat = (n * mean(z^2) + k_c * 1) / (n + k_c),    k_c = 60 games

    The padding estimator of section 2 applied to a variance ratio, with prior mean 1.0. A pooled
    ``alpha`` understates realised points variance by 28.9% and covers only 69.9% of nominal 80%
    intervals; this lifts coverage to 79.1%.

    ``n_games`` defaults to the number of usable residuals. **A player with no history gets
    exactly 1.0** — full shrinkage to the prior — which is the intended honest degradation, not a
    fallback: at ``n = 60`` the player and the prior carry equal weight, so half a season
    moves the multiplier halfway to what his own volatility implies.
    """
    values = [x for x in (_num(v) for v in standardised_sq_residuals) if x is not None and x >= 0]
    n = _num(n_games)
    if n is None:
        n = float(len(values))
    n = max(n, 0.0)
    if n <= 0.0 or not values:
        return DISPERSION_PRIOR_MEAN
    z_bar = sum(values) / len(values)
    k_c = DISPERSION_SHRINKAGE_GAMES
    return (n * z_bar + k_c * DISPERSION_PRIOR_MEAN) / (n + k_c)


def effective_alpha(mean: float, alpha: float, multiplier: float = 1.0) -> float:
    """The ``alpha`` whose variance law reproduces ``c * (mu + alpha*mu^2)``.

    ``mu + a_eff*mu^2 = c*(mu + alpha*mu^2)`` gives ``a_eff = c*alpha + (c-1)/mu``. This is how
    the per-player multiplier reaches the interval: the contract's ``low``/``high`` are the bounds
    *after* it. Clamped at zero, so a multiplier below 1 can narrow the distribution to Poisson
    but never below it.
    """
    mu = max(_num(mean) or 0.0, 0.0)
    a = max(_num(alpha) or 0.0, 0.0)
    c = _num(multiplier)
    c = 1.0 if c is None or c <= 0.0 else c
    if mu <= 0.0:
        return a * c
    out = c * a + (c - 1.0) / mu
    return out if out > 0.0 else 0.0


# --------------------------------------------------------------------------- combination line


@dataclass(frozen=True, slots=True)
class ComboProjection:
    """A combination line (PTS+REB+AST), reported both correlated and independent."""

    label: str
    mean: float
    sd: float
    sd_if_independent: float
    inflation: float
    low: int | None
    high: int | None
    interval_level: float | None
    correlation: list[list[float]]
    alpha: float
    keys: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "mean": _round(self.mean, 3),
            "sd": _round(self.sd, 3),
            "sdIfIndependent": _round(self.sd_if_independent, 3),
            "inflation": _round(self.inflation, 4),
            "low": self.low,
            "high": self.high,
            "correlation": [[_round(v, 4) for v in row] for row in self.correlation],
        }


def _combo_pairs(
    mean_and_sd_per_stat: Mapping[str, Any] | Sequence[Any],
) -> tuple[tuple[str, ...], list[tuple[float, float]]]:
    """Normalise the combo input to ``(keys, [(mean, sd), ...])``."""
    keys: list[str] = []
    pairs: list[tuple[float, float]] = []
    items: Iterable[Any]
    if isinstance(mean_and_sd_per_stat, Mapping):
        items = mean_and_sd_per_stat.items()
        for key, value in items:
            keys.append(str(key))
            pairs.append(_one_mean_sd(value))
    else:
        for i, value in enumerate(mean_and_sd_per_stat):
            if isinstance(value, StatProjection):
                keys.append(value.stat_key)
            else:
                keys.append(COMBO_METRICS[i] if i < len(COMBO_METRICS) else f"stat{i}")
            pairs.append(_one_mean_sd(value))
    return tuple(keys), pairs


def _one_mean_sd(value: Any) -> tuple[float, float]:
    if isinstance(value, StatProjection):
        return max(value.mean, 0.0), max(value.sd, 0.0)
    if isinstance(value, Mapping):
        mean = _num(value.get("mean")) or 0.0
        sd = _num(value.get("sd")) or 0.0
        return max(mean, 0.0), max(sd, 0.0)
    mean, sd = value  # a (mean, sd) pair
    return max(_num(mean) or 0.0, 0.0), max(_num(sd) or 0.0, 0.0)


def combo(
    mean_and_sd_per_stat: Mapping[str, Any] | Sequence[Any],
    correlation: Sequence[Sequence[float]] | None = None,
    *,
    level: float | None = DEFAULT_INTERVAL_LEVEL,
    label: str = COMBO_LABEL,
) -> ComboProjection:
    """Combine per-statistic means and SDs into a line, with the residual correlation applied.

    ``Var(sum) = sigma^T C sigma`` (paper eq. 11.1), **not** ``sum sigma^2``. The residuals of
    points, rebounds and assists are positively dependent — they share the minutes channel and the
    game-context channel — so treating them as independent understates the spread of their sum.
    On the paper's held-out season the independent calculation gives 6.70 against a realised 7.68.

    Both standard deviations are returned, because the gap between them is the reason the widget
    exists. ``inflation`` is ``sd/sd_if_independent - 1``.

    The interval comes from a negative binomial moment-matched to this mean and *correlated*
    variance. The source pipeline instead draws from a Gaussian copula, which needs numpy and
    scipy; the first two moments, which are what eq. 11.1 pins down exactly, are identical either
    way.
    """
    keys, pairs = _combo_pairs(mean_and_sd_per_stat)
    size = len(pairs)
    if correlation is None:
        # Keys not in the estimated PTS/REB/AST block are reported as uncorrelated rather than
        # handed a borrowed rho; a bare three-element sequence is assumed to be the paper's trio.
        corr = C.correlation_for(keys)
    else:
        corr = [[float(v) for v in row] for row in correlation]
    if len(corr) != size or any(len(row) != size for row in corr):
        raise ValueError(
            f"correlation matrix is {len(corr)}x{len(corr[0]) if corr else 0}, "
            f"expected {size}x{size}"
        )

    mean = sum(m for m, _ in pairs)
    sigmas = [s for _, s in pairs]
    var_independent = sum(s * s for s in sigmas)
    var = 0.0
    for i in range(size):
        for j in range(size):
            var += sigmas[i] * corr[i][j] * sigmas[j]
    var = max(var, 0.0)
    sd = math.sqrt(var)
    sd_independent = math.sqrt(max(var_independent, 0.0))
    inflation = (sd / sd_independent - 1.0) if sd_independent > 0.0 else 0.0

    alpha = 0.0
    if mean > 0.0 and var > mean:
        alpha = (var - mean) / (mean * mean)
    if level is None or mean <= 0.0:
        low: int | None = None
        high: int | None = None
    else:
        low, high = nb_interval(mean, alpha, level)
    return ComboProjection(
        label=label,
        mean=mean,
        sd=sd,
        sd_if_independent=sd_independent,
        inflation=inflation,
        low=low,
        high=high,
        interval_level=level,
        correlation=corr,
        alpha=alpha,
        keys=keys,
    )


# --------------------------------------------------------------------------- the master formula


@dataclass(frozen=True, slots=True)
class StatInput:
    """Everything :func:`project_box_score` needs for one statistic."""

    stat_key: str
    rate: RegressedRate
    dispersion_alpha: float = 0.0
    dispersion_multiplier: float = 1.0
    season_average: float | None = None


@dataclass(frozen=True, slots=True)
class StatProjection:
    """One projected line: the mean, the interval, and every input that produced them."""

    stat_key: str
    mean: float
    sd: float
    low: int | None
    high: int | None
    interval_level: float | None
    season_average: float | None
    delta: float | None
    rate: RegressedRate
    dispersion_alpha: float
    dispersion_multiplier: float
    effective_alpha: float
    projected_minutes: float
    context_multiplier: float

    @property
    def variance(self) -> float:
        return self.sd * self.sd

    def payload(self, *, include_descriptor: bool = True) -> dict[str, Any]:
        """The ``lines[]`` entry of contracts/CONTRACT.md section 4, key for key."""
        out: dict[str, Any] = {
            "metric": self.stat_key,
            "descriptor": _descriptor(self.stat_key) if include_descriptor else None,
            "mean": _round(self.mean, 3),
            "displayValue": _display(self.stat_key, self.mean),
            "low": self.low,
            "high": self.high,
            "intervalLevel": self.interval_level,
            "seasonAverage": _round(self.season_average, 3),
            "delta": _round(self.delta, 3),
            "ratePerMinute": _round(self.rate.rate, 5),
            "shrinkageK": _as_number(self.rate.k),
            "exposureMinutes": _round(self.rate.exposure_minutes, 1),
            "shrinkageWeight": _round(self.rate.shrinkage_weight, 4),
            "halfLifeGames": _as_number(self.rate.halflife_games),
            "dispersionAlpha": _round(self.dispersion_alpha, 5),
            "dispersionMultiplier": _round(self.dispersion_multiplier, 4),
            # A projection is never a record (docs/PROJECTION.md section 7 rule 5).
            "availability": AVAILABILITY,
        }
        return out


def project_stat(
    stat_key: str,
    *,
    projected_minutes: float,
    rate: RegressedRate,
    factors: Sequence[Factor] = (),
    context_multiplier: float | None = None,
    dispersion_alpha: float = 0.0,
    dispersion_multiplier: float = 1.0,
    season_average: float | None = None,
    interval_level: float | None = DEFAULT_INTERVAL_LEVEL,
) -> StatProjection:
    """One statistic through the master formula, with its predictive interval.

    ::

        mean = M_hat * r_hat_reg * f_pace * f_opp * f_home * f_rest
        Var  = c_hat * (mean + alpha*mean^2)

    ``context_multiplier`` overrides the product of ``factors`` when supplied (useful when the
    factors have already been multiplied out). The interval is the negative-binomial interval
    *after* the dispersion multiplier, as the contract requires; pass ``interval_level=None`` for
    the widget's ``interval: "none"`` setting, which suppresses the bounds but never the mean's
    provenance.

    Minutes uncertainty is not added on top: ``alpha`` is fitted on residuals of predictions that
    already used projected minutes, so it is in there already.
    """
    if not C.has_stat(stat_key):
        raise UnknownProjectionStatError(stat_key)
    minutes = max(_num(projected_minutes) or 0.0, 0.0)
    multiplier = (
        total_context_multiplier(factors)
        if context_multiplier is None
        else max(_num(context_multiplier) or 1.0, 0.0)
    )
    mean = max(minutes * max(rate.rate, 0.0) * multiplier, 0.0)

    alpha = max(_num(dispersion_alpha) or 0.0, 0.0)
    c_hat = _num(dispersion_multiplier)
    c_hat = 1.0 if c_hat is None or c_hat <= 0.0 else c_hat
    a_eff = effective_alpha(mean, alpha, c_hat)
    sd = math.sqrt(nb_variance(mean, a_eff))

    if interval_level is None:
        low: int | None = None
        high: int | None = None
    else:
        # A zero mean is a real answer here (zero projected minutes), and its interval is (0, 0).
        low, high = nb_interval(mean, a_eff, interval_level)

    avg = _num(season_average)
    delta = mean - avg if avg is not None else None
    return StatProjection(
        stat_key=stat_key,
        mean=mean,
        sd=sd,
        low=low,
        high=high,
        interval_level=interval_level,
        season_average=avg,
        delta=delta,
        rate=rate,
        dispersion_alpha=alpha,
        dispersion_multiplier=c_hat,
        effective_alpha=a_eff,
        projected_minutes=minutes,
        context_multiplier=multiplier,
    )


def _thin_history_note(lines: Sequence[StatProjection]) -> str | None:
    thin = [
        ln.stat_key
        for ln in lines
        if ln.rate.shrinkage_weight < 0.5 or ln.rate.exposure_minutes < 100.0
    ]
    if not thin:
        return None
    names = ", ".join(key.upper() for key in thin)
    return (
        f"Thin history: {names} {'rates are' if len(thin) > 1 else 'rate is'} shrunk mostly to "
        "the league prior. Read the exposure and shrinkage weight beside the line."
    )


def project_box_score(
    stats: Sequence[StatInput | Mapping[str, Any]],
    *,
    minutes: MinutesProjection | Sequence[float],
    factors: Sequence[Factor] = (),
    interval_level: float | None = DEFAULT_INTERVAL_LEVEL,
    minutes_season_average: float | None = None,
    minutes_halflife_games: float = MINUTES_HALFLIFE_GAMES,
    include_combo: bool = True,
    player: Mapping[str, Any] | None = None,
    game: Mapping[str, Any] | None = None,
    notes: Sequence[str] = (),
    include_descriptors: bool = True,
) -> dict[str, Any]:
    """Assemble the whole ``next_game_projection`` payload of CONTRACT.md section 4.

    ``player`` and ``game`` are passed through verbatim — this module owns the mathematics, not
    the database, so the caller supplies the ``PlayerRef`` and the game context it has already
    loaded. ``game`` of ``None`` is the "no scheduled next game" case: the payload is still
    produced, against whatever context factors were handed in (league-average ones give 1.0
    throughout), and the reason is stated in ``notes``.

    The combination line is added when ``include_combo`` is set and PTS, REB and AST are all
    present, using the correlated variance identity.
    """
    normalised: list[StatInput] = []
    for item in stats:
        if isinstance(item, StatInput):
            normalised.append(item)
            continue
        key = str(item["stat_key"]) if "stat_key" in item else str(item["metric"])
        normalised.append(
            StatInput(
                stat_key=key,
                rate=item["rate"],
                dispersion_alpha=_num(item.get("dispersion_alpha")) or 0.0,
                dispersion_multiplier=_num(item.get("dispersion_multiplier")) or 1.0,
                season_average=_num(item.get("season_average")),
            )
        )

    m_mean, m_low, m_high = (
        minutes if isinstance(minutes, MinutesProjection) else MinutesProjection(*minutes)
    )
    multiplier = total_context_multiplier(factors)

    lines = [
        project_stat(
            s.stat_key,
            projected_minutes=m_mean,
            rate=s.rate,
            context_multiplier=multiplier,
            dispersion_alpha=s.dispersion_alpha,
            dispersion_multiplier=s.dispersion_multiplier,
            season_average=s.season_average,
            interval_level=interval_level,
        )
        for s in normalised
    ]
    by_key = {ln.stat_key: ln for ln in lines}

    combo_result: ComboProjection | None = None
    if include_combo and all(k in by_key for k in COMBO_METRICS):
        combo_result = combo(
            {k: by_key[k] for k in COMBO_METRICS},
            level=interval_level,
        )

    all_notes: list[str] = [
        "Projected minutes carry most of the error: supplying true minutes improves points "
        "RMSE by 19.3% and rebounds by 17.6%, far more than any model change.",
    ]
    if game is None:
        all_notes.append(
            "No scheduled next game; projected against a league-average opponent and neutral "
            "context."
        )
    thin = _thin_history_note(lines)
    if thin:
        all_notes.append(thin)
    if combo_result is not None and combo_result.sd_if_independent > 0:
        all_notes.append(
            f"{combo_result.label} uses the residual correlation: the independent calculation "
            f"would have claimed an SD of {combo_result.sd_if_independent:.2f} against "
            f"{combo_result.sd:.2f}."
        )
    if interval_level is None:
        all_notes.append(
            "Intervals are hidden by configuration; a projected mean on its own is misleading."
        )
    all_notes.extend(str(n) for n in notes)

    return {
        "player": dict(player) if player is not None else None,
        "game": dict(game) if game is not None else None,
        "projectedMinutes": {
            "value": _round(m_mean, 2),
            "displayValue": _display(MINUTES_METRIC_KEY, m_mean),
            "halfLifeGames": _as_number(minutes_halflife_games),
            "seasonAverage": _round(_num(minutes_season_average), 2),
            "low": _round(m_low, 2),
            "high": _round(m_high, 2),
        },
        "lines": [ln.payload(include_descriptor=include_descriptors) for ln in lines],
        "factors": [f.payload() for f in factors],
        "combo": combo_result.payload() if combo_result is not None else None,
        "method": {
            "summary": _METHOD_SUMMARY,
            "minutesHalfLifeGames": _as_number(minutes_halflife_games),
            "correlationApplied": combo_result is not None,
            "dispersionShrinkageGames": _as_number(DISPERSION_SHRINKAGE_GAMES),
        },
        "notes": all_notes,
    }
