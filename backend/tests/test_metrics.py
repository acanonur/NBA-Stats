"""Tests for the advanced-statistics engine and the percentile helpers.

The suite is built around one worked example - a 29-point game inside a 110-point
team game - whose expected values are computed by hand in the comments and
asserted as literals, so a refactor that quietly changes a coefficient fails
here rather than in the app.

Four kinds of test:

* **Worked examples** for every formula the contract names, with the arithmetic
  spelled out.
* **Degenerate inputs** - zero attempts, missing minutes, zero team minutes,
  negative values - asserting ``None`` and never ``0``.
* **Properties** checked with plain loops over a grid (no hypothesis
  dependency): eFG% >= FG% whenever a three went in, TS% >= eFG% whenever the
  free throws are good enough, percentile monotonicity, and Dean Oliver's
  structural identity that individual possessions and points produced sum back
  to the team's.
* **Catalog drift**: every key in ``contracts/metrics.json`` must be resolvable
  by :func:`compute_metric`, and the engine may not know keys the catalog does
  not list.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from nbastats import metrics as M  # noqa: E402
from nbastats import percentiles as P  # noqa: E402

METRICS_JSON = REPO_ROOT / "contracts" / "metrics.json"


# --------------------------------------------------------------------------- #
# The worked example
#
# One player's game line, the team line it sits inside, and the opponent's line.
# The team and opponent lines are internally consistent:
#   team PTS     = 2*40 + 12 + 18 = 110
#   opponent PTS = 2*38 + 11 + 15 = 102
#   player PTS   = 2*10 +  4 +  5 =  29
# --------------------------------------------------------------------------- #

PLAYER = {
    "min": 36, "pts": 29, "fgm": 10, "fga": 20, "fg3m": 4, "fg3a": 9,
    "ftm": 5, "fta": 6, "oreb": 2, "dreb": 7, "reb": 9, "ast": 8,
    "stl": 2, "blk": 1, "tov": 3, "pf": 2, "season": "2025-26",
}
TEAM = {
    "min": 240, "pts": 110, "fgm": 40, "fga": 88, "fg3m": 12, "fg3a": 34,
    "ftm": 18, "fta": 22, "oreb": 10, "dreb": 33, "reb": 43, "ast": 25,
    "stl": 8, "blk": 5, "tov": 14, "pf": 19, "gp": 1, "season": "2025-26",
}
OPPONENT = {
    "min": 240, "pts": 102, "fgm": 38, "fga": 85, "fg3m": 11, "fg3a": 30,
    "ftm": 15, "fta": 20, "oreb": 9, "dreb": 34, "reb": 43, "ast": 22,
    "stl": 7, "blk": 4, "tov": 13, "pf": 20, "gp": 1, "season": "2025-26",
}

#: The same three lines scaled to a full 82-game season, plus the model-fitted
#: columns that no box score can produce. Used by the catalog-coverage test.
SEASON_PLAYER = {
    **{k: v * 82 for k, v in PLAYER.items() if k != "min" and isinstance(v, (int, float))},
    "min": 36 * 82,
    "gp": 82,
    "gs": 80,
    "plus_minus": 312,
    "per": 24.8,
    "ws": 11.4,
    "ows": 7.9,
    "dws": 3.5,
    "ws48": 0.187,
    "bpm": 7.6,
    "obpm": 5.4,
    "dbpm": 2.2,
    "season": "2025-26",
}
SEASON_TEAM = {
    **{k: v * 82 for k, v in TEAM.items()
       if k not in ("min", "gp") and isinstance(v, (int, float))},
    "min": 240 * 82,
    "gp": 82,
    "wins": 50,
    "losses": 32,
    "season": "2025-26",
}
SEASON_OPPONENT = {
    **{k: v * 82 for k, v in OPPONENT.items()
       if k not in ("min", "gp") and isinstance(v, (int, float))},
    "min": 240 * 82,
    "gp": 82,
    "season": "2025-26",
}


# --------------------------------------------------------------------------- #
# Worked examples
# --------------------------------------------------------------------------- #


def test_possessions_worked_example():
    # Team: 88 + 0.44*22 - 10 + 14 = 88 + 9.68 - 10 + 14 = 101.68
    assert M.possessions(88, 22, 10, 14) == pytest.approx(101.68)
    # Opponent: 85 + 0.44*20 - 9 + 13 = 85 + 8.8 - 9 + 13 = 97.8
    assert M.possessions(85, 20, 9, 13) == pytest.approx(97.8)


def test_true_shooting_pct_worked_example():
    # 29 / (2 * (20 + 0.44*6)) = 29 / (2 * 22.64) = 29 / 45.28 = 0.6404593...
    assert M.true_shooting_pct(29, 20, 6) == pytest.approx(0.64045936, rel=1e-7)


def test_effective_fg_pct_worked_example():
    # (10 + 0.5*4) / 20 = 12 / 20 = 0.60
    assert M.effective_fg_pct(10, 4, 20) == pytest.approx(0.60)


def test_usage_pct_worked_example():
    # 100 * ((20 + 0.44*6 + 3) * (240/5)) / (36 * (88 + 0.44*22 + 14))
    #   = 100 * (25.64 * 48) / (36 * 111.68)
    #   = 100 * 1230.72 / 4020.48 = 30.6112703%
    assert M.usage_pct(20, 6, 3, 36, 240, 88, 22, 14) == pytest.approx(30.6112703, rel=1e-7)


def test_assist_pct_worked_example():
    # 100 * 8 / (((36 / (240/5)) * 40) - 10) = 800 / ((0.75 * 40) - 10) = 800 / 20 = 40.0
    assert M.assist_pct(8, 36, 240, 40, 10) == pytest.approx(40.0)


def test_rebound_pct_worked_examples():
    # TRB%: 100 * (9 * 48) / (36 * (43 + 43)) = 43200 / 3096 = 13.9534884%
    assert M.rebound_pct(9, 36, 240, 43, 43) == pytest.approx(13.9534884, rel=1e-7)
    # ORB%: 100 * (2 * 48) / (36 * (10 + 34)) = 9600 / 1584 = 6.0606061%
    assert M.offensive_rebound_pct(2, 36, 240, 10, 34) == pytest.approx(6.0606061, rel=1e-7)
    # DRB%: 100 * (7 * 48) / (36 * (33 + 9)) = 33600 / 1512 = 22.2222222%
    assert M.defensive_rebound_pct(7, 36, 240, 33, 9) == pytest.approx(22.2222222, rel=1e-7)


def test_turnover_pct_worked_example():
    # 100 * 3 / (20 + 0.44*6 + 3) = 300 / 25.64 = 11.700468%
    assert M.turnover_pct(3, 20, 6) == pytest.approx(11.700468, rel=1e-7)


def test_steal_and_block_pct_worked_examples():
    # STL%: 100 * (2 * 48) / (36 * 97.8) = 9600 / 3520.8 = 2.7266530%
    assert M.steal_pct(2, 36, 240, 97.8) == pytest.approx(2.7266530, rel=1e-6)
    # BLK%: 100 * (1 * 48) / (36 * (85 - 30)) = 4800 / 1980 = 2.4242424%
    assert M.block_pct(1, 36, 240, 85, 30) == pytest.approx(2.4242424, rel=1e-7)


def test_pace_worked_example():
    # 48 * ((101.68 + 97.8) / (2 * (240/5))) = 48 * (199.48 / 96) = 99.74
    assert M.pace(101.68, 97.8, 240) == pytest.approx(99.74)


def test_game_score_worked_example():
    # 29 + 0.4*10 - 0.7*20 - 0.4*(6-5) + 0.7*2 + 0.3*7 + 2 + 0.7*8 + 0.7*1 - 0.4*2 - 3
    # = 29 + 4 - 14 - 0.4 + 1.4 + 2.1 + 2 + 5.6 + 0.7 - 0.8 - 3 = 26.6
    assert M.game_score(29, 10, 20, 6, 5, 2, 7, 2, 8, 1, 2, 3) == pytest.approx(26.6)


def test_fantasy_points_worked_example():
    # 29 + 1.2*9 + 1.5*8 + 3*2 + 3*1 - 3 = 29 + 10.8 + 12 + 6 + 3 - 3 = 57.8
    assert M.fantasy_points(29, 9, 8, 2, 1, 3) == pytest.approx(57.8)


def test_assist_ratio_worked_example():
    # 100 * 8 / (20 + 0.44*6 + 8 + 3) = 800 / 33.64 = 23.7812128
    assert M.assist_ratio(8, 20, 6, 3) == pytest.approx(23.7812128, rel=1e-7)


def test_vorp_basketball_reference_worked_example():
    """BPM +7.6 on 70% of team minutes across all 82 games -> 6.7."""
    team_minutes = 82 * 240
    value = M.vorp(bpm=7.6, minutes=0.70 * team_minutes, team_minutes=team_minutes, team_games=82)
    # (7.6 + 2.0) * 0.70 * (82/82) = 9.6 * 0.7 = 6.72
    assert value == pytest.approx(6.72)
    assert round(value, 1) == 6.7


def test_vorp_scales_with_games_and_minutes():
    team_minutes = 41 * 240
    # Half a season at the same rate is worth half the VORP.
    assert M.vorp(7.6, 0.70 * team_minutes, team_minutes, 41) == pytest.approx(3.36)
    # A replacement-level player is worth exactly zero by construction.
    assert M.vorp(-2.0, 0.70 * team_minutes, team_minutes, 41) == pytest.approx(0.0)


def test_shooting_rate_worked_examples():
    assert M.three_point_attempt_rate(9, 20) == pytest.approx(0.45)
    assert M.free_throw_rate(6, 20) == pytest.approx(0.30)
    assert M.points_per_shot(29, 20) == pytest.approx(1.45)


def test_net_rating_is_a_difference():
    assert M.net_rating(118.2, 110.4) == pytest.approx(7.8)
    assert M.net_rating(None, 110.4) is None
    assert M.net_rating(118.2, None) is None


# --------------------------------------------------------------------------- #
# Dean Oliver ORtg / DRtg
# --------------------------------------------------------------------------- #


def _ortg(player, team, opp_dreb=34):
    return M.offensive_rating(
        pts=player["pts"], fgm=player["fgm"], fga=player["fga"], fg3m=player["fg3m"],
        ftm=player["ftm"], fta=player["fta"], ast=player["ast"], oreb=player["oreb"],
        tov=player["tov"], minutes=player["min"],
        team_pts=team["pts"], team_fgm=team["fgm"], team_fga=team["fga"],
        team_fg3m=team["fg3m"], team_ftm=team["ftm"], team_fta=team["fta"],
        team_ast=team["ast"], team_oreb=team["oreb"], team_tov=team["tov"],
        team_minutes=team["min"], opp_dreb=opp_dreb,
    )


def test_offensive_rating_intermediates():
    """Each Oliver intermediate on the worked example, computed by hand."""
    # Team_ScoringPoss = 40 + (1 - (1 - 18/22)^2) * 22 * 0.4
    #                  = 40 + (1 - 0.1818182^2) * 8.8 = 40 + 0.9669421 * 8.8 = 48.509091
    assert M.team_scoring_possessions(40, 18, 22) == pytest.approx(48.5090909, rel=1e-7)
    # Team_ORB% = 10 / (10 + 34) = 0.2272727
    assert M.team_oreb_pct(10, 34) == pytest.approx(0.2272727, rel=1e-6)
    # Team_Play% = 48.509091 / (88 + 22*0.4 + 14) = 48.509091 / 110.8 = 0.4378076
    play = M.team_play_pct(48.5090909, 88, 22, 14)
    assert play == pytest.approx(0.4378076, rel=1e-6)
    # FT_Part = (1 - (1 - 5/6)^2) * 0.4 * 6 = (1 - 0.0277778) * 2.4 = 2.3333333
    assert M.ft_part(5, 6) == pytest.approx(2.3333333, rel=1e-7)
    # FTxPoss = (1 - 5/6)^2 * 0.4 * 6 = 0.0277778 * 2.4 = 0.0666667
    assert M.missed_ft_possessions(5, 6) == pytest.approx(0.0666667, rel=1e-6)
    # FGxPoss = (20 - 10) * (1 - 1.07 * 0.2272727) = 10 * 0.7568182 = 7.5681818
    assert M.missed_fg_possessions(20, 10, 0.2272727) == pytest.approx(7.5681818, rel=1e-6)
    # AST_Part = 0.5 * (((110 - 18) - (29 - 5)) / (2 * (88 - 20))) * 8
    #          = 0.5 * (68 / 136) * 8 = 2.0
    assert M.assist_part(8, 29, 5, 20, 110, 18, 88) == pytest.approx(2.0)


def test_offensive_rating_worked_example():
    """ORtg lands where a 29-on-20-shots line with 8 assists should: ~126."""
    value = _ortg(PLAYER, TEAM)
    assert value == pytest.approx(126.47, abs=0.01)


def test_defensive_rating_worked_example():
    team_poss = M.possessions(TEAM["fga"], TEAM["fta"], TEAM["oreb"], TEAM["tov"])
    value = M.defensive_rating(
        minutes=36, stl=2, blk=1, dreb=7, pf=2,
        team_minutes=240, team_stl=8, team_blk=5, team_dreb=33, team_pf=19,
        team_possessions=team_poss,
        opp_pts=102, opp_fgm=38, opp_fga=85, opp_ftm=15, opp_fta=20,
        opp_tov=13, opp_oreb=9,
    )
    # Team DRtg = 100 * 102 / 101.68 = 100.31; the individual estimate is shrunk
    # 80% toward it, and this line's steal/block/rebound profile pulls it down.
    assert M.team_defensive_rating(102, team_poss) == pytest.approx(100.3147, rel=1e-5)
    assert value == pytest.approx(99.29, abs=0.01)
    assert 80.0 < value < 130.0


def test_offensive_rating_needs_every_input():
    for missing in ("pts", "fgm", "fga", "fg3m", "ftm", "fta", "ast", "oreb", "tov", "min"):
        line = dict(PLAYER)
        line[missing] = None
        assert _ortg(line, TEAM) is None, f"ORtg should be None without {missing}"
    # ...and the opponent's defensive rebounds, which price an offensive board.
    assert _ortg(PLAYER, TEAM, opp_dreb=None) is None


def test_oliver_individual_possessions_sum_to_the_team():
    """Structural property: Oliver's construction is possession- and point-conserving.

    Five players whose box lines add up exactly to the team line must, between
    them, account for the team's possessions and the team's points. Any sign or
    coefficient error in the ORtg chain breaks this identity immediately.
    """
    players = [
        {"min": 48, "pts": 29, "fgm": 10, "fga": 20, "fg3m": 4, "ftm": 5, "fta": 6,
         "oreb": 2, "dreb": 7, "ast": 8, "tov": 3},
        {"min": 48, "pts": 24, "fgm": 9, "fga": 18, "fg3m": 2, "ftm": 4, "fta": 5,
         "oreb": 1, "dreb": 6, "ast": 6, "tov": 4},
        {"min": 48, "pts": 21, "fgm": 8, "fga": 17, "fg3m": 3, "ftm": 2, "fta": 3,
         "oreb": 2, "dreb": 8, "ast": 5, "tov": 2},
        {"min": 48, "pts": 19, "fgm": 7, "fga": 16, "fg3m": 2, "ftm": 3, "fta": 4,
         "oreb": 3, "dreb": 6, "ast": 4, "tov": 3},
        {"min": 48, "pts": 17, "fgm": 6, "fga": 17, "fg3m": 1, "ftm": 4, "fta": 4,
         "oreb": 2, "dreb": 6, "ast": 2, "tov": 2},
    ]
    columns = ["min", "pts", "fgm", "fga", "fg3m", "ftm", "fta", "oreb", "dreb", "ast", "tov"]
    team = {c: sum(p[c] for p in players) for c in columns}
    opp_dreb = 34

    tm_scoring_poss = M.team_scoring_possessions(team["fgm"], team["ftm"], team["fta"])
    tm_orb_pct = M.team_oreb_pct(team["oreb"], opp_dreb)
    play = M.team_play_pct(tm_scoring_poss, team["fga"], team["fta"], team["tov"])
    weight = M.team_oreb_weight(tm_orb_pct, play)

    total_poss = 0.0
    total_produced = 0.0
    for player in players:
        qast = M.q_assist(
            ast=player["ast"], fgm=player["fgm"], minutes=player["min"],
            team_ast=team["ast"], team_fgm=team["fgm"], team_minutes=team["min"],
        )
        scoring = M.scoring_possessions(
            M.fg_part(player["fgm"], player["fga"], player["pts"], player["ftm"], qast),
            M.assist_part(player["ast"], player["pts"], player["ftm"], player["fga"],
                          team["pts"], team["ftm"], team["fga"]),
            M.ft_part(player["ftm"], player["fta"]),
            M.oreb_part(player["oreb"], weight, play),
            team["oreb"], tm_scoring_poss, weight, play,
        )
        total = M.total_possessions(
            scoring,
            M.missed_fg_possessions(player["fga"], player["fgm"], tm_orb_pct),
            M.missed_ft_possessions(player["ftm"], player["fta"]),
            player["tov"],
        )
        produced = M.points_produced(
            pts=player["pts"], fgm=player["fgm"], fga=player["fga"], fg3m=player["fg3m"],
            ftm=player["ftm"], ast=player["ast"], oreb=player["oreb"], qast=qast,
            team_pts=team["pts"], team_fgm=team["fgm"], team_fga=team["fga"],
            team_fg3m=team["fg3m"], team_ftm=team["ftm"], team_oreb=team["oreb"],
            team_scoring_poss=tm_scoring_poss, orb_weight=weight, play_pct=play,
        )
        # Floor% is a probability, and every piece is a real number.
        assert 0.0 <= M.floor_pct(scoring, total) <= 1.0
        total_poss += total
        total_produced += produced

    # Oliver's team possession formula, the one the individual chain reconciles to.
    oliver_team_poss = (
        team["fga"] + 0.4 * team["fta"]
        - 1.07 * (team["oreb"] / (team["oreb"] + opp_dreb)) * (team["fga"] - team["fgm"])
        + team["tov"]
    )
    assert total_poss == pytest.approx(oliver_team_poss, rel=0.02)
    assert total_produced == pytest.approx(team["pts"], rel=0.02)


# --------------------------------------------------------------------------- #
# PIE and the four factors
# --------------------------------------------------------------------------- #


def test_pie_worked_example():
    """PIE numerator and denominator, both spelled out."""
    game_totals = {k: TEAM[k] + OPPONENT[k] for k in TEAM if k in OPPONENT and k != "season"}
    # Player: 29 + 10 + 5 - 20 - 6 + 7 + 0.5*2 + 8 + 2 + 0.5*1 - 2 - 3 = 31.5
    # Game:   212 + 78 + 33 - 173 - 42 + 67 + 0.5*19 + 47 + 15 + 0.5*9 - 39 - 27 = 185.0
    # PIE = 31.5 / 185.0 = 0.1702703
    assert M.pie(PLAYER, game_totals) == pytest.approx(31.5 / 185.0, rel=1e-9)
    assert M.pie(PLAYER, game_totals) == pytest.approx(0.1702703, rel=1e-6)


def test_pie_requires_both_lines_complete():
    game_totals = {k: TEAM[k] + OPPONENT[k] for k in TEAM if k in OPPONENT and k != "season"}
    incomplete = dict(PLAYER)
    incomplete["stl"] = None
    assert M.pie(incomplete, game_totals) is None
    assert M.pie(None, game_totals) is None
    assert M.pie(PLAYER, None) is None
    # An empty game cannot be a denominator.
    assert M.pie(PLAYER, {k: 0 for k in ("pts", "fgm", "ftm", "fga", "fta", "dreb",
                                         "oreb", "ast", "stl", "blk", "pf", "tov")}) is None


def test_four_factors_shape_weights_and_values():
    factors = M.four_factors(fgm=40, fg3m=12, fga=88, fta=22, tov=14, oreb=10, opp_dreb=34)
    assert [f.key for f in factors] == ["efg_pct", "tov_pct", "oreb_pct", "ftr"]
    assert [f.weight for f in factors] == [0.40, 0.25, 0.20, 0.15]
    assert sum(f.weight for f in factors) == pytest.approx(1.0)
    by_key = {f.key: f.value for f in factors}
    # eFG% = (40 + 6) / 88 = 0.5227273
    assert by_key["efg_pct"] == pytest.approx(46 / 88)
    # TOV% = 14 / (88 + 0.44*22 + 14) = 14 / 111.68 = 0.1253582 (a fraction, not 12.5)
    assert by_key["tov_pct"] == pytest.approx(14 / 111.68)
    # OREB% = 10 / (10 + 34) = 0.2272727
    assert by_key["oreb_pct"] == pytest.approx(10 / 44)
    # FTr = 22 / 88 = 0.25
    assert by_key["ftr"] == pytest.approx(0.25)
    for factor in factors:
        assert 0.0 <= factor.value <= 1.0


def test_four_factors_keeps_rows_for_missing_inputs():
    factors = M.four_factors(fgm=40, fg3m=None, fga=88, fta=22, tov=None, oreb=10, opp_dreb=34)
    by_key = {f.key: f.value for f in factors}
    assert len(factors) == 4
    assert by_key["efg_pct"] is None  # no 3PM: unknown, not zero
    assert by_key["tov_pct"] is None
    assert by_key["oreb_pct"] == pytest.approx(10 / 44)
    assert by_key["ftr"] == pytest.approx(0.25)


# --------------------------------------------------------------------------- #
# Degenerate inputs: missing data is None, never zero
# --------------------------------------------------------------------------- #


def test_zero_attempts_never_divide():
    assert M.effective_fg_pct(0, 0, 0) is None
    assert M.true_shooting_pct(0, 0, 0) is None
    assert M.three_point_attempt_rate(0, 0) is None
    assert M.free_throw_rate(0, 0) is None
    assert M.points_per_shot(0, 0) is None
    assert M.turnover_pct(0, 0, 0) is None
    assert M.assist_ratio(0, 0, 0, 0) is None
    # A player who attempted nothing but scored nothing still used no plays.
    assert M.usage_pct(0, 0, 0, 12, 240, 0, 0, 0) is None


def test_missing_inputs_return_none_not_zero():
    assert M.possessions(88, 22, 10, None) is None  # pre-1977-78: no individual TOV
    assert M.effective_fg_pct(10, None, 20) is None  # unknown 3PM is unknown
    assert M.true_shooting_pct(None, 20, 6) is None
    assert M.game_score(29, 10, 20, 6, 5, 2, 7, None, 8, 1, 2, 3) is None
    assert M.fantasy_points(29, 9, 8, None, 1, 3) is None
    assert M.steal_pct(None, 36, 240, 97.8) is None
    assert M.vorp(None, 100, 1000, 82) is None
    assert M.pace(101.68, None, 240) is None


def test_none_minutes_and_zero_team_minutes():
    assert M.usage_pct(20, 6, 3, None, 240, 88, 22, 14) is None
    assert M.usage_pct(20, 6, 3, 36, None, 88, 22, 14) is None
    assert M.usage_pct(20, 6, 3, 0, 240, 88, 22, 14) is None
    assert M.usage_pct(20, 6, 3, 36, 0, 88, 22, 14) is None
    assert M.assist_pct(8, None, 240, 40, 10) is None
    assert M.assist_pct(8, 36, 0, 40, 10) is None
    assert M.rebound_pct(9, 0, 240, 43, 43) is None
    assert M.rebound_pct(9, 36, 0, 43, 43) is None
    assert M.rebound_pct(9, 36, 240, 0, 0) is None
    assert M.steal_pct(2, 36, 240, 0) is None
    assert M.steal_pct(2, 36, 0, 97.8) is None
    assert M.block_pct(1, 36, 0, 85, 30) is None
    assert M.block_pct(1, 36, 240, 30, 30) is None  # every attempt was a three
    assert M.pace(101.68, 97.8, 0) is None
    assert M.vorp(7.6, 100, 0, 82) is None


def test_negative_minutes_are_not_a_rate():
    """A negative denominator is corrupt data, not a small denominator."""
    assert M.usage_pct(20, 6, 3, -36, 240, 88, 22, 14) is None
    assert M.assist_pct(8, -36, 240, 40, 10) is None
    assert M.rebound_pct(9, -36, 240, 43, 43) is None
    assert M.steal_pct(2, -36, 240, 97.8) is None
    assert M.block_pct(1, -36, 240, 85, 30) is None
    assert M.pace(101.68, 97.8, -240) is None
    # A corrupt team-minutes total is not a small denominator either: a zero
    # numerator must not be mistaken for a real 0% usage or rebound rate.
    assert M.usage_pct(20, 6, 3, 36, -240, 88, 22, 14) is None
    assert M.assist_pct(8, 36, -240, 40, 10) is None
    assert M.rebound_pct(9, 36, -240, 43, 43) is None
    assert M.steal_pct(2, 36, -240, 97.8) is None
    assert M.block_pct(1, 36, -240, 85, 30) is None


def test_negative_counting_stats_pass_through_the_arithmetic():
    """Negative *numerators* are arithmetic, not division hazards.

    Plus/minus is legitimately negative, and a stat correction can briefly make a
    season total negative; those must flow through rather than silently vanish.
    """
    assert M.game_score(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5) == pytest.approx(-5.0)
    assert M.net_rating(105.0, 118.0) == pytest.approx(-13.0)
    assert M.vorp(-6.0, 1000, 10000, 82) == pytest.approx(-0.4)


def test_non_numeric_and_boolean_inputs_are_missing():
    assert M.true_shooting_pct("not a number", 20, 6) is None
    assert M.game_score(29, 10, 20, 6, 5, 2, 7, True, 8, 1, 2, 3) is None
    assert M.true_shooting_pct(float("nan"), 20, 6) is None
    # Numeric strings from a CSV ingest are still numbers.
    assert M.true_shooting_pct("29", "20", "6") == pytest.approx(0.64045936, rel=1e-7)


# --------------------------------------------------------------------------- #
# Properties, checked with plain loops
# --------------------------------------------------------------------------- #


def test_property_efg_at_least_fg_pct_when_a_three_falls():
    for fga in range(1, 31):
        for fgm in range(0, fga + 1):
            for fg3m in range(0, fgm + 1):
                efg = M.effective_fg_pct(fgm, fg3m, fga)
                fg_pct = fgm / fga
                if fg3m > 0:
                    assert efg > fg_pct, (fgm, fg3m, fga)
                else:
                    assert efg == pytest.approx(fg_pct)


def test_property_ts_at_least_efg_with_a_reasonable_ft_pct():
    """TS% >= eFG% exactly when FT% >= 0.88 * eFG%.

    Derivation: with P2 = 2*FGM + 3PM, TS = (P2 + FTM) / (2*(FGA + 0.44*FTA)) and
    eFG = P2 / (2*FGA), so TS >= eFG  <=>  FGA*FTM >= 0.44*P2*FTA  <=>
    FT% >= 0.44 * (P2/FGA) = 0.88 * eFG%. The loop asserts both directions.
    """
    for fga in (8, 15, 22, 30):
        for fgm in range(1, fga + 1):
            for fg3m in (0, min(2, fgm), min(5, fgm)):
                for fta in (1, 4, 9):
                    for ft_pct in (0.30, 0.50, 0.70, 0.80, 0.90, 1.00):
                        ftm = ft_pct * fta
                        pts = 2 * fgm + fg3m + ftm
                        efg = M.effective_fg_pct(fgm, fg3m, fga)
                        ts = M.true_shooting_pct(pts, fga, fta)
                        if ft_pct >= 0.88 * efg:
                            assert ts >= efg - 1e-12, (fgm, fg3m, fga, fta, ft_pct)
                        else:
                            assert ts <= efg + 1e-12, (fgm, fg3m, fga, fta, ft_pct)


def test_property_usage_rises_with_shot_volume():
    previous = None
    for fga in range(5, 30):
        value = M.usage_pct(fga, 6, 3, 36, 240, 88, 22, 14)
        if previous is not None:
            assert value > previous
        previous = value


def test_property_rate_stats_stay_in_range_for_plausible_lines():
    for minutes in (12, 24, 36, 48):
        for reb in range(0, 20):
            value = M.rebound_pct(reb, minutes, 240, 43, 43)
            assert 0.0 <= value <= 100.0
        for tov in range(0, 10):
            value = M.turnover_pct(tov, 15, 5)
            assert 0.0 <= value <= 100.0


# --------------------------------------------------------------------------- #
# per_mode_convert
# --------------------------------------------------------------------------- #


def test_per_mode_convert_worked_examples():
    total, minutes, games = 1640.0, 2460.0, 82.0  # 20.0 a game on 30.0 minutes
    assert M.per_mode_convert(total, minutes, games, "Totals") == pytest.approx(1640.0)
    assert M.per_mode_convert(total, minutes, games, "PerGame") == pytest.approx(20.0)
    # Per36: 1640 * 36 / 2460 = 24.0
    assert M.per_mode_convert(total, minutes, games, "Per36") == pytest.approx(24.0)
    # Per100 with known possessions: 1640 * 100 / 4000 = 41.0
    assert M.per_mode_convert(
        total, minutes, games, "Per100", possessions_played=4000
    ) == pytest.approx(41.0)
    # Per100 from pace: poss = 100 * 2460/48 = 5125, so 1640 * 100 / 5125 = 32.0
    assert M.per_mode_convert(
        total, minutes, games, "Per100", team_pace=100
    ) == pytest.approx(32.0)


def test_per_mode_convert_round_trips():
    total, minutes, games = 1640.0, 2460.0, 82.0
    per_game = M.per_mode_convert(total, minutes, games, "PerGame")
    assert per_game * games == pytest.approx(total)
    per36 = M.per_mode_convert(total, minutes, games, "Per36")
    assert per36 * minutes / 36.0 == pytest.approx(total)
    per100 = M.per_mode_convert(total, minutes, games, "Per100", possessions_played=4000)
    assert per100 * 4000 / 100.0 == pytest.approx(total)
    # Totals is the identity, and every mode agrees with itself case-insensitively.
    assert M.per_mode_convert(total, minutes, games, "Totals") == total
    assert M.per_mode_convert(total, minutes, games, "per36") == pytest.approx(per36)
    assert M.per_mode_convert(total, minutes, games, "PERGAME") == pytest.approx(per_game)


def test_per_mode_convert_degenerate_inputs():
    assert M.per_mode_convert(None, 2460, 82, "PerGame") is None
    assert M.per_mode_convert(1640, 2460, 0, "PerGame") is None
    assert M.per_mode_convert(1640, 2460, None, "PerGame") is None
    assert M.per_mode_convert(1640, 0, 82, "Per36") is None
    assert M.per_mode_convert(1640, None, 82, "Per36") is None
    # Per100 without possessions or pace must not invent a denominator.
    assert M.per_mode_convert(1640, 2460, 82, "Per100") is None
    assert M.per_mode_convert(1640, None, 82, "Per100", team_pace=100) is None
    # Totals needs nothing but the value.
    assert M.per_mode_convert(1640, None, None, "Totals") == pytest.approx(1640.0)


def test_per_mode_convert_rejects_an_unknown_mode():
    with pytest.raises(ValueError):
        M.per_mode_convert(1640, 2460, 82, "Per48")


# --------------------------------------------------------------------------- #
# rolling_average
# --------------------------------------------------------------------------- #


def test_rolling_average_basic_window():
    assert M.rolling_average([1, 2, 3, 4, 5], 1) == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert M.rolling_average([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    assert M.rolling_average([1, 2, 3, 4, 5], 5) == [None, None, None, None, 3.0]


def test_rolling_average_window_larger_than_series():
    assert M.rolling_average([1, 2, 3], 4) == [None, None, None]
    assert M.rolling_average([], 5) == []
    assert M.rolling_average([], 1) == []


def test_rolling_average_leading_and_interior_nones():
    # Missing games are skipped inside the window, never counted as zero.
    assert M.rolling_average([None, None, 3, 4, 5], 3) == [None, None, 3.0, 3.5, 4.0]
    assert M.rolling_average([None, None, None, 4], 3) == [None, None, None, 4.0]
    assert M.rolling_average([2, None, 4], 3) == [None, None, 3.0]
    # A window with nothing in it stays unknown.
    assert M.rolling_average([None, None, None], 2) == [None, None, None]


def test_rolling_average_rejects_a_non_positive_window():
    with pytest.raises(ValueError):
        M.rolling_average([1, 2, 3], 0)
    with pytest.raises(ValueError):
        M.rolling_average([1, 2, 3], -2)


# --------------------------------------------------------------------------- #
# compute_metric: dispatch, units and era honesty
# --------------------------------------------------------------------------- #


def _metric_catalog():
    with METRICS_JSON.open(encoding="utf-8") as handle:
        return json.load(handle)


def test_contract_catalog_is_readable():
    catalog = _metric_catalog()
    assert catalog["schemaVersion"] == 1
    assert len(catalog["metrics"]) == 61


#: Keys resolved by reading the column straight off the row because no box score
#: can produce them: they are fitted against league-wide data during ingest.
KNOWN_DIRECT_COLUMNS = {
    "per", "ws", "ows", "dws", "ws48", "bpm", "obpm", "dbpm", "plus_minus",
}


def test_no_metric_key_is_unhandled():
    """The catalog and the engine may never drift apart, in either direction."""
    catalog_keys = {m["key"] for m in _metric_catalog()["metrics"]}
    unhandled = catalog_keys - M.SUPPORTED_METRIC_KEYS - KNOWN_DIRECT_COLUMNS
    assert unhandled == set(), f"metrics.json keys with no computer: {sorted(unhandled)}"
    unknown = M.SUPPORTED_METRIC_KEYS - catalog_keys
    assert unknown == set(), f"engine keys missing from metrics.json: {sorted(unknown)}"
    assert KNOWN_DIRECT_COLUMNS <= M.SUPPORTED_METRIC_KEYS
    assert KNOWN_DIRECT_COLUMNS == M.MODEL_FITTED_METRIC_KEYS


def test_every_catalog_metric_resolves_against_a_full_row():
    """With complete inputs, no metric in the catalog comes back unknown."""
    missing = []
    for descriptor in _metric_catalog()["metrics"]:
        key = descriptor["key"]
        if "player" in descriptor["scope"]:
            value = M.compute_metric(
                key, row=SEASON_PLAYER, team_row=SEASON_TEAM, opponent_row=SEASON_OPPONENT
            )
        else:
            value = M.compute_metric(key, row=SEASON_TEAM, opponent_row=SEASON_OPPONENT)
        if value is None:
            missing.append(key)
    assert missing == [], f"unresolved with full inputs: {missing}"


def test_percent_metrics_are_fractions_not_hundreds():
    """Every percent-formatted metric leaves compute_metric in [0, 1]."""
    catalog = _metric_catalog()
    percent_formats = {f["key"] for f in catalog["formats"] if f.get("suffix") == "%"}
    percent_keys = {m["key"] for m in catalog["metrics"] if m["format"] in percent_formats}
    assert percent_keys == set(M.PERCENT_METRICS), "PERCENT_METRICS drifted from the catalog"

    for descriptor in catalog["metrics"]:
        key = descriptor["key"]
        if key not in percent_keys:
            continue
        if "player" in descriptor["scope"]:
            value = M.compute_metric(
                key, row=SEASON_PLAYER, team_row=SEASON_TEAM, opponent_row=SEASON_OPPONENT
            )
        else:
            value = M.compute_metric(key, row=SEASON_TEAM, opponent_row=SEASON_OPPONENT)
        assert value is not None
        assert 0.0 <= value <= 1.0, f"{key} = {value} is not a fraction"


#: ``net_rtg``'s catalog domain (-25..25) is a display hint sized for *team*
#: net rating; an individual ORtg-minus-DRtg differential for a star line
#: legitimately exceeds it, so it is excluded from the range check below.
DOMAIN_CHECK_EXEMPT = {"net_rtg"}


def test_computed_values_land_inside_their_catalog_domains():
    """A unit error would put the value outside the range the catalog declares.

    ``domain`` drives chart axes in the client, so a metric that came back on the
    wrong scale (USG% as 30.6 rather than 0.306, pace per game rather than per
    48) would fall outside it immediately.
    """
    for descriptor in _metric_catalog()["metrics"]:
        key = descriptor["key"]
        domain = descriptor.get("domain")
        if not domain or key in DOMAIN_CHECK_EXEMPT:
            continue
        if "player" in descriptor["scope"]:
            value = M.compute_metric(
                key, row=SEASON_PLAYER, team_row=SEASON_TEAM, opponent_row=SEASON_OPPONENT
            )
        else:
            value = M.compute_metric(key, row=SEASON_TEAM, opponent_row=SEASON_OPPONENT)
        assert value is not None, key
        assert domain["min"] <= value <= domain["max"], f"{key} = {value} outside {domain}"


def test_compute_metric_matches_the_named_functions():
    kwargs = {"row": PLAYER, "team_row": TEAM, "opponent_row": OPPONENT}
    assert M.compute_metric("ts_pct", **kwargs) == pytest.approx(0.64045936, rel=1e-7)
    assert M.compute_metric("efg_pct", **kwargs) == pytest.approx(0.60)
    assert M.compute_metric("usg_pct", **kwargs) == pytest.approx(0.306112703, rel=1e-7)
    assert M.compute_metric("ast_pct", **kwargs) == pytest.approx(0.40)
    assert M.compute_metric("reb_pct", **kwargs) == pytest.approx(0.139534884, rel=1e-7)
    assert M.compute_metric("tov_pct", **kwargs) == pytest.approx(0.11700468, rel=1e-7)
    assert M.compute_metric("game_score", **kwargs) == pytest.approx(26.6)
    assert M.compute_metric("pace", **kwargs) == pytest.approx(99.74)
    assert M.compute_metric("fantasy_pts", **kwargs) == pytest.approx(57.8)
    assert M.compute_metric("ast_tov", **kwargs) == pytest.approx(8 / 3)
    assert M.compute_metric("off_rtg", **kwargs) == pytest.approx(126.47, abs=0.01)
    assert M.compute_metric("net_rtg", **kwargs) == pytest.approx(
        M.compute_metric("off_rtg", **kwargs) - M.compute_metric("def_rtg", **kwargs)
    )
    assert M.compute_metric("pie", **kwargs) == pytest.approx(31.5 / 185.0, rel=1e-9)


def test_compute_metric_reads_the_long_sql_column_vocabulary():
    """The repo's BigQuery-shaped column names resolve exactly like the short ones."""
    long_row = {
        "minutes_played": 36, "points": 29, "field_goals_made": 10,
        "field_goals_attempted": 20, "three_pointers_made": 4,
        "three_pointers_attempted": 9, "free_throws_made": 5,
        "free_throws_attempted": 6, "offensive_rebounds": 2,
        "defensive_rebounds": 7, "rebounds": 9, "assists": 8, "steals": 2,
        "blocks": 1, "turnovers": 3, "personal_fouls": 2, "season": "2025-26",
    }
    assert M.compute_metric("ts_pct", row=long_row) == pytest.approx(0.64045936, rel=1e-7)
    assert M.compute_metric("game_score", row=long_row) == pytest.approx(26.6)
    assert M.compute_metric("usg_pct", row=long_row, team_row=TEAM) == pytest.approx(
        0.306112703, rel=1e-7
    )


