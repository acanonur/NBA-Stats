# Data sources and acquisition plan

This is the operational version of the source research document
("All-Time NBA Advanced-Stats App: Data Sources, Acquisition Plan & Architecture"). It says
where every number in Hardwood comes from, what it costs, and what it is legally allowed to do.

**The one-line strategy:** backfill history from bulk open datasets, keep current with a small
`nba_api` job. Never try to pull ~64,000 games one at a time from stats.nba.com — that is tens
of thousands of calls and days of runtime at safe rate limits.

---

## 1. The hybrid pipeline

```
   ┌─ ONE TIME ────────────────────────────────┐     ┌─ CONTINUOUS ──────────────────────┐
   │ Kaggle NBA Database (SQLite, 1946-)       │     │ nba_api, residential IP           │
   │ Sumitro Datta BBRef season CSVs (1947-)   │     │  · poll scoreboard during games   │
   │ shufinskiy nba_data PBP/shots (1996-)     │     │  · each game -> Final -> ingest    │
   │ hoopR release parquet (2002-)             │     │  · re-pull last 3 days nightly    │
   └───────────────┬───────────────────────────┘     └─────────────┬─────────────────────┘
                   │                                               │
                   └───────────────► serving database ◄────────────┘
                        (SQLite / Postgres — see docs/ARCHITECTURE.md)
                                        │
                                 Hardwood API  ──►  iOS app
```

Implemented in `backend/nbastats/ingest/`: `backfill.py` for the left arm, `daily.py` +
`runner.py` for the right arm, `aggregate.py` for everything derived.

---

## 2. Source catalogue

### A. NBA.com Stats via `nba_api` — free, unofficial, official data

`https://github.com/swar/nba_api` · MIT · Python 3.10+. This is the engine for incremental
updates. The endpoints that matter:

| Endpoint | Use |
| --- | --- |
| `LeagueGameLog` / `LeagueGameFinder` | Enumerate every `game_id` for a season and season type |
| `PlayerGameLogs` (plural) with `MeasureType='Advanced'` | **The efficient one** — one call per season returns every player's per-game advanced rows (ORtg, DRtg, NetRtg, AST%, TOV%, eFG%, TS%, USG%, Pace, PIE) |
| `BoxScoreAdvancedV3` | Per-game advanced box for a single `game_id` — used only for a just-finalized game |
| `BoxScoreTraditionalV3` | The basic box for that same game |
| `LeagueDashPlayerStats` | Season aggregates |
| `CommonAllPlayers` / `PlayerCareerStats` | Roster crosswalk and career totals |
| `PlayByPlayV3` / `ShotChartDetail` | Possession and shot granularity (optional, large) |

**Two operational facts that shape the whole deployment:**

1. **Datacenter IPs are silently blocked.** stats.nba.com sits behind Akamai bot protection and
   drops requests from AWS, GCP and Azure ranges — they hang rather than erroring. The ingest
   worker must run from a residential IP, a home box, or through a residential proxy
   (`NBA_API_PROXY`). GitHub Actions runners are usually on cloud ranges too. Only the database
   write step is safe to run in a cloud region.
2. **Browser headers are mandatory:** `User-Agent`, `Referer: https://www.nba.com/`,
   `x-nba-stats-origin: stats`, `x-nba-stats-token: true`, `Accept-Language`. `nba_api` bundles
   them; `backend/nbastats/ingest/client.py` lets you override them.

Rate limits are undocumented. Community consensus is roughly one request per 0.6–1.5s; the
client defaults to 1.0s with exponential backoff on timeouts.

### B. Basketball-Reference — free to read, **do not build on**

The richest historical source and the origin of PER, WS, BPM and VORP. But:

* 20 requests/minute, enforced, with about an hour's block for bots.
* The data-use policy explicitly says not to create websites or tools based on scraped data.
* Custom data requests start at $5,000.

Hardwood therefore **never scrapes Basketball-Reference**. Pre-1997 season advanced numbers
come from the Sumitro Datta Kaggle dump (a redistributed compilation) and are marked
`is_estimated`, with Basketball-Reference credited as the methodology source.

### C. Bulk open datasets — the backfill

