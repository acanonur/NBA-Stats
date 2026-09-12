import Foundation
import SwiftUI
import UIKit

/// Hardwood's semantic color set.
///
/// Every color declares its light **and** dark value explicitly, in code, so neither appearance
/// is an afterthought and so the app never depends on asset catalog entries that may not ship.
/// Views must pull color from here rather than constructing literals inline.
public enum Palette {

    // MARK: - Construction

    private static func solid(_ hex: UInt32, alpha: CGFloat) -> UIColor {
        UIColor(
            red: CGFloat((hex >> 16) & 0xFF) / 255.0,
            green: CGFloat((hex >> 8) & 0xFF) / 255.0,
            blue: CGFloat(hex & 0xFF) / 255.0,
            alpha: alpha
        )
    }

    /// A color whose light and dark values are both chosen deliberately.
    /// - Parameters:
    ///   - light: `0xRRGGBB` used when the trait collection is light (or unspecified).
    ///   - dark: `0xRRGGBB` used when the trait collection is dark.
    public static func adaptive(light: UInt32,
                                dark: UInt32,
                                lightAlpha: CGFloat = 1,
                                darkAlpha: CGFloat = 1) -> Color {
        let lightColor = solid(light, alpha: lightAlpha)
        let darkColor = solid(dark, alpha: darkAlpha)
        return Color(uiColor: UIColor { traits in
            traits.userInterfaceStyle == .dark ? darkColor : lightColor
        })
    }

    // MARK: - Surfaces

    /// The page behind every card.
    public static let background = adaptive(light: 0xF3F4F7, dark: 0x0C0E12)
    /// The standard widget / card fill.
    public static let surface = adaptive(light: 0xFFFFFF, dark: 0x171A21)
    /// A surface that sits above `surface`: sheets, popovers, selected chips.
    public static let surfaceRaised = adaptive(light: 0xFFFFFF, dark: 0x212632)
    /// A recessed fill used for tracks, skeletons and table zebra striping.
    public static let surfaceSunken = adaptive(light: 0xECEEF2, dark: 0x11141A)
    /// Hairline rules and card borders.
    public static let separator = adaptive(light: 0xD3D7DF, dark: 0x2B3039)

    // MARK: - Text

    public static let textPrimary = adaptive(light: 0x0F1216, dark: 0xF2F4F8)
    public static let textSecondary = adaptive(light: 0x596070, dark: 0xA2AAB9)
    public static let textTertiary = adaptive(light: 0x8A92A1, dark: 0x6C7586)

    // MARK: - Semantics

    /// Better than the comparison baseline.
    public static let positive = adaptive(light: 0x0F7A46, dark: 0x3DD68C)
    /// Worse than the comparison baseline.
    public static let negative = adaptive(light: 0xB3261E, dark: 0xFF7B70)
    /// Indistinguishable from the baseline, or no baseline at all.
    public static let neutral = adaptive(light: 0x6B7280, dark: 0x8A93A3)
    /// Era caveats, stale data, estimated values.
    public static let warning = adaptive(light: 0x8F5A00, dark: 0xF5B942)
    /// Selection and interactive emphasis that is not tied to a layout accent.
    public static let selection = adaptive(light: 0x1D4ED8, dark: 0x7AA7FF)

    // MARK: - Component fills

    /// The unfilled part of a percentile or factor bar.
    public static let track = adaptive(light: 0xE2E5EB, dark: 0x252A33)
    /// Placeholder bars in a loading tile.
    public static let skeleton = adaptive(light: 0xE5E8EE, dark: 0x222732)
    /// The travelling highlight swept across a loading tile.
    public static let skeletonHighlight = adaptive(light: 0xF9FAFC, dark: 0x39404F,
                                                   lightAlpha: 0.95, darkAlpha: 0.95)

    // MARK: - Chart ramp

