#!/usr/bin/env bash
# Copy the canonical contracts (and golden fixtures) into the iOS app bundle.
#
# The app bundles its own copies so it can format metrics, build config UIs and run in demo
# mode with no network. scripts/check_contracts.py fails CI if these copies ever drift from
# contracts/, so run this after editing anything under contracts/.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$root/ios/NBAStats/Resources/Contracts" "$root/ios/NBAStatsTests/Fixtures"
cp "$root/contracts/metrics.json" "$root/contracts/widgets.json" "$root/contracts/presets.json" \
   "$root/ios/NBAStats/Resources/Contracts/"

if compgen -G "$root/contracts/fixtures/*.json" > /dev/null; then
  cp "$root"/contracts/fixtures/*.json "$root/ios/NBAStatsTests/Fixtures/"
  # Demo mode replays the same golden payloads the tests decode.
  mkdir -p "$root/ios/NBAStats/Resources/Fixtures"
  cp "$root"/contracts/fixtures/*.json "$root/ios/NBAStats/Resources/Fixtures/"
  echo "synced contracts + $(ls "$root"/contracts/fixtures/*.json | wc -l | tr -d ' ') fixtures"
else
  echo "synced contracts (no fixtures generated yet)"
fi
