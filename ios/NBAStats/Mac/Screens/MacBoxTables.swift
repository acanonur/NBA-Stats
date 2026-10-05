#if os(macOS)
import SwiftUI

// The EuroLeague box score: one row per player of a team, two column sets (Box and Shooting), and
// the team's totals in a one-row grid beneath the table.
//
// WHAT A ROW SHOWS
// Every cell is a value the server recorded for that player in that game. A player who was listed
// and did not play (`participation == "dnp"`) shows "DNP" in the minutes column and an em dash in
// every other, because a dozen zeros for someone who never stepped on the floor would be a claim.
// A statistic the source did not record for a game (fouls drawn, blocks against and plus/minus are
// missing from the workbook's box scores) is an em dash, never 0. A shooting percentage is a
// fraction, and an em dash when there were no attempts.
//
// MINUTES
// The unit of the EuroLeague's minutes is unverified, so the header says MIN and the number is shown
// exactly as the server sent it, with one decimal.
//
// THE TEAM TOTALS ARE NOT A ROW
// Totals sit below the table in their own grid so a click on a header can never sort them into the
// middle of the players.

// MARK: - A player's name, with a mark for starters

struct MacBoxPlayerCell: View {

    private let name: String
    private let isStarter: Bool

    init(name: String, isStarter: Bool) {
        self.name = name
        self.isStarter = isStarter
    }

    var body: some View {
        HStack(spacing: Spacing.xs) {
            Text(name)
                .hardwoodText(.tableCell, monospacedDigits: false)
                .lineLimit(1)
                .truncationMode(.tail)
            if isStarter {
                Text("S")
                    .hardwoodText(.caption, color: Palette.textTertiary)
                    .help("Started the game")
            }
            Spacer(minLength: 0)
        }
        .help(name)
    }
}

// MARK: - Totals

/// One figure of the totals grid.
struct MacBoxTotal: Identifiable, Hashable {
    let label: String
    let value: String

    var id: String { label }
}

/// A single row of figures, each with its label above it. It is a plain row of small columns (the
/// totals are not a table and are never sorted), so a reader can find a figure under its label.
struct MacBoxTotalsGrid: View {

    private let title: String
    private let totals: [MacBoxTotal]

    init(title: String, totals: [MacBoxTotal]) {
        self.title = title
        self.totals = totals
    }

    var body: some View {
        if !totals.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text(title)
                    .hardwoodText(.statLabel)
                HStack(alignment: .top, spacing: Spacing.lg) {
                    ForEach(totals) { item in
                        VStack(alignment: .leading, spacing: Spacing.xxs) {
                            Text(item.label)
                                .hardwoodText(.tableHeader, color: Palette.textTertiary)
                            Text(item.value)
                                .hardwoodText(.tableCell)
                        }
                    }
                    Spacer(minLength: 0)
                }
            }
            .padding(.horizontal, Spacing.md)
            .padding(.vertical, Spacing.sm)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

// MARK: - Rows

struct MacBoxElRow: Identifiable, Hashable {
    let id: String
    let order: Int
    let player: String
    let isStarter: Bool
    let minutes: MacStatValue
    let points: MacStatValue
    let rebounds: MacStatValue
    let assists: MacStatValue
    let steals: MacStatValue
    let turnovers: MacStatValue
    let blocks: MacStatValue
    let fouls: MacStatValue
    let pir: MacStatValue
    let twoPointers: MacStatValue
    let twoPointPct: MacStatValue
    let threePointers: MacStatValue
    let threePointPct: MacStatValue
    let freeThrows: MacStatValue
    let freeThrowPct: MacStatValue
    let offensiveRebounds: MacStatValue
    let defensiveRebounds: MacStatValue

    static let copyHeader: [String] = [
        "Player", "MIN", "PTS", "REB", "AST", "STL", "TOV", "BLK", "PF", "PIR",
        "2P", "2P%", "3P", "3P%", "FT", "FT%", "OREB", "DREB"
    ]

    var copyCells: [String] {
        [player, minutes.text, points.text, rebounds.text, assists.text, steals.text, turnovers.text,
         blocks.text, fouls.text, pir.text, twoPointers.text, twoPointPct.text, threePointers.text,
         threePointPct.text, freeThrows.text, freeThrowPct.text, offensiveRebounds.text,
         defensiveRebounds.text]
    }
}

enum MacBoxElRows {

