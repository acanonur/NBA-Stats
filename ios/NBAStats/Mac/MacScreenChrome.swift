#if os(macOS)
import SwiftUI

// The pieces every Mac screen is built from: the filter bar, the load states (loading, failed,
// stale, league off), the table cells, the card, the title and subtitle, the request consumers, and
// the load key.
//
// WHY ONE FILE OF SMALL HELPERS
// Fourteen screens are written by separate packages, and a reader switching between them should
// feel one app. Everything a screen draws that is not its own data lives here, so the loading
// spinner, the failure message, the "turned off" notice and the table cell alignment are the same
// everywhere, and a fix to one is a fix to all.
//
// THE STATES A SCREEN MOVES THROUGH (MAC_DESIGN 3.1), AND WHICH HELPER DRAWS EACH
//   first load, nothing yet       MacLoadingView
//   failed, nothing to show       MacLoadFailureView (or MacLeagueOffView for a league that is off)
//   failed, an older payload      the old payload, with MacStaleStrip above it
//   loaded, no rows               the screen's own ContentUnavailableView
// The screen decides which applies from its `payload` and `failure` `@State`; these views only draw.
//
// WHAT NONE OF THESE DO
// None of them computes a number. A cell shows the string it is given. A missing value reaches
// them already written as an em dash, so a missing statistic is never drawn as 0.

// MARK: - Load key

/// What a screen's `.task(id:)` watches: the request it makes and the league's reload counter.
/// A change in either restarts the load, and an unrelated redraw does not.
struct MacLoadKey: Hashable {
    let route: LeagueRoute
    let generation: Int
}

// MARK: - Words

enum MacText {

    /// The navigation subtitle: `EuroLeague · data through Fri, Oct 2`.
    static func subtitle(league: LeagueKey, dataThrough: String?) -> String {
        guard let day = dataThrough, !day.isEmpty else {
            return league.displayName
        }
        return league.displayName + " · data through " + LeagueFormatting.leagueDate(day)
    }

    /// What a screen says when demo mode has no bundled copy of its data.
    static let demoNotBundled = "Demo data for this screen isn't bundled yet"
}

extension View {

    /// The window title and subtitle of a league screen, set the same way everywhere.
    func macScreenTitle(_ screen: MacScreen, league: LeagueKey, dataThrough: String?) -> some View {
        self
            .navigationTitle(screen.title(for: league))
            .navigationSubtitle(MacText.subtitle(league: league, dataThrough: dataThrough))
    }
}

// MARK: - Filter bar

/// The row of pickers, steppers and toggles at the top of a screen, with a rule beneath it. Every
/// filter a screen has lives here, never in the window toolbar.
struct MacFilterBar<Content: View>: View {

    private let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: Spacing.md) {
                content
                Spacer(minLength: 0)
            }
            .padding(Spacing.md)
            Divider()
        }
    }
}

/// The toolbar button that shows or hides the screen's inspector.
struct MacInspectorToggle: View {

    @EnvironmentObject private var model: MacAppModel

    init() { }

    var body: some View {
        Button {
            model.isInspectorShown.toggle()
        } label: {
            Label("Inspector", systemImage: "sidebar.trailing")
        }
        .help("Show or hide the inspector")
    }
}

// MARK: - Load states

/// First load, nothing to show yet.
struct MacLoadingView: View {

    init() { }

