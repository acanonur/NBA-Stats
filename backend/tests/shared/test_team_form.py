"""Tests for team scoring, points allowed, form and opponent-adjusted scoring.

Every formula in design section 7.1 is checked against a number worked out by hand, with the
arithmetic in the comment above the assertion, and then against an independent, deliberately
naive reference implementation on random leagues. The hand-worked cases pin what each number
*means* (points allowed is the opponent's score; adjusted values leave the game itself out of
the opponent's baseline); the random comparison pins that the efficient code and the plain
loops agree everywhere else.

Also pinned, because they are the easy things to get quietly wrong:

* a missing value stays ``None`` (an unknown team time makes the regulation mean ``None``; it
  is not computed from the games that happen to have one);
* the neutral flag is never assumed false: unknown neutrality is classed by the home flag and
  the neutral split is ``None`` when nothing is known;
* the form window is validated, not clamped, so a route can answer 400;
* no key in the payload is a rank, and none is a forbidden market word.
"""

from __future__ import annotations

import dataclasses
import json
import math
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from nbastats.shared import league_profile as LP
from nbastats.shared import market_guard
from nbastats.shared import team_form as TF

D0 = date(2026, 10, 1)


def day(n: int) -> date:
    return D0 + timedelta(days=n)


def both_sides(game_id, when, home, away, home_pts, away_pts, **kwargs):
    """The two ``TeamGame`` rows of one final game."""
    common = dict(
        game_id=game_id,
        date=when,
        is_neutral=kwargs.pop("is_neutral", None),
        overtime_periods=kwargs.pop("overtime_periods", None),
    )
    seconds = kwargs.pop("team_seconds", None)
    assert not kwargs, kwargs
    return [
        TF.TeamGame(
            team=home,
            opponent=away,
            is_home=True,
            pts=home_pts,
            opp_pts=away_pts,
            team_seconds=seconds,
            **common,
        ),
        TF.TeamGame(
            team=away,
            opponent=home,
            is_home=False,
            pts=away_pts,
            opp_pts=home_pts,
            team_seconds=seconds,
            **common,
        ),
    ]


def small_profile(adjusted_min_games: int = 2) -> LP.LeagueProfile:
    return dataclasses.replace(
        LP.NBA, matchup=LP.MatchupRules(adjusted_min_games=adjusted_min_games)
    )


# --------------------------------------------------------------- hand-worked: scoring

# Team T's six games, oldest first (day 1..6), against O1..O3:
#   g1 home 90-80 vs O1   g2 away 70-85 vs O2   g3 home 100-90 vs O3
#   g4 away 88-92 vs O1   g5 home 95-75 vs O2   g6 away 80-84 vs O3
T_GAMES = [
    ("g1", 1, True, 90, 80, "O1"),
    ("g2", 2, False, 70, 85, "O2"),
    ("g3", 3, True, 100, 90, "O3"),
    ("g4", 4, False, 88, 92, "O1"),
    ("g5", 5, True, 95, 75, "O2"),
    ("g6", 6, False, 80, 84, "O3"),
]


def scoring_league(**overrides):
    rows = []
    for game_id, n, home, pts, allowed, opp in T_GAMES:
        extra = overrides.get(game_id, {})
        if home:
            rows += both_sides(game_id, day(n), "T", opp, pts, allowed, **extra)
        else:
            rows += both_sides(game_id, day(n), opp, "T", allowed, pts, **extra)
    return TF.LeagueIndex(rows)


def test_scoring_means_and_record() -> None:
    form = TF.compute_team_form(scoring_league(), "T", profile=LP.NBA)
    # 90+70+100+88+95+80 = 523 over 6;  80+85+90+92+75+84 = 506 over 6;  difference 17/6
    assert form.games == 6
    assert form.points_per_game == pytest.approx(523 / 6, abs=1e-12)
    assert form.points_allowed_per_game == pytest.approx(506 / 6, abs=1e-12)
    assert form.differential_per_game == pytest.approx(17 / 6, abs=1e-12)
    # wins: g1, g3, g5; losses: g2, g4, g6
    assert (form.wins, form.losses) == (3, 3)


