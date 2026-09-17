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

Then open `ios/NBAStats.xcodeproj` in Xcode 16+, run on a simulator. The app points at
`http://localhost:8000/v1` by default (`HardwoodAPIBaseURL` in `ios/Info.plist`, overridable in
Settings). With no server running it falls back to the bundled fixtures and is still fully
browsable.

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

### What this gives you, and what it does not

* **Real players, real teams, real statistics** for every game on those dates. Search finds them
  because `ensure_player` writes a row for everyone who appears in a box score.
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
| `HARDWOOD_API_KEY` | unset | When set, every `/v1` route except `/v1/health` requires `X-API-Key` |
| `HARDWOOD_DEMO_MODE` | `0` | Seed synthetic data at startup if the database is empty |
| `HARDWOOD_CONTRACTS_DIR` | repo `contracts/` | Override the catalog location |
| `NBA_API_PROXY` | unset | Residential proxy for the ingest client |
| `INGEST_POLL_SECONDS` | `300` | Scoreboard poll interval during the game window |
| `CORRECTION_WINDOW_DAYS` | `3` | How many past days the nightly pass re-pulls |
| `CURRENT_SEASON` | derived | Override the season the literal `"latest"` resolves to |
| `HARDWOOD_INGEST_FIXTURES` | unset | Read recorded JSON instead of calling the network (tests) |
| `LOG_LEVEL` | `INFO` | |

---

## 5. Operating checks

```bash
curl -s localhost:8000/v1/health          # databaseReady, syncVersion, dataThrough
curl -s 'localhost:8000/v1/sync?since=0'  # what changed, and which widget kinds to invalidate
sqlite3 hardwood.db 'select * from sync_state;'
sqlite3 hardwood.db 'select job, status, started_at, games_written, error from ingest_log order by id desc limit 10;'
```

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Ingest requests hang forever, no error | Datacenter IP block | Move the worker to a residential IP or set `NBA_API_PROXY` |
| `403`/empty responses from stats.nba.com | Missing browser headers | Check `ingest/client.py` headers; pin a known-good `nba_api` |
| `sync_version` climbing but `dataThrough` stuck | Some game on the slate is not Final | Expected — `dataThrough` only advances on a complete slate |
| A stat shows an em dash for an old season | Working as designed | That stat did not exist then; see [DATA_SOURCES.md](DATA_SOURCES.md) §3 |
| A widget tile shows an error but the rest render | Per-widget failure isolation | Read `requestId` in the result and grep the server log |
| Numbers changed for a game played two days ago | League stat correction | Expected — the three-day re-pull window exists for this |
| `check_contracts.py` fails after editing a catalog | Generated files not regenerated, or the app's bundled copies drifted | Re-run the `contracts/tools/gen_*.py` generator, then `scripts/sync_contracts.sh` |
| `git pull` aborts with "local changes to ios/NBAStats.xcodeproj/project.pbxproj" | Xcode rewrites the project file on its own — opening the project is enough | Quit Xcode, then `git checkout -- ios/NBAStats.xcodeproj/project.pbxproj` and pull again. That edit is Xcode's bookkeeping, not your work. **Check `git log --oneline -1` before concluding a fix did not work**: a silently aborted pull looks exactly like a failed fix. |
| Search finds no current players (Doncic, LeBron, Jokic) | The app is in demo mode, whose search index is harvested from the bundled widget fixtures — the couple of dozen players on the sample dashboard, not a roster. The seeded demo *database* does contain them; demo mode just never opens it | Start the service (`§1`), then Settings → Data source → turn off "Use bundled demo data". The seeded league has 600+ real identities including Doncic, on synthetic teams with generated numbers |
| Xcode still reports a build error a fix was supposed to clear | The pull may not have landed, or DerivedData is stale | `git log --oneline -1` first, then `rm -rf ~/Library/Developer/Xcode/DerivedData/NBAStats-*` and Clean Build Folder |

---

## 6. Tests

```bash
cd backend && python3 -m pytest -q          # backend suite
python3 scripts/check_contracts.py          # contract drift guard (from the repo root)
python3 -m nbastats.fixtures_export --out ../contracts/fixtures && ./scripts/sync_contracts.sh
```

iOS, on a Mac with Xcode 16+:

```bash
cd ios
xcodebuild test -scheme Hardwood -destination 'platform=iOS Simulator,name=iPhone 16'
```

---

## 7. Before you ship this to anyone else

Read [LEGAL.md](LEGAL.md) first. The short version: this configuration is a private,
non-commercial deployment, which is what NBA.com's terms permit. Distributing or monetizing the
app means licensing a feed (Sportradar, SportsDataIO, API-NBA, or balldontlie's paid tier) and
swapping the ingest client — the pipeline is structured so that is one new module against
`normalize.py`, not a rewrite.