def test_compute_metric_reads_prefixed_team_and_opponent_columns():
    """A flattened game-log row carries its context as team_/opp_ prefixes."""
    flat = dict(PLAYER)
    flat.update({f"team_{k}": v for k, v in TEAM.items() if k != "season"})
    flat.update({f"opp_{k}": v for k, v in OPPONENT.items() if k != "season"})
    assert M.compute_metric("usg_pct", row=flat) == pytest.approx(0.306112703, rel=1e-7)
    assert M.compute_metric("reb_pct", row=flat) == pytest.approx(0.139534884, rel=1e-7)
    assert M.compute_metric("blk_pct", row=flat) == pytest.approx(0.024242424, rel=1e-7)


def test_compute_metric_unknown_key_is_none():
    assert M.compute_metric("not_a_metric", row=PLAYER) is None
    assert M.compute_metric("", row=PLAYER) is None


def test_compute_metric_falls_back_to_a_stored_column():
    """A precomputed advanced column is used when the components are absent."""
    sparse = {"min": 36, "ts_pct": 0.615, "usg_pct": 0.284, "bpm": 4.1}
    assert M.compute_metric("ts_pct", row=sparse) == pytest.approx(0.615)
    assert M.compute_metric("usg_pct", row=sparse) == pytest.approx(0.284)
    assert M.compute_metric("bpm", row=sparse) == pytest.approx(4.1)
    # Components win when both are available: this row's own line says 0.6405.
    both = dict(PLAYER)
    both["ts_pct"] = 0.999
    assert M.compute_metric("ts_pct", row=both) == pytest.approx(0.64045936, rel=1e-7)


