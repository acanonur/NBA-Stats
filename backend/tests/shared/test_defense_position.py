"""Tests for defence by opponent position.

This is the statistically delicate module, so the suite is built in layers, each of which
would catch a different kind of mistake:

1. **The allocation**, worked by hand: a game with a guard, a guard-forward, a centre, a player
   with no listed position and a DNP, checked bucket by bucket, including how a missing
   attempts figure stays missing instead of becoming zero.
2. **Reconciliation** at its exact tolerance, and what an unallocatable game does.
3. **The league reference** worked by hand on a three-game league with an unknown bucket.
4. **Empirical Bayes**, worked by hand: a four-team league whose guard means, standard errors,
   between-team variance, reliability, shrunk indices, posterior spreads and bands were derived
   independently with exact rational arithmetic, on both the per-game and per-minute bases.
5. **The gates at their exact thresholds**: minimum games, the provisional line, the coverage
   ceiling (exactly 5% passes, a hair over withholds), the league sample share (exactly 80%
   passes), and which reason wins when more than one applies.
6. **Properties on random leagues**: the buckets always sum to points allowed, the deltas always
   sum to the difference from the league, every game that reconciles does so exactly.
7. **Honesty about noise**: a simulated league of identical teams produces no better or worse
   cell at all, and a league with a real difference does.
8. **The wire shape**: exact key sets, no rank anywhere, no forbidden word.

Numbers marked *hand* below come from ``fractions.Fraction`` derivations done outside the code
under test, written out in the comments so a reader can re-derive them.
"""

from __future__ import annotations

import dataclasses
import json
import math
import random
from datetime import date, timedelta
from statistics import NormalDist

import pytest

from nbastats.shared import defense_position as D
from nbastats.shared import league_profile as LP
from nbastats.shared import market_guard

D0 = date(2026, 10, 1)


def day(n: int) -> date:
    return D0 + timedelta(days=n)


def profile(min_games=3, provisional_below=4, ceiling=0.05, base=LP.NBA) -> LP.LeagueProfile:
    """A small-sample profile so that four-game leagues can be worked by hand."""
    rules = LP.DefenseRules(
        min_games=min_games, provisional_below=provisional_below, unknown_ceiling=ceiling
    )
    return dataclasses.replace(base, defense=rules)


def three_lines(g, f=15, c=10, *, gm=30, fm=30, cm=30, unknown=None):
    """One guard, one forward and one centre (and optionally an unlisted player)."""
    lines = [
        D.OpponentLine(gm, g, {"G": 1.0}, fga=g / 2, fta=3, fg3a=2),
        D.OpponentLine(fm, f, {"F": 1.0}, fga=f / 2, fta=3, fg3a=2),
        D.OpponentLine(cm, c, {"C": 1.0}, fga=c / 2, fta=3, fg3a=2),
    ]
    if unknown:
        lines.append(D.OpponentLine(5, unknown, None, fga=1, fta=1, fg3a=0))
    return lines


def game(team, n, g, f=15, c=10, *, gm=30, fm=30, cm=30, unknown=None, opp_pts=None):
    lines = three_lines(g, f, c, gm=gm, fm=fm, cm=cm, unknown=unknown)
    total = g + f + c + (unknown or 0)
    return D.allocate_game(
        game_id=f"{team}-{n:02d}",
        date=day(n),
        team=team,
        opp_pts=total if opp_pts is None else opp_pts,
        lines=lines,
    )


# ------------------------------------------------------------------ 1. the allocation


def test_a_game_is_allocated_across_buckets_by_weight() -> None:
    lines = [
        D.OpponentLine(30, 10, {"G": 1.0}, fga=8, fta=2, fg3a=3),  # a guard
        D.OpponentLine(20, 20, {"G": 0.5, "F": 0.5}, fga=12, fta=4, fg3a=4),  # a guard-forward
        D.OpponentLine(25, 8, {"C": 1.0}, fga=5, fta=2, fg3a=0),  # a centre
        D.OpponentLine(5, 7, None, fga=None, fta=1, fg3a=1),  # no listed position
        D.OpponentLine(0, 0, {"G": 1.0}, fga=9, fta=9, fg3a=9),  # a DNP: no line at all
    ]
    a = D.allocate_game(game_id="g", date=day(1), team="T", opp_pts=45, lines=lines)
    # points: G 10 + 20/2 = 20, F 20/2 = 10, C 8, unknown 7  -> 45
    assert dict(a.points) == {"G": 20.0, "F": 10.0, "C": 8.0, "unknown": 7.0}
    # minutes: G 30 + 20/2 = 40, F 10, C 25, unknown 5
    assert dict(a.minutes) == {"G": 40.0, "F": 10.0, "C": 25.0, "unknown": 5.0}
    assert a.reconciled and a.reconciliation_error == 0.0
    # attempts are allocated the same way: G fga 8 + 12/2 = 14, F 6, C 5; the unknown player
    # did not record fga, so the unknown bucket's figure is missing, not zero
    assert dict(a.attempts["fga"]) == {"G": 14.0, "F": 6.0, "C": 5.0, "unknown": None}
    assert dict(a.attempts["fta"]) == {"G": 2 + 2.0, "F": 2.0, "C": 2.0, "unknown": 1.0}
    assert dict(a.attempts["fg3a"]) == {"G": 3 + 2.0, "F": 2.0, "C": 0.0, "unknown": 1.0}
    # points by how the scorer's position was established
    assert dict(a.basis_points) == {"listed": 38.0, "workbookListing": 0.0, "unknown": 7.0}


def test_a_missing_attempts_figure_poisons_only_the_buckets_it_touches() -> None:
    lines = [
        D.OpponentLine(30, 10, {"G": 1.0}, fga=None),
        D.OpponentLine(20, 20, {"G": 0.5, "F": 0.5}, fga=12),
        D.OpponentLine(20, 5, {"C": 1.0}, fga=4),
    ]
    a = D.allocate_game(game_id="g", date=day(1), team="T", opp_pts=35, lines=lines)
    assert a.attempts["fga"]["G"] is None  # the guard's line lacks it
    assert a.attempts["fga"]["F"] == 6.0  # the guard-forward's half is fully recorded
    assert a.attempts["fga"]["C"] == 4.0
    assert a.attempts["fga"]["unknown"] == 0.0  # nobody unlisted played: a recorded zero


def test_a_bucket_nobody_played_at_is_a_real_zero() -> None:
    a = D.allocate_game(
        game_id="g",
        date=day(1),
        team="T",
        opp_pts=20,
        lines=[D.OpponentLine(30, 20, {"G": 1.0})],
    )
    assert a.points["F"] == 0.0 and a.points["C"] == 0.0 and a.minutes["C"] == 0.0


def test_weights_naming_a_bucket_outside_the_scheme_are_treated_as_unknown() -> None:
    lines = [D.OpponentLine(30, 20, {"PG": 1.0}), D.OpponentLine(30, 10, {"C": 1.0})]
    gfc = D.allocate_game(game_id="g", date=day(1), team="T", opp_pts=30, lines=lines)
    assert gfc.points["unknown"] == 20.0 and gfc.points["C"] == 10.0 and gfc.reconciled
    five = D.allocate_game(
        game_id="g", date=day(1), team="T", opp_pts=30, lines=lines, scheme="workbook5"
    )
    assert five.points["PG"] == 20.0 and five.points["unknown"] == 0.0
    assert set(five.points) == {"PG", "SG", "SF", "PF", "C", "unknown"}


