"""Tests for the next-game projection engine.

The engine is pure mathematics over plain dicts, so every test here runs without a database, a
request or a fixture file. Five kinds of test:

* **Constants against the paper.** Every number in ``projection_constants`` is asserted as a
  literal against ``docs/PROJECTION.md`` section 2 and the paper's Tables B.3/B.4/B.5/B.13. A
  refactor that quietly rounds ``k`` for steals fails here rather than in the app.
* **Worked examples**, with the arithmetic spelled out in the comment above the assertion.
* **Properties** checked with plain loops and a seeded :mod:`random` — no hypothesis, no numpy.
  The padding estimator's monotonicity, the additivity of pace deviations, the unimodal-interval
  minimality, and simulated coverage of the negative-binomial interval.
* **Degenerate inputs**: zero minutes, no history, one game, alpha of zero, no scheduled next
  game. The rule is that these degrade to the league prior and say so, never crash and never
  quietly look confident.
* **Contract and policy drift**: the payload's key sets against ``contracts/CONTRACT.md``
  section 4, the widget's default metric list against what the engine can actually project, and
  an AST scan asserting that no market-translation identifier and no numpy/scipy import ever
  appears in either module (docs/PROJECTION.md section 6).

The empirical claim the suite is built around: ``k`` varies by a factor of 9.5 across statistics,
so at the same exposure a steals rate is shrunk far harder toward the league prior than a
rebounding rate. That is asserted directly, in
:func:`test_steals_are_shrunk_far_harder_than_rebounds_at_equal_exposure`.
"""

from __future__ import annotations

import ast
import json
import math
import random
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from nbastats import catalog  # noqa: E402
from nbastats import projection as P  # noqa: E402
from nbastats import projection_constants as K  # noqa: E402

IDENTITIES_JSON = BACKEND_ROOT / "nbastats" / "data" / "nba_identities.json"
PROJECTION_SOURCES = (
    BACKEND_ROOT / "nbastats" / "projection.py",
    BACKEND_ROOT / "nbastats" / "projection_constants.py",
)


# --------------------------------------------------------------------------- helpers


def _poisson_draw(rng: random.Random, lam: float) -> int:
    """Knuth's Poisson sampler — stdlib only, and independent of the module under test."""
    if lam <= 0:
        return 0
    target = math.exp(-lam)
    k, product = 0, 1.0
    while True:
        product *= rng.random()
        if product <= target:
            return k
        k += 1


def _nb_draw(rng: random.Random, mu: float, alpha: float) -> int:
    """A negative-binomial draw as a gamma-Poisson mixture.

    ``lambda ~ Gamma(shape=1/alpha, scale=mu*alpha)`` has mean ``mu`` and variance ``alpha*mu^2``;
    mixing Poisson over it gives ``Var = mu + alpha*mu^2``. This reaches the same law as
    :mod:`nbastats.projection` by a completely different route, so coverage tests below are a
    check on the implementation rather than a tautology.
    """
    if alpha <= 0:
        return _poisson_draw(rng, mu)
    shape = 1.0 / alpha
    return _poisson_draw(rng, rng.gammavariate(shape, mu * alpha))


def _wide_support(mu: float, alpha: float, sds: float = 40.0) -> int:
    """Far enough into the tail that anything left out is well below the tolerances used here."""
    return int(mu + sds * math.sqrt(mu + alpha * mu * mu)) + 50


def _pmf_mean_var(mu: float, alpha: float) -> tuple[float, float]:
    """First two moments computed from the pmf itself."""
    table = P.nb_pmf_table(mu, alpha, _wide_support(mu, alpha))
    m = sum(k * p for k, p in enumerate(table))
    m2 = sum(k * k * p for k, p in enumerate(table))
    return m, m2 - m * m


def _interval_mass(lo: int, hi: int, mu: float, alpha: float) -> float:
    return sum(P.nb_pmf(k, mu, alpha) for k in range(lo, hi + 1))


def _flat_rate(stat_key: str, minutes: float, rate_per_min: float) -> P.RegressedRate:
    """A rate built from season history only, with no form term — the clean padding case."""
    return P.regressed_rate(stat_key, minutes * rate_per_min, minutes)


# =========================================================================== constants


class TestConstants:
    """Every fitted number, asserted against docs/PROJECTION.md section 2."""

    def test_per_stat_table_matches_the_paper(self) -> None:
        # docs/PROJECTION.md section 2, "Constants (paper Table B.3 and B.4)".
        expected = {
            "pts": (0.4182, 81, 6),
            "reb": (0.1762, 34, 8),
            "ast": (0.0915, 36, 8),
            "fg3m": (0.0315, 62, 12),
            "tov": (0.0570, 209, 15),
            "blk": (0.0202, 69, 20),
            "stl": (0.0314, 322, 30),
        }
        assert set(K.STAT_CONSTANTS) == set(expected)
        for key, (mu, k, halflife) in expected.items():
            consts = K.stat_constants(key)
            assert consts.league_rate_per_min == mu
            assert consts.k_minutes == k
            assert consts.halflife_games == halflife
            assert K.league_rate(key) == mu
            assert K.stabilisation_k(key) == k
            assert K.halflife_games(key) == halflife

    def test_minutes_half_life_is_two_games(self) -> None:
        # The whole justification for separating opportunity from rate: minutes decay in 2 games,
        # production rates in 6-30.
        assert K.MINUTES_HALFLIFE_GAMES == 2
        assert min(c.halflife_games for c in K.STAT_CONSTANTS.values()) == 6

    def test_k_spans_a_factor_of_nine_and_a_half(self) -> None:
        # "it varies by a factor of 9.5 across statistics. That variation is the whole point."
        ks = [c.k_minutes for c in K.STAT_CONSTANTS.values()]
        assert max(ks) / min(ks) == pytest.approx(9.5, abs=0.05)
        assert max(ks) == K.stabilisation_k("stl")
        assert min(ks) == K.stabilisation_k("reb")

    def test_shooting_percentage_stabilisation_in_attempts(self) -> None:
        assert K.pct_stabilisation_attempts("fg3_pct") == 275
        assert K.pct_stabilisation_attempts("fg_pct") == 129
        assert K.pct_stabilisation_attempts("ft_pct") == 25

    def test_blend_and_dispersion_constants(self) -> None:
        assert K.SEASON_CAREER_BLEND_MINUTES == 400
        assert K.FORM_BLEND_BASE_WEIGHT == 0.75
        assert K.FORM_BLEND_FORM_WEIGHT == 0.25
        assert K.FORM_BLEND_BASE_WEIGHT + K.FORM_BLEND_FORM_WEIGHT == 1.0
        assert K.DISPERSION_SHRINKAGE_GAMES == 60
        assert K.DISPERSION_PRIOR_MEAN == 1.0
        assert K.MAX_REST_DAYS == 3
        assert K.DEFAULT_INTERVAL_LEVEL == 0.80

    def test_correlation_matrix_matches_the_contract(self) -> None:
        # contracts/CONTRACT.md section 4 and docs/PROJECTION.md section 5.
        assert [list(row) for row in K.COMBO_CORRELATION] == [
            [1.00, 0.30, 0.17],
            [0.30, 1.00, 0.20],
            [0.17, 0.20, 1.00],
        ]
        for i in range(3):
            assert K.COMBO_CORRELATION[i][i] == 1.0
            for j in range(3):
                # Symmetric, and every pair positively dependent: the correction to Var(sum) is
                # strictly positive, which is why ignoring it understates the spread.
                assert K.COMBO_CORRELATION[i][j] == K.COMBO_CORRELATION[j][i]
                assert K.COMBO_CORRELATION[i][j] > 0

    def test_correlation_for_subsets_and_unknown_keys(self) -> None:
        assert K.correlation_for(["pts", "reb", "ast"]) == [
            [1.0, 0.30, 0.17],
            [0.30, 1.0, 0.20],
            [0.17, 0.20, 1.0],
        ]
        assert K.correlation_for(["ast", "pts"]) == [[1.0, 0.17], [0.17, 1.0]]
        # An unestimated pair is reported as uncorrelated, never handed a borrowed rho.
        assert K.correlation_for(["pts", "blk"]) == [[1.0, 0.0], [0.0, 1.0]]
        assert K.correlation_for([]) == []

    def test_unknown_stat_raises_rather_than_borrowing_a_constant(self) -> None:
        for bad in ("fg_pct", "plus_minus", "", "PTS"):
            with pytest.raises(K.UnknownProjectionStatError):
                K.stat_constants(bad)
            assert K.has_stat(bad) is False
        assert K.has_stat("pts") is True

    def test_rest_days_clip_to_the_fitted_buckets(self) -> None:
        assert K.clip_rest_days(-4) == 0
        assert K.clip_rest_days(0) == 0
        assert K.clip_rest_days(3) == 3
        assert K.clip_rest_days(11) == 3
        assert K.clip_rest_days(None) is None
        assert K.clip_rest_days("nonsense") is None

    def test_default_context_multipliers_are_near_neutral(self) -> None:
        # These are the documented fallbacks, replaced by calibrate_context_factors once there is
        # history. A wrong default should be boring.
        for value in list(K.DEFAULT_HOME_FACTORS.values()) + list(K.DEFAULT_REST_FACTORS.values()):
            assert 0.95 < value < 1.05
        assert K.DEFAULT_HOME_FACTORS[True] > K.DEFAULT_HOME_FACTORS[False]
        assert K.DEFAULT_REST_FACTORS[0] < K.DEFAULT_REST_FACTORS[2]
        assert set(K.DEFAULT_REST_FACTORS) == {0, 1, 2, 3}

    def test_widget_default_metric_list_is_projectable(self) -> None:
        """Contract drift: the widget cannot default to a stat the engine has no constants for."""
        descriptor = catalog.widget("next_game_projection")
        stats_field = next(f for f in descriptor["config"] if f["key"] == "stats")
        assert stats_field["default"]
        for key in stats_field["default"]:
            assert K.has_stat(key), key
        assert set(K.PROJECTABLE_STATS) <= set(catalog.metric_keys())
        assert K.MINUTES_METRIC_KEY in catalog.metric_keys()