def test_points_allowed_is_the_opponents_score_not_the_margin() -> None:
    form = TF.compute_team_form(scoring_league(), "T", profile=LP.NBA)
    # the opponents' own view of the same games: what O1, O2, O3 scored against T
    opp_scored = [80, 85, 90, 92, 75, 84]
    assert form.points_allowed_per_game == pytest.approx(sum(opp_scored) / 6)


def test_latest_game_and_form_are_newest_first() -> None:
    form = TF.compute_team_form(scoring_league(), "T", profile=LP.NBA)
    assert form.latest_game.game_id == "g6"
    assert [g.game_id for g in form.form] == ["g6", "g5", "g4", "g3", "g2"]  # default window 5
    assert form.latest_game.result == "L"
    assert form.latest_game.opponent == "O3"
    three = TF.compute_team_form(scoring_league(), "T", profile=LP.NBA, form_window=3)
    assert [g.game_id for g in three.form] == ["g6", "g5", "g4"]


def test_last_n_and_last_10_windows() -> None:
    form = TF.compute_team_form(scoring_league(), "T", profile=LP.NBA, form_window=3)
    # last 3 are g6, g5, g4: points (80+95+88)/3 = 263/3, allowed (84+75+92)/3 = 251/3
    assert form.last_n.window == 3 and form.last_n.games == 3
    assert form.last_n.points_per_game == pytest.approx(263 / 3, abs=1e-12)
    assert form.last_n.points_allowed_per_game == pytest.approx(251 / 3, abs=1e-12)
    # last 10 with only 6 played is the six, and says so
    assert form.last10.window == 10 and form.last10.games == 6
    assert form.last10.points_per_game == pytest.approx(523 / 6, abs=1e-12)


def test_venue_splits_home_and_away() -> None:
    form = TF.compute_team_form(scoring_league(), "T", profile=LP.NBA)
    # home g1, g3, g5: scored (90+100+95)/3 = 95, allowed (80+90+75)/3 = 245/3
    assert form.venue_splits.home.games == 3
    assert form.venue_splits.home.points_per_game == pytest.approx(95.0)
    assert form.venue_splits.home.points_allowed_per_game == pytest.approx(245 / 3)
    # away g2, g4, g6: scored (70+88+80)/3 = 238/3, allowed (85+92+84)/3 = 87
    assert form.venue_splits.away.games == 3
    assert form.venue_splits.away.points_per_game == pytest.approx(238 / 3)
    assert form.venue_splits.away.points_allowed_per_game == pytest.approx(87.0)
    # nothing says whether any venue was neutral, so the neutral split is "not tracked"
    assert form.venue_splits.neutral is None


def test_a_neutral_game_goes_only_in_the_neutral_split() -> None:
    index = scoring_league(g4={"is_neutral": True}, g1={"is_neutral": False})
    form = TF.compute_team_form(index, "T", profile=LP.EUROLEAGUE)
    splits = form.venue_splits
    # g4 (away 88-92) is neutral. Away is then g2 (70-85) and g6 (80-84): 75 and 84.5
    assert splits.neutral.games == 1
    assert splits.neutral.points_per_game == 88.0 and splits.neutral.points_allowed_per_game == 92.0
    assert splits.away.games == 2
    assert splits.away.points_per_game == pytest.approx(75.0)
    assert splits.away.points_allowed_per_game == pytest.approx(84.5)
    assert splits.home.games == 3
    # the neutral game still counts in the totals
    assert form.games == 6


def test_tracked_neutrality_with_no_neutral_games_is_an_empty_split_not_none() -> None:
    index = scoring_league(**{g: {"is_neutral": False} for g, *_ in T_GAMES})
    neutral = TF.compute_team_form(index, "T", profile=LP.EUROLEAGUE).venue_splits.neutral
    assert neutral == TF.Split(games=0, points_per_game=None, points_allowed_per_game=None)


