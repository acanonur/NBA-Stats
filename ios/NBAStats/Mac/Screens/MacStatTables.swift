#if os(macOS)
import SwiftUI

// The pieces every workbook screen shares (one displayed number, the stat key table, the status
// cell, the load key) and the Season Stats tables: one row type and two column sets per league.
//
// WHY A ROW IS MADE OF `MacStatValue`s
// A native `Table` column needs two things from a row: what to show and what to sort by. For a
// number those are a string (already formatted, an em dash when the server sent nothing) and a
// `Double` (negative infinity when it sent nothing, so a missing value sorts below every real one
// and is never mistaken for 0). `MacStatValue` is that pair plus a tooltip. A row then holds one
// `MacStatValue` per statistic, a column sorts by `\Row.points.sort`, and a cell is one line:
// `MacStatCell(value: row.points)`. Nothing is computed on the way: the text is
// `LeagueFormatting` applied to a number the server sent.
//
// ONE KEY TABLE
// The EuroLeague's stats arrive as `values[key]` and `gamesWithStat[key]` (ElPlayerStatsTable), with
// keys the backend's metric catalogue defines (`contracts/metrics.json#/leagueMetrics/euroleague`).
// `ElStatKeys` is the only place those spellings are written in the app.
//
// COLUMN SETS
// A table is capped at ten columns and every column has to be sortable, and a column set cannot be
// chosen with an `if` inside a `Table` builder at macOS 14.0. So each column set is its own concrete
// `Table` struct, and the screen picks one with a `switch`. "Copy Table" copies every column of
// both sets, so a reader who wants them side by side gets them in a spreadsheet.

// MARK: - One displayed number

/// A number as it is shown (`text`), as it sorts (`sort`) and as it is explained (`help`).
struct MacStatValue: Hashable {
    let text: String
    let sort: Double
    let help: String
}

extension MacStatValue {

    /// A value the server did not send: an em dash that sorts below every real number.
    static let missing = MacStatValue(text: Formatting.emDash, sort: -Double.infinity, help: "")

    /// A plain number with `places` decimals.
    init(number: Double?, places: Int = 1, help: String = "") {
        self.init(text: LeagueFormatting.number(number, places: places),
                  sort: LeagueFormatting.sortKey(number),
                  help: help)
    }

    /// A number that shows its sign: `+2.4`.
    init(signed: Double?, places: Int = 1, help: String = "") {
        self.init(text: LeagueFormatting.signed(signed, places: places),
                  sort: LeagueFormatting.sortKey(signed),
                  help: help)
    }

    /// A fraction in [0, 1] shown as a percentage.
    init(fraction: Double?, places: Int = 1, help: String = "") {
        self.init(text: LeagueFormatting.percent(fraction, places: places),
                  sort: LeagueFormatting.sortKey(fraction),
                  help: help)
    }

    /// A whole number (a count of games, a round).
    init(whole: Int?, help: String = "") {
        self.init(text: LeagueFormatting.integer(whole),
                  sort: LeagueFormatting.sortKey(whole),
                  help: help)
    }

    /// Words with a sort position of their own (a score, "DNP").
    init(words: String, sort: Double, help: String = "") {
        self.init(text: words, sort: sort, help: help)
    }
}

/// A number in a table: right-aligned, digits that keep their width, the explanation as tooltip.
struct MacStatCell: View {

    private let value: MacStatValue

    init(value: MacStatValue) {
        self.value = value
    }

    var body: some View {
        Text(value.text)
            .hardwoodText(.tableCell)
            .lineLimit(1)
            .frame(maxWidth: .infinity, alignment: .trailing)
            .help(value.help)
    }
}

/// An availability status as a chip, or an em dash when nobody has reported one. A missing status
/// is never drawn as "Available": the workbook marked everybody it did not list available, and
/// Hardwood shows a status only when a source reported it.
struct MacOptionalStatusCell: View {

    private let status: String
    private let label: String

    init(status: String, label: String) {
        self.status = status
        self.label = label
    }

    var body: some View {
        if status.isEmpty {
            Text(Formatting.emDash)
                .hardwoodText(.tableCell, color: Palette.textTertiary)
        } else {
            StatusChipCell(status: status, label: label)
        }
    }
}

// MARK: - Small shared vocabulary

/// What a workbook screen's `.task(id:)` watches when its request may not exist yet (a EuroLeague
/// round that has not been chosen).
struct MacWorkbookLoadKey: Hashable {
    let route: LeagueRoute?
    let generation: Int
}

/// The EuroLeague's statistic keys, written once.
enum ElStatKeys {
    static let minutes = "min"
    static let points = "pts"
    static let rebounds = "reb"
    static let assists = "ast"
    static let steals = "stl"
    static let blocks = "blk"
    static let turnovers = "tov"
    static let threesMade = "fg3m"
    static let twoPointPct = "fg2_pct"
    static let threePointPct = "fg3_pct"
    static let freeThrowPct = "ft_pct"
    static let pir = "pir"
}

