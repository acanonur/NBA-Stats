#if os(macOS)
import SwiftUI

// The Round (EuroLeague) and Slate (NBA) table: one row per game, with the projected score for
// each side, the margin, the projected winner and the game's status.
//
// WHY ROWS ARE BUILT FIRST AND CELLS ARE DUMB
// A native `Table` of ten sortable columns is the heaviest thing the type checker sees in this app.
// So everything that needs thought happens before the table is built, in `MacRoundRows`: each
// payload game becomes one `MacRoundRow` made only of display strings and plain sort keys
// (a `String`, a `Double` or an `Int`, never an Optional). The table body is then ten one-line
// columns, and each custom cell is one small view of its own, with no ternary and no string
// interpolation inside a cell closure (MAC_DESIGN 6.5).
//
// WHAT A ROW SHOWS, AND WHAT IT DOES NOT
// Every number is a value the server sent, run through `LeagueFormatting`; a value the server did
// not send is an em dash, and sorts below every real one. Nothing is computed here. The projected
// winner is the server's own `summary` ("ZZA by 8.7"), or "Toss-up" when the server says so; there
// is no likelihood of winning anywhere on this screen, because the server has none to send.
//
// A game that was played, or that began, before its projection was made carries a small mark in
// its Status cell: that projection is a reconstruction, not a prediction, and the mark says so in
// its tooltip. The decision comes from the server's own fields (`LeagueGameProjection.
// isComputedAfterTheFact`); the app compares no timestamps.

// MARK: - Row

/// One game of a round or a slate, ready to draw and to sort.
struct MacRoundRow: Identifiable, Hashable {
    let id: String
    let tip: String
    let tipHelp: String
    let tipSort: String
    let home: String
    let homeName: String
    let away: String
    let awayName: String
    let projHome: String
    let projHomeSort: Double
    let projAway: String
    let projAwaySort: Double
    let margin: String
    let marginSort: Double
    let winner: String
    let combined: String
    let combinedSort: Double
    let injuryEffect: String
    let injuryEffectSort: Double
    let status: String
    let statusSort: Int
    let isFinal: Bool
    let isPending: Bool
    let isAfterTheFact: Bool

    /// The column titles of the table, in order, for "Copy Table".
    static let copyHeader: [String] = [
        "Tip", "Home", "Away", "Proj home", "Proj away", "Margin",
        "Projected winner", "Combined pts", "Injury effect", "Status"
    ]

    /// The row as the table shows it, for "Copy Table" and "Copy Row".
    var copyCells: [String] {
        [tip, home, away, projHome, projAway, margin, winner, combined, injuryEffect, status]
    }
}

// MARK: - Building the rows

enum MacRoundRows {

    /// One row per projected game, in the order the server sent them.
    static func rows(from games: [LeagueGameProjection], league: LeagueKey) -> [MacRoundRow] {
        var result: [MacRoundRow] = []
        var index = 0
        for projection in games {
            result.append(row(from: projection, league: league, index: index))
            index += 1
        }
        return result
    }

