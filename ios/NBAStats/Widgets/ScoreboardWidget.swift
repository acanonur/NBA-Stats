import Foundation
import SwiftUI

/// Last night's slate: one row per game, with both teams, the score, where the game is in its
/// life, and — when the tile is large enough — who carried it.
///
/// A game that has not tipped shows an em dash rather than `0-0`, and the winner is marked with
/// weight and a leading caret as well as colour, so the result survives a grayscale screenshot.
public struct ScoreboardWidget: View {

    private let payload: ScoreboardPayload
    private let size: WidgetSize

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    public init(payload: ScoreboardPayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Density

    private var gameLimit: Int {
        switch size {
        case .small:  return 2
        case .medium: return 4
        case .large:  return 8
        }
    }

    private var games: [ScoreboardGame] {
        Array(payload.games.prefix(max(gameLimit, 0)))
    }

    private var showsPerformers: Bool {
        size == .large && !dynamicTypeSize.isAccessibilitySize
    }

    private var showsTeamNames: Bool {
        size != .small
    }

    private var headline: String {
        Formatting.mediumGameDate(payload.date)
    }

    private var subhead: String {
        let remaining = payload.games.count - games.count
        var parts: [String] = []
        if payload.allFinal == true {
            parts.append("All final")
        } else if payload.games.contains(where: { $0.game.status == .live }) {
            parts.append("In progress")
        } else if payload.isLatestCompleted == true {
            parts.append("Latest completed slate")
        }
        parts.append(payload.games.count == 1 ? "1 game" : "\(payload.games.count) games")
        if remaining > 0 { parts.append("showing \(games.count)") }
        return parts.joined(separator: " · ")
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            if games.isEmpty {
                emptyNote
            } else {
                VStack(alignment: .leading, spacing: Spacing.xs) {
                    ForEach(games) { entry in
                        gameRow(entry)
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Pieces

    private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            Text(headline)
                .hardwoodText(.widgetTitle)
                .lineLimit(1)
            Text(subhead)
                .hardwoodText(.caption)
                .lineLimit(1)
        }
        .accessibilityElement(children: .combine)
    }

    private var emptyNote: some View {
        HStack(spacing: Spacing.xs) {
            Image(systemName: "calendar")
                .imageScale(.small)
                .foregroundStyle(Palette.textTertiary)
                .accessibilityHidden(true)
            Text("No games on this date.")
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }

    private func gameRow(_ entry: ScoreboardGame) -> some View {
        let game = entry.game
        let winner = game.winner
        return VStack(alignment: .leading, spacing: Spacing.xs) {
            HStack(alignment: .center, spacing: Spacing.sm) {
                VStack(alignment: .leading, spacing: Spacing.xxs) {
                    teamLine(game.away,
                             points: game.awayPts,
                             isWinner: winner?.teamId == game.away.teamId)
                    teamLine(game.home,
                             points: game.homePts,
                             isWinner: winner?.teamId == game.home.teamId)
                }
                Spacer(minLength: Spacing.xs)
                statusLabel(game)
            }
            if showsPerformers, !entry.topPerformers.isEmpty {
                performers(entry.topPerformers)
            }
        }
        .padding(.vertical, Spacing.xxs)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText(entry))
    }

    private func teamLine(_ team: TeamRef, points: Int?, isWinner: Bool) -> some View {
        HStack(spacing: Spacing.xs) {
            // The winner is marked with a caret as well as weight and colour, so the result
            // survives a grayscale screenshot. Both lines reserve the same width for it.
            Text(verbatim: isWinner ? "\u{25B8}" : " ")
                .font(Typography.tableHeader)
                .foregroundStyle(Palette.positive)
                .frame(minWidth: 9, alignment: .leading)
                .accessibilityHidden(true)
            TeamBadge(team: team, size: .small)
            if showsTeamNames {
                Text(team.nickname ?? team.name)
                    .hardwoodText(.tableCell,
                                  color: isWinner ? Palette.textPrimary : Palette.textSecondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
            }
            Spacer(minLength: Spacing.xs)
            Text(Formatting.integer(points))
                .font(Typography.statValueMono)
                .fontWeight(isWinner ? .semibold : .regular)
                .foregroundStyle(points == nil ? Palette.textTertiary : Palette.textPrimary)
        }
    }

    @ViewBuilder private func statusLabel(_ game: GameRef) -> some View {
        switch game.status {
        case .final:
            Text("Final")
                .hardwoodText(.tableHeader, color: Palette.textSecondary)
                .lineLimit(1)
        case .live:
            HStack(spacing: Spacing.xxs) {
                Circle()
                    .fill(Palette.negative)
                    .frame(width: 6, height: 6)
                    .accessibilityHidden(true)
                Text(liveText(game))
                    .hardwoodText(.tableHeader, color: Palette.negative, monospacedDigits: true)
                    .lineLimit(1)
            }
        case .scheduled:
            Text("Scheduled")
                .hardwoodText(.tableHeader, color: Palette.textTertiary)
                .lineLimit(1)
        }
    }

    private func performers(_ list: [TopPerformer]) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            ForEach(list.prefix(2)) { performer in
                HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
                    Text(performer.player.shortName)
                        .hardwoodText(.caption, color: Palette.textSecondary)
                        .lineLimit(1)
                    if let abbreviation = performer.teamAbbr, !abbreviation.isEmpty {
                        Text(abbreviation)
                            .hardwoodText(.caption)
                            .lineLimit(1)
                    }
                    Text(performer.line ?? Formatting.emDash)
                        .hardwoodText(.caption, color: Palette.textPrimary, monospacedDigits: true)
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                    Spacer(minLength: 0)
                }
            }
        }
        .padding(.leading, Spacing.lg)
        .accessibilityHidden(true)
    }

    // MARK: Text

    private func liveText(_ game: GameRef) -> String {
        var parts: [String] = []
        if let period = game.period, period > 0 {
            parts.append(period <= 4 ? "Q\(period)" : "OT\(period - 4)")
        } else {
            parts.append("Live")
        }
        if let clock = game.clock, !clock.isEmpty { parts.append(clock) }
        return parts.joined(separator: " ")
    }

    private func statusText(_ game: GameRef) -> String {
        switch game.status {
        case .final:     return "final"
        case .live:      return "in progress, \(liveText(game))"
        case .scheduled: return "not yet played"
        }
    }

    private func accessibilityText(_ entry: ScoreboardGame) -> String {
        let game = entry.game
        var parts: [String] = []
        let away = game.awayPts.map { "\(game.away.name) \($0)" } ?? "\(game.away.name), no score yet"
        let home = game.homePts.map { "\(game.home.name) \($0)" } ?? "\(game.home.name), no score yet"
        parts.append(away)
        parts.append(home)
        parts.append(statusText(game))
        if let winner = game.winner { parts.append("\(winner.name) won") }
        if showsPerformers {
            for performer in entry.topPerformers.prefix(2) {
                var text = performer.player.name
                if let line = performer.line, !line.isEmpty { text += ", \(line)" }
                if let value = performer.value {
                    text += ", \(value.metric) \(value.displayValue)"
                }
                parts.append(text)
            }
        }
        return parts.joined(separator: ", ")
    }
}

#if DEBUG
#Preview("Scoreboard") {
    ScrollView {
        VStack(spacing: Spacing.md) {
            ScoreboardWidget(payload: .preview, size: .small)
                .hardwoodCard()
            ScoreboardWidget(payload: .preview, size: .large)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}

#Preview("Scoreboard, live and scheduled") {
    let live = GameRef(gameId: "0022500601", date: "2026-01-03", season: "2025-26",
                       seasonType: "Regular Season", home: .preview, away: .previewOpponent,
                       homePts: 88, awayPts: 91, status: .live, period: 3, clock: "4:21",
                       finalizedAt: nil)
    let scheduled = GameRef(gameId: "0022500602", date: "2026-01-03", season: "2025-26",
                            seasonType: "Regular Season", home: .previewOpponent, away: .preview,
                            homePts: nil, awayPts: nil, status: .scheduled, period: nil,
                            clock: nil, finalizedAt: nil)
    let payload = ScoreboardPayload(date: "2026-01-03",
                                    isLatestCompleted: false,
                                    allFinal: false,
                                    games: [ScoreboardGame(game: live), ScoreboardGame(game: scheduled)])
    return ScoreboardWidget(payload: payload, size: .medium)
        .hardwoodCard()
        .padding(Spacing.lg)
        .hardwoodBackground()
}
#endif
