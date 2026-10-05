"""A later poll must never overwrite a recorded ``started`` flag with a missing one.

Why this file exists
--------------------
``player_game_basic.started`` is fed by two kinds of source:

* the per-game ``BoxScoreTraditionalV3`` path (:func:`daily.ingest_game`), which
  marks a starter by his position, so every line it produces carries
  ``started=True`` or ``started=False``; and
* the bulk ``PlayerGameLogs`` path (:func:`daily.ingest_day`, which is also what
  the nightly correction window runs), whose rows have no starter column at all.

``write_player_basic_row`` used to write ``bool(line.get("started"))`` for both.
The bulk path therefore turned "this source does not know" into ``False`` and
overwrote the starting five with a bench every night. ``games_started`` collapsed
to zero league-wide, and because the flip counted as a change it also bumped
``sync_version`` for a game whose box score had not moved.

The rule these tests pin: a line that says nothing about ``started`` leaves the
stored flag alone, and on a fresh insert leaves it ``NULL`` (not recorded, an em
dash on screen, never a guessed ``False``). A line that states ``True`` or
``False`` still wins, because that is how a genuine correction to the starting
five lands.

Everything here is invented: the unit tests build their own teams, players and
games, and the pipeline tests replay the committed look-alike corpus under
``tests/fixtures/nba_api``. No network is touched; a socket is an immediate failure.
"""
from __future__ import annotations

import shutil
import socket
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from nbastats.ingest import daily
from nbastats.ingest.client import StatsClient
from nbastats.models import Game, Player, PlayerGameBasic, PlayerSeason, Team

FIXTURES = Path(__file__).parent / "fixtures" / "nba_api"

SLATE_DATE = date(2026, 1, 2)
MODERN_GAME = "0022500512"
SEASON = "2025-26"

# Invented ids, far from any real person or franchise id.
GAME_ID = "0022599901"
HOME_TEAM = 9001
AWAY_TEAM = 9002
STARTER = 990001
BENCH = 990002


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any socket the ingest path tries to open is an immediate test failure."""

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("these tests must never open a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture()
def db(empty_engine: Engine) -> Iterator[Session]:
    """A session on a fresh, empty schema, seeded with one invented game."""
    session = Session(empty_engine, future=True)
    session.add_all(
        [
            Team(team_id=HOME_TEAM, abbr="ZZH", name="Home Club", city="Homeville",
                 nickname="Clubs"),
            Team(team_id=AWAY_TEAM, abbr="ZZA", name="Away Club", city="Awayton",
                 nickname="Visitors"),
            Game(game_id=GAME_ID, game_date=date(2026, 1, 2), season=SEASON,
                 season_type="Regular Season", home_team_id=HOME_TEAM,
                 away_team_id=AWAY_TEAM, home_pts=100, away_pts=90, status="final"),
            Player(player_id=STARTER, full_name="Ann Starter", first_name="Ann",
                   last_name="Starter", is_active=True),
            Player(player_id=BENCH, full_name="Ben Bench", first_name="Ben",
                   last_name="Bench", is_active=True),
        ]
    )
    session.flush()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def client(tmp_path: Path) -> StatsClient:
    """A fixture-mode client over a private copy of the base recorded corpus."""
    workspace = tmp_path / "nba_api"
    workspace.mkdir()
    for source in FIXTURES.glob("*.json"):
        if source.stem.endswith(("__corrected", "__complete")):
            continue
        shutil.copyfile(source, workspace / source.name)
    return StatsClient(fixtures_dir=workspace, min_delay=0.0)


def v3_line(player_id: int = STARTER, *, started: bool, pts: int = 20) -> dict[str, Any]:
    """A line shaped like the per-game V3 normaliser's output: the key is present."""
    return {**bulk_line(player_id, pts=pts), "started": started}


def bulk_line(player_id: int = STARTER, *, pts: int = 20) -> dict[str, Any]:
    """A line shaped like a bulk ``PlayerGameLogs`` row: no ``started`` key at all."""
    return {
        "player_id": player_id,
        "team_id": HOME_TEAM,
        "minutes": 30.0,
        "fgm": 8, "fga": 15, "fg3m": 2, "fg3a": 5, "ftm": 2, "fta": 2,
        "oreb": 1, "dreb": 4, "reb": 5, "ast": 3, "stl": 1, "blk": 0, "tov": 2, "pf": 2,
        "pts": pts, "plus_minus": 4.0,
    }


