# Hardwood — iOS app

An editable advanced-stats dashboard for NBA data. SwiftUI, iOS 17+, Swift Charts, **no
third-party packages**.

The app is a grid of widgets the reader arranges themselves: drag to reorder, resize, configure
every widget from a schema the server ships, or start from one of twelve presets. Every number is
era-honest — a stat the league did not record in a given season renders as an em dash with an
explanation, never as a zero.

---

## Opening the project

```bash
open ios/NBAStats.xcodeproj
```

* **Xcode 16 or newer is required.** The project uses *file-system synchronized groups*
  (`PBXFileSystemSynchronizedRootGroup`, project object version 77), so the `NBAStats/` and
  `NBAStatsTests/` folders are mirrored into the targets automatically. Adding a Swift file is
  just putting it in the right folder — there is nothing to add to the project file, and no merge
  conflicts in `project.pbxproj` when two people add files at once.
* Two targets: **Hardwood** (the app, module name `Hardwood`) and **HardwoodTests** (unit tests,
  hosted by the app).
* Deployment target: **iOS 17.0**. Swift language mode 5.

Select the **Hardwood** scheme and an iPhone simulator, and press Run. Nothing else is needed —
the app launches with no backend (see *Demo mode* below).

---

## Running with a backend

The app reads its base URL from `Info.plist`, and a value the reader sets in **Settings › Stats
server › Server** overrides it. Out of the box it points at a local backend:

```xml
<key>HardwoodAPIBaseURL</key>
<string>http://localhost:8000/v1</string>
```

To run against the service in this repository:

```bash
# from the repository root
cd backend
python3 -m pip install -e '.[test,serve]'
./scripts/serve_dev.sh --fresh          # seeds a demo league and serves it on :8000
```

`serve_dev.sh` is a wrapper; by hand it is
`uvicorn nbastats.api.app:app --reload --port 8000` once a league has been seeded. The backend
needs no network of its own — `nbastats.seed` generates a complete deterministic league — so
this works on a fresh checkout with nothing else running.

Then in the app: **Settings › Data source → turn "Use bundled demo data" off**. If your server is
on another machine, put its URL in **Settings › Stats server › Server**
(`http://192.168.1.20:8000/v1`, say) and press **Test Connection** — that saves what you typed and
then calls `/v1/health` on the live server, whatever demo mode is set to.

* `NSAllowsLocalNetworking` is already set, so a plain-HTTP LAN address works without further
  App Transport Security changes.
* If the server is started with `HARDWOOD_API_KEY` set, put the same key in **Settings › Stats
  server › API key**; it is sent as `X-API-Key` on every request except `/v1/health`.
* Leaving the Server field empty goes back to the address the build shipped with.
* Both URL and key live in `UserDefaults` (`hardwood.api.baseURL`, `hardwood.api.apiKey`) and take
  effect immediately — no relaunch.

### Demo mode

`HardwoodDemoModeDefault` is `true` in `Info.plist`, so a **fresh install serves the bundled
golden fixtures** and every screen works with no server at all. The fixtures in
`NBAStats/Resources/Fixtures/` are byte-identical copies of `contracts/fixtures/`, which is what
the backend's own tests decode — so demo mode shows real shapes rather than invented ones.

Demo mode is the **Settings › Data source** toggle. Switching it clears the widget cache and
re-resolves the dashboard, so the two data sources can never be mixed on screen.

---

## Folder map