@pytest.mark.parametrize(
    "weights", [{"G": 0.6, "F": 0.5}, {"G": 0.4}, {"G": 1.5, "F": -0.5}, {}, {"G": math.nan}]
)
def test_malformed_weights_are_treated_as_unknown_not_repaired(weights) -> None:
    a = D.allocate_game(
        game_id="g",
        date=day(1),
        team="T",
        opp_pts=20,
        lines=[D.OpponentLine(30, 20, weights)],
    )
    assert a.points["unknown"] == 20.0 and a.points["G"] == 0.0 and a.reconciled


def test_the_declared_basis_is_ignored_for_an_unknown_player() -> None:
    lines = [
        D.OpponentLine(30, 12, {"G": 1.0}, basis="workbookListing"),
        D.OpponentLine(30, 8, None, basis="listed"),  # claims a basis but has no position
        D.OpponentLine(30, 5, {"F": 1.0}, basis="made-up"),
    ]
    a = D.allocate_game(game_id="g", date=day(1), team="T", opp_pts=25, lines=lines)
    assert dict(a.basis_points) == {"listed": 0.0, "workbookListing": 12.0, "unknown": 13.0}


# ------------------------------------------------------------------ 2. reconciliation


def test_reconciliation_has_a_tolerance_of_one_billionth_of_a_point() -> None:
    lines = [D.OpponentLine(30, 40, {"G": 1.0}), D.OpponentLine(30, 60, {"F": 1.0})]
    inside = D.allocate_game(game_id="g", date=day(1), team="T", opp_pts=100 + 5e-10, lines=lines)
    assert inside.reconciled and inside.reconciliation_error == pytest.approx(5e-10, abs=1e-13)
    outside = D.allocate_game(game_id="g", date=day(1), team="T", opp_pts=100 + 2e-9, lines=lines)
    assert not outside.reconciled
    assert D.RECONCILIATION_TOLERANCE == 1e-9


def test_a_game_whose_players_do_not_sum_to_the_score_is_unreconciled() -> None:
    lines = [D.OpponentLine(30, 40, {"G": 1.0}), D.OpponentLine(30, 55, {"F": 1.0})]
    a = D.allocate_game(game_id="g", date=day(1), team="T", opp_pts=100, lines=lines)
    assert not a.reconciled and a.reconciliation_error == 5.0


def test_a_player_with_minutes_but_no_points_makes_the_game_unallocatable() -> None:
    lines = [D.OpponentLine(30, 40, {"G": 1.0}), D.OpponentLine(30, None, {"F": 1.0})]
    a = D.allocate_game(game_id="g", date=day(1), team="T", opp_pts=40, lines=lines)
    assert not a.reconciled and math.isinf(a.reconciliation_error)


def test_a_missing_team_score_is_unreconcilable() -> None:
    lines = [D.OpponentLine(30, 40, {"G": 1.0})]
    for bad in (None, math.nan):
        a = D.allocate_game(game_id="g", date=day(1), team="T", opp_pts=bad, lines=lines)
        assert not a.reconciled


def test_lines_with_no_minutes_contribute_nothing_even_if_they_carry_points() -> None:
    lines = [D.OpponentLine(30, 40, {"G": 1.0}), D.OpponentLine(0, 6, {"F": 1.0})]
    a = D.allocate_game(game_id="g", date=day(1), team="T", opp_pts=46, lines=lines)
    assert a.points["F"] == 0.0 and not a.reconciled  # the six points have nowhere to go


def test_a_game_with_no_opposing_lines_reconciles_only_at_zero() -> None:
    assert D.allocate_game(game_id="g", date=day(1), team="T", opp_pts=0, lines=[]).reconciled
    assert not D.allocate_game(game_id="g", date=day(1), team="T", opp_pts=7, lines=[]).reconciled


# ------------------------------------------------------------- 3. the league reference


def test_league_reference_by_hand() -> None:
    games = [
        D.allocate_game(
            game_id="g1",
            date=day(1),
            team="X",
            opp_pts=35,
            lines=[
                D.OpponentLine(30, 20, {"G": 1.0}),
                D.OpponentLine(10, 10, None),
                D.OpponentLine(20, 5, {"C": 1.0}),
            ],
        ),
        D.allocate_game(
            game_id="g2",
            date=day(2),
            team="X",
            opp_pts=30,
            lines=[
                D.OpponentLine(20, 10, {"G": 1.0}),
                D.OpponentLine(20, 15, {"F": 1.0}),
                D.OpponentLine(5, 5, None),
            ],
        ),
        D.allocate_game(
            game_id="g3",
            date=day(3),
            team="Y",
            opp_pts=40,
            lines=[
                D.OpponentLine(15, 12, {"G": 1.0}),
                D.OpponentLine(25, 8, {"F": 1.0}),
                D.OpponentLine(40, 20, {"C": 1.0}),
            ],
        ),
        D.allocate_game(
            game_id="bad",
            date=day(4),
            team="Y",
            opp_pts=99,
            lines=[
                D.OpponentLine(15, 12, {"G": 1.0}),
            ],
        ),
    ]
    ref = D.build_league_reference(games, scheme="gfc", regulation_minutes=48)
    assert ref.team_games == 3 and ref.teams == 2  # the unreconciled game is not in the reference
    # mu: G (20+10+12)/3 = 14, F (0+15+8)/3 = 23/3, C (5+0+20)/3 = 25/3, unknown (10+5)/3 = 5
    assert ref.mean_points["G"] == pytest.approx(14.0)
    assert ref.mean_points["F"] == pytest.approx(23 / 3)
    assert ref.mean_points["C"] == pytest.approx(25 / 3)
    assert ref.mean_points["unknown"] == pytest.approx(5.0)
    # the buckets sum to the league's points allowed per game: (35+30+40)/3 = 35
    assert ref.points_allowed_per_game == pytest.approx(35.0)
    # rho = (sum A / sum M) * 48: G 42/65, F 23/45, C 25/60, unknown 15/15
    assert ref.rate["G"] == pytest.approx(42 / 65 * 48)
    assert ref.rate["F"] == pytest.approx(23 / 45 * 48)
    assert ref.rate["C"] == pytest.approx(25 / 60 * 48)
    assert ref.rate["unknown"] == pytest.approx(48.0)
    # sigma = mu / sum mu: 14/35, (23/3)/35, (25/3)/35, 5/35
    assert ref.share["G"] == pytest.approx(0.4)
    assert ref.share["unknown"] == pytest.approx(1 / 7)
    assert sum(ref.share.values()) == pytest.approx(1.0)
    # u_L: unknown points over all points allowed, 15 / 105
    assert ref.unknown_share == pytest.approx(1 / 7)


