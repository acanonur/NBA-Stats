#!/usr/bin/env bash
# Nightly correction pass: re-pull the last CORRECTION_WINDOW_DAYS days and re-aggregate.
#
# The NBA revises box scores after the fact, so last night's numbers are not final just
# because last night is over. This is the pass that catches that.
#
# WHERE THIS MUST RUN: stats.nba.com silently drops requests from AWS, GCP and Azure IP
# ranges — they hang rather than erroring, which is a confusing way to lose an afternoon.
# Run it from a residential connection, or set NBA_API_PROXY to a residential pass-through
# proxy. GitHub Actions runners are usually on blocked ranges too. Only the database and the
# API belong in a cloud region.
#
# cron (05:30 US Eastern, after the last west-coast game is long final):
#   30 5 * * *  /srv/hardwood/backend/scripts/ingest_daily.sh >> /var/log/hardwood-ingest.log 2>&1
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

: "${DATABASE_URL:=sqlite:///./hardwood.db}"
export DATABASE_URL

exec python3 -m nbastats.ingest.runner --nightly "$@"
