"""What the live ingest writes, when it writes it, and what it refuses to write.

Three groups, in the order data travels:

**The writer** (``write.py``): the schedule upsert (a final never goes back to scheduled, a
corrected score invalidates the lines, a look-alike club is refused), the box-score write (lines,
team rows, DNP lines with every stat NULL, idempotence, one version bump per change), quarantine (a
hard rule failing keeps the previous good lines and names the rule), raw-payload retention, cursors
and source state.

**The jobs** (``jobs.py``): each of the worker's five entry points against a scripted fake service
with explicit clock values. The cadence is pinned (the 06:00 sweep, polls from tip-off plus 105
minutes, corrections at 24 and 48 hours, weekly structure), and so is what a job does when nothing
is due (no request), when a switch is off (no request, even with ``force``), when the service says
no (a six-hour breaker that survives a restart) and when a response is the wrong shape (the source
becomes ``unreadable``, says which key it wanted, and the job then waits an hour, two, four, six
before asking again unless forced). The sweep is pinned too: a postponed game never holds "the
current round" back, its round is re-read so a new date is noticed, and a run that cannot read every
round of the sweep goes on at the next tick instead of marking the sweep done.

**The command line** (``__main__``): the parser, the backfill's "print the plan, ask nothing without
``--yes``", ``status`` and ``reconcile``.

The fake service serves the authored fixtures (invented clubs, fantasy names, invented numbers). No
network is opened and no time passes: the polite client gets a no-op ``sleep``.
"""

from __future__ import annotations

import copy
import inspect
import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nbastats.euroleague.db import StoreKindMismatch, ensure_identity, read_identity
from nbastats.euroleague.ingest import __main__ as cli
from nbastats.euroleague.ingest import client as client_module
from nbastats.euroleague.ingest import endpoints, jobs, parse, reconcile, write
from nbastats.euroleague.ingest.client import EuroLeagueClient, Fetched, RawPayloadStore
from nbastats.euroleague.models import (
    ElClub,
    ElClubAlias,
    ElClubSeason,
    ElGame,
    ElIngestLog,
    ElIntelNewsFeed,
    ElIntelNewsItem,
    ElIntelNewsSubject,
    ElIntelStatus,
    ElJobState,
    ElPerson,
    ElPersonAlias,
    ElPlayerGame,
    ElRawPayload,
    ElRegistration,
    ElSeason,
    ElSourceState,
    ElSyncState,
    ElTeamGame,
)
from nbastats.euroleague.models import STAT_COLUMNS
from nbastats.intel.http import PoliteClient

UTC = timezone.utc
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "euroleague" / "authored"
BASE = "https://api-live.euroleague.net/v2/competitions/E/seasons/E2031"
SEASON = "E2031"
NOW = datetime(2031, 10, 4, 9, 0)  # naive UTC, for the writer
T0 = datetime(2031, 10, 1, 8, 0, tzinfo=UTC)


