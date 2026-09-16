import Foundation
import SwiftUI

/// The Fantasy Board: tonight's projections, each drawn as a range.
///
/// Artboard 2b of the `Hardwood Predictions` handoff. One thing in the design is deliberately
/// absent and its absence is the most important property of this file: **there is no book line,
/// no price, no edge and no over/under.** The tick on every bar is the player's own season
/// average. `docs/BROADSHEET.md` §1 gives the two reasons, either of which is sufficient.
///
/// The widget renders in the broadsheet language whenever it is on a broadsheet page, and falls
/// back to the app's card typography elsewhere — a reader who drags this tile onto an ordinary
/// dashboard should get a tile, not a serif island.
public struct ProjectionBoardWidget: View {

    private let payload: ProjectionBoardPayload
    private let size: WidgetSize

    @Environment(\.isBroadsheet) private var isBroadsheet
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    public init(payload: ProjectionBoardPayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Derived

    /// The board is a `large`-only kind in the catalog, but a stored layout can still ask for a
    /// smaller one, and a bar squeezed into a small tile is unreadable rather than merely tight.
    private var rowLimit: Int {
        switch size {
        case .small:  return 2
        case .medium: return 4
        case .large:  return 8
        }
    }

    private var rows: [ProjectionBoardRow] {
        Array(payload.rows.prefix(max(rowLimit, 0)))
    }

    /// At accessibility text sizes the matchup and the metric stop fitting on one line with the
    /// name, so the row head stacks instead of compressing to illegibility.
    private var isStacked: Bool { dynamicTypeSize.isAccessibilitySize }

    private var contextLine: String {
        [payload.dateHeadline, payload.gameCountText]
            .filter { !$0.isEmpty && $0 != Formatting.emDash }
            .joined(separator: " · ")
    }

    /// Said out loud because it is not obvious from the board: the players were chosen from a
    /// slate that has already been played, and these are the games they play next.
    private var selectionNote: String? {
        guard let selectionDate = payload.selectionDate, !selectionDate.isEmpty,
              selectionDate != payload.date else { return nil }
        return "Players chosen from the \(Formatting.shortGameDate(selectionDate)) slate."
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: isBroadsheet ? Spacing.md : Spacing.sm) {
            header
            if rows.isEmpty {
                emptyNote
            } else {
                board
            }
            footnotes
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Header

    @ViewBuilder private var header: some View {
        if isBroadsheet {
            // No title here: on a broadsheet page the container has already set the name as a
            // kicker above this view, and repeating it would be the one thing an editorial
            // column cannot afford — the same words twice in two sizes.
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                Text(contextLine)
                    .font(Broadsheet.figures(13))
                    .foregroundStyle(Broadsheet.text)
                BroadsheetRule(weight: 1)
                    .padding(.top, Spacing.xxs)
            }
        } else {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                Text("Tonight's Projections")
                    .hardwoodText(.widgetTitle)
                    .lineLimit(1)
                Text(contextLine)
                    .hardwoodText(.caption)
                    .lineLimit(1)
            }
        }
    }

    // MARK: Board

