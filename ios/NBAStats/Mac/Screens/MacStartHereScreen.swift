#if os(macOS)
import SwiftUI

// Start Here: what is loaded, how fresh it is, what is invented, and where to go next.
//
// WHY THIS IS THE FIRST SCREEN
// The first question after opening a stats app is "is this real, and how current?". Day one of a
// season is the hardest time to answer it: the NBA has no games yet, the EuroLeague has played a few
// rounds, and a server that was just installed may be showing an invented demo league. This screen
// answers that before anything else, in the server's own words: for each league its state, the
// reason when it is not simply ready, a badge when its games are invented, the season, how far its
// data runs, and who to credit.
//
// WHAT IT SHOWS, AS CARDS
//   Welcome        choose the NBA team and EuroLeague club you follow (until you finish it)
//   League status  one card per league: state, reason, invented-demo badge, season, data through
//   Day one        the EuroLeague's own notice about what to expect, verbatim
//   Rounds         one chip per EuroLeague round; a click opens that round
//   Next NBA slate the date and how many games have projections
//   Check these    EuroLeague club codes the server could not verify, when there are any
//   What's included  what the data covers and where injuries and headlines come from
//   Quick links    the four screens most used on a game day
//
// WHERE THE DATA COMES FROM
// `MacAppModel` already holds the league list (`/v1/leagues`) and the EuroLeague's meta. This screen
// adds two requests of its own: the NBA's meta (for its attribution line) and the next NBA slate.
// When the model has no EuroLeague meta (a server that was down at launch, or demo mode, where the
// bundled league list marks the EuroLeague as off) the screen asks for it directly, so a card is
// never blank because a different part of the app had a bad moment. A failed request is a line of
// text in its own card; the other cards still draw.
//
// WHAT IT NEVER DOES
// Compute a number. Counts are counts of what the server listed; every other value is formatted
// as sent, with an em dash for anything missing.

private enum MacStartHereKeys {
    static let welcomeDone = "hardwood.mac.welcomeDone"
}

// MARK: - League status

enum MacLeagueStateWords {

    /// A league's state in words. An empty state means the list has not loaded.
    static func word(_ state: String?) -> String {
        guard let state = state, !state.isEmpty else { return "Waiting for your server" }
        switch state {
        case "ready":
            return "Ready"
        case "unavailable":
            return "Not available"
        case "disabled":
            return "Off"
        case "misconfigured":
            return "Not set up correctly"
        case "notConfigured":
            return "Not set up"
        case "error":
            return "Error"
        default:
            return LeagueFormatting.stateWord(state)
        }
    }

    static func tint(_ state: String?) -> Color {
        switch state ?? "" {
        case "ready":
            return Palette.positive
        case "error", "misconfigured":
            return Palette.negative
        case "unavailable", "disabled", "notConfigured":
            return Palette.textTertiary
        default:
            return Palette.neutral
        }
    }
}

/// What a league's status card prints, with every optional already turned into text.
struct MacLeagueStatusInfo {
    var name: String
    var stateText: String
    var reason: String
    var isDemo: Bool
    var season: String
    var dataThrough: String
    var attribution: String
    var problem: String
    var tint: Color
}

struct MacLeagueStatusCard: View {

    private let info: MacLeagueStatusInfo

    init(info: MacLeagueStatusInfo) {
        self.info = info
    }

    private var seasonText: String {
        info.season.isEmpty ? Formatting.emDash : info.season
    }

