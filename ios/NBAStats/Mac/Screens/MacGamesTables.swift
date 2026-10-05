#if os(macOS)
import SwiftUI

// The games list of the Box Scores screen: the EuroLeague's schedule and results (the workbook's
// Latest Games sheet and its round sheets) and the NBA's scoreboard for a day.
//
// WHAT A ROW SAYS
// A game's score appears only once the game is final. A game that should be over and has no stored
// result says "Result pending", which is not the same thing as upcoming; a postponed game says so.
// The EuroLeague's tip-off is shown in the reader's own time zone with Berlin's in the tooltip,
// because the league's day is Berlin's. Nothing is derived: every cell is a field of the game the
// server sent.
//
// ONE ROW TYPE, TWO TABLES
// Both leagues' games become the same `MacGameListRow`. The EuroLeague table has seven columns
// (it also shows the round and the venue); the NBA's has four (it has neither).

// MARK: - Row

struct MacGameListRow: Identifiable, Hashable {
    let id: String
    let order: Int
    let title: String
    let date: String
    let dateSort: String
    let dateHelp: String
    let round: MacStatValue
    let home: String
    let homeName: String
    let away: String
    let awayName: String
    let score: MacStatValue
    let venue: String
    let status: String
    let statusSort: Int
    let isPending: Bool
    let isFinal: Bool

    static let elCopyHeader: [String] = ["Date", "Round", "Home", "Away", "Score", "Venue", "Status"]
    static let nbaCopyHeader: [String] = ["Date", "Away", "Home", "Score", "Status"]

    var elCopyCells: [String] {
        [date, round.text, home, away, score.text, venue, status]
    }

    var nbaCopyCells: [String] {
        [date, away, home, score.text, status]
    }
}

enum MacGameListRows {

    /// The EuroLeague's games, in schedule order.
    static func rows(from list: ElGamesList?) -> [MacGameListRow] {
        guard let games = list?.games else { return [] }
        var result: [MacGameListRow] = []
        var index = 0
        for game in games {
            result.append(make(game, index: index))
            index += 1
        }
        return result
    }

    /// The NBA's games of one day, in the order the server sent them.
    static func rows(from board: ScoreboardResponse?) -> [MacGameListRow] {
        guard let games = board?.games else { return [] }
        var result: [MacGameListRow] = []
        var index = 0
        for game in games {
            result.append(make(game, index: index))
            index += 1
        }
        return result
    }

    private static func make(_ game: LeagueGameRef, index: Int) -> MacGameListRow {
        let status: String = game.status ?? ""
        let isFinal: Bool = status == "final"
        let overtime: String = leagueOvertimeText(game.overtimePeriods)
        var statusText: String = LeagueFormatting.gameStatusWord(game.status)
        if isFinal && !overtime.isEmpty {
            statusText += " (" + overtime + ")"
        }
        var scoreText: String = Formatting.emDash
        var scoreSort: Double = -Double.infinity
        if isFinal {
            scoreText = LeagueFormatting.score(game.homePts, game.awayPts)
            scoreSort = LeagueFormatting.sortKey(game.homePts)
        }
        let berlin: String = LeagueFormatting.berlinTime(game.tipoffUtc)
        var berlinHelp: String = ""
        if berlin != Formatting.emDash {
            berlinHelp = "Berlin time: " + berlin
        }
        let score = MacStatValue(words: scoreText, sort: scoreSort)
        return MacGameListRow(
            id: game.gameId ?? ("game-" + String(index)),
            order: index,
            title: game.matchupText(for: .euroleague),
            date: dateText(game),
            dateSort: game.tipoffUtc ?? game.date ?? "",
            dateHelp: berlinHelp,
            round: MacStatValue(whole: game.round),
            home: game.home?.displayAbbr ?? Formatting.emDash,
            homeName: game.home?.displayName ?? Formatting.emDash,
            away: game.away?.displayAbbr ?? Formatting.emDash,
            awayName: game.away?.displayName ?? Formatting.emDash,
            score: score,
            venue: game.venue ?? Formatting.emDash,
            status: statusText,
            statusSort: statusOrder(status),
            isPending: status == "resultPending",
            isFinal: isFinal
        )
    }