    static func row(from projection: LeagueGameProjection, league: LeagueKey, index: Int) -> MacRoundRow {
        let game: LeagueGameRef? = projection.game
        let homeTeam: LeagueTeamRef? = game?.home ?? projection.home?.team
        let awayTeam: LeagueTeamRef? = game?.away ?? projection.away?.team
        let homePoints: Double? = projection.home?.projectedPoints
        let awayPoints: Double? = projection.away?.projectedPoints
        let status: String = game?.status ?? ""
        let gameId: String = game?.gameId ?? ("row-" + String(index))
        let tipSort: String = game?.tipoffUtc ?? game?.date ?? ""
        let homeCode: String = homeTeam?.displayAbbr ?? Formatting.emDash
        let homeName: String = homeTeam?.displayName ?? Formatting.emDash
        let awayCode: String = awayTeam?.displayAbbr ?? Formatting.emDash
        let awayName: String = awayTeam?.displayName ?? Formatting.emDash
        let margin: Double? = projection.margin
        let combined: Double? = projection.combinedPoints
        let injuryEffect: Double? = projection.combinedAvailabilityEffect

        return MacRoundRow(
            id: gameId,
            tip: tipText(game),
            tipHelp: berlinHelp(game, league: league),
            tipSort: tipSort,
            home: homeCode,
            homeName: homeName,
            away: awayCode,
            awayName: awayName,
            projHome: LeagueFormatting.number(homePoints),
            projHomeSort: LeagueFormatting.sortKey(homePoints),
            projAway: LeagueFormatting.number(awayPoints),
            projAwaySort: LeagueFormatting.sortKey(awayPoints),
            margin: LeagueFormatting.signed(margin),
            marginSort: LeagueFormatting.sortKey(margin),
            winner: winnerText(projection),
            combined: LeagueFormatting.number(combined),
            combinedSort: LeagueFormatting.sortKey(combined),
            injuryEffect: LeagueFormatting.signed(injuryEffect),
            injuryEffectSort: LeagueFormatting.sortKey(injuryEffect),
            status: LeagueFormatting.gameStatusText(game),
            statusSort: statusOrder(status),
            isFinal: status == "final",
            isPending: status == "resultPending",
            isAfterTheFact: projection.isComputedAfterTheFact
        )
    }

    /// The tip-off in the reader's own time zone; the league's day when no tip-off is known (the
    /// NBA often has none yet).
    static func tipText(_ game: LeagueGameRef?) -> String {
        let time = LeagueFormatting.tipoff(game?.tipoffUtc)
        if time != Formatting.emDash {
            return time
        }
        return LeagueFormatting.leagueDate(game?.date)
    }

    /// The EuroLeague's day is Berlin's, so its rows say what time it is there as well.
    static func berlinHelp(_ game: LeagueGameRef?, league: LeagueKey) -> String {
        guard league == .euroleague else { return "" }
        let time = LeagueFormatting.berlinTime(game?.tipoffUtc)
        if time == Formatting.emDash {
            return ""
        }
        return "Berlin time: " + time
    }

    /// The server's own words for who is projected to win, or "Toss-up".
    static func winnerText(_ projection: LeagueGameProjection) -> String {
        if projection.isTossUp == true {
            return "Toss-up"
        }
        return projection.summary ?? Formatting.emDash
    }

    /// Scheduled first, then result pending, final and postponed. A value this build does not know
    /// sorts last.
    static func statusOrder(_ status: String) -> Int {
        switch status {
        case "scheduled":
            return 0
        case "resultPending":
            return 1
        case "final":
            return 2
        case "postponed":
            return 3
        default:
            return 4
        }
    }
}

// MARK: - Table

struct MacRoundTable: View {

    private let rows: [MacRoundRow]
    @Binding private var selection: MacRoundRow.ID?
    private let onShowDetails: (MacRoundRow.ID) -> Void
    private let onOpenMatchup: (MacRoundRow.ID) -> Void
    private let onOpenBoxScore: (MacRoundRow.ID) -> Void
    @State private var sortOrder: [KeyPathComparator<MacRoundRow>] = [KeyPathComparator(\MacRoundRow.tipSort)]

