"""The EuroLeague engine, its session dependency, the store-identity stamp and the sync cursor.

This module is the EuroLeague's counterpart of ``nbastats/db.py`` and deliberately does **not**
import it. The sealed package may not reach the NBA's engine, its models or its seeder (an AST
test holds that line), so the few things worth sharing, a SQLite pragma listener and a naive
UTC clock, are written out again here. They are a dozen lines each; a shared import would be
the thin end of the coupling the whole design exists to avoid.

One engine per URL
------------------
``get_el_engine()`` caches an engine **per URL** rather than one global engine. The NBA module
keeps a single process-wide engine and every test that points it somewhere else has to remember
to dispose it; here a test (or a user who edits ``HARDWOOD_EL_DATABASE_URL``) simply gets the
engine for the URL it now names. :func:`dispose_el_engine` drops them all.

SQLite connections get WAL journaling and a busy timeout, scoped to the engine instance (never
the ``Engine`` class, which would reach every other engine in the process): the API reads while
the worker writes, and two writers must wait for each other rather than fail with "database is
locked". ``foreign_keys`` stays off for the reason ``models.py`` gives. An in-memory store gets
a ``StaticPool`` so every connection sees the same database, which is what a test expects.

Refusing the wrong file
-----------------------
:func:`init_el_db` creates the tables and first checks, **before creating anything**, that the
file does not already hold a ``teams`` table, the unmistakable sign of an NBA store. Creating
``el_*`` tables in an NBA file would put EuroLeague rows where the NBA seeder and every NBA
query can reach them, which is exactly the leak the separate file prevents. After creating the
tables it also refuses an ``el_store_identity`` row whose ``league`` is not ``euroleague``.
The same-store guard in ``config`` catches the usual cause (one URL in two variables); this is
the backstop for when it was bypassed.

Store identity
--------------
``el_store_identity`` is a singleton stamped when the store is first used, with a ``kind``:

* ``synthetic``: the invented league. Takes demo seeding only.
* ``workbook`` and ``live``: real data. They take workbook imports and live ingest alike,
  because the workbook's box scores came from the same data service the live jobs read, and a
  user who imports a workbook and then lets live ingest add later rounds has one coherent store.

:func:`require_writer` is what the importer, the demo seeder and the live writer call before
touching a row: a synthetic store refuses real writes and a real store refuses demo seeding, so
invented rows and real rows can never meet in one file. Switching means a new file.

The sync cursor
---------------
``el_sync_state`` is the EuroLeague's own freshness cursor (``/v1/el/sync``), independent of the
NBA's: ``sync_version`` goes up once per change to the data, ``data_through`` only ever moves
forward (a back-fill of an old round must not make the store look stale), and ``mode`` says
whether the data is invented, imported or live.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_el_settings, is_memory_url, is_sqlite_url, sqlite_file
from .models import ElBase, ElStoreIdentity, ElSyncState
from .profile import KIND_LIVE, KIND_SYNTHETIC, KIND_WORKBOOK, LEAGUE_KEY, REAL_KINDS, STORE_KINDS

__all__ = [
    "ElStoreError",
    "StoreKindMismatch",
    "utcnow",
    "create_el_engine",
    "get_el_engine",
    "get_el_sessionmaker",
    "dispose_el_engine",
    "get_el_db",
    "el_session_scope",
    "init_el_db",
    "read_identity",
    "ensure_identity",
    "kinds_compatible",
    "require_writer",
    "is_synthetic_store",
    "promote_to_live",
    "holds_league_data",
    "release_empty_real_stamp",
    "read_sync_state",
    "bump_sync_version",
    "sync_mode_for_kind",
]


class ElStoreError(RuntimeError):
    """The EuroLeague store cannot be used as asked; the message says what to change."""


class StoreKindMismatch(ElStoreError):
    """A write of one kind (invented or real) was attempted on a store of the other."""


def utcnow() -> datetime:
    """Naive UTC ``datetime``: every timestamp column stores UTC and renders with a ``Z``."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- engine


def _install_sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def create_el_engine(url: str | None = None, echo: bool = False) -> Engine:
    """Build an engine for ``url`` (default: the configured EuroLeague store).

    The parent directory of a SQLite file is created if it is missing, so a first run into a
    fresh ``HARDWOOD_DATA_DIR`` just works.
    """
    target = url or get_el_settings().database_url
    kwargs: dict[str, object] = {"echo": echo, "future": True}
    if is_sqlite_url(target):
        kwargs["connect_args"] = {"check_same_thread": False}
        if is_memory_url(target):
            kwargs["poolclass"] = StaticPool
        else:
            path = sqlite_file(target)
            if path is not None:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
    else:
        kwargs["pool_pre_ping"] = True
    engine = create_engine(target, **kwargs)
    if is_sqlite_url(target):
        _install_sqlite_pragmas(engine)
    return engine


