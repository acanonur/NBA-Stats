import Combine
import Foundation
import OSLog
import SwiftUI

// MARK: - Client router

/// The one client the app holds, routing every call to either the live server or the bundled
/// fixtures.
///
/// `AppEnvironment.client` is a `let`, because `DashboardService` and `SyncService` are built once
/// and keep the reference they were given. Demo mode and a changed server URL still have to take
/// effect without relaunching, so the switch lives *inside* the client rather than beside it: this
/// actor holds both a live `APIClient` and a `DemoAPIClient` and forwards to whichever one the
/// reader has chosen.
public actor HardwoodAPIClient: APIClientProtocol {

    private var live: APIClient
    private let demo: DemoAPIClient
    private var usesDemoData: Bool

    public init(configuration: APIConfiguration,
                isDemoMode: Bool,
                bundle: Bundle = .main) {
        self.live = APIClient(configuration: configuration)
        self.demo = DemoAPIClient(bundle: bundle)
        self.usesDemoData = isDemoMode
    }

    /// True while the bundled fixtures are being served.
    public var isDemoMode: Bool { usesDemoData }

    public func setDemoMode(_ enabled: Bool) {
        usesDemoData = enabled
    }

    /// Points the live half at a different server. The demo half never changes.
    public func updateConfiguration(_ configuration: APIConfiguration) {
        live = APIClient(configuration: configuration)
    }

    /// Whichever client is answering right now.
    private var active: any APIClientProtocol {
        if usesDemoData { return demo }
        return live
    }

    // MARK: Routes

    public func meta() async throws -> MetaResponse {
        try await active.meta()
    }

    public func presets() async throws -> PresetsResponse {
        try await active.presets()
    }

    public func health() async throws -> HealthResponse {
        try await active.health()
    }

    /// Always answered by the live server: "can Hardwood reach this URL" is a question demo mode
    /// cannot answer honestly.
    public func liveHealth() async throws -> HealthResponse {
        try await live.health()
    }

    public func sync(since: Int?) async throws -> SyncResponse {
        try await active.sync(since: since)
    }

    public func searchPlayers(query: String, limit: Int) async throws -> PlayerSearchResponse {
        try await active.searchPlayers(query: query, limit: limit)
    }

    public func player(_ id: PlayerID) async throws -> PlayerDetailResponse {
        try await active.player(id)
    }

    public func teams() async throws -> TeamsResponse {
        try await active.teams()
    }

    public func resolve(_ request: DashboardResolveRequest) async throws -> DashboardResolveResponse {
        try await active.resolve(request)
    }
}

// MARK: - Environment

/// Everything one running copy of Hardwood is made of, assembled once and handed down the view
/// tree as an `EnvironmentObject`.
///
/// It owns the composition — which client, which cache, which persistence — and the handful of
/// preferences that are not part of a dashboard document: the reader's favourite player and team,
/// whether the tour has been seen, and whether the app is serving bundled data. It is also the one
/// place that knows how to build a `ResolveContext`, so every screen asks for a dashboard load the
/// same way.
@MainActor
public final class AppEnvironment: ObservableObject {

    /// Where the preferences that are not part of a layout document are kept.
    public enum DefaultsKey {
        public static let favoritePlayerID = "hardwood.favorite.playerID"
        public static let favoritePlayerName = "hardwood.favorite.playerName"
        public static let favoriteTeamID = "hardwood.favorite.teamID"
        public static let favoriteTeamName = "hardwood.favorite.teamName"
        public static let onboardingCompleted = "hardwood.onboarding.completed"
        public static let recentSearches = "hardwood.search.recent"
    }

    /// The line the league requires on anything built from its numbers.
    public static let defaultAttribution = "Stats via NBA.com. Not endorsed by or affiliated with the NBA."

    /// How many search terms are remembered.
    public static let maximumRecentSearches = 8

    // MARK: Composition

    public let catalog: Catalog
    public let client: any APIClientProtocol
    public let cache: DiskCache
    public let dashboard: DashboardService
    public let sync: SyncService
    public let store: DashboardStore

    // MARK: Preferences

    @Published public var favoritePlayerID: PlayerID? {
        didSet {
            guard favoritePlayerID != oldValue else { return }
            if favoritePlayerID == nil { favoritePlayerName = nil }
            persistFavoritePlayer()
            reloadForContextChange()
        }
    }

    @Published public var favoriteTeamID: TeamID? {
        didSet {
            guard favoriteTeamID != oldValue else { return }
            if favoriteTeamID == nil { favoriteTeamName = nil }
            persistFavoriteTeam()
            reloadForContextChange()
        }
    }

