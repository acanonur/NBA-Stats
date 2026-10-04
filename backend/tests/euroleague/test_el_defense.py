"""Defence by opponent position on the EuroLeague's registrations: what counts, and when it is withheld.

The statistics themselves (allocation, shrinkage, bands) are the shared core's and are tested
there. These tests hold the EuroLeague's part: where a player's position comes from, that the
buckets add up to the points a club actually allowed, that estimates are labelled as estimates, and
that the gates withhold at their exact thresholds.

The demo league gives every club four games, which is below the six a defence needs, so tests that
need more *clone* final games inside a throwaway store (``clone``): the copies are exact, so the
numbers they produce are easy to reason about.
"""

from __future__ import annotations

import math
from datetime import timedelta, timezone

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from nbastats.euroleague.demo import DEMO_AS_OF
from nbastats.euroleague.models import ElGame, ElPlayerGame, ElRegistration, ElTeamGame
from nbastats.euroleague.read import defense as defense_module
from nbastats.euroleague.read.queries import (
    ReadContext,
    build_context,
    clear_memo,
    game_start,
)
from nbastats.euroleague.read.sources import KEYS_STATS, freshness_for
from nbastats.shared.market_guard import split_words

NOW = DEMO_AS_OF.replace(tzinfo=timezone.utc)


@pytest.fixture()
def session(fresh_demo_engine):
    with Session(fresh_demo_engine, future=True) as s:
        yield s


def ctx_of(session) -> ReadContext:
    clear_memo()
    return build_context(session, None, now=NOW)


def clone(session, game_id: str, new_id: str, round_number: int) -> None:
    """Copy a final game, its team rows and its player lines to a new game id and round."""
    game = session.get(ElGame, game_id)
    values = {c.name: getattr(game, c.name) for c in ElGame.__table__.columns}
    values.update(game_id=new_id, round_number=round_number, game_code=None)
    session.add(ElGame(**values))
    session.flush()
    for row in (
        session.execute(select(ElTeamGame).where(ElTeamGame.game_id == game_id)).scalars().all()
    ):
        v = {c.name: getattr(row, c.name) for c in ElTeamGame.__table__.columns}
        v["game_id"] = new_id
        session.add(ElTeamGame(**v))
    for row in (
        session.execute(select(ElPlayerGame).where(ElPlayerGame.game_id == game_id)).scalars().all()
    ):
        v = {c.name: getattr(row, c.name) for c in ElPlayerGame.__table__.columns}
        v["game_id"] = new_id
        session.add(ElPlayerGame(**v))
    session.commit()


def round_games(session, round_number: int) -> list[str]:
    return list(
        session.execute(
            select(ElGame.game_id)
            .where(ElGame.round_number == round_number)
            .order_by(ElGame.game_id)
        ).scalars()
    )


def table(session, **options):
    ctx = ctx_of(session)
    return ctx, defense_module.build_table(ctx, **options)


def payload(session, club="ZZA", **options):
    ctx = ctx_of(session)
    base = dict(scheme="gfc", basis="perGame", window=0, phases=None, freshness={})
    base.update(options)
    return defense_module.defense_payload(ctx, club, **base)


# --------------------------------------------------------------------------- reconciliation


def test_the_buckets_add_up_to_the_points_a_club_allowed(session) -> None:
    ctx, tbl = table(session)
    for team in tbl.teams:
        assert team.unreconciled_games == 0
        assert team.games == 4
        assert team.sum_of_buckets == pytest.approx(team.points_allowed_per_game, abs=1e-9)
        deltas = math.fsum(b.delta_per_game for b in team.buckets)
        assert deltas == pytest.approx(
            team.points_allowed_per_game - team.league_points_allowed_per_game, abs=1e-9
        )
    # ...and the headline is what the box scores say: the club's opponents' points per game.
    totals = dict(
        session.execute(
            select(ElTeamGame.club_code, func.avg(ElTeamGame.opp_pts)).group_by(
                ElTeamGame.club_code
            )
        ).all()
    )
    for team in tbl.teams:
        assert team.points_allowed_per_game == pytest.approx(totals[team.team])


