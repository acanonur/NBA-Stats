#if os(macOS)
import SwiftUI

// Method: how the numbers on the other screens are made, and every constant behind them.
//
// TWO TABS
// "About the model" is prose. For the EuroLeague it is read from the server (`GET /v1/el/method`:
// where Hardwood differs from the workbook it replaces, and the limits to keep in mind). For the NBA,
// which has no such route, it is the text bundled in `MacMethodText.nba`, and it needs no network.
// "Constants" is the table of every number the model uses with where each came from: from
// `GET /v1/el/method` for the EuroLeague and `GET /v1/model-settings` for the NBA.
//
// WHAT THE PAGE WILL NOT DO
// It does not change a constant (the table is read-only; the settings are listed under Settings,
// Model too), and it says plainly that the workbook's columns that compared projections with
// outside numbers are not reproduced.
//
// HOW IT LOADS (MAC_DESIGN 6.6)
// One `.task(id:)` keyed on the league's route and its reload counter. The NBA's About tab does not
// wait for it.

private enum MacMethodTab: String, CaseIterable, Identifiable, Hashable {
    case about
    case constants

    var id: String { rawValue }

    var title: String {
        switch self {
        case .about:
            return "About the model"
        case .constants:
            return "Constants"
        }
    }
}

struct MacMethodScreen: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    private let league: LeagueKey

    @State private var tab: MacMethodTab = .about
    @State private var selection: String?
    @State private var sortOrder: [KeyPathComparator<MacConstantRow>] = [
        KeyPathComparator(\MacConstantRow.order)
    ]
    @State private var elMethod: ElMethod?
    @State private var nbaSettings: LeagueModelSettings?
    @State private var failure: APIError?
    @State private var isLoading = false
    @State private var loadedRoute: LeagueRoute?

    init(league: LeagueKey) {
        self.league = league
    }

    // MARK: Request

    private var route: LeagueRoute {
        switch league {
        case .nba:
            return LeagueRoutes.modelSettings(.nba)
        case .euroleague:
            return LeagueRoutes.elMethod()
        }
    }

    private var loadKey: MacLoadKey {
        MacLoadKey(route: route, generation: model.generation(for: league))
    }

    private var offReason: String? {
        if environment.isDemoMode {
            return nil
        }
        return model.offReason(for: league)
    }

    // MARK: What is loaded

    private var hasPayload: Bool {
        league == .nba ? nbaSettings != nil : elMethod != nil
    }

    private var constants: [LeagueModelConstant]? {
        league == .nba ? nbaSettings?.settings : elMethod?.constants
    }

    private var rows: [MacConstantRow] {
        MacConstantRows.rows(from: constants)
    }

    private var freshness: LeagueFreshness? {
        league == .nba ? nbaSettings?.freshness : elMethod?.freshness
    }

    private var attribution: String? {
        league == .euroleague ? model.elMeta?.attribution : nil
    }

    // MARK: Body

    var body: some View {
        VStack(spacing: 0) {
            MacFilterBar {
                Picker("Show", selection: $tab) {
                    ForEach(MacMethodTab.allCases) { option in
                        Text(option.title).tag(option)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 280)
                if isLoading {
                    ProgressView()
                        .controlSize(.small)
                }
            }
            FreshnessBar(freshness: freshness, isBundledDemo: environment.isDemoMode)
            staleStrip
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            NotesFooter(notes: nil, attribution: attribution)
        }
        .macScreenTitle(.method, league: league, dataThrough: freshness?.dataThrough)
        .toolbar {
            toolbarContent
        }
        .task(id: loadKey) {
            await refresh()
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
        } else if league == .nba && tab == .about {
            nbaAbout
        } else if !hasPayload, let problem = failure {
            MacLoadFailureView(error: problem, league: league) {
                retry()
            }
        } else if !hasPayload {
            MacLoadingView()
        } else if tab == .about {
            elAbout
        } else {
            constantsView
        }
    }

    // MARK: About

    private var nbaAbout: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.xl) {
                ForEach(MacMethodText.nba) { section in
                    sectionView(heading: section.heading, paragraphs: section.paragraphs)
                }
            }
            .padding(Spacing.lg)
            .frame(maxWidth: 760, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var elAbout: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.xl) {
                Text(MacMethodText.euroleagueIntro)
                    .hardwoodText(.tableCell, monospacedDigits: false)
                    .fixedSize(horizontal: false, vertical: true)
                bulletSection(heading: "Where Hardwood differs from your workbook",
                              items: elMethod?.deviations ?? [])
                bulletSection(heading: "Known limits", items: elMethod?.limitations ?? [])
                Text(MacMethodText.outsideNumbersNote)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(Spacing.lg)
            .frame(maxWidth: 760, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func sectionView(heading: String, paragraphs: [String]) -> some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            Text(heading)
                .hardwoodText(.sectionTitle)
            ForEach(Array(paragraphs.enumerated()), id: \.offset) { pair in
                Text(pair.element)
                    .hardwoodText(.tableCell, monospacedDigits: false)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .textSelection(.enabled)
    }

    @ViewBuilder private func bulletSection(heading: String, items: [String]) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text(heading)
                    .hardwoodText(.sectionTitle)
                ForEach(Array(items.enumerated()), id: \.offset) { pair in
                    HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                        Text("•")
                            .hardwoodText(.tableCell, color: Palette.textTertiary)
                        Text(pair.element)
                            .hardwoodText(.tableCell, monospacedDigits: false)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .textSelection(.enabled)
        }
    }

    // MARK: Constants

    @ViewBuilder private var constantsView: some View {
        if rows.isEmpty {
            ContentUnavailableView("No constants listed",
                                   systemImage: "book",
                                   description: Text("The server lists no model constants for this league."))
        } else {
            VStack(spacing: 0) {
                Text("Read-only here. Settings (⌘,) lists the model's settings too.")
                    .hardwoodText(.caption)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, Spacing.md)
                    .padding(.vertical, Spacing.xs)
                MacConstantsTable(rows: rows, selection: $selection, sortOrder: $sortOrder)
            }
        }
    }

    // MARK: Toolbar

    private var copyRows: [[String]] {
        guard tab == .constants else { return [] }
        let shown: [MacConstantRow] = rows.sorted(using: sortOrder)
        return shown.map { $0.copyCells }
    }

    @ToolbarContentBuilder private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .primaryAction) {
            MacCopyTableButton(header: MacConstantRow.copyHeader, rows: copyRows)
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
        if loadedRoute != route {
            elMethod = nil
            nbaSettings = nil
            failure = nil
        }
        isLoading = true
        do {
            switch league {
            case .euroleague:
                let result = try await environment.league.get(ElMethod.self, route)
                if Task.isCancelled { return }
                elMethod = result
            case .nba:
                let result = try await environment.league.get(LeagueModelSettings.self, route)
                if Task.isCancelled { return }
                nbaSettings = result
            }
            loadedRoute = route
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
        }
        isLoading = false
    }
}
#endif