# =========================================================================== EWMA


class TestEwma:
    def test_constant_series_returns_that_constant(self) -> None:
        for halflife in (1, 2, 6, 8, 12, 15, 20, 30):
            assert P.ewma([7.5] * 25, halflife) == pytest.approx(7.5)
            assert P.ewma([7.5], halflife) == pytest.approx(7.5)

    def test_an_observation_one_half_life_old_carries_half_the_weight(self) -> None:
        # Series A puts a 1 exactly h games before the most recent slot; series B puts it in the
        # most recent slot. The ratio of the two EWMAs is the relative weight, which must be 0.5.
        for halflife in (2, 6, 8, 12, 15, 20, 30):
            h = int(halflife)
            oldest = P.ewma([1.0] + [0.0] * h, halflife)
            newest = P.ewma([0.0] * h + [1.0], halflife)
            assert oldest / newest == pytest.approx(0.5, rel=1e-12)

    def test_most_recent_is_last(self) -> None:
        rising = P.ewma([10, 20, 30, 40], 2)
        falling = P.ewma([40, 30, 20, 10], 2)
        assert rising > falling
        flat = sum([10, 20, 30, 40]) / 4
        assert rising > flat  # weighted toward the 40 at the end
        assert falling < flat

    def test_fewer_observations_than_the_half_life_still_works(self) -> None:
        # Three games at a 30-game half-life: weights are nearly flat, so the answer sits just
        # above the flat mean, not damped toward zero.
        value = P.ewma([10, 20, 30], 30)
        assert value == pytest.approx(20.15, abs=0.05)
        assert 20 < value < 21
        assert P.ewma([12.0], 30) == pytest.approx(12.0)

    def test_gaps_are_skipped_but_still_age_the_series(self) -> None:
        # [10, None, 20] with h=2: the 10 is two slots old, so weight 0.5 against the 20's 1.0.
        assert P.ewma([10, None, 20], 2) == pytest.approx((10 * 0.5 + 20) / 1.5)
        # Without the gap the 10 is only one slot old and counts for more, so the mean is lower.
        assert P.ewma([10, 20], 2) < P.ewma([10, None, 20], 2)
        assert P.ewma([None, None, 5.0], 6) == pytest.approx(5.0)
        assert P.ewma([float("nan"), 4.0], 6) == pytest.approx(4.0)

    def test_empty_and_unusable_series_return_zero(self) -> None:
        assert P.ewma([], 6) == 0.0
        assert P.ewma([None, None], 6) == 0.0
        assert P.ewma([float("inf"), None], 6) == 0.0

    def test_non_positive_half_life_keeps_only_the_latest_observation(self) -> None:
        assert P.ewma([10, 20, 30], 0) == 30.0
        assert P.ewma([10, 20, None], -5) == 20.0

    def test_result_is_always_inside_the_observed_range(self) -> None:
        rng = random.Random(11)
        for _ in range(200):
            values = [rng.uniform(0, 40) for _ in range(rng.randint(1, 20))]
            halflife = rng.choice([1, 2, 6, 8, 30])
            assert min(values) - 1e-9 <= P.ewma(values, halflife) <= max(values) + 1e-9


# =========================================================================== minutes


class TestProjectMinutes:
    def test_unpacks_as_mean_low_high(self) -> None:
        mean, low, high = P.project_minutes([30, 32, 34], 31.0)
        assert low < mean < high
        assert isinstance(P.project_minutes([30], None), tuple)

    def test_uses_the_two_game_half_life(self) -> None:
        recent = [24, 26, 38, 40]
        mean, _, _ = P.project_minutes(recent, 20.0)
        assert mean == pytest.approx(P.ewma(recent, K.MINUTES_HALFLIFE_GAMES))
        # A 2-game half-life reacts fast: a rotation change is a discrete, persistent event.
        assert mean > sum(recent) / len(recent)

    def test_season_average_is_a_fallback_and_never_blended_in(self) -> None:
        with_history = P.project_minutes([30, 30, 30], 12.0).mean
        assert with_history == pytest.approx(30.0)  # the season average did not drag it down
        no_history = P.project_minutes([], 26.5)
        assert no_history.mean == pytest.approx(26.5)
        assert no_history.low < 26.5 < no_history.high

    def test_no_history_at_all_is_zero_rather_than_a_guess(self) -> None:
        assert P.project_minutes([], None) == (0.0, 0.0, 0.0)
        assert P.project_minutes([None, None], None) == (0.0, 0.0, 0.0)
        assert P.project_minutes([0, 0, 0], 0.0) == (0.0, 0.0, 0.0)

    def test_single_game_still_gets_an_honest_interval(self) -> None:
        mean, low, high = P.project_minutes([32.0], None)
        assert mean == pytest.approx(32.0)
        # One logged game cannot produce a tight range: the floor is 15% of the mean.
        assert high - low > 0.25 * mean
        assert low >= 0.0

    def test_interval_is_clipped_to_a_regulation_game(self) -> None:
        mean, low, high = P.project_minutes([47, 48, 48], None)
        assert high <= K.MINUTES_MAX
        assert low >= 0.0
        # A small mean keeps its interval inside [0, 48] too.
        small = P.project_minutes([2.0], None)
        assert small.low >= 0.0 and small.high <= K.MINUTES_MAX

    def test_volatile_minutes_widen_the_interval(self) -> None:
        steady = P.project_minutes([30, 30, 31, 30, 30], None)
        erratic = P.project_minutes([8, 38, 12, 41, 30], None)
        assert (erratic.high - erratic.low) > (steady.high - steady.low)

    def test_interval_can_be_suppressed(self) -> None:
        mean, low, high = P.project_minutes([30, 32], None, level=None)
        assert low == high == mean


# =========================================================================== regressed rate


