#!/usr/bin/env bash
#
# backfill.sh — load league history from bulk files that were downloaded once.
#
# ----------------------------------------------------------------------------
# DO NOT BACKFILL THROUGH THE API. Not with this script, not with a loop around
# nbastats.ingest.daily, not "just for one season".
#
# There are ~64,000 games in league history and roughly 1.3-1.7 million
# player-game rows. At the polite rate this project holds itself to (one request
# per second), pulling them from stats.nba.com one game at a time is tens of
# thousands of requests and days of runtime — and it would be rude besides. The
# open bulk datasets already contain that history in a handful of files.
#
# The split is deliberate: this script loads the past from files, and
# scripts/ingest_daily.sh keeps the present current from the API.
# ----------------------------------------------------------------------------
#
# Sources (download them yourself first; none are fetched here):
#
#   --kaggle FILE    Wyatt Walsh's "NBA Database" — a single SQLite file with
#                    games and box scores from 1946. Opened read-only.
#   --bbref DIR      Sumitro Datta's Basketball-Reference season dumps (CSV).
#                    The only practical source of pre-1997 PER, WS, BPM and VORP;
#                    every row lands with is_estimated = true, because those are
#                    box-score derivations rather than measurements.
#   --parquet DIR    hoopR / shufinskiy release files. pyarrow is optional — with
#                    it parquet is read, without it the directory's CSVs are, and
#                    each skipped parquet file is reported rather than ignored.
#
# Hardwood never scrapes Basketball-Reference itself: their terms forbid building
# tools on scraped data and their rate limit is enforced with hour-long blocks.
# These loaders read the redistributed compilations. See docs/LEGAL.md.
#
# Every loader is chunked, resumable (finished seasons are recorded in
# ingest_log and skipped on a re-run) and idempotent, so an interrupted overnight
# load continues rather than restarting, and a second run changes nothing.
#
# Environment:
#   DATABASE_URL   SQLAlchemy URL (default sqlite:///./hardwood.db). Postgres is
#                  strongly preferred for a full-history load.
#   LOG_LEVEL      INFO by default.
#
# Usage:
#   backfill.sh --kaggle nba.sqlite [--seasons 1996-97,1997-98] [--no-resume]
#   backfill.sh --bbref ./bbref-csv [--seasons 1985-86]
#   backfill.sh --parquet ./hoopr
#   backfill.sh --reconcile              # rebuild id_crosswalk only
#   backfill.sh --aggregate 1996-97      # recompute one season's derived tables
#
# At least one source flag is required; several may be combined, and they run in
# the order that keeps the id crosswalk useful: Kaggle (people and games) first,
# then reconcile, then Basketball-Reference (which matches on the slug).
#
# Exit: 0 success, 1 bad usage, 3 a loader failed.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

KAGGLE=""
BBREF=""
PARQUET=""
SEASONS=""
AGGREGATE=""
RECONCILE="0"
RESUME="1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kaggle)    KAGGLE="${2:?--kaggle needs a path to the SQLite file}"; shift 2 ;;
    --bbref)     BBREF="${2:?--bbref needs a directory of season CSVs}"; shift 2 ;;
    --parquet)   PARQUET="${2:?--parquet needs a release directory}"; shift 2 ;;
    --seasons)   SEASONS="${2:?--seasons needs a comma-separated list}"; shift 2 ;;
    --aggregate) AGGREGATE="${2:?--aggregate needs a season, e.g. 1996-97}"; shift 2 ;;
    --reconcile) RECONCILE="1"; shift ;;
    --no-resume) RESUME="0"; shift ;;
    -h|--help)   sed -n '2,53p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)           echo "backfill.sh: unknown argument $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$KAGGLE$BBREF$PARQUET$AGGREGATE" && "$RECONCILE" == "0" ]]; then
  echo "backfill.sh: nothing to do — pass --kaggle, --bbref, --parquet, --reconcile or --aggregate" >&2
  echo "backfill.sh: run with --help for the full note on why history is never pulled from the API" >&2
  exit 1
fi

export DATABASE_URL="${DATABASE_URL:-sqlite:///./hardwood.db}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

echo "hardwood backfill: db=${DATABASE_URL}"

HARDWOOD_KAGGLE="$KAGGLE" HARDWOOD_BBREF="$BBREF" HARDWOOD_PARQUET="$PARQUET" \
HARDWOOD_SEASONS="$SEASONS" HARDWOOD_AGGREGATE="$AGGREGATE" \
HARDWOOD_RECONCILE="$RECONCILE" HARDWOOD_RESUME="$RESUME" python3 - <<'PY'
import json
import logging
import os
import sys

from nbastats.config import get_settings
from nbastats.db import create_db_engine, init_db, session_scope
from nbastats.ingest import aggregate, backfill

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

seasons = [s.strip() for s in os.environ.get("HARDWOOD_SEASONS", "").split(",") if s.strip()]
resume = os.environ.get("HARDWOOD_RESUME", "1") == "1"
reports = []

engine = create_db_engine(get_settings().database_url)
init_db(engine)

try:
    # Kaggle first: it supplies the people and the games everything else joins to.
    if os.environ.get("HARDWOOD_KAGGLE"):
        with session_scope(engine) as session:
            reports.append(
                backfill.load_kaggle_sqlite(
                    session,
                    os.environ["HARDWOOD_KAGGLE"],
                    seasons=seasons or None,
                    resume=resume,
                ).as_dict()
            )

    # Then the crosswalk, so the Basketball-Reference slugs have something to match.
    if os.environ.get("HARDWOOD_RECONCILE") == "1" or os.environ.get("HARDWOOD_BBREF"):
        with session_scope(engine) as session:
            reports.append(backfill.reconcile_ids(session).as_dict())

    if os.environ.get("HARDWOOD_BBREF"):
        with session_scope(engine) as session:
            reports.append(
                backfill.load_bbref_season_csvs(
                    session,
                    os.environ["HARDWOOD_BBREF"],
                    seasons=seasons or None,
                    resume=resume,
                ).as_dict()
            )

    if os.environ.get("HARDWOOD_PARQUET"):
        with session_scope(engine) as session:
            reports.append(
                backfill.load_parquet_dir(
                    session, os.environ["HARDWOOD_PARQUET"], seasons=seasons or None
                ).as_dict()
            )

    if os.environ.get("HARDWOOD_AGGREGATE"):
        with session_scope(engine) as session:
            reports.extend(
                report.as_dict()
                for report in aggregate.recompute_seasons(
                    session, [(os.environ["HARDWOOD_AGGREGATE"], None)]
                )
            )
except (FileNotFoundError, NotADirectoryError) as exc:
    print(f"backfill: {exc}", file=sys.stderr)
    raise SystemExit(3)

print(json.dumps(reports, indent=2))
unmatched = sum(report.get("unmatchedCount", 0) for report in reports)
if unmatched:
    print(
        f"{unmatched} source row(s) matched no known player and were reported, not guessed at.",
        file=sys.stderr,
    )
PY
