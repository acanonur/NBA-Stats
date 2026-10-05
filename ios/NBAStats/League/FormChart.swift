import Charts
import SwiftUI

// A team's recent games, three ways: a grouped bar chart (points scored against points allowed, game
// by game), a row of W/L dots, and a list. All three draw the `form` array the server sent, and
// nothing else.
//
// WHAT THE APP DOES NOT DO HERE
// - It computes no average and no trend. Averages are the server's (`pointsPerGame`, `lastN`);
//   the chart only draws the scores of each game.
// - A game with no score is left out, never drawn as a zero-height bar.
// - Bars start at zero. A bar chart whose axis starts at 98 turns a 4-point gap into a cliff.
//
// ORDER
// The server sends `form` newest first. The chart and the dots read left to right through time
// (oldest to newest), the way a form guide does, so they re-order by the games' own dates; the list
// keeps the server's order (newest at the top), which is the order a table is read in.

// MARK: - Order and result marks

/// Re-orders the server's form games for a left-to-right reading.
enum LeagueFormOrder {

    /// Oldest first. The server sends newest first, so this reverses the array when its first
    /// game is later than its last; a list that is already oldest first, or whose dates are missing,
    /// is returned as it came.
    static func chronological(_ games: [LeagueFormGame]) -> [LeagueFormGame] {
        guard let first = games.first?.date, let last = games.last?.date, first > last else {
            return games
        }
        return Array(games.reversed())
    }
}

/// W or L in a coloured capsule. Draws nothing when the result is missing.
struct LeagueResultChip: View {
    private let result: String?

    init(result: String?) {
        self.result = result
    }

    /// Green for a win, red for a loss, grey for anything else.
    static func tint(for result: String?) -> Color {
        switch result ?? "" {
        case "W": return Palette.positive
        case "L": return Palette.negative
        default: return Palette.neutral
        }
    }

    var body: some View {
        if let result = result, !result.isEmpty {
            LeagueChip(text: result, tint: LeagueResultChip.tint(for: result))
        }
    }
}

/// An overtime marker for a game: `OT`, `2OT`, or an empty string.
func leagueOvertimeText(_ periods: Int?) -> String {
    guard let periods = periods, periods > 0 else { return "" }
    if periods == 1 { return "OT" }
    return String(periods) + "OT"
}

// MARK: - Chart

/// Points scored and points allowed in each of a team's recent games, as paired bars, with the
/// league's average points per game as a dashed rule when the server sent one.
struct FormChart: View {

    private struct FormBar: Identifiable {
        let id: String
        let slot: String
        let side: String
        let points: Double
        let color: Color
    }

    private let bars: [FormBar]
    private let slots: [String]
    private let slotLabels: [String: String]
    private let leagueAverage: Double?
    private let height: CGFloat

    private static let scoredName = "Scored"
    private static let allowedName = "Allowed"

    init(games: [LeagueFormGame]?, leagueAverage: Double? = nil, height: CGFloat = 150) {
        let ordered = LeagueFormOrder.chronological(games ?? [])
        var built: [FormBar] = []
        var slotNames: [String] = []
        var labels: [String: String] = [:]
        for (index, game) in ordered.enumerated() {
            // A game with no score on either side is left out, not drawn as a zero.
            guard let scored = game.teamScore, let allowed = game.opponentScore else { continue }
            let slot = String(index)
            slotNames.append(slot)
            labels[slot] = game.opponent?.displayAbbr ?? Formatting.emDash
            built.append(FormBar(id: slot + "-scored",
                                 slot: slot,
                                 side: FormChart.scoredName,
                                 points: Double(scored),
                                 color: Palette.chartColor(at: 0)))
            built.append(FormBar(id: slot + "-allowed",
                                 slot: slot,
                                 side: FormChart.allowedName,
                                 points: Double(allowed),
                                 color: Palette.chartColor(at: 1)))
        }
        self.bars = built
        self.slots = slotNames
        self.slotLabels = labels
        self.leagueAverage = leagueAverage
        self.height = height
    }

    private var yMax: Double {
        var values: [Double] = bars.map { $0.points }
        if let average = leagueAverage, average.isFinite { values.append(average) }
        let top = values.max() ?? 1
        return max(top * 1.1, 1)
    }

    /// The opponent code under a pair of bars. Beyond eight games only every second (third, ...)
    /// game is labelled, so the labels never run into each other; the list below names them all.
    private func axisLabel(for slot: String?) -> String {
        guard let slot = slot, let label = slotLabels[slot] else { return "" }
        let step = max((slots.count + 7) / 8, 1)
        if let index = Int(slot), index % step != 0 { return "" }
        return label
    }

    private var accessibilityText: String {
        var parts: [String] = []
        for slot in slots {
            let scored = bars.first { $0.slot == slot && $0.side == FormChart.scoredName }
            let allowed = bars.first { $0.slot == slot && $0.side == FormChart.allowedName }
            let opponent = slotLabels[slot] ?? Formatting.emDash
            let first = LeagueFormatting.integer(scored.map { Int($0.points) })
            let second = LeagueFormatting.integer(allowed.map { Int($0.points) })
            parts.append(opponent + " " + first + " to " + second)
        }
        return "Points scored and allowed, oldest to newest: " + parts.joined(separator: ", ")
    }

