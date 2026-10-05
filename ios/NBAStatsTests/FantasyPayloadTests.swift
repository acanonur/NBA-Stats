import Foundation
import XCTest
@testable import Hardwood

/// The fantasy toolkit's payloads: the draft board and the trade analyzer.
///
/// Four things are worth a test here and the rest is plumbing:
///
/// * **Direction.** Turnovers are sign-flipped, so a positive z is *few* turnovers. Every piece
///   of wording in the app has to follow that or it praises a flaw.
/// * **The roster adjustment survives decoding as its own number.** Folding it into the net is
///   the single change that would make an honest trade read as a blowout.
/// * **The sensitivity block is never a probability.** It is a sweep, and `flips` is its point.
/// * **Percentage categories carry their volume.** A z of the raw percentage is the classic
///   error, and the payload has to keep `attempts` and `impact` to prove it did not make it.
final class FantasyPayloadTests: XCTestCase {

    private let decoder = APIClient.makeDecoder()

    private static let boardFixture = "widget_\(WidgetKind.fantasyDraftBoard.rawValue)"
    private static let tradeFixture = "widget_\(WidgetKind.fantasyTrade.rawValue)"

    private func board() throws -> FantasyDraftBoardPayload {
        guard let data = TestBundles.fixtureData(named: Self.boardFixture) else {
            return .preview
        }
        return try decoder.decode(FantasyDraftBoardPayload.self, from: data)
    }

    private func trade() throws -> FantasyTradePayload {
        guard let data = TestBundles.fixtureData(named: Self.tradeFixture) else {
            return .preview
        }
        return try decoder.decode(FantasyTradePayload.self, from: data)
    }

    // MARK: - Categories

    func testTheNineCategoriesAreTheWorkbooksNine() {
        XCTAssertEqual(FantasyCategory.order,
                       ["pts", "fg3m", "reb", "ast", "stl", "blk", "tov", "fg_pct", "ft_pct"])
        XCTAssertTrue(FantasyCategory.isNegative("tov"))
        XCTAssertFalse(FantasyCategory.isNegative("pts"))
        XCTAssertTrue(FantasyCategory.isPercentage("fg_pct"))
        XCTAssertTrue(FantasyCategory.isPercentage("ft_pct"))
        XCTAssertFalse(FantasyCategory.isPercentage("fg3m"))
    }

    func testTurnoversAreDescribedInTheDirectionTheyAreScored() {
        // A positive turnover z means FEW turnovers. "Gains turnovers" would be praise for the
        // thing the category penalises.
        XCTAssertEqual(FantasyCategory.changePhrase("tov", z: 0.8), "0.80 better on turnovers")
        XCTAssertEqual(FantasyCategory.changePhrase("tov", z: -0.8), "0.80 worse on turnovers")
        XCTAssertTrue(FantasyCategory.changePhrase("reb", z: 1.2).contains("rebounds"))
        XCTAssertTrue(FantasyCategory.changePhrase("reb", z: 1.2).hasPrefix("+"))
        for key in FantasyCategory.order {
            XCTAssertFalse(FantasyCategory.changePhrase(key, z: 1.0).lowercased()
                .contains("gains turnovers"))
        }
    }

    func testAnUnknownCategoryDegradesToItsKeyRatherThanCrashing() {
        XCTAssertEqual(FantasyCategory.shortLabel("usg_pct"), "USG_PCT")
        XCTAssertEqual(FantasyCategory.label("usg_pct"), "usg_pct")
        XCTAssertFalse(FantasyCategory.isNegative("usg_pct"))
    }

    // MARK: - Draft board (a table)

    func testTheBoardFixtureDecodesAsATable() throws {
        let payload = try board()
        XCTAssertFalse(payload.columns.isEmpty)
        XCTAssertFalse(payload.rows.isEmpty)
        XCTAssertEqual(payload.categories.count, 9)
        XCTAssertGreaterThan(payload.poolSize, 0)

        let keys = Set(payload.columns.map { $0.key })
        for row in payload.rows {
            XCTAssertEqual(row.availability, .estimated, "a valuation is not a record")
            XCTAssertNotNil(row.player)
            // Every numeric column must be answerable from the row. `team` is the one text
            // value and lands on its own property rather than in `values`.
            for key in keys where key != "team" {
                XCTAssertNotNil(row.value(key), "row \(row.rank) has no cell for \(key)")
            }
            XCTAssertNotNil(row.team)
        }
    }

