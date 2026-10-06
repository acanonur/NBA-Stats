#if os(macOS)
import AppKit
import Foundation
import SwiftUI

// How the Mac app starts, and how it keeps up while it is open: read the server's API key from the
// installer's settings file, wake the shared services, ask the server what leagues it serves, and
// then check once a minute whether anything moved.
//
// WHY THE KEY IS READ FROM A FILE
// `backend/scripts/macos/install.sh` makes a random API key and writes it to
// `~/Library/Application Support/Hardwood/hardwood.env`. With a key set, the server asks for it on
// every read (and for every change), so an app that did not send it would show nothing. The app is
// not sandboxed, so it can read that file itself; the person never has to copy a secret by hand.
// The key is read only when none is stored, so a key typed into Settings is never overwritten, and
// it is never shown on screen or logged.
//
// HOW THE FILE IS READ
// Exactly as the server and the installer read it (`nbastats.accounts.config.load_dotenv`,
// `ensure_api_key.first_assignment`): `#` lines and lines without `=` are skipped, the line is split
// at the first `=`, both sides are trimmed, one pair of matching quotes is removed, and the FIRST
// `HARDWOOD_API_KEY=` line decides. An empty first assignment therefore means "no key", even if a
// later line has one, because that is what the server would do.
//
// HOW IT KEEPS UP
// There is no push channel in use (the server's event stream is not read). Instead a loop wakes
// every 60 seconds and asks `GET /v1/leagues`, whose rows carry each league's own cursor
// (`syncVersion`, `dataThrough`). A league whose row changed gets its generation bumped, and every
// screen showing that league reloads through its `.task(id:)`. Every fifth tick bumps both leagues
// anyway, to catch state the cursor does not reflect. A wake from sleep and a return to the app tick
// at once. None of this runs in demo mode, where there is no server to ask.

// MARK: - The settings file

/// The folders and the settings file the backend installer made, and the few actions on them.
enum MacHardwoodEnv {

    static let apiKeyName = "HARDWOOD_API_KEY"

    /// What the installer's own help prints for restarting the API under launchd.
    static let restartCommand = "launchctl kickstart -k gui/$(id -u)/com.hardwood.api"

    /// For messages: where the settings file is, written the way a person would type it.
    static let envFileDisplayPath = "~/Library/Application Support/Hardwood/hardwood.env"

    /// `~/Library/Application Support/Hardwood`: the backend's data folder.
    static var dataFolderURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("Application Support", isDirectory: true)
            .appendingPathComponent("Hardwood", isDirectory: true)
    }

    /// `~/Library/Application Support/Hardwood/hardwood.env`.
    static var envFileURL: URL {
        dataFolderURL.appendingPathComponent("hardwood.env", isDirectory: false)
    }

    /// `~/Library/Logs/Hardwood`: where the launchd jobs write their logs.
    static var logsFolderURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("Logs", isDirectory: true)
            .appendingPathComponent("Hardwood", isDirectory: true)
    }

    // MARK: Reading the key

    /// The API key in the text of a settings file, or nil when the first assignment is empty or
    /// there is none.
    static func parseAPIKey(from contents: String) -> String? {
        for rawLine in contents.components(separatedBy: .newlines) {
            let line = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
            if line.isEmpty || line.hasPrefix("#") {
                continue
            }
            guard let equals = line.firstIndex(of: "=") else {
                continue
            }
            let name = String(line[line.startIndex..<equals]).trimmingCharacters(in: .whitespacesAndNewlines)
            if name != apiKeyName {
                continue
            }
            let value = unquote(String(line[line.index(after: equals)...]))
            return value.isEmpty ? nil : value
        }
        return nil
    }

    /// Removes one pair of matching quotes, after trimming.
    static func unquote(_ text: String) -> String {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 2, let first = trimmed.first, let last = trimmed.last else {
            return trimmed
        }
        if first == last && (first == "\"" || first == "'") {
            return String(trimmed.dropFirst().dropLast())
        }
        return trimmed
    }

    /// The key in the installer's settings file, or nil when the file is missing, unreadable or
    /// holds no key.
    static func readAPIKey() -> String? {
        guard let text = try? String(contentsOf: envFileURL, encoding: .utf8) else {
            return nil
        }
        return parseAPIKey(from: text)
    }

    /// Stores the installer's key as the app's own when the app has none yet. Called from
    /// `HardwoodApp.init`, before `AppEnvironment.live()`: the clients are built with the key that
    /// is stored at that moment, and the first screen asks the server for data as soon as it is
    /// drawn -- before the bootstrap task has run -- so a key adopted only by the bootstrap left
    /// those first requests without one (the server log of the CI launch check showed them refused
    /// with 401 on a first launch).
    static func adoptInstallerKeyIfNeeded(defaults: UserDefaults = .standard) {
        let stored = APIConfiguration.resolved(defaults: defaults).apiKey ?? ""
        if !stored.isEmpty {
            return
        }
        guard let key = readAPIKey() else {
            return
        }
        APIConfiguration.setAPIKeyOverride(key, defaults: defaults)
    }

    // MARK: Actions

    /// Puts the restart command on the pasteboard.
    static func copyRestartCommand() {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(restartCommand, forType: .string)
    }

    static func openLogsFolder() {
        openFolder(logsFolderURL)
    }

    static func openDataFolder() {
        openFolder(dataFolderURL)
    }

    /// Opens a folder in Finder, making it first so the click never silently does nothing.
    private static func openFolder(_ url: URL) {
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        _ = NSWorkspace.shared.open(url)
    }
}

