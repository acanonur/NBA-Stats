import SwiftUI

// The small labelled capsules of the league screens and tiles: a player's status, a defence band,
// "Provisional", "Estimated", "Result pending", "Assumed range", a reason and a source kind.
//
// WHY EVERY CHIP CARRIES WORDS, NOT ONLY A COLOUR
// A status or a band changes what a reader decides, and about one reader in twelve cannot tell the
// red of "Out" from the amber of "Doubtful". So colour is only ever the second signal: each chip
// spells its meaning, and the status and band chips add a symbol as well.
//
// WHAT THE CHIPS WILL NOT SAY
// A missing status is "No report", never "Available". A missing band is an em dash, never
// "Typical". Both rules come from the backend's own contract (a `null` status means nobody has
// reported, and a `null` band means the position was not tested), and a chip that quietly filled
// the gap with a reassuring word would be a statistic the app invented.

// MARK: - The capsule

/// The one capsule every chip below is made from.
struct LeagueChip: View {
    private let text: String
    private let symbol: String?
    private let tint: Color

    init(text: String, symbol: String? = nil, tint: Color = Palette.neutral) {
        self.text = text
        self.symbol = symbol
        self.tint = tint
    }

    var body: some View {
        HStack(spacing: Spacing.xxs) {
            if let symbol = symbol {
                Image(systemName: symbol)
                    .font(Typography.tableHeader)
                    .accessibilityHidden(true)
            }
            Text(text)
                .font(Typography.tableHeader)
                .fontWeight(.semibold)
                .lineLimit(1)
        }
        .foregroundStyle(tint)
        .padding(.horizontal, Spacing.sm)
        .padding(.vertical, Spacing.xxs)
        .background(Capsule(style: .continuous).fill(tint.opacity(0.14)))
        .overlay(Capsule(style: .continuous).strokeBorder(tint.opacity(0.32), lineWidth: 1))
        .fixedSize()
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Availability status

/// A player's availability: Out, Doubtful, Questionable, Probable, Available, or `No report`.
///
/// `status` is the raw key the server sent (`out`, `doubtful`, ...); `nil` and the empty string both
/// mean nobody has reported. `label` is the server's own wording and wins when it is present.
struct StatusChip: View {
    private let status: String?
    private let label: String?

    init(status: String?, label: String? = nil) {
        self.status = status
        self.label = label
    }

    /// The colour a status is drawn in. An unknown or missing status is grey.
    static func tint(for status: String?) -> Color {
        guard let status = status, !status.isEmpty else { return Palette.textTertiary }
        switch status {
        case "out":
            return Palette.negative
        case "doubtful", "questionable":
            return Palette.warning
        case "probable", "available":
            return Palette.positive
        default:
            return Palette.neutral
        }
    }

    private var text: String {
        if let label = label, !label.isEmpty { return label }
        return LeagueFormatting.statusWord(status)
    }

    var body: some View {
        LeagueChip(text: text,
                   symbol: LeagueFormatting.statusSymbol(status),
                   tint: StatusChip.tint(for: status))
    }
}

// MARK: - Defence band

/// How a team's defence at one position compares with the league: Better, Typical, Worse.
///
/// `better` means the team allows fewer points than is typical at that position. The band comes
/// from the server and only exists when the table is neither provisional nor withheld; with no band
/// this view is an em dash.
struct BandChip: View {
    private let band: String?

    init(band: String?) {
        self.band = band
    }

    /// The colour a band is drawn in; a missing band is grey.
    static func tint(for band: String?) -> Color {
        guard let band = band, !band.isEmpty else { return Palette.textTertiary }
        switch band {
        case "better":
            return Palette.positive
        case "worse":
            return Palette.negative
        default:
            return Palette.neutral
        }
    }

    private static func symbol(for band: String) -> String {
        switch band {
        case "better": return "arrow.down"
        case "worse": return "arrow.up"
        default: return "minus"
        }
    }

    var body: some View {
        if let band = band, !band.isEmpty {
            LeagueChip(text: LeagueFormatting.bandWord(band),
                       symbol: BandChip.symbol(for: band),
                       tint: BandChip.tint(for: band))
        } else {
            Text(Formatting.emDash)
                .font(Typography.tableCell)
                .foregroundStyle(Palette.textTertiary)
                .accessibilityLabel("No band")
        }
    }
}

// MARK: - States of a number

/// Marks a table or a team whose few games make every position figure unsteady.
struct ProvisionalBadge: View {
    var body: some View {
        LeagueChip(text: "Provisional", symbol: "hourglass", tint: Palette.warning)
            .help("Few games so far, so these numbers can still move a lot.")
    }
}

/// Marks numbers that are modelled or built from estimates rather than recorded.
struct EstimatedBadge: View {
    private let text: String

    init(text: String = "Estimated") {
        self.text = text
    }

    var body: some View {
        LeagueChip(text: text, tint: Palette.warning)
            .help("Some of these numbers are modelled or come from estimates, not official records.")
    }
}

/// A game that should be over, with no result stored yet. This is not the same as upcoming.
struct ResultPendingChip: View {
    var body: some View {
        LeagueChip(text: "Result pending", symbol: "clock", tint: Palette.neutral)
            .help("The game has been played, but its result has not been loaded yet.")
    }
}

/// Marks a projection whose 80% range uses a standing assumed width.
struct AssumedRangeBadge: View {
    var body: some View {
        LeagueChip(text: "Assumed range", tint: Palette.warning)
            .help("The range width is an assumption, not yet calibrated against past results.")
    }
}

// MARK: - Provenance

/// Why a player is listed: Injury, Illness, Rest, and so on. Draws nothing without a category.
struct ReasonChip: View {
    private let category: String?

    init(category: String?) {
        self.category = category
    }

    var body: some View {
        if let category = category, !category.isEmpty {
            LeagueChip(text: LeagueFormatting.reasonWord(category), tint: Palette.neutral)
        }
    }
}

/// Where a status came from: League report, Club statement, Press, Box score, Your workbook,
/// Entered by you. A kind this build does not know is shown as the server wrote it. Draws nothing
/// without a kind.
struct SourceKindChip: View {
    private let kind: String?

    init(kind: String?) {
        self.kind = kind
    }

    var body: some View {
        if let kind = kind, !kind.isEmpty {
            LeagueChip(text: LeagueFormatting.sourceKindWord(kind), tint: Palette.textSecondary)
        }
    }
}

#if DEBUG
#Preview("League chips") {
    VStack(alignment: .leading, spacing: Spacing.sm) {
        HStack {
            StatusChip(status: "out")
            StatusChip(status: "doubtful")
            StatusChip(status: "questionable")
            StatusChip(status: "probable")
            StatusChip(status: "available")
            StatusChip(status: nil)
        }
        HStack {
            BandChip(band: "better")
            BandChip(band: "typical")
            BandChip(band: "worse")
            BandChip(band: nil)
        }
        HStack {
            ProvisionalBadge()
            EstimatedBadge()
            ResultPendingChip()
            AssumedRangeBadge()
        }
        HStack {
            ReasonChip(category: "injury")
            SourceKindChip(kind: "clubStatement")
        }
    }
    .padding(Spacing.lg)
    .hardwoodBackground()
}
#endif
