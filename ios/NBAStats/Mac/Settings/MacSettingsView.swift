#if os(macOS)
import AppKit
import SwiftUI

// The Settings window (Hardwood > Settings, Command-comma): four tabs.
//
//   General   the existing Settings screen: demo data, server address and key, favourites, refresh
//             notes, cache, era coverage.
//   Leagues   the league the app opens on, the EuroLeague club to follow, the server's API key
//             (read from the installer's `hardwood.env`), and the three server helpers.
//   Model     the model's constants and where each came from, read-only.
//   About     the version, who the numbers come from, and what the app is for.
//
// WHY THE MODEL TAB IS READ-ONLY
// The server can now change a few allowlisted constants (`PATCH {prefix}/model-settings`), but this
// release only shows them: a number that moves every projection deserves a design of its own (which
// ones, with what limits, how to put one back), and a read-only table already answers the question
// a reader actually has, "what is the model assuming and where did that come from". Each row shows
// its provenance, so an assumption the workbook never validated reads as exactly that.
//
// WHY THE KEY IS NEVER SHOWN
// The API key is a password for the server. The Leagues tab says whether one is set and can load
// the installer's, but never prints it.

struct MacSettingsView: View {

    init() { }

    var body: some View {
        TabView {
            SettingsScreen()
                .tabItem {
                    Label("General", systemImage: "gearshape")
                }
            MacLeagueSettingsView()
                .tabItem {
                    Label("Leagues", systemImage: "sportscourt")
                }
            MacModelSettingsView()
                .tabItem {
                    Label("Model", systemImage: "slider.horizontal.3")
                }
            MacAboutView()
                .tabItem {
                    Label("About", systemImage: "info.circle")
                }
        }
        .frame(width: 640, height: 560)
    }
}

// MARK: - Leagues

struct MacLeagueSettingsView: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    @State private var hasKey = false
    @State private var keyMessage: String?

    init() { }

    var body: some View {
        Form {
            leagueSection
            clubSection
            keySection
            serverSection
        }
        .formStyle(.grouped)
        .onAppear {
            refreshKeyState()
        }
    }

    // MARK: League and club

    private var leagueSection: some View {
        Section {
            Picker("League", selection: $model.league) {
                ForEach(LeagueKey.allCases) { option in
                    Text(option.displayName).tag(option)
                }
            }
            .pickerStyle(.segmented)
        } header: {
            Text("League")
        } footer: {
            Text("Hardwood remembers the league you last chose and opens on it.")
        }
    }

    private var clubSection: some View {
        Section {
            Picker("Favorite EuroLeague club", selection: $model.favoriteClubCode) {
                Text("None").tag(nil as String?)
                ForEach(model.elClubs) { club in
                    Text(club.name).tag(club.code as String?)
                }
            }
            if model.elClubs.isEmpty {
                Text("The club list loads once your server answers.")
                    .hardwoodText(.caption)
            }
        } header: {
            Text("EuroLeague")
        } footer: {
            Text("The club the Matchup screen and the Matchups & Defence dashboard follow.")
        }
    }

    // MARK: API key

    private var keyStateText: String {
        hasKey ? "Set" : "Not set"
    }

    private var keySection: some View {
        Section {
            LabeledContent("API key") {
                Text(keyStateText)
            }
            Button("Read API key from hardwood.env") {
                readKey()
            }
            if let message = keyMessage {
                Text(message)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
        } header: {
            Text("Server key")
        } footer: {
            Text("Your server asks for this key when it reads or changes anything. The installer made one and keeps it in hardwood.env. Hardwood never shows it.")
        }
    }

    private func refreshKeyState() {
        let stored = environment.configuration.apiKey ?? ""
        hasKey = !stored.isEmpty
    }

    private func readKey() {
        guard let key = MacHardwoodEnv.readAPIKey() else {
            keyMessage = "No API key was found in " + MacHardwoodEnv.envFileDisplayPath + "."
            return
        }
        let override = UserDefaults.standard.string(forKey: APIConfiguration.DefaultsKey.baseURL)
        Task {
            await environment.applyServerSettingsAndWait(baseURL: override, apiKey: key)
            hasKey = true
            keyMessage = "Key loaded from hardwood.env."
            await model.reconnect()
        }
    }

    // MARK: Server helpers

    private var serverSection: some View {
        Section {
            Button("Copy Restart Command") {
                MacHardwoodEnv.copyRestartCommand()
            }
            Button("Open Logs Folder") {
                MacHardwoodEnv.openLogsFolder()
            }
            Button("Open Data Folder") {
                MacHardwoodEnv.openDataFolder()
            }
        } header: {
            Text("Server")
        } footer: {
            Text("Paste the restart command into Terminal to restart Hardwood's server.")
        }
    }
}

// MARK: - Model

/// One constant of the model, already written as the strings the table shows.
struct MacModelSettingRow: Identifiable, Hashable {
    let id: String
    let key: String
    let value: String
    let valueSort: Double
    let provenance: String
    let status: String
}

