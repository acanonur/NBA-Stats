#if os(macOS)
import SwiftUI

// One team, in full: its header (record, scoring and what it allows, its next game, who is missing,
// its defence by position) and its players in a sortable table. Also the content of the Club window.
//
// ONE VIEW, TWO LEAGUES
// EuroLeague (the workbook's Team View and Squads sheets): `GET /v1/el/teams/{clubCode}` answers an
// `ElClubView`, whose squad is shown in four column sets (Squad, Season, Per 40, and Projected when
// the server sends the squad's projected per-game line). NBA: `GET /v1/teams/{id}` with the roster's
// metrics (the existing route), plus the team's matchup for the scoring header and the availability
// report for each player's status.
//
// WHERE THE PLAYER CARD LIVES
// The card of the selected player is this view's own inspector, controlled by this view's own
// state, because the view is also the whole content of a window that has no sidebar and no shared
// inspector toggle. Selecting a row (or choosing Show Details) opens it.
//
// WHAT IT NEVER DOES
// It never shows a status nobody reported (an em dash, with one sentence saying why), never draws
// an estimate as an official number, and computes nothing: the record, the averages, the indices and
// the projections are the server's.

struct MacTeamViewContent: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    private let league: LeagueKey
    private let teamId: String

    @State private var club: ElClubView?
    @State private var nbaDetail: TeamDetailResponse?
    @State private var nbaMatchup: LeagueMatchup?
    @State private var nbaReport: LeagueAvailabilityReport?
    @State private var columnSet: MacSquadColumnSet = .squad
    @State private var selection: String?
    @State private var elSortOrder: [KeyPathComparator<MacSquadRow>] = [
        KeyPathComparator(\MacSquadRow.order)
    ]
    @State private var nbaSortOrder: [KeyPathComparator<MacRosterRow>] = [
        KeyPathComparator(\MacRosterRow.order)
    ]
    @State private var isDetailShown = false
    @State private var failure: APIError?
    @State private var isLoading = false
    @State private var loadedRoute: LeagueRoute?
    @State private var playerSheet: PlayerRef?

    init(league: LeagueKey, teamId: String) {
        self.league = league
        self.teamId = teamId
    }

    // MARK: Request

    private var nbaTeamNumber: Int? {
        Int(teamId)
    }

    private var route: LeagueRoute? {
        switch league {
        case .euroleague:
            return LeagueRoutes.elClub(teamId)
        case .nba:
            guard let number = nbaTeamNumber else { return nil }
            return LeagueRoutes.nbaTeam(number, rosterMetrics: NbaRosterKeys.teamView)
        }
    }

    private var loadKey: MacWorkbookLoadKey {
        MacWorkbookLoadKey(route: route, generation: model.generation(for: league))
    }

    private var offReason: String? {
        if environment.isDemoMode {
            return nil
        }
        return model.offReason(for: league)
    }

    // MARK: What is loaded

    private var hasPayload: Bool {
        league == .nba ? nbaDetail != nil : club != nil
    }

    private var squadRows: [MacSquadRow] {
        MacSquadRows.rows(from: club)
    }

    private var rosterRows: [MacRosterRow] {
        MacRosterRows.rows(from: nbaDetail)
    }

    /// The column sets on offer: the Projected set only when the server sent a projected line.
    private var availableSets: [MacSquadColumnSet] {
        var sets: [MacSquadColumnSet] = [.squad, .season, .per40]
        if MacSquadRows.hasProjectedLine(club) {
            sets.append(.projected)
        }
        return sets
    }

    private var effectiveSet: MacSquadColumnSet {
        availableSets.contains(columnSet) ? columnSet : .squad
    }

    /// The matchup's side for this team, when the server has a scheduled game for it.
    private var nbaForm: LeagueTeamForm? {
        let forms: [LeagueTeamForm] = nbaMatchup?.teams ?? []
        let wanted: Int? = Int(teamId)
        return forms.first { $0.team?.id == teamId || $0.team?.teamId == wanted }
    }

    private var freshness: LeagueFreshness? {
        club?.freshness
    }

    private var footerNotes: [String] {
        league == .euroleague ? (club?.notes ?? []) : []
    }

    private var selectedMember: ElSquadMember? {
        guard let id = selection else { return nil }
        let squad: [ElSquadMember] = club?.squad ?? []
        for (index, member) in squad.enumerated() {
            if MacSquadRows.id(of: member, index: index) == id {
                return member
            }
        }
        return nil
    }

    private var selectedRosterRow: MacRosterRow? {
        guard let id = selection else { return nil }
        return rosterRows.first { $0.id == id }
    }

    /// The report entry for a player, preferring one that is still in force.
    private func reportEntry(for playerId: Int) -> LeagueAvailabilityEntry? {
        var found: LeagueAvailabilityEntry?
        for team in nbaReport?.teams ?? [] {
            for entry in team.entries ?? [] {
                guard entry.player?.playerId == playerId else { continue }
                if entry.inForce != false {
                    return entry
                }
                if found == nil {
                    found = entry
                }
            }
        }
        return found
    }

    // MARK: Body

    var body: some View {
        VStack(spacing: 0) {
            content
        }
        .toolbar {
            toolbarContent
        }
        .inspector(isPresented: $isDetailShown) {
            inspectorContent
        }
        .task(id: loadKey) {
            await refresh()
        }
        .sheet(item: $playerSheet) { player in
            MacNbaPlayerSheet(player: player)
                .environmentObject(environment)
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
            staleStrip
            headerArea
            Divider()
            controls
            tableArea
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            statusNote
            FreshnessBar(freshness: freshness, isBundledDemo: environment.isDemoMode)
            NotesFooter(notes: footerNotes, attribution: nil)
        }
    }

    @ViewBuilder private var staleStrip: some View {
        if let problem = failure {
            MacStaleStrip(message: problem.userMessage) {
                retry()
            }
        }
    }

    // MARK: Header

    @ViewBuilder private var headerArea: some View {
        ScrollView {
            switch league {
            case .euroleague:
                if let loadedClub = club {
                    MacClubHeader(club: loadedClub,
                                  onOpenDefense: { model.open(.defenceTeam(teamId)) })
                        .padding(Spacing.md)
                }
            case .nba:
                if let detail = nbaDetail {
                    MacNbaTeamHeader(detail: detail,
                                     form: nbaForm,
                                     leagueAverage: nbaMatchup?.leagueAverage?.pointsPerGame,
                                     game: nbaMatchup?.game,
                                     onOpenDefense: { model.open(.defenceTeam(teamId)) })
                        .padding(Spacing.md)
                }
            }
        }
        .frame(maxHeight: 300)
    }

    // MARK: Controls and table

    private var controls: some View {
        HStack(spacing: Spacing.md) {
            if league == .euroleague {
                Picker("Columns", selection: columnBinding) {
                    ForEach(availableSets) { option in
                        Text(option.title).tag(option)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: segmentWidth)
            }
            if isLoading {
                ProgressView()
                    .controlSize(.small)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, Spacing.md)
        .padding(.vertical, Spacing.sm)
    }

    /// The picker shows the set actually on screen, even when the one chosen is no longer offered.
    private var columnBinding: Binding<MacSquadColumnSet> {
        Binding<MacSquadColumnSet>(
            get: { effectiveSet },
            set: { newValue in
                columnSet = newValue
            }
        )
    }

    private var segmentWidth: CGFloat {
        availableSets.count > 3 ? 320 : 250
    }

    @ViewBuilder private var tableArea: some View {
        switch league {
        case .euroleague:
            if squadRows.isEmpty {
                ContentUnavailableView("No squad listed",
                                       systemImage: "person.crop.rectangle.stack",
                                       description: Text("The server lists no players for this club yet."))
            } else {
                squadTable
            }
        case .nba:
            if rosterRows.isEmpty {
                ContentUnavailableView("No roster listed",
                                       systemImage: "person.crop.rectangle.stack",
                                       description: Text("The server lists no players for this team this season."))
            } else {
                MacRosterTable(rows: rosterRows,
                               selection: $selection,
                               sortOrder: $nbaSortOrder,
                               onShowDetails: { id in showDetails(id) })
            }
        }
    }

    @ViewBuilder private var squadTable: some View {
        switch effectiveSet {
        case .squad:
            MacSquadTable(rows: squadRows,
                          selection: $selection,
                          sortOrder: $elSortOrder,
                          onShowDetails: { id in showDetails(id) })
        case .season:
            MacSquadSeasonTable(rows: squadRows,
                                selection: $selection,
                                sortOrder: $elSortOrder,
                                onShowDetails: { id in showDetails(id) })
        case .per40:
            MacSquadPer40Table(rows: squadRows,
                               selection: $selection,
                               sortOrder: $elSortOrder,
                               onShowDetails: { id in showDetails(id) })
        case .projected:
            MacSquadProjectedTable(rows: squadRows,
                                   selection: $selection,
                                   sortOrder: $elSortOrder,
                                   onShowDetails: { id in showDetails(id) })
        }
    }

    @ViewBuilder private var statusNote: some View {
        if league == .euroleague {
            Text("The workbook marked unlisted players AVAILABLE. Hardwood shows a status only when a source reported one.")
                .hardwoodText(.caption)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, Spacing.md)
                .padding(.vertical, Spacing.xs)
        }
    }

    private func showDetails(_ id: String) {
        selection = id
        isDetailShown = true
    }

    // MARK: Toolbar and inspector

    private var copyHeader: [String] {
        league == .euroleague ? MacSquadRow.copyHeader : MacRosterRow.copyHeader
    }

    private var copyRows: [[String]] {
        switch league {
        case .euroleague:
            let shown: [MacSquadRow] = squadRows.sorted(using: elSortOrder)
            return shown.map { $0.copyCells }
        case .nba:
            let shown: [MacRosterRow] = rosterRows.sorted(using: nbaSortOrder)
            return shown.map { $0.copyCells }
        }
    }

    @ToolbarContentBuilder private var toolbarContent: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            MacCopyTableButton(header: copyHeader, rows: copyRows)
            Button {
                isDetailShown.toggle()
            } label: {
                Label("Player card", systemImage: "sidebar.trailing")
            }
            .help("Show or hide the card of the selected player")
        }
    }

    private var inspectorContent: some View {
        inspectorBody
            .inspectorColumnWidth(min: 280, ideal: 340, max: 460)
    }

    @ViewBuilder private var inspectorBody: some View {
        if league == .euroleague, let member = selectedMember {
            ScrollView {
                MacSquadMemberPanel(member: member,
                                    clubName: club?.team?.displayName ?? Formatting.emDash,
                                    clubAbbr: club?.team?.displayAbbr ?? Formatting.emDash)
                    .padding(Spacing.lg)
            }
        } else if league == .nba, let row = selectedRosterRow, let detail = nbaDetail {
            ScrollView {
                MacRosterMemberPanel(row: row,
                                     entry: reportEntry(for: row.playerRef.playerId),
                                     teamAbbr: detail.team.abbr,
                                     teamName: detail.team.name,
                                     onOpenPlayer: { playerSheet = row.playerRef })
                    .padding(Spacing.lg)
            }
        } else {
            ContentUnavailableView("Select a player",
                                   systemImage: "person",
                                   description: Text("Choose a row to see the player in detail."))
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
            await loadClub()
        case .nba:
            await loadNbaTeam()
        }
    }

    private func loadClub() async {
        let wanted: LeagueRoute = LeagueRoutes.elClub(teamId)
        if loadedRoute != wanted {
            club = nil
            failure = nil
            selection = nil
        }
        isLoading = true
        do {
            let result = try await environment.league.get(ElClubView.self, wanted)
            if Task.isCancelled { return }
            club = result
            loadedRoute = wanted
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
        }
        isLoading = false
    }

    /// The roster first (it is the table), then the matchup and the report, which only add a header
    /// and statuses: if either fails, the roster is still complete.
    private func loadNbaTeam() async {
        guard let wanted = route, let number = nbaTeamNumber else {
            failure = APIError.decoding("This team has no number the server could look up.")
            return
        }
        if loadedRoute != wanted {
            nbaDetail = nil
            nbaMatchup = nil
            nbaReport = nil
            failure = nil
            selection = nil
        }
        isLoading = true
        do {
            let result = try await environment.league.get(TeamDetailResponse.self, wanted)
            if Task.isCancelled { return }
            nbaDetail = result
            loadedRoute = wanted
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
            isLoading = false
            return
        }
        await loadNbaMatchup(String(number))
        await loadNbaReport(String(number))
        isLoading = false
    }

    private func loadNbaMatchup(_ team: String) async {
        do {
            let result = try await environment.league.get(LeagueMatchup.self,
                                                          LeagueRoutes.teamMatchup(.nba, team: team))
            if Task.isCancelled { return }
            nbaMatchup = result
        } catch {
            if Task.isCancelled { return }
            nbaMatchup = nil
        }
    }

    private func loadNbaReport(_ team: String) async {
        do {
            let route = LeagueRoutes.availability(.nba, team: team, date: "next")
            let result = try await environment.league.get(LeagueAvailabilityReport.self, route)
            if Task.isCancelled { return }
            nbaReport = result
        } catch {
            if Task.isCancelled { return }
            nbaReport = nil
        }
    }
}
#endif
