"""The EuroLeague store survives everything the NBA seeder does.

``nbastats/seed.py`` clears every table on ``Base.metadata`` with no WHERE clause, and
``api/app.py`` runs it on boot whenever ``HARDWOOD_DEMO_MODE`` is set and ``teams`` is empty.
The EuroLeague's tables live on their own metadata in their own file, so neither can reach
them. These tests prove that empirically, for the invented league and for a real
(workbook-imported) store, and prove the reverse: the NBA file never grows an ``el_`` table.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session

from nbastats import config
from nbastats import db as nba_db
from nbastats.euroleague import bootstrap
from nbastats.euroleague.db import create_el_engine, init_el_db
from nbastats.euroleague.importers.workbook import import_workbook
from nbastats.euroleague.models import EL_TABLE_NAMES
from nbastats.models import Base
from nbastats.seed import seed_database

SMALL_SEED = dict(as_of=date(2026, 3, 1), seasons=["2025-26"], games_per_team=4, players_per_team=8)


def counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            name: connection.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar_one()
            for name in EL_TABLE_NAMES
        }


@pytest.fixture()
def nba_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = nba_db.create_db_engine(f"sqlite:///{tmp_path / 'nba.db'}")
    nba_db.init_db(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _fresh_bootstrap_state() -> Iterator[None]:
    """``bootstrap`` remembers its last outcome in the process; none of it may leak out."""
    bootstrap.reset_state()
    yield
    bootstrap.reset_state()


def test_reseeding_the_nba_does_not_touch_the_invented_league(
    fresh_demo_engine: Engine, nba_engine: Engine
) -> None:
    before = counts(fresh_demo_engine)
    assert before["el_game"] == 50 and before["el_player_game"] > 900
    for _ in range(2):  # twice: the seeder replaces, never accumulates
        with Session(nba_engine) as session:
            seed_database(session, **SMALL_SEED)
            session.commit()
    assert counts(fresh_demo_engine) == before


def test_reseeding_the_nba_does_not_touch_a_real_store(
    el_engine: Engine, nba_engine: Engine, mini_league: Any
) -> None:
    report = import_workbook(mini_league.workbook(), el_engine, crosswalk=mini_league.crosswalk)
    assert report.status == "ok"
    before = counts(el_engine)
    assert before["el_player_game"] > 0 and before["el_intel_status"] == 7
    with Session(nba_engine) as session:
        seed_database(session, **SMALL_SEED)
        session.commit()
    assert counts(el_engine) == before


def test_the_seeder_cannot_see_the_euroleague_tables() -> None:
    cleared = {table.name for table in Base.metadata.sorted_tables}
    assert not cleared & set(EL_TABLE_NAMES)
    assert not any(name.startswith("el_") for name in cleared)


def test_the_nba_file_never_gains_a_euroleague_table(
    fresh_demo_engine: Engine, nba_engine: Engine
) -> None:
    with Session(nba_engine) as session:
        seed_database(session, **SMALL_SEED)
        session.commit()
    nba_tables = set(inspect(nba_engine).get_table_names())
    assert nba_tables and not nba_tables & set(EL_TABLE_NAMES)
    el_tables = set(inspect(fresh_demo_engine).get_table_names())
    assert "teams" not in el_tables and "games" not in el_tables


def test_booting_the_nba_in_demo_mode_leaves_the_euroleague_alone(
    tmp_path: Path, fresh_demo_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_prepare_database`` seeds an empty NBA store on boot; the EuroLeague must not notice."""
    from nbastats import seed as seed_module
    from nbastats.api import app as app_module

    real_seed = seed_module.seed_database

    def small_seed(session: Session, *args: Any, **kwargs: Any) -> Any:
        """The real seeder (and its unconditional clear), at the size the other tests use."""
        return real_seed(session, **SMALL_SEED)

    monkeypatch.setattr(seed_module, "seed_database", small_seed)

    nba_url = f"sqlite:///{tmp_path / 'boot_nba.db'}"
    el_url = str(fresh_demo_engine.url)
    monkeypatch.setenv("DATABASE_URL", nba_url)
    monkeypatch.setenv("HARDWOOD_DEMO_MODE", "1")
    monkeypatch.setenv("HARDWOOD_EL_DATABASE_URL", el_url)
    monkeypatch.setenv("HARDWOOD_EL_DEMO", "1")
    config.reset_settings_cache()
    nba_db.dispose_engine()
    try:
        before = counts(fresh_demo_engine)
        app_module._prepare_database()  # empty teams table + demo mode: the NBA seeds itself
        with Session(nba_db.get_engine()) as session:
            assert session.execute(text("SELECT COUNT(*) FROM teams")).scalar_one() > 0
        assert counts(fresh_demo_engine) == before
        # and a second boot, with teams now present, changes nothing either
        app_module._prepare_database()
        assert counts(fresh_demo_engine) == before
    finally:
        nba_db.dispose_engine()
        config.reset_settings_cache()


def test_an_el_store_can_be_rebuilt_without_the_nba(tmp_path: Path) -> None:
    """The two stores are independent files with independent engines."""
    first = create_el_engine(f"sqlite:///{tmp_path / 'one.db'}")
    second = create_el_engine(f"sqlite:///{tmp_path / 'two.db'}")
    init_el_db(first)
    init_el_db(second)
    assert set(inspect(first).get_table_names()) == set(inspect(second).get_table_names())
    assert first.url != second.url
