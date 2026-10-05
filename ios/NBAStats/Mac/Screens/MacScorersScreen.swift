#if os(macOS)
import SwiftUI

// Scorers: the players projected to score the most. Round Scorers (EuroLeague) is the workbook's
// R3 Scorers sheet: each club's top scorers for a round, with the model's points, the player's recent
// form and the projection. Projected Scorers (NBA) is each player's next-game points with the
// season average beside it.
//
// ONE SCREEN, TWO PAYLOADS
// The EuroLeague reads `GET /v1/el/rounds/{n}/scorers` (an `ElRoundScorers`). The NBA has no round
// and no scorers route, so it asks the dashboard's own resolver for the projection board, once, the
// same way a dashboard tile would: `POST /v1/dashboard/resolve` with a single `projection_board`
// widget, scope League or Favourites, points only. The server ranks that board; the table starts in
// that order.
//
// WHAT THE SCREEN DOES NOT SAY
// A projection here is one player's projected points. There is nothing to compare it with and no
// likelihood: the workbook's columns that compared projections with outside numbers are not
// reproduced. A player with no reported status shows an em dash, not "Available".
//
// HOW IT LOADS (MAC_DESIGN 6.6)
// One `.task(id:)` keyed on what the request depends on (league, round, players per club, scope and
// the league's reload counter). A request that differs from the last one clears the payload first,
// so one round's players never sit under another round's name.

/// What the screen's `.task(id:)` watches.
private struct MacScorersLoadKey: Hashable {
    let league: LeagueKey
    let round: Int?
    let perClub: Int
    let scope: String
    let generation: Int
}

/// One entry of the round menu.
private struct MacScorersRoundOption: Identifiable, Hashable {
    let number: Int
    let label: String

    var id: Int { number }
}

