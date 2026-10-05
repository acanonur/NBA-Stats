"""The EuroLeague must never share a database with the NBA.

Three layers, each tested on its own:

* ``config.same_store`` resolves two URLs to the same database (a symlink, a relative path and an
  absolute one are all the same SQLite file; server URLs compare without credentials);
* ``bootstrap.prepare`` turns equal URLs into state ``misconfigured`` and opens nothing, leaving
  the NBA untouched;
* ``db.init_el_db`` independently refuses a file that already holds a ``teams`` table or another
  league's identity, before creating a single table, for the day the guard is bypassed.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest
from sqlalchemy import inspect

from nbastats.config import Settings
from nbastats.euroleague import bootstrap
from nbastats.euroleague.config import (
    EL_DB_FILENAME,
    ElSettings,
    check_same_store,
    default_data_dir,
    default_el_database_url,
    inbox_workbooks,
    is_memory_url,
    same_store,
    sqlite_file,
)
from nbastats.euroleague.db import ElStoreError, create_el_engine, init_el_db
from nbastats.euroleague.models import EL_TABLE_NAMES

# --------------------------------------------------------------------------- same_store


@pytest.fixture(autouse=True)
def _fresh_bootstrap_state() -> Iterator[None]:
    """``bootstrap`` remembers its last outcome in the process; none of it may leak out."""
    bootstrap.reset_state()
    yield
    bootstrap.reset_state()


def test_one_file_spelled_three_ways_is_one_store(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    absolute = f"sqlite:///{tmp_path / 'hardwood.db'}"
    dotted = f"sqlite:///{tmp_path}/sub/../hardwood.db"
    assert same_store(absolute, dotted)
    link = tmp_path / "link.db"
    link.symlink_to(tmp_path / "hardwood.db")
    assert same_store(absolute, f"sqlite:///{link}")
    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        assert same_store(absolute, "sqlite:///./hardwood.db")
        assert same_store(absolute, "sqlite:///hardwood.db")
    finally:
        os.chdir(previous)


def test_different_files_are_different_stores(tmp_path: Path) -> None:
    assert not same_store(f"sqlite:///{tmp_path / 'a.db'}", f"sqlite:///{tmp_path / 'b.db'}")
    (tmp_path / "x").mkdir()
    assert not same_store(f"sqlite:///{tmp_path / 'a.db'}", f"sqlite:///{tmp_path / 'x' / 'a.db'}")


def test_two_in_memory_stores_are_never_the_same(tmp_path: Path) -> None:
    for first in ("sqlite://", "sqlite:///:memory:"):
        for second in ("sqlite://", "sqlite:///:memory:", f"sqlite:///{tmp_path / 'a.db'}"):
            assert not same_store(first, second)
    assert is_memory_url("sqlite://") and is_memory_url("sqlite:///:memory:")
    assert not is_memory_url(f"sqlite:///{tmp_path / 'a.db'}")
    assert not is_memory_url("postgresql://u@h/db")


def test_server_urls_compare_without_credentials() -> None:
    assert same_store(
        "postgresql://alice:secret@db.example/hardwood", "postgresql://bob@db.example/hardwood"
    )
    assert same_store("postgresql://db.example/hardwood", "postgresql://u:p@db.example/hardwood")
    assert not same_store("postgresql://db.example/hardwood", "postgresql://db.example/hardwood_el")
    assert not same_store("postgresql://db.example/hardwood", "postgresql://other.example/hardwood")


def test_a_file_store_is_never_a_server_store(tmp_path: Path) -> None:
    assert not same_store(f"sqlite:///{tmp_path / 'a.db'}", "postgresql://db.example/hardwood")
    assert sqlite_file("postgresql://db.example/hardwood") is None
    assert sqlite_file("sqlite://") is None
    assert sqlite_file(f"sqlite:///{tmp_path / 'a.db'}") == (tmp_path / "a.db").resolve()


# --------------------------------------------------------------------------- settings resolution


def _settings(tmp_path: Path, **env: str | None) -> ElSettings:
    environ: dict[str, str] = {"DATABASE_URL": f"sqlite:///{tmp_path / 'hardwood.db'}"}
    for key, value in env.items():
        if value is None:
            environ.pop(key, None)
        else:
            environ[key] = value
    return ElSettings.from_env(environ, nba_settings=Settings.from_env(environ))


def test_the_default_store_sits_beside_the_nba_file(tmp_path: Path) -> None:
    """``hardwood.db`` pairs with ``hardwood_el.db``, and the worker resolves it the same way."""
    sibling = _settings(tmp_path)
    assert sibling.database_url == f"sqlite:///{tmp_path / EL_DB_FILENAME}"
    assert not sibling.database_url_explicit and check_same_store(sibling) is None
    data = _settings(tmp_path, HARDWOOD_DATA_DIR=str(tmp_path / "data"))
    assert data.database_url == sibling.database_url  # the data dir does not move a sibling
    assert data.inbox_dir == tmp_path / "data" / "inbox"
    other = _settings(tmp_path, DATABASE_URL=f"sqlite:///{tmp_path / 'nba.db'}")
    assert other.database_url == f"sqlite:///{tmp_path / 'nba_el.db'}"
    explicit = _settings(
        tmp_path,
        HARDWOOD_EL_DATABASE_URL=f"sqlite:///{tmp_path / 'mine.db'}",
        HARDWOOD_DATA_DIR=str(tmp_path / "data"),
    )
    assert explicit.database_url == f"sqlite:///{tmp_path / 'mine.db'}"
    assert explicit.database_url_explicit


def test_a_server_nba_store_puts_the_euroleague_in_the_data_dir() -> None:
    data = Path("/srv/hardwood")
    assert (
        default_el_database_url("postgresql://db/x", data) == f"sqlite:///{data}/{EL_DB_FILENAME}"
    )
    assert default_el_database_url("sqlite://", None) == "sqlite://"
    environ = {"HARDWOOD_DATA_DIR": "/elsewhere"}
    assert default_el_database_url("postgresql://db/x", None, environ) == (
        f"sqlite:////elsewhere/{EL_DB_FILENAME}"
    )
    assert default_data_dir({"HARDWOOD_DATA_DIR": "/elsewhere"}) == Path("/elsewhere")
    assert default_data_dir({"XDG_DATA_HOME": "/xdg"}) in (
        Path("/xdg/hardwood"),
        Path.home() / "Library" / "Application Support" / "Hardwood",
    )


def test_the_legacy_mode_switch_is_read_so_the_api_and_the_worker_agree(tmp_path: Path) -> None:
    assert _settings(tmp_path, HARDWOOD_EL_MODE="off").enabled is False
    assert _settings(tmp_path, HARDWOOD_EL_MODE="OFF", HARDWOOD_EL_ENABLED="1").enabled is False
    workbook = _settings(tmp_path, HARDWOOD_EL_MODE="workbook")
    assert workbook.enabled is True and workbook.live is False and workbook.mode == "workbook"
    demo = _settings(tmp_path, HARDWOOD_EL_MODE="demo")
    assert (demo.enabled, demo.live, demo.demo) == (True, False, True)
    assert _settings(tmp_path, HARDWOOD_EL_MODE="demo", HARDWOOD_EL_DEMO="0").demo is False
    live = _settings(tmp_path, HARDWOOD_EL_MODE="live")
    assert live.live is True and live.mode == "live"
    unknown = _settings(tmp_path, HARDWOOD_EL_MODE="sideways")  # ignored, as the worker ignores it
    assert unknown.mode is None and unknown.enabled and unknown.live
    assert _settings(tmp_path).mode is None


def test_equal_urls_are_refused_with_a_reason(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'hardwood.db'}"
    settings = _settings(tmp_path, HARDWOOD_EL_DATABASE_URL=url)
    reason = check_same_store(settings)
    assert reason is not None and "HARDWOOD_EL_DATABASE_URL" in reason and "never share" in reason


def test_the_switches_and_their_defaults(tmp_path: Path) -> None:
    default = _settings(tmp_path)
    assert (default.enabled, default.live, default.demo, default.include_estimates) == (
        True,
        True,
        False,
        True,
    )
    off = _settings(
        tmp_path,
        HARDWOOD_EL_ENABLED="0",
        HARDWOOD_EL_LIVE="off",
        HARDWOOD_EL_INCLUDE_ESTIMATES="no",
    )
    assert (off.enabled, off.live, off.include_estimates) == (False, False, False)
    assert _settings(tmp_path, HARDWOOD_DEMO_MODE="1").demo is True  # follows the NBA's demo switch
    assert _settings(tmp_path, HARDWOOD_DEMO_MODE="1", HARDWOOD_EL_DEMO="0").demo is False
    assert _settings(tmp_path, HARDWOOD_EL_DEMO="yes").demo is True
    with pytest.raises(ValueError, match="HARDWOOD_EL_LIVE"):
        _settings(tmp_path, HARDWOOD_EL_LIVE="maybe")


def test_there_is_no_terms_setting(tmp_path: Path) -> None:
    fields = set(ElSettings.__dataclass_fields__)
    assert not [f for f in fields if "terms" in f]
    noisy = _settings(
        tmp_path,
        HARDWOOD_EL_TERMS_OUTCOME="denied",
        HARDWOOD_EL_TERMS_URL="x",
        HARDWOOD_EL_TERMS_REVIEWED_ON="never",
    )
    assert noisy == _settings(tmp_path)  # a leftover terms variable has no effect at all


def test_the_inbox_lists_workbooks_oldest_first_and_skips_lock_files(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for name, mtime in (
        ("b.xlsx", 200),
        ("a.xlsx", 100),
        ("c.XLSX", 300),
        ("~$a.xlsx", 50),
        (".hidden.xlsx", 50),
        ("notes.txt", 10),
    ):
        path = inbox / name
        path.write_bytes(b"x")
        os.utime(path, (mtime, mtime))
    (inbox / "folder.xlsx").mkdir()
    assert [p.name for p in inbox_workbooks(inbox)] == ["a.xlsx", "b.xlsx", "c.XLSX"]
    assert inbox_workbooks(None) == [] and inbox_workbooks(tmp_path / "missing") == []


# --------------------------------------------------------------------------- bootstrap


def test_prepare_refuses_to_share_the_nba_file_and_opens_nothing(tmp_path: Path) -> None:
    nba = tmp_path / "hardwood.db"
    nba.write_bytes(b"")
    environ = {
        "DATABASE_URL": f"sqlite:///{nba}",
        "HARDWOOD_EL_DATABASE_URL": f"sqlite:///{nba}",
        "HARDWOOD_EL_DEMO": "1",
    }
    settings = ElSettings.from_env(environ, nba_settings=Settings.from_env(environ))
    result = bootstrap.prepare(settings)
    assert result.state == bootstrap.STATE_MISCONFIGURED and not result.ready
    assert "same database" in (result.reason or "")
    assert nba.read_bytes() == b""  # the NBA's file was not touched
    assert bootstrap.get_state() == result
    with pytest.raises(ElStoreError, match="same database"):
        bootstrap.open_store(settings)


def test_prepare_turns_an_nba_file_at_another_path_into_an_error_state(tmp_path: Path) -> None:
    """The URLs differ, so the same-store guard passes; the file itself is still an NBA store."""
    other = tmp_path / "somebody_elses.db"
    connection = sqlite3.connect(other)
    connection.execute("CREATE TABLE teams (team_id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    environ = {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'hardwood.db'}",
        "HARDWOOD_EL_DATABASE_URL": f"sqlite:///{other}",
        "HARDWOOD_EL_DEMO": "1",
    }
    settings = ElSettings.from_env(environ, nba_settings=Settings.from_env(environ))
    result = bootstrap.prepare(settings)
    assert result.state == bootstrap.STATE_ERROR and "NBA store" in (result.reason or "")
    assert set(inspect(create_el_engine(settings.database_url)).get_table_names()) == {"teams"}


def test_prepare_proceeds_when_the_stores_differ(
    tmp_path: Path, el_env: Callable[..., Any]
) -> None:
    _, settings = el_env(HARDWOOD_EL_DEMO="1")
    result = bootstrap.prepare(settings)
    assert result.ready and result.kind == "synthetic"


def test_importing_by_hand_is_refused_too(tmp_path: Path, mini_league: Any) -> None:
    nba = tmp_path / "hardwood.db"
    environ = {"DATABASE_URL": f"sqlite:///{nba}", "HARDWOOD_EL_DATABASE_URL": f"sqlite:///{nba}"}
    settings = ElSettings.from_env(environ, nba_settings=Settings.from_env(environ))
    path = tmp_path / "w.xlsx"
    path.write_bytes(mini_league.workbook())
    report = bootstrap.run_workbook_import(path, settings=settings)
    assert report.status == "failed" and "same database" in (report.error or "")
    assert not nba.exists()


# --------------------------------------------------------------------------- init_el_db backstop


def test_init_refuses_a_file_that_holds_an_nba_teams_table(tmp_path: Path) -> None:
    path = tmp_path / "nba_like.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE teams (team_id INTEGER PRIMARY KEY, abbr TEXT)")
    connection.execute("INSERT INTO teams VALUES (1610612737, 'ATL')")
    connection.commit()
    connection.close()
    engine = create_el_engine(f"sqlite:///{path}")
    with pytest.raises(ElStoreError, match="NBA store"):
        init_el_db(engine)
    assert set(inspect(engine).get_table_names()) == {"teams"}  # nothing was created in it
    assert not set(EL_TABLE_NAMES) & set(inspect(engine).get_table_names())


def test_the_nba_side_refuses_a_euroleague_store_and_creates_nothing(tmp_path: Path) -> None:
    """The mirror of the guard above: ``init_db`` and the seeder refuse a EuroLeague file, so a
    mistyped ``DATABASE_URL`` can never put the NBA schema (or the demo league) in it, after
    which the EuroLeague would refuse its own store for good."""
    from sqlalchemy.orm import Session

    from nbastats import seed
    from nbastats.db import NbaStoreError, create_db_engine, init_db

    url = f"sqlite:///{tmp_path / 'hardwood_el.db'}"
    init_el_db(create_el_engine(url))
    before = set(inspect(create_el_engine(url)).get_table_names())
    engine = create_db_engine(url)
    with pytest.raises(NbaStoreError, match="EuroLeague store"):
        init_db(engine)
    with Session(engine) as session, pytest.raises(NbaStoreError):
        seed.seed_database(session, games_per_team=2, players_per_team=5)
    assert seed.main(["--db", url, "--quiet"]) == 2
    assert set(inspect(create_el_engine(url)).get_table_names()) == before
    init_el_db(create_el_engine(url))  # and the EuroLeague still opens its own store
    engine.dispose()


def test_init_refuses_another_leagues_identity(tmp_path: Path) -> None:
    path = tmp_path / "other.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE el_store_identity (id INTEGER PRIMARY KEY, league TEXT, kind TEXT, "
        "created_at TEXT)"
    )
    connection.execute("INSERT INTO el_store_identity VALUES (1, 'nba', 'live', '2026-01-01')")
    connection.commit()
    connection.close()
    with pytest.raises(ElStoreError, match="stamped for league 'nba'"):
        init_el_db(create_el_engine(f"sqlite:///{path}"))


def test_init_is_idempotent_and_accepts_its_own_file(tmp_path: Path) -> None:
    engine = create_el_engine(f"sqlite:///{tmp_path / 'el.db'}")
    init_el_db(engine)
    init_el_db(engine)
    assert set(EL_TABLE_NAMES) <= set(inspect(engine).get_table_names())


def test_the_store_directory_is_created_on_first_use(tmp_path: Path) -> None:
    engine = create_el_engine(f"sqlite:///{tmp_path / 'new' / 'dir' / 'el.db'}")
    init_el_db(engine)
    assert (tmp_path / "new" / "dir" / "el.db").exists()


def test_the_engine_has_wal_and_a_busy_timeout(tmp_path: Path) -> None:
    from sqlalchemy import text

    engine = create_el_engine(f"sqlite:///{tmp_path / 'el.db'}")
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 5000
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 0


def test_an_in_memory_store_shares_one_database_across_connections() -> None:
    from sqlalchemy import text

    engine = create_el_engine("sqlite://")
    init_el_db(engine)
    with engine.connect() as one:
        one.execute(
            text("INSERT INTO el_store_identity VALUES (1, 'euroleague', 'live', '2026-01-01')")
        )
        one.commit()
    with engine.connect() as two:
        assert two.execute(text("SELECT COUNT(*) FROM el_store_identity")).scalar() == 1
