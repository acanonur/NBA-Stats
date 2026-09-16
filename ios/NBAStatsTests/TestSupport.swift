import Foundation
import XCTest
@testable import Hardwood

// MARK: - Bundles and fixtures

/// Where the tests find the JSON they read.
///
/// Two bundles are in play. The **app** bundle carries `Resources/Contracts/*.json` and
/// `Resources/Fixtures/*.json`, and since these are host-application unit tests, `Bundle.main`
/// *is* the app bundle. The **test** bundle carries `NBAStatsTests/Fixtures/*.json`. A resource
/// added through a file-system synchronized group can land either inside its folder or flattened
/// at the bundle root depending on how Xcode adds it, so every lookup tries both.
enum TestBundles {

    /// The bundle the test code itself was compiled into.
    static var tests: Bundle { Bundle(for: BundleAnchor.self) }

    /// The host application's bundle, which carries the bundled contracts.
    static var app: Bundle { Bundle.main }

    /// A class whose only job is to name the test bundle for `Bundle(for:)`.
    private final class BundleAnchor {}

    /// Looks for `name.json` in `subdirectory` and then at the root, in the test bundle first and
    /// the app bundle second.
    static func url(forFixture name: String, subdirectory: String = "Fixtures") -> URL? {
        for bundle in [tests, app] {
            if let url = bundle.url(forResource: name, withExtension: "json", subdirectory: subdirectory) {
                return url
            }
            if let url = bundle.url(forResource: name, withExtension: "json") {
                return url
            }
        }
        return nil
    }

    /// The bytes of a golden fixture, or `nil` when no bundle carries it.
    static func fixtureData(named name: String) -> Data? {
        guard let url = url(forFixture: name) else { return nil }
        return try? Data(contentsOf: url)
    }

    /// The bytes of one of the bundled contract catalogs (`metrics`, `widgets`, `presets`).
    ///
    /// Deliberately *not* `url(forFixture:subdirectory:)`. Two different documents are called
    /// `presets.json`: the catalog in `Resources/Contracts/`, and the golden fixture of
    /// `GET /v1/presets` in `NBAStatsTests/Fixtures/`. The generic lookup tries the test bundle's
    /// root before the app bundle's `Contracts` folder, so on a packaging that flattens
    /// synchronized folders it would hand back the *fixture* — a different document, generated at
    /// a different time, which drifts from the catalog the app actually loads. Every `Contracts`
    /// folder is therefore tried before any bundle root, and the app bundle (which never carries
    /// a fixture by that name — `scripts/sync_contracts.sh` excludes it) before the test bundle.
    static func contractData(named name: String) -> Data? {
        let candidates: [URL?] = [
            app.url(forResource: name, withExtension: "json", subdirectory: "Contracts"),
            tests.url(forResource: name, withExtension: "json", subdirectory: "Contracts"),
            app.url(forResource: name, withExtension: "json"),
            tests.url(forResource: name, withExtension: "json")
        ]
        guard let url = candidates.compactMap({ $0 }).first else { return nil }
        return try? Data(contentsOf: url)
    }

    /// The widget fixture names the contract promises, one per `WidgetKind`.
    static var widgetFixtureNames: [String] {
        WidgetKind.allCases.map { "widget_\($0.rawValue)" }
    }

    /// True when at least one widget fixture is reachable, so a test can skip rather than fail
    /// when the fixtures have not been generated into the bundle.
    static var hasWidgetFixtures: Bool {
        widgetFixtureNames.contains { url(forFixture: $0) != nil }
    }
}

// MARK: - Temporary directories

/// A throwaway directory that deletes itself, for the persistence and cache tests.
final class TemporaryDirectory {
    let url: URL

    init(_ label: String = "hardwood-tests") {
        url = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(label)-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    }

    deinit {
        try? FileManager.default.removeItem(at: url)
    }
}

