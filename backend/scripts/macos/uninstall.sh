#!/usr/bin/env bash
#
# uninstall.sh - stop Hardwood's background programs and remove them.
#
# By default this only stops the four launch agents (api, nba-watch, nba-nightly, worker) and
# deletes their files from ~/Library/LaunchAgents. Your data is kept: the databases, the settings
# file, the inbox and the logs stay where they are, so installing again picks up exactly where
# you left off.
#
# Usage:
#   backend/scripts/macos/uninstall.sh [options]
#
# Options:
#   --remove-venv   also delete the private Python environment (it is rebuilt by install.sh)
#   --purge-data    ALSO DELETE THE DATA FOLDER: both databases, your settings, the inbox and
#                   everything Hardwood has stored. This cannot be undone. You are asked to type
#                   DELETE to confirm, unless you also give --yes.
#   --yes           do not ask for confirmation (only matters with --purge-data)
#   --data-dir DIR  the data folder, if you installed with a different --data-dir
#   -h, --help      show this help
#
# For tests and unusual setups only:
#   HARDWOOD_INSTALL_HOME=DIR    treat DIR as the home folder
#   HARDWOOD_UNINSTALL_NO_LAUNCHCTL=1   do not call launchctl, only remove files

set -euo pipefail

LABELS="com.hardwood.api com.hardwood.nba-watch com.hardwood.nba-nightly com.hardwood.worker"

HOME_DIR="${HARDWOOD_INSTALL_HOME:-$HOME}"
DATA_DIR=""
REMOVE_VENV="0"
PURGE="0"
ASSUME_YES="0"

say()  { printf '%s\n' "$*"; }
warn() { printf 'Warning: %s\n' "$*" >&2; }
die()  { printf '\nHardwood uninstall stopped.\n%s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,/^set -euo pipefail/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --remove-venv) REMOVE_VENV="1"; shift ;;
    --purge-data)  PURGE="1"; shift ;;
    --yes)         ASSUME_YES="1"; shift ;;
    --data-dir)    [ $# -ge 2 ] || die "--data-dir needs a folder."; DATA_DIR="$2"; shift 2 ;;
    -h|--help)     usage; exit 0 ;;
    *)             die "I do not know the option '$1'. Try --help." ;;
  esac
done

AGENT_DIR="$HOME_DIR/Library/LaunchAgents"

# If the installer was given a different data folder, the installed worker agent remembers it.
# plutil is part of macOS; where it is missing (tests, other systems) the default is used.
if [ -z "$DATA_DIR" ] && command -v plutil >/dev/null 2>&1 \
   && [ -f "$AGENT_DIR/com.hardwood.worker.plist" ]; then
  DATA_DIR="$(plutil -extract EnvironmentVariables.HARDWOOD_DATA_DIR raw -o - \
              "$AGENT_DIR/com.hardwood.worker.plist" 2>/dev/null || true)"
fi
if [ -z "$DATA_DIR" ]; then
  DATA_DIR="$HOME_DIR/Library/Application Support/Hardwood"
fi

stop_agent() {
  local label="$1" domain tries=0
  [ "${HARDWOOD_UNINSTALL_NO_LAUNCHCTL:-0}" = "1" ] && return 0
  command -v launchctl >/dev/null 2>&1 || return 0
  domain="gui/$(id -u)"
  launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
  while launchctl print "$domain/$label" >/dev/null 2>&1 && [ "$tries" -lt 20 ]; do
    sleep 0.5; tries=$((tries + 1))
  done
}

# Deleting a folder is the one irreversible thing this script can do, so it refuses anything
# that does not look like a Hardwood data folder, and anything that could be a person's whole
# home folder.
safe_to_purge() {
  local dir="$1"
  case "$dir" in
    ""|"/"|"$HOME_DIR"|"$HOME_DIR/"|"$HOME_DIR/Library"|"$HOME_DIR/Library/Application Support")
      return 1 ;;
  esac
  [ -d "$dir" ] || return 1
  [ -f "$dir/hardwood.db" ] || [ -f "$dir/hardwood.env" ] || [ -f "$dir/venv/pyvenv.cfg" ]
}

say "Stopping Hardwood's background programs..."
for label in $LABELS; do
  stop_agent "$label"
  if [ -f "$AGENT_DIR/$label.plist" ]; then
    rm -f "$AGENT_DIR/$label.plist"
    say "Removed $label"
  else
    say "$label was not installed"
  fi
done

if [ "$REMOVE_VENV" = "1" ] || [ "$PURGE" = "1" ]; then
  if [ -f "$DATA_DIR/venv/pyvenv.cfg" ]; then
    rm -rf "$DATA_DIR/venv"
    say "Removed the Python environment"
  fi
fi

if [ "$PURGE" = "1" ]; then
  if ! safe_to_purge "$DATA_DIR"; then
    die "I will not delete '$DATA_DIR': it does not look like a Hardwood data folder.
Nothing else was deleted. If it really is yours, remove it by hand."
  fi
  if [ "$ASSUME_YES" != "1" ]; then
    say ""
    say "This will permanently delete everything in:"
    say "    $DATA_DIR"
    say "That includes the databases and your settings file."
    printf 'Type DELETE to continue: '
    answer=""
    read -r answer || true
    [ "$answer" = "DELETE" ] || die "Not confirmed. The data folder was kept."
  fi
  rm -rf "$DATA_DIR"
  say "Deleted $DATA_DIR"
else
  say ""
  say "Your data was kept in: $DATA_DIR"
  say "Logs are in:          $HOME_DIR/Library/Logs/Hardwood"
  say "Run the installer again whenever you like; it will pick up where this left off."
fi
