import Foundation
import OSLog

// MARK: - Load result

/// Everything reading the stored layouts produced: the usable documents, the migration notes worth
/// showing the reader, and the documents that could not be migrated at all.
public struct LayoutLoadResult: Sendable {
    public let layouts: [DashboardLayout]
    public let notes: [String]
    public let failures: [LayoutMigrationError]

    public init(layouts: [DashboardLayout] = [],
                notes: [String] = [],
                failures: [LayoutMigrationError] = []) {
        self.layouts = layouts
        self.notes = notes
        self.failures = failures
    }

    public var isEmpty: Bool { layouts.isEmpty }

    public var hasIssues: Bool { !notes.isEmpty || !failures.isEmpty }

    /// Migration notes first, then the reason each unreadable document was left out.
    public var messages: [String] { notes + failures.map { $0.userMessage } }
}

// MARK: - Protocol

/// Where the editable dashboards live between launches.
///
/// `loadAllWithNotes()` is the entry point the store actually uses; it is a requirement rather than
/// an extension-only method so a conformer's own implementation is reached through the existential.
public protocol LayoutPersisting: Sendable {
    func loadAll() throws -> [DashboardLayout]
    func loadAllWithNotes() throws -> LayoutLoadResult
    func save(_ layouts: [DashboardLayout]) throws
}

public extension LayoutPersisting {
    /// A conformer that has nothing to report about migration gets this for free.
    func loadAllWithNotes() throws -> LayoutLoadResult {
        LayoutLoadResult(layouts: try loadAll())
    }
}

/// Why the layout file could not be read at all. A file that is merely out of date is not an
/// error: `LayoutMigrator` handles that and reports it in the notes.
public enum LayoutPersistenceError: Error, Hashable, Sendable {
    case unreadable(String)

    public var userMessage: String {
        switch self {
        case .unreadable(let detail):
            return "Your saved dashboards could not be opened (\(detail))."
        }
    }
}

extension LayoutPersistenceError: LocalizedError {
    public var errorDescription: String? { userMessage }
}

// MARK: - File-backed persistence

/// One JSON document in Application Support, written atomically.
///
/// The document is an envelope — `{"schemaVersion": 1, "updatedAt": "…", "layouts": [...]}` — so a
/// later version can add fields beside the array. A file that is a bare array (or a single layout)
/// is still read, because `LayoutMigrator.migrateCollection(_:)` accepts both shapes.
///
/// Only `URL`s are stored, never a `FileManager`, so the type is genuinely `Sendable` and the store
/// can hand it to a background task unchanged.
public struct FileLayoutPersistence: LayoutPersisting {

    /// The folder holding the layout document and any corruption backups.
    public let directoryURL: URL
    /// The layout document itself.
    public let fileURL: URL

    private static let logger = Logger(subsystem: "com.hardwood.nbastats", category: "layouts")

    public init(directoryURL: URL? = nil, fileName: String = "Layouts.json") {
        let base = directoryURL ?? FileLayoutPersistence.defaultDirectory()
        self.directoryURL = base
        self.fileURL = base.appendingPathComponent(fileName, isDirectory: false)
    }

    /// `Application Support/Hardwood`, falling back to the temporary directory on the (effectively
    /// impossible) chance that Application Support cannot be created.
    private static func defaultDirectory() -> URL {
        let manager = FileManager.default
        if let support = try? manager.url(for: .applicationSupportDirectory,
                                          in: .userDomainMask,
                                          appropriateFor: nil,
                                          create: true) {
            return support.appendingPathComponent("Hardwood", isDirectory: true)
        }
        return URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("Hardwood", isDirectory: true)
    }

    // MARK: Reading

    public func loadAll() throws -> [DashboardLayout] {
        try loadAllWithNotes().layouts
    }

    public func loadAllWithNotes() throws -> LayoutLoadResult {
        let manager = FileManager.default
        guard manager.fileExists(atPath: fileURL.path) else {
            // First launch. The store seeds from the bundled presets.
            return LayoutLoadResult()
        }

        let data: Data
        do {
            data = try Data(contentsOf: fileURL)
        } catch {
            FileLayoutPersistence.logger.error("Layouts could not be read: \(error.localizedDescription, privacy: .public)")
            throw LayoutPersistenceError.unreadable(error.localizedDescription)
        }
        guard !data.isEmpty else { return LayoutLoadResult() }

        let migration = LayoutMigrator.migrateCollection(FileLayoutPersistence.layoutsPayload(from: data))

        // Nothing survived *and* the bytes were not a layout document: keep a copy before the store
        // re-seeds over it, so a reader who cares can still recover the file by hand.
        if migration.results.isEmpty && migration.failures.contains(where: { $0.isCorruption }) {
            backUpCorruptFile()
        }

        return LayoutLoadResult(layouts: migration.layouts,
                                notes: migration.notes,
                                failures: migration.failures)
    }

