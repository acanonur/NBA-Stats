"""Listed positions and the points they allocate: everything defence by position stands on.

Defence by position needs, for every opposing player, one answer to "which bucket do his points
belong to?" that is the same for every game he plays. ``player_position_season`` is that answer,
and these tests cover how it is built and what it refuses to be:

* **the table**: weights are all ``NULL`` or sum to one (a row summing to 0.9 would silently lose
  a tenth of a defence's points allowed), one row per player-season, three documented writers;
* **the reader** of a ``CommonTeamRoster`` response, which reports whether the ``POSITION``
  column exists separately from whether a player has a position in it;
* **the weekly job**: normalised weights, players the store does not hold skipped not invented,
  a second run that moves nothing, source precedence (a roster beats Kaggle, Kaggle never beats a
  roster, a blank never erases a usable position, a seeded row is never overwritten), and every
  way the response can be unreadable ending in an explicit ``unreadable`` state with nothing
  written;
* **the Kaggle path**: current listings only, for the current season only, never over a roster;
* **the seeder**: one deterministic row per roster spot, drawn from no random stream, so no other
  seeded number moved;
* **``opp_pts``**, the number those allocations reconcile to, and how its metric-map entries
  switch on with the catalog.

All people are invented and every response is a committed look-alike. No socket is opened.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import Engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nbastats import catalog, metrics, models
from nbastats.ingest import normalize, rosters, runner
from nbastats.ingest.client import (
    IngestUnavailable,
    StatsClient,
    UpstreamUnavailable,
)
from nbastats.ingest.rosters import (
    ABORT_AFTER_BAD_TEAMS,
    current_season,
    load_kaggle_positions,
    refresh_rosters,
    upsert_position,
)
from nbastats.models import (
    TEAM_GAME_METRIC_COLUMNS,
    TEAM_SEASON_PER_GAME_COLUMNS,
    Game,
    Player,
    PlayerPositionSeason,
    Team,
    season_column_for,
)
from nbastats.seed import ARCHETYPES, LeagueGenerator, listed_position, seed_database
from nbastats.shared import positions as shared_positions

FIXTURES = Path(__file__).parent / "fixtures" / "nba_api"
BACKEND = Path(__file__).resolve().parents[1]

SEASON = "2025-26"
LAL, BOS, MIA, NYK = 1610612747, 1610612738, 1610612748, 1610612752
NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)

LAL_PLAYERS = range(1000101, 1000110)   # nine of the ten on the recorded LAL roster
BOS_PLAYERS = range(1000201, 1000209)
NOT_IN_STORE = 1000190                  # on the LAL roster fixture, with no player row


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the roster tests must never open a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture()
def db(empty_engine: Engine) -> Iterator[Session]:
    """An empty store holding four franchises and the invented players on two rosters.

    Every player carries a sentinel ``players.position`` so a test can prove the roster job
    neither reads nor writes it.
    """
    session = Session(empty_engine, future=True)
    for team_id, abbr in ((LAL, "LAL"), (BOS, "BOS"), (MIA, "MIA"), (NYK, "NYK")):
        session.add(Team(team_id=team_id, abbr=abbr, name=abbr, city=abbr, nickname=abbr,
                         is_active=True))
    for player_id in [*LAL_PLAYERS, *BOS_PLAYERS]:
        session.add(Player(player_id=player_id, full_name=f"Player {player_id}", position="ZZ"))
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


class Corpus:
    """A writable copy of the recorded roster responses, so a test can edit one."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def file(self, team_id: int, season: str = SEASON) -> Path:
        return self.path / f"commonteamroster__{season}__{team_id}.json"

    def load(self, team_id: int) -> dict[str, Any]:
        return json.loads(self.file(team_id).read_text(encoding="utf-8"))

    def save(self, team_id: int, payload: dict[str, Any]) -> None:
        self.file(team_id).write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture()
def corpus(tmp_path: Path) -> Corpus:
    workspace = tmp_path / "rosters"
    workspace.mkdir()
    for source in FIXTURES.glob("commonteamroster__*.json"):
        shutil.copyfile(source, workspace / source.name)
    return Corpus(workspace)


@pytest.fixture()
def client(corpus: Corpus) -> StatsClient:
    return StatsClient(fixtures_dir=corpus.path, min_delay=0.0, sleeper=lambda s: None)


def positions(session: Session) -> dict[int, PlayerPositionSeason]:
    session.expire_all()
    rows = session.execute(select(PlayerPositionSeason)).scalars().all()
    return {row.player_id: row for row in rows}


def weights(row: PlayerPositionSeason) -> tuple[float | None, float | None, float | None]:
    return (row.g_weight, row.f_weight, row.c_weight)


def source_state(session: Session) -> Any:
    from nbastats.nba_intel import store

    session.expire_all()
    return store.get_source_state(session, "nba.rosters")


# ============================================================================ the table


def put(session: Session, **override: Any) -> PlayerPositionSeason:
    values: dict[str, Any] = {
        "player_id": 1000101, "season": SEASON, "team_id": LAL, "position_raw": "G-F",
        "g_weight": 0.5, "f_weight": 0.5, "c_weight": 0.0, "source": "commonTeamRoster",
        "fetched_at": datetime(2026, 1, 5), "data_source": "nba_api",
    }
    values.update(override)
    row = PlayerPositionSeason(**values)
    session.add(row)
    return row


@pytest.mark.parametrize(
    ("g", "f", "c"),
    [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.5, 0.5, 0.0), (0.0, 0.5, 0.5),
     (None, None, None)],
)
def test_weights_that_are_all_null_or_sum_to_one_are_stored(
    db: Session, g: Any, f: Any, c: Any
) -> None:
    put(db, g_weight=g, f_weight=f, c_weight=c)
    db.commit()
    stored = db.get(PlayerPositionSeason, (1000101, SEASON))
    assert stored is not None and weights(stored) == (g, f, c)


@pytest.mark.parametrize(
    ("g", "f", "c"),
    [
        (0.5, 0.4, 0.0),      # sums to 0.9: a tenth of a defence's points would vanish
        (0.5, 0.6, 0.0),      # sums to 1.1
        (0.0, 0.0, 0.0),      # sums to nothing
        (None, 1.0, 0.0),     # partly NULL: a NULL comparison is not a failure in SQL, so the
        (1.0, None, None),    # constraint has to say IS NOT NULL for each weight explicitly
        (1.0, 0.0, None),
        (-0.5, 1.5, 0.0),     # sums to one but is not a share
        (1.5, -0.5, 0.0),
    ],
)
def test_weights_that_break_the_rule_are_refused_by_the_database(
    db: Session, g: Any, f: Any, c: Any
) -> None:
    put(db, g_weight=g, f_weight=f, c_weight=c)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_only_the_three_documented_writers_may_name_a_source(db: Session) -> None:
    assert models.POSITION_SOURCES == ("commonTeamRoster", "kaggleCurrent", "seedArchetype")
    for index, source in enumerate(models.POSITION_SOURCES):
        put(db, player_id=1000101 + index, source=source)
    db.commit()
    put(db, player_id=1000150, source="guess")
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_a_player_has_one_row_per_season(db: Session) -> None:
    put(db)
    db.commit()
    put(db)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    put(db, season="2024-25")  # another season is another row
    db.commit()


