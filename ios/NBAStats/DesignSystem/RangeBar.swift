import Foundation
import SwiftUI

/// One projection drawn as a range: the band, the dot, and the mark it is read against.
///
/// This is artboard 2b of the `Hardwood Predictions` handoff, with one substitution that is the
/// whole point of `docs/BROADSHEET.md` §1: **the tick is the player's own season average, not a
/// sportsbook line.** Nothing in this view knows what odds are, and nothing should teach it.
///
/// ```
///   ├─────────────────────────────────────────────┤   baseline rule
///             ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓                 band            ← p10…p90
///                     ●                                dot            ← projection
///                  │                                   tick           ← season average
/// ```
///
/// The band is the 80% predictive interval the engine publishes, which *is* the 10th-to-90th
/// percentile — so the design's own caption is accurate for this data, with the correction that
/// ours is analytic rather than simulated. The caller supplies that caption; this view draws.
public struct RangeBar: View {

    /// Everything one bar needs, resolved before it reaches the geometry.
    public struct Model: Equatable {
        /// The band's bounds — the 10th and 90th percentile.
        public var low: Double
        public var high: Double
        /// The projection, drawn as the dot. Clamped into the band when it sits outside.
        public var projection: Double
        /// The mark, drawn as the tick. `nil` draws no tick, which is the `reference: "none"` case.
        public var reference: Double?

        public init(low: Double, high: Double, projection: Double, reference: Double? = nil) {
            self.low = low
            self.high = high
            self.projection = projection
            self.reference = reference
        }

        /// A bar can only be drawn when the axis has width and every value is a real number.
        public var isDrawable: Bool {
            low.isFinite && high.isFinite && projection.isFinite && high > low
        }

        /// The axis the bar is drawn on.
        ///
        /// It is the band padded by a tenth of its width on each side, widened further if the
        /// reference mark falls outside — a tick drawn hard against the edge, or worse clamped
        /// invisibly onto it, would hide exactly the case the reader most wants to see.
        public var axis: ClosedRange<Double> {
            let padding = max((high - low) * 0.1, 0.5)
            var lower = low - padding
            var upper = high + padding
            if let reference = reference, reference.isFinite {
                lower = Swift.min(lower, reference - padding * 0.5)
                upper = Swift.max(upper, reference + padding * 0.5)
            }
            return lower...Swift.max(upper, lower + 0.001)
        }

        /// Where `value` sits on the axis, as 0…1.
        public func position(of value: Double) -> Double {
            let axis = self.axis
            let span = axis.upperBound - axis.lowerBound
            guard span > 0, value.isFinite else { return 0.5 }
            return Swift.min(Swift.max((value - axis.lowerBound) / span, 0), 1)
        }
    }

    private let model: Model
    /// Cyan above the mark, magenta below, neutral with no mark — the design's colour logic,
    /// carried over without its betting meaning.
    private let accent: Color
    private let height: CGFloat

    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.isBroadsheet) private var isBroadsheet

    // The bar belongs to the broadsheet but is not confined to it: the board renders on ordinary
    // dashboards too, and a paper-grey band on a white card beside the app's own greys would read
    // as a rendering bug. Each part therefore takes the page's colour, not the design's.
    private var bandColor: Color { isBroadsheet ? Broadsheet.band : Palette.track }
    private var ruleColor: Color { isBroadsheet ? Broadsheet.rule : Palette.separator }
    private var dotColor: Color { isBroadsheet ? Broadsheet.text : Palette.textPrimary }
    private var pageColor: Color { isBroadsheet ? Broadsheet.background : Palette.surface }

    public init(model: Model, accent: Color, height: CGFloat = 26) {
        self.model = model
        self.accent = accent
        self.height = height
    }

    public var body: some View {
        GeometryReader { proxy in
            let width = proxy.size.width
            if model.isDrawable, width > 1 {
                // A leading-aligned ZStack still centres its children vertically, which is what
                // puts the band, the tick and the dot on one axis without any y arithmetic.
                ZStack(alignment: .leading) {
                    baseline
                    band(width: width)
                    tick(width: width)
                    dot(width: width)
                }
                .frame(width: width, height: proxy.size.height, alignment: .leading)
            } else {
                // Not drawable is not the same as zero. An empty axis draws the baseline alone
                // rather than a bar pinned at one end, which would read as a real measurement.
                baseline
            }
        }
        .frame(height: height)
        .accessibilityHidden(true)
    }

    // MARK: Parts

    private var baseline: some View {
        Rectangle()
            .fill(ruleColor)
            .frame(height: 1)
    }

    /// `position(of:)` works in `Double`; every offset here is a `CGFloat`, and the conversion
    /// is written out rather than left to the implicit bridge.
    private func x(_ value: Double, in width: CGFloat) -> CGFloat {
        CGFloat(model.position(of: value)) * width
    }

    private func band(width: CGFloat) -> some View {
        let start = x(model.low, in: width)
        let end = x(model.high, in: width)
        return Capsule()
            .fill(bandColor)
            .frame(width: max(end - start, 2), height: Broadsheet.bandHeight)
            .offset(x: start)
    }

    @ViewBuilder private func tick(width: CGFloat) -> some View {
        if let reference = model.reference, reference.isFinite {
            Rectangle()
                .fill(accent)
                .frame(width: Broadsheet.tickWidth, height: Broadsheet.tickHeight)
                .offset(x: x(reference, in: width) - Broadsheet.tickWidth / 2)
        }
    }

    private func dot(width: CGFloat) -> some View {
        Circle()
            .fill(dotColor)
            // A hairline ring in the page colour, so the dot stays legible where it overlaps the
            // tick. Dropped under Reduce Transparency, where a ring reads as a second mark.
            .overlay(
                Circle().strokeBorder(reduceTransparency ? Color.clear : pageColor,
                                      lineWidth: 1.5)
            )
            .frame(width: Broadsheet.dotSize, height: Broadsheet.dotSize)
            .offset(x: x(model.projection, in: width) - Broadsheet.dotSize / 2)
    }
}

