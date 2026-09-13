import Foundation
import XCTest
@testable import Hardwood

/// Every number Hardwood shows goes through `Formatting` or `HardwoodNumberFormat`, and the two
/// rules that matter are the same in both: a percentage arrives as a fraction and is shown as a
/// percentage, and a value that does not exist is an **em dash and never a zero**.
///
/// Number text follows the reader's locale, so the assertions compare through
/// `XCTAssertFormatted`, which normalizes the separators back to the contract's spelling. The rule
/// is what is under test, not the simulator's region setting.
final class FormattingTests: XCTestCase {

    // MARK: - The eight metric formats

    func testIntegerFormat() {
        XCTAssertFormatted(Formatting.value(32, format: .integer), "32")
        XCTAssertFormatted(Formatting.value(32.4, format: .integer), "32")
        XCTAssertFormatted(Formatting.value(0, format: .integer), "0")
        XCTAssertFormatted(Formatting.value(-7, format: .integer), "-7")
    }

    func testDecimal1Format() {
        XCTAssertFormatted(Formatting.value(24.14, format: .decimal1), "24.1")
        XCTAssertFormatted(Formatting.value(24, format: .decimal1), "24.0")
    }

    func testDecimal2Format() {
        XCTAssertFormatted(Formatting.value(1.368, format: .decimal2), "1.37")
        XCTAssertFormatted(Formatting.value(1, format: .decimal2), "1.00")
    }

    /// The contract's own example: `0.6153` as `percent1` is `"61.5%"`.
    func testPercent1FormatMultipliesTheFraction() {
        XCTAssertFormatted(Formatting.value(0.6153, format: .percent1), "61.5%")
        XCTAssertFormatted(Formatting.value(0.5, format: .percent1), "50.0%")
        XCTAssertFormatted(Formatting.value(1, format: .percent1), "100.0%")
        XCTAssertFormatted(Formatting.value(0, format: .percent1), "0.0%")
    }

    func testPercent2Format() {
        XCTAssertFormatted(Formatting.value(0.6153, format: .percent2), "61.53%")
    }

    func testRating1Format() {
        XCTAssertFormatted(Formatting.value(118.24, format: .rating1), "118.2")
        // A rating carries no grouping separator, so a four-figure rating reads as one number.
        XCTAssertFormatted(Formatting.value(1234.5, format: .rating1), "1234.5")
    }

    /// A plus/minus is always signed — except at exactly zero, where a sign reads as noise.
    func testPlusMinus1FormatIsSigned() {
        XCTAssertFormatted(Formatting.value(7.8, format: .plusMinus1), "+7.8")
        XCTAssertFormatted(Formatting.value(-7.8, format: .plusMinus1), "-7.8")
        XCTAssertFormatted(Formatting.value(0, format: .plusMinus1), "0.0")
    }

    func testMinutesFormat() {
        XCTAssertFormatted(Formatting.value(34.5, format: .minutes), "34.5")
    }

    /// The rule that makes an era-honest dashboard possible: no format ever turns `nil` into `0`.
    func testEveryFormatRendersNilAsAnEmDash() {
        for format in MetricFormat.allCases {
            XCTAssertEqual(Formatting.value(nil, format: format), Formatting.emDash,
                           "\(format.rawValue) does not render nil as an em dash")
            XCTAssertNotEqual(Formatting.value(nil, format: format), "0")
        }
        XCTAssertEqual(MetricFormat.allCases.count, 8, "A format was added without a test")
    }

    func testEveryFormatRendersAnInfiniteValueAsAnEmDash() {
        for format in MetricFormat.allCases {
            XCTAssertEqual(Formatting.value(Double.infinity, format: format), Formatting.emDash)
            XCTAssertEqual(Formatting.value(Double.nan, format: format), Formatting.emDash)
        }
    }

