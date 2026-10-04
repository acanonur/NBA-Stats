"""Availability in force: which entry applies, whether it may drive a projection, and what is shown.

The EuroLeague has no injury feed, so every status is a sourced, dated entry and three questions
are asked of the pile: what do we *show* (exactly what was said, or an em dash), what does the model
*assume* (a player with nothing usable plays, and is counted as assumed), and does an old entry still
*count* (the three in-force rules). The tests here are the design's section 6.1 as executable
claims, run through the EuroLeague store and read layer rather than the pure core alone (which has
its own tests): the store supplies the calendar, each player's last played game and whether his club
has played since the source date.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from nbastats.api.errors import ApiError
from nbastats.euroleague.demo import DEMO_AS_OF
from nbastats.euroleague.models import (
    ElGame,
    ElIntelOverride,
    ElIntelStatus,
    ElPlayerGame,
)
from nbastats.euroleague.read import availability as av
from nbastats.euroleague.read.queries import build_context, clear_memo, game_start
from nbastats.euroleague.read.sources import KEYS_AVAILABILITY, freshness_for
from nbastats.shared.market_guard import scan_keys

NOW = DEMO_AS_OF.replace(tzinfo=timezone.utc)
DAY = timedelta(days=1)


@pytest.fixture()
def session(fresh_demo_engine):
    with Session(fresh_demo_engine, future=True) as s:
        s.execute(delete(ElIntelStatus))  # start from silence; each test says what was said
        s.commit()
        yield s


def ctx_at(session, now=NOW):
    clear_memo()
    return build_context(session, None, now=now)


def put(
    session,
    club,
    person,
    status,
    published,
    *,
    expected=None,
    reason="injury",
    model_status=None,
    game_id=None,
    rounds=(None, None),
    on=None,
    kind="manual",
    name="x",
    retracts=None,
    recorded=None,
):
    row = ElIntelStatus(
        club_code=club,
        person_code=person,
        player_name_raw=name,
        game_id=game_id,
        status=status,
        model_status=model_status,
        reason_category=reason,
        expected_return_text=expected,
        expected_return_round_from=rounds[0],
        expected_return_round_to=rounds[1],
        expected_return_date=on,
        source_kind=kind,
        source_label="Test",
        source_url=None,
        source_published_at=published,
        as_of=published,
        recorded_at=recorded or published,
        retracts_status_id=retracts,
    )
    session.add(row)
    session.commit()
    return row


def player_of(ctx, club="ZZA", index=0):
    return sorted(ctx.registrations_for_club(club), key=lambda r: r.person_code)[index].person_code


def idle_player(session, ctx, club="ZZA", skip=0):
    """A registered player of ``club`` with no played line at all, so rule (b) cannot fire."""
    played = {
        code
        for (code,) in session.execute(
            select(ElPlayerGame.person_code).where(ElPlayerGame.participation == "played")
        )
    }
    idle = sorted(
        r.person_code for r in ctx.registrations_for_club(club) if r.person_code not in played
    )
    assert len(idle) > skip, "the demo league has bench players who never played"
    return idle[skip]


def last_game_of(session, person):
    """The start of the last game ``person`` played, from the store."""
    row = (
        session.execute(
            select(ElGame)
            .join(ElPlayerGame, ElPlayerGame.game_id == ElGame.game_id)
            .where(ElPlayerGame.person_code == person, ElPlayerGame.participation == "played")
            .order_by(ElGame.round_number.desc(), ElGame.game_id.desc())
        )
        .scalars()
        .first()
    )
    return row, game_start(row)


# --------------------------------------------------------------------------- rule (a)


def test_a_return_in_rounds_holds_until_the_last_game_of_the_last_round_is_over(session) -> None:
    """``Rounds 3-4`` is the span of the absence: in force through round 4, out after its last game.

    (The design text says "the first game of its expected-return round range"; read literally
    that voids every ``Rounds 2-4`` entry on the day round 4 starts. See the module docstring of
    ``nbastats.shared.availability``, which records the deviation.)
    """
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    put(session, "ZZA", person, "out", datetime(2026, 9, 28), expected="Rounds 3-4", rounds=(3, 4))
    ctx = ctx_at(session)
    book = av.get_book(ctx)
    end = ctx.round_end_at(4)
    first_tip_of_round_4 = min(g.tipoff_utc for g in ctx.games_of_round(4)).replace(
        tzinfo=timezone.utc
    )
    assert book.resolve(person, None, first_tip_of_round_4 + timedelta(hours=1)).in_force
    assert book.resolve(person, None, end - timedelta(seconds=1)).in_force
    gone = book.resolve(person, None, end)
    assert not gone.in_force and gone.reason == "returnRoundPassed"


def test_the_round_clause_flips_exactly_when_the_last_game_has_been_played(session) -> None:
    from nbastats.shared.availability import StatusEntry, entry_in_force

    ctx = ctx_at(session)
    end = ctx.round_end_at(4)
    assert end is not None
    entry = StatusEntry(
        entry_id=1,
        status="out",
        source_kind="manual",
        source_published_at=datetime(2026, 10, 9),
        expected_return_round_from=3,
        expected_return_round_to=4,
    )
    just_before = entry_in_force(
        entry, as_of=end - timedelta(seconds=1), round_end_at=ctx.round_end_at
    )
    at_end = entry_in_force(entry, as_of=end, round_end_at=ctx.round_end_at)
    assert just_before.in_force and not at_end.in_force and at_end.reason == "returnRoundPassed"
    # The last game of the round is *expected to end* three hours after the last tip-off.
    last_tip = max(g.tipoff_utc for g in ctx.games_of_round(4)).replace(tzinfo=timezone.utc)
    assert end == last_tip + timedelta(hours=3)


def test_a_round_the_store_has_no_games_for_cannot_end_an_entry(session) -> None:
    ctx = ctx_at(session)
    person = player_of(ctx)
    put(session, "ZZA", person, "out", NOW - DAY, expected="Rounds 20-21", rounds=(20, 21))
    ctx = ctx_at(session)
    resolved = av.get_book(ctx).resolve(person, None, NOW)
    assert resolved.in_force and ctx.round_end_at(21) is None


def test_a_return_date_ends_the_entry_the_day_after_in_berlin(session) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    ret = datetime(2026, 10, 20).date()
    put(session, "ZZA", person, "out", NOW - DAY, expected="Around 20 Oct", on=ret)
    ctx = ctx_at(session)
    book = av.get_book(ctx)
    last_minute = datetime(2026, 10, 20, 21, 59, tzinfo=timezone.utc)  # 23:59 CEST on the 20th
    next_day = datetime(2026, 10, 20, 22, 1, tzinfo=timezone.utc)  # 00:01 CEST on the 21st
    assert book.resolve(person, None, last_minute).in_force
    gone = book.resolve(person, None, next_day)
    assert not gone.in_force and gone.reason == "returnDatePassed"


# --------------------------------------------------------------------------- rule (b)


def test_a_box_score_after_the_entry_supersedes_it(session) -> None:
    ctx = ctx_at(session)
    person = next(
        p
        for p in (player_of(ctx, index=i) for i in range(5))
        if session.execute(
            select(ElPlayerGame).where(
                ElPlayerGame.person_code == p, ElPlayerGame.participation == "played"
            )
        ).first()
    )
    game, start = last_game_of(session, person)
    start = start.replace(tzinfo=None) if start.tzinfo else start
    put(
        session,
        "ZZA",
        person,
        "out",
        start - timedelta(days=2),
        expected="Rounds 5-9",
        rounds=(5, 9),
    )
    ctx = ctx_at(session)
    resolved = av.get_book(ctx).resolve(person, None, NOW)
    assert not resolved.in_force and resolved.reason == "playedSince"
    assert resolved.row is not None  # still listed, flagged
    # An entry published after his last game is not superseded by it.
    put(
        session,
        "ZZA",
        person,
        "out",
        start + timedelta(hours=6),
        expected="Rounds 5-9",
        rounds=(5, 9),
    )
    ctx = ctx_at(session)
    assert av.get_book(ctx).resolve(person, None, NOW).in_force


def test_rule_b_is_cut_off_at_as_of_so_a_later_game_cannot_rewrite_an_earlier_view(session) -> None:
    ctx = ctx_at(session)
    person = next(
        p
        for p in (player_of(ctx, index=i) for i in range(5))
        if session.execute(
            select(ElPlayerGame).where(
                ElPlayerGame.person_code == p, ElPlayerGame.participation == "played"
            )
        ).first()
    )
    game, start = last_game_of(session, person)
    put(session, "ZZA", person, "out", datetime(2026, 9, 20), expected="Rounds 5-9", rounds=(5, 9))
    ctx = ctx_at(session)
    book = av.get_book(ctx)
    before_any_game = datetime(
        2026, 9, 21, tzinfo=timezone.utc
    )  # the demo's first game is on 24 Sep
    assert book.resolve(
        person, None, before_any_game
    ).in_force  # no game yet played since the entry
    assert not book.resolve(person, None, start + timedelta(hours=4)).in_force


# --------------------------------------------------------------------------- rule (c)


def test_an_entry_older_than_fourteen_days_stops_driving_the_model(session) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    exactly = NOW - timedelta(days=14)
    put(session, "ZZA", person, "out", exactly.replace(tzinfo=None))
    book = av.get_book(ctx_at(session))
    assert book.resolve(person, None, NOW).in_force  # exactly fourteen days is not over fourteen
    assert not book.resolve(person, None, NOW + timedelta(minutes=1)).in_force
    again = book.resolve(person, None, NOW + timedelta(minutes=1))
    assert again.reason == "tooOld" and again.present


@pytest.mark.parametrize(
    "text",
    ["Long-term", "indefinite absence", "out for the season", "knee surgery", "Season-ending"],
)
def test_a_long_absence_stays_in_force_but_turns_stale_after_a_week(session, text) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    put(
        session,
        "ZZA",
        person,
        "out",
        (NOW - timedelta(days=30)).replace(tzinfo=None),
        expected=text,
    )
    ctx = ctx_at(session)
    book = av.get_book(ctx)
    resolved = book.resolve(person, None, NOW)
    assert resolved.in_force
    assert book.is_stale(resolved, "ZZA", NOW)  # past seven days
    young = person
    session.execute(delete(ElIntelStatus))
    session.commit()
    put(session, "ZZA", young, "out", (NOW - timedelta(days=3)).replace(tzinfo=None), expected=text)
    book = av.get_book(ctx_at(session))
    assert not book.is_stale(book.resolve(young, None, NOW), "ZZA", NOW)


def test_a_status_is_stale_after_a_week_or_once_the_club_has_played(session) -> None:
    ctx = ctx_at(session)
    club = "ZZA"
    person = idle_player(session, ctx)
    last_game = max(game_start(g) for g in ctx.club_games(club) if g.status == "final")
    # Young, but written before the club's last game: stale because the club has played since.
    put(
        session, club, person, "questionable", (last_game - timedelta(hours=5)).replace(tzinfo=None)
    )
    ctx = ctx_at(session)
    book = av.get_book(ctx)
    resolved = book.resolve(person, None, NOW)
    # The player did not play that game (deep bench), so rule (b) does not fire; the club-played test does.
    if resolved.in_force:
        assert book.is_stale(resolved, club, NOW)
    session.execute(delete(ElIntelStatus))
    session.commit()
    # Written after the club's last game and less than a week ago: fresh.
    put(
        session, club, person, "questionable", (last_game + timedelta(hours=5)).replace(tzinfo=None)
    )
    book = av.get_book(ctx_at(session))
    resolved = book.resolve(person, None, NOW)
    assert resolved.in_force and not book.is_stale(resolved, club, NOW)
    session.execute(delete(ElIntelStatus))
    session.commit()
    put(session, club, person, "questionable", (NOW - timedelta(days=8)).replace(tzinfo=None))
    book = av.get_book(ctx_at(session))
    assert book.is_stale(book.resolve(person, None, NOW), club, NOW)


def test_age_is_computed_from_the_sources_date_not_the_fetch(session) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    published = (NOW - timedelta(days=6, hours=2)).replace(tzinfo=None)
    put(
        session, "ZZA", person, "doubtful", published, recorded=NOW.replace(tzinfo=None)
    )  # fetched just now
    ctx = ctx_at(session)
    freshness = freshness_for(ctx, KEYS_AVAILABILITY)
    report = av.build_availability_report(ctx, club_code="ZZA", freshness=freshness)
    entry = report["teams"][0]["entries"][0]
    assert entry["ageMinutes"] == (6 * 24 + 2) * 60
    assert entry["source"]["publishedAt"].startswith(
        (NOW - timedelta(days=6, hours=2)).strftime("%Y-%m-%dT%H")
    )


# --------------------------------------------------------------------------- which entry applies


def test_the_newest_sourced_entry_applies(session) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    put(session, "ZZA", person, "out", (NOW - timedelta(days=3)).replace(tzinfo=None))
    newer = put(session, "ZZA", person, "probable", (NOW - timedelta(days=1)).replace(tzinfo=None))
    resolved = av.get_book(ctx_at(session)).resolve(person, None, NOW)
    assert resolved.entry.entry_id == newer.status_id and resolved.status == "probable"
    assert resolved.rule == "playerEntry"


def test_an_entry_for_this_exact_game_beats_an_older_general_one(session) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    game = ctx.next_game_of("ZZA")
    put(session, "ZZA", person, "available", (NOW - timedelta(days=5)).replace(tzinfo=None))
    put(
        session,
        "ZZA",
        person,
        "out",
        (NOW - timedelta(days=1)).replace(tzinfo=None),
        game_id=game.game_id,
    )
    resolved = av.get_book(ctx_at(session)).resolve(person, game.game_id, NOW)
    assert resolved.rule == "gameEntry" and resolved.status == "out"
    general = av.get_book(ctx_at(session)).resolve(person, None, NOW)
    assert general.rule == "playerEntry" and general.status == "available"  # asked "in general"


def test_a_newer_general_entry_overrules_an_older_note_about_this_one_game(session) -> None:
    """Typing "out" for a player (no game named) must not be overruled by last week's game note."""
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    game = ctx.next_game_of("ZZA")
    put(
        session,
        "ZZA",
        person,
        "probable",
        (NOW - timedelta(days=5)).replace(tzinfo=None),
        game_id=game.game_id,
    )
    book = av.get_book(ctx_at(session))
    assert book.resolve(person, game.game_id, NOW).status == "probable"  # nothing newer yet
    put(session, "ZZA", person, "out", (NOW - timedelta(days=1)).replace(tzinfo=None))
    book = av.get_book(ctx_at(session))
    resolved = book.resolve(person, game.game_id, NOW)
    assert resolved.status == "out" and resolved.rule == "playerEntry" and resolved.in_force
    # Before the newer entry existed, the game note still applied.
    assert book.resolve(person, game.game_id, NOW - timedelta(days=2)).status == "probable"