/// A `UserDefaults` domain that cannot disturb the simulator's real one, wiped on creation.
func makeTestDefaults(_ label: String = "hardwood.tests") -> UserDefaults {
    let name = "\(label).\(UUID().uuidString)"
    guard let defaults = UserDefaults(suiteName: name) else { return .standard }
    defaults.removePersistentDomain(forName: name)
    return defaults
}

// MARK: - Locale-independent number comparison

/// Number formatting follows the reader's locale, so a test that hard-codes `"61.5%"` would fail
/// on a simulator set to German. These helpers normalize the separators back to the contract's
/// spelling so the *rule* is asserted rather than the reader's locale.
enum NumberText {

    static var decimalSeparator: String {
        Locale.current.decimalSeparator ?? "."
    }

    static var groupingSeparator: String {
        Locale.current.groupingSeparator ?? ","
    }

    /// `"61,5 %"` becomes `"61.5%"`: grouping separators and non-breaking spaces are dropped and
    /// the decimal separator is rewritten as a period.
    static func canonical(_ text: String) -> String {
        var result = text
        if groupingSeparator != decimalSeparator {
            result = result.replacingOccurrences(of: groupingSeparator, with: "")
        }
        result = result.replacingOccurrences(of: decimalSeparator, with: ".")
        // A locale may spell the sign with a true minus sign or a non-breaking space.
        result = result.replacingOccurrences(of: "\u{2212}", with: "-")
        for space in ["\u{00A0}", "\u{202F}", "\u{2009}", " "] {
            result = result.replacingOccurrences(of: space, with: "")
        }
        return result
    }
}

/// Asserts that a formatted number matches the contract's spelling, ignoring locale separators.
func XCTAssertFormatted(_ actual: String,
                        _ expected: String,
                        _ message: String = "",
                        file: StaticString = #filePath,
                        line: UInt = #line) {
    XCTAssertEqual(NumberText.canonical(actual),
                   NumberText.canonical(expected),
                   message.isEmpty ? "\(actual) is not \(expected)" : message,
                   file: file,
                   line: line)
}

// MARK: - Catalog helper

/// The catalog under test: the bundled contracts, loaded from whichever bundle carries them.
@MainActor
func makeTestCatalog() -> Catalog {
    let catalog = Catalog(bundle: TestBundles.app)
    if !catalog.widgets.isEmpty { return catalog }
    // The app bundle did not carry the contracts (an unusual packaging); try the test bundle.
    return Catalog(bundle: TestBundles.tests)
}

// MARK: - Stub API client

/// What `StubAPIClient` should answer for one widget id.
enum StubWidgetOutcome: Sendable {
    case ok(WidgetPayload)
    /// The preview payload that belongs to whatever kind was asked for.
    case okPreview
    case partial(WidgetPayload, notes: [String])
    case unchanged
    case failure(code: String, message: String, recoverable: Bool)
    /// A result whose `availability` is `unavailable`: the era rule, not an error.
    case unavailable(reason: String)
    /// A success the server sent no payload with, which the client treats as a failure.
    case okWithNoPayload
}

