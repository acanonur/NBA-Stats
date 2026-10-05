"""The five parsers: what they accept, and, more to the point, what they refuse.

The fixtures under ``tests/fixtures/euroleague/authored/`` copy the *shapes* of the documented
service payloads (full registration objects, ``local``/``road``, a ``team`` row beside a ``total``
row) with invented clubs, fantasy-style names and invented numbers. Nothing real is committed.
Real recordings, if any have been made on the Mac, are read from ``backend/tests/local/`` and
``HARDWOOD_DATA_DIR/recordings/euroleague`` by the last test here, which skips when there are none.

The tests are grouped by the question they answer:

* does the documented shape parse, into the right values? (the happy paths)
* is **null kept as null**? (an absent stat is ``None``, never ``0``)
* does a wrong shape become an explicit ``Unreadable`` that names the path? (fail closed)
* can *any* single wrong leaf make the parser raise something other than ``Unreadable``? (a fuzz)
"""

from __future__ import annotations

import copy
import json
import os
import random
from pathlib import Path
from typing import Any

import pytest

from nbastats.euroleague.ingest import parse
from nbastats.euroleague.ingest.parse import Unreadable

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "euroleague" / "authored"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def box() -> dict[str, Any]:
    return load("box_game_1.json")


@pytest.fixture()
def games1() -> dict[str, Any]:
    return load("games_round_1.json")


# ----------------------------------------------------------------------------- primitives


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        (0, 0),
        (0.0, 0),
        (23.0, 23),
        ("23", 23),
        (" 7 ", 7),
    ],
)
def test_as_count_accepts_whole_numbers_and_blanks(value: Any, expected: int | None) -> None:
    assert parse.as_count(value) == expected


@pytest.mark.parametrize("value", [3.5, "abc", True, False, [], {}, -1, float("nan"), float("inf")])
def test_as_count_rejects_what_is_not_a_whole_count(value: Any) -> None:
    with pytest.raises(Unreadable):
        parse.as_count(value, "stats.points")


def test_as_count_allows_negatives_when_asked_and_reports_the_path() -> None:
    assert parse.as_count(-12, minimum=None) == -12
    with pytest.raises(Unreadable) as caught:
        parse.as_count(-1, "local.players[0].stats.points")
    assert caught.value.path == "local.players[0].stats.points"
    assert "0 or more" in caught.value.reason


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        (0.0, 0),
        (2412.0, 2412),
        (2411.6, 2412),
        ("754", 754),
        ("12:34", 754),
        ("0:05", 5),
    ],
)
def test_as_seconds(value: Any, expected: int | None) -> None:
    assert parse.as_seconds(value) == expected


@pytest.mark.parametrize("value", [-1.0, "12:75", "soon", True, []])
def test_as_seconds_rejects_nonsense(value: Any) -> None:
    with pytest.raises(Unreadable):
        parse.as_seconds(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        ("No", False),
    ],
)
def test_as_bool(value: Any, expected: bool | None) -> None:
    assert parse.as_bool(value) is expected


@pytest.mark.parametrize("value", [2, "maybe", [], 0.5])
def test_as_bool_rejects_ambiguity(value: Any) -> None:
    with pytest.raises(Unreadable):
        parse.as_bool(value)


def test_as_str_keeps_digits_and_rejects_booleans() -> None:
    assert parse.as_str(10) == "10"
    assert parse.as_str("  Harbour ") == "Harbour"
    assert parse.as_str("") is None
    with pytest.raises(Unreadable):
        parse.as_str(True)
    with pytest.raises(Unreadable):
        parse.as_str({"a": 1})


# --------------------------------------------------------------------------- decode_json


def test_decode_json_reads_a_byte_order_mark_and_whitespace() -> None:
    assert parse.decode_json(b'\xef\xbb\xbf  {"a": 1} \n') == {"a": 1}


def test_decode_json_empty_body_is_unreadable() -> None:
    with pytest.raises(Unreadable, match="is empty"):
        parse.decode_json(b"   ")


def test_decode_json_names_an_html_page() -> None:
    page = b"<html><head><title>Attention Required! | Cloudflare</title></head>"
    with pytest.raises(Unreadable) as caught:
        parse.decode_json(page, what="the E2 response")
    assert "not JSON" in caught.value.reason
    assert "<html>" in caught.value.reason  # the first characters are shown
    assert "E2 response" in caught.value.reason


