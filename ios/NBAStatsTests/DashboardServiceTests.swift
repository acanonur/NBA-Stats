import Foundation
import XCTest
@testable import Hardwood

/// `DashboardService` is the one place a whole dashboard becomes a screen, and the three promises
/// it makes are all about failure: one bad widget never costs the reader the others, a failed
/// refresh never blanks numbers that are already on screen, and a layout bigger than the
/// contract's 24-widget cap is split rather than rejected.
@MainActor
final class DashboardServiceTests: XCTestCase {

    private let context = ResolveContext(favoritePlayerId: 2544,
                                         favoriteTeamId: 1_610_612_747,
                                         timeZone: "America/New_York",
                                         asOf: "2026-01-02")

    /// Scratch directories are held for the life of the test case so nothing is deleted out from
    /// under a cache that is still being written to.
    private var scratch: [TemporaryDirectory] = []

    /// Every service gets its own cache directory, so nothing leaks between tests.
    private func makeService(_ label: String = "dashboard-service")
        -> (service: DashboardService, client: StubAPIClient, cache: DiskCache) {
        let directory = TemporaryDirectory(label)
        scratch.append(directory)
        let cache = DiskCache(directory: directory.url)
        let client = StubAPIClient()
        let service = DashboardService(client: client, cache: cache, catalog: makeTestCatalog())
        return (service, client, cache)
    }

    private func isUnavailable(_ state: WidgetState?) -> Bool {
        guard case .unavailable = state else { return false }
        return true
    }

    private func isLoaded(_ state: WidgetState?) -> Bool {
        guard case .loaded = state else { return false }
        return true
    }

    // MARK: - Mixed results

    /// One response carrying an ok, an error, an `unchanged` with nothing cached, and a stat the
    /// era never had — four results, four different tiles, no thrown error anywhere.
    func testAMixedResponseMapsOntoTheRightWidgetStates() async throws {
        let (service, client, _) = makeService("mixed")

        let layout = TestLayouts.layout(widgets: [
            TestLayouts.widget(id: "ok", kind: .statTile, size: .small),
            TestLayouts.widget(id: "bad", kind: .leaderboard, size: .large),
            TestLayouts.widget(id: "same", kind: .scoreboard, size: .large),
            TestLayouts.widget(id: "era", kind: .careerArc, size: .large)
        ])
        await client.setOutcomes([
            "ok": .ok(.statTile(.preview)),
            "bad": .failure(code: APIError.Code.internalError.rawValue,
                            message: "The ingest worker fell over.",
                            recoverable: true),
            "same": .unchanged,
            "era": .unavailable(reason: "Shot locations were not recorded before 1996-97.")
        ])

        await service.load(layout: layout, context: context, force: true)

        // ok — a payload on screen, not stale, with the server's availability.
        let ok = service.results["ok"]
        XCTAssertTrue(isLoaded(ok))
        XCTAssertEqual(ok?.payload, .statTile(.preview))
        XCTAssertEqual(ok?.availability, .full)
        XCTAssertEqual(ok?.isStale, false)

        // error — a failed tile carrying the server's own message.
        let bad = service.results["bad"]
        XCTAssertNil(bad?.payload, "A failed widget is showing a payload")
        let error = try XCTUnwrap(bad?.error)
        XCTAssertEqual(error.rawCode, APIError.Code.internalError.rawValue)
        XCTAssertTrue(error.isRetryable)
        XCTAssertFalse(error.userMessage.isEmpty)

        // unchanged with nothing cached — asked again, then reported honestly.
        let same = service.results["same"]
        XCTAssertNil(same?.payload)
        XCTAssertEqual(same?.error?.rawCode, "unchanged_without_cache")

        // era — never an error, never a zero.
        XCTAssertTrue(isUnavailable(service.results["era"]),
                      "An era-unavailable stat was rendered as something other than an em dash")
        XCTAssertEqual(service.results["era"]?.availability, .unavailable)

        XCTAssertNotNil(service.lastUpdated)
        XCTAssertEqual(service.dataThrough, "2026-01-02")
        XCTAssertEqual(service.knownSyncVersion, 412, "The response's sync version was not absorbed")
        XCTAssertFalse(service.isRefreshing)
    }

