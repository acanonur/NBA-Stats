import Foundation
import SwiftUI

/// The Hardwood type scale.
///
/// Every style is built on a Dynamic Type text style, so the whole dashboard reflows with the
/// reader's chosen size. Numeric styles use monospaced digits: a stat column that re-renders
/// every time a game finalizes must not jitter as `111` becomes `98`.
public enum HardwoodTextStyle: String, CaseIterable, Sendable {
    /// The hero number on a tile.
    case displayValue
    /// A supporting metric's number.
    case statValue
    /// The label under or beside a number.
    case statLabel
    /// A column heading in a table.
    case tableHeader
    /// A cell in a table.
    case tableCell
    /// Footnotes, era notes, timestamps.
    case caption
    /// A heading above a group of widgets or a list section.
    case sectionTitle
    /// The title in a widget's chrome.
    case widgetTitle

    /// True for styles that render numbers, and therefore default to monospaced digits.
    public var isNumeric: Bool {
        switch self {
        case .displayValue, .statValue, .tableCell:
            return true
        case .statLabel, .tableHeader, .caption, .sectionTitle, .widgetTitle:
            return false
        }
    }

    private var textStyle: Font.TextStyle {
        switch self {
        case .displayValue: return .title
        case .statValue:    return .title3
        case .statLabel:    return .caption
        case .tableHeader:  return .caption2
        case .tableCell:    return .footnote
        case .caption:      return .caption
        case .sectionTitle: return .headline
        case .widgetTitle:  return .subheadline
        }
    }

    private var weight: Font.Weight {
        switch self {
        case .displayValue: return .bold
        case .statValue:    return .semibold
        case .statLabel:    return .medium
        case .tableHeader:  return .semibold
        case .tableCell:    return .regular
        case .caption:      return .regular
        case .sectionTitle: return .semibold
        case .widgetTitle:  return .semibold
        }
    }

    /// The font for this style. Numeric styles already carry monospaced digits.
    public var font: Font {
        let base = Font.system(textStyle, design: .default, weight: weight)
        return isNumeric ? base.monospacedDigit() : base
    }

    /// The same size and weight with monospaced digits forced on. Use this for any run of text
    /// that has to line up in a column, even when the style is not normally numeric.
    public var monospacedDigitFont: Font {
        Font.system(textStyle, design: .default, weight: weight).monospacedDigit()
    }

    /// The color a style takes when the call site does not override it.
    public var defaultColor: Color {
        switch self {
        case .displayValue, .statValue, .sectionTitle, .widgetTitle, .tableCell:
            return Palette.textPrimary
        case .statLabel, .tableHeader:
            return Palette.textSecondary
        case .caption:
            return Palette.textTertiary
        }
    }

    /// Small labels get a little tracking so they stay readable at 11pt.
    ///
    /// Note that no style upper-cases its text. Metric short names come from the catalog already
    /// cased the way the sport writes them — `eFG%`, `NetRtg`, `WS/48` — and forcing them to
    /// capitals would destroy that. A caller who genuinely wants capitals applies `.textCase`.
    public var tracking: CGFloat {
        switch self {
        case .statLabel, .tableHeader:
            return 0.4
        case .displayValue:
            return -0.4
        case .statValue, .tableCell, .caption, .sectionTitle, .widgetTitle:
            return 0
        }
    }

}

/// Static fonts, for the places that only need a `Font` rather than a whole treatment.
public enum Typography {
    public static let displayValue: Font = HardwoodTextStyle.displayValue.font
    public static let statValue: Font = HardwoodTextStyle.statValue.font
    public static let statLabel: Font = HardwoodTextStyle.statLabel.font
    public static let tableHeader: Font = HardwoodTextStyle.tableHeader.font
    public static let tableCell: Font = HardwoodTextStyle.tableCell.font
    public static let caption: Font = HardwoodTextStyle.caption.font
    public static let sectionTitle: Font = HardwoodTextStyle.sectionTitle.font
    public static let widgetTitle: Font = HardwoodTextStyle.widgetTitle.font

