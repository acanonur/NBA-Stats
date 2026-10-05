# Runbook — running Hardwood for real

Everything below assumes a checkout of this repository and Python 3.11+.

---

## 1. Five-minute local start (no network, no NBA data)

```bash
cd backend
pip install -e .                      # or: pip install fastapi 'pydantic>=2' 'sqlalchemy>=2' uvicorn httpx
python3 -m nbastats.seed --db sqlite:///./hardwood.db     # deterministic synthetic league
HARDWOOD_DEMO_MODE=1 uvicorn nbastats.api.app:app --reload --port 8000
```

```bash
curl -s localhost:8000/v1/health | python3 -m json.tool
curl -s localhost:8000/v1/meta   | python3 -m json.tool | head -40
curl -s -X POST localhost:8000/v1/dashboard/resolve \
     -H 'content-type: application/json' \
     -d '{"widgets":[{"id":"w1","kind":"scoreboard","size":"large","config":{"date":"latest"}}]}' \
   | python3 -m json.tool
```

Then open `ios/NBAStats.xcodeproj` in Xcode 16+ and run it. On an iPhone simulator the app points
at `http://localhost:8000/v1` by default (`HardwoodAPIBaseURL` in `ios/Info.plist`, overridable in
Settings) and starts in demo mode, so with no server running it falls back to the bundled fixtures
and is still fully browsable.

**On a Mac, choose the *My Mac* destination instead.** The Mac app is the main client: it starts on
live data at `http://127.0.0.1:8000/v1` (`ios/Info-macOS.plist`) and covers both leagues. This
five-minute server is enough to *read* with it; for the installed, always-on server and the API key
the app needs in order to *change* anything, use §1c. [MAC.md](MAC.md) is the Mac app's manual.

---

## 1b. The web app

```bash
./scripts/web.sh setup      # venv, pip install -e "backend[serve,web]", npm ci, npm run build
./scripts/web.sh dev        # http://127.0.0.1:8000 — SPA at /, API unchanged at /v1
./scripts/web.sh doctor     # what is configured, what is not, and why each provider is off
cd backend && .venv/bin/python3 -m nbastats.accounts.admin invite --note "me"
```

One process serves both; there is no separate frontend to deploy, and `web/dist` is committed
so a fresh checkout has a working bundle. Sign-up defaults to `invite`, which is why the last
line is not optional.

**[WEB.md](WEB.md) is the document for this**: every environment variable, Google in about
fifteen minutes, what Apple costs and requires, sessions and CSRF, the threat model, backups,
and the four first-hour failures that all present as "the page doesn't work". One thing from it
is worth repeating here because it will cost you an afternoon otherwise: **the server does not
read `backend/.env` by itself** — `web.sh doctor` does, which is what makes the mismatch so
confusing. Export the values (`set -a; . backend/.env; set +a`) before starting it.

---

## 1c. Hardwood on your Mac — installed, always on

This is the section for running Hardwood as a normal part of your Mac: it starts when you log in,
keeps itself up to date, and you never have to leave a Terminal window open. You do not need to
know how any of it works. If you have never used a command line, every command below is meant to
be copied exactly, one at a time, into the **Terminal** app (Applications → Utilities →
Terminal).

Hardwood here is **private and for you alone**. Everything it fetches, it fetches for your own
use, slowly, from your own Mac, and it never shows anything to anyone else. That is not a detail:
it is the whole basis on which the data sources are used (see "Where the data comes from"
below, and [LEGAL.md](LEGAL.md) §2d and §2e). The one rule that follows: do not put Hardwood on
a public address.

### What gets installed

Four small background programs, called *launch agents*, all owned by your user account. None
needs an administrator password, and none touches anything outside your home folder.

| Program | What it does |
| --- | --- |
| `com.hardwood.api` | The Hardwood server. It listens on `127.0.0.1` only, so nothing outside your Mac can reach it. The Mac app and the browser talk to this. |
| `com.hardwood.nba-watch` | Watches NBA games and writes each one the moment it finishes. |
| `com.hardwood.nba-nightly` | At 06:10 each morning, re-checks the last few days of NBA games, because the league corrects box scores after the fact. |
| `com.hardwood.worker` | Everything else, in one program: team projections, the NBA injury report, headlines, the EuroLeague, and importing your workbook. Details below. |

Each restarts itself if it ever stops.

### Before you start

You need two things.

**1. Python 3.11 or newer.** The `python3` that ships with macOS is too old (it is 3.9) and the
installer will not use it. The easiest way to get a current one is Homebrew:

```bash
brew install python@3.12
```

No Homebrew? Install it first from <https://brew.sh>, or download Python from
<https://www.python.org/downloads/macos/>. You do not need to make it your default Python; the
installer finds it.

**2. The Hardwood folder somewhere macOS allows.** macOS stops background programs from reading
**Documents, Desktop, Downloads and iCloud Drive**. A Hardwood folder in any of them looks fine
and then fails with "Operation not permitted" in the background, with no obvious reason. The
installer checks for this and stops with instructions. Keep the folder directly in your home
folder:

```bash
mv ~/Downloads/NBA-Stats ~/NBA-Stats     # only if it is somewhere macOS protects
```

Leave it there afterwards. The installed programs run the code in that folder, which is also how
updating works (below).

### Install

```bash
cd ~/NBA-Stats
backend/scripts/macos/install.sh
```

The first run takes a few minutes, mostly downloading Python packages. It:

1. creates the Hardwood **data folder**, `~/Library/Application Support/Hardwood`;
2. makes a private Python environment inside it and installs what Hardwood needs;
3. writes your settings file, `hardwood.env` (and never overwrites it on a later run), and puts a
   private **API key** in it (next section);
