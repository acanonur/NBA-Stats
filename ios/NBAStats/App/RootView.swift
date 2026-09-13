import Foundation
import SwiftUI

/// The three places Hardwood has: the board, the search field, and the settings.
public enum RootTab: String, Hashable, CaseIterable, Sendable {
    case dashboard
    case search
    case settings

    public var title: String {
        switch self {
        case .dashboard: return "Dashboard"
        case .search:    return "Search"
        case .settings:  return "Settings"
        }
    }

    public var icon: String {
        switch self {
        case .dashboard: return "square.grid.2x2"
        case .search:    return "magnifyingglass"
        case .settings:  return "gearshape"
        }
    }
}

/// The app's root: a tab view, the first-launch tour, and the one place the app notices it has
/// come back to the foreground.
public struct RootView: View {

    @EnvironmentObject private var environment: AppEnvironment

    public init() { }

    public var body: some View {
        RootTabView(environment: environment, store: environment.store)
    }
}

/// The tab view itself.
///
/// It observes the store as well as the environment, so the accent follows whichever dashboard is
/// selected: `AppEnvironment` publishes nothing when a *layout* changes, because the layouts live
/// in `DashboardStore`.
struct RootTabView: View {

    @ObservedObject private var environment: AppEnvironment
    @ObservedObject private var store: DashboardStore

    init(environment: AppEnvironment, store: DashboardStore) {
        _environment = ObservedObject(wrappedValue: environment)
        _store = ObservedObject(wrappedValue: store)
    }

    @Environment(\.scenePhase) private var scenePhase

    @State private var selection: RootTab = .dashboard
    @State private var isShowingOnboarding = false

    private var accent: AccentName { store.selectedLayout?.accent ?? .orange }

    var body: some View {
        TabView(selection: $selection) {
            DashboardScreen()
                .tabItem { Label(RootTab.dashboard.title, systemImage: RootTab.dashboard.icon) }
                .tag(RootTab.dashboard)

            SearchScreen()
                .tabItem { Label(RootTab.search.title, systemImage: RootTab.search.icon) }
                .tag(RootTab.search)

            SettingsScreen()
                .tabItem { Label(RootTab.settings.title, systemImage: RootTab.settings.icon) }
                .tag(RootTab.settings)
        }
        .tint(accent.color)
        .task {
            await environment.start()
        }
        .onAppear {
            if !environment.hasCompletedOnboarding {
                isShowingOnboarding = true
            }
        }
        .onChange(of: scenePhase) { _, phase in
            handleScenePhase(phase)
        }
        .sheet(isPresented: $isShowingOnboarding, onDismiss: finishOnboarding) {
            OnboardingSheet(onFinish: { isShowingOnboarding = false })
                .environmentObject(environment)
        }
    }

    /// Coming back to the foreground is the cheapest moment to notice a game has finished; going
    /// away is the moment to ask for the next background opportunity.
    private func handleScenePhase(_ phase: ScenePhase) {
        switch phase {
        case .active:
            Task { await environment.checkForUpdates(force: false) }
        case .background:
            environment.scheduleBackgroundRefresh()
        case .inactive:
            break
        @unknown default:
            break
        }
    }

    /// However the tour was left — finished or swiped away — it does not come back.
    private func finishOnboarding() {
        environment.hasCompletedOnboarding = true
    }
}

#if DEBUG
#Preview("Root") {
    RootView()
        .environmentObject(AppEnvironment.demo())
}
#endif
