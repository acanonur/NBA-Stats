#if os(macOS)
import SwiftUI

// The Team Ratings tables and the cards beside them: the EuroLeague's club ratings (the workbook's
// Team Ratings sheet) and the NBA's efficiency table.
//
// WHAT EACH LEAGUE'S TABLE IS MADE OF
// EuroLeague (`GET /v1/el/teams`): record, points scored and allowed per game, and the model's two
// indices. `attackIndex` is the club's projected scoring relative to the league's; `defenceIndex`
// is its projected points allowed relative to the league's, so HIGHER MEANS MORE POINTS ALLOWED and
// a small defence index is a good defence. The caption says so, because the opposite reading is the
// natural one.
// NBA (`team_efficiency` through the dashboard resolver, joined to the defence table): record,
// offensive, defensive and net rating, pace, and points allowed per game. Net rating is the value
// the server sent, not ORtg minus DRtg worked out here.
//
// THE CARD
// Selecting a club opens its rating as the workbook builds it: the priors, the adjustments the
// round results have made, the projected points they imply, where the latest update came from, and
// each game that moved it (what the model projected, what happened, the weight). All of that is read
// from `GET /v1/el/ratings`; the app computes none of it.

// MARK: - EuroLeague rows

struct MacRatingsRow: Identifiable, Hashable {
    let id: String
    let order: Int
    let clubCode: String
    let club: String
    let clubName: String
    let record: MacStatValue
    let pointsFor: MacStatValue
    let pointsAgainst: MacStatValue
    let attackIndex: MacStatValue
    let defenceIndex: MacStatValue
    let asOfRound: MacStatValue

    static let copyHeader: [String] = [
        "Club", "Record", "PPG", "PA/G", "Attack idx", "Defence idx", "As of round"
    ]

    var copyCells: [String] {
        [club, record.text, pointsFor.text, pointsAgainst.text, attackIndex.text, defenceIndex.text,
         asOfRound.text]
    }
}

enum MacRatingsRows {

    /// One row per club, in the order the server sent them.
    static func rows(from table: ElTeamsTable?) -> [MacRatingsRow] {
        guard let source = table?.teams else { return [] }
        var result: [MacRatingsRow] = []
        var index = 0
        for entry in source {
            result.append(make(entry, index: index))
            index += 1
        }
        return result
    }

    private static func make(_ entry: ElTeamsRow, index: Int) -> MacRatingsRow {
        let team: LeagueTeamRef? = entry.team
        let code: String = team?.clubCode ?? team?.id ?? ("row-" + String(index))
        return MacRatingsRow(
            id: code,
            order: index,
            clubCode: code,
            club: team?.displayAbbr ?? Formatting.emDash,
            clubName: team?.displayName ?? Formatting.emDash,
            record: recordValue(entry),
            pointsFor: MacStatValue(number: entry.pointsPerGame, places: 1),
            pointsAgainst: MacStatValue(number: entry.pointsAllowedPerGame, places: 1),
            attackIndex: MacStatValue(number: entry.rating?.attackIndex, places: 3),
            defenceIndex: MacStatValue(number: entry.rating?.defenceIndex, places: 3),
            asOfRound: MacStatValue(whole: entry.rating?.asOfRound)
        )
    }

    /// `3–1`, sorted by wins, with the games played as the tooltip.
    private static func recordValue(_ entry: ElTeamsRow) -> MacStatValue {
        var help = ""
        if let games = entry.games {
            help = String(games) + (games == 1 ? " game" : " games")
        }
        return MacStatValue(words: LeagueFormatting.record(entry.record),
                            sort: LeagueFormatting.sortKey(entry.record?.wins),
                            help: help)
    }
}

/// Words for where a club's latest rating update came from.
enum MacRatingsWords {