def test_an_empty_league_reference_is_nothing_not_zero() -> None:
    ref = D.build_league_reference([], scheme="gfc", regulation_minutes=48)
    assert ref.team_games == 0 and ref.points_allowed_per_game is None
    assert ref.unknown_share is None
    assert all(v is None for v in ref.mean_points.values())


def test_a_bucket_with_no_minutes_has_no_rate() -> None:
    a = D.allocate_game(
        game_id="g", date=day(1), team="T", opp_pts=20, lines=[D.OpponentLine(30, 20, {"G": 1.0})]
    )
    ref = D.build_league_reference([a], scheme="gfc", regulation_minutes=48)
    assert ref.rate["G"] == pytest.approx(20 / 30 * 48)
    assert ref.rate["F"] is None and ref.rate["C"] is None


# --------------------------------------------------- 4. empirical Bayes, worked by hand

# Four teams, four games each; only the guards' points vary (forward 15 and centre 10 always,
# 30 minutes each). Guards' points allowed per game:
GUARD_SERIES = {
    "T1": [20, 24, 22, 26],
    "T2": [30, 30, 28, 32],
    "T3": [24, 26, 25, 25],
    "T4": [18, 22, 20, 20],
}
# Derived with exact rationals (hand):
#   mu_G = (92 + 120 + 100 + 80) / 16 = 24.5
#   I_T  = mean / mu_G        SE_T = s / sqrt(4) / mu_G   (s: sample sd of the four values)
#   mean SE^2 = 0.0013188949049007359    var(I) (sample) = 0.02943218103567958
#   tau^2 = var(I) - mean SE^2 = 0.028113286130778844     R = tau^2/(tau^2 + mean SE^2)
#   B_T = tau^2/(tau^2 + SE_T^2)    shrunk = 1 + B_T (I_T - 1)
#   psd = sqrt(tau^2 SE_T^2 / (tau^2 + SE_T^2))
#   z = Phi^-1(1 - 0.05/12) = 2.638257273476751      (k = 3 positions x 4 teams)
HAND_PER_GAME = {
    "mu": 24.5,
    "tau2": 0.028113286130778844,
    "reliability": 0.9551886792452831,
    "z": 2.638257273476751,
    "raw": {
        "T1": 0.9387755102040817,
        "T2": 1.2244897959183674,
        "T3": 1.0204081632653061,
        "T4": 0.8163265306122449,
    },
    "se": {
        "T1": 0.052693650968808396,
        "T2": 0.033326391058274535,
        "T3": 0.016663195529137267,
        "T4": 0.033326391058274535,
    },
    "index": {
        "T1": 0.9442788351295575,
        "T2": 1.2159581172136313,
        "T3": 1.0202085724265255,
        "T4": 0.823306995007029,
    },
    # shrunk +- z*psd:  T1 (0.8117, 1.0769)  T2 (1.1297, 1.3022)
    #                   T3 (0.9765, 1.0640)  T4 (0.7371, 0.9095)
    "band": {"T1": "typical", "T2": "worse", "T3": "typical", "T4": "better"},
}


def guard_league(series=GUARD_SERIES, **minutes):
    return {
        team: [game(team, n, g, gm=minutes.get(team, [30] * 4)[n]) for n, g in enumerate(values)]
        for team, values in series.items()
    }


def test_empirical_bayes_per_game_by_hand() -> None:
    table = D.compute_defense(guard_league(), profile=profile())
    hand = HAND_PER_GAME
    assert table.league.mean_points["G"] == pytest.approx(hand["mu"], abs=1e-12)
    assert table.method.z == pytest.approx(hand["z"], abs=1e-9)
    assert table.method.family_size == 12
    assert table.method.league_reliability["G"] == pytest.approx(hand["reliability"], abs=1e-9)
    for team in GUARD_SERIES:
        cell = table.team(team).bucket("G")
        assert cell.raw_index == pytest.approx(hand["raw"][team], abs=1e-9)
        assert cell.standard_error == pytest.approx(hand["se"][team], abs=1e-9)
        assert cell.index == pytest.approx(hand["index"][team], abs=1e-9)
        assert cell.band == hand["band"][team]
    assert table.method.league_signal == "detected"
    assert table.league_signal_message is None


def test_a_position_with_no_between_team_variation_is_exactly_one_with_no_band() -> None:
    table = D.compute_defense(guard_league(), profile=profile())
    for team in GUARD_SERIES:
        for position in ("F", "C"):
            cell = table.team(team).bucket(position)
            assert cell.raw_index == 1.0 and cell.index == 1.0  # tau^2 = 0 gives B = 0
            assert cell.standard_error == 0.0
            assert cell.band is None
    assert table.method.league_reliability == pytest.approx(
        {"G": HAND_PER_GAME["reliability"], "F": 0.0, "C": 0.0}, abs=1e-9
    )


def test_the_unknown_bucket_never_gets_an_index() -> None:
    table = D.compute_defense(guard_league(), profile=profile())
    cell = table.team("T1").bucket("unknown")
    assert cell.raw_index is None and cell.index is None and cell.band is None
    assert cell.points_allowed_per_game == 0.0  # a recorded zero


def test_the_shrunk_index_is_between_one_and_the_raw_index() -> None:
    table = D.compute_defense(guard_league(), profile=profile())
    for team in GUARD_SERIES:
        cell = table.team(team).bucket("G")
        low, high = sorted((1.0, cell.raw_index))
        assert low <= cell.index <= high


# Same guards' points, with their minutes varying by team and game (forward and centre
# constant), on the per-minute basis. Derived with exact rationals (hand):
#   rho_G = (392 / 1 ... ) = 28.0       r_T = (sum A / sum M) * 40
#   SE = sqrt(sum (A - rhat*M)^2 / (n (n-1))) / mean(M) * 40 / rho_G        (n = 4)
GUARD_MINUTES = {
    "T1": [40, 40, 44, 44],
    "T2": [36, 36, 36, 36],
    "T3": [30, 34, 30, 34],
    "T4": [28, 32, 28, 32],
}
HAND_PER_MINUTE = {
    "rho": 28.0,
    "tau2": 0.031906653187087256,
    "reliability": 0.9670267471925346,
    "rate": {
        "T1": 21.904761904761905,
        "T2": 33.333333333333336,
        "T3": 31.25,
        "T4": 26.666666666666668,
    },
    "raw": {
        "T1": 0.782312925170068,
        "T2": 1.1904761904761905,
        "T3": 1.1160714285714286,
        "T4": 0.9523809523809523,
    },
    "se": {
        "T1": 0.03932003337875634,
        "T2": 0.032400657973322464,
        "T3": 0.030266257617695275,
        "T4": 0.028980029497627836,
    },
    "index": {
        "T1": 0.7923736434242905,
        "T2": 1.1844087174404312,
        "T3": 1.1128319994567577,
        "T4": 0.9536022275449373,
    },
    # shrunk +- z*psd: T1 (0.691, 0.894) T2 (1.100, 1.268) T3 (1.034, 1.192) T4 (0.878, 1.029)
    "band": {"T1": "better", "T2": "worse", "T3": "worse", "T4": "typical"},
}


