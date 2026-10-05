"""The nine-category fantasy valuation, the trade analyzer and the draft board.

Built from the user's ``Fantasy NBA 2026-27 Toolkit`` workbook. The tests that matter are the
ones guarding decisions that would look fine on screen while being wrong:

* **The percentage impact sums to zero over the pool.** If the baseline and the numerator ever
  stop being the same quantity, every z shifts by a constant and the board still looks
  plausible — it just ranks volume shooters wrongly. :func:`test_percentage_impacts_sum_to_zero`
  is the guard, and ``value_pool`` asserts it at runtime too.
* **Volume weighting.** A 90% free-throw shooter on one attempt must not outrank a 78% shooter
  on nine. This is the single thing a naive z of the percentage gets wrong.
* **The sensitivity sweep must actually move.** Its first version scaled every player's minutes
  by the same factor, which cancels exactly under standardisation and produced a range of 0.02
  across five scenarios. A range that cannot move is worse than no range.
* **The module must never touch the count machinery.** ``combo`` and ``nb_interval`` clamp at
  zero and return integers; a signed z-delta routed through them would come back silently wrong.
"""
from __future__ import annotations

import math
from typing import Sequence

import pytest

from nbastats import fantasy as F
from nbastats import projection_constants as C


# --------------------------------------------------------------------------- fixtures


def line(player_id: int, name: str = "", **stats: float) -> F.SeasonLine:
    """A season line with a plausible baseline, overridden per test."""
    base = dict(
        games_played=70,
        minutes_per_game=30.0,
        pts=15.0, fg3m=1.5, reb=5.0, ast=3.0, stl=1.0, blk=0.5, tov=2.0,
        fgm=5.5, fga=12.0, ftm=3.0, fta=4.0,
    )
    base.update(stats)
    return F.SeasonLine(player_id=player_id, name=name or f"Player {player_id}", **base)


@pytest.fixture()
def population() -> list[F.SeasonLine]:
    """Sixty players spread over a realistic range, deterministic and hand-built."""
    out: list[F.SeasonLine] = []
    for i in range(60):
        t = i / 59.0
        out.append(
            line(
                1000 + i,
                f"Player {i:02d}",
                pts=8.0 + 20.0 * t,
                fg3m=0.4 + 2.6 * t,
                reb=2.5 + 8.0 * (1.0 - t) ** 0.5,
                ast=1.0 + 7.0 * t,
                stl=0.4 + 1.4 * t,
                blk=0.1 + 1.9 * (1.0 - t),
                tov=1.0 + 2.5 * t,
                fgm=3.0 + 7.0 * t,
                fga=7.0 + 13.0 * t,
                ftm=1.2 + 5.0 * t,
                fta=1.6 + 6.0 * t,
            )
        )
    return out


@pytest.fixture()
def pool(population: list[F.SeasonLine]) -> F.ValuedPool:
    return F.value_pool(population, pool_size=40, replacement_depth=8)


# --------------------------------------------------------------------------- the pool


def test_the_nine_categories_are_the_workbook_s_nine() -> None:
    assert F.CATEGORIES == ("pts", "fg3m", "reb", "ast", "stl", "blk", "tov", "fg_pct", "ft_pct")
    assert set(F.COUNTING_CATEGORIES) | set(F.PERCENTAGE_CATEGORIES) == set(F.CATEGORIES)
    assert F.NEGATIVE_CATEGORIES == {"tov"}


def test_percentage_impacts_sum_to_zero_over_the_pool(pool: F.ValuedPool) -> None:
    """The identity the percentage baseline exists to satisfy.

    ``impact = attempts x (shrunk rate - pool rate)`` sums to zero only when the pool rate is
    the attempts-weighted aggregate of the same shrunk rates. Computing it from raw makes and
    attempts instead leaves every player looking slightly below average, because shrinkage
    pulls each rate toward a league mean that sits below the pool's.
    """
    for category in F.PERCENTAGE_CATEGORIES:
        total = sum(pool.players[pid].categories[category].standardised for pid in pool.pool_ids)
        assert abs(total) < 1e-6, f"{category} impacts sum to {total:+.9f}"
        mean, _sd = pool.moments[category]
        assert abs(mean) < 1e-9