def test_era_unavailable_stats_are_none_never_zero():
    """A 1961-62 line: no steals, blocks, turnovers or rebound split existed."""
    old = {
        "season": "1961-62", "min": 3882, "pts": 4029, "fgm": 1597, "fga": 3159,
        "ftm": 835, "fta": 1363, "reb": 2052, "ast": 192, "pf": 123, "gp": 80,
    }
    for key in ("stl", "blk", "tov", "oreb", "dreb", "fg3m", "fg3a"):
        assert M.compute_metric(key, row=old) is None, key
    for key in ("tov_pct", "usg_pct", "stl_pct", "blk_pct", "game_score", "pie",
                "fantasy_pts", "fg3a_rate", "ast_ratio", "ast_tov"):
        assert M.compute_metric(key, row=old, team_row=TEAM, opponent_row=OPPONENT) is None, key
    # Possessions need turnovers, which this era did not record for individuals.
    assert M.compute_metric("poss", row=old) is None
    # What the era *did* record still computes.
    assert M.compute_metric("pts", row=old) == 4029
    assert M.compute_metric("fg_pct", row=old) == pytest.approx(1597 / 3159)
    assert M.compute_metric("ts_pct", row=old) == pytest.approx(
        4029 / (2 * (3159 + 0.44 * 1363))
    )


