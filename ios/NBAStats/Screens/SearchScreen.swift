import Foundation
import SwiftUI

/// Finding a player.
///
/// The search field is the whole screen: two characters start a query, a pause of about a quarter
/// of a second sends it, and every result is one tap from that player's page or from becoming the
/// favourite the dashboard resolves `$favorite_player` to.
public struct SearchScreen: View {

    @EnvironmentObject private var environment: AppEnvironment

    public init() { }

    public var body: some View {
        SearchScreenContent(environment: environment, client: environment.client)
    }
}

struct SearchScreenContent: View {

    /// Long enough that typing does not fire a request per keystroke, short enough that a pause
    /// feels like an answer rather than a wait.
    static let debounceNanoseconds: UInt64 = 280_000_000

    /// The contract's own floor: `q` must be at least two characters.
    static let minimumQueryLength = 2

    @ObservedObject private var environment: AppEnvironment
    private let client: any APIClientProtocol

    init(environment: AppEnvironment, client: any APIClientProtocol) {
        _environment = ObservedObject(wrappedValue: environment)
        self.client = client
    }

    @State private var query = ""
    @State private var results: [PlayerSearchResult] = []
    @State private var isSearching = false
    @State private var errorMessage: String?
    @State private var hasSearched = false
    @State private var selectedPlayer: PlayerRef?

    // MARK: Derived

