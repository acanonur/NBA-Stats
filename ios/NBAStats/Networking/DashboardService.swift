import Combine
import Foundation
import OSLog

/// The context a dashboard is resolved in — the reader's favourites and clock.
///
/// The wire type is `ResolveContextPayload` (`Core/Models.swift`); this name is what the rest of
/// the app calls it, so a screen never has to think about the request body.
public typealias ResolveContext = ResolveContextPayload

/// What one tile is doing right now.
public enum WidgetState: Hashable, Sendable {
    case loading
    /// `stale` is true while the payload on screen came from the cache and a refresh is still out.
    case loaded(WidgetPayload, availability: MetricAvailability, notes: [String], stale: Bool)
    case failed(APIError)
    /// The stat did not exist in the era asked about (`contracts/CONTRACT.md` §6).
    case unavailable(reason: String)

    public var payload: WidgetPayload? {
        if case .loaded(let payload, _, _, _) = self { return payload }
        return nil
    }

    public var availability: MetricAvailability? {
        switch self {
        case .loaded(_, let availability, _, _): return availability
        case .unavailable: return MetricAvailability.unavailable
        case .loading, .failed: return nil
        }
    }

    public var notes: [String] {
        if case .loaded(_, _, let notes, _) = self { return notes }
        return []
    }

    public var isStale: Bool {
        if case .loaded(_, _, _, let stale) = self { return stale }
        return false
    }

    public var isLoading: Bool {
        if case .loading = self { return true }
        return false
    }

    public var error: APIError? {
        if case .failed(let error) = self { return error }
        return nil
    }

    /// The same state with its staleness flag flipped; every other case is returned untouched.
    public func markedStale(_ stale: Bool) -> WidgetState {
        guard case .loaded(let payload, let availability, let notes, _) = self else { return self }
        return .loaded(payload, availability: availability, notes: notes, stale: stale)
    }
}

/// One chunk of a split resolve, carried out to a child task and back.
private struct ResolveChunk: Sendable {
    let widgets: [DashboardWidget]
    let request: DashboardResolveRequest
}

private struct ResolveChunkOutcome: Sendable {
    let widgets: [DashboardWidget]
    let outcome: Result<DashboardResolveResponse, APIError>
    /// The chunk was called off — a superseded refresh, a screen that went away. Never a
    /// failure the reader should see, so these outcomes are dropped rather than painted on.
    var cancelled: Bool = false
}

/// What one cached tile is written as: the payload plus the labels the tile needs to redraw
/// itself without another round trip.
private struct CachedTileEnvelope: Encodable {
    let kind: String
    let availability: String
    let notes: [String]
    let generatedAt: String?
    let payload: WidgetPayload
}

/// The read side of `CachedTileEnvelope`. The payload comes back as `JSONValue` because a
/// `WidgetPayload` can only be decoded once its kind is known, and the kind is the caller's.
private struct StoredTileEnvelope: Decodable {
    let kind: String?
    let availability: String?
    let notes: [String]?
    let generatedAt: String?
    let payload: JSONValue
}

/// Loads a dashboard: cache first, network second, one tile at a time never.
///
/// The shape is stale-while-revalidate. Cached payloads are published the moment the screen
/// appears — marked stale, so the chrome can show a dot — and one `POST /v1/dashboard/resolve`
/// carries the whole layout. A layout with more than the contract's 24 widgets is split into as
/// few requests as that allows, and a request that fails leaves the cached tiles standing.
@MainActor
public final class DashboardService: ObservableObject {

    /// Widget states keyed by widget id.
    @Published public private(set) var results: [String: WidgetState] = [:]

