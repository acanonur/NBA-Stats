import Combine
import Foundation
import SwiftUI

/// The editable dashboards, the current selection, and edit mode.
///
/// Every mutating method persists immediately — there is no "save" button anywhere in Hardwood —
/// and every mutation of a layout whose `isPreset` is true silently converts it to an editable copy
/// first, keeping `presetKey` so "Reset to Daily Recap" stays available afterwards.
@MainActor
public final class DashboardStore: ObservableObject {

    // MARK: Published state

    @Published public private(set) var layouts: [DashboardLayout] = []

    @Published public var selectedLayoutID: String? = nil {
        didSet {
            guard selectedLayoutID != oldValue else { return }
            persistSelection()
        }
    }

    @Published public var isEditing: Bool = false {
        didSet {
            guard isEditing != oldValue else { return }
            // A fresh edit session starts with nothing to undo, and leaving edit mode commits.
            undoSnapshot = nil
        }
    }

    /// What migration had to change on the way in, for the banner the dashboard shows once.
    @Published public private(set) var migrationNotes: [String] = []

    /// The last thing that went wrong while reading or writing, in plain English.
    @Published public private(set) var lastErrorMessage: String? = nil

    // MARK: Dependencies

    private let persistence: LayoutPersisting
    private let catalog: Catalog
    private let defaults: UserDefaults

    private static let selectionDefaultsKey = "com.hardwood.nbastats.selectedLayoutID"

    /// One level of undo: the layout as it was immediately before the most recent edit.
    private struct EditSnapshot {
        /// The id the edited layout carries *now* (a preset conversion changes it).
        let currentID: String
        let previous: DashboardLayout
    }

    private var undoSnapshot: EditSnapshot?

    // MARK: Init

    public init(persistence: LayoutPersisting,
                catalog: Catalog,
                defaults: UserDefaults = .standard) {
        self.persistence = persistence
        self.catalog = catalog
        self.defaults = defaults
        loadFromDisk()
    }

    // MARK: Derived state

    public var selectedLayout: DashboardLayout? {
        if let id = selectedLayoutID, let match = layouts.first(where: { $0.id == id }) {
            return match
        }
        return layouts.first
    }

    public func layout(id layoutID: String) -> DashboardLayout? {
        layouts.first { $0.id == layoutID }
    }

    /// True while an edit can be taken back.
    public var canUndo: Bool { undoSnapshot != nil }

    /// The preset a layout was built from, when it still knows.
    public func basePreset(for layout: DashboardLayout) -> DashboardLayout? {
        guard let key = layout.presetKey else { return nil }
        return catalog.preset(key)
    }

    // MARK: Loading

    private func loadFromDisk() {
        var loaded = LayoutLoadResult()
        do {
            loaded = try persistence.loadAllWithNotes()
        } catch {
            lastErrorMessage = (error as? LayoutPersistenceError)?.userMessage
                ?? "Your saved dashboards could not be opened."
        }

        let reconciled = reconcileAgainstCatalog(loaded.layouts)
        var restored = reconciled.layouts
        var notes = loaded.messages + reconciled.notes

        let needsSeed = restored.isEmpty
        if needsSeed {
            restored = LayoutSeeder.seedLayouts(catalog: catalog)
            if !loaded.failures.isEmpty {
                notes.append("Hardwood started you again from the Daily Recap preset.")
            }
        }

        layouts = restored
        migrationNotes = notes

        let stored = defaults.string(forKey: DashboardStore.selectionDefaultsKey)
        if let stored = stored, restored.contains(where: { $0.id == stored }) {
            selectedLayoutID = stored
        } else {
            selectedLayoutID = restored.first?.id
        }

        // Seeded or repaired documents are written straight back, so the next launch is clean.
        if needsSeed || !notes.isEmpty {
            persist()
        }
    }

