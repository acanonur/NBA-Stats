import Charts
import Foundation
import SwiftUI

/// `trend_chart` — up to four subjects' per-game values with their rolling average.
///
/// Three decisions worth naming:
/// * the **rolling average is the emphasized line**; the per-game values are low-opacity marks
///   behind it, and they are dropped entirely below the large size where they turn into noise;
/// * a game with no value **breaks the line** instead of being interpolated through or plotted
///   as a zero — a missed game did not happen, and pretending it was a zero would be a lie;
/// * a point whose date string does not parse is dropped, never plotted at "now".
public struct TrendChartWidget: View {

    // MARK: Plot model

    /// One plotted mark. `runID` groups the marks that belong to one unbroken stretch of a
    /// series, which is how the line is made to break across missing games.
    private struct Mark: Identifiable {
        let id: String
        let runID: String
        let seriesID: String
        let seriesLabel: String
        let color: Color
        let date: Date
        let value: Double
        let opponentAbbr: String?
    }

    private struct SeriesInfo: Identifiable {
        let id: String
        let label: String
        let color: Color
    }

    private struct Model {
        var lineMarks: [Mark] = []
        var rawMarks: [Mark] = []
        var series: [SeriesInfo] = []
        var dates: [Date] = []
        var yDomain: ClosedRange<Double> = 0...1
        var usesRolling = false
        var droppedPoints = 0

        var isEmpty: Bool { lineMarks.isEmpty && rawMarks.isEmpty }
    }

    /// One line of the readout above the chart.
    private struct ReadoutRow: Identifiable {
        let id: String
        let label: String
        let color: Color
        let opponentAbbr: String?
        let valueText: String
        let rollingText: String?
        let isMissing: Bool
    }

    // MARK: Stored

    private let payload: TrendChartPayload
    private let size: WidgetSize
    private let model: Model

    @State private var selectedDate: Date?

    public init(payload: TrendChartPayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
        self.model = Self.makeModel(payload: payload)
    }

    // MARK: Derived

    /// The raw per-game marks are only worth drawing where there is room for them.
    private var showsRawPoints: Bool {
        switch size {
        case .small, .medium: return false
        case .large: return true
        }
    }

    private var chartHeight: CGFloat {
        switch size {
        case .small: return 96
        case .medium: return 142
        case .large: return 206
        }
    }

    private var metric: MetricDescriptor { payload.metric }

    private var rollingWindowText: String? {
        guard model.usesRolling, let window = payload.rollingWindow, window > 1 else { return nil }
        return "\(window)-game rolling average"
    }

    /// The date the readout is describing: whatever the drag is nearest to, or the last game.
    private var inspectedDate: Date? {
        let target = selectedDate ?? model.dates.last
        guard let target else { return nil }
        return model.dates.min(by: { first, second in
            abs(first.timeIntervalSince(target)) < abs(second.timeIntervalSince(target))
        })
    }

    /// Only a live drag draws the selection rule; the resting readout describes the last game.
    private var selectionRuleDate: Date? {
        selectedDate == nil ? nil : inspectedDate
    }

    private var selectionMarks: [Mark] {
        guard let date = selectionRuleDate else { return [] }
        return model.rawMarks.filter { Self.isSameGameDay($0.date, date) }
    }

    private var readoutRows: [ReadoutRow] {
        guard let date = inspectedDate else { return [] }
        return model.series.map { series -> ReadoutRow in
            let raw = model.rawMarks.first { $0.seriesID == series.id && Self.isSameGameDay($0.date, date) }
            let rolling = model.lineMarks.first { $0.seriesID == series.id && Self.isSameGameDay($0.date, date) }
            var rollingText: String?
            if model.usesRolling, let rolling = rolling {
                rollingText = HardwoodChart.valueLabel(rolling.value, format: metric.format)
            }
            return ReadoutRow(id: series.id,
                              label: series.label,
                              color: series.color,
                              opponentAbbr: raw?.opponentAbbr,
                              valueText: HardwoodChart.valueLabel(raw?.value, format: metric.format),
                              rollingText: rollingText,
                              isMissing: raw == nil)
        }
    }

    private var legendItems: [HardwoodChartLegendItem] {
        var items = model.series.map { series in
            HardwoodChartLegendItem(id: series.id, label: series.label, color: series.color, symbol: .line)
        }
        if payload.leagueAverage != nil {
            items.append(HardwoodChartLegendItem(id: "league",
                                                 label: "League average",
                                                 color: Palette.textTertiary,
                                                 symbol: .dashedLine))
        }
        return items
    }

    private var footnoteLines: [String] {
        var lines: [String] = []
        if !showsRawPoints, !model.rawMarks.isEmpty, model.usesRolling {
            lines.append("Per-game values are folded into the rolling line at this size.")
        }
        if model.droppedPoints > 0 {
            lines.append("\(model.droppedPoints) point\(model.droppedPoints == 1 ? "" : "s") had no readable game date and are not plotted.")
        }
        return lines
    }

