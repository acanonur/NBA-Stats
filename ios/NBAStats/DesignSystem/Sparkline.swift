import Foundation
import SwiftUI

/// A cheap line/area sparkline drawn straight into a `Path`.
///
/// Swift Charts is the right tool for a full chart widget, but a small tile may hold one of
/// these per row, so this stays deliberately simple: no axes, no scales, no chart engine.
///
/// Three cases the league's data forces on us are handled explicitly:
/// * fewer than two readable points — a single dot, or nothing at all;
/// * every value identical — a flat line through the middle rather than a divide by zero;
/// * `nil` gaps — the line **breaks**. A missed game is not a zero and is not interpolated
///   through, because either would invent a performance that never happened.
public struct Sparkline: View {
    private let points: [Double?]
    private let tint: Color
    private let baseline: Double?
    private let showsArea: Bool
    private let showsLastPoint: Bool
    private let lineWidth: CGFloat
    private let accessibilityDescription: String?

    public init(points: [Double?],
                tint: Color,
                baseline: Double? = nil,
                showsArea: Bool = true,
                showsLastPoint: Bool = true,
                lineWidth: CGFloat = 1.5,
                accessibilityDescription: String? = nil) {
        self.points = points
        self.tint = tint
        self.baseline = baseline
        self.showsArea = showsArea
        self.showsLastPoint = showsLastPoint
        self.lineWidth = lineWidth
        self.accessibilityDescription = accessibilityDescription
    }

    /// The resolved geometry for one render pass.
    private struct Resolved {
        /// Runs of consecutive non-nil points. A run of one is drawn as a dot.
        var segments: [[CGPoint]] = []
        var baselineY: CGFloat? = nil
        var lastPoint: CGPoint? = nil
    }

    private func resolve(in size: CGSize) -> Resolved {
        var resolved = Resolved()
        guard size.width > 0, size.height > 0, !points.isEmpty else { return resolved }

        let values = points.compactMap { $0 }.filter { $0.isFinite }
        guard let rawMin = values.min(), let rawMax = values.max() else { return resolved }

        var lower = rawMin
        var upper = rawMax
        if let baseline, baseline.isFinite {
            lower = min(lower, baseline)
            upper = max(upper, baseline)
        }

        let inset = lineWidth / 2 + 1
        let usableHeight = max(size.height - inset * 2, 1)
        let range = upper - lower
        let isFlat = range <= 1e-12

        func y(_ value: Double) -> CGFloat {
            guard !isFlat else { return size.height / 2 }
            let fraction = (value - lower) / range
            return inset + usableHeight * CGFloat(1 - fraction)
        }

        func x(_ index: Int) -> CGFloat {
            guard points.count > 1 else { return size.width / 2 }
            return size.width * CGFloat(index) / CGFloat(points.count - 1)
        }

        var run: [CGPoint] = []
        for (index, value) in points.enumerated() {
            if let value, value.isFinite {
                run.append(CGPoint(x: x(index), y: y(value)))
            } else if !run.isEmpty {
                resolved.segments.append(run)
                run = []
            }
        }
        if !run.isEmpty { resolved.segments.append(run) }

        if let baseline, baseline.isFinite {
            resolved.baselineY = y(baseline)
        }
        resolved.lastPoint = resolved.segments.last?.last
        return resolved
    }

    private func linePath(_ resolved: Resolved) -> Path {
        var path = Path()
        for segment in resolved.segments where segment.count > 1 {
            path.addLines(segment)
        }
        return path
    }

    private func areaPath(_ resolved: Resolved, in size: CGSize) -> Path {
        var path = Path()
        for segment in resolved.segments where segment.count > 1 {
            guard let first = segment.first, let last = segment.last else { continue }
            // Built point by point rather than with `addLines`, which would start a fresh
            // subpath and leave the left edge of the fill open.
            path.move(to: CGPoint(x: first.x, y: size.height))
            path.addLine(to: first)
            for point in segment.dropFirst() {
                path.addLine(to: point)
            }
            path.addLine(to: CGPoint(x: last.x, y: size.height))
            path.closeSubpath()
        }
        return path
    }