    /// Pulls the `layouts` array out of the envelope and re-encodes it for the migrator. A file
    /// that is not an envelope is handed through untouched.
    private static func layoutsPayload(from data: Data) -> Data {
        guard let document = try? JSONDecoder().decode(StoredDocument.self, from: data),
              let layouts = document.layouts,
              let encoded = try? JSONEncoder().encode(layouts) else {
            return data
        }
        return encoded
    }

    private func backUpCorruptFile() {
        let manager = FileManager.default
        let stamp = Int(Date().timeIntervalSince1970)
        let backup = directoryURL.appendingPathComponent("Layouts-corrupt-\(stamp).json", isDirectory: false)
        try? manager.removeItem(at: backup)
        do {
            try manager.copyItem(at: fileURL, to: backup)
            FileLayoutPersistence.logger.error("Unreadable layout file backed up to \(backup.lastPathComponent, privacy: .public).")
        } catch {
            FileLayoutPersistence.logger.error("Unreadable layout file could not be backed up: \(error.localizedDescription, privacy: .public)")
        }
    }

    // MARK: Writing

    public func save(_ layouts: [DashboardLayout]) throws {
        let manager = FileManager.default
        try manager.createDirectory(at: directoryURL, withIntermediateDirectories: true)

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let document = OutgoingDocument(schemaVersion: DashboardLayout.currentSchemaVersion,
                                        updatedAt: Formatting.timestampString(Date()),
                                        layouts: layouts)
        let data = try encoder.encode(document)
        // `.atomic` writes to a sibling temp file and renames, so a crash mid-write cannot leave a
        // half-written document behind.
        try data.write(to: fileURL, options: [.atomic])
    }

    // MARK: Document shape

    private struct StoredDocument: Decodable {
        let schemaVersion: Int?
        /// Kept untyped so the migrator, not the decoder, decides what a layout is.
        let layouts: [JSONValue]?
    }

    private struct OutgoingDocument: Encodable {
        let schemaVersion: Int
        let updatedAt: String
        let layouts: [DashboardLayout]
    }
}

private extension LayoutMigrationError {
    /// True when the bytes themselves were wrong, rather than merely newer than this build.
    var isCorruption: Bool {
        switch self {
        case .unreadable, .notALayout:
            return true
        case .tooNew:
            return false
        }
    }
}

// MARK: - In-memory persistence

/// A persistence layer that keeps everything in memory. Used by previews and tests.
///
/// The lock is what makes the `Sendable` conformance honest: `LayoutPersisting` is `Sendable`, so
/// this object can be touched from more than one task.
public final class InMemoryLayoutPersistence: LayoutPersisting, @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [DashboardLayout]
    private var saves: Int = 0

    public init(layouts: [DashboardLayout] = []) {
        self.storage = layouts
    }

    public func loadAll() throws -> [DashboardLayout] {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }

    public func save(_ layouts: [DashboardLayout]) throws {
        lock.lock()
        defer { lock.unlock() }
        storage = layouts
        saves += 1
    }

    /// How many times the store has written, for tests that assert "every edit persists".
    public var saveCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return saves
    }
}

// MARK: - Seeding

/// Builds the layouts a brand new install starts with.
///
/// Only one preset is copied into an editable layout; the rest stay in the catalog and are offered
/// through the preset gallery, so a first launch is a working dashboard rather than nine of them.
public enum LayoutSeeder {

    /// The preset a fresh install starts on.
    public static let defaultPresetKey = "daily_recap"

    @MainActor
    public static func seedLayouts(catalog: Catalog) -> [DashboardLayout] {
        let preset = catalog.preset(defaultPresetKey)
            ?? catalog.presets.first(where: { !$0.widgets.isEmpty })
        guard let preset = preset else {
            // No catalog at all (a broken bundle). An empty dashboard the reader can fill is still
            // better than a screen with nothing on it and no way forward.
            return [blankLayout()]
        }

        var layout = preset.makeEditableCopy()
        layout.isPreset = false
        layout.presetKey = preset.presetKey ?? defaultPresetKey
        layout.widgets = layout.widgets.map { widget in
            var normalized = widget
            normalized.config = catalog.normalizedConfig(for: widget.kind, config: widget.config)
            return normalized
        }
        return [layout]
    }

    public static func blankLayout(named name: String = "My Dashboard",
                                   accent: AccentName = .orange) -> DashboardLayout {
        let now = Date()
        return DashboardLayout(name: name,
                               icon: "square.grid.2x2",
                               accent: accent,
                               isPreset: false,
                               createdAt: now,
                               updatedAt: now,
                               widgets: [])
    }
}
