#if os(macOS)
import SwiftUI

// Season Stats: every player's season line, sortable by any column, with the player's game log (and
// a way to open the full NBA player screen) in the inspector.
//
// ONE SCREEN, TWO WAYS TO GET A SEASON
// The EuroLeague serves a stats table (`GET /v1/el/stats/players`, up to 500 rows in one answer,
// per game, as totals or per 40 minutes). The NBA has no such route, so the screen builds the same
// table from what does exist: the roster of each of the thirty teams, each carrying the player
// metrics asked for (`GET /v1/teams/{id}?rosterMetrics=...`). Those thirty requests go out four at a
// time, the rows appear as each batch lands, and the progress is on screen. A team whose request
// fails is left out and said so, never filled with zeros.
//
// WHAT THE APP DOES WITH THE ROWS
// It filters (club or team, minimum games, a name), sorts by the column a reader clicks, and
// formats. It computes no average and no rank. The EuroLeague's per-game, totals and per-40 modes
// are three different requests, so the server does the arithmetic; the column sets (Box, Shooting)
// are only a choice of which ten columns fit on screen, and "Copy Table" copies all of them.
//
// A NOTE ON "MINIMUM GAMES"
// The server's own default is one game (a player who never played is not a row). The stepper goes
// from 0 and starts at 1. The NBA table has no such server filter, so its stepper does not appear.