    /// Remembered alongside the id so Settings can say "LeBron James" rather than "#2544" before
    /// anything has been fetched.
    @Published public private(set) var favoritePlayerName: String?
    @Published public private(set) var favoriteTeamName: String?

    @Published public var isDemoMode: Bool {
        didSet {
            guard isDemoMode != oldValue else { return }
            APIConfiguration.setDemoModeEnabled(isDemoMode, defaults: defaults)
            applyDataSourceChange(isDemoMode)
        }
    }

    @Published public var hasCompletedOnboarding: Bool {
        didSet {
            guard hasCompletedOnboarding != oldValue else { return }
            defaults.set(hasCompletedOnboarding, forKey: DefaultsKey.onboardingCompleted)
        }
    }

    /// The most recent search terms, newest first.
    @Published public private(set) var recentSearches: [String]

    /// The attribution line, replaced by `/v1/meta` when the server sends its own.
    @Published public private(set) var attribution: String

    /// When the bundled catalogs were last refreshed from `/v1/meta`.
    @Published public private(set) var lastCatalogRefresh: Date?

    // MARK: Internals

    private let router: HardwoodAPIClient
    private let defaults: UserDefaults
    private var didStart = false

    private static let logger = Logger(subsystem: "com.hardwood.nbastats", category: "environment")

    // MARK: Init

    public init(catalog: Catalog,
                client: HardwoodAPIClient,
                cache: DiskCache,
                persistence: any LayoutPersisting,
                defaults: UserDefaults = .standard,
                isDemoMode: Bool) {
        self.catalog = catalog
        self.router = client
        self.client = client
        self.cache = cache
        self.defaults = defaults

        self.store = DashboardStore(persistence: persistence, catalog: catalog, defaults: defaults)
        self.dashboard = DashboardService(client: client, cache: cache, catalog: catalog)
        self.sync = SyncService(client: client, defaults: defaults, cache: cache)

        self.favoritePlayerID = defaults.object(forKey: DefaultsKey.favoritePlayerID) as? PlayerID
        self.favoriteTeamID = defaults.object(forKey: DefaultsKey.favoriteTeamID) as? TeamID
        self.favoritePlayerName = defaults.string(forKey: DefaultsKey.favoritePlayerName)
        self.favoriteTeamName = defaults.string(forKey: DefaultsKey.favoriteTeamName)
        self.isDemoMode = isDemoMode
        self.hasCompletedOnboarding = defaults.bool(forKey: DefaultsKey.onboardingCompleted)
        self.recentSearches = defaults.stringArray(forKey: DefaultsKey.recentSearches) ?? []
        self.attribution = AppEnvironment.defaultAttribution
        self.lastCatalogRefresh = nil

        bridgeSyncToDashboard()
    }

    /// The app's own composition: the shared catalog, a disk cache, layouts in Application Support.
    public static func live(defaults: UserDefaults = .standard, bundle: Bundle = .main) -> AppEnvironment {
        let isDemo = APIConfiguration.isDemoModeEnabled(bundle: bundle, defaults: defaults)
        let configuration = APIConfiguration.resolved(bundle: bundle, defaults: defaults)
        let router = HardwoodAPIClient(configuration: configuration, isDemoMode: isDemo, bundle: bundle)
        return AppEnvironment(catalog: Catalog.shared,
                              client: router,
                              cache: DiskCache(),
                              persistence: FileLayoutPersistence(),
                              defaults: defaults,
                              isDemoMode: isDemo)
    }

    /// A throwaway copy for previews and screenshots: bundled fixtures, layouts in memory, a cache
    /// in the temporary directory, and a defaults domain that cannot disturb a real install.
    public static func demo(bundle: Bundle = .main) -> AppEnvironment {
        let defaults = UserDefaults(suiteName: "com.hardwood.nbastats.demo") ?? .standard
        let catalog = Catalog(bundle: bundle)
        let router = HardwoodAPIClient(configuration: APIConfiguration(baseURL: APIConfiguration.defaultBaseURL),
                                       isDemoMode: true,
                                       bundle: bundle)
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("HardwoodDemoCache", isDirectory: true)
        let persistence = InMemoryLayoutPersistence(layouts: LayoutSeeder.seedLayouts(catalog: catalog))
        let environment = AppEnvironment(catalog: catalog,
                                         client: router,
                                         cache: DiskCache(directory: directory),
                                         persistence: persistence,
                                         defaults: defaults,
                                         isDemoMode: true)
        environment.hasCompletedOnboarding = true
        return environment
    }

    // MARK: Lifecycle

