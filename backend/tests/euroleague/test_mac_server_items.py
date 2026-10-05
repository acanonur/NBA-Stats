"""What the Mac app asked the EuroLeague side of the server for (BD-4 to BD-8).

The native app computes nothing. Every column it prints has to arrive as a number the server
already worked out, and each item below is a thing the app would otherwise have had to calculate:

BD-4  a player's game log says which club he played for, who the opponent was, whether it was at
      home, and how it ended, in the same words a club's ``scoring.form`` rows use;
BD-5  a round scorer carries his points in the club's last five games (the workbook's L1 to L5),
      newest first, with ``null`` where he did not play;
BD-6  a squad member carries his projected per-game line (minutes, and each per-40 rate scaled to
      them), with ``pir`` null because a projected PIR would be invented;
BD-7  a reviewed game carries its combined-points miss, signed like the margin miss;
BD-8  a projection says itself whether it was computed after tip-off, three-valued so "cannot say"
      is never turned into a guess.

Every test builds on the invented league (``ZZ`` clubs), never on anything real.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nbastats.euroleague.demo import DEMO_AS_OF
from nbastats.euroleague.model.player_rates import PlayerState
from nbastats.euroleague.model.scorers import RECENT_GAMES, form_index, recent_points
from nbastats.euroleague.models import ElGame, ElPlayerGame
from nbastats.euroleague.read import review as review_module
from nbastats.euroleague.read import round as round_module
from nbastats.euroleague.read import stats as stats_module
from nbastats.euroleague.read import teams as teams_module
from nbastats.euroleague.read.queries import (
    build_context,
    clear_memo,
    computed_after_tipoff,
    counts_for_scoring,
    game_start,
)
from nbastats.shared.market_guard import scan_keys

NOW = DEMO_AS_OF.replace(tzinfo=timezone.utc)
CLUB = "ZZA"
UPCOMING_ROUND = 5


@pytest.fixture()
def session(demo_engine) -> Any:
    with Session(demo_engine, future=True) as s:
        clear_memo()
        yield s
        s.rollback()


@pytest.fixture()
def ctx(session: Session) -> Any:
    return build_context(session, None, now=NOW)


# =========================================================================== BD-4: the game log


def _person_of(ctx: Any, club: str = CLUB) -> str:
    return sorted(ctx.registrations_for_club(club), key=lambda r: r.person_code)[0].person_code


def test_every_game_log_row_says_who_the_opponent_was_where_and_how_it_ended(ctx: Any) -> None:
    person = _person_of(ctx)
    log = stats_module.build_player_gamelog(ctx, person, None, {})
    assert log["games"], "the invented league has played games for this club"
    for row in log["games"]:
        game, club = row["game"], row["club"]["clubCode"]
        at_home = game["home"]["clubCode"] == club
        assert row["isHome"] is at_home
        assert row["opponent"]["clubCode"] == (game["away" if at_home else "home"]["clubCode"])
        assert row["opponent"]["clubCode"] != club
        own, other = (
            (game["homePts"], game["awayPts"]) if at_home else (game["awayPts"], game["homePts"])
        )
        assert (row["teamScore"], row["opponentScore"]) == (own, other)
        assert row["result"] == ("W" if own > other else "L")
        assert row["isNeutral"] == game["isNeutral"]


def test_the_game_log_keeps_every_key_it_had_and_stays_newest_first(ctx: Any) -> None:
    log = stats_module.build_player_gamelog(ctx, _person_of(ctx), None, {})
    for row in log["games"]:
        assert {"game", "club", "participation", "isStarter", "stats"} <= set(row)
        assert set(row) == {
            "game", "club", "opponent", "isHome", "isNeutral", "teamScore", "opponentScore",
            "result", "participation", "isStarter", "stats",
        }
    dates = [row["game"]["date"] for row in log["games"]]
    assert dates == sorted(dates, reverse=True)
    assert scan_keys(log) == []


def test_a_did_not_play_row_still_has_the_game_result_and_no_stats(ctx: Any) -> None:
    """A game he sat out is part of the club's log: the result is the club's, the line is null."""
    found = 0
    for reg in ctx.registrations_for_club(CLUB):
        for row in stats_module.build_player_gamelog(ctx, reg.person_code, None, {})["games"]:
            if row["participation"] == "dnp":
                found += 1
                assert row["stats"] is None and row["isStarter"] is None
                assert row["result"] in ("W", "L") and row["opponent"] is not None
    assert found > 0, "the invented league has did-not-play rows"


# =========================================================================== BD-5: recent points


def _expected_recent(session: Session, ctx: Any, person: str, club: str, before: datetime) -> list:
    """The club's last five scoring-scope games before ``before``, newest first, each as the
    player's points or None, worked out from the raw lines instead of through the function."""
    games = [g for g in ctx.club_games(club) if counts_for_scoring(g) and game_start(g) < before]
    games.sort(key=game_start, reverse=True)
    out: list[int | None] = []
    for game in games[:RECENT_GAMES]:
        row = session.get(ElPlayerGame, (game.game_id, person))
        played = (
            row is not None
            and row.participation == "played"
            and (row.seconds_played or 0) > 0
            and row.pts is not None
        )
        out.append(row.pts if played else None)
    return out