/// The two column sets a stats table offers.
enum MacStatColumnSet: String, CaseIterable, Identifiable, Hashable {
    case box
    case shooting

    var id: String { rawValue }

    var title: String {
        switch self {
        case .box:
            return "Box"
        case .shooting:
            return "Shooting"
        }
    }
}

/// The order availability statuses sort in: most serious first, "no report" last.
enum MacWorkbookStatus {

    static func order(_ status: String?) -> Int {
        switch status ?? "" {
        case "out":
            return 0
        case "doubtful":
            return 1
        case "questionable":
            return 2
        case "probable":
            return 3
        case "available":
            return 4
        default:
            return 5
        }
    }

    /// The raw status, or an empty string when there is none (a table cell draws an em dash).
    static func key(_ status: String?) -> String {
        status ?? ""
    }
}

// MARK: - EuroLeague Season Stats: rows

/// One player of the EuroLeague stats table, ready to draw and to sort.
struct MacElStatRow: Identifiable, Hashable {
    let id: String
    let order: Int
    let personCode: String
    let player: String
    let position: String
    let jersey: String
    let clubCode: String
    let club: String
    let clubName: String
    let games: MacStatValue
    let minutes: MacStatValue
    let points: MacStatValue
    let rebounds: MacStatValue
    let assists: MacStatValue
    let steals: MacStatValue
    let blocks: MacStatValue
    let turnovers: MacStatValue
    let threesMade: MacStatValue
    let twoPointPct: MacStatValue
    let threePointPct: MacStatValue
    let freeThrowPct: MacStatValue
    let pir: MacStatValue

    /// Every column of both sets, in order, for "Copy Table" and "Copy Row".
    static let copyHeader: [String] = [
        "Club", "Player", "GP", "MIN", "PTS", "REB", "AST", "STL", "BLK", "TOV",
        "3PM", "2P%", "3P%", "FT%", "PIR"
    ]

    var copyCells: [String] {
        [club, player, games.text, minutes.text, points.text, rebounds.text, assists.text,
         steals.text, blocks.text, turnovers.text, threesMade.text, twoPointPct.text,
         threePointPct.text, freeThrowPct.text, pir.text]
    }
}

enum MacElStatRows {

    /// One row per player, in the order the server sent them (best scorer first).
    static func rows(from table: ElPlayerStatsTable?, perMode: String) -> [MacElStatRow] {
        guard let source = table?.rows else { return [] }
        var result: [MacElStatRow] = []
        var index = 0
        for entry in source {
            result.append(make(entry, index: index, perMode: perMode))
            index += 1
        }
        return result
    }

    private static func make(_ entry: ElStatsRow, index: Int, perMode: String) -> MacElStatRow {
        let player: LeaguePlayerRef? = entry.player
        let team: LeagueTeamRef? = entry.team
        let code: String = player?.personCode ?? player?.id ?? ("row-" + String(index))
        let countPlaces: Int = perMode == "Totals" ? 0 : 1
        return MacElStatRow(
            id: code,
            order: index,
            personCode: code,
            player: player?.name ?? Formatting.emDash,
            position: player?.positionRaw ?? player?.position ?? "",
            jersey: player?.jersey ?? "",
            clubCode: team?.clubCode ?? team?.id ?? "",
            club: team?.displayAbbr ?? Formatting.emDash,
            clubName: team?.displayName ?? Formatting.emDash,
            games: MacStatValue(whole: entry.games),
            minutes: stat(entry, ElStatKeys.minutes, places: 1),
            points: stat(entry, ElStatKeys.points, places: countPlaces),
            rebounds: stat(entry, ElStatKeys.rebounds, places: countPlaces),
            assists: stat(entry, ElStatKeys.assists, places: countPlaces),
            steals: stat(entry, ElStatKeys.steals, places: countPlaces),
            blocks: stat(entry, ElStatKeys.blocks, places: countPlaces),
            turnovers: stat(entry, ElStatKeys.turnovers, places: countPlaces),
            threesMade: stat(entry, ElStatKeys.threesMade, places: countPlaces),
            twoPointPct: percentStat(entry, ElStatKeys.twoPointPct),
            threePointPct: percentStat(entry, ElStatKeys.threePointPct),
            freeThrowPct: percentStat(entry, ElStatKeys.freeThrowPct),
            pir: stat(entry, ElStatKeys.pir, places: countPlaces)
        )
    }

    private static func stat(_ entry: ElStatsRow, _ key: String, places: Int) -> MacStatValue {
        MacStatValue(number: entry.value(key), places: places, help: helpText(entry, key))
    }

    private static func percentStat(_ entry: ElStatsRow, _ key: String) -> MacStatValue {
        MacStatValue(fraction: entry.value(key), places: 1, help: helpText(entry, key))
    }

    /// "12 games with this stat": how many games the average is over, as the server counted them.
    private static func helpText(_ entry: ElStatsRow, _ key: String) -> String {
        guard let count = entry.gamesWithStat?[key]?.intValue else { return "" }
        let noun: String = count == 1 ? " game" : " games"
        return String(count) + noun + " with this stat"
    }

