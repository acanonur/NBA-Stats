"""The ingest pipeline, exercised end to end against recorded payloads.

**Nothing here touches the network, ever.** ``_no_network`` is autouse and replaces
``socket.socket`` with a function that raises, so a regression that reintroduces a
live call fails loudly instead of hanging for sixty seconds against a stats.nba.com
that silently drops datacenter traffic. Every call reads a file from
``tests/fixtures/nba_api/`` through ``StatsClient``'s fixture mode.

What the recorded corpus contains
---------------------------------
One slate, ``2026-01-02``, with two games:

``0022500512``  BOS @ LAL, final. Traditional and advanced V3 box scores, the V2
                team log and both V2 player logs, plus ``__corrected`` variants in
                which one assist moved from a turnover — the shape of a real
                post-hoc stat correction.
``0022500513``  NYK @ MIA. Live in ``scoreboardv2__2026-01-02.json`` and final in
                the ``__complete`` variant, which is how a game flipping to Final
                between two polls is simulated.

``0029500012`` is a 1995-96 game: no advanced box exists for it and none may be
invented, which is what the era tests assert.

The corpus is arithmetically closed — team totals are the sum of the player lines,
``PTS == 2*FGM + FG3M + FTM``, and the V2 and V3 payloads describe the same game —
so :func:`test_v2_and_v3_describe_the_same_game` is a real cross-check of the
translation layer rather than a tautology.
"""
from __future__ import annotations

import importlib
import json
import random
import re
import shutil
import socket
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from nbastats import catalog
from nbastats.db import read_sync_state
from nbastats.ingest import aggregate, backfill, daily
from nbastats.ingest import client as client_module
from nbastats.ingest import normalize as norm
from nbastats.ingest.client import (
    FixtureMissing,
    IngestUnavailable,
    RateLimiter,
    RateLimitStats,
    StatsClient,
    UpstreamUnavailable,
    fixture_name,
    polite_get,
)
from nbastats.models import (
    Base,
    Game,
    IngestLog,
    LeagueSeason,
    Player,
    PlayerGameAdvanced,
    PlayerGameBasic,
    PlayerSeason,
    ShotZoneSeason,
    Team,
    TeamGame,
    TeamSeason,
)

FIXTURES = Path(__file__).parent / "fixtures" / "nba_api"

SLATE_DATE = date(2026, 1, 2)
MODERN_GAME = "0022500512"
SECOND_GAME = "0022500513"
PRE_ADVANCED_GAME = "0029500012"
PRE_ADVANCED_DATE = date(1995, 11, 7)
CURRENT_SEASON = "2025-26"
PRE_ADVANCED_SEASON = "1995-96"

#: The player whose line the ``__corrected`` payloads revise: one assist gained,
#: one turnover dropped, three days after the game.
CORRECTED_PLAYER = 1000101


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any socket the ingest path tries to open an immediate test failure."""

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "the ingest tests must never open a socket; something bypassed fixture mode"
        )

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


class Recorded:
    """A private copy of the recorded corpus, so corrections can be swapped in.

    ``StatsClient`` resolves a payload by file name, so a stat correction is
    modelled the way it actually reaches a worker: the *same* call returns
    different bytes the next night. ``apply(...)`` copies a ``__corrected`` or
    ``__complete`` variant over the name the client asks for.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def apply(self, suffix: str) -> list[str]:
        applied = []
        for source in sorted(FIXTURES.glob(f"*__{suffix}.json")):
            target = self.path / source.name.replace(f"__{suffix}", "")
            shutil.copyfile(source, target)
            applied.append(target.name)
        assert applied, f"no recorded payloads carry the {suffix!r} variant"
        return applied

    def payload(self, name: str) -> dict[str, Any]:
        return json.loads((self.path / name).read_text(encoding="utf-8"))


@pytest.fixture()
def recorded(tmp_path: Path) -> Recorded:
    """The base corpus, copied somewhere writable. Variants stay in the source tree."""
    workspace = tmp_path / "nba_api"
    workspace.mkdir()
    for source in FIXTURES.glob("*.json"):
        if source.stem.endswith(("__corrected", "__complete")):
            continue
        shutil.copyfile(source, workspace / source.name)
    return Recorded(workspace)


@pytest.fixture()
def client(recorded: Recorded) -> StatsClient:
    """A fixture-mode client: no pacing, no sleeping, no network."""
    return StatsClient(
        fixtures_dir=recorded.path, min_delay=0.0, sleeper=_unexpected_sleep
    )


@pytest.fixture()
def db(empty_engine: Engine) -> Iterator[Session]:
    """A session on a fresh, empty schema — every ingest test writes."""
    session = Session(empty_engine, future=True)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _unexpected_sleep(seconds: float) -> None:
    raise AssertionError(f"fixture mode slept for {seconds}s; it must not pace or retry")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def snapshot(
    session: Session, model: type[Base], *, ignore: tuple[str, ...] = ()
) -> dict[tuple[Any, ...], dict[str, Any]]:
    """Every row of ``model``, keyed by primary key, as plain comparable dicts."""
    columns = [c.key for c in model.__table__.columns if c.key not in ignore]
    keys = [c.key for c in model.__table__.primary_key.columns]
    return {
        tuple(getattr(row, key) for key in keys): {
            column: getattr(row, column) for column in columns
        }
        for row in session.execute(select(model)).scalars().all()
    }


def stat_columns(model: type[Base]) -> set[str]:
    return {column.key for column in model.__table__.columns}


def v2_rows(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """The raw ``headers``/``rowSet`` pairs, zipped but otherwise untouched."""
    result = next(item for item in payload["resultSets"] if item["name"] == name)
    return [dict(zip(result["headers"], row)) for row in result["rowSet"]]


def ingest_slate(session: Session, client: StatsClient) -> None:
    """Poll the slate and ingest the one game that is final on it."""
    daily.poll_finalized_games(session, SLATE_DATE, client=client)
    daily.ingest_game(session, MODERN_GAME, client=client)


# --------------------------------------------------------------------------- #
# Scalar parsers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("34:12", 34.2),  # MM:SS — seconds are a fraction of a minute, not decimals
        ("38:24", 38.4),
        ("240:00", 240.0),
        ("PT34M12.00S", 34.2),  # the V3 ISO-8601 duration
        ("PT12M00.00S", 12.0),
        ("34", 34.0),
        (34, 34.0),
        (34.2, 34.2),
        (0, 0.0),  # a real zero survives; only *missing* becomes None
        (None, None),
        ("", None),
        ("-", None),
        ("not-a-time", None),  # malformed
        (True, None),  # a bool is never a minutes value
    ],
)
def test_parse_minutes(value: Any, expected: float | None) -> None:
    parsed = norm.parse_minutes(value)
    if expected is None:
        assert parsed is None
    else:
        assert parsed == pytest.approx(expected)
        assert isinstance(parsed, float)


def test_parse_minutes_never_turns_a_dnp_into_zero() -> None:
    """A DNP has no minutes line at all; 0.0 would read as "played, did nothing"."""
    for missing in (None, "", "-", "--"):
        assert norm.parse_minutes(missing) is None


@pytest.mark.parametrize(
    ("status_id", "status_text", "expected"),
    [
        (3, "Final", "final"),
        (2, "Q3 4:21", "live"),
        (1, "7:30 pm ET", "scheduled"),
        ("3", None, "final"),  # the id arrives as a string from some feeds
        (None, "Final", "final"),
        (None, "Final/OT", "final"),
        (None, "Final/2OT", "final"),
        (None, "Q1 11:23", "live"),
        (None, "Halftime", "live"),
        (None, "End of 3rd Qtr", "live"),
        (None, "OT 2:11", "live"),
        (None, "4:21", "live"),  # a bare clock
        (None, "7:30 pm ET", "scheduled"),
        (None, "12:00 am ET", "scheduled"),
        (None, "PPD", "scheduled"),
        (None, "", "scheduled"),
        (None, None, "scheduled"),
        (None, "something new", "scheduled"),  # unknown falls back to publishing nothing
    ],
)
def test_parse_game_status(status_id: Any, status_text: Any, expected: str) -> None:
    assert norm.parse_game_status(status_id, status_text) == expected


def test_game_status_id_outranks_the_display_text() -> None:
    """The numeric id is authoritative: a stale "Q4 0:00" must not unfinalize a game."""
    assert norm.parse_game_status(3, "Q4 0:00") == "final"
    assert norm.parse_game_status(1, "Final") == "scheduled"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-01-02", date(2026, 1, 2)),
        ("2026-01-02T00:00:00", date(2026, 1, 2)),
        ("JAN 02, 2026", date(2026, 1, 2)),
        ("01/02/2026", date(2026, 1, 2)),
        ("20260102", date(2026, 1, 2)),
        (date(2026, 1, 2), date(2026, 1, 2)),
        ("", None),
        (None, None),
        ("garbage", None),
    ],
)
def test_parse_date(value: Any, expected: date | None) -> None:
    assert norm.parse_date(value) == expected


@pytest.mark.parametrize("missing", ["", "-", "--", "none", "null", "NaN", None])
def test_missing_is_none_never_zero(missing: Any) -> None:
    assert norm.parse_int(missing) is None
    assert norm.parse_float(missing) is None