def test_empirical_bayes_per_minute_by_hand() -> None:
    gm = {team: values for team, values in GUARD_MINUTES.items()}
    league = {
        team: [game(team, n, g, gm=gm[team][n]) for n, g in enumerate(GUARD_SERIES[team])]
        for team in GUARD_SERIES
    }
    # the hand derivation used 40-minute games, so this profile is the EuroLeague's
    table = D.compute_defense(league, profile=profile(base=LP.EUROLEAGUE), basis="perMinute")
    hand = HAND_PER_MINUTE
    assert table.league.rate["G"] == pytest.approx(hand["rho"], abs=1e-12)
    assert table.method.league_reliability["G"] == pytest.approx(hand["reliability"], abs=1e-9)
    for team in GUARD_SERIES:
        cell = table.team(team).bucket("G")
        # the rate itself is basis-independent and always reported
        assert cell.points_per_regulation_minutes == pytest.approx(hand["rate"][team], abs=1e-9)
        assert cell.raw_index == pytest.approx(hand["raw"][team], abs=1e-9)
        assert cell.standard_error == pytest.approx(hand["se"][team], abs=1e-9)
        assert cell.index == pytest.approx(hand["index"][team], abs=1e-9)
        assert cell.band == hand["band"][team]


def test_the_per_minute_basis_controls_for_minutes_where_per_game_cannot() -> None:
    """Identical points per minute, very different minutes: no per-minute difference at all."""
    series = {f"T{i}": [20.0 * m / 30 for m in (30, 30, 30, 30)] for i in range(4)}
    minutes = {"T0": [30] * 4, "T1": [45] * 4, "T2": [20] * 4, "T3": [36] * 4}
    league = {
        team: [game(team, n, 40.0 * minutes[team][n] / 30, gm=minutes[team][n]) for n in range(4)]
        for team in series
    }
    per_game = D.compute_defense(league, profile=profile(), basis="perGame")
    per_minute = D.compute_defense(league, profile=profile(), basis="perMinute")
    # per game, the long-minutes team looks like it allows far more guard points
    assert per_game.team("T1").bucket("G").raw_index > 1.2
    # per minute they are identical teams
    for team in series:
        assert per_minute.team(team).bucket("G").raw_index == pytest.approx(1.0, abs=1e-12)
        assert per_minute.team(team).bucket("G").band is None


def test_tau_squared_is_clamped_at_zero_and_means_no_signal() -> None:
    """Identical team means: all the spread is within-team noise."""
    series = {
        "T1": [20, 30, 20, 30],
        "T2": [30, 20, 30, 20],
        "T3": [25, 25, 20, 30],
        "T4": [22, 28, 26, 24],
    }
    table = D.compute_defense(guard_league(series), profile=profile())
    assert table.method.league_reliability["G"] == 0.0
    for team in series:
        cell = table.team(team).bucket("G")
        assert cell.index == 1.0 and cell.band is None
        assert cell.raw_index == pytest.approx(1.0)  # means are all 25
    assert table.method.league_signal == "none detected"
    assert table.league_signal_message == D.NO_SIGNAL_MESSAGE
    assert "differ from the league by more than chance" in D.NO_SIGNAL_MESSAGE


def test_a_reliability_below_the_gate_shrinks_but_shows_no_band() -> None:
    series = {
        "T0": [32, 20, 35, 30],
        "T1": [25, 20, 17, 30],
        "T2": [23, 31, 32, 31],
        "T3": [26, 17, 26, 33],
    }
    table = D.compute_defense(guard_league(series), profile=profile())
    reliability = table.method.league_reliability["G"]
    assert 0.0 < reliability < 0.2  # about 0.097
    for team in series:
        cell = table.team(team).bucket("G")
        assert cell.index is not None and cell.index != cell.raw_index
        assert cell.band is None
    assert table.method.league_signal == "none detected"


def test_a_position_whose_reliability_equals_the_gate_exactly_shows_bands() -> None:
    first = D.compute_defense(guard_league(), profile=profile())
    reliability = first.method.league_reliability["G"]
    assert any(t.bucket("G").band in ("better", "worse") for t in first.teams)
    rules = LP.DefenseRules(
        min_games=3, provisional_below=4, unknown_ceiling=0.05, reliability_gate=reliability
    )
    at_gate = D.compute_defense(guard_league(), profile=dataclasses.replace(LP.NBA, defense=rules))
    assert at_gate.method.league_reliability["G"] == reliability
    assert [t.bucket("G").band for t in at_gate.teams] == [t.bucket("G").band for t in first.teams]
    above = dataclasses.replace(rules, reliability_gate=math.nextafter(reliability, 1.0))
    over = D.compute_defense(guard_league(), profile=dataclasses.replace(LP.NBA, defense=above))
    assert all(t.bucket("G").band is None for t in over.teams)  # just under the gate: no bands
    assert all(t.bucket("G").index is not None for t in over.teams)  # but still shrunk


def test_zero_variance_everywhere_is_not_a_division_by_zero() -> None:
    series = {team: [25, 25, 25, 25] for team in ("T1", "T2", "T3", "T4")}
    table = D.compute_defense(guard_league(series), profile=profile())
    cell = table.team("T1").bucket("G")
    assert cell.standard_error == 0.0 and cell.index == 1.0 and cell.band is None


def test_a_team_with_zero_spread_in_a_league_with_signal_is_taken_at_face_value() -> None:
    series = {**GUARD_SERIES, "T3": [25, 25, 25, 25]}
    table = D.compute_defense(guard_league(series), profile=profile())
    cell = table.team("T3").bucket("G")
    assert cell.standard_error == 0.0
    assert cell.index == pytest.approx(cell.raw_index)  # B = 1: no sampling noise to shrink


def test_bonferroni_critical_values() -> None:
    assert D.bonferroni_z(1, 0.05) == pytest.approx(1.6448536269514722, abs=1e-9)
    assert D.bonferroni_z(5, 0.05) == pytest.approx(2.3263478740408408, abs=1e-9)  # 1 - 0.01
    assert D.bonferroni_z(12, 0.05) == pytest.approx(2.638257273476751, abs=1e-9)
    assert NormalDist().cdf(D.bonferroni_z(60, 0.05)) == pytest.approx(1 - 0.05 / 60)
    for bad in ((0, 0.05), (3, 0.0), (3, 1.0), (3, -0.1)):
        with pytest.raises(ValueError):
            D.bonferroni_z(*bad)


def test_the_family_grows_with_the_number_of_cells() -> None:
    four = D.compute_defense(guard_league(), profile=profile())
    assert four.method.family_size == 3 * 4
    league = {
        f"T{i}": [game(f"T{i}", n, 25 + (i % 5) + n % 3) for n in range(4)] for i in range(10)
    }
    ten = D.compute_defense(league, profile=profile())
    assert ten.method.family_size == 3 * 10
    assert ten.method.z > four.method.z


