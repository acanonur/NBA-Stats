"""Tests for the availability vocabulary and the rules that decide which entry counts.

The point of this module is that three different questions get three different answers, so the
suite is organised around them:

* **What do we show?** ``None`` for "no report"; never a default of "available".
* **What does the model assume?** One for a player with no entry, stated as an assumption.
* **Does an old entry still count?** The three in-force rules, each tested at its exact edge,
  plus the order in which they are reported and the one deliberate reading of rule (a).

Every boundary is tested on both sides, because the rules are written in words ("passes 14
days", "has passed") and a refactor changes an inclusive edge into an exclusive one without
changing a single line anyone would read twice.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from nbastats.shared import availability as A
from nbastats.shared import league_profile as LP

UTC = timezone.utc
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
BERLIN = ZoneInfo("Europe/Berlin")


def entry(entry_id="e1", *, published=NOW - timedelta(hours=1), **kwargs) -> A.StatusEntry:
    kwargs.setdefault("status", "out")
    kwargs.setdefault("source_kind", "pressArticle")
    return A.StatusEntry(entry_id=entry_id, source_published_at=published, **kwargs)


# ------------------------------------------------------------------------- statuses


@pytest.mark.parametrize("raw", ["out", "OUT", " Out ", "doubtful", "QUESTIONABLE", "probable"])
def test_statuses_are_accepted_in_any_capitalisation(raw) -> None:
    assert A.normalise_status(raw) == raw.strip().lower()


def test_available_and_none() -> None:
    assert A.normalise_status("AVAILABLE") == "available"
    assert A.normalise_status(None) is None


@pytest.mark.parametrize(
    "raw", ["game-time decision", "", "   ", "o u t", "day-to-day", "questionable?", 1, True, []]
)
def test_an_unknown_status_is_rejected_and_never_becomes_available(raw) -> None:
    """The workbook's IFERROR(...,1) read an unrecognised status as 'certainly plays'."""
    with pytest.raises(A.InvalidStatusError):
        A.normalise_status(raw)


def test_the_invalid_status_error_is_a_value_error_naming_the_input() -> None:
    with pytest.raises(ValueError, match="game-time"):
        A.normalise_status("game-time")


def test_vocabulary_constants_are_the_designed_ones() -> None:
    assert A.STATUSES == ("out", "doubtful", "questionable", "probable", "available")
    assert A.REASON_CATEGORIES == (
        "injury",
        "illness",
        "rest",
        "coachDecision",
        "personal",
        "suspension",
        "gLeague",
        "notWithTeam",
        "notRegistered",
        "other",
    )
    assert A.SOURCE_KINDS == (
        "leagueReport",
        "clubStatement",
        "pressArticle",
        "boxScoreInference",
        "workbookImport",
        "manual",
    )
    assert A.TEAM_REPORT_STATES == ("submitted", "notYetSubmitted", "noReport")


def test_reason_category_and_source_kind_validation() -> None:
    assert A.normalise_reason_category("coachDecision") == "coachDecision"
    assert A.normalise_reason_category(None) is None
    for bad in ("coach_decision", "Injury", "", "ill"):
        with pytest.raises(A.InvalidReasonCategoryError):
            A.normalise_reason_category(bad)
    assert A.normalise_source_kind("leagueReport") == "leagueReport"
    for bad in (None, "", "league", "LeagueReport"):
        with pytest.raises(A.InvalidSourceKindError):
            A.normalise_source_kind(bad)


def test_status_label_is_none_for_no_status() -> None:
    assert A.status_label("questionable") == "Questionable"
    assert A.status_label("OUT") == "Out"
    assert A.status_label(None) is None
    with pytest.raises(A.InvalidStatusError):
        A.status_label("maybe")


# --------------------------------------------------------- display versus model chance


def test_default_chances_are_the_workbook_table() -> None:
    expected = {
        "out": 0.0,
        "doubtful": 0.25,
        "questionable": 0.5,
        "probable": 0.85,
        "available": 1.0,
    }
    for status, chance in expected.items():
        assert A.display_chance(status) == chance
        assert A.model_chance(status) == chance
    assert dict(LP.NBA.status_chance) == expected
    assert dict(LP.EUROLEAGUE.status_chance) == expected


