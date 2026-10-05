import SwiftUI

// One player's availability, drawn the same way in an injury table's inspector, in the matchup's
// key-absence list and in a dashboard tile; and the four counts that summarise a team's report.
//
// EVERYTHING SHOWN IS SOMETHING THE SERVER SAID
// A row never invents a status. A missing status is `No report` (never `Available`); the model's
// own status is shown only when it differs from the reported one ("In model: Out"); an entry that no
// longer drives the projection is dimmed, still listed, and labelled Superseded with the server's
// reason in the tooltip; an old entry is labelled Stale. Every row carries its source: the kind,
// the label as a link, and how long ago the source published it (`LeagueSourceLine`).
//
// TWO PAYLOAD SHAPES, ONE ROW
// A report entry (`LeagueAvailabilityEntry`) has every field; a key absence on a matchup or a
// projection (`LeagueAbsence`) has the status, the chance, the cost of the absence and the source.
// Both are mapped into one small display model, so the row is written once.

// MARK: - Display model

/// What a row draws, with every optional already resolved to something displayable.
struct AvailabilityRowModel {
    var playerName: String = Formatting.emDash
    var teamAbbr: String?
    var position: String?
    var status: String?
    var statusLabel: String?
    var modelStatus: String?
    var chanceOfPlaying: Double?
    var reasonCategory: String?
    var reasonText: String?
    var expectedReturn: String?
    var gameText: String?
    var isOverride: Bool = false
    var inForce: Bool = true
    var outOfForceReason: String?
    var isStale: Bool = false
    var ageMinutes: Double?
    var source: LeagueSource?
    var expectedPointsLost: Double?
    var expectedMinutesLost: Double?

    /// A report entry. A missing `inForce` means the entry is in force.
    init(entry: LeagueAvailabilityEntry, teamAbbr: String?) {
        self.playerName = entry.playerName ?? entry.player?.name ?? Formatting.emDash
        self.teamAbbr = teamAbbr
        self.position = entry.player?.position
        self.status = entry.status
        self.statusLabel = entry.statusLabel
        self.modelStatus = entry.modelStatus
        self.chanceOfPlaying = entry.chanceOfPlaying
        self.reasonCategory = entry.reasonCategory
        self.reasonText = entry.reasonText
        self.expectedReturn = AvailabilityRowModel.returnText(text: entry.expectedReturnText,
                                                              structured: entry.expectedReturn)
        self.gameText = AvailabilityRowModel.gameLine(entry.game)
        self.isOverride = entry.isOverride ?? false
        self.inForce = entry.inForce ?? true
        self.outOfForceReason = entry.outOfForceReason
        self.isStale = entry.isStale ?? false
        self.ageMinutes = entry.ageMinutes
        self.source = entry.source
    }

    /// A key absence from a matchup or a projection.
    init(absence: LeagueAbsence, teamAbbr: String?) {
        self.playerName = absence.player?.name ?? Formatting.emDash
        self.teamAbbr = teamAbbr
        self.position = absence.player?.position
        self.status = absence.status
        self.chanceOfPlaying = absence.chanceOfPlaying
        self.inForce = absence.inForce ?? true
        self.isStale = absence.isStale ?? false
        self.source = absence.source
        self.expectedPointsLost = absence.expectedPointsLost
        self.expectedMinutesLost = absence.expectedMinutesLost
    }

    /// The server's own words for when a player is expected back, else the structured range or day.
    static func returnText(text: String?, structured: LeagueExpectedReturn?) -> String? {
        if let text = text, !text.isEmpty { return text }
        guard let structured = structured else { return nil }
        if let from = structured.roundFrom {
            if let to = structured.roundTo, to != from {
                return "Rounds " + String(from) + "–" + String(to)
            }
            return "Round " + String(from)
        }
        if let day = structured.date, !day.isEmpty {
            return LeagueFormatting.leagueDate(day)
        }
        return nil
    }

    /// `ZZA v ZZP · Fri 2 Oct 19:30` for the game an entry applies to, or nil without one.
    static func gameLine(_ game: LeagueGameRef?) -> String? {
        guard let game = game else { return nil }
        var text = game.matchupTitle
        let time = LeagueFormatting.tipoff(game.tipoffUtc)
        if time != Formatting.emDash {
            text += " · " + time
        } else if let day = game.date, !day.isEmpty {
            text += " · " + LeagueFormatting.leagueDate(day)
        }
        return text
    }
}

// MARK: - The row

/// A player's availability. `compact` is one headline and, below it, the reason and the source.
/// `full` adds every detail the server sent and, for an entry a person typed, a Retract button.
struct AvailabilityEntryRow: View {
    private let model: AvailabilityRowModel
    private let density: LeagueDensity
    private let onRetract: (() -> Void)?

