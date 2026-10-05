#if os(macOS)
import SwiftUI

// Sources & Freshness: where every number on screen comes from, how current each source is, and
// what is wrong with the ones that are not fine.
//
// WHY THIS SCREEN EXISTS
// Hardwood's statistics are fetched; its injury statuses are fetched (the NBA's official report),
// imported (your workbook) or typed (by you); its headlines come from feeds that a server checks
// against each outlet's robots.txt. A reader deciding how far to trust a status needs to know which
// of those it was and whether that source is working. So this screen lists every source with its
// state in words and colour, the reason when it is not fine, how long ago it last succeeded, the
// last error it hit, the day its robots.txt was last checked, and whether it is switched on.
//
// WHAT IT PRINTS VERBATIM
// The server's own notice about what to expect on day one, the attribution lines, the reason of each
// source and its last error: they are the server's sentences about itself, and paraphrasing them
// would put words in the server's mouth. A source state this build does not know is shown as the
// server wrote it, in a neutral chip.
//
// WHAT THE APP DOES NOT SAY
// It does not call a source healthy because it is switched on, or rank sources. The state column
// sorts problems first only because that is the useful order for a person looking for what is
// broken.

// MARK: - Words

enum MacSourceWords {

    /// The kind of source in words. A kind this build does not know is shown as sent.
    static func kind(_ kind: String?) -> String {
        guard let kind = kind, !kind.isEmpty else { return Formatting.emDash }
        switch kind {
        case "stats":
            return "Statistics"
        case "rosters":
            return "Rosters"
        case "injuryReport":
            return "Injury report"
        case "news":
            return "Headline feed"
        case "workbook":
            return "Your workbook"
        case "dataService":
            return "Data service"
        case "manual":
            return "Entered by hand"
        default:
            return kind
        }
    }

    /// Problems first, so a click on the State header brings what is broken to the top.
    static func stateOrder(_ state: String?) -> Int {
        switch state ?? "" {
        case "error":
            return 0
        case "blocked":
            return 1
        case "unreadable":
            return 2
        case "stale":
            return 3
        case "noReportYet":
            return 4
        case "notConfigured":
            return 5
        case "disabled":
            return 6
        case "ok":
            return 7
        default:
            return 8
        }
    }

    /// The store's own state: `ready` is the one word the shared state words do not cover.
    static func storeState(_ state: String?) -> String {
        if state == "ready" {
            return "Ready"
        }
        return LeagueFormatting.stateWord(state)
    }

    /// What kind of data the store holds.
    static func storeKind(_ kind: String?) -> String {
        switch kind ?? "" {
        case "synthetic":
            return "invented demo league"
        case "workbook":
            return "from your workbook"
        case "live":
            return "live data"
        default:
            return ""
        }
    }
}

// MARK: - Row

struct MacSourceRow: Identifiable, Hashable {
    let id: String
    let source: String
    let kind: String
    let state: String
    let stateLabel: String
    let stateSort: Int
    let reason: String
    let lastSuccess: String
    let lastSuccessSort: Double
    let lastError: String
    let robots: String
    let robotsSort: String
    let enabled: String
    let enabledSort: Int
    let attribution: String

    static let copyHeader: [String] = [
        "Source", "Kind", "State", "Reason", "Last success", "Last error", "robots.txt checked", "Enabled"
    ]

    var copyCells: [String] {
        [source, kind, stateLabel, reason, lastSuccess, lastError, robots, enabled]
    }
}

enum MacSourceRows {

    static func rows(from list: LeagueSourceList) -> [MacSourceRow] {
        var result: [MacSourceRow] = []
        var index = 0
        for entry in list.sources ?? [] {
            result.append(row(from: entry, index: index))
            index += 1
        }
        return result
    }

