#if os(macOS)
import SwiftUI

// The injury and availability table of the Injury Report & News screen, the filter that narrows it,
// and the small summaries that sit under it.
//
// EVERY ROW CARRIES ITS SOURCE
// A status is only useful if the reader can see who said it and how long ago. So no row is drawn
// without a Source cell (the kind of source as a chip, its label as a link when the server sent a
// usable web address) and a Reported cell (how long ago the SOURCE published it, never when
// Hardwood read it). An entry that no longer drives the projection stays in the list, dimmed and
// marked Superseded (the server says why, and the inspector words it); an old one is marked Stale.
// A player nobody has reported on is "No report", never "Available".
//
// THE FILTER IS APPLIED TO WHAT WAS SERVED
// The status toggles, the superseded switch and the name field only decide which of the served
// entries to draw. They never add, merge or reinterpret one. The club filter is different: it is
// part of the request (`teamId` or `clubCode`), so the server answers for that club alone.
//
// WHY ROWS ARE BUILT FIRST
// As in the Round table, each entry becomes one `MacInjuryRow` of display strings and plain sort
// keys before the table is built, so the table body is eight one-line columns (MAC_DESIGN 6.5).

// MARK: - Statuses

/// One choice of the status filter: the key a status falls under and the word the menu shows.
struct MacInjuryStatusChoice: Identifiable, Hashable {
    let key: String
    let label: String

    var id: String { key }
}

enum MacInjuryStatuses {

    /// The key of an entry the server left without a status: nobody has reported on the player.
    static let noReportKey = "none"

    /// The five statuses, then "No report", in the order a reader scans them.
    static let choices: [MacInjuryStatusChoice] = [
        MacInjuryStatusChoice(key: "out", label: "Out"),
        MacInjuryStatusChoice(key: "doubtful", label: "Doubtful"),
        MacInjuryStatusChoice(key: "questionable", label: "Questionable"),
        MacInjuryStatusChoice(key: "probable", label: "Probable"),
        MacInjuryStatusChoice(key: "available", label: "Available"),
        MacInjuryStatusChoice(key: "none", label: "No report")
    ]

    static var allKeys: Set<String> {
        Set(choices.map { $0.key })
    }

    /// The filter key a served status falls under: the status itself when it is one of the five,
    /// `none` when there is no status, and `other` for a word this build does not know (which no
    /// toggle can hide, because it is not one the toggles describe).
    static func key(for status: String?) -> String {
        guard let status = status, !status.isEmpty else { return noReportKey }
        switch status {
        case "out", "doubtful", "questionable", "probable", "available":
            return status
        default:
            return "other"
        }
    }

    /// Out first, then doubtful, questionable, probable, available, no report, anything else.
    static func order(for status: String?) -> Int {
        switch key(for: status) {
        case "out":
            return 0
        case "doubtful":
            return 1
        case "questionable":
            return 2
        case "probable":
            return 3
        case "available":
            return 4
        case "none":
            return 5
        default:
            return 6
        }
    }
}

// MARK: - Filter

/// Which of the served entries to draw.
struct MacInjuryFilter {
    var statuses: Set<String>
    var includeSuperseded: Bool
    var name: String

    func allows(_ entry: LeagueAvailabilityEntry) -> Bool {
        if !includeSuperseded && entry.inForce == false {
            return false
        }
        let key = MacInjuryStatuses.key(for: entry.status)
        if key != "other" && !statuses.contains(key) {
            return false
        }
        let needle = name.trimmingCharacters(in: .whitespacesAndNewlines)
        if needle.isEmpty {
            return true
        }
        let player: String = entry.playerName ?? entry.player?.name ?? ""
        return player.localizedCaseInsensitiveContains(needle)
    }
}

// MARK: - Row

/// One availability entry, ready to draw and to sort.
struct MacInjuryRow: Identifiable, Hashable {
    let id: String
    let club: String
    let player: String
    let statusKey: String
    let statusLabel: String
    let statusSort: Int
    let inModel: String
    let problem: String
    let reasonCategory: String
    let reasonText: String
    let expectedReturn: String
    let source: String
    let sourceKind: String
    let sourceLabel: String
    let sourceURL: String
    let reported: String
    let reportedSort: Double
    let isSuperseded: Bool
    let isStale: Bool
    let isOverride: Bool

    static let copyHeader: [String] = [
        "Club", "Player", "Status", "In model", "Problem", "Expected return", "Source", "Reported"
    ]

    /// The status as a spreadsheet should see it: marked when it is superseded or stale.
    var statusCopy: String {
        if isSuperseded {
            return statusLabel + " (superseded)"
        }
        if isStale {
            return statusLabel + " (stale)"
        }
        return statusLabel
    }