def test_every_z_is_centred_and_scaled_on_the_pool(pool: F.ValuedPool) -> None:
    for category in F.CATEGORIES:
        zs = [pool.players[pid].z(category) for pid in pool.pool_ids]
        assert abs(sum(zs) / len(zs)) < 1e-9, category
        sd = math.sqrt(sum(z * z for z in zs) / len(zs))
        assert sd == pytest.approx(1.0, abs=1e-9), category


def test_turnovers_are_sign_flipped(population: list[F.SeasonLine]) -> None:
    """Fewer is better, so the player with the most turnovers must have the lowest z."""
    pool = F.value_pool(population, pool_size=40)
    worst = max(population, key=lambda ln: ln.tov)
    best = min(population, key=lambda ln: ln.tov)
    assert pool.get(worst.player_id).z("tov") < pool.get(best.player_id).z("tov")
    assert pool.get(worst.player_id).z("tov") < 0


def test_the_pool_is_the_top_n_and_replacement_sits_just_below(pool: F.ValuedPool) -> None:
    assert len(pool.pool_ids) == 40
    assert len(pool.replacement_ids) == 8
    ranks = [pool.players[pid].baseline_rank for pid in pool.pool_ids]
    assert ranks == sorted(ranks) and max(ranks) == 40
    replacement_ranks = [pool.players[pid].baseline_rank for pid in pool.replacement_ids]
    assert replacement_ranks == list(range(41, 49))
    assert pool.replacement() < 0, "a waiver body is below pool average by construction"


def test_the_population_sd_is_population_not_sample(population: list[F.SeasonLine]) -> None:
    """The pool is the population of interest, not a sample drawn from a bigger one."""
    values = [1.0, 2.0, 3.0, 4.0]
    assert F._population_sd(values) == pytest.approx(math.sqrt(1.25))


# --------------------------------------------------------------------------- percentages


def test_volume_decides_how_much_a_percentage_is_worth() -> None:
    """The one thing a naive z of the raw percentage gets wrong.

    A 90% free-throw shooter taking 1.5 a game barely moves the category; a 78% shooter taking
    nine moves it a lot, and downward. Ranking on the percentage alone reverses them.
    """
    sniper = line(1, "Low volume 90%", ftm=1.35, fta=1.5)
    hacker = line(2, "High volume 78%", ftm=7.0, fta=9.0)
    # The rest of the pool shoots about 85%, so 78% really is below average here. An earlier
    # version of this fixture put them at 75%, which quietly made the "bad" shooter the good
    # one and tested nothing.
    others = [
        line(10 + i, fta=4.0 + 0.1 * i, ftm=0.85 * (4.0 + 0.1 * i) + 0.01 * ((i % 7) - 3))
        for i in range(30)
    ]
    pool = F.value_pool([sniper, hacker, *others], pool_size=32)
    assert 0.80 < pool.pool_pct["ft_pct"] < 0.90, pool.pool_pct["ft_pct"]

    sniper_ft = pool.get(1).categories["ft_pct"]
    hacker_ft = pool.get(2).categories["ft_pct"]

    assert sniper_ft.value > hacker_ft.value, "the sniper really is the better shooter"
    assert abs(sniper_ft.standardised) < abs(hacker_ft.standardised), (
        "yet he moves the category less, because he barely shoots"
    )
    assert hacker_ft.standardised < 0 < sniper_ft.standardised
    assert hacker_ft.z < sniper_ft.z


def test_a_percentage_shrinks_toward_the_league_by_attempts() -> None:
    """Paper Table B.5: FT% settles in 25 attempts, FG% in 129 — the first production use."""
    assert C.pct_stabilisation_attempts("ft_pct") == 25.0
    assert C.pct_stabilisation_attempts("fg_pct") == 129.0

    thin = F.shrunk_pct("ft_pct", makes=5, attempts=5, league_value=0.78)
    thick = F.shrunk_pct("ft_pct", makes=500, attempts=500, league_value=0.78)
    assert thin.value < thick.value, "less evidence means more league prior"
    assert thin.weight == pytest.approx(5 / 30)
    assert thick.weight == pytest.approx(500 / 525)
    assert thick.value == pytest.approx(1.0, abs=0.02)


