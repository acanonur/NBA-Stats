"""Tests for the injury layer.

The layer is the workbook's mechanism, so the first duty of this suite is arithmetic: small
squads worked by hand, every intermediate named (full, available, lost, replacement, absorbed,
boost, squad points, the availability factor). The second duty is the two places the layer is
*deliberately* not the sheet:

* **The clamp.** When the missing players score less than replacement level would, the sheet's
  formula says teammates absorb *more than was lost*, so an absence raises scoring. The layer
  clamps that. The agreement everywhere else is checked by
  ``test_team_projection_formulas.py`` against a literal transcription of the cells.
* **The cap policy.** The sheet caps each player's boost but not the team total, so when the
  cap binds the team projection counts points no player is credited with. ``consistent``
  (the default) makes the team equal the sum of its players; ``workbook`` reproduces the sheet
  and reports the gap as ``unassigned``.

Properties, on random squads, guard what no single example can: an absence never makes a squad
score more than at full strength, the consistent policy never exceeds the workbook one, and
under the consistent policy the team always equals the sum of its players plus the
replacement players' line, and never falls below the replacement floor.
"""

from __future__ import annotations

import math
import random

import pytest

from nbastats.shared import injury_layer as IL

# The workbook's levers: 9 replacement points per 40 minutes, 60% absorbed, boost cap 1.35,
# half of lost minutes taken by the listed rotation.
Q = 9 / 40


def settings(policy=IL.CAP_CONSISTENT, **overrides) -> IL.InjurySettings:
    base = dict(
        replacement_per_minute=Q,
        absorb_share=0.6,
        boost_cap=1.35,
        rotation_share=0.5,
        cap_policy=policy,
    )
    base.update(overrides)
    return IL.InjurySettings(**base)


def squad(*rows):
    """``(minutes, points, chance)`` triples as players named by position in the list."""
    return [
        IL.PlayerInput(key=f"P{i}", minutes=m, points=p, chance=c)
        for i, (m, p, c) in enumerate(rows, start=1)
    ]


# A squad with a star out and a rotation player doubtful, hand-worked:
#   P1 30 min 20 pts c=0 (out)   P2 30 min 10 pts c=1   P3 30 min 8 pts c=.5
#   P4 20 min 6 pts c=1          P5 20 min 4 pts c=1
BIG_ABSENCE = squad((30, 20, 0), (30, 10, 1), (30, 8, 0.5), (20, 6, 1), (20, 4, 1))


def test_hand_worked_squad_under_the_default_policy() -> None:
    r = IL.apply_injury_layer(BIG_ABSENCE, settings())
    # full = 20+10+8+6+4 = 48          avail = 0+10+4+6+4 = 24        lost = 20+0+4 = 24
    # lostMin = 30+0+15 = 45           availMin = 0+30+15+20+20 = 85
    assert r.full_strength_points == 48.0 and r.available_points == 24.0
    assert r.lost_points == 24.0
    assert r.lost_minutes == 45.0 and r.available_minutes == 85.0
    # repl = 45 * 0.225 = 10.125;  lost >= repl so absorbed = 10.125 + 0.6 * (24 - 10.125) = 18.45
    assert r.replacement_points == pytest.approx(10.125)
    assert r.absorbed_points == pytest.approx(18.45)
    # 1 + 18.45/24 = 1.76875 exceeds the cap, so every player is boosted by exactly 1.35
    assert r.boost == pytest.approx(1.35) and r.cap_binding
    # consistent: the players take 0.35 * 24 = 8.4; the replacement players are credited the
    # rest up to repl: absorbedEff = min(18.45, 8.4 + 10.125 = 18.525) = 18.45; squad = 42.45
    assert r.absorbed_effective == pytest.approx(18.45) and r.unassigned_points == 0.0
    assert r.replacement_credited == pytest.approx(18.45 - 8.4)
    assert r.squad_points == pytest.approx(42.45)
    assert r.availability_factor == pytest.approx(42.45 / 48)
    assert r.cap_policy == "consistent"