    var body: some View {
        SectionCard(title: info.name) {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                HStack(spacing: Spacing.sm) {
                    LeagueChip(text: info.stateText, tint: info.tint)
                    if info.isDemo {
                        LeagueChip(text: "Invented demo league", tint: Palette.warning)
                            .help("The games and players in this league are invented. Nothing here is real.")
                    }
                    Spacer(minLength: 0)
                }
                reasonLine
                DetailLine(label: "Season", value: seasonText)
                DetailLine(label: "Data through", value: LeagueFormatting.leagueDate(info.dataThrough))
                attributionLine
                problemLine
            }
        }
    }

    @ViewBuilder private var reasonLine: some View {
        if !info.reason.isEmpty {
            Text(info.reason)
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private var attributionLine: some View {
        if !info.attribution.isEmpty {
            Text(info.attribution)
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private var problemLine: some View {
        if !info.problem.isEmpty {
            LeagueStrip(symbol: "exclamationmark.triangle", text: info.problem, tint: Palette.warning)
        }
    }
}

// MARK: - Round chip

/// One EuroLeague round as a button: `R3 · RS · Result pending`, and when its first game starts.
struct MacRoundChipButton: View {

    private let round: ElRoundSummary
    private let action: () -> Void

    init(round: ElRoundSummary, action: @escaping () -> Void) {
        self.round = round
        self.action = action
    }

    private var numberText: String {
        guard let number = round.round else { return "R?" }
        return "R" + String(number)
    }

    private var headline: String {
        var parts: [String] = [numberText]
        if let phase = round.phase, !phase.isEmpty {
            parts.append(phase)
        }
        parts.append(LeagueFormatting.roundStatusWord(round.status))
        return parts.joined(separator: " · ")
    }

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                Text(headline)
                    .hardwoodText(.tableCell, monospacedDigits: false)
                    .lineLimit(1)
                Text(LeagueFormatting.tipoff(round.firstTipoffUtc))
                    .hardwoodText(.caption)
                    .lineLimit(1)
            }
            .padding(.horizontal, Spacing.sm)
            .padding(.vertical, Spacing.xs)
            .background(RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                .fill(Palette.surfaceSunken))
        }
        .buttonStyle(.plain)
        .help("Open this round")
    }
}

// MARK: - Screen

