#if os(macOS)
import SwiftUI

// Matchup: two teams side by side, with what each scored last, what each averages, and what each
// lets opponents score. The first of the two questions this app was built to answer.
//
// WHAT IS ON SCREEN
// For each side (home on the left, away on the right) the shared `MatchupSideColumn` draws, from
// the server's `LeagueTeamForm`: the latest score with its opponent and where it was played, points
// scored and points allowed per game against the league's average, the last games as a chart and a
// list, the last 5 and last 10 averages, home and away splits, how the team did against these
// opponents compared with what they usually do, who is missing, and its defence by position. A game
// strip names the game, its time and its projected score. The inspector holds the full projection.
//
// THREE WAYS IN, ONE PAYLOAD (`LeagueMatchup`)
//   Next game    `teams/{id}/matchup`: a team's next scheduled game. A team with none is a 404
//                `game_not_found`, shown as a plain "No scheduled game" with a way to choose two.
//   Two teams    `matchups?homeTeamId=&awayTeamId=`: any pair. The game (and projection) appear only
//                when the two are scheduled to meet with those sides. This screen offers a season
//                choice for the NBA only; it is also how "Show last season" works, because the
//                next-game and one-game routes take no season.
//   One game     `games/{id}/matchup`, reached from the Round or Slate screen: every input cut off
//                before that game started.
//
// THE APP COMPUTES NOTHING HERE
// Averages, margins, adjusted values and the league reference are all fields of the payload. The
// toggle for per-regulation-minutes swaps which served field is drawn; the stepper changes the
// `window` the server is asked for. A team with no games yet shows "No games yet this season"
// (never zeros), and the NBA offers last season from the server's own season list.
//
// HOW IT LOADS (MAC_DESIGN 6.6)
// One `.task(id:)` keyed on the request and the league's reload counter. Changing the request clears
// the payload first, so one team's numbers never sit under another's name.

/// What the filter bar's mode picker chooses between.
private enum MacMatchupMode: Hashable {
    case nextGame
    case twoTeams
}

/// The screen's `.task(id:)` key. The route is optional because a team may not be chosen yet.
private struct MacMatchupLoadKey: Hashable {
    let route: LeagueRoute?
    let generation: Int
}

/// One side of the game, in the place it is drawn.
private struct MacMatchupSlot: Identifiable {
    let id: String
    let heading: String
    let form: LeagueTeamForm
}

