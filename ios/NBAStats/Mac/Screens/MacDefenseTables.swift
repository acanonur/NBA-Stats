#if os(macOS)
import SwiftUI

// The league table of the Defence by Position screen: one row per team, one column per position,
// in two column sets (guards, forwards, centres; and the workbook's five positions), plus the row
// builder that turns the server's `LeagueDefenseDocument` into those rows.
//
// WHY TWO CONCRETE TABLES AND ONE ROW TYPE
// A `Table` column builder cannot hold an `if` at macOS 14.0 (MAC_DESIGN 6.3), so "three positions
// or five" cannot be a conditional inside one table. It is two `Table` structs, chosen by the
// screen. Both read the same `MacDefenseRow`, which carries a cell for every position either
// scheme has (G, F, PG, SG, SF, PF, the C both share, and Unassigned); a scheme leaves the cells it
// does not use at `MacDefenseCell.empty`. One row type keeps the sort state, the selection and the
// Copy Table export in one place.
//
// WHAT A POSITION CELL SHOWS, AND WHERE IT COMES FROM
// "Per game" basis: the points that team allowed per game to opponents listed at that position
// (`pointsAllowedPerGame`), then the server's signed difference from the league (`deltaPerGame`).
// "Per N minutes" basis: the server's rate (`pointsPerRegulationMinutes`) and the league's rate
// (`leagueRate`). The app never subtracts one from the other. The server keeps `deltaPerGame` per
// game on both bases, so showing it next to a per-minute rate would put two units in one cell.
//
// THE HONEST STATES, DECIDED BY THE ROW'S OWN PAYLOAD FIELDS (never by the app)
//   withheld, reason minimumGames   the points are shown dimmed; no difference, no band
//   withheld for any other reason,  the difference is shown as a plain fact in grey; no band tone
//   or provisional
//   neither                         the difference is toned by the server's `band` and nothing else
//                                   (better is green, worse is red, typical is neutral, none is grey)
// These are the same rules `DefenseBucketBars` applies in the team breakdown (`DefenseDisplay`), so
// a table cell and the inspector beside it can never disagree about what a number means.
//
// NOT HERE, ON PURPOSE
// There is no rank column and no ordering by a score. Rows keep the server's order (points allowed
// per game, ascending) until a header is clicked, and a click sorts by a served value only. The
// unassigned bucket is drawn neutral and labelled, never reassigned. A missing number is an em dash.

// MARK: - Tone and status

/// How a position cell is drawn. Derived from the payload's `withheld`, `provisional` and `band`.
enum MacDefenseTone: String, Hashable {
    /// Too few games to compare with the league: dimmed, no difference.
    case dimmed
    /// Points scored by opponents with no listed position.
    case unassigned
    /// A fact in plain grey: provisional, withheld for another reason, or no band.
    case plain
    case better
    case typical
    case worse

    /// The tone the server's band gives a difference. A missing or unknown band is plain.
    static func forBand(_ band: String?) -> MacDefenseTone {
        switch band ?? "" {
        case "better":
            return MacDefenseTone.better
        case "worse":
            return MacDefenseTone.worse
        case "typical":
            return MacDefenseTone.typical
        default:
            return MacDefenseTone.plain
        }
    }
}

/// The Status column. The raw values double as the sort key, so a click groups the rows that need
/// a caution together.
enum MacDefenseStatus: Int, Hashable {
    case clear = 0
    case provisional = 1
    case withheld = 2

    var text: String {
        switch self {
        case .clear:
            return ""
        case .provisional:
            return "Provisional"
        case .withheld:
            return "Withheld"
        }
    }
}

// MARK: - Cell and row

/// One position of one team: the text to draw and the number to sort by.
struct MacDefenseCell: Hashable {
    let value: String
    let secondary: String
    let sortKey: Double
    let tone: MacDefenseTone
    let help: String

    /// A position this table has no figure for: an em dash that sorts below every real number.
    static let empty = MacDefenseCell(value: Formatting.emDash,
                                      secondary: "",
                                      sortKey: -Double.infinity,
                                      tone: MacDefenseTone.plain,
                                      help: "")

