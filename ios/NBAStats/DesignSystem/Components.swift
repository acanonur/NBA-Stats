import Foundation
import SwiftUI

// MARK: - Number formatting

/// Client-side formatting for the few numbers the server does not pre-format: deltas, league
/// averages, axis labels. Anything that arrives with a `displayValue` is rendered verbatim.
public enum HardwoodNumberFormat {

    /// The em dash Hardwood uses wherever a number does not exist. Never `0`.
    public static let missing = Formatting.emDash

    /// Formats a raw value in the metric's native unit. Percentages arrive as fractions.
    ///
    /// One rule set, not two. This forwards to `Formatting.value`, which every other layer
    /// already uses. A second implementation here disagreed with it on grouping separators —
    /// `Formatting` turns grouping off for ratings, minutes and plus/minus, while
    /// `FloatingPointFormatStyle` groups by default, so season-total minutes read as `3,012.0`
    /// on one path and `3012.0` on the other — and on the percent sign, where `Formatting`
    /// appends an ASCII `%` and the `.Percent()` style emits the locale's own symbol and
    /// spacing. The same metric rendered differently depending on which path a view took.
    public static func string(_ value: Double?,
                              format: MetricFormat,
                              signed: Bool = false,
                              placeholder: String = HardwoodNumberFormat.missing) -> String {
        guard let value, value.isFinite else { return placeholder }
        let text = Formatting.value(value, format: format)
        // `.plusMinus1` signs itself; `signed` asks any other format for a leading `+` on a
        // positive value. Zero is never signed — a sign there reads as noise.
        guard signed, value > 0, !text.hasPrefix("+") else { return text }
        return "+" + text
    }

    /// "1st", "12th" — used in accessibility labels and percentile captions.
    public static func ordinal(_ value: Int) -> String {
        Formatting.ordinal(value)
    }

    /// A delta's *magnitude*, for a chip that already carries the direction in an arrow and a
    /// tint.
    ///
    /// `.plusMinus1` signs every non-zero value, so the magnitude of a bad night on `net_rtg`
    /// would render as `+19.6` beside a downward arrow — a red "▼ +19.6". The size of a
    /// plus/minus is a plain decimal; only its own value carries a sign.
    public static func magnitude(_ value: Double, format: MetricFormat) -> String {
        string(abs(value), format: format == .plusMinus1 ? .decimal1 : format)
    }
}

// MARK: - Stat value

/// How prominent a `StatValueView` should be.
public enum StatValueStyle: String, CaseIterable, Sendable {
    /// The hero number on a tile.
    case display
    /// A supporting metric under the hero number.
    case stat
    /// A number inside a table row.
    case cell

    var valueTextStyle: HardwoodTextStyle {
        switch self {
        case .display: return .displayValue
        case .stat:    return .statValue
        case .cell:    return .tableCell
        }
    }

    var labelTextStyle: HardwoodTextStyle {
        switch self {
        case .display, .stat: return .statLabel
        case .cell:           return .tableHeader
        }
    }

    var spacing: CGFloat {
        switch self {
        case .display: return Spacing.xs
        case .stat:    return Spacing.xxs
        case .cell:    return 0
        }
    }
}

/// Renders one `MetricValue` with the right typography, the era treatment, and optional rank
/// and delta chips.
///
/// The server's `displayValue` is shown verbatim — it already encodes the metric's format and
/// the em dash for a stat that never existed — so this view never turns a missing number into a
/// zero.
public struct StatValueView: View {
    private let value: MetricValue
    private let descriptor: MetricDescriptor?
    private let style: StatValueStyle
    private let showsLabel: Bool
    private let showsRank: Bool
    private let showsDelta: Bool
    private let season: String?
    private let alignment: HorizontalAlignment