def test_init_db_adds_the_three_tables_to_a_file_that_predates_them(tmp_path: Path) -> None:
    """Additive, no ALTER: ``create_all`` brings the new tables and leaves the old rows alone."""
    from nbastats.db import create_db_engine, init_db

    new_tables = {"player_position_season", "game_schedule_detail", "team_projection_ledger"}
    engine = create_db_engine(f"sqlite:///{tmp_path / 'old.db'}")
    models.Base.metadata.create_all(
        engine, tables=[t for t in models.Base.metadata.sorted_tables if t.name not in new_tables]
    )
    with Session(engine, future=True) as session:
        session.add(Team(team_id=LAL, abbr="LAL", name="LAL", city="LAL", nickname="LAL"))
        session.add(Player(player_id=1000101, full_name="Marcus Vale"))
        session.commit()
    assert not new_tables & set(inspect(engine).get_table_names())

    init_db(engine)

    assert new_tables <= set(inspect(engine).get_table_names())
    with Session(engine, future=True) as session:
        assert session.get(Team, LAL) is not None and session.get(Player, 1000101) is not None
        put(session)  # and the new table takes a row
        session.commit()
    engine.dispose()


def test_the_table_is_stamped_for_the_seeders_synthetic_check_and_indexed_for_rosters(
    empty_engine: Engine,
) -> None:
    inspector = inspect(empty_engine)
    assert "data_source" in {c["name"] for c in inspector.get_columns("player_position_season")}
    indexes = {tuple(i["column_names"]) for i in inspector.get_indexes("player_position_season")}
    assert ("team_id", "season") in indexes, "the injury matcher looks a team's roster up by it"
    pk = inspector.get_pk_constraint("player_position_season")["constrained_columns"]
    assert pk == ["player_id", "season"]


# ============================================================================ the reader


def test_a_readable_roster_reports_its_position_column_and_raw_labels(client: StatsClient) -> None:
    roster = normalize.normalize_common_team_roster(client.common_team_roster(LAL, SEASON))
    assert roster.position_column_present is True
    by_id = {row["player_id"]: row for row in roster.rows}
    assert len(roster.rows) == 10
    assert by_id[1000101]["position"] == "F" and by_id[1000104]["position"] == "F-C"
    assert by_id[1000109]["position"] == "PG-SG", "the raw label is kept, not interpreted"
    assert by_id[1000108]["position"] is None, "a blank position is missing, not an empty string"
    assert by_id[1000101]["team_id"] == LAL and by_id[1000101]["full_name"] == "Marcus Vale"
    assert by_id[1000101]["jersey"] == "7"


def test_a_response_without_a_position_column_says_so(client: StatsClient) -> None:
    roster = normalize.normalize_common_team_roster(client.common_team_roster(MIA, SEASON))
    assert roster.position_column_present is False
    assert len(roster.rows) == 8 and all(row["position"] is None for row in roster.rows)


def test_a_response_whose_every_position_is_blank_still_has_the_column(client: StatsClient) -> None:
    roster = normalize.normalize_common_team_roster(client.common_team_roster(NYK, SEASON))
    assert roster.position_column_present is True
    assert all(row["position"] is None for row in roster.rows)


def test_the_birth_date_of_an_october_birthday_parses(client: StatsClient) -> None:
    """``OCT 23, 1995``: a bare "T in text" check once cut it to ``OC`` and warned."""
    rows = normalize.normalize_common_team_roster(client.common_team_roster(BOS, SEASON)).rows
    petrov = next(row for row in rows if row["player_id"] == 1000202)
    assert petrov["birthdate"] == date(1995, 10, 23)
    assert normalize.parse_date("2026-01-02T00:00:00") == date(2026, 1, 2)
    assert normalize.parse_date("JAN 02, 2026") == date(2026, 1, 2)


def test_a_row_without_a_player_id_is_dropped(corpus: Corpus) -> None:
    payload = corpus.load(LAL)
    headers = payload["resultSets"][0]["headers"]
    payload["resultSets"][0]["rowSet"][0][headers.index("PLAYER_ID")] = None
    roster = normalize.normalize_common_team_roster(payload)
    assert len(roster.rows) == 9


@pytest.mark.parametrize("payload", [None, [], "roster", 3, {}, {"resultSets": []}])
def test_an_unexpected_roster_payload_is_empty_and_has_no_position_column(payload: Any) -> None:
    roster = normalize.normalize_common_team_roster(payload)
    assert roster.rows == [] and roster.position_column_present is False


def test_an_already_normalised_payload_is_read_too() -> None:
    roster = normalize.normalize_common_team_roster(
        {"CommonTeamRoster": [{"PLAYER_ID": 1, "POSITION": "G", "TeamID": 9}]}
    )
    assert roster.position_column_present is True and roster.rows[0]["position"] == "G"


# ============================================================================ the weekly job


def test_a_sweep_writes_normalised_weights_for_the_players_the_store_holds(
    db: Session, client: StatsClient
) -> None:
    report = refresh_rosters(db, client, season=SEASON, team_ids=[LAL, BOS], now=NOW)

    assert report.state == "ok" and not report.partial
    assert (report.teams_read, report.rows_read) == (2, 18)
    stored = positions(db)
    assert set(stored) == {*LAL_PLAYERS, *BOS_PLAYERS}
    assert weights(stored[1000102]) == (1.0, 0.0, 0.0)            # G
    assert weights(stored[1000101]) == (0.0, 1.0, 0.0)            # F
    assert weights(stored[1000103]) == (0.0, 0.0, 1.0)            # C
    assert weights(stored[1000105]) == (0.5, 0.5, 0.0)            # G-F
    assert weights(stored[1000107]) == (0.5, 0.5, 0.0)            # F-G, the same split
    assert weights(stored[1000104]) == (0.0, 0.5, 0.5)            # F-C
    assert weights(stored[1000206]) == (0.0, 0.5, 0.5)            # C-F
    assert weights(stored[1000204]) == (0.0, 0.5, 0.5)            # Forward-Center
    assert weights(stored[1000203]) == (1.0, 0.0, 0.0)            # Guard
    for row in stored.values():
        assert (row.source, row.data_source, row.season) == ("commonTeamRoster", "nba_api", SEASON)
        assert row.fetched_at == datetime(2026, 1, 5, 12, 0)
    assert stored[1000101].team_id == LAL and stored[1000201].team_id == BOS


def test_every_stored_row_obeys_the_sum_to_one_rule(db: Session, client: StatsClient) -> None:
    refresh_rosters(db, client, season=SEASON, team_ids=[LAL, BOS], now=NOW)
    for row in positions(db).values():
        if row.g_weight is None:
            assert (row.f_weight, row.c_weight) == (None, None)
        else:
            assert row.g_weight + row.f_weight + row.c_weight == pytest.approx(1.0, abs=1e-12)


