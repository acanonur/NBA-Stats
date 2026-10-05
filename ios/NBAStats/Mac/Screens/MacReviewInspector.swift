#if os(macOS)
import SwiftUI

// The inspector of the Review screen: one reviewed game in full.
//
// WHAT IT SHOWS
// From the review row itself, always: the projected score, the result, both misses, whether the
// winner was called, and which kind of projection it is (locked before tip-off, imported from the
// workbook, or reconstructed after the game). From the game's own projection route, when the server
// has more to say: the projection that was locked at tip-off and every run of the model since. For a
// EuroLeague game, also the quarter scores, the venue and the attendance (from its box score) and
// the rating updates the game caused (from the club ratings): the workbook's rating-change columns.
//
// HOW IT LOADS
// All of it in one `.task(id:)` on the inspector's root, which always has content (the header), so
// the task is never attached to an empty view. Each extra is optional: a request that fails only
// leaves its section out. Demo mode loads none of them, because a bundled answer describes one
// invented game and would sit under another's name.

private struct MacReviewInspectorKey: Hashable {
    let gameId: String
    let generation: Int
}

struct MacReviewInspector: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    private let league: LeagueKey
    private let row: MacReviewRow
    private let onOpenMatchup: () -> Void
    private let onOpenBoxScore: () -> Void

    @State private var detail: LeagueGameProjectionDetail?
    @State private var box: ElBoxScore?
    @State private var ratings: ElRatingsTable?

    init(league: LeagueKey,
         row: MacReviewRow,
         onOpenMatchup: @escaping () -> Void,
         onOpenBoxScore: @escaping () -> Void) {
        self.league = league
        self.row = row
        self.onOpenMatchup = onOpenMatchup
        self.onOpenBoxScore = onOpenBoxScore
    }

    private var key: MacReviewInspectorKey {
        MacReviewInspectorKey(gameId: row.gameId, generation: model.generation(for: league))
    }

    private var projectedText: String {
        if row.projectedHome.text == Formatting.emDash || row.projectedAway.text == Formatting.emDash {
            return Formatting.emDash
        }
        return row.projectedHome.text + "–" + row.projectedAway.text
    }

    // MARK: Body

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.lg) {
                header
                facts
                lockedSection
                historySection
                boxSection
                updatesSection
                actions
            }
            .padding(Spacing.lg)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task(id: key) {
            await load()
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text(row.title)
                .hardwoodText(.sectionTitle)
            Text(row.date + " · " + row.basis)
                .hardwoodText(.caption)
        }
    }

    private var facts: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            DetailLine(label: "Model", value: MacReviewWords.model(row.modelKey))
            DetailLine(label: "Projected", value: projectedText)
            DetailLine(label: "Result", value: row.result.text)
            DetailLine(label: "Margin miss", value: row.marginMiss.text)
            DetailLine(label: "Combined miss", value: row.combinedMiss.text)
            DetailLine(label: "Winner", value: row.winner)
        }
    }

    // MARK: Locked projection and earlier runs

    @ViewBuilder private var lockedSection: some View {
        if let locked = detail?.locked {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text("Locked at tip-off")
                    .hardwoodText(.statLabel)
                Text(lockedText(locked))
                    .hardwoodText(.tableCell, monospacedDigits: false)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func lockedText(_ locked: LeagueGameProjection) -> String {
        let home: String = LeagueFormatting.number(locked.home?.projectedPoints)
        let away: String = LeagueFormatting.number(locked.away?.projectedPoints)
        var text: String = home + "–" + away
        if let words = locked.summary, !words.isEmpty {
            text += " · " + words
        }
        return text
    }

    @ViewBuilder private var historySection: some View {
        let entries: [LeagueProjectionHistoryEntry] = detail?.history ?? []
        if !entries.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text("Every run of the model")
                    .hardwoodText(.statLabel)
                ForEach(Array(entries.enumerated()), id: \.offset) { pair in
                    DetailLine(label: LeagueFormatting.absoluteLocal(pair.element.computedAt),
                               value: historyText(pair.element))
                }
            }
        }
    }

    private func historyText(_ entry: LeagueProjectionHistoryEntry) -> String {
        let home: String = LeagueFormatting.number(entry.homePts)
        let away: String = LeagueFormatting.number(entry.awayPts)
        return LeagueFormatting.modelKindWord(entry.kind) + " · " + home + "–" + away
    }

    // MARK: EuroLeague extras

    @ViewBuilder private var boxSection: some View {
        if let loaded = box, loaded.game?.gameId == row.gameId {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text("The game")
                    .hardwoodText(.statLabel)
                boxFacts(loaded)
            }
        }
    }

    @ViewBuilder private func boxFacts(_ loaded: ElBoxScore) -> some View {
        let partials: String = MacBoxHeaderInfo.partialsText(loaded.partials)
        if !partials.isEmpty {
            DetailLine(label: "By period", value: partials)
        }
        if let venue = loaded.game?.venue, !venue.isEmpty {
            DetailLine(label: "Venue", value: venue)
        }
        if let people = loaded.attendance {
            DetailLine(label: "Attendance", value: LeagueFormatting.number(people, places: 0))
        }
    }

    @ViewBuilder private var updatesSection: some View {
        let lines: [MacRatingUpdateLine] = MacRatingUpdateLines.lines(forGame: row.gameId, from: ratings?.updates)
        if !lines.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text("Rating updates from this game")
                    .hardwoodText(.statLabel)
                ForEach(lines) { line in
                    VStack(alignment: .leading, spacing: 1) {
                        Text(line.title)
                            .hardwoodText(.tableCell, monospacedDigits: false)
                        Text(line.detail)
                            .hardwoodText(.caption)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private var actions: some View {
        HStack(spacing: Spacing.sm) {
            Button("Open Matchup") {
                onOpenMatchup()
            }
            Button("Open Box Score") {
                onOpenBoxScore()
            }
            Spacer(minLength: 0)
        }
    }

    // MARK: Loading

    private func load() async {
        if row.gameId.isEmpty || environment.isDemoMode {
            return
        }
        await loadDetail()
        if league == .euroleague {
            await loadBox()
            await loadRatings()
        }
    }

    private func loadDetail() async {
        do {
            let route = LeagueRoutes.gameProjection(league, gameId: row.gameId)
            let result = try await environment.league.get(LeagueGameProjectionDetail.self, route)
            if Task.isCancelled { return }
            detail = result
        } catch {
            if Task.isCancelled { return }
            detail = nil
        }
    }

    private func loadBox() async {
        do {
            let result = try await environment.league.get(ElBoxScore.self, LeagueRoutes.elBox(row.gameId))
            if Task.isCancelled { return }
            box = result
        } catch {
            if Task.isCancelled { return }
            box = nil
        }
    }

    private func loadRatings() async {
        do {
            let result = try await environment.league.get(ElRatingsTable.self, LeagueRoutes.elRatings())
            if Task.isCancelled { return }
            ratings = result
        } catch {
            if Task.isCancelled { return }
            ratings = nil
        }
    }
}
#endif