    func testAPartialResultStillShowsItsPayloadAndItsNotes() async throws {
        let (service, client, _) = makeService("partial")
        let layout = TestLayouts.layout(widgets: [TestLayouts.widget(id: "w1", kind: .careerArc, size: .large)])
        await client.setOutcome(.partial(.careerArc(.preview),
                                         notes: ["Six seasons predate per-game ratings."]),
                                forWidget: "w1")

        await service.load(layout: layout, context: context, force: true)

        let state = try XCTUnwrap(service.results["w1"])
        XCTAssertEqual(state.payload, .careerArc(.preview))
        XCTAssertEqual(state.availability, .partial)
        XCTAssertEqual(state.notes, ["Six seasons predate per-game ratings."])
    }

    /// A `metric_unavailable` error is the era rule wearing an error's clothes, and the tile has to
    /// treat it as the era rule.
    func testAMetricUnavailableErrorBecomesAnUnavailableTile() async throws {
        let (service, client, _) = makeService("metric-unavailable")
        let layout = TestLayouts.layout(widgets: [TestLayouts.widget(id: "w1", kind: .statTile)])
        await client.setOutcome(.failure(code: APIError.Code.metricUnavailable.rawValue,
                                         message: "TS% is not available for the 1958-59 season.",
                                         recoverable: false),
                                forWidget: "w1")

        await service.load(layout: layout, context: context, force: true)

        let state = try XCTUnwrap(service.results["w1"])
        XCTAssertTrue(isUnavailable(state))
        if case .unavailable(let reason) = state {
            XCTAssertTrue(reason.contains("1958-59"), reason)
        }
    }

    func testASuccessWithNoPayloadIsAFailedTileRatherThanABlankOne() async throws {
        let (service, client, _) = makeService("no-payload")
        let layout = TestLayouts.layout(widgets: [TestLayouts.widget(id: "w1", kind: .statTile)])
        await client.setOutcome(.okWithNoPayload, forWidget: "w1")

        await service.load(layout: layout, context: context, force: true)

        XCTAssertNil(service.results["w1"]?.payload)
        XCTAssertNotNil(service.results["w1"]?.error)
    }

    // MARK: - Splitting

    /// The contract caps a resolve at 24 widgets, so a 25-widget dashboard has to become two
    /// requests rather than one `400 too_many_widgets`.
    func testALayoutOverTheCapSplitsIntoTwoRequests() async throws {
        let (service, client, _) = makeService("split")
        let widgets = TestLayouts.manyWidgets(25)
        let layout = TestLayouts.layout(widgets: widgets)

        await service.load(layout: layout, context: context, force: true)

        let counts = await client.resolveWidgetCounts
        XCTAssertEqual(counts.count, 2, "A 25-widget layout was not split")
        XCTAssertEqual(counts.reduce(0, +), 25)
        XCTAssertEqual(counts.sorted(), [1, 24])
        for count in counts {
            XCTAssertLessThanOrEqual(count, DashboardResolveRequest.maxWidgets)
        }
        // Every widget still ended up with a state.
        XCTAssertEqual(service.results.count, 25)
        XCTAssertTrue(widgets.allSatisfy { isLoaded(service.results[$0.id]) })
    }

    func testALayoutAtTheCapIsOneRequest() async throws {
        let (service, client, _) = makeService("at-cap")
        let layout = TestLayouts.layout(widgets: TestLayouts.manyWidgets(24))

        await service.load(layout: layout, context: context, force: true)

        let counts = await client.resolveWidgetCounts
        XCTAssertEqual(counts, [24])
    }

    func testAnEmptyLayoutAsksForNothing() async {
        let (service, client, _) = makeService("empty")

        await service.load(layout: TestLayouts.layout(widgets: []), context: context, force: true)

        let requests = await client.resolveRequests
        XCTAssertTrue(requests.isEmpty, "An empty dashboard still hit the network")
        XCTAssertTrue(service.results.isEmpty)
        XCTAssertFalse(service.isRefreshing)
    }

    // MARK: - Failure never blanks the screen

