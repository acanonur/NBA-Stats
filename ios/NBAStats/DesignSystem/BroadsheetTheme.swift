import Foundation
import SwiftUI

/// The broadsheet presentation's tokens and page furniture.
///
/// Taken from the `Hardwood Predictions` design handoff (`_ds/broadsheet-…/styles.css`), which
/// specifies **light values only** — the dark column of the table in `docs/BROADSHEET.md` §4 is
/// derived here rather than given. Deriving it was unavoidable: shipping a presentation that
/// only works in one appearance would be worse than choosing the other half carefully.
///
/// This is deliberately *not* folded into `Palette`. The broadsheet is a second visual language
/// — paper-white rather than app-grey, serif rather than sans, ruled rather than boxed — and
/// merging the two namespaces would let a tile widget reach for a broadsheet colour by accident
/// and half-apply an aesthetic. A widget gets these only by being rendered inside a broadsheet
/// layout, and only through `\.isBroadsheet`.
public enum Broadsheet {

    // MARK: - Colour

    /// The page. Warm off-white in light, near-black with a little warmth in dark.
    public static let background = Palette.adaptive(light: 0xF3F2F2, dark: 0x161514)
    /// Body text and the projection dot.
    public static let text = Palette.adaptive(light: 0x201E1D, dark: 0xEDEBEA)
    /// Kickers, captions, the second half of a dot leader.
    public static let textMuted = Palette.adaptive(light: 0x6B6664, dark: 0x9C9795)
    /// The design's `neutral-300`: hairline rules between rows and sections.
    public static let rule = Palette.adaptive(light: 0xD7D3D3, dark: 0x3A3736)
    /// The design's `neutral-400`: the interval band itself.
    public static let band = Palette.adaptive(light: 0xBAB6B6, dark: 0x514D4C)
    /// Cyan. The design's colour for the model's side of a line; here, above the reference.
    public static let accentAbove = Palette.adaptive(light: 0x006786, dark: 0x4FC3E8)
    /// Magenta. Below the reference.
    public static let accentBelow = Palette.adaptive(light: 0xAA0B56, dark: 0xFF7FB0)

    /// The tick's colour for a projection that sits above or below its reference mark.
    ///
    /// `nil` — no reference, or a difference indistinguishable from zero — is drawn in the
    /// neutral rule colour. The board never colours a comparison it cannot actually make.
    public static func accent(isAbove: Bool?) -> Color {
        switch isAbove {
        case .some(true): return accentAbove
        case .some(false): return accentBelow
        case .none: return textMuted
        }
    }

    // MARK: - Metrics

    /// Height of the interval band.
    public static let bandHeight: CGFloat = 5
    /// Diameter of the projection dot.
    public static let dotSize: CGFloat = 11
    /// The reference tick: 2pt wide, 20pt tall, straddling the band.
    public static let tickWidth: CGFloat = 2
    public static let tickHeight: CGFloat = 20
    /// Letter-spacing on an uppercase kicker, as the design's `.08em` at 11pt.
    public static let kickerTracking: CGFloat = 0.88

    // MARK: - Type

    /// The broadsheet's body face. `Source Serif 4` in the design; the system serif on device,
    /// because bundling a webfont to approximate a webfont is not an improvement.
    public static func serif(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .serif)
    }

    /// Numerals that line up in a column. The design sets `font-feature-settings: 'tnum' 1`;
    /// without this a proportional `1` makes every band's labels jitter against each other.
    public static func figures(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        serif(size, weight: weight).monospacedDigit()
    }
}

// MARK: - Environment

private struct BroadsheetKey: EnvironmentKey {
    static let defaultValue = false
}

public extension EnvironmentValues {
    /// True inside a layout whose `presentation` is `broadsheet`.
    ///
    /// A widget reads this to pick its variant. It is an environment flag rather than a
    /// parameter because the decision belongs to the page: `DashboardScreen` sets it once from
    /// `DashboardLayout.presentation` and everything below inherits it, so a widget added to a
    /// broadsheet layout is drawn as a broadsheet widget with no plumbing at the call site.
    var isBroadsheet: Bool {
        get { self[BroadsheetKey.self] }
        set { self[BroadsheetKey.self] = newValue }
    }
}