def test_pre_three_point_era_efg_equals_fg_pct():
    """Before 1979-80 a missing 3PM is a certainty, not a gap - but only then."""
    old = {"season": "1961-62", "fgm": 1597, "fga": 3159, "pts": 4029, "fta": 1363}
    assert M.compute_metric("efg_pct", row=old) == pytest.approx(1597 / 3159)
    # The same row without a season cannot claim to know, and a modern row with a
    # missing 3PM is missing data.
    assert M.compute_metric("efg_pct", row={k: v for k, v in old.items() if k != "season"}) is None
    modern = dict(old)
    modern["season"] = "2025-26"
    assert M.compute_metric("efg_pct", row=modern) is None


def test_team_subject_resolves_without_a_team_row():
    """A team is its own context: opponent metrics and the record still work."""
    kwargs = {"row": SEASON_TEAM, "opponent_row": SEASON_OPPONENT}
    assert M.compute_metric("win_pct", **kwargs) == pytest.approx(50 / 82)
    assert M.compute_metric("opp_efg_pct", **kwargs) == pytest.approx((38 + 5.5) / 85)
    assert M.compute_metric("opp_ftr", **kwargs) == pytest.approx(20 / 85)
    assert M.compute_metric("opp_oreb_pct", **kwargs) == pytest.approx(9 / (9 + 33))
    assert M.compute_metric("opp_tov_pct", **kwargs) == pytest.approx(13 / (85 + 0.44 * 20 + 13))
    # A team's ORtg is simply its own points per 100 possessions.
    assert M.compute_metric("off_rtg", **kwargs) == pytest.approx(
        100 * 110 / (88 + 0.44 * 22 - 10 + 14), rel=1e-9
    )


