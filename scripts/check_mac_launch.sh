#!/usr/bin/env bash
# Open Hardwood.app the way a person would, on every screen, and fail if it does not stay open.
#
#   scripts/check_mac_launch.sh <Hardwood.dmg | Hardwood.app> <label> [seconds per screen]
#
# WHY THIS EXISTS
# The unit tests run a Debug build as their host and never draw most screens, so a green test run
# said nothing about whether the Release app in Hardwood.dmg opens. The first disk image built
# cleanly, passed every test, and quit unexpectedly on a real Mac. This opens the very app the disk
# image carries, once per sidebar screen in each league, and keeps each one open long enough for
# its first load to land.
#
# WHAT IT LEAVES ALONE
# The app is told which screen to open through its own preferences (`hardwood.mac.selection` and
# `hardwood.mac.league`, the keys MacAppModel reads), never by scripting the interface. Whatever
# else is in those preferences when it starts -- demo mode on, a key, nothing at all -- is the
# caller's scenario and is kept, apart from the two keys above. It is meant for a throwaway machine
# (a CI runner): it rewrites the app's preferences as it goes.
#
# WHEN IT FAILS
# It prints what the app wrote to stdout and stderr (a Swift fatal error says its reason there),
# then the crash report macOS writes to ~/Library/Logs/DiagnosticReports: the exception, the
# termination reason, any "application specific" message, and the crashed thread's frames.
set -uo pipefail

TARGET="${1:?usage: check_mac_launch.sh <Hardwood.dmg | Hardwood.app> <label> [seconds]}"
LABEL="${2:?give the scenario a label}"
PER_SCREEN="${3:-12}"
DOMAIN="com.hardwood.nbastats"
REPORTS="$HOME/Library/Logs/DiagnosticReports"
SCREENS="startHere round matchup defence injuries scorers seasonStats playerSearch games review teamView ratings method sources"
LEAGUES="euroleague nba"

say() { printf 'check_mac_launch.sh [%s]: %s\n' "$LABEL" "$*"; }
die() { say "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "this opens a Mac app, so it has to run on a Mac."

WORK="$(mktemp -d "${TMPDIR:-/tmp}/hardwood-launch.XXXXXX")"
MOUNT=""
cleanup() {
  if [ -n "$MOUNT" ]; then hdiutil detach "$MOUNT" -quiet >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

case "$TARGET" in
  *.dmg)
    MOUNT="$WORK/mount"
    mkdir -p "$MOUNT"
    hdiutil attach "$TARGET" -nobrowse -readonly -noautoopen -mountpoint "$MOUNT" -quiet \
      || die "could not open the disk image $TARGET."
    [ -d "$MOUNT/Hardwood.app" ] || die "the disk image has no Hardwood.app at its top level."
    cp -R "$MOUNT/Hardwood.app" "$WORK/" || die "could not copy Hardwood.app out of the disk image."
    hdiutil detach "$MOUNT" -quiet >/dev/null 2>&1 || true
    MOUNT=""
    APP="$WORK/Hardwood.app"
    ;;
  *.app) APP="$TARGET" ;;
  *) die "expected a .dmg or a .app, got $TARGET" ;;
esac

BIN="$APP/Contents/MacOS/Hardwood"
[ -x "$BIN" ] || die "no executable at $BIN"

