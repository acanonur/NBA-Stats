#!/usr/bin/env bash
#
# serve_dev.sh — seed a demo league and serve it on localhost.
#
# No network, no nba_api, no credentials: nbastats.seed generates a complete,
# deterministic synthetic league (30 real franchises, fictional players, a real
# calendar) and the API serves it exactly as it would serve ingested data. This
# is how the iOS app is developed and demoed offline.
#
# The seed is idempotent — it clears the tables first — so re-running replaces
# the league rather than doubling it. By default an existing database is reused;
# pass --fresh to rebuild it.
#
# Environment:
#   DATABASE_URL          SQLAlchemy URL (default sqlite:///./hardwood-dev.db)
#   HARDWOOD_API_KEY      when set, /v1 requires X-API-Key (except /v1/health)
#   HARDWOOD_DEMO_MODE    advertised in /v1/health; set to 1 here
#   PORT                  default 8000
#
# Usage: serve_dev.sh [--fresh] [--port N] [--seasons 2024-25,2025-26]
#                     [--games-per-team N] [--no-serve]

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PORT="${PORT:-8000}"
FRESH="0"
SERVE="1"
SEASONS="2024-25,2025-26"
GAMES_PER_TEAM="24"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fresh)           FRESH="1"; shift ;;
    --port)            PORT="${2:?--port needs a number}"; shift 2 ;;
    --seasons)         SEASONS="${2:?--seasons needs a comma-separated list}"; shift 2 ;;
    --games-per-team)  GAMES_PER_TEAM="${2:?--games-per-team needs a number}"; shift 2 ;;
    --no-serve)        SERVE="0"; shift ;;
    -h|--help)         sed -n '2,22p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)                 echo "serve_dev.sh: unknown argument $1" >&2; exit 1 ;;
  esac
done

export DATABASE_URL="${DATABASE_URL:-sqlite:///./hardwood-dev.db}"
export HARDWOOD_DEMO_MODE="${HARDWOOD_DEMO_MODE:-1}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

# A file-backed SQLite URL is the only case we can check for existence cheaply;
# anything else is seeded on request only.
SQLITE_PATH=""
if [[ "$DATABASE_URL" == sqlite:///* && "$DATABASE_URL" != "sqlite:///:memory:" ]]; then
  SQLITE_PATH="${DATABASE_URL#sqlite:///}"
fi

if [[ "$FRESH" == "1" && -n "$SQLITE_PATH" ]]; then
  rm -f "$SQLITE_PATH"
fi

if [[ "$FRESH" == "1" || -z "$SQLITE_PATH" || ! -f "$SQLITE_PATH" ]]; then
  echo "seeding the demo league into ${DATABASE_URL} ..."
  python3 -m nbastats.seed \
    --db "$DATABASE_URL" \
    --seasons "$SEASONS" \
    --games-per-team "$GAMES_PER_TEAM"
else
  echo "reusing ${SQLITE_PATH} (pass --fresh to rebuild it)"
fi

BASE="http://127.0.0.1:${PORT}"

cat <<EOF

Serving on ${BASE} — docs at ${BASE}/docs

  # Liveness and the freshness cursors. Never needs an API key.
  curl -s ${BASE}/v1/health | python3 -m json.tool

  # The three catalogs plus league state; the app caches this for 24 hours.
  curl -s ${BASE}/v1/meta | python3 -m json.tool | head -40

  # One round trip that renders a whole dashboard (CONTRACT.md §3). Each widget
  # resolves independently, so one failure degrades a tile rather than the screen
  # — expect a mix of "ok", "partial" and "error" in results[].
  # Served by nbastats/api/routes_dashboard.py, which app.py includes only if it
  # is present; without the widget layer this 404s and the rest still works.
  curl -s -X POST ${BASE}/v1/dashboard/resolve \\
    -H 'Content-Type: application/json' \\
    -d '{
      "context": {"favoriteTeamId": 1610612747, "timeZone": "America/New_York"},
      "widgets": [
        {"id": "w1", "kind": "scoreboard", "size": "large",
         "config": {"date": "latest"}},
        {"id": "w2", "kind": "leaderboard", "size": "large",
         "config": {"metric": "ts_pct", "season": "latest", "limit": 10}}
      ]
    }' | python3 -m json.tool

  # With HARDWOOD_API_KEY set, every /v1 call but health needs the header:
  #   curl -s -H "X-API-Key: \$HARDWOOD_API_KEY" ${BASE}/v1/meta

EOF

if [[ "$SERVE" == "0" ]]; then
  exit 0
fi

exec python3 -m uvicorn nbastats.api.app:app --reload --host 127.0.0.1 --port "$PORT"