# --------------------------------------------------------------------------- #
# percentiles
# --------------------------------------------------------------------------- #


def test_rank_and_percentile_dense_ranks_with_ties():
    values = {"a": 30.0, "b": 30.0, "c": 28.0, "d": 10.0}
    result = P.rank_and_percentile(values, higher_is_better=True)
    assert result["a"][0] == 1
    assert result["b"][0] == 1  # ties share a rank
    assert result["c"][0] == 2  # dense: the next distinct value is 2, not 3
    assert result["d"][0] == 3
    # a and b: 2 worse, 2 tied -> (2 + 1)/4 = 0.75
    assert result["a"][1] == pytest.approx(0.75)
    assert result["b"][1] == pytest.approx(0.75)
    # c: 1 worse, 1 tied -> (1 + 0.5)/4 = 0.375
    assert result["c"][1] == pytest.approx(0.375)
    # d: 0 worse, 1 tied -> 0.5/4 = 0.125
    assert result["d"][1] == pytest.approx(0.125)


def test_rank_and_percentile_respects_direction():
    values = [("low", 95.0), ("mid", 110.0), ("high", 125.0)]
    ascending_is_better = P.rank_and_percentile(values, higher_is_better=False)
    assert ascending_is_better["low"][0] == 1  # defensive rating: lower is better
    assert ascending_is_better["high"][0] == 3
    assert ascending_is_better["low"][1] > ascending_is_better["high"][1]

    descending = P.rank_and_percentile(values, higher_is_better=True)
    assert descending["high"][0] == 1
    assert descending["low"][0] == 3


