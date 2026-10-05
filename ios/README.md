# Hardwood — the iOS and Mac app

An editable advanced-stats dashboard for NBA data, and, on the Mac, a native workbench for the NBA
and the EuroLeague: matchups, points allowed by opponent position, injuries and news with their
sources. SwiftUI, iOS 17+ and macOS 14+, Swift Charts, **no third-party packages**.

The app is a grid of widgets the reader arranges themselves: drag to reorder, resize, configure
every widget from a schema the server ships, or start from one of twelve presets. Every number is
era-honest — a stat the league did not record in a given season renders as an em dash with an
explanation, never as a zero.

**One target, two platforms.** `Hardwood` builds for iOS 17 and for macOS 14 from the same
sources. **The Mac is the product** (a sidebar, native tables, an inspector, menus, a Settings
window — [`docs/MAC.md`](../docs/MAC.md) is its manual); the iPhone and iPad build keeps its tab
bar and must keep compiling. [*One target, two platforms*](#one-target-two-platforms) below is the
rule for where platform-specific code may live.

---

## Opening the project

```bash
open ios/NBAStats.xcodeproj
```

* **Xcode 16 or newer is required** (itself needing a recent macOS, Sonoma 14.5 or later at the
  time of writing). The project uses *file-system synchronized groups*
  (`PBXFileSystemSynchronizedRootGroup`, project object version 77), so the `NBAStats/` and
  `NBAStatsTests/` folders are mirrored into the targets automatically. Adding a Swift file is
  just putting it in the right folder — there is nothing to add to the project file, and no merge
  conflicts in `project.pbxproj` when two people add files at once.
* Two targets: **Hardwood** (the app, module name `Hardwood`) and **HardwoodTests** (unit tests,
  hosted by the app). Both build for iOS, the iOS simulator and macOS. There is no Mac Catalyst and
  no "Designed for iPad" build: SwiftUI targets the Mac directly.
* Deployment targets: **iOS 17.0** and **macOS 14.0**. Swift language mode 5.
* Two property lists, because the platforms need different keys: `Info.plist` (iOS) and
  `Info-macOS.plist` (selected by `INFOPLIST_FILE[sdk=macosx*]`). Both live in `ios/`, outside the
  synchronized `NBAStats/` folder: a plist inside that folder is copied into the bundle as a
  resource while the build also produces it, and the build stops with "Multiple commands produce".

**To run on an iPhone simulator,** select the **Hardwood** scheme and an iPhone simulator, and press
Run. Nothing else is needed — the app launches with no backend (see *Demo mode* below).

**To run on the Mac,** install the server once (`backend/scripts/macos/install.sh`; it runs under
launchd, see [`docs/RUNBOOK.md`](../docs/RUNBOOK.md) §1c), choose **My Mac** as the run
destination, and press Run. The Mac build starts on live data at `http://127.0.0.1:8000/v1`. The
whole story — first launch, demo or live, signing, what each screen shows, how to paste Xcode errors
back — is in [`docs/MAC.md`](../docs/MAC.md).

---

## Running with a backend

The app reads its base URL from its property list, and a value the reader sets in **Settings ›
Stats server › Server** overrides it. Out of the box it points at a local backend:

```xml
<key>HardwoodAPIBaseURL</key>
<string>http://localhost:8000/v1</string>        <!-- Info.plist, iOS -->
<string>http://127.0.0.1:8000/v1</string>        <!-- Info-macOS.plist, the Mac -->
```

The Mac uses `127.0.0.1` because the installed server listens on IPv4 loopback only, and
`localhost` may resolve to `::1` first.

**On the Mac, use the installed server, not `serve_dev.sh`.** `backend/scripts/macos/install.sh`
runs the API, the NBA watcher and a worker under launchd, with a private API key in
`~/Library/Application Support/Hardwood/hardwood.env`
([`docs/RUNBOOK.md`](../docs/RUNBOOK.md) §1c). The Mac app reads that key from the file itself at
launch (only when none is stored, and it never shows or rewrites it), so nothing needs copying by
hand. With a key set, the server asks for it on every read except `/v1/health`, and a change made
from the app (recording an injury status, pasting a headline link) is accepted only with it.

To run against the demo service in this repository instead (any platform):

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

* `NSAllowsLocalNetworking` is already set in both plists, so a plain-HTTP LAN address works
  without further App Transport Security changes.
* If the server is started with `HARDWOOD_API_KEY` set, put the same key in **Settings › Stats
  server › API key** (on the Mac, **Settings › Leagues › Read API key from hardwood.env** does it
  for you); it is sent as `X-API-Key` on every request except `/v1/health`.
* The app sends no `Origin` header and no custom "I am the Mac app" header. The server treats a
  request that carries an `Origin` as a browser's (session, CSRF, same origin), and a header any web
  page can also send is not a credential, so the key alone is what authorises a native change.
* Leaving the Server field empty goes back to the address the build shipped with.
* Both URL and key live in `UserDefaults` (`hardwood.api.baseURL`, `hardwood.api.apiKey`) and take
  effect immediately — no relaunch.

### Demo mode

`HardwoodDemoModeDefault` is `true` in `Info.plist`, so a **fresh iOS install serves the bundled
golden fixtures** and every screen works with no server at all. `Info-macOS.plist` sets it to
`false`: the Mac app is meant to read the server on the same machine, and offers demo data from its
server banner (and from the same Settings toggle) when that server is not answering. The fixtures
in `NBAStats/Resources/Fixtures/` are byte-identical copies of `contracts/fixtures/`, which is what
the backend's own tests decode — so demo mode shows real shapes rather than invented ones. The
league screens read the same way: one recorded response of every NBA and EuroLeague route, copied
in as `league_nba_<name>.json` and `league_el_<name>.json` (the prefix keeps the names unique,
because Xcode flattens resource folders into the bundle root).

Demo mode is the **Settings › Data source** toggle. Switching it clears the widget cache and
re-resolves the dashboard, so the two data sources can never be mixed on screen. It is read-only:
a write to the server (an injury status, a headline link) is refused in demo mode with a sentence
saying so. A league screen with no bundled fixture says "Demo data for this screen isn't bundled
yet" rather than showing an error, and a EuroLeague dashboard tile never falls back to the NBA
fixture of the same kind.

---

## Folder map

```
ios/
├── NBAStats.xcodeproj          Xcode project (synchronized groups; nothing to hand-edit)
├── project.yml                 An XcodeGen mirror of the project settings. The .xcodeproj is canonical.
├── Info.plist                  The iOS property list (outside NBAStats/ on purpose, see above)
├── Info-macOS.plist            The Mac property list: live data by default, 127.0.0.1
├── ARCHITECTURE.md             The binding type surface every module is written against
├── README.md                   This file
├── NBAStats/
│   ├── App/                    HardwoodApp (the iOS WindowGroup, or the Mac's MacScenes),
│   │                             AppEnvironment, RootView (iOS tabs), background registration
│   ├── Core/                   Value types, JSONValue, the bundled catalog, layout documents,
│   │                             layout migration, formatting.  Depends on nothing.  Also the league
│   │                             payloads: LeaguePayloads (every NBA and EuroLeague route's shape,
│   │                             all-optional), LeagueFormatting (em dash for nil), LeaguePreviewData
│   ├── DesignSystem/           Palette, typography, spacing, and the small shared views:
│   │                             stat values, chips, sparkline, percentile bar, era badges,
│   │                             loading / error / empty / unavailable tiles.  PlatformShims and
│   │                             PlatformNavigation are where an iOS-versus-Mac difference is spelled
│   ├── Networking/             APIClient (actor), DemoAPIClient (actor), Endpoints, DiskCache
│   │                             (actor), DashboardService, SyncService, background refresh;
│   │                             LeagueRoute and LeagueClient (actor) for the league routes
│   ├── Dashboard/              The dashboard screen, the flowing grid, edit mode, the widget
│   │                             catalog sheet, the schema-driven config sheet, preset gallery,
│   │                             layout switcher, layout persistence, the EuroLeague club picker
│   ├── League/                 Views shared by the Mac screens and the four league widgets: chips,
│   │                             banners, source line, matchup comparison, form chart, defence
│   │                             bars, projection card, availability row.  Compiled for both platforms
│   ├── Widgets/                One view per widget kind (twenty), plus WidgetHost and the chart
│   │                             plumbing
│   ├── Screens/                Search, player detail, settings, onboarding
│   ├── Mac/                    macOS only: every file is wrapped whole in #if os(macOS).
│   │   ├── (root)              Scenes, the shared MacAppModel, bootstrap and the minute-by-minute
│   │   │                         refresh, sidebar, commands (menus), the server banner, table chrome,
│   │   │                         Copy Table, the Matchups & Defence dashboard
│   │   ├── Screens/            One group of files per sidebar screen, and its tables
│   │   ├── Settings/           The Settings window (General, Leagues, Model, About)
│   │   └── Windows/            The read-only Box Score and Club windows
│   └── Resources/
│       ├── Contracts/          metrics.json, widgets.json, presets.json — copies of contracts/
│       ├── Fixtures/           Golden payloads, which is what demo mode serves, including
│       │                         league_nba_*.json and league_el_*.json for the league screens
│       └── Assets.xcassets
└── NBAStatsTests/
    ├── TestSupport.swift       Bundle lookup, temp directories, the stub API client
    ├── CatalogTests.swift      The bundled contracts decode and agree with each other
    ├── FormattingTests.swift   Every MetricFormat, and the em-dash rule
    ├── JSONValueTests.swift    Round trips, Bool/Int/Double discrimination, nesting
    ├── LayoutTests.swift       Editing, persistence, migration
    ├── PayloadDecodingTests.swift  Every golden fixture, decoded into its payload type
    ├── ProjectionPayloadTests.swift The next-game projection: low ≤ mean ≤ high, every line
    │                                estimated, the correlated spread wider than the independent
    ├── ProjectionBoardPayloadTests.swift, FantasyPayloadTests.swift   The board and the toolkit
    ├── PlayerAvatarTests.swift      Initials and the deterministic monogram colour
    ├── DashboardStoreTests.swift   Presets, undo, deleting the last dashboard
    ├── DashboardServiceTests.swift Mixed results, request splitting, stale-while-revalidate
    ├── SyncServiceTests.swift      Polling, cache invalidation
    ├── LeaguePayloadDecodingTests.swift  Every league fixture decodes into its Swift type
    ├── LeagueRouteTests.swift      The URL of every league route under both prefixes; what is on
    │                                the wire (the key always, an Origin header never)
    ├── LeagueFormattingTests.swift Nil is an em dash; a fraction is a percentage; a score needs both
    ├── LeagueWidgetPayloadTests.swift  The four league widget fixtures decode as widget payloads
    └── Fixtures/               The golden payloads the tests decode
```

Dependency direction is one way: `Core` → `DesignSystem` / `Networking` → `League` →
`Dashboard` / `Widgets` → `Screens` → `App` and `Mac` (which sit on top; `HardwoodApp` is the only
file that names `MacScenes`). `ARCHITECTURE.md` §2 is the binding list of type signatures; anything
named there exists with exactly that shape.

---

## Running the tests

```bash
xcodebuild test -scheme Hardwood -destination "platform=iOS Simulator,name=iPhone 16"
xcodebuild test -scheme Hardwood -destination 'platform=macOS'
```

Or ⌘U in Xcode, with the destination set to an iPhone simulator or to **My Mac**. The same suite
runs on both: the test target builds for iOS, the iOS simulator and macOS. A test of iOS-only code
is fenced (`#if os(iOS)`, as the grid's size-class assertions are, with the same assertions run
against `columnSpan(isRegular:)` everywhere), and a test of Mac-only code is fenced
`#if os(macOS)`. The suite is pure unit tests — no network, no simulator UI automation — and
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

`sync_contracts.sh` also copies every recorded league response from
`contracts/fixtures/leagues/{nba,el}/` into both bundles as `league_<nba|el>_<name>.json`, and it
regenerates `contracts/theme.json` from `DesignSystem/Theme.swift`, `Typography.swift`,
`BroadsheetTheme.swift` and `Core/DashboardLayout.swift`. **Run it after editing any of those
four Swift files too**, or `check_contracts.py` check (i) fails: the web client's design tokens
are scraped from them.

`CatalogTests` asserts the counts the contract commits to — **62 metrics, 20 widget kinds (every
one of them has a Swift widget), 12 presets** — so a catalog change that the app has not been told
about fails the iOS suite too. The twenty are the sixteen the app began with plus the four league
widgets: `team_matchup`, `defense_by_position`, `availability_report`, `slate_projections`.

---

## How the dashboard loads

1. `DashboardStore` reads the layouts from `Layouts.json` in `Application Support/Hardwood/` on
   iOS and in `Application Support/com.hardwood.nbastats/` on the Mac (the Mac's `Hardwood` folder
   is the server's data folder, which `uninstall.sh --purge-data` deletes, so the dashboards must
   not live there), running each through `LayoutMigrator`: unknown widget kinds are dropped with a
   note the reader sees, unknown configuration keys are stripped, missing keys take the catalog
   default, and a document from a newer build is refused rather than corrupted.
2. `DashboardService` publishes whatever is in the disk cache immediately, marked stale.
3. One `POST /v1/dashboard/resolve` carries the whole layout — split into as few requests as the
   contract's 24-widget cap allows — and each result replaces its tile independently.
4. A failure never blanks a tile that already has numbers on it: it is marked stale instead.
   A widget that fails on its own renders an error inside its own tile with a retry button.
5. `SyncService` polls `/v1/sync` on foreground, on pull-to-refresh and, on iOS, from a
   `BGAppRefreshTask`. When the league's sync version moves it drops exactly the widget kinds the
   server names and asks the dashboard to re-resolve. macOS has no `BGTaskScheduler`: the
   `BackgroundRefresh` calls compile to no-ops there, and the Mac app instead checks the server once
   a minute while it is open (`MacAppModel`), and again when the Mac wakes and when the app comes to
   the front. The launchd server keeps collecting while the app is closed.

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

The draft board is a **table**, not a column of cards — a draft kit is a spreadsheet and gets
scanned like one. It reuses `GameLogWidget`'s idiom (pinned identity block, columns scrolling
under a fixed header, stacked fallback at accessibility sizes) with three of that file's defects
left behind: it does not rubber-band when the strip already fits, it does not hide the numbers
from VoiceOver, and its pinned name column shrinks against the measured container as type grows
instead of widening until the table has no room. Columns arrive as data, so adding one needs no
app release.

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

