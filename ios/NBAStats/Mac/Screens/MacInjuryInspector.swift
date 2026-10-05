#if os(macOS)
import SwiftUI

// The inspector of the Injury Report & News screen: one availability entry in full, with its source
// as a link, and a Retract button for an entry a person typed.
//
// WHAT "IN FULL" MEANS
// `AvailabilityEntryRow` at full density prints every field the server sent for the entry: the
// status and what the projection used instead (when they differ), the reason, when the player is
// expected back, the chance of playing, the game, when the source said it, when Hardwood fetched it,
// how long ago that was, and, for a superseded entry, why it no longer counts. The source line is the
// kind of source, its label as a link to the source, and its age. Nothing is summarised.
//
// WHO MAY RETRACT WHAT
// Only an entry a person typed can be retracted. For the EuroLeague that is a status whose source is
// "manual" (the server appends a retraction and keeps the original); for the NBA it is an override
// (the server clears it and keeps the row). Every other entry (a league report row, a club statement
// the workbook imported) is the source's word, and the app offers no way to take it back. The button
// asks the screen to confirm; the screen sends the request. A refusal is shown under the entry.

struct MacInjuryInspector: View {

    private let league: LeagueKey
    private let ref: MacInjuryEntryRef?
    private let retractProblem: String?
    private let onRetract: (Int, String) -> Void

    /// `onRetract` is called with the id the delete route takes and the player's name, for the
    /// screen's confirmation.
    init(league: LeagueKey,
         ref: MacInjuryEntryRef?,
         retractProblem: String?,
         onRetract: @escaping (Int, String) -> Void) {
        self.league = league
        self.ref = ref
        self.retractProblem = retractProblem
        self.onRetract = onRetract
    }

    var body: some View {
        content
            .inspectorColumnWidth(min: 280, ideal: 340, max: 460)
    }

    @ViewBuilder private var content: some View {
        if let current = ref {
            details(current)
        } else {
            ContentUnavailableView("Select an entry",
                                   systemImage: "cross.case",
                                   description: Text("Choose a row to see its source and every detail."))
        }
    }

    private func details(_ current: MacInjuryEntryRef) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.md) {
                AvailabilityEntryRow(entry: current.entry,
                                     density: .full,
                                     teamAbbr: current.team?.displayAbbr,
                                     onRetract: retractAction(for: current.entry))
                retractNote(for: current.entry)
                problemStrip
            }
            .padding(Spacing.lg)
        }
    }

    // MARK: Retracting

    /// The id the delete route takes, only for an entry a person typed. Nil for everything else.
    static func retractID(for entry: LeagueAvailabilityEntry, league: LeagueKey) -> Int? {
        switch league {
        case .nba:
            if entry.isOverride == true {
                return entry.overrideId
            }
            return nil
        case .euroleague:
            if entry.source?.kind == "manual" {
                return entry.statusId
            }
            return nil
        }
    }

    private func retractAction(for entry: LeagueAvailabilityEntry) -> (() -> Void)? {
        guard let id = MacInjuryInspector.retractID(for: entry, league: league) else { return nil }
        let name: String = entry.playerName ?? entry.player?.name ?? "this player"
        return {
            onRetract(id, name)
        }
    }

    @ViewBuilder private func retractNote(for entry: LeagueAvailabilityEntry) -> some View {
        if MacInjuryInspector.retractID(for: entry, league: league) != nil {
            Text(explanation)
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var explanation: String {
        switch league {
        case .euroleague:
            return "Retracting adds a retraction entry. The original stays in the history."
        case .nba:
            return "Clearing takes the override out of projections. The row stays in the history."
        }
    }

    @ViewBuilder private var problemStrip: some View {
        if let message = retractProblem {
            LeagueStrip(symbol: "exclamationmark.triangle", text: message, tint: Palette.warning)
        }
    }
}
#endif
