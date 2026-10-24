# Hardwood — an editable NBA advanced-stats dashboard for iOS, the Mac and the browser

A SwiftUI app whose home screen you build yourself: pick from nine ready-made dashboards or
assemble your own out of twelve widget types, each configurable down to the metric. Behind it
sits a Python service that backfills eighty years of league history from bulk open datasets and
then keeps itself current **game by game** — each box score lands as soon as that game goes
final, not at the end of the night.

Built from the acquisition and architecture research in
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).

```
┌── contracts/ ── the shared truth: 62 metrics, 20 widgets, 12 presets, golden fixtures ──┐
│                                                                                         │
│                                                  ⇄   ios/  SwiftUI dashboard + sync     │
│   backend/  ingest → SQLite/Postgres → FastAPI                                          │
│                                                  ──►  web/  the same dashboards, served │
│                                                             from the same process at /  │
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

The backend needs Python 3.11 or newer. On a Mac that is not the `python3` Apple ships, which is
3.9: install a newer one (for example `brew install python@3.12`) and name it in the second line
below. From the repository root, this makes a virtual environment in `backend/.venv` (the same one
`web.sh setup` makes), installs the backend into it, and serves a deterministic synthetic league —
no network, no NBA data needed — on port 8000:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[test,serve,web]'
python3 -m nbastats.seed --db sqlite:///./hardwood.db
uvicorn nbastats.api.app:app --reload --port 8000
```

The server keeps that window busy until you stop it with Control-C. For the iOS app, go to the
repository root in a second Terminal window and open the project; it needs Xcode 16 or newer and
runs on any iOS 17+ simulator:

```bash
open ios/NBAStats.xcodeproj
```