    private static func make(_ game: GameRef, index: Int) -> MacGameListRow {
        let isFinal: Bool = game.status == .final
        var statusText: String = LeagueFormatting.gameStatusWord(game.statusRaw ?? game.status.rawValue)
        if game.status == .live {
            statusText = "Live"
        }
        var scoreText: String = Formatting.emDash
        var scoreSort: Double = -Double.infinity
        if isFinal {
            scoreText = LeagueFormatting.score(game.awayPts, game.homePts)
            scoreSort = LeagueFormatting.sortKey(game.awayPts)
        }
        let score = MacStatValue(words: scoreText, sort: scoreSort)
        return MacGameListRow(
            id: game.gameId,
            order: index,
            title: game.away.abbr + " at " + game.home.abbr,
            date: Formatting.shortGameDate(game.date),
            dateSort: game.date,
            dateHelp: "",
            round: MacStatValue.missing,
            home: game.home.abbr,
            homeName: game.home.name,
            away: game.away.abbr,
            awayName: game.away.name,
            score: score,
            venue: "",
            status: statusText,
            statusSort: statusOrder(game.statusRaw ?? game.status.rawValue),
            isPending: false,
            isFinal: isFinal
        )
    }

    /// The tip-off in the reader's zone, or the league's day when there is none.
    private static func dateText(_ game: LeagueGameRef) -> String {
        let time = LeagueFormatting.tipoff(game.tipoffUtc)
        if time != Formatting.emDash {
            return time
        }
        return LeagueFormatting.leagueDate(game.date)
    }

    /// Scheduled first, then live, result pending, final and postponed.
    private static func statusOrder(_ status: String) -> Int {
        switch status {
        case "scheduled":
            return 0
        case "live":
            return 1
        case "resultPending":
            return 2
        case "final":
            return 3
        case "postponed":
            return 4
        default:
            return 5
        }
    }
}

/// `2026-10-19` as a date at noon in the reader's calendar, so a daylight-saving change can never
/// move it to another day.
enum MacGamesDay {

    static func date(from day: String?) -> Date? {
        guard let day = day else { return nil }
        let parts: [String] = day.split(separator: "-").map { String($0) }
        guard parts.count == 3,
              let year = Int(parts[0]),
              let month = Int(parts[1]),
              let dayNumber = Int(parts[2]) else {
            return nil
        }
        var components = DateComponents()
        components.year = year
        components.month = month
        components.day = dayNumber
        components.hour = 12
        return Calendar.current.date(from: components)
    }
}

// MARK: - Cells

/// A game's status: its words, or a "Result pending" chip.
struct MacGameStatusCell: View {

    private let text: String
    private let isPending: Bool

    init(text: String, isPending: Bool) {
        self.text = text
        self.isPending = isPending
    }

    var body: some View {
        if isPending {
            HStack(spacing: 0) {
                ResultPendingChip()
                Spacer(minLength: 0)
            }
        } else {
            Text(text)
                .hardwoodText(.tableCell, monospacedDigits: false)
                .lineLimit(1)
        }
    }
}

/// A date or time, with Berlin's time as the tooltip when there is one.
struct MacGameDateCell: View {

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

// MARK: - EuroLeague table

/// Games, EuroLeague: Date, Round, Home, Away, Score, Venue, Status (7 columns).
struct MacElGamesTable: View {

    private let rows: [MacGameListRow]
    @Binding private var selection: MacGameListRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacGameListRow>]
    private let onOpenWindow: (MacGameListRow.ID) -> Void
    private let onOpenMatchup: (MacGameListRow.ID) -> Void

    init(rows: [MacGameListRow],
         selection: Binding<MacGameListRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacGameListRow>]>,
         onOpenWindow: @escaping (MacGameListRow.ID) -> Void,
         onOpenMatchup: @escaping (MacGameListRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onOpenWindow = onOpenWindow
        self.onOpenMatchup = onOpenMatchup
    }