    public init(value: MetricValue,
                descriptor: MetricDescriptor? = nil,
                style: StatValueStyle = .display,
                showsLabel: Bool = true,
                showsRank: Bool = true,
                showsDelta: Bool = false,
                season: String? = nil,
                alignment: HorizontalAlignment = .leading) {
        self.value = value
        self.descriptor = descriptor
        self.style = style
        self.showsLabel = showsLabel
        self.showsRank = showsRank
        self.showsDelta = showsDelta
        self.season = season
        self.alignment = alignment
    }

    /// The server formats every value; the fallback only covers an empty string.
    private var displayText: String {
        if value.displayValue.isEmpty {
            return HardwoodNumberFormat.string(value.value, format: descriptor?.format ?? .decimal1)
        }
        return value.displayValue
    }

    private var higherIsBetter: Bool { descriptor?.higherIsBetter ?? true }

    private var hasChips: Bool {
        let rankVisible = showsRank && (value.rank ?? 0) > 0
        let deltaVisible = showsDelta && value.delta != nil
        return rankVisible || deltaVisible
    }

    private var accessibilityText: String {
        var parts: [String] = [descriptor?.name ?? value.metric]
        parts.append(value.availability == .unavailable ? "not available" : displayText)
        if let rank = value.rank, rank > 0 {
            parts.append("ranked \(HardwoodNumberFormat.ordinal(rank))")
        }
        if let percentile = value.percentile, percentile.isFinite {
            // Clamped before the `Int` conversion: `Int(_: Double)` traps on an out-of-range
            // value, and this number came off the wire.
            let raw = percentile > 1 ? percentile : percentile * 100
            let scaled = min(max(raw, 0), 100)
            parts.append("\(HardwoodNumberFormat.ordinal(Int(scaled.rounded()))) percentile")
        }
        if value.availability != .full {
            parts.append(value.availability.hardwoodShortLabel)
        }
        return parts.joined(separator: ", ")
    }

