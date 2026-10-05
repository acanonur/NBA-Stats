import Foundation

/// Every number, time and word the league screens show goes through here.
///
/// WHY A SECOND FORMATTING TYPE
/// `Formatting` owns the app's number and date rules and is pinned, deliberately, to US Eastern for
/// the NBA's scheduling day. A EuroLeague tip-off has to be shown in the reader's own time zone
/// (and in Berlin's, because the league's day is Berlin's), and the league payloads carry a
/// vocabulary of status words that the NBA widgets never had. This type adds only that, and builds
/// on `Formatting` for everything numeric so the two rules that matter stay in one place:
///
/// - A missing value renders as an em dash and never as 0 (`Formatting.emDash`).
/// - A percentage arrives as a fraction in [0, 1] and is multiplied by 100 on display.
///
/// The app computes nothing: every function here formats a value the server sent. `sortKey` turns
/// a missing value into negative infinity only so a table can order a column without crashing; it
/// is never shown.
///
/// WORDS
/// The backend sends enum-like values as strings (`out`, `resultPending`, `workbookImport`, ...).
/// Each `...Word` function maps the ones the backend defines to a phrase a reader understands and
/// returns any other string unchanged, so a value a newer server invents is shown as it arrived
/// instead of being hidden or mislabelled. A nil value is an em dash, except where the design says
/// nil has a meaning of its own (`statusWord(nil)` is "No report", never "Available").
///
/// Betting vocabulary appears nowhere in this file's strings: the projection words are "range",
/// "margin" and "combined", and the lint (rule P9) keeps it that way in the screens.
public enum LeagueFormatting {

    // MARK: - Numbers

    /// A decimal with the given places, grouped, or an em dash.
    public static func number(_ value: Double?, places: Int = 1) -> String {
        Formatting.decimal(value, places: places)
    }

    /// A decimal that always shows its sign when positive: `+2.4`, `-1.0`.
    public static func signed(_ value: Double?, places: Int = 1) -> String {
        Formatting.decimal(value, places: places, signed: true)
    }

    /// A fraction in [0, 1] as a percentage: `0.4286` is `42.9%`.
    public static func percent(_ value: Double?, places: Int = 1) -> String {
        Formatting.percent(value, places: places)
    }

    /// A whole number with grouping, or an em dash.
    public static func integer(_ value: Int?) -> String {
        Formatting.integer(value)
    }

    /// Minutes with one decimal. The unit of the EuroLeague's minutes is unverified, so the
    /// header says only MIN and the number is shown as the server sent it.
    public static func minutes(_ value: Double?) -> String {
        Formatting.decimal(value, places: 1)
    }

    /// `94–84`, only when both scores are present.
    public static func score(_ first: Int?, _ second: Int?) -> String {
        guard let first = first, let second = second else { return Formatting.emDash }
        return Formatting.integer(first) + "–" + Formatting.integer(second)
    }

    /// `9–1`, only when both wins and losses are present.
    public static func record(_ value: TeamRecord?) -> String {
        guard let value = value, let wins = value.wins, let losses = value.losses else {
            return Formatting.emDash
        }
        return Formatting.integer(wins) + "–" + Formatting.integer(losses)
    }

    /// `5–9`, made and attempted, only when both are present. Per-game averages of makes pass
    /// `places: 1`.
    public static func madeAttempted(_ made: Double?, _ attempted: Double?, places: Int = 0) -> String {
        guard let made = made, let attempted = attempted else { return Formatting.emDash }
        return Formatting.decimal(made, places: places) + "–" + Formatting.decimal(attempted, places: places)
    }

    /// The row sort key for a number: a missing value sorts below every real one.
    public static func sortKey(_ value: Double?) -> Double {
        guard let value = value, value.isFinite else { return -Double.infinity }
        return value
    }

    /// The row sort key for a whole number.
    public static func sortKey(_ value: Int?) -> Double {
        guard let value = value else { return -Double.infinity }
        return Double(value)
    }

