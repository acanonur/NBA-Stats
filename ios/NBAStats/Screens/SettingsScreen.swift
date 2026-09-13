import Foundation
import SwiftUI

/// Where the numbers come from, how often they are checked, what is cached, and what the league's
/// record can and cannot tell you.
public struct SettingsScreen: View {

    @EnvironmentObject private var environment: AppEnvironment

    public init() { }

    public var body: some View {
        SettingsScreenContent(environment: environment,
                              sync: environment.sync,
                              catalog: environment.catalog,
                              client: environment.client)
    }
}

struct SettingsScreenContent: View {

    /// What the Test Connection button is doing, and what it found out.
    enum ConnectionState: Equatable {
        case idle
        case testing
        case reachable(String)
        case unreachable(String)
    }

    @ObservedObject private var environment: AppEnvironment
    @ObservedObject private var sync: SyncService
    private let catalog: Catalog
    private let client: any APIClientProtocol

    init(environment: AppEnvironment,
         sync: SyncService,
         catalog: Catalog,
         client: any APIClientProtocol) {
        _environment = ObservedObject(wrappedValue: environment)
        _sync = ObservedObject(wrappedValue: sync)
        self.catalog = catalog
        self.client = client
    }

    @State private var baseURLText = ""
    @State private var apiKeyText = ""
    @State private var connection: ConnectionState = .idle
    @State private var statistics: DiskCache.Statistics?
    @State private var teams: [TeamRef] = []
    @State private var isCheckingForUpdates = false

    // MARK: Derived

    private var eraFacts: [EraBoundary] {
        guard catalog.eraBoundaries.isEmpty else { return catalog.eraBoundaries }
        // A bundle that could not be read still gets the timeline the design system carries.
        return AvailabilityExplainer.eraTimeline.map {
            EraBoundary(season: $0.season, label: $0.headline, detail: $0.detail)
        }
    }

    private var cacheSizeText: String {
        guard let statistics = statistics else { return "Measuring…" }
        let bytes = Int64(statistics.byteCount).formatted(.byteCount(style: .file))
        let limit = Int64(statistics.byteLimit).formatted(.byteCount(style: .file))
        let entries = statistics.entryCount == 1 ? "1 saved widget" : "\(statistics.entryCount) saved widgets"
        return "\(entries) · \(bytes) of \(limit)"
    }

    private var lastCheckText: String {
        guard let lastCheck = sync.lastCheck else { return "Not checked yet" }
        return "Checked " + Formatting.relative(lastCheck)
    }

    private var favoritePlayerText: String {
        if let name = environment.favoritePlayerName, !name.isEmpty { return name }
        if let id = environment.favoritePlayerID { return "Player #\(id)" }
        return "Not set"
    }

    private var favoriteTeam: TeamRef? {
        guard let id = environment.favoriteTeamID else { return nil }
        return teams.first { $0.teamId == id }
    }

    private var favoriteTeamText: String {
        if let team = favoriteTeam { return team.name }
        if let name = environment.favoriteTeamName, !name.isEmpty { return name }
        if let id = environment.favoriteTeamID { return "Team #\(id)" }
        return "Not set"
    }

    // MARK: Body