class TestRegressedRate:
    def test_zero_exposure_is_the_league_prior_exactly(self) -> None:
        for key in K.PROJECTABLE_STATS:
            rate = P.regressed_rate(key, 0.0, 0.0)
            assert rate.rate == K.league_rate(key)
            assert rate.shrinkage_weight == 0.0
            assert rate.prior_weight == 1.0
            assert rate.exposure_minutes == 0.0
            assert rate.observed_rate is None
            assert rate.form_rate is None

    def test_enormous_exposure_converges_to_the_observed_rate(self) -> None:
        # 40,000 minutes at 0.62 points a minute, with recent form agreeing.
        rate = P.regressed_rate("pts", 24_800, 40_000, 24_800, 40_000, 21.7, 35.0)
        assert rate.rate == pytest.approx(0.62, abs=2e-3)
        assert rate.shrinkage_weight > 0.99
        assert rate.observed_rate == pytest.approx(0.62)

    def test_shrinkage_weight_is_monotone_in_exposure(self) -> None:
        previous = -1.0
        previous_rate = K.league_rate("ast")
        for minutes in (0, 10, 50, 100, 400, 1_000, 5_000, 30_000):
            rate = _flat_rate("ast", minutes, 0.20)
            assert rate.shrinkage_weight > previous
            # ...and so is the rate itself, which starts at the prior and climbs toward 0.20.
            assert rate.rate >= previous_rate
            previous = rate.shrinkage_weight
            previous_rate = rate.rate
        assert previous < 1.0

    def test_steals_are_shrunk_far_harder_than_rebounds_at_equal_exposure(self) -> None:
        """The paper's central empirical claim, made visible.

        At the same 200 minutes of exposure, with each player producing at exactly twice his
        statistic's league prior, the fraction of the way the estimate travels from the prior to
        the observed rate is ``M / (M + k)``: 200/234 = 0.855 for rebounds against 200/522 = 0.383
        for steals. Rebounding volume is a structural consequence of role and size; steals are
        rare events dominated by opportunity.
        """
        exposure = 200.0
        travelled = {}
        for key in ("reb", "stl"):
            mu = K.league_rate(key)
            observed = 2.0 * mu
            rate = _flat_rate(key, exposure, observed)
            travelled[key] = (rate.rate - mu) / (observed - mu)
            # Exactly the padding weight, which is the point of the formula.
            assert travelled[key] == pytest.approx(exposure / (exposure + K.stabilisation_k(key)))

        assert travelled["reb"] == pytest.approx(0.8547, abs=1e-3)
        assert travelled["stl"] == pytest.approx(0.3831, abs=1e-3)
        assert travelled["reb"] > 2.0 * travelled["stl"]
        # The ordering holds across the whole table, exposure held fixed.
        ordered = sorted(K.PROJECTABLE_STATS, key=K.stabilisation_k)
        fractions = [
            (_flat_rate(k, exposure, 2 * K.league_rate(k)).rate - K.league_rate(k))
            / K.league_rate(k)
            for k in ordered
        ]
        assert fractions == sorted(fractions, reverse=True)

    def test_padding_estimator_is_exact(self) -> None:
        # reb: k = 34, mu = 0.1762. (60 + 34*0.1762) / (200 + 34) = 65.9908 / 234.
        rate = _flat_rate("reb", 200.0, 0.30)
        assert rate.rate == pytest.approx((60 + 34 * 0.1762) / 234)
        assert rate.season_rate == pytest.approx(rate.career_rate)
        assert rate.blended_rate == pytest.approx(rate.rate)  # no form term to blend in

    def test_season_and_career_carry_equal_weight_at_four_hundred_minutes(self) -> None:
        # w = n / (n + 400) = 0.5 at n = 400.
        rate = P.regressed_rate("pts", 240.0, 400.0, 3000.0, 10_000.0)
        assert rate.season_weight == pytest.approx(0.5)
        r_season = (240 + 81 * 0.4182) / (400 + 81)
        r_career = (3000 + 81 * 0.4182) / (10_000 + 81)
        assert rate.season_rate == pytest.approx(r_season)
        assert rate.career_rate == pytest.approx(r_career)
        assert rate.rate == pytest.approx(0.5 * r_season + 0.5 * r_career)

    def test_season_weight_climbs_with_season_minutes(self) -> None:
        for minutes, expected in ((0, 0.0), (100, 0.2), (400, 0.5), (1200, 0.75)):
            rate = P.regressed_rate("pts", minutes * 0.5, minutes, 10_000 * 0.4, 10_000)
            assert rate.season_weight == pytest.approx(expected)

    def test_form_enters_at_one_quarter_weight(self) -> None:
        # No exposure at all, so the base term is exactly the prior: 0.75*0.4182 + 0.25*1.0.
        rate = P.regressed_rate("pts", 0, 0, 0, 0, 30.0, 30.0)
        assert rate.form_rate == pytest.approx(1.0)
        assert rate.rate == pytest.approx(0.75 * 0.4182 + 0.25 * 1.0)
        # The form term is the player's own play, so it lifts the player's share to 0.25.
        assert rate.shrinkage_weight == pytest.approx(0.25)

    def test_form_is_dropped_not_zeroed_when_there_are_no_recent_minutes(self) -> None:
        base = P.regressed_rate("reb", 300.0, 1500.0, 2000.0, 12_000.0)
        for form in ((None, None), (0.0, 0.0), (5.0, 0.0), (None, 30.0)):
            rate = P.regressed_rate("reb", 300.0, 1500.0, 2000.0, 12_000.0, form[0], form[1])
            assert rate.form_rate is None
            assert rate.rate == pytest.approx(base.rate)
            assert rate.rate == pytest.approx(base.blended_rate)

    def test_career_defaults_to_the_season_when_not_supplied(self) -> None:
        explicit = P.regressed_rate("ast", 150.0, 900.0, 150.0, 900.0)
        implied = P.regressed_rate("ast", 150.0, 900.0)
        assert implied.rate == pytest.approx(explicit.rate)
        assert implied.career_minutes == 900.0

    def test_exposure_reported_is_the_larger_of_season_and_career(self) -> None:
        rate = P.regressed_rate("blk", 40.0, 500.0, 300.0, 9_000.0)
        assert rate.exposure_minutes == 9_000.0
        assert rate.season_minutes == 500.0
        assert rate.career_minutes == 9_000.0

    def test_degenerate_inputs_are_clamped_not_crashed(self) -> None:
        rate = P.regressed_rate("tov", -5.0, -20.0, None, None, float("nan"), float("inf"))
        assert rate.rate == pytest.approx(K.league_rate("tov"))
        assert rate.exposure_minutes == 0.0
        assert rate.shrinkage_weight == 0.0
        one_game = P.regressed_rate("pts", 31.0, 38.0, 31.0, 38.0, 31.0, 38.0)
        assert 0 < one_game.shrinkage_weight < 0.6  # one game is mostly prior
        assert K.league_rate("pts") < one_game.rate < 31.0 / 38.0

    def test_unknown_stat_raises(self) -> None:
        with pytest.raises(K.UnknownProjectionStatError):
            P.regressed_rate("dunks", 10, 100)


# =========================================================================== context factors