def test_a_player_with_no_usable_position_gets_a_row_of_nulls_not_a_guess(
    db: Session, client: StatsClient
) -> None:
    report = refresh_rosters(db, client, season=SEASON, team_ids=[LAL], now=NOW)
    stored = positions(db)
    blank, unrecognised = stored[1000108], stored[1000109]
    assert weights(blank) == (None, None, None) and blank.position_raw is None
    assert weights(unrecognised) == (None, None, None)
    assert unrecognised.position_raw == "PG-SG", "what the source said is kept for coverage"
    assert report.unrecognised == 2 and report.unrecognised_labels == ["PG-SG"]


def test_a_roster_player_the_store_does_not_hold_is_skipped_not_invented(
    db: Session, client: StatsClient
) -> None:
    report = refresh_rosters(db, client, season=SEASON, team_ids=[LAL], now=NOW)
    assert report.unknown_player == 1
    assert NOT_IN_STORE not in positions(db)
    assert db.get(Player, NOT_IN_STORE) is None, "the job must not create a player"


def test_the_roster_job_never_reads_or_writes_players_position(
    db: Session, client: StatsClient
) -> None:
    refresh_rosters(db, client, season=SEASON, team_ids=[LAL, BOS], now=NOW)
    db.expire_all()
    sentinels = db.execute(select(func.count()).select_from(Player).where(Player.position == "ZZ"))
    assert sentinels.scalar_one() == len([*LAL_PLAYERS, *BOS_PLAYERS])


def test_a_second_sweep_over_an_unchanged_league_moves_nothing(
    db: Session, client: StatsClient
) -> None:
    refresh_rosters(db, client, season=SEASON, team_ids=[LAL, BOS], now=NOW)
    next_week = datetime(2026, 1, 12, tzinfo=timezone.utc)
    second = refresh_rosters(db, client, season=SEASON, team_ids=[LAL, BOS], now=next_week)
    assert (second.inserted, second.updated) == (0, 0)
    assert second.unchanged == 17
    assert all(row.fetched_at == datetime(2026, 1, 5, 12, 0) for row in positions(db).values())


def test_a_changed_listing_and_a_trade_are_both_picked_up(
    db: Session, client: StatsClient, corpus: Corpus
) -> None:
    refresh_rosters(db, client, season=SEASON, team_ids=[LAL, BOS], now=NOW)
    payload = corpus.load(LAL)
    headers = payload["resultSets"][0]["headers"]
    rows = payload["resultSets"][0]["rowSet"]
    moved = next(row for row in rows if row[headers.index("PLAYER_ID")] == 1000102)
    moved[headers.index("POSITION")] = "G-F"            # relisted
    traded = next(row for row in rows if row[headers.index("PLAYER_ID")] == 1000106)
    traded[headers.index("TeamID")] = BOS               # now on Boston's books
    corpus.save(LAL, payload)

    later = datetime(2026, 1, 12, 12, 0, tzinfo=timezone.utc)
    report = refresh_rosters(db, client, season=SEASON, team_ids=[LAL], now=later)
    stored = positions(db)
    assert report.updated == 2
    assert weights(stored[1000102]) == (0.5, 0.5, 0.0) and stored[1000102].position_raw == "G-F"
    assert stored[1000106].team_id == BOS
    assert stored[1000102].fetched_at == datetime(2026, 1, 12, 12, 0)
    assert stored[1000103].fetched_at == datetime(2026, 1, 5, 12, 0), "unchanged rows keep theirs"


def test_a_team_unknown_to_the_store_leaves_the_team_column_null(
    db: Session, client: StatsClient, corpus: Corpus
) -> None:
    payload = corpus.load(LAL)
    headers = payload["resultSets"][0]["headers"]
    payload["resultSets"][0]["rowSet"][0][headers.index("TeamID")] = 999
    corpus.save(LAL, payload)
    refresh_rosters(db, client, season=SEASON, team_ids=[LAL], now=NOW)
    assert positions(db)[1000101].team_id == LAL, "falls back to the roster's own team"
    db.execute(Team.__table__.delete().where(Team.team_id == LAL))
    db.commit()
    refresh_rosters(db, client, season=SEASON, team_ids=[LAL], now=NOW)
    assert positions(db)[1000101].team_id is None


def test_the_default_team_list_is_the_active_franchises(db: Session, client: StatsClient) -> None:
    db.get(Team, NYK).is_active = False  # type: ignore[union-attr]
    db.commit()
    report = refresh_rosters(db, client, season=SEASON, now=NOW)
    assert report.teams_requested == 3
    assert client.stats.by_endpoint["commonteamroster"] == 3
    assert sorted(report.teams_unreadable) == [MIA], "MIA's response has no POSITION column"
    assert report.state == "ok" and report.partial


# ----------------------------------------------------------------------- precedence


def row_for(session: Session, player_id: int = 1000101) -> PlayerPositionSeason:
    session.expire_all()
    row = session.get(PlayerPositionSeason, (player_id, SEASON))
    assert row is not None
    return row


def write(session: Session, source: str, raw: str | None, **kwargs: Any) -> str:
    outcome = upsert_position(
        session, player_id=1000101, season=SEASON, team_id=LAL, position_raw=raw,
        source=source, data_source="nba_api", fetched_at=datetime(2026, 1, 5), **kwargs,
    )
    session.commit()
    return outcome


def test_a_roster_listing_replaces_a_kaggle_one(db: Session) -> None:
    assert write(db, "kaggleCurrent", "Forward") == "inserted"
    assert write(db, "commonTeamRoster", "G-F") == "updated"
    row = row_for(db)
    assert (row.source, weights(row)) == ("commonTeamRoster", (0.5, 0.5, 0.0))


def test_a_kaggle_listing_never_replaces_a_roster_one(db: Session) -> None:
    write(db, "commonTeamRoster", "G-F")
    assert write(db, "kaggleCurrent", "Center") == "kept"
    row = row_for(db)
    assert (row.source, row.position_raw) == ("commonTeamRoster", "G-F")


def test_a_blank_or_unrecognised_label_never_erases_a_usable_position(db: Session) -> None:
    write(db, "kaggleCurrent", "Guard")
    for label in (None, "", "   ", "PG-SG", "Point Guard"):
        assert write(db, "commonTeamRoster", label) == "kept", label
    row = row_for(db)
    assert (row.source, weights(row)) == ("kaggleCurrent", (1.0, 0.0, 0.0))
    # ...but a usable one from the stronger source does replace it.
    assert write(db, "commonTeamRoster", "C") == "updated"
    assert weights(row_for(db)) == (0.0, 0.0, 1.0)


def test_a_blank_label_may_still_create_a_row_where_there_was_none(db: Session) -> None:
    assert write(db, "commonTeamRoster", "") == "inserted"
    row = row_for(db)
    assert weights(row) == (None, None, None) and row.position_raw is None


def test_an_unusable_row_is_upgraded_by_a_usable_one_from_the_same_source(db: Session) -> None:
    write(db, "commonTeamRoster", "PG-SG")
    assert write(db, "commonTeamRoster", "G") == "updated"
    assert weights(row_for(db)) == (1.0, 0.0, 0.0)