# -------------------------------------------------------------------------------- names


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("THISTLEWOOD, TOBIAS", "Tobias Thistlewood"),
        ("O'HALLOW, MAREK", "Marek O'Hallow"),
        ("FENWICK-HALE, ANDRO", "Andro Fenwick-Hale"),
        ("DE LA CRUZ, JUAN", "Juan De La Cruz"),
        ("Tobias Thistlewood", "Tobias Thistlewood"),  # already mixed case: untouched
        ("Thistlewood, Tobias", "Tobias Thistlewood"),  # re-ordered only
        ("  PLAIN   NAME ", "Plain Name"),
        ("", ""),
    ],
)
def test_friendly_name(raw: str, expected: str) -> None:
    assert parse.friendly_name(raw) == expected


def test_person_variants_cover_both_orders_the_passport_the_abbreviation_and_an_alias() -> None:
    person = parse.PersonInfo(
        code="900001",
        official_name="THISTLEWOOD, TOBIAS",
        name="Tobias Thistlewood",
        abbreviated_name="Thistlewood, T.",
        alias="Tobi Thistlewood",
        passport_name="TOBIAS ALDER",
        passport_surname="THISTLEWOOD",
    )
    variants = parse.person_variants(person)
    assert {"thistlewood tobias", "tobias thistlewood"} <= variants
    assert "tobias alder thistlewood" in variants
    assert "tobi thistlewood" in variants
    assert {"t thistlewood", "thistlewood t"} <= variants  # the service's own abbreviation


def test_person_variants_ignore_a_one_word_alias() -> None:
    person = parse.PersonInfo(
        code="1", official_name="MARROWBY, ILIR", name="Ilir Marrowby", alias="Marrowby"
    )
    assert "marrowby" not in parse.person_variants(person)


def test_stored_name_variants_are_the_name_and_the_published_abbreviation() -> None:
    variants = parse.stored_name_variants("Ilir Marrowby", "Marrowby, I.")
    assert variants == {"ilir marrowby", "i marrowby", "marrowby i"}
    assert parse.stored_name_variants("Ilir Marrowby", "Marrowby") == {"ilir marrowby"}
    assert parse.stored_name_variants(None, None) == frozenset()


# ------------------------------------------------------------------------------- rounds


def test_rounds_parse_the_authored_calendar() -> None:
    parsed = parse.parse_rounds(load("rounds.json"), expect_season="E2031")
    assert [(r.round, r.phase_code) for r in parsed.items] == [
        (1, "RS"),
        (2, "RS"),
        (3, "RS"),
        (4, "PO"),
    ]
    assert parsed.complete and not parsed.rejected
    first = parsed.items[0]
    assert first.first_start is not None and first.first_start.isoformat() == "2031-10-02T00:00:00"
    assert first.name == "Round 1"


def test_a_bare_list_is_as_good_as_the_data_envelope() -> None:
    wrapped = load("rounds.json")
    assert parse.parse_rounds(wrapped["data"]).items == parse.parse_rounds(wrapped).items


def test_a_total_larger_than_the_items_is_incomplete_not_complete() -> None:
    payload = load("rounds.json")
    payload["total"] = 47
    parsed = parse.parse_rounds(payload)
    assert len(parsed.items) == 4
    assert parsed.complete is False


def test_round_of_another_season_is_rejected_when_one_is_expected() -> None:
    payload = load("rounds.json")
    payload["data"][1]["seasonCode"] = "E2030"
    parsed = parse.parse_rounds(payload, expect_season="E2031")
    assert [r.round for r in parsed.items] == [1, 3, 4]
    assert "E2030" in parsed.rejected[0].reason


def test_every_round_rejected_is_an_unreadable_payload() -> None:
    payload = {"data": [{"roundNumber": 1}, {"roundNumber": 2}]}
    with pytest.raises(Unreadable) as caught:
        parse.parse_rounds(payload)
    assert "all 2 rounds were rejected" in caught.value.reason
    assert "'round' is missing" in caught.value.reason
    assert "roundNumber" in caught.value.reason  # the key it found instead


def test_an_object_without_a_data_list_names_the_keys_it_found() -> None:
    with pytest.raises(Unreadable) as caught:
        parse.parse_rounds({"items": [], "count": 0})
    assert "items" in caught.value.reason and "count" in caught.value.reason


# -------------------------------------------------------------------------------- clubs


