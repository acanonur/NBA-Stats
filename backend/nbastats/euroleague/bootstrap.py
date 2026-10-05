"""What happens to the EuroLeague at start-up: guard, stamp, create, seed or import.

``api/app.py`` calls :func:`prepare` once, inside the main lifespan and inside a ``try``, right
after the NBA schema is created. The EuroLeague is a router and not a mounted sub-app, so there
is no second lifespan to wait for; and because the call is wrapped, a failure here only turns
the EuroLeague off (state ``error``, with the reason) and can never stop the NBA from serving.
:func:`prepare` itself never raises for the same reason.

The decision it makes
---------------------
1. ``HARDWOOD_EL_ENABLED=0``: state ``disabled``. Nothing is opened.
2. The EuroLeague store and the NBA store are the same database: state ``misconfigured``, with
   the reason. The NBA is untouched and ``/v1/el/*`` answers 503 ``league_unavailable``.
3. Otherwise the store is opened (created if it does not exist) and stamped. The kind of a new
   store follows what the user asked for, in this order: a workbook (``HARDWOOD_WORKBOOK_PATH``
   or a ``.xlsx`` in the inbox) makes a real ``workbook`` store; the demo switch makes the
   invented ``synthetic`` store; live ingest on makes a real ``live`` store; with none of those
   there is nothing to hold, so state ``notConfigured`` and no file is created. An *existing*
   store keeps the kind it was stamped with, whatever the switches say now: a real store is
   never given the demo league and a synthetic store never takes the user's workbook (the
   result says so and names the file to replace).
4. A synthetic store is seeded with the invented league if it is empty. A real store imports
   the pending workbooks: the configured one first, then every ``.xlsx`` in the inbox, oldest
   first. The import is idempotent by file hash, so a restart re-imports nothing; a file that
   failed once is not retried on every start (its error is logged and the file waits to be
   changed or imported by hand).

Why the workbook import runs here
---------------------------------
The user's contract is "drop a workbook in the inbox and it is there". An import takes a second
or two, so doing it at start-up is cheaper than a second process, and :func:`import_pending_
workbooks` is a plain function the worker can call on a timer to pick up a file dropped while the
service is running.

News feeds
----------
A real store is given the two candidate headline feeds, enabled, if the table has no feed yet.
Nothing is fetched here. The fetch job checks ``robots.txt`` automatically before every fetch
and disables a feed that disallows it, with the reason on ``/v1/el/sources``. A synthetic store
gets no feeds: the demo never touches the network.

The state
---------
:func:`get_state` returns the last :class:`BootstrapResult`, which the router reads to decide
between serving and ``503 league_unavailable``, and which ``/v1/el/meta`` and ``/sources``
report. Before :func:`prepare` has run it is ``notPrepared``; the router treats that like any
other state that is not ``ready``.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session

from .config import ElSettings, check_same_store, get_el_settings, inbox_workbooks
from .db import (
    ElStoreError,
    StoreKindMismatch,
    create_el_engine,
    ensure_identity,
    get_el_engine,
    init_el_db,
    read_identity,
    read_sync_state,
    release_empty_real_stamp,
    utcnow,
)
from .demo import seed_demo
from .importers import xlsx as _xlsx
from .importers.workbook import WorkbookReport, import_workbook, last_import_failed
from .models import ElIntelNewsFeed
from .profile import KIND_LIVE, KIND_SYNTHETIC, KIND_WORKBOOK, REAL_KINDS

__all__ = [
    "STATE_READY",
    "STATE_DISABLED",
    "STATE_MISCONFIGURED",
    "STATE_NOT_CONFIGURED",
    "STATE_ERROR",
    "STATE_NOT_PREPARED",
    "DEFAULT_NEWS_FEEDS",
    "BootstrapResult",
    "prepare",
    "get_state",
    "set_state",
    "reset_state",
    "open_store",
    "import_pending_workbooks",
    "run_workbook_import",
    "ensure_default_feeds",
]

logger = logging.getLogger("nbastats.euroleague")

STATE_READY: Final = "ready"
STATE_DISABLED: Final = "disabled"
STATE_MISCONFIGURED: Final = "misconfigured"
STATE_NOT_CONFIGURED: Final = "notConfigured"
STATE_ERROR: Final = "error"
STATE_NOT_PREPARED: Final = "notPrepared"

#: The two candidate headline feeds (both unverified). Seeded enabled into a real store; the
#: fetch job checks robots.txt before the first request and disables a feed that disallows.
DEFAULT_NEWS_FEEDS: Final[tuple[tuple[str, str], ...]] = (
    ("Eurohoops", "https://eurohoops.net/feed"),
    ("TalkBasket", "https://talkbasket.net/feed"),
)


@dataclass(frozen=True)
class BootstrapResult:
    """The EuroLeague's start-up outcome, for the router and for ``/v1/el/meta``."""

    state: str
    reason: str | None = None
    #: The store's stamp (``synthetic``, ``workbook`` or ``live``) once it is open.
    kind: str | None = None
    is_demo: bool = False
    database_url: str | None = None
    seeded_demo: bool = False
    live: bool = False
    #: ``to_dict()`` of every workbook import attempted this run.
    imports: tuple[dict[str, Any], ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    checked_at: datetime | None = None

    @property
    def ready(self) -> bool:
        return self.state == STATE_READY

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "kind": self.kind,
            "isDemo": self.is_demo,
            "seededDemo": self.seeded_demo,
            "live": self.live,
            "imports": list(self.imports),
            "notes": list(self.notes),
            "checkedAt": self.checked_at.isoformat() if self.checked_at else None,
        }