    /// What Copy Table puts in the cell: the value, then the second figure in brackets.
    var exportText: String {
        if secondary.isEmpty { return value }
        return value + " (" + secondary + ")"
    }
}

/// One team of the table. Every field is already display text, or a number to sort by.
struct MacDefenseRow: Identifiable, Hashable {
    let id: String
    /// The row's place in the server's order, which is the default order of the table.
    let serverOrder: Int
    let abbr: String
    let name: String
    let games: String
    let gamesSort: Double
    let allowed: String
    let allowedSort: Double
    let g: MacDefenseCell
    let f: MacDefenseCell
    let c: MacDefenseCell
    let pg: MacDefenseCell
    let sg: MacDefenseCell
    let sf: MacDefenseCell
    let pf: MacDefenseCell
    let unassigned: MacDefenseCell
    let status: MacDefenseStatus
    let statusSort: Int
    let statusHelp: String

    /// The row as the text of a spreadsheet row, in the column order of its table.
    func exportCells(fiveWay: Bool) -> [String] {
        var cells: [String] = [abbr, games, allowed]
        if fiveWay {
            cells.append(pg.exportText)
            cells.append(sg.exportText)
            cells.append(sf.exportText)
            cells.append(pf.exportText)
            cells.append(c.exportText)
        } else {
            cells.append(g.exportText)
            cells.append(f.exportText)
            cells.append(c.exportText)
        }
        cells.append(unassigned.exportText)
        cells.append(status.text)
        return cells
    }
}

/// The column titles, in the order of each table, for Copy Table.
enum MacDefenseColumns {
    static let gfcHeader: [String] = ["Team", "GP", "PA/G", "G", "F", "C", "Unassigned", "Status"]
    static let fiveHeader: [String] = ["Team", "GP", "PA/G", "PG", "SG", "SF", "PF", "C", "Unassigned", "Status"]
}

// MARK: - Row builder

enum MacDefenseRows {

    /// The rows of a league table document, in the server's order. A document with no `teams` (the
    /// one-team route) has none. `minutes` is the league's regulation length, for the tooltips.
    static func make(from document: LeagueDefenseDocument?, perMinute: Bool, minutes: Int) -> [MacDefenseRow] {
        guard let teams = document?.teams else { return [] }
        var result: [MacDefenseRow] = []
        for (index, row) in teams.enumerated() {
            result.append(makeRow(row, index: index, perMinute: perMinute, minutes: minutes))
        }
        return result
    }

    private static func makeRow(_ row: LeagueDefenseTeamRow,
                                index: Int,
                                perMinute: Bool,
                                minutes: Int) -> MacDefenseRow {
        let status = statusOf(row)
        let g = cell("G", in: row, perMinute: perMinute, minutes: minutes)
        let f = cell("F", in: row, perMinute: perMinute, minutes: minutes)
        let c = cell("C", in: row, perMinute: perMinute, minutes: minutes)
        let pg = cell("PG", in: row, perMinute: perMinute, minutes: minutes)
        let sg = cell("SG", in: row, perMinute: perMinute, minutes: minutes)
        let sf = cell("SF", in: row, perMinute: perMinute, minutes: minutes)
        let pf = cell("PF", in: row, perMinute: perMinute, minutes: minutes)
        let unassigned = cell("unknown", in: row, perMinute: perMinute, minutes: minutes)
        let teamId: String = row.team?.id ?? ("row-" + String(index))
        return MacDefenseRow(id: teamId,
                             serverOrder: index,
                             abbr: row.team?.displayAbbr ?? Formatting.emDash,
                             name: row.team?.displayName ?? Formatting.emDash,
                             games: LeagueFormatting.integer(row.games),
                             gamesSort: LeagueFormatting.sortKey(row.games),
                             allowed: LeagueFormatting.number(row.pointsAllowedPerGame),
                             allowedSort: LeagueFormatting.sortKey(row.pointsAllowedPerGame),
                             g: g,
                             f: f,
                             c: c,
                             pg: pg,
                             sg: sg,
                             sf: sf,
                             pf: pf,
                             unassigned: unassigned,
                             status: status.kind,
                             statusSort: status.kind.rawValue,
                             statusHelp: status.help)
    }