def test_rank_and_percentile_edge_cases():
    assert P.rank_and_percentile({}) == {}
    assert P.rank_and_percentile([]) == {}
    # A single subject sits in the middle of a field of one.
    single = P.rank_and_percentile({"only": 12.3})
    assert single == {"only": (1, 0.5)}
    # Everybody equal: one shared rank, everybody at 0.5.
    equal = P.rank_and_percentile({"a": 5.0, "b": 5.0, "c": 5.0})
    assert {v[0] for v in equal.values()} == {1}
    assert all(v[1] == pytest.approx(0.5) for v in equal.values())
    # Missing values are excluded outright, not ranked last.
    sparse = P.rank_and_percentile({"a": 10.0, "b": None, "c": 5.0})
    assert set(sparse) == {"a", "c"}
    assert sparse["a"] == (1, 0.75)
    assert P.rank_and_percentile({"a": None, "b": None}) == {}


def test_rank_and_percentile_ignores_non_finite_values():
    result = P.rank_and_percentile(
        {"a": 10.0, "b": float("nan"), "c": float("inf"), "d": True, "e": "x"}
    )
    assert set(result) == {"a"}


def test_property_percentile_is_monotonic_in_value():
    values = {f"p{i}": float(v) for i, v in enumerate([3, 17, 17, 2, 99, 41, 8, 60, 41, 0])}
    for higher_is_better in (True, False):
        result = P.rank_and_percentile(values, higher_is_better=higher_is_better)
        for left, left_value in values.items():
            for right, right_value in values.items():
                left_rank, left_pct = result[left]
                right_rank, right_pct = result[right]
                better = left_value > right_value if higher_is_better else left_value < right_value
                if better:
                    assert left_pct > right_pct, (left, right)
                    assert left_rank < right_rank, (left, right)
                elif left_value == right_value:
                    assert left_pct == pytest.approx(right_pct)
                    assert left_rank == right_rank
        for _, percentile in result.values():
            assert 0.0 <= percentile <= 1.0


