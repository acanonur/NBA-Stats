#if os(macOS)
import SwiftUI

// The NBA box score: one row per player of a team, two column sets (Box and Shooting), and the
// team's totals beneath. The EuroLeague's twin is MacBoxTables.swift.
//
// WHAT THE NBA BOX SERVES
// The keys are exactly those of `contracts/fixtures/game_box.json`: `min`, `pts`, `reb`, `oreb`,
// `dreb`, `ast`, `stl`, `blk`, `tov`, `pf`, `fgm`, `fga`, `fg3m`, `fg3a`, `ftm`, `fta`, `plus_minus`,
// `ts_pct`, `efg_pct`, `usg_pct`, `game_score` for a player, and the same plus the team ratings for
// a team. A player's line carries no field-goal, three-point or free-throw FRACTION, so none is
// shown and none is worked out from the makes and attempts: the shooting columns are the makes and
// attempts ("6–11") and the three rates the server did send (true shooting, effective field goal
// and usage).
//
// A value the box does not have for a player is an em dash. A player with no minutes is shown with
// the em dashes the server's nulls become; the app never turns a null into 0.

// MARK: - Rows

struct MacBoxNbaRow: Identifiable, Hashable {
    let id: String
    let order: Int
    let player: String
    let isStarter: Bool
    let minutes: MacStatValue
    let points: MacStatValue
    let rebounds: MacStatValue
    let assists: MacStatValue
    let steals: MacStatValue
    let blocks: MacStatValue
    let turnovers: MacStatValue
    let plusMinus: MacStatValue
    let gameScore: MacStatValue
    let fieldGoals: MacStatValue
    let threePointers: MacStatValue
    let freeThrows: MacStatValue
    let trueShootingPct: MacStatValue
    let effectiveFieldGoalPct: MacStatValue
    let usagePct: MacStatValue
    let offensiveRebounds: MacStatValue
    let defensiveRebounds: MacStatValue

    static let copyHeader: [String] = [
        "Player", "MIN", "PTS", "REB", "AST", "STL", "BLK", "TOV", "+/-", "Game score",
        "FG", "3P", "FT", "TS%", "eFG%", "USG%", "OREB", "DREB"
    ]

    var copyCells: [String] {
        [player, minutes.text, points.text, rebounds.text, assists.text, steals.text, blocks.text,
         turnovers.text, plusMinus.text, gameScore.text, fieldGoals.text, threePointers.text,
         freeThrows.text, trueShootingPct.text, effectiveFieldGoalPct.text, usagePct.text,
         offensiveRebounds.text, defensiveRebounds.text]
    }
}

enum MacBoxNbaRows {

    /// One row per player, in the order the server sent them.
    static func rows(from team: BoxScoreTeam?) -> [MacBoxNbaRow] {
        guard let players = team?.players else { return [] }
        var result: [MacBoxNbaRow] = []
        var index = 0
        for entry in players {
            result.append(make(entry, index: index))
            index += 1
        }
        return result
    }

    private static func make(_ entry: BoxScorePlayer, index: Int) -> MacBoxNbaRow {
        MacBoxNbaRow(
            id: String(entry.player.playerId),
            order: index,
            player: entry.player.name,
            isStarter: entry.started == true,
            minutes: MacStatValue(number: entry.minutes ?? entry.value("min"), places: 1),
            points: MacStatValue(number: entry.value("pts"), places: 0),
            rebounds: MacStatValue(number: entry.value("reb"), places: 0),
            assists: MacStatValue(number: entry.value("ast"), places: 0),
            steals: MacStatValue(number: entry.value("stl"), places: 0),
            blocks: MacStatValue(number: entry.value("blk"), places: 0),
            turnovers: MacStatValue(number: entry.value("tov"), places: 0),
            plusMinus: MacStatValue(signed: entry.value("plus_minus"), places: 0),
            gameScore: MacStatValue(number: entry.value("game_score"), places: 1),
            fieldGoals: madeAttempted(entry.value("fgm"), entry.value("fga")),
            threePointers: madeAttempted(entry.value("fg3m"), entry.value("fg3a")),
            freeThrows: madeAttempted(entry.value("ftm"), entry.value("fta")),
            trueShootingPct: MacStatValue(fraction: entry.value("ts_pct")),
            effectiveFieldGoalPct: MacStatValue(fraction: entry.value("efg_pct")),
            usagePct: MacStatValue(fraction: entry.value("usg_pct")),
            offensiveRebounds: MacStatValue(number: entry.value("oreb"), places: 0),
            defensiveRebounds: MacStatValue(number: entry.value("dreb"), places: 0)
        )
    }

