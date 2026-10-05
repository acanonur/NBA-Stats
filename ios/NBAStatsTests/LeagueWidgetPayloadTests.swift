import Foundation
import XCTest
@testable import Hardwood

/// The four league widget kinds (`team_matchup`, `defense_by_position`, `availability_report`,
/// `slate_projections`) from the wire to the dashboard: their payloads decode through the same
/// `WidgetPayload` seam every other kind uses, their settings hide what belongs to the other league,
/// a club is checked the way the catalog and the server check it, and in demo mode a EuroLeague
/// tile is answered from the EuroLeague's own fixture and never from the NBA's.
///
/// Every assertion about a fixture is about its shape (which league, whether there are two sides,
/// whether rows exist), never about an invented number, so regenerating the fixtures cannot turn
/// these red unless a contract changed.
@MainActor
final class LeagueWidgetPayloadTests: XCTestCase {

    private let decoder = APIClient.makeDecoder()

    private static let leagueKinds: [WidgetKind] = [
        .teamMatchup, .defenseByPosition, .availabilityReport, .slateProjections
    ]

    private func data(_ name: String) throws -> Data {
        try XCTUnwrap(TestBundles.fixtureData(named: name), "\(name).json is not in any test bundle")
    }

    // MARK: - Decoding through WidgetPayload

    /// The NBA's tile payloads are the flat `widget_<kind>` fixtures; the EuroLeague's are the
    /// `league_el_widget_<kind>` copies. Both decode into the case that belongs to their kind, and
    /// both survive the disk cache's encode-and-decode round trip.
    func testTheFourFixturesDecodeThroughWidgetPayloadInBothLeagues() throws {
        let encoder = APIClient.makeEncoder()
        for kind in LeagueWidgetPayloadTests.leagueKinds {
            for (name, league) in [("widget_\(kind.rawValue)", "nba"),
                                   ("league_el_widget_\(kind.rawValue)", "euroleague")] {
                guard let bytes = TestBundles.fixtureData(named: name) else {
                    throw XCTSkip("\(name).json is not bundled; run scripts/sync_contracts.sh.")
                }
                let payload = try WidgetPayload.decode(kind: kind, from: bytes, using: decoder)
                XCTAssertEqual(payload.kind, kind, "\(name) decoded into the wrong case")
                XCTAssertEqual(leagueKey(of: payload), league, "\(name) says it is another league")

                let again = try WidgetPayload.decode(kind: kind,
                                                     from: try encoder.encode(payload),
                                                     using: decoder)
                XCTAssertEqual(again, payload, "\(name) changed on the way through the cache")
            }
        }
    }

    private func leagueKey(of payload: WidgetPayload) -> String? {
        switch payload {
        case .teamMatchup(let value): return value.league
        case .defenseByPosition(let value): return value.league
        case .availabilityReport(let value): return value.league
        case .slateProjections(let value): return value.league
        default: return nil
        }
    }

    func testAMatchupTileCarriesTwoSidesAndTheLeaguesAverage() throws {
        for name in ["widget_team_matchup", "league_el_widget_team_matchup"] {
            guard let bytes = TestBundles.fixtureData(named: name) else {
                throw XCTSkip("\(name).json is not bundled; run scripts/sync_contracts.sh.")
            }
            let matchup = try decoder.decode(LeagueMatchup.self, from: bytes)
            XCTAssertEqual(matchup.teams?.count, 2, name)
            XCTAssertEqual(Set((matchup.teams ?? []).compactMap { $0.side }), ["home", "away"], name)
            XCTAssertNotNil(matchup.leagueAverage?.pointsPerGame, name)
            for side in matchup.teams ?? [] {
                XCTAssertNotNil(side.team?.displayAbbr, name)
                // A side either has games with both numbers, or none of them: never a zero.
                if (side.games ?? 0) > 0 {
                    XCTAssertNotNil(side.pointsPerGame, name)
                    XCTAssertNotNil(side.pointsAllowedPerGame, name)
                }
            }
        }
    }

