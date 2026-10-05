import Foundation
import XCTest
@testable import Hardwood

/// The league routes are the one place that knows every path and query parameter, and the backend
/// spells them differently per league (`teamId` against `clubCode`, `date` against `round`). These
/// tests pin the URL of every route under both prefixes, so a change on either side is a red test
/// rather than a 404 on the user's Mac, and pin what the client puts on the wire: the API key on
/// every request, and never an `Origin` header.
final class LeagueRouteTests: XCTestCase {

    private let base: URL = URL(string: "http://127.0.0.1:8000/v1") ?? URL(fileURLWithPath: "/")

    override func setUp() {
        super.setUp()
        StubLeagueProtocol.reset()
    }

    override func tearDown() {
        StubLeagueProtocol.reset()
        super.tearDown()
    }

    // MARK: - One URL per route

    /// A route, the path it must produce after `/v1` and the league prefix, and its query.
    private struct RouteCase {
        let route: LeagueRoute
        let path: String
        let query: [String: String]

        init(_ route: LeagueRoute, _ path: String, _ query: [String: String] = [:]) {
            self.route = route
            self.path = path
            self.query = query
        }
    }

    private var stateAndMatchupCases: [RouteCase] {
        [
            RouteCase(LeagueRoutes.leagues(), "/leagues"),
            RouteCase(LeagueRoutes.elMeta(), "/meta"),
            RouteCase(LeagueRoutes.teamMatchup(.nba, team: "1610612743", window: 5),
                      "/teams/1610612743/matchup", ["window": "5"]),
            RouteCase(LeagueRoutes.teamMatchup(.euroleague, team: "ZZA"), "/teams/ZZA/matchup"),
            RouteCase(LeagueRoutes.pairMatchup(.nba, home: "1", away: "2", window: 7, season: "2025-26"),
                      "/matchups",
                      ["homeTeamId": "1", "awayTeamId": "2", "window": "7", "season": "2025-26"]),
            RouteCase(LeagueRoutes.pairMatchup(.euroleague, home: "ZZA", away: "ZZB"),
                      "/matchups", ["homeTeamId": "ZZA", "awayTeamId": "ZZB"]),
            RouteCase(LeagueRoutes.gameMatchup(.nba, gameId: "0022500001"), "/games/0022500001/matchup"),
            RouteCase(LeagueRoutes.gameMatchup(.euroleague, gameId: "E2026-R05-01", window: 5),
                      "/games/E2026-R05-01/matchup", ["window": "5"])
        ]
    }

    private var defenseAndProjectionCases: [RouteCase] {
        [
            RouteCase(LeagueRoutes.teamDefense(.nba, team: "1610612743", window: 0, basis: "perMinute"),
                      "/teams/1610612743/defense-by-position", ["window": "0", "basis": "perMinute"]),
            RouteCase(LeagueRoutes.teamDefense(.euroleague, team: "ZZA", scheme: "workbook5"),
                      "/teams/ZZA/defense-by-position", ["scheme": "workbook5"]),
            RouteCase(LeagueRoutes.defenseTable(.nba, season: "2025-26"),
                      "/defense-by-position", ["season": "2025-26"]),
            RouteCase(LeagueRoutes.defenseTable(.euroleague, window: 5, basis: "perGame", scheme: "gfc"),
                      "/defense-by-position", ["window": "5", "basis": "perGame", "scheme": "gfc"]),
            RouteCase(LeagueRoutes.slate(date: "next"), "/projections", ["date": "next"]),
            RouteCase(LeagueRoutes.elSlate(round: "3"), "/projections", ["round": "3"]),
            RouteCase(LeagueRoutes.elRound(3), "/rounds/3"),
            RouteCase(LeagueRoutes.gameProjection(.nba, gameId: "0022500001"), "/games/0022500001/projection"),
            RouteCase(LeagueRoutes.gameProjection(.euroleague, gameId: "E2026-R05-01"),
                      "/games/E2026-R05-01/projection"),
            RouteCase(LeagueRoutes.review(.nba, date: "2026-01-02", round: 4, season: "2025-26"),
                      "/projections/review", ["date": "2026-01-02", "season": "2025-26"]),
            RouteCase(LeagueRoutes.review(.euroleague, date: "2026-01-02", round: 4),
                      "/projections/review", ["round": "4"])
        ]
    }

