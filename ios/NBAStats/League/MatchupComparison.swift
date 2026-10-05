import SwiftUI

// The pieces of the matchup comparison: one side of the game (`MatchupSideColumn`) built from a
// team's latest score, what it scores and allows per game, its recent form, its home and away
// splits, how it fares against these opponents, who is missing, and how its defence does by
// position.
//
// THE USER'S FIRST QUESTION, ANSWERED WITH THE SERVER'S NUMBERS
// "What did each team score last, and what do they average, and what do they let opponents score?"
// `LatestScoreCard` is the first part, `ScoringBlock` the second (Scores) and the third (Allows).
// Every number is a field of `LeagueTeamForm`; this file formats and arranges, and computes nothing.
//
// ONE VIEW FOR A TILE AND A SCREEN
// `density: .compact` draws the dashboard tile's version (badge and record, latest score, scores and
// allows, margin, form dots); `.full` draws the screen's. `includesExtras` adds, to a compact
// column, the large tile's extra rows (last-N and last-10 averages, home and away splits, a
// defence-by-position summary, the availability counts), which are built from the same views the
// screen uses, so a tile and its screen can never disagree about a number.
//
// HONEST EMPTY STATES
// A team with no games yet shows "No games yet this season" and no figures: every field is nil, and
// a nil is never drawn as a zero.

// MARK: - The game line

/// The game a matchup is about: who plays whom, when, and where, with the projection's one-line
/// summary when the server sent one. Without a game it says the next opponent is not scheduled.
struct MatchupGameLine: View {
    private let game: LeagueGameRef?
    private let league: LeagueKey
    private let summary: String?

    init(game: LeagueGameRef?, league: LeagueKey, summary: String? = nil) {
        self.game = game
        self.league = league
        self.summary = summary
    }

    private var timeText: String {
        guard let game = game else { return "" }
        return LeagueFormatting.gameStatusText(game)
    }

    private var berlinHelp: String {
        guard league == .euroleague, let game = game else { return "" }
        let time = LeagueFormatting.berlinTime(game.tipoffUtc)
        return time == Formatting.emDash ? "" : "Berlin time: " + time
    }

    var body: some View {
        if let game = game {
            HStack(spacing: Spacing.sm) {
                Text(game.matchupText(for: league))
                    .hardwoodText(.widgetTitle)
                Text(timeText)
                    .hardwoodText(.caption)
                    .help(berlinHelp)
                if game.status == "resultPending" {
                    ResultPendingChip()
                }
                if game.isNeutral == true {
                    LeagueChip(text: "Neutral venue", tint: Palette.neutral)
                }
                if let venue = game.venue, !venue.isEmpty {
                    Text(venue)
                        .hardwoodText(.caption)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                if let headline = summary, !headline.isEmpty {
                    Text(headline)
                        .hardwoodText(.tableCell)
                }
            }
        } else {
            Text("Next opponent not scheduled")
                .hardwoodText(.caption)
        }
    }
}

// MARK: - Latest score

/// A team's most recent game: result, score, opponent, home or away, date, and overtime.
struct LatestScoreCard: View {
    private let game: LeagueFormGame?
    private let density: LeagueDensity

    init(game: LeagueFormGame?, density: LeagueDensity = .full) {
        self.game = game
        self.density = density
    }

    private var scoreStyle: HardwoodTextStyle {
        density == .full ? HardwoodTextStyle.statValue : HardwoodTextStyle.widgetTitle
    }

    private var opponentText: String {
        "v " + (game?.opponent?.displayAbbr ?? Formatting.emDash)
    }

    /// H or A, and N for a neutral floor. Missing when the server did not say.
    private var venueTag: String {
        if game?.isNeutral == true { return "N" }
        guard let isHome = game?.isHome else { return "" }
        return isHome ? "H" : "A"
    }

