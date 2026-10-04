"""The worker's EuroLeague jobs: what is due, what to ask for, and what to do with the answer.

``python -m nbastats.worker`` calls five functions here by name (``run_structure``,
``run_rosters``, ``run_round``, ``run_box``, ``run_news``) with whichever of ``now``, ``shutdown``,
``force`` and ``data_dir`` they declare. The first four are the design's "called often and expected
to decide for themselves" kind: the worker calls them every ten minutes or every hour and each
returns ``{"status": "skipped"}`` straight away, **before opening a socket**, when nothing is due.
That is a contract and not a courtesy. The request budget (about ninety a week, a hundred and fifty
in a double-round week) was worked out for the cadence below, and a job that fetched on every call
would spend it in a day.

The cadence (design section 4.4, with the amendment's politeness)
-----------------------------------------------------------------
``el.round`` (E2, the schedule and results)
    Once a day at 06:00 local for the current round and the next, and, for each game whose result
    is still missing, a poll of its round from tip-off plus 105 minutes, every ten minutes, at most
    twelve polls per game. One E2 request answers for every game in the round, so a double-round
    evening costs the same as a single game. "Current" is the earliest round with a *scheduled*
    game: a postponed game never holds it back. A round that holds a postponed game is read in the
    daily sweep too (at most two such rounds, none older than sixty days), because the new date
    arrives there and a game that is never re-read is never polled for a result. A run reads at
    most three rounds, results first; the rest of the sweep goes on at the next tick, and the
    sweep is not marked done until every round of it was read.
``el.box`` (E3, the box score)
    Once for each final game as soon as the schedule says it is final, then a correction re-fetch
    twenty-four and forty-eight hours after the first (an unchanged digest writes nothing). A game
    the workbook supplied is fetched once to replace its lines with the service's, which carry
    starters, fouls drawn and plus/minus. Twelve games at most per run; a game whose box is not
    published yet is retried every ten minutes, twelve times, then a few more at six-hour intervals,
    then left until a person asks.
``el.structure`` (E1 calendar, E4 clubs)
    Weekly (the first run after Monday 00:00 local) and the day before a round (clubs only).
``el.rosters`` (E5 registrations)
    Weekly and the day before a round: one request per club, resumable if interrupted.
``el.news`` (headlines)
    Hourly, enabled feeds only, ``robots.txt`` checked first; see :func:`run_news`.

"Due" is always *the latest valid state at or before now*, like every worker job: a Mac that slept
through three days runs the daily sweep once, now, and never replays the nights it missed.

What a run may not do
---------------------
* **Touch the network with the switches off.** ``HARDWOOD_EL_ENABLED=0`` or ``HARDWOOD_EL_LIVE=0``
  makes every job here return ``skipped`` (the worker also gates on them; this is the second
  lock, which also covers the command line). ``force`` bypasses *due-ness* (and the wait after an
  unreadable answer), never a switch and never the circuit breaker.
* **Create the store.** The API's bootstrap creates and stamps it; a job that finds no store says
  so and skips. A **synthetic** (demo) store is refused with a clear error: invented and real rows
  never meet.
* **Hammer a service that said no.** A 401, 403, Cloudflare 1015 or three 429s opens the breaker for
  six hours. The pause is stored in ``el_source_state.paused_until`` and put back into the client on
  every run, so a restarted worker still waits. Every job also checks the pause *before* deciding.
* **Request more than its budget.** Forty requests per run, enforced by the client.

What a failure becomes
----------------------
Everything is recorded on the ``el.dataService`` row of ``el_source_state``, which
``/v1/el/sources`` reads: ``ok``; ``blocked`` with ``paused_until``; ``unreadable`` when the
parsers refused a response, with the path of the key they were looking for in ``last_error``; or
``error``. A job returns ``{"status": "error", ...}`` for the worker to log. Nothing is retried
inside a run beyond the polite client's own backoff.

An *unreadable* answer also makes the job wait before it asks again: one hour, then two, four and
six (``UNREADABLE_BACKOFF_*``), kept in the job's own cursor and cleared by the first run that
reads what the service sends. The worker runs these jobs every ten minutes and gives an interval
job no back-off of its own, so without this a changed shape would be asked about a hundred and
forty times a day for as long as it lasted. The job says so when it skips, and ``force`` (the
command line's ``run JOB --force``) asks at once. A 5xx or a transport failure does not start the
wait: those are the polite client's to retry, and they do pass.

The job's cursor
----------------
Each job keeps what it needs between runs in its own ``el_job_state.cursor_json`` (the worker never
touches that column): ``el.structure`` holds the round calendar and the last fetch times;
``el.round`` the last daily sweep and the per-game poll counts; ``el.box`` the per-game attempt
history; ``el.rosters`` the sweep in progress. Conditional-request validators ride along, bounded.

Written against documentation
-----------------------------
None of this has run against the real service. The first run on the Mac should be
``python -m nbastats.euroleague.ingest probe``; this module is what runs afterwards.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Final, Mapping

from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from ...intel import feeds as intel_feeds
from ...intel.http import Conditional, FetchError, PoliteClient
from ...intel.robots import RobotsChecker
from ..config import ElSettings, check_same_store, default_data_dir, get_el_settings
from ..db import ElStoreError, StoreKindMismatch, get_el_engine
from ..models import (
    ElClub,
    ElClubSeason,
    ElGame,
    ElIntelNewsFeed,
    ElIntelNewsItem,
    ElIntelNewsSubject,
    ElPerson,
    ElRegistration,
    ElSeason,
    ElSourceState,
)
from ..profile import fold_name, is_denied_url, parse_season_ref
from . import endpoints, parse, reconcile, write
from .client import (
    DEFAULT_REQUEST_BUDGET,
    SOURCE_KEY,
    BudgetExhausted,
    EuroLeagueClient,
    Fetched,
    RawPayloadStore,
    classify_failure,
    el_raw_dir,
)
from .endpoints import EndpointNotAllowed, current_season_code

__all__ = [
    "StopLike",
    "stop_check",
    "BOX_ATTEMPT_LIMIT",
    "BOX_SLOW_RETRY",
    "MAX_BOX_PER_RUN",
    "MAX_POLLS_PER_GAME",
    "POLL_AFTER_TIPOFF",
    "POLL_EVERY",
    "NEWS_RETENTION",
    "JobEnv",
    "Outcome",
    "ServiceStatus",
    "latest_slot",
    "latest_monday",
    "run_structure",
    "run_rosters",
    "run_round",
    "run_box",
    "run_news",
    "run_backfill_plan",
    "run_backfill",
]

logger = logging.getLogger("nbastats.euroleague.ingest")

#: Local hour of the daily schedule sweep.
DAILY_HOUR: Final = 6
POLL_AFTER_TIPOFF: Final = timedelta(minutes=105)
POLL_EVERY: Final = timedelta(minutes=10)
#: A little under ten minutes, so a worker tick that fires a moment early still polls.
_POLL_SLACK: Final = timedelta(minutes=9)
MAX_POLLS_PER_GAME: Final = 12
MAX_ROUNDS_PER_RUN: Final = 3
#: Rounds holding a postponed game that the daily sweep reads besides the current and the next.
MAX_POSTPONED_ROUNDS: Final = 2
#: A game postponed for longer than this (counted from its original date) is no longer re-read.
POSTPONED_LOOKBACK: Final = timedelta(days=60)
MAX_BOX_PER_RUN: Final = 12
#: A game whose box is missing is tried this many times at the poll cadence...
BOX_ATTEMPT_LIMIT: Final = 12
#: ...then this many more at this spacing, and then left alone until a person asks.
BOX_SLOW_ATTEMPTS: Final = 8
BOX_SLOW_RETRY: Final = timedelta(hours=6)
BOX_CORRECTIONS: Final = (timedelta(hours=24), timedelta(hours=48))
NEWS_RETENTION: Final = timedelta(days=30)
#: After a response the parsers could not read, a job waits this long before asking again, then
#: twice as long, up to the ceiling (the same six hours the circuit breaker uses). ``force`` skips
#: the wait. Nothing about an unreadable shape fixes itself in ten minutes, and the worker would
#: otherwise ask again at every tick for as long as it lasted.
UNREADABLE_BACKOFF_BASE: Final = timedelta(hours=1)
UNREADABLE_BACKOFF_MAX: Final = timedelta(hours=6)
_VALIDATORS_KEPT: Final = 60
#: A roster that cannot be read is tried this many times in one sweep, then left until the next.
_CLUB_RETRIES: Final = 2
_MISSING: Final = object()


# ----------------------------------------------------------------------------------- time


def _aware(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _naive(moment: datetime) -> datetime:
    return _aware(moment).replace(tzinfo=None)


def _stamp(moment: datetime | None) -> str | None:
    return _naive(moment).isoformat(timespec="seconds") if moment is not None else None


def _unstamp(text: Any) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def local_zone() -> tzinfo:
    """The machine's own time zone (the daily 06:00 is the owner's 06:00)."""
    return datetime.now().astimezone().tzinfo or timezone.utc


def latest_slot(now: datetime, hour: int, zone: tzinfo) -> datetime:
    """The newest ``hour``:00 in ``zone`` at or before ``now``, as naive UTC."""
    local = _aware(now).astimezone(zone)
    slot = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if slot > local:
        slot -= timedelta(days=1)
    return _naive(slot)


def latest_monday(now: datetime, zone: tzinfo) -> datetime:
    """The newest Monday 00:00 in ``zone`` at or before ``now``, as naive UTC."""
    local = _aware(now).astimezone(zone)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start -= timedelta(days=start.weekday())
    return _naive(start)


# ------------------------------------------------------------------------------- shutdown

#: What a job may be handed as ``shutdown``: nothing, a callable returning a bool, an object with
#: ``is_set()`` (``threading.Event``, the worker's ``StopSignal``), or anything with a truth value.
StopLike = Callable[[], bool] | Any


def stop_check(shutdown: StopLike | None) -> Callable[[], bool]:
    """A zero-argument "should I stop?" for whatever the caller passed as ``shutdown``.

    The worker passes its ``StopSignal``, which is truthy while the process is stopping and has no
    ``__call__``; tests and the command line pass nothing or a lambda. Calling the object
    unconditionally was a ``TypeError`` waiting for the first real shutdown, so the shapes are
    sorted out here, once.
    """
    if shutdown is None:
        return lambda: False
    is_set = getattr(shutdown, "is_set", None)
    if callable(is_set):
        return lambda: bool(is_set())
    if callable(shutdown):
        return lambda: bool(shutdown())
    return lambda: bool(shutdown)


# ---------------------------------------------------------------------------------- results


@dataclass
class Outcome:
    """What a job body decided. ``status`` is ``ok`` or ``skipped``; failures are exceptions.

    ``source`` is ``None`` for a skip (the source row is left alone) or the source row's new
    state: ``ok``, or ``unreadable``/``error`` for a run that made requests and could not use
    what came back. ``error`` is then the reason.
    """

    status: str
    detail: str
    requests: int = 0
    source: str | None = None
    error: str | None = None
    source_detail: dict[str, Any] = field(default_factory=dict)

    def to_job_result(self) -> dict[str, Any]:
        if self.source in ("unreadable", "error"):
            return {
                "status": "error",
                "error": f"{self.source}: {self.error or self.detail}",
                "detail": self.detail,
                "requests": self.requests,
            }
        return {"status": self.status, "detail": self.detail, "requests": self.requests}


class ServiceStatus(Exception):
    """The service answered with a status the job cannot use (a 5xx after the retries, a 400)."""

    def __init__(self, what: str, status: int) -> None:
        super().__init__(f"{what}: the service answered {status}")
        self.status = status


@dataclass
class JobEnv:
    """Everything one run needs, built lazily so a run with nothing due touches nothing."""

    key: str
    now: datetime
    settings: ElSettings
    engine: Engine
    shutdown: Callable[[], bool]
    zone: tzinfo
    force: bool
    data_dir: Path
    client_factory: Callable[[], EuroLeagueClient]
    _client: EuroLeagueClient | None = None

    @property
    def naive_now(self) -> datetime:
        return _naive(self.now)

    @property
    def client(self) -> EuroLeagueClient:
        if self._client is None:
            self._client = self.client_factory()
        return self._client

    @property
    def requests(self) -> int:
        return self._client.requests_made if self._client is not None else 0

    @property
    def raw(self) -> RawPayloadStore:
        return RawPayloadStore(el_raw_dir(self.data_dir))

    def stopping(self) -> bool:
        return bool(self.shutdown())

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


# ----------------------------------------------------------------------------- framework


def _open_existing_store(settings: ElSettings) -> Engine:
    """The engine for the configured store, which must already exist (this never creates it)."""
    reason = check_same_store(settings)
    if reason:
        raise ElStoreError(reason)
    path = settings.store_path
    if path is not None and not path.exists():
        raise _NoStore(f"the EuroLeague store {path} does not exist yet; open the API once")
    engine = get_el_engine(settings.database_url)
    if "el_store_identity" not in set(inspect(engine).get_table_names()):
        raise _NoStore("the EuroLeague store has no tables yet; open the API once")
    return engine


class _NoStore(Exception):
    pass


def _skipped(detail: str) -> dict[str, Any]:
    return {"status": "skipped", "detail": detail, "requests": 0}


def _error(message: str, requests: int = 0) -> dict[str, Any]:
    return {"status": "error", "error": message, "detail": message, "requests": requests}


def _sessions(engine: Engine) -> "sessionmaker[Session]":
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _source_detail(session: Session) -> dict[str, Any]:
    row = session.get(ElSourceState, SOURCE_KEY)
    if row is None or not row.detail_json:
        return {}
    try:
        value = json.loads(row.detail_json)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _paused(session: Session, now: datetime) -> ElSourceState | None:
    row = session.get(ElSourceState, SOURCE_KEY)
    if row is not None and row.paused_until is not None and row.paused_until > now:
        return row
    return None


def execute(
    key: str,
    body: Callable[[JobEnv, Session], Outcome],
    *,
    now: datetime | None = None,
    shutdown: StopLike | None = None,
    force: bool = False,
    data_dir: Path | str | None = None,
    engine: Engine | None = None,
    client: EuroLeagueClient | None = None,
    settings: ElSettings | None = None,
    zone: tzinfo | None = None,
    needs_data_service: bool = True,
    request_budget: int | None = DEFAULT_REQUEST_BUDGET,
) -> dict[str, Any]:
    """Run one job body inside the framework and return the worker's result dict.

    Order of business: switches, store, breaker, body, then the source row. Nothing here raises;
    the body's exceptions become the source row's state and an ``error`` result.
    """
    moment = _aware(now)
    try:
        resolved = settings or get_el_settings()
    except ValueError as exc:
        return _error(str(exc))
    if not resolved.enabled:
        return _skipped("the EuroLeague is switched off (HARDWOOD_EL_ENABLED)")
    if needs_data_service and not resolved.live:
        return _skipped("EuroLeague live ingest is switched off (HARDWOOD_EL_LIVE)")
    try:
        store = engine or _open_existing_store(resolved)
    except _NoStore as exc:
        return _skipped(str(exc))
    except ElStoreError as exc:
        return _error(str(exc))

    base = Path(data_dir) if data_dir is not None else (resolved.data_dir or default_data_dir())
    env = JobEnv(
        key=key,
        now=moment,
        settings=resolved,
        engine=store,
        shutdown=stop_check(shutdown),
        zone=zone or local_zone(),
        force=force,
        data_dir=base,
        client_factory=lambda: _new_client(env, client, request_budget),
    )
    factory = _sessions(store)
    started = _naive(moment)
    try:
        with factory() as session:
            if needs_data_service:
                pause = _paused(session, started)
                if pause is not None:
                    return _skipped(
                        f"requests are paused until {pause.paused_until:%Y-%m-%d %H:%M} UTC "
                        f"({pause.last_error or 'the service refused a request'})"
                    )
                hold = None if force else _unreadable_hold(session, key, started)
                if hold is not None:
                    return _skipped(
                        "the last answer could not be read, so the service is not asked again "
                        f"until {hold:%Y-%m-%d %H:%M} UTC (run with --force to try now)"
                    )
            try:
                outcome = body(env, session)
                session.commit()
            except StoreKindMismatch as exc:
                session.rollback()
                return _error(str(exc), env.requests)
            except Exception as exc:  # noqa: BLE001 - classified below, never raised to the worker
                session.rollback()
                return _record_failure(factory, env, exc, started, needs_data_service)
            if outcome.source == "ok":
                write.mark_source_ok(
                    session,
                    SOURCE_KEY,
                    started,
                    detail=_merged(session, key, outcome.source_detail),
                )
                if needs_data_service:
                    _clear_unreadable(session, key)
            elif outcome.source in ("unreadable", "error"):
                write.mark_source_failed(
                    session,
                    SOURCE_KEY,
                    started,
                    state=outcome.source,
                    error=outcome.error or outcome.detail,
                    detail=_merged(session, key, outcome.source_detail),
                )
                if outcome.source == "unreadable" and needs_data_service:
                    _note_unreadable(session, key, started)
            session.commit()
            return outcome.to_job_result() | {"requests": env.requests}
    finally:
        env.close()


def _merged(session: Session, key: str, detail: Mapping[str, Any]) -> dict[str, Any]:
    """The source row's detail with this job's part replaced (jobs share one source row)."""
    current = _source_detail(session)
    if detail:
        current[key] = dict(detail)
    return current


def _new_client(
    env: JobEnv, supplied: EuroLeagueClient | None, budget: int | None
) -> EuroLeagueClient:
    if supplied is not None:
        return supplied
    client = EuroLeagueClient(max_requests=budget, stop_requested=env.stopping)
    with _sessions(env.engine)() as session:
        pause = _paused(session, env.naive_now)
        if pause is not None:
            client.restore_breaker(
                pause.paused_until.replace(tzinfo=timezone.utc), pause.last_error
            )
    return client


def _record_failure(
    factory: "sessionmaker[Session]",
    env: JobEnv,
    exc: BaseException,
    started: datetime,
    touches_service: bool = True,
) -> dict[str, Any]:
    """Turn an exception from a body into the source row's state and an ``error`` result.

    ``touches_service`` is ``False`` for the headline job: a feed that cannot be read says nothing
    about the data service, whose row is therefore left exactly as it was.
    """
    if isinstance(exc, parse.Unreadable):
        state, reason, paused = "unreadable", f"{exc.path}: {exc.reason}", None
    elif isinstance(exc, ServiceStatus):
        state, reason, paused = "error", str(exc), None
    else:
        failure = classify_failure(exc)
        state, reason, paused = failure.state, failure.reason, failure.paused_until
        if not isinstance(exc, (EndpointNotAllowed, BudgetExhausted, FetchError)):
            logger.error("%s failed unexpectedly", env.key, exc_info=exc)
    if paused is not None:
        paused = _naive(paused)
    if touches_service:
        try:
            with factory() as session:
                write.mark_source_failed(
                    session, SOURCE_KEY, started, state=state, error=reason, paused_until=paused
                )
                if state == "unreadable":
                    _note_unreadable(session, env.key, started)
                session.commit()
        except Exception:  # noqa: BLE001 - failing to record must not hide the real failure
            logger.exception("could not record the failure of %s", env.key)
    logger.warning("el job %s: %s: %s", env.key, state, reason)
    return _error(f"{state}: {reason}", env.requests)


# --------------------------------------------------------------------------- fetch helper


def _conditional(cursor: Mapping[str, Any], url: str) -> Conditional | None:
    entry = (cursor.get("validators") or {}).get(url)
    if isinstance(entry, dict) and (entry.get("etag") or entry.get("lastModified")):
        return Conditional(entry.get("etag"), entry.get("lastModified"))
    return None


def _remember(cursor: dict[str, Any], fetched: Fetched) -> None:
    validators = fetched.validators()
    book: dict[str, Any] = cursor.setdefault("validators", {})
    if validators is None:
        book.pop(fetched.url, None)
        return
    book[fetched.url] = {"etag": validators.etag, "lastModified": validators.last_modified}
    while len(book) > _VALIDATORS_KEPT:  # insertion-ordered: forget the oldest
        book.pop(next(iter(book)))


def _payload(env: JobEnv, session: Session, fetched: Fetched, what: str) -> Any:
    """The decoded JSON of a response; ``None`` for a 304, ``_MISSING`` for a 404 or 410.

    A ``200`` body is saved gzipped and recorded; any other status the job cannot use raises
    :class:`ServiceStatus`; a body that is not JSON raises :class:`~parse.Unreadable`.
    """
    if fetched.not_modified:
        return None
    if fetched.missing:
        return _MISSING
    if not fetched.ok:
        raise ServiceStatus(what, fetched.status)
    ref = env.raw.save(fetched)
    write.record_raw_payload(session, fetched, ref, env.raw)
    return parse.decode_json(fetched.body, what=what)


# ------------------------------------------------------------------------------- seasons


def _newest_season(session: Session) -> str | None:
    rows = list(session.execute(select(ElSeason)).scalars())
    return max(rows, key=lambda s: s.start_year).season_code if rows else None


def _work_season(session: Session, now: datetime) -> str:
    """The season the round, box and roster jobs work on: the newest stored, else the calendar's."""
    return _newest_season(session) or current_season_code(_aware(now))