    func testADefenceTileIsOneTeamsBreakdownAndTheTableRouteIsTheWholeLeague() throws {
        for name in ["widget_defense_by_position", "league_el_widget_defense_by_position"] {
            guard let bytes = TestBundles.fixtureData(named: name) else {
                throw XCTSkip("\(name).json is not bundled; run scripts/sync_contracts.sh.")
            }
            let document = try decoder.decode(LeagueDefenseDocument.self, from: bytes)
            XCTAssertFalse(document.isLeagueTable, "\(name) is one team's breakdown")
            XCTAssertNotNil(document.team, name)
            XCTAssertFalse((document.buckets ?? []).isEmpty, name)
            XCTAssertNotNil(document.caveat, "\(name) must carry the sentence about who guarded whom")
        }
        for name in ["league_nba_defense_by_position_table", "league_el_defense_by_position_table"] {
            guard let bytes = TestBundles.fixtureData(named: name) else {
                throw XCTSkip("\(name).json is not bundled; run scripts/sync_contracts.sh.")
            }
            let table = try decoder.decode(LeagueDefenseDocument.self, from: bytes)
            XCTAssertTrue(table.isLeagueTable, "\(name) is the table of every team")
            XCTAssertFalse((table.teams ?? []).isEmpty, name)
        }
    }

    func testAnAvailabilityTileCarriesItsStateAndEveryEntryItsSource() throws {
        for name in ["widget_availability_report", "league_el_widget_availability_report"] {
            guard let bytes = TestBundles.fixtureData(named: name) else {
                throw XCTSkip("\(name).json is not bundled; run scripts/sync_contracts.sh.")
            }
            let report = try decoder.decode(LeagueAvailabilityReport.self, from: bytes)
            XCTAssertNotNil(report.state, name)
            XCTAssertFalse((report.teams ?? []).isEmpty, name)
            XCTAssertNotNil(report.attribution, name)
            for team in report.teams ?? [] {
                for entry in team.entries ?? [] {
                    XCTAssertNotNil(entry.source, "\(name): a status with no source")
                }
            }
        }
    }

    func testASlateTileHasGamesWithBothSidesProjected() throws {
        for name in ["widget_slate_projections", "league_el_widget_slate_projections"] {
            guard let bytes = TestBundles.fixtureData(named: name) else {
                throw XCTSkip("\(name).json is not bundled; run scripts/sync_contracts.sh.")
            }
            let slate = try decoder.decode(LeagueSlateProjections.self, from: bytes)
            XCTAssertFalse((slate.games ?? []).isEmpty, name)
            for game in slate.games ?? [] {
                XCTAssertNotNil(game.home?.projectedPoints, name)
                XCTAssertNotNil(game.away?.projectedPoints, name)
            }
            // The EuroLeague's slate is a round; the NBA's is a day.
            if slate.league == "euroleague" {
                XCTAssertNotNil(slate.round, name)
            } else {
                XCTAssertNotNil(slate.date, name)
            }
        }
    }

    // MARK: - The catalog entries

    func testTheCatalogOffersEachLeagueWidgetItsLeagueAndClubFields() throws {
        let catalog = makeTestCatalog()
        try XCTSkipIf(catalog.widgets.isEmpty, "The bundled contracts are not in a test-visible bundle.")
        for kind in LeagueWidgetPayloadTests.leagueKinds {
            let spec = try XCTUnwrap(catalog.widget(kind), "No catalog entry for \(kind.rawValue)")
            let league = try XCTUnwrap(spec.field("league"), "\(kind.rawValue) has no league field")
            XCTAssertEqual(league.type, .`enum`)
            XCTAssertEqual(league.options ?? [], ["nba", "euroleague"])
            XCTAssertEqual(league.`default`, .string("nba"))
            // Each of the four takes a club for the EuroLeague except the slate, which takes a round.
            if kind != .slateProjections {
                XCTAssertEqual(spec.field("club")?.type, .club, kind.rawValue)
            }
        }
        let slate = try XCTUnwrap(catalog.widget(.slateProjections))
        XCTAssertEqual(slate.field("date")?.`default`, .string("next"))
        XCTAssertEqual(slate.sizes, [.large])
    }