def test_a_team_with_no_games_has_nothing_not_zeros() -> None:
    form = TF.compute_team_form(scoring_league(), "nobody", profile=LP.NBA)
    assert form.games == 0 and (form.wins, form.losses) == (0, 0)
    assert form.points_per_game is None and form.points_allowed_per_game is None
    assert form.differential_per_game is None
    assert form.points_per_regulation is None and form.points_allowed_per_regulation is None
    assert form.latest_game is None and form.form == ()
    assert form.last_n.games == 0 and form.last_n.points_per_game is None
    assert form.venue_splits.home == TF.Split(0, None, None)
    assert form.venue_splits.neutral is None
    assert form.adjusted_points_against == TF.Adjusted(None, 0)


# ------------------------------------------------------- hand-worked: regulation scaling


def test_regulation_scaling_divides_out_overtime() -> None:
    # EuroLeague: regulation is 12,000 team seconds; one overtime adds 1,500.
    reg, ot = 12_000, 13_500
    per_game = {g: {"team_seconds": reg} for g, *_ in T_GAMES}
    per_game["g3"] = {"team_seconds": ot, "overtime_periods": 1}
    form = TF.compute_team_form(scoring_league(**per_game), "T", profile=LP.EUROLEAGUE)
    # g3 scored 100 in 13,500 s: 100 * 12000 / 13500 = 88.8888...; allowed 90 -> 80
    scored = (90 + 70 + 100 * 12_000 / 13_500 + 88 + 95 + 80) / 6
    allowed = (80 + 85 + 90 * 12_000 / 13_500 + 92 + 75 + 84) / 6
    assert form.points_per_regulation == pytest.approx(scored, abs=1e-12)
    assert form.points_allowed_per_regulation == pytest.approx(allowed, abs=1e-12)
    assert form.points_per_regulation < form.points_per_game  # overtime inflated the raw mean
    assert form.latest_game.overtime_periods is None
    assert next(g for g in form.form if g.game_id == "g3").overtime_periods == 1


def test_regulation_uses_the_leagues_own_regulation_time() -> None:
    seconds = {g: {"team_seconds": 14_400} for g, *_ in T_GAMES}
    form = TF.compute_team_form(scoring_league(**seconds), "T", profile=LP.NBA)
    assert form.points_per_regulation == pytest.approx(523 / 6)  # NBA regulation is 14,400 s
    form = TF.compute_team_form(scoring_league(**seconds), "T", profile=LP.EUROLEAGUE)
    assert form.points_per_regulation == pytest.approx(523 / 6 * 12_000 / 14_400)


def test_one_unknown_team_time_makes_the_regulation_mean_unknown() -> None:
    seconds = {g: {"team_seconds": 12_000} for g, *_ in T_GAMES}
    seconds["g2"] = {"team_seconds": None}
    form = TF.compute_team_form(scoring_league(**seconds), "T", profile=LP.EUROLEAGUE)
    assert form.points_per_regulation is None
    assert form.points_allowed_per_regulation is None
    assert form.points_per_game is not None  # the raw means are unaffected


def test_a_non_positive_team_time_is_unknown_not_a_division_by_zero() -> None:
    for bad in (0, -5):
        seconds = {g: {"team_seconds": 12_000} for g, *_ in T_GAMES}
        seconds["g1"] = {"team_seconds": bad}
        form = TF.compute_team_form(scoring_league(**seconds), "T", profile=LP.EUROLEAGUE)
        assert form.points_per_regulation is None


# ------------------------------------------------------- hand-worked: adjusted points

