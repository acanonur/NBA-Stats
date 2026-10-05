#if os(macOS)
import SwiftUI

// Team View & Squads (EuroLeague) and Team View (NBA): choose a team, see its header and its players.
//
// WHAT THIS FILE IS
// A team menu and a button, around `MacTeamViewContent`, which does the loading and the drawing and
// is also the whole content of the Club window. Keeping the two apart means a club opened from
// another screen in its own window looks exactly like the screen, because it is the same view.
//
// WHICH TEAM OPENS FIRST
// The reader's choice if they have made one this session, else their favourite (the EuroLeague club
// set on Start Here or in Settings, or the NBA team set in Settings), else the first team in the
// menu. The menu is alphabetical by name: an order for finding a team, not a ranking.

struct MacTeamViewScreen: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel
    @Environment(\.openWindow) private var openWindow

    private let league: LeagueKey

    @State private var teamChoice: String?

    init(league: LeagueKey) {
        self.league = league
    }

    // MARK: Which team

    private var effectiveTeam: String? {
        if let chosen = teamChoice {
            return chosen
        }
        switch league {
        case .euroleague:
            if let favourite = model.favoriteClubCode, !favourite.isEmpty {
                return favourite
            }
        case .nba:
            if let favourite = environment.favoriteTeamID {
                return String(favourite)
            }
        }
        return model.teamOptions(for: league).first?.id
    }

    private var teamBinding: Binding<String?> {
        Binding<String?>(
            get: { effectiveTeam },
            set: { newValue in
                teamChoice = newValue
            }
        )
    }

    private var teamName: String {
        guard let id = effectiveTeam else { return "" }
        let options: [MacTeamOption] = model.teamOptions(for: league)
        return options.first(where: { $0.id == id })?.name ?? id
    }

    // MARK: Body

    var body: some View {
        VStack(spacing: 0) {
            MacFilterBar {
                filters
            }
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .macScreenTitle(.teamView, league: league, dataThrough: model.info(for: league)?.dataThrough)
    }

    private var filters: some View {
        HStack(spacing: Spacing.md) {
            MacTeamPicker(league: league, title: "Team", selection: teamBinding)
            Button("Open in New Window") {
                openInWindow()
            }
            .disabled(effectiveTeam == nil)
            .help("Show this team in its own window")
        }
    }

    @ViewBuilder private var content: some View {
        if let id = effectiveTeam {
            MacTeamViewContent(league: league, teamId: id)
                .id(league.rawValue + "-" + id)
        } else {
            ContentUnavailableView("Choose a team",
                                   systemImage: "person.crop.rectangle.stack",
                                   description: Text("The team list has not loaded yet, or the server has no teams for this league."))
        }
    }

    private func openInWindow() {
        guard let id = effectiveTeam else { return }
        openWindow(id: MacWindowID.club, value: LeagueLink(league: league, id: id, title: teamName))
    }
}
#endif
