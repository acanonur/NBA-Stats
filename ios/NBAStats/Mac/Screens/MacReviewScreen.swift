#if os(macOS)
import SwiftUI

// Round Review (EuroLeague) and Slate Review (NBA): what the model said before the games against
// what happened, game by game, with how many winners it called and how far off its scores were.
//
// ONE SCREEN, ONE PAYLOAD
// Both leagues answer `GET {prefix}/projections/review` with a `LeagueProjectionReview`. The
// EuroLeague reviews by round (one completed round, or every round: no round chosen); the NBA by
// day (one day, or the whole season). The cards above the table are the server's own counts and
// means for each model, with the rows rebuilt after the fact counted apart; the table is every game.
//
// HONESTY ABOUT THE EVIDENCE
// A locked projection is one frozen before tip-off; a reconstructed one was made afterwards from
// inputs dated before tip-off and says so in its Basis cell and in its own card. The screen never
// blends them and never reviews a game against a number from outside Hardwood: the workbook's review
// columns that compared projections with outside numbers are not reproduced.
//
// WHAT THE APP DOES NOT DO
// It averages nothing and calls no winner. The counts, the means and each game's misses are the
// server's; a game with no projection recorded before tip-off is simply not in the table.

/// One entry of the round menu.
private struct MacReviewRoundOption: Identifiable, Hashable {
    let number: Int
    let label: String

    var id: Int { number }
}

