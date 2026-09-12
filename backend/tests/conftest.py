"""Shared fixtures: one small, deterministic, seeded database for the whole suite.

The seed is a reduced version of the demo league — three seasons, a 24-game schedule, a
mid-season ``as_of`` — so it still exercises every path the API layer cares about (a
pre-advanced era, a completed season with playoffs, an in-progress season with scheduled
games ahead of it) while staying quick enough to build once per test session.

Fixtures other modules rely on, by name:

``seeded_engine``  a SQLAlchemy ``Engine`` pointed at the seeded SQLite file (session scope)
``seeded_db``      a ``Session`` on that engine, one per test
``seed_summary``   the dict :func:`nbastats.seed.seed_database` returned

While the session fixture is alive, ``DATABASE_URL`` points at the seeded file and the
process-wide engine cache is cleared, so anything built through :func:`nbastats.db.get_engine`
sees the same database. The previous value is restored on teardown.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any, Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from nbastats import config
from nbastats import db as db_module
from nbastats.db import create_db_engine, init_db
from nbastats.seed import seed_database

#: A pre-advanced season, a completed modern season with playoffs, and one in progress.
SEED_SEASONS: tuple[str, ...] = ("1992-93", "2024-25", "2025-26")

#: "Today" for the in-progress season. Late enough that qualified players clear the
#: 15-game minimum the preset leaderboards use, early enough to leave scheduled games.
SEED_AS_OF = date(2026, 3, 1)

SEED_GAMES_PER_TEAM = 24
SEED_PLAYERS_PER_TEAM = 10

#: First season with per-game advanced box scores, plus/minus and shot charts. Anything
#: before this has no advanced rows at all, which several tests rely on.
ADVANCED_FROM_SEASON = "1996-97"


@pytest.fixture(scope="session")
def seeded_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Engine, dict]]:
    """Build the seeded SQLite database once for the whole test session."""
    path = tmp_path_factory.mktemp("hardwood") / "hardwood-test.db"
    url = f"sqlite:///{path}"

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    config.reset_settings_cache()
    db_module.dispose_engine()

    engine = create_db_engine(url)
    init_db(engine)
    with Session(engine, future=True) as session:
        summary = seed_database(
            session,
            as_of=SEED_AS_OF,
            seasons=list(SEED_SEASONS),
            games_per_team=SEED_GAMES_PER_TEAM,
            players_per_team=SEED_PLAYERS_PER_TEAM,
        )
        session.commit()

    try:
        yield engine, summary
    finally:
        engine.dispose()
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        config.reset_settings_cache()
        db_module.dispose_engine()


@pytest.fixture(scope="session")
def seeded_engine(seeded_database: tuple[Engine, dict]) -> Engine:
    """Engine bound to the seeded database."""
    return seeded_database[0]


@pytest.fixture(scope="session")
def seed_summary(seeded_database: tuple[Engine, dict]) -> dict[str, Any]:
    """Summary returned by the seeder: row counts, ``data_through``, ``sync_version``."""
    return seeded_database[1]


@pytest.fixture()
def seeded_db(seeded_engine: Engine) -> Iterator[Session]:
    """A read-oriented session on the seeded database, rolled back after each test."""
    session = Session(seeded_engine, future=True)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def empty_engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    """A fresh, empty database with the schema created — for write-path tests."""
    path = tmp_path_factory.mktemp("hardwood-empty") / "empty.db"
    engine = create_db_engine(f"sqlite:///{path}")
    init_db(engine)
    try:
        yield engine
    finally:
        engine.dispose()