def test_a_player_with_no_attempts_is_exactly_the_league_rate() -> None:
    shrunk = F.shrunk_pct("fg_pct", makes=0, attempts=0, league_value=0.461)
    assert shrunk.value == pytest.approx(0.461)
    assert shrunk.weight == 0.0


def test_the_pool_rate_is_an_aggregate_ratio_not_a_mean_of_ratios() -> None:
    """A twelve-attempt shooter should not get the same say as a two-attempt one."""
    heavy = line(1, fgm=10.0, fga=20.0)     # .500 on 20
    light = line(2, fgm=1.0, fga=2.0)       # .500 on 2
    odd = line(3, fgm=0.0, fga=2.0)         # .000 on 2
    pool = F.value_pool([heavy, light, odd], pool_size=3)
    aggregate = (10.0 + 1.0 + 0.0) / (20.0 + 2.0 + 2.0)
    # The pool rate is built from shrunk rates, so it will not equal the raw aggregate — but it
    # must sit far closer to it than to the unweighted mean of .500/.500/.000 = .333.
    assert abs(pool.pool_pct["fg_pct"] - aggregate) < abs(pool.pool_pct["fg_pct"] - 1 / 3)


# --------------------------------------------------------------------------- points


def test_the_two_points_systems_are_the_workbook_s(pool: F.ValuedPool) -> None:
    assert F.ESPN_POINTS["stl"] == 4.0 and F.ESPN_POINTS["fga"] == -1.0
    assert F.YAHOO_POINTS["reb"] == 1.2 and F.YAHOO_POINTS["ast"] == 1.5
    assert "fg3m" not in F.YAHOO_POINTS, "Yahoo's default does not score threes separately"

    one = line(1, pts=20.0, fg3m=2.0, reb=6.0, ast=4.0, stl=1.5, blk=1.0, tov=3.0,
               fgm=7.0, fga=15.0, ftm=4.0, fta=5.0)
    espn = (20 * 1 + 2 * 1 + 7 * 2 + 15 * -1 + 4 * 1 + 5 * -1 + 6 * 1 + 4 * 2
            + 1.5 * 4 + 1 * 4 + 3 * -2)
    yahoo = 20 * 1 + 6 * 1.2 + 4 * 1.5 + 1.5 * 3 + 1 * 3 + 3 * -1
    assert F.fantasy_points(one, F.ESPN_POINTS) == pytest.approx(espn)
    assert F.fantasy_points(one, F.YAHOO_POINTS) == pytest.approx(yahoo)


# --------------------------------------------------------------------------- weights


def test_a_punt_zeroes_a_category_without_touching_the_z(pool: F.ValuedPool) -> None:
    """Weights apply at read time, so two managers see the same per-category numbers."""
    someone = pool.ranked()[5]
    balanced = someone.total_z()
    punt_ft = someone.total_z({**{c: 1.0 for c in F.CATEGORIES}, "ft_pct": 0.0})
    assert punt_ft == pytest.approx(balanced - someone.z("ft_pct"))
    assert someone.z("ft_pct") == pool.get(someone.player_id).z("ft_pct"), "z is unchanged"


def test_score_is_total_z_over_the_weight_and_they_must_not_be_confused(
    pool: F.ValuedPool,
) -> None:
    """A verdict band written for one is off by a factor of nine against the other."""
    someone = pool.ranked()[3]
    assert someone.score() == pytest.approx(someone.total_z() / 9.0)
    punt = {**{c: 1.0 for c in F.CATEGORIES}, "blk": 0.0, "ft_pct": 0.0}
    assert someone.score(punt) == pytest.approx(someone.total_z(punt) / 7.0)


# --------------------------------------------------------------------------- trades


def test_a_trade_reports_give_get_and_change_per_category(pool: F.ValuedPool) -> None:
    ranked = pool.ranked()
    give, get = [ranked[10].player_id], [ranked[2].player_id]
    result = F.evaluate_trade(pool, give, get)

    assert set(result.categories) == set(F.CATEGORIES)
    for category, (gave, got, change, _net) in result.categories.items():
        assert gave == pytest.approx(pool.get(give[0]).z(category))
        assert got == pytest.approx(pool.get(get[0]).z(category))
        assert change == pytest.approx(got - gave)
    assert result.change_z > 0, "trading up should read as a gain"
    assert result.roster_adjustment == 0.0, "one for one frees no roster spot"
    assert result.net_z == pytest.approx(result.change_z)