    /// Runs every loaded layout through the catalog-level half of `LayoutMigrator`.
    ///
    /// Skipped entirely when the catalog is empty: a bundle that failed to load must not be allowed
    /// to strip every widget out of the reader's dashboards.
    private func reconcileAgainstCatalog(_ input: [DashboardLayout]) -> (layouts: [DashboardLayout], notes: [String]) {
        guard !catalog.widgets.isEmpty else { return (input, []) }
        var result: [DashboardLayout] = []
        var notes: [String] = []
        for layout in input {
            let migration = LayoutMigrator.normalize(layout, catalog: catalog)
            result.append(migration.layout)
            notes.append(contentsOf: migration.notes)
        }
        return (result, notes)
    }

    /// Drops the migration banner once the reader has seen it.
    public func acknowledgeMigrationNotes() {
        migrationNotes = []
    }

    public func clearError() {
        lastErrorMessage = nil
    }

    // MARK: Layouts

    @discardableResult
    public func addLayout(fromPreset key: String) -> DashboardLayout {
        var layout: DashboardLayout
        if let preset = catalog.preset(key) {
            layout = preset.makeEditableCopy(named: uniqueName(preset.name))
            layout.presetKey = preset.presetKey ?? key
            layout.widgets = layout.widgets.map { normalizedWidget($0) }
        } else {
            layout = LayoutSeeder.blankLayout(named: uniqueName("New Dashboard"))
        }
        layout.isPreset = false
        layouts.append(layout)
        selectedLayoutID = layout.id
        undoSnapshot = nil
        persist()
        return layout
    }

    @discardableResult
    public func addBlankLayout(named name: String) -> DashboardLayout {
        let layout = LayoutSeeder.blankLayout(named: uniqueName(name))
        layouts.append(layout)
        selectedLayoutID = layout.id
        undoSnapshot = nil
        persist()
        return layout
    }

    public func duplicate(_ layout: DashboardLayout) {
        var copy = layout.makeEditableCopy(named: uniqueName("\(layout.name) Copy"))
        copy.isPreset = false
        if let index = layouts.firstIndex(where: { $0.id == layout.id }) {
            layouts.insert(copy, at: layouts.index(after: index))
        } else {
            layouts.append(copy)
        }
        selectedLayoutID = copy.id
        undoSnapshot = nil
        persist()
    }

    public func delete(_ layoutID: String) {
        let wasSelected = selectedLayoutID == layoutID
        layouts.removeAll { $0.id == layoutID }

        // Never leave the reader with no dashboard at all.
        if layouts.isEmpty {
            layouts = LayoutSeeder.seedLayouts(catalog: catalog)
        }
        if wasSelected || !layouts.contains(where: { $0.id == selectedLayoutID }) {
            selectedLayoutID = layouts.first?.id
        }
        undoSnapshot = nil
        persist()
    }

    public func rename(_ layoutID: String, to name: String) {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        edit(layoutID) { layout, _ in layout.name = trimmed }
    }

    /// Replaces a layout wholesale, bumping `updatedAt` and persisting.
    public func update(_ layout: DashboardLayout) {
        guard let index = layouts.firstIndex(where: { $0.id == layout.id }) else { return }
        if isEditing {
            undoSnapshot = EditSnapshot(currentID: layout.id, previous: layouts[index])
        }
        var updated = layout
        updated.updatedAt = Date()
        if updated.createdAt == nil { updated.createdAt = updated.updatedAt }
        layouts[index] = updated
        persist()
    }

    public func moveLayouts(fromOffsets: IndexSet, toOffset: Int) {
        layouts.move(fromOffsets: fromOffsets, toOffset: toOffset)
        persist()
    }

    public func setAccent(_ accent: AccentName, for layoutID: String) {
        edit(layoutID) { layout, _ in layout.accent = accent }
    }

    public func setIcon(_ icon: String, for layoutID: String) {
        edit(layoutID) { layout, _ in layout.icon = icon }
    }

    public func select(_ layoutID: String) {
        guard layouts.contains(where: { $0.id == layoutID }) else { return }
        selectedLayoutID = layoutID
    }