def test_recent_points_are_the_clubs_last_five_games_newest_first(
    session: Session, ctx: Any
) -> None:
    person = _person_of(ctx)
    got = recent_points(ctx, person, CLUB, NOW)
    assert got == _expected_recent(session, ctx, person, CLUB, NOW)
    assert len(got) == RECENT_GAMES - 1 or len(got) == RECENT_GAMES
    assert all(value is None or isinstance(value, int) for value in got)


def test_a_game_he_missed_is_a_none_in_its_place_not_a_zero_and_not_skipped(
    session: Session, ctx: Any
) -> None:
    """Pick a player with a did-not-play game inside the club's last five and check the hole."""
    chosen = None
    for reg in sorted(ctx.registrations_for_club(CLUB), key=lambda r: r.person_code):
        expected = _expected_recent(session, ctx, reg.person_code, CLUB, NOW)
        if None in expected and any(v is not None for v in expected):
            chosen = (reg.person_code, expected)
            break
    assert chosen is not None, "the invented league has a player with a hole in his last five"
    person, expected = chosen
    got = recent_points(ctx, person, CLUB, NOW)
    assert got == expected
    assert None in got
    # A missed game keeps its slot instead of the window reaching further back, so the list is as
    # long as the club's last five games say, whoever played in them.
    counted = [g for g in ctx.club_games(CLUB) if counts_for_scoring(g) and game_start(g) < NOW]
    assert len(got) == min(RECENT_GAMES, len(counted))
    for game, value in zip(sorted(counted, key=game_start, reverse=True), got):
        row = session.get(ElPlayerGame, (game.game_id, person))
        if value is None:
            assert row is None or row.participation != "played" or not row.seconds_played
        else:
            assert row is not None and row.pts == value  # a played zero stays a zero


def test_the_window_ends_before_the_cutoff_so_a_played_game_never_lists_itself(
    session: Session, ctx: Any
) -> None:
    person = _person_of(ctx)
    played = sorted(
        (g for g in ctx.club_games(CLUB) if counts_for_scoring(g)), key=game_start
    )
    third = played[2]
    before = game_start(third) - timedelta(seconds=1)
    got = recent_points(ctx, person, CLUB, before)
    assert len(got) == 2  # only the club's first two games precede the third
    assert got == _expected_recent(session, ctx, person, CLUB, before)
    assert recent_points(ctx, person, CLUB, game_start(played[0]) - timedelta(seconds=1)) == []


def test_recent_points_use_the_games_the_form_average_uses(ctx: Any) -> None:
    """One definition of "a game that counts": the points a list shows for the games he played
    are the most recent points his form average is made of."""
    person = _person_of(ctx)
    form_points = [points for _, points in form_index(ctx).get(person, [])]
    listed = [value for value in recent_points(ctx, person, CLUB, NOW) if value is not None]
    assert listed, "this player scored in at least one of the club's last games"
    assert sorted(listed) == sorted(form_points[-len(listed):])


def test_the_scorers_payload_carries_recent_points_for_every_player(ctx: Any) -> None:
    payload = round_module.build_round_scorers(ctx, UPCOMING_ROUND, 3, {})
    assert payload["clubs"]
    for club in payload["clubs"]:
        club_code = club["team"]["clubCode"]
        for scorer in club["players"]:
            listed = scorer["recentPoints"]
            assert isinstance(listed, list) and len(listed) <= RECENT_GAMES
            assert all(value is None or isinstance(value, int) for value in listed)
            assert scorer["projectedMinutes"] is not None  # served already; the app reads it
            assert listed == recent_points(ctx, scorer["player"]["personCode"], club_code, NOW)
    assert scan_keys(payload) == []


# =========================================================================== BD-6: per-game line


def _state(minutes: float | None, **rates: float | None) -> PlayerState:
    return PlayerState(
        person_code="p", club_code=CLUB, as_of_round=4, basis="workbookOfficial",
        prior_minutes=400.0, minutes=minutes, rates=rates, games_after=0, club_games_after=0,
    )


def test_the_per_game_line_is_each_per_40_rate_scaled_to_the_projected_minutes() -> None:
    state = _state(
        30.0, pts40=20.0, reb40=8.0, ast40=4.0, fg3m40=2.0, stl40=1.2, blk40=0.4, tov40=2.8
    )
    line = state.per_game
    assert line is not None
    assert line["min"] == 30.0
    expected = {
        "pts": 15.0, "reb": 6.0, "ast": 3.0, "fg3m": 1.5, "stl": 0.9, "blk": 0.3, "tov": 2.1,
    }
    for key, value in expected.items():
        assert line[key] == pytest.approx(value)
    assert line["pir"] is None
    assert state.points_per_game == pytest.approx(line["pts"])


