import BackgroundTasks
import Foundation
import OSLog

/// Which background task woke the app.
public enum BackgroundRefreshKind: String, Hashable, Sendable, CaseIterable {
    /// A short opportunistic poll of `/v1/sync` (`BGAppRefreshTask`).
    case appRefresh
    /// The longer nightly pass: sync, then cache housekeeping (`BGProcessingTask`).
    case nightly

    public var identifier: String {
        switch self {
        case .appRefresh: return BackgroundRefresh.appRefreshIdentifier
        case .nightly: return BackgroundRefresh.nightlyIdentifier
        }
    }
}

/// Registration and scheduling for the two background tasks declared in `Info.plist`.
///
/// **iOS decides whether these ever run.** The system weighs battery, network, and how often the
/// reader actually opens the app, and it is free to run a task hours late or never. So nothing in
/// Hardwood depends on one: a background run is a chance to have fresher numbers ready, and every
/// screen still loads correctly — from cache, then from the network — if no background task ever
/// fires. Scheduling is also one-shot per run, which is why each handler re-schedules before it
/// starts working.
public enum BackgroundRefresh {

    /// Both identifiers are declared under `BGTaskSchedulerPermittedIdentifiers` in `Info.plist`;
    /// registering one that is not declared traps at launch.
    public static let appRefreshIdentifier = "com.hardwood.nbastats.refresh"
    public static let nightlyIdentifier = "com.hardwood.nbastats.nightly"

    /// The earliest the system is asked to consider another refresh.
    public static let defaultRefreshInterval: TimeInterval = 15 * 60

    private static let logger = Logger(subsystem: "com.hardwood.nbastats", category: "background")

    @MainActor private static var isRegistered = false

    /// Registers both launch handlers. Call this from the app's initialiser, before the first
    /// scene connects: `BGTaskScheduler` requires registration to finish during launch.
    ///
    /// Registering twice for one identifier is fatal in `BGTaskScheduler`, so this is idempotent.
    @MainActor
    public static func register(handler: @escaping @Sendable (BackgroundRefreshKind) async -> Void) {
        guard !isRegistered else { return }
        isRegistered = true
        for kind in BackgroundRefreshKind.allCases {
            let didRegister = BGTaskScheduler.shared.register(forTaskWithIdentifier: kind.identifier, using: nil) { task in
                BackgroundRefresh.execute(task, kind: kind, handler: handler)
            }
            if !didRegister {
                logger.error("Could not register \(kind.identifier, privacy: .public); it may be missing from Info.plist.")
            }
        }
    }

    /// Asks the system for both kinds of run. Safe to call as often as the app likes.
    public static func scheduleAll() {
        scheduleAppRefresh()
        scheduleNightly()
    }

    public static func schedule(_ kind: BackgroundRefreshKind) {
        switch kind {
        case .appRefresh: scheduleAppRefresh()
        case .nightly: scheduleNightly()
        }
    }

    public static func scheduleAppRefresh(after interval: TimeInterval = BackgroundRefresh.defaultRefreshInterval) {
        let request = BGAppRefreshTaskRequest(identifier: appRefreshIdentifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: max(60, interval))
        submit(request)
    }

    /// The nightly pass wants a network but not a charger: it is a few small requests, not an
    /// index rebuild.
    public static func scheduleNightly(earliestBeginDate: Date? = nil) {
        let request = BGProcessingTaskRequest(identifier: nightlyIdentifier)
        request.requiresNetworkConnectivity = true
        request.requiresExternalPower = false
        request.earliestBeginDate = earliestBeginDate ?? nextNightlyDate()
        submit(request)
    }

    public static func cancelAll() {
        BGTaskScheduler.shared.cancelAllTaskRequests()
    }

    // MARK: - Internals

    /// Lets exactly one of the two completion paths report. The first `claim()` wins.
    private actor CompletionLatch {
        private var claimed = false
        func claim() -> Bool {
            if claimed { return false }
            claimed = true
            return true
        }
    }

    /// Runs one task to completion, or to expiry, and reports exactly once either way.
    private static func execute(_ task: BGTask,
                                kind: BackgroundRefreshKind,
                                handler: @escaping @Sendable (BackgroundRefreshKind) async -> Void) {
        // Re-schedule first: if the work below is killed part way through, a future opportunity
        // is already on the system's calendar.
        schedule(kind)

        let latch = CompletionLatch()
        let work = Task {
            await handler(kind)
        }
        // The system gives a few seconds' notice before it stops the app, and expects to be told
        // the task is over inside that window. Cancelling alone is not enough: a handler that
        // does not unwind in time — the nightly pass swallows cancellation and then purges the
        // cache, which is file removals plus an index write — gets the app *killed*, and a killed
        // background task costs every future opportunity. So report from whichever path arrives
        // first, and let the latch make sure that is exactly one of them.
        task.expirationHandler = {
            logger.notice("\(kind.identifier, privacy: .public) expired before it finished.")
            work.cancel()
            Task { @MainActor in
                if await latch.claim() { task.setTaskCompleted(success: false) }
            }
        }
        Task { @MainActor in
            await work.value
            if await latch.claim() { task.setTaskCompleted(success: !work.isCancelled) }
        }
    }

    private static func submit(_ request: BGTaskRequest) {
        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            // Submitting fails on the simulator and when the reader has switched Background App
            // Refresh off. Neither is worth bothering anyone about.
            logger.notice("Could not schedule \(request.identifier, privacy: .public): \(error.localizedDescription, privacy: .public)")
        }
    }

    /// The next 3:30 a.m. in the reader's own time zone — after the league's nightly corrections
    /// pass, and while the phone is most likely on a charger.
    private static func nextNightlyDate(after now: Date = Date(), calendar: Calendar = .current) -> Date {
        var components = DateComponents()
        components.hour = 3
        components.minute = 30
        return calendar.nextDate(after: now, matching: components, matchingPolicy: .nextTime)
            ?? now.addingTimeInterval(12 * 60 * 60)
    }
}
