import Foundation

/// Every number, date and season string the app renders goes through here.
///
/// Two rules drive the design: a missing value renders as an em dash and never as `0`, and the
/// decimal separator follows the reader's locale. `Formatting` is the implementation behind
/// `Catalog.format(_:_:)`; views should prefer the catalog entry point so the metric's own
/// `MetricFormat` picks the rule.
public enum Formatting {

    /// What an absent value looks like everywhere in the app.
    public static let emDash = "—"

    // MARK: - Numbers

    /// Formats a raw value in the metric's native unit. Percentages arrive as fractions in
    /// `[0, 1]` and are multiplied by 100 here, exactly as `contracts/metrics.json#/formats` says.
    public static func value(_ value: Double?, format: MetricFormat) -> String {
        guard let value = value, value.isFinite else { return emDash }
        switch format {
        case .integer:
            return FormatterBox.shared.string(value, decimals: 0, signed: false, grouping: true)
        case .decimal1:
            return FormatterBox.shared.string(value, decimals: 1, signed: false, grouping: true)
        case .decimal2:
            return FormatterBox.shared.string(value, decimals: 2, signed: false, grouping: true)
        case .percent1:
            return FormatterBox.shared.string(value * 100, decimals: 1, signed: false, grouping: false) + "%"
        case .percent2:
            return FormatterBox.shared.string(value * 100, decimals: 2, signed: false, grouping: false) + "%"
        case .rating1:
            return FormatterBox.shared.string(value, decimals: 1, signed: false, grouping: false)
        case .plusMinus1:
            // A plus/minus is always signed, except at exactly zero where a sign reads as noise.
            return FormatterBox.shared.string(value, decimals: 1, signed: value != 0, grouping: false)
        case .minutes:
            return FormatterBox.shared.string(value, decimals: 1, signed: false, grouping: false)
        }
    }

    /// A decimal with an explicit number of places, for values with no metric descriptor.
    public static func decimal(_ value: Double?, places: Int = 1, signed: Bool = false) -> String {
        guard let value = value, value.isFinite else { return emDash }
        return FormatterBox.shared.string(value, decimals: max(0, places), signed: signed, grouping: true)
    }

    /// A whole number with grouping separators, for counts and totals.
    public static func integer(_ value: Int?) -> String {
        guard let value = value else { return emDash }
        return FormatterBox.shared.string(Double(value), decimals: 0, signed: false, grouping: true)
    }

    /// A fraction in `[0, 1]` rendered as a percentage.
    public static func percent(_ value: Double?, places: Int = 1) -> String {
        guard let value = value, value.isFinite else { return emDash }
        return FormatterBox.shared.string(value * 100, decimals: max(0, places), signed: false, grouping: false) + "%"
    }

    /// `12` becomes `"12th"`, for ranks.
    public static func ordinal(_ rank: Int?) -> String {
        guard let rank = rank else { return emDash }
        return FormatterBox.shared.ordinalString(rank)
    }

    /// A percentile in `[0, 1]` rendered as a rank-like string: `0.93` becomes `"93rd"`.
    public static func percentile(_ value: Double?) -> String {
        guard let value = value, value.isFinite else { return emDash }
        // Clamp in `Double` space, *before* the conversion: `Int(_: Double)` traps on anything
        // outside `Int`'s range, and this number comes straight off the wire as an untyped
        // `Double`. Clamping afterwards is too late — the trap has already happened.
        let scaled = min(max(value * 100, 0), 100)
        return FormatterBox.shared.ordinalString(Int(scaled.rounded()))
    }

    // MARK: - Dates

    /// Parses an RFC-3339 UTC timestamp such as `"2026-01-03T07:12:44Z"`, with or without
    /// fractional seconds.
    public static func parseTimestamp(_ string: String?) -> Date? {
        guard let string = string, !string.isEmpty else { return nil }
        return FormatterBox.shared.timestamp(from: string)
    }

