"""The EuroLeague matchup: two clubs as they stood going into a game.

Every figure is recomputed here straight from the box-score rows in the store, so the shared core
and the EuroLeague's loading cannot agree with each other and both be wrong. The rules held:
points allowed means "what opponents scored"; a game's matchup is cut off at its own tip-off;
neutral-site games sit in their own split; nothing is ranked; an opponent-adjusted value is
withheld until five games qualify.
"""

from __future__ import annotations

from datetime import timedelta, timezone

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from nbastats.api.errors import ApiError
from nbastats.euroleague import read
from nbastats.euroleague.demo import DEMO_AS_OF
from nbastats.euroleague.models import ElGame, ElTeamGame
from nbastats.euroleague.read.queries import build_context, clear_memo, game_start
from nbastats.shared.market_guard import scan_keys

NOW = DEMO_AS_OF.replace(tzinfo=timezone.utc)


@pytest.fixture()
def session(fresh_demo_engine):
    with Session(fresh_demo_engine, future=True) as s:
        yield s


def rows_of(session, club, before=None):
    """The club's box-score team rows (with their game), oldest first."""
    pairs = session.execute(
        select(ElTeamGame, ElGame)
        .join(ElGame, ElGame.game_id == ElTeamGame.game_id)
        .where(ElTeamGame.club_code == club, ElGame.stats_status == "ok")
        .order_by(ElGame.game_date, ElGame.game_id)
    ).all()
    if before is not None:
        pairs = [(t, g) for t, g in pairs if game_start(g) < before]
    return pairs


def block(payload, club):
    return next(t for t in payload["teams"] if t["team"]["id"] == club)


# --------------------------------------------------------------------------- the numbers


def test_scoring_and_points_allowed_are_the_means_of_the_box_score_rows(session) -> None:
    clear_memo()
    payload = read.team_matchup(session, home="ZZA", away="ZZB", now=NOW)
    for club in ("ZZA", "ZZB"):
        pairs = rows_of(session, club)
        scored = [t.pts for t, _ in pairs]
        allowed = [t.opp_pts for t, _ in pairs]
        team = block(payload, club)
        assert team["games"] == len(pairs) == 4
        assert team["pointsPerGame"] == pytest.approx(sum(scored) / 4)
        assert team["pointsAllowedPerGame"] == pytest.approx(sum(allowed) / 4)
        assert team["differentialPerGame"] == pytest.approx(sum(scored) / 4 - sum(allowed) / 4)
        assert team["record"] == {
            "wins": sum(1 for t, _ in pairs if t.pts > t.opp_pts),
            "losses": sum(1 for t, _ in pairs if t.pts < t.opp_pts),
        }
        latest = team["latestGame"]
        newest = pairs[-1]
        assert latest["teamScore"] == newest[0].pts and latest["opponentScore"] == newest[0].opp_pts
        assert latest["gameId"] == newest[1].game_id and latest["result"] in ("W", "L")
        assert (
            team["lastN"]["games"] == 4 and team["last10"]["games"] == 4 and len(team["form"]) == 4
        )
    assert [t["side"] for t in payload["teams"]] == ["home", "away"]


def test_the_league_average_is_the_mean_over_every_team_game(session) -> None:
    payload = read.team_matchup(session, home="ZZA", away="ZZB", now=NOW)
    mean = session.execute(
        select(func.avg(ElTeamGame.pts))
        .join(ElGame, ElGame.game_id == ElTeamGame.game_id)
        .where(ElGame.stats_status == "ok")
    ).scalar_one()
    assert payload["leagueAverage"]["pointsPerGame"] == pytest.approx(mean)
    assert payload["leagueAverage"]["teams"] == 20


def test_regulation_scaled_scoring_uses_each_games_team_time(session) -> None:
    payload = read.team_matchup(session, home="ZZA", away="ZZB", now=NOW)
    for club in ("ZZA", "ZZB"):
        pairs = rows_of(session, club)
        want = sum(t.pts * 12000 / t.seconds_played for t, _ in pairs) / len(pairs)
        want_allowed = sum(t.opp_pts * 12000 / t.seconds_played for t, _ in pairs) / len(pairs)
        team = block(payload, club)
        assert team["pointsPerRegulation"] == pytest.approx(want)
        assert team["pointsAllowedPerRegulation"] == pytest.approx(want_allowed)