def test_an_older_general_entry_does_not_overrule_a_newer_note_about_the_game(session) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    game = ctx.next_game_of("ZZA")
    put(session, "ZZA", person, "out", (NOW - timedelta(days=4)).replace(tzinfo=None))
    put(
        session,
        "ZZA",
        person,
        "probable",
        (NOW - timedelta(days=1)).replace(tzinfo=None),
        game_id=game.game_id,
    )
    resolved = av.get_book(ctx_at(session)).resolve(person, game.game_id, NOW)
    assert resolved.status == "probable" and resolved.rule == "gameEntry"


def test_a_tie_keeps_the_more_specific_game_entry_and_an_out_of_force_general_one_never_overrules(
    session,
) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    game = ctx.next_game_of("ZZA")
    same = (NOW - timedelta(days=2)).replace(tzinfo=None)
    put(session, "ZZA", person, "out", same)
    put(session, "ZZA", person, "probable", same, game_id=game.game_id)
    assert av.get_book(ctx_at(session)).resolve(person, game.game_id, NOW).rule == "gameEntry"
    session.execute(delete(ElIntelStatus))
    session.commit()
    put(
        session,
        "ZZA",
        person,
        "doubtful",
        (NOW - timedelta(days=17)).replace(tzinfo=None),
        game_id=game.game_id,
    )
    put(
        session, "ZZA", person, "out", (NOW - timedelta(days=16)).replace(tzinfo=None)
    )  # newer, but over fourteen days old
    resolved = av.get_book(ctx_at(session)).resolve(person, game.game_id, NOW)
    assert resolved.status == "doubtful" and resolved.rule == "gameEntry" and not resolved.in_force


