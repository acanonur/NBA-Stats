"""Engine, session factory and the sync-state singleton.

SQLite is the default store and needs ``check_same_thread=False`` so FastAPI's threadpool can
hand a connection between workers. Pointing ``DATABASE_URL`` at Postgres needs no code change:
the SQLite-only connect-args are applied only for a SQLite URL.

``get_session()`` is a FastAPI-compatible dependency; ``init_db()`` creates every table;
``bump_sync_version()`` and ``read_sync_state()`` own the freshness cursor described in
``CONTRACT.md`` §8.

Two SQLite pragmas are set on every connection this module opens (see
``_install_sqlite_pragmas`` below): ``journal_mode=WAL`` and a ``busy_timeout``, because two
browser tabs saving a dashboard at the same moment would otherwise trip "database is locked".
Both are scoped to the one ``Engine`` *instance* this module builds —
``event.listens_for(engine, "connect")`` — never to the SQLAlchemy ``Engine`` class, which
would reach every other engine in the process including each test's own throwaway one.

``foreign_keys=ON`` is deliberately **not** among them, though the account tables declare
``ON DELETE CASCADE``. That pragma is per *connection*, not per table: switching it on to serve
six new tables would also switch it on for the fourteen older ones, whose constraints SQLite has
silently ignored since the schema was written. Twenty-three existing tests build a deliberately
minimal store — a ``games`` row with no ``teams`` row — and would begin failing for a
reason that has nothing to do with accounts. Enforcing referential integrity across the stats
schema may well be worth doing; it is its own change, with its own fixture work, and it is not a
side effect the account system gets to impose. Account rows therefore cascade at the ORM layer
instead (``cascade="all, delete-orphan"`` on each relationship, plus the explicit sweep in
``accounts/store.py``), which holds whatever the pragma says.

Why this module imports :mod:`nbastats.accounts.models`, and only in this direction
--------------------------------------------------------------------------------------
``init_db()`` has to create the account tables too, so it needs :class:`~nbastats.accounts.
models.AccountBase` imported (which registers its tables on its own ``MetaData``) before
``create_all()`` runs. That import goes ``db.py -> accounts/models.py`` and never the other
way: ``accounts/models.py`` imports nothing from this module or from ``nbastats.models`` — it
only uses SQLAlchemy types, exactly like ``nbastats/models.py`` itself, which has never needed
to import ``db.py`` either. Every row that needs "now", in either schema, gets it from an
explicit ``nbastats.db.utcnow()`` call made by whichever module inserts the row (``seed.py``,
``routes_dashboard.py``, ``ingest/*.py`` today; ``accounts/sessions.py`` and ``accounts/
store.py`` once WP1/WP2 land), never from a column-level default. That convention — not a
special case for accounts — is what keeps this a clean one-way import with nothing pulling
back.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from .accounts.models import AccountBase
from .config import get_settings
from .models import Base, SyncState

__all__ = [
    "create_db_engine",
    "get_engine",
    "get_sessionmaker",
    "get_session",
    "session_scope",
    "init_db",
    "dispose_engine",
    "read_sync_state",
    "bump_sync_version",
    "utcnow",
]

_engine: Engine | None = None
_sessionmaker: sessionmaker[Session] | None = None


def utcnow() -> datetime:
    """Naive UTC ``datetime`` — every timestamp column stores UTC and renders with a ``Z``."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_db_engine(url: str | None = None, echo: bool = False) -> Engine:
    """Build an engine for ``url`` (default: ``DATABASE_URL``).

    For a SQLite URL this also turns on WAL journaling for this engine specifically — see
    the module docstring, including why foreign-key enforcement is not among the pragmas.
    """
    settings = get_settings()
    target = url or settings.database_url
    is_sqlite = target.startswith("sqlite")
    kwargs: dict[str, object] = {"echo": echo, "future": True}
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # Postgres and friends: keep a modest pool and recycle connections the proxy may drop.
        kwargs["pool_pre_ping"] = True
    engine = create_engine(target, **kwargs)
    if is_sqlite:
        _install_sqlite_pragmas(engine)
    return engine


def _install_sqlite_pragmas(engine: Engine) -> None:
    """Turn on WAL journaling and a busy timeout for exactly this SQLite engine.

    Scoped with ``event.listens_for(engine, ...)`` rather than the global ``Engine`` class —
    see the module docstring for why a class-level listener is the wrong tool here, and why
    ``foreign_keys`` is not set.
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def get_engine() -> Engine:
    """Process-wide engine, created on first use."""
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    """Process-wide session factory bound to :func:`get_engine`."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _sessionmaker


def dispose_engine() -> None:
    """Drop the cached engine and session factory (tests, or a settings change)."""
    global _engine, _sessionmaker
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _sessionmaker = None


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped :class:`Session`."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Context manager that commits on success and rolls back on failure."""
    factory = (
        sessionmaker(bind=engine, expire_on_commit=False, future=True)
        if engine is not None
        else get_sessionmaker()
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


def init_db(engine: Engine | None = None) -> Engine:
    """Create every table that does not exist yet — both metadata objects — and return the
    engine used.

    ``AccountBase`` is a separate ``MetaData`` from ``Base`` on purpose: see
    ``nbastats/accounts/models.py`` for why account tables must never share the metadata that
    ``nbastats/seed.py::_clear()`` wipes on every seed run.
    """
    target = engine or get_engine()
    Base.metadata.create_all(target)
    AccountBase.metadata.create_all(target)
    return target


def read_sync_state(session: Session) -> SyncState:
    """The sync-state singleton, created on first read so callers never see ``None``."""
    state = session.execute(select(SyncState).where(SyncState.id == 1)).scalar_one_or_none()
    if state is None:
        state = SyncState(id=1, sync_version=0, games_ingested=0)
        session.add(state)
        session.flush()
    return state


def bump_sync_version(
    session: Session,
    data_through: date | None = None,
    note: str | None = None,
    games_ingested: int | None = None,
    commit: bool = True,
) -> int:
    """Increment the sync version and return the new value.

    Per ``CONTRACT.md`` §8 this happens once per finalized game. ``data_through`` only ever
    moves forward, so a back-fill of an older date cannot make the service look stale.
    """
    state = read_sync_state(session)
    state.sync_version = (state.sync_version or 0) + 1
    now = utcnow()
    state.last_run_at = now
    state.last_success_at = now
    moves_forward = state.data_through is None or (
        data_through is not None and data_through > state.data_through
    )
    if data_through is not None and moves_forward:
        state.data_through = data_through
    if games_ingested is not None:
        state.games_ingested = games_ingested
    if note:
        state.notes = note
    session.flush()
    if commit:
        session.commit()
    return state.sync_version
