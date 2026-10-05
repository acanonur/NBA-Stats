import Foundation
import SwiftUI

/// `defense_by_position`: the points a defence allows, by the position of the player who scored them.
///
/// One tile for both shapes of the payload. `payload.isLeagueTable` (no `team`, a `teams` list)
/// draws the table of every team; otherwise it draws one team's breakdown.
///
/// WHAT THE NUMBERS ARE
/// Points scored by opposing players *listed at* a position. They do not say who guarded whom, and
/// every payload carries a `caveat` that says so; the tile always prints it (one line on a medium
/// tile, in full on a large one). The tile never ranks anything: a league table keeps the server's
/// order, there is no rank column, and a position is toned only by the `band` the server sent. A
/// team that is `provisional` or `withheld` is marked as such, and a withheld team's raw points are
/// dimmed and carry no difference at all.
///
/// SIZES
/// - Team, medium: the short headline, the withheld message, one bar row per position, the caveat.
///   Large adds each position's share of the points beside the league's, the checksum line, and the
///   server's own method sentence.
/// - League table, medium: the first six rows. Large: every row. Columns are the positions the rows
///   carry (three for the NBA, five for the EuroLeague's workbook scheme).
///
/// Everything shown is a field of `LeagueDefenseDocument` run through `LeagueFormatting`.
public struct DefenseByPositionWidget: View {

    private let payload: LeagueDefenseDocument
    private let size: WidgetSize

    public init(payload: LeagueDefenseDocument, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Derived

    private var isLarge: Bool { size == .large }

    private var hasContent: Bool {
        payload.team != nil || payload.teams != nil || payload.buckets != nil
    }

    private var isEstimated: Bool {
        payload.availability == "estimated" || payload.scheme == "workbook5"
    }

    /// `EuroLeague · 2026-27 · Per game`.
    private var contextText: String {
        var parts: [String] = []
        if let key = LeagueKey(rawValue: payload.league ?? "") {
            parts.append(key.displayName)
        }
        if let season = payload.season, !season.isEmpty {
            parts.append(season)
        }
        if let basis = payload.basis, !basis.isEmpty {
            parts.append(LeagueFormatting.defenseBasisWord(basis))
        }
        return parts.joined(separator: " · ")
    }

    private var methodSentence: String {
        payload.methodMessage ?? ""
    }

    /// True when the reader chose the per-minute basis. On that basis the server judges each
    /// `band` (and index) from points per opponent minute, while `deltaPerGame` stays per game:
    /// the two units must never share a cell unexplained (backend shared/defense_position.py).
    private var isPerMinute: Bool {
        payload.basis == "perMinute"
    }

    /// The regulation length the per-minute rates are scaled to: the payload's own, else the
    /// league's (48 NBA, 40 EuroLeague).
    private var regulationMinutes: Int {
        if let minutes = payload.regulationMinutes, minutes > 0 {
            return minutes
        }
        return LeagueKey(rawValue: payload.league ?? "")?.regulationMinutes ?? 48
    }

    /// The one-team breakdown draws per-game bars and differences whatever the basis (the bars
    /// are DefenseBucketBars, shared with the Mac screens), so on the per-minute basis this says
    /// which unit the colours come from. Same sentence as the Mac Defence screen's.
    private var perMinuteBandNote: String {
        "The bands use points per " + String(regulationMinutes)
            + " opponent minutes. The bars and differences are points per game."
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            if !hasContent {
                Text("No defence figures yet")
                    .hardwoodText(.caption)
            } else if payload.isLeagueTable {
                leagueTable
            } else {
                teamBreakdown
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            HStack(spacing: Spacing.sm) {
                if !contextText.isEmpty {
                    Text(contextText)
                        .hardwoodText(.caption)
                }
                if isEstimated {
                    EstimatedBadge(text: "Estimated positions")
                }
                Spacer(minLength: 0)
            }
            if payload.freshness?.isDemo == true {
                LeagueStrip(symbol: "exclamationmark.triangle.fill",
                            text: "Invented demo league — not real games",
                            tint: Palette.warning)
            }
        }
    }

    // MARK: One team

    private var teamBreakdown: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            if let team = payload.team {
                Text(team.displayName)
                    .hardwoodText(.sectionTitle)
                    .lineLimit(1)
            }
            DefenseBreakdownView(document: payload, density: LeagueDensity.compact)
            if isPerMinute {
                Text(perMinuteBandNote)
                    .hardwoodText(.caption, color: Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            largeTeamExtras
        }
    }

    @ViewBuilder private var largeTeamExtras: some View {
        if isLarge {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                windowCaption
                DefenseShareRows(buckets: payload.buckets)
                DefenseReconciliationLine(reconciliation: payload.reconciliation)
                methodCaption
            }
        }
    }

