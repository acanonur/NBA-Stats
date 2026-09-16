import Foundation
import XCTest
@testable import Hardwood

/// `projection_board` — the Fantasy Board's payload (`contracts/CONTRACT.md` §4,
/// `docs/BROADSHEET.md`).
///
/// Three things are worth a test here and the rest is plumbing:
///
/// * **No market layer.** The design this was built from drew every band against a sportsbook
///   line. `testTheBoardCarriesNoMarketTranslation` searches the decoded payload's own JSON for
///   the vocabulary, so the day somebody adds an odds field the build says so.
/// * **The dates.** `selectionDate` is the completed slate the players came from; `date` and
///   `throughDate` bracket the games being projected. Conflating them puts last night's date
///   above tomorrow night's numbers, which is the bug the payload is shaped to prevent.
/// * **The range bar's geometry.** `RangeBar.Model` turns four numbers into positions, and a
///   sign error there is invisible in a screenshot and wrong in every row.
///
/// Every test asserts against `ProjectionBoardPayload.preview` before it reaches for the golden
/// fixture, so none of them is a no-op on a machine that has not run the exporter.
final class ProjectionBoardPayloadTests: XCTestCase {

    private let decoder = APIClient.makeDecoder()

    private static let fixtureName = "widget_\(WidgetKind.projectionBoard.rawValue)"

    // MARK: - Shared invariants

    private func assertBoardInvariants(_ payload: ProjectionBoardPayload,
                                       _ source: String,
                                       file: StaticString = #filePath,
                                       line: UInt = #line) {
        XCTAssertFalse(payload.rows.isEmpty, "\(source): a board with no rows shows nothing",
                       file: file, line: line)
        XCTAssertFalse(payload.date.isEmpty, "\(source): the board names no date",
                       file: file, line: line)

        for row in payload.rows {
            let label = "\(source) · \(row.player.name) · \(row.metric)"

            // A projection is never a record (docs/PROJECTION.md §7 rule 5).
            XCTAssertEqual(row.availability, .estimated, "\(label) is not marked estimated",
                           file: file, line: line)
            XCTAssertFalse(row.displayValue.isEmpty, "\(label) has no rendered value",
                           file: file, line: line)

            // §7 rule 1: never a mean without its interval, and the mean inside it.
            if let low = row.low, let high = row.high, let projection = row.projection {
                XCTAssertLessThanOrEqual(low, high, "\(label): low is above high",
                                         file: file, line: line)
                if low < high {
                    XCTAssertGreaterThanOrEqual(projection, low, "\(label): projection below its band",
                                                file: file, line: line)
                    XCTAssertLessThanOrEqual(projection, high, "\(label): projection above its band",
                                             file: file, line: line)
                }
            }

            // The delta is the difference from the mark, not something else with a sign.
            if let delta = row.delta, let projection = row.projection, let reference = row.referenceValue {
                XCTAssertEqual(delta, projection - reference, accuracy: 0.01,
                               "\(label): delta does not match projection minus reference",
                               file: file, line: line)
            }
        }
    }

    private func fixtureOrPreview() throws -> ProjectionBoardPayload {
        guard let data = TestBundles.fixtureData(named: ProjectionBoardPayloadTests.fixtureName) else {
            return .preview
        }
        return try decoder.decode(ProjectionBoardPayload.self, from: data)
    }

    // MARK: - Decoding

    func testFixtureDecodesAndSatisfiesTheInvariants() throws {
        assertBoardInvariants(.preview, "preview")

        guard let data = TestBundles.fixtureData(named: ProjectionBoardPayloadTests.fixtureName) else {
            throw XCTSkip("""
                \(ProjectionBoardPayloadTests.fixtureName).json is not in any test-visible bundle, \
                so only the preview payload was checked. Regenerate with \
                `python3 -m nbastats.fixtures_export --out ../contracts/fixtures` from backend/, \
                then scripts/sync_contracts.sh.
                """)
        }

        let payload = try decoder.decode(ProjectionBoardPayload.self, from: data)
        assertBoardInvariants(payload, "fixture")

        let wrapped = try WidgetPayload.decode(kind: .projectionBoard, from: data, using: decoder)
        XCTAssertEqual(wrapped.kind, .projectionBoard)
        guard case .projectionBoard(let unwrapped) = wrapped else {
            return XCTFail("The fixture decoded into the wrong payload case")
        }
        XCTAssertEqual(unwrapped, payload)
    }

    func testTheBoardSurvivesTheDiskCacheRoundTrip() throws {
        let encoder = APIClient.makeEncoder()
        let data = try encoder.encode(ProjectionBoardPayload.preview)
        XCTAssertEqual(try decoder.decode(ProjectionBoardPayload.self, from: data),
                       ProjectionBoardPayload.preview)
    }

