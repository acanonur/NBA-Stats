# Hardwood — an editable NBA advanced-stats dashboard for iOS

A SwiftUI app whose home screen you build yourself: pick from nine ready-made dashboards or
assemble your own out of twelve widget types, each configurable down to the metric. Behind it
sits a Python service that backfills eighty years of league history from bulk open datasets and
then keeps itself current **game by game** — each box score lands as soon as that game goes
final, not at the end of the night.

Built from the acquisition and architecture research in
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).

```
┌── contracts/ ── the shared truth: 61 metrics, 16 widgets, 12 presets, golden fixtures ──┐
│                                                                                         │
│   backend/  ingest → SQLite/Postgres → FastAPI   ⇄   ios/  SwiftUI dashboard + sync    │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## What it does

**A dashboard you actually own.** Every tile can be added, removed, dragged, resized and
reconfigured. The configuration sheet is generated at runtime from a schema the server ships, so
a new option on a widget shows up in the app without an App Store release. Editing one of the
bundled presets silently forks it into your own copy and keeps a "reset to original" path.

**Nine presets, ready on first launch.** *Daily Recap* (last night's slate, biggest games, the
performances that beat their own season baseline) · *Player Deep Dive* · *Advanced Scout* ·
*Efficiency Hunt* · *All-Time Greats* · *Fantasy Watch* · *Team Pulse* · *Playoff Lab* ·
*Blank Canvas*.

**Twelve widget kinds.** Stat tile · player snapshot · leaderboard · game log · trend chart ·
four factors · shot profile · comparison · scoreboard · daily movers · team efficiency ·
career arc. Charts are Swift Charts; there are no third-party dependencies anywhere in the app.

**Stats that arrive per game.** The ingest worker watches the scoreboard, and the moment a game
is final it pulls that one game's traditional and advanced box, writes it, and increments a sync
version. The app notices through `GET /v1/sync`, invalidates only the affected widget kinds, and
refreshes — from the foreground, from pull-to-refresh, or from a background refresh task. The
league revises box scores after the fact, so the last three days are re-pulled every night.

**Honest about eras — this is the part most NBA apps get wrong.** Per-game advanced box scores,
plus/minus, play-by-play and shot charts only exist from **1996-97**. Steals, blocks and the
offensive/defensive rebound split start in 1973-74; individual turnovers in 1977-78; the
three-point line in 1979-80. Hardwood never renders a zero for a stat that did not exist. A
missing value is an em dash you can tap for an explanation; a box-score-derived pre-1997
estimate carries a dashed underline and an "est." badge; the career-arc chart draws the era
boundaries as vertical rules, because that is exactly where comparing 1962 to 2026 goes wrong.

---

## Quick start

```bash
# 1. Backend with a deterministic synthetic league — no network, no NBA data needed
cd backend
pip install -e .
python3 -m nbastats.seed --db sqlite:///./hardwood.db
uvicorn nbastats.api.app:app --reload --port 8000

# 2. App
open ios/NBAStats.xcodeproj      # Xcode 16+, run on any iOS 17+ simulator
```

The app points at `http://localhost:8000/v1` by default. **With no server running at all it
still works** — it falls back to golden fixtures bundled in the app, so every screen, every
widget and every preset is browsable offline.

Real data, licensing, the residential-IP requirement and the nightly cron line are all in
[`docs/RUNBOOK.md`](docs/RUNBOOK.md).

---

## Repository map

