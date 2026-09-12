# Hardwood — backend

The service behind the Hardwood iOS advanced-stats dashboard. It implements
[`contracts/CONTRACT.md`](../contracts/CONTRACT.md), which is the binding agreement between
this service and the app: REST surface, payload shapes, era rules and error codes.

Python 3.11, FastAPI, SQLAlchemy 2.0, SQLite by default. It runs **with no network access**:
`nbastats.seed` generates a complete, deterministic synthetic league so the app is fully
demoable offline.

## Install

```bash
cd backend
python3 -m pip install -e ".[test]"       # add ".[live]" for the nba_api ingest path
```

`nba_api` is an *optional* import used only by the live ingest path, behind `try/except`.
Nothing else needs it. There is no BigQuery client: the original BigQuery
"partition by `game_date`, cluster by `player_id`" plan is expressed here as plain composite
indexes, which SQLite and Postgres both understand.

## Seed the demo league

```bash
python3 -m nbastats.seed --db sqlite:///./hardwood.db
```

About six seconds for the default set. Useful flags:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--db` | `DATABASE_URL`, else `sqlite:///./hardwood.db` | Any SQLAlchemy URL |
| `--as-of` | `2026-01-02` | "Today" for the in-progress season |
| `--seasons` | `1985-86,1992-93,2005-06,2024-25,2025-26` | Comma-separated |
| `--games-per-team` | `82` | Shrink the schedule for a quick database |
| `--players-per-team` | `10` | Rotation size |
| `--no-playoffs` | off | Regular season only |

The seeder is idempotent: it clears the tables first, so re-running replaces the league
rather than doubling it.

What the default run produces:

| | |
| --- | --- |
| Teams | 30 real franchises, real NBA.com ids, correct conference/division |
| Players | ~1,140 fictional players with archetypes, biographies and id crosswalk rows |
| Games | ~6,100 (5,400 final, 700 scheduled ahead of the as-of date) |
| Player game lines | ~96,000 basic, ~56,000 advanced (1996-97 onward only) |
| Aggregates | player seasons, team seasons, league distributions, shot zones |

Seasons are `1985-86` and `1992-93` (pre-advanced era), `2005-06`, `2024-25` complete with
play-in and playoffs, and `2025-26` in progress through the as-of date. Historical seasons
use only the franchises that existed: 23 teams in 1985-86, 27 in 1992-93.

Two honest caveats about the demo data. Players do not change teams mid-season
(`player_season` still keys on `team_id`, so a traded player is representable), and because
the default season list samples five seasons across three disjoint eras, most careers touch
only one or two of them — pass `--seasons` with consecutive seasons if you want a dense
`career_arc`.

## Run

```bash
export DATABASE_URL=sqlite:///./hardwood.db
python3 -m uvicorn nbastats.app:app --reload --port 8000   # see the service module
curl localhost:8000/v1/health
```

## Test

```bash
cd backend && python3 -m pytest
```

`tests/conftest.py` builds one small deterministic database for the whole session and
exposes it as `seeded_engine`, `seeded_db` (a `Session`) and `seed_summary`. It covers a
pre-advanced season, a completed season with playoffs, and an in-progress season with
scheduled games ahead of it. While those fixtures are alive `DATABASE_URL` points at the
seeded file, so anything built through `nbastats.db.get_engine()` sees the same data.

## Layout

| Module | Responsibility |
| --- | --- |
| `nbastats/config.py` | `Settings` read from the environment, `get_settings()`, season-rollover helpers |
| `nbastats/catalog.py` | Loads `contracts/*.json`; formatting, widget-config validation, era rules. **stdlib only** — no fastapi, no sqlalchemy |
| `nbastats/models.py` | SQLAlchemy 2.0 tables, metric→column maps, `render_schema_sql()` |
| `nbastats/schema.sql` | Generated DDL (SQLite dialect). Regenerate with `python3 -m nbastats.models > nbastats/schema.sql` |
| `nbastats/db.py` | Engine, session factory, `get_session()` dependency, `init_db()`, sync-state helpers |
| `nbastats/seed.py` | The deterministic synthetic league |

### Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./hardwood.db` | A Postgres URL works with no code change |
| `HARDWOOD_API_KEY` | unset | When set, `/v1` requires `X-API-Key` (except `/v1/health`) |
| `HARDWOOD_DEMO_MODE` | `false` | Advertises the seeded league in `/v1/health` |
| `HARDWOOD_CONTRACTS_DIR` | repo `contracts/` | Where `metrics.json` et al. live |
| `NBA_API_PROXY` | unset | Proxy for the live ingest path |
| `INGEST_POLL_SECONDS` | `60` | Scoreboard poll cadence during the game window |
| `CURRENT_SEASON` | `2025-26` | The season `"latest"` resolves to. The default matches the demo data; a live deployment sets it, or uses `config.current_season_string()`, which rolls over on 1 July |
| `LOG_LEVEL` | `INFO` | |
| `CORRECTION_WINDOW_DAYS` | `3` | Days of past games re-pulled nightly for stat corrections |

## Conventions worth knowing before you write a query

**Percentages are fractions in `[0, 1]`.** Everywhere in the data layer, and in
`MetricValue.value`. `catalog.format_metric("ts_pct", 0.6153)` is what turns that into
`"61.5%"` for `displayValue`.

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

## Era honesty

This is a correctness requirement, not a nicety. A stat that did not exist in an era is
`NULL` with availability `"unavailable"` — never `0`.

The rules live in one place, `contracts/metrics.json`, and are read by
`catalog.metric_availability(metric_key, season, granularity)`, which returns `full`,
`estimated`, `partial` or `unavailable`. `seed.py` does not restate them: it computes every
value and then nulls whatever the catalog says did not exist, so the data and the API agree
by construction.

* Per-game advanced box scores, plus/minus, play-by-play and shot charts begin in
  **1996-97**. Earlier seasons have **no** `player_game_advanced` rows at all, and no
  per-game ratings, pace, possessions or plus/minus anywhere.
* Pre-1996-97 *season* advanced values (USG%, AST%, rebound rates, PER, WS, BPM, VORP) are
  written with `is_estimated = True`: they are box-score derivations, not measurements.
* Earlier boundaries are in the catalog too — rebounds from 1950-51, minutes 1951-52,
  steals/blocks/OREB-DREB 1973-74, turnovers 1977-78, the three-point line 1979-80.
* `partial` is what a span of seasons that straddles a boundary gets; pass a list of seasons
  or `"career"` to `metric_availability`, or fold values with
  `catalog.combine_availability()`.

One wrinkle worth knowing: `game_score` and `ast_tov` are pure box-score formulas that the
catalog marks available from 1973-74 and 1977-78, but they physically live in
`player_game_advanced`, which starts in 1996-97. For an older game the API computes them
from `player_game_basic` rather than reading a column.

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
   That is why every team total is the sum of its players' lines, and the team's points are
   the final score — both asserted in `tests/test_seed.py`.
4. Possessions use Oliver's estimate *averaged over the two teams* (they necessarily play
   the same number), which keeps `off_rtg - def_rtg` equal to the margin per 100.
5. Season ratings are normalised the way the real metrics are: PER to a minute-weighted
   league average of exactly 15.00, BPM to 0.0, and each team's Win Shares to that team's
   wins.

The result tracks the real league closely enough to be believable — 2024-25 comes out around
113 points at a pace near 100 with 37 three-point attempts, 1985-86 around 110 points at a
pace of 102 with three.