def _weekly_due(last: datetime | None, env: JobEnv) -> bool:
    return last is None or last < latest_monday(env.now, env.zone)


def _round_ahead(session: Session, season: str, now: datetime) -> bool:
    """True on the day before a *round* begins, which is when clubs and rosters are read again.

    That is: a scheduled game is on tomorrow's date in Berlin, in a round none of whose games
    (postponed ones aside) is dated today or earlier. A round runs over two to four days, and the
    days between its first game and its last are not "the day before a round": counting them
    would read twenty-one rosters on both the Wednesday and the Thursday of a Thursday-and-Friday
    round, a request volume the design's budget of about ninety a week does not have room for. A
    postponed game moved to a date in a round that is already under way does not count either.

    The competition keeps its calendar in Berlin, so the day is Berlin's. ``now`` is naive UTC.
    """
    today = now.replace(tzinfo=timezone.utc).astimezone(parse.BERLIN).date()
    tomorrow = today + timedelta(days=1)
    rounds = {
        number
        for (number,) in session.execute(
            select(ElGame.round_number).where(
                ElGame.season_code == season,
                ElGame.status == "scheduled",
                ElGame.game_date == tomorrow,
            )
        )
    }
    for number in rounds:
        begun = session.execute(
            select(ElGame.game_id)
            .where(
                ElGame.season_code == season,
                ElGame.round_number == number,
                ElGame.status != "postponed",
                ElGame.game_date <= today,
            )
            .limit(1)
        ).first()
        if begun is None:
            return True
    return False


