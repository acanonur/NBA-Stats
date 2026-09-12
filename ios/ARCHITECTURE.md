# Hardwood iOS — architecture and type surface

SwiftUI, iOS 17+, no third-party dependencies. Swift Charts and BackgroundTasks are used from
the system SDK. The app must build and run **with no backend reachable** by falling back to the
bundled golden fixtures (demo mode).

Everything in §2 is a **binding signature**: files are written in parallel by different authors,
so a type or function named here must exist with exactly this shape. Add to it freely; do not
rename or re-shape it.

---

## 1. Module map (folders under `ios/NBAStats/`)

| Folder | Owns | Depends on |
| --- | --- | --- |
| `Core/` | Value types decoded from the API, the bundled catalog, formatting, layout documents, layout persistence | nothing |
| `DesignSystem/` | Colors, typography, spacing, shared small views (badges, bars, sparkline, empty/error/loading states) | `Core` |
| `Networking/` | `APIClient`, endpoints, `DashboardService`, `SyncService`, `DiskCache`, demo-mode fixture loader | `Core` |
| `Dashboard/` | The dashboard screen, the grid, edit mode, the widget configuration editor, the preset gallery | `Core`, `DesignSystem`, `Networking` |
| `Widgets/` | One SwiftUI view per widget kind, plus the `WidgetHost` that renders a payload | `Core`, `DesignSystem` |
| `Screens/` | Search, player detail, settings, onboarding | all of the above |
| `App/` | `HardwoodApp`, `AppEnvironment`, root navigation, background refresh registration | all of the above |
| `Resources/` | `Contracts/*.json` (byte-identical copies of `contracts/`), `Fixtures/*.json`, `Assets.xcassets` | — |

Tests live in `ios/NBAStatsTests/`, with golden fixtures in `ios/NBAStatsTests/Fixtures/`.

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

Decoding note: `Int` is tried before `Double`, so `10` round-trips as `.int(10)` and the
fixtures re-encode byte-identically.

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

public enum ConfigFieldType: String, Codable, Hashable, Sendable {
    case season, `enum`, enumList, metric, metricList, player, playerList
    case team, teamList, subject, subjectList, int, double, bool, date
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
    case careerArc         = "career_arc"
}

public enum WidgetSize: String, Codable, Hashable, Sendable, CaseIterable {
    case small, medium, large
    public func columnSpan(horizontalSizeClass: UserInterfaceSizeClass?) -> Int
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
names exactly. The umbrella:

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
    case careerArc(CareerArcPayload)

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

Root is a `TabView`: **Dashboard**, **Search**, **Settings**. Background refresh registers
`com.hardwood.nbastats.refresh` (already declared in `Info.plist`).

---

## 3. Rules that are not negotiable

1. **Never render `0` for an unavailable stat.** `MetricAvailability.unavailable` renders an em
   dash with an explanatory tap target; `.estimated` renders with a dashed underline and an
   "est." badge; `.partial` renders with a caret and a footnote.
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
