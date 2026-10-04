# Architecture

Hardwood is three clients and one service, joined by one contract.

```
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  contracts/                                                                  │
 │    metrics.json · widgets.json · presets.json · theme.json · fixtures/       │
 │    the single source of truth, consumed by ALL THREE, drift-checked in CI    │
 └───────────────┬──────────────────────┬───────────────────┬───────────────────┘
                 │                      │                   │
   ┌─────────────▼──────────────┐   ┌───▼───────────────┐   │
   │  backend/  (Python)        │   │  ios/  (SwiftUI)  │   │
   │                            │◄─►│  dashboard ·      │   │
   │  ingest → store → API      │   │  widgets · sync   │   │
   │            │               │   └───────────────────┘   │
   │            └── serves ──┐  │                           │
   └────────────────────────┼──┘   ┌───────────────────────▼┐
                            └─────►│  web/  (React 19 + TS) │
                    one origin,    │  the same dashboards,  │
                    SPA at /,      │  design system scraped │
                    API at /v1     │  from Theme.swift      │
                                   └────────────────────────┘
```

---

## 1. Why the contract is a separate top-level directory

Three codebases in three languages have to agree on 61 metric definitions, 16 widget payload
shapes and 12 preset dashboards. Encoding that agreement in prose alone guarantees drift, so it
is encoded in data instead:

* `contracts/metrics.json` — every metric's name, display format, direction, era availability
  and glossary line. The backend formats values with it; the app formats values with it; neither
  hard-codes a percentage or a decimal place.
* `contracts/widgets.json` — the 16 widget kinds and, for each, a typed schema of its
  configuration fields. **Both configuration editors are generated from this file** — the iOS
  one at runtime, the web one from `generated/contracts.ts` — which is why adding a widget
  option does not require an app release.
* `contracts/presets.json` — the 12 shipped dashboards, pre-validated against both catalogs by
  their generator.
* `contracts/theme.json` — the design tokens, scraped from `ios/NBAStats/DesignSystem/Theme.swift`
  so the web client cannot drift from the iOS one by hand. See §4.
* `contracts/fixtures/*.json` — golden response payloads produced by running the real backend.
  Backend tests assert they regenerate byte-identically; iOS tests decode them; the app replays
  them in demo mode.

`scripts/check_contracts.py` fails CI when any of these fall out of sync — including the copies
bundled into the app under `ios/NBAStats/Resources/Contracts/` and the files generated into
`web/src/generated/`.

---

## 2. Backend

```
backend/nbastats/
├── config.py            environment-driven settings
├── catalog.py           loads contracts/*.json; formatting + era availability rules
├── models.py            SQLAlchemy 2.0 schema
├── db.py                engine, session, sync_state
├── metrics.py           every advanced formula (TS%, USG%, ORtg/DRtg, PER inputs, VORP…)
├── percentiles.py       ranks, percentiles, league distributions
├── seed.py              deterministic synthetic league — the app runs with zero network
├── ingest/
│   ├── client.py        polite stats.nba.com client (backoff, headers, proxy, fixture mode)
│   ├── normalize.py     V2 UPPER_SNAKE ↔ V3 camelCase → our snake_case
│   ├── daily.py         per-game finalization + bulk day pull + correction window
│   ├── backfill.py      Kaggle SQLite / BBRef CSV / parquet loaders + id reconciliation
│   ├── aggregate.py     season, team and league-distribution rollups (idempotent)
│   └── runner.py        the scheduler process
├── widgets/             one resolver per widget kind + the registry
├── api/                 FastAPI: routes, pydantic schemas, serializers, errors
├── shared/              pure, stdlib-only core shared by both leagues (§6)
├── intel/               shared fetch plumbing: polite HTTP client, feeds, robots.txt, recordings
├── nba_intel/           NBA injury report, headlines, model settings, job state
├── nba_matchup/         NBA read side: matchup, defence by position, team projections
├── euroleague/          the sealed EuroLeague package: its own store, importer, ingest, model, routes
└── worker.py            the one scheduler process for every job the two leagues add
```

### Storage choice

SQLAlchemy 2.0 over **SQLite by default**, Postgres by changing `DATABASE_URL` and nothing else.