_state: BootstrapResult = BootstrapResult(state=STATE_NOT_PREPARED, reason="not prepared yet")


def get_state() -> BootstrapResult:
    """The last start-up outcome (``notPrepared`` before :func:`prepare` has run)."""
    return _state


def set_state(result: BootstrapResult) -> None:
    """Replace the state; for :func:`prepare` and for tests."""
    global _state
    _state = result


def reset_state() -> None:
    set_state(BootstrapResult(state=STATE_NOT_PREPARED, reason="not prepared yet"))


# --------------------------------------------------------------------------- store


def _store_exists(settings: ElSettings, engine: Engine) -> bool:
    """True when the store already holds EuroLeague tables (so it has a past to respect)."""
    path = settings.store_path
    if path is not None and not path.exists():
        return False
    return "el_store_identity" in set(inspect(engine).get_table_names())


def open_store(settings: ElSettings | None = None, kind: str | None = None) -> Engine:
    """The initialised store's engine, creating and stamping it as ``kind`` if it is new.

    Raises :class:`~nbastats.euroleague.db.ElStoreError` for the wrong file or a kind that does
    not fit an existing stamp. ``kind`` of ``None`` opens without stamping (the importer and
    the demo stamp on first write).
    """
    settings = settings or get_el_settings()
    reason = check_same_store(settings)
    if reason:
        raise ElStoreError(reason)
    engine = get_el_engine(settings.database_url)
    init_el_db(engine)
    if kind is not None:
        with Session(engine, future=True) as session:
            ensure_identity(session, kind)
            session.commit()
    return engine


def ensure_default_feeds(session: Session) -> int:
    """Add the candidate headline feeds to a store that has none. Returns how many were added."""
    if session.execute(select(ElIntelNewsFeed.feed_id).limit(1)).first() is not None:
        return 0
    for name, url in DEFAULT_NEWS_FEEDS:
        session.add(ElIntelNewsFeed(name=name, url=url, enabled=True))
    session.flush()
    return len(DEFAULT_NEWS_FEEDS)


# --------------------------------------------------------------------------- workbooks


def _pending_paths(settings: ElSettings) -> list[Path]:
    paths: list[Path] = []
    if settings.workbook_path is not None:
        paths.append(settings.workbook_path)
    for path in inbox_workbooks(settings.inbox_dir):
        if path not in paths:
            paths.append(path)
    return paths


