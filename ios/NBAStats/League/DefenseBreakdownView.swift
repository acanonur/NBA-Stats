import SwiftUI

// One team's defence by opponent position: "Allows 83.2 per game, league 85.1, over 31 games" and
// then where those points went, position by position.
//
// THE USER'S SECOND QUESTION
// "How good is this defence against each kind of player?" The answer is the server's table of points
// allowed per position, set against the league's, and this view shows it with every caution the
// payload carries: the caveat (points by players LISTED at a position, not who guarded whom), the
// withheld message when there are too few games, the Provisional badge, the coverage of listed
// positions, and the method's limitations.
//
// NO RANKS, NO VERDICTS OF ITS OWN
// There is no rank, no ordering by a score and no "best defence" label. The band (better, typical,
// worse) is the server's and only exists when the table is neither provisional nor withheld. The
// index (1.00 is the league's typical) is shown only in the collapsed Method details and only when
// the band logic says the table can be read; `rawIndex` appears there as "unadjusted" and is never
// used to order anything.
//
// THE CHECKSUM
// The positions' points add up to the team's points allowed per game, an identity the payload
// states. The view prints both numbers side by side ("Positions add up to 83.2; points allowed per
// game is 83.2") rather than asserting they are equal, and says plainly when games were left out
// because their position split did not add up to the final score.

// MARK: - Pieces

extension LeagueDefenseDocument {

    /// The league table's shared fields with one team's row folded in, so one view can draw a team
    /// from either route. The table carries no coverage or reconciliation, so those sections are
    /// absent from a document made this way.
    func focused(on row: LeagueDefenseTeamRow) -> LeagueDefenseDocument {
        var copy = self
        copy.team = row.team
        copy.teams = nil
        copy.buckets = row.buckets
        copy.pointsAllowedPerGame = row.pointsAllowedPerGame
        copy.provisional = row.provisional
        copy.withheld = row.withheld
        var focusedWindow = window ?? LeagueDefenseWindow()
        focusedWindow.games = row.games
        copy.window = focusedWindow
        return copy
    }
}

/// The sentences of the headline, built from the payload's own numbers.
enum DefenseHeadlineText {

    /// `31 games (season)`, `Last 10 games`, or `Last 10 requested, 8 reconciled`.
    static func windowText(_ window: LeagueDefenseWindow?) -> String {
        guard let window = window else { return "" }
        let played = window.games
        let noun = played == 1 ? " game" : " games"
        switch window.kind ?? "" {
        case "season":
            if let played = played { return String(played) + noun + " (season)" }
            return "Whole season"
        case "lastGames":
            guard let asked = window.requested else {
                if let played = played { return "Last " + String(played) + noun }
                return "Recent games"
            }
            if let played = played, played != asked {
                return "Last " + String(asked) + " requested, " + String(played) + " reconciled"
            }
            return "Last " + String(asked) + " games"
        default:
            return window.kind ?? ""
        }
    }

    /// The whole headline. A tile gets the short form: `Allows 83.2 · league 85.1 · 31 g`.
    static func text(_ document: LeagueDefenseDocument, density: LeagueDensity) -> String {
        let allowed = LeagueFormatting.number(document.pointsAllowedPerGame)
        let league = LeagueFormatting.number(document.leaguePointsAllowedPerGame)
        if density == .full {
            var sentence = "Allows " + allowed + " per game · league " + league
            let span = DefenseHeadlineText.windowText(document.window)
            if !span.isEmpty { sentence += " · " + span }
            return sentence
        }
        var short = "Allows " + allowed + " · league " + league
        if let played = document.window?.games {
            short += " · " + String(played) + " g"
        }
        return short
    }
}

/// The headline with the Provisional badge beside it when the server says the team is provisional.
struct DefenseHeadline: View {
    private let document: LeagueDefenseDocument
    private let density: LeagueDensity

    init(document: LeagueDefenseDocument, density: LeagueDensity = .full) {
        self.document = document
        self.density = density
    }

    private var style: HardwoodTextStyle {
        density == .full ? HardwoodTextStyle.sectionTitle : HardwoodTextStyle.widgetTitle
    }