    /// Renders a date as the RFC-3339 UTC timestamp the API and the layout document use.
    public static func timestampString(_ date: Date) -> String {
        FormatterBox.shared.timestampString(from: date)
    }

    /// Parses an ISO-8601 calendar date such as `"2026-01-02"`, interpreted in US Eastern — the
    /// league's scheduling day — falling back to a full timestamp.
    public static func parseDate(_ string: String?) -> Date? {
        guard let string = string, !string.isEmpty else { return nil }
        if let date = FormatterBox.shared.calendarDate(from: string) { return date }
        return FormatterBox.shared.timestamp(from: string)
    }

    /// `"2026-01-02"` becomes `"Jan 2"`. Unparseable input is echoed back rather than blanked.
    public static func shortGameDate(_ string: String?) -> String {
        guard let string = string, !string.isEmpty else { return emDash }
        guard let date = parseDate(string) else { return string }
        return FormatterBox.shared.string(date, template: "MMMd")
    }

    /// `"2026-01-02"` becomes `"Fri, Jan 2"`.
    public static func mediumGameDate(_ string: String?) -> String {
        guard let string = string, !string.isEmpty else { return emDash }
        guard let date = parseDate(string) else { return string }
        return FormatterBox.shared.string(date, template: "EEEMMMd")
    }

    /// A short relative description of a past instant: `"4 minutes ago"`.
    public static func relative(_ date: Date, now: Date = Date()) -> String {
        let interval = now.timeIntervalSince(date)
        if abs(interval) < 60 { return "just now" }
        return FormatterBox.shared.relativeString(for: date, relativeTo: now)
    }

    /// The freshness line under a dashboard: `"Updated 4 minutes ago"`.
    public static func updatedString(_ date: Date?, now: Date = Date()) -> String {
        guard let date = date else { return "Not updated yet" }
        return "Updated " + relative(date, now: now)
    }

    // MARK: - Seasons

    /// `"2025-26"` stays itself; the request tokens get readable names.
    public static func seasonDisplay(_ season: String?) -> String {
        guard let season = season, !season.isEmpty else { return emDash }
        switch season.lowercased() {
        case "latest", "current":
            return "Current Season"
        case "career":
            return "Career"
        case "all_time", "alltime":
            return "All Time"
        default:
            return season
        }
    }

    /// The first calendar year of an NBA season string: `"1996-97"` is `1996`. Returns `nil` for
    /// tokens such as `"latest"` or `"career"`.
    public static func seasonStartYear(_ season: String?) -> Int? {
        guard let season = season else { return nil }
        let head = season.prefix(4)
        guard head.count == 4, let year = Int(head) else { return nil }
        return year
    }

    /// `"2025-26"` and `"Regular Season"` become `"2025-26 · Regular Season"`.
    public static func seasonContext(season: String?, seasonType: String?, perMode: String? = nil) -> String {
        var parts: [String] = []
        if let season = season, !season.isEmpty { parts.append(seasonDisplay(season)) }
        if let seasonType = seasonType, !seasonType.isEmpty { parts.append(seasonType) }
        if let perMode = perMode, !perMode.isEmpty { parts.append(perModeDisplay(perMode)) }
        return parts.joined(separator: " · ")
    }

    /// `"PerGame"` becomes `"Per Game"`.
    public static func perModeDisplay(_ perMode: String) -> String {
        switch perMode {
        case "PerGame":
            return "Per Game"
        case "Totals":
            return "Totals"
        case "Per36":
            return "Per 36"
        case "Per100":
            return "Per 100"
        default:
            return perMode
        }
    }
}

/// The formatter cache.
///
/// `NumberFormatter` and `DateFormatter` are expensive to build, so they are reused; every use
/// happens inside the lock because the same instance can be reached from the main actor (views)
/// and from a networking actor (fixture decoding) in the same run loop turn.
private final class FormatterBox: @unchecked Sendable {
    static let shared = FormatterBox()

