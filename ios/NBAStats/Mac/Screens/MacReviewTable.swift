#if os(macOS)
import SwiftUI

// The Round Review and Slate Review tables: each reviewed game's projected score next to what
// happened, how far off the projection was, and whether its winner was right; and the summary cards
// above them (the workbook's R2 Review sheet, minus every comparison with an outside number).
//
// WHAT IS REVIEWED, AND UNDER WHICH NAME
// A projection is reviewed only against the result it was made for. The server separates three
// kinds, and so does this screen:
//   locked        frozen before tip-off: the only kind that measures the model as a forecaster
//   imported      the workbook's own projections for rounds it had already played, under its own name
//   reconstructed made after the game from inputs dated before tip-off, a weaker claim, counted
//                 apart and labelled in the Basis column
// A reconstructed row is never folded into the cards above the table.
//
// MISSES ARE SIGNED, AS THE SERVER SENDS THEM
// Margin miss is the actual margin (home minus away) less the projected margin; combined miss is the
// actual combined points less the projected combined points. A negative combined miss means the game
// finished lower than projected. The cards' means are the server's averages of the sizes of the
// misses; the app averages nothing.
//
// A TOSS-UP CALLS NO WINNER
// A projection whose margin is under half a point names no winner, so "winner called" is nil for it
// and the column says "Toss-up".

// MARK: - Words

enum MacReviewWords {

    /// The name of a model in the review: the server's own keys, in words.
    static func model(_ key: String?) -> String {
        guard let key = key, !key.isEmpty else { return Formatting.emDash }
        switch key {
        case "hardwood":
            return "Hardwood model"
        case "workbook":
            return "Your workbook's projections"
        default:
            return key
        }
    }

    /// `✓ Called`, `✗ Missed` or `Toss-up`.
    static func winner(_ called: Bool?) -> String {
        guard let called = called else { return "Toss-up" }
        return called ? "✓ Called" : "✗ Missed"
    }

    /// Called first, then missed, then toss-ups.
    static func winnerOrder(_ called: Bool?) -> Int {
        guard let called = called else { return 2 }
        return called ? 0 : 1
    }

    /// `96–82` from a pair of whole scores, only when both are present.
    static func score(_ pair: LeagueScorePair?) -> String {
        guard let home = pair?.homePts, let away = pair?.awayPts else { return Formatting.emDash }
        return LeagueFormatting.number(home, places: 0) + "–" + LeagueFormatting.number(away, places: 0)
    }

    /// `86.7–85.2`, one decimal.
    static func projectedScore(_ pair: LeagueScorePair?) -> String {
        guard let home = pair?.homePts, let away = pair?.awayPts else { return Formatting.emDash }
        return LeagueFormatting.number(home) + "–" + LeagueFormatting.number(away)
    }

    /// `Winners called 13 of 18 (+2 toss-ups)`.
    static func winnersLine(_ row: LeagueReviewModelRow) -> String {
        var text = "Winners called " + LeagueFormatting.integer(row.winnersCalled)
        if row.decidedGames != nil {
            text += " of " + LeagueFormatting.integer(row.decidedGames)
        }
        if let tossUps = row.tossUps, tossUps > 0 {
            if tossUps == 1 {
                text += " (+1 toss-up)"
            } else {
                text += " (+" + String(tossUps) + " toss-ups)"
            }
        }
        return text
    }
}

// MARK: - Rows

struct MacReviewRow: Identifiable, Hashable {
    let id: String
    let order: Int
    let gameId: String
    let title: String
    let date: String
    let dateSort: String
    let home: String
    let homeName: String
    let away: String
    let awayName: String
    let projectedHome: MacStatValue
    let projectedAway: MacStatValue
    let result: MacStatValue
    let marginMiss: MacStatValue
    let combinedMiss: MacStatValue
    let winner: String
    let winnerSort: Int
    let basis: String
    let basisSort: Int
    let modelKey: String
    let kind: String

    static let copyHeader: [String] = [
        "Date", "Home", "Away", "Proj home", "Proj away", "Result", "Margin miss", "Combined miss",
        "Winner", "Basis"
    ]

    var copyCells: [String] {
        [date, home, away, projectedHome.text, projectedAway.text, result.text, marginMiss.text,
         combinedMiss.text, winner, basis]
    }
}

enum MacReviewRows {