    /// The launch pass: refresh the catalogs, then ask what the league has done since last time.
    /// Safe to call from every `.task`; only the first call does the work.
    public func start() async {
        guard !didStart else { return }
        didStart = true
        sync.scheduleNextRefresh()
        await refreshCatalog()
        await checkForUpdates(force: false)
    }

    public func registerBackgroundTasks() {
        sync.registerBackgroundTasks()
    }

    public func scheduleBackgroundRefresh() {
        sync.scheduleNextRefresh()
    }

    // MARK: Loading

    /// The context every resolve is made in: the reader's favourites, their clock, and the
    /// league's own calendar day.
    ///
    /// `asOf` is the league's scheduling day in US Eastern, not the device's — "last night's
    /// games" means a different slate in Honolulu than it does in Boston, and the cache key is
    /// built from this context, so the two must not share an entry.
    public func resolveContext(now: Date = Date()) -> ResolveContext {
        ResolveContext(favoritePlayerId: favoritePlayerID,
                       favoriteTeamId: favoriteTeamID,
                       timeZone: TimeZone.current.identifier,
                       asOf: ConfigDisplay.dayString(now),
                       season: nil)
    }

    /// Loads the selected layout. Cached tiles appear immediately; the network fills in behind.
    public func refreshDashboard(force: Bool) async {
        guard let layout = store.selectedLayout else {
            dashboard.reset()
            return
        }
        if sync.syncVersion > 0 {
            dashboard.updateKnownSyncVersion(sync.syncVersion)
        }
        await dashboard.load(layout: layout, context: resolveContext(), force: force)
    }

    /// Polls `/v1/sync`. Returns true when something changed.
    @discardableResult
    public func checkForUpdates(force: Bool) async -> Bool {
        await sync.check(force: force)
    }

    /// Pull-to-refresh: ask what changed, then re-resolve everything.
    public func refreshEverything() async {
        await checkForUpdates(force: true)
        await refreshDashboard(force: true)
    }

    /// Replaces the bundled catalogs with the server's, when it has newer ones. A failure here is
    /// never surfaced: the bundled catalog is a complete, working fallback.
    public func refreshCatalog() async {
        do {
            let meta = try await client.meta()
            catalog.update(from: meta)
            if let line = meta.attribution, !line.isEmpty {
                attribution = line
            }
            lastCatalogRefresh = Date()
        } catch {
            AppEnvironment.logger.notice("Catalog refresh skipped: \(APIError.from(error).userMessage, privacy: .public)")
        }
        do {
            let response = try await client.presets()
            catalog.update(from: response)
        } catch {
            AppEnvironment.logger.notice("Preset refresh skipped: \(APIError.from(error).userMessage, privacy: .public)")
        }
    }

    // MARK: Favourites

    public func isFavorite(player: PlayerRef) -> Bool {
        favoritePlayerID == player.playerId
    }

    public func isFavorite(team: TeamRef) -> Bool {
        favoriteTeamID == team.teamId
    }

    /// Pins a player, or with `nil` clears the pin.
    public func setFavoritePlayer(_ player: PlayerRef?) {
        favoritePlayerName = player?.name
        favoritePlayerID = player?.playerId
        persistFavoritePlayer()
    }

    public func setFavoriteTeam(_ team: TeamRef?) {
        favoriteTeamName = team?.name
        favoriteTeamID = team?.teamId
        persistFavoriteTeam()
    }

    public func toggleFavorite(player: PlayerRef) {
        setFavoritePlayer(isFavorite(player: player) ? nil : player)
    }

    public func toggleFavorite(team: TeamRef) {
        setFavoriteTeam(isFavorite(team: team) ? nil : team)
    }

    // MARK: Recent searches

    public func recordSearch(_ term: String) {
        let trimmed = term.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 2 else { return }
        var updated = recentSearches.filter { $0.localizedCaseInsensitiveCompare(trimmed) != .orderedSame }
        updated.insert(trimmed, at: 0)
        if updated.count > AppEnvironment.maximumRecentSearches {
            updated = Array(updated.prefix(AppEnvironment.maximumRecentSearches))
        }
        recentSearches = updated
        defaults.set(updated, forKey: DefaultsKey.recentSearches)
    }

    public func clearRecentSearches() {
        recentSearches = []
        defaults.removeObject(forKey: DefaultsKey.recentSearches)
    }

    // MARK: Server settings

    /// The configuration the live client is currently pointed at.
    public var configuration: APIConfiguration {
        APIConfiguration.resolved(defaults: defaults)
    }

