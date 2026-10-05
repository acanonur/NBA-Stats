#if os(macOS)
import SwiftUI

// Injuries & News (NBA) and Injury Report & News (EuroLeague): who is out, doubtful, questionable
// or probable, where every status came from and how old it is, the headlines the server has, and
// the report rows that matched nobody.
//
// THREE TABS, THREE ROUTES
//   Injuries       GET {prefix}/availability   every status, grouped by team, each with its source
//   Headlines      GET {prefix}/news           title, link, date and outlet; never an article body
//   Needs review   GET {prefix}/availability/review-queue   report rows whose player matched nobody
// The three loads are independent: a failure in one is that tab's message, and the others stay.
// The injury report is asked for again every five minutes while the screen is open, and whenever
// the league's cursor moves, a write succeeds or the reader presses Refresh.
//
// PROVENANCE IS NOT OPTIONAL
// A row without a source would be a claim with no one behind it, so the table, the inspector and the
// headline list all show who said it and how long ago THEY said it (never when Hardwood fetched
// it). A status nobody has reported is "No report", never "Available". The server's own state for
// the report (stale, no report yet, unreadable, off) is a banner with its message; when the report
// is fresh the server's sentence is printed at the foot, because it says the statuses are dated
// research and none of them is a live feed.
//
// WRITES, AND WHO MAY MAKE THEM
// "Record Status…" and "Paste Link…" open sheets that post to the server with its API key (the app
// reads it from the installer's settings file at launch). "Retract" is offered only for an entry a
// person typed: the EuroLeague's manual statuses (the server appends a retraction) and the NBA's
// overrides (the server clears them); it asks first. A write that succeeds bumps the league's reload
// counter, so every screen showing the league reloads.

enum MacInjuryTab: String, CaseIterable, Identifiable, Hashable {
    case injuries
    case headlines
    case review

    var id: String { rawValue }
}

/// An entry the reader may retract, by the id the delete route takes.
private struct MacRetractTarget: Hashable {
    let id: Int
    let playerName: String
}