# Three teams, six games (no ties):
#   G1 A 100-90 B   G2 A 80-70 C   G3 B 85-95 C   G4 A 90-100 B   G5 A 75-60 C   G6 B 70-80 C
ADJ_GAMES = [
    ("G1", 1, "A", "B", 100, 90),
    ("G2", 2, "A", "C", 80, 70),
    ("G3", 3, "B", "C", 85, 95),
    ("G4", 4, "A", "B", 90, 100),
    ("G5", 5, "A", "C", 75, 60),
    ("G6", 6, "B", "C", 70, 80),
]


def adjusted_league():
    rows = []
    for game_id, n, home, away, hp, ap in ADJ_GAMES:
        rows += both_sides(game_id, day(n), home, away, hp, ap)
    return TF.LeagueIndex(rows)


def test_adjusted_points_leave_the_game_itself_out_of_the_opponents_baseline() -> None:
    form = TF.compute_team_form(adjusted_league(), "A", profile=small_profile(4))
    # A's four games, each against an opponent's OTHER games:
    #  G1 vs B (others G3,G4,G6: B scored 85,100,70 = mean 85; B allowed 95,90,80 = mean 265/3)
    #     against: 90 - 85 = 5            for: 100 - 265/3 = 35/3
    #  G2 vs C (others G3,G5,G6: C scored 95,60,80 = 235/3; C allowed 85,75,70 = 230/3)
    #     against: 70 - 235/3 = -25/3     for: 80 - 230/3 = 10/3
    #  G4 vs B (others G1,G3,G6: B scored 90,85,70 = 245/3; B allowed 100,95,80 = 275/3)
    #     against: 100 - 245/3 = 55/3     for: 90 - 275/3 = -5/3
    #  G5 vs C (others G2,G3,G6: C scored 70,95,80 = 245/3; C allowed 80,85,70 = 235/3)
    #     against: 60 - 245/3 = -65/3     for: 75 - 235/3 = -10/3
    # means: against (5 - 25/3 + 55/3 - 65/3)/4 = -5/3 ; for (35 + 10 - 5 - 10)/3/4 = 2.5
    assert form.adjusted_points_against.games == 4
    assert form.adjusted_points_against.value == pytest.approx(-5 / 3, abs=1e-12)
    assert form.adjusted_points_for.games == 4
    assert form.adjusted_points_for.value == pytest.approx(2.5, abs=1e-12)


def test_the_baseline_really_excludes_the_game() -> None:
    """Guard against the easy mistake: using the opponent's whole-season mean."""
    form = TF.compute_team_form(adjusted_league(), "A", profile=small_profile(4))
    # with the whole-season mean of B (G1 90, G3 85, G4 100, G6 70 = 86.25) and C (70, 95, 60, 80
    # = 76.25) the first game's 'against' would be 90 - 86.25 = 3.75, not 5
    whole_season_value = ((90 - 86.25) + (70 - 76.25) + (100 - 86.25) + (60 - 76.25)) / 4
    assert form.adjusted_points_against.value != pytest.approx(whole_season_value)


def test_the_adjusted_value_needs_enough_qualifying_games() -> None:
    four = TF.compute_team_form(adjusted_league(), "A", profile=LP.NBA)  # needs 5
    assert four.adjusted_points_against == TF.Adjusted(None, 4)
    assert four.adjusted_points_for == TF.Adjusted(None, 4)
    exact = TF.compute_team_form(adjusted_league(), "A", profile=small_profile(4))
    assert exact.adjusted_points_against.value is not None
    five = TF.compute_team_form(adjusted_league(), "A", profile=small_profile(5))
    assert five.adjusted_points_against == TF.Adjusted(None, 4)


def test_an_opponent_with_fewer_than_two_other_games_does_not_qualify() -> None:
    rows = []
    for game_id, n, home, away, hp, ap in ADJ_GAMES:
        rows += both_sides(game_id, day(n), home, away, hp, ap)
    rows += both_sides("G7", day(7), "A", "D", 99, 60)  # D has played only this game
    form = TF.compute_team_form(TF.LeagueIndex(rows), "A", profile=small_profile(4))
    assert form.games == 5
    assert form.adjusted_points_against.games == 4  # G7 did not qualify
    assert form.adjusted_points_against.value == pytest.approx(-5 / 3, abs=1e-12)
    # D with one other game still does not qualify; with two it does
    rows += both_sides("G8", day(8), "D", "B", 80, 70)
    rows += both_sides("G9", day(9), "D", "C", 70, 90)
    later = TF.compute_team_form(TF.LeagueIndex(rows), "A", profile=small_profile(4))
    assert later.adjusted_points_against.games == 5


