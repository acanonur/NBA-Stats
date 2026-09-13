# Hardwood — backend

The service behind the Hardwood iOS advanced-stats dashboard. It implements
[`contracts/CONTRACT.md`](../contracts/CONTRACT.md), which is the binding agreement between
this service and the app: REST surface, payload shapes, era rules and error codes.

Python 3.11, FastAPI, SQLAlchemy 2.0, SQLite by default. It runs **with no network access**:
`nbastats.seed` generates a complete, deterministic synthetic league, so a fresh checkout can
serve a full dashboard offline.

## Install

```bash
cd backend
python3 -m pip install -e ".[test,serve]"     # add ".[live]" for the nba_api ingest path
```

| Extra | Brings | Needed for |
| --- | --- | --- |
| `test` | pytest, pytest-asyncio | the test suite |
| `serve` | uvicorn | running the API |
| `live` | nba_api | the live ingest path **only** |

`nba_api` is an *optional* import behind a `try/except`, resolved at call time. Everything
else — the seeded league, the whole API, the entire test suite — runs without it, and a live
call without it raises a `IngestUnavailable` that says so rather than an `ImportError`
traceback. There is no BigQuery client: the original "partition by `game_date`, cluster by
`player_id`" plan is expressed here as plain composite indexes, which SQLite and Postgres both
understand.

## Seed, run, test

```bash
./scripts/serve_dev.sh --fresh          # seed a demo league and serve it on :8000
python3 -m pytest -q                    # the whole suite, no network
```

`serve_dev.sh` prints the curl commands for `/v1/health`, `/v1/meta` and a
`POST /v1/dashboard/resolve`. Or do it by hand:

```bash
python3 -m nbastats.seed --db sqlite:///./hardwood.db
export DATABASE_URL=sqlite:///./hardwood.db
python3 -m uvicorn nbastats.api.app:app --reload --port 8000
curl -s localhost:8000/v1/health | python3 -m json.tool
```

Seeder flags:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--db` | `DATABASE_URL`, else `sqlite:///./hardwood.db` | Any SQLAlchemy URL |
| `--as-of` | `2026-01-02` | "Today" for the in-progress season |
| `--seasons` | `1985-86,1992-93,2005-06,2024-25,2025-26` | Comma-separated |
| `--games-per-team` | `82` | Shrink the schedule for a quick database |
| `--players-per-team` | `10` | Rotation size |
| `--no-playoffs` | off | Regular season only |

The seeder is idempotent: it clears the tables first, so re-running replaces the league rather
than doubling it. A default run produces 30 real franchises with real NBA.com ids, ~1,140
fictional players, ~6,100 games (5,400 final), ~96,000 basic and ~56,000 advanced player game
lines, and every aggregate on top of them — about six seconds.

Two honest caveats about the demo data. Players do not change teams mid-season (`player_season`
still keys on `team_id`, so a traded player is representable), and because the default season
list samples five seasons across three disjoint eras, most careers touch only one or two of
them — pass `--seasons` with consecutive seasons if you want a dense `career_arc`.

### Tests

```bash
python3 -m pytest -q
```

| File | Covers |
| --- | --- |
| `tests/test_metrics.py` | every advanced formula, against worked examples |
| `tests/test_models.py` | schema, metric→column maps, era column sets |
| `tests/test_seed.py` | the synthetic league reconciles: player lines sum to team totals |
| `tests/test_api_core.py` | the HTTP surface, key names, the error envelope |
| `tests/test_widgets.py` | widget payloads and the dashboard resolve |
| `tests/test_ingest.py` | normalize / client / daily / aggregate / backfill |

`tests/conftest.py` builds one small deterministic database for the whole session and exposes
it as `seeded_engine`, `seeded_db` (a `Session`) and `seed_summary`, plus `empty_engine` for
write-path tests. It covers a pre-advanced season, a completed season with playoffs, and an
in-progress season with scheduled games ahead of it.

**No test touches the network.** `test_ingest.py` replaces `socket.socket` with a function that
raises and drives the whole pipeline from recorded payloads in `tests/fixtures/nba_api/` — a
regression that reintroduces a live call fails loudly instead of hanging for sixty seconds.

## Getting real data in

Two arms, because the two problems are different (see [`docs/DATA_SOURCES.md`](../docs/DATA_SOURCES.md)).

### The live path — `scripts/ingest_daily.sh`