def test_workbook5_uses_five_positions_and_says_what_they_are() -> None:
    def five_game(team, n, base):
        lines = [
            D.OpponentLine(30, base + (n % 3), {"PG": 1.0}, basis="workbookListing"),
            D.OpponentLine(30, 14, {"SG": 1.0}, basis="workbookListing"),
            D.OpponentLine(30, 12, {"SF": 1.0}, basis="workbookListing"),
            D.OpponentLine(30, 10, {"PF": 1.0}, basis="workbookListing"),
            D.OpponentLine(30, 8, {"C": 1.0}, basis="workbookListing"),
        ]
        return D.allocate_game(
            game_id=f"{team}{n}",
            date=day(n),
            team=team,
            opp_pts=base + (n % 3) + 44,
            lines=lines,
            scheme="workbook5",
        )

    league = {f"T{i}": [five_game(f"T{i}", n, 15 + i) for n in range(4)] for i in range(5)}
    table = D.compute_defense(league, profile=profile(), scheme="workbook5")
    positions = [b.position for b in table.teams[0].buckets]
    assert positions == ["PG", "SG", "SF", "PF", "C", "unknown"]
    assert table.method.family_size == 5 * 5
    assert "workbook author" in table.method.taxonomy
    assert table.teams[0].coverage.workbook_listing == pytest.approx(1.0)
    assert table.teams[0].coverage.listed == 0.0
    assert set(table.method.league_reliability) == {"PG", "SG", "SF", "PF", "C"}


# --------------------------------------------------------- 5. the gates, at their edges


def league_of(games_by_team, *, unknown=None, seed=1, **kwargs):
    """Teams with the given game counts and noisy guard points (and optional unknown points)."""
    rng = random.Random(seed)
    out = {}
    for team, n_games in games_by_team.items():
        u = unknown(team) if callable(unknown) else unknown
        out[team] = [game(team, n, round(rng.gauss(30, 4), 1), unknown=u) for n in range(n_games)]
    return out


def el(min_games=6, provisional_below=12, ceiling=0.05):
    return profile(min_games, provisional_below, ceiling)


def test_minimum_games_at_the_exact_threshold() -> None:
    teams = {f"T{i}": 6 for i in range(8)} | {"SHORT": 5}
    table = D.compute_defense(league_of(teams), profile=el())
    short = table.team("SHORT")
    assert short.withheld.reason == "minimumGames"
    assert short.withheld.message == "Only 5 games, too few to judge a defence"
    assert short.provisional
    assert all(b.index is None and b.band is None and b.raw_index is None for b in short.buckets)
    assert short.buckets[0].points_allowed_per_game is not None  # recorded facts still show
    at_six = table.team("T0")
    assert at_six.withheld is None
    assert at_six.buckets[0].index is not None


def test_the_message_reads_correctly_for_one_game_and_none() -> None:
    teams = {f"T{i}": 6 for i in range(8)} | {"ONE": 1, "NONE": 0}
    table = D.compute_defense(league_of(teams), profile=el())
    assert table.team("ONE").withheld.message == "Only 1 game, too few to judge a defence"
    none = table.team("NONE")
    assert none.withheld.message == "Only 0 games, too few to judge a defence"
    assert none.points_allowed_per_game is None
    assert all(b.points_allowed_per_game is None for b in none.buckets)
    assert none.sum_of_buckets is None


@pytest.mark.parametrize("games, provisional", [(6, True), (11, True), (12, False), (30, False)])
def test_the_provisional_line_at_its_exact_threshold(games, provisional) -> None:
    table = D.compute_defense(league_of({f"T{i}": games for i in range(8)}), profile=el())
    team = table.teams[0]
    assert team.provisional is provisional
    assert team.withheld is None
    assert team.buckets[0].index is not None  # a provisional table still shows indices
    if provisional:
        assert all(b.band is None for t in table.teams for b in t.buckets)


def test_a_non_provisional_league_with_signal_shows_bands() -> None:
    league = {}
    for i in range(10):
        rng = random.Random(i)
        league[f"T{i}"] = [game(f"T{i}", n, rng.gauss(25 + 2 * i, 2)) for n in range(14)]
    table = D.compute_defense(league, profile=el())
    assert not table.teams[0].provisional
    bands = {t.team: t.bucket("G").band for t in table.teams}
    assert bands["T0"] == "better" and bands["T9"] == "worse"
    assert table.method.league_signal == "detected"


def test_the_coverage_ceiling_is_exact() -> None:
    def build(unknown_points):
        # 100 points allowed every game: unknown share is unknown_points / 100
        return {
            f"T{i}": [
                game(
                    f"T{i}",
                    n,
                    40 + (i % 3),
                    f=30,
                    c=100 - 70 - (i % 3) - unknown_points,
                    unknown=unknown_points,
                )
                for n in range(6)
            ]
            for i in range(8)
        }

    at_ceiling = D.compute_defense(build(5), profile=el())
    assert at_ceiling.league.unknown_share == 0.05
    assert at_ceiling.teams[0].withheld is None  # exactly 5% is allowed
    over = D.compute_defense(build(5.1), profile=el())
    assert over.league.unknown_share > 0.05
    for team in over.teams:
        assert team.withheld.reason == "positionCoverage"
        assert all(b.index is None and b.band is None for b in team.buckets)
        # the raw buckets, including unknown, are recorded facts and still show
        assert team.bucket("unknown").points_allowed_per_game == pytest.approx(5.1)
        assert team.coverage.unknown == pytest.approx(0.051)
    assert over.method.league_signal is None  # nothing could be tested


def test_a_team_over_the_ceiling_is_withheld_alone_when_the_league_is_fine() -> None:
    def unknown(team):
        return 9 if team == "T0" else 0  # 9% for one team of eight: the league stays at ~1.1%

    table = D.compute_defense(
        league_of({f"T{i}": 8 for i in range(8)}, unknown=unknown), profile=el()
    )
    assert table.league.unknown_share < 0.05
    withheld = table.team("T0").withheld
    assert withheld.reason == "positionCoverage" and "this team's" in withheld.message
    assert table.team("T1").withheld is None
    assert table.team("T1").bucket("G").index is not None


def test_a_league_over_the_ceiling_withholds_a_clean_team_too() -> None:
    def unknown(team):
        return 0 if team == "T0" else 8  # seven teams at ~8%: the league is over 5%

    table = D.compute_defense(
        league_of({f"T{i}": 8 for i in range(8)}, unknown=unknown), profile=el()
    )
    assert table.league.unknown_share > 0.05
    clean = table.team("T0")
    assert clean.withheld.reason == "positionCoverage" and "the league's" in clean.withheld.message


def test_the_league_sample_share_is_exact() -> None:
    eight_of_ten = {f"T{i}": 6 for i in range(8)} | {"S1": 5, "S2": 5}
    table = D.compute_defense(league_of(eight_of_ten), profile=el())
    assert table.team("T0").withheld is None  # exactly 80% meet the minimum
    seven_of_ten = {f"T{i}": 6 for i in range(7)} | {"S1": 5, "S2": 5, "S3": 5}
    table = D.compute_defense(league_of(seven_of_ten), profile=el())
    assert table.team("T0").withheld.reason == "leagueSample"
    assert table.team("S1").withheld.reason == "minimumGames"  # a team's own shortfall first
    assert table.method.league_signal is None