def stored(db: Session, player_id: int = STARTER) -> PlayerGameBasic:
    db.flush()
    db.expire_all()
    row = db.get(PlayerGameBasic, (GAME_ID, player_id))
    assert row is not None
    return row


def write(db: Session, line: dict[str, Any]) -> tuple[bool, bool]:
    return daily.write_player_basic_row(db, GAME_ID, line, SEASON)


# --------------------------------------------------------------------------- #
# The rule, at the function
# --------------------------------------------------------------------------- #


def test_a_bulk_style_line_keeps_a_recorded_start(db: Session) -> None:
    """The design's own test: V3 says started, a later bulk line is silent."""
    assert write(db, v3_line(started=True)) == (True, True)
    assert stored(db).started is True

    written, _ = write(db, bulk_line())

    assert written is True
    assert stored(db).started is True


def test_a_bulk_style_line_keeps_a_recorded_bench_flag_too(db: Session) -> None:
    """``False`` is a recorded value: silence must not turn it into NULL either."""
    write(db, v3_line(BENCH, started=False))
    assert stored(db, BENCH).started is False

    write(db, bulk_line(BENCH))

    assert stored(db, BENCH).started is False


def test_a_fresh_insert_without_the_key_is_null_not_false(db: Session) -> None:
    """A source that does not know who started must not invent a bench player."""
    assert write(db, bulk_line()) == (True, True)
    assert stored(db).started is None


def test_a_none_flag_is_silence_not_a_bench_player(db: Session) -> None:
    """``started=None`` carries no information, so it behaves like a missing key."""
    write(db, {**bulk_line(), "started": None})
    assert stored(db).started is None

    write(db, v3_line(started=True))
    write(db, {**bulk_line(), "started": None})
    assert stored(db).started is True


def test_an_explicit_value_from_a_later_line_still_wins(db: Session) -> None:
    """Only silence is ignored: a source stating a fact is how a correction lands."""
    write(db, v3_line(started=True))
    write(db, v3_line(started=False))
    assert stored(db).started is False

    write(db, v3_line(started=True))
    assert stored(db).started is True


def test_a_null_start_is_filled_in_when_a_source_finally_knows(db: Session) -> None:
    write(db, bulk_line())
    assert stored(db).started is None

    write(db, v3_line(started=True))

    assert stored(db).started is True


def test_a_silent_line_that_changes_nothing_else_reports_no_change(db: Session) -> None:
    """The flip used to count as a change and bump ``sync_version`` every night."""
    write(db, v3_line(started=True))

    written, changed = write(db, bulk_line())

    assert (written, changed) == (True, False)


def test_a_silent_line_still_applies_a_real_stat_correction(db: Session) -> None:
    """Leaving ``started`` alone must not freeze the rest of the line."""
    write(db, v3_line(started=True, pts=20))

    written, changed = write(db, bulk_line(pts=22))

    assert (written, changed) == (True, True)
    row = stored(db)
    assert row.pts == 22
    assert row.started is True


def test_a_silent_line_for_a_dnp_still_writes_nothing(db: Session) -> None:
    """The surrounding rules are untouched: no minutes means no row."""
    line = {**bulk_line(), "minutes": None}
    assert write(db, line) == (False, False)
    assert db.get(PlayerGameBasic, (GAME_ID, STARTER)) is None


# --------------------------------------------------------------------------- #
# The rule, through the real pipeline
# --------------------------------------------------------------------------- #


def _started_by_player(session: Session) -> dict[int, bool | None]:
    session.flush()
    session.expire_all()
    rows = session.execute(
        select(PlayerGameBasic).where(PlayerGameBasic.game_id == MODERN_GAME)
    ).scalars()
    return {row.player_id: row.started for row in rows}


def _player_lines(session: Session) -> dict[int, dict[str, Any]]:
    """Every stored column of every ``player_game_basic`` line of the corpus game."""
    session.flush()
    session.expire_all()
    columns = [column.key for column in PlayerGameBasic.__table__.columns]
    rows = session.execute(
        select(PlayerGameBasic).where(PlayerGameBasic.game_id == MODERN_GAME)
    ).scalars()
    return {row.player_id: {name: getattr(row, name) for name in columns} for row in rows}