def test_a_missing_rate_is_none_not_zero_and_no_minutes_is_no_line() -> None:
    line = _state(20.0, pts40=10.0).per_game
    assert line is not None and line["pts"] == pytest.approx(5.0)
    assert line["reb"] is None and line["tov"] is None and line["pir"] is None
    assert _state(None, pts40=10.0).per_game is None
    assert _state(0.0, pts40=10.0).per_game == {
        "min": 0.0, "pts": 0.0, "reb": None, "ast": None, "fg3m": None, "stl": None,
        "blk": None, "tov": None, "pir": None,
    }


def test_every_squad_member_carries_the_line_his_own_minutes_and_rates_imply(ctx: Any) -> None:
    view = teams_module.build_club_view(ctx, CLUB, {})
    assert view["squad"]
    with_line = 0
    for member in view["squad"]:
        line = member["perGame"]
        if member["projectedMinutes"] is None or member["per40"] is None:
            assert line is None
            continue
        with_line += 1
        assert line["min"] == member["projectedMinutes"]
        assert line["pir"] is None
        for key in ("pts", "reb", "ast", "fg3m", "stl", "blk", "tov"):
            rate = member["per40"][key]
            if rate is None:
                assert line[key] is None
            else:
                assert line[key] == pytest.approx(rate * member["projectedMinutes"] / 40.0)
    assert with_line > 0
    assert scan_keys(view) == []


# =========================================================================== BD-7: combined miss


def test_the_combined_miss_is_actual_total_less_projected_total_signed(ctx: Any) -> None:
    review = review_module.build_review(ctx, round_number=None, freshness={})
    assert review["games"]
    for item in review["games"]:
        actual = item["result"]["homePts"] + item["result"]["awayPts"]
        projected = item["projected"]["homePts"] + item["projected"]["awayPts"]
        assert item["combinedMiss"] == pytest.approx(actual - projected)
    # Signed, like marginMiss: the value is not an absolute one, and it is not always zero.
    assert any(item["combinedMiss"] != 0 for item in review["games"])


def test_the_mean_absolute_combined_miss_is_the_mean_of_the_listed_ones(ctx: Any) -> None:
    review = review_module.build_review(ctx, round_number=None, freshness={})
    rows = [g for g in review["games"] if g["kind"] == "reconstructed"]
    assert rows and review["reconstructed"] is not None
    mean = sum(abs(g["combinedMiss"]) for g in rows) / len(rows)
    assert review["reconstructed"]["meanAbsCombinedMiss"] == pytest.approx(mean)


# =========================================================================== BD-8: after tip-off


def _game(tipoff: datetime | None, day: date = date(2026, 10, 9)) -> ElGame:
    return ElGame(game_id="g", game_date=day, tipoff_utc=tipoff)


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc)


def test_with_a_known_tipoff_the_answer_is_a_plain_comparison() -> None:
    game = _game(datetime(2026, 10, 9, 17, 30))  # the store keeps naive UTC
    assert computed_after_tipoff(game, utc(2026, 10, 9, 17, 29, 59)) is False
    assert computed_after_tipoff(game, utc(2026, 10, 9, 17, 30)) is True
    assert computed_after_tipoff(game, utc(2026, 10, 10, 9, 0)) is True
    assert computed_after_tipoff(game, datetime(2026, 10, 8, 9, 0)) is False  # a naive moment


def test_with_no_tipoff_only_what_is_certain_is_said() -> None:
    game = _game(None)  # 9 October, a Berlin day: 22:00 UTC the evening before to 21:59 UTC
    assert computed_after_tipoff(game, utc(2026, 10, 8, 12, 0)) is False  # before the day began
    assert computed_after_tipoff(game, utc(2026, 10, 9, 12, 0)) is None  # during it: cannot say
    assert computed_after_tipoff(game, utc(2026, 10, 10, 9, 0)) is True  # after its last minute


def test_a_projection_with_no_creation_time_is_cannot_say() -> None:
    assert computed_after_tipoff(_game(datetime(2026, 10, 9, 17, 30)), None) is None
    assert computed_after_tipoff(_game(None), None) is None


def _models(document: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "computedAfterTipoff" in node:
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document)
    return found


def test_a_round_still_to_play_was_not_computed_after_tipoff(ctx: Any) -> None:
    view = round_module.build_round_view(ctx, UPCOMING_ROUND, {})
    models = [g["model"] for g in view["games"]]
    assert models
    assert all(m["kind"] == "latest" and m["computedAfterTipoff"] is False for m in models)


def test_a_round_already_played_was_computed_after_tipoff(ctx: Any) -> None:
    view = round_module.build_round_view(ctx, 4, {})
    models = [g["model"] for g in view["games"]]
    assert models
    assert all(m["kind"] == "reconstructed" and m["computedAfterTipoff"] is True for m in models)


def test_the_rounds_own_model_block_is_not_about_one_game_so_it_says_nothing(
    session: Session,
) -> None:
    from nbastats.euroleague import read

    slate = read.slate_projections(session, round_number="next", now=NOW)
    assert slate["model"]["computedAfterTipoff"] is None
    per_game = [g["model"]["computedAfterTipoff"] for g in slate["games"]]
    assert per_game == [False] * len(slate["games"])
    first = session.execute(select(ElGame).order_by(ElGame.game_id)).scalars().first()
    assert first is not None
    assert len(_models(slate)) == 1 + len(slate["games"])
