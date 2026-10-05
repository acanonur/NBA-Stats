#if os(macOS)
import SwiftUI

// The Mac app's scenes: one main window, two read-only detail windows, and the Settings window.
//
// WHY `Window`, NOT `WindowGroup`, FOR THE MAIN WINDOW
// `DashboardService` holds one layout and one generation counter, and `DashboardStore.isEditing`
// is app-wide, so two main windows would fight over the same state. A `Window` scene is single
// instance by construction. Closing it leaves the app running; Window > Hardwood opens it again.
//
// WHY EVERY SCENE ROOT IS HANDED ITS OWN ENVIRONMENT OBJECTS
// A scene does not inherit environment objects from another scene. A view that reads
// `@EnvironmentObject` and finds nothing there crashes the first time it is drawn, so each root
// below is given all four (the environment, the catalog, the store and the Mac model), whether or
// not the screens inside happen to read each of them today. The detail windows are read-only: they
// call `environment.league` and never `DashboardService`. They get the store only so that a view
// they embed later cannot crash on a missing one.
// `scripts/check_swift_portability.py` rule P11 fails when a root loses one of these.

/// The identifiers `openWindow(id:value:)` takes.
enum MacWindowID {
    static let main = "main"
    static let boxScore = "boxscore"
    static let club = "club"
}

struct MacScenes: Scene {
    let environment: AppEnvironment
    let model: MacAppModel

    init(environment: AppEnvironment, model: MacAppModel) {
        self.environment = environment
        self.model = model
    }

    var body: some Scene {
        Window("Hardwood", id: MacWindowID.main) {
            MacRootView()
                .environmentObject(environment)
                .environmentObject(environment.catalog)
                .environmentObject(environment.store)
                .environmentObject(model)
        }
        .defaultSize(width: 1320, height: 860)
        .windowResizability(.contentMinSize)
        .commands {
            MacCommands(model: model, store: environment.store)
        }

        WindowGroup("Box Score", id: MacWindowID.boxScore, for: LeagueLink.self) { $link in
            MacBoxScoreWindow(link: link)
                .environmentObject(environment)
                .environmentObject(environment.catalog)
                .environmentObject(environment.store)
                .environmentObject(model)
        }
        .defaultSize(width: 960, height: 680)

        WindowGroup("Club", id: MacWindowID.club, for: LeagueLink.self) { $link in
            MacClubWindow(link: link)
                .environmentObject(environment)
                .environmentObject(environment.catalog)
                .environmentObject(environment.store)
                .environmentObject(model)
        }
        .defaultSize(width: 1000, height: 720)

        Settings {
            MacSettingsView()
                .environmentObject(environment)
                .environmentObject(environment.catalog)
                .environmentObject(environment.store)
                .environmentObject(model)
        }
    }
}
#endif