    private var sortedRows: [MacGameListRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Date", value: \MacGameListRow.dateSort) { row in
                MacGameDateCell(text: row.date, help: row.dateHelp)
            }
            .width(min: 96, ideal: 128, max: 170)
            TableColumn("Round", value: \MacGameListRow.round.sort) { row in
                MacStatCell(value: row.round)
            }
            .width(min: 44, ideal: 52, max: 70)
            TableColumn("Home", value: \MacGameListRow.home) { row in
                TeamCell(abbr: row.home, name: row.homeName)
            }
            .width(min: 62, ideal: 76, max: 110)
            TableColumn("Away", value: \MacGameListRow.away) { row in
                TeamCell(abbr: row.away, name: row.awayName)
            }
            .width(min: 62, ideal: 76, max: 110)
            TableColumn("Score", value: \MacGameListRow.score.sort) { row in
                MacStatCell(value: row.score)
            }
            .width(min: 60, ideal: 72, max: 96)
            TableColumn("Venue", value: \MacGameListRow.venue) { row in
                TextCell(text: row.venue)
            }
            .width(min: 80, ideal: 120)
            TableColumn("Status", value: \MacGameListRow.statusSort) { row in
                MacGameStatusCell(text: row.status, isPending: row.isPending)
            }
            .width(min: 96, ideal: 120, max: 160)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacGameListRow.ID.self) { ids in
            menuItems(for: ids)
        } primaryAction: { ids in
            if let id = ids.first {
                onOpenWindow(id)
            }
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacGameListRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Open in New Window") {
                onOpenWindow(id)
            }
            Button("Open Matchup") {
                onOpenMatchup(id)
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacGameListRow.elCopyHeader, rows: [row.elCopyCells])
            }
        }
    }
}

// MARK: - NBA table

/// Games, NBA: Date, Away, Home, Score, Status (5 columns).
struct MacNbaGamesTable: View {

    private let rows: [MacGameListRow]
    @Binding private var selection: MacGameListRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacGameListRow>]
    private let onOpenWindow: (MacGameListRow.ID) -> Void
    private let onOpenMatchup: (MacGameListRow.ID) -> Void

    init(rows: [MacGameListRow],
         selection: Binding<MacGameListRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacGameListRow>]>,
         onOpenWindow: @escaping (MacGameListRow.ID) -> Void,
         onOpenMatchup: @escaping (MacGameListRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onOpenWindow = onOpenWindow
        self.onOpenMatchup = onOpenMatchup
    }

    private var sortedRows: [MacGameListRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Date", value: \MacGameListRow.dateSort) { row in
                MacGameDateCell(text: row.date, help: row.dateHelp)
            }
            .width(min: 80, ideal: 100, max: 140)
            TableColumn("Away", value: \MacGameListRow.away) { row in
                TeamCell(abbr: row.away, name: row.awayName)
            }
            .width(min: 62, ideal: 76, max: 110)
            TableColumn("Home", value: \MacGameListRow.home) { row in
                TeamCell(abbr: row.home, name: row.homeName)
            }
            .width(min: 62, ideal: 76, max: 110)
            TableColumn("Score", value: \MacGameListRow.score.sort) { row in
                MacStatCell(value: row.score)
            }
            .width(min: 60, ideal: 72, max: 96)
            TableColumn("Status", value: \MacGameListRow.statusSort) { row in
                MacGameStatusCell(text: row.status, isPending: row.isPending)
            }
            .width(min: 80, ideal: 100, max: 140)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacGameListRow.ID.self) { ids in
            menuItems(for: ids)
        } primaryAction: { ids in
            if let id = ids.first {
                onOpenWindow(id)
            }
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacGameListRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Open in New Window") {
                onOpenWindow(id)
            }
            Button("Open Matchup") {
                onOpenMatchup(id)
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacGameListRow.nbaCopyHeader, rows: [row.nbaCopyCells])
            }
        }
    }
}
#endif
