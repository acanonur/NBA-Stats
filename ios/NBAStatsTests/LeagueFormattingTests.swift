import Foundation
import XCTest
@testable import Hardwood

/// The two rules every league screen leans on: a value that does not exist is an em dash and never
/// a zero, and a percentage arrives as a fraction. Number text follows the reader's locale, so the
/// assertions compare through `XCTAssertFormatted`, which normalizes the separators.
final class LeagueFormattingTests: XCTestCase {

    private let dash = Formatting.emDash

    // MARK: - Missing values

    func testEveryNumberFormatterTurnsNilIntoAnEmDashAndNeverZero() {
        XCTAssertEqual(LeagueFormatting.number(nil), dash)
        XCTAssertEqual(LeagueFormatting.signed(nil), dash)
        XCTAssertEqual(LeagueFormatting.percent(nil), dash)
        XCTAssertEqual(LeagueFormatting.integer(nil), dash)
        XCTAssertEqual(LeagueFormatting.minutes(nil), dash)
        XCTAssertEqual(LeagueFormatting.score(nil, nil), dash)
        XCTAssertEqual(LeagueFormatting.record(nil), dash)
        XCTAssertEqual(LeagueFormatting.madeAttempted(nil, nil), dash)
    }

    func testEveryTimeFormatterTurnsNilIntoAnEmDash() {
        XCTAssertEqual(LeagueFormatting.tipoff(nil), dash)
        XCTAssertEqual(LeagueFormatting.tipoff(""), dash)
        XCTAssertEqual(LeagueFormatting.berlinTime(nil), dash)
        XCTAssertEqual(LeagueFormatting.age(nil), dash)
        XCTAssertEqual(LeagueFormatting.ageFromMinutes(nil), dash)
        XCTAssertEqual(LeagueFormatting.absoluteLocal(nil), dash)
        XCTAssertEqual(LeagueFormatting.leagueDate(nil), dash)
        XCTAssertEqual(LeagueFormatting.gameStatusText(nil), dash)
    }

    /// A zero that is really there stays a zero: only a missing value is a dash.
    func testARealZeroIsNotADash() {
        XCTAssertFormatted(LeagueFormatting.number(0), "0.0")
        XCTAssertFormatted(LeagueFormatting.integer(0), "0")
        XCTAssertFormatted(LeagueFormatting.percent(0), "0.0%")
        XCTAssertFormatted(LeagueFormatting.score(0, 0), "0–0")
    }

    // MARK: - Numbers

    func testPercentMultipliesTheFraction() {
        XCTAssertFormatted(LeagueFormatting.percent(0.4286), "42.9%")
        XCTAssertFormatted(LeagueFormatting.percent(1), "100.0%")
        XCTAssertFormatted(LeagueFormatting.percent(0.4286, places: 0), "43%")
    }

    func testNumberAndSignedPlaces() {
        XCTAssertFormatted(LeagueFormatting.number(83.456), "83.5")
        XCTAssertFormatted(LeagueFormatting.number(83.456, places: 2), "83.46")
        XCTAssertFormatted(LeagueFormatting.signed(2.44), "+2.4")
        XCTAssertFormatted(LeagueFormatting.signed(-2.44), "-2.4")
        XCTAssertFormatted(LeagueFormatting.minutes(27.46), "27.5")
    }

    func testScoreIsShownOnlyWhenBothSidesArePresent() {
        XCTAssertFormatted(LeagueFormatting.score(94, 84), "94–84")
        XCTAssertEqual(LeagueFormatting.score(94, nil), dash)
        XCTAssertEqual(LeagueFormatting.score(nil, 84), dash)
    }

    func testRecordNeedsWinsAndLosses() {
        XCTAssertFormatted(LeagueFormatting.record(TeamRecord(wins: 9, losses: 1)), "9–1")
        XCTAssertEqual(LeagueFormatting.record(TeamRecord(wins: 9, losses: nil)), dash)
        XCTAssertEqual(LeagueFormatting.record(TeamRecord(wins: nil, losses: 1)), dash)
    }

