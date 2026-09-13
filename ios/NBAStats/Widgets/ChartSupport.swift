import Charts
import Foundation
import SwiftUI

// MARK: - Chart vocabulary

/// Shared plumbing for the charted widgets: the series ramp, the formatter bridge that turns a
/// raw metric value into an axis label, the domain arithmetic, and the date parsing every
/// payload forces on us.
///
/// Payload points carry ISO calendar **strings**, not `Date`s, because the API speaks the
/// league's scheduling day. Parsing happens here, once, and a string that does not parse yields
/// `nil` so the caller can drop that point. Nothing in this file ever traps on bad data.
public enum HardwoodChart {

    // MARK: Constants

    /// The dash pattern for a reference rule (league average, era boundary).
    public static let ruleDash: [CGFloat] = [4, 3]
    /// The dash pattern that marks a stretch of estimated — not measured — values.
    public static let estimateDash: [CGFloat] = [5, 4]
    /// How strongly grid lines read against the plot background.
    public static let gridOpacity: Double = 0.55
    /// The opacity raw per-game marks are drawn at, under an emphasized rolling average.
    public static let rawMarkOpacity: Double = 0.35

    // MARK: Dates

    /// Parses a payload date string such as `"2026-01-02"` in the league's own time zone.
    ///
    /// Returns `nil` rather than substituting `Date()`: a point whose date cannot be read is
    /// dropped by the caller, never plotted at "now".
    public static func date(from string: String?) -> Date? {
        Formatting.parseDate(string)
    }

    /// `"2026-01-02"` becomes `"Jan 2"`, for an axis tick or a readout.
    public static func shortDate(_ date: Date?) -> String {
        guard let date else { return HardwoodNumberFormat.missing }
        return date.formatted(.dateTime.month(.abbreviated).day())
    }

    // MARK: Series ramp

    /// The categorical ramp every charted widget draws its series from.
    public static var seriesRamp: [Color] { Palette.chartSeries }

    /// The ramp entry for a series index, wrapping rather than trapping on an index the server
    /// invented.
    public static func seriesColor(at index: Int) -> Color {
        Palette.chartColor(at: index)
    }

    // MARK: Formatter bridge

    /// An axis tick for a raw value in a metric's native unit.
    ///
    /// `compact` drops the decimals a tick does not have room for, so a `percent1` metric reads
    /// `"62%"` on the axis while the same number reads `"61.5%"` in a readout.
    public static func axisLabel(_ value: Double?, format: MetricFormat, compact: Bool = true) -> String {
        guard let value, value.isFinite else { return HardwoodNumberFormat.missing }
        guard compact else { return HardwoodNumberFormat.string(value, format: format) }
        switch format {
        case .integer:
            return decimalString(value, fractionDigits: 0, signed: false)
        case .decimal1, .minutes:
            return decimalString(value, fractionDigits: abs(value) >= 100 ? 0 : 1, signed: false)
        case .decimal2:
            return decimalString(value, fractionDigits: abs(value) >= 100 ? 0 : 2, signed: false)
        case .percent1, .percent2:
            return decimalString(value * 100, fractionDigits: 0, signed: false) + "%"
        case .rating1:
            return decimalString(value, fractionDigits: abs(value) >= 100 ? 0 : 1, signed: false)
        case .plusMinus1:
            return decimalString(value, fractionDigits: 1, signed: true)
        }
    }

    /// The full-precision rendering of a value, with an em dash for a value that does not exist.
    public static func valueLabel(_ value: Double?, format: MetricFormat) -> String {
        HardwoodNumberFormat.string(value, format: format)
    }

    private static func decimalString(_ value: Double, fractionDigits: Int, signed: Bool) -> String {
        let style = FloatingPointFormatStyle<Double>()
            .precision(.fractionLength(max(fractionDigits, 0)))
            .sign(strategy: signed ? .always(includingZero: false) : .automatic)
        return value.formatted(style)
    }

    // MARK: Domains

    /// A padded range covering `values`, never degenerate.
    public static func paddedDomain(for values: [Double],
                                    fallback: ClosedRange<Double> = 0...1,
                                    padding: Double = 0.08) -> ClosedRange<Double> {
        let finite = values.filter { $0.isFinite }
        guard let low = finite.min(), let high = finite.max() else { return fallback }
        if high - low <= 1e-9 {
            let pad = max(abs(high) * 0.1, 0.5)
            return (low - pad)...(high + pad)
        }
        let pad = (high - low) * max(padding, 0)
        return (low - pad)...(high + pad)
    }