/// A hand-written `APIClientProtocol` that answers from a script and records what it was asked.
///
/// It is an `actor` because the protocol is `Sendable` and `DashboardService` resolves chunks
/// concurrently in a task group; the actor serializes the recording without a lock.
actor StubAPIClient: APIClientProtocol {

    // MARK: Script

    private var outcomes: [String: StubWidgetOutcome] = [:]
    private var defaultOutcome: StubWidgetOutcome = .okPreview
    private var resolveFailure: APIError?
    private var syncQueue: [SyncResponse] = []
    private var syncFallback = SyncResponse(syncVersion: 0, hasChanges: false)
    private var syncFailure: APIError?
    private var responseSyncVersion: Int? = 412
    private let responseDataThrough: String? = "2026-01-02"
    private var searchResponse = PlayerSearchResponse(query: "", results: [], nextCursor: nil)
    private let teamsResponse = TeamsResponse(teams: [.preview, .previewOpponent])

    // MARK: Recording

    private(set) var resolveRequests: [DashboardResolveRequest] = []
    private(set) var syncSinceValues: [Int?] = []
    private(set) var metaCallCount = 0
    private(set) var presetsCallCount = 0
    private(set) var healthCallCount = 0
    private(set) var searchQueries: [String] = []
    private(set) var playerRequests: [PlayerID] = []
    private(set) var teamsCallCount = 0

    // MARK: Configuration

    func setOutcome(_ outcome: StubWidgetOutcome, forWidget id: String) {
        outcomes[id] = outcome
    }

    func setOutcomes(_ plan: [String: StubWidgetOutcome]) {
        outcomes = plan
    }

    func setDefaultOutcome(_ outcome: StubWidgetOutcome) {
        defaultOutcome = outcome
    }

    func setResolveFailure(_ error: APIError?) {
        resolveFailure = error
    }

    func setSyncFailure(_ error: APIError?) {
        syncFailure = error
    }

    /// Queues one `/v1/sync` answer. Once the queue is empty the fallback is repeated.
    func enqueueSync(_ response: SyncResponse) {
        syncQueue.append(response)
    }

    func setSyncFallback(_ response: SyncResponse) {
        syncFallback = response
    }

    func setResponseSyncVersion(_ version: Int?) {
        responseSyncVersion = version
    }

    func setSearchResponse(_ response: PlayerSearchResponse) {
        searchResponse = response
    }

    /// The number of widgets in each resolve request, in the order they were recorded.
    var resolveWidgetCounts: [Int] {
        resolveRequests.map { $0.widgets.count }
    }

    // MARK: APIClientProtocol

    func meta() async throws -> MetaResponse {
        metaCallCount += 1
        return MetaResponse(syncVersion: responseSyncVersion,
                            dataThrough: responseDataThrough,
                            currentSeason: "2025-26",
                            seasons: [SeasonInfo(season: "2025-26",
                                                 seasonTypes: ["Regular Season"],
                                                 isCurrent: true,
                                                 hasAdvanced: true,
                                                 gameCount: 612)],
                            metrics: nil,
                            widgets: nil,
                            teams: teamsResponse.teams,
                            coverage: Coverage(advancedFrom: "1996-97",
                                               trackingFrom: "2013-14",
                                               hustleFrom: "2016-17",
                                               shotChartsFrom: "1996-97",
                                               seasonFrom: "1946-47"),
                            attribution: "Stats via NBA.com. Not endorsed by or affiliated with the NBA.")
    }

    func presets() async throws -> PresetsResponse {
        presetsCallCount += 1
        return PresetsResponse(schemaVersion: 1, version: 7, presets: [], subjectTokens: [])
    }

    func health() async throws -> HealthResponse {
        healthCallCount += 1
        return HealthResponse(status: "ok",
                              version: "test",
                              syncVersion: responseSyncVersion,
                              dataThrough: responseDataThrough,
                              databaseReady: true,
                              seededDemoData: false)
    }

    func sync(since: Int?) async throws -> SyncResponse {
        syncSinceValues.append(since)
        if let syncFailure { throw syncFailure }
        if syncQueue.isEmpty { return syncFallback }
        return syncQueue.removeFirst()
    }

    func searchPlayers(query: String, limit: Int) async throws -> PlayerSearchResponse {
        searchQueries.append(query)
        return searchResponse
    }

    func player(_ id: PlayerID) async throws -> PlayerDetailResponse {
        playerRequests.append(id)
        return PlayerDetailResponse(player: .preview, bio: nil, careerTotals: nil, seasons: [])
    }

    func teams() async throws -> TeamsResponse {
        teamsCallCount += 1
        return teamsResponse
    }

    func resolve(_ request: DashboardResolveRequest) async throws -> DashboardResolveResponse {
        resolveRequests.append(request)
        if let resolveFailure { throw resolveFailure }
        if request.widgets.count > DashboardResolveRequest.maxWidgets {
            throw APIError.server(code: APIError.Code.tooManyWidgets.rawValue,
                                  message: "Too many widgets.",
                                  status: 400,
                                  recoverable: false)
        }
        let generatedAt = "2026-01-03T07:12:44Z"
        var results: [ResolveResult] = []
        results.reserveCapacity(request.widgets.count)
        for widget in request.widgets {
            results.append(StubAPIClient.result(for: widget,
                                                outcome: outcomes[widget.id] ?? defaultOutcome,
                                                generatedAt: generatedAt))
        }
        return DashboardResolveResponse(syncVersion: responseSyncVersion,
                                        dataThrough: responseDataThrough,
                                        generatedAt: generatedAt,
                                        resolvedContext: request.context,
                                        results: results)
    }

    // MARK: Result building

    private static func result(for widget: ResolveWidgetRequest,
                               outcome: StubWidgetOutcome,
                               generatedAt: String) -> ResolveResult {
        switch outcome {
        case .okPreview:
            return result(for: widget,
                          outcome: .ok(WidgetPayload.preview(for: widget.kind)),
                          generatedAt: generatedAt)
        case .ok(let payload):
            return ResolveResult(widgetId: widget.id,
                                 kindRaw: widget.kind.rawValue,
                                 status: .ok,
                                 payload: payload,
                                 generatedAt: generatedAt,
                                 ttlSeconds: widget.kind.defaultCacheTTLSeconds,
                                 availability: .full,
                                 notes: [])
        case .partial(let payload, let notes):
            return ResolveResult(widgetId: widget.id,
                                 kindRaw: widget.kind.rawValue,
                                 status: .partial,
                                 payload: payload,
                                 generatedAt: generatedAt,
                                 ttlSeconds: widget.kind.defaultCacheTTLSeconds,
                                 availability: .partial,
                                 notes: notes)
        case .unchanged:
            return ResolveResult(widgetId: widget.id,
                                 kindRaw: widget.kind.rawValue,
                                 status: .unchanged,
                                 generatedAt: generatedAt)
        case .failure(let code, let message, let recoverable):
            return ResolveResult(widgetId: widget.id,
                                 kindRaw: widget.kind.rawValue,
                                 status: .error,
                                 error: APIErrorBody(code: code, message: message, recoverable: recoverable),
                                 generatedAt: generatedAt)
        case .unavailable(let reason):
            return ResolveResult(widgetId: widget.id,
                                 kindRaw: widget.kind.rawValue,
                                 status: .ok,
                                 generatedAt: generatedAt,
                                 availability: .unavailable,
                                 notes: [reason])
        case .okWithNoPayload:
            return ResolveResult(widgetId: widget.id,
                                 kindRaw: widget.kind.rawValue,
                                 status: .ok,
                                 generatedAt: generatedAt,
                                 availability: .full,
                                 notes: [])
        }
    }
}

