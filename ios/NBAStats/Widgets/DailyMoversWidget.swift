import Foundation
import SwiftUI

/// The widget that answers "what happened last night".
///
/// A raw 38.2 Game Score means nothing to most readers, and even a rank only says who was best.
/// What makes a night remarkable is the distance from the player's own normal, so the delta
/// against their season average is the largest, boldest thing in every row, and the absolute
/// value sits underneath it as the supporting detail.
public struct DailyMoversWidget: View {

    private let payload: DailyMoversPayload
    private let size: WidgetSize

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    public init(payload: DailyMoversPayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Density

    private var rowLimit: Int {
        switch size {
        case .small:  return 2
        case .medium: return 3
        case .large:  return 5
        }
    }

    private var rows: [DailyMoverRow] {
        Array(payload.rows.prefix(max(rowLimit, 0)))
    }

    private var isStacked: Bool { dynamicTypeSize.isAccessibilitySize }

    private var showsSeasonAverage: Bool { size == .large }

    private var directionLabel: String {
        switch payload.direction {
        case "worst":    return "Toughest nights"
        case "surprise": return "Biggest surprises"
        default:         return "Biggest nights"
        }
    }

    private var contextLine: String {
        [Formatting.mediumGameDate(payload.date), payload.metric.name]
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            if rows.isEmpty {
                emptyNote
            } else {
                VStack(alignment: .leading, spacing: Spacing.sm) {
                    ForEach(rows) { row in
                        moverRow(row)
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Pieces

    private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            Text(directionLabel)
                .hardwoodText(.widgetTitle)
                .lineLimit(1)
            Text(contextLine)
                .hardwoodText(.caption)
                .lineLimit(1)
        }
        .accessibilityElement(children: .combine)
    }

    private var emptyNote: some View {
        HStack(spacing: Spacing.xs) {
            Image(systemName: "moon.zzz")
                .imageScale(.small)
                .foregroundStyle(Palette.textTertiary)
                .accessibilityHidden(true)
            Text("Nobody played on this date.")
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private func moverRow(_ row: DailyMoverRow) -> some View {
        Group {
            if isStacked {
                VStack(alignment: .leading, spacing: Spacing.xs) {
                    identity(row)
                    HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                        deltaBlock(row, alignment: .leading)
                        Spacer(minLength: Spacing.xs)
                        valueBlock(row, alignment: .trailing)
                    }
                }
            } else {
                HStack(alignment: .center, spacing: Spacing.sm) {
                    rankLabel(row.rank)
                    identity(row)
                    Spacer(minLength: Spacing.xs)
                    valueBlock(row, alignment: .trailing)
                    deltaBlock(row, alignment: .trailing)
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText(row))
    }

    private func rankLabel(_ rank: Int) -> some View {
        Text(rank > 0 ? "\(rank)" : Formatting.emDash)
            .font(Typography.tableCellMono)
            .foregroundStyle(Palette.textTertiary)
            .frame(minWidth: 18, alignment: .trailing)
    }

    private func identity(_ row: DailyMoverRow) -> some View {
        HStack(spacing: Spacing.sm) {
            // This widget shows two to five rows, and the value and delta blocks opposite are
            // each two or three lines, so the row is already about 44pt tall: the portrait costs
            // no height here, unlike the single-line leaderboard rows.
            // Decorative: `accessibilityText(_:)` reads the name.
            PlayerAvatar(player: row.player, size: .medium)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: Spacing.xs) {
                    if let abbreviation = row.player.teamAbbr, !abbreviation.isEmpty {
                        TeamBadge(abbreviation: abbreviation, size: .small)
                    }
                    Text(row.player.name)
                        .hardwoodText(.widgetTitle)
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                }
                Text(subtitle(row))
                    .hardwoodText(.caption, monospacedDigits: true)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
        }
    }

    /// `"vs BOS · W · 46 PTS · 9 AST"` — the matchup first, then the server's own line.
    private func subtitle(_ row: DailyMoverRow) -> String {
        var parts: [String] = [row.matchupText]
        if let result = row.result, !result.isEmpty {
            parts.append(String(result.prefix(1)).uppercased())
        }
        if let line = row.line, !line.isEmpty {
            parts.append(line)
        }
        return parts.joined(separator: " · ")
    }

    private func valueBlock(_ row: DailyMoverRow, alignment: HorizontalAlignment) -> some View {
        VStack(alignment: alignment, spacing: 0) {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.xxs) {
                Text(row.value.displayValue.isEmpty ? Formatting.emDash : row.value.displayValue)
                    .availabilityStyled(row.value.availability)
                    .font(Typography.tableCellMono)
                    .foregroundStyle(row.value.availability == .unavailable
                                     ? Palette.textTertiary
                                     : Palette.textPrimary)
                    .lineLimit(1)
                AvailabilityBadge(availability: row.value.availability,
                                  showsText: false,
                                  isInteractive: false,
                                  metricName: payload.metric.name)
            }
            Text(payload.metric.shortName)
                .hardwoodText(.tableHeader)
                .lineLimit(1)
            if showsSeasonAverage {
                Text("avg \(Formatting.value(row.seasonAverage, format: payload.metric.format))")
                    .hardwoodText(.caption, monospacedDigits: true)
                    .lineLimit(1)
            }
        }
    }

    /// The anchor: how far above or below this player's own season average the night was.
    private func deltaBlock(_ row: DailyMoverRow, alignment: HorizontalAlignment) -> some View {
        let delta = resolvedDelta(row)
        let tint = Palette.value(for: delta, higherIsBetter: payload.metric.higherIsBetter)
        return VStack(alignment: alignment, spacing: 0) {
            HStack(spacing: 1) {
                if let delta = delta, abs(delta) > 1e-9 {
                    Image(systemName: delta > 0 ? "arrow.up.right" : "arrow.down.right")
                        .font(Typography.statValue)
                        .accessibilityHidden(true)
                }
                Text(deltaText(delta))
                    .font(Typography.statValueMono)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
            .foregroundStyle(tint)
            Text("vs avg")
                .hardwoodText(.tableHeader)
                .lineLimit(1)
        }
        .padding(.horizontal, Spacing.xs)
        .padding(.vertical, Spacing.xxs)
        .background(
            RoundedRectangle(cornerRadius: Radius.chip, style: .continuous)
                .fill(tint.opacity(0.12))
        )
    }

    // MARK: Values

    /// The server's delta, or the difference it implies. Never a zero standing in for "unknown".
    private func resolvedDelta(_ row: DailyMoverRow) -> Double? {
        if let delta = row.delta, delta.isFinite { return delta }
        guard let value = row.value.value, let average = row.seasonAverage,
              value.isFinite, average.isFinite else { return nil }
        return value - average
    }

    private func deltaText(_ delta: Double?) -> String {
        guard let delta = delta else { return Formatting.emDash }
        // The arrow and the tint beside this carry the direction, so only the size goes here.
        return HardwoodNumberFormat.magnitude(delta, format: payload.metric.format)
    }

    private func accessibilityText(_ row: DailyMoverRow) -> String {
        var parts: [String] = []
        if row.rank > 0 { parts.append("number \(row.rank)") }
        parts.append(row.player.name)
        parts.append(row.matchupText)
        if let line = row.line, !line.isEmpty { parts.append(line) }
        let value = row.value.availability == .unavailable
            ? "not available"
            : (row.value.displayValue.isEmpty ? Formatting.emDash : row.value.displayValue)
        parts.append("\(payload.metric.name) \(value)")
        if let delta = resolvedDelta(row) {
            let direction = delta > 0 ? "above" : "below"
            let magnitude = HardwoodNumberFormat.magnitude(delta, format: payload.metric.format)
            if let average = row.seasonAverage {
                let averageText = Formatting.value(average, format: payload.metric.format)
                parts.append("\(magnitude) \(direction) their season average of \(averageText)")
            } else {
                parts.append("\(magnitude) \(direction) their season average")
            }
        }
        if row.value.availability != .full {
            parts.append(row.value.availability.hardwoodShortLabel)
        }
        return parts.joined(separator: ", ")
    }
}

#if DEBUG
#Preview("Daily movers") {
    ScrollView {
        VStack(spacing: Spacing.md) {
            DailyMoversWidget(payload: .preview, size: .small)
                .hardwoodCard()
            DailyMoversWidget(payload: .preview, size: .large)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}

#Preview("Daily movers, worst nights") {
    let payload = DailyMoversPayload(
        date: "2026-01-02",
        metric: .previewImpact,
        direction: "worst",
        rows: [
            DailyMoverRow(rank: 1, player: .preview, gameId: "0022500512", opponentAbbr: "BOS",
                          isHome: true, result: "L", line: "6 PTS · 2 AST",
                          value: MetricValue(metric: "game_score", value: 2.1, displayValue: "2.1",
                                             rank: 1, percentile: 0.02, leagueAverage: 9.8),
                          seasonAverage: 21.7, delta: -19.6)
        ]
    )
    return DailyMoversWidget(payload: payload, size: .medium)
        .hardwoodCard()
        .padding(Spacing.lg)
        .hardwoodBackground()
}
#endif