    private var detailText: String {
        var parts: [String] = []
        if let day = game?.date, !day.isEmpty {
            parts.append(LeagueFormatting.leagueDate(day))
        }
        let overtime = leagueOvertimeText(game?.overtimePeriods)
        if !overtime.isEmpty { parts.append(overtime) }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            Text("Latest game")
                .hardwoodText(.statLabel)
            if let game = game {
                HStack(spacing: Spacing.sm) {
                    LeagueResultChip(result: game.result)
                    Text(LeagueFormatting.score(game.teamScore, game.opponentScore))
                        .hardwoodText(scoreStyle)
                    Text(opponentText)
                        .hardwoodText(.tableCell)
                    if !venueTag.isEmpty {
                        Text(venueTag)
                            .hardwoodText(.caption)
                    }
                    Spacer(minLength: 0)
                }
                if !detailText.isEmpty {
                    Text(detailText)
                        .hardwoodText(.caption)
                }
            } else {
                Text(Formatting.emDash)
                    .hardwoodText(.tableCell, color: Palette.textTertiary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Scores and allows

/// A thin track with the league's average as a tick and the team's own value as a dot. Both sit on
/// one shared scale, so Scores and Allows can be compared by eye. The dot is a position, not a bar,
/// so a scale that does not start at zero does not exaggerate a difference.
struct ReferenceTrack: View {
    private let value: Double?
    private let reference: Double?
    private let domain: ClosedRange<Double>

    init(value: Double?, reference: Double?, domain: ClosedRange<Double>) {
        self.value = value
        self.reference = reference
        self.domain = domain
    }

    private func xPosition(of point: Double, width: CGFloat) -> CGFloat {
        let span = domain.upperBound - domain.lowerBound
        guard span > 0, point.isFinite else { return 0 }
        let fraction = min(max((point - domain.lowerBound) / span, 0), 1)
        return CGFloat(fraction) * width
    }

    var body: some View {
        GeometryReader { proxy in
            let width = proxy.size.width
            ZStack(alignment: .leading) {
                Capsule(style: .continuous)
                    .fill(Palette.track)
                    .frame(height: 4)
                if let reference = reference {
                    Rectangle()
                        .fill(Palette.textSecondary)
                        .frame(width: 2, height: 10)
                        .offset(x: xPosition(of: reference, width: width) - 1)
                }
                if let value = value {
                    Circle()
                        .fill(Palette.selection)
                        .frame(width: 10, height: 10)
                        .offset(x: xPosition(of: value, width: width) - 5)
                }
            }
            .frame(width: width, height: proxy.size.height, alignment: .leading)
        }
        .accessibilityHidden(true)
    }
}

/// What a team scores and what it allows, per game: two numbers, each with its games count and the
/// league's average as a tick, and the per-game margin. `usesRegulation` swaps in the figures
/// scaled to a regulation game (overtime scaled out); the league reference is then hidden, because
/// the server sends it only per game, and so is the margin.
struct ScoringBlock: View {
    private let form: LeagueTeamForm
    private let leagueAverage: Double?
    private let usesRegulation: Bool
    private let regulationMinutes: Int?
    private let density: LeagueDensity

    init(team form: LeagueTeamForm,
         leagueAverage: Double? = nil,
         usesRegulation: Bool = false,
         regulationMinutes: Int? = nil,
         density: LeagueDensity = .full) {
        self.form = form
        self.leagueAverage = leagueAverage
        self.usesRegulation = usesRegulation
        self.regulationMinutes = regulationMinutes
        self.density = density
    }

    private var scores: Double? {
        usesRegulation ? form.pointsPerRegulation : form.pointsPerGame
    }

    private var allows: Double? {
        usesRegulation ? form.pointsAllowedPerRegulation : form.pointsAllowedPerGame
    }

    private var reference: Double? {
        usesRegulation ? nil : leagueAverage
    }

    private var gamesText: String {
        guard let games = form.games else { return "" }
        return LeagueFormatting.integer(games) + (games == 1 ? " game" : " games")
    }

    private var unitText: String {
        guard usesRegulation else { return "per game" }
        if let minutes = regulationMinutes { return "per " + String(minutes) + " minutes" }
        return "per regulation game"
    }

    private var domain: ClosedRange<Double> {
        var values: [Double] = []
        if let value = scores { values.append(value) }
        if let value = allows { values.append(value) }
        if let value = reference { values.append(value) }
        return HardwoodChart.paddedDomain(for: values, fallback: 0...1, padding: 0.5)
    }

    private var numberStyle: HardwoodTextStyle {
        density == .full ? HardwoodTextStyle.displayValue : HardwoodTextStyle.statValue
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            HStack(alignment: .top, spacing: Spacing.lg) {
                figure(title: "Scores", value: scores, caption: "points scored " + unitText)
                figure(title: "Allows", value: allows, caption: "points opponents score " + unitText)
                Spacer(minLength: 0)
            }
            marginLine
        }
    }

    private func figure(title: String, value: Double?, caption: String) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            Text(title)
                .hardwoodText(.statLabel)
            Text(LeagueFormatting.number(value))
                .hardwoodText(numberStyle)
            if density == .full {
                ReferenceTrack(value: value, reference: reference, domain: domain)
                    .frame(width: 112, height: 10)
                Text(caption)
                    .hardwoodText(.caption)
                if !gamesText.isEmpty {
                    Text(gamesText)
                        .hardwoodText(.caption)
                }
            }
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var marginLine: some View {
        if !usesRegulation {
            HStack(spacing: Spacing.xs) {
                Text("Margin")
                    .hardwoodText(.statLabel)
                Text(LeagueFormatting.signed(form.differentialPerGame))
                    .hardwoodText(.tableCell, color: Palette.value(for: form.differentialPerGame, higherIsBetter: true))
                Text("per game")
                    .hardwoodText(.caption)
            }
        }
    }
}

// MARK: - Windows, splits, adjusted

/// The average of a window of recent games, in one sentence: `Last 5: 81.2 for, 79.0 against (5
/// games)`, or `(only 3 games)` when the team has played fewer than the window. Draws nothing
/// without a window.
struct WindowAverageLine: View {
    private let window: LeagueWindowAverage?

    init(window: LeagueWindowAverage?) {
        self.window = window
    }

    private var gamesText: String {
        guard let window = window, let played = window.games else { return "" }
        let noun = played == 1 ? " game" : " games"
        if let asked = window.window, played < asked {
            return "only " + String(played) + noun
        }
        return LeagueFormatting.integer(played) + noun
    }

    private var text: String {
        guard let window = window else { return "" }
        let title = LeagueFormatting.windowText(window.window)
        if window.pointsPerGame == nil && window.pointsAllowedPerGame == nil {
            return title + ": " + Formatting.emDash
        }
        var sentence = title + ": "
        sentence += LeagueFormatting.number(window.pointsPerGame) + " for, "
        sentence += LeagueFormatting.number(window.pointsAllowedPerGame) + " against"
        if !gamesText.isEmpty { sentence += " (" + gamesText + ")" }
        return sentence
    }

    var body: some View {
        if window != nil {
            Text(text)
                .hardwoodText(.tableCell)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// Home, away and (when the team has played one) neutral-floor games: how many, and what the team
/// scored and allowed in them.
struct VenueSplitsGrid: View {
    private let splits: LeagueVenueSplits?

    private struct SplitRow: Identifiable {
        let title: String
        let split: LeagueSplit?
        var id: String { title }
    }

    init(splits: LeagueVenueSplits?) {
        self.splits = splits
    }

    private var rows: [SplitRow] {
        var found: [SplitRow] = [
            SplitRow(title: "Home", split: splits?.home),
            SplitRow(title: "Away", split: splits?.away)
        ]
        if let neutral = splits?.neutral {
            found.append(SplitRow(title: "Neutral", split: neutral))
        }
        return found
    }

    var body: some View {
        Grid(alignment: .trailing, horizontalSpacing: Spacing.md, verticalSpacing: Spacing.xs) {
            GridRow {
                Text("Venue")
                    .hardwoodText(.tableHeader)
                    .gridColumnAlignment(.leading)
                Text("GP")
                    .hardwoodText(.tableHeader)
                Text("Scores")
                    .hardwoodText(.tableHeader)
                Text("Allows")
                    .hardwoodText(.tableHeader)
            }
            ForEach(rows) { row in
                GridRow {
                    Text(row.title)
                        .hardwoodText(.tableCell)
                        .gridColumnAlignment(.leading)
                    Text(LeagueFormatting.integer(row.split?.games))
                        .hardwoodText(.tableCell)
                    Text(LeagueFormatting.number(row.split?.pointsPerGame))
                        .hardwoodText(.tableCell)
                    Text(LeagueFormatting.number(row.split?.pointsAllowedPerGame))
                        .hardwoodText(.tableCell)
                }
            }
        }
    }
}

/// How a team did against these opponents compared with what they usually do, in a sentence:
/// "Allows 2.1 fewer than these opponents usually score (7 games)". When the server could not
/// produce the value it says how many qualifying games there are; `minimumGames` (when the caller
/// knows it) says how many are needed.
struct AdjustedSentence: View {

    enum Kind {
        case allows
        case scores
    }

    private let adjusted: LeagueAdjusted?
    private let kind: Kind
    private let minimumGames: Int?

    init(adjusted: LeagueAdjusted?, kind: Kind, minimumGames: Int? = nil) {
        self.adjusted = adjusted
        self.kind = kind
        self.minimumGames = minimumGames
    }

    private var missingText: String {
        var have = Formatting.emDash
        if let games = adjusted?.games { have = String(games) }
        var sentence = ""
        if let need = minimumGames {
            sentence += "Needs " + String(need)
            sentence += " qualifying games (has " + have + ")"
        } else {
            sentence += "Not enough qualifying games yet (has " + have + ")"
        }
        return sentence
    }

    private func adjustedText(for value: Double, games: Int?) -> String {
        let size = LeagueFormatting.number(abs(value))
        let direction = value < 0 ? " fewer" : " more"
        var closing = ""
        if let games = games {
            closing += " (" + LeagueFormatting.integer(games)
            closing += games == 1 ? " game)" : " games)"
        }
        var sentence = ""
        switch kind {
        case .allows:
            if abs(value) < 0.05 {
                sentence += "Allows about as many as these opponents usually score"
            } else {
                sentence += "Allows " + size + direction
                sentence += " than these opponents usually score"
            }
        case .scores:
            if abs(value) < 0.05 {
                sentence += "Scores about as many as these opponents usually allow"
            } else {
                sentence += "Scores " + size + direction
                sentence += " than these opponents usually allow"
            }
        }
        return sentence + closing
    }

    private var text: String {
        guard let adjusted = adjusted else { return "" }
        guard let value = adjusted.value, value.isFinite else { return missingText }
        return adjustedText(for: value, games: adjusted.games)
    }

    var body: some View {
        if adjusted != nil {
            Text(text)
                .hardwoodText(.caption, color: Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - One side of the matchup

/// One team's column of the matchup. See the file comment for what each density draws.
struct MatchupSideColumn: View {
    private let form: LeagueTeamForm
    private let density: LeagueDensity
    private let leagueAverage: Double?
    private let usesRegulation: Bool
    private let regulationMinutes: Int?
    private let includesExtras: Bool
    private let onOpenGame: ((String) -> Void)?
    private let onOpenDefense: (() -> Void)?

    init(team form: LeagueTeamForm,
         density: LeagueDensity,
         leagueAverage: Double? = nil,
         usesRegulation: Bool = false,
         regulationMinutes: Int? = nil,
         includesExtras: Bool = false,
         onOpenGame: ((String) -> Void)? = nil,
         onOpenDefense: (() -> Void)? = nil) {
        self.form = form
        self.density = density
        self.leagueAverage = leagueAverage
        self.usesRegulation = usesRegulation
        self.regulationMinutes = regulationMinutes
        self.includesExtras = includesExtras
        self.onOpenGame = onOpenGame
        self.onOpenDefense = onOpenDefense
    }

    private var hasGames: Bool {
        (form.games ?? 0) > 0 || form.latestGame != nil
    }

    private var badgeSize: TeamBadge.Size {
        density == .full ? TeamBadge.Size.large : TeamBadge.Size.medium
    }

    private var recordLine: String {
        var parts: [String] = [LeagueFormatting.record(form.record)]
        if let games = form.games {
            parts.append(LeagueFormatting.integer(games) + (games == 1 ? " game" : " games"))
        }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            header
            if hasGames {
                LatestScoreCard(game: form.latestGame, density: density)
                ScoringBlock(team: form,
                             leagueAverage: leagueAverage,
                             usesRegulation: usesRegulation,
                             regulationMinutes: regulationMinutes,
                             density: density)
                detail
            } else {
                Text("No games yet this season")
                    .hardwoodText(.caption)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var header: some View {
        HStack(spacing: Spacing.sm) {
            TeamBadge(abbreviation: form.team?.displayAbbr ?? Formatting.emDash,
                      name: form.team?.name,
                      size: badgeSize)
            VStack(alignment: .leading, spacing: 1) {
                Text(form.team?.displayName ?? Formatting.emDash)
                    .hardwoodText(.widgetTitle)
                    .lineLimit(1)
                Text(recordLine)
                    .hardwoodText(.caption)
            }
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder private var detail: some View {
        if density == .full {
            VStack(alignment: .leading, spacing: Spacing.md) {
                formSection
                contextSection
            }
        } else {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                FormDots(games: form.form)
                if includesExtras {
                    extras
                }
            }
        }
    }

    // MARK: Full

    private var formSection: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            Text("Recent games")
                .hardwoodText(.statLabel)
            FormChart(games: form.form, leagueAverage: usesRegulation ? nil : leagueAverage)
            FormList(games: form.form, onOpenGame: onOpenGame)
            WindowAverageLine(window: form.lastN)
            WindowAverageLine(window: form.last10)
        }
    }

    private var contextSection: some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            VenueSplitsGrid(splits: form.venueSplits)
            VStack(alignment: .leading, spacing: Spacing.xs) {
                AdjustedSentence(adjusted: form.adjustedPointsAgainst, kind: .allows)
                AdjustedSentence(adjusted: form.adjustedPointsFor, kind: .scores)
            }
            AvailabilitySummaryBlock(summary: form.availability, teamAbbr: form.team?.displayAbbr)
            defenceSection
        }
    }

    private var defenceSection: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("Defence by position")
                .hardwoodText(.statLabel)
            defenceSummaryContent
            if let openDefense = onOpenDefense {
                Button("Open Defence") { openDefense() }
            }
        }
    }

    @ViewBuilder private var defenceSummaryContent: some View {
        if let summary = form.defenseSummary {
            WithheldBanner(withheld: summary.withheld)
            DefenseBucketBars(buckets: summary.buckets,
                              withheld: summary.withheld,
                              provisional: false,
                              density: .compact)
        } else {
            Text(Formatting.emDash)
                .hardwoodText(.caption)
        }
    }

    // MARK: Compact extras

    private var extras: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            WindowAverageLine(window: form.lastN)
            WindowAverageLine(window: form.last10)
            VenueSplitsGrid(splits: form.venueSplits)
            defenceSummaryContent
            AvailabilityCountsRow(summary: form.availability)
        }
    }
}

#if DEBUG
#Preview("Matchup column") {
    ScrollView {
        if let form = LeagueMatchup.preview.teams?.first {
            VStack(alignment: .leading, spacing: Spacing.xl) {
                MatchupSideColumn(team: form,
                                  density: .full,
                                  leagueAverage: LeagueMatchup.preview.leagueAverage?.pointsPerGame)
                MatchupSideColumn(team: form,
                                  density: .compact,
                                  leagueAverage: LeagueMatchup.preview.leagueAverage?.pointsPerGame,
                                  includesExtras: true)
            }
            .padding(Spacing.lg)
        }
    }
    .hardwoodBackground()
}
#endif
