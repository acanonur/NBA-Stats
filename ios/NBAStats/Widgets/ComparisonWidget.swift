import Charts
import Foundation
import SwiftUI

/// `comparison` — two to four subjects across the chosen metrics, in whichever of the three
/// shapes the payload asked for.
///
/// * `"bars"` — grouped horizontal bars. Percentile-normalized when the payload says so, in
///   which case the axis is a real 0-100 scale; otherwise each row is scaled within itself and
///   the axis is hidden, because a chart that puts 61.5% and 118.2 on one axis is nonsense.
/// * `"table"` — a compact matrix with the best value in each row emphasized, direction aware.
/// * `"radar"` — drawn with `Path`, since Swift Charts has no radar mark. Above eight metrics it
///   falls back to bars: nine spokes is a scribble, not a comparison.
///
/// A subject with no number for a metric gets no bar, no vertex and no zero — the gap is named
/// in a footnote instead.
public struct ComparisonWidget: View {

    // MARK: Types

    private enum Presentation {
        case bars
        case table
        case radar
    }

    private struct SubjectInfo: Identifiable {
        let id: String
        let subject: ComparisonSubject
        let color: Color

        var name: String { subject.displayName }
        var shortName: String {
            if let player = subject.player { return player.shortName }
            if let team = subject.team { return team.abbr }
            return subject.displayName
        }
    }

    private struct BarDatum: Identifiable {
        let id: String
        let metricLabel: String
        let subjectName: String
        let color: Color
        let plotted: Double
        let displayValue: String
    }

    /// The polar geometry of the hand-drawn radar.
    private struct RadarLayout {
        let center: CGPoint
        let radius: CGFloat
        let axisCount: Int

        private func angle(_ axis: Int) -> Double {
            guard axisCount > 0 else { return -Double.pi / 2 }
            return -Double.pi / 2 + 2 * Double.pi * Double(axis) / Double(axisCount)
        }

        func point(axis: Int, fraction: Double) -> CGPoint {
            let clamped = min(max(fraction, 0), 1)
            let theta = angle(axis)
            let distance = Double(radius) * clamped
            return CGPoint(x: center.x + CGFloat(distance * cos(theta)),
                           y: center.y + CGFloat(distance * sin(theta)))
        }

        func labelPoint(axis: Int, inset: CGFloat) -> CGPoint {
            let theta = angle(axis)
            let distance = Double(radius + inset)
            return CGPoint(x: center.x + CGFloat(distance * cos(theta)),
                           y: center.y + CGFloat(distance * sin(theta)))
        }
    }

    // MARK: Stored

    private let payload: ComparisonPayload
    private let size: WidgetSize

