#if os(macOS)
import SwiftUI

// The two read-only detail windows: a box score and a club. Each is opened with
// `openWindow(id: MacWindowID.boxScore, value: LeagueLink(...))`, and `WindowGroup(for:)` gives one
// window per distinct link, so double-clicking the same game twice raises the window it already
// opened.
//
// WHY THEY ARE READ-ONLY AND SEPARATE
// A detail window only reads from the league routes (`environment.league`); it never touches the
// dashboard service, whose single layout and refresh counter belong to the main window. The
// contents are the same views the main window shows (`MacBoxScoreView`, `MacTeamViewContent`), so
// a box score looks the same in a window as it does beside the games list.
//
// A window whose value is nil (the system can ask for one with no value) says so instead of
// drawing an empty frame.

struct MacBoxScoreWindow: View {

    private let link: LeagueLink?

    init(link: LeagueLink?) {
        self.link = link
    }

    var body: some View {
        if let link = link {
            MacBoxScoreView(league: link.league, gameId: link.id)
                .navigationTitle(link.title)
                .frame(minWidth: 720, minHeight: 480)
        } else {
            ContentUnavailableView("No game to show", systemImage: "sportscourt")
                .frame(minWidth: 480, minHeight: 320)
        }
    }
}

struct MacClubWindow: View {

    private let link: LeagueLink?

    init(link: LeagueLink?) {
        self.link = link
    }

    var body: some View {
        if let link = link {
            MacTeamViewContent(league: link.league, teamId: link.id)
                .navigationTitle(link.title)
                .frame(minWidth: 760, minHeight: 520)
        } else {
            ContentUnavailableView("No team to show", systemImage: "person.crop.rectangle.stack")
                .frame(minWidth: 480, minHeight: 320)
        }
    }
}
#endif