def test_the_form_window_is_validated_and_a_long_window_holds_the_games_played(session) -> None:
    assert (
        len(
            block(read.team_matchup(session, home="ZZA", away="ZZB", window=3, now=NOW), "ZZA")[
                "form"
            ]
        )
        == 3
    )
    big = block(read.team_matchup(session, home="ZZA", away="ZZB", window=15, now=NOW), "ZZA")
    assert big["lastN"] == {
        "window": 15,
        "games": 4,
        "pointsPerGame": big["pointsPerGame"],
        "pointsAllowedPerGame": big["pointsAllowedPerGame"],
    }
    for bad in (2, 16, 0, -5):
        with pytest.raises(ApiError) as caught:
            read.team_matchup(session, home="ZZA", away="ZZB", window=bad, now=NOW)
        assert caught.value.http_status == 400 and caught.value.field == "window"


# --------------------------------------------------------------------------- venue splits


def test_a_neutral_site_game_is_only_in_the_neutral_split(session) -> None:
    ctx = build_context(session, None, now=NOW)
    neutral = next(g for g in ctx.games if g.is_neutral)
    club = neutral.home_club_code
    payload = read.team_matchup(session, home=club, away=neutral.away_club_code, now=NOW)
    team = block(payload, club)
    splits = team["venueSplits"]
    assert splits["neutral"]["games"] == 1
    pairs = rows_of(session, club)
    neutral_row = next(t for t, g in pairs if g.game_id == neutral.game_id)
    assert splits["neutral"]["pointsPerGame"] == neutral_row.pts
    others = [t for t, g in pairs if g.game_id != neutral.game_id]
    home = [t.pts for t in others if t.is_home]
    assert splits["home"]["games"] == len(home)
    assert splits["home"]["games"] + splits["away"]["games"] + splits["neutral"]["games"] == 4


def test_the_neutral_split_is_null_when_no_game_has_a_known_neutral_flag(session) -> None:
    session.execute(update(ElGame).values(is_neutral=None))
    session.commit()
    payload = read.team_matchup(session, home="ZZA", away="ZZB", now=NOW)
    for club in ("ZZA", "ZZB"):
        splits = block(payload, club)["venueSplits"]
        assert splits["neutral"] is None  # "not tracked", not "none played"
        assert splits["home"]["games"] + splits["away"]["games"] == 4  # classed by the home flag


def test_a_split_with_no_games_has_null_averages_not_zeros(session) -> None:
    ctx = build_context(session, None, now=NOW)
    neutral = next(g for g in ctx.games if g.is_neutral)
    club = neutral.home_club_code
    # Make every other game of the club a home game: its away split is then empty.
    session.execute(
        update(ElTeamGame)
        .where(ElTeamGame.club_code == club, ElTeamGame.game_id != neutral.game_id)
        .values(is_home=True)
    )
    session.commit()
    splits = block(
        read.team_matchup(session, home=club, away=neutral.away_club_code, now=NOW), club
    )["venueSplits"]
    assert splits["away"] == {"games": 0, "pointsPerGame": None, "pointsAllowedPerGame": None}


# --------------------------------------------------------------------------- opponent-adjusted


def test_opponent_adjusted_values_wait_for_five_qualifying_games(session) -> None:
    payload = read.team_matchup(session, home="ZZA", away="ZZB", now=NOW)
    for club in ("ZZA", "ZZB"):
        team = block(payload, club)
        assert team["adjustedPointsAgainst"] == {"value": None, "games": 4}
        assert team["adjustedPointsFor"] == {"value": None, "games": 4}
    assert any("at least 5 qualifying games" in n for n in payload["notes"])
    assert payload["availability"] == "partial"