    private var availabilityNewsAndSourceCases: [RouteCase] {
        [
            RouteCase(LeagueRoutes.availability(.nba, team: "1610612743", date: "next", round: 3),
                      "/availability", ["teamId": "1610612743", "date": "next"]),
            RouteCase(LeagueRoutes.availability(.euroleague, team: "ZZA", date: "next", round: 3),
                      "/availability", ["clubCode": "ZZA", "round": "3"]),
            RouteCase(LeagueRoutes.reviewQueue(.nba), "/availability/review-queue"),
            RouteCase(LeagueRoutes.reviewQueue(.euroleague), "/review-queue"),
            RouteCase(LeagueRoutes.news(.nba, team: "1610612743", limit: 20),
                      "/news", ["teamId": "1610612743", "limit": "20"]),
            RouteCase(LeagueRoutes.news(.euroleague, team: "ZZA", limit: 20, player: "demo-101"),
                      "/news", ["teamId": "ZZA", "limit": "20", "playerId": "demo-101"]),
            RouteCase(LeagueRoutes.sources(.nba), "/sources"),
            RouteCase(LeagueRoutes.sources(.euroleague), "/sources"),
            RouteCase(LeagueRoutes.modelSettings(.nba), "/model-settings"),
            RouteCase(LeagueRoutes.modelSettings(.euroleague), "/model-settings")
        ]
    }

    private var euroLeagueOnlyCases: [RouteCase] {
        [
            RouteCase(LeagueRoutes.elTeams(), "/teams"),
            RouteCase(LeagueRoutes.elClub("ZZA"), "/teams/ZZA"),
            RouteCase(LeagueRoutes.elScorers(round: 3, perClub: 5), "/rounds/3/scorers", ["perClub": "5"]),
            RouteCase(LeagueRoutes.elGames(round: 3, club: "ZZA"), "/games", ["round": "3", "clubCode": "ZZA"]),
            RouteCase(LeagueRoutes.elBox("E2026-R02-01"), "/games/E2026-R02-01"),
            RouteCase(LeagueRoutes.elPlayerStats(perMode: "PerGame", club: "ZZA", minGames: 2, sort: "pts", limit: 50),
                      "/stats/players",
                      ["perMode": "PerGame", "clubCode": "ZZA", "minGames": "2", "sort": "pts", "limit": "50"]),
            RouteCase(LeagueRoutes.elPlayer("demo-101"), "/players/demo-101"),
            RouteCase(LeagueRoutes.elGameLog("demo-101"), "/players/demo-101/gamelog"),
            RouteCase(LeagueRoutes.elRatings(asOfRound: 2), "/ratings", ["asOfRound": "2"]),
            RouteCase(LeagueRoutes.elMethod(), "/method")
        ]
    }

    private var nbaFlatCases: [RouteCase] {
        [
            RouteCase(LeagueRoutes.nbaTeams(), "/teams"),
            RouteCase(LeagueRoutes.nbaTeam(1610612743, rosterMetrics: ["pts_pg", "reb_pg"]),
                      "/teams/1610612743", ["rosterMetrics": "pts_pg,reb_pg"]),
            RouteCase(LeagueRoutes.nbaGames(date: "2026-01-02"), "/games", ["date": "2026-01-02"]),
            RouteCase(LeagueRoutes.nbaBox("0022500001"), "/games/0022500001/box", ["view": "both"])
        ]
    }

    private var writeCases: [RouteCase] {
        [
            RouteCase(LeagueRoutes.recordStatus(.nba), "/availability"),
            RouteCase(LeagueRoutes.recordStatus(.euroleague), "/availability"),
            RouteCase(LeagueRoutes.retractStatus(.nba, id: 12), "/availability/12"),
            RouteCase(LeagueRoutes.retractStatus(.euroleague, id: 7), "/availability/7"),
            RouteCase(LeagueRoutes.pasteLink(.nba), "/news/links"),
            RouteCase(LeagueRoutes.pasteLink(.euroleague), "/news/links"),
            RouteCase(LeagueRoutes.patchModelSettings(.euroleague), "/model-settings")
        ]
    }

    private var readCases: [RouteCase] {
        var cases: [RouteCase] = stateAndMatchupCases
        cases += defenseAndProjectionCases
        cases += availabilityNewsAndSourceCases
        cases += euroLeagueOnlyCases
        cases += nbaFlatCases
        return cases
    }

