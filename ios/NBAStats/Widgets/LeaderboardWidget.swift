import Foundation
import SwiftUI

/// A ranked list of players or teams for one metric.
///
/// Three things make a leaderboard honest rather than decorative: the qualifier line, because a
/// rate-stat leader board without a minimum is meaningless; the era treatment on every value;
/// and the reader's own favourite marked wherever it lands, so the list answers "where is my
/// guy" without a search.
public struct LeaderboardWidget: View {

    /// A secondary column, kept with its index so the header and the cells stay in step even
    /// when a row's `secondary` array is short or out of order.
    private struct Column: Identifiable {
        let index: Int
        let descriptor: MetricDescriptor
        var id: Int { index }
    }

    private let payload: LeaderboardPayload
    private let size: WidgetSize

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.widgetFavorites) private var favorites

    @ScaledMetric(relativeTo: .footnote) private var valueWidth: CGFloat = 66
    @ScaledMetric(relativeTo: .caption2) private var columnWidth: CGFloat = 52

    public init(payload: LeaderboardPayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Density

    private var rowLimit: Int {
        switch size {
        case .small:  return dynamicTypeSize.isAccessibilitySize ? 2 : 3
        case .medium: return dynamicTypeSize.isAccessibilitySize ? 4 : 6
        case .large:  return 10
        }
    }

    private var rows: [LeaderboardRow] {
        Array(payload.rows.prefix(max(rowLimit, 0)))
    }

    private var isStacked: Bool { dynamicTypeSize.isAccessibilitySize }

    /// Extra columns only exist where there is room for them to line up.
    private var columns: [Column] {
        guard size == .large, !isStacked else { return [] }
        return payload.secondaryMetrics.prefix(3).enumerated().map { pair in
            Column(index: pair.offset, descriptor: pair.element)
        }
    }

    private var isAllTime: Bool { payload.scope == "all_time" }

    /// Whether a player row carries a portrait.
    ///
    /// Three things have to be true, and the third is the interesting one. A small tile is a
    /// single grid column, too narrow once the rank, the team badge and the value have taken
    /// their share. An accessibility size stacks the row and needs every point of width for the
    /// name. And a large tile that is *also* showing secondary metric columns has already spent
    /// its width on numbers — `valueWidth` plus three `columnWidth`s is most of an iPhone — so the
    /// face would come out of the name, which is the one thing the row cannot lose. A ten-row
    /// leaderboard therefore gets 24pt portraits or none, never anything bigger.
    private var showsAvatars: Bool {
        size != .small && !isStacked && columns.isEmpty
    }

    private var contextLine: String {
        var parts: [String] = []
        if isAllTime {
            parts.append("All time")
        } else {
            let context = Formatting.seasonContext(season: payload.season,
                                                   seasonType: payload.seasonType,
                                                   perMode: payload.perMode)
            if !context.isEmpty { parts.append(context) }
        }
        parts.append(payload.subjectType == "team" ? "Teams" : "Players")
        return parts.joined(separator: " · ")
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            header
            if !columns.isEmpty {
                columnHeader
            }
            if rows.isEmpty {
                emptyNote
            } else {
                ForEach(rows) { row in
                    rowView(row)
                }
            }
            if let qualifier = payload.qualifier, !qualifier.isEmpty, size != .small {
                Text(qualifier)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, Spacing.xxs)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Pieces

    private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
                Text(payload.metric.name)
                    .hardwoodText(.widgetTitle)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                Text(payload.metric.shortName)
                    .hardwoodText(.tableHeader)
                    .lineLimit(1)
                Spacer(minLength: 0)
            }
            Text(contextLine)
                .hardwoodText(.caption)
                .lineLimit(1)
        }
        .accessibilityElement(children: .combine)
    }

    private var columnHeader: some View {
        HStack(spacing: Spacing.sm) {
            Spacer(minLength: 0)
            Text(payload.metric.shortName)
                .hardwoodText(.tableHeader)
                .lineLimit(1)
                .frame(width: valueWidth, alignment: .trailing)
            ForEach(columns) { column in
                Text(column.descriptor.shortName)
                    .hardwoodText(.tableHeader)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .frame(width: columnWidth, alignment: .trailing)
            }
        }
        .accessibilityHidden(true)
    }

    private var emptyNote: some View {
        HStack(spacing: Spacing.xs) {
            Image(systemName: "line.3.horizontal.decrease.circle")
                .imageScale(.small)
                .foregroundStyle(Palette.textTertiary)
                .accessibilityHidden(true)
            Text("No one qualified for this leaderboard.")
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private func rowView(_ row: LeaderboardRow) -> some View {
        let isFavorite = favorites.matches(player: row.player) || favorites.matches(team: row.team)
        Group {
            if isStacked {
                stackedRow(row, isFavorite: isFavorite)
            } else {
                compactRow(row, isFavorite: isFavorite)
            }
        }
        .padding(.horizontal, Spacing.xs)
        .padding(.vertical, Spacing.xxs)
        .background(favoriteBackground(isFavorite))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText(row, isFavorite: isFavorite))
    }

    private func compactRow(_ row: LeaderboardRow, isFavorite: Bool) -> some View {
        HStack(spacing: Spacing.sm) {
            rankLabel(row.rank)
            subjectBadge(row)
            subjectLabel(row, isFavorite: isFavorite)
            Spacer(minLength: Spacing.xs)
            primaryValue(row)
                .frame(width: columns.isEmpty ? nil : valueWidth, alignment: .trailing)
            ForEach(columns) { column in
                Text(secondaryText(row, column: column))
                    .hardwoodText(.tableCell, monospacedDigits: true)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .frame(width: columnWidth, alignment: .trailing)
            }
        }
    }

    private func stackedRow(_ row: LeaderboardRow, isFavorite: Bool) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            HStack(spacing: Spacing.sm) {
                rankLabel(row.rank)
                subjectBadge(row)
                subjectLabel(row, isFavorite: isFavorite)
                Spacer(minLength: 0)
            }
            HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                Text(payload.metric.shortName)
                    .hardwoodText(.tableHeader)
                Spacer(minLength: Spacing.xs)
                primaryValue(row)
            }
        }
    }

    private func rankLabel(_ rank: Int) -> some View {
        Text(rank > 0 ? "\(rank)" : Formatting.emDash)
            .font(Typography.tableCellMono)
            .foregroundStyle(rank > 0 && rank <= 3 ? Palette.selection : Palette.textTertiary)
            .frame(minWidth: 22, alignment: .trailing)
    }

    @ViewBuilder private func subjectBadge(_ row: LeaderboardRow) -> some View {
        if let team = row.team {
            TeamBadge(team: team, size: .small)
        } else if let player = row.player, showsAvatars {
            HStack(spacing: Spacing.xs) {
                // Decorative: `accessibilityText(_:)` already reads the subject's name.
                PlayerAvatar(player: player, size: .small)
                    .accessibilityHidden(true)
                if let abbreviation = player.teamAbbr, !abbreviation.isEmpty {
                    TeamBadge(abbreviation: abbreviation, size: .small)
                }
            }
        } else if let abbreviation = row.player?.teamAbbr, !abbreviation.isEmpty {
            // Unchanged from before the avatar existed. Kept as its own branch rather than as an
            // empty `HStack` inside the one above, because a zero-width container still collects
            // the parent stack's spacing on both sides and would nudge the whole row across.
            TeamBadge(abbreviation: abbreviation, size: .small)
        }
    }

    private func subjectLabel(_ row: LeaderboardRow, isFavorite: Bool) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: Spacing.xxs) {
                Text(row.subjectName)
                    .hardwoodText(.tableCell,
                                  color: isFavorite ? Palette.selection : Palette.textPrimary)
                    .fontWeight(isFavorite ? .semibold : .regular)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                if isFavorite {
                    Image(systemName: "star.fill")
                        .font(Typography.tableHeader)
                        .foregroundStyle(Palette.selection)
                        .accessibilityHidden(true)
                }
            }
            if isAllTime, let season = row.season, !season.isEmpty {
                Text(season)
                    .hardwoodText(.caption)
                    .lineLimit(1)
            }
        }
    }

    private func primaryValue(_ row: LeaderboardRow) -> some View {
        StatValueView(value: row.value,
                      descriptor: payload.metric,
                      style: .stat,
                      showsLabel: false,
                      showsRank: false,
                      showsDelta: false,
                      season: row.season ?? payload.season,
                      alignment: .trailing)
    }

    @ViewBuilder private func favoriteBackground(_ isFavorite: Bool) -> some View {
        if isFavorite {
            RoundedRectangle(cornerRadius: Radius.chip, style: .continuous)
                .fill(Palette.selection.opacity(0.12))
                .overlay(
                    RoundedRectangle(cornerRadius: Radius.chip, style: .continuous)
                        .strokeBorder(Palette.selection.opacity(0.35), lineWidth: 1)
                )
        } else {
            Color.clear
        }
    }

    // MARK: Values

    /// Matches a secondary value by metric key first, and only falls back to position when the
    /// server sent an unkeyed row.
    private func secondaryValue(_ row: LeaderboardRow, column: Column) -> MetricValue? {
        if let match = row.secondary.first(where: { $0.metric == column.descriptor.key }) {
            return match
        }
        guard column.index < row.secondary.count else { return nil }
        return row.secondary[column.index]
    }

    private func secondaryText(_ row: LeaderboardRow, column: Column) -> String {
        guard let value = secondaryValue(row, column: column) else { return Formatting.emDash }
        return value.displayValue.isEmpty
            ? Formatting.value(value.value, format: column.descriptor.format)
            : value.displayValue
    }

    private func accessibilityText(_ row: LeaderboardRow, isFavorite: Bool) -> String {
        var parts: [String] = []
        if row.rank > 0 { parts.append("number \(row.rank)") }
        parts.append(row.subjectName)
        if isAllTime, let season = row.season, !season.isEmpty { parts.append(season) }
        let value = row.value.availability == .unavailable
            ? "not available"
            : (row.value.displayValue.isEmpty ? Formatting.emDash : row.value.displayValue)
        parts.append("\(payload.metric.name) \(value)")
        if row.value.availability != .full {
            parts.append(row.value.availability.hardwoodShortLabel)
        }
        for column in columns {
            parts.append("\(column.descriptor.name) \(secondaryText(row, column: column))")
        }
        if isFavorite { parts.append("one of your favourites") }
        return parts.joined(separator: ", ")
    }
}