def test_giving_more_players_than_you_get_costs_the_freed_spots(pool: F.ValuedPool) -> None:
    """The adjustment most trade tools omit, and the reason a 2-for-1 is not free.

    Two players out, one in, leaves a hole that gets filled from waivers — below pool average
    by construction, so the adjustment is negative.
    """
    ranked = pool.ranked()
    result = F.evaluate_trade(
        pool, [ranked[8].player_id, ranked[9].player_id], [ranked[0].player_id]
    )
    assert result.roster_adjustment == pytest.approx(pool.replacement())
    assert result.roster_adjustment < 0
    assert result.net_z == pytest.approx(result.change_z + result.roster_adjustment)
    assert any("roster" in note for note in result.notes)


def test_the_roster_adjustment_is_its_own_number_not_folded_in(pool: F.ValuedPool) -> None:
    """It routinely dominates an uneven trade, so it has to be separable on screen."""
    ranked = pool.ranked()
    result = F.evaluate_trade(
        pool, [ranked[5].player_id, ranked[6].player_id, ranked[7].player_id],
        [ranked[0].player_id],
    )
    assert result.roster_adjustment == pytest.approx(2 * pool.replacement())
    assert result.change_z != pytest.approx(result.net_z)


def test_a_trade_is_antisymmetric(pool: F.ValuedPool) -> None:
    """Both managers must see the same trade, with the sign reversed. Even numbers only —
    an uneven trade is genuinely not antisymmetric, because both sides free a different
    number of roster spots and the replacement term does not cancel."""
    ranked = pool.ranked()
    a, b = [ranked[4].player_id], [ranked[11].player_id]
    forward = F.evaluate_trade(pool, a, b)
    backward = F.evaluate_trade(pool, b, a)
    assert forward.net_z == pytest.approx(-backward.net_z)
    for category in F.CATEGORIES:
        assert forward.categories[category][2] == pytest.approx(-backward.categories[category][2])


def test_an_unknown_player_is_left_out_and_said_out_loud(pool: F.ValuedPool) -> None:
    ranked = pool.ranked()
    result = F.evaluate_trade(pool, [ranked[0].player_id], [999_999])
    assert result.get.count == 0
    assert result.get.missing == ("999999",)
    assert any("Not valued" in note for note in result.notes)


def test_the_verdict_bands_are_reported_with_the_scale_they_live_on(pool: F.ValuedPool) -> None:
    """A band quoted without the pool's own spread is a number pretending to be a judgement."""
    ranked = pool.ranked()
    result = F.evaluate_trade(pool, [ranked[10].player_id], [ranked[9].player_id])
    assert result.pool_spread == pytest.approx(pool.spread())
    assert result.pool_spread > 0
    assert any("spread" in note for note in result.notes)


def test_verdict_wording_follows_the_bands() -> None:
    bands = F.VerdictBands(fair_z=0.75, clear_z=2.0)
    assert bands.verdict(0.2, scale="categories") == "fair"
    assert bands.verdict(-0.2, scale="categories") == "fair"
    assert bands.verdict(1.0, scale="categories") == "slight win"
    assert bands.verdict(-1.0, scale="categories") == "slight loss"
    assert bands.verdict(3.0, scale="categories") == "clear win"
    assert bands.verdict(-3.0, scale="categories") == "clear loss"
    assert bands.verdict(2.0, scale="points") == "fair", "points use their own band"
    assert bands.verdict(9.0, scale="points") == "clear win"


def test_bands_are_configurable_because_they_are_league_specific() -> None:
    tight = F.VerdictBands(fair_z=0.2, clear_z=0.5)
    assert tight.verdict(0.3, scale="categories") == "slight win"
    assert F.DEFAULT_BANDS.verdict(0.3, scale="categories") == "fair"


# --------------------------------------------------------------------------- sensitivity


