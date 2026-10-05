#!/usr/bin/env bash
#
# install.sh - set Hardwood up to run by itself on this Mac.
#
# What it does, in plain terms:
#   1. Finds a Python that is new enough (3.11 or later) and makes a private copy of the
#      Python packages Hardwood needs, inside the Hardwood data folder.
#   2. Creates the Hardwood data folder and a settings file (hardwood.env) you can edit, with a
#      private random API key in it (the Mac app reads the key from that file; it is never
#      printed, never overwritten once it exists, and the file is readable by you only).
#   3. Installs four background programs ("launch agents") that start when you log in:
#        com.hardwood.api          the Hardwood server, reachable from this Mac only
#        com.hardwood.nba-watch    brings in each NBA game as it finishes
#        com.hardwood.nba-nightly  re-checks the last few days of NBA games at 06:10
#        com.hardwood.worker       projections, injuries, headlines and EuroLeague updates
#   4. Starts them, and checks the server answers.
#
# It is safe to run again: it never overwrites your hardwood.env or your data, and it simply
# reloads the programs. Nothing needs sudo, and nothing leaves your home folder.
#
# Usage:
#   backend/scripts/macos/install.sh [options]
#
# Options:
#   --data-dir DIR   where Hardwood keeps its data
#                    (default: ~/Library/Application Support/Hardwood)
#   --port N         the port the server listens on, on 127.0.0.1 only (default: 8000)
#   --python PATH    use this Python instead of searching for one
#   --dry-run        say what would happen, change nothing
#   --no-load        write everything but do not start the background programs
#   --skip-pip       do not (re)install the Python packages
#   --no-api-key     do not create an API key in the settings file (the server then refuses every
#                    change from the Mac app, because it has no key to check)
#   -h, --help       show this help
#
# For tests and unusual setups only:
#   HARDWOOD_INSTALL_HOME=DIR          treat DIR as the home folder
#   HARDWOOD_INSTALL_ALLOW_NON_MAC=1   allow running somewhere that is not macOS
#
# Compatibility note: macOS ships bash 3.2, so this script avoids anything newer than that.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../.." && pwd -P)"

LABELS="com.hardwood.api com.hardwood.nba-watch com.hardwood.nba-nightly com.hardwood.worker"

HOME_DIR="${HARDWOOD_INSTALL_HOME:-$HOME}"
DATA_DIR=""
PORT="8000"
PYTHON_ARG=""
DRY_RUN="0"
NO_LOAD="0"
SKIP_PIP="0"
NO_API_KEY="0"

# ------------------------------------------------------------------------------ messages

say()  { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'Warning: %s\n' "$*" >&2; }
die()  { printf '\nHardwood install stopped.\n%s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,/^set -euo pipefail/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
}

# ------------------------------------------------------------------------------ arguments

while [ $# -gt 0 ]; do
  case "$1" in
    --data-dir)  [ $# -ge 2 ] || die "--data-dir needs a folder."; DATA_DIR="$2"; shift 2 ;;
    --port)      [ $# -ge 2 ] || die "--port needs a number."; PORT="$2"; shift 2 ;;
    --python)    [ $# -ge 2 ] || die "--python needs a path."; PYTHON_ARG="$2"; shift 2 ;;
    --dry-run)   DRY_RUN="1"; shift ;;
    --no-load)   NO_LOAD="1"; shift ;;
    --skip-pip)  SKIP_PIP="1"; shift ;;
    --no-api-key) NO_API_KEY="1"; shift ;;
    -h|--help)   usage; exit 0 ;;
    *)           die "I do not know the option '$1'. Try --help." ;;
  esac
done

case "$PORT" in
  ''|*[!0-9]*) die "--port must be a number, not '$PORT'." ;;