    func testAClubLeftUnsetIsKeptAsNullByNormalization() throws {
        let catalog = makeTestCatalog()
        try XCTSkipIf(catalog.widget(.teamMatchup) == nil, "The bundled contracts are not in a test-visible bundle.")
        let normalized = catalog.normalizedConfig(for: .teamMatchup,
                                                  config: ["league": .string("euroleague"), "club": .null])
        XCTAssertEqual(normalized["league"], .string("euroleague"))
        XCTAssertEqual(normalized["club"], .null)
        XCTAssertEqual(normalized["window"], .int(5), "A catalog default was not filled in")
    }

    // MARK: - The club field

    func testAClubCodeIsThreeLetters() {
        let field = WidgetSpec.ConfigField(key: "club", type: .club, label: "Club")
        XCTAssertNil(ConfigValidator.issue(for: field, value: .string("PAN")))
        XCTAssertNil(ConfigValidator.issue(for: field, value: .string("pan")),
                     "The editor and the server both upper-case what they are given")
        XCTAssertNil(ConfigValidator.issue(for: field, value: .null), "A club is optional until a widget says otherwise")
        XCTAssertNil(ConfigValidator.issue(for: field, value: nil))
        XCTAssertNotNil(ConfigValidator.issue(for: field, value: .string("PA")))
        XCTAssertNotNil(ConfigValidator.issue(for: field, value: .string("PANA")))
        XCTAssertNotNil(ConfigValidator.issue(for: field, value: .string("PA1")))
        XCTAssertNotNil(ConfigValidator.issue(for: field, value: .int(3)))
    }

    func testARequiredClubMustBeChosen() {
        let field = WidgetSpec.ConfigField(key: "club", type: .club, label: "Club", required: true)
        XCTAssertNotNil(ConfigValidator.issue(for: field, value: .null))
        XCTAssertNil(ConfigValidator.issue(for: field, value: .string("ZZA")))
    }

    // MARK: - Which fields the sheet shows

    private func visibleKeys(_ kind: WidgetKind, league: String?, catalog: Catalog) throws -> Set<String> {
        let spec = try XCTUnwrap(catalog.widget(kind), "No catalog entry for \(kind.rawValue)")
        var config: [String: JSONValue] = [:]
        if let league = league {
            config["league"] = .string(league)
        }
        return Set(spec.config.filter { LeagueConfigVisibility.isVisible($0, config: config) }.map { $0.key })
    }

    func testAnNbaTileHidesTheEuroLeaguesFieldsAndTheOtherWayRound() throws {
        let catalog = makeTestCatalog()
        try XCTSkipIf(catalog.widget(.teamMatchup) == nil, "The bundled contracts are not in a test-visible bundle.")

        let nbaMatchup = try visibleKeys(.teamMatchup, league: "nba", catalog: catalog)
        XCTAssertTrue(nbaMatchup.contains("league"))
        XCTAssertTrue(nbaMatchup.contains("team"))
        XCTAssertTrue(nbaMatchup.contains("opponent"))
        XCTAssertFalse(nbaMatchup.contains("club"))
        XCTAssertFalse(nbaMatchup.contains("opponentClub"))

        let euroMatchup = try visibleKeys(.teamMatchup, league: "euroleague", catalog: catalog)
        XCTAssertTrue(euroMatchup.contains("league"))
        XCTAssertTrue(euroMatchup.contains("club"))
        XCTAssertTrue(euroMatchup.contains("opponentClub"))
        XCTAssertFalse(euroMatchup.contains("team"))
        XCTAssertFalse(euroMatchup.contains("opponent"))

        let nbaSlate = try visibleKeys(.slateProjections, league: "nba", catalog: catalog)
        XCTAssertTrue(nbaSlate.contains("date"))
        XCTAssertFalse(nbaSlate.contains("round"))
        let euroSlate = try visibleKeys(.slateProjections, league: "euroleague", catalog: catalog)
        XCTAssertTrue(euroSlate.contains("round"))
        XCTAssertFalse(euroSlate.contains("date"))

        let nbaDefence = try visibleKeys(.defenseByPosition, league: "nba", catalog: catalog)
        XCTAssertFalse(nbaDefence.contains("scheme"), "The NBA lists three positions only")
        let euroDefence = try visibleKeys(.defenseByPosition, league: "euroleague", catalog: catalog)
        XCTAssertTrue(euroDefence.contains("scheme"))
    }