The source research recommends BigQuery as the warehouse and a separate serving layer, because
BigQuery's per-query latency and cost are wrong for an app making many small lookups. Hardwood
implements the **serving layer**. If you already have the BigQuery warehouse described in the
research, `ingest/backfill.py` is where you export from it into this store; the app never talks
to BigQuery directly.

Indexes mirror the recommended BigQuery partition-and-cluster plan: `(game_date)`,
`(player_id, season, season_type)`, `(season, season_type)`.

Era-unavailable columns are **NULL, never 0**, and every row carries `data_source`; derived
pre-1997 advanced rows carry `is_estimated`.

### The API's one unusual endpoint

`POST /v1/dashboard/resolve` renders an entire dashboard in a single round trip: the client
sends the widget list with each widget's configuration, the server resolves each one
independently and returns a result array. This matters because:

* a 12-widget dashboard is one request, not twelve;
* one widget failing produces one `status: "error"` tile, not a broken screen;
* `knownSyncVersion` lets the server answer `"unchanged"` for tiles whose data has not moved,
  so a refresh after one game finishes re-sends almost nothing;
* per-result `ttlSeconds` drives the client's cache without the client knowing the domain.

### Freshness: "stats arrive as each game finishes"

1. `runner.py` polls the scoreboard during the game window.
2. A game flipping to Final triggers `ingest_game()` for that single `game_id` — the traditional
   and advanced boxes for one game.
3. `sync_state.sync_version` increments **once per finalized game**. `data_through` advances
   only when the whole slate is final.
4. Affected season and league aggregates are recomputed.
5. Every night the last three days are re-pulled, because the league issues stat corrections
   after the fact. All writes are upserts.
6. Clients discover the change through `GET /v1/sync`, which names the widget kinds to
   invalidate.

The nightly bulk path exists too (`ingest_day`: one `LeagueGameLog` call plus one
`PlayerGameLogs(MeasureType='Advanced')` call per season), because per-game calls are only
sensible for the handful of games that just ended.

---

## 3. iOS app

See [`ios/ARCHITECTURE.md`](../ios/ARCHITECTURE.md) for the full Swift type surface. The shape:

```
ios/NBAStats/
├── Core/          value types, bundled catalog, layout document, migration
├── DesignSystem/  colors, type scale, sparkline, percentile bar, availability badges
├── Networking/    APIClient (actor), DemoAPIClient, DiskCache, DashboardService, SyncService
├── Dashboard/     DashboardStore, the grid, edit mode, the dynamic config sheet, presets
├── Widgets/       one view per kind + WidgetHost dispatcher
├── Screens/       search, player detail, settings, onboarding
└── App/           entry point, environment, root navigation, background refresh
```

### The editable dashboard

A layout is a plain JSON document (`DashboardLayout`) stored on device: a name, an accent, and
an ordered list of widgets, each with a `kind`, a `size` and a free-form `config` object. There
are no grid coordinates — widgets flow into a 2-column (iPhone) or 4-column (iPad) grid
according to their size, which keeps reordering trivial and keeps the document portable.

Presets are the same document type, shipped by the server. **Editing a preset silently forks it**
into an editable copy that retains its `presetKey`, so "Reset to Daily Recap" stays available
forever.

The configuration sheet is **generated from `contracts/widgets.json` at runtime** — one editor
per field type (metric picker, player search, enum, stepper, toggle, date). A new widget option
on the server appears in the app's UI without an app update.

`LayoutMigrator` handles version skew in both directions: an older document is upgraded
(unknown kinds dropped with a visible note, unknown config keys stripped, missing required keys
defaulted); a *newer* document is shown read-only behind an "update the app" banner rather than
being silently corrupted.

### Offline and demo behaviour

`DiskCache` stores resolved payloads keyed by a hash of kind + normalized config + context, with
the server-supplied TTL and stale-while-revalidate semantics. `DemoAPIClient` serves the bundled
golden fixtures, so the app is fully explorable with no backend at all — which is also how the
SwiftUI previews and the unit tests get their data.

### Era honesty in the UI

The rule the whole product hangs on: **a stat that did not exist is never rendered as zero.**
`MetricAvailability` travels with every value from the database to the pixel, and the design
system renders `.unavailable` as an em dash with a tappable explanation, `.estimated` with a
dashed underline and an "est." badge, `.partial` with a caret and a footnote. The career-arc
widget draws era boundaries as vertical rules, because that is where comparing a 1962 season to
a 2026 season is most tempting and most wrong.