# Prints the newest Hardwood crash report written after the marker file, waiting for ReportCrash,
# which writes it a few seconds after the process is gone.
print_crash_report() {
  local marker="$1" report="" tries
  for tries in $(seq 1 30); do
    report="$(find "$REPORTS" -maxdepth 1 -name 'Hardwood*' -newer "$marker" -print 2>/dev/null | sort | tail -1)"
    [ -n "$report" ] && break
    sleep 1
  done
  if [ -z "$report" ]; then
    say "macOS wrote no crash report within 30 seconds."
    return
  fi
  say "crash report: $report"
  python3 - "$report" <<'PY'
import json, sys

path = sys.argv[1]
text = open(path, encoding="utf-8", errors="replace").read()
# An .ips file is a one-line JSON header followed by the JSON report. Older .crash files are text.
header, _, body = text.partition("\n")
try:
    report = json.loads(body)
except ValueError:
    print(text[:12000])
    sys.exit(0)

def show(name, value):
    if value:
        print(f"{name}: {json.dumps(value, indent=2) if not isinstance(value, str) else value}")

print("os:", report.get("osVersion", {}).get("train"), report.get("osVersion", {}).get("build"))
show("exception", report.get("exception"))
show("termination", report.get("termination"))
show("application specific", report.get("asi"))
show("ktriageinfo", report.get("ktriageinfo"))

images = report.get("usedImages", [])
def frame_line(frame):
    image = images[frame["imageIndex"]].get("name", "?") if 0 <= frame.get("imageIndex", -1) < len(images) else "?"
    symbol = frame.get("symbol", "?")
    where = ""
    if frame.get("sourceFile"):
        where = f"  ({frame['sourceFile']}:{frame.get('sourceLine', '?')})"
    return f"  {image:<28} {symbol} + {frame.get('symbolLocation', 0)}{where}"

if report.get("lastExceptionBacktrace"):
    print("last exception backtrace:")
    for frame in report["lastExceptionBacktrace"][:40]:
        print(frame_line(frame))

threads = report.get("threads", [])
faulting = report.get("faultingThread")
for index, thread in enumerate(threads):
    if index == faulting or thread.get("triggered"):
        print(f"crashed thread {index} ({thread.get('queue') or thread.get('name') or ''}):")
        for frame in thread.get("frames", [])[:60]:
            print(frame_line(frame))
PY
}

# Opens the app on one screen and keeps it open for PER_SCREEN seconds. Fails if it quits first.
#
# By default the app is opened through Launch Services (`open`), as a double-click opens it: the app
# is brought to the front, so the code that runs on becoming active runs too. Started as a plain
# child process the app may never become active, and a first version of this check that started it
# that way passed 140 launches while the same disk image quit on a real Mac. LAUNCH_WITH=exec starts
# it as a child instead (its exit status is then known).
open_on() {
  local league="$1" screen="$2" log marker pid="" status="unknown" alive=1 second=0 waited
  defaults write "$DOMAIN" hardwood.mac.league -string "$league"
  defaults write "$DOMAIN" hardwood.mac.selection -string "screen:$screen"
  log="$WORK/$league-$screen.log"
  marker="$WORK/$league-$screen.marker"
  touch "$marker"
  if [ "${LAUNCH_WITH:-open}" = "exec" ]; then
    "$BIN" >"$log" 2>&1 &
    pid=$!
  else
    : >"$log"
    if ! open -n --stdout "$log" --stderr "$log" "$APP"; then
      say "FAILED: Launch Services refused to open $APP."
      return 1
    fi
    for waited in $(seq 1 20); do
      pid="$(pgrep -n -f "$BIN" || true)"
      [ -n "$pid" ] && break
      sleep 0.5
    done
    if [ -z "$pid" ]; then
      alive=0
    fi
  fi
  if [ "$alive" -eq 1 ]; then
    for second in $(seq 1 "$PER_SCREEN"); do
      sleep 1
      if ! kill -0 "$pid" 2>/dev/null; then alive=0; break; fi
    done
  fi
  if [ "$alive" -eq 1 ]; then
    kill "$pid" 2>/dev/null
    for waited in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    kill -9 "$pid" 2>/dev/null
    if [ "${LAUNCH_WITH:-open}" = "exec" ]; then wait "$pid" 2>/dev/null; fi
    return 0
  fi
  if [ "${LAUNCH_WITH:-open}" = "exec" ]; then
    wait "$pid" 2>/dev/null
    status=$?
  fi
  say "FAILED: Hardwood quit after ${second}s on $league / $screen (exit status $status)."
  say "what it printed:"
  tail -n 80 "$log" | sed 's/^/  | /'
  print_crash_report "$marker"
  return 1
}

say "opening $APP ($(defaults read "$APP/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo '?')) on $(sw_vers -productVersion)"
failures=0
for league in $LEAGUES; do
  for screen in $SCREENS; do
    if open_on "$league" "$screen"; then
      say "ok: $league / $screen stayed open ${PER_SCREEN}s"
    else
      failures=$((failures + 1))
      # One crash report says what is wrong; the rest of the screens would only repeat it, slowly.
      [ "$failures" -ge 2 ] && break 2
    fi
  done
done

if [ "$failures" -gt 0 ]; then
  die "Hardwood did not stay open ($failures failure(s))."
fi
say "Hardwood stayed open on every screen in both leagues."