    private var board: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(rows) { row in
                VStack(alignment: .leading, spacing: Spacing.xs) {
                    rowHead(row)
                    bar(row)
                }
                .padding(.vertical, isBroadsheet ? Spacing.sm : Spacing.xs)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(rowAccessibilityLabel(row))
                if isBroadsheet, row.id != rows.last?.id {
                    BroadsheetRule()
                }
            }
        }
    }

    @ViewBuilder private func rowHead(_ row: ProjectionBoardRow) -> some View {
        if isStacked {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                name(row)
                HStack(spacing: Spacing.xs) {
                    matchup(row)
                    Spacer(minLength: 0)
                    metricAndValue(row)
                }
            }
        } else {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                name(row)
                matchup(row)
                Spacer(minLength: Spacing.xs)
                metricAndValue(row)
            }
        }
    }

    /// The roles this widget sets type in, so each one is chosen in a single place rather than
    /// with a `isBroadsheet ? … : …` at every call site.
    private enum Role {
        case name, matchup, metricLabel, value, delta, note, empty
    }

    private func font(_ role: Role) -> Font {
        if !isBroadsheet {
            switch role {
            case .name:        return HardwoodTextStyle.tableCell.font
            case .matchup:     return HardwoodTextStyle.caption.monospacedDigitFont
            case .metricLabel: return HardwoodTextStyle.tableHeader.font
            case .value:       return HardwoodTextStyle.statValue.font
            case .delta:       return HardwoodTextStyle.caption.monospacedDigitFont
            case .note:        return HardwoodTextStyle.caption.font
            case .empty:       return HardwoodTextStyle.caption.font
            }
        }
        switch role {
        case .name:        return Broadsheet.serif(15, weight: .semibold)
        case .matchup:     return Broadsheet.figures(12)
        case .metricLabel: return Broadsheet.serif(11, weight: .semibold)
        case .value:       return Broadsheet.figures(17, weight: .semibold)
        case .delta:       return Broadsheet.figures(12)
        case .note:        return Broadsheet.serif(11)
        case .empty:       return Broadsheet.serif(13)
        }
    }

    private func color(_ role: Role) -> Color {
        if !isBroadsheet {
            switch role {
            case .name, .value:            return Palette.textPrimary
            case .matchup, .empty:         return Palette.textSecondary
            case .metricLabel, .note:      return Palette.textTertiary
            case .delta:                   return Palette.textSecondary
            }
        }
        switch role {
        case .name, .value:                              return Broadsheet.text
        case .matchup, .metricLabel, .note, .empty, .delta: return Broadsheet.textMuted
        }
    }

    private func name(_ row: ProjectionBoardRow) -> some View {
        Text(row.player.name)
            .font(font(.name))
            .foregroundStyle(color(.name))
            .lineLimit(1)
            .minimumScaleFactor(0.85)
    }

    @ViewBuilder private func matchup(_ row: ProjectionBoardRow) -> some View {
        if let matchup = row.matchup {
            Text(matchup)
                .font(font(.matchup))
                .foregroundStyle(color(.matchup))
                .lineLimit(1)
        }
    }

    private func metricAndValue(_ row: ProjectionBoardRow) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
            Text(row.metricLabel)
                .font(font(.metricLabel))
                .tracking(isBroadsheet ? Broadsheet.kickerTracking : 0.4)
                .foregroundStyle(color(.metricLabel))
            Text(row.displayValue)
                .font(font(.value))
                .foregroundStyle(color(.value))
            if let deltaText = row.deltaText {
                Text(deltaText)
                    .font(font(.delta))
                    .foregroundStyle(deltaColor(row))
            }
        }
    }

    @ViewBuilder private func bar(_ row: ProjectionBoardRow) -> some View {
        if let model = barModel(row) {
            LabelledRangeBar(
                model: model,
                accent: deltaColor(row),
                lowText: row.lowText,
                highText: row.highText,
                referenceLabel: payload.hasReference ? payload.referenceLabel : nil,
                referenceText: payload.hasReference ? row.referenceText : nil,
                projectionText: row.displayValue
            )
        } else {
            // §7 rule 1 of docs/PROJECTION.md: never a mean without its interval. With no
            // interval there is no bar to draw, and saying so beats drawing an empty one.
            Text("No interval for this projection.")
                .font(font(.note))
                .foregroundStyle(color(.note))
        }
    }

    private func barModel(_ row: ProjectionBoardRow) -> RangeBar.Model? {
        guard let low = row.low, let high = row.high, let projection = row.projection else {
            return nil
        }
        let model = RangeBar.Model(low: low,
                                   high: high,
                                   projection: projection,
                                   reference: payload.hasReference ? row.referenceValue : nil)
        return model.isDrawable ? model : nil
    }

    private func deltaColor(_ row: ProjectionBoardRow) -> Color {
        isBroadsheet
            ? Broadsheet.accent(isAbove: row.isAboveReference)
            : Palette.value(for: row.delta, higherIsBetter: true)
    }

    // MARK: Footnotes

    @ViewBuilder private var footnotes: some View {
        let lines = [payload.note, selectionNote].compactMap { $0 }
        if !lines.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                if isBroadsheet {
                    BroadsheetRule(weight: 1)
                        .padding(.bottom, Spacing.xxs)
                }
                ForEach(lines, id: \.self) { line in
                    Text(line)
                        .font(font(.note))
                        .foregroundStyle(color(.note))
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var emptyNote: some View {
        Text("No projection cleared tonight's filters.")
            .font(font(.empty))
            .foregroundStyle(color(.empty))
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Accessibility

    /// One sentence per row, because a bar is a picture and VoiceOver reads sentences.
    ///
    /// It names the interval explicitly rather than describing the bar's geometry: "between 19
    /// and 38" is the fact; "a band from a third of the way along" is not.
    private func rowAccessibilityLabel(_ row: ProjectionBoardRow) -> String {
        var parts: [String] = [row.player.name]
        if let matchup = row.matchup { parts.append(matchup) }
        let metricName = row.descriptor?.name ?? row.metricLabel
        parts.append("\(metricName) projected \(row.displayValue)")
        if row.low != nil || row.high != nil {
            parts.append("likely between \(row.lowText) and \(row.highText)")
        }
        if payload.hasReference,
           let referenceText = row.referenceText,
           let label = payload.referenceLabel {
            parts.append("\(label) \(referenceText)")
        }
        if let deltaText = row.deltaText {
            parts.append("difference \(deltaText)")
        }
        parts.append("estimated, not recorded")
        return parts.joined(separator: ", ")
    }
}

#if DEBUG
#Preview("Broadsheet") {
    ScrollView {
        ProjectionBoardWidget(payload: .preview, size: .large)
            .padding(Spacing.lg)
    }
    .broadsheet()
    .broadsheetBackground()
}

#Preview("On an ordinary tile") {
    ScrollView {
        ProjectionBoardWidget(payload: .preview, size: .large)
            .padding(Spacing.md)
            .hardwoodCard()
            .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
