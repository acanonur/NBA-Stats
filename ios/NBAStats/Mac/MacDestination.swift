#if os(macOS)
import Foundation
import SwiftUI

// The vocabulary of "where can the Mac app be": the fourteen screens, what is selected in the
// sidebar, the small request values one screen leaves for another, and the state of the server.
//
// WHY THESE ARE PLAIN VALUES
// A menu command, a sidebar row and a table's context menu all need to say "go to the Matchup
// screen, for this game" without owning the screen. So every such request is a small value
// (`MacPendingAction`, `MacNavigation`, `MacStepRequest`) that `MacAppModel` publishes and the
// target screen consumes, in the way a note is left on a desk. There is no `focusedValue` plumbing
// and no stored `Task`: both were judged too risky to write without a compiler (MAC_DESIGN 6.3).
//
// WHAT A SCREEN IS ALLOWED TO ASSUME
// - `MacScreen.title(for:)` is the one place a screen's name lives, so the sidebar row, the Go menu,
//   the window title and the stub all agree. The two leagues name some screens differently
//   ("Slate" in the NBA, "Round" in the EuroLeague) because the sport does.
// - `playerSearch` exists only in the NBA (its player search is an NBA route). The EuroLeague's
//   nearest screen is Season Stats, and `MacAppModel.show(_:)` sends it there.
// - Nothing here touches the network or the dashboard store.

// MARK: - Sidebar sections

/// The headings of the sidebar below the Dashboards list.
enum MacSidebarSection: String, CaseIterable, Hashable {
    case gameDay
    case players
    case results
    case teams
    case reference

    var title: String {
        switch self {
        case .gameDay:
            return "Game Day"
        case .players:
            return "Players"
        case .results:
            return "Results"
        case .teams:
            return "Teams"
        case .reference:
            return "Reference"
        }
    }

    /// The screens that sit under this heading, in sidebar order.
    var screens: [MacScreen] {
        MacScreen.allCases.filter { $0.section == self }
    }
}

// MARK: - Screens

/// Every screen the Mac app has apart from the dashboards.
enum MacScreen: String, CaseIterable, Hashable, Codable {
    case startHere, round, matchup, defence, injuries
    case scorers, seasonStats, playerSearch
    case games, review
    case teamView, ratings
    case method, sources

    /// The name of the screen in a league. This is the sidebar row, the Go menu item, the window
    /// title and the heading of the screen itself.
    func title(for league: LeagueKey) -> String {
        switch self {
        case .startHere:
            return "Start Here"
        case .round:
            return league == .nba ? "Slate" : "Round"
        case .matchup:
            return "Matchup"
        case .defence:
            return "Defence by Position"
        case .injuries:
            return league == .nba ? "Injuries & News" : "Injury Report & News"
        case .scorers:
            return league == .nba ? "Projected Scorers" : "Round Scorers"
        case .seasonStats:
            return "Season Stats"
        case .playerSearch:
            return "Player Search"
        case .games:
            return league == .nba ? "Games & Box Scores" : "Box Scores & Latest Games"
        case .review:
            return league == .nba ? "Slate Review" : "Round Review"
        case .teamView:
            return league == .nba ? "Team View" : "Team View & Squads"
        case .ratings:
            return "Team Ratings"
        case .method:
            return "Method"
        case .sources:
            return "Sources & Freshness"
        }
    }

    /// An SF Symbol name.
    var icon: String {
        switch self {
        case .startHere:
            return "house"
        case .round:
            return "calendar"
        case .matchup:
            return "rectangle.split.2x1"
        case .defence:
            return "shield.lefthalf.filled"
        case .injuries:
            return "cross.case"
        case .scorers:
            return "person.3"
        case .seasonStats:
            return "tablecells"
        case .playerSearch:
            return "magnifyingglass"
        case .games:
            return "sportscourt"
        case .review:
            return "checkmark.seal"
        case .teamView:
            return "person.crop.rectangle.stack"
        case .ratings:
            return "chart.bar.xaxis"
        case .method:
            return "book"
        case .sources:
            return "antenna.radiowaves.left.and.right"
        }
    }