    /// Six-plus perceptually distinct hues that stay separable on both the light and the dark
    /// background. They differ in lightness as well as hue, so a monochrome or colorblind reader
    /// can still tell adjacent series apart.
    public static let chartSeries: [Color] = [
        adaptive(light: 0x2563EB, dark: 0x6BA5FF),   // blue
        adaptive(light: 0xCF5A12, dark: 0xFFA05C),   // orange
        adaptive(light: 0x0E7C86, dark: 0x39D6C4),   // teal
        adaptive(light: 0xA21CAF, dark: 0xF07CD8),   // magenta
        adaptive(light: 0x5B8A00, dark: 0xAEE64B),   // lime
        adaptive(light: 0x6D28D9, dark: 0xB49BFB),   // violet
        adaptive(light: 0xB42318, dark: 0xFF8A80),   // red
        adaptive(light: 0x846017, dark: 0xE2C36A)    // gold
    ]

    /// Wraps around, so a series index from the API never traps.
    public static func chartColor(at index: Int) -> Color {
        guard !chartSeries.isEmpty else { return neutral }
        let wrapped = ((index % chartSeries.count) + chartSeries.count) % chartSeries.count
        return chartSeries[wrapped]
    }

    /// The palette monograms are drawn from. Deliberately distinct from `chartSeries` so a team
    /// badge is never confused with a chart series swatch beside it.
    public static let monogramPalette: [Color] = [
        adaptive(light: 0x1F4E9C, dark: 0x6FA3F5),
        adaptive(light: 0x9A3412, dark: 0xF59E63),
        adaptive(light: 0x15706B, dark: 0x4BD0C2),
        adaptive(light: 0x7A1F6B, dark: 0xE58AD2),
        adaptive(light: 0x4C6B12, dark: 0xB2D75B),
        adaptive(light: 0x5B21B6, dark: 0xB39BF0),
        adaptive(light: 0x9B1C1C, dark: 0xF58A8A),
        adaptive(light: 0x7C5A10, dark: 0xDCBA63),
        adaptive(light: 0x1E5B3A, dark: 0x5FC993),
        adaptive(light: 0x3F4650, dark: 0xA9B2BF),
        adaptive(light: 0x0F5C78, dark: 0x5FBDE0),
        adaptive(light: 0x7A3B00, dark: 0xE0A05C)
    ]

    /// A stable color for a short string such as a team abbreviation.
    ///
    /// Swift's `hashValue` is seeded per process, so it cannot be used here: the badge color has
    /// to be identical across launches. This is a plain FNV-1a fold instead.
    public static func monogramColor(for text: String) -> Color {
        guard !monogramPalette.isEmpty else { return neutral }
        var hash: UInt32 = 2_166_136_261
        for scalar in text.uppercased().unicodeScalars {
            hash = (hash ^ (scalar.value & 0xFF)) &* 16_777_619
        }
        let index = Int(hash % UInt32(monogramPalette.count))
        return monogramPalette[index]
    }

    // MARK: - Comparison

    /// The color for a number compared against a baseline.
    ///
    /// `nil` or a delta indistinguishable from zero is neutral: the dashboard never colors a
    /// number it cannot actually compare.
    public static func value(for delta: Double?, higherIsBetter: Bool) -> Color {
        guard let delta, delta.isFinite, abs(delta) > 1e-9 else { return neutral }
        let isImprovement = higherIsBetter ? (delta > 0) : (delta < 0)
        return isImprovement ? positive : negative
    }

    /// The same decision expressed as a comparison between a value and a league average.
    public static func value(_ value: Double?, comparedTo baseline: Double?, higherIsBetter: Bool) -> Color {
        guard let value, let baseline else { return neutral }
        return self.value(for: value - baseline, higherIsBetter: higherIsBetter)
    }
}

/// A friendlier spelling of `Palette` for call sites that read better with a noun.
public typealias HardwoodColor = Palette

// MARK: - Accents

public extension AccentName {