public extension View {
    /// Declares that everything below this view is being drawn on a broadsheet page.
    func broadsheet(_ enabled: Bool = true) -> some View {
        environment(\.isBroadsheet, enabled)
    }
}

// MARK: - Furniture

/// The uppercase, letter-spaced label above a section or a column.
public struct BroadsheetKicker: View {
    private let text: String
    private let size: CGFloat

    public init(_ text: String, size: CGFloat = 11) {
        self.text = text
        self.size = size
    }

    public var body: some View {
        Text(text.uppercased())
            .font(Broadsheet.serif(size, weight: .semibold))
            .tracking(Broadsheet.kickerTracking)
            .foregroundStyle(Broadsheet.textMuted)
            .accessibilityLabel(text)
    }
}

/// A hairline rule, the broadsheet's substitute for a card border.
public struct BroadsheetRule: View {
    @Environment(\.displayScale) private var displayScale

    private let weight: CGFloat?

    /// - Parameter weight: `nil` draws the thinnest line the display can, which is what a rule
    ///   between rows wants. A section rule passes 1 so it reads as heavier than the row rules.
    public init(weight: CGFloat? = nil) {
        self.weight = weight
    }

    public var body: some View {
        Rectangle()
            .fill(Broadsheet.rule)
            .frame(height: weight ?? (1 / max(displayScale, 1)))
            .accessibilityHidden(true)
    }
}

/// A label and a value separated by dot leaders, the way a broadsheet sets a table of contents.
public struct BroadsheetLeaderRow: View {
    private let label: String
    private let value: String
    private let valueColor: Color

    public init(_ label: String, value: String, valueColor: Color = Broadsheet.text) {
        self.label = label
        self.value = value
        self.valueColor = valueColor
    }

    public var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
            Text(label)
                .font(Broadsheet.serif(13))
                .foregroundStyle(Broadsheet.textMuted)
                .lineLimit(1)
                .layoutPriority(1)
            Leaders()
            Text(value)
                .font(Broadsheet.figures(13, weight: .semibold))
                .foregroundStyle(valueColor)
                .lineLimit(1)
                .layoutPriority(1)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label), \(value)")
    }

    /// The row of dots between the two. Drawn rather than typed, so it fills whatever width is
    /// left over without a string of periods that VoiceOver would read one by one.
    private struct Leaders: View {
        var body: some View {
            Line()
                .stroke(style: StrokeStyle(lineWidth: 1, dash: [1, 3]))
                .foregroundStyle(Broadsheet.rule)
                .frame(height: 1)
                .padding(.bottom, 3)
                .accessibilityHidden(true)
        }

        private struct Line: Shape {
            func path(in rect: CGRect) -> Path {
                var path = Path()
                path.move(to: CGPoint(x: rect.minX, y: rect.midY))
                path.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
                return path
            }
        }
    }
}

public extension View {
    /// The broadsheet page background, for a screen drawn in this presentation.
    func broadsheetBackground() -> some View {
        background(Broadsheet.background.ignoresSafeArea())
    }
}

#if DEBUG
#Preview("Broadsheet furniture") {
    VStack(alignment: .leading, spacing: Spacing.md) {
        BroadsheetKicker("Tonight's projections")
        BroadsheetRule(weight: 1)
        BroadsheetLeaderRow("Projected minutes", value: "34.2")
        BroadsheetRule()
        BroadsheetLeaderRow("Pace", value: "+0.6", valueColor: Broadsheet.accentAbove)
        BroadsheetRule()
        BroadsheetLeaderRow("Opponent defence", value: "−0.5", valueColor: Broadsheet.accentBelow)
        BroadsheetRule()
    }
    .padding(Spacing.lg)
    .broadsheetBackground()
}
#endif
