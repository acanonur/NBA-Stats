import Foundation
import SwiftUI

/// `team_matchup`: two teams side by side going into a game.
///
/// For each team: its latest score, what it scores and what it allows per game, how its last games
/// went, and (on the large tile) its recent averages, home and away splits, who is missing, where
/// its defence gives up its points, and the projected score of the game.
///
/// WHY THIS TILE DRAWS NOTHING ITSELF
/// The payload is the object `GET /v1/teams/{id}/matchup` and its EuroLeague twin return, and the
/// Mac's Matchup screen draws the same object with the same views (`MatchupSideColumn`) at a larger
/// density. A tile built from those views can never disagree with its screen about a number or how
/// it is written. Everything shown is a field of `LeagueMatchup` run through `LeagueFormatting`;
/// nothing is computed here. A missing value is an em dash, never 0.
///
/// SIZES
/// `.medium` (and `.small`, which renders as medium): the game line, then the two sides at compact
/// density. `.large` adds the extra rows of each side and the projected score, labelled as an
/// estimate.
public struct TeamMatchupWidget: View {

    private let payload: LeagueMatchup
    private let size: WidgetSize

    public init(payload: LeagueMatchup, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Derived

    private var isLarge: Bool { size == .large }

    private var league: LeagueKey {
        LeagueKey(rawValue: payload.league ?? "") ?? LeagueKey.nba
    }

    /// Home on the left, away on the right: by the payload's `side`, else by its order.
    private var sides: [LeagueTeamForm] {
        TeamMatchupWidget.orderedSides(payload.teams ?? [])
    }

    private static func orderedSides(_ forms: [LeagueTeamForm]) -> [LeagueTeamForm] {
        var remaining: [LeagueTeamForm] = forms
        var home: LeagueTeamForm?
        var away: LeagueTeamForm?
        if let index = remaining.firstIndex(where: { $0.side == "home" }) {
            home = remaining.remove(at: index)
        }
        if let index = remaining.firstIndex(where: { $0.side == "away" }) {
            away = remaining.remove(at: index)
        }
        if home == nil && !remaining.isEmpty {
            home = remaining.removeFirst()
        }
        if away == nil && !remaining.isEmpty {
            away = remaining.removeFirst()
        }
        var result: [LeagueTeamForm] = []
        if let form = home {
            result.append(form)
        }
        if let form = away {
            result.append(form)
        }
        return result
    }

    /// `NBA · 2025-26 · Regular Season`: which league and season the numbers are from.
    private var contextText: String {
        var parts: [String] = []
        if let key = LeagueKey(rawValue: payload.league ?? "") {
            parts.append(key.displayName)
        }
        if let season = payload.season, !season.isEmpty {
            parts.append(season)
        }
        if let type = payload.seasonType, !type.isEmpty {
            parts.append(type)
        }
        return parts.joined(separator: " · ")
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            header
            if sides.isEmpty {
                Text("No games yet this season")
                    .hardwoodText(.caption)
            } else {
                MatchupGameLine(game: payload.game, league: league)
                columns
                projectionSection
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            if !contextText.isEmpty {
                Text(contextText)
                    .hardwoodText(.caption)
            }
            if payload.freshness?.isDemo == true {
                LeagueStrip(symbol: "exclamationmark.triangle.fill",
                            text: "Invented demo league — not real games",
                            tint: Palette.warning)
            }
        }
    }

    private var columns: some View {
        HStack(alignment: .top, spacing: Spacing.lg) {
            ForEach(Array(sides.enumerated()), id: \.offset) { pair in
                MatchupSideColumn(team: pair.element,
                                  density: LeagueDensity.compact,
                                  leagueAverage: payload.leagueAverage?.pointsPerGame,
                                  includesExtras: isLarge)
            }
        }
    }

    // MARK: Projection (large)

    @ViewBuilder private var projectionSection: some View {
        if isLarge, let projection = payload.projection {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Divider()
                HStack(spacing: Spacing.sm) {
                    Text(projectedText(projection))
                        .hardwoodText(.widgetTitle)
                    EstimatedBadge(text: "Estimated")
                    Spacer(minLength: 0)
                }
                ReconstructionNote(projection: projection)
            }
        }
    }

    /// `Projected ZZA 90–84 ZZB · ZZA by 5.0`: both sides' projected points, each beside its team
    /// code so the order is never a guess, then the server's own summary of the margin.
    private func projectedText(_ projection: LeagueGameProjection) -> String {
        let homeCode = projection.home?.team?.displayAbbr ?? payload.game?.home?.displayAbbr ?? Formatting.emDash
        let awayCode = projection.away?.team?.displayAbbr ?? payload.game?.away?.displayAbbr ?? Formatting.emDash
        var text = "Projected "
        if let homePoints = projection.home?.projectedPoints, let awayPoints = projection.away?.projectedPoints {
            text += homeCode + " " + LeagueFormatting.number(homePoints, places: 0)
            text += "–" + LeagueFormatting.number(awayPoints, places: 0) + " " + awayCode
        } else {
            text += Formatting.emDash
        }
        let summary = projection.summary ?? ""
        if !summary.isEmpty {
            text += " · " + summary
        } else if projection.isTossUp == true {
            text += " · Toss-up"
        }
        return text
    }
}

#if DEBUG
#Preview("Team matchup") {
    ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            TeamMatchupWidget(payload: LeagueMatchup.preview, size: .large)
                .hardwoodCard()
            TeamMatchupWidget(payload: LeagueMatchup.preview, size: .medium)
                .hardwoodCard()
            TeamMatchupWidget(payload: LeagueMatchup(), size: .medium)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
