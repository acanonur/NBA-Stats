import Foundation
import SwiftUI

/// A 0-100 percentile track with a marker.
///
/// Meaning is carried by **position and label**, never by hue alone: the numeric label is on by
/// default, the quartile ticks give the eye a fixed reference, and the marker is a high-contrast
/// bar with an outline so it reads on any tint.
public struct PercentileBar: View {
    private let percentile: Double?
    private let tint: Color
    private let showsLabel: Bool
    private let trackHeight: CGFloat

    public init(percentile: Double?,
                tint: Color,
                showsLabel: Bool = true,
                trackHeight: CGFloat = 8) {
        self.percentile = percentile
        self.tint = tint
        self.showsLabel = showsLabel
        self.trackHeight = trackHeight
    }

    /// The API sends percentiles as fractions in `[0, 1]`; a caller holding an already-scaled
    /// 0-100 number should not silently peg to the top, so both are accepted.
    private var fraction: Double? {
        guard let percentile, percentile.isFinite else { return nil }
        let normalized = percentile > 1 ? percentile / 100 : percentile
        return min(max(normalized, 0), 1)
    }

    private var rounded: Int? {
        guard let fraction else { return nil }
        return Int((fraction * 100).rounded())
    }

    private var labelText: String {
        guard let rounded else { return HardwoodNumberFormat.missing }
        return HardwoodNumberFormat.ordinal(rounded)
    }

    private var accessibilityText: String {
        guard let rounded else { return "Percentile not available" }
        return "\(HardwoodNumberFormat.ordinal(rounded)) percentile"
    }

    private var markerHeight: CGFloat { trackHeight + 8 }

