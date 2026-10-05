# Hardwood iOS — architecture and type surface

SwiftUI, one multiplatform target (iOS 17+ and macOS 14+), no third-party dependencies. Swift
Charts is used from the system SDK, and BackgroundTasks on iOS only. The app must build and run
**with no backend reachable** by falling back to the bundled golden fixtures (demo mode). On the
Mac, which is the product, demo mode is an option rather than the default; §1.1 is the rule that
keeps one source tree compiling for both platforms, and
[`docs/MAC.md`](../docs/MAC.md) is the Mac app's manual.

Everything in §2 is a **binding signature**: files are written in parallel by different authors,
so a type or function named here must exist with exactly this shape. Add to it freely; do not
rename or re-shape it.

---

## 1. Module map (folders under `ios/NBAStats/`)

| Folder | Owns | Depends on |
| --- | --- | --- |
| `Core/` | Value types decoded from the API, the bundled catalog, formatting, layout documents, layout persistence; the league payload types, their preview data and their formatting (`League*.swift`, §2.11) | nothing |
| `DesignSystem/` | Colors, typography, spacing, shared small views (badges, bars, sparkline, empty/error/loading states); `PlatformShims` and `PlatformNavigation`, where an iOS-versus-Mac difference is spelled | `Core` |
| `Networking/` | `APIClient`, endpoints, `DashboardService`, `SyncService`, `DiskCache`, demo-mode fixture loader; `LeagueRoute` and `LeagueClient` for the league routes (§2.11) | `Core` |
| `League/` | Views shared by the Mac screens and the four league widgets: chips, banners, the source line, matchup comparison, form chart, defence bars and breakdown, the projection card, the availability row. Compiled for both platforms | `Core`, `DesignSystem` |
| `Dashboard/` | The dashboard screen, the grid, edit mode, the widget configuration editor, the preset gallery, the EuroLeague club picker | `Core`, `DesignSystem`, `Networking`, `League` |
| `Widgets/` | One SwiftUI view per widget kind (twenty), plus the `WidgetHost` that renders a payload | `Core`, `DesignSystem`, `League` |
| `Screens/` | Search, player detail, settings, onboarding | all of the above |
| `Mac/` | **macOS only** (§1.1, §2.12): the Mac shell: scenes, `MacAppModel`, sidebar, menus, bootstrap and polling, the screens and their tables, Settings, the detail windows | all of the above |
| `App/` | `HardwoodApp`, `AppEnvironment`, root navigation (the iOS `RootView`), background refresh registration | all of the above |
| `Resources/` | `Contracts/*.json` (byte-identical copies of `contracts/`), `Fixtures/*.json` (including `league_nba_*` and `league_el_*`), `Assets.xcassets` | — |

Tests live in `ios/NBAStatsTests/`, with golden fixtures in `ios/NBAStatsTests/Fixtures/`. The
two property lists live in `ios/` itself, outside the synchronized `NBAStats/` folder
(`Info.plist` for iOS, `Info-macOS.plist` for the Mac): a plist inside that folder would be copied
into the bundle as a resource while the build also produces it.

### 1.1 One target, two platforms: the rule for platform code

`Hardwood` builds for iOS 17 and macOS 14 from one source tree. The synchronized folder compiles
**every** file for **both** platforms, so where platform-specific code may live is a rule, and
`scripts/check_swift_portability.py` enforces it (rule IDs in brackets). It is a lint, not a
compiler.

1. **Mac files** are every file under `NBAStats/Mac/`. The first non-comment line is
   `#if os(macOS)` and the last is `#endif` [P1]. AppKit (`NS…`, `import AppKit`) and the Mac-only
   SwiftUI (`Table`, `TableColumn`, `.inspector`, `HSplitView`, `Window`, `Settings`, `openWindow`,
   `.navigationSubtitle`, `.onDeleteCommand`, `.datePickerStyle(.field)`) appear only in Mac files or
   inside an `#if os(macOS)` region [P2].
2. **Shared new files** (`Core/League*`, `Networking/League*`, `League/`, `DesignSystem/Platform*`,
   the four league widgets, `Dashboard/ClubFieldEditor.swift`) use only APIs that exist on both iOS
   17.0 and macOS 14.0.
3. **Existing shared files** differ by platform at named seams only: the shims (`hardwoodInlineTitle()`,
   `hardwoodCapitalizeWords()`, `hardwoodNeverCapitalize()`, `hardwoodURLField()`,
   `hardwoodSecretField()`, `hardwoodGroupedListStyle()`, `hardwoodNavigationPickerStyle()`,
   `hardwoodPagedTabStyle()`, `hardwoodSheetFrame(minWidth:minHeight:)`, `PlatformMetrics`,
   `PlatformCopy`), `HardwoodPushLink` / `hardwoodSearchField` / `hardwoodReadableWidth()` in
   `PlatformNavigation.swift`, `Theme.swift` (`UIColor` or `NSColor`), `Typography.swift` (the Mac maps
   each text style to the point size the iOS layout was tuned for), `WidgetSize.columnSpan(isRegular:)`
   and the grid's width-driven columns, `BackgroundRefresh`, `LayoutPersistence`'s folder name,
   `APIClient.makeUserAgent`, and the macOS branch of `HardwoodApp`. An iOS-only name (`UIKit`,
   `horizontalSizeClass`, `EditButton`, `.topBarTrailing`, `.textInputAutocapitalization`,
   `.keyboardType`, `.listStyle(.insetGrouped)`, `BGTask*`, and the rest of the list in the script)
   appears only inside `#if os(iOS)` / `#if canImport(UIKit)` or a shim's iOS branch [P3].