    /// The layout accent, at full strength, for text, strokes and chart emphasis.
    var color: Color {
        switch self {
        case .orange:   return Palette.adaptive(light: 0xC2410C, dark: 0xFB923C)
        case .indigo:   return Palette.adaptive(light: 0x4338CA, dark: 0x8F9CFF)
        case .teal:     return Palette.adaptive(light: 0x0F766E, dark: 0x2DD4BF)
        case .red:      return Palette.adaptive(light: 0xB91C1C, dark: 0xFF7A7A)
        case .amber:    return Palette.adaptive(light: 0x9A6400, dark: 0xFBBF24)
        case .green:    return Palette.adaptive(light: 0x15803D, dark: 0x4ADE80)
        case .blue:     return Palette.adaptive(light: 0x1D4ED8, dark: 0x60A5FA)
        case .purple:   return Palette.adaptive(light: 0x7E22CE, dark: 0xC084FC)
        case .graphite: return Palette.adaptive(light: 0x3F4650, dark: 0xAEB6C2)
        }
    }

    /// A low-contrast wash of the accent, for chip and tile backgrounds. Alpha differs by
    /// appearance because a tint that reads as subtle on white shouts on near-black.
    var softTint: Color {
        switch self {
        case .orange:   return Palette.adaptive(light: 0xC2410C, dark: 0xFB923C, lightAlpha: 0.12, darkAlpha: 0.20)
        case .indigo:   return Palette.adaptive(light: 0x4338CA, dark: 0x8F9CFF, lightAlpha: 0.12, darkAlpha: 0.20)
        case .teal:     return Palette.adaptive(light: 0x0F766E, dark: 0x2DD4BF, lightAlpha: 0.12, darkAlpha: 0.20)
        case .red:      return Palette.adaptive(light: 0xB91C1C, dark: 0xFF7A7A, lightAlpha: 0.12, darkAlpha: 0.20)
        case .amber:    return Palette.adaptive(light: 0x9A6400, dark: 0xFBBF24, lightAlpha: 0.14, darkAlpha: 0.20)
        case .green:    return Palette.adaptive(light: 0x15803D, dark: 0x4ADE80, lightAlpha: 0.12, darkAlpha: 0.20)
        case .blue:     return Palette.adaptive(light: 0x1D4ED8, dark: 0x60A5FA, lightAlpha: 0.12, darkAlpha: 0.20)
        case .purple:   return Palette.adaptive(light: 0x7E22CE, dark: 0xC084FC, lightAlpha: 0.12, darkAlpha: 0.20)
        case .graphite: return Palette.adaptive(light: 0x3F4650, dark: 0xAEB6C2, lightAlpha: 0.12, darkAlpha: 0.20)
        }
    }

    // `displayName` deliberately lives on the type itself in `Core/DashboardLayout.swift`;
    // the design system only adds the colors.
}

// MARK: - Spacing and radius

/// The spacing scale. Widget internals should only ever use these values.
public enum Spacing {
    public static let hairline: CGFloat = 1
    public static let xxs: CGFloat = 2
    public static let xs: CGFloat = 4
    public static let sm: CGFloat = 8
    public static let md: CGFloat = 12
    public static let lg: CGFloat = 16
    public static let xl: CGFloat = 24
    public static let xxl: CGFloat = 32
}

/// The corner radius scale.
public enum Radius {
    public static let chip: CGFloat = 6
    public static let control: CGFloat = 10
    public static let card: CGFloat = 16
    public static let sheet: CGFloat = 20
    /// Large enough that any realistic control renders as a capsule.
    public static let pill: CGFloat = 999
}

// MARK: - Card

/// The standard Hardwood card: surface fill, rounded corners, a hairline border and — in light
/// mode only — a very small drop shadow. Dark mode separates by lightness instead, because a
/// black shadow on a near-black page is invisible.
public struct HardwoodCardModifier: ViewModifier {
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.displayScale) private var displayScale

    private let cornerRadius: CGFloat
    private let padding: CGFloat
    private let isRaised: Bool

    public init(cornerRadius: CGFloat = Radius.card,
                padding: CGFloat = Spacing.md,
                isRaised: Bool = false) {
        self.cornerRadius = cornerRadius
        self.padding = padding
        self.isRaised = isRaised
    }

    private var shape: RoundedRectangle {
        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
    }

    public func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(shape.fill(isRaised ? Palette.surfaceRaised : Palette.surface))
            .overlay(shape.strokeBorder(Palette.separator, lineWidth: 1 / max(displayScale, 1)))
            .clipShape(shape)
            .shadow(color: colorScheme == .dark ? Color.clear : Color.black.opacity(0.06),
                    radius: isRaised ? 12 : 6,
                    x: 0,
                    y: isRaised ? 4 : 2)
    }
}

