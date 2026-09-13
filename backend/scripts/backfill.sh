#!/usr/bin/env bash
# Load history from bulk files.
#
# DO NOT BACKFILL THROUGH THE API. Pulling ~35,000 games one at a time from stats.nba.com is
# 12+ hours at best and realistically days with backoff, and it is exactly the abuse the rate
# limits exist to stop. Download the bulk datasets instead — they are free, they are updated
# nightly, and they cover 1946 to today.
#
#   kaggle datasets download -d wyattowalsh/basketball        -p /data/nba   --unzip
#   kaggle datasets download -d sumitrodatta/nba-aba-baa-stats -p /data/bbref --unzip
#
# Usage:
#   scripts/backfill.sh /data/nba/nba.sqlite [/data/bbref]
#
# The Basketball-Reference dump is the only practical source of pre-1997 advanced seasons
# (PER, WS, BPM, VORP); those rows are written with is_estimated set, because they are
# box-score estimates rather than measured possessions.
#
# Both loaders are chunked, resumable and idempotent — re-running after an interruption is
# safe, and every run is recorded in the ingest_log table.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

kaggle_db="${1:-}"
bbref_dir="${2:-}"

if [[ -z "$kaggle_db" ]]; then
  echo "usage: $(basename "$0") <kaggle-nba.sqlite> [bbref-csv-dir]" >&2
  exit 64
fi

: "${DATABASE_URL:=sqlite:///./hardwood.db}"
export DATABASE_URL

echo "==> games and box scores from $kaggle_db"
python3 -m nbastats.ingest.runner --backfill-kaggle "$kaggle_db"

if [[ -n "$bbref_dir" ]]; then
  echo "==> pre-1997 advanced seasons from $bbref_dir"
  python3 -m nbastats.ingest.runner --backfill-bbref "$bbref_dir"
fi

# NBA.com integer ids, Basketball-Reference slugs and ESPN ids have no official mapping
# between them. Build the crosswalk before trusting any cross-source join, or the same player
# shows up twice.
echo "==> reconciling player identities"
python3 -m nbastats.ingest.runner --reconcile-ids