## One target, two platforms

`Hardwood` is one target that builds for iOS 17 and macOS 14, so a source file is compiled for
both, and the rule for platform-specific code is mechanical so that a mistake is found on Linux
rather than in Xcode.

* **Mac files** are everything under `NBAStats/Mac/`. Each opens with `#if os(macOS)` as its first
  non-comment line and ends with `#endif`, because the synchronized folder compiles every file for
  both platforms and there is no per-folder exclusion. AppKit and the Mac-only SwiftUI (`Table`,
  `.inspector`, `HSplitView`, `Window`, `Settings`, `openWindow`, `.navigationSubtitle`,
  `.onDeleteCommand`, `NSWorkspace`, `NSPasteboard`) may appear only there, or inside an
  `#if os(macOS)` region of a shared file.
* **Shared new files** (`Core/League*`, `Networking/League*`, `League/`, `DesignSystem/Platform*`,
  the four league widgets, `Dashboard/ClubFieldEditor.swift`) use only APIs that exist on both iOS
  17 and macOS 14.
* **Existing shared files** carry the platform difference at named seams and nowhere else:
  `DesignSystem/PlatformShims.swift` and `PlatformNavigation.swift` (view modifiers that are the
  iOS call on iOS and a Mac-appropriate one, or nothing, on the Mac: `hardwoodInlineTitle()`,
  `hardwoodURLField()`, `hardwoodGroupedListStyle()`, `HardwoodPushLink`, `hardwoodSearchField`,
  `hardwoodSheetFrame()`, and the `PlatformCopy` sentences); `Theme.swift` (`UIColor` or `NSColor`);
  `Typography.swift` (the Mac maps text styles to the point sizes the iOS layout was tuned for,
  because macOS has no Dynamic Type); `DashboardLayout.columnSpan(isRegular:)` and the grid's
  width-driven columns (two below 760 points, four above); `BackgroundRefresh` (real on iOS,
  no-ops on the Mac); and the macOS branch of `HardwoodApp`.
