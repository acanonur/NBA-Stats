"""The ingest process: the loop that makes stats appear as each game finishes.

Three modes, all driving the functions in :mod:`nbastats.ingest.daily` and
:mod:`nbastats.ingest.backfill`:

``--watch``
    Poll the league scoreboard during the game window. The moment a game flips to Final,
    ingest that one ``game_id`` and bump ``sync_version`` for it. This is what lets the app
    show a box score minutes after the buzzer instead of the next morning.

``--once`` / ``--nightly``
    The bulk and correction paths. ``--once`` pulls a single date with one ``LeagueGameLog``
    call plus one ``PlayerGameLogs`` call per season, rather than one call per game.
    ``--nightly`` re-pulls the last ``CORRECTION_WINDOW_DAYS`` days because the league issues
    stat corrections after the fact, then re-aggregates.

``--backfill-*``
    The historical loaders, which read already-downloaded bulk files. Never backfill through
    the API: ~35,000 games at a safe request rate is days of runtime.

**Deployment constraint.** stats.nba.com silently drops requests from AWS, GCP and Azure IP
ranges — they hang rather than failing, which is a confusing way to lose an afternoon. Run
this process from a residential connection, or point ``NBA_API_PROXY`` at a residential
pass-through proxy. Only the database and the API belong in a cloud region. GitHub Actions
runners are usually on blocked ranges too, so test before relying on one.

Examples::

    python3 -m nbastats.ingest.runner --watch
    python3 -m nbastats.ingest.runner --once --date 2026-01-02
    python3 -m nbastats.ingest.runner --nightly
    python3 -m nbastats.ingest.runner --backfill-kaggle /data/nba/nba.sqlite
    python3 -m nbastats.ingest.runner --backfill-bbref /data/bbref --reconcile-ids
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from types import FrameType
from typing import Callable, Sequence

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import init_db, read_sync_state, session_scope
from . import backfill, daily
from .client import IngestUnavailable, StatsClient, UpstreamUnavailable, log_line

__all__ = [
    "DEFAULT_WINDOW_START_HOUR",
    "DEFAULT_WINDOW_END_HOUR",
    "ShutdownFlag",
    "WatchReport",
    "eastern_today",
    "in_game_window",
    "run_once",
    "run_nightly",
    "watch",
    "main",
]

logger = logging.getLogger("nbastats.ingest.runner")

#: The NBA schedules by US Eastern day, and games run late. The window is deliberately wide:
#: an early Saturday start is 12:00 ET and a west-coast game can finish after 01:00 ET the
#: next morning, which lands in the *following* Eastern day's early hours.
DEFAULT_WINDOW_START_HOUR = 11
DEFAULT_WINDOW_END_HOUR = 4

#: US Eastern without a tzdata dependency. The NBA's scheduling day is what matters here, and
#: being an hour off during the DST changeover only widens or narrows the polling window.
_EASTERN_OFFSET = timedelta(hours=-5)


def eastern_today(now: datetime | None = None) -> date:
    """The NBA scheduling date for ``now`` (US Eastern)."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment.astimezone(timezone.utc) + _EASTERN_OFFSET).date()