    /// Puts a layout back to the preset it was built from, keeping its id, name and place in the list.
    public func resetToPreset(_ layoutID: String) {
        guard let index = layouts.firstIndex(where: { $0.id == layoutID }) else { return }
        let current = layouts[index]
        guard let key = current.presetKey, let preset = catalog.preset(key) else { return }

        var restored = preset.makeEditableCopy(named: current.name)
        restored.id = current.id
        restored.presetKey = key
        restored.isPreset = false
        restored.createdAt = current.createdAt ?? Date()
        restored.updatedAt = Date()
        restored.widgets = restored.widgets.map { normalizedWidget($0) }

        if isEditing {
            undoSnapshot = EditSnapshot(currentID: current.id, previous: current)
        }
        layouts[index] = restored
        persist()
    }

    // MARK: Widgets

    public func addWidget(_ widget: DashboardWidget, to layoutID: String) {
        edit(layoutID) { layout, _ in
            layout.append(self.normalizedWidget(widget))
        }
    }

    /// Appends a new widget of this kind with the catalog's defaults and the spec's default size,
    /// returning it so the caller can open its configuration immediately.
    @discardableResult
    public func addWidget(kind: WidgetKind, to layoutID: String) -> DashboardWidget? {
        let widget = normalizedWidget(catalog.makeWidget(kind: kind))
        let updated = edit(layoutID) { layout, _ in
            layout.append(widget)
        }
        return updated == nil ? nil : widget
    }

    public func removeWidget(_ widgetID: String, from layoutID: String) {
        edit(layoutID) { layout, idMap in
            layout.remove(widgetID: idMap[widgetID] ?? widgetID)
        }
    }

    public func duplicateWidget(_ widgetID: String, in layoutID: String) {
        edit(layoutID) { layout, idMap in
            let resolved = idMap[widgetID] ?? widgetID
            guard let source = layout.widget(id: resolved),
                  let index = layout.widgets.firstIndex(where: { $0.id == resolved }) else { return }
            layout.widgets.insert(source.copyWithNewID(), at: layout.widgets.index(after: index))
        }
    }

    public func moveWidgets(in layoutID: String, fromOffsets: IndexSet, toOffset: Int) {
        edit(layoutID) { layout, _ in
            layout.move(fromOffsets: fromOffsets, toOffset: toOffset)
        }
    }

    /// Moves one widget to a new index, which is what the drag-to-reorder gesture produces.
    public func moveWidget(_ widgetID: String, toIndex destination: Int, in layoutID: String) {
        edit(layoutID) { layout, idMap in
            let resolved = idMap[widgetID] ?? widgetID
            guard let from = layout.widgets.firstIndex(where: { $0.id == resolved }) else { return }
            let clamped = max(0, min(destination, layout.widgets.count - 1))
            guard clamped != from else { return }
            // `move(fromOffsets:toOffset:)` inserts *before* `toOffset`, so a move down the list
            // needs one extra step to land on the index the reader dropped on.
            let offset = clamped > from ? clamped + 1 : clamped
            layout.move(fromOffsets: IndexSet(integer: from), toOffset: offset)
        }
    }

    public func resizeWidget(_ widgetID: String, to size: WidgetSize, in layoutID: String) {
        edit(layoutID) { layout, idMap in
            let resolved = idMap[widgetID] ?? widgetID
            guard var widget = layout.widget(id: resolved) else { return }
            guard let spec = self.catalog.widget(widget.kind) else {
                widget.size = size
                layout.replace(widget)
                return
            }
            widget.size = spec.sizes.contains(size) ? size : spec.defaultSize
            layout.replace(widget)
        }
    }

    /// Walks the sizes this widget kind actually supports, in catalog order.
    public func cycleWidgetSize(_ widgetID: String, in layoutID: String) {
        guard let layout = layout(id: layoutID), let widget = layout.widget(id: widgetID) else { return }
        let sizes = catalog.widget(widget.kind)?.sizes ?? WidgetSize.allCases
        guard !sizes.isEmpty else { return }
        let current = sizes.firstIndex(of: widget.size) ?? 0
        let next = sizes[(current + 1) % sizes.count]
        resizeWidget(widgetID, to: next, in: layoutID)
    }