    static func row(from entry: LeagueSourceEntry, index: Int) -> MacSourceRow {
        let key: String = entry.key ?? ""
        let identifier: String = key.isEmpty ? ("source-" + String(index)) : key
        let name: String = entry.label ?? entry.key ?? Formatting.emDash
        let succeeded: Double? = Formatting.parseTimestamp(entry.lastSuccessAt)?.timeIntervalSince1970

        return MacSourceRow(
            id: identifier,
            source: name,
            kind: MacSourceWords.kind(entry.kind),
            state: entry.state ?? "",
            stateLabel: LeagueFormatting.stateWord(entry.state),
            stateSort: MacSourceWords.stateOrder(entry.state),
            reason: text(entry.reason),
            lastSuccess: LeagueFormatting.age(entry.lastSuccessAt),
            lastSuccessSort: LeagueFormatting.sortKey(succeeded),
            lastError: text(entry.lastError),
            robots: LeagueFormatting.leagueDate(entry.robotsCheckedOn),
            robotsSort: entry.robotsCheckedOn ?? "",
            enabled: enabledText(entry.enabled),
            enabledSort: enabledOrder(entry.enabled),
            attribution: text(entry.attribution)
        )
    }

    /// The server's sentence, or an em dash when there is none.
    static func text(_ value: String?) -> String {
        guard let value = value, !value.isEmpty else { return Formatting.emDash }
        return value
    }

    static func enabledText(_ enabled: Bool?) -> String {
        guard let enabled = enabled else { return Formatting.emDash }
        return enabled ? "Yes" : "No"
    }

    static func enabledOrder(_ enabled: Bool?) -> Int {
        guard let enabled = enabled else { return 0 }
        return enabled ? 2 : 1
    }
}

// MARK: - Table

struct MacSourcesTable: View {

    private let rows: [MacSourceRow]
    @Binding private var selection: MacSourceRow.ID?
    @State private var sortOrder: [KeyPathComparator<MacSourceRow>] = [KeyPathComparator(\MacSourceRow.source)]

    init(rows: [MacSourceRow], selection: Binding<MacSourceRow.ID?>) {
        self.rows = rows
        _selection = selection
    }

    private var sortedRows: [MacSourceRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Source", value: \MacSourceRow.source) { row in
                TextCell(text: row.source)
            }
            .width(min: 140, ideal: 200)
            TableColumn("Kind", value: \MacSourceRow.kind) { row in
                TextCell(text: row.kind)
            }
            .width(min: 90, ideal: 120, max: 160)
            TableColumn("State", value: \MacSourceRow.stateSort) { row in
                SourceStateCell(state: row.state, label: row.stateLabel)
            }
            .width(min: 100, ideal: 130, max: 170)
            TableColumn("Reason", value: \MacSourceRow.reason) { row in
                TextCell(text: row.reason)
            }
            .width(min: 180, ideal: 320)
            TableColumn("Last success", value: \MacSourceRow.lastSuccessSort) { row in
                NumCell(text: row.lastSuccess)
            }
            .width(min: 90, ideal: 110, max: 150)
            TableColumn("Last error", value: \MacSourceRow.lastError) { row in
                TextCell(text: row.lastError)
            }
            .width(min: 120, ideal: 200)
            TableColumn("robots.txt checked", value: \MacSourceRow.robotsSort) { row in
                TextCell(text: row.robots)
            }
            .width(min: 110, ideal: 130, max: 170)
            TableColumn("Enabled", value: \MacSourceRow.enabledSort) { row in
                TextCell(text: row.enabled)
            }
            .width(min: 60, ideal: 70, max: 90)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
    }
}

/// A source's state as a chip in words; a state this build does not know is neutral.
private struct SourceStateCell: View {

    private let state: String
    private let label: String

    init(state: String, label: String) {
        self.state = state
        self.label = label
    }

    private var tint: Color {
        switch state {
        case "ok":
            return Palette.positive
        case "stale":
            return Palette.warning
        case "error", "blocked", "unreadable":
            return Palette.negative
        case "disabled", "notConfigured":
            return Palette.textTertiary
        default:
            return Palette.neutral
        }
    }

    var body: some View {
        HStack(spacing: 0) {
            LeagueChip(text: label, tint: tint)
            Spacer(minLength: 0)
        }
    }
}

// MARK: - Screen