def in_game_window(
    now: datetime | None = None,
    *,
    start_hour: int = DEFAULT_WINDOW_START_HOUR,
    end_hour: int = DEFAULT_WINDOW_END_HOUR,
) -> bool:
    """True when games could plausibly be in progress, so polling is worth the requests.

    The window wraps midnight, which is why this is not a simple range check.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    hour = (moment.astimezone(timezone.utc) + _EASTERN_OFFSET).hour
    if start_hour <= end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


class ShutdownFlag:
    """Cooperative shutdown, so SIGTERM finishes the game in flight instead of tearing it up.

    A half-written game would be repaired by the correction window anyway, but leaving the
    database consistent on the way out is cheaper than relying on that.
    """

    def __init__(self) -> None:
        self._set = False

    def __bool__(self) -> bool:
        return self._set

    @property
    def is_set(self) -> bool:
        return self._set

    def request(self, signum: int | None = None, frame: FrameType | None = None) -> None:
        if not self._set:
            name = signal.Signals(signum).name if signum is not None else "shutdown"
            logger.info(log_line("shutdown_requested", signal=name))
        self._set = True

    def install(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self.request)
            except (ValueError, OSError):  # not on the main thread, or unsupported
                logger.debug("could not install a handler for %s", sig)


@dataclass
class WatchReport:
    """What one watch run did, returned so tests can assert on it without reading logs."""

    polls: int = 0
    games_ingested: int = 0
    errors: int = 0
    dates_seen: list[date] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)

    def record_date(self, value: date) -> None:
        if value not in self.dates_seen:
            self.dates_seen.append(value)


def _client(settings=None) -> StatsClient:
    settings = settings or get_settings()
    return StatsClient(proxy=settings.nba_api_proxy)


def _ingest_finalized(
    session: Session,
    game_date: date,
    *,
    client: StatsClient,
    report: WatchReport,
) -> int:
    """Ingest every game on ``game_date`` that has newly gone Final. Returns the count.

    Each game is written and committed on its own so that a failure on the fourth game of a
    slate does not roll back the first three, and so ``sync_version`` advances once per game —
    which is precisely what the client's incremental refresh keys off.
    """
    finalized = daily.poll_finalized_games(session, game_date, client=client)
    if not finalized:
        return 0

    ingested = 0
    for game_id in finalized:
        try:
            result = daily.ingest_game(session, game_id, client=client, game_date=game_date)
        except UpstreamUnavailable as exc:
            report.errors += 1
            logger.warning(log_line("game_ingest_deferred", game_id=game_id, error=str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - one bad game must not stop the slate
            report.errors += 1
            session.rollback()
            logger.exception(log_line("game_ingest_failed", game_id=game_id, error=str(exc)))
            continue

        if result.skipped:
            continue
        ingested += 1
        logger.info(
            log_line(
                "game_ingested",
                game_id=game_id,
                season=result.season,
                players=result.player_rows,
                advanced=result.advanced_rows,
                sync_version=result.sync_version,
            )
        )

    if ingested:
        daily.move_data_through(session, game_date)
    return ingested


def run_once(
    game_date: date | None = None,
    *,
    client: StatsClient | None = None,
    session: Session | None = None,
) -> daily.DayIngestResult:
    """Pull one date in bulk: one game-log call plus one advanced call per season."""
    target = game_date or eastern_today()
    owned = client or _client()

    def _work(active: Session) -> daily.DayIngestResult:
        result = daily.ingest_day(active, target, client=owned)
        logger.info(
            log_line(
                "day_ingested",
                date=target.isoformat(),
                games=result.games,
                players=result.player_rows,
                advanced=result.advanced_rows,
                finalized=result.finalized,
                data_through_moved=result.data_through_moved,
            )
        )
        return result

    if session is not None:
        return _work(session)
    with session_scope() as active:
        return _work(active)


def run_nightly(
    *,
    days: int | None = None,
    end_date: date | None = None,
    client: StatsClient | None = None,
    session: Session | None = None,
) -> list[daily.DayIngestResult]:
    """Re-pull the correction window. The league revises box scores after the fact."""
    settings = get_settings()
    span = days if days is not None else settings.correction_window_days
    owned = client or _client(settings)

    def _work(active: Session) -> list[daily.DayIngestResult]:
        results = daily.run_correction_window(active, span, client=owned, end_date=end_date)
        changed = sum(r.changed_rows for r in results)
        logger.info(
            log_line(
                "correction_window_complete",
                days=span,
                dates=len(results),
                games=sum(r.games for r in results),
                changed_rows=changed,
            )
        )
        if changed:
            logger.info(log_line("stat_corrections_applied", rows=changed))
        return results

    if session is not None:
        return _work(session)
    with session_scope() as active:
        return _work(active)


def watch(
    *,
    client: StatsClient | None = None,
    poll_seconds: int | None = None,
    idle_seconds: int = 900,
    shutdown: ShutdownFlag | None = None,
    max_polls: int | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    session: Session | None = None,
) -> WatchReport:
    """Poll for finalized games until asked to stop.

    ``max_polls``, ``sleeper`` and ``clock`` exist so this loop is testable without waiting on
    wall-clock time; in production the defaults are the real ones.
    """
    settings = get_settings()
    interval = poll_seconds if poll_seconds is not None else settings.ingest_poll_seconds
    flag = shutdown or ShutdownFlag()
    owned = client or _client(settings)
    report = WatchReport()

    logger.info(
        log_line("watch_started", poll_seconds=interval, idle_seconds=idle_seconds)
    )

    def _loop(active: Session) -> None:
        nightly_done_for: date | None = None
        while not flag:
            if max_polls is not None and report.polls >= max_polls:
                break
            now = clock()
            report.polls += 1

            if in_game_window(now):
                today = eastern_today(now)
                report.record_date(today)
                try:
                    count = _ingest_finalized(active, today, client=owned, report=report)
                except UpstreamUnavailable as exc:
                    report.errors += 1
                    count = 0
                    logger.warning(log_line("poll_deferred", error=str(exc)))
                report.games_ingested += count
                if count:
                    state = read_sync_state(active)
                    logger.info(
                        log_line(
                            "slate_progress",
                            date=today.isoformat(),
                            ingested=count,
                            sync_version=state.sync_version,
                            data_through=str(state.data_through),
                        )
                    )
                delay = float(interval)
            else:
                # Outside the window, take the chance to run the correction pass once a day.
                today = eastern_today(now)
                if nightly_done_for != today:
                    try:
                        run_nightly(client=owned, session=active)
                        nightly_done_for = today
                    except UpstreamUnavailable as exc:
                        report.errors += 1
                        logger.warning(log_line("nightly_deferred", error=str(exc)))
                delay = float(idle_seconds)

            if flag or (max_polls is not None and report.polls >= max_polls):
                break
            report.sleeps.append(delay)
            sleeper(delay)

    if session is not None:
        _loop(session)
    else:
        with session_scope() as active:
            _loop(active)

    logger.info(
        log_line(
            "watch_stopped",
            polls=report.polls,
            games=report.games_ingested,
            errors=report.errors,
        )
    )
    return report


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an ISO date (YYYY-MM-DD)") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m nbastats.ingest.runner",
        description="Hardwood ingest: watch for finished games, pull a day, or backfill history.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "stats.nba.com silently blocks AWS/GCP/Azure IP ranges: run this from a\n"
            "residential connection or set NBA_API_PROXY. Bulk files download fine anywhere."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--watch", action="store_true",
                      help="poll during the game window and ingest each game as it finishes")
    mode.add_argument("--once", action="store_true",
                      help="pull a single date in bulk and exit")
    mode.add_argument("--nightly", action="store_true",
                      help="re-pull the correction window and re-aggregate, then exit")
    mode.add_argument("--backfill-kaggle", metavar="SQLITE",
                      help="load Wyatt Walsh's NBA Database SQLite file")
    mode.add_argument("--backfill-bbref", metavar="DIR",
                      help="load Basketball-Reference season CSVs (pre-1997 advanced seasons)")
    mode.add_argument("--backfill-parquet", metavar="DIR",
                      help="load a hoopR / shufinskiy style parquet or CSV directory")
    mode.add_argument("--reconcile-ids", action="store_true",
                      help="build the nba_person_id to bbref_slug crosswalk and report misses")

    parser.add_argument(
        "--date",
        type=_parse_date,
        help="ISO date: the day for --once (default: today), or the LAST day of the window "
             "for --nightly (default: data_through, else today)",
    )
    parser.add_argument("--days", type=int, help="override the correction window for --nightly")
    parser.add_argument("--poll-seconds", type=int, help="override INGEST_POLL_SECONDS")
    parser.add_argument("--seasons", nargs="*", help="restrict a backfill to these seasons")
    parser.add_argument("--no-resume", action="store_true",
                        help="reload seasons a previous backfill already completed")
    parser.add_argument("--log-level", default=None, help="override LOG_LEVEL")
    return parser


def _report_load(report: backfill.LoadReport) -> None:
    logger.info(
        log_line(
            "backfill_complete",
            job=report.job,
            source=report.source,
            read=report.rows_read,
            written=report.rows_written,
            changed=report.rows_changed,
            skipped=report.rows_skipped,
            seasons=len(report.seasons),
        )
    )
    for note in report.notes:
        logger.info(log_line("backfill_note", note=note))
    if report.unmatched:
        logger.warning(
            log_line("backfill_unmatched", count=len(report.unmatched),
                     sample=", ".join(str(u) for u in list(report.unmatched)[:5]))
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    logging.basicConfig(
        level=(args.log_level or settings.log_level).upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    init_db()
    flag = ShutdownFlag()
    flag.install()

    try:
        if args.watch:
            watch(poll_seconds=args.poll_seconds, shutdown=flag)
            return 0

        if args.once:
            run_once(args.date)
            return 0

        if args.nightly:
            # --date is the window's END. It used to be parsed, accepted and silently dropped
            # here, which is worse than rejecting it: `--nightly --days 250 --date 2026-04-15`
            # looked like a season backfill and quietly walked back from today instead.
            run_nightly(days=args.days, end_date=args.date)
            return 0

        with session_scope() as session:
            resume = not args.no_resume
            if args.backfill_kaggle:
                _report_load(backfill.load_kaggle_sqlite(
                    session, args.backfill_kaggle, seasons=args.seasons, resume=resume))
            elif args.backfill_bbref:
                _report_load(backfill.load_bbref_season_csvs(
                    session, args.backfill_bbref, seasons=args.seasons, resume=resume))
            elif args.backfill_parquet:
                _report_load(backfill.load_parquet_dir(
                    session, args.backfill_parquet, seasons=args.seasons))
            else:
                result = backfill.reconcile_ids(session)
                logger.info(
                    log_line(
                        "crosswalk_built",
                        candidates=result.candidates,
                        matched=result.matched,
                        written=result.written,
                        ambiguous=len(result.ambiguous),
                        unmatched=len(result.unmatched),
                    )
                )
                if result.unmatched:
                    logger.warning(
                        log_line("crosswalk_unmatched", count=len(result.unmatched),
                                 sample=", ".join(str(u) for u in list(result.unmatched)[:5]))
                    )
        return 0

    except IngestUnavailable as exc:
        # The most common cause by far is a missing nba_api, closely followed by a datacenter
        # IP block. Say so rather than printing a bare traceback.
        logger.error(log_line("ingest_unavailable", error=str(exc)))
        logger.error(
            "Install nba_api (pip install nba_api) and run from a residential IP, or set "
            "NBA_API_PROXY. Bulk backfills need neither."
        )
        return 2
    except UpstreamUnavailable as exc:
        logger.error(log_line("upstream_unavailable", error=str(exc)))
        return 3
    except KeyboardInterrupt:
        logger.info(log_line("interrupted"))
        return 130


if __name__ == "__main__":  # pragma: no cover - exercised via the CLI
    sys.exit(main())
