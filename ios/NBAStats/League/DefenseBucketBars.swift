import SwiftUI

// Points allowed to opponents at each position, as one row per position: a bar for what the team
// allows against a tick for the league's average, the difference, the band, and (on a screen) the
// opponents' minutes.
//
// THE HONEST STATES, DRIVEN ONLY BY THE PAYLOAD
// A defence-by-position table is only worth reading when it has enough games and enough placed
// points. The server says so with `withheld` (a reason and a message) and `provisional`, and this
// view obeys exactly that, never deciding for itself:
//   - `withheld.reason == "minimumGames"`: the raw points allowed per position are shown, dimmed;
//     no differences, no bands. Too few games to say a team is better or worse than the league.
//   - any other `withheld`, or `provisional == true`: the raw numbers and differences are shown in
//     plain grey; the band is an em dash. A grey difference says "this is a fact, not a verdict".
//   - neither: the difference is toned by its `band` and nothing else (better is green, worse is
//     red, typical is neutral, no band is plain grey).
// There is no rank anywhere, no ordering by a score, and the bar is never coloured good or bad.
//
// WHAT "UNASSIGNED" MEANS
// The server keeps points scored by players with no listed position in their own bucket
// (`position == "unknown"`) and never reassigns them. It is drawn neutral and labelled
// "Unassigned". On a tile, an Unassigned row whose served value is exactly zero is left out (there
// is nothing to show); on a screen it is always listed, because "0.0 unassigned" is the coverage
// fact itself.

// MARK: - Rules shared with the tables

/// The display rules every defence view applies, in one place so a table cell and a bar row agree.
enum DefenseDisplay {

    /// The row label: "Unassigned" for the unknown bucket; on a screen the server's own label
    /// ("Guards"), on a tile the position code ("G").
    static func title(for bucket: LeagueDefenseBucket, density: LeagueDensity) -> String {
        if bucket.position == "unknown" { return "Unassigned" }
        if density == .full {
            if let label = bucket.label, !label.isEmpty { return label }
            return LeagueFormatting.positionWord(bucket.position)
        }
        let code = bucket.position ?? ""
        return code.isEmpty ? Formatting.emDash : code
    }

    /// True when the differences must not be shown at all: too few games to judge.
    static func hidesDelta(withheld: LeagueWithheld?) -> Bool {
        withheld?.reason == "minimumGames"
    }

    /// True when a difference is shown as a plain fact in grey rather than toned by a band.
    static func isGreyed(withheld: LeagueWithheld?, provisional: Bool?) -> Bool {
        withheld != nil || provisional == true
    }

    /// The colour of a difference. Only the band tones it, and only when the table is neither
    /// withheld nor provisional.
    static func deltaTint(band: String?, greyed: Bool) -> Color {
        if greyed { return Palette.textSecondary }
        switch band ?? "" {
        case "better": return Palette.positive
        case "worse": return Palette.negative
        case "typical": return Palette.neutral
        default: return Palette.textSecondary
        }
    }

    /// The band to show: none while the table is withheld or provisional.
    static func shownBand(_ band: String?, greyed: Bool) -> String? {
        greyed ? nil : band
    }
}

// MARK: - One bar

/// A bar from zero for the points allowed, and a tick at the league's average when there is one.
private struct DefenseBar: View {
    let value: Double?
    let reference: Double?
    let scaleMax: Double
    let tint: Color

    private func xPosition(of point: Double?, width: CGFloat) -> CGFloat {
        guard let point = point, point.isFinite, scaleMax > 0 else { return 0 }
        let fraction = min(max(point / scaleMax, 0), 1)
        return CGFloat(fraction) * width
    }