    private func prefixText(for league: LeagueKey) -> String {
        league == .nba ? "" : "/el"
    }

    private func assertURL(_ entry: RouteCase, file: StaticString = #filePath, line: UInt = #line) {
        let prefix = entry.route.league.fallbackPrefix
        let url = entry.route.url(base: base, prefix: prefix)
        let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        XCTAssertEqual(components?.path,
                       "/v1" + prefixText(for: entry.route.league) + entry.path,
                       "path of \(entry.path)",
                       file: file,
                       line: line)
        var query: [String: String] = [:]
        for item in components?.queryItems ?? [] {
            query[item.name] = item.value ?? ""
        }
        XCTAssertEqual(query, entry.query, "query of \(entry.path)", file: file, line: line)
        XCTAssertEqual(url.host, "127.0.0.1", file: file, line: line)
        XCTAssertEqual(url.port, 8000, file: file, line: line)
    }

    func testEveryReadRouteBuildsItsUrl() {
        for entry in readCases {
            assertURL(entry)
        }
    }

    func testEveryWriteRouteBuildsItsUrl() {
        for entry in writeCases {
            assertURL(entry)
        }
    }

    func testTheTableCoversManyRoutes() {
        XCTAssertGreaterThanOrEqual(readCases.count + writeCases.count, 45)
    }

    // MARK: - What differs between the leagues

    func testAvailabilityIsTeamIdForTheNbaAndClubCodeForTheEuroLeague() {
        let nba = LeagueRoutes.availability(.nba, team: "5")
        let euroLeague = LeagueRoutes.availability(.euroleague, team: "ZZA")
        XCTAssertEqual(nba.query.map { $0.name }, ["teamId"])
        XCTAssertEqual(euroLeague.query.map { $0.name }, ["clubCode"])
    }

    /// The design guessed `clubCode` for EuroLeague news; the backend takes `teamId` in both.
    func testNewsTakesTeamIdInBothLeagues() {
        XCTAssertEqual(LeagueRoutes.news(.nba, team: "5").query.map { $0.name }, ["teamId"])
        XCTAssertEqual(LeagueRoutes.news(.euroleague, team: "ZZA").query.map { $0.name }, ["teamId"])
    }

    func testDefenceSchemeIsEuroLeagueOnly() {
        XCTAssertTrue(LeagueRoutes.teamDefense(.nba, team: "5", scheme: "gfc").query.isEmpty)
        XCTAssertEqual(LeagueRoutes.teamDefense(.euroleague, team: "ZZA", scheme: "gfc").query,
                       [LeagueQueryItem(name: "scheme", value: "gfc")])
    }

    func testNilAndEmptyQueryValuesAreDropped() {
        let route = LeagueRoutes.pairMatchup(.nba, home: "1", away: "2", window: nil, season: "")
        XCTAssertEqual(route.query.map { $0.name }, ["homeTeamId", "awayTeamId"])
        XCTAssertTrue(LeagueRoutes.news(.nba).query.isEmpty)
        XCTAssertTrue(LeagueRoutes.nbaTeam(1, rosterMetrics: []).query.isEmpty)
        XCTAssertTrue(LeagueRoutes.nbaTeam(1, rosterMetrics: ["", ""]).query.isEmpty)
    }

    /// `+` is escaped as `%2B`, exactly as `Endpoints` does, because a server reads `+` as a space.
    func testPlusInAQueryValueIsEscaped() {
        let route = LeagueRoutes.pairMatchup(.nba, home: "1", away: "2", window: nil, season: "2025+26")
        let text = route.url(base: base, prefix: []).absoluteString
        XCTAssertTrue(text.contains("season=2025%2B26"), text)
        XCTAssertFalse(text.contains("season=2025+26"), text)
    }

    func testAPathComponentThatNeedsEscapingIsEscaped() {
        let route = LeagueRoutes.elClub("Z Z")
        let text = route.url(base: base, prefix: ["el"]).absoluteString
        XCTAssertTrue(text.hasSuffix("/v1/el/teams/Z%20Z"), text)
    }

    // MARK: - Prefixes and fixture names