    /// The rows left after the name filter. A name matches the player or the club.
    static func filtered(_ rows: [MacElStatRow], name: String) -> [MacElStatRow] {
        let needle = name.trimmingCharacters(in: .whitespacesAndNewlines)
        if needle.isEmpty { return rows }
        return rows.filter { row in
            row.player.localizedCaseInsensitiveContains(needle)
                || row.clubName.localizedCaseInsensitiveContains(needle)
                || row.club.localizedCaseInsensitiveContains(needle)
        }
    }
}

// MARK: - EuroLeague Season Stats: tables

/// Box: Club, Player, GP and the counting stats (10 columns).
struct MacElStatBoxTable: View {

    private let rows: [MacElStatRow]
    @Binding private var selection: MacElStatRow.ID?
    private let onShowDetails: (MacElStatRow.ID) -> Void
    @Binding private var sortOrder: [KeyPathComparator<MacElStatRow>]

    init(rows: [MacElStatRow],
         selection: Binding<MacElStatRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacElStatRow>]>,
         onShowDetails: @escaping (MacElStatRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onShowDetails = onShowDetails
    }

    private var sortedRows: [MacElStatRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Club", value: \MacElStatRow.club) { row in
                TeamCell(abbr: row.club, name: row.clubName)
            }
            .width(min: 64, ideal: 78, max: 110)
            TableColumn("Player", value: \MacElStatRow.player) { row in
                TextCell(text: row.player)
            }
            .width(min: 140, ideal: 190)
            TableColumn("GP", value: \MacElStatRow.games.sort) { row in
                MacStatCell(value: row.games)
            }
            .width(min: 36, ideal: 44, max: 60)
            TableColumn("MIN", value: \MacElStatRow.minutes.sort) { row in
                MacStatCell(value: row.minutes)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("PTS", value: \MacElStatRow.points.sort) { row in
                MacStatCell(value: row.points)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("REB", value: \MacElStatRow.rebounds.sort) { row in
                MacStatCell(value: row.rebounds)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("AST", value: \MacElStatRow.assists.sort) { row in
                MacStatCell(value: row.assists)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("STL", value: \MacElStatRow.steals.sort) { row in
                MacStatCell(value: row.steals)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("BLK", value: \MacElStatRow.blocks.sort) { row in
                MacStatCell(value: row.blocks)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("TOV", value: \MacElStatRow.turnovers.sort) { row in
                MacStatCell(value: row.turnovers)
            }
            .width(min: 44, ideal: 54, max: 76)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacElStatRow.ID.self) { ids in
            menuItems(for: ids)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacElStatRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Show Details") {
                onShowDetails(id)
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacElStatRow.copyHeader, rows: [row.copyCells])
            }
        }
    }
}

/// Shooting: Club, Player, GP, 3PM, the three percentages and PIR (8 columns).
struct MacElStatShootingTable: View {

    private let rows: [MacElStatRow]
    @Binding private var selection: MacElStatRow.ID?
    private let onShowDetails: (MacElStatRow.ID) -> Void
    @Binding private var sortOrder: [KeyPathComparator<MacElStatRow>]

    init(rows: [MacElStatRow],
         selection: Binding<MacElStatRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacElStatRow>]>,
         onShowDetails: @escaping (MacElStatRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onShowDetails = onShowDetails
    }

    private var sortedRows: [MacElStatRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Club", value: \MacElStatRow.club) { row in
                TeamCell(abbr: row.club, name: row.clubName)
            }
            .width(min: 64, ideal: 78, max: 110)
            TableColumn("Player", value: \MacElStatRow.player) { row in
                TextCell(text: row.player)
            }
            .width(min: 140, ideal: 190)
            TableColumn("GP", value: \MacElStatRow.games.sort) { row in
                MacStatCell(value: row.games)
            }
            .width(min: 36, ideal: 44, max: 60)
            TableColumn("3PM", value: \MacElStatRow.threesMade.sort) { row in
                MacStatCell(value: row.threesMade)
            }
            .width(min: 48, ideal: 58, max: 80)
            TableColumn("2P%", value: \MacElStatRow.twoPointPct.sort) { row in
                MacStatCell(value: row.twoPointPct)
            }
            .width(min: 54, ideal: 64, max: 88)
            TableColumn("3P%", value: \MacElStatRow.threePointPct.sort) { row in
                MacStatCell(value: row.threePointPct)
            }
            .width(min: 54, ideal: 64, max: 88)
            TableColumn("FT%", value: \MacElStatRow.freeThrowPct.sort) { row in
                MacStatCell(value: row.freeThrowPct)
            }
            .width(min: 54, ideal: 64, max: 88)
            TableColumn("PIR", value: \MacElStatRow.pir.sort) { row in
                MacStatCell(value: row.pir)
            }
            .width(min: 48, ideal: 58, max: 80)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacElStatRow.ID.self) { ids in
            menuItems(for: ids)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacElStatRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Show Details") {
                onShowDetails(id)
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacElStatRow.copyHeader, rows: [row.copyCells])
            }
        }
    }
}
#endif