def test_percentile_of_places_a_value_in_a_distribution():
    field = [0.50, 0.52, 0.54, 0.56, 0.58]
    # Above everything: 5 worse, 0 tied -> 1.0
    assert P.percentile_of(0.62, field, True) == pytest.approx(1.0)
    # Below everything -> 0.0
    assert P.percentile_of(0.40, field, True) == pytest.approx(0.0)
    # Exactly the median: 2 worse, 1 tied -> (2 + 0.5)/5 = 0.5
    assert P.percentile_of(0.54, field, True) == pytest.approx(0.5)
    # Direction flips the answer.
    assert P.percentile_of(0.62, field, False) == pytest.approx(0.0)
    # Unsorted and None-laden reference sets still work.
    assert P.percentile_of(0.54, [0.58, None, 0.50, 0.56, 0.52, 0.54], True) == pytest.approx(0.5)


def test_percentile_of_edge_cases():
    assert P.percentile_of(0.5, [], True) is None
    assert P.percentile_of(0.5, [None, None], True) is None
    assert P.percentile_of(None, [0.1, 0.2], True) is None
    assert P.percentile_of(0.5, [0.5], True) == pytest.approx(0.5)
    assert P.percentile_of(0.5, [0.5, 0.5, 0.5], True) == pytest.approx(0.5)


