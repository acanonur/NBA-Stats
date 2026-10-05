import Foundation
import SwiftUI

/// `availability_report`: who is out, doubtful or questionable, each status with its source.
///
/// The payload is the object `GET /v1/availability` and its EuroLeague twin return. A status never
/// travels alone: each row carries what kind of source said it, the source's name (a link on the
/// screens, plain text on a tile), how long ago the SOURCE published it, and whether it is stale or
/// has been superseded. "No report" is a missing status, never "available".
///
/// WHAT THE TILE SAYS ABOUT THE REPORT ITSELF
/// When the report's `state` is anything but fresh (stale, none yet, unreadable, switched off) a
/// strip above the rows prints the state and the server's message as they arrived. A report with
/// nothing in it says which of three things is true instead of one hopeful sentence: nobody is
/// listed, the team has not submitted its report, or there is no report to read.
///
/// SIZES
/// - Medium: up to six rows across all teams in the server's order, entries still in force first
///   and superseded ones (dimmed) after, then "n more".
/// - Large: up to fourteen rows grouped by team, each group headed by the team and the state of its
///   report, then up to three headlines (title, source and age only; the app never reads an
///   article), then the attribution line.
///
/// Rows are `AvailabilityEntryRow` at compact density, the same view the Mac's injury screen uses.
public struct AvailabilityReportWidget: View {

    /// One entry with the team it belongs to, numbered so a `ForEach` can tell two apart.
    private struct TileEntry: Identifiable {
        let id: Int
        let team: LeagueTeamRef?
        let entry: LeagueAvailabilityEntry
    }

    /// One team of a large tile: its heading and the entries that fit.
    private struct TileGroup: Identifiable {
        let id: Int
        let team: LeagueTeamRef?
        let reportState: String?
        let entries: [TileEntry]
    }

    private let payload: LeagueAvailabilityReport
    private let size: WidgetSize

    public init(payload: LeagueAvailabilityReport, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    private static let mediumRowLimit = 6
    private static let largeRowLimit = 14
    private static let headlineLimit = 3

    // MARK: Derived

    private var isLarge: Bool { size == .large }

    private var teams: [LeagueAvailabilityTeam] { payload.teams ?? [] }

    private var totalEntries: Int {
        var count = 0
        for team in teams {
            count += (team.entries ?? []).count
        }
        return count
    }

    /// The league's name and the day the report is for, when it has one.
    private var contextText: String {
        var parts: [String] = []
        if let key = LeagueKey(rawValue: payload.league ?? "") {
            parts.append(key.displayName)
        }
        if let day = teams.first?.game?.date, !day.isEmpty {
            parts.append(LeagueFormatting.leagueDate(day))
        }
        return parts.joined(separator: " · ")
    }

    /// Entries still in force first, superseded ones after, each in the server's order.
    private static func inForceFirst(_ entries: [LeagueAvailabilityEntry]) -> [LeagueAvailabilityEntry] {
        let current: [LeagueAvailabilityEntry] = entries.filter { $0.inForce != false }
        let superseded: [LeagueAvailabilityEntry] = entries.filter { $0.inForce == false }
        return current + superseded
    }

    /// Medium: every team's entries in one list, in force first.
    private var flatEntries: [TileEntry] {
        var found: [TileEntry] = []
        var counter = 0
        for team in teams {
            for entry in team.entries ?? [] {
                found.append(TileEntry(id: counter, team: team.team, entry: entry))
                counter += 1
            }
        }
        let current: [TileEntry] = found.filter { $0.entry.inForce != false }
        let superseded: [TileEntry] = found.filter { $0.entry.inForce == false }
        return current + superseded
    }

    /// Large: the teams that have entries, in the server's order, with at most fourteen rows
    /// between them.
    private var groups: [TileGroup] {
        var remaining = AvailabilityReportWidget.largeRowLimit
        var found: [TileGroup] = []
        var counter = 0
        for team in teams {
            let ordered = AvailabilityReportWidget.inForceFirst(team.entries ?? [])
            if ordered.isEmpty || remaining <= 0 {
                continue
            }
            var kept: [TileEntry] = []
            for entry in ordered.prefix(remaining) {
                kept.append(TileEntry(id: counter, team: team.team, entry: entry))
                counter += 1
            }
            remaining -= kept.count
            found.append(TileGroup(id: found.count,
                                   team: team.team,
                                   reportState: team.reportState,
                                   entries: kept))
        }
        return found
    }

    /// The abbreviations of the teams that have no entries and whose report is in this state.
    private func codesOfEmptyTeams(reportState state: String) -> [String] {
        var codes: [String] = []
        for team in teams {
            if !(team.entries ?? []).isEmpty { continue }
            if team.reportState == state {
                codes.append(team.team?.displayAbbr ?? Formatting.emDash)
            }
        }
        return codes
    }

    /// True when there is a report to read: a fresh one, a stale one, or a payload that names no
    /// state. Any other state (none yet, unreadable, switched off, one this build does not know)
    /// means there are no statuses to trust, so the tile says nothing about who is or is not listed.
    private var hasReadableReport: Bool {
        let state = payload.state ?? ""
        return state.isEmpty || state == "fresh" || state == "stale"
    }

    /// What an empty tile says, depending on why it is empty.
    private var emptyText: String {
        if !hasReadableReport {
            return "No statuses to show"
        }
        if !teams.isEmpty && teams.allSatisfy({ $0.reportState == "notYetSubmitted" }) {
            return teams.count == 1 ? "Team report not submitted yet" : "Team reports not submitted yet"
        }
        return "No reported absences"
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            LeagueStateBanner(state: payload.state, message: payload.message)
            if totalEntries == 0 {
                emptyBlock
            } else if isLarge {
                largeRows
            } else {
                mediumRows
            }
            newsSection
            attributionLine
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            if !contextText.isEmpty {
                Text(contextText)
                    .hardwoodText(.caption)
            }
            if payload.freshness?.isDemo == true {
                LeagueStrip(symbol: "exclamationmark.triangle.fill",
                            text: "Invented demo league — not real games",
                            tint: Palette.warning)
            }
        }
    }