def test_the_sensitivity_sweep_actually_moves(
    population: list[F.SeasonLine], pool: F.ValuedPool
) -> None:
    """Its first version swept five scenarios to a range of 0.02.

    Scaling every player's minutes by the same factor cancels exactly under standardisation, so
    a whole-league scenario cannot move a z-score. The scenarios have to be asymmetric.
    """
    ranked = pool.ranked()
    give, get = [ranked[12].player_id], [ranked[10].player_id]
    sweep = F.trade_sensitivity(population, give, get, pool_size=40, replacement_depth=8)
    assert sweep.width > 0.5, f"range of {sweep.width:.3f} is not a range"
    assert len(sweep.by_scenario) == len(F.STANDARD_SCENARIOS)
    assert sweep.low_scenario != sweep.high_scenario


def test_availability_moves_the_answer_the_way_a_reader_expects(
    population: list[F.SeasonLine], pool: F.ValuedPool
) -> None:
    """Missing games must make the player you GET worth less and the one you GIVE cost less.

    In per-game units a line is unchanged by missing twenty games, so availability is a weight
    on what you receive rather than a change to the line. The first version scaled the line by
    games, which only perturbed the percentage shrinkage exposure and came out backwards.
    """
    ranked = pool.ranked()
    give, get = [ranked[12].player_id], [ranked[4].player_id]
    sweep = F.trade_sensitivity(population, give, get, pool_size=40, replacement_depth=8)

    assert sweep.by_scenario["get_injured"] < sweep.by_scenario["base"]
    assert sweep.by_scenario["get_role_loss"] < sweep.by_scenario["base"]
    assert sweep.by_scenario["give_injured"] > sweep.by_scenario["base"]
    assert sweep.by_scenario["give_breakout"] < sweep.by_scenario["base"]


def test_a_sweep_that_changes_the_sign_says_so(
    population: list[F.SeasonLine], pool: F.ValuedPool
) -> None:
    """The most useful thing the range can report: this one is a coin flip on role."""
    ranked = pool.ranked()
    nearly_even = F.trade_sensitivity(
        population, [ranked[20].player_id], [ranked[21].player_id],
        pool_size=40, replacement_depth=8,
    )
    assert nearly_even.flips is (nearly_even.low < 0 < nearly_even.high)
    lopsided = F.trade_sensitivity(
        population, [ranked[39].player_id], [ranked[0].player_id],
        pool_size=40, replacement_depth=8,
    )
    assert lopsided.low > 0 and not lopsided.flips


def test_a_scenario_targets_one_side_only(population: list[F.SeasonLine]) -> None:
    assert {s.applies_to for s in F.STANDARD_SCENARIOS} == {"all", "get", "give"}
    base = next(s for s in F.STANDARD_SCENARIOS if s.key == "base")
    assert base.minutes == 1.0 and base.games is None


# --------------------------------------------------------------------------- draft


def test_the_board_ranks_undrafted_players_by_value(pool: F.ValuedPool) -> None:
    board = F.build_draft_board(pool, teams=12, roster_spots=13, limit=10)
    assert len(board.picks) == 10
    suggestions = [p.suggestion for p in board.picks]
    assert suggestions == sorted(suggestions, reverse=True)
    assert board.picks[0].reason.startswith("Best available")
    assert all(p.value_over_replacement > 0 for p in board.picks[:5])


def test_drafted_players_leave_the_board(pool: F.ValuedPool) -> None:
    ranked = pool.ranked()
    taken = {ranked[0].player_id: "Team 2", ranked[1].player_id: "My Team"}
    board = F.build_draft_board(pool, drafted=taken, my_team="My Team", teams=12, limit=5)
    shown = {p.valuation.player_id for p in board.picks}
    assert not (shown & set(taken))
    assert board.next_overall == 3


def test_the_snake_order_turns_around(pool: F.ValuedPool) -> None:
    """Pick 13 of a 12-team draft belongs to whoever picked 12th."""
    assert F._snake_position(1, 12) == (1, 1)
    assert F._snake_position(12, 12) == (1, 12)
    assert F._snake_position(13, 12) == (2, 12)
    assert F._snake_position(24, 12) == (2, 1)
    assert F._snake_position(25, 12) == (3, 1)
    assert F._snake_position(1, 1) == (1, 1)