def test_no_report_displays_as_null_but_the_model_assumes_one() -> None:
    assert A.display_chance(None) is None
    assert A.model_chance(None) == 1.0
    # and the two differ only here
    assert A.display_chance("probable") == A.model_chance("probable") == 0.85


def test_a_custom_chance_table_is_honoured() -> None:
    table = {"out": 0.0, "doubtful": 0.1, "questionable": 0.4, "probable": 0.9, "available": 1.0}
    assert A.display_chance("doubtful", table) == 0.1
    assert A.model_chance("DOUBTFUL", table) == 0.1
    assert A.model_chance(None, table) == 1.0


def test_validate_status_chance() -> None:
    good = {"out": 0, "doubtful": 0.25, "questionable": 0.5, "probable": 0.85, "available": 1}
    out = A.validate_status_chance(good)
    assert out == {
        "out": 0.0,
        "doubtful": 0.25,
        "questionable": 0.5,
        "probable": 0.85,
        "available": 1.0,
    }
    assert all(isinstance(v, float) for v in out.values())
    bad_tables = [
        {k: v for k, v in good.items() if k != "out"},
        {**good, "extra": 0.5},
        {**good, "probable": 1.01},
        {**good, "probable": -0.01},
        {**good, "probable": math.nan},
        {**good, "probable": True},
        {**good, "probable": "0.5"},
    ]
    for table in bad_tables:
        with pytest.raises(ValueError):
            A.validate_status_chance(table)


@pytest.mark.parametrize(
    "league, state, basis",
    [
        ("euroleague", None, "noEntry"),
        ("euroleague", "submitted", "noEntry"),
        ("nba", "submitted", "notOnSubmittedReport"),
        ("nba", "notYetSubmitted", "teamReportPending"),
        ("nba", "noReport", "noReportPublished"),
        ("nba", None, "noReportPublished"),
    ],
)
def test_the_assumed_available_basis(league, state, basis) -> None:
    assert A.assumed_available_basis(league, state) == basis
    assert basis in A.ASSUMED_BASES


def test_the_assumed_available_basis_rejects_unknowns() -> None:
    with pytest.raises(ValueError):
        A.assumed_available_basis("wnba")
    with pytest.raises(ValueError):
        A.assumed_available_basis("nba", "pending")


# ----------------------------------------------------------------------------- time


def test_age_is_whole_minutes_from_the_source_time() -> None:
    published = NOW - timedelta(minutes=90, seconds=59)
    assert A.age_minutes(published, NOW) == 90  # floors, never rounds up
    assert A.age_minutes(NOW, NOW) == 0
    assert A.age_minutes(NOW - timedelta(days=7), NOW) == 7 * 24 * 60


def test_age_mixes_naive_utc_and_aware_datetimes() -> None:
    naive = datetime(2026, 10, 4, 10, 0)
    assert A.age_minutes(naive, NOW) == 120
    berlin = datetime(2026, 10, 4, 13, 0, tzinfo=BERLIN)  # 11:00 UTC
    assert A.age_minutes(berlin, NOW) == 60


def test_a_source_dated_in_the_future_is_age_zero_not_negative() -> None:
    assert A.age_minutes(NOW + timedelta(hours=3), NOW) == 0


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Long-term", True),
        ("long term", True),
        ("LONG-TERM absence", True),
        ("Indefinite", True),
        ("Out for the season", True),
        ("Season-ending surgery", True),
        ("surgery on 27 Sep", True),
        ("Rounds 2-4", False),
        ("Around 8 Oct", False),
        ("a few days", False),
        ("", False),
        (None, False),
    ],
)
def test_long_term_marker(text, expected) -> None:
    assert A.is_long_term(text) is expected