def test_an_override_outranks_a_sourced_entry_until_it_is_cleared(session) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    put(session, "ZZA", person, "out", (NOW - timedelta(days=1)).replace(tzinfo=None))
    session.add(
        ElIntelOverride(
            person_code=person,
            club_code="ZZA",
            status="available",
            created_at=(NOW - timedelta(hours=2)).replace(tzinfo=None),
        )
    )
    session.commit()
    resolved = av.get_book(ctx_at(session)).resolve(person, None, NOW)
    assert resolved.rule == "override" and resolved.status == "available" and resolved.in_force
    session.execute(
        ElIntelOverride.__table__.update().values(
            cleared_at=(NOW - timedelta(hours=1)).replace(tzinfo=None)
        )
    )
    session.commit()
    cleared = av.get_book(ctx_at(session)).resolve(person, None, NOW)
    assert cleared.rule == "playerEntry" and cleared.status == "out"


def test_information_from_after_the_cut_off_does_not_exist_yet(session) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    put(session, "ZZA", person, "out", (NOW - timedelta(days=1)).replace(tzinfo=None))
    book = av.get_book(ctx_at(session))
    assert book.resolve(person, None, NOW - timedelta(days=2)).entry is None  # not yet published
    assert book.resolve(person, None, NOW).status == "out"