struct MacScorersScreen: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    private let league: LeagueKey

    @State private var selectedRound: Int?
    @State private var localRounds: [ElRoundSummary] = []
    @State private var perClub = 2
    @State private var clubFilter: String?
    @State private var scope = "league"
    @State private var selection: String?
    @State private var elSortOrder: [KeyPathComparator<MacScorerRow>] = [
        KeyPathComparator(\MacScorerRow.order)
    ]
    @State private var nbaSortOrder: [KeyPathComparator<MacNbaScorerRow>] = [
        KeyPathComparator(\MacNbaScorerRow.order)
    ]
    @State private var elScorers: ElRoundScorers?
    @State private var nbaBoard: ProjectionBoardPayload?
    @State private var nbaStatuses: [Int: MacNbaStatusInfo] = [:]
    @State private var nbaNotes: [String] = []
    @State private var nbaMessage: String?
    @State private var failure: APIError?
    @State private var isLoading = false
    @State private var loadedKey: MacScorersLoadKey?
    @State private var playerSheet: PlayerRef?

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

    private var rounds: [ElRoundSummary] {
        if let fromModel = model.elMeta?.rounds, !fromModel.isEmpty {
            return fromModel
        }
        return localRounds
    }

    private var roundOptions: [MacScorersRoundOption] {
        var options: [MacScorersRoundOption] = []
        for summary in rounds {
            guard let number = summary.round else { continue }
            let label = "Round " + String(number) + " · " + LeagueFormatting.roundStatusWord(summary.status)
            options.append(MacScorersRoundOption(number: number, label: label))
        }
        return options
    }

    /// The round on screen: the reader's choice, else the first that is not complete, else the last.
    private var effectiveRound: Int? {
        if let chosen = selectedRound {
            return chosen
        }
        let known: [ElRoundSummary] = rounds.filter { $0.round != nil }
        if let current = known.first(where: { $0.status != "complete" }) {
            return current.round
        }
        return known.last?.round
    }

    private var loadKey: MacScorersLoadKey {
        let round: Int? = league == .euroleague ? effectiveRound : nil
        return MacScorersLoadKey(league: league,
                                 round: round,
                                 perClub: perClub,
                                 scope: scope,
                                 generation: model.generation(for: league))
    }

    private var roundBinding: Binding<Int> {
        Binding<Int>(
            get: { effectiveRound ?? 0 },
            set: { newValue in
                selectedRound = newValue
            }
        )
    }

    // MARK: Rows

    private var elRows: [MacScorerRow] {
        MacScorerRows.filtered(MacScorerRows.rows(from: elScorers), clubCode: clubFilter)
    }

    private var nbaRows: [MacNbaScorerRow] {
        MacNbaScorerRows.rows(from: nbaBoard, statuses: nbaStatuses)
    }

    private var hasPayload: Bool {
        switch league {
        case .nba:
            return nbaBoard != nil || nbaMessage != nil
        case .euroleague:
            return elScorers != nil
        }
    }

    private var rowCount: Int {
        league == .nba ? nbaRows.count : elRows.count
    }

    private var freshness: LeagueFreshness? {
        elScorers?.freshness
    }

    private var footerNotes: [String] {
        switch league {
        case .nba:
            return nbaNotes
        case .euroleague:
            return elScorers?.notes ?? []
        }
    }

    private var attribution: String? {
        league == .euroleague ? model.elMeta?.attribution : nil
    }

    private var selectedElRow: MacScorerRow? {
        guard let id = selection else { return nil }
        return elRows.first { $0.id == id }
    }

    private var selectedNbaRow: MacNbaScorerRow? {
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
            NotesFooter(notes: footerNotes, attribution: attribution)
        }
        .macScreenTitle(.scorers, league: league, dataThrough: freshness?.dataThrough)
        .toolbar {
            toolbarContent
        }
        .inspector(isPresented: $model.isInspectorShown) {
            inspectorContent
        }
        .task(id: loadKey) {
            await refresh()
        }
        .macOnStep { delta in
            stepRound(delta)
        }
        .sheet(item: $playerSheet) { player in
            MacNbaPlayerSheet(player: player)
                .environmentObject(environment)
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
            stepButton(-1)
            roundPicker
            stepButton(1)
            Stepper("Players per club: " + String(perClub), value: $perClub, in: 1...5)
            MacTeamPicker(league: league, title: "Club", selection: $clubFilter, noneLabel: "All clubs")
            loadingIndicator
        }
    }

    private var nbaFilters: some View {
        HStack(spacing: Spacing.md) {
            Picker("Players", selection: $scope) {
                Text("League").tag("league")
                Text("Favourites").tag("favorites")
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 200)
            nbaHeadline
            loadingIndicator
        }
    }

    @ViewBuilder private var nbaHeadline: some View {
        if let board = nbaBoard {
            Text(board.dateHeadline + " · " + board.gameCountText)
                .hardwoodText(.caption)
        }
    }

    private func stepButton(_ delta: Int) -> some View {
        Button {
            stepRound(delta)
        } label: {
            Image(systemName: delta < 0 ? "chevron.left" : "chevron.right")
        }
        .disabled(!canStep(delta))
        .help(delta < 0 ? "Previous round (⌘[)" : "Next round (⌘])")
    }

    @ViewBuilder private var roundPicker: some View {
        if roundOptions.isEmpty {
            Text("No rounds yet")
                .hardwoodText(.caption)
        } else {
            Picker("Round", selection: roundBinding) {
                ForEach(roundOptions) { option in
                    Text(option.label).tag(option.number)
                }
            }
            .pickerStyle(.menu)
            .labelsHidden()
            .frame(minWidth: 230)
        }
    }

    @ViewBuilder private var loadingIndicator: some View {
        if isLoading {
            ProgressView()
                .controlSize(.small)
        }
    }

    private func canStep(_ delta: Int) -> Bool {
        let numbers: [Int] = roundOptions.map { $0.number }
        guard let current = effectiveRound, let index = numbers.firstIndex(of: current) else { return false }
        let target = index + delta
        return target >= 0 && target < numbers.count
    }

    private func stepRound(_ delta: Int) {
        guard league == .euroleague, canStep(delta) else { return }
        let numbers: [Int] = roundOptions.map { $0.number }
        guard let current = effectiveRound, let index = numbers.firstIndex(of: current) else { return }
        selectedRound = numbers[index + delta]
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
            ContentUnavailableView("No projections yet",
                                   systemImage: "person.3",
                                   description: Text(message))
        } else if rowCount == 0 {
            emptyState
        } else {
            scorersTable
        }
    }

    @ViewBuilder private var emptyState: some View {
        switch league {
        case .nba:
            ContentUnavailableView("No projections for this slate",
                                   systemImage: "person.3",
                                   description: Text("Try League, or come back when a slate is scheduled."))
        case .euroleague:
            ContentUnavailableView("No scorers for this round",
                                   systemImage: "person.3",
                                   description: Text("The server has no projected scorers for this round yet."))
        }
    }

    @ViewBuilder private var scorersTable: some View {
        switch league {
        case .euroleague:
            MacScorersTable(rows: elRows,
                            selection: $selection,
                            sortOrder: $elSortOrder,
                            onShowDetails: { id in showDetails(id) },
                            onOpenMatchup: { id in openElMatchup(id) })
        case .nba:
            MacNbaScorersTable(rows: nbaRows,
                               selection: $selection,
                               sortOrder: $nbaSortOrder,
                               onShowDetails: { id in showDetails(id) },
                               onOpenMatchup: { id in openNbaMatchup(id) })
        }
    }

    private func showDetails(_ id: String) {
        selection = id
        model.isInspectorShown = true
    }

    private func openElMatchup(_ id: String) {
        guard let row = elRows.first(where: { $0.id == id }), !row.gameId.isEmpty else { return }
        model.open(.matchupGame(row.gameId))
    }

    private func openNbaMatchup(_ id: String) {
        guard let row = nbaRows.first(where: { $0.id == id }), !row.gameId.isEmpty else { return }
        model.open(.matchupGame(row.gameId))
    }

    // MARK: Toolbar and inspector

    private var copyHeader: [String] {
        league == .euroleague ? MacScorerRow.copyHeader : MacNbaScorerRow.copyHeader
    }

    private var copyRows: [[String]] {
        switch league {
        case .euroleague:
            let shown: [MacScorerRow] = elRows.sorted(using: elSortOrder)
            return shown.map { $0.copyCells }
        case .nba:
            let shown: [MacNbaScorerRow] = nbaRows.sorted(using: nbaSortOrder)
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
                MacScorerPanel(row: row) {
                    openElMatchup(row.id)
                }
                .padding(Spacing.lg)
            }
        } else if league == .nba, let row = selectedNbaRow {
            ScrollView {
                MacNbaScorerPanel(row: row,
                                  onOpenPlayer: { playerSheet = row.playerRef },
                                  onOpenMatchup: { openNbaMatchup(row.id) })
                    .padding(Spacing.lg)
            }
        } else {
            ContentUnavailableView("Select a player",
                                   systemImage: "person",
                                   description: Text("Choose a row to see the projection in detail."))
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
            guard let round = effectiveRound else {
                await fetchRoundList()
                return
            }
            await loadEuroLeague(round)
        case .nba:
            await loadNba()
        }
    }

    /// The model's round list is empty: ask the EuroLeague's meta directly.
    private func fetchRoundList() async {
        do {
            let meta = try await environment.league.get(ElMeta.self, LeagueRoutes.elMeta())
            if Task.isCancelled { return }
            localRounds = meta.rounds ?? []
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
        }
    }

    private func loadEuroLeague(_ round: Int) async {
        let key = loadKey
        if loadedKey != key {
            elScorers = nil
            failure = nil
        }
        isLoading = true
        do {
            let route = LeagueRoutes.elScorers(round: round, perClub: perClub)
            let result = try await environment.league.get(ElRoundScorers.self, route)
            if Task.isCancelled { return }
            elScorers = result
            loadedKey = key
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
        }
        isLoading = false
    }

    /// The projection board through the dashboard's resolver: one widget, points only.
    private func loadNba() async {
        let key = loadKey
        if loadedKey != key {
            nbaBoard = nil
            nbaMessage = nil
            nbaNotes = []
            failure = nil
        }
        isLoading = true
        do {
            let response = try await environment.client.resolve(boardRequest())
            if Task.isCancelled { return }
            guard let result = response.results.first else {
                throw APIError.decoding("The server's answer had no result for the projection board.")
            }
            absorb(result)
            loadedKey = key
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
            isLoading = false
            return
        }
        await loadNbaStatuses()
        isLoading = false
    }

    private func boardRequest() -> DashboardResolveRequest {
        let config: [String: JSONValue] = environment.catalog.normalizedConfig(
            for: .projectionBoard,
            config: [
                "scope": .string(scope),
                "limit": .int(20),
                "metrics": .array([.string("pts")])
            ]
        )
        let widget = ResolveWidgetRequest(id: "mac-scorers", kind: .projectionBoard, size: .large, config: config)
        return DashboardResolveRequest(layoutId: "mac-scorers",
                                       context: environment.resolveContext(),
                                       knownSyncVersion: nil,
                                       widgets: [widget])
    }

    /// Keeps the board, or the server's reason there is none.
    private func absorb(_ result: ResolveResult) {
        nbaNotes = result.notes
        guard let payload = result.payload else {
            nbaBoard = nil
            nbaMessage = result.failureMessage ?? result.notes.first ?? "The server has no projections for this slate."
            return
        }
        if case .projectionBoard(let board) = payload {
            nbaBoard = board
            nbaMessage = nil
            if let note = board.note, !note.isEmpty {
                nbaNotes.append(note)
            }
        } else {
            nbaBoard = nil
            nbaMessage = "The server answered with something other than a projection board."
        }
    }

    /// The report's statuses, joined to the board's players. A failure leaves the column empty.
    private func loadNbaStatuses() async {
        do {
            let route = LeagueRoutes.availability(.nba, team: nil, date: "next")
            let report = try await environment.league.get(LeagueAvailabilityReport.self, route)
            if Task.isCancelled { return }
            nbaStatuses = MacNbaScorerRows.statuses(from: report)
        } catch {
            if Task.isCancelled { return }
            nbaStatuses = [:]
        }
    }
}
#endif