```
ios/
├── NBAStats.xcodeproj          Xcode project (synchronized groups; nothing to hand-edit)
├── ARCHITECTURE.md             The binding type surface every module is written against
├── README.md                   This file
├── NBAStats/
│   ├── App/                    HardwoodApp, AppEnvironment, RootView, background registration
│   ├── Core/                   Value types, JSONValue, the bundled catalog, layout documents,
│   │                             layout migration, formatting.  Depends on nothing.
│   ├── DesignSystem/           Palette, typography, spacing, and the small shared views:
│   │                             stat values, chips, sparkline, percentile bar, era badges,
│   │                             loading / error / empty / unavailable tiles
│   ├── Networking/             APIClient (actor), DemoAPIClient (actor), Endpoints, DiskCache
│   │                             (actor), DashboardService, SyncService, background refresh
│   ├── Dashboard/              The dashboard screen, the flowing grid, edit mode, the widget
│   │                             catalog sheet, the schema-driven config sheet, preset gallery,
│   │                             layout switcher, layout persistence
│   ├── Widgets/                One view per widget kind, plus WidgetHost and the chart plumbing
│   ├── Screens/                Search, player detail, settings, onboarding
│   ├── Resources/
│   │   ├── Contracts/          metrics.json, widgets.json, presets.json — copies of contracts/
│   │   ├── Fixtures/           Golden payloads, which is what demo mode serves
│   │   └── Assets.xcassets
│   └── Info.plist
└── NBAStatsTests/
    ├── TestSupport.swift       Bundle lookup, temp directories, the stub API client
    ├── CatalogTests.swift      The bundled contracts decode and agree with each other
    ├── FormattingTests.swift   Every MetricFormat, and the em-dash rule
    ├── JSONValueTests.swift    Round trips, Bool/Int/Double discrimination, nesting
    ├── LayoutTests.swift       Editing, persistence, migration
    ├── PayloadDecodingTests.swift  Every golden fixture, decoded into its payload type
    ├── ProjectionPayloadTests.swift The next-game projection: low ≤ mean ≤ high, every line
    │                                estimated, the correlated spread wider than the independent
    ├── PlayerAvatarTests.swift      Initials and the deterministic monogram colour
    ├── DashboardStoreTests.swift   Presets, undo, deleting the last dashboard
    ├── DashboardServiceTests.swift Mixed results, request splitting, stale-while-revalidate
    ├── SyncServiceTests.swift      Polling, cache invalidation
    └── Fixtures/               The golden payloads the tests decode
```

Dependency direction is one way: `Core` → `DesignSystem` / `Networking` → `Dashboard` / `Widgets`
→ `Screens` → `App`. `ARCHITECTURE.md` §2 is the binding list of type signatures; anything named
there exists with exactly that shape.

---

## Running the tests

```bash
xcodebuild test -scheme Hardwood -destination "platform=iOS Simulator,name=iPhone 16"
```

Or ⌘U in Xcode. The suite is pure unit tests — no network, no simulator UI automation — and
finishes in a few seconds.

To run one case or one test:

```bash
xcodebuild test -scheme Hardwood \
  -destination "platform=iOS Simulator,name=iPhone 16" \
  -only-testing:HardwoodTests/DashboardServiceTests

xcodebuild test -scheme Hardwood \
  -destination "platform=iOS Simulator,name=iPhone 16" \
  -only-testing:HardwoodTests/FormattingTests/testPercent1FormatMultipliesTheFraction
```

If the destination is rejected, list what is installed with
`xcrun simctl list devices available` and substitute a name from that list.

### What the tests assume

* The bundled contracts (`NBAStats/Resources/Contracts/*.json`) are in the **app** bundle, which
  is `Bundle.main` for a host-application unit test.
* The golden fixtures are in the **test** bundle (`NBAStatsTests/Fixtures/*.json`) or the app
  bundle. A test that cannot find them reports `XCTSkip` with the reason rather than failing, and
  still asserts against each payload type's own `preview` value so it is never vacuous.

### Keeping the contracts in sync

`NBAStats/Resources/Contracts/` and `NBAStatsTests/Fixtures/` are copies of `contracts/` and
`contracts/fixtures/` at the repository root. After regenerating either, re-copy them and re-run
the drift check:

```bash
./scripts/sync_contracts.sh      # copies contracts/ and contracts/fixtures/ into ios/
python3 scripts/check_contracts.py
```

`CatalogTests` asserts the counts the contract commits to — **61 metrics, 16 widgets, 12 presets**
— so a catalog change that the app has not been told about fails the iOS suite too.

---

## How the dashboard loads

1. `DashboardStore` reads the layouts from `Application Support/Hardwood/Layouts.json`, running
   each through `LayoutMigrator`: unknown widget kinds are dropped with a note the reader sees,
   unknown configuration keys are stripped, missing keys take the catalog default, and a document
   from a newer build is refused rather than corrupted.
2. `DashboardService` publishes whatever is in the disk cache immediately, marked stale.
3. One `POST /v1/dashboard/resolve` carries the whole layout — split into as few requests as the
   contract's 24-widget cap allows — and each result replaces its tile independently.
4. A failure never blanks a tile that already has numbers on it: it is marked stale instead.
   A widget that fails on its own renders an error inside its own tile with a retry button.
5. `SyncService` polls `/v1/sync` on foreground, on pull-to-refresh and from a `BGAppRefreshTask`.
   When the league's sync version moves it drops exactly the widget kinds the server names and
   asks the dashboard to re-resolve.

---

## The Next Game widget

