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
└── api/                 FastAPI: routes, pydantic schemas, serializers, errors
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