def _release(session: Session) -> None:
    """End the session's transaction before a request, so none is held open across the network.

    A SQLite read transaction kept open for seconds while a response is awaited holds a snapshot
    that goes stale if another process (the API writing an availability entry) commits meanwhile;
    in WAL mode the job's next write then fails at once with "database is locked", and
    ``busy_timeout`` does not help. Committing here costs nothing (anything pending is wanted)
    and the next statement simply begins a fresh transaction.
    """
    session.commit()


def _checkpoint(session: Session, key: str, cursor: Mapping[str, Any]) -> None:
    """Save the job's cursor and commit what the run has done so far.

    Called after every unit of work (a round, a game, a club), so a failure later in the same run
    cannot lose the bookkeeping of what already happened: an attempt count, a poll count, a
    correction timestamp. If the commit itself fails the session is already unusable and the
    caller's own rollback and failure handling take over, so this never raises over the top of the
    error that brought it here.
    """
    try:
        write.write_cursor(session, key, cursor)
        session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("could not checkpoint the cursor of %s", key, exc_info=True)


def unreadable_delay(count: int) -> timedelta:
    """How long to wait after the ``count``-th unreadable answer in a row: 1 h, 2 h, 4 h, 6 h..."""
    return min(UNREADABLE_BACKOFF_MAX, UNREADABLE_BACKOFF_BASE * (2 ** max(0, count - 1)))


def _unreadable_hold(session: Session, key: str, now: datetime) -> datetime | None:
    """When the job may ask again, if it is waiting out an unreadable answer; else ``None``."""
    until = _unstamp(write.read_cursor(session, key).get("unreadableUntil"))
    return until if until is not None and until > now else None


def _note_unreadable(session: Session, key: str, now: datetime) -> None:
    """Start (or lengthen) the wait after an answer the parsers could not read."""
    cursor = write.read_cursor(session, key)
    count = int(cursor.get("unreadableCount") or 0) + 1
    until = now + unreadable_delay(count)
    cursor["unreadableCount"] = count
    cursor["unreadableUntil"] = _stamp(until)
    write.write_cursor(session, key, cursor)
    # ``/v1/el/sources`` shows ``last_error``, so say there when the service will be asked again.
    # (``mark_source_failed`` has just replaced the text, so there is no earlier note to remove.)
    row = session.get(ElSourceState, SOURCE_KEY)
    if row is not None and row.last_error:
        row.last_error = (
            f"{row.last_error[:1800]} (not asked again until {until:%Y-%m-%d %H:%M} UTC)"
        )