    private var accessibilityText: String {
        let names = model.series.map(\.label).joined(separator: ", ")
        let count = model.rawMarks.isEmpty ? model.lineMarks.count : model.rawMarks.count
        var text = "\(metric.name) over \(count) games for \(names)"
        if let rollingWindowText {
            text += ", drawn as a \(rollingWindowText)"
        }
        if let league = payload.leagueAverage {
            text += ", league average \(HardwoodChart.valueLabel(league, format: metric.format))"
        }
        return text
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            if model.isEmpty {
                HardwoodChartPlaceholder(icon: "chart.xyaxis.line",
                                         message: "No games with a \(metric.name) value yet.",
                                         detail: "The line appears as soon as a game in this span finalizes.",
                                         minHeight: chartHeight)
            } else {
                readout
                chart
                if legendItems.count > 1 {
                    HardwoodChartLegend(items: legendItems)
                }
                HardwoodChartFootnote(lines: footnoteLines)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
            Text(metric.name)
                .hardwoodText(.statLabel, color: Palette.textPrimary)
                .lineLimit(1)
            if let rollingWindowText {
                Text(rollingWindowText)
                    .hardwoodText(.caption)
                    .lineLimit(1)
            }
            Spacer(minLength: Spacing.xs)
            if let league = payload.leagueAverage {
                Text("Lg \(HardwoodChart.valueLabel(league, format: metric.format))")
                    .font(Typography.tableHeaderMono)
                    .foregroundStyle(Palette.textTertiary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    /// The drag readout. It is always present — resting on the most recent game — so inspecting
    /// the chart never changes the tile's height.
    private var readout: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            HStack(spacing: Spacing.xs) {
                Text(HardwoodChart.shortDate(inspectedDate))
                    .font(Typography.tableHeaderMono)
                    .foregroundStyle(Palette.textSecondary)
                if selectedDate == nil {
                    Text("latest · drag to inspect")
                        .hardwoodText(.caption)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            ForEach(readoutRows) { row in
                readoutLine(row)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    private func readoutLine(_ row: ReadoutRow) -> some View {
        HStack(spacing: Spacing.xs) {
            Circle()
                .fill(row.color)
                .frame(width: 7, height: 7)
                .accessibilityHidden(true)
            if model.series.count > 1 {
                Text(row.label)
                    .hardwoodText(.caption, color: Palette.textSecondary)
                    .lineLimit(1)
            }
            if let opponent = row.opponentAbbr {
                Text("vs \(opponent)")
                    .hardwoodText(.caption)
                    .lineLimit(1)
            }
            Spacer(minLength: Spacing.xs)
            Text(row.valueText)
                .font(Typography.statValueMono)
                .foregroundStyle(row.isMissing ? Palette.textTertiary : Palette.textPrimary)
            if let rolling = row.rollingText {
                Text("avg \(rolling)")
                    .font(Typography.tableHeaderMono)
                    .foregroundStyle(Palette.textTertiary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var chart: some View {
        Chart {
            ForEach(model.lineMarks) { mark in
                LineMark(x: .value("Date", mark.date),
                         y: .value(metric.shortName, mark.value),
                         series: .value("Series", mark.runID))
                    .foregroundStyle(mark.color)
                    .interpolationMethod(.monotone)
                    .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
            }
            if showsRawPoints {
                ForEach(model.rawMarks) { mark in
                    PointMark(x: .value("Date", mark.date),
                              y: .value(metric.shortName, mark.value))
                        .foregroundStyle(mark.color)
                        .opacity(HardwoodChart.rawMarkOpacity)
                        .symbolSize(22)
                }
            }
            if let league = payload.leagueAverage, league.isFinite {
                RuleMark(y: .value("League average", league))
                    .foregroundStyle(Palette.textTertiary)
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: HardwoodChart.ruleDash))
            }
            if let date = selectionRuleDate {
                RuleMark(x: .value("Inspected", date))
                    .foregroundStyle(Palette.textSecondary.opacity(0.5))
                    .lineStyle(StrokeStyle(lineWidth: 1))
                ForEach(selectionMarks) { mark in
                    PointMark(x: .value("Date", mark.date),
                              y: .value(metric.shortName, mark.value))
                        .foregroundStyle(mark.color)
                        .symbolSize(58)
                }
            }
        }
        .chartYScale(domain: model.yDomain)
        .chartYAxis { HardwoodChartAxis.metricValues(format: metric.format) }
        .chartXAxis { HardwoodChartAxis.dates(desiredCount: size == .large ? 5 : 3) }
        .chartOverlay { proxy in
            GeometryReader { geometry in
                Rectangle()
                    .fill(Color.clear)
                    .contentShape(Rectangle())
                    .gesture(inspectGesture(proxy: proxy, geometry: geometry))
            }
        }
        .hardwoodChartStyle()
        .frame(height: chartHeight)
        .accessibilityLabel(accessibilityText)
    }

    private func inspectGesture(proxy: ChartProxy, geometry: GeometryProxy) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { drag in
                guard let plotFrame = proxy.plotFrame else { return }
                let origin = geometry[plotFrame].origin
                let position = drag.location.x - origin.x
                guard let date = proxy.value(atX: position, as: Date.self) else { return }
                selectedDate = date
            }
            .onEnded { _ in
                // The selection is deliberately kept after the finger lifts: the reader is
                // usually reading the number they just scrubbed to.
            }
    }

    // MARK: Model building

    private static func isSameGameDay(_ lhs: Date, _ rhs: Date) -> Bool {
        abs(lhs.timeIntervalSince(rhs)) < 43_200
    }

    private static func makeModel(payload: TrendChartPayload) -> Model {
        var model = Model()
        let series = Array(payload.series.prefix(4))
        let usesRolling = series.contains { line in
            line.points.contains { $0.rolling != nil }
        }
        model.usesRolling = usesRolling

        var lineValues: [Double] = []
        var rawValues: [Double] = []
        var dates: Set<Date> = []

        for (index, line) in series.enumerated() {
            let colorIndex = line.colorIndex >= 0 ? line.colorIndex : index
            let color = HardwoodChart.seriesColor(at: colorIndex)
            let label = line.label.isEmpty ? "Series \(index + 1)" : line.label
            model.series.append(SeriesInfo(id: line.id, label: label, color: color))

            // A run is an unbroken stretch of readable values; a gap starts a new one so the
            // drawn line breaks rather than leaping across a missed game.
            var runIndex = 0
            var runHasPoints = false

            for (pointIndex, point) in line.points.enumerated() {
                guard let date = HardwoodChart.date(from: point.x) else {
                    model.droppedPoints += 1
                    if runHasPoints {
                        runIndex += 1
                        runHasPoints = false
                    }
                    continue
                }
                dates.insert(date)

                if let raw = point.y, raw.isFinite {
                    rawValues.append(raw)
                    model.rawMarks.append(Mark(id: "raw|\(line.id)|\(pointIndex)|\(point.id)",
                                               runID: "raw|\(line.id)",
                                               seriesID: line.id,
                                               seriesLabel: label,
                                               color: color,
                                               date: date,
                                               value: raw,
                                               opponentAbbr: point.opponentAbbr))
                }

                let emphasized = usesRolling ? point.rolling : point.y
                if let emphasized, emphasized.isFinite {
                    lineValues.append(emphasized)
                    model.lineMarks.append(Mark(id: "line|\(line.id)|\(pointIndex)|\(point.id)",
                                                runID: "\(line.id)#\(runIndex)",
                                                seriesID: line.id,
                                                seriesLabel: label,
                                                color: color,
                                                date: date,
                                                value: emphasized,
                                                opponentAbbr: point.opponentAbbr))
                    runHasPoints = true
                } else if runHasPoints {
                    runIndex += 1
                    runHasPoints = false
                }
            }
        }

        model.lineMarks.sort { $0.date < $1.date }
        model.rawMarks.sort { $0.date < $1.date }
        model.dates = dates.sorted()

        var league: [Double] = []
        if let average = payload.leagueAverage, average.isFinite {
            league = [average]
        }
        model.yDomain = HardwoodChart.yDomain(preferred: payload.yDomain,
                                              metric: payload.metric,
                                              values: lineValues + rawValues,
                                              mustInclude: league)
        return model
    }
}

#if DEBUG
#Preview("Trend chart") {
    ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            Text("Large — raw points plus the rolling line").hardwoodText(.caption)
            TrendChartWidget(payload: .preview, size: .large)
                .hardwoodCard()
            Text("Medium — raw points dropped").hardwoodText(.caption)
            TrendChartWidget(payload: .preview, size: .medium)
                .hardwoodCard()
            Text("Single series with a broken line").hardwoodText(.caption)
            TrendChartWidget(payload: TrendChartPayload(
                metric: .preview,
                rollingWindow: 3,
                leagueAverage: 0.5671,
                yDomain: nil,
                series: [
                    TrendSeries(id: "2544", label: "LeBron James", colorIndex: 0, points: [
                        TrendPoint(x: "2025-12-26", y: 0.588, rolling: 0.571, gameId: "1", opponentAbbr: "SAC"),
                        TrendPoint(x: "2025-12-28", y: nil, rolling: nil, gameId: "2", opponentAbbr: "DEN"),
                        TrendPoint(x: "2025-12-30", y: 0.515, rolling: 0.552, gameId: "3", opponentAbbr: "GSW"),
                        TrendPoint(x: "not-a-date", y: 0.700, rolling: 0.600, gameId: "4", opponentAbbr: "???"),
                        TrendPoint(x: "2026-01-02", y: 0.641, rolling: 0.578, gameId: "5", opponentAbbr: "BOS")
                    ])
                ]), size: .large)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