    /// One row per player, in the order the server sent them (starters first).
    static func rows(from team: ElBoxTeam?) -> [MacBoxElRow] {
        guard let players = team?.players else { return [] }
        var result: [MacBoxElRow] = []
        var index = 0
        for entry in players {
            result.append(make(entry, index: index))
            index += 1
        }
        return result
    }

    private static func make(_ entry: ElBoxPlayer, index: Int) -> MacBoxElRow {
        let stats: ElStatLine? = entry.stats
        let code: String = entry.player?.personCode ?? entry.player?.id ?? ("row-" + String(index))
        let didNotPlay: Bool = entry.participation == "dnp"
        let minutes: MacStatValue
        if didNotPlay {
            minutes = MacStatValue(words: "DNP", sort: -Double.infinity, help: "Did not play")
        } else {
            minutes = MacStatValue(number: stats?.minutes, places: 1)
        }
        return MacBoxElRow(
            id: code,
            order: index,
            player: entry.player?.name ?? Formatting.emDash,
            isStarter: entry.isStarter == true,
            minutes: minutes,
            points: MacStatValue(number: stats?.pts, places: 0),
            rebounds: MacStatValue(number: stats?.reb, places: 0),
            assists: MacStatValue(number: stats?.ast, places: 0),
            steals: MacStatValue(number: stats?.stl, places: 0),
            turnovers: MacStatValue(number: stats?.tov, places: 0),
            blocks: MacStatValue(number: stats?.blk, places: 0),
            fouls: MacStatValue(number: stats?.pf, places: 0),
            pir: MacStatValue(number: stats?.pir, places: 0),
            twoPointers: madeAttempted(stats?.fgm2, stats?.fga2),
            twoPointPct: MacStatValue(fraction: stats?.fg2Pct),
            threePointers: madeAttempted(stats?.fgm3, stats?.fga3),
            threePointPct: MacStatValue(fraction: stats?.fg3Pct),
            freeThrows: madeAttempted(stats?.ftm, stats?.fta),
            freeThrowPct: MacStatValue(fraction: stats?.ftPct),
            offensiveRebounds: MacStatValue(number: stats?.oreb, places: 0),
            defensiveRebounds: MacStatValue(number: stats?.dreb, places: 0)
        )
    }

    /// `5–9`, sorted by the makes.
    private static func madeAttempted(_ made: Double?, _ attempted: Double?) -> MacStatValue {
        MacStatValue(words: LeagueFormatting.madeAttempted(made, attempted),
                     sort: LeagueFormatting.sortKey(made))
    }

    /// The team's totals line as a grid, for the column set on screen. Nothing is added up here:
    /// these are the totals the server sent.
    static func totals(from team: ElBoxTeam?, set: MacStatColumnSet) -> [MacBoxTotal] {
        guard let line = team?.totals else { return [] }
        switch set {
        case .box:
            return [
                MacBoxTotal(label: "PTS", value: LeagueFormatting.number(line.pts, places: 0)),
                MacBoxTotal(label: "REB", value: LeagueFormatting.number(line.reb, places: 0)),
                MacBoxTotal(label: "AST", value: LeagueFormatting.number(line.ast, places: 0)),
                MacBoxTotal(label: "STL", value: LeagueFormatting.number(line.stl, places: 0)),
                MacBoxTotal(label: "TOV", value: LeagueFormatting.number(line.tov, places: 0)),
                MacBoxTotal(label: "BLK", value: LeagueFormatting.number(line.blk, places: 0)),
                MacBoxTotal(label: "PF", value: LeagueFormatting.number(line.pf, places: 0)),
                MacBoxTotal(label: "PIR", value: LeagueFormatting.number(line.pir, places: 0))
            ]
        case .shooting:
            return [
                MacBoxTotal(label: "2P", value: LeagueFormatting.madeAttempted(line.fgm2, line.fga2)),
                MacBoxTotal(label: "2P%", value: LeagueFormatting.percent(line.fg2Pct)),
                MacBoxTotal(label: "3P", value: LeagueFormatting.madeAttempted(line.fgm3, line.fga3)),
                MacBoxTotal(label: "3P%", value: LeagueFormatting.percent(line.fg3Pct)),
                MacBoxTotal(label: "FT", value: LeagueFormatting.madeAttempted(line.ftm, line.fta)),
                MacBoxTotal(label: "FT%", value: LeagueFormatting.percent(line.ftPct)),
                MacBoxTotal(label: "OREB", value: LeagueFormatting.number(line.oreb, places: 0)),
                MacBoxTotal(label: "DREB", value: LeagueFormatting.number(line.dreb, places: 0))
            ]
        }
    }
}

// MARK: - Tables

/// Box: Player, MIN, PTS, REB, AST, STL, TOV, BLK, PF, PIR (10 columns).
struct MacBoxElBoxTable: View {