    /// The y range a value chart should use.
    ///
    /// The server's suggested domain wins when it has one — it was computed from the same rows —
    /// then the data itself, then the metric's declared domain.
    public static func yDomain(preferred: MetricDescriptor.Domain?,
                               metric: MetricDescriptor,
                               values: [Double],
                               mustInclude extra: [Double] = []) -> ClosedRange<Double> {
        if let preferred, preferred.span > 1e-9 {
            return preferred.min...preferred.max
        }
        let combined = (values + extra).filter { $0.isFinite }
        if !combined.isEmpty {
            return paddedDomain(for: combined)
        }
        if let domain = metric.domain, domain.span > 1e-9 {
            return domain.min...domain.max
        }
        return 0...1
    }

    // MARK: Seasons

    /// `"1996-97"` becomes `1996`; a token such as `"latest"` returns `nil`.
    public static func startYear(ofSeason season: String?) -> Int? {
        Formatting.seasonStartYear(season)
    }

    /// `1996` becomes `"1996-97"`, `1999` becomes `"1999-00"`.
    public static func seasonLabel(forStartYear year: Int) -> String {
        let tail = (((year + 1) % 100) + 100) % 100
        let padded = tail < 10 ? "0\(tail)" : "\(tail)"
        return "\(year)-\(padded)"
    }

    /// Thins a list of x positions down to at most `count` ticks, keeping the first and last.
    public static func thinnedTicks(_ values: [Double], count: Int) -> [Double] {
        let sorted = Array(Set(values.filter { $0.isFinite })).sorted()
        guard sorted.count > max(count, 2) else { return sorted }
        let step = Double(sorted.count - 1) / Double(max(count - 1, 1))
        var picked: [Double] = []
        var index = 0
        while index < count {
            let position = Int((Double(index) * step).rounded())
            if position >= 0, position < sorted.count {
                let value = sorted[position]
                if picked.last != value { picked.append(value) }
            }
            index += 1
        }
        if let last = sorted.last, picked.last != last { picked.append(last) }
        return picked
    }
}

// MARK: - Axis styles

/// The axis treatments every charted widget shares, so a tick in one tile looks like a tick in
/// the next one.
public enum HardwoodChartAxis {

    /// A quantitative axis whose ticks are rendered through the metric's own format.
    @AxisContentBuilder
    public static func metricValues(format: MetricFormat,
                                    position: AxisMarkPosition = .leading,
                                    desiredCount: Int = 4) -> some AxisContent {
        AxisMarks(position: position, values: .automatic(desiredCount: desiredCount)) { mark in
            AxisGridLine()
                .foregroundStyle(Palette.separator.opacity(HardwoodChart.gridOpacity))
            AxisTick()
                .foregroundStyle(Palette.separator)
            AxisValueLabel {
                Text(HardwoodChart.axisLabel(mark.as(Double.self), format: format))
                    .hardwoodText(.tableHeader, color: Palette.textTertiary)
            }
        }
    }

    /// A date axis, labelled `"Jan 2"`.
    @AxisContentBuilder
    public static func dates(position: AxisMarkPosition = .bottom, desiredCount: Int = 4) -> some AxisContent {
        AxisMarks(position: position, values: .automatic(desiredCount: desiredCount)) { mark in
            AxisGridLine()
                .foregroundStyle(Palette.separator.opacity(0.3))
            AxisValueLabel {
                Text(HardwoodChart.shortDate(mark.as(Date.self)))
                    .hardwoodText(.tableHeader, color: Palette.textTertiary)
            }
        }
    }

    /// A categorical axis: the plottable value is shown verbatim.
    @AxisContentBuilder
    public static func categories(position: AxisMarkPosition = .leading) -> some AxisContent {
        AxisMarks(position: position) { mark in
            AxisValueLabel {
                Text(mark.as(String.self) ?? "")
                    .hardwoodText(.tableHeader, color: Palette.textSecondary)
                    .lineLimit(1)
            }
        }
    }

    /// A quantitative axis with hand-picked ticks, labelled by the caller — used where the x
    /// position is a season start year or an age rather than a measurement.
    @AxisContentBuilder
    public static func labelled(values: [Double],
                                position: AxisMarkPosition = .bottom,
                                label: @escaping (Double) -> String) -> some AxisContent {
        AxisMarks(position: position, values: values) { mark in
            AxisGridLine()
                .foregroundStyle(Palette.separator.opacity(0.3))
            AxisValueLabel {
                Text(mark.as(Double.self).map(label) ?? "")
                    .hardwoodText(.tableHeader, color: Palette.textTertiary)
                    .lineLimit(1)
            }
        }
    }
}