def test_the_bulk_correction_pass_leaves_the_starting_five_alone(
    db: Session, client: StatsClient
) -> None:
    """``ingest_game`` (V3) then ``ingest_day`` (bulk), the order production runs them."""
    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    daily.ingest_game(db, MODERN_GAME, client=client)
    db.commit()
    after_v3 = _started_by_player(db)
    assert sum(1 for flag in after_v3.values() if flag is True) == 10, (
        "the corpus has five starters a side"
    )
    lines_after_v3 = _player_lines(db)

    daily.ingest_day(db, SLATE_DATE, client=client)
    db.commit()

    assert _started_by_player(db) == after_v3
    # The bulk pass restates the same box score, so no player line may move at all.
    # (Team ratings and franchise reference rows can still be refined by the second
    # source; that is a different table and not what this rule governs.)
    assert _player_lines(db) == lines_after_v3


def test_games_started_survives_the_bulk_pass_in_the_season_rollup(
    db: Session, client: StatsClient
) -> None:
    """``gs`` is what the client shows; it was zero for everyone after one night."""
    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    daily.ingest_game(db, MODERN_GAME, client=client)
    daily.ingest_day(db, SLATE_DATE, client=client)
    db.commit()

    starters = [
        player_id
        for player_id, flag in _started_by_player(db).items()
        if flag is True
    ]
    assert len(starters) == 10
    db.expire_all()
    rollups = {
        row.player_id: row.gs
        for row in db.execute(
            select(PlayerSeason).where(PlayerSeason.season == SEASON)
        ).scalars()
    }
    assert all(rollups[player_id] == 1 for player_id in starters)


def test_a_bulk_only_ingest_leaves_every_start_unrecorded(
    db: Session, client: StatsClient
) -> None:
    """With no V3 pass at all, nobody is marked a starter or a bench player."""
    daily.ingest_day(db, SLATE_DATE, client=client)
    db.commit()

    flags = _started_by_player(db)
    assert flags, "the bulk pass should have written the final game's lines"
    assert set(flags.values()) == {None}


# --------------------------------------------------------------------------- #
# The same rule through the Kaggle backfill
# --------------------------------------------------------------------------- #


def _kaggle_box(columns: list[str], rows: list[tuple[Any, ...]]) -> Any:
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row  # as load_kaggle_sqlite opens the release
    connection.execute(f"CREATE TABLE player_box ({', '.join(columns)})")
    marks = ", ".join("?" for _ in columns)
    connection.executemany(f"INSERT INTO player_box VALUES ({marks})", rows)
    return connection


_BOX_COLUMNS = ["game_id", "player_id", "team_id", "player_name", "min", "pts"]


def test_a_backfill_file_with_no_starter_column_keeps_recorded_starters(db: Session) -> None:
    """A Kaggle release that has no starter column says nothing about who started; it must not
    write ``False`` over the ``True`` a V3 box score recorded (PlayerSeason.gs would drop to 0)."""
    from nbastats.ingest import backfill

    write(db, v3_line(STARTER, started=True))
    write(db, v3_line(BENCH, started=False))
    connection = _kaggle_box(
        _BOX_COLUMNS,
        [
            (GAME_ID, STARTER, HOME_TEAM, "Ann Starter", "30:00", 22),
            (GAME_ID, BENCH, HOME_TEAM, "Ben Bench", "12:00", 4),
        ],
    )
    written, _, read = backfill._load_kaggle_player_box(
        db, connection, "player_box", [GAME_ID], SEASON, 100
    )
    assert read == 2 and written == 2
    assert stored(db, STARTER).started is True and stored(db, STARTER).pts == 22
    assert stored(db, BENCH).started is False


def test_a_backfill_file_with_a_starter_column_still_says_who_started(db: Session) -> None:
    from nbastats.ingest import backfill

    write(db, v3_line(STARTER, started=False))
    connection = _kaggle_box(
        [*_BOX_COLUMNS, "start_position"],
        [
            (GAME_ID, STARTER, HOME_TEAM, "Ann Starter", "30:00", 20, "F"),
            (GAME_ID, BENCH, HOME_TEAM, "Ben Bench", "12:00", 4, None),  # a blank cell: bench
        ],
    )
    backfill._load_kaggle_player_box(db, connection, "player_box", [GAME_ID], SEASON, 100)
    assert stored(db, STARTER).started is True  # an explicit value from the file still wins
    assert stored(db, BENCH).started is False
