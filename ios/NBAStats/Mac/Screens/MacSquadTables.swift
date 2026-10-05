#if os(macOS)
import SwiftUI

// The squad tables of Team View: the EuroLeague's squad in four column sets (the workbook's Squads
// sheet) and the NBA's roster in one.
//
// WHOSE NUMBERS ARE THESE
// A squad member's per-40 rates, projected minutes and projected per-game line are the model's, and
// the model's inputs differ in quality. The Basis column says which: an official figure, an official
// update, a figure the workbook author estimated (source not recorded), a position average, or an
// invented demo. An estimate is drawn as an estimate, never as an official number.
//
// STATUS
// The workbook marked every player it did not list AVAILABLE. Hardwood shows a status only when a
// source reported one, so a player nobody has reported on shows an em dash, and the screen says
// so. A status that no longer drives the projection is labelled "superseded", an old one "stale".
//
// EVERY COLUMN IS A SERVED VALUE
// The Squad set shows age, role, basis, projected minutes and three per-40 rates; Season shows the
// official season averages; Per 40 the full set of rates; Projected the squad's projected per-game
// line (its PIR is nil by design: a projected PIR needs inputs the rates do not carry). Percentages
// are fractions the server sent. Nothing is added up here.

// MARK: - Column sets

/// The EuroLeague squad's four column sets.
enum MacSquadColumnSet: String, CaseIterable, Identifiable, Hashable {
    case squad
    case season
    case per40
    case projected

    var id: String { rawValue }

    var title: String {
        switch self {
        case .squad:
            return "Squad"
        case .season:
            return "Season"
        case .per40:
            return "Per 40"
        case .projected:
            return "Projected"
        }
    }
}

// MARK: - Basis chip

/// Whose numbers a squad member's rates are, as a chip. Estimates are drawn in the warning colour.
struct MacBasisCell: View {

    private let basis: String
    private let label: String

    init(basis: String, label: String) {
        self.basis = basis
        self.label = label
    }

    private var tint: Color {
        switch basis {
        case "workbookEstimate", "positionPrior":
            return Palette.warning
        case "syntheticDemo":
            return Palette.neutral
        default:
            return Palette.textSecondary
        }
    }

    var body: some View {
        if basis.isEmpty {
            Text(Formatting.emDash)
                .hardwoodText(.tableCell, color: Palette.textTertiary)
        } else {
            HStack(spacing: 0) {
                LeagueChip(text: label, tint: tint)
                Spacer(minLength: 0)
            }
        }
    }
}

// MARK: - EuroLeague rows

struct MacSquadRow: Identifiable, Hashable {
    let id: String
    let order: Int
    let player: String
    let position: String
    let age: MacStatValue
    let role: String
    let basis: String
    let basisLabel: String
    let basisSort: Int
    let status: String
    let statusLabel: String
    let statusSort: Int
    let projectedMinutes: MacStatValue
    let per40Points: MacStatValue
    let per40Rebounds: MacStatValue
    let per40Assists: MacStatValue
    let per40Threes: MacStatValue
    let per40Steals: MacStatValue
    let per40Blocks: MacStatValue
    let per40Turnovers: MacStatValue
    let seasonGames: MacStatValue
    let seasonMinutes: MacStatValue
    let seasonPoints: MacStatValue
    let seasonRebounds: MacStatValue
    let seasonAssists: MacStatValue
    let seasonPir: MacStatValue
    let seasonTwoPct: MacStatValue
    let seasonThreePct: MacStatValue
    let seasonFreeThrowPct: MacStatValue
    let gameMinutes: MacStatValue
    let gamePoints: MacStatValue
    let gameRebounds: MacStatValue
    let gameAssists: MacStatValue
    let gameThrees: MacStatValue
    let gameSteals: MacStatValue
    let gameBlocks: MacStatValue
    let gameTurnovers: MacStatValue
    let gamePir: MacStatValue

    /// Every column of the four sets, once, for "Copy Table" and "Copy Row".
    static let copyHeader: [String] = [
        "Player", "Pos", "Age", "Role", "Basis", "Status", "Proj MIN",
        "PTS/40", "REB/40", "AST/40", "3PM/40", "STL/40", "BLK/40", "TOV/40",
        "GP", "MIN", "PTS", "REB", "AST", "PIR", "2P%", "3P%", "FT%",
        "Proj PTS", "Proj REB", "Proj AST", "Proj 3PM", "Proj STL", "Proj BLK", "Proj TOV"
    ]