// MARK: - Chart chrome

/// The plot surface and legend behaviour shared by every chart in the app. Hardwood always draws
/// its own legend, so the built-in one is suppressed.
public struct HardwoodChartStyleModifier: ViewModifier {
    private let showsPlotBackground: Bool

    public init(showsPlotBackground: Bool = true) {
        self.showsPlotBackground = showsPlotBackground
    }

    public func body(content: Content) -> some View {
        content
            .chartLegend(.hidden)
            .chartPlotStyle { plot in
                plot.background(showsPlotBackground ? Palette.surfaceSunken.opacity(0.55) : Color.clear)
            }
    }
}

public extension View {
    /// Applies the shared plot surface and hides the built-in legend.
    func hardwoodChartStyle(showsPlotBackground: Bool = true) -> some View {
        modifier(HardwoodChartStyleModifier(showsPlotBackground: showsPlotBackground))
    }
}

// MARK: - Legend

/// The swatch shape a legend entry draws.
public enum HardwoodChartSymbol: String, Hashable, CaseIterable {
    case line
    case dashedLine
    case filledDot
    case hollowDot
    case square
}

/// One entry of a chart legend.
public struct HardwoodChartLegendItem: Identifiable, Hashable {
    public let id: String
    public let label: String
    public let color: Color
    public let symbol: HardwoodChartSymbol
    public let detail: String?

    public init(id: String,
                label: String,
                color: Color,
                symbol: HardwoodChartSymbol = .line,
                detail: String? = nil) {
        self.id = id
        self.label = label
        self.color = color
        self.symbol = symbol
        self.detail = detail
    }

    /// Convenience for a series that names its slot in the categorical ramp.
    public init(id: String,
                label: String,
                colorIndex: Int,
                symbol: HardwoodChartSymbol = .line,
                detail: String? = nil) {
        self.init(id: id,
                  label: label,
                  color: HardwoodChart.seriesColor(at: colorIndex),
                  symbol: symbol,
                  detail: detail)
    }
}

/// A compact legend that lays out in a row when it fits and stacks when it does not.
public struct HardwoodChartLegend: View {
    private let items: [HardwoodChartLegendItem]

    public init(items: [HardwoodChartLegendItem]) {
        self.items = items
    }

    private var accessibilityText: String {
        let names = items.map { item -> String in
            guard let detail = item.detail else { return item.label }
            return "\(item.label), \(detail)"
        }
        return "Legend: " + names.joined(separator: ", ")
    }

    public var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: Spacing.md) {
                ForEach(items) { item in
                    entry(item)
                }
            }
            VStack(alignment: .leading, spacing: Spacing.xs) {
                ForEach(items) { item in
                    entry(item)
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
    }

    private func entry(_ item: HardwoodChartLegendItem) -> some View {
        HStack(spacing: Spacing.xs) {
            swatch(item)
            Text(item.label)
                .hardwoodText(.caption, color: Palette.textSecondary)
                .lineLimit(1)
            if let detail = item.detail {
                Text(detail)
                    .hardwoodText(.caption, color: Palette.textTertiary)
                    .lineLimit(1)
            }
        }
        .fixedSize(horizontal: true, vertical: false)
    }

    @ViewBuilder private func swatch(_ item: HardwoodChartLegendItem) -> some View {
        switch item.symbol {
        case .line:
            Capsule(style: .continuous)
                .fill(item.color)
                .frame(width: 14, height: 3)
        case .dashedLine:
            HStack(spacing: 2) {
                Capsule(style: .continuous).fill(item.color).frame(width: 5, height: 3)
                Capsule(style: .continuous).fill(item.color).frame(width: 5, height: 3)
            }
            .frame(width: 14, alignment: .leading)
        case .filledDot:
            Circle()
                .fill(item.color)
                .frame(width: 8, height: 8)
        case .hollowDot:
            Circle()
                .strokeBorder(item.color, lineWidth: 1.5)
                .frame(width: 9, height: 9)
        case .square:
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .fill(item.color)
                .frame(width: 9, height: 9)
        }
    }
}

// MARK: - Placeholder

/// What a charted widget renders instead of an empty plot: an explanation.
///
/// A chart with no marks in it looks like a bug, and an era with no data is not a bug — so the
/// tile says what is missing and, when the caller passes an availability, offers the explainer.
public struct HardwoodChartPlaceholder: View {
    private let icon: String
    private let message: String
    private let detail: String?
    private let availability: MetricAvailability?
    private let metricName: String?
    private let season: String?
    private let minHeight: CGFloat

