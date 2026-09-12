import Combine
import Foundation
import SwiftUI

// MARK: - Vocabulary

/// Which kind of subject a `subject` / `subjectList` field is currently pointing at. The value
/// comes from the field named by `dependsOn`, which is always a `subjectType` enum in practice.
public enum ConfigSubjectKind: String, Hashable, Sendable, CaseIterable {
    case player, team

    public var displayName: String {
        switch self {
        case .player: return "Player"
        case .team: return "Team"
        }
    }
}

/// One `$`-prefixed subject token the reader can choose instead of a concrete player or team.
public struct SubjectTokenOption: Identifiable, Hashable, Sendable {
    public let token: String
    public let title: String
    public let summary: String?

    public var id: String { token }

    public init(token: String, title: String, summary: String? = nil) {
        self.token = token
        self.title = title
        self.summary = summary
    }
}

/// The tokens the presets use, with names a reader recognises.
public enum SubjectTokenCatalog {

    public static func title(for token: String) -> String {
        switch token {
        case "$favorite_player": return "My favourite player"
        case "$favorite_team":   return "My favourite team"
        case "$featured_player": return "Featured player"
        case "$featured_team":   return "Featured team"
        case "$league_leader":   return "League scoring leader"
        default:
            return token.hasPrefix("$")
                ? ConfigDisplay.humanize(String(token.dropFirst()))
                : ConfigDisplay.humanize(token)
        }
    }

    /// The tokens that make sense for this kind of subject, catalog order preserved.
    @MainActor
    public static func options(for kind: ConfigSubjectKind, catalog: Catalog) -> [SubjectTokenOption] {
        let known = catalog.subjectTokens.isEmpty ? fallbackTokens : catalog.subjectTokens
        return known.compactMap { token -> SubjectTokenOption? in
            guard matches(token: token.token, kind: kind) else { return nil }
            return SubjectTokenOption(token: token.token,
                                      title: title(for: token.token),
                                      summary: token.summary)
        }
    }

    private static func matches(token: String, kind: ConfigSubjectKind) -> Bool {
        let lowered = token.lowercased()
        switch kind {
        case .player:
            return lowered.contains("player") || lowered.contains("leader")
        case .team:
            return lowered.contains("team")
        }
    }

    private static let fallbackTokens: [SubjectToken] = [
        SubjectToken(token: "$favorite_player", summary: "The player you pinned as a favourite."),
        SubjectToken(token: "$favorite_team", summary: "The team you pinned as a favourite."),
        SubjectToken(token: "$featured_player", summary: "The season's leader in PIE."),
        SubjectToken(token: "$featured_team", summary: "The season's leader in net rating."),
        SubjectToken(token: "$league_leader", summary: "The season's scoring leader.")
    ]
}

/// Small text helpers shared by the editors.
public enum ConfigDisplay {

    /// `"all_time"` becomes `"All Time"`; a string that already carries capitals is left alone.
    public static func humanize(_ raw: String) -> String {
        guard !raw.isEmpty else { return raw }
        let spaced = raw.replacingOccurrences(of: "_", with: " ")
        guard spaced == spaced.lowercased() else { return spaced }
        return spaced
            .split(separator: " ")
            .map { word -> String in
                guard let first = word.first else { return String(word) }
                return String(first).uppercased() + String(word.dropFirst())
            }
            .joined(separator: " ")
    }

    /// `["a", "b", "c"]` becomes `"a, b, c"`, with an em dash for nothing at all.
    public static func list(_ items: [String], empty: String = "Not set") -> String {
        items.isEmpty ? empty : items.joined(separator: ", ")
    }

    /// An ISO calendar day in the league's own time zone, which is how the API spells dates.
    public static func dayString(_ date: Date) -> String {
        var calendar = Calendar(identifier: .gregorian)
        if let zone = TimeZone(identifier: "America/New_York") {
            calendar.timeZone = zone
        }
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        let year = parts.year ?? 2000
        let month = parts.month ?? 1
        let day = parts.day ?? 1
        return "\(year)-\(pad(month))-\(pad(day))"
    }

    private static func pad(_ value: Int) -> String {
        value < 10 ? "0\(value)" : "\(value)"
    }
}

// MARK: - Seasons

/// The season strings the season picker offers. The catalog carries no season list of its own, so
/// they are generated from the league's own calendar: a season is named for the year it starts in
/// and the league year turns over in the autumn.
public enum SeasonOptions {

    public static let latestToken = "latest"
    public static let firstSeasonStartYear = 1946

    public static func currentSeasonStartYear(now: Date = Date()) -> Int {
        var calendar = Calendar(identifier: .gregorian)
        if let zone = TimeZone(identifier: "America/New_York") {
            calendar.timeZone = zone
        }
        let parts = calendar.dateComponents([.year, .month], from: now)
        let year = parts.year ?? 2025
        let month = parts.month ?? 1
        // Pre-season starts in late September, and that already belongs to the new league year.
        return month >= 9 ? year : year - 1
    }