    /// Command-1 to Command-9, in sidebar order; the rest have none. Command-0 is the dashboard.
    var shortcut: KeyboardShortcut? {
        switch self {
        case .startHere:
            return KeyboardShortcut("1", modifiers: .command)
        case .round:
            return KeyboardShortcut("2", modifiers: .command)
        case .matchup:
            return KeyboardShortcut("3", modifiers: .command)
        case .defence:
            return KeyboardShortcut("4", modifiers: .command)
        case .injuries:
            return KeyboardShortcut("5", modifiers: .command)
        case .scorers:
            return KeyboardShortcut("6", modifiers: .command)
        case .seasonStats:
            return KeyboardShortcut("7", modifiers: .command)
        case .games:
            return KeyboardShortcut("8", modifiers: .command)
        case .teamView:
            return KeyboardShortcut("9", modifiers: .command)
        case .playerSearch, .review, .ratings, .method, .sources:
            return nil
        }
    }

    var section: MacSidebarSection {
        switch self {
        case .startHere, .round, .matchup, .defence, .injuries:
            return .gameDay
        case .scorers, .seasonStats, .playerSearch:
            return .players
        case .games, .review:
            return .results
        case .teamView, .ratings:
            return .teams
        case .method, .sources:
            return .reference
        }
    }

    /// False for the one screen a league does not have: the EuroLeague has no player search.
    func isAvailable(in league: LeagueKey) -> Bool {
        if self == .playerSearch {
            return league == .nba
        }
        return true
    }
}

// MARK: - Selection

/// What the sidebar has selected: one of the user's dashboards, or one of the screens.
enum MacSelection: Hashable {
    case dashboard(String)
    case screen(MacScreen)

    /// The form kept in UserDefaults: `screen:matchup` or `dashboard:<layout id>`.
    var storageKey: String {
        switch self {
        case .dashboard(let id):
            return "dashboard:" + id
        case .screen(let screen):
            return "screen:" + screen.rawValue
        }
    }

    /// The inverse of `storageKey`. Anything that does not parse is nil, so a stale or hand-edited
    /// preference never blocks a launch.
    static func parse(_ text: String?) -> MacSelection? {
        guard let text = text else { return nil }
        if text.hasPrefix("dashboard:") {
            let id = String(text.dropFirst("dashboard:".count))
            if id.isEmpty { return nil }
            return MacSelection.dashboard(id)
        }
        if text.hasPrefix("screen:") {
            let raw = String(text.dropFirst("screen:".count))
            if let screen = MacScreen(rawValue: raw) {
                return MacSelection.screen(screen)
            }
        }
        return nil
    }
}

/// A screen in a league. Used as the `.id` of the detail view, so switching league (or screen)
/// throws the old screen's `@State` away: one league's numbers never sit under the other's name.
struct MacScreenIdentity: Hashable {
    let screen: MacScreen
    let league: LeagueKey
}

// MARK: - Detail windows

/// What a detail window (a box score, a club) is about. `Codable` and `Hashable` because that is
/// what `WindowGroup(for:)` needs to open one window per value and restore it.
struct LeagueLink: Codable, Hashable {
    var league: LeagueKey
    var id: String
    var title: String
}

// MARK: - Requests one place leaves for another

/// A thing a menu command or a toolbar button asks a screen to do. The screen consumes it and sets
/// it back to nil (see `View.macOnPendingAction`).
enum MacPendingAction: Hashable {
    case recordStatus
    case pasteLink
}

/// "Show me this": a game's matchup, a team's defence, a round.
enum MacNavigation: Hashable {
    case matchupGame(String)
    /// One team's next game on the Matchup screen: the Defence table's "Open Matchup".
    case matchupTeam(String)
    case defenceTeam(String)
    case round(Int)

    /// The screen that answers this request. `MacAppModel.open(_:)` goes there, and only that
    /// screen's `macOnPendingNavigation(for:)` takes the request off the model, so the screen the
    /// reader is leaving can never swallow a request meant for the one being opened.
    var screen: MacScreen {
        switch self {
        case .matchupGame, .matchupTeam:
            return MacScreen.matchup
        case .defenceTeam:
            return MacScreen.defence
        case .round:
            return MacScreen.round
        }
    }
}

/// "Go one round or one day back or forward." `id` changes on every request, so two requests in a
/// row for the same direction are still two changes to whoever is watching.
struct MacStepRequest: Hashable {
    var id: Int
    var delta: Int
}

// MARK: - The server

/// What the app knows about the server it is pointed at.
enum MacServerState: Hashable {
    /// Nothing has been asked yet.
    case unknown
    /// The server answered.
    case reachable
    /// The app is serving its bundled fixtures; the server is not being asked.
    case demo
    /// No answer, with the sentence to show.
    case unreachable(String)
    /// The server answered and refused the API key, with the sentence to show.
    case keyRejected(String)
}
#endif
