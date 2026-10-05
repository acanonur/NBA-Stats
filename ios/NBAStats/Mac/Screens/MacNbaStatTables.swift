#if os(macOS)
import SwiftUI

// The NBA's Season Stats rows and tables: one line per player per team, built from the rosters of
// the thirty teams (`GET /v1/teams/{id}?rosterMetrics=...`, the existing route), and the two column
// sets that show them. The EuroLeague's twins, and the pieces both share, are in
// MacStatTables.swift.
//
// Every number is a roster value the server sent, through `MacStatValue`; a missing value is an em
// dash that sorts below every real one.

// MARK: - NBA Season Stats: rows

/// The metric keys the NBA roster request asks for (`rosterMetrics`), all from the metrics catalogue.
enum NbaRosterKeys {
    static let seasonStats = "gp,min,pts,reb,ast,stl,blk,tov,fg_pct,fg3_pct,ft_pct,ts_pct,usg_pct,net_rtg"
    static let teamView = "gp,min,pts,reb,ast,ts_pct,usg_pct,net_rtg"
}

/// One roster line of the NBA table, ready to draw and to sort.
struct MacNbaStatRow: Identifiable, Hashable {
    let id: String
    let order: Int
    let playerRef: PlayerRef
    let player: String
    let position: String
    let teamId: Int
    let team: String
    let teamName: String
    let games: MacStatValue
    let minutes: MacStatValue
    let points: MacStatValue
    let rebounds: MacStatValue
    let assists: MacStatValue
    let steals: MacStatValue
    let blocks: MacStatValue
    let turnovers: MacStatValue
    let fieldGoalPct: MacStatValue
    let threePointPct: MacStatValue
    let freeThrowPct: MacStatValue
    let trueShootingPct: MacStatValue
    let usagePct: MacStatValue
    let netRating: MacStatValue

    static let copyHeader: [String] = [
        "Player", "Team", "GP", "MIN", "PTS", "REB", "AST", "STL", "BLK", "TOV",
        "FG%", "3P%", "FT%", "TS%", "USG%", "Net rtg"
    ]

    var copyCells: [String] {
        [player, team, games.text, minutes.text, points.text, rebounds.text, assists.text,
         steals.text, blocks.text, turnovers.text, fieldGoalPct.text, threePointPct.text,
         freeThrowPct.text, trueShootingPct.text, usagePct.text, netRating.text]
    }
}

enum MacNbaStatRows {

    /// The union of the rosters of the teams loaded so far. A row is one player on one team: a
    /// player who was traded has a row for each, and the same line twice (which the bundled demo
    /// answer produces for every team) is kept once.
    static func rows(from teams: [TeamDetailResponse]) -> [MacNbaStatRow] {
        var result: [MacNbaStatRow] = []
        var seen = Set<String>()
        var index = 0
        for detail in teams {
            for entry in detail.roster {
                let key = String(entry.player.playerId) + "-" + String(detail.team.teamId)
                if seen.contains(key) { continue }
                seen.insert(key)
                result.append(make(entry, team: detail.team, id: key, index: index))
                index += 1
            }
        }
        return result
    }

    private static func make(_ entry: TeamRosterEntry, team: TeamRef, id: String, index: Int) -> MacNbaStatRow {
        MacNbaStatRow(
            id: id,
            order: index,
            playerRef: entry.player,
            player: entry.player.name,
            position: entry.player.position ?? "",
            teamId: team.teamId,
            team: team.abbr,
            teamName: team.name,
            games: MacStatValue(number: entry.value("gp"), places: 0),
            minutes: MacStatValue(number: entry.value("min"), places: 1),
            points: MacStatValue(number: entry.value("pts"), places: 1),
            rebounds: MacStatValue(number: entry.value("reb"), places: 1),
            assists: MacStatValue(number: entry.value("ast"), places: 1),
            steals: MacStatValue(number: entry.value("stl"), places: 1),
            blocks: MacStatValue(number: entry.value("blk"), places: 1),
            turnovers: MacStatValue(number: entry.value("tov"), places: 1),
            fieldGoalPct: MacStatValue(fraction: entry.value("fg_pct")),
            threePointPct: MacStatValue(fraction: entry.value("fg3_pct")),
            freeThrowPct: MacStatValue(fraction: entry.value("ft_pct")),
            trueShootingPct: MacStatValue(fraction: entry.value("ts_pct")),
            usagePct: MacStatValue(fraction: entry.value("usg_pct")),
            netRating: MacStatValue(signed: entry.value("net_rtg"))
        )
    }

