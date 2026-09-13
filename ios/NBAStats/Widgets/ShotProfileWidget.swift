import Charts
import Foundation
import SwiftUI

/// `shot_profile` — where the shots come from and how they go in, against the league's own diet.
///
/// Hardwood deliberately does **not** draw a court. A hex map of five aggregated zones is a
/// decorative lie: the payload has five numbers per zone, not a shot chart, and paired bars say
/// exactly what those numbers are. Shot locations begin in 1996-97, so a pre-1997 subject gets
/// the explanation instead of an empty plot.
public struct ShotProfileWidget: View {

    /// One bar: either the subject's number or the league's, for one zone.
    private struct ZoneBar: Identifiable {
        let id: String
        let zoneLabel: String
        let whose: String
        let value: Double
        let color: Color
    }

    private let payload: ShotProfilePayload
    private let size: WidgetSize

    public init(payload: ShotProfilePayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Derived

    private static let subjectSeries = "Subject"
    private static let leagueSeries = "League"

    private var subjectColor: Color { HardwoodChart.seriesColor(at: 0) }
    private var leagueColor: Color { Palette.neutral.opacity(0.55) }

    /// Zones the payload actually carries a share for. A zone with no attempts recorded is not a
    /// zone the subject avoided — it is a zone nobody measured.
    private var shareZones: [ShotZone] {
        payload.zones.filter { zone in
            guard let share = zone.shareOfFga else { return false }
            return share.isFinite
        }
    }

    private var accuracyZones: [ShotZone] {
        payload.zones.filter { zone in
            guard let pct = zone.fgPct else { return false }
            return pct.isFinite
        }
    }

    private var zoneOrder: [String] { payload.zones.map(\.label) }

    private var showsAccuracyChart: Bool {
        switch size {
        case .small, .medium: return false
        case .large: return !accuracyZones.isEmpty
        }
    }

    private var chartHeight: CGFloat {
        let rows = max(payload.zones.count, 1)
        switch size {
        case .small: return CGFloat(rows) * 16 + 24
        case .medium: return CGFloat(rows) * 22 + 26
        case .large: return CGFloat(rows) * 24 + 28
        }
    }

    private var contextText: String {
        Formatting.seasonContext(season: payload.season, seasonType: payload.seasonType)
    }

    /// True when the payload is telling us the era has no shot locations at all.
    private var hasNothingToPlot: Bool {
        shareZones.isEmpty && accuracyZones.isEmpty
    }

    private var shareBars: [ZoneBar] {
        var bars: [ZoneBar] = []
        for zone in shareZones {
            if let share = zone.shareOfFga, share.isFinite {
                bars.append(ZoneBar(id: "s|\(zone.zone)",
                                    zoneLabel: zone.label,
                                    whose: Self.subjectSeries,
                                    value: share,
                                    color: subjectColor))
            }
            if let league = zone.leagueShareOfFga, league.isFinite {
                bars.append(ZoneBar(id: "sl|\(zone.zone)",
                                    zoneLabel: zone.label,
                                    whose: Self.leagueSeries,
                                    value: league,
                                    color: leagueColor))
            }
        }
        return bars
    }

    private var accuracyBars: [ZoneBar] {
        var bars: [ZoneBar] = []
        for zone in accuracyZones {
            if let pct = zone.fgPct, pct.isFinite {
                bars.append(ZoneBar(id: "a|\(zone.zone)",
                                    zoneLabel: zone.label,
                                    whose: Self.subjectSeries,
                                    value: pct,
                                    color: subjectColor))
            }
            if let league = zone.leagueFgPct, league.isFinite {
                bars.append(ZoneBar(id: "al|\(zone.zone)",
                                    zoneLabel: zone.label,
                                    whose: Self.leagueSeries,
                                    value: league,
                                    color: leagueColor))
            }
        }
        return bars
    }

    private var legendItems: [HardwoodChartLegendItem] {
        [
            HardwoodChartLegendItem(id: "subject", label: payload.subject.shortName, color: subjectColor, symbol: .square),
            HardwoodChartLegendItem(id: "league", label: "League", color: leagueColor, symbol: .square)
        ]
    }

    private var footnoteLines: [String] {
        var lines: [String] = []
        if let note = payload.note, !note.isEmpty {
            lines.append(note)
        }
        if !showsAccuracyChart, !accuracyZones.isEmpty {
            lines.append("Accuracy, league in brackets: " + accuracyZones.prefix(3).map { zone -> String in
                let own = HardwoodChart.valueLabel(zone.fgPct, format: .percent1)
                let league = HardwoodChart.valueLabel(zone.leagueFgPct, format: .percent1)
                return "\(zone.label) \(own) [\(league)]"
            }.joined(separator: " · "))
        }
        return lines
    }

    private var accessibilityText: String {
        let zones = shareZones.map { zone in
            "\(zone.label) \(HardwoodChart.valueLabel(zone.shareOfFga, format: .percent1)) of attempts at \(HardwoodChart.valueLabel(zone.fgPct, format: .percent1))"
        }
        return "Shot profile for \(payload.subject.displayName): " + zones.joined(separator: ", ")
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            if hasNothingToPlot {
                eraExplanation
            } else {
                rateChips
                chart(title: "Share of attempts", bars: shareBars, format: .percent1)
                if showsAccuracyChart {
                    chart(title: "Field goal percentage", bars: accuracyBars, format: .percent1)
                }
                HardwoodChartLegend(items: legendItems)
                HardwoodChartFootnote(lines: footnoteLines)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
            Text(payload.subject.displayName)
                .hardwoodText(.statLabel, color: Palette.textPrimary)
                .lineLimit(1)
            Spacer(minLength: Spacing.xs)
            if !contextText.isEmpty {
                Text(contextText)
                    .hardwoodText(.caption)
                    .lineLimit(1)
            }
        }
        .accessibilityElement(children: .combine)
    }

    /// The era case: the note from the server is the content, not a footnote under an empty
    /// chart.
    private var eraExplanation: some View {
        HardwoodChartPlaceholder(icon: "clock.arrow.circlepath",
                                 message: payload.note ?? "Shot locations were not recorded before 1996-97, so there is no zone breakdown for this season.",
                                 detail: contextText.isEmpty ? nil : contextText,
                                 availability: .unavailable,
                                 metricName: "Shot profile",
                                 season: payload.season,
                                 minHeight: 120)
    }

    private var rateChips: some View {
        HStack(spacing: Spacing.md) {
            rateChip(title: "3PA rate", value: payload.threePointRate)
            rateChip(title: "FT rate", value: payload.freeThrowRate)
            Spacer(minLength: 0)
        }
    }

    private func rateChip(title: String, value: Double?) -> some View {
        HStack(spacing: Spacing.xs) {
            Text(title)
                .hardwoodText(.caption)
            Text(HardwoodChart.valueLabel(value, format: .percent1))
                .font(Typography.tableHeaderMono)
                .foregroundStyle(value == nil ? Palette.textTertiary : Palette.textPrimary)
        }
        .accessibilityElement(children: .combine)
    }

    private func chart(title: String, bars: [ZoneBar], format: MetricFormat) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text(title)
                .hardwoodText(.caption, color: Palette.textSecondary)
            Chart(bars) { bar in
                BarMark(x: .value(title, bar.value),
                        y: .value("Zone", bar.zoneLabel))
                    .position(by: .value("Whose", bar.whose))
                    .foregroundStyle(bar.color)
                    .cornerRadius(2)
            }
            .chartYScale(domain: zoneOrder)
            .chartXAxis { HardwoodChartAxis.metricValues(format: format, position: .bottom, desiredCount: 3) }
            .chartYAxis { HardwoodChartAxis.categories() }
            .hardwoodChartStyle()
            .frame(height: chartHeight)
            .accessibilityLabel("\(title). \(accessibilityText)")
        }
    }
}

#if DEBUG
#Preview("Shot profile") {
    ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            Text("Large — diet and accuracy").hardwoodText(.caption)
            ShotProfileWidget(payload: .preview, size: .large)
                .hardwoodCard()
            Text("Medium — diet only").hardwoodText(.caption)
            ShotProfileWidget(payload: .preview, size: .medium)
                .hardwoodCard()
            Text("An era with no shot locations").hardwoodText(.caption)
            ShotProfileWidget(payload: ShotProfilePayload(
                subject: .preview,
                season: "1961-62",
                seasonType: "Regular Season",
                zones: [],
                threePointRate: nil,
                freeThrowRate: nil,
                note: "Shot locations were not recorded in 1961-62. The league began charting shot coordinates in 1996-97."),
                size: .large)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
