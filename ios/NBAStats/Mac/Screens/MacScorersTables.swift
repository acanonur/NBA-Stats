#if os(macOS)
import SwiftUI

// The Scorers tables: the EuroLeague's Round Scorers (each club's top scorers for a round, as in the
// workbook's R3 Scorers sheet) and the NBA's Projected Scorers (each player's next-game points, with
// the season average beside it).
//
// WHAT A ROW IS
// Every number is one the server sent, in a `MacStatValue` (formatted text, a sort key, a tooltip).
// A projection is one player's projected points and nothing else: there is no comparison with an
// outside number and no likelihood anywhere. A missing number is an em dash and sorts below
// every real one; a missing status is an em dash too, because the workbook marked everyone it did
// not list as available and Hardwood shows a status only when a source reported one.
//
// ORDER
// Both tables start in the order the server sent. For the EuroLeague that is club by club; for the
// NBA it is the server's own ranking of the projections (the ones that differ most from the player's
// season average, in units of their own spread, come first). A column header re-sorts by the value
// it shows.

// MARK: - EuroLeague rows

/// One projected scorer of a round.
struct MacScorerRow: Identifiable, Hashable {
    let id: String
    let order: Int
    let personCode: String
    let clubCode: String
    let club: String
    let clubName: String
    let player: String
    let position: String
    let jersey: String
    let gameId: String
    let gameText: String
    let status: String
    let statusLabel: String
    let statusSort: Int
    let chance: MacStatValue
    let modelPoints: MacStatValue
    let formAverage: MacStatValue
    let formGames: MacStatValue
    let projection: MacStatValue
    let projectedMinutes: MacStatValue
    let recent: [Double?]

    static let copyHeader: [String] = [
        "Club", "Player", "Game", "Status", "Chance", "Model pts", "Form avg", "Form games", "Projection"
    ]

    var copyCells: [String] {
        let statusText: String = status.isEmpty ? Formatting.emDash : statusLabel
        return [club, player, gameText, statusText, chance.text, modelPoints.text, formAverage.text,
                formGames.text, projection.text]
    }
}

enum MacScorerRows {

    /// One row per scorer, club by club, in the order the server sent them.
    static func rows(from scorers: ElRoundScorers?) -> [MacScorerRow] {
        guard let clubs = scorers?.clubs else { return [] }
        var result: [MacScorerRow] = []
        var index = 0
        for club in clubs {
            let players: [ElScorer] = club.players ?? []
            for scorer in players {
                result.append(make(scorer, club: club, index: index))
                index += 1
            }
        }
        return result
    }

    private static func make(_ scorer: ElScorer, club: ElScorerClub, index: Int) -> MacScorerRow {
        let team: LeagueTeamRef? = club.team
        let person: LeaguePlayerRef? = scorer.player
        let code: String = person?.personCode ?? person?.id ?? ("row-" + String(index))
        let clubCode: String = team?.clubCode ?? team?.id ?? ""
        let status: String = MacWorkbookStatus.key(scorer.status)
        return MacScorerRow(
            id: clubCode + "-" + code,
            order: index,
            personCode: code,
            clubCode: clubCode,
            club: team?.displayAbbr ?? Formatting.emDash,
            clubName: team?.displayName ?? Formatting.emDash,
            player: person?.name ?? Formatting.emDash,
            position: person?.positionRaw ?? person?.position ?? "",
            jersey: person?.jersey ?? "",
            gameId: club.game?.gameId ?? "",
            gameText: gameText(club),
            status: status,
            statusLabel: LeagueFormatting.statusWord(scorer.status),
            statusSort: MacWorkbookStatus.order(scorer.status),
            chance: MacStatValue(fraction: scorer.chanceOfPlaying, places: 0),
            modelPoints: MacStatValue(number: scorer.modelPoints, places: 1),
            formAverage: MacStatValue(number: scorer.formAverage, places: 1),
            formGames: MacStatValue(whole: scorer.formGames),
            projection: MacStatValue(number: scorer.projectedPoints, places: 1),
            projectedMinutes: MacStatValue(number: scorer.projectedMinutes, places: 1),
            recent: scorer.recentPoints ?? []
        )
    }

