#!/usr/bin/env bash
# Copy the canonical contracts (and golden fixtures) into the iOS app bundle.
#
# The app bundles its own copies so it can format metrics, build config UIs and run in demo
# mode with no network. scripts/check_contracts.py fails CI if these copies ever drift from
# contracts/, so run this after editing anything under contracts/.
#
# WHY presets.json IS NOT COPIED INTO THE APP'S Fixtures/
# Xcode 16 file-system synchronized groups flatten resource subdirectories into the bundle
# root, so Resources/Contracts/presets.json and Resources/Fixtures/presets.json would both
# try to produce Hardwood.app/presets.json — "Multiple commands produce ..." and the build
# stops. Nothing in the app reads the fixture copy anyway: DemoAPIClient.presets() builds its
# response from the Contracts copy via bundledDocument(). The test target keeps its own copy,
# where there is no such collision.
#
# If you add a fixture whose name matches a catalog, add it to APP_BUNDLE_EXCLUDE below.
# check_contracts.py check (g) fails on any collision, so you will hear about it before Xcode
# does.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Fixtures that must not be copied into the app bundle, because a catalog already owns that
# basename in Resources/Contracts/.
APP_BUNDLE_EXCLUDE=("presets.json")

mkdir -p "$root/ios/NBAStats/Resources/Contracts" "$root/ios/NBAStatsTests/Fixtures"
cp "$root/contracts/metrics.json" "$root/contracts/widgets.json" "$root/contracts/presets.json" \
   "$root/ios/NBAStats/Resources/Contracts/"

if compgen -G "$root/contracts/fixtures/*.json" > /dev/null; then
  app_fixtures="$root/ios/NBAStats/Resources/Fixtures"
  test_fixtures="$root/ios/NBAStatsTests/Fixtures"
  mkdir -p "$app_fixtures"

  # The test bundle takes everything; it has no catalog copies to collide with.
  cp "$root"/contracts/fixtures/*.json "$test_fixtures/"

  copied=0
  skipped=0
  for source in "$root"/contracts/fixtures/*.json; do
    name="$(basename "$source")"
    excluded=0
    for blocked in "${APP_BUNDLE_EXCLUDE[@]}"; do
      [[ "$name" == "$blocked" ]] && excluded=1 && break
    done
    if (( excluded )); then
      rm -f "$app_fixtures/$name"      # in case an older sync left one behind
      skipped=$((skipped + 1))
      continue
    fi
    cp "$source" "$app_fixtures/$name"
    copied=$((copied + 1))
  done

  echo "synced 3 catalogs, ${copied} app fixtures (${skipped} excluded), $(ls "$test_fixtures"/*.json | wc -l | tr -d ' ') test fixtures"
else
  echo "synced 3 catalogs (no fixtures generated yet)"
fi