def test_need_only_applies_once_a_roster_has_a_shape(pool: F.ValuedPool) -> None:
    ranked = pool.ranked()
    thin = {ranked[0].player_id: "My Team"}
    board = F.build_draft_board(pool, drafted=thin, my_team="My Team", teams=12, limit=5)
    for pick in board.picks:
        assert pick.suggestion == pytest.approx(pick.valuation.total_z())
    assert any("three picks" in note for note in board.notes)


def test_need_tilts_the_board_but_never_dominates_it(pool: F.ValuedPool) -> None:
    """A board that chases categories over talent drafts badly."""
    ranked = pool.ranked()
    # Four blocks-heavy players, so the roster is starved of assists and threes.
    big_men = sorted(pool.players.values(), key=lambda v: -v.z("blk"))[:4]
    drafted = {v.player_id: "My Team" for v in big_men}
    board = F.build_draft_board(pool, drafted=drafted, my_team="My Team", teams=12, limit=12)

    assert board.weakest, "a lopsided roster must report weak categories"
    for pick in board.picks:
        drift = abs(pick.suggestion - pick.valuation.total_z())
        assert drift <= F.NEED_WEIGHT * 3.0 + 1e-9, "need moved a suggestion too far"
    assert any("Weakest categories" in note for note in board.notes)


def test_turnovers_are_described_in_the_direction_they_are_scored(pool: F.ValuedPool) -> None:
    """A high turnover z means FEW turnovers. "Carries turnovers" reads as praise for a flaw."""
    assert F._strengths_phrase(["tov"]) == "protects the ball"
    assert F._strengths_phrase(["pts", "tov"]) == "carries points; protects the ball"
    assert F._weakness_phrase("tov") == "turns it over"
    assert F._weakness_phrase("reb") == "costs you rebounds"
    for pick in F.build_draft_board(pool, teams=12, limit=20).picks:
        assert "carries turnovers" not in pick.reason


# --------------------------------------------------------------------------- policy


def test_the_module_never_touches_the_count_machinery() -> None:
    """``combo`` and ``nb_interval`` are COUNT objects: they clamp at zero and return integers.

    A signed z-delta routed through either comes back silently wrong rather than raising, so
    the guard is a grep rather than a try/except. See the module docstring on why the trade
    analyzer reports a scenario range instead of a predictive interval.
    """
    import pathlib

    source = pathlib.Path(F.__file__).read_text(encoding="utf-8")
    for forbidden in ("nb_interval", "combo(", "NegativeBinomial", "nb_pmf"):
        assert forbidden not in source, f"fantasy.py reaches for {forbidden}"
    assert "import" in source and "from .projection import" not in source


def test_nothing_here_translates_to_a_market() -> None:
    """docs/PROJECTION.md section 6 and docs/LEGAL.md: no odds, no price, no stake."""
    import pathlib

    source = pathlib.Path(F.__file__).read_text(encoding="utf-8").lower()
    for word in ("odds", "vig", "juice", "kelly", "wager", "sportsbook", "payout", "stake"):
        assert word not in source, f"fantasy.py mentions {word}"


# --------------------------------------------------------------------------- the board payload
#
# The board is a table whose columns ship as data. These guard the three properties that would
# be invisible on screen if they broke: the value column agreeing with the rank beside it, the
# column descriptors staying out of MetricDescriptor's way, and a punt greying the right cells.


@pytest.fixture()
def board_payload(seeded_db, monkeypatch):
    """The resolved ``fantasy_draft_board`` payload from the seeded league."""
    from nbastats import catalog
    from nbastats.widgets import RESOLVERS, ResolveContext

    def build(**overrides):
        ctx = ResolveContext.from_request(seeded_db, None, request_id="test")
        config, errors = catalog.validate_widget_config(
            "fantasy_draft_board",
            {**catalog.widget_config_defaults("fantasy_draft_board"), **overrides},
        )
        assert not errors, errors
        payload, _availability, _notes = RESOLVERS["fantasy_draft_board"](config, ctx)
        return payload

    return build