# ----------------------------------------------------------------- expected return


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Rounds 2-4", (2, 4, None)),
        ("Rounds 1-6", (1, 6, None)),
        ("Round 5", (5, 5, None)),
        ("Rounds 3", (3, 3, None)),
        ("round 2 – 4", (2, 4, None)),
        ("  ROUNDS 2-3  ", (2, 3, None)),
        ("Rounds 4-4", (4, 4, None)),
    ],
)
def test_rounds_are_parsed(text, expected) -> None:
    assert tuple(A.parse_expected_return(text)) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Likely Round 4",  # a hedge the structured columns cannot carry
        "After Round 3",
        "Around Round 4-5",
        "Not for Round 3",
        "Out for Round 3",
        "At least six weeks",
        "Expected back",
        "Rounds 4-2",  # backwards
        "Round 0",
        "Rounds 0-2",
        "Rounds",
        "Round x",
        "Rounds 2-4 or later",
        "",
        None,
    ],
)
def test_everything_else_parses_to_nothing(text) -> None:
    assert tuple(A.parse_expected_return(text, source_date=date(2026, 9, 30))) == (None, None, None)


def test_around_a_date_takes_the_sources_year() -> None:
    parsed = A.parse_expected_return("Around 8 Oct", source_date=date(2026, 9, 30))
    assert parsed == A.ExpectedReturn(None, None, date(2026, 10, 8))
    assert A.parse_expected_return("around 8 october", source_date=date(2026, 9, 30)).date == date(
        2026, 10, 8
    )


def test_around_a_date_rolls_into_the_next_year_across_new_year() -> None:
    parsed = A.parse_expected_return("Around 3 Jan", source_date=date(2026, 12, 28))
    assert parsed.date == date(2027, 1, 3)
    # a date a little before the source date stays in the source year (a stale hint, not next year)
    parsed = A.parse_expected_return("Around 25 Sep", source_date=date(2026, 9, 30))
    assert parsed.date == date(2026, 9, 25)


def test_around_a_date_needs_a_source_date_and_a_real_date() -> None:
    assert A.parse_expected_return("Around 8 Oct").date is None  # never guess a year
    assert A.parse_expected_return("Around 31 Feb", source_date=date(2026, 9, 30)).date is None
    assert A.parse_expected_return("Around 8 Xyz", source_date=date(2026, 9, 30)).date is None
    assert A.parse_expected_return("Around 0 Oct", source_date=date(2026, 9, 30)).date is None


# ---------------------------------------------------------------------- in force (a)


def test_a_return_date_has_passed_only_once_the_local_day_is_later() -> None:
    e = entry(expected_return_date=date(2026, 10, 4))
    assert A.entry_in_force(e, as_of=NOW).in_force  # the day itself is not "passed"
    tomorrow = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    verdict = A.entry_in_force(e, as_of=tomorrow)
    assert verdict == A.ForceVerdict(False, "returnDatePassed")


def test_the_return_date_is_compared_in_the_leagues_own_calendar() -> None:
    e = entry(expected_return_date=date(2026, 10, 8), published=datetime(2026, 9, 30, tzinfo=UTC))
    late_evening_utc = datetime(2026, 10, 8, 22, 30, tzinfo=UTC)  # 00:30 on the 9th in Berlin
    assert A.entry_in_force(e, as_of=late_evening_utc).in_force  # UTC day is still the 8th
    assert A.entry_in_force(e, as_of=late_evening_utc, tz=BERLIN) == A.ForceVerdict(
        False, "returnDatePassed"
    )


def test_a_round_range_is_in_force_until_its_last_round_has_been_played() -> None:
    """The documented reading of rule (a): 'Rounds 2-4' is the span of the absence."""
    e = entry(expected_return_round_from=2, expected_return_round_to=4)
    round_end = {
        2: datetime(2026, 10, 1, 20, 0, tzinfo=UTC),
        3: datetime(2026, 10, 8, 20, 0, tzinfo=UTC),
        4: datetime(2026, 10, 15, 20, 0, tzinfo=UTC),
    }
    # round 2's last game has been played, and the entry is still in force: the range is the
    # absence, and it has two rounds left to run
    assert A.entry_in_force(e, as_of=NOW, round_end_at=round_end).in_force
    during_round_4 = datetime(2026, 10, 15, 19, 59, tzinfo=UTC)
    assert A.entry_in_force(
        entry(
            published=during_round_4 - timedelta(days=1),
            expected_return_round_from=2,
            expected_return_round_to=4,
        ),
        as_of=during_round_4,
        round_end_at=round_end,
    ).in_force
    after = datetime(2026, 10, 15, 20, 0, tzinfo=UTC)  # the last game of round 4 has been played
    assert A.entry_in_force(
        entry(
            published=after - timedelta(days=1),
            expected_return_round_from=2,
            expected_return_round_to=4,
        ),
        as_of=after,
        round_end_at=round_end,
    ) == A.ForceVerdict(False, "returnRoundPassed")


