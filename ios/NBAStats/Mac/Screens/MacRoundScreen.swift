#if os(macOS)
import SwiftUI

// Round (EuroLeague) and Slate (NBA): every game of one round or one day with its projected score
// for each side, the margin, the projected winner and the game's status; the inspector shows the
// whole projection for the game that is selected.
//
// ONE SCREEN, TWO PAYLOADS
// The two leagues answer different routes with different envelopes. The EuroLeague asks
// `GET /v1/el/rounds/{n}` and gets an `ElRoundView` (the games, the round's status and a summary).
// The NBA asks `GET /v1/projections?date=` and gets a `LeagueSlateProjections` (the games and the
// model block). Both carry the same game element, `LeagueGameProjection`, so everything below the
// load (the rows, the table, the inspector) is written once and reads `games`.
//
// WHAT PICKS THE ROUND, AND WHAT PICKS THE DAY
// The EuroLeague's round list is `ElMeta.rounds`, which the Mac model loads at launch. When the
// model has none (a server that was down at launch, or demo mode, where the bundled league list
// marks the EuroLeague as off), this screen reads the meta itself rather than sit empty. The default
// round is the first one that is not complete (the one being played or about to be), else the last.
// The NBA has no rounds: it shows "the next slate" (the server picks the next date with scheduled
// games) until the reader moves the date, and then shows exactly that day. A date is a day in the
// reader's calendar, written `yyyy-MM-dd` by `LeagueFormatting.isoDay`.
//
// WHAT THE APP COMPUTES
// Nothing. It chooses which request to make, formats what comes back, and orders rows by a served
// value when a column header is clicked. Moving a date by a day is calendar arithmetic on a date
// the reader is choosing, not a statistic.

/// What the screen's `.task(id:)` watches. The route is optional because the EuroLeague has none
/// until it knows a round.
private struct MacRoundLoadKey: Hashable {
    let route: LeagueRoute?
    let generation: Int
}

/// One entry of the round menu.
private struct MacRoundOption: Identifiable, Hashable {
    let number: Int
    let label: String

    var id: Int { number }
}

/// Day strings and dates for the NBA's date control.
private enum MacRoundDay {

    /// `2026-10-19` as a date at noon in the reader's calendar, so a daylight-saving change can
    /// never move it to another day. Nil for anything that is not `yyyy-MM-dd`.
    static func date(from day: String?) -> Date? {
        guard let day = day else { return nil }
        let parts: [String] = day.split(separator: "-").map { String($0) }
        guard parts.count == 3,
              let year = Int(parts[0]),
              let month = Int(parts[1]),
              let dayNumber = Int(parts[2]) else {
            return nil
        }
        var components = DateComponents()
        components.year = year
        components.month = month
        components.day = dayNumber
        components.hour = 12
        return Calendar.current.date(from: components)
    }
}

