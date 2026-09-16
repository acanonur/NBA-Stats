import Foundation
import SwiftUI

/// The thirteen widget kinds (`contracts/widgets.json`, `contracts/CONTRACT.md` §4).
public enum WidgetKind: String, Codable, Hashable, Sendable, CaseIterable {
    case statTile          = "stat_tile"
    case playerSnapshot    = "player_snapshot"
    case leaderboard       = "leaderboard"
    case gameLog           = "game_log"
    case trendChart        = "trend_chart"
    case fourFactors       = "four_factors"
    case shotProfile       = "shot_profile"
    case comparison        = "comparison"
    case scoreboard        = "scoreboard"
    case dailyMovers       = "daily_movers"
    case teamEfficiency    = "team_efficiency"
    case nextGameProjection = "next_game_projection"
    case careerArc         = "career_arc"

    /// The catalog holds the real name; this is what the UI falls back to when the bundled
    /// catalog could not be read.
    public var fallbackName: String {
        switch self {
        case .statTile: return "Stat Tile"
        case .playerSnapshot: return "Player Snapshot"
        case .leaderboard: return "Leaderboard"
        case .gameLog: return "Game Log"
        case .trendChart: return "Trend"
        case .fourFactors: return "Four Factors"
        case .shotProfile: return "Shot Profile"
        case .comparison: return "Comparison"
        case .scoreboard: return "Scoreboard"
        case .dailyMovers: return "Daily Movers"
        case .teamEfficiency: return "Team Efficiency"
        case .nextGameProjection: return "Next Game"
        case .careerArc: return "Career Arc"
        }
    }

    /// The SF Symbol the catalog assigns to this kind, as a compile-time fallback.
    public var fallbackIcon: String {
        switch self {
        case .statTile: return "number.square"
        case .playerSnapshot: return "person.crop.square"
        case .leaderboard: return "list.number"
        case .gameLog: return "tablecells"
        case .trendChart: return "chart.xyaxis.line"
        case .fourFactors: return "square.grid.2x2"
        case .shotProfile: return "scope"
        case .comparison: return "person.2"
        case .scoreboard: return "sportscourt"
        case .dailyMovers: return "arrow.up.right"
        case .teamEfficiency: return "chart.bar.xaxis"
        case .nextGameProjection: return "function"
        case .careerArc: return "waveform.path.ecg"
        }
    }
}

/// A widget's footprint in the flowing grid (`contracts/widgets.json#/sizes`).
public enum WidgetSize: String, Codable, Hashable, Sendable, CaseIterable {
    case small, medium, large

    /// Columns spanned in a 2-column (compact) or 4-column (regular) grid. An unknown size class
    /// is treated as compact, which is the safe direction: a widget is never wider than the grid.
    public func columnSpan(horizontalSizeClass: UserInterfaceSizeClass?) -> Int {
        let isRegular = horizontalSizeClass == .regular
        switch self {
        case .small:
            return 1
        case .medium:
            return 2
        case .large:
            return isRegular ? 4 : 2
        }
    }

    /// The height the grid reserves before the content measures itself.
    public var estimatedHeight: CGFloat {
        switch self {
        case .small: return 148
        case .medium: return 232
        case .large: return 360
        }
    }

    public var displayName: String {
        switch self {
        case .small: return "Small"
        case .medium: return "Medium"
        case .large: return "Large"
        }
    }

    /// The next size in the small → medium → large → small cycle the resize control walks.
    public var next: WidgetSize {
        switch self {
        case .small: return .medium
        case .medium: return .large
        case .large: return .small
        }
    }
}

/// One widget inside a layout (`contracts/CONTRACT.md` §5).
public struct DashboardWidget: Codable, Hashable, Sendable, Identifiable {
    /// Unique within its layout and stable across drags, resizes and reconfigurations.
    public var id: String
    public var kind: WidgetKind
    /// `nil` means "use the catalog's default title for this kind".
    public var title: String?
    public var size: WidgetSize
    /// Schema-driven configuration; the fields come from `contracts/widgets.json`.
    public var config: [String: JSONValue]