class TestContextFactors:
    def _factors(self, **kwargs) -> dict[str, P.Factor]:
        args = dict(
            team_pace=100.0,
            opp_pace=100.0,
            league_pace=100.0,
            opp_def_rtg=113.0,
            league_def_rtg=113.0,
            is_home=True,
            rest_days=1,
        )
        args.update(kwargs)
        return {f.key: f for f in P.context_factors(**args)}

    def test_two_league_average_teams_give_exactly_one(self) -> None:
        for pace in (88.0, 99.4, 104.7):
            factors = self._factors(team_pace=pace, opp_pace=pace, league_pace=pace)
            assert factors["pace"].value == 1.0
            assert factors["opponent"].value == 1.0

    def test_tempo_deviations_add(self) -> None:
        """Section 3's motivation for the product-over-league form, checked numerically.

        Pace_tm = Pace_lg(1+a) and Pace_opp = Pace_lg(1+b) give f_pace = (1+a)(1+b) = 1+a+b+ab,
        so the two teams' deviations add up to the second-order term ab. A simple average of the
        two paces would instead give 1 + (a+b)/2 — half the effect.
        """
        league = 99.4
        for a, b in ((0.02, 0.03), (0.05, -0.04), (0.01, 0.01), (-0.03, -0.02)):
            factors = self._factors(
                team_pace=league * (1 + a), opp_pace=league * (1 + b), league_pace=league
            )
            value = factors["pace"].value
            assert value == pytest.approx((1 + a) * (1 + b))
            assert value == pytest.approx(1 + a + b, abs=abs(a * b) + 1e-12)
            # The additive approximation is good to the product of the deviations, which for
            # realistic tempo gaps is under a tenth of a percent.
            assert abs(value - (1 + a + b)) <= 2.5e-3
            # An average of the two paces would move only half as far from 1.0.
            averaged = ((league * (1 + a) + league * (1 + b)) / 2) / league
            if a + b != 0:
                assert abs(value - 1) > abs(averaged - 1) - 1e-9

    def test_pace_is_the_product_over_the_league_squared(self) -> None:
        factors = self._factors(team_pace=100.2, opp_pace=99.0, league_pace=99.4)
        assert factors["pace"].value == pytest.approx(100.2 * 99.0 / (99.4**2))

    def test_opponent_factor_is_the_defensive_rating_ratio(self) -> None:
        factors = self._factors(opp_def_rtg=111.8, league_def_rtg=113.5)
        assert factors["opponent"].value == pytest.approx(111.8 / 113.5)
        assert factors["opponent"].value < 1.0  # a better defence projects production down
        assert "stronger" in factors["opponent"].explanation
        weak = self._factors(opp_def_rtg=118.0, league_def_rtg=113.5)
        assert weak["opponent"].value > 1.0
        assert "weaker" in weak["opponent"].explanation

    def test_venue_and_rest_use_the_documented_defaults(self) -> None:
        home = self._factors(is_home=True, rest_days=0)
        away = self._factors(is_home=False, rest_days=0)
        assert home["venue"].value == K.DEFAULT_HOME_FACTORS[True]
        assert away["venue"].value == K.DEFAULT_HOME_FACTORS[False]
        assert home["rest"].value == K.DEFAULT_REST_FACTORS[0]
        assert "back-to-back" in home["rest"].explanation
        # Defaults say so on screen rather than passing themselves off as fitted.
        assert "Default multiplier" in home["venue"].explanation
        assert "Default multiplier" in home["rest"].explanation

    def test_rest_days_are_clipped_to_zero_through_three(self) -> None:
        assert self._factors(rest_days=9)["rest"].value == K.DEFAULT_REST_FACTORS[3]
        assert self._factors(rest_days=-2)["rest"].value == K.DEFAULT_REST_FACTORS[0]
        assert "Three or more" in self._factors(rest_days=7)["rest"].explanation

    def test_unknown_inputs_are_neutral_and_say_so(self) -> None:
        unknown = self._factors(
            team_pace=None, opp_pace=None, league_pace=None, opp_def_rtg=None, rest_days=None
        )
        assert unknown["pace"].value == 1.0
        assert unknown["opponent"].value == 1.0
        assert unknown["rest"].value == 1.0
        assert "unavailable" in unknown["pace"].explanation
        assert "unavailable" in unknown["opponent"].explanation
        assert "unknown" in unknown["rest"].explanation
        # A zero or negative league pace cannot divide, and must not raise.
        assert self._factors(league_pace=0.0)["pace"].value == 1.0
        assert self._factors(league_def_rtg=0.0)["opponent"].value == 1.0

    def test_all_four_factors_are_reported_in_order(self) -> None:
        factors = P.context_factors(100.0, 100.0, 100.0, 113.0, 113.0, True, 1)
        assert [f.key for f in factors] == ["pace", "opponent", "venue", "rest"]
        assert all(f.label and f.explanation for f in factors)
        assert all(math.isfinite(f.value) for f in factors)
        payload = factors[0].payload()
        assert set(payload) == {"key", "label", "value", "explanation"}

    def test_total_multiplier_is_the_product(self) -> None:
        factors = P.context_factors(102.0, 101.0, 100.0, 116.0, 113.0, True, 3)
        expected = 1.0
        for f in factors:
            expected *= f.value
        assert P.total_context_multiplier(factors) == pytest.approx(expected)
        assert P.total_context_multiplier([]) == 1.0


# =========================================================================== contributions


class TestFactorContribution:
    """``mean * (factor - 1)`` — the "Why" section of docs/BROADSHEET.md section 5."""

    def test_a_neutral_factor_contributes_nothing(self) -> None:
        assert P.factor_contribution(28.4, 1.0) == 0.0

    def test_the_sign_follows_the_factor(self) -> None:
        assert P.factor_contribution(28.4, 1.021) > 0
        assert P.factor_contribution(28.4, 0.979) < 0

    def test_it_scales_with_the_mean(self) -> None:
        """A 2% factor is worth more points to a 30-point scorer than to a 10-point one."""
        assert P.factor_contribution(30.0, 1.02) == pytest.approx(0.6)
        assert P.factor_contribution(10.0, 1.02) == pytest.approx(0.2)

    def test_it_is_first_order_and_the_error_is_second_order(self) -> None:
        """The headline caveat, made quantitative rather than left in a docstring.

        Two factors of a+b deviation give a product of ``1 + a + b + ab``, so the sum of the
        contributions misses the neutral-to-actual difference by exactly ``mean*ab/(1+a+b+ab)``
        of the mean. Small while a and b are small, never zero, and the reason the payload is
        documented as the largest movers rather than as a column that adds up.
        """
        a, b = 0.02, 0.03
        multiplier = (1.0 + a) * (1.0 + b)
        mean = 30.0
        neutral = mean / multiplier

        summed = P.factor_contribution(mean, 1.0 + a) + P.factor_contribution(mean, 1.0 + b)
        truth = mean - neutral

        assert summed != pytest.approx(truth, abs=1e-9), "a decomposition it is not"
        # ...but close enough to be worth showing: under a tenth of a point here.
        assert abs(summed - truth) < 0.1

    def test_the_payload_carries_contributions_only_when_asked(self) -> None:
        factor = P.Factor("pace", "Pace", 1.021, "faster")
        assert "contributions" not in factor.payload()
        with_contributions = factor.payload({"pts": 28.4 * 0.021, "reb": 8.0 * 0.021})
        assert with_contributions["contributions"] == {"pts": 0.6, "reb": 0.17}


# =========================================================================== calibration


class TestCalibrateContextFactors:
    TRUE_HOME = {True: 1.03, False: 0.97}
    TRUE_REST = {0: 0.90, 1: 1.00, 2: 1.10, 3: 1.00}

    def _rows(self, per_cell: int = 40) -> list[dict]:
        """A balanced design: the rest mix is identical at home and away, and the raw-weighted
        mean of the true rest factors is exactly 1.0, so both stages recover their factors
        exactly."""
        rng = random.Random(4)
        raws = [rng.uniform(5.0, 30.0) for _ in range(per_cell)]
        rows = []
        for is_home in (True, False):
            for bucket in (0, 1, 2, 3):
                for raw in raws:
                    rows.append(
                        {
                            "actual": raw * self.TRUE_HOME[is_home] * self.TRUE_REST[bucket],
                            "raw_predicted": raw,
                            "is_home": is_home,
                            "rest_days": bucket,
                        }
                    )
        return rows

    def test_recovers_the_factors_it_was_generated_from(self) -> None:
        fitted = P.calibrate_context_factors(self._rows())
        assert fitted.home_factors[True] == pytest.approx(1.03, rel=1e-9)
        assert fitted.home_factors[False] == pytest.approx(0.97, rel=1e-9)
        for bucket, value in self.TRUE_REST.items():
            assert fitted.rest_factors[bucket] == pytest.approx(value, rel=1e-9)
        assert fitted.fully_calibrated
        assert fitted.n_rows == 320
        assert fitted.rest_rows == {0: 80, 1: 80, 2: 80, 3: 80}

    def test_rest_is_fitted_after_the_venue_correction(self) -> None:
        # Away games only: the venue factor absorbs its own bias, so the rest factors still come
        # back as generated rather than carrying the venue effect twice.
        rows = [r for r in self._rows() if not r["is_home"]]
        fitted = P.calibrate_context_factors(rows)
        assert fitted.home_factors[False] == pytest.approx(0.97, rel=1e-9)
        for bucket, value in self.TRUE_REST.items():
            assert fitted.rest_factors[bucket] == pytest.approx(value, rel=1e-9)
        assert fitted.defaulted_home == (True,)  # no home rows, so that bucket keeps its default
        assert fitted.home_factors[True] == K.DEFAULT_HOME_FACTORS[True]

    def test_a_ratio_of_sums_is_not_distorted_by_a_near_zero_denominator(self) -> None:
        rows = self._rows()
        # One low-minute game whose ratio of realised to predicted is five million.
        rows.append(
            {"actual": 5.0, "raw_predicted": 1e-6, "is_home": True, "rest_days": 1}
        )
        fitted = P.calibrate_context_factors(rows)
        assert fitted.home_factors[True] == pytest.approx(1.03, abs=0.01)
        # A mean of ratios would have produced something in the thousands.
        assert fitted.home_factors[True] < 1.1

    def test_thin_buckets_keep_their_documented_defaults(self) -> None:
        fitted = P.calibrate_context_factors(self._rows(per_cell=2), min_rows=25)
        assert not fitted.fully_calibrated
        assert set(fitted.defaulted_home) == {True, False}
        assert set(fitted.defaulted_rest) == {0, 1, 2, 3}
        assert fitted.home_factors == dict(K.DEFAULT_HOME_FACTORS)
        assert fitted.rest_factors == dict(K.DEFAULT_REST_FACTORS)

    def test_empty_and_unusable_rows(self) -> None:
        empty = P.calibrate_context_factors([])
        assert empty.n_rows == 0
        assert empty.home_factors == dict(K.DEFAULT_HOME_FACTORS)
        junk = P.calibrate_context_factors(
            [
                {"actual": None, "raw_predicted": 10.0, "is_home": True, "rest_days": 1},
                {"actual": 10.0, "raw_predicted": 0.0, "is_home": True, "rest_days": 1},
                {"actual": 10.0, "raw_predicted": -3.0, "is_home": False, "rest_days": 1},
                {"actual": 10.0, "raw_predicted": 9.0, "is_home": True, "rest_days": None},
            ]
        )
        assert junk.n_rows == 1  # only the last row is usable, and it has no rest bucket
        assert junk.rest_rows == {0: 0, 1: 0, 2: 0, 3: 0}

    def test_fitted_factors_flow_into_context_factors(self) -> None:
        fitted = P.calibrate_context_factors(self._rows())
        factors = {
            f.key: f
            for f in P.context_factors(
                100.0, 100.0, 100.0, 113.0, 113.0, True, 2, calibration=fitted
            )
        }
        assert factors["venue"].value == pytest.approx(1.03, rel=1e-9)
        assert factors["rest"].value == pytest.approx(1.10, rel=1e-9)
        # Once fitted, the explanation stops calling itself a default.
        assert "Default multiplier" not in factors["venue"].explanation
        assert "Default multiplier" not in factors["rest"].explanation