    /// Stale-while-revalidate's whole point: a refresh that fails leaves the reader's numbers up,
    /// marked stale, rather than replacing them with an error.
    func testAFailedRefreshKeepsThePayloadAndMarksItStale() async throws {
        let (service, client, _) = makeService("stale")
        let layout = TestLayouts.layout(widgets: [TestLayouts.widget(id: "w1", kind: .statTile)])

        await client.setOutcome(.ok(.statTile(.preview)), forWidget: "w1")
        await service.load(layout: layout, context: context, force: true)
        XCTAssertEqual(service.results["w1"]?.payload, .statTile(.preview))
        XCTAssertEqual(service.results["w1"]?.isStale, false)

        await client.setResolveFailure(.offline)
        await service.load(layout: layout, context: context, force: true)

        XCTAssertEqual(service.results["w1"]?.payload, .statTile(.preview),
                       "A failed refresh blanked a tile that had numbers on it")
        XCTAssertEqual(service.results["w1"]?.isStale, true, "The tile is not marked stale")
        XCTAssertEqual(service.lastError, .offline)
        XCTAssertFalse(service.isRefreshing)
    }

    /// With nothing on screen to keep, the same failure is shown as a failed tile.
    func testAFailedFirstLoadShowsAFailedTile() async throws {
        let (service, client, _) = makeService("first-failure")
        await client.setResolveFailure(.timeout)
        let layout = TestLayouts.layout(widgets: [TestLayouts.widget(id: "w1", kind: .statTile)])

        await service.load(layout: layout, context: context, force: true)

        XCTAssertNil(service.results["w1"]?.payload)
        XCTAssertEqual(service.results["w1"]?.error, .timeout)
        XCTAssertEqual(service.lastError, .timeout)
    }

    /// A server that simply does not answer for a widget is a failure of that widget, not silence.
    func testAMissingResultBecomesAFailedTile() async throws {
        let (service, client, _) = makeService("missing")
        // The stub answers for every widget it is sent, so ask about a layout whose widget is
        // dropped by shrinking the response: an `unchanged` for a widget with nothing cached is
        // the closest honest analogue, and it must not leave the tile loading forever.
        let layout = TestLayouts.layout(widgets: [TestLayouts.widget(id: "w1", kind: .statTile)])
        await client.setOutcome(.unchanged, forWidget: "w1")

        await service.load(layout: layout, context: context, force: true)

        XCTAssertEqual(service.results["w1"]?.isLoading, false, "The tile is still spinning")
        XCTAssertNotNil(service.results["w1"]?.error)
    }

    // MARK: - Retry

    func testRetryReplacesOneFailedTileWithoutTouchingTheOthers() async throws {
        let (service, client, _) = makeService("retry")
        let layout = TestLayouts.layout(widgets: [
            TestLayouts.widget(id: "good", kind: .statTile),
            TestLayouts.widget(id: "bad", kind: .leaderboard, size: .large)
        ])
        await client.setOutcomes([
            "good": .ok(.statTile(.preview)),
            "bad": .failure(code: APIError.Code.internalError.rawValue, message: "Fell over.", recoverable: true)
        ])
        await service.load(layout: layout, context: context, force: true)
        XCTAssertNotNil(service.results["bad"]?.error)

        await client.setOutcome(.ok(.leaderboard(.preview)), forWidget: "bad")
        await service.retry(widgetID: "bad")

        XCTAssertEqual(service.results["bad"]?.payload, .leaderboard(.preview))
        XCTAssertEqual(service.results["good"]?.payload, .statTile(.preview), "Retrying one tile disturbed another")

        // The retry asked about exactly one widget.
        let requests = await client.resolveRequests
        XCTAssertEqual(requests.last?.widgets.count, 1)
        XCTAssertEqual(requests.last?.widgets.first?.id, "bad")
        XCTAssertNil(requests.last?.knownSyncVersion, "A retry asked for a verdict instead of a payload")
    }

    func testRetryOfAnUnknownWidgetDoesNothing() async {
        let (service, client, _) = makeService("retry-unknown")
        let layout = TestLayouts.layout(widgets: [TestLayouts.widget(id: "w1", kind: .statTile)])
        await service.load(layout: layout, context: context, force: true)
        let before = await client.resolveRequests

        await service.retry(widgetID: "not-in-this-layout")

        let after = await client.resolveRequests
        XCTAssertEqual(before.count, after.count)
    }

