import SwiftUI

// One game's projected score, drawn in full: who is playing, the projected points and 80% range for
// each side, the margin and the winner the projection implies, what it assumed about who plays, the
// model behind it, and the result next to it once the game is final.
//
// A PROJECTED SCORE AND NOTHING ELSE
// The projection is a score for each team and the margin between them, with an honest range. It is
// never compared with an outside number and carries no likelihood figure. When the game is already
// over, the real result and how far the projection was off sit beside it ("margin miss"), and
// whether the projected winner won.
//
// A RECONSTRUCTION IS LABELLED
// A projection made after a game began (or was played) is a reconstruction, not a prediction, and
// the card says so (`LeagueGameProjection.isComputedAfterTheFact`). The server says it outright
// with `model.computedAfterTipoff` when it can. When it cannot (the field is null) the card falls
// back to what the game's status and the model's kind imply. No timestamps are compared in the
// app.
//
// THE RANGE
// `range80` is an 80% range of the side's points. Where the server has not yet calibrated its width
// against past results, the card says `Assumed range` (the server's `intervalBasis`), and where
// there is no range at all it says `Range not yet calibrated`, never a made-up one.

// MARK: - After the fact

extension LeagueGameProjection {

    /// The sentence a table row or a card prints under a projection that was not made in advance.
    static let afterTheFactNote = "Computed after tip-off — a reconstruction, not a prediction"

    /// True when this projection was made after the game started or was played.
    ///
    /// The server's own tri-state decides first: `reconstructed` is always after the fact,
    /// `computedAfterTipoff == true` is, and `computedAfterTipoff == false` is not. Only when that
    /// field is null does the status fallback apply: a final or result-pending game whose
    /// projection is neither locked nor imported.
    var isComputedAfterTheFact: Bool {
        if model?.kind == "reconstructed" { return true }
        if let after = model?.computedAfterTipoff { return after }
        let status = game?.status ?? ""
        let kind = model?.kind ?? ""
        if status == "final" || status == "resultPending" {
            return kind != "locked" && kind != "imported"
        }
        return false
    }
}

/// The reconstruction note, drawn only when it applies.
struct ReconstructionNote: View {
    private let projection: LeagueGameProjection

    init(projection: LeagueGameProjection) {
        self.projection = projection
    }

    var body: some View {
        if projection.isComputedAfterTheFact {
            LeagueStrip(symbol: "clock.arrow.circlepath",
                        text: LeagueGameProjection.afterTheFactNote,
                        tint: Palette.warning)
        }
    }
}

// MARK: - One side

/// One team's side of a projection: its projected points and 80% range, then the pieces the model
/// built them from (full-strength points, the effect of who is missing, the attack and defence
/// indices), and the key absences with their sources.
struct SideProjectionPanel: View {
    private let side: LeagueSideProjection
    private let title: String
    private let intervalBasis: String?
    private let capPolicy: String?

    init(side: LeagueSideProjection,
         title: String,
         intervalBasis: String? = nil,
         capPolicy: String? = nil) {
        self.side = side
        self.title = title
        self.intervalBasis = intervalBasis
        self.capPolicy = capPolicy
    }

    private var rangeText: String {
        guard let low = side.range80?.low, let high = side.range80?.high else { return "" }
        return "80% range " + LeagueFormatting.number(low) + "–" + LeagueFormatting.number(high)
    }