def _clone_all(session, extra_rounds=2):
    from nbastats.euroleague.models import ElPlayerGame

    games = session.execute(select(ElGame).where(ElGame.round_number.in_((3, 4)))).scalars().all()
    for round_offset in range(extra_rounds):
        for game in games:
            values = {c.name: getattr(game, c.name) for c in ElGame.__table__.columns}
            values.update(
                game_id=f"E2026-A{round_offset}{game.game_id[-3:]}",
                round_number=10 + round_offset,
                game_code=None,
            )
            session.add(ElGame(**values))
            session.flush()
            for model in (ElTeamGame, ElPlayerGame):
                for row in (
                    session.execute(select(model).where(model.game_id == game.game_id))
                    .scalars()
                    .all()
                ):
                    v = {c.name: getattr(row, c.name) for c in model.__table__.columns}
                    v["game_id"] = f"E2026-A{round_offset}{game.game_id[-3:]}"
                    session.add(model(**v))
    session.commit()


def test_adjusted_values_appear_at_five_games_and_match_the_hand_calculation(session) -> None:
    _clone_all(session, extra_rounds=1)  # every club now has six games
    payload = read.team_matchup(session, home="ZZA", away="ZZB", now=NOW)
    team = block(payload, "ZZA")
    assert team["games"] == 6
    pairs = rows_of(session, "ZZA")
    against, scored = [], []
    for row, game in pairs:
        others = [t for t, g in rows_of(session, row.opp_club_code) if g.game_id != game.game_id]
        if len(others) < 2:
            continue
        against.append(row.opp_pts - sum(t.pts for t in others) / len(others))
        scored.append(row.pts - sum(t.opp_pts for t in others) / len(others))
    assert len(against) == 6
    assert team["adjustedPointsAgainst"]["games"] == 6
    assert team["adjustedPointsAgainst"]["value"] == pytest.approx(sum(against) / 6)
    assert team["adjustedPointsFor"]["value"] == pytest.approx(sum(scored) / 6)
    assert payload["availability"] == "full"


# --------------------------------------------------------------------------- the three ways in


def test_a_clubs_matchup_is_its_next_scheduled_game_with_the_projection(session) -> None:
    payload = read.team_matchup(session, club="ZZA", now=NOW)
    game = payload["game"]
    assert game["gameId"] == "E2026-R05-01" and game["status"] == "scheduled"
    assert [t["team"]["id"] for t in payload["teams"]] == [game["home"]["id"], game["away"]["id"]]
    projection = payload["projection"]
    assert (
        projection["game"]["gameId"] == "E2026-R05-01" and projection["model"]["kind"] == "latest"
    )
    assert projection["availability"] == "estimated"
    assert block(payload, "ZZA")["defenseSummary"]["withheld"]["reason"] == "minimumGames"


def test_two_clubs_with_a_game_to_play_get_that_game_and_without_one_get_none(session) -> None:
    ctx = build_context(session, None, now=NOW)
    game = ctx.game("E2026-R05-01")
    both = read.team_matchup(session, home=game.home_club_code, away=game.away_club_code, now=NOW)
    assert both["game"]["gameId"] == game.game_id and both["projection"] is not None
    reversed_sides = read.team_matchup(
        session, home=game.away_club_code, away=game.home_club_code, now=NOW
    )
    assert reversed_sides["game"] is None and reversed_sides["projection"] is None
    assert any("no projection" in n for n in reversed_sides["notes"])


def test_a_club_with_no_game_to_play_is_a_404(session) -> None:
    session.execute(update(ElGame).where(ElGame.status == "scheduled").values(status="postponed"))
    session.commit()
    with pytest.raises(ApiError) as caught:
        read.team_matchup(session, club="ZZA", now=NOW)
    assert caught.value.code == "game_not_found" and caught.value.http_status == 404


def test_unknown_clubs_and_unusable_pairs_are_refused(session) -> None:
    with pytest.raises(ApiError) as caught:
        read.team_matchup(session, home="NOPE", away="ZZB", now=NOW)
    assert caught.value.code == "club_not_found" and caught.value.http_status == 404
    with pytest.raises(ApiError) as caught:
        read.team_matchup(session, home="ZZA", away="ZZA", now=NOW)
    assert caught.value.code == "bad_request"
    with pytest.raises(ApiError):
        read.team_matchup(session, home="ZZA", now=NOW)
    with pytest.raises(ApiError) as caught:
        read.team_matchup(session, game_id="E2026-9999", now=NOW)
    assert caught.value.code == "game_not_found"


