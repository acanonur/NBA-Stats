import SwiftUI

// The two small pieces of shared vocabulary every league view leans on: how much room a view has
// (`LeagueDensity`) and which EuroLeague clubs a configuration picker may offer
// (`LeaguePickerClub`, carried down the view tree by `EnvironmentValues.leagueClubs`).
//
// WHY THE CLUB LIST TRAVELS IN THE ENVIRONMENT
// The dashboard's configuration sheet is one shared iOS and macOS file, and it must be able to offer
// a picker of EuroLeague clubs without knowing where the clubs come from. On the Mac the root view
// reads them from `ElMeta.clubs` and writes them into the environment once; on iOS nothing writes
// them, the default is empty, and the same editor falls back to a plain text field. A view that
// merely reads the list therefore never has to ask "which platform am I on".
//
// Everything here is internal on purpose. Only the payload types are public (the public
// `WidgetPayload` needs them to be); a view or a helper that took an internal type through a public
// signature would not compile.

/// How much room a league view has, and so how much of itself it draws.
///
/// A dashboard tile is `.compact`: the headline numbers, one line per fact. A screen or an
/// inspector is `.full`: every field the payload carries. The same view draws both, so a tile and
/// the screen it summarises always show the same number written the same way.
enum LeagueDensity: String, Hashable, Sendable {
    case compact
    case full
}

/// A club a configuration picker can offer: its code (the value stored in the widget's config) and
/// its name (what the reader sees).
struct LeaguePickerClub: Identifiable, Hashable, Sendable {
    let code: String
    let name: String

    var id: String { code }

    /// The clubs of `ElMeta.clubs`, in the order the server sent them, skipping any entry that has
    /// no code to store. A club with no name shows its code.
    static func list(from teams: [LeagueTeamRef]?) -> [LeaguePickerClub] {
        guard let teams = teams else { return [] }
        var clubs: [LeaguePickerClub] = []
        for team in teams {
            let code = team.clubCode ?? team.id ?? ""
            if code.isEmpty { continue }
            let name = team.name ?? team.shortName ?? code
            clubs.append(LeaguePickerClub(code: code, name: name))
        }
        return clubs
    }
}

private struct LeagueClubsKey: EnvironmentKey {
    static let defaultValue: [LeaguePickerClub] = []
}

extension EnvironmentValues {
    /// The clubs a configuration picker may offer. Empty unless the Mac root has set it.
    var leagueClubs: [LeaguePickerClub] {
        get { self[LeagueClubsKey.self] }
        set { self[LeagueClubsKey.self] = newValue }
    }
}
