import Charts
import Foundation
import SwiftUI

/// `career_arc` — one metric across a whole career, by season or by age.
///
/// This is the widget where comparing a 1962 season with a 2026 one is most tempting and most
/// wrong, so the era work is the point of it:
/// * a season whose value is **estimated** from the box score rather than measured is drawn with
///   a dashed segment and a diamond marker, never silently blended into the measured line;
/// * a season the league has no number for is **left out**, not plotted at zero, and counted in
///   the footnote;
/// * the boundaries where the league's record actually changed are dashed vertical rules with
///   their labels, so the reader can see which side of 1996-97 a number came from.
public struct CareerArcWidget: View {

    // MARK: Types

    private struct ArcPoint: Identifiable {
        let id: String
        let x: Double
        let value: Double
        let seasonLabel: String
        let startYear: Int?
        let age: Int?
        let teamAbbr: String?
        let availability: MetricAvailability
        let isPlayoffs: Bool
        let displayValue: String

        var isEstimated: Bool { availability == .estimated }
    }

    private struct LineMarkDatum: Identifiable {
        let id: String
        let runID: String
        let x: Double
        let value: Double
        let color: Color
        let isEstimated: Bool
        let isPlayoffs: Bool
    }

    private struct EraMarkDatum: Identifiable {
        let id: String
        let x: Double
        let seasonLabel: String
        let label: String
    }

    private struct PeakDatum {
        let x: Double
        let value: Double
        let label: String
    }

    private struct Model {
        var regular: [ArcPoint] = []
        var playoffs: [ArcPoint] = []
        var lineMarks: [LineMarkDatum] = []
        var eraMarks: [EraMarkDatum] = []
        var peak: PeakDatum?
        var yDomain: ClosedRange<Double> = 0...1
        var xTicks: [Double] = []
        var usesAgeAxis = false
        var estimatedCount = 0
        var partialCount = 0
        var missingCount = 0

        var allPoints: [ArcPoint] { regular + playoffs }
        var isEmpty: Bool { regular.isEmpty && playoffs.isEmpty }
    }

    // MARK: Stored

    private let payload: CareerArcPayload
    private let size: WidgetSize
    private let model: Model

    public init(payload: CareerArcPayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
        self.model = Self.makeModel(payload: payload)
    }

    // MARK: Derived

    private var metric: MetricDescriptor { payload.metric }

    private static let regularColorIndex = 0
    private static let playoffColorIndex = 3

    private var regularColor: Color { HardwoodChart.seriesColor(at: Self.regularColorIndex) }
    private var playoffColor: Color { HardwoodChart.seriesColor(at: Self.playoffColorIndex) }

    private var chartHeight: CGFloat {
        switch size {
        case .small: return 112
        case .medium: return 158
        case .large: return 224
        }
    }

    private var showsEraLabels: Bool {
        switch size {
        case .small, .medium: return false
        case .large: return true
        }
    }

    private var axisCaption: String {
        model.usesAgeAxis ? "Age" : "Season"
    }

    private var legendItems: [HardwoodChartLegendItem] {
        var items: [HardwoodChartLegendItem] = [
            HardwoodChartLegendItem(id: "regular", label: "Regular season", color: regularColor, symbol: .line)
        ]
        if !model.playoffs.isEmpty {
            items.append(HardwoodChartLegendItem(id: "playoffs", label: "Playoffs", color: playoffColor, symbol: .line))
        }
        if model.estimatedCount > 0 {
            items.append(HardwoodChartLegendItem(id: "estimated",
                                                 label: "Estimated",
                                                 color: Palette.warning,
                                                 symbol: .dashedLine))
        }
        if !model.eraMarks.isEmpty {
            items.append(HardwoodChartLegendItem(id: "era",
                                                 label: "Era boundary",
                                                 color: Palette.textTertiary,
                                                 symbol: .dashedLine))
        }
        return items
    }