    /// `6–11`, sorted by the makes.
    private static func madeAttempted(_ made: Double?, _ attempted: Double?) -> MacStatValue {
        MacStatValue(words: LeagueFormatting.madeAttempted(made, attempted),
                     sort: LeagueFormatting.sortKey(made))
    }

    /// The team's totals as a grid, for the column set on screen: the values the server sent.
    static func totals(from team: BoxScoreTeam?, set: MacStatColumnSet) -> [MacBoxTotal] {
        guard let team = team else { return [] }
        switch set {
        case .box:
            return [
                MacBoxTotal(label: "PTS", value: LeagueFormatting.number(team.value("pts"), places: 0)),
                MacBoxTotal(label: "REB", value: LeagueFormatting.number(team.value("reb"), places: 0)),
                MacBoxTotal(label: "AST", value: LeagueFormatting.number(team.value("ast"), places: 0)),
                MacBoxTotal(label: "STL", value: LeagueFormatting.number(team.value("stl"), places: 0)),
                MacBoxTotal(label: "BLK", value: LeagueFormatting.number(team.value("blk"), places: 0)),
                MacBoxTotal(label: "TOV", value: LeagueFormatting.number(team.value("tov"), places: 0)),
                MacBoxTotal(label: "PF", value: LeagueFormatting.number(team.value("pf"), places: 0))
            ]
        case .shooting:
            return [
                MacBoxTotal(label: "FG", value: LeagueFormatting.madeAttempted(team.value("fgm"), team.value("fga"))),
                MacBoxTotal(label: "FG%", value: LeagueFormatting.percent(team.value("fg_pct"))),
                MacBoxTotal(label: "3P", value: LeagueFormatting.madeAttempted(team.value("fg3m"), team.value("fg3a"))),
                MacBoxTotal(label: "3P%", value: LeagueFormatting.percent(team.value("fg3_pct"))),
                MacBoxTotal(label: "FT", value: LeagueFormatting.madeAttempted(team.value("ftm"), team.value("fta"))),
                MacBoxTotal(label: "FT%", value: LeagueFormatting.percent(team.value("ft_pct"))),
                MacBoxTotal(label: "OREB", value: LeagueFormatting.number(team.value("oreb"), places: 0)),
                MacBoxTotal(label: "DREB", value: LeagueFormatting.number(team.value("dreb"), places: 0))
            ]
        }
    }
}

// MARK: - Tables

/// Box: Player, MIN, PTS, REB, AST, STL, BLK, TOV, +/-, Game score (10 columns).
struct MacBoxNbaBoxTable: View {