    func testAnEmptyBoardDecodesToItsDefaultsRatherThanThrowing() throws {
        let payload = try decoder.decode(ProjectionBoardPayload.self, from: Data("{}".utf8))
        XCTAssertTrue(payload.rows.isEmpty)
        XCTAssertEqual(payload.gameCount, 0)
        XCTAssertNil(payload.throughDate)
    }

    func testAGameCountWrittenAsADecimalStillDecodes() throws {
        let json = #"{"date": "2026-01-04", "gameCount": 7.0, "rows": []}"#
        let payload = try decoder.decode(ProjectionBoardPayload.self, from: Data(json.utf8))
        XCTAssertEqual(payload.gameCount, 7)
    }

    func testARowThatCallsItselfFullIsStillAnEstimate() throws {
        let json = """
        {"player": {"playerId": 2544, "name": "LeBron James"},
         "metric": "pts", "displayValue": "28.4", "availability": "full"}
        """
        let row = try decoder.decode(ProjectionBoardRow.self, from: Data(json.utf8))
        XCTAssertEqual(row.availability, .estimated,
                       "A projected row decoded as a measured record, which §7 rule 5 forbids")
    }

    // MARK: - The three dates

    func testTheDatesAreThreeSeparateThings() throws {
        let payload = try fixtureOrPreview()
        guard let selectionDate = payload.selectionDate else {
            return XCTFail("The board does not say which slate it chose its players from")
        }
        XCTAssertNotEqual(selectionDate, payload.date, """
            The projected night and the slate that picked the players coincided, which makes \
            this test vacuous rather than passing.
            """)
        for row in payload.rows {
            guard let gameDate = row.gameDate else { continue }
            XCTAssertGreaterThan(gameDate, selectionDate,
                                 "\(row.player.name) is projected into a game on or before the slate")
        }
    }

    func testTheHeadlineNamesOneNightOrTwoButNeverTheSlate() {
        let oneNight = ProjectionBoardPayload(date: "2026-01-04", throughDate: nil, selectionDate: "2026-01-02")
        XCTAssertFalse(oneNight.dateHeadline.contains("–"), oneNight.dateHeadline)

        let twoNights = ProjectionBoardPayload(date: "2026-01-04", throughDate: "2026-01-05", selectionDate: "2026-01-02")
        XCTAssertTrue(twoNights.dateHeadline.contains("–"), twoNights.dateHeadline)

        // The one thing the headline must never be is the day the games were *selected* from.
        XCTAssertFalse(twoNights.dateHeadline.contains(Formatting.mediumGameDate("2026-01-02")))

        // A `throughDate` that repeats `date` is the same single night, not a range.
        let repeated = ProjectionBoardPayload(date: "2026-01-04", throughDate: "2026-01-04")
        XCTAssertEqual(repeated.dateHeadline, oneNight.dateHeadline)
    }

    func testTheGameCountReadsAsASentence() {
        XCTAssertEqual(ProjectionBoardPayload(date: "2026-01-04", gameCount: 1).gameCountText, "1 game")
        XCTAssertEqual(ProjectionBoardPayload(date: "2026-01-04", gameCount: 7).gameCountText, "7 games")
        XCTAssertEqual(ProjectionBoardPayload(date: "2026-01-04", gameCount: 0).gameCountText, "0 games")
    }

    // MARK: - The reference mark

    func testTheReferenceIsTheOnlyMarkAndItIsNamed() throws {
        let payload = try fixtureOrPreview()
        XCTAssertTrue(payload.hasReference, "The board drew a band with nothing to read it against")
        XCTAssertEqual(payload.referenceLabel, "season avg",
                       "The mark has to say what it is, or a reader can take it for a line")
        for row in payload.rows {
            XCTAssertNotNil(row.referenceValue, "\(row.player.name) has a labelled mark and no value")
        }
    }

    func testNoReferenceMeansNoMarkAtAll() {
        let payload = ProjectionBoardPayload(date: "2026-01-04",
                                             reference: "none",
                                             referenceLabel: nil)
        XCTAssertFalse(payload.hasReference)
    }

    func testAboveAndBelowAreDistinguishedAndNeitherIsTheDefault() {
        let player = PlayerRef.preview
        func row(delta: Double?) -> ProjectionBoardRow {
            ProjectionBoardRow(player: player, metric: "pts", delta: delta)
        }
        XCTAssertEqual(row(delta: 1.2).isAboveReference, true)
        XCTAssertEqual(row(delta: -1.2).isAboveReference, false)
        XCTAssertNil(row(delta: nil).isAboveReference, "No comparison must not read as 'below'")
        XCTAssertNil(row(delta: 0).isAboveReference, "A dead heat must not read as 'below' either")
    }