    static func source(_ source: String?) -> String {
        guard let source = source, !source.isEmpty else { return Formatting.emDash }
        switch source {
        case "workbookImport":
            return "Imported from your workbook"
        case "roundUpdate":
            return "Moved by round results"
        case "manual":
            return "Entered by you"
        case "syntheticDemo":
            return "Invented demo"
        default:
            return source
        }
    }

    static func updateBasis(_ basis: String?) -> String {
        guard let basis = basis, !basis.isEmpty else { return Formatting.emDash }
        switch basis {
        case "locked":
            return "measured against the projection locked before tip-off"
        case "reconstructed":
            return "measured against a projection rebuilt after the game"
        default:
            return basis
        }
    }
}

// MARK: - EuroLeague table

/// Team Ratings (7 columns).
struct MacRatingsTable: View {

    private let rows: [MacRatingsRow]
    @Binding private var selection: MacRatingsRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacRatingsRow>]
    private let onShowDetails: (MacRatingsRow.ID) -> Void
    private let onOpenDefense: (MacRatingsRow.ID) -> Void
    private let onOpenClub: (MacRatingsRow.ID) -> Void

    init(rows: [MacRatingsRow],
         selection: Binding<MacRatingsRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacRatingsRow>]>,
         onShowDetails: @escaping (MacRatingsRow.ID) -> Void,
         onOpenDefense: @escaping (MacRatingsRow.ID) -> Void,
         onOpenClub: @escaping (MacRatingsRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onShowDetails = onShowDetails
        self.onOpenDefense = onOpenDefense
        self.onOpenClub = onOpenClub
    }

    private var sortedRows: [MacRatingsRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Club", value: \MacRatingsRow.club) { row in
                TeamCell(abbr: row.club, name: row.clubName)
            }
            .width(min: 64, ideal: 84, max: 120)
            TableColumn("Record", value: \MacRatingsRow.record.sort) { row in
                MacStatCell(value: row.record)
            }
            .width(min: 56, ideal: 68, max: 90)
            TableColumn("PPG", value: \MacRatingsRow.pointsFor.sort) { row in
                MacStatCell(value: row.pointsFor)
            }
            .width(min: 52, ideal: 64, max: 90)
            TableColumn("PA/G", value: \MacRatingsRow.pointsAgainst.sort) { row in
                MacStatCell(value: row.pointsAgainst)
            }
            .width(min: 52, ideal: 64, max: 90)
            TableColumn("Attack idx", value: \MacRatingsRow.attackIndex.sort) { row in
                MacStatCell(value: row.attackIndex)
            }
            .width(min: 70, ideal: 84, max: 110)
            TableColumn("Defence idx", value: \MacRatingsRow.defenceIndex.sort) { row in
                MacStatCell(value: row.defenceIndex)
            }
            .width(min: 78, ideal: 92, max: 120)
            TableColumn("As of round", value: \MacRatingsRow.asOfRound.sort) { row in
                MacStatCell(value: row.asOfRound)
            }
            .width(min: 76, ideal: 90, max: 120)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacRatingsRow.ID.self) { ids in
            menuItems(for: ids)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacRatingsRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Show Details") {
                onShowDetails(id)
            }
            Button("Open Defence") {
                onOpenDefense(id)
            }
            Button("Open in New Window") {
                onOpenClub(id)
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacRatingsRow.copyHeader, rows: [row.copyCells])
            }
        }
    }
}

// MARK: - EuroLeague card

/// One game that moved a club's rating, as two short lines.
struct MacRatingUpdateLine: Identifiable, Hashable {
    let id: String
    let title: String
    let detail: String
}

enum MacRatingUpdateLines {

    /// The updates that belong to one club, in the order the server sent them.
    static func lines(for clubCode: String, from updates: [ElRatingUpdate]?) -> [MacRatingUpdateLine] {
        var result: [MacRatingUpdateLine] = []
        var index = 0
        for update in updates ?? [] {
            let owner: String = update.team?.clubCode ?? update.team?.id ?? ""
            if owner == clubCode {
                result.append(line(update, index: index))
            }
            index += 1
        }
        return result
    }

