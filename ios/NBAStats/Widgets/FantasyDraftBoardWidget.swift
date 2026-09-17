import Foundation
import SwiftUI

/// The fantasy draft board, as a table.
///
/// A draft kit is a spreadsheet — you scan down a rank column and across a stat line — so this
/// follows `GameLogWidget`'s shape rather than inventing a third table idiom: a pinned rank and
/// name block on the leading edge, the value columns scrolling horizontally under a header that
/// stays put, and the whole table abandoned for stacked blocks at accessibility text sizes.
///
/// Three things it does **not** copy from the game log, each because that file gets them wrong:
///
/// * **It does not rubber-band when there is nothing to scroll.** `.scrollBounceBehavior`
///   is gated on content size, so a narrow strip that already fits sits still.
/// * **It does not hide the numbers from VoiceOver.** The game log marks its whole scroll view
///   `accessibilityHidden` and re-speaks a summary; here the row label carries the columns that
///   actually moved, so the table is navigable rather than merely announced.
/// * **The pinned block shrinks as type grows instead of growing with it.** A `@ScaledMetric`
///   name column widens at XXL until the scrolling window collapses; this caps it against the
///   measured container so the columns keep their room.
///
/// Columns arrive as data (`payload.columns`), so the server owns the layout and adding one
/// needs no app release. See `docs/FANTASY.md`.
public struct FantasyDraftBoardWidget: View {

    private let payload: FantasyDraftBoardPayload
    private let size: WidgetSize

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    /// 26pt at default type, scaled against `.footnote` because that is what `.tableCell` maps
    /// to — the box grows at exactly the rate of the text inside it.
    @ScaledMetric(relativeTo: .footnote) private var rowHeight: CGFloat = 26
    @ScaledMetric(relativeTo: .footnote) private var rankWidth: CGFloat = 26
    @ScaledMetric(relativeTo: .footnote) private var nameWidth: CGFloat = 100
    @ScaledMetric(relativeTo: .footnote) private var columnWidth: CGFloat = 46

    /// The name column may not eat the table. Above this share of the container it stops
    /// growing, which is the difference between a tight table at XXL and no table at all.
    private static let maxNameShare: CGFloat = 0.29

    @State private var group: String?