def import_pending_workbooks(
    settings: ElSettings | None = None, engine: Engine | None = None
) -> list[WorkbookReport]:
    """Import every workbook the configuration points at that has not been imported yet.

    Idempotent: a file already imported is skipped by its hash, and a file that failed is
    skipped with a logged reason until it changes. Reports are returned for the files that were
    actually attempted. Safe to call on a timer.
    """
    settings = settings or get_el_settings()
    engine = engine or open_store(settings)
    reports: list[WorkbookReport] = []
    for path in _pending_paths(settings):
        try:
            if not path.is_file():
                logger.warning("EuroLeague workbook %s does not exist; skipped", path)
                continue
            size = path.stat().st_size
            if size > _xlsx.MAX_FILE_BYTES:
                logger.warning("EuroLeague workbook %s is %s bytes; refused", path.name, size)
                continue
            data = path.read_bytes()
        except OSError as exc:
            logger.warning("EuroLeague workbook %s cannot be read: %s", path, exc)
            continue
        sha = hashlib.sha256(data).hexdigest()
        with Session(engine, future=True) as session:
            earlier_error = last_import_failed(session, sha)
        if earlier_error is not None:
            logger.warning(
                "EuroLeague workbook %s failed before (%s); not retrying it automatically. "
                "Fix the file, or import it by hand.",
                path.name,
                earlier_error,
            )
            continue
        report = import_workbook(
            data, engine, include_estimates=settings.include_estimates, file_name=path.name
        )
        if report.status == "alreadyImported":
            continue
        reports.append(report)
        if report.ok:
            logger.info("EuroLeague workbook %s imported: %s", path.name, report.counts)
        else:
            logger.error("EuroLeague workbook %s: %s: %s", path.name, report.status, report.error)
    return reports


def run_workbook_import(
    path: str | Path,
    *,
    dry_run: bool = False,
    include_estimates: bool | None = None,
    settings: ElSettings | None = None,
) -> WorkbookReport:
    """Import one workbook by hand (the ``import-workbook`` command). Never raises.

    Refuses when the EuroLeague is disabled or misconfigured. A dry run against a store that
    does not exist yet uses a throwaway in-memory store, so it leaves no file behind.
    """
    settings = settings or get_el_settings()
    estimates = settings.include_estimates if include_estimates is None else include_estimates
    failure = WorkbookReport(dry_run=dry_run, file_name=Path(path).name, status="failed")
    if not settings.enabled:
        failure.error = "the EuroLeague is switched off (HARDWOOD_EL_ENABLED)"
        return failure
    reason = check_same_store(settings)
    if reason:
        failure.error = reason
        return failure
    try:
        engine = get_el_engine(settings.database_url)
        existing = _store_exists(settings, engine)
        if dry_run and not existing:
            engine = create_el_engine("sqlite://")
        init_el_db(engine)
    except ElStoreError as exc:
        failure.error = str(exc)
        return failure
    return import_workbook(
        Path(path), engine, dry_run=dry_run, include_estimates=estimates, file_name=Path(path).name
    )


# --------------------------------------------------------------------------- prepare


def prepare(settings: ElSettings | None = None, *, now: datetime | None = None) -> BootstrapResult:
    """Run the start-up decision (see the module docstring) and remember its outcome.

    Never raises: an unexpected failure becomes state ``error`` with the exception named, and
    the NBA side carries on.
    """
    try:
        resolved = settings or get_el_settings()
    except ValueError as exc:  # a malformed boolean in the environment
        result = BootstrapResult(state=STATE_ERROR, reason=str(exc), checked_at=now or utcnow())
        set_state(result)
        return result
    try:
        result = _prepare(resolved, now or utcnow())
    except Exception as exc:  # noqa: BLE001 - the EuroLeague must never take the NBA down
        logger.exception("the EuroLeague could not start")
        result = BootstrapResult(
            state=STATE_ERROR,
            reason=f"{type(exc).__name__}: {exc}",
            database_url=resolved.database_url,
            checked_at=now or utcnow(),
        )
    set_state(result)
    return result


