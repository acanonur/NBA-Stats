"""The NBA matchup: points scored and allowed, form, splits, cut-offs and the honesty rules.

What is asserted, and how
-------------------------
**The arithmetic, by hand.** A three-team league with six final games is small enough that every
figure in ``TeamMatchup`` is worked out in the test from the scores written in the test: points
for and against per game, the differential, the record, the newest-first form, the venue splits
and the league average. A second, four-team league checks the opponent-adjusted values against a
literal re-statement of their definition. If the shared core, the loader or the payload builder
mis-wired any of it, one of these numbers would be off.

**The three ways in.** Two teams by id (a game and a projection only when they are scheduled to
meet with those sides), a team's next game (``404 game_not_found`` when it has none), and one game
with every input cut off before it started. The last is the no-leakage rule: the matchup of an
early game, built in a store that also holds the games that followed it, must equal the matchup
built in a store that never held them.

**Nothing becomes zero.** A team with no games has ``null`` points and a 0-0 record, an unknown
overtime count is ``null``, a window longer than the games played says how many it held, and the
NBA's neutral split is ``null`` because the NBA records no neutral sites.

**Wrong-league and bad input.** A EuroLeague club code or season code is an unknown id and an
unknown season here; a window or season type out of range is ``400``; an unloaded season is
``422``.

The helpers at the top (:class:`LeagueBuilder` and friends) build tiny stores by hand and are
imported by the sibling test modules of this package.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from nbastats import nba_matchup
from nbastats.api.errors import ApiError
from nbastats.db import create_db_engine, init_db
from nbastats.models import (
    Game,
    GameScheduleDetail,
    Player,
    PlayerGameBasic,
    PlayerPositionSeason,
    Team,
    TeamGame,
)
from nbastats.nba_matchup import queries

UTC = timezone.utc
SEASON = "2025-26"


# =========================================================================== helpers


@pytest.fixture(autouse=True)
def _fresh_memo() -> Iterator[None]:
    """The model and the defence table are memoised per data mark; start every test clean."""
    queries.clear_memo()
    yield
    queries.clear_memo()


def at(day: str, hour: int = 12, minute: int = 0) -> datetime:
    """An aware UTC moment from an ISO date and a UTC time."""
    year, month, date_ = (int(x) for x in day.split("-"))
    return datetime(year, month, date_, hour, minute, tzinfo=UTC)


@dataclass
class LeagueBuilder:
    """A tiny hand-built stats store: teams, players, games, box lines and listed positions."""

    session: Session
    teams: list[int] = field(default_factory=list)

    def team(self, team_id: int, abbr: str | None = None) -> int:
        code = abbr or f"T{team_id:02d}"
        self.session.add(
            Team(
                team_id=team_id,
                abbr=code,
                name=f"{code} Club",
                city=code,
                nickname="Club",
                is_active=True,
            )
        )
        self.teams.append(team_id)
        return team_id

    def player(self, player_id: int, name: str | None = None) -> int:
        self.session.add(Player(player_id=player_id, full_name=name or f"Player {player_id}"))
        return player_id

    def listing(
        self,
        player_id: int,
        team_id: int,
        label: str | None,
        *,
        season: str = SEASON,
        source: str = "commonTeamRoster",
    ) -> None:
        """One listed position. ``label`` of ``None`` is "the source named none" (all NULL)."""
        from nbastats.shared import positions

        weights = positions.normalise_nba_position(label) if label else None
        self.session.add(
            PlayerPositionSeason(
                player_id=player_id,
                season=season,
                team_id=team_id,
                position_raw=label,
                g_weight=weights.get("G", 0.0) if weights else None,
                f_weight=weights.get("F", 0.0) if weights else None,
                c_weight=weights.get("C", 0.0) if weights else None,
                source=source,
                fetched_at=datetime(2026, 1, 1),
                data_source="nba_api",
            )
        )

    def game(
        self,
        game_id: str,
        day: str,
        home: int,
        away: int,
        home_pts: int | None = None,
        away_pts: int | None = None,
        *,
        season: str = SEASON,
        kind: str = "Regular Season",
        status: str | None = None,
        minutes: float | None = 240.0,
        tipoff: datetime | None = None,
        home_lines: Sequence[tuple[int, float | None, int | None]] = (),
        away_lines: Sequence[tuple[int, float | None, int | None]] = (),
        team_rows: bool = True,
    ) -> str:
        """Add one game. With scores it is final (and gets both team rows); without, scheduled."""
        final = home_pts is not None and away_pts is not None
        self.session.add(
            Game(
                game_id=game_id,
                game_date=date.fromisoformat(day),
                season=season,
                season_type=kind,
                home_team_id=home,
                away_team_id=away,
                home_pts=home_pts,
                away_pts=away_pts,
                status=status or ("final" if final else "scheduled"),
                ingested_at=datetime(2026, 1, 1),
            )
        )
        if tipoff is not None:
            self.session.add(
                GameScheduleDetail(
                    game_id=game_id,
                    tipoff_utc=tipoff.replace(tzinfo=None),
                    source="scoreboard",
                    ingested_at=datetime(2026, 1, 1),
                )
            )
        if final and team_rows:
            for team, is_home, pts, opp in (
                (home, True, home_pts, away_pts),
                (away, False, away_pts, home_pts),
            ):
                self.session.add(
                    TeamGame(
                        game_id=game_id,
                        team_id=team,
                        is_home=is_home,
                        won=pts > opp,
                        minutes=minutes,
                        pts=pts,
                        opp_pts=opp,
                    )
                )
        for team, lines in ((home, home_lines), (away, away_lines)):
            for player_id, mins, pts in lines:
                self.session.add(
                    PlayerGameBasic(
                        game_id=game_id,
                        player_id=player_id,
                        team_id=team,
                        minutes=mins,
                        pts=pts,
                    )
                )
        return game_id

    def commit(self) -> None:
        self.session.commit()


@dataclass
class Store:
    engine: Engine
    session: Session
    league: LeagueBuilder


@pytest.fixture()
def store(tmp_path: Path) -> Iterator[Store]:
    """A fresh store with the full schema (stats, accounts, intel)."""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'league.db'}")
    init_db(engine)
    session = Session(engine, future=True)
    try:
        yield Store(engine, session, LeagueBuilder(session))
    finally:
        session.close()
        engine.dispose()


def three_team_league(league: LeagueBuilder) -> None:
    """The hand-worked league: six final games, teams 1, 2 and 3."""
    for team in (1, 2, 3):
        league.team(team)
    league.game("g1", "2025-11-01", 1, 2, 100, 90)
    league.game("g2", "2025-11-03", 2, 3, 110, 105)
    league.game("g3", "2025-11-05", 3, 1, 98, 102)
    league.game("g4", "2025-11-07", 1, 3, 120, 100)
    league.game("g5", "2025-11-09", 2, 1, 95, 99)
    league.game("g6", "2025-11-11", 3, 2, 101, 107)
    league.commit()


NOW = at("2025-11-12", 16, 0)


def close(a: float | None, b: float, tol: float = 1e-9) -> bool:
    return a is not None and math.isclose(a, b, rel_tol=0, abs_tol=tol)


# =========================================================================== the arithmetic


def test_every_figure_of_a_matchup_is_the_hand_calculation(store: Store) -> None:
    three_team_league(store.league)
    body = nba_matchup.team_matchup(store.session, home=1, away=2, window=3, now=NOW)
    assert body["league"] == "nba"
    assert body["season"] == SEASON and body["seasonType"] == "Regular Season"
    assert body["phase"] is None
    home, away = body["teams"]
    assert (home["side"], away["side"]) == ("home", "away")
    assert home["team"]["id"] == "1" and home["team"]["teamId"] == 1
    assert "clubCode" not in home["team"]

    # Team 1: 100, 102, 120, 99 for; 90, 98, 100, 95 against; four wins.
    assert home["games"] == 4 and home["record"] == {"wins": 4, "losses": 0}
    assert close(home["pointsPerGame"], 105.25)
    assert close(home["pointsAllowedPerGame"], 95.75)
    assert close(home["differentialPerGame"], 9.5)
    # Team 2: 90, 110, 95, 107 for; 100, 105, 99, 101 against; two wins.
    assert away["record"] == {"wins": 2, "losses": 2}
    assert close(away["pointsPerGame"], 100.5)
    assert close(away["pointsAllowedPerGame"], 101.25)
    assert close(away["differentialPerGame"], -0.75)
    # Every game appears once per side: league average points is points allowed per game.
    assert close(body["leagueAverage"]["pointsPerGame"], 1227 / 12)
    assert body["leagueAverage"]["teams"] == 3

    # Newest first, three of them, with the opponent and the score as played.
    assert [g["gameId"] for g in home["form"]] == ["g5", "g4", "g3"]
    latest = home["latestGame"]
    assert latest["gameId"] == "g5" and latest["date"] == "2025-11-09"
    assert (latest["teamScore"], latest["opponentScore"], latest["result"]) == (99, 95, "W")
    assert latest["isHome"] is False and latest["isNeutral"] is None
    assert latest["opponent"]["id"] == "2"
    assert home["lastN"] == {
        "window": 3,
        "games": 3,
        "pointsPerGame": pytest.approx((99 + 120 + 102) / 3),
        "pointsAllowedPerGame": pytest.approx((95 + 100 + 98) / 3),
    }
    assert home["last10"]["window"] == 10 and home["last10"]["games"] == 4
    assert close(home["last10"]["pointsPerGame"], 105.25)  # a longer window holds the games played

    # Home and away: team 1 was at home for g1 and g4. The NBA records no neutral sites.
    splits = home["venueSplits"]
    assert splits["home"] == {
        "games": 2,
        "pointsPerGame": pytest.approx(110.0),
        "pointsAllowedPerGame": pytest.approx(95.0),
    }
    assert splits["away"] == {
        "games": 2,
        "pointsPerGame": pytest.approx(100.5),
        "pointsAllowedPerGame": pytest.approx(96.5),
    }
    assert splits["neutral"] is None

    # No rank anywhere, no probability of winning, and a note that says why values are missing.
    assert "rank" not in " ".join(_keys(body)).lower()
    assert any("5 qualifying games" in note for note in body["notes"])
    assert body["availability"] == "partial"


def _keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [k for k in value] + [x for v in value.values() for x in _keys(v)]
    if isinstance(value, list):
        return [x for v in value for x in _keys(v)]
    return []


def test_a_team_with_no_games_has_null_figures_never_zero(store: Store) -> None:
    store.league.team(1)
    store.league.team(2)
    store.league.team(3)
    store.league.game("g1", "2025-11-01", 1, 2, 100, 90)
    store.league.commit()
    body = nba_matchup.team_matchup(store.session, home=1, away=3, window=3, now=NOW)
    idle = body["teams"][1]
    assert idle["games"] == 0 and idle["record"] == {"wins": 0, "losses": 0}
    for key in ("pointsPerGame", "pointsAllowedPerGame", "differentialPerGame"):
        assert idle[key] is None
    assert idle["latestGame"] is None and idle["form"] == []
    assert idle["lastN"]["games"] == 0 and idle["lastN"]["pointsPerGame"] is None
    assert idle["adjustedPointsAgainst"] == {"value": None, "games": 0}
    assert idle["venueSplits"]["home"]["pointsPerGame"] is None
    assert idle["venueSplits"]["neutral"] is None


def test_opponent_adjusted_values_match_their_definition(store: Store) -> None:
    """Four teams, each pair meeting twice with the scores below: every team has six games, so
    each clears the five-game gate, and the adjusted value is recomputed here from first
    principles: the opponent's points in the game less the opponent's mean in its *other* games."""
    for team in (1, 2, 3, 4):
        store.league.team(team)
    scores: list[tuple[str, int, int, int, int]] = []
    n = 0
    for home in (1, 2, 3, 4):
        for away in (1, 2, 3, 4):
            if home == away:
                continue
            n += 1
            scores.append((f"g{n:02d}", home, away, 90 + 3 * home + n, 85 + 2 * away + (n % 5)))
    for index, (gid, home, away, hp, ap) in enumerate(scores):
        store.league.game(
            gid, (date(2025, 11, 1) + timedelta(days=index)).isoformat(), home, away, hp, ap
        )
    store.league.commit()
    body = nba_matchup.team_matchup(store.session, home=1, away=2, window=5, now=NOW)

    rows = {t: [] for t in (1, 2, 3, 4)}  # team -> [(game, pts, opp_pts, opponent)]
    for gid, home, away, hp, ap in scores:
        rows[home].append((gid, hp, ap, away))
        rows[away].append((gid, ap, hp, home))

    def expected(team: int) -> tuple[float, float]:
        against, scored = [], []
        for gid, pts, opp_pts, opp in rows[team]:
            others = [r for r in rows[opp] if r[0] != gid]
            against.append(opp_pts - sum(r[1] for r in others) / len(others))
            scored.append(pts - sum(r[2] for r in others) / len(others))
        return sum(against) / len(against), sum(scored) / len(scored)

    for payload, team in zip(body["teams"], (1, 2)):
        want_against, want_for = expected(team)
        assert payload["games"] == 6
        assert payload["adjustedPointsAgainst"]["games"] == 6
        assert close(payload["adjustedPointsAgainst"]["value"], want_against)
        assert close(payload["adjustedPointsFor"]["value"], want_for)
    assert not any("5 qualifying games" in note for note in body["notes"])


def test_an_overtime_game_is_scaled_to_regulation_and_counted(store: Store) -> None:
    for team in (1, 2):
        store.league.team(team)
    store.league.game("g1", "2025-11-01", 1, 2, 110, 105, minutes=265.0)
    store.league.game("g2", "2025-11-03", 2, 1, 100, 98, minutes=240.0)
    store.league.game("g3", "2025-11-05", 1, 2, 101, 99, minutes=None)  # time not recorded
    store.league.commit()
    body = nba_matchup.team_matchup(store.session, home=1, away=2, window=3, now=NOW)
    home = body["teams"][0]
    by_game = {g["gameId"]: g for g in home["form"]}
    assert by_game["g1"]["overtimePeriods"] == 1
    assert by_game["g2"]["overtimePeriods"] == 0
    assert by_game["g3"]["overtimePeriods"] is None  # unknown, not zero
    # One game's time is unknown, so the regulation-scaled mean is withheld rather than partial.
    assert home["pointsPerRegulation"] is None and home["pointsAllowedPerRegulation"] is None
    only = {"g1"}
    store.session.query(Game).filter(Game.game_id.notin_(only)).delete(synchronize_session=False)
    store.session.query(TeamGame).filter(TeamGame.game_id.notin_(only)).delete(
        synchronize_session=False
    )
    store.session.commit()
    queries.clear_memo()
    again = nba_matchup.team_matchup(store.session, home=1, away=2, window=3, now=NOW)["teams"][0]
    assert close(again["pointsPerRegulation"], 110 * 240 / 265)
    assert close(again["pointsAllowedPerRegulation"], 105 * 240 / 265)
    assert close(again["pointsPerGame"], 110.0)  # the raw mean is the raw mean


# =========================================================================== the three ways in


def test_two_teams_carry_their_game_and_projection_only_when_scheduled(store: Store) -> None:
    three_team_league(store.league)
    none = nba_matchup.team_matchup(store.session, home=1, away=2, now=NOW)
    assert none["game"] is None and none["projection"] is None
    assert any("No game is scheduled" in note for note in none["notes"])

    store.league.game("g7", "2025-11-13", 1, 2)  # team 1 hosts team 2
    store.league.game("g8", "2025-11-14", 2, 1)  # the reverse fixture is a different game
    store.league.commit()
    queries.clear_memo()
    found = nba_matchup.team_matchup(store.session, home=1, away=2, now=NOW)
    assert found["game"]["gameId"] == "g7" and found["game"]["status"] == "scheduled"
    projection = found["projection"]
    assert projection is not None and projection["model"]["kind"] == "latest"
    assert projection["game"]["gameId"] == "g7"
    reverse = nba_matchup.team_matchup(store.session, home=2, away=1, now=NOW)
    assert reverse["game"]["gameId"] == "g8"


def test_a_teams_next_game_is_its_earliest_scheduled_one(store: Store) -> None:
    three_team_league(store.league)
    store.league.game("g9", "2025-11-16", 3, 1)
    store.league.game("g7", "2025-11-13", 1, 2)
    store.league.commit()
    body = nba_matchup.team_matchup(store.session, team=1, now=NOW)
    assert body["game"]["gameId"] == "g7"
    assert [t["team"]["id"] for t in body["teams"]] == ["1", "2"]
    # Team 3's only scheduled game is g9; take it away and team 3 has no next game.
    store.session.query(Game).filter(Game.game_id == "g9").delete(synchronize_session=False)
    store.session.commit()
    queries.clear_memo()
    with pytest.raises(ApiError) as caught:
        nba_matchup.team_matchup(store.session, team=3, now=NOW)
    assert caught.value.code == "game_not_found" and caught.value.http_status == 404


def test_a_games_matchup_equals_the_matchup_of_a_store_that_never_held_what_followed(
    tmp_path: Path,
) -> None:
    """No future leakage: the matchup of g5 built beside g6 and the games after it equals the one
    built where they do not exist."""

    def build(name: str, *, full: bool) -> dict[str, Any]:
        engine = create_db_engine(f"sqlite:///{tmp_path / name}")
        init_db(engine)
        with Session(engine, future=True) as session:
            league = LeagueBuilder(session)
            for team in (1, 2, 3):
                league.team(team)
            league.game("g1", "2025-11-01", 1, 2, 100, 90)
            league.game("g2", "2025-11-03", 2, 3, 110, 105)
            league.game("g3", "2025-11-05", 3, 1, 98, 102)
            league.game("g4", "2025-11-07", 1, 3, 120, 100)
            if full:
                league.game("g5", "2025-11-09", 2, 1, 95, 99)
                league.game("g6", "2025-11-11", 3, 2, 101, 107)
                league.game("g7", "2025-11-12", 1, 2, 140, 60)  # a blow-out that must not leak
            else:
                league.game("g5", "2025-11-09", 2, 1)  # never played in this store
            league.commit()
            queries.clear_memo()
            body = nba_matchup.team_matchup(session, game_id="g5", window=3, now=at("2025-11-20"))
        engine.dispose()
        return body

    full, truncated = build("full.db", full=True), build("truncated.db", full=False)
    assert full["game"]["gameId"] == "g5" and full["game"]["status"] == "final"
    assert truncated["game"]["status"] == "resultPending"
    assert any("cut off" in note for note in full["notes"])
    for key in ("teams", "leagueAverage"):
        assert full[key] == truncated[key], key
    # Before g5 only g1 to g4 count: team 2 has played g1 and g2.
    assert [t["games"] for t in full["teams"]] == [2, 3]
    assert full["projection"]["model"]["kind"] == "reconstructed"
    for side in ("home", "away"):
        assert full["projection"][side]["projectedPoints"] == pytest.approx(
            truncated["projection"][side]["projectedPoints"], abs=1e-12
        )


def test_a_game_anchored_matchup_uses_the_regular_season_for_a_playoff_game(
    store: Store,
) -> None:
    three_team_league(store.league)
    store.league.game("p1", "2025-11-13", 1, 2, kind="Playoffs")
    store.league.commit()
    body = nba_matchup.team_matchup(store.session, game_id="p1", now=at("2025-11-12"))
    assert body["seasonType"] == "Regular Season"
    assert body["game"]["gameId"] == "p1"
    assert [t["games"] for t in body["teams"]] == [4, 4]
    assert any("Regular Season only" in note and "Playoffs" in note for note in body["notes"])
    assert body["projection"] is not None
    assert any("regular season's ratings" in note for note in body["projection"]["notes"])


# =========================================================================== bad input


def test_a_matchup_refuses_what_it_cannot_honestly_answer(store: Store) -> None:
    three_team_league(store.league)
    session = store.session
    with pytest.raises(ApiError) as club:  # a EuroLeague club code is not an NBA team
        nba_matchup.team_matchup(session, home="ZZA", away=2, now=NOW)
    assert (club.value.code, club.value.http_status) == ("team_not_found", 404)
    with pytest.raises(ApiError) as unknown:
        nba_matchup.team_matchup(session, home=1, away=99, now=NOW)
    assert unknown.value.code == "team_not_found"
    with pytest.raises(ApiError) as itself:
        nba_matchup.team_matchup(session, home=1, away=1, now=NOW)
    assert itself.value.code == "bad_request"
    for window in (2, 16, 0, -1):
        with pytest.raises(ApiError) as bad:
            nba_matchup.team_matchup(session, home=1, away=2, window=window, now=NOW)
        assert (bad.value.code, bad.value.field) == ("bad_request", "window")
    with pytest.raises(ApiError) as kind:
        nba_matchup.team_matchup(session, home=1, away=2, season_type="Exhibition", now=NOW)
    assert (kind.value.code, kind.value.field) == ("bad_request", "seasonType")
    with pytest.raises(ApiError) as euro:  # a EuroLeague season code is not a season here
        nba_matchup.team_matchup(session, home=1, away=2, season="E2026", now=NOW)
    assert euro.value.code == "bad_request"
    with pytest.raises(ApiError) as missing:
        nba_matchup.team_matchup(session, home=1, away=2, season="2030-31", now=NOW)
    assert (missing.value.code, missing.value.http_status) == ("season_not_loaded", 422)
    with pytest.raises(ApiError) as game:
        nba_matchup.team_matchup(session, game_id="0000000000", now=NOW)
    assert game.value.code == "game_not_found"
    with pytest.raises(ApiError) as none:
        nba_matchup.team_matchup(session, now=NOW)
    assert none.value.code == "bad_request"


def test_an_empty_store_says_no_season_is_loaded(store: Store) -> None:
    with pytest.raises(ApiError) as caught:
        nba_matchup.team_matchup(store.session, home=1, away=2, now=NOW)
    assert caught.value.code == "season_not_loaded" and caught.value.http_status == 422


# =========================================================================== context helpers


def test_a_game_waiting_for_its_result_is_not_upcoming(store: Store) -> None:
    three_team_league(store.league)
    store.league.game("known", "2025-11-12", 1, 2, tipoff=at("2025-11-12", 19, 0))
    store.league.game("unknown", "2025-11-12", 2, 3)
    store.league.game("live", "2025-11-12", 3, 1, status="live", tipoff=at("2025-11-12", 20, 0))
    store.league.commit()

    def statuses(now: datetime) -> dict[str, str]:
        ctx = queries.build_context(store.session, SEASON, now=now)
        return {g: ctx.game_status(ctx.game(g)) for g in ("known", "unknown", "live", "g1")}

    assert statuses(at("2025-11-12", 21, 59)) == {
        "known": "scheduled",
        "unknown": "scheduled",
        "live": "scheduled",
        "g1": "final",
    }
    # Three hours after a known tip-off; for an unknown one, three hours after 23:59 Eastern
    # (04:59Z the next day, so 07:59Z).
    assert statuses(at("2025-11-12", 22, 1))["known"] == "resultPending"
    assert statuses(at("2025-11-12", 22, 1))["live"] == "scheduled"  # in progress, not yet late
    assert statuses(at("2025-11-12", 23, 1))["live"] == "resultPending"
    assert statuses(at("2025-11-13", 7, 0))["unknown"] == "scheduled"
    assert statuses(at("2025-11-13", 7, 58))["unknown"] == "scheduled"
    assert statuses(at("2025-11-13", 8, 1))["unknown"] == "resultPending"


def test_the_three_instants_of_a_game_err_towards_not_claiming_knowledge(store: Store) -> None:
    three_team_league(store.league)
    store.league.game("known", "2025-11-12", 1, 2, tipoff=at("2025-11-12", 19, 30))
    store.league.game("unknown", "2025-11-12", 2, 3)
    store.league.commit()
    ctx = queries.build_context(store.session, SEASON, now=NOW)
    known, unknown = ctx.game("known"), ctx.game("unknown")
    assert ctx.start_of(known) == ctx.deadline_of(known) == at("2025-11-12", 19, 30)
    # US Eastern is UTC-5 in November: midnight is 05:00Z and noon is 17:00Z.
    assert ctx.start_of(unknown) == at("2025-11-12", 5, 0)
    assert ctx.deadline_of(unknown) == at("2025-11-12", 17, 0)
    assert queries.game_reference(unknown, None) == at("2025-11-13", 4, 59)  # 23:59 Eastern


def test_the_next_slate_token_survives_a_store_that_is_behind_the_clock(store: Store) -> None:
    three_team_league(store.league)
    store.league.game("old", "2025-11-08", 1, 2)  # scheduled, but before the newest final: stale
    store.league.game("n1", "2025-11-13", 1, 3)
    store.league.game("n2", "2025-11-15", 2, 3)
    store.league.commit()
    s = store.session
    assert queries.next_slate_date(s, at("2025-11-12")) == date(2025, 11, 13)
    assert queries.next_slate_date(s, at("2025-11-13", 3)) == date(
        2025, 11, 13
    )  # still 22:00 Nov 12 ET
    assert queries.next_slate_date(s, at("2025-11-14", 12)) == date(2025, 11, 15)
    # The clock has run on past everything scheduled (the demo league, a stalled ingest): the
    # answer is the first scheduled date after the newest final game, not "nothing".
    assert queries.next_slate_date(s, at("2026-03-01")) == date(2025, 11, 13)
    assert queries.latest_final_date(s) == date(2025, 11, 11)
    assert queries.next_slate_date(s, at("2026-03-01"), "2024-25") is None


def test_primary_bucket_names_the_first_position_of_a_hybrid() -> None:
    cases = {
        "G": "G",
        "PG": "G",
        "Guard": "G",
        "G-F": "G",
        "F-G": "F",
        "Guard-Forward": "G",
        "Forward-Guard": "F",
        "F": "F",
        "F-C": "F",
        "C-F": "C",
        "Center": "C",
        "Forward-Center": "F",
        "PG-SG": None,  # not on the table: unknown, not interpreted
        "": None,
        None: None,
    }
    for raw, expected in cases.items():
        assert queries.primary_bucket(raw) == expected, raw


def test_season_resolution_is_the_stores_not_the_calendars(store: Store) -> None:
    three_team_league(store.league)
    store.league.game("o1", "2024-11-01", 1, 2, 100, 90, season="2024-25")
    store.league.commit()
    s = store.session
    assert queries.loaded_seasons(s) == ["2024-25", "2025-26"]
    assert queries.resolve_season(s, None) == queries.resolve_season(s, "latest") == "2025-26"
    assert queries.previous_season_of(s, "2025-26") == "2024-25"
    assert queries.previous_season_of(s, "2024-25") is None  # last season means last season
    assert queries.parse_team_id("1610612738") == 1610612738
    assert queries.parse_team_id(7) == 7
    for bad in ("ZZA", "E2026", "", " ", "1.5", None, True):
        assert queries.parse_team_id(bad) is None, bad


def test_the_memo_key_moves_with_the_data_it_depends_on(store: Store) -> None:
    three_team_league(store.league)
    ctx = queries.build_context(store.session, SEASON, now=NOW)
    before = ctx.data_key()
    store.league.game("g7", "2025-11-12", 1, 2, 101, 99)
    store.league.commit()
    after = queries.build_context(store.session, SEASON, now=NOW).data_key()
    assert before != after
    assert queries.build_context(store.session, SEASON, now=NOW).data_key() == after
    assert queries.digest("a", 1.0000000000001, [2.0]) == queries.digest("a", 1.0, [2.0])
    assert queries.digest("a", 1.0001) != queries.digest("a", 1.0)
