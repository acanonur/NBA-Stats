#if os(macOS)
import Charts
import SwiftUI

// The player card the Season Stats and Projected Scorers inspectors show: who the player is, the
// season line the table row carries, and (EuroLeague) the player's official games with a small bar
// chart of the points scored in each.
//
// WHAT THE CARD ADDS TO THE ROW
// The table row is a summary. The card repeats it as a list a reader can select text from, and adds
// the one thing a row cannot hold: the game log, read from `GET /v1/el/players/{personCode}/gamelog`
// when the card opens. The log is only drawn when the server's answer is for the player the card is
// about (the bundled demo answer is one invented player's, and would otherwise sit under another's
// name).
//
// NBA players have a full detail screen already (`PlayerDetailScreen`), so the NBA card is the
// season line and an "Open player" button that shows that screen in a sheet.
//
// WHAT IS NEVER DONE
// A game the player did not play shows "DNP" and no bar, never a zero. Every number is a value the
// server sent, formatted by `LeagueFormatting`; the chart only draws the points of each game.

// MARK: - Header

/// A badge, the player's name and one line of detail (position, jersey, club).
struct MacPlayerHeader: View {

    private let name: String
    private let detail: String
    private let teamAbbr: String
    private let teamName: String?

    init(name: String, detail: String, teamAbbr: String, teamName: String?) {
        self.name = name
        self.detail = detail
        self.teamAbbr = teamAbbr
        self.teamName = teamName
    }

    var body: some View {
        HStack(spacing: Spacing.md) {
            TeamBadge(abbreviation: teamAbbr, name: teamName, size: .large)
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                Text(name)
                    .hardwoodText(.sectionTitle)
                    .lineLimit(2)
                if !detail.isEmpty {
                    Text(detail)
                        .hardwoodText(.caption)
                        .lineLimit(2)
                }
            }
            Spacer(minLength: 0)
        }
    }
}

/// `Guard · #62 · Alderwick Herons`: the facts that fit on one line.
enum MacPlayerDetailText {

    static func line(position: String, jersey: String, team: String) -> String {
        var parts: [String] = []
        if !position.isEmpty { parts.append(position) }
        if !jersey.isEmpty { parts.append("#" + jersey) }
        if !team.isEmpty { parts.append(team) }
        return parts.joined(separator: " · ")
    }
}

// MARK: - EuroLeague card

/// The EuroLeague player card for a Season Stats row.
struct MacElPlayerPanel: View {

    private let row: MacElStatRow
    private let perMode: String

    init(row: MacElStatRow, perMode: String) {
        self.row = row
        self.perMode = perMode
    }

    private var modeWords: String {
        switch perMode {
        case "Totals":
            return "Season totals"
        case "Per40":
            return "Per 40 minutes"
        default:
            return "Per game"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            MacPlayerHeader(name: row.player,
                            detail: MacPlayerDetailText.line(position: row.position,
                                                             jersey: row.jersey,
                                                             team: row.clubName),
                            teamAbbr: row.club,
                            teamName: row.clubName)
            seasonLine
            MacElGameLogSection(personCode: row.personCode)
                .id(row.personCode)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var seasonLine: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("Season · " + modeWords)
                .hardwoodText(.statLabel)
            DetailLine(label: "Games", value: row.games.text)
            DetailLine(label: "Minutes", value: row.minutes.text)
            DetailLine(label: "Points", value: row.points.text)
            DetailLine(label: "Rebounds", value: row.rebounds.text)
            DetailLine(label: "Assists", value: row.assists.text)
            seasonLineMore
        }
    }

    private var seasonLineMore: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            DetailLine(label: "Steals", value: row.steals.text)
            DetailLine(label: "Blocks", value: row.blocks.text)
            DetailLine(label: "Turnovers", value: row.turnovers.text)
            DetailLine(label: "3-pointers made", value: row.threesMade.text)
            DetailLine(label: "2-point %", value: row.twoPointPct.text)
            DetailLine(label: "3-point %", value: row.threePointPct.text)
            DetailLine(label: "Free-throw %", value: row.freeThrowPct.text)
            DetailLine(label: "PIR", value: row.pir.text)
        }
    }
}

// MARK: - Game log

/// One game of the log as the card shows it.
struct MacGameLogLine: Identifiable, Hashable {
    let id: String
    let date: String
    let opponent: String
    let result: String
    let scoreHelp: String
    let minutes: String
    let points: String
    let rebounds: String
    let assists: String
    let pir: String
    let pointsValue: Double?
}

enum MacGameLogRows {

    /// The log's games in the order the server sent them (newest first).
    static func lines(from log: ElPlayerGameLog?) -> [MacGameLogLine] {
        guard let games = log?.games else { return [] }
        var result: [MacGameLogLine] = []
        var index = 0
        for entry in games {
            result.append(line(entry, index: index))
            index += 1
        }
        return result
    }