    func testAWidgetWithNoLeagueSettingShowsEveryField() throws {
        let catalog = makeTestCatalog()
        try XCTSkipIf(catalog.widget(.leaderboard) == nil, "The bundled contracts are not in a test-visible bundle.")
        let spec = try XCTUnwrap(catalog.widget(.leaderboard))
        for field in spec.config {
            XCTAssertTrue(LeagueConfigVisibility.isVisible(field, config: [:]), field.key)
        }
        // And a league widget whose draft has lost its league falls back to showing everything.
        let everything = try visibleKeys(.teamMatchup, league: nil, catalog: catalog)
        let all = Set(try XCTUnwrap(catalog.widget(.teamMatchup)).config.map { $0.key })
        XCTAssertEqual(everything, all)
    }

    // MARK: - Demo mode

    private func resolveRequest(_ widgets: [ResolveWidgetRequest]) -> DashboardResolveRequest {
        DashboardResolveRequest(layoutId: "test",
                                context: ResolveContextPayload(),
                                knownSyncVersion: nil,
                                widgets: widgets)
    }

    /// The demo client answers a EuroLeague tile from `league_el_widget_<kind>.json` and an NBA tile
    /// from `widget_<kind>.json`, so a EuroLeague title never sits over NBA numbers.
    func testDemoModeAnswersEachLeaguesTileFromItsOwnFixture() async throws {
        try XCTSkipIf(TestBundles.fixtureData(named: "league_el_widget_team_matchup") == nil
                        || TestBundles.fixtureData(named: "widget_team_matchup") == nil,
                      "The league widget fixtures are not in a test-visible bundle.")
        let client = DemoAPIClient(bundle: TestBundles.app, latencyMilliseconds: 0)
        let widgets: [ResolveWidgetRequest] = [
            ResolveWidgetRequest(id: "nba", kind: .teamMatchup, size: .large,
                                 config: ["league": .string("nba")]),
            ResolveWidgetRequest(id: "el", kind: .teamMatchup, size: .large,
                                 config: ["league": .string("euroleague")])
        ]
        let response = try await client.resolve(resolveRequest(widgets))
        let byID = response.resultsByWidgetID

        let nbaResult = try XCTUnwrap(byID["nba"])
        let euroResult = try XCTUnwrap(byID["el"])
        guard case .teamMatchup(let nbaPayload)? = nbaResult.payload else {
            return XCTFail("The NBA tile did not get a matchup payload")
        }
        guard case .teamMatchup(let euroPayload)? = euroResult.payload else {
            return XCTFail("The EuroLeague tile did not get a matchup payload")
        }
        XCTAssertEqual(nbaPayload.league, "nba")
        XCTAssertEqual(euroPayload.league, "euroleague")
    }

    func testDemoModeLabelsAProjectedSlateAsAnEstimate() async throws {
        try XCTSkipIf(TestBundles.fixtureData(named: "widget_slate_projections") == nil,
                      "The league widget fixtures are not in a test-visible bundle.")
        let client = DemoAPIClient(bundle: TestBundles.app, latencyMilliseconds: 0)
        let widget = ResolveWidgetRequest(id: "slate", kind: .slateProjections, size: .large,
                                          config: ["league": .string("nba")])
        let response = try await client.resolve(resolveRequest([widget]))
        let result = try XCTUnwrap(response.resultsByWidgetID["slate"])
        XCTAssertEqual(result.availability, .estimated)
    }

    func testTheLeagueTilesHaveTheirOwnCacheLifetimes() {
        XCTAssertEqual(WidgetKind.teamMatchup.defaultCacheTTLSeconds, 600)
        XCTAssertEqual(WidgetKind.defenseByPosition.defaultCacheTTLSeconds, 600)
        XCTAssertEqual(WidgetKind.slateProjections.defaultCacheTTLSeconds, 600)
        XCTAssertEqual(WidgetKind.availabilityReport.defaultCacheTTLSeconds, 300)
    }

    // MARK: - The Mac starter dashboard

    #if os(macOS)

