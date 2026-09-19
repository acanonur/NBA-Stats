import Foundation

/// What a migration produced: the usable layout, plus notes the app can show the reader about
/// anything it had to change.
public struct MigrationResult: Hashable, Sendable {
    public let layout: DashboardLayout
    public let notes: [String]

    /// The same notes, under the name the architecture uses for them.
    public var migrationNotes: [String] { notes }

    /// True when the layout on disk is not what came back, so it is worth saving again.
    public var didChange: Bool { !notes.isEmpty }

    public init(layout: DashboardLayout, notes: [String] = []) {
        self.layout = layout
        self.notes = notes
    }
}

/// Every migration of a collection of layouts: the ones that survived, and why the others did not.
public struct CollectionMigrationResult: Sendable {
    public let results: [MigrationResult]
    public let failures: [LayoutMigrationError]

    public var layouts: [DashboardLayout] { results.map { $0.layout } }
    public var notes: [String] { results.flatMap { $0.notes } }

    public init(results: [MigrationResult] = [], failures: [LayoutMigrationError] = []) {
        self.results = results
        self.failures = failures
    }
}

/// Why a stored layout could not be migrated.
public enum LayoutMigrationError: Error, Hashable, Sendable {
    /// The layout was written by a newer build; it is shown read-only rather than corrupted.
    case tooNew(found: Int, supported: Int)
    /// The bytes are not JSON, or not a layout at all.
    case unreadable(String)
    /// The document decoded, but there was nothing layout-shaped in it.
    case notALayout

    public var userMessage: String {
        switch self {
        case .tooNew(let found, let supported):
            return "This dashboard was made with a newer version of Hardwood (format \(found); this build reads \(supported)). Update the app to edit it."
        case .unreadable:
            return "This dashboard file could not be read."
        case .notALayout:
            return "This file does not contain a dashboard."
        }
    }
}

extension LayoutMigrationError: LocalizedError {
    public var errorDescription: String? { userMessage }
}

/// Upgrades a stored layout to the schema this build understands.
///
/// The rules come from `contracts/CONTRACT.md` §5: unknown widget kinds are dropped with a
/// user-visible note, unknown config keys are stripped, missing required keys take the catalog
/// default, and a layout from a newer schema is refused rather than corrupted.
///
/// The `Data`-only entry points are deliberately catalog-free so that a persistence layer can run
/// them off the main actor; the config-level rules need the catalog and are therefore
/// `@MainActor`, matching `Catalog` itself.
public enum LayoutMigrator {

    // MARK: Schema-level migration

    /// Migrates one stored layout, discarding the notes.
    public static func migrate(_ raw: Data) throws -> DashboardLayout {
        try migrateResult(raw).layout
    }

    /// Migrates one stored layout and reports what changed.
    public static func migrateResult(_ raw: Data) throws -> MigrationResult {
        let document: RawLayout
        do {
            document = try JSONDecoder().decode(RawLayout.self, from: raw)
        } catch {
            throw LayoutMigrationError.unreadable(error.localizedDescription)
        }
        return try migrate(document)
    }

    /// Migrates a file holding either one layout or an array of them, keeping whatever survives.
    public static func migrateCollection(_ raw: Data) -> CollectionMigrationResult {
        let decoder = JSONDecoder()
        var documents: [RawLayout] = []
        if let array = try? decoder.decode([RawLayout].self, from: raw) {
            documents = array
        } else if let single = try? decoder.decode(RawLayout.self, from: raw) {
            documents = [single]
        } else {
            return CollectionMigrationResult(results: [], failures: [.unreadable("The saved dashboards could not be read.")])
        }

        var results: [MigrationResult] = []
        var failures: [LayoutMigrationError] = []
        for document in documents {
            do {
                results.append(try migrate(document))
            } catch let error as LayoutMigrationError {
                failures.append(error)
            } catch {
                failures.append(.unreadable(error.localizedDescription))
            }
        }
        return CollectionMigrationResult(results: results, failures: failures)
    }

    // MARK: Catalog-level migration

    /// Migrates a stored layout and then reconciles every widget against the catalog.
    @MainActor
    public static func migrate(_ raw: Data, catalog: Catalog) throws -> MigrationResult {
        let base = try migrateResult(raw)
        let reconciled = normalize(base.layout, catalog: catalog)
        return MigrationResult(layout: reconciled.layout, notes: base.notes + reconciled.notes)
    }

    /// Strips configuration keys the catalog does not define, fills in missing ones from the
    /// catalog defaults, and drops widgets the catalog no longer lists.
    @MainActor
    public static func normalize(_ layout: DashboardLayout, catalog: Catalog) -> MigrationResult {
        var notes: [String] = []
        var kept: [DashboardWidget] = []

        for widget in layout.widgets {
            guard let spec = catalog.widget(widget.kind) else {
                notes.append("Removed “\(widget.title ?? widget.kind.fallbackName)”: this version of Hardwood no longer has that widget.")
                continue
            }

            var updated = widget
            let normalizedConfig = catalog.normalizedConfig(for: widget.kind, config: widget.config)

            let dropped = widget.config.keys.filter { normalizedConfig[$0] == nil }.sorted()
            if !dropped.isEmpty {
                notes.append("“\(widget.title ?? spec.name)”: removed \(listPhrase(dropped)), which this widget no longer uses.")
            }
            let filled = normalizedConfig.keys.filter { widget.config[$0] == nil }.sorted()
            if !filled.isEmpty {
                notes.append("“\(widget.title ?? spec.name)”: filled in \(listPhrase(filled)) from the defaults.")
            }
            updated.config = normalizedConfig

            if !spec.sizes.contains(widget.size) {
                notes.append("“\(widget.title ?? spec.name)”: resized to \(spec.defaultSize.displayName), the only size it supports now.")
                updated.size = spec.defaultSize
            }

            kept.append(updated)
        }

        var migrated = layout
        migrated.widgets = kept
        return MigrationResult(layout: migrated, notes: notes)
    }