    var body: some View {
        if bars.isEmpty {
            EmptyView()
        } else {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                legend
                chart
            }
        }
    }

    private var legend: some View {
        HStack(spacing: Spacing.md) {
            legendItem(name: FormChart.scoredName, color: Palette.chartColor(at: 0))
            legendItem(name: FormChart.allowedName, color: Palette.chartColor(at: 1))
            if leagueAverage != nil {
                Text("Dashed: league average")
                    .hardwoodText(.caption)
            }
            Spacer(minLength: 0)
        }
    }

    private func legendItem(name: String, color: Color) -> some View {
        HStack(spacing: Spacing.xs) {
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .fill(color)
                .frame(width: 10, height: 10)
                .accessibilityHidden(true)
            Text(name)
                .hardwoodText(.caption, color: Palette.textSecondary)
        }
    }

    private var chart: some View {
        Chart {
            ForEach(bars) { bar in
                BarMark(x: .value("Game", bar.slot),
                        y: .value("Points", bar.points))
                    .position(by: .value("Side", bar.side))
                    .foregroundStyle(bar.color)
                    .cornerRadius(2)
            }
            if let average = leagueAverage {
                RuleMark(y: .value("League average", average))
                    .foregroundStyle(Palette.textTertiary)
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: HardwoodChart.ruleDash))
            }
        }
        .chartXScale(domain: slots)
        .chartYScale(domain: 0...yMax)
        .chartXAxis {
            AxisMarks(position: .bottom) { mark in
                AxisValueLabel {
                    Text(axisLabel(for: mark.as(String.self)))
                        .hardwoodText(.tableHeader, color: Palette.textTertiary)
                }
            }
        }
        .chartYAxis {
            HardwoodChartAxis.metricValues(format: .integer, position: .leading, desiredCount: 3)
        }
        .hardwoodChartStyle()
        .frame(height: height)
        .accessibilityLabel(accessibilityText)
    }
}

// MARK: - Dots

/// The last few results as W and L dots, oldest on the left. Draws nothing without games.
struct FormDots: View {
    private let games: [LeagueFormGame]

    init(games: [LeagueFormGame]?, limit: Int = 5) {
        let ordered = LeagueFormOrder.chronological(games ?? [])
        self.games = Array(ordered.suffix(max(limit, 0)))
    }

    private var summary: String {
        let letters = games.map { $0.result ?? Formatting.emDash }
        return "Last " + String(games.count) + ", oldest to newest: " + letters.joined(separator: " ")
    }

    var body: some View {
        if !games.isEmpty {
            HStack(spacing: Spacing.xs) {
                ForEach(Array(games.enumerated()), id: \.offset) { pair in
                    FormDot(result: pair.element.result)
                }
            }
            .help(summary)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(summary)
        }
    }
}

private struct FormDot: View {
    let result: String?

    var body: some View {
        let tint = LeagueResultChip.tint(for: result)
        ZStack {
            Circle().fill(tint.opacity(0.16))
            Circle().strokeBorder(tint, lineWidth: 1)
            Text(result ?? Formatting.emDash)
                .font(Typography.tableHeader)
                .foregroundStyle(tint)
        }
        .frame(width: 20, height: 20)
    }
}

// MARK: - List

/// A team's recent games as rows, newest first as the server sent them: date, opponent, home or
/// away, result, score. Double-clicking a row calls `onOpenGame` with the game id when the caller
/// gave one.
struct FormList: View {
    private let games: [LeagueFormGame]
    private let onOpenGame: ((String) -> Void)?

    init(games: [LeagueFormGame]?, onOpenGame: ((String) -> Void)? = nil) {
        self.games = games ?? []
        self.onOpenGame = onOpenGame
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            ForEach(Array(games.enumerated()), id: \.offset) { pair in
                row(pair.element)
            }
        }
    }

    private func venueMarker(_ game: LeagueFormGame) -> String {
        if game.isNeutral == true { return "N" }
        guard let isHome = game.isHome else { return "" }
        return isHome ? "H" : "A"
    }

    private func scoreText(_ game: LeagueFormGame) -> String {
        let base = LeagueFormatting.score(game.teamScore, game.opponentScore)
        let overtime = leagueOvertimeText(game.overtimePeriods)
        if overtime.isEmpty { return base }
        return base + " (" + overtime + ")"
    }

    private func row(_ game: LeagueFormGame) -> some View {
        HStack(spacing: Spacing.sm) {
            Text(Formatting.shortGameDate(game.date))
                .hardwoodText(.caption)
                .frame(width: 52, alignment: .leading)
            TeamBadge(abbreviation: game.opponent?.displayAbbr ?? Formatting.emDash,
                      name: game.opponent?.name,
                      size: .small)
            Text(venueMarker(game))
                .hardwoodText(.caption)
                .frame(width: 16)
            LeagueResultChip(result: game.result)
            Text(scoreText(game))
                .hardwoodText(.tableCell)
            Spacer(minLength: 0)
        }
        .contentShape(Rectangle())
        .onTapGesture(count: 2) {
            if let id = game.gameId, let openGame = onOpenGame {
                openGame(id)
            }
        }
        .help(rowHelp)
    }

    private var rowHelp: String {
        if onOpenGame != nil { return "Double-click to open the box score" }
        return ""
    }
}

#if DEBUG
#Preview("Recent form") {
    VStack(alignment: .leading, spacing: Spacing.lg) {
        if let form = LeagueMatchup.preview.teams?.first {
            FormChart(games: form.form, leagueAverage: LeagueMatchup.preview.leagueAverage?.pointsPerGame)
            FormDots(games: form.form)
            FormList(games: form.form)
        }
    }
    .padding(Spacing.lg)
    .hardwoodBackground()
}
#endif