    private static func line(_ entry: ElGameLogRow, index: Int) -> MacGameLogLine {
        let id: String = entry.game?.gameId ?? ("game-" + String(index))
        let stats: ElStatLine? = entry.stats
        let played: Bool = entry.participation != "dnp" && stats != nil
        let mark: String = LeagueFormatting.venueMark(isHome: entry.isHome, isNeutral: entry.isNeutral)
        let opponentCode: String = entry.opponent?.displayAbbr ?? Formatting.emDash
        let opponent: String = (mark + " " + opponentCode).trimmingCharacters(in: .whitespaces)
        let score: String = LeagueFormatting.score(entry.teamScore, entry.opponentScore)

        var minutes: String = "DNP"
        var points: String = Formatting.emDash
        var rebounds: String = Formatting.emDash
        var assists: String = Formatting.emDash
        var pir: String = Formatting.emDash
        var pointsValue: Double?
        if played {
            minutes = LeagueFormatting.minutes(stats?.minutes)
            points = LeagueFormatting.number(stats?.pts, places: 0)
            rebounds = LeagueFormatting.number(stats?.reb, places: 0)
            assists = LeagueFormatting.number(stats?.ast, places: 0)
            pir = LeagueFormatting.number(stats?.pir, places: 0)
            pointsValue = stats?.pts
        }
        return MacGameLogLine(
            id: id,
            date: Formatting.shortGameDate(entry.game?.date),
            opponent: opponent,
            result: entry.result ?? "",
            scoreHelp: score,
            minutes: minutes,
            points: points,
            rebounds: rebounds,
            assists: assists,
            pir: pir,
            pointsValue: pointsValue
        )
    }
}

/// The card's game-log section. Reads the player's log on its own, so it can be dropped under any
/// player.
struct MacElGameLogSection: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    private let personCode: String

    @State private var log: ElPlayerGameLog?
    @State private var failure: APIError?
    @State private var isLoading = false

    init(personCode: String) {
        self.personCode = personCode
    }

    private var key: MacLoadKey {
        MacLoadKey(route: LeagueRoutes.elGameLog(personCode),
                   generation: model.generation(for: .euroleague))
    }

    /// True unless the server answered for a different player (the bundled demo answer does).
    private var isForThisPlayer: Bool {
        guard let code = log?.player?.personCode else { return true }
        return code == personCode
    }

    private var lines: [MacGameLogLine] {
        isForThisPlayer ? MacGameLogRows.lines(from: log) : []
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            Text("Game log")
                .hardwoodText(.statLabel)
            content
        }
        .task(id: key) {
            await load()
        }
    }

    @ViewBuilder private var content: some View {
        if log == nil, let problem = failure {
            Text("The game log is not available: " + problem.userMessage)
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        } else if log == nil {
            ProgressView()
                .controlSize(.small)
        } else if lines.isEmpty {
            Text(emptyText)
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                MacGameLogBars(lines: lines)
                MacGameLogTable(lines: lines)
            }
        }
    }

    private var emptyText: String {
        if environment.isDemoMode && !isForThisPlayer {
            return "Demo data has no game log for this player."
        }
        return "No official games yet."
    }

    private func load() async {
        isLoading = true
        do {
            let result = try await environment.league.get(ElPlayerGameLog.self,
                                                          LeagueRoutes.elGameLog(personCode))
            if Task.isCancelled { return }
            log = result
            failure = nil
        } catch {
            if Task.isCancelled { return }
            log = nil
            failure = APIError.from(error)
        }
        isLoading = false
    }
}

/// The log as short rows: date, opponent, result, minutes, points, rebounds, assists, PIR.
struct MacGameLogTable: View {

    private let lines: [MacGameLogLine]

    init(lines: [MacGameLogLine]) {
        self.lines = lines
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            headerRow
            ForEach(lines) { line in
                row(line)
            }
        }
    }

    private var headerRow: some View {
        HStack(spacing: Spacing.xs) {
            head("Date", width: 46, alignment: .leading)
            head("Opp", width: 58, alignment: .leading)
            head("Res", width: 24, alignment: .leading)
            head("MIN", width: 34, alignment: .trailing)
            head("PTS", width: 28, alignment: .trailing)
            head("REB", width: 28, alignment: .trailing)
            head("AST", width: 28, alignment: .trailing)
            head("PIR", width: 28, alignment: .trailing)
            Spacer(minLength: 0)
        }
    }

    private func head(_ title: String, width: CGFloat, alignment: Alignment) -> some View {
        Text(title)
            .hardwoodText(.tableHeader, color: Palette.textTertiary)
            .frame(width: width, alignment: alignment)
    }

    private func cell(_ text: String, width: CGFloat, alignment: Alignment) -> some View {
        Text(text)
            .hardwoodText(.caption)
            .lineLimit(1)
            .frame(width: width, alignment: alignment)
    }

    private func row(_ line: MacGameLogLine) -> some View {
        HStack(spacing: Spacing.xs) {
            cell(line.date, width: 46, alignment: .leading)
            cell(line.opponent, width: 58, alignment: .leading)
            cell(line.result, width: 24, alignment: .leading)
                .help(line.scoreHelp)
            cell(line.minutes, width: 34, alignment: .trailing)
            cell(line.points, width: 28, alignment: .trailing)
            cell(line.rebounds, width: 28, alignment: .trailing)
            cell(line.assists, width: 28, alignment: .trailing)
            cell(line.pir, width: 28, alignment: .trailing)
            Spacer(minLength: 0)
        }
    }
}