def test_the_consistent_policy_never_caps_away_the_replacement_floor() -> None:
    # A tight cap: the players can take only 0.05 * 24 = 1.2, so absorbedEff = 1.2 + 10.125
    r = IL.apply_injury_layer(BIG_ABSENCE, settings(boost_cap=1.05))
    assert r.cap_binding
    assert r.absorbed_effective == pytest.approx(1.2 + 10.125)
    assert r.replacement_credited == pytest.approx(10.125)
    assert r.squad_points == pytest.approx(24 + 1.2 + 10.125)
    assert r.squad_points >= r.available_points + min(r.replacement_points, r.lost_points)


def test_the_consistent_policy_makes_the_team_equal_the_sum_of_its_players() -> None:
    r = IL.apply_injury_layer(BIG_ABSENCE, settings())
    # p * c * boost: P2 13.5, P3 5.4, P4 8.1, P5 5.4, P1 0; plus the replacement players' line
    assert [p.projected_points for p in r.players] == pytest.approx([0.0, 13.5, 5.4, 8.1, 5.4])
    assert sum(p.projected_points for p in r.players) + r.replacement_credited == pytest.approx(
        r.squad_points
    )


def test_the_workbook_policy_reproduces_the_sheet_and_reports_the_gap() -> None:
    r = IL.apply_injury_layer(BIG_ABSENCE, settings(IL.CAP_WORKBOOK))
    # the sheet credits the team with all 18.45 recovered points: squad = 24 + 18.45 = 42.45
    assert r.absorbed_effective == pytest.approx(18.45)
    assert r.squad_points == pytest.approx(42.45)
    assert r.availability_factor == pytest.approx(42.45 / 48)
    # but no player can take more than the 1.35 boost: 0.35 * 24 = 8.4 of them are assigned
    assert r.unassigned_points == pytest.approx(18.45 - 8.4)
    assert sum(p.projected_points for p in r.players) == pytest.approx(32.4)
    assert r.squad_points - sum(p.projected_points for p in r.players) == pytest.approx(
        r.unassigned_points
    )
    assert r.cap_binding and r.cap_policy == "workbook"


def test_policies_agree_when_the_cap_does_not_bind() -> None:
    # A(30 min, 15 pts, plays) B(30, 12, plays) C(20, 6, c=.5):
    #   lost = 3, lostMin = 10, repl = 2.25, absorbed = 2.25 + 0.6 * 0.75 = 2.7
    #   avail = 15 + 12 + 3 = 30, boost = 1 + 2.7/30 = 1.09 (under the cap)
    players = squad((30, 15, 1), (30, 12, 1), (20, 6, 0.5))
    consistent = IL.apply_injury_layer(players, settings())
    workbook = IL.apply_injury_layer(players, settings(IL.CAP_WORKBOOK))
    assert consistent.absorbed_points == pytest.approx(2.7)
    assert consistent.boost == pytest.approx(1.09) and not consistent.cap_binding
    assert consistent.squad_points == pytest.approx(32.7)
    assert consistent.availability_factor == pytest.approx(32.7 / 33)
    assert consistent.unassigned_points == 0.0 and workbook.unassigned_points == pytest.approx(0.0)
    for field in ("squad_points", "availability_factor", "boost", "absorbed_effective"):
        assert getattr(consistent, field) == pytest.approx(getattr(workbook, field))
    # and the players add up to the team under either policy
    for result in (consistent, workbook):
        assert sum(p.projected_points for p in result.players) == pytest.approx(result.squad_points)