    public func updateWidgetConfig(_ widgetID: String,
                                   config: [String: JSONValue],
                                   title: String?,
                                   in layoutID: String) {
        updateWidget(widgetID, config: config, title: title, size: nil, in: layoutID)
    }

    /// The configuration sheet's write-back: configuration, custom title and size in one edit.
    public func updateWidget(_ widgetID: String,
                             config: [String: JSONValue],
                             title: String?,
                             size: WidgetSize?,
                             in layoutID: String) {
        edit(layoutID) { layout, idMap in
            let resolved = idMap[widgetID] ?? widgetID
            guard var widget = layout.widget(id: resolved) else { return }
            widget.config = self.catalog.normalizedConfig(for: widget.kind, config: config)
            widget.title = DashboardStore.sanitizedTitle(title)
            if let size = size {
                let sizes = self.catalog.widget(widget.kind)?.sizes
                widget.size = (sizes?.contains(size) ?? true) ? size : widget.size
            }
            layout.replace(widget)
        }
    }

    public func setWidgetTitle(_ title: String?, for widgetID: String, in layoutID: String) {
        edit(layoutID) { layout, idMap in
            let resolved = idMap[widgetID] ?? widgetID
            guard var widget = layout.widget(id: resolved) else { return }
            widget.title = DashboardStore.sanitizedTitle(title)
            layout.replace(widget)
        }
    }

    // MARK: Undo

    /// Takes back the most recent edit made while in edit mode. One level, deliberately: this is a
    /// dashboard editor, not a document editor, and a deep undo stack would outlive the session it
    /// belongs to.
    public func undoLastEdit() {
        guard let snapshot = undoSnapshot else { return }
        if let index = layouts.firstIndex(where: { $0.id == snapshot.currentID }) {
            layouts[index] = snapshot.previous
        } else {
            layouts.append(snapshot.previous)
        }
        if selectedLayoutID == snapshot.currentID {
            selectedLayoutID = snapshot.previous.id
        }
        undoSnapshot = nil
        persist()
    }

    // MARK: Editing plumbing

    /// The single path every layout mutation takes.
    ///
    /// It converts a preset to an editable copy first (keeping `presetKey`, and handing the caller
    /// a map from the widget ids it knows to the ids in the copy), records one level of undo while
    /// edit mode is on, bumps `updatedAt`, and writes to disk.
    @discardableResult
    private func edit(_ layoutID: String,
                      _ body: (inout DashboardLayout, [String: String]) -> Void) -> DashboardLayout? {
        guard let index = layouts.firstIndex(where: { $0.id == layoutID }) else { return nil }
        let original = layouts[index]

        var working = original
        var idMap: [String: String] = [:]
        if original.isPreset {
            let copy = original.makeEditableCopy()
            for (old, new) in zip(original.widgets, copy.widgets) {
                idMap[old.id] = new.id
            }
            working = copy
            working.isPreset = false
            if working.presetKey == nil {
                working.presetKey = DashboardStore.presetKey(fromLayoutID: original.id)
            }
        }

        body(&working, idMap)

        working.updatedAt = Date()
        if working.createdAt == nil { working.createdAt = working.updatedAt }

        if isEditing {
            undoSnapshot = EditSnapshot(currentID: working.id, previous: original)
        }

        layouts[index] = working
        if selectedLayoutID == original.id && working.id != original.id {
            selectedLayoutID = working.id
        }
        persist()
        return working
    }

    private func persist() {
        do {
            try persistence.save(layouts)
            lastErrorMessage = nil
        } catch {
            lastErrorMessage = "Your changes could not be saved: \(error.localizedDescription)"
        }
    }

    private func persistSelection() {
        if let id = selectedLayoutID {
            defaults.set(id, forKey: DashboardStore.selectionDefaultsKey)
        } else {
            defaults.removeObject(forKey: DashboardStore.selectionDefaultsKey)
        }
    }

