#if os(macOS)
import SwiftUI

// The "Needs review" tab of the Injury Report & News screen: report rows whose player the server
// could not match to exactly one person.
//
// WHY A NAME IS NEVER GUESSED
// A report says "J. Smith" and a squad may hold two of them. The server never picks one: the row
// waits here until a person records the status again and chooses the player from the squad. This
// view lists what is waiting, with where each row came from, and offers that one action. It is the
// third tab of the screen, and the same view can be presented in a sheet; it holds no state of its
// own.
//
// WHAT A ROW SHOWS
// The kind of waiting item (a status, or for the EuroLeague a person the workbook named that the
// live service has not matched), the name as reported, the team, and (for a status) its source
// with the age of what the SOURCE said. "Record status" opens the Record Status sheet with the club
// and the name filled in; the person still chooses the player and the status.

struct MacReviewQueueSheet: View {

    private let league: LeagueKey
    private let items: [LeagueReviewQueueItem]
    private let onResolve: (LeagueReviewQueueItem) -> Void

    init(league: LeagueKey,
         items: [LeagueReviewQueueItem],
         onResolve: @escaping (LeagueReviewQueueItem) -> Void) {
        self.league = league
        self.items = items
        self.onResolve = onResolve
    }

    /// The NBA lists a roster and the EuroLeague a squad; the sentence says which to choose from.
    private var introText: String {
        let group: String = league == .nba ? "roster" : "squad"
        let first = "These rows named a player the server could not match to exactly one person. "
        let second = "Nothing is guessed. To resolve one, record the status again and choose the player from the "
        return first + second + group + "."
    }

    var body: some View {
        if items.isEmpty {
            ContentUnavailableView("Nothing is waiting for review",
                                   systemImage: "checkmark.circle",
                                   description: Text("Every report row was matched to a player."))
        } else {
            list
        }
    }

    private var list: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.md) {
                Text(introText)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(Array(items.enumerated()), id: \.offset) { pair in
                    MacReviewQueueRow(item: pair.element) {
                        onResolve(pair.element)
                    }
                    Divider()
                }
            }
            .padding(Spacing.md)
        }
    }
}

/// One waiting item.
private struct MacReviewQueueRow: View {

    private let item: LeagueReviewQueueItem
    private let onResolve: () -> Void

    init(item: LeagueReviewQueueItem, onResolve: @escaping () -> Void) {
        self.item = item
        self.onResolve = onResolve
    }

    private var kindText: String {
        item.kind == "person" ? "Person" : "Status"
    }

    private var nameText: String {
        item.playerName ?? item.name ?? Formatting.emDash
    }

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                HStack(spacing: Spacing.sm) {
                    LeagueChip(text: kindText, tint: Palette.neutral)
                    Text(nameText)
                        .hardwoodText(.widgetTitle)
                        .lineLimit(1)
                    teamChip
                }
                sourceLine
            }
            Spacer(minLength: 0)
            Button("Record status…") {
                onResolve()
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
    }

    @ViewBuilder private var teamChip: some View {
        if let team = item.team {
            LeagueChip(text: team.displayAbbr, tint: Palette.textSecondary)
                .help(team.displayName)
        }
    }

    @ViewBuilder private var sourceLine: some View {
        if item.source != nil {
            LeagueSourceLine(source: item.source, density: .full)
        }
    }
}
#endif