    private let lock = NSLock()
    private var numberFormatters: [String: NumberFormatter] = [:]
    private var dateFormatters: [String: DateFormatter] = [:]
    private let ordinalFormatter: NumberFormatter
    private let isoFormatter: ISO8601DateFormatter
    private let isoFractionalFormatter: ISO8601DateFormatter
    private let calendarFormatter: DateFormatter
    private let relativeFormatter: RelativeDateTimeFormatter

    /// The league's scheduling day is US Eastern; UTC is only a fallback if the zone is missing.
    private let leagueTimeZone: TimeZone

    private init() {
        let zone = TimeZone(identifier: "America/New_York") ?? TimeZone(secondsFromGMT: 0) ?? .current
        leagueTimeZone = zone

        let ordinal = NumberFormatter()
        ordinal.numberStyle = .ordinal
        ordinal.locale = Locale.current
        ordinalFormatter = ordinal

        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]
        isoFormatter = iso

        let isoFractional = ISO8601DateFormatter()
        isoFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        isoFractionalFormatter = isoFractional

        let calendar = DateFormatter()
        calendar.locale = Locale(identifier: "en_US_POSIX")
        calendar.timeZone = zone
        calendar.dateFormat = "yyyy-MM-dd"
        calendarFormatter = calendar

        let relative = RelativeDateTimeFormatter()
        relative.unitsStyle = .full
        relativeFormatter = relative
    }

    func string(_ value: Double, decimals: Int, signed: Bool, grouping: Bool) -> String {
        lock.lock()
        defer { lock.unlock() }
        let key = "\(decimals)|\(signed)|\(grouping)"
        let formatter: NumberFormatter
        if let cached = numberFormatters[key] {
            formatter = cached
        } else {
            let created = NumberFormatter()
            created.numberStyle = .decimal
            created.locale = Locale.current
            created.minimumFractionDigits = decimals
            created.maximumFractionDigits = decimals
            created.usesGroupingSeparator = grouping
            if signed { created.positivePrefix = "+" }
            numberFormatters[key] = created
            formatter = created
        }
        if let formatted = formatter.string(from: NSNumber(value: value)) {
            return formatted
        }
        // Locale-independent last resort; only reached if the formatter itself fails.
        let plain = String(format: "%.\(decimals)f", value)
        return signed && value > 0 ? "+" + plain : plain
    }

    func ordinalString(_ value: Int) -> String {
        lock.lock()
        defer { lock.unlock() }
        return ordinalFormatter.string(from: NSNumber(value: value)) ?? String(value)
    }

    func timestamp(from string: String) -> Date? {
        lock.lock()
        defer { lock.unlock() }
        if let date = isoFormatter.date(from: string) { return date }
        return isoFractionalFormatter.date(from: string)
    }

    func timestampString(from date: Date) -> String {
        lock.lock()
        defer { lock.unlock() }
        return isoFormatter.string(from: date)
    }

    func calendarDate(from string: String) -> Date? {
        lock.lock()
        defer { lock.unlock() }
        return calendarFormatter.date(from: string)
    }

    func string(_ date: Date, template: String) -> String {
        lock.lock()
        defer { lock.unlock() }
        let key = "template|\(template)"
        let formatter: DateFormatter
        if let cached = dateFormatters[key] {
            formatter = cached
        } else {
            let created = DateFormatter()
            created.locale = Locale.current
            created.timeZone = leagueTimeZone
            created.setLocalizedDateFormatFromTemplate(template)
            dateFormatters[key] = created
            formatter = created
        }
        return formatter.string(from: date)
    }

    func relativeString(for date: Date, relativeTo reference: Date) -> String {
        lock.lock()
        defer { lock.unlock() }
        return relativeFormatter.localizedString(for: date, relativeTo: reference)
    }
}