    /// One row per reviewed game, in the order the server sent them (by date).
    static func rows(from review: LeagueProjectionReview?, league: LeagueKey) -> [MacReviewRow] {
        guard let games = review?.games else { return [] }
        var result: [MacReviewRow] = []
        var index = 0
        for entry in games {
            result.append(make(entry, index: index, league: league))
            index += 1
        }
        return result
    }

    private static func make(_ entry: LeagueReviewGame, index: Int, league: LeagueKey) -> MacReviewRow {
        let game: LeagueGameRef? = entry.game
        let kind: String = entry.kind ?? ""
        let shown: LeagueScorePair? = entry.projected ?? entry.locked
        let id: String = (game?.gameId ?? ("game-" + String(index))) + "-" + kind
        return MacReviewRow(
            id: id,
            order: index,
            gameId: game?.gameId ?? "",
            title: game?.matchupText(for: league) ?? Formatting.emDash,
            date: Formatting.shortGameDate(game?.date),
            dateSort: game?.tipoffUtc ?? game?.date ?? "",
            home: game?.home?.displayAbbr ?? Formatting.emDash,
            homeName: game?.home?.displayName ?? Formatting.emDash,
            away: game?.away?.displayAbbr ?? Formatting.emDash,
            awayName: game?.away?.displayName ?? Formatting.emDash,
            projectedHome: MacStatValue(number: shown?.homePts, places: 1),
            projectedAway: MacStatValue(number: shown?.awayPts, places: 1),
            result: MacStatValue(words: MacReviewWords.score(entry.result),
                                 sort: LeagueFormatting.sortKey(entry.result?.homePts)),
            marginMiss: MacStatValue(signed: entry.marginMiss, places: 1),
            combinedMiss: MacStatValue(signed: entry.combinedMiss, places: 1),
            winner: MacReviewWords.winner(entry.winnerCalled),
            winnerSort: MacReviewWords.winnerOrder(entry.winnerCalled),
            basis: LeagueFormatting.modelKindWord(entry.kind),
            basisSort: basisOrder(kind),
            modelKey: entry.modelKey ?? "",
            kind: kind
        )
    }

    /// Locked first, then imported, then reconstructed.
    private static func basisOrder(_ kind: String) -> Int {
        switch kind {
        case "locked":
            return 0
        case "imported":
            return 1
        case "reconstructed":
            return 2
        default:
            return 3
        }
    }

    /// The model keys the review has numbers for, in the order the server sent them.
    static func modelKeys(from review: LeagueProjectionReview?) -> [String] {
        var keys: [String] = []
        for row in review?.byModel ?? [] {
            if let key = row.modelKey, !key.isEmpty, !keys.contains(key) {
                keys.append(key)
            }
        }
        return keys
    }

    /// The rows for the model on screen. A reconstructed row belongs to no model above the table,
    /// so it stays visible whichever model is chosen; with one model or none, every row stays.
    static func filtered(_ rows: [MacReviewRow], modelKeys: [String], selected: String?) -> [MacReviewRow] {
        guard modelKeys.count > 1, let wanted = selected else { return rows }
        return rows.filter { $0.kind == "reconstructed" || $0.modelKey == wanted }
    }
}

// MARK: - Table

/// Review (10 columns).
struct MacReviewTable: View {