def _clear_unreadable(session: Session, key: str) -> None:
    """A run read what the service sent: forget any wait."""
    cursor = write.read_cursor(session, key)
    had_count = cursor.pop("unreadableCount", None) is not None
    had_until = cursor.pop("unreadableUntil", None) is not None
    if had_count or had_until:
        write.write_cursor(session, key, cursor)


# ---------------------------------------------------------------------------- structure


def _calendar(cursor: Mapping[str, Any]) -> list[dict[str, Any]]:
    rounds = cursor.get("rounds")
    if not isinstance(rounds, list):
        return []
    return [r for r in rounds if isinstance(r, dict) and isinstance(r.get("round"), int)]


def _structure(env: JobEnv, session: Session) -> Outcome:
    now = env.naive_now
    cursor = write.read_cursor(session, "el.structure")
    calendar_season = current_season_code(env.now)
    newest = _newest_season(session)
    season = calendar_season if newest is None or calendar_season > newest else newest
    last_e1, last_e4 = _unstamp(cursor.get("e1_at")), _unstamp(cursor.get("e4_at"))
    weekly = _weekly_due(last_e1, env)
    # With no calendar at all (the new season's is not published) ask once a day, not every tick.
    no_calendar_retry = not _calendar(cursor) and (
        last_e1 is None or now - last_e1 > timedelta(hours=20)
    )
    due_e1 = env.force or weekly or no_calendar_retry
    due_e4 = (
        env.force
        or _weekly_due(last_e4, env)
        or (
            _round_ahead(session, season, now)
            and (last_e4 is None or now - last_e4 > timedelta(hours=20))
        )
    )
    if not (due_e1 or due_e4):
        return Outcome("skipped", "the round calendar and the clubs are current")
    write.ensure_live_writer(session)
    notes: list[str] = []
    detail: dict[str, Any] = {}
    requests_before = env.requests

    if due_e1:
        url = endpoints.rounds_url(season)
        _release(session)
        fetched = env.client.rounds(season, validators=_conditional(cursor, url))
        payload = _payload(env, session, fetched, "E1 rounds")
        _remember(cursor, fetched)
        rounds = None
        if payload is not None and payload is not _MISSING:
            rounds = parse.parse_rounds(payload, expect_season=season)
        if (
            (payload is _MISSING or (rounds is not None and not rounds.items))
            and newest
            and season != newest
        ):
            # The new season's calendar is not published yet: stay on the one we have.
            notes.append(f"{season} has no calendar yet; staying on {newest}")
            season = newest
            _release(session)
            fetched = env.client.rounds(season)
            payload = _payload(env, session, fetched, "E1 rounds")
            _remember(cursor, fetched)
            rounds = (
                None
                if payload in (None, _MISSING)
                else parse.parse_rounds(payload, expect_season=season)
            )
        if payload is _MISSING:
            notes.append(f"E1: the service has no rounds for {season} (404)")
        elif rounds is not None:
            write.ensure_season(session, season)
            cursor["rounds"] = [
                {
                    "round": r.round,
                    "phase": r.phase_code,
                    "name": r.name,
                    "first": r.first_start.isoformat() if r.first_start else None,
                    "last": r.last_start.isoformat() if r.last_start else None,
                }
                for r in rounds.items
            ]
            cursor["season"] = season
            detail["rounds"] = len(rounds.items)
            if rounds.rejected:
                notes.extend(f"E1 rejected {r}" for r in rounds.rejected[:5])
            if not rounds.complete:
                notes.append(
                    f"E1: the service says {rounds.total} rounds but sent {len(rounds.items)}"
                )
        cursor["e1_at"] = _stamp(env.now)

    if due_e4:
        url = endpoints.clubs_url(season)
        _release(session)
        fetched = env.client.clubs(season, validators=_conditional(cursor, url))
        payload = _payload(env, session, fetched, "E4 clubs")
        _remember(cursor, fetched)
        if payload is _MISSING:
            notes.append(f"E4: the service has no clubs for {season} (404)")
        elif payload is not None:
            clubs = parse.parse_clubs(payload, expect_season=season)
            write.ensure_season(session, season)
            report = reconcile.reconcile_all(session, season, clubs=clubs.items, now=now)
            session.commit()
            write.log_run(
                session,
                job="structure",
                started_at=now,
                status="partial" if report.needs_attention else "ok",
                rows_written=len(report.clubs.created) + len(report.people.matched),
                detail=report.to_dict(),
            )
            detail["clubsVerified"] = sorted(report.clubs.verified)
            detail["clubMismatches"] = [
                {"code": c, "reason": r} for c, r in report.clubs.mismatches
            ]
            detail["crosswalkConfirmed"] = sorted(report.clubs.crosswalk_confirmed)
            if report.clubs.mismatches:
                notes.append(f"{len(report.clubs.mismatches)} club code(s) disagree with the store")
            if clubs.rejected:
                notes.extend(f"E4 rejected {r}" for r in clubs.rejected[:5])
            if not clubs.complete:
                notes.append(
                    f"E4: the service says {clubs.total} clubs but sent {len(clubs.items)}"
                )
        cursor["e4_at"] = _stamp(env.now)
    write.write_cursor(session, "el.structure", cursor)
    summary = ", ".join(
        f"{k} {v if not isinstance(v, list) else len(v)}" for k, v in detail.items()
    )
    text = f"structure for {season}: {summary or 'nothing new'}" + (
        f" ({'; '.join(notes)})" if notes else ""
    )
    return Outcome("ok", text, env.requests - requests_before, "ok", source_detail=detail)


def run_structure(
    now: datetime | None = None,
    shutdown: StopLike | None = None,
    force: bool = False,
    data_dir: Path | str | None = None,
    *,
    engine: Engine | None = None,
    client: EuroLeagueClient | None = None,
    settings: ElSettings | None = None,
    zone: tzinfo | None = None,
) -> dict[str, Any]:
    """``el.structure``: the round calendar (E1) and the clubs (E4). See the module docstring."""
    return execute(
        "el.structure",
        _structure,
        now=now,
        shutdown=shutdown,
        force=force,
        data_dir=data_dir,
        engine=engine,
        client=client,
        settings=settings,
        zone=zone,
    )


# ------------------------------------------------------------------------------- rosters


def _clubs_of_season(session: Session, season: str) -> list[str]:
    codes = {
        code
        for (code,) in session.execute(
            select(ElClubSeason.club_code).where(ElClubSeason.season_code == season)
        )
    }
    if not codes:
        codes = {code for (code,) in session.execute(select(ElClub.club_code))}
    return sorted(codes)