    /// The cache key each published payload was built for, keyed by widget id.
    ///
    /// A tile keeps its id when the reader reconfigures it, so the id alone cannot say whether
    /// the numbers on screen belong to the config now being shown. `unchanged` is a claim about
    /// the sync version and nothing else, so without this a reconfigured tile would go on
    /// showing the previous metric's numbers for as long as no game finalized.
    private var resultKeys: [String: String] = [:]
    @Published public private(set) var lastUpdated: Date?
    @Published public private(set) var dataThrough: String?
    @Published public private(set) var isRefreshing: Bool = false
    /// The last whole-request failure, for the banner above the grid. Per-widget failures live in
    /// `results` instead.
    @Published public private(set) var lastError: APIError?

    /// The sync version the client believes the server is on; sent as `knownSyncVersion` so the
    /// server can answer `unchanged` instead of re-sending payloads that have not moved.
    public private(set) var knownSyncVersion: Int?

    private let client: APIClientProtocol
    private let cache: DiskCache
    private let catalog: Catalog
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    private var layout: DashboardLayout?
    private var context: ResolveContext = ResolveContext()
    /// Bumped on every load so a slow response cannot overwrite a newer one.
    private var generation = 0

    private static let logger = Logger(subsystem: "com.hardwood.nbastats", category: "dashboard")

    public init(client: APIClientProtocol, cache: DiskCache, catalog: Catalog) {
        self.client = client
        self.cache = cache
        self.catalog = catalog
        self.encoder = APIClient.makeEncoder()
        self.decoder = APIClient.makeDecoder()
    }

    // MARK: - Loading

    /// Returns cached payloads immediately, then refreshes — stale-while-revalidate.
    public func load(layout: DashboardLayout, context: ResolveContext, force: Bool) async {
        self.layout = layout
        self.context = context
        generation += 1
        let thisGeneration = generation

        // A widget that is no longer in the layout keeps no state.
        let liveIDs = Set(layout.widgets.map { $0.id })
        results = results.filter { liveIDs.contains($0.key) }
        resultKeys = resultKeys.filter { liveIDs.contains($0.key) }

        guard !layout.widgets.isEmpty else {
            isRefreshing = false
            return
        }

        // 1. Whatever is on disk goes on screen now, marked stale.
        var published = results
        var everythingIsFresh = true
        for widget in layout.widgets {
            let key = cacheKey(for: widget)
            // Whatever is already in `results` was fetched under the config it carried then. A
            // reconfigured tile keeps its id but changes its key, and those old numbers must
            // not stand in for the new config.
            if resultKeys[widget.id] != key {
                published[widget.id] = nil
                resultKeys[widget.id] = nil
            }
            if let entry = await cache.entry(forKey: key), let state = cachedState(from: entry, widget: widget) {
                published[widget.id] = state
                resultKeys[widget.id] = key
                if entry.isExpired() { everythingIsFresh = false }
            } else {
                everythingIsFresh = false
                if published[widget.id] == nil { published[widget.id] = .loading }
            }
        }
        guard thisGeneration == generation else { return }
        results = published

        // 2. Inside its TTL and nobody asked for fresh: the cache *is* the answer.
        if !force && everythingIsFresh {
            for id in Array(results.keys) {
                if let state = results[id] { results[id] = state.markedStale(false) }
            }
            isRefreshing = false
            return
        }

        // 3. One request for the whole layout, split only when the contract's cap forces it.
        isRefreshing = true
        // Only the newest load owns the spinner; an older one finishing late must not clear it.
        defer { if thisGeneration == generation { isRefreshing = false } }

        let pending = await resolve(widgets: layout.widgets,
                                    layoutID: layout.id,
                                    knownSyncVersion: force ? nil : knownSyncVersion,
                                    generation: thisGeneration)
        guard thisGeneration == generation else { return }

        // A widget the server called `unchanged` that this device has no copy of has to be asked
        // for again, this time without a sync version to compare against.
        if !pending.isEmpty {
            let retryWidgets = layout.widgets.filter { pending.contains($0.id) }
            let stillPending = await resolve(widgets: retryWidgets,
                                             layoutID: layout.id,
                                             knownSyncVersion: nil,
                                             generation: thisGeneration)
            guard thisGeneration == generation else { return }
            for id in stillPending {
                results[id] = .failed(.server(
                    code: "unchanged_without_cache",
                    message: "Your stats server says this widget has not changed, but Hardwood has no saved copy of it.",
                    status: 200,
                    recoverable: true
                ))
            }
        }
    }