    func testPrefixComponentsDropV1AndEmptyParts() {
        XCTAssertEqual(LeagueClient.prefixComponents("/v1/el"), ["el"])
        XCTAssertEqual(LeagueClient.prefixComponents("/v1"), [])
        XCTAssertEqual(LeagueClient.prefixComponents("v1/el/"), ["el"])
        XCTAssertEqual(LeagueClient.prefixComponents(""), [])
        XCTAssertEqual(LeagueClient.prefixComponents("/"), [])
        XCTAssertEqual(LeagueClient.prefixComponents("/v1/eu/x"), ["eu", "x"])
    }

    func testFallbackPrefixesAndKeys() {
        XCTAssertEqual(LeagueKey.nba.fallbackPrefix, [])
        XCTAssertEqual(LeagueKey.euroleague.fallbackPrefix, ["el"])
        XCTAssertEqual(LeagueKey.nba.rawValue, "nba")
        XCTAssertEqual(LeagueKey.euroleague.rawValue, "euroleague")
        XCTAssertEqual(LeagueKey.nba.regulationMinutes, 48)
        XCTAssertEqual(LeagueKey.euroleague.regulationMinutes, 40)
        XCTAssertEqual(LeagueKey.nba.displayName, "NBA")
        XCTAssertEqual(LeagueKey.euroleague.displayName, "EuroLeague")
    }

    func testFixtureNames() {
        XCTAssertEqual(LeagueRoutes.leagues().fixtureName, "league_nba_leagues")
        XCTAssertEqual(LeagueRoutes.elMeta().fixtureName, "league_el_meta")
        XCTAssertEqual(LeagueRoutes.teamMatchup(.nba, team: "1").fixtureName, "league_nba_team_matchup")
        XCTAssertEqual(LeagueRoutes.gameMatchup(.euroleague, gameId: "g").fixtureName, "league_el_game_matchup")
        XCTAssertEqual(LeagueRoutes.elRound(5).fixtureName, "league_el_round_view")
        XCTAssertEqual(LeagueRoutes.elRound(4, complete: true).fixtureName, "league_el_round_view_complete")
        XCTAssertEqual(LeagueRoutes.reviewQueue(.nba).fixtureName, "league_nba_availability_review_queue")
        XCTAssertEqual(LeagueRoutes.reviewQueue(.euroleague).fixtureName, "league_el_review_queue")
        XCTAssertEqual(LeagueRoutes.nbaTeams().fixtureName, "teams")
        XCTAssertEqual(LeagueRoutes.nbaTeam(1).fixtureName, "team_detail")
        XCTAssertEqual(LeagueRoutes.nbaGames().fixtureName, "games_scoreboard")
        XCTAssertEqual(LeagueRoutes.nbaBox("g").fixtureName, "game_box")
    }

    /// Every read route names a fixture that `sync_contracts.sh` bundles, so demo mode never says
    /// "not bundled" for a route that has one.
    func testEveryReadRouteNamesABundledFixture() throws {
        try XCTSkipIf(TestBundles.url(forFixture: "league_nba_leagues") == nil,
                      "The league fixtures are not in a test-visible bundle; run scripts/sync_contracts.sh.")
        var missing: [String] = []
        for entry in readCases where TestBundles.url(forFixture: entry.route.fixtureName) == nil {
            missing.append(entry.route.fixtureName)
        }
        XCTAssertTrue(missing.isEmpty, "Routes whose demo fixture is not bundled: \(missing)")
    }

    // MARK: - The client

    func testClientResolvesUrlsFromTheBaseAndLearnedPrefixes() async {
        let client = LeagueClient(configuration: APIConfiguration(baseURL: base),
                                  isDemoMode: true,
                                  bundle: TestBundles.tests)
        var url = await client.url(for: LeagueRoutes.elMeta())
        XCTAssertEqual(url.path, "/v1/el/meta")
        url = await client.url(for: LeagueRoutes.teamMatchup(.nba, team: "1"))
        XCTAssertEqual(url.path, "/v1/teams/1/matchup")

        await client.updatePrefixes([LeagueInfo(key: "euroleague", apiPrefix: "/v1/eu"),
                                     LeagueInfo(key: "somethingElse", apiPrefix: "/v1/zz")])
        url = await client.url(for: LeagueRoutes.elMeta())
        XCTAssertEqual(url.path, "/v1/eu/meta")
        url = await client.url(for: LeagueRoutes.leagues())
        XCTAssertEqual(url.path, "/v1/leagues")
    }

