"""Schedule detail (tip-off and arena) and the projection ledger table: the two tables about *when*.

``game_schedule_detail`` is written from the scoreboard when it says something and not otherwise;
``team_projection_ledger`` is where a projection is kept, as computed, so it can be reviewed
against the result. They share a file because the ledger's one cross-table rule ("a locked row is
computed before tip-off") reads the tip-off this table holds.

What is pinned here
-------------------
* the reader (:func:`normalize_scoreboard_schedule`): a scheduled game's ``"7:30 pm ET"`` becomes
  the right UTC instant on both sides of a daylight-saving change; a live or final game's status
  text, a missing time-zone database, an offset-less timestamp and an implausible one all give
  ``None`` rather than a guess;
* the writer: a row only when something is present, never an overwrite with absence, never a touch
  of a ``manual`` row, unknown games skipped, ``ingested_at`` moving only when a field changed;
* the runner hook: the scoreboard response is read a second time without a second request, and a
  failure in it cannot fail the ingest;
* the tables: ``game_schedule_detail`` is not seeded and has no ``data_source``; both tables are
  wiped by a re-seed; the ledger rejects an unknown kind, a duplicate (game, kind, inputs) and a
  missing required value, and has no column whose name belongs to betting.

Everything is invented or fixture-recorded. No socket is opened.
"""
from __future__ import annotations

import copy
import json
import shutil
import socket
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfoNotFoundError

import pytest
from sqlalchemy import Engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nbastats.ingest import normalize, runner, schedule_detail
from nbastats.ingest.client import StatsClient
from nbastats.ingest.normalize import ScheduleDetailRow, normalize_scoreboard_schedule
from nbastats.models import (
    Game,
    GameScheduleDetail,
    Team,
    TeamProjectionLedger,
)
from nbastats.seed import seed_database
from nbastats.shared import market_guard

FIXTURES = Path(__file__).parent / "fixtures" / "nba_api"

SLATE = date(2026, 1, 2)
HOME, AWAY = 1610612747, 1610612738

# Game ids: the first two are the recorded corpus's own; the third is invented here.
FINAL_GAME = "0022500512"      # in the corpus: Final
LIVE_GAME = "0022500513"       # in the corpus: Q3 4:21
UPCOMING = "0022599914"        # added by these tests: a scheduled game at 8:00 pm ET