    /// Re-resolves a single widget, for the "Try again" button inside a failed tile.
    public func retry(widgetID: String) async {
        guard let layout = layout, let widget = layout.widget(id: widgetID) else { return }
        // Kept so a failed retry can put the reader's numbers back rather than blanking the tile.
        let previous = results[widgetID]
        results[widgetID] = .loading
        // No `knownSyncVersion`: a retry is a request for the payload itself, not for a verdict
        // on whether it changed.
        let request = DashboardResolveRequest(layoutId: layout.id,
                                              context: context,
                                              knownSyncVersion: nil,
                                              widgets: [resolveRequest(for: widget)])
        do {
            let response = try await client.resolve(request)
            absorb(response)
            if let result = response.resultsByWidgetID[widgetID] {
                if apply(result, widget: widget) {
                    // `unchanged` with nothing cached: restore what was on screen, or say so.
                    let fallback = WidgetState.failed(.server(
                        code: "unchanged_without_cache",
                        message: "Your stats server says this widget has not changed, but Hardwood has no saved copy of it.",
                        status: 200,
                        recoverable: true
                    ))
                    results[widgetID] = previous?.markedStale(true) ?? fallback
                }
            } else {
                results[widgetID] = .failed(.server(code: "missing_result",
                                                    message: "Your stats server did not answer for this widget.",
                                                    status: 200,
                                                    recoverable: true))
            }
            lastUpdated = Date()
            lastError = nil
        } catch {
            guard !APIError.isCancellation(error) else {
                results[widgetID] = previous ?? WidgetState.loading
                return
            }
            let apiError = APIError.from(error)
            lastError = apiError
            if let previous = previous, previous.payload != nil {
                results[widgetID] = previous.markedStale(true)
            } else {
                results[widgetID] = .failed(apiError)
            }
        }
    }

    /// Drops the cached payloads for these kinds and marks the tiles showing them stale — what a
    /// `/v1/sync` response's `invalidate` list asks for.
    public func invalidate(kinds: [WidgetKind]) async {
        guard !kinds.isEmpty else { return }
        await cache.purgeKinds(kinds)
        guard let layout = layout else { return }
        let affected = layout.widgets.filter { kinds.contains($0.kind) }.map { $0.id }
        for id in affected {
            if let state = results[id] { results[id] = state.markedStale(true) }
        }
    }

    /// Called by `SyncService` so the next resolve can ask for "only what changed".
    public func updateKnownSyncVersion(_ version: Int?) {
        knownSyncVersion = version
    }

    /// Forgets every published state, for a sign-out or a layout switch that should not flash the
    /// previous dashboard's numbers.
    public func reset() {
        results = [:]
        lastUpdated = nil
        lastError = nil
        layout = nil
    }

    // MARK: - Resolving