    var body: some View {
        ProgressView("Loading…")
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// A load that failed with nothing older to show. A league the server has turned off, and a demo
/// fixture that is not bundled, get their own plain messages; every other failure shows the error's
/// headline, its sentence, any hint, and a Retry button.
struct MacLoadFailureView: View {

    @EnvironmentObject private var model: MacAppModel
    private let failure: APIError
    private let league: LeagueKey?
    private let retry: () -> Void

    init(error: APIError, league: LeagueKey? = nil, retry: @escaping () -> Void) {
        self.failure = error
        self.league = league
        self.retry = retry
    }

    private var isLeagueOff: Bool {
        failure.rawCode == "league_unavailable"
    }

    private var isFixtureMissing: Bool {
        if case .demoModeMissingFixture = failure {
            return true
        }
        return false
    }

    var body: some View {
        content
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder private var content: some View {
        if isLeagueOff {
            MacLeagueOffView(league: league ?? model.league, reason: failure.userMessage)
        } else if isFixtureMissing {
            ContentUnavailableView {
                Label(MacText.demoNotBundled, systemImage: "shippingbox")
            } description: {
                Text("Turn off demo data to read this screen from your server.")
            } actions: {
                Button("Use My Server") {
                    model.useLiveServer()
                }
            }
        } else {
            ContentUnavailableView {
                Label(failure.title, systemImage: "exclamationmark.triangle")
            } description: {
                failureText
            } actions: {
                Button("Retry") {
                    retry()
                }
            }
        }
    }

    private var failureText: some View {
        VStack(spacing: Spacing.xs) {
            Text(failure.userMessage)
            if let hint = failure.recoveryHint {
                Text(hint)
            }
        }
    }
}

/// "EuroLeague is turned off on your server", with the server's own reason.
struct MacLeagueOffView: View {

    private let league: LeagueKey
    private let reason: String?

    init(league: LeagueKey, reason: String?) {
        self.league = league
        self.reason = reason
    }

    private var reasonText: String {
        if let reason = reason, !reason.isEmpty {
            return reason
        }
        return "This league is not available on your server."
    }

    var body: some View {
        ContentUnavailableView(league.displayName + " is turned off on your server",
                               systemImage: "power",
                               description: Text(reasonText))
    }
}

/// A refresh failed but an older payload is still on screen: say so above it, with Retry.
struct MacStaleStrip: View {

    private let message: String
    private let retry: () -> Void

    init(message: String, retry: @escaping () -> Void) {
        self.message = message
        self.retry = retry
    }

    var body: some View {
        HStack(spacing: Spacing.sm) {
            LeagueStrip(symbol: "exclamationmark.triangle",
                        text: "Couldn't refresh: " + message,
                        tint: Palette.warning)
            Button("Retry") {
                retry()
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(.horizontal, Spacing.md)
        .padding(.vertical, Spacing.xs)
    }
}

// MARK: - Table cells

/// A number in a table: right-aligned, with digits that keep their width.
struct NumCell: View {

    private let text: String

    init(text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .hardwoodText(.tableCell)
            .lineLimit(1)
            .frame(maxWidth: .infinity, alignment: .trailing)
    }
}

/// Words in a table. The full text is the tooltip, for a cell too narrow to show it.
struct TextCell: View {

    private let text: String

    init(text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .hardwoodText(.tableCell, monospacedDigits: false)
            .lineLimit(1)
            .truncationMode(.tail)
            .help(text)
    }
}

/// A team's badge, small enough for a table row.
struct TeamCell: View {

    private let abbr: String
    private let name: String?

    init(abbr: String, name: String?) {
        self.abbr = abbr
        self.name = name
    }

    var body: some View {
        HStack(spacing: 0) {
            TeamBadge(abbreviation: abbr, name: name, size: .small)
            Spacer(minLength: 0)
        }
    }
}

/// A player's availability as a chip. An empty `status` is "No report", never "Available".
struct StatusChipCell: View {

    private let status: String
    private let label: String

    init(status: String, label: String) {
        self.status = status
        self.label = label
    }

    var body: some View {
        HStack(spacing: 0) {
            StatusChip(status: status, label: label)
            Spacer(minLength: 0)
        }
    }
}

/// A defence band as a chip. An empty `band` is an em dash.
struct BandChipCell: View {

    private let band: String

    init(band: String) {
        self.band = band
    }

    var body: some View {
        HStack(spacing: 0) {
            BandChip(band: band)
            Spacer(minLength: 0)
        }
    }
}

// MARK: - Card

/// A titled card for the screens that are cards rather than tables (Start Here, the team header).
struct SectionCard<Content: View>: View {

    private let title: String
    private let content: Content

    init(title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            Text(title)
                .hardwoodText(.sectionTitle)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .hardwoodCard()
    }
}

// MARK: - Requests left by menus and other screens

/// Runs `handler` for a pending action, whether it was left before this screen appeared (the menu
/// switched screens first) or while it is on screen, and clears it so it runs once.
struct MacPendingActionModifier: ViewModifier {

    @EnvironmentObject private var model: MacAppModel
    private let handler: (MacPendingAction) -> Void

    init(handler: @escaping (MacPendingAction) -> Void) {
        self.handler = handler
    }

    func body(content: Content) -> some View {
        content
            .onAppear {
                consume()
            }
            .onChange(of: model.pendingAction) { _, _ in
                consume()
            }
    }

    private func consume() {
        guard let action = model.pendingAction else { return }
        model.pendingAction = nil
        handler(action)
    }
}

/// The same for "show me this game / team / round".
///
/// `screen` limits it to the requests meant for that screen (`MacNavigation.screen`); a request
/// for another screen is left on the model untouched. Without the filter, the screen the reader is
/// leaving (still on screen for one more update after `open(_:)` switched the selection) could take
/// and drop a request meant for the screen being opened. nil takes every request.
struct MacPendingNavigationModifier: ViewModifier {

    @EnvironmentObject private var model: MacAppModel
    private let screen: MacScreen?
    private let handler: (MacNavigation) -> Void

    init(screen: MacScreen?, handler: @escaping (MacNavigation) -> Void) {
        self.screen = screen
        self.handler = handler
    }

    func body(content: Content) -> some View {
        content
            .onAppear {
                consume()
            }
            .onChange(of: model.pendingNavigation) { _, _ in
                consume()
            }
    }

    private func consume() {
        guard let navigation = model.pendingNavigation else { return }
        if let only = screen, navigation.screen != only {
            return
        }
        model.pendingNavigation = nil
        handler(navigation)
    }
}

/// And for stepping a round or a day back and forward. Only changes after the screen appears
/// count: a step requested before it existed is not replayed.
struct MacStepModifier: ViewModifier {

    @EnvironmentObject private var model: MacAppModel
    private let handler: (Int) -> Void

    init(handler: @escaping (Int) -> Void) {
        self.handler = handler
    }

    func body(content: Content) -> some View {
        content
            .onChange(of: model.step) { _, newValue in
                if let request = newValue {
                    handler(request.delta)
                }
            }
    }
}

extension View {

    /// Record Status, Paste Link: see `MacPendingActionModifier`.
    func macOnPendingAction(perform handler: @escaping (MacPendingAction) -> Void) -> some View {
        modifier(MacPendingActionModifier(handler: handler))
    }

    /// Open Matchup, Open Defence, a round chosen elsewhere: see `MacPendingNavigationModifier`.
    /// Pass the screen so only the requests meant for it are taken.
    func macOnPendingNavigation(for screen: MacScreen? = nil,
                                perform handler: @escaping (MacNavigation) -> Void) -> some View {
        modifier(MacPendingNavigationModifier(screen: screen, handler: handler))
    }

    /// Previous and next round or day: see `MacStepModifier`.
    func macOnStep(perform handler: @escaping (Int) -> Void) -> some View {
        modifier(MacStepModifier(handler: handler))
    }
}
#endif