`next_game_projection` is the one widget that shows numbers for a game that has not been played:
a projected box score, `Ŝ = M̂ · r̂_reg · f_pace · f_opp · f_home · f_rest`. The derivation is
[`docs/PROJECTION.md`](../docs/PROJECTION.md); §7 of that document is the widget's design, and it
is not optional:

* **No mean is drawn without its interval.** Every line shows its low–high beside the mean, in
  the same weight. A line that arrived without bounds says so on the line instead of quietly
  rendering a bare number.
* **Projected minutes get their own block, above the stat lines.** Minutes are the dominant error
  term — supplying true minutes cuts points error by about 19%, where a better production model
  gains under 4% — so a reader who disagrees with the minutes meets that number first.
* **The context factors are shown**, each as a multiplier around 1.00, so the projection can be
  argued with a factor at a time rather than accepted whole.
* **Thin history is stated, not smoothed over.** A rate that is mostly the league prior, or one
  with less exposure behind it than its own stabilisation constant, is marked on its own line, and
  the exposure in minutes is printed under the lines at every size.
* **A projection is never a record.** Every projected value carries `availability: "estimated"`
  and renders with the app's estimated treatment — the dashed underline and the `est.` marker.
  `ProjectedLine` enforces that in the decoder: a payload that called a line `"full"` is clamped
  back to `estimated`, because `.full` is the one value that would render as a plain confident
  number. `WidgetHost` gives the tile a projection-specific footnote rather than the pre-1997
  possession-data sentence, which is true of a derived season stat and false of a projection.
* **There is no market translation and no place to put one** — no odds, implied probability,
  expected value, edge or staking, for the two reasons in `docs/PROJECTION.md` §6. `CatalogTests`
  asserts that vocabulary appears nowhere in the shipped catalogs either.

The **Next Game** preset (`presetKey: next_game`) puts the projection above the player's form,
minutes trend, recent games and tonight's slate, all pointed at `$favorite_player`.

The golden fixture is `contracts/fixtures/widget_next_game_projection.json`, generated from the
service like every other one, so demo mode serves a real projection with no backend running.
`ProjectionPayloadTests` decodes it and asserts the invariants — every line estimated, the mean
inside its own interval, the correlated spread wider than the independent one, the correlation
matrix square and symmetric with a unit diagonal — and, if the fixture is ever missing, skips with
the command that regenerates it while still asserting all of that against
`NextGameProjectionPayload.preview`.

One thing the fixture will teach you before the code does: these are *quantile* intervals over a
count, so a tiny mean can sit outside a collapsed one. The demo player projects 0.08 three-pointers
with an 80% interval of 0–0, because he makes none more than nine nights in ten. That is the
distribution being honest, not a bug, and the test allows it only when `low == high`.

Each factor also carries `contributions`: the same multiplier expressed in each statistic's own
units, so the widget's "Why" block can say `+0.6` rather than `1.021`. It is presented as the
largest movers and never as a breakdown — the factors multiply, so the column does not sum, and
`ProjectionPayloadTests` asserts the gap rather than leaving the caveat in prose.

---

## The Fantasy Board and the broadsheet

`projection_board` is the same engine turned into a slate view: several players, each line drawn
as a range with the player's own season average marked on it. It comes from a Claude Design
handoff and it is the one preset that does not render as tiles.

* **`DashboardLayout.presentation`** is `tiles` or `broadsheet` and governs the page, not a
  widget: `DashboardGrid` collapses to one column, `WidgetContainer` drops the card entirely and
  sets the title as a kicker, and `\.isBroadsheet` reaches every widget below.
  A layout document written before the field existed decodes as `tiles`.
* **`RangeBar`** draws the band (the 80% interval, which *is* the 10th-to-90th percentile), the
  dot (the projection) and the tick (the reference). It takes its greys from the page, so the
  board is legible on an ordinary dashboard too.
* **The tick is a season average, never a book line.** The design drew every band against a
  sportsbook number; that layer is not implemented and should not be added without the licensing
  conversation in [`docs/BROADSHEET.md`](../docs/BROADSHEET.md) §1 happening first.
  `ProjectionBoardPayloadTests` searches the encoded payload for the vocabulary.
* **Rows are ranked by `deltaZ`, not by `delta`.** A raw delta is not comparable across
  statistics — ranking on it returns six rows of points every night — so the sort key is the
  delta over the projection's own spread. The test's power check re-ranks by raw delta and
  asserts that it would have collapsed.