    func testTheValueColumnIsMonotonicWithTheRankBesideIt() throws {
        // A table that says it is sorted and visibly is not. The board ranks on a weighted
        // suggestion, so the value column has to be computed with the same weights.
        let payload = try board()
        let scores = payload.rows.compactMap { $0.value("score") }
        XCTAssertEqual(scores.count, payload.rows.count)
        XCTAssertEqual(scores, scores.sorted(by: >))
        XCTAssertEqual(payload.rows.map { $0.rank }, payload.rows.map { $0.rank }.sorted())
    }

    func testTheZBlockKeepsTheExportOrdering() throws {
        /// Raw runs PTS TPM REB AST; the z block runs zPTS zTPM zAST zREB. Reproduced from the
        /// export this table is modelled on so the two diff column by column.
        let payload = try board()
        let impact = payload.columns(in: "impact").map { $0.label }
        XCTAssertEqual(impact, ["zPTS", "zTPM", "zAST", "zREB", "zSTL", "zBLK", "zTOV", "zFG%", "zFT%"])
        let production = payload.columns(in: "production").map { $0.label }
        XCTAssertEqual(Array(production.prefix(4)), ["PTS", "TPM", "REB", "AST"])
    }

    func testTheGroupsArePagedInServerOrder() throws {
        let payload = try board()
        XCTAssertEqual(payload.groups, ["summary", "production", "impact"])
        for group in payload.groups {
            XCTAssertFalse(payload.columns(in: group).isEmpty, group)
        }
    }

    func testAColumnFormatsItsOwnCell() {
        let percent = FantasyColumn(key: "fg_pct", label: "FG%", format: .percent1)
        XCTAssertTrue(percent.text(0.5355).contains("%"))
        let signed = FantasyColumn(key: "z_pts", label: "zPTS", format: .decimal2, signed: true)
        XCTAssertFormatted(signed.text(2.29), "+2.29")
        XCTAssertFormatted(signed.text(-0.43), "-0.43")
        let count = FantasyColumn(key: "gp", label: "GP", format: .integer)
        XCTAssertFormatted(count.text(68), "68")
    }

    func testAnAbsentCellIsADashAndNeverAZero() {
        // §6's rule, and it matters more here than usual: a missing value and a genuine 0.0
        // are indistinguishable once rendered.
        let column = FantasyColumn(key: "blk", label: "BLK", format: .decimal1)
        XCTAssertEqual(column.text(nil), Formatting.emDash)
        XCTAssertEqual(column.text(Double.nan), Formatting.emDash)
        XCTAssertNotEqual(column.text(0), Formatting.emDash)
    }

    func testAColumnWithAFormatThisBuildCannotReadStillRenders() throws {
        // A new format must cost the column its formatting, never the whole table.
        let json = #"{"key": "xyz", "label": "XYZ", "format": "furlongs", "group": "impact"}"#
        let column = try decoder.decode(FantasyColumn.self, from: Data(json.utf8))
        XCTAssertNil(column.format)
        XCTAssertEqual(column.label, "XYZ")
        XCTAssertFalse(column.text(1.5).isEmpty)
    }