def test_a_single_round_uses_that_round() -> None:
    e = entry(expected_return_round_from=5, expected_return_round_to=None)
    ended = {5: NOW - timedelta(minutes=1)}
    assert A.entry_in_force(e, as_of=NOW, round_end_at=ended) == A.ForceVerdict(
        False, "returnRoundPassed"
    )
    pending = {5: NOW + timedelta(minutes=1)}
    assert A.entry_in_force(e, as_of=NOW, round_end_at=pending).in_force


def test_round_resolution_may_be_a_callable_and_may_be_unknown() -> None:
    e = entry(expected_return_round_from=2, expected_return_round_to=3)
    calls = []

    def resolver(number):
        calls.append(number)
        return NOW - timedelta(days=1) if number == 3 else None

    assert A.entry_in_force(e, as_of=NOW, round_end_at=resolver).reason == "returnRoundPassed"
    assert calls == [3]
    # no calendar, or a calendar that does not know the round: the clause cannot fire
    assert A.entry_in_force(e, as_of=NOW).in_force
    assert A.entry_in_force(e, as_of=NOW, round_end_at={}).in_force
    assert A.entry_in_force(e, as_of=NOW, round_end_at=lambda n: None).in_force


# ---------------------------------------------------------------------- in force (b)


def test_a_box_score_after_the_entry_supersedes_it() -> None:
    published = NOW - timedelta(days=2)
    e = entry(published=published)
    assert A.entry_in_force(e, as_of=NOW, last_played_at=published - timedelta(days=1)).in_force
    assert A.entry_in_force(e, as_of=NOW, last_played_at=published).in_force  # not strictly after
    verdict = A.entry_in_force(e, as_of=NOW, last_played_at=published + timedelta(seconds=1))
    assert verdict == A.ForceVerdict(False, "playedSince")
    assert A.entry_in_force(e, as_of=NOW, last_played_at=None).in_force


def test_a_box_score_supersedes_even_a_long_term_entry() -> None:
    e = entry(published=NOW - timedelta(days=30), expected_return_text="Season-ending surgery")
    assert A.entry_in_force(e, as_of=NOW).in_force
    assert A.entry_in_force(e, as_of=NOW, last_played_at=NOW - timedelta(days=1)).reason == (
        "playedSince"
    )


# ---------------------------------------------------------------------- in force (c)


def test_an_entry_older_than_fourteen_days_is_out_of_force() -> None:
    fourteen = timedelta(days=14)
    e = entry(published=NOW - fourteen)
    assert A.entry_in_force(e, as_of=NOW).in_force  # exactly 14 days: has not yet passed
    older = entry(published=NOW - fourteen - timedelta(seconds=1))
    assert A.entry_in_force(older, as_of=NOW) == A.ForceVerdict(False, "tooOld")


@pytest.mark.parametrize(
    "text", ["Long-term", "long term", "Indefinite", "Out for the season", "knee surgery"]
)
def test_a_long_term_entry_never_ages_out(text) -> None:
    e = entry(published=NOW - timedelta(days=200), expected_return_text=text)
    assert A.entry_in_force(e, as_of=NOW).in_force


def test_a_non_long_term_text_does_not_exempt() -> None:
    e = entry(published=NOW - timedelta(days=15), expected_return_text="Rounds 2-4")
    assert A.entry_in_force(e, as_of=NOW).reason == "tooOld"