def _rosters(env: JobEnv, session: Session) -> Outcome:
    now = env.naive_now
    cursor = write.read_cursor(session, "el.rosters")
    season = _work_season(session, env.now)
    sweep = cursor.get("sweep") if isinstance(cursor.get("sweep"), dict) else None
    last_done = _unstamp(cursor.get("complete_at"))
    due = (
        env.force
        or sweep is not None  # one is in progress: finish it
        or _weekly_due(last_done, env)
        or (
            _round_ahead(session, season, now)
            and (last_done is None or now - last_done > timedelta(hours=20))
        )
    )
    if not due:
        return Outcome("skipped", "the rosters are current")
    write.ensure_live_writer(session)
    clubs = _clubs_of_season(session, season)
    if not clubs:
        return Outcome("skipped", "no clubs are known yet (waiting for el.structure)")
    if sweep is None or sweep.get("season") != season:
        sweep = {"season": season, "started": _stamp(env.now), "done": [], "failed": {}}
    failed: dict[str, int] = dict(sweep.get("failed") or {})
    done: list[str] = [c for c in sweep["done"] if c in clubs]
    # a club that could not be read twice in this sweep is given up on until the next sweep
    gave_up = [c for c in clubs if c not in done and failed.get(c, 0) >= _CLUB_RETRIES]
    todo = [c for c in clubs if c not in done and c not in gave_up]
    requests_before = env.requests
    people_total = matched = 0
    notes: list[str] = []
    unreadable: list[str] = []
    for club in todo:
        if env.stopping():
            notes.append("stopped by shutdown")
            break
        if env.client.remaining == 0:
            notes.append("request budget used; the sweep resumes next run")
            break
        url = endpoints.club_people_url(season, club)
        _release(session)
        fetched = env.client.people(season, club, validators=_conditional(cursor, url))
        _remember(cursor, fetched)
        payload = _payload(env, session, fetched, f"E5 people of {club}")
        if payload is _MISSING:
            notes.append(f"{club}: no registrations (404)")
            done.append(club)
            continue
        if payload is None:  # 304
            done.append(club)
            continue
        try:
            parsed = parse.parse_people(payload, expect_club=club, expect_season=season)
        except parse.Unreadable as exc:
            unreadable.append(f"{club} {exc.path}: {exc.reason}")
            failed[club] = failed.get(club, 0) + 1
            sweep["failed"] = failed
            if len(unreadable) >= 2 and not done:  # two clubs, none good: the shape has changed
                raise
            _checkpoint(session, "el.rosters", {**cursor, "sweep": sweep})
            continue
        result = write.upsert_people(session, season, club, parsed.items, now=now)
        report = reconcile.reconcile_people(
            session, season, {club: parsed.items}, now=now, only_clubs={club}
        )
        people_total += len(parsed.items)
        matched += len(report.matched)
        if parsed.rejected:
            notes.extend(f"{club} rejected {r}" for r in parsed.rejected[:3])
        done.append(club)
        sweep["done"] = done
        write.write_cursor(session, "el.rosters", {**cursor, "sweep": sweep})
        session.commit()
    abandoned = [c for c in clubs if c not in done and failed.get(c, 0) >= _CLUB_RETRIES]
    for club in abandoned:
        notes.append(f"{club}: unreadable twice in this sweep; left until the next one")
    remaining = [c for c in clubs if c not in done and c not in abandoned]
    if remaining:
        sweep["done"] = done
        cursor["sweep"] = sweep
    else:
        cursor.pop("sweep", None)
        cursor["complete_at"] = _stamp(env.now)
        write.log_run(
            session,
            job="rosters",
            started_at=now,
            status="ok",
            rows_written=people_total,
            detail={
                "season": season,
                "clubs": len(clubs),
                "peopleMatched": matched,
                "notes": notes[:10],
            },
        )
    write.write_cursor(session, "el.rosters", cursor)
    text = (
        f"rosters for {season}: {len(done)}/{len(clubs)} clubs, {people_total} players, "
        f"{matched} matched to the workbook" + (f" ({'; '.join(notes)})" if notes else "")
    )
    if unreadable and not people_total:
        return Outcome("ok", text, env.requests - requests_before, "unreadable", unreadable[0])
    return Outcome("ok", text, env.requests - requests_before, "ok")


def run_rosters(
    now: datetime | None = None,
    shutdown: StopLike | None = None,
    force: bool = False,
    data_dir: Path | str | None = None,
    *,
    engine: Engine | None = None,
    client: EuroLeagueClient | None = None,
    settings: ElSettings | None = None,
    zone: tzinfo | None = None,
) -> dict[str, Any]:
    """``el.rosters``: every club's registered players (E5), resumable. See the module docstring."""
    return execute(
        "el.rosters",
        _rosters,
        now=now,
        shutdown=shutdown,
        force=force,
        data_dir=data_dir,
        engine=engine,
        client=client,
        settings=settings,
        zone=zone,
    )


# --------------------------------------------------------------------------------- round


def _current_and_next(
    session: Session, cursor: Mapping[str, Any], season: str, env: JobEnv
) -> list[int]:
    """The round being played and the one after it, which the daily sweep reads.

    "Being played" is the earliest round that still has a *scheduled* game. A postponed game is
    deliberately not counted: it can wait weeks for a new date, and if it counted, one postponed
    game in round 5 would keep the sweep on rounds 5 and 6 for the rest of the season while the
    league moved on, so the fixtures of every later round would never be fetched. Rounds holding a
    postponed game are read separately (:func:`_postponed_rounds`).
    """
    calendar = sorted(r["round"] for r in _calendar(cursor))
    stored = list(
        session.execute(
            select(ElGame.round_number, ElGame.status).where(ElGame.season_code == season)
        )
    )
    if stored:
        unfinished = sorted({r for r, status in stored if status == "scheduled"})
        known = sorted({r for r, _ in stored})
        current = unfinished[0] if unfinished else known[-1]
    elif calendar:
        today = env.now.astimezone(parse.BERLIN).date() - timedelta(days=1)
        by_round = {r["round"]: _unstamp(r.get("last")) for r in _calendar(cursor)}
        upcoming = [n for n in calendar if by_round[n] is None or by_round[n].date() >= today]
        current = upcoming[0] if upcoming else calendar[-1]
    else:
        return []
    later = [n for n in calendar if n > current]
    following = later[0] if later else (None if calendar else current + 1)
    return [current] + ([following] if following is not None else [])


def _postponed_rounds(session: Session, season: str, now: datetime) -> list[int]:
    """Rounds, oldest first, that hold a game the service has postponed and not yet re-dated.

    A postponed game's new date arrives in its own round's E2 answer, so that round has to be read
    again or the game stays "postponed" for ever and, once it is played, no result is ever polled
    for it (polls are for scheduled games). Two things keep the cost bounded: only the most recent
    :data:`MAX_POSTPONED_ROUNDS` such rounds are read, and a game that has been postponed for more
    than :data:`POSTPONED_LOOKBACK` of its original date is let go (it is not coming back this
    week, and a person can re-run ``run round --force`` if it does). ``now`` is naive UTC.
    """
    today = now.replace(tzinfo=timezone.utc).astimezone(parse.BERLIN).date()
    rounds = {
        number
        for (number,) in session.execute(
            select(ElGame.round_number).where(
                ElGame.season_code == season,
                ElGame.status == "postponed",
                ElGame.game_date >= today - POSTPONED_LOOKBACK,
            )
        )
    }
    return sorted(sorted(rounds, reverse=True)[:MAX_POSTPONED_ROUNDS])


def _daily_rounds(
    session: Session, cursor: Mapping[str, Any], season: str, env: JobEnv
) -> list[int]:
    """Every round the 06:00 sweep reads: the current one, the next, and any postponed one."""
    base = _current_and_next(session, cursor, season, env)
    extra = [n for n in _postponed_rounds(session, season, env.naive_now) if n not in base]
    return base + extra


def _pending_polls(
    session: Session, season: str, polls: Mapping[str, Any], now: datetime
) -> dict[int, list[str]]:
    """``{round: [game ids]}`` whose result is overdue and whose poll is due."""
    out: dict[int, list[str]] = {}
    rows = session.execute(
        select(ElGame).where(
            ElGame.season_code == season,
            ElGame.status == "scheduled",
            ElGame.tipoff_utc.is_not(None),
            ElGame.tipoff_utc <= now - POLL_AFTER_TIPOFF,
        )
    ).scalars()
    for game in rows:
        entry = polls.get(game.game_id) or {}
        if int(entry.get("n", 0)) >= MAX_POLLS_PER_GAME:
            continue
        last = _unstamp(entry.get("last"))
        if last is not None and now - last < _POLL_SLACK:
            continue
        out.setdefault(game.round_number, []).append(game.game_id)
    return out