    // MARK: - Times and days

    /// A tip-off in the reader's own zone, such as `Fri 2 Oct 19:30`. An absent timestamp is an
    /// em dash; one that does not parse is echoed back rather than hidden.
    public static func tipoff(_ utc: String?, timeZone: TimeZone = .current) -> String {
        guard let utc = utc, !utc.isEmpty else { return Formatting.emDash }
        guard let date = Formatting.parseTimestamp(utc) else { return utc }
        if timeZone.identifier == TimeZone.current.identifier {
            return LeagueDateFormatters.local.string(from: date)
        }
        let formatter = LeagueDateFormatters.make(template: LeagueDateFormatters.dayAndTime, zone: timeZone)
        return formatter.string(from: date)
    }

    /// The same instant in Berlin time with its zone abbreviation: `Fri 2 Oct 20:30 CEST`. The
    /// EuroLeague's day is Berlin's, so this goes in a `.help` next to the local time.
    public static func berlinTime(_ utc: String?) -> String {
        guard let utc = utc, !utc.isEmpty else { return Formatting.emDash }
        guard let date = Formatting.parseTimestamp(utc) else { return utc }
        return LeagueDateFormatters.berlin.string(from: date)
    }

    /// How long ago, from an RFC-3339 timestamp: `3 hr. ago`, or `just now` inside a minute.
    public static func age(_ timestamp: String?, now: Date = Date()) -> String {
        guard let timestamp = timestamp, !timestamp.isEmpty else { return Formatting.emDash }
        guard let date = Formatting.parseTimestamp(timestamp) else { return Formatting.emDash }
        if abs(now.timeIntervalSince(date)) < 60 { return "just now" }
        return LeagueDateFormatters.relative.localizedString(for: date, relativeTo: now)
    }

    /// How long ago, from the server's own `ageMinutes`, for rows that carry that and no clock.
    public static func ageFromMinutes(_ minutes: Double?) -> String {
        guard let minutes = minutes, minutes.isFinite else { return Formatting.emDash }
        if abs(minutes) < 1 { return "just now" }
        return LeagueDateFormatters.relative.localizedString(fromTimeInterval: -minutes * 60)
    }

    /// The full local date and time of a timestamp, for a `.help` tooltip.
    public static func absoluteLocal(_ timestamp: String?) -> String {
        guard let timestamp = timestamp, !timestamp.isEmpty else { return Formatting.emDash }
        guard let date = Formatting.parseTimestamp(timestamp) else { return timestamp }
        return LeagueDateFormatters.absolute.string(from: date)
    }

    /// A league day (`2026-10-02`, in the league's own calendar) as `Fri, Oct 2`. The day string
    /// round-trips unchanged, so a Berlin day is never shifted by the reader's zone.
    public static func leagueDate(_ day: String?) -> String {
        Formatting.mediumGameDate(day)
    }