@pytest.mark.parametrize(
    ("text", "horizon"),
    [
        ("Target: November", date(2026, 11, 30)),
        ("Until November", date(2026, 11, 30)),
        ("Late October or early November", date(2026, 11, 30)),
        ("Mid-October", date(2026, 10, 31)),
        ("At least six weeks", date(2026, 10, 30)),
        ("6 weeks", date(2026, 10, 30)),
        ("2-3 weeks", date(2026, 10, 9)),
        ("Several weeks", date(2026, 10, 9)),
        ("Out for 10 days", date(2026, 9, 28)),
        ("About a month", date(2026, 10, 19)),
        ("January", date(2027, 1, 31)),  # a month already past this year means next year's
        ("July", date(2027, 7, 31)),
        ("August", date(2026, 8, 31)),  # ended under a month before: this year, already past
    ],
)
def test_the_return_horizon_reads_months_and_durations(text, horizon) -> None:
    assert A.return_horizon(text, source_date=date(2026, 9, 18)) == horizon


@pytest.mark.parametrize(
    "text", ["Rounds 2-4", "Likely Round 4", "Day-to-day", "Questionable", "May return", ""]
)
def test_texts_with_no_month_or_duration_have_no_horizon(text) -> None:
    assert A.return_horizon(text, source_date=date(2026, 9, 18)) is None
    assert A.return_horizon("Until November", source_date=None) is None


def test_an_entry_whose_text_names_a_month_still_running_stays_in_force() -> None:
    """``Until November`` published 17 days ago is still the source's word: rule (c) waits for
    the end of November, then ends it."""
    published = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)
    e = entry(published=published, expected_return_text="Until November")
    assert A.entry_in_force(e, as_of=NOW).in_force
    assert A.entry_in_force(e, as_of=datetime(2026, 11, 30, 21, 0, tzinfo=UTC)).in_force
    late = A.entry_in_force(e, as_of=datetime(2026, 12, 1, 0, 30, tzinfo=UTC))
    assert late == A.ForceVerdict(False, "tooOld")
    # the league's day decides: 23:30 UTC on 30 November is already 1 December in Berlin
    berlin = A.entry_in_force(e, as_of=datetime(2026, 11, 30, 23, 30, tzinfo=UTC), tz=BERLIN)
    assert berlin.reason == "tooOld"


def test_a_duration_keeps_an_entry_in_force_only_until_it_runs_out() -> None:
    published = NOW - timedelta(days=20)
    e = entry(published=published, expected_return_text="At least six weeks")
    assert A.entry_in_force(e, as_of=NOW).in_force
    assert A.entry_in_force(e, as_of=published + timedelta(days=43)).reason == "tooOld"
    # a horizon never overrides the other rules
    assert A.entry_in_force(e, as_of=NOW, last_played_at=NOW - timedelta(days=1)).reason == (
        "playedSince"
    )


def test_rules_are_reported_in_a_then_b_then_c_order() -> None:
    old = NOW - timedelta(days=20)
    e = entry(published=old, expected_return_date=date(2026, 9, 1))
    assert A.entry_in_force(e, as_of=NOW, last_played_at=NOW - timedelta(days=1)).reason == (
        "returnDatePassed"
    )
    e = entry(published=old)
    assert A.entry_in_force(e, as_of=NOW, last_played_at=NOW - timedelta(days=1)).reason == (
        "playedSince"
    )
    assert A.entry_in_force(e, as_of=NOW).reason == "tooOld"


def test_naive_datetimes_are_taken_as_utc() -> None:
    e = entry(published=datetime(2026, 9, 20, 12, 0))
    assert A.entry_in_force(e, as_of=datetime(2026, 10, 4, 12, 0)).in_force
    assert not A.entry_in_force(e, as_of=datetime(2026, 10, 4, 12, 1)).in_force


# ------------------------------------------------------------------------ staleness


def test_out_of_force_is_always_stale() -> None:
    e = entry(published=NOW - timedelta(minutes=1))
    verdict = A.ForceVerdict(False, "playedSince")
    assert A.entry_is_stale(e, verdict, profile=LP.NBA, as_of=NOW)
    assert A.entry_is_stale(e, verdict, profile=LP.EUROLEAGUE, as_of=NOW)