* **Three dates, because there are three**: `selectionDate` is the completed slate the players
  were chosen from, `date` and `throughDate` bracket the games being projected.

`docs/BROADSHEET.md` §8 records where the presentation deliberately stops: the loading, failure
and era-gap tiles keep the app's treatment even on a broadsheet page.

---

## The fantasy toolkit

Two widgets built from the user's `Fantasy NBA 2026-27 Toolkit` workbook: `fantasy_draft_board`
and `fantasy_trade`. `docs/FANTASY.md` has the mathematics; what matters on the client side is
that three properties of the payload must survive any redesign.

* **Turnovers are sign-flipped.** A positive z means *few* turnovers. `FantasyCategory` owns the
  direction, and every phrase goes through `changePhrase(_:z:)` — a row that says "gains
  turnovers" is praising the thing the category penalises, and the sign alone cannot tell you.
* **`rosterAdjustment` renders as its own line.** Give two players and get one and the freed slot
  refills from waivers below pool average, often outweighing the players themselves.
  `rosterDominates` exists so the widget can say so. Showing only `netZ` leaves a reader unable
  to tell a bad trade from slot arithmetic.
* **`sensitivity` is a range and is never labelled an interval.** It is the same trade recomputed
  under four named assumptions, with no probability anywhere in it, and
  `FantasyPayloadTests` greps its wording for "confidence", "probability" and "%".
  `flips` — the scenarios disagreeing about the sign — is what the widget leads with, because a
  trade that only wins if everyone stays healthy is a different proposition from one that wins
  either way.

The nine-category strip is the reason a category league is not a single number: two players with
the same `totalZ` can be opposite picks. Colour on that strip carries the sign and nothing else,
since the z is printed beside it and a gradient would imply precision it does not have. At
accessibility text sizes the strip is replaced by naming the two strongest categories, and
VoiceOver gets a sentence rather than eighteen numbers.

---

## Headshots

`PlayerAvatar` draws a player's face where there is one and a designed mark where there is not.
`PlayerRef.headshotUrl` points at the NBA's public CDN, which is unreachable from many build and
test environments and has no asset at all for a large share of historical players, so the view is
written around the assumption that **the photo is the exception**:

* the circle is laid out at its final diameter (24 / 44 / 88pt) before any request is made, so a
  photo arriving — or never arriving — never moves the row it sits in;
* loading and failure draw the *same* monogram on the *same* tinted circle, so a slow CDN and a
  missing headshot degrade into one deliberate-looking mark; the only difference while a request
  is in flight is that the initials are dimmed;
* a `nil`, blank or unparseable URL skips the network entirely;
* there is no broken-image glyph and no spinner anywhere in the file.

The initials come from `firstName`/`lastName` when the server sent both, and otherwise from the
first two *words* of `name` — "Gary Payton II" is GP, not GI — skipping punctuation ("J.R. Smith"
is JS) and keeping accents ("Álex Abrines" is ÁA, precomposed or decomposed). A name with nothing
readable in it gets `?`, never an empty circle. The colour is a fixed FNV-1a fold of
`"player<id>"`, so a player is the same colour on every launch and every device; Swift's own
`hashValue` is seeded per process and cannot be used for this.

Sizes are fixed points rather than `@ScaledMetric` on purpose: Dynamic Type scales the glyphs
inside the circle, never the circle, or a column of ten leaderboard rows stops lining up. Avatars
are `accessibilityHidden` everywhere — the row's own label already reads the name.

---

## Conventions worth knowing before you edit

* **Never render `0` for a stat that did not exist.** `MetricValue.value == nil` means an em dash
  (`Formatting.emDash`), an `AvailabilityBadge`, and a tap target that explains the gap in the
  league's record. `AvailabilityExplainer.eraTimeline` holds the dates.
* **Colors come from `Palette` only.** Dark and light mode are both first-class; there is no
  literal `Color(red:green:blue:)` inside a view.
* **UI state is `@MainActor`; networking types are actors.** `Catalog`, `DashboardStore`,
  `DashboardService` and `SyncService` are main-actor isolated; `APIClient`, `DemoAPIClient` and
  `DiskCache` are actors.
* **No force unwrapping of decoded data, no `try!`, no `fatalError`.** Every response type
  tolerates a missing field, and a single unreadable entry costs that entry rather than the
  document around it.
* **Every view has a `#Preview`**, and the previews run from `DashboardPreviewData` or a payload's
  own `preview` static, so they work with no server and no simulator data.