    @ViewBuilder private var windowCaption: some View {
        let span = DefenseHeadlineText.windowText(payload.window)
        if !span.isEmpty {
            Text(span)
                .hardwoodText(.caption)
        }
    }

    @ViewBuilder private var methodCaption: some View {
        if !methodSentence.isEmpty {
            Text(methodSentence)
                .hardwoodText(.caption, color: Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: The league table

    private var allRows: [LeagueDefenseTeamRow] {
        payload.teams ?? []
    }

    /// Six rows on a medium tile, every row on a large one, always in the order the server sent.
    private var shownRows: [LeagueDefenseTeamRow] {
        isLarge ? allRows : Array(allRows.prefix(6))
    }

    /// The position columns, read from the first row that has buckets: the positions the server
    /// actually sent, in its order, without the unassigned bucket (which has no column).
    private var positionCodes: [String] {
        for row in allRows {
            let codes: [String] = (row.buckets ?? []).compactMap { bucket -> String? in
                guard let position = bucket.position, position != "unknown" else { return nil }
                return position
            }
            if !codes.isEmpty {
                return codes
            }
        }
        return []
    }

    private var tableSummary: String {
        var parts: [String] = []
        if let average = payload.leaguePointsAllowedPerGame {
            parts.append("League allows " + LeagueFormatting.number(average) + " per game")
        }
        let span = DefenseHeadlineText.windowText(payload.window)
        if !span.isEmpty {
            parts.append(span)
        }
        return parts.joined(separator: " · ")
    }

    private var leagueTable: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            if !tableSummary.isEmpty {
                Text(tableSummary)
                    .hardwoodText(.widgetTitle)
                    .lineLimit(2)
            }
            if allRows.isEmpty {
                Text("No teams to show yet")
                    .hardwoodText(.caption)
            } else {
                tableGrid
                tableFooter
            }
        }
    }

    private var tableGrid: some View {
        Grid(alignment: .trailing, horizontalSpacing: Spacing.md, verticalSpacing: Spacing.xs) {
            GridRow {
                Text("Team")
                    .hardwoodText(.tableHeader)
                    .gridColumnAlignment(.leading)
                Text("PA/G")
                    .hardwoodText(.tableHeader)
                    .help("Points allowed per game")
                ForEach(positionCodes, id: \.self) { code in
                    Text(code)
                        .hardwoodText(.tableHeader)
                }
                Text("")
                    .hardwoodText(.tableHeader)
            }
            ForEach(Array(shownRows.enumerated()), id: \.offset) { pair in
                GridRow {
                    Text(pair.element.team?.displayAbbr ?? Formatting.emDash)
                        .hardwoodText(.tableCell)
                        .gridColumnAlignment(.leading)
                    Text(LeagueFormatting.number(pair.element.pointsAllowedPerGame))
                        .hardwoodText(.tableCell)
                    ForEach(positionCodes, id: \.self) { code in
                        DefenseTileCell(bucket: DefenseByPositionWidget.bucket(in: pair.element, position: code),
                                        withheld: pair.element.withheld,
                                        provisional: pair.element.provisional ?? false,
                                        perMinute: isPerMinute)
                    }
                    DefenseTileStatus(withheld: pair.element.withheld,
                                      provisional: pair.element.provisional ?? false,
                                      isWide: isLarge)
                }
            }
        }
    }

    private static func bucket(in row: LeagueDefenseTeamRow, position: String) -> LeagueDefenseBucket? {
        (row.buckets ?? []).first { $0.position == position }
    }

    @ViewBuilder private var tableFooter: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            if !isLarge && allRows.count > shownRows.count {
                Text("Showing " + String(shownRows.count) + " of " + String(allRows.count) + " teams")
                    .hardwoodText(.caption)
            }
            if isLarge {
                Text(tableLegend)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                methodCaption
                CaveatFooter(text: payload.caveat)
            } else {
                mediumCaveat
            }
        }
    }

    /// What the numbers in a table cell are, in the unit the basis says.
    private var tableLegend: String {
        if isPerMinute {
            return "PA/G is points allowed per game. Each position shows points per "
                + String(regulationMinutes)
                + " opponent minutes at that position, with the league's rate beneath it."
        }
        return "PA/G is points allowed per game. Beneath each position, the difference from the league average."
    }

    @ViewBuilder private var mediumCaveat: some View {
        if let caveat = payload.caveat, !caveat.isEmpty {
            Text(caveat)
                .hardwoodText(.caption)
                .lineLimit(1)
                .help(caveat)
        }
    }
}