def _round(env: JobEnv, session: Session) -> Outcome:
    now = env.naive_now
    cursor = write.read_cursor(session, "el.round")
    season = _work_season(session, env.now)
    polls: dict[str, Any] = dict(cursor.get("polls") or {})
    slot = latest_slot(env.now, DAILY_HOUR, env.zone)
    slot_key = _stamp(slot)
    last_daily = _unstamp(cursor.get("daily_at"))
    daily = env.force or last_daily is None or last_daily < slot
    # Rounds of this slot's sweep already read by an earlier run that had to stop short (a run
    # reads at most MAX_ROUNDS_PER_RUN rounds). A forced run starts the sweep again.
    swept: set[int] = set()
    book = cursor.get("daily_swept")
    if not env.force and isinstance(book, dict) and book.get("slot") == slot_key:
        swept = {n for n in book.get("rounds", []) if isinstance(n, int)}
    daily_rounds: list[int] = []
    due_polls = _pending_polls(session, season, polls, now)
    plan: set[int] = set(due_polls)  # results first: they are what a person is waiting for
    if daily:
        daily_rounds = _daily_rounds(
            session, write.read_cursor(session, "el.structure"), season, env
        )
        plan |= {number for number in daily_rounds if number not in swept}
    if not plan:
        if daily and not daily_rounds:  # the sweep stays due until the calendar exists
            return Outcome("skipped", "no round is known yet (waiting for el.structure)")
        return Outcome("skipped", "no schedule sweep or result poll is due")
    write.ensure_live_writer(session)
    phases = {
        r["round"]: r.get("phase")
        for r in _calendar(write.read_cursor(session, "el.structure"))
        if r.get("phase")
    }
    requests_before = env.requests
    summary: list[str] = []
    notes: list[str] = []
    new_results: list[str] = []

    def mark_swept(number: int) -> None:
        """A round of the daily sweep has been read and understood (or the service has none)."""
        if number in daily_rounds:
            swept.add(number)
            cursor["daily_swept"] = {"slot": slot_key, "rounds": sorted(swept)}

    # Result polls first, then the sweep, each oldest round first. A run reads a few rounds and the
    # sweep goes on at the next tick from where it stopped (see ``daily_swept``), so nothing that
    # does not fit is dropped.
    ordered = sorted(plan, key=lambda number: (number not in due_polls, number))
    for number in ordered[:MAX_ROUNDS_PER_RUN]:
        reason = "poll" if number in due_polls else "daily"
        if env.stopping():
            notes.append("stopped by shutdown")
            break
        url = endpoints.games_url(season, number)
        _release(session)
        fetched = env.client.games(season, number, validators=_conditional(cursor, url))
        payload = _payload(env, session, fetched, f"E2 round {number}")
        _remember(cursor, fetched)
        polled = due_polls.get(number, [])
        for gid in polled:
            entry = polls.setdefault(gid, {"n": 0})
            entry["n"] = int(entry.get("n", 0)) + 1
            entry["last"] = _stamp(env.now)
        cursor["polls"] = polls
        _checkpoint(session, "el.round", cursor)  # the attempt happened, whatever comes of it
        if payload is _MISSING:
            notes.append(f"round {number}: the service has no games (404)")
            mark_swept(number)
            _checkpoint(session, "el.round", cursor)
            continue
        if payload is None:
            summary.append(f"round {number}: unchanged")
            mark_swept(number)
            _checkpoint(session, "el.round", cursor)
            continue
        parsed = parse.parse_games(
            payload, expect_season=season, expect_round=number, phase_by_round=phases
        )
        result = write.upsert_games(session, season, parsed.items, now=now)
        session.commit()
        for gid in result.results:
            polls.pop(gid, None)
        new_results += result.results
        mark_swept(number)
        _checkpoint(session, "el.round", cursor)
        summary.append(f"round {number}: {result.summary()}")
        for item in (*parsed.rejected, *result.rejected):
            notes.append(f"rejected {item}")
        notes.extend(result.notes[:5])
        flagged_without_score = [
            g for g in parsed.items if g.played is True and g.status != "final"
        ]
        if flagged_without_score and not any(g.status == "final" for g in parsed.items if g.played):
            raise parse.Unreadable(
                f"{len(flagged_without_score)} game(s) are flagged played but none has a usable "
                "score, so the shape of local.score / road.score has probably changed",
                path="data[].local.score",
            )
        if not parsed.complete:
            notes.append(
                f"round {number}: the service says {parsed.total} games "
                f"but sent {len(parsed.items)}"
            )
        write.log_run(
            session,
            job="round",
            started_at=now,
            status="partial" if (parsed.rejected or result.rejected) else "ok",
            games_written=len(result.created) + len(result.updated),
            detail={
                "round": number,
                "reason": reason,
                "result": result.summary(),
                "notes": notes[:10],
            },
        )
    if daily and all(n in swept for n in daily_rounds):
        cursor["daily_at"] = _stamp(env.now)  # the whole sweep is done: not due until tomorrow
        cursor.pop("daily_swept", None)
    # forget poll counters for games that are no longer scheduled
    live_ids = {
        gid
        for (gid,) in session.execute(
            select(ElGame.game_id).where(ElGame.season_code == season, ElGame.status == "scheduled")
        )
    }
    cursor["polls"] = {gid: v for gid, v in polls.items() if gid in live_ids}
    write.write_cursor(session, "el.round", cursor)
    text = "; ".join(summary) or "nothing changed"
    if new_results:
        text += f" ({len(new_results)} new result(s))"
    if notes:
        text += f" [{'; '.join(notes[:4])}]"
    return Outcome(
        "ok",
        text,
        env.requests - requests_before,
        "ok",
        source_detail={"lastRound": sorted(ordered[:MAX_ROUNDS_PER_RUN])},
    )


def run_round(
    now: datetime | None = None,
    shutdown: StopLike | None = None,
    force: bool = False,
    data_dir: Path | str | None = None,
    *,
    engine: Engine | None = None,
    client: EuroLeagueClient | None = None,
    settings: ElSettings | None = None,
    zone: tzinfo | None = None,
) -> dict[str, Any]:
    """``el.round``: the schedule and results (E2). See the module docstring for the cadence."""
    return execute(
        "el.round",
        _round,
        now=now,
        shutdown=shutdown,
        force=force,
        data_dir=data_dir,
        engine=engine,
        client=client,
        settings=settings,
        zone=zone,
    )


# ----------------------------------------------------------------------------------- box


def _box_due(game: ElGame, entry: Mapping[str, Any], now: datetime) -> tuple[bool, int]:
    """``(due, priority)`` for one final game; lower priority numbers go first.

    Priority 0 is a game with no checked box score at all; 2 is a correction re-fetch (or the
    daily retry of a quarantined game); 3 is the one-time upgrade of a workbook game; 9 means
    "not due, and not going to be".
    """
    attempts = int(entry.get("attempts", 0))
    last = _unstamp(entry.get("last_attempt"))
    fetched = [t for t in (_unstamp(x) for x in entry.get("fetched", [])) if t is not None]
    if game.game_code is None:
        return False, 9  # the service has not named it yet: nothing to ask for
    quick = game.stats_status == "none"
    needs_first = quick or (
        game.stats_status == "ok" and game.data_source != "euroleague-v2" and not fetched
    )
    if needs_first:
        limit = BOX_ATTEMPT_LIMIT if quick else 1
        if attempts < limit:
            spacing = _POLL_SLACK
        elif attempts < limit + BOX_SLOW_ATTEMPTS:
            spacing = BOX_SLOW_RETRY
        else:
            return False, 9
        return (last is None or now - last >= spacing), (0 if quick else 3)
    if game.stats_status == "quarantined":
        if last is None:
            return True, 2
        if len(fetched) <= len(BOX_CORRECTIONS):
            return now - last >= BOX_CORRECTIONS[0], 2
        return False, 9
    if fetched:
        first = fetched[0]
        for index, wait in enumerate(BOX_CORRECTIONS, start=1):
            if len(fetched) == index and now - first >= wait:
                return True, 2
    return False, 9