    public var body: some View {
        VStack(alignment: alignment, spacing: style.spacing) {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
                Text(displayText)
                    .font(style.valueTextStyle.font)
                    .availabilityStyled(value.availability)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                AvailabilityBadge(availability: value.availability,
                                  showsText: style != .cell,
                                  isInteractive: style != .cell,
                                  metricName: descriptor?.name,
                                  season: season)
            }
            if showsLabel, let descriptor {
                Text(descriptor.shortName)
                    .hardwoodText(style.labelTextStyle)
                    .lineLimit(1)
            }
            if hasChips {
                HStack(spacing: Spacing.xs) {
                    if showsRank {
                        RankChip(rank: value.rank)
                    }
                    if showsDelta {
                        DeltaChip(delta: value.delta,
                                  higherIsBetter: higherIsBetter,
                                  format: descriptor?.format ?? .decimal1)
                    }
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
    }
}

// MARK: - Chips

/// A league rank, rendered as `#12`. Nothing renders when the rank is unknown.
public struct RankChip: View {
    private let rank: Int?
    private let total: Int?

    public init(rank: Int?, outOf total: Int? = nil) {
        self.rank = rank
        self.total = total
    }

    private func foreground(for rank: Int) -> Color {
        rank <= 10 ? Palette.selection : Palette.textSecondary
    }

    private func accessibilityText(for rank: Int) -> String {
        if let total, total > 0 {
            return "ranked \(HardwoodNumberFormat.ordinal(rank)) of \(total)"
        }
        return "ranked \(HardwoodNumberFormat.ordinal(rank))"
    }

    public var body: some View {
        if let rank, rank > 0 {
            Text("#\(rank)")
                .font(Typography.tableHeaderMono)
                .foregroundStyle(foreground(for: rank))
                .padding(.horizontal, Spacing.xs)
                .padding(.vertical, 1)
                .background(
                    Capsule(style: .continuous)
                        .fill(foreground(for: rank).opacity(0.12))
                )
                .accessibilityLabel(accessibilityText(for: rank))
        } else {
            EmptyView()
        }
    }
}

/// A signed change against a baseline, colored by whether the change is an improvement.
///
/// The arrow carries the same meaning as the color, so the chip still reads correctly without
/// hue perception.
public struct DeltaChip: View {
    private let delta: Double?
    private let higherIsBetter: Bool
    private let format: MetricFormat
    private let caption: String?

    public init(delta: Double?,
                higherIsBetter: Bool,
                format: MetricFormat,
                caption: String? = nil) {
        self.delta = delta
        self.higherIsBetter = higherIsBetter
        self.format = format
        self.caption = caption
    }

    private var tint: Color { Palette.value(for: delta, higherIsBetter: higherIsBetter) }

    private func accessibilityText(for delta: Double) -> String {
        let magnitude = HardwoodNumberFormat.magnitude(delta, format: format)
        let direction = delta > 0 ? "up" : "down"
        let suffix = caption.map { " \($0)" } ?? ""
        return "\(direction) \(magnitude)\(suffix)"
    }

    public var body: some View {
        if let delta, delta.isFinite, abs(delta) > 1e-9 {
            HStack(spacing: 1) {
                Image(systemName: delta > 0 ? "arrow.up.right" : "arrow.down.right")
                    .accessibilityHidden(true)
                Text(HardwoodNumberFormat.magnitude(delta, format: format))
                if let caption {
                    Text(caption)
                        .foregroundStyle(Palette.textTertiary)
                }
            }
            .font(Typography.tableHeaderMono)
            .foregroundStyle(tint)
            .padding(.horizontal, Spacing.xs)
            .padding(.vertical, 1)
            .background(Capsule(style: .continuous).fill(tint.opacity(0.12)))
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(accessibilityText(for: delta))
        } else {
            EmptyView()
        }
    }
}

// MARK: - Team badge

/// A colored monogram capsule standing in for a team mark.
///
/// The color is derived from the abbreviation with a fixed hash, so it is identical on every
/// launch. Hardwood ships no league logos.
public struct TeamBadge: View {
    /// Badge sizes, scaled by the reader's Dynamic Type setting.
    public enum Size: String, CaseIterable, Sendable {
        case small, medium, large

        var minWidth: CGFloat {
            switch self {
            case .small:  return 34
            case .medium: return 42
            case .large:  return 54
            }
        }

        var textStyle: HardwoodTextStyle {
            switch self {
            case .small:  return .tableHeader
            case .medium: return .statLabel
            case .large:  return .widgetTitle
            }
        }
    }

    @ScaledMetric(relativeTo: .footnote) private var scale: CGFloat = 1

    private let abbreviation: String
    private let fullName: String?
    private let size: Size

    public init(team: TeamRef, size: Size = .medium) {
        self.abbreviation = team.abbr
        self.fullName = team.name
        self.size = size
    }

    /// For call sites that only have an abbreviation, such as a `PlayerRef`'s `teamAbbr`.
    public init(abbreviation: String, name: String? = nil, size: Size = .medium) {
        self.abbreviation = abbreviation
        self.fullName = name
        self.size = size
    }

    private var tint: Color { Palette.monogramColor(for: abbreviation) }

    public var body: some View {
        Text(abbreviation)
            .font(size.textStyle.font)
            .fontWeight(.semibold)
            .foregroundStyle(tint)
            .lineLimit(1)
            .minimumScaleFactor(0.6)
            .padding(.horizontal, Spacing.xs)
            .padding(.vertical, Spacing.xxs)
            .frame(minWidth: size.minWidth * scale)
            .background(Capsule(style: .continuous).fill(tint.opacity(0.16)))
            .overlay(Capsule(style: .continuous).strokeBorder(tint.opacity(0.34), lineWidth: 1))
            .accessibilityLabel(fullName ?? abbreviation)
    }
}

// MARK: - Player row

/// A player line: optional rank, the player's face, team monogram, name, subtitle, and whatever
/// the caller wants on the trailing edge.
public struct PlayerRow<Trailing: View>: View {
    private let player: PlayerRef
    private let subtitle: String?
    private let rank: Int?
    private let avatar: PlayerAvatar.Size?
    private let trailing: Trailing

    /// - Parameter avatar: the leading portrait, or `nil` to suppress it. `.small` by default,
    ///   which is the only size that fits a row of this height without changing it; a caller with
    ///   a genuinely full-width list row — search results, say — passes `.medium` instead.
    public init(player: PlayerRef,
                subtitle: String? = nil,
                rank: Int? = nil,
                avatar: PlayerAvatar.Size? = .small,
                @ViewBuilder trailing: () -> Trailing) {
        self.player = player
        self.subtitle = subtitle
        self.rank = rank
        self.avatar = avatar
        self.trailing = trailing()
    }

    private var accessibilityText: String {
        var parts: [String] = []
        if let rank, rank > 0 { parts.append("rank \(rank)") }
        parts.append(player.name)
        if let subtitle, !subtitle.isEmpty { parts.append(subtitle) }
        return parts.joined(separator: ", ")
    }

    public var body: some View {
        HStack(spacing: Spacing.sm) {
            if let rank, rank > 0 {
                Text("\(rank)")
                    .font(Typography.tableCellMono)
                    .foregroundStyle(Palette.textTertiary)
                    .frame(minWidth: 22, alignment: .trailing)
                    .accessibilityHidden(true)
            }
            if let avatar {
                // Hidden from VoiceOver: the row's own label already reads the name, and the
                // avatar would otherwise say it a second time before it.
                PlayerAvatar(player: player, size: avatar)
                    .accessibilityHidden(true)
            }
            if let abbr = player.teamAbbr, !abbr.isEmpty {
                TeamBadge(abbreviation: abbr, size: .small)
                    .accessibilityHidden(true)
            }
            VStack(alignment: .leading, spacing: 1) {
                Text(player.name)
                    .hardwoodText(.widgetTitle)
                    .lineLimit(1)
                if let subtitle, !subtitle.isEmpty {
                    Text(subtitle)
                        .hardwoodText(.caption)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: Spacing.sm)
            trailing
        }
        .padding(.vertical, Spacing.xxs)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(accessibilityText)
    }
}

public extension PlayerRow where Trailing == EmptyView {
    init(player: PlayerRef,
         subtitle: String? = nil,
         rank: Int? = nil,
         avatar: PlayerAvatar.Size? = .small) {
        self.init(player: player,
                  subtitle: subtitle,
                  rank: rank,
                  avatar: avatar,
                  trailing: { EmptyView() })
    }
}

// MARK: - Section header

/// A heading above a group, with an optional trailing action.
public struct SectionHeader: View {
    private let title: String
    private let subtitle: String?
    private let actionTitle: String?
    private let action: (() -> Void)?

    public init(title: String,
                subtitle: String? = nil,
                actionTitle: String? = nil,
                action: (() -> Void)? = nil) {
        self.title = title
        self.subtitle = subtitle
        self.actionTitle = actionTitle
        self.action = action
    }

    public var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .hardwoodText(.sectionTitle)
                    .fixedSize(horizontal: false, vertical: true)
                if let subtitle, !subtitle.isEmpty {
                    Text(subtitle)
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: Spacing.sm)
            if let actionTitle, let action {
                Button(actionTitle, action: action)
                    .buttonStyle(.plain)
                    .font(Typography.widgetTitle)
                    .foregroundStyle(Palette.selection)
            }
        }
        .accessibilityElement(children: .contain)
    }
}

// MARK: - Metric pill

/// A metric chip for the configuration pickers.
public struct MetricPill: View {
    private let descriptor: MetricDescriptor
    private let isSelected: Bool
    private let tint: Color
    private let action: (() -> Void)?