    var copyCells: [String] {
        let statusText: String = status.isEmpty ? Formatting.emDash : statusLabel
        return [player, position, age.text, role, basisLabel, statusText, projectedMinutes.text,
                per40Points.text, per40Rebounds.text, per40Assists.text, per40Threes.text,
                per40Steals.text, per40Blocks.text, per40Turnovers.text,
                seasonGames.text, seasonMinutes.text, seasonPoints.text, seasonRebounds.text,
                seasonAssists.text, seasonPir.text, seasonTwoPct.text, seasonThreePct.text,
                seasonFreeThrowPct.text,
                gamePoints.text, gameRebounds.text, gameAssists.text, gameThrees.text,
                gameSteals.text, gameBlocks.text, gameTurnovers.text]
    }
}

enum MacSquadRows {

    /// One row per squad member, in the order the server sent them.
    static func rows(from club: ElClubView?) -> [MacSquadRow] {
        guard let squad = club?.squad else { return [] }
        var result: [MacSquadRow] = []
        var index = 0
        for member in squad {
            result.append(make(member, index: index))
            index += 1
        }
        return result
    }

    /// True when any member carries a projected per-game line, so the Projected set is worth offering.
    static func hasProjectedLine(_ club: ElClubView?) -> Bool {
        for member in club?.squad ?? [] {
            if member.perGame != nil {
                return true
            }
        }
        return false
    }

    /// The row id of a member: the player code, or its position in the squad.
    static func id(of member: ElSquadMember, index: Int) -> String {
        member.player?.personCode ?? member.player?.id ?? ("row-" + String(index))
    }

    private static func make(_ member: ElSquadMember, index: Int) -> MacSquadRow {
        let per40: ElPer40? = member.per40
        let season: ElSeasonAverages? = member.seasonAverages
        let game: ElPerGameProjection? = member.perGame
        return MacSquadRow(
            id: id(of: member, index: index),
            order: index,
            player: member.player?.name ?? Formatting.emDash,
            position: member.positionWorkbook5 ?? member.player?.position ?? "",
            age: MacStatValue(number: member.age, places: 0),
            role: member.role ?? "",
            basis: member.basis ?? "",
            basisLabel: LeagueFormatting.basisWord(member.basis),
            basisSort: basisOrder(member.basis),
            status: MacWorkbookStatus.key(member.status),
            statusLabel: statusLabel(member),
            statusSort: MacWorkbookStatus.order(member.status),
            projectedMinutes: MacStatValue(number: member.projectedMinutes, places: 1),
            per40Points: MacStatValue(number: per40?.pts, places: 1),
            per40Rebounds: MacStatValue(number: per40?.reb, places: 1),
            per40Assists: MacStatValue(number: per40?.ast, places: 1),
            per40Threes: MacStatValue(number: per40?.fg3m, places: 1),
            per40Steals: MacStatValue(number: per40?.stl, places: 1),
            per40Blocks: MacStatValue(number: per40?.blk, places: 1),
            per40Turnovers: MacStatValue(number: per40?.tov, places: 1),
            seasonGames: MacStatValue(whole: season?.games),
            seasonMinutes: MacStatValue(number: season?.min, places: 1),
            seasonPoints: MacStatValue(number: season?.pts, places: 1),
            seasonRebounds: MacStatValue(number: season?.reb, places: 1),
            seasonAssists: MacStatValue(number: season?.ast, places: 1),
            seasonPir: MacStatValue(number: season?.pir, places: 1),
            seasonTwoPct: MacStatValue(fraction: season?.fg2Pct),
            seasonThreePct: MacStatValue(fraction: season?.fg3Pct),
            seasonFreeThrowPct: MacStatValue(fraction: season?.ftPct),
            gameMinutes: MacStatValue(number: game?.min, places: 1),
            gamePoints: MacStatValue(number: game?.pts, places: 1),
            gameRebounds: MacStatValue(number: game?.reb, places: 1),
            gameAssists: MacStatValue(number: game?.ast, places: 1),
            gameThrees: MacStatValue(number: game?.fg3m, places: 1),
            gameSteals: MacStatValue(number: game?.stl, places: 1),
            gameBlocks: MacStatValue(number: game?.blk, places: 1),
            gameTurnovers: MacStatValue(number: game?.tov, places: 1),
            gamePir: MacStatValue(number: game?.pir, places: 1)
        )
    }

    /// `Doubtful`, `Doubtful (stale)` or `Doubtful (superseded)`.
    private static func statusLabel(_ member: ElSquadMember) -> String {
        let word: String = LeagueFormatting.statusWord(member.status)
        if member.inForce == false {
            return word + " (superseded)"
        }
        if member.isStale == true {
            return word + " (stale)"
        }
        return word
    }

    /// Official figures first, then updates, then estimates, then position averages, then demo.
    private static func basisOrder(_ basis: String?) -> Int {
        switch basis ?? "" {
        case "workbookOfficial":
            return 0
        case "officialUpdate":
            return 1
        case "workbookEstimate":
            return 2
        case "positionPrior":
            return 3
        case "syntheticDemo":
            return 4
        default:
            return 5
        }
    }
}

// MARK: - EuroLeague tables