    var copyCells: [String] {
        [club, player, statusCopy, inModel, problem, expectedReturn, source, reported]
    }
}

/// An entry and the team block it was served under.
struct MacInjuryEntryRef {
    let entry: LeagueAvailabilityEntry
    let team: LeagueTeamRef?
}

// MARK: - Building the rows

enum MacInjuryRows {

    /// Every served entry the filter allows, team by team in the order the server sent them.
    static func rows(from report: LeagueAvailabilityReport, filter: MacInjuryFilter) -> [MacInjuryRow] {
        var result: [MacInjuryRow] = []
        var teamIndex = 0
        for block in report.teams ?? [] {
            var entryIndex = 0
            for entry in block.entries ?? [] {
                if filter.allows(entry) {
                    let id = entryID(entry, teamIndex: teamIndex, entryIndex: entryIndex)
                    result.append(row(entry: entry, team: block.team, id: id))
                }
                entryIndex += 1
            }
            teamIndex += 1
        }
        return result
    }

    /// A stable identity for an entry: the id of its status row, else of its override, else its
    /// place in the report. The prefixes keep the three kinds from ever colliding.
    static func entryID(_ entry: LeagueAvailabilityEntry, teamIndex: Int, entryIndex: Int) -> String {
        if let statusId = entry.statusId {
            return "s" + String(statusId)
        }
        if let overrideId = entry.overrideId {
            return "o" + String(overrideId)
        }
        return "t" + String(teamIndex) + "-" + String(entryIndex)
    }

    /// The entry a row stands for, found again by the same identity.
    static func find(id: String, in report: LeagueAvailabilityReport) -> MacInjuryEntryRef? {
        var teamIndex = 0
        for block in report.teams ?? [] {
            var entryIndex = 0
            for entry in block.entries ?? [] {
                if entryID(entry, teamIndex: teamIndex, entryIndex: entryIndex) == id {
                    return MacInjuryEntryRef(entry: entry, team: block.team)
                }
                entryIndex += 1
            }
            teamIndex += 1
        }
        return nil
    }

    static func row(entry: LeagueAvailabilityEntry, team: LeagueTeamRef?, id: String) -> MacInjuryRow {
        let status: String = entry.status ?? ""
        let source: LeagueSource? = entry.source
        let published: Double? = Formatting.parseTimestamp(source?.publishedAt)?.timeIntervalSince1970
        let club: String = team?.displayAbbr ?? Formatting.emDash
        let player: String = entry.playerName ?? entry.player?.name ?? Formatting.emDash
        let expected: String = AvailabilityRowModel.returnText(text: entry.expectedReturnText,
                                                               structured: entry.expectedReturn) ?? Formatting.emDash

        return MacInjuryRow(
            id: id,
            club: club,
            player: player,
            statusKey: status,
            statusLabel: statusLabel(entry),
            statusSort: MacInjuryStatuses.order(for: entry.status),
            inModel: inModelText(entry),
            problem: problemText(entry),
            reasonCategory: entry.reasonCategory ?? "",
            reasonText: entry.reasonText ?? "",
            expectedReturn: expected,
            source: sourceText(source),
            sourceKind: source?.kind ?? "",
            sourceLabel: source?.label ?? "",
            sourceURL: source?.url ?? "",
            reported: LeagueFormatting.age(source?.publishedAt),
            reportedSort: LeagueFormatting.sortKey(published),
            isSuperseded: entry.inForce == false,
            isStale: entry.isStale == true,
            isOverride: entry.isOverride == true
        )
    }

    /// The server's own word for the status; "No report" when there is none.
    static func statusLabel(_ entry: LeagueAvailabilityEntry) -> String {
        if let status = entry.status, !status.isEmpty,
           let served = entry.statusLabel, !served.isEmpty {
            return served
        }
        return LeagueFormatting.statusWord(entry.status)
    }

    /// What the projection used, only when it differs from what was reported.
    static func inModelText(_ entry: LeagueAvailabilityEntry) -> String {
        guard let used = entry.modelStatus, !used.isEmpty, used != entry.status else {
            return Formatting.emDash
        }
        return LeagueFormatting.statusWord(used)
    }

    /// `Injury · Left knee sprain`: the reason category in words, then the reported text.
    static func problemText(_ entry: LeagueAvailabilityEntry) -> String {
        var parts: [String] = []
        if let category = entry.reasonCategory, !category.isEmpty {
            parts.append(LeagueFormatting.reasonWord(category))
        }
        if let text = entry.reasonText, !text.isEmpty {
            parts.append(text)
        }
        if parts.isEmpty {
            return Formatting.emDash
        }
        return parts.joined(separator: " · ")
    }