def test_a_real_zero_survives_parsing() -> None:
    assert norm.parse_int(0) == 0
    assert norm.parse_int("0") == 0
    assert norm.parse_float("0.0") == 0.0


@pytest.mark.parametrize(
    ("game_id", "season", "season_type"),
    [
        ("0022500512", "2025-26", "Regular Season"),
        ("0029500012", "1995-96", "Regular Season"),
        ("0029600001", "1996-97", "Regular Season"),
        ("0020400001", "2004-05", "Regular Season"),
        ("0042400301", "2024-25", "Playoffs"),
        ("0052400101", "2024-25", "Play In"),
        ("0012500001", "2025-26", "Pre Season"),
    ],
)
def test_game_id_vocabulary(game_id: str, season: str, season_type: str) -> None:
    """The century pivot keeps 1996-97 and 2004-05 apart, which a naive parse loses."""
    assert norm.season_from_game_id(game_id) == season
    assert norm.season_type_from_game_id(game_id) == season_type


def test_season_string_round_trip() -> None:
    assert norm.season_string(2025) == "2025-26"
    assert norm.season_string(1999) == "1999-00"
    assert norm.season_start_year("2025-26") == 2025
    assert norm.season_start_year("nonsense") is None
    assert norm.season_from_season_id("22025") == "2025-26"


@pytest.mark.parametrize(
    ("matchup", "opponent", "is_home"),
    [
        ("LAL vs. BOS", "BOS", True),
        ("BOS @ LAL", "LAL", False),
        ("", None, None),
        (None, None, None),
    ],
)
def test_matchup_parsing(matchup: Any, opponent: str | None, is_home: bool | None) -> None:
    assert norm.opponent_from_matchup(matchup) == opponent
    assert norm.is_home_from_matchup(matchup) is is_home


def test_percent_scale_is_a_declared_property_of_the_source() -> None:
    """A source reporting 0-100 is converted; one reporting fractions is left alone."""
    rows = [{"TS_PCT": 61.5}]
    scaled = norm.normalize_rows(rows, {"TS_PCT": "ts_pct"}, percent_scale_100=("ts_pct",))
    assert scaled[0]["ts_pct"] == pytest.approx(0.615)
    assert norm.normalize_rows(rows, {"TS_PCT": "ts_pct"})[0]["ts_pct"] == 61.5


def test_unknown_upstream_columns_are_ignored_without_error() -> None:
    rows = [{"PTS": 30, "WNBA_FANTASY_PTS": 41.2, "SOME_NEW_TRACKING_COLUMN": 7}]
    assert norm.normalize_rows(rows, {"PTS": "pts"}) == [{"pts": 30}]


# --------------------------------------------------------------------------- #
# V2 envelope — UPPER_SNAKE_CASE headers/rowSet
# --------------------------------------------------------------------------- #


def test_result_set_rows_zips_headers_and_rowset() -> None:
    payload = {
        "resultSets": [
            {"name": "A", "headers": ["GAME_ID", "PTS"], "rowSet": [["0022500512", 118]]}
        ]
    }
    assert norm.result_set_rows(payload, "A") == [{"GAME_ID": "0022500512", "PTS": 118}]
    assert norm.result_set_names(payload) == ["A"]
    # An absent result set is an empty slate, not an exception.
    assert norm.result_set_rows(payload, "B") == []
    # nba_api's get_normalized_dict() shape records just as well.
    assert norm.result_set_rows({"A": [{"GAME_ID": "x"}]}, "A") == [{"GAME_ID": "x"}]


def test_league_game_log_v2_round_trip(recorded: Recorded) -> None:
    """``LeagueGameLog`` → ``team_game`` columns, values and all."""
    name = "leaguegamelog__2025-26__regular-season__t__2026-01-02__2026-01-02.json"
    payload = recorded.payload(name)
    raw = {row["TEAM_ID"]: row for row in v2_rows(payload, "LeagueGameLog")}
    rows = norm.normalize_league_game_log(payload)

    assert {row["team_id"] for row in rows} == set(raw)
    columns = stat_columns(TeamGame)
    for row in rows:
        source = raw[row["team_id"]]
        for column in daily._TEAM_COLUMNS:
            assert column in columns, f"{column} is not a team_game column"
        assert row["game_id"] == source["GAME_ID"]
        assert row["pts"] == source["PTS"]
        assert row["fgm"] == source["FGM"] and row["fga"] == source["FGA"]
        assert row["minutes"] == pytest.approx(float(source["MIN"]))
        assert row["plus_minus"] == pytest.approx(float(source["PLUS_MINUS"]))
        assert row["fg_pct"] == pytest.approx(source["FG_PCT"])
        # Derived from SEASON_ID, the game id and MATCHUP so callers need not re-parse.
        assert row["season"] == CURRENT_SEASON
        assert row["season_type"] == "Regular Season"
        assert "season_id" not in row, "SEASON_ID is consumed, not carried through"
        assert row["opponent_abbr"] != row["team_abbr"]
        assert row["is_home"] is (" vs. " in source["MATCHUP"])
        assert row["won"] is (source["WL"] == "W")


def test_player_game_logs_v2_round_trip(recorded: Recorded) -> None:
    """``PlayerGameLogs`` Base and Advanced → the two player game tables."""
    base_name = "playergamelogs__2025-26__regular-season__base__2026-01-02__2026-01-02.json"
    adv_name = "playergamelogs__2025-26__regular-season__advanced__2026-01-02__2026-01-02.json"
    base_payload, adv_payload = recorded.payload(base_name), recorded.payload(adv_name)

    base_raw = {row["PLAYER_ID"]: row for row in v2_rows(base_payload, "PlayerGameLogs")}
    base = norm.normalize_player_game_logs(base_payload, "Base")
    assert {row["player_id"] for row in base} == set(base_raw)
    basic_columns = stat_columns(PlayerGameBasic)
    for row in base:
        source = base_raw[row["player_id"]]
        assert row["pts"] == source["PTS"] and row["ast"] == source["AST"]
        assert row["minutes"] == pytest.approx(source["MIN"])
        assert row["fantasy_pts"] == pytest.approx(source["NBA_FANTASY_PTS"])
        assert row["season"] == CURRENT_SEASON
        assert row["won"] is (source["WL"] == "W")
        assert set(daily._BASIC_COLUMNS) <= basic_columns

    adv_raw = {row["PLAYER_ID"]: row for row in v2_rows(adv_payload, "PlayerGameLogs")}
    advanced = norm.normalize_player_game_logs(adv_payload, "Advanced")
    advanced_columns = stat_columns(PlayerGameAdvanced)
    for row in advanced:
        source = adv_raw[row["player_id"]]
        assert row["off_rtg"] == pytest.approx(source["OFF_RATING"])
        assert row["ts_pct"] == pytest.approx(source["TS_PCT"])
        # TM_TOV_PCT is an upstream misnomer: on a player row it is his own ratio.
        assert row["tov_pct"] == pytest.approx(source["TM_TOV_PCT"])
        assert row["poss"] == pytest.approx(source["POSS"])
        assert set(daily._ADVANCED_COLUMNS) <= advanced_columns
        # The league's own E_-prefixed estimates are deliberately dropped, so one
        # number has one meaning.
        assert not any(key.startswith("e_") for key in row)


def test_common_all_players_splits_the_name_reliably(recorded: Recorded) -> None:
    rows = norm.normalize_common_all_players(
        recorded.payload("commonallplayers__2025-26__all.json")
    )
    by_id = {row["player_id"]: row for row in rows}
    assert by_id[CORRECTED_PLAYER]["first_name"] == "Marcus"
    assert by_id[CORRECTED_PLAYER]["last_name"] == "Vale"
    assert by_id[CORRECTED_PLAYER]["is_active"] is True
    assert all("roster_status" not in row for row in rows)


def test_scoreboard_v2_joins_the_line_score(recorded: Recorded) -> None:
    games = {
        row["game_id"]: row
        for row in norm.normalize_scoreboard(
            recorded.payload("scoreboardv2__2026-01-02.json"), game_date=SLATE_DATE
        )
    }
    final, live = games[MODERN_GAME], games[SECOND_GAME]
    assert final["status"] == "final"
    assert (final["home_pts"], final["away_pts"]) == (118, 112)
    assert final["clock"] is None, "a final game must never carry a stale clock"
    assert live["status"] == "live"
    assert live["clock"] == "4:21" and live["period"] == 3
    assert final["season"] == CURRENT_SEASON and final["season_type"] == "Regular Season"
    assert final["game_date"] == SLATE_DATE


# --------------------------------------------------------------------------- #
# V3 envelope — nested camelCase objects
# --------------------------------------------------------------------------- #


