#if os(macOS)
import SwiftUI

// Defence by Position: how many points a team allows to opponents listed at each position, set
// against the league. The second of the two questions this app was built to answer.
//
// WHAT THE SCREEN SHOWS
// - "All teams" (the default): the league table (`MacDefenseTables.swift`), one row per team in the
//   server's order (points allowed per game, lowest first), one column per position. Selecting a
//   row opens that team's full breakdown in the inspector.
// - One team: that team's `DefenseBreakdownView` (headline, a bar per position against the
//   league's tick, shares, the checksum, the coverage bar, the collapsed method details).
// Window (season, last 5, 10, 15), basis (per game, or per regulation minutes), the EuroLeague's
// scheme (three positions or the workbook's five) and the NBA's season are all request parameters:
// the server recomputes, the app never does.
//
// WHAT IT IS CAREFUL TO SAY
// - The caveat is always on screen: these are points scored by players LISTED at a position, not
//   by whoever guarded them. The NBA lists three positions, and says so.
// - A team with too few games is "Withheld", a team with fewer than the league's comfortable number
//   is "Provisional". Both come from the payload; the table and the inspector obey them
//   (`DefenseDisplay`), so a withheld team shows its raw points dimmed and no verdict at all.
// - A season with no games yet says "No games yet this season" and, for the NBA, offers last season
//   from the server's own season list. There is no rank anywhere, and no ordering by a score.
//
// HOW IT LOADS (MAC_DESIGN 6.6)
// One `.task(id:)` keyed on the route and the league's reload counter. A request that differs from
// the last one clears the payload first, so one season's numbers never sit under another's name.
// The inspector loads the selected team's own route the same way, in its own small view, and shows
// the table row's numbers until the fuller answer arrives.

/// The inspector's own load key. The route is optional because nothing is selected at first.
private struct MacDefenseDetailKey: Hashable {
    let route: LeagueRoute?
    let generation: Int
}

