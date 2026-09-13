#!/usr/bin/env bash
#
# ingest_daily.sh — the nightly stat-correction pass.
#
# Re-pulls the last CORRECTION_WINDOW_DAYS days (default 3) through the bulk
# endpoints and upserts them. The league revises box scores after the fact — a
# rebound reassigned, an assist added, days later — so a pipeline that only ever
# inserted would be wrong by the end of the week. Every write here is an upsert,
# so a correction replaces yesterday's row instead of duplicating it, and the
# season aggregates follow it.
#
# This is *not* the live path. Games are ingested the moment they go Final by the
# poll loop (nbastats.ingest.daily.poll_finalized_games -> ingest_game); this job
# only fixes up what the league changed afterwards.
#
# ----------------------------------------------------------------------------
# RUN THIS FROM A RESIDENTIAL IP.
#
# stats.nba.com silently blocks datacenter IP ranges. AWS, GCP, Azure and most
# GitHub Actions runners get no error and no 403 from the Akamai layer in front
# of the API: the request simply hangs until it times out. No amount of retrying
# fixes it, and on a cloud host that timeout is the *only* symptom you will see.
#
#   * Run this job from a home box, a Raspberry Pi, or a residential-IP VPS, or
#   * set NBA_API_PROXY to a residential pass-through proxy.
#
# Only the database and the read API are safe to host in a cloud region.
# ----------------------------------------------------------------------------
#
# Cron — 04:15 local, after the last West Coast game has been final for hours:
#
#   15 4 * * *  cd /srv/hardwood/backend && DATABASE_URL=postgresql://hardwood@localhost/hardwood \
#               ./scripts/ingest_daily.sh >> /var/log/hardwood/ingest.log 2>&1
#
# Environment:
#   DATABASE_URL            SQLAlchemy URL         (default sqlite:///./hardwood.db)
#   CORRECTION_WINDOW_DAYS  days re-pulled         (default 3)
#   NBA_API_PROXY           residential proxy URL  (optional)
#   HARDWOOD_INGEST_FIXTURES  directory of recorded payloads; set this and the run
#                           touches no network at all, which is how it is tested
#   LOG_LEVEL               INFO by default
#
# Usage: ingest_daily.sh [--days N] [--through YYYY-MM-DD]
# Exit:  0 success, 1 bad usage, 2 nothing reachable upstream, 3 any other failure.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

DAYS=""
THROUGH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --days)    DAYS="${2:?--days needs a number}"; shift 2 ;;
    --through) THROUGH="${2:?--through needs a YYYY-MM-DD date}"; shift 2 ;;
    -h|--help) sed -n '2,45p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)         echo "ingest_daily.sh: unknown argument $1" >&2; exit 1 ;;
  esac
done

export DATABASE_URL="${DATABASE_URL:-sqlite:///./hardwood.db}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

echo "hardwood ingest_daily: db=${DATABASE_URL} proxy=${NBA_API_PROXY:-none} fixtures=${HARDWOOD_INGEST_FIXTURES:-none}"

HARDWOOD_DAYS="$DAYS" HARDWOOD_THROUGH="$THROUGH" python3 - <<'PY'
import json
import logging
import os
import sys
from datetime import date

from nbastats.config import get_settings
from nbastats.db import create_db_engine, init_db, read_sync_state, session_scope
from nbastats.ingest.client import IngestUnavailable, UpstreamUnavailable, StatsClient
from nbastats.ingest.daily import record_ingest_log, run_correction_window

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

settings = get_settings()
days = int(os.environ["HARDWOOD_DAYS"]) if os.environ.get("HARDWOOD_DAYS") else None
through = (
    date.fromisoformat(os.environ["HARDWOOD_THROUGH"])
    if os.environ.get("HARDWOOD_THROUGH")
    else None
)

engine = create_db_engine(settings.database_url)
init_db(engine)
client = StatsClient()
print(client.describe())

try:
    with session_scope(engine) as session:
        results = run_correction_window(
            session, days, client=client, end_date=through, commit=False
        )
        state = read_sync_state(session)
        summary = {
            "days": [result.as_dict() for result in results],
            "syncVersion": state.sync_version,
            "dataThrough": state.data_through.isoformat() if state.data_through else None,
            "requests": client.stats.as_dict(),
        }
except UpstreamUnavailable as exc:
    print(f"upstream_unavailable: {exc}", file=sys.stderr)
    with session_scope(engine) as session:
        record_ingest_log(session, "correction", status="error", error=str(exc))
    raise SystemExit(2)
except IngestUnavailable as exc:
    print(f"ingest_unavailable: {exc}", file=sys.stderr)
    raise SystemExit(3)

print(json.dumps(summary, indent=2))
changed = sum(day["changedRows"] for day in summary["days"])
print(f"corrections applied: {changed} row(s); sync_version now {summary['syncVersion']}")
PY