4. writes the four launch agents to `~/Library/LaunchAgents`, starts them, and checks the server
   answers.

It is safe to run again whenever you like, for example after an update. `install.sh --help`
lists the options. The ones you might want: `--port 8123` if something else on your Mac already
uses port 8000, `--dry-run` to see what it would do without changing anything, and `--no-api-key`
if you do not want a key made (see below for what that costs).

### The API key

The installer makes a random key (256 bits, `secrets.token_urlsafe(32)`) and writes it to
`hardwood.env` as `HARDWOOD_API_KEY=...`, and the server starts with it. It exists so the Mac app
can **change** things: type in an injury status, paste a headline link, retract a status, change a
model setting. The server accepts such a change from exactly two callers, and no third:

* the Mac app, which sends the key in `X-API-Key` and no browser headers;
* the web app, with its signed-in session, its CSRF token and a same-origin `Origin`.

A web page open in your browser cannot use the key even if it somehow learned it, because every
browser request carries an `Origin` header that the page cannot remove, and a request with an
`Origin` is judged as a browser request whatever else it sends. `X-Hardwood-Client` is **not** a
credential and is never read; without a key, a request from this same Mac is refused too. (Why:
the server answers any browser's CORS preflight, so a page can send any header to
`http://127.0.0.1:8000`, and the request does come from loopback.) The full rule is in
[`contracts/CONTRACT.md`](../contracts/CONTRACT.md) §3, "Who may write".

How the installer treats the key, each one a test: it makes one **only when there is none**; it
**never replaces** a key already in the file (your own, or the one it made last time), and a key
you chose survives every re-install; an empty `HARDWOOD_API_KEY=` line counts as "no key" and is
filled in; it never prints the key, only whether it made one; and `hardwood.env` ends up readable
by you alone (mode 600). To rotate the key, delete the `HARDWOOD_API_KEY` line, run `install.sh`
again, and restart the app. To use your own, put it on that line.

With a key set, the server also asks for it on **reads** (except `/v1/health`), or for a signed-in
web session, which is what keeps a web page from reading your local statistics. The consequence to
know about: opening `http://127.0.0.1:8000` in a browser without signing in no longer shows
statistics. The Mac app reads the key from `hardwood.env` and sends it with every request.
`install.sh --no-api-key` leaves the file without one, and then **every change from the Mac app
is refused** (there is no keyless way to write); reads stay open to anything on your Mac.

### Check that it is working

```bash
curl -s http://127.0.0.1:8000/v1/health            # should print a line of JSON
launchctl list | grep hardwood                     # four rows (see below)
~/Library/"Application Support"/Hardwood/hardwood-python -m nbastats.worker --list
```

`launchctl list` shows a process number for the programs that are running. `nba-nightly` shows a
dash until 06:10, which is normal: it is a once-a-day job, not one that stays running.

**`hardwood-python` is the way to run any Hardwood command yourself.** The installer writes it
into the data folder. It runs the installed Python with the same settings the background programs
use, so a command typed in Terminal looks at the same databases they do. Without it a command
would quietly open a different, empty database and report problems that are only a path. Every
command in this section that starts with `python -m nbastats...` means
`~/Library/"Application Support"/Hardwood/hardwood-python -m nbastats...`; if you will type it
often, add a shortcut to `~/.zshrc`:

```bash
alias hardwood='"$HOME/Library/Application Support/Hardwood/hardwood-python"'
```

and then write `hardwood -m nbastats.worker --list`.

The last command prints every background job, whether it is switched on, when it last ran, and
the reason when it is not running. It is the first thing to run when something looks wrong. A
job marked `off` with a reason is working as designed; a job marked `not installed` is waiting
for a part of Hardwood that is not on this machine yet.

### The Mac app

The server above is what the native Mac app talks to. To build and run it, open
`ios/NBAStats.xcodeproj` in Xcode 16 or later, choose **My Mac** as the run destination and press
Run (⌘R). The app starts on live data at `http://127.0.0.1:8000/v1`, reads the API key from
`hardwood.env` itself (it never shows or rewrites it), and, when the server is not answering, shows
a banner with **Try Again**, **Use Demo Data**, **Copy Restart Command** (the `launchctl kickstart`
line below) and **Open Logs Folder**.

[MAC.md](MAC.md) is its manual: the run story, what to do if Xcode asks about signing, first launch
with live or demo data, what each screen shows today and what is not shown yet, and how to paste
Xcode's errors back.

The app reads what the server serves and fetches nothing from the internet itself. The only things
it can change on the server are availability statuses and headline links, and both need the API key
from "The API key" above.

### Where everything lives

| What | Where |
| --- | --- |
| Data folder | `~/Library/Application Support/Hardwood/` |
| NBA store (stats, accounts, saved dashboards, injury and headline records) | `hardwood.db` |
| EuroLeague store, kept completely separate | `hardwood_el.db` |
| Your settings | `hardwood.env` |
| **Drop your EuroLeague workbook here** | `inbox/` |
| Raw downloaded files, kept for diagnosis and never shown | `raw/`, `el-raw/` |
| Recordings made by the probe commands (never committed anywhere) | `recordings/` |
| Python environment, and the `hardwood-python` shortcut that runs it with Hardwood's settings | `venv/`, `hardwood-python` |
| Logs | `~/Library/Logs/Hardwood/` (`api.log`, `worker.log`, `nba-watch.log`, `nba-nightly.log`) |
| **The Mac app's dashboards** | `~/Library/Application Support/com.hardwood.nbastats/Layouts.json`. Deliberately *not* in the data folder above, so `uninstall.sh --purge-data` leaves them alone |
| The Mac app's widget cache (safe to delete) | `~/Library/Caches/Hardwood/Widgets` |