    private var footnoteLines: [String] {
        var lines: [String] = []
        if model.estimatedCount > 0 {
            lines.append("\(model.estimatedCount) season\(model.estimatedCount == 1 ? " is" : "s are") estimated from the box score rather than measured — dashed line, diamond markers.")
        }
        if model.partialCount > 0 {
            lines.append("\(model.partialCount) season\(model.partialCount == 1 ? " is" : "s are") built from an incomplete set of games — triangle markers.")
        }
        if model.missingCount > 0 {
            lines.append("\(model.missingCount) season\(model.missingCount == 1 ? " has" : "s have") no \(metric.shortName) in the league's record and \(model.missingCount == 1 ? "is" : "are") left out rather than drawn as zero.")
        }
        if !showsEraLabels, !model.eraMarks.isEmpty {
            lines.append("Dashed rules: " + model.eraMarks.map { "\($0.seasonLabel) \($0.label)" }.joined(separator: " · "))
        }
        return lines
    }

    private var accessibilityText: String {
        var text = "\(metric.name) by \(model.usesAgeAxis ? "age" : "season") for \(payload.player.name)"
        if let first = model.regular.first, let last = model.regular.last {
            text += ", from \(first.displayValue) in \(first.seasonLabel) to \(last.displayValue) in \(last.seasonLabel)"
        }
        if let peak = model.peak {
            text += ", peaking at \(peak.label)"
        }
        if model.estimatedCount > 0 {
            text += ". \(model.estimatedCount) seasons are estimated, not measured"
        }
        return text
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            if model.isEmpty {
                HardwoodChartPlaceholder(icon: "chart.xyaxis.line",
                                         message: "No season of this career has a \(metric.name).",
                                         detail: "\(metric.name) goes back to \(metric.availability.seasonFrom).",
                                         availability: .unavailable,
                                         metricName: metric.name,
                                         season: payload.seasons.first?.season)
            } else {
                chart
                HardwoodChartLegend(items: legendItems)
                HardwoodChartFootnote(lines: footnoteLines)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
            Text(payload.player.name)
                .hardwoodText(.statLabel, color: Palette.textPrimary)
                .lineLimit(1)
            Text(metric.shortName)
                .hardwoodText(.caption)
            Spacer(minLength: Spacing.xs)
            Text("by \(axisCaption.lowercased())")
                .hardwoodText(.caption)
        }
        .accessibilityElement(children: .combine)
    }