    /// A row for a report entry. `teamAbbr` is shown after the player's name when the list mixes
    /// teams. `onRetract` makes a Retract button appear in `full` density, for an entry a person
    /// typed; the caller asks for confirmation.
    init(entry: LeagueAvailabilityEntry,
         density: LeagueDensity = .compact,
         teamAbbr: String? = nil,
         onRetract: (() -> Void)? = nil) {
        self.model = AvailabilityRowModel(entry: entry, teamAbbr: teamAbbr)
        self.density = density
        self.onRetract = onRetract
    }

    /// A row for a key absence of a matchup or a projection.
    init(absence: LeagueAbsence,
         density: LeagueDensity = .compact,
         teamAbbr: String? = nil) {
        self.model = AvailabilityRowModel(absence: absence, teamAbbr: teamAbbr)
        self.density = density
        self.onRetract = nil
    }

    private var isSuperseded: Bool { !model.inForce }

    private var canRetract: Bool {
        guard density == .full, onRetract != nil else { return false }
        return model.isOverride || model.source?.kind == "manual"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            headline
            detailLines
            LeagueSourceLine(source: model.source, density: density, isStale: model.isStale)
            fullDetails
            retractButton
        }
        .opacity(isSuperseded ? 0.6 : 1.0)
        .accessibilityElement(children: .contain)
    }

    // MARK: Headline

    /// The team code and the listed position after the player's name: `ZZA · G`.
    private var subtitle: String {
        var parts: [String] = []
        if let abbr = model.teamAbbr, !abbr.isEmpty { parts.append(abbr) }
        if let position = model.position, !position.isEmpty { parts.append(position) }
        return parts.joined(separator: " · ")
    }

    private var headline: some View {
        HStack(spacing: Spacing.sm) {
            StatusChip(status: model.status, label: model.statusLabel)
            Text(model.playerName)
                .hardwoodText(.widgetTitle)
                .lineLimit(1)
            if !subtitle.isEmpty {
                Text(subtitle)
                    .hardwoodText(.caption)
            }
            Spacer(minLength: 0)
            if isSuperseded {
                LeagueChip(text: "Superseded", tint: Palette.neutral)
                    .help(LeagueFormatting.outOfForceWord(model.outOfForceReason))
            }
            if model.isOverride {
                LeagueChip(text: "Override", tint: Palette.selection)
                    .help("Entered by hand; it replaces the reported status.")
            }
        }
    }

    // MARK: Detail

    private var modelStatusText: String? {
        guard let inModel = model.modelStatus, !inModel.isEmpty, inModel != model.status else {
            return nil
        }
        return "In model: " + LeagueFormatting.statusWord(inModel)
    }

    @ViewBuilder private var detailLines: some View {
        if density == .full {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                if let inModel = modelStatusText {
                    Text(inModel).hardwoodText(.caption, color: Palette.textSecondary)
                }
                reasonLine
                if let comeBack = model.expectedReturn, !comeBack.isEmpty {
                    DetailLine(label: "Expected return", value: comeBack)
                }
                if let chance = model.chanceOfPlaying {
                    DetailLine(label: "Chance of playing", value: LeagueFormatting.percent(chance, places: 0))
                }
            }
        } else if let reason = model.reasonText, !reason.isEmpty {
            Text(reason)
                .hardwoodText(.caption)
                .lineLimit(1)
        }
    }

    @ViewBuilder private var reasonLine: some View {
        let reason = model.reasonText ?? ""
        let category = model.reasonCategory ?? ""
        if !reason.isEmpty || !category.isEmpty {
            HStack(spacing: Spacing.sm) {
                ReasonChip(category: model.reasonCategory)
                if !reason.isEmpty {
                    Text(reason)
                        .hardwoodText(.tableCell)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
        }
    }

    @ViewBuilder private var fullDetails: some View {
        if density == .full {
            let items = detailItems
            if !items.isEmpty {
                VStack(alignment: .leading, spacing: Spacing.xxs) {
                    ForEach(items) { item in
                        DetailLine(label: item.label, value: item.value)
                    }
                }
            }
        }
    }

    private var detailItems: [AvailabilityDetailItem] {
        var found: [AvailabilityDetailItem] = []
        func add(_ label: String, _ value: String?) {
            guard let value = value, !value.isEmpty, value != Formatting.emDash else { return }
            found.append(AvailabilityDetailItem(label: label, value: value))
        }
        add("Game", model.gameText)
        add("Source as of", model.source.map { LeagueFormatting.absoluteLocal($0.asOf) })
        add("Fetched", model.source.map { LeagueFormatting.absoluteLocal($0.fetchedAt) })
        add("Reported", LeagueFormatting.ageFromMinutes(model.ageMinutes))
        if isSuperseded {
            add("Superseded because", LeagueFormatting.outOfForceWord(model.outOfForceReason))
        }
        add("Points lost if out", model.expectedPointsLost.map { LeagueFormatting.number($0) })
        add("Minutes lost if out", model.expectedMinutesLost.map { LeagueFormatting.number($0) })
        return found
    }

    @ViewBuilder private var retractButton: some View {
        if canRetract {
            Button(role: .destructive) {
                if let action = onRetract { action() }
            } label: {
                Text("Retract…")
            }
        }
    }
}

/// A fact and its value, in two columns.
struct DetailLine: View {
    private let label: String
    private let value: String

    init(label: String, value: String) {
        self.label = label
        self.value = value
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
            Text(label)
                .hardwoodText(.caption)
                .frame(width: 118, alignment: .leading)
            Text(value)
                .hardwoodText(.tableCell)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }
}

/// One labelled fact of an availability row's detail block.
struct AvailabilityDetailItem: Identifiable {
    let label: String
    let value: String
    var id: String { label }
}

// MARK: - Counts

/// Out, Doubtful, Questionable and Probable, as the whole numbers the server counted. A count the
/// server did not send is an em dash; a real zero is shown as 0.
struct AvailabilityCountsRow: View {
    private let summary: LeagueAvailabilitySummary?

    init(summary: LeagueAvailabilitySummary?) {
        self.summary = summary
    }

    var body: some View {
        HStack(spacing: Spacing.md) {
            CountItem(title: "Out", count: summary?.out, status: "out")
            CountItem(title: "Doubtful", count: summary?.doubtful, status: "doubtful")
            CountItem(title: "Questionable", count: summary?.questionable, status: "questionable")
            CountItem(title: "Probable", count: summary?.probable, status: "probable")
            Spacer(minLength: 0)
        }
    }
}

private struct CountItem: View {
    let title: String
    let count: Int?
    let status: String

    private var tint: Color {
        if let count = count, count > 0 { return StatusChip.tint(for: status) }
        return Palette.textSecondary
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(LeagueFormatting.integer(count))
                .hardwoodText(.widgetTitle, color: tint)
            Text(title)
                .hardwoodText(.caption)
        }
        .accessibilityElement(children: .combine)
    }
}