The two databases are separate files on purpose: a re-seed of the NBA demo league cannot touch
the EuroLeague, and real and invented data can never be mixed in one file.

### What you get on day one — said plainly

**NBA matchups, defence by position and team projections** work from the NBA store as soon as it
has games in it. A brand-new install starts empty: `nba-watch` fills it game by game as the
season runs, and §2b above loads a past season in one command if you want history now. (To
explore with invented data and no network, `backend/scripts/serve_dev.sh --fresh` serves the
demo league instead; do not point the installed Hardwood at a demo database, see §2b.)

**NBA injuries** are read automatically from the NBA's own injury report. The 2026-27 season
has no reports before about **19 October**, so until then the injury screens say there is no
report yet, which is true. The report parser has been built from the report's documented layout
but **has not yet been confirmed against a real report**; `/v1/sources` says so until it has
parsed one successfully on your Mac. If the NBA changes the layout, you see "unreadable", never a
wrong status.

**The EuroLeague** needs your workbook. Copy the `.xlsx` into the `inbox/` folder and within a
minute Hardwood imports Rounds 1 and 2 (results and box scores), the Round 3 fixtures and the 56
sourced availability statuses. **Round 3 was played on 1–2 October, so it shows as "result
pending", not as upcoming**, until fresher data arrives. Fresh data comes from either:

* live ingest, which is on by default and fetches from the EuroLeague's own data service (a
  polite trickle, about 90 requests a week); or
* importing an updated workbook, by dropping it in `inbox/`.

Availability statuses are always shown with their source and their date. Nothing is presented as
live that is not.

**Headlines** are on for the configured feeds. Hardwood stores only a title, a link, a date and
the source's name, never an article, and it reads each feed's `robots.txt` before fetching. A
feed that disallows fetching is switched off by itself, with the reason shown in the Sources
screen.

### The background worker

`com.hardwood.worker` is one program with its own timetable. Because a Mac sleeps, the timetable
is built around "what is the latest thing that should have happened by now", not "do this at
exactly 05:00": if the Mac was asleep at 05:00, the job runs once when it wakes. It does not
replay what it missed. **Hardwood fetches only while the Mac is awake and online.**

| Job | What it does | When |
| --- | --- | --- |
| `nba.rosters` | Fetches each NBA team's roster for the player positions defence-by-position needs | Mondays 05:30, and on start if the last run is over 7 days old |
| `nba.injuries` | The NBA's official injury report | Checked every 15 minutes; it decides for itself: it only fetches when a game is within 36 hours, every 15 minutes inside the reporting windows and hourly otherwise |
| `nba.news` | NBA headlines from the enabled feeds (only headlines that name an NBA team or player; not run against the demo league) | Hourly |
| `projections.refresh` | Recomputes team projections for games in the next 48 hours, both leagues | Every 30 minutes |
| `projections.lock` | Freezes each projection just before tip-off, so a review compares what was actually predicted | Every 5 minutes |
| `projections.calibrate` | Fits the projection spreads from past results | Daily 05:00 |
| `el.structure` | EuroLeague round calendar and clubs | Checked hourly; fetches weekly |
| `el.round` | EuroLeague fixtures and results | Checked every 10 minutes; fetches daily and again after each game is due to have finished. The "current round" is the earliest with a scheduled game, so a postponed game never pins it; rounds holding a postponed game (at most 2, none older than 60 days) are re-read in the daily sweep so a new date is noticed. After an unreadable answer it waits 1 h, 2 h, 4 h, then 6 h before asking again |
| `el.box` | Box scores for finished EuroLeague games | Checked every 10 minutes |
| `el.rosters` | EuroLeague rosters and listed positions | Checked hourly; fetches Mondays and the day before each round that has not begun |
| `el.news` | EuroLeague headlines | Hourly |
| `el.ratings` | Updates club ratings after each completed round | Checked every 15 minutes |
| `el.workbook` | Imports any `.xlsx` you drop in `inbox/` | Checked every minute |

Where the table says "checked", the job looks at the schedule and the data and quietly does
nothing unless something is due. That is how the request budget stays small.

### Settings and switches (`hardwood.env`)

Everything is **on** by default, and each source has a switch to turn it off. Open
`~/Library/Application Support/Hardwood/hardwood.env` in TextEdit. Every line in it is a default
that is switched off with a `#`; delete the `# ` in front of a line to change it, and read the
note above it. (The one live line is `HARDWOOD_API_KEY`, which the installer wrote; leave it, and
see "The API key" above before touching it.) For example, to stop headlines:

```
HARDWOOD_NEWS=off
```

| Setting | Default | Turns off |
| --- | --- | --- |
| `HARDWOOD_EL_LIVE` | `on` | EuroLeague live data from the data service. Your workbook still works. |
| `HARDWOOD_EL_ENABLED` | `on` | The whole EuroLeague: its store, screens and jobs |
| `HARDWOOD_NBA_INJURIES` | `on` | The NBA injury report job. It also refuses on its own while the NBA store holds the demo league. |
| `HARDWOOD_NEWS` | `on` | Headlines for both leagues |
| `HARDWOOD_WORKBOOK_PATH` | unset | (A path, outside Documents, Desktop, Downloads and iCloud Drive.) Import this one file as well as the inbox |
| `HARDWOOD_WORKER_TICK_SECONDS` | `30` | (A number, 5–300.) How often the worker checks what is due |
| `HARDWOOD_EL_MODE` | unset | An older name. `off` stops the EuroLeague; `demo` or `workbook` stops live fetching |