    /// Withheld wins over provisional: a withheld team is always provisional too, and the stronger
    /// caution is the one to show. The tooltip is the server's own sentence.
    private static func statusOf(_ row: LeagueDefenseTeamRow) -> (kind: MacDefenseStatus, help: String) {
        if let withheld = row.withheld {
            let message = withheld.message ?? LeagueFormatting.withheldReasonWord(withheld.reason)
            return (MacDefenseStatus.withheld, message)
        }
        if row.provisional == true {
            return (MacDefenseStatus.provisional, "Few games so far, so these numbers can still move a lot.")
        }
        return (MacDefenseStatus.clear, "")
    }

    /// The cell of one position, looked up by the bucket's own `position` key, never by index: a
    /// team the server sent four buckets for and a team it sent six for both land in the right column.
    static func cell(_ position: String,
                     in row: LeagueDefenseTeamRow,
                     perMinute: Bool,
                     minutes: Int) -> MacDefenseCell {
        let buckets: [LeagueDefenseBucket] = row.buckets ?? []
        var match: LeagueDefenseBucket?
        for bucket in buckets where bucket.position == position {
            match = bucket
            break
        }
        guard let bucket = match else { return MacDefenseCell.empty }
        return makeCell(bucket, withheld: row.withheld, provisional: row.provisional, perMinute: perMinute, minutes: minutes)
    }

    private static func makeCell(_ bucket: LeagueDefenseBucket,
                                 withheld: LeagueWithheld?,
                                 provisional: Bool?,
                                 perMinute: Bool,
                                 minutes: Int) -> MacDefenseCell {
        let isUnassigned = bucket.position == "unknown"
        let primary: Double? = perMinute ? bucket.pointsPerRegulationMinutes : bucket.pointsAllowedPerGame
        var tone = MacDefenseTone.plain
        var secondary = ""
        if isUnassigned {
            tone = MacDefenseTone.unassigned
        } else if DefenseDisplay.hidesDelta(withheld: withheld) {
            tone = MacDefenseTone.dimmed
        } else {
            secondary = secondaryText(bucket, perMinute: perMinute)
            let greyed = DefenseDisplay.isGreyed(withheld: withheld, provisional: provisional)
            tone = greyed ? MacDefenseTone.plain : MacDefenseTone.forBand(bucket.band)
        }
        return MacDefenseCell(value: LeagueFormatting.number(primary),
                              secondary: secondary,
                              sortKey: LeagueFormatting.sortKey(primary),
                              tone: tone,
                              help: helpText(bucket, tone: tone, perMinute: perMinute, minutes: minutes))
    }

    /// The second figure of a cell: the server's difference from the league (per game), or the
    /// league's own rate (per minutes). Empty when the server sent none.
    private static func secondaryText(_ bucket: LeagueDefenseBucket, perMinute: Bool) -> String {
        if perMinute {
            guard let rate = bucket.leagueRate else { return "" }
            return "lg " + LeagueFormatting.number(rate)
        }
        guard let delta = bucket.deltaPerGame else { return "" }
        return LeagueFormatting.signed(delta)
    }

    private static func helpText(_ bucket: LeagueDefenseBucket,
                                 tone: MacDefenseTone,
                                 perMinute: Bool,
                                 minutes: Int) -> String {
        if bucket.position == "unknown" {
            return "Points scored by opponents with no listed position. They are never reassigned to a position."
        }
        var sentence = DefenseDisplay.title(for: bucket, density: .full) + ": "
        if perMinute {
            sentence += LeagueFormatting.number(bucket.pointsPerRegulationMinutes)
            sentence += " points per " + String(minutes) + " opponent minutes at this position"
            if let rate = bucket.leagueRate {
                sentence += "; the league's rate is " + LeagueFormatting.number(rate)
            }
        } else {
            sentence += LeagueFormatting.number(bucket.pointsAllowedPerGame) + " points allowed per game"
            if let average = bucket.leagueAverage {
                sentence += "; the league average is " + LeagueFormatting.number(average)
            }
        }
        sentence += "."
        if tone == MacDefenseTone.dimmed {
            sentence += " Too few games to compare with the league."
        }
        return sentence
    }
}

