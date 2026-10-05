#if os(macOS)
import SwiftUI

// A box score: the final score and how it was reached, then one team's players in a sortable table
// (two column sets) with the team's totals beneath. Also the content of the Box Score window.
//
// ONE VIEW FOR BOTH LEAGUES
// The EuroLeague's box is `GET /v1/el/games/{id}` (an `ElBoxScore`); the NBA's is
// `GET /v1/games/{id}/box?view=both` (the existing `BoxScoreResponse`). They carry different keys, so
// each has its own row type and its own two tables (MacBoxTables.swift, MacNbaBoxTables.swift); this
// view only picks which to show.
//
// WHAT IT SAYS ABOUT A GAME WITHOUT LINES
// A game that has not been played has no lines, and a EuroLeague game whose box failed the server's
// own checks (`statsStatus` "quarantined": the lines and the final score disagreed) shows its score
// and no lines, with the server's sentence under it. The notes the server sends are printed as they
// arrive; the view never fills a gap with zeros.
//
// HOW IT LOADS (MAC_DESIGN 6.6)
// One `.task(id:)` keyed on the route and the league's reload counter. The host gives the view an
// `.id` of the game, so choosing another game starts from a clean state.

// MARK: - Header facts

/// What the header of a box score says, as text.
struct MacBoxHeaderInfo: Hashable {
    let firstAbbr: String
    let firstName: String
    let secondAbbr: String
    let secondName: String
    let scoreText: String
    let statusText: String
    let detailText: String
    let partialsText: String
    let attendanceText: String

    /// A EuroLeague box: home first, as the league writes a game.
    static func make(from box: ElBoxScore) -> MacBoxHeaderInfo {
        let game: LeagueGameRef? = box.game
        var detailParts: [String] = []
        if let day = game?.date, !day.isEmpty {
            detailParts.append(LeagueFormatting.leagueDate(day))
        }
        if let venue = game?.venue, !venue.isEmpty {
            detailParts.append(venue)
        }
        if game?.isNeutral == true {
            detailParts.append("Neutral venue")
        }
        var attendance = ""
        if let people = box.attendance {
            attendance = "Attendance " + LeagueFormatting.number(people, places: 0)
        }
        let overtime: String = leagueOvertimeText(game?.overtimePeriods)
        var status: String = LeagueFormatting.gameStatusWord(game?.status)
        if !overtime.isEmpty {
            status += " (" + overtime + ")"
        }
        return MacBoxHeaderInfo(
            firstAbbr: game?.home?.displayAbbr ?? Formatting.emDash,
            firstName: game?.home?.displayName ?? Formatting.emDash,
            secondAbbr: game?.away?.displayAbbr ?? Formatting.emDash,
            secondName: game?.away?.displayName ?? Formatting.emDash,
            scoreText: LeagueFormatting.score(game?.homePts, game?.awayPts),
            statusText: status,
            detailText: detailParts.joined(separator: " · "),
            partialsText: partialsText(box.partials),
            attendanceText: attendance
        )
    }

    /// An NBA box: the visitor first, as the NBA writes a game.
    static func make(from box: BoxScoreResponse) -> MacBoxHeaderInfo {
        let game: GameRef = box.game
        var status: String = LeagueFormatting.gameStatusWord(game.statusRaw ?? game.status.rawValue)
        if let period = game.period, period > 4 {
            if period == 5 {
                status += " (OT)"
            } else {
                status += " (" + String(period - 4) + "OT)"
            }
        }
        return MacBoxHeaderInfo(
            firstAbbr: game.away.abbr,
            firstName: game.away.name,
            secondAbbr: game.home.abbr,
            secondName: game.home.name,
            scoreText: LeagueFormatting.score(game.awayPts, game.homePts),
            statusText: status,
            detailText: LeagueFormatting.leagueDate(game.date),
            partialsText: "",
            attendanceText: ""
        )
    }

    /// `Q1 23–17 · Q2 20–25 · Q3 ... · OT 9–8`. A period the server sent for one side only is left out.
    static func partialsText(_ partials: ElPartials?) -> String {
        guard let home = partials?.home, let away = partials?.away else { return "" }
        let count: Int = Swift.min(home.count, away.count)
        var parts: [String] = []
        for index in 0..<count {
            let label: String
            if index < 4 {
                label = "Q" + String(index + 1)
            } else if index == 4 {
                label = "OT"
            } else {
                label = String(index - 3) + "OT"
            }
            parts.append(label + " " + LeagueFormatting.score(home[index], away[index]))
        }
        return parts.joined(separator: " · ")
    }
}