# --------------------------------------------------------------------------- cut off before tip-off


def test_a_games_matchup_uses_only_what_had_started_before_its_tip_off(session) -> None:
    ctx = build_context(session, None, now=NOW)
    game = next(g for g in ctx.games if g.round_number == 3 and not g.is_neutral)
    payload = read.team_matchup(session, game_id=game.game_id, now=NOW)
    assert payload["game"]["gameId"] == game.game_id and payload["game"]["status"] == "final"
    for club in (game.home_club_code, game.away_club_code):
        team = block(payload, club)
        assert team["games"] == 2  # rounds 1 and 2: the game itself and round 4 are not in it
        before = rows_of(session, club, before=game_start(game))
        assert team["pointsPerGame"] == pytest.approx(sum(t.pts for t, _ in before) / 2)
        assert team["latestGame"]["gameId"] == before[-1][1].game_id
    assert any("cut off before this game's tip-off" in n for n in payload["notes"])
    # The defence table behind the summary is cut off the same way: two games a club is below the minimum.
    assert (
        block(payload, game.home_club_code)["defenseSummary"]["withheld"]["message"]
        == "Only 2 games, too few to judge a defence"
    )


def test_a_games_matchup_reads_availability_as_it_stood_then(session) -> None:
    from nbastats.euroleague.models import ElIntelStatus

    ctx = build_context(session, None, now=NOW)
    game = next(g for g in ctx.games if g.round_number == 3 and not g.is_neutral)
    club = game.home_club_code
    person = sorted(ctx.registrations_for_club(club), key=lambda r: r.person_code)[-1].person_code
    tip = game.tipoff_utc

    def only_entry(published):
        session.execute(delete(ElIntelStatus))
        session.expire_all()
        session.add(
            ElIntelStatus(
                club_code=club,
                person_code=person,
                player_name_raw="x",
                status="out",
                source_kind="manual",
                source_label="t",
                source_published_at=published,
                as_of=published,
                recorded_at=published,
            )
        )
        session.commit()
        return block(read.team_matchup(session, game_id=game.game_id, now=NOW), club)[
            "availability"
        ]

    before = only_entry(tip - timedelta(days=1))
    assert before["out"] == 1 and before["freshnessState"] == "fresh"
    after = only_entry(
        tip + timedelta(days=1)
    )  # published once the game was under way: not yet known
    assert after["out"] == 0 and after["freshnessState"] == "noReportYet"
    # ...while the same entry is plainly there in a view of the club today.
    today = block(read.team_matchup(session, club=club, now=NOW), club)["availability"]
    assert today["out"] == 1


# --------------------------------------------------------------------------- hygiene


def test_nothing_is_ranked_and_no_key_carries_gambling_vocabulary(session) -> None:
    payload = read.team_matchup(session, club="ZZA", now=NOW)
    assert scan_keys(payload) == []
    text = repr(payload).lower()
    assert "'rank'" not in text and "winprobability" not in text
    assert (
        payload["seasonType"] is None
        and payload["phase"] is None
        and payload["season"] == "2026-27"
    )


def test_the_stats_part_of_a_matchup_says_estimated_when_positions_are_the_workbooks(
    session,
) -> None:
    from nbastats.euroleague.models import ElPlayerGame, ElRegistration

    session.execute(update(ElRegistration).values(position_code=None))
    session.execute(update(ElPlayerGame).values(position_code_at_game=None))
    session.commit()
    payload = read.team_matchup(session, home="ZZA", away="ZZB", now=NOW)
    assert payload["availability"] == "estimated"


def test_a_league_with_no_results_has_an_unavailable_matchup(session) -> None:
    session.execute(
        update(ElGame)
        .where(ElGame.status == "final")
        .values(stats_status="none", status="scheduled", home_pts=None, away_pts=None)
    )
    session.commit()
    payload = read.team_matchup(session, home="ZZA", away="ZZB", now=NOW)
    assert payload["availability"] == "unavailable"
    assert block(payload, "ZZA")["games"] == 0 and block(payload, "ZZA")["pointsPerGame"] is None
    assert block(payload, "ZZA")["latestGame"] is None and block(payload, "ZZA")["form"] == []