def jload(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ------------------------------------------------------------------------ a scripted service


class Service:
    """The EuroLeague service, scripted. Serves the authored fixtures unless told otherwise."""

    def __init__(self) -> None:
        self.now = T0
        self.calls: list[str] = []
        self.headers: list[httpx.Headers] = []
        self.bodies: dict[str, bytes] = {}
        self.queue: dict[str, list[httpx.Response]] = {}
        self.fixed: dict[str, httpx.Response] = {}
        self.etags: dict[str, str] = {}  # url -> validator: the service then honours If-None-Match

    # routes ------------------------------------------------------------------------------
    def serve_authored(self) -> "Service":
        for suffix, name in {
            "rounds": "rounds.json",
            "clubs": "clubs.json",
            "games?roundNumber=1": "games_round_1.json",
            "games?roundNumber=2": "games_round_2.json",
            "games?roundNumber=3": "games_round_3.json",
            "games/1/stats": "box_game_1.json",
            "games/3/stats": "box_game_3_overtime.json",
            "clubs/ZZA/people": "people_ZZA.json",
        }.items():
            self.bodies[f"{BASE}/{suffix}"] = raw(name)
        return self

    def put(self, suffix: str, payload: Any) -> None:
        self.bodies[f"{BASE}/{suffix}"] = json.dumps(payload).encode()

    def status(self, suffix: str, status: int, **headers: str) -> None:
        self.fixed[f"{BASE}/{suffix}"] = httpx.Response(status, headers=headers)

    def then(self, suffix: str, *responses: httpx.Response) -> None:
        self.queue.setdefault(f"{BASE}/{suffix}", []).extend(responses)

    # transport ---------------------------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        self.headers.append(request.headers)
        if self.queue.get(url):
            return self.queue[url].pop(0)
        if url in self.fixed:
            return self.fixed[url]
        if url in self.bodies:
            headers = {"content-type": "application/json"}
            if url in self.etags:
                if request.headers.get("if-none-match") == self.etags[url]:
                    return httpx.Response(304)
                headers["etag"] = self.etags[url]
            return httpx.Response(200, content=self.bodies[url], headers=headers)
        return httpx.Response(404, text="not found")

    def polite(self) -> PoliteClient:
        return PoliteClient(
            transport=httpx.MockTransport(self.handler),
            clock=lambda: 0.0,
            wall_clock=lambda: self.now,
            sleep=lambda seconds: None,
            jitter=lambda: 0.0,
        )

    def client(self, budget: int | None = 40) -> EuroLeagueClient:
        return EuroLeagueClient(self.polite(), max_requests=budget)

    def suffixes(self) -> list[str]:
        return [c.replace(BASE + "/", "") for c in self.calls]


@pytest.fixture()
def service() -> Service:
    return Service().serve_authored()


@pytest.fixture()
def run(service: Service, el_engine: Any, tmp_path: Path) -> Callable[..., dict[str, Any]]:
    """``run(job, now, **kw)``: one worker job against ``service`` and the test's store."""

    def go(job: Callable[..., dict[str, Any]], now: datetime, **kw: Any) -> dict[str, Any]:
        service.now = now
        if job is not jobs.run_news:  # the headline job has its own polite client
            kw.setdefault("client", service.client())
        return job(now=now, engine=el_engine, data_dir=tmp_path, zone=UTC, **kw)

    return go


@pytest.fixture()
def session(el_engine: Any) -> Any:
    with Session(el_engine, future=True) as s:
        yield s


def rows(session: Session, model: Any) -> list[Any]:
    session.expire_all()
    return list(session.execute(select(model)).scalars())


def count(session: Session, model: Any) -> int:
    session.expire_all()
    return int(session.scalar(select(func.count()).select_from(model)))


def sync_version(session: Session) -> int:
    session.expire_all()
    state = session.get(ElSyncState, 1)
    return state.sync_version if state else 0


def games_of(name: str, **changes: Any) -> list[parse.GameInfo]:
    return list(parse.parse_games(jload(name), expect_season=SEASON).items)


def live(session: Session) -> Session:
    write.ensure_live_writer(session)
    return session


def box(name: str = "box_game_1.json") -> parse.BoxScore:
    return parse.parse_box_score(jload(name))


def stored_game(session: Session, game_id: str = "E2031-0001") -> ElGame:
    session.expire_all()
    game = session.get(ElGame, game_id)
    assert game is not None
    return game


def with_round_one(session: Session) -> None:
    live(session)
    write.upsert_games(session, SEASON, games_of("games_round_1.json"), now=NOW)
    session.commit()


# ========================================================================= the writer: schedule


def test_a_round_is_stored_with_live_provenance_clubs_and_a_current_season(
    session: Session,
) -> None:
    live(session)
    result = write.upsert_games(session, SEASON, games_of("games_round_1.json"), now=NOW)
    session.commit()
    assert (len(result.created), len(result.updated), len(result.unchanged)) == (2, 0, 0)
    assert result.results == ["E2031-0001", "E2031-0002"] and result.version == 1
    game = stored_game(session)
    assert (game.status, game.stats_status, game.data_source) == ("final", "none", "euroleague-v2")
    assert (game.home_club_code, game.away_club_code, game.home_pts, game.away_pts) == (
        "ZZA",
        "ZZB",
        84,
        79,
    )
    assert game.home_partials_json == "[21, 19, 20, 24]" and game.ot_periods == 0
    assert game.tipoff_utc == datetime(2031, 10, 2, 17, 45) and game.game_date == date(2031, 10, 2)
    assert (game.venue_name, game.attendance, game.is_neutral) == ("Harbour Dome", 8120, False)
    assert game.phase_code == "RS" and game.round_number == 1 and game.game_code == 1
    season = session.get(ElSeason, SEASON)
    assert season is not None and season.is_current and season.label == "2031-32"
    assert {c.club_code for c in rows(session, ElClub)} == {"ZZA", "ZZB", "ZZC", "ZZD"}
    aliases = {(a.system, a.code) for a in rows(session, ElClubAlias)}
    assert {("official", "ZZA"), ("tv", "ZHB")} <= aliases
    assert count(session, ElClubSeason) == 4
    state = session.get(ElSyncState, 1)
    assert state is not None and state.mode == "live" and state.data_through == date(2031, 10, 3)


def test_storing_the_same_round_again_writes_nothing_and_bumps_nothing(session: Session) -> None:
    with_round_one(session)
    again = write.upsert_games(
        session, SEASON, games_of("games_round_1.json"), now=NOW + timedelta(hours=1)
    )
    session.commit()
    assert again.unchanged == ["E2031-0001", "E2031-0002"] and again.version is None
    assert not again.changed and sync_version(session) == 1
    assert stored_game(session).ingested_at == NOW  # an unchanged game is not touched


def test_a_scheduled_game_becomes_final_and_is_reported_as_a_new_result(session: Session) -> None:
    live(session)
    payload = jload("games_round_2.json")
    for side in ("local", "road"):
        payload["data"][0][side]["score"] = None
        payload["data"][0][side]["partials"] = {"extraPeriods": {}}
    payload["data"][0]["played"] = False
    write.upsert_games(session, SEASON, list(parse.parse_games(payload).items), now=NOW)
    session.commit()
    assert stored_game(session, "E2031-0003").status == "scheduled"
    result = write.upsert_games(session, SEASON, games_of("games_round_2.json"), now=NOW)
    session.commit()
    assert result.results == ["E2031-0003"] and result.updated == ["E2031-0003"]
    final = stored_game(session, "E2031-0003")
    assert (final.status, final.home_pts, final.ot_periods) == ("final", 91, 1)
    assert final.home_partials_json == "[20, 22, 19, 22, 8]"


def test_a_final_game_never_goes_back_to_scheduled(session: Session) -> None:
    with_round_one(session)
    version = sync_version(session)
    payload = jload("games_round_1.json")
    payload["data"][0]["played"] = False
    payload["data"][0]["utcDate"] = "2031-10-05T17:45:00Z"
    result = write.upsert_games(session, SEASON, list(parse.parse_games(payload).items), now=NOW)
    session.commit()
    assert result.regressions == ["E2031-0001"]
    assert any("no longer calls this game played" in n for n in result.notes)
    game = stored_game(session)
    assert (game.status, game.home_pts) == ("final", 84)
    assert game.tipoff_utc == datetime(2031, 10, 2, 17, 45)  # nothing was updated
    assert sync_version(session) == version


def test_a_corrected_final_score_invalidates_the_lines_until_they_are_checked_again(
    session: Session,
) -> None:
    with_round_one(session)
    assert write.write_box_score(session, "E2031-0001", box(), now=NOW).wrote
    session.commit()
    payload = jload("games_round_1.json")
    payload["data"][0]["local"]["score"] = 85
    payload["data"][0]["local"]["partials"]["partials4"] = 25  # the partials move with the score
    result = write.upsert_games(session, SEASON, list(parse.parse_games(payload).items), now=NOW)
    session.commit()
    assert result.corrections == ["E2031-0001"]
    game = stored_game(session)
    assert (game.home_pts, game.stats_status) == (85, "none")  # no longer proven consistent
    assert count(session, ElPlayerGame) == 24  # the lines themselves are kept
    recheck = write.write_box_score(session, "E2031-0001", box(), now=NOW)
    assert recheck.outcome == "quarantined"  # the old box no longer adds up to the new score
    assert "team_points" in {v.rule for v in recheck.hard}


def test_changed_partials_alone_also_send_a_checked_game_back_for_checking(
    session: Session,
) -> None:
    with_round_one(session)
    write.write_box_score(session, "E2031-0001", box(), now=NOW)
    session.commit()
    payload = jload("games_round_1.json")
    payload["data"][0]["local"]["partials"].update(partials1=20, partials2=20)
    write.upsert_games(session, SEASON, list(parse.parse_games(payload).items), now=NOW)
    session.commit()
    assert stored_game(session).stats_status == "none"


def test_a_club_that_resembles_a_stored_club_is_refused_with_its_games(session: Session) -> None:
    live(session)
    session.add(
        ElClub(club_code="ZZQ", tv_code="ZHB", name="Quillon BC")
    )  # the store knows ZHB as ZZQ
    session.commit()
    result = write.upsert_games(session, SEASON, games_of("games_round_1.json"), now=NOW)
    session.commit()
    assert [r.path for r in result.rejected] == ["game 1"]
    assert "ZZQ (Quillon BC) with the same broadcast code" in result.rejected[0].reason
    assert result.created == ["E2031-0002"]  # the other game is unaffected
    assert session.get(ElClub, "ZZA") is None


def test_a_provisional_workbook_fixture_is_re_keyed_to_the_official_game(session: Session) -> None:
    live(session)
    for code, tv, name in (
        ("ZZA", "ZHB", "Zenith Harbour BC"),
        ("ZZB", "BMF", "Brightmoor Falcons"),
    ):
        session.add(ElClub(club_code=code, tv_code=tv, name=name))
    session.add(
        ElSeason(
            season_code=SEASON,
            competition_code="E",
            label="2031-32",
            start_year=2031,
            is_current=True,
        )
    )
    session.add(
        ElGame(
            game_id="E2031-R01-01",
            season_code=SEASON,
            game_code=None,
            round_number=1,
            phase_code="RS",
            game_date=date(2031, 10, 2),
            home_club_code="ZZA",
            away_club_code="ZZB",
            status="scheduled",
            stats_status="none",
            data_source="workbook:abcd1234",
            ingested_at=NOW,
            home_advantage_override=1.5,
        )
    )
    session.add(
        ElIntelStatus(
            club_code="ZZA",
            game_id="E2031-R01-01",
            player_name_raw="x",
            status="out",
            source_kind="manual",
            source_label="l",
            source_published_at=NOW,
            as_of=NOW,
            recorded_at=NOW,
        )
    )
    session.commit()
    result = write.upsert_games(session, SEASON, games_of("games_round_1.json"), now=NOW)
    session.commit()
    assert result.rekeyed == [("E2031-R01-01", "E2031-0001")]
    assert session.get(ElGame, "E2031-R01-01") is None
    game = stored_game(session)
    assert (game.game_code, game.status, game.data_source) == (1, "final", "euroleague-v2")
    assert (
        game.home_advantage_override == 1.5
    )  # the user's workbook value is not the service's to erase
    assert session.execute(select(ElIntelStatus.game_id)).scalar_one() == "E2031-0001"


def test_a_game_code_between_other_clubs_than_stored_is_left_alone(session: Session) -> None:
    with_round_one(session)
    payload = jload("games_round_1.json")
    payload["data"][0]["local"]["club"]["code"] = "ZZD"
    payload["data"][0]["local"]["club"]["tvCode"] = "DMW"
    payload["data"][0]["local"]["club"]["name"] = "Dunmere Wolves"
    result = write.upsert_games(session, SEASON, list(parse.parse_games(payload).items), now=NOW)
    assert any("left alone" in r.reason for r in result.rejected)
    assert stored_game(session).home_club_code == "ZZA"


def test_the_same_matchup_under_another_game_code_is_left_alone(session: Session) -> None:
    with_round_one(session)
    payload = jload("games_round_1.json")
    payload["data"][0]["gameCode"] = 9
    result = write.upsert_games(session, SEASON, list(parse.parse_games(payload).items), now=NOW)
    assert [r.path for r in result.rejected] == ["game 9"]
    assert "already stored as E2031-0001" in result.rejected[0].reason
    assert session.get(ElGame, "E2031-0009") is None


def test_a_workbook_game_is_not_changed_by_an_identical_live_schedule(session: Session) -> None:
    live(session)
    with_round_one(session)
    game = stored_game(session)
    game.data_source = "workbook:abcd1234"
    session.commit()
    result = write.upsert_games(
        session, SEASON, games_of("games_round_1.json"), now=NOW + timedelta(days=1)
    )
    session.commit()
    assert result.unchanged == ["E2031-0001", "E2031-0002"]
    assert stored_game(session).data_source == "workbook:abcd1234"


def test_issues_noticed_in_the_schedule_are_reported_as_notes(session: Session) -> None:
    live(session)
    payload = jload("games_round_1.json")
    payload["data"][0]["local"]["score"] = payload["data"][0]["road"]["score"] = 70
    result = write.upsert_games(session, SEASON, list(parse.parse_games(payload).items), now=NOW)
    assert any("tied score 70-70" in n for n in result.notes)
    assert stored_game(session).status == "scheduled"


def test_ensure_season_marks_only_the_newest_as_current(session: Session) -> None:
    write.ensure_season(session, "E2030")
    write.ensure_season(session, "E2031")
    write.ensure_season(session, "E2029")
    current = {s.season_code for s in rows(session, ElSeason) if s.is_current}
    assert current == {"E2031"}
    with pytest.raises(ValueError):
        write.ensure_season(session, "2031-32x")


# ============================================================================ the writer: boxes


def test_a_box_score_writes_every_line_the_team_rows_and_the_people(session: Session) -> None:
    with_round_one(session)
    result = write.write_box_score(session, "E2031-0001", box(), now=NOW)
    session.commit()
    assert result.outcome == "written" and result.lines == 24 and result.version == 2
    assert result.hard == () and result.soft == ()
    game = stored_game(session)
    assert game.stats_status == "ok" and game.raw_sha256 == parse.box_digest(box())
    assert count(session, ElPlayerGame) == 24 and count(session, ElTeamGame) == 2
    assert count(session, ElPerson) == 24 and count(session, ElRegistration) == 24
    person = session.execute(select(ElPerson).limit(1)).scalar_one()
    assert person.code_system == "official" and "," not in person.name and person.display_name
    registration = session.execute(select(ElRegistration).limit(1)).scalar_one()
    assert registration.source == "euroleague-v2" and registration.season_code == SEASON
    teams = {t.club_code: t for t in rows(session, ElTeamGame)}
    home, away = teams["ZZA"], teams["ZZB"]
    assert (home.is_home, home.pts, home.opp_pts, home.won, home.opp_club_code) == (
        True,
        84,
        79,
        True,
        "ZZB",
    )
    assert (away.is_home, away.pts, away.opp_pts, away.won) == (False, 79, 84, False)
    assert home.seconds_played == 12000 == away.seconds_played  # five men for forty minutes
    assert home.reb == 38  # includes the team-rebounds row that no player is credited with
    lines = rows(session, ElPlayerGame)
    assert sum(l.pts or 0 for l in lines if l.club_code == "ZZA") == 84


def test_a_player_who_did_not_play_is_a_dnp_line_with_every_stat_null(session: Session) -> None:
    with_round_one(session)
    write.write_box_score(session, "E2031-0001", box(), now=NOW)
    session.commit()
    dnp = [l for l in rows(session, ElPlayerGame) if l.participation == "dnp"]
    assert len(dnp) == 1
    for column in STAT_COLUMNS:
        assert getattr(dnp[0], column) is None, column
    assert dnp[0].dorsal and dnp[0].position_code_at_game in (1, 2, 3)
    played = [l for l in rows(session, ElPlayerGame) if l.participation == "played"]
    assert all(l.seconds_played and l.pts is not None for l in played)
    assert sum(1 for l in played if l.is_starter) == 10


def test_an_overtime_box_score_stores_the_extra_time(session: Session) -> None:
    live(session)
    write.upsert_games(session, SEASON, games_of("games_round_2.json"), now=NOW)
    result = write.write_box_score(session, "E2031-0003", box("box_game_3_overtime.json"), now=NOW)
    session.commit()
    assert result.wrote and stored_game(session, "E2031-0003").ot_periods == 1
    assert {t.seconds_played for t in rows(session, ElTeamGame)} == {13500}


def test_writing_the_same_box_score_again_writes_and_bumps_nothing(session: Session) -> None:
    with_round_one(session)
    write.write_box_score(session, "E2031-0001", box(), now=NOW)
    session.commit()
    version = sync_version(session)
    again = write.write_box_score(session, "E2031-0001", box(), now=NOW + timedelta(days=1))
    session.commit()
    assert again.outcome == "unchanged" and again.version is None
    assert sync_version(session) == version
    assert stored_game(session).ingested_at == NOW


def test_a_corrected_box_score_replaces_the_lines_and_bumps_the_version_once(
    session: Session,
) -> None:
    with_round_one(session)
    write.write_box_score(session, "E2031-0001", box(), now=NOW)
    session.commit()
    version = sync_version(session)
    payload = jload("box_game_1.json")
    payload["local"]["players"][0]["stats"]["steals"] += 1.0
    payload["local"]["players"][0]["stats"]["valuation"] += 1.0
    payload["local"]["total"]["valuation"] += 1.0
    result = write.write_box_score(
        session, "E2031-0001", parse.parse_box_score(payload), now=NOW + timedelta(days=1)
    )
    session.commit()
    assert result.wrote and sync_version(session) == version + 1
    assert (
        count(session, ElPlayerGame) == 24 and count(session, ElTeamGame) == 2
    )  # replaced, not added
    first = next(p for p in jload("box_game_1.json")["local"]["players"][:1])
    stored = session.get(ElPlayerGame, ("E2031-0001", first["player"]["person"]["code"]))
    assert stored is not None and stored.stl == int(first["stats"]["steals"]) + 1


def test_a_hard_failure_quarantines_without_writing_a_single_line(session: Session) -> None:
    with_round_one(session)
    payload = jload("box_game_1.json")
    payload["local"]["players"][0]["stats"]["fieldGoalsMade2"] += 1.0
    payload["local"]["players"][0]["stats"]["fieldGoalsAttempted2"] += 1.0
    payload["local"]["players"][0]["stats"][
        "points"
    ] += 2.0  # self-consistent, but the team did not score it
    version = sync_version(session)
    result = write.write_box_score(session, "E2031-0001", parse.parse_box_score(payload), now=NOW)
    session.commit()
    assert result.outcome == "quarantined" and result.stats_status == "quarantined"
    assert {v.rule for v in result.hard} == {"team_points"} and "team_points" in (
        result.reason or ""
    )
    assert stored_game(session).stats_status == "quarantined"
    assert count(session, ElPlayerGame) == 0 and count(session, ElTeamGame) == 0
    assert result.version == version + 1  # the status flip is visible to readers: one bump
    again = write.write_box_score(session, "E2031-0001", parse.parse_box_score(payload), now=NOW)
    assert again.outcome == "quarantined" and again.version is None  # not bumped a second time


def test_a_quarantine_keeps_the_previous_good_lines_and_a_good_box_recovers(
    session: Session,
) -> None:
    with_round_one(session)
    write.write_box_score(session, "E2031-0001", box(), now=NOW)
    session.commit()
    good_points = {l.person_code: l.pts for l in rows(session, ElPlayerGame)}
    payload = jload("box_game_1.json")
    payload["road"]["players"][0]["stats"][
        "timePlayed"
    ] = 100.0  # the team's minutes no longer add up
    bad = write.write_box_score(session, "E2031-0001", parse.parse_box_score(payload), now=NOW)
    session.commit()
    assert bad.outcome == "quarantined" and {v.rule for v in bad.hard} == {"team_time"}
    assert {l.person_code: l.pts for l in rows(session, ElPlayerGame)} == good_points  # kept
    assert stored_game(session).stats_status == "quarantined"
    recovered = write.write_box_score(session, "E2031-0001", box(), now=NOW + timedelta(days=1))
    session.commit()
    assert recovered.outcome == "written" and stored_game(session).stats_status == "ok"


def test_a_box_score_for_the_wrong_clubs_is_quarantined_not_attributed(session: Session) -> None:
    live(session)
    write.upsert_games(session, SEASON, games_of("games_round_1.json"), now=NOW)
    result = write.write_box_score(
        session, "E2031-0002", box("box_game_1.json"), now=NOW
    )  # ZZA v ZZB's box
    assert result.outcome == "quarantined" and "club_mismatch" in {v.rule for v in result.hard}
    assert count(session, ElPlayerGame) == 0


def test_a_player_with_zero_seconds_and_points_quarantines_the_game(session: Session) -> None:
    with_round_one(session)
    payload = jload("box_game_1.json")
    dnp = next(p for p in payload["local"]["players"] if p["stats"]["timePlayed"] == 0)
    dnp["stats"]["points"] = 2.0
    result = write.write_box_score(session, "E2031-0001", parse.parse_box_score(payload), now=NOW)
    assert "dnp_has_stats" in {v.rule for v in result.hard}


def test_soft_failures_are_reported_but_the_game_is_still_written(session: Session) -> None:
    with_round_one(session)
    payload = jload("box_game_1.json")
    for entry in payload["local"]["players"]:
        entry["stats"]["startFive"] = False  # no starters recorded as such: a soft failure
    result = write.write_box_score(session, "E2031-0001", parse.parse_box_score(payload), now=NOW)
    assert result.wrote and {v.rule for v in result.soft} == {"starters"}


def test_a_box_is_refused_when_the_game_has_no_final_score_or_does_not_exist(
    session: Session,
) -> None:
    live(session)
    write.upsert_games(session, SEASON, games_of("games_round_2.json"), now=NOW)
    unplayed = write.write_box_score(session, "E2031-0004", box(), now=NOW)
    assert unplayed.outcome == "refused" and "final score" in (unplayed.reason or "")
    missing = write.write_box_score(session, "E2031-0099", box(), now=NOW)
    assert missing.outcome == "refused" and "no such game" in (missing.reason or "")
    payload = jload("box_game_1.json")
    for side in ("local", "road"):
        for entry in payload[side]["players"]:
            entry["player"]["season"]["code"] = "E2030"
    write.upsert_games(session, SEASON, games_of("games_round_1.json"), now=NOW)
    wrong = write.write_box_score(session, "E2031-0001", parse.parse_box_score(payload), now=NOW)
    assert wrong.outcome == "refused" and "E2030" in (wrong.reason or "")
    assert count(session, ElPlayerGame) == 0


def test_an_absent_stat_is_stored_as_null_never_zero(session: Session) -> None:
    with_round_one(session)
    payload = jload("box_game_1.json")
    for side in ("local", "road"):
        for entry in payload[side]["players"]:
            entry["stats"].pop("foulsReceived")
    assert write.write_box_score(
        session, "E2031-0001", parse.parse_box_score(payload), now=NOW
    ).wrote
    played = [l for l in rows(session, ElPlayerGame) if l.participation == "played"]
    assert played and all(l.fouls_drawn is None for l in played)
    assert all(l.blk_against is not None for l in played)  # the ones that were sent are kept


def test_people_and_registrations_from_a_box_are_not_duplicated_by_a_later_one(
    session: Session,
) -> None:
    live(session)
    write.upsert_games(session, SEASON, games_of("games_round_1.json"), now=NOW)
    write.write_box_score(session, "E2031-0001", box(), now=NOW)
    before = (count(session, ElPerson), count(session, ElRegistration))
    payload = jload("box_game_1.json")
    payload["local"]["players"][0]["stats"]["assistances"] += 1.0
    payload["local"]["players"][0]["stats"]["valuation"] += 1.0
    payload["local"]["total"]["assistances"] += 1.0
    payload["local"]["total"]["valuation"] += 1.0
    write.write_box_score(session, "E2031-0001", parse.parse_box_score(payload), now=NOW)
    assert (count(session, ElPerson), count(session, ElRegistration)) == before


# ================================================================== the writer: store and people


def test_a_synthetic_store_refuses_live_writes(fresh_demo_engine: Any) -> None:
    with Session(fresh_demo_engine, future=True) as s:
        with pytest.raises(StoreKindMismatch):
            write.ensure_live_writer(s)


def test_a_workbook_store_takes_live_writes_and_becomes_live(session: Session) -> None:
    ensure_identity(session, "workbook")
    session.commit()
    write.ensure_live_writer(session)
    assert read_identity(session).kind == "workbook"
    write.mark_live_wrote(session)
    assert read_identity(session).kind == "live"


def test_a_clubs_players_are_stored_once_with_the_services_positions(session: Session) -> None:
    live(session)
    squad = list(parse.parse_people(jload("people_ZZA.json"), expect_club="ZZA").items)
    result = write.upsert_people(session, SEASON, "ZZA", squad, now=NOW)
    session.commit()
    assert (result.persons_created, result.registrations_created) == (14, 14)
    assert count(session, ElPerson) == 14 and count(session, ElRegistration) == 14
    registration = session.get(ElRegistration, (SEASON, "ZZA", squad[0].person.code))
    assert registration is not None
    assert (registration.position_code, registration.position_name) == (
        squad[0].position_code,
        squad[0].position_name,
    )
    assert registration.source == "euroleague-v2" and registration.start_date == date(2031, 8, 15)
    again = write.upsert_people(session, SEASON, "ZZA", squad, now=NOW)
    assert again.rows == 0


def test_a_registration_for_another_club_is_not_stored_under_this_one(session: Session) -> None:
    live(session)
    squad = list(parse.parse_people(jload("people_ZZA.json")).items)
    result = write.upsert_people(session, SEASON, "ZZB", squad, now=NOW)
    assert len(result.rejected) == 14 and result.rows == 0


# ====================================================================== raw payloads and state


def fetched(url_suffix: str, body: bytes, *, status: int = 200, minutes: int = 0) -> Fetched:
    url = f"{BASE}/{url_suffix}"
    return Fetched(
        match=endpoints.match_endpoint(url),
        url=url,
        status=status,
        body=body,
        headers={},
        fetched_at=datetime(2031, 10, 4, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes),
    )


def test_only_the_last_three_raw_payloads_per_url_are_kept_with_their_files(
    session: Session, tmp_path: Path
) -> None:
    store = RawPayloadStore(tmp_path / "el-raw")
    saved = []
    for index in range(5):
        item = fetched("rounds", f'{{"n": {index}}}'.encode(), minutes=index)
        ref = store.save(item)
        write.record_raw_payload(session, item, ref, store)
        saved.append(ref)
    session.commit()
    assert count(session, ElRawPayload) == 3
    assert [p.is_file() for p in (r.path for r in saved)] == [False, False, True, True, True]
    assert {r.sha256 for r in rows(session, ElRawPayload)} == {r.sha256 for r in saved[2:]}
    other = fetched("clubs", b"{}")
    write.record_raw_payload(session, other, store.save(other), store)
    assert count(session, ElRawPayload) == 4  # retention is per URL


def test_an_identical_body_refreshes_the_newest_row_instead_of_adding_one(
    session: Session, tmp_path: Path
) -> None:
    store = RawPayloadStore(tmp_path)
    first = fetched("rounds", b"{}")
    write.record_raw_payload(session, first, store.save(first), store)
    second = fetched("rounds", b"{}", minutes=30)
    row = write.record_raw_payload(session, second, store.save(second), store)
    assert count(session, ElRawPayload) == 1 and row is not None
    assert row.fetched_at == datetime(2031, 10, 4, 9, 30)


def test_a_file_shared_by_a_surviving_row_is_not_deleted_with_a_pruned_one(
    session: Session, tmp_path: Path
) -> None:
    store = RawPayloadStore(tmp_path)
    bodies = [b'{"v": "a"}', b'{"v": "b"}', b'{"v": "a"}', b'{"v": "c"}', b'{"v": "d"}']
    refs = []
    for index, body in enumerate(bodies):
        item = fetched("rounds", body, minutes=index)
        ref = store.save(item)
        write.record_raw_payload(session, item, ref, store)
        refs.append(ref)
    session.commit()
    assert refs[0].path == refs[2].path
    surviving = {r.path for r in rows(session, ElRawPayload)}
    assert all(Path(p).is_file() for p in surviving)  # the shared "a" file is held by the 3rd row
    assert not refs[1].path.exists()  # "b" belonged to a pruned row only


def test_a_response_that_was_not_stored_is_not_recorded(session: Session) -> None:
    item = fetched("games/9/stats", b"nope", status=404)
    assert write.record_raw_payload(session, item, None) is None
    assert count(session, ElRawPayload) == 0


def test_a_cursor_round_trips_and_never_touches_the_workers_columns(session: Session) -> None:
    started = datetime(2031, 10, 4, 8, 0)
    session.add(ElJobState(job_key="el.round", last_started_at=started, last_error="in progress"))
    session.commit()
    write.write_cursor(
        session, "el.round", {"daily_at": "2031-10-04T06:00:00", "polls": {"g": {"n": 2}}}
    )
    session.commit()
    row = session.get(ElJobState, "el.round")
    assert row is not None and row.last_started_at == started and row.last_error == "in progress"
    assert write.read_cursor(session, "el.round") == {
        "daily_at": "2031-10-04T06:00:00",
        "polls": {"g": {"n": 2}},
    }
    write.write_cursor(session, "el.box", {"x": 1})  # a job the worker has not recorded yet
    assert write.read_cursor(session, "el.box") == {"x": 1}
    assert write.read_cursor(session, "el.nothing") == {}
    session.get(ElJobState, "el.box").cursor_json = "{not json"
    assert write.read_cursor(session, "el.box") == {}


def test_source_state_failures_count_up_keep_the_last_success_and_reset_on_ok(
    session: Session,
) -> None:
    live(session)
    t1, t2, t3 = NOW, NOW + timedelta(hours=1), NOW + timedelta(hours=2)
    write.mark_source_ok(session, "el.dataService", t1, detail={"a": 1})
    write.mark_source_failed(
        session, "el.dataService", t2, state="unreadable", error="data[].played: gone"
    )
    write.mark_source_failed(session, "el.dataService", t3, state="error", error="boom")
    row = write.get_source_state(session, "el.dataService")
    assert (row.state, row.consecutive_failures, row.last_success_at, row.last_error) == (
        "error",
        2,
        t1,
        "boom",
    )
    write.mark_source_ok(session, "el.dataService", t3)
    row = write.get_source_state(session, "el.dataService")
    assert (row.state, row.consecutive_failures, row.last_error) == ("ok", 0, None)
    assert json.loads(row.detail_json) == {"a": 1}  # detail is kept unless replaced


def test_a_block_is_mirrored_into_the_sync_state_unless_the_source_is_a_feed(
    session: Session,
) -> None:
    live(session)
    until = NOW + timedelta(hours=6)
    write.mark_source_failed(
        session, "el.dataService", NOW, state="blocked", error="forbidden (403)", paused_until=until
    )
    assert session.get(ElSyncState, 1).paused_until == until
    write.mark_source_ok(
        session, "el.news.1", NOW, touch_sync=False
    )  # a feed answering says nothing about the service
    assert session.get(ElSyncState, 1).paused_until == until
    write.mark_source_failed(session, "el.news.1", NOW, state="error", error="x", touch_sync=False)
    assert session.get(ElSyncState, 1).paused_until == until
    write.mark_source_ok(session, "el.dataService", NOW + timedelta(hours=7))
    assert session.get(ElSyncState, 1).paused_until is None


def test_log_run_truncates_and_serialises(session: Session) -> None:
    entry = write.log_run(
        session,
        job="box",
        started_at=NOW,
        status="quarantined",
        invariant_failed="x" * 5000,
        error="e" * 5000,
        detail={"gameId": "E2031-0001", "when": NOW},
    )
    assert len(entry.invariant_failed) == 2000 and len(entry.error) == 2000
    assert json.loads(entry.detail_json)["gameId"] == "E2031-0001"


# ======================================================================================= jobs


def request_count(service: Service) -> int:
    return len(service.calls)


def test_nothing_due_is_skipped_before_any_request(run: Any, service: Service) -> None:
    assert run(jobs.run_structure, T0)["status"] == "ok"
    assert run(jobs.run_round, T0)["status"] == "ok"
    made = request_count(service)
    quiet = service.client()
    for job in (jobs.run_structure, jobs.run_round, jobs.run_rosters):
        result = (
            run(job, T0 + timedelta(minutes=5), client=quiet)
            if job is not jobs.run_rosters
            else None
        )
        if result is not None:
            assert result["status"] == "skipped", job
    assert quiet.requests_made == 0 and request_count(service) == made


def test_a_job_that_has_nothing_to_do_never_builds_a_client(
    service: Service, el_engine: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a client was built although nothing was due")

    monkeypatch.setattr(jobs, "EuroLeagueClient", forbidden)
    result = jobs.run_box(now=T0, engine=el_engine, data_dir=tmp_path, zone=UTC)
    assert result["status"] == "skipped" and result["requests"] == 0


@pytest.mark.parametrize(
    ("variable", "value", "fragment"),
    [
        ("HARDWOOD_EL_ENABLED", "0", "switched off"),
        ("HARDWOOD_EL_LIVE", "0", "live ingest is switched off"),
    ],
)
def test_the_switches_are_locks_and_force_does_not_open_them(
    run: Any,
    service: Service,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
    fragment: str,
) -> None:
    monkeypatch.setenv(variable, value)
    for job in (jobs.run_structure, jobs.run_rosters, jobs.run_round, jobs.run_box):
        result = run(job, T0, force=True)
        assert result["status"] == "skipped" and fragment in result["detail"].lower(), job
    assert service.calls == []


def test_the_headline_job_is_not_behind_the_live_switch(
    run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARDWOOD_EL_LIVE", "0")
    result = run(jobs.run_news, T0)
    assert result["status"] == "skipped" and "no headline feed is enabled" in result["detail"]
    monkeypatch.setenv("HARDWOOD_EL_ENABLED", "0")
    assert "switched off" in run(jobs.run_news, T0)["detail"]


def test_a_malformed_switch_is_an_error_not_a_guess(
    run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARDWOOD_EL_LIVE", "perhaps")
    result = run(jobs.run_round, T0)
    assert result["status"] == "error" and "not a boolean" in result["error"]


def test_a_missing_store_is_a_skip_with_the_reason(
    service: Service, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARDWOOD_EL_DATABASE_URL", f"sqlite:///{tmp_path / 'absent.db'}")
    monkeypatch.setenv("HARDWOOD_DATA_DIR", str(tmp_path))
    result = jobs.run_round(now=T0, client=service.client(), zone=UTC)
    assert result["status"] == "skipped" and "does not exist yet" in result["detail"]
    assert not (tmp_path / "absent.db").exists()  # a job never creates the store
    assert service.calls == []


def test_the_same_store_as_the_nba_is_refused(
    service: Service, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "one.db"
    shared.write_bytes(b"")
    monkeypatch.setenv("HARDWOOD_EL_DATABASE_URL", f"sqlite:///{shared}")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{shared}")
    result = jobs.run_round(now=T0, client=service.client(), zone=UTC)
    assert result["status"] == "error" and "same database" in result["error"]


@pytest.mark.parametrize("job", ["run_structure", "run_rosters", "run_round", "run_box"])
def test_a_synthetic_store_is_refused_before_a_single_request(
    job: str, service: Service, fresh_demo_engine: Any, tmp_path: Path
) -> None:
    result = getattr(jobs, job)(
        now=T0,
        force=True,
        engine=fresh_demo_engine,
        data_dir=tmp_path,
        zone=UTC,
        client=service.client(),
    )
    assert result["status"] == "error" and "invented" in result["error"]
    assert service.calls == []


# ------------------------------------------------------------------------------ structure


def monday_after(moment: datetime, hour: int = 0, minute: int = 30) -> datetime:
    days = (7 - moment.weekday()) % 7 or 7
    return (moment + timedelta(days=days)).replace(hour=hour, minute=minute)


def test_structure_fetches_the_calendar_and_the_clubs_then_waits_for_monday(
    run: Any, service: Service, session: Session
) -> None:
    first = run(jobs.run_structure, T0)
    assert first["status"] == "ok" and first["requests"] == 2
    assert service.suffixes() == ["rounds", "clubs"]
    cursor = write.read_cursor(session, "el.structure")
    assert [r["round"] for r in cursor["rounds"]] == [1, 2, 3, 4] and cursor["season"] == SEASON
    assert {c.club_code for c in rows(session, ElClub)} == {"ZZA", "ZZB", "ZZC", "ZZD"}
    source = session.get(ElSourceState, "el.dataService")
    assert source is not None and source.state == "ok"
    assert json.loads(source.detail_json)["el.structure"]["rounds"] == 4
    assert run(jobs.run_structure, T0 + timedelta(days=2))["status"] == "skipped"
    again = run(jobs.run_structure, monday_after(T0))
    assert again["status"] == "ok" and again["requests"] == 2


def test_structure_the_day_before_a_round_refreshes_only_the_clubs(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    live(session)
    # round 3: games on Thursday the 16th and Friday the 17th
    write.upsert_games(session, SEASON, games_of("games_round_3.json"), now=NOW)
    session.commit()
    service.calls.clear()
    cursor = write.read_cursor(session, "el.structure")
    cursor["e1_at"] = cursor["e4_at"] = "2031-10-14T10:00:00"  # after Monday's weekly refresh
    write.write_cursor(session, "el.structure", cursor)
    session.commit()
    eve = datetime(2031, 10, 15, 8, 0, tzinfo=UTC)  # the Wednesday before the round begins
    result = run(jobs.run_structure, eve - timedelta(hours=1))
    assert result["status"] == "ok" and service.suffixes() == ["clubs"]
    assert run(jobs.run_structure, eve)["status"] == "skipped"  # refreshed within the last 20 hours


def test_a_day_inside_a_round_is_not_the_day_before_one(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    live(session)
    write.upsert_games(session, SEASON, games_of("games_round_3.json"), now=NOW)
    session.commit()
    cursor = write.read_cursor(session, "el.structure")
    cursor["e1_at"] = cursor["e4_at"] = "2031-10-14T10:00:00"
    write.write_cursor(session, "el.structure", cursor)
    session.commit()
    service.calls.clear()
    # Thursday morning: the round has begun (its first game is today), and the Friday game is
    # tomorrow. Twenty-two hours have passed since the clubs were last read, which would have been
    # enough to read them again if "a game tomorrow" were all it took.
    thursday = datetime(2031, 10, 16, 8, 0, tzinfo=UTC)
    assert run(jobs.run_structure, thursday)["status"] == "skipped"
    assert service.calls == []


def test_the_day_before_a_round_is_decided_by_whether_the_round_has_begun(
    session: Session,
) -> None:
    store_games(
        session,
        [
            (1, 1, "final", date(2031, 10, 2)),
            (2, 1, "final", date(2031, 10, 3)),
            (3, 2, "scheduled", date(2031, 10, 9)),
            (4, 2, "scheduled", date(2031, 10, 10)),
            (5, 3, "postponed", date(2031, 10, 16)),  # moved to the 17th below
            (6, 3, "scheduled", date(2031, 10, 18)),
            (7, 1, "scheduled", date(2031, 10, 13)),  # a round-1 game re-dated to the 13th
        ],
    )

    def ahead(year: int, month: int, day: int) -> bool:
        return jobs._round_ahead(session, SEASON, datetime(year, month, day, 9, 0))

    assert ahead(2031, 10, 8) is True  # round 2 starts tomorrow
    assert ahead(2031, 10, 9) is False  # its first game is today: it is under way
    assert ahead(2031, 10, 7) is False  # two days before is not the day before
    assert ahead(2031, 10, 12) is False  # tomorrow's game belongs to round 1, which has begun
    # round 3's first game was postponed, so only its Saturday game counts: Friday is the eve
    assert ahead(2031, 10, 17) is True
    assert ahead(2031, 10, 18) is False


def test_structure_falls_back_to_the_previous_season_when_the_new_calendar_is_not_out(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    service.calls.clear()
    july = datetime(
        2032, 7, 6, 8, 0, tzinfo=UTC
    )  # the calendar says E2032; the service has no such season yet
    result = run(jobs.run_structure, july)
    assert result["status"] == "ok"
    assert service.calls[0].endswith("E2032/rounds") and service.calls[1].endswith("E2031/rounds")
    assert (
        session.get(ElSeason, "E2032") is None
        and write.read_cursor(session, "el.structure")["season"] == SEASON
    )


def test_a_calendar_of_the_wrong_shape_makes_the_source_unreadable_and_says_why(
    run: Any, service: Service, session: Session
) -> None:
    service.bodies[f"{BASE}/rounds"] = b'{"items": [], "count": 0}'
    result = run(jobs.run_structure, T0)
    assert result["status"] == "error" and "unreadable" in result["error"]
    source = session.get(ElSourceState, "el.dataService")
    assert source is not None and source.state == "unreadable"
    assert "items" in (source.last_error or "") and "count" in (source.last_error or "")
    assert source.consecutive_failures == 1


def test_club_mismatches_are_reported_in_the_source_detail(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    payload = jload("clubs.json")
    payload["data"][0][
        "code"
    ] = "ZZX"  # the service renumbered a club: same tv code and name as ZZA
    service.put("clubs", payload)
    result = run(jobs.run_structure, monday_after(T0))
    assert result["status"] == "ok"
    detail = json.loads(session.execute(select(ElSourceState.detail_json)).scalar_one())
    assert [m["code"] for m in detail["el.structure"]["clubMismatches"]] == ["ZZX"]
    assert session.get(ElClub, "ZZX") is None


def test_with_no_calendar_published_the_service_is_asked_daily_not_hourly(
    run: Any, service: Service
) -> None:
    del service.bodies[f"{BASE}/rounds"]  # the season's calendar is not out yet
    first = run(jobs.run_structure, T0)
    assert first["status"] == "ok" and "no rounds for E2031 (404)" in first["detail"]
    assert service.suffixes() == ["rounds", "clubs"]
    service.calls.clear()
    for hours in (1, 5, 19):
        assert run(jobs.run_structure, T0 + timedelta(hours=hours))["status"] == "skipped"
    assert service.calls == []  # an hourly worker tick does not become an hourly request
    again = run(jobs.run_structure, T0 + timedelta(hours=21))
    assert again["status"] == "ok" and service.suffixes() == ["rounds"]


# ---------------------------------------------------------------------------------- round


def test_round_waits_for_the_calendar_then_sweeps_the_current_and_the_next_round(
    run: Any, service: Service, session: Session
) -> None:
    first = run(jobs.run_round, T0)
    assert first["status"] == "skipped" and "waiting for el.structure" in first["detail"]
    assert (
        service.calls == [] and write.read_cursor(session, "el.round") == {}
    )  # the sweep stays due
    run(jobs.run_structure, T0)
    service.calls.clear()
    swept = run(jobs.run_round, T0 + timedelta(minutes=1))
    assert swept["status"] == "ok" and service.suffixes() == [
        "games?roundNumber=1",
        "games?roundNumber=2",
    ]
    assert {g.game_id for g in rows(session, ElGame)} == {f"E2031-000{n}" for n in (1, 2, 3, 4)}


def test_the_daily_sweep_happens_once_per_six_oclock_slot(run: Any, service: Service) -> None:
    run(jobs.run_structure, T0)
    run(jobs.run_round, datetime(2031, 10, 4, 9, 0, tzinfo=UTC))
    service.calls.clear()
    for moment, due in (
        (datetime(2031, 10, 4, 10, 0, tzinfo=UTC), False),
        (datetime(2031, 10, 5, 5, 59, tzinfo=UTC), False),
        (datetime(2031, 10, 5, 6, 0, tzinfo=UTC), True),
        (datetime(2031, 10, 5, 7, 0, tzinfo=UTC), False),
    ):
        result = run(jobs.run_round, moment)
        assert (result["status"] == "ok") is due, moment


def test_a_mac_that_slept_for_days_sweeps_once_and_does_not_replay_the_missed_days(
    run: Any, service: Service
) -> None:
    run(jobs.run_structure, T0)
    run(jobs.run_round, datetime(2031, 10, 4, 9, 0, tzinfo=UTC))
    service.calls.clear()
    assert run(jobs.run_round, datetime(2031, 10, 9, 13, 0, tzinfo=UTC))["status"] == "ok"
    assert run(jobs.run_round, datetime(2031, 10, 9, 13, 10, tzinfo=UTC))["status"] == "skipped"


def finish_game_four(service: Service) -> None:
    payload = jload("games_round_2.json")
    game = payload["data"][1]
    game.update(played=True)
    game["local"].update(
        score=80,
        standingsScore=80,
        partials={
            "partials1": 20,
            "partials2": 20,
            "partials3": 20,
            "partials4": 20,
            "extraPeriods": {},
        },
    )
    game["road"].update(
        score=75,
        standingsScore=75,
        partials={
            "partials1": 19,
            "partials2": 18,
            "partials3": 19,
            "partials4": 19,
            "extraPeriods": {},
        },
    )
    service.put("games?roundNumber=2", payload)


def test_a_result_is_polled_from_tipoff_plus_105_minutes_at_most_twelve_times(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    sweep = datetime(2031, 10, 10, 6, 5, tzinfo=UTC)  # game 4 tips off at 18:00 UTC the same day
    run(jobs.run_round, sweep)
    tip = datetime(2031, 10, 10, 18, 0, tzinfo=UTC)
    service.calls.clear()
    assert run(jobs.run_round, tip + timedelta(minutes=104))["status"] == "skipped"
    assert service.calls == []
    polls = 0
    moment = tip + timedelta(minutes=106)
    for _ in range(14):
        result = run(jobs.run_round, moment)
        polls += result["status"] == "ok"
        moment += timedelta(minutes=10)
    assert polls == 12 == jobs.MAX_POLLS_PER_GAME  # no thirteenth
    assert service.suffixes() == ["games?roundNumber=2"] * 12
    cursor = write.read_cursor(session, "el.round")
    assert cursor["polls"]["E2031-0004"]["n"] == 12


def test_a_result_that_arrives_ends_the_polling_and_is_stored(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    run(jobs.run_round, datetime(2031, 10, 10, 6, 5, tzinfo=UTC))
    tip = datetime(2031, 10, 10, 18, 0, tzinfo=UTC)
    run(jobs.run_round, tip + timedelta(minutes=106))
    assert write.read_cursor(session, "el.round")["polls"]["E2031-0004"]["n"] == 1
    finish_game_four(service)
    result = run(jobs.run_round, tip + timedelta(minutes=116))
    assert result["status"] == "ok" and "1 new result" in result["detail"]
    game = stored_game(session, "E2031-0004")
    assert (game.status, game.home_pts, game.away_pts) == ("final", 80, 75)
    assert "E2031-0004" not in write.read_cursor(session, "el.round")["polls"]
    service.calls.clear()
    assert run(jobs.run_round, tip + timedelta(minutes=126))["status"] == "skipped"


def test_a_conditional_request_that_answers_304_is_reported_as_unchanged(
    run: Any, service: Service
) -> None:
    run(jobs.run_structure, T0)
    service.bodies[f"{BASE}/games?roundNumber=1"] = raw("games_round_1.json")
    first = run(jobs.run_round, T0 + timedelta(minutes=1))
    assert first["status"] == "ok"
    service.fixed[f"{BASE}/games?roundNumber=1"] = httpx.Response(304)
    service.fixed[f"{BASE}/games?roundNumber=2"] = httpx.Response(304)
    again = run(jobs.run_round, T0 + timedelta(minutes=2), force=True)
    assert again["status"] == "ok" and "unchanged" in again["detail"]


def test_a_schedule_with_no_played_key_is_unreadable_and_names_the_path(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    payload = jload("games_round_1.json")
    for game in payload["data"]:
        del game["played"]
    service.put("games?roundNumber=1", payload)
    result = run(jobs.run_round, T0 + timedelta(minutes=1))
    assert result["status"] == "error"
    source = session.get(ElSourceState, "el.dataService")
    assert (
        source is not None and source.state == "unreadable" and "data[].played" in source.last_error
    )
    assert count(session, ElGame) == 0  # nothing from the unreadable payload was kept


def test_played_games_with_no_usable_score_make_the_schedule_unreadable(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    payload = jload("games_round_1.json")
    for game in payload["data"]:
        game["local"].pop("score")
        game["road"].pop("score")
    service.put("games?roundNumber=1", payload)
    result = run(jobs.run_round, T0 + timedelta(minutes=1))
    assert result["status"] == "error" and "local.score" in result["error"]
    assert session.get(ElSourceState, "el.dataService").state == "unreadable"


# ------------------------------------- postponed games, sweep progress, the unreadable wait


def plan_env(now: datetime) -> Any:
    """What the sweep-planning helpers read from a ``JobEnv``: the clock, aware and naive."""
    return SimpleNamespace(now=now, naive_now=now.replace(tzinfo=None))


def store_games(session: Session, games: list[tuple[int, int, str, date]]) -> None:
    """Store games as ``(code, round, status, date)``; a final one gets a score and a loser."""
    session.add(
        ElSeason(
            season_code=SEASON,
            competition_code="E",
            label="2031-32",
            start_year=2031,
            is_current=True,
        )
    )
    for code in ("ZZA", "ZZB"):
        session.add(ElClub(club_code=code, name=f"Club {code}"))
    session.flush()
    for code, number, status, day in games:
        final = status == "final"
        session.add(
            ElGame(
                game_id=f"{SEASON}-{code:04d}",
                season_code=SEASON,
                game_code=code,
                round_number=number,
                phase_code="RS",
                game_date=day,
                home_club_code="ZZA",
                away_club_code="ZZB",
                status=status,
                home_pts=80 if final else None,
                away_pts=70 if final else None,
                stats_status="none",
                data_source="euroleague-v2",
                ingested_at=datetime(2031, 10, 1),
            )
        )
    session.commit()


CALENDAR = {"rounds": [{"round": n, "phase": "RS"} for n in (1, 2, 3, 4, 5)]}
LATER = datetime(2031, 10, 20, 8, 0, tzinfo=UTC)


def test_a_postponed_game_does_not_hold_the_sweep_on_its_old_round(session: Session) -> None:
    store_games(
        session,
        [
            (1, 1, "final", date(2031, 10, 2)),
            (2, 2, "postponed", date(2031, 10, 9)),
            (3, 2, "final", date(2031, 10, 9)),
            (4, 3, "final", date(2031, 10, 16)),
            (5, 4, "scheduled", date(2031, 10, 23)),
        ],
    )
    env = plan_env(LATER)
    # the league is in round 4: the sweep must read 4 and 5, not stay on 2 and 3
    assert jobs._current_and_next(session, CALENDAR, SEASON, env) == [4, 5]
    # and the round with the postponed game is read as well, so its new date is noticed
    assert jobs._daily_rounds(session, CALENDAR, SEASON, env) == [4, 5, 2]


def test_only_recent_postponed_rounds_are_read_and_at_most_two(session: Session) -> None:
    store_games(
        session,
        [
            (1, 1, "postponed", date(2031, 5, 1)),  # postponed for months: let go
            (2, 2, "postponed", date(2031, 10, 1)),
            (3, 3, "postponed", date(2031, 10, 8)),
            (4, 4, "postponed", date(2031, 10, 15)),
            (5, 5, "scheduled", date(2031, 10, 30)),
        ],
    )
    assert jobs._postponed_rounds(session, SEASON, LATER.replace(tzinfo=None)) == [3, 4]
    # a round that is the current one is not listed twice
    assert jobs._daily_rounds(session, CALENDAR, SEASON, plan_env(LATER)) == [5, 3, 4]


def test_a_postponed_games_new_date_is_noticed_and_later_rounds_are_still_swept(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    payload = jload("games_round_2.json")
    payload["data"][1].update(played=False, gameStatus="Postponed")  # game 4
    service.put("games?roundNumber=2", payload)
    run(jobs.run_round, datetime(2031, 10, 4, 9, 0, tzinfo=UTC))
    assert stored_game(session, "E2031-0004").status == "postponed"

    run(jobs.run_round, datetime(2031, 10, 5, 6, 0, tzinfo=UTC))  # round 3 arrives
    service.calls.clear()
    result = run(jobs.run_round, datetime(2031, 10, 6, 6, 0, tzinfo=UTC))
    assert result["status"] == "ok"
    # round 3 is the current one, 4 the next (the service has none yet: a note, not a failure),
    # and round 2 is read again because it holds the postponed game
    assert sorted(service.suffixes()) == [
        "games?roundNumber=2",
        "games?roundNumber=3",
        "games?roundNumber=4",
    ]

    moved = jload("games_round_2.json")
    moved["data"][1].update(played=False, utcDate="2031-10-20T18:00:00Z")
    service.put("games?roundNumber=2", moved)
    run(jobs.run_round, datetime(2031, 10, 7, 6, 0, tzinfo=UTC))
    game = stored_game(session, "E2031-0004")
    assert game.status == "scheduled" and game.tipoff_utc == datetime(2031, 10, 20, 18, 0)


def test_a_run_that_cannot_read_every_round_goes_on_at_the_next_tick(
    run: Any, service: Service, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(jobs.run_structure, T0)
    monkeypatch.setattr(jobs, "MAX_ROUNDS_PER_RUN", 1)
    service.calls.clear()
    first = run(jobs.run_round, datetime(2031, 10, 4, 9, 0, tzinfo=UTC))
    assert first["status"] == "ok" and service.suffixes() == ["games?roundNumber=1"]
    cursor = write.read_cursor(session, "el.round")
    assert cursor["daily_swept"]["rounds"] == [1] and "daily_at" not in cursor

    service.calls.clear()
    second = run(jobs.run_round, datetime(2031, 10, 4, 9, 10, tzinfo=UTC))
    assert second["status"] == "ok" and service.suffixes() == ["games?roundNumber=2"]
    cursor = write.read_cursor(session, "el.round")
    assert "daily_at" in cursor and "daily_swept" not in cursor  # the whole sweep is done

    service.calls.clear()
    third = run(jobs.run_round, datetime(2031, 10, 4, 9, 20, tzinfo=UTC))
    assert third["status"] == "skipped" and service.calls == []


def test_a_forced_run_starts_the_sweep_again_instead_of_continuing_it(
    run: Any, service: Service, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(jobs.run_structure, T0)
    monkeypatch.setattr(jobs, "MAX_ROUNDS_PER_RUN", 1)
    run(jobs.run_round, datetime(2031, 10, 4, 9, 0, tzinfo=UTC))  # reads round 1 of [1, 2]
    assert write.read_cursor(session, "el.round")["daily_swept"]["rounds"] == [1]
    service.calls.clear()
    # a plain tick would go on with round 2; a forced one begins the sweep again at round 1
    forced = run(jobs.run_round, datetime(2031, 10, 4, 9, 5, tzinfo=UTC), force=True)
    assert forced["status"] == "ok" and service.suffixes() == ["games?roundNumber=1"]


def test_results_are_polled_before_the_sweep_when_a_run_cannot_do_both(
    run: Any, service: Service, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(jobs.run_structure, T0)
    run(jobs.run_round, datetime(2031, 10, 4, 9, 0, tzinfo=UTC))  # game 4 (round 2) scheduled
    finish_game_four(service)
    monkeypatch.setattr(jobs, "MAX_ROUNDS_PER_RUN", 1)
    service.calls.clear()
    # on the 10th, 19:50 UTC: game 4 (18:00) is overdue and a new sweep slot has opened
    result = run(jobs.run_round, datetime(2031, 10, 10, 19, 50, tzinfo=UTC))
    assert result["status"] == "ok" and service.suffixes() == ["games?roundNumber=2"]
    assert stored_game(session, "E2031-0004").status == "final"


def test_the_wait_after_an_unreadable_answer_grows_to_six_hours() -> None:
    hours = [jobs.unreadable_delay(n) / timedelta(hours=1) for n in (0, 1, 2, 3, 4, 5, 9)]
    assert hours == [1, 1, 2, 4, 6, 6, 6]


def test_after_an_unreadable_answer_the_job_waits_and_force_asks_anyway(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    broken = jload("games_round_1.json")
    for game in broken["data"]:
        del game["played"]
    service.put("games?roundNumber=1", broken)

    first = run(jobs.run_round, T0 + timedelta(minutes=1))
    assert first["status"] == "error"
    # the sources panel shows last_error, so it says when the service will be asked again
    shown = session.get(ElSourceState, "el.dataService").last_error
    assert "data[].played" in shown and shown.endswith(
        "(not asked again until 2031-10-01 09:01 UTC)"
    )
    service.calls.clear()
    # the worker ticks every ten minutes: it must not ask again for an hour
    waiting = run(jobs.run_round, T0 + timedelta(minutes=11))
    assert waiting["status"] == "skipped" and "not asked again until" in waiting["detail"]
    assert "--force" in waiting["detail"] and service.calls == []
    assert session.get(ElSourceState, "el.dataService").state == "unreadable"

    # after the hour it asks once more, and the wait doubles when it is still unreadable
    again = run(jobs.run_round, T0 + timedelta(minutes=62))
    assert again["status"] == "error" and service.calls
    session.expire_all()
    shown = session.get(ElSourceState, "el.dataService").last_error
    assert shown.count("not asked again until") == 1  # replaced, not stacked
    assert shown.endswith("(not asked again until 2031-10-01 11:02 UTC)")
    cursor = write.read_cursor(session, "el.round")
    assert cursor["unreadableCount"] == 2
    assert cursor["unreadableUntil"] == (T0 + timedelta(minutes=62, hours=2)).replace(
        tzinfo=None
    ).isoformat(timespec="seconds")

    # force asks at once, and a readable answer clears the wait
    service.calls.clear()
    forced = run(jobs.run_round, T0 + timedelta(minutes=64), force=True)
    assert forced["status"] == "error" and service.calls
    service.put("games?roundNumber=1", jload("games_round_1.json"))
    fixed = run(jobs.run_round, T0 + timedelta(minutes=66), force=True)
    assert fixed["status"] == "ok"
    cursor = write.read_cursor(session, "el.round")
    assert "unreadableCount" not in cursor and "unreadableUntil" not in cursor
    assert session.get(ElSourceState, "el.dataService").state == "ok"


def test_the_wait_belongs_to_one_job_and_a_server_error_does_not_start_it(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    service.status("games?roundNumber=1", 503)
    failed = run(jobs.run_round, T0 + timedelta(minutes=1))
    assert failed["status"] == "error"
    service.calls.clear()
    retried = run(jobs.run_round, T0 + timedelta(minutes=11))  # a 5xx is asked about again
    assert retried["status"] == "error" and service.calls
    assert "unreadableCount" not in write.read_cursor(session, "el.round")

    service.fixed.clear()
    broken = jload("games_round_1.json")
    for game in broken["data"]:
        del game["played"]
    service.put("games?roundNumber=1", broken)
    run(jobs.run_round, T0 + timedelta(minutes=21))
    # the round job is waiting, but another job's cursor is its own
    other = run(jobs.run_box, T0 + timedelta(minutes=31))
    assert "not asked again" not in other.get("detail", "")


def test_a_round_the_service_does_not_have_yet_is_a_note_not_a_failure(
    run: Any, service: Service
) -> None:
    run(jobs.run_structure, T0)
    del service.bodies[f"{BASE}/games?roundNumber=2"]
    result = run(jobs.run_round, T0 + timedelta(minutes=1))
    assert result["status"] == "ok" and "round 1: 2 new" in result["detail"]
    assert "round 2: the service has no games (404)" in result["detail"]


# ---------------------------------------------------------------------------- the breaker


def test_a_403_opens_a_six_hour_breaker_that_later_runs_respect(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    service.fixed[f"{BASE}/games?roundNumber=1"] = httpx.Response(403)
    now = T0 + timedelta(minutes=1)
    blocked = run(jobs.run_round, now)
    assert (
        blocked["status"] == "error"
        and "blocked" in blocked["error"]
        and "forbidden (403)" in blocked["error"]
    )
    source = session.get(ElSourceState, "el.dataService")
    assert source.state == "blocked"
    session.expire_all()
    until = session.get(ElSourceState, "el.dataService").paused_until
    assert until == (now + timedelta(hours=6)).replace(tzinfo=None)
    assert session.get(ElSyncState, 1).paused_until == until
    made = request_count(service)
    for job in (jobs.run_round, jobs.run_box, jobs.run_structure, jobs.run_rosters):
        waiting = run(job, now + timedelta(hours=5), force=True)
        assert waiting["status"] == "skipped" and "paused until" in waiting["detail"], job
    assert request_count(service) == made  # not one request left the machine
    service.fixed.pop(f"{BASE}/games?roundNumber=1")
    after = run(jobs.run_round, now + timedelta(hours=6, minutes=1))
    assert after["status"] == "ok"
    session.expire_all()
    assert session.get(ElSourceState, "el.dataService").state == "ok"
    assert session.get(ElSourceState, "el.dataService").paused_until is None
    assert session.get(ElSyncState, 1).paused_until is None


def test_a_restarted_client_starts_with_the_persisted_pause(el_engine: Any, tmp_path: Path) -> None:
    with Session(el_engine, future=True) as s:
        live(s)
        write.mark_source_failed(
            s,
            "el.dataService",
            NOW,
            state="blocked",
            error="forbidden (403)",
            paused_until=NOW + timedelta(hours=3),
        )
        s.commit()
    env = jobs.JobEnv(
        key="el.round",
        now=T0.replace(year=2031, month=10, day=4, hour=9),
        settings=None,  # type: ignore[arg-type]
        engine=el_engine,
        shutdown=lambda: False,
        zone=UTC,
        force=False,
        data_dir=tmp_path,
        client_factory=lambda: None,  # type: ignore[arg-type, return-value]
    )
    client = jobs._new_client(env, None, 40)
    try:
        assert client.breaker().paused_until is not None
        assert client.breaker().reason == "forbidden (403)"
    finally:
        client.close()


def test_three_429s_block_and_a_503_is_only_an_error(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    service.then("games?roundNumber=1", *[httpx.Response(503)] * 3)
    result = run(jobs.run_round, T0 + timedelta(minutes=1))
    assert result["status"] == "error" and "503" in result["error"]
    session.expire_all()
    source = session.get(ElSourceState, "el.dataService")
    assert source.state == "error" and source.paused_until is None


# ------------------------------------------------------------------------------------ box


def prepare_finals(run: Any, service: Service) -> None:
    run(jobs.run_structure, T0)
    run(jobs.run_round, T0 + timedelta(minutes=1))
    run(
        jobs.run_round, datetime(2031, 10, 4, 9, 0, tzinfo=UTC)
    )  # round 3 swept: game 3 is final (round 2)


def test_box_scores_are_fetched_written_and_then_corrected_at_24_and_48_hours(
    run: Any, service: Service, session: Session
) -> None:
    prepare_finals(run, service)
    t = datetime(2031, 10, 4, 10, 0, tzinfo=UTC)
    service.calls.clear()
    first = run(jobs.run_box, t)
    assert (
        first["status"] == "ok"
        and "2 written" in first["detail"]
        and "1 notPublished" in first["detail"]
    )
    assert sorted(service.suffixes()) == ["games/1/stats", "games/2/stats", "games/3/stats"]
    assert {g.game_id: g.stats_status for g in rows(session, ElGame) if g.status == "final"} == {
        "E2031-0001": "ok",
        "E2031-0002": "none",
        "E2031-0003": "ok",
    }
    version = sync_version(session)
    service.calls.clear()
    assert run(jobs.run_box, t + timedelta(minutes=10))["status"] == "ok"
    assert service.suffixes() == ["games/2/stats"]  # only the one still waiting is retried
    service.calls.clear()
    day = run(jobs.run_box, t + timedelta(hours=24, minutes=1))
    assert sorted(service.suffixes()) == ["games/1/stats", "games/2/stats", "games/3/stats"]
    assert (
        "2 unchanged" in day["detail"] and sync_version(session) == version
    )  # a correction pass that finds none
    service.calls.clear()
    run(jobs.run_box, t + timedelta(hours=25))
    assert "games/1/stats" not in service.suffixes()  # the next correction is not until 48 hours
    service.calls.clear()
    run(jobs.run_box, t + timedelta(hours=48, minutes=2))
    assert "games/1/stats" in service.suffixes() and "games/3/stats" in service.suffixes()
    service.calls.clear()
    run(jobs.run_box, t + timedelta(hours=73))
    assert (
        "games/1/stats" not in service.suffixes()
    )  # first fetch, plus two corrections, and that is all


def test_a_box_score_that_is_not_published_backs_off_and_eventually_stops(
    run: Any, service: Service
) -> None:
    prepare_finals(run, service)
    service.bodies.pop(f"{BASE}/games/1/stats")
    service.bodies.pop(f"{BASE}/games/3/stats")
    t = datetime(2031, 10, 4, 10, 0, tzinfo=UTC)
    service.calls.clear()
    for index in range(jobs.BOX_ATTEMPT_LIMIT + 3):
        run(jobs.run_box, t + timedelta(minutes=10 * index))
    assert (
        len(service.calls) == 3 * jobs.BOX_ATTEMPT_LIMIT
    )  # three games, twelve tries each, then quiet
    service.calls.clear()
    slow = t + timedelta(minutes=10 * jobs.BOX_ATTEMPT_LIMIT) + jobs.BOX_SLOW_RETRY
    run(jobs.run_box, slow)
    assert len(service.calls) == 3  # the slow retry, six hours later
    service.calls.clear()
    run(jobs.run_box, slow + timedelta(hours=1))
    assert service.calls == []
    moment = slow
    for _ in range(jobs.BOX_SLOW_ATTEMPTS + 2):
        moment += jobs.BOX_SLOW_RETRY
        run(jobs.run_box, moment)
    service.calls.clear()
    run(jobs.run_box, moment + jobs.BOX_SLOW_RETRY)
    assert service.calls == []  # given up until a person asks


def test_a_run_makes_at_most_twelve_box_requests(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    live(session)
    (
        session.add(
            ElSeason(
                season_code=SEASON,
                competition_code="E",
                label="2031-32",
                start_year=2031,
                is_current=True,
            )
        )
        if session.get(ElSeason, SEASON) is None
        else None
    )
    for code in ("ZZA", "ZZB"):
        if session.get(ElClub, code) is None:
            session.add(ElClub(club_code=code, name=code))
    for number in range(1, 16):
        session.add(
            ElGame(
                game_id=f"E2031-{number + 100:04d}",
                season_code=SEASON,
                game_code=number + 100,
                round_number=1,
                phase_code="RS",
                game_date=date(2031, 10, 2),
                home_club_code="ZZA",
                away_club_code="ZZB",
                status="final",
                home_pts=80,
                away_pts=70,
                stats_status="none",
                data_source="euroleague-v2",
                ingested_at=NOW,
            )
        )
    session.commit()
    service.calls.clear()
    result = run(jobs.run_box, datetime(2031, 10, 4, 10, 0, tzinfo=UTC))
    assert result["requests"] == jobs.MAX_BOX_PER_RUN == len(service.calls)


def test_a_failure_part_way_through_a_run_keeps_the_bookkeeping_of_what_already_happened(
    run: Any, service: Service, session: Session
) -> None:
    prepare_finals(run, service)
    service.then("games/3/stats", *[httpx.Response(503)] * 3)  # the third game's fetch fails
    now = datetime(2031, 10, 4, 10, 0, tzinfo=UTC)
    result = run(jobs.run_box, now)
    assert result["status"] == "error" and "503" in result["error"]
    games = write.read_cursor(session, "el.box")["games"]
    assert len(games["E2031-0001"]["fetched"]) == 1  # written before the failure: remembered
    assert games["E2031-0002"]["attempts"] == 1  # a 404 before the failure: remembered
    assert games["E2031-0003"]["last_attempt"]  # the attempt that failed: remembered too
    assert stored_game(session, "E2031-0001").stats_status == "ok"  # and the write was kept


def test_a_quarantined_game_is_logged_with_its_rule_and_retried_the_next_day(
    run: Any, service: Service, session: Session
) -> None:
    prepare_finals(run, service)
    payload = jload("box_game_1.json")
    payload["local"]["players"][0]["stats"]["timePlayed"] = 100.0
    service.put("games/1/stats", payload)
    t = datetime(2031, 10, 4, 10, 0, tzinfo=UTC)
    result = run(jobs.run_box, t)
    assert result["status"] == "ok" and "1 quarantined" in result["detail"]
    assert stored_game(session, "E2031-0001").stats_status == "quarantined"
    log = [l for l in rows(session, ElIngestLog) if l.job == "box" and l.status == "quarantined"]
    assert len(log) == 1 and log[0].invariant_failed == "E2031-0001: team_time"
    assert any("team_time" in v for v in json.loads(log[0].detail_json)["violations"])
    assert (
        run(jobs.run_box, t + timedelta(hours=1))["status"] == "ok"
    )  # game 2 is still being retried...
    service.calls.clear()
    service.put("games/1/stats", jload("box_game_1.json"))  # the service corrects its data
    run(jobs.run_box, t + timedelta(hours=25))
    assert "games/1/stats" in service.suffixes()
    assert stored_game(session, "E2031-0001").stats_status == "ok"


def test_every_box_unreadable_marks_the_source_unreadable_with_the_first_path(
    run: Any, service: Service, session: Session
) -> None:
    prepare_finals(run, service)
    for suffix in ("games/1/stats", "games/3/stats"):
        service.bodies[f"{BASE}/{suffix}"] = b'{"home": {}, "away": {}}'
    result = run(jobs.run_box, datetime(2031, 10, 4, 10, 0, tzinfo=UTC))
    assert result["status"] == "error" and "unreadable" in result["error"]
    source = session.get(ElSourceState, "el.dataService")
    assert source.state == "unreadable" and "required key 'local' is missing" in source.last_error
    assert "E2031-000" in source.last_error  # the game it was looking at
    assert count(session, ElPlayerGame) == 0


def test_a_box_score_job_whose_every_answer_is_unreadable_also_waits(
    run: Any, service: Service, session: Session
) -> None:
    prepare_finals(run, service)
    for suffix in ("games/1/stats", "games/3/stats"):
        service.bodies[f"{BASE}/{suffix}"] = b'{"home": {}, "away": {}}'
    first = run(jobs.run_box, datetime(2031, 10, 4, 10, 0, tzinfo=UTC))
    assert first["status"] == "error" and "unreadable" in first["error"]
    service.calls.clear()
    waiting = run(jobs.run_box, datetime(2031, 10, 4, 10, 10, tzinfo=UTC))
    assert waiting["status"] == "skipped" and "not asked again" in waiting["detail"]
    assert service.calls == []
    run(jobs.run_box, datetime(2031, 10, 4, 11, 1, tzinfo=UTC))  # an hour on: it asks once more
    assert service.calls


def test_a_workbook_game_is_upgraded_once_with_the_services_lines(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)  # creates the season and the clubs
    live(session)
    assert session.get(ElSeason, SEASON) is not None and session.get(ElClub, "ZZA") is not None
    session.add(
        ElGame(
            game_id="E2031-0001",
            season_code=SEASON,
            game_code=1,
            round_number=1,
            phase_code="RS",
            game_date=date(2031, 10, 2),
            home_club_code="ZZA",
            away_club_code="ZZB",
            status="final",
            home_pts=84,
            away_pts=79,
            home_partials_json="[21, 19, 20, 24]",
            away_partials_json="[18, 22, 17, 22]",
            stats_status="ok",
            data_source="workbook:abcd1234",
            ingested_at=NOW,
        )
    )
    session.add(
        ElPlayerGame(
            game_id="E2031-0001", person_code="wb-1", club_code="ZZA", participation="played", pts=9
        )
    )
    session.commit()
    first = run(jobs.run_box, datetime(2031, 10, 4, 10, 0, tzinfo=UTC))
    assert "1 written" in first["detail"]
    game = stored_game(session)
    assert (game.data_source, game.stats_status) == ("euroleague-v2", "ok")
    assert "wb-1" not in {
        l.person_code for l in rows(session, ElPlayerGame)
    }  # the workbook's lines are replaced
    service.calls.clear()
    assert run(jobs.run_box, datetime(2031, 10, 4, 10, 30, tzinfo=UTC))["status"] == "skipped"


# ---------------------------------------------------------------------------------- rosters


def test_a_roster_sweep_is_resumable_and_matches_workbook_people_as_each_club_arrives(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    squad = list(parse.parse_people(jload("people_ZZA.json"), expect_club="ZZA").items)
    live(session)
    session.add(
        ElPerson(person_code="wb-0000000a", code_system="workbook", name=squad[0].person.name)
    )
    session.add(
        ElRegistration(
            season_code=SEASON,
            club_code="ZZA",
            person_code="wb-0000000a",
            position5_workbook="PG",
            source="workbook",
        )
    )
    session.commit()
    service.calls.clear()
    first = run(jobs.run_rosters, T0 + timedelta(minutes=1), client=service.client(budget=2))
    assert first["status"] == "ok" and first["requests"] == 2
    assert service.suffixes() == ["clubs/ZZA/people", "clubs/ZZB/people"]
    cursor = write.read_cursor(session, "el.rosters")
    assert cursor["sweep"]["done"] == ["ZZA", "ZZB"] and "complete_at" not in cursor
    assert (
        session.get(ElPersonAlias, "wb-0000000a").official_code == squad[0].person.code
    )  # matched at once
    service.calls.clear()
    second = run(jobs.run_rosters, T0 + timedelta(minutes=2))
    assert service.suffixes() == [
        "clubs/ZZC/people",
        "clubs/ZZD/people",
    ]  # it resumed where it stopped
    cursor = write.read_cursor(session, "el.rosters")
    assert "sweep" not in cursor and cursor["complete_at"]
    assert "4/4 clubs" in second["detail"]
    assert run(jobs.run_rosters, T0 + timedelta(minutes=3))["status"] == "skipped"
    assert run(jobs.run_rosters, monday_after(T0))["status"] == "ok"  # weekly


def test_a_roster_of_the_wrong_shape_makes_the_source_unreadable(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    for code in ("ZZA", "ZZB", "ZZC", "ZZD"):
        service.bodies[f"{BASE}/clubs/{code}/people"] = b'{"members": []}'
    result = run(jobs.run_rosters, T0 + timedelta(minutes=1))
    assert result["status"] == "error" and "unreadable" in result["error"]
    assert session.get(ElSourceState, "el.dataService").state == "unreadable"


def test_validators_are_remembered_and_sent_back_and_a_304_is_handled_everywhere(
    run: Any, service: Service, session: Session
) -> None:
    for suffix in ("rounds", "clubs", "clubs/ZZA/people", "games/1/stats", "games?roundNumber=2"):
        service.etags[f"{BASE}/{suffix}"] = f'"v-{suffix}"'
    run(jobs.run_structure, T0)
    run(jobs.run_round, T0 + timedelta(minutes=1))
    run(jobs.run_rosters, T0 + timedelta(minutes=2))
    run(jobs.run_box, T0 + timedelta(minutes=3))
    version, games_before = sync_version(session), count(session, ElGame)
    assert any(
        h.get("if-none-match") is None for h in service.headers
    )  # the first fetches were plain
    service.calls.clear()
    service.headers.clear()
    # a week later everything is due again, and the service says nothing has changed
    week = monday_after(T0 + timedelta(days=7))
    statuses = [
        run(job, week, force=True)["status"]
        for job in (jobs.run_structure, jobs.run_round, jobs.run_rosters, jobs.run_box)
    ]
    assert statuses == ["ok", "ok", "ok", "ok"]
    sent = {
        c.replace(BASE + "/", ""): h.get("if-none-match")
        for c, h in zip(service.calls, service.headers)
    }
    assert sent["rounds"] == '"v-rounds"' and sent["clubs"] == '"v-clubs"'
    assert (
        sent["clubs/ZZA/people"] == '"v-clubs/ZZA/people"'
        and sent["games/1/stats"] == '"v-games/1/stats"'
    )
    assert sent["games?roundNumber=2"] == '"v-games?roundNumber=2"'
    # round 3 had never been fetched, so its two new fixtures are the only change: one bump
    assert count(session, ElGame) == games_before + 2 and sync_version(session) == version + 1
    session.expire_all()
    assert session.get(ElSourceState, "el.dataService").state == "ok"


def test_an_unreadable_roster_is_retried_once_more_and_then_left_until_the_next_sweep(
    run: Any, service: Service, session: Session
) -> None:
    run(jobs.run_structure, T0)
    service.bodies[f"{BASE}/clubs/ZZB/people"] = b'{"members": []}'  # the wrong shape, for one club
    service.calls.clear()
    first = run(jobs.run_rosters, T0 + timedelta(minutes=1))
    assert first["status"] == "ok" and len(service.calls) == 4
    cursor = write.read_cursor(session, "el.rosters")
    assert cursor["sweep"]["failed"] == {"ZZB": 1} and "ZZB" not in cursor["sweep"]["done"]
    service.calls.clear()
    second = run(jobs.run_rosters, T0 + timedelta(minutes=2))
    assert service.suffixes() == ["clubs/ZZB/people"]  # only the one that failed
    cursor = write.read_cursor(session, "el.rosters")
    assert "sweep" not in cursor and cursor["complete_at"]  # given up on: the sweep is complete
    assert "unreadable twice" in second["detail"]
    service.calls.clear()
    assert run(jobs.run_rosters, T0 + timedelta(minutes=3))["status"] == "skipped"
    assert service.calls == []  # not retried every hour for ever
    assert run(jobs.run_rosters, monday_after(T0))["status"] == "ok"  # the next sweep tries again


# ----------------------------------------------------------------------------------- news


ROBOTS_OK = b"User-agent: *\nAllow: /\n"
ROBOTS_NO = b"User-agent: *\nDisallow: /\n"


def rss(items: list[tuple[str, str, str, datetime]]) -> bytes:
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link><guid>{g}</guid>"
        f"<pubDate>{format_datetime(when)}</pubDate><description>NEVER STORED</description></item>"
        for t, link, g, when in items
    )
    head = '<?xml version="1.0"?><rss version="2.0"><channel><title>Invented Daily</title>'
    return f"{head}{body}</channel></rss>".encode()


def feed_polite(feed_body: bytes, robots: bytes = ROBOTS_OK) -> tuple[PoliteClient, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=robots)
        if request.headers.get("if-none-match") == '"f1"':
            return httpx.Response(304)
        return httpx.Response(
            200, content=feed_body, headers={"etag": '"f1"', "content-type": "application/rss+xml"}
        )

    polite = PoliteClient(
        transport=httpx.MockTransport(handler),
        clock=lambda: 0.0,
        sleep=lambda s: None,
        wall_clock=lambda: datetime(2031, 10, 4, 12, 0, tzinfo=UTC),
    )
    return polite, seen


@pytest.fixture()
def news_store(session: Session) -> Session:
    live(session)
    session.add(
        ElSeason(
            season_code=SEASON,
            competition_code="E",
            label="2031-32",
            start_year=2031,
            is_current=True,
        )
    )
    session.add(
        ElClub(club_code="ZZA", tv_code="ZHB", name="Zenith Harbour BC", short_name="Harbour")
    )
    session.add(
        ElClub(club_code="ZZB", tv_code="BMF", name="Brightmoor Falcons", short_name="Brightmoor")
    )
    session.add(ElPerson(person_code="900101", code_system="official", name="Tobias Thistlewood"))
    session.add(
        ElRegistration(
            season_code=SEASON, club_code="ZZA", person_code="900101", source="euroleague-v2"
        )
    )
    session.add(ElPerson(person_code="900201", code_system="official", name="Marek Ashgrove"))
    session.add(
        ElPerson(person_code="900202", code_system="official", name="Marek Ashgrove")
    )  # two men, one name
    session.add(
        ElRegistration(
            season_code=SEASON, club_code="ZZB", person_code="900201", source="euroleague-v2"
        )
    )
    session.add(
        ElRegistration(
            season_code=SEASON, club_code="ZZB", person_code="900202", source="euroleague-v2"
        )
    )
    session.add(
        ElIntelNewsFeed(name="Invented Daily", url="https://news.example.org/feed", enabled=True)
    )
    session.commit()
    return session


NEWS_NOW = datetime(2031, 10, 4, 12, 0, tzinfo=UTC)


def test_headlines_are_stored_with_subjects_and_nothing_but_four_facts(
    run: Any, news_store: Session
) -> None:
    when = NEWS_NOW - timedelta(hours=2)
    body = rss(
        [
            ("Zenith Harbour BC sign a guard", "https://news.example.org/a", "g1", when),
            ("Tobias Thistlewood returns to training", "https://news.example.org/b", "g2", when),
            ("Marek Ashgrove scores 30", "https://news.example.org/c", "g3", when),
            ("Odds and ends", "https://www.mozzartsport.com/x", "g4", when),
            ("A quiet day", "https://news.example.org/e", "g5", when),
        ]
    )
    polite, seen = feed_polite(body)
    result = run(jobs.run_news, NEWS_NOW, polite=polite)
    assert (
        result["status"] == "ok" and "4 new headline(s)" in result["detail"]
    )  # the denylisted link is dropped
    items = {i.guid: i for i in rows(news_store, ElIntelNewsItem)}
    assert set(items) == {"g1", "g2", "g3", "g5"}
    assert all(not hasattr(i, "description") for i in items.values())
    subjects = {
        (items_by_id.guid, s.club_code, s.person_code)
        for s in rows(news_store, ElIntelNewsSubject)
        for items_by_id in items.values()
        if items_by_id.item_id == s.item_id
    }
    assert ("g1", "ZZA", "") in subjects  # a club, by its name
    assert ("g2", "ZZA", "900101") in subjects  # a player, by his full name, and his club
    assert not any(
        g == "g3" for g, _, _ in subjects
    )  # two people share the name: an ambiguous name gets no link
    assert not any(g == "g5" for g, _, _ in subjects)
    feed = news_store.execute(select(ElIntelNewsFeed)).scalar_one()
    assert (
        feed.etag == '"f1"'
        and feed.robots_checked_on == date(2031, 10, 4)
        and feed.last_status == "ok 200"
    )
    state = news_store.get(ElSourceState, f"el.news.{feed.feed_id}")
    assert state is not None and state.state == "ok"


def test_a_feed_is_fetched_at_most_hourly_and_conditionally(run: Any, news_store: Session) -> None:
    polite, seen = feed_polite(
        rss(
            [
                (
                    "Zenith Harbour BC win",
                    "https://news.example.org/a",
                    "g1",
                    NEWS_NOW - timedelta(hours=1),
                )
            ]
        )
    )
    run(jobs.run_news, NEWS_NOW, polite=polite)
    made = len(seen)
    again = run(jobs.run_news, NEWS_NOW + timedelta(minutes=30), polite=polite)
    assert (
        again["status"] == "skipped"
        and "no headline feed is due" in again["detail"]
        and len(seen) == made
    )
    later = run(jobs.run_news, NEWS_NOW + timedelta(hours=1, minutes=1), polite=polite)
    assert (
        later["status"] == "ok" and "unchanged" in later["detail"]
    )  # the validators matched: a 304
    assert count(news_store, ElIntelNewsItem) == 1


def test_a_feed_whose_robots_file_disallows_hardwood_is_switched_off_with_the_reason(
    run: Any, news_store: Session
) -> None:
    polite, seen = feed_polite(b"<rss/>", robots=ROBOTS_NO)
    result = run(jobs.run_news, NEWS_NOW, polite=polite)
    assert "disabled" in result["detail"]
    feed = news_store.execute(select(ElIntelNewsFeed)).scalar_one()
    news_store.refresh(feed)
    assert feed.enabled is False and "disallows" in (feed.disabled_reason or "")
    assert seen == [seen[0]] and seen[0].endswith(
        "/robots.txt"
    )  # the feed itself was never requested


def test_old_headlines_are_pruned_but_a_pasted_link_is_kept(run: Any, news_store: Session) -> None:
    old = NEWS_NOW.replace(tzinfo=None) - timedelta(days=31)
    feed = news_store.execute(select(ElIntelNewsFeed)).scalar_one()
    stale = ElIntelNewsItem(
        feed_id=feed.feed_id,
        guid="old",
        title="Old",
        link="https://news.example.org/o",
        published_at=old,
        fetched_at=old,
        source_name="s",
    )
    pasted = ElIntelNewsItem(
        feed_id=None,
        guid=None,
        title="Pasted",
        link="https://news.example.org/p",
        published_at=old,
        fetched_at=old,
        source_name="me",
    )
    news_store.add_all([stale, pasted])
    news_store.flush()
    news_store.add(ElIntelNewsSubject(item_id=stale.item_id, club_code="ZZA", person_code=""))
    news_store.add(ElIntelNewsSubject(item_id=pasted.item_id, club_code="ZZA", person_code=""))
    news_store.commit()
    polite, _ = feed_polite(
        rss([("Fresh", "https://news.example.org/f", "g9", NEWS_NOW - timedelta(hours=1))])
    )
    run(jobs.run_news, NEWS_NOW, polite=polite)
    titles = {i.title for i in rows(news_store, ElIntelNewsItem)}
    assert titles == {"Fresh", "Pasted"}
    assert {s.item_id for s in rows(news_store, ElIntelNewsSubject)} == {pasted.item_id}


def test_a_failure_in_the_headline_job_never_touches_the_data_services_row(
    run: Any, news_store: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    write.mark_source_ok(news_store, "el.dataService", NOW)
    news_store.commit()

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the feed library broke")

    monkeypatch.setattr(jobs.intel_feeds, "fetch_feed", explode)
    polite, _ = feed_polite(b"<rss/>")
    result = run(jobs.run_news, NEWS_NOW, polite=polite)
    assert result["status"] == "error" and "the feed library broke" in result["error"]
    row = news_store.get(ElSourceState, "el.dataService")
    news_store.refresh(row)
    assert (row.state, row.consecutive_failures, row.last_error) == ("ok", 0, None)


def test_the_names_are_indexed_once_per_feed_not_once_per_headline(
    run: Any, news_store: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[int] = []
    real = jobs._subject_matcher

    def counting(*args: Any, **kwargs: Any) -> Any:
        built.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(jobs, "_subject_matcher", counting)
    when = NEWS_NOW - timedelta(hours=1)
    body = rss(
        [
            (f"Zenith Harbour BC story {n}", f"https://news.example.org/{n}", f"g{n}", when)
            for n in range(12)
        ]
    )
    polite, _ = feed_polite(body)
    run(jobs.run_news, NEWS_NOW, polite=polite)
    assert count(news_store, ElIntelNewsItem) == 12 and built == [1]


# ------------------------------------------------------------------------------- backfill


def test_backfill_prints_the_plan_and_asks_nothing_without_yes(
    service: Service, el_engine: Any, tmp_path: Path
) -> None:
    lines: list[str] = []
    result = jobs.run_backfill(
        SEASON,
        confirmed=False,
        out=lines.append,
        engine=el_engine,
        client=service.client(),
        data_dir=tmp_path,
    )
    assert result["status"] == "skipped" and result["requests"] == 0 and service.calls == []
    text = " ".join(lines)
    assert "about" in text and "requests" in text and "Run again with --yes" in text
    plan = jobs.run_backfill_plan(SEASON, rounds=34, games_per_round=9, clubs=20)
    assert plan["total"] == 2 + 34 + 20 + 34 * 9 and plan["boxScores"] == 306
    with pytest.raises(endpoints.EndpointNotAllowed):
        jobs.run_backfill_plan("2031")


def test_a_confirmed_backfill_loads_the_calendar_clubs_rounds_and_boxes(
    service: Service, el_engine: Any, tmp_path: Path, session: Session
) -> None:
    lines: list[str] = []
    service.now = T0
    result = jobs.run_backfill(
        SEASON,
        confirmed=True,
        out=lines.append,
        engine=el_engine,
        client=service.client(budget=None),
        data_dir=tmp_path,
        now=T0,
    )
    assert result["status"] == "ok" and "backfilled E2031" in result["detail"]
    suffixes = service.suffixes()
    assert suffixes[:2] == ["rounds", "clubs"]
    assert [s for s in suffixes if s.startswith("games?")] == [
        f"games?roundNumber={n}" for n in (1, 2, 3, 4)
    ]
    assert sorted(s for s in suffixes if s.endswith("/stats")) == [
        "games/1/stats",
        "games/2/stats",
        "games/3/stats",
    ]
    assert stored_game(session, "E2031-0001").stats_status == "ok"
    assert stored_game(session, "E2031-0003").stats_status == "ok"
    assert read_identity(session).kind == "live"


# ============================================================ the worker's view of these jobs


def test_every_job_the_worker_names_exists_and_accepts_what_it_passes() -> None:
    from nbastats import worker

    named = {
        spec.target.spec.split(":", 1)[1]
        for spec in worker.build_jobs()
        if isinstance(spec.target, worker.CallTarget)
        and spec.target.spec.startswith("nbastats.euroleague.ingest.jobs:")
    }
    assert named == {"run_round", "run_box", "run_structure", "run_rosters", "run_news"}
    for name in named:
        function = getattr(jobs, name)
        parameters = inspect.signature(function).parameters
        assert {"now", "shutdown", "force", "data_dir"} <= set(parameters), name
        assert all(p.default is not inspect.Parameter.empty for p in parameters.values()), name


def test_job_results_are_in_the_shape_the_worker_reads(run: Any, service: Service) -> None:
    from nbastats import worker

    ok = worker.normalise_result(run(jobs.run_structure, T0))
    assert ok.status == worker.OK and not ok.failed
    skipped = worker.normalise_result(run(jobs.run_structure, T0 + timedelta(minutes=1)))
    assert skipped.status == worker.SKIPPED
    service.bodies[f"{BASE}/rounds"] = b"not json"
    failed = worker.normalise_result(run(jobs.run_structure, monday_after(T0)))
    assert failed.failed and "unreadable" in (failed.detail or "")


def test_shutdown_may_be_a_callable_an_event_a_stop_signal_or_just_truthy() -> None:
    import threading

    from nbastats import worker

    assert jobs.stop_check(None)() is False
    assert jobs.stop_check(lambda: True)() is True and jobs.stop_check(lambda: False)() is False
    event = threading.Event()
    check = jobs.stop_check(event)
    assert check() is False
    event.set()
    assert check() is True
    signal = worker.StopSignal()  # truthy while stopping, and *not callable*
    check = jobs.stop_check(signal)
    assert not callable(signal) and check() is False
    signal.request()
    assert check() is True

    class OnlyTruthy:
        def __bool__(self) -> bool:
            return True

    assert jobs.stop_check(OnlyTruthy())() is True and jobs.stop_check(True)() is True


@pytest.mark.parametrize("kind", ["event", "stop_signal", "lambda"])
def test_a_job_stops_before_its_first_request_when_the_process_is_stopping(
    kind: str, run: Any, service: Service
) -> None:
    import threading

    from nbastats import worker

    run(jobs.run_structure, T0)  # the clubs exist now
    service.calls.clear()
    if kind == "event":
        stop: Any = threading.Event()
        stop.set()
    elif kind == "stop_signal":
        stop = worker.StopSignal()
        stop.request()
    else:
        stop = lambda: True  # noqa: E731
    result = run(jobs.run_rosters, T0 + timedelta(minutes=1), shutdown=stop)
    assert result["status"] == "ok" and "stopped by shutdown" in result["detail"]
    assert service.calls == []  # not one request after the signal


def test_the_worker_can_call_a_job_with_its_own_menu_including_a_stop_signal(
    el_engine: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nbastats import worker

    monkeypatch.setenv("HARDWOOD_EL_DATABASE_URL", f"sqlite:///{el_engine.url.database}")
    monkeypatch.setenv("HARDWOOD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'nba.db'}")
    for name in ("run_structure", "run_rosters", "run_round", "run_box", "run_news"):
        result = worker.call_with_menu(
            getattr(jobs, name),
            now=T0,
            league="euroleague",
            shutdown=worker.StopSignal(),
            force=False,
            data_dir=tmp_path,
        )
        outcome = worker.normalise_result(result)
        # an empty store has nothing to do (structure would ask, but is stopped first)
        assert not outcome.failed or name == "run_structure", (name, result)


# ================================================================================== the CLI


@pytest.fixture()
def cli_store(el_engine: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the environment at the ``el_engine`` store, so ``main()`` finds it."""
    path = Path(el_engine.url.database)
    monkeypatch.setenv("HARDWOOD_EL_DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("HARDWOOD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'nba.db'}")
    return path


def test_the_parser_has_every_command_and_the_documented_flags() -> None:
    parser = cli.build_parser()
    probe = parser.parse_args(["probe", "--season", "E2031", "--round", "2", "--offline", "x"])
    assert (probe.season, probe.round_number, probe.offline) == ("E2031", 2, "x")
    assert parser.parse_args(["import-workbook", "w.xlsx"]).include_estimates is None
    assert (
        parser.parse_args(["import-workbook", "w.xlsx", "--no-include-estimates"]).include_estimates
        is False
    )
    assert parser.parse_args(
        ["import-workbook", "w.xlsx", "--include-estimates", "--dry-run"]
    ).dry_run
    assert parser.parse_args(["backfill", "--season", "E2025"]).yes is False
    assert parser.parse_args(["backfill", "--season", "E2025", "--yes"]).yes is True
    assert parser.parse_args(["run", "box", "--force"]).force is True
    for command in ("reconcile", "status"):
        assert parser.parse_args([command]).command == command
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "everything"])
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_backfill_from_the_command_line_prints_the_plan_and_exits_cleanly(capsys: Any) -> None:
    lines: list[str] = []
    assert cli.main(["backfill", "--season", "E2025"], out=lines.append) == 0
    assert any("Back-filling E2025" in l for l in lines)
    assert any("Nothing was requested" in l for l in lines)


def test_status_and_reconcile_read_the_store_without_a_network(
    cli_store: Path, session: Session
) -> None:
    live(session)
    write.upsert_games(session, SEASON, games_of("games_round_1.json"), now=NOW)
    write.mark_source_failed(
        session,
        "el.dataService",
        NOW,
        state="blocked",
        error="forbidden (403)",
        paused_until=NOW + timedelta(hours=6),
    )
    session.commit()
    lines: list[str] = []
    assert cli.main(["status"], out=lines.append) == 0
    text = "\n".join(lines)
    assert "store kind: live" in text and "games final/none: 2" in text
    assert "source el.dataService: blocked" in text and "forbidden (403)" in text
    lines.clear()
    assert cli.main(["reconcile"], out=lines.append) == 0
    assert any("people:" in l for l in lines)


def test_run_from_the_command_line_skips_without_a_calendar_and_makes_no_request(
    cli_store: Path,
) -> None:
    lines: list[str] = []
    assert cli.main(["run", "round"], out=lines.append) == 0
    result = json.loads("\n".join(lines))
    assert result["status"] == "skipped" and result["requests"] == 0


def test_import_workbook_from_the_command_line_fails_cleanly_for_a_missing_file(
    cli_store: Path, tmp_path: Path
) -> None:
    lines: list[str] = []
    assert cli.main(["import-workbook", str(tmp_path / "absent.xlsx")], out=lines.append) == 1
    assert any("cannot read the file" in l for l in lines)


def test_reconcile_with_no_season_says_there_is_nothing_to_do(cli_store: Path) -> None:
    lines: list[str] = []
    assert cli.main(["reconcile"], out=lines.append) == 0
    assert any("nothing to reconcile" in l for l in lines)