def test_a_league_larger_than_the_teams_present_counts_its_absent_teams() -> None:
    eight = {f"T{i}": 6 for i in range(8)}
    assert (
        D.compute_defense(league_of(eight), profile=el(), league_team_count=10).team("T0").withheld
        is None
    )
    assert (
        D.compute_defense(league_of(eight), profile=el(), league_team_count=11)
        .team("T0")
        .withheld.reason
        == "leagueSample"
    )


def test_fewer_than_two_comparable_teams_is_a_league_sample_problem() -> None:
    table = D.compute_defense(league_of({"ONLY": 8}), profile=el())
    assert table.team("ONLY").withheld.reason == "leagueSample"
    two = D.compute_defense(league_of({"A": 8, "B": 8}), profile=el())
    assert two.team("A").withheld is None  # two teams can at least be compared


def test_the_reasons_win_in_the_order_minimum_coverage_sample() -> None:
    # a short team that is also over the coverage ceiling: its own shortfall is reported first
    teams = {f"T{i}": 8 for i in range(7)} | {"SHORT": 3}
    table = D.compute_defense(
        league_of(teams, unknown=lambda team: 20 if team == "SHORT" else 0), profile=el()
    )
    assert table.team("SHORT").withheld.reason == "minimumGames"
    # coverage outranks the league sample when both apply
    bad = {f"T{i}": 8 for i in range(3)} | {f"S{i}": 2 for i in range(7)}
    table = D.compute_defense(
        league_of(bad, unknown=lambda team: 9 if team == "T0" else 0), profile=el()
    )
    assert table.league.unknown_share < 0.05  # the league is fine; only T0 is over, and the
    # league sample (3 of 10 teams meet the minimum) is too small as well
    assert table.team("T0").withheld.reason == "positionCoverage"
    assert table.team("T1").withheld.reason == "leagueSample"


def test_the_coverage_ceiling_can_be_overridden() -> None:
    def build(u):
        return league_of({f"T{i}": 8 for i in range(8)}, unknown=u)

    assert D.compute_defense(build(8), profile=el()).teams[0].withheld.reason == "positionCoverage"
    relaxed = D.compute_defense(build(8), profile=el(), coverage_ceiling=0.15)
    assert relaxed.teams[0].withheld is None  # about 12.6% of points went to unlisted players
    assert relaxed.method.coverage_ceiling == 0.15


# ---------------------------------------------------------------- the window and sorting


def test_a_window_uses_each_teams_last_n_reconciled_games_and_the_league_stays_whole() -> None:
    table = D.compute_defense(guard_league(), profile=profile(), window=3)
    t1 = table.team("T1")
    # T1's last three games are n=1..3: guards 24, 22, 26 -> mean 24
    assert t1.games == 3 and t1.season_games == 4
    assert t1.window_kind == "lastGames" and t1.window_requested == 3
    assert t1.bucket("G").points_allowed_per_game == pytest.approx(24.0)
    assert t1.points_allowed_per_game == pytest.approx(24.0 + 25.0)
    # the league reference is the whole season: guards 392 / 16
    assert table.league.mean_points["G"] == pytest.approx(24.5)
    assert t1.bucket("G").league_average == pytest.approx(24.5)


def test_the_window_picks_the_newest_games_by_date() -> None:
    games = [game("T1", n, g) for n, g in [(5, 99), (1, 10), (3, 20), (2, 30), (4, 40)]]
    table = D.compute_defense(
        {"T1": games, "T2": [game("T2", n, 25) for n in range(1, 6)]}, profile=profile(), window=2
    )
    # newest by date: n=5 (99) and n=4 (40)
    assert table.team("T1").bucket("G").points_allowed_per_game == pytest.approx((99 + 40) / 2)


def test_minimum_games_applies_to_the_window() -> None:
    table = D.compute_defense(guard_league(), profile=profile(min_games=3), window=2)
    assert table.team("T1").withheld.reason == "minimumGames"
    assert table.team("T1").games == 2


def test_season_window_reports_no_requested_count() -> None:
    team = D.compute_defense(guard_league(), profile=profile()).team("T1")
    assert team.window_kind == "season" and team.window_requested is None
    payload = team.to_payload()
    assert payload["window"] == {"kind": "season", "games": 4, "requested": None}


def test_unreconciled_games_are_excluded_and_counted() -> None:
    league = guard_league()
    league["T1"].append(game("T1", 9, 99, opp_pts=500))  # does not sum to its score
    league["T1"].append(game("T1", 10, 5, opp_pts=1))
    table = D.compute_defense(league, profile=profile())
    t1 = table.team("T1")
    assert t1.unreconciled_games == 2 and t1.games == 4 and t1.season_games == 4
    assert t1.bucket("G").points_allowed_per_game == pytest.approx(23.0)  # the bad games are out
    assert table.league.team_games == 16
    assert table.team("T2").unreconciled_games == 0
    assert t1.to_payload()["reconciliation"]["unreconciledGames"] == 2


def test_the_table_is_sorted_by_points_allowed_with_no_rank() -> None:
    table = D.compute_defense(guard_league(), profile=profile())
    papg = [t.points_allowed_per_game for t in table.teams]
    assert papg == sorted(papg)
    # guards + 25: T4 45, T1 48, T3 50, T2 55
    assert [t.team for t in table.teams] == ["T4", "T1", "T3", "T2"]
    blob = json.dumps([t.to_payload() for t in table.teams] + [table.method.to_payload()])
    assert "rank" not in blob.lower()


def test_teams_with_nothing_sort_last() -> None:
    league = guard_league() | {"EMPTY": []}
    table = D.compute_defense(league, profile=profile())
    assert table.teams[-1].team == "EMPTY"


def test_unknown_team_lookup_raises() -> None:
    table = D.compute_defense(guard_league(), profile=profile())
    with pytest.raises(KeyError):
        table.team("nobody")
    with pytest.raises(KeyError):
        table.team("T1").bucket("X")


# --------------------------------------------------------------- raw numbers by hand


def test_raw_bucket_numbers_for_one_team_by_hand() -> None:
    t1 = D.compute_defense(guard_league(), profile=profile()).team("T1")
    g = t1.bucket("G")
    # guards 20, 24, 22, 26: mean 23; minutes 30 each; rate (92 / 120) * 48 = 36.8
    assert g.points_allowed_per_game == pytest.approx(23.0)
    assert g.opponent_minutes_per_game == pytest.approx(30.0)
    assert g.points_per_regulation_minutes == pytest.approx(92 / 120 * 48)
    # league: mean 24.5, rate (392 / 480) * 48 = 39.2
    assert g.league_average == pytest.approx(24.5)
    assert g.league_rate == pytest.approx(392 / 480 * 48)
    assert g.delta_per_game == pytest.approx(-1.5)
    # shares: 23 of 48 points allowed, against 24.5 of 49.5 for the league
    assert g.share == pytest.approx(23 / 48)
    assert g.league_share == pytest.approx(24.5 / 49.5)
    assert t1.points_allowed_per_game == pytest.approx(48.0)
    assert t1.league_points_allowed_per_game == pytest.approx(49.5)
    assert t1.sum_of_buckets == pytest.approx(48.0)
    assert t1.coverage == D.Coverage(listed=1.0, workbook_listing=0.0, unknown=0.0)