    // MARK: - Ranking

    func testRowsAreOrderedByStandardisedDistanceFromTheMark() throws {
        let payload = try fixtureOrPreview()
        let scores = payload.rows.compactMap { row in row.deltaZ.map { Swift.abs($0) } }
        guard scores.count == payload.rows.count else {
            throw XCTSkip("The board did not publish a deltaZ for every row")
        }
        XCTAssertEqual(scores, scores.sorted(by: >),
                       "The board is not in the order docs/BROADSHEET.md §6 specifies")
    }

    func testDeltaZIsTheDeltaInUnitsOfTheBandsOwnSpread() throws {
        let payload = try fixtureOrPreview()
        for row in payload.rows {
            guard let deltaZ = row.deltaZ, let delta = row.delta,
                  let low = row.low, let high = row.high, high > low else { continue }
            // z(0.9) = 1.2816, so an 80% interval is 2·z standard deviations wide.
            let spread = (high - low) / (2 * 1.2816)
            XCTAssertEqual(deltaZ, delta / spread, accuracy: 0.01,
                           "\(row.player.name) · \(row.metric)")
        }
    }

    // MARK: - No market translation

    func testTheBoardCarriesNoMarketTranslation() throws {
        // NBA.com's terms forbid using their statistics in connection with gambling, and the
        // source monograph declines to claim anything about markets. This is the assertion that
        // keeps both true as the payload grows (docs/BROADSHEET.md §1, docs/PROJECTION.md §6).
        let payload = try fixtureOrPreview()
        let encoded = try JSONEncoder().encode(payload)
        let text = String(decoding: encoded, as: UTF8.self).lowercased()
        for word in ["odds", "vig", "juice", "kelly", "implied", "sportsbook", "payout", "bookmaker"] {
            XCTAssertFalse(text.contains(word), "The board payload mentions \(word)")
        }
        // "edge" would false-positive on a legitimate word, so it is checked as a key.
        XCTAssertFalse(text.contains("\"edge\""), "The board payload carries an edge field")
    }

    // MARK: - The range bar

    func testTheBarPutsEveryMarkWhereTheNumberSaysItIs() {
        let model = RangeBar.Model(low: 20, high: 40, projection: 30, reference: 25)
        XCTAssertTrue(model.isDrawable)

        // Symmetric axis, symmetric band: the projection at the midpoint of 20…40 sits at 0.5.
        XCTAssertEqual(model.position(of: 30), 0.5, accuracy: 0.001)
        XCTAssertLessThan(model.position(of: 25), model.position(of: 30))
        XCTAssertLessThan(model.position(of: model.low), model.position(of: model.high))

        // The band is inset from the ends, so neither bound is drawn against the edge.
        XCTAssertGreaterThan(model.position(of: 20), 0)
        XCTAssertLessThan(model.position(of: 40), 1)
    }

    func testAMarkOutsideTheBandWidensTheAxisRatherThanBeingClamped() {
        let model = RangeBar.Model(low: 20, high: 40, projection: 30, reference: 8)
        XCTAssertLessThan(model.axis.lowerBound, 8, """
            A mark below the band was pinned to the edge, which hides the case a reader most \
            wants to see.
            """)
        XCTAssertGreaterThan(model.position(of: 8), 0)
        XCTAssertLessThan(model.position(of: 8), model.position(of: 20))
    }

    func testADegenerateBandIsNotDrawable() {
        XCTAssertFalse(RangeBar.Model(low: 5, high: 5, projection: 5).isDrawable,
                       "A collapsed interval has no bar; drawing one would invent width")
        XCTAssertFalse(RangeBar.Model(low: 10, high: 4, projection: 6).isDrawable)
        XCTAssertFalse(RangeBar.Model(low: .nan, high: 40, projection: 30).isDrawable)
        XCTAssertFalse(RangeBar.Model(low: 20, high: .infinity, projection: 30).isDrawable)
    }

    func testPositionsStayInsideTheTrackForAValueOffTheAxis() {
        let model = RangeBar.Model(low: 20, high: 40, projection: 30)
        XCTAssertEqual(model.position(of: -1000), 0, accuracy: 0.0001)
        XCTAssertEqual(model.position(of: 1000), 1, accuracy: 0.0001)
        XCTAssertEqual(model.position(of: .nan), 0.5, accuracy: 0.0001,
                       "A non-finite value must land somewhere defined rather than off-screen")
    }

    // MARK: - Identity

    func testARowIsIdentifiedByPlayerAndMetricSoOnePlayerCanAppearTwice() throws {
        let payload = try fixtureOrPreview()
        let ids = payload.rows.map { $0.id }
        XCTAssertEqual(Set(ids).count, ids.count, "Two rows share an id, so ForEach will drop one")
    }
}