    private var absences: [LeagueAbsence] {
        Array((side.keyAbsences ?? []).prefix(5))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            Text(title)
                .hardwoodText(.statLabel)
            LeagueTeamLabel(team: side.team, size: .medium, showsName: true)
            Text(LeagueFormatting.number(side.projectedPoints))
                .hardwoodText(.displayValue)
            rangeLine
            figures
            capNote
            absenceRows
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private var rangeLine: some View {
        HStack(spacing: Spacing.sm) {
            if rangeText.isEmpty {
                Text(Formatting.emDash)
                    .hardwoodText(.tableCell, color: Palette.textTertiary)
                Text("Range not yet calibrated")
                    .hardwoodText(.caption)
            } else {
                Text(rangeText)
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                if intervalBasis == "assumed" {
                    AssumedRangeBadge()
                }
            }
            Spacer(minLength: 0)
        }
    }

    private var figures: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            DetailLine(label: "Full strength", value: LeagueFormatting.number(side.fullStrengthPoints))
            DetailLine(label: "Availability effect", value: LeagueFormatting.signed(side.availabilityEffect))
            DetailLine(label: "Attack index", value: LeagueFormatting.number(side.attackIndex, places: 2))
            DetailLine(label: "After availability",
                       value: LeagueFormatting.number(side.attackIndexAfterAvailability, places: 2))
            DetailLine(label: "Defence index", value: LeagueFormatting.number(side.defenceIndex, places: 2))
            Text("Defence index: higher means more points allowed.")
                .hardwoodText(.caption)
        }
    }

    @ViewBuilder private var capNote: some View {
        let usesWorkbookCap = capPolicy == "workbook"
        if side.capBinding == true || (usesWorkbookCap && side.unassignedPoints != nil) {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                if side.capBinding == true {
                    Text("Cap binding: the cap on teammates' scoring boost limits how much of the missing scoring they take on.")
                        .hardwoodText(.caption, color: Palette.warning)
                        .fixedSize(horizontal: false, vertical: true)
                    if !usesWorkbookCap, let replacement = side.replacementPoints, replacement > 0 {
                        DetailLine(label: "Replacement", value: LeagueFormatting.number(replacement))
                    }
                }
                if usesWorkbookCap, let unassigned = side.unassignedPoints {
                    DetailLine(label: "Unassigned points", value: LeagueFormatting.number(unassigned))
                }
            }
        }
    }

    @ViewBuilder private var absenceRows: some View {
        if !absences.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text("Key absences")
                    .hardwoodText(.statLabel)
                ForEach(Array(absences.enumerated()), id: \.offset) { pair in
                    AvailabilityEntryRow(absence: pair.element,
                                         density: .compact,
                                         teamAbbr: side.team?.displayAbbr)
                }
            }
        }
    }
}

// MARK: - The card

/// A game's projection in full. `sideBySide` puts the two sides next to each other (a wide
/// window); the default stacks them (an inspector). `onOpenMatchup` and `onOpenBoxScore` make the
/// two buttons appear; the box score button is only ever shown for a final game.
struct GameProjectionCard: View {
    private let projection: LeagueGameProjection
    private let league: LeagueKey
    private let sideBySide: Bool
    private let onOpenMatchup: (() -> Void)?
    private let onOpenBoxScore: (() -> Void)?