> **stats.nba.com silently blocks datacenter IP ranges.** AWS, GCP, Azure and most GitHub
> Actions runners get no error and no 403 from the Akamai layer in front of the API: the
> request simply hangs until it times out. No amount of retrying fixes it, and on a cloud host
> that timeout is the *only* symptom you will see.
>
> Run the ingest worker from a **residential connection** — a home box, a Raspberry Pi, a
> residential-IP VPS — or set `NBA_API_PROXY` to a residential pass-through proxy. Only the
> database and the read API are safe to host in a cloud region.
>
> Browser headers are mandatory too. Without `User-Agent`, `Referer: https://www.nba.com/`,
> `x-nba-stats-origin`, `x-nba-stats-token` and `Accept-Language`, responses come back empty
> rather than failing loudly. `client.DEFAULT_HEADERS` restates the working set so it can be
> overridden per deployment when upstream changes.

Stats arrive **as each game finishes**, not overnight (`CONTRACT.md` §8):

1. `poll_finalized_games()` reads the scoreboard for a date — one request for the whole slate —
   writes each game's live state, and returns the ids that *just* flipped to Final.
2. `ingest_game()` pulls that one game's traditional and advanced box (two requests), writes
   the player, team and game rows, recomputes the affected season, and bumps `sync_version`
   **exactly once**. A client polling `/v1/sync` sees the game within a poll interval of the
   final buzzer.
3. `ingest_day()` is the bulk form: one `LeagueGameLog` plus one `PlayerGameLogs` per measure
   type covers an entire slate in three requests, however many games it holds.
4. The nightly `scripts/ingest_daily.sh` re-pulls the last `CORRECTION_WINDOW_DAYS` days,
   because the league revises box scores after the fact. Every write is an upsert, so a
   correction overwrites yesterday instead of duplicating it.

```bash
./scripts/ingest_daily.sh --days 3
# cron, 04:15 local:
# 15 4 * * * cd /srv/hardwood/backend && ./scripts/ingest_daily.sh >> /var/log/hardwood/ingest.log 2>&1
```

Set `HARDWOOD_INGEST_FIXTURES` to a directory of recorded payloads and the same script runs
against files instead of the network — which is exactly how the pipeline is tested.

`sync_version` and `data_through` answer different questions and are maintained separately:
`sync_version` increments per finalized game, while `data_through` only moves to a date once
**every** game on that slate is final. A half-finished night must never look complete.

### The history path — `scripts/backfill.sh`

**History is never pulled from the API.** ~64,000 games and 1.3-1.7M player-game rows at one
request per second is tens of thousands of requests and days of runtime, and it would be rude
besides. The open bulk datasets already hold that history in a handful of files:

```bash
./scripts/backfill.sh --kaggle nba.sqlite        # Wyatt Walsh's NBA Database (SQLite)
./scripts/backfill.sh --bbref ./bbref-csv        # Basketball-Reference season dumps (CSV)
./scripts/backfill.sh --parquet ./hoopr          # hoopR / shufinskiy release files
./scripts/backfill.sh --reconcile                # rebuild id_crosswalk only
```

Every loader is chunked (a 64,000-game file never lands in memory), resumable (each finished
season is recorded in `ingest_log` and skipped on a re-run) and idempotent. None of them ever
invents a player: a source row that matches nobody is reported, not guessed at.

The Basketball-Reference dumps are the only practical source of pre-1997 PER, Win Shares, BPM
and VORP, and every row lands with `is_estimated = true`. Hardwood never scrapes
Basketball-Reference itself — their terms forbid it and their rate limit is enforced with
hour-long blocks; these loaders read the redistributed compilations. See
[`docs/LEGAL.md`](../docs/LEGAL.md).

## Configuration