struct MacSeasonStatsScreen: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    private let league: LeagueKey

    @State private var perMode = "PerGame"
    @State private var columnSet: MacStatColumnSet = .box
    @State private var teamFilter: String?
    @State private var minGames = 1
    @State private var nameFilter = ""
    @State private var selection: String?
    @State private var elSortOrder: [KeyPathComparator<MacElStatRow>] = [
        KeyPathComparator(\MacElStatRow.points.sort, order: .reverse)
    ]
    @State private var nbaSortOrder: [KeyPathComparator<MacNbaStatRow>] = [
        KeyPathComparator(\MacNbaStatRow.points.sort, order: .reverse)
    ]
    @State private var elTable: ElPlayerStatsTable?
    @State private var nbaTeamsLoaded: [TeamDetailResponse] = []
    @State private var nbaProgress = 0
    @State private var nbaTeamCount = 0
    @State private var nbaNote: String?
    @State private var failure: APIError?
    @State private var isLoading = false
    @State private var loadedRoute: LeagueRoute?
    @State private var playerSheet: PlayerRef?

    init(league: LeagueKey) {
        self.league = league
    }

    // MARK: Request

    private var route: LeagueRoute {
        switch league {
        case .nba:
            return LeagueRoutes.nbaTeams()
        case .euroleague:
            return LeagueRoutes.elPlayerStats(perMode: perMode,
                                              club: teamFilter,
                                              minGames: minGames,
                                              limit: 500)
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

    // MARK: Rows

    private var elRows: [MacElStatRow] {
        let all: [MacElStatRow] = MacElStatRows.rows(from: elTable, perMode: elTable?.perMode ?? perMode)
        return MacElStatRows.filtered(all, name: nameFilter)
    }

    private var nbaRows: [MacNbaStatRow] {
        let all: [MacNbaStatRow] = MacNbaStatRows.rows(from: nbaTeamsLoaded)
        let wanted: Int? = teamFilter.flatMap { Int($0) }
        return MacNbaStatRows.filtered(all, teamId: wanted, name: nameFilter)
    }

    private var hasData: Bool {
        switch league {
        case .nba:
            return !nbaTeamsLoaded.isEmpty
        case .euroleague:
            return elTable != nil
        }
    }

    private var shownCount: Int {
        switch league {
        case .nba:
            return nbaRows.count
        case .euroleague:
            return elRows.count
        }
    }

    private var freshness: LeagueFreshness? {
        elTable?.freshness
    }

    private var notes: [String]? {
        var all: [String] = elTable?.notes ?? []
        if let extra = nbaNote {
            all.append(extra)
        }
        return all
    }

    private var attribution: String? {
        league == .euroleague ? model.elMeta?.attribution : nil
    }

    private var selectedElRow: MacElStatRow? {
        guard let id = selection else { return nil }
        return elRows.first { $0.id == id }
    }

    private var selectedNbaRow: MacNbaStatRow? {
        guard let id = selection else { return nil }
        return nbaRows.first { $0.id == id }
    }

    // MARK: Body

    var body: some View {
        VStack(spacing: 0) {
            MacFilterBar {
                filters
            }
            FreshnessBar(freshness: freshness, isBundledDemo: environment.isDemoMode)
            staleStrip
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            NotesFooter(notes: notes, attribution: attribution)
        }
        .macScreenTitle(.seasonStats, league: league, dataThrough: freshness?.dataThrough)
        .toolbar {
            toolbarContent
        }
        .inspector(isPresented: $model.isInspectorShown) {
            inspectorContent
        }
        .task(id: loadKey) {
            await refresh()
        }
        .onChange(of: columnSet) { _, newValue in
            elSortOrder = MacSeasonStatsScreen.defaultElSort(for: newValue)
            nbaSortOrder = MacSeasonStatsScreen.defaultNbaSort(for: newValue)
        }
        .sheet(item: $playerSheet) { player in
            MacNbaPlayerSheet(player: player)
                .environmentObject(environment)
        }
    }

    /// Each column set starts sorted by a column it shows, best first: points in Box, PIR in
    /// Shooting (the EuroLeague) and true shooting in Shooting (the NBA).
    private static func defaultElSort(for set: MacStatColumnSet) -> [KeyPathComparator<MacElStatRow>] {
        switch set {
        case .box:
            return [KeyPathComparator(\MacElStatRow.points.sort, order: .reverse)]
        case .shooting:
            return [KeyPathComparator(\MacElStatRow.pir.sort, order: .reverse)]
        }
    }

    private static func defaultNbaSort(for set: MacStatColumnSet) -> [KeyPathComparator<MacNbaStatRow>] {
        switch set {
        case .box:
            return [KeyPathComparator(\MacNbaStatRow.points.sort, order: .reverse)]
        case .shooting:
            return [KeyPathComparator(\MacNbaStatRow.trueShootingPct.sort, order: .reverse)]
        }
    }

    // MARK: Filters

    @ViewBuilder private var filters: some View {
        switch league {
        case .nba:
            nbaFilters
        case .euroleague:
            elFilters
        }
    }

    private var elFilters: some View {
        HStack(spacing: Spacing.md) {
            Picker("Per", selection: $perMode) {
                Text("Per game").tag("PerGame")
                Text("Totals").tag("Totals")
                Text("Per 40").tag("Per40")
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 230)
            columnPicker
            MacTeamPicker(league: league, title: "Club", selection: $teamFilter, noneLabel: "All clubs")
            Stepper("Min games: " + String(minGames), value: $minGames, in: 0...40)
            nameField
            countText
            loadingIndicator
        }
    }

    private var nbaFilters: some View {
        HStack(spacing: Spacing.md) {
            columnPicker
            MacTeamPicker(league: league, title: "Team", selection: $teamFilter, noneLabel: "All teams")
            nameField
            countText
            loadingIndicator
        }
    }

    private var columnPicker: some View {
        Picker("Columns", selection: $columnSet) {
            ForEach(MacStatColumnSet.allCases) { option in
                Text(option.title).tag(option)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .frame(width: 170)
    }

    private var nameField: some View {
        TextField("Filter by name", text: $nameFilter)
            .textFieldStyle(.roundedBorder)
            .frame(width: 180)
    }

    @ViewBuilder private var countText: some View {
        if hasData {
            Text(String(shownCount) + (shownCount == 1 ? " player" : " players"))
                .hardwoodText(.caption)
        }
    }

    @ViewBuilder private var loadingIndicator: some View {
        if isLoading {
            ProgressView()
                .controlSize(.small)
        }
    }

    // MARK: Content

    @ViewBuilder private var staleStrip: some View {
        if let problem = failure, hasData {
            MacStaleStrip(message: problem.userMessage) {
                retry()
            }
        }
    }

    @ViewBuilder private var content: some View {
        if let reason = offReason {
            MacLeagueOffView(league: league, reason: reason)
        } else if !hasData, let problem = failure {
            MacLoadFailureView(error: problem, league: league) {
                retry()
            }
        } else if !hasData {
            waiting
        } else if shownCount == 0 {
            ContentUnavailableView("No players match",
                                   systemImage: "tablecells",
                                   description: Text("Change the filters to see more players."))
        } else {
            statsTable
        }
    }

    @ViewBuilder private var waiting: some View {
        if league == .nba && nbaTeamCount > 0 {
            VStack(spacing: Spacing.sm) {
                ProgressView()
                Text(verbatim: "Loading rosters \(nbaProgress) of \(nbaTeamCount)")
                    .hardwoodText(.caption)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            MacLoadingView()
        }
    }

    @ViewBuilder private var statsTable: some View {
        switch league {
        case .euroleague:
            elStatsTable
        case .nba:
            nbaStatsTable
        }
    }

    @ViewBuilder private var elStatsTable: some View {
        switch columnSet {
        case .box:
            MacElStatBoxTable(rows: elRows,
                              selection: $selection,
                              sortOrder: $elSortOrder,
                              onShowDetails: { id in showDetails(id) })
        case .shooting:
            MacElStatShootingTable(rows: elRows,
                                   selection: $selection,
                                   sortOrder: $elSortOrder,
                                   onShowDetails: { id in showDetails(id) })
        }
    }

    @ViewBuilder private var nbaStatsTable: some View {
        switch columnSet {
        case .box:
            MacNbaStatBoxTable(rows: nbaRows,
                               selection: $selection,
                               sortOrder: $nbaSortOrder,
                               onShowDetails: { id in showDetails(id) })
        case .shooting:
            MacNbaStatShootingTable(rows: nbaRows,
                                    selection: $selection,
                                    sortOrder: $nbaSortOrder,
                                    onShowDetails: { id in showDetails(id) })
        }
    }

    private func showDetails(_ id: String) {
        selection = id
        model.isInspectorShown = true
    }

    // MARK: Toolbar and inspector

    private var copyHeader: [String] {
        league == .euroleague ? MacElStatRow.copyHeader : MacNbaStatRow.copyHeader
    }

    /// The rows in the order the table shows them, as text.
    private var copyRows: [[String]] {
        switch league {
        case .euroleague:
            let shown: [MacElStatRow] = elRows.sorted(using: elSortOrder)
            return shown.map { $0.copyCells }
        case .nba:
            let shown: [MacNbaStatRow] = nbaRows.sorted(using: nbaSortOrder)
            return shown.map { $0.copyCells }
        }
    }

    @ToolbarContentBuilder private var toolbarContent: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            MacCopyTableButton(header: copyHeader, rows: copyRows)
            MacInspectorToggle()
        }
    }

    private var inspectorContent: some View {
        inspectorBody
            .inspectorColumnWidth(min: 280, ideal: 340, max: 460)
    }

    @ViewBuilder private var inspectorBody: some View {
        if league == .euroleague, let row = selectedElRow {
            ScrollView {
                MacElPlayerPanel(row: row, perMode: elTable?.perMode ?? perMode)
                    .padding(Spacing.lg)
            }
        } else if league == .nba, let row = selectedNbaRow {
            ScrollView {
                MacNbaPlayerPanel(row: row) {
                    playerSheet = row.playerRef
                }
                .padding(Spacing.lg)
            }
        } else {
            ContentUnavailableView("Select a player",
                                   systemImage: "person",
                                   description: Text("Choose a row to see the player's season and games."))
        }
    }

    // MARK: Loading

    private func retry() {
        Task {
            await refresh()
        }
    }

    private func refresh() async {
        if offReason != nil {
            return
        }
        switch league {
        case .euroleague:
            await loadEuroLeague(route)
        case .nba:
            await loadNba(route)
        }
    }

    private func loadEuroLeague(_ route: LeagueRoute) async {
        if loadedRoute != route {
            elTable = nil
            failure = nil
        }
        isLoading = true
        do {
            let result = try await environment.league.get(ElPlayerStatsTable.self, route)
            if Task.isCancelled { return }
            elTable = result
            loadedRoute = route
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
        }
        isLoading = false
    }

    /// The thirty rosters, four teams at a time, with the rows growing as each batch arrives.
    private func loadNba(_ route: LeagueRoute) async {
        if loadedRoute != route {
            nbaTeamsLoaded = []
            nbaNote = nil
            failure = nil
        }
        isLoading = true
        nbaProgress = 0
        let teamIds: [Int]
        do {
            teamIds = try await nbaTeamIds()
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
            isLoading = false
            return
        }
        if Task.isCancelled { return }
        nbaTeamCount = teamIds.count

        let client: LeagueClient = environment.league
        // On the first load the table grows as batches arrive. On a refresh the old rows stay put
        // until the new ones are all in, so the table never shrinks to four teams and grows back.
        let showsProgress: Bool = nbaTeamsLoaded.isEmpty
        var collected: [TeamDetailResponse] = []
        var firstFailure: APIError?
        var skipped = 0
        var start = 0
        while start < teamIds.count {
            let end: Int = Swift.min(start + 4, teamIds.count)
            let batch: [Int] = Array(teamIds[start..<end])
            let outcomes: [MacRosterOutcome] = await MacRosterFetch.fetch(batch,
                                                                         client: client,
                                                                         keys: NbaRosterKeys.seasonStats)
            if Task.isCancelled { return }
            for outcome in outcomes {
                if let detail = outcome.detail {
                    collected.append(detail)
                } else {
                    skipped += 1
                    if firstFailure == nil {
                        firstFailure = outcome.failure
                    }
                }
            }
            start = end
            nbaProgress = end
            if showsProgress {
                nbaTeamsLoaded = collected
            }
        }
        finishNba(route, collected: collected, skipped: skipped, firstFailure: firstFailure)
    }

    private func finishNba(_ route: LeagueRoute,
                           collected: [TeamDetailResponse],
                           skipped: Int,
                           firstFailure: APIError?) {
        if collected.isEmpty, let problem = firstFailure {
            failure = problem
        } else {
            nbaTeamsLoaded = collected
            failure = nil
            loadedRoute = route
        }
        if skipped > 0 && !collected.isEmpty {
            nbaNote = String(skipped) + " of " + String(nbaTeamCount) + " teams could not be loaded and are missing from this table."
        } else {
            nbaNote = nil
        }
        isLoading = false
    }

    private func nbaTeamIds() async throws -> [Int] {
        let known: [Int] = model.nbaTeams.map { $0.teamId }
        if !known.isEmpty {
            return known
        }
        let response = try await environment.client.teams()
        return response.teams.map { $0.teamId }
    }
}
#endif