    /// `v ZZP (H)`: the opponent and whether the club is at home, away or on a neutral floor.
    static func gameText(_ club: ElScorerClub) -> String {
        guard let game = club.game else { return Formatting.emDash }
        let teamKey: String? = club.team?.id ?? club.team?.clubCode
        let homeKey: String? = game.home?.id ?? game.home?.clubCode
        let isHome: Bool = homeKey == teamKey
        let opponent: LeagueTeamRef? = isHome ? game.away : game.home
        let mark: String = LeagueFormatting.venueMark(isHome: isHome, isNeutral: game.isNeutral)
        let side: String
        if game.isNeutral == true {
            side = "N"
        } else if isHome {
            side = "H"
        } else {
            side = "A"
        }
        return mark + " " + (opponent?.displayAbbr ?? Formatting.emDash) + " (" + side + ")"
    }

    /// `L1 11 · L2 14 · L3 DNP`: the last games' points, newest first, with DNP for a game the
    /// player did not play.
    static func recentText(_ values: [Double?]) -> String {
        var parts: [String] = []
        var index = 1
        for value in values {
            let words: String
            if let points = value {
                words = LeagueFormatting.number(points, places: 0)
            } else {
                words = "DNP"
            }
            parts.append("L" + String(index) + " " + words)
            index += 1
        }
        return parts.joined(separator: " · ")
    }

    /// The rows left after the club filter. A nil club keeps every club.
    static func filtered(_ rows: [MacScorerRow], clubCode: String?) -> [MacScorerRow] {
        guard let wanted = clubCode, !wanted.isEmpty else { return rows }
        return rows.filter { $0.clubCode == wanted }
    }
}

/// A projection, in bold: the number a reader came for.
struct MacProjectionCell: View {

    private let value: MacStatValue

    init(value: MacStatValue) {
        self.value = value
    }

    var body: some View {
        Text(value.text)
            .font(Typography.tableCell)
            .fontWeight(.semibold)
            .foregroundStyle(Palette.textPrimary)
            .lineLimit(1)
            .frame(maxWidth: .infinity, alignment: .trailing)
            .help(value.help)
    }
}

// MARK: - EuroLeague table

/// Round Scorers (9 columns).
struct MacScorersTable: View {

    private let rows: [MacScorerRow]
    @Binding private var selection: MacScorerRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacScorerRow>]
    private let onShowDetails: (MacScorerRow.ID) -> Void
    private let onOpenMatchup: (MacScorerRow.ID) -> Void

    init(rows: [MacScorerRow],
         selection: Binding<MacScorerRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacScorerRow>]>,
         onShowDetails: @escaping (MacScorerRow.ID) -> Void,
         onOpenMatchup: @escaping (MacScorerRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onShowDetails = onShowDetails
        self.onOpenMatchup = onOpenMatchup
    }

    private var sortedRows: [MacScorerRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Club", value: \MacScorerRow.club) { row in
                TeamCell(abbr: row.club, name: row.clubName)
            }
            .width(min: 64, ideal: 78, max: 110)
            TableColumn("Player", value: \MacScorerRow.player) { row in
                TextCell(text: row.player)
            }
            .width(min: 140, ideal: 190)
            TableColumn("Game", value: \MacScorerRow.gameText) { row in
                TextCell(text: row.gameText)
            }
            .width(min: 80, ideal: 104, max: 140)
            TableColumn("Status", value: \MacScorerRow.statusSort) { row in
                MacOptionalStatusCell(status: row.status, label: row.statusLabel)
            }
            .width(min: 100, ideal: 124, max: 160)
            TableColumn("Chance", value: \MacScorerRow.chance.sort) { row in
                MacStatCell(value: row.chance)
            }
            .width(min: 52, ideal: 62, max: 84)
            TableColumn("Model pts", value: \MacScorerRow.modelPoints.sort) { row in
                MacStatCell(value: row.modelPoints)
            }
            .width(min: 64, ideal: 78, max: 100)
            TableColumn("Form avg", value: \MacScorerRow.formAverage.sort) { row in
                MacStatCell(value: row.formAverage)
            }
            .width(min: 60, ideal: 72, max: 96)
            TableColumn("Form games", value: \MacScorerRow.formGames.sort) { row in
                MacStatCell(value: row.formGames)
            }
            .width(min: 72, ideal: 84, max: 110)
            TableColumn("Projection", value: \MacScorerRow.projection.sort) { row in
                MacProjectionCell(value: row.projection)
            }
            .width(min: 72, ideal: 84, max: 110)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacScorerRow.ID.self) { ids in
            menuItems(for: ids)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacScorerRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Show Details") {
                onShowDetails(id)
            }
            if !row.gameId.isEmpty {
                Button("Open Matchup") {
                    onOpenMatchup(id)
                }
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacScorerRow.copyHeader, rows: [row.copyCells])
            }
        }
    }
}

