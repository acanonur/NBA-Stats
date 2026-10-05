"""Player rates and minutes: the workbook's update, continued, and counted exactly once.

The failure this file exists to catch is the one the judges named: the imported rates are "as of
round 2", so adding Round 2's box score to them again counts it twice. Every test here is built
around that fence (only games *after* the row's ``as_of_round`` may move a number), and around the
rules that decide what an absent stat, a missed game and a player with no rate of his own mean.

Part one is pure arithmetic over hand-built bases and lines, with every expected value worked out
in the test. Part two reads the invented demo league and recomputes one player's numbers straight
from the store, so the loading and the arithmetic cannot disagree.
"""

from __future__ import annotations

import math
from datetime import timezone

import pytest
from sqlalchemy import select

from nbastats.euroleague.demo import DEMO_AS_OF
from nbastats.euroleague.model import player_rates as pr
from nbastats.euroleague.model.round_projection import get_model
from nbastats.euroleague.models import ElGame, ElPlayerGame, ElPlayerRate
from nbastats.euroleague.read.queries import build_context

NOW = DEMO_AS_OF.replace(tzinfo=timezone.utc)

RATES = {
    "pts40": 20.0,
    "reb40": 6.0,
    "ast40": 4.0,
    "fg3m40": 2.0,
    "stl40": 1.0,
    "blk40": 0.5,
    "tov40": 2.5,
    "fga2_40": 8.0,
    "fga3_40": 6.0,
    "fta40": 4.0,
    "fg2_pct": 0.5,
    "fg3_pct": 1.0 / 3.0,
    "ft_pct": 0.75,
}


def base(
    person="p1",
    club="AAA",
    as_of=2,
    basis="workbookOfficial",
    prior=400.0,
    minutes=25.0,
    rates=None,
    bucket="G",
):
    return pr.PlayerBase(
        person_code=person,
        club_code=club,
        as_of_round=as_of,
        basis=basis,
        prior_minutes=prior,
        proj_minutes=minutes,
        rates=dict(RATES if rates is None else rates),
        bucket=bucket,
    )


def line(person="p1", club="AAA", rnd=3, game=None, seconds=1800, **stats):
    values = dict(
        pts=0, reb=0, ast=0, fgm2=0, fga2=0, fgm3=0, fga3=0, ftm=0, fta=0, stl=0, blk=0, tov=0
    )
    values.update(stats)
    return pr.PlayerLine(person, club, rnd, game or f"G{rnd}-{club}", seconds, values)


def games(club="AAA", rounds=(1, 2, 3, 4)):
    return [pr.ClubGame(club, r, f"G{r}-{club}") for r in rounds]


def never(person, game_id):
    return False


# --------------------------------------------------------------------------- the arithmetic


def test_blend_rate_counts_the_prior_as_p_minutes() -> None:
    assert pr.blend_rate(15.0, 400, 18, 10) == pytest.approx((15 * 400 + 40 * 18) / 410)
    # No minutes added: the prior is untouched.
    assert pr.blend_rate(15.0, 400, 0, 0) == 15.0
    with pytest.raises(ValueError):
        pr.blend_rate(15.0, 0, 1, 1)


def test_minutes_blend_continues_the_workbooks_weight_exactly() -> None:
    """``w = rounds / (rounds + 3)`` applied to the whole history equals the carried-forward blend."""
    preseason = 18.0
    history = [30.0, 12.0, 25.0, 0.0, 28.0]  # five rounds, a DNP among them
    k = 2
    implied = (1 - k / (k + 3)) * preseason + (k / (k + 3)) * (sum(history[:k]) / k)
    after = history[k:]
    carried = pr.blend_minutes(implied, k, sum(after), len(after))
    n = len(history)
    whole = (1 - n / (n + 3)) * preseason + (n / (n + 3)) * (sum(history) / n)
    assert carried == pytest.approx(whole, abs=1e-12)


def test_normalise_minutes_hits_the_total_and_holds_the_cap() -> None:
    squad = {
        f"p{i}": m for i, m in enumerate([40, 36, 30, 28, 24, 20, 16, 12, 8, 4, 2, 1], start=1)
    }
    out = pr.normalise_minutes(squad)
    assert math.fsum(out.values()) == pytest.approx(200.0)
    assert max(out.values()) <= 32.0 + 1e-9
    assert out["p1"] == pytest.approx(32.0) and out["p2"] == pytest.approx(32.0)
    # The ones that were not capped keep their relative order.
    ordered = [out[k] for k in sorted(squad, key=lambda k: -squad[k])]
    assert ordered == sorted(ordered, reverse=True)