    public static func seasonString(startingIn year: Int) -> String {
        let end = (year + 1) % 100
        let suffix = end < 10 ? "0\(end)" : "\(end)"
        return "\(year)-\(suffix)"
    }

    /// Every season, newest first.
    public static func allSeasons(now: Date = Date()) -> [String] {
        let start = currentSeasonStartYear(now: now)
        guard start >= firstSeasonStartYear else { return [seasonString(startingIn: firstSeasonStartYear)] }
        return stride(from: start, through: firstSeasonStartYear, by: -1).map { seasonString(startingIn: $0) }
    }
}

// MARK: - Subject directory

/// The player and team lookups the configuration editors need.
///
/// A `nil` client is legitimate: previews and tests use one, and the pickers then offer only the
/// subject tokens and whatever was handed in up front, rather than failing.
@MainActor
public final class ConfigSubjectDirectory: ObservableObject {

    @Published public private(set) var teams: [TeamRef] = []
    @Published public private(set) var searchResults: [PlayerRef] = []
    @Published public private(set) var knownPlayers: [PlayerID: PlayerRef] = [:]
    @Published public private(set) var isSearching: Bool = false
    @Published public private(set) var lastErrorMessage: String? = nil

    private let client: (any APIClientProtocol)?
    private var hasLoadedTeams = false

    public init(client: (any APIClientProtocol)?,
                teams: [TeamRef] = [],
                players: [PlayerRef] = []) {
        self.client = client
        self.teams = teams
        self.hasLoadedTeams = !teams.isEmpty
        for player in players {
            knownPlayers[player.playerId] = player
        }
    }

    public func loadTeamsIfNeeded() async {
        guard !hasLoadedTeams, let client = client else { return }
        hasLoadedTeams = true
        do {
            let response = try await client.teams()
            teams = response.teams.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
        } catch {
            hasLoadedTeams = false
            lastErrorMessage = "Teams could not be loaded."
        }
    }

    public func search(_ query: String) async {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 2 else {
            searchResults = []
            isSearching = false
            return
        }
        guard let client = client else {
            searchResults = []
            return
        }
        isSearching = true
        defer { isSearching = false }
        do {
            let response = try await client.searchPlayers(query: trimmed, limit: 25)
            let players = response.results.map { $0.player }
            searchResults = players
            for player in players {
                knownPlayers[player.playerId] = player
            }
            lastErrorMessage = nil
        } catch {
            searchResults = []
            lastErrorMessage = "Player search is unavailable right now."
        }
    }

    public func remember(_ player: PlayerRef) {
        knownPlayers[player.playerId] = player
    }

    public func team(_ id: TeamID) -> TeamRef? {
        teams.first { $0.teamId == id }
    }

    public func player(_ id: PlayerID) -> PlayerRef? {
        knownPlayers[id]
    }

    /// What to show on a row for a stored player value: a token name, a known player's name, or the
    /// id itself while the name is still unknown.
    public func playerTitle(for value: JSONValue?) -> String? {
        guard let value = value, !value.isNull else { return nil }
        if let token = value.stringValue {
            return SubjectTokenCatalog.title(for: token)
        }
        if let id = value.intValue {
            return player(id)?.name ?? "Player #\(id)"
        }
        return nil
    }

    public func teamTitle(for value: JSONValue?) -> String? {
        guard let value = value, !value.isNull else { return nil }
        if let token = value.stringValue {
            return SubjectTokenCatalog.title(for: token)
        }
        if let id = value.intValue {
            return team(id)?.name ?? "Team #\(id)"
        }
        return nil
    }

    public func title(for value: JSONValue?, kind: ConfigSubjectKind) -> String? {
        switch kind {
        case .player: return playerTitle(for: value)
        case .team: return teamTitle(for: value)
        }
    }
}

// MARK: - Validation

/// Live validation for a widget's configuration, keyed by field.
public enum ConfigValidator {

    public static func issues(for spec: WidgetSpec, config: [String: JSONValue]) -> [String: String] {
        var result: [String: String] = [:]
        for field in spec.config {
            if let message = issue(for: field, value: config[field.key]) {
                result[field.key] = message
            }
        }
        return result
    }

    public static func issue(for field: WidgetSpec.ConfigField, value: JSONValue?) -> String? {
        if isEmpty(value) {
            return field.required ? "\(field.label) is required." : nil
        }
        guard let value = value else { return nil }

        switch field.type {
        case .int, .double:
            guard let number = value.doubleValue else {
                return "\(field.label) must be a number."
            }
            if let lowerBound = field.min, number < lowerBound {
                return "\(field.label) cannot be below \(Formatting.decimal(lowerBound, places: field.type == .int ? 0 : 1))."
            }
            if let upperBound = field.max, number > upperBound {
                return "\(field.label) cannot be above \(Formatting.decimal(upperBound, places: field.type == .int ? 0 : 1))."
            }
        case .enumList, .metricList, .playerList, .teamList, .subjectList:
            let count = value.arrayValue?.count ?? 0
            if let minItems = field.minItems, count < minItems {
                return "Choose at least \(minItems)."
            }
            if let maxItems = field.maxItems, count > maxItems {
                return "Choose no more than \(maxItems)."
            }
        case .`enum`:
            if let options = field.options, let selected = value.stringValue, !options.contains(selected) {
                return "“\(selected)” is no longer one of the choices."
            }
        case .season, .metric, .player, .team, .subject, .bool, .date:
            break
        }
        return nil
    }

