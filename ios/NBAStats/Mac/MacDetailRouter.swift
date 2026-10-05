#if os(macOS)
import SwiftUI

// Which screen the detail column shows: the dashboard (the existing `DashboardScreen`), or one of
// the league screens.
//
// WHY THE LEAGUE SCREEN HAS AN `.id`
// `MacScreenHost` is given `.id(MacScreenIdentity(screen:league:))`. Changing either throws the
// old view away and builds a new one, so a screen's `@State` (its loaded payload, its filters, its
// selected row) can never carry one league's numbers into the other's. A reload of the same screen
// (a bumped generation) keeps its `.id` and refreshes in place through `.task(id:)`.
//
// WHY `MacScreenHost` HAS A FIXED SET OF INITIALISERS
// Each package that builds a screen replaces its stub file and keeps its type name and `init`
// exactly (MAC_DESIGN 2.7a), so this switch never changes when a screen is written.

struct MacDetailRouter: View {

    @EnvironmentObject private var model: MacAppModel

    init() { }

    private var current: MacSelection {
        model.selection ?? MacSelection.screen(.startHere)
    }

    var body: some View {
        switch current {
        case .dashboard:
            DashboardScreen()
        case .screen(let screen):
            MacScreenHost(screen: screen, league: model.league)
                .id(MacScreenIdentity(screen: screen, league: model.league))
        }
    }
}

/// Builds the screen for a `MacScreen` in a league.
struct MacScreenHost: View {

    private let screen: MacScreen
    private let league: LeagueKey

    init(screen: MacScreen, league: LeagueKey) {
        self.screen = screen
        self.league = league
    }

    var body: some View {
        switch screen {
        case .startHere:
            MacStartHereScreen()
        case .round:
            MacRoundScreen(league: league)
        case .matchup:
            MacMatchupScreen(league: league)
        case .defence:
            MacDefenseScreen(league: league)
        case .injuries:
            MacInjuryReportScreen(league: league)
        case .scorers:
            MacScorersScreen(league: league)
        case .seasonStats:
            MacSeasonStatsScreen(league: league)
        case .playerSearch:
            playerSearch
        case .games:
            MacBoxScoresScreen(league: league)
        case .review:
            MacReviewScreen(league: league)
        case .teamView:
            MacTeamViewScreen(league: league)
        case .ratings:
            MacTeamRatingsScreen(league: league)
        case .method:
            MacMethodScreen(league: league)
        case .sources:
            MacSourcesScreen(league: league)
        }
    }

    /// The NBA's player search is the existing screen, with its own navigation stack. The
    /// EuroLeague has none, so a stale request for it lands on Season Stats.
    @ViewBuilder private var playerSearch: some View {
        if league == .nba {
            SearchScreen()
        } else {
            MacSeasonStatsScreen(league: league)
        }
    }
}
#endif