struct MacMatchupScreen: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel
    @Environment(\.openWindow) private var openWindow

    private let league: LeagueKey

    @State private var mode: MacMatchupMode = .nextGame
    @State private var teamId: String?
    @State private var homeId: String?
    @State private var awayId: String?
    @State private var gameId: String?
    @State private var window = 5
    @State private var perRegulation = false
    @State private var season: String?
    @State private var seasonOptions: [String] = []
    @State private var payload: LeagueMatchup?
    @State private var failure: APIError?
    @State private var isLoading = false
    @State private var loadedRoute: LeagueRoute?

    init(league: LeagueKey) {
        self.league = league
    }

    // MARK: Request

    private var seasonParam: String? {
        league == .nba ? season : nil
    }

    private var currentRoute: LeagueRoute? {
        if let game = gameId {
            return LeagueRoutes.gameMatchup(league, gameId: game, window: window)
        }
        switch mode {
        case .nextGame:
            guard let team = teamId else { return nil }
            return LeagueRoutes.teamMatchup(league, team: team, window: window)
        case .twoTeams:
            guard let home = homeId, let away = awayId, home != away else { return nil }
            return LeagueRoutes.pairMatchup(league,
                                            home: home,
                                            away: away,
                                            window: window,
                                            season: seasonParam)
        }
    }

    /// True when the screen shows one team's next game (not one game, not a pair of teams).
    private var isNextGameMode: Bool {
        gameId == nil && mode == .nextGame
    }

    /// True when the screen shows two chosen teams (not one game, not a team's next game).
    private var isPairMode: Bool {
        gameId == nil && mode == .twoTeams
    }

    private var promptText: String {
        switch mode {
        case .nextGame:
            return "Choose a team to see its next game."
        case .twoTeams:
            if homeId != nil && homeId == awayId {
                return "Choose two different teams."
            }
            return "Choose a home team and an away team."
        }
    }

    private var windowLabel: String {
        "Last " + String(window) + " games"
    }

    private var regulationLabel: String {
        "Per " + String(league.regulationMinutes) + " min"
    }

    private var regulationHelp: String {
        "Scale each game to " + String(league.regulationMinutes) + " minutes, so overtime does not inflate points."
    }

    private var favoriteTeamId: String? {
        switch league {
        case .nba:
            guard let id = environment.favoriteTeamID else { return nil }
            return String(id)
        case .euroleague:
            return model.favoriteClubCode
        }
    }

    // MARK: Body

    var body: some View {
        VStack(spacing: 0) {
            filterBar
            FreshnessBar(freshness: payload?.freshness, isBundledDemo: environment.isDemoMode)
            content
            NotesFooter(notes: payload?.notes)
        }
        .macScreenTitle(MacScreen.matchup, league: league, dataThrough: payload?.freshness?.dataThrough)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                MacInspectorToggle()
            }
        }
        .inspector(isPresented: $model.isInspectorShown) {
            inspectorContent
                .inspectorColumnWidth(min: 280, ideal: 340, max: 460)
        }
        .task(id: MacMatchupLoadKey(route: currentRoute, generation: model.generation(for: league))) {
            await load()
        }
        .task {
            await loadSeasons()
        }
        .onAppear {
            seedDefaults()
        }
        .onChange(of: mode) { _, _ in
            gameId = nil
        }
        .macOnPendingNavigation(for: MacScreen.matchup) { navigation in
            handle(navigation)
        }
    }

    // MARK: Filter bar

    private var filterBar: some View {
        MacFilterBar {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                modeRow
                optionsRow
            }
        }
    }

    private var modeRow: some View {
        HStack(spacing: Spacing.md) {
            Picker("Mode", selection: $mode) {
                Text("Next game").tag(MacMatchupMode.nextGame)
                Text("Two teams").tag(MacMatchupMode.twoTeams)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 200)
            teamPickers
            if isLoading {
                ProgressView()
                    .controlSize(.small)
            }
        }
    }

    @ViewBuilder private var teamPickers: some View {
        switch mode {
        case .nextGame:
            MacTeamPicker(league: league, title: "Team", selection: teamBinding, noneLabel: "Choose a team")
        case .twoTeams:
            MacTeamPicker(league: league, title: "Home", selection: homeBinding, noneLabel: "Choose a team")
            Button {
                swapTeams()
            } label: {
                Image(systemName: "arrow.left.arrow.right")
            }
            .buttonStyle(.borderless)
            .help("Swap home and away")
            MacTeamPicker(league: league, title: "Away", selection: awayBinding, noneLabel: "Choose a team")
        }
    }

    // Choosing a team in a picker leaves the one-game view (a game opened from the Round or Slate
    // screen) and goes back to the teams. A change the screen makes itself, such as filling in the
    // favourite team on first appearance, goes straight to the state and does not.

    private var teamBinding: Binding<String?> {
        Binding<String?>(get: { teamId }, set: { newValue in
            teamId = newValue
            gameId = nil
        })
    }

    private var homeBinding: Binding<String?> {
        Binding<String?>(get: { homeId }, set: { newValue in
            homeId = newValue
            gameId = nil
        })
    }

    private var awayBinding: Binding<String?> {
        Binding<String?>(get: { awayId }, set: { newValue in
            awayId = newValue
            gameId = nil
        })
    }

    private var optionsRow: some View {
        HStack(spacing: Spacing.lg) {
            Stepper(value: $window, in: 3...15) {
                Text(windowLabel)
            }
            Toggle(regulationLabel, isOn: $perRegulation)
                .help(regulationHelp)
            seasonFilter
        }
    }

    /// Only the NBA has a season to choose, and only the two-team route takes one.
    @ViewBuilder private var seasonFilter: some View {
        if league == .nba && isPairMode {
            Picker("Season", selection: $season) {
                Text("This season").tag(nil as String?)
                ForEach(seasonOptions, id: \.self) { option in
                    Text(option).tag(option as String?)
                }
            }
            .pickerStyle(.menu)
            .frame(minWidth: 190)
        }
    }

    // MARK: Content

    @ViewBuilder private var content: some View {
        if currentRoute == nil {
            ContentUnavailableView("Choose a team",
                                   systemImage: "rectangle.split.2x1",
                                   description: Text(promptText))
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let matchup = payload {
            loaded(matchup)
        } else if let error = failure {
            failureView(error)
        } else {
            MacLoadingView()
        }
    }

    @ViewBuilder private var staleStrip: some View {
        if let error = failure {
            MacStaleStrip(message: error.userMessage) {
                Task {
                    await load()
                }
            }
        }
    }

    @ViewBuilder private func failureView(_ error: APIError) -> some View {
        if error.rawCode == "game_not_found" && isNextGameMode {
            ContentUnavailableView {
                Label("No scheduled game for " + teamName(teamId), systemImage: "calendar")
            } description: {
                Text(error.userMessage)
            } actions: {
                Button("Choose two teams") {
                    chooseTwoTeams()
                }
            }
        } else {
            MacLoadFailureView(error: error, league: league) {
                Task {
                    await load()
                }
            }
        }
    }

    private func loaded(_ matchup: LeagueMatchup) -> some View {
        VStack(spacing: 0) {
            staleStrip
            ScrollView {
                VStack(alignment: .leading, spacing: Spacing.lg) {
                    gameModeStrip
                    gameStrip(matchup)
                    noticeStrips(matchup)
                    columns(matchup)
                }
                .padding(Spacing.lg)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    @ViewBuilder private var gameModeStrip: some View {
        if gameId != nil {
            HStack(spacing: Spacing.sm) {
                LeagueStrip(symbol: "clock.arrow.circlepath",
                            text: "This is one game's matchup, with every input cut off before it started.",
                            tint: Palette.neutral)
                Button("Back to teams") {
                    gameId = nil
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
        }
    }

    @ViewBuilder private func gameStrip(_ matchup: LeagueMatchup) -> some View {
        if matchup.game == nil && isPairMode {
            Text("These two teams are not scheduled to meet with these sides.")
                .hardwoodText(.caption)
        } else {
            MatchupGameLine(game: matchup.game, league: league, summary: matchup.projection?.summary)
        }
    }

    // MARK: Notices

    /// What the server's `availability` means for a matchup, in words. Not the metric-era wording
    /// of `AvailabilityBadge`, which describes possession data and would misread here.
    private func availabilityText(_ matchup: LeagueMatchup) -> String {
        switch matchup.availability ?? "" {
        case "partial":
            return "Partial: some figures are built from fewer games than asked for, or are not available yet. The notes below say which."
        case "estimated":
            return "Estimated: the defence summary rests on the workbook's position labels, not the league's own."
        case "unavailable":
            return "No games have been played yet, so there are no scoring figures."
        default:
            return ""
        }
    }

    @ViewBuilder private func noticeStrips(_ matchup: LeagueMatchup) -> some View {
        let note = availabilityText(matchup)
        if !note.isEmpty {
            HStack(spacing: Spacing.sm) {
                LeagueStrip(symbol: "info.circle", text: note, tint: Palette.neutral)
                lastSeasonButton(matchup)
            }
        }
    }

    @ViewBuilder private func lastSeasonButton(_ matchup: LeagueMatchup) -> some View {
        if matchup.availability == "unavailable" && lastSeason(before: matchup.season) != nil {
            Button("Show last season") {
                showLastSeason(for: matchup)
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
    }

    /// The newest loaded season before the one on screen, from the server's list (NBA only).
    private func lastSeason(before current: String?) -> String? {
        guard league == .nba, let current = current else { return nil }
        return seasonOptions.first { $0 < current }
    }

    /// The same two teams, in last season: the two-team route is the only one that takes a season.
    private func showLastSeason(for matchup: LeagueMatchup) {
        guard let last = lastSeason(before: matchup.season) else { return }
        let found = MacMatchupScreen.slots(from: matchup.teams ?? [])
        guard found.count == 2 else { return }
        homeId = found[0].form.team?.id
        awayId = found[1].form.team?.id
        season = last
        gameId = nil
        mode = .twoTeams
    }

    // MARK: Columns

    /// Home on the left, away on the right: by the payload's `side`, else by its order.
    private static func slots(from forms: [LeagueTeamForm]) -> [MacMatchupSlot] {
        var remaining = forms
        var home: LeagueTeamForm?
        var away: LeagueTeamForm?
        if let index = remaining.firstIndex(where: { $0.side == "home" }) {
            home = remaining.remove(at: index)
        }
        if let index = remaining.firstIndex(where: { $0.side == "away" }) {
            away = remaining.remove(at: index)
        }
        if home == nil && !remaining.isEmpty {
            home = remaining.removeFirst()
        }
        if away == nil && !remaining.isEmpty {
            away = remaining.removeFirst()
        }
        var result: [MacMatchupSlot] = []
        if let form = home {
            result.append(MacMatchupSlot(id: "home", heading: "Home", form: form))
        }
        if let form = away {
            result.append(MacMatchupSlot(id: "away", heading: "Away", form: form))
        }
        return result
    }

    private func columns(_ matchup: LeagueMatchup) -> some View {
        let found = MacMatchupScreen.slots(from: matchup.teams ?? [])
        let average = matchup.leagueAverage?.pointsPerGame
        return HStack(alignment: .top, spacing: Spacing.lg) {
            ForEach(found) { slot in
                column(slot, average: average)
            }
        }
    }

    private func column(_ slot: MacMatchupSlot, average: Double?) -> some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            Text(slot.heading)
                .hardwoodText(.statLabel)
            MatchupSideColumn(team: slot.form,
                              density: .full,
                              leagueAverage: average,
                              usesRegulation: perRegulation,
                              regulationMinutes: league.regulationMinutes,
                              includesExtras: false,
                              onOpenGame: { id in
                                  openBoxScore(game: id)
                              },
                              onOpenDefense: defenseAction(for: slot.form))
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .hardwoodCard()
    }

    /// "Open Defence" carries the team to the Defence screen.
    private func defenseAction(for form: LeagueTeamForm) -> (() -> Void)? {
        guard let id = form.team?.id, !id.isEmpty else { return nil }
        return {
            model.open(MacNavigation.defenceTeam(id))
        }
    }

    // MARK: Inspector

    @ViewBuilder private var inspectorContent: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.lg) {
                if let projection = payload?.projection {
                    GameProjectionCard(projection: projection,
                                       league: league,
                                       onOpenBoxScore: boxScoreAction(for: projection))
                } else if payload != nil {
                    Text("No projection for this pairing")
                        .hardwoodText(.tableCell, monospacedDigits: false)
                } else {
                    Text("Choose a team to see the projected score here.")
                        .hardwoodText(.tableCell, monospacedDigits: false)
                }
            }
            .padding(Spacing.lg)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // MARK: Box score windows

    /// The projection card's "Open Box Score" button, when the game has an id.
    private func boxScoreAction(for projection: LeagueGameProjection) -> (() -> Void)? {
        guard let id = projection.game?.gameId, !id.isEmpty else { return nil }
        return {
            openBoxScore(game: id)
        }
    }

    private func openBoxScore(game id: String) {
        let link = LeagueLink(league: league, id: id, title: boxScoreTitle(forGame: id))
        openWindow(id: MacWindowID.boxScore, value: link)
    }

    /// `ZZA at ZZQ` for a game found in either team's form, else plain "Box score".
    private func boxScoreTitle(forGame id: String) -> String {
        let forms: [LeagueTeamForm] = payload?.teams ?? []
        for team in forms {
            for game in team.form ?? [] where game.gameId == id {
                let mark = LeagueFormatting.venueMark(isHome: game.isHome, isNeutral: game.isNeutral)
                let ownCode = team.team?.displayAbbr ?? Formatting.emDash
                let opponentCode = game.opponent?.displayAbbr ?? Formatting.emDash
                let words: [String] = [ownCode, mark, opponentCode]
                return words.filter { !$0.isEmpty }.joined(separator: " ")
            }
        }
        return "Box score"
    }

    // MARK: Choices

    private func swapTeams() {
        let first = homeId
        homeId = awayId
        awayId = first
    }

    private func chooseTwoTeams() {
        homeId = teamId
        awayId = nil
        mode = .twoTeams
    }

    private func teamName(_ id: String?) -> String {
        guard let id = id else { return "this team" }
        for option in model.teamOptions(for: league) where option.id == id {
            return option.name.isEmpty ? option.abbr : option.name
        }
        return id
    }

    /// The team the reader follows, until they choose another.
    private func seedDefaults() {
        guard teamId == nil && homeId == nil else { return }
        let favorite = favoriteTeamId
        teamId = favorite
        homeId = favorite
    }

    /// "Open Matchup" on a game of the Round or Slate screen arrives here carrying a game id; the
    /// Defence table's "Open Matchup" carries a team, shown as that team's next game.
    private func handle(_ navigation: MacNavigation) {
        switch navigation {
        case .matchupGame(let id):
            gameId = id
        case .matchupTeam(let id):
            mode = MacMatchupMode.nextGame
            teamId = id
            gameId = nil
        case .defenceTeam, .round:
            break
        }
    }

    // MARK: Loading

    private func load() async {
        guard let route = currentRoute else {
            payload = nil
            failure = nil
            loadedRoute = nil
            isLoading = false
            return
        }
        if loadedRoute != route {
            payload = nil
            failure = nil
        }
        isLoading = true
        do {
            let result = try await environment.league.get(LeagueMatchup.self, route)
            if Task.isCancelled { return }
            payload = result
            loadedRoute = route
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
        }
        isLoading = false
    }

    /// The NBA's loaded seasons, newest first. A failure only leaves the season menu short.
    private func loadSeasons() async {
        guard league == .nba else { return }
        do {
            let meta = try await environment.client.meta()
            if Task.isCancelled { return }
            let names: [String] = meta.seasons.map { $0.season }
            seasonOptions = names.sorted(by: >)
        } catch {
            if Task.isCancelled { return }
            seasonOptions = []
        }
    }
}
#endif