    /// `Club statement · Alderwick statement`.
    static func sourceText(_ source: LeagueSource?) -> String {
        var parts: [String] = []
        if let kind = source?.kind, !kind.isEmpty {
            parts.append(LeagueFormatting.sourceKindWord(kind))
        }
        if let label = source?.label, !label.isEmpty {
            parts.append(label)
        }
        if parts.isEmpty {
            return Formatting.emDash
        }
        return parts.joined(separator: " · ")
    }

    // MARK: Counts and lists the screen prints

    /// How many served entries no longer drive the projection (the switch's label).
    static func supersededCount(in report: LeagueAvailabilityReport) -> Int {
        var count = 0
        for block in report.teams ?? [] {
            for entry in block.entries ?? [] where entry.inForce == false {
                count += 1
            }
        }
        return count
    }

    /// How many entries the report carries at all.
    static func entryCount(in report: LeagueAvailabilityReport) -> Int {
        var count = 0
        for block in report.teams ?? [] {
            count += (block.entries ?? []).count
        }
        return count
    }

    /// Teams the server listed with no entry and no report: "No injury news found for ...".
    static func teamsWithoutNews(in report: LeagueAvailabilityReport) -> [String] {
        var codes: [String] = []
        for block in report.teams ?? [] {
            let isEmpty = (block.entries ?? []).isEmpty
            if isEmpty && block.reportState == "noReport" {
                codes.append(block.team?.displayAbbr ?? Formatting.emDash)
            }
        }
        return codes
    }

    /// Teams whose own report has not been submitted yet.
    static func teamsNotSubmitted(in report: LeagueAvailabilityReport) -> [String] {
        var codes: [String] = []
        for block in report.teams ?? [] where block.reportState == "notYetSubmitted" {
            codes.append(block.team?.displayAbbr ?? Formatting.emDash)
        }
        return codes
    }
}

// MARK: - Strips under the table

/// "No injury news found for: VAL, MIL" and, for the NBA, "Report not submitted yet: BOS". Draws
/// nothing when there is nothing to say.
struct MacInjuryStrips: View {

    private let report: LeagueAvailabilityReport?

    init(report: LeagueAvailabilityReport?) {
        self.report = report
    }

    private var withoutNews: [String] {
        guard let report = report else { return [] }
        return MacInjuryRows.teamsWithoutNews(in: report)
    }

    private var notSubmitted: [String] {
        guard let report = report else { return [] }
        return MacInjuryRows.teamsNotSubmitted(in: report)
    }

    var body: some View {
        VStack(spacing: Spacing.xs) {
            if !withoutNews.isEmpty {
                LeagueStrip(symbol: "info.circle",
                            text: "No injury news found for: " + withoutNews.joined(separator: ", "),
                            tint: Palette.neutral)
            }
            if !notSubmitted.isEmpty {
                LeagueStrip(symbol: "clock",
                            text: "Report not submitted yet: " + notSubmitted.joined(separator: ", "),
                            tint: Palette.neutral)
            }
        }
        .padding(.horizontal, Spacing.md)
    }
}

// MARK: - Table

struct MacInjuryTable: View {

    private let rows: [MacInjuryRow]
    @Binding private var selection: MacInjuryRow.ID?
    private let onShowDetails: (MacInjuryRow.ID) -> Void
    @State private var sortOrder: [KeyPathComparator<MacInjuryRow>] = [KeyPathComparator(\MacInjuryRow.club)]

    init(rows: [MacInjuryRow],
         selection: Binding<MacInjuryRow.ID?>,
         onShowDetails: @escaping (MacInjuryRow.ID) -> Void) {
        self.rows = rows
        _selection = selection
        self.onShowDetails = onShowDetails
    }