    init(projection: LeagueGameProjection,
         league: LeagueKey,
         sideBySide: Bool = false,
         onOpenMatchup: (() -> Void)? = nil,
         onOpenBoxScore: (() -> Void)? = nil) {
        self.projection = projection
        self.league = league
        self.sideBySide = sideBySide
        self.onOpenMatchup = onOpenMatchup
        self.onOpenBoxScore = onOpenBoxScore
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            header
            ReconstructionNote(projection: projection)
            outlook
            panels
            assumptions
            resultBlock
            modelBlock
            actions
            notes
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Header

    private var headerTitle: String {
        projection.game?.matchupText(for: league) ?? Formatting.emDash
    }

    private var berlinHelp: String {
        guard league == .euroleague else { return "" }
        let time = LeagueFormatting.berlinTime(projection.game?.tipoffUtc)
        return time == Formatting.emDash ? "" : "Berlin time: " + time
    }

    private var homeAdvantageText: String {
        guard projection.game?.isNeutral != true, let points = projection.homeAdvantagePoints else { return "" }
        return "Home advantage " + LeagueFormatting.number(points) + " points"
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text(headerTitle)
                .hardwoodText(.sectionTitle)
            HStack(spacing: Spacing.sm) {
                Text(LeagueFormatting.gameStatusText(projection.game))
                    .hardwoodText(.tableCell)
                    .help(berlinHelp)
                if projection.game?.status == "resultPending" {
                    ResultPendingChip()
                }
                if let venue = projection.game?.venue, !venue.isEmpty {
                    Text(venue)
                        .hardwoodText(.caption)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            venueNote
        }
    }

    @ViewBuilder private var venueNote: some View {
        if projection.game?.isNeutral == true {
            Text("Neutral venue: no home advantage")
                .hardwoodText(.caption)
        } else if !homeAdvantageText.isEmpty {
            HStack(spacing: Spacing.sm) {
                Text(homeAdvantageText)
                    .hardwoodText(.caption)
                if projection.venueAssumed == true {
                    EstimatedBadge(text: "Assumed")
                }
                Spacer(minLength: 0)
            }
        }
    }

    // MARK: Outlook

    private var winnerText: String {
        if projection.isTossUp == true { return "Toss-up" }
        return projection.summary ?? Formatting.emDash
    }

    private var marginRangeText: String {
        guard let low = projection.marginRange80?.low, let high = projection.marginRange80?.high else {
            return ""
        }
        return LeagueFormatting.signed(low) + " to " + LeagueFormatting.signed(high)
    }

    private var outlook: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            Text(winnerText)
                .hardwoodText(.widgetTitle)
            DetailLine(label: "Margin (home minus away)", value: LeagueFormatting.signed(projection.margin))
            if !marginRangeText.isEmpty {
                DetailLine(label: "Margin 80% range", value: marginRangeText)
            }
            DetailLine(label: "Combined points", value: LeagueFormatting.number(projection.combinedPoints))
            DetailLine(label: "Availability effect",
                       value: LeagueFormatting.signed(projection.combinedAvailabilityEffect))
        }
    }

    // MARK: Sides

    @ViewBuilder private var homePanel: some View {
        if let home = projection.home {
            SideProjectionPanel(side: home,
                                title: "Home",
                                intervalBasis: projection.intervalBasis,
                                capPolicy: projection.model?.capPolicy)
        }
    }

    @ViewBuilder private var awayPanel: some View {
        if let away = projection.away {
            SideProjectionPanel(side: away,
                                title: "Away",
                                intervalBasis: projection.intervalBasis,
                                capPolicy: projection.model?.capPolicy)
        }
    }

    @ViewBuilder private var panels: some View {
        if sideBySide {
            HStack(alignment: .top, spacing: Spacing.lg) {
                homePanel
                awayPanel
            }
        } else {
            VStack(alignment: .leading, spacing: Spacing.lg) {
                homePanel
                awayPanel
            }
        }
    }

    // MARK: Assumptions

    private var assumptionText: String {
        guard let assumed = projection.assumptions?.assumedAvailable else { return "" }
        var counts: [String] = []
        if let home = assumed.home { counts.append(String(home) + " home") }
        if let away = assumed.away { counts.append(String(away) + " away") }
        if counts.isEmpty { return "" }
        var sentence = counts.joined(separator: " and ") + " players assumed available"
        if let basis = assumed.basis, !basis.isEmpty {
            sentence += " (" + LeagueFormatting.assumedBasisWord(basis) + ")"
        } else if let homeBasis = assumed.homeBasis, let awayBasis = assumed.awayBasis {
            sentence += " (home: " + LeagueFormatting.assumedBasisWord(homeBasis)
            sentence += "; away: " + LeagueFormatting.assumedBasisWord(awayBasis) + ")"
        }
        return sentence
    }

    private var staleIgnoredText: String {
        guard let count = projection.assumptions?.staleEntriesIgnored, count > 0 else { return "" }
        if count == 1 { return "1 out-of-date entry ignored" }
        return String(count) + " out-of-date entries ignored"
    }

    @ViewBuilder private var assumptions: some View {
        if !assumptionText.isEmpty || !staleIgnoredText.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                if !assumptionText.isEmpty {
                    Text(assumptionText)
                        .hardwoodText(.caption, color: Palette.textSecondary)
                }
                if !staleIgnoredText.isEmpty {
                    Text(staleIgnoredText)
                        .hardwoodText(.caption, color: Palette.textSecondary)
                }
            }
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: Result

