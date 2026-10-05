import Foundation
import SwiftUI

/// Hardwood's entry point.
///
/// Three things happen here and nowhere else: the app's composition is built (`AppEnvironment`),
/// the two background task handlers are registered — which `BGTaskScheduler` insists happens
/// during launch, before any scene connects — and the first screen is put on screen.
///
/// On iOS that screen is `RootView`. On the Mac it is `MacScenes`: one main window with a sidebar,
/// two read-only detail windows and a Settings window, all driven by one `MacAppModel`. The
/// background task registration is a no-op on the Mac (there is no BGTaskScheduler); the Mac app
/// refreshes in the foreground instead.
///
/// Everything else, scene-phase handling included, is a screen's own business.
@main
struct HardwoodApp: App {

    @StateObject private var environment: AppEnvironment
    #if os(macOS)
    @StateObject private var macModel: MacAppModel
    #endif

    init() {
        let environment = AppEnvironment.live()
        _environment = StateObject(wrappedValue: environment)
        #if os(macOS)
        let model = MacAppModel(environment: environment)
        _macModel = StateObject(wrappedValue: model)
        #endif
        // `BGTaskScheduler.register(forTaskWithIdentifier:using:)` must be called before the app
        // finishes launching; `SyncService` owns the handlers and re-schedules after each run.
        environment.registerBackgroundTasks()
    }

    var body: some Scene {
        #if os(macOS)
        MacScenes(environment: environment, model: macModel)
        #else
        WindowGroup {
            RootView()
                .environmentObject(environment)
                .environmentObject(environment.catalog)
        }
        #endif
    }
}