def test_a_retraction_removes_the_entry_but_not_from_a_view_before_it(session) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    original = put(session, "ZZA", person, "out", (NOW - timedelta(days=3)).replace(tzinfo=None))
    put(
        session,
        "ZZA",
        person,
        None,
        (NOW - timedelta(days=1)).replace(tzinfo=None),
        retracts=original.status_id,
        kind="manual",
    )
    book = av.get_book(ctx_at(session))
    assert not book.resolve(person, None, NOW).present  # retracted
    assert (
        book.resolve(person, None, NOW - timedelta(days=2)).status == "out"
    )  # as it was known then


# --------------------------------------------------------------------------- show versus assume


def test_no_entry_is_a_dash_on_screen_and_a_certainty_in_the_model(session) -> None:
    ctx = ctx_at(session)
    club = "ZZA"
    person = idle_player(session, ctx)
    resolved = av.get_book(ctx).resolve(person, None, NOW)
    chance, assumed = av.get_book(ctx).chance(resolved, ctx.chance_table())
    assert (chance, assumed) == (1.0, True)
    report = av.build_availability_report(ctx, club_code=club, freshness={})
    assert report["teams"][0]["reportState"] == "noReport" and report["teams"][0]["entries"] == []
    assert report["state"] == "noReportYet"
    squad = None
    from nbastats.euroleague.read import teams as teams_module

    squad = teams_module.build_club_view(ctx, club, {})["squad"]
    row = next(r for r in squad if r["player"]["id"] == person)
    assert (
        row["status"] is None
        and row["chanceOfPlaying"] is None
        and row["availabilitySource"] is None
    )