4. **Nothing above iOS 17 / macOS 14** [P4]: no `@Observable`, `@Bindable`, `Tab(…)`,
   `.presentationSizing`, `.defaultLaunchBehavior`, `.onScrollGeometryChange`, `UndoManager`
   registration, `MenuBarExtra`. Models are `ObservableObject` with `@Published`.
5. **A Mac `Table` has at most ten `TableColumn`s, and every one has `value:`** (mixing sortable
   and unsortable columns in one table does not compile), with no `if`, `switch` or `ForEach` in
   the column builder; a different column set is a different `Table` [P6]. No builder holds more
   than ten children.
6. **Every scene root in `Mac/MacScenes.swift` is handed the app environment, the catalog and the
   Mac model, and the `Window` and `Settings` roots the dashboard store as well** [P11] (the detail
   windows are given it too, so a view they embed later cannot crash on a missing one): scenes do
   not inherit environment objects, and a missing one is a crash at first draw.
7. **No betting vocabulary in any string `Mac/`, `League/` or the four league widgets display** [P9],
   and **no non-Swift file under `NBAStats/` outside `Resources/`** [P8].
8. **Every `*Widget.swift` is the widget of a real `WidgetKind`** [P7], and every exhaustive
   `switch` over `WidgetKind`, `ConfigFieldType` or `APIError.Code` without a `default` names every
   case [P5]. Adding a case to one of those enums therefore breaks the lint before it breaks Xcode.
9. **League payload types** (`Core/LeaguePayloads.swift`) are `public struct … Codable, Hashable,
   Sendable` with only `public var` stored properties, every one `Optional`, synthesized `Codable`
   only, no `Date`, statistic-named properties `Double?` [P10, P13], and every key of every league
   fixture is a property of the struct that decodes it [P14].