public extension View {
    /// Wraps the view in the standard Hardwood card surface.
    func hardwoodCard(cornerRadius: CGFloat = Radius.card,
                      padding: CGFloat = Spacing.md,
                      isRaised: Bool = false) -> some View {
        modifier(HardwoodCardModifier(cornerRadius: cornerRadius, padding: padding, isRaised: isRaised))
    }

    /// The page background, for screens that sit behind cards.
    func hardwoodBackground() -> some View {
        background(Palette.background.ignoresSafeArea())
    }
}

#if DEBUG
private struct PaletteSwatch: Identifiable {
    let id: String
    let color: Color
}

#Preview("Palette") {
    let semantic: [PaletteSwatch] = [
        PaletteSwatch(id: "background", color: Palette.background),
        PaletteSwatch(id: "surface", color: Palette.surface),
        PaletteSwatch(id: "surfaceRaised", color: Palette.surfaceRaised),
        PaletteSwatch(id: "surfaceSunken", color: Palette.surfaceSunken),
        PaletteSwatch(id: "separator", color: Palette.separator),
        PaletteSwatch(id: "textPrimary", color: Palette.textPrimary),
        PaletteSwatch(id: "textSecondary", color: Palette.textSecondary),
        PaletteSwatch(id: "textTertiary", color: Palette.textTertiary),
        PaletteSwatch(id: "positive", color: Palette.positive),
        PaletteSwatch(id: "negative", color: Palette.negative),
        PaletteSwatch(id: "neutral", color: Palette.neutral),
        PaletteSwatch(id: "warning", color: Palette.warning),
        PaletteSwatch(id: "selection", color: Palette.selection),
        PaletteSwatch(id: "track", color: Palette.track)
    ]
    let series: [PaletteSwatch] = Palette.chartSeries.enumerated().map { pair in
        PaletteSwatch(id: "series \(pair.offset)", color: pair.element)
    }
    return ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text("Semantic").font(.headline)
                ForEach(semantic) { swatch in
                    HStack(spacing: Spacing.sm) {
                        RoundedRectangle(cornerRadius: Radius.chip, style: .continuous)
                            .fill(swatch.color)
                            .frame(width: 44, height: 24)
                            .overlay(RoundedRectangle(cornerRadius: Radius.chip, style: .continuous)
                                .strokeBorder(Palette.separator, lineWidth: 1))
                        Text(swatch.id).font(.footnote).foregroundStyle(Palette.textSecondary)
                    }
                }
            }
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text("Chart series").font(.headline)
                ForEach(series) { swatch in
                    HStack(spacing: Spacing.sm) {
                        Capsule().fill(swatch.color).frame(width: 60, height: 12)
                        Text(swatch.id).font(.footnote).foregroundStyle(Palette.textSecondary)
                    }
                }
            }
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text("Accents").font(.headline)
                ForEach(AccentName.allCases, id: \.self) { accent in
                    HStack(spacing: Spacing.sm) {
                        Circle().fill(accent.color).frame(width: 22, height: 22)
                        RoundedRectangle(cornerRadius: Radius.chip, style: .continuous)
                            .fill(accent.softTint)
                            .frame(width: 60, height: 22)
                        Text(accent.displayName).font(.footnote).foregroundStyle(Palette.textSecondary)
                    }
                }
            }
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}

#Preview("Card") {
    VStack(spacing: Spacing.lg) {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("Net Rating").font(.caption).foregroundStyle(Palette.textSecondary)
            Text("+7.8").font(.largeTitle).foregroundStyle(Palette.textPrimary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .hardwoodCard()

        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("Raised").font(.caption).foregroundStyle(Palette.textSecondary)
            Text("61.5%").font(.largeTitle).foregroundStyle(Palette.textPrimary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .hardwoodCard(isRaised: true)
    }
    .padding(Spacing.lg)
    .hardwoodBackground()
}
#endif
