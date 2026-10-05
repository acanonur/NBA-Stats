#!/usr/bin/env bash
# Build Hardwood.app for macOS and wrap it in a disk image you can double-click.
#
#   scripts/make_dmg.sh            build, then write dist/Hardwood.dmg
#   scripts/make_dmg.sh --open     the same, then open the disk image in Finder
#
# WHAT THIS MAKES, AND WHAT IT DOES NOT
# A Release build of the native Mac app (Apple silicon and Intel in one binary), signed "to run
# locally" -- an ad-hoc signature, no Apple developer account -- inside a compressed disk image
# that holds the app, a shortcut to Applications to drag it onto, and a short read-me. That is
# the whole of the app. It is not the whole of Hardwood: the app reads every number from the
# Hardwood server, which runs in the background on this Mac and is installed once, from the
# project folder, with backend/scripts/macos/install.sh. The read-me says so.
#
# WHY AD-HOC SIGNING IS ENOUGH HERE, AND WHERE IT IS NOT
# A disk image built on this Mac carries no quarantine flag, so the app opens with a double-click.
# A copy downloaded from anywhere -- including the one GitHub Actions builds -- is quarantined,
# and macOS will say it cannot verify the developer the first time it is opened; the read-me gives
# the two ways past that. Removing that step for other people's Macs needs a paid Developer ID and
# notarization, which this project does not have and which would only matter if the app were
# being shared, which docs/LEGAL.md asks you not to do without the licensing conversation first.
#
# Needs only what Xcode installs (xcodebuild, codesign) and what macOS ships (hdiutil).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PROJECT="$ROOT/ios/NBAStats.xcodeproj"
BUILD_DIR="$ROOT/build/mac"
OUT_DIR="$ROOT/dist"
DMG="$OUT_DIR/Hardwood.dmg"
LOG="$BUILD_DIR/xcodebuild.log"
OPEN_WHEN_DONE=0

die() { printf 'make_dmg.sh: %s\n' "$*" >&2; exit 1; }
say() { printf 'make_dmg.sh: %s\n' "$*"; }

for arg in "$@"; do
  case "$arg" in
    --open) OPEN_WHEN_DONE=1 ;;
    -h|--help) sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option '$arg' (try --help)" ;;
  esac
done

[ "$(uname -s)" = "Darwin" ] || die "this builds a Mac app, so it has to run on a Mac."
command -v xcodebuild >/dev/null 2>&1 || die "Xcode is not installed. Install it from the App Store, open it once, then run this again."
if ! xcodebuild -version >/dev/null 2>&1; then
  die "xcodebuild is pointing at the Command Line Tools, not Xcode. Run:
    sudo xcode-select -s /Applications/Xcode.app
then run this again."
fi
[ -d "$PROJECT" ] || die "cannot find $PROJECT. Run this from inside the Hardwood project folder."

mkdir -p "$BUILD_DIR" "$OUT_DIR"
say "building Hardwood for macOS ($(xcodebuild -version | head -1)) -- the first build takes a few minutes"

# CODE_SIGN_STYLE=Manual with the identity "-" is Xcode's "Sign to Run Locally": an ad-hoc
# signature over the whole bundle, with no team to choose. Apple silicon refuses to run an
# unsigned binary at all, and a bundle with no seal is reported as "damaged" when downloaded,
# so building unsigned and hoping is not an option.
set +e
xcodebuild build \
  -project "$PROJECT" \
  -scheme Hardwood \
  -configuration Release \
  -destination 'generic/platform=macOS' \
  -derivedDataPath "$BUILD_DIR" \
  -quiet \
  CODE_SIGN_STYLE=Manual \
  CODE_SIGN_IDENTITY=- \
  DEVELOPMENT_TEAM= \
  >"$LOG" 2>&1
status=$?
set -e

if [ "$status" -ne 0 ]; then
  say "the build failed. The errors, once each:"
  grep -E '(error|fatal error): ' "$LOG" | sed -E 's#^.*/ios/#ios/#' | sort -u | head -100 >&2 || true
  die "full log: $LOG -- paste the lines above (or the log) back to get them fixed."
fi

APP="$BUILD_DIR/Build/Products/Release/Hardwood.app"
[ -d "$APP" ] || die "the build said it succeeded but $APP is not there."
codesign --verify --deep --strict "$APP" || die "the app's signature does not verify; it would not open."

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/hardwood-dmg.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cat >"$STAGE/Read Me First.txt" <<'README'
Hardwood for Mac
================

1. Drag Hardwood onto the Applications folder next to it.

2. Hardwood reads every number from the Hardwood server, which runs in the
   background on this Mac. Install it once, from the project folder, in Terminal:

       cd ~/NBA-Stats
       backend/scripts/macos/install.sh

   Check it is running:   curl -s http://127.0.0.1:8000/v1/health
   If it is not, the app opens with a banner that says so and offers the restart
   command.

3. Open Hardwood from Applications.

   If this disk image was downloaded (for example from GitHub) rather than built
   on this Mac, macOS will say it cannot verify the developer the first time.
   Either:
     - click Done, then open System Settings > Privacy & Security, scroll down to
       "Hardwood was blocked", and click Open Anyway; or
     - in Terminal:   xattr -dr com.apple.quarantine /Applications/Hardwood.app

   The app is signed to run locally, not by a registered Apple developer.

More: docs/MAC.md in the project folder.
README

rm -f "$DMG"
hdiutil create -volname "Hardwood" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
hdiutil verify "$DMG" >/dev/null 2>&1 || die "hdiutil could not verify $DMG."

say "done: $DMG ($(du -h "$DMG" | cut -f1 | tr -d ' '))"
say "double-click it, drag Hardwood onto Applications, and read 'Read Me First' if the server is not installed yet."
if [ "$OPEN_WHEN_DONE" -eq 1 ]; then open "$DMG"; fi