    /// Every update of one game, whichever club it moved, in the order the server sent them.
    static func lines(forGame gameId: String, from updates: [ElRatingUpdate]?) -> [MacRatingUpdateLine] {
        var result: [MacRatingUpdateLine] = []
        var index = 0
        for update in updates ?? [] {
            if update.gameId == gameId {
                result.append(line(update, index: index, includesClub: true))
            }
            index += 1
        }
        return result
    }

    private static func line(_ update: ElRatingUpdate, index: Int, includesClub: Bool = false) -> MacRatingUpdateLine {
        var titleParts: [String] = []
        if includesClub {
            titleParts.append(update.team?.displayAbbr ?? Formatting.emDash)
        }
        titleParts.append("Round " + LeagueFormatting.integer(update.round))
        titleParts.append("v " + (update.opponent?.displayAbbr ?? Formatting.emDash))

        let scoredActual: String = LeagueFormatting.number(update.scored, places: 0)
        let scoredModel: String = LeagueFormatting.number(update.projectedScored)
        let allowedActual: String = LeagueFormatting.number(update.allowed, places: 0)
        let allowedModel: String = LeagueFormatting.number(update.projectedAllowed)
        let scoredText: String = "Scored " + scoredActual + " (projected " + scoredModel + ")"
        let allowedText: String = "Allowed " + allowedActual + " (projected " + allowedModel + ")"
        let weightText: String = "Weight " + LeagueFormatting.number(update.weight, places: 3)
        var weightLine: String = weightText + ", " + MacRatingsWords.updateBasis(update.basis)
        if update.extrapolated == true {
            weightLine += " (weight beyond the rounds the workbook states)"
        }
        let detail: String = scoredText + " · " + allowedText + "\n" + weightLine
        return MacRatingUpdateLine(id: (update.gameId ?? "update") + "-" + String(index),
                                   title: titleParts.joined(separator: " · "),
                                   detail: detail)
    }
}

/// A club's rating in full.
struct MacRatingsPanel: View {

    private let row: MacRatingsRow
    private let detail: ElRatingRow?
    private let updates: [MacRatingUpdateLine]
    private let onOpenDefense: () -> Void
    private let onOpenClub: () -> Void

    init(row: MacRatingsRow,
         detail: ElRatingRow?,
         updates: [MacRatingUpdateLine],
         onOpenDefense: @escaping () -> Void,
         onOpenClub: @escaping () -> Void) {
        self.row = row
        self.detail = detail
        self.updates = updates
        self.onOpenDefense = onOpenDefense
        self.onOpenClub = onOpenClub
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            HStack(spacing: Spacing.md) {
                TeamBadge(abbreviation: row.club, name: row.clubName, size: .large)
                Text(row.clubName)
                    .hardwoodText(.sectionTitle)
                    .lineLimit(2)
                Spacer(minLength: 0)
            }
            summary
            ratingFacts
            updatesSection
            HStack(spacing: Spacing.sm) {
                Button("Open Defence") {
                    onOpenDefense()
                }
                Button("Open in New Window") {
                    onOpenClub()
                }
                Spacer(minLength: 0)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var summary: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            DetailLine(label: "Record", value: row.record.text)
            DetailLine(label: "Scores per game", value: row.pointsFor.text)
            DetailLine(label: "Allows per game", value: row.pointsAgainst.text)
            DetailLine(label: "Attack index", value: row.attackIndex.text)
            DetailLine(label: "Defence index", value: row.defenceIndex.text)
        }
    }

