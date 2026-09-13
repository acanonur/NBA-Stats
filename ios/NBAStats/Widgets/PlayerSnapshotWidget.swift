import Foundation
import SwiftUI

/// A player's season at a glance: who they are, how much they played, and where each of their
/// rate stats sits against the league.
///
/// The percentile bar is the point of the widget — a raw 28.1% usage rate means nothing on its
/// own — so a metric with no percentile still shows its value and simply leaves the bar empty
/// rather than inventing a position for it.
public struct PlayerSnapshotWidget: View {

    private struct Entry: Identifiable {
        let index: Int
        let value: MetricValue
        let descriptor: MetricDescriptor?

        var id: Int { index }
        var label: String { descriptor?.shortName ?? Entry.fallbackShortName(value.metric) }
        var name: String { descriptor?.name ?? value.metric }
        /// `nil` when the catalog does not know this metric. The direction is genuinely unknown
        /// then, and assuming "higher is better" paints a confident green bar on a
        /// worse-than-league value for any inverted metric a newer server adds.
        var higherIsBetter: Bool? { descriptor?.higherIsBetter }

        /// A readable label for a metric key the bundled catalog does not know: `"ts_pct"`
        /// becomes `"TS%"`, `"off_rtg"` becomes `"OFF RTG"`.
        static func fallbackShortName(_ key: String) -> String {
            let separator: Character = "_"
            var parts = key.split(separator: separator).map(String.init)
            var suffix = ""
            if parts.last == "pct" {
                parts.removeLast()
                suffix = "%"
            }
            let body = parts.map { $0.uppercased() }.joined(separator: " ")
            return body.isEmpty ? key.uppercased() : body + suffix
        }
    }

    private let payload: PlayerSnapshotPayload
    private let size: WidgetSize

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @ScaledMetric(relativeTo: .caption) private var labelWidth: CGFloat = 56
    @ScaledMetric(relativeTo: .footnote) private var valueWidth: CGFloat = 62