    /// Empty means "the reader has not chosen anything", which is what `required` is about.
    public static func isEmpty(_ value: JSONValue?) -> Bool {
        guard let value = value, !value.isNull else { return true }
        if let string = value.stringValue { return string.isEmpty }
        if let array = value.arrayValue { return array.isEmpty }
        return false
    }
}

// MARK: - Shared rows

/// The right-hand summary on a row that pushes a picker.
struct ConfigValueRow: View {
    let label: String
    let value: String
    var isPlaceholder: Bool = false

    var body: some View {
        HStack(spacing: Spacing.sm) {
            Text(label)
                .foregroundStyle(Palette.textPrimary)
            Spacer(minLength: Spacing.sm)
            Text(value)
                .foregroundStyle(isPlaceholder ? Palette.textTertiary : Palette.textSecondary)
                .multilineTextAlignment(.trailing)
                .lineLimit(2)
        }
        .accessibilityElement(children: .combine)
    }
}

/// A metric as the pickers show it: short name, full name and the glossary line.
struct MetricPickerRow: View {
    let metric: MetricDescriptor
    let isSelected: Bool

    private var eraNote: String? {
        if let estimated = metric.availability.estimatedBefore {
            return "Estimated before \(estimated)"
        }
        if metric.availability.seasonFrom != "1946-47" {
            return "From \(metric.availability.seasonFrom)"
        }
        return nil
    }

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.sm) {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                HStack(spacing: Spacing.xs) {
                    Text(metric.shortName)
                        .font(Typography.tableHeaderMono)
                        .foregroundStyle(Palette.textPrimary)
                    Text(metric.name)
                        .hardwoodText(.tableCell)
                        .lineLimit(1)
                }
                Text(metric.glossary)
                    .hardwoodText(.caption)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                if let eraNote = eraNote {
                    Label(eraNote, systemImage: "clock.arrow.circlepath")
                        .hardwoodText(.caption, color: Palette.warning)
                }
            }
            Spacer(minLength: Spacing.sm)
            if isSelected {
                Image(systemName: "checkmark")
                    .foregroundStyle(Palette.selection)
                    .accessibilityHidden(true)
            }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(isSelected ? AccessibilityTraits.isSelected : [])
    }
}

/// One metric category with its metrics, so the picker can use `ForEach` without key paths into
/// tuples (which Swift does not have).
private struct MetricGroup: Identifiable {
    let id: String
    let title: String
    let metrics: [MetricDescriptor]
}

// MARK: - Scalar editors

/// A season, or the `latest` token that follows the league forward.
struct SeasonFieldEditor: View {
    let field: WidgetSpec.ConfigField
    @Binding var value: JSONValue?
    let seasons: [String]

    private var selection: String {
        value?.stringValue ?? field.`default`?.stringValue ?? SeasonOptions.latestToken
    }

    /// A stored season the generated list does not contain (a token such as `career`) is offered
    /// anyway, so opening the sheet never silently rewrites the configuration.
    private var options: [String] {
        var list = seasons
        let current = selection
        if current != SeasonOptions.latestToken && !list.contains(current) {
            list.insert(current, at: 0)
        }
        return list
    }

    var body: some View {
        Picker(field.label, selection: Binding(get: { selection }, set: { value = .string($0) })) {
            Text("Latest").tag(SeasonOptions.latestToken)
            ForEach(options, id: \.self) { season in
                Text(season).tag(season)
            }
        }
        .pickerStyle(.navigationLink)
    }
}

/// A one-of-many choice: segmented when the options are few and short, a menu otherwise.
struct EnumFieldEditor: View {
    let field: WidgetSpec.ConfigField
    @Binding var value: JSONValue?

    private var options: [String] { field.options ?? [] }

    private var selection: String {
        value?.stringValue ?? field.`default`?.stringValue ?? options.first ?? ""
    }

    private var prefersSegmented: Bool {
        options.count > 1 && options.count <= 3 && options.allSatisfy { $0.count <= 10 }
    }

    private var picker: some View {
        Picker(field.label, selection: Binding(get: { selection }, set: { value = .string($0) })) {
            ForEach(options, id: \.self) { option in
                Text(ConfigDisplay.humanize(option)).tag(option)
            }
        }
    }