| Path | What lives there |
| --- | --- |
| `contracts/` | **Start here.** `CONTRACT.md` is the API and payload spec; `metrics.json`, `widgets.json` and `presets.json` are consumed by *both* sides; `fixtures/` holds golden payloads both sides test against |
| `backend/` | Ingest pipeline, advanced-stat formulas, schema, FastAPI service, tests |
| `ios/` | The SwiftUI app. `ios/ARCHITECTURE.md` is its binding Swift type surface |
| `docs/` | [Architecture](docs/ARCHITECTURE.md) · [Data sources](docs/DATA_SOURCES.md) · [Projection](docs/PROJECTION.md) · [Broadsheet](docs/BROADSHEET.md) · [Fantasy](docs/FANTASY.md) · [Legal](docs/LEGAL.md) · [Runbook](docs/RUNBOOK.md) |
| `sql/` | The original BigQuery-flavoured queries this project grew out of |
| `scripts/` | `check_contracts.py` (CI drift guard), `sync_contracts.sh` |

### Why `contracts/` is a top-level directory

Two codebases in two languages have to agree on 61 metric definitions, 16 payload shapes and 12
preset dashboards. Prose guarantees drift, so the agreement is data: the backend formats values
from `metrics.json`, the app formats values from the same file, the app *generates its
configuration UI* from `widgets.json`, and `scripts/check_contracts.py` fails CI the moment the
two copies diverge — including the copies bundled into the app.

---

## Where the data comes from

A hybrid, exactly as the research recommends: **bulk files for history, a small `nba_api` job
for today.** Pulling 64,000 games one at a time from stats.nba.com would take days at safe rate
limits, so the backfill reads Wyatt Walsh's Kaggle SQLite (games and box scores from 1946),
Sumitro Datta's Basketball-Reference season dumps (the only practical source for pre-1997
advanced seasons), and shufinskiy's play-by-play files. Only the nightly delta touches the API.

Two traps worth knowing before you deploy:

1. **stats.nba.com silently blocks datacenter IPs** — AWS, GCP, Azure, and usually GitHub
   Actions runners. Requests hang rather than failing. The ingest worker needs a residential
   connection or a residential proxy; only the database and API belong in a cloud region.
2. **Basketball-Reference forbids building tools on scraped data.** There is no BBRef scraper in
   this repository and there must not be one. The PER / Win Shares / BPM / VORP implementations
   are written from the published formulas, with Basketball-Reference credited as their origin.

Stats via NBA.com. Not endorsed by or affiliated with the NBA. **This is a private,
non-commercial project** — which is what NBA.com's terms allow. Shipping or monetizing it means
licensing a feed first; [`docs/LEGAL.md`](docs/LEGAL.md) spells out exactly what changes.

---

## Testing

```bash
cd backend && python3 -m pytest -q     # metrics, schema, ingest, API, all 12 widget resolvers
python3 scripts/check_contracts.py     # catalogs, presets, registry and bundled copies agree

cd ios && xcodebuild test -scheme Hardwood \
    -destination 'platform=iOS Simulator,name=iPhone 16'
```

The backend suite includes hand-computed worked examples for every advanced formula, a
reconciliation check that seeded box scores sum to their game's final score, ingest tests that
run entirely from recorded fixtures, and an integration test that resolves **every widget of
every shipped preset**.

### One honesty note

This repository was developed on Linux, where **no Swift toolchain exists**. The backend is
verified by actually running its 416 tests. The iOS app is not — it has never been through a
Swift compiler here.

What it did get instead: a locked type surface (`ios/ARCHITECTURE.md`) that every file was
written against, a mechanical sweep checking argument labels against all 241 initializers plus
switch exhaustiveness over nine enums, and then an adversarial review pass by independent
reviewers told to treat that clean sweep as a hypothesis to falsify. They found **no compile
errors** and 43 semantic defects, 33 of which are fixed — including a path that destroyed the
user's saved layouts on any read failure, and a comparison chart that could rank players
backwards.

That is real evidence, but it is not a compiler. `.github/workflows/ios.yml` builds and tests
the app on a macOS runner, which is the first place the compiler genuinely sees it. Expect to
fix a few things on the first real build. The known risks are listed in `ios/README.md`; the
largest is that `@MainActor` inference from SwiftUI's protocol conformances requires the
iOS 18 SDK, so Xcode 16+ is a hard requirement rather than a preference.
