"""Persistence helpers for the NBA intel tables, and the guard that keeps real statuses away from
the demo league.

Nothing here commits. Every function takes a :class:`~sqlalchemy.orm.Session`, flushes what it
writes and leaves the transaction to the caller (a job wraps a whole run in one
:func:`nbastats.db.session_scope`), so a run that fails halfway leaves no half-written snapshot.

The synthetic-store guard
-------------------------
:func:`store_is_synthetic` is a single question put to the *data*: does the ``games`` table hold a
row stamped ``data_source = 'synthetic-demo'``? It is deliberately not "is ``HARDWOOD_DEMO_MODE``
set". The environment variable describes how a process was started; the rows describe what the
store contains, and the two can disagree, for instance when someone re-seeds a live database file
by hand. Keyed on content, the guard cannot be skipped that way. When it returns true the injury
job writes nothing and the availability routes report ``freshness.state = "disabled"`` with the
reason "Demo league: real injury statuses are not shown next to invented games". A store with no
``games`` table at all is not synthetic (there is nothing invented in it).

Source state
------------
``nba_intel_source_state`` is what ``/v1/sources`` reads. :func:`set_source_state` is the one
writer, so the bookkeeping (a success clears the failure streak and the last error; a failure
increments it and keeps the last good time) cannot drift between callers. States come from
:data:`~nbastats.nba_intel.models.SOURCE_STATE_VALUES`.

The parser confirmation counter
-------------------------------
The injury parser was written against the documented shape of the report, with no real report
available in the development environment. So ``/v1/sources`` says so, plainly, until the parser
has read a real one: "not yet confirmed against a real report". The evidence is a counter kept in
the ``nba.injuryReport`` row's ``detail_json`` (:func:`record_parser_confirmation`), incremented
only when a report parsed ``ok`` and produced at least one entry. It lives in the database, not in
memory, so a restart does not forget it, and it is per machine because the Mac is where the real
reports are. A parse failure is shown as "unreadable" and never counts.

Job cursors
-----------
The scheduler (``nbastats/worker.py``) owns ``last_started_at``, ``last_success_at`` and
``last_error`` in ``nba_intel_job_state``; a job owns ``cursor_json``. :func:`write_cursor` creates
the row if the worker has not yet, and touches only that one column.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from .models import (
    PARSE_STATUSES,
    SOURCE_STATE_VALUES,
    NbaIntelJobState,
    NbaIntelRawFetch,
    NbaIntelSnapshot,
    NbaIntelSourceState,
)

__all__ = [
    "SOURCE_INJURY_REPORT",
    "SOURCE_STATS",
    "JOB_INJURIES",
    "JOB_NEWS",
    "SYNTHETIC_REASON",
    "ParserConfirmation",
    "store_is_synthetic",
    "table_exists",
    "news_source_key",
    "clip",
    "get_source_state",
    "set_source_state",
    "source_detail",
    "injury_parser_confirmation",
    "record_parser_confirmation",
    "read_cursor",
    "write_cursor",
    "insert_snapshot",
    "latest_snapshot",
    "snapshot_with_sha",
    "newest_fetched_slot",
    "record_raw_fetch",
]

SOURCE_INJURY_REPORT = "nba.injuryReport"
SOURCE_STATS = "nba.stats"
JOB_INJURIES = "nba.injuries"
JOB_NEWS = "nba.news"

#: The message shown when the injury report is refused because the store holds the demo league.
SYNTHETIC_REASON = "Demo league: real injury statuses are not shown next to invented games"

#: The ``detail_json`` key holding the parser's confirmation record.
_CONFIRMATION = "parserConfirmation"


def clip(message: str | None, limit: int = 400) -> str | None:
    """A message on one line and bounded, for error columns. ``None`` stays ``None``."""
    if message is None:
        return None
    flat = " ".join(str(message).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def news_source_key(feed_id: int) -> str:
    return f"nba.news.{feed_id}"


# ------------------------------------------------------------------------ the stats store


def table_exists(session: Session, name: str) -> bool:
    """True when ``name`` is a table in the session's database."""
    return inspect(session.get_bind()).has_table(name)