def test_box_score_traditional_v3_round_trip(recorded: Recorded) -> None:
    payload = recorded.payload(f"boxscoretraditionalv3__{MODERN_GAME}.json")
    box = norm.normalize_box_score(payload, "traditional")
    node = payload["boxScoreTraditional"]

    assert box.game_id == MODERN_GAME and box.view == "traditional"
    assert box.home.team_id == node["homeTeamId"] and box.home.is_home is True
    assert box.away.team_id == node["awayTeamId"] and box.away.is_home is False

    raw = {p["personId"]: p for p in node["homeTeam"]["players"]}
    basic_columns = stat_columns(PlayerGameBasic)
    for line in box.home.players:
        source = raw[line["player_id"]]["statistics"]
        # camelCase in, snake_case that the model actually has out.
        for column in daily._BASIC_COLUMNS:
            assert column in basic_columns
        assert line["pts"] == source["points"]
        assert line["reb"] == source["reboundsTotal"]
        assert line["oreb"] == source["reboundsOffensive"]
        assert line["fg3a"] == source["threePointersAttempted"]
        assert line["tov"] == source["turnovers"]
        assert line["pf"] == source["foulsPersonal"]
        assert line["plus_minus"] == pytest.approx(source["plusMinusPoints"])
        assert line["minutes"] == norm.parse_minutes(source["minutes"])
        assert line["team_id"] == box.home.team_id
        assert not any(key[0].isupper() or "." in key for key in line)

    team_columns = stat_columns(TeamGame)
    assert set(daily._TEAM_COLUMNS) <= team_columns
    assert box.home.stats["pts"] == node["homeTeam"]["statistics"]["points"]
    assert box.home.stats["minutes"] == pytest.approx(240.0)


def test_box_score_advanced_v3_round_trip(recorded: Recorded) -> None:
    payload = recorded.payload(f"boxscoreadvancedv3__{MODERN_GAME}.json")
    box = norm.normalize_box_score(payload, "advanced")
    raw = {
        p["personId"]: p["statistics"]
        for p in payload["boxScoreAdvanced"]["homeTeam"]["players"]
    }
    advanced_columns = stat_columns(PlayerGameAdvanced)
    for line in box.home.players:
        source = raw[line["player_id"]]
        for column in daily._ADVANCED_COLUMNS:
            assert column in advanced_columns
        assert line["off_rtg"] == pytest.approx(source["offensiveRating"])
        assert line["usg_pct"] == pytest.approx(source["usagePercentage"])
        assert line["tov_pct"] == pytest.approx(source["turnoverRatio"])
        assert line["efg_pct"] == pytest.approx(source["effectiveFieldGoalPercentage"])
        assert line["poss"] == pytest.approx(source["possessions"])
        assert line["pie"] == pytest.approx(source["PIE"])
    # PIE and every *_pct come through as fractions, per CONTRACT.md §1.
    for line in box.players():
        for column in ("ts_pct", "efg_pct", "usg_pct", "ast_pct", "reb_pct", "tov_pct"):
            assert 0.0 <= line[column] <= 1.0


def test_v3_marks_starters_and_did_not_play(recorded: Recorded) -> None:
    box = norm.normalize_box_score(
        recorded.payload(f"boxscoretraditionalv3__{MODERN_GAME}.json"), "traditional"
    )
    home = box.home.players
    assert sum(1 for line in home if line["started"]) == 5
    dnp = [line for line in home if line["did_not_play"]]
    assert len(dnp) == 1
    assert dnp[0]["minutes"] is None and dnp[0]["started"] is False


def test_normalize_box_score_rejects_an_unknown_view() -> None:
    with pytest.raises(ValueError, match="traditional"):
        norm.normalize_box_score({}, "tracking")


def test_normalize_box_score_survives_an_empty_payload() -> None:
    box = norm.normalize_box_score({}, "traditional")
    assert box.game_id is None and box.players() == []
    assert box.home.team_id is None and box.away.team_id is None


def test_v2_and_v3_describe_the_same_game(recorded: Recorded) -> None:
    """The two dialects must land on identical columns *and* identical values.

    This is the whole point of the translation layer: a game ingested per-match
    through the V3 box score and the same game re-pulled in bulk through the V2
    logs during the nightly correction pass must not disagree, or every
    correction run would rewrite the season and bump ``sync_version`` forever.
    """
    base = norm.normalize_player_game_logs(
        recorded.payload(
            "playergamelogs__2025-26__regular-season__base__2026-01-02__2026-01-02.json"
        ),
        "Base",
    )
    adv = norm.normalize_player_game_logs(
        recorded.payload(
            "playergamelogs__2025-26__regular-season__advanced__2026-01-02__2026-01-02.json"
        ),
        "Advanced",
    )
    team_log = norm.normalize_league_game_log(
        recorded.payload(
            "leaguegamelog__2025-26__regular-season__t__2026-01-02__2026-01-02.json"
        )
    )

    for game_id in (MODERN_GAME, SECOND_GAME):
        traditional = norm.normalize_box_score(
            recorded.payload(f"boxscoretraditionalv3__{game_id}.json"), "traditional"
        )
        advanced = norm.normalize_box_score(
            recorded.payload(f"boxscoreadvancedv3__{game_id}.json"), "advanced"
        )
        v3_basic = {
            line["player_id"]: line
            for line in traditional.players()
            if line["minutes"] is not None
        }
        v2_basic = {row["player_id"]: row for row in base if row["game_id"] == game_id}
        assert set(v3_basic) == set(v2_basic)
        for player_id, line in v3_basic.items():
            for column in daily._BASIC_COLUMNS:
                assert line[column] == pytest.approx(v2_basic[player_id][column]), (
                    game_id,
                    player_id,
                    column,
                )

        v3_adv = {line["player_id"]: line for line in advanced.players()}
        v2_adv = {row["player_id"]: row for row in adv if row["game_id"] == game_id}
        assert set(v3_adv) == set(v2_adv)
        for player_id, line in v3_adv.items():
            for column in daily._ADVANCED_COLUMNS:
                assert line[column] == pytest.approx(v2_adv[player_id][column]), (
                    game_id,
                    player_id,
                    column,
                )

        v3_team = {side.team_id: side.stats for side in traditional.teams}
        v2_team = {row["team_id"]: row for row in team_log if row["game_id"] == game_id}
        assert set(v3_team) == set(v2_team)
        for team_id, stats in v3_team.items():
            for column in daily._TEAM_COLUMNS:
                assert stats[column] == pytest.approx(v2_team[team_id][column]), (
                    game_id,
                    team_id,
                    column,
                )


#: The only keys a normalizer may emit that are *not* a column somewhere in
#: ``models.py``. Each is a piece of upstream identity the caller consumes and
#: throws away: the abbreviations and display names it resolves to ids, the
#: ``MATCHUP``/``WL`` strings it derives ``is_home`` and ``won`` from, and the two
#: flags — ``did_not_play``, ``comment`` — that decide whether a line is written
#: at all. Anything outside this list has to be a real column: a typo in a column
#: map otherwise produces a key nothing writes and a column that stays NULL forever.
TRANSPORT_KEYS = frozenset(
    {
        "comment",
        "did_not_play",
        "games_played_flag",
        "matchup",
        "name_initial",
        "opponent_abbr",
        "player_code",
        "player_name",
        "player_slug",
        "team_abbr",
        "team_city",
        "team_name",
        "team_nickname",
        "wl",
    }
)

_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


def schema_vocabulary() -> set[str]:
    """Every column name in the Hardwood schema, across every table."""
    return {
        column.key
        for mapper in Base.registry.mappers
        for column in mapper.class_.__table__.columns
    }


@pytest.mark.parametrize(
    ("normalizer", "payload_name", "model", "written"),
    [
        (
            lambda p: norm.normalize_league_game_log(p),
            "leaguegamelog__2025-26__regular-season__t__2026-01-02__2026-01-02.json",
            TeamGame,
            daily._TEAM_COLUMNS,
        ),
        (
            lambda p: norm.normalize_player_game_logs(p, "Base"),
            "playergamelogs__2025-26__regular-season__base__2026-01-02__2026-01-02.json",
            PlayerGameBasic,
            daily._BASIC_COLUMNS,
        ),
        (
            lambda p: norm.normalize_player_game_logs(p, "Advanced"),
            "playergamelogs__2025-26__regular-season__advanced__2026-01-02__2026-01-02.json",
            PlayerGameAdvanced,
            daily._ADVANCED_COLUMNS,
        ),
        (
            lambda p: [
                line
                for line in norm.normalize_box_score(p, "traditional").players()
                if not line["did_not_play"]
            ],
            f"boxscoretraditionalv3__{MODERN_GAME}.json",
            PlayerGameBasic,
            daily._BASIC_COLUMNS,
        ),
        (
            lambda p: norm.normalize_box_score(p, "advanced").players(),
            f"boxscoreadvancedv3__{MODERN_GAME}.json",
            PlayerGameAdvanced,
            daily._ADVANCED_COLUMNS,
        ),
        (
            lambda p: [side.stats for side in norm.normalize_box_score(p, "traditional").teams],
            f"boxscoretraditionalv3__{MODERN_GAME}.json",
            TeamGame,
            daily._TEAM_COLUMNS,
        ),
        (
            lambda p: norm.normalize_scoreboard(p, game_date=SLATE_DATE),
            "scoreboardv2__2026-01-02.json",
            Game,
            ("game_date", "season", "season_type", "home_team_id", "away_team_id", "status"),
        ),
    ],
    ids=[
        "v2-leaguegamelog",
        "v2-playergamelogs-base",
        "v2-playergamelogs-advanced",
        "v3-boxscore-traditional-players",
        "v3-boxscore-advanced-players",
        "v3-boxscore-traditional-teams",
        "v2-scoreboard",
    ],
)
def test_normalized_keys_are_snake_case_schema_columns(
    recorded: Recorded,
    normalizer: Any,
    payload_name: str,
    model: type[Base],
    written: tuple[str, ...],
) -> None:
    """Both dialects have to land on ``models.py``'s vocabulary, exactly.

    V2 speaks ``UPPER_SNAKE_CASE`` and V3 ``camelCase``; the store speaks
    ``snake_case``. This asserts three things at once: every emitted key is
    snake_case, every emitted key is a real schema column (or one of the declared
    transport keys above), and every column the writer will reach for is both
    present in the row and a column of the table it is about to be written to.
    """
    rows = normalizer(recorded.payload(payload_name))
    assert rows, payload_name
    vocabulary = schema_vocabulary()
    target = stat_columns(model)

    for row in rows:
        for key in row:
            assert _SNAKE_CASE.match(key), f"{key!r} is not snake_case"
            assert key in vocabulary or key in TRANSPORT_KEYS, (
                f"{key!r} is neither a Hardwood column nor a declared transport key"
            )
        for column in written:
            assert column in target, f"{column} is not a {model.__tablename__} column"
            assert column in row, f"{payload_name} produced no {column}"


