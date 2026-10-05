"""The NBA intel tables: their own metadata, a re-seed that cannot touch them, and what they refuse.

The most important test in this file is
:func:`test_intel_rows_survive_a_reseed_and_the_demo_boot_path`. ``seed_database()`` deletes every
row of every table on ``Base.metadata``, and the API's startup runs it unattended whenever the demo
flag is on and ``teams`` is empty. An injury snapshot, a pasted link, a model setting the person
changed or the scheduler's job state living on that metadata would vanish without a log line. They
live on ``NbaIntelBase`` instead, and this file proves that empirically, through both ways in.

Around it, the rest of the schema's promises:

* the tables are disjoint from the stats and account metadata, prefixed ``nba_intel_``, created by
  ``init_db`` in the same file, and described by a committed ``schema.sql`` that cannot drift;
* the database itself refuses what the vocabulary forbids: an unknown status, reason or source kind,
  a model-setting key that is not allowlisted (there is no key for a total spread, a player spread
  or a threshold on a probability), an unreadable state, a link-less row that is neither manual nor
  a withheld link; and status rows cannot be updated;
* no table, column, constraint or setting key uses gambling vocabulary, and the new packages pass
  the prose scan the web-release guard applies elsewhere;
* the synthetic-store guard is a question about the *rows*, not the environment;
* the settings, the source-state bookkeeping, the parser-confirmation counter and the job cursor
  behave as their docstrings say, and never disturb the columns the worker owns;
* the import discipline the package docstrings state is checked, so ``intel`` stays free of
  databases and leagues and ``nba_intel`` never reaches into the stats models or the EuroLeague.
"""

from __future__ import annotations

import ast
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nbastats import config
from nbastats import db as db_module
from nbastats.api import app as app_module  # imported at collection, before any env can leak
from nbastats.db import init_db
from nbastats.models import Base, Game, Team, render_schema_sql
from nbastats.accounts.models import AccountBase
from nbastats.nba_intel import models as M
from nbastats.nba_intel import settings as S
from nbastats.nba_intel import status, store
from nbastats.nba_intel.models import NbaIntelBase, render_schema_sql as render_intel_sql
from nbastats.seed import seed_database
from nbastats.shared import availability as avail
from nbastats.shared import market_guard
from nbastats.shared.league_profile import NBA

BACKEND = Path(__file__).resolve().parents[1]
PACKAGE = BACKEND / "nbastats"
SCHEMA_SQL = PACKAGE / "nba_intel" / "schema.sql"
NOW = datetime(2026, 10, 20, 15, 0)

DESIGN_SETTING_KEYS = [  # design section 8.6, the NBA allowlist, written out
    "priorRegression",
    "priorWeightGames",
    "leagueLevelWeight",
    "homeAdvantagePoints",
    "teamSd",
    "marginSd",
    "replacementShare",
    "absorbShare",
    "boostCap",
    "capPolicyConsistent",
    "statusChance.out",
    "statusChance.doubtful",
    "statusChance.questionable",
    "statusChance.probable",
    "statusChance.available",
    "positionCoverageCeiling",
]


# ---------------------------------------------------------------------- the separation


def test_the_tables_are_on_their_own_metadata() -> None:
    intel = set(NbaIntelBase.metadata.tables)
    assert intel == set(M.NBA_INTEL_TABLE_NAMES) and len(intel) == 11
    assert not intel & set(Base.metadata.tables)
    assert not intel & set(AccountBase.metadata.tables)
    assert all(name.startswith("nba_intel_") for name in intel)
    assert not any(name.startswith("nba_intel_") for name in Base.metadata.tables)
    assert NbaIntelBase.metadata is not Base.metadata


def test_the_stats_ddl_never_mentions_the_intel_tables() -> None:
    ddl = render_schema_sql()
    assert "nba_intel" not in ddl
    assert "nba_intel" not in (PACKAGE / "schema.sql").read_text()


def test_init_db_creates_every_intel_table_and_is_idempotent(empty_engine: Engine) -> None:
    names = set(inspect(empty_engine).get_table_names())
    assert set(M.NBA_INTEL_TABLE_NAMES) <= names
    init_db(empty_engine)  # a second call changes nothing and raises nothing
    assert set(M.NBA_INTEL_TABLE_NAMES) <= set(inspect(empty_engine).get_table_names())


def test_the_committed_schema_sql_is_current() -> None:
    assert SCHEMA_SQL.is_file(), "nbastats/nba_intel/schema.sql is missing; regenerate it"
    assert SCHEMA_SQL.read_text() == render_intel_sql()


def test_the_generated_ddl_covers_every_table_and_index_and_is_valid_sqlite() -> None:
    ddl = render_intel_sql()
    for table in NbaIntelBase.metadata.sorted_tables:
        assert f"CREATE TABLE {table.name} " in ddl
        for index in table.indexes:
            assert index.name is not None and index.name in ddl
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(ddl)
        created = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        connection.close()
    assert created == set(M.NBA_INTEL_TABLE_NAMES)