def test_projected_minutes_for_display() -> None:
    r = IL.apply_injury_layer(BIG_ABSENCE, settings())
    # min(1 + 0.5 * 45 / 85, 1.35) = 1.264705882...
    factor = 1 + 0.5 * 45 / 85
    assert [p.projected_minutes for p in r.players] == pytest.approx(
        [0.0, 30 * factor, 15 * factor, 20 * factor, 20 * factor]
    )
    # the factor is capped too: a rotation share of 1 with many lost minutes
    capped = IL.apply_injury_layer(BIG_ABSENCE, settings(rotation_share=1.0))
    assert capped.players[1].projected_minutes == pytest.approx(30 * 1.35)


def test_per_player_losses_are_reported() -> None:
    r = IL.apply_injury_layer(BIG_ABSENCE, settings())
    assert [p.expected_points_lost for p in r.players] == pytest.approx([20, 0, 4, 0, 0])
    assert [p.expected_minutes_lost for p in r.players] == pytest.approx([30, 0, 15, 0, 0])
    assert [p.chance for p in r.players] == [0, 1, 0.5, 1, 1]
    assert [p.key for p in r.players] == ["P1", "P2", "P3", "P4", "P5"]  # input order kept


# ----------------------------------------------------------------------- the clamp


def test_an_absence_of_someone_who_scores_below_replacement_cannot_help() -> None:
    # X plays 30 minutes at 3 points: 0.1 a minute, below the 0.225 replacement pace.
    #   lost = 3, lostMin = 30, repl = 6.75 > lost.
    # The sheet's formula: absorbed = 6.75 + 0.6 * (3 - 6.75) = 4.5 > lost, so squad = 33 + 4.5.
    players = squad((30, 3, 0), (30, 20, 1), (30, 10, 1))
    r = IL.apply_injury_layer(players, settings(IL.CAP_WORKBOOK))
    assert r.lost_points == 3.0 and r.replacement_points == pytest.approx(6.75)
    assert r.absorbed_points == 3.0  # clamped to what was lost
    assert r.squad_points == pytest.approx(30.0 + 3.0)
    assert r.availability_factor == pytest.approx(1.0)  # an absence that costs nothing
    sheet_absorbed = 6.75 + 0.6 * (3 - 6.75)
    assert sheet_absorbed > r.lost_points  # what the sheet would have claimed
    assert (30.0 + sheet_absorbed) / 33.0 > 1.0


def test_the_clamp_is_inactive_exactly_at_replacement_level() -> None:
    # lost == repl: both branches give the same answer (repl + 0.6 * 0 = lost)
    players = squad((40, 9, 0), (30, 20, 1))  # lostMin 40 * 0.225 = 9 = lost
    r = IL.apply_injury_layer(players, settings())
    assert r.lost_points == 9.0 and r.replacement_points == pytest.approx(9.0)
    assert r.absorbed_points == pytest.approx(9.0)


# ---------------------------------------------------------------- degenerate squads


def test_a_healthy_squad_loses_nothing() -> None:
    r = IL.apply_injury_layer(squad((30, 20, 1), (30, 10, 1)), settings())
    assert (r.lost_points, r.lost_minutes, r.replacement_points, r.absorbed_points) == (0, 0, 0, 0)
    assert r.boost == 1.0 and r.availability_factor == 1.0 and not r.cap_binding
    assert r.squad_points == r.full_strength_points == 30.0


def test_an_empty_squad_is_a_squad_with_nothing_to_lose() -> None:
    r = IL.apply_injury_layer([], settings())
    assert r.full_strength_points == 0.0 and r.availability_factor == 1.0 and r.players == ()


def test_a_squad_that_scores_nothing_has_availability_factor_one() -> None:
    r = IL.apply_injury_layer(squad((30, 0, 0.5), (30, 0, 1)), settings())
    assert r.full_strength_points == 0.0 and r.availability_factor == 1.0