// MARK: - Labelled bar

/// A `RangeBar` with the design's three labels beneath it: the low bound, the mark and the
/// projection in the middle, the high bound.
///
/// Split out from `RangeBar` so the bar itself stays a pure drawing and this can own the wording,
/// which is where the honesty lives: the middle label names the mark (`season avg 31.5`) rather
/// than leaving a bare number the reader could take for a line.
public struct LabelledRangeBar: View {
    private let model: RangeBar.Model
    private let accent: Color
    private let lowText: String
    private let highText: String
    private let referenceLabel: String?
    private let referenceText: String?
    private let projectionText: String

    @Environment(\.isBroadsheet) private var isBroadsheet

    public init(model: RangeBar.Model,
                accent: Color,
                lowText: String,
                highText: String,
                referenceLabel: String?,
                referenceText: String?,
                projectionText: String) {
        self.model = model
        self.accent = accent
        self.lowText = lowText
        self.highText = highText
        self.referenceLabel = referenceLabel
        self.referenceText = referenceText
        self.projectionText = projectionText
    }

    private var middleText: String {
        if let referenceLabel = referenceLabel, let referenceText = referenceText {
            return "\(referenceLabel) \(referenceText) · proj \(projectionText)"
        }
        return "proj \(projectionText)"
    }

    private var labelFont: Font {
        isBroadsheet ? Broadsheet.figures(11) : HardwoodTextStyle.caption.monospacedDigitFont
    }

    private var labelColor: Color {
        isBroadsheet ? Broadsheet.textMuted : Palette.textTertiary
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            RangeBar(model: model, accent: accent)
            HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
                Text(lowText)
                    .font(labelFont)
                    .foregroundStyle(labelColor)
                Spacer(minLength: Spacing.xs)
                Text(middleText)
                    .font(labelFont)
                    .foregroundStyle(labelColor)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                Spacer(minLength: Spacing.xs)
                Text(highText)
                    .font(labelFont)
                    .foregroundStyle(labelColor)
            }
        }
    }
}

#if DEBUG
#Preview("Range bars") {
    VStack(alignment: .leading, spacing: Spacing.lg) {
        LabelledRangeBar(
            model: RangeBar.Model(low: 19, high: 38, projection: 28.4, reference: 27.1),
            accent: Broadsheet.accentAbove,
            lowText: "19", highText: "38",
            referenceLabel: "season avg", referenceText: "27.1",
            projectionText: "28.4"
        )
        LabelledRangeBar(
            model: RangeBar.Model(low: 4, high: 12, projection: 6.9, reference: 7.8),
            accent: Broadsheet.accentBelow,
            lowText: "4", highText: "12",
            referenceLabel: "season avg", referenceText: "7.8",
            projectionText: "6.9"
        )
        LabelledRangeBar(
            model: RangeBar.Model(low: 5, high: 13, projection: 8.9, reference: nil),
            accent: Broadsheet.textMuted,
            lowText: "5", highText: "13",
            referenceLabel: nil, referenceText: nil,
            projectionText: "8.9"
        )
        // A mark well outside the band: the axis has to widen rather than clamp the tick.
        LabelledRangeBar(
            model: RangeBar.Model(low: 19, high: 38, projection: 28.4, reference: 14.0),
            accent: Broadsheet.accentAbove,
            lowText: "19", highText: "38",
            referenceLabel: "season avg", referenceText: "14.0",
            projectionText: "28.4"
        )
    }
    .padding(Spacing.xl)
    .broadsheet()
    .broadsheetBackground()
}
#endif