#if DEBUG
#Preview("Leaderboard") {
    ScrollView {
        VStack(spacing: Spacing.md) {
            LeaderboardWidget(payload: .preview, size: .small)
                .hardwoodCard()
            LeaderboardWidget(payload: .preview, size: .large)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
    .widgetFavorites(playerID: 2544, teamID: nil)
}

#Preview("Leaderboard with columns") {
    let rows: [LeaderboardRow] = LeaderboardPayload.preview.rows.enumerated().map { pair in
        LeaderboardRow(rank: pair.element.rank,
                       player: pair.element.player,
                       team: pair.element.team,
                       value: pair.element.value,
                       secondary: [
                           MetricValue(metric: "net_rtg",
                                       value: 9.4 - Double(pair.offset),
                                       displayValue: Formatting.value(9.4 - Double(pair.offset), format: .rating1),
                                       availability: .full),
                           MetricValue(metric: "game_score",
                                       value: 24.0 - Double(pair.offset),
                                       displayValue: Formatting.value(24.0 - Double(pair.offset), format: .decimal1),
                                       availability: .full)
                       ],
                       season: pair.element.season)
    }
    let payload = LeaderboardPayload(
        metric: .preview,
        subjectType: "player",
        scope: "season",
        season: "2025-26",
        seasonType: "Regular Season",
        perMode: "PerGame",
        qualifier: "Minimum 15 games and 20.0 minutes per game",
        secondaryMetrics: [.previewRating, .previewImpact],
        rows: rows,
        nextCursor: nil
    )
    return LeaderboardWidget(payload: payload, size: .large)
        .hardwoodCard()
        .padding(Spacing.lg)
        .hardwoodBackground()
}
#endif
