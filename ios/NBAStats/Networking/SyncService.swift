import Combine
import Foundation
import OSLog

/// What one `/v1/sync` poll turned up.
public struct SyncChange: Hashable, Sendable {
    public let syncVersion: Int
    public let previousVersion: Int?
    public let dataThrough: String?
    /// The widget kinds whose cached payloads were dropped.
    public let invalidatedKinds: [WidgetKind]
    public let finalizedGames: [GameRef]
    public let changedDates: [String]

    public init(syncVersion: Int,
                previousVersion: Int? = nil,
                dataThrough: String? = nil,
                invalidatedKinds: [WidgetKind] = [],
                finalizedGames: [GameRef] = [],
                changedDates: [String] = []) {
        self.syncVersion = syncVersion
        self.previousVersion = previousVersion
        self.dataThrough = dataThrough
        self.invalidatedKinds = invalidatedKinds
        self.finalizedGames = finalizedGames
        self.changedDates = changedDates
    }
}

/// Tracks "what has the league done since I last looked" (`contracts/CONTRACT.md` §8).
///
/// The service's sync version moves once per finalized game, so a single integer decides whether
/// anything on screen is out of date. This polls `/v1/sync` on foreground, on pull-to-refresh and
/// from the background task, honours the server's own `nextPollAfterSeconds`, and never polls in
/// a loop: a floor is applied even if the server asks for zero.
@MainActor
public final class SyncService: ObservableObject {

    /// The floor on polling, whatever the server says.
    public static let minimumPollSeconds = 60
    /// Used when the server does not send `nextPollAfterSeconds`.
    public static let defaultPollSeconds = 900
    /// How long to wait after a failed poll before trying again.
    public static let failureBackoffSeconds: TimeInterval = 120

    /// Where the last known sync version is kept between launches.
    public static let syncVersionDefaultsKey = "hardwood.sync.version"
    public static let dataThroughDefaultsKey = "hardwood.sync.dataThrough"

    /// 0 until the first successful poll.
    @Published public private(set) var syncVersion: Int
    @Published public private(set) var dataThrough: String?
    @Published public private(set) var lastCheck: Date?
    /// Games that finished since the reader last acknowledged them, newest batch first.
    @Published public private(set) var newlyFinalizedGames: [GameRef] = []
    @Published public private(set) var lastInvalidatedKinds: [WidgetKind] = []
    @Published public private(set) var lastError: APIError?
    @Published public private(set) var isChecking = false

    /// Called on the main actor whenever a poll reports real change, so the app can re-resolve
    /// the dashboard. Set by `AppEnvironment`.
    public var onChange: (@MainActor (SyncChange) -> Void)?

    private let client: APIClientProtocol
    private let defaults: UserDefaults
    private let cache: DiskCache?
    private var nextPollDate: Date?

    private static let logger = Logger(subsystem: "com.hardwood.nbastats", category: "sync")

    /// `cache` is optional so a preview or a test can run the service with nothing to purge.
    public init(client: APIClientProtocol, defaults: UserDefaults = .standard, cache: DiskCache? = nil) {
        self.client = client
        self.defaults = defaults
        self.cache = cache
        self.syncVersion = defaults.integer(forKey: SyncService.syncVersionDefaultsKey)
        self.dataThrough = defaults.string(forKey: SyncService.dataThroughDefaultsKey)
    }

    /// True when the app has never heard from a server.
    public var hasNeverSynced: Bool { syncVersion <= 0 }

    /// `"3 games finished since you last looked"`, or `nil` when there is nothing to announce.
    public var finalizedSummary: String? {
        let count = newlyFinalizedGames.count
        guard count > 0 else { return nil }
        let noun = count == 1 ? "game" : "games"
        return "\(count) \(noun) finished since you last looked"
    }

    /// Clears the finalized-games banner once the reader has seen it.
    public func acknowledgeFinalizedGames() {
        guard !newlyFinalizedGames.isEmpty else { return }
        newlyFinalizedGames = []
    }

