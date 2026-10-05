#if os(macOS)
import SwiftUI

// The Headlines tab of the Injury Report & News screen: one row per headline, with a button that
// starts recording a status from it.
//
// WHAT A HEADLINE IS HERE
// A title, a link, a date and the name of the outlet, and the teams the server tied it to; nothing
// else. Hardwood never fetches an article, so there is no excerpt to show and none is invented.
// `LeagueNewsRow` (League/) draws the row: the title is a link to the article in the reader's own
// browser, the age is how long ago the OUTLET published it, and a small chip names each team.
//
// WHAT THE BUTTON DOES
// "Record status from this headline" hands the headline's link, outlet and date to the Record Status
// sheet, so the person only has to say which player and what the headline reports. The status is
// still entered by a person: the app does not read headlines for injuries.
//
// AN EMPTY LIST SAYS WHY
// The screen decides the empty sentence (no feed configured, or a feed with nothing new) because it
// knows the freshness block; this view prints what it is given.

struct MacHeadlinesList: View {

    private let items: [LeagueNewsLink]
    private let emptyText: String
    private let onRecordStatus: (LeagueNewsLink) -> Void

    init(items: [LeagueNewsLink],
         emptyText: String,
         onRecordStatus: @escaping (LeagueNewsLink) -> Void) {
        self.items = items
        self.emptyText = emptyText
        self.onRecordStatus = onRecordStatus
    }

    var body: some View {
        if items.isEmpty {
            ContentUnavailableView("No headlines",
                                   systemImage: "newspaper",
                                   description: Text(emptyText))
        } else {
            list
        }
    }

    private var list: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(Array(items.enumerated()), id: \.offset) { pair in
                    headlineRow(pair.element)
                    Divider()
                }
            }
            .padding(.horizontal, Spacing.md)
        }
    }

    private func headlineRow(_ item: LeagueNewsLink) -> some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            LeagueNewsRow(item: item, density: .full)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button("Record status from this headline") {
                onRecordStatus(item)
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(.vertical, Spacing.sm)
    }
}
#endif