def store_is_synthetic(session: Session) -> bool:
    """True when the store holds the seeded demo league, judged by the rows themselves.

    ``SELECT 1 FROM games WHERE data_source = 'synthetic-demo' LIMIT 1``. See the module
    docstring for why this is asked of the data and not of the environment.
    """
    if not table_exists(session, "games"):
        return False
    row = session.execute(
        text("SELECT 1 FROM games WHERE data_source = 'synthetic-demo' LIMIT 1")
    ).first()
    return row is not None


# --------------------------------------------------------------------------- source state


def get_source_state(session: Session, key: str) -> NbaIntelSourceState | None:
    return session.get(NbaIntelSourceState, key)


def source_detail(row: NbaIntelSourceState | None) -> dict[str, Any]:
    """The row's ``detail_json`` as a dict; empty when absent or unreadable."""
    if row is None or not row.detail_json:
        return {}
    try:
        value = json.loads(row.detail_json)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def set_source_state(
    session: Session,
    key: str,
    state: str,
    *,
    now: datetime,
    error: str | None = None,
    success: bool = False,
    paused_until: datetime | None = None,
    detail: Mapping[str, Any] | None = None,
    reset_failures: bool = False,
) -> NbaIntelSourceState:
    """Record the standing of one source.

    ``success=True`` stamps ``last_success_at``, clears ``last_error`` and the failure streak.
    Otherwise a state of ``unreadable``, ``error`` or ``blocked`` counts as one more consecutive
    failure and keeps the previous good time; other states leave the streak alone, unless
    ``reset_failures`` says the host has just answered normally (a recovery that is not a new
    delivery, so ``last_success_at`` is left alone). ``detail`` is *merged* into ``detail_json``
    so one writer cannot erase another's keys (the parser confirmation counter in particular),
    except that an ``ok`` state drops a stale ``reason`` (a refusal's explanation must not outlive
    the refusal).
    """
    if state not in SOURCE_STATE_VALUES:
        raise ValueError(f"{state!r} is not a source state")
    row = session.get(NbaIntelSourceState, key)
    if row is None:
        row = NbaIntelSourceState(source_key=key, state=state, consecutive_failures=0)
        session.add(row)
    row.state = state
    if success:
        row.last_success_at = now
        row.last_error = None
        row.consecutive_failures = 0
    else:
        if error is not None:
            row.last_error = clip(error)
        if state in ("unreadable", "error", "blocked"):
            row.consecutive_failures = (row.consecutive_failures or 0) + 1
    if reset_failures:
        row.consecutive_failures = 0
        row.last_error = None
    row.paused_until = paused_until
    merged = source_detail(row)
    if detail:
        merged.update(detail)
    if state == "ok" and not (detail and "reason" in detail):
        merged.pop("reason", None)
    if merged or row.detail_json:
        row.detail_json = json.dumps(merged, sort_keys=True, default=str)
    session.flush()
    return row


# ----------------------------------------------------------- parser confirmation counter


@dataclass(frozen=True, slots=True)
class ParserConfirmation:
    """How many real reports the injury parser has read successfully on this machine."""

    count: int
    first_at: datetime | None
    last_at: datetime | None

    @property
    def confirmed(self) -> bool:
        return self.count > 0


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def injury_parser_confirmation(session: Session) -> ParserConfirmation:
    """The persisted confirmation record; zero when no report has ever parsed ``ok``."""
    record = source_detail(get_source_state(session, SOURCE_INJURY_REPORT)).get(_CONFIRMATION)
    if not isinstance(record, dict):
        return ParserConfirmation(0, None, None)
    count = record.get("count")
    return ParserConfirmation(
        count if isinstance(count, int) and count > 0 else 0,
        _parse_time(record.get("firstAt")),
        _parse_time(record.get("lastAt")),
    )


def record_parser_confirmation(session: Session, *, now: datetime) -> ParserConfirmation:
    """Count one more real report parsed successfully. Call only for an ``ok`` parse with entries.

    Creates the ``nba.injuryReport`` source row if it does not exist yet (as ``ok``); an existing
    row keeps its state, which the caller sets separately.
    """
    previous = injury_parser_confirmation(session)
    first = previous.first_at or now
    record = {"count": previous.count + 1, "firstAt": first.isoformat(), "lastAt": now.isoformat()}
    row = get_source_state(session, SOURCE_INJURY_REPORT)
    state = row.state if row is not None else "ok"
    set_source_state(
        session,
        SOURCE_INJURY_REPORT,
        state,
        now=now,
        paused_until=row.paused_until if row is not None else None,
        detail={_CONFIRMATION: record},
    )
    return ParserConfirmation(previous.count + 1, first, now)