    // MARK: - Sync plumbing

    func testTheKnownSyncVersionIsSentAndUpdated() async throws {
        let (service, client, _) = makeService("sync-version")
        let layout = TestLayouts.layout(widgets: [TestLayouts.widget(id: "w1", kind: .statTile)])

        service.updateKnownSyncVersion(411)
        await service.load(layout: layout, context: context, force: false)

        let requests = await client.resolveRequests
        XCTAssertEqual(requests.first?.knownSyncVersion, 411)
        XCTAssertEqual(requests.first?.layoutId, layout.id)
        XCTAssertEqual(requests.first?.context, context)
        // The response's own version replaces it for the next round trip.
        XCTAssertEqual(service.knownSyncVersion, 412)
    }

    func testAForcedLoadAsksForThePayloadRatherThanAVerdict() async {
        let (service, client, _) = makeService("forced")
        let layout = TestLayouts.layout(widgets: [TestLayouts.widget(id: "w1", kind: .statTile)])
        service.updateKnownSyncVersion(411)

        await service.load(layout: layout, context: context, force: true)

        let requests = await client.resolveRequests
        XCTAssertNil(requests.first?.knownSyncVersion)
    }

    /// `/v1/sync` telling the client to drop a kind has to mark the tiles showing it as stale, so
    /// the reader can see the numbers are behind while the refresh runs.
    func testInvalidateMarksTheAffectedTilesStale() async throws {
        let (service, client, cache) = makeService("invalidate")
        let layout = TestLayouts.layout(widgets: [
            TestLayouts.widget(id: "board", kind: .scoreboard, size: .large),
            TestLayouts.widget(id: "arc", kind: .careerArc, size: .large)
        ])
        await client.setDefaultOutcome(.okPreview)
        await service.load(layout: layout, context: context, force: true)
        XCTAssertEqual(service.results["board"]?.isStale, false)

        await service.invalidate(kinds: [.scoreboard])

        XCTAssertEqual(service.results["board"]?.isStale, true, "A dropped kind was not marked stale")
        XCTAssertEqual(service.results["arc"]?.isStale, false, "An unrelated tile was marked stale")
        XCTAssertNotNil(service.results["board"]?.payload, "Invalidating blanked a tile")
        _ = await cache.statistics()
    }

    func testInvalidateWithNoKindsChangesNothing() async {
        let (service, _, _) = makeService("invalidate-empty")
        let layout = TestLayouts.layout(widgets: [TestLayouts.widget(id: "w1", kind: .statTile)])
        await service.load(layout: layout, context: context, force: true)
        await service.invalidate(kinds: [])
        XCTAssertEqual(service.results["w1"]?.isStale, false)
    }

    // MARK: - Housekeeping

    func testResetForgetsEverything() async {
        let (service, _, _) = makeService("reset")
        let layout = TestLayouts.layout(widgets: [TestLayouts.widget(id: "w1", kind: .statTile)])
        await service.load(layout: layout, context: context, force: true)
        XCTAssertFalse(service.results.isEmpty)

        service.reset()

        XCTAssertTrue(service.results.isEmpty)
        XCTAssertNil(service.lastUpdated)
        XCTAssertNil(service.lastError)
    }

    /// A widget the reader deleted must not leave its state behind, or the next layout with the
    /// same widget id would open on somebody else's numbers.
    func testAWidgetThatLeavesTheLayoutLosesItsState() async {
        let (service, _, _) = makeService("pruning")
        let first = TestLayouts.layout(widgets: [
            TestLayouts.widget(id: "keep", kind: .statTile),
            TestLayouts.widget(id: "drop", kind: .leaderboard, size: .large)
        ])
        await service.load(layout: first, context: context, force: true)
        XCTAssertEqual(service.results.count, 2)

        let second = TestLayouts.layout(widgets: [TestLayouts.widget(id: "keep", kind: .statTile)])
        await service.load(layout: second, context: context, force: true)

        XCTAssertNil(service.results["drop"], "A removed widget kept its state")
        XCTAssertNotNil(service.results["keep"])
    }

    // MARK: - Cache keys