// MARK: - The view

/// Also the content of the Box Score window (`MacBoxScoreWindow`).
struct MacBoxScoreView: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    private let league: LeagueKey
    private let gameId: String

    @State private var elBox: ElBoxScore?
    @State private var nbaBox: BoxScoreResponse?
    @State private var teamIndex = 0
    @State private var columnSet: MacStatColumnSet = .box
    @State private var selection: String?
    @State private var elSortOrder: [KeyPathComparator<MacBoxElRow>] = [
        KeyPathComparator(\MacBoxElRow.order)
    ]
    @State private var nbaSortOrder: [KeyPathComparator<MacBoxNbaRow>] = [
        KeyPathComparator(\MacBoxNbaRow.order)
    ]
    @State private var failure: APIError?
    @State private var isLoading = false
    @State private var loadedRoute: LeagueRoute?

    init(league: LeagueKey, gameId: String) {
        self.league = league
        self.gameId = gameId
    }

    // MARK: Request

    private var route: LeagueRoute {
        switch league {
        case .nba:
            return LeagueRoutes.nbaBox(gameId)
        case .euroleague:
            return LeagueRoutes.elBox(gameId)
        }
    }

    private var loadKey: MacLoadKey {
        MacLoadKey(route: route, generation: model.generation(for: league))
    }

    private var offReason: String? {
        if environment.isDemoMode {
            return nil
        }
        return model.offReason(for: league)
    }

    // MARK: What is loaded

    private var hasPayload: Bool {
        league == .nba ? nbaBox != nil : elBox != nil
    }

    private var header: MacBoxHeaderInfo? {
        switch league {
        case .nba:
            guard let box = nbaBox else { return nil }
            return MacBoxHeaderInfo.make(from: box)
        case .euroleague:
            guard let box = elBox else { return nil }
            return MacBoxHeaderInfo.make(from: box)
        }
    }

    /// The abbreviations on the team switch, in the order the server sent the two teams.
    private var teamLabels: [String] {
        switch league {
        case .nba:
            return (nbaBox?.teams ?? []).map { $0.team.abbr }
        case .euroleague:
            return (elBox?.teams ?? []).map { $0.team?.displayAbbr ?? Formatting.emDash }
        }
    }

    private var elTeam: ElBoxTeam? {
        let teams: [ElBoxTeam] = elBox?.teams ?? []
        if teamIndex >= 0 && teamIndex < teams.count {
            return teams[teamIndex]
        }
        return teams.first
    }

    private var nbaTeam: BoxScoreTeam? {
        let teams: [BoxScoreTeam] = nbaBox?.teams ?? []
        if teamIndex >= 0 && teamIndex < teams.count {
            return teams[teamIndex]
        }
        return teams.first
    }

    private var elRows: [MacBoxElRow] {
        MacBoxElRows.rows(from: elTeam)
    }

    private var nbaRows: [MacBoxNbaRow] {
        MacBoxNbaRows.rows(from: nbaTeam)
    }

    private var rowCount: Int {
        league == .nba ? nbaRows.count : elRows.count
    }

    private var totals: [MacBoxTotal] {
        switch league {
        case .nba:
            return MacBoxNbaRows.totals(from: nbaTeam, set: columnSet)
        case .euroleague:
            return MacBoxElRows.totals(from: elTeam, set: columnSet)
        }
    }

    private var totalsTitle: String {
        switch league {
        case .nba:
            return "Team totals · " + (nbaTeam?.team.abbr ?? Formatting.emDash)
        case .euroleague:
            return "Team totals · " + (elTeam?.team?.displayAbbr ?? Formatting.emDash)
        }
    }

    private var notes: [String] {
        league == .nba ? [] : (elBox?.notes ?? [])
    }

    private var freshness: LeagueFreshness? {
        elBox?.freshness
    }

    // MARK: Body

    var body: some View {
        content
            .task(id: loadKey) {
                await load()
            }
    }

    @ViewBuilder private var content: some View {
        if let reason = offReason {
            MacLeagueOffView(league: league, reason: reason)
        } else if !hasPayload, let problem = failure {
            MacLoadFailureView(error: problem, league: league) {
                retry()
            }
        } else if !hasPayload {
            MacLoadingView()
        } else {
            loaded
        }
    }

    private var loaded: some View {
        VStack(spacing: 0) {
            headerView
            Divider()
            controls
            tableArea
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            MacBoxTotalsGrid(title: totalsTitle, totals: totals)
            FreshnessBar(freshness: freshness, isBundledDemo: environment.isDemoMode)
            NotesFooter(notes: notes, attribution: nil)
        }
    }

    // MARK: Header

    @ViewBuilder private var headerView: some View {
        if let info = header {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                HStack(spacing: Spacing.md) {
                    TeamBadge(abbreviation: info.firstAbbr, name: info.firstName, size: .large)
                    Text(info.scoreText)
                        .hardwoodText(.displayValue)
                    TeamBadge(abbreviation: info.secondAbbr, name: info.secondName, size: .large)
                    VStack(alignment: .leading, spacing: Spacing.xxs) {
                        Text(info.statusText)
                            .hardwoodText(.widgetTitle, color: Palette.textPrimary)
                        Text(info.detailText)
                            .hardwoodText(.caption)
                            .lineLimit(2)
                    }
                    Spacer(minLength: 0)
                }
                headerExtras(info)
            }
            .padding(Spacing.md)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder private func headerExtras(_ info: MacBoxHeaderInfo) -> some View {
        if !info.partialsText.isEmpty {
            Text(info.partialsText)
                .hardwoodText(.tableCell, monospacedDigits: true)
        }
        if !info.attendanceText.isEmpty {
            Text(info.attendanceText)
                .hardwoodText(.caption)
        }
    }

    // MARK: Controls

    private var controls: some View {
        HStack(spacing: Spacing.md) {
            Picker("Team", selection: $teamIndex) {
                ForEach(Array(teamLabels.enumerated()), id: \.offset) { pair in
                    Text(pair.element).tag(pair.offset)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 200)
            Picker("Columns", selection: $columnSet) {
                ForEach(MacStatColumnSet.allCases) { option in
                    Text(option.title).tag(option)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 170)
            Spacer(minLength: 0)
            MacCopyTableButton(header: copyHeader, rows: copyRows)
        }
        .padding(.horizontal, Spacing.md)
        .padding(.vertical, Spacing.sm)
    }

    private var copyHeader: [String] {
        league == .nba ? MacBoxNbaRow.copyHeader : MacBoxElRow.copyHeader
    }

    private var copyRows: [[String]] {
        switch league {
        case .nba:
            let shown: [MacBoxNbaRow] = nbaRows.sorted(using: nbaSortOrder)
            return shown.map { $0.copyCells }
        case .euroleague:
            let shown: [MacBoxElRow] = elRows.sorted(using: elSortOrder)
            return shown.map { $0.copyCells }
        }
    }

    // MARK: Table

    @ViewBuilder private var tableArea: some View {
        if rowCount == 0 {
            ContentUnavailableView("No player statistics",
                                   systemImage: "sportscourt",
                                   description: Text(noLinesText))
        } else {
            boxTable
        }
    }

    private var noLinesText: String {
        if let first = notes.first(where: { $0.contains("box score") }) {
            return first
        }
        return "The server has no player statistics for this game."
    }

    @ViewBuilder private var boxTable: some View {
        switch league {
        case .euroleague:
            elTable
        case .nba:
            nbaTable
        }
    }

    @ViewBuilder private var elTable: some View {
        switch columnSet {
        case .box:
            MacBoxElBoxTable(rows: elRows, selection: $selection, sortOrder: $elSortOrder)
        case .shooting:
            MacBoxElShootingTable(rows: elRows, selection: $selection, sortOrder: $elSortOrder)
        }
    }

    @ViewBuilder private var nbaTable: some View {
        switch columnSet {
        case .box:
            MacBoxNbaBoxTable(rows: nbaRows, selection: $selection, sortOrder: $nbaSortOrder)
        case .shooting:
            MacBoxNbaShootingTable(rows: nbaRows, selection: $selection, sortOrder: $nbaSortOrder)
        }
    }

    // MARK: Loading

    private func retry() {
        Task {
            await load()
        }
    }

    private func load() async {
        if offReason != nil {
            return
        }
        let wanted: LeagueRoute = route
        if loadedRoute != wanted {
            elBox = nil
            nbaBox = nil
            failure = nil
        }
        isLoading = true
        do {
            switch league {
            case .euroleague:
                let result = try await environment.league.get(ElBoxScore.self, wanted)
                if Task.isCancelled { return }
                elBox = result
            case .nba:
                let result = try await environment.league.get(BoxScoreResponse.self, wanted)
                if Task.isCancelled { return }
                nbaBox = result
            }
            loadedRoute = wanted
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
        }
        isLoading = false
    }
}
#endif