    public init(id: String = UUID().uuidString,
                kind: WidgetKind,
                title: String? = nil,
                size: WidgetSize,
                config: [String: JSONValue]) {
        self.id = id
        self.kind = kind
        self.title = title
        self.size = size
        self.config = config
    }

    private enum CodingKeys: String, CodingKey {
        case id, kind, title, size, config
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decodeIfPresent(String.self, forKey: .id) ?? UUID().uuidString
        kind = try container.decode(WidgetKind.self, forKey: .kind)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        // Read as a raw string, not as `WidgetSize`. `decodeIfPresent` tolerates an absent key
        // but still *throws* on a size this build does not know, and that throw propagates out of
        // this widget — so `LayoutMigrator` drops it the way it drops an unknown *kind*, and the
        // reader loses a widget they configured over a cosmetic field with an obvious default.
        // The `?? .medium` below was always the intent; this makes it true for a bad value too.
        let rawSize = (try? container.decodeIfPresent(String.self, forKey: .size)) ?? nil
        size = rawSize.flatMap(WidgetSize.init(rawValue:)) ?? .medium
        config = try container.decodeIfPresent([String: JSONValue].self, forKey: .config) ?? [:]
    }

    /// A copy carrying a brand new id, used when duplicating a widget or a preset.
    public func copyWithNewID() -> DashboardWidget {
        DashboardWidget(id: UUID().uuidString, kind: kind, title: title, size: size, config: config)
    }

    /// A string value from the configuration, such as `"metric"` or `"season"`.
    public func configString(_ key: String) -> String? { config[key]?.stringValue }

    /// An integer value from the configuration, such as `"limit"`.
    public func configInt(_ key: String) -> Int? { config[key]?.intValue }

    /// A boolean value from the configuration, such as `"showSparkline"`.
    public func configBool(_ key: String) -> Bool? { config[key]?.boolValue }
}

/// The colour a layout is themed with (`contracts/CONTRACT.md` §5). The design system owns the
/// actual colours; this is only the name.
public enum AccentName: String, Codable, Hashable, Sendable, CaseIterable {
    case orange, indigo, teal, red, amber, green, blue, purple, graphite

    public var displayName: String {
        switch self {
        case .orange: return "Orange"
        case .indigo: return "Indigo"
        case .teal: return "Teal"
        case .red: return "Red"
        case .amber: return "Amber"
        case .green: return "Green"
        case .blue: return "Blue"
        case .purple: return "Purple"
        case .graphite: return "Graphite"
        }
    }
}

/// The editable dashboard document (`contracts/CONTRACT.md` §5).
///
/// The same shape is what `GET /v1/presets` returns per preset and what the app writes to disk.
/// Timestamps are encoded as RFC-3339 UTC strings by this type itself rather than by a decoder
/// strategy, so a layout round-trips identically whichever `JSONDecoder` reads it.
public struct DashboardLayout: Codable, Hashable, Sendable, Identifiable {
    public var id: String
    public var name: String
    /// An SF Symbol name.
    public var icon: String
    public var accent: AccentName
    public var schemaVersion: Int
    public var isPreset: Bool
    /// Retained after a preset is copied, so the app can offer "based on Daily Recap" and a reset.
    public var presetKey: String?
    public var tagline: String?
    public var createdAt: Date?
    public var updatedAt: Date?
    /// Render order; there are no explicit grid coordinates.
    public var widgets: [DashboardWidget]

    public static let currentSchemaVersion = 1

    public init(id: String = UUID().uuidString,
                name: String,
                icon: String = "square.grid.2x2",
                accent: AccentName = .orange,
                schemaVersion: Int = DashboardLayout.currentSchemaVersion,
                isPreset: Bool = false,
                presetKey: String? = nil,
                tagline: String? = nil,
                createdAt: Date? = nil,
                updatedAt: Date? = nil,
                widgets: [DashboardWidget] = []) {
        self.id = id
        self.name = name
        self.icon = icon
        self.accent = accent
        self.schemaVersion = schemaVersion
        self.isPreset = isPreset
        self.presetKey = presetKey
        self.tagline = tagline
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.widgets = widgets
    }