# ----------------------------------------------------------------------- league average


def test_league_average_is_the_mean_over_all_team_games() -> None:
    average = adjusted_league().league_average()
    # 190 + 150 + 180 + 190 + 135 + 150 = 995 points over 12 team-games
    assert average.points_per_game == pytest.approx(995 / 12, abs=1e-12)
    assert average.teams == 3
    assert average.to_payload() == {"pointsPerGame": pytest.approx(995 / 12), "teams": 3}


def test_league_average_equals_league_points_allowed() -> None:
    index = adjusted_league()
    allowed = [g.opp_pts for team in index.teams for g in index.games_for(team)]
    assert index.league_average().points_per_game == pytest.approx(sum(allowed) / len(allowed))


def test_an_empty_league() -> None:
    average = TF.LeagueIndex([]).league_average()
    assert average == TF.LeagueAverage(points_per_game=None, teams=0)


# ---------------------------------------------------------------------------- ordering


def test_games_sort_newest_first_with_a_deterministic_tiebreak() -> None:
    rows = []
    rows += both_sides("b", day(3), "T", "X", 80, 70)
    rows += both_sides("a", day(3), "T", "Y", 80, 70)
    rows += both_sides("z", day(1), "T", "Z", 80, 70)
    ordered = TF.LeagueIndex(rows).games_for("T")
    assert [g.game_id for g in ordered] == ["b", "a", "z"]


def test_the_tip_off_orders_games_on_the_same_day() -> None:
    early = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    late = datetime(2026, 10, 3, 20, 0, tzinfo=timezone.utc)

    def row(game_id, tip, opp):
        return TF.TeamGame(
            game_id=game_id,
            date=day(2),
            team="T",
            opponent=opp,
            is_home=True,
            pts=80,
            opp_pts=70,
            tipoff_utc=tip,
        )

    ordered = TF.LeagueIndex([row("x", early, "A"), row("y", late, "B"), row("w", None, "C")])
    assert [g.game_id for g in ordered.games_for("T")] == ["y", "x", "w"]


def test_games_for_returns_a_copy() -> None:
    index = scoring_league()
    games = index.games_for("T")
    games.clear()
    assert len(index.games_for("T")) == 6


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize("window", [2, 16, 0, -1, 100])
def test_a_form_window_outside_the_range_is_an_error_not_a_clamp(window) -> None:
    with pytest.raises(ValueError, match="between 3 and 15"):
        TF.compute_team_form(scoring_league(), "T", profile=LP.NBA, form_window=window)


@pytest.mark.parametrize("window", [3, 5, 15])
def test_the_ends_of_the_form_window_range_are_allowed(window) -> None:
    form = TF.compute_team_form(scoring_league(), "T", profile=LP.NBA, form_window=window)
    assert form.last_n.window == window


@pytest.mark.parametrize("window", [True, 5.0, "5"])
def test_a_non_integer_form_window_is_an_error(window) -> None:
    with pytest.raises(ValueError):
        TF.compute_team_form(scoring_league(), "T", profile=LP.NBA, form_window=window)


def test_duplicate_rows_are_rejected() -> None:
    rows = both_sides("g1", day(1), "T", "X", 80, 70) * 2
    with pytest.raises(ValueError, match="duplicate"):
        TF.LeagueIndex(rows)


@pytest.mark.parametrize("pts", [None, "80", math.nan, math.inf, True])
def test_a_score_must_be_a_finite_number(pts) -> None:
    with pytest.raises(ValueError):
        TF.TeamGame(
            game_id="g", date=day(1), team="A", opponent="B", is_home=True, pts=pts, opp_pts=70
        )