    @ViewBuilder private var ratingFacts: some View {
        if let rating = detail {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                HStack(spacing: Spacing.sm) {
                    Text("Rating")
                        .hardwoodText(.statLabel)
                    if rating.priorIsEstimate == true {
                        EstimatedBadge(text: "Prior is an estimate")
                    }
                    Spacer(minLength: 0)
                }
                ratingLines(rating)
            }
        } else {
            Text("The rating breakdown is not available.")
                .hardwoodText(.caption)
        }
    }

    private func ratingLines(_ rating: ElRatingRow) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            DetailLine(label: "Points for, prior", value: LeagueFormatting.number(rating.pfPrior))
            DetailLine(label: "Points against, prior", value: LeagueFormatting.number(rating.paPrior))
            DetailLine(label: "Attack adjustment", value: LeagueFormatting.signed(rating.attackAdj, places: 2))
            DetailLine(label: "Defence adjustment", value: LeagueFormatting.signed(rating.defenceAdj, places: 2))
            DetailLine(label: "Projected for", value: LeagueFormatting.number(rating.projectedPointsFor))
            DetailLine(label: "Projected against", value: LeagueFormatting.number(rating.projectedPointsAgainst))
            DetailLine(label: "Latest update", value: MacRatingsWords.source(rating.source))
            DetailLine(label: "Games counted", value: LeagueFormatting.integer(rating.gamesCounted))
        }
    }

    @ViewBuilder private var updatesSection: some View {
        if !updates.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text("Games that moved this rating")
                    .hardwoodText(.statLabel)
                ForEach(updates) { update in
                    VStack(alignment: .leading, spacing: 1) {
                        Text(update.title)
                            .hardwoodText(.tableCell, monospacedDigits: false)
                        Text(update.detail)
                            .hardwoodText(.caption)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }
}

// MARK: - NBA rows

struct MacNbaRatingsRow: Identifiable, Hashable {
    let id: String
    let order: Int
    let teamId: Int
    let team: String
    let teamName: String
    let record: MacStatValue
    let offensive: MacStatValue
    let defensive: MacStatValue
    let net: MacStatValue
    let pace: MacStatValue
    let pointsAllowed: MacStatValue

    static let copyHeader: [String] = ["Team", "W–L", "ORtg", "DRtg", "Net", "Pace", "PA/G"]

    var copyCells: [String] {
        [team, record.text, offensive.text, defensive.text, net.text, pace.text, pointsAllowed.text]
    }
}

enum MacNbaRatingsRows {

    /// One row per team of the efficiency table, with points allowed per game from the defence
    /// table when it loaded.
    static func rows(from efficiency: TeamEfficiencyPayload?, defense: LeagueDefenseDocument?) -> [MacNbaRatingsRow] {
        guard let source = efficiency?.rows else { return [] }
        let allowed: [Int: Double] = pointsAllowed(from: defense)
        var result: [MacNbaRatingsRow] = []
        var index = 0
        for entry in source {
            result.append(make(entry, index: index, allowed: allowed[entry.team.teamId]))
            index += 1
        }
        return result
    }

    private static func make(_ entry: TeamEfficiencyRow, index: Int, allowed: Double?) -> MacNbaRatingsRow {
        MacNbaRatingsRow(
            id: String(entry.team.teamId),
            order: index,
            teamId: entry.team.teamId,
            team: entry.team.abbr,
            teamName: entry.team.name,
            record: MacStatValue(words: entry.recordText.replacingOccurrences(of: "-", with: "–"),
                                 sort: LeagueFormatting.sortKey(entry.wins)),
            offensive: MacStatValue(number: entry.value("off_rtg"), places: 1),
            defensive: MacStatValue(number: entry.value("def_rtg"), places: 1),
            net: MacStatValue(signed: entry.value("net_rtg"), places: 1),
            pace: MacStatValue(number: entry.value("pace"), places: 1),
            pointsAllowed: MacStatValue(number: allowed, places: 1)
        )
    }

    private static func pointsAllowed(from defense: LeagueDefenseDocument?) -> [Int: Double] {
        var result: [Int: Double] = [:]
        for row in defense?.teams ?? [] {
            if let id = row.team?.teamId, let value = row.pointsAllowedPerGame {
                result[id] = value
            }
        }
        return result
    }
}

// MARK: - NBA table

/// Team Ratings, NBA (7 columns).
struct MacNbaRatingsTable: View {

    private let rows: [MacNbaRatingsRow]
    @Binding private var selection: MacNbaRatingsRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacNbaRatingsRow>]
    private let onShowDetails: (MacNbaRatingsRow.ID) -> Void
    private let onOpenDefense: (MacNbaRatingsRow.ID) -> Void

    init(rows: [MacNbaRatingsRow],
         selection: Binding<MacNbaRatingsRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacNbaRatingsRow>]>,
         onShowDetails: @escaping (MacNbaRatingsRow.ID) -> Void,
         onOpenDefense: @escaping (MacNbaRatingsRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onShowDetails = onShowDetails
        self.onOpenDefense = onOpenDefense
    }

    private var sortedRows: [MacNbaRatingsRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Team", value: \MacNbaRatingsRow.team) { row in
                TeamCell(abbr: row.team, name: row.teamName)
            }
            .width(min: 64, ideal: 84, max: 120)
            TableColumn("W–L", value: \MacNbaRatingsRow.record.sort) { row in
                MacStatCell(value: row.record)
            }
            .width(min: 56, ideal: 68, max: 90)
            TableColumn("ORtg", value: \MacNbaRatingsRow.offensive.sort) { row in
                MacStatCell(value: row.offensive)
            }
            .width(min: 56, ideal: 68, max: 90)
            TableColumn("DRtg", value: \MacNbaRatingsRow.defensive.sort) { row in
                MacStatCell(value: row.defensive)
            }
            .width(min: 56, ideal: 68, max: 90)
            TableColumn("Net", value: \MacNbaRatingsRow.net.sort) { row in
                MacStatCell(value: row.net)
            }
            .width(min: 56, ideal: 68, max: 90)
            TableColumn("Pace", value: \MacNbaRatingsRow.pace.sort) { row in
                MacStatCell(value: row.pace)
            }
            .width(min: 56, ideal: 68, max: 90)
            TableColumn("PA/G", value: \MacNbaRatingsRow.pointsAllowed.sort) { row in
                MacStatCell(value: row.pointsAllowed)
            }
            .width(min: 56, ideal: 68, max: 90)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacNbaRatingsRow.ID.self) { ids in
            menuItems(for: ids)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacNbaRatingsRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Show Details") {
                onShowDetails(id)
            }
            Button("Open Defence") {
                onOpenDefense(id)
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacNbaRatingsRow.copyHeader, rows: [row.copyCells])
            }
        }
    }
}

/// An NBA team's efficiency line.
struct MacNbaRatingsPanel: View {

    private let row: MacNbaRatingsRow
    private let onOpenDefense: () -> Void

    init(row: MacNbaRatingsRow, onOpenDefense: @escaping () -> Void) {
        self.row = row
        self.onOpenDefense = onOpenDefense
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            HStack(spacing: Spacing.md) {
                TeamBadge(abbreviation: row.team, name: row.teamName, size: .large)
                Text(row.teamName)
                    .hardwoodText(.sectionTitle)
                    .lineLimit(2)
                Spacer(minLength: 0)
            }
            VStack(alignment: .leading, spacing: Spacing.xs) {
                DetailLine(label: "Record", value: row.record.text)
                DetailLine(label: "Offensive rating", value: row.offensive.text)
                DetailLine(label: "Defensive rating", value: row.defensive.text)
                DetailLine(label: "Net rating", value: row.net.text)
                DetailLine(label: "Pace", value: row.pace.text)
                DetailLine(label: "Allows per game", value: row.pointsAllowed.text)
            }
            Button("Open Defence") {
                onOpenDefense()
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
#endif