---

## 4. Web

The browser client is not a second deployment. `nbastats.api.routes_web.mount_web` is called
last, after every router, and serves the built Vite bundle at `/` from the same process that
answers `/v1`. **One origin** is the load-bearing choice: no CORS, no preflight, no API token
in browser storage, and a Content-Security-Policy of `script-src 'self'` with no nonce and no
`unsafe-inline`, which a single-origin static bundle can actually hold to.

```
web/src/
├── api/          fetch wrapper (CSRF header, same-origin credentials, typed errors), resolve, session
├── auth/         AuthProvider, RequireAuth, the four forms, provider buttons
├── dashboard/    the editor: layout store, flow layout, widget container, generated config sheet
├── design/       ~25 primitives + hand-drawn SVG chart parts
├── generated/    tokens.ts · tokens.css · contracts.ts · registry.ts — never hand-edited
├── pages/        routed screens, including /legal/terms and /legal/privacy
└── widgets/      one directory per widget kind, sixteen of them
```

### Why the design system is generated

The iOS app's `DesignSystem/Theme.swift` is the source. `contracts/tools/gen_theme.py` parses
it into `contracts/theme.json`; `gen_web_tokens.py` turns that into CSS custom properties and
TypeScript constants. A colour cannot drift between the two clients by hand, because a hand
cannot write one — `stylelint` bans literal colours in components, and a `check_contracts.py`
check fails CI if the generated files do not reproduce byte-identically. The cost is that a
legitimate `Theme.swift` refactor blocks CI until the generator is taught about it, which is
the trade made deliberately: a loud maintenance cost in place of a silent drift.

`registry.ts` maps a widget kind to its component, generated by looking for
`src/widgets/<kind>/index.tsx` on disk. Adding a widget is a directory plus a regeneration,
never an edit to a switch statement.

### Accounts, sessions and saved dashboards

`backend/nbastats/accounts/` is a self-contained package on its **own** SQLAlchemy declarative
base, deliberately not `nbastats.models.Base`. `HARDWOOD_DEMO_MODE` re-seeds by deleting every
table on the stats metadata; account rows must not be in that blast radius, and a separate
metadata is what guarantees it rather than a convention someone has to remember.

A session is an opaque `{id}.{secret}` cookie; the database stores only `sha256(secret)`, so
revocation is a row update and a stolen database yields no live sessions. CSRF is a
synchroniser token derived from that secret, sent in `X-Hardwood-CSRF` and checked against the
session row, plus an `Origin` / `Sec-Fetch-Site` check — deliberately not a double-submit
cookie, which is unsound on `127.0.0.1` where cookies are scoped by host and not by port.

Saved dashboards are the same `DashboardLayout` JSON document the iOS app stores on device,
now server-side per user, with a Python port of `LayoutMigrator` applying the same version-skew
rules. `contracts/fixtures/layout_migration_cases.json` is asserted from both test suites, with
note strings copied verbatim, so a divergence between the Swift and Python migrators shows up
as a wording diff in review.

Configuration, threat model and operations: [WEB.md](WEB.md).

---

## 5. Testing strategy

| Layer | What is actually verified |
| --- | --- |
| `backend/tests/test_metrics.py` | Hand-computed worked examples for every formula, degenerate inputs, and a guard that every metric key in the catalog is handled |
| `backend/tests/test_seed.py` | Seeded box scores reconcile to team totals and final scores; no pre-1997 advanced rows exist |
| `backend/tests/test_ingest.py` | V2/V3 normalization, idempotent re-ingest, one sync bump per game, correction-window overwrite — all with recorded fixtures, no network |
| `backend/tests/test_api_core.py` | Every route, the camelCase key contract, null-not-zero, pagination, auth gate |
| `backend/tests/test_widgets.py` | All 12 kinds resolve; **every widget of every shipped preset resolves**; the era case returns unavailable, not 500 |
| `backend/tests/test_contract_fixtures.py` | Golden fixtures regenerate byte-identically |
| `ios/NBAStatsTests/` | Catalog decoding, formatting, JSON round-trips, layout editing and migration, payload decoding against the same golden fixtures, dashboard service state mapping |
| `backend/tests/test_accounts_*`, `test_auth_*` | Sessions, CSRF, password hashing, the whole Google and Apple flows against a local fake issuer with a generated EC key, linking rules, and a test whose only job is to prove a user survives a stats reseed |
| `web/src/**/__tests__/` | 502 vitest tests: the design primitives, every widget, the dashboard editor, the auth forms, and the migrator parity cases shared with Swift |
| `scripts/check_contracts.py` | Twelve checks. Catalogs regenerate; presets validate; backend registry matches the catalog; the app's bundled copies are byte-identical; the web's generated tokens, contracts and registry regenerate; widget kinds agree across Python, Swift and TypeScript |