def test_a_seeded_row_is_never_overwritten_by_anything(db: Session) -> None:
    put(db, source="seedArchetype", data_source="synthetic-demo", position_raw="G-F")
    db.commit()
    for source in ("commonTeamRoster", "kaggleCurrent"):
        assert write(db, source, "C") == "kept"
    row = row_for(db)
    assert (row.source, row.data_source, weights(row)) == (
        "seedArchetype", "synthetic-demo", (0.5, 0.5, 0.0),
    )


def test_the_precedence_is_the_order_the_schema_documents() -> None:
    ranks = [rosters.source_rank(s) for s in models.POSITION_SOURCES]
    assert ranks == sorted(ranks, reverse=True) and len(set(ranks)) == 3
    assert rosters.source_rank("something else") < min(ranks)


def test_an_unknown_source_is_a_programming_error(db: Session) -> None:
    with pytest.raises(ValueError, match="not a position source"):
        write(db, "guess", "G")


def test_the_label_is_stripped_and_bounded(db: Session) -> None:
    write(db, "commonTeamRoster", "  Guard-Forward  ")
    assert row_for(db).position_raw == "Guard-Forward"
    upsert_position(db, player_id=1000102, season=SEASON, team_id=None,
                    position_raw="x" * 80, source="commonTeamRoster", data_source="nba_api")
    db.commit()
    assert len(row_for(db, 1000102).position_raw or "") == 32


# ----------------------------------------------------------------------- fail closed


class FakeClient:
    """A stand-in client: team id to a payload, or an exception to raise."""

    def __init__(self, by_team: dict[int, Any]) -> None:
        self.by_team = by_team
        self.calls: list[int] = []

    def common_team_roster(self, team_id: int, season: str) -> Any:
        self.calls.append(team_id)
        outcome = self.by_team[team_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def without_positions(payload: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(payload))
    block = clone["resultSets"][0]
    index = block["headers"].index("POSITION")
    block["headers"].pop(index)
    block["rowSet"] = [row[:index] + row[index + 1:] for row in block["rowSet"]]
    return clone


def test_when_no_team_has_a_position_column_nothing_is_written_and_the_source_says_unreadable(
    db: Session, client: StatsClient
) -> None:
    report = refresh_rosters(db, client, season=SEASON, team_ids=[MIA], now=NOW)
    assert (report.state, report.teams_read, report.written) == ("unreadable", 0, 0)
    assert report.teams_unreadable == [MIA]
    assert positions(db) == {}
    state = source_state(db)
    assert state.state == "unreadable" and state.consecutive_failures == 1
    assert "POSITION" in (state.last_error or "")


def test_a_position_column_full_of_blanks_is_unreadable_too(
    db: Session, client: StatsClient
) -> None:
    report = refresh_rosters(db, client, season=SEASON, team_ids=[NYK], now=NOW)
    assert report.state == "unreadable" and positions(db) == {}


def test_a_column_whose_every_label_is_foreign_to_the_schema_is_unreadable(
    db: Session, corpus: Corpus, client: StatsClient
) -> None:
    payload = corpus.load(LAL)
    headers = payload["resultSets"][0]["headers"]
    for row in payload["resultSets"][0]["rowSet"]:
        row[headers.index("POSITION")] = "Point Guard"  # a vocabulary the schema does not know
    corpus.save(LAL, payload)
    report = refresh_rosters(db, client, season=SEASON, team_ids=[LAL], now=NOW)
    assert report.state == "unreadable" and positions(db) == {}


def test_unreadable_teams_among_readable_ones_write_nothing_for_themselves(
    db: Session, client: StatsClient
) -> None:
    report = refresh_rosters(db, client, season=SEASON, team_ids=[LAL, MIA, NYK], now=NOW)
    assert report.state == "ok" and report.partial
    assert sorted(report.teams_unreadable) == [MIA, NYK]
    assert set(positions(db)) == set(LAL_PLAYERS)
    state = source_state(db)
    assert state.state == "ok" and state.last_success_at is not None
    detail = json.loads(state.detail_json)
    assert detail["teamsRead"] == 1 and sorted(detail["teamsUnreadable"]) == [MIA, NYK]


def test_the_sweep_stops_after_three_consecutive_bad_teams(corpus: Corpus, db: Session) -> None:
    broken = without_positions(corpus.load(LAL))
    good = corpus.load(LAL)
    client = FakeClient({1: broken, 2: broken, 3: broken, 4: good, 5: good})
    report = refresh_rosters(db, client, season=SEASON, team_ids=[1, 2, 3, 4, 5], now=NOW)
    assert ABORT_AFTER_BAD_TEAMS == 3
    assert client.calls == [1, 2, 3], "the other teams would fail the same way, at full backoff"
    assert report.state == "unreadable" and "Stopped after 3" in (report.reason or "")


def test_a_good_team_resets_the_streak(corpus: Corpus, db: Session) -> None:
    broken = without_positions(corpus.load(LAL))
    good = corpus.load(LAL)
    client = FakeClient({1: broken, 2: broken, 3: good, 4: broken, 5: broken, 6: good})
    report = refresh_rosters(db, client, season=SEASON, team_ids=[1, 2, 3, 4, 5, 6], now=NOW)
    assert client.calls == [1, 2, 3, 4, 5, 6]
    assert report.state == "ok" and report.teams_read == 2 and len(report.teams_unreadable) == 4


def test_fetch_failures_are_recorded_per_team_and_stop_the_sweep_the_same_way(
    corpus: Corpus, db: Session
) -> None:
    down = UpstreamUnavailable("stats.nba.com did not answer")
    client = FakeClient({n: down for n in range(1, 8)})
    report = refresh_rosters(db, client, season=SEASON, team_ids=list(range(1, 8)), now=NOW)
    assert client.calls == [1, 2, 3]
    assert (report.state, report.teams_failed) == ("error", [1, 2, 3])
    state = source_state(db)
    assert state.state == "error" and state.consecutive_failures == 1


def test_a_missing_nba_api_propagates_and_writes_nothing(db: Session) -> None:
    client = FakeClient({LAL: IngestUnavailable("nba_api is not installed")})
    with pytest.raises(IngestUnavailable):
        refresh_rosters(db, client, season=SEASON, team_ids=[LAL], now=NOW)
    assert positions(db) == {}


@pytest.mark.parametrize("payload", [None, [], "<html>blocked</html>", {"resultSets": "nope"}, 7])
def test_an_unexpected_shape_is_unreadable_for_that_team_and_never_a_crash(
    db: Session, payload: Any
) -> None:
    client = FakeClient({LAL: payload})
    report = refresh_rosters(db, client, season=SEASON, team_ids=[LAL], now=NOW)
    assert report.state == "unreadable" and report.teams_unreadable == [LAL]
    assert positions(db) == {}


def test_a_parser_exception_fails_closed_for_that_team_only(
    db: Session, client: StatsClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = normalize.normalize_common_team_roster
    calls: list[int] = []

    def flaky(payload: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise KeyError("a shape nobody planned for")
        return real(payload)

    monkeypatch.setattr(normalize, "normalize_common_team_roster", flaky)
    report = refresh_rosters(db, client, season=SEASON, team_ids=[LAL, BOS], now=NOW)
    assert report.teams_unreadable == [LAL] and report.teams_read == 1
    assert set(positions(db)) == set(BOS_PLAYERS)


def test_a_shutdown_request_stops_between_teams(db: Session, client: StatsClient) -> None:
    flag = {"stop": False}

    class Stop:
        def __bool__(self) -> bool:
            return flag["stop"]

    class StopAfterFirst(StatsClient):
        def common_team_roster(self, team_id: int, season: str, **kw: Any) -> Any:
            flag["stop"] = True
            return super().common_team_roster(team_id, season, **kw)

    stopper = StopAfterFirst(fixtures_dir=client.fixtures_dir, min_delay=0.0)
    report = refresh_rosters(db, stopper, season=SEASON, team_ids=[LAL, BOS], now=NOW,
                             shutdown=Stop())
    assert report.interrupted and report.teams_read == 1
    assert set(positions(db)) == set(LAL_PLAYERS), "the team in flight was finished and kept"
    assert runner.rosters_exit_code(report) == 130


def test_a_store_with_no_teams_is_an_explicit_error(
    empty_engine: Engine, client: StatsClient
) -> None:
    with Session(empty_engine, future=True) as session:
        report = refresh_rosters(session, client, season=SEASON, now=NOW)
        assert (report.state, report.teams_requested) == ("error", 0)
        assert "no teams" in (report.reason or "")
        assert source_state(session).state == "error"


def test_record_state_false_leaves_the_source_state_alone(db: Session, client: StatsClient) -> None:
    refresh_rosters(db, client, season=SEASON, team_ids=[LAL], now=NOW, record_state=False)
    assert source_state(db) is None


# ----------------------------------------------------------------------- the demo guard


@pytest.fixture()
def demo_db(empty_engine: Engine) -> Iterator[Session]:
    with Session(empty_engine, future=True) as session:
        seed_database(session, as_of=date(2026, 1, 2), seasons=["2024-25"],
                      games_per_team=4, players_per_team=9, include_playoffs=False)
        session.commit()
        yield session


def test_a_store_holding_the_demo_league_is_refused_before_any_request(
    demo_db: Session,
) -> None:
    before = demo_db.execute(select(func.count()).select_from(PlayerPositionSeason)).scalar_one()
    client = FakeClient({})
    report = refresh_rosters(demo_db, client, season="2024-25", now=NOW)
    assert report.state == "refused" and "Demo league" in (report.reason or "")
    assert client.calls == []
    after = demo_db.execute(select(func.count()).select_from(PlayerPositionSeason)).scalar_one()
    assert after == before
    assert source_state(demo_db) is None, "a refusal is not a source failure"
    assert runner.rosters_exit_code(report) == 5


def test_a_demo_store_refuses_the_kaggle_path_too(demo_db: Session, tmp_path: Path) -> None:
    path = make_kaggle(tmp_path / "kaggle.sqlite", [(1, "Guard", LAL, "Active", 2030)])
    report = load_kaggle_positions(demo_db, path, season="2024-25", now=NOW)
    assert report.state == "refused"


def test_the_guard_agrees_with_the_intel_packages_definition(
    empty_engine: Engine, demo_db: Session
) -> None:
    store = pytest.importorskip("nbastats.nba_intel.store")
    assert rosters.store_is_synthetic(demo_db) is True
    assert store.store_is_synthetic(demo_db) is True
    with Session(empty_engine, future=True) as other:
        other.execute(Game.__table__.delete())
        other.commit()
        assert rosters.store_is_synthetic(other) is False
        assert store.store_is_synthetic(other) is False


def test_a_store_with_no_games_table_is_not_synthetic(tmp_path: Path) -> None:
    from nbastats.db import create_db_engine

    engine = create_db_engine(f"sqlite:///{tmp_path / 'bare.db'}")
    with Session(engine, future=True) as session:
        assert rosters.store_is_synthetic(session) is False
    engine.dispose()


# ----------------------------------------------------------------------- the season


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 10, 1, tzinfo=timezone.utc), "2026-27"),
        (datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc), "2025-26"),
        (datetime(2026, 7, 15, tzinfo=timezone.utc), "2025-26"),
        (datetime(2027, 1, 15, tzinfo=timezone.utc), "2026-27"),
        (datetime(2099, 12, 31, tzinfo=timezone.utc), "2099-00"),
        (datetime(2026, 1, 5), "2025-26"),  # naive is read as UTC
    ],
)
def test_the_current_season_turns_over_on_the_first_of_october(
    moment: datetime, expected: str
) -> None:
    assert current_season(moment) == expected