struct MacRoundScreen: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel
    @Environment(\.openWindow) private var openWindow

    private let league: LeagueKey

    @State private var elRound: ElRoundView?
    @State private var nbaSlate: LeagueSlateProjections?
    @State private var failure: APIError?
    @State private var isLoading = false
    @State private var loadedRoute: LeagueRoute?
    @State private var selection: MacRoundRow.ID?
    @State private var selectedRound: Int?
    @State private var localRounds: [ElRoundSummary] = []
    @State private var roundListTried = false
    @State private var chosenDay = Date()
    @State private var isNextMode = true

    init(league: LeagueKey) {
        self.league = league
    }

    // MARK: Body

    var body: some View {
        VStack(spacing: 0) {
            MacFilterBar {
                filters
            }
            summaryStrip
            FreshnessBar(freshness: freshness, isBundledDemo: environment.isDemoMode)
            banners
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            NotesFooter(notes: notes, attribution: attribution)
        }
        .macScreenTitle(.round, league: league, dataThrough: freshness?.dataThrough)
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
            step(delta)
        }
        .onAppear {
            consumeRoundRequest()
        }
        .onChange(of: model.pendingNavigation) { _, _ in
            consumeRoundRequest()
        }
    }

    // MARK: What is on screen

    private var games: [LeagueGameProjection] {
        switch league {
        case .nba:
            return nbaSlate?.games ?? []
        case .euroleague:
            return elRound?.games ?? []
        }
    }

    private var hasPayload: Bool {
        nbaSlate != nil || elRound != nil
    }

    private var freshness: LeagueFreshness? {
        elRound?.freshness ?? nbaSlate?.freshness
    }

    private var notes: [String]? {
        elRound?.notes ?? nbaSlate?.notes
    }

    private var attribution: String? {
        league == .euroleague ? model.elMeta?.attribution : nil
    }

    /// The server's own word that the league is off; demo mode ignores it, because the bundled
    /// league list describes a server, not the demo data.
    private var offReason: String? {
        if environment.isDemoMode {
            return nil
        }
        return model.offReason(for: league)
    }

    private var rows: [MacRoundRow] {
        MacRoundRows.rows(from: games, league: league)
    }

    private var copyRows: [[String]] {
        rows.map { $0.copyCells }
    }

    private var selectedProjection: LeagueGameProjection? {
        guard let id = selection else { return nil }
        return games.first { $0.game?.gameId == id }
    }

    // MARK: Rounds and days

    private var rounds: [ElRoundSummary] {
        if let fromModel = model.elMeta?.rounds, !fromModel.isEmpty {
            return fromModel
        }
        return localRounds
    }

    private var roundOptions: [MacRoundOption] {
        var options: [MacRoundOption] = []
        for summary in rounds {
            guard let number = summary.round else { continue }
            let label = "Round " + String(number) + " · " + LeagueFormatting.roundStatusWord(summary.status)
            options.append(MacRoundOption(number: number, label: label))
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

    private func isComplete(_ number: Int) -> Bool {
        rounds.first(where: { $0.round == number })?.status == "complete"
    }

    private var route: LeagueRoute? {
        switch league {
        case .nba:
            let day: String = isNextMode ? "next" : LeagueFormatting.isoDay(chosenDay)
            return LeagueRoutes.slate(date: day)
        case .euroleague:
            guard let number = effectiveRound else { return nil }
            return LeagueRoutes.elRound(number, complete: isComplete(number))
        }
    }

    private var loadKey: MacRoundLoadKey {
        MacRoundLoadKey(route: route, generation: model.generation(for: league))
    }

    private var dayBinding: Binding<Date> {
        Binding<Date>(
            get: { chosenDay },
            set: { newValue in
                chosenDay = newValue
                isNextMode = false
            }
        )
    }

    private var roundBinding: Binding<Int> {
        Binding<Int>(
            get: { effectiveRound ?? 0 },
            set: { newValue in
                selectedRound = newValue
            }
        )
    }

    // MARK: Loading

    /// One load: the slate or round for the current route, or (EuroLeague) the round list first.
    private func refresh() async {
        if offReason != nil {
            return
        }
        guard let route = route else {
            await fetchRoundList()
            return
        }
        await load(route)
    }

    private func load(_ route: LeagueRoute) async {
        if loadedRoute != route {
            elRound = nil
            nbaSlate = nil
            failure = nil
            selection = nil
        }
        isLoading = true
        do {
            switch league {
            case .nba:
                let result = try await environment.league.get(LeagueSlateProjections.self, route)
                if Task.isCancelled { return }
                nbaSlate = result
                syncDay(with: result.date)
            case .euroleague:
                let result = try await environment.league.get(ElRoundView.self, route)
                if Task.isCancelled { return }
                elRound = result
            }
            loadedRoute = route
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
        }
        isLoading = false
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
        roundListTried = true
    }

    private func retry() {
        Task {
            await refresh()
        }
    }

    /// While "next slate" is showing, the date control shows the day the server chose.
    private func syncDay(with day: String?) {
        guard isNextMode, let parsed = MacRoundDay.date(from: day) else { return }
        chosenDay = parsed
    }

    // MARK: Requests from elsewhere

    /// "Show this round", left by Start Here or a menu. Only a round request is taken and cleared:
    /// a request for another screen (a game's matchup) must stay where it is for that screen.
    private func consumeRoundRequest() {
        guard let navigation = model.pendingNavigation else { return }
        switch navigation {
        case .round(let number):
            model.pendingNavigation = nil
            selectedRound = number
        case .matchupGame, .matchupTeam, .defenceTeam:
            break
        }
    }

    private func step(_ delta: Int) {
        switch league {
        case .nba:
            stepDay(delta)
        case .euroleague:
            stepRound(delta)
        }
    }

    private func stepDay(_ delta: Int) {
        let moved: Date = Calendar.current.date(byAdding: .day, value: delta, to: chosenDay) ?? chosenDay
        chosenDay = moved
        isNextMode = false
    }

    private func stepRound(_ delta: Int) {
        let numbers: [Int] = roundOptions.map { $0.number }
        guard let current = effectiveRound, let index = numbers.firstIndex(of: current) else { return }
        let target = index + delta
        guard target >= 0, target < numbers.count else { return }
        selectedRound = numbers[target]
    }

    private func canStep(_ delta: Int) -> Bool {
        let numbers: [Int] = roundOptions.map { $0.number }
        guard let current = effectiveRound, let index = numbers.firstIndex(of: current) else { return false }
        let target = index + delta
        return target >= 0 && target < numbers.count
    }

    private func showDetails(_ id: MacRoundRow.ID) {
        selection = id
        model.isInspectorShown = true
    }

    private func openMatchup(_ id: MacRoundRow.ID) {
        model.open(.matchupGame(id))
    }

    private func openBoxScore(_ id: MacRoundRow.ID) {
        guard let projection = games.first(where: { $0.game?.gameId == id }) else { return }
        openBoxScore(for: projection)
    }

    private func openBoxScore(for projection: LeagueGameProjection) {
        guard let gameId = projection.game?.gameId else { return }
        let title: String = projection.game?.matchupText(for: league) ?? "Box Score"
        openWindow(id: MacWindowID.boxScore, value: LeagueLink(league: league, id: gameId, title: title))
    }

    // MARK: Filters

    @ViewBuilder private var filters: some View {
        switch league {
        case .nba:
            nbaFilters
        case .euroleague:
            euroleagueFilters
        }
    }

    private var nbaFilters: some View {
        HStack(spacing: Spacing.sm) {
            Button {
                step(-1)
            } label: {
                Image(systemName: "chevron.left")
            }
            .help("Previous day (⌘[)")
            DatePicker("Date", selection: dayBinding, displayedComponents: .date)
                .datePickerStyle(.field)
                .labelsHidden()
            Button {
                step(1)
            } label: {
                Image(systemName: "chevron.right")
            }
            .help("Next day (⌘])")
            Button("Next slate") {
                isNextMode = true
            }
            .disabled(isNextMode)
            dayCaption
            loadingIndicator
        }
    }

    @ViewBuilder private var dayCaption: some View {
        if let day = nbaSlate?.date {
            Text(LeagueFormatting.leagueDate(day))
                .hardwoodText(.caption)
        }
    }

    private var euroleagueFilters: some View {
        HStack(spacing: Spacing.sm) {
            Button {
                step(-1)
            } label: {
                Image(systemName: "chevron.left")
            }
            .disabled(!canStep(-1))
            .help("Previous round (⌘[)")
            roundPicker
            Button {
                step(1)
            } label: {
                Image(systemName: "chevron.right")
            }
            .disabled(!canStep(1))
            .help("Next round (⌘])")
            loadingIndicator
        }
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

    // MARK: Strips

    @ViewBuilder private var summaryStrip: some View {
        if let summary = elRound?.summary {
            MacRoundSummaryStrip(summary: summary)
        }
    }

    private var hasAfterTheFactRows: Bool {
        games.contains { $0.isComputedAfterTheFact }
    }

    @ViewBuilder private var banners: some View {
        VStack(spacing: Spacing.xs) {
            if let problem = failure, hasPayload {
                MacStaleStrip(message: problem.userMessage) {
                    retry()
                }
            }
            if elRound?.status == "resultPending" {
                LeagueStrip(symbol: "clock",
                            text: "Played; results not loaded yet.",
                            tint: Palette.neutral)
                    .padding(.horizontal, Spacing.md)
            }
            if hasAfterTheFactRows {
                LeagueStrip(symbol: "clock.arrow.circlepath",
                            text: "Rows with a clock mark: " + LeagueGameProjection.afterTheFactNote.lowercased(),
                            tint: Palette.warning)
                    .padding(.horizontal, Spacing.md)
            }
        }
    }

    // MARK: Content

    @ViewBuilder private var content: some View {
        if let reason = offReason {
            MacLeagueOffView(league: league, reason: reason)
        } else if !hasPayload, let problem = failure {
            MacLoadFailureView(error: problem, league: league) {
                retry()
            }
        } else if !hasPayload {
            waiting
        } else if games.isEmpty {
            emptyState
        } else {
            gamesTable
        }
    }

    @ViewBuilder private var waiting: some View {
        if league == .euroleague && roundListTried && rounds.isEmpty {
            ContentUnavailableView("No rounds yet",
                                   systemImage: "calendar",
                                   description: Text("The EuroLeague has no rounds on your server yet."))
        } else {
            MacLoadingView()
        }
    }

    @ViewBuilder private var emptyState: some View {
        switch league {
        case .nba:
            ContentUnavailableView {
                Label("No projected games on this date", systemImage: "calendar")
            } description: {
                Text("The notes below say why: no game is scheduled, or none can be projected yet. "
                     + "Choose another date, or go to the next scheduled slate.")
            } actions: {
                Button("Next slate") {
                    isNextMode = true
                }
            }
        case .euroleague:
            ContentUnavailableView("No projected games in this round",
                                   systemImage: "calendar",
                                   description: Text("None of this round's games has a projection yet. The notes below say why."))
        }
    }

    private var gamesTable: some View {
        MacRoundTable(rows: rows,
                      selection: $selection,
                      onShowDetails: { id in showDetails(id) },
                      onOpenMatchup: { id in openMatchup(id) },
                      onOpenBoxScore: { id in openBoxScore(id) })
    }

    // MARK: Toolbar and inspector

    @ToolbarContentBuilder private var toolbarContent: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            MacCopyTableButton(header: MacRoundRow.copyHeader, rows: copyRows)
            MacInspectorToggle()
        }
    }

    @ViewBuilder private var inspectorBody: some View {
        if let projection = selectedProjection {
            MacRoundInspector(league: league,
                              projection: projection,
                              onOpenMatchup: { openMatchup(of: projection) },
                              onOpenBoxScore: { openBoxScore(for: projection) })
        } else {
            ContentUnavailableView("Select a game",
                                   systemImage: "sportscourt",
                                   description: Text("Choose a row to see its projection."))
        }
    }

    private var inspectorContent: some View {
        inspectorBody
            .inspectorColumnWidth(min: 280, ideal: 340, max: 460)
    }

    private func openMatchup(of projection: LeagueGameProjection) {
        guard let gameId = projection.game?.gameId else { return }
        model.open(.matchupGame(gameId))
    }
}
#endif