// MARK: - NBA rows

/// What an NBA availability report said about a player.
struct MacNbaStatusInfo: Hashable {
    let status: String
    let label: String
}

/// One player's next-game points from the projection board.
struct MacNbaScorerRow: Identifiable, Hashable {
    let id: String
    let order: Int
    let playerRef: PlayerRef
    let player: String
    let team: String
    let matchup: String
    let gameId: String
    let metricLabel: String
    let projection: MacStatValue
    let range: MacStatValue
    let seasonAverage: MacStatValue
    let change: MacStatValue
    let projectedMinutes: MacStatValue
    let status: String
    let statusLabel: String
    let statusSort: Int

    static let copyHeader: [String] = [
        "Player", "Team", "Matchup", "Projection", "Range", "Season avg", "Change vs season",
        "Proj min", "Status"
    ]

    var copyCells: [String] {
        let statusText: String = status.isEmpty ? Formatting.emDash : statusLabel
        return [player, team, matchup, projection.text, range.text, seasonAverage.text, change.text,
                projectedMinutes.text, statusText]
    }
}

enum MacNbaScorerRows {

    /// One row per board row, in the server's own order.
    static func rows(from board: ProjectionBoardPayload?, statuses: [Int: MacNbaStatusInfo]) -> [MacNbaScorerRow] {
        guard let source = board?.rows else { return [] }
        var result: [MacNbaScorerRow] = []
        var index = 0
        for entry in source {
            result.append(make(entry, index: index, statuses: statuses))
            index += 1
        }
        return result
    }

    private static func make(_ entry: ProjectionBoardRow,
                             index: Int,
                             statuses: [Int: MacNbaStatusInfo]) -> MacNbaScorerRow {
        let info: MacNbaStatusInfo? = statuses[entry.player.playerId]
        return MacNbaScorerRow(
            id: entry.id,
            order: index,
            playerRef: entry.player,
            player: entry.player.name,
            team: entry.player.teamAbbr ?? Formatting.emDash,
            matchup: entry.matchup ?? Formatting.emDash,
            gameId: entry.gameId ?? "",
            metricLabel: entry.metricLabel,
            projection: MacStatValue(words: entry.displayValue, sort: LeagueFormatting.sortKey(entry.projection)),
            range: rangeValue(entry),
            seasonAverage: MacStatValue(words: entry.referenceText ?? Formatting.emDash,
                                        sort: LeagueFormatting.sortKey(entry.referenceValue)),
            change: MacStatValue(words: entry.deltaText ?? Formatting.emDash,
                                 sort: LeagueFormatting.sortKey(entry.delta)),
            projectedMinutes: MacStatValue(number: entry.projectedMinutes, places: 1),
            status: info?.status ?? "",
            statusLabel: info?.label ?? "",
            statusSort: MacWorkbookStatus.order(info?.status)
        )
    }

