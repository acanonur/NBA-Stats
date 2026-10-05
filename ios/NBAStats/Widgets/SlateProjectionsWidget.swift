import Foundation
import SwiftUI

/// `slate_projections`: the projected score of every game on the next slate (NBA) or in the next
/// round (EuroLeague).
///
/// The payload is the object `GET /v1/projections` and `GET /v1/el/projections` return. Each game
/// carries both sides' projected points, the margin, the projected winner (or a toss-up) and how
/// many key players the model counted as missing. A projection is an estimate of a game that has
/// not been played, so the tile says so: `WidgetHost`'s footer carries the "Projected, not
/// recorded" sentence, and the rows below carry the two honesty marks a single projection can have.
///
/// THE TWO MARKS
/// - `Assumed range`: the width of the projection's 80% range is a standing assumption, not yet
///   fitted to past results (`intervalBasis == "assumed"`).
/// - `After tip-off`: the projection was made after the game began or was played, so it is a
///   reconstruction and not a prediction (`LeagueGameProjection.isComputedAfterTheFact`, which
///   obeys the server's own `model.computedAfterTipoff` and compares no timestamps itself).
///
/// ORDER OF THE TWO NUMBERS
/// A game's title and its numbers are written in the league's own reading order: the NBA names the
/// visitor first ("BOS at NYK", so "108–112"), the EuroLeague the home side first ("OLY v PAN", so
/// "84–79"). Reading the title tells you which number is whose.
///
/// SIZES
/// `.large`: up to ten games. `.medium` (and `.small`): five. "n more" says how many were left out.
/// Everything shown is a field of `LeagueSlateProjections` run through `LeagueFormatting`; the
/// tile compares nothing with any outside number and carries no likelihood figure.
public struct SlateProjectionsWidget: View {

    private let payload: LeagueSlateProjections
    private let size: WidgetSize

    public init(payload: LeagueSlateProjections, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Derived

    private var league: LeagueKey {
        LeagueKey(rawValue: payload.league ?? "") ?? LeagueKey.nba
    }

    private var rowLimit: Int {
        size == .large ? 10 : 5
    }

    private var games: [LeagueGameProjection] {
        payload.games ?? []
    }

    private var shownGames: [LeagueGameProjection] {
        Array(games.prefix(rowLimit))
    }

    /// `Round 5` for a EuroLeague round, the day for an NBA slate, else a plain title.
    private var headerTitle: String {
        if let round = payload.round {
            return "Round " + String(round)
        }
        if let day = payload.date, !day.isEmpty {
            return LeagueFormatting.leagueDate(day)
        }
        return "Projected scores"
    }

    private var leagueText: String {
        LeagueKey(rawValue: payload.league ?? "")?.displayName ?? ""
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            if games.isEmpty {
                Text("No games to project")
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
            } else {
                rows
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                Text(headerTitle)
                    .hardwoodText(.sectionTitle)
                if !leagueText.isEmpty {
                    Text(leagueText)
                        .hardwoodText(.caption)
                }
                Spacer(minLength: 0)
            }
            if payload.freshness?.isDemo == true {
                LeagueStrip(symbol: "exclamationmark.triangle.fill",
                            text: "Invented demo league — not real games",
                            tint: Palette.warning)
            }
        }
    }

    private var rows: some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            ForEach(Array(shownGames.enumerated()), id: \.offset) { pair in
                SlateGameRow(game: pair.element, league: league)
            }
            moreLine
        }
    }

    @ViewBuilder private var moreLine: some View {
        if games.count > shownGames.count {
            Text(String(games.count - shownGames.count) + " more")
                .hardwoodText(.caption)
        }
    }
}

// MARK: - One game

/// One game of the slate: the title and the projected score on the first row; when it is played (or
/// its result), the server's summary of the margin, and the marks on the second.
private struct SlateGameRow: View {
    let game: LeagueGameProjection
    let league: LeagueKey

