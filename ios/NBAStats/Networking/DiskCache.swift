import CryptoKit
import Foundation
import OSLog

extension WidgetKind {
    /// How long a resolved payload stays good when the server does not say
    /// (`contracts/CONTRACT.md` §8): a minute for anything tied to tonight's games, five for a
    /// player's season, ten for leaderboards and team tables, an hour for a finished career.
    public var defaultCacheTTLSeconds: Int {
        switch self {
        case .scoreboard, .dailyMovers:
            return 60
        case .statTile, .playerSnapshot, .gameLog, .trendChart, .shotProfile, .comparison:
            return 300
        case .leaderboard, .teamEfficiency, .fourFactors, .nextGameProjection, .projectionBoard:
            return 600
        case .careerArc:
            return 3600
        }
    }
}

/// The on-disk half of stale-while-revalidate.
///
/// Entries are keyed by a SHA-256 of widget kind + normalized configuration + resolved context,
/// so two widgets asking the same question share one entry and a change to either the question
/// or the reader's context misses cleanly. Expired entries are *kept*: the dashboard shows them
/// with a staleness dot while the network answers. Nothing here throws out of a read — a file
/// that has gone missing or been truncated is simply a miss.
public actor DiskCache {

    /// One stored payload plus the freshness the reader needs to label it.
    public struct CachedEntry: Sendable {
        public let data: Data
        public let kind: WidgetKind?
        public let storedAt: Date
        public let expiresAt: Date

        public init(data: Data, kind: WidgetKind?, storedAt: Date, expiresAt: Date) {
            self.data = data
            self.kind = kind
            self.storedAt = storedAt
            self.expiresAt = expiresAt
        }

        public func isExpired(now: Date = Date()) -> Bool { now >= expiresAt }
        public func age(now: Date = Date()) -> TimeInterval { now.timeIntervalSince(storedAt) }
    }

    /// What the Settings screen shows next to "Clear cache".
    public struct Statistics: Hashable, Sendable {
        public let entryCount: Int
        public let byteCount: Int
        public let byteLimit: Int

        public init(entryCount: Int, byteCount: Int, byteLimit: Int) {
            self.entryCount = entryCount
            self.byteCount = byteCount
            self.byteLimit = byteLimit
        }
    }

    /// 25 MB: hundreds of payloads, a rounding error next to a photo library.
    public static let defaultByteLimit = 25 * 1024 * 1024

    /// However generous its TTL, nothing older than a week is served: a stale dashboard is
    /// useful, a dashboard from last season is not.
    public static let maximumEntryAge: TimeInterval = 7 * 24 * 60 * 60

    private struct Record: Codable {
        let key: String
        let kind: String
        var storedAt: Date
        var expiresAt: Date
        var lastAccess: Date
        var byteCount: Int
    }

    private struct IndexFile: Codable {
        var records: [String: Record]
    }

    private let directory: URL
    private let byteLimit: Int
    private let fileManager: FileManager
    private let indexURL: URL

    private var records: [String: Record] = [:]
    private var totalBytes = 0
    private var isLoaded = false

    /// The in-memory index has changes the file on disk does not.
    private var indexIsDirty = false
    /// The pending coalesced index write, if one is scheduled.
    private var indexFlushTask: Task<Void, Never>?

    private static let logger = Logger(subsystem: "com.hardwood.nbastats", category: "cache")

    /// Defaults to `Caches/Hardwood/Widgets`, which iOS may reclaim under storage pressure —
    /// exactly the right promise for data the app can always fetch again.
    public init(directory: URL? = nil,
                byteLimit: Int = DiskCache.defaultByteLimit,
                fileManager: FileManager = .default) {
        let base = directory
            ?? fileManager.urls(for: .cachesDirectory, in: .userDomainMask).first?
                .appendingPathComponent("Hardwood", isDirectory: true)
                .appendingPathComponent("Widgets", isDirectory: true)
            ?? URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
                .appendingPathComponent("HardwoodWidgets", isDirectory: true)
        self.directory = base
        self.byteLimit = byteLimit > 0 ? byteLimit : DiskCache.defaultByteLimit
        self.fileManager = fileManager
        self.indexURL = base.appendingPathComponent("index.json", isDirectory: false)
    }

    // MARK: - Keys

    /// The cache key for one widget: kind + normalized config + the context it was resolved in.
    ///
    /// `JSONValue.description` sorts object keys, so two configurations that differ only in
    /// dictionary ordering hash the same.
    public static func key(kind: WidgetKind,
                           config: [String: JSONValue],
                           context: ResolveContextPayload) -> String {
        let canonical = [
            kind.rawValue,
            JSONValue.object(config).description,
            contextFingerprint(context)
        ].joined(separator: "\u{1F}")
        return digest(of: canonical)
    }

    /// The parts of the context that can change what a widget resolves to. `timeZone` is in
    /// there because "latest" means a different night's games in Honolulu than in Boston.
    public static func contextFingerprint(_ context: ResolveContextPayload) -> String {
        [
            context.favoritePlayerId.map { String($0) } ?? "-",
            context.favoriteTeamId.map { String($0) } ?? "-",
            context.timeZone ?? "-",
            context.asOf ?? "-",
            context.season ?? "-"
        ].joined(separator: "|")
    }

    /// Hex SHA-256, used for the key itself and again for the file name.
    public static func digest(of string: String) -> String {
        let hashed = SHA256.hash(data: Data(string.utf8))
        return hashed.map { byte in String(format: "%02x", byte) }.joined()
    }

    // MARK: - Reading

    /// The entry for a key, expired or not, or `nil` for a miss. Never throws.
    public func entry(forKey key: String) -> CachedEntry? {
        load()
        guard var record = records[key] else { return nil }
        let now = Date()
        guard now.timeIntervalSince(record.storedAt) <= DiskCache.maximumEntryAge else {
            discard(key: key)
            return nil
        }
        guard let data = try? Data(contentsOf: fileURL(for: key)), !data.isEmpty else {
            // The file is gone or unreadable: treat it as a miss and forget the record, rather
            // than leaving the index claiming bytes that are not there.
            discard(key: key)
            return nil
        }
        record.lastAccess = now
        records[key] = record
        // `lastAccess` is the only thing `evictIfNeeded` sorts on, and the read path is the only
        // thing that moves it. Left in memory it never reaches `index.json`, so after a relaunch
        // every record carries the timestamp it was *written* with and LRU degrades to eviction
        // in write order — the most-read tiles go first. The write itself is coalesced, so a read
        // still costs no disk I/O of its own.
        scheduleIndexFlush()
        return CachedEntry(data: data,
                           kind: WidgetKind(rawValue: record.kind),
                           storedAt: record.storedAt,
                           expiresAt: record.expiresAt)
    }

    /// True when a usable, unexpired entry exists.
    public func hasFreshEntry(forKey key: String) -> Bool {
        guard let entry = entry(forKey: key) else { return false }
        return !entry.isExpired()
    }

    // MARK: - Writing

    /// Stores a payload under a per-entry TTL, then evicts if the cache is over its cap.
    public func store(_ data: Data, forKey key: String, kind: WidgetKind, ttlSeconds: Int?) {
        load()
        guard !data.isEmpty else { return }
        // A single entry larger than a fifth of the cap would evict everything else on the next
        // write; refusing it keeps the cache useful.
        guard data.count <= byteLimit / 5 else { return }

        let now = Date()
        let ttl = max(1, ttlSeconds ?? kind.defaultCacheTTLSeconds)
        let url = fileURL(for: key)
        do {
            try createDirectoryIfNeeded()
            try data.write(to: url, options: .atomic)
        } catch {
            DiskCache.logger.error("Cache write failed: \(error.localizedDescription, privacy: .public)")
            return
        }
        if let previous = records[key] {
            totalBytes -= previous.byteCount
        }
        let record = Record(key: key,
                            kind: kind.rawValue,
                            storedAt: now,
                            expiresAt: now.addingTimeInterval(TimeInterval(ttl)),
                            lastAccess: now,
                            byteCount: data.count)
        records[key] = record
        totalBytes += data.count
        evictIfNeeded()
        // One dashboard load stores up to 24 payloads, and rewriting the whole index after each
        // one means 24 full atomic rewrites of a file that only needs to be right once the burst
        // is over. Deletions still flush immediately — see `flushIndexNow`.
        scheduleIndexFlush()
    }

    // MARK: - Invalidation

    /// Drops every entry of these kinds, which is what `SyncResponse.invalidate` asks for.
    public func purgeKinds(_ kinds: [WidgetKind]) {
        guard !kinds.isEmpty else { return }
        load()
        let raw = Set(kinds.map { $0.rawValue })
        let doomed = records.values.filter { raw.contains($0.kind) }.map { $0.key }
        guard !doomed.isEmpty else { return }
        for key in doomed { discard(key: key) }
        flushIndexNow()
    }

    public func remove(forKey key: String) {
        load()
        discard(key: key)
        flushIndexNow()
    }

    /// Drops entries that are past their TTL, for a housekeeping pass in the nightly task.
    public func purgeExpired() {
        load()
        let now = Date()
        let doomed = records.values.filter { $0.expiresAt <= now }.map { $0.key }
        for key in doomed { discard(key: key) }
        removeOrphanedFiles()
        if !doomed.isEmpty || indexIsDirty { flushIndexNow() }
    }

    public func removeAll() {
        load()
        for key in records.keys {
            try? fileManager.removeItem(at: fileURL(for: key))
        }
        records = [:]
        totalBytes = 0
        flushIndexNow()
    }

    /// Writes the index now if a coalesced change is still pending.
    public func flushPendingIndex() {
        guard indexIsDirty else { return }
        flushIndexNow()
    }

    public func statistics() -> Statistics {
        load()
        // A natural coalescing point: the caller is already waiting on the actor.
        if indexIsDirty { flushIndexNow() }
        return Statistics(entryCount: records.count, byteCount: totalBytes, byteLimit: byteLimit)
    }

    // MARK: - Internals

    private func fileURL(for key: String) -> URL {
        // The key is hashed again so that a caller-supplied key can never escape the directory.
        directory.appendingPathComponent(DiskCache.digest(of: key) + ".payload", isDirectory: false)
    }

    private func createDirectoryIfNeeded() throws {
        guard !fileManager.fileExists(atPath: directory.path) else { return }
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    private func discard(key: String) {
        if let record = records.removeValue(forKey: key) {
            totalBytes -= record.byteCount
            if totalBytes < 0 { totalBytes = 0 }
        }
        try? fileManager.removeItem(at: fileURL(for: key))
    }

    /// Least-recently-used eviction, with anything already expired thrown overboard first.
    private func evictIfNeeded() {
        guard totalBytes > byteLimit else { return }
        let target = (byteLimit * 9) / 10
        let now = Date()
        let ordered = records.values.sorted { first, second in
            let firstExpired = first.expiresAt <= now
            let secondExpired = second.expiresAt <= now
            if firstExpired != secondExpired { return firstExpired }
            return first.lastAccess < second.lastAccess
        }
        for record in ordered {
            guard totalBytes > target else { break }
            discard(key: record.key)
        }
    }

    /// Reads the index once per process. A corrupt index costs the cache its bookkeeping, not
    /// the app its launch: the directory is wiped and the cache starts empty.
    private func load() {
        guard !isLoaded else { return }
        isLoaded = true
        guard let data = try? Data(contentsOf: indexURL) else { return }
        guard let index = try? JSONDecoder().decode(IndexFile.self, from: data) else {
            DiskCache.logger.notice("Cache index unreadable; starting from empty.")
            try? fileManager.removeItem(at: directory)
            return
        }
        records = index.records
        totalBytes = index.records.values.reduce(0) { $0 + $1.byteCount }
    }

    /// Marks the index dirty and makes sure exactly one write is queued behind it.
    private func scheduleIndexFlush() {
        indexIsDirty = true
        guard indexFlushTask == nil else { return }
        indexFlushTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 500_000_000)
            if Task.isCancelled { return }
            await self?.performScheduledFlush()
        }
    }

    private func performScheduledFlush() {
        indexFlushTask = nil
        if indexIsDirty { flushIndexNow() }
    }

    /// Writes the index immediately and drops any pending coalesced write.
    private func flushIndexNow() {
        indexFlushTask?.cancel()
        indexFlushTask = nil
        indexIsDirty = false
        flushIndex()
    }

    /// Deletes payload files the index knows nothing about.
    ///
    /// Because the index write is coalesced, a process that dies inside that window can leave a
    /// payload on disk with no record pointing at it: never read, never counted against the byte
    /// limit, never evicted. The nightly housekeeping pass is where those get collected.
    private func removeOrphanedFiles() {
        guard let names = try? fileManager.contentsOfDirectory(atPath: directory.path) else { return }
        let known = Set(records.keys.map { DiskCache.digest(of: $0) + ".payload" })
        for name in names where name.hasSuffix(".payload") && !known.contains(name) {
            try? fileManager.removeItem(at: directory.appendingPathComponent(name, isDirectory: false))
        }
    }

    private func flushIndex() {
        do {
            try createDirectoryIfNeeded()
            let data = try JSONEncoder().encode(IndexFile(records: records))
            try data.write(to: indexURL, options: .atomic)
        } catch {
            DiskCache.logger.error("Cache index write failed: \(error.localizedDescription, privacy: .public)")
        }
    }
}