    init(rows: [MacRoundRow],
         selection: Binding<MacRoundRow.ID?>,
         onShowDetails: @escaping (MacRoundRow.ID) -> Void,
         onOpenMatchup: @escaping (MacRoundRow.ID) -> Void,
         onOpenBoxScore: @escaping (MacRoundRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        self.onShowDetails = onShowDetails
        self.onOpenMatchup = onOpenMatchup
        self.onOpenBoxScore = onOpenBoxScore
    }

    private var sortedRows: [MacRoundRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Tip", value: \MacRoundRow.tipSort) { row in
                RoundTipCell(text: row.tip, help: row.tipHelp)
            }
            .width(min: 120, ideal: 150, max: 190)
            TableColumn("Home", value: \MacRoundRow.home) { row in
                RoundTeamCell(abbr: row.home, name: row.homeName)
            }
            .width(min: 70, ideal: 84, max: 120)
            TableColumn("Away", value: \MacRoundRow.away) { row in
                RoundTeamCell(abbr: row.away, name: row.awayName)
            }
            .width(min: 70, ideal: 84, max: 120)
            TableColumn("Proj home", value: \MacRoundRow.projHomeSort) { row in
                NumCell(text: row.projHome)
            }
            .width(min: 64, ideal: 76, max: 100)
            TableColumn("Proj away", value: \MacRoundRow.projAwaySort) { row in
                NumCell(text: row.projAway)
            }
            .width(min: 64, ideal: 76, max: 100)
            TableColumn("Margin", value: \MacRoundRow.marginSort) { row in
                NumCell(text: row.margin)
            }
            .width(min: 56, ideal: 66, max: 90)
            TableColumn("Projected winner", value: \MacRoundRow.winner) { row in
                TextCell(text: row.winner)
            }
            .width(min: 110, ideal: 140, max: 200)
            TableColumn("Combined pts", value: \MacRoundRow.combinedSort) { row in
                NumCell(text: row.combined)
            }
            .width(min: 76, ideal: 92, max: 120)
            TableColumn("Injury effect", value: \MacRoundRow.injuryEffectSort) { row in
                NumCell(text: row.injuryEffect)
            }
            .width(min: 76, ideal: 92, max: 120)
            TableColumn("Status", value: \MacRoundRow.statusSort) { row in
                RoundStatusCell(text: row.status,
                                isPending: row.isPending,
                                isAfterTheFact: row.isAfterTheFact)
            }
            .width(min: 120, ideal: 160)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacRoundRow.ID.self) { ids in
            menuItems(for: ids)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacRoundRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Show Details") {
                onShowDetails(id)
            }
            Button("Open Matchup") {
                onOpenMatchup(id)
            }
            if row.isFinal {
                Button("Open Box Score") {
                    onOpenBoxScore(id)
                }
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacRoundRow.copyHeader, rows: [row.copyCells])
            }
        }
    }
}

// MARK: - Cells

/// The tip-off. The tooltip is Berlin's time for a EuroLeague game.
private struct RoundTipCell: View {

    private let text: String
    private let help: String

    init(text: String, help: String) {
        self.text = text
        self.help = help
    }

    var body: some View {
        Text(text)
            .hardwoodText(.tableCell, monospacedDigits: false)
            .lineLimit(1)
            .help(help)
    }
}

/// A team's code as a small badge.
private struct RoundTeamCell: View {

    private let abbr: String
    private let name: String

    init(abbr: String, name: String) {
        self.abbr = abbr
        self.name = name
    }

    var body: some View {
        TeamCell(abbr: abbr, name: name)
    }
}

/// The status of a game: its score or tip-off, a "Result pending" chip for a game that should be
/// over with no result yet, and a mark on a projection that was made after the fact.
private struct RoundStatusCell: View {

    private let text: String
    private let isPending: Bool
    private let isAfterTheFact: Bool

    init(text: String, isPending: Bool, isAfterTheFact: Bool) {
        self.text = text
        self.isPending = isPending
        self.isAfterTheFact = isAfterTheFact
    }

    var body: some View {
        HStack(spacing: Spacing.xs) {
            label
            if isAfterTheFact {
                Image(systemName: "clock.arrow.circlepath")
                    .foregroundStyle(Palette.warning)
                    .help(LeagueGameProjection.afterTheFactNote)
            }
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder private var label: some View {
        if isPending {
            ResultPendingChip()
        } else {
            Text(text)
                .hardwoodText(.tableCell)
                .lineLimit(1)
        }
    }
}
#endif