def test_a_team_cannot_play_itself() -> None:
    with pytest.raises(ValueError):
        TF.TeamGame(
            game_id="g", date=day(1), team="A", opponent="A", is_home=True, pts=80, opp_pts=70
        )


def test_a_tied_score_is_neither_a_win_nor_a_loss() -> None:
    index = TF.LeagueIndex(both_sides("g", day(1), "A", "B", 80, 80))
    form = TF.compute_team_form(index, "A", profile=LP.NBA)
    assert (form.wins, form.losses, form.games) == (0, 0, 1)
    assert form.latest_game.result is None


# ----------------------------------------------------------------------------- payload


def opponent_ref(key):
    return {
        "league": "nba",
        "id": str(key),
        "abbr": str(key),
        "name": f"Team {key}",
        "shortName": None,
    }


def test_payload_shape_matches_the_contract() -> None:
    index = scoring_league(g4={"is_neutral": True, "overtime_periods": 1})
    form = TF.compute_team_form(index, "T", profile=LP.EUROLEAGUE)
    payload = form.to_payload(opponent_ref)
    assert list(payload) == [
        "record",
        "games",
        "pointsPerGame",
        "pointsAllowedPerGame",
        "differentialPerGame",
        "pointsPerRegulation",
        "pointsAllowedPerRegulation",
        "latestGame",
        "form",
        "lastN",
        "last10",
        "venueSplits",
        "adjustedPointsAgainst",
        "adjustedPointsFor",
    ]
    assert payload["record"] == {"wins": 3, "losses": 3}
    latest = payload["latestGame"]
    assert list(latest) == [
        "gameId",
        "date",
        "opponent",
        "isHome",
        "isNeutral",
        "teamScore",
        "opponentScore",
        "result",
        "overtimePeriods",
    ]
    assert latest["gameId"] == "g6" and latest["date"] == "2026-10-07"
    assert latest["opponent"]["id"] == "O3" and latest["result"] == "L"
    assert latest["teamScore"] == 80 and latest["opponentScore"] == 84
    assert len(payload["form"]) == 5 and payload["form"][0] == latest
    g4 = next(g for g in payload["form"] if g["gameId"] == "g4")
    assert g4["isNeutral"] is True and g4["overtimePeriods"] == 1
    assert list(payload["lastN"]) == ["window", "games", "pointsPerGame", "pointsAllowedPerGame"]
    assert list(payload["venueSplits"]) == ["home", "away", "neutral"]
    assert list(payload["venueSplits"]["home"]) == [
        "games",
        "pointsPerGame",
        "pointsAllowedPerGame",
    ]
    assert list(payload["adjustedPointsAgainst"]) == ["value", "games"]
    assert json.loads(json.dumps(payload)) == payload


def test_payload_for_a_team_with_no_games_is_all_null_not_zero() -> None:
    form = TF.compute_team_form(scoring_league(), "nobody", profile=LP.NBA)
    payload = form.to_payload(opponent_ref)
    assert payload["latestGame"] is None and payload["form"] == []
    assert payload["pointsPerGame"] is None and payload["pointsAllowedPerGame"] is None
    assert payload["venueSplits"]["neutral"] is None
    assert payload["record"] == {"wins": 0, "losses": 0}


def test_the_payload_has_no_rank_and_no_forbidden_word() -> None:
    form = TF.compute_team_form(adjusted_league(), "A", profile=small_profile(4))
    payload = form.to_payload(opponent_ref)
    assert market_guard.scan_keys(payload) == []
    words = {w for key in _all_keys(payload) for w in market_guard.split_words(key)}
    assert not words & {"rank", "ranks", "ranking", "percentile"}


def _all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_keys(child)


# ------------------------------------------------- against an independent naive reference


