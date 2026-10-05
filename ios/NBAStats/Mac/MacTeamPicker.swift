#if os(macOS)
import SwiftUI

// A menu that picks one team of the league on screen, for the filter bars of the Matchup, Defence,
// Injuries, Box Scores and Team screens.
//
// WHY ONE PICKER FOR BOTH LEAGUES
// An NBA team is a number and a EuroLeague club is a code, and the two leagues reach their teams
// through different payloads (`/v1/teams` and `ElMeta.clubs`). `MacAppModel.teamOptions(for:)` turns
// either into the same `MacTeamOption`, so a screen asks "which team" the same way in both leagues
// and stores the answer as the plain text the routes take (`teams/{id}`, `clubCode=`).
//
// The selection is `String?`: nil is "no team" (the screen decides what that means: "All teams",
// "Choose a team"), and `noneLabel` is the menu row that selects it. While the lists are still
// loading the menu is simply short.

/// One team in a picker: the text a route takes (`id`), the short code and the name.
struct MacTeamOption: Identifiable, Hashable {
    let id: String
    let abbr: String
    let name: String

    /// `OLY · Olympiacos`: the code finds the team at a glance and the name settles which one.
    var label: String {
        if name.isEmpty || name == abbr {
            return abbr
        }
        return abbr + " · " + name
    }
}

struct MacTeamPicker: View {

    @EnvironmentObject private var model: MacAppModel
    @Binding private var selection: String?
    private let league: LeagueKey
    private let title: String
    private let noneLabel: String?

    init(league: LeagueKey, title: String, selection: Binding<String?>, noneLabel: String? = nil) {
        self.league = league
        self.title = title
        self.noneLabel = noneLabel
        _selection = selection
    }

    var body: some View {
        Picker(title, selection: $selection) {
            if let none = noneLabel {
                Text(none).tag(nil as String?)
            }
            ForEach(model.teamOptions(for: league)) { option in
                Text(option.label).tag(option.id as String?)
            }
        }
        .pickerStyle(.menu)
        .frame(minWidth: 200)
    }
}
#endif