    /// Sends every chunk, applies every result, and returns the widget ids that came back
    /// `unchanged` with nothing cached to keep.
    private func resolve(widgets: [DashboardWidget],
                         layoutID: String,
                         knownSyncVersion: Int?,
                         generation thisGeneration: Int) async -> Set<String> {
        let chunks = widgets.hardwoodChunked(into: DashboardResolveRequest.maxWidgets).map { group in
            ResolveChunk(widgets: group,
                         request: DashboardResolveRequest(layoutId: layoutID,
                                                          context: context,
                                                          knownSyncVersion: knownSyncVersion,
                                                          widgets: group.map { resolveRequest(for: $0) }))
        }
        guard !chunks.isEmpty else { return [] }

        let outcomes = await send(chunks)
        guard thisGeneration == generation else { return [] }

        var pending: Set<String> = []
        var sawSuccess = false
        for outcome in outcomes where !outcome.cancelled {
            switch outcome.outcome {
            case .success(let response):
                sawSuccess = true
                absorb(response)
                let byID = response.resultsByWidgetID
                for widget in outcome.widgets {
                    guard let result = byID[widget.id] else {
                        markMissing(widget)
                        continue
                    }
                    if apply(result, widget: widget) { pending.insert(widget.id) }
                }
            case .failure(let error):
                lastError = error
                DashboardService.logger.error("Resolve failed: \(error.userMessage, privacy: .public)")
                for widget in outcome.widgets { markFailed(widget, error: error) }
            }
        }
        if sawSuccess {
            lastUpdated = Date()
            if outcomes.allSatisfy({ outcome in
                if outcome.cancelled { return true }
                if case .success = outcome.outcome { return true }
                return false
            }) {
                lastError = nil
            }
        }
        return pending
    }

    private func send(_ chunks: [ResolveChunk]) async -> [ResolveChunkOutcome] {
        let client = self.client
        return await withTaskGroup(of: ResolveChunkOutcome.self) { group in
            for chunk in chunks {
                group.addTask {
                    do {
                        let response = try await client.resolve(chunk.request)
                        return ResolveChunkOutcome(widgets: chunk.widgets, outcome: .success(response))
                    } catch {
                        // Every other network call site in the app guards on this first; without
                        // it a cancelled resolve becomes a bogus `network_error` and is painted
                        // onto every tile in the chunk.
                        if APIError.isCancellation(error) {
                            return ResolveChunkOutcome(widgets: chunk.widgets,
                                                       outcome: .failure(.offline),
                                                       cancelled: true)
                        }
                        return ResolveChunkOutcome(widgets: chunk.widgets,
                                                   outcome: .failure(APIError.from(error)))
                    }
                }
            }
            var collected: [ResolveChunkOutcome] = []
            for await outcome in group { collected.append(outcome) }
            return collected
        }
    }

    private func absorb(_ response: DashboardResolveResponse) {
        if let version = response.syncVersion { knownSyncVersion = version }
        if let through = response.dataThrough, !through.isEmpty { dataThrough = through }
    }

    /// Applies one result to one tile. Returns true when the tile is still waiting because the
    /// server answered `unchanged` and there was nothing cached to keep.
    @discardableResult
    private func apply(_ result: ResolveResult, widget: DashboardWidget) -> Bool {
        // The era rule comes first: a stat that did not exist is never an error, and never a zero.
        if result.availability == .unavailable {
            results[widget.id] = .unavailable(reason: unavailableReason(for: result))
            return false
        }

        switch result.effectiveStatus {
        case .ok, .partial:
            guard let payload = result.payload else {
                results[widget.id] = .failed(.decoding(result.failureMessage ?? "This widget arrived with no data."))
                return false
            }
            // Never default *upward*: a result the server called `partial` is qualified even
            // when it did not spell out how, and `.full` is the one answer that would render it
            // as fully trustworthy — no caret, no footnote (ARCHITECTURE.md §3 rule 1).
            let availability = result.availability ?? (result.effectiveStatus == .partial ? .partial : .full)
            results[widget.id] = .loaded(payload, availability: availability, notes: result.notes, stale: false)
            resultKeys[widget.id] = cacheKey(for: widget)
            persist(payload: payload, result: result, widget: widget)
            return false

        case .unchanged:
            // The cached copy is still the truth; it just stopped being stale — but only when it
            // was built for the config this tile shows now. The server compared sync versions,
            // not configs, so a tile the reader just reconfigured is still waiting and the
            // caller re-asks for it without a `knownSyncVersion`.
            if let existing = results[widget.id], existing.payload != nil,
               resultKeys[widget.id] == cacheKey(for: widget) {
                results[widget.id] = existing.markedStale(false)
                return false
            }
            return true

        case .error:
            let error: APIError
            if let body = result.error {
                error = APIError(body: body, status: 200)
            } else {
                error = .decoding(result.failureMessage ?? "This widget could not be loaded.")
            }
            if error.contractCode == .metricUnavailable {
                results[widget.id] = .unavailable(reason: error.userMessage)
            } else {
                results[widget.id] = .failed(error)
            }
            return false
        }
    }

