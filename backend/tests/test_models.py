"""Data-foundation tests: the schema (round-tripping, era-nullability, DDL drift) and the
contract catalog that sits in front of it (formatting, widget-config validation, era rules).
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session

from nbastats import catalog
from nbastats.db import bump_sync_version, read_sync_state
from nbastats.models import (
    PLAYER_GAME_ADVANCED_METRIC_COLUMNS,
    PLAYER_GAME_METRIC_COLUMNS,
    PLAYER_SEASON_PER_GAME_COLUMNS,
    PLAYER_SEASON_RATE_COLUMNS,
    PLAYER_SEASON_TOTALS_COLUMNS,
    TEAM_GAME_METRIC_COLUMNS,
    TEAM_SEASON_PER_GAME_COLUMNS,
    TEAM_SEASON_RATE_COLUMNS,
    TEAM_SEASON_TOTALS_COLUMNS,
    Base,
    Game,
    Player,
    PlayerGameAdvanced,
    PlayerGameBasic,
    PlayerSeason,
    Team,
    render_schema_sql,
    season_column_for,
)

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "nbastats" / "schema.sql"


def test_every_contract_table_exists(seeded_engine: Engine) -> None:
    tables = set(inspect(seeded_engine).get_table_names())
    expected = {
        "teams", "players", "games", "player_game_basic", "player_game_advanced",
        "team_game", "player_season", "team_season", "shot_zone_season", "league_season",
        "id_crosswalk", "sync_state", "ingest_log",
    }
    assert expected <= tables


def test_schema_sql_is_current() -> None:
    """The committed DDL must match what the models generate — no silent drift."""
    assert SCHEMA_SQL.is_file(), "nbastats/schema.sql is missing; regenerate it"
    assert SCHEMA_SQL.read_text() == render_schema_sql()


def test_generated_ddl_covers_every_table_and_index() -> None:
    ddl = render_schema_sql()
    for table in Base.metadata.sorted_tables:
        assert f"CREATE TABLE {table.name}" in ddl
        for index in table.indexes:
            assert index.name is not None and index.name in ddl


def test_partition_equivalent_indexes_exist(seeded_engine: Engine) -> None:
    """The SQLite/Postgres stand-in for the BigQuery partition-and-cluster plan."""
    inspector = inspect(seeded_engine)

    def columns_of(table: str) -> list[list[str]]:
        return [list(index["column_names"]) for index in inspector.get_indexes(table)]

    assert ["game_date"] in columns_of("games")
    assert ["player_id", "season", "season_type"] in columns_of("player_season")
    assert ["season", "season_type"] in columns_of("player_season")
    assert ["season", "season_type"] in columns_of("team_season")


def test_advanced_columns_are_nullable() -> None:
    """A stat that did not exist in an era must be storable as NULL, never as 0."""
    for model in (PlayerGameAdvanced, PlayerSeason):
        for name in ("off_rtg", "def_rtg", "net_rtg", "ts_pct", "usg_pct", "pace", "poss"):
            column = model.__table__.columns.get(name)
            if column is not None:
                assert column.nullable, f"{model.__tablename__}.{name} must be nullable"
    assert PlayerGameBasic.__table__.columns["plus_minus"].nullable
    assert PlayerGameBasic.__table__.columns["fg3m"].nullable
    assert PlayerGameBasic.__table__.columns["stl"].nullable


def test_schema_round_trips(empty_engine: Engine) -> None:
    """Insert one row of each core shape and read it back with its types intact."""
    with Session(empty_engine, future=True) as session:
        session.add(
            Team(
                team_id=1610612747, abbr="LAL", name="Los Angeles Lakers", city="Los Angeles",
                nickname="Lakers", conference="West", division="Pacific", is_active=True,
                year_founded=1948,
            )
        )
        session.add(
            Player(
                player_id=2544, full_name="Test Player", first_name="Test", last_name="Player",
                bbref_slug="playete01", position="F", height="6-9", weight=250,
                birthdate=date(1984, 12, 30), country="USA", draft_year=2003, draft_round=1,
                draft_pick=1, from_year=2003, to_year=2026, is_active=True, jersey="23",
            )
        )
        session.add(
            Game(
                game_id="0022500512", game_date=date(2026, 1, 2), season="2025-26",
                season_type="Regular Season", home_team_id=1610612747,
                away_team_id=1610612747, home_pts=118, away_pts=112, status="final",
                period=4, finalized_at=datetime(2026, 1, 3, 2, 41, 7), data_source="test",
            )
        )
        session.add(
            PlayerGameBasic(
                game_id="0022500512", player_id=2544, team_id=1610612747, started=True,
                minutes=34.5, fgm=11, fga=20, fg3m=3, fg3a=7, ftm=7, fta=8, oreb=1, dreb=7,
                reb=8, ast=11, stl=1, blk=1, tov=3, pf=2, pts=32, plus_minus=6.0,
            )
        )
        session.add(
            PlayerGameAdvanced(
                game_id="0022500512", player_id=2544, ts_pct=0.6153, usg_pct=0.31,
                pace=99.4, is_estimated=False,
            )
        )
        session.commit()

    with Session(empty_engine, future=True) as session:
        game = session.get(Game, "0022500512")
        assert game is not None
        assert game.game_date == date(2026, 1, 2)
        assert game.status == "final"
        assert game.finalized_at == datetime(2026, 1, 3, 2, 41, 7)

        line = session.execute(select(PlayerGameBasic)).scalar_one()
        assert line.pts == 32
        assert line.minutes == pytest.approx(34.5)
        assert line.started is True

        advanced = session.execute(select(PlayerGameAdvanced)).scalar_one()
        assert advanced.ts_pct == pytest.approx(0.6153)
        # Percentages are fractions in [0, 1] everywhere in the data layer.
        assert 0.0 <= advanced.ts_pct <= 1.0
        assert advanced.off_rtg is None
        assert advanced.is_estimated is False


def test_era_limited_columns_accept_null(empty_engine: Engine) -> None:
    with Session(empty_engine, future=True) as session:
        session.add(
            Team(
                team_id=1, abbr="SYR", name="Syracuse Nationals", city="Syracuse",
                nickname="Nationals", conference="East", is_active=False,
                year_founded=1949, year_last_active=1963,
            )
        )
        session.add(Player(player_id=1, full_name="Old Timer"))
        session.add(
            Game(
                game_id="0025800001", game_date=date(1959, 1, 2), season="1958-59",
                season_type="Regular Season", home_team_id=1, away_team_id=1, status="final",
            )
        )
        session.add(
            PlayerGameBasic(
                game_id="0025800001", player_id=1, team_id=1, minutes=38.0, pts=24,
                fgm=9, fga=20, ftm=6, fta=8, reb=12, ast=3,
                fg3m=None, fg3a=None, stl=None, blk=None, tov=None,
                oreb=None, dreb=None, plus_minus=None,
            )
        )
        session.commit()
        row = session.execute(select(PlayerGameBasic)).scalar_one()
        assert row.pts == 24
        assert row.fg3a is None and row.stl is None and row.plus_minus is None


def test_sync_state_singleton_and_bump(empty_engine: Engine) -> None:
    with Session(empty_engine, future=True) as session:
        state = read_sync_state(session)
        assert state.sync_version == 0

        version = bump_sync_version(session, data_through=date(2026, 1, 2), note="first")
        assert version == 1
        assert read_sync_state(session).data_through == date(2026, 1, 2)

        # data_through only ever moves forward, so a back-fill cannot make us look stale.
        bump_sync_version(session, data_through=date(2025, 12, 30))
        state = read_sync_state(session)
        assert state.sync_version == 2
        assert state.data_through == date(2026, 1, 2)
        assert state.last_success_at is not None


@pytest.mark.parametrize(
    "mapping, model_name",
    [
        (PLAYER_GAME_METRIC_COLUMNS, "player_game_basic"),
        (PLAYER_GAME_ADVANCED_METRIC_COLUMNS, "player_game_advanced"),
        (TEAM_GAME_METRIC_COLUMNS, "team_game"),
        (PLAYER_SEASON_PER_GAME_COLUMNS, "player_season"),
        (PLAYER_SEASON_TOTALS_COLUMNS, "player_season"),
        (PLAYER_SEASON_RATE_COLUMNS, "player_season"),
        (TEAM_SEASON_PER_GAME_COLUMNS, "team_season"),
        (TEAM_SEASON_TOTALS_COLUMNS, "team_season"),
        (TEAM_SEASON_RATE_COLUMNS, "team_season"),
    ],
)
def test_metric_maps_point_at_real_columns_and_real_metrics(
    mapping: dict[str, str], model_name: str
) -> None:
    table = Base.metadata.tables[model_name]
    for metric_key, column in mapping.items():
        assert catalog.has_metric(metric_key), f"{metric_key} is not in metrics.json"
        assert column in table.c, f"{model_name}.{column} does not exist"


def test_season_column_resolution() -> None:
    assert season_column_for("pts", "player", "PerGame") == "pts"
    assert season_column_for("pts", "player", "Totals") == "pts_tot"
    assert season_column_for("min", "player", "PerGame") == "min_pg"
    assert season_column_for("min", "player", "Totals") == "minutes"
    # Rates do not change with per-mode.
    assert season_column_for("ts_pct", "player", "Totals") == "ts_pct"
    assert season_column_for("net_rtg", "team", "PerGame") == "net_rtg"
    # Per36/Per100 are derived by the API from totals, not stored twice.
    assert season_column_for("pts", "player", "Per36") is None
    assert season_column_for("opp_efg_pct", "player") is None


def test_catalogs_load_with_the_expected_shape() -> None:
    assert len(catalog.all_metrics()) == 61
    assert len(catalog.all_widgets()) == 12
    assert len(catalog.presets()) == 9
    assert catalog.metrics_document()["schemaVersion"] == 1
    assert catalog.subject_tokens() >= {"$favorite_player", "$favorite_team"}


@pytest.mark.parametrize(
    "metric_key, value, expected",
    [
        ("ts_pct", 0.6153, "61.5%"),      # percentages are fractions, rendered as percent
        ("pts", 24.05, "24.1"),
        ("gp", 41.0, "41"),
        ("plus_minus", 7.24, "+7.2"),     # plusMinus1 is signed
        ("plus_minus", -3.15, "-3.2"),
        ("plus_minus", -0.02, "+0.0"),    # never "-0.0"
        ("off_rtg", 118.24, "118.2"),
        ("ws48", 0.1964, "0.20"),
        ("min", 34.55, "34.6"),
        ("pts", None, "—"),          # em dash for an era-unavailable value
        ("ts_pct", None, "—"),
    ],
)
def test_format_metric(metric_key: str, value: float | None, expected: str) -> None:
    assert catalog.format_metric(metric_key, value) == expected


@pytest.mark.parametrize(
    "metric_key, season, granularity, expected",
    [
        ("pts", "1985-86", "season", "full"),
        ("fg3m", "1978-79", "game", "unavailable"),      # no three-point line yet
        ("stl", "1971-72", "game", "unavailable"),       # steals from 1973-74
        ("plus_minus", "1992-93", "game", "unavailable"),
        ("off_rtg", "1992-93", "season", "unavailable"),
        ("usg_pct", "1992-93", "season", "estimated"),   # box-score derivation
        ("usg_pct", "1992-93", "game", "unavailable"),
        ("usg_pct", "2024-25", "season", "full"),
        ("per", "2024-25", "game", "unavailable"),       # season-level only
        ("per", "2024-25", "season", "full"),
        ("ts_pct", "1985-86", "season", "full"),
        ("ts_pct", "1985-86", "game", "unavailable"),
    ],
)
def test_metric_availability(
    metric_key: str, season: str, granularity: str, expected: str
) -> None:
    assert catalog.metric_availability(metric_key, season, granularity) == expected


def test_availability_across_a_career_is_partial() -> None:
    """A span that straddles an era boundary is 'partial', not a confident 'full'."""
    assert catalog.metric_availability("pts", "career") == "full"
    assert catalog.metric_availability("off_rtg", "career") == "partial"
    assert catalog.metric_availability("usg_pct", ["1992-93", "2024-25"]) == "estimated"
    assert catalog.metric_availability("off_rtg", ["1992-93", "2024-25"]) == "partial"
    assert catalog.combine_availability(["full", "unavailable"]) == "partial"


def test_season_sort_key() -> None:
    assert catalog.season_sort_key("2025-26") == 2025
    assert catalog.season_sort_key("1999-00") == 1999
    assert catalog.season_sort_key("1946-47") == 1946
    with pytest.raises(ValueError):
        catalog.season_sort_key("latest")


def test_widget_config_validation_cleans_and_reports() -> None:
    cleaned, errors = catalog.validate_widget_config(
        "leaderboard",
        {
            "metric": "ts_pct",
            "limit": 999,           # above max: clamped
            "minGames": "many",     # wrong type: reported, default restored
            "seasonType": "Summer", # not an enum member
            "somethingElse": 1,     # unknown: dropped
        },
    )
    assert cleaned["limit"] == 50                    # clamped to the catalog max
    assert cleaned["minGames"] == 15                 # fell back to the default
    assert cleaned["seasonType"] == "Regular Season"
    assert "somethingElse" not in cleaned
    assert cleaned["season"] == "latest"             # missing optional key filled in
    fields = {error.field for error in errors}
    assert fields == {"minGames", "seasonType"}


def test_widget_config_accepts_ids_and_subject_tokens() -> None:
    cleaned, errors = catalog.validate_widget_config(
        "stat_tile", {"subjectType": "team", "subjectId": 1610612747, "metric": "net_rtg"}
    )
    assert not errors and cleaned["subjectId"] == 1610612747

    cleaned, errors = catalog.validate_widget_config(
        "stat_tile", {"subjectId": "$favorite_player", "metric": "pts"}
    )
    assert not errors and cleaned["subjectId"] == "$favorite_player"

    cleaned, errors = catalog.validate_widget_config(
        "stat_tile", {"subjectId": "$nonsense", "metric": "pts"}
    )
    assert [error.field for error in errors] == ["subjectId"]


def test_widget_config_reports_a_missing_required_key() -> None:
    _, errors = catalog.validate_widget_config("player_snapshot", {})
    assert [error.field for error in errors] == ["playerId"]

    _, errors = catalog.validate_widget_config("comparison", {"playerIds": [1]})
    assert any(error.field == "playerIds" for error in errors)  # below minItems


def test_widget_config_honours_metric_scope_and_max_items() -> None:
    _, errors = catalog.validate_widget_config(
        "player_snapshot", {"playerId": 1, "metrics": ["opp_efg_pct"]}
    )
    assert any("player metric" in error.message for error in errors)

    cleaned, errors = catalog.validate_widget_config(
        "stat_tile",
        {"subjectId": 1, "metric": "pts", "secondaryMetrics": ["pts", "ast", "reb", "stl", "blk"]},
    )
    assert len(cleaned["secondaryMetrics"]) == 4  # truncated to maxItems, and reported
    assert any(error.field == "secondaryMetrics" for error in errors)


def test_every_preset_widget_is_valid() -> None:
    """The nine shipped dashboards must survive the validator untouched."""
    for preset in catalog.presets():
        for widget in preset["widgets"]:
            spec = catalog.widget(widget["kind"])
            assert widget["size"] in spec["sizes"]
            cleaned, errors = catalog.validate_widget_config(widget["kind"], widget["config"])
            assert not errors, f"{widget['id']}: {[str(e) for e in errors]}"
            for key, value in widget["config"].items():
                assert cleaned[key] == value, f"{widget['id']}.{key} was rewritten"


def test_widget_defaults_are_copies() -> None:
    first = catalog.widget_config_defaults("leaderboard")
    first["secondaryMetrics"].append("ts_pct")
    assert catalog.widget_config_defaults("leaderboard")["secondaryMetrics"] == ["pts", "min"]


def test_every_player_scope_metric_is_reachable() -> None:
    """Each player metric can be served from some column, or is derived on the fly."""
    stored = (
        set(PLAYER_SEASON_PER_GAME_COLUMNS)
        | set(PLAYER_SEASON_TOTALS_COLUMNS)
        | set(PLAYER_SEASON_RATE_COLUMNS)
    )
    # Team-only companions of the four factors are not player metrics.
    derived = {"wins", "losses", "win_pct"}
    missing = {
        metric["key"]
        for metric in catalog.metrics_for_scope("player")
        if metric["key"] not in stored and metric["key"] not in derived
    }
    assert not missing, f"player metrics with nowhere to live: {sorted(missing)}"
