"""The two jobs the scheduler runs for this package: ``nba.injuries`` and ``nba.news``.

``nbastats/worker.py`` owns the clock; these functions own what happens when it rings. The worker
imports them by string name (``nbastats.nba_intel.jobs:run_injuries`` and ``:run_news``) and calls
each with whichever of ``now`` (aware UTC), ``shutdown`` (truthy while stopping), ``force`` and
``data_dir`` the signature declares. Each returns a small dict, ``{"status": ..., "detail": ...}``,
that the worker records: ``ok``, ``skipped`` (nothing was due, or the source is off) or ``error``.
A source that is merely unreadable or blocked is *not* a job error, because the job did its work and
recorded the fact; it shows in ``/v1/sources`` instead.

``nba.injuries`` decides for itself
-----------------------------------
The worker calls it every 15 minutes and :func:`~nbastats.nba_intel.report_fetch.decide_poll`
decides whether anything is due, from the games in the store: nothing unless a game is scheduled
within 36 hours, every 15 minutes inside a reporting window, hourly otherwise. Then it follows the
design's walk-back rule (look at the newest slot, step back only until a slot it already has, at
most eight steps), parses what it finds and stores it. It refuses outright, writing nothing, in
three cases:

* the switch ``HARDWOOD_NBA_INJURIES`` is off;
* the store holds the synthetic demo league, judged by the store's own rows
  (:func:`~nbastats.nba_intel.store.store_is_synthetic`), so real statuses are never joined to
  invented games, and the source reports ``disabled`` with that reason;
* ``pypdf`` is not installed, reported as ``notConfigured`` with the install hint.

Why it reads, then fetches, then writes
---------------------------------------
SQLite allows one writer. A job that opened a write transaction and then waited on a slow host (a
fetch can take twenty seconds and a run up to eight of them) would hold the lock and make the API's
own writes wait out their ``busy_timeout``. So each run reads what it needs in one short
transaction, makes every network request with no transaction open, and writes the outcome in a
second short one. The consequence is that nothing is written for a run that fails before its write
phase, which is the right behaviour: the next run starts from the same state.

What a run records
------------------
Every fetched report becomes a snapshot, readable or not; an unreadable one is the recorded fact
"the report at 5:30 PM could not be read" with its reason. The raw PDF is kept under
``HARDWOOD_DATA_DIR/raw`` (never served) and a row in ``nba_intel_raw_fetch`` points at it, so a
parser fix can re-read exactly what arrived. The job's cursor remembers the newest slot it has seen
(an unchanged file is skipped but its slot is remembered, so it is not asked for again) and the time
of the last probe. Source state is written so ``/v1/sources`` can say ``ok``, ``unreadable``,
``blocked`` (with ``paused_until``), ``noReportYet``, ``disabled`` or ``notConfigured``, and a
report that parsed ``ok`` with entries bumps the persisted confirmation counter that moves the
parser from "not yet confirmed against a real report" to confirmed.

``nba.news`` fetches what is due
--------------------------------
Hourly, for every enabled feed (and any feed a ``robots.txt`` disallow switched off, so it can come
back). Feeds are fetched one at a time, each in its own transaction, so one slow or failing feed
does not undo the others. Old items are pruned at the end of the run.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import session_scope
from ..intel import recordings
from ..intel.http import BREAKER_PAUSE, PoliteClient
from ..intel.robots import RobotsChecker
from . import news, report_fetch, report_pdf, status, store
from .models import NbaIntelNewsFeed

__all__ = [
    "JOBS",
    "run_injuries",
    "run_news",
    "switch_on",
    "upcoming_games",
    "RAW_RETENTION",
]

logger = logging.getLogger(__name__)

#: Raw PDFs older than this are deleted (their ``nba_intel_raw_fetch`` rows are kept).
RAW_RETENTION = timedelta(days=30)

_ON = {"1", "true", "t", "yes", "y", "on"}
_OFF = {"0", "false", "f", "no", "n", "off"}


def switch_on(name: str, *, default: bool = True, env: dict[str, str] | None = None) -> bool:
    """An environment switch: unset means ``default`` (on), a recognised value means itself, and an
    unrecognised one means off (the safe reading, and the one the worker applies)."""
    raw = (os.environ if env is None else env).get(name)
    if raw is None or not raw.strip():
        return default
    lowered = raw.strip().lower()
    if lowered in _ON:
        return True
    if lowered in _OFF:
        return False
    return False


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _naive(value: datetime) -> datetime:
    return _aware(value).replace(tzinfo=None)


def _stopper(shutdown: Any) -> Callable[[], bool]:
    return lambda: bool(shutdown)


def _iso(value: datetime | None) -> str | None:
    return _naive(value).isoformat() if value is not None else None


def _from_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _result(status_: str, detail: str | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status_}
    if detail:
        out["detail"] = detail
    out.update(extra)
    return out


# ------------------------------------------------------------------------------ games


def _to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def upcoming_games(
    session: Session, now: datetime, *, horizon: timedelta = report_fetch.GAME_HORIZON
) -> list[report_fetch.GameInfo]:
    """Not-yet-final games from today (US Eastern) through the horizon, with tip-off times when the
    store has them (``game_schedule_detail``) and ``None`` when it does not."""
    if not store.table_exists(session, "games"):
        return []
    moment = _aware(now)
    first = moment.astimezone(report_fetch.EASTERN).date()
    last = (moment + horizon).astimezone(report_fetch.EASTERN).date()
    detail = store.table_exists(session, "game_schedule_detail")
    tip = "d.tipoff_utc" if detail else "NULL"
    join = "LEFT JOIN game_schedule_detail d ON d.game_id = g.game_id " if detail else ""
    rows = session.execute(
        text(
            f"SELECT g.game_id, g.game_date, {tip} FROM games g {join}"
            "WHERE g.game_date >= :first AND g.game_date <= :last AND g.status <> 'final'"
        ),
        {"first": first, "last": last},
    ).all()
    games: list[report_fetch.GameInfo] = []
    for game_id, game_date, tipoff in rows:
        day = _to_date(game_date)
        if day is not None:
            games.append(report_fetch.GameInfo(str(game_id), day, _to_datetime(tipoff)))
    return games


# ------------------------------------------------------------------------- injuries


def run_injuries(
    now: datetime | None = None,
    shutdown: Any = None,
    force: bool = False,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """One tick of the NBA injury report job. See the module docstring."""
    moment = _aware(now)
    stamp = _naive(moment)
    if not switch_on("HARDWOOD_NBA_INJURIES"):
        return _result("skipped", "HARDWOOD_NBA_INJURIES is off")
    try:
        template = report_fetch.url_template()
    except report_fetch.InvalidTemplateError as exc:
        return _result("error", str(exc))
    host = urlsplit(template).hostname or ""
    stop = _stopper(shutdown)
    base = Path(data_dir) if data_dir is not None else None

    # ---- phase 1: read, in one short transaction
    with session_scope() as session:
        if store.store_is_synthetic(session):
            store.set_source_state(
                session,
                store.SOURCE_INJURY_REPORT,
                "disabled",
                now=stamp,
                detail={"reason": store.SYNTHETIC_REASON},
            )
            return _result("skipped", store.SYNTHETIC_REASON)
        if not report_pdf.pypdf_available():
            hint = (
                "pypdf is not installed. Install the 'injuries' extra: "
                "pip install 'hardwood-backend[injuries]'"
            )
            store.set_source_state(
                session,
                store.SOURCE_INJURY_REPORT,
                "notConfigured",
                now=stamp,
                detail={"reason": hint},
            )
            return _result("skipped", hint)
        source = store.get_source_state(session, store.SOURCE_INJURY_REPORT)
        paused_until = source.paused_until if source is not None else None
        paused_reason = source.last_error if source is not None else None
        games = upcoming_games(session, moment)
        cursor = store.read_cursor(session, store.JOB_INJURIES)
        newest_db = store.newest_fetched_slot(session)

    decision = report_fetch.decide_poll(
        moment, games, _from_iso(cursor.get("lastProbeAt")), force=force
    )
    if not decision.poll:
        return _result("skipped", decision.reason)

    seen = _from_iso(cursor.get("newestSlot"))
    newest = max((t for t in (newest_db, seen) if t is not None), default=None)
    slots = report_fetch.candidate_slots(moment, newest)
    if not slots:
        return _result("skipped", "the newest slot has already been fetched")

    # ---- phase 2: the network, with no transaction open
    attempts: list[tuple[datetime, report_fetch.SlotFetch]] = []
    found: tuple[datetime, report_fetch.SlotFetch] | None = None
    with PoliteClient(stop_requested=stop) as client:
        if paused_until is not None:
            client.restore_breaker(host, paused_until, paused_reason)
        for slot in slots:
            if stop():
                break
            fetch = report_fetch.fetch_slot(client, report_fetch.slot_url(slot, template))
            attempts.append((slot, fetch))
            if fetch.state == "ok":
                found = (slot, fetch)
                break
            if fetch.state in ("blocked", "error"):
                break

    parsed: report_pdf.ParsedReport | None = None
    parser_missing = False
    if found is not None and found[1].body is not None:
        try:
            parsed = report_pdf.parse_report(found[1].body)
        except report_pdf.ReportParserUnavailable:
            parser_missing = True

    # ---- phase 3: write, in one short transaction
    forbidden_now = sum(1 for _, f in attempts if f.state == "forbidden")
    with session_scope() as session:
        cursor = store.read_cursor(session, store.JOB_INJURIES)
        cursor["lastProbeAt"] = _iso(moment)
        streak = 0 if found is not None else int(cursor.get("forbiddenStreak", 0)) + forbidden_now
        cursor["forbiddenStreak"] = streak
        summary = _record_outcome(
            session, cursor, attempts, found, parsed, parser_missing, streak, moment, stamp, base
        )
        store.write_cursor(session, store.JOB_INJURIES, cursor)
        _prune_raw(session, moment, base)
    return summary


def _record_outcome(
    session: Session,
    cursor: dict[str, Any],
    attempts: list[tuple[datetime, report_fetch.SlotFetch]],
    found: tuple[datetime, report_fetch.SlotFetch] | None,
    parsed: report_pdf.ParsedReport | None,
    parser_missing: bool,
    streak: int,
    moment: datetime,
    stamp: datetime,
    base: Path | None,
) -> dict[str, Any]:
    key = store.SOURCE_INJURY_REPORT
    last = attempts[-1][1] if attempts else None

    if found is not None and parser_missing:
        store.set_source_state(
            session,
            key,
            "notConfigured",
            now=stamp,
            detail={"reason": "pypdf is not installed. Install the 'injuries' extra."},
        )
        return _result("skipped", "pypdf is not installed")

    if found is not None and parsed is not None:
        slot, fetch = found
        slot_utc = report_fetch.slot_to_utc(slot)
        cursor["newestSlot"] = _iso(slot_utc)
        assert fetch.body is not None and fetch.sha256 is not None
        label = slot.strftime("%Y-%m-%d %H:%M ET")

        raw = recordings.save_raw("nba_injury", fetch.body, extension="pdf", base=base)
        store.record_raw_fetch(
            session,
            source="nba_injury",
            url=fetch.url,
            fetched_at=_naive(fetch.fetched_at),
            http_status=fetch.http_status,
            sha256=raw.sha256,
            size=raw.bytes,
            path=str(raw.path),
        )

        previous = store.snapshot_with_sha(session, fetch.sha256)
        if previous is not None and previous.parse_status != "headerMismatch":
            # Unchanged content that was read: nothing to parse or store, but the slot is now
            # seen. (A file that was *unreadable* is parsed again, so a parser fix takes effect.)
            store.set_source_state(session, key, "ok", now=stamp, success=True)
            return _result("ok", f"{label}: unchanged since an earlier slot")

        result = status.ingest_report(
            session,
            parsed,
            url=fetch.url,
            slot_at_utc=slot_utc,
            sha256=fetch.sha256,
            fetched_at=fetch.fetched_at,
            now=stamp,
        )
        if result.parse_status == "headerMismatch":
            store.set_source_state(
                session,
                key,
                "unreadable",
                now=stamp,
                error=parsed.parse_error,
                detail={
                    "reason": "The report's layout was not recognised, so no entries were read"
                },
            )
            return _result("ok", f"{label}: unreadable ({parsed.parse_error})")
        store.set_source_state(session, key, "ok", now=stamp, success=True)
        if result.parse_status == "ok" and result.stored > 0:
            store.record_parser_confirmation(session, now=stamp)
        return _result(
            "ok",
            f"{label}: {result.stored} entries ({result.parse_status}), "
            f"{result.unmatched_players} unmatched, {result.teams_marked} team states",
        )

    if last is not None and last.state == "blocked":
        store.set_source_state(
            session,
            key,
            "blocked",
            now=stamp,
            error=last.reason,
            paused_until=_naive(last.paused_until) if last.paused_until else None,
        )
        return _result("skipped", f"blocked: {last.reason}")
    if last is not None and last.state == "error":
        store.set_source_state(session, key, "error", now=stamp, error=last.reason)
        return _result("error", last.reason)
    if streak >= report_fetch.FORBIDDEN_STREAK_LIMIT:
        paused = stamp + BREAKER_PAUSE
        reason = f"the host answered 403 to {streak} requests in a row"
        store.set_source_state(
            session, key, "blocked", now=stamp, error=reason, paused_until=paused
        )
        return _result("skipped", f"blocked: {reason}")

    # Everything asked for was simply not there (404, or a tolerated 403).
    snapshots = ("ok", "partial", "empty", "headerMismatch")
    if store.latest_snapshot(session, parse_statuses=snapshots) is None:
        store.set_source_state(
            session,
            key,
            "noReportYet",
            now=stamp,
            detail={"reason": "No injury report has been published yet"},
        )
    else:
        row = store.get_source_state(session, key)
        if row is not None and row.state in ("blocked", "error"):
            # The host answered normally again, so the block or the error is over.
            store.set_source_state(session, key, "ok", now=stamp, reset_failures=True)
    return _result("ok", f"{len(attempts)} slots asked for; none published")


def _prune_raw(session: Session, moment: datetime, base: Path | None) -> None:
    """Delete raw PDFs older than :data:`RAW_RETENTION`, at most once a day."""
    cursor = store.read_cursor(session, store.JOB_INJURIES)
    last = _from_iso(cursor.get("lastPrunedAt"))
    if last is not None and _naive(moment) - last < timedelta(days=1):
        return
    removed = recordings.prune_older_than(
        recordings.raw_dir(base) / "nba_injury", moment - RAW_RETENTION
    )
    cursor["lastPrunedAt"] = _iso(moment)
    store.write_cursor(session, store.JOB_INJURIES, cursor)
    if removed:
        logger.info("pruned %d raw injury reports", removed)


# --------------------------------------------------------------------------------- news


def run_news(
    now: datetime | None = None,
    shutdown: Any = None,
    force: bool = False,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """One tick of the headline job: refresh every feed that is due. See the module docstring."""
    moment = _aware(now)
    stamp = _naive(moment)
    if not switch_on("HARDWOOD_NEWS"):
        return _result("skipped", "HARDWOOD_NEWS is off")
    stop = _stopper(shutdown)

    with session_scope() as session:
        news.ensure_default_feeds(session)
        due = [(f.feed_id, f.url) for f in news.due_feeds(session, stamp, force=force)]
        index = news.SubjectIndex(session) if due else None
        pauses = {}
        for feed_id, _url in due:
            row = store.get_source_state(session, store.news_source_key(feed_id))
            if row is not None and row.paused_until is not None:
                pauses[feed_id] = (row.paused_until, row.last_error)
    if not due:
        with session_scope() as session:
            pruned = news.prune_items(session, stamp)
        return _result("skipped", "no feed is due" + (f"; pruned {pruned}" if pruned else ""))

    outcomes: list[news.FeedRefresh] = []
    with PoliteClient(stop_requested=stop) as client:
        robots = RobotsChecker(client)
        for feed_id, url in due:
            if stop():
                break
            if feed_id in pauses:
                client.restore_breaker(
                    (urlsplit(url).hostname or "").lower(), pauses[feed_id][0], pauses[feed_id][1]
                )
            with session_scope() as session:
                feed = session.get(NbaIntelNewsFeed, feed_id)
                if feed is None:
                    continue
                outcomes.append(
                    news.refresh_feed(session, client, robots, feed, now=moment, index=index)
                )
    with session_scope() as session:
        pruned = news.prune_items(session, stamp)

    new_items = sum(o.new_items for o in outcomes)
    problems = [o for o in outcomes if o.state not in ("ok", "notModified", "disallowed")]
    detail = f"{len(outcomes)} feeds checked, {new_items} new headlines"
    off = sum(1 for o in outcomes if o.state == "disallowed")
    if off:
        detail += f", {off} switched off by robots.txt"
    if problems:
        detail += f", {len(problems)} with problems ({problems[0].state}: {problems[0].reason})"
    if pruned:
        detail += f", {pruned} old items removed"
    if outcomes and len(problems) == len(outcomes):
        return _result("error", detail)
    return _result("ok", detail, newItems=new_items)


#: The worker looks here first (``JOBS`` wins over a same-named attribute).
JOBS: dict[str, Callable[..., dict[str, Any]]] = {
    "nba.injuries": run_injuries,
    "nba.news": run_news,
}
