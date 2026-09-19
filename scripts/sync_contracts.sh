#!/usr/bin/env bash
# Copy the canonical contracts (and golden fixtures) into the iOS app bundle, and regenerate
# everything the web app reads from those same contracts.
#
# The app bundles its own copies so it can format metrics, build config UIs and run in demo
# mode with no network. scripts/check_contracts.py fails CI if these copies ever drift from
# contracts/, so run this after editing anything under contracts/ OR under
# ios/NBAStats/DesignSystem/*.swift (the web's design tokens are scraped from there — see
# contracts/tools/gen_theme.py).
#
# WHY presets.json IS NOT COPIED INTO THE APP'S Fixtures/
# Xcode 16 file-system synchronized groups flatten resource subdirectories into the bundle
# root, so Resources/Contracts/presets.json and Resources/Fixtures/presets.json would both
# try to produce Hardwood.app/presets.json — "Multiple commands produce ..." and the build
# stops. Nothing in the app reads the fixture copy anyway: DemoAPIClient.presets() builds its
# response from the Contracts copy via bundledDocument(). The test target keeps its own copy,
# where there is no such collision.
#
# WHY THE THREE PARITY CASE FILES ARE EXCLUDED THE SAME WAY
# contracts/tools/gen_parity_cases.py (run below) writes layout_migration_cases.json,
# monogram_cases.json and format_cases.json straight into contracts/fixtures/, so the loop
# further down picks them up like any other fixture. They are genuinely fixtures the Swift
# parity tests need (ios/NBAStatsTests/Fixtures/), not catalogs, so — unlike presets.json —
# there is no *content* collision; they are excluded from the shipping app bundle simply
# because a production build has no test to run them against and no reason to carry the bytes.
#
# If you add a fixture whose name matches a catalog, add it to APP_BUNDLE_EXCLUDE below.
# check_contracts.py check (g) fails on any collision, so you will hear about it before Xcode
# does.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Fixtures that must not be copied into the app bundle: presets.json because a catalog already
# owns that basename in Resources/Contracts/ (see above), the three parity case files because a
# shipping build has nothing to run them against.
APP_BUNDLE_EXCLUDE=("presets.json" "layout_migration_cases.json" "monogram_cases.json" "format_cases.json")

mkdir -p "$root/ios/NBAStats/Resources/Contracts" "$root/ios/NBAStatsTests/Fixtures"
cp "$root/contracts/metrics.json" "$root/contracts/widgets.json" "$root/contracts/presets.json" \
   "$root/ios/NBAStats/Resources/Contracts/"

# The web's design tokens, typed contracts and cross-language parity fixtures — all generated
# FROM the catalogs and Swift design system just synced above, never hand-edited (WEB_DESIGN.md
# §8). Order matters: gen_web_tokens.py reads contracts/theme.json, so gen_theme.py runs first;
# gen_parity_cases.py reads contracts/widgets.json (already current) and contracts/fixtures/
# teams.json, not theme.json, so its position relative to the other two does not matter, but it
# runs last here simply to keep the fixture-copy loop below acting on a fully up to date
# contracts/fixtures/.
python3 "$root/contracts/tools/gen_theme.py" > "$root/contracts/theme.json"
python3 "$root/contracts/tools/gen_web_tokens.py"
python3 "$root/contracts/tools/gen_web_contracts.py"
python3 "$root/contracts/tools/gen_parity_cases.py"

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

  echo "synced 3 catalogs, ${copied} app fixtures (${skipped} excluded), $(ls "$test_fixtures"/*.json | wc -l | tr -d ' ') test fixtures, web tokens/contracts/registry regenerated"
else
  echo "synced 3 catalogs (no fixtures generated yet), web tokens/contracts/registry regenerated"
fi