    var body: some View {
        Group {
            if options.isEmpty {
                ConfigValueRow(label: field.label, value: "No choices", isPlaceholder: true)
            } else if prefersSegmented {
                VStack(alignment: .leading, spacing: Spacing.xs) {
                    Text(field.label).hardwoodText(.statLabel)
                    picker
                        .pickerStyle(.segmented)
                        .labelsHidden()
                }
            } else {
                picker.pickerStyle(.menu)
            }
        }
    }
}

/// A yes/no field.
struct BoolFieldEditor: View {
    let field: WidgetSpec.ConfigField
    @Binding var value: JSONValue?

    private var isOn: Bool {
        value?.boolValue ?? field.`default`?.boolValue ?? false
    }

    var body: some View {
        Toggle(field.label, isOn: Binding(get: { isOn }, set: { value = .bool($0) }))
            .tint(Palette.selection)
    }
}

/// A whole number, clamped to the range the catalog gives.
struct IntFieldEditor: View {
    let field: WidgetSpec.ConfigField
    @Binding var value: JSONValue?

    private var lowerBound: Int { field.min.map { Int($0.rounded()) } ?? 0 }
    private var upperBound: Int {
        let proposed = field.max.map { Int($0.rounded()) } ?? max(lowerBound + 1, 100)
        return proposed > lowerBound ? proposed : lowerBound + 1
    }

    private var current: Int {
        let raw = value?.intValue ?? field.`default`?.intValue ?? lowerBound
        return min(max(raw, lowerBound), upperBound)
    }

    var body: some View {
        Stepper(value: Binding(get: { current }, set: { value = .int($0) }),
                in: lowerBound...upperBound) {
            HStack {
                Text(field.label)
                Spacer(minLength: Spacing.sm)
                Text("\(current)")
                    .font(Typography.tableCellMono)
                    .foregroundStyle(Palette.textSecondary)
            }
        }
    }
}

/// A fractional number, as a slider with the value beside the label.
struct DoubleFieldEditor: View {
    let field: WidgetSpec.ConfigField
    @Binding var value: JSONValue?

    private var lowerBound: Double { field.min ?? 0 }
    private var upperBound: Double {
        let proposed = field.max ?? 100
        return proposed > lowerBound ? proposed : lowerBound + 1
    }

    private var current: Double {
        let raw = value?.doubleValue ?? field.`default`?.doubleValue ?? lowerBound
        return min(max(raw, lowerBound), upperBound)
    }

    private var step: Double {
        let span = upperBound - lowerBound
        return span > 20 ? 1 : 0.5
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            HStack {
                Text(field.label)
                Spacer(minLength: Spacing.sm)
                Text(Formatting.decimal(current, places: 1))
                    .font(Typography.tableCellMono)
                    .foregroundStyle(Palette.textSecondary)
            }
            Slider(value: Binding(get: { current }, set: { value = .double($0) }),
                   in: lowerBound...upperBound,
                   step: step)
                .tint(Palette.selection)
                .accessibilityLabel(field.label)
                .accessibilityValue(Formatting.decimal(current, places: 1))
        }
    }
}

/// A slate date, or the `latest` token that follows the schedule forward.
struct DateFieldEditor: View {
    let field: WidgetSpec.ConfigField
    @Binding var value: JSONValue?

    private var raw: String {
        value?.stringValue ?? field.`default`?.stringValue ?? "latest"
    }

    private var isLatest: Bool { raw.lowercased() == "latest" || raw.isEmpty }

    private var date: Date {
        Formatting.parseDate(raw) ?? Date()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Toggle("Latest slate", isOn: Binding(
                get: { isLatest },
                set: { newValue in
                    value = newValue ? .string("latest") : .string(ConfigDisplay.dayString(date))
                }
            ))
            .tint(Palette.selection)

            if !isLatest {
                DatePicker(field.label,
                           selection: Binding(get: { date },
                                              set: { value = .string(ConfigDisplay.dayString($0)) }),
                           displayedComponents: [.date])
            }
        }
    }
}

// MARK: - Multi-select of plain options

/// A checklist over the catalog's own option strings.
struct MultiSelectPickerView: View {
    let title: String
    let options: [String]
    @Binding var selection: [String]