    func testARowSurvivesAValuesDictThatMixesNumbersAndText() throws {
        // `values` carries one string among the numbers. Decoding it as [String: Double] would
        // throw on the team and cost the reader the whole row.
        let json = #"""
        {"rank": 3, "values": {"score": 1.2, "team": "LAL", "pts": 28.3}}
        """#
        let row = try decoder.decode(FantasyDraftRow.self, from: Data(json.utf8))
        XCTAssertEqual(row.rank, 3)
        XCTAssertEqual(row.team, "LAL")
        XCTAssertEqual(row.value("pts") ?? 0, 28.3, accuracy: 1e-9)
        XCTAssertNil(row.value("team"), "the team is text and must not surface as a number")
    }

    func testAPuntGreysTheZColumnAndLeavesProductionAlone() throws {
        let payload = try board()
        for column in payload.columns where column.punted {
            XCTAssertTrue(column.key.hasPrefix("z_"),
                          "\(column.key) is punted but is not a value column")
        }
        // A punt zeroes a category's weight, not a player's rebounds.
        for key in ["pts", "reb", "fg_pct", "fta"] {
            let column = payload.columns.first { $0.key == key }
            XCTAssertEqual(column?.punted, false, key)
        }
    }

    func testTheBoardServesNoContractColumnAndNoForeignValue() throws {
        let payload = try board()
        let keys = Set(payload.columns.map { $0.key })
        XCTAssertFalse(keys.contains("contract"),
                       "contract status is nowhere in this project's data model")
        XCTAssertTrue(keys.contains("score"))
        let value = try XCTUnwrap(payload.columns.first { $0.key == "score" })
        XCTAssertEqual(value.label, "Value")
    }

    func testMinutesAreLabelledForWhatTheyAre() throws {
        let payload = try board()
        let column = try XCTUnwrap(payload.columns.first { $0.key == "mpg" })
        XCTAssertEqual(column.label, "MPG")
        for row in payload.rows {
            let minutes = try XCTUnwrap(row.value("mpg"))
            XCTAssertTrue((0...48).contains(minutes), "\(minutes) is not a per-game figure")
        }
    }

    func testOnlyNumbersAreTrailingAligned() throws {
        let payload = try board()
        for column in payload.columns {
            if column.key == "team" {
                XCTAssertFalse(column.isTrailing, "text reads left")
            } else {
                XCTAssertTrue(column.isTrailing, "\(column.key): decimals have to line up")
            }
        }
    }

    func testAnEmptyBoardDecodesToDefaultsRatherThanThrowing() throws {
        let payload = try decoder.decode(FantasyDraftBoardPayload.self, from: Data("{}".utf8))
        XCTAssertTrue(payload.rows.isEmpty)
        XCTAssertTrue(payload.columns.isEmpty)
        XCTAssertEqual(payload.categories, FantasyCategory.order)
        XCTAssertEqual(payload.nextPick.round, 1)
    }

    func testABoardRowThatClaimsToBeFullIsStillAnEstimate() throws {
        let json = #"{"rank": 1, "availability": "full"}"#
        let row = try decoder.decode(FantasyDraftRow.self, from: Data(json.utf8))
        XCTAssertEqual(row.availability, .estimated)
    }

    // MARK: - Trade

    func testTheTradeFixtureDecodesWithBothSidesFilled() throws {
        let payload = try trade()
        XCTAssertGreaterThan(payload.give.count, 0)
        XCTAssertGreaterThan(payload.get.count, 0)
        XCTAssertFalse(payload.isEmpty)
        XCTAssertEqual(payload.categories.count, 9)
        XCTAssertEqual(Set(payload.categories.map { $0.category }), Set(FantasyCategory.order))
    }

    func testTheNetIsTheChangePlusTheRosterAdjustment() throws {
        /// The relationship a client must not collapse: if it ever shows only `netZ`, a reader
        /// cannot tell a bad trade from the arithmetic of freeing a roster spot.
        let payload = try trade()
        let change = try XCTUnwrap(payload.changeZ)
        let adjustment = try XCTUnwrap(payload.rosterAdjustment)
        let net = try XCTUnwrap(payload.netZ)
        XCTAssertEqual(net, change + adjustment, accuracy: 0.01)
    }

    func testAnEvenTradeMovesNoRosterSpots() throws {
        let payload = try trade()
        if payload.give.count == payload.get.count {
            XCTAssertEqual(payload.rosterAdjustment ?? 0, 0, accuracy: 0.001)
        } else {
            XCTAssertNotEqual(payload.rosterAdjustment ?? 0, 0, accuracy: 0.001)
        }
    }

    func testTheBandsComeWithTheScaleTheyAreReadAgainst() throws {
        /// 0.75 means one thing against a pool spread of 0.9 and another against 2.8.
        let payload = try trade()
        XCTAssertGreaterThan(payload.bands.fair, 0)
        XCTAssertGreaterThan(payload.bands.clear, payload.bands.fair)
        let spread = try XCTUnwrap(payload.poolSpread)
        XCTAssertGreaterThan(spread, 0, "a band without its scale is not a judgement")
    }

    func testTheSensitivityBlockIsARangeAndNeverAProbability() throws {
        let payload = try trade()
        let sweep = try XCTUnwrap(payload.sensitivity)
        XCTAssertFalse(sweep.scenarios.isEmpty)
        XCTAssertLessThanOrEqual(sweep.low ?? 0, sweep.base ?? 0)
        XCTAssertGreaterThanOrEqual(sweep.high ?? 0, sweep.base ?? 0)
        XCTAssertEqual(sweep.flips, (sweep.low ?? 0) < 0 && 0 < (sweep.high ?? 0))

        // Nothing in the block may read as a probability.
        let text = ([sweep.lowScenario, sweep.highScenario].compactMap { $0 }
                    + sweep.scenarios.map { $0.label }).joined(separator: " ").lowercased()
        for word in ["%", "probability", "confidence", "interval", "chance", "likelihood"] {
            XCTAssertFalse(text.contains(word), "the sweep's wording says \(word)")
        }
    }

    func testEveryScenarioNamesTheAssumptionItChanges() throws {
        let payload = try trade()
        let sweep = try XCTUnwrap(payload.sensitivity)
        for scenario in sweep.scenarios {
            XCTAssertFalse(scenario.label.isEmpty, scenario.key)
            XCTAssertFalse(scenario.key.isEmpty)
        }
        XCTAssertTrue(sweep.scenarios.contains { $0.key == "base" })
    }

    func testACategoryLinePhrasesItselfInTheRightDirection() {
        let turnovers = FantasyTradeCategory(category: "tov", net: 0.9, verdict: "gain")
        XCTAssertTrue(turnovers.phrase.contains("better on turnovers"), turnovers.phrase)
        let rebounds = FantasyTradeCategory(category: "reb", net: -0.9, verdict: "loss")
        XCTAssertTrue(rebounds.phrase.contains("rebounds"), rebounds.phrase)
        XCTAssertTrue(rebounds.phrase.contains("−"), rebounds.phrase)
    }

    func testAnEmptyTradeIsRecognisedRatherThanRenderedAsZero() throws {
        let payload = try decoder.decode(FantasyTradePayload.self, from: Data("{}".utf8))
        XCTAssertTrue(payload.isEmpty)
        XCTAssertNil(payload.sensitivity)
        XCTAssertEqual(payload.bands.fair, 0.75)
    }

    func testRosterDominatesFlagsAnUnevenTradeHonestly() {
        let dominated = FantasyTradePayload(changeZ: 1.0, rosterAdjustment: -3.2, netZ: -2.2)
        XCTAssertTrue(dominated.rosterDominates)
        let normal = FantasyTradePayload(changeZ: 4.0, rosterAdjustment: -0.2, netZ: 3.8)
        XCTAssertFalse(normal.rosterDominates)
    }

    // MARK: - Envelope and policy

    func testBothDecodeThroughTheResolveEnvelopeIntoTheRightCase() throws {
        for (kind, name) in [(WidgetKind.fantasyDraftBoard, Self.boardFixture),
                             (WidgetKind.fantasyTrade, Self.tradeFixture)] {
            guard let data = TestBundles.fixtureData(named: name) else { continue }
            let wrapped = try WidgetPayload.decode(kind: kind, from: data, using: decoder)
            XCTAssertEqual(wrapped.kind, kind)
        }
    }

    func testBothSurviveTheDiskCacheRoundTrip() throws {
        let encoder = APIClient.makeEncoder()
        let boardData = try encoder.encode(FantasyDraftBoardPayload.preview)
        XCTAssertEqual(try decoder.decode(FantasyDraftBoardPayload.self, from: boardData),
                       FantasyDraftBoardPayload.preview)
        let tradeData = try encoder.encode(FantasyTradePayload.preview)
        XCTAssertEqual(try decoder.decode(FantasyTradePayload.self, from: tradeData),
                       FantasyTradePayload.preview)
    }

    func testNeitherPayloadCarriesAMarket() throws {
        // docs/LEGAL.md §2a: this analyses a manager's roster, it does not run a contest.
        let encoder = JSONEncoder()
        let documents = [
            try encoder.encode(try board()),
            try encoder.encode(try trade()),
        ]
        for data in documents {
            let text = String(decoding: data, as: UTF8.self).lowercased()
            for word in ["odds", "wager", "kelly", "sportsbook", "payout", "stake", "vig"] {
                XCTAssertFalse(text.contains(word), "a fantasy payload mentions \(word)")
            }
        }
    }
}