IN_FORCE = A.ForceVerdict(True, None)


def test_euroleague_staleness_is_seven_days_or_the_team_has_played() -> None:
    seven = entry(published=NOW - timedelta(days=7))
    assert not A.entry_is_stale(seven, IN_FORCE, profile=LP.EUROLEAGUE, as_of=NOW)
    eight_ish = entry(published=NOW - timedelta(days=7, minutes=1))
    assert A.entry_is_stale(eight_ish, IN_FORCE, profile=LP.EUROLEAGUE, as_of=NOW)
    fresh = entry(published=NOW - timedelta(hours=2))
    assert not A.entry_is_stale(fresh, IN_FORCE, profile=LP.EUROLEAGUE, as_of=NOW)
    assert A.entry_is_stale(
        fresh, IN_FORCE, profile=LP.EUROLEAGUE, as_of=NOW, team_played_since_source=True
    )


def test_nba_staleness_is_the_snapshot_clock() -> None:
    sixty = entry(published=NOW - timedelta(minutes=60))
    sixty_one = entry(published=NOW - timedelta(minutes=61))
    kw = dict(profile=LP.NBA, as_of=NOW)
    assert not A.entry_is_stale(sixty, IN_FORCE, in_reporting_window=True, **kw)
    assert A.entry_is_stale(sixty_one, IN_FORCE, in_reporting_window=True, **kw)
    # outside a reporting window the limit is a day
    assert not A.entry_is_stale(sixty_one, IN_FORCE, in_reporting_window=False, **kw)
    day = entry(published=NOW - timedelta(hours=24))
    day_plus = entry(published=NOW - timedelta(hours=24, minutes=1))
    assert not A.entry_is_stale(day, IN_FORCE, in_reporting_window=False, **kw)
    assert A.entry_is_stale(day_plus, IN_FORCE, in_reporting_window=False, **kw)
    # when nobody says whether a window is open, the longer limit applies
    assert not A.entry_is_stale(sixty_one, IN_FORCE, **kw)
    # "the team has played" is a EuroLeague rule only
    assert not A.entry_is_stale(sixty, IN_FORCE, team_played_since_source=True, **kw)


def test_a_long_term_entry_is_stale_after_seven_days_whatever_the_league() -> None:
    kw = dict(expected_return_text="Out for the season")
    seven = entry(published=NOW - timedelta(days=7), **kw)
    over = entry(published=NOW - timedelta(days=7, minutes=1), **kw)
    for profile in (LP.NBA, LP.EUROLEAGUE):
        assert not A.entry_is_stale(seven, IN_FORCE, profile=profile, as_of=NOW)
        assert A.entry_is_stale(over, IN_FORCE, profile=profile, as_of=NOW)
    # and a long-term entry in force is still in force at 30 days, though stale
    thirty = entry(published=NOW - timedelta(days=30), **kw)
    assert A.entry_in_force(thirty, as_of=NOW).in_force
    assert A.entry_is_stale(thirty, IN_FORCE, profile=LP.EUROLEAGUE, as_of=NOW)


# ------------------------------------------------------ which entry applies to a game


def select(entries, **kwargs):
    kwargs.setdefault("as_of", NOW)
    kwargs.setdefault("game_id", "G1")
    return A.select_effective_entry(entries, **kwargs)


def test_no_entries_means_none() -> None:
    got = select([])
    assert got.entry is None and got.rule == "none" and not got.verdict.in_force


def test_an_active_override_beats_everything() -> None:
    report = entry("report", status="out", source_kind="leagueReport", game_id="G1")
    override = entry(
        "ov",
        status="available",
        is_override=True,
        source_kind="manual",
        recorded_at=NOW - timedelta(minutes=30),
    )
    got = select([report, override])
    assert got.entry is override and got.rule == "override" and got.verdict.in_force