    public init(payload: PlayerSnapshotPayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Density

    private var metricLimit: Int {
        switch size {
        case .small:  return 3
        case .medium: return 6
        case .large:  return payload.metrics.count
        }
    }

    private var isStacked: Bool {
        dynamicTypeSize.isAccessibilitySize
    }

    private var subtitle: String {
        var parts: [String] = []
        let context = Formatting.seasonContext(season: payload.season, seasonType: payload.seasonType)
        if !context.isEmpty { parts.append(context) }
        if let team = payload.teamAbbr, !team.isEmpty, team != payload.player.teamAbbr {
            parts.append(team)
        }
        return parts.joined(separator: " · ")
    }

    private var usageText: String {
        let parts = [
            "\(Formatting.integer(payload.gp)) GP",
            "\(Formatting.integer(payload.gs)) GS",
            "\(Formatting.decimal(payload.minutesPerGame, places: 1)) MPG"
        ]
        return parts.joined(separator: "  ·  ")
    }

    private var usageAccessibilityText: String {
        let games = payload.gp.map { "\($0) games played" } ?? "games played not recorded"
        let starts = payload.gs.map { "\($0) started" } ?? "games started not recorded"
        let minutes = payload.minutesPerGame
            .map { "\(Formatting.decimal($0, places: 1)) minutes per game" } ?? "minutes not recorded"
        return "\(games), \(starts), \(minutes)"
    }

    // MARK: Catalog

    @MainActor private func resolveEntries() -> [Entry] {
        let catalog = Catalog.shared
        return payload.metrics.prefix(max(metricLimit, 0)).enumerated().map { pair in
            Entry(index: pair.offset,
                  value: pair.element,
                  descriptor: catalog.metric(pair.element.metric))
        }
    }

    // MARK: Body

    public var body: some View {
        let entries = resolveEntries()
        VStack(alignment: .leading, spacing: Spacing.sm) {
            PlayerRow(player: payload.player, subtitle: subtitle)
            usageLine
            rule
            slate(entries)
            if let note = payload.eraNote, !note.isEmpty {
                eraNote(note)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Pieces

    private var usageLine: some View {
        Text(usageText)
            .hardwoodText(.statLabel, monospacedDigits: true)
            .lineLimit(2)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityLabel(usageAccessibilityText)
    }

    private var rule: some View {
        Rectangle()
            .fill(Palette.separator)
            .frame(height: 1)
            .accessibilityHidden(true)
    }

    @ViewBuilder private func slate(_ entries: [Entry]) -> some View {
        if entries.isEmpty {
            Text("No metrics are available for this season.")
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            // A season the server sent no percentiles for gets no empty tracks: the bars are
            // dropped entirely rather than drawn as a column of blanks.
            let showsBars = size != .small && entries.contains(where: { $0.value.percentile != nil })
            VStack(alignment: .leading, spacing: isStacked ? Spacing.md : Spacing.sm) {
                ForEach(entries) { entry in
                    metricRow(entry, showsBars: showsBars)
                }
            }
        }
    }

    @ViewBuilder private func metricRow(_ entry: Entry, showsBars: Bool) -> some View {
        Group {
            if isStacked {
                VStack(alignment: .leading, spacing: Spacing.xs) {
                    HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                        Text(entry.label)
                            .hardwoodText(.statLabel)
                        Spacer(minLength: Spacing.xs)
                        valueText(entry)
                    }
                    if showsBars {
                        bar(entry)
                    }
                }
            } else {
                HStack(alignment: .center, spacing: Spacing.sm) {
                    Text(entry.label)
                        .hardwoodText(.statLabel)
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                        .frame(width: labelWidth, alignment: .leading)
                    valueText(entry)
                        .frame(width: valueWidth, alignment: .trailing)
                    if showsBars {
                        bar(entry)
                    } else {
                        Spacer(minLength: 0)
                    }
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText(entry))
    }

    private func valueText(_ entry: Entry) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.xxs) {
            Text(entry.value.displayValue.isEmpty ? Formatting.emDash : entry.value.displayValue)
                .availabilityStyled(entry.value.availability)
                .hardwoodText(.tableCell,
                              color: entry.value.availability == .unavailable
                                  ? Palette.textTertiary
                                  : Palette.textPrimary,
                              monospacedDigits: true)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            AvailabilityBadge(availability: entry.value.availability,
                              showsText: false,
                              isInteractive: false,
                              metricName: entry.name,
                              season: payload.season)
        }
    }

    private func bar(_ entry: Entry) -> some View {
        PercentileBar(percentile: entry.value.percentile,
                      tint: entry.higherIsBetter.map { direction in
                          Palette.value(entry.value.value,
                                        comparedTo: entry.value.leagueAverage,
                                        higherIsBetter: direction)
                      } ?? Palette.neutral,
                      showsLabel: size == .large && !isStacked,
                      trackHeight: 7)
    }

    private func eraNote(_ note: String) -> some View {
        HStack(alignment: .top, spacing: Spacing.xs) {
            Image(systemName: "clock.arrow.circlepath")
                .imageScale(.small)
                .foregroundStyle(Palette.warning)
                .accessibilityHidden(true)
            Text(note)
                .hardwoodText(.caption, color: Palette.warning)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }

    private func accessibilityText(_ entry: Entry) -> String {
        var parts: [String] = [entry.name]
        if entry.value.availability == .unavailable {
            parts.append("not available in this era")
        } else {
            parts.append(entry.value.displayValue.isEmpty ? Formatting.emDash : entry.value.displayValue)
        }
        if let percentile = entry.value.percentile, percentile.isFinite {
            parts.append("\(Formatting.percentile(percentile)) percentile")
        }
        if let rank = entry.value.rank, rank > 0 {
            parts.append("ranked \(Formatting.ordinal(rank))")
        }
        if entry.value.availability == .estimated || entry.value.availability == .partial {
            parts.append(entry.value.availability.hardwoodShortLabel)
        }
        return parts.joined(separator: ", ")
    }
}

#if DEBUG
#Preview("Player snapshot") {
    ScrollView {
        VStack(spacing: Spacing.md) {
            PlayerSnapshotWidget(payload: .preview, size: .medium)
                .hardwoodCard()
            PlayerSnapshotWidget(payload: .preview, size: .large)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}

#Preview("Player snapshot, 1962-63") {
    let payload = PlayerSnapshotPayload(
        player: PlayerRef(playerId: 76375, name: "Elgin Baylor", firstName: "Elgin", lastName: "Baylor",
                          teamId: 1_610_612_747, teamAbbr: "LAL", position: "F", jersey: "22",
                          headshotUrl: nil, isActive: false),
        season: "1962-63",
        seasonType: "Regular Season",
        teamAbbr: "LAL",
        gp: 80,
        gs: nil,
        minutesPerGame: 43.5,
        metrics: [
            MetricValue(metric: "pts", value: 34.0, displayValue: "34.0", rank: 2, percentile: 0.99, leagueAverage: 12.4, availability: .full),
            MetricValue(metric: "ts_pct", value: 0.531, displayValue: "53.1%", rank: 18, percentile: 0.74, leagueAverage: 0.482, availability: .estimated),
            MetricValue(metric: "usg_pct", value: nil, displayValue: Formatting.emDash, availability: .unavailable),
            MetricValue(metric: "stl", value: nil, displayValue: Formatting.emDash, availability: .unavailable)
        ],
        eraNote: "Individual turnovers and steals were not recorded until the 1970s, so usage and steal rates cannot be computed for this season."
    )
    return PlayerSnapshotWidget(payload: payload, size: .large)
        .hardwoodCard()
        .padding(Spacing.lg)
        .hardwoodBackground()
}
#endif
