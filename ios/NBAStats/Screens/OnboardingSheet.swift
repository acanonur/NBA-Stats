import Foundation
import SwiftUI

/// The three things worth saying before the first dashboard appears: pick a starting point, tell
/// Hardwood who you follow, and understand that the numbers arrive as each game finishes rather
/// than all at once overnight.
///
/// Nothing here is required. Every page can be skipped, and skipping leaves a working dashboard.
public struct OnboardingSheet: View {

    private let onFinish: () -> Void

    @EnvironmentObject private var environment: AppEnvironment

    public init(onFinish: @escaping () -> Void) {
        self.onFinish = onFinish
    }

    public var body: some View {
        OnboardingSheetContent(environment: environment,
                               store: environment.store,
                               catalog: environment.catalog,
                               client: environment.client,
                               onFinish: onFinish)
    }
}

struct OnboardingSheetContent: View {

    /// The three pages, in order.
    enum Page: Int, CaseIterable, Identifiable {
        case preset
        case favorites
        case freshness

        var id: Int { rawValue }

        var title: String {
            switch self {
            case .preset:    return "Pick a starting point"
            case .favorites: return "Who do you follow?"
            case .freshness: return "As each game finishes"
            }
        }
    }

    @ObservedObject private var environment: AppEnvironment
    @ObservedObject private var store: DashboardStore
    private let catalog: Catalog
    private let client: any APIClientProtocol
    private let onFinish: () -> Void

    init(environment: AppEnvironment,
         store: DashboardStore,
         catalog: Catalog,
         client: any APIClientProtocol,
         onFinish: @escaping () -> Void) {
        _environment = ObservedObject(wrappedValue: environment)
        _store = ObservedObject(wrappedValue: store)
        self.catalog = catalog
        self.client = client
        self.onFinish = onFinish
    }

    @State private var page: Page = .preset
    @State private var teams: [TeamRef] = []
    @State private var playerQuery = ""
    @State private var playerResults: [PlayerRef] = []
    @State private var isSearchingPlayers = false

    // MARK: Derived

    private var accent: AccentName { store.selectedLayout?.accent ?? .orange }

    private var isLastPage: Bool { page == .freshness }

    private var chosenPresetKeys: Set<String> {
        Set(store.layouts.compactMap { $0.presetKey })
    }