    public init(payload: FantasyDraftBoardPayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Density

    private var isStacked: Bool { dynamicTypeSize.isAccessibilitySize }

    /// Row counts that fit `WidgetSize.estimatedHeight` once the header and footer are paid
    /// for, and drop again at the largest non-accessibility sizes where each row is taller.
    private var rowLimit: Int {
        let tight = dynamicTypeSize >= .xxLarge
        switch size {
        case .small:  return tight ? 2 : 3
        case .medium: return tight ? 3 : 4
        case .large:  return tight ? 6 : 8
        }
    }

    private var rows: [FantasyDraftRow] {
        Array(payload.rows.prefix(max(rowLimit, 0)))
    }

    private var activeGroup: String {
        group ?? payload.groups.first ?? "production"
    }

    private var visibleColumns: [FantasyColumn] {
        let inGroup = payload.columns(in: activeGroup)
        return inGroup.isEmpty ? payload.columns : inGroup
    }

    private var contextLine: String {
        [Formatting.seasonDisplay(payload.season), payload.contextText]
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            if payload.rows.isEmpty {
                emptyNote
            } else if isStacked {
                stackedBoard
            } else {
                groupPicker
                table
            }
            footer
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
                Text("On the clock")
                    .hardwoodText(.widgetTitle)
                Text(payload.nextPick.text)
                    .hardwoodText(.caption, monospacedDigits: true)
                Spacer(minLength: 0)
            }
            Text(contextLine)
                .hardwoodText(.caption)
                .lineLimit(1)
        }
    }

    /// Twenty-four columns do not fit a phone, so they are paged in named strips rather than
    /// asked to share one window. A segmented control is the cheapest thing that says
    /// "there is more to the right of this" without a reader having to discover it by flicking.
    @ViewBuilder private var groupPicker: some View {
        if payload.groups.count > 1 {
            Picker("Columns", selection: Binding(
                get: { activeGroup },
                set: { group = $0 }
            )) {
                ForEach(payload.groups, id: \.self) { name in
                    Text(groupLabel(name)).tag(name)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
        }
    }

    private func groupLabel(_ name: String) -> String {
        switch name {
        case "summary": return "Summary"
        case "production": return "Per game"
        case "impact": return "Value"
        default: return name.capitalized
        }
    }

    // MARK: Table

    private var table: some View {
        GeometryReader { proxy in
            let pinned = pinnedWidth(in: proxy.size.width)
            HStack(alignment: .top, spacing: Spacing.sm) {
                // The pinned limb is ordinary content, not a scroll view. It stays in vertical
                // lockstep with the scrolling limb because both emit the same sequence of
                // `rowHeight`-tall boxes — there is no coordinate space and no alignment guide.
                VStack(alignment: .leading, spacing: 0) {
                    pinnedHeader
                    rule
                    ForEach(rows) { row in
                        pinnedCell(row)
                    }
                }
                .frame(width: pinned, alignment: .leading)

                ScrollView(.horizontal, showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 0) {
                        HStack(spacing: 0) {
                            ForEach(visibleColumns) { column in
                                Text(column.label)
                                    .hardwoodText(.tableHeader)
                                    .lineLimit(1)
                                    .minimumScaleFactor(0.7)
                                    .frame(width: columnWidth, height: rowHeight,
                                           alignment: column.isTrailing ? .trailing : .leading)
                                    .opacity(column.punted ? 0.35 : 1)
                            }
                        }
                        scrolledRule
                        ForEach(rows) { row in
                            HStack(spacing: 0) {
                                ForEach(visibleColumns) { column in
                                    cell(column, row)
                                }
                            }
                            .frame(height: rowHeight)
                            .background(stripe(row))
                        }
                    }
                }
                // Nothing to scroll in the summary strip, and a table that bounces when it is
                // already whole reads as broken. Gated on content size rather than disabled.
                .scrollBounceBehavior(.basedOnSize, axes: .horizontal)
            }
        }
        .frame(height: rowHeight * CGFloat(rows.count + 1) + 1)
    }

    private func pinnedWidth(in container: CGFloat) -> CGFloat {
        let available = container > 0 ? container : 345
        let name = min(nameWidth, available * FantasyDraftBoardWidget.maxNameShare)
        return rankWidth + Spacing.xs + name
    }

    private var pinnedHeader: some View {
        HStack(spacing: Spacing.xs) {
            Text("#")
                .hardwoodText(.tableHeader)
                .frame(width: rankWidth, alignment: .trailing)
            Text("Player")
                .hardwoodText(.tableHeader)
            Spacer(minLength: 0)
        }
        .frame(height: rowHeight, alignment: .leading)
    }

    private func pinnedCell(_ row: FantasyDraftRow) -> some View {
        HStack(spacing: Spacing.xs) {
            Text(Formatting.integer(row.rank))
                .hardwoodText(.tableCell, color: Palette.textTertiary, monospacedDigits: true)
                .frame(width: rankWidth, alignment: .trailing)
            Text(row.player?.name ?? Formatting.emDash)
                .hardwoodText(.tableCell)
                .lineLimit(1)
                .truncationMode(.tail)
            Spacer(minLength: 0)
        }
        .frame(height: rowHeight, alignment: .leading)
        .background(stripe(row))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityLabel(row))
    }

    private func cell(_ column: FantasyColumn, _ row: FantasyDraftRow) -> some View {
        Text(text(column, row))
            .hardwoodText(.tableCell, color: color(column, row), monospacedDigits: true)
            .lineLimit(1)
            .minimumScaleFactor(0.7)
            .frame(width: columnWidth, height: rowHeight,
                   alignment: column.isTrailing ? .trailing : .leading)
            .opacity(column.punted ? 0.35 : 1)
    }

    private func text(_ column: FantasyColumn, _ row: FantasyDraftRow) -> String {
        // The one text column. Everything else is a number the column knows how to format.
        if column.key == "team" { return row.team ?? Formatting.emDash }
        return column.text(row.value(column.key))
    }

    private func color(_ column: FantasyColumn, _ row: FantasyDraftRow) -> Color {
        guard !column.punted else { return Palette.textTertiary }
        // Only the z columns are coloured. Colouring a raw per-game number would be claiming a
        // good-or-bad about 12 rebounds that depends entirely on who is reading it.
        guard column.group == "impact", let higherIsBetter = column.higherIsBetter else {
            return Palette.textPrimary
        }
        return Palette.value(for: row.value(column.key), higherIsBetter: higherIsBetter)
    }

    private var rule: some View {
        Rectangle()
            .fill(Palette.separator)
            .frame(height: 1)
            .accessibilityHidden(true)
    }

    /// The same hairline inside the scroll view, which proposes no width — so it is given the
    /// column block's, or it collapses to a stub while the rows beside it stay full width.
    private var scrolledRule: some View {
        Rectangle()
            .fill(Palette.separator)
            .frame(width: columnWidth * CGFloat(max(visibleColumns.count, 1)), height: 1)
            .accessibilityHidden(true)
    }

    private func stripe(_ row: FantasyDraftRow) -> Color {
        row.rank.isMultiple(of: 2) ? Palette.surfaceSunken : Color.clear
    }

    // MARK: Stacked

    /// At 300% text a table is not a table. Each row becomes a block of label/value pairs that
    /// reflow, with no frames and no widths anywhere.
    private var stackedBoard: some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            ForEach(rows) { row in
                VStack(alignment: .leading, spacing: Spacing.xs) {
                    HStack(spacing: Spacing.xs) {
                        Text(Formatting.integer(row.rank))
                            .hardwoodText(.widgetTitle, color: Palette.textTertiary)
                        Text(row.player?.name ?? Formatting.emDash)
                            .hardwoodText(.widgetTitle)
                        Spacer(minLength: 0)
                    }
                    ForEach(visibleColumns) { column in
                        HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                            Text(column.label)
                                .hardwoodText(.statLabel)
                            Spacer(minLength: Spacing.xs)
                            Text(text(column, row))
                                .hardwoodText(.tableCell, color: color(column, row),
                                              monospacedDigits: true)
                        }
                    }
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(accessibilityLabel(row))
            }
        }
    }

    // MARK: Footer

    @ViewBuilder private var footer: some View {
        if size == .large && !payload.rows.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                if let first = rows.first, let reason = first.reason, !reason.isEmpty {
                    Text("\(first.player?.shortName ?? "Top pick"): \(reason).")
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !payload.weakestCategories.isEmpty {
                    Text("Your roster is thinnest at "
                         + payload.weakestCategories.map(FantasyCategory.label).joined(separator: ", ")
                         + ".")
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var emptyNote: some View {
        Text("No players are valued for this season yet.")
            .hardwoodText(.caption)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Accessibility

    /// One sentence per row, naming the columns that actually moved.
    ///
    /// The game log hides its whole numeric half from VoiceOver; here the numbers are in the
    /// label, because a draft board with no numbers spoken is a list of names.
    private func accessibilityLabel(_ row: FantasyDraftRow) -> String {
        var parts: [String] = ["Rank \(row.rank)", row.player?.name ?? "unknown player"]
        if let team = row.team { parts.append(team) }
        if let score = row.value("score") {
            parts.append("value \(Formatting.decimal(score, places: 2, signed: true))")
        }
        let notable = payload.columns(in: "impact")
            .filter { !$0.punted && abs(row.value($0.key) ?? 0) > 1.0 }
            .sorted { abs(row.value($0.key) ?? 0) > abs(row.value($1.key) ?? 0) }
            .prefix(3)
        for column in notable {
            let category = String(column.key.dropFirst(2))
            parts.append(FantasyCategory.changePhrase(category, z: row.value(column.key) ?? 0))
        }
        if let reason = row.reason, !reason.isEmpty { parts.append(reason) }
        parts.append("fantasy value, not a record")
        return parts.joined(separator: ", ")
    }
}

#if DEBUG
#Preview("Draft board") {
    ScrollView {
        VStack(spacing: Spacing.md) {
            FantasyDraftBoardWidget(payload: .preview, size: .large)
                .padding(Spacing.md)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