// MARK: - Bootstrap and polling

extension MacAppModel {

    /// The window's `.task`: bootstrap once, then poll until the window goes away. Opening the
    /// window again (Window menu) runs it again and resumes polling without bootstrapping twice;
    /// a bootstrap that was cut short by the window closing is run again from the top.
    func start() async {
        if !bootstrapFinished && !isBootstrapping {
            isBootstrapping = true
            await bootstrap()
            isBootstrapping = false
        }
        await runPollingLoop()
    }

    /// The launch pass, in order: the key, the shared services, the server, the leagues. Every
    /// step is safe to run twice, which is what lets a cancelled pass be repeated.
    func bootstrap() async {
        followSelectionInStore()
        await adoptKeyFromSettingsFileIfNeeded()
        await environment.start()
        await checkServer()
        await loadLeagueState()
        // The Mac's own first dashboard (and, on later launches, the favourite club written into
        // its EuroLeague matchup tile). A local change only, so it runs whatever the server said.
        MacStarterLayout.seedIfNeeded(store: environment.store,
                                      catalog: environment.catalog,
                                      clubCode: favoriteClubCode)
        if !Task.isCancelled {
            bootstrapFinished = true
        }
    }

    /// Uses the installer's key when the app has none of its own. A key that is already stored (typed
    /// in Settings, or read before) is left alone. `HardwoodApp.init` has normally adopted it already
    /// (`MacHardwoodEnv.adoptInstallerKeyIfNeeded`); this second look also points the running clients
    /// at it, for a settings file that appeared after the app started.
    func adoptKeyFromSettingsFileIfNeeded() async {
        let stored = environment.configuration.apiKey ?? ""
        if !stored.isEmpty {
            return
        }
        guard let key = MacHardwoodEnv.readAPIKey() else {
            return
        }
        // Passing the current URL override (or nil when there is none) leaves the server address
        // exactly as it was; only the key changes.
        let override = UserDefaults.standard.string(forKey: APIConfiguration.DefaultsKey.baseURL)
        await environment.applyServerSettingsAndWait(baseURL: override, apiKey: key)
    }

    /// Asks `/v1/health`, which the server answers with or without a key, whether anything is
    /// there at all. Demo mode never asks.
    func checkServer() async {
        if environment.isDemoMode {
            server = .demo
            return
        }
        let result = await environment.testConnection()
        if Task.isCancelled { return }
        switch result {
        case .success:
            server = .reachable
        case .failure(let failure):
            server = .unreachable(failure.userMessage)
        }
    }

    /// Reads the league list, learns each league's route prefix from it, and then the EuroLeague's
    /// meta and the NBA's teams. A failure is shown as the server state; the last good data stays.
    func loadLeagueState() async {
        do {
            let loaded = try await environment.league.get([LeagueInfo].self, LeagueRoutes.leagues())
            if Task.isCancelled { return }
            await environment.league.updatePrefixes(loaded)
            leagues = loaded
            _ = recordSignatures(of: loaded, bumpingChanged: false)
            markReachableAfterSuccess()
            await loadElMeta()
            await loadNbaTeams()
        } catch {
            if Task.isCancelled { return }
            noteLeagueFailure(APIError.from(error))
        }
    }

    /// `GET /v1/el/meta`, unless the server says the EuroLeague is off. A failure keeps the last
    /// meta: the screens report their own errors.
    func loadElMeta() async {
        if offReason(for: .euroleague) != nil {
            elMeta = nil
            return
        }
        do {
            let meta = try await environment.league.get(ElMeta.self, LeagueRoutes.elMeta())
            if Task.isCancelled { return }
            elMeta = meta
        } catch {
            return
        }
    }

    /// The NBA's team list, for the team pickers. Only asked for while it is empty.
    func loadNbaTeams() async {
        if !nbaTeams.isEmpty {
            return
        }
        do {
            let response = try await environment.client.teams()
            if Task.isCancelled { return }
            nbaTeams = response.teams
        } catch {
            return
        }
    }

    /// Ask again from the top: used by "Try Again", by changing the key, and by leaving demo mode.
    func reconnect() async {
        await checkServer()
        await loadLeagueState()
        bumpAllGenerations()
        await environment.refreshDashboard(force: true)
    }

    // MARK: Demo mode