    private var trimmedQuery: String {
        query.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var isIdle: Bool {
        trimmedQuery.count < SearchScreenContent.minimumQueryLength
    }

    /// The favourite, rebuilt from what was persisted, so the row is there before any request is.
    private var favoritePlayer: PlayerRef? {
        guard let id = environment.favoritePlayerID else { return nil }
        return PlayerRef(playerId: id, name: environment.favoritePlayerName ?? "Your player")
    }

    // MARK: Body

    var body: some View {
        NavigationStack {
            List {
                if isIdle {
                    favoriteSection
                    recentSection
                    aboutSection
                } else {
                    resultsSection
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Search")
            .searchable(text: $query, prompt: "Players")
            .autocorrectionDisabled()
            .textInputAutocapitalization(.words)
            .navigationDestination(item: $selectedPlayer) { player in
                PlayerDetailScreen(player: player)
                    .environmentObject(environment)
            }
            .task(id: query) {
                await runSearch()
            }
        }
    }

    // MARK: Sections

    @ViewBuilder private var favoriteSection: some View {
        if let player = favoritePlayer {
            Section {
                row(for: player, subtitle: "Your favourite player")
            } header: {
                Text("Favourite")
            } footer: {
                Text("Widgets set to $favorite_player follow whoever is starred here.")
            }
        }
    }

    @ViewBuilder private var recentSection: some View {
        if !environment.recentSearches.isEmpty {
            Section {
                ForEach(environment.recentSearches, id: \.self) { term in
                    Button {
                        query = term
                    } label: {
                        Label(term, systemImage: "clock.arrow.circlepath")
                            .hardwoodText(.tableCell)
                    }
                    .buttonStyle(.plain)
                }
                Button("Clear recent searches", role: .destructive) {
                    environment.clearRecentSearches()
                }
                .hardwoodText(.caption, color: Palette.negative)
            } header: {
                Text("Recent")
            }
        }
    }

    /// What the search field can actually reach, which is not the same thing in both modes.
    ///
    /// This copy used to promise "4,900 players" and offer `"doncic" finds Dončić` as the
    /// worked example, unconditionally. In demo mode both are false: `DemoAPIClient` builds its
    /// index by harvesting the player refs embedded in the bundled widget fixtures, so it can
    /// only find the couple of dozen players who appear on the sample dashboard — and Dončić is
    /// not one of them. A reader who followed the app's own example got an empty result, which is
    /// a worse failure than having no example at all.
    private var aboutSection: some View {
        Section {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text(environment.isDemoMode
                     ? "Demo data: only the players on the sample dashboard are searchable."
                     : "Search every player the server has loaded, from 1946-47 to tonight.")
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(environment.isDemoMode
                     ? "That is a few dozen names, not the league. Turn off \u{201C}Use bundled demo data\u{201D} in Settings, with the stats server running, to search the full roster."
                     : "Names match on any part and ignore accents, so \u{201C}doncic\u{201D} finds Don\u{10D}i\u{107}. Open a player to see their seasons, their career arc, and to put them straight onto a dashboard.")
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.vertical, Spacing.xs)
        }
    }

    @ViewBuilder private var resultsSection: some View {
        Section {
            if isSearching && results.isEmpty {
                searchingRow
            } else if let errorMessage = errorMessage {
                failureRow(errorMessage)
            } else if results.isEmpty && hasSearched {
                emptyRow
            } else {
                ForEach(results) { result in
                    row(for: result.player, subtitle: subtitle(for: result))
                }
            }
        } header: {
            Text(results.isEmpty ? "Results" : "\(results.count) players")
        }
    }

    private var searchingRow: some View {
        HStack(spacing: Spacing.sm) {
            ProgressView()
                .controlSize(.small)
            Text("Searching…")
                .hardwoodText(.tableCell, color: Palette.textSecondary)
        }
        .accessibilityElement(children: .combine)
    }

    private func failureRow(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Label("Search is unavailable", systemImage: "exclamationmark.triangle")
                .hardwoodText(.widgetTitle, color: Palette.warning)
            Text(message)
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
            Button("Try again") {
                Task { await performSearch(trimmedQuery) }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(.vertical, Spacing.xs)
    }

    /// "No match" means two different things, and only one of them is about the spelling.
    ///
    /// On a live server it usually is a spelling or an over-long query. In demo mode it almost
    /// always means the player exists but was never bundled, and "try a last name" is advice that
    /// cannot work — so the row says which of the two the reader is looking at.
    private var emptyRow: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("No players match \u{201C}\(trimmedQuery)\u{201D}.")
                .hardwoodText(.tableCell, color: Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Text(environment.isDemoMode
                 ? "Demo data only carries the players on the sample dashboard. Most of the league is missing, including current stars."
                 : "Try a last name, or fewer letters.")
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, Spacing.xs)
    }

    // MARK: Rows

    private func row(for player: PlayerRef, subtitle: String?) -> some View {
        HStack(spacing: Spacing.xs) {
            // A search result is a full-width list row and the reader is scanning for a person,
            // so this is the one place in the app that earns the 44pt portrait.
            PlayerRow(player: player, subtitle: subtitle, avatar: .medium)
            favoriteButton(for: player)
            Image(systemName: "chevron.right")
                .imageScale(.small)
                .foregroundStyle(Palette.textTertiary)
                .accessibilityHidden(true)
        }
        .contentShape(Rectangle())
        .onTapGesture {
            open(player)
        }
        .accessibilityElement(children: .contain)
        .accessibilityHint("Opens \(player.name).")
    }

    private func favoriteButton(for player: PlayerRef) -> some View {
        let isFavorite = environment.isFavorite(player: player)
        return Button {
            environment.toggleFavorite(player: player)
        } label: {
            Image(systemName: isFavorite ? "star.fill" : "star")
                .imageScale(.medium)
                .foregroundStyle(isFavorite ? Palette.warning : Palette.textTertiary)
                .frame(width: 36, height: 36)
                .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .accessibilityLabel(isFavorite
                            ? "Remove \(player.name) as your favourite player"
                            : "Make \(player.name) your favourite player")
    }

    private func subtitle(for result: PlayerSearchResult) -> String? {
        var parts: [String] = []
        if let position = result.player.position, !position.isEmpty {
            parts.append(position)
        }
        if let span = result.careerSpan {
            parts.append(span)
        }
        if result.player.isActive == false && result.careerSpan == nil {
            parts.append("Retired")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    // MARK: Searching

    /// The debounce. `.task(id:)` cancels the previous run whenever the query changes, so the
    /// sleep below is what turns a burst of keystrokes into one request.
    private func runSearch() async {
        let term = trimmedQuery
        guard term.count >= SearchScreenContent.minimumQueryLength else {
            results = []
            errorMessage = nil
            hasSearched = false
            isSearching = false
            return
        }
        isSearching = true
        do {
            try await Task.sleep(nanoseconds: SearchScreenContent.debounceNanoseconds)
        } catch {
            // Cancelled: a newer keystroke owns the search now.
            return
        }
        await performSearch(term)
    }

    private func performSearch(_ term: String) async {
        guard term.count >= SearchScreenContent.minimumQueryLength else { return }
        isSearching = true
        do {
            let response = try await client.searchPlayers(query: term, limit: 25)
            guard !Task.isCancelled else { return }
            results = response.results
            errorMessage = nil
        } catch {
            guard !APIError.isCancellation(error) else { return }
            results = []
            errorMessage = APIError.from(error).userMessage
        }
        hasSearched = true
        isSearching = false
    }

    /// Opening a player is what makes a search worth remembering.
    private func open(_ player: PlayerRef) {
        if !trimmedQuery.isEmpty {
            environment.recordSearch(trimmedQuery)
        }
        selectedPlayer = player
    }
}

#if DEBUG
#Preview("Search") {
    SearchScreen()
        .environmentObject(AppEnvironment.demo())
}
#endif