    var body: some View {
        List {
            Section {
                ForEach(options, id: \.self) { option in
                    Button {
                        toggle(option)
                    } label: {
                        HStack {
                            Text(ConfigDisplay.humanize(option))
                                .foregroundStyle(Palette.textPrimary)
                            Spacer(minLength: Spacing.sm)
                            if selection.contains(option) {
                                Image(systemName: "checkmark")
                                    .foregroundStyle(Palette.selection)
                                    .accessibilityHidden(true)
                            }
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(selection.contains(option) ? AccessibilityTraits.isSelected : [])
                }
            } footer: {
                Text("Leave everything unticked to include them all.")
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }

    private func toggle(_ option: String) {
        if let index = selection.firstIndex(of: option) {
            selection.remove(at: index)
        } else {
            selection.append(option)
        }
    }
}

struct EnumListFieldEditor: View {
    let field: WidgetSpec.ConfigField
    @Binding var value: JSONValue?

    private var selected: [String] {
        value?.stringArrayValue ?? field.`default`?.stringArrayValue ?? []
    }

    var body: some View {
        NavigationLink {
            MultiSelectPickerView(title: field.label,
                                  options: field.options ?? [],
                                  selection: Binding(get: { selected },
                                                     set: { value = .array($0.map { JSONValue.string($0) }) }))
        } label: {
            ConfigValueRow(label: field.label,
                           value: selected.isEmpty ? "All" : ConfigDisplay.list(selected.map { ConfigDisplay.humanize($0) }),
                           isPlaceholder: selected.isEmpty)
        }
    }
}

// MARK: - Metric pickers

/// One metric, searchable and grouped by the catalog's own categories.
struct MetricPickerView: View {
    let catalog: Catalog
    let scope: String
    let title: String
    /// Keys already used elsewhere in the same list, so they can be marked.
    var excluded: Set<String> = []
    @Binding var selection: String?

    @Environment(\.dismiss) private var dismiss
    @State private var query: String = ""

    private var groups: [MetricGroup] {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let candidates = catalog.metrics(inScope: scope).filter { metric in
            guard !excluded.contains(metric.key) else { return false }
            guard !trimmed.isEmpty else { return true }
            return metric.name.lowercased().contains(trimmed)
                || metric.shortName.lowercased().contains(trimmed)
                || metric.key.lowercased().contains(trimmed)
        }
        var order: [String] = []
        var buckets: [String: [MetricDescriptor]] = [:]
        for metric in candidates {
            if buckets[metric.category] == nil {
                order.append(metric.category)
                buckets[metric.category] = []
            }
            buckets[metric.category]?.append(metric)
        }
        return order.map { key in
            MetricGroup(id: key, title: catalog.categoryName(key), metrics: buckets[key] ?? [])
        }
    }

    var body: some View {
        List {
            if groups.isEmpty {
                Text("No metric matches “\(query)”.")
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
            }
            ForEach(groups) { group in
                Section(group.title) {
                    ForEach(group.metrics) { metric in
                        Button {
                            selection = metric.key
                            dismiss()
                        } label: {
                            MetricPickerRow(metric: metric, isSelected: metric.key == selection)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .searchable(text: $query, prompt: "Search metrics")
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct MetricFieldEditor: View {
    let field: WidgetSpec.ConfigField
    @Binding var value: JSONValue?
    let catalog: Catalog

    private var selectedKey: String? { value?.stringValue }

    private var summary: String {
        guard let key = selectedKey else { return "Choose" }
        return catalog.metric(key)?.shortName ?? key
    }

    var body: some View {
        NavigationLink {
            MetricPickerView(catalog: catalog,
                             scope: field.metricScope ?? "any",
                             title: field.label,
                             selection: Binding(get: { selectedKey },
                                                set: { newValue in
                                                    if let newValue = newValue { value = .string(newValue) }
                                                }))
        } label: {
            ConfigValueRow(label: field.label, value: summary, isPlaceholder: selectedKey == nil)
        }
    }
}

/// An ordered set of metrics: drag to reorder, swipe to remove, `+` to add another.
struct OrderedMetricPickerView: View {
    let catalog: Catalog
    let field: WidgetSpec.ConfigField
    @Binding var keys: [String]

    @State private var isAdding = false

    private var limit: Int? { field.maxItems }

    private var isFull: Bool {
        guard let limit = limit else { return false }
        return keys.count >= limit
    }

    private var footerText: String {
        var parts: [String] = []
        if let minItems = field.minItems { parts.append("at least \(minItems)") }
        if let limit = limit { parts.append("up to \(limit)") }
        let limits = parts.isEmpty ? "" : " (\(parts.joined(separator: ", ")))"
        return "Drag to reorder\(limits). The order here is the order on the widget."
    }

    var body: some View {
        List {
            Section {
                if keys.isEmpty {
                    Text("Nothing chosen yet.")
                        .hardwoodText(.tableCell, color: Palette.textSecondary)
                }
                ForEach(keys, id: \.self) { key in
                    row(for: key)
                }
                .onMove { offsets, destination in
                    keys.move(fromOffsets: offsets, toOffset: destination)
                }
                .onDelete { offsets in
                    keys.remove(atOffsets: offsets)
                }
            } header: {
                Text("Chosen")
            } footer: {
                Text(footerText)
            }
        }
        .navigationTitle(field.label)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) { EditButton() }
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    isAdding = true
                } label: {
                    Image(systemName: "plus")
                }
                .disabled(isFull)
                .accessibilityLabel("Add a metric")
            }
        }
        .sheet(isPresented: $isAdding) {
            NavigationStack {
                MetricPickerView(catalog: catalog,
                                 scope: field.metricScope ?? "any",
                                 title: "Add Metric",
                                 excluded: Set(keys),
                                 selection: Binding(get: { nil },
                                                    set: { newValue in
                                                        guard let newValue = newValue, !keys.contains(newValue) else { return }
                                                        if !isFull { keys.append(newValue) }
                                                    }))
            }
        }
    }

    @ViewBuilder private func row(for key: String) -> some View {
        if let metric = catalog.metric(key) {
            VStack(alignment: .leading, spacing: 1) {
                Text(metric.shortName)
                    .font(Typography.tableHeaderMono)
                    .foregroundStyle(Palette.textPrimary)
                Text(metric.name)
                    .hardwoodText(.caption)
            }
        } else {
            Text(key)
                .hardwoodText(.tableCell, color: Palette.textSecondary)
        }
    }
}

struct MetricListFieldEditor: View {
    let field: WidgetSpec.ConfigField
    @Binding var value: JSONValue?
    let catalog: Catalog

    private var keys: [String] {
        value?.stringArrayValue ?? field.`default`?.stringArrayValue ?? []
    }

    private var summary: String {
        guard !keys.isEmpty else { return "Choose" }
        let names = keys.map { catalog.metric($0)?.shortName ?? $0 }
        return ConfigDisplay.list(Array(names.prefix(3))) + (names.count > 3 ? " +\(names.count - 3)" : "")
    }

    var body: some View {
        NavigationLink {
            OrderedMetricPickerView(catalog: catalog,
                                    field: field,
                                    keys: Binding(get: { keys },
                                                  set: { value = .array($0.map { JSONValue.string($0) }) }))
        } label: {
            ConfigValueRow(label: field.label, value: summary, isPlaceholder: keys.isEmpty)
        }
    }
}

// MARK: - Player and team pickers

/// One player: the `$` tokens first, then live search results.
struct PlayerPickerView: View {
    @ObservedObject var directory: ConfigSubjectDirectory
    let title: String
    let tokens: [SubjectTokenOption]
    /// Ids already chosen elsewhere in the same list.
    var excluded: Set<Int> = []
    let onPick: (JSONValue) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var query: String = ""

    private var results: [PlayerRef] {
        directory.searchResults.filter { !excluded.contains($0.playerId) }
    }

    var body: some View {
        List {
            if !tokens.isEmpty {
                Section {
                    ForEach(tokens) { token in
                        Button {
                            onPick(.string(token.token))
                            dismiss()
                        } label: {
                            tokenLabel(token)
                        }
                        .buttonStyle(.plain)
                    }
                } header: {
                    Text("Follows you")
                } footer: {
                    Text("These stay pointed at whoever you pin as a favourite, so the widget keeps up on its own.")
                }
            }
            Section("Search") {
                if directory.isSearching {
                    HStack(spacing: Spacing.sm) {
                        ProgressView()
                        Text("Searching…").hardwoodText(.caption)
                    }
                }
                ForEach(results) { player in
                    Button {
                        directory.remember(player)
                        onPick(.int(player.playerId))
                        dismiss()
                    } label: {
                        PlayerRow(player: player,
                                  subtitle: [player.position, player.teamAbbr].compactMap { $0 }.joined(separator: " · "))
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
                if results.isEmpty && !directory.isSearching {
                    Text(query.count >= 2 ? "No player matches “\(query)”." : "Type at least two letters to search.")
                        .hardwoodText(.tableCell, color: Palette.textSecondary)
                }
            }
        }
        .searchable(text: $query, prompt: "Search players")
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .task(id: query) {
            // A short pause so a fast typist makes one request instead of eight.
            try? await Task.sleep(for: .milliseconds(280))
            guard !Task.isCancelled else { return }
            await directory.search(query)
        }
    }

    private func tokenLabel(_ token: SubjectTokenOption) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(token.title)
                .hardwoodText(.widgetTitle)
            if let summary = token.summary {
                Text(summary).hardwoodText(.caption)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
    }
}

/// One team, grouped by conference.
struct TeamPickerView: View {
    @ObservedObject var directory: ConfigSubjectDirectory
    let title: String
    let tokens: [SubjectTokenOption]
    var excluded: Set<Int> = []
    let onPick: (JSONValue) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var query: String = ""

    private var matches: [TeamRef] {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return directory.teams.filter { team in
            guard !excluded.contains(team.teamId) else { return false }
            guard !trimmed.isEmpty else { return true }
            return team.name.lowercased().contains(trimmed) || team.abbr.lowercased().contains(trimmed)
        }
    }

    private var east: [TeamRef] { matches.filter { $0.conference == "East" } }
    private var west: [TeamRef] { matches.filter { $0.conference == "West" } }
    private var unplaced: [TeamRef] { matches.filter { $0.conference != "East" && $0.conference != "West" } }

    var body: some View {
        List {
            if !tokens.isEmpty {
                Section("Follows you") {
                    ForEach(tokens) { token in
                        Button {
                            onPick(.string(token.token))
                            dismiss()
                        } label: {
                            Text(token.title)
                                .hardwoodText(.widgetTitle)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            teamSection("Eastern Conference", teams: east)
            teamSection("Western Conference", teams: west)
            teamSection("Other", teams: unplaced)
            if matches.isEmpty {
                Text(directory.teams.isEmpty ? "Teams are not available offline." : "No team matches “\(query)”.")
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
            }
        }
        .searchable(text: $query, prompt: "Search teams")
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .task { await directory.loadTeamsIfNeeded() }
    }

    @ViewBuilder private func teamSection(_ title: String, teams: [TeamRef]) -> some View {
        if !teams.isEmpty {
            Section(title) {
                ForEach(teams) { team in
                    Button {
                        onPick(.int(team.teamId))
                        dismiss()
                    } label: {
                        HStack(spacing: Spacing.sm) {
                            TeamBadge(team: team, size: .small)
                            Text(team.name).foregroundStyle(Palette.textPrimary)
                            Spacer(minLength: 0)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}

struct SubjectFieldEditor: View {
    let field: WidgetSpec.ConfigField
    @Binding var value: JSONValue?
    let kind: ConfigSubjectKind
    let tokens: [SubjectTokenOption]
    @ObservedObject var directory: ConfigSubjectDirectory

    private var summary: String {
        directory.title(for: value, kind: kind) ?? "Choose"
    }

    var body: some View {
        NavigationLink {
            destination
        } label: {
            ConfigValueRow(label: field.label,
                           value: summary,
                           isPlaceholder: ConfigValidator.isEmpty(value))
        }
    }

    @ViewBuilder private var destination: some View {
        switch kind {
        case .player:
            PlayerPickerView(directory: directory,
                             title: field.label,
                             tokens: tokens,
                             onPick: { value = $0 })
        case .team:
            TeamPickerView(directory: directory,
                           title: field.label,
                           tokens: tokens,
                           onPick: { value = $0 })
        }
    }
}

/// One entry in an ordered subject list. `JSONValue` is `Hashable`, but two identical entries would
/// collide as `ForEach` ids, so each row carries its own index-derived identity.
private struct SubjectEntry: Identifiable, Hashable {
    let id: String
    let value: JSONValue
}

/// An ordered list of players or teams: drag to reorder, swipe to remove, `+` to add.
struct OrderedSubjectPickerView: View {
    let field: WidgetSpec.ConfigField
    let kind: ConfigSubjectKind
    let tokens: [SubjectTokenOption]
    @ObservedObject var directory: ConfigSubjectDirectory
    @Binding var values: [JSONValue]

    @State private var isAdding = false

    private var entries: [SubjectEntry] {
        values.enumerated().map { SubjectEntry(id: "\($0.offset)-\($0.element.description)", value: $0.element) }
    }

    private var chosenIDs: Set<Int> {
        Set(values.compactMap { $0.intValue })
    }

    private var isFull: Bool {
        guard let maxItems = field.maxItems else { return false }
        return values.count >= maxItems
    }

    private var footerText: String {
        var parts: [String] = []
        if let minItems = field.minItems { parts.append("at least \(minItems)") }
        if let maxItems = field.maxItems { parts.append("up to \(maxItems)") }
        guard !parts.isEmpty else { return "Drag to reorder." }
        return "Drag to reorder. Choose \(parts.joined(separator: ", "))."
    }

    var body: some View {
        List {
            Section {
                if values.isEmpty {
                    Text("Nothing chosen yet.")
                        .hardwoodText(.tableCell, color: Palette.textSecondary)
                }
                ForEach(entries) { entry in
                    Text(directory.title(for: entry.value, kind: kind) ?? entry.value.description)
                        .foregroundStyle(Palette.textPrimary)
                }
                .onMove { offsets, destination in
                    values.move(fromOffsets: offsets, toOffset: destination)
                }
                .onDelete { offsets in
                    values.remove(atOffsets: offsets)
                }
            } header: {
                Text("Chosen")
            } footer: {
                Text(footerText)
            }
        }
        .navigationTitle(field.label)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) { EditButton() }
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    isAdding = true
                } label: {
                    Image(systemName: "plus")
                }
                .disabled(isFull)
                .accessibilityLabel(kind == .player ? "Add a player" : "Add a team")
            }
        }
        .sheet(isPresented: $isAdding) {
            NavigationStack {
                addDestination
            }
        }
    }

    @ViewBuilder private var addDestination: some View {
        switch kind {
        case .player:
            PlayerPickerView(directory: directory,
                             title: "Add Player",
                             tokens: tokens,
                             excluded: chosenIDs,
                             onPick: { append($0) })
        case .team:
            TeamPickerView(directory: directory,
                           title: "Add Team",
                           tokens: tokens,
                           excluded: chosenIDs,
                           onPick: { append($0) })
        }
    }

    private func append(_ value: JSONValue) {
        guard !isFull, !values.contains(value) else { return }
        values.append(value)
    }
}

struct SubjectListFieldEditor: View {
    let field: WidgetSpec.ConfigField
    @Binding var value: JSONValue?
    let kind: ConfigSubjectKind
    let tokens: [SubjectTokenOption]
    @ObservedObject var directory: ConfigSubjectDirectory

    private var values: [JSONValue] {
        value?.arrayValue ?? field.`default`?.arrayValue ?? []
    }

    private var summary: String {
        let titles = values.compactMap { directory.title(for: $0, kind: kind) }
        guard !titles.isEmpty else { return "Choose" }
        return ConfigDisplay.list(Array(titles.prefix(2))) + (titles.count > 2 ? " +\(titles.count - 2)" : "")
    }

    var body: some View {
        NavigationLink {
            OrderedSubjectPickerView(field: field,
                                     kind: kind,
                                     tokens: tokens,
                                     directory: directory,
                                     values: Binding(get: { values }, set: { value = .array($0) }))
        } label: {
            ConfigValueRow(label: field.label, value: summary, isPlaceholder: values.isEmpty)
        }
    }
}

// MARK: - Dispatcher

/// Builds the right control for one catalog field at runtime.
///
/// The switch is split in two so the type checker never has to reconcile fifteen different view
/// types in one `ViewBuilder` expression.
struct ConfigFieldEditor: View {
    let field: WidgetSpec.ConfigField
    @Binding var value: JSONValue?
    let catalog: Catalog
    @ObservedObject var directory: ConfigSubjectDirectory
    let seasons: [String]
    /// For `subject` / `subjectList`, resolved from the field named by `dependsOn`.
    let subjectKind: ConfigSubjectKind

    var body: some View {
        Group {
            switch field.type {
            case .season, .`enum`, .enumList, .bool, .int, .double, .date:
                scalarEditor
            case .metric, .metricList, .player, .playerList, .team, .teamList, .subject, .subjectList:
                subjectEditor
            }
        }
    }

    @ViewBuilder private var scalarEditor: some View {
        switch field.type {
        case .season:
            SeasonFieldEditor(field: field, value: $value, seasons: seasons)
        case .`enum`:
            EnumFieldEditor(field: field, value: $value)
        case .enumList:
            EnumListFieldEditor(field: field, value: $value)
        case .bool:
            BoolFieldEditor(field: field, value: $value)
        case .int:
            IntFieldEditor(field: field, value: $value)
        case .double:
            DoubleFieldEditor(field: field, value: $value)
        case .date:
            DateFieldEditor(field: field, value: $value)
        default:
            EmptyView()
        }
    }

    @ViewBuilder private var subjectEditor: some View {
        switch field.type {
        case .metric:
            MetricFieldEditor(field: field, value: $value, catalog: catalog)
        case .metricList:
            MetricListFieldEditor(field: field, value: $value, catalog: catalog)
        case .player, .team, .subject:
            SubjectFieldEditor(field: field,
                               value: $value,
                               kind: resolvedKind,
                               tokens: SubjectTokenCatalog.options(for: resolvedKind, catalog: catalog),
                               directory: directory)
        case .playerList, .teamList, .subjectList:
            SubjectListFieldEditor(field: field,
                                   value: $value,
                                   kind: resolvedKind,
                                   tokens: SubjectTokenCatalog.options(for: resolvedKind, catalog: catalog),
                                   directory: directory)
        default:
            EmptyView()
        }
    }

    /// A `player` field is always a player whatever the `subjectType` says; only `subject` and
    /// `subjectList` follow the field they depend on.
    private var resolvedKind: ConfigSubjectKind {
        switch field.type {
        case .player, .playerList:
            return .player
        case .team, .teamList:
            return .team
        default:
            return subjectKind
        }
    }
}

#if DEBUG
#Preview("Metric picker") {
    NavigationStack {
        MetricPickerView(catalog: DashboardPreviewData.catalog,
                         scope: "any",
                         title: "Metric",
                         selection: .constant("ts_pct"))
    }
}

#Preview("Player picker") {
    NavigationStack {
        PlayerPickerView(directory: ConfigSubjectDirectory(client: nil,
                                                           teams: [TeamRef.preview, TeamRef.previewOpponent],
                                                           players: [PlayerRef.preview, PlayerRef.previewSecondary]),
                         title: "Player",
                         tokens: SubjectTokenCatalog.options(for: .player, catalog: DashboardPreviewData.catalog),
                         onPick: { _ in })
    }
}

#Preview("Team picker") {
    NavigationStack {
        TeamPickerView(directory: ConfigSubjectDirectory(client: nil,
                                                         teams: [TeamRef.preview, TeamRef.previewOpponent]),
                       title: "Team",
                       tokens: [],
                       onPick: { _ in })
    }
}
#endif