def test_clubs_are_keyed_by_code_never_by_tv_code() -> None:
    parsed = parse.parse_clubs(load("clubs.json"))
    harbour = next(c for c in parsed.items if c.code == "ZZA")
    assert (harbour.tv_code, harbour.name, harbour.short_name) == (
        "ZHB",
        "Zenith Harbour BC",
        "Harbour",
    )
    assert harbour.country_code == "UTO" and harbour.venue_code == "ZHD"
    assert {c.code for c in parsed.items} == {"ZZA", "ZZB", "ZZC", "ZZD"}


def test_a_club_without_a_code_is_rejected_and_the_rest_kept() -> None:
    payload = load("clubs.json")
    del payload["data"][2]["code"]
    parsed = parse.parse_clubs(payload)
    assert {c.code for c in parsed.items} == {"ZZA", "ZZB", "ZZD"}
    assert "required key 'code' is missing" in parsed.rejected[0].reason
    assert parsed.rejected[0].path == "data[2].code"


# -------------------------------------------------------------------------------- games


def test_final_games_parse_with_scores_partials_tipoff_and_berlin_date(
    games1: dict[str, Any],
) -> None:
    parsed = parse.parse_games(games1, expect_season="E2031", expect_round=1)
    first = parsed.items[0]
    assert (first.game_code, first.round, first.phase_code, first.status) == (1, 1, "RS", "final")
    assert (first.home.club.code, first.home.score) == ("ZZA", 84)
    assert (first.away.club.code, first.away.score) == ("ZZB", 79)
    assert first.home.partials == (21, 19, 20, 24)
    assert sum(first.away.partials or ()) == 79
    assert first.ot_periods == 0
    assert first.tipoff_utc is not None and first.tipoff_utc.isoformat() == "2031-10-02T17:45:00"
    assert first.tipoff_utc.tzinfo is None  # naive UTC, like every timestamp column
    assert first.game_date.isoformat() == "2031-10-02"
    assert (first.venue_name, first.venue_code) == ("Harbour Dome", "ZHD")
    assert first.attendance == 8120 and first.is_neutral is False
    assert first.issues == ()


def test_game_date_is_the_berlin_day_of_the_instant_not_the_utc_day() -> None:
    payload = load("games_round_1.json")
    payload["data"][0]["utcDate"] = "2031-10-02T23:30:00Z"  # 01:30 on the 3rd in Berlin (CEST)
    game = parse.parse_games(payload).items[0]
    assert game.game_date.isoformat() == "2031-10-03"
    winter = load("games_round_1.json")
    winter["data"][0]["utcDate"] = "2031-12-02T23:30:00Z"  # CET, UTC+1: still the 3rd
    assert parse.parse_games(winter).items[0].game_date.isoformat() == "2031-12-03"


def test_overtime_games_carry_extra_periods() -> None:
    game = parse.parse_games(load("games_round_2.json")).items[0]
    assert game.home.partials == (20, 22, 19, 22, 8)
    assert game.ot_periods == 1


def test_extra_periods_are_ordered_naturally_not_alphabetically() -> None:
    payload = load("games_round_2.json")
    side = payload["data"][0]["local"]["partials"]
    side["extraPeriods"] = {f"extraPeriod{n}": n for n in (10, 2, 1, 3)}
    other = payload["data"][0]["road"]["partials"]
    other["extraPeriods"] = {f"extraPeriod{n}": n for n in (10, 2, 1, 3)}
    game = parse.parse_games(payload).items[0]
    assert game.home.partials[4:] == (1, 2, 3, 10)


def test_scheduled_games_have_no_score_and_are_not_final() -> None:
    games = parse.parse_games(load("games_round_2.json")).items
    scheduled = games[1]
    assert scheduled.status == "scheduled" and scheduled.played is False
    assert scheduled.home.score is None and scheduled.home.partials is None
    assert scheduled.attendance is None
    assert scheduled.tipoff_utc is not None


def test_a_score_on_an_unplayed_game_does_not_make_it_final() -> None:
    payload = load("games_round_2.json")
    payload["data"][1]["local"]["score"] = 12  # in progress
    payload["data"][1]["road"]["score"] = 9
    game = parse.parse_games(payload).items[1]
    assert game.status == "scheduled"


def test_played_without_a_score_is_not_final_and_says_why() -> None:
    payload = load("games_round_1.json")
    payload["data"][0]["local"]["score"] = None
    game = parse.parse_games(payload).items[0]
    assert game.status == "scheduled"
    assert any("a score is missing" in issue for issue in game.issues)