    func testTheCacheKeyFollowsTheConfigurationAndTheContext() {
        let config: [String: JSONValue] = ["metric": .string("ts_pct"), "season": .string("2025-26")]
        let sameConfigDifferentOrder: [String: JSONValue] = ["season": .string("2025-26"), "metric": .string("ts_pct")]

        let base = DiskCache.key(kind: .statTile, config: config, context: context)
        XCTAssertEqual(base, DiskCache.key(kind: .statTile, config: sameConfigDifferentOrder, context: context),
                       "Dictionary ordering changed the cache key")
        XCTAssertNotEqual(base, DiskCache.key(kind: .leaderboard, config: config, context: context))
        XCTAssertNotEqual(base,
                          DiskCache.key(kind: .statTile,
                                        config: ["metric": .string("pts"), "season": .string("2025-26")],
                                        context: context))
        // "Last night" means a different slate in Honolulu, so the time zone is part of the key.
        let honolulu = ResolveContext(favoritePlayerId: context.favoritePlayerId,
                                      favoriteTeamId: context.favoriteTeamId,
                                      timeZone: "Pacific/Honolulu",
                                      asOf: context.asOf)
        XCTAssertNotEqual(base, DiskCache.key(kind: .statTile, config: config, context: honolulu))
    }

    func testEveryWidgetKindHasASensibleCacheTTL() {
        XCTAssertEqual(WidgetKind.scoreboard.defaultCacheTTLSeconds, 60)
        XCTAssertEqual(WidgetKind.dailyMovers.defaultCacheTTLSeconds, 60)
        XCTAssertEqual(WidgetKind.statTile.defaultCacheTTLSeconds, 300)
        XCTAssertEqual(WidgetKind.leaderboard.defaultCacheTTLSeconds, 600)
        XCTAssertEqual(WidgetKind.careerArc.defaultCacheTTLSeconds, 3600)
    }

    // MARK: - The disk cache itself

    func testTheCacheStoresReadsAndPurgesByKind() async {
        let directory = TemporaryDirectory("disk-cache")
        let cache = DiskCache(directory: directory.url)
        let scoreboardKey = DiskCache.key(kind: .scoreboard, config: [:], context: context)
        let arcKey = DiskCache.key(kind: .careerArc, config: [:], context: context)
        let payload = Data("{\"ok\":true}".utf8)

        await cache.store(payload, forKey: scoreboardKey, kind: .scoreboard, ttlSeconds: 600)
        await cache.store(payload, forKey: arcKey, kind: .careerArc, ttlSeconds: 600)

        let stored = await cache.entry(forKey: scoreboardKey)
        XCTAssertEqual(stored?.data, payload)
        XCTAssertEqual(stored?.kind, .scoreboard)
        XCTAssertEqual(stored?.isExpired(), false)
        let arcIsFresh = await cache.hasFreshEntry(forKey: arcKey)
        XCTAssertTrue(arcIsFresh)

        await cache.purgeKinds([.scoreboard])
        let purged = await cache.entry(forKey: scoreboardKey)
        let survivor = await cache.entry(forKey: arcKey)
        XCTAssertNil(purged, "A purged entry is still readable")
        XCTAssertNotNil(survivor, "Purging one kind took another with it")

        await cache.removeAll()
        let afterRemoveAll = await cache.entry(forKey: arcKey)
        let statistics = await cache.statistics()
        XCTAssertNil(afterRemoveAll)
        XCTAssertEqual(statistics.entryCount, 0)
    }

    func testAnExpiredEntryIsKeptSoATileCanShowStaleNumbers() async {
        let directory = TemporaryDirectory("disk-cache-expiry")
        let cache = DiskCache(directory: directory.url)
        let key = DiskCache.key(kind: .scoreboard, config: [:], context: context)
        await cache.store(Data("{}".utf8), forKey: key, kind: .scoreboard, ttlSeconds: 1)

        // The TTL floor is one second, so the entry is fresh now and expired later; the point is
        // that an expired entry is still *readable*.
        let entry = await cache.entry(forKey: key)
        XCTAssertNotNil(entry)
        XCTAssertEqual(entry?.isExpired(now: Date().addingTimeInterval(3600)), true)
        let stillThere = await cache.entry(forKey: key)
        XCTAssertNotNil(stillThere, "An expired entry was thrown away")
    }
}