def reference_form(rows_by_team, team, regulation, min_other, min_games):
    """The plain-loop definition of every scalar, written without the module's helpers."""
    games = sorted(rows_by_team[team], key=lambda g: (g.date, g.game_id), reverse=True)
    n = len(games)
    out = {"games": n}
    if n:
        out["ppg"] = sum(g.pts for g in games) / n
        out["papg"] = sum(g.opp_pts for g in games) / n
        if all(g.team_seconds for g in games):
            out["ppr"] = sum(g.pts * regulation / g.team_seconds for g in games) / n
            out["papr"] = sum(g.opp_pts * regulation / g.team_seconds for g in games) / n
    against, scored = [], []
    for g in games:
        others = [r for r in rows_by_team[g.opponent] if r.game_id != g.game_id]
        if len(others) >= min_other:
            against.append(g.opp_pts - sum(r.pts for r in others) / len(others))
            scored.append(g.pts - sum(r.opp_pts for r in others) / len(others))
    out["qualifying"] = len(against)
    if len(against) >= min_games:
        out["adj_against"] = sum(against) / len(against)
        out["adj_for"] = sum(scored) / len(scored)
    return out


@pytest.mark.parametrize("seed", range(12))
def test_random_leagues_agree_with_the_naive_reference(seed) -> None:
    rng = random.Random(seed)
    teams = [f"T{i}" for i in range(rng.randint(4, 8))]
    rows = []
    game_no = 0
    for day_no in range(rng.randint(12, 30)):
        pool = teams[:]
        rng.shuffle(pool)
        for i in range(0, len(pool) - 1, 2):
            if rng.random() < 0.6:
                continue
            game_no += 1
            overtime = rng.random() < 0.15
            seconds = 12_000 + (1_500 if overtime else 0)
            rows += both_sides(
                f"g{game_no:03d}",
                day(day_no),
                pool[i],
                pool[i + 1],
                rng.randint(60, 110),
                rng.randint(60, 110),
                team_seconds=seconds,
            )
    # a tie cannot happen in basketball; drop both sides of any the generator produced
    rows = [r for r in rows if r.pts != r.opp_pts]
    index = TF.LeagueIndex(rows)
    by_team = {t: index.games_for(t) for t in teams}
    for team in teams:
        got = TF.compute_team_form(index, team, profile=LP.EUROLEAGUE)
        ref = reference_form(by_team, team, LP.EUROLEAGUE.team_regulation_seconds, 2, 5)
        assert got.games == ref["games"]
        if ref["games"] == 0:
            assert got.points_per_game is None
            continue
        assert got.points_per_game == pytest.approx(ref["ppg"], abs=1e-10)
        assert got.points_allowed_per_game == pytest.approx(ref["papg"], abs=1e-10)
        assert got.points_per_regulation == pytest.approx(ref["ppr"], abs=1e-10)
        assert got.points_allowed_per_regulation == pytest.approx(ref["papr"], abs=1e-10)
        assert got.adjusted_points_against.games == ref["qualifying"]
        if "adj_against" in ref:
            assert got.adjusted_points_against.value == pytest.approx(ref["adj_against"], abs=1e-10)
            assert got.adjusted_points_for.value == pytest.approx(ref["adj_for"], abs=1e-10)
        else:
            assert got.adjusted_points_against.value is None
            assert got.adjusted_points_for.value is None
        assert got.wins + got.losses == got.games


def test_league_points_allowed_equals_points_scored_in_random_leagues() -> None:
    rng = random.Random(99)
    rows = []
    for i in range(40):
        rows += both_sides(
            f"g{i}",
            day(i),
            "A" if i % 2 else "B",
            "C" if i % 3 else "D",
            rng.randint(60, 100),
            rng.randint(60, 100),
        )
    index = TF.LeagueIndex(rows)
    scored = sum(g.pts for t in index.teams for g in index.games_for(t))
    allowed = sum(g.opp_pts for t in index.teams for g in index.games_for(t))
    assert scored == allowed