def test_a_tied_score_is_never_a_final() -> None:
    payload = load("games_round_1.json")
    payload["data"][0]["local"]["score"] = 80
    payload["data"][0]["road"]["score"] = 80
    game = parse.parse_games(payload).items[0]
    assert game.status == "scheduled"
    assert any("tied score 80-80" in issue for issue in game.issues)


@pytest.mark.parametrize("text", ["Postponed", "Cancelled", "SUSPENDED", "abandoned"])
def test_postponed_is_recognised_from_the_status_text(text: str) -> None:
    payload = load("games_round_2.json")
    payload["data"][1]["gameStatus"] = text
    assert parse.parse_games(payload).items[1].status == "postponed"


def test_a_missing_played_key_is_a_note_for_one_game_and_unreadable_for_all() -> None:
    payload = load("games_round_1.json")
    del payload["data"][0]["played"]
    parsed = parse.parse_games(payload)
    assert parse.NO_PLAYED_FLAG in parsed.items[0].issues
    assert parsed.items[1].status == "final"
    del payload["data"][1]["played"]
    with pytest.raises(Unreadable) as caught:
        parse.parse_games(payload)
    assert "no result could ever be recognised" in caught.value.reason
    assert caught.value.path == "data[].played"


def test_played_null_is_not_the_same_as_the_key_being_absent() -> None:
    payload = load("games_round_2.json")
    for entry in payload["data"]:
        entry["played"] = None
    parsed = parse.parse_games(payload)  # future rounds may well say null: not a shape change
    assert [g.status for g in parsed.items] == ["scheduled", "scheduled"]


def test_phase_comes_from_the_game_then_the_calendar_else_the_game_is_rejected() -> None:
    payload = load("games_round_2.json")
    del payload["data"][0]["phaseType"]
    filled = parse.parse_games(payload, phase_by_round={2: "PO"})
    assert filled.items[0].phase_code == "PO"
    parsed = parse.parse_games(payload)
    assert len(parsed.items) == 1
    assert "no phaseType.code" in parsed.rejected[0].reason


def test_an_unknown_phase_is_rejected_not_defaulted() -> None:
    payload = load("games_round_2.json")
    payload["data"][0]["phaseType"]["code"] = "XX"
    parsed = parse.parse_games(payload)
    assert "phase 'XX'" in parsed.rejected[0].reason


def test_a_game_of_another_round_or_season_than_requested_is_rejected() -> None:
    payload = load("games_round_1.json")
    assert parse.parse_games(payload, expect_round=1).items
    with pytest.raises(Unreadable):  # every game is in round 1, so asking for 7 rejects them all
        parse.parse_games(payload, expect_round=7)
    with pytest.raises(Unreadable):
        parse.parse_games(payload, expect_season="E2030")


def test_a_game_between_a_club_and_itself_is_rejected() -> None:
    payload = load("games_round_1.json")
    payload["data"][0]["road"]["club"]["code"] = "ZZA"
    parsed = parse.parse_games(payload)
    assert len(parsed.items) == 1
    assert "both sides are the club ZZA" in parsed.rejected[0].reason


def test_a_game_with_no_date_at_all_is_rejected() -> None:
    payload = load("games_round_1.json")
    for key in ("utcDate", "date", "localDate"):
        payload["data"][0].pop(key, None)
    parsed = parse.parse_games(payload)
    assert "neither utcDate nor date" in parsed.rejected[0].reason


def test_only_a_local_date_gives_a_date_and_no_tipoff() -> None:
    payload = load("games_round_1.json")
    payload["data"][0].pop("utcDate")
    game = parse.parse_games(payload).items[0]
    assert game.tipoff_utc is None and game.game_date.isoformat() == "2031-10-02"


def test_unreadable_partials_are_noted_not_fatal() -> None:
    payload = load("games_round_1.json")
    payload["data"][0]["local"]["partials"] = {"partials1": "twenty", "partials2": 1}
    game = parse.parse_games(payload).items[0]
    assert game.status == "final" and game.home.partials is None
    assert any("partials unreadable" in issue for issue in game.issues)


def test_incomplete_partials_are_not_recorded_rather_than_padded() -> None:
    payload = load("games_round_1.json")
    payload["data"][0]["local"]["partials"]["partials4"] = None
    game = parse.parse_games(payload).items[0]
    assert game.home.partials is None and game.ot_periods is None