    /// `yyyy-MM-dd` in the reader's calendar, for the `date` query of the NBA slate. Built from
    /// date components, not a formatter, so it cannot be shifted by a locale or a zone.
    public static func isoDay(_ date: Date, calendar: Calendar = .current) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        let year = parts.year ?? 1970
        let month = parts.month ?? 1
        let day = parts.day ?? 1
        return pad(year, width: 4) + "-" + pad(month, width: 2) + "-" + pad(day, width: 2)
    }

    private static func pad(_ value: Int, width: Int) -> String {
        let text = String(value)
        let missing = max(0, width - text.count)
        return String(repeating: "0", count: missing) + text
    }

    // MARK: - Games

    /// What a game's status cell says: the tip-off time while it is scheduled, `Result pending`
    /// once it should be over and no result is stored, the score when it is final, `Postponed`,
    /// or the raw status for a value this build does not know.
    public static func gameStatusText(_ game: LeagueGameRef?) -> String {
        guard let game = game else { return Formatting.emDash }
        let status = game.status ?? ""
        switch status {
        case "final":
            let text = score(game.homePts, game.awayPts)
            if text == Formatting.emDash { return "Final" }
            return text + overtimeSuffix(game.overtimePeriods)
        case "resultPending":
            return "Result pending"
        case "postponed":
            return "Postponed"
        case "scheduled", "":
            let time = tipoff(game.tipoffUtc)
            if time != Formatting.emDash { return time }
            return leagueDate(game.date)
        default:
            return status
        }
    }

    private static func overtimeSuffix(_ periods: Int?) -> String {
        guard let periods = periods, periods > 0 else { return "" }
        if periods == 1 { return " (OT)" }
        return " (" + String(periods) + "OT)"
    }

    /// The status word on its own: Scheduled, Result pending, Final, Postponed.
    public static func gameStatusWord(_ status: String?) -> String {
        guard let status = status, !status.isEmpty else { return Formatting.emDash }
        switch status {
        case "scheduled": return "Scheduled"
        case "resultPending": return "Result pending"
        case "final": return "Final"
        case "postponed": return "Postponed"
        default: return status
        }
    }

    /// A round's status: Upcoming, In progress, Result pending, Complete.
    public static func roundStatusWord(_ status: String?) -> String {
        guard let status = status, !status.isEmpty else { return Formatting.emDash }
        switch status {
        case "upcoming": return "Upcoming"
        case "inProgress": return "In progress"
        case "resultPending": return "Result pending"
        case "complete": return "Complete"
        default: return status
        }
    }

    /// A EuroLeague phase code: RS, PI, PO, FF.
    public static func phaseWord(_ phase: String?) -> String {
        guard let phase = phase, !phase.isEmpty else { return Formatting.emDash }
        switch phase {
        case "RS": return "Regular season"
        case "PI": return "Play-in"
        case "PO": return "Playoffs"
        case "FF": return "Final Four"
        default: return phase
        }
    }

    /// `Last 5`, for a window of recent games.
    public static func windowText(_ games: Int?) -> String {
        guard let games = games else { return Formatting.emDash }
        return "Last " + Formatting.integer(games)
    }

    /// `vs` at home, `at` away, `v` on a neutral floor; empty when unknown.
    public static func venueMark(isHome: Bool?, isNeutral: Bool?) -> String {
        if isNeutral == true { return "v" }
        guard let isHome = isHome else { return "" }
        return isHome ? "vs" : "at"
    }

    /// A finished game in one team's form, as `W 94–84`; an em dash when the result is missing.
    public static func formText(_ game: LeagueFormGame?) -> String {
        guard let game = game else { return Formatting.emDash }
        let scoreText = score(game.teamScore, game.opponentScore)
        guard let result = game.result, !result.isEmpty else { return scoreText }
        if scoreText == Formatting.emDash { return result }
        return result + " " + scoreText
    }

    // MARK: - Availability words

    /// An availability status: Out, Doubtful, Questionable, Probable, Available. A nil status is
    /// `No report`, which is never the same thing as `Available`.
    public static func statusWord(_ status: String?) -> String {
        guard let status = status, !status.isEmpty else { return "No report" }
        switch status {
        case "out": return "Out"
        case "doubtful": return "Doubtful"
        case "questionable": return "Questionable"
        case "probable": return "Probable"
        case "available": return "Available"
        default: return status
        }
    }

    /// The SF Symbol that goes with a status. Every name exists on iOS 17 and macOS 14.
    public static func statusSymbol(_ status: String?) -> String {
        guard let status = status, !status.isEmpty else { return "minus.circle" }
        switch status {
        case "out": return "xmark.circle.fill"
        case "doubtful": return "exclamationmark.triangle.fill"
        case "questionable": return "questionmark.circle.fill"
        case "probable": return "checkmark.circle"
        case "available": return "checkmark.circle.fill"
        default: return "questionmark.circle"
        }
    }

    /// A reason category: Injury, Illness, Rest, Coach's decision, Personal, Suspension,
    /// G League, Not with team, Not registered, Other.
    public static func reasonWord(_ reason: String?) -> String {
        guard let reason = reason, !reason.isEmpty else { return Formatting.emDash }
        switch reason {
        case "injury": return "Injury"
        case "illness": return "Illness"
        case "rest": return "Rest"
        case "coachDecision": return "Coach's decision"
        case "personal": return "Personal"
        case "suspension": return "Suspension"
        case "gLeague": return "G League"
        case "notWithTeam": return "Not with team"
        case "notRegistered": return "Not registered"
        case "other": return "Other"
        default: return reason
        }
    }

    /// Whose numbers a squad member's rates are.
    public static func basisWord(_ basis: String?) -> String {
        guard let basis = basis, !basis.isEmpty else { return Formatting.emDash }
        switch basis {
        case "workbookOfficial": return "Official (workbook)"
        case "workbookEstimate": return "Estimate (workbook)"
        case "positionPrior": return "Position average"
        case "officialUpdate": return "Official update"
        case "syntheticDemo": return "Invented demo"
        default: return basis
        }
    }

    /// Where a status or headline came from.
    public static func sourceKindWord(_ kind: String?) -> String {
        guard let kind = kind, !kind.isEmpty else { return Formatting.emDash }
        switch kind {
        case "leagueReport": return "League report"
        case "clubStatement": return "Club statement"
        case "pressArticle": return "Press"
        case "boxScoreInference": return "Box score"
        case "workbookImport": return "Your workbook"
        case "manual": return "Entered by you"
        default: return kind
        }
    }

    /// The state of a report or a source: Fresh, Stale, No report yet, Unreadable, Off, ...
    public static func stateWord(_ state: String?) -> String {
        guard let state = state, !state.isEmpty else { return Formatting.emDash }
        switch state {
        case "fresh": return "Fresh"
        case "ok": return "OK"
        case "stale": return "Stale"
        case "noReportYet": return "No report yet"
        case "unreadable": return "Unreadable"
        case "disabled": return "Off"
        case "notConfigured": return "Not set up"
        case "blocked": return "Blocked"
        case "error": return "Error"
        default: return state
        }
    }

    /// Why an entry no longer drives the projection (`LeagueAvailabilityEntry.outOfForceReason`).
    public static func outOfForceWord(_ reason: String?) -> String {
        guard let reason = reason, !reason.isEmpty else { return Formatting.emDash }
        switch reason {
        case "returnDatePassed": return "Expected return date has passed"
        case "returnRoundPassed": return "Expected return round has passed"
        case "playedSince": return "Played since this was reported"
        case "tooOld": return "Too old to count"
        default: return reason
        }
    }

    /// One team's report state.
    public static func reportStateWord(_ state: String?) -> String {
        guard let state = state, !state.isEmpty else { return Formatting.emDash }
        switch state {
        case "submitted": return "Report submitted"
        case "notYetSubmitted": return "Team has not submitted its report yet"
        case "noReport": return "No report"
        default: return state
        }
    }

    // MARK: - Projection and defence words

    /// Which run of the model a projection is.
    public static func modelKindWord(_ kind: String?) -> String {
        guard let kind = kind, !kind.isEmpty else { return Formatting.emDash }
        switch kind {
        case "latest": return "Latest"
        case "locked": return "Locked"
        case "reconstructed": return "Reconstructed"
        case "imported": return "Imported"
        default: return kind
        }
    }

    /// Why absent players were assumed available, as a phrase for the end of a sentence.
    public static func assumedBasisWord(_ basis: String?) -> String {
        guard let basis = basis, !basis.isEmpty else { return Formatting.emDash }
        switch basis {
        case "notOnSubmittedReport": return "not on the submitted report"
        case "teamReportPending": return "the team's report is not in yet"
        case "noReportPublished": return "no report published"
        case "noEntry": return "no entry"
        default: return basis
        }
    }

    /// How a projection's range was obtained.
    public static func intervalBasisWord(_ basis: String?) -> String {
        guard let basis = basis, !basis.isEmpty else { return Formatting.emDash }
        switch basis {
        case "assumed": return "Assumed range"
        case "fittedPrevSeason": return "Range fitted on last season"
        case "fittedLedger": return "Range fitted on past projections"
        default: return basis
        }
    }

    /// A defence band. `better` means the team allows fewer points than is typical at that
    /// position. A nil band is an em dash, never "typical".
    public static func bandWord(_ band: String?) -> String {
        guard let band = band, !band.isEmpty else { return Formatting.emDash }
        switch band {
        case "better": return "Better"
        case "typical": return "Typical"
        case "worse": return "Worse"
        default: return band
        }
    }

    /// Why a defence table is withheld.
    public static func withheldReasonWord(_ reason: String?) -> String {
        guard let reason = reason, !reason.isEmpty else { return Formatting.emDash }
        switch reason {
        case "minimumGames": return "Too few games"
        case "positionCoverage": return "Too few players placed at a position"
        case "leagueSample": return "League sample too small"
        default: return reason
        }
    }

    /// A position bucket: the NBA's G, F, C and the EuroLeague's five workbook positions.
    public static func positionWord(_ position: String?) -> String {
        guard let position = position, !position.isEmpty else { return Formatting.emDash }
        switch position {
        case "G": return "Guards"
        case "F": return "Forwards"
        case "C": return "Centers"
        case "PG": return "Point guards"
        case "SG": return "Shooting guards"
        case "SF": return "Small forwards"
        case "PF": return "Power forwards"
        case "unknown": return "Position not listed"
        default: return position
        }
    }

    /// The defence basis: per game or per minute.
    public static func defenseBasisWord(_ basis: String?) -> String {
        guard let basis = basis, !basis.isEmpty else { return Formatting.emDash }
        switch basis {
        case "perGame": return "Per game"
        case "perMinute": return "Per minute"
        default: return basis
        }
    }

    /// The defence scheme: three NBA-style buckets, or the workbook's five estimated positions.
    public static func schemeWord(_ scheme: String?) -> String {
        guard let scheme = scheme, !scheme.isEmpty else { return Formatting.emDash }
        switch scheme {
        case "gfc": return "Guards, forwards, centers"
        case "workbook5": return "Five positions (estimated)"
        default: return scheme
        }
    }
}