# ------------------------------------------------------------------------------ cursors


def read_cursor(session: Session, job_key: str) -> dict[str, Any]:
    """The job's ``cursor_json`` as a dict; empty when there is none or it is unreadable."""
    row = session.get(NbaIntelJobState, job_key)
    if row is None or not row.cursor_json:
        return {}
    try:
        value = json.loads(row.cursor_json)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def write_cursor(session: Session, job_key: str, cursor: Mapping[str, Any]) -> None:
    """Replace the job's cursor, creating its state row if needed, touching no other column."""
    row = session.get(NbaIntelJobState, job_key)
    payload = json.dumps(dict(cursor), sort_keys=True, default=str)
    if row is None:
        session.add(NbaIntelJobState(job_key=job_key, cursor_json=payload))
    else:
        row.cursor_json = payload
    session.flush()


# ----------------------------------------------------------------------------- snapshots


def insert_snapshot(
    session: Session,
    *,
    source_kind: str,
    fetched_at: datetime,
    parse_status: str,
    row_count: int = 0,
    url: str | None = None,
    slot_at_utc: datetime | None = None,
    report_as_of_utc: datetime | None = None,
    sha256: str | None = None,
    parse_error: str | None = None,
) -> NbaIntelSnapshot:
    if parse_status not in PARSE_STATUSES:
        raise ValueError(f"{parse_status!r} is not a parse status")
    snapshot = NbaIntelSnapshot(
        source_kind=source_kind,
        url=url,
        slot_at_utc=slot_at_utc,
        report_as_of_utc=report_as_of_utc,
        fetched_at=fetched_at,
        sha256=sha256,
        row_count=row_count,
        parse_status=parse_status,
        parse_error=parse_error,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def latest_snapshot(
    session: Session,
    *,
    parse_statuses: tuple[str, ...] = ("ok", "partial", "empty"),
    source_kind: str = "leagueReport",
) -> NbaIntelSnapshot | None:
    """The newest snapshot of a kind whose parse status is one of ``parse_statuses``, by slot
    time (then fetch time). Defaults to the snapshots that yielded usable entries."""
    stmt = (
        select(NbaIntelSnapshot)
        .where(
            NbaIntelSnapshot.source_kind == source_kind,
            NbaIntelSnapshot.parse_status.in_(parse_statuses),
        )
        .order_by(
            func.coalesce(NbaIntelSnapshot.slot_at_utc, NbaIntelSnapshot.fetched_at).desc(),
            NbaIntelSnapshot.snapshot_id.desc(),
        )
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def snapshot_with_sha(session: Session, sha256: str) -> NbaIntelSnapshot | None:
    """A snapshot already stored for a file with this hash, if any (an unchanged re-fetch)."""
    stmt = (
        select(NbaIntelSnapshot)
        .where(NbaIntelSnapshot.sha256 == sha256, NbaIntelSnapshot.source_kind == "leagueReport")
        .order_by(NbaIntelSnapshot.snapshot_id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def newest_fetched_slot(session: Session) -> datetime | None:
    """The slot time of the newest league report that was actually fetched (any parse status
    other than a recorded failure to fetch), as naive UTC."""
    stmt = select(func.max(NbaIntelSnapshot.slot_at_utc)).where(
        NbaIntelSnapshot.source_kind == "leagueReport",
        NbaIntelSnapshot.parse_status.in_(("ok", "partial", "empty", "headerMismatch")),
    )
    return session.execute(stmt).scalar()


def record_raw_fetch(
    session: Session,
    *,
    source: str,
    url: str | None,
    fetched_at: datetime,
    http_status: int | None,
    sha256: str | None,
    size: int | None,
    path: str | None,
) -> NbaIntelRawFetch:
    row = NbaIntelRawFetch(
        source=source,
        url=url,
        fetched_at=fetched_at,
        http_status=http_status,
        sha256=sha256,
        bytes=size,
        path=path,
    )
    session.add(row)
    session.flush()
    return row