def test_a_wrong_type_score_rejects_the_game_with_its_path() -> None:
    payload = load("games_round_1.json")
    payload["data"][1]["road"]["score"] = "seventy"
    parsed = parse.parse_games(payload)
    assert [g.game_code for g in parsed.items] == [1]
    assert parsed.rejected[0].path == "data[1].road.score"


# ---------------------------------------------------------------------------- people


def test_people_keep_only_players_and_count_the_staff() -> None:
    parsed = parse.parse_people(load("people_ZZA.json"), expect_club="ZZA", expect_season="E2031")
    assert len(parsed.items) == 14 and parsed.ignored == 2
    assert parsed.complete and not parsed.rejected
    first = parsed.items[0]
    assert first.club_code == "ZZA" and first.season_code == "E2031"
    assert first.position_code in (1, 2, 3) and first.dorsal is not None
    assert first.start_date is not None and first.start_date.isoformat() == "2031-08-15"
    assert "," not in first.person.name and first.person.name != first.person.official_name


def test_a_registration_without_a_type_cannot_be_told_from_a_coach() -> None:
    payload = load("people_ZZA.json")
    del payload["data"][0]["type"]
    parsed = parse.parse_people(payload)
    assert len(parsed.items) == 13
    assert "cannot be told from a coach" in parsed.rejected[0].reason


def test_the_person_code_prefix_is_stripped() -> None:
    payload = load("people_ZZA.json")
    payload["data"][0]["person"]["code"] = "P009549"
    assert parse.parse_people(payload).items[0].person.code == "009549"


def test_a_position_outside_one_to_three_is_not_recorded() -> None:
    payload = load("people_ZZA.json")
    payload["data"][0]["position"] = 5
    payload["data"][1]["position"] = 0
    items = parse.parse_people(payload).items
    assert items[0].position_code is None and items[1].position_code is None


def test_a_registration_for_another_club_is_rejected_when_one_is_expected() -> None:
    # every registration is ZZA's, so asking for ZZB rejects all of them: the payload is unreadable
    with pytest.raises(Unreadable):
        parse.parse_people(load("people_ZZA.json"), expect_club="ZZB")


def test_cosmetic_identity_facts_that_are_placeholders_are_simply_not_recorded() -> None:
    payload = load("people_ZZA.json")
    person = payload["data"][0]["person"]
    person["birthDate"] = "0001-01-01T00:00:00"  # the usual "unknown"
    person["height"] = 0
    person["weight"] = "n/a"
    payload["data"][0]["startDate"] = "yesterday-ish"
    info = parse.parse_people(payload).items[0]
    assert info.person.birth_date is None
    assert info.person.height_cm is None and info.person.weight_kg is None
    assert info.start_date is None


def test_an_all_staff_roster_is_empty_not_unreadable() -> None:
    payload = load("people_ZZA.json")
    payload["data"] = [e for e in payload["data"] if e["type"] != "J"]
    parsed = parse.parse_people(payload)
    assert parsed.items == () and parsed.ignored == 2


# ---------------------------------------------------------------------------- box score


def test_the_authored_box_score_parses_into_two_sides(box: dict[str, Any]) -> None:
    parsed = parse.parse_box_score(box)
    assert (parsed.home.side, parsed.away.side) == ("home", "away")
    assert (parsed.home.club_code, parsed.away.club_code) == ("ZZA", "ZZB")
    assert parsed.season_code == "E2031"
    assert len(parsed.home.players) == 12 and len(parsed.away.players) == 12
    assert sum(p.participation == "dnp" for p in parsed.home.players) == 1
    assert sum(bool(p.is_starter) for p in parsed.home.players) == 5
    assert parsed.home.coach_name == "COACH, ZZA"


def test_a_players_line_maps_every_documented_key(box: dict[str, Any]) -> None:
    parsed = parse.parse_box_score(box)
    raw = box["local"]["players"][0]
    line = parsed.home.players[0]
    s = raw["stats"]
    assert line.participation == "played" and line.seconds == int(s["timePlayed"])
    expected = {
        "pts": "points",
        "fgm2": "fieldGoalsMade2",
        "fga2": "fieldGoalsAttempted2",
        "fgm3": "fieldGoalsMade3",
        "fga3": "fieldGoalsAttempted3",
        "ftm": "freeThrowsMade",
        "fta": "freeThrowsAttempted",
        "oreb": "offensiveRebounds",
        "dreb": "defensiveRebounds",
        "reb": "totalRebounds",
        "ast": "assistances",
        "stl": "steals",
        "tov": "turnovers",
        "blk": "blocksFavour",
        "blk_against": "blocksAgainst",
        "pf": "foulsCommited",
        "fouls_drawn": "foulsReceived",
        "plus_minus": "plusMinus",
        "pir_official": "valuation",
    }
    for column, key in expected.items():
        assert line.stats[column] == int(s[key]), column
    assert line.person.code == raw["player"]["person"]["code"]
    assert line.person.official_name.isupper() and "," in line.person.official_name
    assert "," not in line.person.name and not line.person.name.isupper()
    assert line.person.abbreviated_name == raw["player"]["person"]["abbreviatedName"]
    assert line.position_code == raw["player"]["position"]
    assert line.dorsal == raw["player"]["dorsal"]