    func testMadeAttemptedNeedsBoth() {
        XCTAssertFormatted(LeagueFormatting.madeAttempted(5, 9), "5–9")
        XCTAssertFormatted(LeagueFormatting.madeAttempted(4.6, 9.2, places: 1), "4.6–9.2")
        XCTAssertEqual(LeagueFormatting.madeAttempted(5, nil), dash)
        XCTAssertEqual(LeagueFormatting.madeAttempted(nil, 9), dash)
    }

    func testSortKeysPutAMissingValueBelowEveryRealOne() {
        XCTAssertEqual(LeagueFormatting.sortKey(nil as Double?), -Double.infinity)
        XCTAssertEqual(LeagueFormatting.sortKey(nil as Int?), -Double.infinity)
        XCTAssertEqual(LeagueFormatting.sortKey(2.5), 2.5)
        XCTAssertEqual(LeagueFormatting.sortKey(Int(7)), 7)
        XCTAssertLessThan(LeagueFormatting.sortKey(nil as Double?), LeagueFormatting.sortKey(-1000.0))
        XCTAssertLessThan(LeagueFormatting.sortKey(Int(0)), LeagueFormatting.sortKey(0.5))
    }

    // MARK: - Times

    /// A tip-off is shown in the zone it is asked for. 17:30 UTC on 2 October 2026 is 19:30 in
    /// Berlin (CEST) and 02:30 on the 3rd in Tokyo; the digits are what is asserted, because the
    /// order of weekday, day and month follows the reader's locale.
    func testTipoffIsShownInTheRequestedZone() throws {
        let utc = try XCTUnwrap(TimeZone(identifier: "UTC"))
        let berlin = try XCTUnwrap(TimeZone(identifier: "Europe/Berlin"))
        let tokyo = try XCTUnwrap(TimeZone(identifier: "Asia/Tokyo"))
        let stamp = "2026-10-02T17:30:00Z"
        XCTAssertTrue(LeagueFormatting.tipoff(stamp, timeZone: utc).contains("17:30"))
        XCTAssertTrue(LeagueFormatting.tipoff(stamp, timeZone: berlin).contains("19:30"))
        XCTAssertTrue(LeagueFormatting.tipoff(stamp, timeZone: tokyo).contains("02:30"))
    }

    func testBerlinTimeIsBerlinTime() {
        XCTAssertTrue(LeagueFormatting.berlinTime("2026-10-02T17:30:00Z").contains("19:30"))
        // Winter: CET, one hour from UTC.
        XCTAssertTrue(LeagueFormatting.berlinTime("2026-12-02T17:30:00Z").contains("18:30"))
    }

    func testAnUnparseableTimestampIsEchoedNotHidden() {
        XCTAssertEqual(LeagueFormatting.tipoff("soon"), "soon")
        XCTAssertEqual(LeagueFormatting.berlinTime("soon"), "soon")
        XCTAssertEqual(LeagueFormatting.absoluteLocal("soon"), "soon")
        XCTAssertEqual(LeagueFormatting.age("soon"), dash)
    }

    func testAgeIsRelativeToTheClockItIsGiven() throws {
        let published = "2026-10-02T09:00:00Z"
        let start = try XCTUnwrap(Formatting.parseTimestamp(published))
        XCTAssertEqual(LeagueFormatting.age(published, now: start.addingTimeInterval(30)), "just now")
        let later = LeagueFormatting.age(published, now: start.addingTimeInterval(3 * 3600))
        XCTAssertTrue(later.contains("3"), later)
        XCTAssertNotEqual(later, dash)
        XCTAssertEqual(LeagueFormatting.ageFromMinutes(0.5), "just now")
        XCTAssertTrue(LeagueFormatting.ageFromMinutes(180).contains("3"))
    }