    /// The rows left after the team and name filters. `teamId` nil keeps every team.
    static func filtered(_ rows: [MacNbaStatRow], teamId: Int?, name: String) -> [MacNbaStatRow] {
        let needle = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return rows.filter { row in
            if let wanted = teamId, row.teamId != wanted { return false }
            if needle.isEmpty { return true }
            return row.player.localizedCaseInsensitiveContains(needle)
                || row.teamName.localizedCaseInsensitiveContains(needle)
                || row.team.localizedCaseInsensitiveContains(needle)
        }
    }
}

// MARK: - NBA Season Stats: tables

/// Box: Player, Team, GP and the counting stats (10 columns).
struct MacNbaStatBoxTable: View {

    private let rows: [MacNbaStatRow]
    @Binding private var selection: MacNbaStatRow.ID?
    private let onShowDetails: (MacNbaStatRow.ID) -> Void
    @Binding private var sortOrder: [KeyPathComparator<MacNbaStatRow>]

    init(rows: [MacNbaStatRow],
         selection: Binding<MacNbaStatRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacNbaStatRow>]>,
         onShowDetails: @escaping (MacNbaStatRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onShowDetails = onShowDetails
    }

    private var sortedRows: [MacNbaStatRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Player", value: \MacNbaStatRow.player) { row in
                TextCell(text: row.player)
            }
            .width(min: 140, ideal: 190)
            TableColumn("Team", value: \MacNbaStatRow.team) { row in
                TeamCell(abbr: row.team, name: row.teamName)
            }
            .width(min: 64, ideal: 78, max: 110)
            TableColumn("GP", value: \MacNbaStatRow.games.sort) { row in
                MacStatCell(value: row.games)
            }
            .width(min: 36, ideal: 44, max: 60)
            TableColumn("MIN", value: \MacNbaStatRow.minutes.sort) { row in
                MacStatCell(value: row.minutes)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("PTS", value: \MacNbaStatRow.points.sort) { row in
                MacStatCell(value: row.points)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("REB", value: \MacNbaStatRow.rebounds.sort) { row in
                MacStatCell(value: row.rebounds)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("AST", value: \MacNbaStatRow.assists.sort) { row in
                MacStatCell(value: row.assists)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("STL", value: \MacNbaStatRow.steals.sort) { row in
                MacStatCell(value: row.steals)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("BLK", value: \MacNbaStatRow.blocks.sort) { row in
                MacStatCell(value: row.blocks)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("TOV", value: \MacNbaStatRow.turnovers.sort) { row in
                MacStatCell(value: row.turnovers)
            }
            .width(min: 44, ideal: 54, max: 76)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacNbaStatRow.ID.self) { ids in
            menuItems(for: ids)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacNbaStatRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Show Details") {
                onShowDetails(id)
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacNbaStatRow.copyHeader, rows: [row.copyCells])
            }
        }
    }
}

/// Shooting: Player, Team, GP, the three percentages, TS%, USG% and net rating (9 columns).
struct MacNbaStatShootingTable: View {

    private let rows: [MacNbaStatRow]
    @Binding private var selection: MacNbaStatRow.ID?
    private let onShowDetails: (MacNbaStatRow.ID) -> Void
    @Binding private var sortOrder: [KeyPathComparator<MacNbaStatRow>]

    init(rows: [MacNbaStatRow],
         selection: Binding<MacNbaStatRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacNbaStatRow>]>,
         onShowDetails: @escaping (MacNbaStatRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onShowDetails = onShowDetails
    }

    private var sortedRows: [MacNbaStatRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Player", value: \MacNbaStatRow.player) { row in
                TextCell(text: row.player)
            }
            .width(min: 140, ideal: 190)
            TableColumn("Team", value: \MacNbaStatRow.team) { row in
                TeamCell(abbr: row.team, name: row.teamName)
            }
            .width(min: 64, ideal: 78, max: 110)
            TableColumn("GP", value: \MacNbaStatRow.games.sort) { row in
                MacStatCell(value: row.games)
            }
            .width(min: 36, ideal: 44, max: 60)
            TableColumn("FG%", value: \MacNbaStatRow.fieldGoalPct.sort) { row in
                MacStatCell(value: row.fieldGoalPct)
            }
            .width(min: 54, ideal: 64, max: 88)
            TableColumn("3P%", value: \MacNbaStatRow.threePointPct.sort) { row in
                MacStatCell(value: row.threePointPct)
            }
            .width(min: 54, ideal: 64, max: 88)
            TableColumn("FT%", value: \MacNbaStatRow.freeThrowPct.sort) { row in
                MacStatCell(value: row.freeThrowPct)
            }
            .width(min: 54, ideal: 64, max: 88)
            TableColumn("TS%", value: \MacNbaStatRow.trueShootingPct.sort) { row in
                MacStatCell(value: row.trueShootingPct)
            }
            .width(min: 54, ideal: 64, max: 88)
            TableColumn("USG%", value: \MacNbaStatRow.usagePct.sort) { row in
                MacStatCell(value: row.usagePct)
            }
            .width(min: 54, ideal: 64, max: 88)
            TableColumn("Net rtg", value: \MacNbaStatRow.netRating.sort) { row in
                MacStatCell(value: row.netRating)
            }
            .width(min: 56, ideal: 68, max: 92)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacNbaStatRow.ID.self) { ids in
            menuItems(for: ids)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacNbaStatRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Show Details") {
                onShowDetails(id)
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacNbaStatRow.copyHeader, rows: [row.copyCells])
            }
        }
    }
}