| Dataset | Contents | License |
| --- | --- | --- |
| [Wyatt Walsh "NBA Database"](https://www.kaggle.com/datasets/wyattowalsh/basketball) | SQLite: 64,000+ games, 4,800+ players, box scores and play-by-play from 1946, daily-updated | CC BY-SA 4.0 (compilation) |
| [eoinamoore "Historical NBA Data"](https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores) | Box scores from 1947, advanced from 1997, nightly | Kaggle terms |
| [Sumitro Datta "NBA Stats (1947-present)"](https://www.kaggle.com/datasets/sumitrodatta/nba-aba-baa-stats) | Basketball-Reference season dumps keyed on the BBRef slug — **the only practical pre-1997 advanced source** | Kaggle terms |
| [shufinskiy/nba_data](https://github.com/shufinskiy/nba_data) | Play-by-play + shot detail 1996-, downloadable files; `nba_on_court` pulls a season in seconds | Repo terms |
| [hoopR / sportsdataverse](https://github.com/sportsdataverse/hoopR-nba-data) | Release parquet, ESPN-sourced box/PBP 2002- | Repo terms |

The CC license on a compilation does **not** override the NBA's rights over the underlying
data for commercial redistribution. Fine for personal and research use; see §4.

### D. Licensed feeds — required if this ships commercially

| Provider | Shape | Cost |
| --- | --- | --- |
| **Sportradar** | Official NBA data partner; live PBP, tracking-derived metrics, an SLA | Enterprise, reported ~$10,000+/month |
| **SportsDataIO** | US-focused, DFS salaries built in | Tiered commercial |
| **API-NBA** (api-sports.io) | Free 100 req/day; Pro $15/mo; Ultra $25/mo; Mega $35/mo | Budget-friendly; verify advanced depth |
| **balldontlie** | Free 5 req/min; ALL-STAR $9.99/mo; GOAT $39.99/mo unlocks box scores and PBP | Cheap and clean, but advanced stats only from 2015 and PBP only from 2025 — **not** a pre-2015 advanced source |
| **BigDataBall** | Cleaned CSV play-by-play with all 10 on-court players, 2002-03 onward | Per season |

`backend/nbastats/ingest/` is written so the upstream client is swappable: moving to a licensed
feed means writing one new module against `normalize.py`'s column maps, not rewriting the app.

### E. Impact metrics that are not on NBA.com

EPM (Dunks & Threes), DARKO, LEBRON (BBall-Index) and Cleaning the Glass are view-only or
subscription products with no bulk export — Hardwood does not redistribute them. RAPTOR is
frozen at its final 2023-era archive after FiveThirtyEight was shut down in March 2025.
BPM 2.0 and VORP are computed from the box score using the published formulas
(`backend/nbastats/metrics.py`), not copied from anyone's table.

---

## 3. Era coverage — what can and cannot exist

| Stat | First season | Note |
| --- | --- | --- |
| Basic box (FG, FT, PTS, PF, AST) | 1946-47 | BAA |
| Total rebounds | 1950-51 | No offensive/defensive split |
| Minutes played | 1951-52 | Enables per-minute metrics |
| ORB/DRB split, steals, blocks | 1973-74 | First season BPM/VORP are computable |
| Individual turnovers | 1977-78 | Enables USG% and TOV% |
| Three-point line | 1979-80 | |
| **Per-game advanced box, +/-, play-by-play, shot charts** | **1996-97** | The hard boundary |
| Player tracking (SportVU → Hawk-Eye 2023-24) | 2013-14 | Licensing-encumbered |
| Hustle stats | 2016-17 | Box outs added ~2019-20 |

Shot-location data from 1996-97 to 1999-00 is unreliable: Sports Reference flags 194,239 field
goal attempts in those years with no shot distance or coordinates.

**Consequence for the product:** for 1946-47 → 1995-96 Hardwood shows basic box scores plus
box-derived season estimates (PER, WS, and BPM/VORP from 1973-74). True per-game advanced
ratings *cannot* exist for those years — the possession data was never recorded. The schema
allows NULL for every era-unavailable column, stamps `data_source` and `is_estimated`, and the
API returns an `availability` of `unavailable` or `estimated` rather than a zero. The iOS app
renders an em dash and explains why. This is a correctness requirement, not a polish item.

---

## 4. Legal position

*Not legal advice.* See [LEGAL.md](LEGAL.md) for the full treatment.

* Raw statistics are facts and generally not copyrightable (US: *Feist*), but compilations,
  presentation and **terms of use** are enforceable.
* **NBA.com Terms of Use** permit NBA statistics only for "legitimate news reporting or private,
  non-commercial purposes", require prominent NBA.com attribution, and forbid use with
  sponsorship or commercial identification, gambling, fantasy games, real-time play-by-play
  depiction, or a database product.
* **Sports Reference** forbids building tools or websites on scraped data.

So: **this repository as configured is a private, non-commercial project.** Running it for
yourself is fine. Shipping it to the App Store, adding ads or a subscription, or wiring it to a
sportsbook is not — at that point you license a feed (Sportradar for real scale, API-NBA or
balldontlie GOAT for hobby scale) and stop relying on scraped stats.nba.com data in production.

The app carries the attribution string returned by `/v1/meta`:
*"Stats via NBA.com. Not endorsed by or affiliated with the NBA."*

---

## 5. Volume and cost

* ~64,000+ games all-time (a rolling regular-season + playoff figure from the Kaggle DB; there
  is no single official lifetime count). Modern seasons are 1,230 regular-season games.
* ~1.3–1.7 million player-game rows all-time; the advanced subset (1996-97+) is ~35,000 games.
* Full per-game backfill via `BoxScoreAdvancedV3` would be ~35,000 calls → 12+ hours at best,
  realistically days with backoff. **This is why the backfill reads bulk files** and why
  `PlayerGameLogs` (about 30 calls for every advanced log from 1996 to today) is the incremental
  fallback.
* Storage: the full player-game box (basic + advanced) is comfortably ~1–2 GB. Play-by-play is
  tens of GB — keep it optional, and Hardwood does.

## 6. Known pitfalls

* Cloud IP blocks (the number-one practical blocker).
* Missing required headers → silent failure.
* V2 (`UPPER_SNAKE_CASE`) vs V3 (`camelCase`) schema drift — handled in `normalize.py`.
* Endpoints deprecated without notice; pin `nba_api` and watch its releases.
* Post-hoc stat corrections — hence the three-day re-pull window.
* `PlayerGameLogs` `MeasureType` coverage varies by era.