    func testClientAppendsV1WhenTheConfiguredBaseStopsAtTheHost() async {
        let host = URL(string: "http://127.0.0.1:8000") ?? base
        let client = LeagueClient(configuration: APIConfiguration(baseURL: host), isDemoMode: true)
        let url = await client.url(for: LeagueRoutes.elClub("ZZA"))
        XCTAssertEqual(url.path, "/v1/el/teams/ZZA")
    }

    func testDemoModeAnswersFromTheBundledFixture() async throws {
        try XCTSkipIf(TestBundles.url(forFixture: "league_nba_team_matchup") == nil,
                      "league_nba_team_matchup.json is not bundled; run scripts/sync_contracts.sh.")
        let client = LeagueClient(configuration: APIConfiguration(baseURL: base),
                                  isDemoMode: true,
                                  bundle: TestBundles.tests)
        let isDemo = await client.isDemoMode
        XCTAssertTrue(isDemo)
        let matchup = try await client.get(LeagueMatchup.self, LeagueRoutes.teamMatchup(.nba, team: "1"))
        XCTAssertEqual(matchup.teams?.count, 2)
        XCTAssertEqual(StubLeagueProtocol.seen.count, 0)
    }

    func testDemoModeNamesAFixtureThatIsNotBundled() async {
        let client = LeagueClient(configuration: APIConfiguration(baseURL: base),
                                  isDemoMode: true,
                                  bundle: TestBundles.tests)
        let route = LeagueRoute(league: .nba, path: ["x"], query: [], fixture: "no_such_fixture")
        do {
            _ = try await client.get(LeagueMatchup.self, route)
            XCTFail("a missing fixture must throw")
        } catch let error as APIError {
            XCTAssertEqual(error, APIError.demoModeMissingFixture("league_nba_no_such_fixture.json"))
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testDemoModeRefusesWrites() async {
        let client = LeagueClient(configuration: APIConfiguration(baseURL: base),
                                  isDemoMode: true,
                                  bundle: TestBundles.tests,
                                  session: stubbedSession())
        do {
            try await client.send(method: "POST", route: LeagueRoutes.recordStatus(.nba), body: nil)
            XCTFail("a demo write must throw")
        } catch let error as APIError {
            XCTAssertEqual(error.rawCode, "demo_read_only")
        } catch {
            XCTFail("unexpected error \(error)")
        }
        XCTAssertEqual(StubLeagueProtocol.seen.count, 0)
    }

    // MARK: - On the wire

    private func stubbedSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubLeagueProtocol.self]
        return URLSession(configuration: configuration)
    }

    private func liveClient(apiKey: String?) -> LeagueClient {
        LeagueClient(configuration: APIConfiguration(baseURL: base, apiKey: apiKey),
                     isDemoMode: false,
                     bundle: TestBundles.tests,
                     session: stubbedSession())
    }

    func testALiveReadSendsTheApiKeyAndNeverAnOrigin() async throws {
        StubLeagueProtocol.handler = { _ in (200, Data(#"{"league": "euroleague", "round": 3}"#.utf8)) }
        let client = liveClient(apiKey: "secret-key")
        let view = try await client.get(ElRoundView.self, LeagueRoutes.elRound(3))
        XCTAssertEqual(view.round, 3)

        let request = try XCTUnwrap(StubLeagueProtocol.seen.first)
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.url?.path, "/v1/el/rounds/3")
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-API-Key"), "secret-key")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Accept"), "application/json")
        XCTAssertTrue(request.value(forHTTPHeaderField: "User-Agent")?.hasPrefix("Hardwood/") ?? false)
        XCTAssertNil(request.value(forHTTPHeaderField: "Origin"))
        XCTAssertNil(request.value(forHTTPHeaderField: "Sec-Fetch-Site"))
        XCTAssertNil(request.value(forHTTPHeaderField: "X-Hardwood-Client"))
    }