def test_a_player_who_did_not_play_has_every_stat_null_never_zero(box: dict[str, Any]) -> None:
    parsed = parse.parse_box_score(box)
    dnp = next(p for p in parsed.home.players if p.participation == "dnp")
    assert all(value is None for value in dnp.stats.values()), dnp.stats
    assert dnp.unexplained == ()
    assert dnp.person.code and dnp.dorsal  # identity facts are kept


def test_team_totals_keep_the_clock_apart_from_the_sum_of_player_time(box: dict[str, Any]) -> None:
    parsed = parse.parse_box_score(box)
    totals = parsed.home.totals
    assert totals.clock_seconds == 2400  # the game clock...
    assert sum(p.seconds or 0 for p in parsed.home.players) == 12000  # ...is not five men's time
    assert totals.stats["pts"] == 84 and totals.stats["reb"] == 38
    assert totals.team_row["reb"] == 4  # the team-rebounds row, credited to no player
    assert totals.stats["reb"] == sum(p.stats["reb"] or 0 for p in parsed.home.players) + 4


def test_an_absent_stat_is_none_not_zero(box: dict[str, Any]) -> None:
    for side in ("local", "road"):
        for entry in box[side]["players"]:
            entry["stats"].pop("foulsReceived", None)
            entry["stats"].pop("startFive", None)
    parsed = parse.parse_box_score(box)
    played = [p for p in parsed.home.players if p.participation == "played"]
    assert all(p.stats["fouls_drawn"] is None for p in played)
    assert all(p.is_starter is None for p in parsed.home.players)
    assert all(p.stats["pts"] is not None for p in played)


def test_the_digest_moves_with_a_number_and_not_with_the_envelope(box: dict[str, Any]) -> None:
    base = parse.box_digest(parse.parse_box_score(box))
    assert parse.box_digest(parse.parse_box_score(copy.deepcopy(box))) == base
    noisy = copy.deepcopy(box)
    noisy["local"]["players"][0]["player"]["images"]["headshot"] = "https://example.org/other.png"
    noisy["local"]["players"][0]["player"]["externalId"] = 1
    noisy["local"]["coach"]["name"] = "SOMEONE, ELSE"
    assert parse.box_digest(parse.parse_box_score(noisy)) == base
    changed = copy.deepcopy(box)
    changed["local"]["players"][0]["stats"]["steals"] += 1
    assert parse.box_digest(parse.parse_box_score(changed)) != base


def test_a_dnp_with_production_is_flagged_but_a_bench_foul_is_not(box: dict[str, Any]) -> None:
    index = next(i for i, p in enumerate(box["local"]["players"]) if p["stats"]["timePlayed"] == 0)
    foul = copy.deepcopy(box)
    foul["local"]["players"][index]["stats"]["foulsCommited"] = 1.0
    foul["local"]["players"][index]["stats"]["valuation"] = -1.0
    line = parse.parse_box_score(foul).home.players[index]
    assert line.participation == "dnp" and line.unexplained == ()
    scored = copy.deepcopy(box)
    scored["local"]["players"][index]["stats"]["points"] = 2.0
    line = parse.parse_box_score(scored).home.players[index]
    assert line.participation == "dnp" and line.unexplained == ("pts",)


def test_a_clock_string_for_time_played_is_read_as_minutes_and_seconds(box: dict[str, Any]) -> None:
    box["local"]["players"][0]["stats"]["timePlayed"] = "30:12"
    assert parse.parse_box_score(box).home.players[0].seconds == 1812


def test_cosmetic_person_fields_never_break_a_box_score(box: dict[str, Any]) -> None:
    person = box["local"]["players"][0]["player"]["person"]
    person["birthDate"] = "0001-01-01T00:00:00"
    person["height"] = 0
    person["weight"] = None
    line = parse.parse_box_score(box).home.players[0]
    assert (line.person.birth_date, line.person.height_cm, line.person.weight_kg) == (
        None,
        None,
        None,
    )