def test_a_cleared_override_does_not_apply_but_a_future_clearing_does_not_stop_it() -> None:
    cleared = entry(
        "ov",
        is_override=True,
        recorded_at=NOW - timedelta(hours=2),
        cleared_at=NOW - timedelta(minutes=1),
        status="out",
    )
    assert select([cleared]).rule == "none"
    clearing_later = entry(
        "ov",
        is_override=True,
        recorded_at=NOW - timedelta(hours=2),
        cleared_at=NOW + timedelta(hours=1),
        status="out",
    )
    assert select([clearing_later]).entry is clearing_later


def test_an_override_cleared_at_the_instant_asked_about_is_already_cleared() -> None:
    cleared = entry(
        "ov", is_override=True, recorded_at=NOW - timedelta(hours=2), cleared_at=NOW, status="out"
    )
    assert select([cleared]).rule == "none"
    just_after = entry(
        "ov",
        is_override=True,
        recorded_at=NOW - timedelta(hours=2),
        cleared_at=NOW + timedelta(seconds=1),
        status="out",
    )
    assert select([just_after]).rule == "override"


def test_an_override_ends_when_a_newer_league_report_arrives() -> None:
    recorded = NOW - timedelta(hours=3)
    override = entry("ov", is_override=True, recorded_at=recorded, status="out")
    assert select([override], newest_league_report_at=None).rule == "override"
    assert select([override], newest_league_report_at=recorded - timedelta(minutes=1)).rule == (
        "override"
    )
    assert select([override], newest_league_report_at=recorded).rule == "override"  # not newer
    superseded = select([override], newest_league_report_at=recorded + timedelta(minutes=1))
    assert superseded.rule == "none"


def test_an_override_for_another_game_does_not_apply_and_a_player_wide_one_does() -> None:
    other = entry("ov-other", is_override=True, game_id="G2", recorded_at=NOW - timedelta(hours=1))
    assert select([other]).rule == "none"
    wide = entry("ov-wide", is_override=True, game_id=None, recorded_at=NOW - timedelta(hours=1))
    assert select([wide], game_id="G1").entry is wide
    assert select([wide], game_id="G7").entry is wide
    # when the game is asked about specifically, the game's own override is the one
    own = entry("ov-own", is_override=True, game_id="G1", recorded_at=NOW - timedelta(hours=2))
    assert select([wide, own], game_id="G1").entry is wide  # newest override wins


def test_the_newest_of_several_overrides_wins() -> None:
    older = entry("a", is_override=True, recorded_at=NOW - timedelta(hours=5), status="out")
    newer = entry("b", is_override=True, recorded_at=NOW - timedelta(hours=1), status="probable")
    assert select([older, newer]).entry is newer


def test_the_newest_entry_for_the_game_wins_by_source_publication_time() -> None:
    old = entry("old", game_id="G1", published=NOW - timedelta(hours=9), status="out")
    new = entry("new", game_id="G1", published=NOW - timedelta(hours=2), status="probable")
    got = select([old, new])
    assert got.entry is new and got.rule == "gameEntry"
    # recorded_at does not decide it; the source's own time does
    late_recorded = entry(
        "late",
        game_id="G1",
        published=NOW - timedelta(hours=20),
        recorded_at=NOW - timedelta(minutes=5),
    )
    assert select([late_recorded, new]).entry is new


def test_a_game_entry_beats_a_newer_player_level_entry() -> None:
    game_entry = entry("g", game_id="G1", published=NOW - timedelta(hours=8))
    player_entry = entry("p", game_id=None, published=NOW - timedelta(hours=1))
    assert select([game_entry, player_entry]).entry is game_entry


def test_a_game_entry_that_is_out_of_force_is_not_replaced_by_a_vaguer_one() -> None:
    stale = entry("g", game_id="G1", published=NOW - timedelta(days=20))
    vague = entry("p", game_id=None, published=NOW - timedelta(days=1))
    got = select([stale, vague])
    assert got.entry is stale and got.rule == "gameEntry"
    assert got.verdict == A.ForceVerdict(False, "tooOld")


def test_entries_for_other_games_are_not_candidates() -> None:
    elsewhere = entry("g2", game_id="G2", published=NOW - timedelta(hours=1))
    assert select([elsewhere], game_id="G1").rule == "none"


