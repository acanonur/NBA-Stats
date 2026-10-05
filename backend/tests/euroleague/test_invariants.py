"""The box-score rules of design section 4.5, one failure at a time.

Every test starts from an authored box score that satisfies every rule (``test_baseline`` proves
it), breaks exactly one thing, and asserts that exactly the right rule fires with the right
hardness. That is the only way to know a rule is *the* reason a game was quarantined and not an
accident of another one.

The last group runs the workbook importer's own ``check_game`` and this module's over the same
broken games. Both exist (the importer's was written for sheets, this one for the service), and the
test fails if they ever disagree about which rule fired, how hard it is, or how many overtimes the
game had, so the two definitions cannot drift apart.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from nbastats.euroleague.importers import workbook as workbook_module
from nbastats.euroleague.ingest import invariants, parse
from nbastats.euroleague.ingest.invariants import GameFacts, Line, check_game

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "euroleague" / "authored"

HOME_PARTIALS = [21, 19, 20, 24]
AWAY_PARTIALS = [18, 22, 17, 22]


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def facts(
    *,
    fixture: str = "box_game_1.json",
    final: tuple[int, int] = (84, 79),
    home: str | None = "ZZA",
    away: str | None = "ZZB",
    home_partials: list[int] | None = None,
    away_partials: list[int] | None = None,
    ot: int | None = None,
    use_partials: bool = True,
) -> GameFacts:
    box = parse.parse_box_score(load(fixture))
    return invariants.box_to_facts(
        box,
        final_home=final[0],
        final_away=final[1],
        home_club=home,
        away_club=away,
        home_partials=(home_partials or HOME_PARTIALS) if use_partials else None,
        away_partials=(away_partials or AWAY_PARTIALS) if use_partials else None,
        ot_periods=ot,
    )


def rules(check: invariants.GameCheck, kind: str | None = None) -> set[str]:
    return {v.rule for v in check.violations if kind is None or v.kind == kind}


def players(f: GameFacts, side: str, *, played: bool = True) -> list[Line]:
    return [p for p in f.players if p.side == side and (p.participation == "played") is played]


# ----------------------------------------------------------------------------- baseline


def test_baseline_has_no_violation_at_all() -> None:
    check = check_game(facts())
    assert check.violations == () and check.ok and check.ot_periods == 0


def test_the_overtime_box_score_is_clean_too() -> None:
    f = facts(
        fixture="box_game_3_overtime.json",
        final=(91, 88),
        home="ZZB",
        away="ZZC",
        home_partials=[20, 22, 19, 22, 8],
        away_partials=[18, 24, 21, 17, 8],
    )
    check = check_game(f)
    assert check.violations == () and check.ot_periods == 1
    assert sum(p.seconds or 0 for p in players(f, "home")) == 13500


def test_the_rule_names_are_a_closed_vocabulary() -> None:
    assert set(invariants.HARD_RULES).isdisjoint(invariants.SOFT_RULES)
    assert "pts_formula" in invariants.HARD_RULES and "pir_formula" in invariants.SOFT_RULES


# ------------------------------------------------------------------------- hard: per line


def test_points_must_equal_the_formula_on_a_player_line() -> None:
    f = facts()
    players(f, "home")[0].pts += 1  # type: ignore[operator]
    check = check_game(f)
    assert "pts_formula" in rules(check, "hard")
    assert not check.ok


def test_points_must_equal_the_formula_on_the_team_line() -> None:
    f = facts()
    f.teams["away"].pts += 2  # type: ignore[operator]
    check = check_game(f)
    assert "pts_formula" in rules(check, "hard")
    assert "final_score" in rules(check, "hard")  # and the team no longer says the final score


def test_rebounds_must_equal_offensive_plus_defensive() -> None:
    f = facts()
    players(f, "away")[2].reb += 1  # type: ignore[operator]
    assert "reb_sum" in rules(check_game(f), "hard")


@pytest.mark.parametrize(("made", "tried"), [("fgm2", "fga2"), ("fgm3", "fga3"), ("ftm", "fta")])
def test_makes_never_exceed_attempts(made: str, tried: str) -> None:
    f = facts()
    line = players(f, "home")[0]
    setattr(line, tried, getattr(line, made) - 1)
    assert "makes_le_attempts" in rules(check_game(f), "hard")


# ------------------------------------------------------------------ hard: players and team


def test_players_must_sum_to_the_team_and_to_the_final_score() -> None:
    f = facts()
    line = players(f, "home")[0]
    line.fgm2 += 1  # type: ignore[operator]
    line.fga2 += 1  # type: ignore[operator]
    line.pts += 2  # type: ignore[operator]  # the line is self-consistent now...
    check = check_game(f)
    assert rules(check, "hard") == {"team_points"}  # ...but the team did not score that
    messages = " ".join(v.message for v in check.violations)
    assert "team line says 84" in messages and "final score is 84" in messages


def test_the_final_score_must_match_the_team_line() -> None:
    check = check_game(facts(final=(85, 79)))
    assert {"final_score", "team_points"} <= rules(check, "hard")


def test_a_missing_final_score_skips_the_rules_that_need_it() -> None:
    f = facts()
    f.final_home = f.final_away = None
    assert check_game(f).violations == ()


def test_partials_must_add_up_to_the_final_score() -> None:
    check = check_game(facts(home_partials=[21, 19, 20, 25]))
    assert rules(check, "hard") == {"partials"}
    assert check.ot_periods is None  # the overtime count is withheld when the evidence disagrees


def test_partials_need_at_least_four_periods_and_equal_lengths() -> None:
    assert "partials" in rules(
        check_game(facts(home_partials=[40, 44], away_partials=[40, 39])), "hard"
    )
    assert "partials" in rules(check_game(facts(away_partials=[18, 22, 17, 22, 0])), "hard")


def test_partials_are_skipped_when_absent() -> None:
    assert check_game(facts(use_partials=False)).violations == ()


# ------------------------------------------------------------------------- hard: time


def test_team_time_is_five_men_for_forty_minutes() -> None:
    f = facts()
    assert sum(p.seconds or 0 for p in players(f, "home")) == 12000
    players(f, "home")[0].seconds -= 60  # type: ignore[operator]
    check = check_game(f)
    assert rules(check, "hard") == {"team_time"}
    assert "11940s, expected 12000s" in " ".join(v.message for v in check.violations)


@pytest.mark.parametrize(("delta", "clean"), [(6, True), (-6, True), (7, False), (-7, False)])
def test_the_live_tolerance_is_six_seconds(delta: int, clean: bool) -> None:
    f = facts()
    players(f, "home")[0].seconds += delta  # type: ignore[operator]
    f.teams["home"].seconds = sum(p.seconds or 0 for p in players(f, "home"))
    assert ("team_time" not in rules(check_game(f))) is clean


def test_the_workbook_tolerance_is_three_seconds_and_is_a_parameter() -> None:
    f = facts()
    players(f, "home")[0].seconds += 4  # type: ignore[operator]
    f.teams["home"].seconds = sum(p.seconds or 0 for p in players(f, "home"))
    assert "team_time" not in rules(check_game(f))  # inside the live six
    f.seconds_tolerance = invariants.WORKBOOK_SECONDS_TOLERANCE
    assert "team_time" in rules(check_game(f), "hard")


def test_overtime_from_partials_expects_the_extra_minutes() -> None:
    f = facts(
        fixture="box_game_3_overtime.json",
        final=(91, 88),
        home="ZZB",
        away="ZZC",
        home_partials=[20, 22, 19, 22],  # claims regulation, but the box has an overtime's minutes
        away_partials=[18, 24, 21, 17],
    )
    check = check_game(f)
    assert {"team_time", "partials"} <= rules(check, "hard")  # the partials don't sum either


def test_overtime_inferred_from_seconds_when_there_are_no_partials() -> None:
    f = facts(
        fixture="box_game_3_overtime.json",
        final=(91, 88),
        home="ZZB",
        away="ZZC",
        use_partials=False,
    )
    check = check_game(f)
    assert check.violations == () and check.ot_periods == 1


def test_an_overtime_hint_from_the_stored_game_is_used_without_partials() -> None:
    f = facts(
        fixture="box_game_3_overtime.json",
        final=(91, 88),
        home="ZZB",
        away="ZZC",
        use_partials=False,
        ot=1,
    )
    assert check_game(f).violations == ()
    wrong = facts(
        fixture="box_game_3_overtime.json",
        final=(91, 88),
        home="ZZB",
        away="ZZC",
        use_partials=False,
        ot=0,
    )
    assert "team_time" in rules(check_game(wrong), "hard")


def test_a_regulation_box_that_says_overtime_is_a_team_time_failure() -> None:
    f = facts(home_partials=[21, 19, 20, 12, 12], away_partials=[18, 22, 17, 11, 11])
    check = check_game(f)
    assert "team_time" in rules(check, "hard")
    assert check.ot_periods is None


# ------------------------------------------------------------------- hard: live additions


def test_a_player_with_zero_seconds_and_production_is_a_hard_failure() -> None:
    f = facts()
    dnp = players(f, "home", played=False)[0]
    dnp.unexplained = ("pts", "reb")
    check = check_game(f)
    assert rules(check, "hard") == {"dnp_has_stats"}
    assert "pts, reb" in check.violations[0].message


def test_the_same_person_twice_in_a_game_is_a_hard_failure() -> None:
    f = facts()
    a, b = players(f, "home")[:2]
    b.person_code = a.person_code
    assert "duplicate_player" in rules(check_game(f), "hard")
    f = facts()
    f.players[-1].person_code = f.players[0].person_code  # one on each side
    message = " ".join(v.message for v in check_game(f).violations if v.rule == "duplicate_player")
    assert "on both sides" in message


def test_a_player_of_another_club_is_a_hard_failure() -> None:
    f = facts()
    players(f, "home")[0].club = "ZZC"
    check = check_game(f)
    assert rules(check, "hard") == {"club_mismatch"}
    assert "home side is ZZA but has players of ZZC" in check.violations[0].message


def test_clubs_swapped_between_sides_are_caught() -> None:
    assert "club_mismatch" in rules(check_game(facts(home="ZZB", away="ZZA")), "hard")


def test_the_club_rule_is_skipped_when_the_clubs_are_not_given() -> None:
    assert check_game(facts(home=None, away=None)).violations == ()


# ------------------------------------------------------------------------------ soft


def test_players_may_fall_short_of_the_team_in_rebounds_and_turnovers_but_not_exceed() -> None:
    f = facts()
    mine = sum(p.reb or 0 for p in players(f, "home"))
    assert f.teams["home"].reb > mine  # the team row's rebounds are credited to no player
    assert check_game(f).violations == ()
    team = f.teams["home"]
    team.oreb, team.dreb = 10, mine - 11  # the team line stays self-consistent...
    team.reb = team.oreb + team.dreb  # ...but is now one short of its own players
    team.tov = sum(p.tov or 0 for p in players(f, "home")) - 1
    check = check_game(f)
    assert rules(check) == {"reb_vs_team", "tov_vs_team"}
    assert check.ok  # soft only: the game is still written


def test_five_starters_per_side_when_the_flag_is_recorded() -> None:
    f = facts()
    next(p for p in players(f, "home") if p.is_starter).is_starter = False
    check = check_game(f)
    assert rules(check) == {"starters"} and check.ok
    assert "4 starters, expected 5" in check.violations[0].message


def test_starters_are_not_judged_when_no_flag_was_recorded() -> None:
    f = facts()
    for p in f.players:
        p.is_starter = None
    assert check_game(f).violations == ()


def test_pir_must_equal_its_formula_when_fouls_drawn_and_blocks_against_are_known() -> None:
    f = facts()
    players(f, "away")[1].pir_official += 1  # type: ignore[operator]
    check = check_game(f)
    assert rules(check) == {"pir_formula"} and check.ok
    g = facts()
    line = players(g, "away")[1]
    line.pir_official += 1  # type: ignore[operator]
    line.fouls_drawn = None  # the formula cannot be evaluated: skipped, not failed
    assert check_game(g).violations == ()


def test_the_reported_game_clock_is_soft_and_accepts_either_convention() -> None:
    f = facts()
    assert f.teams["home"].clock_seconds == 2400
    f.teams["home"].clock_seconds = 12000  # the players' sum is also a fair reading
    assert check_game(f).violations == ()
    f.teams["home"].clock_seconds = 3000
    check = check_game(f)
    assert rules(check) == {"team_clock"} and check.ok


def test_the_overtime_game_clock_is_2700() -> None:
    f = facts(
        fixture="box_game_3_overtime.json",
        final=(91, 88),
        home="ZZB",
        away="ZZC",
        home_partials=[20, 22, 19, 22, 8],
        away_partials=[18, 24, 21, 17, 8],
    )
    assert f.teams["home"].clock_seconds == 2700
    assert check_game(f).violations == ()


# ---------------------------------------------------------------- null is not a violation


def test_a_rule_that_needs_an_unrecorded_value_is_skipped_never_failed() -> None:
    f = facts()
    for line in [*f.players, *f.teams.values()]:
        line.pir_official = line.fouls_drawn = line.blk_against = line.plus_minus = None
        line.is_starter = None
    assert check_game(f).violations == ()
    g = facts()
    for line in g.players:
        if line.participation == "played":
            line.pts = None  # players' points unknown: the sum rules have nothing to compare
    assert "team_points" not in rules(check_game(g))


def test_a_zero_is_not_a_missing_value() -> None:
    f = facts()
    line = players(f, "home")[0]
    line.pts, line.fgm2, line.fgm3, line.ftm = 0, 0, 0, 0
    line.reb, line.oreb, line.dreb = 0, 0, 0
    assert "team_points" in rules(check_game(f), "hard")  # 0 counts: the team total is now short


# ---------------------------------------------------------------------------- describing


def test_describe_and_rule_names_are_ready_for_the_log() -> None:
    f = facts()
    players(f, "home")[0].pts += 1  # type: ignore[operator]
    check = check_game(f)
    assert invariants.rule_names(check.violations) == sorted({v.rule for v in check.violations})
    text = invariants.describe(check.violations, limit=1)
    assert text.startswith("pts_formula:") or "and " in text
    many = [invariants.Violation("r", "hard", "m")] * 9
    assert invariants.describe(many, limit=3).endswith("and 6 more")


# ------------------------------------------------------------- parity with the importer's


def _workbook_line(line: Line) -> Any:
    names = {f.name for f in dataclasses.fields(workbook_module.Line)}
    return workbook_module.Line(**{n: getattr(line, n) for n in names})


def _as_workbook(f: GameFacts) -> Any:
    return workbook_module.GameFacts(
        final_home=f.final_home,
        final_away=f.final_away,
        players=[_workbook_line(p) for p in f.players],
        teams={side: _workbook_line(line) for side, line in f.teams.items()},
        home_partials=f.home_partials,
        away_partials=f.away_partials,
        seconds_tolerance=f.seconds_tolerance,
    )


def _break(f: GameFacts, what: str) -> None:
    home = players(f, "home")
    away = players(f, "away")
    if what == "points":
        home[0].pts += 1
    elif what == "team_points":
        f.teams["home"].pts += 3
    elif what == "reb":
        away[0].reb += 2
    elif what == "makes":
        home[1].fgm3 = home[1].fga3 + 1
    elif what == "final":
        f.final_home += 1
    elif what == "seconds":
        away[0].seconds -= 90
    elif what == "partials":
        f.home_partials = [21, 19, 20, 20]
    elif what == "starters":
        next(p for p in home if p.is_starter).is_starter = False
    elif what == "pir":
        away[3].pir_official += 2
    elif what == "reb_team":
        f.teams["away"].reb = 1
    elif what == "tov_team":
        f.teams["home"].tov = 1
    elif what == "ot_partials":
        f.home_partials = [21, 19, 20, 12, 12]
        f.away_partials = [18, 22, 17, 11, 11]
    elif what == "missing":
        for line in f.players:
            line.fouls_drawn = None
            line.pir_official = None
    elif what == "none":
        pass
    else:  # pragma: no cover
        raise AssertionError(what)


_LIVE_ONLY = {"dnp_has_stats", "duplicate_player", "club_mismatch", "team_clock"}


@pytest.mark.parametrize(
    "what",
    [
        "none",
        "points",
        "team_points",
        "reb",
        "makes",
        "final",
        "seconds",
        "partials",
        "starters",
        "pir",
        "reb_team",
        "tov_team",
        "ot_partials",
        "missing",
    ],
)
def test_the_importers_check_game_and_this_one_agree(what: str) -> None:
    f = facts()
    _break(f, what)
    mine = check_game(f)
    theirs = workbook_module.check_game(_as_workbook(f))
    pairs = lambda violations: {
        (v.rule, v.kind) for v in violations if v.rule not in _LIVE_ONLY
    }  # noqa: E731
    assert pairs(mine.violations) == pairs(theirs.violations), what
    assert mine.ot_periods == theirs.ot_periods, what


def test_the_importers_check_game_agrees_on_an_overtime_game_without_partials() -> None:
    f = facts(
        fixture="box_game_3_overtime.json",
        final=(91, 88),
        home="ZZB",
        away="ZZC",
        use_partials=False,
    )
    mine = check_game(f)
    theirs = workbook_module.check_game(_as_workbook(f))
    assert mine.violations == () and theirs.violations == ()
    assert mine.ot_periods == theirs.ot_periods == 1


def test_every_rule_the_function_can_emit_is_in_the_vocabulary() -> None:
    seen: set[str] = set()
    for what in [
        "points",
        "team_points",
        "reb",
        "makes",
        "final",
        "seconds",
        "partials",
        "starters",
        "pir",
        "reb_team",
        "tov_team",
        "ot_partials",
    ]:
        f = facts()
        _break(f, what)
        seen |= rules(check_game(f))
    f = facts()
    players(f, "home", played=False)[0].unexplained = ("pts",)
    players(f, "home")[0].club = "ZZC"
    players(f, "home")[1].person_code = players(f, "home")[0].person_code
    f.teams["home"].clock_seconds = 3000
    seen |= rules(check_game(f))
    assert seen <= set(invariants.HARD_RULES) | set(invariants.SOFT_RULES)
    assert set(invariants.HARD_RULES) <= seen | {"reb_sum"} | set()  # reb_sum has its own test


def _copy_of(f: GameFacts) -> GameFacts:
    return copy.deepcopy(f)


def test_checking_does_not_mutate_the_facts() -> None:
    f = facts()
    before = _copy_of(f)
    check_game(f)
    assert f == before
