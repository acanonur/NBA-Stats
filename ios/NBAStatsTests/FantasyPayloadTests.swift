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

    // MARK: - Draft board

    func testTheBoardFixtureDecodesAndRanks() throws {
        let payload = try board()
        XCTAssertFalse(payload.picks.isEmpty)
        XCTAssertEqual(payload.categories.count, 9)
        XCTAssertGreaterThan(payload.poolSize, 0)

        // Ordered by the suggestion, which is what the board sorted on.
        let suggestions = payload.picks.compactMap { $0.suggestion }
        XCTAssertEqual(suggestions, suggestions.sorted(by: >))

        for pick in payload.picks {
            XCTAssertEqual(pick.availability, .estimated, "a valuation is not a record")
            XCTAssertEqual(Set(pick.categories.map { $0.category }), Set(FantasyCategory.order))
            XCTAssertNotNil(pick.player)
        }
    }

    func testTheSnakeSlotsAdvanceDownTheBoard() throws {
        let payload = try board()
        let overalls = payload.picks.map { $0.overall }
        XCTAssertEqual(overalls, Array(overalls.first!...(overalls.first! + overalls.count - 1)))
        XCTAssertEqual(payload.picks.first?.overall, payload.nextPick.overall)
    }

    func testAPercentageCategoryKeepsTheVolumeItWasWeightedBy() throws {
        let payload = try board()
        for pick in payload.picks {
            for key in ["fg_pct", "ft_pct"] {
                let value = try XCTUnwrap(pick.category(key))
                XCTAssertNotNil(value.attempts, "\(key) lost the volume it is weighted by")
                XCTAssertNotNil(value.impact, "\(key) lost the quantity actually standardised")
                let weight = try XCTUnwrap(value.shrinkageWeight)
                XCTAssertTrue((0...1).contains(weight), "\(key) shrinkage weight \(weight)")
            }
            // A counting category must NOT carry them, or a client could mistake one for the
            // other and render a rebound total as a shooting percentage.
            XCTAssertNil(pick.category("pts")?.attempts)
            XCTAssertNil(pick.category("pts")?.impact)
        }
    }

    func testAPercentageRendersAsAPercentageAndACountDoesNot() {
        let shooting = FantasyCategoryValue(category: "fg_pct", value: 0.571, z: 1.1,
                                            attempts: 17.5, impact: 1.74, shrinkageWeight: 0.91)
        let counting = FantasyCategoryValue(category: "pts", value: 26.8, z: 1.94)
        XCTAssertTrue(shooting.displayValue.contains("%"), shooting.displayValue)
        XCTAssertFalse(counting.displayValue.contains("%"), counting.displayValue)
        XCTAssertFormatted(counting.zText, "+1.94")
    }

    func testTotalZAndScoreAreNotTheSameNumber() throws {
        // They differ by the weight total. A threshold written for one is wrong for the other
        // by that factor, which is why both are on the payload rather than one being derived.
        let payload = try board()
        let pick = try XCTUnwrap(payload.picks.first)
        let totalZ = try XCTUnwrap(pick.totalZ)
        let score = try XCTUnwrap(pick.score)
        XCTAssertNotEqual(totalZ, score, accuracy: 1e-9)
        XCTAssertEqual(score, totalZ / 9.0, accuracy: 0.01)
    }

    func testAnEmptyBoardDecodesToDefaultsRatherThanThrowing() throws {
        let payload = try decoder.decode(FantasyDraftBoardPayload.self, from: Data("{}".utf8))
        XCTAssertTrue(payload.picks.isEmpty)
        XCTAssertEqual(payload.categories, FantasyCategory.order)
        XCTAssertEqual(payload.nextPick.round, 1)
    }

    func testABoardRowThatClaimsToBeFullIsStillAnEstimate() throws {
        let json = #"{"overall": 1, "availability": "full"}"#
        let pick = try decoder.decode(FantasyDraftPick.self, from: Data(json.utf8))
        XCTAssertEqual(pick.availability, .estimated)
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
