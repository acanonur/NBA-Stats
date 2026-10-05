import SwiftUI

// How a team is written on a league screen: a badge and, when there is room, a name; and how a game
// is titled ("BOS at NYK" in the NBA, "OLY v PAN" in the EuroLeague).
//
// WHY A SEPARATE LABEL FOR THE LEAGUE TEAM TYPE
// `TeamBadge` and `TeamRef` belong to the NBA dashboard and need an NBA team id. A league payload
// names a team with `LeagueTeamRef`, whose `id` is either an NBA id or a EuroLeague club code and
// whose abbreviation may, in principle, be absent. This label never fails: it falls back from the
// abbreviation to the club code to the id to an em dash, so a row is always drawn and never blank.

extension LeagueTeamRef {

    /// The short code a badge shows: the abbreviation, else the club code, else the id, else an
    /// em dash.
    var displayAbbr: String {
        if let abbr = abbr, !abbr.isEmpty { return abbr }
        if let code = clubCode, !code.isEmpty { return code }
        if let key = id, !key.isEmpty { return key }
        return Formatting.emDash
    }

    /// The full name, else the short name, else the badge code.
    var displayName: String {
        if let full = name, !full.isEmpty { return full }
        if let short = shortName, !short.isEmpty { return short }
        return displayAbbr
    }
}

extension LeagueGameRef {

    /// The game's title in the league's own reading order: the NBA names the visitor first
    /// ("WAS at NOP"), the EuroLeague the home side first ("ZZA v ZZP").
    func matchupText(for league: LeagueKey) -> String {
        let homeCode = home?.displayAbbr ?? Formatting.emDash
        let awayCode = away?.displayAbbr ?? Formatting.emDash
        switch league {
        case .nba:
            return awayCode + " at " + homeCode
        case .euroleague:
            return homeCode + " v " + awayCode
        }
    }

    /// The same title for a game that carries its own league key. A missing or unknown key reads
    /// like the EuroLeague's.
    var matchupTitle: String {
        let key = LeagueKey(rawValue: league ?? "") ?? LeagueKey.euroleague
        return matchupText(for: key)
    }
}

/// A team badge with, optionally, the team's name beside it.
struct LeagueTeamLabel: View {
    private let team: LeagueTeamRef?
    private let size: TeamBadge.Size
    private let showsName: Bool

    init(team: LeagueTeamRef?, size: TeamBadge.Size = .medium, showsName: Bool = false) {
        self.team = team
        self.size = size
        self.showsName = showsName
    }

    var body: some View {
        HStack(spacing: Spacing.sm) {
            TeamBadge(abbreviation: team?.displayAbbr ?? Formatting.emDash,
                      name: team?.name,
                      size: size)
            if showsName {
                Text(team?.displayName ?? Formatting.emDash)
                    .hardwoodText(.tableCell)
                    .lineLimit(1)
            }
        }
    }
}