struct MacStartHereScreen: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    @State private var nbaMeta: MetaResponse?
    @State private var nbaMetaFailure: APIError?
    @State private var localElMeta: ElMeta?
    @State private var elMetaFailure: APIError?
    @State private var nbaSlate: LeagueSlateProjections?
    @State private var slateFailure: APIError?
    @State private var welcomeDone: Bool = UserDefaults.standard.bool(forKey: MacStartHereKeys.welcomeDone)
    @State private var nbaTeamChoice: Int?

    private let columns: [GridItem] = [
        GridItem(.flexible(), spacing: Spacing.lg, alignment: .top),
        GridItem(.flexible(), spacing: Spacing.lg, alignment: .top)
    ]

    init() { }

    // MARK: Body

    var body: some View {
        ScrollView {
            LazyVGrid(columns: columns, alignment: .leading, spacing: Spacing.lg) {
                welcomeCard
                ForEach(LeagueKey.allCases) { key in
                    MacLeagueStatusCard(info: statusInfo(for: key))
                }
                noticeCard
                roundsCard
                slateCard
                unverifiedCard
                includedCard
                linksCard
            }
            .padding(Spacing.lg)
        }
        .macScreenTitle(.startHere,
                        league: model.league,
                        dataThrough: model.info(for: model.league)?.dataThrough)
        .onAppear {
            nbaTeamChoice = environment.favoriteTeamID
        }
        .task(id: MacLoadKey(route: LeagueRoutes.slate(date: "next"),
                             generation: model.generation(for: .nba))) {
            await loadSlate()
        }
        .task(id: environment.isDemoMode) {
            await loadNbaMeta()
        }
        .task(id: model.generation(for: .euroleague)) {
            await loadLocalElMeta()
        }
    }

    // MARK: Data

    /// The EuroLeague's meta: the model's, else this screen's own.
    private var elMeta: ElMeta? {
        model.elMeta ?? localElMeta
    }

    private func statusInfo(for key: LeagueKey) -> MacLeagueStatusInfo {
        switch key {
        case .nba:
            return nbaStatusInfo()
        case .euroleague:
            return euroleagueStatusInfo()
        }
    }

    private func nbaStatusInfo() -> MacLeagueStatusInfo {
        let row: LeagueInfo? = model.info(for: .nba)
        let state: String? = row?.state
        let season: String = row?.currentSeason ?? nbaMeta?.currentSeason ?? ""
        let through: String = row?.dataThrough ?? nbaMeta?.dataThrough ?? ""
        return MacLeagueStatusInfo(name: LeagueKey.nba.displayName,
                                   stateText: MacLeagueStateWords.word(state),
                                   reason: row?.reason ?? "",
                                   isDemo: row?.isDemo == true,
                                   season: season,
                                   dataThrough: through,
                                   attribution: nbaMeta?.attribution ?? "",
                                   problem: nbaMetaFailure?.userMessage ?? "",
                                   tint: MacLeagueStateWords.tint(state))
    }

    private func euroleagueStatusInfo() -> MacLeagueStatusInfo {
        let row: LeagueInfo? = model.info(for: .euroleague)
        let meta: ElMeta? = elMeta
        let state: String? = meta?.state ?? row?.state
        let reason: String = meta?.reason ?? row?.reason ?? ""
        let isDemo: Bool = meta?.isDemo ?? row?.isDemo ?? false
        let season: String = meta?.currentSeason ?? row?.currentSeason ?? ""
        let through: String = meta?.dataThrough ?? row?.dataThrough ?? ""
        let problem: String = meta == nil ? (elMetaFailure?.userMessage ?? "") : ""
        return MacLeagueStatusInfo(name: LeagueKey.euroleague.displayName,
                                   stateText: MacLeagueStateWords.word(state),
                                   reason: reason,
                                   isDemo: isDemo,
                                   season: season,
                                   dataThrough: through,
                                   attribution: meta?.attribution ?? "",
                                   problem: problem,
                                   tint: MacLeagueStateWords.tint(state))
    }

    private func loadNbaMeta() async {
        do {
            let result = try await environment.client.meta()
            if Task.isCancelled { return }
            nbaMeta = result
            nbaMetaFailure = nil
        } catch {
            if Task.isCancelled { return }
            nbaMetaFailure = APIError.from(error)
        }
    }

    /// Asked only when the model has no EuroLeague meta. The meta route answers even when the
    /// league is off: its state and reason are the answer.
    private func loadLocalElMeta() async {
        if model.elMeta != nil {
            return
        }
        do {
            let result = try await environment.league.get(ElMeta.self, LeagueRoutes.elMeta())
            if Task.isCancelled { return }
            localElMeta = result
            elMetaFailure = nil
        } catch {
            if Task.isCancelled { return }
            elMetaFailure = APIError.from(error)
        }
    }

    private func loadSlate() async {
        do {
            let result = try await environment.league.get(LeagueSlateProjections.self,
                                                          LeagueRoutes.slate(date: "next"))
            if Task.isCancelled { return }
            nbaSlate = result
            slateFailure = nil
        } catch {
            if Task.isCancelled { return }
            slateFailure = APIError.from(error)
        }
    }

    // MARK: Welcome

    @ViewBuilder private var welcomeCard: some View {
        if !welcomeDone {
            SectionCard(title: "Welcome") {
                VStack(alignment: .leading, spacing: Spacing.sm) {
                    Text("Choose the teams you follow. Hardwood uses them for your dashboard and for the matchup screens.")
                        .hardwoodText(.tableCell, monospacedDigits: false)
                        .fixedSize(horizontal: false, vertical: true)
                    nbaTeamPicker
                    clubPicker
                    HStack(spacing: Spacing.sm) {
                        Button("Save") {
                            saveWelcome()
                        }
                        .buttonStyle(.bordered)
                        Button("Skip") {
                            finishWelcome()
                        }
                    }
                }
            }
        }
    }

    private var sortedNbaTeams: [TeamRef] {
        model.nbaTeams.sorted { $0.name < $1.name }
    }

    private var nbaTeamPicker: some View {
        Picker("NBA team", selection: $nbaTeamChoice) {
            Text("None").tag(nil as Int?)
            ForEach(sortedNbaTeams) { team in
                Text(team.name).tag(team.teamId as Int?)
            }
        }
        .pickerStyle(.menu)
    }

    private var clubPicker: some View {
        Picker("EuroLeague club", selection: $model.favoriteClubCode) {
            Text("None").tag(nil as String?)
            ForEach(model.elClubs) { club in
                Text(club.name).tag(club.code as String?)
            }
        }
        .pickerStyle(.menu)
    }

    private func saveWelcome() {
        let team: TeamRef? = model.nbaTeams.first(where: { $0.teamId == nbaTeamChoice })
        environment.setFavoriteTeam(team)
        // The club the reader just chose goes straight into the first dashboard's EuroLeague
        // matchup tile (only if that tile's club is still empty; a chosen club is never
        // overwritten). Without this the club would arrive only on the next launch, when
        // bootstrap's seedIfNeeded refills it.
        MacStarterLayout.applyFavorites(store: environment.store,
                                        catalog: environment.catalog,
                                        clubCode: model.favoriteClubCode)
        finishWelcome()
    }

    private func finishWelcome() {
        welcomeDone = true
        UserDefaults.standard.set(true, forKey: MacStartHereKeys.welcomeDone)
    }

    // MARK: Cards

    @ViewBuilder private var noticeCard: some View {
        if let notice = elMeta?.dayOneNotice, !notice.isEmpty {
            SectionCard(title: "EuroLeague: what to expect") {
                Text(notice)
                    .hardwoodText(.tableCell, monospacedDigits: false)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        }
    }

    @ViewBuilder private var roundsCard: some View {
        if let rounds = elMeta?.rounds, !rounds.isEmpty {
            SectionCard(title: "EuroLeague rounds") {
                ScrollView(.horizontal, showsIndicators: PlatformMetrics.showsHorizontalIndicators) {
                    HStack(spacing: Spacing.sm) {
                        ForEach(Array(rounds.enumerated()), id: \.offset) { pair in
                            MacRoundChipButton(round: pair.element) {
                                openRound(pair.element)
                            }
                        }
                    }
                    .padding(.vertical, Spacing.xxs)
                }
            }
        }
    }

    private func openRound(_ round: ElRoundSummary) {
        guard let number = round.round else { return }
        model.league = .euroleague
        model.open(.round(number))
    }

    private var slateCard: some View {
        SectionCard(title: "Next NBA slate") {
            slateBody
        }
    }

    @ViewBuilder private var slateBody: some View {
        if let slate = nbaSlate {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                DetailLine(label: "Date", value: LeagueFormatting.leagueDate(slate.date))
                DetailLine(label: "Games with projections",
                           value: LeagueFormatting.integer((slate.games ?? []).count))
                ForEach(Array((slate.notes ?? []).enumerated()), id: \.offset) { pair in
                    Text(pair.element)
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        } else if let problem = slateFailure {
            Text(problem.userMessage)
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            ProgressView()
                .controlSize(.small)
        }
    }

    @ViewBuilder private var unverifiedCard: some View {
        if let codes = elMeta?.unverifiedClubCodes, !codes.isEmpty {
            SectionCard(title: "Check these club codes") {
                Text("The server could not confirm these club codes against the league's data: "
                     + codes.joined(separator: ", ") + ".")
                    .hardwoodText(.tableCell, monospacedDigits: false)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var includedCard: some View {
        SectionCard(title: "What's included") {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text("EuroLeague games only: friendlies, domestic and national-team games are not included.")
                    .hardwoodText(.tableCell, monospacedDigits: false)
                    .fixedSize(horizontal: false, vertical: true)
                Text(includedSources)
                    .hardwoodText(.tableCell, monospacedDigits: false)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var includedSources: String {
        let injuries = "EuroLeague injuries come from your workbook and the statuses you record; "
        let nba = "NBA injuries come from the league's official report; "
        return injuries + nba + "headlines from the feeds listed in Sources."
    }

    private var linksCard: some View {
        SectionCard(title: "Go to") {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                ForEach(MacStartHereScreen.quickLinks, id: \.self) { screen in
                    Button {
                        model.show(screen)
                    } label: {
                        Label(screen.title(for: model.league), systemImage: screen.icon)
                    }
                    .buttonStyle(.borderless)
                }
            }
        }
    }

    private static let quickLinks: [MacScreen] = [.round, .matchup, .defence, .injuries]
}
#endif