    public init(descriptor: MetricDescriptor,
                isSelected: Bool,
                tint: Color = Palette.selection,
                action: (() -> Void)? = nil) {
        self.descriptor = descriptor
        self.isSelected = isSelected
        self.tint = tint
        self.action = action
    }

    /// True when the metric does not go all the way back, so the picker can warn before the
    /// widget has to.
    private var hasEraLimit: Bool {
        descriptor.availability.estimatedBefore != nil || descriptor.availability.seasonFrom != "1946-47"
    }

    private var accessibilityText: String {
        var text = descriptor.name
        if hasEraLimit {
            text += ", available from \(descriptor.availability.seasonFrom)"
        }
        return text
    }

    public var body: some View {
        Group {
            if let action {
                Button(action: action) { label }
                    .buttonStyle(.plain)
            } else {
                label
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
        .accessibilityAddTraits(isSelected ? AccessibilityTraits.isSelected : [])
    }

    private var label: some View {
        HStack(spacing: Spacing.xs) {
            VStack(alignment: .leading, spacing: 0) {
                Text(descriptor.shortName)
                    .font(Typography.tableHeader)
                    .foregroundStyle(isSelected ? tint : Palette.textPrimary)
                Text(descriptor.name)
                    .font(Typography.caption)
                    .foregroundStyle(isSelected ? tint.opacity(0.8) : Palette.textSecondary)
                    .lineLimit(1)
            }
            if hasEraLimit {
                Image(systemName: "clock.arrow.circlepath")
                    .imageScale(.small)
                    .foregroundStyle(Palette.warning)
                    .accessibilityHidden(true)
            }
        }
        .padding(.horizontal, Spacing.sm)
        .padding(.vertical, Spacing.xs)
        .background(
            RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                .fill(isSelected ? tint.opacity(0.14) : Palette.surfaceRaised)
        )
        .overlay(
            RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                .strokeBorder(isSelected ? tint.opacity(0.55) : Palette.separator,
                              lineWidth: isSelected ? 1.5 : 1)
        )
    }
}

// MARK: - Staleness

/// Marks whether a tile is showing live data or a cached copy.
///
/// Stale is a hollow ring, fresh is a filled dot: the state is legible without color.
public struct StalenessDot: View {
    private let isStale: Bool
    private let updatedAt: Date?
    private let showsText: Bool

    public init(isStale: Bool, updatedAt: Date? = nil, showsText: Bool = false) {
        self.isStale = isStale
        self.updatedAt = updatedAt
        self.showsText = showsText
    }

    private var relativeText: String? {
        guard let updatedAt else { return nil }
        return updatedAt.formatted(.relative(presentation: .named))
    }

    private var accessibilityText: String {
        guard let when = relativeText else {
            return isStale ? "Showing cached data" : "Up to date"
        }
        return isStale ? "Showing cached data, last updated \(when)" : "Updated \(when)"
    }

    public var body: some View {
        HStack(spacing: Spacing.xs) {
            if isStale {
                Circle()
                    .strokeBorder(Palette.warning, lineWidth: 1.5)
                    .frame(width: 8, height: 8)
            } else {
                Circle()
                    .fill(Palette.positive)
                    .frame(width: 6, height: 6)
            }
            if showsText, let relativeText {
                Text(relativeText)
                    .hardwoodText(.caption, color: isStale ? Palette.warning : Palette.textTertiary)
                    .lineLimit(1)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
    }
}

#if DEBUG

/// Preview fixtures, decoded from contract-shaped JSON rather than built with memberwise
/// initializers, so the previews keep compiling as `Core` evolves and so they exercise the
/// same decoding path the app uses.
enum DesignSystemPreviewData {

    private static func decode<T: Decodable>(_ type: T.Type, from json: String) -> T? {
        guard let data = json.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    private static let playersJSON = """
    [
      {"playerId": 2544, "name": "LeBron James", "firstName": "LeBron", "lastName": "James",
       "teamId": 1610612747, "teamAbbr": "LAL", "position": "F", "jersey": "23",
       "headshotUrl": null, "isActive": true},
      {"playerId": 1629029, "name": "Luka Doncic", "firstName": "Luka", "lastName": "Doncic",
       "teamId": 1610612742, "teamAbbr": "DAL", "position": "G", "jersey": "77",
       "headshotUrl": null, "isActive": true},
      {"playerId": 203507, "name": "Giannis Antetokounmpo", "firstName": "Giannis",
       "lastName": "Antetokounmpo", "teamId": 1610612749, "teamAbbr": "MIL", "position": "F",
       "jersey": "34", "headshotUrl": null, "isActive": true}
    ]
    """

    private static let teamsJSON = """
    [
      {"teamId": 1610612747, "abbr": "LAL", "name": "Los Angeles Lakers", "city": "Los Angeles",
       "nickname": "Lakers", "conference": "West", "division": "Pacific"},
      {"teamId": 1610612738, "abbr": "BOS", "name": "Boston Celtics", "city": "Boston",
       "nickname": "Celtics", "conference": "East", "division": "Atlantic"},
      {"teamId": 1610612749, "abbr": "MIL", "name": "Milwaukee Bucks", "city": "Milwaukee",
       "nickname": "Bucks", "conference": "East", "division": "Central"}
    ]
    """

    private static let metricValuesJSON = """
    [
      {"metric": "ts_pct", "value": 0.6153, "displayValue": "61.5%", "rank": 12,
       "percentile": 0.93, "leagueAverage": 0.5671, "delta": 0.0482, "isEstimated": false,
       "availability": "full"},
      {"metric": "usg_pct", "value": 0.2981, "displayValue": "29.8%", "rank": 7,
       "percentile": 0.88, "leagueAverage": 0.2, "delta": -0.0121, "isEstimated": true,
       "availability": "estimated"},
      {"metric": "stl", "value": null, "displayValue": "\u{2014}", "rank": null,
       "percentile": null, "leagueAverage": null, "delta": null, "isEstimated": false,
       "availability": "unavailable"},
      {"metric": "net_rtg", "value": 7.8, "displayValue": "+7.8", "rank": 1,
       "percentile": 0.99, "leagueAverage": 0.0, "delta": 2.4, "isEstimated": false,
       "availability": "partial"}
    ]
    """

    private static let descriptorsJSON = """
    [
      {"key": "ts_pct", "name": "True Shooting %", "shortName": "TS%", "category": "shooting",
       "format": "percent1", "higherIsBetter": true, "scope": ["player", "team"],
       "availability": {"seasonFrom": "1946-47", "perGameFrom": "1996-97",
                        "seasonLevelOnly": false, "estimatedBefore": null},
       "domain": {"min": 0.35, "max": 0.75},
       "glossary": "PTS / (2 * (FGA + 0.44*FTA)). Shooting efficiency including free throws."},
      {"key": "usg_pct", "name": "Usage %", "shortName": "USG%", "category": "usage",
       "format": "percent1", "higherIsBetter": true, "scope": ["player", "team"],
       "availability": {"seasonFrom": "1977-78", "perGameFrom": "1996-97",
                        "seasonLevelOnly": false, "estimatedBefore": "1996-97"},
       "domain": {"min": 0.05, "max": 0.42},
       "glossary": "Share of team possessions a player ends while on the floor."},
      {"key": "stl", "name": "Steals", "shortName": "STL", "category": "volume",
       "format": "decimal1", "higherIsBetter": true, "scope": ["player", "team"],
       "availability": {"seasonFrom": "1973-74", "perGameFrom": "1973-74",
                        "seasonLevelOnly": false, "estimatedBefore": null},
       "domain": null, "glossary": "Steals. Officially tracked from 1973-74."},
      {"key": "net_rtg", "name": "Net Rating", "shortName": "NetRtg", "category": "efficiency",
       "format": "rating1", "higherIsBetter": true, "scope": ["player", "team"],
       "availability": {"seasonFrom": "1996-97", "perGameFrom": "1996-97",
                        "seasonLevelOnly": false, "estimatedBefore": null},
       "domain": {"min": -25.0, "max": 25.0},
       "glossary": "Offensive Rating minus Defensive Rating."}
    ]
    """

    static let players: [PlayerRef] = decode([PlayerRef].self, from: playersJSON) ?? []
    static let teams: [TeamRef] = decode([TeamRef].self, from: teamsJSON) ?? []
    static let metricValues: [MetricValue] = decode([MetricValue].self, from: metricValuesJSON) ?? []
    static let descriptors: [MetricDescriptor] = decode([MetricDescriptor].self, from: descriptorsJSON) ?? []

    static var player: PlayerRef? { players.first }
    static var team: TeamRef? { teams.first }
    static var metricValue: MetricValue? { metricValues.first }
    static var descriptor: MetricDescriptor? { descriptors.first }
}

/// Unwraps an optional preview fixture without force-unwrapping.
struct DSPreviewFixture<Value, Content: View>: View {
    private let value: Value?
    private let content: (Value) -> Content

    init(_ value: Value?, @ViewBuilder content: @escaping (Value) -> Content) {
        self.value = value
        self.content = content
    }

    var body: some View {
        if let value {
            content(value)
        } else {
            Text("Preview fixture unavailable")
                .hardwoodText(.caption)
        }
    }
}

#Preview("Stat values") {
    ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            ForEach(DesignSystemPreviewData.metricValues.indices, id: \.self) { index in
                let value = DesignSystemPreviewData.metricValues[index]
                StatValueView(value: value,
                              descriptor: DesignSystemPreviewData.descriptors.first(where: { $0.key == value.metric }),
                              style: .display,
                              showsDelta: true,
                              season: "1971-72")
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .hardwoodCard()
            }
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}

#Preview("Chips and badges") {
    VStack(alignment: .leading, spacing: Spacing.lg) {
        HStack(spacing: Spacing.sm) {
            RankChip(rank: 1)
            RankChip(rank: 12, outOf: 482)
            RankChip(rank: nil)
            DeltaChip(delta: 0.048, higherIsBetter: true, format: .percent1)
            DeltaChip(delta: -1.4, higherIsBetter: true, format: .rating1)
            DeltaChip(delta: nil, higherIsBetter: true, format: .decimal1)
        }
        HStack(spacing: Spacing.sm) {
            ForEach(DesignSystemPreviewData.teams) { team in
                TeamBadge(team: team, size: .medium)
            }
        }
        HStack(spacing: Spacing.sm) {
            StalenessDot(isStale: false, updatedAt: Date(), showsText: true)
            StalenessDot(isStale: true, updatedAt: Date(timeIntervalSinceNow: -7200), showsText: true)
        }
    }
    .padding(Spacing.lg)
    .hardwoodCard()
    .padding(Spacing.lg)
    .hardwoodBackground()
}

#Preview("Rows, headers, pills") {
    VStack(alignment: .leading, spacing: Spacing.lg) {
        SectionHeader(title: "Leaders",
                      subtitle: "True Shooting %, 2025-26",
                      actionTitle: "See all",
                      action: { })
        VStack(spacing: 0) {
            ForEach(DesignSystemPreviewData.players.indices, id: \.self) { index in
                let player = DesignSystemPreviewData.players[index]
                PlayerRow(player: player,
                          subtitle: "\(player.position ?? "—") · 34.5 MIN",
                          rank: index + 1) {
                    DSPreviewFixture(DesignSystemPreviewData.metricValue) { value in
                        StatValueView(value: value, style: .cell, showsLabel: false, showsRank: false)
                    }
                }
                Rectangle()
                    .fill(Palette.separator)
                    .frame(height: 1)
            }
        }
        HStack(spacing: Spacing.sm) {
            ForEach(Array(DesignSystemPreviewData.descriptors.prefix(3))) { descriptor in
                MetricPill(descriptor: descriptor,
                           isSelected: descriptor.key == "ts_pct",
                           action: { })
            }
        }
    }
    .padding(Spacing.lg)
    .hardwoodBackground()
}
#endif