// MARK: - Layout builders

/// Small builders so a test can say what it means without repeating `DashboardWidget(...)`.
enum TestLayouts {

    static func widget(id: String,
                       kind: WidgetKind = .statTile,
                       size: WidgetSize = .small,
                       config: [String: JSONValue] = [:]) -> DashboardWidget {
        DashboardWidget(id: id, kind: kind, title: nil, size: size, config: config)
    }

    static func layout(id: String = "layout-1",
                       name: String = "Test Dashboard",
                       isPreset: Bool = false,
                       presetKey: String? = nil,
                       widgets: [DashboardWidget]) -> DashboardLayout {
        DashboardLayout(id: id,
                        name: name,
                        icon: "square.grid.2x2",
                        accent: .orange,
                        schemaVersion: DashboardLayout.currentSchemaVersion,
                        isPreset: isPreset,
                        presetKey: presetKey,
                        tagline: nil,
                        createdAt: nil,
                        updatedAt: nil,
                        widgets: widgets)
    }

    /// `count` widgets that cycle through the thirteen kinds, so no two adjacent widgets share a
    /// cache key.
    static func manyWidgets(_ count: Int) -> [DashboardWidget] {
        let kinds = WidgetKind.allCases
        return (0..<count).map { index in
            widget(id: "w\(index)", kind: kinds[index % kinds.count], size: .small)
        }
    }
}