    private var gameRef: LeagueGameRef? { game.game }

    private var title: String {
        gameRef?.matchupText(for: league) ?? Formatting.emDash
    }

    /// Two numbers in the league's reading order, joined by an en dash.
    private func pair(home: String, away: String) -> String {
        switch league {
        case .nba:
            return away + "–" + home
        case .euroleague:
            return home + "–" + away
        }
    }

    private var projectedText: String {
        guard let home = game.home?.projectedPoints, let away = game.away?.projectedPoints else {
            return Formatting.emDash
        }
        return pair(home: LeagueFormatting.number(home, places: 0),
                    away: LeagueFormatting.number(away, places: 0))
    }

    /// The tip-off while the game is ahead, `Result pending`, `Postponed`, or the final score.
    private var whenText: String {
        guard let ref = gameRef else { return Formatting.emDash }
        if ref.status == "final" {
            if let homePoints = ref.homePts, let awayPoints = ref.awayPts {
                var text = "Final " + pair(home: LeagueFormatting.integer(homePoints),
                                           away: LeagueFormatting.integer(awayPoints))
                let overtime = leagueOvertimeText(ref.overtimePeriods)
                if !overtime.isEmpty {
                    text += " (" + overtime + ")"
                }
                return text
            }
            return "Final"
        }
        return LeagueFormatting.gameStatusText(ref)
    }

    private var summaryText: String {
        let summary = game.summary ?? ""
        if !summary.isEmpty { return summary }
        return game.isTossUp == true ? "Toss-up" : ""
    }

    private var keyAbsenceCount: Int {
        (game.home?.keyAbsences ?? []).count + (game.away?.keyAbsences ?? []).count
    }

    private var absenceText: String {
        keyAbsenceCount == 1 ? "1 key absence" : String(keyAbsenceCount) + " key absences"
    }

    private var berlinHelp: String {
        guard league == .euroleague else { return "" }
        let time = LeagueFormatting.berlinTime(gameRef?.tipoffUtc)
        return time == Formatting.emDash ? "" : "Berlin time: " + time
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                Text(title)
                    .hardwoodText(.widgetTitle)
                    .lineLimit(1)
                Spacer(minLength: Spacing.sm)
                Text(projectedText)
                    .hardwoodText(.widgetTitle)
            }
            secondLine
            marks
        }
        .accessibilityElement(children: .combine)
    }

    private var secondLine: some View {
        HStack(spacing: Spacing.xs) {
            Text(whenText)
                .hardwoodText(.caption)
                .lineLimit(1)
                .help(berlinHelp)
            if !summaryText.isEmpty {
                Text("·")
                    .hardwoodText(.caption)
                    .accessibilityHidden(true)
                Text(summaryText)
                    .hardwoodText(.tableCell)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder private var marks: some View {
        if keyAbsenceCount > 0 || game.intervalBasis == "assumed" || game.isComputedAfterTheFact {
            HStack(spacing: Spacing.xs) {
                if keyAbsenceCount > 0 {
                    LeagueChip(text: absenceText, symbol: "cross.case", tint: Palette.warning)
                }
                if game.intervalBasis == "assumed" {
                    AssumedRangeBadge()
                }
                if game.isComputedAfterTheFact {
                    LeagueChip(text: "After tip-off", symbol: "clock.arrow.circlepath", tint: Palette.warning)
                        .help(LeagueGameProjection.afterTheFactNote)
                }
                Spacer(minLength: 0)
            }
        }
    }
}

#if DEBUG
#Preview("Slate projections") {
    ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            SlateProjectionsWidget(payload: LeagueSlateProjections.preview, size: .large)
                .hardwoodCard()
            SlateProjectionsWidget(payload: LeagueSlateProjections.preview, size: .medium)
                .hardwoodCard()
            SlateProjectionsWidget(payload: LeagueSlateProjections(), size: .large)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