/// Squad: Player, Pos, Age, Role, Basis, Proj MIN, PTS/40, REB/40, AST/40, Status (10 columns).
struct MacSquadTable: View {

    private let rows: [MacSquadRow]
    @Binding private var selection: MacSquadRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacSquadRow>]
    private let onShowDetails: (MacSquadRow.ID) -> Void

    init(rows: [MacSquadRow],
         selection: Binding<MacSquadRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacSquadRow>]>,
         onShowDetails: @escaping (MacSquadRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onShowDetails = onShowDetails
    }

    private var sortedRows: [MacSquadRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Player", value: \MacSquadRow.player) { row in
                TextCell(text: row.player)
            }
            .width(min: 130, ideal: 170)
            TableColumn("Pos", value: \MacSquadRow.position) { row in
                TextCell(text: row.position)
            }
            .width(min: 36, ideal: 44, max: 60)
            TableColumn("Age", value: \MacSquadRow.age.sort) { row in
                MacStatCell(value: row.age)
            }
            .width(min: 36, ideal: 44, max: 60)
            TableColumn("Role", value: \MacSquadRow.role) { row in
                TextCell(text: row.role)
            }
            .width(min: 64, ideal: 84, max: 120)
            TableColumn("Basis", value: \MacSquadRow.basisSort) { row in
                MacBasisCell(basis: row.basis, label: row.basisLabel)
            }
            .width(min: 120, ideal: 150, max: 200)
            TableColumn("Proj MIN", value: \MacSquadRow.projectedMinutes.sort) { row in
                MacStatCell(value: row.projectedMinutes)
            }
            .width(min: 60, ideal: 70, max: 90)
            TableColumn("PTS/40", value: \MacSquadRow.per40Points.sort) { row in
                MacStatCell(value: row.per40Points)
            }
            .width(min: 54, ideal: 64, max: 84)
            TableColumn("REB/40", value: \MacSquadRow.per40Rebounds.sort) { row in
                MacStatCell(value: row.per40Rebounds)
            }
            .width(min: 54, ideal: 64, max: 84)
            TableColumn("AST/40", value: \MacSquadRow.per40Assists.sort) { row in
                MacStatCell(value: row.per40Assists)
            }
            .width(min: 54, ideal: 64, max: 84)
            TableColumn("Status", value: \MacSquadRow.statusSort) { row in
                MacOptionalStatusCell(status: row.status, label: row.statusLabel)
            }
            .width(min: 110, ideal: 150, max: 200)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacSquadRow.ID.self) { ids in
            MacSquadMenu.items(ids: ids, rows: rows, onShowDetails: onShowDetails)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }
}

/// Season: Player, GP, MIN, PTS, REB, AST, PIR, 2P%, 3P%, FT% (10 columns).
struct MacSquadSeasonTable: View {