def test_the_default_season_is_the_one_the_run_happens_in(
    db: Session, corpus: Corpus
) -> None:
    for team in (LAL, BOS):
        shutil.copyfile(corpus.file(team), corpus.file(team, "2026-27"))
    client = StatsClient(fixtures_dir=corpus.path, min_delay=0.0)
    report = refresh_rosters(db, client, team_ids=[LAL],
                             now=datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc))
    assert report.season == "2026-27"
    assert {row.season for row in positions(db).values()} == {"2026-27"}


# ============================================================================ the Kaggle path


def make_kaggle(
    path: Path,
    rows: list[tuple[Any, ...]],
    columns: tuple[str, ...] = ("person_id", "position", "team_id", "rosterstatus", "to_year"),
    table: str = "common_player_info",
) -> Path:
    connection = sqlite3.connect(path)
    connection.execute(f"CREATE TABLE {table} ({', '.join(columns)})")
    connection.executemany(
        f"INSERT INTO {table} VALUES ({', '.join('?' * len(columns))})", rows
    )
    connection.commit()
    connection.close()
    return path


def test_kaggle_writes_current_listings_for_active_players_in_the_current_season(
    db: Session, tmp_path: Path
) -> None:
    path = make_kaggle(
        tmp_path / "kaggle.sqlite",
        [
            (1000101, "Forward-Center", LAL, "Active", 2030),
            (1000102, "Guard", LAL, "Active", 2030),
            (1000201, "Center", BOS, "Inactive", 2018),    # retired: not in this season's pool
            (1000202, "Guard-Forward", 0, 1, 2030),        # team 0 is "no team"; 1 is active
            (1000109, "Point Guard", LAL, "Active", 2030), # a label the schema does not know
            (7777777, "Guard", LAL, "Active", 2030),       # a player the store does not hold
        ],
    )
    report = load_kaggle_positions(db, path, now=NOW)

    assert report.state == "ok" and report.season == "2025-26" and report.source == "kaggleCurrent"
    assert report.rows_read == 5 and report.unknown_player == 1 and report.unrecognised == 1
    stored = positions(db)
    assert set(stored) == {1000101, 1000102, 1000202, 1000109}
    assert weights(stored[1000101]) == (0.0, 0.5, 0.5) and stored[1000101].team_id == LAL
    assert stored[1000202].team_id is None and weights(stored[1000202]) == (0.5, 0.5, 0.0)
    assert weights(stored[1000109]) == (None, None, None)
    for row in stored.values():
        assert (row.source, row.data_source, row.season) == ("kaggleCurrent", "kaggle", "2025-26")