    private func normalizedWidget(_ widget: DashboardWidget) -> DashboardWidget {
        var copy = widget
        copy.config = catalog.normalizedConfig(for: widget.kind, config: widget.config)
        if let spec = catalog.widget(widget.kind), !spec.sizes.contains(copy.size) {
            copy.size = spec.defaultSize
        }
        return copy
    }

    /// `"My Dashboard"` becomes `"My Dashboard 2"` when that name is taken.
    private func uniqueName(_ proposed: String) -> String {
        let trimmed = proposed.trimmingCharacters(in: .whitespacesAndNewlines)
        let base = trimmed.isEmpty ? "Dashboard" : trimmed
        let taken = Set(layouts.map { $0.name })
        guard taken.contains(base) else { return base }
        var suffix = 2
        while taken.contains("\(base) \(suffix)") && suffix < 1_000 {
            suffix += 1
        }
        return "\(base) \(suffix)"
    }

    private static func sanitizedTitle(_ title: String?) -> String? {
        guard let trimmed = title?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty else {
            return nil
        }
        return trimmed
    }

    /// `"preset.daily_recap"` becomes `"daily_recap"`, so a preset stored without an explicit
    /// `presetKey` can still be reset.
    private static func presetKey(fromLayoutID layoutID: String) -> String? {
        let prefix = "preset."
        guard layoutID.hasPrefix(prefix) else { return nil }
        let key = String(layoutID.dropFirst(prefix.count))
        return key.isEmpty ? nil : key
    }
}

#if DEBUG

/// In-memory fixtures shared by every Dashboard preview.
///
/// The catalog is built from the bundle when it is there and topped up from literal JSON when it is
/// not, so a preview renders the same controls the app does without needing a running service.
@MainActor
enum DashboardPreviewData {

    static let catalog: Catalog = {
        let catalog = Catalog(bundle: .main)
        if catalog.widgets.isEmpty || catalog.presets.isEmpty || catalog.metrics.isEmpty {
            catalog.update(metrics: decode([MetricDescriptor].self, from: metricsJSON),
                           widgets: decode([WidgetSpec].self, from: widgetsJSON),
                           presets: decode([DashboardLayout].self, from: presetsJSON))
        }
        return catalog
    }()

    static let layout: DashboardLayout = {
        if let seeded = LayoutSeeder.seedLayouts(catalog: catalog).first, !seeded.widgets.isEmpty {
            return seeded
        }
        var layout = LayoutSeeder.blankLayout(named: "Daily Recap", accent: .orange)
        layout.presetKey = "daily_recap"
        layout.widgets = [
            DashboardWidget(kind: .statTile, title: "Your Player", size: .small,
                            config: ["metric": .string("ts_pct"), "season": .string("latest")]),
            DashboardWidget(kind: .statTile, title: "Your Team", size: .small,
                            config: ["metric": .string("net_rtg"), "season": .string("latest")]),
            DashboardWidget(kind: .leaderboard, title: "True Shooting", size: .large,
                            config: ["metric": .string("ts_pct"), "limit": .int(10)]),
            DashboardWidget(kind: .scoreboard, title: "Last Night", size: .large,
                            config: ["date": .string("latest")])
        ]
        return layout
    }()

    static func store() -> DashboardStore {
        let persistence = InMemoryLayoutPersistence(layouts: [layout])
        return DashboardStore(persistence: persistence,
                              catalog: catalog,
                              defaults: previewDefaults)
    }

    /// A throwaway defaults domain, so previews never disturb the simulator's real selection.
    static let previewDefaults: UserDefaults = UserDefaults(suiteName: "com.hardwood.nbastats.previews") ?? .standard