    private var chart: some View {
        Chart {
            ForEach(model.lineMarks) { mark in
                LineMark(x: .value(axisCaption, mark.x),
                         y: .value(metric.shortName, mark.value),
                         series: .value("Run", mark.runID))
                    .foregroundStyle(mark.color)
                    .interpolationMethod(.monotone)
                    .lineStyle(StrokeStyle(lineWidth: mark.isPlayoffs ? 1.6 : 2.2,
                                           lineCap: .round,
                                           lineJoin: .round,
                                           dash: mark.isEstimated ? HardwoodChart.estimateDash : []))
            }
            ForEach(model.allPoints) { point in
                PointMark(x: .value(axisCaption, point.x),
                          y: .value(metric.shortName, point.value))
                    .foregroundStyle(Self.pointColor(for: point,
                                                     regular: regularColor,
                                                     playoff: playoffColor))
                    .symbol(Self.symbol(for: point.availability))
                    .symbolSize(point.isPlayoffs ? 26 : 34)
            }
            ForEach(model.eraMarks) { era in
                RuleMark(x: .value("Era boundary", era.x))
                    .foregroundStyle(Palette.textTertiary.opacity(0.8))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: HardwoodChart.ruleDash))
                    .annotation(position: .top, alignment: .leading, spacing: 0) {
                        if showsEraLabels {
                            Text(era.label)
                                .hardwoodText(.caption, color: Palette.textTertiary)
                                .lineLimit(1)
                                .fixedSize()
                        }
                    }
            }
            if let peak = model.peak {
                PointMark(x: .value(axisCaption, peak.x),
                          y: .value(metric.shortName, peak.value))
                    .foregroundStyle(Palette.selection)
                    .symbolSize(70)
                    .annotation(position: .top, alignment: .center, spacing: 2) {
                        Text(peak.label)
                            .font(Typography.tableHeaderMono)
                            .foregroundStyle(Palette.selection)
                            .lineLimit(1)
                            .fixedSize()
                    }
            }
        }
        .chartYScale(domain: model.yDomain)
        .chartYAxis { HardwoodChartAxis.metricValues(format: metric.format) }
        .chartXAxis { HardwoodChartAxis.labelled(values: model.xTicks, label: xAxisLabel) }
        .hardwoodChartStyle()
        .frame(height: chartHeight)
        .accessibilityLabel(accessibilityText)
    }

    /// Season ticks read `"2008-09"`; age ticks read as plain numbers.
    private var xAxisLabel: (Double) -> String {
        let usesAge = model.usesAgeAxis
        return { value in
            // The tick values are the widget's own, but `Int(_: Double)` traps on anything wild,
            // and these numbers started life as strings on the wire.
            guard value.isFinite, abs(value) < 100_000 else { return "" }
            let rounded = Int(value.rounded())
            return usesAge ? "\(rounded)" : HardwoodChart.seasonLabel(forStartYear: rounded)
        }
    }

    // MARK: Marker vocabulary

    /// An estimated season is drawn in the era-caveat color, so the marker carries the caveat
    /// even where the dash pattern is hard to see.
    private static func pointColor(for point: ArcPoint, regular: Color, playoff: Color) -> Color {
        if point.availability == .estimated { return Palette.warning }
        return point.isPlayoffs ? playoff : regular
    }

    private static func symbol(for availability: MetricAvailability) -> BasicChartSymbolShape {
        switch availability {
        case .full:        return .circle
        case .estimated:   return .diamond
        case .partial:     return .triangle
        case .unavailable: return .circle    // never plotted: a value that does not exist has no point
        }
    }

    // MARK: Model building

    private static func makeModel(payload: CareerArcPayload) -> Model {
        var model = Model()

        let wantsAge = payload.xAxis.lowercased() == "age"
        let hasAges = (payload.seasons + payload.playoffSeasons).contains { $0.age != nil }
        model.usesAgeAxis = wantsAge && hasAges

        var missing = 0
        model.regular = points(from: payload.seasons,
                               isPlayoffs: false,
                               usesAgeAxis: model.usesAgeAxis,
                               format: payload.metric.format,
                               missing: &missing)
        model.playoffs = points(from: payload.playoffSeasons,
                                isPlayoffs: true,
                                usesAgeAxis: model.usesAgeAxis,
                                format: payload.metric.format,
                                missing: &missing)
        model.missingCount = missing

        model.estimatedCount = model.regular.filter { $0.availability == .estimated }.count
        model.partialCount = model.regular.filter { $0.availability == .partial }.count

        model.lineMarks = lineMarks(for: model.regular,
                                    color: HardwoodChart.seriesColor(at: regularColorIndex),
                                    isPlayoffs: false,
                                    prefix: "rs")
        model.lineMarks += lineMarks(for: model.playoffs,
                                     color: HardwoodChart.seriesColor(at: playoffColorIndex),
                                     isPlayoffs: true,
                                     prefix: "po")

        let values = model.allPoints.map(\.value)
        var mustInclude: [Double] = []

        // The peak: prefer the season row the arc already holds, so the annotation lands exactly
        // on a drawn point.
        if let peak = payload.peak {
            let peakPoint = model.regular.first(where: { $0.seasonLabel == peak.season })
            if let peakPoint = peakPoint {
                let text = peak.displayValue ?? peakPoint.displayValue
                model.peak = PeakDatum(x: peakPoint.x,
                                       value: peakPoint.value,
                                       label: "Peak \(text)")
                mustInclude.append(peakPoint.value)
            } else if let value = peak.value, value.isFinite,
                      let x = position(forSeason: peak.season,
                                       usesAgeAxis: model.usesAgeAxis,
                                       anchor: anchor(in: model.regular)) {
                let text = peak.displayValue ?? HardwoodNumberFormat.string(value, format: payload.metric.format)
                model.peak = PeakDatum(x: x, value: value, label: "Peak \(text)")
                mustInclude.append(value)
            }
        }

        model.yDomain = HardwoodChart.yDomain(preferred: nil,
                                              metric: payload.metric,
                                              values: values,
                                              mustInclude: mustInclude)

        let xs = model.allPoints.map(\.x)
        model.xTicks = HardwoodChart.thinnedTicks(xs, count: 5)

        // Only the boundaries the career actually crosses are worth a rule; anything else would
        // stretch the axis into empty years.
        if let lowest = xs.min(), let highest = xs.max() {
            let careerAnchor = anchor(in: model.regular)
            model.eraMarks = payload.eraBoundaries.compactMap { boundary -> EraMarkDatum? in
                guard let x = position(forSeason: boundary.season,
                                       usesAgeAxis: model.usesAgeAxis,
                                       anchor: careerAnchor),
                      x >= lowest - 0.5, x <= highest + 0.5 else { return nil }
                return EraMarkDatum(id: boundary.season,
                                    x: x,
                                    seasonLabel: boundary.season,
                                    label: boundary.label)
            }
        }

        return model
    }

    /// The (season start year, age) pair that lets an era boundary be placed on an age axis: a
    /// player ages exactly one year per season, so one known pair fixes the whole mapping.
    private static func anchor(in points: [ArcPoint]) -> (year: Int, age: Int)? {
        for point in points {
            if let year = point.startYear, let age = point.age {
                return (year, age)
            }
        }
        return nil
    }

    private static func position(forSeason season: String,
                                 usesAgeAxis: Bool,
                                 anchor: (year: Int, age: Int)?) -> Double? {
        guard let year = HardwoodChart.startYear(ofSeason: season) else { return nil }
        guard usesAgeAxis else { return Double(year) }
        guard let anchor else { return nil }
        return Double(anchor.age + (year - anchor.year))
    }

    private static func points(from seasons: [CareerSeason],
                               isPlayoffs: Bool,
                               usesAgeAxis: Bool,
                               format: MetricFormat,
                               missing: inout Int) -> [ArcPoint] {
        var result: [ArcPoint] = []
        for season in seasons {
            let startYear = HardwoodChart.startYear(ofSeason: season.season)
            guard let value = season.value, value.isFinite, season.availability != .unavailable else {
                if !isPlayoffs { missing += 1 }
                continue
            }
            let x: Double?
            if usesAgeAxis {
                x = season.age.map { Double($0) }
            } else {
                x = startYear.map { Double($0) }
            }
            guard let x else {
                if !isPlayoffs { missing += 1 }
                continue
            }
            let display = season.displayValue ?? HardwoodNumberFormat.string(value, format: format)
            result.append(ArcPoint(id: "\(isPlayoffs ? "po" : "rs")|\(season.id)",
                                   x: x,
                                   value: value,
                                   seasonLabel: season.season,
                                   startYear: startYear,
                                   age: season.age,
                                   teamAbbr: season.teamAbbr,
                                   availability: season.availability,
                                   isPlayoffs: isPlayoffs,
                                   displayValue: display))
        }
        return result.sorted { $0.x < $1.x }
    }

    /// Splits a career into runs so the drawn line breaks at a missed season and changes to a
    /// dashed stroke exactly where the numbers stop being measured.
    private static func lineMarks(for points: [ArcPoint],
                                  color: Color,
                                  isPlayoffs: Bool,
                                  prefix: String) -> [LineMarkDatum] {
        var marks: [LineMarkDatum] = []
        guard points.count > 1 else { return marks }

        var runIndex = 0
        var previous: ArcPoint?

        for point in points {
            if let last = previous {
                let gap = point.x - last.x > 1.5
                let switchesEstimation = last.isEstimated != point.isEstimated
                if gap {
                    runIndex += 1
                } else if switchesEstimation {
                    runIndex += 1
                    // The joining point is repeated at the head of the new run, in the new run's
                    // style, so the two strokes meet instead of leaving a hole.
                    marks.append(LineMarkDatum(id: "\(prefix)|join|\(runIndex)|\(last.id)",
                                               runID: "\(prefix)#\(runIndex)",
                                               x: last.x,
                                               value: last.value,
                                               color: color,
                                               isEstimated: point.isEstimated,
                                               isPlayoffs: isPlayoffs))
                }
            }
            marks.append(LineMarkDatum(id: "\(prefix)|\(runIndex)|\(point.id)",
                                       runID: "\(prefix)#\(runIndex)",
                                       x: point.x,
                                       value: point.value,
                                       color: color,
                                       isEstimated: point.isEstimated,
                                       isPlayoffs: isPlayoffs))
            previous = point
        }
        return marks
    }
}