    var body: some View {
        NavigationStack {
            Form {
                dataSourceSection
                serverSection
                favoritesSection
                refreshSection
                storageSection
                eraSection
                aboutSection
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .task {
                await loadSettingsState()
            }
        }
    }

    // MARK: Data source

    private var dataSourceSection: some View {
        Section {
            Toggle("Use bundled demo data", isOn: $environment.isDemoMode)
            if environment.isDemoMode {
                Label("Every number you see is a fixture shipped with the app.", systemImage: "shippingbox")
                    .hardwoodText(.caption, color: Palette.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
        } header: {
            Text("Data source")
        } footer: {
            Text("Demo mode serves the golden fixtures from the app bundle, so Hardwood works with no server at all. Switch it off to talk to your own stats service.")
        }
    }

    // MARK: Server

    private var serverSection: some View {
        Section {
            LabeledContent("Server") {
                TextField("http://localhost:8000/v1", text: $baseURLText)
                    .textContentType(.URL)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .multilineTextAlignment(.trailing)
                    .onSubmit(applyServerSettings)
            }
            LabeledContent("API key") {
                SecureField("Optional", text: $apiKeyText)
                    .textContentType(.password)
                    .multilineTextAlignment(.trailing)
                    .onSubmit(applyServerSettings)
            }
            Button(action: testConnection) {
                testConnectionLabel
            }
            .disabled(connection == .testing)
            connectionStatus
        } header: {
            Text("Stats server")
        } footer: {
            Text("Testing saves what you typed first, then calls /v1/health — the one route that never needs a key. Leave the server empty to go back to the address this build shipped with.")
        }
    }

    private var testConnectionLabel: some View {
        HStack(spacing: Spacing.sm) {
            if connection == .testing {
                ProgressView()
                    .controlSize(.small)
            }
            Text(connection == .testing ? "Testing…" : "Test Connection")
        }
    }

    @ViewBuilder private var connectionStatus: some View {
        switch connection {
        case .idle, .testing:
            EmptyView()
        case .reachable(let message):
            Label(message, systemImage: "checkmark.circle.fill")
                .hardwoodText(.caption, color: Palette.positive)
                .fixedSize(horizontal: false, vertical: true)
        case .unreachable(let message):
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .hardwoodText(.caption, color: Palette.negative)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: Favourites

    private var favoritesSection: some View {
        Section {
            LabeledContent("Player", value: favoritePlayerText)
            if environment.favoritePlayerID != nil {
                Button("Clear favourite player", role: .destructive) {
                    environment.setFavoritePlayer(nil)
                }
            }
            teamPicker
        } header: {
            Text("Favourites")
        } footer: {
            Text("Presets point their widgets at $favorite_player and $favorite_team. Star a player from the Search tab; pick a team here.")
        }
    }

    private var teamPicker: some View {
        Menu {
            Button("None") {
                environment.setFavoriteTeam(nil)
            }
            ForEach(teams) { team in
                Button {
                    environment.setFavoriteTeam(team)
                } label: {
                    if team.teamId == environment.favoriteTeamID {
                        Label(team.name, systemImage: "checkmark")
                    } else {
                        Text(team.name)
                    }
                }
            }
        } label: {
            LabeledContent("Team", value: favoriteTeamText)
        }
        .accessibilityLabel("Favourite team: \(favoriteTeamText)")
    }

    // MARK: Refreshing

    private var refreshSection: some View {
        Section {
            LabeledContent("Last check", value: lastCheckText)
            LabeledContent("Data through", value: sync.dataThrough.map { Formatting.mediumGameDate($0) } ?? "Unknown")
            LabeledContent("Sync version", value: sync.hasNeverSynced ? "Never synced" : "\(sync.syncVersion)")
            checkNowButton
            if let error = sync.lastError {
                Label(error.userMessage, systemImage: "exclamationmark.triangle")
                    .hardwoodText(.caption, color: Palette.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
        } header: {
            Text("Refreshing")
        } footer: {
            Text(refreshFooter)
        }
    }

    private var refreshFooter: String {
        let minutes = Int(BackgroundRefresh.defaultRefreshInterval / 60)
        return "Hardwood asks iOS for a background refresh about every \(minutes) minutes and again overnight, after the league publishes its stat corrections. iOS decides whether those ever run, so every screen also refreshes when you open it and when you pull down."
    }

    private var checkNowButton: some View {
        Button {
            Task { await checkNow() }
        } label: {
            HStack(spacing: Spacing.sm) {
                if isCheckingForUpdates {
                    ProgressView()
                        .controlSize(.small)
                }
                Text(isCheckingForUpdates ? "Checking…" : "Check for finished games")
            }
        }
        .disabled(isCheckingForUpdates)
    }

    // MARK: Storage

    private var storageSection: some View {
        Section {
            LabeledContent("Saved payloads", value: cacheSizeText)
            Button("Clear cache", role: .destructive) {
                Task { await clearCache() }
            }
        } header: {
            Text("Storage")
        } footer: {
            Text("Hardwood keeps each widget's last answer on this device so the dashboard opens instantly and still works offline. Clearing it costs one refresh, nothing more.")
        }
    }

    // MARK: Era coverage

    private var eraSection: some View {
        Section {
            ForEach(eraFacts) { fact in
                eraRow(fact)
            }
        } header: {
            Text("What the record covers")
        } footer: {
            Text("Before 1996-97 the league published no possession data, so advanced numbers for those seasons are box-score estimates — Hardwood marks them “est.” and never mixes them silently with modern figures. A stat that did not exist at all shows an em dash, never a zero.")
        }
    }

    private func eraRow(_ fact: EraBoundary) -> some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            Text(fact.season)
                .hardwoodText(.tableHeader, color: Palette.textPrimary, monospacedDigits: true)
                .frame(minWidth: 66, alignment: .leading)
                .fixedSize(horizontal: true, vertical: false)
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                Text(fact.label)
                    .hardwoodText(.tableCell)
                    .fixedSize(horizontal: false, vertical: true)
                if let detail = fact.detail, !detail.isEmpty {
                    Text(detail)
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(.vertical, Spacing.xxs)
        .accessibilityElement(children: .combine)
    }

    // MARK: About

    private var aboutSection: some View {
        Section {
            LabeledContent("Version", value: versionText)
            LabeledContent("Catalog", value: catalogText)
            Text(environment.attribution)
                .hardwoodText(.tableCell, color: Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("Hardwood is an independent app. Stats come from NBA.com. It is not endorsed by, affiliated with, or sponsored by the National Basketball Association or any of its teams.")
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        } header: {
            Text("About")
        }
    }

    private var versionText: String {
        let bundle = Bundle.main
        let short = (bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "1.0"
        let build = (bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String) ?? "1"
        return "\(short) (\(build))"
    }

    private var catalogText: String {
        "\(catalog.metrics.count) metrics · \(catalog.widgets.count) widgets"
    }

    // MARK: Actions

    private func loadSettingsState() async {
        let configuration = environment.configuration
        if baseURLText.isEmpty {
            baseURLText = configuration.baseURL.absoluteString
        }
        if apiKeyText.isEmpty {
            apiKeyText = configuration.apiKey ?? ""
        }
        statistics = await environment.cacheStatistics()
        await loadTeams()
    }

    private func loadTeams() async {
        guard teams.isEmpty else { return }
        do {
            let response = try await client.teams()
            teams = response.teams.sorted {
                $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
            }
        } catch {
            // A team list that will not load costs the picker its rows, nothing else: the stored
            // favourite id is still sent with every resolve.
            teams = []
        }
    }

    private func applyServerSettings() {
        let accepted = environment.applyServerSettings(baseURL: baseURLText, apiKey: apiKeyText)
        if !accepted {
            connection = .unreachable("That is not an address Hardwood can use. Try something like http://192.168.1.10:8000/v1.")
        }
    }

    private func testConnection() {
        applyServerSettings()
        if case .unreachable = connection { return }
        connection = .testing
        Task {
            let result = await environment.testConnection()
            switch result {
            case .success(let health):
                connection = .reachable(describe(health))
            case .failure(let error):
                connection = .unreachable(error.userMessage)
            }
        }
    }

    private func describe(_ health: HealthResponse) -> String {
        var parts: [String] = []
        parts.append(health.isHealthy ? "Reachable" : "Reachable, but its database is not ready")
        if let version = health.version, !version.isEmpty { parts.append("v\(version)") }
        if let through = health.dataThrough, !through.isEmpty {
            parts.append("data through \(Formatting.shortGameDate(through))")
        }
        return parts.joined(separator: " · ")
    }

    private func checkNow() async {
        isCheckingForUpdates = true
        await environment.checkForUpdates(force: true)
        isCheckingForUpdates = false
    }

    private func clearCache() async {
        await environment.clearCache()
        statistics = await environment.cacheStatistics()
    }
}

#if DEBUG
#Preview("Settings") {
    SettingsScreen()
        .environmentObject(AppEnvironment.demo())
}
#endif