def test_an_entry_out_of_force_is_listed_and_flagged(session) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    put(
        session,
        "ZZA",
        person,
        "out",
        (NOW - timedelta(days=20)).replace(tzinfo=None),
        expected="Rounds 6-9",
        rounds=(6, 9),
    )
    ctx = ctx_at(session)
    report = av.build_availability_report(ctx, club_code="ZZA", freshness={})
    entries = report["teams"][0]["entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert (
        entry["inForce"] is False
        and entry["isStale"] is True
        and entry["outOfForceReason"] == "tooOld"
    )
    assert entry["status"] == "out" and entry["chanceOfPlaying"] == 0.0
    assert report["state"] == "stale"


def test_the_report_shows_what_the_research_said_and_what_the_model_used(session) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    put(
        session,
        "ZZA",
        person,
        "available",
        (NOW - DAY).replace(tzinfo=None),
        model_status="out",
        reason="notRegistered",
    )
    ctx = ctx_at(session)
    entry = av.build_availability_report(ctx, club_code="ZZA", freshness={})["teams"][0]["entries"][
        0
    ]
    assert entry["status"] == "available" and entry["modelStatus"] == "out"
    assert entry["chanceOfPlaying"] == 1.0 and entry["reasonCategory"] == "notRegistered"
    assert scan_keys(entry) == []


def test_statuses_filter_and_round_scope(session) -> None:
    ctx = ctx_at(session)
    a, b = idle_player(session, ctx), idle_player(session, ctx, skip=1)
    put(session, "ZZA", a, "out", (NOW - DAY).replace(tzinfo=None))
    put(session, "ZZA", b, "probable", (NOW - DAY).replace(tzinfo=None))
    ctx = ctx_at(session)
    only_out = av.build_availability_report(ctx, club_code="ZZA", statuses=["out"], freshness={})
    assert [e["status"] for e in only_out["teams"][0]["entries"]] == ["out"]
    everyone = av.build_availability_report(ctx, round_number=5, freshness={})
    assert len(everyone["teams"]) == 20
    with pytest.raises(ApiError) as caught:
        av.build_availability_report(ctx, round_number=99, freshness={})
    assert caught.value.code == "bad_request"