struct MacDefenseScreen: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    private let league: LeagueKey

    @State private var teamId: String?
    @State private var window = 0
    @State private var basis = "perGame"
    @State private var scheme = "gfc"
    @State private var season: String?
    @State private var seasonOptions: [String] = []
    @State private var selection: MacDefenseRow.ID?
    @State private var sortOrder: [KeyPathComparator<MacDefenseRow>] = [
        KeyPathComparator(\MacDefenseRow.serverOrder)
    ]
    @State private var payload: LeagueDefenseDocument?
    @State private var failure: APIError?
    @State private var isLoading = false
    @State private var loadedRoute: LeagueRoute?

    init(league: LeagueKey) {
        self.league = league
    }

    // MARK: Request

    /// The NBA is the only league with a season menu; the EuroLeague's server picks its own.
    private var seasonParam: String? {
        league == .nba ? season : nil
    }

    private var route: LeagueRoute {
        if let team = teamId {
            return LeagueRoutes.teamDefense(league,
                                            team: team,
                                            window: window,
                                            basis: basis,
                                            scheme: scheme,
                                            season: seasonParam)
        }
        return LeagueRoutes.defenseTable(league,
                                         window: window,
                                         basis: basis,
                                         scheme: scheme,
                                         season: seasonParam)
    }

    private var perMinute: Bool {
        basis == "perMinute"
    }

    private var perMinuteLabel: String {
        "Per " + String(league.regulationMinutes) + " min"
    }

    /// The table that matches what the server actually answered, not what was asked.
    private var isFiveWay: Bool {
        (payload?.scheme ?? scheme) == "workbook5"
    }

    private var rows: [MacDefenseRow] {
        MacDefenseRows.make(from: payload, perMinute: perMinute, minutes: league.regulationMinutes)
    }

    /// Last season, from the server's own list: the newest one before the season on screen.
    private var lastSeasonOption: String? {
        guard league == .nba, let current = payload?.season else { return nil }
        return seasonOptions.first { $0 < current }
    }

    private var lastSeasonAction: (() -> Void)? {
        guard let last = lastSeasonOption else { return nil }
        return {
            season = last
        }
    }

    // MARK: Body

    var body: some View {
        VStack(spacing: 0) {
            filterBar
            FreshnessBar(freshness: payload?.freshness, isBundledDemo: environment.isDemoMode)
            content
            footer
        }
        .macScreenTitle(MacScreen.defence, league: league, dataThrough: payload?.freshness?.dataThrough)
        .toolbar {
            toolbarContent
        }
        .inspector(isPresented: $model.isInspectorShown) {
            inspectorContent
                .inspectorColumnWidth(min: 280, ideal: 340, max: 460)
        }
        .task(id: MacLoadKey(route: route, generation: model.generation(for: league))) {
            await load(route)
        }
        .task {
            await loadSeasons()
        }
        .macOnPendingNavigation(for: MacScreen.defence) { navigation in
            handle(navigation)
        }
    }

    @ToolbarContentBuilder private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .primaryAction) {
            MacCopyTableButton(header: exportHeader, rows: exportRows)
        }
        ToolbarItem(placement: .primaryAction) {
            MacInspectorToggle()
        }
    }

    // MARK: Filter bar

    private var filterBar: some View {
        MacFilterBar {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                scopeRow
                detailRow
            }
        }
    }

    private var scopeRow: some View {
        HStack(spacing: Spacing.md) {
            MacTeamPicker(league: league, title: "Team", selection: $teamId, noneLabel: "All teams")
            Picker("Window", selection: $window) {
                Text("Season").tag(0)
                Text("Last 5").tag(5)
                Text("Last 10").tag(10)
                Text("Last 15").tag(15)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 260)
            if isLoading {
                ProgressView()
                    .controlSize(.small)
            }
        }
    }

    private var detailRow: some View {
        HStack(spacing: Spacing.md) {
            Picker("Basis", selection: $basis) {
                Text("Per game").tag("perGame")
                Text(perMinuteLabel).tag("perMinute")
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 220)
            leagueSpecificFilter
        }
    }

    @ViewBuilder private var leagueSpecificFilter: some View {
        if league == .euroleague {
            Picker("Positions", selection: $scheme) {
                Text("G · F · C").tag("gfc")
                Text("5 positions (estimated)").tag("workbook5")
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 270)
        } else {
            seasonMenu
        }
    }

    private var seasonMenu: some View {
        Picker("Season", selection: $season) {
            Text("This season").tag(nil as String?)
            ForEach(seasonOptions, id: \.self) { option in
                Text(option).tag(option as String?)
            }
        }
        .pickerStyle(.menu)
        .frame(minWidth: 190)
    }

    // MARK: Content

    @ViewBuilder private var content: some View {
        if let document = payload {
            loaded(document)
        } else if let failure = failure {
            MacLoadFailureView(error: failure, league: league) {
                Task {
                    await load(route)
                }
            }
        } else {
            MacLoadingView()
        }
    }

    @ViewBuilder private func loaded(_ document: LeagueDefenseDocument) -> some View {
        if document.availability == "unavailable" {
            noGames
        } else if document.isLeagueTable {
            leagueTable(document)
        } else {
            teamView(document)
        }
    }

    private var noGames: some View {
        ContentUnavailableView {
            Label("No games yet this season", systemImage: "sportscourt")
        } description: {
            Text("Points allowed by position appear once teams have played.")
        } actions: {
            if let action = lastSeasonAction {
                Button("Show last season") {
                    action()
                }
            }
        }
    }

    @ViewBuilder private var staleStrip: some View {
        if let failure = failure {
            MacStaleStrip(message: failure.userMessage) {
                Task {
                    await load(route)
                }
            }
        }
    }

    // MARK: League table

    private func leagueTable(_ document: LeagueDefenseDocument) -> some View {
        VStack(spacing: 0) {
            staleStrip
            MacDefenseTableCaption(document: document, perMinute: perMinute, minutes: league.regulationMinutes)
            MacDefenseTableNotes(document: document, rows: rows)
            if rows.isEmpty {
                ContentUnavailableView("No team has a breakdown yet", systemImage: "shield.lefthalf.filled")
            } else {
                tableView
            }
        }
    }

    @ViewBuilder private var tableView: some View {
        if isFiveWay {
            MacDefenseTable5(rows: rows,
                             selection: $selection,
                             sortOrder: $sortOrder,
                             onOpenMatchup: { id in openMatchup(id) },
                             onShowDetails: { id in showDetails(id) })
        } else {
            MacDefenseTableGFC(rows: rows,
                               selection: $selection,
                               sortOrder: $sortOrder,
                               onOpenMatchup: { id in openMatchup(id) },
                               onShowDetails: { id in showDetails(id) })
        }
    }

    private func showDetails(_ id: MacDefenseRow.ID) {
        selection = id
        model.isInspectorShown = true
    }

    // MARK: One team

    private func teamView(_ document: LeagueDefenseDocument) -> some View {
        VStack(spacing: 0) {
            staleStrip
            ScrollView {
                VStack(alignment: .leading, spacing: Spacing.lg) {
                    LeagueTeamLabel(team: document.team, size: .large, showsName: true)
                    MacDefenseBasisNote(perMinute: perMinute, minutes: league.regulationMinutes)
                    DefenseBreakdownView(document: document,
                                         density: .full,
                                         onShowLastSeason: lastSeasonAction,
                                         showsCaveat: false)
                }
                .padding(Spacing.lg)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    // MARK: Footer

    private var caveatExtra: String? {
        league == .nba ? "The NBA lists players as guards, forwards or centres, so three positions are shown." : nil
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 0) {
            CaveatFooter(text: payload?.caveat ?? MacDefenseScreen.fallbackCaveat, extra: caveatExtra)
                .padding(.horizontal, Spacing.md)
                .padding(.vertical, Spacing.xs)
            NotesFooter(notes: payload?.notes)
        }
    }

    /// The server's own sentence, for the moments before a payload has arrived.
    private static let fallbackCaveat =
        "Counts points scored by opposing players listed at each position. It does not measure who guarded whom."

    // MARK: Inspector

    private var selectedTeamRow: LeagueDefenseTeamRow? {
        guard let id = selection, let teams = payload?.teams else { return nil }
        for row in teams where row.team?.id == id {
            return row
        }
        return nil
    }

    @ViewBuilder private var inspectorContent: some View {
        if let document = payload, document.isLeagueTable {
            MacDefenseInspector(league: league,
                                document: document,
                                row: selectedTeamRow,
                                window: window,
                                basis: basis,
                                scheme: scheme,
                                season: seasonParam,
                                onShowLastSeason: lastSeasonAction)
        } else {
            MacDefenseReadingGuide(showsSelectHint: false)
        }
    }

    // MARK: Copy Table

    private var exportHeader: [String] {
        isFiveWay ? MacDefenseColumns.fiveHeader : MacDefenseColumns.gfcHeader
    }

    /// The rows in the order the table shows them (the reader's sort), as text.
    private var exportRows: [[String]] {
        guard let document = payload, document.isLeagueTable else { return [] }
        let shown: [MacDefenseRow] = rows.sorted(using: sortOrder)
        let fiveWay = isFiveWay
        return shown.map { $0.exportCells(fiveWay: fiveWay) }
    }

    // MARK: Requests from elsewhere

    /// "Open Matchup" on a team row (MAC_DESIGN 3.1): that team's next game on the Matchup screen.
    /// A row whose team had no id carries a made-up `row-N` id, which no route knows, so it does
    /// nothing.
    private func openMatchup(_ id: MacDefenseRow.ID) {
        guard !id.hasPrefix("row-") else { return }
        model.open(MacNavigation.matchupTeam(id))
    }

    /// "Open Defence" on a matchup arrives here carrying a team.
    private func handle(_ navigation: MacNavigation) {
        if case .defenceTeam(let id) = navigation {
            teamId = id
        }
    }

    // MARK: Loading

    private func load(_ route: LeagueRoute) async {
        if loadedRoute != route {
            payload = nil
            failure = nil
        }
        isLoading = true
        do {
            let result = try await environment.league.get(LeagueDefenseDocument.self, route)
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

    /// The NBA's loaded seasons, newest first, for the season menu and Show last season. The screen
    /// does not depend on it: a failure leaves the menu with only This season.
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

// MARK: - Notes above the table

/// One sentence under the filters saying what the table's numbers are: the league's average, the
/// window, the positions and the basis, all as the payload states them.
private struct MacDefenseTableCaption: View {
    private let document: LeagueDefenseDocument
    private let perMinute: Bool
    private let minutes: Int

    init(document: LeagueDefenseDocument, perMinute: Bool, minutes: Int) {
        self.document = document
        self.perMinute = perMinute
        self.minutes = minutes
    }

    private var windowText: String {
        guard let window = document.window else { return "" }
        if window.kind == "lastGames", let asked = window.requested {
            return "Each team's last " + String(asked) + " games (GP shows how many counted)"
        }
        if window.kind == "season" {
            return "Season to date"
        }
        return ""
    }

    private var text: String {
        var parts: [String] = []
        if let average = document.leaguePointsAllowedPerGame {
            parts.append("League average " + LeagueFormatting.number(average) + " allowed per game")
        }
        parts.append(windowText)
        if let scheme = document.scheme {
            parts.append(LeagueFormatting.schemeWord(scheme))
        }
        if perMinute {
            parts.append("positions in points per " + String(minutes) + " opponent minutes")
        }
        return parts.filter { !$0.isEmpty }.joined(separator: " · ")
    }

    var body: some View {
        if !text.isEmpty {
            Text(text)
                .hardwoodText(.caption)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Spacing.md)
                .padding(.vertical, Spacing.xs)
        }
    }
}

/// The cautions that apply to the table as a whole, each drawn only when the payload says so.
private struct MacDefenseTableNotes: View {
    private let document: LeagueDefenseDocument
    private let rows: [MacDefenseRow]

    init(document: LeagueDefenseDocument, rows: [MacDefenseRow]) {
        self.document = document
        self.rows = rows
    }

    private var anyWithheld: Bool {
        rows.contains { $0.status == MacDefenseStatus.withheld }
    }

    private var anyProvisional: Bool {
        rows.contains { $0.status == MacDefenseStatus.provisional }
    }

    private var isEstimated: Bool {
        document.availability == "estimated" || document.scheme == "workbook5"
    }

    private var signalText: String {
        if let message = document.methodMessage, !message.isEmpty { return message }
        if document.method?.leagueSignal == "none detected" {
            return "No team's points allowed at this position differ from the league by more than chance this season."
        }
        return ""
    }

    private var hasContent: Bool {
        anyWithheld || anyProvisional || isEstimated || !signalText.isEmpty
    }

    var body: some View {
        if hasContent {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                if anyWithheld {
                    LeagueStrip(symbol: "exclamationmark.triangle",
                                text: "Teams marked Withheld cannot be judged against the league yet; hover the chip for why. No band is shown for them, and a team with too few games shows no differences at all.",
                                tint: Palette.warning)
                }
                if anyProvisional {
                    LeagueStrip(symbol: "hourglass",
                                text: "Provisional means few games so far: these numbers can still move a lot, and no team is called better or worse yet.",
                                tint: Palette.neutral)
                }
                estimatedRow
                if !signalText.isEmpty {
                    Text(signalText)
                        .hardwoodText(.caption, color: Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.horizontal, Spacing.md)
            .padding(.vertical, Spacing.xs)
        }
    }

    @ViewBuilder private var estimatedRow: some View {
        if isEstimated {
            HStack(spacing: Spacing.sm) {
                EstimatedBadge()
                if document.scheme == "workbook5" {
                    Text("Five-position labels come from your workbook's listings.")
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
        }
    }
}

/// On the per-minute basis the bands use a rate, while the bars and differences next to them stay
/// per game. Say so rather than let the two units look like one.
private struct MacDefenseBasisNote: View {
    private let perMinute: Bool
    private let minutes: Int

    init(perMinute: Bool, minutes: Int) {
        self.perMinute = perMinute
        self.minutes = minutes
    }

    var body: some View {
        if perMinute {
            Text("The bands use points per " + String(minutes) + " opponent minutes. The bars and differences below are points per game.")
                .hardwoodText(.caption, color: Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - Inspector

/// What the inspector says when no team is selected, and always for the one-team view: how to
/// read a position bar.
private struct MacDefenseReadingGuide: View {
    private let showsSelectHint: Bool

    init(showsSelectHint: Bool) {
        self.showsSelectHint = showsSelectHint
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.md) {
                Text("How to read this")
                    .hardwoodText(.sectionTitle)
                if showsSelectHint {
                    guide("Select a team in the table to see where its points allowed go.")
                }
                guide("Each bar is the points per game a team allows to opponents listed at that position. The tick on the bar is the league's average.")
                guide("Better and Worse appear only when a team has enough games for the difference to mean something. Until then the differences are grey, or hidden.")
                guide("Positions are the ones each roster lists. This is not who guarded whom.")
            }
            .padding(Spacing.lg)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func guide(_ text: String) -> some View {
        Text(text)
            .hardwoodText(.tableCell, monospacedDigits: false)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// The selected team's full breakdown. It starts from the table row's own numbers and replaces
/// them with the team route's answer (which adds the coverage and the checksum) when it arrives.
private struct MacDefenseInspector: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    private let league: LeagueKey
    private let document: LeagueDefenseDocument
    private let row: LeagueDefenseTeamRow?
    private let window: Int
    private let basis: String
    private let scheme: String
    private let season: String?
    private let onShowLastSeason: (() -> Void)?

    @State private var detail: LeagueDefenseDocument?
    @State private var loadedRoute: LeagueRoute?

    init(league: LeagueKey,
         document: LeagueDefenseDocument,
         row: LeagueDefenseTeamRow?,
         window: Int,
         basis: String,
         scheme: String,
         season: String?,
         onShowLastSeason: (() -> Void)?) {
        self.league = league
        self.document = document
        self.row = row
        self.window = window
        self.basis = basis
        self.scheme = scheme
        self.season = season
        self.onShowLastSeason = onShowLastSeason
    }

    private var detailRoute: LeagueRoute? {
        guard let id = row?.team?.id, !id.isEmpty else { return nil }
        return LeagueRoutes.teamDefense(league,
                                        team: id,
                                        window: window,
                                        basis: basis,
                                        scheme: scheme,
                                        season: season)
    }

    /// The fuller answer once it is for this team, else the table row folded into the table's
    /// shared fields.
    private var shown: LeagueDefenseDocument? {
        if let full = detail, loadedRoute == detailRoute { return full }
        guard let row = row else { return nil }
        return document.focused(on: row)
    }

    var body: some View {
        if let current = shown, let selected = row {
            ScrollView {
                VStack(alignment: .leading, spacing: Spacing.lg) {
                    LeagueTeamLabel(team: selected.team, size: .large, showsName: true)
                    MacDefenseBasisNote(perMinute: basis == "perMinute", minutes: league.regulationMinutes)
                    DefenseBreakdownView(document: current,
                                         density: .full,
                                         onShowLastSeason: onShowLastSeason,
                                         showsCaveat: false)
                }
                .padding(Spacing.lg)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .task(id: MacDefenseDetailKey(route: detailRoute, generation: model.generation(for: league))) {
                await loadDetail()
            }
        } else {
            MacDefenseReadingGuide(showsSelectHint: true)
        }
    }

    private func loadDetail() async {
        guard let route = detailRoute else {
            detail = nil
            loadedRoute = nil
            return
        }
        if loadedRoute != route {
            detail = nil
        }
        do {
            let result = try await environment.league.get(LeagueDefenseDocument.self, route)
            if Task.isCancelled { return }
            detail = result
            loadedRoute = route
        } catch {
            if Task.isCancelled { return }
            // The row's own numbers stay on screen; the fuller breakdown is simply not added.
        }
    }
}
#endif