    // MARK: Editing

    /// A user-editable copy of a preset: new id, fresh widget ids, `isPreset` false, `presetKey`
    /// retained so the copy can still be reset back to the preset it came from.
    public func makeEditableCopy(named: String? = nil) -> DashboardLayout {
        let now = Date()
        return DashboardLayout(
            id: UUID().uuidString,
            name: named ?? name,
            icon: icon,
            accent: accent,
            schemaVersion: DashboardLayout.currentSchemaVersion,
            isPreset: false,
            presetKey: presetKey,
            tagline: tagline,
            createdAt: now,
            updatedAt: now,
            widgets: widgets.map { $0.copyWithNewID() }
        )
    }

    public mutating func move(fromOffsets: IndexSet, toOffset: Int) {
        widgets.move(fromOffsets: fromOffsets, toOffset: toOffset)
    }

    public mutating func remove(widgetID: String) {
        widgets.removeAll { $0.id == widgetID }
    }

    public mutating func append(_ widget: DashboardWidget) {
        widgets.append(widget)
    }

    /// Replaces the widget carrying the same id; a widget that is not in the layout is ignored.
    public mutating func replace(_ widget: DashboardWidget) {
        guard let index = widgets.firstIndex(where: { $0.id == widget.id }) else { return }
        widgets[index] = widget
    }

    public func widget(id widgetID: String) -> DashboardWidget? {
        widgets.first { $0.id == widgetID }
    }

    public var isEmpty: Bool { widgets.isEmpty }

    // MARK: Codable

    private enum CodingKeys: String, CodingKey {
        case id, name, icon, accent, schemaVersion, isPreset, presetKey, tagline
        case createdAt, updatedAt, widgets
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decodeIfPresent(String.self, forKey: .id) ?? UUID().uuidString
        name = try container.decodeIfPresent(String.self, forKey: .name) ?? "Dashboard"
        icon = try container.decodeIfPresent(String.self, forKey: .icon) ?? "square.grid.2x2"
        let accentRaw = try container.decodeIfPresent(String.self, forKey: .accent)
        accent = AccentName(rawValue: accentRaw ?? "") ?? .orange
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? DashboardLayout.currentSchemaVersion
        isPreset = try container.decodeIfPresent(Bool.self, forKey: .isPreset) ?? false
        presetKey = try container.decodeIfPresent(String.self, forKey: .presetKey)
        tagline = try container.decodeIfPresent(String.self, forKey: .tagline)
        let createdRaw = try container.decodeIfPresent(String.self, forKey: .createdAt)
        let updatedRaw = try container.decodeIfPresent(String.self, forKey: .updatedAt)
        createdAt = Formatting.parseTimestamp(createdRaw)
        updatedAt = Formatting.parseTimestamp(updatedRaw)
        // A widget this build cannot read is dropped rather than costing the reader the layout.
        // `LayoutMigrator` takes the same file through a path that also reports what it dropped.
        let rawWidgets = try container.decodeIfPresent([LenientWidget].self, forKey: .widgets) ?? []
        widgets = rawWidgets.compactMap { $0.widget }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(name, forKey: .name)
        try container.encode(icon, forKey: .icon)
        try container.encode(accent, forKey: .accent)
        try container.encode(schemaVersion, forKey: .schemaVersion)
        try container.encode(isPreset, forKey: .isPreset)
        try container.encodeIfPresent(presetKey, forKey: .presetKey)
        try container.encodeIfPresent(tagline, forKey: .tagline)
        if let createdAt = createdAt {
            try container.encode(Formatting.timestampString(createdAt), forKey: .createdAt)
        }
        if let updatedAt = updatedAt {
            try container.encode(Formatting.timestampString(updatedAt), forKey: .updatedAt)
        }
        try container.encode(widgets, forKey: .widgets)
    }
}

/// Decodes a widget without ever throwing, so one unreadable entry cannot cost a reader the
/// whole layout.
private struct LenientWidget: Decodable {
    let widget: DashboardWidget?

    init(from decoder: Decoder) throws {
        widget = try? DashboardWidget(from: decoder)
    }
}