def test_the_payload_prints_the_identity_a_reader_can_check(session) -> None:
    data = payload(session)
    rec = data["reconciliation"]
    assert rec["sumOfBuckets"] == pytest.approx(data["pointsAllowedPerGame"])
    assert rec["unreconciledGames"] == 0
    assert (
        rec["identity"] == "sum(deltaPerGame) = pointsAllowedPerGame - leaguePointsAllowedPerGame"
    )
    assert math.fsum(b["deltaPerGame"] for b in data["buckets"]) == pytest.approx(
        data["pointsAllowedPerGame"] - data["leaguePointsAllowedPerGame"]
    )
    assert data["caveat"].endswith("who guarded whom.")


def test_guard_points_are_the_opponents_registered_guards_points(session) -> None:
    """Recompute one club's guard bucket from the store: opposing registered guards, per game."""
    ctx = ctx_of(session)
    club = "ZZA"
    games = [
        g
        for g in ctx.games
        if club in (g.home_club_code, g.away_club_code) and g.stats_status == "ok"
    ]
    guard_points = []
    for game in games:
        opponent = game.away_club_code if game.home_club_code == club else game.home_club_code
        lines = session.execute(
            select(ElPlayerGame, ElRegistration)
            .join(
                ElRegistration,
                (ElRegistration.person_code == ElPlayerGame.person_code)
                & (ElRegistration.club_code == ElPlayerGame.club_code),
            )
            .where(
                ElPlayerGame.game_id == game.game_id,
                ElPlayerGame.club_code == opponent,
                ElPlayerGame.participation == "played",
            )
        ).all()
        guard_points.append(sum(pg.pts for pg, reg in lines if reg.position_code == 1))
    data = payload(session, club)
    guards = next(b for b in data["buckets"] if b["position"] == "G")
    assert guards["pointsAllowedPerGame"] == pytest.approx(sum(guard_points) / len(guard_points))
    assert data["coverage"] == {"listed": 1.0, "workbookListing": 0.0, "unknown": 0.0}
    assert data["availability"] == "full"


# --------------------------------------------------------------------------- where a position comes from


def test_a_missing_registration_falls_back_to_the_box_scores_own_listing_never_the_workbook_label(
    session,
) -> None:
    """A player with no registration code is placed by the code his box score carries, and is
    unknown where it carries none; the workbook author's label never fills the gap."""
    games = {g.game_id: g for g in session.execute(select(ElGame)).scalars()}
    early, late = {}, {}
    for line in session.execute(
        select(ElPlayerGame).where(ElPlayerGame.participation == "played", ElPlayerGame.pts > 3)
    ).scalars():
        (early if games[line.game_id].round_number <= 2 else late).setdefault(
            line.person_code, []
        ).append(line)
    person = next(p for p in sorted(early) if p in late)
    early_line, late_line = early[person][0], late[person][0]
    session.execute(
        update(ElRegistration)
        .where(ElRegistration.person_code == person)
        .values(position_code=None)
    )
    session.commit()
    ctx = ctx_of(session)
    allocations = defense_module._allocate(ctx, "gfc")["allocations"]

    def unknown_points(line) -> float:
        game = games[line.game_id]
        defender = (
            game.away_club_code if line.club_code == game.home_club_code else game.home_club_code
        )
        allocation = next(a for a in allocations[defender] if a.game_id == line.game_id)
        return allocation.basis_points["unknown"]

    assert late_line.position_code_at_game is not None and early_line.position_code_at_game is None
    assert unknown_points(late_line) == 0.0  # placed by the box score's own listing
    assert unknown_points(early_line) == early_line.pts  # nothing to place him by: unknown
    # The workbook label exists for him and is not used, because the store has official registrations.
    assert (
        session.execute(
            select(ElRegistration.position5_workbook).where(ElRegistration.person_code == person)
        ).scalar_one()
        is not None
    )
    assert all(
        a.basis_points["workbookListing"] == 0.0 for rows in allocations.values() for a in rows
    )


def test_a_store_with_no_official_registrations_falls_back_to_the_workbook_labels_and_says_so(
    session,
) -> None:
    session.execute(update(ElRegistration).values(position_code=None))
    session.execute(update(ElPlayerGame).values(position_code_at_game=None))
    session.commit()
    ctx, tbl = table(session)
    assert not ctx.has_official_registrations
    for team in tbl.teams:
        assert team.coverage.workbook_listing == pytest.approx(1.0)
        assert team.coverage.listed == 0.0
    data = payload(session)
    assert data["availability"] == "estimated"
    assert any("workbook author's labels" in note for note in data["notes"])
    assert "estimated" in data["method"]["positionSource"]