    func testMetricFormatDecimalsAndPercentageFlags() {
        XCTAssertEqual(MetricFormat.integer.decimals, 0)
        XCTAssertEqual(MetricFormat.decimal1.decimals, 1)
        XCTAssertEqual(MetricFormat.decimal2.decimals, 2)
        XCTAssertEqual(MetricFormat.percent1.decimals, 1)
        XCTAssertEqual(MetricFormat.percent2.decimals, 2)
        XCTAssertEqual(MetricFormat.rating1.decimals, 1)
        XCTAssertEqual(MetricFormat.plusMinus1.decimals, 1)
        XCTAssertEqual(MetricFormat.minutes.decimals, 1)
        XCTAssertTrue(MetricFormat.percent1.isPercentage)
        XCTAssertTrue(MetricFormat.percent2.isPercentage)
        // Exactly two of the eight are fractions in [0, 1]; everything else is already in its
        // native unit, so multiplying it by 100 would be wrong.
        XCTAssertEqual(MetricFormat.allCases.filter { $0.isPercentage }.count, 2)
    }

    // MARK: - The design system's own formatter

    /// `HardwoodNumberFormat` is what the views reach for when a value arrives without a
    /// server-formatted string; it has to agree with `Formatting` on both rules.
    func testHardwoodNumberFormatAgreesWithFormatting() {
        XCTAssertFormatted(HardwoodNumberFormat.string(0.6153, format: .percent1), "61.5%")
        XCTAssertFormatted(HardwoodNumberFormat.string(7.8, format: .plusMinus1), "+7.8")
        XCTAssertEqual(HardwoodNumberFormat.missing, Formatting.emDash)
        for format in MetricFormat.allCases {
            XCTAssertEqual(HardwoodNumberFormat.string(nil, format: format), HardwoodNumberFormat.missing)
        }
    }

    func testHardwoodNumberFormatHonoursAnExplicitPlaceholder() {
        XCTAssertEqual(HardwoodNumberFormat.string(nil, format: .decimal1, placeholder: "n/a"), "n/a")
    }

    func testHardwoodNumberFormatOrdinals() {
        // Ordinals are locale-driven, so only the digits are asserted.
        XCTAssertTrue(HardwoodNumberFormat.ordinal(1).contains("1"))
        XCTAssertTrue(HardwoodNumberFormat.ordinal(12).contains("12"))
    }

    // MARK: - Loose helpers

    func testDecimalHelper() {
        XCTAssertFormatted(Formatting.decimal(24.14, places: 1), "24.1")
        XCTAssertFormatted(Formatting.decimal(24.14, places: 0), "24")
        XCTAssertFormatted(Formatting.decimal(24.14, places: 1, signed: true), "+24.1")
        XCTAssertEqual(Formatting.decimal(nil), Formatting.emDash)
    }

    func testIntegerHelper() {
        XCTAssertFormatted(Formatting.integer(41), "41")
        XCTAssertEqual(Formatting.integer(nil), Formatting.emDash)
    }

    func testPercentHelper() {
        XCTAssertFormatted(Formatting.percent(0.6153), "61.5%")
        XCTAssertFormatted(Formatting.percent(0.6153, places: 0), "62%")
        XCTAssertEqual(Formatting.percent(nil), Formatting.emDash)
    }

    func testOrdinalHelper() {
        XCTAssertTrue(Formatting.ordinal(12).contains("12"))
        XCTAssertEqual(Formatting.ordinal(nil), Formatting.emDash)
    }

    func testPercentileHelperScalesAndClamps() {
        XCTAssertTrue(Formatting.percentile(0.93).contains("93"))
        XCTAssertTrue(Formatting.percentile(1.0).contains("100"))
        XCTAssertTrue(Formatting.percentile(0).contains("0"))
        // A percentile off the end of the scale is clamped rather than trapped on.
        XCTAssertTrue(Formatting.percentile(4.0).contains("100"))
        XCTAssertTrue(Formatting.percentile(-1.0).contains("0"))
        XCTAssertEqual(Formatting.percentile(nil), Formatting.emDash)
    }

    // MARK: - Dates

    func testParsesAnRFC3339Timestamp() throws {
        let date = try XCTUnwrap(Formatting.parseTimestamp("2026-01-03T07:12:44Z"))
        XCTAssertEqual(Formatting.timestampString(date), "2026-01-03T07:12:44Z")
    }

    func testParsesAFractionalTimestamp() {
        XCTAssertNotNil(Formatting.parseTimestamp("2026-01-03T07:12:44.512Z"))
    }