    // MARK: Empty

    private var emptyBlock: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text(emptyText)
                .hardwoodText(.tableCell, color: Palette.textSecondary)
            if isLarge {
                teamStrips
            }
        }
    }

    // MARK: Medium

    private var mediumRows: some View {
        let all = flatEntries
        let shown = Array(all.prefix(AvailabilityReportWidget.mediumRowLimit))
        return VStack(alignment: .leading, spacing: Spacing.sm) {
            ForEach(shown) { item in
                AvailabilityEntryRow(entry: item.entry,
                                     density: LeagueDensity.compact,
                                     teamAbbr: item.team?.displayAbbr)
            }
            moreLine(hidden: all.count - shown.count)
        }
    }

    @ViewBuilder private func moreLine(hidden: Int) -> some View {
        if hidden > 0 {
            Text(String(hidden) + " more")
                .hardwoodText(.caption)
        }
    }

    // MARK: Large

    private var largeRows: some View {
        let built = groups
        var shownCount = 0
        for group in built {
            shownCount += group.entries.count
        }
        return VStack(alignment: .leading, spacing: Spacing.md) {
            ForEach(built) { group in
                groupView(group)
            }
            moreLine(hidden: totalEntries - shownCount)
            teamStrips
        }
    }

    private func groupView(_ group: TileGroup) -> some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            groupHeading(group)
            ForEach(group.entries) { item in
                AvailabilityEntryRow(entry: item.entry, density: LeagueDensity.compact)
            }
        }
    }

    private func groupHeading(_ group: TileGroup) -> some View {
        HStack(spacing: Spacing.sm) {
            TeamBadge(abbreviation: group.team?.displayAbbr ?? Formatting.emDash,
                      name: group.team?.name,
                      size: .small)
            if let state = group.reportState, !state.isEmpty, state != "submitted" {
                Text(LeagueFormatting.reportStateWord(state))
                    .hardwoodText(.caption)
            }
            Spacer(minLength: 0)
        }
    }

    /// The teams with nothing listed, one sentence per reason, so an empty team is never mistaken
    /// for a healthy one.
    @ViewBuilder private var teamStrips: some View {
        let notSubmitted = codesOfEmptyTeams(reportState: "notYetSubmitted")
        let noNews = codesOfEmptyTeams(reportState: "noReport")
        let quiet = codesOfEmptyTeams(reportState: "submitted")
        let hasAny = !notSubmitted.isEmpty || !noNews.isEmpty || !quiet.isEmpty
        if hasReadableReport && hasAny {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                stripText("Report not submitted yet: ", codes: notSubmitted)
                stripText("No injury news found for: ", codes: noNews)
                stripText("No reported absences: ", codes: quiet)
            }
        }
    }

    @ViewBuilder private func stripText(_ title: String, codes: [String]) -> some View {
        if !codes.isEmpty {
            Text(title + codes.joined(separator: ", "))
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: Headlines and attribution

    @ViewBuilder private var newsSection: some View {
        let items: [LeagueNewsLink] = Array((payload.news ?? []).prefix(AvailabilityReportWidget.headlineLimit))
        if isLarge && !items.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Divider()
                Text("Headlines")
                    .hardwoodText(.statLabel)
                ForEach(Array(items.enumerated()), id: \.offset) { pair in
                    LeagueNewsRow(item: pair.element, density: LeagueDensity.compact)
                }
            }
        }
    }

    @ViewBuilder private var attributionLine: some View {
        if let attribution = payload.attribution, !attribution.isEmpty {
            Text(attribution)
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

#if DEBUG
#Preview("Availability report") {
    ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            AvailabilityReportWidget(payload: LeagueAvailabilityReport.preview, size: .large)
                .hardwoodCard()
            AvailabilityReportWidget(payload: LeagueAvailabilityReport.preview, size: .medium)
                .hardwoodCard()
            AvailabilityReportWidget(payload: LeagueAvailabilityReport(), size: .medium)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