    private var rowLimit: Int {
        density == .full ? 2 : 1
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
            Text(DefenseHeadlineText.text(document, density: density))
                .hardwoodText(style)
                .lineLimit(rowLimit)
            if document.provisional == true {
                ProvisionalBadge()
            }
            Spacer(minLength: 0)
        }
    }
}

/// Each position's share of the points a team allows, beside the league's share. Both are
/// fractions the server sent; a position with neither is left out.
struct DefenseShareRows: View {
    private let buckets: [LeagueDefenseBucket]

    init(buckets: [LeagueDefenseBucket]?) {
        self.buckets = (buckets ?? []).filter { $0.share != nil || $0.leagueShare != nil }
    }

    var body: some View {
        if !buckets.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                HStack(spacing: Spacing.sm) {
                    Text("Share of points allowed")
                        .hardwoodText(.tableHeader)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Text("Team")
                        .hardwoodText(.tableHeader)
                        .frame(width: 56, alignment: .trailing)
                    Text("League")
                        .hardwoodText(.tableHeader)
                        .frame(width: 56, alignment: .trailing)
                }
                ForEach(Array(buckets.enumerated()), id: \.offset) { pair in
                    HStack(spacing: Spacing.sm) {
                        Text(DefenseDisplay.title(for: pair.element, density: .full))
                            .hardwoodText(.tableCell)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Text(LeagueFormatting.percent(pair.element.share))
                            .hardwoodText(.tableCell)
                            .frame(width: 56, alignment: .trailing)
                        Text(LeagueFormatting.percent(pair.element.leagueShare))
                            .hardwoodText(.tableCell, color: Palette.textSecondary)
                            .frame(width: 56, alignment: .trailing)
                    }
                }
            }
        }
    }
}

/// The checksum line and, when games were left out, why.
struct DefenseReconciliationLine: View {
    private let reconciliation: LeagueDefenseReconciliation?

    init(reconciliation: LeagueDefenseReconciliation?) {
        self.reconciliation = reconciliation
    }

    private var sumText: String {
        guard let sum = reconciliation?.sumOfBuckets else { return "" }
        var sentence = "Positions add up to " + LeagueFormatting.number(sum)
        sentence += "; points allowed per game is "
        sentence += LeagueFormatting.number(reconciliation?.pointsAllowedPerGame) + "."
        return sentence
    }

    private var excludedText: String {
        guard let count = reconciliation?.unreconciledGames, count > 0 else { return "" }
        if count == 1 {
            return "1 game excluded: its position split did not add up to the final score."
        }
        return String(count) + " games excluded: their position split did not add up to the final score."
    }

    var body: some View {
        if !sumText.isEmpty || !excludedText.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                if !sumText.isEmpty {
                    Text(sumText)
                        .hardwoodText(.caption, color: Palette.textSecondary)
                }
                if !excludedText.isEmpty {
                    Text(excludedText)
                        .hardwoodText(.caption, color: Palette.warning)
                }
            }
            .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// How much of the points allowed could be placed at a position: roster-listed, workbook-listed, or
/// unassigned, as one stacked bar and its legend. The three are fractions the server sent.
struct CoverageBar: View {
    private let coverage: LeagueDefenseCoverage?

    init(coverage: LeagueDefenseCoverage?) {
        self.coverage = coverage
    }

    private func segmentWidth(_ fraction: Double?, total: CGFloat) -> CGFloat {
        guard let fraction = fraction, fraction.isFinite else { return 0 }
        return CGFloat(min(max(fraction, 0), 1)) * total
    }

    private var legend: String {
        var sentence = "Roster listing " + LeagueFormatting.percent(coverage?.listed, places: 0)
        sentence += " · Workbook listing " + LeagueFormatting.percent(coverage?.workbookListing, places: 0)
        sentence += " · Unassigned " + LeagueFormatting.percent(coverage?.unknown, places: 0)
        return sentence
    }