def test_the_five_way_scheme_is_opt_in_estimated_and_says_why(session) -> None:
    data = payload(session, scheme="workbook5")
    positions = [b["position"] for b in data["buckets"]]
    assert positions == ["PG", "SG", "SF", "PF", "C", "unknown"]
    assert data["availability"] == "estimated"
    assert (
        "Positions assigned by the workbook author; the EuroLeague registers only Guard, Forward and Center."
        in data["notes"]
    )
    assert data["coverage"]["workbookListing"] == pytest.approx(1.0)
    assert math.fsum(b["pointsAllowedPerGame"] for b in data["buckets"]) == pytest.approx(
        data["pointsAllowedPerGame"]
    )
    assert data["method"]["taxonomy"].startswith("Positions assigned by the workbook author")
    # The default stays the three official positions.
    assert [b["position"] for b in payload(session)["buckets"]] == ["G", "F", "C", "unknown"]


def test_a_player_with_no_position_anywhere_is_unknown_and_never_reassigned(session) -> None:
    person = (
        session.execute(
            select(ElPlayerGame.person_code).where(
                ElPlayerGame.participation == "played", ElPlayerGame.pts > 5
            )
        )
        .scalars()
        .first()
    )
    session.execute(
        update(ElRegistration)
        .where(ElRegistration.person_code == person)
        .values(position_code=None)
    )
    session.execute(
        update(ElPlayerGame)
        .where(ElPlayerGame.person_code == person)
        .values(position_code_at_game=None)
    )
    session.commit()
    ctx, tbl = table(session)
    unknown_total = sum(
        t.coverage.unknown * t.sum_of_buckets for t in tbl.teams if t.coverage.unknown
    )
    assert unknown_total > 0
    for team in tbl.teams:
        assert team.sum_of_buckets == pytest.approx(team.points_allowed_per_game)  # still adds up
        unknown = next(b for b in team.buckets if b.position == "unknown")
        assert unknown.points_allowed_per_game == pytest.approx(
            (team.coverage.unknown or 0.0) * team.points_allowed_per_game
        )


# --------------------------------------------------------------------------- scope


def test_quarantined_and_unfinished_games_do_not_count(session) -> None:
    ctx, before = table(session)
    victim = round_games(session, 4)[0]
    session.execute(
        update(ElGame).where(ElGame.game_id == victim).values(stats_status="quarantined")
    )
    session.commit()
    ctx, after = table(session)
    home = session.get(ElGame, victim).home_club_code
    assert before.team(home).games == 4 and after.team(home).games == 3


def test_a_game_that_does_not_reconcile_is_excluded_and_counted_never_patched(session) -> None:
    victim = round_games(session, 3)[0]
    game = session.get(ElGame, victim)
    line = (
        session.execute(
            select(ElPlayerGame).where(
                ElPlayerGame.game_id == victim,
                ElPlayerGame.club_code == game.away_club_code,
                ElPlayerGame.participation == "played",
                ElPlayerGame.pts > 0,
            )
        )
        .scalars()
        .first()
    )
    session.execute(
        delete(ElPlayerGame).where(
            ElPlayerGame.game_id == victim, ElPlayerGame.person_code == line.person_code
        )
    )
    session.commit()
    ctx, tbl = table(session)
    defender = tbl.team(
        game.home_club_code
    )  # the home side faced the away club whose player we removed
    assert defender.unreconciled_games == 1 and defender.games == 3
    assert tbl.team(game.away_club_code).unreconciled_games == 0
    data = payload(session, game.home_club_code)
    assert data["reconciliation"]["unreconciledGames"] == 1
    assert data["availability"] == "partial"


def test_the_window_is_the_clubs_last_games_and_the_league_reference_stays_whole(session) -> None:
    ctx = ctx_of(session)
    club = "ZZA"
    games = sorted(
        (
            g
            for g in ctx.games
            if club in (g.home_club_code, g.away_club_code) and g.stats_status == "ok"
        ),
        key=lambda g: (g.game_date, g.game_id),
        reverse=True,
    )[:2]
    wanted = []
    for g in games:
        wanted.append(g.away_pts if g.home_club_code == club else g.home_pts)
    data = payload(session, club, window=2)
    assert data["window"] == {"kind": "lastGames", "games": 2, "requested": 2}
    assert data["pointsAllowedPerGame"] == pytest.approx(sum(wanted) / 2)
    whole = payload(session, club)
    assert data["leaguePointsAllowedPerGame"] == whole["leaguePointsAllowedPerGame"]


