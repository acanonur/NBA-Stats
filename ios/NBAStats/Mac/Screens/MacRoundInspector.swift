#if os(macOS)
import SwiftUI

// The pieces of the Round and Slate screen that sit beside the table: the EuroLeague round's
// summary strip, and the inspector that shows one game's projection and what the server's history
// says about earlier runs of the model for it.
//
// WHY THESE ARE NOT IN THE SCREEN FILE
// `MacRoundScreen.swift` owns loading, the filter bar and the table. The inspector is a separate
// concern with its own request (`GET {prefix}/games/{id}/projection`) and its own state, and a
// compiler error in it should point at a small file.
//
// WHAT THE INSPECTOR SHOWS
// `GameProjectionCard` (League/) draws the projection in full: both sides, the range, what the
// model assumed about who plays, the model's constants, the result once the game is final. Below it,
// `MacProjectionHistory` adds the projection locked at tip-off, when the server froze one, and every
// run so far. Both are the server's words and numbers; nothing is recomputed.

// MARK: - Summary strip (EuroLeague)

/// The round at a glance, from the server's `summary`: how many games, how many have no clear
/// favourite, the closest game, the average combined points, and how many projected winners are
/// at home and away. Counts and averages the server computed; none is a likelihood.
struct MacRoundSummaryStrip: View {

    private let summary: ElRoundStats

    init(summary: ElRoundStats) {
        self.summary = summary
    }

    private var closest: String {
        summary.closestGame?.matchupTitle ?? Formatting.emDash
    }

    var body: some View {
        ScrollView(.horizontal, showsIndicators: PlatformMetrics.showsHorizontalIndicators) {
            HStack(alignment: .top, spacing: Spacing.xl) {
                MacRoundStat(value: LeagueFormatting.integer(summary.games), label: "Games")
                MacRoundStat(value: LeagueFormatting.integer(summary.tossUps), label: "Toss-ups")
                MacRoundStat(value: closest, label: "Closest game")
                MacRoundStat(value: LeagueFormatting.number(summary.averageCombinedPoints),
                             label: "Average combined points")
                MacRoundStat(value: LeagueFormatting.integer(summary.homeWinnersProjected),
                             label: "Home projected winners")
                MacRoundStat(value: LeagueFormatting.integer(summary.awayWinnersProjected),
                             label: "Away projected winners")
            }
            .padding(.horizontal, Spacing.md)
            .padding(.vertical, Spacing.sm)
        }
    }
}

struct MacRoundStat: View {

    private let value: String
    private let label: String

    init(value: String, label: String) {
        self.value = value
        self.label = label
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            Text(value)
                .hardwoodText(.statValue)
                .lineLimit(1)
            Text(label)
                .hardwoodText(.statLabel)
                .lineLimit(1)
        }
    }
}

// MARK: - Inspector

/// The inspector for one game: the projection in full, then what the server's history says about
/// earlier runs.
struct MacRoundInspector: View {

    private let league: LeagueKey
    private let projection: LeagueGameProjection
    private let onOpenMatchup: () -> Void
    private let onOpenBoxScore: () -> Void

    init(league: LeagueKey,
         projection: LeagueGameProjection,
         onOpenMatchup: @escaping () -> Void,
         onOpenBoxScore: @escaping () -> Void) {
        self.league = league
        self.projection = projection
        self.onOpenMatchup = onOpenMatchup
        self.onOpenBoxScore = onOpenBoxScore
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.lg) {
                GameProjectionCard(projection: projection,
                                   league: league,
                                   onOpenMatchup: onOpenMatchup,
                                   onOpenBoxScore: onOpenBoxScore)
                history
            }
            .padding(Spacing.lg)
        }
    }

    @ViewBuilder private var history: some View {
        if let gameId = projection.game?.gameId {
            MacProjectionHistory(league: league, gameId: gameId)
                .id(gameId)
        }
    }
}

/// What the model said about one game before: the projection locked at tip-off (when one was
/// frozen) and every run so far, each with its time and its kind in words. Read from
/// `GET {prefix}/games/{id}/projection` and shown only when the server has something to say.
/// Demo mode skips it: the bundled detail is one game's, and would sit under another's name.
struct MacProjectionHistory: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    private let league: LeagueKey
    private let gameId: String

    @State private var detail: LeagueGameProjectionDetail?
    @State private var failure: APIError?

    init(league: LeagueKey, gameId: String) {
        self.league = league
        self.gameId = gameId
    }

    private var key: MacLoadKey {
        MacLoadKey(route: LeagueRoutes.gameProjection(league, gameId: gameId),
                   generation: model.generation(for: league))
    }

    private var lockedText: String {
        guard let locked = detail?.locked else { return "" }
        let home: Double? = locked.home?.projectedPoints
        let away: Double? = locked.away?.projectedPoints
        let score = MacProjectionHistory.scoreText(home: home, away: away)
        if let words = locked.summary, !words.isEmpty {
            return score + " · " + words
        }
        return score
    }

    private var entries: [LeagueProjectionHistoryEntry] {
        detail?.history ?? []
    }

    /// The task hangs on a VStack whose last child is always there. `content` is an if/else-if
    /// chain with no final `else`, so in live mode before the first answer it draws nothing, and
    /// SwiftUI never starts `.task` (or `.onAppear`) on a view that draws nothing: the history
    /// would then never load. The one-point clear anchor makes the stack appear from the start.
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            content
            Color.clear
                .frame(height: 1)
                .accessibilityHidden(true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task(id: key) {
            await load()
        }
    }

    @ViewBuilder private var content: some View {
        if environment.isDemoMode {
            EmptyView()
        } else if !lockedText.isEmpty || !entries.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text("Earlier runs")
                    .hardwoodText(.statLabel)
                if !lockedText.isEmpty {
                    DetailLine(label: "Locked at tip-off", value: lockedText)
                }
                ForEach(Array(entries.enumerated()), id: \.offset) { pair in
                    DetailLine(label: LeagueFormatting.absoluteLocal(pair.element.computedAt),
                               value: entryText(pair.element))
                }
            }
        } else if let problem = failure {
            Text("Earlier runs are not available: " + problem.userMessage)
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func entryText(_ entry: LeagueProjectionHistoryEntry) -> String {
        let score = MacProjectionHistory.scoreText(home: entry.homePts, away: entry.awayPts)
        return LeagueFormatting.modelKindWord(entry.kind) + " · " + score
    }

    /// `84.2–79.1`, only when both sides are present.
    static func scoreText(home: Double?, away: Double?) -> String {
        guard let home = home, let away = away else { return Formatting.emDash }
        return LeagueFormatting.number(home) + "–" + LeagueFormatting.number(away)
    }

    private func load() async {
        if environment.isDemoMode {
            return
        }
        do {
            let result = try await environment.league.get(LeagueGameProjectionDetail.self,
                                                          LeagueRoutes.gameProjection(league, gameId: gameId))
            if Task.isCancelled { return }
            detail = result
            failure = nil
        } catch {
            if Task.isCancelled { return }
            failure = APIError.from(error)
        }
    }
}
#endif
