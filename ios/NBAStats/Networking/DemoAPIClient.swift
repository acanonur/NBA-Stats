import Foundation
import OSLog

/// Serves the bundled golden fixtures so the app is fully usable with no server.
///
/// The fixtures in `Resources/Fixtures/` are the same files the contract tests decode
/// (`contracts/fixtures/*.json`, copied in by `scripts/sync_contracts.sh`), so demo mode shows
/// real shapes rather than invented ones. Anything the bundle does not contain degrades to one
/// failed tile or an empty list — never to a crash, and never to a fabricated number.
public actor DemoAPIClient: APIClientProtocol {

    /// Enough delay for a loading state to be visible in a preview and in the simulator, little
    /// enough that the app still feels instant.
    public static let defaultLatencyMilliseconds: UInt64 = 140

    private let bundle: Bundle
    private let latencyMilliseconds: UInt64
    private let decoder: JSONDecoder

    private var payloads: [WidgetKind: WidgetPayload] = [:]
    private var missingKinds: Set<WidgetKind> = []
    private var players: [PlayerID: PlayerRef] = [:]
    private var teamsByID: [TeamID: TeamRef] = [:]
    private var didBuildIndex = false

    /// Demo mode is a frozen snapshot, so its sync version never moves.
    private let demoSyncVersion = 1

    private static let logger = Logger(subsystem: "com.hardwood.nbastats", category: "demo")

    public init(bundle: Bundle = .main,
                latencyMilliseconds: UInt64 = DemoAPIClient.defaultLatencyMilliseconds) {
        self.bundle = bundle
        self.latencyMilliseconds = latencyMilliseconds
        self.decoder = APIClient.makeDecoder()
    }

    // MARK: - Routes

    public func meta() async throws -> MetaResponse {
        await pause()
        if let data = fixtureData(named: "meta"),
           let meta = try? decoder.decode(MetaResponse.self, from: data) {
            return meta
        }
        buildIndexIfNeeded()
        let season = demoSeason()
        return MetaResponse(
            syncVersion: demoSyncVersion,
            dataThrough: demoDataThrough(),
            currentSeason: season,
            seasons: [SeasonInfo(season: season,
                                 seasonTypes: ["Regular Season"],
                                 isCurrent: true,
                                 hasAdvanced: true,
                                 gameCount: nil)],
            metrics: bundledDocument(MetricCatalogDocument.self, named: "metrics"),
            widgets: bundledDocument(WidgetCatalogDocument.self, named: "widgets"),
            teams: sortedTeams(),
            // The era boundaries the contract fixes in §6; they are facts about the league's
            // record, not sample data, so demo mode can state them.
            coverage: Coverage(advancedFrom: "1996-97",
                               trackingFrom: "2013-14",
                               hustleFrom: "2016-17",
                               shotChartsFrom: "1996-97",
                               seasonFrom: "1946-47"),
            attribution: "Stats via NBA.com. Not endorsed by or affiliated with the NBA."
        )
    }

    public func presets() async throws -> PresetsResponse {
        await pause()
        guard let document = bundledDocument(PresetCatalogDocument.self, named: "presets") else {
            throw APIError.demoModeMissingFixture("presets.json")
        }
        return PresetsResponse(schemaVersion: document.schemaVersion,
                               version: 1,
                               presets: document.presets,
                               subjectTokens: document.subjectTokens)
    }

    public func health() async throws -> HealthResponse {
        await pause()
        return HealthResponse(status: "ok",
                              version: "demo",
                              syncVersion: demoSyncVersion,
                              dataThrough: demoDataThrough(),
                              databaseReady: true,
                              seededDemoData: true)
    }

    public func sync(since: Int?) async throws -> SyncResponse {
        await pause()
        if let data = fixtureData(named: "sync"),
           let response = try? decoder.decode(SyncResponse.self, from: data) {
            guard since != response.syncVersion else {
                // The fixture is static: once the client has caught up with it, nothing has
                // changed, whatever the file says. Otherwise every poll would re-invalidate.
                return SyncResponse(syncVersion: response.syncVersion,
                                    previousVersion: since,
                                    serverTime: Formatting.timestampString(Date()),
                                    dataThrough: response.dataThrough,
                                    hasChanges: false,
                                    nextPollAfterSeconds: response.nextPollAfterSeconds ?? 900)
            }
            return response
        }
        let known = since ?? demoSyncVersion
        return SyncResponse(syncVersion: demoSyncVersion,
                            previousVersion: since,
                            serverTime: Formatting.timestampString(Date()),
                            dataThrough: demoDataThrough(),
                            hasChanges: known != demoSyncVersion,
                            nextPollAfterSeconds: 900)
    }

    public func searchPlayers(query: String, limit: Int) async throws -> PlayerSearchResponse {
        await pause()
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 2 else {
            return PlayerSearchResponse(query: trimmed, results: [], nextCursor: nil)
        }
        buildIndexIfNeeded()
        let needle = DemoAPIClient.folded(trimmed)
        let scored = players.values.compactMap { player -> (PlayerRef, Int)? in
            guard let score = DemoAPIClient.matchScore(for: player, needle: needle) else { return nil }
            return (player, score)
        }
        let ordered = scored.sorted { first, second in
            if first.1 != second.1 { return first.1 < second.1 }
            return first.0.name.localizedCaseInsensitiveCompare(second.0.name) == .orderedAscending
        }
        let capped = ordered.prefix(max(1, min(limit, 50)))
        let results = capped.map { entry in
            PlayerSearchResult(player: entry.0,
                               fromYear: nil,
                               toYear: nil,
                               matchScore: entry.1 == 0 ? 1.0 : 0.6)
        }
        return PlayerSearchResponse(query: trimmed, results: Array(results), nextCursor: nil)
    }

    public func player(_ id: PlayerID) async throws -> PlayerDetailResponse {
        await pause()
        let data = fixtureData(named: "player_\(id)") ?? fixtureData(named: "player_detail")
        if let data = data,
           let response = try? decoder.decode(PlayerDetailResponse.self, from: data),
           response.player.playerId == id {
            return response
        }
        buildIndexIfNeeded()
        if let player = players[id] {
            // The bundle knows who this is but carries no season table for them. An honest empty
            // history is better than inventing one.
            return PlayerDetailResponse(player: player, bio: nil, careerTotals: nil, seasons: [])
        }
        throw APIError.demoModeMissingFixture("player_\(id).json")
    }

    public func teams() async throws -> TeamsResponse {
        await pause()
        if let data = fixtureData(named: "teams"),
           let response = try? decoder.decode(TeamsResponse.self, from: data),
           !response.teams.isEmpty {
            return response
        }
        buildIndexIfNeeded()
        return TeamsResponse(teams: sortedTeams())
    }

    /// Answers a whole dashboard from the fixtures, one result per requested widget.
    ///
    /// Each result is the fixture for that widget's *kind*, re-labelled with the widget's own id,
    /// so a demo dashboard renders every tile no matter how its widgets are configured.
    public func resolve(_ request: DashboardResolveRequest) async throws -> DashboardResolveResponse {
        guard request.widgets.count <= DashboardResolveRequest.maxWidgets else {
            throw APIError.server(code: APIError.Code.tooManyWidgets.rawValue,
                                  message: "A resolve request carries at most \(DashboardResolveRequest.maxWidgets) widgets.",
                                  status: 400,
                                  recoverable: false)
        }
        await pause()
        let generatedAt = Formatting.timestampString(Date())
        var results: [ResolveResult] = []
        results.reserveCapacity(request.widgets.count)
        for widget in request.widgets {
            if let payload = payload(for: widget.kind) {
                results.append(ResolveResult(widgetId: widget.id,
                                             kindRaw: widget.kind.rawValue,
                                             status: .ok,
                                             payload: payload,
                                             generatedAt: generatedAt,
                                             ttlSeconds: widget.kind.defaultCacheTTLSeconds,
                                             availability: DemoAPIClient.demoAvailability(for: widget.kind),
                                             notes: []))
            } else {
                let name = "widget_\(widget.kind.rawValue).json"
                results.append(ResolveResult(widgetId: widget.id,
                                             kindRaw: widget.kind.rawValue,
                                             status: .error,
                                             error: APIErrorBody(code: "demo_fixture_missing",
                                                                 message: "Demo mode has no bundled data for this widget (\(name)).",
                                                                 recoverable: false),
                                             generatedAt: generatedAt))
            }
        }
        let context = ResolveContextPayload(favoritePlayerId: request.context.favoritePlayerId,
                                            favoriteTeamId: request.context.favoriteTeamId,
                                            timeZone: request.context.timeZone,
                                            asOf: request.context.asOf ?? demoDataThrough(),
                                            season: demoSeason())
        return DashboardResolveResponse(syncVersion: demoSyncVersion,
                                        dataThrough: demoDataThrough(),
                                        generatedAt: generatedAt,
                                        resolvedContext: context,
                                        results: results)
    }

    // MARK: - Fixtures

    /// What the service would have called this result, so demo mode does not look *more*
    /// confident than the real thing.
    ///
    /// The fixtures are bare payloads and carry no result envelope, so the qualifier has to be
    /// restated here. Only one kind needs it: a projection is an estimate of a game that has not
    /// been played, and `contracts/fixtures/dashboard_resolve.json` shows the service answering
    /// `availability: "estimated"` for it. Without this, the demo tile would lose the chrome-level
    /// "est." badge that `WidgetContainer` draws from this value, which is exactly the
    /// projection-dressed-as-a-record that `docs/PROJECTION.md` §7 rule 5 forbids.
    ///
    /// Both projection kinds need it, for the same reason and with the same force.
    private static func demoAvailability(for kind: WidgetKind) -> MetricAvailability {
        switch kind {
        case .nextGameProjection, .projectionBoard: return .estimated
        default: return .full
        }
    }

    /// The payload for a kind, decoded once and kept. A kind whose fixture is missing or
    /// unreadable is remembered as missing so the bundle is not searched again on every resolve.
    private func payload(for kind: WidgetKind) -> WidgetPayload? {
        if let cached = payloads[kind] { return cached }
        if missingKinds.contains(kind) { return nil }
        guard let data = fixtureData(named: "widget_\(kind.rawValue)") else {
            missingKinds.insert(kind)
            return nil
        }
        do {
            let payload = try decodePayload(kind: kind, data: data)
            payloads[kind] = payload
            return payload
        } catch {
            DemoAPIClient.logger.error("Fixture widget_\(kind.rawValue, privacy: .public).json could not be decoded: \(error.localizedDescription, privacy: .public)")
            missingKinds.insert(kind)
            return nil
        }
    }

    /// Reads a bare payload document, and tolerates a fixture that was saved as a whole resolve
    /// result by unwrapping its `payload` member.
    private func decodePayload(kind: WidgetKind, data: Data) throws -> WidgetPayload {
        do {
            return try WidgetPayload.decode(kind: kind, from: data, using: decoder)
        } catch {
            if let object = try? decoder.decode([String: JSONValue].self, from: data),
               let inner = object["payload"], !inner.isNull,
               let nested = try? JSONEncoder().encode(inner) {
                return try WidgetPayload.decode(kind: kind, from: nested, using: decoder)
            }
            throw error
        }
    }

    private func fixtureData(named name: String) -> Data? {
        let url = bundle.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
            ?? bundle.url(forResource: name, withExtension: "json")
        guard let url = url else { return nil }
        return try? Data(contentsOf: url)
    }

    private func bundledDocument<T: Decodable>(_ type: T.Type, named name: String) -> T? {
        let url = bundle.url(forResource: name, withExtension: "json", subdirectory: "Contracts")
            ?? bundle.url(forResource: name, withExtension: "json")
        guard let url = url, let data = try? Data(contentsOf: url) else { return nil }
        return try? decoder.decode(type, from: data)
    }

    // MARK: - Index

    /// Harvests the players and teams that appear anywhere in the fixtures, which is what makes
    /// search and the team list work with no server.
    private func buildIndexIfNeeded() {
        guard !didBuildIndex else { return }
        didBuildIndex = true
        for kind in WidgetKind.allCases {
            guard let payload = payload(for: kind) else { continue }
            harvest(payload)
        }
        if let data = fixtureData(named: "teams"),
           let response = try? decoder.decode(TeamsResponse.self, from: data) {
            for team in response.teams { teamsByID[team.teamId] = team }
        }
    }

    private func harvest(_ payload: WidgetPayload) {
        switch payload {
        case .statTile(let value):
            note(subject: value.subject)
        case .playerSnapshot(let value):
            note(player: value.player)
        case .leaderboard(let value):
            for row in value.rows {
                note(player: row.player)
                note(team: row.team)
            }
        case .gameLog(let value):
            note(player: value.player)
        case .trendChart:
            // Series carry labels, not subjects.
            break
        case .fourFactors(let value):
            note(team: value.team)
        case .shotProfile(let value):
            note(subject: value.subject)
        case .comparison(let value):
            for subject in value.subjects {
                note(player: subject.player)
                note(team: subject.team)
            }
        case .scoreboard(let value):
            for entry in value.games {
                note(team: entry.game.home)
                note(team: entry.game.away)
                for performer in entry.topPerformers { note(player: performer.player) }
            }
        case .dailyMovers(let value):
            for row in value.rows { note(player: row.player) }
        case .teamEfficiency(let value):
            for row in value.rows { note(team: row.team) }
        case .nextGameProjection(let value):
            note(player: value.player)
            note(team: value.game?.opponent)
        case .projectionBoard(let value):
            for row in value.rows { note(player: row.player) }
        case .careerArc(let value):
            note(player: value.player)
        }
    }

    private func note(player: PlayerRef?) {
        guard let player = player else { return }
        players[player.playerId] = player
    }

    private func note(team: TeamRef?) {
        guard let team = team else { return }
        teamsByID[team.teamId] = team
    }

    private func note(subject: SubjectRef) {
        note(player: subject.player)
        note(team: subject.team)
    }

    private func sortedTeams() -> [TeamRef] {
        teamsByID.values.sorted { $0.abbr < $1.abbr }
    }

    // MARK: - Derived league state

    /// The season the fixtures describe, falling back to the season the calendar is in.
    private func demoSeason() -> String {
        if let payload = payload(for: .leaderboard),
           case .leaderboard(let leaderboard) = payload,
           let season = leaderboard.season, !season.isEmpty {
            return season
        }
        if let payload = payload(for: .playerSnapshot),
           case .playerSnapshot(let snapshot) = payload,
           let season = snapshot.season, !season.isEmpty {
            return season
        }
        return DemoAPIClient.currentSeasonString()
    }

    /// The last date the fixtures have games for.
    private func demoDataThrough() -> String? {
        if let payload = payload(for: .scoreboard), case .scoreboard(let scoreboard) = payload {
            return scoreboard.date
        }
        if let payload = payload(for: .dailyMovers), case .dailyMovers(let movers) = payload {
            return movers.date
        }
        return nil
    }

    /// `"2025-26"` for any date in the 2025-26 season. The league names a season for the calendar
    /// year it tips off in, and it tips off in October.
    public static func currentSeasonString(now: Date = Date(),
                                           calendar: Calendar = Calendar(identifier: .gregorian)) -> String {
        let components = calendar.dateComponents([.year, .month], from: now)
        let year = components.year ?? 2025
        let month = components.month ?? 1
        let start = month >= 10 ? year : year - 1
        let suffix = String(format: "%02d", (start + 1) % 100)
        return "\(start)-\(suffix)"
    }

    // MARK: - Search helpers

    private static func folded(_ string: String) -> String {
        string.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: Locale.current)
    }

    /// 0 for a prefix match on the whole name or the last name, 1 for any other match, `nil` for
    /// no match — the same ordering the service documents, minus the career-minutes tie-break it
    /// has data for and this does not.
    private static func matchScore(for player: PlayerRef, needle: String) -> Int? {
        let name = folded(player.name)
        if name.hasPrefix(needle) { return 0 }
        if let last = player.lastName, folded(last).hasPrefix(needle) { return 0 }
        let parts = name.split(separator: " ")
        if parts.contains(where: { $0.hasPrefix(needle) }) { return 0 }
        return name.contains(needle) ? 1 : nil
    }

    private func pause() async {
        guard latencyMilliseconds > 0 else { return }
        try? await Task.sleep(nanoseconds: latencyMilliseconds * 1_000_000)
    }
}
