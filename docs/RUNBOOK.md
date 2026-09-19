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
| `HARDWOOD_API_KEY` | unset | When set, every `/v1` route except `/v1/health` requires `X-API-Key` — **or** a valid web session cookie, so the SPA keeps working |
| `HARDWOOD_DEMO_MODE` | `0` | Seed synthetic data at startup if the database is empty |
| `HARDWOOD_CONTRACTS_DIR` | repo `contracts/` | Override the catalog location |
| `NBA_API_PROXY` | unset | Residential proxy for the ingest client |
| `INGEST_POLL_SECONDS` | `60` | Scoreboard poll interval during the game window |
| `CORRECTION_WINDOW_DAYS` | `3` | How many past days the nightly pass re-pulls |
| `CURRENT_SEASON` | derived | Override the season the literal `"latest"` resolves to |
| `HARDWOOD_INGEST_FIXTURES` | unset | Read recorded JSON instead of calling the network (tests) |
| `LOG_LEVEL` | `INFO` | |
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
| `check_contracts.py` fails after editing a catalog | Generated files not regenerated, or the app's bundled copies drifted | Re-run the `contracts/tools/gen_*.py` generator, then `scripts/sync_contracts.sh` |
| `git pull` aborts with "local changes to ios/NBAStats.xcodeproj/project.pbxproj" | Xcode rewrites the project file on its own — opening the project is enough | Quit Xcode, then `git checkout -- ios/NBAStats.xcodeproj/project.pbxproj` and pull again. That edit is Xcode's bookkeeping, not your work. **Check `git log --oneline -1` before concluding a fix did not work**: a silently aborted pull looks exactly like a failed fix. |
| Search finds no current players (Doncic, LeBron, Jokic) | The app is in demo mode, whose search index is harvested from the bundled widget fixtures — the couple of dozen players on the sample dashboard, not a roster. The seeded demo *database* does contain them; demo mode just never opens it | Start the service (`§1`), then Settings → Data source → turn off "Use bundled demo data". The seeded league has 600+ real identities including Doncic, on synthetic teams with generated numbers |
| Xcode still reports a build error a fix was supposed to clear | The pull may not have landed, or DerivedData is stale | `git log --oneline -1` first, then `rm -rf ~/Library/Developer/Xcode/DerivedData/NBAStats-*` and Clean Build Folder |
| `/` returns 404 but `/v1/health` is fine | `web/dist/index.html` is missing, so `mount_web` mounted nothing and logged a warning | `./scripts/web.sh build`. If Node is missing it says so in a sentence |
| `web.sh doctor` shows a provider as enabled, the site shows no button | `doctor` reads `backend/.env`; the server does not | `set -a; . backend/.env; set +a` before starting, then re-check `GET /v1/auth/methods` |
| The server exits immediately with a `RuntimeError` naming a `HARDWOOD_*` variable | A startup refusal — cleartext cookies off loopback, open signup with no backstop, a group-readable Apple `.p8`, or a cleartext `smtp://` URL | The message names the variable and the fix. [WEB.md](WEB.md) §3 |
| Google sign-in fails at the callback with `invalid_client` | The client ID and secret do not belong together. Whitespace is not the cause — every value is stripped as it is read | Re-copy both from the Credentials page. A *redirect URI* mismatch is Google's own distinct error |
| Everyone shares one rate-limit bucket behind a proxy | `HARDWOOD_TRUSTED_PROXY_CIDRS` is empty, so `X-Forwarded-For` is stripped from every request | Set it and `HARDWOOD_TRUSTED_PROXY_HOPS`. `/v1/health.authWarnings` warns about exactly this |

---

## 6. Tests

```bash
cd backend && python3 -m pytest -q          # backend suite — 888 tests
python3 scripts/check_contracts.py          # contract drift guard, 12 checks (from the repo root)
cd web && npm test && npm run build         # 502 tests; CI then fails on any diff in web/dist
python3 -m nbastats.fixtures_export --out ../contracts/fixtures && ./scripts/sync_contracts.sh
```

iOS, on a Mac with Xcode 16+:

```bash
cd ios
xcodebuild test -scheme Hardwood -destination 'platform=iOS Simulator,name=iPhone 16'
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