/// A team's availability at a glance: the four counts, how current the report is, and its most
/// costly absences (three by default), each with its source.
struct AvailabilitySummaryBlock: View {
    private let summary: LeagueAvailabilitySummary?
    private let teamAbbr: String?
    private let absenceLimit: Int

    init(summary: LeagueAvailabilitySummary?, teamAbbr: String? = nil, absenceLimit: Int = 3) {
        self.summary = summary
        self.teamAbbr = teamAbbr
        self.absenceLimit = absenceLimit
    }

    private var absences: [LeagueAbsence] {
        Array((summary?.keyAbsences ?? []).prefix(max(absenceLimit, 0)))
    }

    private var stateNote: String? {
        guard let state = summary?.freshnessState, !state.isEmpty, state != "fresh" else { return nil }
        return LeagueFormatting.stateWord(state)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            Text("Availability")
                .hardwoodText(.statLabel)
            if summary == nil {
                Text(Formatting.emDash)
                    .hardwoodText(.caption)
            } else {
                AvailabilityCountsRow(summary: summary)
                reportLine
                ForEach(Array(absences.enumerated()), id: \.offset) { pair in
                    AvailabilityEntryRow(absence: pair.element, density: .compact, teamAbbr: teamAbbr)
                }
            }
        }
    }

    @ViewBuilder private var reportLine: some View {
        let asOf = summary?.asOf ?? ""
        if stateNote != nil || !asOf.isEmpty {
            HStack(spacing: Spacing.sm) {
                if let note = stateNote {
                    LeagueChip(text: note, tint: Palette.warning)
                }
                if !asOf.isEmpty {
                    Text("Report as of " + LeagueFormatting.age(asOf))
                        .hardwoodText(.caption)
                        .help(LeagueFormatting.absoluteLocal(asOf))
                }
                Spacer(minLength: 0)
            }
        }
    }
}

#if DEBUG
#Preview("Availability") {
    ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            if let report = LeagueAvailabilityReport.preview.teams?.first,
               let entry = report.entries?.first {
                AvailabilityEntryRow(entry: entry, density: .full, teamAbbr: report.team?.displayAbbr)
                AvailabilityEntryRow(entry: entry, density: .compact, teamAbbr: report.team?.displayAbbr)
            }
            if let form = LeagueMatchup.preview.teams?.first {
                AvailabilitySummaryBlock(summary: form.availability, teamAbbr: form.team?.displayAbbr)
            }
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