`HARDWOOD_EL_ENABLED` also decides whether the API mounts the EuroLeague screens, and the API
only reads the file at start, so that one needs the restart described below to take full effect;
the worker side stops within the half minute either way.

Values are `on` or `off` (also `1`/`0`, `yes`/`no`, `true`/`false`). A value that is neither is
treated as **off**, and `--list` shows the reason, so a typo can never leave a source running
that you meant to stop.

**When a change takes effect.** The worker re-reads the file every 30 seconds, so a switch
there stops or starts that work within the half minute, with no restart. The API and the two NBA
programs read the file when they start; to apply a change to them:

```bash
launchctl kickstart -k gui/$(id -u)/com.hardwood.api
```

(and the same with `com.hardwood.nba-watch`). A value set in the launch agents themselves, which
means the folder and database paths the installer wrote, wins over the file, which is why the
switches are not in the agents.

**The one refusal.** If `HARDWOOD_PUBLIC_BASE_URL` is set to anything other than this Mac
(`127.0.0.1` or `localhost`), the worker **refuses** to run the jobs that fetch EuroLeague data,
injury reports or headlines, and says so in the log and in `--list`. A private tool becomes a
different thing when other people can reach it ([LEGAL.md](LEGAL.md) §2b), so the fetching stops
rather than carrying on quietly. Set it back to `http://127.0.0.1:8000` (or remove the line).

### Importing your EuroLeague workbook

1. **Copy** the `.xlsx` into `~/Library/Application Support/Hardwood/inbox/` (in Finder: Go →
   Go to Folder, paste that path). Copy rather than point at the original: a workbook left in
   Downloads, Documents or Desktop cannot be read by a background program, and Hardwood will say
   so in `--list` and the log.
2. Wait about a minute. Hardwood leaves a file alone until it has stopped changing, so a large
   copy is never read half-finished.
3. Check `~/Library/Logs/Hardwood/worker.log` for `event=workbook_imported`, or open the
   EuroLeague screens.

Importing the same file again does nothing. Dropping in a **newer** workbook (the same name or a
new one) adds its later rounds and any new statuses; history is never rewritten, and an official
row from the data service is never overwritten by a workbook row. The workbook's own betting
columns, if it has any, are not read. The importer says exactly what it imported and what it
skipped, in the log. You can also run it by hand:

```bash
~/Library/"Application Support"/Hardwood/hardwood-python -m nbastats.euroleague.ingest \
    import-workbook ~/Downloads/EuroLeague_2026-27_Toolkit.xlsx --dry-run
```

### Two checks to run once, and record

These are verification records, not gates: Hardwood runs without them, and they exist so that
what you are shown is known to be right rather than hoped to be.

**1. The Round 3 replay.** Your workbook computed ten Round 3 projections. This check
recomputes all ten from the workbook's own inputs and compares them to the workbook's own cells
to nine decimal places. It reads your real workbook, so it lives in a folder that git ignores:

```bash
cd ~/NBA-Stats/backend
HW=~/Library/"Application Support"/Hardwood/hardwood-python
"$HW" -m pip install -e ".[test]"        # first time only: adds the test runner (pytest)
HARDWOOD_WORKBOOK_PATH=~/Downloads/EuroLeague_2026-27_Toolkit.xlsx \
  "$HW" -m pytest tests/local/test_workbook_round3_replay.py -q
```

When it passes, write the date and result in the "Replay record" table in
[EUROLEAGUE.md](EUROLEAGUE.md). Until you have, the EuroLeague projections are labelled as not
yet replayed against the workbook.

**2. The NBA injury report, against a real report.** Once the season's first reports are out
(about 19 October), record a couple with the probe:

```bash
cd ~/NBA-Stats
~/Library/"Application Support"/Hardwood/hardwood-python backend/scripts/probe_nba_injury_report.py
```

It saves the PDFs to `recordings/`. Then open the Sources screen (or
`curl -s http://127.0.0.1:8000/v1/sources`). The NBA injury report row changes from "not yet
confirmed against a real report" once the parser has read one successfully. If a report cannot be
read it says `unreadable`; nothing is guessed. The same pattern applies to the EuroLeague data
service: `python -m nbastats.euroleague.ingest probe` records real responses to `recordings/`
for diagnosis, with no gate and nothing sent anywhere.

### Where the data comes from

There is no checklist to complete before Hardwood runs. [DATA_SOURCES.md](DATA_SOURCES.md) §7
lists every source, what is taken from it and the posture it is used under, and
[LEGAL.md](LEGAL.md) §2d and §2e state the same plainly, including the part worth knowing:
**the terms of these sources could not be read from the environment this code was written in, so
the posture is private, personal and non-commercial use, never redistributed, never used for
gambling, and attributed. Sharing, publishing or putting Hardwood in front of other people
changes everything.** If you ever want to do that, start with those two sections, not with a
setting.

### Updating Hardwood

```bash
cd ~/NBA-Stats
git pull
backend/scripts/macos/install.sh --skip-pip      # reload the programs; add nothing
```

Drop `--skip-pip` if the update says it changed the requirements. Your data and your
`hardwood.env` are never touched.

### Stopping and removing it

```bash
backend/scripts/macos/uninstall.sh                  # stop and remove the four programs; keep all data
backend/scripts/macos/uninstall.sh --remove-venv    # also remove the Python environment
```

Your data stays in the data folder, and installing again picks up exactly where you left off.
`uninstall.sh --purge-data` deletes the data folder too, asks you to type `DELETE` first, and
refuses to touch any folder that does not look like a Hardwood data folder.

### Backups

The data folder holds two databases. Both use write-ahead journaling, so copying the `.db` file
alone can miss recent changes. The safe way is:

```bash
cd ~/Library/"Application Support"/Hardwood
sqlite3 hardwood.db    ".backup '$HOME/Backups/hardwood-$(date +%F).db'"
sqlite3 hardwood_el.db ".backup '$HOME/Backups/hardwood_el-$(date +%F).db'"
```

The NBA store holds anything you typed in by hand (saved dashboards, availability notes). Both
stores can be rebuilt from their sources if lost, except what you typed. `hardwood.env` holds the
API key: if you back it up, keep the copy as private as the original (mode 600, not in a shared
or synced folder). The Mac app's own dashboards are one file, `Layouts.json` in
`~/Library/Application Support/com.hardwood.nbastats/`; copy it to back them up.

---

## 2. Loading real historical data

Download the bulk files first — do **not** point the API backfill at stats.nba.com.

```bash
# Wyatt Walsh's NBA Database (Kaggle): games, box scores, play-by-play from 1946
kaggle datasets download -d wyattowalsh/basketball -p /data/nba --unzip

# Sumitro Datta's Basketball-Reference season dumps: the only pre-1997 advanced source
kaggle datasets download -d sumitrodatta/nba-aba-baa-stats -p /data/bbref --unzip
```

```bash
cd backend
python3 -m nbastats.ingest.runner --backfill-kaggle /data/nba/nba.sqlite
python3 -m nbastats.ingest.runner --backfill-bbref  /data/bbref
python3 -m nbastats.ingest.runner --reconcile-ids            # build the id crosswalk
```

Loaders are chunked, resumable and idempotent; each run is recorded in `ingest_log`. Re-running
after an interruption is safe.

After a backfill, check the crosswalk report for unmatched players before trusting cross-source
joins — NBA.com integer ids, Basketball-Reference slugs and ESPN ids have no official mapping
between them.

---

## 2b. Loading a real season straight from NBA.com

The bulk files above are the right answer for deep history. For *one recent season* — enough to
search a real roster, see real numbers and run the projection engine on them — the live client
can walk the schedule itself, with no downloads.

### First, a fresh database. This part is not optional.

`seed.py` mints game ids in the **real NBA format**: `00` + a season-type digit + the two-digit
year + a five-digit sequence, so a seeded 2025-26 league occupies `0022500001`…`0022501230` —
exactly the id space real 2025-26 regular-season games live in. Ingesting real data into a
seeded database therefore *upserts real box scores onto synthetic ones*, leaving `player_season`
a blend of measured and invented numbers with nothing to tell them apart.

```bash
cd backend
export DATABASE_URL="sqlite:///./hardwood-live.db"   # a NEW file
unset HARDWOOD_DEMO_MODE                             # do not seed into it
```

### Then walk the season

`--nightly` is a date-range walker, not just a 3-day correction pass: `--days` sets the width and
`--date` sets the **last** day of the window.

```bash
python3 -m nbastats.ingest.runner --nightly --days 200 --date 2026-04-15
```

That covers the 2025-26 regular season. It commits **per day**, so it is safe to interrupt and
safe to re-run: every write is an upsert, `data_through` only moves forward, and a resumed run
picks up rather than duplicating.

| | |
| --- | --- |
| Requests | `1 + 3 × scopes` per day — 4 on a normal night, 7 when a Play-In and the regular season share a date |
| Pacing | 1.0s floor between calls, so ~200 days is roughly 15–25 minutes |
| Writes | `teams`, `players`, `games`, `player_game_basic`, `player_game_advanced`, `team_game`, then season aggregates **once** at the end |

### Restoring who started: `--refetch-games`

Starters are recorded by the per-game path and by nothing else, so two situations leave a database
with starters missing that no nightly pass can put back:

* a season loaded by the bulk walk above (every `started` is unrecorded), and
* a database the old ingest bug flattened (an earlier release wrote `False` for "this source does
  not say", so the first correction pass overwrote every recorded starter with a bench player and
  games started fell to zero for everyone; the fix stops that, and deliberately does not rewrite
  history).

`--nightly --days N` **cannot restore them**: its lines carry no starter flag, and a line that is
silent about it leaves the stored flag alone. The repair is the per-game box score, run again over
the games already stored for a date range:

```bash
python3 -m nbastats.ingest.runner --refetch-games --days 200 --date 2026-04-15
```

`--date` is the **last** day of the window and defaults to the day the store is current through;
`--days` is the width and defaults to `CORRECTION_WINDOW_DAYS`. It covers the *stored final games*
of those days: it does not look for games, so a date nothing was ever ingested for is skipped
without a request.

| | |
| --- | --- |
| Requests | 2 per game (the traditional and the advanced box score), so a 200-day season of about 1,230 games is roughly 2,500 requests |
| Pacing | The client's 1.0 s floor between calls: **about 40 minutes** for a full season, a few seconds for a night |
| Safe to stop | Yes. It commits a game at a time; Ctrl-C finishes the game in hand, rebuilds the season aggregates for what was repaired, and exits 130. Run it again to carry on |
| Safe to repeat | Yes. The second run changes nothing and moves no sync version |
| A game that cannot be fetched | Counted, named in the log and in `ingest_log`, and the run carries on. Run it again later for those |
| The demo league | Refused (exit 5), with no request: its game ids are real games' ids, and real box scores must never land on invented ones |

The log line `refetch_games_complete` says `starts_before`, `starts_after` and `unrecorded_after`:
a repaired season shows about ten starters a game and `unrecorded_after=0`. Season aggregates are
rebuilt once at the end, and `sync_version` moves once per changed game plus once after the rebuild,
so the app refreshes. Exit status: **0** done (some games may be named as unfetched), **3** games
were found and none could be fetched, **5** refused, **130** interrupted.