The iOS app cannot be compiled in the environment this repository was built in (Linux, no Swift
toolchain). See the honesty note in the root README.

---

## 6. Leagues: the EuroLeague, availability, matchups and defence

The service is no longer only the NBA. It also serves the EuroLeague, who is available to play
(injuries and absences, with the source and age of every status), team-against-team matchups, and
how each defence fares against each kind of opponent. The design question was how to add a second
league **without any way for it to leak into the first**, and without anything the seeder can
wipe.

```
                         ┌───────────────────────────────────────────────────┐
  hardwood.db            │ Base            NBA stats      (the seeder wipes it)│
  (NBA)                  │ AccountBase     accounts       (own MetaData)       │
                         │ NbaIntelBase    injuries, news (own MetaData)       │
                         └───────────────────────────────────────────────────┘
  hardwood_el.db         ┌───────────────────────────────────────────────────┐
  (EuroLeague, sealed)   │ ElBase          el_* tables, string ids             │
                         └───────────────────────────────────────────────────┘

  /v1/...        NBA  ─┐
                       ├─ identical route suffixes, one payload format (`league`, `freshness`)
  /v1/el/...     EuroLeague ┘
```

### Why three metadata objects and two files

* **The seeder wipes every table on `Base`, with no WHERE.** Anything that must survive a re-seed
  (accounts, injury and headline records, the EuroLeague) is therefore on its own `MetaData`, the
  same precedent the accounts feature set. A test counts the rows before and after a re-seed.
* **The EuroLeague gets its own database file**, its own engine, its own write-ahead log and its
  own sync cursor. Two guards stop it ever being pointed at the NBA file: the same-store guard
  (equal real paths, or equal URLs without credentials, disables the EuroLeague) and a refusal to
  initialise a file that already holds an NBA `teams` table.
* **Real and invented data cannot meet.** Each EuroLeague store is stamped `synthetic`,
  `workbook` or `live`, and a store refuses writes of another kind. On the NBA side, the synthetic
  check is *keyed on the store's own contents* (`games.data_source = 'synthetic-demo'`), not on
  `HARDWOOD_DEMO_MODE`, so a manual re-seed of a live file cannot join real injury statuses to
  invented games.
* **Season codes such as `E2026` and string ids exist only in the EuroLeague store**, so they can
  never reach code that treats a season as `2025-26` or an id as a number.

### The packages

| Package | Role | Depends on |
| --- | --- | --- |
| `shared/` | The team-score model, the injury layer, team form, defence-by-position allocation and statistics, the availability vocabulary, position normalisers, the forbidden-vocabulary guard list, and a late-bound lookup that lets a route ask "give me league X's session" without importing league X. **Standard library only**; an AST test enforces it | nothing |
| `intel/` | The polite HTTP client, the feed fetcher, the `robots.txt` check, raw-payload recordings | `shared` |
| `nba_intel/` | The NBA injury report (PDF) and its parser, NBA headlines, model settings, job state | `shared`, `intel` |
| `nba_matchup/` | NBA matchup, defence by position and team projections, read from the stats store | `shared`, the stats store |
| `euroleague/` | Models, workbook importer, demo league, live ingest, rating model, read side and routes. **Sealed**: nothing outside it may import it | `shared`, `intel` |
| `worker.py` | The scheduler for all of the above | nothing, by design: every job is reached by string name |

A test walks every module's imports: `euroleague` imports only the shared core and the shared
plumbing, and no module outside it imports `euroleague` except the three-line route shim
(`api/routes_euroleague.py`). `api/app.py` reaches the EuroLeague bootstrap with
`importlib` by string name, inside a `try/except`, so a EuroLeague failure disables the
EuroLeague and nothing else. `worker.py` does the same for every job, which is why it can start
before, or without, any of those packages.