#if DEBUG
private enum CareerArcPreviewData {
    static let metric = MetricDescriptor(
        key: "per",
        name: "Player Efficiency Rating",
        shortName: "PER",
        category: "impact",
        format: .decimal1,
        higherIsBetter: true,
        scope: ["player"],
        availability: MetricDescriptor.Availability(seasonFrom: "1951-52",
                                                    perGameFrom: "1951-52",
                                                    seasonLevelOnly: true,
                                                    estimatedBefore: "1996-97"),
        domain: MetricDescriptor.Domain(min: 0, max: 35),
        glossary: "Hollinger's per-minute rating, pace and league adjusted to a league average of 15.00."
    )

    /// A career that straddles 1996-97: the early seasons are estimates, the later ones measured.
    static let acrossTheBoundary = CareerArcPayload(
        player: PlayerRef(playerId: 76_003, name: "Hakeem Olajuwon", firstName: "Hakeem", lastName: "Olajuwon",
                          teamId: 1_610_612_745, teamAbbr: "HOU", position: "C", jersey: "34",
                          headshotUrl: nil, isActive: false),
        metric: metric,
        xAxis: "season",
        seasons: [
            CareerSeason(season: "1993-94", seasonType: "Regular Season", age: 31, teamAbbr: "HOU", gp: 80,
                         value: 25.3, displayValue: "25.3", availability: .estimated),
            CareerSeason(season: "1994-95", seasonType: "Regular Season", age: 32, teamAbbr: "HOU", gp: 72,
                         value: 24.9, displayValue: "24.9", availability: .estimated),
            CareerSeason(season: "1995-96", seasonType: "Regular Season", age: 33, teamAbbr: "HOU", gp: 72,
                         value: 24.1, displayValue: "24.1", availability: .estimated),
            CareerSeason(season: "1996-97", seasonType: "Regular Season", age: 34, teamAbbr: "HOU", gp: 78,
                         value: 23.4, displayValue: "23.4", availability: .full),
            CareerSeason(season: "1997-98", seasonType: "Regular Season", age: 35, teamAbbr: "HOU", gp: 47,
                         value: 21.0, displayValue: "21.0", availability: .full),
            CareerSeason(season: "1998-99", seasonType: "Regular Season", age: 36, teamAbbr: "HOU", gp: 50,
                         value: nil, displayValue: Formatting.emDash, availability: .unavailable),
            CareerSeason(season: "1999-00", seasonType: "Regular Season", age: 37, teamAbbr: "HOU", gp: 44,
                         value: 16.2, displayValue: "16.2", availability: .full)
        ],
        playoffSeasons: [
            CareerSeason(season: "1993-94", seasonType: "Playoffs", age: 31, teamAbbr: "HOU", gp: 23,
                         value: 26.8, displayValue: "26.8", availability: .estimated),
            CareerSeason(season: "1994-95", seasonType: "Playoffs", age: 32, teamAbbr: "HOU", gp: 22,
                         value: 28.9, displayValue: "28.9", availability: .estimated),
            CareerSeason(season: "1996-97", seasonType: "Playoffs", age: 34, teamAbbr: "HOU", gp: 16,
                         value: 22.6, displayValue: "22.6", availability: .full)
        ],
        eraBoundaries: [
            EraBoundary(season: "1996-97", label: "Measured possessions begin", detail: nil)
        ],
        peak: CareerPeak(season: "1993-94", value: 25.3, displayValue: "25.3")
    )
}

#Preview("Career arc") {
    ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            Text("Large — by season").hardwoodText(.caption)
            CareerArcWidget(payload: .preview, size: .large)
                .hardwoodCard()
            Text("Estimated seasons, a missed season, playoffs overlaid").hardwoodText(.caption)
            CareerArcWidget(payload: CareerArcPreviewData.acrossTheBoundary, size: .large)
                .hardwoodCard()
            Text("Medium — by age").hardwoodText(.caption)
            CareerArcWidget(payload: CareerArcPayload(
                player: CareerArcPreviewData.acrossTheBoundary.player,
                metric: CareerArcPreviewData.metric,
                xAxis: "age",
                seasons: CareerArcPreviewData.acrossTheBoundary.seasons,
                playoffSeasons: [],
                eraBoundaries: CareerArcPreviewData.acrossTheBoundary.eraBoundaries,
                peak: CareerArcPreviewData.acrossTheBoundary.peak), size: .medium)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