def test_there_is_no_alter_and_no_migration_anywhere() -> None:
    for path in [SCHEMA_SQL, *sorted((PACKAGE / "nba_intel").glob("*.py"))]:
        body = path.read_text()
        assert not re.search(r"\bALTER\s+TABLE\b", body, re.IGNORECASE), path.name
        assert "alembic" not in body.lower() and "add_column" not in body, path.name


def test_the_package_data_ships_the_schema_and_the_denylist() -> None:
    import tomllib

    declared = tomllib.loads((BACKEND / "pyproject.toml").read_text())["tool"]["setuptools"][
        "package-data"
    ]["nbastats"]
    assert "nba_intel/schema.sql" in declared and "nba_intel/data/*.json" in declared
    assert (PACKAGE / "nba_intel" / "data" / "source_denylist.json").is_file()


# ------------------------------------------------------- surviving a re-seed (the big one)


def populate_every_table(session: Session) -> None:
    """One row in each of the eleven tables."""
    snapshot = M.NbaIntelSnapshot(
        source_kind="leagueReport",
        url="https://static.example.org/r.pdf",
        slot_at_utc=NOW,
        report_as_of_utc=NOW,
        fetched_at=NOW,
        sha256="b" * 64,
        row_count=1,
        parse_status="ok",
    )
    session.add(snapshot)
    session.flush()
    session.add(
        M.NbaIntelTeamReport(snapshot_id=snapshot.snapshot_id, team_id=1, state="submitted")
    )
    session.add(
        M.NbaIntelStatus(
            team_id=1,
            player_id=2,
            player_name_raw="Sample, Alex",
            status="out",
            source_kind="leagueReport",
            source_label="NBA official injury report",
            source_url="https://static.example.org/r.pdf",
            source_published_at=NOW,
            as_of=NOW,
            recorded_at=NOW,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    session.add(M.NbaIntelOverride(player_id=2, status="questionable", created_at=NOW))
    feed = M.NbaIntelNewsFeed(name="Example", url="https://feeds.example.org/f", enabled=True)
    session.add(feed)
    session.flush()
    item = M.NbaIntelNewsItem(
        feed_id=feed.feed_id,
        guid="g",
        title="Headline",
        link="https://feeds.example.org/a",
        published_at=NOW,
        fetched_at=NOW,
        source_name="Example",
    )
    session.add(item)
    session.flush()
    session.add(M.NbaIntelNewsSubject(item_id=item.item_id, team_id=1, player_id=0))
    session.add(M.NbaIntelSourceState(source_key="nba.injuryReport", state="ok"))
    session.add(M.NbaIntelJobState(job_key="nba.injuries", cursor_json="{}"))
    session.add(
        M.NbaIntelModelSetting(
            key="homeAdvantagePoints", value=3.1, provenance="manual", set_at=NOW
        )
    )
    session.add(M.NbaIntelRawFetch(source="nba_injury", fetched_at=NOW, http_status=200))
    session.commit()


def intel_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            name: connection.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar_one()
            for name in sorted(M.NBA_INTEL_TABLE_NAMES)
        }


SMALL_SEED: dict[str, Any] = dict(
    seasons=["2025-26"], games_per_team=2, players_per_team=2, include_playoffs=False
)


def test_intel_rows_survive_a_reseed_and_the_demo_boot_path(
    empty_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Session(empty_engine) as session:
        populate_every_table(session)
    before = intel_counts(empty_engine)
    assert set(before) == set(M.NBA_INTEL_TABLE_NAMES) and all(n == 1 for n in before.values())

    # 1. a direct reseed (what `hardwood-seed` and the demo path both call)
    with Session(empty_engine) as session:
        seed_database(session, **SMALL_SEED)
        session.commit()
        assert session.execute(select(func.count()).select_from(Team)).scalar() > 0
    assert intel_counts(empty_engine) == before
    with Session(empty_engine) as session:
        seed_database(session, **SMALL_SEED)  # and again: the seeder clears and refills every time
        session.commit()
    assert intel_counts(empty_engine) == before

    # 2. the API's own startup path, on a store whose `teams` table is empty and demo mode is on
    from nbastats import seed as seed_module

    real_seed = seed_module.seed_database
    monkeypatch.setattr(
        seed_module, "seed_database", lambda session, **kwargs: real_seed(session, **SMALL_SEED)
    )
    path = tmp_path / "boot.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("HARDWOOD_DEMO_MODE", "1")
    monkeypatch.delenv("HARDWOOD_PUBLIC_BASE_URL", raising=False)
    config.reset_settings_cache()
    db_module.dispose_engine()
    try:
        engine = db_module.get_engine()
        init_db(engine)
        with Session(engine) as session:
            populate_every_table(session)
            assert session.execute(select(func.count()).select_from(Team)).scalar() == 0
        expected = intel_counts(engine)
        app_module._prepare_database()
        with Session(engine) as session:
            assert session.execute(select(func.count()).select_from(Team)).scalar() > 0  # it seeded
        assert intel_counts(engine) == expected
    finally:
        db_module.dispose_engine()
        config.reset_settings_cache()


# --------------------------------------------------------------- what the database refuses


def _row(table: str, **overrides: Any) -> Any:
    """A valid row of ``table`` with ``overrides`` applied, for the constraint tests."""
    base: dict[str, dict[str, Any]] = {
        "snapshot": dict(
            source_kind="leagueReport", fetched_at=NOW, row_count=0, parse_status="ok"
        ),
        "status": dict(
            team_id=1,
            player_name_raw="Sample, Alex",
            status="out",
            source_kind="leagueReport",
            source_label="NBA official injury report",
            source_url="https://x.example.org/r",
            source_published_at=NOW,
            as_of=NOW,
            recorded_at=NOW,
        ),
        "override": dict(player_id=1, status="out", created_at=NOW),
        "feed": dict(name="F", url="https://f.example.org/feed", enabled=True),
        "source_state": dict(source_key="k", state="ok"),
        "setting": dict(key="boostCap", value=1.35, provenance="manual", set_at=NOW),
        "team_report": dict(snapshot_id=1, team_id=1, state="submitted"),
    }
    models = {
        "snapshot": M.NbaIntelSnapshot,
        "status": M.NbaIntelStatus,
        "override": M.NbaIntelOverride,
        "feed": M.NbaIntelNewsFeed,
        "source_state": M.NbaIntelSourceState,
        "setting": M.NbaIntelModelSetting,
        "team_report": M.NbaIntelTeamReport,
    }
    return models[table](**{**base[table], **overrides})


def _accepted(engine: Engine, row: Any) -> bool:
    with Session(engine) as session:
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return False
    return True


@pytest.mark.parametrize(
    "table, bad",
    [
        ("status", dict(status="healthy")),
        ("status", dict(status="OUT")),
        ("status", dict(status="")),
        ("status", dict(model_status="fine")),
        ("status", dict(reason_category="boredom")),
        ("status", dict(source_kind="rumour")),
        (
            "status",
            dict(source_url=None, source_kind="pressArticle", source_label="basketnews.com"),
        ),
        ("override", dict(status="healthy")),
        ("override", dict(status=None)),
        ("snapshot", dict(parse_status="great")),
        ("snapshot", dict(source_kind="tweet")),
        ("team_report", dict(state="maybe")),
        ("source_state", dict(state="termsNotReviewed")),
        ("source_state", dict(state="fine")),
        ("setting", dict(key="totalSd")),
        ("setting", dict(key="edgeP")),
        ("setting", dict(key="psdBase")),
        ("setting", dict(key="psdSlope")),
        ("setting", dict(key="line")),
        ("setting", dict(provenance="guess")),
        ("feed", dict(robots_state="maybe")),
    ],
)
def test_the_database_refuses_what_the_vocabulary_forbids(
    empty_engine: Engine, table: str, bad: dict[str, Any]
) -> None:
    assert not _accepted(empty_engine, _row(table, **bad)), (table, bad)
    assert _accepted(empty_engine, _row(table)), "the same row, valid, must be accepted"


@pytest.mark.parametrize(
    "table, good",
    [
        ("status", dict(status=None)),  # not stated: NULL, never "available"
        ("status", dict(model_status=None, reason_category=None)),
        ("status", dict(status="available", source_kind="workbookImport")),
        ("status", dict(source_url=None, source_kind="manual", source_label="By hand")),
        (
            "status",
            dict(
                source_url=None,
                source_kind="pressArticle",
                source_label="mozzartsport.com (link withheld: betting operator)",
            ),
        ),
        ("setting", dict(key="statusChance.questionable", value=0.4)),
        ("source_state", dict(state="noReportYet")),
        ("feed", dict(robots_state="disallowed", enabled=False)),
    ],
)
def test_and_accepts_the_cases_the_design_allows(
    empty_engine: Engine, table: str, good: dict[str, Any]
) -> None:
    assert _accepted(empty_engine, _row(table, **good)), (table, good)


def test_every_allowlisted_setting_key_is_storable_and_only_those(empty_engine: Engine) -> None:
    assert list(M.MODEL_SETTING_KEYS) == DESIGN_SETTING_KEYS
    for key in DESIGN_SETTING_KEYS:
        assert _accepted(empty_engine, _row("setting", key=key)), key


def test_status_rows_cannot_be_updated(empty_engine: Engine) -> None:
    with Session(empty_engine) as session:
        row = _row("status")
        session.add(row)
        session.commit()
        row.status = "available"
        with pytest.raises(M.AppendOnlyError):
            session.commit()


def test_a_primary_key_game_is_the_empty_string_when_the_game_is_unknown(
    empty_engine: Engine,
) -> None:
    assert M.NO_GAME == ""
    with Session(empty_engine) as session:
        session.add(M.NbaIntelTeamReport(snapshot_id=5, team_id=1, state="submitted"))
        session.commit()
        stored = session.execute(select(M.NbaIntelTeamReport)).scalars().one()
    assert stored.game_id == M.NO_GAME


# ------------------------------------------------------------------- vocabularies agree


def test_the_check_vocabularies_match_the_shared_core() -> None:
    assert M.STATUS_VALUES == avail.STATUSES
    assert M.REASON_CATEGORY_VALUES == avail.REASON_CATEGORIES
    assert M.SOURCE_KIND_VALUES == avail.SOURCE_KINDS
    assert set(M.TEAM_REPORT_STATES) <= set(avail.TEAM_REPORT_STATES)
    assert "noReport" not in M.TEAM_REPORT_STATES  # computed, never stored
    assert "termsNotReviewed" not in M.SOURCE_STATE_VALUES  # the gate was removed
    assert {
        "ok",
        "stale",
        "disabled",
        "notConfigured",
        "noReportYet",
        "blocked",
        "unreadable",
        "error",
    } == set(M.SOURCE_STATE_VALUES)
    assert "notFound" in M.PARSE_STATUSES and "headerMismatch" in M.PARSE_STATUSES


def test_the_setting_defaults_come_from_the_profile() -> None:
    for status_name, chance in NBA.status_chance:
        assert S.DEFAULTS[f"statusChance.{status_name}"] == chance
    assert S.DEFAULTS["homeAdvantagePoints"] == NBA.home_advantage_default == 2.5
    assert S.DEFAULTS["positionCoverageCeiling"] == NBA.defense.unknown_ceiling == 0.05
    assert S.NO_DEFAULT_KEYS == {"teamSd", "marginSd"}  # nothing honest to put there yet


# ------------------------------------------------------------------ no gambling words


def test_no_table_column_constraint_or_key_uses_gambling_vocabulary() -> None:
    names: list[str] = list(M.MODEL_SETTING_KEYS)
    for table in NbaIntelBase.metadata.sorted_tables:
        names.append(table.name)
        names.extend(column.name for column in table.columns)
        names.extend(index.name or "" for index in table.indexes)
        names.extend(str(constraint.name or "") for constraint in table.constraints)
    assert len(names) > 100
    offenders = {
        n: market_guard.forbidden_words_in(n) for n in names if market_guard.forbidden_words_in(n)
    }
    assert offenders == {}


PROSE_BANNED = (
    r"odds",
    r"vig",
    r"kelly",
    r"wager\w*",
    r"sportsbook\w*",
    r"payout\w*",
    r"bett?ing",
    r"bookmaker\w*",
    r"parlay\w*",
    r"stake",
    r"staking",
    r"over/under",
    r"point spread",
    r"moneyline\w*",
)


def test_the_new_packages_pass_the_prose_guard() -> None:
    pattern = re.compile(r"\b(?:" + "|".join(PROSE_BANNED) + r")\b")
    scanned = 0
    offences: list[str] = []
    for base in (PACKAGE / "intel", PACKAGE / "nba_intel"):
        for path in sorted(base.rglob("*.py")):
            scanned += 1
            for found in sorted(set(pattern.findall(path.read_text(encoding="utf-8").lower()))):
                offences.append(f"{path.relative_to(PACKAGE)} mentions {found!r}")
    assert scanned >= 14, scanned
    assert offences == []


def test_no_half_point_rounding_in_the_new_packages() -> None:
    for base in (PACKAGE / "intel", PACKAGE / "nba_intel"):
        for path in sorted(base.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                    left = node.left
                    if (
                        isinstance(left, ast.Call)
                        and getattr(left.func, "id", "") == "round"
                        and left.args
                        and isinstance(left.args[0], ast.BinOp)
                        and isinstance(left.args[0].op, ast.Mult)
                    ):
                        pytest.fail(f"{path.name} rounds to a half point")


# --------------------------------------------------------------------- the guard


def test_the_synthetic_guard_reads_the_rows_not_the_environment(
    seeded_db: Session, empty_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HARDWOOD_DEMO_MODE", raising=False)
    assert store.store_is_synthetic(seeded_db) is True  # the demo league, with demo mode "off"
    monkeypatch.setenv("HARDWOOD_DEMO_MODE", "1")
    with Session(empty_engine) as session:
        assert store.store_is_synthetic(session) is False  # demo mode "on", but nothing invented


def test_a_live_store_and_a_store_with_no_games_table_are_not_synthetic(
    empty_engine: Engine, tmp_path: Path
) -> None:
    with Session(empty_engine) as session:
        session.add_all(
            [
                Team(team_id=1, abbr="AAA", name="A", city="A", nickname="A"),
                Team(team_id=2, abbr="BBB", name="B", city="B", nickname="B"),
            ]
        )
        session.add(
            Game(
                game_id="L-1",
                game_date=date(2026, 10, 20),
                season="2026-27",
                season_type="Regular Season",
                home_team_id=1,
                away_team_id=2,
                status="final",
                data_source="nba_api",
            )
        )
        session.commit()
        assert store.store_is_synthetic(session) is False
        session.add(
            Game(
                game_id="S-1",
                game_date=date(2026, 10, 21),
                season="2026-27",
                season_type="Regular Season",
                home_team_id=1,
                away_team_id=2,
                status="final",
                data_source="synthetic-demo",
            )
        )
        session.commit()
        assert store.store_is_synthetic(session) is True  # one invented game is enough

    bare = create_engine(f"sqlite:///{tmp_path / 'bare.db'}")
    NbaIntelBase.metadata.create_all(bare)
    with Session(bare) as session:
        assert store.table_exists(session, "games") is False
        assert store.store_is_synthetic(session) is False
    bare.dispose()


# ------------------------------------------------------------------------- settings


def test_defaults_are_reported_as_defaults_in_allowlist_order(empty_engine: Engine) -> None:
    with Session(empty_engine) as session:
        everything = S.get_all(session)
    assert [s.key for s in everything] == DESIGN_SETTING_KEYS
    by_key = {s.key: s for s in everything}
    assert all(s.is_default and s.provenance == "default" and s.set_at is None for s in everything)
    assert by_key["priorRegression"].value == 0.3
    assert by_key["priorWeightGames"].value == 10
    assert by_key["leagueLevelWeight"].value == 150
    assert by_key["replacementShare"].value == 0.52
    assert by_key["absorbShare"].value == 0.6 and by_key["boostCap"].value == 1.35
    assert by_key["capPolicyConsistent"].value == 1.0
    assert by_key["teamSd"].value is None and by_key["marginSd"].value is None


def test_a_set_value_replaces_the_default_and_a_reset_restores_it(empty_engine: Engine) -> None:
    with Session(empty_engine) as session:
        changed = S.set_setting(session, "homeAdvantagePoints", 3.1, now=NOW)
        session.commit()
        assert changed.value == 3.1 and not changed.is_default and changed.provenance == "manual"
        again = {s.key: s for s in S.get_all(session)}["homeAdvantagePoints"]
        assert again.value == 3.1 and again.set_at == NOW and not again.is_default
        assert S.get_value(session, "homeAdvantagePoints") == 3.1
        assert S.reset_setting(session, "homeAdvantagePoints") is True
        assert S.reset_setting(session, "homeAdvantagePoints") is False
        assert S.get_value(session, "homeAdvantagePoints") == 2.5
        assert S.get_value(session, "teamSd") is None


def test_fitted_values_carry_their_provenance(empty_engine: Engine) -> None:
    with Session(empty_engine) as session:
        S.apply_patch(
            session, {"teamSd": 10.2, "marginSd": 13.1}, provenance="fittedPrevSeason", now=NOW
        )
        session.commit()
        by_key = {s.key: s for s in S.get_all(session)}
    assert by_key["teamSd"].value == 10.2 and by_key["teamSd"].provenance == "fittedPrevSeason"
    assert not by_key["teamSd"].is_default


@pytest.mark.parametrize(
    "key, value",
    [
        ("totalSd", 20.0),
        ("edgeP", 0.05),
        ("line", 210.5),
        ("homeAdvantagePoints", -1),
        ("homeAdvantagePoints", 99),
        ("priorRegression", 1.5),
        ("priorRegression", -0.1),
        ("priorWeightGames", 0),
        ("boostCap", 0.9),
        ("boostCap", 5),
        ("capPolicyConsistent", 0.5),
        ("capPolicyConsistent", 2),
        ("teamSd", 0),
        ("marginSd", -3),
        ("absorbShare", float("nan")),
        ("absorbShare", float("inf")),
        ("absorbShare", True),
        ("absorbShare", "0.5"),
        ("absorbShare", None),
        ("statusChance.out", 1.2),
        ("positionCoverageCeiling", -0.01),
    ],
)
def test_bad_settings_are_refused_with_a_message(
    empty_engine: Engine, key: str, value: Any
) -> None:
    with Session(empty_engine) as session:
        with pytest.raises(S.InvalidSettingError) as raised:
            S.set_setting(session, key, value)
        assert key in str(raised.value) or "model setting" in str(raised.value)
        assert (
            session.execute(select(func.count()).select_from(M.NbaIntelModelSetting)).scalar() == 0
        )


def test_the_status_chances_must_stay_in_order(empty_engine: Engine) -> None:
    with Session(empty_engine) as session:
        with pytest.raises(S.InvalidSettingError):
            S.set_setting(session, "statusChance.questionable", 0.9)  # above "probable" (0.85)
        S.set_setting(session, "statusChance.questionable", 0.8)
        with pytest.raises(S.InvalidSettingError):
            S.set_setting(session, "statusChance.doubtful", 0.95)
        S.apply_patch(session, {"statusChance.doubtful": 0.1, "statusChance.out": 0.05})
        table = S.chance_table(session)
    assert table == {
        "out": 0.05,
        "doubtful": 0.1,
        "questionable": 0.8,
        "probable": 0.85,
        "available": 1.0,
    }
    assert avail.validate_status_chance(table) == table  # the shared core accepts it


def test_a_patch_is_all_or_nothing(empty_engine: Engine) -> None:
    with Session(empty_engine) as session:
        with pytest.raises(S.InvalidSettingError):
            S.apply_patch(session, {"boostCap": 1.5, "absorbShare": 7})
        assert (
            session.execute(select(func.count()).select_from(M.NbaIntelModelSetting)).scalar() == 0
        )
        assert S.get_value(session, "boostCap") == 1.35
        assert S.apply_patch(session, {}) == []
        with pytest.raises(S.InvalidSettingError):
            S.apply_patch(session, {"boostCap": 1.5}, provenance="default")
        with pytest.raises(S.InvalidSettingError):
            S.apply_patch(session, {"boostCap": 1.5}, provenance="whim")


def test_the_chance_table_defaults_to_the_shared_profile(empty_engine: Engine) -> None:
    with Session(empty_engine) as session:
        assert S.chance_table(session) == NBA.chance_table()


def test_settings_reject_unknown_keys_on_every_path(empty_engine: Engine) -> None:
    with Session(empty_engine) as session:
        with pytest.raises(S.InvalidSettingError):
            S.get_value(session, "totalSd")
        with pytest.raises(S.InvalidSettingError):
            S.reset_setting(session, "edgeP")


# -------------------------------------------------------------------- store: state


def test_source_state_bookkeeping(empty_engine: Engine) -> None:
    key = "nba.injuryReport"
    with Session(empty_engine) as session:
        store.set_source_state(session, key, "ok", now=NOW, success=True)
        row = store.get_source_state(session, key)
        assert row is not None and row.last_success_at == NOW and row.consecutive_failures == 0

        later = NOW + timedelta(minutes=15)
        store.set_source_state(session, key, "unreadable", now=later, error="header changed")
        store.set_source_state(session, key, "unreadable", now=later, error="header changed\nagain")
        row = store.get_source_state(session, key)
        assert row is not None and row.state == "unreadable" and row.consecutive_failures == 2
        assert row.last_success_at == NOW  # the last good time is kept
        assert row.last_error == "header changed again"  # one line

        store.set_source_state(session, key, "noReportYet", now=later)
        row = store.get_source_state(session, key)
        assert row is not None and row.consecutive_failures == 2  # not a failure, not a success

        store.set_source_state(session, key, "ok", now=later, success=True)
        row = store.get_source_state(session, key)
        assert row is not None and row.consecutive_failures == 0 and row.last_error is None

        with pytest.raises(ValueError):
            store.set_source_state(session, key, "termsNotReviewed", now=later)


def test_detail_is_merged_so_writers_do_not_erase_each_other(empty_engine: Engine) -> None:
    key = "nba.injuryReport"
    with Session(empty_engine) as session:
        store.set_source_state(session, key, "ok", now=NOW, detail={"a": 1})
        store.set_source_state(session, key, "ok", now=NOW, detail={"b": 2})
        assert store.source_detail(store.get_source_state(session, key)) == {"a": 1, "b": 2}
        session.get(M.NbaIntelSourceState, key).detail_json = "not json"  # type: ignore[union-attr]
        assert store.source_detail(store.get_source_state(session, key)) == {}
        assert store.source_detail(None) == {}


def test_the_parser_confirmation_counter_persists_and_only_counts_what_it_is_told(
    empty_engine: Engine,
) -> None:
    with Session(empty_engine) as session:
        assert store.injury_parser_confirmation(session) == store.ParserConfirmation(0, None, None)
        assert not store.injury_parser_confirmation(session).confirmed
        first = store.record_parser_confirmation(session, now=NOW)
        second = store.record_parser_confirmation(session, now=NOW + timedelta(days=1))
        session.commit()
    assert (first.count, second.count) == (1, 2)
    with Session(empty_engine) as session:  # a new session, as after a restart
        seen = store.injury_parser_confirmation(session)
        row = store.get_source_state(session, "nba.injuryReport")
    assert seen.count == 2 and seen.confirmed
    assert seen.first_at == NOW and seen.last_at == NOW + timedelta(days=1)
    assert row is not None and row.state == "ok"


def test_a_confirmation_keeps_the_state_and_pause_another_writer_set(empty_engine: Engine) -> None:
    pause = NOW + timedelta(hours=6)
    with Session(empty_engine) as session:
        store.set_source_state(
            session, "nba.injuryReport", "blocked", now=NOW, error="403", paused_until=pause
        )
        store.record_parser_confirmation(session, now=NOW)
        row = store.get_source_state(session, "nba.injuryReport")
        assert row is not None and row.state == "blocked" and row.paused_until == pause


def test_the_job_cursor_creates_its_row_and_never_touches_the_workers_columns(
    empty_engine: Engine,
) -> None:
    with Session(empty_engine) as session:
        assert store.read_cursor(session, "nba.injuries") == {}
        store.write_cursor(session, "nba.injuries", {"newestSlot": "2026-10-22T21:15:00"})
        session.commit()
        # the worker writes its own columns afterwards
        row = session.get(M.NbaIntelJobState, "nba.injuries")
        assert row is not None
        row.last_started_at, row.last_success_at, row.last_error = NOW, NOW, None
        session.commit()
        store.write_cursor(session, "nba.injuries", {"newestSlot": "later", "extra": 1})
        session.commit()
        row = session.get(M.NbaIntelJobState, "nba.injuries")
        assert row is not None
        assert row.last_started_at == NOW and row.last_success_at == NOW
        assert store.read_cursor(session, "nba.injuries") == {"newestSlot": "later", "extra": 1}
        row.cursor_json = "[1, 2]"
        assert store.read_cursor(session, "nba.injuries") == {}  # not a dict: treated as empty


def test_the_worker_can_insert_a_job_row_with_only_its_own_columns(empty_engine: Engine) -> None:
    with empty_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO nba_intel_job_state (job_key, last_started_at, last_success_at, "
                "last_error) VALUES ('nba.news', :t, :t, NULL)"
            ),
            {"t": NOW},
        )
    with Session(empty_engine) as session:
        assert store.read_cursor(session, "nba.news") == {}
        store.write_cursor(session, "nba.news", {"k": 1})
        session.commit()
        row = session.get(M.NbaIntelJobState, "nba.news")
        assert row is not None and row.last_started_at == NOW


def test_snapshot_helpers(empty_engine: Engine) -> None:
    with Session(empty_engine) as session:
        assert store.latest_snapshot(session) is None
        assert store.newest_fetched_slot(session) is None
        older = store.insert_snapshot(
            session,
            source_kind="leagueReport",
            fetched_at=NOW,
            parse_status="ok",
            row_count=3,
            slot_at_utc=NOW - timedelta(hours=1),
            sha256="1" * 64,
        )
        newer = store.insert_snapshot(
            session,
            source_kind="leagueReport",
            fetched_at=NOW,
            parse_status="partial",
            row_count=2,
            slot_at_utc=NOW,
            sha256="2" * 64,
        )
        store.insert_snapshot(
            session,
            source_kind="leagueReport",
            fetched_at=NOW,
            parse_status="headerMismatch",
            slot_at_utc=NOW + timedelta(hours=1),
            sha256="3" * 64,
        )
        store.insert_snapshot(
            session,
            source_kind="leagueReport",
            fetched_at=NOW,
            parse_status="fetchFailed",
            slot_at_utc=NOW + timedelta(hours=2),
        )
        session.commit()
        latest = store.latest_snapshot(session)
        assert latest is not None and latest.snapshot_id == newer.snapshot_id
        # the newest *seen* slot counts an unreadable report (it was fetched) but not a failure
        assert store.newest_fetched_slot(session) == NOW + timedelta(hours=1)
        found = store.snapshot_with_sha(session, "1" * 64)
        assert found is not None and found.snapshot_id == older.snapshot_id
        assert store.snapshot_with_sha(session, "9" * 64) is None
        with pytest.raises(ValueError):
            store.insert_snapshot(
                session, source_kind="leagueReport", fetched_at=NOW, parse_status="great"
            )


def test_clip_makes_one_bounded_line() -> None:
    assert store.clip(None) is None
    assert store.clip("a\n  b\tc") == "a b c"
    long = store.clip("x" * 1000, 50)
    assert long is not None and len(long) == 50 and long.endswith("…")


# ------------------------------------------------------------------------- denylist


def test_the_denylist_withholds_the_link_and_keeps_the_label_and_date() -> None:
    deny = status.load_denylist()
    assert {"mozzartsport.com", "mozzartbet.com"} <= set(deny.domains)
    label, url = status.apply_denylist("mozzartsport.com", "https://www.mozzartsport.com/n/1")
    assert url is None and label.endswith("(link withheld: betting operator)")
    assert label.startswith("mozzartsport.com")
    # an ordinary link is untouched
    assert status.apply_denylist("basketnews.com", "https://basketnews.com/x") == (
        "basketnews.com",
        "https://basketnews.com/x",
    )
    # the label is bounded, suffix included
    long_label, _ = status.apply_denylist("L" * 500, "https://mozzartbet.com/x", limit=120)
    assert len(long_label) == 120 and long_label.endswith("(link withheld: betting operator)")
    # a club whose *name* contains the word is not affected: only a URL's host is matched
    assert status.apply_denylist("Partizan Mozzart Bet", None) == ("Partizan Mozzart Bet", None)
    assert status.apply_denylist("Partizan Mozzart Bet", "https://partizan.example.org/a") == (
        "Partizan Mozzart Bet",
        "https://partizan.example.org/a",
    )
    assert status.is_denied("https://sub.mozzartbet.com/x") and not status.is_denied(None)


@pytest.mark.parametrize(
    "url",
    [
        "https://WWW.MozzartSport.com./a",  # upper case and a trailing dot
        "https://mozzartbet.com./promo",
        "https://mozzartsport.com\\@example.org/a",  # a browser opens the operator's site
    ],
)
def test_a_spelling_trick_does_not_get_an_operators_link_stored(url: str) -> None:
    assert status.is_denied(url)
    label, kept = status.apply_denylist("Label", url)
    assert kept is None and label.endswith("(link withheld: betting operator)")


def test_a_withheld_row_satisfies_the_source_url_check(empty_engine: Engine) -> None:
    label, url = status.apply_denylist("Preview site", "https://mozzartsport.com/p")
    assert url is None
    row = _row("status", source_kind="pressArticle", source_label=label, source_url=url)
    assert _accepted(empty_engine, row)


def test_an_unreadable_denylist_is_an_error_not_an_open_door(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(status, "_DATA", tmp_path)  # a directory with no denylist in it
    status.load_denylist.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            status.load_denylist()
        (tmp_path / "source_denylist.json").write_text('{"domains": [], "linkWithheldSuffix": "x"}')
        status.load_denylist.cache_clear()
        with pytest.raises(RuntimeError):
            status.load_denylist()
    finally:
        monkeypatch.undo()
        status.load_denylist.cache_clear()


# ---------------------------------------------------------------- import discipline


def _imports(path: Path) -> set[str]:
    """Every module a file imports, resolved to absolute dotted names where it can be."""
    package = ".".join(path.relative_to(BACKEND).with_suffix("").parts[:-1])
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                module = ".".join([*base, *([node.module] if node.module else [])])
                found.add(module)
                found.update(f"{module}.{alias.name}" for alias in node.names)
            elif node.module:
                found.add(node.module)
    return found


def test_intel_knows_no_database_and_no_league() -> None:
    for path in sorted((PACKAGE / "intel").glob("*.py")):
        for module in _imports(path):
            top = module.split(".")
            if top[0] != "nbastats":
                continue
            assert top[:2] == ["nbastats", "intel"], f"{path.name} imports {module}"


def test_nba_intel_reads_the_stats_tables_by_name_and_never_the_euroleague() -> None:
    allowed = {"intel", "shared", "db", "config", "nba_intel"}
    for path in sorted((PACKAGE / "nba_intel").glob("*.py")):
        for module in _imports(path):
            parts = module.split(".")
            if parts[0] != "nbastats":
                continue
            assert len(parts) >= 2 and parts[1] in allowed, f"{path.name} imports {module}"
            assert "euroleague" not in parts and "models" not in parts[:2]


def test_importing_db_loads_no_feature_package_but_init_db_still_creates_the_tables() -> None:
    """The scheduler imports ``nbastats.db`` and must not load a feature package at import time (a
    missing package is a ``notInstalled`` row, not a startup crash). The import therefore lives
    inside ``init_db``, which is still where the intel tables are created."""
    import subprocess
    import sys

    code = (
        "import sys, nbastats.db;"
        "loaded = sorted(m for m in sys.modules if m.startswith('nbastats.nba_intel'));"
        "print(loaded); sys.exit(1 if loaded else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=BACKEND)
    assert done.returncode == 0, done.stdout + done.stderr
    source = (PACKAGE / "db.py").read_text()
    assert "NbaIntelBase.metadata.create_all(target)" in source
    top_level = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("nba_intel")
    ]
    assert top_level == []


def test_the_package_init_is_import_free_so_db_can_import_the_models() -> None:
    tree = ast.parse((PACKAGE / "nba_intel" / "__init__.py").read_text())
    assert not [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    models_imports = {m.split(".")[0] for m in _imports(PACKAGE / "nba_intel" / "models.py")}
    assert models_imports <= {"__future__", "datetime", "typing", "sqlalchemy"}


@pytest.fixture(autouse=True)
def _restore_settings_cache() -> Iterator[None]:
    yield
    config.reset_settings_cache()


def test_an_ok_state_drops_a_stale_reason_and_a_recovery_resets_the_failure_streak(
    empty_engine: Engine,
) -> None:
    key = "nba.injuryReport"
    with Session(empty_engine) as session:
        store.set_source_state(
            session, key, "disabled", now=NOW, detail={"reason": "Demo league", "label": "X"}
        )
        row = store.get_source_state(session, key)
        assert row is not None and store.source_detail(row)["reason"] == "Demo league"
        store.set_source_state(session, key, "ok", now=NOW, success=True)
        detail = store.source_detail(store.get_source_state(session, key))
        assert "reason" not in detail and detail["label"] == "X"  # only the stale reason goes

        store.set_source_state(session, key, "blocked", now=NOW, error="403")
        store.set_source_state(session, key, "blocked", now=NOW, error="403")
        before = store.get_source_state(session, key)
        assert before is not None and before.consecutive_failures == 2
        last = before.last_success_at
        store.set_source_state(session, key, "ok", now=NOW, reset_failures=True)
        after = store.get_source_state(session, key)
        assert after is not None and after.consecutive_failures == 0 and after.last_error is None
        assert after.last_success_at == last  # a recovery is not a delivery