    /// Polls `/v1/sync`. Returns true when something changed and the dashboard should re-resolve.
    ///
    /// `force` is for the two moments the reader is explicitly asking — a pull-to-refresh and a
    /// background run — and skips the pacing window. Every other caller is rate limited.
    @discardableResult
    public func check(force: Bool = false) async -> Bool {
        if isChecking { return false }
        if !force, let nextPollDate = nextPollDate, Date() < nextPollDate { return false }

        isChecking = true
        defer { isChecking = false }

        do {
            let response = try await client.sync(since: hasNeverSynced ? nil : syncVersion)
            lastCheck = Date()
            lastError = nil
            schedulePacing(from: response.nextPollAfterSeconds)

            let previous = syncVersion
            if response.syncVersion > 0 { persist(syncVersion: response.syncVersion) }
            if let through = response.dataThrough, !through.isEmpty { persist(dataThrough: through) }

            guard response.hasChanges else { return false }

            let kinds = response.invalidatedKinds
            lastInvalidatedKinds = kinds
            if !kinds.isEmpty, let cache = cache {
                await cache.purgeKinds(kinds)
            }

            let finalized = response.finalizedGames.filter { $0.status == .final }
            if !finalized.isEmpty {
                // Batches accumulate until the reader acknowledges them, so a game that finished
                // during one poll is still announced if the next poll finds another.
                var merged = finalized
                for game in newlyFinalizedGames where !merged.contains(where: { $0.gameId == game.gameId }) {
                    merged.append(game)
                }
                newlyFinalizedGames = merged
            }

            let change = SyncChange(syncVersion: response.syncVersion,
                                    previousVersion: response.previousVersion ?? previous,
                                    dataThrough: response.dataThrough,
                                    invalidatedKinds: kinds,
                                    finalizedGames: finalized,
                                    changedDates: response.changedDates)
            SyncService.logger.notice("Sync \(response.syncVersion, privacy: .public): \(finalized.count, privacy: .public) finalized, invalidating \(kinds.count, privacy: .public) kinds.")
            onChange?(change)
            return true
        } catch {
            guard !APIError.isCancellation(error) else { return false }
            let apiError = APIError.from(error)
            lastError = apiError
            lastCheck = Date()
            // Back off after a failure, so a server that is down is not polled on every foreground.
            nextPollDate = Date().addingTimeInterval(SyncService.failureBackoffSeconds)
            return false
        }
    }

    /// Registers the background tasks and points them at this service.
    ///
    /// iOS decides if these ever run; everything here still works from the foreground if they
    /// never do.
    public func registerBackgroundTasks() {
        BackgroundRefresh.register { [weak self] kind in
            guard let self = self else { return }
            await self.runBackgroundTask(kind)
        }
    }

    /// Asks the system for the next background opportunity. Call when the app goes to the
    /// background and after each background run.
    public func scheduleNextRefresh() {
        BackgroundRefresh.scheduleAll()
    }

    private func runBackgroundTask(_ kind: BackgroundRefreshKind) async {
        switch kind {
        case .appRefresh:
            await check(force: true)
        case .nightly:
            await check(force: true)
            // The league re-publishes the last three days every night, so anything past its TTL
            // is worth dropping while the app is awake anyway.
            await cache?.purgeExpired()
        }
    }

    // MARK: - Internals

    private func schedulePacing(from serverSeconds: Int?) {
        let requested = serverSeconds ?? SyncService.defaultPollSeconds
        let seconds = max(SyncService.minimumPollSeconds, requested)
        nextPollDate = Date().addingTimeInterval(TimeInterval(seconds))
    }

    private func persist(syncVersion version: Int) {
        guard version != syncVersion else { return }
        syncVersion = version
        defaults.set(version, forKey: SyncService.syncVersionDefaultsKey)
    }

    private func persist(dataThrough value: String) {
        guard value != dataThrough else { return }
        dataThrough = value
        defaults.set(value, forKey: SyncService.dataThroughDefaultsKey)
    }
}