    private var sortedRows: [MacInjuryRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Club", value: \MacInjuryRow.club) { row in
                InjuryTextCell(text: row.club, dimmed: row.isSuperseded)
            }
            .width(min: 48, ideal: 60, max: 90)
            TableColumn("Player", value: \MacInjuryRow.player) { row in
                InjuryTextCell(text: row.player, dimmed: row.isSuperseded)
            }
            .width(min: 120, ideal: 170)
            TableColumn("Status", value: \MacInjuryRow.statusSort) { row in
                InjuryStatusCell(row: row)
            }
            .width(min: 150, ideal: 220)
            TableColumn("In model", value: \MacInjuryRow.inModel) { row in
                InjuryTextCell(text: row.inModel, dimmed: row.isSuperseded)
            }
            .width(min: 70, ideal: 90, max: 120)
            TableColumn("Problem", value: \MacInjuryRow.problem) { row in
                InjuryProblemCell(row: row)
            }
            .width(min: 160, ideal: 240)
            TableColumn("Expected return", value: \MacInjuryRow.expectedReturn) { row in
                InjuryTextCell(text: row.expectedReturn, dimmed: row.isSuperseded)
            }
            .width(min: 100, ideal: 130, max: 180)
            TableColumn("Source", value: \MacInjuryRow.source) { row in
                InjurySourceCell(row: row)
            }
            .width(min: 180, ideal: 260)
            TableColumn("Reported", value: \MacInjuryRow.reportedSort) { row in
                InjuryAgeCell(text: row.reported, dimmed: row.isSuperseded)
            }
            .width(min: 80, ideal: 96, max: 130)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacInjuryRow.ID.self) { ids in
            menuItems(for: ids)
        } primaryAction: { ids in
            if let id = ids.first {
                onShowDetails(id)
            }
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacInjuryRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Show Details") {
                onShowDetails(id)
            }
            Button("Copy Row") {
                MacTableExport.copy(header: MacInjuryRow.copyHeader, rows: [row.copyCells])
            }
        }
    }
}

// MARK: - Cells

/// Words in a table, dimmed when the entry is superseded.
private struct InjuryTextCell: View {

    private let text: String
    private let dimmed: Bool

    init(text: String, dimmed: Bool) {
        self.text = text
        self.dimmed = dimmed
    }

    var body: some View {
        Text(text)
            .hardwoodText(.tableCell, monospacedDigits: false)
            .lineLimit(1)
            .truncationMode(.tail)
            .opacity(dimmed ? 0.6 : 1.0)
            .help(text)
    }
}

/// How long ago the source published the entry, right-aligned; the exact time is the tooltip.
private struct InjuryAgeCell: View {

    private let text: String
    private let dimmed: Bool

    init(text: String, dimmed: Bool) {
        self.text = text
        self.dimmed = dimmed
    }

    var body: some View {
        NumCell(text: text)
            .opacity(dimmed ? 0.6 : 1.0)
    }
}

/// The status chip, and beside it Superseded or Stale (and Override for an entry a person typed).
private struct InjuryStatusCell: View {

    private let row: MacInjuryRow

    init(row: MacInjuryRow) {
        self.row = row
    }

    var body: some View {
        HStack(spacing: Spacing.xs) {
            StatusChip(status: row.statusKey, label: row.statusLabel)
            flags
            Spacer(minLength: 0)
        }
        .opacity(row.isSuperseded ? 0.6 : 1.0)
    }

    @ViewBuilder private var flags: some View {
        if row.isSuperseded {
            LeagueChip(text: "Superseded", tint: Palette.neutral)
        } else if row.isStale {
            LeagueChip(text: "Stale", tint: Palette.warning)
        }
        if row.isOverride {
            LeagueChip(text: "Override", tint: Palette.selection)
        }
    }
}

/// The reason category as a chip, then the reported text.
private struct InjuryProblemCell: View {

    private let row: MacInjuryRow

    init(row: MacInjuryRow) {
        self.row = row
    }

    var body: some View {
        HStack(spacing: Spacing.xs) {
            ReasonChip(category: row.reasonCategory)
            Text(row.reasonText)
                .hardwoodText(.tableCell, monospacedDigits: false)
                .lineLimit(1)
                .truncationMode(.tail)
            Spacer(minLength: 0)
        }
        .opacity(row.isSuperseded ? 0.6 : 1.0)
        .help(row.problem)
    }
}

/// Where the entry came from: the kind of source as a chip, and its label as a link to the source
/// when the server sent a usable web address.
private struct InjurySourceCell: View {

    private let row: MacInjuryRow

    init(row: MacInjuryRow) {
        self.row = row
    }

    private var labelText: String {
        row.sourceLabel.isEmpty ? "Unnamed source" : row.sourceLabel
    }

    var body: some View {
        HStack(spacing: Spacing.xs) {
            SourceKindChip(kind: row.sourceKind)
            label
            Spacer(minLength: 0)
        }
        .opacity(row.isSuperseded ? 0.6 : 1.0)
    }

    @ViewBuilder private var label: some View {
        if let url = LeagueWebLink.url(row.sourceURL) {
            Link(labelText, destination: url)
                .font(Typography.tableCell)
                .lineLimit(1)
                .help(url.absoluteString)
        } else {
            Text(labelText)
                .hardwoodText(.tableCell, monospacedDigits: false)
                .lineLimit(1)
        }
    }
}
#endif
