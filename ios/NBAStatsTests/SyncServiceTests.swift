import Foundation
import XCTest
@testable import Hardwood

/// `SyncService` answers one question — "what has the league done since I last looked" — and the
/// answer has to be cheap when nothing has happened. A poll that finds no change must not purge a
/// single cached payload; a poll that finds change must drop exactly the kinds the server named.
@MainActor
final class SyncServiceTests: XCTestCase {

    private var scratch: [TemporaryDirectory] = []

    private func makeCache(_ label: String) -> DiskCache {
        let directory = TemporaryDirectory(label)
        scratch.append(directory)
        return DiskCache(directory: directory.url)
    }

    private func makeService(cache: DiskCache?, label: String = "sync") -> (SyncService, StubAPIClient) {
        let client = StubAPIClient()
        let service = SyncService(client: client,
                                  defaults: makeTestDefaults("hardwood.\(label)"),
                                  cache: cache)
        return (service, client)
    }

    private let context = ResolveContext(timeZone: "America/New_York", asOf: "2026-01-02")

    private func key(_ kind: WidgetKind) -> String {
        DiskCache.key(kind: kind, config: [:], context: context)
    }

    /// Puts one payload in the cache for each kind, so a purge has something to take.
    private func seed(_ cache: DiskCache, kinds: [WidgetKind]) async {
        for kind in kinds {
            await cache.store(Data("{\"kind\":\"\(kind.rawValue)\"}".utf8),
                              forKey: key(kind),
                              kind: kind,
                              ttlSeconds: 3600)
        }
    }

    // MARK: - Nothing changed

    /// The cheap case. `hasChanges: false` means the body is small and the client does nothing —
    /// specifically, it does not throw away cached payloads it is about to need.
    func testAPollThatFindsNoChangeDoesNothing() async throws {
        let cache = makeCache("sync-nochange")
        await seed(cache, kinds: [.scoreboard, .leaderboard])
        let (service, client) = makeService(cache: cache, label: "sync-nochange")

        var changes: [SyncChange] = []
        service.onChange = { change in changes.append(change) }

        await client.enqueueSync(SyncResponse(syncVersion: 412,
                                              previousVersion: 412,
                                              serverTime: "2026-01-03T07:12:44Z",
                                              dataThrough: "2026-01-02",
                                              hasChanges: false,
                                              invalidate: ["scoreboard", "leaderboard"],
                                              nextPollAfterSeconds: 900))

        let didChange = await service.check(force: true)

        XCTAssertFalse(didChange, "A poll with hasChanges: false reported a change")
        XCTAssertTrue(changes.isEmpty, "The dashboard was told to re-resolve for nothing")
        let scoreboard = await cache.entry(forKey: key(.scoreboard))
        let leaderboard = await cache.entry(forKey: key(.leaderboard))
        XCTAssertNotNil(scoreboard, "A cached payload was purged even though nothing changed")
        XCTAssertNotNil(leaderboard)
        XCTAssertTrue(service.lastInvalidatedKinds.isEmpty)
        // It still learns the version and the data date, which is the point of asking.
        XCTAssertEqual(service.syncVersion, 412)
        XCTAssertEqual(service.dataThrough, "2026-01-02")
        XCTAssertNotNil(service.lastCheck)
        XCTAssertNil(service.lastError)
        XCTAssertFalse(service.isChecking)
    }

    // MARK: - Something changed

