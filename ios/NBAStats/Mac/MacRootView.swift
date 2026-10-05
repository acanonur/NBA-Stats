#if os(macOS)
import AppKit
import Combine
import SwiftUI

// The main window: a sidebar on the left, the screen on the right, the league switch and Refresh
// in the toolbar, and the server banner above the screen.
//
// WHAT THIS VIEW OWNS
// - The league switch (a segmented control in the toolbar) and the Refresh button. Every other
//   control a screen needs (a club, a round, a date, a column set) lives in the screen's own
//   `MacFilterBar`, so the toolbar never has to guess which screen is showing.
// - The `.task` that bootstraps the app and then polls the server once a minute
//   (`MacAppModel.start()`), and the two notifications that tick early: waking from sleep and
//   coming back to the front.
// - The accent colour, which follows the selected dashboard exactly as it does on iOS.
// - The club list handed down the environment (`leagueClubs`), so the dashboard's widget settings
//   can offer a picker of EuroLeague clubs instead of a text field.
//
// WHY THE PUBLISHERS ARE THESE TWO
// Waking from sleep is announced on the workspace's own notification centre, not the default one:
// `NSWorkspace.didWakeNotification` posted to `NotificationCenter.default` is never delivered.
// Becoming active is an ordinary application notification on the default centre.
struct MacRootView: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel
    @EnvironmentObject private var store: DashboardStore

    private let wakePublisher = NSWorkspace.shared.notificationCenter
        .publisher(for: NSWorkspace.didWakeNotification)
    private let activePublisher = NotificationCenter.default
        .publisher(for: NSApplication.didBecomeActiveNotification)

    init() { }

    private var accent: AccentName {
        store.selectedLayout?.accent ?? AccentName.orange
    }

    var body: some View {
        NavigationSplitView {
            MacSidebar()
        } detail: {
            MacDetailRouter()
                .safeAreaInset(edge: .top, spacing: 0) {
                    MacBackendBanner()
                }
                .toolbar {
                    rootToolbar
                }
        }
        .frame(minWidth: 1100, minHeight: 700)
        .environment(\.leagueClubs, model.elClubs)
        .tint(accent.color)
        .task {
            await model.start()
        }
        .onReceive(wakePublisher) { _ in
            model.refreshAfterWake()
        }
        .onReceive(activePublisher) { _ in
            model.refreshOnActivate()
        }
        .onChange(of: store.selectedLayoutID) { _, newValue in
            model.storeSelectionChanged(newValue)
        }
        .onChange(of: environment.isDemoMode) { _, newValue in
            model.demoModeChanged(newValue)
        }
    }

    // MARK: Toolbar

    private var isDashboardSelected: Bool {
        guard let current = model.selection else { return false }
        switch current {
        case .dashboard:
            return true
        case .screen:
            return false
        }
    }

    @ToolbarContentBuilder private var rootToolbar: some ToolbarContent {
        ToolbarItem(placement: .principal) {
            leagueSwitch
        }
        ToolbarItem(placement: .primaryAction) {
            refreshButton
        }
    }

    /// Hidden while a dashboard is selected: a dashboard picks its leagues widget by widget.
    @ViewBuilder private var leagueSwitch: some View {
        if !isDashboardSelected {
            Picker("League", selection: $model.league) {
                ForEach(LeagueKey.allCases) { option in
                    Text(option.displayName).tag(option)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 220)
        }
    }

    private var refreshButton: some View {
        Button {
            model.refreshNow()
        } label: {
            Label("Refresh", systemImage: "arrow.clockwise")
        }
        .help("Refresh (⌘R)")
    }
}
#endif