    private func markMissing(_ widget: DashboardWidget) {
        if let state = results[widget.id], state.payload != nil {
            results[widget.id] = state.markedStale(true)
            return
        }
        results[widget.id] = .failed(.server(code: "missing_result",
                                             message: "Your stats server did not answer for this widget.",
                                             status: 200,
                                             recoverable: true))
    }

    /// A whole-request failure never blanks a tile that already has numbers on it.
    private func markFailed(_ widget: DashboardWidget, error: APIError) {
        if let state = results[widget.id], state.payload != nil {
            results[widget.id] = state.markedStale(true)
            return
        }
        results[widget.id] = .failed(error)
    }

    private func unavailableReason(for result: ResolveResult) -> String {
        if let message = result.error?.message, !message.isEmpty { return message }
        if let note = result.notes.first, !note.isEmpty { return note }
        return MetricAvailability.unavailable.explanation
    }

    private func resolveRequest(for widget: DashboardWidget) -> ResolveWidgetRequest {
        ResolveWidgetRequest(id: widget.id,
                             kind: widget.kind,
                             size: widget.size,
                             config: catalog.normalizedConfig(for: widget.kind, config: widget.config))
    }

    // MARK: - Cache

    private func cacheKey(for widget: DashboardWidget) -> String {
        DiskCache.key(kind: widget.kind,
                      config: catalog.normalizedConfig(for: widget.kind, config: widget.config),
                      context: context)
    }

    private func persist(payload: WidgetPayload, result: ResolveResult, widget: DashboardWidget) {
        let envelope = CachedTileEnvelope(kind: widget.kind.rawValue,
                                          availability: (result.availability ?? .full).rawValue,
                                          notes: result.notes,
                                          generatedAt: result.generatedAt,
                                          payload: payload)
        guard let data = try? encoder.encode(envelope) else { return }
        let key = cacheKey(for: widget)
        let kind = widget.kind
        let ttl = result.ttlSeconds
        let cache = self.cache
        // Writing is fire-and-forget: the screen has the payload already and must not wait on
        // the disk to draw it.
        Task.detached(priority: .utility) {
            await cache.store(data, forKey: key, kind: kind, ttlSeconds: ttl)
        }
    }

    private func cachedState(from entry: DiskCache.CachedEntry, widget: DashboardWidget) -> WidgetState? {
        guard let envelope = try? decoder.decode(StoredTileEnvelope.self, from: entry.data) else { return nil }
        guard let payloadData = try? encoder.encode(envelope.payload) else { return nil }
        guard let payload = try? WidgetPayload.decode(kind: widget.kind, from: payloadData, using: decoder) else { return nil }
        let availability = envelope.availability.flatMap { MetricAvailability(rawValue: $0) } ?? .full
        return .loaded(payload, availability: availability, notes: envelope.notes ?? [], stale: true)
    }
}

extension Array {
    /// Splits into runs of at most `size`, which is how a layout larger than the contract's
    /// 24-widget cap becomes two requests instead of one rejection.
    func hardwoodChunked(into size: Int) -> [[Element]] {
        guard size > 0, count > size else { return isEmpty ? [] : [Array(self)] }
        var chunks: [[Element]] = []
        var index = 0
        while index < count {
            let end = Swift.min(index + size, count)
            chunks.append(Array(self[index..<end]))
            index = end
        }
        return chunks
    }
}