    /// The expensive case: the named kinds are dropped from the cache, and nothing else is.
    func testAPollThatFindsChangePurgesExactlyTheNamedKinds() async throws {
        let cache = makeCache("sync-change")
        await seed(cache, kinds: [.scoreboard, .dailyMovers, .leaderboard, .careerArc])
        let (service, client) = makeService(cache: cache, label: "sync-change")

        var changes: [SyncChange] = []
        service.onChange = { change in changes.append(change) }

        await client.enqueueSync(SyncResponse(syncVersion: 413,
                                              previousVersion: 412,
                                              serverTime: "2026-01-03T07:12:44Z",
                                              dataThrough: "2026-01-02",
                                              hasChanges: true,
                                              changedDates: ["2026-01-02"],
                                              finalizedGames: [.preview],
                                              affectedPlayerIds: [2544],
                                              invalidate: ["scoreboard", "daily_movers"],
                                              nextPollAfterSeconds: 900))

        let didChange = await service.check(force: true)

        XCTAssertTrue(didChange)
        let scoreboard = await cache.entry(forKey: key(.scoreboard))
        let movers = await cache.entry(forKey: key(.dailyMovers))
        let leaderboard = await cache.entry(forKey: key(.leaderboard))
        let arc = await cache.entry(forKey: key(.careerArc))
        XCTAssertNil(scoreboard, "scoreboard was named but not purged")
        XCTAssertNil(movers, "daily_movers was named but not purged")
        XCTAssertNotNil(leaderboard, "leaderboard was purged but was not named")
        XCTAssertNotNil(arc, "career_arc was purged but was not named")

        XCTAssertEqual(service.lastInvalidatedKinds, [.scoreboard, .dailyMovers])
        XCTAssertEqual(service.syncVersion, 413)
        XCTAssertEqual(service.newlyFinalizedGames.count, 1)
        XCTAssertEqual(service.finalizedSummary, "1 game finished since you last looked")

        // The dashboard is told once, with everything it needs to re-resolve.
        XCTAssertEqual(changes.count, 1)
        let change = try XCTUnwrap(changes.first)
        XCTAssertEqual(change.syncVersion, 413)
        XCTAssertEqual(change.previousVersion, 412)
        XCTAssertEqual(change.invalidatedKinds, [.scoreboard, .dailyMovers])
        XCTAssertEqual(change.changedDates, ["2026-01-02"])
        XCTAssertEqual(change.finalizedGames.count, 1)
    }

    /// A kind a newer server knows and this build does not must not fail the whole response.
    func testAnUnknownInvalidateKindIsSkippedRatherThanFatal() async {
        let cache = makeCache("sync-unknown-kind")
        await seed(cache, kinds: [.scoreboard])
        let (service, client) = makeService(cache: cache, label: "sync-unknown-kind")

        await client.enqueueSync(SyncResponse(syncVersion: 500,
                                              hasChanges: true,
                                              invalidate: ["scoreboard", "hologram"]))

        let didChange = await service.check(force: true)

        XCTAssertTrue(didChange)
        XCTAssertEqual(service.lastInvalidatedKinds, [.scoreboard])
        let scoreboard = await cache.entry(forKey: key(.scoreboard))
        XCTAssertNil(scoreboard)
    }

    func testChangeWithNoCacheIsStillReported() async {
        let (service, client) = makeService(cache: nil, label: "sync-no-cache")
        await client.enqueueSync(SyncResponse(syncVersion: 7, hasChanges: true, invalidate: ["leaderboard"]))
        let didChange = await service.check(force: true)
        XCTAssertTrue(didChange, "A service with no cache to purge still has to report the change")
        XCTAssertEqual(service.lastInvalidatedKinds, [.leaderboard])
    }

    // MARK: - The finalized-games banner

    func testFinalizedGamesAccumulateUntilTheReaderAcknowledgesThem() async throws {
        let (service, client) = makeService(cache: nil, label: "sync-finalized")
        let second = GameRef(gameId: "0022500513",
                             date: "2026-01-02",
                             season: "2025-26",
                             seasonType: "Regular Season",
                             home: .preview,
                             away: .previewOpponent,
                             homePts: 101,
                             awayPts: 99,
                             status: .final,
                             period: 4,
                             clock: nil,
                             finalizedAt: "2026-01-03T03:12:55Z")

        await client.enqueueSync(SyncResponse(syncVersion: 1, hasChanges: true, finalizedGames: [.preview]))
        await client.enqueueSync(SyncResponse(syncVersion: 2, hasChanges: true, finalizedGames: [second]))

        _ = await service.check(force: true)
        XCTAssertEqual(service.newlyFinalizedGames.count, 1)
        _ = await service.check(force: true)
        XCTAssertEqual(service.newlyFinalizedGames.count, 2, "The first batch was forgotten")
        XCTAssertEqual(service.finalizedSummary, "2 games finished since you last looked")

        service.acknowledgeFinalizedGames()
        XCTAssertTrue(service.newlyFinalizedGames.isEmpty)
        XCTAssertNil(service.finalizedSummary)
    }