def test_everyone_out_under_each_policy() -> None:
    players = squad((30, 20, 0), (30, 10, 0))
    # full 30, avail 0, lost 30, lostMin 60, repl 13.5, absorbed 13.5 + 0.6 * 16.5 = 23.4
    consistent = IL.apply_injury_layer(players, settings())
    assert consistent.available_points == 0.0 and consistent.boost == 1.0
    assert consistent.absorbed_points == pytest.approx(23.4)
    # nobody listed plays, so only the replacement players score: min(23.4, 0 + 13.5) = 13.5
    assert consistent.absorbed_effective == pytest.approx(13.5)
    assert consistent.squad_points == pytest.approx(13.5)
    assert consistent.replacement_credited == pytest.approx(13.5)
    assert consistent.availability_factor == pytest.approx(13.5 / 30) and consistent.cap_binding
    workbook = IL.apply_injury_layer(players, settings(IL.CAP_WORKBOOK))
    # the sheet's own arithmetic: squad = 0 + absorbed, and no player can take any of it
    assert workbook.squad_points == pytest.approx(23.4)
    assert workbook.unassigned_points == pytest.approx(23.4)
    assert workbook.boost == 1.0


def test_the_boost_never_exceeds_the_cap_and_never_falls_below_one() -> None:
    r = IL.apply_injury_layer(BIG_ABSENCE, settings(boost_cap=1.1))
    assert r.boost == pytest.approx(1.1)
    r = IL.apply_injury_layer(BIG_ABSENCE, settings(boost_cap=1.0))
    # a cap of 1 forbids any boost; only the replacement-level refill is credited
    assert r.boost == 1.0 and r.absorbed_effective == pytest.approx(10.125)
    assert r.availability_factor == pytest.approx((24 + 10.125) / 48)


def test_zero_absorb_share_recovers_only_replacement() -> None:
    r = IL.apply_injury_layer(BIG_ABSENCE, settings(absorb_share=0.0, boost_cap=5.0))
    assert r.absorbed_points == pytest.approx(10.125)  # just the replacement-level refill
    assert r.squad_points == pytest.approx(24 + 10.125)


# --------------------------------------------------------------------- the properties


def random_squad(rng):
    return [
        IL.PlayerInput(
            key=i,
            minutes=rng.uniform(0, 36),
            points=rng.uniform(0, 25),
            chance=rng.choice([0, 0.25, 0.5, 0.85, 1, 1, 1, rng.random()]),
        )
        for i in range(rng.randint(1, 16))
    ]


@pytest.mark.parametrize("seed", range(20))
def test_an_absence_never_raises_a_squads_scoring(seed) -> None:
    rng = random.Random(seed)
    for _ in range(30):
        players = random_squad(rng)
        for policy in IL.CAP_POLICIES:
            s = settings(
                policy,
                absorb_share=rng.random(),
                boost_cap=rng.uniform(1.0, 2.0),
                rotation_share=rng.random(),
            )
            r = IL.apply_injury_layer(players, s)
            assert r.squad_points <= r.full_strength_points + 1e-9
            assert r.availability_factor <= 1.0 + 1e-12
            assert r.availability_factor >= 0.0
            assert r.absorbed_points <= r.lost_points + 1e-9  # the clamp, as an invariant


@pytest.mark.parametrize("seed", range(20))
def test_consistent_never_exceeds_workbook_and_always_matches_its_players(seed) -> None:
    rng = random.Random(1000 + seed)
    for _ in range(30):
        players = random_squad(rng)
        kappa = rng.uniform(1.0, 2.0)
        a = rng.random()
        consistent = IL.apply_injury_layer(players, settings(absorb_share=a, boost_cap=kappa))
        workbook = IL.apply_injury_layer(
            players, settings(IL.CAP_WORKBOOK, absorb_share=a, boost_cap=kappa)
        )
        assert consistent.squad_points <= workbook.squad_points + 1e-9
        assert consistent.unassigned_points == 0.0 and workbook.unassigned_points >= -1e-9
        assert workbook.replacement_credited == 0.0 and consistent.replacement_credited >= 0.0
        floor = consistent.available_points + min(
            consistent.replacement_points, consistent.lost_points
        )
        assert consistent.squad_points >= floor - 1e-9  # the replacement floor survives the cap
        total = sum(p.projected_points for p in consistent.players)
        assert total + consistent.replacement_credited == pytest.approx(
            consistent.squad_points, abs=1e-9
        )
        if consistent.available_points > 0:
            gap = workbook.squad_points - sum(p.projected_points for p in workbook.players)
            assert gap == pytest.approx(workbook.unassigned_points, abs=1e-9)
        if not workbook.cap_binding:
            assert consistent.squad_points == pytest.approx(workbook.squad_points, abs=1e-9)
            assert workbook.unassigned_points == pytest.approx(0.0, abs=1e-9)