struct MacReviewScreen: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel
    @Environment(\.openWindow) private var openWindow

    private let league: LeagueKey

    @State private var selectedRound: Int?
    @State private var localRounds: [ElRoundSummary] = []
    @State private var isOneDay = false
    @State private var chosenDay = Date()
    @State private var modelChoice: String?
    @State private var selection: String?
    @State private var sortOrder: [KeyPathComparator<MacReviewRow>] = [
        KeyPathComparator(\MacReviewRow.dateSort)
    ]
    @State private var payload: LeagueProjectionReview?
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

    private var rounds: [ElRoundSummary] {
        if let fromModel = model.elMeta?.rounds, !fromModel.isEmpty {
            return fromModel
        }
        return localRounds
    }

    /// Rounds with at least one game played: the ones a review can say anything about.
    private var roundOptions: [MacReviewRoundOption] {
        var options: [MacReviewRoundOption] = []
        for summary in rounds {
            guard let number = summary.round, summary.status != "upcoming" else { continue }
            let label = "Round " + String(number) + " · " + LeagueFormatting.roundStatusWord(summary.status)
            options.append(MacReviewRoundOption(number: number, label: label))
        }
        return options
    }

    private var route: LeagueRoute {
        switch league {
        case .nba:
            let day: String? = isOneDay ? LeagueFormatting.isoDay(chosenDay) : nil
            return LeagueRoutes.review(.nba, date: day)
        case .euroleague:
            return LeagueRoutes.review(.euroleague, round: selectedRound)
        }
    }

    private var loadKey: MacLoadKey {
        MacLoadKey(route: route, generation: model.generation(for: league))
    }

    private var roundBinding: Binding<Int?> {
        Binding<Int?>(
            get: { selectedRound },
            set: { newValue in
                selectedRound = newValue
            }
        )
    }

    private var dayBinding: Binding<Date> {
        Binding<Date>(
            get: { chosenDay },
            set: { newValue in
                chosenDay = newValue
            }
        )
    }

    // MARK: Rows

    private var modelKeys: [String] {
        MacReviewRows.modelKeys(from: payload)
    }

    /// The model on screen: the reader's choice if it is still offered, else the first.
    private var effectiveModel: String? {
        if let chosen = modelChoice, modelKeys.contains(chosen) {
            return chosen
        }
        return modelKeys.first
    }

    private var rows: [MacReviewRow] {
        let all: [MacReviewRow] = MacReviewRows.rows(from: payload, league: league)
        return MacReviewRows.filtered(all, modelKeys: modelKeys, selected: effectiveModel)
    }

    private var selectedRow: MacReviewRow? {
        guard let id = selection else { return nil }
        return rows.first { $0.id == id }
    }

    private var freshness: LeagueFreshness? {
        payload?.freshness
    }

    private var attribution: String? {
        league == .euroleague ? model.elMeta?.attribution : nil
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
            NotesFooter(notes: payload?.notes, attribution: attribution)
        }
        .macScreenTitle(.review, league: league, dataThrough: freshness?.dataThrough)
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
        HStack(spacing: Spacing.sm) {
            stepButton(-1)
            Picker("Round", selection: roundBinding) {
                Text("All rounds").tag(nil as Int?)
                ForEach(roundOptions) { option in
                    Text(option.label).tag(option.number as Int?)
                }
            }
            .pickerStyle(.menu)
            .labelsHidden()
            .frame(minWidth: 230)
            stepButton(1)
            modelPicker
            loadingIndicator
        }
    }

    private var nbaFilters: some View {
        HStack(spacing: Spacing.sm) {
            Picker("Scope", selection: $isOneDay) {
                Text("Season").tag(false)
                Text("One day").tag(true)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 180)
            if isOneDay {
                stepButton(-1)
                DatePicker("Date", selection: dayBinding, displayedComponents: .date)
                    .datePickerStyle(.field)
                    .labelsHidden()
                stepButton(1)
            }
            modelPicker
            loadingIndicator
        }
    }

    @ViewBuilder private var modelPicker: some View {
        if modelKeys.count > 1 {
            Picker("Model", selection: modelBinding) {
                ForEach(modelKeys, id: \.self) { key in
                    Text(MacReviewWords.model(key)).tag(key as String?)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 320)
        }
    }

    private var modelBinding: Binding<String?> {
        Binding<String?>(
            get: { effectiveModel },
            set: { newValue in
                modelChoice = newValue
            }
        )
    }

    private func stepButton(_ delta: Int) -> some View {
        Button {
            step(delta)
        } label: {
            Image(systemName: delta < 0 ? "chevron.left" : "chevron.right")
        }
        .disabled(!canStep(delta))
        .help(delta < 0 ? "Previous (⌘[)" : "Next (⌘])")
    }

    @ViewBuilder private var loadingIndicator: some View {
        if isLoading {
            ProgressView()
                .controlSize(.small)
        }
    }

    private func canStep(_ delta: Int) -> Bool {
        switch league {
        case .nba:
            return isOneDay
        case .euroleague:
            let numbers: [Int] = roundOptions.map { $0.number }
            guard let current = selectedRound, let index = numbers.firstIndex(of: current) else {
                return false
            }
            let target = index + delta
            return target >= 0 && target < numbers.count
        }
    }

    private func step(_ delta: Int) {
        guard canStep(delta) else { return }
        switch league {
        case .nba:
            let moved: Date = Calendar.current.date(byAdding: .day, value: delta, to: chosenDay) ?? chosenDay
            chosenDay = moved
        case .euroleague:
            let numbers: [Int] = roundOptions.map { $0.number }
            guard let current = selectedRound, let index = numbers.firstIndex(of: current) else { return }
            selectedRound = numbers[index + delta]
        }
    }

    // MARK: Content

    @ViewBuilder private var staleStrip: some View {
        if let problem = failure, payload != nil {
            MacStaleStrip(message: problem.userMessage) {
                retry()
            }
        }
    }

    @ViewBuilder private var content: some View {
        if let reason = offReason {
            MacLeagueOffView(league: league, reason: reason)
        } else if payload == nil, let problem = failure {
            MacLoadFailureView(error: problem, league: league) {
                retry()
            }
        } else if payload == nil {
            MacLoadingView()
        } else if rows.isEmpty {
            emptyState
        } else {
            loadedContent
        }
    }

    private var emptyState: some View {
        VStack(spacing: 0) {
            MacReviewCardsRow(cards: MacReviewCards.cards(from: payload))
            ContentUnavailableView("Nothing to review here",
                                   systemImage: "checkmark.seal",
                                   description: Text(emptyText))
        }
    }

    private var emptyText: String {
        if league == .euroleague && selectedRound != nil {
            return "No projection was recorded before tip-off for this round."
        }
        return "No final game in this choice has a projection to review yet."
    }

    private var loadedContent: some View {
        VStack(spacing: 0) {
            MacReviewCardsRow(cards: MacReviewCards.cards(from: payload))
            Text("Miss is the actual result less the projection. A negative combined miss means the game finished lower than projected.")
                .hardwoodText(.caption)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Spacing.md)
                .padding(.bottom, Spacing.xs)
            MacReviewTable(rows: rows,
                           selection: $selection,
                           sortOrder: $sortOrder,
                           onShowDetails: { id in showDetails(id) },
                           onOpenMatchup: { id in openMatchup(id) },
                           onOpenBoxScore: { id in openBoxScore(id) })
        }
    }

    private func showDetails(_ id: String) {
        selection = id
        model.isInspectorShown = true
    }

    private func gameId(of id: String) -> String? {
        guard let row = rows.first(where: { $0.id == id }), !row.gameId.isEmpty else { return nil }
        return row.gameId
    }

    private func openMatchup(_ id: String) {
        guard let game = gameId(of: id) else { return }
        model.open(.matchupGame(game))
    }

    private func openBoxScore(_ id: String) {
        guard let game = gameId(of: id) else { return }
        let title: String = rows.first(where: { $0.id == id })?.title ?? "Box Score"
        openWindow(id: MacWindowID.boxScore, value: LeagueLink(league: league, id: game, title: title))
    }

    // MARK: Toolbar and inspector

    private var copyRows: [[String]] {
        let shown: [MacReviewRow] = rows.sorted(using: sortOrder)
        return shown.map { $0.copyCells }
    }

    @ToolbarContentBuilder private var toolbarContent: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            MacCopyTableButton(header: MacReviewRow.copyHeader, rows: copyRows)
            MacInspectorToggle()
        }
    }

    private var inspectorContent: some View {
        inspectorBody
            .inspectorColumnWidth(min: 280, ideal: 340, max: 460)
    }

    @ViewBuilder private var inspectorBody: some View {
        if let row = selectedRow {
            MacReviewInspector(league: league,
                               row: row,
                               onOpenMatchup: { openMatchup(row.id) },
                               onOpenBoxScore: { openBoxScore(row.id) })
                .id(row.id)
        } else {
            ContentUnavailableView("Select a game",
                                   systemImage: "checkmark.seal",
                                   description: Text("Choose a row to see the projection and what happened."))
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
        if league == .euroleague && rounds.isEmpty && localRounds.isEmpty {
            await fetchRoundList()
        }
        let wanted: LeagueRoute = route
        if loadedRoute != wanted {
            payload = nil
            failure = nil
        }
        isLoading = true
        do {
            let result = try await environment.league.get(LeagueProjectionReview.self, wanted)
            if Task.isCancelled { return }
            payload = result
            loadedRoute = wanted
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
        }
        isLoading = false
    }

    /// The model's round list is empty: ask the EuroLeague's meta directly, for the round menu.
    private func fetchRoundList() async {
        do {
            let meta = try await environment.league.get(ElMeta.self, LeagueRoutes.elMeta())
            if Task.isCancelled { return }
            localRounds = meta.rounds ?? []
        } catch {
            if Task.isCancelled { return }
        }
    }
}
#endif
