#if os(macOS)
import SwiftUI

// The NBA roster of Team View: one row per player of a team, from `GET /v1/teams/{id}` with the
// roster's metrics (the existing route). The EuroLeague's squad tables are MacSquadTables.swift.
//
// Every number is a roster value the server sent, per game, through `MacStatValue`; a missing value
// is an em dash that sorts below every real one. The roster arrives most minutes first, and the table
// starts in that order. The position is the one the roster lists, or an em dash.

// MARK: - NBA roster

struct MacRosterRow: Identifiable, Hashable {
    let id: String
    let order: Int
    let playerRef: PlayerRef
    let player: String
    let position: String
    let games: MacStatValue
    let minutes: MacStatValue
    let points: MacStatValue
    let rebounds: MacStatValue
    let assists: MacStatValue
    let trueShootingPct: MacStatValue
    let usagePct: MacStatValue
    let netRating: MacStatValue

    static let copyHeader: [String] = [
        "Player", "Pos", "GP", "MIN", "PTS", "REB", "AST", "TS%", "USG%", "Net rtg"
    ]

    /// The position as the table shows it: an em dash when the roster does not list one.
    var positionText: String {
        position.isEmpty ? Formatting.emDash : position
    }

    var copyCells: [String] {
        [player, positionText, games.text, minutes.text, points.text,
         rebounds.text, assists.text, trueShootingPct.text, usagePct.text, netRating.text]
    }
}

enum MacRosterRows {

    /// One row per player of the roster, in the order the server sent them (most minutes first).
    static func rows(from detail: TeamDetailResponse?) -> [MacRosterRow] {
        guard let roster = detail?.roster else { return [] }
        var result: [MacRosterRow] = []
        var index = 0
        for entry in roster {
            result.append(make(entry, index: index))
            index += 1
        }
        return result
    }

    private static func make(_ entry: TeamRosterEntry, index: Int) -> MacRosterRow {
        MacRosterRow(
            id: String(entry.player.playerId),
            order: index,
            playerRef: entry.player,
            player: entry.player.name,
            position: entry.player.position ?? "",
            games: MacStatValue(number: entry.value("gp"), places: 0),
            minutes: MacStatValue(number: entry.value("min"), places: 1),
            points: MacStatValue(number: entry.value("pts"), places: 1),
            rebounds: MacStatValue(number: entry.value("reb"), places: 1),
            assists: MacStatValue(number: entry.value("ast"), places: 1),
            trueShootingPct: MacStatValue(fraction: entry.value("ts_pct")),
            usagePct: MacStatValue(fraction: entry.value("usg_pct")),
            netRating: MacStatValue(signed: entry.value("net_rtg"))
        )
    }
}

/// The NBA roster: Player, Pos, GP, MIN, PTS, REB, AST, TS%, USG%, Net rtg (10 columns).
struct MacRosterTable: View {

    private let rows: [MacRosterRow]
    @Binding private var selection: MacRosterRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacRosterRow>]
    private let onShowDetails: (MacRosterRow.ID) -> Void

    init(rows: [MacRosterRow],
         selection: Binding<MacRosterRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacRosterRow>]>,
         onShowDetails: @escaping (MacRosterRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onShowDetails = onShowDetails
    }

    private var sortedRows: [MacRosterRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Player", value: \MacRosterRow.player) { row in
                TextCell(text: row.player)
            }
            .width(min: 130, ideal: 170)
            TableColumn("Pos", value: \MacRosterRow.position) { row in
                TextCell(text: row.positionText)
            }
            .width(min: 36, ideal: 44, max: 60)
            TableColumn("GP", value: \MacRosterRow.games.sort) { row in
                MacStatCell(value: row.games)
            }
            .width(min: 36, ideal: 44, max: 60)
            TableColumn("MIN", value: \MacRosterRow.minutes.sort) { row in
                MacStatCell(value: row.minutes)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("PTS", value: \MacRosterRow.points.sort) { row in
                MacStatCell(value: row.points)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("REB", value: \MacRosterRow.rebounds.sort) { row in
                MacStatCell(value: row.rebounds)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("AST", value: \MacRosterRow.assists.sort) { row in
                MacStatCell(value: row.assists)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("TS%", value: \MacRosterRow.trueShootingPct.sort) { row in
                MacStatCell(value: row.trueShootingPct)
            }
            .width(min: 52, ideal: 62, max: 84)
            TableColumn("USG%", value: \MacRosterRow.usagePct.sort) { row in
                MacStatCell(value: row.usagePct)
            }
            .width(min: 52, ideal: 62, max: 84)
            TableColumn("Net rtg", value: \MacRosterRow.netRating.sort) { row in
                MacStatCell(value: row.netRating)
            }
            .width(min: 56, ideal: 68, max: 92)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacRosterRow.ID.self) { ids in
            menuItems(for: ids)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacRosterRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Show Details") {
                onShowDetails(id)
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacRosterRow.copyHeader, rows: [row.copyCells])
            }
        }
    }
}
#endif