    func testRejectsNonsenseTimestamps() {
        XCTAssertNil(Formatting.parseTimestamp(nil))
        XCTAssertNil(Formatting.parseTimestamp(""))
        XCTAssertNil(Formatting.parseTimestamp("not a date"))
    }

    func testParsesACalendarDate() {
        XCTAssertNotNil(Formatting.parseDate("2026-01-02"))
        XCTAssertNil(Formatting.parseDate("latest"))
    }

    /// An unparseable date is echoed rather than blanked: a reader is better served by the raw
    /// string than by an em dash that hides a server bug.
    func testShortGameDateEchoesWhatItCannotParse() {
        XCTAssertEqual(Formatting.shortGameDate("latest"), "latest")
        XCTAssertEqual(Formatting.shortGameDate(nil), Formatting.emDash)
        XCTAssertFalse(Formatting.shortGameDate("2026-01-02").isEmpty)
        XCTAssertNotEqual(Formatting.shortGameDate("2026-01-02"), "2026-01-02")
    }

    func testMediumGameDate() {
        XCTAssertEqual(Formatting.mediumGameDate(nil), Formatting.emDash)
        XCTAssertFalse(Formatting.mediumGameDate("2026-01-02").isEmpty)
    }

    func testRelativeAndUpdatedStrings() {
        let now = Date(timeIntervalSince1970: 1_767_312_000)
        XCTAssertEqual(Formatting.relative(now.addingTimeInterval(-10), now: now), "just now")
        XCTAssertFalse(Formatting.relative(now.addingTimeInterval(-600), now: now).isEmpty)
        XCTAssertEqual(Formatting.updatedString(nil), "Not updated yet")
        XCTAssertTrue(Formatting.updatedString(now.addingTimeInterval(-5), now: now).hasPrefix("Updated "))
    }

    // MARK: - Seasons

    func testSeasonDisplayNamesTheRequestTokens() {
        XCTAssertEqual(Formatting.seasonDisplay("2025-26"), "2025-26")
        XCTAssertEqual(Formatting.seasonDisplay("latest"), "Current Season")
        XCTAssertEqual(Formatting.seasonDisplay("current"), "Current Season")
        XCTAssertEqual(Formatting.seasonDisplay("career"), "Career")
        XCTAssertEqual(Formatting.seasonDisplay("all_time"), "All Time")
        XCTAssertEqual(Formatting.seasonDisplay(nil), Formatting.emDash)
        XCTAssertEqual(Formatting.seasonDisplay(""), Formatting.emDash)
    }

    func testSeasonStartYear() {
        XCTAssertEqual(Formatting.seasonStartYear("1996-97"), 1996)
        XCTAssertEqual(Formatting.seasonStartYear("2025-26"), 2025)
        XCTAssertNil(Formatting.seasonStartYear("latest"))
        XCTAssertNil(Formatting.seasonStartYear("career"))
        XCTAssertNil(Formatting.seasonStartYear(nil))
    }

    func testSeasonContextJoinsWithMiddleDots() {
        XCTAssertEqual(Formatting.seasonContext(season: "2025-26", seasonType: "Regular Season"),
                       "2025-26 · Regular Season")
        XCTAssertEqual(Formatting.seasonContext(season: "2025-26",
                                                seasonType: "Regular Season",
                                                perMode: "PerGame"),
                       "2025-26 · Regular Season · Per Game")
        XCTAssertEqual(Formatting.seasonContext(season: nil, seasonType: nil), "")
    }

    func testPerModeDisplay() {
        XCTAssertEqual(Formatting.perModeDisplay("PerGame"), "Per Game")
        XCTAssertEqual(Formatting.perModeDisplay("Totals"), "Totals")
        XCTAssertEqual(Formatting.perModeDisplay("Per36"), "Per 36")
        XCTAssertEqual(Formatting.perModeDisplay("Per100"), "Per 100")
        XCTAssertEqual(Formatting.perModeDisplay("Whatever"), "Whatever")
    }

    // MARK: - The em dash itself

    func testTheEmDashIsAnEmDash() {
        XCTAssertEqual(Formatting.emDash, "\u{2014}")
        XCTAssertEqual(MetricAvailability.unavailable.hardwoodBadgeText, "\u{2014}")
    }
}
