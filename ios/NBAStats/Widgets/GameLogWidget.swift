import Foundation
import SwiftUI

/// A player's recent games as a real table.
///
/// The date, opponent and result are pinned on the leading edge while the metric columns scroll
/// horizontally under a header that stays put, because the first question about any row is
/// always "which game was this". A cell whose metric the era never recorded prints an em dash;
/// a cell matching the season best is marked, so a career night is visible at a glance.
///
/// At accessibility text sizes the columns are abandoned entirely and each game becomes a
/// stacked block: a table that has to be read at 300% is not a table any more.
public struct GameLogWidget: View {

    private struct Entry: Identifiable {
        let index: Int
        let row: GameLogPayloadRow
        var id: GameID { row.gameId }
    }

    private struct Column: Identifiable {
        let index: Int
        let descriptor: MetricDescriptor
        var id: Int { index }
    }

    private let payload: GameLogPayload
    private let size: WidgetSize

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @ScaledMetric(relativeTo: .footnote) private var rowHeight: CGFloat = 26
    @ScaledMetric(relativeTo: .footnote) private var pinnedWidth: CGFloat = 112
    @ScaledMetric(relativeTo: .footnote) private var columnWidth: CGFloat = 58

    public init(payload: GameLogPayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Density

    private var rowLimit: Int {
        switch size {
        case .small:  return 3
        case .medium: return 5
        case .large:  return 8
        }
    }

    private var columnLimit: Int {
        switch size {
        case .small:  return 2
        case .medium: return 3
        case .large:  return 6
        }
    }

    private var isStacked: Bool { dynamicTypeSize.isAccessibilitySize }

    private var entries: [Entry] {
        payload.rows.prefix(max(rowLimit, 0)).enumerated().map { pair in
            Entry(index: pair.offset, row: pair.element)
        }
    }

    private var columns: [Column] {
        payload.columns.prefix(max(columnLimit, 0)).enumerated().map { pair in
            Column(index: pair.offset, descriptor: pair.element)
        }
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            if entries.isEmpty {
                emptyNote
            } else if isStacked {
                stackedLog
            } else {
                table
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            HStack(spacing: Spacing.xs) {
                if let abbreviation = payload.player.teamAbbr, !abbreviation.isEmpty {
                    TeamBadge(abbreviation: abbreviation, size: .small)
                }
                Text(payload.player.name)
                    .hardwoodText(.widgetTitle)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                Spacer(minLength: 0)
            }
            Text(Formatting.seasonContext(season: payload.season, seasonType: payload.seasonType))
                .hardwoodText(.caption)
                .lineLimit(1)
        }
        .accessibilityElement(children: .combine)
    }

    private var emptyNote: some View {
        HStack(spacing: Spacing.xs) {
            Image(systemName: "tablecells")
                .imageScale(.small)
                .foregroundStyle(Palette.textTertiary)
                .accessibilityHidden(true)
            Text("No games logged for this season yet.")
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: Table

    private var table: some View {
        HStack(alignment: .top, spacing: Spacing.sm) {
            VStack(alignment: .leading, spacing: 0) {
                pinnedHeaderCell
                rule
                ForEach(entries) { entry in
                    pinnedCell(entry)
                }
            }
            .frame(width: pinnedWidth, alignment: .leading)
            ScrollView(.horizontal, showsIndicators: false) {
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 0) {
                        ForEach(columns) { column in
                            Text(column.descriptor.shortName)
                                .hardwoodText(.tableHeader)
                                .lineLimit(1)
                                .minimumScaleFactor(0.7)
                                .frame(width: columnWidth, height: rowHeight, alignment: .trailing)
                        }
                    }
                    scrollRule
                    ForEach(entries) { entry in
                        HStack(spacing: 0) {
                            ForEach(columns) { column in
                                valueCell(entry, column: column)
                            }
                        }
                        .background(stripe(entry))
                    }
                }
            }
            .accessibilityHidden(true)
        }
    }

    private var rule: some View {
        Rectangle()
            .fill(Palette.separator)
            .frame(height: 1)
            .accessibilityHidden(true)
    }

    /// The same hairline, inside the horizontal scroll view. A `Rectangle` has no ideal width,
    /// and the scroll axis proposes none, so the rule is given the width of the column block —
    /// otherwise it would collapse to a stub while the rows beside it stayed full width.
    private var scrollRule: some View {
        Rectangle()
            .fill(Palette.separator)
            .frame(width: columnWidth * CGFloat(max(columns.count, 1)), height: 1)
            .accessibilityHidden(true)
    }

    private var pinnedHeaderCell: some View {
        HStack(spacing: Spacing.xs) {
            Text("Date")
                .hardwoodText(.tableHeader)
            Spacer(minLength: 0)
            Text("Opp")
                .hardwoodText(.tableHeader)
        }
        .frame(height: rowHeight)
        .accessibilityHidden(true)
    }

    private func pinnedCell(_ entry: Entry) -> some View {
        HStack(spacing: Spacing.xs) {
            Text(Formatting.shortGameDate(entry.row.date))
                .hardwoodText(.tableCell, color: Palette.textSecondary, monospacedDigits: true)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            Spacer(minLength: Spacing.xxs)
            Text(entry.row.matchupText)
                .hardwoodText(.tableCell, monospacedDigits: false)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            Text(resultLetter(entry.row.result))
                .font(Typography.tableHeaderMono)
                .foregroundStyle(resultColor(entry.row.result))
                .frame(minWidth: 12, alignment: .trailing)
        }
        .frame(height: rowHeight)
        .background(stripe(entry))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText(entry))
    }

