# Hardwood — iOS app

An editable advanced-stats dashboard for NBA data. SwiftUI, iOS 17+, Swift Charts, **no
third-party packages**.

The app is a grid of widgets the reader arranges themselves: drag to reorder, resize, configure
every widget from a schema the server ships, or start from one of nine presets. Every number is
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

`CatalogTests` asserts the counts the contract commits to — **61 metrics, 12 widgets, 9 presets**
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