# ----------------------------------------------------------------- the box score refuses


def _unreadable(payload: Any) -> Unreadable:
    with pytest.raises(Unreadable) as caught:
        parse.parse_box_score(payload)
    return caught.value


def test_a_box_score_without_a_side_names_the_keys_it_found(box: dict[str, Any]) -> None:
    del box["road"]
    err = _unreadable(box)
    assert err.path == "road" and "'road' is missing" in err.reason and "local" in err.reason


def test_a_renamed_players_key_is_named(box: dict[str, Any]) -> None:
    box["local"]["roster"] = box["local"].pop("players")
    err = _unreadable(box)
    assert err.path == "local.players"
    assert "roster" in err.reason  # the key it found instead


def test_a_non_object_payload_is_unreadable() -> None:
    assert "expected an object" in _unreadable([1, 2, 3]).reason
    assert "expected an object" in _unreadable("<html>").reason


def test_a_data_envelope_around_a_box_score_is_accepted(box: dict[str, Any]) -> None:
    assert parse.parse_box_score({"data": box}).home.club_code == "ZZA"


@pytest.mark.parametrize(
    ("key", "bad", "path_tail"),
    [
        ("points", 3.5, "stats.points"),
        ("points", "lots", "stats.points"),
        ("points", -2, "stats.points"),
        ("points", True, "stats.points"),
        ("totalRebounds", [], "stats.totalRebounds"),
        ("timePlayed", "long", "stats.timePlayed"),
        ("timePlayed", -5, "stats.timePlayed"),
        ("startFive", "perhaps", "stats.startFive"),
    ],
)
def test_a_wrong_kind_of_value_is_unreadable_with_the_path(
    box: dict[str, Any], key: str, bad: Any, path_tail: str
) -> None:
    box["local"]["players"][1]["stats"][key] = bad
    err = _unreadable(box)
    assert err.path == f"local.players[1].{path_tail}"


def test_a_missing_time_played_is_unreadable_not_a_dnp(box: dict[str, Any]) -> None:
    del box["road"]["players"][0]["stats"]["timePlayed"]
    err = _unreadable(box)
    assert err.path == "road.players[0].stats.timePlayed" and "required key" in err.reason


@pytest.mark.parametrize(
    "key", ["points", "fieldGoalsMade2", "fieldGoalsAttempted3", "freeThrowsMade"]
)
def test_a_played_line_without_its_core_columns_is_shape_drift(
    box: dict[str, Any], key: str
) -> None:
    del box["local"]["players"][0]["stats"][key]
    err = _unreadable(box)
    assert "no value for" in err.reason and err.path == "local.players[0].stats"


def test_a_staff_member_in_the_players_list_is_unreadable(box: dict[str, Any]) -> None:
    box["local"]["players"][3]["player"]["type"] = "E"
    err = _unreadable(box)
    assert "not a player" in err.reason and err.path == "local.players[3].player"


def test_a_side_whose_players_belong_to_two_clubs_is_unreadable(box: dict[str, Any]) -> None:
    box["local"]["players"][2]["player"]["club"]["code"] = "ZZC"
    err = _unreadable(box)
    assert "different clubs" in err.reason and "ZZA" in err.reason and "ZZC" in err.reason


def test_both_sides_being_one_club_is_unreadable(box: dict[str, Any]) -> None:
    for entry in box["road"]["players"]:
        entry["player"]["club"]["code"] = "ZZA"
    assert "both sides are the club ZZA" in _unreadable(box).reason


def test_players_of_two_seasons_are_unreadable(box: dict[str, Any]) -> None:
    box["road"]["players"][0]["player"]["season"]["code"] = "E2030"
    assert "different seasons" in _unreadable(box).reason


def test_a_box_score_without_team_totals_is_unreadable(box: dict[str, Any]) -> None:
    del box["local"]["total"]
    assert _unreadable(box).path == "local.total"


# ---------------------------------------------------------------------------- the fuzz

_BAD_VALUES: tuple[Any, ...] = (None, "", "abc", -1, 3.5, True, False, [], {}, "12:34", 1e30, "NaN")


def _leaf_paths(node: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    if isinstance(node, dict):
        return [p for k, v in node.items() for p in _leaf_paths(v, (*prefix, k))]
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in _leaf_paths(v, (*prefix, i))]
    return [prefix]