// MARK: - Table cells

/// One position of one team in the table: the points allowed per game and, beneath it, the
/// difference from the league average. Only the server's `band` tones the difference, and only
/// when the row is neither provisional nor withheld; a row withheld for too few games shows its raw
/// points dimmed and no difference at all.
///
/// On the per-minute basis the cell shows the server's `pointsPerRegulationMinutes` with the
/// league's `leagueRate` beneath it ("lg 22.9"), the same pair the Mac Defence table shows. The
/// band was judged per minute, so it tones a per-minute pair; the per-game `deltaPerGame` is not
/// shown there, because a per-game difference painted by a per-minute verdict can contradict
/// itself (a green "+2.0"). The app subtracts nothing.
private struct DefenseTileCell: View {
    let bucket: LeagueDefenseBucket?
    let withheld: LeagueWithheld?
    let provisional: Bool
    let perMinute: Bool

    private var primary: Double? {
        perMinute ? bucket?.pointsPerRegulationMinutes : bucket?.pointsAllowedPerGame
    }

    /// The figure beneath: the league's rate (per minutes) or the difference (per game); empty
    /// when the server sent none.
    private var secondary: String {
        if perMinute {
            guard let rate = bucket?.leagueRate else { return "" }
            return "lg " + LeagueFormatting.number(rate)
        }
        guard let delta = bucket?.deltaPerGame else { return "" }
        return LeagueFormatting.signed(delta)
    }

    private var hidesDelta: Bool {
        DefenseDisplay.hidesDelta(withheld: withheld)
    }

    private var deltaTint: Color {
        DefenseDisplay.deltaTint(band: bucket?.band,
                                 greyed: DefenseDisplay.isGreyed(withheld: withheld, provisional: provisional))
    }

    var body: some View {
        VStack(alignment: .trailing, spacing: 0) {
            Text(LeagueFormatting.number(primary))
                .hardwoodText(.tableCell)
            if !hidesDelta && !secondary.isEmpty {
                Text(secondary)
                    .hardwoodText(.caption, color: deltaTint)
            }
        }
        .opacity(hidesDelta ? 0.55 : 1.0)
    }
}

/// The last column of a table row: Withheld (with the server's reason as a tooltip), Provisional,
/// or nothing. A large tile spells the word out; a medium one keeps to a symbol.
private struct DefenseTileStatus: View {
    let withheld: LeagueWithheld?
    let provisional: Bool
    let isWide: Bool

    var body: some View {
        if let withheld = withheld {
            mark(text: "Withheld",
                 symbol: "exclamationmark.triangle",
                 help: withheld.message ?? LeagueFormatting.withheldReasonWord(withheld.reason))
        } else if provisional {
            mark(text: "Provisional",
                 symbol: "hourglass",
                 help: "Few games so far, so these numbers can still move a lot.")
        } else {
            Color.clear
                .frame(width: 1, height: 1)
        }
    }

    @ViewBuilder private func mark(text: String, symbol: String, help: String) -> some View {
        if isWide {
            LeagueChip(text: text, symbol: symbol, tint: Palette.warning)
                .help(help)
        } else {
            Image(systemName: symbol)
                .foregroundStyle(Palette.warning)
                .help(help)
                .accessibilityLabel(text)
        }
    }
}

#if DEBUG
#Preview("Defence by position") {
    ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            DefenseByPositionWidget(payload: LeagueDefenseDocument.preview, size: .large)
                .hardwoodCard()
            DefenseByPositionWidget(payload: LeagueDefenseDocument.preview, size: .medium)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