// MARK: - Formatter instances

/// The date formatters, each created once.
///
/// A `DateFormatter` is expensive to build, and a table of forty rows asks for eighty times. These
/// are never mutated after creation (the zone and locale are the system's auto-updating ones, so a
/// change of region is followed without a new instance), which is what makes sharing them safe: a
/// formatter that is only read can be used from any thread. A caller that wants some other zone
/// (the tests) gets a fresh formatter from `make`.
private enum LeagueDateFormatters {

    /// Weekday, day, month, hour and minute in the system's own order: `Fri 2 Oct 19:30`.
    static let dayAndTime = "EEEdMMMHHmm"

    static let local: DateFormatter = LeagueDateFormatters.make(
        template: LeagueDateFormatters.dayAndTime,
        zone: TimeZone.autoupdatingCurrent
    )

    static let berlin: DateFormatter = LeagueDateFormatters.make(
        template: LeagueDateFormatters.dayAndTime + "z",
        zone: TimeZone(identifier: "Europe/Berlin") ?? TimeZone(secondsFromGMT: 3600) ?? TimeZone.current
    )

    static let absolute: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale.autoupdatingCurrent
        formatter.timeZone = TimeZone.autoupdatingCurrent
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter
    }()

    static let relative: RelativeDateTimeFormatter = {
        let formatter = RelativeDateTimeFormatter()
        formatter.locale = Locale.autoupdatingCurrent
        formatter.unitsStyle = .abbreviated
        return formatter
    }()

    static func make(template: String, zone: TimeZone) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.locale = Locale.autoupdatingCurrent
        formatter.timeZone = zone
        formatter.setLocalizedDateFormatFromTemplate(template)
        return formatter
    }
}