struct MacModelSettingsView: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    @State private var league: LeagueKey = LeagueKey.euroleague
    @State private var payload: LeagueModelSettings?
    @State private var failure: APIError?
    @State private var isLoading = false
    @State private var loadedRoute: LeagueRoute?
    @State private var sortOrder: [KeyPathComparator<MacModelSettingRow>] = [
        KeyPathComparator(\MacModelSettingRow.key)
    ]

    init() { }

    private var route: LeagueRoute {
        LeagueRoutes.modelSettings(league)
    }

    private var rows: [MacModelSettingRow] {
        MacModelSettingsView.makeRows(from: payload)
    }

    private var sortedRows: [MacModelSettingRow] {
        rows.sorted(using: sortOrder)
    }

    var body: some View {
        VStack(spacing: 0) {
            MacFilterBar {
                Picker("League", selection: $league) {
                    ForEach(LeagueKey.allCases) { option in
                        Text(option.displayName).tag(option)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 220)
                Text("The numbers behind the projections. Changing them from the app is not available yet.")
                    .hardwoodText(.caption)
                    .lineLimit(2)
            }
            content
        }
        .onAppear {
            league = model.league
        }
        .task(id: MacLoadKey(route: route, generation: model.generation(for: league))) {
            await load(route)
        }
    }

    @ViewBuilder private var content: some View {
        if let payload = payload {
            VStack(spacing: 0) {
                FreshnessBar(freshness: payload.freshness, isBundledDemo: environment.isDemoMode)
                if let failure = failure {
                    MacStaleStrip(message: failure.userMessage) {
                        Task {
                            await load(route)
                        }
                    }
                }
                if rows.isEmpty {
                    ContentUnavailableView("No settings were returned", systemImage: "slider.horizontal.3")
                } else {
                    table
                }
            }
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

    private var table: some View {
        Table(sortedRows, sortOrder: $sortOrder) {
            TableColumn("Setting", value: \MacModelSettingRow.key)
                .width(min: 150, ideal: 210)
            TableColumn("Value", value: \MacModelSettingRow.valueSort) { row in
                NumCell(text: row.value)
            }
            .width(min: 70, ideal: 90)
            TableColumn("Provenance", value: \MacModelSettingRow.provenance)
                .width(min: 120, ideal: 170)
            TableColumn("Default", value: \MacModelSettingRow.status)
                .width(min: 60, ideal: 80)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
    }

    // MARK: Loading

    private func load(_ route: LeagueRoute) async {
        if loadedRoute != route {
            payload = nil
            failure = nil
        }
        isLoading = true
        do {
            let result = try await environment.league.get(LeagueModelSettings.self, route)
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

    // MARK: Rows

    static func makeRows(from payload: LeagueModelSettings?) -> [MacModelSettingRow] {
        var result: [MacModelSettingRow] = []
        let settings: [LeagueModelConstant] = payload?.settings ?? []
        for setting in settings {
            guard let key = setting.key, !key.isEmpty else { continue }
            result.append(MacModelSettingRow(id: key,
                                             key: key,
                                             value: valueText(setting.value),
                                             valueSort: setting.value?.doubleValue ?? -Double.infinity,
                                             provenance: provenanceText(setting.provenance),
                                             status: defaultText(setting.isDefault)))
        }
        return result
    }

    /// A setting's value: a number formatted exactly as the Method screen's constants table
    /// formats it (`MacConstantWords.numberText`: the reader's locale, up to six decimals,
    /// trailing zeros trimmed), or the text itself. `String(Double)` was used here before; it
    /// ignores the locale, prints `12.0`, and switches to `1e-05` for small values.
    static func valueText(_ value: JSONValue?) -> String {
        guard let value = value else { return Formatting.emDash }
        if let number = value.doubleValue {
            return MacConstantWords.numberText(number)
        }
        if let text = value.stringValue {
            return text
        }
        return Formatting.emDash
    }

    /// `workbookUnvalidated` reads as words; a provenance this build does not know is shown as sent.
    static func provenanceText(_ provenance: String?) -> String {
        guard let provenance = provenance, !provenance.isEmpty else { return Formatting.emDash }
        switch provenance {
        case "default":
            return "Default"
        case "manual":
            return "Set by you"
        case "workbookUnvalidated":
            return "Workbook assumption, not checked"
        case "fitted":
            return "Fitted from results"
        default:
            return provenance
        }
    }

    static func defaultText(_ isDefault: Bool?) -> String {
        guard let isDefault = isDefault else { return Formatting.emDash }
        return isDefault ? "Yes" : "No"
    }
}

// MARK: - About

struct MacAboutView: View {

    init() { }

    private var versionText: String {
        let info = Bundle.main.infoDictionary
        let short = (info?["CFBundleShortVersionString"] as? String) ?? "1.0"
        let build = (info?["CFBundleVersion"] as? String) ?? "1"
        return "Version " + short + " (" + build + ")"
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.lg) {
                VStack(alignment: .leading, spacing: Spacing.xs) {
                    Text("Hardwood")
                        .hardwoodText(.displayValue)
                    Text(versionText)
                        .hardwoodText(.caption)
                }
                aboutParagraph("Stats via NBA.com. Injury status from the NBA's official injury report.")
                aboutParagraph("EuroLeague statistics from the EuroLeague's data service. Availability researched from the linked sources.")
                aboutParagraph("Hardwood is a private, personal tool. It reads public pages at a polite pace, for use on this Mac only, and keeps what it reads on this Mac.")
                aboutParagraph("Hardwood shows projected scores and recorded statistics. It has no betting features.")
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Spacing.xl)
        }
    }

    private func aboutParagraph(_ text: String) -> some View {
        Text(text)
            .hardwoodText(.tableCell, monospacedDigits: false)
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
    }
}
#endif