Every variable, read once by `nbastats.config.Settings.from_env()`:

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./hardwood.db` | A Postgres URL works with no code change |
| `HARDWOOD_API_KEY` | unset | When set, `/v1` requires `X-API-Key` (except `/v1/health`) |
| `HARDWOOD_DEMO_MODE` | `false` | Seeds an empty store on startup and advertises it in `/v1/health` |
| `HARDWOOD_CONTRACTS_DIR` | repo `contracts/` | Where `metrics.json` et al. live |
| `NBA_API_PROXY` | unset | Residential pass-through proxy for the live ingest path |
| `INGEST_POLL_SECONDS` | `60` | Scoreboard poll cadence during the game window |
| `CURRENT_SEASON` | `2025-26` | The season `"latest"` resolves to. The default matches the demo data; a live deployment sets it, or uses `config.current_season_string()`, which rolls over on 1 July |
| `LOG_LEVEL` | `INFO` | |
| `CORRECTION_WINDOW_DAYS` | `3` | Days of past games re-pulled nightly for stat corrections |

Plus one that belongs to the ingest client rather than to `Settings`:

| Variable | Notes |
| --- | --- |
| `HARDWOOD_INGEST_FIXTURES` | A directory of recorded JSON payloads. Set it and `StatsClient` reads files instead of the network — no `nba_api` needed, no requests made |

`get_settings()` is memoised; tests that mutate the environment call `config.reset_settings_cache()`.

## Module map

| Module | Responsibility |
| --- | --- |
| `nbastats/config.py` | `Settings` read from the environment, `get_settings()`, season-rollover helpers |
| `nbastats/catalog.py` | Loads `contracts/*.json`; formatting, widget-config validation, era rules. **stdlib only** — no fastapi, no sqlalchemy |
| `nbastats/models.py` | SQLAlchemy 2.0 tables, metric→column maps, `render_schema_sql()` |
| `nbastats/schema.sql` | Generated DDL (SQLite dialect). Regenerate with `python3 -m nbastats.models > nbastats/schema.sql` |
| `nbastats/db.py` | Engine, session factory, `get_session()` dependency, `init_db()`, sync-state helpers |
| `nbastats/metrics.py` | Every advanced formula, and `compute_metric()` |
| `nbastats/percentiles.py` | `rank_and_percentile()`, `percentile_of()`, `summarize()` |
| `nbastats/seed.py` | The deterministic synthetic league |
| **`nbastats/ingest/`** | |
| `ingest/client.py` | Polite stats.nba.com client: optional `nba_api`, recorded-fixture mode, rate limiting, backoff |
| `ingest/normalize.py` | The V2 (`UPPER_SNAKE_CASE`) / V3 (`camelCase`) translation layer |
| `ingest/daily.py` | poll → ingest one game → bump `sync_version` → re-aggregate; the correction window |
| `ingest/backfill.py` | Bulk-file loaders (Kaggle, Basketball-Reference, hoopR) and the id crosswalk |
| `ingest/aggregate.py` | Everything derived: season rows, team seasons, league distributions, shot zones |
| **`nbastats/api/`** | `app.py` (application factory), `deps.py`, `errors.py`, `schemas.py`, `serializers.py`, and the `routes_*` modules |
| **`nbastats/widgets/`** | One module per widget kind, plus `base.py` and `queries.py` |
| **`scripts/`** | `ingest_daily.sh`, `backfill.sh`, `serve_dev.sh` |

`app.py` includes `routes_dashboard` and `routes_leaders` **if present**: a service that can
serve players, teams, games and sync is useful on its own, and an absent widget layer degrades
the dashboard route rather than the process.

## Conventions worth knowing before you write a query

**Percentages are fractions in `[0, 1]`.** Everywhere in the data layer, and in
`MetricValue.value`. `catalog.format_metric("ts_pct", 0.6153)` is what turns that into
`"61.5%"` for `displayValue`.

**JSON keys are lowerCamelCase; database columns are snake_case.** The translation happens in
`api/serializers.py` and nowhere else.

**Metric key → column.** Never hard-code a column name. `models.season_column_for(key,
subject_type, per_mode)` resolves it, and the `*_METRIC_COLUMNS` dicts are the full maps.
Per-game columns are unsuffixed (`pts`), totals carry `_tot` (`pts_tot`), minutes are the
exception (`min_pg` per game, `minutes` total). `Per36`/`Per100` are derived by the API from
the totals plus minutes or possessions rather than stored twice.

**`league_season` is keyed `(subject_type, season, season_type, metric_key)`.** The
`subject_type` discriminator is part of the key on purpose: a player's offensive-rating
distribution is not a team's, and serving one for the other would silently corrupt every
percentile bar. Always filter on it.

**Timestamps are naive UTC** in the database and render with a `Z`. Dates are the NBA
scheduling day in US Eastern.

**Writes are upserts.** `aggregate.upsert()` is the pipeline's only write primitive, and it
returns whether anything actually changed — the signal `ingest_game()` uses to decide whether a
game warrants a `sync_version` bump. A timestamp of the pass (`ingested_at`) is always written
*outside* that comparison, or every re-run would look like a rewrite.

## Era honesty

This is a correctness requirement, not a nicety. A stat that did not exist in an era is `NULL`
with availability `"unavailable"` — never `0`.

The rules live in one place, `contracts/metrics.json`, and are read by
`catalog.metric_availability(metric_key, season, granularity)`, which returns `full`,
`estimated`, `partial` or `unavailable`. Neither `seed.py` nor the ingest path restates them:
both compute every value and then null whatever the catalog says did not exist
(`aggregate.era_plan()` / `aggregate.apply_era()`), so the data and the API agree by
construction.

* Per-game advanced box scores, plus/minus, play-by-play and shot charts begin in **1996-97**.
  Earlier seasons have **no** `player_game_advanced` rows at all, and no per-game ratings,
  pace, possessions or plus/minus anywhere. `daily.ingest_game()` does not even call the
  advanced box-score endpoint for them.
* Pre-1996-97 *season* advanced values (USG%, AST%, rebound rates, PER, WS, BPM, VORP) are
  written with `is_estimated = True`: they are box-score derivations, not measurements.
* Earlier boundaries are in the catalog too — rebounds from 1950-51, minutes 1951-52,
  steals/blocks/OREB-DREB 1973-74, turnovers 1977-78, the three-point line 1979-80.
* `partial` is what a span of seasons straddling a boundary gets; pass a list of seasons or
  `"career"` to `metric_availability`, or fold values with `catalog.combine_availability()`.

Two wrinkles worth knowing, both stemming from the same fact — `player_game_advanced` does not
exist before 1996-97:

* `efg_pct`, `ts_pct` and `ast_tov` are pure box-score formulas, and the catalog marks them
  `full` at **season** granularity from the era that recorded their inputs — but only from
  1996-97 **per game**, because that is where the stored per-game value would live. The ingest
  path writes `NULL` for them on an older game rather than second-guessing the catalog.
* `game_score` goes the other way: the catalog marks it available per game from 1973-74, yet
  the column that holds it is on `player_game_advanced`. For an older game the API computes it
  from `player_game_basic` instead of reading a column — see `serializers._DERIVABLE_FROM_BOX`.

Either way the answer the API gives matches what `/v1/meta.coverage` and
`metric_availability()` promise, which is the only thing that has to be true.

## How the synthetic league is built

`random.Random(20260912)`, start to finish — the same seed gives the same league every time,
which is what lets the tests assert exact reconciliation.

1. Each team-season gets a roster of archetypes (rim-running big, 3-and-D wing, high-usage
   guard, stretch four, bench playmaker), aged along a curve peaking at 27. Team strength is
   centred on its own season's league, so the average team scores the era's average.
2. A round-robin schedule spreads over a real calendar, with home court and no team playing
   twice on a day.
3. A game's final score comes from team strength, home court and noise. The team's shooting
   line is then solved so that `2*FGM + 3PM + FTM` equals that score **exactly**, and the
   totals are apportioned to players by largest-remainder allocation under per-player caps.
   That is why every team total is the sum of its players' lines, and the team's points are the
   final score — both asserted in `tests/test_seed.py`.
4. Possessions use Oliver's estimate *averaged over the two teams* (they necessarily play the
   same number), which keeps `off_rtg - def_rtg` equal to the margin per 100.
5. Season ratings are normalised the way the real metrics are: PER to a minute-weighted league
   average of exactly 15.00, BPM to 0.0, and each team's Win Shares to that team's wins.

The result tracks the real league closely enough to be believable — 2024-25 comes out around
113 points at a pace near 100 with 37 three-point attempts, 1985-86 around 110 points at a pace
of 102 with three.

## The recorded ingest corpus

`tests/fixtures/nba_api/` holds small, structurally faithful recordings of the upstream
envelopes — V2's `resultSets`/`headers`/`rowSet` and V3's nested camelCase objects — for one
slate, `2026-01-02`:

| File | Contents |
| --- | --- |
| `scoreboardv2__2026-01-02.json` | Two games: `0022500512` final, `0022500513` live |
| `scoreboardv2__2026-01-02__complete.json` | The same slate with both games final |
| `scoreboardv2.json` | Empty slate — the endpoint-wide fallback for any other date |
| `boxscore{traditional,advanced}v3__00225005{12,13}.json` | The two games' V3 box scores |
| `boxscoretraditionalv3__0029500012.json` | A 1995-96 game: no advanced box exists, and none may be invented |
| `leaguegamelog__…`, `playergamelogs__…` | The V2 bulk endpoints for the same slate |
| `commonallplayers__2025-26__all.json` | The roster behind the id crosswalk |
| `*__corrected.json` | The same payloads with one assist moved off a turnover, three days later — a real post-hoc stat correction |

The corpus is arithmetically closed: team totals are the sum of the player lines,
`PTS == 2*FGM + 3PM + FTM`, and the V2 and V3 payloads describe the same games with the same
numbers. That is what makes the V2↔V3 cross-check in `test_ingest.py` a real test of the
translation layer rather than a tautology. `StatsClient` resolves a call to a file name
(`<endpoint>__<part>__<part>.json`, falling back to `<endpoint>.json`), so a correction is
modelled the way it actually reaches a worker: the same call returns different bytes the next
night.
