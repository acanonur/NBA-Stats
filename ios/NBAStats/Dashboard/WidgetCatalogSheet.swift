import Foundation
import SwiftUI

// MARK: - Grouping

/// How the thirteen widget kinds are grouped in the "Add Widget" sheet.
///
/// The grouping is about what a widget is *for* rather than what it renders, so the reader looking
/// for "something about last night" finds the scoreboard and the movers together.
public enum WidgetCatalogGroup: String, CaseIterable, Identifiable, Sendable {
    case today
    case flexible
    case players
    case teams
    case league

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .today:    return "Last Night"
        case .flexible: return "Player or Team"
        case .players:  return "Players"
        case .teams:    return "Teams"
        case .league:   return "The League"
        }
    }

    public var icon: String {
        switch self {
        case .today:    return "moon.stars"
        case .flexible: return "square.on.square"
        case .players:  return "person.crop.square"
        case .teams:    return "flag.checkered"
        case .league:   return "list.number"
        }
    }

    public var summary: String {
        switch self {
        case .today:    return "The completed slate and who moved the needle on it."
        case .flexible: return "Point these at a player or a team, whichever you care about."
        case .players:  return "One player, in depth."
        case .teams:    return "One team, or the shape of a team's season."
        case .league:   return "Everyone, ranked."
        }
    }

    public static func group(for kind: WidgetKind) -> WidgetCatalogGroup {
        switch kind {
        case .scoreboard, .dailyMovers:
            return .today
        case .statTile, .trendChart, .shotProfile:
            return .flexible
        case .playerSnapshot, .gameLog, .careerArc, .comparison, .nextGameProjection:
            return .players
        case .fourFactors, .teamEfficiency:
            return .teams
        case .leaderboard:
            return .league
        }
    }
}

// MARK: - Thumbnail

/// A cheap sketch of what a widget kind looks like.
///
/// Deliberately abstract: a real miniature would need a resolved payload, which the catalog sheet
/// has no business fetching. The shape still tells the reader whether they are about to add a
/// table, a chart or a single number.
struct WidgetThumbnail: View {

    /// The shape family a kind is drawn as.
    private enum Sketch {
        case headline
        case rows
        case grid
        case line
        case columns
        case split
    }

    let kind: WidgetKind
    let accent: AccentName

    private var sketch: Sketch {
        switch kind {
        case .statTile:       return .headline
        case .playerSnapshot: return .columns
        case .leaderboard:    return .rows
        case .gameLog:        return .grid
        case .trendChart:     return .line
        case .fourFactors:    return .grid
        case .shotProfile:    return .split
        case .comparison:     return .columns
        case .scoreboard:     return .split
        case .dailyMovers:    return .rows
        case .teamEfficiency: return .columns
        case .nextGameProjection: return .headline
        case .careerArc:      return .line
        }
    }

    private var tint: Color { accent.color }

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: Radius.chip, style: .continuous)
                .fill(accent.softTint)
            content
                .padding(Spacing.xs)
        }
        .frame(width: 58, height: 46)
        .overlay(
            RoundedRectangle(cornerRadius: Radius.chip, style: .continuous)
                .strokeBorder(tint.opacity(0.25), lineWidth: 1)
        )
        .accessibilityHidden(true)
    }

    @ViewBuilder private var content: some View {
        switch sketch {
        case .headline:
            VStack(alignment: .leading, spacing: 3) {
                bar(width: 14, height: 4, opacity: 0.4)
                bar(width: 30, height: 12, opacity: 0.9)
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        case .rows:
            VStack(spacing: 4) {
                ForEach(0..<3, id: \.self) { index in
                    HStack(spacing: 3) {
                        bar(width: 6, height: 5, opacity: 0.35)
                        bar(width: CGFloat(26 - index * 5), height: 5, opacity: 0.8)
                        Spacer(minLength: 0)
                    }
                }
            }
        case .grid:
            VStack(spacing: 3) {
                ForEach(0..<3, id: \.self) { _ in
                    HStack(spacing: 3) {
                        ForEach(0..<3, id: \.self) { _ in
                            bar(width: 11, height: 5, opacity: 0.55)
                        }
                    }
                }
            }
        case .line:
            HStack(alignment: .bottom, spacing: 3) {
                ForEach(WidgetThumbnail.lineHeights.indices, id: \.self) { index in
                    bar(width: 4, height: WidgetThumbnail.lineHeights[index], opacity: 0.75)
                }
            }
            .frame(maxWidth: .infinity, alignment: .center)
        case .columns:
            HStack(alignment: .bottom, spacing: 4) {
                ForEach(WidgetThumbnail.columnHeights.indices, id: \.self) { index in
                    bar(width: 6, height: WidgetThumbnail.columnHeights[index], opacity: 0.8)
                }
            }
        case .split:
            VStack(spacing: 4) {
                HStack(spacing: 4) {
                    bar(width: 16, height: 9, opacity: 0.8)
                    bar(width: 16, height: 9, opacity: 0.45)
                }
                bar(width: 36, height: 4, opacity: 0.3)
            }
        }
    }

    /// Fixed silhouettes, held as data so the `ViewBuilder` switch stays trivial to type-check.
    fileprivate static let lineHeights: [CGFloat] = [6, 11, 8, 15, 12, 19]
    fileprivate static let columnHeights: [CGFloat] = [16, 10, 20, 13]

    private func bar(width: CGFloat, height: CGFloat, opacity: Double) -> some View {
        RoundedRectangle(cornerRadius: 2, style: .continuous)
            .fill(tint.opacity(opacity))
            .frame(width: width, height: height)
    }
}

