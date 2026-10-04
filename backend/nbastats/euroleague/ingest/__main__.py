"""``python -m nbastats.euroleague.ingest``: the EuroLeague ingest from the command line.

Commands
--------
``probe``             the first real run: five polite requests, recorded under
                      ``HARDWOOD_DATA_DIR/recordings/euroleague/``, parsed and explained. Writes no
                      database row. ``--offline DIR`` re-checks saved recordings with no request.
``import-workbook X`` import the user's workbook (``--dry-run`` to see what would happen). This is
                      the command the worker's inbox scan runs, and it exits non-zero on failure.
``backfill``          load a whole past season. Prints the request count first and makes no request
                      without ``--yes``.
``run JOB``           run one worker job now (``structure``, ``rosters``, ``round``, ``box``,
                      ``news``). ``--force`` skips the "is it due" decision and the wait after an
                      unreadable answer, never a switch and never the circuit breaker.
``reconcile``         match workbook people to official ones from what the store already holds
                      (no network) and print what matched, what is ambiguous and what waits.
``status``            what the store holds, the source's state and pause, and the last runs.

Exit codes: ``0`` success (a skipped job is success), ``1`` failure, ``2`` the service refused or
the request was refused locally.

The switches are the environment's (``HARDWOOD_EL_ENABLED``, ``HARDWOOD_EL_LIVE``,
``HARDWOOD_DATA_DIR``, ``HARDWOOD_EL_DATABASE_URL``); there is no terms variable of any kind.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

__all__ = ["main", "build_parser"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nbastats.euroleague.ingest",
        description="Hardwood's EuroLeague ingest (live service, workbook, diagnostics).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="make five requests, record and explain the responses")
    probe.add_argument("--season", help="season code such as E2026 (default: the current season)")
    probe.add_argument("--round", dest="round_number", type=int, help="round for E2 (default 1)")
    probe.add_argument("--game-code", type=int, help="game for E3 (default: the first played one)")
    probe.add_argument("--club", help="club for E5 (default: the first game's home club)")
    probe.add_argument("--out", help="a data directory to record under (default HARDWOOD_DATA_DIR)")
    probe.add_argument("--offline", metavar="DIR", help="re-check recordings in DIR; no network")

    workbook = sub.add_parser("import-workbook", help="import the user's workbook")
    workbook.add_argument("path")
    workbook.add_argument("--dry-run", action="store_true", help="run everything, then roll back")
    estimates = workbook.add_mutually_exclusive_group()
    estimates.add_argument(
        "--include-estimates",
        dest="include_estimates",
        action="store_true",
        default=None,
        help="store the workbook's est. per-40 lines, labelled as estimates (the default)",
    )
    estimates.add_argument(
        "--no-include-estimates",
        dest="include_estimates",
        action="store_false",
        help="keep only minutes and identity for est. lines",
    )

    backfill = sub.add_parser("backfill", help="load a past season (prints the plan first)")
    backfill.add_argument("--season", required=True, help="season code such as E2025")
    backfill.add_argument("--yes", action="store_true", help="really make the requests")

    run = sub.add_parser("run", help="run one worker job now")
    run.add_argument("job", choices=("structure", "rosters", "round", "box", "news"))
    run.add_argument("--force", action="store_true", help="skip the due-ness decision")

    sub.add_parser("reconcile", help="match workbook people to official ones (no network)")
    sub.add_parser("status", help="what the store holds and how the source is doing")
    return parser


# ---------------------------------------------------------------------------- commands


def _probe(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    from . import probe

    report = probe.run_probe(
        season=args.season,
        out=args.out,
        offline=args.offline,
        round_number=args.round_number,
        club=args.club,
        game_code=args.game_code,
        echo=out,
    )
    out(probe.render(report))
    return report.exit_code


def _import_workbook(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    from .. import bootstrap

    report = bootstrap.run_workbook_import(
        args.path, dry_run=args.dry_run, include_estimates=args.include_estimates
    )
    out(report.render())
    return 0 if report.ok else 1


def _backfill(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    from . import jobs

    result = jobs.run_backfill(args.season, confirmed=args.yes, out=out)
    if result["status"] == "error":
        out(f"error: {result.get('error')}")
        return 1
    if result["status"] == "ok":
        out(result.get("detail", "done"))
    return 0


def _run_job(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    from . import jobs

    function = {
        "structure": jobs.run_structure,
        "rosters": jobs.run_rosters,
        "round": jobs.run_round,
        "box": jobs.run_box,
        "news": jobs.run_news,
    }[args.job]
    result = function(force=args.force)
    out(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 1 if result.get("status") == "error" else 0


def _store_session() -> Session | None:
    from ..bootstrap import open_store
    from ..db import ElStoreError

    try:
        engine = open_store()
    except ElStoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None
    return Session(engine, future=True)


def _reconcile(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    from ..db import utcnow
    from ..models import ElSeason
    from . import reconcile

    session = _store_session()
    if session is None:
        return 1
    with session:
        seasons = list(session.execute(select(ElSeason)).scalars())
        if not seasons:
            out("The store has no season yet; there is nothing to reconcile.")
            return 0
        season = max(seasons, key=lambda s: s.start_year).season_code
        report = reconcile.reconcile_all(session, season, now=utcnow())
        session.commit()
    out(report.render())
    return 0


def _status(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    from ..models import (
        ElGame,
        ElIngestLog,
        ElJobState,
        ElSourceState,
        ElStoreIdentity,
        ElSyncState,
    )

    session = _store_session()
    if session is None:
        return 1
    with session:
        identity = session.get(ElStoreIdentity, 1)
        sync = session.get(ElSyncState, 1)
        out(f"store kind: {identity.kind if identity else 'not stamped yet'}")
        if sync is not None:
            out(
                f"sync version {sync.sync_version}, data through {sync.data_through}, "
                f"mode {sync.mode}, paused until {sync.paused_until or 'never'}"
            )
        for row in session.execute(
            select(ElSourceState).order_by(ElSourceState.source_key)
        ).scalars():
            out(
                f"source {row.source_key}: {row.state}, last success {row.last_success_at}, "
                f"failures {row.consecutive_failures}, paused until {row.paused_until or '-'}"
                + (f", last error: {row.last_error}" if row.last_error else "")
            )
        counts = session.execute(
            select(ElGame.status, ElGame.stats_status, func.count()).group_by(
                ElGame.status, ElGame.stats_status
            )
        ).all()
        for status, stats, number in counts:
            out(f"games {status}/{stats}: {number}")
        for row in session.execute(select(ElJobState).order_by(ElJobState.job_key)).scalars():
            out(
                f"job {row.job_key}: started {row.last_started_at}, success {row.last_success_at}"
                + (f", error {row.last_error}" if row.last_error else "")
            )
        for log in session.execute(
            select(ElIngestLog).order_by(ElIngestLog.id.desc()).limit(8)
        ).scalars():
            out(
                f"log #{log.id} {log.job} {log.status} at {log.started_at}"
                + (f" quarantined: {log.invariant_failed}" if log.invariant_failed else "")
                + (f" error: {log.error}" if log.error else "")
            )
    return 0


_COMMANDS: dict[str, Callable[[argparse.Namespace, Callable[[str], None]], int]] = {
    "probe": _probe,
    "import-workbook": _import_workbook,
    "backfill": _backfill,
    "run": _run_job,
    "reconcile": _reconcile,
    "status": _status,
}


def main(argv: Sequence[str] | None = None, out: Callable[[str], None] = print) -> int:
    """Run one command and return its exit code. ``out`` receives every line printed."""
    args = build_parser().parse_args(argv)
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        return _COMMANDS[args.command](args, out)
    except KeyboardInterrupt:
        out("interrupted")
        return 130


if __name__ == "__main__":  # pragma: no cover - exercised through main() in tests
    raise SystemExit(main())