_engines: dict[str, Engine] = {}
_sessionmakers: dict[str, "sessionmaker[Session]"] = {}
_cache_lock = threading.RLock()


def get_el_engine(url: str | None = None) -> Engine:
    """The cached engine for ``url`` (default: the configured store), created on first use."""
    target = url or get_el_settings().database_url
    with _cache_lock:
        engine = _engines.get(target)
        if engine is None:
            engine = _engines[target] = create_el_engine(target)
        return engine


def get_el_sessionmaker(url: str | None = None) -> "sessionmaker[Session]":
    """The cached session factory for ``url`` (default: the configured store)."""
    target = url or get_el_settings().database_url
    with _cache_lock:
        factory = _sessionmakers.get(target)
        if factory is None:
            factory = _sessionmakers[target] = sessionmaker(
                bind=get_el_engine(target), expire_on_commit=False, future=True
            )
        return factory


def dispose_el_engine() -> None:
    """Drop every cached engine and session factory (tests, or a settings change)."""
    with _cache_lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
        _sessionmakers.clear()


def get_el_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped EuroLeague :class:`Session`.

    The EuroLeague router uses this and never ``nbastats.api.deps.get_db``, so a EuroLeague
    request can never be handed an NBA session. Whether the store is *usable* (enabled, not
    misconfigured) is :mod:`nbastats.euroleague.bootstrap`'s state; the router checks it and
    answers ``503 league_unavailable`` before opening a session.
    """
    session = get_el_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def el_session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Commit on success, roll back on failure. Defaults to the configured store."""
    factory = (
        sessionmaker(bind=engine, expire_on_commit=False, future=True)
        if engine is not None
        else get_el_sessionmaker()
    )
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# --------------------------------------------------------------------------- init


def init_el_db(engine: Engine | None = None) -> Engine:
    """Create every ``el_*`` table that does not exist yet, and return the engine used.

    Refuses (raising :class:`ElStoreError`) a file that holds a ``teams`` table, and an
    ``el_store_identity`` row for another league. Nothing is created in a refused file.
    """
    target = engine or get_el_engine()
    existing = set(inspect(target).get_table_names())
    if "teams" in existing:
        raise ElStoreError(
            "Refusing to create the EuroLeague tables in a database that holds a 'teams' table: "
            "that is an NBA store. Point HARDWOOD_EL_DATABASE_URL at a file of its own."
        )
    ElBase.metadata.create_all(target)
    if "el_store_identity" in existing:
        with target.connect() as connection:
            row = connection.execute(
                text("SELECT league FROM el_store_identity WHERE id = 1")
            ).first()
        if row is not None and row[0] != LEAGUE_KEY:
            raise ElStoreError(
                f"This database is stamped for league {row[0]!r}, not {LEAGUE_KEY!r}; "
                "refusing to use it as the EuroLeague store."
            )
    return target


# --------------------------------------------------------------------------- identity


def read_identity(session: Session) -> ElStoreIdentity | None:
    """The store's stamp, or ``None`` for a store nothing has written to yet."""
    return session.get(ElStoreIdentity, 1)


def kinds_compatible(existing: str, requested: str) -> bool:
    """True when a store of kind ``existing`` may take writes of kind ``requested``.

    Invented data only with invented data; real data (workbook or live) with real data.
    """
    if existing not in STORE_KINDS or requested not in STORE_KINDS:
        return False
    if existing == KIND_SYNTHETIC or requested == KIND_SYNTHETIC:
        return existing == requested
    return existing in REAL_KINDS and requested in REAL_KINDS


def ensure_identity(session: Session, kind: str, now: datetime | None = None) -> ElStoreIdentity:
    """Stamp the store with ``kind`` if it has no identity; otherwise verify it fits.

    Raises :class:`StoreKindMismatch` when the stamp is the other family (invented versus
    real), and :class:`ElStoreError` for another league's stamp or an unknown kind.
    """
    if kind not in STORE_KINDS:
        raise ElStoreError(f"unknown store kind {kind!r}; expected one of {', '.join(STORE_KINDS)}")
    identity = read_identity(session)
    if identity is None:
        identity = ElStoreIdentity(id=1, league=LEAGUE_KEY, kind=kind, created_at=now or utcnow())
        session.add(identity)
        session.flush()
        return identity
    if identity.league != LEAGUE_KEY:
        raise ElStoreError(f"store is stamped for league {identity.league!r}, not {LEAGUE_KEY!r}")
    if not kinds_compatible(identity.kind, kind):
        raise StoreKindMismatch(
            f"This EuroLeague store holds {_family(identity.kind)} data and cannot take "
            f"{_family(kind)} writes. Switching means a new file: set "
            "HARDWOOD_EL_DATABASE_URL to one that does not exist yet."
        )
    return identity