    public var body: some View {
        HStack(spacing: Spacing.sm) {
            GeometryReader { proxy in
                let width = max(proxy.size.width, 1)
                let quartiles: [Double] = [0.25, 0.5, 0.75]
                ZStack(alignment: .leading) {
                    Capsule(style: .continuous)
                        .fill(Palette.track)
                        .frame(height: trackHeight)
                    ForEach(quartiles, id: \.self) { stop in
                        Rectangle()
                            .fill(Palette.separator)
                            .frame(width: 1, height: trackHeight)
                            .offset(x: width * CGFloat(stop))
                    }
                    if let fraction {
                        Capsule(style: .continuous)
                            .fill(tint.opacity(0.45))
                            .frame(width: max(width * CGFloat(fraction), trackHeight), height: trackHeight)
                        RoundedRectangle(cornerRadius: 1.5, style: .continuous)
                            .fill(tint)
                            .overlay(
                                RoundedRectangle(cornerRadius: 1.5, style: .continuous)
                                    .strokeBorder(Palette.surface, lineWidth: 1)
                            )
                            .frame(width: 3, height: markerHeight)
                            .offset(x: min(max(width * CGFloat(fraction) - 1.5, 0), width - 3))
                    }
                }
                .frame(height: markerHeight, alignment: .center)
            }
            .frame(height: markerHeight)
            if showsLabel {
                Text(labelText)
                    .font(Typography.tableHeaderMono)
                    .foregroundStyle(fraction == nil ? Palette.textTertiary : Palette.textPrimary)
                    .frame(minWidth: 34, alignment: .trailing)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
    }
}

/// A single four-factors bar: the team's value inside a fixed domain, with the league average
/// marked as a separate tick.
///
/// The league tick is a separate vertical rule in the primary text color rather than a second
/// hue, so the comparison survives both a colorblind reader and a grayscale screenshot. Set
/// `showsLegend` on just one bar in a stack of them to avoid repeating the caption.
public struct FactorBar: View {
    private let value: Double
    private let leagueAverage: Double?
    private let domain: ClosedRange<Double>
    private let tint: Color
    private let label: String?
    private let valueText: String?
    private let showsLegend: Bool
    private let barHeight: CGFloat

    public init(value: Double,
                leagueAverage: Double? = nil,
                domain: ClosedRange<Double>,
                tint: Color,
                label: String? = nil,
                valueText: String? = nil,
                showsLegend: Bool = true,
                barHeight: CGFloat = 10) {
        self.value = value
        self.leagueAverage = leagueAverage
        self.domain = domain
        self.tint = tint
        self.label = label
        self.valueText = valueText
        self.showsLegend = showsLegend
        self.barHeight = barHeight
    }

    private var span: Double { domain.upperBound - domain.lowerBound }

    private func fraction(_ raw: Double?) -> Double? {
        guard let raw, raw.isFinite, span > 1e-12 else { return nil }
        return min(max((raw - domain.lowerBound) / span, 0), 1)
    }

    private var accessibilityText: String {
        var parts: [String] = []
        if let label { parts.append(label) }
        parts.append(valueText ?? HardwoodNumberFormat.string(value, format: .decimal2))
        if let leagueAverage {
            let league = HardwoodNumberFormat.string(leagueAverage, format: .decimal2)
            parts.append(value >= leagueAverage ? "above the league average of \(league)"
                                                : "below the league average of \(league)")
        }
        return parts.joined(separator: ", ")
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            if label != nil || valueText != nil {
                HStack(spacing: Spacing.sm) {
                    if let label {
                        Text(label).hardwoodText(.statLabel)
                    }
                    Spacer(minLength: Spacing.xs)
                    if let valueText {
                        Text(valueText)
                            .font(Typography.statValueMono)
                            .foregroundStyle(Palette.textPrimary)
                    }
                }
            }
            GeometryReader { proxy in
                let width = max(proxy.size.width, 1)
                ZStack(alignment: .leading) {
                    Capsule(style: .continuous)
                        .fill(Palette.track)
                        .frame(height: barHeight)
                    if let filled = fraction(value) {
                        Capsule(style: .continuous)
                            .fill(tint)
                            .frame(width: max(width * CGFloat(filled), barHeight), height: barHeight)
                    }
                    if let average = fraction(leagueAverage) {
                        Rectangle()
                            .fill(Palette.textPrimary)
                            .frame(width: 1.5, height: barHeight + 6)
                            .offset(x: min(max(width * CGFloat(average) - 0.75, 0), width - 1.5))
                            .accessibilityHidden(true)
                    }
                }
                .frame(height: barHeight + 6, alignment: .center)
            }
            .frame(height: barHeight + 6)
            if showsLegend, leagueAverage != nil {
                Text("Vertical rule marks the league average")
                    .hardwoodText(.caption)
                    .accessibilityHidden(true)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
    }
}

#if DEBUG
#Preview("Percentile bars") {
    VStack(alignment: .leading, spacing: Spacing.md) {
        let samples: [Double] = [0.93, 0.5, 0.08, 1.0, 0.0]
        ForEach(samples.indices, id: \.self) { index in
            PercentileBar(percentile: samples[index], tint: Palette.chartColor(at: index))
        }
        PercentileBar(percentile: nil, tint: Palette.neutral)
        PercentileBar(percentile: 72, tint: Palette.chartColor(at: 2))
    }
    .padding(Spacing.lg)
    .hardwoodCard()
    .padding(Spacing.lg)
    .hardwoodBackground()
}

#Preview("Four factors") {
    VStack(alignment: .leading, spacing: Spacing.lg) {
        FactorBar(value: 0.556,
                  leagueAverage: 0.538,
                  domain: 0.45...0.62,
                  tint: Palette.chartColor(at: 0),
                  label: "eFG%",
                  valueText: "55.6%")
        FactorBar(value: 0.121,
                  leagueAverage: 0.134,
                  domain: 0.08...0.18,
                  tint: Palette.chartColor(at: 1),
                  label: "TOV%",
                  valueText: "12.1%",
                  showsLegend: false)
        FactorBar(value: 0.268,
                  leagueAverage: 0.271,
                  domain: 0.18...0.36,
                  tint: Palette.chartColor(at: 2),
                  label: "OREB%",
                  valueText: "26.8%",
                  showsLegend: false)
        FactorBar(value: 0.241,
                  domain: 0.14...0.34,
                  tint: Palette.chartColor(at: 3),
                  label: "FT Rate",
                  valueText: "0.241")
    }
    .padding(Spacing.lg)
    .hardwoodCard()
    .padding(Spacing.lg)
    .hardwoodBackground()
}
#endif