    public init(payload: ComparisonPayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Derived

    private var subjectInfos: [SubjectInfo] {
        payload.subjects.prefix(4).enumerated().map { pair -> SubjectInfo in
            let index = pair.offset
            let subject = pair.element
            let colorIndex = subject.colorIndex >= 0 ? subject.colorIndex : index
            return SubjectInfo(id: subject.id,
                               subject: subject,
                               color: HardwoodChart.seriesColor(at: colorIndex))
        }
    }

    private var metricLimit: Int {
        switch size {
        case .small: return 4
        case .medium: return 6
        case .large: return 8
        }
    }

    private var metrics: [MetricDescriptor] { Array(payload.metrics.prefix(metricLimit)) }

    private var usesPercentile: Bool { payload.normalization.lowercased() == "percentile" }

    /// Percentiles only drive the axis when every displayed cell actually has one; a half-filled
    /// percentile axis would silently compare two different scales.
    private var hasPercentileEverywhere: Bool {
        let values = metrics.flatMap { metric in subjectInfos.compactMap { $0.subject.value(metric.key) } }
        let present = values.filter { $0.value != nil }
        return !present.isEmpty && present.allSatisfy { $0.percentile != nil }
    }

    private var usesPercentileAxis: Bool { usesPercentile && hasPercentileEverywhere }

    private var presentation: Presentation {
        if size == .small { return .table }
        switch payload.style.lowercased() {
        case "table":
            return .table
        case "radar":
            return (payload.metrics.count > 8 || metrics.count < 3) ? .bars : .radar
        default:
            return .bars
        }
    }

    private var chartHeight: CGFloat {
        let rows = max(metrics.count, 1)
        switch size {
        case .small: return CGFloat(rows) * 22 + 24
        case .medium: return CGFloat(rows) * 26 + 26
        case .large: return CGFloat(rows) * 30 + 28
        }
    }

    private var radarHeight: CGFloat {
        switch size {
        case .small: return 150
        case .medium: return 190
        case .large: return 250
        }
    }

    private var contextText: String {
        Formatting.seasonContext(season: payload.season, seasonType: payload.seasonType)
    }

    private var legendItems: [HardwoodChartLegendItem] {
        subjectInfos.map { info in
            HardwoodChartLegendItem(id: info.id, label: info.name, color: info.color, symbol: .square)
        }
    }

    private var scaleNote: String {
        if usesPercentileAxis {
            return "Bars are league percentiles: 100 is the best mark in the league this season."
        }
        return "Each row is scaled within itself — the labels carry the real numbers, not the bar lengths."
    }

    private var missingNotes: [String] {
        var notes: [String] = []
        for metric in metrics {
            let missing = subjectInfos.filter { info in
                guard let value = info.subject.value(metric.key) else { return true }
                return value.value == nil
            }
            guard !missing.isEmpty else { continue }
            let names = missing.map(\.shortName).joined(separator: ", ")
            notes.append("\(metric.shortName) has no number for \(names): \(metric.availability.seasonFrom) is as far back as it goes.")
        }
        return Array(notes.prefix(2))
    }

    private var accessibilityText: String {
        let names = subjectInfos.map(\.name).joined(separator: " versus ")
        let metricNames = metrics.map(\.shortName).joined(separator: ", ")
        return "Comparison of \(names) across \(metricNames)"
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            if subjectInfos.isEmpty || metrics.isEmpty {
                HardwoodChartPlaceholder(icon: "person.2",
                                         message: "Pick at least two subjects and one metric to compare.",
                                         detail: contextText.isEmpty ? nil : contextText)
            } else {
                content
                if legendItems.count > 1, presentation != .table {
                    HardwoodChartLegend(items: legendItems)
                }
                HardwoodChartFootnote(lines: footnoteLines)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var footnoteLines: [String] {
        var lines: [String] = []
        switch presentation {
        case .bars:
            lines.append(scaleNote)
        case .radar:
            lines.append(usesPercentileAxis
                         ? "Spokes are league percentiles; further from the centre is better."
                         : "Spokes are scaled within each metric and oriented so further from the centre is better.")
        case .table:
            break
        }
        lines.append(contentsOf: missingNotes)
        return lines
    }

    @ViewBuilder private var content: some View {
        switch presentation {
        case .bars:  barsChart
        case .table: table
        case .radar: radar
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
            Text(subjectInfos.map(\.shortName).joined(separator: " · "))
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

    // MARK: Bars

    private var barData: [BarDatum] {
        var data: [BarDatum] = []
        for metric in metrics {
            let rowValues = subjectInfos.compactMap { $0.subject.value(metric.key)?.value }.filter { $0.isFinite }
            let lowest = rowValues.min()
            let highest = rowValues.max()
            for info in subjectInfos {
                guard let measurement = info.subject.value(metric.key),
                      let raw = measurement.value,
                      raw.isFinite else { continue }
                let plotted: Double
                if usesPercentileAxis, let percentile = measurement.percentile, percentile.isFinite {
                    let scaled = percentile > 1 ? percentile : percentile * 100
                    plotted = min(max(scaled, 0), 100)
                } else if let lowest, let highest, highest - lowest > 1e-9 {
                    // Row-relative: the shortest bar still has to be visible, so the scale
                    // starts at 12 rather than 0.
                    plotted = 12 + 88 * (raw - lowest) / (highest - lowest)
                } else {
                    plotted = 60
                }
                data.append(BarDatum(id: "\(metric.key)|\(info.id)",
                                     metricLabel: metric.shortName,
                                     subjectName: info.shortName,
                                     color: info.color,
                                     plotted: plotted,
                                     displayValue: displayText(measurement, metric: metric)))
            }
        }
        return data
    }

    /// Percentiles are a real 0-100 axis; the row-relative scale leaves headroom on the right
    /// for the value label that carries the actual number.
    private var barXDomain: ClosedRange<Double> {
        usesPercentileAxis ? 0...100 : 0...128
    }

    private var barsChart: some View {
        Chart(barData) { datum in
            BarMark(x: .value("Value", datum.plotted),
                    y: .value("Metric", datum.metricLabel))
                .position(by: .value("Subject", datum.subjectName))
                .foregroundStyle(datum.color)
                .cornerRadius(2)
                .annotation(position: .trailing, alignment: .leading, spacing: 2) {
                    if !usesPercentileAxis {
                        Text(datum.displayValue)
                            .font(Typography.tableHeaderMono)
                            .foregroundStyle(Palette.textSecondary)
                            .lineLimit(1)
                    }
                }
        }
        .chartXScale(domain: barXDomain)
        .chartYScale(domain: metrics.map(\.shortName))
        .chartXAxis(usesPercentileAxis ? .automatic : .hidden)
        .chartYAxis { HardwoodChartAxis.categories() }
        .hardwoodChartStyle()
        .frame(height: chartHeight)
        .accessibilityLabel(accessibilityText)
    }

    // MARK: Table

    private var table: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            HStack(spacing: Spacing.xs) {
                Text("Metric")
                    .hardwoodText(.tableHeader)
                    .frame(width: 62, alignment: .leading)
                ForEach(subjectInfos) { info in
                    HStack(spacing: Spacing.xxs) {
                        Circle()
                            .fill(info.color)
                            .frame(width: 6, height: 6)
                            .accessibilityHidden(true)
                        Text(info.shortName)
                            .hardwoodText(.tableHeader)
                            .lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, alignment: .trailing)
                }
            }
            Divider().overlay(Palette.separator)
            ForEach(metrics) { metric in
                tableRow(metric)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(accessibilityText)
    }

    private func tableRow(_ metric: MetricDescriptor) -> some View {
        let best = bestValue(for: metric)
        return HStack(spacing: Spacing.xs) {
            HStack(spacing: 2) {
                Text(metric.shortName)
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                    .lineLimit(1)
                if !metric.higherIsBetter {
                    Image(systemName: "arrow.down")
                        .imageScale(.small)
                        .foregroundStyle(Palette.textTertiary)
                        .accessibilityHidden(true)
                }
            }
            .frame(width: 62, alignment: .leading)
            ForEach(subjectInfos) { info in
                tableCell(metric: metric, info: info, best: best)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func tableCell(metric: MetricDescriptor, info: SubjectInfo, best: Double?) -> some View {
        let measurement = info.subject.value(metric.key)
        var isBest = false
        if let best = best, let raw = measurement?.value, raw.isFinite {
            isBest = abs(raw - best) < 1e-9
        }
        let availability = measurement?.availability ?? .unavailable
        return HStack(spacing: 2) {
            Text(displayText(measurement, metric: metric))
                .font(Typography.tableCellMono)
                .fontWeight(isBest ? .semibold : .regular)
                .availabilityStyled(availability)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
            AvailabilityBadge(availability: availability,
                              showsText: false,
                              isInteractive: false,
                              metricName: metric.name,
                              season: payload.season)
        }
        .padding(.horizontal, Spacing.xs)
        .padding(.vertical, 1)
        .background(
            RoundedRectangle(cornerRadius: Radius.chip, style: .continuous)
                .fill(isBest ? info.color.opacity(0.16) : Color.clear)
        )
        .frame(maxWidth: .infinity, alignment: .trailing)
    }

    /// The best raw value in a row, respecting the metric's own direction.
    private func bestValue(for metric: MetricDescriptor) -> Double? {
        let values = subjectInfos.compactMap { $0.subject.value(metric.key)?.value }.filter { $0.isFinite }
        return metric.higherIsBetter ? values.max() : values.min()
    }

    // MARK: Radar

    /// How far along its spoke a subject's value sits, or `nil` when the subject has no number
    /// for that metric — a missing vertex breaks the outline rather than collapsing to the
    /// centre, which would read as a zero.
    private func radarFraction(metric: MetricDescriptor, subject: ComparisonSubject) -> Double? {
        guard let measurement = subject.value(metric.key), let raw = measurement.value, raw.isFinite else {
            return nil
        }
        // `usesPercentileAxis`, not `usesPercentile`: the same all-or-nothing rule the bars and
        // the caption obey. Keyed off `usesPercentile` alone, a subject that happens to carry a
        // percentile is plotted on the percentile scale while one that does not falls through to
        // the row-relative scale below — and that branch orients itself by `higherIsBetter` while
        // percentiles do not, so on a lower-is-better metric the shape can rank them backwards.
        if usesPercentileAxis, let percentile = measurement.percentile, percentile.isFinite {
            let normalized = percentile > 1 ? percentile / 100 : percentile
            return min(max(normalized, 0.02), 1)
        }
        let values = subjectInfos.compactMap { $0.subject.value(metric.key)?.value }.filter { $0.isFinite }
        guard let lowest = values.min(), let highest = values.max(), highest - lowest > 1e-9 else {
            return 0.6
        }
        let fraction = (raw - lowest) / (highest - lowest)
        let oriented = metric.higherIsBetter ? fraction : 1 - fraction
        return 0.2 + 0.8 * min(max(oriented, 0), 1)
    }

    private func radarPath(for subject: ComparisonSubject, layout: RadarLayout) -> (path: Path, isComplete: Bool) {
        var path = Path()
        var runStarted = false
        var isComplete = true
        var firstPoint: CGPoint?

        for (index, metric) in metrics.enumerated() {
            guard let fraction = radarFraction(metric: metric, subject: subject) else {
                isComplete = false
                runStarted = false
                continue
            }
            let point = layout.point(axis: index, fraction: fraction)
            if firstPoint == nil { firstPoint = point }
            if runStarted {
                path.addLine(to: point)
            } else {
                path.move(to: point)
                runStarted = true
            }
        }
        if isComplete, metrics.count > 2, let anchor = firstPoint {
            path.addLine(to: anchor)
        }
        return (path, isComplete)
    }

    private func radarGridPath(_ layout: RadarLayout, fraction: Double) -> Path {
        var path = Path()
        guard metrics.count > 2 else { return path }
        for index in metrics.indices {
            let point = layout.point(axis: index, fraction: fraction)
            if index == 0 { path.move(to: point) } else { path.addLine(to: point) }
        }
        path.closeSubpath()
        return path
    }

    private func radarSpokesPath(_ layout: RadarLayout) -> Path {
        var path = Path()
        for index in metrics.indices {
            path.move(to: layout.center)
            path.addLine(to: layout.point(axis: index, fraction: 1))
        }
        return path
    }

    private var radar: some View {
        GeometryReader { geometry in
            let layout = RadarLayout(center: CGPoint(x: geometry.size.width / 2,
                                                     y: geometry.size.height / 2),
                                     radius: max(min(geometry.size.width, geometry.size.height) / 2 - 30, 12),
                                     axisCount: metrics.count)
            ZStack {
                ForEach([0.25, 0.5, 0.75, 1.0], id: \.self) { ring in
                    radarGridPath(layout, fraction: ring)
                        .stroke(Palette.separator, lineWidth: ring == 1.0 ? 1.2 : 0.7)
                }
                radarSpokesPath(layout)
                    .stroke(Palette.separator.opacity(0.7), lineWidth: 0.7)
                ForEach(subjectInfos) { info in
                    radarOutline(info, layout: layout)
                }
                ForEach(metrics.indices, id: \.self) { index in
                    radarAxisLabel(index: index, layout: layout)
                }
            }
        }
        .frame(height: radarHeight)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
    }

    private func radarOutline(_ info: SubjectInfo, layout: RadarLayout) -> some View {
        let shape = radarPath(for: info.subject, layout: layout)
        return ZStack {
            if shape.isComplete {
                shape.path.fill(info.color.opacity(0.14))
            }
            shape.path.stroke(info.color,
                              style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
            ForEach(metrics.indices, id: \.self) { index in
                radarVertex(info: info, index: index, layout: layout)
            }
        }
    }

    @ViewBuilder private func radarVertex(info: SubjectInfo, index: Int, layout: RadarLayout) -> some View {
        if index < metrics.count, let fraction = radarFraction(metric: metrics[index], subject: info.subject) {
            Circle()
                .fill(info.color)
                .frame(width: 6, height: 6)
                .position(layout.point(axis: index, fraction: fraction))
        }
    }

    @ViewBuilder private func radarAxisLabel(index: Int, layout: RadarLayout) -> some View {
        if index < metrics.count {
            let metric = metrics[index]
            let isMissing = subjectInfos.allSatisfy { radarFraction(metric: metric, subject: $0.subject) == nil }
            Text(metric.shortName)
                .hardwoodText(.tableHeader, color: isMissing ? Palette.textTertiary : Palette.textSecondary)
                .lineLimit(1)
                .fixedSize()
                .position(layout.labelPoint(axis: index, inset: 16))
        }
    }

    // MARK: Shared

    /// The server formats every value; this only covers a cell the payload never sent.
    private func displayText(_ measurement: MetricValue?, metric: MetricDescriptor) -> String {
        guard let measurement else { return HardwoodNumberFormat.missing }
        if measurement.displayValue.isEmpty {
            return HardwoodNumberFormat.string(measurement.value, format: metric.format)
        }
        return measurement.displayValue
    }
}

#if DEBUG
private enum ComparisonPreviewData {
    static let threeWay = ComparisonPayload(
        season: "2025-26",
        seasonType: "Regular Season",
        normalization: "percentile",
        style: "radar",
        metrics: [.preview, .previewRating, .previewImpact],
        subjects: [
            ComparisonSubject(player: .preview, colorIndex: 0, values: [
                MetricValue(metric: "ts_pct", value: 0.6153, displayValue: "61.5%", rank: 12, percentile: 0.93, leagueAverage: 0.5671),
                MetricValue(metric: "net_rtg", value: 7.8, displayValue: "+7.8", rank: 12, percentile: 0.91, leagueAverage: 0),
                MetricValue(metric: "game_score", value: 19.4, displayValue: "19.4", rank: 7, percentile: 0.88, leagueAverage: 10.1)
            ]),
            ComparisonSubject(player: .previewSecondary, colorIndex: 1, values: [
                MetricValue(metric: "ts_pct", value: 0.6612, displayValue: "66.1%", rank: 1, percentile: 1.0, leagueAverage: 0.5671),
                MetricValue(metric: "net_rtg", value: 9.4, displayValue: "+9.4", rank: 4, percentile: 0.97, leagueAverage: 0),
                MetricValue(metric: "game_score", value: nil, displayValue: Formatting.emDash, rank: nil,
                            percentile: nil, leagueAverage: nil, delta: nil, isEstimated: nil, availability: .unavailable)
            ])
        ]
    )

    static let table = ComparisonPayload(
        season: ComparisonPayload.preview.season,
        seasonType: ComparisonPayload.preview.seasonType,
        normalization: "raw",
        style: "table",
        metrics: ComparisonPayload.preview.metrics,
        subjects: ComparisonPayload.preview.subjects
    )
}

#Preview("Comparison") {
    ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            Text("Bars — percentile normalized").hardwoodText(.caption)
            ComparisonWidget(payload: .preview, size: .large)
                .hardwoodCard()
            Text("Radar — one subject missing a metric").hardwoodText(.caption)
            ComparisonWidget(payload: ComparisonPreviewData.threeWay, size: .large)
                .hardwoodCard()
            Text("Table — best value per row emphasized").hardwoodText(.caption)
            ComparisonWidget(payload: ComparisonPreviewData.table, size: .medium)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