    /// `14–31`, sorted by its upper end.
    private static func rangeValue(_ entry: ProjectionBoardRow) -> MacStatValue {
        guard entry.low != nil, entry.high != nil else { return MacStatValue.missing }
        return MacStatValue(words: entry.lowText + "–" + entry.highText,
                            sort: LeagueFormatting.sortKey(entry.high),
                            help: "The 80% range of this projection")
    }

    /// Player id to status, from the NBA availability report. An entry that no longer drives the
    /// projection is skipped, so a retired report never decides the status.
    static func statuses(from report: LeagueAvailabilityReport?) -> [Int: MacNbaStatusInfo] {
        var result: [Int: MacNbaStatusInfo] = [:]
        let teams: [LeagueAvailabilityTeam] = report?.teams ?? []
        for team in teams {
            let entries: [LeagueAvailabilityEntry] = team.entries ?? []
            for entry in entries {
                guard entry.inForce != false, let playerId = entry.player?.playerId else { continue }
                guard let status = entry.status, !status.isEmpty else { continue }
                result[playerId] = MacNbaStatusInfo(status: status, label: LeagueFormatting.statusWord(status))
            }
        }
        return result
    }
}

// MARK: - NBA table

/// Projected Scorers (9 columns).
struct MacNbaScorersTable: View {

    private let rows: [MacNbaScorerRow]
    @Binding private var selection: MacNbaScorerRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacNbaScorerRow>]
    private let onShowDetails: (MacNbaScorerRow.ID) -> Void
    private let onOpenMatchup: (MacNbaScorerRow.ID) -> Void

    init(rows: [MacNbaScorerRow],
         selection: Binding<MacNbaScorerRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacNbaScorerRow>]>,
         onShowDetails: @escaping (MacNbaScorerRow.ID) -> Void,
         onOpenMatchup: @escaping (MacNbaScorerRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onShowDetails = onShowDetails
        self.onOpenMatchup = onOpenMatchup
    }

    private var sortedRows: [MacNbaScorerRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Player", value: \MacNbaScorerRow.player) { row in
                TextCell(text: row.player)
            }
            .width(min: 140, ideal: 180)
            TableColumn("Team", value: \MacNbaScorerRow.team) { row in
                TeamCell(abbr: row.team, name: row.playerRef.teamAbbr)
            }
            .width(min: 64, ideal: 78, max: 110)
            TableColumn("Matchup", value: \MacNbaScorerRow.matchup) { row in
                TextCell(text: row.matchup)
            }
            .width(min: 84, ideal: 104, max: 140)
            TableColumn("Projection", value: \MacNbaScorerRow.projection.sort) { row in
                MacProjectionCell(value: row.projection)
            }
            .width(min: 72, ideal: 84, max: 110)
            TableColumn("Range", value: \MacNbaScorerRow.range.sort) { row in
                MacStatCell(value: row.range)
            }
            .width(min: 64, ideal: 78, max: 100)
            TableColumn("Season avg", value: \MacNbaScorerRow.seasonAverage.sort) { row in
                MacStatCell(value: row.seasonAverage)
            }
            .width(min: 72, ideal: 84, max: 110)
            TableColumn("Change vs season", value: \MacNbaScorerRow.change.sort) { row in
                MacStatCell(value: row.change)
            }
            .width(min: 100, ideal: 120, max: 150)
            TableColumn("Proj min", value: \MacNbaScorerRow.projectedMinutes.sort) { row in
                MacStatCell(value: row.projectedMinutes)
            }
            .width(min: 60, ideal: 72, max: 96)
            TableColumn("Status", value: \MacNbaScorerRow.statusSort) { row in
                MacOptionalStatusCell(status: row.status, label: row.statusLabel)
            }
            .width(min: 100, ideal: 124, max: 160)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacNbaScorerRow.ID.self) { ids in
            menuItems(for: ids)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacNbaScorerRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Show Details") {
                onShowDetails(id)
            }
            if !row.gameId.isEmpty {
                Button("Open Matchup") {
                    onOpenMatchup(id)
                }
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacNbaScorerRow.copyHeader, rows: [row.copyCells])
            }
        }
    }
}