    var body: some View {
        if coverage != nil {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text("Where the points were placed")
                    .hardwoodText(.tableHeader)
                GeometryReader { proxy in
                    HStack(spacing: 0) {
                        Rectangle()
                            .fill(Palette.chartColor(at: 0))
                            .frame(width: segmentWidth(coverage?.listed, total: proxy.size.width))
                        Rectangle()
                            .fill(Palette.chartColor(at: 2))
                            .frame(width: segmentWidth(coverage?.workbookListing, total: proxy.size.width))
                        Rectangle()
                            .fill(Palette.neutral)
                            .frame(width: segmentWidth(coverage?.unknown, total: proxy.size.width))
                        Spacer(minLength: 0)
                    }
                    .background(Palette.track)
                    .clipShape(Capsule(style: .continuous))
                }
                .frame(height: 8)
                Text(legend)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)
        }
    }
}

// MARK: - Method details

/// The collapsed method block: the index per position (when it may be read), the method's own
/// thresholds and rules, and its limitations, all as the server wrote them.
struct DefenseMethodDetails: View {
    private let document: LeagueDefenseDocument

    init(document: LeagueDefenseDocument) {
        self.document = document
    }

    private var isGreyed: Bool {
        DefenseDisplay.isGreyed(withheld: document.withheld, provisional: document.provisional)
    }

    private var reliabilityText: String {
        guard let reliability = document.method?.leagueReliability else { return "" }
        var parts: [String] = []
        for key in ["G", "F", "C", "PG", "SG", "SF", "PF"] {
            guard let entry = reliability[key] else { continue }
            parts.append(key + " " + LeagueFormatting.number(entry.doubleValue, places: 2))
        }
        return parts.joined(separator: " · ")
    }

    private var thresholdText: String {
        guard let method = document.method else { return "" }
        var parts: [String] = []
        if let minimum = method.minimumGames { parts.append("withheld below " + String(minimum) + " games") }
        if let provisional = method.provisionalBelowGames {
            parts.append("provisional below " + String(provisional) + " games")
        }
        if let ceiling = method.coverageCeiling {
            parts.append("withheld when more than " + LeagueFormatting.percent(ceiling, places: 0) + " is unassigned")
        }
        return parts.joined(separator: "; ")
    }