# =========================================================================== negative binomial


class TestNegativeBinomial:
    CASES = ((10.0, 0.1), (28.4, 0.061), (2.5, 0.4), (0.6, 1.2), (45.0, 0.02))

    def test_pmf_sums_to_one_over_a_wide_support(self) -> None:
        for mu, alpha in self.CASES:
            support = _wide_support(mu, alpha, sds=60.0)
            total = sum(P.nb_pmf_table(mu, alpha, support))
            assert total == pytest.approx(1.0, abs=1e-9)
            # ...and the one-at-a-time accessor agrees with the table it is summed from.
            table = P.nb_pmf_table(mu, alpha, 40)
            for k in (0, 1, 7, 40):
                assert P.nb_pmf(k, mu, alpha) == pytest.approx(table[k], rel=1e-12)
            assert len(P.nb_pmf_table(mu, alpha)) > int(mu)

    def test_moments_match_the_variance_law(self) -> None:
        for mu, alpha in self.CASES:
            mean, var = _pmf_mean_var(mu, alpha)
            assert mean == pytest.approx(mu, rel=1e-6)
            assert var == pytest.approx(mu + alpha * mu * mu, rel=1e-6)
            assert P.nb_variance(mu, alpha) == pytest.approx(mu + alpha * mu**2)
            dist = P.NegativeBinomial(mu, alpha)
            assert dist.sd == pytest.approx(math.sqrt(mu + alpha * mu**2))

    def test_params_follow_the_contract_formulas(self) -> None:
        r, p = P.nb_params(20.0, 0.05)
        assert r == pytest.approx(1 / 0.05)
        assert p == pytest.approx(r / (r + 20.0))
        assert P.nb_params(20.0, 0.0) == (math.inf, 1.0)

    def test_poisson_limit_as_alpha_goes_to_zero(self) -> None:
        mu = 9.0
        mean, var = _pmf_mean_var(mu, 0.0)
        assert mean == pytest.approx(mu, rel=1e-9)
        assert var == pytest.approx(mu, rel=1e-6)  # Var = mu exactly, no overdispersion
        for k in (0, 1, 5, 12):
            expected = math.exp(-mu) * mu**k / math.factorial(k)
            assert P.nb_pmf(k, mu, 0.0) == pytest.approx(expected, rel=1e-12)
        # ...and approaching zero from above converges to it rather than dividing by it.
        for alpha in (1e-13, 1e-10, 1e-8):
            assert P.nb_pmf(3, mu, alpha) == pytest.approx(P.nb_pmf(3, mu, 0.0), rel=1e-5)
            assert _pmf_mean_var(mu, alpha)[1] == pytest.approx(mu, rel=1e-4)

    def test_cdf_is_a_distribution_function(self) -> None:
        mu, alpha = 12.0, 0.15
        assert P.nb_cdf(-1, mu, alpha) == 0.0
        assert P.nb_cdf(-0.5, mu, alpha) == 0.0
        previous = 0.0
        for k in range(0, 120):
            value = P.nb_cdf(k, mu, alpha)
            assert value >= previous - 1e-15
            assert value == pytest.approx(previous + P.nb_pmf(k, mu, alpha), abs=1e-12)
            previous = value
        assert P.nb_cdf(5000, mu, alpha) == pytest.approx(1.0)
        assert P.nb_cdf(7.9, mu, alpha) == pytest.approx(P.nb_cdf(7, mu, alpha))

    def test_interval_brackets_the_mean_and_carries_at_least_the_nominal_mass(self) -> None:
        for mu, alpha in self.CASES:
            for level in (0.5, 0.8, 0.95):
                lo, hi = P.nb_interval(mu, alpha, level)
                assert isinstance(lo, int) and isinstance(hi, int)
                assert lo <= round(mu) <= hi
                assert lo >= 0
                assert _interval_mass(lo, hi, mu, alpha) >= level - 1e-12

    def test_interval_widens_with_alpha(self) -> None:
        widths = []
        for alpha in (0.0, 0.02, 0.06, 0.15, 0.4):
            lo, hi = P.nb_interval(24.0, alpha, 0.8)
            widths.append(hi - lo)
        assert widths == sorted(widths)
        assert widths[-1] > widths[0]

    def test_interval_widens_with_the_level(self) -> None:
        previous = (10**9, -(10**9))
        for level in (0.5, 0.8, 0.95, 0.99):
            lo, hi = P.nb_interval(18.0, 0.08, level)
            assert lo <= previous[0] and hi >= previous[1]
            previous = (lo, hi)

    def test_interval_is_the_shortest_one_containing_the_mean(self) -> None:
        """Brute force: no shorter window containing the anchor holds as much mass."""
        for mu, alpha, level in ((10.0, 0.1, 0.8), (28.4, 0.061, 0.8), (6.0, 0.3, 0.5)):
            lo, hi = P.nb_interval(mu, alpha, level)
            anchor = round(mu)
            width = hi - lo
            for a in range(0, anchor + 1):
                for b in range(anchor, anchor + width + 5):
                    if b - a < width:
                        assert _interval_mass(a, b, mu, alpha) < level

    def test_simulated_coverage_is_close_to_nominal(self) -> None:
        rng = random.Random(20260116)
        for mu, alpha, level in ((12.0, 0.15, 0.8), (26.0, 0.06, 0.8), (4.0, 0.25, 0.5)):
            lo, hi = P.nb_interval(mu, alpha, level)
            exact = _interval_mass(lo, hi, mu, alpha)
            draws = [_nb_draw(rng, mu, alpha) for _ in range(4000)]
            covered = sum(1 for y in draws if lo <= y <= hi) / len(draws)
            assert covered == pytest.approx(exact, abs=0.03)
            assert covered >= level - 0.03
            # The sampler and the pmf agree on the first two moments as well.
            assert sum(draws) / len(draws) == pytest.approx(mu, rel=0.06)

    def test_degenerate_inputs(self) -> None:
        assert P.nb_pmf(0, 0.0, 0.1) == 1.0
        assert P.nb_pmf(1, 0.0, 0.1) == 0.0
        assert P.nb_interval(0.0, 0.1, 0.8) == (0, 0)
        assert P.nb_interval(-5.0, 0.1, 0.8) == (0, 0)
        assert P.nb_cdf(0, 0.0, 0.0) == 1.0
        assert P.nb_pmf(-1, 10.0, 0.1) == 0.0
        assert P.nb_interval(10.0, 0.1, 0.0) == (10, 10)
        assert P.nb_interval(10.0, 0.1, None) == (10, 10)
        # A level of 1 cannot be reached exactly by a countable support; it must terminate.
        lo, hi = P.nb_interval(10.0, 0.1, 1.0)
        assert lo == 0 and hi > 10
        # Negative alpha is treated as zero rather than producing a negative variance.
        assert P.nb_variance(10.0, -1.0) == 10.0


# =========================================================================== dispersion


