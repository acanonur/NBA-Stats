#if os(macOS)
import Combine
import Foundation
import SwiftUI

// The Mac app's one shared model: which screen and league are showing, what the server's league
// list and the EuroLeague's club list say, whether the server is answering, and the small
// "go there" requests screens leave for each other.
//
// WHY ONE MODEL, AND WHY IT HOLDS SO LITTLE
// Every Mac screen reads its own payload with its own `.task(id:)` (MAC_DESIGN 6.6), so this model
// holds no statistics at all. What it does hold is what must be the same everywhere: the selection
// the sidebar, the menus and the window title agree on, the league, the EuroLeague's `ElMeta` (its
// rounds and clubs are needed by pickers on five screens) and the NBA's team list.
//
// HOW A SCREEN KNOWS ITS DATA IS OLD
// `generations` is a counter per league. A screen puts `generation(for: league)` into its
// `.task(id:)` key, so bumping the counter restarts the load. The bootstrap and polling code in
// `MacBootstrap.swift` bumps it when `/v1/leagues` says a league's cursor moved; a manual refresh,
// a wake from sleep and a successful write bump it too. The counter is the only signal; nothing
// else tells a screen to reload.
//
// WHAT LIVES IN THIS FILE AND WHAT DOES NOT
// Stored state that `MacBootstrap.swift` also touches is internal (not private) because Swift keeps
// `private` per file. The published state itself changes only through methods here.

@MainActor
final class MacAppModel: ObservableObject {

    /// UserDefaults keys. All carry the `hardwood.` prefix (MAC_DESIGN 6.3).
    enum DefaultsKey {
        static let selection = "hardwood.mac.selection"
        static let league = "hardwood.mac.league"
        static let favoriteClubCode = "hardwood.favorite.clubCode"
    }

    // MARK: Published state

    /// What the sidebar has selected. Changing it to a dashboard also selects that dashboard in the
    /// store, so `DashboardScreen` (which reads the store) follows.
    @Published var selection: MacSelection? {
        didSet {
            guard selection != oldValue else { return }
            persistSelection()
            followSelectionInStore()
        }
    }

    /// The league the league screens show. Remembered across launches.
    @Published var league: LeagueKey {
        didSet {
            guard league != oldValue else { return }
            defaults.set(league.rawValue, forKey: DefaultsKey.league)
            redirectSelectionIfNeeded()
        }
    }

    @Published var isInspectorShown = false

    /// `GET /v1/leagues`, as last read.
    @Published var leagues: [LeagueInfo] = []

    /// `GET /v1/el/meta`, as last read. Its `rounds` and `clubs` feed the round and club pickers.
    @Published var elMeta: ElMeta?

    @Published var nbaTeams: [TeamRef] = []

    @Published var server: MacServerState = .unknown

    @Published var pendingAction: MacPendingAction?
    @Published var pendingNavigation: MacNavigation?
    @Published var step: MacStepRequest?

    /// The EuroLeague club the reader follows, by club code. Remembered across launches.
    @Published var favoriteClubCode: String? {
        didSet {
            guard favoriteClubCode != oldValue else { return }
            persistFavoriteClub()
        }
    }

    @Published private(set) var generations: [LeagueKey: Int] = [:]

    // MARK: Collaborators and bookkeeping

    let environment: AppEnvironment
    private let defaults: UserDefaults

    // Internal, not private: `MacBootstrap.swift` extends this class and reads and writes these.
    var isBootstrapping = false
    var bootstrapFinished = false
    var isTicking = false
    var lastTickAt: Date?
    var tickCount = 0
    var leagueSignatures: [LeagueKey: String] = [:]

    private var stepCounter = 0

    // MARK: Init

    init(environment: AppEnvironment) {
        let defaults = UserDefaults.standard
        self.environment = environment
        self.defaults = defaults

        // First launch opens on the EuroLeague: it is in season, and the NBA's has no games yet.
        let savedLeague = LeagueKey(rawValue: defaults.string(forKey: DefaultsKey.league) ?? "")
        let initialLeague: LeagueKey = savedLeague ?? LeagueKey.euroleague
        self.league = initialLeague

        var initialSelection: MacSelection = MacSelection.screen(.startHere)
        if let saved = MacSelection.parse(defaults.string(forKey: DefaultsKey.selection)) {
            switch saved {
            case .dashboard(let id):
                if environment.store.layout(id: id) != nil {
                    initialSelection = saved
                }
            case .screen(let screen):
                if screen.isAvailable(in: initialLeague) {
                    initialSelection = saved
                }
            }
        }
        self.selection = initialSelection

        let savedClub = defaults.string(forKey: DefaultsKey.favoriteClubCode) ?? ""
        self.favoriteClubCode = savedClub.isEmpty ? nil : savedClub
    }

    // MARK: Generations

    /// The reload counter of a league. A screen puts this in its `.task(id:)` key.
    func generation(for league: LeagueKey) -> Int {
        generations[league] ?? 0
    }

    func bumpGeneration(_ league: LeagueKey) {
        generations[league, default: 0] += 1
    }

    func bumpAllGenerations() {
        for key in LeagueKey.allCases {
            bumpGeneration(key)
        }
    }