def test_the_summary_counts_in_force_entries_by_status(session) -> None:
    ctx = ctx_at(session)
    people = [idle_player(session, ctx, skip=i) for i in range(5)]
    for person, status in zip(people, ["out", "out", "doubtful", "questionable", "probable"]):
        put(session, "ZZA", person, status, (NOW - DAY).replace(tzinfo=None))
    put(
        session,
        "ZZA",
        idle_player(session, ctx, skip=2),
        "out",
        (NOW - timedelta(days=30)).replace(tzinfo=None),
    )  # too old
    ctx = ctx_at(session)
    summary = av.summary_for_club(ctx, "ZZA", NOW)
    assert (summary["out"], summary["doubtful"], summary["questionable"], summary["probable"]) == (
        2,
        1,
        1,
        1,
    )
    assert summary["freshnessState"] == "fresh"
    empty = av.summary_for_club(ctx, "ZZB", NOW)
    assert empty["out"] == 0 and empty["freshnessState"] == "noReportYet"


# --------------------------------------------------------------------------- excused rounds


def test_a_missed_game_is_excused_only_when_the_status_says_injured_not_chosen(session) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    game = next(g for g in ctx.games if g.round_number == 4)
    published = game_start(game).replace(tzinfo=None) - timedelta(days=1)
    row = put(session, "ZZA", person, "out", published, reason="injury")
    book = av.get_book(ctx_at(session))
    assert book.excused(person, game.game_id, game_start(game))
    for reason in ("coachDecision", "rest"):
        session.execute(delete(ElIntelStatus))
        session.commit()
        put(session, "ZZA", person, "out", published, reason=reason)
        assert not av.get_book(ctx_at(session)).excused(person, game.game_id, game_start(game))
    session.execute(delete(ElIntelStatus))
    session.commit()
    put(session, "ZZA", person, "probable", published, reason="injury")
    assert not av.get_book(ctx_at(session)).excused(person, game.game_id, game_start(game))
    assert row.status_id  # the first entry existed


# --------------------------------------------------------------------------- the writes


def write(session, **fields):
    ctx = ctx_at(session)
    base = dict(
        club_code="ZZA",
        status="out",
        source_label="Alderwick statement",
        source_published_at=(NOW - DAY).isoformat(),
        person_code=idle_player(session, ctx),
    )
    base.update(fields)
    result = av.record_status(ctx, **base)
    session.commit()
    return result


def test_an_unknown_status_is_rejected_at_entry(session) -> None:
    for bad in ("maybe", "OUT!", "", "healthy"):
        with pytest.raises(ApiError) as caught:
            write(session, status=bad)
        assert caught.value.code == "invalid_status" and caught.value.http_status == 400
    assert session.execute(select(ElIntelStatus)).first() is None  # nothing was written
    # Any capitalisation of a real status is fine, and it is stored lower-case.
    assert write(session, status="  OUT ")["status"] == "out"


def test_a_status_entered_by_hand_carries_its_source_and_prefills_nothing_else(session) -> None:
    result = write(
        session,
        reason_category="illness",
        reason_text="Flu",
        expected_return_text="Rounds 5-6",
        source_url="https://news.example.org/a",
        game_id="E2026-R05-01",
    )
    row = session.get(ElIntelStatus, result["statusId"])
    assert row.source_kind == "clubStatement"  # the label names the club
    assert row.reason_category == "illness" and row.game_id == "E2026-R05-01"
    assert (row.expected_return_round_from, row.expected_return_round_to) == (5, 6)
    assert row.source_url == "https://news.example.org/a" and row.model_status is None
    assert row.as_of == row.source_published_at


def test_the_source_kind_is_manual_without_a_link_and_press_for_a_foreign_label(session) -> None:
    manual = session.get(ElIntelStatus, write(session)["statusId"])
    assert manual.source_kind == "manual" and manual.source_url is None
    press = session.get(
        ElIntelStatus,
        write(session, source_label="example.org", source_url="https://example.org/x")["statusId"],
    )
    assert press.source_kind == "pressArticle"