// MARK: - Inspectors

/// The EuroLeague scorer's card: who, how available, what the model and the recent games say.
struct MacScorerPanel: View {

    private let row: MacScorerRow
    private let onOpenMatchup: () -> Void

    init(row: MacScorerRow, onOpenMatchup: @escaping () -> Void) {
        self.row = row
        self.onOpenMatchup = onOpenMatchup
    }

    private var recentLine: String {
        row.recent.isEmpty ? "Last five games are not available." : MacScorerRows.recentText(row.recent)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            MacPlayerHeader(name: row.player,
                            detail: MacPlayerDetailText.line(position: row.position,
                                                             jersey: row.jersey,
                                                             team: row.clubName),
                            teamAbbr: row.club,
                            teamName: row.clubName)
            HStack(spacing: Spacing.sm) {
                MacOptionalStatusCell(status: row.status, label: row.statusLabel)
                Spacer(minLength: 0)
            }
            facts
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text("Recent games")
                    .hardwoodText(.statLabel)
                Text(recentLine)
                    .hardwoodText(.tableCell, monospacedDigits: false)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !row.gameId.isEmpty {
                Button("Open Matchup") {
                    onOpenMatchup()
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var facts: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            DetailLine(label: "Game", value: row.gameText)
            DetailLine(label: "Chance of playing", value: row.chance.text)
            DetailLine(label: "Model points", value: row.modelPoints.text)
            DetailLine(label: "Form average", value: row.formAverage.text)
            DetailLine(label: "Form games", value: row.formGames.text)
            DetailLine(label: "Projected minutes", value: row.projectedMinutes.text)
            DetailLine(label: "Projection", value: row.projection.text)
        }
    }
}

/// The NBA scorer's card.
struct MacNbaScorerPanel: View {

    private let row: MacNbaScorerRow
    private let onOpenPlayer: () -> Void
    private let onOpenMatchup: () -> Void

    init(row: MacNbaScorerRow,
         onOpenPlayer: @escaping () -> Void,
         onOpenMatchup: @escaping () -> Void) {
        self.row = row
        self.onOpenPlayer = onOpenPlayer
        self.onOpenMatchup = onOpenMatchup
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            MacPlayerHeader(name: row.player,
                            detail: MacPlayerDetailText.line(position: row.playerRef.position ?? "",
                                                             jersey: row.playerRef.jersey ?? "",
                                                             team: row.matchup),
                            teamAbbr: row.team,
                            teamName: row.playerRef.teamAbbr)
            HStack(spacing: Spacing.sm) {
                MacOptionalStatusCell(status: row.status, label: row.statusLabel)
                Spacer(minLength: 0)
            }
            facts
            actions
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var facts: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            DetailLine(label: "Statistic", value: row.metricLabel)
            DetailLine(label: "Projection", value: row.projection.text)
            DetailLine(label: "80% range", value: row.range.text)
            DetailLine(label: "Season average", value: row.seasonAverage.text)
            DetailLine(label: "Change vs season", value: row.change.text)
            DetailLine(label: "Projected minutes", value: row.projectedMinutes.text)
        }
    }

    private var actions: some View {
        HStack(spacing: Spacing.sm) {
            Button("Open player") {
                onOpenPlayer()
            }
            if !row.gameId.isEmpty {
                Button("Open Matchup") {
                    onOpenMatchup()
                }
            }
            Spacer(minLength: 0)
        }
    }
}
#endif