def test_normalise_minutes_cannot_invent_minutes_for_a_tiny_squad() -> None:
    out = pr.normalise_minutes({"a": 30.0, "b": 20.0, "c": 10.0})
    assert out == {"a": 32.0, "b": 32.0, "c": 32.0}  # three men cannot play 200 minutes
    assert pr.normalise_minutes({}) == {}
    assert pr.normalise_minutes({"a": 0.0}) == {"a": 0.0}


def test_pooled_rates_are_total_production_over_total_minutes() -> None:
    a = base("a", minutes=30.0, rates={**RATES, "pts40": 20.0})
    b = base("b", minutes=10.0, rates={**RATES, "pts40": 10.0})
    estimate = base("c", basis="workbookEstimate", minutes=20.0, rates={**RATES, "pts40": 99.0})
    other_position = base("d", bucket="C", minutes=20.0, rates={**RATES, "pts40": 14.0})
    pooled = pr.pooled_position_rates([a, b, estimate, other_position])
    assert pooled["G"]["pts40"] == pytest.approx((30 * 20 + 10 * 10) / 40)
    assert pooled["C"]["pts40"] == pytest.approx(14.0)
    # An estimate never feeds the pool, and a percentage pools through makes over attempts.
    assert "fg3_pct" in pooled["G"]
    assert pooled["G"]["fg3_pct"] == pytest.approx(1 / 3)


# --------------------------------------------------------------------------- the fence


def test_games_at_or_before_the_base_round_move_nothing() -> None:
    """The imported rates already contain rounds 1 and 2; their box scores must not be added."""
    lines = {"p1": [line(rnd=1, pts=40), line(rnd=2, pts=40)]}
    states = pr.build_states({"p1": base()}, lines, {"AAA": games()}, never, 2)
    state = states["p1"]
    assert state.rates["pts40"] == 20.0  # untouched
    assert state.minutes == 25.0  # untouched: no game after the base
    assert state.games_after == 0 and state.club_games_after == 0


def test_a_game_after_the_base_round_moves_the_rate_by_the_blend() -> None:
    lines = {
        "p1": [
            line(rnd=3, seconds=1800, pts=30, reb=9, fgm3=3, fga3=5, fgm2=6, fga2=9, ftm=6, fta=8)
        ]
    }
    state = pr.build_states({"p1": base()}, lines, {"AAA": games()}, never, 3)["p1"]
    assert state.rates["pts40"] == pytest.approx((20 * 400 + 40 * 30) / (400 + 30))
    assert state.rates["reb40"] == pytest.approx((6 * 400 + 40 * 9) / 430)
    assert state.games_after == 1


def test_the_same_game_is_not_added_twice_when_asked_twice() -> None:
    lines = {"p1": [line(rnd=3, pts=30)]}
    first = pr.build_states({"p1": base()}, lines, {"AAA": games()}, never, 3)["p1"]
    again = pr.build_states({"p1": base()}, lines, {"AAA": games()}, never, 3)["p1"]
    assert first.rates == again.rates and first.minutes == again.minutes


# --------------------------------------------------------------------------- minutes


def test_minutes_follow_the_workbook_blend_and_a_missed_game_counts_as_zero() -> None:
    """Round 3: 30 minutes. Round 4: not used, not injured: zero minutes of evidence."""
    lines = {"p1": [line(rnd=3, seconds=30 * 60)]}
    state = pr.build_states(
        {"p1": base(minutes=25.0)}, lines, {"AAA": games()}, never, 4, normalise=False
    )["p1"]
    assert state.club_games_after == 2
    assert state.minutes == pytest.approx(((2 + 3) * 25.0 + 30.0 + 0.0) / (2 + 3 + 2))


def test_a_game_missed_through_an_injury_is_no_evidence_either_way() -> None:
    lines = {"p1": [line(rnd=3, seconds=30 * 60)]}
    state = pr.build_states(
        {"p1": base(minutes=25.0)},
        lines,
        {"AAA": games()},
        lambda person, game: game == "G4-AAA",  # excused in round 4
        4,
        normalise=False,
    )["p1"]
    assert state.club_games_after == 1
    assert state.minutes == pytest.approx(((2 + 3) * 25.0 + 30.0) / (2 + 3 + 1))


def test_a_played_line_with_no_recorded_minutes_is_no_evidence() -> None:
    lines = {"p1": [pr.PlayerLine("p1", "AAA", 3, "G3-AAA", None, {"pts": 30})]}
    state = pr.build_states({"p1": base()}, lines, {"AAA": games()}, never, 3, normalise=False)[
        "p1"
    ]
    assert state.club_games_after == 0 and state.minutes == 25.0
    assert state.rates["pts40"] == 20.0