def test_kaggle_is_written_for_the_current_season_and_never_a_past_one(
    db: Session, tmp_path: Path
) -> None:
    path = make_kaggle(tmp_path / "k.sqlite", [(1000101, "Guard", LAL, "Active", 2030)])
    load_kaggle_positions(db, path, now=datetime(2026, 11, 3, tzinfo=timezone.utc))
    assert {row.season for row in positions(db).values()} == {"2026-27"}
    assert db.execute(
        select(func.count()).select_from(PlayerPositionSeason)
        .where(PlayerPositionSeason.season == "2025-26")
    ).scalar_one() == 0


def test_kaggle_never_replaces_a_roster_row_and_a_roster_replaces_kaggle(
    db: Session, client: StatsClient, tmp_path: Path
) -> None:
    refresh_rosters(db, client, season=SEASON, team_ids=[LAL], now=NOW)
    path = make_kaggle(
        tmp_path / "k.sqlite",
        [(1000101, "Center", BOS, "Active", 2030), (1000150, "Guard", LAL, "Active", 2030)],
    )
    db.add(Player(player_id=1000150, full_name="Only In Kaggle"))
    db.commit()
    report = load_kaggle_positions(db, path, now=NOW)
    stored = positions(db)
    assert report.kept == 1 and report.inserted == 1
    assert (stored[1000101].source, weights(stored[1000101])) == (
        "commonTeamRoster", (0.0, 1.0, 0.0),
    )
    assert stored[1000150].source == "kaggleCurrent"
    assert stored[1000150].team_id == LAL


def test_kaggle_does_not_touch_players_position(db: Session, tmp_path: Path) -> None:
    path = make_kaggle(tmp_path / "k.sqlite", [(1000101, "Guard", LAL, "Active", 2030)])
    load_kaggle_positions(db, path, now=NOW)
    db.expire_all()
    assert db.get(Player, 1000101).position == "ZZ"  # type: ignore[union-attr]


def test_without_a_roster_status_the_last_season_decides_who_is_active(
    db: Session, tmp_path: Path
) -> None:
    path = make_kaggle(
        tmp_path / "k.sqlite",
        [
            (1000101, "Guard", LAL, 2026),
            (1000102, "Guard", LAL, 2025),
            (1000103, "Guard", LAL, None),
        ],
        columns=("person_id", "position", "team_id", "to_year"),
    )
    report = load_kaggle_positions(db, path, now=NOW)  # season 2025-26 starts in 2025
    assert set(positions(db)) == {1000101, 1000102} and report.state == "ok"


def test_column_names_are_resolved_case_insensitively(db: Session, tmp_path: Path) -> None:
    path = make_kaggle(
        tmp_path / "k.sqlite", [(1000101, "Guard", LAL, "Active")],
        columns=("PERSON_ID", "POSITION", "TEAM_ID", "ROSTERSTATUS"),
    )
    assert load_kaggle_positions(db, path, now=NOW).state == "ok"
    assert 1000101 in positions(db)


@pytest.mark.parametrize(
    ("columns", "table", "why"),
    [
        (("person_id", "team_id", "rosterstatus"), "common_player_info", "no position column"),
        (("position", "team_id", "rosterstatus"), "common_player_info", "no player id"),
        (("person_id", "position", "team_id"), "common_player_info", "cannot tell active"),
        (("person_id", "position", "rosterstatus"), "player_bios", "no common_player_info"),
    ],
)
def test_a_kaggle_file_that_cannot_be_read_exactly_is_unreadable_and_writes_nothing(
    db: Session, tmp_path: Path, columns: tuple[str, ...], table: str, why: str
) -> None:
    row = tuple({"person_id": 1000101, "position": "Guard", "team_id": LAL,
                 "rosterstatus": "Active"}[c] for c in columns)
    path = make_kaggle(tmp_path / "k.sqlite", [row], columns=columns, table=table)
    report = load_kaggle_positions(db, path, now=NOW)
    assert report.state == "unreadable", why
    assert positions(db) == {}


def test_a_kaggle_file_with_no_active_players_is_unreadable(db: Session, tmp_path: Path) -> None:
    path = make_kaggle(tmp_path / "k.sqlite", [(1000101, "Guard", LAL, "Inactive", 2010)])
    assert load_kaggle_positions(db, path, now=NOW).state == "unreadable"