    var body: some View {
        DisclosureGroup("Method details") {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                indexRows
                facts
                limitations
            }
            .padding(.top, Spacing.xs)
        }
    }

    @ViewBuilder private var indexRows: some View {
        let buckets = document.buckets ?? []
        if isGreyed || buckets.isEmpty {
            Text("Position indices are shown once there are enough games to read them.")
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text("An index of 1.00 is the league's typical; above 1.00 allows more points than typical.")
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(Array(buckets.enumerated()), id: \.offset) { pair in
                    indexRow(pair.element)
                }
            }
        }
    }

    private func indexRow(_ bucket: LeagueDefenseBucket) -> some View {
        HStack(spacing: Spacing.sm) {
            Text(DefenseDisplay.title(for: bucket, density: .full))
                .hardwoodText(.tableCell)
                .frame(width: 96, alignment: .leading)
            Text("Index " + LeagueFormatting.number(bucket.index, places: 2))
                .hardwoodText(.tableCell)
            Text("unadjusted " + LeagueFormatting.number(bucket.rawIndex, places: 2))
                .hardwoodText(.caption)
            Text("± " + LeagueFormatting.number(bucket.standardError, places: 2))
                .hardwoodText(.caption)
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder private var facts: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            if !thresholdText.isEmpty {
                DetailLine(label: "Thresholds", value: thresholdText)
            }
            if !reliabilityText.isEmpty {
                DetailLine(label: "League reliability", value: reliabilityText)
            }
            if let source = document.method?.positionSource, !source.isEmpty {
                DetailLine(label: "Position source", value: source)
            }
            if let taxonomy = document.method?.taxonomy, !taxonomy.isEmpty {
                DetailLine(label: "Positions", value: taxonomy)
            }
            if let rule = document.method?.bandRule, !rule.isEmpty {
                DetailLine(label: "Band rule", value: rule)
            }
            if let shrinkage = document.method?.shrinkage, !shrinkage.isEmpty {
                DetailLine(label: "Shrinkage", value: shrinkage)
            }
        }
    }

    @ViewBuilder private var limitations: some View {
        let items = document.method?.limitations ?? []
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text("Limitations")
                    .hardwoodText(.tableHeader)
                ForEach(Array(items.enumerated()), id: \.offset) { pair in
                    Text("• " + pair.element)
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

// MARK: - The breakdown

/// One team's defence by position: headline, withheld and estimated notes, the position rows, the
/// shares, the checksum, the coverage, the collapsed method details and the caveat.
///
/// `density: .compact` is a tile's version: the short headline, the withheld message, one row per
/// position and the caveat on one truncated line. `onShowLastSeason` makes a Show last season
/// button appear when the team is withheld for too few games; the screen decides what it does.
struct DefenseBreakdownView: View {
    private let document: LeagueDefenseDocument
    private let density: LeagueDensity
    private let onShowLastSeason: (() -> Void)?
    private let showsCaveat: Bool

    init(document: LeagueDefenseDocument,
         density: LeagueDensity = .full,
         onShowLastSeason: (() -> Void)? = nil,
         showsCaveat: Bool = true) {
        self.document = document
        self.density = density
        self.onShowLastSeason = onShowLastSeason
        self.showsCaveat = showsCaveat
    }

    private var isEstimated: Bool {
        document.availability == "estimated" || document.scheme == "workbook5"
    }

    private var signalText: String {
        if let message = document.methodMessage, !message.isEmpty { return message }
        if document.method?.leagueSignal == "none detected" {
            return "No team's points allowed at this position differ from the league by more than chance this season."
        }
        return ""
    }

    var body: some View {
        if density == .full {
            fullBody
        } else {
            compactBody
        }
    }

    // MARK: Compact

    private var compactBody: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            DefenseHeadline(document: document, density: .compact)
            WithheldBanner(withheld: document.withheld)
            DefenseBucketBars(buckets: document.buckets,
                              withheld: document.withheld,
                              provisional: document.provisional ?? false,
                              density: .compact,
                              showsBadge: false)
            compactCaveat
        }
    }

    @ViewBuilder private var compactCaveat: some View {
        if let caveat = document.caveat, !caveat.isEmpty {
            Text(caveat)
                .hardwoodText(.caption)
                .lineLimit(1)
                .help(caveat)
        }
    }

    // MARK: Full

    private var fullBody: some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            notesSection
            positionsSection
            VStack(alignment: .leading, spacing: Spacing.md) {
                DefenseMethodDetails(document: document)
                if showsCaveat {
                    CaveatFooter(text: document.caveat)
                }
            }
        }
    }

    private var notesSection: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            DefenseHeadline(document: document, density: .full)
            WithheldBanner(withheld: document.withheld)
            lastSeasonButton
            estimatedNote
            signalNote
        }
    }

    @ViewBuilder private var lastSeasonButton: some View {
        if let action = onShowLastSeason, document.withheld?.reason == "minimumGames" {
            Button("Show last season") { action() }
        }
    }

    @ViewBuilder private var estimatedNote: some View {
        if isEstimated {
            HStack(spacing: Spacing.sm) {
                EstimatedBadge()
                if document.scheme == "workbook5" {
                    Text("Five-position labels come from your workbook's listings.")
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
        }
    }

    @ViewBuilder private var signalNote: some View {
        if !signalText.isEmpty {
            Text(signalText)
                .hardwoodText(.caption, color: Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var positionsSection: some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            DefenseBucketBars(buckets: document.buckets,
                              withheld: document.withheld,
                              provisional: document.provisional ?? false,
                              density: .full,
                              showsBadge: false)
            DefenseShareRows(buckets: document.buckets)
            DefenseReconciliationLine(reconciliation: document.reconciliation)
            CoverageBar(coverage: document.coverage)
        }
    }
}

#if DEBUG
#Preview("Defence breakdown") {
    ScrollView {
        VStack(alignment: .leading, spacing: Spacing.xl) {
            DefenseBreakdownView(document: LeagueDefenseDocument.preview, density: .full)
            DefenseBreakdownView(document: LeagueDefenseDocument.preview, density: .compact)
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