def test_the_board_is_a_table_of_columns_and_rows(board_payload) -> None:
    payload = board_payload(limit=6)
    assert payload["columns"] and payload["rows"]
    assert "picks" not in payload, "the card-shaped payload is gone, not shipped alongside"
    keys = {column["key"] for column in payload["columns"]}
    for row in payload["rows"]:
        # Every column must be answerable from the row, or a cell renders as a dash the server
        # could have filled. `team` is the one text value.
        assert keys - set(row["values"]) == set(), sorted(keys - set(row["values"]))


def test_a_column_descriptor_is_not_a_metric_descriptor(board_payload) -> None:
    """§2's marker: anything carrying these six keys together *is* a catalog entry.

    Nine of these columns are pool-relative z-scores and three are identity; none is in
    ``metrics.json``. Tripping the marker would fail the fixture contract test with a confusing
    "describes unknown metric 'z_pts'" rather than here.
    """
    marker = {"key", "name", "shortName", "category", "format", "availability"}
    for column in board_payload()["columns"]:
        assert not marker <= set(column), f"{column['key']} looks like a MetricDescriptor"


def test_the_value_column_is_monotonic_with_the_rank_beside_it(board_payload) -> None:
    """A table that says it is sorted and visibly is not.

    The board ranks on a weighted suggestion; the value column has to be computed with the same
    weights or a punt makes rank 1 show a lower number than rank 5.
    """
    for punts in ([], ["ft_pct"], ["ft_pct", "tov"]):
        payload = board_payload(limit=8, puntCategories=punts)
        scores = [row["values"]["score"] for row in payload["rows"]]
        assert scores == sorted(scores, reverse=True), f"punt={punts}: {scores}"
        ranks = [row["rank"] for row in payload["rows"]]
        assert ranks == sorted(ranks)


def test_a_punt_greys_the_z_column_and_leaves_production_alone(board_payload) -> None:
    """A punt zeroes a category's weight, not a player's rebounds.

    Greying the raw column too would hide a fact rather than a judgement — the reader still
    wants to see the nine rebounds, they just do not want them counted.
    """
    payload = board_payload(puntCategories=["ft_pct"])
    punted = {column["key"] for column in payload["columns"] if column["punted"]}
    assert punted == {"z_ft_pct"}
    formats = {column["key"]: column for column in payload["columns"]}
    assert formats["ft_pct"]["punted"] is False
    assert formats["fta"]["punted"] is False


def test_the_z_block_keeps_the_export_ordering(board_payload) -> None:
    """Raw runs PTS TPM REB AST; the z block runs zPTS zTPM zAST zREB.

    A quirk of the export this table is modelled on, reproduced so the two diff column by
    column. If it is ever "fixed", that has to be a decision rather than a drift.
    """
    payload = board_payload()
    impact = [c["label"] for c in payload["columns"] if c["group"] == "impact"]
    assert impact == ["zPTS", "zTPM", "zAST", "zREB", "zSTL", "zBLK", "zTOV", "zFG%", "zFT%"]
    production = [c["label"] for c in payload["columns"] if c["group"] == "production"]
    assert production[:4] == ["PTS", "TPM", "REB", "AST"]


def test_the_board_serves_no_contract_column_and_no_foreign_value(board_payload) -> None:
    """Two columns an export has that this project cannot honestly produce.

    Contract status is nowhere in the data model; a proprietary value is not reproducible from
    the nine z-scores printed beside it. `score` occupies that slot and is this engine's own.
    """
    payload = board_payload()
    keys = {column["key"] for column in payload["columns"]}
    assert "contract" not in keys
    assert "score" in keys
    value_column = next(c for c in payload["columns"] if c["key"] == "score")
    assert value_column["label"] == "Value"
    # And it really is our number: the weighted mean, not something imported.
    row = payload["rows"][0]
    assert row["values"]["score"] == pytest.approx(row["totalZ"] / 9.0, abs=0.01)


def test_minutes_are_labelled_for_what_they_are(board_payload) -> None:
    """The export calls this "Total Minutes" and the values are 5-37, i.e. per game."""
    payload = board_payload()
    column = next(c for c in payload["columns"] if c["key"] == "mpg")
    assert column["label"] == "MPG"
    for row in payload["rows"]:
        assert 0 <= row["values"]["mpg"] <= 48


def test_every_row_is_marked_estimated(board_payload) -> None:
    for row in board_payload()["rows"]:
        assert row["availability"] == "estimated"