    var body: some View {
        GeometryReader { proxy in
            let width = proxy.size.width
            ZStack(alignment: .leading) {
                Capsule(style: .continuous)
                    .fill(Palette.track)
                    .frame(height: 8)
                Capsule(style: .continuous)
                    .fill(tint)
                    .frame(width: xPosition(of: value, width: width), height: 8)
                if reference != nil {
                    Rectangle()
                        .fill(Palette.textPrimary)
                        .frame(width: 2, height: 12)
                        .offset(x: xPosition(of: reference, width: width) - 1)
                }
            }
            .frame(width: width, height: proxy.size.height, alignment: .leading)
        }
        .frame(height: 12)
        .accessibilityHidden(true)
    }
}

// MARK: - Rows

/// The column widths, shared by the header and the rows so they line up.
private enum DefenseColumns {
    static func title(for density: LeagueDensity) -> CGFloat {
        density == .full ? 96 : 68
    }
    static let value: CGFloat = 48
    static let delta: CGFloat = 52
    static let band: CGFloat = 78
    static let minutes: CGFloat = 56
}

private struct DefenseBarRowData: Identifiable {
    let id: String
    let title: String
    let value: Double?
    let reference: Double?
    let delta: Double?
    let band: String?
    let minutes: Double?
    let isUnassigned: Bool
}

private struct DefenseBarRow: View {
    let row: DefenseBarRowData
    let scaleMax: Double
    let density: LeagueDensity
    let showsDelta: Bool
    let isGreyed: Bool
    let showsBandColumn: Bool

    private var titleColor: Color {
        row.isUnassigned ? Palette.textSecondary : Palette.textPrimary
    }

    private var barTint: Color {
        row.isUnassigned ? Palette.neutral : Palette.chartColor(at: 0)
    }

    private var deltaText: String {
        showsDelta ? LeagueFormatting.signed(row.delta) : Formatting.emDash
    }

    private var deltaColor: Color {
        showsDelta ? DefenseDisplay.deltaTint(band: row.band, greyed: isGreyed) : Palette.textTertiary
    }