### One payload format

Every new payload carries a `league` (`"nba"` or `"euroleague"`) and a `freshness` block (data
through, the sources that fed it, whether it is the demo). Teams and players are referenced by
league-neutral `LeagueTeamRef` and `LeaguePlayerRef` objects whose `id` is a string, so a client
keys entities by `(league, id)` and never hard-codes `/v1/el`: `GET /v1/leagues` says which
leagues exist, their route prefix, their state and the reason when one is off. The route
*suffixes* are identical across leagues (`matchups`, `teams/{id}/matchup`,
`teams/{id}/defense-by-position`, `projections`, `availability`, `news`, `sources`,
`model-settings`); the league is chosen by the mount point, never by a header or a parameter.

The EuroLeague router is mounted through the same optional-router path as the other optional
routers, behind the same API-key-or-session and rate-limit guards, and uses its own database
dependency, never the NBA one.

### Availability is data with a source, not a flag

A status is `out`, `doubtful`, `questionable`, `probable`, `available` or *null*, and null renders
as an em dash, **never as "available"**. Every status carries who said it, where, and the date
*they* said it (the age is computed from the source's date, not from when Hardwood fetched it).
Statuses are append-only; a correction is a new row. For the NBA they come from the league's
official report, automatically; for the EuroLeague, which publishes no such list, from the
workbook and from entries made by hand. A status drives a projection only while it is in force
(§4.5 of [EUROLEAGUE.md](EUROLEAGUE.md)).

### What stands between this and a betting product

Defence against a position and combined points are adjacent to products this repository must not
become. So: no field, setting or route parameter exists for a line, an odds figure, an
over/under or any outside number to compare a projection with, and **a test walks every new
payload key, every new route parameter and both settings allowlists to prove it**. The prose
guard that already scans the web and accounts code now also scans these packages. There is no win
probability in this version. The injury layer is not connected to the fantasy toolkit, and a test
proves it. Betting-operator links are withheld from availability sources.

### Running it on a Mac

```
launchd (your login)
  ├── com.hardwood.api          uvicorn on 127.0.0.1, KeepAlive
  ├── com.hardwood.nba-watch    ingest loop, KeepAlive
  ├── com.hardwood.nba-nightly  06:10, a calendar job (coalesced on wake)
  └── com.hardwood.worker       python -m nbastats.worker, KeepAlive
          one scheduler, many jobs, state in nba_intel_job_state / el_job_state
```

One KeepAlive worker with its own timetable replaces what would otherwise be a launchd job per
task. Its jobs are written to converge on "the latest valid state at or before now", so a Mac
that slept through 05:00 simply runs the job once on waking and there is no backlog to replay. It
re-reads `hardwood.env` every 30 seconds, so a switch is a kill switch. It never creates the
EuroLeague store (the API does) and never writes a job's cursor (the job does). A job whose
package is not installed is recorded as `notInstalled`, and the rest carry on. The scheduler, its
contract with the jobs and its refusals are described in the docstring of
`backend/nbastats/worker.py`; operation is in [RUNBOOK.md](RUNBOOK.md) §1c.

### The client

The client for this work is a native macOS app, built after the backend, which the existing
SwiftUI target grows into. The four new widget kinds (team matchup, defence by position,
availability report, slate projections) therefore get Swift widgets in that phase, not web
components; the web client renders its placeholder tile for them. The backend serves identical
JSON to either.

### The tests that hold the line

| Guard | What it proves |
| --- | --- |
| `test_league_isolation.py` | The import boundary, the metadata prefixes, and that no `el_` or `nba_intel_` table is on `Base` |
| `euroleague/test_no_leak.py` | With deliberately colliding data (the same club code, season label, game date), no EuroLeague id appears in any NBA response and vice versa |
| `euroleague/test_survives_reseed.py`, `test_nba_intel_schema.py` | The new stores' row counts are unchanged by a re-seed |
| `test_no_market_machinery.py` | No forbidden word in any new payload key, route parameter or settings key; no half-point rounding; no win-probability field |
| `test_worker_jobs.py` | The worker's timetable, catch-up, switches, refusals and state; the launchd files and the installer |
| `shared/test_stdlib_only.py` | The shared core imports nothing but the standard library |