    private func makeStore(catalog: Catalog) -> DashboardStore {
        let mine = TestLayouts.layout(id: "mine",
                                      name: "Mine",
                                      isPreset: false,
                                      widgets: [TestLayouts.widget(id: "w1", kind: .statTile, size: .small)])
        return DashboardStore(persistence: InMemoryLayoutPersistence(layouts: [mine]),
                              catalog: catalog,
                              defaults: makeTestDefaults("hardwood.starter.tests"))
    }

    func testTheStarterDashboardIsMadeOnceWithTheSevenLeagueTiles() throws {
        let catalog = makeTestCatalog()
        try XCTSkipIf(catalog.widget(.teamMatchup) == nil, "The bundled contracts are not in a test-visible bundle.")
        let store = makeStore(catalog: catalog)
        let defaults = makeTestDefaults("hardwood.starter.defaults")

        MacStarterLayout.seedIfNeeded(store: store, catalog: catalog, clubCode: nil, defaults: defaults)
        XCTAssertEqual(store.layouts.count, 2)
        let starter = try XCTUnwrap(store.layouts.first { $0.name == MacStarterLayout.layoutName })
        XCTAssertEqual(starter.widgets.count, 7)
        XCTAssertEqual(defaults.string(forKey: MacStarterLayout.DefaultsKey.layoutID), starter.id)
        XCTAssertTrue(defaults.bool(forKey: MacStarterLayout.DefaultsKey.seeded))

        let kinds = starter.widgets.map { $0.kind }
        XCTAssertEqual(kinds.filter { $0 == .teamMatchup }.count, 2)
        XCTAssertEqual(kinds.filter { $0 == .defenseByPosition }.count, 2)
        XCTAssertEqual(kinds.filter { $0 == .availabilityReport }.count, 2)
        XCTAssertEqual(kinds.filter { $0 == .slateProjections }.count, 1)
        let leagues = starter.widgets.compactMap { $0.configString("league") }
        XCTAssertEqual(leagues.filter { $0 == "nba" }.count, 3)
        XCTAssertEqual(leagues.filter { $0 == "euroleague" }.count, 4)

        // A second launch adds nothing, and deleting the dashboard is not undone.
        MacStarterLayout.seedIfNeeded(store: store, catalog: catalog, clubCode: nil, defaults: defaults)
        XCTAssertEqual(store.layouts.count, 2)
        store.delete(starter.id)
        MacStarterLayout.seedIfNeeded(store: store, catalog: catalog, clubCode: nil, defaults: defaults)
        XCTAssertEqual(store.layouts.count, 1)
    }

    func testTheFavouriteClubFillsAnEmptyClubAndNeverOverwritesOne() throws {
        let catalog = makeTestCatalog()
        try XCTSkipIf(catalog.widget(.teamMatchup) == nil, "The bundled contracts are not in a test-visible bundle.")
        let store = makeStore(catalog: catalog)
        let defaults = makeTestDefaults("hardwood.starter.favourite")

        MacStarterLayout.seedIfNeeded(store: store, catalog: catalog, clubCode: nil, defaults: defaults)
        func euroleagueClub() -> String? {
            let starter = store.layouts.first { $0.name == MacStarterLayout.layoutName }
            let tile = starter?.widgets.first { $0.kind == .teamMatchup && $0.configString("league") == "euroleague" }
            return tile?.configString("club")
        }
        XCTAssertNil(euroleagueClub(), "No favourite was set, so the tile has no club yet")

        MacStarterLayout.applyFavorites(store: store, catalog: catalog, clubCode: "ZZA", defaults: defaults)
        XCTAssertEqual(euroleagueClub(), "ZZA")
        MacStarterLayout.applyFavorites(store: store, catalog: catalog, clubCode: "ZZB", defaults: defaults)
        XCTAssertEqual(euroleagueClub(), "ZZA", "A club already on the tile is the reader's choice")

        // The NBA matchup follows the favourite team token and is never given a club.
        let starter = try XCTUnwrap(store.layouts.first { $0.name == MacStarterLayout.layoutName })
        let nbaTile = try XCTUnwrap(starter.widgets.first { $0.kind == .teamMatchup && $0.configString("league") == "nba" })
        XCTAssertNil(nbaTile.configString("club"))
        XCTAssertEqual(nbaTile.configString("team"), "$favorite_team")
    }

    #endif
}