struct MacSourcesScreen: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    private let league: LeagueKey

    @State private var payload: LeagueSourceList?
    @State private var failure: APIError?
    @State private var isLoading = false
    @State private var loadedRoute: LeagueRoute?
    @State private var selection: MacSourceRow.ID?

    init(league: LeagueKey) {
        self.league = league
    }

    // MARK: Body

    var body: some View {
        VStack(spacing: 0) {
            intro
            FreshnessBar(freshness: payload?.freshness, isBundledDemo: environment.isDemoMode)
            staleStrip
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .overlay(alignment: .topTrailing) {
                    refreshing
                }
            detailStrip
            NotesFooter(notes: nil, attribution: payload?.attribution)
        }
        .macScreenTitle(.sources, league: league, dataThrough: payload?.freshness?.dataThrough)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                MacCopyTableButton(header: MacSourceRow.copyHeader, rows: copyRows)
            }
        }
        .task(id: MacLoadKey(route: route, generation: model.generation(for: league))) {
            await load(route)
        }
    }

    // MARK: What is on screen

    private var route: LeagueRoute {
        LeagueRoutes.sources(league)
    }

    private var rows: [MacSourceRow] {
        guard let list = payload else { return [] }
        return MacSourceRows.rows(from: list)
    }

    private var copyRows: [[String]] {
        rows.map { $0.copyCells }
    }

    private var selectedRow: MacSourceRow? {
        guard let id = selection else { return nil }
        return rows.first(where: { $0.id == id })
    }

    private var offReason: String? {
        if environment.isDemoMode {
            return nil
        }
        return model.offReason(for: league)
    }

    // MARK: Intro

    private var introText: String {
        let fetched = "Fetched automatically: NBA statistics, the NBA's official injury report "
            + "(once its parser has read a real report; your server says \"not yet confirmed\" until then), "
            + "and the headline feeds below. "
        let typed = "EuroLeague injuries come from your workbook and the statuses you record."
        return fetched + typed
    }

    /// The server's notice about what to expect, as it wrote it. The NBA's rides on its source list;
    /// the EuroLeague's is on its meta.
    private var noticeText: String {
        if let served = payload?.dayOneNotice, !served.isEmpty {
            return served
        }
        if league == .euroleague, let fromMeta = model.elMeta?.dayOneNotice {
            return fromMeta
        }
        return ""
    }

    /// `Data store: Ready · invented demo league`, with the server's reason when it gives one.
    private var storeText: String {
        guard let store = payload?.store else { return "" }
        var parts: [String] = ["Data store: " + MacSourceWords.storeState(store.state)]
        let kind = MacSourceWords.storeKind(store.kind)
        if !kind.isEmpty {
            parts.append(kind)
        }
        if let reason = store.reason, !reason.isEmpty {
            parts.append(reason)
        }
        return parts.joined(separator: " · ")
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text(introText)
                .hardwoodText(.tableCell, monospacedDigits: false)
                .fixedSize(horizontal: false, vertical: true)
            if !noticeText.isEmpty {
                Text(noticeText)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !storeText.isEmpty {
                Text(storeText)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Spacing.md)
        .textSelection(.enabled)
    }

    // MARK: Strips and content

    /// A small spinner while a refresh runs over a table that is already on screen.
    @ViewBuilder private var refreshing: some View {
        if isLoading && payload != nil {
            ProgressView()
                .controlSize(.small)
                .padding(Spacing.sm)
        }
    }

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
            ContentUnavailableView("No sources listed",
                                   systemImage: "antenna.radiowaves.left.and.right",
                                   description: Text("Your server did not list any sources."))
        } else {
            MacSourcesTable(rows: rows, selection: $selection)
        }
    }

    /// The full sentences of the selected source, for the cells that had to truncate them.
    @ViewBuilder private var detailStrip: some View {
        if let row = selectedRow {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                Divider()
                Text(row.source)
                    .hardwoodText(.widgetTitle)
                DetailLine(label: "Reason", value: row.reason)
                DetailLine(label: "Last error", value: row.lastError)
                DetailLine(label: "Attribution", value: row.attribution)
            }
            .padding(.horizontal, Spacing.md)
            .padding(.bottom, Spacing.sm)
            .textSelection(.enabled)
        }
    }

    // MARK: Loading

    private func load(_ route: LeagueRoute) async {
        if offReason != nil {
            return
        }
        if loadedRoute != route {
            payload = nil
            failure = nil
        }
        isLoading = true
        do {
            let result = try await environment.league.get(LeagueSourceList.self, route)
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

    private func retry() {
        Task {
            await load(route)
        }
    }
}
#endif