def test_a_missing_kaggle_file_is_an_error_not_an_empty_result(db: Session, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_kaggle_positions(db, tmp_path / "absent.sqlite", now=NOW)


def test_the_kaggle_file_is_opened_read_only(db: Session, tmp_path: Path) -> None:
    path = make_kaggle(tmp_path / "k.sqlite", [(1000101, "Guard", LAL, "Active", 2030)])
    before = path.read_bytes()
    load_kaggle_positions(db, path, now=NOW)
    assert path.read_bytes() == before


def test_a_path_with_spaces_and_symbols_still_opens(db: Session, tmp_path: Path) -> None:
    folder = tmp_path / "my data #1 & more"
    folder.mkdir()
    path = make_kaggle(folder / "nba file.sqlite", [(1000101, "Guard", LAL, "Active", 2030)])
    assert load_kaggle_positions(db, path, now=NOW).state == "ok"


def test_a_kaggle_failure_never_fails_the_backfill_that_called_it(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("corrupt file")

    monkeypatch.setattr(rosters, "load_kaggle_positions", boom)
    runner._record_kaggle_positions(db, str(tmp_path / "anything.sqlite"))  # must not raise
    assert db.execute(select(func.count()).select_from(Team)).scalar_one() == 4, "session usable"


# ============================================================================ the runner mode


def test_the_cli_knows_the_rosters_mode_and_takes_a_season() -> None:
    parser = runner.build_parser()
    args = parser.parse_args(["--rosters", "--seasons", "2026-27"])
    assert args.rosters is True and args.seasons == ["2026-27"]
    with pytest.raises(SystemExit):
        parser.parse_args(["--rosters", "--watch"])


@pytest.mark.parametrize(
    ("state", "code"), [("ok", 0), ("error", 3), ("unreadable", 4), ("refused", 5), ("weird", 3)]
)
def test_the_exit_status_says_what_happened(state: str, code: int) -> None:
    assert runner.rosters_exit_code(rosters.RosterReport(season=SEASON, state=state)) == code


def test_run_rosters_creates_the_franchises_then_sweeps(
    empty_engine: Engine, corpus: Corpus
) -> None:
    client = FakeClient({})

    class Recording(FakeClient):
        def common_team_roster(self, team_id: int, season: str) -> Any:
            self.calls.append(team_id)
            raise UpstreamUnavailable("down")

    recording = Recording({})
    with Session(empty_engine, future=True) as session:
        assert session.execute(select(func.count()).select_from(Team)).scalar_one() == 0
        report = runner.run_rosters(client=recording, session=session)
        assert session.execute(select(func.count()).select_from(Team)).scalar_one() == 30
    assert len(recording.calls) == ABORT_AFTER_BAD_TEAMS and report.state == "error"
    assert client.calls == []


# ============================================================================ the seeder


def test_every_seeded_player_season_has_a_listed_position(seeded_db: Session) -> None:
    missing = seeded_db.execute(
        select(func.count()).select_from(models.PlayerSeason)
        .outerjoin(
            PlayerPositionSeason,
            (PlayerPositionSeason.player_id == models.PlayerSeason.player_id)
            & (PlayerPositionSeason.season == models.PlayerSeason.season),
        )
        .where(PlayerPositionSeason.player_id.is_(None))
    ).scalar_one()
    assert missing == 0


def test_seeded_rows_are_stamped_and_only_ever_hold_the_documented_labels(
    seeded_db: Session, seed_summary: dict[str, Any]
) -> None:
    rows = seeded_db.execute(select(PlayerPositionSeason)).scalars().all()
    assert len(rows) == seed_summary["player_position_rows"] > 0
    assert {r.source for r in rows} == {"seedArchetype"}
    assert {r.data_source for r in rows} == {"synthetic-demo"}
    by_label = {r.position_raw: weights(r) for r in rows}
    assert by_label == {
        "G": (1.0, 0.0, 0.0),
        "G-F": (0.5, 0.5, 0.0),
        "F-C": (0.0, 0.5, 0.5),
    }
    for row in rows:
        assert shared_positions.weights_from_columns(*weights(row)) is not None
        assert row.team_id is not None and row.fetched_at is not None


def test_the_seeded_weights_are_what_the_shared_normaliser_says_for_the_label(
    seeded_db: Session,
) -> None:
    for row in seeded_db.execute(select(PlayerPositionSeason)).scalars():
        expected = shared_positions.normalise_nba_position(row.position_raw)
        assert expected is not None
        assert shared_positions.weights_from_columns(*weights(row)) == expected


def test_each_seeded_player_is_listed_once_per_season_on_his_own_team(seeded_db: Session) -> None:
    listed = PlayerPositionSeason
    pairs = seeded_db.execute(select(listed.player_id, listed.season, listed.team_id)).all()
    assert len({(p, s) for p, s, _t in pairs}) == len(pairs)
    mismatched = seeded_db.execute(
        select(func.count()).select_from(PlayerPositionSeason)
        .join(
            models.PlayerSeason,
            (models.PlayerSeason.player_id == PlayerPositionSeason.player_id)
            & (models.PlayerSeason.season == PlayerPositionSeason.season),
        )
        .where(models.PlayerSeason.team_id != PlayerPositionSeason.team_id)
    ).scalar_one()
    assert mismatched == 0


def test_the_listed_position_is_the_last_element_of_the_archetype_tuple() -> None:
    by_key = {a.key: listed_position(a) for a in ARCHETYPES}
    assert by_key == {
        "rim_runner": "F-C",
        "three_and_d": "G-F",
        "high_usage_guard": "G-F",
        "stretch_four": "F-C",
        "bench_playmaker": "G",
    }
    for archetype in ARCHETYPES:
        assert shared_positions.normalise_nba_position(listed_position(archetype)) is not None


def seeded_generator(session: Session) -> tuple[LeagueGenerator, dict[str, Any]]:
    generator = LeagueGenerator(
        session, as_of=date(2026, 1, 2), seasons=["2024-25"], games_per_team=4,
        players_per_team=9, include_playoffs=False,
    )
    generator.write_teams()
    rosters_by_season = {
        season: generator.build_rosters(catalog.season_sort_key(season))
        for season in generator.seasons
    }
    generator.write_players()
    generator.writer.flush()
    return generator, rosters_by_season


def test_writing_the_positions_draws_nothing_from_the_random_stream(empty_engine: Engine) -> None:
    """The stream every existing number came from must be exactly where it was."""
    with Session(empty_engine, future=True) as session:
        generator, by_season = seeded_generator(session)
        before = generator.rng.getstate()
        generator.write_player_positions(by_season)
        assert generator.rng.getstate() == before
        generator.writer.flush()
        spots = sum(len(roster) for roster in by_season["2024-25"].values())
        assert session.execute(
            select(func.count()).select_from(PlayerPositionSeason)
        ).scalar_one() == spots
        session.rollback()


def test_adding_the_position_rows_moved_no_other_seeded_number(tmp_path: Path, monkeypatch) -> None:
    """Seed with and without the new step: every other table must come out identical."""
    from sqlalchemy import text

    from nbastats.db import create_db_engine, init_db

    def fingerprint(path: Path, *, with_positions: bool) -> tuple:
        if not with_positions:
            monkeypatch.setattr(LeagueGenerator, "write_player_positions", lambda *a, **k: None)
        engine = create_db_engine(f"sqlite:///{path}")
        init_db(engine)
        with Session(engine, future=True) as session:
            seed_database(session, as_of=date(2026, 1, 2), seasons=["2024-25"],
                          games_per_team=4, players_per_team=9, include_playoffs=False)
            session.commit()
            tables = ("player_game_basic", "player_game_advanced", "team_game", "games",
                      "player_season", "team_season", "league_season", "shot_zone_season",
                      "players", "id_crosswalk")
            columns = {
                "player_game_basic": "SUM(pts), SUM(fga), SUM(minutes), SUM(ast)",
                "player_game_advanced": "SUM(off_rtg), SUM(usg_pct)",
                "team_game": "SUM(pts), SUM(opp_pts), SUM(poss)",
                "games": "SUM(home_pts), SUM(away_pts)",
                "player_season": "SUM(pts), SUM(per), SUM(ws)",
                "team_season": "SUM(pts), SUM(opp_pts), SUM(net_rtg)",
                "league_season": "SUM(average), SUM(p90)",
                "shot_zone_season": "SUM(fga), SUM(fg_pct)",
                "players": "SUM(weight), COUNT(DISTINCT full_name)",
                "id_crosswalk": "SUM(espn_id)",
            }
            out = tuple(
                tuple(session.execute(text(f"SELECT COUNT(*), {columns[t]} FROM {t}")).one())
                for t in tables
            )
        engine.dispose()
        monkeypatch.undo()
        return out

    assert fingerprint(tmp_path / "a.db", with_positions=True) == fingerprint(
        tmp_path / "b.db", with_positions=False
    )


def test_the_position_rows_are_the_same_on_every_seed(tmp_path: Path) -> None:
    from nbastats.db import create_db_engine, init_db

    def rows(path: Path) -> list[tuple]:
        engine = create_db_engine(f"sqlite:///{path}")
        init_db(engine)
        with Session(engine, future=True) as session:
            seed_database(session, as_of=date(2026, 1, 2), seasons=["2024-25"],
                          games_per_team=4, players_per_team=9, include_playoffs=False)
            session.commit()
            found = session.execute(
                select(
                    PlayerPositionSeason.player_id, PlayerPositionSeason.season,
                    PlayerPositionSeason.team_id, PlayerPositionSeason.position_raw,
                    PlayerPositionSeason.g_weight, PlayerPositionSeason.f_weight,
                    PlayerPositionSeason.c_weight,
                ).order_by(PlayerPositionSeason.player_id, PlayerPositionSeason.season)
            ).all()
        engine.dispose()
        return [tuple(r) for r in found]

    first, second = rows(tmp_path / "1.db"), rows(tmp_path / "2.db")
    assert first and first == second


def test_a_reseed_replaces_the_position_rows_and_drops_anything_else(empty_engine: Engine) -> None:
    def seed(session: Session) -> None:
        seed_database(session, as_of=date(2026, 1, 2), seasons=["2024-25"],
                      games_per_team=4, players_per_team=9, include_playoffs=False)
        session.commit()

    with Session(empty_engine, future=True) as session:
        seed(session)
        first = session.execute(select(func.count()).select_from(PlayerPositionSeason)).scalar_one()
        player_id = session.execute(
            select(Player.player_id).order_by(Player.player_id)
        ).scalars().first()
        assert player_id is not None
        session.add(PlayerPositionSeason(
            player_id=player_id, season="1999-00", source="commonTeamRoster",
            fetched_at=datetime(2026, 1, 1), data_source="nba_api"))
        session.commit()
        seed(session)
        assert session.execute(
            select(func.count()).select_from(PlayerPositionSeason)
        ).scalar_one() == first, "no stray row, no doubled row"


# ============================================================================ opp_pts


def test_the_opp_pts_columns_exist_and_are_populated_in_the_seed(seeded_db: Session) -> None:
    """The number defence by position reconciles to is a real, filled column."""
    game_rows = seeded_db.execute(
        select(func.count(), func.count(models.TeamGame.opp_pts)).select_from(models.TeamGame)
    ).one()
    assert game_rows[0] > 0 and game_rows[1] == game_rows[0]
    season_rows = seeded_db.execute(
        select(func.count(), func.count(models.TeamSeason.opp_pts)).select_from(models.TeamSeason)
    ).one()
    assert season_rows[0] > 0 and season_rows[1] == season_rows[0]


def test_opp_pts_is_the_opponents_points_in_every_seeded_game(seeded_db: Session) -> None:
    mismatched = seeded_db.execute(
        select(func.count()).select_from(models.TeamGame)
        .join(Game, Game.game_id == models.TeamGame.game_id)
        .where(models.TeamGame.opp_pts != func.coalesce(
            select(func.sum(models.PlayerGameBasic.pts))
            .where(models.PlayerGameBasic.game_id == models.TeamGame.game_id,
                   models.PlayerGameBasic.team_id != models.TeamGame.team_id)
            .scalar_subquery(), -1))
    ).scalar_one()
    assert mismatched == 0


def test_the_opp_pts_map_entries_follow_the_catalog() -> None:
    """In the maps exactly when ``contracts/metrics.json`` declares the metric."""
    declared = catalog.has_metric("opp_pts")
    assert ("opp_pts" in TEAM_GAME_METRIC_COLUMNS) is declared
    assert ("opp_pts" in TEAM_SEASON_PER_GAME_COLUMNS) is declared
    assert season_column_for("opp_pts", "team", "PerGame") == ("opp_pts" if declared else None)
    assert season_column_for("opp_pts", "team", "Totals") is None, "there is no opp_pts_tot"
    if declared:
        assert TEAM_GAME_METRIC_COLUMNS["opp_pts"] == "opp_pts"
        assert TEAM_SEASON_PER_GAME_COLUMNS["opp_pts"] == "opp_pts"
        assert "opp_pts" in models.TeamGame.__table__.c
        assert "opp_pts" in models.TeamSeason.__table__.c


def test_a_catalog_that_cannot_be_read_leaves_the_maps_as_they_were(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert models._declared_by_catalog("pts") is True
    assert models._declared_by_catalog("definitely_not_a_metric") is False

    def broken(key: str) -> bool:
        raise FileNotFoundError("no contracts directory")

    monkeypatch.setattr(catalog, "has_metric", broken)
    assert models._declared_by_catalog("pts") is False, "fails closed, never raises"


def test_declaring_opp_pts_in_the_catalog_switches_the_map_entries_on_by_itself(
    tmp_path: Path,
) -> None:
    """The handoff this module relies on, proven without anyone editing ``models.py``.

    A throwaway contracts directory carries ``metrics.json`` plus one extra descriptor for
    ``opp_pts`` (copied from ``pts`` so every field a caller reads is realistic). A fresh
    interpreter pointed at it must see the two entries, and must still seed a league whose
    ``opp_pts`` numbers are untouched by the era rules.
    """
    document = json.loads((BACKEND.parent / "contracts" / "metrics.json").read_text())
    if any(m["key"] == "opp_pts" for m in document["metrics"]):
        pytest.skip("the real catalog already declares opp_pts; the maps are covered above")
    descriptor = json.loads(json.dumps(next(m for m in document["metrics"] if m["key"] == "pts")))
    descriptor.update({"key": "opp_pts", "name": "Points Allowed", "scope": ["team"]})
    document["metrics"].append(descriptor)
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "metrics.json").write_text(json.dumps(document), encoding="utf-8")

    script = (
        "import json\n"
        "from datetime import date\n"
        "from sqlalchemy import text\n"
        "from sqlalchemy.orm import Session\n"
        "from nbastats import models\n"
        "from nbastats.db import create_db_engine, init_db\n"
        "from nbastats.seed import seed_database\n"
        "engine = create_db_engine('sqlite://')\n"
        "init_db(engine)\n"
        "with Session(engine, future=True) as s:\n"
        "    seed_database(s, as_of=date(2026, 1, 2), seasons=['2024-25'], games_per_team=4,\n"
        "                  players_per_team=9, include_playoffs=False)\n"
        "    s.commit()\n"
        "    nulls = s.execute(\n"
        "        text('SELECT COUNT(*) FROM team_game WHERE opp_pts IS NULL')).scalar()\n"
        "    total = s.execute(text('SELECT COUNT(*) FROM team_game')).scalar()\n"
        "print(json.dumps({'game': models.TEAM_GAME_METRIC_COLUMNS.get('opp_pts'),\n"
        "                  'season': models.TEAM_SEASON_PER_GAME_COLUMNS.get('opp_pts'),\n"
        "                  'nulls': nulls, 'total': total}))\n"
    )
    environment = {**os.environ, "HARDWOOD_CONTRACTS_DIR": str(contracts),
                   "PYTHONPATH": str(BACKEND)}
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            env=environment, cwd=BACKEND, timeout=180)
    assert result.returncode == 0, result.stderr[-2000:]
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    assert seen["game"] == "opp_pts" and seen["season"] == "opp_pts"
    assert seen["total"] > 0 and seen["nulls"] == 0, "the era rules must not null points allowed"


def test_points_allowed_survives_the_team_season_rebuild() -> None:
    """Guards the one hazard in switching the entry on.

    ``ingest.aggregate`` rebuilds every ``team_season`` per-game column by running each mapped
    key through ``metrics.compute_metric`` and writes the result over the value it had just
    computed for ``opp_pts``. A key with no computer returns ``None``, which would erase every
    team's points allowed on the next aggregation. So once ``opp_pts`` is in the map, the
    metric engine must answer it with the opponent's points.
    """
    if "opp_pts" not in TEAM_SEASON_PER_GAME_COLUMNS:
        pytest.skip("opp_pts is not in the maps until the catalog declares it")
    subject = {"pts": 9000.0, "fga": 7000.0}
    opponent = {"pts": 8800.0, "fga": 6900.0}
    assert metrics.compute_metric("opp_pts", row=subject, opponent_row=opponent) == 8800.0
    assert metrics.compute_metric("opp_pts", row=subject) is None, "no opponent, no number"