* **Nothing above macOS 14.0 and iOS 17.0.** No `@Observable`, `Tab(...)`,
  `.presentationSizing`, `.defaultLaunchBehavior` or any other macOS 15 API. Models are
  `ObservableObject` with `@Published`.
* **Mac tables**: at most ten columns, and every column sortable (`value:`); mixing sortable and
  unsortable columns in one `Table` does not compile. No `if` or `switch` inside a table's column
  builder (a different column set is a different `Table`).
* **Scenes do not inherit environment objects.** Every scene root in `Mac/MacScenes.swift` is handed
  the app environment, the catalog, the dashboard store and the Mac model itself; a view that reads
  an `@EnvironmentObject` it was not given crashes the first time it draws.
* **No non-Swift file under `NBAStats/` outside `Resources/`.** The folder is bundled whole.

`scripts/check_swift_portability.py` enforces all of this, plus exhaustive `switch` coverage of
`WidgetKind`, `ConfigFieldType` and `APIError.Code`, that every `*Widget.swift` file is the widget
of a real `WidgetKind`, the league payload rules above, and the Mac plist and project settings. It
is **not a compiler** and never says a file compiles: it prints `file:line: rule: message` and
exits 1 on a finding. Run it, with `scripts/check_contracts.py`, before handing a change over:

```bash
python3 scripts/check_swift_portability.py
python3 scripts/check_contracts.py
```

Two workflows compile this code on every push that touches it, each with the newest Xcode on
GitHub's macOS runner:

* `.github/workflows/ios.yml` builds the iOS destination on an iPhone simulator and runs the unit
  tests there.
* `.github/workflows/mac.yml` runs `scripts/make_dmg.sh` -- a Release build of the Mac app, signed to
  run locally, packed into `Hardwood.dmg` and attached to the run as a download -- then runs the
  unit tests on macOS.

Until October 2026 the iOS workflow could not fail: it piped xcodebuild into xcpretty without
`pipefail`, so a step took xcpretty's exit status, and for a while it was building nothing at all
because the runner had dropped the iOS platform its pinned Xcode needed. Both workflows now set
`pipefail`, and neither runs the portability script, which stays a local check.

---

## Known risks

Nothing in the repository has been through a Swift compiler. The checks above catch the mechanical
mistakes; these are what they cannot.

* **Xcode 16 is a hard requirement.** `@MainActor` inference from SwiftUI's protocol conformances
  needs the iOS 18 / macOS 15 SDK, so an older Xcode builds wrongly rather than slowly. The code
  itself targets iOS 17 and macOS 14.
* **The Mac app has never been built or run.** Expect compile errors on the first build, mostly
  small (a missing label, an optional, a modifier whose availability was misremembered). The
  expected route is `docs/MAC.md` §2 and §14: build in order, paste the text of the errors back.