def test_squads_are_normalised_only_once_a_game_has_been_added() -> None:
    bases = {
        f"p{i}": base(f"p{i}", minutes=m) for i, m in enumerate([26, 25, 24, 23, 22, 21, 20, 19], 1)
    }
    quiet = pr.build_states(bases, {}, {"AAA": games()}, never, 2)
    assert {p: s.minutes for p, s in quiet.items()} == {p: b.proj_minutes for p, b in bases.items()}
    lines = {p: [line(p, rnd=3, seconds=20 * 60)] for p in bases}
    moved = pr.build_states(bases, lines, {"AAA": games()}, never, 3)
    assert math.fsum(s.minutes for s in moved.values()) == pytest.approx(200.0)
    assert max(s.minutes for s in moved.values()) <= 32.0 + 1e-9


# --------------------------------------------------------------------------- the null rule


def test_a_stat_a_line_did_not_record_is_left_out_of_that_stats_minutes() -> None:
    lines = {
        "p1": [
            line(rnd=3, seconds=20 * 60, tov=2),
            pr.PlayerLine(
                "p1", "AAA", 4, "G4-AAA", 20 * 60, {**line().stats, "tov": None, "pts": 10}
            ),
        ]
    }
    state = pr.build_states({"p1": base()}, lines, {"AAA": games()}, never, 4, normalise=False)[
        "p1"
    ]
    # Turnovers: only the first line carries them, so only its 20 minutes count.
    assert state.rates["tov40"] == pytest.approx((2.5 * 400 + 40 * 2) / (400 + 20))
    # Points: both lines carry them, over 40 minutes.
    assert state.rates["pts40"] == pytest.approx((20 * 400 + 40 * (0 + 10)) / (400 + 40))


def test_a_percentage_is_updated_through_makes_and_is_none_without_attempts() -> None:
    rates = {**RATES, "fta40": 0.0, "ft_pct": None, "fga3_40": 0.0, "fg3_pct": None, "fg3m40": 0.0}
    lines = {"p1": [line(rnd=3, seconds=20 * 60, ftm=0, fta=0, fgm3=0, fga3=0)]}
    state = pr.build_states({"p1": base(rates=rates)}, lines, {"AAA": games()}, never, 3)["p1"]
    assert state.rates["ft_pct"] is None and state.rates["fg3_pct"] is None
    # Two-point percentage: prior 0.5 on 8 attempts per 40; one game of 4-for-4 in 20 minutes.
    lines = {"p1": [line(rnd=3, seconds=20 * 60, fgm2=4, fga2=4)]}
    state = pr.build_states({"p1": base()}, lines, {"AAA": games()}, never, 3)["p1"]
    makes = (0.5 * 8.0 * 400 + 40 * 4) / (400 + 20)
    attempts = (8.0 * 400 + 40 * 4) / (400 + 20)
    assert state.rates["fg2_pct"] == pytest.approx(makes / attempts)
    assert 0.0 <= state.rates["fg2_pct"] <= 1.0


def test_attempts_after_a_zero_attempt_prior_give_a_percentage_at_last() -> None:
    rates = {**RATES, "fga3_40": 0.0, "fg3_pct": None, "fg3m40": 0.0}
    lines = {"p1": [line(rnd=3, seconds=20 * 60, fgm3=1, fga3=2)]}
    state = pr.build_states({"p1": base(rates=rates)}, lines, {"AAA": games()}, never, 3)["p1"]
    assert state.rates["fg3_pct"] == pytest.approx(((40 * 1) / 420) / ((40 * 2) / 420))


# --------------------------------------------------------------------------- bases without rates


def test_a_position_prior_player_starts_from_the_pooled_mean_with_the_small_prior() -> None:
    official = base("o", minutes=20.0, rates={**RATES, "pts40": 22.0})
    blank = pr.PlayerBase(
        "n", "AAA", 2, "positionPrior", 150.0, 12.0, {k: None for k in RATES}, "G"
    )
    state = pr.build_states({"o": official, "n": blank}, {}, {"AAA": games()}, never, 2)["n"]
    assert state.rates["pts40"] == pytest.approx(22.0)
    assert state.estimated
    # With a game added, the 150-minute prior is the weight.
    lines = {"n": [line("n", rnd=3, seconds=20 * 60, pts=10)]}
    after = pr.build_states({"o": official, "n": blank}, lines, {"AAA": games()}, never, 3)["n"]
    assert after.rates["pts40"] == pytest.approx((22.0 * 150 + 40 * 10) / (150 + 20))