def test_the_regulation_minutes_are_the_leagues() -> None:
    nba = dataclasses.replace(LP.NBA, defense=profile().defense)
    el_profile = dataclasses.replace(LP.EUROLEAGUE, defense=profile().defense)
    g_nba = D.compute_defense(guard_league(), profile=nba).team("T1").bucket("G")
    g_el = D.compute_defense(guard_league(), profile=el_profile).team("T1").bucket("G")
    assert g_nba.points_per_regulation_minutes == pytest.approx(92 / 120 * 48)
    assert g_el.points_per_regulation_minutes == pytest.approx(92 / 120 * 40)


def test_attempts_are_averaged_over_the_games_that_recorded_them() -> None:
    games = []
    for n, fga in enumerate([10, None, 14]):
        lines = [D.OpponentLine(30, 20, {"G": 1.0}, fga=fga, fta=2, fg3a=1)]
        games.append(
            D.allocate_game(game_id=f"g{n}", date=day(n), team="T", opp_pts=20, lines=lines)
        )
    table = D.compute_defense({"T": games, "U": games[:]}, profile=profile())
    g = table.team("T").bucket("G")
    assert g.attempts_per_game["fga"] == pytest.approx(12.0)  # (10 + 14) / 2, not / 3
    assert g.attempts_per_game["fta"] == pytest.approx(2.0)
    assert table.team("T").bucket("C").attempts_per_game["fga"] == 0.0


# ------------------------------------------------------------------ 6. the properties


def random_game(rng, team, n, scheme="gfc"):
    known = ("G", "F", "C") if scheme == "gfc" else ("PG", "SG", "SF", "PF", "C")
    lines = []
    for _ in range(rng.randint(6, 12)):
        roll = rng.random()
        if roll < 0.15:
            weights = None
        elif roll < 0.35:
            a, b = rng.sample(known, 2)
            weights = {a: 0.5, b: 0.5}
        else:
            weights = {rng.choice(known): 1.0}
        minutes = rng.choice([0, 0, rng.uniform(1, 38)])  # some DNPs
        pts = 0 if minutes == 0 else rng.randint(0, 25)
        lines.append(
            D.OpponentLine(
                minutes,
                pts,
                weights,
                fga=pts // 2 + rng.randint(0, 4),
                fta=rng.randint(0, 6),
                fg3a=rng.randint(0, 6),
            )
        )
    total = sum(line.pts for line in lines)
    if rng.random() < 0.1:
        total += rng.choice([-3, 1, 7])  # an unreconcilable game now and then
    return D.allocate_game(
        game_id=f"{team}-{n}", date=day(n), team=team, opp_pts=total, lines=lines, scheme=scheme
    )


@pytest.mark.parametrize("seed", range(10))
def test_buckets_sum_to_points_allowed_and_deltas_to_the_difference(seed) -> None:
    rng = random.Random(seed)
    league = {
        f"T{i}": [random_game(rng, f"T{i}", n) for n in range(rng.randint(4, 14))] for i in range(6)
    }
    table = D.compute_defense(league, profile=profile(min_games=3, provisional_below=8))
    reconciled = [g for games in league.values() for g in games if g.reconciled]
    assert reconciled
    league_papg = sum(g.opp_pts for g in reconciled) / len(reconciled)
    assert table.league.points_allowed_per_game == pytest.approx(league_papg, abs=1e-9)
    assert sum(b for b in table.league.mean_points.values()) == pytest.approx(league_papg)
    for team in table.teams:
        if team.games == 0:
            continue
        mine = sorted(
            (g for g in league[team.team] if g.reconciled),
            key=lambda g: (g.date, g.game_id),
            reverse=True,
        )
        papg = sum(g.opp_pts for g in mine) / len(mine)
        assert team.points_allowed_per_game == pytest.approx(papg, abs=1e-9)
        assert team.sum_of_buckets == pytest.approx(papg, abs=1e-9)
        deltas = [b.delta_per_game for b in team.buckets]
        assert sum(deltas) == pytest.approx(papg - league_papg, abs=1e-9)
        assert sum(b.share for b in team.buckets) == pytest.approx(1.0, abs=1e-9)
        assert sum(
            filter(
                None, [team.coverage.listed, team.coverage.workbook_listing, team.coverage.unknown]
            )
        ) == pytest.approx(1.0, abs=1e-9)
        assert team.unreconciled_games == len(league[team.team]) - len(mine)


@pytest.mark.parametrize("seed", range(6))
def test_a_game_reconciles_exactly_when_its_players_sum_to_its_score(seed) -> None:
    rng = random.Random(100 + seed)
    seen_reconciled = seen_not = 0
    for n in range(60):
        scheme = "gfc" if seed % 2 == 0 else "workbook5"
        g = random_game(rng, "T", n, scheme=scheme)
        allocated = math.fsum(g.points.values())
        # whatever the weights, every point that was scored lands in exactly one bucket
        assert math.fsum(g.basis_points.values()) == pytest.approx(allocated)
        assert g.reconciled == (abs(allocated - g.opp_pts) < 1e-9)
        assert g.reconciliation_error == pytest.approx(abs(allocated - g.opp_pts), abs=1e-12)
        seen_reconciled += g.reconciled
        seen_not += not g.reconciled
    assert seen_reconciled > 20 and seen_not >= 1  # the generator exercises both outcomes


# ----------------------------------------------------- 7. honesty about noise and signal


def noisy_league(seed, *, teams=20, games=38, signal=0.0):
    rng = random.Random(seed)
    out = {}
    for t in range(teams):
        effect = signal * (t - teams / 2) / teams
        name = f"T{t}"
        out[name] = [
            game(name, n, rng.gauss(30 * (1 + effect), 6), rng.gauss(25, 5), rng.gauss(20, 5))
            for n in range(games)
        ]
    return out


def test_a_league_of_identical_teams_shows_no_better_or_worse_cell() -> None:
    """The family-wise correction is what stops noise from being read as a defence."""
    cells = banded = detected = gate_cleared = 0
    for seed in range(30):
        table = D.compute_defense(noisy_league(seed), profile=LP.EUROLEAGUE)
        detected += table.method.league_signal == "detected"
        gate_cleared += any(r >= 0.2 for r in table.method.league_reliability.values())
        for team in table.teams:
            for bucket in team.buckets[:3]:
                cells += 1
                banded += bucket.band in ("better", "worse")
    assert cells == 30 * 20 * 3
    assert banded == 0
    assert detected == 0  # and the league is never told there is a signal
    # why the signal label is keyed to the displayed bands and not to the reliability gate: in
    # leagues of identical teams, noise alone clears a reliability of 0.2 at some position often
    assert gate_cleared >= 5


