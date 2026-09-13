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
`http://localhost:8000/v1` by default (`HardwoodAPIBaseURL` in `Info.plist`, overridable in
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