def _box(env: JobEnv, session: Session) -> Outcome:
    now = env.naive_now
    cursor = write.read_cursor(session, "el.box")
    season = _work_season(session, env.now)
    book: dict[str, Any] = dict(cursor.get("games") or {})
    finals = list(
        session.execute(
            select(ElGame)
            .where(ElGame.season_code == season, ElGame.status == "final")
            .order_by(ElGame.game_date, ElGame.game_id)
        ).scalars()
    )
    due: list[tuple[int, str, ElGame]] = []
    for game in finals:
        is_due, priority = _box_due(game, book.get(game.game_id, {}), now)
        if is_due or (env.force and game.game_code is not None):
            due.append((priority, game.game_id, game))
    if not due:
        return Outcome("skipped", "no box score is due")
    due.sort(key=lambda item: (item[0], item[1]))
    write.ensure_live_writer(session)
    requests_before = env.requests
    summary: dict[str, int] = {}
    unreadable: list[str] = []
    notes: list[str] = []
    served = 0
    for _, gid, game in due[:MAX_BOX_PER_RUN]:
        if env.stopping():
            notes.append("stopped by shutdown")
            break
        if env.client.remaining == 0:
            notes.append("request budget used")
            break
        assert game.game_code is not None
        entry = book.setdefault(gid, {"attempts": 0, "fetched": []})
        entry["last_attempt"] = _stamp(env.now)
        cursor["games"] = book
        _checkpoint(session, "el.box", cursor)  # an attempt is recorded even if the fetch fails
        url = endpoints.game_stats_url(season, game.game_code)
        _release(session)
        fetched = env.client.box_score(season, game.game_code, validators=_conditional(cursor, url))
        _remember(cursor, fetched)
        if fetched.not_modified:
            entry["fetched"] = [*entry.get("fetched", []), _stamp(env.now)]
            summary["unchanged"] = summary.get("unchanged", 0) + 1
            _checkpoint(session, "el.box", cursor)
            continue
        if fetched.missing:
            entry["attempts"] = int(entry.get("attempts", 0)) + 1
            summary["notPublished"] = summary.get("notPublished", 0) + 1
            _checkpoint(session, "el.box", cursor)
            continue
        if not fetched.ok:
            raise ServiceStatus(f"E3 box score of game {game.game_code}", fetched.status)
        served += 1
        ref = env.raw.save(fetched)
        write.record_raw_payload(session, fetched, ref, env.raw)
        try:
            box = parse.parse_box_score(parse.decode_json(fetched.body, what=f"box score {gid}"))
        except parse.Unreadable as exc:
            entry["attempts"] = int(entry.get("attempts", 0)) + 1
            unreadable.append(f"{gid} {exc.path}: {exc.reason}")
            summary["unreadable"] = summary.get("unreadable", 0) + 1
            _checkpoint(session, "el.box", cursor)
            continue
        result = write.write_box_score(session, gid, box, now=now)
        entry["fetched"] = [*entry.get("fetched", []), _stamp(env.now)][-3:]
        entry["attempts"] = 0
        summary[result.outcome] = summary.get(result.outcome, 0) + 1
        if result.outcome == "quarantined":
            write.log_run(
                session,
                job="box",
                started_at=now,
                status="quarantined",
                games_written=0,
                invariant_failed=f"{gid}: {','.join(sorted({v.rule for v in result.hard}))}",
                detail={
                    "gameId": gid,
                    "violations": [f"{v.rule}: {v.message}" for v in result.hard[:8]],
                },
            )
            notes.append(f"{gid} quarantined ({', '.join(sorted({v.rule for v in result.hard}))})")
        elif result.outcome == "refused":
            notes.append(f"{gid} refused: {result.reason}")
        elif result.outcome == "written":
            write.mark_live_wrote(session)
            write.log_run(
                session,
                job="box",
                started_at=now,
                status="ok",
                games_written=1,
                rows_written=result.lines,
                detail={"gameId": gid, "soft": [f"{v.rule}: {v.message}" for v in result.soft[:8]]},
            )
        cursor["games"] = book
        _checkpoint(session, "el.box", cursor)
    cursor["games"] = {k: v for k, v in book.items() if k in {g.game_id for g in finals}}
    write.write_cursor(session, "el.box", cursor)
    text = ", ".join(f"{n} {k}" for k, n in sorted(summary.items())) or "nothing fetched"
    if notes:
        text += f" [{'; '.join(notes[:4])}]"
    spent = env.requests - requests_before
    if unreadable and served and summary.get("unreadable", 0) == served:
        return Outcome("ok", text, spent, "unreadable", unreadable[0])
    return Outcome("ok", text, spent, "ok", source_detail=dict(summary))


def run_box(
    now: datetime | None = None,
    shutdown: StopLike | None = None,
    force: bool = False,
    data_dir: Path | str | None = None,
    *,
    engine: Engine | None = None,
    client: EuroLeagueClient | None = None,
    settings: ElSettings | None = None,
    zone: tzinfo | None = None,
) -> dict[str, Any]:
    """``el.box``: box scores for final games (E3), with corrections. See the module docstring."""
    return execute(
        "el.box",
        _box,
        now=now,
        shutdown=shutdown,
        force=force,
        data_dir=data_dir,
        engine=engine,
        client=client,
        settings=settings,
        zone=zone,
    )


# ---------------------------------------------------------------------------------- news


def _subject_matcher(
    session: Session, season: str | None
) -> Callable[[str], list[tuple[str, str]]]:
    """A function from a headline to ``[(club code, person code or '')]`` it is about.

    The name indices are built once (clubs, and the players registered this season) and reused for
    every headline of a feed. A club matches by its folded name or short name appearing in the
    folded title as a whole phrase; a player by his folded full name (two words or more). A name
    shared by two different people, or by two clubs, links to **neither**: an ambiguous name gets
    no link.
    """
    clubs: dict[str, set[str]] = {}
    for club in session.execute(select(ElClub)).scalars():
        for name in (club.name, club.short_name):
            folded = fold_name(name or "")
            if len(folded) >= 4:
                clubs.setdefault(folded, set()).add(club.club_code)
    unique_clubs = {name: next(iter(codes)) for name, codes in clubs.items() if len(codes) == 1}
    people: dict[str, set[tuple[str, str]]] = {}
    query = select(ElPerson.person_code, ElPerson.name, ElRegistration.club_code).join(
        ElRegistration, ElRegistration.person_code == ElPerson.person_code
    )
    if season is not None:
        query = query.where(ElRegistration.season_code == season)
    for code, name, club_code in session.execute(query):
        folded = fold_name(name)
        if len(folded.split()) >= 2:
            people.setdefault(folded, set()).add((code, club_code))
    unique_people = {
        name: sorted(owners)[0]
        for name, owners in people.items()
        if len({code for code, _ in owners}) == 1
    }

    def match(title: str) -> list[tuple[str, str]]:
        haystack = f" {fold_name(title)} "
        found: list[tuple[str, str]] = []
        for name, code in unique_clubs.items():
            if f" {name} " in haystack and (code, "") not in found:
                found.append((code, ""))
        for name, (person, club_code) in unique_people.items():
            if f" {name} " in haystack and (club_code, person) not in found:
                found.append((club_code, person))
        return found

    return match


def _news(env: JobEnv, session: Session, supplied: PoliteClient | None = None) -> Outcome:
    now = env.naive_now
    feeds = list(
        session.execute(
            select(ElIntelNewsFeed)
            .where(ElIntelNewsFeed.enabled.is_(True))
            .order_by(ElIntelNewsFeed.feed_id)
        ).scalars()
    )
    if not feeds:
        return Outcome("skipped", "no headline feed is enabled")
    due = [f for f in feeds if env.force or intel_feeds.feed_is_due(f.last_fetch_at, now)]
    if not due:
        return Outcome("skipped", "no headline feed is due")
    season = _newest_season(session)
    polite = supplied if supplied is not None else PoliteClient(stop_requested=env.stopping)
    owned = supplied is None  # a client the caller handed in is the caller's to close
    summary: list[str] = []
    try:
        robots = RobotsChecker(polite, wall_clock=lambda: env.now)
        for feed in due:
            if env.stopping():
                break
            state_key = f"el.news.{feed.feed_id}"
            outcome = intel_feeds.fetch_feed(
                polite,
                robots,
                feed.url,
                source_name=feed.name,
                conditional=Conditional(feed.etag, feed.last_modified),
                now=env.now,
                link_allowed=lambda link: not is_denied_url(link),
            )
            feed.last_fetch_at = now
            feed.last_status = (
                outcome.state
                if outcome.http_status is None
                else f"{outcome.state} {outcome.http_status}"
            )[:64]
            if outcome.robots_checked_at is not None:
                feed.robots_checked_on = _naive(outcome.robots_checked_at).date()
            if outcome.state == "disallowed":
                feed.enabled = False
                feed.disabled_reason = (outcome.reason or "robots.txt disallows this feed")[:200]
                write.mark_source_failed(
                    session,
                    state_key,
                    now,
                    state="error",
                    error=feed.disabled_reason,
                    touch_sync=False,
                )
                summary.append(f"{feed.name}: disabled ({feed.disabled_reason})")
            elif outcome.state == "ok" and outcome.parsed is not None:
                stored = _store_items(session, feed, outcome.parsed, now, season)
                feed.etag = outcome.etag[:200] if outcome.etag else None
                feed.last_modified = outcome.last_modified[:64] if outcome.last_modified else None
                write.mark_source_ok(session, state_key, now, touch_sync=False)
                summary.append(f"{feed.name}: {stored} new headline(s)")
            elif outcome.state == "notModified":
                write.mark_source_ok(session, state_key, now, touch_sync=False)
                summary.append(f"{feed.name}: unchanged")
            else:
                mapped = {"blocked": "blocked", "unreadable": "unreadable"}.get(
                    outcome.state, "error"
                )
                write.mark_source_failed(
                    session,
                    state_key,
                    now,
                    state=mapped,
                    error=outcome.reason or outcome.state,
                    paused_until=_naive(outcome.paused_until) if outcome.paused_until else None,
                    touch_sync=False,
                )
                summary.append(f"{feed.name}: {outcome.state}")
            session.commit()
        _prune_news(session, now)
    finally:
        if owned:
            polite.close()
    return Outcome("ok", "; ".join(summary) or "nothing fetched", 0, None)