// MARK: - Cells

/// A position cell: the points, then the second figure in a smaller type, right-aligned.
struct MacDefensePositionCell: View {
    private let cell: MacDefenseCell

    init(cell: MacDefenseCell) {
        self.cell = cell
    }

    private var valueColor: Color {
        switch cell.tone {
        case .dimmed:
            return Palette.textTertiary
        case .unassigned:
            return Palette.textSecondary
        case .plain, .better, .typical, .worse:
            return Palette.textPrimary
        }
    }

    private var secondaryColor: Color {
        switch cell.tone {
        case .better:
            return Palette.positive
        case .worse:
            return Palette.negative
        case .typical:
            return Palette.neutral
        case .plain, .unassigned:
            return Palette.textSecondary
        case .dimmed:
            return Palette.textTertiary
        }
    }

    private var opacityValue: Double {
        cell.tone == MacDefenseTone.dimmed ? 0.6 : 1.0
    }

    var body: some View {
        HStack(spacing: Spacing.xs) {
            Spacer(minLength: 0)
            Text(cell.value)
                .hardwoodText(.tableCell, color: valueColor)
            if !cell.secondary.isEmpty {
                Text(cell.secondary)
                    .hardwoodText(.caption, color: secondaryColor)
            }
        }
        .lineLimit(1)
        .opacity(opacityValue)
        .help(cell.help)
    }
}

/// The Status column: a Withheld or Provisional chip, with the server's sentence as the tooltip.
struct MacDefenseStatusCell: View {
    private let status: MacDefenseStatus
    private let help: String

    init(status: MacDefenseStatus, help: String) {
        self.status = status
        self.help = help
    }

    var body: some View {
        HStack(spacing: 0) {
            switch status {
            case .withheld:
                LeagueChip(text: status.text, symbol: "exclamationmark.triangle", tint: Palette.warning)
            case .provisional:
                ProvisionalBadge()
            case .clear:
                EmptyView()
            }
            Spacer(minLength: 0)
        }
        .help(help)
    }
}

// MARK: - Tables

/// Guards, forwards, centres: eight columns.
struct MacDefenseTableGFC: View {
    private let rows: [MacDefenseRow]
    @Binding private var selection: MacDefenseRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacDefenseRow>]
    private let onOpenMatchup: (MacDefenseRow.ID) -> Void
    private let onShowDetails: (MacDefenseRow.ID) -> Void

    init(rows: [MacDefenseRow],
         selection: Binding<MacDefenseRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacDefenseRow>]>,
         onOpenMatchup: @escaping (MacDefenseRow.ID) -> Void,
         onShowDetails: @escaping (MacDefenseRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onOpenMatchup = onOpenMatchup
        self.onShowDetails = onShowDetails
    }

    private var sortedRows: [MacDefenseRow] {
        rows.sorted(using: sortOrder)
    }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Team", value: \MacDefenseRow.abbr) { row in
                TeamCell(abbr: row.abbr, name: row.name)
            }
            .width(min: 70, ideal: 90, max: 130)
            TableColumn("GP", value: \MacDefenseRow.gamesSort) { row in
                NumCell(text: row.games)
            }
            .width(min: 36, ideal: 44, max: 60)
            TableColumn("PA/G", value: \MacDefenseRow.allowedSort) { row in
                NumCell(text: row.allowed)
            }
            .width(min: 56, ideal: 66, max: 90)
            TableColumn("G", value: \MacDefenseRow.g.sortKey) { row in
                MacDefensePositionCell(cell: row.g)
            }
            .width(min: 90, ideal: 120, max: 170)
            TableColumn("F", value: \MacDefenseRow.f.sortKey) { row in
                MacDefensePositionCell(cell: row.f)
            }
            .width(min: 90, ideal: 120, max: 170)
            TableColumn("C", value: \MacDefenseRow.c.sortKey) { row in
                MacDefensePositionCell(cell: row.c)
            }
            .width(min: 90, ideal: 120, max: 170)
            TableColumn("Unassigned", value: \MacDefenseRow.unassigned.sortKey) { row in
                MacDefensePositionCell(cell: row.unassigned)
            }
            .width(min: 70, ideal: 90, max: 130)
            TableColumn("Status", value: \MacDefenseRow.statusSort) { row in
                MacDefenseStatusCell(status: row.status, help: row.statusHelp)
            }
            .width(min: 90, ideal: 110, max: 150)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacDefenseRow.ID.self) { ids in
            if let id = ids.first {
                Button("Show Details") {
                    onShowDetails(id)
                }
                Button("Open Matchup") {
                    onOpenMatchup(id)
                }
                Button("Copy Row") {
                    copyRow(id)
                }
            }
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }

    private func copyRow(_ id: MacDefenseRow.ID) {
        guard let row = rows.first(where: { $0.id == id }) else { return }
        MacTableExport.copy(header: MacDefenseColumns.gfcHeader, rows: [row.exportCells(fiveWay: false)])
    }
}