def _family(kind: str) -> str:
    return "invented (demo)" if kind == KIND_SYNTHETIC else "real"


def require_writer(session: Session, kind: str) -> ElStoreIdentity:
    """Call before writing: ``kind`` is ``synthetic`` for the demo seeder, ``workbook`` for the
    importer, ``live`` for the live writer. Stamps a store that has no identity yet."""
    return ensure_identity(session, kind)


def is_synthetic_store(session: Session) -> bool:
    """True when the store holds the invented league. Keyed on the store's own stamp, so a
    hand-edited environment variable can never make real statuses meet invented games."""
    identity = read_identity(session)
    return identity is not None and identity.kind == KIND_SYNTHETIC


def promote_to_live(session: Session) -> ElStoreIdentity | None:
    """Mark a ``workbook`` store as ``live`` once live ingest has written to it.

    Real to real only; a synthetic store is left alone and ``None`` is returned for a store
    with no identity.
    """
    identity = read_identity(session)
    if identity is not None and identity.kind == KIND_WORKBOOK:
        identity.kind = KIND_LIVE
        session.flush()
    return identity


#: Tables whose rows are league data (or a record of having fetched some). A real store with none
#: of them has only been *stamped*: no workbook was imported and live ingest never wrote.
_DATA_TABLES: tuple[str, ...] = (
    "el_season",
    "el_club",
    "el_person",
    "el_game",
    "el_intel_status",
    "el_intel_news_item",
    "el_raw_payload",
    "el_ingest_log",
)


def holds_league_data(session: Session) -> bool:
    """True when the store holds any league data or any record of an import or fetch."""
    existing = set(inspect(session.get_bind()).get_table_names())
    for table in _DATA_TABLES:
        if table in existing and session.execute(
            text(f"SELECT 1 FROM {table} LIMIT 1")  # noqa: S608 - names from the tuple above
        ).first():
            return True
    return False


def release_empty_real_stamp(session: Session) -> bool:
    """Remove the stamp of a ``workbook``/``live`` store that holds no league data at all.

    An app start with live ingest on stamps a new store ``live`` before anything is fetched; if
    the person then asks for the invented demo league, that empty store must not refuse it as
    though it held real data. Only the stamp and the default headline feeds (which a real store
    is seeded with and a demo store never has) are removed; nothing of the person's is. Returns
    whether the stamp was released.
    """
    identity = read_identity(session)
    if identity is None or identity.kind not in REAL_KINDS or holds_league_data(session):
        return False
    session.execute(text("DELETE FROM el_intel_news_feed"))
    session.delete(identity)
    session.flush()
    return True


# --------------------------------------------------------------------------- sync cursor


def sync_mode_for_kind(kind: str | None) -> str:
    """The ``el_sync_state.mode`` that goes with a store kind."""
    if kind == KIND_SYNTHETIC:
        return "demo"
    if kind == KIND_LIVE:
        return "live"
    return "workbook"


def read_sync_state(session: Session) -> ElSyncState:
    """The sync-state singleton, created on first read so callers never see ``None``."""
    state = session.execute(select(ElSyncState).where(ElSyncState.id == 1)).scalar_one_or_none()
    if state is None:
        identity = read_identity(session)
        state = ElSyncState(
            id=1,
            sync_version=0,
            mode=sync_mode_for_kind(identity.kind if identity else None),
        )
        session.add(state)
        session.flush()
    return state


def bump_sync_version(
    session: Session,
    data_through: date | None = None,
    mode: str | None = None,
    now: datetime | None = None,
) -> int:
    """Increment the sync version once and return it. Does not commit.

    ``data_through`` only ever moves forward. A change that touched nothing real should not
    call this at all: a client treats a new version as "something changed".
    """
    state = read_sync_state(session)
    state.sync_version = (state.sync_version or 0) + 1
    state.last_success_at = now or utcnow()
    if data_through is not None and (
        state.data_through is None or data_through > state.data_through
    ):
        state.data_through = data_through
    if mode is not None:
        state.mode = mode
    session.flush()
    return state.sync_version