def test_cap_binding_is_the_designed_condition() -> None:
    rng = random.Random(7)
    for _ in range(200):
        players = random_squad(rng)
        kappa = rng.uniform(1.0, 2.0)
        r = IL.apply_injury_layer(players, settings(boost_cap=kappa))
        if r.available_points > 0:
            assert r.cap_binding == ((1 + r.absorbed_points / r.available_points) > kappa)
            assert r.boost == pytest.approx(min(1 + r.absorbed_points / r.available_points, kappa))


# ------------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(chance=1.0001),
        dict(chance=-0.0001),
        dict(chance=math.nan),
        dict(chance=True),
        dict(minutes=-1),
        dict(minutes=math.inf),
        dict(points=-0.5),
        dict(points="20"),
        dict(points=None),
    ],
)
def test_a_player_outside_the_valid_ranges_is_refused_at_entry(kwargs) -> None:
    base = dict(key="x", minutes=20, points=10, chance=1.0)
    with pytest.raises(ValueError):
        IL.PlayerInput(**{**base, **kwargs})


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(replacement_per_minute=-0.1),
        dict(absorb_share=1.1),
        dict(absorb_share=-0.1),
        dict(boost_cap=0.99),
        dict(rotation_share=1.5),
        dict(cap_policy="optimistic"),
        dict(replacement_per_minute=math.nan),
        dict(boost_cap=True),
        dict(absorb_share="0.6"),
    ],
)
def test_settings_outside_the_valid_ranges_are_refused(kwargs) -> None:
    with pytest.raises(ValueError):
        settings(**kwargs)


def test_settings_from_the_workbooks_units() -> None:
    s = IL.InjurySettings.from_per40(9, 0.6, 1.35)
    assert s.replacement_per_minute == pytest.approx(0.225)
    assert s.cap_policy == "consistent" and s.rotation_share == 0.5
    assert (
        IL.InjurySettings.from_per40(9, 0.6, 1.35, cap_policy="workbook").cap_policy == "workbook"
    )


def test_the_policy_names() -> None:
    assert IL.CAP_POLICIES == ("consistent", "workbook")
    assert IL.CAP_CONSISTENT == "consistent" and IL.CAP_WORKBOOK == "workbook"
    assert settings().cap_policy == "consistent"  # the default


# ------------------------------------------------------------ squad reconciliation


def test_scale_to_total() -> None:
    assert IL.scale_to_total([10, 20, 30], 90) == pytest.approx([15, 30, 45])
    scaled = IL.scale_to_total([3.3, 4.4, 5.5, 1.1], 100)
    assert sum(scaled) == pytest.approx(100.0)
    assert scaled[0] / scaled[1] == pytest.approx(3.3 / 4.4)  # proportions are kept
    assert IL.scale_to_total([], 50) == []


def test_scale_to_total_leaves_an_all_zero_squad_alone() -> None:
    assert IL.scale_to_total([0, 0, 0], 80) == [0, 0, 0]


@pytest.mark.parametrize("target", [-1, math.nan, math.inf])
def test_scale_to_total_rejects_a_bad_target(target) -> None:
    with pytest.raises(ValueError):
        IL.scale_to_total([1, 2], target)