    /// Applies a new base URL and API key. Returns false when the URL could not be parsed, in
    /// which case the previous one is left in place.
    @discardableResult
    public func applyServerSettings(baseURL: String?, apiKey: String?) -> Bool {
        let accepted = APIConfiguration.setBaseURLOverride(baseURL, defaults: defaults)
        APIConfiguration.setAPIKeyOverride(apiKey, defaults: defaults)
        let updated = APIConfiguration.resolved(defaults: defaults)
        let router = self.router
        Task {
            await router.updateConfiguration(updated)
        }
        return accepted
    }

    /// Asks the live server whether it is up, whatever demo mode is set to.
    public func testConnection() async -> Result<HealthResponse, APIError> {
        do {
            let health = try await router.liveHealth()
            return .success(health)
        } catch {
            return .failure(APIError.from(error))
        }
    }

    // MARK: Cache

    public func cacheStatistics() async -> DiskCache.Statistics {
        await cache.statistics()
    }

    /// Throws away every saved payload and loads the dashboard again from the network.
    public func clearCache() async {
        await cache.removeAll()
        dashboard.reset()
        await refreshDashboard(force: true)
    }

    // MARK: Internals

    /// A sync poll that found real change re-resolves the affected tiles.
    private func bridgeSyncToDashboard() {
        sync.onChange = { [weak self] change in
            guard let self = self else { return }
            self.handleSyncChange(change)
        }
    }

    private func handleSyncChange(_ change: SyncChange) {
        dashboard.updateKnownSyncVersion(change.syncVersion)
        Task { [weak self] in
            guard let self = self else { return }
            await self.dashboard.invalidate(kinds: change.invalidatedKinds)
            await self.refreshDashboard(force: true)
        }
    }

    /// Switching between the live server and the fixtures invalidates everything on screen.
    private func applyDataSourceChange(_ enabled: Bool) {
        let router = self.router
        Task { [weak self] in
            await router.setDemoMode(enabled)
            guard let self = self else { return }
            await self.cache.removeAll()
            self.dashboard.reset()
            await self.refreshCatalog()
            await self.refreshDashboard(force: true)
        }
    }

    private func reloadForContextChange() {
        Task { [weak self] in
            guard let self = self else { return }
            await self.refreshDashboard(force: true)
        }
    }

    private func persistFavoritePlayer() {
        if let id = favoritePlayerID {
            defaults.set(id, forKey: DefaultsKey.favoritePlayerID)
        } else {
            defaults.removeObject(forKey: DefaultsKey.favoritePlayerID)
        }
        if let name = favoritePlayerName, !name.isEmpty {
            defaults.set(name, forKey: DefaultsKey.favoritePlayerName)
        } else {
            defaults.removeObject(forKey: DefaultsKey.favoritePlayerName)
        }
    }

    private func persistFavoriteTeam() {
        if let id = favoriteTeamID {
            defaults.set(id, forKey: DefaultsKey.favoriteTeamID)
        } else {
            defaults.removeObject(forKey: DefaultsKey.favoriteTeamID)
        }
        if let name = favoriteTeamName, !name.isEmpty {
            defaults.set(name, forKey: DefaultsKey.favoriteTeamName)
        } else {
            defaults.removeObject(forKey: DefaultsKey.favoriteTeamName)
        }
    }
}

// MARK: - Convenience

public extension AppEnvironment {

    /// The layout the dashboard is showing, or `nil` while the store is still empty.
    var selectedLayout: DashboardLayout? { store.selectedLayout }

    /// The accent the whole UI tints itself with, following the selected layout.
    var accent: AccentName { store.selectedLayout?.accent ?? .orange }

    /// `"data through Jan 2"`, from whichever of the two services heard from the server last.
    var dataThroughText: String? {
        let raw = dashboard.dataThrough ?? sync.dataThrough
        guard let raw = raw, !raw.isEmpty else { return nil }
        return "data through " + Formatting.shortGameDate(raw)
    }
}

#if DEBUG
#Preview("Environment summary") {
    let environment = AppEnvironment.demo()
    return VStack(alignment: .leading, spacing: Spacing.sm) {
        Text("Hardwood")
            .hardwoodText(.sectionTitle)
        Text(environment.isDemoMode ? "Demo mode: bundled fixtures" : "Live server")
            .hardwoodText(.tableCell, color: Palette.textSecondary)
        Text("\(environment.catalog.metrics.count) metrics · \(environment.catalog.widgets.count) widgets · \(environment.catalog.presets.count) presets")
            .hardwoodText(.caption)
        Text("\(environment.store.layouts.count) dashboards")
            .hardwoodText(.caption)
        Text(environment.attribution)
            .hardwoodText(.caption)
            .fixedSize(horizontal: false, vertical: true)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(Spacing.lg)
    .hardwoodCard()
    .padding(Spacing.lg)
    .hardwoodBackground()
}
#endif