// MARK: - Sheet

/// "Add Widget": the whole catalog, grouped, with a sketch and a one-line summary each.
public struct WidgetCatalogSheet: View {

    @ObservedObject private var catalog: Catalog
    private let accent: AccentName
    private let onSelect: (WidgetKind) -> Void

    @Environment(\.dismiss) private var dismiss

    public init(catalog: Catalog,
                accent: AccentName = .orange,
                onSelect: @escaping (WidgetKind) -> Void) {
        _catalog = ObservedObject(wrappedValue: catalog)
        self.accent = accent
        self.onSelect = onSelect
    }

    /// Catalog entries for the kinds in a group, in catalog order.
    private func specs(in group: WidgetCatalogGroup) -> [WidgetSpec] {
        catalog.widgets.filter { WidgetCatalogGroup.group(for: $0.kind) == group }
    }

    public var body: some View {
        NavigationStack {
            List {
                if catalog.widgets.isEmpty {
                    Section {
                        Text("The widget catalog could not be read from this build. Reinstalling Hardwood will restore it.")
                            .hardwoodText(.tableCell, color: Palette.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                ForEach(WidgetCatalogGroup.allCases) { group in
                    section(for: group)
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Add Widget")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }

    @ViewBuilder private func section(for group: WidgetCatalogGroup) -> some View {
        let entries = specs(in: group)
        if !entries.isEmpty {
            Section {
                ForEach(entries) { spec in
                    Button {
                        onSelect(spec.kind)
                        dismiss()
                    } label: {
                        row(for: spec)
                    }
                    .buttonStyle(.plain)
                }
            } header: {
                Label(group.title, systemImage: group.icon)
            } footer: {
                Text(group.summary)
            }
        }
    }

    private func row(for spec: WidgetSpec) -> some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            WidgetThumbnail(kind: spec.kind, accent: accent)
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                HStack(spacing: Spacing.xs) {
                    Image(systemName: spec.icon)
                        .imageScale(.small)
                        .foregroundStyle(accent.color)
                        .accessibilityHidden(true)
                    Text(spec.name)
                        .hardwoodText(.widgetTitle)
                }
                Text(spec.summary)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                if let availableFrom = spec.availableFrom {
                    Text("From \(availableFrom) onward")
                        .hardwoodText(.caption, color: Palette.warning)
                }
            }
            Spacer(minLength: 0)
            Image(systemName: "plus.circle.fill")
                .foregroundStyle(accent.color)
                .accessibilityHidden(true)
        }
        .padding(.vertical, Spacing.xxs)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(spec.name). \(spec.summary)")
        .accessibilityHint("Adds this widget and opens its settings.")
    }
}

#if DEBUG
#Preview("Widget catalog") {
    WidgetCatalogSheet(catalog: DashboardPreviewData.catalog, accent: .orange) { _ in }
}

#Preview("Thumbnails") {
    ScrollView {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 80))], spacing: Spacing.md) {
            ForEach(WidgetKind.allCases, id: \.self) { kind in
                VStack(spacing: Spacing.xs) {
                    WidgetThumbnail(kind: kind, accent: .indigo)
                    Text(kind.fallbackName)
                        .hardwoodText(.caption)
                        .lineLimit(1)
                }
            }
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