def test_the_player_level_entry_must_be_in_force() -> None:
    newest_but_returned = entry(
        "n", published=NOW - timedelta(days=1), expected_return_date=date(2026, 10, 1)
    )
    older_in_force = entry("o", published=NOW - timedelta(days=2))
    got = select([newest_but_returned, older_in_force], tz=UTC)
    assert got.entry is older_in_force and got.rule == "playerEntry" and got.verdict.in_force
    only_stale = entry("s", published=NOW - timedelta(days=30))
    assert select([only_stale]).rule == "none"


def test_a_played_game_after_the_entry_removes_it_from_the_player_level() -> None:
    e = entry("p", published=NOW - timedelta(days=3))
    assert select([e]).entry is e
    assert select([e], last_played_at=NOW - timedelta(days=1)).rule == "none"


def test_retractions_remove_the_retracted_entry_and_are_never_candidates() -> None:
    target = entry("t", game_id="G1", published=NOW - timedelta(hours=5), status="out")
    retraction = entry(
        "r", game_id="G1", retracts="t", published=NOW - timedelta(hours=1), status="available"
    )
    older = entry("o", game_id=None, published=NOW - timedelta(hours=9), status="doubtful")
    got = select([target, retraction, older])
    assert got.entry is older and got.rule == "playerEntry"
    assert select([target, retraction]).rule == "none"
    # a retracted override is gone too
    override = entry("ov", is_override=True, recorded_at=NOW - timedelta(hours=1))
    retract_ov = entry("rv", retracts="ov", published=NOW - timedelta(minutes=5))
    assert select([override, retract_ov]).rule == "none"


def test_information_from_the_future_is_not_yet_known() -> None:
    future = entry("f", game_id="G1", published=NOW + timedelta(minutes=1))
    assert select([future]).rule == "none"
    same_instant = entry("s", game_id="G1", published=NOW)
    assert (
        select([same_instant]).rule == "none"
    )  # strictly before as_of, as a rebuilt "pre-tip" input
    future_override = entry("fo", is_override=True, recorded_at=NOW + timedelta(seconds=1))
    assert select([future_override]).rule == "none"


def test_asking_without_a_game_skips_the_game_step() -> None:
    game_entry = entry("g", game_id="G1", published=NOW - timedelta(hours=1))
    player_entry = entry("p", game_id=None, published=NOW - timedelta(hours=5))
    assert select([game_entry, player_entry], game_id=None).entry is player_entry


def test_ties_are_broken_by_recording_time_then_id() -> None:
    t = NOW - timedelta(hours=3)
    a = entry("a", game_id="G1", published=t, recorded_at=NOW - timedelta(hours=2))
    b = entry("b", game_id="G1", published=t, recorded_at=NOW - timedelta(hours=1))
    assert select([a, b]).entry is b
    assert select([b, a]).entry is b
    c = entry("c", game_id="G1", published=t, recorded_at=NOW - timedelta(hours=1))
    assert select([b, c]).entry is c  # same time: the larger id, deterministically
    assert select([c, b]).entry is c


def test_integer_and_string_keys_both_work() -> None:
    e = entry(7, game_id=1610612738, player_key=42, published=NOW - timedelta(hours=1))
    assert select([e], game_id=1610612738).entry is e


# ----------------------------------------------------------------------- the contract


def test_the_vocabulary_contract_is_json_ready_and_complete() -> None:
    contract = A.vocabulary()
    assert json.loads(json.dumps(contract)) == contract
    assert [s["key"] for s in contract["statuses"]] == list(A.STATUSES)
    assert contract["statusChance"]["probable"] == 0.85
    assert contract["inForce"]["maxAgeDays"] == 14
    assert contract["inForce"]["longTermStaleDays"] == 7
    assert contract["staleAfter"]["nba"]["inWindowMinutes"] == 60
    assert contract["staleAfter"]["nba"]["outsideWindowMinutes"] == 1440
    assert contract["staleAfter"]["euroleague"]["maxAgeDays"] == 7
    assert contract["staleAfter"]["euroleague"]["staleWhenTeamHasPlayed"] is True
    assert A.LONG_TERM_PATTERN.pattern == contract["inForce"]["longTermPattern"]