def _prepare(settings: ElSettings, moment: datetime) -> BootstrapResult:
    url = settings.database_url
    if not settings.enabled:
        return BootstrapResult(
            STATE_DISABLED, "HARDWOOD_EL_ENABLED is off", database_url=url, checked_at=moment
        )
    reason = check_same_store(settings)
    if reason:
        logger.error("EuroLeague disabled: %s", reason)
        return BootstrapResult(STATE_MISCONFIGURED, reason, database_url=url, checked_at=moment)

    engine = get_el_engine(url)
    existed = _store_exists(settings, engine)
    workbook_wanted = settings.workbook_requested
    notes: list[str] = []

    with Session(engine, future=True) as probe:
        identity = None
        if existed:
            identity = read_identity(probe)
            if (
                identity is not None
                and identity.kind in REAL_KINDS
                and settings.demo
                and not workbook_wanted
                and release_empty_real_stamp(probe)
            ):
                # Stamped real by an earlier start, but nothing real ever arrived: the demo
                # the person asked for may have it.
                probe.commit()
                notes.append(
                    "This store was stamped for real data but never received any; it now "
                    "holds the invented demo league."
                )
                identity = None
    if identity is None:
        if workbook_wanted:
            intended = KIND_WORKBOOK
        elif settings.demo:
            intended = KIND_SYNTHETIC
        elif settings.live:
            intended = KIND_LIVE
        else:
            return BootstrapResult(
                STATE_NOT_CONFIGURED,
                "No EuroLeague source is on: set HARDWOOD_WORKBOOK_PATH, drop a workbook in "
                "HARDWOOD_DATA_DIR/inbox, set HARDWOOD_EL_DEMO=1, or leave HARDWOOD_EL_LIVE on.",
                database_url=url,
                checked_at=moment,
            )
    else:
        intended = identity.kind

    init_el_db(engine)
    seeded = False
    imports: tuple[dict[str, Any], ...] = ()
    with Session(engine, future=True) as session:
        stamp = ensure_identity(session, intended, moment)
        kind = stamp.kind
        if kind == KIND_SYNTHETIC:
            if settings.demo or not existed:
                seeded = seed_demo(session, now=None, anchor=moment).seeded
            if workbook_wanted:
                notes.append(
                    "A workbook is waiting, but this store holds the invented demo league and "
                    "never takes real data. Move or delete the store file "
                    f"({settings.store_path or url}), or point HARDWOOD_EL_DATABASE_URL at a "
                    "new one, to import it."
                )
        else:
            if settings.demo and not workbook_wanted:
                notes.append(
                    "Demo mode is on, but this store is stamped for real data and holds some, "
                    "so it is never given the invented league. Point HARDWOOD_EL_DATABASE_URL "
                    "at a new file for the demo."
                )
            ensure_default_feeds(session)
        state = read_sync_state(session)
        wanted_mode = (
            "demo" if kind == KIND_SYNTHETIC else ("live" if settings.live else "workbook")
        )
        if state.mode != wanted_mode:
            state.mode = wanted_mode
        session.commit()

    if kind in REAL_KINDS and workbook_wanted:
        try:
            reports = import_pending_workbooks(settings, engine)
        except StoreKindMismatch as exc:  # pragma: no cover - guarded above
            notes.append(str(exc))
            reports = []
        imports = tuple(report.to_dict() for report in reports)
        for report in reports:
            if not report.ok:
                notes.append(f"{report.file_name}: {report.status}: {report.error}")

    return BootstrapResult(
        state=STATE_READY,
        reason=notes[0] if notes else None,
        kind=kind,
        is_demo=kind == KIND_SYNTHETIC,
        database_url=url,
        seeded_demo=seeded,
        live=bool(settings.live and kind in REAL_KINDS),
        imports=imports,
        notes=tuple(notes),
        checked_at=moment,
    )