class TestDispersion:
    def test_fit_recovers_a_known_alpha(self) -> None:
        rng = random.Random(7)
        mu, alpha = 15.0, 0.20
        draws = [_nb_draw(rng, mu, alpha) for _ in range(8000)]
        fitted = P.fit_dispersion(draws, [mu] * len(draws))
        assert fitted == pytest.approx(alpha, abs=0.03)

    def test_fit_works_with_varying_means(self) -> None:
        rng = random.Random(8)
        alpha = 0.12
        mus = [rng.uniform(4.0, 30.0) for _ in range(8000)]
        draws = [_nb_draw(rng, mu, alpha) for mu in mus]
        assert P.fit_dispersion(draws, mus) == pytest.approx(alpha, abs=0.03)

    def test_matches_the_closed_form_for_a_constant_mean(self) -> None:
        # With mu constant, alpha = (mean((y-mu)^2) - mu) / mu^2.
        observed = [10, 14, 6, 9, 21, 3, 12, 8]
        mu = 11.0
        expected = (sum((y - mu) ** 2 for y in observed) / len(observed) - mu) / mu**2
        assert P.fit_dispersion(observed, [mu] * len(observed)) == pytest.approx(expected)

    def test_no_overdispersion_reads_as_zero(self) -> None:
        assert P.fit_dispersion([], []) == 0.0
        assert P.fit_dispersion([10.0] * 50, [10.0] * 50) == 0.0  # underdispersed, clamped at 0
        assert P.fit_dispersion([5, 5], [0.0, -1.0]) == 0.0  # no usable mean
        assert P.fit_dispersion([5, None, 7], [6.0, 6.0, None]) >= 0.0

    def test_standardised_residuals_average_one_under_a_correct_law(self) -> None:
        rng = random.Random(21)
        mu, alpha = 18.0, 0.10
        draws = [_nb_draw(rng, mu, alpha) for _ in range(6000)]
        z2 = P.standardised_sq_residuals(draws, [mu] * len(draws), alpha)
        assert len(z2) == len(draws)
        assert sum(z2) / len(z2) == pytest.approx(1.0, abs=0.08)
        assert all(v >= 0 for v in z2)
        assert P.standardised_sq_residuals([5], [0.0], 0.1) == []

    def test_no_history_shrinks_fully_to_the_prior(self) -> None:
        assert P.shrunken_dispersion_multiplier([], 0) == 1.0
        assert P.shrunken_dispersion_multiplier([], None) == 1.0
        assert P.shrunken_dispersion_multiplier([3.0, 4.0], 0) == 1.0
        assert P.shrunken_dispersion_multiplier([], 50) == 1.0

    def test_crossover_sits_at_sixty_games(self) -> None:
        # k_c = 60: at n = 60 the player's own volatility and the prior carry equal weight.
        z_bar = 2.0
        assert P.shrunken_dispersion_multiplier([z_bar] * 60, 60) == pytest.approx(
            (z_bar + 1.0) / 2.0
        )
        assert P.shrunken_dispersion_multiplier([z_bar] * 60, 60) == pytest.approx(1.5)
        assert P.shrunken_dispersion_multiplier([z_bar] * 20, 20) == pytest.approx(
            (20 * 2.0 + 60) / 80
        )

    def test_large_history_approaches_the_observed_ratio(self) -> None:
        z_bar = 2.36  # the paper's ninetieth-percentile multiplier for points
        previous = 1.0
        for n in (1, 10, 30, 60, 120, 400, 5000):
            value = P.shrunken_dispersion_multiplier([z_bar] * n, n)
            assert value > previous
            assert 1.0 <= value < z_bar
            previous = value
        assert P.shrunken_dispersion_multiplier([z_bar] * 20_000, 20_000) == pytest.approx(
            z_bar, abs=0.01
        )

    def test_a_steady_player_is_left_alone(self) -> None:
        assert P.shrunken_dispersion_multiplier([1.0] * 200, 200) == pytest.approx(1.0)

    def test_effective_alpha_reproduces_the_inflated_variance(self) -> None:
        mu, alpha, c = 24.0, 0.06, 1.34
        a_eff = P.effective_alpha(mu, alpha, c)
        assert P.nb_variance(mu, a_eff) == pytest.approx(c * P.nb_variance(mu, alpha))
        assert P.effective_alpha(mu, alpha, 1.0) == pytest.approx(alpha)
        # A multiplier below one can narrow toward Poisson but never below it.
        assert P.effective_alpha(mu, alpha, 0.1) >= 0.0
        assert P.nb_variance(mu, P.effective_alpha(mu, alpha, 0.1)) >= mu - 1e-9
        assert P.effective_alpha(0.0, alpha, 1.5) >= 0.0


# =========================================================================== combination