def test_a_position_prior_player_with_nobody_to_pool_has_no_rate() -> None:
    blank = pr.PlayerBase(
        "n", "AAA", 2, "positionPrior", 150.0, 12.0, {k: None for k in RATES}, "G"
    )
    state = pr.build_states({"n": blank}, {}, {"AAA": games()}, never, 2)["n"]
    assert state.rates["pts40"] is None
    assert state.points_per_game is None


def test_expected_points_are_the_rate_times_minutes_over_forty() -> None:
    state = pr.build_states({"p1": base(minutes=30.0)}, {}, {"AAA": games()}, never, 2)["p1"]
    assert state.points_per_game == pytest.approx(20.0 * 30.0 / 40.0)


# --------------------------------------------------------------------------- the demo league


@pytest.fixture()
def ctx(demo_session):
    return build_context(demo_session, None, now=NOW)


def test_loaded_bases_are_the_imported_rows(demo_session, ctx) -> None:
    bases = pr.load_bases(ctx)
    rows = demo_session.execute(select(ElPlayerRate)).scalars().all()
    assert len(bases) == len(rows) > 300
    row = rows[0]
    loaded = bases[row.person_code]
    assert loaded.as_of_round == 2 and loaded.club_code == row.club_code
    assert loaded.rates["pts40"] == row.pts40
    assert loaded.prior_minutes == row.prior_minutes


def test_load_lines_reads_only_games_after_the_round_and_only_played_lines(
    demo_session, ctx
) -> None:
    lines, club_games = pr.load_lines(ctx, 2)
    rounds = {entry.round for entries in lines.values() for entry in entries}
    assert rounds == {3, 4}
    assert all(g.round in (3, 4) for gs in club_games.values() for g in gs)
    assert all(len([g for g in gs if g.round == 3]) == 1 for gs in club_games.values())
    played = (
        demo_session.execute(
            select(ElPlayerGame)
            .join(ElGame, ElGame.game_id == ElPlayerGame.game_id)
            .where(ElGame.round_number > 2, ElPlayerGame.participation == "played")
        )
        .scalars()
        .all()
    )
    assert sum(len(v) for v in lines.values()) == len(played)


def test_the_model_state_at_the_base_round_is_the_import_unchanged(demo_session, ctx) -> None:
    model = get_model(ctx)
    states = model.players[2]
    for row in demo_session.execute(select(ElPlayerRate)).scalars():
        state = states[row.person_code]
        assert state.minutes == row.proj_minutes
        assert state.rates["pts40"] == row.pts40
        assert state.rates["ft_pct"] == row.ft_pct


def test_a_demo_players_round_four_rate_is_the_hand_computed_blend(demo_session, ctx) -> None:
    """Recompute one established starter's points rate from the store, rounds 3 and 4 only."""
    model = get_model(ctx)
    row = (
        demo_session.execute(
            select(ElPlayerRate)
            .where(ElPlayerRate.prior_minutes == 400.0)
            .order_by(ElPlayerRate.person_code)
        )
        .scalars()
        .first()
    )
    lines = demo_session.execute(
        select(ElPlayerGame, ElGame)
        .join(ElGame, ElGame.game_id == ElPlayerGame.game_id)
        .where(
            ElPlayerGame.person_code == row.person_code,
            ElPlayerGame.participation == "played",
            ElGame.round_number > 2,
        )
    ).all()
    assert lines, "the first demo player with a 400-minute prior played in rounds 3 and 4"
    pts = sum(pg.pts for pg, _ in lines)
    minutes = sum(pg.seconds_played for pg, _ in lines) / 60.0
    expected = (row.pts40 * 400.0 + 40.0 * pts) / (400.0 + minutes)
    assert model.players[4][row.person_code].rates["pts40"] == pytest.approx(expected)
    # And round 3's state used only the round-3 game.
    third = [(pg, g) for pg, g in lines if g.round_number == 3]
    pts3 = sum(pg.pts for pg, _ in third)
    min3 = sum(pg.seconds_played for pg, _ in third) / 60.0
    assert model.players[3][row.person_code].rates["pts40"] == pytest.approx(
        (row.pts40 * 400.0 + 40.0 * pts3) / (400.0 + min3)
    )


def test_squads_in_the_model_add_up_to_two_hundred_minutes_after_games(demo_session, ctx) -> None:
    model = get_model(ctx)
    by_club: dict[str, float] = {}
    for state in model.players[4].values():
        if state.minutes is not None:
            by_club[state.club_code] = by_club.get(state.club_code, 0.0) + state.minutes
    assert len(by_club) == 20
    for club, total in by_club.items():
        assert total == pytest.approx(200.0, abs=1e-6), club
    assert all(s.minutes <= 32.0 + 1e-9 for s in model.players[4].values() if s.minutes is not None)