    /// Isolated points — a single game between two missed ones — still deserve a mark.
    private func dots(_ resolved: Resolved) -> [CGPoint] {
        resolved.segments.filter { $0.count == 1 }.compactMap { $0.first }
    }

    private var derivedAccessibilityLabel: String {
        if let accessibilityDescription { return accessibilityDescription }
        let values = points.compactMap { $0 }.filter { $0.isFinite }
        guard let first = values.first, let last = values.last else {
            return "Sparkline, no data"
        }
        let missing = points.count - values.count
        let format = FloatingPointFormatStyle<Double>().precision(.fractionLength(0...2))
        let direction: String
        if abs(last - first) <= 1e-9 {
            direction = "flat"
        } else {
            direction = last > first ? "trending up" : "trending down"
        }
        var text = "Sparkline over \(points.count) games, \(direction), from \(first.formatted(format)) to \(last.formatted(format))"
        if missing > 0 {
            text += ", \(missing) with no value"
        }
        return text
    }

    public var body: some View {
        GeometryReader { proxy in
            let resolved = resolve(in: proxy.size)
            let isolated = dots(resolved)
            ZStack {
                if showsArea {
                    areaPath(resolved, in: proxy.size)
                        .fill(
                            LinearGradient(gradient: Gradient(colors: [tint.opacity(0.28),
                                                                      tint.opacity(0.02)]),
                                           startPoint: .top,
                                           endPoint: .bottom)
                        )
                }
                if let baselineY = resolved.baselineY {
                    Path { path in
                        path.move(to: CGPoint(x: 0, y: baselineY))
                        path.addLine(to: CGPoint(x: proxy.size.width, y: baselineY))
                    }
                    .stroke(Palette.textTertiary,
                            style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                }
                linePath(resolved)
                    .stroke(tint, style: StrokeStyle(lineWidth: lineWidth,
                                                     lineCap: .round,
                                                     lineJoin: .round))
                ForEach(isolated.indices, id: \.self) { index in
                    Circle()
                        .fill(tint)
                        .frame(width: lineWidth * 2, height: lineWidth * 2)
                        .position(isolated[index])
                }
                if showsLastPoint, let last = resolved.lastPoint {
                    Circle()
                        .fill(tint)
                        .frame(width: lineWidth * 2.4, height: lineWidth * 2.4)
                        .position(last)
                }
            }
        }
        .frame(minHeight: 18)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(derivedAccessibilityLabel)
    }
}

#if DEBUG
private struct SparklineSample: Identifiable {
    let id: String
    let colorIndex: Int
    let points: [Double?]
}

#Preview("Sparklines") {
    let samples: [SparklineSample] = [
        SparklineSample(id: "Rising", colorIndex: 0,
                        points: [0.48, 0.51, 0.49, 0.55, 0.58, 0.57, 0.62, 0.64]),
        SparklineSample(id: "Gaps break the line", colorIndex: 1,
                        points: [0.48, 0.51, nil, nil, 0.58, 0.57, nil, 0.64]),
        SparklineSample(id: "All equal", colorIndex: 2,
                        points: [0.55, 0.55, 0.55, 0.55, 0.55]),
        SparklineSample(id: "One point", colorIndex: 3,
                        points: [nil, 0.61, nil]),
        SparklineSample(id: "No data", colorIndex: 4,
                        points: [nil, nil, nil])
    ]

    return ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            ForEach(samples) { sample in
                VStack(alignment: .leading, spacing: Spacing.xs) {
                    Text(sample.id).hardwoodText(.statLabel)
                    Sparkline(points: sample.points,
                              tint: Palette.chartColor(at: sample.colorIndex),
                              baseline: 0.55)
                        .frame(height: 44)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .hardwoodCard()
            }
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