DURING_GAMES = datetime(2026, 1, 2, 20, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the schedule-detail tests must never open a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture()
def db(empty_engine: Engine) -> Iterator[Session]:
    session = Session(empty_engine, future=True)
    session.add_all(
        [
            Team(team_id=HOME, abbr="LAL", name="Los Angeles Lakers", city="Los Angeles",
                 nickname="Lakers", is_active=True),
            Team(team_id=AWAY, abbr="BOS", name="Boston Celtics", city="Boston",
                 nickname="Celtics", is_active=True),
        ]
    )
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def add_game(session: Session, game_id: str, day: date = SLATE, status: str = "scheduled") -> None:
    session.add(
        Game(game_id=game_id, game_date=day, season="2025-26", season_type="Regular Season",
             home_team_id=HOME, away_team_id=AWAY, status=status)
    )
    session.commit()


HEADERS = [
    "GAME_DATE_EST", "GAME_SEQUENCE", "GAME_ID", "GAME_STATUS_ID", "GAME_STATUS_TEXT",
    "GAMECODE", "HOME_TEAM_ID", "VISITOR_TEAM_ID", "SEASON", "LIVE_PERIOD", "LIVE_PC_TIME",
]


def header_row(
    game_id: str,
    status_id: int = 1,
    text: str = "7:30 pm ET",
    day: str = "2026-01-02",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "GAME_DATE_EST": f"{day}T00:00:00", "GAME_SEQUENCE": 1, "GAME_ID": game_id,
        "GAME_STATUS_ID": status_id, "GAME_STATUS_TEXT": text, "GAMECODE": "20260102/BOSLAL",
        "HOME_TEAM_ID": HOME, "VISITOR_TEAM_ID": AWAY, "SEASON": "2025", "LIVE_PERIOD": 0,
        "LIVE_PC_TIME": "", **extra,
    }


def scoreboard(*rows: dict[str, Any]) -> dict[str, Any]:
    """A V2 scoreboard whose GameHeader carries every key any row mentions."""
    headers = list(HEADERS)
    for row in rows:
        headers.extend(key for key in row if key not in headers)
    return {
        "resource": "scoreboardv2",
        "resultSets": [
            {"name": "GameHeader", "headers": headers,
             "rowSet": [[row.get(h) for h in headers] for row in rows]},
            {"name": "LineScore", "headers": ["GAME_ID", "TEAM_ID", "PTS"], "rowSet": []},
        ],
    }


def schedule_of(payload: dict[str, Any], **kwargs: Any) -> dict[str, ScheduleDetailRow]:
    return {row.game_id: row for row in normalize_scoreboard_schedule(payload, **kwargs)}


# ----------------------------------------------------------------------- the reader


def test_a_scheduled_games_status_text_is_its_tipoff_in_eastern_time() -> None:
    rows = schedule_of(scoreboard(header_row("A", text="7:30 pm ET")))
    # 19:30 EST on 2 January is 00:30 UTC on the 3rd.
    assert rows["A"].tipoff_utc == datetime(2026, 1, 3, 0, 30)


@pytest.mark.parametrize(
    ("day", "text", "expected"),
    [
        ("2026-01-02", "7:30 pm ET", datetime(2026, 1, 3, 0, 30)),     # EST, UTC-5
        ("2026-07-01", "7:30 pm ET", datetime(2026, 7, 1, 23, 30)),    # EDT, UTC-4
        ("2026-03-07", "7:00 pm ET", datetime(2026, 3, 8, 0, 0)),      # the night before DST
        ("2026-03-09", "7:00 pm ET", datetime(2026, 3, 9, 23, 0)),     # the day after it
        ("2026-10-31", "8:00 pm ET", datetime(2026, 11, 1, 0, 0)),     # last EDT night
        ("2026-11-02", "8:00 pm ET", datetime(2026, 11, 3, 1, 0)),     # first full EST day
        ("2026-01-02", "12:00 pm ET", datetime(2026, 1, 2, 17, 0)),    # noon is 12 pm, not 0
        ("2026-01-02", "12:30 am ET", datetime(2026, 1, 2, 5, 30)),    # 12 am is hour 0
        ("2026-01-02", "1:05 PM ET", datetime(2026, 1, 2, 18, 5)),     # case and padding
        ("2026-01-02", " 7:30 p.m. ET ", datetime(2026, 1, 3, 0, 30)), # dots and spaces
    ],
)
def test_tipoff_is_right_on_both_sides_of_a_daylight_saving_change(
    day: str, text: str, expected: datetime
) -> None:
    rows = schedule_of(scoreboard(header_row("A", text=text, day=day)))
    assert rows["A"].tipoff_utc == expected


@pytest.mark.parametrize(
    ("status_id", "text"),
    [
        (2, "Q3 4:21"),          # live: a clock, not a tip-off
        (2, "Halftime"),
        (3, "Final"),
        (3, "Final/OT"),
        (3, "7:30 pm ET"),       # a final game's text is never read as a time, whatever it says
        (2, "7:30 pm ET"),
        (1, "TBD"),
        (1, "PPD"),
        (1, "Postponed"),
        (1, ""),
        (1, "7:30 pm"),          # no zone: refused rather than assumed to be Eastern
        (1, "7:30 pm CT"),       # another zone: refused, not converted
        (1, "19:30 ET"),         # not the format
        (1, "13:00 pm ET"),      # hour out of range
        (1, "7:75 pm ET"),       # minute out of range
        (1, "0:30 am ET"),
    ],
)
def test_anything_that_is_not_a_scheduled_clock_time_gives_no_tipoff(
    status_id: int, text: str
) -> None:
    rows = schedule_of(scoreboard(header_row("A", status_id=status_id, text=text)))
    assert rows["A"].tipoff_utc is None


def test_a_missing_status_id_still_reads_a_clock_time() -> None:
    row = header_row("A", text="7:30 pm ET")
    row["GAME_STATUS_ID"] = None
    assert schedule_of(scoreboard(row))["A"].tipoff_utc == datetime(2026, 1, 3, 0, 30)


def test_a_game_with_no_date_anywhere_has_no_tipoff_from_status_text() -> None:
    row = header_row("A", text="7:30 pm ET")
    row["GAME_DATE_EST"] = None
    assert schedule_of(scoreboard(row))["A"].tipoff_utc is None
    assert schedule_of(scoreboard(row), game_date=SLATE)["A"].tipoff_utc == datetime(
        2026, 1, 3, 0, 30
    ), "the caller's date is the fallback"


def test_an_explicit_utc_timestamp_beats_the_status_text() -> None:
    row = header_row("A", text="7:30 pm ET", GAME_TIME_UTC="2026-01-03T00:35:00Z")
    assert schedule_of(scoreboard(row))["A"].tipoff_utc == datetime(2026, 1, 3, 0, 35)


def test_an_explicit_timestamp_with_an_offset_is_converted_to_utc() -> None:
    row = header_row("A", status_id=2, text="Q1 9:00", gameEt="2026-01-02T19:30:00-05:00")
    assert schedule_of(scoreboard(row))["A"].tipoff_utc == datetime(2026, 1, 3, 0, 30)


@pytest.mark.parametrize(
    "value",
    [
        "2026-01-03T00:30:00",        # no offset: Eastern or UTC? ambiguous, so refused
        "2026-01-03 00:30:00",
        "not a time",
        "",
        12345,
        "0001-01-01T00:00:00Z",       # a placeholder
        "1970-01-01T00:00:00Z",
        "2026-01-20T00:30:00Z",       # weeks away from the game's own date
    ],
)
def test_an_ambiguous_or_implausible_timestamp_is_refused(value: Any) -> None:
    row = header_row("A", status_id=2, text="Q1 9:00", GAME_TIME_UTC=value)
    assert schedule_of(scoreboard(row))["A"].tipoff_utc is None


def test_a_refused_timestamp_falls_back_to_the_status_text() -> None:
    row = header_row("A", text="7:30 pm ET", GAME_TIME_UTC="1970-01-01T00:00:00Z")
    assert schedule_of(scoreboard(row))["A"].tipoff_utc == datetime(2026, 1, 3, 0, 30)


def test_no_timezone_database_means_no_status_text_tipoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never an approximation: an hour wrong would let a "locked" projection postdate tip-off."""

    def missing(key: str) -> Any:
        raise ZoneInfoNotFoundError(key)

    monkeypatch.setattr(normalize, "ZoneInfo", missing)
    explicit = header_row("B", GAME_TIME_UTC="2026-01-03T00:30:00Z")
    rows = schedule_of(scoreboard(header_row("A", text="7:30 pm ET"), explicit))
    assert rows["A"].tipoff_utc is None
    assert rows["B"].tipoff_utc == datetime(2026, 1, 3, 0, 30), "an explicit time needs no zone"


def test_the_arena_is_read_cleaned_and_bounded() -> None:
    rows = schedule_of(
        scoreboard(
            header_row("A", ARENA_NAME="  Fixture   Arena \n", ARENA_CITY="Testville"),
            header_row("B", arenaName="Bounded " + "x" * 200, arenaCity="y" * 200),
            header_row("C", ARENA_NAME=""),
            header_row("D", ARENA_NAME=None),
            header_row("E", ARENA_NAME=1234),
            header_row("F", ARENA_NAME="-"),
        )
    )
    assert (rows["A"].arena_name, rows["A"].arena_city) == ("Fixture Arena", "Testville")
    assert len(rows["B"].arena_name or "") == 96 and len(rows["B"].arena_city or "") == 64
    for key in "CDEF":
        assert rows[key].arena_name is None, key


def test_the_first_present_spelling_of_a_field_wins() -> None:
    rows = schedule_of(scoreboard(header_row("A", ARENA_NAME="", arenaName="Second Spelling")))
    assert rows["A"].arena_name == "Second Spelling"


def test_a_game_with_nothing_to_say_is_still_returned_with_no_detail() -> None:
    rows = schedule_of(scoreboard(header_row("A", status_id=3, text="Final")))
    assert rows["A"] == ScheduleDetailRow("A", None, None, None)
    assert rows["A"].has_detail is False


@pytest.mark.parametrize("payload", [None, [], "scoreboard", 7, {}, {"resultSets": []},
                                     {"resultSets": [{"name": "LineScore", "headers": [],
                                                      "rowSet": []}]}])
def test_an_unexpected_payload_is_an_empty_slate_not_a_crash(payload: Any) -> None:
    assert normalize_scoreboard_schedule(payload) == []


def test_a_row_without_a_game_id_is_dropped() -> None:
    assert schedule_of(scoreboard(header_row(""), header_row("A"))).keys() == {"A"}


def test_the_recorded_corpus_scoreboard_has_nothing_to_record() -> None:
    """The committed scoreboard has no arena and only Final/live games: no detail, no guess."""
    payload = json.loads((FIXTURES / "scoreboardv2__2026-01-02.json").read_text())
    rows = normalize_scoreboard_schedule(payload, game_date=SLATE)
    assert {row.game_id for row in rows} == {FINAL_GAME, LIVE_GAME}
    assert not any(row.has_detail for row in rows)


# ----------------------------------------------------------------------- the writer


def test_a_row_is_written_only_when_something_is_present(db: Session) -> None:
    for game_id in ("A", "B", "C"):
        add_game(db, game_id)
    rows = [
        ScheduleDetailRow("A", datetime(2026, 1, 3, 0, 30), None, None),
        ScheduleDetailRow("B", None, "Fixture Arena", None),
        ScheduleDetailRow("C", None, None, None),
    ]
    report = schedule_detail.write_schedule_detail(db, rows, now=datetime(2026, 1, 2, 12, 0))
    db.commit()

    assert (report.rows_seen, report.inserted, report.no_detail) == (3, 2, 1)
    stored = {row.game_id: row for row in db.execute(select(GameScheduleDetail)).scalars()}
    assert set(stored) == {"A", "B"}
    assert stored["A"].tipoff_utc == datetime(2026, 1, 3, 0, 30)
    assert stored["A"].arena_name is None, "an absent field stays NULL, never an empty string"
    assert stored["B"].tipoff_utc is None and stored["B"].arena_name == "Fixture Arena"
    assert {row.source for row in stored.values()} == {"scoreboard"}
    assert stored["A"].ingested_at == datetime(2026, 1, 2, 12, 0)


def test_a_game_the_store_does_not_hold_is_counted_and_skipped(db: Session) -> None:
    report = schedule_detail.write_schedule_detail(
        db, [ScheduleDetailRow("NOPE", datetime(2026, 1, 3, 0, 30), "Arena", None)]
    )
    assert (report.unknown_game, report.inserted) == (1, 0)
    assert db.execute(select(func.count()).select_from(GameScheduleDetail)).scalar_one() == 0


def test_a_later_poll_with_no_tipoff_never_erases_the_stored_one(db: Session) -> None:
    add_game(db, "A")
    first = datetime(2026, 1, 2, 12, 0)
    schedule_detail.write_schedule_detail(
        db, [ScheduleDetailRow("A", datetime(2026, 1, 3, 0, 30), "Fixture Arena", "Testville")],
        now=first,
    )
    # The game is live now: its status text is a clock, so the reader offers no tip-off, and
    # the arena is not repeated either.
    report = schedule_detail.write_schedule_detail(
        db, [ScheduleDetailRow("A", None, None, None)], now=first + timedelta(hours=9)
    )
    db.commit()
    row = db.get(GameScheduleDetail, "A")
    assert (report.no_detail, report.updated) == (1, 0)
    assert row is not None
    assert row.tipoff_utc == datetime(2026, 1, 3, 0, 30)
    assert (row.arena_name, row.arena_city) == ("Fixture Arena", "Testville")
    assert row.ingested_at == first


def test_a_poll_that_carries_only_some_fields_never_blanks_the_others(db: Session) -> None:
    """A live game's text has no tip-off but its arena column stays: merge, never blank."""
    add_game(db, "A")
    t0 = datetime(2026, 1, 2, 12, 0)
    schedule_detail.write_schedule_detail(
        db, [ScheduleDetailRow("A", datetime(2026, 1, 3, 0, 30), "Fixture Arena", "Testville")],
        now=t0,
    )
    arena_only = schedule_detail.write_schedule_detail(
        db, [ScheduleDetailRow("A", None, "Fixture Arena", None)], now=t0 + timedelta(hours=8)
    )
    row = db.get(GameScheduleDetail, "A")
    assert row is not None and arena_only.unchanged == 1
    assert row.tipoff_utc == datetime(2026, 1, 3, 0, 30) and row.arena_city == "Testville"

    later_tipoff = schedule_detail.write_schedule_detail(
        db, [ScheduleDetailRow("A", datetime(2026, 1, 3, 1, 0), None, None)],
        now=t0 + timedelta(hours=9),
    )
    assert later_tipoff.updated == 1
    assert row.tipoff_utc == datetime(2026, 1, 3, 1, 0), "the new value is taken..."
    assert (row.arena_name, row.arena_city) == ("Fixture Arena", "Testville"), "...the rest stays"


def test_ingested_at_moves_only_when_a_field_actually_changes(db: Session) -> None:
    add_game(db, "A")
    t0 = datetime(2026, 1, 2, 12, 0)
    detail = ScheduleDetailRow("A", datetime(2026, 1, 3, 0, 30), "Fixture Arena", None)
    schedule_detail.write_schedule_detail(db, [detail], now=t0)

    same = schedule_detail.write_schedule_detail(db, [detail], now=t0 + timedelta(minutes=5))
    assert (same.unchanged, same.updated) == (1, 0)
    assert db.get(GameScheduleDetail, "A").ingested_at == t0  # type: ignore[union-attr]

    moved = ScheduleDetailRow("A", datetime(2026, 1, 3, 1, 0), "Fixture Arena", "Testville")
    changed = schedule_detail.write_schedule_detail(db, [moved], now=t0 + timedelta(hours=1))
    row = db.get(GameScheduleDetail, "A")
    assert changed.updated == 1 and row is not None
    assert row.tipoff_utc == datetime(2026, 1, 3, 1, 0), "a game that moved is updated"
    assert row.arena_city == "Testville", "a field that was absent is filled in"
    assert row.ingested_at == t0 + timedelta(hours=1)


def test_a_manual_row_is_never_touched_by_the_scoreboard(db: Session) -> None:
    add_game(db, "A")
    typed = datetime(2026, 1, 3, 0, 0)
    db.add(GameScheduleDetail(game_id="A", tipoff_utc=typed, arena_name="Typed Arena",
                              source="manual", ingested_at=datetime(2026, 1, 1)))
    db.commit()
    report = schedule_detail.write_schedule_detail(
        db, [ScheduleDetailRow("A", datetime(2026, 1, 3, 1, 0), "Scoreboard Arena", "City")]
    )
    db.commit()
    row = db.get(GameScheduleDetail, "A")
    assert report.manual_kept == 1 and row is not None
    assert (row.tipoff_utc, row.arena_name, row.arena_city, row.source) == (
        typed, "Typed Arena", None, "manual",
    )


def test_the_source_column_only_takes_scoreboard_or_manual(db: Session) -> None:
    add_game(db, "A")
    db.add(GameScheduleDetail(game_id="A", source="rumour", ingested_at=datetime(2026, 1, 1)))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_record_scoreboard_reads_a_payload_end_to_end(db: Session) -> None:
    add_game(db, UPCOMING)
    add_game(db, LIVE_GAME, status="live")
    payload = scoreboard(
        header_row(UPCOMING, text="8:00 pm ET", ARENA_NAME="Fixture Arena"),
        header_row(LIVE_GAME, status_id=2, text="Q3 4:21"),
    )
    report = schedule_detail.record_scoreboard(db, payload, game_date=SLATE)
    assert (report.inserted, report.no_detail) == (1, 1)
    row = db.get(GameScheduleDetail, UPCOMING)
    assert row is not None and row.tipoff_utc == datetime(2026, 1, 3, 1, 0)
    assert db.get(GameScheduleDetail, LIVE_GAME) is None


def test_commit_false_leaves_the_transaction_to_the_caller(db: Session) -> None:
    add_game(db, UPCOMING)
    payload = scoreboard(header_row(UPCOMING, text="8:00 pm ET"))
    schedule_detail.record_scoreboard(db, payload, game_date=SLATE, commit=False)
    db.rollback()
    assert db.get(GameScheduleDetail, UPCOMING) is None


# ----------------------------------------------------------------------- the runner hook


class CorpusWithUpcomingGame:
    """A writable copy of the recorded corpus whose scoreboard also lists a scheduled game."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def rewrite_scoreboard(self, *, arena_for: dict[str, str]) -> None:
        file = self.path / "scoreboardv2__2026-01-02.json"
        payload = json.loads(file.read_text(encoding="utf-8"))
        header = next(s for s in payload["resultSets"] if s["name"] == "GameHeader")
        header["headers"].append("ARENA_NAME")
        for row in header["rowSet"]:
            row.append(arena_for.get(row[header["headers"].index("GAME_ID")]))
        template = copy.deepcopy(header["rowSet"][0])
        index = {name: i for i, name in enumerate(header["headers"])}
        template[index["GAME_ID"]] = UPCOMING
        template[index["GAME_STATUS_ID"]] = 1
        template[index["GAME_STATUS_TEXT"]] = "8:00 pm ET"
        template[index["HOME_TEAM_ID"]] = 1610612760
        template[index["VISITOR_TEAM_ID"]] = 1610612766
        template[index["LIVE_PERIOD"]] = 0
        template[index["LIVE_PC_TIME"]] = ""
        template[index["ARENA_NAME"]] = arena_for.get(UPCOMING)
        header["rowSet"].append(template)  # no LineScore row: a scheduled game has no score yet
        file.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture()
def corpus(tmp_path: Path) -> CorpusWithUpcomingGame:
    workspace = tmp_path / "nba_api"
    workspace.mkdir()
    for source in FIXTURES.glob("*.json"):
        if source.stem.endswith(("__corrected", "__complete")):
            continue
        shutil.copyfile(source, workspace / source.name)
    corpus = CorpusWithUpcomingGame(workspace)
    corpus.rewrite_scoreboard(
        arena_for={UPCOMING: "Fixture Arena", LIVE_GAME: "Fixture Garden"}
    )
    return corpus


@pytest.fixture()
def client(corpus: CorpusWithUpcomingGame) -> StatsClient:
    return StatsClient(fixtures_dir=corpus.path, min_delay=0.0, sleeper=lambda s: None)


def test_run_once_records_schedule_detail_without_a_second_scoreboard_request(
    db: Session, client: StatsClient
) -> None:
    runner.run_once(SLATE, client=client, session=db)

    assert client.stats.by_endpoint["scoreboardv2"] == 1, "the same response is read twice"
    upcoming = db.get(GameScheduleDetail, UPCOMING)
    assert upcoming is not None
    assert upcoming.tipoff_utc == datetime(2026, 1, 3, 1, 0)
    assert upcoming.arena_name == "Fixture Arena" and upcoming.source == "scoreboard"
    live = db.get(GameScheduleDetail, LIVE_GAME)
    assert live is not None and live.tipoff_utc is None, "a live game has an arena, no tip-off"
    assert live.arena_name == "Fixture Garden"
    assert db.get(GameScheduleDetail, FINAL_GAME) is None, "nothing said, nothing written"


def test_the_watch_loop_records_it_on_every_poll_and_stays_one_request_a_poll(
    db: Session, client: StatsClient
) -> None:
    class Clock:
        def __init__(self) -> None:
            self.now = DURING_GAMES

        def __call__(self) -> datetime:
            value, self.now = self.now, self.now + timedelta(minutes=5)
            return value

    report = runner.watch(client=client, session=db, max_polls=2, poll_seconds=1,
                          sleeper=lambda _: None, clock=Clock())
    assert report.polls == 2
    assert client.stats.by_endpoint["scoreboardv2"] == 2
    assert db.get(GameScheduleDetail, UPCOMING) is not None


def test_a_failure_in_schedule_detail_never_fails_the_ingest(
    db: Session, client: StatsClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the table is on fire")

    monkeypatch.setattr(schedule_detail, "record_scoreboard", boom)
    result = runner.run_once(SLATE, client=client, session=db)
    assert result.games >= 1 and result.player_rows > 0, "the box scores still landed"
    assert db.execute(select(func.count()).select_from(GameScheduleDetail)).scalar_one() == 0
    # ...and the session is still usable afterwards.
    assert db.execute(select(func.count()).select_from(Game)).scalar_one() >= 1


def test_a_client_that_remembers_nothing_is_simply_skipped(db: Session) -> None:
    class Forgetful:
        pass

    class Blank:
        def last_scoreboard(self, game_date: date) -> None:
            return None

    assert schedule_detail.record_from_client(db, Forgetful(), SLATE) is None
    assert schedule_detail.record_from_client(db, Blank(), SLATE) is None


def test_the_recorded_corpus_alone_writes_no_schedule_rows(
    db: Session, tmp_path: Path
) -> None:
    """Without the added arena and scheduled game the committed scoreboard says nothing."""
    plain = tmp_path / "plain"
    plain.mkdir()
    for source in FIXTURES.glob("*.json"):
        if not source.stem.endswith(("__corrected", "__complete")):
            shutil.copyfile(source, plain / source.name)
    plain_client = StatsClient(fixtures_dir=plain, min_delay=0.0, sleeper=lambda s: None)
    runner.run_once(SLATE, client=plain_client, session=db)
    assert db.execute(select(func.count()).select_from(GameScheduleDetail)).scalar_one() == 0


# ----------------------------------------------------------------------- the tables


def test_schedule_detail_has_no_data_source_and_is_never_seeded(seeded_db: Session) -> None:
    columns = {c["name"] for c in inspect(seeded_db.get_bind()).get_columns("game_schedule_detail")}
    assert columns == {"game_id", "tipoff_utc", "arena_name", "arena_city", "source", "ingested_at"}
    assert seeded_db.execute(select(func.count()).select_from(GameScheduleDetail)).scalar_one() == 0
    assert seeded_db.execute(
        select(func.count()).select_from(TeamProjectionLedger)
    ).scalar_one() == 0


def test_a_reseed_wipes_both_tables_with_the_games_they_describe(empty_engine: Engine) -> None:
    def seed(session: Session) -> None:
        seed_database(
            session, as_of=date(2026, 1, 2), seasons=["2024-25"],
            games_per_team=4, players_per_team=9, include_playoffs=False,
        )
        session.commit()

    with Session(empty_engine, future=True) as session:
        seed(session)
        game_id = session.execute(select(Game.game_id).order_by(Game.game_id)).scalars().first()
        assert game_id is not None
        session.add(GameScheduleDetail(game_id=game_id, source="manual",
                                       ingested_at=datetime(2026, 1, 1)))
        session.add(TeamProjectionLedger(**ledger_values(game_id)))
        session.commit()
        assert count(session, GameScheduleDetail) == 1

        seed(session)
        assert count(session, GameScheduleDetail) == 0
        assert count(session, TeamProjectionLedger) == 0


def count(session: Session, model: Any) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def ledger_values(game_id: str = "A", **override: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "game_id": game_id, "kind": "latest", "model_version": "test-1",
        "computed_at": datetime(2026, 1, 2, 12, 0), "inputs_cutoff": datetime(2026, 1, 2, 11, 0),
        "home_pts": 112.4, "away_pts": 109.1, "home_full_strength": 113.0,
        "away_full_strength": 110.2, "home_attack_index": 1.02, "away_attack_index": 0.98,
        "home_defence_index": 0.99, "away_defence_index": 1.01,
        "home_availability_factor": 0.995, "away_availability_factor": 1.0,
        "home_advantage_points": 2.5, "settings_sha256": "a" * 64, "inputs_sha256": "b" * 64,
    }
    values.update(override)
    return values


def test_the_ledger_stores_a_projection_and_leaves_the_spreads_null_until_fitted(
    db: Session,
) -> None:
    add_game(db, "A")
    db.add(TeamProjectionLedger(**ledger_values("A")))
    db.commit()
    row = db.execute(select(TeamProjectionLedger)).scalar_one()
    assert row.ledger_id is not None
    assert (row.team_sd, row.margin_sd, row.availability_snapshot_id) == (None, None, None)
    assert row.kind == "latest" and row.home_pts == pytest.approx(112.4)


@pytest.mark.parametrize("kind", ["latest", "locked"])
def test_the_ledger_accepts_exactly_the_two_kinds(db: Session, kind: str) -> None:
    add_game(db, "A")
    db.add(TeamProjectionLedger(**ledger_values("A", kind=kind)))
    db.commit()


@pytest.mark.parametrize("kind", ["reconstructed", "imported", "final", ""])
def test_the_ledger_refuses_any_other_kind(db: Session, kind: str) -> None:
    add_game(db, "A")
    db.add(TeamProjectionLedger(**ledger_values("A", kind=kind)))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_a_game_kind_and_inputs_hash_is_recorded_once(db: Session) -> None:
    add_game(db, "A")
    db.add(TeamProjectionLedger(**ledger_values("A")))
    db.commit()
    db.add(TeamProjectionLedger(**ledger_values("A")))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # New inputs, or the other kind, are different rows.
    db.add(TeamProjectionLedger(**ledger_values("A", inputs_sha256="c" * 64)))
    db.add(TeamProjectionLedger(**ledger_values("A", kind="locked")))
    db.commit()
    assert db.execute(select(func.count()).select_from(TeamProjectionLedger)).scalar_one() == 3


@pytest.mark.parametrize(
    "column",
    ["home_pts", "away_pts", "home_full_strength", "away_full_strength", "home_attack_index",
     "away_attack_index", "home_defence_index", "away_defence_index",
     "home_availability_factor", "away_availability_factor", "home_advantage_points",
     "model_version", "computed_at", "inputs_cutoff", "settings_sha256", "inputs_sha256"],
)
def test_every_projected_number_is_required_so_a_null_cannot_pose_as_a_forecast(
    db: Session, column: str
) -> None:
    add_game(db, "A")
    db.add(TeamProjectionLedger(**ledger_values("A", **{column: None})))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_no_ledger_or_schedule_column_is_named_for_betting() -> None:
    """The structural guard's own vocabulary, applied to column names at the source."""
    for model in (TeamProjectionLedger, GameScheduleDetail):
        for column in model.__table__.columns:
            assert market_guard.forbidden_words_in(column.name) == (), (
                model.__tablename__, column.name,
            )
    names = {c.name for c in TeamProjectionLedger.__table__.columns}
    assert not names & {"win_prob", "home_win_pct", "line", "total", "odds", "edge"}