def _set(root: Any, path: tuple[Any, ...], value: Any) -> None:
    for key in path[:-1]:
        root = root[key]
    root[path[-1]] = value


@pytest.mark.parametrize(
    ("fixture", "parser"),
    [
        ("box_game_1.json", parse.parse_box_score),
        ("games_round_2.json", parse.parse_games),
        ("people_ZZA.json", parse.parse_people),
        ("clubs.json", parse.parse_clubs),
        ("rounds.json", parse.parse_rounds),
    ],
)
def test_no_single_wrong_leaf_can_make_a_parser_raise_anything_but_unreadable(
    fixture: str, parser: Any
) -> None:
    """Fail closed means *closed*: whatever one value is replaced by, the parser either reads the
    payload or says ``Unreadable``. A ``TypeError`` or ``KeyError`` escaping would crash the job
    instead of recording a shape problem."""
    original = load(fixture)
    paths = _leaf_paths(original)
    rng = random.Random(20311004)
    for _ in range(400):
        payload = copy.deepcopy(original)
        path = rng.choice(paths)
        bad = rng.choice(_BAD_VALUES)
        _set(payload, path, copy.deepcopy(bad))
        try:
            parser(payload)
        except Unreadable:
            pass
        except Exception as exc:  # pragma: no cover - the failure we are looking for
            pytest.fail(
                f"{parser.__name__} raised {type(exc).__name__} for {path} = {bad!r}: {exc}"
            )


def test_truncating_a_list_or_removing_a_key_is_also_closed() -> None:
    original = load("box_game_1.json")
    rng = random.Random(7)
    keys = [p for p in _leaf_paths(original)]
    for _ in range(200):
        payload = copy.deepcopy(original)
        path = rng.choice(keys)
        parent = payload
        for key in path[:-1]:
            parent = parent[key]
        if isinstance(parent, dict):
            parent.pop(path[-1], None)
        elif isinstance(parent, list) and parent:
            parent.pop()
        try:
            parse.parse_box_score(payload)
        except Unreadable:
            pass


# ----------------------------------------------------------------------------- expected


def test_every_required_path_the_probe_reports_exists_in_the_authored_fixtures() -> None:
    """The shape report is only trustworthy if the authored shapes satisfy it themselves."""
    from nbastats.euroleague.ingest import probe

    payloads = {
        "E1": load("rounds.json"),
        "E2": load("games_round_1.json"),
        "E3": load("box_game_1.json"),
        "E4": load("clubs.json"),
        "E5": load("people_ZZA.json"),
    }
    for key, payload in payloads.items():
        lines, _ = probe.shape_report(key, payload)
        missing = [line.path for line in lines if line.required and not line.present]
        assert missing == [], (key, missing)
        assert lines, key


def test_the_expected_paths_cover_every_endpoint() -> None:
    assert set(parse.EXPECTED_PATHS) == {"E1", "E2", "E3", "E4", "E5"}


# ------------------------------------------------------------------- real recordings, locally


def _local_recording_folders() -> list[Path]:
    here = Path(__file__).resolve().parents[1]
    folders = [here / "local" / "euroleague", here / "local" / "recordings" / "euroleague"]
    base = os.environ.get("HARDWOOD_DATA_DIR")
    if base:
        folders.append(Path(base) / "recordings" / "euroleague")
    return [f for f in folders if f.is_dir()]


def test_real_recordings_parse_when_the_mac_has_made_some() -> None:
    """Runs over responses recorded by ``probe`` on the Mac; skips on a clean checkout.

    This is the contract test the design asks for: parsers are locked against recordings, not
    against documentation. A failure prints the path and reason from the parser, which is exactly
    what ``probe`` printed on the Mac.
    """
    from nbastats.euroleague.ingest import probe

    folders = _local_recording_folders()
    found: dict[str, tuple[Path, dict[str, Any]]] = {}
    for folder in folders:
        found.update(probe.find_recordings(folder))
    if not found:
        pytest.skip("no real EuroLeague recordings under tests/local or HARDWOOD_DATA_DIR")
    failures = []
    for key, (path, meta) in sorted(found.items()):
        endpoint, _ = probe._judge(
            key, meta.get("url"), path.read_bytes(), meta.get("status", 200), meta.get("season", "")
        )
        if endpoint.verdict not in ("ok", "notFound"):
            failures.append(f"{key} {path.name}: {endpoint.verdict}: {endpoint.reason}")
    assert failures == [], "\n".join(failures)