    var body: some View {
        HStack(spacing: Spacing.sm) {
            Text(row.title)
                .hardwoodText(.tableCell, color: titleColor)
                .lineLimit(1)
                .frame(width: DefenseColumns.title(for: density), alignment: .leading)
            DefenseBar(value: row.value, reference: row.reference, scaleMax: scaleMax, tint: barTint)
                .frame(minWidth: 40, maxWidth: .infinity)
            Text(LeagueFormatting.number(row.value))
                .hardwoodText(.tableCell)
                .frame(width: DefenseColumns.value, alignment: .trailing)
            Text(deltaText)
                .hardwoodText(.tableCell, color: deltaColor)
                .frame(width: DefenseColumns.delta, alignment: .trailing)
            if showsBandColumn {
                BandChip(band: DefenseDisplay.shownBand(row.band, greyed: isGreyed))
                    .frame(width: DefenseColumns.band, alignment: .leading)
            }
            if density == .full {
                Text(LeagueFormatting.minutes(row.minutes))
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                    .frame(width: DefenseColumns.minutes, alignment: .trailing)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - The rows of a team

/// One row per position bucket, in the server's order. See the file comment for the states.
///
/// `density: .compact` is a tile's version (position code, bar, value, difference, and a band
/// column only when some band exists); `.full` adds the server's labels, a header row, the band
/// column always, and the opponents' minutes per game. `provisional` and `withheld` come from the
/// team or table row the buckets belong to. `showsBadge: false` leaves the "Provisional" badge to
/// the caller (the breakdown view puts it in its own headline).
struct DefenseBucketBars: View {
    private let buckets: [LeagueDefenseBucket]
    private let withheld: LeagueWithheld?
    private let provisional: Bool
    private let density: LeagueDensity
    private let showsBadge: Bool

    init(buckets: [LeagueDefenseBucket]?,
         withheld: LeagueWithheld? = nil,
         provisional: Bool = false,
         density: LeagueDensity = .compact,
         showsBadge: Bool = true) {
        self.buckets = buckets ?? []
        self.withheld = withheld
        self.provisional = provisional
        self.density = density
        self.showsBadge = showsBadge
    }

    private var rows: [DefenseBarRowData] {
        var found: [DefenseBarRowData] = []
        for (index, bucket) in buckets.enumerated() {
            let isUnassigned = bucket.position == "unknown"
            if isUnassigned && density == .compact && (bucket.pointsAllowedPerGame ?? 0) == 0 {
                continue
            }
            found.append(DefenseBarRowData(id: String(index) + "-" + (bucket.position ?? ""),
                                           title: DefenseDisplay.title(for: bucket, density: density),
                                           value: bucket.pointsAllowedPerGame,
                                           reference: bucket.leagueAverage,
                                           delta: bucket.deltaPerGame,
                                           band: bucket.band,
                                           minutes: bucket.opponentMinutesPerGame,
                                           isUnassigned: isUnassigned))
        }
        return found
    }

    private var isGreyed: Bool {
        DefenseDisplay.isGreyed(withheld: withheld, provisional: provisional)
    }

    /// A tile shows a band column only when some bucket has a band to show; a screen always does.
    private var showsBandColumn: Bool {
        if density == .full { return true }
        if isGreyed { return false }
        return buckets.contains { $0.band != nil }
    }

    private func scaleMax(for shown: [DefenseBarRowData]) -> Double {
        var top: Double = 0
        for row in shown {
            if let value = row.value, value.isFinite, value > top { top = value }
            if let reference = row.reference, reference.isFinite, reference > top { top = reference }
        }
        return top > 0 ? top : 1
    }

    var body: some View {
        let shown = rows
        if shown.isEmpty {
            Text(Formatting.emDash)
                .hardwoodText(.caption)
        } else {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                if provisional && showsBadge {
                    ProvisionalBadge()
                }
                if density == .full {
                    headerRow
                }
                rowsView(shown)
            }
        }
    }

    private var headerRow: some View {
        HStack(spacing: Spacing.sm) {
            Text("Position")
                .hardwoodText(.tableHeader)
                .frame(width: DefenseColumns.title(for: .full), alignment: .leading)
            Text("League average is the tick")
                .hardwoodText(.tableHeader)
                .lineLimit(1)
                .frame(minWidth: 40, maxWidth: .infinity, alignment: .leading)
            Text("Allowed")
                .hardwoodText(.tableHeader)
                .frame(width: DefenseColumns.value, alignment: .trailing)
            Text("vs league")
                .hardwoodText(.tableHeader)
                .frame(width: DefenseColumns.delta, alignment: .trailing)
            Text("Band")
                .hardwoodText(.tableHeader)
                .frame(width: DefenseColumns.band, alignment: .leading)
            Text("Opp. min")
                .hardwoodText(.tableHeader)
                .frame(width: DefenseColumns.minutes, alignment: .trailing)
        }
    }

    private func rowsView(_ shown: [DefenseBarRowData]) -> some View {
        let top = scaleMax(for: shown)
        let hidesDelta = DefenseDisplay.hidesDelta(withheld: withheld)
        let bandColumn = showsBandColumn
        return VStack(alignment: .leading, spacing: Spacing.xs) {
            ForEach(shown) { row in
                DefenseBarRow(row: row,
                              scaleMax: top,
                              density: density,
                              showsDelta: !hidesDelta,
                              isGreyed: isGreyed,
                              showsBandColumn: bandColumn)
            }
        }
        .opacity(hidesDelta ? 0.55 : 1.0)
    }
}

#if DEBUG
#Preview("Defence bars") {
    VStack(alignment: .leading, spacing: Spacing.xl) {
        DefenseBucketBars(buckets: LeagueDefenseDocument.preview.buckets,
                          withheld: LeagueDefenseDocument.preview.withheld,
                          provisional: LeagueDefenseDocument.preview.provisional ?? false,
                          density: .full)
        DefenseBucketBars(buckets: LeagueDefenseDocument.preview.buckets,
                          withheld: LeagueDefenseDocument.preview.withheld,
                          provisional: LeagueDefenseDocument.preview.provisional ?? false,
                          density: .compact)
    }
    .padding(Spacing.lg)
    .hardwoodBackground()
}
#endif