def test_a_link_to_a_gambling_operator_is_withheld_but_the_label_and_date_stay(session) -> None:
    result = write(
        session, source_label="Mozzart report", source_url="https://www.mozzartsport.com/news/1"
    )
    row = session.get(ElIntelStatus, result["statusId"])
    assert row.source_url is None and result["linkWithheld"] is True
    assert row.source_label.endswith("(link withheld: betting operator)")
    assert row.source_kind == "pressArticle" and row.source_published_at is not None


def test_a_name_that_matches_nobody_is_stored_unmatched_and_queued(session) -> None:
    ctx = ctx_at(session)
    result = write(session, person_code=None, player_name="Nobody Atall")
    assert result["matched"] is False and result["reviewQueue"] is True
    row = session.get(ElIntelStatus, result["statusId"])
    assert row.person_code is None and row.player_name_raw == "Nobody Atall"
    queue = av.review_queue(ctx_at(session))
    assert [i["playerName"] for i in queue["items"]] == ["Nobody Atall"]
    assert queue["items"][0]["team"]["clubCode"] == "ZZA"
    # It never drives a projection.
    book = av.get_book(ctx_at(session))
    assert all(
        not book.resolve(p.person_code, None, NOW).present
        for p in ctx.registrations_for_club("ZZA")
    )


def test_a_full_name_on_the_squad_is_matched_exactly_once(session) -> None:
    ctx = ctx_at(session)
    person = idle_player(session, ctx)
    name = ctx.player_name(person).upper()  # folding is case-insensitive
    result = write(session, person_code=None, player_name=name)
    assert result["matched"] is True and result["person"]["personCode"] == person


def test_the_entry_must_be_for_a_registered_person_a_known_club_and_a_real_game(session) -> None:
    with pytest.raises(ApiError) as caught:
        write(session, club_code="NOPE")
    assert caught.value.code == "club_not_found" and caught.value.http_status == 404
    with pytest.raises(ApiError) as caught:
        write(session, person_code="not-a-person")
    assert caught.value.code == "bad_request" and caught.value.field == "personCode"
    with pytest.raises(ApiError) as caught:
        write(session, game_id="E2026-R05-02")  # a game of two other clubs
    assert caught.value.field == "gameId"
    with pytest.raises(ApiError) as caught:
        write(session, reason_category="because")
    assert caught.value.field == "reasonCategory"
    with pytest.raises(ApiError) as caught:
        write(session, source_published_at=(NOW + timedelta(days=2)).isoformat())
    assert caught.value.field == "sourcePublishedAt"
    with pytest.raises(ApiError) as caught:
        write(session, source_url="javascript:alert(1)")
    assert caught.value.field == "sourceUrl"
    with pytest.raises(ApiError):
        write(session, person_code=None, player_name=None)
    with pytest.raises(ApiError):
        write(session, source_label="  ")


def test_a_write_bumps_the_sync_cursor_and_a_retraction_appends(session) -> None:
    ctx = ctx_at(session)
    before = ctx.sync_version
    result = write(session)
    assert ctx_at(session).sync_version == before + 1
    ctx = ctx_at(session)
    out = av.retract_status(ctx, result["statusId"])
    session.commit()
    assert out["alreadyRetracted"] is False
    rows = session.execute(select(ElIntelStatus).order_by(ElIntelStatus.status_id)).scalars().all()
    assert (
        len(rows) == 2
        and rows[1].retracts_status_id == result["statusId"]
        and rows[1].status is None
    )
    assert rows[0].status == "out"  # history is never rewritten
    assert av.retract_status(ctx_at(session), result["statusId"])["alreadyRetracted"] is True
    assert len(session.execute(select(ElIntelStatus)).scalars().all()) == 2
    person = rows[0].person_code
    assert not av.get_book(ctx_at(session)).resolve(person, None, NOW).present


def test_retracting_something_that_does_not_exist_or_is_itself_a_retraction_is_refused(
    session,
) -> None:
    result = write(session)
    ctx = ctx_at(session)
    out = av.retract_status(ctx, result["statusId"])
    session.commit()
    with pytest.raises(ApiError) as caught:
        av.retract_status(ctx_at(session), 99999)
    assert caught.value.http_status == 404
    with pytest.raises(ApiError) as caught:
        av.retract_status(ctx_at(session), out["retractedBy"])
    assert caught.value.code == "bad_request"