    /// The day string comes from the calendar it is given, so a reader in Auckland who looks at
    /// 23:00 on the 2nd asks for the 2nd, whatever zone the machine running the test is in.
    func testIsoDayUsesTheGivenCalendar() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try XCTUnwrap(TimeZone(identifier: "Pacific/Auckland"))
        let date = try XCTUnwrap(calendar.date(from: DateComponents(year: 2026, month: 10, day: 2, hour: 23)))
        XCTAssertEqual(LeagueFormatting.isoDay(date, calendar: calendar), "2026-10-02")
        let early = try XCTUnwrap(calendar.date(from: DateComponents(year: 2026, month: 1, day: 5, hour: 0, minute: 30)))
        XCTAssertEqual(LeagueFormatting.isoDay(early, calendar: calendar), "2026-01-05")
    }

    func testLeagueDateRoundTripsAPlainDay() {
        let text = LeagueFormatting.leagueDate("2026-10-02")
        XCTAssertNotEqual(text, dash)
        XCTAssertTrue(text.contains("2"), text)
        XCTAssertEqual(LeagueFormatting.leagueDate("not a day"), "not a day")
    }

    // MARK: - Games

    func testGameStatusText() {
        let teams = LeagueTeamRef(abbr: "ZZA")
        var game = LeagueGameRef(date: "2026-10-08", status: "final", home: teams, homePts: 94, awayPts: 84)
        XCTAssertFormatted(LeagueFormatting.gameStatusText(game), "94–84")

        game.overtimePeriods = 1
        XCTAssertTrue(LeagueFormatting.gameStatusText(game).hasSuffix("(OT)"))
        game.overtimePeriods = 2
        XCTAssertTrue(LeagueFormatting.gameStatusText(game).hasSuffix("(2OT)"))

        game.homePts = nil
        game.overtimePeriods = nil
        XCTAssertEqual(LeagueFormatting.gameStatusText(game), "Final")

        game.status = "resultPending"
        XCTAssertEqual(LeagueFormatting.gameStatusText(game), "Result pending")
        game.status = "postponed"
        XCTAssertEqual(LeagueFormatting.gameStatusText(game), "Postponed")
        game.status = "somethingNew"
        XCTAssertEqual(LeagueFormatting.gameStatusText(game), "somethingNew")
    }

    /// A scheduled game shows its tip-off, or its day when no tip-off is known.
    func testAScheduledGameShowsItsTipoffOrItsDay() {
        var game = LeagueGameRef(date: "2026-10-08", tipoffUtc: "2026-10-08T18:30:00Z", status: "scheduled")
        XCTAssertEqual(LeagueFormatting.gameStatusText(game), LeagueFormatting.tipoff("2026-10-08T18:30:00Z"))
        game.tipoffUtc = nil
        XCTAssertEqual(LeagueFormatting.gameStatusText(game), LeagueFormatting.leagueDate("2026-10-08"))
    }

    func testFormText() {
        let won = LeagueFormGame(teamScore: 94, opponentScore: 84, result: "W")
        XCTAssertFormatted(LeagueFormatting.formText(won), "W 94–84")
        XCTAssertEqual(LeagueFormatting.formText(LeagueFormGame(result: "L")), "L")
        XCTAssertEqual(LeagueFormatting.formText(nil), dash)
        XCTAssertEqual(LeagueFormatting.venueMark(isHome: true, isNeutral: false), "vs")
        XCTAssertEqual(LeagueFormatting.venueMark(isHome: false, isNeutral: nil), "at")
        XCTAssertEqual(LeagueFormatting.venueMark(isHome: true, isNeutral: true), "v")
        XCTAssertEqual(LeagueFormatting.venueMark(isHome: nil, isNeutral: nil), "")
        XCTAssertEqual(LeagueFormatting.windowText(5), "Last 5")
    }

    // MARK: - Words

    /// A missing status is "No report", never "Available".
    func testStatusWords() {
        XCTAssertEqual(LeagueFormatting.statusWord(nil), "No report")
        XCTAssertEqual(LeagueFormatting.statusWord(""), "No report")
        XCTAssertNotEqual(LeagueFormatting.statusWord(nil), LeagueFormatting.statusWord("available"))
        XCTAssertEqual(LeagueFormatting.statusWord("out"), "Out")
        XCTAssertEqual(LeagueFormatting.statusWord("doubtful"), "Doubtful")
        XCTAssertEqual(LeagueFormatting.statusWord("questionable"), "Questionable")
        XCTAssertEqual(LeagueFormatting.statusWord("probable"), "Probable")
        XCTAssertEqual(LeagueFormatting.statusWord("available"), "Available")
        XCTAssertEqual(LeagueFormatting.statusSymbol(nil), "minus.circle")
        XCTAssertNotEqual(LeagueFormatting.statusSymbol("out"), LeagueFormatting.statusSymbol("available"))
    }

    /// A value a newer server invents is shown as it arrived, never hidden or mislabelled.
    func testUnknownValuesAreShownRaw() {
        XCTAssertEqual(LeagueFormatting.statusWord("suspended"), "suspended")
        XCTAssertEqual(LeagueFormatting.reasonWord("somethingNew"), "somethingNew")
        XCTAssertEqual(LeagueFormatting.basisWord("somethingNew"), "somethingNew")
        XCTAssertEqual(LeagueFormatting.bandWord("somethingNew"), "somethingNew")
        XCTAssertEqual(LeagueFormatting.sourceKindWord("somethingNew"), "somethingNew")
        XCTAssertEqual(LeagueFormatting.modelKindWord("somethingNew"), "somethingNew")
        XCTAssertEqual(LeagueFormatting.roundStatusWord("somethingNew"), "somethingNew")
        XCTAssertEqual(LeagueFormatting.assumedBasisWord("somethingNew"), "somethingNew")
        XCTAssertEqual(LeagueFormatting.stateWord("somethingNew"), "somethingNew")
        XCTAssertEqual(LeagueFormatting.reportStateWord("somethingNew"), "somethingNew")
        XCTAssertEqual(LeagueFormatting.outOfForceWord("somethingNew"), "somethingNew")
        XCTAssertEqual(LeagueFormatting.intervalBasisWord("somethingNew"), "somethingNew")
        XCTAssertEqual(LeagueFormatting.positionWord("somethingNew"), "somethingNew")
    }

    func testNilWordsAreEmDashesExceptStatus() {
        XCTAssertEqual(LeagueFormatting.reasonWord(nil), dash)
        XCTAssertEqual(LeagueFormatting.basisWord(nil), dash)
        XCTAssertEqual(LeagueFormatting.bandWord(nil), dash)
        XCTAssertEqual(LeagueFormatting.sourceKindWord(nil), dash)
        XCTAssertEqual(LeagueFormatting.modelKindWord(nil), dash)
        XCTAssertEqual(LeagueFormatting.roundStatusWord(nil), dash)
        XCTAssertEqual(LeagueFormatting.assumedBasisWord(nil), dash)
        XCTAssertEqual(LeagueFormatting.stateWord(nil), dash)
        XCTAssertEqual(LeagueFormatting.gameStatusWord(nil), dash)
        XCTAssertEqual(LeagueFormatting.phaseWord(nil), dash)
    }

    func testTheWordsTheDesignNames() {
        XCTAssertEqual(LeagueFormatting.sourceKindWord("leagueReport"), "League report")
        XCTAssertEqual(LeagueFormatting.sourceKindWord("clubStatement"), "Club statement")
        XCTAssertEqual(LeagueFormatting.sourceKindWord("pressArticle"), "Press")
        XCTAssertEqual(LeagueFormatting.sourceKindWord("boxScoreInference"), "Box score")
        XCTAssertEqual(LeagueFormatting.sourceKindWord("workbookImport"), "Your workbook")
        XCTAssertEqual(LeagueFormatting.sourceKindWord("manual"), "Entered by you")
        XCTAssertEqual(LeagueFormatting.reportStateWord("notYetSubmitted"), "Team has not submitted its report yet")
        XCTAssertEqual(LeagueFormatting.roundStatusWord("resultPending"), "Result pending")
        XCTAssertEqual(LeagueFormatting.gameStatusWord("resultPending"), "Result pending")
        XCTAssertEqual(LeagueFormatting.modelKindWord("locked"), "Locked")
        XCTAssertEqual(LeagueFormatting.bandWord("better"), "Better")
        XCTAssertEqual(LeagueFormatting.phaseWord("PI"), "Play-in")
        XCTAssertEqual(LeagueFormatting.defenseBasisWord("perMinute"), "Per minute")
        XCTAssertEqual(LeagueFormatting.reasonWord("coachDecision"), "Coach's decision")
        XCTAssertEqual(LeagueFormatting.reasonWord("gLeague"), "G League")
    }

    /// The vocabulary a product with no betting features must not use, in any word the formatter
    /// produces for the values the backend defines.
    func testNoWordIsBettingVocabulary() {
        let banned: Set<String> = [
            "line", "lines", "odds", "over", "under", "lean", "edge", "pick", "picks", "push",
            "implied", "cover", "vig", "juice", "stake", "wager", "bookmaker", "market", "parlay",
            "handicap", "ats", "probability", "spread", "moneyline", "total"
        ]
        var produced: [String] = []
        for status in ["out", "doubtful", "questionable", "probable", "available"] {
            produced.append(LeagueFormatting.statusWord(status))
        }
        for reason in ["injury", "illness", "rest", "coachDecision", "personal", "suspension",
                       "gLeague", "notWithTeam", "notRegistered", "other"] {
            produced.append(LeagueFormatting.reasonWord(reason))
        }
        for basis in ["workbookOfficial", "workbookEstimate", "positionPrior", "officialUpdate", "syntheticDemo"] {
            produced.append(LeagueFormatting.basisWord(basis))
        }
        for state in ["fresh", "ok", "stale", "noReportYet", "unreadable", "disabled", "notConfigured", "blocked", "error"] {
            produced.append(LeagueFormatting.stateWord(state))
        }
        for reason in ["returnDatePassed", "returnRoundPassed", "playedSince", "tooOld"] {
            produced.append(LeagueFormatting.outOfForceWord(reason))
        }
        for basis in ["assumed", "fittedPrevSeason", "fittedLedger"] {
            produced.append(LeagueFormatting.intervalBasisWord(basis))
        }
        for basis in ["notOnSubmittedReport", "teamReportPending", "noReportPublished", "noEntry"] {
            produced.append(LeagueFormatting.assumedBasisWord(basis))
        }
        for position in ["G", "F", "C", "PG", "SG", "SF", "PF", "unknown"] {
            produced.append(LeagueFormatting.positionWord(position))
        }
        for reason in ["minimumGames", "positionCoverage", "leagueSample"] {
            produced.append(LeagueFormatting.withheldReasonWord(reason))
        }
        for scheme in ["gfc", "workbook5"] {
            produced.append(LeagueFormatting.schemeWord(scheme))
        }
        produced.append(LeagueFormatting.gameStatusWord("scheduled"))
        produced.append(LeagueFormatting.gameStatusWord("final"))
        produced.append(LeagueFormatting.reportStateWord("submitted"))
        produced.append(LeagueFormatting.reportStateWord("noReport"))

        for text in produced {
            let words = text.lowercased().split(whereSeparator: { !$0.isLetter }).map { String($0) }
            let hits = banned.intersection(Set(words))
            XCTAssertTrue(hits.isEmpty, "\"\(text)\" contains \(hits)")
        }
        XCTAssertGreaterThan(produced.count, 50)
    }
}