// MARK: - Loading the thirty rosters

/// What one team's roster request came to: its detail, or why it did not.
struct MacRosterOutcome: Sendable {
    let teamId: Int
    let detail: TeamDetailResponse?
    let failure: APIError?
}

/// Fetches some teams' rosters at once. The screen calls this four teams at a time, so at most four
/// requests are ever in flight: thirty at once would be a burst the server's rate limit may refuse.
///
/// A task group is the one structured-concurrency construct the app uses for this; the results come
/// back in the order the requests finished, so they are sorted by team id to keep the table's
/// starting order the same from one load to the next.
enum MacRosterFetch {

    static func fetch(_ teamIds: [Int], client: LeagueClient, keys: String) async -> [MacRosterOutcome] {
        let collected: [MacRosterOutcome] = await withTaskGroup(of: MacRosterOutcome.self,
                                                                returning: [MacRosterOutcome].self) { group in
            for teamId in teamIds {
                group.addTask {
                    await MacRosterFetch.one(teamId, client: client, keys: keys)
                }
            }
            var results: [MacRosterOutcome] = []
            for await outcome in group {
                results.append(outcome)
            }
            return results
        }
        return collected.sorted { $0.teamId < $1.teamId }
    }

    private static func one(_ teamId: Int, client: LeagueClient, keys: String) async -> MacRosterOutcome {
        do {
            let route: LeagueRoute = LeagueRoutes.nbaTeam(teamId, rosterMetrics: keys)
            let detail = try await client.get(TeamDetailResponse.self, route)
            return MacRosterOutcome(teamId: teamId, detail: detail, failure: nil)
        } catch {
            return MacRosterOutcome(teamId: teamId, detail: nil, failure: APIError.from(error))
        }
    }
}
#endif
