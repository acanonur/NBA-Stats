#if os(macOS)
import SwiftUI

// Box Scores & Latest Games (EuroLeague) and Games & Box Scores (NBA): a list of games on the left,
// the box score of the selected game on the right.
//
// THE LIST
// EuroLeague: By round (every game of a round, scheduled and played, the workbook's round sheets) or
// By club (that club's games, the workbook's Latest Games), from `GET /v1/el/games`, which answers an
// envelope (`ElGamesList`) of `GameRef`-shaped games. NBA: one day's scoreboard (`GET /v1/games`),
// the latest day with results until the reader moves the date. Selecting a game shows its box score
// beside the list; double-clicking one opens it in its own window.
//
// THE CLUB'S RECORD
// By club shows the club's win-loss record from the teams table, under one sentence the screen must
// always say: it counts EuroLeague games only, so it differs from a record that counts friendlies,
// domestic and national-team games too.
//
// WHAT THE APP DOES NOT DO
// It computes no record, no score and no leader. Every cell is a field of a game or a box score.

/// The EuroLeague's two ways of choosing games.
private enum MacGamesMode: String, CaseIterable, Identifiable, Hashable {
    case byRound
    case byClub

    var id: String { rawValue }

    var title: String {
        switch self {
        case .byRound:
            return "By round"
        case .byClub:
            return "By club"
        }
    }
}

/// One entry of the round menu.
private struct MacGamesRoundOption: Identifiable, Hashable {
    let number: Int
    let label: String

    var id: Int { number }
}

