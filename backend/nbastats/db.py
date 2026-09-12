"""Engine, session factory and the sync-state singleton.

SQLite is the default store and needs ``check_same_thread=False`` so FastAPI's threadpool can
hand a connection between workers. Pointing ``DATABASE_URL`` at Postgres needs no code change:
the SQLite-only connect-args are applied only for a SQLite URL.

``get_session()`` is a FastAPI-compatible dependency; ``init_db()`` creates every table;
``bump_sync_version()`` and ``read_sync_state()`` own the freshness cursor described in
``CONTRACT.md`` §8.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Iterator

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

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
    """Build an engine for ``url`` (default: ``DATABASE_URL``)."""
    settings = get_settings()
    target = url or settings.database_url
    kwargs: dict[str, object] = {"echo": echo, "future": True}
    if target.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # Postgres and friends: keep a modest pool and recycle connections the proxy may drop.
        kwargs["pool_pre_ping"] = True
    return create_engine(target, **kwargs)


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
    """Create every table that does not exist yet, and return the engine used."""
    target = engine or get_engine()
    Base.metadata.create_all(target)
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