/// The workbook's five positions: ten columns, the most a Table here may have.
struct MacDefenseTable5: View {
    private let rows: [MacDefenseRow]
    @Binding private var selection: MacDefenseRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacDefenseRow>]
    private let onOpenMatchup: (MacDefenseRow.ID) -> Void
    private let onShowDetails: (MacDefenseRow.ID) -> Void

    init(rows: [MacDefenseRow],
         selection: Binding<MacDefenseRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacDefenseRow>]>,
         onOpenMatchup: @escaping (MacDefenseRow.ID) -> Void,
         onShowDetails: @escaping (MacDefenseRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onOpenMatchup = onOpenMatchup
        self.onShowDetails = onShowDetails
    }

    private var sortedRows: [MacDefenseRow] {
        rows.sorted(using: sortOrder)
    }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Team", value: \MacDefenseRow.abbr) { row in
                TeamCell(abbr: row.abbr, name: row.name)
            }
            .width(min: 70, ideal: 84, max: 120)
            TableColumn("GP", value: \MacDefenseRow.gamesSort) { row in
                NumCell(text: row.games)
            }
            .width(min: 36, ideal: 42, max: 56)
            TableColumn("PA/G", value: \MacDefenseRow.allowedSort) { row in
                NumCell(text: row.allowed)
            }
            .width(min: 56, ideal: 64, max: 80)
            TableColumn("PG", value: \MacDefenseRow.pg.sortKey) { row in
                MacDefensePositionCell(cell: row.pg)
            }
            .width(min: 78, ideal: 100, max: 140)
            TableColumn("SG", value: \MacDefenseRow.sg.sortKey) { row in
                MacDefensePositionCell(cell: row.sg)
            }
            .width(min: 78, ideal: 100, max: 140)
            TableColumn("SF", value: \MacDefenseRow.sf.sortKey) { row in
                MacDefensePositionCell(cell: row.sf)
            }
            .width(min: 78, ideal: 100, max: 140)
            TableColumn("PF", value: \MacDefenseRow.pf.sortKey) { row in
                MacDefensePositionCell(cell: row.pf)
            }
            .width(min: 78, ideal: 100, max: 140)
            TableColumn("C", value: \MacDefenseRow.c.sortKey) { row in
                MacDefensePositionCell(cell: row.c)
            }
            .width(min: 78, ideal: 100, max: 140)
            TableColumn("Unassigned", value: \MacDefenseRow.unassigned.sortKey) { row in
                MacDefensePositionCell(cell: row.unassigned)
            }
            .width(min: 64, ideal: 80, max: 110)
            TableColumn("Status", value: \MacDefenseRow.statusSort) { row in
                MacDefenseStatusCell(status: row.status, help: row.statusHelp)
            }
            .width(min: 86, ideal: 100, max: 140)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacDefenseRow.ID.self) { ids in
            if let id = ids.first {
                Button("Show Details") {
                    onShowDetails(id)
                }
                Button("Open Matchup") {
                    onOpenMatchup(id)
                }
                Button("Copy Row") {
                    copyRow(id)
                }
            }
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }

    private func copyRow(_ id: MacDefenseRow.ID) {
        guard let row = rows.first(where: { $0.id == id }) else { return }
        MacTableExport.copy(header: MacDefenseColumns.fiveHeader, rows: [row.exportCells(fiveWay: true)])
    }
}
#endif
