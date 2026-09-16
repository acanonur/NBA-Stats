"""Fitted constants for the next-game projection, each traceable to the paper.

Source: *"Forecasting the Next-Game Box Score of an NBA Player: A Mathematical and Empirical
Treatment of Opportunity, Rate, Shrinkage, Dynamics and Market Translation"*, and the summary in
``docs/PROJECTION.md``. Every number below carries the table or equation it came from; nothing
here is a guess except the two blocks explicitly labelled DEFAULT, which exist only so the engine
has a neutral starting point before :func:`nbastats.projection.calibrate_context_factors` replaces
them with values fitted on real games.

Stdlib only. This module is imported by the pure projection engine and must stay free of
sqlalchemy, fastapi, numpy and scipy.

Layout
------
``STAT_CONSTANTS``      per-statistic league rate, stabilisation constant k and EWMA half-life
``PCT_STABILISATION``   attempts at which a shooting percentage stabilises
``COMBO_CORRELATION``   the PTS/REB/AST residual correlation matrix
the scalars            the blend, dispersion and interval constants

The one structural fact to take away from ``STAT_CONSTANTS``: ``k`` runs from 34 minutes
(rebounds) to 322 (steals), a factor of 9.5. Applying a single shrinkage to every statistic is
wrong in both directions at once, which is why the lookup is keyed by metric.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping, Sequence

__all__ = [
    "StatConstants",
    "UnknownProjectionStatError",
    "STAT_CONSTANTS",
    "PROJECTABLE_STATS",
    "MINUTES_HALFLIFE_GAMES",
    "MINUTES_METRIC_KEY",
    "MINUTES_INTERVAL_Z",
    "MINUTES_SD_FLOOR_FRACTION",
    "MINUTES_MAX",
    "PCT_STABILISATION",
    "SEASON_CAREER_BLEND_MINUTES",
    "FORM_BLEND_BASE_WEIGHT",
    "FORM_BLEND_FORM_WEIGHT",
    "DISPERSION_SHRINKAGE_GAMES",
    "DISPERSION_PRIOR_MEAN",
    "COMBO_METRICS",
    "COMBO_LABEL",
    "COMBO_CORRELATION",
    "DEFAULT_HOME_FACTORS",
    "DEFAULT_REST_FACTORS",
    "MAX_REST_DAYS",
    "DEFAULT_INTERVAL_LEVEL",
    "MIN_DISPERSION_ALPHA",
    "stat_constants",
    "has_stat",
    "league_rate",
    "stabilisation_k",
    "halflife_games",
    "pct_stabilisation_attempts",
    "correlation_for",
    "home_factor",
    "rest_factor",
    "clip_rest_days",
]


class UnknownProjectionStatError(KeyError):
    """Raised for a statistic the paper does not supply constants for."""


@dataclass(frozen=True, slots=True)
class StatConstants:
    """The three fitted numbers a counting statistic needs.

    ``league_rate_per_min``
        The league prior ``mu`` in the padding estimator: production per minute played, pooled
        over every player-game in the training seasons (paper Table B.3).
    ``k_minutes``
        The stabilisation constant, in minutes of exposure: ``k = sigma_e^2 / tau^2``, the
        exposure at which a player's own rate carries exactly as much weight as the league prior
        (paper Table B.3, estimated three independent ways in Chapter 5).
    ``halflife_games``
        Half-life of the exponentially weighted form term, in games, chosen by out-of-sample loss
        (paper Table B.4).
    """

    key: str
    name: str
    league_rate_per_min: float
    k_minutes: float
    halflife_games: float

    @property
    def half_weight_exposure(self) -> float:
        """Exposure at which the player and the prior carry equal weight — that is ``k``."""
        return self.k_minutes


# --- Paper Table B.3 (league rate, stabilisation constant k) and Table B.4 (EWMA half-life).
#     Reproduced in docs/PROJECTION.md section 2. Keys are contracts/metrics.json metric keys.
_STATS: Final[tuple[StatConstants, ...]] = (
    StatConstants("pts", "Points", 0.4182, 81.0, 6.0),
    StatConstants("reb", "Rebounds", 0.1762, 34.0, 8.0),
    StatConstants("ast", "Assists", 0.0915, 36.0, 8.0),
    StatConstants("fg3m", "3PM", 0.0315, 62.0, 12.0),
    StatConstants("tov", "Turnovers", 0.0570, 209.0, 15.0),
    StatConstants("blk", "Blocks", 0.0202, 69.0, 20.0),
    StatConstants("stl", "Steals", 0.0314, 322.0, 30.0),
)

#: Per-statistic constants, keyed by metric key. Read-only.
STAT_CONSTANTS: Final[Mapping[str, StatConstants]] = MappingProxyType(
    {s.key: s for s in _STATS}
)

#: The statistics this engine can project, in the paper's table order.
PROJECTABLE_STATS: Final[tuple[str, ...]] = tuple(s.key for s in _STATS)

#: Metric key for minutes in contracts/metrics.json.
MINUTES_METRIC_KEY: Final[str] = "min"

#: Minutes half-life, in games (paper Table B.4; docs/PROJECTION.md section 2).
#: Minutes need 2 games where production rates need 6-30. That gap is the quantitative
#: justification for modelling opportunity and rate separately: a rotation change is a discrete,
#: persistent event, while shooting ability drifts slowly, and no single-series model can weight
#: history correctly for both at once.
MINUTES_HALFLIFE_GAMES: Final[float] = 2.0

#: DEFAULT. Half-width of the projected-minutes interval, in standard deviations. 1.2816 is the
#: normal 80% central interval, matching DEFAULT_INTERVAL_LEVEL. The paper's minutes model is a
#: gradient-boosted quantile fit (Chapter 8), which needs a library this service does not carry;
#: the engine instead spreads the interval from the observed dispersion of a player's own recent
#: minutes. See docs/PROJECTION.md section 1 on why minutes uncertainty must stay visible.
MINUTES_INTERVAL_Z: Final[float] = 1.2816

#: DEFAULT. Floor on the projected-minutes standard deviation, as a fraction of the mean, so a
#: player with one or two logged games is not handed a falsely tight minutes range.
MINUTES_SD_FLOOR_FRACTION: Final[float] = 0.15

#: Regulation game length; the upper end of a minutes projection is clipped here.
MINUTES_MAX: Final[float] = 48.0

# --- Paper Table B.5: shooting percentages stabilise in ATTEMPTS, not minutes, so they take a
#     different exposure variable (binomial rather than exposure-proportional). Carried here
#     because the widget reports them; the counting-stat pipeline does not use them.
PCT_STABILISATION: Final[Mapping[str, float]] = MappingProxyType(
    {
        "fg3_pct": 275.0,  # three-point percentage, attempts
        "fg_pct": 129.0,  # field-goal percentage, attempts
        "ft_pct": 25.0,  # free-throw percentage, attempts
    }
)

#: Season/career blend constant, in minutes (docs/PROJECTION.md section 2):
#: ``w = n / (n + 400)`` with ``n`` the cumulative season minutes. At 400 season minutes the
#: season and career padding estimators carry equal weight.
SEASON_CAREER_BLEND_MINUTES: Final[float] = 400.0

#: Weight on the season/career blend in ``r_hat = 0.75*r + 0.25*r_form``.
FORM_BLEND_BASE_WEIGHT: Final[float] = 0.75

#: Weight on the EWMA form term in the same blend.
FORM_BLEND_FORM_WEIGHT: Final[float] = 0.25

#: k_c in paper eq. 10.5: the games of history at which a player's own volatility carries equal
#: weight to the prior multiplier of 1.0. Shrinking the dispersion multiplier this way lifts
#: coverage of the nominal 80% points interval from 69.9% to 79.1%.
DISPERSION_SHRINKAGE_GAMES: Final[float] = 60.0

#: Prior mean of the dispersion multiplier in eq. 10.5. A player with no history gets exactly
#: this, which is the intended honest degradation.
DISPERSION_PRIOR_MEAN: Final[float] = 1.0

#: Smallest dispersion alpha treated as genuinely negative-binomial; at or below it the
#: distribution is evaluated in its Poisson limit (Var = mu) instead of dividing by alpha.
MIN_DISPERSION_ALPHA: Final[float] = 1e-12

#: The combination line the widget offers.
COMBO_METRICS: Final[tuple[str, ...]] = ("pts", "reb", "ast")
COMBO_LABEL: Final[str] = "PTS+REB+AST"

# --- Paper Table B.13 / Figure 11.1: correlation of held-out forecast residuals. Reported to
#     three decimals as 0.296 (PTS-REB), 0.175 (PTS-AST) and 0.195 (REB-AST); docs/PROJECTION.md
#     section 5 and contracts/CONTRACT.md section 4 both carry the rounded matrix, and the
#     contract is what the client decodes, so the rounded values are what ship.
#     All three pairs are positive, so the correction to Var(sum) is strictly positive.
COMBO_CORRELATION: Final[tuple[tuple[float, ...], ...]] = (
    (1.00, 0.30, 0.17),
    (0.30, 1.00, 0.20),
    (0.17, 0.20, 1.00),
)

#: DEFAULT, to be replaced by :func:`nbastats.projection.calibrate_context_factors`.
#: Venue multiplier on the raw prediction. The paper fits this empirically as a ratio of realised
#: to raw-predicted totals within each bucket (eq. 4.9) rather than importing a published value,
#: and publishes no point estimate, so the fallback here is a small home tilt consistent with the
#: league's scoring split. It is deliberately near 1.0: a wrong default should be boring.
DEFAULT_HOME_FACTORS: Final[Mapping[bool, float]] = MappingProxyType(
    {True: 1.012, False: 0.988}
)

#: DEFAULT, to be replaced by :func:`nbastats.projection.calibrate_context_factors`.
#: Rest multiplier by days of rest, clipped to 0-3 as in eq. 4.9. 0 days is the second night of a
#: back-to-back. Fitted after the venue correction, so these are multipliers on top of it.
DEFAULT_REST_FACTORS: Final[Mapping[int, float]] = MappingProxyType(
    {0: 0.985, 1: 1.000, 2: 1.005, 3: 1.005}
)

#: Rest-day buckets are clipped to 0..3 (paper eq. 4.9: ``{0, 1, 2, 3+}``).
MAX_REST_DAYS: Final[int] = 3

#: Nominal interval level. The paper's calibration work (Chapter 10) is reported at 80%, and
#: contracts/widgets.json defaults the widget's ``interval`` config to "80".
DEFAULT_INTERVAL_LEVEL: Final[float] = 0.80


# --------------------------------------------------------------------------- lookup helpers


def stat_constants(stat_key: str) -> StatConstants:
    """Constants for one statistic.

    Raises :class:`UnknownProjectionStatError` rather than returning a plausible default: a
    statistic without a fitted ``k`` cannot be shrunk correctly, and silently borrowing another
    statistic's constant is the exact error docs/PROJECTION.md section 2 warns about.
    """
    try:
        return STAT_CONSTANTS[stat_key]
    except KeyError as exc:
        raise UnknownProjectionStatError(stat_key) from exc


def has_stat(stat_key: str) -> bool:
    """Whether the paper supplies constants for ``stat_key``."""
    return stat_key in STAT_CONSTANTS


def league_rate(stat_key: str) -> float:
    """League prior ``mu``, per minute played."""
    return stat_constants(stat_key).league_rate_per_min


def stabilisation_k(stat_key: str) -> float:
    """Stabilisation constant ``k``, in minutes of exposure."""
    return stat_constants(stat_key).k_minutes


def halflife_games(stat_key: str) -> float:
    """EWMA half-life for the form term, in games."""
    return stat_constants(stat_key).halflife_games


def pct_stabilisation_attempts(metric_key: str) -> float:
    """Attempts at which a shooting percentage stabilises (paper Table B.5)."""
    try:
        return PCT_STABILISATION[metric_key]
    except KeyError as exc:
        raise UnknownProjectionStatError(metric_key) from exc


def correlation_for(stat_keys: Sequence[str]) -> list[list[float]]:
    """The residual correlation sub-matrix for ``stat_keys``.

    Only the PTS/REB/AST block is estimated in the paper (Table B.13). Any other pairing is
    reported as uncorrelated — an honest 0 rather than a borrowed number — and the diagonal is
    always 1.
    """
    index = {key: i for i, key in enumerate(COMBO_METRICS)}
    size = len(stat_keys)
    out = [[0.0] * size for _ in range(size)]
    for i in range(size):
        out[i][i] = 1.0
    for i, a in enumerate(stat_keys):
        for j, b in enumerate(stat_keys):
            if i == j:
                continue
            ia, ib = index.get(a), index.get(b)
            if ia is not None and ib is not None:
                out[i][j] = COMBO_CORRELATION[ia][ib]
    return out


def clip_rest_days(rest_days: float | int | None) -> int | None:
    """Clip days of rest into the fitted buckets 0..3; ``None`` passes through unchanged."""
    if rest_days is None:
        return None
    try:
        value = int(rest_days)
    except (TypeError, ValueError):
        return None
    return max(0, min(MAX_REST_DAYS, value))


def home_factor(
    is_home: bool, factors: Mapping[bool, float] | Mapping[int, float] | None = None
) -> float:
    """Venue multiplier, from a calibrated mapping when supplied and the default otherwise."""
    table: Mapping = DEFAULT_HOME_FACTORS if factors is None else factors
    key = bool(is_home)
    if key in table:
        return float(table[key])
    alt = int(key)
    if alt in table:
        return float(table[alt])
    return float(DEFAULT_HOME_FACTORS[key])


def rest_factor(
    rest_days: float | int | None, factors: Mapping[int, float] | None = None
) -> float:
    """Rest multiplier for a bucket, clipping ``rest_days`` to 0..3.

    Unknown rest is neutral (1.0) rather than assumed: the projection says so in its explanation
    instead of quietly picking a bucket.
    """
    bucket = clip_rest_days(rest_days)
    if bucket is None:
        return 1.0
    table: Mapping[int, float] = DEFAULT_REST_FACTORS if factors is None else factors
    if bucket in table:
        return float(table[bucket])
    return float(DEFAULT_REST_FACTORS.get(bucket, 1.0))
