#!/usr/bin/env bash
# The long-running ingest process: poll during the game window and write each game the moment
# it goes final. This is what makes a box score appear in the app minutes after the buzzer
# rather than the next morning.
#
# Same deployment constraint as ingest_daily.sh: residential IP, or NBA_API_PROXY.
#
# As a systemd unit:
#   [Unit]
#   Description=Hardwood ingest
#   After=network-online.target
#   [Service]
#   ExecStart=/srv/hardwood/backend/scripts/ingest_watch.sh
#   Restart=always
#   RestartSec=30
#   [Install]
#   WantedBy=multi-user.target
#
# The process handles SIGTERM cooperatively, so `systemctl stop` finishes the game in flight
# instead of tearing it up mid-write.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

: "${DATABASE_URL:=sqlite:///./hardwood.db}"
: "${INGEST_POLL_SECONDS:=300}"
export DATABASE_URL INGEST_POLL_SECONDS

exec python3 -m nbastats.ingest.runner --watch "$@"