### What this gives you, and what it does not

* **Real players, real teams, real statistics** for every game on those dates. Search finds them
  because `ensure_player` writes a row for everyone who appears in a box score.
* **No starters.** Who started is in the per-game box score only; the bulk rows `--nightly` reads
  have no such column, so every game this walk loads has `started` *unrecorded* and games started
  (`gs`) is 0 until it is repaired. Running `--nightly` again cannot fix that. Run
  `--refetch-games` (above) over the same range.
* **No roster endpoint is called.** A player who did not play in the window does not exist.
  `common_all_players` is implemented in `ingest/client.py` and wired to nothing.
* **No schedule is ingested.** `poll_finalized_games` only writes the date you asked for, and
  after a finished day every game on it is final — so there is never a `scheduled` row ahead of
  `data_through`. `next_game_projection` therefore falls back to a league-average opponent and
  says so in its notes, exactly as it does out of season.
* **Headshot URLs are null** on players first seen through a box score. The identity file has
  them; nothing on this path reads it for players.

---

## 3. Keeping current — the part that makes stats appear after each game

> **On a Mac, do not set this up by hand.** `backend/scripts/macos/install.sh` ([§1c](#1c-hardwood-on-your-mac--installed-always-on))
> installs `--watch` and the nightly pass as background programs with the right settings, and adds
> the worker for everything else. What follows is the same thing done manually, which is what you
> want on a Linux box or a Raspberry Pi.

```bash
pip install nba_api          # optional dependency, only needed for the live path
python3 -m nbastats.ingest.runner --watch     # or: backend/scripts/ingest_watch.sh
```

`--watch` polls the scoreboard every `INGEST_POLL_SECONDS` during the game window, ingests each
game the moment it goes Final, bumps `sync_version` once per game, and recomputes the affected
aggregates. Outside the window it sleeps.

Nightly, run the correction pass — the league revises box scores after the fact:

```cron
# 05:30 US Eastern: re-pull the last three days and re-aggregate
30 5 * * *  /srv/hardwood/backend/scripts/ingest_daily.sh >> /var/log/hardwood-ingest.log 2>&1
```

### ⚠ The deployment constraint that catches everyone

**stats.nba.com silently blocks datacenter IP ranges** — AWS, GCP, Azure. Requests do not error;
they hang. GitHub Actions runners are usually on those ranges too.

So:

* run the ingest worker on a **residential connection** (a home box, a Raspberry Pi, a
  residential-IP VPS), or set `NBA_API_PROXY` to a residential pass-through proxy;
* keep only the database and the API in a cloud region;
* **test before relying on any runner** — a five-minute check saves a week of confusion.

Bulk file downloads from Kaggle and GitHub work fine from anywhere.

---

## 4. Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./hardwood.db` | Any SQLAlchemy URL; Postgres needs no code change |
| `HARDWOOD_API_KEY` | unset (**set by `install.sh` on a Mac**) | When set, every `/v1` route except `/v1/health` requires `X-API-Key` — **or** a valid web session cookie, so the SPA keeps working. It is also the Mac app's credential for league **writes**, accepted only on a request with no `Origin` header; with no key configured no write is accepted without a session (see "The API key", §1c) |
| `HARDWOOD_DEMO_MODE` | `0` | Seed synthetic data at startup if the database is empty |
| `HARDWOOD_CONTRACTS_DIR` | repo `contracts/` | Override the catalog location |
| `NBA_API_PROXY` | unset | Residential proxy for the ingest client |
| `INGEST_POLL_SECONDS` | `60` | Scoreboard poll interval during the game window |
| `CORRECTION_WINDOW_DAYS` | `3` | How many past days the nightly pass re-pulls |
| `CURRENT_SEASON` | derived | Override the season the literal `"latest"` resolves to |
| `HARDWOOD_INGEST_FIXTURES` | unset | Read recorded JSON instead of calling the network (tests) |
| `LOG_LEVEL` | `INFO` | |
| `HARDWOOD_DATA_DIR` | `~/Library/Application Support/Hardwood` on a Mac | Where the databases, raw payloads, inbox, recordings and `hardwood.env` live |
| `HARDWOOD_ENV_FILE` | `<data dir>/hardwood.env` under the Mac install, else `backend/.env` | The settings file the API, the worker and the NBA programs read |
| `HARDWOOD_LOG_DIR` | unset | When set the worker logs to `worker.log` there (rotated, 2 MB × 3) instead of the screen |
| `HARDWOOD_EL_DATABASE_URL` | `hardwood_el.db` beside the NBA file | The EuroLeague store. A separate file; pointing it at the NBA file is refused |
| `HARDWOOD_EL_ENABLED` | `on` | The EuroLeague feature as a whole |
| `HARDWOOD_EL_LIVE` | `on` | EuroLeague live ingest from the data service |
| `HARDWOOD_EL_MODE` | unset | Older name for the two above: `off`, `demo`, `workbook` or `live` |
| `HARDWOOD_NBA_INJURIES` | `on` | The NBA injury report job (refused on a demo league) |
| `HARDWOOD_NEWS` | `on` | Headlines, both leagues; each feed's `robots.txt` is checked first |
| `HARDWOOD_WORKBOOK_PATH` | unset | A EuroLeague workbook to import, in addition to the inbox |
| `HARDWOOD_WORKER_TICK_SECONDS` | `30` | How often the worker checks what is due (5–300) |
| `HARDWOOD_*` (web/accounts) | see [WEB.md](WEB.md) §3 | Public base URL, signup mode, cookies, sessions, providers, SMTP, proxy trust, CSP |

---

## 5. Operating checks

```bash
curl -s localhost:8000/v1/health          # databaseReady, syncVersion, dataThrough
curl -s 'localhost:8000/v1/sync?since=0'  # what changed, and which widget kinds to invalidate
sqlite3 hardwood.db 'select * from sync_state;'
sqlite3 hardwood.db 'select job, status, started_at, games_written, error from ingest_log order by id desc limit 10;'
```

### Backups — the database is three files, not one

WAL journaling is on for every SQLite engine this project creates, so `hardwood.db` alone is an
incomplete copy: committed transactions can still be sitting in `hardwood.db-wal`. Since the web
release that is not an abstract risk — the missing tail is the most recent sign-ups and the
dashboard somebody just saved, and unlike the stats it cannot be re-ingested.

```bash
sqlite3 backend/hardwood.db ".backup '/backups/hardwood-$(date +%F).db'"   # consistent, online, one file
```

If you would rather copy files, stop the service and copy all three (`.db`, `.db-wal`,
`.db-shm`). [WEB.md](WEB.md) §10 has the longer version.

### Accounts housekeeping

```cron
# 04:00 daily: complete deletions past their 30-day window, sweep expired sessions and tokens
0 4 * * *  cd /srv/hardwood/backend && .venv/bin/python3 -m nbastats.accounts.admin purge >> /var/log/hardwood-purge.log 2>&1
```

`DELETE /v1/me` soft-deletes; **`purge` is what finishes it**. Without the cron line, a deletion
never completes. `admin.py doctor` is the other one to know: `create_all` never `ALTER`s and
there is no Alembic, so a new column on an existing account table will not appear on restart,
and `doctor` is how you find out rather than how you find out from a traceback.

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Ingest requests hang forever, no error | Datacenter IP block | Move the worker to a residential IP or set `NBA_API_PROXY` |
| `403`/empty responses from stats.nba.com | Missing browser headers | Check `ingest/client.py` headers; pin a known-good `nba_api` |
| `sync_version` climbing but `dataThrough` stuck | Some game on the slate is not Final | Expected — `dataThrough` only advances on a complete slate |
| A stat shows an em dash for an old season | Working as designed | That stat did not exist then; see [DATA_SOURCES.md](DATA_SOURCES.md) §3 |
| A widget tile shows an error but the rest render | Per-widget failure isolation | Read `requestId` in the result and grep the server log |
| Numbers changed for a game played two days ago | League stat correction | Expected — the three-day re-pull window exists for this |
| Games started (`gs`) is 0 for everyone, or starters are missing after a season walk | `--nightly` never records starters, and the old ingest bug flattened any that were recorded | `--refetch-games --days N --date D` over the same range (§2b); a nightly re-run cannot fix it |
| The Mac app says "Your server needs its API key for changes" | `hardwood.env` has no `HARDWOOD_API_KEY` (installed with `--no-api-key`, or the line was removed), or the app is reading a different file | Run `install.sh` again (it adds one and never replaces yours), then restart the server: `launchctl kickstart -k gui/$(id -u)/com.hardwood.api` |
| A browser at `http://127.0.0.1:8000` shows no statistics after an update | A key is set, so reads need it or a signed-in session (a web page must not be able to read your store) | Sign in on the web page; the Mac app is unaffected |
| The Mac app's banner says "Hardwood's server isn't answering at http://127.0.0.1:8000" | The `com.hardwood.api` agent is stopped or crashed | `launchctl list \| grep hardwood`; use the banner's **Copy Restart Command** and paste it into Terminal; read `tail ~/Library/Logs/Hardwood/api.log`. **Use Demo Data** browses invented data meanwhile |
| The Mac app's banner says the server refused the API key | The key the app stored is not the one in `hardwood.env` now (rotated, or the file changed) | In the app: the banner's **Read Key from hardwood.env**, or Settings ▸ Leagues ▸ Read API key from hardwood.env |
| Every league screen in the Mac app is empty, with no banner | The server answers but its store has no games yet | NBA: load a season (§2b). EuroLeague: drop your workbook into `inbox/` (§1c). [MAC.md](MAC.md) §8 says what each screen looks like on day one |
| `check_contracts.py` fails after editing a catalog | Generated files not regenerated, or the app's bundled copies drifted | Re-run the `contracts/tools/gen_*.py` generator, then `scripts/sync_contracts.sh` |
| `git pull` aborts with "local changes to ios/NBAStats.xcodeproj/project.pbxproj" | Xcode rewrites the project file on its own — opening the project is enough | Quit Xcode, then `git checkout -- ios/NBAStats.xcodeproj/project.pbxproj` and pull again. That edit is Xcode's bookkeeping, not your work. **Check `git log --oneline -1` before concluding a fix did not work**: a silently aborted pull looks exactly like a failed fix. |
| Search finds no current players (Doncic, LeBron, Jokic) | The app is in demo mode, whose search index is harvested from the bundled widget fixtures — the couple of dozen players on the sample dashboard, not a roster. The seeded demo *database* does contain them; demo mode just never opens it | Start the service (`§1`), then Settings → Data source → turn off "Use bundled demo data". The seeded league has 600+ real identities including Doncic, on synthetic teams with generated numbers |
| Xcode still reports a build error a fix was supposed to clear | The pull may not have landed, or DerivedData is stale | `git log --oneline -1` first, then `rm -rf ~/Library/Developer/Xcode/DerivedData/NBAStats-*` and Clean Build Folder |
| `/` returns 404 but `/v1/health` is fine | `web/dist/index.html` is missing, so `mount_web` mounted nothing and logged a warning | `./scripts/web.sh build`. If Node is missing it says so in a sentence |
| `web.sh doctor` shows a provider as enabled, the site shows no button | `doctor` reads `backend/.env`; the server does not | `set -a; . backend/.env; set +a` before starting, then re-check `GET /v1/auth/methods` |
| The server exits immediately with a `RuntimeError` naming a `HARDWOOD_*` variable | A startup refusal — cleartext cookies off loopback, open signup with no backstop, a group-readable Apple `.p8`, or a cleartext `smtp://` URL | The message names the variable and the fix. [WEB.md](WEB.md) §3 |
| Google sign-in fails at the callback with `invalid_client` | The client ID and secret do not belong together. Whitespace is not the cause — every value is stripped as it is read | Re-copy both from the Credentials page. A *redirect URI* mismatch is Google's own distinct error |
| Everyone shares one rate-limit bucket behind a proxy | `HARDWOOD_TRUSTED_PROXY_CIDRS` is empty, so `X-Forwarded-For` is stripped from every request | Set it and `HARDWOOD_TRUSTED_PROXY_HOPS`. `/v1/health.authWarnings` warns about exactly this |
| An agent fails with "Operation not permitted", or logs nothing at all | The Hardwood folder is in Documents, Desktop, Downloads or iCloud Drive, which macOS hides from background programs | Move the whole folder into your home folder (`mv ... ~/NBA-Stats`) and run `install.sh` again. The installer refuses these folders for exactly this reason |
| `install.sh` stops: "launchd would not load" | You are not logged in at the Mac's own screen (for example, connected over SSH), or an old copy of the agent is half-loaded | Run it in Terminal on the Mac. If it still fails, `uninstall.sh` and then `install.sh` again |
| `install.sh` stops: "Port 8000 is already in use" | Another program uses it | `install.sh --port 8123`, or stop that program |
| `worker --list` shows a EuroLeague job as `off`: "The EuroLeague store does not exist yet" | The API creates the EuroLeague store when it first starts, and has not yet | Wait a few seconds after install and run `--list` again. If it persists, the API is not running: `tail ~/Library/Logs/Hardwood/api.log` |
| A job is `off` with a reason that names a `HARDWOOD_...` setting | You (or a typo) switched it off in `hardwood.env` | Delete or correct the line; the worker picks it up within 30 seconds |
| Jobs are `off`: "HARDWOOD_PUBLIC_BASE_URL ... is not a loopback address" | The server address was set to something other than this Mac | Set it back to `http://127.0.0.1:8000` or remove the line. See "The one refusal" in §1c |
| A job is `not installed` | The part of Hardwood that job lives in is not on this machine: an older checkout, or the install step that adds the PDF reader was skipped | `git pull` and run `install.sh` (without `--skip-pip`) |
| A job shows an `error` in `--list` | Read the message next to it, then `tail ~/Library/Logs/Hardwood/worker.log`. A source being down is normal and retried | Nothing to do for a transient error: failed daily and weekly jobs retry after 15 minutes, then 30, up to 6 hours |
| Injuries or headlines show "unreadable" or the source shows `blocked` | The NBA changed its report layout (the parser refuses to guess), or a source returned 401, 403 or a rate-limit block, which pauses that source for 6 hours | Nothing wrong is shown in the meantime. `/v1/sources` gives the reason and the time it will retry |
| Everything looks a day stale after a trip or a long sleep | The Mac was asleep; Hardwood fetches only while awake | Wake the Mac. The worker catches up within a minute, and the nightly pass runs once on wake |
| `worker` says "Another Hardwood worker is already running" | The installed agent already runs it | Use `--list` or `--run KEY` (which do not need the lock), or `launchctl bootout gui/$(id -u)/com.hardwood.worker` first |

---

## 6. Tests

```bash
cd backend && python3 -m pytest -q          # the backend suite; it opens no network connection
python3 scripts/check_contracts.py          # contract drift guard, every check must pass (from the repo root)
cd web && npm test && npm run build         # the web suite; CI then fails on any diff in web/dist
python3 -m nbastats.fixtures_export --out ../contracts/fixtures && ./scripts/sync_contracts.sh
```

The Swift app, on a Mac with Xcode 16+ (one target, both platforms; [MAC.md](MAC.md) §15):

```bash
cd ios
xcodebuild test -scheme Hardwood -destination 'platform=iOS Simulator,name=iPhone 16'
xcodebuild test -scheme Hardwood -destination 'platform=macOS'
```

On any machine, from the repository root, the Swift lint (not a compiler; it catches a Mac-only API
in the iOS build, a table that will not compile, a payload that cannot decode the fixtures the
backend serves, and the like):

```bash
python3 scripts/check_swift_portability.py
```

---

## 7. Before you ship this to anyone else

Read [LEGAL.md](LEGAL.md) first — §2b in particular, which is new.

The short version: the **default** configuration is a private, non-commercial deployment, which
is what NBA.com's terms permit. What ends that is not a store submission; it is leaving the
private configuration. Since the web release that is two environment variables away, so it is
worth saying plainly what the steps are:

* **Your own machine**, on loopback: the default, and the case §2 of LEGAL.md is written about.
* **Your home LAN**: the server refuses to start until you accept cleartext session cookies,
  and you should read that flag as what it is. Keep `HARDWOOD_SIGNUP_MODE=invite`. Defensible
  for a household; not a security posture.
* **A public DNS name**: https, real proxy trust, and the licensing conversation *first*. You
  are also processing other people's personal data at that point — LEGAL.md §2c.

Distributing or monetizing means licensing a feed (Sportradar, SportsDataIO, API-NBA, or
balldontlie's paid tier) and swapping the ingest client — the pipeline is structured so that is
one new module against `normalize.py`, not a rewrite. [WEB.md](WEB.md) §9 is the operational
checklist for each of the three steps above.