    // MARK: Implementation

    private static func migrate(_ document: RawLayout) throws -> MigrationResult {
        let foundVersion = document.schemaVersion ?? DashboardLayout.currentSchemaVersion
        guard foundVersion <= DashboardLayout.currentSchemaVersion else {
            throw LayoutMigrationError.tooNew(found: foundVersion, supported: DashboardLayout.currentSchemaVersion)
        }
        guard document.name != nil || document.widgets != nil || document.id != nil else {
            throw LayoutMigrationError.notALayout
        }

        var notes: [String] = []
        var widgets: [DashboardWidget] = []
        var usedIDs: Set<String> = []

        for rawWidget in document.widgets ?? [] {
            guard let kindRaw = rawWidget.kind else {
                notes.append("Removed a widget that had no type.")
                continue
            }
            guard let kind = WidgetKind(rawValue: kindRaw) else {
                notes.append("Removed a “\(kindRaw)” widget: this version of Hardwood does not have that widget.")
                continue
            }

            let size: WidgetSize
            if let sizeRaw = rawWidget.size {
                if let decoded = WidgetSize(rawValue: sizeRaw) {
                    size = decoded
                } else {
                    size = .medium
                    notes.append("“\(rawWidget.title ?? kind.fallbackName)”: the size “\(sizeRaw)” is unknown, so it is medium now.")
                }
            } else {
                size = .medium
            }

            var id = rawWidget.id ?? UUID().uuidString
            if usedIDs.contains(id) {
                id = UUID().uuidString
                notes.append("Two widgets shared an id; one of them was given a new one.")
            }
            usedIDs.insert(id)

            widgets.append(DashboardWidget(id: id,
                                           kind: kind,
                                           title: rawWidget.title,
                                           size: size,
                                           config: rawWidget.config ?? [:]))
        }

        let accent: AccentName
        if let accentRaw = document.accent {
            accent = AccentName(rawValue: accentRaw) ?? .orange
        } else {
            accent = .orange
        }

        let layout = DashboardLayout(
            id: document.id ?? UUID().uuidString,
            name: document.name ?? "Dashboard",
            icon: document.icon ?? "square.grid.2x2",
            accent: accent,
            schemaVersion: DashboardLayout.currentSchemaVersion,
            isPreset: document.isPreset ?? false,
            presetKey: document.presetKey,
            tagline: document.tagline,
            presentation: document.presentation.flatMap(LayoutPresentation.init(rawValue:)) ?? .tiles,
            createdAt: Formatting.parseTimestamp(document.createdAt),
            updatedAt: Formatting.parseTimestamp(document.updatedAt),
            widgets: widgets
        )

        if foundVersion < DashboardLayout.currentSchemaVersion {
            notes.append("Updated this dashboard from format \(foundVersion) to \(DashboardLayout.currentSchemaVersion).")
        }
        return MigrationResult(layout: layout, notes: notes)
    }

    /// `["a", "b", "c"]` becomes `"a, b and c"`.
    private static func listPhrase(_ items: [String]) -> String {
        guard let last = items.last else { return "" }
        if items.count == 1 { return "“\(last)”" }
        let leading = items.dropLast().map { "“\($0)”" }.joined(separator: ", ")
        return "\(leading) and “\(last)”"
    }
}

/// The stored layout as it is on disk, with nothing assumed about it.
private struct RawLayout: Decodable {
    let id: String?
    let name: String?
    let icon: String?
    let accent: String?
    let schemaVersion: Int?
    let isPreset: Bool?
    let presetKey: String?
    let tagline: String?
    /// Read as a raw string, like `accent` and `size`, so a presentation this build does not
    /// know falls back rather than throwing the whole document out. It has to be *here* and not
    /// only on `DashboardLayout`: `migrateResult(_:)` decodes into this struct, so a field this
    /// struct omits is lost on every migration — a reader who chose Broadsheet had it silently
    /// reset to Tiles the next time the file was opened. (`nbastats/accounts/layouts.py` has
    /// always carried it through, so the two clients disagreed on the same bytes.)
    let presentation: String?
    let createdAt: String?
    let updatedAt: String?
    let widgets: [RawWidget]?
}

/// The stored widget as it is on disk; the kind and size stay raw so an unknown one can be
/// reported instead of throwing.
private struct RawWidget: Decodable {
    let id: String?
    let kind: String?
    let title: String?
    let size: String?
    let config: [String: JSONValue]?
}