struct MacBoxScoresScreen: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel
    @Environment(\.openWindow) private var openWindow

    private let league: LeagueKey

    @State private var mode: MacGamesMode = .byRound
    @State private var selectedRound: Int?
    @State private var localRounds: [ElRoundSummary] = []
    @State private var clubCode: String?
    @State private var isLatestDay = true
    @State private var chosenDay = Date()
    @State private var selection: String?
    @State private var sortOrder: [KeyPathComparator<MacGameListRow>] = [
        KeyPathComparator(\MacGameListRow.dateSort)
    ]
    @State private var elGames: ElGamesList?
    @State private var nbaBoard: ScoreboardResponse?
    @State private var elTeams: ElTeamsTable?
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

    private var roundOptions: [MacGamesRoundOption] {
        var options: [MacGamesRoundOption] = []
        for summary in rounds {
            guard let number = summary.round else { continue }
            let label = "Round " + String(number) + " · " + LeagueFormatting.roundStatusWord(summary.status)
            options.append(MacGamesRoundOption(number: number, label: label))
        }
        return options
    }

    /// The round on screen: the reader's choice, else the latest round that has begun, else the first.
    private var effectiveRound: Int? {
        if let chosen = selectedRound {
            return chosen
        }
        let known: [ElRoundSummary] = rounds.filter { $0.round != nil }
        if let begun = known.last(where: { $0.status != "upcoming" }) {
            return begun.round
        }
        return known.first?.round
    }

    /// The club on screen: the reader's choice, else their favourite club, else the first in the list.
    private var effectiveClub: String? {
        if let chosen = clubCode {
            return chosen
        }
        if let favourite = model.favoriteClubCode, !favourite.isEmpty {
            return favourite
        }
        return model.teamOptions(for: .euroleague).first?.id
    }

    /// The request for what is chosen, or nil while a EuroLeague round or club is not known yet.
    private var route: LeagueRoute? {
        switch league {
        case .nba:
            let day: String = isLatestDay ? "latest" : LeagueFormatting.isoDay(chosenDay)
            return LeagueRoutes.nbaGames(date: day)
        case .euroleague:
            switch mode {
            case .byRound:
                guard let number = effectiveRound else { return nil }
                return LeagueRoutes.elGames(round: number)
            case .byClub:
                guard let code = effectiveClub else { return nil }
                return LeagueRoutes.elGames(club: code)
            }
        }
    }

    private var loadKey: MacWorkbookLoadKey {
        MacWorkbookLoadKey(route: route, generation: model.generation(for: league))
    }

    private var roundBinding: Binding<Int> {
        Binding<Int>(
            get: { effectiveRound ?? 0 },
            set: { newValue in
                selectedRound = newValue
            }
        )
    }

    private var clubBinding: Binding<String?> {
        Binding<String?>(
            get: { effectiveClub },
            set: { newValue in
                clubCode = newValue
            }
        )
    }

    private var dayBinding: Binding<Date> {
        Binding<Date>(
            get: { chosenDay },
            set: { newValue in
                chosenDay = newValue
                isLatestDay = false
            }
        )
    }

    // MARK: Rows

    private var rows: [MacGameListRow] {
        switch league {
        case .nba:
            return MacGameListRows.rows(from: nbaBoard)
        case .euroleague:
            return MacGameListRows.rows(from: elGames)
        }
    }

    private var hasPayload: Bool {
        league == .nba ? nbaBoard != nil : elGames != nil
    }

    private var freshness: LeagueFreshness? {
        league == .nba ? nil : (elGames?.freshness ?? elTeams?.freshness)
    }

    private var footerNotes: [String] {
        league == .nba ? [] : (elGames?.notes ?? [])
    }

    private var attribution: String? {
        league == .euroleague ? model.elMeta?.attribution : nil
    }

    /// `Alderwick Herons · 3–1 in EuroLeague games.` and the sentence that says what the record counts.
    private var clubRecordText: String {
        guard league == .euroleague, mode == .byClub, let code = effectiveClub else { return "" }
        let teams: [ElTeamsRow] = elTeams?.teams ?? []
        guard let entry = teams.first(where: { ($0.team?.clubCode ?? $0.team?.id) == code }) else { return "" }
        let name: String = entry.team?.displayName ?? code
        let record: String = LeagueFormatting.record(entry.record)
        let first: String = name + " · " + record + " in EuroLeague games."
        let second: String = "EuroLeague games only: friendlies, domestic and national-team games are not included, so this record differs from the workbook's."
        return first + " " + second
    }

    // MARK: Body

    var body: some View {
        VStack(spacing: 0) {
            MacFilterBar {
                filters
            }
            FreshnessBar(freshness: freshness, isBundledDemo: environment.isDemoMode)
            recordStrip
            staleStrip
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            NotesFooter(notes: footerNotes, attribution: attribution)
        }
        .macScreenTitle(.games, league: league, dataThrough: freshness?.dataThrough)
        .toolbar {
            toolbarContent
        }
        .task(id: loadKey) {
            await refresh()
        }
        .onChange(of: mode) { _, newValue in
            sortOrder = MacBoxScoresScreen.defaultSort(for: newValue)
        }
        .macOnStep { delta in
            step(delta)
        }
    }

    /// A round reads oldest first, a club's latest games newest first.
    private static func defaultSort(for mode: MacGamesMode) -> [KeyPathComparator<MacGameListRow>] {
        switch mode {
        case .byRound:
            return [KeyPathComparator(\MacGameListRow.dateSort)]
        case .byClub:
            return [KeyPathComparator(\MacGameListRow.dateSort, order: .reverse)]
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
            Picker("Show", selection: $mode) {
                ForEach(MacGamesMode.allCases) { option in
                    Text(option.title).tag(option)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 180)
            elScopeControls
            loadingIndicator
        }
    }

    @ViewBuilder private var elScopeControls: some View {
        switch mode {
        case .byRound:
            HStack(spacing: Spacing.sm) {
                stepButton(-1)
                roundPicker
                stepButton(1)
            }
        case .byClub:
            MacTeamPicker(league: league, title: "Club", selection: clubBinding)
        }
    }

    private var nbaFilters: some View {
        HStack(spacing: Spacing.sm) {
            stepButton(-1)
            DatePicker("Date", selection: dayBinding, displayedComponents: .date)
                .datePickerStyle(.field)
                .labelsHidden()
            stepButton(1)
            Button("Latest") {
                isLatestDay = true
            }
            .disabled(isLatestDay)
            loadingIndicator
        }
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
        switch league {
        case .nba:
            return true
        case .euroleague:
            guard mode == .byRound else { return false }
            let numbers: [Int] = roundOptions.map { $0.number }
            guard let current = effectiveRound, let index = numbers.firstIndex(of: current) else { return false }
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
            isLatestDay = false
        case .euroleague:
            let numbers: [Int] = roundOptions.map { $0.number }
            guard let current = effectiveRound, let index = numbers.firstIndex(of: current) else { return }
            selectedRound = numbers[index + delta]
        }
    }

    // MARK: Content

    @ViewBuilder private var recordStrip: some View {
        if !clubRecordText.isEmpty {
            Text(clubRecordText)
                .hardwoodText(.caption)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, Spacing.md)
                .padding(.vertical, Spacing.xs)
        }
    }

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
            waiting
        } else if rows.isEmpty {
            emptyState
        } else {
            splitView
        }
    }

    @ViewBuilder private var waiting: some View {
        if route == nil && league == .euroleague {
            ContentUnavailableView("Nothing to show yet",
                                   systemImage: "sportscourt",
                                   description: Text("The EuroLeague has no rounds or clubs on your server yet."))
        } else {
            MacLoadingView()
        }
    }

    @ViewBuilder private var emptyState: some View {
        switch league {
        case .nba:
            ContentUnavailableView {
                Label("No games on this date", systemImage: "sportscourt")
            } description: {
                Text("Choose another date, or go to the latest day with results.")
            } actions: {
                Button("Latest") {
                    isLatestDay = true
                }
            }
        case .euroleague:
            ContentUnavailableView("No games here",
                                   systemImage: "sportscourt",
                                   description: Text("The server has no games for this choice."))
        }
    }

    private var splitView: some View {
        HSplitView {
            gamesPane
                .frame(minWidth: 380, idealWidth: 540, maxWidth: .infinity)
            boxPane
                .frame(minWidth: 560, idealWidth: 720, maxWidth: .infinity)
        }
    }

    @ViewBuilder private var gamesPane: some View {
        switch league {
        case .euroleague:
            MacElGamesTable(rows: rows,
                            selection: $selection,
                            sortOrder: $sortOrder,
                            onOpenWindow: { id in openBoxWindow(id) },
                            onOpenMatchup: { id in model.open(.matchupGame(id)) })
        case .nba:
            MacNbaGamesTable(rows: rows,
                             selection: $selection,
                             sortOrder: $sortOrder,
                             onOpenWindow: { id in openBoxWindow(id) },
                             onOpenMatchup: { id in model.open(.matchupGame(id)) })
        }
    }

    @ViewBuilder private var boxPane: some View {
        if let id = selection, rows.contains(where: { $0.id == id }) {
            MacBoxScoreView(league: league, gameId: id)
                .id(id)
        } else {
            ContentUnavailableView("Select a game",
                                   systemImage: "sportscourt",
                                   description: Text("Choose a game to see its box score."))
        }
    }

    private func openBoxWindow(_ id: String) {
        selection = id
        let title: String = rows.first(where: { $0.id == id })?.title ?? "Box Score"
        openWindow(id: MacWindowID.boxScore, value: LeagueLink(league: league, id: id, title: title))
    }

    // MARK: Toolbar

    private var copyHeader: [String] {
        league == .nba ? MacGameListRow.nbaCopyHeader : MacGameListRow.elCopyHeader
    }

    private var copyRows: [[String]] {
        let shown: [MacGameListRow] = rows.sorted(using: sortOrder)
        if league == .nba {
            return shown.map { $0.nbaCopyCells }
        }
        return shown.map { $0.elCopyCells }
    }

    @ToolbarContentBuilder private var toolbarContent: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            MacCopyTableButton(header: copyHeader, rows: copyRows)
            Button {
                if let id = selection {
                    openBoxWindow(id)
                }
            } label: {
                Label("Open in New Window", systemImage: "macwindow")
            }
            .help("Open the selected game's box score in its own window")
            .disabled(selection == nil)
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
        guard let wanted = route else {
            if league == .euroleague {
                await fetchRoundList()
            }
            return
        }
        if loadedRoute != wanted {
            elGames = nil
            nbaBoard = nil
            failure = nil
        }
        isLoading = true
        do {
            switch league {
            case .nba:
                let result = try await environment.league.get(ScoreboardResponse.self, wanted)
                if Task.isCancelled { return }
                nbaBoard = result
                syncDay(with: result.date)
            case .euroleague:
                let result = try await environment.league.get(ElGamesList.self, wanted)
                if Task.isCancelled { return }
                elGames = result
            }
            loadedRoute = wanted
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
            isLoading = false
            return
        }
        if league == .euroleague && mode == .byClub {
            await loadClubRecords()
        }
        isLoading = false
    }

    /// While "latest" is showing, the date control shows the day the server chose.
    private func syncDay(with day: String?) {
        guard isLatestDay, let parsed = MacGamesDay.date(from: day) else { return }
        chosenDay = parsed
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

    /// The clubs' records, for the line under the filters. A failure only leaves that line out.
    private func loadClubRecords() async {
        do {
            let table = try await environment.league.get(ElTeamsTable.self, LeagueRoutes.elTeams())
            if Task.isCancelled { return }
            elTeams = table
        } catch {
            if Task.isCancelled { return }
            elTeams = nil
        }
    }
}
#endif