def _store_items(
    session: Session, feed: ElIntelNewsFeed, parsed: Any, now: datetime, season: str | None
) -> int:
    existing = {
        guid
        for (guid,) in session.execute(
            select(ElIntelNewsItem.guid).where(ElIntelNewsItem.feed_id == feed.feed_id)
        )
        if guid
    }
    added = 0
    subjects = _subject_matcher(session, season)
    for item in parsed.items:
        if item.guid in existing:
            continue
        row = ElIntelNewsItem(
            feed_id=feed.feed_id,
            guid=item.guid[:300],
            title=item.title[:300],
            link=item.link,
            published_at=item.published_at,
            fetched_at=now,
            source_name=item.source_name[:80],
        )
        session.add(row)
        session.flush()
        for club, person in subjects(item.title):
            session.add(ElIntelNewsSubject(item_id=row.item_id, club_code=club, person_code=person))
        added += 1
    session.flush()
    return added


def _prune_news(session: Session, now: datetime) -> None:
    """Headlines from a feed are kept 30 days; a pasted link (no feed) is kept for good."""
    cutoff = now - NEWS_RETENTION
    old = [
        item_id
        for (item_id,) in session.execute(
            select(ElIntelNewsItem.item_id).where(
                ElIntelNewsItem.feed_id.is_not(None), ElIntelNewsItem.published_at < cutoff
            )
        )
    ]
    for item_id in old:
        for subject in session.execute(
            select(ElIntelNewsSubject).where(ElIntelNewsSubject.item_id == item_id)
        ).scalars():
            session.delete(subject)
        row = session.get(ElIntelNewsItem, item_id)
        if row is not None:
            session.delete(row)
    session.flush()


def run_news(
    now: datetime | None = None,
    shutdown: StopLike | None = None,
    force: bool = False,
    data_dir: Path | str | None = None,
    *,
    engine: Engine | None = None,
    settings: ElSettings | None = None,
    zone: tzinfo | None = None,
    polite: PoliteClient | None = None,
) -> dict[str, Any]:
    """``el.news``: headlines from the enabled feeds, hourly, ``robots.txt`` checked first.

    Not behind ``HARDWOOD_EL_LIVE`` (a feed is not the data service); the worker's own gate covers
    the headlines switch. Title, link, date and source name only; nothing else of an item is read
    and no article is ever fetched. A feed whose ``robots.txt`` disallows Hardwood is switched off
    with the reason, which ``/v1/el/sources`` shows.
    """
    return execute(
        "el.news",
        lambda env, session: _news(env, session, polite),
        now=now,
        shutdown=shutdown,
        force=force,
        data_dir=data_dir,
        engine=engine,
        settings=settings,
        zone=zone,
        needs_data_service=False,
    )


# ------------------------------------------------------------------------------ backfill


def run_backfill_plan(
    season: str, *, rounds: int | None = None, games_per_round: int = 10, clubs: int = 20
) -> dict[str, int]:
    """The request count a back-fill of ``season`` would make, so it can be printed first.

    One E1, one E4, one E2 per round, one E5 per club and one E3 per game. ``rounds`` defaults to
    the number the calendar would give (the plan is an estimate until E1 has been read, and says
    so).
    """
    endpoints.validate_season_code(season)
    number = rounds if rounds is not None else 38
    games = number * games_per_round
    return {
        "rounds": 1,
        "clubs": 1,
        "schedule": number,
        "rosters": clubs,
        "boxScores": games,
        "total": 2 + number + clubs + games,
    }


def run_backfill(
    season: str,
    *,
    confirmed: bool,
    out: Callable[[str], None] = print,
    engine: Engine | None = None,
    client: EuroLeagueClient | None = None,
    settings: ElSettings | None = None,
    data_dir: Path | str | None = None,
    now: datetime | None = None,
    shutdown: StopLike | None = None,
) -> dict[str, Any]:
    """Load a whole past season. CLI only, and only with ``--yes``; it prints the plan first.

    Without ``confirmed`` it prints the plan and makes **no request**. With it, the backfill
    fetches E1, E4, every round's E2, every club's E5 and every final game's E3 for ``season``,
    politely (one request at a time with the floor between), in one pass that can be interrupted
    and resumed (everything already stored is kept, and an unchanged game writes nothing). The
    request budget is lifted for this command alone; the breaker is not.
    """
    endpoints.validate_season_code(season)
    plan = run_backfill_plan(season)
    out(
        f"Back-filling {season} would make about {plan['total']} requests "
        f"(1 calendar, 1 clubs, ~{plan['schedule']} rounds, ~{plan['rosters']} rosters, "
        f"~{plan['boxScores']} box scores), at one a second or slower: roughly "
        f"{plan['total'] // 60 + 1} minute(s)."
    )
    if not confirmed:
        out("Nothing was requested. Run again with --yes to proceed.")
        return {"status": "skipped", "detail": "not confirmed", "requests": 0, "plan": plan}
    return execute(
        "el.backfill",
        lambda env, session: _backfill(env, session, season, out),
        now=now,
        shutdown=shutdown,
        force=True,
        data_dir=data_dir,
        engine=engine,
        client=client,
        settings=settings,
        request_budget=None,
    )


def _backfill(env: JobEnv, session: Session, season: str, out: Callable[[str], None]) -> Outcome:
    now = env.naive_now
    write.ensure_live_writer(session)
    ref = parse_season_ref(season)
    assert ref is not None
    _release(session)
    fetched = env.client.rounds(season)
    payload = _payload(env, session, fetched, "E1 rounds")
    if payload is _MISSING or payload is None:
        return Outcome("ok", f"the service has no calendar for {season}", env.requests, "ok")
    rounds = parse.parse_rounds(payload, expect_season=season)
    write.ensure_season(session, season)
    phases = {r.round: r.phase_code for r in rounds.items if r.phase_code}
    _release(session)
    payload = _payload(env, session, env.client.clubs(season), "E4 clubs")
    if payload not in (None, _MISSING):
        clubs = parse.parse_clubs(payload, expect_season=season)
        reconcile.reconcile_all(session, season, clubs=clubs.items, now=now)
    session.commit()
    games = 0
    for info in sorted(rounds.items, key=lambda r: r.round):
        if env.stopping():
            break
        _release(session)
        payload = _payload(
            env, session, env.client.games(season, info.round), f"E2 round {info.round}"
        )
        if payload in (None, _MISSING):
            continue
        parsed = parse.parse_games(
            payload, expect_season=season, expect_round=info.round, phase_by_round=phases
        )
        result = write.upsert_games(session, season, parsed.items, now=now)
        session.commit()
        games += len(result.created)
        out(f"round {info.round}: {result.summary()}")
    wrote = 0
    finals = list(
        session.execute(
            select(ElGame)
            .where(ElGame.season_code == season, ElGame.status == "final")
            .order_by(ElGame.game_id)
        ).scalars()
    )
    for game in finals:
        if env.stopping():
            break
        if (
            game.stats_status == "ok"
            and game.data_source == "euroleague-v2"
            or game.game_code is None
        ):
            continue
        _release(session)
        fetched = env.client.box_score(season, game.game_code)
        payload = _payload(env, session, fetched, f"E3 box score {game.game_id}")
        if payload in (None, _MISSING):
            continue
        try:
            box = parse.parse_box_score(payload)
        except parse.Unreadable as exc:
            out(f"{game.game_id}: unreadable ({exc})")
            continue
        result = write.write_box_score(session, game.game_id, box, now=now)
        session.commit()
        wrote += int(result.wrote)
        if result.outcome != "written":
            out(f"{game.game_id}: {result.outcome} {result.reason or ''}".strip())
    write.mark_live_wrote(session)
    return Outcome(
        "ok", f"backfilled {season}: {games} games, {wrote} box scores", env.requests, "ok"
    )