def test_every_column_the_ingest_writes_exists_on_its_model() -> None:
    """The other direction: ``daily``'s write lists must not name a phantom column."""
    assert set(daily._BASIC_COLUMNS) <= stat_columns(PlayerGameBasic)
    assert set(daily._ADVANCED_COLUMNS) <= stat_columns(PlayerGameAdvanced)
    assert set(daily._TEAM_COLUMNS) <= stat_columns(TeamGame)
    # ...and each of them is actually produced by both dialects, so a per-game
    # ingest and a bulk re-pull fill the same set of columns.
    v3_traditional = set(norm.BOX_SCORE_TRADITIONAL_V3_COLUMNS.values())
    v3_advanced = set(norm.BOX_SCORE_ADVANCED_V3_COLUMNS.values())
    assert set(daily._BASIC_COLUMNS) <= v3_traditional
    assert set(daily._BASIC_COLUMNS) <= set(norm.PLAYER_GAME_LOGS_BASE_COLUMNS.values())
    assert set(daily._ADVANCED_COLUMNS) <= v3_advanced
    assert set(daily._ADVANCED_COLUMNS) <= set(
        norm.PLAYER_GAME_LOGS_ADVANCED_COLUMNS.values()
    )
    assert set(daily._TEAM_COLUMNS) <= v3_traditional
    assert set(daily._TEAM_COLUMNS) <= set(norm.LEAGUE_GAME_LOG_COLUMNS.values())


# --------------------------------------------------------------------------- #
# Client: politeness, fixtures, and the absent optional dependency
# --------------------------------------------------------------------------- #


def test_the_ingest_package_exports_only_modules_it_ships() -> None:
    """``__all__`` must not name a module that is not there.

    A star-import checks every name in ``__all__`` and raises ``AttributeError``
    on the first one it cannot resolve — so one aspirational entry (the scheduler
    entry point, built separately) takes the whole package's star-import down
    rather than simply offering one name fewer.
    """
    import nbastats.ingest as package

    assert package.__all__, "the package exports nothing at all"
    for name in package.__all__:
        module = importlib.import_module(f"nbastats.ingest.{name}")
        assert module is not None

    namespace: dict[str, Any] = {}
    exec("from nbastats.ingest import *", namespace)  # noqa: S102 - that is the test
    assert set(package.__all__) <= set(namespace)
    assert {"client", "normalize", "daily", "aggregate", "backfill"} <= set(package.__all__)


def test_fixture_name_slugs_the_call() -> None:
    assert fixture_name("scoreboardv2", ["2026-01-02"]) == "scoreboardv2__2026-01-02.json"
    assert (
        fixture_name("leaguegamelog", ["2025-26", "Regular Season", "T", None, ""])
        == "leaguegamelog__2025-26__regular-season__t.json"
    )
    assert fixture_name("boxscoreadvancedv3") == "boxscoreadvancedv3.json"


def test_fixture_mode_makes_no_live_calls(client: StatsClient) -> None:
    client.scoreboard(SLATE_DATE)
    client.box_score_traditional(MODERN_GAME)
    assert client.fixture_mode is True
    assert client.stats.live_calls == 0
    assert client.stats.fixture_calls == 2
    assert client.stats.retries == 0 and client.stats.failures == 0
    assert "mode=fixture" in client.describe()
    assert client.stats.as_dict()["byEndpoint"] == {
        "scoreboardv2": 1,
        "boxscoretraditionalv3": 1,
    }


def test_missing_fixture_names_both_paths_it_looked_for(tmp_path: Path) -> None:
    """A fixture-mode miss must fail loudly, never fall through to the network."""
    empty = StatsClient(fixtures_dir=tmp_path, min_delay=0.0, sleeper=_unexpected_sleep)
    with pytest.raises(FixtureMissing) as raised:
        empty.box_score_traditional("0022599999")
    message = str(raised.value)
    assert "boxscoretraditionalv3__0022599999.json" in message
    assert "boxscoretraditionalv3.json" in message
    assert str(tmp_path) in message


def test_endpoint_wide_fixture_is_the_fallback(client: StatsClient) -> None:
    """``scoreboardv2.json`` stands in for every date nobody recorded."""
    slate = norm.normalize_scoreboard(client.scoreboard(date(2025, 12, 31)))
    assert slate == []


def test_polite_get_retries_then_succeeds() -> None:
    attempts: list[int] = []
    slept: list[float] = []
    limiter = RateLimiter(0.0, sleeper=slept.append)

    def flaky() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise TimeoutError("stats.nba.com hung")
        return "payload"

    assert polite_get(flaky, retries=5, limiter=limiter, rng=random.Random(7)) == "payload"
    assert len(attempts) == 3
    assert limiter.stats.retries == 2 and limiter.stats.failures == 2
    # Exponential: 2**0 and 2**1, each plus up to a second of jitter.
    assert len(slept) == 2
    assert 1.0 <= slept[0] < 2.0
    assert 2.0 <= slept[1] < 3.0
    assert slept[1] > slept[0]


def test_polite_get_gives_up_after_the_configured_retries() -> None:
    attempts: list[int] = []
    slept: list[float] = []
    limiter = RateLimiter(0.0, sleeper=slept.append)

    def always_fails() -> None:
        attempts.append(1)
        raise ConnectionResetError("dropped")

    with pytest.raises(UpstreamUnavailable) as raised:
        polite_get(always_fails, retries=4, limiter=limiter, rng=random.Random(7))

    assert len(attempts) == 4, "exactly `retries` attempts, no more and no fewer"
    assert len(slept) == 3, "no backoff after the final attempt"
    assert limiter.stats.failures == 4 and limiter.stats.retries == 3
    assert isinstance(raised.value.__cause__, ConnectionResetError)
    # The message has to name the datacenter-IP block: on a cloud host that is
    # what this exception actually means, and retrying will never fix it.
    assert "residential" in str(raised.value)
    assert "NBA_API_PROXY" in str(raised.value)


def test_polite_get_does_not_retry_an_ingest_error() -> None:
    """A missing fixture cannot succeed on a second attempt, so it is not retried."""
    attempts: list[int] = []

    def missing() -> None:
        attempts.append(1)
        raise FixtureMissing("no recorded payload")

    with pytest.raises(FixtureMissing):
        polite_get(missing, retries=5, limiter=RateLimiter(0.0, sleeper=_unexpected_sleep))
    assert attempts == [1]


def test_polite_get_rejects_a_non_positive_retry_budget() -> None:
    with pytest.raises(ValueError, match="retries must be >= 1"):
        polite_get(lambda: None, retries=0)