    /// Monospaced-digit variants. The numeric styles above already use them; these exist so a
    /// non-numeric style (a label in a column of labels, say) can opt in too.
    public static let displayValueMono: Font = HardwoodTextStyle.displayValue.monospacedDigitFont
    public static let statValueMono: Font = HardwoodTextStyle.statValue.monospacedDigitFont
    public static let statLabelMono: Font = HardwoodTextStyle.statLabel.monospacedDigitFont
    public static let tableHeaderMono: Font = HardwoodTextStyle.tableHeader.monospacedDigitFont
    public static let tableCellMono: Font = HardwoodTextStyle.tableCell.monospacedDigitFont
    public static let captionMono: Font = HardwoodTextStyle.caption.monospacedDigitFont
    public static let sectionTitleMono: Font = HardwoodTextStyle.sectionTitle.monospacedDigitFont
    public static let widgetTitleMono: Font = HardwoodTextStyle.widgetTitle.monospacedDigitFont

    /// The font for a style, with an explicit choice about digit spacing.
    public static func font(_ style: HardwoodTextStyle, monospacedDigits: Bool) -> Font {
        monospacedDigits ? style.monospacedDigitFont : style.font
    }
}

/// Applies a `HardwoodTextStyle` — font, tracking, casing and default color — to any view.
public struct HardwoodTextStyleModifier: ViewModifier {
    private let style: HardwoodTextStyle
    private let color: Color?
    private let monospacedDigits: Bool?

    public init(style: HardwoodTextStyle, color: Color? = nil, monospacedDigits: Bool? = nil) {
        self.style = style
        self.color = color
        self.monospacedDigits = monospacedDigits
    }

    public func body(content: Content) -> some View {
        content
            .font(Typography.font(style, monospacedDigits: monospacedDigits ?? style.isNumeric))
            .tracking(style.tracking)
            .foregroundStyle(color ?? style.defaultColor)
    }
}

public extension View {
    /// Applies one of the eight Hardwood text styles.
    func hardwoodText(_ style: HardwoodTextStyle,
                      color: Color? = nil,
                      monospacedDigits: Bool? = nil) -> some View {
        modifier(HardwoodTextStyleModifier(style: style, color: color, monospacedDigits: monospacedDigits))
    }

    func displayValueStyle(color: Color? = nil) -> some View { hardwoodText(.displayValue, color: color) }
    func statValueStyle(color: Color? = nil) -> some View { hardwoodText(.statValue, color: color) }
    func statLabelStyle(color: Color? = nil) -> some View { hardwoodText(.statLabel, color: color) }
    func tableHeaderStyle(color: Color? = nil) -> some View { hardwoodText(.tableHeader, color: color) }
    func tableCellStyle(color: Color? = nil) -> some View { hardwoodText(.tableCell, color: color) }
    func captionStyle(color: Color? = nil) -> some View { hardwoodText(.caption, color: color) }
    func sectionTitleStyle(color: Color? = nil) -> some View { hardwoodText(.sectionTitle, color: color) }
    func widgetTitleStyle(color: Color? = nil) -> some View { hardwoodText(.widgetTitle, color: color) }
}

#if DEBUG
#Preview("Type scale") {
    ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            ForEach(HardwoodTextStyle.allCases, id: \.self) { style in
                VStack(alignment: .leading, spacing: Spacing.xxs) {
                    Text(style.rawValue).font(.system(.caption2, design: .monospaced, weight: .regular))
                        .foregroundStyle(Palette.textTertiary)
                    Text("61.5% · 118 · -7.8").hardwoodText(style)
                }
            }
        }
        .padding(Spacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
    .hardwoodBackground()
}

#Preview("Digits do not jitter") {
    VStack(alignment: .trailing, spacing: Spacing.xs) {
        ForEach(["111.1", "98.4", "100.0", "8.8"], id: \.self) { value in
            Text(value).hardwoodText(.tableCell)
        }
    }
    .padding(Spacing.lg)
    .hardwoodCard()
    .padding(Spacing.lg)
    .hardwoodBackground()
}
#endif