    private let rows: [MacBoxElRow]
    @Binding private var selection: MacBoxElRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacBoxElRow>]

    init(rows: [MacBoxElRow],
         selection: Binding<MacBoxElRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacBoxElRow>]>) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
    }

    private var sortedRows: [MacBoxElRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Player", value: \MacBoxElRow.player) { row in
                MacBoxPlayerCell(name: row.player, isStarter: row.isStarter)
            }
            .width(min: 140, ideal: 190)
            TableColumn("MIN", value: \MacBoxElRow.minutes.sort) { row in
                MacStatCell(value: row.minutes)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("PTS", value: \MacBoxElRow.points.sort) { row in
                MacStatCell(value: row.points)
            }
            .width(min: 40, ideal: 48, max: 70)
            TableColumn("REB", value: \MacBoxElRow.rebounds.sort) { row in
                MacStatCell(value: row.rebounds)
            }
            .width(min: 40, ideal: 48, max: 70)
            TableColumn("AST", value: \MacBoxElRow.assists.sort) { row in
                MacStatCell(value: row.assists)
            }
            .width(min: 40, ideal: 48, max: 70)
            TableColumn("STL", value: \MacBoxElRow.steals.sort) { row in
                MacStatCell(value: row.steals)
            }
            .width(min: 40, ideal: 48, max: 70)
            TableColumn("TOV", value: \MacBoxElRow.turnovers.sort) { row in
                MacStatCell(value: row.turnovers)
            }
            .width(min: 40, ideal: 48, max: 70)
            TableColumn("BLK", value: \MacBoxElRow.blocks.sort) { row in
                MacStatCell(value: row.blocks)
            }
            .width(min: 40, ideal: 48, max: 70)
            TableColumn("PF", value: \MacBoxElRow.fouls.sort) { row in
                MacStatCell(value: row.fouls)
            }
            .width(min: 36, ideal: 44, max: 64)
            TableColumn("PIR", value: \MacBoxElRow.pir.sort) { row in
                MacStatCell(value: row.pir)
            }
            .width(min: 40, ideal: 48, max: 70)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
    }
}

/// Shooting: Player, MIN, 2P, 2P%, 3P, 3P%, FT, FT%, OREB, DREB (10 columns).
struct MacBoxElShootingTable: View {

    private let rows: [MacBoxElRow]
    @Binding private var selection: MacBoxElRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacBoxElRow>]

    init(rows: [MacBoxElRow],
         selection: Binding<MacBoxElRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacBoxElRow>]>) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
    }

    private var sortedRows: [MacBoxElRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Player", value: \MacBoxElRow.player) { row in
                MacBoxPlayerCell(name: row.player, isStarter: row.isStarter)
            }
            .width(min: 140, ideal: 190)
            TableColumn("MIN", value: \MacBoxElRow.minutes.sort) { row in
                MacStatCell(value: row.minutes)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("2P", value: \MacBoxElRow.twoPointers.sort) { row in
                MacStatCell(value: row.twoPointers)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("2P%", value: \MacBoxElRow.twoPointPct.sort) { row in
                MacStatCell(value: row.twoPointPct)
            }
            .width(min: 52, ideal: 62, max: 84)
            TableColumn("3P", value: \MacBoxElRow.threePointers.sort) { row in
                MacStatCell(value: row.threePointers)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("3P%", value: \MacBoxElRow.threePointPct.sort) { row in
                MacStatCell(value: row.threePointPct)
            }
            .width(min: 52, ideal: 62, max: 84)
            TableColumn("FT", value: \MacBoxElRow.freeThrows.sort) { row in
                MacStatCell(value: row.freeThrows)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("FT%", value: \MacBoxElRow.freeThrowPct.sort) { row in
                MacStatCell(value: row.freeThrowPct)
            }
            .width(min: 52, ideal: 62, max: 84)
            TableColumn("OREB", value: \MacBoxElRow.offensiveRebounds.sort) { row in
                MacStatCell(value: row.offensiveRebounds)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("DREB", value: \MacBoxElRow.defensiveRebounds.sort) { row in
                MacStatCell(value: row.defensiveRebounds)
            }
            .width(min: 44, ideal: 54, max: 76)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
    }
}
#endif