A fresh iOS install starts in demo mode on golden fixtures bundled in the app, so **with no
server running at all it still works** — every screen, every widget and every preset is
browsable offline. To see the server you just started instead, turn **Use bundled demo data** off
in **Settings › Data source**; the app then reads `http://localhost:8000/v1`. The Mac app starts
on live data rather than demo data, and is meant for an installed, always-on server: see
[Hardwood for Mac](#hardwood-for-mac).

Real data, licensing, the residential-IP requirement and the nightly cron line are all in
[`docs/RUNBOOK.md`](docs/RUNBOOK.md).

---

## Hardwood for Mac

The same Xcode target builds a native Mac app — a sidebar, tables, an inspector, menus, a
Settings window — for the NBA and the EuroLeague: matchups, points allowed by opponent position,
injuries and news with their sources. It reads everything from a Hardwood server installed on the
same Mac, which runs in the background under launchd and answers at `http://127.0.0.1:8000/v1`.
[`docs/MAC.md`](docs/MAC.md) is its manual: the right code, building, first launch, what each
screen shows, and what to send back when something goes wrong.

**You need commit `fd30608` or later.** GitHub's default branch did not have it when this was
written, so a plain clone or `git pull` of that branch can give you older code that does not
compile for macOS. In Terminal, inside the repository, this prints the OK line when you have it:

```bash
git merge-base --is-ancestor fd3060889e54442eedbae04c59da4e9b30da4ecf HEAD && echo "OK: you have the latest Mac app code"
```

If it prints nothing, or "Not a valid commit name", quit Xcode and take the Mac work from its own
branch, where it lives until it is merged into the default branch. Xcode rewrites the tracked
`project.pbxproj` just by opening the project, so the first line below puts those edits aside
with a copy (`git stash list` shows it; "No local changes to save" means there was nothing).
Do not `git stash pop` it onto the new branch, which leaves conflict markers in the project file,
and do not use `git checkout -- <file>`, `git reset --hard` or `git clean`, which throw edits
away for good:

```bash
git stash push -m "before update"
git fetch origin
git switch claude/modest-hypatia-024y5m
git merge --ff-only origin/claude/modest-hypatia-024y5m
```

Then, from the repository root, install the server and open the project. The installer needs
Python 3.11 or newer and a free port 8000, so stop a Quick start server with Control-C first; it
also refuses to run from a folder in Documents, Desktop, Downloads or iCloud Drive
([`docs/RUNBOOK.md`](docs/RUNBOOK.md) §1c says why):

```bash
backend/scripts/macos/install.sh
open ios/NBAStats.xcodeproj
```

In Xcode, choose **My Mac** as the run destination and press Run (⌘R). No development team or
Apple ID is needed, because Mac builds are signed to run locally; if ⌘R still stops asking for a
team, [`docs/MAC.md`](docs/MAC.md) §3 says what to set. If Xcode offers **Update to recommended
settings**, leave it: **Perform Changes** rewrites the tracked project file. Run `install.sh`
again, without `--skip-pip`, after every update: launchd keeps the old server code running until
you do. A fresh install has no past NBA games yet; RUNBOOK §1c, *What you get on day one*, says
how to load the last week. To keep a copy of the app outside Xcode, `scripts/make_dmg.sh --open`
builds `dist/Hardwood.dmg` ([`docs/MAC.md`](docs/MAC.md) §2).

---

## Hardwood Web

The same dashboards in a browser, served by the same FastAPI process — SPA at `/`, API
unchanged at `/v1`, one origin, no second server to deploy. From the repository root, `setup`
makes a virtualenv, pip-installs the backend, runs `npm ci` and builds the web app; `dev` then
serves it at http://127.0.0.1:8000 until you stop it with Control-C:

```bash
./scripts/web.sh setup
./scripts/web.sh dev
```

To let yourself in, open a second Terminal window at the repository root; the last line prints
an invite code:

```bash
cd backend
.venv/bin/python3 -m nbastats.accounts.admin invite --note "me"
```

Accounts are email-and-password out of the box, with Google and Apple sign-in when you
configure them — the buttons simply do not render until you do. Sessions are opaque ids in the
database, not tokens in the browser; CSRF is a synchroniser token checked against the session
row. Sign-up defaults to `invite`, and the server refuses to start in several configurations
that would quietly be unsafe.

[`docs/WEB.md`](docs/WEB.md) is the whole story: configuration, Google in fifteen minutes,
what Apple costs, the threat model, backups, and the four first-hour failures that all look
identical from the browser. [`web/README.md`](web/README.md) is for working in the SPA itself.

**Accounts change the legal position**, and not as a formality: they replace a property of the
codebase with a property of your deployment, and they mean the project now holds personal data
belonging to whoever signs in. [`docs/LEGAL.md`](docs/LEGAL.md) §2b and §2c.

---

## Repository map

| Path | What lives there |
| --- | --- |
| `contracts/` | **Start here.** `CONTRACT.md` is the API and payload spec; `metrics.json`, `widgets.json` and `presets.json` are consumed by *both* sides; `fixtures/` holds golden payloads both sides test against |
| `backend/` | Ingest pipeline, advanced-stat formulas, schema, FastAPI service, tests; `scripts/macos/install.sh` installs the server on a Mac |
| `ios/` | The SwiftUI app, one target for iOS and the Mac. `ios/ARCHITECTURE.md` is its binding Swift type surface |
| `web/` | The React SPA the backend serves at `/`. See [`web/README.md`](web/README.md) |
| `docs/` | [Mac](docs/MAC.md) · [Architecture](docs/ARCHITECTURE.md) · [Web](docs/WEB.md) · [Data sources](docs/DATA_SOURCES.md) · [EuroLeague](docs/EUROLEAGUE.md) · [Projection](docs/PROJECTION.md) · [Broadsheet](docs/BROADSHEET.md) · [Fantasy](docs/FANTASY.md) · [Legal](docs/LEGAL.md) · [Runbook](docs/RUNBOOK.md) |
| `sql/` | The original BigQuery-flavoured queries this project grew out of |
| `scripts/` | `check_contracts.py` (CI drift guard), `sync_contracts.sh`, `web.sh` (Hardwood Web), `make_dmg.sh` (the Mac disk image), `check_swift_portability.py` (a local Swift lint, not a compiler), `check_mac_launch.sh` and `ci_mac_server.sh` (CI's Mac launch check) |

### Why `contracts/` is a top-level directory

Two codebases in two languages have to agree on 62 metric definitions, 20 payload shapes and 12
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

Stats via NBA.com. Not endorsed by or affiliated with the NBA. **In its default configuration
this is a private, non-commercial project** — which is what NBA.com's terms allow. The thing
that ends that is not a store submission; it is **leaving the private configuration**, which
now means a web deployment people you did not invite can reach or sign up for. Monetizing it in
any form crosses the same line for a different reason. Either way you license a feed first;
[`docs/LEGAL.md`](docs/LEGAL.md) §2b draws the line, and §2c covers the personal data the
account system collects from your own users.

---

## Testing

Each block starts and ends at the repository root. The backend suite (more than 3,800 tests:
metrics, schema, ingest, API, accounts, every widget resolver), then the contract checker (13
checks: catalogs, presets, registry, bundled and generated copies agree). The suite needs the
`test` and `web` extras Quick start installs, so it runs in that virtual environment (the second
line switches to it in a new Terminal window); without `web`, the sign-in tests cannot import
PyJWT and pytest stops before running anything:

```bash
cd backend
source .venv/bin/activate
python3 -m pytest -q
cd ..
python3 scripts/check_contracts.py
```

The web suite (526 tests: design system, widgets, dashboard editor, auth flows), then the build,
after which CI fails on any diff in the committed `web/dist`:

```bash
cd web
npm test
npm run build
cd ..
```

The app's unit tests on an iPhone simulator, then on the Mac. If your Xcode has no iPhone 16
simulator, use the name of one that `xcrun simctl list devices available` lists:

```bash
cd ios
xcodebuild test -scheme Hardwood -destination 'platform=iOS Simulator,name=iPhone 16'
xcodebuild test -scheme Hardwood -destination 'platform=macOS'
cd ..
```

The backend suite includes hand-computed worked examples for every advanced formula, a
reconciliation check that seeded box scores sum to their game's final score, ingest tests that
run entirely from recorded fixtures, and an integration test that resolves **every widget of
every shipped preset**.

### One honesty note

This repository was developed on Linux, where **no Swift toolchain exists**. The backend is
verified by actually running its tests. The Swift app was written without a compiler.

What it got instead: a locked type surface (`ios/ARCHITECTURE.md`) that every file was
written against, a mechanical sweep checking argument labels against all 241 initializers plus
switch exhaustiveness over nine enums, and then an adversarial review pass by independent
reviewers told to treat that clean sweep as a hypothesis to falsify. They found **no compile
errors** and 43 semantic defects, 33 of which are fixed — including a path that destroyed the
user's saved layouts on any read failure, and a comparison chart that could rank players
backwards.

That was real evidence, but it was not a compiler, and the first real builds still found what
the review could not: bundle resources and property-list settings that stopped the app building
or installing, one expression the macOS compiler gave up on, and, the first time the unit tests
ran in CI, seven failures, one of them a real bug.

A compiler now sees every change. On every push that touches the app, GitHub's macOS runners
build it with the newest Xcode they have. `.github/workflows/ios.yml` builds the iPhone-simulator
destination and runs the unit tests there. `.github/workflows/mac.yml` builds the Mac app into
`Hardwood.dmg` with `scripts/make_dmg.sh` (kept as the run's **Hardwood-mac** download for 30
days), runs the unit tests on macOS, and then opens the app from that disk image on every sidebar
screen in both leagues, the dashboards apart, on macOS 15 and macOS 26: with no server, in demo
mode, against a seeded server holding the installer's API key, and, on macOS 26, against live
EuroLeague data with the Mac set to a European region.

What CI cannot check is anything that needs eyes: how the screens look, and whether the toolbar,
the inspector and the menus behave. Nor has it reproduced the crash a real Mac hit with the first
downloaded disk image, whose cause is still unknown. Those are listed under *Known risks* in
`ios/README.md`, with the reason Xcode 16+ is a hard requirement rather than a preference:
`@MainActor` inference from SwiftUI's protocol conformances needs the iOS 18 / macOS 15 SDK.
