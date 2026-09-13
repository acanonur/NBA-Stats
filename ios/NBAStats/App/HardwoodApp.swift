import BackgroundTasks
import Foundation
import SwiftUI

/// Hardwood's entry point.
///
/// Three things happen here and nowhere else: the app's composition is built (`AppEnvironment`),
/// the two background task handlers are registered — which `BGTaskScheduler` insists happens
/// during launch, before any scene connects — and `RootView` is put on screen.
///
/// Everything else, scene-phase handling included, is a screen's own business.
@main
struct HardwoodApp: App {

    @StateObject private var environment: AppEnvironment

    init() {
        let environment = AppEnvironment.live()
        _environment = StateObject(wrappedValue: environment)
        // `BGTaskScheduler.register(forTaskWithIdentifier:using:)` must be called before the app
        // finishes launching; `SyncService` owns the handlers and re-schedules after each run.
        environment.registerBackgroundTasks()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(environment)
                .environmentObject(environment.catalog)
        }
    }
}