    /// A write to the server succeeded (a status recorded, a link pasted): everything that league
    /// shows may have changed.
    func noteSuccessfulWrite(in league: LeagueKey) {
        bumpGeneration(league)
    }

    // MARK: Navigation

    /// Goes to a screen. The EuroLeague has no player search, so that one lands on Season Stats.
    func show(_ screen: MacScreen) {
        if screen.isAvailable(in: league) {
            selection = MacSelection.screen(screen)
        } else {
            selection = MacSelection.screen(.seasonStats)
        }
    }

    /// Goes to the dashboard the store has selected, or the first one.
    func showDashboard() {
        let store = environment.store
        if let id = store.selectedLayoutID, store.layout(id: id) != nil {
            selection = MacSelection.dashboard(id)
        } else if let first = store.layouts.first {
            selection = MacSelection.dashboard(first.id)
        }
    }

    /// Makes a blank dashboard and selects it.
    func newDashboard() {
        let layout = environment.store.addBlankLayout(named: "New Dashboard")
        selection = MacSelection.dashboard(layout.id)
    }

    /// Goes to the screen a request is about and leaves the request for it.
    func open(_ navigation: MacNavigation) {
        pendingNavigation = navigation
        show(navigation.screen)
    }

    /// Menu commands that act on one screen go to that screen first, then leave the action there.
    func requestAction(_ action: MacPendingAction) {
        show(.injuries)
        pendingAction = action
    }

    /// One round or one day back (-1) or forward (+1), for whichever screen steps.
    func requestStep(_ delta: Int) {
        stepCounter += 1
        step = MacStepRequest(id: stepCounter, delta: delta)
    }

    /// The store's own selection moved (a dashboard was added, deleted or switched from inside the
    /// dashboard screen). If the sidebar is on a dashboard, it follows.
    func storeSelectionChanged(_ layoutID: String?) {
        guard let current = selection else { return }
        switch current {
        case .dashboard(let shown):
            if let id = layoutID {
                if id != shown {
                    selection = MacSelection.dashboard(id)
                }
            } else {
                selection = MacSelection.screen(.startHere)
            }
        case .screen:
            break
        }
    }

    // MARK: What the server says

    /// One league's row of `GET /v1/leagues`, once it has loaded.
    func info(for league: LeagueKey) -> LeagueInfo? {
        leagues.first { $0.key == league.rawValue }
    }

    /// The server's own reason when it says a league is off, and nil while it is on or unknown.
    ///
    /// Demo mode never reports a league as off. The bundled answer to `/v1/leagues` is the NBA
    /// server's own export, which marks the EuroLeague `enabled: false`, yet every EuroLeague
    /// route has a bundled fixture. Without this, the shell would skip `loadElMeta()` in demo mode
    /// and every club picker (Matchup, Team View, a widget's club field) would be empty.
    func offReason(for league: LeagueKey) -> String? {
        if environment.isDemoMode {
            return nil
        }
        guard let row = info(for: league), row.enabled == false else { return nil }
        if let reason = row.reason, !reason.isEmpty {
            return reason
        }
        return "This league is not available on your server."
    }

    /// The EuroLeague's clubs as a picker wants them.
    var elClubs: [LeaguePickerClub] {
        LeaguePickerClub.list(from: elMeta?.clubs)
    }

    /// The teams of a league as a picker wants them, alphabetical by name. The order is for
    /// finding a team, not a ranking.
    func teamOptions(for league: LeagueKey) -> [MacTeamOption] {
        switch league {
        case .nba:
            var options: [MacTeamOption] = []
            for team in nbaTeams {
                options.append(MacTeamOption(id: String(team.teamId), abbr: team.abbr, name: team.name))
            }
            return options.sorted { $0.name < $1.name }
        case .euroleague:
            var options: [MacTeamOption] = []
            let clubs: [LeagueTeamRef] = elMeta?.clubs ?? []
            for club in clubs {
                let code = club.clubCode ?? club.id ?? ""
                if code.isEmpty { continue }
                options.append(MacTeamOption(id: code, abbr: club.displayAbbr, name: club.displayName))
            }
            return options.sorted { $0.name < $1.name }
        }
    }

    // MARK: Selection side effects

    func followSelectionInStore() {
        guard let current = selection else { return }
        switch current {
        case .dashboard(let id):
            environment.store.select(id)
        case .screen:
            break
        }
    }

    private func redirectSelectionIfNeeded() {
        guard let current = selection else { return }
        switch current {
        case .screen(let screen):
            if !screen.isAvailable(in: league) {
                selection = MacSelection.screen(.seasonStats)
            }
        case .dashboard:
            break
        }
    }

    private func persistSelection() {
        if let current = selection {
            defaults.set(current.storageKey, forKey: DefaultsKey.selection)
        } else {
            defaults.removeObject(forKey: DefaultsKey.selection)
        }
    }

    private func persistFavoriteClub() {
        if let code = favoriteClubCode, !code.isEmpty {
            defaults.set(code, forKey: DefaultsKey.favoriteClubCode)
        } else {
            defaults.removeObject(forKey: DefaultsKey.favoriteClubCode)
        }
    }
}
#endif