def test_percentile_of_agrees_with_rank_and_percentile():
    values = {"a": 10.0, "b": 20.0, "c": 20.0, "d": 35.0}
    ranked = P.rank_and_percentile(values, higher_is_better=True)
    field = sorted(values.values())
    for subject, value in values.items():
        assert P.percentile_of(value, field, True) == pytest.approx(ranked[subject][1])


def test_summarize_worked_example():
    summary = P.summarize([1, 2, 3, 4, 5])
    assert summary.count == 5
    assert summary.mean == pytest.approx(3.0)
    # Population stddev of 1..5: sqrt(10/5) = sqrt(2) = 1.4142136
    assert summary.stddev == pytest.approx(1.4142136, rel=1e-6)
    assert summary.p50 == pytest.approx(3.0)
    # Linear interpolation: p25 sits at position 0.25*4 = 1.0 -> exactly 2.0
    assert summary.p25 == pytest.approx(2.0)
    assert summary.p75 == pytest.approx(4.0)
    # p10 at position 0.4 -> 1 + 0.4*(2-1) = 1.4
    assert summary.p10 == pytest.approx(1.4)
    assert summary.p90 == pytest.approx(4.6)
    # It unpacks positionally, in the documented order.
    mean, stddev, p10, p25, p50, p75, p90, count = summary
    assert (mean, count) == (pytest.approx(3.0), 5)
    assert p10 < p25 < p50 < p75 < p90
    assert stddev > 0


def test_summarize_edge_cases():
    empty = P.summarize([])
    assert empty.count == 0
    assert all(field is None for field in empty[:-1])
    assert P.summarize([None, None]).count == 0

    single = P.summarize([7.5])
    assert single.count == 1
    assert single.mean == pytest.approx(7.5)
    assert single.stddev == pytest.approx(0.0)
    assert single.p10 == single.p50 == single.p90 == pytest.approx(7.5)

    equal = P.summarize([4.0, 4.0, 4.0])
    assert equal.stddev == pytest.approx(0.0)
    assert equal.p25 == equal.p75 == pytest.approx(4.0)

    # Missing entries are dropped, not counted as zero.
    sparse = P.summarize([2.0, None, 4.0])
    assert sparse.count == 2
    assert sparse.mean == pytest.approx(3.0)


def test_summarize_quantiles_are_ordered_for_a_real_field():
    field = [M.true_shooting_pct(p, 20, 6) for p in range(10, 40)]
    summary = P.summarize(field)
    assert summary.count == 30
    assert summary.p10 <= summary.p25 <= summary.p50 <= summary.p75 <= summary.p90
    assert summary.p10 >= min(field)
    assert summary.p90 <= max(field)