10. `ios/Info-macOS.plist` and the project's `[sdk=macosx*]` settings stay as the Mac build needs
    them [P12]: no iOS-only keys, `HardwoodDemoModeDefault` false, and no sandbox, no hardened
    runtime and no entitlements file (the app reads `hardwood.env`, talks plain HTTP to `127.0.0.1`
    and opens the server's log folder).

---

## 2. Binding type surface

### 2.1 `Core/Identifiers.swift`

```swift
public typealias PlayerID = Int
public typealias TeamID = Int
public typealias GameID = String
```

### 2.2 `Core/JSONValue.swift`

Widget configuration is schema-driven at runtime, so it is carried as JSON rather than as a
fixed struct.

```swift
public enum JSONValue: Codable, Hashable, Sendable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)
    case array([JSONValue])
    case object([String: JSONValue])
    case null

    public var stringValue: String? { get }
    public var intValue: Int? { get }
    public var doubleValue: Double? { get }   // returns a value for .int too
    public var boolValue: Bool? { get }
    public var arrayValue: [JSONValue]? { get }
    public var objectValue: [String: JSONValue]? { get }
    public var isNull: Bool { get }
}

extension JSONValue: ExpressibleByStringLiteral, ExpressibleByIntegerLiteral,
                     ExpressibleByFloatLiteral, ExpressibleByBooleanLiteral {}
```

Decoding note: `Int` is tried before `Double`, so `10` round-trips as `.int(10)` and a decoded
fixture re-encodes to an *equal* value — not necessarily to identical bytes. An integral JSON
number normalises to `.int` however it was written, so the `"plus_minus": -5.0` in
`contracts/fixtures/widget_game_log.json` comes back as `.int(-5)` and re-encodes as `-5`.
Compare fixtures by decoded value, never by bytes.

### 2.3 `Core/Models.swift` — decoded straight from the API (`contracts/CONTRACT.md` §2)

All of these are `Codable, Hashable, Sendable`. **The JSON is lowerCamelCase and the property
names match it exactly**, so no `keyDecodingStrategy` is set on the decoder.

```swift
public struct PlayerRef: Codable, Hashable, Sendable, Identifiable {
    public let playerId: PlayerID
    public let name: String
    public let firstName: String?
    public let lastName: String?
    public let teamId: TeamID?
    public let teamAbbr: String?
    public let position: String?
    public let jersey: String?
    public let headshotUrl: String?
    public let isActive: Bool?
    public var id: PlayerID { playerId }
}

public struct TeamRef: Codable, Hashable, Sendable, Identifiable {
    public let teamId: TeamID
    public let abbr: String
    public let name: String
    public let city: String?
    public let nickname: String?
    public let conference: String?
    public let division: String?
    public var id: TeamID { teamId }
}

public enum MetricAvailability: String, Codable, Hashable, Sendable {
    case full, estimated, partial, unavailable
    /// True when the value may be compared against modern numbers without a caveat.
    public var isTrustworthy: Bool { self == .full }
}

public struct MetricValue: Codable, Hashable, Sendable {
    public let metric: String
    public let value: Double?
    public let displayValue: String
    public let rank: Int?
    public let percentile: Double?
    public let leagueAverage: Double?
    public let delta: Double?
    public let isEstimated: Bool?
    public let availability: MetricAvailability
}

public struct MetricDescriptor: Codable, Hashable, Sendable, Identifiable {
    public struct Availability: Codable, Hashable, Sendable {
        public let seasonFrom: String
        public let perGameFrom: String
        public let seasonLevelOnly: Bool
        public let estimatedBefore: String?
    }
    public struct Domain: Codable, Hashable, Sendable {
        public let min: Double
        public let max: Double
    }
    public let key: String
    public let name: String
    public let shortName: String
    public let category: String
    public let format: MetricFormat
    public let higherIsBetter: Bool
    public let scope: [String]
    public let availability: Availability
    public let domain: Domain?
    public let glossary: String
    public var id: String { key }
}

public enum MetricFormat: String, Codable, Hashable, Sendable {
    case integer, decimal1, decimal2, percent1, percent2, rating1, plusMinus1, minutes
}

public enum GameStatus: String, Codable, Hashable, Sendable { case scheduled, live, final }

public struct GameRef: Codable, Hashable, Sendable, Identifiable {
    public let gameId: GameID
    public let date: String            // ISO-8601 calendar date, US Eastern
    public let season: String?
    public let seasonType: String?
    public let home: TeamRef
    public let away: TeamRef
    public let homePts: Int?
    public let awayPts: Int?
    public let status: GameStatus
    public let period: Int?
    public let clock: String?
    public let finalizedAt: String?
    public var id: GameID { gameId }
}
```

### 2.4 `Core/Catalog.swift`

Loads the bundled `Resources/Contracts/*.json` and, when the server offers newer ones through
`/v1/meta`, replaces them in memory.

```swift
public struct WidgetSpec: Codable, Hashable, Sendable, Identifiable {
    public struct ConfigField: Codable, Hashable, Sendable, Identifiable {
        public let key: String
        public let type: ConfigFieldType
        public let label: String
        public let required: Bool
        public let `default`: JSONValue?
        public let options: [String]?
        public let help: String?
        public let min: Double?
        public let max: Double?
        public let minItems: Int?
        public let maxItems: Int?
        public let metricScope: String?
        public let dependsOn: String?
        public var id: String { key }
    }
    public let kind: WidgetKind
    public let name: String
    public let summary: String
    public let icon: String                // SF Symbol name
    public let sizes: [WidgetSize]
    public let defaultSize: WidgetSize
    public let minRefreshSeconds: Int
    public let availableFrom: String?
    public let config: [ConfigField]
    public var id: WidgetKind { kind }
}

public enum ConfigFieldType: String, Codable, Hashable, Sendable, CaseIterable {
    case season, `enum`, enumList, metric, metricList, player, playerList
    case team, teamList, subject, subjectList, int, double, bool, date
    /// A EuroLeague club code: three capital letters. Used by the four league widgets. A catalog
    /// field type the app does not know is silently dropped from the config sheet, so this case
    /// is not optional.
    case club
}

@MainActor
public final class Catalog: ObservableObject {
    public static let shared: Catalog

    @Published public private(set) var metrics: [MetricDescriptor]
    @Published public private(set) var widgets: [WidgetSpec]
    @Published public private(set) var presets: [DashboardLayout]
    public private(set) var eraBoundaries: [EraBoundary]
    public private(set) var categories: [MetricCategory]

    public func metric(_ key: String) -> MetricDescriptor?
    public func metrics(inScope scope: String) -> [MetricDescriptor]
    public func widget(_ kind: WidgetKind) -> WidgetSpec?
    public func defaultConfig(for kind: WidgetKind) -> [String: JSONValue]
    /// Fills in catalog defaults for missing keys and strips unknown ones.
    public func normalizedConfig(for kind: WidgetKind, config: [String: JSONValue]) -> [String: JSONValue]
    public func preset(_ key: String) -> DashboardLayout?

    /// Formats a raw value using the metric's format; `nil` renders as an em dash.
    public func format(_ key: String, _ value: Double?) -> String
    public func availability(for key: String, season: String, perGame: Bool) -> MetricAvailability

    public func update(metrics: [MetricDescriptor]?, widgets: [WidgetSpec]?, presets: [DashboardLayout]?)
}

public struct EraBoundary: Codable, Hashable, Sendable {
    public let season: String
    public let label: String
    public let detail: String?
}
public struct MetricCategory: Codable, Hashable, Sendable, Identifiable {
    public let key: String
    public let name: String
    public var id: String { key }
}
```

`Catalog` must never crash on a malformed bundle: a decode failure logs and leaves the
previous (or empty) catalog in place.

### 2.5 `Core/DashboardLayout.swift` — the editable document (`contracts/CONTRACT.md` §5)

```swift
public enum WidgetKind: String, Codable, Hashable, Sendable, CaseIterable {
    case statTile          = "stat_tile"
    case playerSnapshot    = "player_snapshot"
    case leaderboard       = "leaderboard"
    case gameLog           = "game_log"
    case trendChart        = "trend_chart"
    case fourFactors       = "four_factors"
    case shotProfile       = "shot_profile"
    case comparison        = "comparison"
    case scoreboard        = "scoreboard"
    case dailyMovers       = "daily_movers"
    case teamEfficiency    = "team_efficiency"
    case nextGameProjection = "next_game_projection"
    case projectionBoard   = "projection_board"
    case fantasyDraftBoard = "fantasy_draft_board"
    case fantasyTrade      = "fantasy_trade"
    case careerArc         = "career_arc"
    // The four league widgets (§2.9, §2.11). Twenty kinds in all, every one with a Swift widget.
    case teamMatchup       = "team_matchup"
    case defenseByPosition = "defense_by_position"
    case availabilityReport = "availability_report"
    case slateProjections  = "slate_projections"
}

public enum WidgetSize: String, Codable, Hashable, Sendable, CaseIterable {
    case small, medium, large
    /// 2-column grid below, 4-column above. A plain `Bool` because `UserInterfaceSizeClass` does
    /// not exist on macOS, where the grid's columns follow the window's width (two below 760
    /// points, four above) instead of a size class.
    public func columnSpan(isRegular: Bool) -> Int
    #if os(iOS)
    public func columnSpan(horizontalSizeClass: UserInterfaceSizeClass?) -> Int
    #endif
    public var estimatedHeight: CGFloat { get }   // 148 / 232 / 360
}

public struct DashboardWidget: Codable, Hashable, Sendable, Identifiable {
    public var id: String
    public var kind: WidgetKind
    public var title: String?
    public var size: WidgetSize
    public var config: [String: JSONValue]
    public init(id: String = UUID().uuidString, kind: WidgetKind, title: String? = nil,
                size: WidgetSize, config: [String: JSONValue])
}

public struct DashboardLayout: Codable, Hashable, Sendable, Identifiable {
    public var id: String
    public var name: String
    public var icon: String
    public var accent: AccentName
    public var schemaVersion: Int
    public var isPreset: Bool
    public var presetKey: String?
    public var tagline: String?
    public var createdAt: Date?
    public var updatedAt: Date?
    public var widgets: [DashboardWidget]

    public static let currentSchemaVersion = 1

    /// A user-editable copy of a preset: new id, isPreset false, presetKey retained.
    public func makeEditableCopy(named: String? = nil) -> DashboardLayout
    public mutating func move(fromOffsets: IndexSet, toOffset: Int)
    public mutating func remove(widgetID: String)
    public mutating func append(_ widget: DashboardWidget)
    public mutating func replace(_ widget: DashboardWidget)
}

public enum AccentName: String, Codable, Hashable, Sendable, CaseIterable {
    case orange, indigo, teal, red, amber, green, blue, purple, graphite
}
```

**Migration.** `LayoutMigrator.migrate(_ raw: Data) throws -> DashboardLayout` drops unknown
widget kinds (recording them in `migrationNotes`), strips unknown config keys, fills missing
required keys from the catalog, and refuses (throws `.tooNew`) a layout whose `schemaVersion`
exceeds `currentSchemaVersion`.

### 2.6 `Core/Payloads.swift` — one struct per widget kind (`contracts/CONTRACT.md` §4)

Every struct is `Codable, Hashable, Sendable` and its properties mirror the contract's key
names exactly. `next_game_projection` is the one payload that lives in its own file,
`Core/ProjectionPayload.swift`, because it is seven types rather than one —
`NextGameProjectionPayload`, `ProjectionGame`, `ProjectedMinutes`, `ProjectedLine`,
`ProjectionFactor`, `ProjectionCombo`, `ProjectionMethod` — and because it carries an invariant
the other payloads do not: a `ProjectedLine` is never `.full`, whatever the server said
(`docs/PROJECTION.md` §7 rule 5). The umbrella:

```swift
public enum WidgetPayload: Hashable, Sendable {
    case statTile(StatTilePayload)
    case playerSnapshot(PlayerSnapshotPayload)
    case leaderboard(LeaderboardPayload)
    case gameLog(GameLogPayload)
    case trendChart(TrendChartPayload)
    case fourFactors(FourFactorsPayload)
    case shotProfile(ShotProfilePayload)
    case comparison(ComparisonPayload)
    case scoreboard(ScoreboardPayload)
    case dailyMovers(DailyMoversPayload)
    case teamEfficiency(TeamEfficiencyPayload)
    case nextGameProjection(NextGameProjectionPayload)
    case projectionBoard(ProjectionBoardPayload)
    case fantasyDraftBoard(FantasyDraftBoardPayload)
    case fantasyTrade(FantasyTradePayload)
    case careerArc(CareerArcPayload)
    // The four league widgets' payloads ARE the league route objects (`Core/LeaguePayloads.swift`,
    // §2.11), so a tile and the screen it summarises decode the same type.
    case teamMatchup(LeagueMatchup)
    case defenseByPosition(LeagueDefenseDocument)
    case availabilityReport(LeagueAvailabilityReport)
    case slateProjections(LeagueSlateProjections)

    public var kind: WidgetKind { get }
    /// Decodes the untyped `payload` object from a resolve result into the right case.
    public static func decode(kind: WidgetKind, from decoder: Decoder, key: CodingKey) throws -> WidgetPayload
}

public struct SubjectRef: Codable, Hashable, Sendable {
    public let type: String          // "player" | "team"
    public let player: PlayerRef?
    public let team: TeamRef?
    public var displayName: String { get }
}
```

### 2.7 `Networking/`

```swift
public struct APIConfiguration: Hashable, Sendable {
    public var baseURL: URL
    public var apiKey: String?
    public var timeout: TimeInterval        // default 20
    public static var fromInfoPlist: APIConfiguration
}

public enum APIError: Error, Hashable, Sendable {
    case offline
    case timeout
    case server(code: String, message: String, status: Int, recoverable: Bool)
    case decoding(String)
    case demoModeMissingFixture(String)
    public var userMessage: String { get }
    public var isRetryable: Bool { get }
}

public protocol APIClientProtocol: Sendable {
    func meta() async throws -> MetaResponse
    func presets() async throws -> PresetsResponse
    func health() async throws -> HealthResponse
    func sync(since: Int?) async throws -> SyncResponse
    func searchPlayers(query: String, limit: Int) async throws -> PlayerSearchResponse
    func player(_ id: PlayerID) async throws -> PlayerDetailResponse
    func teams() async throws -> TeamsResponse
    func resolve(_ request: DashboardResolveRequest) async throws -> DashboardResolveResponse
}

public actor APIClient: APIClientProtocol { public init(configuration: APIConfiguration, session: URLSession = .shared) }

/// Serves the bundled golden fixtures so the app is fully usable with no server.
public actor DemoAPIClient: APIClientProtocol { public init(bundle: Bundle = .main) }
```

`DashboardResolveRequest` / `DashboardResolveResponse` / `ResolveResult` mirror
`contracts/CONTRACT.md` §3 exactly, including `status` as
`public enum ResolveStatus: String, Codable { case ok, unchanged, partial, error }`.

```swift
@MainActor
public final class DashboardService: ObservableObject {
    public init(client: APIClientProtocol, cache: DiskCache, catalog: Catalog)
    /// Returns cached payloads immediately, then refreshes — stale-while-revalidate.
    public func load(layout: DashboardLayout, context: ResolveContext, force: Bool) async
    @Published public private(set) var results: [String: WidgetState]   // keyed by widget id
    @Published public private(set) var lastUpdated: Date?
    @Published public private(set) var dataThrough: String?
    @Published public private(set) var isRefreshing: Bool
}

public enum WidgetState: Hashable, Sendable {
    case loading
    case loaded(WidgetPayload, availability: MetricAvailability, notes: [String], stale: Bool)
    case failed(APIError)
    case unavailable(reason: String)
}

@MainActor
public final class SyncService: ObservableObject {
    public init(client: APIClientProtocol, defaults: UserDefaults = .standard)
    @Published public private(set) var syncVersion: Int
    @Published public private(set) var dataThrough: String?
    @Published public private(set) var lastCheck: Date?
    @Published public private(set) var newlyFinalizedGames: [GameRef]
    /// Returns true when something changed and the dashboard should re-resolve.
    @discardableResult public func check() async -> Bool
    public func registerBackgroundTasks()
    public func scheduleNextRefresh()
}
```

`DiskCache` is an actor storing `Data` under a SHA-256 of widget kind + normalized config +
resolved context, with per-entry TTL from `ttlSeconds`, an LRU byte cap (25 MB default), and
`purgeKinds(_:)` driven by `SyncResponse.invalidate`.

### 2.8 `Dashboard/`

```swift
@MainActor
public final class DashboardStore: ObservableObject {
    public init(persistence: LayoutPersisting, catalog: Catalog)
    @Published public private(set) var layouts: [DashboardLayout]
    @Published public var selectedLayoutID: String?
    @Published public var isEditing: Bool
    public var selectedLayout: DashboardLayout? { get }

    public func addLayout(fromPreset key: String) -> DashboardLayout
    public func addBlankLayout(named: String) -> DashboardLayout
    public func duplicate(_ layout: DashboardLayout)
    public func delete(_ layoutID: String)
    public func rename(_ layoutID: String, to name: String)
    public func update(_ layout: DashboardLayout)          // persists and bumps updatedAt
    public func resetToPreset(_ layoutID: String)
    public func addWidget(_ widget: DashboardWidget, to layoutID: String)
    public func removeWidget(_ widgetID: String, from layoutID: String)
    public func moveWidgets(in layoutID: String, fromOffsets: IndexSet, toOffset: Int)
    public func resizeWidget(_ widgetID: String, to size: WidgetSize, in layoutID: String)
    public func updateWidgetConfig(_ widgetID: String, config: [String: JSONValue], title: String?, in layoutID: String)
    public func undoLastEdit()                             // one level of undo while editing
}

public protocol LayoutPersisting: Sendable {
    func loadAll() throws -> [DashboardLayout]
    func save(_ layouts: [DashboardLayout]) throws
}
public struct FileLayoutPersistence: LayoutPersisting {}   // JSON in Application Support, atomic writes
```

Views: `DashboardScreen`, `DashboardGrid`, `WidgetContainer` (chrome: title, menu, era badge,
staleness dot), `EditingToolbar`, `WidgetCatalogSheet`, `WidgetConfigSheet`, `PresetGallery`,
`LayoutSwitcher`.

**Editing behaviour that must work:** long-press or the Edit button enters edit mode; widgets
gain a delete affordance and a drag handle; drag reorders with animation; a size control cycles
small → medium → large; tapping a widget opens the config sheet built dynamically from
`WidgetSpec.config`; the `+` button opens the catalog sheet grouped by category; edits persist
immediately; Done exits. Editing a preset silently creates an editable copy the first time.

### 2.9 `Widgets/`

```swift
public struct WidgetHost: View {
    public init(widget: DashboardWidget, state: WidgetState, isEditing: Bool)
}
```

One view per kind, each taking its own payload type and a `WidgetSize`, each with an Xcode
preview driven by a bundled fixture. Charts use Swift Charts (`import Charts`).

**The fantasy widgets.** `FantasyDraftBoardWidget` and `FantasyTradeWidget` decode
`Core/FantasyPayload.swift`. `FantasyCategory` is the shared vocabulary — short and long labels,
and `isNegative(_:)`, which every piece of category wording must consult because turnovers are
sign-flipped upstream. See [`docs/FANTASY.md`](../docs/FANTASY.md).

**The broadsheet presentation.** `DashboardLayout.presentation` is `tiles` or `broadsheet`, and
`DashboardScreen` pushes it down as `\.isBroadsheet`. On a broadsheet page `DashboardGrid`
collapses to one column, `WidgetContainer` drops the card entirely (no fill, border, radius or
reserved height) and sets the title as a kicker, and a widget that has a broadsheet variant picks
it up from the environment. `DesignSystem/BroadsheetTheme.swift` holds the tokens — deliberately
a separate namespace from `Palette`, so a tile widget cannot half-adopt the aesthetic by reaching
for one colour. `ProjectionBoardWidget` is the only widget with a variant today; every other kind
renders unchanged. See [`docs/BROADSHEET.md`](../docs/BROADSHEET.md).

**The four league widgets.** `TeamMatchupWidget`, `DefenseByPositionWidget`,
`AvailabilityReportWidget` and `SlateProjectionsWidget` are shared files (compiled for both
platforms), each a `public struct … : View` with an explicit `public init(payload:size:)` whose
payload is the league route object itself (`LeagueMatchup`, `LeagueDefenseDocument`,
`LeagueAvailabilityReport`, `LeagueSlateProjections`). They contain no `Table`, no `.inspector`, no
AppKit and no `#if`. They draw with the `League/` views at `LeagueDensity.compact`, so a tile and
the screen it summarises show the same number written the same way, and **a tile never computes**:
every figure is a payload field run through `LeagueFormatting` (a missing one is an em dash).
Each has a `league` setting (`nba` or `euroleague`) and `LeagueConfigVisibility` (in
`Dashboard/ClubFieldEditor.swift`) shows only the settings that apply to that league.
`WidgetHost` gives the slate tile the "Projected, not recorded" footnote and the other three an
"Estimated: some of these numbers are modelled or come from workbook estimates" one.

### 2.10 `App/`

```swift
@main struct HardwoodApp: App

@MainActor
public final class AppEnvironment: ObservableObject {
    public static func live() -> AppEnvironment
    public static func demo() -> AppEnvironment
    public let catalog: Catalog
    public let client: APIClientProtocol
    public let dashboard: DashboardService
    public let sync: SyncService
    public let store: DashboardStore
    @Published public var favoritePlayerID: PlayerID?
    @Published public var favoriteTeamID: TeamID?
    @Published public var isDemoMode: Bool
}
```

`AppEnvironment` also holds `let league: LeagueClient` (§2.11): **internal**, not public, built
inside the existing initialiser so no public signature changed, and kept in step by
`applyServerSettings`, `applyServerSettingsAndWait` and the demo-mode switch.

**The root differs by platform, and `HardwoodApp.body` is where.**

* **iOS:** a `WindowGroup` holding `RootView`, a `TabView`: **Dashboard**, **Search**, **Settings**.
  Background refresh registers `com.hardwood.nbastats.refresh` (declared in `Info.plist`) and the
  nightly identifier, through `BGTaskScheduler`.
* **macOS:** `MacScenes(environment:model:)` (§2.12), with a `MacAppModel` made beside the
  environment as a second `@StateObject`. `BackgroundRefresh` compiles to no-ops there
  (`BackgroundTasks` is imported on iOS only): a Mac has no `BGTaskScheduler`, the open app checks
  the server once a minute, and the launchd server collects while the app is closed.

### 2.11 The league data layer (shared; `Core/League*`, `Networking/League*`)

The NBA and the EuroLeague are served by the same backend under two route prefixes (`/v1` and
`/v1/el`) with parallel payloads (`contracts/CONTRACT.md` §1, "Leagues and prefixes", and the two
"League routes" parts of §3; recorded examples in `contracts/fixtures/leagues/{nba,el}/`). The
league screens and the four league widgets read them through one small layer.

```swift
enum LeagueKey: String, CaseIterable, Identifiable, Hashable, Sendable, Codable {
    case nba, euroleague
    var displayName: String { get }            // "NBA", "EuroLeague"
    var fallbackPrefix: [String] { get }       // [] and ["el"], until /v1/leagues has loaded
    var fixturePrefix: String { get }          // "league_nba_", "league_el_"
    var regulationMinutes: Int { get }         // 48, 40
}

struct LeagueRoute: Hashable, Sendable {       // a request before it has a host; half of a .task(id:)
    var league: LeagueKey
    var path: [String]                         // after the league prefix
    var query: [LeagueQueryItem]
    var fixture: String                        // the demo fixture that answers it
    func url(base: URL, prefix: [String]) -> URL
}
enum LeagueRoutes { /* one static function per route; nil or empty query values are omitted */ }

actor LeagueClient {                            // reached as AppEnvironment.league
    func get<T: Decodable & Sendable>(_ type: T.Type, _ route: LeagueRoute) async throws -> T
    func send(method: String, route: LeagueRoute, body: Data?) async throws -> Data
    func updatePrefixes(_ leagues: [LeagueInfo])
    func updateConfiguration(_ configuration: APIConfiguration)
    func setDemoMode(_ enabled: Bool)
}
```

* **Why a second client.** `APIClientProtocol`'s surface is depended on by the dashboard, the sync
  service and the test stub; adding a generic fetch to it would change every conformer. The league
  client is a separate actor and the protocol is untouched. `get` is the one generic in the data
  layer and callers always pass the type explicitly.
* **The paths and query names were read from the backend**, not from a design: where the leagues
  spell a parameter differently (`teamId` against `clubCode`, `date` against `round`) the routes
  spell each league's way and a screen never sees the difference. `LeagueRouteTests` pins the URL of
  every route under both prefixes.
* **Writes** (`POST availability`, `DELETE availability/{id}`, `POST news/links`) go through `send`,
  are never retried and never coalesced (an append-only status posted twice would be recorded
  twice), and are refused in demo mode (`demo_read_only`).
* **What is on the wire.** `X-API-Key` on every request when a key is configured (with a key set the
  server asks for it on reads as well as writes), and never an `Origin` header or any custom "I am
  the Mac app" header: the server treats a request with an `Origin` as a browser's, and a header any
  page can send is not a credential. `URLSession` is ephemeral (no cookies, no disk cache).
* **Demo mode** answers from `league_<nba|el>_<name>.json` in the bundle; a missing fixture is
  `APIError.demoModeMissingFixture`, which a screen shows as "Demo data for this screen isn't
  bundled yet", not as an error.
* **Payload rules** (`Core/LeaguePayloads.swift`, enforced by P10, P13 and P14 in §1.1): only the
  payload types are `public` (the public `WidgetPayload` needs them); everything else, the routes,
  the client and the formatting, is internal, and nothing public may take an internal type. Every
  stored property is `Optional`; statistics are `Double?` (a JSON `6.0` into an `Int` throws);
  timestamps and days are `String?` (a `Date` property would let one odd literal cost the payload);
  enum-like values (status, band, state, kind) are `String?` and mapped to words by
  `LeagueFormatting`, and an unknown value is shown as sent. Names carry `League` (both leagues) or
  `El` (EuroLeague only) so they never shadow `TeamRef`, `GameRef`, `PlayerRef`, `GameStatus`.
* **`LeagueFormatting`** is pure Foundation and returns `Formatting.emDash` for every nil: `number`,
  `signed`, `percent` (takes a fraction), `integer`, `score` (only when both sides are present),
  `record`, `tipoff` (the reader's time zone) and `berlinTime`, `age` (from a source's own date),
  and the word tables (`statusWord`, `bandWord`, `sourceKindWord`, `stateWord`, …).
* **No league payload is ever computed on the client.** An average, a margin, an adjusted figure or
  a league reference is a field of the payload.

### 2.12 The Mac shell (`Mac/`, macOS only)

```
HardwoodApp ─ MacScenes ─ Window("Hardwood", id: "main") ─ MacRootView
                        │     NavigationSplitView { MacSidebar } detail: { MacDetailRouter ─ MacScreenHost }
                        ├ WindowGroup("Box Score", for: LeagueLink.self)  ─ MacBoxScoreWindow
                        ├ WindowGroup("Club", for: LeagueLink.self)       ─ MacClubWindow
                        └ Settings ─ MacSettingsView (General | Leagues | Model | About)
```

* **One main window**, `Window` rather than `WindowGroup`: `DashboardService` holds one layout and
  one generation counter and `DashboardStore.isEditing` is app-wide, so two main windows would fight
  over the same state. Closing it leaves the app running; Window ▸ Hardwood reopens it. The two
  detail windows are read-only and call `environment.league` only, never `DashboardService`.
* **`MacAppModel`** (`@MainActor final class … : ObservableObject`) is the one shared model. It
  holds no statistic. It holds what must be the same everywhere: `selection: MacSelection?`
  (`.dashboard(id)` or `.screen(MacScreen)`), `league`, `isInspectorShown`, `leagues` (the last
  `/v1/leagues`), `elMeta` (rounds and clubs for the pickers), `nbaTeams`, `server: MacServerState`
  (`unknown`, `reachable`, `demo`, `unreachable(String)`, `keyRejected(String)`), the small "go there"
  requests (`pendingAction`, `pendingNavigation`, `step`), `favoriteClubCode`, and
  `generations: [LeagueKey: Int]`.
* **`generation(for:)` is the only reload signal.** A screen puts it in its `.task(id:)` key beside
  its route; the bootstrap and polling code bumps it when `/v1/leagues` says a league's cursor moved,
  on a manual refresh, on wake and after a successful write. Nothing else tells a screen to reload.
* **Bootstrap and polling** (`MacBootstrap.swift`): read the API key from the installer's
  `hardwood.env` when none is stored (the first `HARDWOOD_API_KEY=` line decides, exactly as the
  server reads it; never shown, never written back), start the shared services, check `/v1/health`,
  load `/v1/leagues`, `/v1/el/meta` and the NBA team list, make the Mac's first dashboard once, then
  tick every 60 seconds (and at once on wake and on becoming active; every fifth tick reloads both
  leagues). Nothing ticks in demo mode. The wake notification is posted on
  `NSWorkspace.shared.notificationCenter`, not on `NotificationCenter.default`, where it never
  arrives.
* **Every screen takes the same shape:** `struct MacXScreen: View` with `init(league: LeagueKey)`
  (`MacStartHereScreen` takes nothing; `MacBoxScoreView` takes `league:gameId:`,
  `MacTeamViewContent` `league:teamId:`; the two windows take `link: LeagueLink?`), and reads
  `@EnvironmentObject` `AppEnvironment`, `MacAppModel`, `DashboardStore` and `Catalog`. A screen is a
  `MacFilterBar`, a freshness bar, the content, a notes footer, a title and subtitle, a toolbar and,
  where the screen has detail to show, an `.inspector`. `MacScreenHost` switches on `MacScreen` and `.id`s the screen with
  `MacScreenIdentity(screen:league:)`, so changing league throws a screen's `@State` away: one
  league's numbers never sit under the other's name.
* **The load pattern** (one per screen, no generic view model): `@State` `payload`, `failure`
  (never `error`, which `catch` shadows), `isLoading` and `loadedRoute`; `.task(id: MacLoadKey(route:
  generation:))`; a request that differs from `loadedRoute` clears the payload before the fetch;
  `Task.isCancelled` is checked after the `await` because `APIError.from(urlError:)` maps a
  cancelled request to `.offline`. A failure with no payload is `MacLoadFailureView`; with one it is a
  `MacStaleStrip` above the last good payload.
* **The table pattern:** rows are built once, outside `body`, into a concrete `Identifiable, Hashable`
  struct of display strings plus non-optional sort keys (`LeagueFormatting.sortKey` turns nil into
  `-Double.infinity`); each cell is one helper view (`NumCell`, `TextCell`, `TeamCell`,
  `StatusChipCell`); key paths are written with their root type; at most ten columns, every one
  sortable (§1.1). Row context menus use `.contextMenu(forSelectionType:menu:primaryAction:)`.
  "Copy Table" (`MacTableExport`) puts the displayed strings on the pasteboard as tab-separated text.
* **Menus** (`MacCommands`) observe both the model and the dashboard store; every action guards
  itself, so a momentarily stale menu state is cosmetic. A menu item that acts on one screen
  (`Record Availability Status…`, `Paste Headline Link…`) goes to that screen first and leaves a
  `MacPendingAction` for it to consume. There is no `.focusedValue` plumbing.
* **Dashboards on the Mac.** `DashboardScreen` is unchanged in the detail column. `MacStarterLayout`
  makes *Matchups & Defence* (seven tiles across both leagues) once, behind
  `hardwood.mac.starterSeeded`, and writes the favourite club into its EuroLeague matchup tile only
  while that tile has none. The dashboards file lives in `Application Support/com.hardwood.nbastats/`,
  not `Application Support/Hardwood/`, because the latter is the launchd server's data folder.
* **UserDefaults keys all start `hardwood.`**: `hardwood.mac.selection`, `hardwood.mac.league`,
  `hardwood.mac.welcomeDone`, `hardwood.mac.starterSeeded`, `hardwood.mac.starterLayoutID`,
  `hardwood.favorite.clubCode`, plus the existing `hardwood.api.*` and `hardwood.favorite.*`.

---

## 3. Rules that are not negotiable

1. **Never render `0` for an unavailable stat.** `MetricAvailability.unavailable` renders an em
   dash with an explanatory tap target; `.estimated` renders with a dashed underline and an
   "est." badge; `.partial` renders with a caret and a footnote. The four league kinds
   (`teamMatchup`, `defenseByPosition`, `availabilityReport`, `slateProjections`) are the one
   exception to the era tile: for them the server's `unavailable` means "nothing yet" (no games
   this season, no report published) and comes with a payload, so `DashboardService` keeps the
   payload and the widget draws its own empty state (`DashboardService.drawsItsOwnEmptyState`).