    func testALiveWriteIsAPostWithAJsonBodyTheKeyAndNoOrigin() async throws {
        StubLeagueProtocol.handler = { _ in (201, Data("{}".utf8)) }
        let client = liveClient(apiKey: "secret-key")
        let body = ElStatusWrite(clubCode: "ZZA",
                                 status: "out",
                                 sourceLabel: "Club statement",
                                 sourcePublishedAt: "2026-10-02T12:00:00Z")
        try await client.send(method: "POST", route: LeagueRoutes.recordStatus(.euroleague), json: body)

        let request = try XCTUnwrap(StubLeagueProtocol.seen.first)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.path, "/v1/el/availability")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-API-Key"), "secret-key")
        XCTAssertNil(request.value(forHTTPHeaderField: "Origin"))
        XCTAssertEqual(StubLeagueProtocol.seen.count, 1, "a write is sent once")
    }

    func testNoApiKeyHeaderIsSentWhenNoneIsConfigured() async throws {
        StubLeagueProtocol.handler = { _ in (200, Data("{}".utf8)) }
        let client = liveClient(apiKey: nil)
        _ = try await client.get(LeagueMatchup.self, LeagueRoutes.teamMatchup(.nba, team: "1"))
        let hasKey = await client.hasAPIKey
        XCTAssertFalse(hasKey)
        let request = try XCTUnwrap(StubLeagueProtocol.seen.first)
        XCTAssertNil(request.value(forHTTPHeaderField: "X-API-Key"))

        let keyed = liveClient(apiKey: "k")
        let keyedHasKey = await keyed.hasAPIKey
        XCTAssertTrue(keyedHasKey)
        let empty = liveClient(apiKey: "")
        let emptyHasKey = await empty.hasAPIKey
        XCTAssertFalse(emptyHasKey)
    }

    func testAServerErrorEnvelopeBecomesAServerError() async {
        let envelope = #"{"error": {"code": "league_unavailable", "message": "The EuroLeague is off.", "recoverable": false}}"#
        StubLeagueProtocol.handler = { _ in (503, Data(envelope.utf8)) }
        let client = liveClient(apiKey: nil)
        do {
            _ = try await client.get(ElMeta.self, LeagueRoutes.elMeta())
            XCTFail("a 503 must throw")
        } catch let error as APIError {
            XCTAssertEqual(error.rawCode, "league_unavailable")
            XCTAssertEqual(error.httpStatus, 503)
            XCTAssertEqual(error.userMessage, "The EuroLeague is off.")
        } catch {
            XCTFail("unexpected error \(error)")
        }
        XCTAssertEqual(StubLeagueProtocol.seen.count, 1, "a GET that failed is not retried")
    }

    func testABodyThatDoesNotDecodeIsADecodingError() async {
        StubLeagueProtocol.handler = { _ in (200, Data(#"{"round": "three"}"#.utf8)) }
        let client = liveClient(apiKey: nil)
        do {
            _ = try await client.get(ElRoundView.self, LeagueRoutes.elRound(3))
            XCTFail("a wrong type must throw")
        } catch let error as APIError {
            if case .decoding = error {
                XCTAssertTrue(true)
            } else {
                XCTFail("expected a decoding error, got \(error)")
            }
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testChangingTheConfigurationChangesTheKeyAndTheHost() async throws {
        StubLeagueProtocol.handler = { _ in (200, Data("{}".utf8)) }
        let client = liveClient(apiKey: "old")
        let other = URL(string: "http://192.168.1.20:9000/v1") ?? base
        await client.updateConfiguration(APIConfiguration(baseURL: other, apiKey: "new"))
        _ = try await client.get(ElMeta.self, LeagueRoutes.elMeta())
        let request = try XCTUnwrap(StubLeagueProtocol.seen.first)
        XCTAssertEqual(request.url?.host, "192.168.1.20")
        XCTAssertEqual(request.url?.port, 9000)
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-API-Key"), "new")
    }
}

// MARK: - A scripted server

/// Answers every request from `handler` and records what it was sent, so a test can read the
/// headers that went on the wire without a network.
private final class StubLeagueProtocol: URLProtocol {

    static var handler: ((URLRequest) -> (Int, Data))?
    static var seen: [URLRequest] = []

    static func reset() {
        handler = nil
        seen = []
    }

    override class func canInit(with request: URLRequest) -> Bool { true }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        StubLeagueProtocol.seen.append(request)
        let answer: (Int, Data) = StubLeagueProtocol.handler?(request) ?? (500, Data())
        guard let url = request.url,
              let response = HTTPURLResponse(url: url,
                                             statusCode: answer.0,
                                             httpVersion: "HTTP/1.1",
                                             headerFields: ["Content-Type": "application/json"]) else {
            client?.urlProtocol(self, didFailWithError: URLError(.badURL))
            return
        }
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: answer.1)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