    private static func decode<T: Decodable>(_ type: T.Type, from json: String) -> T? {
        guard let data = json.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    private static let metricsJSON = """
    [
      {"key": "ts_pct", "name": "True Shooting %", "shortName": "TS%", "category": "shooting",
       "format": "percent1", "higherIsBetter": true, "scope": ["player", "team"],
       "availability": {"seasonFrom": "1946-47", "perGameFrom": "1996-97",
                        "seasonLevelOnly": false, "estimatedBefore": null},
       "domain": {"min": 0.35, "max": 0.75},
       "glossary": "PTS / (2 * (FGA + 0.44*FTA))."},
      {"key": "net_rtg", "name": "Net Rating", "shortName": "NetRtg", "category": "efficiency",
       "format": "rating1", "higherIsBetter": true, "scope": ["player", "team"],
       "availability": {"seasonFrom": "1996-97", "perGameFrom": "1996-97",
                        "seasonLevelOnly": false, "estimatedBefore": null},
       "domain": {"min": -25.0, "max": 25.0},
       "glossary": "Offensive Rating minus Defensive Rating."},
      {"key": "pts", "name": "Points", "shortName": "PTS", "category": "volume",
       "format": "decimal1", "higherIsBetter": true, "scope": ["player", "team"],
       "availability": {"seasonFrom": "1946-47", "perGameFrom": "1946-47",
                        "seasonLevelOnly": false, "estimatedBefore": null},
       "domain": null, "glossary": "Points."}
    ]
    """

    private static let widgetsJSON = """
    [
      {"kind": "stat_tile", "name": "Stat Tile", "summary": "One headline metric.",
       "icon": "number.square", "sizes": ["small", "medium", "large"], "defaultSize": "small",
       "minRefreshSeconds": 300,
       "config": [
         {"key": "metric", "type": "metric", "label": "Metric", "required": true,
          "default": "ts_pct", "metricScope": "any"},
         {"key": "season", "type": "season", "label": "Season", "required": true, "default": "latest"},
         {"key": "showSparkline", "type": "bool", "label": "Show Sparkline", "default": true}
       ]},
      {"kind": "leaderboard", "name": "Leaderboard", "summary": "Top players by any metric.",
       "icon": "list.number", "sizes": ["medium", "large"], "defaultSize": "large",
       "minRefreshSeconds": 600,
       "config": [
         {"key": "metric", "type": "metric", "label": "Metric", "required": true,
          "default": "pie", "metricScope": "any"},
         {"key": "limit", "type": "int", "label": "Rows", "default": 10, "min": 3, "max": 50}
       ]},
      {"kind": "scoreboard", "name": "Scoreboard", "summary": "Every game on a slate.",
       "icon": "sportscourt", "sizes": ["medium", "large"], "defaultSize": "large",
       "minRefreshSeconds": 60,
       "config": [
         {"key": "date", "type": "date", "label": "Date", "required": true, "default": "latest"}
       ]}
    ]
    """

    private static let presetsJSON = """
    [
      {"id": "preset.daily_recap", "name": "Daily Recap", "icon": "sun.horizon", "accent": "orange",
       "schemaVersion": 1, "isPreset": true, "presetKey": "daily_recap",
       "tagline": "Last night's slate, decoded.",
       "widgets": [
         {"id": "w1", "kind": "scoreboard", "title": "Last Night", "size": "large",
          "config": {"date": "latest"}},
         {"id": "w2", "kind": "stat_tile", "title": "Your Player", "size": "small",
          "config": {"metric": "ts_pct", "season": "latest"}},
         {"id": "w3", "kind": "stat_tile", "title": "Your Team", "size": "small",
          "config": {"metric": "net_rtg", "season": "latest"}}
       ]},
      {"id": "preset.efficiency_hunt", "name": "Efficiency Hunt", "icon": "flame", "accent": "red",
       "schemaVersion": 1, "isPreset": true, "presetKey": "efficiency_hunt",
       "tagline": "Who scores without wasting possessions.",
       "widgets": [
         {"id": "w1", "kind": "leaderboard", "title": "True Shooting", "size": "large",
          "config": {"metric": "ts_pct", "limit": 10}}
       ]}
    ]
    """
}
#endif