2. **The app works offline.** Every screen renders from cache or bundled fixtures; a failed
   refresh shows a stale badge, never an empty screen.
3. **One failing widget never breaks the dashboard.** `WidgetState.failed` renders inside the
   tile with a retry button.
4. **No force-unwrapping** of decoded data, no `try!`, no `fatalError` outside `preconditionFailure`
   in genuinely unreachable switch defaults.
5. **All UI state mutation is on `@MainActor`.** Networking types are actors.
6. Accessibility: every tile has an `accessibilityLabel` reading the metric name and value;
   Dynamic Type is respected; charts carry `accessibilityChartDescriptor` or at minimum a
   descriptive label.
7. Dark mode and light mode are both first-class; colors come from `DesignSystem`, never
   literal `Color(red:green:blue:)` inside a view.
8. **The app computes no statistic.** It fetches, filters, sorts by a served value and formats. A
   missing number is an em dash, never `0`; a percentage arrives as a fraction in `[0, 1]`.
9. **Platform code lives only where §1.1 says.** A Mac file is fenced whole in `#if os(macOS)`; a
   shared file differs by platform only at a named seam; nothing above iOS 17 / macOS 14.
10. **Nothing betting-shaped exists in the code, the payloads or the strings the app displays**: no
    line, price, probability of winning, edge, lean or pick. The server has no field or route
    parameter for one, and P9 reads the app's strings.