class TestCombo:
    # Paper Table B.13: the held-out residual standard deviations behind the 14.6% figure.
    PAPER_SIGMAS = (5.974, 2.483, 1.733)

    def test_zero_correlation_equals_the_independent_calculation(self) -> None:
        identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        result = P.combo(
            [(25.0, 5.974), (8.0, 2.483), (6.0, 1.733)], identity
        )
        assert result.sd == pytest.approx(result.sd_if_independent)
        assert result.inflation == pytest.approx(0.0)
        assert result.sd == pytest.approx(math.sqrt(sum(s * s for s in self.PAPER_SIGMAS)))

    def test_the_papers_matrix_inflates_the_spread_by_about_fifteen_percent(self) -> None:
        """Paper section 11.3: 6.697 independent against 7.678 with covariance, a 14.6% inflation.

        The contract's rounded correlations (0.30/0.17/0.20 against the estimated
        0.296/0.175/0.195) move the answer in the fourth decimal, so this asserts the region the
        paper reports rather than an exact equality.
        """
        result = P.combo(
            {
                "pts": (25.0, self.PAPER_SIGMAS[0]),
                "reb": (8.0, self.PAPER_SIGMAS[1]),
                "ast": (6.0, self.PAPER_SIGMAS[2]),
            }
        )
        assert result.sd_if_independent == pytest.approx(6.697, abs=0.005)
        assert result.sd == pytest.approx(7.678, abs=0.01)
        assert result.inflation > 0.0
        assert result.inflation == pytest.approx(0.146, abs=0.01)
        # Equivalently, an independence assumption understates the spread by 12.8%.
        assert 1 - result.sd_if_independent / result.sd == pytest.approx(0.128, abs=0.01)

    def test_inflation_is_positive_for_any_positive_sigmas(self) -> None:
        rng = random.Random(99)
        for _ in range(100):
            sigmas = [rng.uniform(0.5, 9.0) for _ in range(3)]
            result = P.combo([(10.0, s) for s in sigmas])
            assert result.sd > result.sd_if_independent
            assert result.inflation > 0.0
            assert result.inflation < 0.30  # bounded by the largest off-diagonal

    def test_variance_is_the_quadratic_form(self) -> None:
        sigmas = self.PAPER_SIGMAS
        corr = K.COMBO_CORRELATION
        expected = sum(
            sigmas[i] * corr[i][j] * sigmas[j] for i in range(3) for j in range(3)
        )
        result = P.combo([(1.0, s) for s in sigmas])
        assert result.sd**2 == pytest.approx(expected)

    def test_mean_is_the_sum_and_the_interval_brackets_it(self) -> None:
        result = P.combo([(25.0, 5.974), (8.0, 2.483), (6.0, 1.733)])
        assert result.mean == pytest.approx(39.0)
        assert result.low is not None and result.high is not None
        assert result.low <= 39 <= result.high
        assert isinstance(result.low, int) and isinstance(result.high, int)
        assert result.label == "PTS+REB+AST"
        assert result.correlation == [list(row) for row in K.COMBO_CORRELATION]

    def test_perfect_correlation_sums_the_standard_deviations(self) -> None:
        ones = [[1.0] * 3 for _ in range(3)]
        result = P.combo([(10.0, 2.0), (5.0, 3.0), (4.0, 4.0)], ones)
        assert result.sd == pytest.approx(9.0)

    def test_accepts_stat_projections_directly(self) -> None:
        rate = _flat_rate("pts", 2000.0, 0.62)
        line = P.project_stat("pts", projected_minutes=34.0, rate=rate, dispersion_alpha=0.06)
        result = P.combo([line, line, line], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        assert result.mean == pytest.approx(3 * line.mean)
        assert result.sd == pytest.approx(math.sqrt(3) * line.sd)

    def test_degenerate_inputs(self) -> None:
        zero = P.combo([(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)])
        assert zero.mean == 0.0
        assert zero.sd == 0.0
        assert zero.inflation == 0.0
        assert zero.low is None and zero.high is None
        single = P.combo([(10.0, 3.0)])
        assert single.sd == pytest.approx(3.0)
        assert single.inflation == pytest.approx(0.0)
        assert P.combo([]).mean == 0.0
        no_interval = P.combo([(25.0, 5.0), (8.0, 2.0), (6.0, 1.5)], level=None)
        assert no_interval.low is None and no_interval.high is None

    def test_a_mis_sized_correlation_matrix_is_an_error(self) -> None:
        with pytest.raises(ValueError):
            P.combo([(1.0, 1.0), (1.0, 1.0)], K.COMBO_CORRELATION)


# =========================================================================== project_stat


class TestProjectStat:
    def _rate(self) -> P.RegressedRate:
        return _flat_rate("pts", 2000.0, 0.62)

    def test_master_formula_is_the_product(self) -> None:
        rate = self._rate()
        factors = P.context_factors(102.0, 101.0, 100.0, 116.0, 113.0, True, 2)
        line = P.project_stat("pts", projected_minutes=34.0, rate=rate, factors=factors)
        expected = 34.0 * rate.rate * P.total_context_multiplier(factors)
        assert line.mean == pytest.approx(expected)
        assert line.context_multiplier == pytest.approx(P.total_context_multiplier(factors))
        assert line.projected_minutes == 34.0

    def test_context_multiplier_overrides_the_factor_list(self) -> None:
        rate = self._rate()
        line = P.project_stat(
            "pts", projected_minutes=30.0, rate=rate, factors=(), context_multiplier=1.05
        )
        assert line.mean == pytest.approx(30.0 * rate.rate * 1.05)

    def test_dispersion_multiplier_widens_the_interval(self) -> None:
        rate = self._rate()
        base = P.project_stat(
            "pts", projected_minutes=34.0, rate=rate, dispersion_alpha=0.06,
            dispersion_multiplier=1.0,
        )
        volatile = P.project_stat(
            "pts", projected_minutes=34.0, rate=rate, dispersion_alpha=0.06,
            dispersion_multiplier=2.36,
        )
        assert volatile.mean == pytest.approx(base.mean)  # the mean is untouched
        assert volatile.sd > base.sd
        assert volatile.sd**2 == pytest.approx(2.36 * base.sd**2)
        assert (volatile.high - volatile.low) > (base.high - base.low)

    def test_interval_can_be_suppressed_but_the_mean_keeps_its_provenance(self) -> None:
        line = P.project_stat(
            "pts", projected_minutes=34.0, rate=self._rate(), interval_level=None
        )
        assert line.low is None and line.high is None
        payload = line.payload()
        assert payload["low"] is None and payload["intervalLevel"] is None
        assert payload["exposureMinutes"] == 2000.0
        assert payload["shrinkageWeight"] is not None

    def test_delta_against_the_season_average(self) -> None:
        line = P.project_stat(
            "pts", projected_minutes=34.0, rate=self._rate(), season_average=20.0
        )
        assert line.delta == pytest.approx(line.mean - 20.0)
        assert P.project_stat("pts", projected_minutes=34.0, rate=self._rate()).delta is None

    def test_a_projection_is_never_a_record(self) -> None:
        line = P.project_stat("pts", projected_minutes=34.0, rate=self._rate())
        assert line.payload()["availability"] == "estimated"
        assert P.AVAILABILITY == "estimated"

    def test_payload_reports_the_shrinkage_diagnostics_the_widget_shows(self) -> None:
        payload = P.project_stat(
            "pts", projected_minutes=34.0, rate=self._rate(), dispersion_alpha=0.06,
            dispersion_multiplier=1.34, season_average=19.0,
        ).payload()
        assert payload["metric"] == "pts"
        assert payload["shrinkageK"] == 81
        assert payload["halfLifeGames"] == 6
        assert payload["exposureMinutes"] == 2000.0
        assert 0.9 < payload["shrinkageWeight"] <= 1.0
        assert payload["dispersionAlpha"] == 0.06
        assert payload["dispersionMultiplier"] == 1.34
        assert payload["displayValue"] == catalog.format_metric("pts", payload["mean"])
        assert payload["descriptor"]["key"] == "pts"
        assert payload["descriptor"]["shortName"] == "PTS"

    def test_zero_minutes_degrades_to_zero_rather_than_crashing(self) -> None:
        line = P.project_stat("pts", projected_minutes=0.0, rate=self._rate())
        assert line.mean == 0.0
        assert (line.low, line.high) == (0, 0)
        assert line.sd == 0.0
        assert line.payload()["displayValue"] == "0.0"

    def test_no_history_projects_the_league_prior(self) -> None:
        empty = P.regressed_rate("stl", 0.0, 0.0)
        line = P.project_stat("stl", projected_minutes=20.0, rate=empty)
        assert line.mean == pytest.approx(20.0 * K.league_rate("stl"))
        assert line.rate.shrinkage_weight == 0.0
        assert line.dispersion_multiplier == 1.0  # nothing to estimate volatility from

    def test_unknown_stat_raises(self) -> None:
        with pytest.raises(K.UnknownProjectionStatError):
            P.project_stat("dunks", projected_minutes=30.0, rate=self._rate())


# =========================================================================== whole payload


def _full_inputs() -> tuple[list[P.StatInput], P.MinutesProjection, list[P.Factor]]:
    """A realistic set of inputs: a heavy-minute starter with several seasons behind him."""
    recent_minutes = [36, 31, 34, 38, 33, 35, 37]
    per_minute = {
        "pts": 0.78, "reb": 0.23, "ast": 0.19, "fg3m": 0.07,
        "tov": 0.09, "blk": 0.02, "stl": 0.03,
    }
    season_minutes, career_minutes = 1240.0, 22_000.0
    stats = []
    for key, rate_per_min in per_minute.items():
        rate = P.regressed_rate(
            key,
            season_minutes * rate_per_min,
            season_minutes,
            career_minutes * rate_per_min,
            career_minutes,
            rate_per_min * 34.0,
            34.0,
        )
        stats.append(
            P.StatInput(
                stat_key=key,
                rate=rate,
                dispersion_alpha=0.06,
                dispersion_multiplier=1.34,
                season_average=rate_per_min * 34.0,
            )
        )
    minutes = P.project_minutes(recent_minutes, 33.8)
    factors = P.context_factors(100.2, 99.0, 99.4, 111.8, 113.5, True, 2)
    return stats, minutes, factors


class TestProjectBoxScore:
    # contracts/CONTRACT.md section 4, next_game_projection.
    TOP_LEVEL_KEYS = {
        "player", "game", "projectedMinutes", "lines", "factors", "combo", "method", "notes",
    }
    LINE_KEYS = {
        "metric", "descriptor", "mean", "displayValue", "low", "high", "intervalLevel",
        "seasonAverage", "delta", "ratePerMinute", "shrinkageK", "exposureMinutes",
        "shrinkageWeight", "halfLifeGames", "dispersionAlpha", "dispersionMultiplier",
        "availability",
    }
    MINUTES_KEYS = {"value", "displayValue", "halfLifeGames", "seasonAverage", "low", "high"}
    COMBO_KEYS = {"label", "mean", "sd", "sdIfIndependent", "inflation", "low", "high",
                  "correlation"}
    METHOD_KEYS = {
        "summary", "minutesHalfLifeGames", "correlationApplied", "dispersionShrinkageGames",
    }

    def _payload(self, **kwargs) -> dict:
        stats, minutes, factors = _full_inputs()
        args = dict(
            minutes=minutes,
            factors=factors,
            minutes_season_average=33.8,
            player={"playerId": 2544, "name": "LeBron James"},
            game={
                "gameId": "0022500640",
                "date": "2026-01-04",
                "opponentAbbr": "BOS",
                "isHome": True,
                "restDays": 2,
                "isBackToBack": False,
                "opponentDefRtg": 111.8,
                "expectedPace": 99.1,
            },
        )
        args.update(kwargs)
        return P.project_box_score(kwargs.pop("stats", stats), **args)

    def test_payload_shape_matches_the_contract(self) -> None:
        payload = self._payload()
        assert set(payload) == self.TOP_LEVEL_KEYS
        assert set(payload["projectedMinutes"]) == self.MINUTES_KEYS
        assert set(payload["method"]) == self.METHOD_KEYS
        assert set(payload["combo"]) == self.COMBO_KEYS
        for line in payload["lines"]:
            assert set(line) == self.LINE_KEYS
        for factor in payload["factors"]:
            assert set(factor) == {"key", "label", "value", "explanation", "contributions"}
            assert set(factor["contributions"]) == {ln["metric"] for ln in payload["lines"]}

    def test_payload_is_json_serialisable(self) -> None:
        text = json.dumps(self._payload())
        assert json.loads(text)["method"]["summary"]

    def test_every_line_is_estimated(self) -> None:
        payload = self._payload()
        assert payload["lines"]
        for line in payload["lines"]:
            assert line["availability"] == "estimated"

    def test_minutes_are_reported_separately_and_prominently(self) -> None:
        payload = self._payload()
        minutes = payload["projectedMinutes"]
        assert minutes["halfLifeGames"] == 2
        assert minutes["seasonAverage"] == 33.8
        assert minutes["low"] < minutes["value"] < minutes["high"]
        assert minutes["displayValue"] == catalog.format_metric("min", minutes["value"])
        assert any("minutes carry most of the error" in n for n in payload["notes"])

    def test_context_factors_are_shown(self) -> None:
        payload = self._payload()
        assert [f["key"] for f in payload["factors"]] == ["pace", "opponent", "venue", "rest"]
        assert all(f["explanation"] for f in payload["factors"])

    def test_lines_follow_the_requested_order(self) -> None:
        stats, minutes, factors = _full_inputs()
        subset = [s for s in stats if s.stat_key in ("ast", "pts")]
        payload = P.project_box_score(subset[::-1], minutes=minutes, factors=factors)
        assert [line["metric"] for line in payload["lines"]] == ["ast", "pts"]

    def test_combo_appears_only_with_all_three_statistics(self) -> None:
        payload = self._payload()
        combo = payload["combo"]
        assert combo["label"] == "PTS+REB+AST"
        by_key = {line["metric"]: line for line in payload["lines"]}
        assert combo["mean"] == pytest.approx(
            sum(by_key[k]["mean"] for k in ("pts", "reb", "ast")), abs=1e-3
        )
        assert combo["sd"] > combo["sdIfIndependent"]
        assert combo["inflation"] > 0
        assert combo["low"] <= combo["mean"] <= combo["high"]
        assert payload["method"]["correlationApplied"] is True

        stats, minutes, factors = _full_inputs()
        partial = [s for s in stats if s.stat_key in ("pts", "reb")]
        no_combo = P.project_box_score(partial, minutes=minutes, factors=factors)
        assert no_combo["combo"] is None
        assert no_combo["method"]["correlationApplied"] is False

        stats, minutes, factors = _full_inputs()
        suppressed = P.project_box_score(
            stats, minutes=minutes, factors=factors, include_combo=False
        )
        assert suppressed["combo"] is None

    def test_no_scheduled_next_game_still_projects_and_says_so(self) -> None:
        stats, minutes, _ = _full_inputs()
        neutral = P.context_factors(None, None, None, None, None, True, None)
        payload = P.project_box_score(stats, minutes=minutes, factors=neutral, game=None)
        assert payload["game"] is None
        assert payload["lines"]
        assert any("No scheduled next game" in n for n in payload["notes"])
        # Against a neutral opponent only venue moves the projection.
        assert P.total_context_multiplier(neutral) == pytest.approx(
            K.DEFAULT_HOME_FACTORS[True]
        )

    def test_thin_history_is_reported_not_hidden(self) -> None:
        rookie = [
            P.StatInput(key, P.regressed_rate(key, 0.0, 0.0)) for key in ("pts", "reb", "ast")
        ]
        payload = P.project_box_score(
            rookie, minutes=P.project_minutes([14.0], None), factors=[]
        )
        assert any("Thin history" in n for n in payload["notes"])
        for line in payload["lines"]:
            assert line["shrinkageWeight"] == 0.0
            assert line["exposureMinutes"] == 0.0
            assert line["dispersionMultiplier"] == 1.0
            assert line["mean"] == pytest.approx(
                14.0 * K.league_rate(line["metric"]), abs=1e-3
            )

    def test_accepts_plain_dicts_as_well_as_stat_inputs(self) -> None:
        stats, minutes, factors = _full_inputs()
        as_dicts = [
            {
                "stat_key": s.stat_key,
                "rate": s.rate,
                "dispersion_alpha": s.dispersion_alpha,
                "dispersion_multiplier": s.dispersion_multiplier,
                "season_average": s.season_average,
            }
            for s in stats
        ]
        from_dicts = P.project_box_score(as_dicts, minutes=minutes, factors=factors)
        from_inputs = P.project_box_score(stats, minutes=minutes, factors=factors)
        assert from_dicts == from_inputs

    def test_accepts_a_bare_minutes_tuple(self) -> None:
        stats, _, factors = _full_inputs()
        payload = P.project_box_score(stats, minutes=(30.0, 24.0, 36.0), factors=factors)
        assert payload["projectedMinutes"]["value"] == 30.0
        assert payload["projectedMinutes"]["low"] == 24.0

    def test_interval_none_hides_bounds_and_says_why(self) -> None:
        stats, minutes, factors = _full_inputs()
        payload = P.project_box_score(
            stats, minutes=minutes, factors=factors, interval_level=None
        )
        for line in payload["lines"]:
            assert line["low"] is None and line["high"] is None
        assert payload["combo"]["low"] is None
        assert any("misleading" in n for n in payload["notes"])

    def test_empty_stat_list_is_a_valid_if_empty_projection(self) -> None:
        payload = P.project_box_score([], minutes=(0.0, 0.0, 0.0), factors=[])
        assert payload["lines"] == []
        assert payload["combo"] is None
        assert payload["notes"]

    def test_descriptors_can_be_omitted(self) -> None:
        stats, minutes, factors = _full_inputs()
        payload = P.project_box_score(
            stats, minutes=minutes, factors=factors, include_descriptors=False
        )
        assert all(line["descriptor"] is None for line in payload["lines"])

    def test_extra_notes_are_appended(self) -> None:
        payload = self._payload(notes=["Questionable, right ankle."])
        assert payload["notes"][-1] == "Questionable, right ankle."

    def test_the_fixture_uses_a_real_nba_identity(self) -> None:
        """The payload's PlayerRef is passed through verbatim, so the fixture uses a real one."""
        identities = json.loads(IDENTITIES_JSON.read_text())
        by_id = {
            p["playerId"]: (p.get("name") or p.get("fullName")) for p in identities["players"]
        }
        payload = self._payload()
        assert by_id[payload["player"]["playerId"]] == payload["player"]["name"]
        abbrs = {t["abbr"] for t in identities["teams"]}
        assert payload["game"]["opponentAbbr"] in abbrs


# =========================================================================== policy


class TestPolicy:
    """docs/PROJECTION.md section 6 and the no-new-dependency rule, enforced on the source."""

    BANNED_IDENTIFIERS = {
        "kelly", "kelly_fraction", "vig", "devig", "devig_two_way", "odds", "american_to_decimal",
        "decimal_odds", "implied_prob", "implied_prob_american", "ev", "ev_per_unit",
        "expected_value", "edge", "edge_erosion", "stake", "staking", "bankroll", "wager",
        "bet", "bet_rate", "payout", "juice", "moneyline", "prop_price", "sportsbook",
    }

    def _identifiers(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text())
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
        return names

    def test_no_market_translation_anywhere(self) -> None:
        for path in PROJECTION_SOURCES:
            names = {n.lower() for n in self._identifiers(path)}
            leaked = names & self.BANNED_IDENTIFIERS
            assert not leaked, f"{path.name} defines market-translation names: {sorted(leaked)}"

    def test_no_new_runtime_dependencies(self) -> None:
        allowed_roots = {
            "__future__", "math", "statistics", "dataclasses", "typing", "types", "nbastats",
        }
        for path in PROJECTION_SOURCES:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] in allowed_roots, alias.name
                elif isinstance(node, ast.ImportFrom):
                    root = (node.module or "").split(".")[0]
                    assert node.level > 0 or root in allowed_roots, node.module
        assert "numpy" not in sys.modules
        assert "scipy" not in sys.modules

    def test_the_engine_needs_no_database_or_web_framework(self) -> None:
        # Prose may name them (the module docstrings explain what they deliberately avoid);
        # an import statement may not.
        banned = ("sqlalchemy", "fastapi", "pandas", "numpy", "scipy", "lightgbm", "statsmodels")
        for path in PROJECTION_SOURCES:
            source = path.read_text()
            for module in banned:
                assert f"import {module}" not in source
                assert f"from {module}" not in source

    def test_exported_surface_is_what_the_callers_use(self) -> None:
        for name in P.__all__:
            assert hasattr(P, name), name
        for name in K.__all__:
            assert hasattr(K, name), name