struct MacInjuryReportScreen: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    private let league: LeagueKey

    @State private var tab: MacInjuryTab = .injuries
    @State private var teamFilter: String?
    @State private var shownStatuses: Set<String> = MacInjuryStatuses.allKeys
    @State private var showsSuperseded = false
    @State private var nameFilter = ""
    @State private var selection: MacInjuryRow.ID?

    @State private var report: LeagueAvailabilityReport?
    @State private var reportFailure: APIError?
    @State private var isLoading = false
    @State private var loadedReportRoute: LeagueRoute?

    @State private var news: LeagueNewsList?
    @State private var newsFailure: APIError?
    @State private var loadedNewsRoute: LeagueRoute?

    @State private var queue: LeagueReviewQueue?
    @State private var queueFailure: APIError?
    @State private var loadedQueueRoute: LeagueRoute?

    @State private var isRecording = false
    @State private var recordPrefill = MacStatusPrefill()
    @State private var isPasting = false

    @State private var retractTarget: MacRetractTarget?
    @State private var isConfirmingRetract = false
    @State private var retractFailure: String?

    init(league: LeagueKey) {
        self.league = league
    }

    // MARK: Body

    var body: some View {
        screen
            .sheet(isPresented: $isRecording) {
                MacRecordStatusSheet(league: league, prefill: recordPrefill)
            }
            .sheet(isPresented: $isPasting) {
                MacPasteLinkSheet(league: league)
            }
            .confirmationDialog(retractDialogTitle,
                                isPresented: $isConfirmingRetract,
                                titleVisibility: .visible) {
                Button(retractButtonTitle, role: .destructive) {
                    performRetract()
                }
                Button("Cancel", role: .cancel) { }
            }
            .task(id: key(reportRoute)) {
                await loadReport(reportRoute)
                await pollReport(reportRoute)
            }
            .task(id: key(newsRoute)) {
                await loadNews(newsRoute)
            }
            .task(id: key(queueRoute)) {
                await loadQueue(queueRoute)
            }
            .macOnPendingAction { action in
                handle(action)
            }
    }

    private var screen: some View {
        VStack(spacing: 0) {
            MacFilterBar {
                MacInjuryFilterBar(league: league,
                                   tab: $tab,
                                   teamFilter: $teamFilter,
                                   shownStatuses: $shownStatuses,
                                   showsSuperseded: $showsSuperseded,
                                   nameFilter: $nameFilter,
                                   reviewCount: queue?.items?.count,
                                   supersededCount: supersededCount,
                                   isLoading: isLoading)
            }
            FreshnessBar(freshness: currentFreshness, isBundledDemo: environment.isDemoMode)
            reportBanner
            staleStrip
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            stripsUnderTable
            NotesFooter(notes: footerNotes, attribution: footerAttribution)
        }
        .macScreenTitle(.injuries, league: league, dataThrough: currentFreshness?.dataThrough)
        .toolbar {
            toolbarContent
        }
        .inspector(isPresented: inspectorBinding) {
            inspectorContent
        }
    }

    // MARK: Routes

    private var reportRoute: LeagueRoute {
        let date: String? = league == .nba ? "next" : nil
        return LeagueRoutes.availability(league, team: teamFilter, date: date)
    }

    private var newsRoute: LeagueRoute {
        LeagueRoutes.news(league, team: teamFilter, limit: 50)
    }

    private var queueRoute: LeagueRoute {
        LeagueRoutes.reviewQueue(league)
    }

    private func key(_ route: LeagueRoute) -> MacLoadKey {
        MacLoadKey(route: route, generation: model.generation(for: league))
    }

    /// The server's own word that the league is off; demo mode ignores it (the bundled league list
    /// describes a server, not the demo data).
    private var offReason: String? {
        if environment.isDemoMode {
            return nil
        }
        return model.offReason(for: league)
    }

    // MARK: Loading

    private func loadReport(_ route: LeagueRoute) async {
        if offReason != nil {
            return
        }
        if loadedReportRoute != route {
            report = nil
            reportFailure = nil
            selection = nil
        }
        isLoading = true
        do {
            let result = try await environment.league.get(LeagueAvailabilityReport.self, route)
            if Task.isCancelled { return }
            report = result
            loadedReportRoute = route
            reportFailure = nil
        } catch {
            if Task.isCancelled { return }
            reportFailure = APIError.from(error)
        }
        isLoading = false
    }

    /// Asks for the report again every five minutes until the screen goes away or the request
    /// changes (either cancels the task this runs in).
    private func pollReport(_ route: LeagueRoute) async {
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: 300_000_000_000)
            if Task.isCancelled { return }
            await loadReport(route)
        }
    }

    private func loadNews(_ route: LeagueRoute) async {
        if offReason != nil {
            return
        }
        if loadedNewsRoute != route {
            news = nil
            newsFailure = nil
        }
        do {
            let result = try await environment.league.get(LeagueNewsList.self, route)
            if Task.isCancelled { return }
            news = result
            loadedNewsRoute = route
            newsFailure = nil
        } catch {
            if Task.isCancelled { return }
            newsFailure = APIError.from(error)
        }
    }

    private func loadQueue(_ route: LeagueRoute) async {
        if offReason != nil {
            return
        }
        if loadedQueueRoute != route {
            queue = nil
            queueFailure = nil
        }
        do {
            let result = try await environment.league.get(LeagueReviewQueue.self, route)
            if Task.isCancelled { return }
            queue = result
            loadedQueueRoute = route
            queueFailure = nil
        } catch {
            if Task.isCancelled { return }
            queueFailure = APIError.from(error)
        }
    }

    private func retry() {
        Task {
            await loadReport(reportRoute)
            await loadNews(newsRoute)
            await loadQueue(queueRoute)
        }
    }

    // MARK: What is on screen

    private var currentFreshness: LeagueFreshness? {
        report?.freshness ?? news?.freshness
    }

    private var tableRows: [MacInjuryRow] {
        guard let current = report else { return [] }
        let filter = MacInjuryFilter(statuses: shownStatuses,
                                     includeSuperseded: showsSuperseded,
                                     name: nameFilter)
        return MacInjuryRows.rows(from: current, filter: filter)
    }

    private var selectedRef: MacInjuryEntryRef? {
        guard let id = selection, let current = report else { return nil }
        return MacInjuryRows.find(id: id, in: current)
    }

    private var inspectorBinding: Binding<Bool> {
        Binding<Bool>(
            get: { model.isInspectorShown && tab == .injuries },
            set: { newValue in
                model.isInspectorShown = newValue
            }
        )
    }

    private func showDetails(_ id: MacInjuryRow.ID) {
        selection = id
        model.isInspectorShown = true
    }

    /// How many served entries no longer drive the projection (the switch's label); nil until the
    /// report has loaded.
    private var supersededCount: Int? {
        guard let current = report else { return nil }
        return MacInjuryRows.supersededCount(in: current)
    }

    // MARK: Banners and strips

    @ViewBuilder private var reportBanner: some View {
        if tab == .injuries, let current = report {
            LeagueStateBanner(state: current.state, message: current.message)
                .padding(.horizontal, Spacing.md)
        }
    }

    /// Set while an older payload is on screen and the latest refresh failed.
    private var staleMessage: String? {
        switch tab {
        case .injuries:
            return report != nil ? reportFailure?.userMessage : nil
        case .headlines:
            return news != nil ? newsFailure?.userMessage : nil
        case .review:
            return queue != nil ? queueFailure?.userMessage : nil
        }
    }

    @ViewBuilder private var staleStrip: some View {
        if let message = staleMessage {
            MacStaleStrip(message: message) {
                retry()
            }
        }
    }

    @ViewBuilder private var stripsUnderTable: some View {
        if tab == .injuries && report != nil {
            MacInjuryStrips(report: report)
        }
    }

    private var footerNotes: [String] {
        switch tab {
        case .injuries:
            guard let current = report,
                  current.state == "fresh",
                  let message = current.message,
                  !message.isEmpty else {
                return []
            }
            return [message]
        case .headlines:
            return news?.notes ?? []
        case .review:
            return []
        }
    }

    private var footerAttribution: String? {
        tab == .review ? nil : report?.attribution
    }

    // MARK: Content

    @ViewBuilder private var content: some View {
        if let reason = offReason {
            MacLeagueOffView(league: league, reason: reason)
        } else {
            switch tab {
            case .injuries:
                injuriesContent
            case .headlines:
                headlinesContent
            case .review:
                reviewContent
            }
        }
    }

    @ViewBuilder private var injuriesContent: some View {
        if report == nil, let problem = reportFailure {
            MacLoadFailureView(error: problem, league: league) {
                retry()
            }
        } else if report == nil {
            MacLoadingView()
        } else if tableRows.isEmpty {
            injuriesEmpty
        } else {
            MacInjuryTable(rows: tableRows,
                           selection: $selection,
                           onShowDetails: { id in showDetails(id) })
        }
    }

    private var injuriesEmpty: some View {
        let hasEntries: Bool = (report.map { MacInjuryRows.entryCount(in: $0) } ?? 0) > 0
        let title: String = hasEntries ? "No entries match these filters" : "No reported absences"
        let detail: String = hasEntries
            ? "Change the status filter, or show superseded entries."
            : "Nothing is reported for the teams shown."
        return ContentUnavailableView(title, systemImage: "cross.case", description: Text(detail))
    }

    @ViewBuilder private var headlinesContent: some View {
        if news == nil, let problem = newsFailure {
            MacLoadFailureView(error: problem, league: league) {
                retry()
            }
        } else if news == nil {
            MacLoadingView()
        } else {
            MacHeadlinesList(items: news?.allItems ?? [],
                             emptyText: headlinesEmptyText,
                             onRecordStatus: { item in recordFromHeadline(item) })
        }
    }

    /// Why there are no headlines: no feed is set up at all, or the feeds had nothing new.
    private var headlinesEmptyText: String {
        let feeds: [LeagueSourceState] = news?.freshness?.sources ?? []
        if feeds.isEmpty {
            return league == .nba
                ? "No NBA headline feed is configured on your server yet."
                : "No EuroLeague headline feed is configured on your server yet."
        }
        return "No headlines yet. The feeds your server reads are listed in Sources & Freshness."
    }

    @ViewBuilder private var reviewContent: some View {
        if queue == nil, let problem = queueFailure {
            MacLoadFailureView(error: problem, league: league) {
                retry()
            }
        } else if queue == nil {
            MacLoadingView()
        } else {
            MacReviewQueueSheet(league: league,
                                items: queue?.items ?? [],
                                onResolve: { item in recordFromQueue(item) })
        }
    }

    // MARK: Toolbar and inspector

    @ToolbarContentBuilder private var toolbarContent: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            screenAction
            copyButton
            inspectorToggle
        }
    }

    @ViewBuilder private var screenAction: some View {
        if tab == .headlines {
            Button {
                isPasting = true
            } label: {
                Label("Paste Link…", systemImage: "link.badge.plus")
            }
            .help("Save a headline link (⇧⌘L)")
        } else {
            Button {
                startRecordStatus(MacStatusPrefill())
            } label: {
                Label("Record Status…", systemImage: "square.and.pencil")
            }
            .help("Record an availability status (⇧⌘R)")
        }
    }

    @ViewBuilder private var copyButton: some View {
        if tab == .injuries {
            MacCopyTableButton(header: MacInjuryRow.copyHeader, rows: tableRows.map { $0.copyCells })
        }
    }

    @ViewBuilder private var inspectorToggle: some View {
        if tab == .injuries {
            MacInspectorToggle()
        }
    }

    private var inspectorContent: some View {
        MacInjuryInspector(league: league,
                           ref: selectedRef,
                           retractProblem: retractFailure,
                           onRetract: { id, name in askToRetract(id: id, name: name) })
    }

    // MARK: Retracting

    /// The inspector's Retract button: remember what to retract and ask first.
    private func askToRetract(id: Int, name: String) {
        retractTarget = MacRetractTarget(id: id, playerName: name)
        retractFailure = nil
        isConfirmingRetract = true
    }

    private var retractDialogTitle: String {
        let name: String = retractTarget?.playerName ?? "this player"
        switch league {
        case .euroleague:
            return "Retract the status you recorded for " + name + "?"
        case .nba:
            return "Clear the status you entered for " + name + "?"
        }
    }

    private var retractButtonTitle: String {
        league == .euroleague ? "Retract" : "Clear"
    }

    private func performRetract() {
        guard let target = retractTarget else { return }
        Task {
            await sendRetract(target)
        }
    }

    private func sendRetract(_ target: MacRetractTarget) async {
        do {
            try await environment.league.send(method: "DELETE",
                                              route: LeagueRoutes.retractStatus(league, id: target.id),
                                              body: nil)
            model.noteSuccessfulWrite(in: league)
            retractTarget = nil
            retractFailure = nil
            selection = nil
        } catch {
            retractFailure = MacWriteMessage.text(for: APIError.from(error))
        }
    }

    // MARK: Writes started from here

    private func handle(_ action: MacPendingAction) {
        switch action {
        case .recordStatus:
            tab = .injuries
            startRecordStatus(MacStatusPrefill())
        case .pasteLink:
            tab = .headlines
            isPasting = true
        }
    }

    private func startRecordStatus(_ prefill: MacStatusPrefill) {
        recordPrefill = prefill
        isRecording = true
    }

    /// The text a write takes for a team: a club code in the EuroLeague, a team id in the NBA.
    private func teamKey(for team: LeagueTeamRef?) -> String? {
        guard let team = team else { return nil }
        switch league {
        case .euroleague:
            return team.clubCode ?? team.id
        case .nba:
            return team.id
        }
    }

    /// "Record status from this headline": the link, outlet and date come along; the person says
    /// which player and what status.
    private func recordFromHeadline(_ item: LeagueNewsLink) {
        var prefill = MacStatusPrefill()
        prefill.teamKey = teamKey(for: item.teams?.first)
        prefill.sourceUrl = item.link
        prefill.sourceLabel = item.sourceName
        prefill.sourcePublishedAt = item.publishedAt
        startRecordStatus(prefill)
    }

    /// "Record status" on a report row that matched nobody: its club and its name come along.
    private func recordFromQueue(_ item: LeagueReviewQueueItem) {
        var prefill = MacStatusPrefill()
        prefill.teamKey = teamKey(for: item.team)
        prefill.playerName = item.playerName ?? item.name
        prefill.sourceUrl = item.source?.url
        prefill.sourceLabel = item.source?.label
        prefill.sourcePublishedAt = item.source?.publishedAt
        startRecordStatus(prefill)
    }
}
#endif