esac
if [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
  die "--port must be between 1024 and 65535."
fi

if [ -z "$DATA_DIR" ]; then
  DATA_DIR="$HOME_DIR/Library/Application Support/Hardwood"
fi
LOG_DIR="$HOME_DIR/Library/Logs/Hardwood"
AGENT_DIR="$HOME_DIR/Library/LaunchAgents"
VENV_DIR="$DATA_DIR/venv"
ENV_FILE="$DATA_DIR/hardwood.env"
DB_FILE="$DATA_DIR/hardwood.db"
EL_DB_FILE="$DATA_DIR/hardwood_el.db"

# ------------------------------------------------------------------------------ checks

check_platform() {
  if [ "$(uname -s)" != "Darwin" ] && [ "${HARDWOOD_INSTALL_ALLOW_NON_MAC:-0}" != "1" ]; then
    die "This installer is for macOS (it sets up launchd background programs).
On another system, run the pieces by hand; see docs/RUNBOOK.md."
  fi
}

# macOS stops background programs from reading Documents, Desktop, Downloads and iCloud Drive
# unless they have been given special permission. Hardwood's programs run in the background,
# so a copy of the project in one of those folders fails in a confusing way ("Operation not
# permitted"). Refuse early and say how to fix it.
check_folder() {
  case "$BACKEND_DIR" in
    "$HOME_DIR/Documents"/*|"$HOME_DIR/Desktop"/*|"$HOME_DIR/Downloads"/*|\
    "$HOME_DIR/Library/Mobile Documents"/*|"$HOME_DIR/Library/CloudStorage"/*)
      die "This copy of Hardwood is in a folder macOS protects:
    $BACKEND_DIR
macOS does not let background programs read Documents, Desktop, Downloads or iCloud Drive.
Move the whole Hardwood folder somewhere else in your home folder, for example:
    mv \"$(cd "$BACKEND_DIR/.." && pwd -P)\" \"$HOME_DIR/Hardwood\"
then run the installer again from there." ;;
  esac
  if [ ! -f "$BACKEND_DIR/pyproject.toml" ]; then
    die "I expected to find pyproject.toml in $BACKEND_DIR. Run this script from a complete
copy of the Hardwood project."
  fi
}

python_ok() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

# Looks for python3.13, python3.12 and python3.11 on the PATH and in the places Homebrew and
# python.org put them, and falls back to a plain python3 only if it is new enough. The
# python3 that macOS itself provides is 3.9, which is too old, and is rejected by the version
# check rather than by name.
find_python() {
  if [ -n "$PYTHON_ARG" ]; then
    if python_ok "$PYTHON_ARG"; then PYTHON="$PYTHON_ARG"; return 0; fi
    die "$PYTHON_ARG is not Python 3.11 or newer (or does not run)."
  fi
  local version candidate dir
  for version in 3.13 3.12 3.11; do
    candidate="$(command -v "python$version" 2>/dev/null || true)"
    if [ -n "$candidate" ] && python_ok "$candidate"; then PYTHON="$candidate"; return 0; fi
    for dir in /opt/homebrew/bin /usr/local/bin \
               "/opt/homebrew/opt/python@$version/bin" "/usr/local/opt/python@$version/bin" \
               "/Library/Frameworks/Python.framework/Versions/$version/bin"; do
      if [ -x "$dir/python$version" ] && python_ok "$dir/python$version"; then
        PYTHON="$dir/python$version"; return 0
      fi
    done
  done
  candidate="$(command -v python3 2>/dev/null || true)"
  if [ -n "$candidate" ] && python_ok "$candidate"; then PYTHON="$candidate"; return 0; fi
  die "Hardwood needs Python 3.11 or newer, and I could not find one.

The python3 that comes with the Mac is too old (it is version 3.9), so it cannot be used.
The easiest fix is Homebrew:

    brew install python@3.12

If you do not have Homebrew, get it from https://brew.sh first, or download Python from
https://www.python.org/downloads/macos/ . Then run this installer again."
}

port_is_busy() {
  command -v lsof >/dev/null 2>&1 || return 1
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
}

# ------------------------------------------------------------------------------ the work

make_folders() {
  mkdir -p "$DATA_DIR" "$DATA_DIR/raw" "$DATA_DIR/el-raw" "$DATA_DIR/recordings" \
           "$DATA_DIR/inbox" "$LOG_DIR" "$AGENT_DIR"
  # The databases hold your accounts and anything you typed in by hand, so keep them private.
  chmod 700 "$DATA_DIR"
}

make_venv() {
  if [ -x "$VENV_DIR/bin/python" ] && python_ok "$VENV_DIR/bin/python"; then
    say "Reusing the Python environment at $VENV_DIR"
  else
    if [ -d "$VENV_DIR" ]; then
      # Only ever remove a folder that is plainly a virtual environment.
      [ -f "$VENV_DIR/pyvenv.cfg" ] || die "$VENV_DIR exists but is not a Python environment I made.
Move it out of the way and run the installer again."
      say "The existing Python environment is too old or broken; rebuilding it."
      rm -rf "$VENV_DIR"
    fi
    say "Creating a Python environment at $VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR" || die "Python could not create its environment.
If you used Homebrew, try:  brew reinstall python@3.12"
  fi
  VENV_PY="$VENV_DIR/bin/python"
}

install_packages() {
  if [ "$SKIP_PIP" = "1" ]; then
    say "Skipping the package install (--skip-pip)."
    return 0
  fi
  say "Installing Hardwood's Python packages (the first time this takes a few minutes)..."
  "$VENV_PY" -m pip install --quiet --upgrade pip \
    || die "Could not update pip. Check that this Mac is online, then run the installer again."
  # Editable on purpose: the code stays in this folder, so updating Hardwood is a "git pull"
  # and a restart, and the contract files and web build are found beside it.
  "$VENV_PY" -m pip install --quiet -e "$BACKEND_DIR[serve,live,injuries]" \
    || die "Installing the packages failed. The messages above say why; the usual cause is
being offline. Fix that and run the installer again."
}

write_env_file() {
  if [ -f "$ENV_FILE" ]; then
    say "Keeping your existing settings file: $ENV_FILE"
  else
    cp "$SCRIPT_DIR/hardwood.env.example" "$ENV_FILE"
    if [ "$PORT" != "8000" ]; then
      {
        printf '\n# Written by install.sh because you chose a port other than 8000.\n'
        printf 'HARDWOOD_PUBLIC_BASE_URL=http://127.0.0.1:%s\n' "$PORT"
      } >> "$ENV_FILE"
    fi
    chmod 600 "$ENV_FILE"
    say "Wrote your settings file: $ENV_FILE"
  fi
  ensure_api_key
}

# The Mac app's changes (a status you type in, a link you paste, a model setting) are accepted
# only with the API key, and the server has no other way in, so there has to be one. This never
# replaces a key that is already there and never prints the key: ensure_api_key.py says only
# whether it made one. The settings file ends up readable by you alone either way.
ensure_api_key() {
  local outcome
  if [ "$NO_API_KEY" = "1" ]; then
    say "Not creating an API key (--no-api-key). The server will refuse changes from the Mac app."
    return 0
  fi
  outcome="$("$VENV_PY" "$SCRIPT_DIR/ensure_api_key.py" "$ENV_FILE")" \
    || die "Could not write an API key into $ENV_FILE.
Check that you can write to that file and its folder, then run the installer again."
  case "$outcome" in
    created) say "Created a private API key in your settings file (the Mac app reads it there)." ;;
    filled)  say "Put a private API key into the empty HARDWOOD_API_KEY line of your settings file." ;;
    kept)    say "Your settings file already has an API key; it was left alone." ;;
    *)       die "ensure_api_key.py said something unexpected: $outcome" ;;
  esac
}

# Fills in a plist template. Done in Python rather than with sed so that a path containing
# '&', a space or a slash cannot break it, and so the result is checked to be a valid plist
# with no leftover placeholder before launchd ever sees it.
render_plist() {
  local template="$1" output="$2"
  HW_PYTHON="$VENV_PY" HW_BACKEND="$BACKEND_DIR" HW_DATA="$DATA_DIR" HW_LOG="$LOG_DIR" \
  HW_ENV="$ENV_FILE" HW_PORT="$PORT" HW_DB="sqlite:///$DB_FILE" HW_ELDB="sqlite:///$EL_DB_FILE" \
  "$VENV_PY" - "$template" "$output" <<'PY'
import os
import plistlib
import re
import sys
from xml.sax.saxutils import escape

template, output = sys.argv[1], sys.argv[2]
tokens = {
    "__PYTHON__": os.environ["HW_PYTHON"],
    "__BACKEND_DIR__": os.environ["HW_BACKEND"],
    "__DATA_DIR__": os.environ["HW_DATA"],
    "__LOG_DIR__": os.environ["HW_LOG"],
    "__ENV_FILE__": os.environ["HW_ENV"],
    "__PORT__": os.environ["HW_PORT"],
    "__DB_URL__": os.environ["HW_DB"],
    "__EL_DB_URL__": os.environ["HW_ELDB"],
}
with open(template, encoding="utf-8") as handle:
    text = handle.read()
for token, value in tokens.items():
    text = text.replace(token, escape(value))
left = re.findall(r"__[A-Z][A-Z_]*__", text)
if left:
    sys.exit("unfilled placeholders in %s: %s" % (template, ", ".join(sorted(set(left)))))
plistlib.loads(text.encode("utf-8"))
tmp = output + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    handle.write(text)
os.replace(tmp, output)
PY
}

write_plists() {
  local label
  for label in $LABELS; do
    render_plist "$SCRIPT_DIR/$label.plist" "$AGENT_DIR/$label.plist" \
      || die "Could not write $AGENT_DIR/$label.plist"
    chmod 644 "$AGENT_DIR/$label.plist"
    say "Wrote $AGENT_DIR/$label.plist"
  done
}

# A small script that runs the installed Python with the same settings the background programs
# use. A command typed into Terminal does not get the launch agents' environment, so without
# this it would open the wrong database and report problems that are only a path.
write_wrapper() {
  local wrapper="$DATA_DIR/hardwood-python" tmp
  tmp="$wrapper.tmp"
  {
    printf '#!/bin/sh\n'
    printf '# Written by install.sh. Runs Python with the settings the Hardwood background programs\n'
    printf '# use, for example:   hardwood-python -m nbastats.worker --list\n'
    printf 'HARDWOOD_DATA_DIR=%s; export HARDWOOD_DATA_DIR\n' "$(printf '%q' "$DATA_DIR")"
    printf 'HARDWOOD_ENV_FILE=%s; export HARDWOOD_ENV_FILE\n' "$(printf '%q' "$ENV_FILE")"
    printf 'DATABASE_URL=%s; export DATABASE_URL\n' "$(printf '%q' "sqlite:///$DB_FILE")"
    printf 'HARDWOOD_EL_DATABASE_URL=%s; export HARDWOOD_EL_DATABASE_URL\n' \
      "$(printf '%q' "sqlite:///$EL_DB_FILE")"
    printf 'exec %s "$@"\n' "$(printf '%q' "$VENV_PY")"
  } > "$tmp"
  chmod 755 "$tmp"
  mv "$tmp" "$wrapper"
  say "Wrote $wrapper"
}

launchctl_domain() { printf 'gui/%s' "$(id -u)"; }

unload_agent() {
  local label="$1" domain tries=0
  domain="$(launchctl_domain)"
  launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
  # bootout returns before the program has finished stopping.
  while launchctl print "$domain/$label" >/dev/null 2>&1 && [ "$tries" -lt 20 ]; do
    sleep 0.5; tries=$((tries + 1))
  done
}

load_agent() {
  local label="$1" domain attempt=1
  domain="$(launchctl_domain)"
  unload_agent "$label"
  while ! launchctl bootstrap "$domain" "$AGENT_DIR/$label.plist" 2>/dev/null; do
    if [ "$attempt" -ge 4 ]; then
      die "launchd would not load $label.
This usually means you are not logged in at the Mac's own screen (for example over SSH),
because background programs belong to a logged-in user. Run this installer in Terminal on the
Mac itself. To see launchd's own message, run:
    launchctl bootstrap $domain \"$AGENT_DIR/$label.plist\""
    fi
    attempt=$((attempt + 1)); sleep 1
  done
  launchctl enable "$domain/$label" >/dev/null 2>&1 || true
  say "Started $label"
}

load_agents() {
  local label
  if port_is_busy && ! launchctl print "$(launchctl_domain)/com.hardwood.api" >/dev/null 2>&1; then
    die "Port $PORT is already in use by another program on this Mac.
Pick a different one, for example:  $0 --port 8123"
  fi
  for label in $LABELS; do
    load_agent "$label"
  done
}

wait_for_api() {
  local i=0
  while [ "$i" -lt 45 ]; do
    if curl -fsS "http://127.0.0.1:$PORT/v1/health" >/dev/null 2>&1; then return 0; fi
    sleep 1; i=$((i + 1))
  done
  return 1
}

summary() {
  cat <<EOF

Hardwood is installed.

  Server        http://127.0.0.1:$PORT   (this Mac only)
  Data          $DATA_DIR
  Settings      $ENV_FILE$( [ "$NO_API_KEY" = "1" ] || echo "   (holds your API key; readable by you only)" )
  Logs          $LOG_DIR

Your EuroLeague workbook: copy the .xlsx file into
    $DATA_DIR/inbox
and Hardwood imports it within a minute.

Check on things:
    "$DATA_DIR/hardwood-python" -m nbastats.worker --list
    launchctl list | grep hardwood
    tail -n 30 "$LOG_DIR/worker.log"

("hardwood-python" runs Python with the same settings the background programs use. Use it
for any Hardwood command you type yourself.)

Turn a data source off, or change anything else: edit the settings file. The notes inside it
say what each line does and when a change takes effect.

Stop and remove the background programs:  $SCRIPT_DIR/uninstall.sh
Everything is explained in docs/RUNBOOK.md (the section "Hardwood on your Mac").
EOF
}

# ------------------------------------------------------------------------------ run it

check_platform
check_folder
find_python

if [ "$DRY_RUN" = "1" ]; then
  cat <<EOF
Dry run: nothing will be changed. I would:
  use Python          $PYTHON
  create the folder   $DATA_DIR  (with raw, el-raw, recordings and inbox inside)
  logs                $LOG_DIR
  Python environment  $VENV_DIR
  install packages    $BACKEND_DIR[serve,live,injuries]  (skip: $SKIP_PIP)
  settings file       $ENV_FILE  (kept if it already exists)
  an API key          $( [ "$NO_API_KEY" = "1" ] && echo "no (--no-api-key)" || echo "made if the settings file has none, never replaced, never printed" )
  a shortcut          $DATA_DIR/hardwood-python  (runs Python with Hardwood's settings)
  write four agents   $(for l in $LABELS; do printf '%s ' "$l"; done)
  into                $AGENT_DIR
  start them          $( [ "$NO_LOAD" = "1" ] && echo "no (--no-load)" || echo "yes" )
  server address      http://127.0.0.1:$PORT
EOF
  exit 0
fi

step "Folders"
make_folders
step "Python environment"
make_venv
step "Packages"
install_packages
step "Settings"
write_env_file
write_wrapper
step "Background programs"
write_plists
if [ "$NO_LOAD" = "1" ]; then
  say "Not starting them (--no-load). Start later by running this installer without it."
else
  load_agents
  step "Checking the server"
  if wait_for_api; then
    say "The server answered: http://127.0.0.1:$PORT/v1/health"
  else
    warn "The server did not answer within 45 seconds. The log may say why:
    $LOG_DIR/api.log"
  fi
fi
summary
