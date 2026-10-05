#if os(macOS)
import SwiftUI

// Team Ratings: how good each team is, by the numbers the server holds, in one sortable table.
//
// EuroLeague (the workbook's Team Ratings sheet): record, points scored and allowed per game, and
// the model's attack and defence indices (`GET /v1/el/teams`), with the full rating of the selected
// club in the inspector (`GET /v1/el/ratings`: priors, adjustments, projected points, and the games
// that moved it). NBA ("Team Ratings, efficiency"): record, offensive, defensive and net rating and
// pace, through the dashboard's `team_efficiency` resolver, with points allowed per game joined in
// from the defence table.
//
// WHAT THE NUMBERS MEAN, SAID ON SCREEN
// The defence index is points allowed relative to the league's, so a HIGHER index is a WORSE
// defence; the caption says so. A ratings table that is sorted descending would otherwise put the
// worst defences first and read as a ranking of the best.
//
// WHAT THE APP DOES NOT DO
// It sorts by a served value when a header is clicked. It computes no rating, no difference and no
// rank. The second request of each league (the club ratings, the defence table) only adds columns
// and a card; if it fails the table is still complete, and the missing parts are em dashes.

struct MacTeamRatingsScreen: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel
    @Environment(\.openWindow) private var openWindow

    private let league: LeagueKey

    @State private var selection: String?
    @State private var elSortOrder: [KeyPathComparator<MacRatingsRow>] = [
        KeyPathComparator(\MacRatingsRow.order)
    ]
    @State private var nbaSortOrder: [KeyPathComparator<MacNbaRatingsRow>] = [
        KeyPathComparator(\MacNbaRatingsRow.order)
    ]
    @State private var elTeams: ElTeamsTable?
    @State private var elRatings: ElRatingsTable?
    @State private var nbaEfficiency: TeamEfficiencyPayload?
    @State private var nbaDefense: LeagueDefenseDocument?
    @State private var nbaNotes: [String] = []
    @State private var nbaMessage: String?
    @State private var failure: APIError?
    @State private var isLoading = false
    @State private var loadedRoute: LeagueRoute?

    init(league: LeagueKey) {
        self.league = league
    }

    // MARK: Request

    private var offReason: String? {
        if environment.isDemoMode {
            return nil
        }
        return model.offReason(for: league)
    }

    /// The league's first request, which is also what tells `.task(id:)` the request changed.
    private var route: LeagueRoute {
        switch league {
        case .nba:
            return LeagueRoutes.defenseTable(.nba)
        case .euroleague:
            return LeagueRoutes.elTeams()
        }
    }

    private var loadKey: MacLoadKey {
        MacLoadKey(route: route, generation: model.generation(for: league))
    }

    // MARK: Rows

    private var elRows: [MacRatingsRow] {
        MacRatingsRows.rows(from: elTeams)
    }

    private var nbaRows: [MacNbaRatingsRow] {
        MacNbaRatingsRows.rows(from: nbaEfficiency, defense: nbaDefense)
    }

    private var hasPayload: Bool {
        switch league {
        case .nba:
            return nbaEfficiency != nil || nbaMessage != nil
        case .euroleague:
            return elTeams != nil
        }
    }

    private var rowCount: Int {
        league == .nba ? nbaRows.count : elRows.count
    }

    private var freshness: LeagueFreshness? {
        elTeams?.freshness
    }

    private var footerNotes: [String] {
        switch league {
        case .nba:
            return nbaNotes
        case .euroleague:
            return elTeams?.notes ?? []
        }
    }

    private var attribution: String? {
        league == .euroleague ? model.elMeta?.attribution : nil
    }

    private var caption: String {
        switch league {
        case .euroleague:
            return "Defence index: higher means more points allowed."
        case .nba:
            return "ORtg and DRtg are points scored and allowed per 100 possessions. PA/G is points allowed per game."
        }
    }

    private var selectedElRow: MacRatingsRow? {
        guard let id = selection else { return nil }
        return elRows.first { $0.id == id }
    }

    private var selectedNbaRow: MacNbaRatingsRow? {
        guard let id = selection else { return nil }
        return nbaRows.first { $0.id == id }
    }

    // MARK: Body

    var body: some View {
        VStack(spacing: 0) {
            MacFilterBar {
                Text(caption)
                    .hardwoodText(.caption)
                if isLoading {
                    ProgressView()
                        .controlSize(.small)
                }
            }
            FreshnessBar(freshness: freshness, isBundledDemo: environment.isDemoMode)
            staleStrip
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            NotesFooter(notes: footerNotes, attribution: attribution)
        }
        .macScreenTitle(.ratings, league: league, dataThrough: freshness?.dataThrough)
        .toolbar {
            toolbarContent
        }
        .inspector(isPresented: $model.isInspectorShown) {
            inspectorContent
        }
        .task(id: loadKey) {
            await refresh()
        }
    }

    // MARK: Content

    @ViewBuilder private var staleStrip: some View {
        if let problem = failure, hasPayload {
            MacStaleStrip(message: problem.userMessage) {
                retry()
            }
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
        } else if let message = nbaMessage, league == .nba, nbaRows.isEmpty {
            ContentUnavailableView("No ratings yet",
                                   systemImage: "chart.bar.xaxis",
                                   description: Text(message))
        } else if rowCount == 0 {
            ContentUnavailableView("No teams yet",
                                   systemImage: "chart.bar.xaxis",
                                   description: Text("The server has no ratings for this league yet."))
        } else {
            ratingsTable
        }
    }

    @ViewBuilder private var ratingsTable: some View {
        switch league {
        case .euroleague:
            MacRatingsTable(rows: elRows,
                            selection: $selection,
                            sortOrder: $elSortOrder,
                            onShowDetails: { id in showDetails(id) },
                            onOpenDefense: { id in model.open(.defenceTeam(id)) },
                            onOpenClub: { id in openClub(id) })
        case .nba:
            MacNbaRatingsTable(rows: nbaRows,
                               selection: $selection,
                               sortOrder: $nbaSortOrder,
                               onShowDetails: { id in showDetails(id) },
                               onOpenDefense: { id in model.open(.defenceTeam(id)) })
        }
    }

    private func showDetails(_ id: String) {
        selection = id
        model.isInspectorShown = true
    }

    private func openClub(_ id: String) {
        guard let row = elRows.first(where: { $0.id == id }) else { return }
        openWindow(id: MacWindowID.club,
                   value: LeagueLink(league: .euroleague, id: row.clubCode, title: row.clubName))
    }

    // MARK: Toolbar and inspector

    private var copyHeader: [String] {
        league == .euroleague ? MacRatingsRow.copyHeader : MacNbaRatingsRow.copyHeader
    }

    private var copyRows: [[String]] {
        switch league {
        case .euroleague:
            let shown: [MacRatingsRow] = elRows.sorted(using: elSortOrder)
            return shown.map { $0.copyCells }
        case .nba:
            let shown: [MacNbaRatingsRow] = nbaRows.sorted(using: nbaSortOrder)
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
                MacRatingsPanel(row: row,
                                detail: ratingDetail(for: row),
                                updates: MacRatingUpdateLines.lines(for: row.clubCode, from: elRatings?.updates),
                                onOpenDefense: { model.open(.defenceTeam(row.clubCode)) },
                                onOpenClub: { openClub(row.id) })
                    .padding(Spacing.lg)
            }
        } else if league == .nba, let row = selectedNbaRow {
            ScrollView {
                MacNbaRatingsPanel(row: row) {
                    model.open(.defenceTeam(row.id))
                }
                .padding(Spacing.lg)
            }
        } else {
            ContentUnavailableView("Select a team",
                                   systemImage: "chart.bar.xaxis",
                                   description: Text("Choose a row to see the rating in detail."))
        }
    }

    private func ratingDetail(for row: MacRatingsRow) -> ElRatingRow? {
        let rows: [ElRatingRow] = elRatings?.rows ?? []
        return rows.first { ($0.team?.clubCode ?? $0.team?.id) == row.clubCode }
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
            await loadEuroLeague()
        case .nba:
            await loadNba()
        }
    }

    private func loadEuroLeague() async {
        if loadedRoute != route {
            elTeams = nil
            elRatings = nil
            failure = nil
        }
        isLoading = true
        do {
            let result = try await environment.league.get(ElTeamsTable.self, LeagueRoutes.elTeams())
            if Task.isCancelled { return }
            elTeams = result
            loadedRoute = route
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
            isLoading = false
            return
        }
        do {
            let ratings = try await environment.league.get(ElRatingsTable.self, LeagueRoutes.elRatings())
            if Task.isCancelled { return }
            elRatings = ratings
        } catch {
            if Task.isCancelled { return }
            elRatings = nil
        }
        isLoading = false
    }

    private func loadNba() async {
        if loadedRoute != route {
            nbaEfficiency = nil
            nbaDefense = nil
            nbaMessage = nil
            nbaNotes = []
            failure = nil
        }
        isLoading = true
        do {
            let response = try await environment.client.resolve(efficiencyRequest())
            if Task.isCancelled { return }
            guard let result = response.results.first else {
                throw APIError.decoding("The server's answer had no result for the efficiency table.")
            }
            absorb(result)
            loadedRoute = route
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
            isLoading = false
            return
        }
        do {
            let table = try await environment.league.get(LeagueDefenseDocument.self, LeagueRoutes.defenseTable(.nba))
            if Task.isCancelled { return }
            nbaDefense = table
        } catch {
            if Task.isCancelled { return }
            nbaDefense = nil
        }
        isLoading = false
    }

    private func efficiencyRequest() -> DashboardResolveRequest {
        let config: [String: JSONValue] = environment.catalog.normalizedConfig(
            for: .teamEfficiency,
            config: ["limit": .int(30)]
        )
        let widget = ResolveWidgetRequest(id: "mac-ratings", kind: .teamEfficiency, size: .large, config: config)
        return DashboardResolveRequest(layoutId: "mac-ratings",
                                       context: environment.resolveContext(),
                                       knownSyncVersion: nil,
                                       widgets: [widget])
    }

    private func absorb(_ result: ResolveResult) {
        nbaNotes = result.notes
        guard let payload = result.payload else {
            nbaEfficiency = nil
            nbaMessage = result.failureMessage ?? result.notes.first ?? "The server has no efficiency table yet."
            return
        }
        if case .teamEfficiency(let table) = payload {
            nbaEfficiency = table
            nbaMessage = nil
        } else {
            nbaEfficiency = nil
            nbaMessage = "The server answered with something other than an efficiency table."
        }
    }
}
#endif
