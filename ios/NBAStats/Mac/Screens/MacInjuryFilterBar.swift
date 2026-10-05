#if os(macOS)
import SwiftUI

// The filter bar of the Injury Report & News screen: the three tabs, the club or team filter, and
// (on the Injuries tab) the status toggles, the superseded switch and the name filter.
//
// WHY IT IS ITS OWN VIEW
// The screen owns the data and the requests; this view owns nothing but the controls. Every value
// it changes is a binding into the screen's `@State`, so a click here is a plain assignment there,
// and the screen decides what a change means (the club filter is part of the request, the status
// toggles only decide which served entries to draw).
//
// TWO ROWS, BECAUSE THE WINDOW IS NARROW
// With the inspector open the screen has under 700 points. The first row holds the tabs and the club
// menu; the second, only on the Injuries tab, holds the filters that act on rows.
//
// WHAT THE LABELS COUNT
// "Needs review (n)" is how many report rows the server says are waiting; "Show superseded (n)" is
// how many served entries no longer drive the projection. Both are counts of what was served, and
// both are left off until the data has loaded, rather than shown as 0.

struct MacInjuryFilterBar: View {

    private let league: LeagueKey
    @Binding private var tab: MacInjuryTab
    @Binding private var teamFilter: String?
    @Binding private var shownStatuses: Set<String>
    @Binding private var showsSuperseded: Bool
    @Binding private var nameFilter: String
    private let reviewCount: Int?
    private let supersededCount: Int?
    private let isLoading: Bool

    init(league: LeagueKey,
         tab: Binding<MacInjuryTab>,
         teamFilter: Binding<String?>,
         shownStatuses: Binding<Set<String>>,
         showsSuperseded: Binding<Bool>,
         nameFilter: Binding<String>,
         reviewCount: Int?,
         supersededCount: Int?,
         isLoading: Bool) {
        self.league = league
        _tab = tab
        _teamFilter = teamFilter
        _shownStatuses = shownStatuses
        _showsSuperseded = showsSuperseded
        _nameFilter = nameFilter
        self.reviewCount = reviewCount
        self.supersededCount = supersededCount
        self.isLoading = isLoading
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            tabRow
            filterRow
        }
    }

    // MARK: Tabs and club

    private var tabRow: some View {
        HStack(spacing: Spacing.md) {
            Picker("View", selection: $tab) {
                ForEach(MacInjuryTab.allCases) { option in
                    Text(title(of: option)).tag(option)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 400)
            clubFilter
            loadingIndicator
        }
    }

    private func title(of option: MacInjuryTab) -> String {
        switch option {
        case .injuries:
            return "Injuries"
        case .headlines:
            return "Headlines"
        case .review:
            guard let count = reviewCount else { return "Needs review" }
            return "Needs review (" + String(count) + ")"
        }
    }

    @ViewBuilder private var clubFilter: some View {
        if tab != .review {
            MacTeamPicker(league: league,
                          title: league == .nba ? "Team" : "Club",
                          selection: $teamFilter,
                          noneLabel: league == .nba ? "All teams" : "All clubs")
        }
    }

    @ViewBuilder private var loadingIndicator: some View {
        if isLoading {
            ProgressView()
                .controlSize(.small)
        }
    }

    // MARK: Filters that act on rows

    @ViewBuilder private var filterRow: some View {
        if tab == .injuries {
            HStack(spacing: Spacing.md) {
                statusMenu
                Toggle(supersededTitle, isOn: $showsSuperseded)
                TextField("Filter by player", text: $nameFilter)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 180)
            }
        }
    }

    private var supersededTitle: String {
        guard let count = supersededCount else { return "Show superseded" }
        return "Show superseded (" + String(count) + ")"
    }

    private var statusMenu: some View {
        Menu {
            ForEach(MacInjuryStatuses.choices) { choice in
                Toggle(choice.label, isOn: statusBinding(choice.key))
            }
        } label: {
            Label("Status", systemImage: "line.3.horizontal.decrease.circle")
        }
        .fixedSize()
    }

    private func statusBinding(_ statusKey: String) -> Binding<Bool> {
        Binding<Bool>(
            get: { shownStatuses.contains(statusKey) },
            set: { isOn in
                if isOn {
                    shownStatuses.insert(statusKey)
                } else {
                    shownStatuses.remove(statusKey)
                }
            }
        )
    }
}
#endif