/// Points scored in each game, oldest to newest, as small bars that start at zero. A game the
/// player did not play has no bar.
struct MacGameLogBars: View {

    private struct Bar: Identifiable {
        let id: String
        let slot: String
        let points: Double
    }

    private let bars: [Bar]
    private let slots: [String]

    init(lines: [MacGameLogLine]) {
        var built: [Bar] = []
        var slotNames: [String] = []
        let oldestFirst: [MacGameLogLine] = Array(lines.reversed())
        for (index, line) in oldestFirst.enumerated() {
            let slot = String(index)
            slotNames.append(slot)
            if let points = line.pointsValue {
                built.append(Bar(id: line.id + "-" + slot, slot: slot, points: points))
            }
        }
        self.bars = built
        self.slots = slotNames
    }

    private var yMax: Double {
        let top: Double = bars.map { $0.points }.max() ?? 1
        return Swift.max(top * 1.1, 1)
    }

    var body: some View {
        if bars.isEmpty {
            EmptyView()
        } else {
            Chart {
                ForEach(bars) { bar in
                    BarMark(x: .value("Game", bar.slot),
                            y: .value("Points", bar.points))
                        .foregroundStyle(Palette.chartColor(at: 0))
                        .cornerRadius(2)
                }
            }
            .chartXScale(domain: slots)
            .chartXAxis(.hidden)
            .chartYScale(domain: 0...yMax)
            .frame(height: 90)
            .accessibilityLabel("Points in each game, oldest to newest")
        }
    }
}

// MARK: - NBA card

/// The NBA player card for a Season Stats or Projected Scorers row: the line the row carries and a
/// button that opens the full player screen.
struct MacNbaPlayerPanel: View {

    private let row: MacNbaStatRow
    private let onOpenPlayer: () -> Void

    init(row: MacNbaStatRow, onOpenPlayer: @escaping () -> Void) {
        self.row = row
        self.onOpenPlayer = onOpenPlayer
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            MacPlayerHeader(name: row.player,
                            detail: MacPlayerDetailText.line(position: row.position,
                                                             jersey: "",
                                                             team: row.teamName),
                            teamAbbr: row.team,
                            teamName: row.teamName)
            seasonLine
            Button("Open player") {
                onOpenPlayer()
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var seasonLine: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("Season · per game")
                .hardwoodText(.statLabel)
            DetailLine(label: "Games", value: row.games.text)
            DetailLine(label: "Minutes", value: row.minutes.text)
            DetailLine(label: "Points", value: row.points.text)
            DetailLine(label: "Rebounds", value: row.rebounds.text)
            DetailLine(label: "Assists", value: row.assists.text)
            seasonLineMore
        }
    }

    private var seasonLineMore: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            DetailLine(label: "Steals", value: row.steals.text)
            DetailLine(label: "Blocks", value: row.blocks.text)
            DetailLine(label: "Turnovers", value: row.turnovers.text)
            DetailLine(label: "Field-goal %", value: row.fieldGoalPct.text)
            DetailLine(label: "3-point %", value: row.threePointPct.text)
            DetailLine(label: "Free-throw %", value: row.freeThrowPct.text)
            DetailLine(label: "True shooting %", value: row.trueShootingPct.text)
            DetailLine(label: "Usage %", value: row.usagePct.text)
            DetailLine(label: "Net rating", value: row.netRating.text)
        }
    }
}

/// The existing player screen in a sheet, with a Done button (a pushed page inside a sheet has no
/// Back control on the Mac).
struct MacNbaPlayerSheet: View {

    @EnvironmentObject private var environment: AppEnvironment
    @Environment(\.dismiss) private var dismiss

    private let player: PlayerRef

    init(player: PlayerRef) {
        self.player = player
    }

    var body: some View {
        VStack(spacing: 0) {
            NavigationStack {
                PlayerDetailScreen(player: player)
            }
            Divider()
            HStack {
                Spacer()
                Button("Done") {
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
            }
            .padding(Spacing.md)
        }
        .environmentObject(environment)
        .hardwoodSheetFrame(minWidth: 760, minHeight: 640)
    }
}
#endif