def test_rate_limiter_enforces_the_minimum_gap() -> None:
    """One request per second, asserted on an injected clock rather than by waiting."""
    now = [100.0]
    slept: list[float] = []

    def clock() -> float:
        return now[0]

    def sleeper(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(1.0, sleeper=sleeper, clock=clock)
    assert limiter.wait() == 0.0, "the first request is never delayed"
    now[0] += 0.25
    assert limiter.wait() == pytest.approx(0.75)
    now[0] += 5.0
    assert limiter.wait() == 0.0, "a slow request pays for its own pacing"
    assert slept == [pytest.approx(0.75)]
    assert limiter.stats.throttled_seconds == pytest.approx(0.75)


def test_live_call_without_nba_api_raises_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ImportError traceback: the message says what to install and what else works."""

    def no_nba_api(name: str, *args: Any, **kwargs: Any) -> Any:
        raise ImportError(f"No module named {name!r}")

    monkeypatch.setattr(importlib, "import_module", no_nba_api)
    live = StatsClient(min_delay=0.0, sleeper=_unexpected_sleep)
    assert live.fixture_mode is False

    with pytest.raises(IngestUnavailable) as raised:
        live.scoreboard(SLATE_DATE)

    message = str(raised.value)
    assert "nba_api" in message and "pip install nba_api" in message
    assert client_module.FIXTURES_ENV_VAR in message
    assert isinstance(raised.value.__cause__, ImportError)
    # Importing the module never needs nba_api; only a live call does.
    assert client_module.nba_api_available() in (True, False)


def test_fixtures_env_var_switches_the_whole_client(
    monkeypatch: pytest.MonkeyPatch, recorded: Recorded
) -> None:
    monkeypatch.setenv(client_module.FIXTURES_ENV_VAR, str(recorded.path))
    api = StatsClient(min_delay=0.0, sleeper=_unexpected_sleep)
    assert api.fixture_mode is True
    assert api.scoreboard(SLATE_DATE)["resultSets"]


def test_rate_limit_stats_summarise_a_run() -> None:
    stats = RateLimitStats()
    stats.record("scoreboardv2", live=True, seconds=0.5)
    stats.record("scoreboardv2", live=False, seconds=0.1)
    summary = stats.as_dict()
    assert summary["calls"] == 2
    assert summary["liveCalls"] == 1 and summary["fixtureCalls"] == 1
    assert summary["byEndpoint"] == {"scoreboardv2": 2}


# --------------------------------------------------------------------------- #
# Polling and the per-game path
# --------------------------------------------------------------------------- #


def test_poll_writes_live_state_and_reports_only_new_finals(
    db: Session, client: StatsClient, recorded: Recorded
) -> None:
    newly_final = daily.poll_finalized_games(db, SLATE_DATE, client=client)
    assert newly_final == [MODERN_GAME]

    final = db.get(Game, MODERN_GAME)
    live = db.get(Game, SECOND_GAME)
    assert final.status == "final" and final.finalized_at is not None
    assert final.clock is None and (final.home_pts, final.away_pts) == (118, 112)
    assert live.status == "live" and live.clock == "4:21" and live.period == 3
    assert live.finalized_at is None
    assert live.game_date == SLATE_DATE and live.season == CURRENT_SEASON

    # Polling again changes nothing and reports nothing: the diff is against what
    # we stored, which is what keeps the per-game box-score calls down to one pair.
    assert daily.poll_finalized_games(db, SLATE_DATE, client=client) == []

    # The second game goes final between polls.
    recorded.apply("complete")
    assert daily.poll_finalized_games(db, SLATE_DATE, client=client) == [SECOND_GAME]
    reloaded = db.get(Game, SECOND_GAME)
    assert reloaded.status == "final" and reloaded.clock is None
    assert (reloaded.home_pts, reloaded.away_pts) == (99, 103)
    assert reloaded.finalized_at is not None


def test_poll_never_bumps_sync_version(db: Session, client: StatsClient) -> None:
    """Only an ingested game counts. A poll that finds a live game publishes nothing."""
    assert read_sync_state(db).sync_version == 0
    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    assert read_sync_state(db).sync_version == 0


def test_ingest_game_twice_writes_the_same_rows(db: Session, client: StatsClient) -> None:
    """Upsert, not insert: a restarted worker must not duplicate a night's work."""
    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    first = daily.ingest_game(db, MODERN_GAME, client=client)
    assert first.changed is True and first.first_ingest is True
    assert (first.player_rows, first.advanced_rows, first.team_rows) == (16, 16, 2)

    before = {
        model: snapshot(db, model)
        for model in (PlayerGameBasic, PlayerGameAdvanced, TeamGame, PlayerSeason, TeamSeason)
    }
    # ``ingested_at`` is a timestamp of the pass, not of the data, so it moves.
    games_before = snapshot(db, Game, ignore=("ingested_at",))

    second = daily.ingest_game(db, MODERN_GAME, client=client)
    assert second.changed is False and second.first_ingest is False
    assert (second.player_rows, second.advanced_rows, second.team_rows) == (16, 16, 2)

    for model, rows in before.items():
        assert snapshot(db, model) == rows, model.__tablename__
    assert snapshot(db, Game, ignore=("ingested_at",)) == games_before
    assert len(db.execute(select(PlayerGameBasic)).scalars().all()) == 16


def test_sync_version_bumps_exactly_once_per_finalized_game(
    db: Session, client: StatsClient, recorded: Recorded
) -> None:
    """CONTRACT.md §8, the behaviour the whole per-match feature rests on.

    The client's poll of ``/v1/sync`` must see one increment per game that lands —
    not one per row written, not one per night, and never one for a game that was
    re-read and turned out unchanged.
    """
    assert read_sync_state(db).sync_version == 0

    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    assert read_sync_state(db).sync_version == 0, "polling publishes nothing"

    first = daily.ingest_game(db, MODERN_GAME, client=client)
    assert first.sync_version == 1
    assert read_sync_state(db).sync_version == 1

    # Re-reading an unchanged game must not inflate the counter, or a restarted
    # worker would make every client refetch the world.
    for _ in range(3):
        repeat = daily.ingest_game(db, MODERN_GAME, client=client)
        assert repeat.changed is False
        assert repeat.sync_version == 1
    assert read_sync_state(db).sync_version == 1

    # The second game on the same slate finishes: exactly one more increment.
    recorded.apply("complete")
    assert daily.poll_finalized_games(db, SLATE_DATE, client=client) == [SECOND_GAME]
    assert read_sync_state(db).sync_version == 1
    second = daily.ingest_game(db, SECOND_GAME, client=client)
    assert second.sync_version == 2
    assert read_sync_state(db).sync_version == 2

    state = read_sync_state(db)
    assert state.games_ingested == 2, "one counted game per first ingest"
    assert state.sync_version == state.games_ingested


def test_a_correction_bumps_sync_version_once_more(
    db: Session, client: StatsClient, recorded: Recorded
) -> None:
    """A revised box score is a change the client has to learn about — exactly once."""
    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    daily.ingest_game(db, MODERN_GAME, client=client)
    assert read_sync_state(db).sync_version == 1

    recorded.apply("corrected")
    corrected = daily.ingest_game(db, MODERN_GAME, client=client)
    assert corrected.changed is True and corrected.first_ingest is False
    assert corrected.sync_version == 2
    # ...and re-reading the corrected game settles again.
    assert daily.ingest_game(db, MODERN_GAME, client=client).sync_version == 2
    assert read_sync_state(db).games_ingested == 1, "a correction is not a new game"


def test_correction_overwrites_the_changed_line_instead_of_duplicating_it(
    db: Session, client: StatsClient, recorded: Recorded
) -> None:
    """The league reassigns an assist days later; the stored row has to follow it."""
    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    daily.ingest_game(db, MODERN_GAME, client=client)

    def line() -> PlayerGameBasic:
        rows = (
            db.execute(
                select(PlayerGameBasic).where(
                    PlayerGameBasic.game_id == MODERN_GAME,
                    PlayerGameBasic.player_id == CORRECTED_PLAYER,
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, f"the correction duplicated the row ({len(rows)} found)"
        return rows[0]

    original = line()
    assert (original.ast, original.tov) == (11, 3)
    rows_before = len(db.execute(select(PlayerGameBasic)).scalars().all())
    team_before = db.execute(
        select(TeamGame).where(
            TeamGame.game_id == MODERN_GAME, TeamGame.team_id == original.team_id
        )
    ).scalar_one()
    assert team_before.ast == 33

    recorded.apply("corrected")
    result = daily.run_correction_window(db, days=1, client=client, end_date=SLATE_DATE)
    assert len(result) == 1 and result[0].changed_rows > 0

    corrected = line()
    assert (corrected.ast, corrected.tov) == (12, 2)
    assert corrected.pts == original.pts, "only the revised fields move"
    assert len(db.execute(select(PlayerGameBasic)).scalars().all()) == rows_before
    team_after = db.execute(
        select(TeamGame).where(
            TeamGame.game_id == MODERN_GAME, TeamGame.team_id == original.team_id
        )
    ).scalar_one()
    assert team_after.ast == 34 and team_after.tov == 13

    # The season aggregate follows the correction rather than keeping the old total.
    season_row = db.execute(
        select(PlayerSeason).where(PlayerSeason.player_id == CORRECTED_PLAYER)
    ).scalar_one()
    assert season_row.ast_tot == 12

    # The run is recorded for the operator, and re-running it changes nothing.
    logs = db.execute(select(IngestLog).where(IngestLog.job == "correction")).scalars().all()
    assert logs and logs[-1].status == "success"
    settled = daily.run_correction_window(db, days=1, client=client, end_date=SLATE_DATE)
    assert settled[0].changed_rows == 0


def test_correction_window_defaults_to_the_configured_span(
    db: Session, client: StatsClient
) -> None:
    """Three days back by default, and a dark day is a no-op rather than an error."""
    results = daily.run_correction_window(db, client=client, end_date=SLATE_DATE)
    assert [result.game_date for result in results] == [
        date(2025, 12, 31),
        date(2026, 1, 1),
        SLATE_DATE,
    ]
    assert results[0].games == 0 and results[1].games == 0
    assert results[-1].games == 2
    assert daily.run_correction_window(db, days=0, client=client) == []


def test_ingest_day_uses_only_the_bulk_endpoints(db: Session, client: StatsClient) -> None:
    """Three requests for a whole slate, never two per game — see DATA_SOURCES §5."""
    result = daily.ingest_day(db, SLATE_DATE, client=client)
    assert result.games == 2 and result.finalized == [MODERN_GAME]
    called = client.stats.by_endpoint
    assert set(called) == {"scoreboardv2", "leaguegamelog", "playergamelogs"}
    assert called["scoreboardv2"] == 1
    assert called["leaguegamelog"] == 1
    assert called["playergamelogs"] == 2, "one call per measure type, not per player"
    assert not any(key.startswith("boxscore") for key in called)
    # Only the final game's rows are written; the live one has no box score yet.
    assert {row.game_id for row in db.execute(select(PlayerGameBasic)).scalars()} == {
        MODERN_GAME
    }


def test_data_through_moves_only_when_the_whole_slate_is_final(
    db: Session, client: StatsClient, recorded: Recorded
) -> None:
    """A half-finished night must never look complete, however many games landed."""
    first = daily.ingest_day(db, SLATE_DATE, client=client)
    assert first.data_through_moved is False
    assert read_sync_state(db).data_through is None

    recorded.apply("complete")
    second = daily.ingest_day(db, SLATE_DATE, client=client)
    assert second.data_through_moved is True
    assert read_sync_state(db).data_through == SLATE_DATE

    # The cursor only ever moves forward.
    assert daily.move_data_through(db, date(2025, 12, 20)) is False
    assert read_sync_state(db).data_through == SLATE_DATE
    # A date with no games at all is not a complete day.
    assert daily.move_data_through(db, date(2026, 6, 1)) is False


def test_ingest_game_writes_reference_rows_and_the_game(
    db: Session, client: StatsClient
) -> None:
    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    daily.ingest_game(db, MODERN_GAME, client=client)

    teams = {team.team_id: team for team in db.execute(select(Team)).scalars()}
    assert len(teams) == 2
    assert {team.abbr for team in teams.values()} == {"LAL", "BOS"}
    assert teams[1610612747].name == "Los Angeles Lakers"

    player = db.get(Player, CORRECTED_PLAYER)
    assert player.full_name == "Marcus Vale" and player.jersey == "23"

    game = db.get(Game, MODERN_GAME)
    assert game.status == "final" and game.data_source == daily.LIVE_SOURCE
    assert game.ingested_at is not None and game.clock is None
    assert (game.home_team_id, game.away_team_id) == (1610612747, 1610612738)


def test_a_dnp_produces_no_line_at_all(db: Session, client: StatsClient) -> None:
    """A row of zeros would render in the app as a bad night, not as "did not play"."""
    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    daily.ingest_game(db, MODERN_GAME, client=client)
    written = {row.player_id for row in db.execute(select(PlayerGameBasic)).scalars()}
    assert 1000109 not in written, "the DNP was stored"
    assert len(written) == 16


def test_ingest_game_refuses_to_invent_a_date(db: Session, client: StatsClient) -> None:
    """A V3 box score carries no scheduling day, and guessing one corrupts every query."""
    with pytest.raises(ValueError, match="No date known"):
        daily.ingest_game(db, MODERN_GAME, client=client)


# --------------------------------------------------------------------------- #
# Era honesty
# --------------------------------------------------------------------------- #


def test_a_pre_1997_game_never_produces_an_advanced_row(
    db: Session, client: StatsClient
) -> None:
    """Possession data was not recorded before 1996-97; there is nothing to store."""
    result = daily.ingest_game(
        db, PRE_ADVANCED_GAME, client=client, game_date=PRE_ADVANCED_DATE
    )
    assert result.season == PRE_ADVANCED_SEASON
    assert result.advanced_rows == 0
    assert result.skipped == f"advanced box unavailable before {daily.ADVANCED_FROM_SEASON}"
    assert db.execute(select(PlayerGameAdvanced)).scalars().all() == []
    assert result.player_rows == 14

    # The advanced box-score endpoint is never even asked for.
    assert "boxscoreadvancedv3" not in client.stats.by_endpoint

    assert daily.season_has_advanced(PRE_ADVANCED_SEASON) is False
    assert daily.season_has_advanced("1996-97") is True
    assert daily.season_has_advanced(None) is False
    assert daily.season_has_advanced("not a season") is False


def test_a_pre_1997_game_writes_null_not_zero(db: Session, client: StatsClient) -> None:
    """The contract's hard rule: an era-unavailable stat is NULL, never 0."""
    daily.ingest_game(db, PRE_ADVANCED_GAME, client=client, game_date=PRE_ADVANCED_DATE)

    lines = db.execute(select(PlayerGameBasic)).scalars().all()
    assert lines
    for line in lines:
        assert line.plus_minus is None, "1995-96 has no plus/minus; 0 would be a lie"
        assert line.pts is not None and line.minutes is not None
        # Stats the era *did* record are still there.
        assert line.stl is not None and line.tov is not None

    for side in db.execute(select(TeamGame)).scalars():
        assert side.off_rtg is None and side.def_rtg is None
        assert side.pace is None and side.poss is None
        assert side.plus_minus is None
        # Counting stats and the plain shooting rates the era recorded are written.
        assert side.pts is not None and side.fg_pct is not None
        assert side.ftr is not None and side.pps is not None
        assert side.won is True or side.won is False

    # The boundary comes from the catalog, not from this test: it is the one place
    # the rules live, and `contracts/metrics.json` is the binding statement of them.
    assert catalog.metric_availability("off_rtg", PRE_ADVANCED_SEASON, "game") == "unavailable"
    assert catalog.metric_availability("plus_minus", PRE_ADVANCED_SEASON, "game") == "unavailable"
    assert catalog.metric_availability("pts", PRE_ADVANCED_SEASON, "game") == "full"
    assert catalog.metric_availability("ftr", PRE_ADVANCED_SEASON, "game") == "full"


def test_per_game_shooting_rates_follow_the_catalogs_own_boundary(
    db: Session, client: StatsClient
) -> None:
    """eFG% and TS% are pure box-score formulas, yet they have a *per-game* boundary.

    ``contracts/metrics.json`` declares ``perGameFrom: 1996-97`` for them because the
    stored per-game value lives in ``player_game_advanced``, a table that does not
    exist for an older game; the API recomputes them from ``player_game_basic``
    instead. The ingest path must honour that boundary rather than second-guess it,
    or the store and ``/v1/meta.coverage`` would disagree about the same season.
    """
    assert catalog.metric_availability("efg_pct", PRE_ADVANCED_SEASON, "game") == "unavailable"
    assert catalog.metric_availability("efg_pct", PRE_ADVANCED_SEASON, "season") == "full"
    assert catalog.metric_availability("efg_pct", CURRENT_SEASON, "game") == "full"

    daily.ingest_game(db, PRE_ADVANCED_GAME, client=client, game_date=PRE_ADVANCED_DATE)
    for side in db.execute(select(TeamGame)).scalars():
        assert side.efg_pct is None and side.ts_pct is None
        assert side.oreb_pct is None and side.tov_pct is None

    # The same columns are populated for a modern game.
    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    daily.ingest_game(db, MODERN_GAME, client=client)
    modern = db.execute(
        select(TeamGame).where(TeamGame.game_id == MODERN_GAME)
    ).scalars().all()
    assert modern
    for side in modern:
        assert side.efg_pct is not None and side.ts_pct is not None
        assert side.oreb_pct is not None and side.opp_efg_pct is not None
        assert side.off_rtg is not None and side.pace is not None

    # The season row for the old season still carries them, marked estimated.
    old_season = db.execute(
        select(TeamSeason).where(TeamSeason.season == PRE_ADVANCED_SEASON)
    ).scalars().all()
    assert old_season
    for row in old_season:
        assert row.efg_pct is not None and 0.0 <= row.efg_pct <= 1.0
        assert row.is_estimated is True


def test_pre_1997_season_rows_are_flagged_estimated(
    db: Session, client: StatsClient
) -> None:
    """Box-score derivations are not measurements, and the app badges them as such."""
    daily.ingest_game(db, PRE_ADVANCED_GAME, client=client, game_date=PRE_ADVANCED_DATE)
    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    daily.ingest_game(db, MODERN_GAME, client=client)

    old = db.execute(
        select(PlayerSeason).where(PlayerSeason.season == PRE_ADVANCED_SEASON)
    ).scalars().all()
    modern = db.execute(
        select(PlayerSeason).where(PlayerSeason.season == CURRENT_SEASON)
    ).scalars().all()
    assert old and modern
    assert all(row.is_estimated is True for row in old)
    assert all(row.is_estimated is False for row in modern)

    for row in old:
        assert row.off_rtg is None and row.pace is None
        assert row.usg_pct is not None, "USG% is a box-score derivation, so it survives"
        assert row.ts_pct is not None and 0.0 <= row.ts_pct <= 1.0
    assert catalog.metric_availability("usg_pct", PRE_ADVANCED_SEASON, "season") == "estimated"


def test_era_plan_reads_the_catalog_not_a_hand_written_year_list() -> None:
    unavailable_old, estimated_old = aggregate.era_plan(
        "player_game_advanced", PRE_ADVANCED_SEASON, "game"
    )
    assert set(daily._ADVANCED_COLUMNS) <= unavailable_old
    unavailable_new, estimated_new = aggregate.era_plan(
        "player_game_advanced", CURRENT_SEASON, "game"
    )
    assert unavailable_new == frozenset()
    assert estimated_new is False

    values = {column: 1.0 for column in daily._ADVANCED_COLUMNS}
    aggregate.apply_era(values, unavailable_old)
    assert set(values.values()) == {None}


def test_percentages_crossing_into_the_store_are_fractions(
    db: Session, client: StatsClient
) -> None:
    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    daily.ingest_game(db, MODERN_GAME, client=client)
    for row in db.execute(select(PlayerGameAdvanced)).scalars():
        for column in ("ts_pct", "efg_pct", "usg_pct", "ast_pct", "reb_pct", "tov_pct"):
            value = getattr(row, column)
            assert value is None or 0.0 <= value <= 1.0, column
    for side in db.execute(select(TeamGame)).scalars():
        for column in ("fg_pct", "fg3_pct", "ft_pct", "efg_pct", "ts_pct", "oreb_pct"):
            value = getattr(side, column)
            assert value is None or 0.0 <= value <= 1.0, column


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

_DERIVED_TABLES = (PlayerSeason, TeamSeason, LeagueSeason, ShotZoneSeason)


def test_aggregate_is_idempotent(db: Session, client: StatsClient) -> None:
    """Run it twice, diff every derived row: they must be identical, and report zero.

    Not a nicety — the league issues stat corrections, so the aggregate has to be
    rebuildable. If a rebuild reported spurious changes, every nightly run would
    bump ``sync_version`` and every client would refetch the world.
    """
    daily.ingest_day(db, SLATE_DATE, client=client)
    daily.ingest_game(db, PRE_ADVANCED_GAME, client=client, game_date=PRE_ADVANCED_DATE)

    seasons = [(CURRENT_SEASON, "Regular Season"), (PRE_ADVANCED_SEASON, "Regular Season")]
    first = aggregate.recompute_seasons(db, seasons)
    db.flush()
    before = {model: snapshot(db, model) for model in _DERIVED_TABLES}
    assert before[PlayerSeason] and before[TeamSeason] and before[LeagueSeason]

    second = aggregate.recompute_seasons(db, seasons)
    db.flush()
    after = {model: snapshot(db, model) for model in _DERIVED_TABLES}

    for model in _DERIVED_TABLES:
        assert after[model] == before[model], model.__tablename__
    assert sum(report.changed for report in second) == 0
    assert [report.player_seasons for report in second] == [
        report.player_seasons for report in first
    ]


def test_recompute_seasons_visits_each_pair_once(db: Session, client: StatsClient) -> None:
    daily.ingest_day(db, SLATE_DATE, client=client)
    pairs = [(CURRENT_SEASON, "Regular Season")] * 3 + [("", "Regular Season")]
    assert len(aggregate.recompute_seasons(db, pairs)) == 1


def test_upsert_reports_a_change_only_when_something_changed(db: Session) -> None:
    keys = {"team_id": 1610612747}
    assert aggregate.upsert(db, Team, keys, {"abbr": "LAL", "name": "Lakers", "city": "LA", "nickname": "Lakers"})
    db.flush()
    assert aggregate.upsert(db, Team, keys, {"abbr": "LAL"}) is False
    assert aggregate.upsert(db, Team, keys, {"abbr": "LAK"}) is True
    db.flush()
    assert db.get(Team, 1610612747).abbr == "LAK"
    # A float re-derived from the same inputs is not a change.
    assert aggregate.upsert(db, Team, keys, {"year_founded": 1947}) is True
    db.flush()
    assert aggregate.upsert(db, Team, keys, {"year_founded": 1947.0}) is False
    # Columns this model does not have are dropped, not raised on.
    assert aggregate.upsert(db, Team, keys, {"not_a_column": 1}) is False


def test_delete_stale_drops_only_what_the_season_no_longer_produces(
    db: Session, client: StatsClient
) -> None:
    daily.ingest_day(db, SLATE_DATE, client=client)
    aggregate.recompute_season(db, CURRENT_SEASON, "Regular Season")
    db.flush()
    kept = {
        (row.player_id, row.team_id)
        for row in db.execute(select(PlayerSeason)).scalars()
    }
    assert kept

    removed = aggregate.delete_stale(
        db,
        PlayerSeason,
        {"season": CURRENT_SEASON, "season_type": "Regular Season"},
        ("player_id", "team_id"),
        kept,
    )
    assert removed == 0

    dropped = aggregate.delete_stale(
        db,
        PlayerSeason,
        {"season": CURRENT_SEASON, "season_type": "Regular Season"},
        ("player_id", "team_id"),
        set(),
    )
    assert dropped == len(kept)


def test_affected_seasons_maps_game_ids_back_to_their_season(
    db: Session, client: StatsClient
) -> None:
    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    daily.ingest_game(db, MODERN_GAME, client=client)
    daily.ingest_game(db, PRE_ADVANCED_GAME, client=client, game_date=PRE_ADVANCED_DATE)
    assert aggregate.affected_seasons(db, [MODERN_GAME, PRE_ADVANCED_GAME]) == {
        (CURRENT_SEASON, "Regular Season"),
        (PRE_ADVANCED_SEASON, "Regular Season"),
    }
    assert aggregate.affected_seasons(db, []) == set()
    assert sorted(aggregate.season_types_in(db, CURRENT_SEASON)) == ["Regular Season"]


# --------------------------------------------------------------------------- #
# Backfill: the bulk-file path
# --------------------------------------------------------------------------- #

#: The Kaggle "NBA Database" ``game`` table carries both sides of a game on one
#: row, as ``*_home`` / ``*_away`` suffixes.
_KAGGLE_SIDE_COLUMNS = (
    "fgm", "fga", "fg_pct", "fg3m", "fg3a", "fg3_pct", "ftm", "fta", "ft_pct",
    "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf", "pts", "plus_minus",
)


def write_kaggle_file(path: Path) -> Path:
    """A one-game stand-in for Wyatt Walsh's NBA Database, in its real shape.

    The numbers are game ``0022500512``'s, taken from the recorded team log, so
    the rows this produces can be compared against the ones the API path writes.
    """
    columns = [
        "game_id TEXT", "game_date TEXT", "season_id TEXT", "season_type TEXT",
        "team_id_home INTEGER", "team_id_away INTEGER",
        "team_abbreviation_home TEXT", "team_abbreviation_away TEXT",
        "team_name_home TEXT", "team_name_away TEXT",
        "matchup_home TEXT", "wl_home TEXT", "min INTEGER",
    ]
    columns += [f"{column}_home REAL" for column in _KAGGLE_SIDE_COLUMNS]
    columns += [f"{column}_away REAL" for column in _KAGGLE_SIDE_COLUMNS]

    home = (43, 91, 0.473, 13, 37, 0.351, 19, 23, 0.826, 10, 39, 49, 33, 6, 7, 14, 19, 118, 6)
    away = (43, 92, 0.467, 13, 35, 0.371, 13, 19, 0.684, 11, 32, 43, 23, 8, 5, 14, 20, 112, -6)
    row = (
        MODERN_GAME, "2026-01-02 00:00:00", "22025", "Regular Season",
        1610612747, 1610612738, "LAL", "BOS",
        "Los Angeles Lakers", "Boston Celtics", "LAL vs. BOS", "W", 240,
    ) + home + away

    connection = sqlite3.connect(path)
    try:
        connection.execute(f"CREATE TABLE game ({', '.join(columns)})")
        connection.execute(
            f"INSERT INTO game VALUES ({','.join('?' * len(row))})", row
        )
        connection.commit()
    finally:
        connection.close()
    return path


def test_pluck_reads_a_sqlite_row_as_well_as_a_dict(tmp_path: Path) -> None:
    """``sqlite3.Row`` indexes by name but is not a Mapping and has no ``.get()``.

    The Kaggle loader streams through ``sqlite3.Row``; every other loader yields
    plain dicts. ``_pluck`` is the single place that reads a source field, so it
    is the single place that has to tolerate both.
    """
    path = write_kaggle_file(tmp_path / "nba.sqlite")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT * FROM game").fetchone()
    finally:
        connection.close()

    assert not isinstance(row, dict) and not hasattr(row, "get")
    resolved = {"game_id": "game_id", "home_team_id": "team_id_home"}
    assert backfill._pluck(row, resolved, "game_id") == MODERN_GAME
    assert backfill._pluck(row, resolved, "home_team_id") == 1610612747
    assert backfill._pluck(row, resolved, "not_resolved") is None
    assert backfill._pluck(row, {"x": "no_such_column"}, "x") is None
    assert backfill._kaggle_side(row, "home")["pts"] == 118
    assert backfill._kaggle_side(row, "away")["pts"] == 112

    # ...and the dict path, which the CSV and parquet loaders use, is unchanged.
    as_dict = dict(row)
    assert backfill._pluck(as_dict, resolved, "game_id") == MODERN_GAME
    assert backfill._pluck(as_dict, {"x": "no_such_column"}, "x") is None
    assert backfill._kaggle_side(as_dict, "home")["pts"] == 118


def test_kaggle_sqlite_load_is_idempotent_and_resumable(
    db: Session, tmp_path: Path
) -> None:
    """The bulk path writes the same rows the API path would, and only once."""
    path = write_kaggle_file(tmp_path / "nba.sqlite")

    first = backfill.load_kaggle_sqlite(db, path)
    db.flush()
    assert first.rows_read == 1 and first.rows_written == 3  # one game, two sides
    assert first.seasons == [CURRENT_SEASON]

    game = db.get(Game, MODERN_GAME)
    assert game.game_date == SLATE_DATE and game.season == CURRENT_SEASON
    assert (game.home_pts, game.away_pts) == (118, 112)
    assert game.status == "final"

    sides = {row.team_id: row for row in db.execute(select(TeamGame)).scalars()}
    assert set(sides) == {1610612747, 1610612738}
    lakers = sides[1610612747]
    assert lakers.pts == 118 and lakers.opp_pts == 112 and lakers.won is True
    assert lakers.is_home is True
    # Derived the same way the live path derives it, so the two sources agree.
    assert lakers.off_rtg == pytest.approx(112.2527, abs=1e-3)
    assert 0.0 <= lakers.efg_pct <= 1.0

    before = snapshot(db, TeamGame)

    # A finished season is recorded in ingest_log and skipped on a re-run, so an
    # interrupted overnight load continues rather than starting over.
    second = backfill.load_kaggle_sqlite(db, path)
    db.flush()
    assert second.seasons_skipped == [CURRENT_SEASON]
    assert second.rows_written == 0
    assert snapshot(db, TeamGame) == before

    # With resume off it reloads the same season, and still changes nothing.
    # ``ingested_at`` moves on every pass and must not be counted as a change,
    # or a re-load would report the whole season as revised.
    games_before = snapshot(db, Game, ignore=("ingested_at",))
    third = backfill.load_kaggle_sqlite(db, path, resume=False)
    db.flush()
    assert third.rows_read == 1
    assert third.rows_changed == 0
    assert snapshot(db, TeamGame) == before
    assert snapshot(db, Game, ignore=("ingested_at",)) == games_before
    assert db.get(Game, MODERN_GAME).ingested_at is not None


def test_kaggle_load_reports_a_missing_file_clearly(db: Session, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Kaggle SQLite file not found"):
        backfill.load_kaggle_sqlite(db, tmp_path / "absent.sqlite")


def test_season_totals_reconcile_with_the_game_rows(
    db: Session, client: StatsClient
) -> None:
    """The aggregate invents nothing: a one-game season is that game's line."""
    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    daily.ingest_game(db, MODERN_GAME, client=client)

    line = db.execute(
        select(PlayerGameBasic).where(
            PlayerGameBasic.game_id == MODERN_GAME,
            PlayerGameBasic.player_id == CORRECTED_PLAYER,
        )
    ).scalar_one()
    season_row = db.execute(
        select(PlayerSeason).where(PlayerSeason.player_id == CORRECTED_PLAYER)
    ).scalar_one()
    assert season_row.gp == 1
    assert season_row.pts_tot == line.pts and season_row.pts == pytest.approx(line.pts)
    assert season_row.ast_tot == line.ast
    assert season_row.minutes == pytest.approx(line.minutes)

    team_row = db.execute(
        select(TeamSeason).where(TeamSeason.team_id == line.team_id)
    ).scalar_one()
    side = db.execute(
        select(TeamGame).where(
            TeamGame.game_id == MODERN_GAME, TeamGame.team_id == line.team_id
        )
    ).scalar_one()
    assert team_row.gp == 1 and team_row.wins == 1 and team_row.losses == 0
    assert team_row.pts == pytest.approx(side.pts)
    assert team_row.opp_pts == pytest.approx(side.opp_pts)


# --------------------------------------------------------------------------- #
# The bulk path's franchises, commits and aggregate cost
#
# Four defects that only showed up at backfill scale. The correction window is
# the only date-range ingest the CLI has, so `--nightly --days 250` is what a
# season backfill actually runs — and at 250 days each of these turns from a
# curiosity into a broken run.
# --------------------------------------------------------------------------- #


def test_the_bulk_path_writes_the_franchises_it_references(
    db: Session, client: StatsClient
) -> None:
    """``ingest_day`` used to leave every team id dangling.

    ``ensure_team`` is called from ``ingest_game`` — the per-game path — because a box score
    names both sides. ``ingest_day`` reads the league game log instead, writes ``team_game``
    rows keyed by ``team_id``, and had nowhere to learn a franchise from. SQLite does not
    enforce foreign keys by default, so a database walked entirely with ``--once`` looked
    fine locally and violated a constraint the moment it met Postgres.
    """
    daily.ingest_day(db, date(2026, 1, 2), client=client)

    assert db.execute(select(func.count()).select_from(Team)).scalar_one() == 30

    dangling = db.execute(
        select(func.count())
        .select_from(TeamGame)
        .where(~TeamGame.team_id.in_(select(Team.team_id)))
    ).scalar_one()
    assert dangling == 0, "a team_game row points at a franchise that does not exist"


def test_the_franchises_carry_the_two_facts_no_endpoint_returns(
    db: Session, client: StatsClient
) -> None:
    """Conference and division come from the identity map, not from the game log.

    No endpoint on the bulk path returns them, which is the reason the franchises are read
    from ``data/nba_identities.json`` rather than synthesised out of whatever the league game
    log happened to carry.
    """
    daily.ingest_day(db, date(2026, 1, 2), client=client)

    denver = db.execute(select(Team).where(Team.abbr == "DEN")).scalar_one()
    assert (denver.conference, denver.division) == ("West", "Northwest")
    assert denver.name == "Denver Nuggets" and denver.city == "Denver"
    assert denver.year_founded == 1976

    for team in db.execute(select(Team)).scalars():
        assert team.conference in {"East", "West"}, team.abbr
        assert team.division, team.abbr


def test_ensuring_franchises_is_idempotent_and_cheap(db: Session) -> None:
    """It writes once and then does nothing, so a 250-day walk pays for it on day one."""
    assert daily.ensure_franchises(db) == 30
    db.flush()
    assert daily.ensure_franchises(db) == 0
    assert db.execute(select(func.count()).select_from(Team)).scalar_one() == 30


def test_an_interrupted_window_keeps_the_days_it_finished(
    db: Session, client: StatsClient
) -> None:
    """The correction window commits per day, so Ctrl-C costs one day and not the walk.

    This is the difference between a resumable season backfill and half an hour of work
    thrown away on the 250th day. The window used to pass ``commit=False`` to every
    ``ingest_day`` and commit once at the very end.
    """
    real = daily.ingest_day
    attempted: list[date] = []

    def exploding(session: Session, game_date: date, **kwargs: Any) -> Any:
        attempted.append(game_date)
        if len(attempted) == 3:
            raise KeyboardInterrupt("simulated Ctrl-C")
        return real(session, game_date, **kwargs)

    daily.ingest_day = exploding  # type: ignore[assignment]
    try:
        with pytest.raises(KeyboardInterrupt):
            daily.run_correction_window(db, 5, client=client, end_date=date(2026, 1, 6))
    finally:
        daily.ingest_day = real  # type: ignore[assignment]

    assert attempted[0] == date(2026, 1, 2), "the walk starts at the far end of the window"
    # 2026-01-02 is the recorded slate, and it was day one — so its rows must have survived
    # a crash two days later.
    assert db.execute(select(func.count()).select_from(Game)).scalar_one() == 2
    assert db.execute(select(func.count()).select_from(PlayerGameBasic)).scalar_one() > 0


def test_resuming_an_interrupted_window_does_not_duplicate(
    db: Session, client: StatsClient
) -> None:
    """Every write on the path is an upsert, so the obvious recovery is the right one."""
    daily.run_correction_window(db, 5, client=client, end_date=date(2026, 1, 6))
    first = (
        db.execute(select(func.count()).select_from(Game)).scalar_one(),
        db.execute(select(func.count()).select_from(PlayerGameBasic)).scalar_one(),
    )
    daily.run_correction_window(db, 5, client=client, end_date=date(2026, 1, 6))
    second = (
        db.execute(select(func.count()).select_from(Game)).scalar_one(),
        db.execute(select(func.count()).select_from(PlayerGameBasic)).scalar_one(),
    )
    assert first == second and first[0] > 0


def test_the_window_recomputes_aggregates_once_not_once_per_day(
    db: Session, client: StatsClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recomputing a season costs the whole season, so doing it per day is quadratic.

    Nothing reads the aggregates until the walk finishes, so once at the end is both cheaper
    and identical in result.
    """
    calls: list[Any] = []
    real = aggregate.recompute_seasons

    def counted(session: Session, scopes: Any, **kwargs: Any) -> Any:
        calls.append(list(scopes))
        return real(session, scopes, **kwargs)

    monkeypatch.setattr(aggregate, "recompute_seasons", counted)
    daily.run_correction_window(db, 10, client=client, end_date=date(2026, 1, 2))

    assert len(calls) == 1, f"recomputed {len(calls)} times for a 10-day window"
    assert calls[0] == [("2025-26", "Regular Season")]


def test_a_window_over_dates_with_no_games_recomputes_nothing(
    db: Session, client: StatsClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The offseason case: no slate, no scopes, nothing to aggregate."""
    calls: list[Any] = []
    monkeypatch.setattr(
        aggregate, "recompute_seasons", lambda s, scopes, **kw: calls.append(list(scopes))
    )
    daily.run_correction_window(db, 3, client=client, end_date=date(2026, 9, 17))
    assert calls == []