def test_a_cut_off_keeps_only_games_that_had_started(session) -> None:
    ctx = ctx_of(session)
    fourth = min((g for g in ctx.games if g.round_number == 4), key=game_start)
    before = defense_module.build_table(ctx, before=game_start(fourth))
    assert {t.games for t in before.teams} == {3}  # rounds 1 to 3 only
    after = defense_module.build_table(ctx, before=game_start(fourth) + timedelta(days=30))
    assert {t.games for t in after.teams} == {4}


def test_a_phase_filter_narrows_the_scope(session) -> None:
    session.execute(update(ElGame).where(ElGame.round_number == 4).values(phase_code="PO"))
    session.commit()
    ctx, regular = table(session, phases=("RS",))
    ctx, playoff = table(session, phases=("PO",))
    assert {t.games for t in regular.teams} == {3} and {t.games for t in playoff.teams} == {1}


# --------------------------------------------------------------------------- the gates, at their thresholds


def test_five_games_are_too_few_and_six_are_enough(session) -> None:
    for game_id in round_games(session, 3):
        clone(session, game_id, game_id.replace("E2026-", "E2026-X"), 6)
    ctx, five = table(session)
    assert {t.games for t in five.teams} == {5}
    assert all(t.withheld and t.withheld.reason == "minimumGames" for t in five.teams)
    assert five.team("ZZA").withheld.message == "Only 5 games, too few to judge a defence"
    for game_id in round_games(session, 4):
        if game_id.startswith("E2026-0"):
            clone(session, game_id, game_id.replace("E2026-", "E2026-Y"), 7)
    ctx, six = table(session)
    assert {t.games for t in six.teams} == {6}
    assert all(t.withheld is None for t in six.teams)
    assert all(t.provisional for t in six.teams)  # six is enough to show, not yet to band
    assert all(b.band is None for t in six.teams for b in t.buckets)  # no bands while provisional
    assert any(b.index is not None for t in six.teams for b in t.buckets)  # indices do appear


def test_twelve_games_end_the_provisional_flag(session) -> None:
    for k, source in enumerate(round_games(session, 3) + round_games(session, 4)):
        for copy in range(4):
            clone(session, source, f"E2026-Z{copy}{k:02d}", 10 + copy)
    ctx, tbl = table(session)
    assert {t.games for t in tbl.teams} == {12}
    assert all(not t.provisional and t.withheld is None for t in tbl.teams)
    # Not provisional, so bands may now appear; whether any does is the data's to say, and the
    # method block says which it was.
    assert tbl.method.league_signal in ("none detected", "detected")
    assert tbl.method.band_rule.startswith("A position shows better or worse only when")


def test_the_league_sample_gate_is_exactly_eighty_percent(session) -> None:
    third = round_games(session, 3)
    # Eight round-3 games cover sixteen clubs. Cloning each twice gives those clubs six games: 80%.
    for index, game_id in enumerate(third[:8]):
        for copy in range(2):
            clone(session, game_id, f"E2026-S{copy}{index}", 6 + copy)
    ctx, at_threshold = table(session)
    meeting = [t for t in at_threshold.teams if t.games >= 6]
    assert len(meeting) == 16
    assert all(t.withheld is None or t.withheld.reason != "leagueSample" for t in meeting)
    # Seven games cover fourteen clubs: 70%, below the line. Withheld for the whole league.
    session.execute(delete(ElPlayerGame).where(ElPlayerGame.game_id.like("E2026-S_7")))
    session.execute(delete(ElTeamGame).where(ElTeamGame.game_id.like("E2026-S_7")))
    session.execute(delete(ElGame).where(ElGame.game_id.like("E2026-S_7")))
    session.commit()
    ctx, below = table(session)
    sixes = [t for t in below.teams if t.games >= 6]
    assert len(sixes) == 14
    assert all(t.withheld and t.withheld.reason == "leagueSample" for t in sixes)
    assert all(b.index is None and b.band is None for t in sixes for b in t.buckets)
    # Raw buckets are recorded facts and still show.
    assert all(b.points_allowed_per_game is not None for t in sixes for b in t.buckets)