    private let rows: [MacReviewRow]
    @Binding private var selection: MacReviewRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacReviewRow>]
    private let onShowDetails: (MacReviewRow.ID) -> Void
    private let onOpenMatchup: (MacReviewRow.ID) -> Void
    private let onOpenBoxScore: (MacReviewRow.ID) -> Void

    init(rows: [MacReviewRow],
         selection: Binding<MacReviewRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacReviewRow>]>,
         onShowDetails: @escaping (MacReviewRow.ID) -> Void,
         onOpenMatchup: @escaping (MacReviewRow.ID) -> Void,
         onOpenBoxScore: @escaping (MacReviewRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
        self.onShowDetails = onShowDetails
        self.onOpenMatchup = onOpenMatchup
        self.onOpenBoxScore = onOpenBoxScore
    }

    private var sortedRows: [MacReviewRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Date", value: \MacReviewRow.dateSort) { row in
                TextCell(text: row.date)
            }
            .width(min: 64, ideal: 80, max: 110)
            TableColumn("Home", value: \MacReviewRow.home) { row in
                TeamCell(abbr: row.home, name: row.homeName)
            }
            .width(min: 62, ideal: 76, max: 110)
            TableColumn("Away", value: \MacReviewRow.away) { row in
                TeamCell(abbr: row.away, name: row.awayName)
            }
            .width(min: 62, ideal: 76, max: 110)
            TableColumn("Proj home", value: \MacReviewRow.projectedHome.sort) { row in
                MacStatCell(value: row.projectedHome)
            }
            .width(min: 64, ideal: 76, max: 100)
            TableColumn("Proj away", value: \MacReviewRow.projectedAway.sort) { row in
                MacStatCell(value: row.projectedAway)
            }
            .width(min: 64, ideal: 76, max: 100)
            TableColumn("Result", value: \MacReviewRow.result.sort) { row in
                MacStatCell(value: row.result)
            }
            .width(min: 60, ideal: 72, max: 96)
            TableColumn("Margin miss", value: \MacReviewRow.marginMiss.sort) { row in
                MacStatCell(value: row.marginMiss)
            }
            .width(min: 80, ideal: 92, max: 120)
            TableColumn("Combined miss", value: \MacReviewRow.combinedMiss.sort) { row in
                MacStatCell(value: row.combinedMiss)
            }
            .width(min: 92, ideal: 104, max: 130)
            TableColumn("Winner", value: \MacReviewRow.winnerSort) { row in
                TextCell(text: row.winner)
            }
            .width(min: 76, ideal: 88, max: 120)
            TableColumn("Basis", value: \MacReviewRow.basisSort) { row in
                TextCell(text: row.basis)
            }
            .width(min: 84, ideal: 100, max: 130)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacReviewRow.ID.self) { ids in
            menuItems(for: ids)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacReviewRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Show Details") {
                onShowDetails(id)
            }
            Button("Open Matchup") {
                onOpenMatchup(id)
            }
            Button("Open Box Score") {
                onOpenBoxScore(id)
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacReviewRow.copyHeader, rows: [row.copyCells])
            }
        }
    }
}

// MARK: - Summary cards

/// One summary card: a title, a note under it and the model's figures.
struct MacReviewCard: Identifiable {
    let id: String
    let title: String
    let note: String
    let lines: [String]
}

enum MacReviewCards {

    /// A card for each model the server has numbers for, and a separate one for the reconstructed
    /// rows (never counted in the others).
    static func cards(from review: LeagueProjectionReview?) -> [MacReviewCard] {
        var result: [MacReviewCard] = []
        for row in review?.byModel ?? [] {
            result.append(card(row, id: "model-" + (row.modelKey ?? "none"), title: MacReviewWords.model(row.modelKey), note: ""))
        }
        if let row = review?.reconstructed {
            result.append(card(row,
                               id: "reconstructed",
                               title: "Reconstructed after the fact",
                               note: "Not counted in the cards above: made after the game, from inputs dated before tip-off."))
        }
        return result
    }

    private static func card(_ row: LeagueReviewModelRow, id: String, title: String, note: String) -> MacReviewCard {
        let lines: [String] = [
            MacReviewWords.winnersLine(row),
            "Mean margin miss " + LeagueFormatting.number(row.meanAbsMarginMiss),
            "Mean score miss " + LeagueFormatting.number(row.meanAbsScoreMiss),
            "Mean combined miss " + LeagueFormatting.number(row.meanAbsCombinedMiss),
            "Games " + LeagueFormatting.integer(row.games)
        ]
        return MacReviewCard(id: id, title: title, note: note, lines: lines)
    }
}

/// The cards in a row above the table.
struct MacReviewCardsRow: View {

    private let cards: [MacReviewCard]

    init(cards: [MacReviewCard]) {
        self.cards = cards
    }

    var body: some View {
        if !cards.isEmpty {
            ScrollView(.horizontal, showsIndicators: PlatformMetrics.showsHorizontalIndicators) {
                HStack(alignment: .top, spacing: Spacing.md) {
                    ForEach(cards) { card in
                        cardView(card)
                    }
                }
                .padding(Spacing.md)
            }
        }
    }

    private func cardView(_ card: MacReviewCard) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text(card.title)
                .hardwoodText(.widgetTitle)
            if !card.note.isEmpty {
                Text(card.note)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(Array(card.lines.enumerated()), id: \.offset) { pair in
                Text(pair.element)
                    .hardwoodText(.tableCell, monospacedDigits: false)
            }
        }
        .frame(width: 260, alignment: .leading)
        .hardwoodCard()
    }
}
#endif