    /// "Use Demo Data" from the banner. Only the environment's switch is flipped here: the main
    /// window watches it (`MacRootView`) and calls `demoModeChanged`, which does the reload, so
    /// the banner, Settings and a failed screen all behave the same.
    func useDemoData() {
        environment.isDemoMode = true
    }

    /// "Use My Server": the reverse.
    func useLiveServer() {
        environment.isDemoMode = false
    }

    /// The demo switch changed, from anywhere: follow it. The league client is switched here as
    /// well as by the environment's own switch, so the reload below cannot run before it.
    func demoModeChanged(_ isDemo: Bool) {
        if isDemo {
            server = .demo
        } else {
            server = .unknown
        }
        // What was loaded came from the other source; none of it may linger under the new one.
        leagues = []
        elMeta = nil
        nbaTeams = []
        leagueSignatures = [:]
        Task { [weak self] in
            guard let self = self else { return }
            await self.environment.league.setDemoMode(isDemo)
            await self.reconnect()
        }
    }

    // MARK: Polling

    /// Sleeps a minute, ticks, repeats; returns when the task it runs in is cancelled.
    func runPollingLoop() async {
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: 60_000_000_000)
            if Task.isCancelled { break }
            await tick()
        }
    }

    /// One look at the server: the dashboard's cursor, then the league list.
    func tick() async {
        if !bootstrapFinished || isTicking || environment.isDemoMode {
            return
        }
        isTicking = true
        lastTickAt = Date()
        tickCount += 1

        _ = await environment.checkForUpdates(force: false)

        do {
            let loaded = try await environment.league.get([LeagueInfo].self, LeagueRoutes.leagues())
            if Task.isCancelled {
                isTicking = false
                return
            }
            await environment.league.updatePrefixes(loaded)
            leagues = loaded
            let changed = recordSignatures(of: loaded, bumpingChanged: true)
            markReachableAfterSuccess()
            if changed.contains(.euroleague) {
                await loadElMeta()
                await environment.refreshDashboard(force: false)
            }
            if tickCount % 5 == 0 {
                bumpAllGenerations()
                await loadElMeta()
                await environment.refreshDashboard(force: false)
            }
            // A server that was down at launch has no EuroLeague meta yet: fetch it the first time
            // it answers, not five minutes later.
            if elMeta == nil {
                await loadElMeta()
            }
            await loadNbaTeams()
        } catch {
            if !Task.isCancelled {
                noteLeagueFailure(APIError.from(error))
            }
        }
        isTicking = false
    }

    /// Back from sleep: the network takes a moment to return, so wait two seconds, then tick.
    func refreshAfterWake() {
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard let self = self else { return }
            await self.tick()
        }
    }

    /// Back in front: tick unless one happened in the last minute.
    func refreshOnActivate() {
        if let last = lastTickAt, Date().timeIntervalSince(last) < 60 {
            return
        }
        Task { [weak self] in
            guard let self = self else { return }
            await self.tick()
        }
    }

    /// Command-R: reload every screen, the dashboard and the league list now.
    func refreshNow() {
        bumpAllGenerations()
        Task { [weak self] in
            guard let self = self else { return }
            await self.environment.refreshEverything()
            await self.loadLeagueState()
        }
    }

    // MARK: Helpers

    /// Remembers each league's cursor. With `bumpingChanged`, a league whose cursor differs from the
    /// last one seen gets its generation bumped; the leagues that changed are returned.
    func recordSignatures(of loaded: [LeagueInfo], bumpingChanged: Bool) -> [LeagueKey] {
        var changed: [LeagueKey] = []
        for row in loaded {
            guard let text = row.key, let key = LeagueKey(rawValue: text) else { continue }
            let signature = MacAppModel.signature(of: row)
            if bumpingChanged, let previous = leagueSignatures[key], previous != signature {
                bumpGeneration(key)
                changed.append(key)
            }
            leagueSignatures[key] = signature
        }
        return changed
    }

    /// A league's cursor as one comparable string. Any change in it means "reload".
    static func signature(of row: LeagueInfo) -> String {
        let version = row.syncVersion.map { String($0) } ?? "-"
        let enabled = (row.enabled ?? false) ? "on" : "off"
        let parts: [String] = [row.state ?? "-", enabled, version, row.dataThrough ?? "-"]
        return parts.joined(separator: "|")
    }

    /// The server answered the league list: whatever problem the banner showed is over.
    private func markReachableAfterSuccess() {
        switch server {
        case .unreachable, .keyRejected, .unknown:
            server = .reachable
            bumpAllGenerations()
        case .reachable, .demo:
            break
        }
    }

    /// A failed league list is the server's state: no answer, or a refused key.
    private func noteLeagueFailure(_ failure: APIError) {
        if failure.isUnauthorized {
            server = .keyRejected(failure.userMessage)
            return
        }
        switch failure {
        case .offline, .timeout:
            server = .unreachable(failure.userMessage)
        case .server, .decoding, .demoModeMissingFixture:
            break
        }
    }
}
#endif