    private let rows: [MacSquadRow]
    @Binding private var selection: MacSquadRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacSquadRow>]
    private let onShowDetails: (MacSquadRow.ID) -> Void

    init(rows: [MacSquadRow],
         selection: Binding<MacSquadRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacSquadRow>]>,
         onShowDetails: @escaping (MacSquadRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onShowDetails = onShowDetails
    }

    private var sortedRows: [MacSquadRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Player", value: \MacSquadRow.player) { row in
                TextCell(text: row.player)
            }
            .width(min: 130, ideal: 170)
            TableColumn("GP", value: \MacSquadRow.seasonGames.sort) { row in
                MacStatCell(value: row.seasonGames)
            }
            .width(min: 36, ideal: 44, max: 60)
            TableColumn("MIN", value: \MacSquadRow.seasonMinutes.sort) { row in
                MacStatCell(value: row.seasonMinutes)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("PTS", value: \MacSquadRow.seasonPoints.sort) { row in
                MacStatCell(value: row.seasonPoints)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("REB", value: \MacSquadRow.seasonRebounds.sort) { row in
                MacStatCell(value: row.seasonRebounds)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("AST", value: \MacSquadRow.seasonAssists.sort) { row in
                MacStatCell(value: row.seasonAssists)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("PIR", value: \MacSquadRow.seasonPir.sort) { row in
                MacStatCell(value: row.seasonPir)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("2P%", value: \MacSquadRow.seasonTwoPct.sort) { row in
                MacStatCell(value: row.seasonTwoPct)
            }
            .width(min: 52, ideal: 62, max: 84)
            TableColumn("3P%", value: \MacSquadRow.seasonThreePct.sort) { row in
                MacStatCell(value: row.seasonThreePct)
            }
            .width(min: 52, ideal: 62, max: 84)
            TableColumn("FT%", value: \MacSquadRow.seasonFreeThrowPct.sort) { row in
                MacStatCell(value: row.seasonFreeThrowPct)
            }
            .width(min: 52, ideal: 62, max: 84)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacSquadRow.ID.self) { ids in
            MacSquadMenu.items(ids: ids, rows: rows, onShowDetails: onShowDetails)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }
}

/// Per 40: Player, Pos, PTS, REB, AST, 3PM, STL, BLK, TOV, Status (10 columns).
struct MacSquadPer40Table: View {

    private let rows: [MacSquadRow]
    @Binding private var selection: MacSquadRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacSquadRow>]
    private let onShowDetails: (MacSquadRow.ID) -> Void

    init(rows: [MacSquadRow],
         selection: Binding<MacSquadRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacSquadRow>]>,
         onShowDetails: @escaping (MacSquadRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onShowDetails = onShowDetails
    }

    private var sortedRows: [MacSquadRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Player", value: \MacSquadRow.player) { row in
                TextCell(text: row.player)
            }
            .width(min: 130, ideal: 170)
            TableColumn("Pos", value: \MacSquadRow.position) { row in
                TextCell(text: row.position)
            }
            .width(min: 36, ideal: 44, max: 60)
            TableColumn("PTS", value: \MacSquadRow.per40Points.sort) { row in
                MacStatCell(value: row.per40Points)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("REB", value: \MacSquadRow.per40Rebounds.sort) { row in
                MacStatCell(value: row.per40Rebounds)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("AST", value: \MacSquadRow.per40Assists.sort) { row in
                MacStatCell(value: row.per40Assists)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("3PM", value: \MacSquadRow.per40Threes.sort) { row in
                MacStatCell(value: row.per40Threes)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("STL", value: \MacSquadRow.per40Steals.sort) { row in
                MacStatCell(value: row.per40Steals)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("BLK", value: \MacSquadRow.per40Blocks.sort) { row in
                MacStatCell(value: row.per40Blocks)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("TOV", value: \MacSquadRow.per40Turnovers.sort) { row in
                MacStatCell(value: row.per40Turnovers)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("Status", value: \MacSquadRow.statusSort) { row in
                MacOptionalStatusCell(status: row.status, label: row.statusLabel)
            }
            .width(min: 110, ideal: 150, max: 200)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacSquadRow.ID.self) { ids in
            MacSquadMenu.items(ids: ids, rows: rows, onShowDetails: onShowDetails)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }
}

/// Projected: Player, MIN, PTS, REB, AST, 3PM, STL, BLK, TOV, PIR (10 columns). Offered only when
/// the server sends a projected line.
struct MacSquadProjectedTable: View {

    private let rows: [MacSquadRow]
    @Binding private var selection: MacSquadRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacSquadRow>]
    private let onShowDetails: (MacSquadRow.ID) -> Void

    init(rows: [MacSquadRow],
         selection: Binding<MacSquadRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacSquadRow>]>,
         onShowDetails: @escaping (MacSquadRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onShowDetails = onShowDetails
    }

    private var sortedRows: [MacSquadRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Player", value: \MacSquadRow.player) { row in
                TextCell(text: row.player)
            }
            .width(min: 130, ideal: 170)
            TableColumn("MIN", value: \MacSquadRow.gameMinutes.sort) { row in
                MacStatCell(value: row.gameMinutes)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("PTS", value: \MacSquadRow.gamePoints.sort) { row in
                MacStatCell(value: row.gamePoints)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("REB", value: \MacSquadRow.gameRebounds.sort) { row in
                MacStatCell(value: row.gameRebounds)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("AST", value: \MacSquadRow.gameAssists.sort) { row in
                MacStatCell(value: row.gameAssists)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("3PM", value: \MacSquadRow.gameThrees.sort) { row in
                MacStatCell(value: row.gameThrees)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("STL", value: \MacSquadRow.gameSteals.sort) { row in
                MacStatCell(value: row.gameSteals)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("BLK", value: \MacSquadRow.gameBlocks.sort) { row in
                MacStatCell(value: row.gameBlocks)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("TOV", value: \MacSquadRow.gameTurnovers.sort) { row in
                MacStatCell(value: row.gameTurnovers)
            }
            .width(min: 44, ideal: 54, max: 76)
            TableColumn("PIR", value: \MacSquadRow.gamePir.sort) { row in
                MacStatCell(value: row.gamePir)
            }
            .width(min: 44, ideal: 54, max: 76)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacSquadRow.ID.self) { ids in
            MacSquadMenu.items(ids: ids, rows: rows, onShowDetails: onShowDetails)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }
}

/// The row menu every squad table shares.
enum MacSquadMenu {

    @ViewBuilder static func items(ids: Set<MacSquadRow.ID>,
                                   rows: [MacSquadRow],
                                   onShowDetails: @escaping (MacSquadRow.ID) -> Void) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Show Details") {
                onShowDetails(id)
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacSquadRow.copyHeader, rows: [row.copyCells])
            }
        }
    }
}
#endif