    // MARK: Body

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                pages
                footer
            }
            .background(Palette.background.ignoresSafeArea())
            .navigationTitle(page.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Skip", action: onFinish)
                }
            }
            .task {
                await loadTeams()
            }
        }
    }

    private var pages: some View {
        TabView(selection: $page) {
            presetPage
                .tag(Page.preset)
            favoritesPage
                .tag(Page.favorites)
            freshnessPage
                .tag(Page.freshness)
        }
        .tabViewStyle(.page(indexDisplayMode: .always))
        .indexViewStyle(.page(backgroundDisplayMode: .always))
    }

    private var footer: some View {
        HStack(spacing: Spacing.md) {
            if page != .preset {
                Button("Back") {
                    withAnimation { page = previousPage }
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
            }
            Spacer(minLength: 0)
            Button(isLastPage ? "Start" : "Next") {
                if isLastPage {
                    onFinish()
                } else {
                    withAnimation { page = nextPage }
                }
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .tint(accent.color)
        }
        .padding(.horizontal, Spacing.lg)
        .padding(.vertical, Spacing.md)
        .background(.ultraThinMaterial)
    }

    private var nextPage: Page {
        switch page {
        case .preset:    return .favorites
        case .favorites: return .freshness
        case .freshness: return .freshness
        }
    }

    private var previousPage: Page {
        switch page {
        case .preset:    return .preset
        case .favorites: return .preset
        case .freshness: return .favorites
        }
    }

    // MARK: Page one — presets

    private var presetPage: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.md) {
                Text("A dashboard is yours to rearrange — these are just good places to begin. You can add, resize and remove widgets on any of them afterwards.")
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(catalog.presets) { preset in
                    presetRow(preset)
                }
                if catalog.presets.isEmpty {
                    Text("No presets shipped with this build, so Hardwood started you on an empty board.")
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(Spacing.lg)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func presetRow(_ preset: DashboardLayout) -> some View {
        let key = preset.presetKey ?? preset.id
        let isChosen = chosenPresetKeys.contains(key)
        return Button {
            choose(preset)
        } label: {
            HStack(alignment: .top, spacing: Spacing.md) {
                Image(systemName: preset.icon)
                    .font(.title3)
                    .foregroundStyle(preset.accent.color)
                    .frame(width: 42, height: 42)
                    .background(Circle().fill(preset.accent.softTint))
                VStack(alignment: .leading, spacing: Spacing.xxs) {
                    Text(preset.name)
                        .hardwoodText(.widgetTitle)
                    Text(preset.tagline ?? "\(preset.widgets.count) widgets")
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: Spacing.sm)
                Image(systemName: isChosen ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isChosen ? preset.accent.color : Palette.textTertiary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .hardwoodCard(padding: Spacing.md)
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(isChosen ? AccessibilityTraits.isSelected : [])
    }

    /// Picking a preset you already have simply switches to it, rather than making a second copy.
    private func choose(_ preset: DashboardLayout) {
        let key = preset.presetKey ?? preset.id
        if let existing = store.layouts.first(where: { $0.presetKey == key }) {
            store.select(existing.id)
        } else {
            store.addLayout(fromPreset: key)
        }
    }

    // MARK: Page two — favourites

    private var favoritesPage: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.lg) {
                Text("Several presets point their widgets at “your player” and “your team”. Choose them here and those widgets fill themselves in — or skip, and Hardwood features the league leaders instead.")
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                teamChooser
                playerChooser
            }
            .padding(Spacing.lg)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var teamChooser: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            SectionHeader(title: "Team", subtitle: teamSubtitle)
            if teams.isEmpty {
                Text("Teams could not be loaded. You can pick one later in Settings.")
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 72), spacing: Spacing.sm)],
                          spacing: Spacing.sm) {
                    ForEach(teams) { team in
                        teamButton(team)
                    }
                }
            }
        }
    }

    private var teamSubtitle: String {
        guard let id = environment.favoriteTeamID else { return "Not set" }
        return teams.first { $0.teamId == id }?.name ?? environment.favoriteTeamName ?? "Team #\(id)"
    }

    private func teamButton(_ team: TeamRef) -> some View {
        let isChosen = environment.favoriteTeamID == team.teamId
        return Button {
            environment.setFavoriteTeam(isChosen ? nil : team)
        } label: {
            TeamBadge(team: team, size: .medium)
                .opacity(isChosen ? 1 : 0.55)
                .overlay(alignment: .topTrailing) {
                    if isChosen {
                        Image(systemName: "checkmark.circle.fill")
                            .imageScale(.small)
                            .foregroundStyle(accent.color)
                            .offset(x: 4, y: -4)
                    }
                }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(team.name)
        .accessibilityAddTraits(isChosen ? AccessibilityTraits.isSelected : [])
    }

    private var playerChooser: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            SectionHeader(title: "Player", subtitle: environment.favoritePlayerName ?? "Not set")
            TextField("Search players", text: $playerQuery)
                .textFieldStyle(.roundedBorder)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.words)
            playerResultList
        }
        .task(id: playerQuery) {
            await searchPlayers()
        }
    }

    @ViewBuilder private var playerResultList: some View {
        if isSearchingPlayers {
            HStack(spacing: Spacing.sm) {
                ProgressView()
                    .controlSize(.small)
                Text("Searching…")
                    .hardwoodText(.caption)
            }
        } else if playerResults.isEmpty && playerQuery.count >= 2 {
            Text("No players match “\(playerQuery)”.")
                .hardwoodText(.caption)
        } else {
            VStack(spacing: Spacing.xs) {
                ForEach(playerResults) { player in
                    playerButton(player)
                }
            }
        }
    }

    private func playerButton(_ player: PlayerRef) -> some View {
        let isChosen = environment.favoritePlayerID == player.playerId
        return Button {
            environment.setFavoritePlayer(isChosen ? nil : player)
        } label: {
            HStack(spacing: Spacing.sm) {
                PlayerRow(player: player, subtitle: player.position)
                Image(systemName: isChosen ? "star.fill" : "star")
                    .foregroundStyle(isChosen ? Palette.warning : Palette.textTertiary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isChosen ? AccessibilityTraits.isSelected : [])
    }

    // MARK: Page three — freshness

    private var freshnessPage: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.lg) {
                freshnessPoint(icon: "clock.arrow.circlepath",
                               title: "Stats land game by game",
                               detail: "When a game goes final, that one box score is pulled and the season numbers behind it are recomputed. There is no nightly wait: a dashboard opened at half past eleven already has the early games on it.")
                freshnessPoint(icon: "arrow.down.circle",
                               title: "Pull down to refresh",
                               detail: "Hardwood checks for finished games when you open it, when you pull the board down, and — if iOS allows it — quietly in the background.")
                freshnessPoint(icon: "minus.circle",
                               title: "An em dash is not a zero",
                               detail: "The league did not record blocks before 1973-74 or possessions before 1996-97. Where a stat never existed Hardwood shows “—” and explains why; where it can only be estimated from the box score, it says “est.” rather than pretending.")
                freshnessPoint(icon: "square.and.pencil",
                               title: "Everything is editable",
                               detail: "Tap Edit on the board to add, resize, reorder or remove widgets. Nothing is saved to a server — your dashboards live on this device.")
                Text(environment.attribution)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(Spacing.lg)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func freshnessPoint(icon: String, title: String, detail: String) -> some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            Image(systemName: icon)
                .font(.title3)
                .foregroundStyle(accent.color)
                .frame(width: 30)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text(title)
                    .hardwoodText(.widgetTitle)
                    .fixedSize(horizontal: false, vertical: true)
                Text(detail)
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: Loading

    private func loadTeams() async {
        guard teams.isEmpty else { return }
        do {
            let response = try await client.teams()
            teams = response.teams.sorted {
                $0.abbr.localizedCaseInsensitiveCompare($1.abbr) == .orderedAscending
            }
        } catch {
            teams = []
        }
    }

    private func searchPlayers() async {
        let term = playerQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        guard term.count >= 2 else {
            playerResults = []
            isSearchingPlayers = false
            return
        }
        isSearchingPlayers = true
        do {
            try await Task.sleep(nanoseconds: 280_000_000)
        } catch {
            return
        }
        do {
            let response = try await client.searchPlayers(query: term, limit: 6)
            guard !Task.isCancelled else { return }
            playerResults = response.results.map { $0.player }
        } catch {
            guard !APIError.isCancellation(error) else { return }
            playerResults = []
        }
        isSearchingPlayers = false
    }
}

#if DEBUG
#Preview("Onboarding") {
    OnboardingSheet(onFinish: { })
        .environmentObject(AppEnvironment.demo())
}
#endif