    public init(icon: String = "chart.xyaxis.line",
                message: String,
                detail: String? = nil,
                availability: MetricAvailability? = nil,
                metricName: String? = nil,
                season: String? = nil,
                minHeight: CGFloat = 96) {
        self.icon = icon
        self.message = message
        self.detail = detail
        self.availability = availability
        self.metricName = metricName
        self.season = season
        self.minHeight = minHeight
    }

    public var body: some View {
        VStack(spacing: Spacing.sm) {
            Image(systemName: icon)
                .font(.title3)
                .foregroundStyle(Palette.textTertiary)
                .accessibilityHidden(true)
            Text(message)
                .hardwoodText(.tableCell, color: Palette.textSecondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            if let detail {
                Text(detail)
                    .hardwoodText(.caption)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let availability {
                AvailabilityBadge(availability: availability,
                                  showsText: true,
                                  isInteractive: true,
                                  metricName: metricName,
                                  season: season,
                                  notes: [message])
            }
        }
        .frame(maxWidth: .infinity, minHeight: minHeight)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(detail.map { "\(message) \($0)" } ?? message)
    }
}

// MARK: - Footnote

/// The small print under a chart: what the dashes mean, which numbers are estimates, which rows
/// the era never recorded.
public struct HardwoodChartFootnote: View {
    private let lines: [String]
    private let icon: String?

    public init(lines: [String], icon: String? = nil) {
        self.lines = lines.filter { !$0.isEmpty }
        self.icon = icon
    }

    public init(_ line: String, icon: String? = nil) {
        self.init(lines: [line], icon: icon)
    }

    public var body: some View {
        if lines.isEmpty {
            EmptyView()
        } else {
            VStack(alignment: .leading, spacing: 1) {
                ForEach(lines, id: \.self) { line in
                    HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
                        if let icon {
                            Image(systemName: icon)
                                .imageScale(.small)
                                .foregroundStyle(Palette.textTertiary)
                                .accessibilityHidden(true)
                        }
                        Text(line)
                            .hardwoodText(.caption)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .combine)
        }
    }
}

#if DEBUG
private struct ChartSupportPreviewPoint: Identifiable {
    let id: Int
    let date: Date
    let value: Double
}

#Preview("Chart support") {
    let base = Date(timeIntervalSince1970: 1_767_312_000)
    let samples: [ChartSupportPreviewPoint] = (0..<8).map { index in
        ChartSupportPreviewPoint(id: index,
                                 date: base.addingTimeInterval(Double(index) * 86_400 * 2),
                                 value: 0.52 + Double(index % 4) * 0.03)
    }
    let legend: [HardwoodChartLegendItem] = [
        HardwoodChartLegendItem(id: "a", label: "LeBron James", colorIndex: 0),
        HardwoodChartLegendItem(id: "b", label: "Luka Doncic", colorIndex: 1),
        HardwoodChartLegendItem(id: "c", label: "Estimated", color: Palette.warning, symbol: .dashedLine),
        HardwoodChartLegendItem(id: "d", label: "League average", color: Palette.textTertiary, symbol: .dashedLine)
    ]

    return ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text("Axis styles and ramp").hardwoodText(.sectionTitle)
                Chart(samples) { sample in
                    LineMark(x: .value("Date", sample.date), y: .value("TS%", sample.value))
                        .foregroundStyle(HardwoodChart.seriesColor(at: 0))
                        .interpolationMethod(.monotone)
                }
                .chartYScale(domain: HardwoodChart.paddedDomain(for: samples.map(\.value)))
                .chartYAxis { HardwoodChartAxis.metricValues(format: .percent1) }
                .chartXAxis { HardwoodChartAxis.dates() }
                .hardwoodChartStyle()
                .frame(height: 160)
                HardwoodChartLegend(items: legend)
                HardwoodChartFootnote(lines: [
                    "Axis ticks come from the metric's own format: \(HardwoodChart.axisLabel(0.615, format: .percent1)).",
                    "Season labels: \(HardwoodChart.seasonLabel(forStartYear: 1999)) · \(HardwoodChart.seasonLabel(forStartYear: 2025))."
                ])
            }
            .hardwoodCard()

            HardwoodChartPlaceholder(icon: "clock.arrow.circlepath",
                                     message: "Shot locations were not recorded before 1996-97.",
                                     detail: "There is nothing to plot for the 1961-62 season.",
                                     availability: .unavailable,
                                     metricName: "Shot profile",
                                     season: "1961-62")
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