* **Mac behaviours that could not be checked without a Mac,** each isolated to one file:
  toolbar merging when the dashboard and Player Search (which have their own navigation stacks) sit
  in the split view's detail column (`Mac/MacDetailRouter.swift`); `.inspector` attached inside that
  detail column; `InspectorCommands` toggling a per-screen inspector; `@ObservedObject` inside
  `Commands` (`Mac/MacCommands.swift`); an `.alert` with a text field in the sidebar
  (`Mac/MacSidebar.swift`). The fallback for each is written down in `docs/MAC.md` §13.
* **Signing.** An empty development team relies on "Sign to Run Locally"; `docs/MAC.md` §3 is the
  fallback if Xcode insists on a team.
* **Scene environment objects.** A missing injection is a runtime crash, not a build error; lint
  rule P11 and review are the guard.
* **Silent iOS regressions** from edits to shared files: build **Any iOS Simulator Device** after
  the Mac builds.
* **Payload drift.** The league payloads were written against recorded fixtures of the real
  backend, and all-optional decoding contains a surprise, but a live server can still send a shape
  nobody recorded. A field that cannot decode costs that field, shown as an em dash.
* **Xcode newer than 16** may report `Sendable` and main-actor annotation differences as warnings
  (the project builds in Swift 5 mode, where they are warnings).

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
* **Every widget and shared view has a `#Preview`**, and the previews run from
  `DashboardPreviewData` or a payload's own `preview` static, so they work with no server and no
  simulator data. The Mac's own screens (`Mac/`) have none: they need the app's environment objects
  and a server or the bundled fixtures, so run them (My Mac, with demo data if no server is up).
* **A league payload is all-optional.** Every property of every type in `Core/LeaguePayloads.swift`
  is an `Optional`, so one odd or missing field costs that field and never the whole screen; they
  use synthesized `Codable` only, take statistics as `Double?` (a JSON `6.0` into an `Int` throws),
  and keep timestamps as `String?`. A payload type whose keys drift from the fixtures the backend
  exports fails `scripts/check_swift_portability.py` (rule P14) before it reaches Xcode.
* **The app computes no statistic.** It fetches, filters, sorts by a served value and formats. A
  missing number is an em dash, never `0`; a percentage arrives as a fraction.
* **No betting vocabulary in anything the app displays.** A lint (rule P9) reads the string
  literals of `Mac/`, `League/` and the four league widgets.
