#!/usr/bin/env bash
# The Hardwood server for the Mac launch check in CI (.github/workflows/mac.yml). Not for people:
# on a Mac of your own, backend/scripts/macos/install.sh is how the server is installed and run.
#
#   ci_mac_server.sh install     install the backend, seed the invented NBA league, write the key
#   ci_mac_server.sh start       start the API on 127.0.0.1:8000 (EuroLeague store: $EL_STORE.db)
#   ci_mac_server.sh stop        stop it
#   ci_mac_server.sh ingest-el   run the EuroLeague live jobs once each, as the worker would
#
# It sets the server up the way install.sh leaves a Mac: an API key in the installer's settings
# file (~/Library/Application Support/Hardwood/hardwood.env), which the app reads on launch, and the
# same key required by the server. HARDWOOD_EL_DEMO / HARDWOOD_EL_LIVE come from the caller.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
STATE="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/hardwood"
KEY="ci-launch-check-key"
PY="${PYTHON:-python}"

export DATABASE_URL="sqlite:///$STATE/hardwood.db"
export HARDWOOD_API_KEY="$KEY"
export HARDWOOD_DATA_DIR="$STATE"
export HARDWOOD_EL_DATABASE_URL="sqlite:///$STATE/${EL_STORE:-el}.db"
export PYTHONUNBUFFERED=1

say() { printf 'ci_mac_server.sh: %s\n' "$*"; }

# Runs a command for at most $1 seconds. macOS has no `timeout`.
capped() {
  local seconds="$1"; shift
  "$@" &
  local pid=$! waited=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge "$seconds" ]; then
      say "stopped after ${seconds}s: $*"
      kill "$pid" 2>/dev/null; sleep 2; kill -9 "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$pid"
}

stop_api() {
  pkill -f "[u]vicorn nbastats.api.app:app" || true
  for _ in $(seq 1 20); do
    curl -s -o /dev/null http://127.0.0.1:8000/v1/health || break
    sleep 1
  done
}

case "${1:-}" in
  install)
    "$PY" -m pip install --quiet -e "$ROOT/backend[serve]"
    mkdir -p "$STATE" "$HOME/Library/Application Support/Hardwood"
    printf 'HARDWOOD_API_KEY=%s\n' "$KEY" > "$HOME/Library/Application Support/Hardwood/hardwood.env"
    (cd "$ROOT/backend" && "$PY" -m nbastats.seed --db "$DATABASE_URL" --games-per-team 24 --quiet)
    ;;
  start)
    # A step that failed before its own `stop` leaves the last server running; this one must not
    # answer for it with the wrong EuroLeague store.
    stop_api
    (cd "$ROOT/backend" && nohup "$PY" -m uvicorn nbastats.api.app:app --host 127.0.0.1 --port 8000 \
       >> "$STATE/api.log" 2>&1 &)
    for _ in $(seq 1 60); do
      curl -sf http://127.0.0.1:8000/v1/health >/dev/null && break
      sleep 1
    done
    curl -sf http://127.0.0.1:8000/v1/health; echo
    curl -sf -H "X-API-Key: $KEY" http://127.0.0.1:8000/v1/leagues \
      | "$PY" -c 'import json,sys; [print(" ", r["key"], r["state"], "syncVersion", r.get("syncVersion"), "dataThrough", r.get("dataThrough"), r.get("reason") or "") for r in json.load(sys.stdin)]'
    ;;
  stop)
    stop_api
    ;;
  ingest-el)
    # The order the worker's own dependencies imply: the calendar and clubs, the squads, the
    # results, then the box scores of finished games, the ratings they update, and headlines.
    for job in el.structure el.rosters el.round el.box el.ratings el.news; do
      say "running $job"
      (cd "$ROOT/backend" && capped 300 "$PY" -m nbastats.worker --run "$job" --log-level WARNING) \
        || say "$job ended with an error (the launch check still runs on whatever arrived)"
    done
    ;;
  *)
    sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