    private func valueCell(_ entry: Entry, column: Column) -> some View {
        let isBest = isSeasonBest(entry.row, column: column)
        return Text(valueText(entry.row, column: column))
            .availabilityStyled(cellAvailability(entry.row, column: column))
            .font(Typography.tableCellMono)
            .fontWeight(isBest ? .semibold : .regular)
            .foregroundStyle(cellColor(entry.row, column: column, isBest: isBest))
            .lineLimit(1)
            .minimumScaleFactor(0.7)
            .frame(width: columnWidth, height: rowHeight, alignment: .trailing)
            .background(bestHighlight(isBest))
    }

    @ViewBuilder private func bestHighlight(_ isBest: Bool) -> some View {
        if isBest {
            RoundedRectangle(cornerRadius: Radius.chip, style: .continuous)
                .fill(Palette.selection.opacity(0.14))
                .padding(.vertical, 2)
        } else {
            Color.clear
        }
    }

    @ViewBuilder private func stripe(_ entry: Entry) -> some View {
        if entry.index.isMultiple(of: 2) {
            Color.clear
        } else {
            Palette.surfaceSunken
        }
    }

    // MARK: Stacked

    private var stackedLog: some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            ForEach(entries) { entry in
                VStack(alignment: .leading, spacing: Spacing.xs) {
                    HStack(spacing: Spacing.xs) {
                        Text(Formatting.mediumGameDate(entry.row.date))
                            .hardwoodText(.widgetTitle)
                        Text(entry.row.matchupText)
                            .hardwoodText(.tableCell, color: Palette.textSecondary)
                        Spacer(minLength: Spacing.xs)
                        Text(resultText(entry.row))
                            .hardwoodText(.tableCell, color: resultColor(entry.row.result))
                    }
                    ForEach(columns) { column in
                        HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                            Text(column.descriptor.shortName)
                                .hardwoodText(.statLabel)
                            Spacer(minLength: Spacing.xs)
                            Text(valueText(entry.row, column: column))
                                .availabilityStyled(cellAvailability(entry.row, column: column))
                                .font(Typography.tableCellMono)
                                .fontWeight(isSeasonBest(entry.row, column: column) ? .semibold : .regular)
                                .foregroundStyle(cellColor(entry.row,
                                                           column: column,
                                                           isBest: isSeasonBest(entry.row, column: column)))
                        }
                    }
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(accessibilityText(entry))
            }
        }
    }

    // MARK: Values

    /// The raw number for a column, falling back to the row's own minutes when the server did
    /// not repeat them inside `values`.
    private func rawValue(_ row: GameLogPayloadRow, key: String) -> Double? {
        if let value = row.value(key) { return value }
        if key == "min" { return row.minutes }
        return nil
    }

    private func valueText(_ row: GameLogPayloadRow, column: Column) -> String {
        Formatting.value(rawValue(row, key: column.descriptor.key), format: column.descriptor.format)
    }

    /// A cell with no number is `unavailable` whatever the row says: there is nothing to dress up.
    private func cellAvailability(_ row: GameLogPayloadRow, column: Column) -> MetricAvailability {
        rawValue(row, key: column.descriptor.key) == nil ? .unavailable : row.availability
    }

    private func isSeasonBest(_ row: GameLogPayloadRow, column: Column) -> Bool {
        guard let best = payload.seasonBest(column.descriptor.key),
              let value = rawValue(row, key: column.descriptor.key),
              best.isFinite, value.isFinite else { return false }
        return abs(best - value) <= 1e-9
    }

    private func cellColor(_ row: GameLogPayloadRow, column: Column, isBest: Bool) -> Color {
        if rawValue(row, key: column.descriptor.key) == nil { return Palette.textTertiary }
        return isBest ? Palette.selection : Palette.textPrimary
    }

    private func resultLetter(_ result: String?) -> String {
        guard let result = result, !result.isEmpty else { return "" }
        return String(result.prefix(1)).uppercased()
    }

    private func resultColor(_ result: String?) -> Color {
        switch resultLetter(result) {
        case "W": return Palette.positive
        case "L": return Palette.negative
        default:  return Palette.textTertiary
        }
    }

    private func resultText(_ row: GameLogPayloadRow) -> String {
        let letter = resultLetter(row.result)
        guard let score = row.score, !score.isEmpty else {
            return letter.isEmpty ? Formatting.emDash : letter
        }
        return letter.isEmpty ? score : "\(letter) \(score)"
    }

    private func accessibilityText(_ entry: Entry) -> String {
        var parts: [String] = [Formatting.mediumGameDate(entry.row.date), entry.row.matchupText]
        let result = resultLetter(entry.row.result)
        if result == "W" {
            parts.append("win")
        } else if result == "L" {
            parts.append("loss")
        }
        if let score = entry.row.score, !score.isEmpty { parts.append(score) }
        for column in columns {
            if rawValue(entry.row, key: column.descriptor.key) == nil {
                parts.append("\(column.descriptor.name) not available")
            } else {
                var text = "\(column.descriptor.name) \(valueText(entry.row, column: column))"
                if isSeasonBest(entry.row, column: column) { text += ", season best" }
                parts.append(text)
            }
        }
        return parts.joined(separator: ", ")
    }
}

#if DEBUG
#Preview("Game log") {
    ScrollView {
        VStack(spacing: Spacing.md) {
            GameLogWidget(payload: .preview, size: .medium)
                .hardwoodCard()
            GameLogWidget(payload: .preview, size: .large)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}

#Preview("Game log, accessibility size") {
    ScrollView {
        GameLogWidget(payload: .preview, size: .large)
            .hardwoodCard()
            .padding(Spacing.lg)
    }
    .hardwoodBackground()
    .environment(\.dynamicTypeSize, .accessibility2)
}
#endif