    func testAGameThatIsNotFinalIsNotAnnounced() async {
        let (service, client) = makeService(cache: nil, label: "sync-live")
        let live = GameRef(gameId: "0022500514",
                           date: "2026-01-02",
                           season: "2025-26",
                           seasonType: "Regular Season",
                           home: .preview,
                           away: .previewOpponent,
                           homePts: 54,
                           awayPts: 61,
                           status: .live,
                           period: 2,
                           clock: "4:21",
                           finalizedAt: nil)
        await client.enqueueSync(SyncResponse(syncVersion: 1, hasChanges: true, finalizedGames: [live]))

        _ = await service.check(force: true)

        XCTAssertTrue(service.newlyFinalizedGames.isEmpty, "A game in progress was announced as finished")
    }

    // MARK: - Asking politely

    /// The client sends `since` only once it has heard from a server, so a first launch asks for
    /// everything rather than for "what changed since version 0".
    func testTheFirstPollAsksWithoutASinceVersion() async {
        let (service, client) = makeService(cache: nil, label: "sync-first")
        XCTAssertTrue(service.hasNeverSynced)

        await client.enqueueSync(SyncResponse(syncVersion: 412, hasChanges: false))
        _ = await service.check(force: true)

        let sinceValues = await client.syncSinceValues
        XCTAssertEqual(sinceValues.count, 1)
        XCTAssertNil(sinceValues.first ?? nil, "The first poll sent a since version it could not have")
        XCTAssertFalse(service.hasNeverSynced)

        await client.enqueueSync(SyncResponse(syncVersion: 413, hasChanges: false))
        _ = await service.check(force: true)
        let afterwards = await client.syncSinceValues
        XCTAssertEqual(afterwards.last ?? nil, 412, "The second poll did not send the version it learned")
    }

    /// A poll is rate limited unless the reader explicitly asked, so a foreground event cannot
    /// turn into a request per second.
    func testAnUnforcedPollRespectsTheServersPacing() async {
        let (service, client) = makeService(cache: nil, label: "sync-pacing")
        await client.enqueueSync(SyncResponse(syncVersion: 1, hasChanges: false, nextPollAfterSeconds: 900))

        _ = await service.check(force: true)
        _ = await service.check(force: false)

        let calls = await client.syncSinceValues
        XCTAssertEqual(calls.count, 1, "A second poll went out inside the pacing window")
    }

    func testAFailedPollBacksOffAndIsNotFatal() async {
        let (service, client) = makeService(cache: nil, label: "sync-failure")
        await client.setSyncFailure(.offline)

        let didChange = await service.check(force: true)

        XCTAssertFalse(didChange)
        XCTAssertEqual(service.lastError, .offline)
        XCTAssertNotNil(service.lastCheck)
        XCTAssertEqual(service.syncVersion, 0, "A failed poll invented a sync version")
        XCTAssertFalse(service.isChecking)

        // And it backs off: the next unforced poll is refused.
        _ = await service.check(force: false)
        let calls = await client.syncSinceValues
        XCTAssertEqual(calls.count, 1)
    }

    func testASuccessfulPollClearsAPreviousError() async {
        let (service, client) = makeService(cache: nil, label: "sync-recovery")
        await client.setSyncFailure(.offline)
        _ = await service.check(force: true)
        XCTAssertNotNil(service.lastError)

        await client.setSyncFailure(nil)
        await client.enqueueSync(SyncResponse(syncVersion: 9, hasChanges: false))
        _ = await service.check(force: true)

        XCTAssertNil(service.lastError)
        XCTAssertEqual(service.syncVersion, 9)
    }

    // MARK: - Constants

    func testPacingHasAFloorWhateverTheServerSays() {
        XCTAssertGreaterThanOrEqual(SyncService.minimumPollSeconds, 60)
        XCTAssertGreaterThan(SyncService.defaultPollSeconds, SyncService.minimumPollSeconds)
        XCTAssertGreaterThan(SyncService.failureBackoffSeconds, 0)
    }

    func testBackgroundIdentifiersMatchTheOnesInfoPlistDeclares() {
        XCTAssertEqual(BackgroundRefresh.appRefreshIdentifier, "com.hardwood.nbastats.refresh")
        XCTAssertEqual(BackgroundRefreshKind.appRefresh.identifier, BackgroundRefresh.appRefreshIdentifier)
        XCTAssertEqual(BackgroundRefreshKind.nightly.identifier, BackgroundRefresh.nightlyIdentifier)
        XCTAssertEqual(BackgroundRefreshKind.allCases.count, 2)
    }
}