    private let rows: [MacBoxNbaRow]
    @Binding private var selection: MacBoxNbaRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacBoxNbaRow>]

    init(rows: [MacBoxNbaRow],
         selection: Binding<MacBoxNbaRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacBoxNbaRow>]>) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
    }

    private var sortedRows: [MacBoxNbaRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Player", value: \MacBoxNbaRow.player) { row in
                MacBoxPlayerCell(name: row.player, isStarter: row.isStarter)
            }
            .width(min: 140, ideal: 190)
            TableColumn("MIN", value: \MacBoxNbaRow.minutes.sort) { row in
                MacStatCell(value: row.minutes)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("PTS", value: \MacBoxNbaRow.points.sort) { row in
                MacStatCell(value: row.points)
            }
            .width(min: 40, ideal: 48, max: 70)
            TableColumn("REB", value: \MacBoxNbaRow.rebounds.sort) { row in
                MacStatCell(value: row.rebounds)
            }
            .width(min: 40, ideal: 48, max: 70)
            TableColumn("AST", value: \MacBoxNbaRow.assists.sort) { row in
                MacStatCell(value: row.assists)
            }
            .width(min: 40, ideal: 48, max: 70)
            TableColumn("STL", value: \MacBoxNbaRow.steals.sort) { row in
                MacStatCell(value: row.steals)
            }
            .width(min: 40, ideal: 48, max: 70)
            TableColumn("BLK", value: \MacBoxNbaRow.blocks.sort) { row in
                MacStatCell(value: row.blocks)
            }
            .width(min: 40, ideal: 48, max: 70)
            TableColumn("TOV", value: \MacBoxNbaRow.turnovers.sort) { row in
                MacStatCell(value: row.turnovers)
            }
            .width(min: 40, ideal: 48, max: 70)
            TableColumn("+/-", value: \MacBoxNbaRow.plusMinus.sort) { row in
                MacStatCell(value: row.plusMinus)
            }
            .width(min: 40, ideal: 48, max: 70)
            TableColumn("Game score", value: \MacBoxNbaRow.gameScore.sort) { row in
                MacStatCell(value: row.gameScore)
            }
            .width(min: 76, ideal: 88, max: 110)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
    }
}

/// Shooting: Player, MIN, FG, 3P, FT, TS%, eFG%, USG%, OREB, DREB (10 columns).
struct MacBoxNbaShootingTable: View {

    private let rows: [MacBoxNbaRow]
    @Binding private var selection: MacBoxNbaRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacBoxNbaRow>]

    init(rows: [MacBoxNbaRow],
         selection: Binding<MacBoxNbaRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacBoxNbaRow>]>) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
    }

    private var sortedRows: [MacBoxNbaRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Player", value: \MacBoxNbaRow.player) { row in
                MacBoxPlayerCell(name: row.player, isStarter: row.isStarter)
            }
            .width(min: 140, ideal: 190)
            TableColumn("MIN", value: \MacBoxNbaRow.minutes.sort) { row in
                MacStatCell(value: row.minutes)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("FG", value: \MacBoxNbaRow.fieldGoals.sort) { row in
                MacStatCell(value: row.fieldGoals)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("3P", value: \MacBoxNbaRow.threePointers.sort) { row in
                MacStatCell(value: row.threePointers)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("FT", value: \MacBoxNbaRow.freeThrows.sort) { row in
                MacStatCell(value: row.freeThrows)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("TS%", value: \MacBoxNbaRow.trueShootingPct.sort) { row in
                MacStatCell(value: row.trueShootingPct)
            }
            .width(min: 52, ideal: 62, max: 84)
            TableColumn("eFG%", value: \MacBoxNbaRow.effectiveFieldGoalPct.sort) { row in
                MacStatCell(value: row.effectiveFieldGoalPct)
            }
            .width(min: 56, ideal: 66, max: 88)
            TableColumn("USG%", value: \MacBoxNbaRow.usagePct.sort) { row in
                MacStatCell(value: row.usagePct)
            }
            .width(min: 52, ideal: 62, max: 84)
            TableColumn("OREB", value: \MacBoxNbaRow.offensiveRebounds.sort) { row in
                MacStatCell(value: row.offensiveRebounds)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("DREB", value: \MacBoxNbaRow.defensiveRebounds.sort) { row in
                MacStatCell(value: row.defensiveRebounds)
            }
            .width(min: 44, ideal: 54, max: 76)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
    }
}
#endif