    @ViewBuilder private var resultBlock: some View {
        if let result = projection.result {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                Text("Result")
                    .hardwoodText(.statLabel)
                DetailLine(label: "Final score", value: LeagueFormatting.score(result.homePts, result.awayPts))
                DetailLine(label: "Margin miss", value: LeagueFormatting.number(result.marginMiss))
                winnerCalledRow(result)
            }
        }
    }

    private func winnerCalledRow(_ result: LeagueProjectionResult) -> some View {
        let word: String
        if projection.isTossUp == true {
            word = "Toss-up, no winner called"
        } else if result.winnerCalled == true {
            word = "Yes"
        } else if result.winnerCalled == false {
            word = "No"
        } else {
            word = Formatting.emDash
        }
        return DetailLine(label: "Winner called", value: word)
    }

    // MARK: Model

    private func constantText(_ value: JSONValue?) -> String {
        if let number = value?.doubleValue {
            return LeagueFormatting.number(number, places: number == number.rounded() ? 0 : 3)
        }
        if let text = value?.stringValue { return text }
        return Formatting.emDash
    }

    @ViewBuilder private var modelBlock: some View {
        if let model = projection.model {
            DisclosureGroup("Model") {
                VStack(alignment: .leading, spacing: Spacing.xs) {
                    modelFacts(model)
                    constantRows(model.constants)
                }
                .padding(.top, Spacing.xs)
            }
        }
    }

    private func modelFacts(_ model: LeagueProjectionModel) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            DetailLine(label: "Key", value: (model.key ?? Formatting.emDash) + " " + (model.version ?? ""))
            DetailLine(label: "Run", value: LeagueFormatting.modelKindWord(model.kind))
            DetailLine(label: "Computed", value: LeagueFormatting.absoluteLocal(model.computedAt))
            DetailLine(label: "Inputs up to", value: LeagueFormatting.absoluteLocal(model.inputsCutoff))
            DetailLine(label: "Cap policy", value: model.capPolicy ?? Formatting.emDash)
        }
    }

    @ViewBuilder private func constantRows(_ constants: [LeagueModelConstant]?) -> some View {
        let items = constants ?? []
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text("Constants")
                    .hardwoodText(.tableHeader)
                ForEach(Array(items.enumerated()), id: \.offset) { pair in
                    HStack(spacing: Spacing.sm) {
                        Text(pair.element.key ?? Formatting.emDash)
                            .hardwoodText(.tableCell)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Text(constantText(pair.element.value))
                            .hardwoodText(.tableCell)
                        if pair.element.isDefault == true {
                            LeagueChip(text: "Default", tint: Palette.neutral)
                        }
                        Text(pair.element.provenance ?? "")
                            .hardwoodText(.caption)
                    }
                }
            }
        }
    }

    // MARK: Actions and notes

    private var showsBoxScoreButton: Bool {
        onOpenBoxScore != nil && projection.game?.status == "final"
    }

    @ViewBuilder private var actions: some View {
        if onOpenMatchup != nil || showsBoxScoreButton {
            HStack(spacing: Spacing.sm) {
                if let openMatchup = onOpenMatchup {
                    Button("Open Matchup") { openMatchup() }
                }
                if showsBoxScoreButton, let openBox = onOpenBoxScore {
                    Button("Open Box Score") { openBox() }
                }
                Spacer(minLength: 0)
            }
        }
    }

    @ViewBuilder private var notes: some View {
        let items = (projection.notes ?? []).filter { !$0.isEmpty }
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                ForEach(Array(items.enumerated()), id: \.offset) { pair in
                    Text(pair.element)
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .textSelection(.enabled)
        }
    }
}

#if DEBUG
#Preview("Game projection") {
    ScrollView {
        if let game = LeagueSlateProjections.preview.games?.first {
            GameProjectionCard(projection: game,
                               league: .euroleague,
                               onOpenMatchup: {},
                               onOpenBoxScore: {})
                .padding(Spacing.lg)
        }
    }
    .hardwoodBackground()
}
#endif