def test_too_much_unlisted_scoring_withholds_every_index_league_wide(session) -> None:
    for game_id in round_games(session, 3) + round_games(session, 4):
        for copy in range(1):
            clone(
                session, game_id, game_id.replace("E2026-", "E2026-W"), 20 + int(game_id[-2:]) % 3
            )
    clubs = ("ZZA", "ZZB")  # two clubs of twenty is ten percent of the league's points
    for club in clubs:
        session.execute(
            update(ElRegistration)
            .where(ElRegistration.club_code == club)
            .values(position_code=None)
        )
        session.execute(
            update(ElPlayerGame)
            .where(ElPlayerGame.club_code == club)
            .values(position_code_at_game=None)
        )
    session.commit()
    ctx, tbl = table(session)
    assert tbl.league.unknown_share is not None and tbl.league.unknown_share > 0.05
    assert all(t.games >= 6 for t in tbl.teams)
    assert all(t.withheld and t.withheld.reason == "positionCoverage" for t in tbl.teams)
    assert all(b.index is None for t in tbl.teams for b in t.buckets)
    data = payload(session)
    assert data["withheld"]["reason"] == "positionCoverage" and "5%" in data["withheld"]["message"]


# --------------------------------------------------------------------------- the payloads


def test_the_table_is_sorted_by_points_allowed_and_has_no_ranks(session) -> None:
    ctx = ctx_of(session)
    data = defense_module.defense_table_payload(
        ctx,
        scheme="gfc",
        basis="perGame",
        window=0,
        phases=None,
        freshness=freshness_for(ctx, KEYS_STATS),
    )
    allowed = [t["pointsAllowedPerGame"] for t in data["teams"]]
    assert allowed == sorted(allowed) and len(allowed) == 20
    assert "rank" not in str(data).lower().replace("rankings", "")  # no rank field anywhere
    words = {w for key in _keys(data) for w in split_words(key)}
    assert not words & {"rank", "ranking", "line", "edge", "pick", "spread", "probability"}
    assert data["leaguePointsAllowedPerGame"] == pytest.approx(
        session.execute(select(func.avg(ElTeamGame.pts))).scalar_one()
    )
    assert data["window"] == {"kind": "season", "requested": None}


def test_the_per_minute_basis_is_points_per_forty_opponent_minutes(session) -> None:
    ctx = ctx_of(session)
    club = "ZZA"
    data = payload(session, club, basis="perMinute")
    centers = next(b for b in data["buckets"] if b["position"] == "C")
    points = minutes = 0.0
    for game in (
        g
        for g in ctx.games
        if club in (g.home_club_code, g.away_club_code) and g.stats_status == "ok"
    ):
        opponent = game.away_club_code if game.home_club_code == club else game.home_club_code
        for pg, reg in session.execute(
            select(ElPlayerGame, ElRegistration)
            .join(
                ElRegistration,
                (ElRegistration.person_code == ElPlayerGame.person_code)
                & (ElRegistration.club_code == ElPlayerGame.club_code),
            )
            .where(
                ElPlayerGame.game_id == game.game_id,
                ElPlayerGame.club_code == opponent,
                ElPlayerGame.participation == "played",
            )
        ).all():
            if reg.position_code == 3:
                points += pg.pts
                minutes += pg.seconds_played / 60.0
    assert centers["pointsPerRegulationMinutes"] == pytest.approx(points / minutes * 40.0)
    assert data["basis"] == "perMinute" and data["regulationMinutes"] == 40


def test_bad_parameters_are_400s_with_the_field_named(session) -> None:
    from nbastats.api.errors import ApiError

    for call, field in (
        (lambda: defense_module.validate_scheme("seven"), "scheme"),
        (lambda: defense_module.validate_basis("perShot"), "basis"),
        (lambda: defense_module.validate_window(-1), "window"),
    ):
        with pytest.raises(ApiError) as caught:
            call()
        assert caught.value.http_status == 400 and caught.value.field == field
    assert (
        defense_module.validate_scheme(None) == "gfc" and defense_module.validate_window(None) == 0
    )


def _keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)