def test_a_league_with_a_real_difference_shows_bands_and_says_so() -> None:
    table = D.compute_defense(noisy_league(1, signal=0.6), profile=LP.EUROLEAGUE)
    assert table.method.league_signal == "detected"
    assert table.league_signal_message is None
    bands = [t.bucket("G").band for t in table.teams]
    assert "better" in bands and "worse" in bands
    assert table.method.league_reliability["G"] > 0.9
    best, worst = min(table.teams, key=lambda t: t.bucket("G").index), max(
        table.teams, key=lambda t: t.bucket("G").index
    )
    assert best.bucket("G").band == "better" and worst.bucket("G").band == "worse"


def test_the_signal_is_untestable_while_every_team_is_provisional() -> None:
    table = D.compute_defense(noisy_league(2, teams=10, games=8, signal=0.6), profile=LP.EUROLEAGUE)
    assert all(t.provisional for t in table.teams)
    assert table.method.league_signal is None  # not "none detected": nothing was tested
    assert table.league_signal_message is None


# ---------------------------------------------------------------------- 8. the wire shape


def sample_table():
    return D.compute_defense(guard_league(), profile=profile(), position_source="roster listing")


def test_team_payload_has_exactly_the_designed_keys() -> None:
    team = sample_table().team("T2")
    payload = team.to_payload()
    assert list(payload) == [
        "window",
        "pointsAllowedPerGame",
        "leaguePointsAllowedPerGame",
        "buckets",
        "provisional",
        "withheld",
        "coverage",
        "reconciliation",
    ]
    assert list(payload["window"]) == ["kind", "games", "requested"]
    assert list(payload["coverage"]) == ["listed", "workbookListing", "unknown"]
    assert list(payload["reconciliation"]) == [
        "sumOfBuckets",
        "pointsAllowedPerGame",
        "unreconciledGames",
        "identity",
    ]
    assert payload["reconciliation"]["identity"] == (
        "sum(deltaPerGame) = pointsAllowedPerGame - leaguePointsAllowedPerGame"
    )
    assert [b["position"] for b in payload["buckets"]] == ["G", "F", "C", "unknown"]
    assert list(payload["buckets"][0]) == [
        "position",
        "label",
        "pointsAllowedPerGame",
        "leagueAverage",
        "deltaPerGame",
        "opponentMinutesPerGame",
        "pointsPerRegulationMinutes",
        "leagueRate",
        "share",
        "leagueShare",
        "rawIndex",
        "index",
        "standardError",
        "band",
    ]
    assert payload["buckets"][0]["label"] == "Guards"
    assert payload["buckets"][0]["band"] == "worse"  # T2 in the hand example
    assert payload["withheld"] is None and payload["provisional"] is False
    assert json.loads(json.dumps(payload)) == payload


def test_withheld_payload_nulls_every_index_and_keeps_the_raw_facts() -> None:
    teams = {f"T{i}": 6 for i in range(8)} | {"SHORT": 2}
    table = D.compute_defense(league_of(teams), profile=el())
    payload = table.team("SHORT").to_payload()
    assert payload["withheld"] == {
        "reason": "minimumGames",
        "message": "Only 2 games, too few to judge a defence",
    }
    for bucket in payload["buckets"]:
        assert bucket["rawIndex"] is None and bucket["index"] is None
        assert bucket["standardError"] is None and bucket["band"] is None
    assert payload["buckets"][0]["pointsAllowedPerGame"] is not None
    assert payload["buckets"][0]["leagueAverage"] is not None


def test_method_payload_has_exactly_the_designed_keys() -> None:
    method = sample_table().method.to_payload()
    assert list(method) == [
        "minimumGames",
        "provisionalBelowGames",
        "coverageCeiling",
        "shrinkage",
        "leagueReliability",
        "leagueSignal",
        "bandRule",
        "positionSource",
        "taxonomy",
        "limitations",
    ]
    assert method["shrinkage"] == "empiricalBayes"
    assert method["minimumGames"] == 3 and method["provisionalBelowGames"] == 4
    assert method["coverageCeiling"] == 0.05
    assert method["positionSource"] == "roster listing"
    assert set(method["leagueReliability"]) == {"G", "F", "C"}
    assert "Bonferroni family of 12 cells" in method["bandRule"]
    assert any("who guarded whom" in text for text in method["limitations"])
    assert any("injury layer never changes defence" in text for text in method["limitations"])
    assert any("opponents a team has faced" in text for text in method["limitations"])
    assert json.loads(json.dumps(method)) == method


def test_the_default_profile_thresholds_reach_the_method_block() -> None:
    nba = D.compute_defense(guard_league(), profile=LP.NBA).method
    assert (nba.minimum_games, nba.provisional_below_games) == (10, 25)
    eu = D.compute_defense(guard_league(), profile=LP.EUROLEAGUE).method
    assert (eu.minimum_games, eu.provisional_below_games) == (6, 12)


def test_table_row_and_summary_shapes() -> None:
    team = sample_table().team("T4")
    row = team.to_table_row()
    assert list(row) == ["games", "pointsAllowedPerGame", "buckets", "provisional", "withheld"]
    summary = team.to_summary()
    assert list(summary) == ["pointsAllowedPerGame", "buckets", "withheld"]
    assert list(summary["buckets"][0]) == [
        "position",
        "pointsAllowedPerGame",
        "deltaPerGame",
        "band",
    ]
    assert summary["buckets"][0]["band"] == "better"


def test_nothing_in_the_payloads_is_a_rank_or_a_market_word() -> None:
    table = sample_table()
    blobs = [t.to_payload() for t in table.teams]
    blobs += [t.to_table_row() for t in table.teams] + [t.to_summary() for t in table.teams]
    blobs.append(table.method.to_payload())
    for blob in blobs:
        assert market_guard.scan_keys(blob) == []
        keys = set()

        def collect(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    keys.update(market_guard.split_words(key))
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(blob)
        assert not keys & {"rank", "ranks", "ranking", "percentile"}


def test_the_caveat_says_what_the_number_is_not() -> None:
    assert "listed at each position" in D.CAVEAT
    assert "does not measure who guarded whom" in D.CAVEAT


def test_taxonomy_text() -> None:
    assert "Guard, Forward and Center" in D.taxonomy_text("gfc")
    assert "workbook author" in D.taxonomy_text("workbook5")


# ------------------------------------------------------------------------- validation


def test_arguments_are_validated() -> None:
    league = guard_league()
    with pytest.raises(ValueError):
        D.compute_defense(league, profile=profile(), basis="perPossession")
    for bad in (-1, True, 2.5, "3"):
        with pytest.raises(ValueError):
            D.compute_defense(league, profile=profile(), window=bad)
    with pytest.raises(ValueError):
        D.compute_defense(league, profile=profile(), scheme="nope")


def test_duplicate_games_for_a_team_are_rejected() -> None:
    games = [game("T1", 1, 20), game("T1", 1, 21)]
    with pytest.raises(ValueError, match="duplicate"):
        D.compute_defense({"T1": games, "T2": [game("T2", 1, 20)]}, profile=profile())


def test_an_empty_league_does_not_crash() -> None:
    table = D.compute_defense({}, profile=profile())
    assert table.teams == () and table.league.team_games == 0
    assert table.method.league_signal is None
    table = D.compute_defense({"T": []}, profile=profile())
    assert table.team("T").withheld.reason == "minimumGames"
